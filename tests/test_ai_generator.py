import subprocess
import sys
from unittest import mock

import pytest

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


def test_generates_ai_tests_with_verified_expected_values(tmp_path):
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

    with mock.patch.object(
        ai_generator,
        "_call_llm",
        return_value="add(a=0, b=0)\nadd(a=-1, b=1)\n",
    ):
        output_dir = tmp_path / "generated_tests"
        written = ai_generator.generate_ai_tests(
            str(trace_path), str(tmp_path), str(output_dir)
        )

    assert written == [output_dir / "test_helper_ai.py"]
    source = (output_dir / "test_helper_ai.py").read_text()
    assert "def test_add_0():" in source
    assert "result = add(a=0, b=0)" in source
    assert "assert result == 0" in source
    assert "def test_add_1():" in source
    assert "result = add(a=-1, b=1)" in source
    assert "assert result == 0" in source

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(output_dir / "test_helper_ai.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout


def test_generates_no_tests_when_llm_response_has_no_valid_calls(tmp_path):
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

    with mock.patch.object(
        ai_generator,
        "_call_llm",
        return_value="no valid calls here\n",
    ):
        output_dir = tmp_path / "generated_tests"
        written = ai_generator.generate_ai_tests(
            str(trace_path), str(tmp_path), str(output_dir)
        )

    assert written == []


def test_parse_response_extracts_function_calls():
    response = "add(a=0, b=0)\nadd(a=-1, b=1)\n# comment\nnot a call\n"
    test_cases = ai_generator._parse_response(response, "add")
    assert test_cases == [{"a": 0, "b": 0}, {"a": -1, "b": 1}]


def test_parse_response_ignores_calls_to_other_functions():
    response = "add(a=0, b=0)\nsubtract(a=5, b=3)\n"
    test_cases = ai_generator._parse_response(response, "add")
    assert test_cases == [{"a": 0, "b": 0}]


def test_parse_response_skips_a_positional_argument_call():
    response = "add(1, 2)\nadd(a=0, b=0)\n"
    test_cases = ai_generator._parse_response(response, "add")
    assert test_cases == [{"a": 0, "b": 0}]


def test_parse_response_keeps_a_zero_argument_call():
    response = "greet()\n"
    test_cases = ai_generator._parse_response(response, "greet")
    assert test_cases == [{}]


def test_does_not_clobber_a_pre_existing_unmarked_file(tmp_path):
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
    output_dir.mkdir(exist_ok=True)
    own_file = output_dir / "test_helper_ai.py"
    own_file.write_text("# hand-written, not ai-tracer output\n")

    with mock.patch.object(ai_generator, "_call_llm", return_value="add(a=0, b=0)\n"):
        written = ai_generator.generate_ai_tests(
            str(trace_path), str(tmp_path), str(output_dir)
        )

    assert own_file.read_text() == "# hand-written, not ai-tracer output\n"
    assert written == [output_dir / "test_helper_ai_1.py"]


def test_removes_a_stale_ai_test_file_for_a_module_no_longer_in_the_trace(tmp_path):
    (tmp_path / "helper.py").write_text(
        "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"
    )
    trace_path = _trace(
        tmp_path,
        "from helper import add, sub\n"
        "\n"
        "\n"
        "def main():\n"
        "    add(1, 2)\n"
        "    sub(1, 2)\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )
    output_dir = tmp_path / "generated_tests"

    with mock.patch.object(
        ai_generator, "_call_llm", return_value="add(a=0, b=0)\nsub(a=0, b=0)\n"
    ):
        ai_generator.generate_ai_tests(str(trace_path), str(tmp_path), str(output_dir))
    assert (output_dir / "test_helper_ai.py").exists()

    # Re-trace calling only `add` -- `helper` is still generatable, but the
    # file should be rewritten with just the current calls, not left stale.
    trace_path_2 = _trace(
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
    with mock.patch.object(ai_generator, "_call_llm", return_value="add(a=0, b=0)\n"):
        written = ai_generator.generate_ai_tests(
            str(trace_path_2), str(tmp_path), str(output_dir)
        )

    assert written == [output_dir / "test_helper_ai.py"]
    source = (output_dir / "test_helper_ai.py").read_text()
    assert "def test_sub_0():" not in source


def test_removes_a_stale_ai_test_file_when_a_later_run_disables_ai(tmp_path):
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

    with mock.patch.object(ai_generator, "_call_llm", return_value="double(x=0)\n"):
        generator.generate(str(trace_path), str(tmp_path), str(output_dir), ai=True)
    assert (output_dir / "test_helper_ai.py").exists()

    generator.generate(str(trace_path), str(tmp_path), str(output_dir), ai=False)

    assert not (output_dir / "test_helper_ai.py").exists()


def test_skips_a_function_gracefully_when_the_llm_call_fails(tmp_path):
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

    with mock.patch.object(
        ai_generator, "_call_llm", side_effect=RuntimeError("connection refused")
    ):
        output_dir = tmp_path / "generated_tests"
        written = ai_generator.generate_ai_tests(
            str(trace_path), str(tmp_path), str(output_dir)
        )

    assert written == []


def test_generates_a_test_for_a_zero_argument_function(tmp_path):
    (tmp_path / "helper.py").write_text("def greet():\n    return 'hi'\n")
    trace_path = _trace(
        tmp_path,
        "from helper import greet\n"
        "\n"
        "\n"
        "def main():\n"
        "    greet()\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    with mock.patch.object(ai_generator, "_call_llm", return_value="greet()\n"):
        output_dir = tmp_path / "generated_tests"
        written = ai_generator.generate_ai_tests(
            str(trace_path), str(tmp_path), str(output_dir)
        )

    assert written == [output_dir / "test_helper_ai.py"]
    source = (output_dir / "test_helper_ai.py").read_text()
    assert "result = greet()" in source
    assert "assert result == 'hi'" in source


def test_does_not_send_source_code_to_the_llm(tmp_path):
    # Confirms the prompt includes the signature but NOT the function's
    # source code -- secrets in source text must never reach the LLM.
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

    captured_prompt = []

    def fake_call_llm(prompt, **kwargs):
        captured_prompt.append(prompt)
        return "add(a=0, b=0)\n"

    with mock.patch.object(ai_generator, "_call_llm", side_effect=fake_call_llm):
        output_dir = tmp_path / "generated_tests"
        ai_generator.generate_ai_tests(str(trace_path), str(tmp_path), str(output_dir))

    prompt = captured_prompt[0]
    assert "def add(a, b):" not in prompt
    assert "return a + b" not in prompt
    assert "Signature:" in prompt
    assert "(a, b)" in prompt


@pytest.mark.parametrize(
    "case_name, import_name, helper_source, call_expr, fake_response",
    [
        (
            "signature_default",
            "connect",
            "def connect(token='super-secret-value'):\n    return len(token)\n",
            "connect('super-secret-value')",
            "connect(token='x')\n",
        ),
        (
            "omitted_argument_default",
            "connect",
            "def connect(token='super-secret'):\n    return len(token)\n",
            "connect()",
            "connect(token='x')\n",
        ),
        (
            "echoed_back_through_return_value",
            "connect",
            "def connect(token='super-secret'):\n    return token\n",
            "connect()",
            "connect(token='x')\n",
        ),
        (
            "embedded_in_a_larger_return_value",
            "connect",
            "def connect(token='super-secret'):\n    return 'Bearer ' + token\n",
            "connect()",
            "connect(token='x')\n",
        ),
        (
            "nested_inside_a_container_default_value",
            "f",
            "def f(cfg={'token': 'super-secret'}):\n    return cfg['token']\n",
            "f()",
            "f(cfg={})\n",
        ),
        (
            "stored_as_a_container_default_key",
            "f",
            "def f(cfg={'super-secret': 1}):\n    return next(iter(cfg))\n",
            "f()",
            "f(cfg={})\n",
        ),
    ],
)
def test_does_not_send_a_default_value_to_the_llm(
    tmp_path, case_name, import_name, helper_source, call_expr, fake_response
):
    # A source-embedded default (token="super-secret") must never reach the
    # LLM prompt, no matter how it's exposed: the signature line, a recorded
    # call that omitted the argument, a return value that echoes it back
    # exactly or as a substring, or a container default's values/keys.
    (tmp_path / "helper.py").write_text(helper_source)
    trace_path = _trace(
        tmp_path,
        f"from helper import {import_name}\n"
        "\n"
        "\n"
        "def main():\n"
        f"    {call_expr}\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    captured_prompt = []

    def fake_call_llm(prompt, **kwargs):
        captured_prompt.append(prompt)
        return fake_response

    with mock.patch.object(ai_generator, "_call_llm", side_effect=fake_call_llm):
        output_dir = tmp_path / "generated_tests"
        ai_generator.generate_ai_tests(str(trace_path), str(tmp_path), str(output_dir))

    prompt = captured_prompt[0]
    assert "super-secret" not in prompt


def test_generates_a_passing_test_for_a_function_literally_named_result(tmp_path):
    (tmp_path / "helper.py").write_text("def result(x):\n    return x\n")
    trace_path = _trace(
        tmp_path,
        "from helper import result\n"
        "\n"
        "\n"
        "def main():\n"
        "    result(1)\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    with mock.patch.object(ai_generator, "_call_llm", return_value="result(x=2)\n"):
        output_dir = tmp_path / "generated_tests"
        written = ai_generator.generate_ai_tests(
            str(trace_path), str(tmp_path), str(output_dir)
        )

    assert written == [output_dir / "test_helper_ai.py"]
    source = (output_dir / "test_helper_ai.py").read_text()
    assert "_result = result(x=2)" in source
    assert "assert _result == 2" in source

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(output_dir / "test_helper_ai.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_skips_an_ai_proposed_input_whose_verified_result_is_not_renderable(tmp_path):
    (tmp_path / "helper.py").write_text(
        "def maybe_nan(x):\n    if x:\n        return float('nan')\n    return 1\n"
    )
    trace_path = _trace(
        tmp_path,
        "from helper import maybe_nan\n"
        "\n"
        "\n"
        "def main():\n"
        "    maybe_nan(False)\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    with mock.patch.object(
        ai_generator,
        "_call_llm",
        return_value="maybe_nan(x=True)\nmaybe_nan(x=False)\n",
    ):
        output_dir = tmp_path / "generated_tests"
        written = ai_generator.generate_ai_tests(
            str(trace_path), str(tmp_path), str(output_dir)
        )

    assert written == [output_dir / "test_helper_ai.py"]
    source = (output_dir / "test_helper_ai.py").read_text()
    assert "def test_maybe_nan_1" not in source
    assert "maybe_nan(x=False)" in source
    assert "assert result == 1" in source


@pytest.mark.parametrize(
    "case_name, helper_source, import_name, call_expr, fake_response",
    [
        (
            "repr_only_looks_like_a_literal",
            # Weird's __repr__ happens to look like a valid Python literal,
            # but the object itself isn't equal to that literal -- a naive
            # repr-round-trip check would accept it and generate a failing
            # test.
            (
                "class Weird:\n"
                "    def __repr__(self):\n"
                "        return '1'\n"
                "\n"
                "\n"
                "def make(x):\n"
                "    if x:\n"
                "        return Weird()\n"
                "    return 0\n"
            ),
            "make",
            "make(False)",
            "make(x=True)\n",
        ),
        (
            "verification_raises",
            # Exceptions aren't rendered as tests yet -- an input that
            # raises during verification is skipped, not turned into a
            # broken or misleading test.
            "def divide(a, b):\n    return a / b\n",
            "divide",
            "divide(10, 2)",
            "divide(a=10, b=0)\n",
        ),
        (
            "verification_causes_sys_exit",
            # SystemExit must still be caught gracefully (not propagate and
            # abort generation), even though it isn't rendered as a test
            # yet either.
            (
                "import sys\n"
                "\n"
                "\n"
                "def maybe_exit(x):\n"
                "    if x:\n"
                "        sys.exit(1)\n"
                "    return 1\n"
            ),
            "maybe_exit",
            "maybe_exit(False)",
            "maybe_exit(x=True)\n",
        ),
        (
            "unrenderable_argument",
            # ast.literal_eval happily parses "1e309" as float('inf'), a
            # real Python value -- but its repr ('inf') isn't valid literal
            # syntax, so it must be rejected before it's ever rendered into
            # a test.
            "def f(x):\n    return x\n",
            "f",
            "f(1)",
            "f(x=1e309)\n",
        ),
    ],
)
def test_skips_ai_proposed_calls_that_cannot_produce_a_safe_test(
    tmp_path, case_name, helper_source, import_name, call_expr, fake_response
):
    (tmp_path / "helper.py").write_text(helper_source)
    trace_path = _trace(
        tmp_path,
        f"from helper import {import_name}\n"
        "\n"
        "\n"
        "def main():\n"
        f"    {call_expr}\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    with mock.patch.object(ai_generator, "_call_llm", return_value=fake_response):
        output_dir = tmp_path / "generated_tests"
        written = ai_generator.generate_ai_tests(
            str(trace_path), str(tmp_path), str(output_dir)
        )

    assert written == []


def test_skips_entry_script_functions(tmp_path):
    # Entry-script ("__main__") functions aren't covered yet -- generate_ai_tests
    # doesn't take an entry_script argument at all, so they're skipped the
    # same way the deterministic generator skips them without one.
    trace_path = _trace(
        tmp_path,
        "def add(a, b):\n    return a + b\n"
        "\n"
        "\n"
        "def main():\n"
        "    add(1, 2)\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    with mock.patch.object(ai_generator, "_call_llm", return_value="add(a=0, b=0)\n"):
        output_dir = tmp_path / "generated_tests"
        written = ai_generator.generate_ai_tests(
            str(trace_path), str(tmp_path), str(output_dir)
        )

    assert written == []


def test_renders_tests_in_verification_order_not_alphabetically(tmp_path):
    # For a module with shared mutable state, an AI test was verified
    # against a real call made in a specific order -- rendering out of
    # that order (e.g. alphabetically) would no longer match the asserted
    # value.
    (tmp_path / "helper.py").write_text(
        "x = 0\n"
        "\n"
        "\n"
        "def b():\n"
        "    global x\n"
        "    x += 1\n"
        "    return x\n"
        "\n"
        "\n"
        "def a():\n"
        "    global x\n"
        "    x += 10\n"
        "    return x\n"
    )
    trace_path = _trace(
        tmp_path,
        "from helper import b, a\n"
        "\n"
        "\n"
        "def main():\n"
        "    b()\n"
        "    a()\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    def fake_call_llm(prompt, **kwargs):
        if "Function: b" in prompt:
            return "b()\n"
        if "Function: a" in prompt:
            return "a()\n"
        return ""

    with mock.patch.object(ai_generator, "_call_llm", side_effect=fake_call_llm):
        output_dir = tmp_path / "generated_tests"
        written = ai_generator.generate_ai_tests(
            str(trace_path), str(tmp_path), str(output_dir)
        )

    assert written == [output_dir / "test_helper_ai.py"]
    source = (output_dir / "test_helper_ai.py").read_text()
    assert source.index("def test_b_0") < source.index("def test_a_0")

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(output_dir / "test_helper_ai.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout


def test_renders_the_originally_proposed_input_even_if_the_function_mutates_it(
    tmp_path,
):
    # f mutates its list argument in place. Without a defensive copy before
    # the verification call, the rendered test would show the *mutated*
    # argument (what the AI proposed plus f's own mutation) instead of what
    # was actually proposed and used to compute the asserted result.
    (tmp_path / "helper.py").write_text(
        "def f(xs, mutate):\n    if mutate:\n        xs.append(1)\n    return len(xs)\n"
    )
    trace_path = _trace(
        tmp_path,
        "from helper import f\n"
        "\n"
        "\n"
        "def main():\n"
        "    f('ab', False)\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    with mock.patch.object(
        ai_generator, "_call_llm", return_value="f(xs=[], mutate=True)\n"
    ):
        output_dir = tmp_path / "generated_tests"
        written = ai_generator.generate_ai_tests(
            str(trace_path), str(tmp_path), str(output_dir)
        )

    assert written == [output_dir / "test_helper_ai.py"]
    source = (output_dir / "test_helper_ai.py").read_text()
    assert "f(xs=[], mutate=True)" in source
    assert "assert result == 1" in source

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(output_dir / "test_helper_ai.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_renders_the_originally_verified_result_even_if_a_later_call_mutates_it(
    tmp_path,
):
    # f returns a shared, module-level list it appends to. Without a
    # defensive copy when a verified result is accepted, an earlier test's
    # stored expected value would keep being the *same* list object, and a
    # later verification call mutating it in place would silently change
    # what the earlier test asserts.
    (tmp_path / "helper.py").write_text(
        "_shared = []\n"
        "\n"
        "\n"
        "def f(v, want_list):\n"
        "    _shared.append(v)\n"
        "    if want_list:\n"
        "        return _shared\n"
        "    return len(_shared)\n"
    )
    trace_path = _trace(
        tmp_path,
        "from helper import f\n"
        "\n"
        "\n"
        "def main():\n"
        "    f(0, False)\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )

    with mock.patch.object(
        ai_generator,
        "_call_llm",
        return_value="f(v=1, want_list=True)\nf(v=2, want_list=True)\n",
    ):
        output_dir = tmp_path / "generated_tests"
        written = ai_generator.generate_ai_tests(
            str(trace_path), str(tmp_path), str(output_dir)
        )

    assert written == [output_dir / "test_helper_ai.py"]
    source = (output_dir / "test_helper_ai.py").read_text()
    assert "assert result == [1]" in source
    assert "assert result == [1, 2]" in source

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(output_dir / "test_helper_ai.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout


def test_does_not_import_a_target_file_that_shadows_the_openai_package(tmp_path):
    # Through the real --ai CLI flow, target_dir ends up on sys.path
    # *twice* by the time the LLM is called: once left behind by run()
    # (which doesn't restore sys.path, so the target's own atexit handlers
    # still see it), and once inserted again by generate_ai_tests() itself.
    # A target program with its own openai.py must never be picked up in
    # place of the real installed SDK despite that.
    (tmp_path / "openai.py").write_text(
        "class OpenAI:\n"
        "    def __init__(self, **kwargs):\n"
        "        print('TARGET OPENAI INIT - this must never print')\n"
    )
    (tmp_path / "helper.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "program.py").write_text("from helper import add\n\n\nadd(1, 2)\n")

    result = subprocess.run(
        [sys.executable, "-m", "ai_tracer.cli", "--ai", str(tmp_path / "program.py")],
        capture_output=True,
        text=True,
        check=False,
    )

    assert "TARGET OPENAI INIT" not in result.stdout
    assert "TARGET OPENAI INIT" not in result.stderr
    # openai isn't installed in this environment either -- the real SDK's
    # own missing-package error is expected here, not a shadowed import.
    assert "pip install 'ai-tracer[ai]'" in result.stderr
