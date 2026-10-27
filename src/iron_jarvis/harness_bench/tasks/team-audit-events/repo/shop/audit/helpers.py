"""Shortcuts for writing audit events."""

import functools

from .log import record


def audit_change(entity, action, **fields):
    """Writes the event ``"<entity>.<action>"``:
    ``audit_change("invoice", "voided")`` writes ``"invoice.voided"``."""
    record(f"{entity}.{action}", **fields)


def audit_failure(area, **fields):
    """Writes the event ``"failure.<area>"``:
    ``audit_failure("payments_declined")`` writes ``"failure.payments_declined"``."""
    record(f"failure.{area}", **fields)


def audited(event):
    """A decorator: each time the decorated function returns, the event
    ``event`` is written (``@audited("report.exported")``)."""

    def wrap(fn):
        @functools.wraps(fn)
        def inner(*args, **kwargs):
            result = fn(*args, **kwargs)
            record(event, function=fn.__qualname__)
            return result

        return inner

    return wrap
