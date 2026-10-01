"""``assign_work`` — an agent hands a job to another agent's queue (v1.296.0).

Unlike ``spawn_agent``/``delegate`` (which RUN a child now and wait for it),
this QUEUES: the assignment waits until the named agent is free, and the
caller gets back an id and one sentence. The same targets are refused as at
``AssignmentStore.create`` (unknown, supervisor, a definition carrying
``delegate``, a remote); the nesting depth rides the caller's own
assignment (an assignment run queueing an assignment is depth 2, the cap),
so a chain of agents cannot queue work for each other without bound.

PERMISSION TIER: the same as ``spawn_agent`` — ``ask`` by default (seeded in
``platform.py`` beside the registration), never ``allow``: queueing a run
spends a whole session of someone's allowance. NOT on any builtin roster
(the supervisor and planner already delegate — ``_COLLAB_TOOLS`` sits on
both, so this is not added there); a custom agent opts in through its tool
list.
"""

from __future__ import annotations

import logging
from typing import Any

from ..tools.base import Tool, ToolContext, ToolResult

log = logging.getLogger("iron_jarvis.assignments.tools")

ASSIGNMENT_ORIGIN_PREFIX = "assignment:"


def caller_assignment_depth(engine, session_id: str) -> int:
    """0 when the calling session is not itself an assignment run; else that
    assignment's ``depth``. Never raises — an unreadable caller is depth 0."""
    try:
        from sqlmodel import select

        from ..core.db import session_scope
        from ..core.models import Session
        from .models import AssignmentRecord

        sid = str(session_id or "")
        if not sid or engine is None:
            return 0
        with session_scope(engine) as db:
            row = db.get(Session, sid)
            origin = str(getattr(row, "origin", "") or "") if row is not None else ""
            if not origin.startswith(ASSIGNMENT_ORIGIN_PREFIX):
                return 0
            asg = db.get(AssignmentRecord, origin[len(ASSIGNMENT_ORIGIN_PREFIX):])
            return int(getattr(asg, "depth", 0) or 0) if asg is not None else 0
    except Exception:  # noqa: BLE001
        log.debug("caller depth read failed", exc_info=True)
        return 0


class AssignWorkTool(Tool):
    name = "assign_work"
    description = (
        "Queue a job for another agent. It does NOT run now: the assignment waits "
        "until that agent is free, then runs as its own session. Use it for work "
        "that can happen later; use spawn_agent when you need the result in this "
        "run. Args: agent (a builtin type like 'builder' or a custom agent's "
        "name), task (self-contained instructions), reason (one line: why), "
        "priority (higher first, default 0), project_id (optional)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "agent": {"type": "string"},
            "task": {"type": "string"},
            "reason": {"type": "string"},
            "priority": {"type": "integer"},
            "project_id": {"type": "string"},
        },
        "required": ["agent", "task"],
    }
    permission_key = "assign_work"

    def __init__(self, platform) -> None:
        self.platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        import asyncio

        store = getattr(self.platform, "assignments", None)
        if store is None:
            return ToolResult(ok=False, output="", error="assignments are not available")
        agent = str(args.get("agent") or "").strip()
        task = str(args.get("task") or "")
        reason = str(args.get("reason") or "")
        try:
            priority = int(args.get("priority") or 0)
        except (TypeError, ValueError):
            priority = 0
        project_id = str(args.get("project_id") or "") or str(ctx.project_id or "")
        engine = getattr(self.platform, "engine", None)
        depth = await asyncio.to_thread(caller_assignment_depth, engine, ctx.session_id) + 1
        try:
            record, created = await asyncio.to_thread(
                store.create,
                agent,
                task,
                project_id=project_id,
                source=f"agent:{ctx.session_id}",
                reason=reason,
                priority=priority,
                depth=depth,
            )
        except ValueError as exc:
            return ToolResult(ok=False, output="", error=str(exc))
        disp = getattr(self.platform, "assignment_dispatcher", None)
        if disp is not None:
            try:
                disp.wake()
            except Exception:  # noqa: BLE001
                pass
        who = record.assignee
        return ToolResult(
            ok=True,
            output=f"{record.id}: queued for {who} — it runs when {who} is free",
            data={
                "assignment_id": record.id,
                "assignee": who,
                "created": bool(created),
                "depth": int(record.depth or 0),
            },
        )
