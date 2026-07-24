# ai-tracer

**Eventually:** automatically generate pytest test cases by recording the real input/output of every function in a running Python program.

**Right now:** this is the first building block. ai-tracer can run an arbitrary external Python program through its own CLI, correctly (imports from sibling files in the target work, and the target doesn't see ai-tracer's own command-line arguments). Nothing is recorded yet, tracing and test generation aren't built.

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

---

## Development

```bash
ruff check .
pytest
```
