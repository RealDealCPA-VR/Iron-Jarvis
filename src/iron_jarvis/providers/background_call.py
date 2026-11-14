"""A MODEL CALL NOBODY IS WAITING FOR (v1.492.0).

The router publishes ``provider.failed`` for every failed call, and that event
is a DEFAULT ALERT (``comm/notifier``): "This PC" toasts it and a phone gets
"<model> couldn't answer. Press Retry." For a call the user asked for, that is
right. For a nicety Iron Jarvis does by itself in the background (a recap of a
finished conversation) it is noise about something the user never asked for
and cannot retry.

So such a call is made inside :func:`background`, the router puts
``background: "<what>"`` on that call's ``provider.failed`` payload
(:func:`payload_extra`), and the notifier says nothing for it. The ledger (the
event log) and the provider's own health still record the failure.

A ContextVar, set and reset around ONE call in the task that makes it. Unset
everywhere else: every payload is byte-identical to before.
"""

from __future__ import annotations

import contextlib
from contextvars import ContextVar
from typing import Any, Iterator

_WHAT: ContextVar[str] = ContextVar("ij_background_call", default="")


def what() -> str:
    """What background work the current model call is for ("" = none)."""
    try:
        return str(_WHAT.get() or "")
    except Exception:  # noqa: BLE001
        return ""


@contextlib.contextmanager
def background(name: str) -> Iterator[None]:
    """Mark the model calls made inside as background work called ``name``.
    Never raises on the way out."""
    token = _WHAT.set(str(name or "")[:60])
    try:
        yield
    finally:
        with contextlib.suppress(Exception):
            _WHAT.reset(token)


def payload_extra() -> dict[str, Any]:
    """``{"background": name}`` for a ``provider.failed`` published inside a
    background call, else ``{}``."""
    name = what()
    return {"background": name} if name else {}


__all__ = ["background", "payload_extra", "what"]
