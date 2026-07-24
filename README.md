# ai-tracer

**Eventually:** automatically generate pytest test cases by recording the real input/output of every function in a running Python program.

**Right now:** ai-tracer runs an arbitrary external Python program through its own CLI, correctly (imports from sibling files in the target work, and the target doesn't see ai-tracer's own command-line arguments), and records which functions it calls along the way, along with the arguments each call received, what it returned (or that it raised instead), which function called which, and which module each call happened in. From that recording it can generate a pytest test per recorded call, for the calls it can reconstruct safely (see "Generating tests" below). Tracing and generation are two separate steps for now; a single-command flow that does both comes later.

---

## Installation

```bash
git clone <repo-url>
cd ai-tracer
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

---

## Running

```bash
./scripts/run.sh path/to/your_program.py
```

This runs the target program the same way `python path/to/your_program.py` would, just through ai-tracer's own harness.

Any extra arguments are forwarded to the target program:

```bash
./scripts/run.sh path/to/your_program.py --some-flag value
```

Every function the target program calls (in files under the target's own directory) gets recorded to `path/to/your_program.trace.json`, in call order, along with the arguments it was called with and where it sits in the call tree:

```json
[
  { "call_id": 0, "parent_call_id": null, "module": "__main__", "qualname": "main", "args": {}, "arg_serialization": {}, "raised": false, "return_value": null, "return_serialization": null, "exception_module": null, "exception_type": null },
  { "call_id": 1, "parent_call_id": 0, "module": "helper", "qualname": "double", "args": { "x": 21 }, "arg_serialization": { "x": "json" }, "raised": false, "return_value": 42, "return_serialization": "json", "exception_module": null, "exception_type": null }
]
```

`call_id` is unique per call; `parent_call_id` is the `call_id` of whichever call was in progress when this one started (`null` for a top-level call). A worker thread's own calls form their own independent tree, `parent_call_id: null` at the root, even if the thread was started from within another traced call.

`module` is the `__name__` of the module the function was defined in - the same name Python's own import machinery assigns it. The entry script itself is always `"__main__"`, matching direct execution. A package's `__init__.py` resolves to the package's own dotted name (e.g. `"pkg"`, not `"pkg.__init__"`).

Argument and return values are recorded as JSON-safe snapshots: taken as of the moment the call happened (or returned), not whatever they become afterward, and falling back to `repr()` for anything that isn't JSON-serializable (e.g. a custom object). `arg_serialization` (per argument name) and `return_serialization` mark which kind each one is, `"json"` for a real JSON value or `"repr"` for a fallback string, so a consumer can tell "the target actually passed/returned this string" apart from "this string is just a repr() of something unrepresentable." `return_serialization` is `null` whenever `return_value` is (a call that raised, or one whose `"return"` event couldn't distinguish a real value from `None`).

`raised` is `true` if the call exited via an exception instead of a normal return, in which case `return_value` is always `null`. This is a best-effort signal, not a perfect one: a call that catches its own exception and then explicitly returns a non-`None` value is always detected correctly, but a call that catches its own exception and then returns `None` - explicitly (`return None`) or implicitly (no `return` statement afterward) - is indistinguishable, at this level, from one that let the exception propagate - `raised` is set to `true` in that ambiguous case too, favoring never missing a real propagating exception over occasionally over-reporting one.

When `raised` is `true`, `exception_type` and `exception_module` name the exception that came out of the call: its `__qualname__` (e.g. `"ValueError"`) and the module it was defined in (`"builtins"` for a built-in exception, or the same module name a function defined there would get, e.g. `"__main__"` for one defined in the entry script). Both are `null` for a call that returned normally. A frame the exception merely passes through records the same propagating exception as the frame that raised it. In the ambiguous catch-then-return-`None` case above, these name whichever exception the frame last saw.

---

## Generating tests

Once you have a trace log, turn it into pytest test files:

```bash
./scripts/generate.sh path/to/your_program.trace.json path/to/target_dir generated_tests
```

The arguments are the trace log, the target program's own directory (the same directory the traced program lives in, used to import the functions under test), and an output directory for the generated tests (defaults to `generated_tests/`).

This writes one `test_<module>.py` file per module, with one test per recorded call, replaying the exact arguments and asserting the exact return value that call produced:

```python
import sys

sys.path.insert(0, "path/to/target_dir")
from helper import double


def test_double_0():
    result = double(x=21)
    assert result == 42
