"""Optional per-request lookup of persisted email tool messages."""

from contextlib import contextmanager
from contextvars import ContextVar

_lookup = ContextVar("email_tool_message_lookup", default=None)


@contextmanager
def email_evidence(lookup):
    token = _lookup.set(lookup)
    try:
        yield
    finally:
        _lookup.reset(token)


def lookup_tool_message(operation_id):
    lookup = _lookup.get()
    if lookup is None:
        return {"status": "UNAVAILABLE"}
    try:
        return lookup(operation_id)
    except Exception:
        return {"status": "UNAVAILABLE"}
