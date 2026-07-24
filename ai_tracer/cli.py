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


def run(target_path, program_args=()):
    resolved_path = Path(target_path).resolve()
    target_dir = str(resolved_path.parent)

    # `python -m` puts the launch cwd at sys.path[0]; `python target.py`
    # puts the target's own directory there instead. Overwrite index 0
    # rather than insert, so sibling imports work and the launch directory
    # doesn't leak in as an accidental import source.
    original_sys_path = list(sys.path)
    sys.path[0] = target_dir
    # Our own imports (e.g. pathlib above) already populate sys.modules,
    # unlike a fresh `python target.py` process. Evict anything the target
    # directory defines itself so a same-named local file can shadow it.
    for name in _local_module_names(target_dir):
        sys.modules.pop(name, None)
    original_sys_modules = set(sys.modules)
    # runpy.run_path only replaces argv[0], everything else needs to be set
    # explicitly: our own argv shouldn't leak into the target, and target
    # args need to actually reach it. runpy.run_path also resets argv[0]
    # itself to whatever path it's given, so that has to be the original
    # (possibly relative) string too, matching what `python app.py` leaves
    # in sys.argv[0].
    original_argv = sys.argv
    sys.argv = [str(target_path), *program_args]
    try:
        runpy.run_path(str(target_path), run_name="__main__")
    finally:
        sys.argv = original_argv
        # Restore the whole list, not just index 0, in case the target
        # itself mutated sys.path (it runs in-process).
        sys.path[:] = original_sys_path
        # run() can be called more than once in the same interpreter (our
        # own tests do this), whatever the target imported would otherwise
        # stay cached and could leak into a later, unrelated run().
        for name in set(sys.modules) - original_sys_modules:
            del sys.modules[name]


def main():
    # Not argparse: REMAINDER swallows a leading "--" right after the
    # program path, unlike real `python app.py -- --flag`. A plain slice
    # forwards everything after the program path verbatim.
    argv = sys.argv[1:]
    if not argv:
        print("usage: ai-tracer <program> [program_args...]", file=sys.stderr)
        sys.exit(1)
    program, *program_args = argv
    run(program, program_args)


if __name__ == "__main__":
    main()
