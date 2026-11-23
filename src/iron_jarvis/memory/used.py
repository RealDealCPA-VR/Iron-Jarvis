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
soft deadline was not in the prompt, so its items are dropped with it. The
chat the turn is IN is the conversation itself, never memory: its own
earlier messages, recalled by the fabric, are left out (:func:`merge`).

THE WORDS ARE HONEST. The list is what Iron Jarvis was GIVEN for the reply,
not a claim about what the model relied on. ``shaped`` (:func:`finish`) says
only that the reply carries words that came from that item and not from the
conversation sent with the turn (:func:`conversation`: the owner's messages
and the earlier replies): the page opens the pop-up by itself only for such
an item (D-15.1, "only when memory changed the answer"); the item is listed
either way.

An item, as it goes on the reply::

    {kind, id, label, when, href, fix, shaped}   (+ ``client`` on a client_preference)

``kind`` is one of :data:`KINDS`; ``label`` is at most :data:`LABEL_CHARS`
characters and passes ``workmap.keys.clean_line`` (THE mask of the work
record: a secret never reaches a label), and ``id`` passes the same mask
(``keys.mask_line``); ``when`` an ISO time or ""; ``href``
a page in the app or ""; ``fix`` the actions the item supports
(``correct`` / ``forget``: a kept preference and a client's preference in
15-a; 15-b widens it). At most :data:`MAX_ITEMS` items, in hop order.

THE CAP NEVER HIDES WHAT SHAPED THE REPLY. Every kept preference is told on
every turn, so one hop can tell more than :data:`MAX_ITEMS` things. The cap
is applied LAST (:func:`finish_with_more`): ``shaped`` is worked out for
every item first, the items that shaped the reply are kept first, and each
tier is taken one kind at a time in turn, so one kind cannot fill the list.
What the cap left out is COUNTED (``used_more`` beside ``used`` on both
lanes, :func:`left_out`), so the page can say "and N more not listed"
instead of letting them disappear without a word.

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
#: How much of a reply is read for its words.
_READ_CHARS = 20000
#: How much of the conversation sent with a turn is read for the words it
#: already had (:func:`conversation`, the newest messages first).
CONVERSATION_CHARS = 80000

#: The most items one hop keeps (what it tells past this is only counted).
HOP_ITEMS = MAX_ITEMS * 4


class Told(list):
    """A list of items that also counts the items past a cap (``dropped``):
    a hop's collector, and :func:`merge`'s answer. A plain list counts 0."""

    dropped: int = 0


_SINK: "ContextVar[Told | None]" = ContextVar("ij_memory_used_sink", default=None)

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


def stems(text: object, *, limit: int = _READ_CHARS) -> frozenset[str]:
    """The distinctive word stems of the first ``limit`` characters of
    ``text``: words of three letters or more (or numbers of two digits or
    more) that are not stop words, lower case, a plural ``s`` dropped, cut
    at six characters. Pure."""
    out: set[str] = set()
    for raw in _WORD.findall(str(text or "")[: max(0, int(limit))].lower()):
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
    from ..workmap.keys import clean_line, mask_line

    # The id rides the reply and the saved chat line beside the label, so it
    # passes the same mask (a note with no ref of its own is named by its
    # title). RAISES when it cannot mask: dropped, like the label.
    rid = " ".join(mask_line(str(ref or "")[:2000]).split())[:200]
    if not rid:
        return None
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
    if item is None:
        return
    if len(sink) < HOP_ITEMS:
        sink.append(item)
        return
    key = (item["kind"], item["id"])
    if not any((s.get("kind"), s.get("id")) == key for s in sink):
        sink.dropped += 1  # told, but past the hop's cap: counted, never lost silently


def collecting() -> bool:
    """Is a collector open here? (A caller may skip building an item.)"""
    return _SINK.get() is not None


def run_collecting(fn: Callable[[], Any]) -> "tuple[Any, Told]":
    """``(fn(), the items fn noted)``. The collector is this call's own, so
    two hops on two threads never share one, and a hop that is dropped takes
    its items with it. Exceptions from ``fn`` propagate unchanged."""
    sink = Told()
    token = _SINK.set(sink)
    try:
        return fn(), sink
    finally:
        _SINK.reset(token)


def merge(parts: Iterable[Iterable[dict[str, Any]]], *, own_chat: str = "") -> Told:
    """The hops' items in hop order, one per ``(kind, id)`` (the first one
    wins: a preference told by the lessons hop keeps its fixes when the
    memory fabric told it too). NOT capped here: :func:`finish_with_more`
    caps last, once it knows what shaped the reply. ``dropped`` sums what
    each hop told past its own cap. Never raises.

    ``own_chat`` is the saved chat the turn is IN (the body's
    ``thread_id``). The page saves and indexes a chat after every turn, so
    the memory fabric recalls that chat's own earlier messages like any
    other conversation. They are the conversation itself, not something
    remembered from elsewhere: a ``chat`` item for it is left out (and not
    counted), or the box would open by itself under almost every follow-up
    and call the chat on screen an "Earlier chat" (D-15.1). The work block
    already leaves the same chat out (``work_block(exclude_ref=…)``)."""
    out = Told()
    seen: set[tuple[str, str]] = set()
    own = str(own_chat or "").strip()
    try:
        for part in parts or ():
            out.dropped += max(0, int(getattr(part, "dropped", 0) or 0))
            for item in part or ():
                if not isinstance(item, dict):
                    continue
                key = (str(item.get("kind") or ""), str(item.get("id") or ""))
                if own and key == ("chat", own):
                    continue
                if key in seen:
                    continue
                seen.add(key)
                out.append(item)
    except Exception:  # noqa: BLE001
        log.debug("memory used: the items could not be merged", exc_info=True)
    return out


