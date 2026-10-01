"""Per-run SIGNALS, read only from the ledger (v1.297.0). No model here.

:func:`run_signals` turns one :class:`~iron_jarvis.core.models.Session` into a
flat dict of facts the taxonomy classifies: the honest outcome
(``agents/outcome.session_result`` — derived from ``ToolInvocation`` and the
approval events, never from prose), the measured score, which tools failed
and how, denials, asks the clock answered, steps against the cap, thumbs-down
feedback, duration, repeated reads of the same file, and whether a later
``/continue`` picked the work up again.

:func:`recent_runs` is the window the coach looks at: the newer of "the last
N runs" and "everything in the last H hours", capped at 25 — so a busy agent
is judged on recent work and a quiet one still has enough history to show a
pattern.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta, timezone
from typing import Any

from sqlmodel import select

from ..agents.outcome import session_result
from ..core.db import session_scope
from ..core.events import EventType
from ..core.ids import utcnow
from ..core.models import AgentRun, EventRecord, PermissionMode, Session, ToolInvocation
from ..detections.redact import mask
from ..improvement.models import OutcomeRecord
from ..learning.models import FeedbackRecord

log = logging.getLogger("iron_jarvis.coach")

#: Hard cap on the runs one report reads (the larger window is bounded).
RUNS_CAP = 25
#: The first error line kept per failed tool.
ERROR_CHARS = 120
#: The run summary kept on a signal.
SUMMARY_CHARS = 300
#: Task text kept on a signal (the quotes cite it).
TASK_CHARS = 200
#: The tools whose argument repeats count as "re-reading the same file".
READ_TOOLS = ("read_file",)
#: The recap line ``Orchestrator.continue_session`` writes into a follow-up
#: session's task — the only ledger trace that links a continuation to its
#: parent (there is no parent column; see ``signals["continued"]``).
CONTINUATION_MARK = "[Continuing an earlier session. Original task: "


def as_naive_utc(moment: Any) -> Any:
    """SQLite hands timestamps back naive; ``utcnow()`` is aware. Compare in
    one convention (naive UTC), like ``agents.allowance._naive``."""
    if moment is None or getattr(moment, "tzinfo", None) is None:
        return moment
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


def _first_line(text: str, limit: int = ERROR_CHARS) -> str:
    """The first non-blank line of a tool's output, whitespace collapsed,
    SECRETS MASKED (``detections.redact.mask``) before the cut: the line
    becomes an evidence quote that rides a proposal row, the bus and the
    model prompt, and a failed shell/web call often echoes its own
    arguments (a token in a URL, a bearer header) into its error."""
    for line in str(text or "").splitlines():
        line = " ".join(line.split())
        if line:
            return str(mask(line))[:limit]
    return ""


def _enum_value(raw: Any) -> str:
    return getattr(raw, "value", raw) if raw is not None else ""


def _read_target(args_json: str) -> str:
    """A stable key for WHAT a read_file call read (its path-ish argument)."""
    try:
        args = json.loads(args_json or "{}")
    except (TypeError, ValueError):
        return (args_json or "").strip()
    if not isinstance(args, dict):
        return json.dumps(args, sort_keys=True, default=str)
    for key in ("path", "file", "filename", "file_path", "target"):
        if args.get(key):
            return str(args[key])
    return json.dumps(args, sort_keys=True, default=str)


def recent_runs(
    platform, roster_name: str, limit: int = 10, since_hours: int = 72
) -> list[Session]:
    """The sessions attributed to ``roster_name`` (``Session.agent_name``),
    newest first: the LARGER of the last ``limit`` runs and every run in the
    last ``since_hours`` hours, capped at :data:`RUNS_CAP`."""
    name = (roster_name or "").strip()
    if not name:
        return []
    limit = max(1, int(limit or 1))
    cutoff = as_naive_utc(utcnow() - timedelta(hours=max(0, int(since_hours or 0))))
    with session_scope(platform.engine) as db:
        rows = list(
            db.exec(
                select(Session)
                .where(Session.agent_name == name)
                .order_by(Session.created_at.desc())  # type: ignore[attr-defined]
                .limit(RUNS_CAP)
            )
        )
        for row in rows:  # detach with every column loaded
            db.refresh(row)
            db.expunge(row)
    in_window = sum(
        1 for r in rows if r.created_at and as_naive_utc(r.created_at) >= cutoff
    )
    keep = min(RUNS_CAP, max(limit, in_window))
    return rows[:keep]


def run_signals(platform, session: Session) -> dict[str, Any]:
    """The facts of ONE run, from the ledger only. Never raises on a thin or
    half-written session — a missing row degrades to zeros, not to a 500."""
    sid = session.id
    engine = platform.engine
    result = session_result(engine, sid)
    config_cap = int(getattr(platform.config, "max_agent_steps", 0) or 0)
    max_steps = int(getattr(session, "max_steps", None) or config_cap or 0)

    steps = 0
    tools_used: list[str] = []
    failed: dict[str, dict[str, Any]] = {}
    denied_ids: set[str] = set()
    read_counts: dict[str, int] = {}
    score: float | None = None
    success: bool | None = None
    down = 0
    denied_events = 0
    continued = False
    with session_scope(engine) as db:
        runs = list(db.exec(select(AgentRun).where(AgentRun.session_id == sid)))
        steps = sum(int(getattr(r, "steps", 0) or 0) for r in runs)
        invocations = list(
            db.exec(
                select(ToolInvocation)
                .where(ToolInvocation.session_id == sid)
                .order_by(ToolInvocation.created_at)  # type: ignore[arg-type]
            )
        )
        for inv in invocations:
            if inv.undo_of:
                continue
            name = inv.tool or "(unknown)"
            if name not in tools_used:
                tools_used.append(name)
            if _enum_value(inv.verdict) == PermissionMode.DENY.value:
                denied_ids.add(inv.id)
            if not inv.ok:
                slot = failed.setdefault(
                    name, {"tool": name, "count": 0, "first_error": ""}
                )
                slot["count"] += 1
                if not slot["first_error"]:
                    slot["first_error"] = _first_line(inv.output)
            if name in READ_TOOLS:
                key = _read_target(inv.args_json)
                read_counts[key] = read_counts.get(key, 0) + 1
        outcome_row = db.exec(
            select(OutcomeRecord)
            .where(OutcomeRecord.session_id == sid)
            .order_by(OutcomeRecord.created_at.desc())  # type: ignore[attr-defined]
        ).first()
        if outcome_row is not None:
            score = float(outcome_row.score)
            success = bool(outcome_row.success)
        down = len(
            list(
                db.exec(
                    select(FeedbackRecord.id).where(
                        FeedbackRecord.session_id == sid,
                        FeedbackRecord.rating == "down",
                    )
                )
            )
        )
        # Denials: the DENY verdict rows plus any tool.denied event whose
        # invocation row is not already counted (the registry writes both for
        # one refusal — count the refusal once).
        for raw in db.exec(
            select(EventRecord.payload_json).where(
                EventRecord.type == EventType.TOOL_DENIED,
                EventRecord.session_id == sid,
            )
        ):
            raw = raw if isinstance(raw, str) else raw[0]
            try:
                payload = json.loads(raw or "{}")
            except (TypeError, ValueError):
                payload = {}
            inv_id = payload.get("invocation_id") if isinstance(payload, dict) else None
            if inv_id and inv_id in denied_ids:
                continue
            denied_events += 1
        # A later /continue: the orchestrator writes the parent's task into the
        # follow-up's recap line; that line is the link (no parent column).
        if session.task:
            mark = CONTINUATION_MARK + repr(session.task)
            later = db.exec(
                select(Session.id).where(
                    Session.id != sid,
                    Session.created_at >= session.created_at,
                    Session.task.contains(mark),  # type: ignore[attr-defined]
                )
            ).first()
            continued = later is not None

    status = _enum_value(session.status)
    outcome = result.get("outcome") or getattr(session, "outcome", None) or None
    return {
        "session_id": sid,
        "agent": session.agent_name or "",
        "task": (session.task or "")[:TASK_CHARS],
        "status": status,
        "outcome": outcome,
        "score": score,
        "success": success,
        "tools_used": tools_used,
        "tools_failed": sorted(failed.values(), key=lambda f: (-f["count"], f["tool"])),
        "denials": len(denied_ids) + denied_events,
        "unanswered_asks": int(result.get("unanswered_asks") or 0),
        "steps": steps,
        "max_steps": max_steps,
        "down_feedback": down,
        "duration_s": result.get("duration_s"),
        "summary": str(mask(session.summary or ""))[:SUMMARY_CHARS],
        "read_repeats": max(read_counts.values()) if read_counts else 0,
        "continued": continued,
        "created_at": session.created_at.isoformat() if session.created_at else None,
    }
