"""What a chat reply was GIVEN from memory (next leaps 15-a, master plan R2.7).

Idea 15, "See what it remembered, and correct it". Every grounding hop of a
chat turn already knows the items it told the model; until now each hop
handed back only its text. This module is the one list those items are
gathered into, so the reply can carry ``used`` and the page can show a small
pop-up ("From memory") and a quiet line ("From memory: 3").

HOW AN ITEM IS RECORDED. The text functions note what they actually put into
the text, at the point they put it there, through :func:`note`:

* ``LearningEngine.apply_to_prompt``: each kept preference or lesson told;
* ``MemoryFabric.ground`` (:meth:`~iron_jarvis.memory.fabric.MemoryFabric.ground_with_hits`):
  each snippet line told (a file, a note, a memory entry, a lesson, a past
  run, a past chat, project knowledge);
* ``workmap.block.work_block``: each piece of work told, a ``recap`` when its
  recap lines were told under it;
* ``workmap.clients.block.client_block_or_raise``: each of the client's
  preferences told;
* ``projects.knowledge.ground``: each knowledge item told;
* ``chat_turn._connector_memory_block``: each note a toggled memory
  connector gave.

A note is kept only while a collector is open (:func:`run_collecting`, which
``chat_turn._gather_grounding`` wraps around EACH hop on its own thread), so
an agent run, terminal help or another app that calls the same functions
records nothing. Because the list is built where the text is built, it never
says more than the turn was told: what the client wall, the Local only rule
or the injection scanner kept out was never noted. A hop that missed the
soft deadline was not in the prompt, so its items are dropped with it.

THE WORDS ARE HONEST. The list is what Iron Jarvis was GIVEN for the reply,
not a claim about what the model relied on. ``shaped`` (:func:`finish`) says
only that the reply carries words that came from that item and not from the
owner's own message: the page opens the pop-up by itself only for such an
item (D-15.1, "only when memory changed the answer"); the item is listed
either way.

An item, as it goes on the reply::

    {kind, id, label, when, href, fix, shaped}   (+ ``client`` on a client_preference)

``kind`` is one of :data:`KINDS`; ``label`` is at most :data:`LABEL_CHARS`
characters and passes ``workmap.keys.clean_line`` (THE mask of the work
record: a secret never reaches a label); ``when`` an ISO time or ""; ``href``
a page in the app or ""; ``fix`` the actions the item supports
(``correct`` / ``forget``: a kept preference and a client's preference in
15-a; 15-b widens it). At most :data:`MAX_ITEMS` items, in hop order.

Text only, no I/O. Never raises out of :func:`note`, :func:`merge` or
:func:`finish`.
"""

from __future__ import annotations

import logging
import re
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

log = logging.getLogger(__name__)

#: Every kind an item may be.
KINDS: tuple[str, ...] = (
    "preference", "client_preference", "work", "recap", "memory", "note", "chat", "run", "file",
    "project",
)
#: The most items one reply carries.
MAX_ITEMS = 24
#: The longest label.
LABEL_CHARS = 80
#: The fixes an item may offer.
FIX_CORRECT = "correct"
FIX_FORGET = "forget"
FIXES: tuple[str, ...] = (FIX_CORRECT, FIX_FORGET)
#: The kept preferences on the Memory page.
PREFERENCES_HREF = "/memory#prefs-kept"
#: How much of an item's own text is compared with the reply.
_TEXT_CHARS = 600
#: How much of a reply (or the owner's message) is read for its words.
_READ_CHARS = 20000

_SINK: "ContextVar[list[dict[str, Any]] | None]" = ContextVar("ij_memory_used_sink", default=None)

_CLIENT_ID = re.compile(r"^client_[0-9a-f]{12}$")
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
#: Words that say nothing about where an answer came from.
_EXTRA_STOP = frozenset(
    """
    you your yours yourself i me my we our us it its is be been being am was were the a an and or
    of to in on at by for with as from into onto than then that this those these there here
    what which who whom whose why how when where will would shall should can could may might
    must not no yes ok okay sure also just only very more most much many some any each every
    all both either neither other others such same own so too again ever never always often
    use uses used using via per like let lets make makes made get gets got give gives given
    answer answers answered reply replies question questions said says say tell told
    """.split()
)


def _stops() -> frozenset[str]:
    try:
        from ..search.index import ASK_WORDS
        from ..workmap.keys import STOP_WORDS

        return frozenset(STOP_WORDS) | frozenset(ASK_WORDS) | _EXTRA_STOP
    except Exception:  # noqa: BLE001 — the local list alone still works
        return _EXTRA_STOP


_STOP = _stops()


def stems(text: object) -> frozenset[str]:
    """The distinctive word stems of ``text``: words of three letters or
    more (or numbers of two digits or more) that are not stop words, lower
    case, a plural ``s`` dropped, cut at six characters. Pure."""
    out: set[str] = set()
    for raw in _WORD.findall(str(text or "")[:_READ_CHARS].lower()):
        if raw.isdigit():
            if len(raw) >= 2:
                out.add(raw)
            continue
        if len(raw) < 3 or raw in _STOP:
            continue
        word = raw[:-1] if len(raw) > 4 and raw.endswith("s") else raw
        out.add(word[:6])
    return frozenset(out)


