import inspect
import itertools
import math
import sys
import threading
from pathlib import Path

_SYNTHETIC_FRAME_NAMES = {"<listcomp>", "<dictcomp>", "<setcomp>", "<genexpr>"}
_JSON_SAFE_SCALAR_TYPES = (str, bool, type(None))

_target_dir = None
_start_cwd = None
_start_thread = None
_calls = []
_previous_trace = None
_previous_thread_trace = None
_call_id_counter = itertools.count()
_thread_local = threading.local()


def _call_stack():
    # Each thread gets its own stack (and call-tree root), matching
    # per-thread call nesting; frame.f_back never links across threads.
    stack = getattr(_thread_local, "stack", None)
    if stack is None:
        stack = []
        _thread_local.stack = stack
    return stack


def _to_json_safe(value):
    # Dispatch on the *exact* type, never a subclass: a subclass could
    # override __iter__/keys()/etc. with side effects. Error messages never
    # interpolate the value or type name, since even that can run target code.
    value_type = type(value)
    if value_type is float:
        if not math.isfinite(value):
            # json.dumps emits bare NaN/Infinity, which isn't valid JSON.
            raise ValueError("non-finite float")
        return value
    if value_type is int:
        try:
            str(value)
        except ValueError:
            # Past sys.get_int_max_str_digits(), same limit json.dumps
            # would hit later, but too late to recover from gracefully.
            raise ValueError(
                "integer exceeds the interpreter's max str digits"
            ) from None
        return value
    # `is` (not `in`, which uses ==) so a custom metaclass __eq__ can't
    # run target code or lie its way past this check.
    if any(value_type is safe_type for safe_type in _JSON_SAFE_SCALAR_TYPES):
        return value
    if value_type is list or value_type is tuple:
        return [_to_json_safe(item) for item in value]
    if value_type is dict:
        result = {}
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError("non-string dict key")
            result[key] = _to_json_safe(item)
        return result
    raise TypeError("not a JSON-safe value")


def _snapshot(value):
    # The "kind" tag ("json" vs "repr") travels with the value so a consumer
    # can tell a real JSON value apart from a repr() fallback string.
    try:
        return _to_json_safe(value), "json"
    except Exception:  # noqa: BLE001 - any snapshot failure must not abort the target
        if type(value) is float:
            return repr(value), "repr"
        # Could have a target-defined __repr__ with side effects; bypass
        # every override via the base implementation instead of repr(value).
        return object.__repr__(value), "repr"


def _exception_info(exc_type):
    # Read via type's own __module__/__qualname__ getset descriptors
    # (bound to exc_type) rather than exc_type.__module__, which could run
    # a custom metaclass's __getattribute__ or a __module__ property.
    try:
        module = type.__dict__["__module__"].__get__(exc_type)
    except Exception:  # noqa: BLE001 - reading a target class attr must never abort
        module = None
    try:
        qualname = type.__dict__["__qualname__"].__get__(exc_type)
    except Exception:  # noqa: BLE001 - reading a target class attr must never abort
        qualname = None
    module_value, _ = _snapshot(module)
    type_value, _ = _snapshot(qualname)
    return module_value, type_value


def _trace_calls(frame):
    co = frame.f_code
    # CO_OPTIMIZED is unset for module-level code and class bodies (real
    # frames, but not function calls); comprehensions/genexprs get their
    # own CO_OPTIMIZED frame but aren't callable functions either.
    if (
        co.co_filename.startswith("<")
        or not (co.co_flags & inspect.CO_OPTIMIZED)
        or co.co_name in _SYNTHETIC_FRAME_NAMES
    ):
        return None
    # Resolved against the cwd at start(), not the target's current cwd,
    # in case the target changes directory before this call fires.
    filename = Path(co.co_filename)
    if not filename.is_absolute():
        filename = _start_cwd / filename
    if not filename.resolve().is_relative_to(_target_dir):
        return None
    # f_locals also holds closed-over free variables for a nested function,
    # so pull names from getargvalues instead of every key.
    arg_info = inspect.getargvalues(frame)
    names = [*arg_info.args]
    if arg_info.varargs:
        names.append(arg_info.varargs)
    if arg_info.keywords:
        names.append(arg_info.keywords)
    # A resumed generator/coroutine fires another "call" event; a param
    # the function already `del`eted before yielding is gone from f_locals.
    args = {}
    arg_serialization = {}
    for name in names:
        if name not in frame.f_locals:
            continue
        args[name], arg_serialization[name] = _snapshot(frame.f_locals[name])
    stack = _call_stack()
    call_id = next(_call_id_counter)
    parent_call_id = stack[-1] if stack else None
    stack.append(call_id)
    # f_globals's __name__ is exactly what Python's import machinery
    # already assigned ("pkg" not "pkg.__init__", etc). Snapshotted since a
    # target could rebind its own __name__ to something non-JSON-safe.
    module, _ = _snapshot(frame.f_globals.get("__name__"))
    record = {
        "call_id": call_id,
        "parent_call_id": parent_call_id,
        "module": module,
        "qualname": co.co_qualname,
        "args": args,
        "arg_serialization": arg_serialization,
    }
    _calls.append(record)
    return record


