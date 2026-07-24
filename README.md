# ai-tracer

**Eventually:** automatically generate pytest test cases by recording the real input/output of every function in a running Python program.

**Right now:** ai-tracer runs an arbitrary external Python program through its own CLI, correctly (imports from sibling files in the target work, and the target doesn't see ai-tracer's own command-line arguments), and records which functions it calls along the way, along with the arguments each call received, what it returned (or that it raised instead), which function called which, and which module each call happened in. Test generation isn't built yet.

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
  { "call_id": 0, "parent_call_id": null, "module": "__main__", "qualname": "main", "args": {}, "raised": false, "return_value": null },
  { "call_id": 1, "parent_call_id": 0, "module": "helper", "qualname": "double", "args": { "x": 21 }, "raised": false, "return_value": 42 }
]
```

`call_id` is unique per call; `parent_call_id` is the `call_id` of whichever call was in progress when this one started (`null` for a top-level call). A worker thread's own calls form their own independent tree, `parent_call_id: null` at the root, even if the thread was started from within another traced call.

`module` is the `__name__` of the module the function was defined in - the same name Python's own import machinery assigns it. The entry script itself is always `"__main__"`, matching direct execution. A package's `__init__.py` resolves to the package's own dotted name (e.g. `"pkg"`, not `"pkg.__init__"`).

Argument and return values are recorded as JSON-safe snapshots: taken as of the moment the call happened (or returned), not whatever they become afterward, and falling back to `repr()` for anything that isn't JSON-serializable (e.g. a custom object).

`raised` is `true` if the call exited via an exception instead of a normal return, in which case `return_value` is always `null`. This is a best-effort signal, not a perfect one: a call that catches its own exception and then explicitly returns a non-`None` value is always detected correctly, but a call that catches its own exception and then returns `None` - explicitly (`return None`) or implicitly (no `return` statement afterward) - is indistinguishable, at this level, from one that let the exception propagate - `raised` is set to `true` in that ambiguous case too, favoring never missing a real propagating exception over occasionally over-reporting one.

---

## Development

```bash
ruff check .
pytest
```
