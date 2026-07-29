import json
import runpy
import sys
from pathlib import Path

from ai_tracer import generator, tracer


def _local_module_names(target_dir):
    names = set()
    for entry in Path(target_dir).iterdir():
        if entry.is_file() and entry.suffix == ".py":
            names.add(entry.stem)
        elif entry.is_dir() and (entry / "__init__.py").is_file():
            names.add(entry.name)
    return names


def run(target_path, program_args=(), restore=True, trace_output=None):
    # restore=False for the CLI: atexit handlers need the target's state, not ours.
    resolved_path = Path(target_path).resolve()
    target_dir = str(resolved_path.parent)
    if trace_output is None:
        trace_output = resolved_path.with_suffix(".trace.json")

    # `python -m` puts the launch cwd at sys.path[0]; `python target.py` uses the target's.
    original_sys_path = list(sys.path)
    sys.path[0] = target_dir
    # Snapshot before eviction so run() can undo it on a later call too.
    original_sys_modules = dict(sys.modules)
    # Evict local names so a target sibling module can shadow one we already imported.
    for name in _local_module_names(target_dir):
        sys.modules.pop(name, None)
    # run_path only replaces argv[0]; pass the original string and set the rest ourselves.
    original_argv = sys.argv
    sys.argv = [str(target_path), *program_args]
    tracer.start(target_dir)
    try:
        runpy.run_path(str(target_path), run_name="__main__")
    finally:
        # Write the trace even on a crash; restoration must still happen if the write fails.
        calls = tracer.stop()
        try:
            Path(trace_output).write_text(json.dumps(calls, indent=2))
        finally:
            if restore:
                sys.argv = original_argv
                sys.path[:] = original_sys_path
                sys.modules.clear()
                sys.modules.update(original_sys_modules)


def main():
    # Not argparse: REMAINDER swallows a leading "--", a plain slice doesn't.
    argv = sys.argv[1:]
    use_ai = False
    if argv and argv[0] == "--ai":
        use_ai = True
        argv = argv[1:]
    if not argv:
        print("usage: ai-tracer [--ai] <program> [program_args...]", file=sys.stderr)
        sys.exit(1)
    program, *program_args = argv
    resolved_path = Path(program).resolve()
    trace_output = resolved_path.with_suffix(".trace.json")
    try:
        run(program, program_args, restore=False, trace_output=trace_output)
    finally:
        # Runs even if the target crashed: run() still writes the trace
        # it collected before the crash, so tests can still be generated.
        generator.generate(
            str(trace_output),
            str(resolved_path.parent),
            str(resolved_path.parent / "generated_tests"),
            entry_script=str(resolved_path),
            ai=use_ai,
        )


if __name__ == "__main__":
    main()
