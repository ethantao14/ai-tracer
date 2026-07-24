import json
import runpy
import sys
from pathlib import Path

from ai_tracer import tracer


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
    if not argv:
        print("usage: ai-tracer <program> [program_args...]", file=sys.stderr)
        sys.exit(1)
    program, *program_args = argv
    run(program, program_args, restore=False)


if __name__ == "__main__":
    main()
