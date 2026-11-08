"""The website RISK DOOR's way to the user, from every surface (v1.449.0, G54).

A browser or computer-use action that reads as a payment, a password, a
deletion or a flagged page stops INSIDE the tool (``browser/tools.
_ActingTool._require_approval``, ``computeruse/tools.WebActionTool``), after
the lane's own card already let the call through. v1.276.0 gave that stop a
card in the browser SIDEBAR (``browser/panel.PanelTurns.resolve_risk_ask``,
installed as ``BrowserRuntime.approval_resolver``); from the CHAT PAGE and
from an AGENT RUN it was still a dead end: the tool refused with "approve it
in Iron Jarvis, then make the identical call again. Pending approval
id=appr_…", and nothing on those surfaces could approve it.

This module is the ONE registry the door consults for "who can ask the
person about THIS call":

* the chat STREAM lane registers a resolver for its TURN
  (:func:`register_turn`, keyed on the ``turn_id`` the page mints — the id
  that already rides ``ToolContext.turn_id``), and
* the agent runtime registers one for its RUN (:func:`register_session`,
  keyed on the session id the runtime puts on ``ToolContext.session_id``).

:func:`resolver_for` picks the turn's, else the run's, else the runtime-wide
fallback (the sidebar's ``resolve_risk_ask``, which answers only ITS OWN
turn). The sidebar's lane turn registers nothing (``arm_family`` marks it),
so its behaviour is byte-identical. The POST lane registers nothing either:
it cannot show a card, so the door refuses in plain words with NO id
(:data:`NO_RESOLVER_TAIL`) — the same sentence minus the card (MIRROR NOTE).

A RISK CARD IS NEVER A GRANT: every answer wider than "once" is read as
once, the door approves and consumes one call, and the next identical call
asks again. A resolver answers :class:`Verdict` (or a bare bool for the
older one-argument shape): the door's refusal then says what happened —
declined, stopped, or not asked — without a queue id anywhere a user reads.
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from typing import Any, Callable

log = logging.getLogger(__name__)

#: The risk door's ``once_reason`` — a SAFETY reason (``core.approvals.
#: SAFETY_REASONS``): Enter never allows it, it never goes to the phone.
REASON = "page"

#: What the door says after "approval required: <reason>. Nothing has been
#: done to the page." when NOTHING could ask (the POST lane, a headless run).
NO_RESOLVER_TAIL = (
    "This step needs your OK on the chat page, where Iron Jarvis asks before it; "
    "nothing here can ask."
)
#: …when the person answered Deny on the card.
DECLINED_TAIL = "The user declined it when asked."
#: …when the turn or run was stopped while the card waited.
STOPPED_TAIL = "The turn was stopped before it was answered."
#: …when the card expired (an unattended run's clock) or the resolver did
#: not say — true for every refusal, promising nothing.
NOT_ALLOWED_TAIL = "It did not get the user's OK, so it was not done."


@dataclass(frozen=True)
class Verdict:
    """A resolver's answer: ``granted`` and, when not, the words that end the
    door's refusal (one of the ``*_TAIL`` sentences, or "" = the generic)."""

    granted: bool
    tail: str = ""


_TURNS: dict[str, Callable[..., Any]] = {}
_SESSIONS: dict[str, Callable[..., Any]] = {}


def register_turn(turn_id: str, resolver: Callable[..., Any]) -> None:
    """The chat stream lane's resolver for its named turn (no-op for "")."""
    tid = str(turn_id or "").strip()
    if tid and resolver is not None:
        _TURNS[tid] = resolver


def unregister_turn(turn_id: str, resolver: Callable[..., Any] | None = None) -> None:
    """Drop the turn's resolver — only the one given, when one is given."""
    tid = str(turn_id or "").strip()
    if tid and (resolver is None or _TURNS.get(tid) is resolver):
        _TURNS.pop(tid, None)


