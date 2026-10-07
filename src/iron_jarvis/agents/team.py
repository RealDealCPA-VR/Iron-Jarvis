"""Who is working on a team RIGHT NOW (v1.307.0) — display only. Also the
team's IDENTITY (v1.309.0): the mission origins and the one root walk every
team rule reads (:func:`mission_root`).

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

import asyncio
import threading
from contextlib import contextmanager
from typing import Any, Iterator

from ..core.ids import utcnow

#: child session id -> {"agent", "parent_session_id", "root_session_id", "task", "since"}
_LIVE: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()

#: The origin ``POST /missions`` stamps on a mission's COORDINATOR (the root).
#: ``daemon/routes/sessions.py`` keeps its own copy of the literal; the
#: agents layer cannot import the daemon (import direction, v1.185.0).
MISSION_ORIGIN = "job:mission"

#: The origin every TEAMMATE of a mission carries (v1.309.0) — the children
#: ``delegate``/``spawn_agent`` start under a mission root, grandchildren
#: included. Deliberately NOT ``MISSION_ORIGIN``: ``GET /missions`` lists rows
#: by origin EQUALITY, so a teammate stamped ``job:mission`` would show up as
#: an objective of its own, and the "objective finished" ping would fire once
#: per teammate. Its ``job`` prefix is in BOTH ``runtime.ASKING_ORIGINS`` and
#: ``ATTENDED_ORIGINS``, so a teammate's ask WAITS for the user under the
#: teammate's own session id (the bell, the desktop ask watcher and the
#: mission view all read asks by session id) — never an origin the allowlist
#: has not heard of (the v1.231.0 rule).
MISSION_MEMBER_ORIGIN = "job:mission-member"

#: Both mission origins: a run that carries either is part of a mission.
MISSION_ORIGINS = (MISSION_ORIGIN, MISSION_MEMBER_ORIGIN)


def is_mission_origin(origin: Any) -> bool:
    """Is ``origin`` a mission coordinator's or a mission teammate's?"""
    return str(origin or "").strip() in MISSION_ORIGINS


def mission_root(engine: Any, session_id: str | None, agent_run_id: str | None = None):
    """The ROOT session of the team ``session_id`` works in (v1.309.0), as a
    DETACHED row — the caller's own row when it is the root (or when nothing
    links it to one), ``None`` when the caller has no row. BLOCKING (SQLite);
    callers hop off the loop. Never raises.

    Walks ``AgentRun.parent_id`` — the edge ``delegate``/``spawn_agent``
    record — from ``agent_run_id`` (else any of the session's own runs that
    has a parent) up to the run with none; its session is the root. Children
    never inherit run OPTIONS (CLAUDE.md, v1.307.0: that keeps ``deliverable``
    from leaking), so a project mission's TEAM lives on the root only and
    every door that enforces it — ``delegate``, ``spawn_agent``, ``consult`` —
    reads it from here, one walk for all three.
    """
    sid = str(session_id or "")
    if not sid:
        return None
    try:
        from sqlmodel import select

        from ..core.db import session_scope
        from ..core.models import AgentRun, Session

        with session_scope(engine) as db:
            caller = db.get(Session, sid)
            if caller is None:
                return None
            start = db.get(AgentRun, agent_run_id) if agent_run_id else None
            if start is None or start.session_id != sid:
                # No usable run handle (chat-lane calls carry none; a test
                # names one that was never persisted): any of this session's
                # runs that hangs off a parent links it into the team.
                start = db.exec(
                    select(AgentRun).where(
                        AgentRun.session_id == sid,
                        AgentRun.parent_id.is_not(None),  # type: ignore[union-attr]
                    )
                ).first()
            root_sid = sid
            current = start
            seen: set[str] = set()
            while current is not None and current.id not in seen and len(seen) < 100:
                seen.add(current.id)
                root_sid = current.session_id or root_sid
                parent = getattr(current, "parent_id", None)
                current = db.get(AgentRun, parent) if parent else None
            row = caller if root_sid == sid else (db.get(Session, root_sid) or caller)
            return Session(**row.model_dump())
    except Exception:  # noqa: BLE001 — an unreadable chain is no team, no root
        return None


def team_for_session(
    engine: Any, session_id: str | None, agent_run_id: str | None = None
) -> list[str]:
    """The project TEAM a caller is held to (v1.309.0): the mission root's
    ``options.team`` (:func:`mission_root`), or the caller's own when it is
    the root. ``[]`` = unrestricted. BLOCKING; never raises."""
    root = mission_root(engine, session_id, agent_run_id)
    return mission_team(root) if root is not None else []


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


#: child session id -> the asyncio Task running that child (v1.309.0). The
#: ONE handle Stop needs, kept apart from ``_LIVE`` (which is display data,
#: copied out to readers) and — as above — never in ``Orchestrator._running``:
#: this grants no slot and feeds no slot-free hook; it only lets
#: ``cancel_session`` end THIS child's run.
_HANDLES: dict[str, Any] = {}
#: Children the USER stopped and whose door has not yet read that fact.
_STOPPED: set[str] = set()


def bind_task(child_session_id: str, task: Any) -> None:
    """Record the task running ``child_session_id`` (inside :func:`working`,
    which drops it on the way out)."""
    sid = str(child_session_id or "")
    if sid and task is not None:
        with _LOCK:
            _HANDLES[sid] = task


def stop_child(child_session_id: str) -> bool:
    """STOP ONE TEAMMATE (v1.309.0): cancel the task running
    ``child_session_id``, on that task's OWN loop (the cancel route is a sync
    ``def`` — FastAPI's threadpool — so this is usually cross-thread, the
    v1.231.0 AE2 rule). Marks the child stopped FIRST, so the delegating door
    can tell "the user stopped my teammate" (carry on) from "my own run was
    cancelled" (propagate). False when no live task runs that child.

    The caller writes the row CANCELLED before calling this — the door reads
    the row as the second witness."""
    sid = str(child_session_id or "")
    with _LOCK:
        task = _HANDLES.get(sid)
        if task is None or task.done():
            return False
        _STOPPED.add(sid)
    try:
        loop = task.get_loop()
        try:
            here = asyncio.get_running_loop()
        except RuntimeError:
            here = None
        if loop is here:
            task.cancel()
        else:
            loop.call_soon_threadsafe(task.cancel)
        return True
    except Exception:  # noqa: BLE001 — a closed loop: the run is already over
        return False


def consume_stop(child_session_id: str) -> bool:
    """Did the user stop ``child_session_id``? Answers once (the mark is
    removed), so the set never outlives the delegation that reads it."""
    sid = str(child_session_id or "")
    with _LOCK:
        if sid in _STOPPED:
            _STOPPED.discard(sid)
            return True
    return False


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
                _HANDLES.pop(sid, None)
            try:
                if hub is not None:
                    hub.unlink(sid)
            except Exception:  # noqa: BLE001
                pass
