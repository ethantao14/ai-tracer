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
    # Each thread gets its own independent stack (and therefore its own
    # call-tree root), matching per-thread call nesting; frame.f_back never
    # links across threads, so there's nothing to reconcile between them.
    stack = getattr(_thread_local, "stack", None)
    if stack is None:
        stack = []
        _thread_local.stack = stack
    return stack


def _to_json_safe(value):
    # Dispatch on the value's *exact* type, never a subclass: iterating a
    # plain list/dict can't run user code, but a subclass could override
    # __iter__/keys()/etc. with side effects, and json.dumps would call
    # straight into those while we're just trying to observe the argument.
    # These raises never interpolate the value or its type name: an f-string
    # `!r` calls repr(), and even `value_type.__name__` can run target code
    # for a custom metaclass overriding __getattribute__ - and every one of
    # these messages is immediately caught and discarded by _snapshot below.
    value_type = type(value)
    if value_type is float:
        if not math.isfinite(value):
            # json.dumps emits bare NaN/Infinity, which isn't valid JSON and
            # would corrupt the .trace.json file it ends up written into.
            raise ValueError("non-finite float")
        return value
    if value_type is int:
        try:
            str(value)
        except ValueError:
            # An int past sys.get_int_max_str_digits() (default 4300
            # digits) can't be converted to a string at all - the same
            # limit json.dumps would hit later when writing the trace file,
            # which by then is too late to recover from gracefully.
            raise ValueError(
                "integer exceeds the interpreter's max str digits"
            ) from None
        return value
    # `in` on a tuple compares with ==, which for a class object with a
    # custom metaclass __eq__ would call that target-defined method (and
    # could even lie and return True, letting the object itself through
    # unvalidated). `is` never invokes anything overridable.
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
    # Freezes the value as of this exact moment (later mutation by the
    # traced code can't change what we recorded). The "kind" tag travels
    # alongside the value so a consumer (e.g. a future test generator) can
    # tell a real JSON value apart from a repr() fallback string - without
    # it, {"obj": "<Thing object at 0x...>"} is indistinguishable from a
    # call that genuinely passed that exact string.
    try:
        return _to_json_safe(value), "json"
    except Exception:  # noqa: BLE001 - any snapshot failure must not abort the target
        if type(value) is float:
            # A float has no nested content, so its own repr (e.g. for NaN
            # or infinity) can't reach any target-defined code.
            return repr(value), "repr"
        # Anything else (a container that failed deeper in, or a
        # target-defined class) could have a __repr__ - its own or a nested
        # element's - that runs arbitrary code with side effects. Bypass
        # every override via the base implementation instead of repr(value).
        return object.__repr__(value), "repr"


