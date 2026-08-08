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
from ..core.models import AgentRun, Session, ToolInvocation, UndoJournal
from ..tools.base import Reversibility

#: Journal kinds that describe a FILE mutation, and what each one means the
#: tool did. (The inverse is the mirror image: to undo a creation you delete.)
_CREATED_KIND = "file_delete"
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

            for inv in invocations:
                # An undo is itself a ledger row; counting it as work the agent
                # did would double-report the action it reversed.
                if inv.undo_of:
                    continue
                name = inv.tool or "(unknown)"
                used[name] = used.get(name, 0) + 1
                if not inv.ok:
                    failed[name] = failed.get(name, 0) + 1
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
            out["errors"] = errors
            out["revertable"] = revertable
            out["reverted"] = already_reverted
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
