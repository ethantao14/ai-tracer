import json
import subprocess
import sys

from ai_tracer import generator


def _trace(tmp_path, program_source, filename="program.py"):
    (tmp_path / filename).write_text(program_source)
    result = subprocess.run(
        [sys.executable, "-m", "ai_tracer.cli", str(tmp_path / filename)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return tmp_path / filename.replace(".py", ".trace.json")


def test_generates_a_test_that_actually_passes_for_a_simple_function(tmp_path):
    (tmp_path / "helper.py").write_text("def double(x):\n    return x * 2\n")
    trace_path = _trace(
        tmp_path,
        "from helper import double\n"
        "\n"
        "\n"
        "def main():\n"
        "    double(21)\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    output_dir = tmp_path / "generated_tests"
    written = generator.generate(str(trace_path), str(tmp_path), str(output_dir))

    assert written == [output_dir / "test_helper.py"]
    source = (output_dir / "test_helper.py").read_text()
    assert f"sys.path.insert(0, {str(tmp_path.resolve())!r})" in source
    assert "from helper import double" in source
    assert "def test_double_0():" in source
    assert "result = double(x=21)" in source
    assert "assert result == 42" in source

    # The whole point: the generated test file must actually run and pass.
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(output_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_skips_the_entry_scripts_own_functions(tmp_path, capsys):
    trace_path = _trace(
        tmp_path,
        'def main():\n    return 1\n\n\nif __name__ == "__main__":\n    main()\n',
    )

    written = generator.generate(
        str(trace_path), str(tmp_path), str(tmp_path / "generated_tests")
    )

    assert written == []
    assert '"__main__"' in capsys.readouterr().err
    assert not (tmp_path / "generated_tests" / "test___main__.py").exists()


def test_skips_a_call_with_a_non_json_serializable_arg(tmp_path, capsys):
    (tmp_path / "helper.py").write_text(
        "class Widget:\n    pass\n\n\ndef take(value):\n    return value\n"
    )
    trace_path = _trace(
        tmp_path,
        "from helper import Widget, take\n"
        "\n"
        "\n"
        "def main():\n"
        "    take(Widget())\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    written = generator.generate(
        str(trace_path), str(tmp_path), str(tmp_path / "generated_tests")
    )

    assert written == []
    assert "could not be captured as a JSON value" in capsys.readouterr().err


def test_skips_a_call_that_raised(tmp_path, capsys):
    (tmp_path / "helper.py").write_text("def fails():\n    raise ValueError('boom')\n")
    trace_path = _trace(
        tmp_path,
        "from helper import fails\n"
        "\n"
        "\n"
        "def main():\n"
        "    try:\n"
        "        fails()\n"
        "    except ValueError:\n"
        "        pass\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    written = generator.generate(
        str(trace_path), str(tmp_path), str(tmp_path / "generated_tests")
    )

    assert written == []
    assert "raised an exception" in capsys.readouterr().err


def test_skips_a_method_call(tmp_path, capsys):
    (tmp_path / "helper.py").write_text(
        "class Greeter:\n    def greet(self):\n        return 'hi'\n"
    )
    trace_path = _trace(
        tmp_path,
        "from helper import Greeter\n"
        "\n"
        "\n"
        "def main():\n"
        "    Greeter().greet()\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    generator.generate(
        str(trace_path), str(tmp_path), str(tmp_path / "generated_tests")
    )

    assert "not a plain top-level function" in capsys.readouterr().err
    assert not (tmp_path / "generated_tests" / "test_helper.py").exists()


def test_skips_a_function_with_a_positional_only_parameter(tmp_path, capsys):
    (tmp_path / "helper.py").write_text("def posonly(x, /):\n    return x + 1\n")
    trace_path = _trace(
        tmp_path,
        "from helper import posonly\n"
        "\n"
        "\n"
        "def main():\n"
        "    posonly(1)\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    written = generator.generate(
        str(trace_path), str(tmp_path), str(tmp_path / "generated_tests")
    )

    assert written == []
    assert "positional-only" in capsys.readouterr().err


def test_skips_a_call_whose_module_name_is_not_a_valid_import_target(tmp_path, capsys):
    (tmp_path / "helper.py").write_text(
        "__name__ = 'class'\n\n\ndef broken():\n    return 1\n"
    )
    trace_path = _trace(
        tmp_path,
        "from helper import broken\n"
        "\n"
        "\n"
        "def main():\n"
        "    broken()\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    written = generator.generate(
        str(trace_path), str(tmp_path), str(tmp_path / "generated_tests")
    )

    assert written == []
    assert "not a valid import target" in capsys.readouterr().err


def test_skips_a_call_whose_module_fails_to_import(tmp_path, capsys):
    (tmp_path / "helper.py").write_text("def works():\n    return 1\n")
    trace_path = _trace(
        tmp_path,
        "from helper import works\n"
        "\n"
        "\n"
        "def main():\n"
        "    works()\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )
    # Break the module for the *generation* step, after tracing already
    # succeeded - mirrors a module that raises on import (which the
    # tracer's crash-tolerant design still records up to the break point).
    (tmp_path / "helper.py").write_text("raise RuntimeError('cannot import')\n")

    written = generator.generate(
        str(trace_path), str(tmp_path), str(tmp_path / "generated_tests")
    )

    assert written == []
    assert "could not be imported" in capsys.readouterr().err


def test_generates_a_passing_test_for_a_function_that_returns_none(tmp_path):
    # A function with no explicit return records raised=false,
    # return_value=null, return_serialization=null. That's a genuine None
    # return (a safe literal), not an uncapturable value - it must still
    # generate a test that passes.
    (tmp_path / "helper.py").write_text("def note(x):\n    pass\n")
    trace_path = _trace(
        tmp_path,
        "from helper import note\n"
        "\n"
        "\n"
        "def main():\n"
        "    note(1)\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    output_dir = tmp_path / "generated_tests"
    written = generator.generate(str(trace_path), str(tmp_path), str(output_dir))

    assert written == [output_dir / "test_helper.py"]
    assert "assert result == None" in (output_dir / "test_helper.py").read_text()

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(output_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_skips_a_call_whose_args_no_longer_bind_to_the_current_signature(
    tmp_path, capsys
):
    # Traced as double(x=...), but the parameter was renamed to y since -
    # emitting double(x=...) would produce a test that fails the instant it
    # calls the function, so skip it instead.
    (tmp_path / "helper.py").write_text("def double(x):\n    return x * 2\n")
    trace_path = _trace(
        tmp_path,
        "from helper import double\n"
        "\n"
        "\n"
        "def main():\n"
        "    double(21)\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )
    # A clearly different length from the traced version, so Python can't
    # reuse a same-second, same-size stale .pyc instead of the new source.
    (tmp_path / "helper.py").write_text(
        "def double(renamed_parameter):\n    return renamed_parameter * 2\n"
    )

    written = generator.generate(
        str(trace_path), str(tmp_path), str(tmp_path / "generated_tests")
    )

    assert written == []
    assert "no longer match the function's signature" in capsys.readouterr().err


def test_a_target_sibling_shadowing_a_cached_module_wins(tmp_path):
    # helper imports a sibling named "config". A module of the same name is
    # already cached in sys.modules (simulating a stdlib/dependency name, or
    # a leftover from a previous generate() call). The target's own sibling
    # must win, or importing helper for signature inspection sees the wrong
    # config and can wrongly skip a call that traced fine.
    (tmp_path / "config.py").write_text("VALUE = 'target'\n")
    (tmp_path / "helper.py").write_text(
        "from config import VALUE\n\n\ndef describe(x):\n    return VALUE\n"
    )
    trace_path = _trace(
        tmp_path,
        "from helper import describe\n"
        "\n"
        "\n"
        "def main():\n"
        "    describe(1)\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    import types

    stale = types.ModuleType("config")
    stale.VALUE = "stale"
    sys.modules["config"] = stale
    output_dir = tmp_path / "generated_tests"
    try:
        written = generator.generate(str(trace_path), str(tmp_path), str(output_dir))
        # Capture what generate() left behind before this test cleans it up.
        restored = sys.modules.get("config")
    finally:
        sys.modules.pop("config", None)

    # The call generated (rather than being skipped, which is what a stale
    # config would have caused), and the pre-existing module was restored
    # afterward rather than left evicted.
    assert written == [output_dir / "test_helper.py"]
    assert restored is stale


def test_skips_a_call_that_returns_a_tuple(tmp_path, capsys):
    # `return a, b` produces a tuple, which the tracer flattens to a JSON
    # array. Generating `assert result == [a, b]` would fail (a tuple isn't
    # equal to a list), so skip it until the trace can tell them apart.
    (tmp_path / "helper.py").write_text("def pair():\n    return 1, 2\n")
    trace_path = _trace(
        tmp_path,
        "from helper import pair\n"
        "\n"
        "\n"
        "def main():\n"
        "    pair()\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    written = generator.generate(
        str(trace_path), str(tmp_path), str(tmp_path / "generated_tests")
    )

    assert written == []
    assert "return value contains a list/array" in capsys.readouterr().err


def test_skips_a_call_with_a_list_argument(tmp_path, capsys):
    (tmp_path / "helper.py").write_text("def first(items):\n    return items[0]\n")
    trace_path = _trace(
        tmp_path,
        "from helper import first\n"
        "\n"
        "\n"
        "def main():\n"
        "    first([10, 20])\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    written = generator.generate(
        str(trace_path), str(tmp_path), str(tmp_path / "generated_tests")
    )

    assert written == []
    assert "an argument contains a list/array" in capsys.readouterr().err


def test_skips_a_call_with_a_list_nested_inside_a_dict_argument(tmp_path, capsys):
    (tmp_path / "helper.py").write_text("def lookup(data):\n    return data['k'][0]\n")
    trace_path = _trace(
        tmp_path,
        "from helper import lookup\n"
        "\n"
        "\n"
        "def main():\n"
        "    lookup({'k': [1, 2]})\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    written = generator.generate(
        str(trace_path), str(tmp_path), str(tmp_path / "generated_tests")
    )

    assert written == []
    assert "an argument contains a list/array" in capsys.readouterr().err


def test_generates_a_passing_test_for_a_dict_return_value(tmp_path):
    # A dict (unlike a tuple) round-trips through JSON unchanged, so it's
    # still generatable as long as it holds no arrays.
    (tmp_path / "helper.py").write_text("def wrap(x):\n    return {'value': x}\n")
    trace_path = _trace(
        tmp_path,
        "from helper import wrap\n"
        "\n"
        "\n"
        "def main():\n"
        "    wrap(5)\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    output_dir = tmp_path / "generated_tests"
    written = generator.generate(str(trace_path), str(tmp_path), str(output_dir))

    assert written == [output_dir / "test_helper.py"]
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(output_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_skips_a_generator_function(tmp_path, capsys):
    (tmp_path / "helper.py").write_text(
        "def counter(n):\n    yield n\n    yield n + 1\n"
    )
    trace_path = _trace(
        tmp_path,
        "from helper import counter\n"
        "\n"
        "\n"
        "def main():\n"
        "    list(counter(1))\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    written = generator.generate(
        str(trace_path), str(tmp_path), str(tmp_path / "generated_tests")
    )

    assert written == []
    assert "plain synchronous call" in capsys.readouterr().err


def test_skips_an_async_function(tmp_path, capsys):
    (tmp_path / "helper.py").write_text(
        "import asyncio\n\n\nasync def fetch(x):\n    return x * 2\n"
    )
    trace_path = _trace(
        tmp_path,
        "import asyncio\n"
        "\n"
        "from helper import fetch\n"
        "\n"
        "\n"
        "def main():\n"
        "    asyncio.run(fetch(21))\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    generator.generate(
        str(trace_path), str(tmp_path), str(tmp_path / "generated_tests")
    )

    assert not (tmp_path / "generated_tests" / "test_helper.py").exists()


def test_colliding_module_filenames_do_not_overwrite_each_other(tmp_path):
    # "pkg.sub" and "pkg_sub" both flatten to test_pkg_sub.py; the second
    # must not clobber the first.
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "sub.py").write_text("def a():\n    return 1\n")
    (tmp_path / "pkg_sub.py").write_text("def b():\n    return 2\n")
    trace_path = _trace(
        tmp_path,
        "from pkg.sub import a\n"
        "from pkg_sub import b\n"
        "\n"
        "\n"
        "def main():\n"
        "    a()\n"
        "    b()\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    output_dir = tmp_path / "generated_tests"
    written = generator.generate(str(trace_path), str(tmp_path), str(output_dir))

    # Two distinct files, no overwrite, and both are real files on disk.
    assert len(written) == 2
    assert len({p.name for p in written}) == 2
    assert all(p.exists() for p in written)


def test_two_submodules_of_one_package_do_not_re_run_its_init(tmp_path):
    # Importing pkg.a then pkg.b must run pkg/__init__.py exactly once, like a
    # normal run - not once per submodule. A package whose __init__ appends to
    # a list would grow it on every re-run, so both submodules generating
    # cleanly is the observable proof __init__ wasn't re-executed underneath.
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        "import os\n"
        "_MARKER = os.path.join(os.path.dirname(__file__), 'init_runs.txt')\n"
        "with open(_MARKER, 'a') as fh:\n"
        "    fh.write('x')\n"
    )
    (pkg / "a.py").write_text("def fa():\n    return 1\n")
    (pkg / "b.py").write_text("def fb():\n    return 2\n")
    trace_path = _trace(
        tmp_path,
        "from pkg.a import fa\n"
        "from pkg.b import fb\n"
        "\n"
        "\n"
        "def main():\n"
        "    fa()\n"
        "    fb()\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )
    # Clear the marker the traced run wrote, so we only measure generate()'s
    # own imports.
    (pkg / "init_runs.txt").write_text("")

    output_dir = tmp_path / "generated_tests"
    written = generator.generate(str(trace_path), str(tmp_path), str(output_dir))

    assert {p.name for p in written} == {"test_pkg_a.py", "test_pkg_b.py"}
    # __init__ ran exactly once for the whole package, not once per submodule.
    assert (pkg / "init_runs.txt").read_text() == "x"


def test_stale_generated_tests_are_cleared_on_rerun(tmp_path):
    (tmp_path / "helper.py").write_text("def f():\n    return 1\n")
    first_trace = _trace(
        tmp_path,
        "from helper import f\n"
        "\n"
        "\n"
        "def main():\n"
        "    f()\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )
    output_dir = tmp_path / "generated_tests"
    generator.generate(str(first_trace), str(tmp_path), str(output_dir))
    assert (output_dir / "test_helper.py").exists()

    # A second trace with no generatable calls (only the entry script) must
    # leave the output dir empty, not keep the first run's stale file.
    second_trace = _trace(
        tmp_path,
        "def main():\n    return 1\n\n\nif __name__ == '__main__':\n    main()\n",
        filename="other.py",
    )
    written = generator.generate(str(second_trace), str(tmp_path), str(output_dir))

    assert written == []
    assert not (output_dir / "test_helper.py").exists()


def test_rerun_does_not_delete_user_test_files_in_the_output_dir(tmp_path):
    # The output dir can be an existing test folder the user also keeps their
    # own tests in. Regeneration must only remove files it generated (marked),
    # never a hand-written test_*.py or a target module named test_*.py.
    (tmp_path / "helper.py").write_text("def f():\n    return 1\n")
    trace_path = _trace(
        tmp_path,
        "from helper import f\n"
        "\n"
        "\n"
        "def main():\n"
        "    f()\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    output_dir = tmp_path / "tests_dir"
    output_dir.mkdir()
    user_test = output_dir / "test_mine.py"
    user_test.write_text("def test_user_written():\n    assert True\n")

    generator.generate(str(trace_path), str(tmp_path), str(output_dir))

    # The user's own file survives; the generated one was written.
    assert user_test.read_text() == "def test_user_written():\n    assert True\n"
    assert (output_dir / "test_helper.py").exists()

    # A second run with nothing generatable still leaves the user's file alone
    # while clearing the previously generated one.
    empty_trace = _trace(
        tmp_path,
        "def main():\n    return 1\n\n\nif __name__ == '__main__':\n    main()\n",
        filename="other.py",
    )
    generator.generate(str(empty_trace), str(tmp_path), str(output_dir))

    assert user_test.exists()
    assert not (output_dir / "test_helper.py").exists()


def test_sys_path_is_restored_after_a_target_mutates_it_at_import(tmp_path):
    (tmp_path / "helper.py").write_text(
        "import sys\n"
        "sys.path.insert(0, '/definitely/not/a/real/leak/path')\n"
        "\n"
        "\n"
        "def f():\n"
        "    return 1\n"
    )
    trace_path = _trace(
        tmp_path,
        "from helper import f\n"
        "\n"
        "\n"
        "def main():\n"
        "    f()\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    before = list(sys.path)
    generator.generate(
        str(trace_path), str(tmp_path), str(tmp_path / "generated_tests")
    )

    assert sys.path == before


def test_generates_a_passing_test_for_a_function_named_result(tmp_path):
    # `result` is the name the generated body binds the call's result to, so a
    # traced function actually named `result` must not turn into
    # `result = result(...)` (which would raise UnboundLocalError).
    (tmp_path / "helper.py").write_text("def result(x):\n    return x + 1\n")
    trace_path = _trace(
        tmp_path,
        "from helper import result\n"
        "\n"
        "\n"
        "def main():\n"
        "    result(5)\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    output_dir = tmp_path / "generated_tests"
    written = generator.generate(str(trace_path), str(tmp_path), str(output_dir))

    assert written == [output_dir / "test_helper.py"]
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(output_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_does_not_overwrite_a_user_file_sharing_the_generated_name(tmp_path):
    # An unmarked test_helper.py already in the output dir (the user's own,
    # for the same module) must be left intact; the generated tests go to a
    # non-conflicting name instead.
    (tmp_path / "helper.py").write_text("def f():\n    return 1\n")
    trace_path = _trace(
        tmp_path,
        "from helper import f\n"
        "\n"
        "\n"
        "def main():\n"
        "    f()\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    output_dir = tmp_path / "tests_dir"
    output_dir.mkdir()
    user_file = output_dir / "test_helper.py"
    user_body = "def test_user_written():\n    assert True\n"
    user_file.write_text(user_body)

    written = generator.generate(str(trace_path), str(tmp_path), str(output_dir))

    assert user_file.read_text() == user_body
    assert written == [output_dir / "test_helper_1.py"]
    assert (output_dir / "test_helper_1.py").exists()


def test_conftest_evicts_cached_target_modules_once_for_the_session(tmp_path):
    # A single generated conftest.py, not each test file, evicts the target's
    # own modules (and packages) from sys.modules and sets up sys.path. Doing
    # it once is what keeps a shared target module imported exactly once.
    (tmp_path / "config.py").write_text("VALUE = 1\n")
    (tmp_path / "helper.py").write_text(
        "from config import VALUE\n\n\ndef f():\n    return VALUE\n"
    )
    trace_path = _trace(
        tmp_path,
        "from helper import f\n"
        "\n"
        "\n"
        "def main():\n"
        "    f()\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    output_dir = tmp_path / "generated_tests"
    generator.generate(str(trace_path), str(tmp_path), str(output_dir))

    conftest = (output_dir / "conftest.py").read_text()
    assert f"sys.path.insert(0, {str(tmp_path.resolve())!r})" in conftest
    # Both the module under test and its sibling are evicted in the conftest.
    assert "'helper'" in conftest and "'config'" in conftest
    # Eviction is by top-level name, so a cached pkg.sub is cleared too, not
    # only the bare package root.
    assert "_top in _local" in conftest
    # Test files no longer do their own eviction; they just import.
    assert "_local" not in (output_dir / "test_helper.py").read_text()


def test_skips_an_unfinished_call_record_without_crashing(tmp_path):
    # A worker thread still running when the main script returns leaves its
    # call in progress; tracer.stop() writes that record before its return
    # event, so it has no raised/return_* fields. The generator must skip it
    # with a reason, not crash on the missing keys. Built as a literal trace
    # (rather than racing a real thread) to stay deterministic.
    (tmp_path / "helper.py").write_text("def worker():\n    return 1\n")
    trace = [
        {
            "call_id": 0,
            "parent_call_id": None,
            "module": "helper",
            "qualname": "worker",
            "args": {},
            "arg_serialization": {},
        }
    ]
    trace_path = tmp_path / "program.trace.json"
    trace_path.write_text(json.dumps(trace))

    output_dir = tmp_path / "generated_tests"
    written = generator.generate(str(trace_path), str(tmp_path), str(output_dir))

    assert written == []


def test_skips_an_unfinished_call_with_a_clear_reason(tmp_path, capsys):
    (tmp_path / "helper.py").write_text("def worker():\n    return 1\n")
    trace = [
        {
            "call_id": 0,
            "parent_call_id": None,
            "module": "helper",
            "qualname": "worker",
            "args": {},
            "arg_serialization": {},
        }
    ]
    trace_path = tmp_path / "program.trace.json"
    trace_path.write_text(json.dumps(trace))

    generator.generate(str(trace_path), str(tmp_path), str(tmp_path / "out"))

    assert "didn't finish before tracing stopped" in capsys.readouterr().err


def test_a_target_conftest_is_not_in_the_eviction_set(tmp_path):
    # If the target dir has its own conftest.py, "conftest" must not end up in
    # the generated conftest's eviction set - deleting sys.modules['conftest']
    # while pytest is importing a conftest aborts collection with a KeyError.
    (tmp_path / "conftest.py").write_text("# target's own conftest\n")
    (tmp_path / "helper.py").write_text("def f():\n    return 1\n")
    trace_path = _trace(
        tmp_path,
        "from helper import f\n"
        "\n"
        "\n"
        "def main():\n"
        "    f()\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    # output dir beneath the target dir, so pytest picks up the target's
    # conftest.py as an ancestor when running the generated tests.
    output_dir = tmp_path / "generated_tests"
    generator.generate(str(trace_path), str(tmp_path), str(output_dir))

    assert "'conftest'" not in (output_dir / "conftest.py").read_text()

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(output_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_output_dir_that_is_a_package_under_target_does_not_self_evict(tmp_path):
    # When the output dir is a package under the target dir (a real tests/ with
    # __init__.py), "tests" is a target-local name, so the generated
    # tests/conftest.py must not evict its own package - deleting
    # sys.modules['tests.conftest'] mid-import aborts collection with KeyError.
    (tmp_path / "helper.py").write_text("def f():\n    return 1\n")
    trace_path = _trace(
        tmp_path,
        "from helper import f\n"
        "\n"
        "\n"
        "def main():\n"
        "    f()\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    output_dir = tmp_path / "tests"
    output_dir.mkdir()
    (output_dir / "__init__.py").write_text("")
    generator.generate(str(trace_path), str(tmp_path), str(output_dir))

    assert "_top != _own" in (output_dir / "conftest.py").read_text()

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(output_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_generates_a_passing_test_for_a_namespace_package(tmp_path):
    # A namespace package (directory with modules but no __init__.py) is still
    # importable, so its name must be in the eviction set and its submodule
    # calls must generate passing tests.
    pkg = tmp_path / "nspkg"
    pkg.mkdir()
    (pkg / "sub.py").write_text("def f(x):\n    return x + 1\n")
    trace_path = _trace(
        tmp_path,
        "from nspkg.sub import f\n"
        "\n"
        "\n"
        "def main():\n"
        "    f(4)\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    output_dir = tmp_path / "generated_tests"
    written = generator.generate(str(trace_path), str(tmp_path), str(output_dir))

    assert [p.name for p in written] == ["test_nspkg_sub.py"]
    assert "'nspkg'" in (output_dir / "conftest.py").read_text()
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(output_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_does_not_overwrite_a_users_existing_conftest(tmp_path, capsys):
    (tmp_path / "helper.py").write_text("def f():\n    return 1\n")
    trace_path = _trace(
        tmp_path,
        "from helper import f\n"
        "\n"
        "\n"
        "def main():\n"
        "    f()\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    output_dir = tmp_path / "tests_dir"
    output_dir.mkdir()
    user_conftest = output_dir / "conftest.py"
    user_body = "# my own conftest\n"
    user_conftest.write_text(user_body)

    generator.generate(str(trace_path), str(tmp_path), str(output_dir))

    assert user_conftest.read_text() == user_body
    assert "conftest.py" in capsys.readouterr().err
    # The generated test still resolves imports via its own sys.path insert.
    source = (output_dir / "test_helper.py").read_text()
    assert f"sys.path.insert(0, {str(tmp_path.resolve())!r})" in source


def test_two_plain_modules_sharing_import_state_generate_passing_tests(tmp_path):
    # b.py imports a token that a.py sets up at import time. pytest runs both
    # generated files in one process; a.py must be imported exactly once (not
    # re-evicted per file), or the value b sees would differ from the trace.
    (tmp_path / "a.py").write_text(
        "import itertools\n\nCOUNTER = itertools.count()\nTOKEN = next(COUNTER)\n"
        "\n\ndef fa():\n    return TOKEN\n"
    )
    (tmp_path / "b.py").write_text(
        "from a import TOKEN\n\n\ndef fb():\n    return TOKEN\n"
    )
    trace_path = _trace(
        tmp_path,
        "from a import fa\n"
        "from b import fb\n"
        "\n"
        "\n"
        "def main():\n"
        "    fa()\n"
        "    fb()\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    output_dir = tmp_path / "generated_tests"
    generator.generate(str(trace_path), str(tmp_path), str(output_dir))

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(output_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout


def test_generated_tests_for_two_submodules_share_one_package_init(tmp_path):
    # pytest imports all generated files in one process. If each test file
    # evicted the shared package before importing its own submodule, the
    # package's __init__ would re-run per file; a submodule reading a value the
    # __init__ increments would then see a different value than the traced run.
    # The generated tests must not do that - __init__ runs once for the run.
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        "import itertools\n\nCOUNTER = itertools.count()\nTOKEN = next(COUNTER)\n"
    )
    (pkg / "a.py").write_text(
        "from pkg import TOKEN\n\n\ndef fa():\n    return TOKEN\n"
    )
    (pkg / "b.py").write_text(
        "from pkg import TOKEN\n\n\ndef fb():\n    return TOKEN\n"
    )
    trace_path = _trace(
        tmp_path,
        "from pkg.a import fa\n"
        "from pkg.b import fb\n"
        "\n"
        "\n"
        "def main():\n"
        "    fa()\n"
        "    fb()\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    output_dir = tmp_path / "generated_tests"
    written = generator.generate(str(trace_path), str(tmp_path), str(output_dir))
    assert {p.name for p in written} == {"test_pkg_a.py", "test_pkg_b.py"}

    # Both submodules traced TOKEN == 0. Running both generated files together
    # (one pytest process) must keep that true, which only holds if __init__
    # ran once. A per-file package eviction would make the second file see 1.
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(output_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout


def test_skips_a_module_that_calls_sys_exit_at_import(tmp_path, capsys):
    # A target module that exits at import (sys.exit(), argparse, etc.) raises
    # SystemExit, which is a BaseException - it must be reported as an import
    # failure and skipped, not left to abort the whole generation run.
    (tmp_path / "helper.py").write_text("def works():\n    return 1\n")
    trace_path = _trace(
        tmp_path,
        "from helper import works\n"
        "\n"
        "\n"
        "def main():\n"
        "    works()\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )
    (tmp_path / "helper.py").write_text("import sys\n\nsys.exit(2)\n")

    written = generator.generate(
        str(trace_path), str(tmp_path), str(tmp_path / "generated_tests")
    )

    assert written == []
    assert "could not be imported" in capsys.readouterr().err


def test_skips_a_call_whose_module_name_is_a_non_string(tmp_path, capsys):
    # A target that rebinds __name__ to a JSON-safe non-string is recorded
    # verbatim; generation must report it as invalid, not crash on .split().
    (tmp_path / "helper.py").write_text(
        "__name__ = 123\n\n\ndef broken():\n    return 1\n"
    )
    trace_path = _trace(
        tmp_path,
        "import helper\n"
        "\n"
        "\n"
        "def main():\n"
        "    helper.broken()\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    written = generator.generate(
        str(trace_path), str(tmp_path), str(tmp_path / "generated_tests")
    )

    assert written == []
    assert "not a valid import target" in capsys.readouterr().err


def test_generates_one_test_per_call_with_an_index_suffix(tmp_path):
    (tmp_path / "helper.py").write_text("def double(x):\n    return x * 2\n")
    trace_path = _trace(
        tmp_path,
        "from helper import double\n"
        "\n"
        "\n"
        "def main():\n"
        "    double(1)\n"
        "    double(2)\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    output_dir = tmp_path / "generated_tests"
    generator.generate(str(trace_path), str(tmp_path), str(output_dir))

    source = (output_dir / "test_helper.py").read_text()
    assert "def test_double_0():" in source
    assert "def test_double_1():" in source


def test_writes_one_file_per_module(tmp_path):
    (tmp_path / "a.py").write_text("def f():\n    return 1\n")
    (tmp_path / "b.py").write_text("def g():\n    return 2\n")
    trace_path = _trace(
        tmp_path,
        "from a import f\n"
        "from b import g\n"
        "\n"
        "\n"
        "def main():\n"
        "    f()\n"
        "    g()\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    output_dir = tmp_path / "generated_tests"
    written = generator.generate(str(trace_path), str(tmp_path), str(output_dir))

    assert {p.name for p in written} == {"test_a.py", "test_b.py"}


def test_a_stale_sys_modules_entry_from_an_earlier_generate_call_is_not_reused(
    tmp_path,
):
    # generate() can run more than once in the same process pointing at
    # different target_dirs whose modules happen to share a name - each
    # call must re-import fresh rather than reuse whatever sys.modules
    # cached from the previous one.
    first_dir = tmp_path / "first"
    first_dir.mkdir()
    (first_dir / "helper.py").write_text("def value():\n    return 'first'\n")
    first_trace = _trace(
        first_dir,
        "from helper import value\n"
        "\n"
        "\n"
        "def main():\n"
        "    value()\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    second_dir = tmp_path / "second"
    second_dir.mkdir()
    (second_dir / "helper.py").write_text("def value():\n    return 'second'\n")
    second_trace = _trace(
        second_dir,
        "from helper import value\n"
        "\n"
        "\n"
        "def main():\n"
        "    value()\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    generator.generate(str(first_trace), str(first_dir), str(first_dir / "out"))
    generator.generate(str(second_trace), str(second_dir), str(second_dir / "out"))

    first_source = (first_dir / "out" / "test_helper.py").read_text()
    second_source = (second_dir / "out" / "test_helper.py").read_text()
    assert "assert result == 'first'" in first_source
    assert "assert result == 'second'" in second_source
