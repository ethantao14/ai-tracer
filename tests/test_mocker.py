import json
import subprocess
import sys

from ai_tracer import generator, mocker


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


def _exception_reference(call):
    # Only builtins are exercised here - the full module/alias-aware
    # reference (matching generator.py's own) is wired in once mocker.py is
    # actually plugged into generate() (M4).
    assert call["exception_module"] == "builtins"
    return call["exception_type"]


def _outer_and_children(trace_path):
    calls = json.loads(trace_path.read_text())
    outer_call = next(c for c in calls if c["qualname"] == "outer")
    children = generator._children_by_call_id(calls)[outer_call["call_id"]]
    return outer_call, children


def _outer_and_inner_calls(trace_path):
    outer_call, children = _outer_and_children(trace_path)
    assert len(children) == 1
    return outer_call, children[0]


def _render_mocked_test(tmp_path, statement, body_lines):
    # Hand-assembled the same way generate() will eventually do it (M4) -
    # mocker.py itself only renders the patch statement, not a whole file.
    lines = [
        "import sys",
        mocker.render_import_line(),
        "",
        f"sys.path.insert(0, {str(tmp_path)!r})",
        "from helper import outer",
        "",
        "",
        "def test_outer_mocked():",
        f"    {statement}",
    ]
    lines += [f"        {line}" for line in body_lines]
    return "\n".join(lines) + "\n"