def _when(value: Any) -> str:
    if isinstance(value, datetime):
        v = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        try:
            return v.astimezone(timezone.utc).isoformat()
        except (ValueError, OSError, OverflowError):  # pragma: no cover — exotic dates
            return ""
    if isinstance(value, str) and 0 < len(value.strip()) <= 40:
        return value.strip()
    return ""


def _href(value: Any) -> str:
    h = str(value or "").strip()
    if not h.startswith("/") or h.startswith("//") or len(h) > 300 or any(c.isspace() for c in h):
        return ""
    return h


def make(kind: str, ref: object, label: object, *, when: Any = None, href: object = "",
         fix: Iterable[str] = (), text: object = "", client: object = "") -> "dict[str, Any] | None":
    """One item, or None when it cannot be said safely: an unknown kind, no
    id, a label that is empty or cannot be masked (fail closed). The item's
    own words (``text``, else the label) are kept as ``_words`` for
    :func:`finish` and never leave this module."""
    if kind not in KINDS:
        return None
    rid = " ".join(str(ref or "").split())[:200]
    if not rid:
        return None
    from ..workmap.keys import clean_line

    shown = clean_line(str(label or ""), LABEL_CHARS)  # RAISES when it cannot mask: dropped
    if not shown.strip():
        return None
    item: dict[str, Any] = {
        "kind": kind,
        "id": rid,
        "label": shown,
        "when": _when(when),
        "href": _href(href),
        "fix": [f for f in FIXES if f in set(fix or ())],
    }
    cid = str(client or "")
    if kind == "client_preference":
        if not _CLIENT_ID.fullmatch(cid):
            item["fix"] = []  # no client to fix it on: listed only
        else:
            item["client"] = cid
    item["_words"] = stems(str(text or "")[:_TEXT_CHARS] or shown)
    return item


def note(kind: str, ref: object, label: object, **kw: Any) -> None:
    """Record one item the caller has just put into the prompt text, when a
    collector is open on this thread (:func:`run_collecting`); otherwise do
    nothing. Never raises: a fault leaves the item out, never the text."""
    sink = _SINK.get()
    if sink is None:
        return
    try:
        item = make(kind, ref, label, **kw)
    except Exception:  # noqa: BLE001 — an item that cannot be said is left out
        log.debug("memory used: an item could not be recorded", exc_info=True)
        return
    if item is not None and len(sink) < MAX_ITEMS * 4:
        sink.append(item)


def collecting() -> bool:
    """Is a collector open here? (A caller may skip building an item.)"""
    return _SINK.get() is not None


def run_collecting(fn: Callable[[], Any]) -> "tuple[Any, list[dict[str, Any]]]":
    """``(fn(), the items fn noted)``. The collector is this call's own, so
    two hops on two threads never share one, and a hop that is dropped takes
    its items with it. Exceptions from ``fn`` propagate unchanged."""
    sink: list[dict[str, Any]] = []
    token = _SINK.set(sink)
    try:
        return fn(), sink
    finally:
        _SINK.reset(token)


def merge(parts: Iterable[Iterable[dict[str, Any]]]) -> list[dict[str, Any]]:
    """The hops' items in hop order, one per ``(kind, id)`` (the first one
    wins: a preference told by the lessons hop keeps its fixes when the
    memory fabric told it too), at most :data:`MAX_ITEMS`. Never raises."""
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    try:
        for part in parts or ():
            for item in part or ():
                if not isinstance(item, dict):
                    continue
                key = (str(item.get("kind") or ""), str(item.get("id") or ""))
                if key in seen:
                    continue
                seen.add(key)
                out.append(item)
                if len(out) >= MAX_ITEMS:
                    return out
    except Exception:  # noqa: BLE001
        log.debug("memory used: the items could not be merged", exc_info=True)
    return out


def finish(items: Iterable[dict[str, Any]], *, reply: object = "", question: object = "") -> list[dict[str, Any]]:
    """The ``used`` list a reply carries: each item without its private
    words, plus ``shaped`` — True when the reply carries a word stem that
    came from the item and is not in the owner's own message (the page opens
    the pop-up by itself only for such an item). [] when nothing was given.
    Never raises (a fault answers [])."""
    try:
        said = stems(reply)
        asked = stems(question)
        out: list[dict[str, Any]] = []
        for item in list(items or ())[:MAX_ITEMS]:
            if not isinstance(item, dict) or item.get("kind") not in KINDS:
                continue
            words = item.get("_words") or frozenset()
            public = {k: v for k, v in item.items() if not str(k).startswith("_")}
            public["fix"] = list(public.get("fix") or [])
            public["shaped"] = bool((frozenset(words) & said) - asked)
            out.append(public)
        return out
    except Exception:  # noqa: BLE001
        log.debug("memory used: the list could not be finished", exc_info=True)
        return []


__all__ = [
    "FIXES", "FIX_CORRECT", "FIX_FORGET", "KINDS", "LABEL_CHARS", "MAX_ITEMS", "PREFERENCES_HREF",
    "collecting", "finish", "make", "merge", "note", "run_collecting", "stems",
]
