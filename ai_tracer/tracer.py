import inspect
import json
import sys
import threading
from pathlib import Path

_SYNTHETIC_FRAME_NAMES = {"<listcomp>", "<dictcomp>", "<setcomp>", "<genexpr>"}

_target_dir = None
_start_cwd = None
_start_thread = None
_calls = []
_previous_trace = None
_previous_thread_trace = None


def _snapshot(value):
    # Round-tripping through JSON freezes the value as of this exact moment
    # (later mutation by the traced code can't change what we recorded) and
    # falls back to repr for anything that isn't JSON-serializable (e.g. self).
    try:
        return json.loads(json.dumps(value))
    except Exception:  # noqa: BLE001 - any encoding failure must not abort the target
        # Not just TypeError (non-serializable, e.g. self) or ValueError
        # (circular containers): a container subclass with a raising
        # __iter__/__repr__/keys() can make the encoder raise anything.
        try:
            return repr(value)
        except Exception:  # noqa: BLE001 - a raising __repr__ must not abort the target
            return f"<unrepresentable {type(value).__name__}>"


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
