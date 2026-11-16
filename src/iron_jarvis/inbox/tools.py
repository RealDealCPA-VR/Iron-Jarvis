"""The Improvements inbox from chat (v1.506.0, north-star plan 6.2).

The Improvements page (``GET /improvements``, ``inbox/collect.py``) lists every
suggestion Iron Jarvis is waiting on the user to answer, and each item names
its OWN accept and decline routes. Chat had no way in: "what suggestions are
waiting?" and "keep the one about my email tone" found no tool, no door and no
lookup line. Two tools close that:

* ``suggestion_list`` (allow, reads only): the same one read the page makes
  (``collect``), as numbered lines with each item's id. A suggestion's words
  can come from a model or an agent run, so the output is fenced as outside
  text (``returns_untrusted_content``).
* ``suggestion_answer`` (ASK: the approval card every time): answers ONE
  item, by its id, THROUGH THAT ITEM'S OWN ROUTE (the very path and body the
  page presses, dispatched in-process to the app's router by
  ``daemon/routes/improvements.answer_through_route``), so the inbox still
  decides nothing itself and every rule a route enforces holds here too. An
  accept the page takes in two presses (``confirm``: it starts a task, creates
  a tool, deletes or rewrites notes) is the card's question, and the card
  says what it will do. An item that cannot be answered here (an offer to stop
  asking, an accept the route would refuse right now) is refused in words and
  the reply's door opens the page.

Both tools' success opens the Improvements page (``daemon/doors``).
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from ..tools.base import Reversibility, RiskClass, Tool, ToolContext, ToolResult

LIST = "suggestion_list"
ANSWER = "suggestion_answer"
#: The page every suggestion is answered on.
HREF = "/improvements"

#: Lines one list shows at most (the page has the rest).
LIST_MAX = 30
ANSWERS = ("accept", "decline")

NOTHING_WAITING = "No suggestion is waiting for an answer."
NO_ROUTE = "Iron Jarvis can only answer a suggestion while its window is running."


def _d(platform: Any) -> Any:
    w = getattr(platform, "config_writer", None)
    return getattr(w, "d", None)


def _read(platform: Any) -> "dict[str, Any] | None":
    """The inbox, as the page reads it. BLOCKING. None with no running app."""
    from .collect import collect

    d = _d(platform)
    if d is None:
        return None
    return collect(d)


def _line(n: int, it: dict[str, Any], label: str) -> str:
    bits = [f"{n}. [{it.get('id')}] {label}: {it.get('title') or ''}"]
    if it.get("about"):
        bits.append(f"(about {it['about']})")
    acc, dec = it.get("accept"), it.get("decline")
    if acc is None and dec is None:
        bits.append("— answered only on its own page, not from chat.")
    else:
        opts = []
        if acc:
            word = acc.get("label") or "Accept"
            if acc.get("blocked"):
                word += f" (cannot right now: {acc['blocked']})"
            opts.append(f"accept = {word}")
        if dec:
            opts.append(f"decline = {dec.get('label') or 'Decline'}")
        bits.append("— " + "; ".join(opts) + ".")
    return " ".join(bits)


class SuggestionListTool(Tool):
    name = LIST
    description = (
        "List the suggestions Iron Jarvis is waiting on the user to accept or decline (the "
        "Improvements page): preferences it noticed, names that may be clients, skill drafts, "
        "changes to an agent's instructions, working habits, lessons from coding sessions, memory "
        "tidy-ups, requests for a tool, and things it wants to do on its own. Each line has the "
        "suggestion's id in brackets; answer one with suggestion_answer. Reads only."
    )
    input_schema = {"type": "object", "properties": {}}
    reversibility = Reversibility.READONLY
    risk_class = RiskClass.READ
    returns_untrusted_content = True
    replay = True

    def __init__(self, platform: Any) -> None:
        self.platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        got = await asyncio.to_thread(_read, self.platform)
        if got is None:
            return ToolResult(ok=False, error=NO_ROUTE)
        from .collect import LABELS

        items = list(got.get("items") or [])
        unreadable = [str(u.get("label") or "") for u in got.get("unreadable") or [] if isinstance(u, dict)]
        lines: list[str] = []
        if not items:
            lines.append(NOTHING_WAITING)
        else:
            total = int(got.get("total") or len(items))
            lines.append(f"{total} suggestion{'s' if total != 1 else ''} waiting for an answer:")
            for n, it in enumerate(items[:LIST_MAX], 1):
                lines.append(_line(n, it, LABELS.get(str(it.get("kind") or ""), "Suggestion")))
            if total > min(len(items), LIST_MAX):
                lines.append(f"(The other {total - min(len(items), LIST_MAX)} are on the Improvements page.)")
        if unreadable:
            # A store that could not be read is never "nothing waiting".
            lines.append("Could not be read just now, so there may be more: " + ", ".join(unreadable) + ".")
        rows = [
            {"id": it.get("id"), "kind": it.get("kind"), "title": it.get("title")}
            for it in items[:LIST_MAX]
        ]
        return ToolResult(ok=True, output="\n".join(lines), data={"suggestions": rows, "total": got.get("total", 0)})


class SuggestionAnswerTool(Tool):
    name = ANSWER
    description = (
        "Answer ONE suggestion that waits on the Improvements page: `id` is the id suggestion_list "
        "shows in brackets (for example \"preference:abc123\"), `answer` is \"accept\" (keep, approve, "
        "yes) or \"decline\" (not this, reject, dismiss). It does exactly what the page's own button "
        "does, and the user approves on a card first. Call suggestion_list first when you do not "
        "have the id."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "The suggestion's id, as suggestion_list shows it."},
            "answer": {"type": "string", "enum": list(ANSWERS)},
        },
        "required": ["id", "answer"],
    }
    reversibility = Reversibility.IRREVERSIBLE
    risk_class = RiskClass.PAGE_ACTION

    def __init__(self, platform: Any) -> None:
        self.platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        sid = str(args.get("id") or "").strip()
        answer = str(args.get("answer") or "").strip().lower()
        if answer not in ANSWERS:
            return ToolResult(ok=False, error='answer must be "accept" or "decline"')
        if not sid:
            return ToolResult(ok=False, error="say which suggestion: its id from suggestion_list")
        d = _d(self.platform)
        if d is None or getattr(d, "suggestion_app", None) is None:
            return ToolResult(ok=False, error=NO_ROUTE)
        got = await asyncio.to_thread(_read, self.platform)
        items = list((got or {}).get("items") or [])
        it = next((i for i in items if str(i.get("id") or "") == sid), None)
        if it is None:
            return ToolResult(
                ok=False,
                error=f"No suggestion {sid!r} is waiting now (it may have been answered). Call suggestion_list.",
                data={"door": HREF},
            )
        press = it.get(answer)
        if not isinstance(press, dict):
            return ToolResult(
                ok=False,
                error="This suggestion is answered only on its own page, not from chat: it widens what runs "
                      "without asking. Point the user to the Improvements page.",
                data={"door": HREF},
            )
        if press.get("blocked"):
            return ToolResult(ok=False, error=f"It cannot be {answer}ed right now: {press['blocked']}",
                              data={"door": HREF})
        method = str(press.get("method") or "POST").upper()
        path = str(press.get("path") or "")
        if not _OWN_ROUTE.fullmatch(path):
            return ToolResult(ok=False, error="This suggestion's answer has no address chat can use.")
        from ..daemon.routes.improvements import answer_through_route

        status, detail = await answer_through_route(d, method, path, press.get("body") or {})
        title = str(it.get("title") or sid)
        if not 200 <= status < 300:
            return ToolResult(ok=False, error=f"Not done: {detail or 'the answer was refused'}", data={"door": HREF})
        word = str(press.get("label") or answer.title())
        note = str(press.get("note") or "")
        out = f"{word}: {title}." + (f" {note}" if note else "")
        return ToolResult(ok=True, output=out, data={"suggestion": sid, "answer": answer, "title": title})


#: An item's own route, as ``inbox/collect`` builds it: a path of plain
#: segments (each id is URL-quoted there), never a full address or a query.
_OWN_ROUTE = re.compile(r"/[A-Za-z0-9_\-/%.:]{1,300}")


def suggestion_tools(platform: Any) -> list[Tool]:
    return [SuggestionListTool(platform), SuggestionAnswerTool(platform)]


# --------------------------------------------------------------------------- #
# the words that bring them to a chat reply
# --------------------------------------------------------------------------- #
#: "what suggestions are waiting?", "show me your suggestions", "the
#: Improvements page", "anything in my inbox from you?" — Iron Jarvis's own
#: suggestions, never "give me suggestions for dinner".
_SUGGESTION_WORDS = re.compile(
    r"\b(?:suggestions?|proposals?)\b[^.!?\n]{0,40}?\b(?:waiting|pending|open|left|to\s+answer|for\s+me"
    r"|you\s+(?:made|have|noticed|left))\b"
    r"|\b(?:your|jarvis['’]?s?|the\s+waiting|waiting|pending)\s+(?:suggestions?|proposals?)\b"
    r"|\bimprovements\s+(?:page|inbox|list)\b|\b(?:open|show|check)\s+(?:me\s+)?(?:the\s+)?improvements\b"
    r"|\bskill\s+drafts?\b",
    re.IGNORECASE,
)
#: "keep the one about my email tone", "decline that one", "accept the
#: second one", "dismiss the suggestion about…": answering an item the reply
#: listed.
_ANSWER_WORDS = re.compile(
    r"\b(?:keep|accept|approve|decline|reject|dismiss|turn\s+down|say\s+(?:yes|no)\s+to)\b[^.!?\n]{0,24}?"
    r"\b(?:(?:the|that|this)\s+(?:one|suggestion|proposal)|the\s+(?:first|second|third|last|next|other)\s+one"
    r"|(?:those|these|them)\s+(?:suggestions?|ones?))\b",
    re.IGNORECASE,
)


def suggestion_tools_for(text: str) -> list[str]:
    """The suggestion tools a chat message asks for (linear: each pattern's
    gaps are bounded). A question keeps the reader only."""
    t = str(text or "")[:4000]
    out: list[str] = []
    if _SUGGESTION_WORDS.search(t):
        out += [LIST, ANSWER]
    if _ANSWER_WORDS.search(t):
        out += [n for n in (LIST, ANSWER) if n not in out]
    return out


ALL_PATTERNS = (_SUGGESTION_WORDS, _ANSWER_WORDS)
