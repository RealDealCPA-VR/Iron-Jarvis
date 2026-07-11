"""TX-01 time-travel routes: list undoable actions and reverse one.

``GET /undo``           — recent reversible, not-yet-undone actions (newest first),
                          joining the UndoJournal inverse to its ToolInvocation.
``POST /undo/{id}``     — replay a captured inverse through the SAME tool +
                          PermissionEngine + fs policy as the forward mutation,
                          then mark the action undone AND write the undo itself
                          into the ledger (a new ToolInvocation with ``undo_of``)
                          + publish ``action.reverted``.

Moved into routes/ like the other domains; closure-local state is reached
through ``d`` (see the deps object built in create_app).
"""

from __future__ import annotations

import json
import logging

from fastapi import FastAPI, HTTPException
from pathlib import Path
from typing import Any

from sqlmodel import select

from ...core.config import restore_config_values
from ...core.db import session_scope
from ...core.events import EventType
from ...core.ids import new_id, utcnow
from ...core.models import PermissionMode, Session, ToolInvocation, UndoJournal
from ...tools.base import Reversibility, ToolContext
from ...tools.undo import RevertConflict

logger = logging.getLogger("iron_jarvis.undo")


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""

    @app.get("/undo")
    def list_undoable(limit: int = 100) -> dict[str, Any]:
        """Recent reversible actions still eligible for undo (newest first)."""
        engine = d.platform.engine
        limit = max(1, min(int(limit or 100), 500))
        items: list[dict[str, Any]] = []
        with session_scope(engine) as db:
            rows = db.exec(
                select(UndoJournal, ToolInvocation)
                .where(UndoJournal.action_id == ToolInvocation.id)
                .where(UndoJournal.reversible == True)  # noqa: E712
                .where(ToolInvocation.undone_at == None)  # noqa: E711
                .order_by(ToolInvocation.created_at.desc())
                .limit(limit)
            ).all()
            for journal, inv in rows:
                rev = (inv.reversibility or "").lower()
                items.append(
                    {
                        "action_id": inv.id,
                        "session_id": inv.session_id,
                        "tool": inv.tool,
                        "kind": journal.kind,
                        "reversible": bool(journal.reversible),
                        "reversibility": inv.reversibility,
                        # eligible to undo right now: recorded reversible AND the
                        # tool didn't declare itself irreversible AND not yet undone
                        # (the query already excludes undone rows).
                        "undoable": bool(journal.reversible)
                        and rev != Reversibility.IRREVERSIBLE.value,
                        "output": (inv.output or "")[:200],
                        "created_at": inv.created_at.isoformat()
                        if inv.created_at
                        else None,
                    }
                )
        return {"actions": items}

    @app.post("/undo/{action_id}")
    async def undo_action(action_id: str) -> dict[str, Any]:
        platform = d.platform
        engine = platform.engine

        # 1) Look up the action + its captured inverse. Snapshot the fields we need
        # before the session closes (SQLModel attrs expire after commit).
        with session_scope(engine) as db:
            inv = db.get(ToolInvocation, action_id)
            if inv is None:
                raise HTTPException(status_code=404, detail="unknown action")
            if inv.undone_at is not None:
                raise HTTPException(status_code=409, detail="action already undone")
            journal = db.get(UndoJournal, action_id)
            session = db.get(Session, inv.session_id)
            tool_name = inv.tool
            session_id = inv.session_id
            agent_run_id = inv.agent_run_id
            reversibility = (inv.reversibility or "").lower()
            workspace_path = session.workspace_path if session is not None else ""
            desc: dict[str, Any] = {}
            journal_kind = ""
            if journal is not None:
                journal_kind = journal.kind
                desc = {
                    "kind": journal.kind,
                    "reversible": bool(journal.reversible),
                    "pre_ref": journal.pre_ref,
                    "pre_inline": journal.pre_inline,
                    "pre_sha256": journal.pre_sha256,
                    "post_sha256": journal.post_sha256,
                }
                journal_reversible = bool(journal.reversible)
            else:
                journal_reversible = False

        # 2) Refuse honestly when there is nothing safe to reverse.
        if journal is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "no inverse was captured for this action — it cannot be undone "
                    "(the action predates undo capture, or the capture failed)"
                ),
            )
        if not journal_reversible:
            raise HTTPException(
                status_code=422,
                detail=(
                    "this action was recorded as non-reversible "
                    "(its effect has no safe inverse) and cannot be undone"
                ),
            )
        if reversibility == Reversibility.IRREVERSIBLE.value:
            raise HTTPException(
                status_code=422,
                detail=(
                    "this tool's effect leaves the machine (external send / spend) "
                    "and cannot be undone"
                ),
            )

        # 3) Settings changes are reversed against the live config, not a tool.
        if journal_kind == "setting_restore":
            try:
                prior = json.loads(desc.get("pre_inline") or "{}").get("prior", {})
            except (TypeError, ValueError):
                prior = {}
            updated = restore_config_values(platform.config, prior)
            result_output = f"undo: restored settings {', '.join(updated) or '(none)'}"
        else:
            # 4) Tool-backed revert: same tool, same PermissionEngine + fs policy.
            tool = platform.registry.get(tool_name)
            if tool is None:
                raise HTTPException(
                    status_code=422,
                    detail=f"tool '{tool_name}' is no longer registered — cannot undo",
                )
            decision = platform.permissions.authorize(tool.perm_key(), {})
            if not decision.allowed:
                raise HTTPException(
                    status_code=403,
                    detail=f"permission denied: {decision.reason}",
                )
            workspace = (
                Path(workspace_path)
                if workspace_path
                else platform.config.workspaces_dir / session_id
            )
            ctx = ToolContext(
                workspace=workspace,
                session_id=session_id,
                agent_run_id=agent_run_id,
                config=platform.config,
                event_bus=platform.event_bus,
                engine=engine,
            )
            try:
                result = await tool.revert(desc, ctx)
            except RevertConflict as exc:
                # The target changed since the action — refuse rather than clobber.
                raise HTTPException(status_code=409, detail=str(exc))
            except Exception as exc:  # noqa: BLE001 — a bad revert must not 500
                raise HTTPException(
                    status_code=409, detail=f"undo failed: {type(exc).__name__}: {exc}"
                )
            if not result.ok:
                raise HTTPException(
                    status_code=409, detail=result.error or "undo failed"
                )
            result_output = result.output

        # 5) Mark the action undone, consume the journal, and record the undo AS a
        # first-class ledger entry (undo_of=<original>). Re-fetch fresh rows.
        # NOTE: step 4 already mutated the target on disk. If this bookkeeping
        # transaction fails, that effect is NOT rolled back — so surface the failure
        # loudly (below) rather than let a reverted-but-unrecorded state pass silently.
        undo_inv_id = new_id("tool")
        try:
            with session_scope(engine) as db:
                inv = db.get(ToolInvocation, action_id)
                if inv is None:  # deleted underneath us (race) — nothing to finalize
                    raise HTTPException(status_code=404, detail="unknown action")
                if inv.undone_at is not None:  # a concurrent undo won
                    raise HTTPException(status_code=409, detail="action already undone")
                inv.undone_at = utcnow()
                db.add(inv)
                j = db.get(UndoJournal, action_id)
                if j is not None:
                    j.applied_at = utcnow()
                    db.add(j)
                db.add(
                    ToolInvocation(
                        id=undo_inv_id,
                        session_id=session_id,
                        agent_run_id=agent_run_id,
                        tool=tool_name,
                        args_json="{}",
                        verdict=PermissionMode.ALLOW,
                        ok=True,
                        output=(result_output or "")[:4000],
                        # the undo action itself is not further reversible
                        reversibility=Reversibility.IRREVERSIBLE.value,
                        undo_of=action_id,
                    )
                )
                db.commit()
        except HTTPException:
            raise  # the intended race conditions (404/409) — not an inconsistency
        except Exception as exc:  # noqa: BLE001 — finalize failed AFTER the revert ran
            logger.error(
                "undo %s: effect reverted but ledger finalize failed: %s",
                action_id, exc, exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail=(
                    "the action was reverted on disk but the audit ledger could not "
                    "be updated — check the daemon logs; the timeline may still show "
                    "it as undoable"
                ),
            )

        await platform.event_bus.publish(
            EventType.ACTION_REVERTED,
            {
                "action_id": action_id,
                "undo_invocation_id": undo_inv_id,
                "tool": tool_name,
                "kind": journal_kind,
            },
            session_id=session_id,
        )
        return {
            "undone": action_id,
            "undo_invocation_id": undo_inv_id,
            "tool": tool_name,
            "output": result_output,
        }
