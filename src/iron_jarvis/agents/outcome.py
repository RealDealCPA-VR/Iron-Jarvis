"""What a session ACTUALLY did (v1.149.0) — read out of the ledger, not the prose.

The report this answers: "agents must stop only describing what they intend to
do." Iron Jarvis was never guessing — every tool call, every file mutation and
every failure has been recorded in ``ToolInvocation`` + ``UndoJournal`` since
TX-01. What was missing is that nothing SUMMARISED it back to the person who
asked; the only thing they saw was the model's own closing paragraph, which is
exactly the part that can claim work it never did.

So this module derives the outcome from the RECORD:

* **files created / changed** come from the undo journal's own descriptor —
  ``file_delete`` means the tool created a file (undo unlinks it), and
  ``file_restore`` means it overwrote one (undo puts the old bytes back). That
  is a fact about a mutation that happened, not a sentence a model wrote.
* **tools used, and which of them FAILED**, come from the invocation rows.
* **errors** are the failed rows' own messages, verbatim.
* **revertable** counts the actions that still have a usable inverse, so the UI
  can offer "revert this task" only when it is genuinely possible.

Nothing here is inferred from the reply text. If the model says it wrote a file
and no write is journaled, this reports no file — and that disagreement is the
entire point.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlmodel import select

from ..core.db import session_scope
from ..core.events import EventType
from ..core.models import AgentRun, EventRecord, Session, ToolInvocation, UndoJournal
from ..tools.base import Reversibility

#: The three verdicts ``Session.outcome`` may carry (v1.227.0, audit A5/U1).
#: ``status`` says how the RUN ended; these say whether the JOB was done.
OUTCOME_COMPLETED = "completed"
OUTCOME_WITH_FAILURES = "completed_with_failures"
OUTCOME_NEEDS_YOU = "needs_you"
OUTCOMES = (OUTCOME_COMPLETED, OUTCOME_WITH_FAILURES, OUTCOME_NEEDS_YOU)

#: The decision an ``approval.resolved`` event carries when the clock, not the
#: user, answered (``agents/runtime._pause_for_approval``).
_TIMEOUT_DECISION = "timeout"


def _count_unanswered_asks(db, session_id: str) -> int:
    """How many of this session's asks were answered by the CLOCK.

    Counted from the persisted ``approval.resolved`` events with
    ``decision == "timeout"`` — the same rows the bell and the audit timeline
    read, so this number can never disagree with them. The approvals registry
    itself is deliberately in-memory and holds no history (core/approvals.py),
    which is why the event log is the record here.
    """
    count = 0
    rows = db.exec(
        select(EventRecord.payload_json).where(
            EventRecord.type == EventType.APPROVAL_RESOLVED,
            EventRecord.session_id == session_id,
        )
    )
    for raw in rows:
        raw = raw if isinstance(raw, str) else raw[0]
        try:
            payload = json.loads(raw or "{}")
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("decision") == _TIMEOUT_DECISION:
            count += 1
    return count


def derive_outcome(result: dict[str, Any], status: Any = None) -> str | None:
    """The verdict on the JOB, from a ``session_result`` payload. Pure.

    * ``needs_you`` — any ask for the session resolved ``timeout``. The run
      may read COMPLETED (it ended without an exception) or FAILED; either way
      the job is waiting on the person, and that is the headline.
    * ``completed_with_failures`` — the run completed, nobody was asked and
      left unanswered, and at least one MUTATING tool call failed.
    * ``completed`` — the run completed and nothing above applies.
    * ``None`` — not found, not finished, or a status (failed with no
      unanswered ask, cancelled) that already tells the truth on its own.

    ``status`` overrides the payload's — the orchestrator derives the verdict
    BEFORE it persists the terminal status, when the row still reads active.
    """
    if not result.get("found"):
        return None
    if int(result.get("unanswered_asks") or 0) > 0:
        return OUTCOME_NEEDS_YOU
    status_value = status if status is not None else result.get("status")
    status_value = str(getattr(status_value, "value", status_value) or "").lower()
    if status_value != "completed":
        return None
    if int(result.get("tools_failed_mutating") or 0) > 0:
        return OUTCOME_WITH_FAILURES
    return OUTCOME_COMPLETED


def session_outcome(engine, session_id: str, status: Any = None) -> str | None:
    """``derive_outcome`` over a fresh ledger read — the finalize-time call.
    BLOCKING (SQLite reads); the orchestrator runs it off the loop."""
    return derive_outcome(session_result(engine, session_id), status)

#: Journal kinds that describe a FILE mutation, and what each one means the
#: tool did. (The inverse is the mirror image: to undo a creation you delete.)
_CREATED_KIND = "file_delete"
#: Multi-file creation envelope (v1.166.0): one row, pre_inline {"paths": [...]}.
_CREATED_MANY_KIND = "files_delete"
_CHANGED_KIND = "file_restore"

#: Cap on each list in the payload. A migration touching 4,000 files must not
#: produce a 4,000-row card (or response); the count stays honest via ``*_total``.
_LIST_CAP = 50

#: Per-error text cap — enough to diagnose, bounded enough for a card.
_ERROR_CHARS = 300


def _envelope_path(journal: UndoJournal) -> str:
    """The target path packed into the journal's ``pre_inline`` envelope."""
    try:
        meta = json.loads(journal.pre_inline or "{}")
    except (TypeError, ValueError):
        return ""
    return str(meta.get("path") or "")


