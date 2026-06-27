"""Blackboard tools (Departments substrate).

Three low-risk, user-visible tools let collaborating sibling agents work as a
standing team instead of only summarizing upward:

* ``blackboard_post`` — post a finding (optionally directed at a teammate).
* ``blackboard_read`` — read the department board (optionally only new / to-me).
* ``message_agent``   — send a directed message to a sibling agent.

Board scope and author are derived from the running :class:`ToolContext`:
``board_id`` from the agent's root session (so siblings share one board) and
``author`` from ``agent_run_id``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..tools.base import Tool, ToolContext, ToolResult
from .models import BlackboardKind, BlackboardRecord
from .store import BlackboardStore


def _parse_since(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _render(records: list[BlackboardRecord]) -> str:
    if not records:
        return "(blackboard is empty)"
    lines = []
    for r in records:
        tag = f" -> {r.to_agent}" if r.to_agent else ""
        lines.append(
            f"[{r.created_at.isoformat()}] {r.kind.value} {r.author}{tag}: {r.text}"
        )
    return "\n".join(lines)


def _to_view(records: list[BlackboardRecord]) -> list[dict[str, Any]]:
    return [
        {
            "id": r.id,
            "author": r.author,
            "kind": r.kind.value,
            "to_agent": r.to_agent,
            "text": r.text,
            "created_at": r.created_at.isoformat(),
        }
        for r in records
    ]


class BlackboardPostTool(Tool):
    name = "blackboard_post"
    description = (
        "Post a finding to your department's shared blackboard so sibling agents "
        "can see it. Args: text (the note) and an optional to_agent (a teammate's "
        "id) to direct the note at one teammate."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "to_agent": {"type": "string"},
        },
        "required": ["text"],
    }
    permission_key = "blackboard_post"

    def __init__(self, store: BlackboardStore) -> None:
        self.store = store

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        text = (args.get("text") or "").strip()
        if not text:
            return ToolResult(ok=False, error="`text` is required")
        to_agent = (args.get("to_agent") or "").strip() or None
        board_id = self.store.board_id_for(ctx.session_id, ctx.agent_run_id)
        record = self.store.post(
            board_id,
            ctx.agent_run_id,
            text,
            kind=BlackboardKind.NOTE,
            to_agent=to_agent,
        )
        return ToolResult(
            ok=True,
            output=f"Posted to blackboard {board_id} as {ctx.agent_run_id}.",
            data={"id": record.id, "board_id": board_id, "to_agent": to_agent},
        )


class BlackboardReadTool(Tool):
    name = "blackboard_read"
    description = (
        "Read your department's shared blackboard — the findings and messages "
        "posted by you and your sibling agents. Args: optional since (ISO "
        "timestamp; return only newer entries) and to_me (true to return only "
        "entries addressed to you)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "since": {"type": "string"},
            "to_me": {"type": "boolean"},
        },
    }
    permission_key = "blackboard_read"

    def __init__(self, store: BlackboardStore) -> None:
        self.store = store

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        board_id = self.store.board_id_for(ctx.session_id, ctx.agent_run_id)
        since = _parse_since(args.get("since"))
        to_agent = ctx.agent_run_id if args.get("to_me") else None
        records = self.store.list(board_id, since=since, to_agent=to_agent)
        # The roster lets a sibling DISCOVER teammates (by id + handle) so it can
        # `message_agent` one directly — the headline "address each other" needs
        # this, since a child otherwise can't know its siblings' run ids.
        roster = self.store.roster(board_id)
        teammates = [r for r in roster if r["agent_run_id"] != ctx.agent_run_id]
        out = _render(records)
        if teammates:
            out += "\n\nTeammates you can message_agent: " + ", ".join(
                f"{t['handle']}={t['agent_run_id']}" for t in teammates
            )
        return ToolResult(
            ok=True,
            output=out,
            data={
                "board_id": board_id,
                "records": _to_view(records),
                "roster": roster,
                "you": ctx.agent_run_id,
            },
        )


class MessageAgentTool(Tool):
    name = "message_agent"
    description = (
        "Send a directed message to a sibling agent on your department board. "
        "Args: to_agent (the teammate's id, e.g. a child run id returned by "
        "delegate/spawn_agent) and text (the message)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "to_agent": {"type": "string"},
            "text": {"type": "string"},
        },
        "required": ["to_agent", "text"],
    }
    permission_key = "message_agent"

    def __init__(self, store: BlackboardStore) -> None:
        self.store = store

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        to_agent = (args.get("to_agent") or "").strip()
        text = (args.get("text") or "").strip()
        if not to_agent:
            return ToolResult(ok=False, error="`to_agent` is required")
        if not text:
            return ToolResult(ok=False, error="`text` is required")
        board_id = self.store.board_id_for(ctx.session_id, ctx.agent_run_id)
        record = self.store.post(
            board_id,
            ctx.agent_run_id,
            text,
            kind=BlackboardKind.MESSAGE,
            to_agent=to_agent,
        )
        return ToolResult(
            ok=True,
            output=f"Sent message to {to_agent} on blackboard {board_id}.",
            data={"id": record.id, "board_id": board_id, "to_agent": to_agent},
        )


def blackboard_tools(store: BlackboardStore) -> list[Tool]:
    """Build the blackboard tool set bound to ``store``."""
    return [
        BlackboardPostTool(store),
        BlackboardReadTool(store),
        MessageAgentTool(store),
    ]
