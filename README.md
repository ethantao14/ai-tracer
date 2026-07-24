# ai-tracer

**Eventually:** automatically generate pytest test cases by recording the real input/output of every function in a running Python program.

**Right now:** ai-tracer runs an arbitrary external Python program through its own CLI, correctly (imports from sibling files in the target work, and the target doesn't see ai-tracer's own command-line arguments), and records which functions it calls along the way, along with the arguments each call received, which function called which, and which module each call happened in. Return values aren't recorded yet, and test generation isn't built.

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
  { "call_id": 0, "parent_call_id": null, "module": "__main__", "qualname": "main", "args": {} },
  { "call_id": 1, "parent_call_id": 0, "module": "helper", "qualname": "double", "args": { "x": 21 } }
]
```

`call_id` is unique per call; `parent_call_id` is the `call_id` of whichever call was in progress when this one started (`null` for a top-level call). A worker thread's own calls form their own independent tree, `parent_call_id: null` at the root, even if the thread was started from within another traced call.

`module` is the `__name__` of the module the function was defined in - the same name Python's own import machinery assigns it. The entry script itself is always `"__main__"`, matching direct execution. A package's `__init__.py` resolves to the package's own dotted name (e.g. `"pkg"`, not `"pkg.__init__"`).

Argument values are recorded as of the moment the call happened, not whatever they become after the function runs. Anything that isn't JSON-serializable (e.g. a custom object) falls back to its `repr()`.

---

## Development

```bash
ruff check .
pytest
```