def _make_local_tracer(record, previous_local):
    # Stays the local tracer for the frame's whole lifetime (we need our own
    # "return" event to pop the call stack), forwarding every event to
    # whatever tracer was previously chained in on top of our own bookkeeping.
    # `record` is mutated in place rather than looked up via `_calls[call_id]`:
    # two threads can both obtain a call_id before either appends its record.
    state = {"previous_local": previous_local, "saw_exception": False, "exc_type": None}

    def handler(frame, event, arg):
        if record is not None and event == "exception":
            state["saw_exception"] = True
            # arg is (exc_type, exc_value, traceback). Keeping the latest is
            # right for `except A: raise B`, but wrong if a `finally` raises
            # and catches a different exception (accepted, documented limitation).
            state["exc_type"] = arg[0]
        elif record is not None and event == "return":
            # arg is None both for `return None` and an exception unwinding
            # through this frame, so only fall back to the exception flag
            # then (misreads catch-then-return-None as raised, accepted).
            if arg is not None:
                record["raised"] = False
                record["return_value"], record["return_serialization"] = _snapshot(arg)
                record["exception_module"] = None
                record["exception_type"] = None
            else:
                record["raised"] = state["saw_exception"]
                record["return_value"] = None
                record["return_serialization"] = None
                if state["saw_exception"]:
                    record["exception_module"], record["exception_type"] = (
                        _exception_info(state["exc_type"])
                    )
                else:
                    record["exception_module"] = None
                    record["exception_type"] = None
            stack = _call_stack()
            if stack and stack[-1] == record["call_id"]:
                stack.pop()
        if state["previous_local"] is not None:
            state["previous_local"] = state["previous_local"](frame, event, arg)
        return handler

    return handler


def _dispatch(frame, event, arg):
    record = _trace_calls(frame)
    previous = (
        _previous_trace
        if threading.current_thread() is _start_thread
        else _previous_thread_trace
    )
    previous_local = previous(frame, event, arg) if previous is not None else None
    if record is None and previous_local is None:
        # Nothing to do: not a call we record, and no other tool watching
        # either. A no-op tracer here would still cost a "return" callback
        # per frame - stdlib-heavy targets could have a lot of these.
        return None
    if previous_local is None:
        # "line" events are pure overhead when nothing needs them; can't
        # suppress this when a previous tracer IS chained in (e.g. coverage.py).
        frame.f_trace_lines = False
    return _make_local_tracer(record, previous_local)


def start(target_dir):
    global \
        _target_dir, \
        _start_cwd, \
        _start_thread, \
        _previous_trace, \
        _previous_thread_trace, \
        _call_id_counter, \
        _thread_local
    _target_dir = Path(target_dir).resolve()
    _start_cwd = Path.cwd()
    _start_thread = threading.current_thread()
    _calls.clear()
    _call_id_counter = itertools.count()
    # A fresh instance, not .clear(): threading.local() only resets the
    # calling thread's view, so a worker's leftover stack from a previous
    # start()/stop() cycle could otherwise leak into this run.
    _thread_local = threading.local()
    _previous_trace = sys.gettrace()
    _previous_thread_trace = threading.gettrace()
    sys.settrace(_dispatch)
    # sys.settrace only covers the current thread; threads the target
    # itself starts need this too or their calls go unrecorded.
    threading.settrace(_dispatch)


def stop():
    # Restore whatever was active before start(), not hardcoded None, so
    # an in-process caller's own tracing (e.g. coverage) isn't clobbered.
    sys.settrace(_previous_trace)
    threading.settrace(_previous_thread_trace)
    return list(_calls)
