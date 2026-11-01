"""WHICH TRY OF A ROUND THIS MODEL CALL IS (v1.422.0 review, Wave 2 "Never lose
work", B1).

A coding session's round that fails with a passing error is asked again by
``harness/patience`` — the SAME call through the router, up to five times. The
router publishes ``provider.failed`` for every failed call, and that event is a
DEFAULT ALERT (``comm/notifier``): "This PC" toasts it, a phone gets it. Before
the patience a failed reply sent ONE such notice; with it, a round that waited
out a 529 storm sent one per try — up to five "<model> couldn't answer. Press
Retry." notices in two minutes, while the reply itself said it was trying
again.

So the patience marks every try AFTER the first (:func:`marked`), the router
puts ``retry: <attempt>`` on that try's ``provider.failed`` payload
(:func:`payload_extra`), and the notifier says only the round's first failure.
The ledger (the event log) keeps every one.

A ContextVar, set and reset around ONE try inside the task that runs the call
(the patience's frames are driven by one pump task; the POST lane awaits the
call itself), so it reaches exactly the router calls of that try and nothing
else. Unset everywhere else: every payload is byte-identical to before.
"""

from __future__ import annotations

import contextlib
from contextvars import ContextVar
from typing import Any, Iterator

_ATTEMPT: ContextVar[int] = ContextVar("ij_round_retry_attempt", default=0)


def attempt() -> int:
    """The try of the round the current model call is (2 and up = a retry),
    0 outside a retried try."""
    try:
        return int(_ATTEMPT.get() or 0)
    except Exception:  # noqa: BLE001
        return 0


@contextlib.contextmanager
def marked(n: int) -> Iterator[None]:
    """Mark the calls made inside as try ``n`` of their round (only ``n`` ≥ 2
    marks anything). Never raises on the way out (a token reset in another
    context — a generator finalised elsewhere — is left alone)."""
    if int(n or 0) < 2:
        yield
        return
    token = _ATTEMPT.set(int(n))
    try:
        yield
    finally:
        with contextlib.suppress(Exception):
            _ATTEMPT.reset(token)


def payload_extra() -> dict[str, Any]:
    """``{"retry": n}`` for a ``provider.failed`` published inside a retried
    try, else ``{}``."""
    n = attempt()
    return {"retry": n} if n >= 2 else {}


__all__ = ["attempt", "marked", "payload_extra"]
