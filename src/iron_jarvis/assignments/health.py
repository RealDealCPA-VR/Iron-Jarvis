"""An agent's health card — last run, last outcome, what waits (v1.296.0).

Pure reads over two tables, never raising: the ``Session`` rows credited to
the roster name (``agent_name == name``; a builtin's own runs may carry
``agent_name == ""`` with ``agent_type == name`` — the v1.193.0 two-signal
rule) and the ``assignment`` rows for that assignee. The roster folds the
result into each entry (``RosterEntry.health``) and derives ``"idle"`` from
it; the Agents page prints it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlmodel import select

from ..core.db import session_scope
from ..core.models import AgentType, Session, SessionStatus

log = logging.getLogger("iron_jarvis.assignments.health")

ERROR_CHARS = 200


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _first_line(text: str, limit: int = ERROR_CHARS) -> str:
    for line in str(text or "").splitlines():
        words = " ".join(line.split())
        if words:
            return words[:limit]
    return ""


_TERMINAL = (SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.CANCELLED)


def _last_session(engine, roster_name: str) -> Session | None:
    """The newest FINISHED session credited to ``roster_name``."""
    name = " ".join(str(roster_name or "").split())
    if engine is None or not name:
        return None
    with session_scope(engine) as db:
        stmt = (
            select(Session)
            .where(Session.agent_name == name, Session.status.in_(_TERMINAL))  # type: ignore[attr-defined]
            .order_by(Session.created_at.desc())  # type: ignore[attr-defined]
            .limit(1)
        )
        row = db.exec(stmt).first()
        if row is None:
            try:
                agent_type = AgentType(name.casefold())
            except ValueError:
                return None
            stmt = (
                select(Session)
                .where(
                    Session.agent_type == agent_type,
                    Session.agent_name == "",
                    Session.status.in_(_TERMINAL),  # type: ignore[attr-defined]
                )
                .order_by(Session.created_at.desc())  # type: ignore[attr-defined]
                .limit(1)
            )
            row = db.exec(stmt).first()
        if row is not None:
            db.expunge(row)
        return row


def _outcome_word(session: Session) -> str | None:
    status = session.status
    if status is SessionStatus.FAILED:
        return "failed"
    if status is SessionStatus.CANCELLED:
        return "cancelled"
    if status is SessionStatus.COMPLETED:
        outcome = str(getattr(session, "outcome", "") or "")
        if outcome in ("completed", "completed_with_failures", "needs_you"):
            return outcome
        return "completed"
    return None


def agent_health(engine, store: Any, roster_name: str) -> dict[str, Any]:
    """``{last_run_at, last_outcome, last_error, last_wake_at, queued,
    running, blocked}`` for one roster name. Never raises — a field that
    cannot be read is None/0."""
    out: dict[str, Any] = {
        "last_run_at": None,
        "last_outcome": None,
        "last_error": "",
        "last_wake_at": None,
        "queued": 0,
        "running": 0,
        "blocked": 0,
    }
    try:
        last = _last_session(engine, roster_name)
        if last is not None:
            out["last_run_at"] = _iso(last.finished_at or last.created_at)
            out["last_outcome"] = _outcome_word(last)
            if last.status is SessionStatus.FAILED:
                out["last_error"] = _first_line(last.summary or "")
    except Exception:  # noqa: BLE001
        log.debug("health: session read failed for %s", roster_name, exc_info=True)
    if store is None:
        return out
    try:
        out.update(store.counts(roster_name))
    except Exception:  # noqa: BLE001
        log.debug("health: assignment counts failed for %s", roster_name, exc_info=True)
    try:
        from .models import CLAIMED, QUEUED

        rows = store.list(assignee=roster_name, limit=25)
        newest_start: datetime | None = None
        for row in rows:
            started = row.started_at
            if started is not None:
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                if newest_start is None or started > newest_start:
                    newest_start = started
        out["last_wake_at"] = _iso(newest_start)
        if not out["last_error"]:
            for row in rows:  # newest first; the last settled job's error
                if row.status in (QUEUED, CLAIMED):
                    continue
                if row.last_error:
                    out["last_error"] = _first_line(row.last_error)
                break
    except Exception:  # noqa: BLE001
        log.debug("health: assignment read failed for %s", roster_name, exc_info=True)
    return out
