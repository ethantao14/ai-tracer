# ai-tracer

**Eventually:** automatically generate pytest test cases by recording the real input/output of every function in a running Python program.

**Right now:** ai-tracer runs an arbitrary external Python program through its own CLI, correctly (imports from sibling files in the target work, and the target doesn't see ai-tracer's own command-line arguments), and records which functions it calls along the way, along with the arguments each call received, what it returned (or that it raised instead), which function called which, and which module each call happened in. From that recording it generates a pytest test per recorded call - asserting the return value, or `pytest.raises` for the exception it raised - for the calls it can reconstruct safely (see "Generating tests" below). Tracing and generation happen together by default (one command does both); each step can still be run on its own if you want to regenerate from an existing trace without re-running the program.

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

This runs the target program the same way `python path/to/your_program.py` would, just through ai-tracer's own harness, and by default also generates pytest tests from what it recorded (see "Generating tests" below) into `generated_tests/` next to the program - the same as running `./scripts/generate.sh` yourself afterward. This still happens even if the program crashes, from whatever it recorded before the crash.

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

`./scripts/run.sh` above already does this by default. To regenerate from an existing trace log without re-running the program (e.g. after editing the target's code):

```bash
./scripts/generate.sh path/to/your_program.trace.json path/to/target_dir generated_tests path/to/your_program.py
```

The arguments are the trace log, the target program's own directory (the same directory the traced program lives in, used to import the functions under test), an output directory for the generated tests (defaults to `generated_tests/`), and optionally the entry script's own path - passing it is what makes the entry script's own functions generatable (see the `"__main__"` skip reason below); `./scripts/run.sh` always passes this automatically.

This writes one `test_<module>.py` file per module, with one test per recorded call. A call that returned normally replays the exact arguments and asserts the exact return value; a call that raised replays the arguments inside `pytest.raises(...)` for the exact exception it raised:

```python
import sys
import pytest

sys.path.insert(0, "path/to/target_dir")
import errors
from helper import divide


def test_divide_0():
    result = divide(a=10, b=2)
    assert result == 5


def test_divide_1():
    with pytest.raises(ZeroDivisionError):
        divide(a=1, b=0)


def test_check_0():
    with pytest.raises(errors.AppError):
        check(n=-1)
```

A built-in exception is named directly (`ZeroDivisionError`); a target-defined one is imported by its module and named module-qualified (`errors.AppError`), so it can't collide with the function names imported for the tests.

It also writes a `conftest.py` next to them. That file runs once when pytest starts and clears any target module cached under the same name (a target module shadowing another, or a stale entry from a previous run) so every generated test imports the target's own code. Doing this once, rather than in each test file, keeps a module or package that other target modules import at load time from being re-executed per file. Run the generated tests as their own pytest invocation, e.g. `pytest generated_tests/`.

If a `conftest.py` ai-tracer didn't generate already exists in the output directory, it's left untouched: the generated tests still import the target through their own `sys.path` setup, but the one-time module eviction is skipped (a warning is printed), so a same-named module already cached in that pytest session could shadow the target's. Point the output at an empty directory (the default `generated_tests/`) to get the full setup.

Not every recorded call becomes a test. A call is skipped, with a one-line reason printed to stderr (so it's never a silent no-op), when it can't be reconstructed safely as a standalone test:

- It isn't a plain top-level function (a method, nested function, or lambda). Only module-level functions are importable by name.
- Any of its arguments, or its return value, fell back to `repr()` rather than a real JSON value (`arg_serialization`/`return_serialization` is `"repr"`) - a `repr()` string isn't a reconstructable literal.
- Any of its arguments, or its return value, contains a list/array. JSON has no tuple type, so the tracer records both lists and tuples as arrays, and the two can't be told apart from the trace alone. Reconstructing a tuple as a list would produce a failing assertion (`(1, 2) == [1, 2]` is `False`), so these are skipped for now rather than generate a test that fails. This is the biggest current limitation, since `return a, b` is a tuple; preserving container type in the trace would lift it.
- It raised an exception whose type can't be named in the generated test: not a plain identifier, not an importable module (a built-in that isn't really built-in, or an exception defined in the entry script's `__main__`), or a type that module no longer has.
- It's an `async`, generator, or async-generator function. Calling one synchronously returns a coroutine or generator object, not the value the tracer recorded when it was awaited or resumed, so a plain `result = f(...)` assertion could never match.
- Its module name isn't a valid Python import target (e.g. a file named `class.py`), or the module can't be imported now (importing re-runs its top-level code, which may raise).
- Its signature can't be replayed with plain keyword arguments (positional-only parameters, `*args`, or `**kwargs`), or the function no longer exists in the module.
- Its recorded arguments no longer bind to the function's current signature (a parameter was renamed, removed, or a new required one was added since it was traced), which would make the generated call fail outright.
- It belongs to the entry script (module `"__main__"`) and no entry script path was given. A bare trace log doesn't carry the entry script's file path by itself, so its own functions can only be imported by name when that path is passed in - `./scripts/run.sh` does this automatically; regenerating standalone needs the optional fourth argument to `./scripts/generate.sh`.

**Known limitation (default arguments):** the trace records every parameter present in the call frame, including ones the caller left at their default, and can't tell an omitted argument from one passed explicitly. The generated test always passes each recorded argument by keyword. For the overwhelmingly common cases (immutable defaults like `None`, numbers, strings) this is exactly correct. It only misbehaves for a parameter whose default is a JSON-serializable object the function then compares by identity (e.g. `DEFAULT = {}` used as a sentinel via `x is DEFAULT`): the generated call passes a fresh equal object rather than the original default, so an identity check flips. Fixing this properly needs the tracer to record which arguments were passed explicitly, which is out of scope here.

**Known limitation (a target module named `conftest`):** pytest reserves the name `conftest` for its own per-directory setup files, and ai-tracer writes one to run the import setup. Both live in `sys.modules` under the name `conftest`, so if the traced program itself has (or imports) a module literally named `conftest`, a generated `from conftest import ...` resolves to ai-tracer's setup file rather than the target's. This only affects targets that use `conftest` as an ordinary module name, which is unusual since it's a pytest-specific convention; renaming that module in the target is the workaround.

**Known limitation (argument aliasing):** each recorded argument is reconstructed as its own fresh literal, so if one call passed the same mutable object through two parameters (`f(a=d, b=d)` with a shared `d`), the generated test passes two separate equal objects instead of one shared one. A function that detects the aliasing (mutating one parameter and reading it back through the other) would then behave differently than the traced run. Like the default-argument case, this needs the tracer to record object identity, which is out of scope here.

**Known limitation (raised false positive):** the generator trusts the trace's `raised` flag, matching the record-then-generate design (it never re-runs a function to second-guess the recording). But `raised` has a documented false positive: a function that catches its own exception and returns `None` - explicitly or implicitly - is recorded as having raised (see the `raised` note above). For such a call the generator emits a `pytest.raises(...)` test that fails, because replaying the function returns `None` instead of raising. This affects the common defensive pattern `try: return d[k] except KeyError: return None`. Reviewing generated `pytest.raises` tests is worthwhile; a call that catches and returns a non-`None` value is recorded correctly (`raised` is `false`) and generates a normal assertion instead.

**Known limitation (exception attribution):** a generated `pytest.raises(...)` names whichever exception the tracer recorded for the call, which is the escaping one except in the rare case where a `finally` block raises and catches a different exception while the original propagates (see the trace-format note on `exception_type` above). In that case the generated test asserts the wrong exception and would fail.

**Known limitation (entry script re-executes):** generating a test for the entry script's own function loads that script a second time, under a different module name so its `if __name__ == "__main__":` guard doesn't fire again - but any top-level code outside that guard (module-level side effects, not function definitions) runs again anyway, the same as importing any other target module for inspection re-runs its top-level code. This happens automatically every time `./scripts/run.sh` is used, not just when standalone regeneration is a deliberate, separate step.

**Known limitation (order-dependent shared state):** each generated test asserts that a function, called with the recorded arguments, returns the recorded value - it assumes the function's result depends only on its arguments. A function whose result also depends on shared mutable state, and on the order calls happened in, isn't faithfully captured this way. Calls within one module keep their traced order, but pytest runs one module's tests independently of another's, so a return value that only held because some other module's function ran first can assert a value that no longer matches. This is inherent to turning individual recorded calls into independent tests, not something the generator can reorder its way out of.

---

## Development

```bash
ruff check .
pytest
```
