"""``checks_list``: "what did you check?", answered from the record.

Read only: it changes nothing and runs no check again. It reads ONLY the
checks of the chat it was called in (the saved chat's, or, in a new chat not
saved yet, this reply's own), or of the run it was called in. ``run`` names
an agent run STARTED FROM THIS CHAT: the tool answers for a run only when
the record says that run belongs to the calling chat. Any other run (another
chat's, one that belongs to no chat, one the record does not know) is
refused in the same plain words, which say where to look. Never another
chat's rows: there is no argument that names a chat.

NEWEST FIRST, BY REPLY. A long chat holds more checks than one answer reads.
The newest replies are answered for, each reply's checks whole, and when
older ones are left out the answer says so: an older file's checks are never
what falls out silently in the middle of a reply.

The answer is words built from the record's rows (the same line the page
shows, then each check with its result and who checked), handed to the model
as data. Each check is said under the record's own word for it: "Problem"
for what a file itself holds, "Worth a look" for what Iron Jarvis worked out
itself and found different (never as a problem), "Not checked", "Checked".
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..tools.base import Reversibility, Tool, ToolContext, ToolResult
from . import record

TOOL_NAME = "checks_list"
#: About how many checks one answer lists (whole replies, newest first; the
#: newest reply is always whole).
MAX_ROWS = 60
NOTHING = (
    "Iron Jarvis's record holds no check for this chat yet. When a reply makes or changes a file, Iron Jarvis "
    "looks at what that file holds when the reply ends."
)
OTHER_RUN = (
    "That run was not started from this chat, so its checks are not read here. Open that run to see what was "
    "checked."
)
OLDER = "Older checks in this chat are not listed here."


def _groups(rows: "list[dict[str, Any]]") -> "list[list[dict[str, Any]]]":
    """Rows grouped by the reply or run they belong to, in the order each
    group is first met."""
    out: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault((str(r.get("turn_id") or ""), str(r.get("session_id") or ""),
                        "" if r.get("turn_id") or r.get("session_id") else str(r.get("run_id") or "")), []).append(r)
    return list(out.values())


def newest_replies(newest_first: "list[dict[str, Any]]", *, limit: int = MAX_ROWS,
                   read_limit: int = record.MAX_READ) -> "tuple[list[dict[str, Any]], bool]":
    """``(rows to answer with, were older ones left out)`` from a chat's rows
    read NEWEST first. Whole replies are kept, newest reply first, while they
    fit *limit* rows (the newest reply is always kept); inside a reply the
    rows read oldest first, as they were written. A read that hit its own
    limit may hold only part of its oldest reply: that reply is left out
    (and said), unless it is the only one."""
    groups = _groups(newest_first)
    hit = len(newest_first) >= max(1, int(read_limit))
    if hit and len(groups) > 1:
        groups = groups[:-1]
    kept: list[dict[str, Any]] = []
    used = 0
    for group in groups:
        if used and len(kept) + len(group) > max(1, int(limit)):
            break
        kept += list(reversed(group))
        used += 1
    return kept, hit or used < len(groups)


def _owner_rows(engine: Any, ctx: ToolContext, run: str) -> "tuple[list[dict[str, Any]], str, bool]":
    """BLOCKING. ``(rows, refusal, older left out)`` for the caller: never
    another chat's."""
    thread = str(getattr(ctx, "thread_id", "") or "")
    session = str(getattr(ctx, "session_id", "") or "")
    inside = session if session and session != "chat" else ""
    if inside and (not run or run == inside):
        # Asked from INSIDE a run: its own rows, on this PC.
        rows = record.for_session(engine, inside)
        return rows, "", len(rows) >= record.MAX_READ
    # Asked from a CHAT. Rows that are kept home (a Local only run's, a
    # Local only chat's own replies': file names, sheet names, figures) are
    # read only by a reply that is Local only itself, asked now (False in
    # any doubt).
    from . import turn as _turn

    local = _turn.may_read_home(engine, thread)
    if run:
        # A run is answered for ONLY when the record says it was started
        # from the calling chat. A run of no chat is not "everybody's".
        mine = [r for r in record.for_session(engine, run) if thread and r.get("thread_id") == thread]
        mine = _turn.home_rows(mine, local)
        return (mine, "", len(mine) >= record.MAX_READ) if mine else ([], OTHER_RUN, False)
    if thread:
        rows, older = newest_replies(_turn.home_rows(record.for_thread(engine, thread, limit=record.MAX_READ), local))
        return rows, "", older
    # A chat that is not saved yet: the rows its own reply wrote, which
    # have NO chat yet. A turn's id is the caller's own choice, so a row of
    # that id that sits under a chat is another chat's, and is not read.
    # (Kept-home rows only while this reply itself is Local only.)
    rows = [r for r in record.for_turn(engine, str(getattr(ctx, "turn_id", "") or "")) if not r.get("thread_id")]
    rows = _turn.home_rows(rows, local)
    return rows, "", len(rows) >= record.MAX_READ


def answer(rows: "list[dict[str, Any]]", older: bool = False) -> str:
    """The rows as words: per reply or run (in the order given: the newest
    reply first), its line, then each check."""
    if not rows:
        return NOTHING
    said: list[str] = ["What Iron Jarvis checked (its own record, newest reply first):"]
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
    if older:
        said.append(OLDER)
    return "\n".join(said)


class ChecksListTool(Tool):
    """What Iron Jarvis itself checked in this chat, from its record."""

    name = TOOL_NAME
    description = (
        "Say what Iron Jarvis itself checked on the work in THIS chat: whether a file a reply made opens, a "
        "spreadsheet's error cells and whether a total adds up, whether a document has text, a PDF's pages, "
        "required files, tests and reviews, what is only worth a look, and what could NOT be checked and why. "
        "Use it for \"what did you check?\", \"did you check the totals?\", \"how do you know "
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
        rows, refusal, older = await asyncio.to_thread(_owner_rows, engine, ctx, run)
        if refusal:
            return ToolResult(ok=False, error=refusal)
        shown = record.payload(rows, limit=MAX_ROWS) or {"checks": [], "more": 0}
        return ToolResult(ok=True, output=answer(rows, older),
                          data={"checks": shown["checks"], "more": shown["more"], "older": bool(older)})


def checks_tools(platform: Any = None) -> list[Tool]:
    return [ChecksListTool(platform)]


__all__ = ["ChecksListTool", "MAX_ROWS", "NOTHING", "OLDER", "OTHER_RUN", "TOOL_NAME", "answer", "checks_tools",
           "newest_replies"]
