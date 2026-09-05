import json
import os
import subprocess
import sys
from unittest import mock

from ai_tracer import ai_generator, generator


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


def test_skips_the_entry_scripts_own_functions_without_an_entry_script_path(
    tmp_path, capsys
):
    # Without entry_script, generate() has no way to import "__main__".
    trace_path = _trace(
        tmp_path,
        'def main():\n    return 1\n\n\nif __name__ == "__main__":\n    main()\n',
    )

    written = generator.generate(
        str(trace_path), str(tmp_path), str(tmp_path / "generated_tests")
    )

    assert written == []
    assert "its file path" in capsys.readouterr().err
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


def test_generates_a_passing_pytest_raises_test_for_a_builtin_exception(tmp_path):
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

    output_dir = tmp_path / "generated_tests"
    written = generator.generate(str(trace_path), str(tmp_path), str(output_dir))

    assert written == [output_dir / "test_helper.py"]
    source = (output_dir / "test_helper.py").read_text()
    assert "import pytest as _raises_pytest" in source
    assert "import builtins as _raises_builtins" in source
    assert "with _raises_pytest.raises(_raises_builtins.ValueError):" in source
    assert "fails()" in source

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(output_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_generates_a_passing_pytest_raises_test_for_a_target_exception(tmp_path):
    (tmp_path / "errors.py").write_text("class AppError(Exception):\n    pass\n")
    (tmp_path / "helper.py").write_text(
        "from errors import AppError\n\n\ndef boom(x):\n    raise AppError(x)\n"
    )
    trace_path = _trace(
        tmp_path,
        "from helper import boom\n"
        "\n"
        "\n"
        "def main():\n"
        "    try:\n"
        "        boom(3)\n"
        "    except Exception:\n"
        "        pass\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    output_dir = tmp_path / "generated_tests"
    generator.generate(str(trace_path), str(tmp_path), str(output_dir))

    source = (output_dir / "test_helper.py").read_text()
    # The exception module is imported under a reserved alias and referenced
    # through it, so it can't be shadowed by a same-named function import.
    assert "import errors as _raises_errors" in source
    assert "with _raises_pytest.raises(_raises_errors.AppError):" in source
    assert "boom(x=3)" in source

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(output_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_exception_module_alias_survives_a_same_named_function(tmp_path):
    # helper defines a function `errors` and raises `errors.AppError` from a
    # sibling `errors` module; the alias must survive `from helper import errors`.
    (tmp_path / "errors.py").write_text("class AppError(Exception):\n    pass\n")
    (tmp_path / "helper.py").write_text(
        "from errors import AppError\n"
        "\n"
        "\n"
        "def errors():\n"
        "    return 1\n"
        "\n"
        "\n"
        "def boom():\n"
        "    raise AppError('x')\n"
    )
    trace_path = _trace(
        tmp_path,
        "from helper import errors, boom\n"
        "\n"
        "\n"
        "def main():\n"
        "    errors()\n"
        "    try:\n"
        "        boom()\n"
        "    except Exception:\n"
        "        pass\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    output_dir = tmp_path / "generated_tests"
    generator.generate(str(trace_path), str(tmp_path), str(output_dir))

    source = (output_dir / "test_helper.py").read_text()
    assert "from helper import boom, errors" in source
    assert "import errors as _raises_errors" in source
    assert "with _raises_pytest.raises(_raises_errors.AppError):" in source

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(output_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout


def test_raised_call_is_generated_when_the_exception_module_only_imports_cleanly_after_the_function_module(
    tmp_path,
):
    # Circular pair: cold-importing the exception module first fails (it
    # eagerly reads an attribute off the partial function module), but the
    # function module first succeeds. generate() must try that order first.
    (tmp_path / "errors.py").write_text(
        "import helper\n\n\nclass AppError(Exception):\n    pass\n"
    )
    (tmp_path / "helper.py").write_text(
        "import errors\n"
        "\n"
        "CHECK = errors.AppError\n"
        "\n"
        "\n"
        "def boom(x):\n"
        "    raise errors.AppError(x)\n"
    )
    trace_path = _trace(
        tmp_path,
        "from helper import boom\n"
        "from errors import AppError\n"
        "\n"
        "\n"
        "def main():\n"
        "    try:\n"
        "        boom(1)\n"
        "    except AppError:\n"
        "        pass\n"
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


def test_emits_pytest_raises_for_the_recorded_raised_flag_even_if_a_false_positive(
    tmp_path,
):
    # Documented limitation: the generator trusts the trace's raised flag, so
    # a catch-and-return-None function still gets a pytest.raises test, which
    # would fail if run. Asserts the emitted shape, not a passing run.
    (tmp_path / "helper.py").write_text(
        "def safe_get(d, k):\n"
        "    try:\n"
        "        return d[k]\n"
        "    except KeyError:\n"
        "        return None\n"
    )
    trace_path = _trace(
        tmp_path,
        "from helper import safe_get\n"
        "\n"
        "\n"
        "def main():\n"
        "    safe_get({'a': 1}, 'missing')\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    output_dir = tmp_path / "generated_tests"
    generator.generate(str(trace_path), str(tmp_path), str(output_dir))

    source = (output_dir / "test_helper.py").read_text()
    assert "with _raises_pytest.raises(_raises_builtins.KeyError):" in source


def test_skips_a_raised_call_whose_exception_module_is_the_entry_script(
    tmp_path, capsys
):
    # module "__main__" has no importable path, so the raised call is
    # skipped. Built as a literal trace since this is awkward to produce naturally.
    (tmp_path / "helper.py").write_text("def boom():\n    raise ValueError('x')\n")
    trace = [
        {
            "call_id": 0,
            "parent_call_id": None,
            "module": "helper",
            "qualname": "boom",
            "args": {},
            "arg_serialization": {},
            "raised": True,
            "return_value": None,
            "return_serialization": None,
            "exception_module": "__main__",
            "exception_type": "LocalError",
        }
    ]
    trace_path = tmp_path / "program.trace.json"
    trace_path.write_text(json.dumps(trace))

    written = generator.generate(
        str(trace_path), str(tmp_path), str(tmp_path / "generated_tests")
    )

    assert written == []
    assert "exception module '__main__'" in capsys.readouterr().err


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

    assert "instance methods aren't supported yet" in capsys.readouterr().err
    assert not (tmp_path / "generated_tests" / "test_helper.py").exists()


def test_generates_a_passing_test_for_a_staticmethod(tmp_path):
    (tmp_path / "helper.py").write_text(
        "class Widget:\n    @staticmethod\n    def double(x):\n        return x * 2\n"
    )
    trace_path = _trace(
        tmp_path,
        "from helper import Widget\n"
        "\n"
        "\n"
        "def main():\n"
        "    Widget.double(21)\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    output_dir = tmp_path / "generated_tests"
    generator.generate(str(trace_path), str(tmp_path), str(output_dir))

    source = (output_dir / "test_helper.py").read_text()
    assert "from helper import Widget" in source
    assert "def test_Widget_double_0():" in source
    assert "result = Widget.double(x=21)" in source

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(output_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_skips_a_classmethod_call(tmp_path, capsys):
    # Unlike a staticmethod, a classmethod's qualname always names the class
    # it's *defined* on, not the one actually used to call it (a call through
    # a subclass still traces as e.g. "Base.named" with cls bound to the
    # subclass) - and the trace can't tell the two apart (cls's own repr()
    # fallback carries no class name at all). Rendering through the
    # defining class could silently assert the wrong class's behavior, so
    # classmethods are skipped entirely for now, not just the inherited case.
    (tmp_path / "helper.py").write_text(
        "class Widget:\n"
        "    @classmethod\n"
        "    def named(cls, name):\n"
        "        return f'{cls.__name__}:{name}'\n"
    )
    trace_path = _trace(
        tmp_path,
        "from helper import Widget\n"
        "\n"
        "\n"
        "def main():\n"
        "    Widget.named('a')\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    written = generator.generate(
        str(trace_path), str(tmp_path), str(tmp_path / "generated_tests")
    )

    assert written == []
    assert "classmethods aren't supported yet" in capsys.readouterr().err


def test_generated_test_names_dont_collide_between_a_function_and_a_method(tmp_path):
    # "Widget.double" and a plain function literally named "Widget_double"
    # sanitize to the same test function name once dots become underscores -
    # without a fix, the second `def test_Widget_double_0():` silently
    # shadows the first one instead of getting its own "_1" suffix.
    (tmp_path / "helper.py").write_text(
        "class Widget:\n"
        "    @staticmethod\n"
        "    def double(x):\n"
        "        return x * 2\n"
        "\n"
        "\n"
        "def Widget_double(x):\n"
        "    return x * 3\n"
    )
    trace_path = _trace(
        tmp_path,
        "from helper import Widget, Widget_double\n"
        "\n"
        "\n"
        "def main():\n"
        "    Widget.double(21)\n"
        "    Widget_double(21)\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    output_dir = tmp_path / "generated_tests"
    generator.generate(str(trace_path), str(tmp_path), str(output_dir))

    source = (output_dir / "test_helper.py").read_text()
    assert source.count("def test_Widget_double_") == 2
    assert "def test_Widget_double_0():" in source
    assert "def test_Widget_double_1():" in source

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(output_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout


def test_generated_test_mocks_a_staticmethod_child_call(tmp_path):
    (tmp_path / "util.py").write_text(
        "class Widget:\n"
        "    @staticmethod\n"
        "    def inner(x):\n"
        "        return x + 1\n"
    )
    (tmp_path / "helper.py").write_text(
        "from util import Widget\n\n\ndef outer(x):\n    return Widget.inner(x) * 10\n"
    )
    trace_path = _trace(
        tmp_path,
        "from helper import outer\n"
        "\n"
        "\n"
        "def main():\n"
        "    outer(4)\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    output_dir = tmp_path / "generated_tests"
    generator.generate(str(trace_path), str(tmp_path), str(output_dir))

    test_helper_path = output_dir / "test_helper.py"
    source = test_helper_path.read_text()
    assert "mock.patch('helper.Widget.inner', return_value=5) as" in source

    # Break the real staticmethod after tracing (test_util.py, which calls
    # it directly and isn't the point of this test, is expected to start
    # failing once it's broken - only test_helper.py's own isolation matters
    # here, same as the plain-function mocking test above).
    (tmp_path / "util.py").write_text(
        "class Widget:\n"
        "    @staticmethod\n"
        "    def inner(x):\n"
        "        raise RuntimeError('the real inner ran')\n"
    )
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(test_helper_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_skips_a_call_to_a_property_shaped_like_a_method(tmp_path, capsys):
    (tmp_path / "helper.py").write_text(
        "class Widget:\n"
        "    @property\n"
        "    def value(self):\n"
        "        return 1\n"
    )
    trace_path = _trace(
        tmp_path,
        "from helper import Widget\n"
        "\n"
        "\n"
        "def main():\n"
        "    Widget().value\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    written = generator.generate(
        str(trace_path), str(tmp_path), str(tmp_path / "generated_tests")
    )

    assert written == []
    assert "is not a plain method" in capsys.readouterr().err


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
    # return_serialization=null is a genuine None return (a safe literal),
    # not an uncapturable value - must still generate a passing test.
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
    # A module named "config" is already cached in sys.modules (simulating
    # a stdlib name or a leftover from a previous generate() call); the
    # target's own sibling "config" must win.
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
    # Importing pkg.a then pkg.b must run pkg/__init__.py exactly once,
    # like a normal run - not once per submodule.
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
    assert "_name.split('.')[0] in _local" in conftest
    # Test files no longer do their own eviction; they just import.
    assert "_local" not in (output_dir / "test_helper.py").read_text()


def test_skips_an_unfinished_call_record_without_crashing(tmp_path):
    # An unjoined worker thread's record has no raised/return_* fields.
    # Built as a literal trace (rather than racing a real thread) to stay
    # deterministic.
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
    # "tests" is a target-local name here, so the generated tests/conftest.py
    # must not evict its own package (would abort collection with KeyError).
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

    assert "_name not in _keep" in (output_dir / "conftest.py").read_text()

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(output_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_output_dir_nested_in_a_target_package_still_evicts_siblings(tmp_path):
    # Output dir <target>/pkg/tests: the conftest's own ancestor chain must
    # be spared, but sibling pkg.calc must still be evicted.
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    trace_path = _trace(
        tmp_path,
        "from pkg.calc import add\n"
        "\n"
        "\n"
        "def main():\n"
        "    add(2, 3)\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    output_dir = pkg / "tests"
    output_dir.mkdir()
    (output_dir / "__init__.py").write_text("")
    generator.generate(str(trace_path), str(tmp_path), str(output_dir))

    conftest = (output_dir / "conftest.py").read_text()
    # Only the ancestor chain is spared; the eviction is keyed off _keep.
    assert "_keep = {__name__}" in conftest
    assert "'pkg'" in conftest

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
    # If each test file evicted the shared package, __init__ would re-run
    # per file, so a submodule reading its counter would see a different
    # value than the traced run. __init__ must run once for the whole session.
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


def test_a_target_that_chdirs_at_import_does_not_leak_cwd_or_misplace_output(
    tmp_path,
):
    # A target module that calls os.chdir() at import time must not leave the
    # caller in that directory, and a relative output_dir must still resolve
    # against the original cwd, not the target's new one.
    other = tmp_path / "elsewhere"
    other.mkdir()
    (tmp_path / "helper.py").write_text(
        f"import os\n\nos.chdir({str(other)!r})\n\n\ndef f():\n    return 1\n"
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

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    original_cwd = os.getcwd()
    os.chdir(workdir)
    try:
        generator.generate(str(trace_path), str(tmp_path), "generated_tests")
        # cwd is restored, and the relative output landed under workdir, not
        # under the directory the target chdir'd into.
        assert os.getcwd() == str(workdir)
        assert (workdir / "generated_tests" / "test_helper.py").exists()
        assert not (other / "generated_tests").exists()
    finally:
        os.chdir(original_cwd)


def test_skips_a_function_whose_getattr_hook_raises(tmp_path, capsys):
    # A module with a __getattr__ that raises (e.g. the function was removed and
    # the hook now errors) can raise something other than AttributeError when
    # looked up. That must be skipped, not abort the whole run.
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
    # Replace the module: importable, but looking up `works` raises a
    # non-AttributeError via __getattr__.
    (tmp_path / "helper.py").write_text(
        "def __getattr__(name):\n    raise RuntimeError('gone: ' + name)\n"
    )

    written = generator.generate(
        str(trace_path), str(tmp_path), str(tmp_path / "generated_tests")
    )

    assert written == []
    assert "Skipping helper.works" in capsys.readouterr().err


def test_skips_a_module_that_raises_a_bare_base_exception_at_import(tmp_path, capsys):
    # Import-time failures aren't limited to Exception/SystemExit: a module can
    # raise other BaseException subclasses (GeneratorExit here). These must be
    # caught and the call skipped, not left to abort the whole run.
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
    (tmp_path / "helper.py").write_text("raise GeneratorExit('gone')\n")

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
    # Two target_dirs whose modules share a name: each generate() call must
    # re-import fresh rather than reuse the previous call's sys.modules entry.
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


def test_generates_a_passing_test_for_the_entry_scripts_own_function(tmp_path):
    program_source = (
        "def add(a, b):\n"
        "    return a + b\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    add(2, 3)\n"
    )
    trace_path = _trace(tmp_path, program_source)

    output_dir = tmp_path / "generated_tests"
    written = generator.generate(
        str(trace_path),
        str(tmp_path),
        str(output_dir),
        entry_script=str(tmp_path / "program.py"),
    )

    assert written == [output_dir / "test___main__.py"]
    source = (output_dir / "test___main__.py").read_text()
    assert "spec_from_file_location" in source
    assert "add(a=2, b=3)" in source

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(output_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_entry_script_reload_does_not_re_trigger_its_own_main_guard(tmp_path):
    marker = tmp_path / "guard_runs.txt"
    program_source = (
        "from pathlib import Path\n"
        "\n"
        "\n"
        "def add(a, b):\n"
        "    return a + b\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        f"    marker = Path({str(marker)!r})\n"
        "    marker.write_text(marker.read_text() + 'x' if marker.exists() else 'x')\n"
        "    add(2, 3)\n"
    )
    # _trace() runs the CLI, which now generates automatically too - if the
    # guard reran during that, marker would already read "xx" here.
    trace_path = _trace(tmp_path, program_source)
    assert marker.read_text() == "x"

    generator.generate(
        str(trace_path),
        str(tmp_path),
        str(tmp_path / "generated_tests"),
        entry_script=str(tmp_path / "program.py"),
    )

    assert marker.read_text() == "x"


def test_entry_script_function_reading_dunder_name_still_generates_correctly(
    tmp_path,
):
    # The entry script is reloaded under a synthetic name during generation
    # so its own main guard doesn't refire; __name__ must still read back as
    # "__main__" afterward, matching what the function saw when traced.
    program_source = (
        "def module_name():\n"
        "    return __name__\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    module_name()\n"
    )
    trace_path = _trace(tmp_path, program_source)

    output_dir = tmp_path / "generated_tests"
    generator.generate(
        str(trace_path),
        str(tmp_path),
        str(output_dir),
        entry_script=str(tmp_path / "program.py"),
    )

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(output_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_entry_script_function_depending_on_dunder_module_is_a_known_limitation(
    tmp_path,
):
    # Documented limitation: a function's own __module__ is baked in at
    # definition time from the synthetic name used to load the entry
    # script, not patched back like the bare __name__ global is.
    program_source = (
        "def where():\n"
        "    return where.__module__\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    where()\n"
    )
    trace_path = _trace(tmp_path, program_source)

    output_dir = tmp_path / "generated_tests"
    generator.generate(
        str(trace_path),
        str(tmp_path),
        str(output_dir),
        entry_script=str(tmp_path / "program.py"),
    )

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(output_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "1 failed" in result.stdout


def test_generates_a_passing_raises_test_for_an_entry_script_exception(tmp_path):
    program_source = (
        "class AppError(Exception):\n"
        "    pass\n"
        "\n"
        "\n"
        "def fail():\n"
        "    raise AppError('boom')\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    try:\n"
        "        fail()\n"
        "    except AppError:\n"
        "        pass\n"
    )
    trace_path = _trace(tmp_path, program_source)

    output_dir = tmp_path / "generated_tests"
    generator.generate(
        str(trace_path),
        str(tmp_path),
        str(output_dir),
        entry_script=str(tmp_path / "program.py"),
    )

    source = (output_dir / "test___main__.py").read_text()
    assert "with _raises_pytest.raises(_entry_module.AppError):" in source

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(output_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def _call(call_id=0, parent_call_id=None, module="helper", qualname="f", **overrides):
    call = {
        "call_id": call_id,
        "parent_call_id": parent_call_id,
        "module": module,
        "qualname": qualname,
        "args": {},
        "arg_serialization": {},
        "raised": False,
        "return_value": 1,
        "return_serialization": "json",
        "exception_module": None,
        "exception_type": None,
    }
    call.update(overrides)
    return call


def test_children_by_call_id_groups_direct_children_only():
    root = _call(call_id=0)
    child = _call(call_id=1, parent_call_id=0)
    grandchild = _call(call_id=2, parent_call_id=1)

    children = generator._children_by_call_id([root, child, grandchild])

    assert children[0] == [child]
    assert children[1] == [grandchild]
    assert 2 not in children


def test_mock_skip_reason_accepts_a_plain_returning_call():
    call = _call(return_value=42, return_serialization="json")
    assert generator._mock_skip_reason(call, module_cache={}) is None


def test_mock_skip_reason_ignores_the_childs_own_unserializable_args():
    # Mocking replaces the whole call, so what it was passed never matters -
    # only a real replayed call needs its arguments to be JSON-safe.
    call = _call(args={"x": "<Widget>"}, arg_serialization={"x": "repr"})
    assert generator._mock_skip_reason(call, module_cache={}) is None


def test_mock_skip_reason_rejects_a_repr_only_return_value():
    call = _call(return_value="<Widget>", return_serialization="repr")
    reason = generator._mock_skip_reason(call, module_cache={})
    assert "could not be captured as a JSON value" in reason


def test_mock_skip_reason_rejects_an_array_return_value():
    call = _call(return_value=[1, 2], return_serialization="json")
    reason = generator._mock_skip_reason(call, module_cache={})
    assert "return value contains a list/array" in reason


def test_mock_skip_reason_rejects_an_unfinished_call():
    call = _call()
    del call["raised"]
    reason = generator._mock_skip_reason(call, module_cache={})
    assert "didn't finish before tracing stopped" in reason


def test_mock_skip_reason_rejects_a_method_qualname():
    import types

    class Greeter:
        def greet(self):
            return "hi"

    module = types.ModuleType("helper")
    module.Greeter = Greeter
    call = _call(qualname="Greeter.greet")
    reason = generator._mock_skip_reason(call, module_cache={"helper": module})
    assert "instance methods aren't supported yet" in reason


def test_mock_skip_reason_rejects_a_nested_function_qualname():
    call = _call(qualname="outer.<locals>.inner")
    reason = generator._mock_skip_reason(call, module_cache={})
    assert "not a plain top-level function" in reason


def test_mock_skip_reason_accepts_a_staticmethod_qualname():
    import types

    class Widget:
        @staticmethod
        def inner(x):
            return x + 1

    module = types.ModuleType("helper")
    module.Widget = Widget
    call = _call(qualname="Widget.inner", return_value=5)
    assert generator._mock_skip_reason(call, module_cache={"helper": module}) is None


def test_mock_skip_reason_rejects_an_invalid_module_name():
    call = _call(module="class")
    reason = generator._mock_skip_reason(call, module_cache={})
    assert "not a valid import target" in reason


def test_mock_skip_reason_accepts_a_raised_builtin_exception():
    call = _call(
        raised=True,
        return_value=None,
        return_serialization=None,
        exception_module="builtins",
        exception_type="ValueError",
    )
    assert generator._mock_skip_reason(call, module_cache={}) is None


def test_mock_skip_reason_rejects_a_raised_exception_from_an_unimportable_module():
    call = _call(
        raised=True,
        return_value=None,
        return_serialization=None,
        exception_module="mymod",
        exception_type="AppError",
    )
    # A pre-populated None entry stands in for an exception module that
    # fails to import, without needing a real unimportable file on disk.
    reason = generator._mock_skip_reason(call, module_cache={"mymod": None})
    assert "could not be imported" in reason


def test_unmockable_child_reason_is_none_with_no_children():
    call = _call(call_id=0)
    assert generator._unmockable_child_reason(call, {}, module_cache={}) is None


def test_unmockable_child_reason_is_none_when_every_child_is_mockable():
    call = _call(call_id=0)
    children_by_call_id = {
        0: [
            _call(call_id=1, parent_call_id=0, qualname="a"),
            _call(call_id=2, parent_call_id=0, qualname="b"),
        ]
    }
    assert (
        generator._unmockable_child_reason(call, children_by_call_id, module_cache={})
        is None
    )


def test_unmockable_child_reason_reports_the_first_unmockable_child():
    call = _call(call_id=0)
    bad_child = _call(
        call_id=1,
        parent_call_id=0,
        qualname="bad",
        return_value="<Widget>",
        return_serialization="repr",
    )
    children_by_call_id = {0: [bad_child]}

    reason = generator._unmockable_child_reason(call, children_by_call_id, module_cache={})

    assert "child call helper.bad can't be mocked" in reason
    assert "could not be captured as a JSON value" in reason


def test_skips_an_entry_script_exception_raised_by_a_helper_module_function(
    tmp_path, capsys
):
    # The cross-module case (a helper function raising an exception class
    # defined in the entry script) stays an unsupported, documented skip
    # even when entry_script is given - only the same-module case is lifted.
    (tmp_path / "helper.py").write_text(
        "def boom():\n    from __main__ import AppError\n    raise AppError('x')\n"
    )
    program_source = (
        "from helper import boom\n"
        "\n"
        "\n"
        "class AppError(Exception):\n"
        "    pass\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    try:\n"
        "        boom()\n"
        "    except AppError:\n"
        "        pass\n"
    )
    trace_path = _trace(tmp_path, program_source)

    written = generator.generate(
        str(trace_path),
        str(tmp_path),
        str(tmp_path / "generated_tests"),
        entry_script=str(tmp_path / "program.py"),
    )

    assert written == []
    assert "can only be named when the raising function is also" in (
        capsys.readouterr().err
    )


def test_generated_test_mocks_a_direct_child_and_still_passes_when_it_is_broken(
    tmp_path,
):
    # The actual point of M4: prove the default generate() pipeline mocks a
    # function under test's own children, so the generated test still
    # passes even when the real child would now behave differently.
    (tmp_path / "util.py").write_text("def inner(x):\n    return x + 1\n")
    (tmp_path / "helper.py").write_text(
        "from util import inner\n\n\ndef outer(x):\n    return inner(x) * 10\n"
    )
    trace_path = _trace(
        tmp_path,
        "from helper import outer\n"
        "\n"
        "\n"
        "def main():\n"
        "    outer(4)\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    output_dir = tmp_path / "generated_tests"
    written = generator.generate(str(trace_path), str(tmp_path), str(output_dir))

    test_helper_path = output_dir / "test_helper.py"
    assert test_helper_path in written
    source = test_helper_path.read_text()
    assert "from unittest import mock" in source
    assert "mock.patch('helper.inner', return_value=5) as" in source

    # Break inner() for real, after tracing - the generated test can only
    # keep passing if it never actually calls this. (test_util.py, which
    # calls the real inner() directly and isn't the point of this test, is
    # left out of the run - it's expected to start failing once inner() is
    # broken.)
    (tmp_path / "util.py").write_text(
        "def inner(x):\n    raise RuntimeError('the real inner ran')\n"
    )

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(test_helper_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_skips_a_call_whose_direct_child_is_unmockable(tmp_path, capsys):
    (tmp_path / "util.py").write_text(
        "class Widget:\n    pass\n\n\ndef inner():\n    return Widget()\n"
    )
    (tmp_path / "helper.py").write_text(
        "from util import inner\n\n\ndef outer():\n    inner()\n    return 1\n"
    )
    trace_path = _trace(
        tmp_path,
        "from helper import outer\n"
        "\n"
        "\n"
        "def main():\n"
        "    outer()\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    written = generator.generate(
        str(trace_path), str(tmp_path), str(tmp_path / "generated_tests")
    )

    assert written == []
    err = capsys.readouterr().err
    assert "child call util.inner can't be mocked" in err
    assert "could not be captured as a JSON value" in err


def test_generated_test_mocks_multiple_and_repeated_children(tmp_path):
    (tmp_path / "util.py").write_text(
        "def inner(x):\n    return x + 1\n\n\ndef extra(x):\n    return x * 2\n"
    )
    (tmp_path / "helper.py").write_text(
        "from util import inner, extra\n"
        "\n"
        "\n"
        "def outer(a, b):\n"
        "    return inner(a) + inner(b) + extra(a)\n"
    )
    trace_path = _trace(
        tmp_path,
        "from helper import outer\n"
        "\n"
        "\n"
        "def main():\n"
        "    outer(4, 10)\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    output_dir = tmp_path / "generated_tests"
    written = generator.generate(str(trace_path), str(tmp_path), str(output_dir))

    test_helper_path = output_dir / "test_helper.py"
    assert test_helper_path in written
    source = test_helper_path.read_text()
    assert "mock.patch('helper.inner', side_effect=[5, 11]) as" in source
    assert "mock.patch('helper.extra', return_value=8) as" in source

    (tmp_path / "util.py").write_text(
        "def inner(x):\n    raise RuntimeError('the real inner ran')\n\n\n"
        "def extra(x):\n    raise RuntimeError('the real extra ran')\n"
    )

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(test_helper_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_generated_test_mocks_a_raised_child_with_a_target_exception(tmp_path):
    (tmp_path / "errors.py").write_text("class AppError(Exception):\n    pass\n")
    (tmp_path / "util.py").write_text(
        "from errors import AppError\n\n\ndef inner():\n    raise AppError('boom')\n"
    )
    (tmp_path / "helper.py").write_text(
        "from util import inner\n"
        "\n"
        "\n"
        "def outer():\n"
        "    try:\n"
        "        inner()\n"
        "    except Exception:\n"
        "        return -1\n"
    )
    trace_path = _trace(
        tmp_path,
        "from helper import outer\n"
        "\n"
        "\n"
        "def main():\n"
        "    outer()\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    output_dir = tmp_path / "generated_tests"
    written = generator.generate(str(trace_path), str(tmp_path), str(output_dir))

    test_helper_path = output_dir / "test_helper.py"
    assert test_helper_path in written
    source = test_helper_path.read_text()
    assert "side_effect=_raises_errors.AppError" in source

    (tmp_path / "util.py").write_text(
        "def inner():\n    raise RuntimeError('the real inner ran')\n"
    )

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(test_helper_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_generated_test_mocks_an_entry_scripts_own_child_function(tmp_path):
    # The entry-script case is special: "__main__" as a mock target string
    # would resolve to whatever this pytest process's own entry point is,
    # not the reloaded copy - proves that's wired around correctly too.
    program_source = (
        "def inner(x):\n"
        "    return x + 1\n"
        "\n"
        "\n"
        "def main():\n"
        "    return inner(4) * 10\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n"
    )
    trace_path = _trace(tmp_path, program_source)

    output_dir = tmp_path / "generated_tests"
    written = generator.generate(
        str(trace_path),
        str(tmp_path),
        str(output_dir),
        entry_script=str(tmp_path / "program.py"),
    )

    assert written == [output_dir / "test___main__.py"]
    source = (output_dir / "test___main__.py").read_text()
    assert "mock.patch('_ai_tracer_entry_script.inner', return_value=5) as" in source

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(output_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # main()'s test mocks inner() away, and inner() also gets its own
    # unmocked test from being directly traced - both pass.
    assert "2 passed" in result.stdout


def test_generate_with_ai_true_includes_the_ai_test_file_in_the_return_value(
    tmp_path,
):
    (tmp_path / "helper.py").write_text("def add(a, b):\n    return a + b\n")
    trace_path = _trace(
        tmp_path,
        "from helper import add\n"
        "\n"
        "\n"
        "def main():\n"
        "    add(1, 2)\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    output_dir = tmp_path / "generated_tests"
    with mock.patch.object(ai_generator, "_call_llm", return_value="add(a=0, b=0)\n"):
        written = generator.generate(
            str(trace_path), str(tmp_path), str(output_dir), ai=True
        )

    assert output_dir / "test_helper.py" in written
    assert output_dir / "test_helper_ai.py" in written
    assert (output_dir / "test_helper_ai.py").exists()
