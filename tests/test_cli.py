import json
import subprocess
import sys

from ai_tracer import cli


def test_cli_prints_usage_and_exits_nonzero_with_no_arguments():
    result = subprocess.run(
        [sys.executable, "-m", "ai_tracer.cli"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "usage" in result.stderr.lower()


def test_atexit_handlers_in_the_target_see_the_targets_own_argv(tmp_path):
    # atexit handlers run after run() would otherwise already have
    # restored ai-tracer's own state, unlike direct execution, where the
    # process just exits with the target's state intact throughout.
    marker = tmp_path / "atexit_marker.txt"
    (tmp_path / "program.py").write_text(
        "import atexit\n"
        "import sys\n"
        "\n"
        "\n"
        "def on_exit():\n"
        f"    open({str(marker)!r}, 'w').write(repr(sys.argv))\n"
        "\n"
        "\n"
        "atexit.register(on_exit)\n"
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_tracer.cli",
            str(tmp_path / "program.py"),
            "--flag",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert marker.read_text() == repr([str(tmp_path / "program.py"), "--flag"])


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


def test_cli_lets_a_target_sibling_shadow_an_already_imported_module_name(tmp_path):
    # ai_tracer.cli already imports pathlib before the target runs, unlike
    # a fresh `python target.py` process. A local pathlib.py should still
    # win, matching direct execution.
    target_dir = tmp_path / "target_program"
    target_dir.mkdir()
    (target_dir / "pathlib.py").write_text("MARKER = 'local, not stdlib'\n")
    (target_dir / "main.py").write_text(
        "import pathlib\n"
        "\n"
        "\n"
        "def main():\n"
        "    assert pathlib.MARKER == 'local, not stdlib'\n"
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


def test_cli_preserves_the_original_program_string_in_argv_0(tmp_path):
    # `python app.py` leaves sys.argv[0] as "app.py", not its resolved path.
    (tmp_path / "argvy.py").write_text(
        "import sys\n"
        "\n"
        "\n"
        "def main():\n"
        "    assert sys.argv[0] == 'argvy.py', sys.argv\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n"
    )

    result = subprocess.run(
        [sys.executable, "-m", "ai_tracer.cli", "argvy.py"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_run_restores_the_whole_sys_path_even_if_the_target_mutates_it(tmp_path):
    original_path = list(sys.path)

    target = tmp_path / "path_mutator.py"
    target.write_text("import sys\nsys.path.clear()\nsys.path.append('/nonsense')\n")

    cli.run(str(target))

    assert sys.path == original_path


def test_run_does_not_leak_imported_modules_into_a_later_run(tmp_path):
    # run() can be called more than once in the same interpreter. A second,
    # unrelated target shouldn't be able to pick up a module the first
    # target imported, matching separate `python target.py` processes.
    first_dir = tmp_path / "first"
    first_dir.mkdir()
    (first_dir / "helper.py").write_text("VALUE = 'from first run'\n")
    (first_dir / "main.py").write_text("import helper\n")

    cli.run(str(first_dir / "main.py"))

    second_dir = tmp_path / "second"
    second_dir.mkdir()
    (second_dir / "main.py").write_text(
        "try:\n"
        "    import helper\n"
        "    raise AssertionError('should not have found a helper module')\n"
        "except ImportError:\n"
        "    pass\n"
    )

    cli.run(str(second_dir / "main.py"))
    assert "helper" not in sys.modules


def test_run_restores_a_module_it_evicted_for_shadowing(tmp_path):
    # run() evicts sys.modules entries that collide with the target
    # directory (see the pathlib-shadowing test above), the original
    # module needs to come back afterward, not just have its target-local
    # replacement removed, leaving the caller with it missing entirely.
    import pathlib

    original_pathlib = sys.modules["pathlib"]

    target_dir = tmp_path / "target_program"
    target_dir.mkdir()
    (target_dir / "pathlib.py").write_text("MARKER = 'local'\n")
    (target_dir / "main.py").write_text("import pathlib\n")

    cli.run(str(target_dir / "main.py"))

    assert sys.modules["pathlib"] is original_pathlib
    assert pathlib.Path is not None


def test_cli_forwards_extra_arguments_to_the_target_program(tmp_path):
    # `run.sh app.py --config cfg.yml` should behave like
    # `python app.py --config cfg.yml`.
    (tmp_path / "argvy.py").write_text(
        "import sys\n"
        "\n"
        "\n"
        "def main():\n"
        "    assert sys.argv[1:] == ['--config', 'cfg.yml'], sys.argv\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n"
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_tracer.cli",
            str(tmp_path / "argvy.py"),
            "--config",
            "cfg.yml",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_cli_forwards_a_literal_double_dash_to_the_target_program(tmp_path):
    # Matches `python app.py -- --flag`, a literal "--" reaches the target.
    (tmp_path / "argvy.py").write_text(
        "import sys\n"
        "\n"
        "\n"
        "def main():\n"
        "    assert sys.argv[1:] == ['--', '--flag'], sys.argv\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n"
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_tracer.cli",
            str(tmp_path / "argvy.py"),
            "--",
            "--flag",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_cli_does_not_leak_the_launch_directory_into_target_imports(tmp_path):
    # A module beside the launch directory, not beside the target, should
    # fail to import, matching direct execution.
    launch_dir = tmp_path / "launch_dir"
    launch_dir.mkdir()
    (launch_dir / "decoy.py").write_text(
        "raise AssertionError('should never import')\n"
    )

    target_dir = tmp_path / "target_dir"
    target_dir.mkdir()
    (target_dir / "program.py").write_text("import decoy\n")

    result = subprocess.run(
        [sys.executable, "-m", "ai_tracer.cli", str(target_dir / "program.py")],
        cwd=launch_dir,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "ModuleNotFoundError" in result.stderr
    assert "decoy" in result.stderr


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


def test_cli_writes_a_trace_of_the_functions_the_target_calls(tmp_path):
    (tmp_path / "program.py").write_text(
        "def helper():\n"
        "    return 1\n"
        "\n"
        "\n"
        "def main():\n"
        "    helper()\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n"
    )

    result = subprocess.run(
        [sys.executable, "-m", "ai_tracer.cli", str(tmp_path / "program.py")],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    trace = json.loads((tmp_path / "program.trace.json").read_text())
    assert trace == [{"qualname": "main"}, {"qualname": "helper"}]


def test_cli_writes_a_trace_even_if_the_target_crashes(tmp_path):
    (tmp_path / "crashy.py").write_text(
        "def doomed():\n"
        '    raise RuntimeError("boom")\n'
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    doomed()\n"
    )

    result = subprocess.run(
        [sys.executable, "-m", "ai_tracer.cli", str(tmp_path / "crashy.py")],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    trace = json.loads((tmp_path / "crashy.trace.json").read_text())
    assert trace == [{"qualname": "doomed"}]


def test_cli_trace_does_not_include_stdlib_calls(tmp_path):
    (tmp_path / "program.py").write_text(
        "import json\n"
        "\n"
        "\n"
        "def main():\n"
        "    json.dumps({})\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n"
    )

    result = subprocess.run(
        [sys.executable, "-m", "ai_tracer.cli", str(tmp_path / "program.py")],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    trace = json.loads((tmp_path / "program.trace.json").read_text())
    assert trace == [{"qualname": "main"}]