```

It also writes a `conftest.py` next to them. That file runs once when pytest starts and clears any target module cached under the same name (a target module shadowing another, or a stale entry from a previous run) so every generated test imports the target's own code. Doing this once, rather than in each test file, keeps a module or package that other target modules import at load time from being re-executed per file. Run the generated tests as their own pytest invocation, e.g. `pytest generated_tests/`.

If a `conftest.py` ai-tracer didn't generate already exists in the output directory, it's left untouched: the generated tests still import the target through their own `sys.path` setup, but the one-time module eviction is skipped (a warning is printed), so a same-named module already cached in that pytest session could shadow the target's. Point the output at an empty directory (the default `generated_tests/`) to get the full setup.

Not every recorded call becomes a test. A call is skipped, with a one-line reason printed to stderr (so it's never a silent no-op), when it can't be reconstructed safely as a standalone test:

- It isn't a plain top-level function (a method, nested function, or lambda). Only module-level functions are importable by name.
- Any of its arguments, or its return value, fell back to `repr()` rather than a real JSON value (`arg_serialization`/`return_serialization` is `"repr"`) - a `repr()` string isn't a reconstructable literal.
- Any of its arguments, or its return value, contains a list/array. JSON has no tuple type, so the tracer records both lists and tuples as arrays, and the two can't be told apart from the trace alone. Reconstructing a tuple as a list would produce a failing assertion (`(1, 2) == [1, 2]` is `False`), so these are skipped for now rather than generate a test that fails. This is the biggest current limitation, since `return a, b` is a tuple; preserving container type in the trace would lift it.
- It raised an exception rather than returning normally (asserting on exceptions comes in a later step).
- It's an `async`, generator, or async-generator function. Calling one synchronously returns a coroutine or generator object, not the value the tracer recorded when it was awaited or resumed, so a plain `result = f(...)` assertion could never match.
- Its module name isn't a valid Python import target (e.g. a file named `class.py`), or the module can't be imported now (importing re-runs its top-level code, which may raise).
- Its signature can't be replayed with plain keyword arguments (positional-only parameters, `*args`, or `**kwargs`), or the function no longer exists in the module.
- Its recorded arguments no longer bind to the function's current signature (a parameter was renamed, removed, or a new required one was added since it was traced), which would make the generated call fail outright.
- It belongs to the entry script (module `"__main__"`). A bare trace log doesn't carry the entry script's file path, so its own functions can't be imported by name yet - this is lifted once tracing and generation share a single command.

**Known limitation (default arguments):** the trace records every parameter present in the call frame, including ones the caller left at their default, and can't tell an omitted argument from one passed explicitly. The generated test always passes each recorded argument by keyword. For the overwhelmingly common cases (immutable defaults like `None`, numbers, strings) this is exactly correct. It only misbehaves for a parameter whose default is a JSON-serializable object the function then compares by identity (e.g. `DEFAULT = {}` used as a sentinel via `x is DEFAULT`): the generated call passes a fresh equal object rather than the original default, so an identity check flips. Fixing this properly needs the tracer to record which arguments were passed explicitly, which is out of scope here.

**Known limitation (a target module named `conftest`):** pytest reserves the name `conftest` for its own per-directory setup files, and ai-tracer writes one to run the import setup. Both live in `sys.modules` under the name `conftest`, so if the traced program itself has (or imports) a module literally named `conftest`, a generated `from conftest import ...` resolves to ai-tracer's setup file rather than the target's. This only affects targets that use `conftest` as an ordinary module name, which is unusual since it's a pytest-specific convention; renaming that module in the target is the workaround.

**Known limitation (argument aliasing):** each recorded argument is reconstructed as its own fresh literal, so if one call passed the same mutable object through two parameters (`f(a=d, b=d)` with a shared `d`), the generated test passes two separate equal objects instead of one shared one. A function that detects the aliasing (mutating one parameter and reading it back through the other) would then behave differently than the traced run. Like the default-argument case, this needs the tracer to record object identity, which is out of scope here.

**Known limitation (order-dependent shared state):** each generated test asserts that a function, called with the recorded arguments, returns the recorded value - it assumes the function's result depends only on its arguments. A function whose result also depends on shared mutable state, and on the order calls happened in, isn't faithfully captured this way. Calls within one module keep their traced order, but pytest runs one module's tests independently of another's, so a return value that only held because some other module's function ran first can assert a value that no longer matches. This is inherent to turning individual recorded calls into independent tests, not something the generator can reorder its way out of.

---

## Development

```bash
ruff check .
pytest
```
