"""``checks_list``: "what did you check?", answered from the record.

Read only: it changes nothing and runs no check again. It reads ONLY the
checks of the chat it was called in (the saved chat's, or, in a new chat not
saved yet, this reply's own), or of the run it was called in. ``run`` names
an agent run started from this chat (or one that belongs to no chat); a run
of another chat is refused. Never another chat's rows: there is no argument
that names a chat.

The answer is words built from the record's rows (the same line the page
shows, then each check with its result and who checked), handed to the model
as data.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..tools.base import Reversibility, Tool, ToolContext, ToolResult
from . import record

TOOL_NAME = "checks_list"
#: How many of a chat's newest checks one answer reads.
MAX_ROWS = 60
NOTHING = (
    "Iron Jarvis's record holds no check for this chat yet. It checks a reply when the reply ends: whether "
    "what it says matches what it did, and, when it made or changed a file, what that file holds."
)
OTHER_CHAT = "That run belongs to another chat, so its checks are not read here."


def _owner_rows(engine: Any, ctx: ToolContext, run: str) -> "tuple[list[dict[str, Any]], str]":
    """BLOCKING. ``(rows, refusal)`` for the caller: never another chat's."""
    thread = str(getattr(ctx, "thread_id", "") or "")
    session = str(getattr(ctx, "session_id", "") or "")
    if run:
        rows = record.for_session(engine, run)
        mine = [r for r in rows if r.get("thread_id") in ("", thread)]
        if rows and not mine:
            return [], OTHER_CHAT
        return mine, ""
    if session and session != "chat":
        return record.for_session(engine, session), ""
    if thread:
        return list(reversed(record.for_thread(engine, thread, limit=MAX_ROWS))), ""
    return record.for_turn(engine, str(getattr(ctx, "turn_id", "") or "")), ""


def _groups(rows: "list[dict[str, Any]]") -> "list[list[dict[str, Any]]]":
    """Rows grouped by the reply or run they belong to, oldest group first."""
    out: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault((str(r.get("turn_id") or ""), str(r.get("session_id") or ""),
                        "" if r.get("turn_id") or r.get("session_id") else str(r.get("run_id") or "")), []).append(r)
    return list(out.values())


def answer(rows: "list[dict[str, Any]]") -> str:
    """The rows as words: per reply or run, its line, then each check."""
    if not rows:
        return NOTHING
    said: list[str] = ["What Iron Jarvis checked (its own record, oldest first):"]
    for group in _groups(rows):
        when = str(group[0].get("at") or "")[:16].replace("T", " ")
        owner = "A run" if group[0].get("session_id") and not group[0].get("turn_id") else "A reply"
        line, _tone = record.line_for(group)
        said.append(f"{owner} ({when} UTC): {line}" if record.has_content(group) else f"{owner} ({when} UTC):")
        shown = record.payload(group) or {"checks": [], "more": 0}
        for item in shown["checks"]:
            said.append(f"- {item['label']}: {item['said']} ({item['result_words']}, {item['who']})")
        if shown.get("more_words"):
            said.append(f"- {shown['more_words']}")
    return "\n".join(said)


class ChecksListTool(Tool):
    """What Iron Jarvis itself checked in this chat, from its record."""

    name = TOOL_NAME
    description = (
        "Say what Iron Jarvis itself checked on the work in THIS chat: whether a file a reply made opens, a "
        "spreadsheet's error cells and whether its totals add up, whether a document has text, a PDF's pages, "
        "whether a reply matched what it really did, required files, tests and reviews, and what could NOT be "
        "checked and why. Use it for \"what did you check?\", \"did you check the totals?\", \"how do you know "
        "it is right?\". It reads Iron Jarvis's own record only, runs no check again and changes nothing. "
        "Leave `run` out for this chat; `run` is the id of an agent run this chat started."
    )
    input_schema = {"type": "object", "properties": {"run": {"type": "string"}}}
    reversibility = Reversibility.READONLY

    def __init__(self, platform: Any = None) -> None:
        self.platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        engine = getattr(ctx, "engine", None)
        if engine is None:
            return ToolResult(ok=False, error="Iron Jarvis's record of checks cannot be read right now.")
        run = " ".join(str((args or {}).get("run") or "").split())[:120]
        rows, refusal = await asyncio.to_thread(_owner_rows, engine, ctx, run)
        if refusal:
            return ToolResult(ok=False, error=refusal)
        shown = record.payload(rows, limit=MAX_ROWS) or {"checks": [], "more": 0}
        return ToolResult(ok=True, output=answer(rows), data={"checks": shown["checks"], "more": shown["more"]})


def checks_tools(platform: Any = None) -> list[Tool]:
    return [ChecksListTool(platform)]


__all__ = ["ChecksListTool", "MAX_ROWS", "NOTHING", "OTHER_CHAT", "TOOL_NAME", "answer", "checks_tools"]
