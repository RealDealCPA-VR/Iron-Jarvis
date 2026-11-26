"""THINGS THE OWNER ASKED FOR THAT IRON JARVIS HAS NO FEATURE FOR (next leaps
20-a, build-out plan R2.12, idea 20 "Every 'I can't' becomes a suggestion").

Every lookup (``find_capability``) already says when nothing in Iron Jarvis
fits a request; nothing kept, grouped or showed those answers. This module
keeps them. Asked twice in 30 days, the request waits on Improvements
("Asked 3 times, no tool: send a fax", with the dates and the owner's own
words) for one of two answers: "Keep as a note for the next version" or "Not
needed" (``inbox/collect.missing_item``, ``daemon/routes/capability_misses``).

WHEN A TURN IS A MISS (:func:`miss_of`, read from the turn's OWN lookup
record, ``join.FOUND.last_answer``, at the END of the reply, in both chat
lanes; an agent run's from its ledger, :func:`keep_run_miss`): the LAST lookup

* answered nothing (``NOTHING_FOUND``), or
* its first line was "Not offered", or
* the request was NAMED by words no entry of the list knows: EVERY way the
  lookup named it (two or more) has such a word that is not a name (a word
  written with a capital mid-sentence, by the owner or the lookup:
  :func:`name_words`), and the query is not in another language
  (:func:`other_language`). MEASURED on the real index: the lookup has no
  relevance floor, so "send a fax / fax a document / fax machine" answers
  five lines, none about faxing, and "fax" is in all three ways. Review
  round 2, on a fresh set written before this rule (20 things Iron Jarvis
  cannot do, 30 it can, names, companies and three other languages among
  them): the first rule (a word no entry knows in the first way) kept 16 of
  the 20 and 11 of the 30; this one keeps 10 of the 20 and 0 of the 30, and
  0 of the reviewer's 13 doable requests;

and the reply then made no successful call that changes or creates something
(for ``unknown``, whose lookup listed lines, no call at all after it, whatever
became of it, other than a lookup: a line the reply took to fit, even one
that only reads, failed or was declined, is not "nothing for it";
:func:`done_another_way`). NEVER a miss: a lookup that
failed (``LOOKUP_FAILED``), one with no search words (``TOO_GENERAL``), one
read from part of the list (``partial``), a low-trust turn (the request may
be an injected one), a task from another computer, an agent run whose task
is not the owner's own words.

WHAT IS KEPT (D-20.1, settled: yes, on this PC only, secrets masked, removed
after 90 days, shown only on Improvements): the owner's message masked with
``failure_words.mask_failure_text`` (strict: a mask that faults keeps no
words) and cut to :data:`WORDS_CHARS`, the lookup's own words (masked the
same way), the kind, the "Not offered" line's id, the words no entry knows,
the chat, the surface, Local only (a group whose title was said only in
Local only chats is named to chat only in a turn that stays on this network:
``inbox/tools``, ``egress.may_recall_local_only_chats``). Rows older than
:data:`KEEP_DAYS` are removed (:func:`prune`, on every keep and once a day)
and the group's ``count`` is kept. Nothing is sent anywhere; no model is
called.

GROUPING IS DETERMINISTIC, no model (:func:`same_request`): a new miss joins
the group one of whose misses (or whose key, for a group whose rows are gone)
shares half or more of its words (the templates' Jaccard rule, on the search
words of all three ways the lookup named the request), or half or more of the
words no entry knows when their other search words share one too (names are
never among them). Measured: three fax lookups in different words share at
most 2 of 7 search words pairwise but all have "fax" as their unknown word
and "send" besides; "book a flight" shares neither. A miss that fits a group the owner already
answered joins it and is not listed again ("Not needed" is not asked again,
as the client and preference answers are not; a kept note gathers its later
asks for 20-c).

BLOCKING (SQLite): every caller is off the event loop. One keep at a time in
this process (:data:`_LOCK`), so two asks at once never make two groups.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections import Counter
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from typing import Any, Iterable

from . import index as _index
from .miss_models import (
    DECLINED,
    KIND_NOT_OFFERED,
    KIND_NOTHING,
    KIND_UNKNOWN,
    KINDS,
    NOTED,
    OPEN,
    CapabilityMiss,
    MissGroup,
)

log = logging.getLogger(__name__)

#: The lookup's tool name (the ledger's ``tool`` for a run).
LOOKUP = "find_capability"

#: The owner's words kept for one miss, at most (D-20.1).
WORDS_CHARS = 300
#: The lookup's words kept, at most (the index's own bound).
QUERY_CHARS = _index.QUERY_CHARS
#: Text masked before it is cut (a secret is masked whole, never half).
MASK_CHARS = 4000
#: Rows are removed after this many days; the group's count stays (D-20.1).
KEEP_DAYS = 90
#: A group is listed when it has :data:`LIST_AT` misses in this many days.
LIST_DAYS = 30
LIST_AT = 2
#: Two misses ask for the same thing at this share of their words.
SAME_AT = Fraction(1, 2)
#: Search words kept for one query.
WORDS_MAX = 24
#: Groups looked at for a new miss (the newest), and their misses.
GROUPS_SCAN = 500
MEMBERS_SCAN = 4000
#: Groups listed at most (the inbox's bound), and asks shown on one.
LIST_MAX = 30
ASKS_SHOWN = 4
#: A group's title, at most.
TITLE_CHARS = 120
#: Ledger rows of one agent run read for its miss (the newest).
RUN_ROWS = 400

_LOCK = threading.Lock()


# --------------------------------------------------------------------------- #
# pure helpers
# --------------------------------------------------------------------------- #
def naive(at: Any) -> "datetime | None":
    """A stamp as SQLite keeps it (naive UTC)."""
    if isinstance(at, str):
        try:
            at = datetime.fromisoformat(at.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(at, datetime):
        return None
    if at.tzinfo is not None:
        at = at.astimezone(timezone.utc).replace(tzinfo=None)
    return at


def _now(now: Any = None) -> datetime:
    return naive(now) or datetime.now(timezone.utc).replace(tzinfo=None)


def search_words(query: Any) -> frozenset[str]:
    """The search words of every way the lookup named the request (the
    index's own ``tokens``: its ``_WORD`` and ``_STOP``), at most
    :data:`WORDS_MAX`."""
    return frozenset(sorted(set(_index.tokens(str(query or "")[:QUERY_CHARS])))[:WORDS_MAX])


def _words_of(text: Any) -> frozenset[str]:
    return frozenset(w for w in str(text or "").split() if w)


def jaccard(a: Iterable[str], b: Iterable[str]) -> Fraction:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return Fraction(0)
    return Fraction(len(sa & sb), len(sa | sb))


def same_request(words_a: Iterable[str], unknown_a: Iterable[str],
                 words_b: Iterable[str], unknown_b: Iterable[str]) -> Fraction:
    """How surely two misses ask for the same thing: the larger of the two
    shares (all their search words; the words no entry knows) that reaches
    :data:`SAME_AT`, else 0. The words no entry knows join two misses only
    when their OTHER search words share one too (review: a word they share
    alone, a name above all, is not the same request)."""
    best = Fraction(0)
    share = jaccard(words_a, words_b)
    if share >= SAME_AT:
        best = share
    ua, ub = set(unknown_a), set(unknown_b)
    if ua and ub and (set(words_a) - ua) & (set(words_b) - ub):
        share = jaccard(ua, ub)
        if share >= SAME_AT and share > best:
            best = share
    return best


#: A word of the owner's (or the lookup's) written with a capital letter
#: and a small one, not at the start of a sentence: a name ("Maria",
#: "Acme"). A word all in capitals ("PDF", "DVD") is not one.
_RAW_WORD = re.compile(r"[^\s/]+")
_SENTENCE_END = re.compile(r"[.!?:;\"“”(\[]$")


def name_words(*texts: Any) -> frozenset[str]:
    """The search words of the names in *texts* (next leaps 20-a review): a
    word that starts with a capital letter, has a small one, and is not the
    first of its sentence (or of one way the lookup named the request). A
    name is the owner's detail, never the thing asked for: it never makes
    a request one Iron Jarvis has no feature for, nor joins two requests."""
    out: set[str] = set()
    for text in texts:
        for part in _index.parts(str(text or "")[:MASK_CHARS]) or [""]:
            start = True
            for raw in _RAW_WORD.findall(part):
                word = raw.strip("\"'“”‘’()[]{}<>,.!?:;")
                if word and not start and word[0].isupper() and any(c.islower() for c in word):
                    out.update(_index.tokens(word))
                start = bool(_SENTENCE_END.search(raw))
    return frozenset(out)


#: Common small words of Spanish, French, German, Italian and Portuguese that
#: are not English words a request is written with: one in a query says it
#: is not in English (the list is), whatever its other words matched.
_OTHER_LANGUAGE_WORDS = frozenset(
    "el los las un una unos unas del y para con por que es en al lo mi mis su sus como pero este esta "
    "le les des du et pour avec sur dans une mon mes ce cette je vous nous aux "
    "der das und mit ein eine einen dem zu von ist nicht ich mein meine bitte auf "
    "il di che gli della uno sono mio questo os um uma em da dos das com meu minha".split()
)


def other_language(query: Any) -> bool:
    """Does *query* read as another language than English? A letter outside
    English ("cálculo", "kürzen", "écrire") or one of
    :data:`_OTHER_LANGUAGE_WORDS` ("una", "des", "und").
    Measured (20-a review): the lookup's own test, "fewer than half the
    words known", is true for most real requests Iron Jarvis has nothing
    for ("order a pizza for delivery / food delivery order / restaurant
    takeout" knows 1 of 6), so it cannot tell a language from a gap."""
    text = str(query or "")[:QUERY_CHARS]
    if any(ch.isalpha() and not ch.isascii() for ch in text):
        return True
    return any(w in _OTHER_LANGUAGE_WORDS for w in re.findall(r"[a-z]+", text.lower()))


def _named_by_unknown(answer: dict[str, Any], names: frozenset[str]) -> bool:
    """Was the request NAMED by words no entry of the list knows? Only when
    every way the lookup named it (two or more) has such a word that is not
    a name, and the query does not read as another language (the list is in
    English: a Spanish query knows none of its words, :func:`other_language`).
    Measured: "send a fax / fax a document / fax machine" has "fax" in all
    three; "calculate a mortgage payment / loan calculator / payment math"
    can be named in words the list knows, so it is not a request nothing in
    Iron Jarvis is about."""
    if other_language(answer.get("query")):
        return False
    parts = [p for p in (answer.get("unknown_parts") or []) if isinstance(p, list)]
    if len(parts) < 2:
        return False
    return all(any(str(w) and str(w) not in names for w in part) for part in parts)


def miss_of(answer: Any, *, words: Any = "") -> "tuple[str, str, list[str]] | None":
    """``(kind, entry_id, unknown words)`` when one lookup's answer
    (``tool.answer_record``) is a miss, else None. *words*: the owner's own
    words of the turn (their names are never unknown words). Pure."""
    if not isinstance(answer, dict):
        return None
    reason = str(answer.get("reason") or "")
    if reason in ("failed", "no_search_words") or answer.get("partial"):
        return None
    if not _index.has_search_words(answer.get("query")):
        return None
    names = name_words(answer.get("query"), words)
    unknown = [str(w) for w in (answer.get("unknown") or []) if str(w or "").strip() and str(w) not in names]
    if reason == "nothing":
        return KIND_NOTHING, "", unknown
    first = answer.get("first") if isinstance(answer.get("first"), dict) else {}
    if str(first.get("kind") or "") == "not_offered":
        return KIND_NOT_OFFERED, str(first.get("id") or ""), unknown
    if unknown and _named_by_unknown(answer, names):
        return KIND_UNKNOWN, "", unknown
    return None


def said_unknown(query: Any, unknown: Any, cap: int = 3) -> list[str]:
    """The owner-readable words behind *unknown* (search words, stemmed):
    the words of the query's first part whose search word is one of them,
    lower-cased, each once, at most *cap* ("fax", never "fax" stemmed)."""
    wanted = set(_words_of(unknown) if isinstance(unknown, str) else (str(w) for w in unknown or ()))
    out: list[str] = []
    for raw in _RAW_WORD.findall(_index.first_part(query)):
        word = raw.strip("\"'“”‘’()[]{}<>,.!?:;").lower()
        if word and word not in out and set(_index.tokens(word)) & wanted:
            out.append(word)
        if len(out) >= cap:
            break
    return out


def changed_something(names: Iterable[str], registry: Any) -> bool:
    """Did any of *names* (calls that RAN this reply, after the lookup)
    change or create something? A tool that cannot be told is taken as one
    that did (a miss is a suggestion: when unsure, none is kept)."""
    from .join import _reads

    for name in names:
        name = str(name or "")
        if not name or name == LOOKUP:
            continue
        try:
            tool = registry.get(name) if registry is not None else None
        except Exception:  # noqa: BLE001
            tool = None
        if tool is None or not _reads(tool, name):
            return True
    return False


def done_another_way(kind: str, names: Iterable[str], registry: Any, *, tried: Iterable[str] = ()) -> bool:
    """Was the request done another way? *names*: the calls that RAN
    (successfully) after the reply's last lookup; *tried*: every call the
    model MADE after it (run, failed, refused or declined on its card).

    * ``unknown``: the lookup DID list lines (it has no relevance floor), so
      ANY call after it, another lookup aside, means the reply took one of
      them to fit, even one that only reads, failed or was declined
      ("calculate a mortgage payment" answers ``calculate``): never a miss.
    * ``nothing`` / ``not_offered`` (the plan's rule): a call that changes or
      creates something (:func:`changed_something`). A read alone (the form
      read before "I can't fax it") is not doing the request."""
    if kind == KIND_UNKNOWN:
        return any(str(n or "") and str(n) != LOOKUP for n in (*names, *tried))
    return changed_something(names, registry)


def calls_after_last_lookup(names: Iterable[str]) -> list[str]:
    """The calls that ran after the reply's last lookup (all of them when no
    lookup is among them: the safe reading)."""
    got = [str(n or "") for n in names or ()]
    for at in range(len(got) - 1, -1, -1):
        if got[at] == LOOKUP:
            return got[at + 1:]
    return got


def _clean(text: Any, cap: int) -> str:
    """*text* masked WHOLE (strict: a mask that faults raises), then cut."""
    from ..providers.failure_words import mask_failure_text

    masked = mask_failure_text(str(text or "")[:MASK_CHARS], strict=True)
    words = " ".join(str(masked or "").split())
    return words[:cap].rstrip()


def clean_words(text: Any) -> str:
    """The owner's words as kept: masked, at most :data:`WORDS_CHARS`; ""
    when the mask could not run (never the words unmasked)."""
    try:
        return _clean(text, WORDS_CHARS)
    except Exception:  # noqa: BLE001 — no words beats unmasked words
        log.warning("a miss's words could not be masked, so they are not kept")
        return ""


def title_of(query: Any) -> str:
    return " ".join(_index.first_part(query).split())[:TITLE_CHARS].rstrip()


# --------------------------------------------------------------------------- #
# keeping
# --------------------------------------------------------------------------- #
def _pick_title(rows: list[tuple[Any, str, str, bool]]) -> tuple[str, str, str, bool]:
    """``(title, query, unknown, local_only)`` of the group's most common
    request (its first part, case-folded); a tie goes to the earliest.
    ``local_only``: every ask with those words was made in a Local only chat
    (an ask outside one with the same words says them anyway). *rows*:
    ``(at, query, unknown, local_only)``."""
    ordered = sorted(rows, key=lambda r: (naive(r[0]) or datetime.min))
    counts = Counter(title_of(r[1]).casefold() for r in ordered if title_of(r[1]))
    if not counts:
        return "", "", "", False
    top = max(counts.values())
    for _at, query, unknown, _local in ordered:
        folded = title_of(query).casefold()
        if counts.get(folded) == top:
            local = all(bool(r[3]) for r in ordered if title_of(r[1]).casefold() == folded)
            return title_of(query), query, unknown, local
    return "", "", "", False


def keep(engine: Any, *, words: Any, query: Any, kind: str, entry_id: str = "",
         unknown: Iterable[str] = (), thread_id: str = "", surface: str = "",
         local_only: bool = False, now: Any = None) -> str:
    """Keep ONE miss and group it; returns its group's id. Removes rows past
    :data:`KEEP_DAYS` afterwards. BLOCKING; RAISES when the query cannot be
    masked or the write fails (the caller says nothing in the reply: a miss
    is never part of an answer)."""
    from sqlmodel import select

    from ..core.db import session_scope

    if kind not in KINDS:
        raise ValueError(f"not a kind of miss: {kind!r}")
    at = _now(now)
    kept_query = _clean(query, QUERY_CHARS)
    if not _index.has_search_words(kept_query):
        raise ValueError("a miss needs the lookup's own words")
    kept_words = clean_words(words)
    mine_words = search_words(kept_query)
    mine_unknown = frozenset(sorted({str(w) for w in unknown if str(w or "").strip()})[:WORDS_MAX])
    with _LOCK:
        with session_scope(engine) as db:
            groups = list(db.exec(
                select(MissGroup).order_by(MissGroup.last_at.desc()).limit(GROUPS_SCAN)
            ))
            ids = [g.id for g in groups]
            members: dict[str, list[tuple[Any, str, str, bool]]] = {}
            if ids:
                rows = db.exec(
                    select(CapabilityMiss.group_id, CapabilityMiss.created_at, CapabilityMiss.query,
                           CapabilityMiss.unknown, CapabilityMiss.local_only)
                    .where(CapabilityMiss.group_id.in_(ids))  # type: ignore[attr-defined]
                    .order_by(CapabilityMiss.created_at.desc()).limit(MEMBERS_SCAN)
                ).all()
                for gid, created, q, u, lo in rows:
                    members.setdefault(str(gid), []).append((created, str(q or ""), str(u or ""), bool(lo)))
            best: "MissGroup | None" = None
            best_share = Fraction(0)
            for group in groups:
                share = same_request(mine_words, mine_unknown, _words_of(group.key), _words_of(group.unknown))
                for _created, q, u, _lo in members.get(group.id, ()):
                    share = max(share, same_request(mine_words, mine_unknown, search_words(q), _words_of(u)))
                if share > best_share:  # ties keep the newest group (the list is newest first)
                    best, best_share = group, share
            group = best
            if group is None:
                group = MissGroup(first_at=at, last_at=at, count=0)
                db.add(group)
                db.flush()
            unknown_text = " ".join(sorted(mine_unknown))
            db.add(CapabilityMiss(
                words=kept_words, query=kept_query, kind=kind, entry_id=str(entry_id or "")[:120],
                unknown=unknown_text, thread_id=str(thread_id or "")[:200], surface=str(surface or "")[:40],
                local_only=bool(local_only), group_id=group.id, created_at=at,
            ))
            group.count = int(group.count or 0) + 1
            first_at, last_at = naive(group.first_at), naive(group.last_at)
            group.first_at = min(first_at, at) if first_at else at
            group.last_at = max(last_at, at) if last_at else at
            title, title_query, title_unknown, title_local = _pick_title(
                [*members.get(group.id, ()), (at, kept_query, unknown_text, bool(local_only))]
            )
            group.title = title or title_of(kept_query)
            group.title_local_only = title_local if title else bool(local_only)
            group.key = " ".join(sorted(search_words(title_query or kept_query)))
            group.unknown = title_unknown if title_query else unknown_text
            db.add(group)
            db.commit()
            gid = group.id
    try:
        # Past 90 days by the clock (never by *now*: a stamp given for an
        # older ask must not keep rows the owner was told are removed).
        prune(engine)
    except Exception:  # noqa: BLE001 — the daily pass removes them next
        log.warning("old misses could not be removed just now", exc_info=True)
    return gid


def prune(engine: Any, *, now: Any = None) -> int:
    """Remove the misses older than :data:`KEEP_DAYS` (their groups and
    counts stay). Returns how many were removed. BLOCKING; RAISES."""
    from sqlalchemy import delete

    from ..core.db import session_scope

    cutoff = _now(now) - timedelta(days=KEEP_DAYS)
    with session_scope(engine) as db:
        got = db.exec(delete(CapabilityMiss).where(CapabilityMiss.created_at < cutoff))  # type: ignore[call-overload]
        db.commit()
        return int(getattr(got, "rowcount", 0) or 0)


# --------------------------------------------------------------------------- #
# reading
# --------------------------------------------------------------------------- #
def waiting(engine: Any, *, now: Any = None) -> tuple[list[dict[str, Any]], int]:
    """The groups to list on Improvements (open, :data:`LIST_AT` or more
    misses in the last :data:`LIST_DAYS` days), newest first, at most
    :data:`LIST_MAX`, each with its kept asks; and how many there are in
    all. BLOCKING; RAISES when the tables cannot be read (the inbox names
    the kind, never "nothing waiting")."""
    from sqlalchemy import func, text
    from sqlmodel import select

    from ..core.db import session_scope

    at = _now(now)
    recent = at - timedelta(days=LIST_DAYS)
    kept = at - timedelta(days=KEEP_DAYS)
    with session_scope(engine) as db:
        often = (
            select(CapabilityMiss.group_id)
            .where(CapabilityMiss.created_at >= recent)
            .group_by(CapabilityMiss.group_id)
            .having(func.count() >= LIST_AT)
        )
        listed = (MissGroup.status == OPEN, MissGroup.id.in_(often))  # type: ignore[attr-defined]
        # Bounded in the query itself; the total is a real count of all.
        total = int(db.exec(select(func.count()).select_from(MissGroup).where(*listed)).one() or 0)
        shown = list(db.exec(
            select(MissGroup).where(*listed)
            .order_by(MissGroup.last_at.desc(), text("rowid DESC")).limit(LIST_MAX)
        ))
        asks: dict[str, list[CapabilityMiss]] = {}
        if shown:
            for row in db.exec(
                select(CapabilityMiss)
                .where(CapabilityMiss.group_id.in_([g.id for g in shown]),  # type: ignore[attr-defined]
                       CapabilityMiss.created_at >= kept)
                .order_by(CapabilityMiss.created_at, text("rowid"))
            ):
                asks.setdefault(row.group_id, []).append(row)
    out = []
    for g in shown:
        mine = asks.get(g.id, [])
        said: list[str] = []
        for m in mine:
            if m.kind == KIND_UNKNOWN:
                said += [w for w in said_unknown(m.query, m.unknown) if w not in said]
        out.append({
            # 20-a review: the words of its asks that nothing Iron Jarvis has
            # mentions, as the owner reads them (the page's why says them).
            "unknown_said": said[:3],
            "id": g.id, "title": g.title, "count": int(g.count or 0),
            # The title's words were said only in Local only chats: chat names
            # them only to a turn that stays on this network (inbox/tools).
            "local_only": bool(g.title_local_only),
            "first_at": naive(g.first_at), "last_at": naive(g.last_at),
            "kinds": sorted({m.kind for m in mine}),
            "asks": [{"at": naive(m.created_at), "words": m.words, "kind": m.kind} for m in mine],
            "kept": len(mine),
        })
    return out, total


class NotFound(LookupError):
    """No such group."""


class AlreadyAnswered(ValueError):
    """The group was answered already (or is not waiting)."""


def answer(engine: Any, group_id: str, status: str, *, now: Any = None) -> dict[str, Any]:
    """The owner's answer to ONE group: :data:`NOTED` ("Keep as a note for
    the next version") or :data:`DECLINED` ("Not needed"). Saves the answer
    and nothing else: nothing is sent anywhere. BLOCKING. Raises
    :class:`NotFound` / :class:`AlreadyAnswered`."""
    from ..core.db import session_scope

    if status not in (NOTED, DECLINED):
        raise ValueError(f"not an answer: {status!r}")
    with _LOCK:
        with session_scope(engine) as db:
            group = db.get(MissGroup, str(group_id or ""))
            if group is None:
                raise NotFound(group_id)
            if group.status != OPEN:
                raise AlreadyAnswered(group.status)
            group.status = status
            group.decided_at = _now(now)
            db.add(group)
            db.commit()
            return {"id": group.id, "status": group.status, "title": group.title, "count": int(group.count or 0)}


# --------------------------------------------------------------------------- #
# an agent run (its ledger)
# --------------------------------------------------------------------------- #
_NOT_OFFERED_LEAD = "1. Not offered: "
_PARTIAL_MARK = "could not be read just now, so this answer may be missing some"


def answer_from_ledger(output: Any, args_json: Any, platform: Any = None) -> dict[str, Any]:
    """One lookup's answer record (``tool.answer_record``'s shape), read back
    from its ledger row: the answer's OWN words say which it was. The words
    no entry knows are worked out again from the index (*platform*)."""
    from . import tool as _tool

    text = str(output or "")
    try:
        args = json.loads(args_json) if isinstance(args_json, str) else dict(args_json or {})
    except (TypeError, ValueError):
        args = {}
    query = " ".join(str((args or {}).get("query") or "").split())[:QUERY_CHARS]
    record: dict[str, Any] = {"query": query, "reason": "", "first": None, "partial": False, "unknown": [],
                              "unknown_parts": []}
    if text.startswith(_tool.LOOKUP_FAILED):
        record["reason"] = "failed"
        return record
    if text.startswith(_tool.TOO_GENERAL):
        record["reason"] = "no_search_words"
        return record
    if text.startswith(_tool.NOTHING_FOUND):
        record["reason"] = "nothing"
    record["partial"] = _PARTIAL_MARK in text
    line = next((ln for ln in text.splitlines() if ln.startswith("1. ")), "")
    if line.startswith(_NOT_OFFERED_LEAD):
        said = line[len(_NOT_OFFERED_LEAD):].strip()
        entry = ""
        try:
            from ..daemon.page_doors import NOT_OFFERED

            entry = next((i.id for i in NOT_OFFERED if " ".join(i.line.split()) == " ".join(said.split())), "")
        except Exception:  # noqa: BLE001 — the line is still "not offered"
            entry = ""
        record["first"] = {"kind": "not_offered", "id": entry}
    elif line:
        record["first"] = {"kind": "", "id": ""}
    try:
        if platform is not None and query:
            idx = _index.index_for(platform)
            record["unknown"] = idx.unknown_words(_index.first_part(query))
            record["unknown_parts"] = [idx.unknown_words(part) for part in _index.parts(query)]
    except Exception:  # noqa: BLE001 — no unknown words: only nothing / not offered count
        record.update(unknown=[], unknown_parts=[])
    return record


def keep_run_miss(platform: Any, session_id: str, *, words: Any, local_only: bool = False,
                  now: Any = None) -> "str | None":
    """An agent run's miss, from its OWN ledger: its last successful lookup,
    and the calls after it (a successful one that changes something, read
    from the row's own ``reversibility``, means it was done another way; a
    rehearsal's row changed nothing; for ``unknown``, ANY call after it,
    ok or not, as :func:`done_another_way`). The caller checks whose words
    the task is, the run's trust and where it came from. Returns the group's id, or
    None. BLOCKING; RAISES."""
    from sqlalchemy import text
    from sqlmodel import select

    from ..core.db import session_scope
    from ..core.models import ToolInvocation as T

    engine = platform.engine
    with session_scope(engine) as db:
        rows = list(db.exec(
            select(T).where(T.session_id == str(session_id))
            .order_by(T.created_at.desc(), text("rowid DESC")).limit(RUN_ROWS)
        ))
    rows.reverse()
    last = next((i for i in range(len(rows) - 1, -1, -1) if rows[i].tool == LOOKUP and rows[i].ok), -1)
    if last < 0:
        return None
    record = answer_from_ledger(rows[last].output, rows[last].args_json, platform)
    found = miss_of(record, words=words)
    if found is None:
        return None
    kind, entry_id, unknown = found
    for row in rows[last + 1:]:
        if row.tool == LOOKUP:
            continue
        if kind == KIND_UNKNOWN:
            # The lookup listed lines: a call after it, even one that only
            # reads, failed or was refused, is a line the run took to fit
            # (``done_another_way``).
            return None
        if not row.ok or getattr(row, "rehearsed", False):
            continue
        if str(row.reversibility or "") != "readonly" and row.tool not in _index.READS_ONLY:
            return None
    return keep(engine, words=words, query=record["query"], kind=kind, entry_id=entry_id, unknown=unknown,
                thread_id=str(session_id), surface="run", local_only=local_only, now=now)


__all__ = [
    "ASKS_SHOWN", "KEEP_DAYS", "LIST_AT", "LIST_DAYS", "LIST_MAX", "WORDS_CHARS", "AlreadyAnswered", "NotFound",
    "answer", "answer_from_ledger", "calls_after_last_lookup", "changed_something", "clean_words",
    "done_another_way", "keep",
    "keep_run_miss", "miss_of", "prune", "same_request", "search_words", "waiting",
]
