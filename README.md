# ai-tracer

Automatically generates pytest test cases by recording the real input/output of every function call a running Python program makes. ai-tracer runs an arbitrary external Python program through its own CLI, correctly (imports from sibling files in the target work, and the target doesn't see ai-tracer's own command-line arguments), and records which functions it calls along the way, along with the arguments each call received, what it returned (or that it raised instead), which function called which, and which module each call happened in. From that recording it generates a pytest test per recorded call - asserting the return value, or `pytest.raises` for the exception it raised - for the calls it can reconstruct safely (see "Generating tests" below). A generated test also mocks out any function the call under test directly calls, standing in with its recorded return value or exception, so the test exercises only that one function - not everything it happens to call underneath. Tracing and generation happen together by default (one command does both); each step can still be run on its own if you want to regenerate from an existing trace without re-running the program.

---

## Installation

```bash
git clone <repo-url>
cd ai-tracer
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

ai-tracer runs your target program in this same Python process, so if your program imports anything beyond the standard library (e.g. `requests`, `numpy`), install those into this same virtualenv too - `pip install <your-dependency>` with it still active. A target that only imports the standard library, or its own sibling files, needs nothing extra.

---

## Quickstart

A minimal, no-context-needed walkthrough - the whole workflow on a two-file example, before pointing ai-tracer at a real project.

1. Anywhere on your machine (this doesn't need to be inside the ai-tracer repo), make a small folder and put a program in it. For example, `shop.py`:

   ```python
   def apply_discount(price, percent):
       return price - (price * percent / 100)

   def total(price, percent):
       return round(apply_discount(price, percent), 2)

   if __name__ == "__main__":
       total(100, 10)
   ```

2. With ai-tracer's virtualenv active (`source path/to/ai-tracer/.venv/bin/activate` - see Installation above), `cd` into that folder and run your program through ai-tracer instead of calling `python` directly:

   ```bash
   python -m ai_tracer.cli shop.py
   ```

   This runs `shop.py` exactly the way `python shop.py` would - same output, same behavior - while recording every function it calls along the way.

3. Two new things show up next to it:
   - `shop.trace.json` - a record of every call: what function, with what arguments, and what it returned (see "Running" below for the full field-by-field reference).
   - `generated_tests/` - real pytest test files, written from that recording. You didn't write any of these by hand.

4. Run what it wrote:

   ```bash
   pytest generated_tests/
   ```

   They pass, because each one just replays a call you already watched happen. `total`'s test even mocks out `apply_discount` inside it (see "Generating tests" below), so it only tests `total` itself, not everything underneath it.

That's the whole loop: run your program once through ai-tracer, get a working test suite for free. Everything past this point is reference material - the exact trace format, the exact generated-test format, and every case ai-tracer intentionally skips rather than risk a wrong test.

---

## Running

```bash
./scripts/run.sh path/to/your_program.py
```

This runs the target program the same way `python path/to/your_program.py` would, just through ai-tracer's own harness, and by default also generates pytest tests from what it recorded (see "Generating tests" below) into `generated_tests/` next to the program - the same as running `./scripts/generate.sh` yourself afterward. This still happens even if the program crashes, from whatever it recorded before the crash. `./scripts/run.sh` is a one-line wrapper around `python -m ai_tracer.cli` (used in the Quickstart above) - the two are interchangeable; `./scripts/run.sh` just assumes you're invoking it from inside the ai-tracer repo (or referencing it by its own path from elsewhere), while `python -m ai_tracer.cli` works from anywhere the virtualenv is active.

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

Environment variable values are redacted from the trace log, but only for variables whose *name* looks secret-like (containing `key`, `token`, `secret`, `password`, `pwd`, `credential`, `auth`, or `api`, case-insensitively): any string that exactly matches, or contains as a substring, one of those variables' values at the time tracing started is replaced with `"<redacted:env>"`. Exact matches are caught regardless of length (even a short secret like a PIN); substring matches additionally require the env value to be 3+ characters. Restricting redaction (both exact and substring) to secret-looking names, rather than every env var, is deliberate: a generic CI-provided variable like `GITHUB_JOB=test` or `GITHUB_REF_NAME=main` holds a short, extremely common value that collides constantly with ordinary code, not just as a substring (`"domain.com"`, `"maintenance"`, this project's own `test_tracer` module name all contain `"test"`), but as an exact match too (a function plainly named `main`, the single most common Python entry-point name, exactly equals `"main"` on the default branch). A secret stored in a variable without a recognizable name (e.g. `DATABASE_URL` embedding a password) is not covered by this heuristic - a known, accepted limitation. A short denylist (`PWD`, `OLDPWD`, `SSH_AUTH_SOCK`) excludes well-known non-secret variables that would otherwise incidentally match a marker (`PWD`/`OLDPWD` via `"pwd"`, `SSH_AUTH_SOCK` via `"auth"`) and are present in nearly every real environment. This applies to argument values, return values, values nested inside containers (including dict keys), and the `repr()` fallback text used for non-JSON-safe objects (which can itself embed a secret, e.g. via a class's `__module__`), so secrets like API keys read via `os.getenv(...)` or `os.environ[...]` never appear in the trace. Structural metadata (`module`, `qualname`, `exception_module`, `exception_type`) additionally never gets substring scanning even for a secret-named variable: a target rebinding `__name__`, a function's code object's qualname, or an exception's `__module__`/`__qualname__` (all writable/replaceable) directly to a secret's exact value is still caught, but a metadata field merely *containing* a secret value as a substring is left alone, since that's a plausible false-positive collision with a legitimate short identifier rather than an actual leak. Env vars set after tracing starts are not captured (the snapshot is taken at `start()` time); this is an accepted limitation of the non-intercepting design, which never modifies `os.environ` or `os.getenv` to avoid changing target behavior.

A consequence of redaction: an argument or return value that gets redacted is no longer equivalent to what actually ran, so it's tagged `"repr"` (not `"json"`) the same as any other non-JSON-safe value - the generator already skips replaying anything tagged `"repr"` rather than treating it as a literal, so a call involving a secret is skipped from generation entirely (with a reason printed to stderr) instead of producing a test with a fragile or misleading assertion.

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

If the call under test itself called other traced functions, those direct calls are mocked out with `unittest.mock`, so the test exercises only the one function - not whatever it happens to call underneath. Say `divide` above actually calls a `round_result` helper before returning:

```python
from unittest import mock

