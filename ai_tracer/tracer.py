import inspect
import sys
import threading
from pathlib import Path

_target_dir = None
_calls = []
_previous_trace = None
_previous_thread_trace = None


def _trace_calls(frame, event, arg):
    if event != "call":
        return
    co = frame.f_code
    # Synthetic filenames like "<frozen importlib._bootstrap>" aren't real
    # files. CO_OPTIMIZED is unset for module-level code and class bodies
    # (both run as their own frame but aren't a real function call).
    if co.co_filename.startswith("<") or not (co.co_flags & inspect.CO_OPTIMIZED):
        return
    if not Path(co.co_filename).resolve().is_relative_to(_target_dir):
        return
    _calls.append({"qualname": co.co_qualname})


def start(target_dir):
    global _target_dir, _previous_trace, _previous_thread_trace
    _target_dir = Path(target_dir).resolve()
    _calls.clear()
    _previous_trace = sys.gettrace()
    _previous_thread_trace = threading.gettrace()
    sys.settrace(_trace_calls)
    # sys.settrace only covers the current thread, threads the target
    # itself starts need this too or their calls go unrecorded.
    threading.settrace(_trace_calls)


def stop():
    # Restore whatever tracer (if any) was active before start(), rather
    # than hardcoding None, so an in-process caller's own tracing (e.g.
    # coverage) isn't clobbered.
    sys.settrace(_previous_trace)
    threading.settrace(_previous_thread_trace)
    return list(_calls)