def conversation(messages: Any) -> str:
    """What the conversation sent with a turn already said: the text of
    EVERY message in it (the owner's and the replies before this one), the
    newest first, at most :data:`CONVERSATION_CHARS` characters in all. It
    is the ``question`` :func:`finish` measures ``shaped`` against.

    The owner's last message alone is not enough: a short follow-up ("ok,
    so what next?") is recalled on with the owner's EARLIER messages
    (``chat_turn._compose_recall_query``), so memory is found by the
    conversation's own words, and a reply that repeats them (or an earlier
    reply's) would count an older chat on the same topic as having changed
    the answer. A word the conversation already had is not memory's
    (D-15.1: the box opens by itself only when memory changed the answer).
    Messages are objects with ``content`` or dicts. Never raises ("" on a
    fault: the owner's words are then not taken out, as before 15-a)."""
    parts: list[str] = []
    left = CONVERSATION_CHARS
    try:
        for m in reversed(list(messages or ())):
            if left <= 0:
                break
            content = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
            text = str(content or "")
            if not text.strip():
                continue
            piece = text[:left]
            parts.append(piece)
            left -= len(piece) + 1
    except Exception:  # noqa: BLE001
        log.debug("memory used: the conversation could not be read", exc_info=True)
        return ""
    return "\n".join(parts)


def _pick(items: "list[dict[str, Any]]", shaped: "list[bool]") -> "list[int]":
    """Which items the capped list keeps, as indexes in their own order:
    every one when they fit; else the items that shaped the reply first,
    then the rest, each tier taken one KIND at a time in turn (in the order
    the kinds were first told), so one kind (every kept preference, say)
    never fills the list. Pure."""
    if len(items) <= MAX_ITEMS:
        return list(range(len(items)))
    chosen: list[int] = []
    for tier in (True, False):
        groups: dict[str, list[int]] = {}
        for i, item in enumerate(items):
            if shaped[i] is tier:
                groups.setdefault(str(item.get("kind") or ""), []).append(i)
        queues = list(groups.values())
        while queues and len(chosen) < MAX_ITEMS:
            for q in queues:
                if q and len(chosen) < MAX_ITEMS:
                    chosen.append(q.pop(0))
            queues = [q for q in queues if q]
        if len(chosen) >= MAX_ITEMS:
            break
    return sorted(chosen)


def finish_with_more(
    items: Iterable[dict[str, Any]], *, reply: object = "", question: object = "",
) -> "tuple[list[dict[str, Any]], int]":
    """``(the used list, how many were given and not listed)``. Each item
    goes without its private words, plus ``shaped`` — True when the reply
    carries a word stem that came from the item and is not in ``question``:
    what the conversation already said (the chat lanes pass
    :func:`conversation`, every message sent with the turn; the page opens
    the pop-up by itself only for such an item). ``shaped`` is worked out
    for EVERY item before the cap, and the cap keeps the shaped items first
    (:func:`_pick`). ([], 0) when nothing was given. Never raises (a fault
    answers ([], 0))."""
    try:
        said = stems(reply)
        asked = stems(question, limit=CONVERSATION_CHARS)
        dropped = max(0, int(getattr(items, "dropped", 0) or 0))
        valid: list[dict[str, Any]] = []
        shaped: list[bool] = []
        for item in list(items or ()):
            if not isinstance(item, dict) or item.get("kind") not in KINDS:
                continue
            words = item.get("_words") or frozenset()
            valid.append(item)
            shaped.append(bool((frozenset(words) & said) - asked))
        out: list[dict[str, Any]] = []
        keep = _pick(valid, shaped)
        for i in keep:
            public = {k: v for k, v in valid[i].items() if not str(k).startswith("_")}
            public["fix"] = list(public.get("fix") or [])
            public["shaped"] = shaped[i]
            out.append(public)
        return out, len(valid) - len(keep) + dropped
    except Exception:  # noqa: BLE001
        log.debug("memory used: the list could not be finished", exc_info=True)
        return [], 0


def finish(items: Iterable[dict[str, Any]], *, reply: object = "", question: object = "") -> list[dict[str, Any]]:
    """The ``used`` list of :func:`finish_with_more`. Never raises."""
    return finish_with_more(items, reply=reply, question=question)[0]


def left_out(items: Iterable[dict[str, Any]]) -> int:
    """How many items were given and are NOT in the capped list (the cap
    keeps :data:`MAX_ITEMS`, whichever they are, plus what a hop told past
    its own cap): the reply's ``used_more``. Never raises (a fault answers 0)."""
    try:
        dropped = max(0, int(getattr(items, "dropped", 0) or 0))
        valid = sum(1 for i in list(items or ()) if isinstance(i, dict) and i.get("kind") in KINDS)
        return max(0, valid - MAX_ITEMS) + dropped
    except Exception:  # noqa: BLE001
        log.debug("memory used: the left-out count could not be made", exc_info=True)
        return 0


__all__ = [
    "CONVERSATION_CHARS", "FIXES", "FIX_CORRECT", "FIX_FORGET", "HOP_ITEMS", "KINDS", "LABEL_CHARS",
    "MAX_ITEMS", "PREFERENCES_HREF", "Told", "collecting", "conversation", "finish", "finish_with_more",
    "left_out", "make", "merge", "note", "run_collecting", "stems",
]