...

def test_divide_0():
    with mock.patch('helper.round_result', return_value=5) as _mock_round_result:
        result = divide(a=10, b=2)
        assert result == 5
```

Each distinct child gets its own `mock.patch(...)` clause (several share one `with` line when there's more than one), and a child called more than once gets a `side_effect=` list replaying every recorded outcome in traced order instead of a single `return_value=`; a child that raised is named as its exception class the same way a top-level raised call is, target-defined exceptions imported and aliased alongside the others. A call with no children of its own is left as a plain `result = ...`/`pytest.raises(...)` body, exactly as before.

It also writes a `conftest.py` next to them. That file runs once when pytest starts and clears any target module cached under the same name (a target module shadowing another, or a stale entry from a previous run) so every generated test imports the target's own code. Doing this once, rather than in each test file, keeps a module or package that other target modules import at load time from being re-executed per file. Run the generated tests as their own pytest invocation, e.g. `pytest generated_tests/`.

If a `conftest.py` ai-tracer didn't generate already exists in the output directory, it's left untouched: the generated tests still import the target through their own `sys.path` setup, but the one-time module eviction is skipped (a warning is printed), so a same-named module already cached in that pytest session could shadow the target's. Point the output at an empty directory (the default `generated_tests/`) to get the full setup.

Not every recorded call becomes a test. A call is skipped, with a one-line reason printed to stderr (so it's never a silent no-op), when it can't be reconstructed safely as a standalone test:

- It isn't a plain top-level function (a method, nested function, or lambda). Only module-level functions are importable by name.
- Any of its arguments, or its return value, fell back to `repr()` rather than a real JSON value (`arg_serialization`/`return_serialization` is `"repr"`) - a `repr()` string isn't a reconstructable literal.
- Any of its arguments, or its return value, contains a list/array. JSON has no tuple type, so the tracer records both lists and tuples as arrays, and the two can't be told apart from the trace alone. Reconstructing a tuple as a list would produce a failing assertion (`(1, 2) == [1, 2]` is `False`), so these are skipped for now rather than generate a test that fails. This is the biggest current limitation, since `return a, b` is a tuple; preserving container type in the trace would lift it.
- It raised an exception whose type can't be named in the generated test: not a plain identifier, not an importable module (a built-in that isn't really built-in, or one defined in the entry script's `__main__` when no entry script path was given, or when the raising function and the exception aren't defined in the same module), or a type that module no longer has.
- It's an `async`, generator, or async-generator function. Calling one synchronously returns a coroutine or generator object, not the value the tracer recorded when it was awaited or resumed, so a plain `result = f(...)` assertion could never match.
- Its module name isn't a valid Python import target (e.g. a file named `class.py`), or the module can't be imported now (importing re-runs its top-level code, which may raise).
- Its signature can't be replayed with plain keyword arguments (positional-only parameters, `*args`, or `**kwargs`), or the function no longer exists in the module.
- Its recorded arguments no longer bind to the function's current signature (a parameter was renamed, removed, or a new required one was added since it was traced), which would make the generated call fail outright.
- It belongs to the entry script (module `"__main__"`) and no entry script path was given. A bare trace log doesn't carry the entry script's file path by itself, so its own functions can only be imported by name when that path is passed in - `./scripts/run.sh` does this automatically; regenerating standalone needs the optional fourth argument to `./scripts/generate.sh`.
- One of the functions it directly called (a "child" call) can't be mocked - for any of the same reasons above that would skip that child as a call on its own, except its own arguments never matter (mocking replaces the whole child call, not just part of it). Only direct children matter; whatever a mocked child itself would have called never gets a chance to run either, so it's irrelevant whether that's mockable.

**Known limitation (mock patch target):** a mocked child is patched under the *calling* function's own name for it (e.g. `helper.round_result`), not the module it's actually defined in - the standard "patch where it's looked up" idiom. This assumes the caller reached the child via a plain `from its_module import name`. If the caller instead used `import its_module; its_module.name(...)`, the calling module has no attribute of that name, so `mock.patch()` raises `AttributeError` immediately - the generated test fails visibly rather than silently under-isolating. The one way this goes wrong silently: if the calling module happens to define or import some unrelated attribute under the same name as the child's qualname, while reaching the real child a different way, the mock patches the wrong thing without any error. This is accepted as a rare edge case; patching the child's own defining module instead was considered and rejected because it would require reimporting the calling module after the patch is applied, breaking the "each shared module is imported once per session" design the generated `conftest.py` relies on - and its own failure mode is silent (a stale, unpatched reference just keeps working) rather than loud.

**Known limitation (default arguments):** the trace records every parameter present in the call frame, including ones the caller left at their default, and can't tell an omitted argument from one passed explicitly. The generated test always passes each recorded argument by keyword. For the overwhelmingly common cases (immutable defaults like `None`, numbers, strings) this is exactly correct. It only misbehaves for a parameter whose default is a JSON-serializable object the function then compares by identity (e.g. `DEFAULT = {}` used as a sentinel via `x is DEFAULT`): the generated call passes a fresh equal object rather than the original default, so an identity check flips. Fixing this properly needs the tracer to record which arguments were passed explicitly, which is out of scope here.

**Known limitation (a target module named `conftest`):** pytest reserves the name `conftest` for its own per-directory setup files, and ai-tracer writes one to run the import setup. Both live in `sys.modules` under the name `conftest`, so if the traced program itself has (or imports) a module literally named `conftest`, a generated `from conftest import ...` resolves to ai-tracer's setup file rather than the target's. This only affects targets that use `conftest` as an ordinary module name, which is unusual since it's a pytest-specific convention; renaming that module in the target is the workaround.

**Known limitation (argument aliasing):** each recorded argument is reconstructed as its own fresh literal, so if one call passed the same mutable object through two parameters (`f(a=d, b=d)` with a shared `d`), the generated test passes two separate equal objects instead of one shared one. A function that detects the aliasing (mutating one parameter and reading it back through the other) would then behave differently than the traced run. Like the default-argument case, this needs the tracer to record object identity, which is out of scope here.

**Known limitation (raised false positive):** the generator trusts the trace's `raised` flag, matching the record-then-generate design (it never re-runs a function to second-guess the recording). But `raised` has a documented false positive: a function that catches its own exception and returns `None` - explicitly or implicitly - is recorded as having raised (see the `raised` note above). For such a call the generator emits a `pytest.raises(...)` test that fails, because replaying the function returns `None` instead of raising. This affects the common defensive pattern `try: return d[k] except KeyError: return None`. Reviewing generated `pytest.raises` tests is worthwhile; a call that catches and returns a non-`None` value is recorded correctly (`raised` is `false`) and generates a normal assertion instead.

**Known limitation (exception attribution):** a generated `pytest.raises(...)` names whichever exception the tracer recorded for the call, which is the escaping one except in the rare case where a `finally` block raises and catches a different exception while the original propagates (see the trace-format note on `exception_type` above). In that case the generated test asserts the wrong exception and would fail.

**Known limitation (entry script re-executes):** generating a test for the entry script's own function loads that script a second time, under a different module name so its `if __name__ == "__main__":` guard doesn't fire again - but any top-level code outside that guard (module-level side effects, not function definitions) runs again anyway, the same as importing any other target module for inspection re-runs its top-level code. This happens automatically every time `./scripts/run.sh` is used, not just when standalone regeneration is a deliberate, separate step.

**Known limitation (entry script module identity):** the generated test patches the reloaded entry script's `__name__` back to `"__main__"` after loading it, so a function that reads the bare `__name__` global still sees what it saw when traced. But every function and class defined while the file executed already has its own `__module__` baked in from the internal name used to load it, and `sys.modules["__main__"]` isn't repointed to this reloaded copy either. A function whose behavior depends on `__module__`, or that looks itself up via `sys.modules[__name__]`, rather than reading the bare `__name__` global, can still generate a test that fails even though the traced call itself returned successfully. Like the argument-identity limitations above, this needs deeper identity tracking that's out of scope here.

**Known limitation (order-dependent shared state):** each generated test asserts that a function, called with the recorded arguments, returns the recorded value - it assumes the function's result depends only on its arguments. A function whose result also depends on shared mutable state, and on the order calls happened in, isn't faithfully captured this way. Calls within one module keep their traced order, but pytest runs one module's tests independently of another's, so a return value that only held because some other module's function ran first can assert a value that no longer matches. This is inherent to turning individual recorded calls into independent tests, not something the generator can reorder its way out of.

---

## AI-generated tests

In addition to the deterministic trace-replay tests above, ai-tracer can use an LLM to generate test cases with varied inputs (edge cases, boundary values, error conditions). These are written to separate `test_<module>_ai.py` files alongside the deterministic ones, so the two never interfere.

Currently covers plain, top-level functions outside the entry script, where the AI-proposed input doesn't raise. An input that causes the function to raise during verification is skipped rather than turned into a test, and a function that's part of the entry script isn't covered yet either - both are planned for a later change.

### Enabling

Install the optional `ai` extra, then pass `--ai` to the CLI:

```bash
pip install 'ai-tracer[ai]'
python -m ai_tracer.cli --ai path/to/your_program.py
```

The `--ai` flag must come before the program name (it's parsed before program arguments). When enabled, ai-tracer first generates the deterministic tests as usual, then generates AI tests on top.

### How it works

For each generatable function, ai-tracer sends the function's signature and recorded calls (arguments, return values, exceptions) to an LLM and asks for test case inputs. The LLM only proposes inputs -- it never provides expected outputs. For each proposed input, ai-tracer calls the real traced function and uses the actual return value as the assertion, so every generated test is verified against real behavior, not LLM claims.

Function source code is never sent to the LLM -- only the signature and trace data, and the signature itself has any default values and annotations stripped before sending, so a default like `token="secret"` isn't leaked either. This ensures secrets or sensitive logic living in source text are not exposed to a third-party API.

### Configuration

The LLM provider is fully configurable via environment variables, so you can use OpenAI, Anthropic's OpenAI-compatible endpoint, OpenRouter, or a local server (Ollama, LM Studio):

```bash
export OPENAI_API_KEY=your-api-key
export OPENAI_BASE_URL=https://api.openai.com/v1  # or your provider's endpoint
export AI_TRACER_MODEL=gpt-4o-mini  # or any model your provider supports
```

`OPENAI_API_KEY` is required. `OPENAI_BASE_URL` and `AI_TRACER_MODEL` are optional (default: `https://api.openai.com/v1` and `gpt-4o-mini`).

