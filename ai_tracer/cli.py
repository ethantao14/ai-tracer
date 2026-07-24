import runpy
import sys
from pathlib import Path


def _local_module_names(target_dir):
    # Enumerated so run() can evict these specific names from sys.modules
    # before the target runs, see the comment there for why.
    names = set()
    for entry in Path(target_dir).iterdir():
        if entry.is_file() and entry.suffix == ".py":
            names.add(entry.stem)
        elif entry.is_dir() and (entry / "__init__.py").is_file():
            names.add(entry.name)
    return names


def run(target_path, program_args=()):
    # target_path itself (not resolved_path below) is what gets passed to
    # runpy.run_path and into sys.argv, preserving whatever the caller
    # actually typed (relative or absolute), the same as `python app.py`
    # leaves sys.argv[0] as "app.py", not its resolved absolute path.
    # Setting sys.argv[0] ourselves isn't enough on its own: runpy.run_path
    # has its own _ModifiedArgv0 context manager that overwrites
    # sys.argv[0] with whatever path string it was actually given,
    # regardless of what sys.argv already held, so that has to be the
    # original string too, not the resolved one.
    resolved_path = Path(target_path).resolve()
    target_dir = str(resolved_path.parent)

    # `python -m ai_tracer.cli` puts the invoking shell's cwd at sys.path[0],
    # unlike `python target.py` directly, which puts the target's own
    # directory there instead, never the caller's cwd. Overwriting index 0
    # (not just inserting ahead of it) removes that mismatch, a target could
    # otherwise accidentally import some unrelated same-named module that
    # only happens to sit wherever ai-tracer itself was launched from, which
    # a real `python target.py` run would never do. This also covers the
    # `from helper import x` sibling-import case runpy.run_path doesn't
    # handle on its own.
    original_sys_path = list(sys.path)
    sys.path[0] = target_dir
    # ai_tracer's own imports (this module's own "from pathlib import
    # Path", etc.) already populate sys.modules before the target ever
    # runs. A real `python target.py` process starts with none of that
    # cached, so if the target directory defines its own file sharing a
    # name with something already imported here, e.g. a local pathlib.py
    # meant to shadow the stdlib module, direct execution would resolve
    # the local file (nothing cached yet, target_dir is first on
    # sys.path), but sys.modules is checked before sys.path is ever
    # consulted, so this harness would otherwise silently hand back
    # whatever was already imported instead. Evicting anything the target
    # directory actually defines forces a fresh, correctly-shadowed import.
    for name in _local_module_names(target_dir):
        sys.modules.pop(name, None)
    # runpy.run_path only ever replaces argv[0] (see its _ModifiedArgv0),
    # everything else needs to be set up explicitly: this CLI's own argv
    # would otherwise leak into the target's sys.argv, and any arguments
    # meant for the target (e.g. `run.sh app.py --config cfg.yml`) need to
    # actually reach it instead of being silently dropped.
    original_argv = sys.argv
    sys.argv = [str(target_path), *program_args]
    try:
        runpy.run_path(str(target_path), run_name="__main__")
    finally:
        sys.argv = original_argv
        # run() executes the target in-process, a target that mutates
        # sys.path itself (clears it, reassigns it) needs the *whole*
        # thing restored, not just index 0, restoring only index 0 could
        # leave the rest polluted with whatever the target left behind, or
        # raise IndexError here if the target emptied it.
        sys.path[:] = original_sys_path


def main():
    # Not using argparse here: everything after the program path is opaque
    # pass-through to the target, and argparse.REMAINDER silently swallows
    # a leading "--" right after the last positional (its own convention for
    # "stop parsing options here"), so `run.sh app.py -- --flag` would
    # forward ['--flag'] instead of ['--', '--flag'], different from what
    # `python app.py -- --flag` actually does. A plain slice has no such
    # reinterpretation, whatever follows the program path reaches the
    # target verbatim.
    argv = sys.argv[1:]
    if not argv:
        print("usage: ai-tracer <program> [program_args...]", file=sys.stderr)
        sys.exit(1)
    program, *program_args = argv
    run(program, program_args)


if __name__ == "__main__":
    main()
