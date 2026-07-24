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


def test_skips_a_function_with_unsupported_varargs_signature(tmp_path, capsys):
    (tmp_path / "helper.py").write_text(
        "def with_varargs(*args, **kwargs):\n    return args\n"
    )
    trace_path = _trace(
        tmp_path,
        "from helper import with_varargs\n"
        "\n"
        "\n"
        "def main():\n"
        "    with_varargs(1, 2)\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    written = generator.generate(
        str(trace_path), str(tmp_path), str(tmp_path / "generated_tests")
    )

    assert written == []
    assert "unsupported signature" in capsys.readouterr().err


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
