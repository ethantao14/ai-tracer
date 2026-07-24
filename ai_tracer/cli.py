import argparse
import runpy
import sys
from pathlib import Path


def run(target_path, program_args=()):
    target_path = Path(target_path).resolve()
    target_dir = str(target_path.parent)

    # `python -m ai_tracer.cli` puts the invoking shell's cwd at sys.path[0],
    # unlike `python target.py` directly, which puts the target's own
    # directory there instead, never the caller's cwd. Overwriting index 0
    # (not just inserting ahead of it) removes that mismatch, a target could
    # otherwise accidentally import some unrelated same-named module that
    # only happens to sit wherever ai-tracer itself was launched from, which
    # a real `python target.py` run would never do. This also covers the
    # `from helper import x` sibling-import case runpy.run_path doesn't
    # handle on its own.
    original_sys_path_0 = sys.path[0]
    sys.path[0] = target_dir
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
        sys.path[0] = original_sys_path_0


def main():
    parser = argparse.ArgumentParser(prog="ai-tracer")
    parser.add_argument("program", help="Run a target Python program")
    parser.add_argument(
        "program_args",
        nargs=argparse.REMAINDER,
        help="Arguments to pass through to the target program",
    )
    args = parser.parse_args()
    run(args.program, args.program_args)


if __name__ == "__main__":
    main()
