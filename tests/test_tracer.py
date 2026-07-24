import sys
import threading

from ai_tracer import tracer


def sample_function():
    return "done"


def test_start_stop_records_a_simple_call():
    tracer.start("tests")
    sample_function()
    calls = tracer.stop()

    assert calls == [{"qualname": "sample_function"}]


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
        {"qualname": "sample_caller"},
        {"qualname": "sample_function"},
    ]


def sample_recursive(n):
    if n == 0:
        return 0
    return sample_recursive(n - 1)


def test_records_each_recursive_call_separately():
    tracer.start("tests")
    sample_recursive(2)
    calls = tracer.stop()

    assert calls == [{"qualname": "sample_recursive"}] * 3


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

    assert calls == [
        {"qualname": "sample_function"},
        {
            "qualname": "test_does_not_record_class_body_execution.<locals>.SampleClass.method"
        },
    ]


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

    assert calls == [{"qualname": "sample_function"}]


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

    assert calls == [{"qualname": "sample_function_with_a_comprehension"}]


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
