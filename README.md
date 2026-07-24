# ai-tracer

**Eventually:** automatically generate pytest test cases by recording the real input/output of every function in a running Python program.

**Right now:** ai-tracer runs an arbitrary external Python program through its own CLI, correctly (imports from sibling files in the target work, and the target doesn't see ai-tracer's own command-line arguments), and records which functions it calls along the way, along with the arguments each call received. Return values and the call tree aren't recorded yet, and test generation isn't built.

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

Every function the target program calls (in files under the target's own directory) gets recorded to `path/to/your_program.trace.json`, in call order, along with the arguments it was called with:

```json
[
  { "qualname": "main", "args": {} },
  { "qualname": "double", "args": { "x": 21 } }
]
```

Argument values are recorded as of the moment the call happened, not whatever they become after the function runs. Anything that isn't JSON-serializable (e.g. a custom object) falls back to its `repr()`.

---

## Development

```bash
ruff check .
pytest
```
