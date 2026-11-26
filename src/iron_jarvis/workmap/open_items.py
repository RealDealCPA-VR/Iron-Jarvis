"""Open items that stay until they are done (next leaps 04-b; build-out plan
R2.13, idea 04 "Pick up where you left off";
``docs/next-leaps/01-comes-to-you-and-scroll-back.md``).

A recap (``workmap/recap.py``) keeps checked ``Open:`` lines for ONE
conversation, and 04-a showed them only while that conversation's recap was
among the newest six pieces of work and still current. Here each such line
becomes an OPEN ITEM (``WorkOpenItem``, table ``workopenitem``) that stays on
the folder's list, however old, until it is closed:

* **by the owner**: Done, or No longer needed (the card's ⋯ menu, or a chat
  the owner asks: ``open_item_tools``); Reopen puts it back;
* **by its own conversation**: a rewritten recap that no longer lists the
  line SETTLES it (``closed_how`` "conversation"); it stays visible under
  "Closed lately" for :data:`CLOSED_LATELY_DAYS` with Reopen. An item the
  owner reopened is never settled again by a recap (``owner_kept``).

THE RECORD DECIDES (vision plan rule 3, decision D-04.3). Only CHECKED recap
lines become items, and only through :func:`sync`, called where a recap is
kept (``recap._store``), IN THE SAME WRITE. :func:`may_be_done` is a pure
match (a shared file name, figure or quoted span, or three shared content
words, with a LATER Done line in the same folder): it only sets
``maybe_done_entry`` so the card asks "This may be done. Mark it done?". It
never closes anything.

WHAT A TURN IS TOLD (:func:`told` then :func:`block_part`): through THE ONE
READER (``lookup.Reader``), as the "In this folder" part is: items in
EXACTLY the folder (a coding session's own copy counts as its repository),
each item's piece of work ASKED AGAIN (deleted, archived or forgotten work
takes its items out of every answer; Local only and kept-home work only for
a turn that stays on this network), behind the client wall, never on the
phone, every line masked and scanned (``block._safe``; a flagged line is
left out). The turn's own conversation is never listed. The block tells at
most :data:`TOLD_MAX`, oldest first, inside :data:`ITEMS_CHARS`, plus the one
the owner picked (``pick_up_item``) wherever it is.

A NEW LINE IS NEVER LOST (review of 04-b): a rewritten recap's line is the
same item as an earlier one only by equal :func:`text_key`, or, for an item
still open or settled (never one the owner closed), by :func:`same_line`:
reworded only in plain words, never in a name, a month or day, or a figure.
So "the April deposit" after "the March deposit" is its own item. ONE
carrier at a time (:data:`WRITING`): two readers' :func:`backfill` never
carry a recap twice. A closed item's Open line is never told as open under
its piece of work (:func:`closed_keys`, ``block._attach_recaps``).

THE SAME LINE IN TWO CONVERSATIONS (review 2 of 04-b): Continue starts a
new chat on the same work, and its recap often lists the same Open line.
Each conversation keeps its OWN copy (so its own rewritten recap settles
only that copy, and deleting one conversation leaves the line on the list),
but the copies of one folder with exactly the same words (equal
:func:`text_key`; never the loose :func:`same_line`) are ONE item to the
owner: :func:`told` tells and shows it once (the oldest copy the turn may be
told; "may be done" when any copy asks it), Closed lately never shows a line
that is open; Done or No longer needed closes every open copy
(:func:`close`), Reopen reopens the copies THAT answer closed (never a copy
the owner closed in another answer, or one its own conversation settled:
:func:`_same_answer`, review 4) and Not yet answers every open one
(:func:`reopen`) and holds for every copy, one written later too
(:func:`_declined_by_words`, review 12), and a tool's Undo puts them all back
(:func:`snapshot`). A NEW copy whose words the owner closed in another
conversation in the last :data:`CLOSED_LATELY_DAYS` is written closed only
when ITS conversation was already going when the owner answered
(``WorkEntry.started_at`` at or before that ``closed_at``;
:func:`_owner_closed_twin`): the owner's answer stands for the work it was
given on, and Reopen brings it back. Such a copy says so (``closed_how``
:data:`~.models.CLOSED_WITH_COPY`, ``closed_ref`` the owner's item): it is
never recorded as an answer of the owner's own. A conversation started
AFTER the answer (each week's "Submit the payroll file to the bank"), one
whose start is not known, and any copy after that window are new work: open
(review 3 of 04-b).

Removed with their work: ``WorkMap.forget`` and a source that is gone
(``WorkMap._refresh``) delete the items of those entries; a recap a reply
marked low trust AFTER it was kept takes its items with it (``drop_entries``).

Everything here is BLOCKING (SQLite) and runs off the event loop.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import delete as sa_delete
from sqlalchemy import or_
from sqlmodel import select

from ..core.db import session_scope, storable_text
from ..core.ids import new_id
from . import keys
from .models import (
    CLOSED_BY_CHAT,
    CLOSED_BY_CONVERSATION,
    CLOSED_BY_YOU,
    CLOSED_WITH_COPY,
    OPEN_ITEM_DONE,
    OPEN_ITEM_DROPPED,
    OPEN_ITEM_OPEN,
    OPEN_ITEM_SETTLED,
    RECAP_KEPT,
    WorkEntry,
    WorkOpenItem,
    WorkRecap,
)

log = logging.getLogger(__name__)

#: An item's words, at most (a recap line's own cap).
TEXT_CHARS = 220
#: A closed item stays under "Closed lately" this long (with Reopen).
CLOSED_LATELY_DAYS = 14
#: Items of one folder read at most (open ones, then closed lately).
OPEN_READ = 200
LATELY_MAX = 20
#: Open items the work block tells, at most (the one the owner picked is
#: told on top), and the room they take in it, at most.
TOLD_MAX = 6
ITEMS_CHARS = 1500
#: Recaps carried forward by one :func:`backfill` call, at most.
BACKFILL_BATCH = 400
#: Later pieces of work one item remembers a "Not yet" for, at most (newest
#: kept): a year of weekly work.
DECLINED_KEEP = 52

#: What the owner may close an item AS.
CLOSE_AS = {"done": OPEN_ITEM_DONE, "dropped": OPEN_ITEM_DROPPED}
CLOSED_STATUSES = (OPEN_ITEM_DONE, OPEN_ITEM_DROPPED, OPEN_ITEM_SETTLED)

ITEMS_LEAD = (
    "Still open in this folder, oldest first (carried from the checked recaps of earlier work "
    "here until the owner closes them; a record of then, not a request now; [open_…] is the "
    "item's id):"
)

_LABEL_RX = re.compile(r"^(done|decided|open)\s*:\s*(.+)$", re.IGNORECASE)
_TOKEN_RX = re.compile(r"\d+(?:[.,]\d+)*|[a-z]+")
#: Words that never tell two items apart.
_KEY_STOP = frozenset(
    """
    a an the is are was were be been being to of for on in at by with and or still yet now it
    its this that these those has have had will we our you your they them there here then
    """.split()
)
_NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12",
}
#: Two keys are one item when this share of their words is the same (and at
#: least two words are) and no figure differs.
SAME_SHARE = 0.6
#: Content words a Done line must share with an item to ask "Mark it done?".
DONE_WORDS = 3


class Unreadable(RuntimeError):
    """The open items, or the work they belong to, could not be read."""


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _naive(value: Any) -> "datetime | None":
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo is None else value.astimezone(timezone.utc).replace(tzinfo=None)


def _iso(value: Any) -> "str | None":
    v = _naive(value)
    return v.replace(tzinfo=timezone.utc).isoformat() if v is not None else None


# --------------------------------------------------------------------------- #
# pure: reading recap lines, matching items
# --------------------------------------------------------------------------- #
def labelled(lines: Iterable[Any]) -> list[tuple[str, str]]:
    """``[("open", "Call the bank"), ("done", …)]`` for a recap's lines
    (``"Open: …"``), in order; a line with no label is left out. Pure."""
    out: list[tuple[str, str]] = []
    for raw in lines or ():
        m = _LABEL_RX.match(" ".join(str(raw or "").split()))
        if not m:
            continue
        text = m.group(2).strip()
        if text:
            out.append((m.group(1).lower(), text[:TEXT_CHARS]))
    return out


def _key_word(tok: str) -> "str | None":
    """One lower-case token as :func:`text_key` keeps it, or None."""
    from ..core.numbers import number_key

    if tok[0].isdigit():
        return number_key(tok)
    if tok in _NUMBER_WORDS:
        return _NUMBER_WORDS[tok]
    if tok in _KEY_STOP:
        return None
    if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
        tok = tok[:-1]
    return tok


def _key_words(text: Any) -> list[str]:
    low = keys._no_apostrophes(str(text or "")[:2000].lower())
    return [w for w in (_key_word(tok) for tok in _TOKEN_RX.findall(low)) if w]


def text_key(text: Any) -> str:
    """How an item's words are compared: lower case, figures as one key each
    ("1,250.00" is "1250", "two" is "2"), the words that never tell items
    apart left out, a plural's "s" dropped; a line made only of such words
    is its own words, lower case. Pure."""
    return " ".join(_key_words(text)) or " ".join(str(text or "").lower().split())[:TEXT_CHARS]


def same_item(a: str, b: str) -> bool:
    """Are two text keys the SAME item reworded ("Ask Ann about the March
    deposit" / "Ask Ann about March deposit")? Equal keys, or no figure that
    differs and at least two words and :data:`SAME_SHARE` of them shared.
    "Send the March invoice" and "Send the April invoice" are two. Pure."""
    if not a or not b:
        return False
    if a == b:
        return True
    aw, bw = set(a.split()), set(b.split())
    af = {w for w in aw if w[:1].isdigit()}
    bf = {w for w in bw if w[:1].isdigit()}
    if af and bf and af != bf:
        return False
    shared = aw & bw
    return len(shared) >= 2 and len(shared) / max(1, len(aw | bw)) >= SAME_SHARE


#: Words that always tell two lines apart, whatever their case: a month or a
#: day ("the March deposit" / "the April deposit" are two items).
_TELLS_APART = frozenset(
    """
    january february march april may june july august september october november december
    jan feb mar apr jun jul aug sep sept oct nov dec
    monday tuesday wednesday thursday friday saturday sunday
    """.split()
)
_CASED_RX = re.compile(r"[A-Za-z]+")


def _written_as_names(text: Any) -> set[str]:
    """The keys of the words ``text`` writes with a capital letter ("Ann",
    "Hartleys", "IRS"), the first word included: a name may start a line."""
    out: set[str] = set()
    for tok in _CASED_RX.findall(keys._no_apostrophes(str(text or "")[:2000])):
        if tok[0].isupper():
            got = _key_word(tok.lower())
            if got:
                out.add(got)
    return out


def same_line(a: Any, b: Any) -> bool:
    """Are two recap lines (their own words) the SAME open item? Equal
    :func:`text_key`, or :func:`same_item` AND every word that differs is a
    plain one: never a figure, a month or a day, nor a word either line
    writes with a capital letter (a name). So "Ask Ann about the March
    deposit" / "... again" is one item, and "Send the Hartleys letter" /
    "Send the Smith letter", "the March deposit" / "the April deposit" and
    "Ask Ann" / "Ask Bob" are two (review of 04-b: a new line must never be
    taken for an earlier one and lost). Pure."""
    ka, kb = text_key(a), text_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    if not same_item(ka, kb):
        return False
    differ = set(ka.split()) ^ set(kb.split())
    if any(w[:1].isdigit() or w in _TELLS_APART for w in differ):
        return False
    return not differ & (_written_as_names(a) | _written_as_names(b))


def _quoted(text: str) -> set[str]:
    from .recap import _QUOTE_RXS

    out: set[str] = set()
    for rx in _QUOTE_RXS:
        for m in rx.finditer(text):
            said = " ".join(m.group(1).lower().split()).strip(" .,;:!?")
            if said:
                out.add(said)
    return out


def _figures(text: str) -> set[str]:
    from ..core.numbers import figures

    return {f.key for f in figures(text)}


def may_be_done(item: str, done: str) -> bool:
    """Does a LATER Done line seem to settle this item? A shared file name, a
    shared figure worth a record, a shared quoted span, or
    :data:`DONE_WORDS` shared content words. It only ASKS (D-04.3); nothing
    is ever closed on it. Pure."""
    from .recap import _file_names

    a, b = str(item or ""), str(done or "")
    if not a.strip() or not b.strip():
        return False
    if _file_names(a) & _file_names(b):
        return True
    if _figures(a) & _figures(b):
        return True
    if _quoted(a) & _quoted(b):
        return True
    return len(keys.words(a) & keys.words(b)) >= DONE_WORDS


# --------------------------------------------------------------------------- #
# the same line in two conversations (review 2 of 04-b)
# --------------------------------------------------------------------------- #
def _twins(db: Any, it: WorkOpenItem) -> list[WorkOpenItem]:
    """The OTHER items of ``it``'s folder with exactly its words
    (:func:`text_key`): other conversations' copies of the same line, which
    the owner sees and answers as ONE item. Inside the caller's session."""
    key = it.text_key or text_key(it.text)
    if not it.folder_key or not key:
        return []
    return list(db.exec(
        select(WorkOpenItem)
        .where(WorkOpenItem.folder_key == it.folder_key)
        .where(WorkOpenItem.text_key == key)
        .where(WorkOpenItem.id != it.id)
        .limit(OPEN_READ)
    ))


def _owner_closed_twin(db: Any, fkey: str, key: str, entry_id: str, when: datetime,
                       started: "datetime | None") -> "WorkOpenItem | None":
    """For a NEW Open line of ``entry_id`` (whose conversation started at
    ``started``): the item of another conversation in the same folder with
    exactly these words that the owner closed (Done or No longer needed, by
    the card or a chat) in the last :data:`CLOSED_LATELY_DAYS` while this
    conversation was already going (``started`` at or before its
    ``closed_at``), the newest, when no copy of the line is still open. None
    otherwise: the line is open (a copy still open, one settled by its own
    conversation, a conversation started after the owner answered or whose
    start is not known, or a Done older than Closed lately: new work)."""
    if not fkey or not key or started is None:
        return None
    rows = list(db.exec(
        select(WorkOpenItem)
        .where(WorkOpenItem.folder_key == fkey)
        .where(WorkOpenItem.text_key == key)
        .where(WorkOpenItem.entry_id != entry_id)
        .limit(OPEN_READ)
    ))
    if any(r.status == OPEN_ITEM_OPEN for r in rows):
        return None
    since = when - timedelta(days=CLOSED_LATELY_DAYS)

    def answered_while_going(r: WorkOpenItem) -> bool:
        # The owner's own answer (never another copy's), lately, and given
        # while this conversation was already going.
        at = _naive(r.closed_at)
        return (r.status in (OPEN_ITEM_DONE, OPEN_ITEM_DROPPED) and r.closed_how in (CLOSED_BY_YOU, CLOSED_BY_CHAT)
                and at is not None and at > since and at >= started)

    shut = [r for r in rows if answered_while_going(r)]
    return max(shut, key=lambda r: (_naive(r.closed_at), r.id)) if shut else None


# --------------------------------------------------------------------------- #
# writing: in the same write as a kept recap
# --------------------------------------------------------------------------- #
def sync(db: Any, entry_id: str, lines: Iterable[Any], *, now: "datetime | None" = None) -> dict[str, int]:
    """Carry one KEPT recap's lines into the open items, inside the caller's
    session (``recap._store``: the same write that keeps the recap; the
    caller commits). New Open lines are added; an open item of this piece of
    work the rewritten recap no longer lists is SETTLED (never one the owner
    reopened); a settled one it lists again is open again; one the owner
    closed stays closed. The recap's Done lines then ask "Mark it done?"
    about OTHER pieces' open items in the same folder (:func:`may_be_done`).
    Work no longer on record: nothing. ``{added, settled, reopened, asked}``."""
    stats = {"added": 0, "settled": 0, "reopened": 0, "asked": 0}
    entry = db.get(WorkEntry, entry_id)
    if entry is None:
        return stats
    when = _naive(now) or _now()
    started = _naive(entry.started_at)
    folder = keys.home_folder(entry.folder or "")
    fkey = keys.folder_key(folder)
    local = bool(entry.local_only or entry.keep_home)
    pairs = labelled(lines)
    opens: list[tuple[str, str]] = []
    seen: set[str] = set()
    for label, text in pairs:
        if label != "open":
            continue
        key = text_key(text)
        if not key or key in seen:
            continue
        seen.add(key)
        opens.append((storable_text(text), key))
    dones = [text for label, text in pairs if label == "done"]

    mine = list(db.exec(select(WorkOpenItem).where(WorkOpenItem.entry_id == entry_id)))
    matched: set[str] = set()
    hits: list["WorkOpenItem | None"] = [None] * len(opens)
    # The same words first, whatever the item's state (one the owner closed
    # stays closed), so a new similar line listed before an old one never
    # takes the old one's place.
    for n, (_text, key) in enumerate(opens):
        hit = next((it for it in mine if it.id not in matched and it.text_key == key), None)
        if hit is not None:
            hits[n] = hit
            matched.add(hit.id)
    # Then a line reworded only in plain words (:func:`same_line`), and only
    # for an item still open or settled by this conversation: never one the
    # owner closed, which a new line would otherwise vanish into.
    for n, (text, _key) in enumerate(opens):
        if hits[n] is not None:
            continue
        hit = next((it for it in mine if it.id not in matched
                    and it.status in (OPEN_ITEM_OPEN, OPEN_ITEM_SETTLED) and same_line(it.text, text)), None)
        if hit is not None:
            hits[n] = hit
            matched.add(hit.id)
    for n, (text, key) in enumerate(opens):
        hit = hits[n]
        if hit is None:
            # The same words as an item the owner closed lately in another
            # conversation here, while this one was already going: this copy
            # is closed with it, so the owner's answer stands (review 2 of
            # 04-b); never in the owner's name (review 3).
            shut = _owner_closed_twin(db, fkey, key, entry_id, when, started)
            # A microsecond apart, so a recap's lines keep their order.
            item = WorkOpenItem(
                id=new_id("open"), entry_id=entry_id, folder_key=fkey, folder=storable_text(folder),
                text=text, text_key=key, status=OPEN_ITEM_OPEN, opened_at=when + timedelta(microseconds=n),
                local_only=local, updated_at=when,
            )
            if shut is not None:
                item.status, item.closed_at = shut.status, shut.closed_at
                item.closed_how, item.closed_ref = CLOSED_WITH_COPY, shut.id
            db.add(item)
            mine.append(item)
            matched.add(item.id)
            stats["added"] += 1
            continue
        matched.add(hit.id)
        if hit.status == OPEN_ITEM_OPEN:
            if hit.text != text:
                hit.text, hit.text_key = text, key  # the latest checked wording
        elif hit.status == OPEN_ITEM_SETTLED:
            # Its own conversation lists it again: open again.
            hit.status, hit.closed_at, hit.closed_how, hit.closed_ref = OPEN_ITEM_OPEN, None, "", ""
            hit.text, hit.text_key = text, key
            stats["reopened"] += 1
        hit.updated_at = when
        db.add(hit)
    for it in mine:
        changed = False
        if it.folder_key != fkey:
            it.folder_key, it.folder, changed = fkey, storable_text(folder), True
        if it.local_only != local:
            it.local_only, changed = local, True
        if it.id not in matched and it.status == OPEN_ITEM_OPEN and not it.owner_kept:
            it.status, it.closed_at = OPEN_ITEM_SETTLED, when
            it.closed_how, it.closed_ref = CLOSED_BY_CONVERSATION, entry_id
            stats["settled"] += 1
            changed = True
        if changed:
            it.updated_at = when
            db.add(it)
    if dones and fkey:
        others = list(db.exec(
            select(WorkOpenItem)
            .where(WorkOpenItem.folder_key == fkey)
            .where(WorkOpenItem.status == OPEN_ITEM_OPEN)
            .where(WorkOpenItem.entry_id != entry_id)
            .limit(OPEN_READ)
        ))
        # A Not yet answers the LINE: a copy of it written after the answer,
        # or one whose copy that holds the answer was settled since, never
        # asks about that piece again (review 12 of 04-b).
        answered = _declined_by_words(db, fkey, {it.text_key for it in others})
        for it in others:
            if it.maybe_done_entry or entry_id in declined(it) or entry_id in answered.get(it.text_key, ()):
                continue
            opened = _naive(it.opened_at)
            if opened is not None and opened > when:
                continue  # opened after this Done line: not "later work"
            if any(may_be_done(it.text, d) for d in dones):
                it.maybe_done_entry = entry_id
                it.updated_at = when
                db.add(it)
                stats["asked"] += 1
    return stats


def closed_keys(db: Any, entry_ids: Iterable[str]) -> dict[str, set[str]]:
    """``{entry_id: {text_key, …}}``: the items of these pieces of work that
    are CLOSED (Done, No longer needed, settled), however long ago, and not
    also open under the same words (inside the caller's session). A recap
    told under its piece of work leaves these Open lines out
    (:func:`without_closed`): a line the owner closed is never told as open
    again (review of 04-b)."""
    ids = sorted({str(i) for i in entry_ids if i})
    shut: dict[str, set[str]] = {}
    still: dict[str, set[str]] = {}
    for i in range(0, len(ids), 400):
        stmt = select(WorkOpenItem.entry_id, WorkOpenItem.text_key, WorkOpenItem.status).where(
            WorkOpenItem.entry_id.in_(ids[i : i + 400]))  # type: ignore[attr-defined]
        for entry_id, key, status in db.exec(stmt):
            (still if status == OPEN_ITEM_OPEN else shut).setdefault(str(entry_id), set()).add(str(key or ""))
    out = {e: ks - still.get(e, set()) for e, ks in shut.items()}
    return {e: ks for e, ks in out.items() if ks}


def without_closed(lines: Iterable[Any], shut: "set[str] | frozenset[str] | None") -> list[str]:
    """A recap's own lines minus the Open lines whose item is closed
    (``shut``: :func:`closed_keys` of that piece of work); ``shut`` None
    (the closed items could not be read) leaves out EVERY Open line. The
    words are compared the way :func:`sync` carried them (:func:`text_key`
    of the line as kept). Pure."""
    out: list[str] = []
    for raw in lines or ():
        got = labelled([raw])
        if got and got[0][0] == "open" and (shut is None or text_key(got[0][1]) in shut):
            continue
        out.append(str(raw))
    return out


def drop_entries(db: Any, entry_ids: Iterable[str]) -> int:
    """Remove every item of these pieces of work (inside the caller's
    session; the caller commits): work that left the record, or a recap a
    reply marked low trust after it was kept. A "may be done" that pointed
    at one of them is cleared too."""
    ids = [str(i) for i in entry_ids if i]
    n = 0
    for i in range(0, len(ids), 400):
        chunk = ids[i : i + 400]
        got = db.exec(sa_delete(WorkOpenItem).where(WorkOpenItem.entry_id.in_(chunk)))  # type: ignore[attr-defined]
        n += int(getattr(got, "rowcount", 0) or 0)
        for it in db.exec(select(WorkOpenItem).where(WorkOpenItem.maybe_done_entry.in_(chunk))):  # type: ignore[attr-defined]
            it.maybe_done_entry = ""
            db.add(it)
    return n


#: ONE carrier of recap lines into the open items at a time in this process
#: (the daemon is one process per home): every reader runs :func:`backfill`,
#: so two at once would both pick the same recaps not yet carried and each
#: add their items (review of 04-b); and a recap being kept
#: (``recap._store``) never interleaves with a backfill of the same recap.
#: Held around the whole read-then-write, so the second one reads what the
#: first one wrote.
WRITING = threading.RLock()


def backfill(engine: Any) -> int:
    """Carry forward, once, the kept recaps written before open items
    existed (or written straight into the table): every kept recap not yet
    synced, oldest first (so a later Done line asks about an earlier item),
    each item dated by its recap. Bounded per call; idempotent (a synced
    recap is marked; :data:`WRITING` makes it so for two readers at once).
    RAISES when the store cannot be written."""
    if engine is None:
        return 0
    with WRITING:
        return _backfill(engine)


def _backfill(engine: Any) -> int:
    R = WorkRecap
    with session_scope(engine) as db:
        todo = list(db.exec(
            select(R)
            .where(R.state == RECAP_KEPT)
            .where(or_(R.open_synced == False, R.open_synced.is_(None)))  # noqa: E712  # type: ignore[union-attr]
            # Kept in one instant (a coarse clock): in the order of the work
            # they recap (its last change, as the recap stamps it), never by
            # their random ids (review 4 of 04-b).
            .order_by(R.created_at, R.source_updated, R.id)  # type: ignore[attr-defined]
            .limit(BACKFILL_BATCH)
        ))
        if not todo:
            return 0
        last: "datetime | None" = None
        for r in todo:
            when = _naive(r.created_at) or _now()
            if last is not None and when <= last:
                # Two recaps kept in the same instant (a coarse clock): still
                # one after the other, in the order they were kept.
                when = last + timedelta(microseconds=50)
            last = when
            sync(db, str(r.id), [ln for ln in (r.text or "").split("\n") if ln.strip()], now=when)
            r.open_synced = True
            db.add(r)
        db.commit()
    log.info("open items: %d recap(s) carried forward", len(todo))
    return len(todo)


# --------------------------------------------------------------------------- #
# the owner's answers
# --------------------------------------------------------------------------- #
def _view(it: WorkOpenItem) -> dict[str, Any]:
    return {
        "id": it.id, "entry_id": it.entry_id, "text": it.text, "status": it.status,
        "how": it.closed_how or "", "closed_at": _iso(it.closed_at), "opened_at": _iso(it.opened_at),
        "folder_key": it.folder_key,
    }


def get(engine: Any, item_id: str) -> "dict[str, Any] | None":
    with session_scope(engine) as db:
        it = db.get(WorkOpenItem, str(item_id or ""))
        return _view(it) if it is not None else None


def _state(it: WorkOpenItem) -> dict[str, Any]:
    return {
        "status": it.status, "closed_at": _iso(it.closed_at), "closed_how": it.closed_how,
        "closed_ref": it.closed_ref, "maybe_done_entry": it.maybe_done_entry,
        "maybe_declined": it.maybe_declined, "owner_kept": bool(it.owner_kept),
    }


def _put_back(it: WorkOpenItem, before: dict[str, Any], now: datetime) -> None:
    status = str(before.get("status") or OPEN_ITEM_OPEN)
    it.status = status if status in (OPEN_ITEM_OPEN, *CLOSED_STATUSES) else OPEN_ITEM_OPEN
    closed = before.get("closed_at")
    try:
        it.closed_at = _naive(datetime.fromisoformat(str(closed))) if closed else None
    except ValueError:
        it.closed_at = None
    it.closed_how = str(before.get("closed_how") or "")
    it.closed_ref = str(before.get("closed_ref") or "")
    it.maybe_done_entry = str(before.get("maybe_done_entry") or "")
    it.maybe_declined = str(before.get("maybe_declined") or "")
    it.owner_kept = bool(before.get("owner_kept"))
    it.updated_at = now


def snapshot(engine: Any, item_id: str) -> "dict[str, Any] | None":
    """What an answer may change, as it is now (a tool's Undo): the item and
    its other conversations' copies (``twins``: an answer changes them too)."""
    with session_scope(engine) as db:
        it = db.get(WorkOpenItem, str(item_id or ""))
        if it is None:
            return None
        return {**_state(it), "twins": {t.id: _state(t) for t in _twins(db, it)}}


def restore(engine: Any, item_id: str, before: "dict[str, Any] | None") -> bool:
    """Put an item (and the copies :func:`snapshot` saw) back as they were.
    False when the item is gone."""
    if not isinstance(before, dict):
        return False
    with session_scope(engine) as db:
        it = db.get(WorkOpenItem, str(item_id or ""))
        if it is None:
            return False
        now = _now()
        _put_back(it, before, now)
        db.add(it)
        twins = before.get("twins")
        for tid, state in (twins.items() if isinstance(twins, dict) else ()):
            row = db.get(WorkOpenItem, str(tid)) if isinstance(state, dict) else None
            if row is not None and row.id != it.id:
                _put_back(row, state, now)
                db.add(row)
        db.commit()
    return True


def close(engine: Any, item_id: str, *, as_: str = "done", by: str = CLOSED_BY_YOU,
          ref: str = "") -> "dict[str, Any] | None":
    """Close one item: Done (``as_`` "done") or No longer needed ("dropped"),
    by the owner (``by`` "you") or a chat the owner asked ("chat", ``ref`` =
    the chat), with every copy of it still open in another conversation of
    the folder (:func:`_twins`: the owner answers the line once). Closing a
    closed item again changes nothing. None when there is no such item;
    ``ValueError`` for another ``as_``."""
    status = CLOSE_AS.get(str(as_ or ""))
    if status is None:
        raise ValueError("close as done or dropped")
    with session_scope(engine) as db:
        it = db.get(WorkOpenItem, str(item_id or ""))
        if it is None:
            return None
        now = _now()
        changed = False
        for row in [it, *(t for t in _twins(db, it) if t.status == OPEN_ITEM_OPEN)]:
            if row.status == status:
                continue
            row.status, row.closed_at = status, now
            row.closed_how = by if by in (CLOSED_BY_YOU, CLOSED_BY_CHAT) else CLOSED_BY_YOU
            row.closed_ref = storable_text(str(ref or ""))[:200]
            row.maybe_done_entry = ""
            row.updated_at = now
            db.add(row)
            changed = True
        if changed:
            db.commit()
            db.refresh(it)
        return _view(it)


#: How an item was closed by an answer of the owner's: by the owner (card or
#: chat), or written closed after that answer (a copy).
_ANSWERED = (CLOSED_BY_YOU, CLOSED_BY_CHAT, CLOSED_WITH_COPY)


def _same_answer(it: WorkOpenItem, twins: list[WorkOpenItem]) -> list[WorkOpenItem]:
    """The copies (of ``twins``) that ONE answer of the owner's closed along
    with ``it`` (review 4 of 04-b): :func:`close` stamps every copy it
    closes with one ``closed_at``, and a copy written closed after it
    (:data:`CLOSED_WITH_COPY`) takes that same ``closed_at`` and points at
    one of them (``closed_ref``). A copy closed by another answer, earlier or
    later, and one settled by its own conversation are not this answer's.
    Nothing when ``it`` was settled by its own conversation. Pure."""
    at = _naive(it.closed_at)
    if it.closed_how not in _ANSWERED or at is None:
        return []

    def same(t: WorkOpenItem) -> bool:
        return t.status == it.status and _naive(t.closed_at) == at

    owners = [t for t in twins if same(t) and t.closed_how in (CLOSED_BY_YOU, CLOSED_BY_CHAT)]
    ids = {it.id, *(t.id for t in owners)}
    if it.closed_how == CLOSED_WITH_COPY and it.closed_ref:
        ids.add(str(it.closed_ref))
    copies = [t for t in twins if same(t) and t.closed_how == CLOSED_WITH_COPY and t.closed_ref in ids]
    return [*owners, *copies]


def declined(it: WorkOpenItem) -> list[str]:
    """The pieces of work (entry ids, oldest first) the owner answered "Not
    yet" for about this item: :func:`sync` never asks about them again
    (review 11 of 04-b). A row written when only one was kept reads as that
    one. Pure."""
    return [e for e in str(it.maybe_declined or "").split("\n") if e]


def _declined_by_words(db: Any, fkey: str, words: Iterable[str]) -> dict[str, set[str]]:
    """``{text_key: {entry_id, …}}``: the pieces of work the owner answered
    "Not yet" for about a line of folder ``fkey`` with these words, over
    EVERY copy of it (other conversations', open or closed): the owner sees
    the copies as one item, so the answer holds for all of them, a copy
    written later included (review 12 of 04-b). The newest
    :data:`OPEN_READ` copies with an answer are read. Inside the caller's
    session."""
    want = sorted({w for w in words if w})
    out: dict[str, set[str]] = {}
    if not fkey or not want:
        return out
    for key, kept in db.exec(
        select(WorkOpenItem.text_key, WorkOpenItem.maybe_declined)
        .where(WorkOpenItem.folder_key == fkey)
        .where(WorkOpenItem.text_key.in_(want))  # type: ignore[attr-defined]
        .where(WorkOpenItem.maybe_declined != "")
        .order_by(WorkOpenItem.updated_at.desc())  # type: ignore[union-attr]
        .limit(OPEN_READ)
    ):
        out.setdefault(str(key or ""), set()).update(e for e in str(kept or "").split("\n") if e)
    return out


def _decline(it: WorkOpenItem, entry_id: str) -> None:
    """Remember one more "Not yet" (newest last, :data:`DECLINED_KEEP` at
    most), never in place of the earlier ones."""
    keep = [e for e in declined(it) if e != entry_id]
    it.maybe_declined = "\n".join([*keep, str(entry_id)][-DECLINED_KEEP:])


def reopen(engine: Any, item_id: str) -> "dict[str, Any] | None":
    """Put a closed item back on the open list (Reopen), for good, with the
    copies in other conversations of the folder that the SAME answer closed
    (:func:`_same_answer`): never a copy the owner closed in another answer
    or one its own conversation settled (review 4 of 04-b). A rewritten
    recap no longer settles what was reopened (``owner_kept``). On an item
    that is still open it answers the "Mark it done?" question with Not yet,
    for its open copies too: the question goes, and that piece of work never
    asks it again. None when there is no such item."""
    with session_scope(engine) as db:
        it = db.get(WorkOpenItem, str(item_id or ""))
        if it is None:
            return None
        now = _now()
        twins = _twins(db, it)
        if it.status == OPEN_ITEM_OPEN:
            for row in [it, *(t for t in twins if t.status == OPEN_ITEM_OPEN)]:
                if row.maybe_done_entry:
                    _decline(row, row.maybe_done_entry)
                    row.maybe_done_entry = ""
                row.updated_at = now
                db.add(row)
        else:
            for row in [it, *_same_answer(it, twins)]:
                row.status, row.closed_at, row.closed_how, row.closed_ref = OPEN_ITEM_OPEN, None, "", ""
                row.maybe_done_entry = ""
                row.owner_kept = True
                row.updated_at = now
                db.add(row)
        db.commit()
        db.refresh(it)
        return _view(it)


# --------------------------------------------------------------------------- #
# reading: what a turn (and the card) may be told
# --------------------------------------------------------------------------- #
@dataclass
class Told:
    """The items one turn in a folder may be told (:func:`told`): ``open``
    oldest first, ``lately`` (closed in the last :data:`CLOSED_LATELY_DAYS`)
    newest first, and ``rows`` the pieces of work they belong to, as they
    are now (by entry id)."""

    open: list[dict[str, Any]] = field(default_factory=list)
    lately: list[dict[str, Any]] = field(default_factory=list)
    rows: dict[str, dict[str, Any]] = field(default_factory=dict)


def told(reader: Any, folder: str, *, exclude_refs: Iterable[str] = (), now: "datetime | None" = None,
         lately: bool = False) -> Told:
    """The open items (and, with ``lately``, the ones closed lately) of
    EXACTLY ``folder`` that the turn ``reader`` reads for may be told (see
    the module docstring). RAISES :class:`Unreadable` when the items, their
    work or its sources cannot be read (the card answers 503; the block
    leaves the part out)."""
    from . import block as _block
    from .index import _row, link_for

    out = Told()
    if reader is None or getattr(reader, "phone", False):
        return out
    own = keys.folder_key(keys.home_folder(str(folder or "").strip()))
    if not own:
        return out
    when = _naive(now) or _now()
    skip = {str(r) for r in exclude_refs or () if str(r or "").strip()}
    engine = reader.engine
    try:
        backfill(engine)
    except Exception:  # noqa: BLE001 — the items already carried are still read
        log.debug("open items: the backfill could not run", exc_info=True)
    try:
        with session_scope(engine) as db:
            items = list(db.exec(
                select(WorkOpenItem)
                .where(WorkOpenItem.folder_key == own)
                .where(WorkOpenItem.status == OPEN_ITEM_OPEN)
                .order_by(WorkOpenItem.opened_at, WorkOpenItem.id)  # type: ignore[arg-type]
                .limit(OPEN_READ)
            ))
            closed: list[WorkOpenItem] = []
            if lately:
                closed = list(db.exec(
                    select(WorkOpenItem)
                    .where(WorkOpenItem.folder_key == own)
                    .where(WorkOpenItem.status.in_(CLOSED_STATUSES))  # type: ignore[attr-defined]
                    .where(WorkOpenItem.closed_at >= when - timedelta(days=CLOSED_LATELY_DAYS))  # type: ignore[operator]
                    .order_by(WorkOpenItem.closed_at.desc(), WorkOpenItem.id)  # type: ignore[union-attr]
                    .limit(LATELY_MAX)
                ))
            wanted = {it.entry_id for it in [*items, *closed]} | {
                it.maybe_done_entry for it in items if it.maybe_done_entry}
            entries = {}
            ids = sorted(i for i in wanted if i)
            for i in range(0, len(ids), 400):
                for e in db.exec(select(WorkEntry).where(WorkEntry.id.in_(ids[i : i + 400]))):  # type: ignore[attr-defined]
                    entries[e.id] = _row(e)
            for it in [*items, *closed]:
                db.expunge(it)
    except Exception as exc:  # noqa: BLE001
        raise Unreadable("the open items could not be read") from exc
    if not items and not closed:
        return out
    try:
        live = reader.live(list(entries.values()))
    except Exception as exc:  # noqa: BLE001
        raise Unreadable("the work the open items belong to could not be asked") from exc

    def shown_row(entry_id: str) -> "dict[str, Any] | None":
        row = live.get(entry_id)
        if row is None or str(row.get("ref") or "") in skip:
            return None
        if keys.folder_key(keys.home_folder(row.get("folder") or "")) != own:
            return None  # the work moved to another folder since
        if not reader.allowed(row) or not reader.told(row):
            return None
        return row

    def view(it: WorkOpenItem, row: dict[str, Any]) -> "dict[str, Any] | None":
        text = _block._safe(_block._clip(it.text or "", TEXT_CHARS)).strip()
        title = _block._safe(_block._clip(row.get("title") or "", _block.TITLE_CHARS)).strip()
        if not text or not title:
            # A line that reads as an instruction is never told, nor an item
            # of a piece whose title does (the block drops that piece too).
            return None
        return {
            "id": it.id, "text": text, "entry_id": it.entry_id, "title": title,
            "kind": row.get("kind") or "", "link": link_for(row["kind"], row["ref"]),
            "at": _iso(it.opened_at), "updated": row.get("updated"),
        }

    def maybe_of(it: WorkOpenItem) -> "dict[str, Any] | None":
        if not it.maybe_done_entry:
            return None
        later = shown_row(it.maybe_done_entry)
        if later is None:
            return None
        later_title = _block._safe(_block._clip(later.get("title") or "", _block.TITLE_CHARS)).strip()
        if not later_title:
            return None
        return {"entry_id": it.maybe_done_entry, "title": later_title, "link": link_for(later["kind"], later["ref"])}

    # The same line in two conversations here (exactly its words,
    # :func:`text_key`) is ONE item: told once, the oldest copy this turn may
    # be told, asking "may be done" when any copy does (review 2 of 04-b).
    shown: dict[str, dict[str, Any]] = {}
    for it in items:
        key = it.text_key or text_key(it.text)
        row = shown_row(it.entry_id)
        if row is None:
            continue
        first = shown.get(key)
        if first is not None:
            if first["maybe_done"] is None:
                first["maybe_done"] = maybe_of(it)
            continue
        got = view(it, row)
        if got is None:
            continue
        got["maybe_done"] = maybe_of(it)
        shown[key] = got
        out.open.append(got)
        out.rows[it.entry_id] = row
    # Closed lately: one line per line closed, newest first, and never a line
    # that is open above.
    shut: set[str] = set()
    for it in closed:
        key = it.text_key or text_key(it.text)
        if key in shown or key in shut:
            continue
        row = shown_row(it.entry_id)
        if row is None:
            continue
        got = view(it, row)
        if got is None:
            continue
        got.update({"status": it.status, "how": it.closed_how or "", "closed_at": _iso(it.closed_at)})
        shut.add(key)
        out.lately.append(got)
        out.rows.setdefault(it.entry_id, row)
    return out


def block_part(items: list[dict[str, Any]], now: datetime, *, chosen: str = "",
               room: int = ITEMS_CHARS) -> tuple[list[str], int, list[str]]:
    """The work block's "Still open in this folder" part for ``items``
    (:func:`told`'s ``open``, oldest first): ``(lines, chars used, ids
    told)``. At most :data:`TOLD_MAX` numbered lines inside ``room``, and
    the item the owner picked (``chosen``, the chat body's
    ``pick_up_item``) on top of them wherever it is, said to be the one the
    owner chose. The rest is counted. Every line through ``block._safe``."""
    from ..projects.recent_activity import age_words
    from . import block as _block

    if not items:
        return [], 0, []
    pick = next((it for it in items if chosen and it["id"] == chosen), None)
    first = items[:TOLD_MAX]
    order = first + ([pick] if pick is not None and pick not in first else [])
    lead = _block._safe(ITEMS_LEAD)
    if not lead:
        return [], 0, []

    def body(it: dict[str, Any]) -> str:
        kind = _block.KIND_WORDS.get(it.get("kind") or "", "work")
        title = _block._clip(it.get("title") or "", 60)
        line = f"{it['text']} [{it['id']}] (from {kind} \"{title}\", {age_words(it.get('updated'), now)})"
        if it.get("maybe_done"):
            line += " (later work here may have done this; ask the owner before marking it done)"
        if it is pick:
            line += " (the owner picked this one to continue with now)"
        return line

    # The picked item's room is kept first, so it is always told; and the
    # room of the "(and N more …)" line.
    said_pick = "The owner picked open item 0 to continue with now."
    reserve = 50 + ((len(body(pick)) + 4 + len(said_pick) + 1) if pick is not None else 0)
    used = len(lead) + 1
    kept: list[dict[str, Any]] = []
    for it in order:
        if it is pick:
            kept.append(it)
            continue
        size = len(body(it)) + 4
        if used + size + reserve > room:
            continue
        kept.append(it)
        used += size
    lines = [lead]
    used = len(lead) + 1
    told_ids: list[str] = []
    picked_n = 0
    for it in kept:
        line = _block._safe(f"{len(told_ids) + 1}. {body(it)}")
        if not line:
            continue  # flagged: left out, and counted below
        lines.append(line)
        used += len(line) + 1
        told_ids.append(it["id"])
        if it is pick:
            picked_n = len(told_ids)
    if not told_ids:
        return [], 0, []
    more = len(items) - len(told_ids)
    if more > 0:
        tail = f"(and {more} more still open in this folder)"
        lines.append(tail)
        used += len(tail) + 1
    if picked_n:
        said = f"The owner picked open item {picked_n} to continue with now."
        lines.append(said)
        used += len(said) + 1
    return lines, used, told_ids


__all__ = [
    "BACKFILL_BATCH", "CLOSED_LATELY_DAYS", "CLOSE_AS", "ITEMS_CHARS", "ITEMS_LEAD", "TOLD_MAX", "Told",
    "Unreadable", "WRITING", "backfill", "block_part", "close", "closed_keys", "drop_entries", "get",
    "labelled", "may_be_done", "reopen", "restore", "same_item", "same_line", "snapshot", "sync",
    "text_key", "told", "without_closed",
]
