import argparse
import runpy
import sys
from pathlib import Path


def run(target_path):
    target_path = Path(target_path).resolve()

    # runpy.run_path does not add the script's own directory to sys.path,
    # unlike running it directly with `python script.py`, so imports like
    # `from helper import x` inside the target script would otherwise fail.
    target_dir = str(target_path.parent)
    sys.path.insert(0, target_dir)
    # runpy.run_path only ever replaces argv[0] (see its _ModifiedArgv0),
    # the rest of this CLI's own argv would otherwise leak into the target
    # program's sys.argv, breaking any target that parses its own
    # command-line arguments (argparse, etc.).
    original_argv = sys.argv
    sys.argv = [str(target_path)]
    try:
        runpy.run_path(str(target_path), run_name="__main__")
    finally:
        sys.argv = original_argv
        if target_dir in sys.path:
            sys.path.remove(target_dir)


def main():
    parser = argparse.ArgumentParser(prog="ai-tracer")
    parser.add_argument("program", help="Run a target Python program")
    args = parser.parse_args()
    run(args.program)


if __name__ == "__main__":
    main()
