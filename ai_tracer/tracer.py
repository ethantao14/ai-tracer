import inspect
import math
import sys
import threading
from pathlib import Path

_SYNTHETIC_FRAME_NAMES = {"<listcomp>", "<dictcomp>", "<setcomp>", "<genexpr>"}
_JSON_SAFE_SCALAR_TYPES = (str, int, bool, type(None))

_target_dir = None
_start_cwd = None
_start_thread = None
_calls = []
_previous_trace = None
_previous_thread_trace = None


def _to_json_safe(value):
    # Dispatch on the value's *exact* type, never a subclass: iterating a
    # plain list/dict can't run user code, but a subclass could override
    # __iter__/keys()/etc. with side effects, and json.dumps would call
    # straight into those while we're just trying to observe the argument.
    value_type = type(value)
    if value_type is float:
        if not math.isfinite(value):
            # json.dumps emits bare NaN/Infinity, which isn't valid JSON and
            # would corrupt the .trace.json file it ends up written into.
            raise ValueError(f"non-finite float: {value!r}")
        return value
    if value_type in _JSON_SAFE_SCALAR_TYPES:
        return value
    if value_type is list or value_type is tuple:
        return [_to_json_safe(item) for item in value]
    if value_type is dict:
        result = {}
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError(f"non-string dict key: {key!r}")
            result[key] = _to_json_safe(item)
        return result
    raise TypeError(f"not a JSON-safe value: {value_type.__name__}")


def _snapshot(value):
    # Freezes the value as of this exact moment (later mutation by the
    # traced code can't change what we recorded).
    try:
        return _to_json_safe(value)
    except Exception:  # noqa: BLE001 - any snapshot failure must not abort the target
        if type(value) is float:
            # A float has no nested content, so its own repr (e.g. for NaN
            # or infinity) can't reach any target-defined code.
            return repr(value)
        # Anything else (a container that failed deeper in, or a
        # target-defined class) could have a __repr__ - its own or a nested
        # element's - that runs arbitrary code with side effects. Bypass
        # every override via the base implementation instead of repr(value).
        return object.__repr__(value)


def _trace_calls(frame, event, arg):
    if event != "call":
        return
    co = frame.f_code
    # Synthetic filenames like "<frozen importlib._bootstrap>" aren't real
    # files. CO_OPTIMIZED is unset for module-level code and class bodies
    # (both run as their own frame but aren't a real function call).
    # Comprehensions/genexprs get their own CO_OPTIMIZED frame with a real
    # filename too, but aren't a function the target could call again.
    if (
        co.co_filename.startswith("<")
        or not (co.co_flags & inspect.CO_OPTIMIZED)
        or co.co_name in _SYNTHETIC_FRAME_NAMES
    ):
        return
    # The main script keeps whatever (possibly relative) path string it was
    # given to preserve argv[0] fidelity. Resolve it against the cwd at
    # start(), not whatever the target's cwd happens to be by the time this
    # call fires, in case the target itself changes directory.
    filename = Path(co.co_filename)
    if not filename.is_absolute():
        filename = _start_cwd / filename
    if not filename.resolve().is_relative_to(_target_dir):
        return
    # f_locals also holds closed-over free variables for a nested function,
    # not just its own parameters, so pull names from getargvalues instead of
    # every key.
    arg_info = inspect.getargvalues(frame)
    names = [*arg_info.args]
    if arg_info.varargs:
        names.append(arg_info.varargs)
    if arg_info.keywords:
        names.append(arg_info.keywords)
    args = {name: _snapshot(frame.f_locals[name]) for name in names}
    _calls.append({"qualname": co.co_qualname, "args": args})


def _dispatch(frame, event, arg):
    # _trace_calls never wants line/return/exception events for a frame (it
    # only acts on "call"), so rather than becoming the frame's local
    # tracer, run it as a side effect and hand off entirely to whatever
    # tracer (if any) was already active, so tools like coverage or a
    # debugger keep observing the target exactly as they would have without
    # us. New threads get _previous_thread_trace instead, since that's what
    # they'd have inherited had we not overridden threading.settrace().
    _trace_calls(frame, event, arg)
    previous = (
        _previous_trace
        if threading.current_thread() is _start_thread
        else _previous_thread_trace
    )
    if previous is not None:
        return previous(frame, event, arg)
    return None


def start(target_dir):
    global \
        _target_dir, \
        _start_cwd, \
        _start_thread, \
        _previous_trace, \
        _previous_thread_trace
    _target_dir = Path(target_dir).resolve()
    _start_cwd = Path.cwd()
    _start_thread = threading.current_thread()
    _calls.clear()
    _previous_trace = sys.gettrace()
    _previous_thread_trace = threading.gettrace()
    sys.settrace(_dispatch)
    # sys.settrace only covers the current thread, threads the target
    # itself starts need this too or their calls go unrecorded.
    threading.settrace(_dispatch)


def stop():
    # Restore whatever tracer (if any) was active before start(), rather
    # than hardcoding None, so an in-process caller's own tracing (e.g.
    # coverage) isn't clobbered.
    sys.settrace(_previous_trace)
    threading.settrace(_previous_thread_trace)
    return list(_calls)
