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


def mock_target(parent_module, child):
    return f"{parent_module}.{child['qualname']}"


def render_patch_line(parent_module, child, alias):
    # Happy path only: `child` was called exactly once and returned rather
    # than raised. Repeated calls and raised children are handled later.
    target = mock_target(parent_module, child)
    return (
        f"with mock.patch({target!r}, return_value={child['return_value']!r}) "
        f"as {alias}:"
    )
