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


def _outer_and_inner_calls(trace_path):
    calls = json.loads(trace_path.read_text())
    outer_call = next(c for c in calls if c["qualname"] == "outer")
    children = generator._children_by_call_id(calls)[outer_call["call_id"]]
    assert len(children) == 1
    return outer_call, children[0]


def _render_mocked_test(tmp_path, outer_call, inner_call):
    # Hand-assembled the same way generate() will eventually do it (M4) -
    # mocker.py itself only renders the patch block, not a whole file.
    lines = [
        "import sys",
        mocker.render_import_line(),
        "",
        f"sys.path.insert(0, {str(tmp_path)!r})",
        "from helper import outer",
        "",
        "",
        "def test_outer_mocked():",
        f"    {mocker.render_patch_line('helper', inner_call, 'mocked_inner')}",
        f"        result = outer(x={outer_call['args']['x']!r})",
        f"        assert result == {outer_call['return_value']!r}",
    ]
    return "\n".join(lines) + "\n"


def test_render_patch_line_produces_a_passing_test_for_a_single_child_call(tmp_path):
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
    assert inner_call["qualname"] == "inner"
    assert inner_call["return_value"] == 5
    assert outer_call["return_value"] == 50

    test_dir = tmp_path / "generated"
    test_dir.mkdir()
    (test_dir / "test_outer.py").write_text(
        _render_mocked_test(tmp_path, outer_call, inner_call)
    )

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(test_dir)],
        capture_output=True,
        text=True,
        check=False,
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

    test_dir = tmp_path / "generated"
    test_dir.mkdir()
    (test_dir / "test_outer.py").write_text(
        _render_mocked_test(tmp_path, outer_call, inner_call)
    )

    # A clearly different length from the traced version, so Python can't
    # reuse a same-second, same-size stale .pyc instead of the new source.
    (tmp_path / "util.py").write_text(
        "def inner(x):\n    raise RuntimeError('the real inner ran')\n"
    )

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(test_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_mock_target_names_the_child_under_the_parents_own_module(tmp_path):
    inner_call = {"qualname": "inner"}
    assert mocker.mock_target("helper", inner_call) == "helper.inner"


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

    test_dir = tmp_path / "generated"
    test_dir.mkdir()
    (test_dir / "test_outer.py").write_text(
        _render_mocked_test(tmp_path, outer_call, inner_call)
    )

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(test_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "AttributeError" in result.stdout