def _exception_info(exc_type):
    # The module and qualified name of the exception class that propagated out
    # of a call. Read through `type`'s own __module__/__qualname__ getset
    # descriptors (bound to exc_type), which sidesteps any custom metaclass:
    # plain `exc_type.__module__` would run a metaclass's __getattribute__ or,
    # worse, a metaclass property named __module__ - target code that could
    # mutate state just because we're observing an exception. type's base
    # descriptors run no target code and still return the real value for both
    # builtins ("builtins"/"ValueError") and target classes. Each is then
    # snapshotted, so a class with a non-string __module__/__qualname__ can't
    # break the final json.dumps of the trace. __module__ here is consistent
    # with how function calls are recorded: a target exception defined in the
    # entry script reads "__main__", matching the function-frame module
    # resolution, and a builtin reads "builtins".
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
        return None
    # The main script keeps whatever (possibly relative) path string it was
    # given to preserve argv[0] fidelity. Resolve it against the cwd at
    # start(), not whatever the target's cwd happens to be by the time this
    # call fires, in case the target itself changes directory.
    filename = Path(co.co_filename)
    if not filename.is_absolute():
        filename = _start_cwd / filename
    if not filename.resolve().is_relative_to(_target_dir):
        return None
    # f_locals also holds closed-over free variables for a nested function,
    # not just its own parameters, so pull names from getargvalues instead of
    # every key.
    arg_info = inspect.getargvalues(frame)
    names = [*arg_info.args]
    if arg_info.varargs:
        names.append(arg_info.varargs)
    if arg_info.keywords:
        names.append(arg_info.keywords)
    # Python fires another "call" event each time a generator/coroutine
    # frame is resumed, not just when it's first entered. If the function
    # already `del`eted one of its own parameters before yielding, that name
    # is gone from f_locals on the next resume.
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
    # f_globals is the actual namespace the function was defined in, so
    # __name__ here is exactly what Python's own import machinery already
    # assigned it: "__main__" for the entry script (matching direct
    # execution), the real dotted name for an imported module, and
    # correctly "pkg" rather than "pkg.__init__" for a package's __init__.py
    # - no need to reconstruct any of that from the file path ourselves.
    # Snapshotted like any other target-controlled value: a target that
    # rebinds its own __name__ to something non-JSON-safe must not be able
    # to break the eventual json.dumps() of the whole trace.
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
    # Unlike PR2's chaining (which fully handed the frame's local tracer off
    # to whatever tool was already active, since _trace_calls only needed
    # "call"), we now need our own "return" event too, to pop the call
    # stack. So this stays the local tracer for the frame's whole lifetime,
    # forwarding every event to the previously-active tracer's own
    # continuation on top of our own return-driven bookkeeping.
    # `record` (this call's own dict, held by direct reference) is mutated
    # in place on "return" rather than looked up via `_calls[call_id]`: two
    # threads can both obtain a call_id before either appends its record, so
    # call_id is not reliably that record's index into the shared _calls
    # list once multiple threads are calling into traced code concurrently.
    state = {"previous_local": previous_local, "saw_exception": False, "exc_type": None}

    def handler(frame, event, arg):
        if record is not None and event == "exception":
            state["saw_exception"] = True
            # arg is (exc_type, exc_value, traceback). Keep the latest one:
            # the exception actually unwinding out of the frame is usually the
            # last one seen before it exits, and only that one is recorded if
            # the frame turns out to have raised. This is right for the common
            # re-raise pattern (`except A: raise B` - B is what escapes). It's
            # wrong only in the rare case where a `finally` block itself raises
            # and catches a *different* exception while the original keeps
            # propagating: the original re-emerges with no further "exception"
            # event, and sys.exc_info() is already cleared by the "return"
            # event, so the last-seen exception is the finally's handled one,
            # not the escaping one. Accepted, documented limitation.
            state["exc_type"] = arg[0]
        elif record is not None and event == "return":
            # "return" fires for every frame exit, exceptional or not, with
            # arg set to the actual return value only for a genuine return -
            # arg is None both for an explicit/implicit `return None` *and*
            # for an exception unwinding through this frame, and those two
            # cases are indistinguishable from "return" alone. A non-None
            # arg is unambiguous (even a frame that caught its own exception
            # and returned a value lands here), so only fall back to the
            # exception flag when arg is None. That still misreads "caught
            # it, then returned None" (explicitly or implicitly) as raised -
            # accepted, since the alternative is silently losing a real propagating
            # exception, which is worse.
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
        # Nothing to do for this frame: we didn't record it (outside
        # target_dir, or not a real function call), and no other tool wants
        # to observe it either. Installing a no-op local tracer here would
        # still cost a "return" callback for every such frame - stdlib-heavy
        # targets could have a lot of these.
        return None
    if previous_local is None:
        # No other tool is watching this frame, so "line" events (fired per
        # source line, far more often than call/return/exception) are pure
        # overhead - we don't need them. Can't suppress this when a previous
        # tracer IS chained in, since it might genuinely need line events
        # (e.g. coverage.py).
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
    # A fresh instance, not .clear() on the shared one, so a worker thread's
    # leftover stack from a previous start()/stop() cycle in the same
    # interpreter can't leak into this run (threading.local() only resets
    # the calling thread's own view, not other threads').
    _thread_local = threading.local()
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
