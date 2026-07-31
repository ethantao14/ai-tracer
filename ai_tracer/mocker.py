# Renders a `mock.patch(...)` block replacing a function's direct child
# call with its recorded return value. Patches under the calling module's
# own name for the child - see README's "mock patch target" limitation for why.


def render_import_line():
    return "from unittest import mock"


def mock_target(parent_module, calls):
    return f"{parent_module}.{calls[0]['qualname']}"


def _outcome(calls, exception_reference):
    # Only a single, non-raised call can use return_value=; anything raised
    # or repeated needs side_effect= to replay outcomes in traced order.
    # A bare exception class in side_effect is enough to make the mock raise it.
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
    # Renders "mock.patch(...) as alias" with no leading "with"/trailing ":"
    # so several children's clauses can share one `with` statement.
    # exception_reference(call) names the raised call's exception class.
    target = mock_target(parent_module, calls)
    return f"mock.patch({target!r}, {_outcome(calls, exception_reference)}) as {alias}"


def render_patch_statement(clauses):
    # One `with clause1, clause2, ...:` line covering every direct child a
    # function under test needs mocked - each entry in `clauses` comes from
    # a `render_patch_clause` call for one distinct child target.
    return "with " + ", ".join(clauses) + ":"
