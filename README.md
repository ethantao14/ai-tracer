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
  { "call_id": 0, "parent_call_id": null, "module": "__main__", "qualname": "main", "args": {}, "arg_serialization": {}, "raised": false, "return_value": null, "return_serialization": null },
  { "call_id": 1, "parent_call_id": 0, "module": "helper", "qualname": "double", "args": { "x": 21 }, "arg_serialization": { "x": "json" }, "raised": false, "return_value": 42, "return_serialization": "json" }
]
```

`call_id` is unique per call; `parent_call_id` is the `call_id` of whichever call was in progress when this one started (`null` for a top-level call). A worker thread's own calls form their own independent tree, `parent_call_id: null` at the root, even if the thread was started from within another traced call.

`module` is the `__name__` of the module the function was defined in - the same name Python's own import machinery assigns it. The entry script itself is always `"__main__"`, matching direct execution. A package's `__init__.py` resolves to the package's own dotted name (e.g. `"pkg"`, not `"pkg.__init__"`).

Argument and return values are recorded as JSON-safe snapshots: taken as of the moment the call happened (or returned), not whatever they become afterward, and falling back to `repr()` for anything that isn't JSON-serializable (e.g. a custom object). `arg_serialization` (per argument name) and `return_serialization` mark which kind each one is, `"json"` for a real JSON value or `"repr"` for a fallback string, so a consumer can tell "the target actually passed/returned this string" apart from "this string is just a repr() of something unrepresentable." `return_serialization` is `null` whenever `return_value` is (a call that raised, or one whose `"return"` event couldn't distinguish a real value from `None`).

`raised` is `true` if the call exited via an exception instead of a normal return, in which case `return_value` is always `null`. This is a best-effort signal, not a perfect one: a call that catches its own exception and then explicitly returns a non-`None` value is always detected correctly, but a call that catches its own exception and then returns `None` - explicitly (`return None`) or implicitly (no `return` statement afterward) - is indistinguishable, at this level, from one that let the exception propagate - `raised` is set to `true` in that ambiguous case too, favoring never missing a real propagating exception over occasionally over-reporting one.

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

Not every recorded call becomes a test. A call is skipped, with a one-line reason printed to stderr (so it's never a silent no-op), when it can't be reconstructed safely as a standalone test:

- It isn't a plain top-level function (a method, nested function, or lambda). Only module-level functions are importable by name.
- Any of its arguments, or its return value, fell back to `repr()` rather than a real JSON value (`arg_serialization`/`return_serialization` is `"repr"`) - a `repr()` string isn't a reconstructable literal.
- It raised an exception rather than returning normally (asserting on exceptions comes in a later step).
- Its module name isn't a valid Python import target (e.g. a file named `class.py`), or the module can't be imported now (importing re-runs its top-level code, which may raise).
- Its signature can't be replayed with plain keyword arguments (positional-only parameters, `*args`, or `**kwargs`), or the function no longer exists in the module.
- It belongs to the entry script (module `"__main__"`). A bare trace log doesn't carry the entry script's file path, so its own functions can't be imported by name yet - this is lifted once tracing and generation share a single command.

---

## Development

```bash
ruff check .
pytest
```
