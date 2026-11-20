"""Leap 06-a: the rehearsal record — one row per press of Rehearse.

A rehearsal's REPORT is never stored here: it is built from the ledger's
rehearsed rows every time it is read (``tools/rehearsal_runs.report``), so
what the card says is what the ledger says. This row only remembers what was
rehearsed, how it is going, and whether the user kept it.

Every method is BLOCKING (SQLite); async callers hop it off the loop.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlmodel import Field, SQLModel

from ..core.db import session_scope
from ..core.ids import new_id, utcnow

#: What can be rehearsed (06-a: rules and schedules; 06-b adds the rest).
SUBJECT_KINDS = ("rule", "schedule", "workflow", "goal")
#: ``running`` until its run ends; then ``ready`` (a report to read),
#: ``failed`` (it could not run; ``summary_json.error`` says why) or
#: ``stopped`` (the user pressed Stop).
STATUSES = ("running", "ready", "failed", "stopped")


class RehearsalRecord(SQLModel, table=True):
    __tablename__ = "rehearsal"

    id: str = Field(default_factory=lambda: new_id("reh"), primary_key=True)
    subject_kind: str = Field(default="rule", index=True)
    subject_id: str = Field(default="", index=True)
    subject_name: str = ""
    status: str = "running"
    #: The runs this rehearsal started (a helper's run is found by its
    #: options, ``rehearsal_runs.sessions_of``).
    session_ids_json: str = "[]"
    run_id: str = ""
    #: What it ran on: ``{"text": …}`` for a rule ("(sample)" when no real
    #: signal was seen since the daemon started), ``{}`` for a schedule.
    sample_json: str = "{}"
    #: ``{"error": …}`` when it could not run, ``{"detail": …}`` otherwise.
    summary_json: str = "{}"
    kept_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None


def _loads(raw: Any, default: Any) -> Any:
    try:
        out = json.loads(raw) if isinstance(raw, str) and raw else default
    except (TypeError, ValueError):
        return default
    return out if isinstance(out, type(default)) else default


class RehearsalStore:
    def __init__(self, engine: Any) -> None:
        self.engine = engine

    def create(self, *, kind: str, subject_id: str, name: str, sample: dict | None = None) -> RehearsalRecord:
        rec = RehearsalRecord(
            subject_kind=kind if kind in SUBJECT_KINDS else "rule",
            subject_id=str(subject_id or ""),
            subject_name=" ".join(str(name or "").split())[:200],
            sample_json=json.dumps(sample or {}, default=str)[:4000],
        )
        with session_scope(self.engine) as db:
            db.add(rec)
            db.commit()
            db.refresh(rec)
            db.expunge(rec)
        return rec

    def get(self, rehearsal_id: str) -> RehearsalRecord | None:
        with session_scope(self.engine) as db:
            rec = db.get(RehearsalRecord, str(rehearsal_id or ""))
            if rec is not None:
                db.expunge(rec)
            return rec

    def update(self, rehearsal_id: str, **fields: Any) -> RehearsalRecord | None:
        """Set ``fields`` (``status``, ``summary``, ``session_ids``,
        ``kept_at``, ``finished_at``). ``summary`` / ``session_ids`` are
        stored as JSON."""
        with session_scope(self.engine) as db:
            rec = db.get(RehearsalRecord, str(rehearsal_id or ""))
            if rec is None:
                return None
            for key, value in fields.items():
                if key == "summary":
                    rec.summary_json = json.dumps(value or {}, default=str)[:4000]
                elif key == "session_ids":
                    rec.session_ids_json = json.dumps([str(s) for s in (value or []) if s])
                elif key == "status" and value in STATUSES:
                    rec.status = value
                elif key in ("kept_at", "finished_at", "run_id"):
                    setattr(rec, key, value)
            db.add(rec)
            db.commit()
            db.refresh(rec)
            db.expunge(rec)
            return rec

    @staticmethod
    def sessions(rec: RehearsalRecord) -> list[str]:
        return [str(s) for s in _loads(rec.session_ids_json, []) if s]

    @staticmethod
    def summary(rec: RehearsalRecord) -> dict[str, Any]:
        return _loads(rec.summary_json, {})

    @staticmethod
    def sample(rec: RehearsalRecord) -> dict[str, Any]:
        return _loads(rec.sample_json, {})

    def stamp_subject(self, rec: RehearsalRecord, when: datetime) -> bool:
        """Keep it: stamp ``rehearsed_at`` on the rule or schedule."""
        if rec.subject_kind == "rule":
            from ..reflex.models import ReflexRule as model
        elif rec.subject_kind == "schedule":
            from ..scheduling.models import ScheduledTaskRecord as model
        else:
            return False
        with session_scope(self.engine) as db:
            row = db.get(model, rec.subject_id)
            if row is None:
                return False
            row.rehearsed_at = when
            db.add(row)
            db.commit()
        return True


def now() -> datetime:
    return utcnow()
