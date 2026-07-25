# Renders a `unittest.mock.patch(...)` block that replaces a function's
# direct child call with its recorded return value, so a generated test
# exercises only the function under test - not whatever it happens to call.
#
# The patch target is the *calling* module's own name for the child
# (`<parent module>.<child qualname>`), assuming the caller reached it via
# a plain `from <child's module> import <name>` - i.e. bound it under its
# own unaliased qualname. This is the standard "patch where it's looked up,
# not where it's defined" idiom, and empirically it fails loudly rather
# than silently in its main failure mode: if the caller instead reached the
# child via `import <module>; <module>.<name>(...)`, the calling module has
# no attribute of that name, so `mock.patch()` raises `AttributeError`
# immediately - the generated test fails visibly instead of under-isolating.
#
# The alternative - patching the child's own defining module instead - covers
# that case, but only if the calling module is reimported *after* the patch
# is applied (a `from x import y` binding resolves at import time), which
# would mean reimporting per test for any module that ends up needing a
# mock. That breaks the "each shared module is imported once per session"
# design the generated conftest.py already relies on, and its own failure
# mode is silent (the stale, unpatched reference just keeps working) rather
# than loud. Chosen against for both reasons.
#
# The one case patching-where-used still gets wrong silently: the calling
# module happens to define or import an unrelated attribute under the same
# name as the child's qualname, while reaching the real child via
# module-attribute access elsewhere. Accepted as a rare edge case.


def render_import_line():
    return "from unittest import mock"


def mock_target(parent_module, calls):
    return f"{parent_module}.{calls[0]['qualname']}"


def _outcome(calls, exception_reference):
    # A raised call can't be expressed as return_value at all, and a call
    # made more than once needs its outcomes replayed in traced order - so
    # only a single, non-raised call gets the plain return_value= form.
    # (A bare exception class in a side_effect list is enough on its own to
    # make the mock raise it - unittest.mock instantiates it with no args,
    # same as pytest.raises(SomeError) names the type, not a message.)
    if len(calls) == 1 and not calls[0]["raised"]:
        return f"return_value={calls[0]['return_value']!r}"
    if len(calls) == 1:
        return f"side_effect={exception_reference(calls[0])}"
    literals = ", ".join(
        exception_reference(call) if call["raised"] else repr(call["return_value"])
        for call in calls
    )
    return f"side_effect=[{literals}]"


def render_patch_clause(parent_module, calls, alias, exception_reference):
    # `calls` holds every recorded call to one direct child, in traced call
    # order - `mock.patch(...) as alias`, with no leading "with" and no
    # trailing ":", so several children's clauses can share one `with`
    # statement. `exception_reference(call)` must return a source
    # expression naming the raised call's exception class (e.g.
    # "ValueError" or "errors.AppError"); only called for a raised call.
    target = mock_target(parent_module, calls)
    return f"mock.patch({target!r}, {_outcome(calls, exception_reference)}) as {alias}"


def render_patch_statement(clauses):
    # One `with clause1, clause2, ...:` line covering every direct child a
    # function under test needs mocked - each entry in `clauses` comes from
    # a `render_patch_clause` call for one distinct child target.
    return "with " + ", ".join(clauses) + ":"
