"""``start_list``: what runs on its own here, from chat (leap 05-b).

One reader over the ONE list of starts (``triggers/starts.list_starts``): the
user's schedules (they run at a time) and rules (something happens: a file, a
download, an email, a calendar event, a phone or Slack message, a webhook,
another task that fails), each with what it does and what it last did, and
which kinds of signal are not set up yet (``triggers/readiness``). It only
reads (allow tier); the words are composed by code from the stored records.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..tools.base import Reversibility, Tool, ToolContext, ToolResult

#: At most this many rows are said in one answer.
LIST_MAX = 40

#: The kinds whose signal must be set up before a rule can hear it, in words.
_KIND_NAMES = {"email": "email", "calendar": "a calendar event", "slack": "a Slack message",
               "phone": "a message from your phone"}


class StartListTool(Tool):
    """List everything that runs on its own: schedules and rules."""

    name = "start_list"
    description = (
        "List what runs on its own in Iron Jarvis, in one list: the user's schedules (they run at "
        "a time) and rules (when something happens: a new file in a folder, a finished download, "
        "an email, a calendar event, a phone or Slack message, a webhook, or another task that "
        "fails), each with what it does, whether it is on, and what it last did; and which kinds "
        "of signal are not set up yet (with the page that sets each up). Call it for \"what runs "
        "on its own here?\", \"what runs my Month end workflow?\", or to find the name of a "
        "schedule or rule before reflex_create, reflex_update or schedule_update. `workflow`: only "
        "what runs that saved workflow. It only reads."
    )
    permission_key = "start_list"
    reversibility = Reversibility.READONLY
    input_schema = {
        "type": "object",
        "properties": {
            "workflow": {
                "type": "string",
                "description": "Optional: a saved workflow's name, to list only what runs it.",
            },
        },
    }

    def __init__(self, platform: Any) -> None:
        self.platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from . import readiness
        from .starts import list_starts

        workflow = " ".join(str(args.get("workflow") or "").split()) or None
        rows = await asyncio.to_thread(list_starts, self.platform, workflow=workflow)
        kinds = await asyncio.to_thread(readiness.kinds, self.platform)
        lines: list[str] = []
        for row in rows[:LIST_MAX]:
            what = "Schedule" if row["store"] == "schedule" else "Rule"
            state = "" if row["enabled"] else " (off)"
            lines.append(
                f"- {what} “{row['name']}”{state}: {row['when_words']} {row['does_words']} {row['last_words']}".strip()
            )
        not_set_up = [
            f"{_KIND_NAMES[k]} ({v['door']['label']})" for k, v in kinds.items()
            if k in _KIND_NAMES and not v.get("ready") and v.get("door")
        ]
        if not rows:
            head = (f"Nothing runs the workflow “{workflow}” on its own yet." if workflow
                    else "Nothing runs on its own here yet: no schedules and no rules.")
        else:
            n = len(rows)
            lead = "1 thing runs" if n == 1 else f"{n} things run"
            tail = f" the workflow “{workflow}”" if workflow else (" on its own here" if n == 1 else " on their own here")
            head = f"{lead}{tail}:"
        out = head + ("\n" + "\n".join(lines) if lines else "")
        if len(rows) > LIST_MAX:
            out += f"\n…and {len(rows) - LIST_MAX} more, on the Rules and Schedules pages."
        if not_set_up and not workflow:
            out += "\nNot set up yet, so no rule can hear it: " + "; ".join(not_set_up) + "."
        return ToolResult(
            ok=True,
            output=out,
            data={"starts": rows[:LIST_MAX], "not_set_up": [k for k, v in kinds.items() if not v.get("ready")]},
        )


def start_tools(platform: Any) -> list[Tool]:
    """Build the start tools bound to the assembled ``platform``."""
    return [StartListTool(platform)]
