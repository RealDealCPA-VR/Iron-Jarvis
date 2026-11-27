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

18-a (``improve/results``): the RESULTS of changes the owner already
accepted. ``suggestion_list`` lists them under their own heading after the
waiting ones, and ``change_results`` (allow, reads only) answers "did that
change help?": each result's words are built by code from the numbers, and a
table that cannot be read is said, never answered as "no results".
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from ..tools.base import Reversibility, RiskClass, Tool, ToolContext, ToolResult

LIST = "suggestion_list"
ANSWER = "suggestion_answer"
#: 18-a: the results of the changes the owner accepted.
RESULTS = "change_results"
#: The page every suggestion is answered on.
HREF = "/improvements"

#: Lines one list shows at most (the page has the rest).
LIST_MAX = 30
#: Results ``suggestion_list`` adds under their own heading at most.
LIST_RESULTS_MAX = 10
ANSWERS = ("accept", "decline")

NOTHING_WAITING = "No suggestion is waiting for an answer."
NO_ROUTE = "Iron Jarvis can only answer a suggestion while its window is running."
RESULTS_HEAD = "Results of changes you accepted (counted from Iron Jarvis's own records):"
NO_RESULTS = (
    "No change you accepted in the last 60 days has a result yet. A change to an agent's instructions, a "
    "skill or a working habit gets one within a day of your accept, and its numbers once there is enough "
    "work on both sides."
)
RESULTS_UNREADABLE = "The results of changes you accepted could not be read just now."


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


def chat_title(it: dict[str, Any]) -> str:
    """The item's title as THIS turn's model may be told it. 20-a: a request
    whose words were said only in Local only chats (``extra.chat_title``) is
    named only to a turn that itself stays on this network
    (``egress.may_recall_local_only_chats``, the work record's rule); any
    other turn, and an unsure one, gets the neutral line."""
    title = str(it.get("title") or "")
    extra = it.get("extra") if isinstance(it.get("extra"), dict) else {}
    if not extra.get("local_only"):
        return title
    try:
        from ..providers.egress import may_recall_local_only_chats

        if may_recall_local_only_chats():
            return title
    except Exception:  # noqa: BLE001 — unsure: the words stay home (fail closed)
        pass
    return str(extra.get("chat_title") or "a request made in a Local only chat")


def _line(n: int, it: dict[str, Any], label: str) -> str:
    bits = [f"{n}. [{it.get('id')}] {label}: {chat_title(it)}"]
    if it.get("about"):
        bits.append(f"(about {it['about']})")
    asked = (it.get("extra") or {}).get("asked") if it.get("kind") == "missing" else None
    if isinstance(asked, list) and asked:
        # 20-a: when it was asked for (the owner's own words stay on the
        # Improvements page, D-20.1).
        bits.append(f"(asked on {', '.join(str(a) for a in asked[-6:])})")
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
        if isinstance((it.get("extra") or {}).get("rehearse"), dict):
            # 19-b: an offered schedule can be rehearsed first (06-a).
            opts.append(f"rehearse first = the rehearse tool with kind offer and name {it.get('id')}")
        bits.append("— " + "; ".join(opts) + ".")
    return " ".join(bits)


