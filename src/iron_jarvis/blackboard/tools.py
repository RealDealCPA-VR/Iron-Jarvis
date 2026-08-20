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
        # Names, not run ids: the identity a teammate can be addressed BY is the
        # useful one to read. Legacy rows carry no name and fall back to the id.
        who = (r.author_name or "") or r.author
        to = (r.to_name or "") or (r.to_agent or "")
        tag = f" -> {to}" if to else ""
        lines.append(
            f"[{r.created_at.isoformat()}] {r.kind.value} {who}{tag}: {r.text}"
        )
    return "\n".join(lines)


def _to_view(records: list[BlackboardRecord]) -> list[dict[str, Any]]:
    return [
        {
            "id": r.id,
            "author": r.author,
            "author_name": r.author_name or "",
            "kind": r.kind.value,
            "to_agent": r.to_agent,
            "to_name": r.to_name or "",
            "text": r.text,
            "created_at": r.created_at.isoformat(),
        }
        for r in records
    ]


def _addressable(roster: list[dict[str, Any]], me: str) -> str:
    """The teammates this agent could have addressed, ``name=run_id``."""
    others = [r for r in roster if r.get("agent_run_id") != me]
    if not others:
        return "(nobody else is on this board yet — delegate/spawn a teammate first)"
    return ", ".join(f"{r.get('handle')}={r.get('agent_run_id')}" for r in others)


def _resolve_recipient(
    store: BlackboardStore, board_id: str, wanted: str, me: str
) -> tuple[str, str, str]:
    """``(run_id, name, error)`` for a recipient the model typed.

    A typo used to be written straight into ``to_agent``, producing a row NO
    ONE could ever read, with no error and no bounce. A refusal that LISTS the
    addressable teammates is the whole fix: the model can retry with a name it
    can actually see.

    ``me`` is passed through so the caller is never its own candidate — the
    resolver and :func:`_addressable` must agree on who "the teammates" are, or
    a refusal lists a run id that resolves back to the sender. The roster is
    fetched ONCE and reused for both the resolution and the refusal text.
    """
    roster = store.roster(board_id)
    run_id, name, candidates = store.resolve_addressee(
        board_id, wanted, me=me, roster=roster
    )
    if run_id:
        return run_id, name, ""
    if len(candidates) > 1:
        listed = ", ".join(
            f"{c.get('handle')}={c.get('agent_run_id')}" for c in candidates
        )
        return (
            "",
            "",
            f"'{wanted}' is ambiguous on this board — {len(candidates)} teammates "
            f"share that name: {listed}. Nothing was posted; re-send addressing "
            "the exact run id you want.",
        )
    return (
        "",
        "",
        f"no teammate '{wanted}' on this board. Addressable teammates: "
        f"{_addressable(roster, me)}. Nothing was posted — "
        "address a teammate by NAME (e.g. 'builder') or by their exact run id.",
    )


class BlackboardPostTool(Tool):
    name = "blackboard_post"
    description = (
        "Post a finding to your department's shared blackboard so sibling agents "
        "can see it. Args: text (the note) and an optional to_agent — a "
        "teammate's NAME exactly as blackboard_read's roster lists it (e.g. "
        "'builder', 'researcher') or their exact run id — to direct the note "
        "at one teammate."
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
        wanted = (args.get("to_agent") or "").strip()
        board_id = self.store.board_id_for(ctx.session_id, ctx.agent_run_id)
        to_agent: str | None = None
        to_name: str | None = None
        if wanted:
            # Same rule as message_agent: a direction nobody can read is worse
            # than a refusal, so an unresolvable name bounces WITH the roster.
            to_agent, to_name, error = _resolve_recipient(
                self.store, board_id, wanted, ctx.agent_run_id
            )
            if error:
                return ToolResult(ok=False, error=error)
        author_name = self.store.name_for(ctx.agent_run_id)
        record = self.store.post(
            board_id,
            ctx.agent_run_id,
            text,
            kind=BlackboardKind.NOTE,
            to_agent=to_agent,
            author_name=author_name,
            to_name=to_name,
        )
        return ToolResult(
            ok=True,
            output=f"Posted to blackboard {board_id} as "
            f"{author_name or ctx.agent_run_id}.",
            data={
                "id": record.id,
                "board_id": board_id,
                "to_agent": to_agent,
                "to_name": to_name,
                "author_name": author_name,
            },
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
        my_name = self.store.name_for(ctx.agent_run_id)
        to_me = bool(args.get("to_me"))
        # "Addressed to me" means EITHER handle: the run id, or my name on a row
        # that carries no run id. A message sent to "builder" must reach the
        # builder.
        records = self.store.list(
            board_id,
            since=since,
            to_agent=ctx.agent_run_id if to_me else None,
            to_name=my_name if to_me else None,
        )
        # The roster lets a sibling DISCOVER teammates (by name + id) so it can
        # `message_agent` one directly — the headline "address each other" needs
        # this, and it now lists teammates who have NEVER POSTED.
        roster = self.store.roster(board_id)
        teammates = [r for r in roster if r["agent_run_id"] != ctx.agent_run_id]
        out = _render(records)
        if teammates:
            out += "\n\nTeammates you can message_agent (by name, or by id): " + (
                ", ".join(f"{t['handle']}={t['agent_run_id']}" for t in teammates)
            )
        return ToolResult(
            ok=True,
            output=out,
            data={
                "board_id": board_id,
                "records": _to_view(records),
                "roster": roster,
                "you": ctx.agent_run_id,
                "you_name": my_name,
            },
        )


class MessageAgentTool(Tool):
    name = "message_agent"
    description = (
        "Send a directed message to a sibling agent on your department board. "
        "Args: to_agent — the teammate's NAME exactly as listed by "
        "blackboard_read's roster (e.g. 'builder', 'researcher') or their "
        "exact run id — and text (the message). An unknown or ambiguous name is "
        "REFUSED and the reply lists who you can address."
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
        run_id, to_name, error = _resolve_recipient(
            self.store, board_id, to_agent, ctx.agent_run_id
        )
        if error:
            return ToolResult(ok=False, error=error)
        author_name = self.store.name_for(ctx.agent_run_id)
        record = self.store.post(
            board_id,
            ctx.agent_run_id,
            text,
            kind=BlackboardKind.MESSAGE,
            to_agent=run_id,
            author_name=author_name,
            to_name=to_name,
        )
        return ToolResult(
            ok=True,
            output=f"Sent message to {to_name or run_id} ({run_id}) on blackboard "
            f"{board_id}.",
            data={
                "id": record.id,
                "board_id": board_id,
                "to_agent": run_id,
                "to_name": to_name,
                "author_name": author_name,
            },
        )


def blackboard_tools(store: BlackboardStore) -> list[Tool]:
    """Build the blackboard tool set bound to ``store``."""
    return [
        BlackboardPostTool(store),
        BlackboardReadTool(store),
        MessageAgentTool(store),
    ]
