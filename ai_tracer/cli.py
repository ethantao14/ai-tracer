import runpy
import sys
from pathlib import Path


def _local_module_names(target_dir):
    names = set()
    for entry in Path(target_dir).iterdir():
        if entry.is_file() and entry.suffix == ".py":
            names.add(entry.stem)
        elif entry.is_dir() and (entry / "__init__.py").is_file():
            names.add(entry.name)
    return names


def run(target_path, program_args=(), restore=True):
    # restore=False for the CLI: atexit handlers run after this returns but
    # before the process exits, and need the target's state, not ours.
    resolved_path = Path(target_path).resolve()
    target_dir = str(resolved_path.parent)

    # `python -m` puts the launch cwd at sys.path[0]; `python target.py`
    # puts the target's own directory there. Overwrite, don't insert.
    original_sys_path = list(sys.path)
    sys.path[0] = target_dir
    # Snapshot before eviction so both it and the target's own imports can
    # be undone, run() may be called more than once per interpreter.
    original_sys_modules = dict(sys.modules)
    # Our own imports already populate sys.modules, unlike a fresh
    # `python target.py` process. Evict local names so they can shadow.
    for name in _local_module_names(target_dir):
        sys.modules.pop(name, None)
    # runpy.run_path only replaces argv[0] with the path it's given, so
    # pass the original (possibly relative) string, and set the rest
    # ourselves.
    original_argv = sys.argv
    sys.argv = [str(target_path), *program_args]
    try:
        runpy.run_path(str(target_path), run_name="__main__")
    finally:
        if restore:
            sys.argv = original_argv
            sys.path[:] = original_sys_path
            sys.modules.clear()
            sys.modules.update(original_sys_modules)


def main():
    # Not argparse: REMAINDER swallows a leading "--" right after the
    # program path, unlike real `python app.py -- --flag`. A plain slice
    # forwards everything after the program path verbatim.
    argv = sys.argv[1:]
    if not argv:
        print("usage: ai-tracer <program> [program_args...]", file=sys.stderr)
        sys.exit(1)
    program, *program_args = argv
    run(program, program_args, restore=False)


if __name__ == "__main__":
    main()
