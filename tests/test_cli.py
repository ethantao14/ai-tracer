import json
import subprocess
import sys

from ai_tracer import cli


def _only_qualname_and_args(record):
    return {"qualname": record["qualname"], "args": record["args"]}


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


def test_run_restores_state_even_if_writing_the_trace_fails(tmp_path):
    original_path = list(sys.path)
    original_argv = list(sys.argv)

    target = tmp_path / "program.py"
    target.write_text("x = 1\n")
    bad_trace_output = tmp_path / "missing_dir" / "trace.json"

    try:
        cli.run(str(target), trace_output=bad_trace_output)
    except OSError:
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
    assert [_only_qualname_and_args(c) for c in trace] == [
        {"qualname": "main", "args": {}},
        {"qualname": "helper", "args": {}},
    ]


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
    assert [_only_qualname_and_args(c) for c in trace] == [
        {"qualname": "doomed", "args": {}}
    ]


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
    assert [_only_qualname_and_args(c) for c in trace] == [
        {"qualname": "main", "args": {}}
    ]


def test_cli_trace_survives_the_target_changing_its_own_working_directory(tmp_path):
    # A relative program path keeps a relative co_filename for functions
    # defined in it. If the target then chdir()s, resolving that filename
    # against the *current* cwd (instead of the cwd run() started in) would
    # point at the wrong directory and silently drop these calls.
    target_dir = tmp_path / "target_dir"
    target_dir.mkdir()
    (target_dir / "elsewhere").mkdir()
    (target_dir / "program.py").write_text(
        "import os\n"
        "\n"
        "\n"
        "def after_chdir():\n"
        "    return 1\n"
        "\n"
        "\n"
        "def main():\n"
        "    os.chdir('elsewhere')\n"
        "    after_chdir()\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n"
    )

    result = subprocess.run(
        [sys.executable, "-m", "ai_tracer.cli", "program.py"],
        cwd=target_dir,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    trace = json.loads((target_dir / "program.trace.json").read_text())
    assert [_only_qualname_and_args(c) for c in trace] == [
        {"qualname": "main", "args": {}},
        {"qualname": "after_chdir", "args": {}},
    ]


def test_cli_trace_includes_the_arguments_a_function_was_called_with(tmp_path):
    (tmp_path / "program.py").write_text(
        "def add(a, b):\n"
        "    return a + b\n"
        "\n"
        "\n"
        "def main():\n"
        "    add(1, 2)\n"
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
    assert [_only_qualname_and_args(c) for c in trace] == [
        {"qualname": "main", "args": {}},
        {"qualname": "add", "args": {"a": 1, "b": 2}},
    ]


def test_cli_trace_stays_valid_json_for_a_nan_argument(tmp_path):
    (tmp_path / "program.py").write_text(
        "def take(value):\n"
        "    return value\n"
        "\n"
        "\n"
        "def main():\n"
        "    take(float('nan'))\n"
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
    raw = (tmp_path / "program.trace.json").read_text()
    assert "NaN" not in raw
    trace = json.loads(raw)
    assert [_only_qualname_and_args(c) for c in trace] == [
        {"qualname": "main", "args": {}},
        {"qualname": "take", "args": {"value": "nan"}},
    ]


def test_cli_writes_a_trace_for_an_int_too_large_to_stringify(tmp_path):
    (tmp_path / "program.py").write_text(
        "def take(value):\n"
        "    return value\n"
        "\n"
        "\n"
        "def main():\n"
        "    take(10**5000)\n"
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
    assert _only_qualname_and_args(trace[0]) == {"qualname": "main", "args": {}}
    assert trace[1]["qualname"] == "take"
    assert isinstance(trace[1]["args"]["value"], str)


def test_cli_trace_records_main_for_the_entry_script(tmp_path):
    # Matches direct execution: `python program.py` gives the entry
    # script's own functions __module__ == "__main__", not its filename.
    (tmp_path / "program.py").write_text(
        "def main():\n    return 1\n\n\nif __name__ == '__main__':\n    main()\n"
    )

    result = subprocess.run(
        [sys.executable, "-m", "ai_tracer.cli", str(tmp_path / "program.py")],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    trace = json.loads((tmp_path / "program.trace.json").read_text())
    assert trace == [
        {
            "call_id": 0,
            "parent_call_id": None,
            "module": "__main__",
            "qualname": "main",
            "args": {},
            "arg_serialization": {},
            "raised": False,
            "return_value": 1,
            "return_serialization": "json",
            "exception_module": None,
            "exception_type": None,
        }
    ]


def test_cli_trace_records_the_module_a_function_was_defined_in(tmp_path):
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
    trace = json.loads((target_dir / "main.trace.json").read_text())
    assert trace[0]["module"] == "__main__"
    assert trace[0]["qualname"] == "main"
    assert trace[1]["module"] == "helper"
    assert trace[1]["qualname"] == "double"


def test_cli_trace_resolves_a_package_init_module_correctly(tmp_path):
    # A function defined directly in pkg/__init__.py has __module__ == "pkg",
    # not "pkg.__init__" - matching how Python's own import machinery names
    # it, not a naive filename-to-dotted-path conversion.
    target_dir = tmp_path / "target_program"
    target_dir.mkdir()
    pkg_dir = target_dir / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("def from_init():\n    return 1\n")
    (pkg_dir / "sub.py").write_text("def from_sub():\n    return 1\n")
    (target_dir / "main.py").write_text(
        "from pkg import from_init\n"
        "from pkg.sub import from_sub\n"
        "\n"
        "\n"
        "def main():\n"
        "    from_init()\n"
        "    from_sub()\n"
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
    trace = json.loads((target_dir / "main.trace.json").read_text())
    modules_by_qualname = {c["qualname"]: c["module"] for c in trace}
    assert modules_by_qualname["from_init"] == "pkg"
    assert modules_by_qualname["from_sub"] == "pkg.sub"


def test_cli_trace_includes_the_call_tree(tmp_path):
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
    assert trace[0]["qualname"] == "main"
    assert trace[0]["parent_call_id"] is None
    assert trace[1]["qualname"] == "helper"
    assert trace[1]["parent_call_id"] == trace[0]["call_id"]
    assert trace[0]["call_id"] != trace[1]["call_id"]


def test_cli_trace_includes_return_values_and_raised_status(tmp_path):
    (tmp_path / "program.py").write_text(
        "def doubles(x):\n"
        "    return x * 2\n"
        "\n"
        "\n"
        "def fails():\n"
        "    raise ValueError('boom')\n"
        "\n"
        "\n"
        "def main():\n"
        "    doubles(21)\n"
        "    try:\n"
        "        fails()\n"
        "    except ValueError:\n"
        "        pass\n"
        "    return 'done'\n"
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
    by_qualname = {c["qualname"]: c for c in trace}
    assert by_qualname["doubles"]["raised"] is False
    assert by_qualname["doubles"]["return_value"] == 42
    assert by_qualname["doubles"]["exception_type"] is None
    assert by_qualname["fails"]["raised"] is True
    assert by_qualname["fails"]["return_value"] is None
    assert by_qualname["fails"]["exception_type"] == "ValueError"
    assert by_qualname["fails"]["exception_module"] == "builtins"
    # An explicit non-None return after catching is unambiguous, unlike an
    # implicit `return None` after a catch (covered separately in
    # test_tracer.py's ambiguity tests).
    assert by_qualname["main"]["raised"] is False
    assert by_qualname["main"]["return_value"] == "done"
