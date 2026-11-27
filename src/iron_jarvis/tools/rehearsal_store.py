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

#: What can be rehearsed (06-a: rules and schedules; 06-b adds the rest;
#: next leaps 19-b: ``offer``, a schedule Iron Jarvis offers on Improvements,
#: rehearsed before it is made).
SUBJECT_KINDS = ("rule", "schedule", "workflow", "goal", "offer")
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
    #: What it ran on, for the report only: ``{"made_up": bool}`` for a rule
    #: (made up when no real signal was seen since the daemon started), ``{}``
    #: for a schedule. Never the signal's words (:func:`sample_record`).
    sample_json: str = "{}"
    #: ``{"error": …}`` when it could not run, ``{"detail": …}`` otherwise.
    summary_json: str = "{}"
    kept_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None


#: The words a rule's rehearsal runs on when no real signal was seen.
MADE_UP_SAMPLE = "(sample)"
#: How much of a summary's words is kept. Each value is cut BEFORE the JSON
#: is written, so the stored text is always whole JSON.
SUMMARY_CHARS = 1000


def _loads(raw: Any, default: Any) -> Any:
    try:
        out = json.loads(raw) if isinstance(raw, str) and raw else default
    except (TypeError, ValueError):
        return default
    return out if isinstance(out, type(default)) else default


def _small(values: Any, limit: int) -> dict[str, str]:
    """``values`` with every value as one line of at most ``limit`` chars."""
    if not isinstance(values, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in list(values.items())[:20]:
        text = " ".join(str(value if value is not None else "").split())
        out[str(key)[:40]] = text[:limit]
    return out


def sample_record(sample: Any) -> dict[str, Any]:
    """What a rehearsal keeps of the signal it ran on: ONLY whether it was
    made up. The signal itself (an email's or a webhook's words) is kept in
    memory only (``ReflexRouter.sample_for``), never written here."""
    if not isinstance(sample, dict) or not sample:
        return {}
    text = " ".join(str(sample.get("text") or "").split())
    body = " ".join(str(sample.get("body") or "").split())
    made_up = text == MADE_UP_SAMPLE or (not text and body in ("", MADE_UP_SAMPLE))
    return {"made_up": made_up}


class RehearsalStore:
    def __init__(self, engine: Any) -> None:
        self.engine = engine

    def create(self, *, kind: str, subject_id: str, name: str, sample: dict | None = None) -> RehearsalRecord:
        rec = RehearsalRecord(
            subject_kind=kind if kind in SUBJECT_KINDS else "rule",
            subject_id=str(subject_id or ""),
            subject_name=" ".join(str(name or "").split())[:200],
            sample_json=json.dumps(sample_record(sample)),
        )
        with session_scope(self.engine) as db:
            db.add(rec)
            db.commit()
            db.refresh(rec)
            db.expunge(rec)
        return rec

    def renew(self, rehearsal_id: str) -> RehearsalRecord:
        """A NEW record for the same subject and signal as ``rehearsal_id``
        (Run again or a follow-up of a rehearsal's run): its own id, so its
        own report, and the old report never changes. A record that is gone
        gives a new one with no subject."""
        old = self.get(rehearsal_id)
        rec = RehearsalRecord(
            subject_kind=old.subject_kind if old is not None else "rule",
            subject_id=old.subject_id if old is not None else "",
            subject_name=old.subject_name if old is not None else "",
            sample_json=old.sample_json if old is not None else "{}",
        )
        with session_scope(self.engine) as db:
            db.add(rec)
            db.commit()
            db.refresh(rec)
            db.expunge(rec)
        return rec

    def newest(self, kind: str, limit: int = 30) -> list[RehearsalRecord]:
        """The newest records of one subject kind, newest first."""
        from sqlmodel import select

        with session_scope(self.engine) as db:
            rows = list(db.exec(
                select(RehearsalRecord)
                .where(RehearsalRecord.subject_kind == kind)
                .order_by(RehearsalRecord.created_at.desc())  # type: ignore[attr-defined]
                .limit(max(1, int(limit)))
            ).all())
            for r in rows:
                db.expunge(r)
        return rows

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
                    rec.summary_json = json.dumps(_small(value, SUMMARY_CHARS))
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

    @staticmethod
    def sample_is_made_up(rec: RehearsalRecord) -> bool:
        """Did a rule's rehearsal run on made-up words (no real signal seen
        since the daemon started)? A record that cannot be read says so."""
        kept = _loads(rec.sample_json, {})
        if isinstance(kept.get("made_up"), bool):
            return kept["made_up"]
        return sample_record(kept).get("made_up", True) is not False

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
