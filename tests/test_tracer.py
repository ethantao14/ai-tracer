import math
import sys
import threading
import time

from ai_tracer import tracer


def _only_qualname_and_args(record):
    # Most of these tests are about qualname/args specifically, not the
    # call tree (call_id/parent_call_id) or module resolution - an allowlist
    # keeps them decoupled from those concerns as more fields get added.
    return {"qualname": record["qualname"], "args": record["args"]}


def sample_function():
    return "done"


def test_start_stop_records_a_simple_call():
    tracer.start("tests")
    sample_function()
    calls = tracer.stop()

    assert [_only_qualname_and_args(c) for c in calls] == [
        {"qualname": "sample_function", "args": {}}
    ]


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

    assert [_only_qualname_and_args(c) for c in calls] == [
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

    assert [_only_qualname_and_args(c) for c in calls] == [
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
    assert _only_qualname_and_args(calls[0]) == {
        "qualname": "sample_function",
        "args": {},
    }
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

    assert [_only_qualname_and_args(c) for c in calls] == [
        {"qualname": "sample_function", "args": {}}
    ]


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

    assert [_only_qualname_and_args(c) for c in calls] == [
        {"qualname": "sample_function_with_a_comprehension", "args": {}}
    ]


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

    assert [_only_qualname_and_args(c) for c in calls] == [
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

    assert [_only_qualname_and_args(c) for c in calls] == [
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

    assert [_only_qualname_and_args(c) for c in calls] == [
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

    assert [_only_qualname_and_args(c) for c in calls] == [
        {
            "qualname": "sample_function_with_a_non_serializable_arg",
            "args": {"value": repr(obj)},
        }
    ]


def sample_function_with_two_args(a, b):
    return a, b


def test_tags_each_arg_with_its_serialization_kind():
    obj = _NotJSONSerializable()

    tracer.start("tests")
    sample_function_with_two_args(1, obj)
    calls = tracer.stop()

    assert calls[0]["arg_serialization"] == {"a": "json", "b": "repr"}


def sample_function_with_a_circular_arg(value):
    return value


def test_falls_back_to_repr_for_a_circular_container_arg():
    # A circular list's own __repr__ would recurse into every element too
    # (list.__repr__ calls repr() on each item), so even for an exact-type
    # list this is bypassed via object.__repr__, not repr(circular).
    circular = []
    circular.append(circular)

    tracer.start("tests")
    sample_function_with_a_circular_arg(circular)
    calls = tracer.stop()

    assert [_only_qualname_and_args(c) for c in calls] == [
        {
            "qualname": "sample_function_with_a_circular_arg",
            "args": {"value": object.__repr__(circular)},
        }
    ]


class _RaisesDuringIteration(list):
    def __iter__(self):
        raise RuntimeError("broken iter")


def sample_function_with_an_arg_that_breaks_json_encoding(value):
    return value


def test_falls_back_to_repr_for_a_list_subclass_with_a_raising_iter():
    # A list/dict subclass is never introspected directly (see
    # _to_json_safe), and its repr isn't trusted either (it could itself be
    # overridden), so this uses object.__repr__ without ever calling the
    # subclass's own __iter__ or __repr__.
    broken = _RaisesDuringIteration([1, 2])

    tracer.start("tests")
    sample_function_with_an_arg_that_breaks_json_encoding(broken)
    calls = tracer.stop()

    assert [_only_qualname_and_args(c) for c in calls] == [
        {
            "qualname": "sample_function_with_an_arg_that_breaks_json_encoding",
            "args": {"value": object.__repr__(broken)},
        }
    ]


class _MutatesSelfOnIteration(list):
    def __iter__(self):
        self.clear()
        return super().__iter__()


def sample_function_with_a_side_effecting_arg(value):
    return value


def test_snapshotting_a_side_effecting_container_subclass_does_not_mutate_it():
    # A naive json.dumps(value) would call this subclass's own __iter__,
    # mutating the target's actual argument as a side effect of merely being
    # traced. The snapshot must never touch a subclass's overridable methods.
    tricky = _MutatesSelfOnIteration([1, 2, 3])

    tracer.start("tests")
    sample_function_with_a_side_effecting_arg(tricky)
    calls = tracer.stop()

    assert tricky == [1, 2, 3]
    assert [_only_qualname_and_args(c) for c in calls] == [
        {
            "qualname": "sample_function_with_a_side_effecting_arg",
            "args": {"value": object.__repr__(tricky)},
        }
    ]


def sample_function_with_a_non_finite_float_arg(value):
    return value


def test_falls_back_to_repr_for_nan_and_infinity():
    tracer.start("tests")
    sample_function_with_a_non_finite_float_arg(math.nan)
    sample_function_with_a_non_finite_float_arg(math.inf)
    calls = tracer.stop()

    assert [_only_qualname_and_args(c) for c in calls] == [
        {
            "qualname": "sample_function_with_a_non_finite_float_arg",
            "args": {"value": "nan"},
        },
        {
            "qualname": "sample_function_with_a_non_finite_float_arg",
            "args": {"value": "inf"},
        },
    ]


def sample_function_with_a_huge_int_arg(value):
    return value


def test_falls_back_to_a_safe_placeholder_for_an_int_too_large_to_stringify():
    # Past sys.get_int_max_str_digits() (default 4300 digits), even str()
    # and repr() raise ValueError - the same limit json.dumps hits, which
    # is exactly why this can't be left for the final write in cli.py to
    # discover.
    huge = 10**5000

    tracer.start("tests")
    sample_function_with_a_huge_int_arg(huge)
    calls = tracer.stop()

    assert [_only_qualname_and_args(c) for c in calls] == [
        {
            "qualname": "sample_function_with_a_huge_int_arg",
            "args": {"value": object.__repr__(huge)},
        }
    ]


def sample_function_with_a_custom_metaclass_arg(value):
    return value


def test_snapshotting_never_calls_a_custom_metaclass_eq():
    # `value_type in _JSON_SAFE_SCALAR_TYPES` would compare with ==, which
    # for a class object with a custom metaclass __eq__ calls that
    # target-defined method - even though value_type is never actually one
    # of those safe types, so this comparison serves no purpose worth that
    # risk.
    log = []

    class SideEffectingMeta(type):
        def __eq__(cls, other):
            log.append(("eq", other))
            return False

        def __hash__(cls):
            return object.__hash__(cls)

    class WithCustomMetaclass(metaclass=SideEffectingMeta):
        pass

    obj = WithCustomMetaclass()

    tracer.start("tests")
    sample_function_with_a_custom_metaclass_arg(obj)
    calls = tracer.stop()

    assert log == []
    assert [_only_qualname_and_args(c) for c in calls] == [
        {
            "qualname": "sample_function_with_a_custom_metaclass_arg",
            "args": {"value": object.__repr__(obj)},
        }
    ]


class _RaisesOnRepr:
    def __repr__(self):
        raise RuntimeError("broken repr")


def sample_function_with_a_broken_repr_arg(value):
    return value


def test_never_calls_a_raising_repr_on_a_target_defined_object():
    obj = _RaisesOnRepr()

    tracer.start("tests")
    sample_function_with_a_broken_repr_arg(obj)
    calls = tracer.stop()

    assert [_only_qualname_and_args(c) for c in calls] == [
        {
            "qualname": "sample_function_with_a_broken_repr_arg",
            "args": {"value": object.__repr__(obj)},
        }
    ]


class _SideEffectingRepr:
    def __init__(self, log):
        self._log = log

    def __repr__(self):
        self._log.append("repr called")
        return "SideEffectingRepr()"


def sample_function_with_a_side_effecting_repr_arg(value):
    return value


def test_snapshotting_never_calls_a_side_effecting_repr():
    # A target's own __repr__ is arbitrary code (mutation, I/O); calling it
    # just to represent an argument would run that code before the traced
    # function body even executes, changing when the target's own side
    # effects happen.
    log = []
    obj = _SideEffectingRepr(log)

    tracer.start("tests")
    sample_function_with_a_side_effecting_repr_arg(obj)
    calls = tracer.stop()

    assert log == []
    assert [_only_qualname_and_args(c) for c in calls] == [
        {
            "qualname": "sample_function_with_a_side_effecting_repr_arg",
            "args": {"value": object.__repr__(obj)},
        }
    ]


class _SideEffectingReprKey:
    def __init__(self, log):
        self._log = log

    def __repr__(self):
        self._log.append("key repr called")
        return "SideEffectingReprKey()"

    def __hash__(self):
        return 0


def sample_function_with_a_non_string_dict_key_arg(value):
    return value


def test_rejecting_a_non_string_dict_key_never_calls_its_repr():
    # The TypeError raised for a non-string key must not build a message
    # via repr(key): that would call the key's own __repr__ just to
    # construct an error that gets immediately discarded.
    log = []
    key = _SideEffectingReprKey(log)
    arg = {key: 1}

    tracer.start("tests")
    sample_function_with_a_non_string_dict_key_arg(arg)
    calls = tracer.stop()

    assert log == []
    assert [_only_qualname_and_args(c) for c in calls] == [
        {
            "qualname": "sample_function_with_a_non_string_dict_key_arg",
            "args": {"value": object.__repr__(arg)},
        }
    ]


def sample_generator_that_deletes_its_own_argument(x):
    yield 1
    del x
    yield 2
    yield 3


def test_tolerates_a_generator_deleting_its_own_argument_before_a_resume():
    # A generator/coroutine frame fires another "call" event on each
    # resume, not just when first entered. If the function already deleted
    # one of its own parameters by then, it's simply missing from f_locals
    # on that later "call" event, not a bug the tracer should crash on.
    tracer.start("tests")
    gen = sample_generator_that_deletes_its_own_argument(5)
    next(gen)
    next(gen)
    next(gen)
    calls = tracer.stop()

    assert [_only_qualname_and_args(c) for c in calls] == [
        {
            "qualname": "sample_generator_that_deletes_its_own_argument",
            "args": {"x": 5},
        },
        {
            "qualname": "sample_generator_that_deletes_its_own_argument",
            "args": {"x": 5},
        },
        {"qualname": "sample_generator_that_deletes_its_own_argument", "args": {}},
    ]


def sample_outer_with_a_closure(secret):
    unused_local = "not passed to inner"  # noqa: F841

    def sample_inner(a):
        return a + secret

    return sample_inner(3)


def test_does_not_record_closed_over_free_variables_as_args():
    tracer.start("tests")
    sample_outer_with_a_closure(2)
    calls = tracer.stop()

    assert [_only_qualname_and_args(c) for c in calls] == [
        {"qualname": "sample_outer_with_a_closure", "args": {"secret": 2}},
        {
            "qualname": "sample_outer_with_a_closure.<locals>.sample_inner",
            "args": {"a": 3},
        },
    ]


def test_a_top_level_call_has_no_parent():
    tracer.start("tests")
    sample_function()
    calls = tracer.stop()

    assert calls == [
        {
            "call_id": 0,
            "parent_call_id": None,
            "module": "test_tracer",
            "qualname": "sample_function",
            "args": {},
            "arg_serialization": {},
            "raised": False,
            "return_value": "done",
            "return_serialization": "json",
        }
    ]


def test_falls_back_to_repr_for_a_non_json_safe_module_name():
    # A target can rebind its own module-level __name__ to anything; that
    # value must go through the same snapshotting as args, or a target
    # doing this breaks json.dumps() for the whole trace, not just this call.
    def sample_function_for_module_repr_test():
        pass

    original_name = globals()["__name__"]
    fake_name = object()
    globals()["__name__"] = fake_name
    try:
        tracer.start("tests")
        sample_function_for_module_repr_test()
        calls = tracer.stop()
    finally:
        globals()["__name__"] = original_name

    assert calls[0]["module"] == object.__repr__(fake_name)


def test_a_nested_call_has_its_caller_as_parent():
    tracer.start("tests")
    sample_caller()
    calls = tracer.stop()

    assert [(c["call_id"], c["parent_call_id"], c["qualname"]) for c in calls] == [
        (0, None, "sample_caller"),
        (1, 0, "sample_function"),
    ]


def test_recursive_calls_form_a_chain_of_parents():
    tracer.start("tests")
    sample_recursive(2)
    calls = tracer.stop()

    assert [(c["call_id"], c["parent_call_id"]) for c in calls] == [
        (0, None),
        (1, 0),
        (2, 1),
    ]


def sample_calls_two_children():
    sample_function()
    sample_caller()


def test_sibling_calls_share_the_same_parent():
    tracer.start("tests")
    sample_calls_two_children()
    calls = tracer.stop()

    # In call order: [0] sample_calls_two_children (top), [1] sample_function
    # (direct child), [2] sample_caller (direct child), [3] sample_function
    # again (nested inside sample_caller - not a sibling of [1]).
    assert calls[0]["qualname"] == "sample_calls_two_children"
    assert calls[0]["parent_call_id"] is None
    top_id = calls[0]["call_id"]

    assert calls[1]["qualname"] == "sample_function"
    assert calls[1]["parent_call_id"] == top_id

    assert calls[2]["qualname"] == "sample_caller"
    assert calls[2]["parent_call_id"] == top_id

    assert calls[3]["qualname"] == "sample_function"
    assert calls[3]["parent_call_id"] == calls[2]["call_id"]


def test_call_ids_are_unique_across_a_run():
    tracer.start("tests")
    sample_function()
    sample_function()
    sample_caller()
    calls = tracer.stop()

    call_ids = [c["call_id"] for c in calls]
    assert len(call_ids) == len(set(call_ids))


def test_call_id_counter_resets_on_each_start():
    tracer.start("tests")
    sample_function()
    tracer.stop()

    tracer.start("tests")
    sample_function()
    calls = tracer.stop()

    assert calls[0]["call_id"] == 0
    assert calls[0]["parent_call_id"] is None


def test_worker_thread_calls_get_their_own_independent_tree():
    tracer.start("tests")
    sample_function()
    thread = threading.Thread(target=sample_caller)
    thread.start()
    thread.join()
    calls = tracer.stop()

    main_call = next(
        c
        for c in calls
        if c["qualname"] == "sample_function" and c["parent_call_id"] is None
    )
    caller_call = next(c for c in calls if c["qualname"] == "sample_caller")
    nested_call = next(
        c
        for c in calls
        if c["qualname"] == "sample_function" and c["call_id"] != main_call["call_id"]
    )

    assert main_call["parent_call_id"] is None
    # The worker thread's own top-level call has no parent, even though the
    # thread itself was started from within a traced call on the main
    # thread - cross-thread attribution is deliberately out of scope.
    assert caller_call["parent_call_id"] is None
    assert nested_call["parent_call_id"] == caller_call["call_id"]


def sample_function_that_echoes(value):
    return value


def test_return_values_are_attributed_correctly_under_thread_interleaving():
    # A call_id is assigned (via the thread-safe counter) before its record
    # is appended to _calls, so two threads can both obtain a call_id before
    # either appends - call_id is not reliably that record's index into the
    # shared list once threads race. Forces exactly that interleaving: both
    # threads' module-name snapshot (the same value, "test_tracer", for
    # both) rendezvous on a barrier, then thread A sleeps a little longer
    # before its own record actually gets appended, so thread B's record
    # lands at a lower list index than thread A's despite thread A having
    # called _trace_calls (and obtained its call_id) first.
    barrier = threading.Barrier(2)
    original_snapshot = tracer._snapshot

    def rendezvousing_snapshot(value):
        if value == "test_tracer":
            barrier.wait(timeout=2)
            if threading.current_thread().name == "A":
                time.sleep(0.05)
        return original_snapshot(value)

    tracer._snapshot = rendezvousing_snapshot
    try:
        tracer.start("tests")
        thread_a = threading.Thread(
            target=sample_function_that_echoes, args=("A",), name="A"
        )
        thread_b = threading.Thread(
            target=sample_function_that_echoes, args=("B",), name="B"
        )
        thread_a.start()
        thread_b.start()
        thread_a.join()
        thread_b.join()
        calls = tracer.stop()
    finally:
        tracer._snapshot = original_snapshot

    assert len(calls) == 2
    for call in calls:
        assert call["return_value"] == call["args"]["value"]


def test_generator_resumes_are_recorded_as_separate_sibling_calls():
    def sample_generator():
        yield 1
        yield 2

    def sample_driver():
        # Fully exhausting the generator (not just retrieving every yielded
        # value) avoids relying on garbage-collection timing: an abandoned,
        # not-yet-exhausted generator gets an extra "call" event whenever
        # Python happens to close() it during GC, which isn't deterministic
        # across Python versions/builds - list() drains it via repeated
        # next() until it raises StopIteration on its own, with no dangling
        # suspended state left for GC to close later.
        list(sample_generator())

    tracer.start("tests")
    sample_driver()
    calls = tracer.stop()

    driver_call_id = calls[0]["call_id"]
    generator_calls = [c for c in calls if "sample_generator" in c["qualname"]]
    # 2 yields, plus one final resume that runs off the end and raises
    # StopIteration.
    assert len(generator_calls) == 3
    # Every resume is parented directly to the driver that resumed it, not
    # to each other - each resume is its own call/return pair.
    assert all(c["parent_call_id"] == driver_call_id for c in generator_calls)
    call_ids = {c["call_id"] for c in generator_calls}
    assert len(call_ids) == 3


def test_start_does_not_blind_a_previously_active_tracer_across_return_events():
    # _make_local_tracer now stays the local tracer for a frame's whole
    # lifetime (it needs its own "return" event), unlike PR2's full
    # hand-off. A previously-active tracer must still see every event it
    # would have, including "return", not just "call".
    seen = []

    def other_tracer(frame, event, arg):
        if frame.f_code.co_name == "sample_function":
            seen.append(event)
        return other_tracer

    sys.settrace(other_tracer)
    try:
        tracer.start("tests")
        sample_function()
        tracer.stop()
    finally:
        sys.settrace(None)

    assert "call" in seen
    assert "return" in seen


def sample_function_outside_target_dir():
    return sys._getframe().f_trace


def test_installs_no_local_tracer_for_an_unrecorded_frame_with_nothing_else_watching(
    tmp_path,
):
    # A frame that's neither recorded by us (outside target_dir here) nor
    # wanted by any other tracer shouldn't get a no-op local tracer
    # installed - that would still cost a callback per frame for nothing.
    tracer.start(str(tmp_path))
    own_f_trace = sample_function_outside_target_dir()
    tracer.stop()

    assert own_f_trace is None


def sample_function_that_returns_a_value():
    return 42


def test_records_the_return_value_of_a_normal_call():
    tracer.start("tests")
    sample_function_that_returns_a_value()
    calls = tracer.stop()

    assert calls[0]["raised"] is False
    assert calls[0]["return_value"] == 42
    assert calls[0]["return_serialization"] == "json"


def sample_function_with_no_return_statement():
    pass


def test_records_an_implicit_none_return_as_not_raised():
    tracer.start("tests")
    sample_function_with_no_return_statement()
    calls = tracer.stop()

    assert calls[0]["raised"] is False
    assert calls[0]["return_value"] is None


def sample_function_that_raises():
    raise ValueError("boom")


def test_records_raised_true_when_an_exception_propagates_uncaught():
    tracer.start("tests")
    try:
        sample_function_that_raises()
    except ValueError:
        pass
    calls = tracer.stop()

    assert calls[0]["raised"] is True
    assert calls[0]["return_value"] is None
    assert calls[0]["return_serialization"] is None


def sample_function_that_catches_and_returns():
    try:
        raise ValueError("boom")
    except ValueError:
        return "caught"


def test_records_raised_false_when_a_call_catches_its_own_exception_and_returns_a_value():
    # "exception" fires for this frame too (it saw the exception even though
    # it went on to handle it), but an explicit non-None return afterward is
    # unambiguous proof it didn't propagate - the exception flag alone would
    # have gotten this wrong.
    tracer.start("tests")
    sample_function_that_catches_and_returns()
    calls = tracer.stop()

    assert calls[0]["raised"] is False
    assert calls[0]["return_value"] == "caught"


def sample_function_that_catches_and_implicitly_returns_none():
    try:
        raise ValueError("boom")
    except ValueError:
        pass


def test_records_raised_true_when_a_call_catches_its_own_exception_and_implicitly_returns_none():
    # A known, accepted false positive: "return" alone can't tell this
    # apart from a real propagating exception (both are event="return",
    # arg=None), and this frame did see an "exception" event. Documented
    # tradeoff, not a bug - see _make_local_tracer.
    tracer.start("tests")
    sample_function_that_catches_and_implicitly_returns_none()
    calls = tracer.stop()

    assert calls[0]["raised"] is True
    assert calls[0]["return_value"] is None


def sample_inner_that_raises():
    raise ValueError("inner boom")


def sample_outer_that_lets_it_propagate():
    sample_inner_that_raises()


def test_records_raised_true_for_a_frame_that_merely_lets_an_exception_pass_through():
    tracer.start("tests")
    try:
        sample_outer_that_lets_it_propagate()
    except ValueError:
        pass
    calls = tracer.stop()

    assert all(c["raised"] is True for c in calls)


_shared_non_serializable_return_value = _NotJSONSerializable()


def sample_function_that_returns_a_non_serializable_value():
    return _shared_non_serializable_return_value


def test_falls_back_to_repr_for_a_non_json_serializable_return_value():
    # return_value goes through the same _snapshot() as args - this just
    # proves that reuse, not re-deriving the whole args edge-case suite.
    tracer.start("tests")
    sample_function_that_returns_a_non_serializable_value()
    calls = tracer.stop()

    assert calls[0]["raised"] is False
    assert calls[0]["return_value"] == object.__repr__(
        _shared_non_serializable_return_value
    )
    assert calls[0]["return_serialization"] == "repr"


def test_records_each_generator_resumes_yielded_value_as_its_own_return_value():
    def sample_generator():
        yield 1
        yield 2

    def sample_driver():
        list(sample_generator())

    tracer.start("tests")
    sample_driver()
    calls = tracer.stop()

    generator_calls = [c for c in calls if "sample_generator" in c["qualname"]]
    # First two resumes: each yield is that resume's own return value. Final
    # resume: no "exception" event fires for the internal StopIteration used
    # to signal exhaustion (verified empirically), so it comes out as a
    # plain, unraised return of None - no special-casing needed for it.
    assert [c["return_value"] for c in generator_calls] == [1, 2, None]
    assert all(c["raised"] is False for c in generator_calls)