def register_session(session_id: str, resolver: Callable[..., Any]) -> Callable[..., Any] | None:
    """The agent runtime's resolver for its run; returns the one it replaced
    (a decomposed run's mini-loops nest) so the caller can put it back."""
    sid = str(session_id or "").strip()
    if not sid or resolver is None:
        return None
    previous = _SESSIONS.get(sid)
    _SESSIONS[sid] = resolver
    return previous


def unregister_session(
    session_id: str, resolver: Callable[..., Any] | None = None, *, restore: Callable[..., Any] | None = None
) -> None:
    """Drop the run's resolver (only the one given), putting ``restore`` back."""
    sid = str(session_id or "").strip()
    if not sid or (resolver is not None and _SESSIONS.get(sid) is not resolver):
        return
    if restore is not None:
        _SESSIONS[sid] = restore
    else:
        _SESSIONS.pop(sid, None)


def resolver_for(ctx: Any, fallback: Callable[..., Any] | None = None) -> Callable[..., Any] | None:
    """Who may ask about this call: the turn's resolver (``ctx.turn_id``),
    else the run's (``ctx.session_id``), else ``fallback`` (the runtime-wide
    one — the sidebar's)."""
    if getattr(ctx, "cannot_ask", False):
        # The POST chat lane (tools/base.ToolContext.cannot_ask): no card can
        # be drawn there, so no resolver — not even the runtime-wide one,
        # which answers only its own turn and would read as "not allowed".
        return None
    turn = str(getattr(ctx, "turn_id", "") or "").strip()
    if turn and turn in _TURNS:
        return _TURNS[turn]
    sid = str(getattr(ctx, "session_id", "") or "").strip()
    if sid and sid in _SESSIONS:
        return _SESSIONS[sid]
    return fallback


def registered_turns() -> list[str]:
    """For tests: the turn ids with a resolver right now."""
    return sorted(_TURNS)


def registered_sessions() -> list[str]:
    """For tests: the session ids with a resolver right now."""
    return sorted(_SESSIONS)


async def consult(
    resolver: Any, req: Any, ctx: Any, *, name: str, args: dict[str, Any]
) -> tuple[bool, str]:
    """Ask ``resolver`` whatever its shape (v1.276.0's rule): a one-argument
    synchronous ``resolver(req) -> bool`` (the older contract, tests), or
    ``resolver(req, ctx, *, name, args)`` sync or async, answering a bool or
    a :class:`Verdict`. Returns ``(granted, tail)``; a resolver that raises
    has granted nothing (a refusal, never an exception out of the door)."""
    if resolver is None:
        return False, NO_RESOLVER_TAIL
    try:
        params = inspect.signature(resolver).parameters
        wide = len(params) >= 2 or any(
            p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD) for p in params.values()
        )
    except (TypeError, ValueError):
        wide = False
    try:
        outcome = resolver(req, ctx, name=name, args=args) if wide else resolver(req)
        if inspect.isawaitable(outcome):
            outcome = await outcome
    except Exception:  # noqa: BLE001 — a resolver that fails has not granted anything
        log.debug("approval resolver failed; treating as refused", exc_info=True)
        return False, ""
    if isinstance(outcome, Verdict):
        return bool(outcome.granted), str(outcome.tail or "")
    return bool(outcome), ""


def refusal_words(reason: str, *, resolver: Any, tail: str = "") -> str:
    """The door's refusal, in words the model and the user can act on, with
    NO queue id: what was asked, that nothing happened, and how it ended."""
    why = " ".join(str(reason or "").split())
    end = tail or (NO_RESOLVER_TAIL if resolver is None else NOT_ALLOWED_TAIL)
    return f"approval required: {why}. Nothing has been done to the page. {end}"


__all__ = [
    "DECLINED_TAIL", "NO_RESOLVER_TAIL", "NOT_ALLOWED_TAIL", "REASON", "STOPPED_TAIL", "Verdict",
    "consult", "refusal_words", "register_session", "register_turn", "registered_sessions",
    "registered_turns", "resolver_for", "unregister_session", "unregister_turn",
]