class SuggestionListTool(Tool):
    name = LIST
    description = (
        "List the suggestions Iron Jarvis is waiting on the user to accept or decline (the "
        "Improvements page): preferences it noticed, names that may be clients, skill drafts, "
        "changes to an agent's instructions, working habits, lessons from coding sessions, memory "
        "tidy-ups, requests for a tool, offers to run a repeated task by itself on a schedule, things the "
        "user asked for that Iron Jarvis can't do yet, and "
        "things it wants to do on its own. Each line has the "
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
        # 18-a: the results of changes already accepted, under their own
        # heading (they wait for nothing, so they are in no count above).
        results = [r for r in got.get("results") or [] if isinstance(r, dict)]
        if results:
            lines.append("")
            lines.append(RESULTS_HEAD)
            lines += [result_line(n, r) for n, r in enumerate(results[:LIST_RESULTS_MAX], 1)]
            if len(results) > LIST_RESULTS_MAX:
                lines.append(f"(The other {len(results) - LIST_RESULTS_MAX} are on the Improvements page; "
                             f"{RESULTS} lists them all.)")
        rows = [
            {"id": it.get("id"), "kind": it.get("kind"), "title": chat_title(it)}
            for it in items[:LIST_MAX]
        ]
        return ToolResult(ok=True, output="\n".join(lines), data={"suggestions": rows, "total": got.get("total", 0)})


def result_line(n: int, r: dict[str, Any]) -> str:
    """"1. New instructions for research: Since you accepted this on 12
    March, research finished 9 of 10 tasks without help. Before, it was 6 of
    10." (the words are the result's own, built from its numbers)."""
    return f"{n}. {r.get('title') or ''}: {r.get('words') or ''}"


def _results(platform: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """``(results, what the last count could not read)``, as the page reads
    them. BLOCKING; RAISES when the results cannot be read."""
    from ..improve import results as R

    rows = R.page(platform.engine)
    try:
        labels = R.unreadable_labels(R.last_check(platform.engine))
    except Exception:  # noqa: BLE001 — named, never "nothing went wrong"
        labels = [R.LABEL]
    return rows, labels


#: 18-a: the line that names what the last count could not read.
RESULTS_NOT_READ = "Could not be read when Iron Jarvis last counted, so some results are missing or older: {}."


def _about(r: dict[str, Any], about: str) -> bool:
    words = about.casefold().strip()
    target = str(r.get("target") or "").casefold()
    return words in target or words in str(r.get("title") or "").casefold()


class ChangeResultsTool(Tool):
    """18-a: "did that change help?" — the result of each change the owner
    accepted, from Iron Jarvis's own records. Reads only; works without the
    window (it reads the results table, not the inbox)."""

    name = RESULTS
    description = (
        "Say whether a change the user ACCEPTED from the Improvements page helped: for each accepted change to "
        "an agent's instructions, each new or improved skill and each working habit of a team member (the last "
        "60 days), its result counted from Iron Jarvis's own records, the tasks before and after the accept. "
        "`about` (optional) is an agent's, skill's or team member's name, to show only its changes. Repeat "
        "each result's words as they are: they say \"since\" and \"before\", never \"because\" (the work "
        "itself may have changed), and \"Not enough work yet\" means there is no answer yet. Reads only."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "about": {"type": "string", "description": "Only the changes to this agent, skill or team member."},
        },
    }
    reversibility = Reversibility.READONLY
    risk_class = RiskClass.READ
    # A habit's words and a skill's name were written by a model: data.
    returns_untrusted_content = True
    replay = True

    def __init__(self, platform: Any) -> None:
        self.platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        about = str((args or {}).get("about") or "").strip()[:120]
        try:
            results, not_read = await asyncio.to_thread(_results, self.platform)
        except Exception:  # noqa: BLE001 — never "no results" when they could not be read
            return ToolResult(ok=False, error=RESULTS_UNREADABLE, data={"door": HREF})
        if about:
            results = [r for r in results if _about(r, about)]
        tail = [RESULTS_NOT_READ.format(", ".join(not_read))] if not_read else []
        if not results:
            head = f"No change you accepted about {about!r} has a result yet." if about else NO_RESULTS
            return ToolResult(ok=True, output="\n".join([head, *tail]),
                              data={"results": [], "total": 0, "unreadable": not_read})
        lines = [RESULTS_HEAD] + [result_line(n, r) for n, r in enumerate(results, 1)] + tail
        rows = [
            {"id": r.get("id"), "kind": r.get("kind"), "title": r.get("title"), "state": r.get("state"),
             "verdict": r.get("verdict"), "before": r.get("before"), "after": r.get("after")}
            for r in results
        ]
        return ToolResult(ok=True, output="\n".join(lines),
                          data={"results": rows, "total": len(rows), "unreadable": not_read})


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
        title = chat_title(it) or sid
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
    return [SuggestionListTool(platform), SuggestionAnswerTool(platform), ChangeResultsTool(platform)]


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


#: 18-a: "did that change help?", "has the new skill made a difference?",
#: "the results of the changes I accepted", "is it better since I accepted
#: it": a change Iron Jarvis suggested and the owner accepted. A question
#: about a change TO a thing (an agent's instructions, a skill, a habit), or
#: the results of changes; never "did that help you understand?".
_RESULT_WORDS = re.compile(
    r"\b(?:did|has|have|is|are|was|were)\s+(?:that|the|this|my|those|these|your)\s+(?:[\w'’-]+\s+){0,3}?"
    r"(?:change|changes|edit|edits|skill|skills|habit|habits|instructions?|suggestion|suggestions)\b"
    r"[^.!?\n]{0,48}?\b(?:help(?:ed|ing|s)?|work(?:ed|ing|s)?|(?:make|made)\s+(?:a\s+|any\s+)?difference"
    r"|pa(?:y|id)\s+off|improve[ds]?|better|worse)\b"
    r"|\bresults?\s+of\s+(?:the\s+|my\s+|those\s+|these\s+)?(?:changes?|suggestions?)\b"
    r"|\b(?:since|after)\s+I\s+(?:accepted|approved|kept)\s+(?:it|that|this|the|them|those)\b"
    r"|\bchange\s+results?\b",
    re.IGNORECASE,
)


#: Next leaps 20-a: "what have I asked for that you couldn't do?", "the things
#: you can't do yet that I asked for": the owner's own asks Iron Jarvis had no
#: feature for (the Improvements kind ``missing``). Always a question: the
#: list only.
_CANT = (
    r"(?:can['’]?t|cannot|can\s+not|couldn['’]?t|could\s+not|weren['’]?t\s+able\s+to|were\s+not\s+able\s+to"
    r"|aren['’]?t\s+able\s+to|are\s+not\s+able\s+to|don['’]?t\s+have\s+a\s+(?:way|feature)|had\s+no\s+(?:way|feature))"
)
_WHO = r"(?:you|jarvis|iron\s+jarvis)"
_MISSING_WORDS = re.compile(
    r"\b(?:what|which|anything|things?|requests?)\b[^.!?\n]{0,24}?\b(?:I|we)\s+(?:have\s+|['’]ve\s+)?"
    r"(?:ask(?:ed)?|request(?:ed)?|want(?:ed)?)\b[^.!?\n]{0,40}?\b" + _WHO + r"\s+" + _CANT
    + r"|\b(?:things?|requests?)\s+(?:that\s+)?" + _WHO + r"\s+" + _CANT + r"\s+do\s+yet\b",
    re.IGNORECASE,
)


def suggestion_tools_for(text: str) -> list[str]:
    """The suggestion tools a chat message asks for (linear: each pattern's
    gaps are bounded). A question keeps the readers only."""
    t = str(text or "")[:4000]
    out: list[str] = []
    if _SUGGESTION_WORDS.search(t):
        out += [LIST, ANSWER]
    if _MISSING_WORDS.search(t) and LIST not in out:
        out.append(LIST)
    if _ANSWER_WORDS.search(t):
        out += [n for n in (LIST, ANSWER) if n not in out]
    if _RESULT_WORDS.search(t):
        out.append(RESULTS)
    return out


#: The tools a QUESTION keeps (they only read).
READERS = (LIST, RESULTS)

ALL_PATTERNS = (_SUGGESTION_WORDS, _ANSWER_WORDS, _RESULT_WORDS, _MISSING_WORDS)
