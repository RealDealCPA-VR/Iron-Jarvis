"""A chat lane's TURN SCOPE: "this turn may continue an answer the model
already ended" (IJ harness part 3, Phase 2 review).

The finish check (``harness/finish``) sends its note AFTER the model ended its
reply, and the model answers it in one more round. For a provider whose turn
lives in a PROCESS — ``codex-cli``'s app-server, whose thread ends when the
model ends its answer — that round used to be a NEW process with the whole
conversation replayed as labelled text (measured with the fake app-server: a
second process start, and every earlier tool call and result sent again as
fresh, uncached input), once per Build reply that changed files.

The lane opens a scope for exactly the turns that can send that note (a turn
whose finish check exists) and closes it when its tool loop ends. While it is
open, an adapter may KEEP a session whose turn just ended, keyed by the
conversation it answered, so the lane's next call — the same conversation
plus the model's answer plus the note — continues it instead of starting
again. Closing the scope stops every session still kept. Nothing outside an
open scope changes: a plain chat turn, an agent run, a schedule never open
one, and a kept session can only be continued by a call of the SAME scope.

A :class:`~contextvars.ContextVar` (the ``iron_proxy.pin`` / ``providers.hold``
pattern): the lane's pump task and every adapter call it makes inherit it.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger("ironjarvis.turn_scope")


@dataclass(eq=False)
class TurnScope:
    """One lane turn's scope. ``on_close`` callbacks run (once) when it
    closes; one registered after it closed runs at once."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    closed: bool = False
    _closers: list[Callable[[], Any]] = field(default_factory=list)

    def on_close(self, fn: Callable[[], Any]) -> bool:
        """Register ``fn`` (sync, run off the loop, or async). False when the
        scope is already closed (the caller then cleans up itself)."""
        if self.closed:
            return False
        self._closers.append(fn)
        return True


TURN_SCOPE: ContextVar["TurnScope | None"] = ContextVar("ij_turn_scope", default=None)


def current() -> "TurnScope | None":
    """The OPEN scope of this call, or None."""
    scope = TURN_SCOPE.get()
    return scope if scope is not None and not scope.closed else None


def open_scope() -> "tuple[TurnScope, Token]":
    scope = TurnScope()
    return scope, TURN_SCOPE.set(scope)


def reset(token: "Token | None") -> None:
    """Undo :func:`open_scope`'s set (a generator closed from another task
    holds a token of another context: the var is then just cleared)."""
    if token is None:
        return
    try:
        TURN_SCOPE.reset(token)
    except ValueError:
        TURN_SCOPE.set(None)


async def close(scope: "TurnScope | None") -> None:
    """Close ``scope``: nothing may be kept in it from now on, and every
    ``on_close`` callback runs (a failing one is logged, never raised)."""
    if scope is None or scope.closed:
        return
    scope.closed = True
    closers, scope._closers = list(scope._closers), []
    for fn in closers:
        try:
            if inspect.iscoroutinefunction(fn):
                await fn()
            else:
                result = await asyncio.to_thread(fn)
                if inspect.isawaitable(result):
                    await result
        except Exception:  # noqa: BLE001 — a cleanup never breaks the turn
            log.debug("a turn scope's cleanup failed", exc_info=True)


__all__ = ["TURN_SCOPE", "TurnScope", "close", "current", "open_scope", "reset"]