def _envelope_paths(journal: UndoJournal) -> list[str]:
    """All target paths in a multi-file ``files_delete`` envelope."""
    try:
        meta = json.loads(journal.pre_inline or "{}")
    except (TypeError, ValueError):
        return []
    paths = meta.get("paths")
    if not isinstance(paths, list):
        return []
    return [str(p) for p in paths if p]


def _rel(path: str, workspace: str) -> str:
    """Workspace-relative when it sits inside one — a card is easier to read as
    ``report.md`` than as a 90-character absolute path. Falls back to the
    absolute path, which is the honest answer for a file outside the workspace
    (and the one the user needs in order to find it)."""
    if not path:
        return ""
    if not workspace:
        return path
    try:
        return str(Path(path).relative_to(Path(workspace)))
    except (ValueError, OSError):
        return path


def session_result(engine, session_id: str) -> dict[str, Any]:
    """The honest outcome of one session.

    Read-only and defensive — this feeds a UI card and an SSE frame, so a
    half-written session or a missing row must degrade to a smaller report, never
    raise. An unknown session id returns ``{"found": False}``.
    """
    out: dict[str, Any] = {
        "found": False,
        "session_id": session_id,
        "status": "",
        "task": "",
        "summary": "",
        "steps": 0,
        "tools_used": [],
        "tools_failed": [],
        "files_created": [],
        "files_changed": [],
        "errors": [],
        "revertable": 0,
        "reverted": 0,
        "duration_s": None,
        # v1.227.0 (A5/U1): the verdict on the JOB and the count of asks the
        # clock answered — see ``derive_outcome``.
        "outcome": None,
        "unanswered_asks": 0,
    }
    try:
        with session_scope(engine) as db:
            session = db.get(Session, session_id)
            if session is None:
                return out
            out["found"] = True
            out["status"] = getattr(session.status, "value", str(session.status))
            out["task"] = (session.task or "")[:400]
            out["summary"] = (session.summary or "")[:2000]
            workspace = session.workspace_path or ""
            stored_outcome = getattr(session, "outcome", None) or None
            out["unanswered_asks"] = _count_unanswered_asks(db, session_id)

            runs = list(
                db.exec(select(AgentRun).where(AgentRun.session_id == session_id))
            )
            out["steps"] = sum(int(getattr(r, "steps", 0) or 0) for r in runs)

            invocations = list(
                db.exec(
                    select(ToolInvocation)
                    .where(ToolInvocation.session_id == session_id)
                    .order_by(ToolInvocation.created_at)  # type: ignore[arg-type]
                )
            )
            journals = {
                j.action_id: j
                for j in db.exec(
                    select(UndoJournal).where(
                        UndoJournal.action_id.in_(  # type: ignore[union-attr]
                            [i.id for i in invocations] or [""]
                        )
                    )
                )
            }

            used: dict[str, int] = {}
            failed: dict[str, int] = {}
            errors: list[dict[str, str]] = []
            created: list[str] = []
            changed: list[str] = []
            revertable = 0
            already_reverted = 0
            mutating_failed = 0

            for inv in invocations:
                # An undo is itself a ledger row; counting it as work the agent
                # did would double-report the action it reversed.
                if inv.undo_of:
                    continue
                name = inv.tool or "(unknown)"
                used[name] = used.get(name, 0) + 1
                if not inv.ok:
                    failed[name] = failed.get(name, 0) + 1
                    # A failed READ is a detour; a failed WRITE (or a denied
                    # one) is work the job needed and did not get. The
                    # registry stamps every row with the tool's declared
                    # reversibility, so "not readonly" is the honest line —
                    # an unstamped legacy row counts as mutating (the
                    # direction that never calls a half-done job complete).
                    if (inv.reversibility or "").lower() != Reversibility.READONLY.value:
                        mutating_failed += 1
                    if len(errors) < _LIST_CAP:
                        errors.append(
                            {"tool": name, "error": (inv.output or "")[:_ERROR_CHARS]}
                        )
                if inv.undone_at is not None:
                    # The action happened AND was later reverted. Both are true,
                    # and the card must say both: the file list still names what
                    # was written (it was), while this count lets the UI add
                    # "already reverted" instead of offering the button again.
                    already_reverted += 1
                journal = journals.get(inv.id)
                if journal is None:
                    continue
                if (
                    journal.reversible
                    and inv.undone_at is None
                    and (inv.reversibility or "").lower()
                    != Reversibility.IRREVERSIBLE.value
                ):
                    revertable += 1
                if journal.kind == _CREATED_MANY_KIND:
                    for p in _envelope_paths(journal):
                        rp = _rel(p, workspace)
                        if rp and rp not in created:
                            created.append(rp)
                    continue
                path = _rel(_envelope_path(journal), workspace)
                if not path:
                    continue
                if journal.kind == _CREATED_KIND and path not in created:
                    created.append(path)
                elif journal.kind == _CHANGED_KIND and path not in changed:
                    changed.append(path)

            # A file both created AND edited in one session is a CREATION — that
            # is what the user needs to know about it; listing it twice reads as
            # two separate things happening.
            changed = [p for p in changed if p not in created]

            out["tools_used"] = [
                {"tool": t, "count": n} for t, n in sorted(used.items())
            ][:_LIST_CAP]
            out["tools_failed"] = [
                {"tool": t, "count": n} for t, n in sorted(failed.items())
            ][:_LIST_CAP]
            out["files_created_total"] = len(created)
            out["files_changed_total"] = len(changed)
            out["files_created"] = created[:_LIST_CAP]
            out["files_changed"] = changed[:_LIST_CAP]
            # ABSOLUTE paths for the same files (v1.155.0), under the key the
            # chat preview already consumes. The lists above are
            # workspace-relative because that is what reads well in a report,
            # but a client cannot open or download a relative path — and an
            # agent session's workspace is a folder no user would guess. Same
            # rule as the tools themselves since v1.153.2: say WHERE, in full.
            out["documents"] = [
                str((Path(workspace) / p).resolve())
                for p in (created + changed)[:_LIST_CAP]
            ] if workspace else []
            out["errors"] = errors
            out["revertable"] = revertable
            out["reverted"] = already_reverted
            out["tools_failed_mutating"] = mutating_failed
            # The stored verdict wins (it was derived at finalize with the
            # status the run actually ended in); a row from before the column
            # existed gets the same derivation live, so an old card is not
            # left blank where a new one would say "needs you".
            out["outcome"] = stored_outcome or derive_outcome(out)
            try:
                # ``finished_at`` is only set once the session ends, so a running
                # session honestly reports no duration rather than a growing one.
                if session.created_at and session.finished_at:
                    out["duration_s"] = max(
                        0.0, (session.finished_at - session.created_at).total_seconds()
                    )
            except Exception:  # noqa: BLE001 — a clock oddity costs the duration only
                out["duration_s"] = None
    except Exception:  # noqa: BLE001 — a result card must never break a turn
        return out
    return out


def did_nothing(result: dict[str, Any]) -> bool:
    """True when a session completed without running a single tool.

    Worth calling out explicitly rather than rendering an empty card: "completed"
    with no actions is exactly the shape of an agent that DESCRIBED the work
    instead of doing it, which is the report this whole wave answers.
    """
    return bool(result.get("found")) and not result.get("tools_used")