### Generated test format

AI-generated tests look like the deterministic ones, but with varied inputs:

```python
# generated by ai-tracer - AI-generated test cases, do not edit
import sys

sys.path.insert(0, "path/to/target_dir")
from helper import add


def test_add_0():
    result = add(a=0, b=0)
    assert result == 0


def test_add_1():
    result = add(a=-1, b=1)
    assert result == 0
```

**Known limitation (order-dependent shared state, across files):** like the deterministic generator's own version of this limitation above, an AI test asserts a value that was verified by actually calling the function once during generation. If the target module has shared mutable state, that verification call and the deterministic test file's replay call are two independent calls against the same state, with no guaranteed order between `test_<module>.py` and `test_<module>_ai.py` when pytest runs both in one session - so an AI test can assert a value that no longer matches by the time it runs. This is a consequence of the deliberate choice to keep AI and deterministic tests in separate files (see "How it works" above); merging them into a single ordered file would remove the failure mode but wasn't the direction taken here.

The same risk exists within a single generation run, not just across files: verifying an AI-proposed input means actually calling the real function, side effects included, even for an input that ends up skipped (it raised, or its result couldn't be rendered). If the target has shared mutable state, a skipped call's side effect still happened and can shift what a later, kept call sees - so a kept call's asserted value can reflect state that no longer exists once the skipped call is left out of the generated file. There's no general way to undo an arbitrary function's side effects after the fact, so this is accepted as part of the same "verify against the real function" tradeoff, not something generation can detect or roll back.

---

## Development

```bash
ruff check .
pytest
```
