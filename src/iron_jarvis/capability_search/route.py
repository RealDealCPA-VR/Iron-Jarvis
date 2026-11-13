"""Iron Jarvis looks the request up ITSELF, before the model answers (v1.489.0).

WHY. With the lookup tool (`find_capability`, v1.478.0) and the look-first
check (v1.480.0) the right tool still reached only about two replies in three
on the practice set, and the same request went right in one run and wrong in
the next: everything hung on the answering model deciding to look, and on the
search words it then wrote. Measured offline on the same 126 requests, ONE
small call that is shown the list of what Iron Jarvis has (id and a few plain
words each) and the user's message picked the right entry for 116 (92%).

WHAT. With the setting **Feature lookup first** on (``Config.capability_route``,
off by default, and only with Feature lookup on), a plain chat turn starts
that call beside its other preparation, on the turn's OWN provider and model
(pinned: never another provider; the message goes only where the reply was
going anyway). What it picks is then treated EXACTLY like something
`find_capability` found: it goes through :func:`join.join_found`, so every
rule of the join holds (a tool that changes something joins as ask-first with
its forced once-only card; a reader on this PC joins granted; Never run, low
trust, Local only, Auto tools off, a weak model's tool budget and the POST
lane's "cannot ask" all apply; it uses one of the reply's two joining
lookups). The model is told what was looked up in one short section of the
system prompt (:func:`section`), and is free to ignore it.

It never raises and never fails a turn: a timeout, a refusal, an answer that
is not a list of known ids, or the demo model leave the turn exactly as it
was. Nothing here is a way round a card.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from . import index as _index

log = logging.getLogger("iron_jarvis.capability_search.route")

#: The whole call is bounded by this (it runs BESIDE the reply, never in its
#: way: see ``HEAD_START_S``).
ROUTE_TIMEOUT_S = 25.0
#: How long the first round waits for the call once the turn's own
#: preparation is done. A fast model (an API key, a local server) answers
#: inside it and its picks are there from the first word; a slow one (a
#: `claude` process per call: 3 to 10 s measured) is not waited for: its picks
#: join at the end of a tool round, or when the reply ends on words.
HEAD_START_S = 1.5
#: At most this many picks are read from the answer.
MAX_PICKS = 4
#: The user's message is cut to this for the call.
MESSAGE_CHARS = 2000
#: A message with fewer words than this is not routed ("yes", "thanks").
MIN_WORDS = 3
#: The section added to the system prompt is cut to this.
SECTION_CHARS = 2400

ROUTE_SYSTEM = (
    "You route one message a user typed to the features of the desktop assistant "
    "app \"Iron Jarvis\".\n\n"
    "The features, one per line as `id: what it is` (tools, settings, setup pages, "
    "and things it cannot do):\n\n{list}\n\n"
    "Which features does carrying out the user's message need? Answer with ONE line "
    "of JSON: a list of up to 4 ids from the list, the best first. Think about what "
    "the user wants done, not the words they used: a thing to happen at a set time "
    "is a schedule, something to do when a message or file arrives is a rule or a "
    "folder watcher, work to keep at until done is a goal, a way of working to keep "
    "is a skill, a lasting instruction about how to answer is a preference, a "
    "question about the app's own settings is read with config_list, where a button "
    "is or how the app works is the guide. Answer [] when the message needs none "
    "of them: small talk; a question answered from general knowledge; writing or "
    "explaining something with no file to make; or a message that only talks "
    "ABOUT such a topic (a general question, someone else's schedule or "
    "settings, asking for advice or an opinion) without asking Iron Jarvis to do "
    "it and without asking about Iron Jarvis itself.\n"
    "Answer with the JSON list only."
)

#: The picks arrived while the reply was being written (a tool round's end).
#: Said in a note of Iron Jarvis's OWN (a message after the round's tool
#: results), never on a tool's result: seen live, the same words appended to a
#: file search's (fenced, untrusted) output were rightly read as text from the
#: files — "the search results also contained some text telling me to queue a
#: night task; that came from the search output, not from you, so I ignored it".
LATE_LEAD = (
    "(From Iron Jarvis, not from the user and not from any tool, file or page. "
    "Meanwhile Iron Jarvis looked the user's message up itself. It found: "
)
#: The reply ended on words and the picks are in: the model is asked ONCE.
REASK_NOTE = (
    "(From Iron Jarvis, not from the user. While you wrote the reply above, Iron "
    "Jarvis looked the user's message up itself.\n{found}\n"
    "If one of these is what the user asked for, use it now instead of the reply "
    "above: a change shows the user an approval card when you call its tool, and "
    "a tool that needs a detail only the user has asks them for it on a card, so "
    "do not ask in words first. Words alone change nothing in Iron Jarvis: a "
    "reply that says something was set, changed or remembered, or will be done "
    "differently from now on, is only true once a tool was called; and what Iron "
    "Jarvis is set to is read with a tool, not from what you know about "
    "yourself. If none of these is what the user asked for, or the reply above "
    "already did what they asked with a tool, answer with the single word KEEP "
    "and the reply above is sent as it is.)"
)
SECTION_HEAD = "# Looked up for this request"
SECTION_LEAD = (
    "Before you answered, Iron Jarvis looked up what it has for the user's message "
    "(the same lookup `find_capability` does). Closest first:"
)
SECTION_TAIL = (
    "If one of these is what the user asked for, use it in this reply. If none "
    "fits what they asked, ignore this list and answer as you would have (you can "
    "still call `find_capability` yourself)."
)


@dataclass
class Routed:
    """What one routing call found, and what it cost."""

    entries: list = field(default_factory=list)
    provider: str = ""
    model: str = ""
    usage: dict = field(default_factory=dict)
    seconds: float = 0.0


# --------------------------------------------------------------------------- #
# the list the call is shown
# --------------------------------------------------------------------------- #
_CATALOGS: "dict[int, tuple[Any, str, dict[str, Any]]]" = {}


def _listed(entry: Any, can: frozenset) -> bool:
    """Only what a lookup could bring or say: a tool chat can bring, every
    setting, page step and "not offered" line."""
    if entry.kind == "tool":
        return (entry.tool or entry.id) in can
    return True


def catalog(platform: Any) -> tuple[str, dict[str, Any]]:
    """``(the list's text, {id: entry})`` for *platform*'s index. BLOCKING on
    first use (the index may be built); kept per index object."""
    from .join import joinable

    idx = _index.index_for(platform)
    kept = _CATALOGS.get(id(platform))
    if kept is not None and kept[0] is idx:
        return kept[1], kept[2]
    can = joinable()
    by_id: dict[str, Any] = {}
    lines: list[str] = []
    for entry in idx.entries:
        if not _listed(entry, can) or entry.id in by_id:
            continue
        by_id[entry.id] = entry
        lines.append(f"{entry.id}: {' '.join(str(entry.title or '').split())[:80]}")
    text = "\n".join(lines)
    if len(_CATALOGS) > 8:
        _CATALOGS.clear()
    _CATALOGS[id(platform)] = (idx, text, by_id)
    return text, by_id


# --------------------------------------------------------------------------- #
# when, and the call
# --------------------------------------------------------------------------- #
def last_user_text(body: Any) -> str:
    """The newest user message's own words ("" when it has none)."""
    for message in reversed(list(getattr(body, "messages", None) or [])):
        role = getattr(message, "role", None) or (message.get("role") if isinstance(message, dict) else "")
        if role != "user":
            continue
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        return content.strip() if isinstance(content, str) else ""
    return ""


def enough_words(text: str) -> bool:
    """A message long enough to say what is wanted (any script counts)."""
    return len(re.findall(r"\w+", text or "", flags=re.UNICODE)) >= MIN_WORDS


def wanted(d: Any, body: Any, *, low_trust: bool = False, plain_chat: bool = True) -> bool:
    """Should this turn be looked up first? Facts of the turn only, never its
    words: both settings on, plain chat, Auto tools on, not low trust, a turn
    id (the join is keyed by it), and a message with a few words."""
    try:
        config = getattr(d.platform, "config", None)
        if not (getattr(config, "capability_search", False) and getattr(config, "capability_route", False)):
            return False
        if not plain_chat or low_trust or not getattr(body, "auto_tools", False):
            return False
        if not str(getattr(body, "turn_id", "") or "").strip():
            return False
        return enough_words(last_user_text(body))
    except Exception:  # noqa: BLE001 — a fault looks nothing up
        return False


def parse_ids(text: Any, known: Any) -> list[str]:
    """The ids in the call's answer: the first JSON list of text in it, only
    ids in *known*, each once, at most :data:`MAX_PICKS`. Linear."""
    raw = str(text or "")[:4000]
    start = raw.find("[")
    end = raw.find("]", start + 1) if start >= 0 else -1
    if start < 0 or end < 0:
        return []
    try:
        picked = json.loads(raw[start : end + 1])
    except ValueError:
        return []
    out: list[str] = []
    for item in picked if isinstance(picked, list) else []:
        if isinstance(item, str) and item in known and item not in out:
            out.append(item)
        if len(out) >= MAX_PICKS:
            break
    return out


async def route_request(d: Any, body: Any, provider: str, model: str) -> "Routed | None":
    """ONE call on the turn's own provider and model: which entries does the
    user's message need? ``None`` when nothing was found or the call could not
    be made (a timeout, a refusal, the demo model, an unreadable answer).
    Never raises; a cancel passes through (Stop ends the turn)."""
    from ..providers.adapters.base import LLMMessage

    started = time.perf_counter()
    try:
        message = last_user_text(body)[:MESSAGE_CHARS]
        if not message:
            return None
        text, by_id = await asyncio.to_thread(catalog, d.platform)
        if not by_id:
            return None
        async with asyncio.timeout(ROUTE_TIMEOUT_S):
            route = await d.platform.router.complete(
                provider=provider or None,
                model=model or None,
                system=ROUTE_SYSTEM.format(list=text),
                messages=[LLMMessage(role="user", content="The user's message (any language):\n<message>\n"
                                     + message + "\n</message>")],
                tools=[],
                task_class="chat",
                pin=True,
            )
    except Exception:  # noqa: BLE001 — the turn goes on as it was
        log.debug("request routing failed", exc_info=True)
        return None
    try:
        served = str(getattr(route, "provider", "") or "")
        if served == "mock":
            return None  # the demo model looks nothing up
        response = route.response
        ids = parse_ids(getattr(response, "text", ""), by_id)
        took = time.perf_counter() - started
        # Names only, never a word of the message.
        log.info("request routed: %s (%.1f s)", ids, took)
        return Routed(
            entries=[by_id[i] for i in ids], provider=served,
            model=str(getattr(route, "model", "") or ""),
            usage=dict(getattr(response, "usage", None) or {}), seconds=took,
        )
    except Exception:  # noqa: BLE001
        log.debug("request routing answer unreadable", exc_info=True)
        return None


# --------------------------------------------------------------------------- #
# what it found joins the reply
# --------------------------------------------------------------------------- #
def section(lines: list[str], note: str) -> str:
    """The system-prompt section for what was looked up ("" when nothing)."""
    if not lines:
        return ""
    body = [SECTION_HEAD, SECTION_LEAD]
    body += [f"{n}. {line}" for n, line in enumerate(lines, 1)]
    if note:
        body.append(note)
    body.append(SECTION_TAIL)
    return "\n".join(body)[:SECTION_CHARS]


def _lines_and_rows(d: Any, routed: "Routed | None", armed: Any) -> tuple[list[str], list[dict[str, Any]]]:
    """The answer lines and the join rows for *routed* (both empty when there
    is nothing to bring, or the lookup tool is not among the reply's tools:
    the user may have set it to Never run, or a weak model's budget kept it
    out)."""
    from .join import NAME
    from .tool import _not_needed, entry_line

    if routed is None or not routed.entries or NAME not in {str(n) for n in (armed or ())}:
        return [], []
    lines: list[str] = []
    rows: list[dict[str, Any]] = []
    for entry in routed.entries:
        if _not_needed(entry):
            continue
        line, row = entry_line(d.platform, entry)
        lines.append(line)
        rows.append(row)
    return lines, rows


def join_routed(d: Any, body: Any, routed: "Routed | None", *, armed: list, ctx: Any,
                found_text: bool = False, **lists: Any) -> str:
    """Bring *routed*'s entries into the reply through the lookup's own join
    (``join.join_found``, every rule of it) and return the section for the
    system prompt ("" when there is nothing to say; with ``found_text`` just
    the numbered lines and the join's note, for the note a reply that ended
    on words is asked with). ``lists`` are the lane's own lists, exactly as
    it hands them to ``join_found``. Never raises."""
    try:
        from .join import FOUND, NAME, join_found, turn_key

        lines, rows = _lines_and_rows(d, routed, armed)
        key = turn_key(ctx)
        if not lines or not key:
            return ""
        FOUND.note(key, rows)
        said = SimpleNamespace(role="tool", name=NAME, content="")
        join_found(d, body, tools_used=[], armed=armed, msgs=[said], ctx=ctx, **lists)
        note = str(said.content or "").strip()
        if found_text:
            body_lines = [f"{n}. {line}" for n, line in enumerate(lines, 1)]
            return "\n".join(body_lines + ([note] if note else []))[:SECTION_CHARS]
        return section(lines, note)
    except Exception:  # noqa: BLE001 — the turn goes on as it was
        log.debug("joining what was routed failed", exc_info=True)
        return ""


def note_late(d: Any, routed: "Routed | None", *, armed: Any, msgs: list, ctx: Any) -> bool:
    """The picks arrived while a tool round ran: leave them for THIS round's
    join (``join.FOUND``; the lane calls ``join_found`` right after, as for
    the model's own lookups) and say what was found on the round's newest
    tool result. True when something was left. Never raises."""
    try:
        from ..providers.adapters.base import LLMMessage
        from .join import FOUND, turn_key

        lines, rows = _lines_and_rows(d, routed, armed)
        key = turn_key(ctx)
        if not lines or not key:
            return False
        if not any(getattr(m, "role", "") == "tool" for m in reversed(msgs[-8:])):
            return False
        FOUND.note(key, rows)
        text = LATE_LEAD + " ".join(f"{n}. {line}" for n, line in enumerate(lines, 1)) + ")"
        # Iron Jarvis's own note, after the round's tool results (the join's
        # own note is added to it: ``join._say``).
        msgs.append(LLMMessage(role="user", content=text[:SECTION_CHARS]))
        return True
    except Exception:  # noqa: BLE001
        log.debug("late routing note failed", exc_info=True)
        return False


__all__ = [
    "HEAD_START_S",
    "LATE_LEAD",
    "MAX_PICKS",
    "MIN_WORDS",
    "REASK_NOTE",
    "ROUTE_SYSTEM",
    "ROUTE_TIMEOUT_S",
    "Routed",
    "SECTION_HEAD",
    "catalog",
    "enough_words",
    "join_routed",
    "last_user_text",
    "note_late",
    "parse_ids",
    "route_request",
    "section",
    "wanted",
]