def _run(test_dir, source):
    test_dir.mkdir(exist_ok=True)
    (test_dir / "test_outer.py").write_text(source)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(test_dir)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_single_non_raised_call_uses_return_value(tmp_path):
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
    outer_call, inner_call = _outer_and_inner_calls(trace_path)
    assert inner_call["return_value"] == 5
    assert outer_call["return_value"] == 50

    clause = mocker.render_patch_clause(
        "helper", [inner_call], "mocked_inner", _exception_reference
    )
    assert clause == "mock.patch('helper.inner', return_value=5) as mocked_inner"
    statement = mocker.render_patch_statement([clause])

    result = _run(
        tmp_path / "generated",
        _render_mocked_test(
            tmp_path,
            statement,
            [
                f"result = outer(x={outer_call['args']['x']!r})",
                f"assert result == {outer_call['return_value']!r}",
            ],
        ),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_the_mocked_child_never_actually_runs(tmp_path):
    # The real proof the patch achieves isolation: break `inner`'s real
    # implementation after tracing, and confirm the same rendered test
    # still passes - it can only do that if `inner` never actually ran.
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
    outer_call, inner_call = _outer_and_inner_calls(trace_path)
    clause = mocker.render_patch_clause(
        "helper", [inner_call], "mocked_inner", _exception_reference
    )
    statement = mocker.render_patch_statement([clause])
    source = _render_mocked_test(
        tmp_path,
        statement,
        [
            f"result = outer(x={outer_call['args']['x']!r})",
            f"assert result == {outer_call['return_value']!r}",
        ],
    )

    # A clearly different length from the traced version, so Python can't
    # reuse a same-second, same-size stale .pyc instead of the new source.
    (tmp_path / "util.py").write_text(
        "def inner(x):\n    raise RuntimeError('the real inner ran')\n"
    )

    result = _run(tmp_path / "generated", source)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_mock_target_names_the_child_under_the_parents_own_module():
    assert mocker.mock_target("helper", [{"qualname": "inner"}]) == "helper.inner"


def test_patch_at_the_callers_module_fails_loudly_for_module_attribute_access(
    tmp_path,
):
    # Documents the accepted failure mode: if the real caller reached the
    # child via `import util; util.inner(...)` rather than a plain
    # `from util import inner`, the calling module has no `inner` attribute
    # of its own, so mock.patch() raises AttributeError immediately - a
    # visibly failing generated test, not a silent isolation gap.
    (tmp_path / "util.py").write_text("def inner(x):\n    return x + 1\n")
    (tmp_path / "helper.py").write_text(
        "import util\n\n\ndef outer(x):\n    return util.inner(x) * 10\n"
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
    outer_call, inner_call = _outer_and_inner_calls(trace_path)
    clause = mocker.render_patch_clause(
        "helper", [inner_call], "mocked_inner", _exception_reference
    )
    statement = mocker.render_patch_statement([clause])

    result = _run(
        tmp_path / "generated",
        _render_mocked_test(
            tmp_path,
            statement,
            [
                f"result = outer(x={outer_call['args']['x']!r})",
                f"assert result == {outer_call['return_value']!r}",
            ],
        ),
    )
    assert result.returncode != 0
    assert "AttributeError" in result.stdout


def test_repeated_calls_use_a_side_effect_list_in_traced_order(tmp_path):
    (tmp_path / "util.py").write_text("def inner(x):\n    return x + 1\n")
    (tmp_path / "helper.py").write_text(
        "from util import inner\n\n\ndef outer(a, b):\n    return inner(a) + inner(b)\n"
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
    outer_call, children = _outer_and_children(trace_path)
    assert [c["return_value"] for c in children] == [5, 11]

    clause = mocker.render_patch_clause(
        "helper", children, "mocked_inner", _exception_reference
    )
    assert "side_effect=[5, 11]" in clause
    statement = mocker.render_patch_statement([clause])
    source = _render_mocked_test(
        tmp_path,
        statement,
        [
            f"result = outer(a={outer_call['args']['a']!r}, b={outer_call['args']['b']!r})",
            f"assert result == {outer_call['return_value']!r}",
        ],
    )

    result = _run(tmp_path / "generated", source)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout

    # Isolation proof: break the real inner() and confirm the same rendered
    # test still passes, and still uses each recorded outcome in order.
    (tmp_path / "util.py").write_text(
        "def inner(x):\n    raise RuntimeError('the real inner ran')\n"
    )
    result = _run(tmp_path / "generated", source)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_repeated_calls_mix_raised_and_returned_outcomes(tmp_path):
    (tmp_path / "util.py").write_text(
        "def inner(x):\n"
        "    if x < 0:\n"
        "        raise ValueError('neg')\n"
        "    return x + 1\n"
    )
    (tmp_path / "helper.py").write_text(
        "from util import inner\n"
        "\n"
        "\n"
        "def outer(a, b):\n"
        "    try:\n"
        "        first = inner(a)\n"
        "    except ValueError:\n"
        "        first = -1\n"
        "    return first + inner(b)\n"
    )
    trace_path = _trace(
        tmp_path,
        "from helper import outer\n"
        "\n"
        "\n"
        "def main():\n"
        "    outer(-1, 10)\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )
    outer_call, children = _outer_and_children(trace_path)
    assert [c["raised"] for c in children] == [True, False]

    clause = mocker.render_patch_clause(
        "helper", children, "mocked_inner", _exception_reference
    )
    assert "side_effect=[ValueError, 11]" in clause
    statement = mocker.render_patch_statement([clause])
    source = _render_mocked_test(
        tmp_path,
        statement,
        [
            f"result = outer(a={outer_call['args']['a']!r}, b={outer_call['args']['b']!r})",
            f"assert result == {outer_call['return_value']!r}",
        ],
    )

    result = _run(tmp_path / "generated", source)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_a_single_raised_call_uses_a_bare_side_effect(tmp_path):
    (tmp_path / "util.py").write_text("def inner(x):\n    raise ValueError('boom')\n")
    (tmp_path / "helper.py").write_text(
        "from util import inner\n"
        "\n"
        "\n"
        "def outer(x):\n"
        "    try:\n"
        "        inner(x)\n"
        "    except ValueError:\n"
        "        return -1\n"
    )
    trace_path = _trace(
        tmp_path,
        "from helper import outer\n"
        "\n"
        "\n"
        "def main():\n"
        "    outer(1)\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )
    outer_call, inner_call = _outer_and_inner_calls(trace_path)
    assert inner_call["raised"] is True

    clause = mocker.render_patch_clause(
        "helper", [inner_call], "mocked_inner", _exception_reference
    )
    # A bare exception class, not a one-item list - unittest.mock treats a
    # class in side_effect the same way either way, but the list form would
    # misleadingly suggest a repeated call.
    assert "side_effect=ValueError" in clause
    assert "side_effect=[ValueError]" not in clause
    statement = mocker.render_patch_statement([clause])
    source = _render_mocked_test(
        tmp_path,
        statement,
        [
            f"result = outer(x={outer_call['args']['x']!r})",
            f"assert result == {outer_call['return_value']!r}",
        ],
    )

    result = _run(tmp_path / "generated", source)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_multiple_distinct_children_get_separate_patches_in_one_statement(tmp_path):
    (tmp_path / "util.py").write_text(
        "def inner(x):\n    return x + 1\n\n\ndef extra(x):\n    return x * 2\n"
    )
    (tmp_path / "helper.py").write_text(
        "from util import inner, extra\n\n\ndef outer(x):\n    return inner(x) + extra(x)\n"
    )
    trace_path = _trace(
        tmp_path,
        "from helper import outer\n"
        "\n"
        "\n"
        "def main():\n"
        "    outer(3)\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )
    outer_call, children = _outer_and_children(trace_path)
    assert {c["qualname"] for c in children} == {"inner", "extra"}

    clauses = [
        mocker.render_patch_clause(
            "helper", [child], f"mocked_{child['qualname']}", _exception_reference
        )
        for child in children
    ]
    statement = mocker.render_patch_statement(clauses)
    assert statement.count("mock.patch(") == 2
    source = _render_mocked_test(
        tmp_path,
        statement,
        [
            f"result = outer(x={outer_call['args']['x']!r})",
            f"assert result == {outer_call['return_value']!r}",
        ],
    )

    result = _run(tmp_path / "generated", source)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout

    # Isolation proof: break both real implementations, confirm still passes.
    (tmp_path / "util.py").write_text(
        "def inner(x):\n    raise RuntimeError('the real inner ran')\n\n\n"
        "def extra(x):\n    raise RuntimeError('the real extra ran')\n"
    )
    result = _run(tmp_path / "generated", source)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
