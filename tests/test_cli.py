import subprocess
import sys

from ai_tracer import cli


def test_cli_runs_a_target_program(tmp_path):
    marker = tmp_path / "ran.txt"
    (tmp_path / "program.py").write_text(
        f"Path = __import__('pathlib').Path\nPath({str(marker)!r}).write_text('yes')\n"
    )

    result = subprocess.run(
        [sys.executable, "-m", "ai_tracer.cli", str(tmp_path / "program.py")],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert marker.read_text() == "yes"


def test_cli_can_import_sibling_modules_in_the_target_directory(tmp_path):
    target_dir = tmp_path / "target_program"
    target_dir.mkdir()
    (target_dir / "helper.py").write_text("def double(x):\n    return x * 2\n")
    (target_dir / "main.py").write_text(
        "from helper import double\n"
        "\n"
        "\n"
        "def main():\n"
        "    double(21)\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n"
    )

    result = subprocess.run(
        [sys.executable, "-m", "ai_tracer.cli", str(target_dir / "main.py")],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_cli_does_not_leak_its_own_argv_into_the_target(tmp_path):
    # runpy.run_path only ever replaces argv[0] (see runpy._ModifiedArgv0),
    # this CLI's own arguments would otherwise leak into the target's
    # sys.argv, breaking any target that parses its own command-line
    # arguments.
    (tmp_path / "argvy.py").write_text(
        "import sys\n"
        "\n"
        "\n"
        "def main():\n"
        "    assert len(sys.argv) == 1, sys.argv\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n"
    )

    result = subprocess.run(
        [sys.executable, "-m", "ai_tracer.cli", str(tmp_path / "argvy.py")],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_cli_lets_the_target_program_crash_normally(tmp_path):
    (tmp_path / "crashy.py").write_text(
        "def main():\n"
        '    raise RuntimeError("boom")\n'
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n"
    )

    result = subprocess.run(
        [sys.executable, "-m", "ai_tracer.cli", str(tmp_path / "crashy.py")],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "RuntimeError: boom" in result.stderr


def test_run_restores_sys_path_and_argv_even_if_the_target_crashes(tmp_path):
    original_path = list(sys.path)
    original_argv = list(sys.argv)

    target = tmp_path / "crashy.py"
    target.write_text("raise RuntimeError('boom')\n")

    try:
        cli.run(str(target))
    except RuntimeError:
        pass

    assert sys.path == original_path
    assert sys.argv == original_argv
