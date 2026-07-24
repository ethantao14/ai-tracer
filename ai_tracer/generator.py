import importlib
import inspect
import json
import keyword
import sys
from pathlib import Path

_UNSUPPORTED_PARAM_KINDS = {
    inspect.Parameter.POSITIONAL_ONLY,
    inspect.Parameter.VAR_POSITIONAL,
    inspect.Parameter.VAR_KEYWORD,
}


def _is_valid_import_name(part):
    # str.isidentifier() alone isn't enough: "class".isidentifier() is True,
    # but it's a reserved keyword, so "from class import add" is still a
    # SyntaxError. A traced file can legally be named "class.py" - filenames
    # aren't restricted by Python's keyword list.
    return part.isidentifier() and not keyword.iskeyword(part)


def generate(trace_log_path, target_dir, output_dir="generated_tests"):
    calls = json.loads(Path(trace_log_path).read_text())
    target_dir = str(Path(target_dir).resolve())

    calls_by_module = {}
    module_cache = {}
    signature_cache = {}

    sys.path.insert(0, target_dir)
    try:
        for call in calls:
            reason = _skip_reason(call, module_cache, signature_cache)
            if reason is None:
                calls_by_module.setdefault(call["module"], []).append(call)
            else:
                print(
                    f"Skipping {call['module']}.{call['qualname']}: {reason}",
                    file=sys.stderr,
                )
    finally:
        sys.path.remove(target_dir)

    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True, parents=True)

    written_paths = []
    for module_name, module_calls in calls_by_module.items():
        file_name = f"test_{module_name.replace('.', '_')}.py"
        test_path = output_path / file_name
        test_path.write_text(_render_test_module(module_name, module_calls, target_dir))
        written_paths.append(test_path)

    return written_paths


def _skip_reason(call, module_cache, signature_cache):
    # Returns None if the call can be generated, otherwise a short,
    # human-readable reason it's being skipped - printed to stderr by the
    # caller so a run's output makes it obvious what didn't make it into
    # generated_tests/ and why, rather than silently doing nothing.

    # Only plain top-level functions for now: qualname.isidentifier() is
    # False for methods ("ClassName.method"), nested functions
    # ("outer.<locals>.inner"), and lambdas ("<lambda>") alike.
    if not call["qualname"].isidentifier():
        return "not a plain top-level function (method, nested function, or lambda)"
    # The entry script is always recorded as module "__main__" (matching
    # direct execution - see the tracer's own module resolution), but
    # "__main__" isn't a name this process can re-import to mean "the
    # traced script": it would resolve to whatever *this* process's own
    # entry point happens to be. Generating tests for the entry script's
    # own functions needs its real file path, which a bare trace log
    # doesn't carry - deferred to the PR that wires trace+generate
    # together, where the CLI already has that path.
    if call["module"] == "__main__":
        return "the entry script's own functions aren't generatable yet (module \"__main__\" has no real import path)"
    if not all(_is_valid_import_name(part) for part in call["module"].split(".")):
        return f"module {call['module']!r} is not a valid import target"
    # Non-JSON (repr-fallback) values are never reconstructable as a
    # literal, don't generate a test that's likely to be wrong.
    if any(kind != "json" for kind in call["arg_serialization"].values()):
        return "one or more arguments could not be captured as a JSON value"
    if call["raised"]:
        return "call raised an exception, not supported yet"
    if call["return_serialization"] != "json":
        return "return value could not be captured as a JSON value"
    # Importing re-executes the module's top-level code, which can raise
    # anything, including whatever originally crashed the target program
    # (the tracer writes the trace log even when the target crashed). Check
    # this once, with a specific message, rather than let a confusing
    # failure surface deeper in the signature check below.
    if _get_module(call["module"], module_cache) is None:
        return (
            f"module {call['module']!r} could not be imported (it may raise on import)"
        )
    if not _has_supported_signature(
        call["module"], call["qualname"], module_cache, signature_cache
    ):
        return (
            "function no longer exists, or has an unsupported signature "
            "(positional-only, *args, or **kwargs)"
        )
    return None


def _fresh_import(module_name):
    # Force a fresh import rather than reusing whatever sys.modules happens
    # to have cached - generate() can be called more than once in the same
    # process (as our own tests do), each time potentially pointing at a
    # different target_dir with an unrelated module that happens to share a
    # name. Popping only the leaf module isn't enough for a dotted name
    # ("pkg.calc"): if the parent package ("pkg") is still cached, its
    # __path__ still points at the *previous* target_dir, so re-importing
    # the submodule would find the stale file through the stale parent.
    parts = module_name.split(".")
    for i in range(len(parts)):
        sys.modules.pop(".".join(parts[: i + 1]), None)
    return importlib.import_module(module_name)


def _get_module(module_name, module_cache):
    # Cached per generate() call (not a module-level global, that would
    # leak across separate calls, see _fresh_import), so a module
    # referenced by many calls is only actually imported, and its top-level
    # code only actually re-executed, once.
    if module_name not in module_cache:
        try:
            module_cache[module_name] = _fresh_import(module_name)
        except Exception:  # noqa: BLE001 - importing target code can raise anything
            module_cache[module_name] = None
    return module_cache[module_name]


def _has_supported_signature(module_name, qualname, module_cache, signature_cache):
    key = (module_name, qualname)
    if key not in signature_cache:
        module = _get_module(module_name, module_cache)
        try:
            function = getattr(module, qualname)
            parameters = inspect.signature(function).parameters.values()
        except (AttributeError, TypeError, ValueError):
            # The trace log can reference a function that no longer exists
            # or can't be introspected (e.g. removed or renamed since it
            # was traced) - skip it rather than crash the whole run.
            signature_cache[key] = False
        else:
            signature_cache[key] = not any(
                p.kind in _UNSUPPORTED_PARAM_KINDS for p in parameters
            )
    return signature_cache[key]


def _render_test_module(module_name, module_calls, target_dir):
    imported_names = sorted({call["qualname"] for call in module_calls})

    lines = [
        "import sys",
        "",
        f"sys.path.insert(0, {target_dir!r})",
        f"from {module_name} import {', '.join(imported_names)}",
    ]

    call_counts = {}
    for call in module_calls:
        qualname = call["qualname"]
        index = call_counts.get(qualname, 0)
        call_counts[qualname] = index + 1

        args = ", ".join(f"{name}={value!r}" for name, value in call["args"].items())
        lines += ["", ""]
        lines.append(f"def test_{qualname}_{index}():")
        lines.append(f"    result = {qualname}({args})")
        lines.append(f"    assert result == {call['return_value']!r}")

    return "\n".join(lines) + "\n"


def main():
    argv = sys.argv[1:]
    if len(argv) not in (2, 3):
        print(
            "usage: python -m ai_tracer.generator <trace_log> <target_dir> [output_dir]",
            file=sys.stderr,
        )
        sys.exit(1)
    trace_log_path, target_dir, *rest = argv
    output_dir = rest[0] if rest else "generated_tests"
    for path in generate(trace_log_path, target_dir, output_dir):
        print(path)


if __name__ == "__main__":
    main()
