"""``chat_fullness``: "why is this chat getting full?", answered from the
chat's own record (the intelligence plan, release 6).

Read only: it changes nothing. It reads ONLY the chat it is asked in: the
reply being written now (its own facts, ``eval.reply_facts``: the very
numbers its usage row will save) and, for the tools and the share the model
server reused, the newest saved reply of that chat
(``context.filling.newest_row``). It takes NO argument, so nothing can name
another chat; inside an agent run it never reads a chat's saved record.

The answer is words built by ``context.filling.answer`` (each part's share
with "about", a reuse figure the server did not report said as not reported),
handed to the model as data. Sizes only: no text of any part is kept, so
there is none to say.

The tool lives here, not in ``context/``: ``context`` is imported by the
providers and must not import ``tools``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..context import filling as _filling
from ..context.breakdown import decode_record
from .base import Reversibility, Tool, ToolContext, ToolResult

TOOL_NAME = _filling.TOOL_NAME


class ChatFullnessTool(Tool):
    """What is filling the chat it is asked in, from Iron Jarvis's own record."""

    name = TOOL_NAME
    description = (
        "Say what is filling THIS chat and why it is getting full: how much of the room the model has is "
        "taken by the user's messages and the replies, attached files, what Iron Jarvis remembered, the "
        "project or folder, its own instructions and the tools it was given, and how much the model server "
        "reused from last time. Use it for \"why is this chat getting full?\", \"what is using up this "
        "chat?\", \"how full is this chat?\". It reads Iron Jarvis's own record of this chat only (sizes, "
        "never the text), takes no argument and changes nothing."
    )
    input_schema = {"type": "object", "properties": {}}
    reversibility = Reversibility.READONLY

    def __init__(self, platform: Any = None) -> None:
        self.platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from ..eval import reply_facts

        facts = reply_facts.current()
        now = getattr(facts, "parts", None) if facts is not None else None
        session = str(getattr(ctx, "session_id", "") or "")
        thread = str(getattr(ctx, "thread_id", "") or "")
        saved = None
        # The saved record is a CHAT's: read only for a chat reply, and only
        # the chat the reply belongs to (there is no argument to name one).
        if thread and (session == "chat" or session.startswith("build:")):
            saved = await asyncio.to_thread(_filling.newest_row, getattr(ctx, "engine", None), thread)
        shown = decode_record(now) if now else (saved or {}).get("record")
        return ToolResult(
            ok=True, output=_filling.answer(now, saved),
            data={"found": shown is not None, "percent": (shown or {}).get("percent"),
                  "parts": dict((shown or {}).get("parts") or {})},
        )


def filling_tools(platform: Any = None) -> list[Tool]:
    return [ChatFullnessTool(platform)]


__all__ = ["ChatFullnessTool", "TOOL_NAME", "filling_tools"]
