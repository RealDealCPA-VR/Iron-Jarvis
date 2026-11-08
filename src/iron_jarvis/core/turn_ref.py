"""Which daemon-owned CHAT TURN the code running now belongs to (v1.455.0).

A USER BUG: a chat on the user's own model server failed, and the phone got
"Fleet-custom couldn't answer. Press Retry." — no chat named, and sent while
the user was looking at that very chat, which already showed the failure.
The router's ``provider.failed`` knew nothing about the turn it failed in.

``daemon/durable_turns`` sets :data:`CURRENT` around the lane it drives (the
turn's own task; every task the lane makes copies it), and the router's ONE
publisher of ``provider.failed`` / ``provider.downgraded``
(``ModelRouter._publish_provider_event``) adds :func:`event_keys` to the
payload. Outside a turn (agent runs, workflows, one-shots) nothing is set and
the payload is exactly what it was. Never raises.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TurnRef:
    """One running turn: its id, and live reads of the chat it belongs to
    (a new chat's first turn learns its chat only after the first save) and
    of whether a window is showing it right now."""

    turn_id: str
    thread_id: Callable[[], str]
    looked_at: Callable[[], bool]
    #: Is the turn still running? (v1.455.0 review.) A task the turn made can
    #: outlive it — a Build library reflection armed at the turn's end fires
    #: ten minutes later from the turn's context — and its failure is not
    #: this turn's: an ended turn lends its reference to nothing.
    running: Callable[[], bool] = lambda: True


CURRENT: ContextVar["TurnRef | None"] = ContextVar("iron_jarvis_chat_turn", default=None)


@contextlib.contextmanager
def scope(ref: "TurnRef | None") -> Iterator[None]:
    token = CURRENT.set(ref)
    try:
        yield
    finally:
        CURRENT.reset(token)


def event_keys() -> dict[str, Any]:
    """``{"turn_id", "thread_id", "looked_at"}`` inside a turn, else ``{}``.
    A read that fails says what it can (never a raise from a publish)."""
    ref = CURRENT.get()
    if ref is None or not ref.turn_id:
        return {}
    try:
        if not ref.running():
            return {}
    except Exception:  # noqa: BLE001 — an unknown turn lends nothing
        return {}
    out: dict[str, Any] = {"turn_id": ref.turn_id}
    try:
        out["thread_id"] = str(ref.thread_id() or "")
    except Exception:  # noqa: BLE001
        out["thread_id"] = ""
    try:
        out["looked_at"] = bool(ref.looked_at())
    except Exception:  # noqa: BLE001
        out["looked_at"] = False
    return out
