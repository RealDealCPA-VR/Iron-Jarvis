"""Who is working on a team RIGHT NOW (v1.307.0) — display only.

``delegate`` and ``spawn_agent`` await ``AgentRuntime.run`` directly, so a
delegated child never enters the orchestrator's ``_running`` / ``_governed`` /
``_queued`` sets. The roster's LIVENESS docstring records the consequence: the
fan-out a "busy" marker would most help with (one coordinator handing work to
four teammates) was exactly what it could not see, and the mission view's
agent cards had nothing live to read.

The two fixes the roster docstring rejected are still rejected — routing a
child through the governor deadlocks, and registering it in ``_running`` hands
``cancel_session`` and the slot-free hook a handle that is not theirs. This is
the third shape: a SEPARATE in-memory map that grants nothing. It is read for
display (:func:`live_children`, the roster) and it links the child's token
stream to its team root (``StreamHub.link``) so one SSE feed carries the whole
team. A child joins in :func:`working` and leaves in its ``finally`` — whatever
way the run ends, it cannot stay "busy".

Never raises: presence is a bonus, never a liability for the run it describes.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Iterator

from ..core.ids import utcnow

#: child session id -> {"agent", "parent_session_id", "root_session_id", "task", "since"}
_LIVE: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()


def _fold(name: Any) -> str:
    """Casefolded roster name, whitespace around a ``kind:`` colon removed."""
    text = " ".join(str(name or "").split())
    if ":" in text:
        head, _, tail = text.partition(":")
        text = f"{head.strip()}:{tail.strip()}"
    return text.casefold()


def mission_team(session: Any) -> list[str]:
    """The roster names a MISSION may hand work to (v1.308.0), from the
    coordinator's run options (``options.team`` — the project's team,
    snapshotted by ``POST /missions``). ``[]`` = no restriction (a mission
    outside a project, a project with no team, every other session)."""
    try:
        from ..scheduling.knobs import session_options

        team = session_options(session).get("team")
    except Exception:  # noqa: BLE001 — no options, no restriction
        return []
    if not isinstance(team, list):
        return []
    return [str(t).strip() for t in team if isinstance(t, str) and str(t).strip()]


def on_team(team: list[str], roster_name: str) -> bool:
    """Is ``roster_name`` one of ``team``? An empty team allows everyone."""
    if not team:
        return True
    want = _fold(roster_name)
    return any(_fold(t) == want for t in team)


def off_team_refusal(team: list[str], roster_name: str) -> str:
    """The sentence a delegation outside the project's team is refused with."""
    names = ", ".join(team)
    return (
        f"'{roster_name}' is not on this project's team — hand this part to one "
        f"of: {names}. (The user picks the team on the project's Agents screen.)"
    )


def live_children() -> dict[str, dict[str, Any]]:
    """A snapshot of every delegated child running in this process."""
    with _LOCK:
        return {sid: dict(row) for sid, row in _LIVE.items()}


def live_child_ids() -> list[str]:
    with _LOCK:
        return list(_LIVE)


@contextmanager
def working(
    platform: Any,
    child_session_id: str,
    parent_session_id: str,
    agent: str,
    task: str = "",
) -> Iterator[None]:
    """Mark ``child_session_id`` as working for its team for the duration of
    the block, and mirror its stream to the team root. Always leaves."""
    sid = str(child_session_id or "")
    root = str(parent_session_id or "")
    hub = getattr(platform, "streams", None)
    try:
        if hub is not None and sid:
            root = hub.link(sid, parent_session_id, {"agent": str(agent or "")})
    except Exception:  # noqa: BLE001 — a broken mirror never stops a delegation
        pass
    if sid:
        with _LOCK:
            _LIVE[sid] = {
                "agent": str(agent or ""),
                "parent_session_id": str(parent_session_id or ""),
                "root_session_id": root,
                "task": " ".join(str(task or "").split())[:240],
                "since": utcnow().isoformat(),
            }
    try:
        yield
    finally:
        if sid:
            with _LOCK:
                _LIVE.pop(sid, None)
            try:
                if hub is not None:
                    hub.unlink(sid)
            except Exception:  # noqa: BLE001
                pass
