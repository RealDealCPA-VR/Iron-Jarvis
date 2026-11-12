"""``find_capability``: the model looks up what Iron Jarvis can do (capability
search plan, Phase 1).

A chat reply's tools are picked from the user's words before the model
answers. When the words fit no pattern the model has no tool for the request,
and it says "I can't do that" about something Iron Jarvis does. This tool is
the model's way to ask: it describes the request in its own words and gets
back what Iron Jarvis has for it: the tool, the setting, the page, or the
true "not offered" sentence.

THE TOOL ONLY ANSWERS. What it found is left for its own turn
(``capability_search/join.FOUND``), and the chat lane decides, at the end of
the round, which of those tools join the reply and how (``join.join_found``:
a reader joins and runs, a changer joins as ask first, the rest is said). So
the answer tells the model to call a listed tool only if it is one of its
tools, and the lane's note under the list says which ones this lookup
brought. Looking things up never brings the script tool
(``chat_turn.offer_script_tool`` does not count a lookup).

It reads the index (``capability_search/index.py``), the permission table and
the remembered answer to "is this program installed". It changes nothing and
reaches nothing outside this PC, and every word it returns is Iron Jarvis's
own (a built-in tool's card words, a setting's label, a page's sentence),
which is why its output is not fenced as untrusted. A tool's DESCRIPTION is
searched but never quoted.

Three answers that must never be confused: nothing matched, the request had
no words to search with, and the lookup itself failed. Only the first may be
said as "no feature for it".
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..tools.base import Reversibility, RiskClass, Tool, ToolContext, ToolResult
from . import index as _index

NAME = "find_capability"

#: The closing lines of every answer (Phase 1: answers only).
HOW_TO_USE = (
    "How to use this list: these are the closest matches, and a match may not "
    "be what the user asked for, so read each line and use only one that fits. "
    "Call a tool listed above only if it is one of your tools in this reply (a "
    "note under this list names the tools this lookup brought). If "
    "it is not, do not act as if it were: tell the user that Iron Jarvis can do "
    "this, and name the setting or the page when one is listed. Never tell the "
    "user Iron Jarvis cannot do something a fitting line shows it can. If no "
    "line fits, say plainly that you found no feature for it."
)

NOTHING_FOUND = (
    "Nothing in Iron Jarvis matches that. Try once more with other ENGLISH words for "
    "the request (what should change, or what should happen): this list is written "
    "in English, whatever language the user writes in. If nothing matches again, "
    "say plainly that you found no feature for it; do not invent one."
)

#: Added to an answer whose query knew fewer than half of its own words.
NOT_ENGLISH_HINT = (
    "(Fewer than half of your search words appear anywhere in this list, which is "
    "written in English. If your query was in another language, look again in "
    "English before you rely on these matches.)"
)

TOO_GENERAL = (
    "That is too general to look up: it names nothing to search for. This is not "
    "a sign that a feature is missing. Name one thing the user wants done, in a few "
    "plain words (for example \"run a task every Monday morning\"), and look again."
)

LOOKUP_FAILED = (
    "The lookup could not be read just now. This says nothing about what Iron "
    "Jarvis can do: do not tell the user a feature is missing because of it. "
    "Answer from what you know, and say the lookup was not available."
)


def _mode(platform: Any, tool_name: str) -> str:
    """``allow`` | ``ask`` | ``deny`` for *tool_name* today, or "" when the
    permission table cannot say."""
    try:
        tool = platform.registry.get(tool_name)
        if tool is None:
            return ""
        perm_key = getattr(tool, "perm_key", None)
        key = perm_key() if callable(perm_key) else tool_name
        return str(getattr(platform.permissions.mode_for(key), "value", "") or "")
    except Exception:  # noqa: BLE001 — unknown is said as unknown, never guessed
        return ""


#: v1.480.0: "It asks the user first" was read by a real model as "ask the
#: user in words first" (see ``join.CARD_WORDS``). The card is the question.
ASKS_LINE = "Calling it shows the user an approval card first."
ASKS_WHEN_FOUND_LINE = "Calling it shows the user an approval card first when a lookup brings it."


def _how_it_runs(mode: str, changes: bool, asks_found: bool = False) -> str:
    if mode == "deny":
        return "The user has set it to Never run."
    if mode == "ask":
        return ASKS_LINE
    if mode == "allow" and asks_found:
        # The same words the join's note uses: a tool that changes something
        # or reaches outside this PC asks when a LOOKUP brings it, whatever
        # its own tier is.
        return ASKS_WHEN_FOUND_LINE
    if mode == "allow":
        return "It runs without asking and changes something." if changes else "It only reads."
    return ""


def _not_needed(entry: _index.Capability) -> bool:
    """A page step this PC no longer needs: an install press for a program
    that is already installed (``page_doors.step_not_needed``, from the
    REMEMBERED answer only, never a scan of the disk). Not known = needed."""
    if entry.kind != "page":
        return False
    try:
        from ..daemon import page_doors
        from ..providers import manager

        step = next((s for s in page_doors.STEPS if s.id == entry.id), None)
        if step is None:
            return False
        return page_doors.step_not_needed(
            step, "", installed=lambda binary: manager.known_cli_presence(binary))
    except Exception:  # noqa: BLE001 — not known: the step stays
        return False


def entry_line(platform: Any, entry: _index.Capability) -> tuple[str, dict[str, Any]]:
    """One answer line for *entry* and its data row."""
    row: dict[str, Any] = {"kind": entry.kind, "id": entry.id, "title": entry.title}
    if entry.kind == "tool":
        mode = _mode(platform, entry.tool)
        row.update(tool=entry.tool, mode=mode, changes=entry.changes)
        asks_found = False
        try:
            from . import join as _join

            asks_found = entry.tool in _join.joinable() and _join.asks_when_found(
                platform.registry, entry.tool)
        except Exception:  # noqa: BLE001 — the table's own word stands
            asks_found = False
        line = f"{entry.title}: tool {entry.tool}. {_how_it_runs(mode, entry.changes, asks_found)}".rstrip()
        return line, row
    if entry.kind == "setting":
        mode = _mode(platform, entry.tool)
        row.update(tool=entry.tool, mode=mode, changes=True, href=entry.href, key=entry.id,
                   protected=entry.protected)
        head = f"Setting \"{entry.title}\" (key {entry.id})"
        if mode == "deny":
            return (
                f"{head}: read with config_list. The user has set tool {entry.tool} to Never "
                f"run, so it is changed only by hand, at {entry.href}."
            ), row
        line = (
            f"{head}: changed with tool {entry.tool}"
            f"{' (calling it shows the user an approval card first)' if mode == 'ask' else ''}, "
            "read with config_list, "
            f"or by hand at {entry.href}."
        )
        if entry.protected:
            line += (
                " Some of its values are protected: those need tool "
                "config_change_protected, which asks the user every time."
            )
        return line, row
    if entry.kind == "page":
        row.update(href=entry.href)
        return f"Needs a page ({entry.title}, {entry.href}): {entry.line}", row
    return f"Not offered: {entry.line}", row


def answer(platform: Any, query: Any, limit: Any = 5) -> tuple[str, dict[str, Any]]:
    """The whole answer text and its data. Blocking, bounded, never raises."""
    query = " ".join(str(query or "")[: _index.QUERY_CHARS * 4].split())[: _index.QUERY_CHARS]
    data: dict[str, Any] = {"query": query, "matches": []}
    try:
        if not _index.has_search_words(query):
            return TOO_GENERAL, {**data, "reason": "no_search_words"}
        idx = _index.index_for(platform)
        found = [entry for _score, entry in idx.search(query, _index.MAX_RESULTS)]
        found = [entry for entry in found if not _not_needed(entry)]
        # v1.479.0: a tool a lookup can bring before one nothing in chat brings.
        from .join import usable_first

        found = usable_first(found)[: _index.clamp(limit)]
        known, total = idx.known_words(query)
        foreign = total >= 2 and known * 2 < total
        missing = ""
        if idx.unreadable:
            missing = (
                f"(The list of {' and '.join(idx.unreadable)} could not be read just now, so "
                "this answer may be missing some. Do not conclude a feature is missing.)"
            )
        if not found:
            if missing:
                return f"{LOOKUP_FAILED} {missing}", {**data, "reason": "failed"}
            return NOTHING_FOUND, {**data, "reason": "nothing"}
        lines = [f"What Iron Jarvis has for \"{query}\", closest match first:"]
        rows = []
        for n, entry in enumerate(found, 1):
            line, row = entry_line(platform, entry)
            lines.append(f"{n}. {line}")
            rows.append(row)
        lines.append("")
        if missing:
            lines.append(missing)
        if foreign:
            lines.append(NOT_ENGLISH_HINT)
        lines.append(HOW_TO_USE)
        return "\n".join(lines), {**data, "matches": rows}
    except Exception:  # noqa: BLE001 — a fault is said as a fault, never as "nothing matches"
        return LOOKUP_FAILED, {**data, "reason": "failed"}


class FindCapabilityTool(Tool):
    name = NAME
    description = (
        "Look up what Iron Jarvis itself can do. Call this BEFORE you tell the "
        "user that something cannot be done here, and whenever the user asks "
        "Iron Jarvis to set up, change, remember, schedule, connect, watch or "
        "create something and none of your other tools fits. Describe the request "
        "in a few plain ENGLISH words, whatever language the user writes in "
        "(the list is in English; for example \"run a task every Monday "
        "morning\", \"make the text bigger\", \"keep this chat on this PC\"). "
        "It returns the closest matching tools, settings and pages, and says so "
        "when Iron Jarvis does not offer the thing. A fitting tool it finds may "
        "join your tools for the rest of this reply (the result says which, and "
        "which show the user an approval card when you call them). The lookup "
        "itself is read-only: it changes nothing."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The request, in a few plain English words (always English).",
            },
            "limit": {
                "type": "integer",
                "description": f"How many matches to return (1 to {_index.MAX_RESULTS}; default 5).",
            },
        },
        "required": ["query"],
    }
    reversibility = Reversibility.READONLY
    risk_class = RiskClass.READ
    replay = True

    def __init__(self, platform: Any) -> None:
        self._platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult(ok=False, output="", error="say what to look up, in a few words")
        text, data = await asyncio.to_thread(answer, self._platform, query, args.get("limit"))
        # Left for THIS turn only: the lane joins what fits at the end of the
        # round (join.join_found). A turn with no id gets the answer alone.
        try:
            from .join import FOUND, turn_key

            FOUND.note(turn_key(ctx), data.get("matches"))
        except Exception:  # noqa: BLE001 — the answer stands by itself
            pass
        return ToolResult(ok=True, output=text, data=data)


def capability_tools(platform: Any) -> list[Tool]:
    return [FindCapabilityTool(platform)]
