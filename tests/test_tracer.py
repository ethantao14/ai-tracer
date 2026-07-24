import sys
import threading

from ai_tracer import tracer


def sample_function():
    return "done"


def test_start_stop_records_a_simple_call():
    tracer.start("tests")
    sample_function()
    calls = tracer.stop()

    assert calls == [{"qualname": "sample_function", "args": {}}]


def test_ignores_calls_outside_target_dir(tmp_path):
    tracer.start(str(tmp_path))
    sample_function()
    calls = tracer.stop()

    assert calls == []


def sample_caller():
    return sample_function()


def test_records_nested_calls_in_call_order():
    tracer.start("tests")
    sample_caller()
    calls = tracer.stop()

    assert calls == [
        {"qualname": "sample_caller", "args": {}},
        {"qualname": "sample_function", "args": {}},
    ]


def sample_recursive(n):
    if n == 0:
        return 0
    return sample_recursive(n - 1)


def test_records_each_recursive_call_separately():
    tracer.start("tests")
    sample_recursive(2)
    calls = tracer.stop()

    assert calls == [
        {"qualname": "sample_recursive", "args": {"n": 2}},
        {"qualname": "sample_recursive", "args": {"n": 1}},
        {"qualname": "sample_recursive", "args": {"n": 0}},
    ]


def test_does_not_record_class_body_execution():
    # A class statement is unrelated to any prior "call" filtering; run it
    # only after start() so its body executes while tracing is active.
    tracer.start("tests")

    class SampleClass:
        CLASS_LEVEL = sample_function()

        def method(self):
            return "called"

    SampleClass().method()
    calls = tracer.stop()

    # `self` isn't JSON-serializable, so it falls back to repr(), whose
    # exact text (memory address) isn't worth pinning down here.
    assert calls[0] == {"qualname": "sample_function", "args": {}}
    assert calls[1]["qualname"] == (
        "test_does_not_record_class_body_execution.<locals>.SampleClass.method"
    )
    assert isinstance(calls[1]["args"]["self"], str)


def test_stop_restores_a_previously_active_tracer():
    def other_tracer(frame, event, arg):
        return other_tracer

    sys.settrace(other_tracer)
    try:
        tracer.start("tests")
        sample_function()
        tracer.stop()

        assert sys.gettrace() is other_tracer
    finally:
        sys.settrace(None)


def test_stop_restores_no_tracer_when_none_was_active():
    sys.settrace(None)
    tracer.start("tests")
    sample_function()
    tracer.stop()

    assert sys.gettrace() is None


def test_records_calls_made_on_a_worker_thread():
    tracer.start("tests")
    thread = threading.Thread(target=sample_function)
    thread.start()
    thread.join()
    calls = tracer.stop()

    assert calls == [{"qualname": "sample_function", "args": {}}]


def test_stop_restores_the_previous_thread_tracer():
    def other_tracer(frame, event, arg):
        return other_tracer

    threading.settrace(other_tracer)
    try:
        tracer.start("tests")
        sample_function()
        tracer.stop()

        assert threading.gettrace() is other_tracer
    finally:
        threading.settrace(None)


def sample_function_with_a_comprehension():
    return [x * 2 for x in range(3)]


def test_does_not_record_comprehension_frames():
    tracer.start("tests")
    sample_function_with_a_comprehension()
    calls = tracer.stop()

    assert calls == [{"qualname": "sample_function_with_a_comprehension", "args": {}}]


def test_start_does_not_blind_a_previously_active_tracer():
    seen = []

    def other_tracer(frame, event, arg):
        if event == "call":
            seen.append(frame.f_code.co_name)
        return other_tracer

    sys.settrace(other_tracer)
    try:
        tracer.start("tests")
        sample_function()
        tracer.stop()
    finally:
        sys.settrace(None)

    assert "sample_function" in seen


def test_start_does_not_blind_a_previously_active_thread_tracer():
    seen = []

    def other_tracer(frame, event, arg):
        if event == "call":
            seen.append(frame.f_code.co_name)
        return other_tracer

    threading.settrace(other_tracer)
    try:
        tracer.start("tests")
        thread = threading.Thread(target=sample_function)
        thread.start()
        thread.join()
        tracer.stop()
    finally:
        threading.settrace(None)

    assert "sample_function" in seen


def sample_function_with_positional_and_default_args(a, b, c=3):
    return a + b + c


def test_records_positional_and_default_args():
    tracer.start("tests")
    sample_function_with_positional_and_default_args(1, 2)
    calls = tracer.stop()

    assert calls == [
        {
            "qualname": "sample_function_with_positional_and_default_args",
            "args": {"a": 1, "b": 2, "c": 3},
        }
    ]


def sample_function_with_varargs(*args, **kwargs):
    return args, kwargs


def test_records_varargs_and_kwargs_already_collected():
    tracer.start("tests")
    sample_function_with_varargs(1, 2, x=3)
    calls = tracer.stop()

    assert calls == [
        {
            "qualname": "sample_function_with_varargs",
            "args": {"args": [1, 2], "kwargs": {"x": 3}},
        }
    ]


def sample_function_with_a_mutable_arg(values):
    values.append("mutated")
    return values


def test_records_the_arg_value_as_of_the_call_not_after_mutation():
    tracer.start("tests")
    sample_function_with_a_mutable_arg([1, 2])
    calls = tracer.stop()

    assert calls == [
        {"qualname": "sample_function_with_a_mutable_arg", "args": {"values": [1, 2]}}
    ]


class _NotJSONSerializable:
    pass


def sample_function_with_a_non_serializable_arg(value):
    return value


def test_falls_back_to_repr_for_a_non_json_serializable_arg():
    obj = _NotJSONSerializable()

    tracer.start("tests")
    sample_function_with_a_non_serializable_arg(obj)
    calls = tracer.stop()

    assert calls == [
        {
            "qualname": "sample_function_with_a_non_serializable_arg",
            "args": {"value": repr(obj)},
        }
    ]
