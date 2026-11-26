"""THE ONE READER of the work record (v1.547.0; next leaps 13-a, "Ask the
record", ``docs/next-leaps/04-a-memory-that-compounds.md``).

Every reader that tells a model about earlier work goes through here, so
there is still ONE wall:

* :class:`Reader` is one turn's read: the record brought up to date
  (``WorkMap.refresh``, bounded: a turn never waits for a first build), the
  turn's Local only rule, its CLIENT WALL (``clients.wall``), and the
  SOURCES ASKED AGAIN (``collect.live_state``) before anything is told.
  ``block.work_block`` (the block pushed into a chat's prompt, and
  ``work_search`` for another app through it) reads through it; its bytes
  are what they were.
* :func:`lookup` is the read behind the ``work_history`` tool
  (``workmap/tools.py``): the work in a folder, or the work some words point
  at, for the turn in flight.

The rules, each pinned (``tests/test_next_leaps_13a_work_history.py``):

* The wall is the TURN's (``wall.current()``: the lanes and the agent
  runtime set it from the OWNER's words). The tool's own ``query`` is the
  model's words: it only chooses among the rows the wall already lets
  through, and never names a client for it.
* A row is told only after its source was asked again: deleted, archived or
  switched to Local only a moment ago is never told from a stale row; when
  the sources cannot be asked, nothing is told (``Found.unreadable``).
* Local only and kept-home work only for a turn that stays on this network
  (``egress.may_recall_local_only_chats``); never to another app, never to
  the phone.
* A row the wall keeps back is COUNTED (``Found.held_back``), never named.
* RECAP LINES (``Reader.recap_ok``): today's rule, work in exactly the
  turn's folder; and decision D-13.1: in a turn that belongs to NO client,
  the checked recap lines of a confirmed client the OWNER's own message
  names, for a row that is that one client's alone. Never in another
  client's chat (the wall refuses the row), never to another app, never to
  the phone.
* THE PHONE (decision D-13.2, ``phone=True``): a row that has an owner of
  any kind (a waiting name too) is told only when every owner is a confirmed
  client the owner's own message named; no recap; Local only never.
* Matching for a tool call is LOOSER than the block's (``WorkMap.matching``
  stays strict for what is pushed unasked): any query word in the title, a
  file name, the folder's name or a recap line this turn may be told, over
  the newest ``index.LIGHT_MAX`` rows; more words matched first, then newer.

BY DATE, BY KIND, BY CLIENT (next leaps 13-b, master plan R2.10):

* ``after`` / ``before`` are the bounds the TOOL read from the ISO dates the
  model supplies (:func:`bound`: the OWNER's own days, this PC's zone; no
  date parser for words here, the ``search/tools.py`` rule). A piece of work
  counts by the day it was last worked on (``updated_at``), read through
  ``WorkMap.between`` (one indexed read), never only the newest rows.
* ``kinds``: only these kinds of work (``KIND_ARGS`` names them).
* ``client`` (a name the MODEL wrote) is honoured only when it is a
  confirmed client that is the turn's own (``turn.client``) or that the
  OWNER's own words named (``turn.named``); otherwise nothing is read and
  ``Found.client_refused`` is set, whatever the name is (a client that
  exists and one that does not are answered alike). An honoured client
  only CHOOSES rows: the ones the wall attributes to it (``owners_of``);
  the wall still decides each one.
* A filter with no folder looks everywhere the wall allows (not only the
  turn's own folder).
* A lookup whose answer SAYS HOW MANY (a filter or a client) asks again
  every row its reads brought back (``COUNTED_MAX``), not only the best
  ``CANDIDATES_MAX``: a busy month's nightly runs never push an older piece
  out of the count. A read that stopped at ``READ_MAX``, or rows past what
  is asked again, set ``Found.partial``: the tool then says "at least".
* Each piece shown carries ``open`` (how many Open lines its kept, current
  recap has) and ``decisions`` (where the owner reads that recap: the
  client's page, else the Memory page) whatever the recap rule says; the
  LINES stay under ``Reader.recap_ok``. Never for another app or the phone.

BLOCKING (SQLite): the callers run it off the event loop. :func:`lookup`
never raises.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import quote

from ..core.db import session_scope
from . import collect, keys
from . import index as _index
from .index import LIGHT_MAX, work_map
from .models import KIND_CHAT, KIND_CODE, KIND_OUTSIDE, KIND_RUN, KIND_TOOL_CODE

log = logging.getLogger(__name__)

#: The kinds a caller may ask for, by the words the tool's schema uses.
KIND_ARGS: dict[str, frozenset[str]] = {
    "chat": frozenset({KIND_CHAT}),
    "coding_session": frozenset({KIND_CODE, KIND_TOOL_CODE}),
    "agent_run": frozenset({KIND_RUN}),
    "other_tool": frozenset({KIND_OUTSIDE}),
}
#: Folders of one client read for a ``client`` lookup, at most.
CLIENT_FOLDERS_READ = 8
#: A kept recap line that is still open ("Open: …"; the recap's own labels).
_OPEN_RX = re.compile(r"^\s*open\s*:\s*\S", re.IGNORECASE)
_DATE_RX = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: Where the owner reads a piece's decisions (the dashboard's own pages).
MEMORY_WORK_HREF = "/memory?work="
CLIENT_HREF = "/clients/"


def kinds_of(value: Any) -> "frozenset[str] | None":
    """The record kinds a ``kinds`` argument names (a list, or words split
    by commas; spaces and hyphens read as ``_``). None = every kind. RAISES
    ``ValueError`` for a word it does not know: a filter the caller asked
    for is honoured, never quietly dropped."""
    if value is None or value == "" or value == []:
        return None
    parts = value if isinstance(value, (list, tuple, set, frozenset)) else str(value).split(",")
    out: set[str] = set()
    for p in parts:
        word = re.sub(r"[\s-]+", "_", str(p or "").strip().lower())
        if not word:
            continue
        if word.endswith("s") and word[:-1] in KIND_ARGS:
            word = word[:-1]
        if word not in KIND_ARGS:
            raise ValueError(word)
        out |= KIND_ARGS[word]
    return frozenset(out) or None


def _zone() -> Any:
    """The owner's zone (``block._local_zone``, the one test seam)."""
    from . import block as _block

    return _block._local_zone()


def _to_utc(local_wall: datetime) -> datetime:
    """A wall-clock time on THIS PC (naive) as naive UTC."""
    zone = _zone()
    aware = local_wall.replace(tzinfo=zone) if zone is not None else local_wall.astimezone()
    return aware.astimezone(timezone.utc).replace(tzinfo=None)


def bound(value: Any, *, end: bool = False) -> "tuple[datetime | None, date | None]":
    """``(the bound as naive UTC, the owner's day it falls on)`` for one ISO
    date or date-time the caller supplied; ``(None, None)`` for nothing. A
    plain date is the OWNER's day (this PC's zone): its first moment, or
    with ``end`` its last. A date-time without a zone is the owner's wall
    clock too; one with a zone is that moment. RAISES ``ValueError`` for
    anything else (words are the model's to turn into dates)."""
    raw = str(value or "").strip()
    if not raw:
        return None, None
    try:
        if _DATE_RX.match(raw):
            day = date.fromisoformat(raw)
            start = datetime(day.year, day.month, day.day)
            if end:
                return _to_utc(start + timedelta(days=1)) - timedelta(microseconds=1), day
            return _to_utc(start), day
        when = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw)
        if when.tzinfo is None:
            utc = _to_utc(when)
            return utc, when.date()
        utc = when.astimezone(timezone.utc).replace(tzinfo=None)
        zone = _zone()
        local = when.astimezone(zone) if zone is not None else when.astimezone()
        return utc, local.date()
    except (TypeError, ValueError, OverflowError, OSError) as exc:
        raise ValueError(raw) from exc

#: Rows a turn's own refresh may rebuild (a long first build is finished in
#: the background; a turn never waits for it).
TURN_REBUILD = 60
#: Pieces of work one lookup lists by default / at most.
LIMIT = 12
LIMIT_MAX = 20
#: Rows asked again and walled for one lookup, at most (the best first).
CANDIDATES_MAX = 200
#: 13-b review: rows one read of the record brings back for a lookup, at
#: most (newest first), and rows asked again and walled for a lookup that
#: SAYS HOW MANY it found (a date, kind or client: the count line), at most:
#: every row its reads brought back. Past either cap the lookup is
#: ``Found.partial`` and no number it says is exact ("at least").
READ_MAX = LIGHT_MAX
COUNTED_MAX = LIGHT_MAX
#: Rows whose kept recap a query is matched against, at most (newest first).
RECAP_SCAN_MAX = 400
_CLIENT = "client:"


def withhold_local_only() -> bool:
    """Is Local only (and kept-home) work kept from the turn in flight? True
    unless the turn itself stays on this network (fabric recall's rule);
    unsure keeps it home (fail closed)."""
    try:
        from ..providers.egress import may_recall_local_only_chats

        return not may_recall_local_only_chats()
    except Exception:  # noqa: BLE001 — unsure: keep them home (fail closed)
        return True


def as_it_is_now(engine: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``rows`` whose SOURCE still exists, each with the source's own Local
    only and archived state. RAISES when the sources cannot be asked (the
    caller then shows nothing)."""
    if not rows:
        return []
    with session_scope(engine) as db:
        live = collect.live_state(db, [(r["kind"], r["ref"]) for r in rows])
    out = []
    for r in rows:
        now = live.get((r["kind"], r["ref"]))
        if now is None:
            continue  # deleted since the record was built
        out.append({**r, "local_only": r["local_only"] or now["local_only"],
                    "archived": r["archived"] or now["archived"],
                    "live_updated": now.get("updated", "")})
    return out


def collapse(rows: list[dict[str, Any]]) -> list[tuple[dict[str, Any], int]]:
    """Repeats of one piece of work (a schedule's nightly run) as ONE row,
    the newest, with how many there were."""
    seen: dict[tuple[str, str], int] = {}
    out: list[tuple[dict[str, Any], int]] = []
    for r in rows:  # newest first
        key = (r["kind"], str(r.get("title") or "").lower())
        if key in seen:
            row, n = out[seen[key]]
            out[seen[key]] = (row, n + 1)
            continue
        seen[key] = len(out)
        out.append((r, 1))
    return out


def _load_map(engine: Any, turn: Any) -> tuple[Any, Any]:
    """The client map for a wall the caller already has; a map that cannot
    be read FAILS CLOSED (``ok=False``, this exact folder only)."""
    from .clients import TurnWall, store
    from .clients.store import ClientMap

    try:
        cmap = store.load_map(engine)
        if not isinstance(cmap, ClientMap):
            raise TypeError("not a client map")
        return turn, cmap
    except Exception:  # noqa: BLE001 — ClientsUnreadable or anything: fail closed
        log.debug("work lookup: the client map could not be read; the turn fails closed", exc_info=True)
        key = str(getattr(turn, "folder_key", "") or "")
        return TurnWall(ok=False, folder_key=key, outside=bool(getattr(turn, "outside", False))), store.EMPTY_MAP


class Reader:
    """One turn's read of the work record (see the module docstring). Made
    by :meth:`open`; its three questions are what every reader asks."""

    def __init__(self, *, wm: Any, engine: Any, turn: Any, cmap: Any, withhold: bool,
                 outside: bool = False, phone: bool = False) -> None:
        self.wm = wm
        self.engine = engine
        self.turn = turn
        self.cmap = cmap
        self.withhold = withhold
        self.outside = outside
        self.phone = phone

    @classmethod
    def open(cls, platform: Any, *, turn: Any = None, cmap: Any = None, folder: str = "",
             query: str = "", outside: bool = False, withhold: "bool | None" = None,
             phone: bool = False) -> "Reader | None":
        """The read for one turn, or None when there is no record. The record
        is refreshed first (bounded). ``turn`` None builds the wall from
        ``folder`` and ``query`` (the OWNER's words: the block's way);
        ``outside`` is another app's wall (``wall.outside_and_map``) whatever
        ``turn`` says. ``withhold`` None asks the turn's Local only scope.
        BLOCKING; RAISES only on what the caller already catches."""
        from .clients import wall

        engine = getattr(platform, "engine", None)
        home = getattr(getattr(platform, "config", None), "home", None)
        wm = work_map(engine, home)
        if wm is None:
            return None
        wm.refresh(budget=TURN_REBUILD)
        # Another app's model and the phone's messaging service are never on
        # this network: Local only and kept-home work is always withheld.
        if outside or phone:
            withhold = True
        elif withhold is None:
            withhold = withhold_local_only()
        if outside:
            turn, cmap = wall.outside_and_map(engine)
        elif turn is None:
            turn, cmap = wall.turn_and_map(engine, folder, query)
        elif cmap is None:
            turn, cmap = _load_map(engine, turn)
        return cls(wm=wm, engine=engine, turn=turn, cmap=cmap, withhold=bool(withhold),
                   outside=outside, phone=phone)

    # -- the three questions ------------------------------------------------
    def allowed(self, r: dict[str, Any]) -> bool:
        """Not archived, and not Local only / kept home unless the turn may
        recall such work (asked of a row AS IT IS NOW)."""
        if r["archived"]:
            return False
        return not (self.withhold and (r["local_only"] or r["keep_home"]))

    def told(self, r: dict[str, Any]) -> bool:
        """THE WALL (``wall.may_tell``) for one row; on the phone, also no
        owner the owner's own message did not name (D-13.2)."""
        from .clients import wall

        files = [f["path"] for f in r.get("files") or []]
        ok = wall.may_tell(self.turn, self.cmap, folder=r.get("folder") or "",
                           title=r.get("title") or "", files=files)
        if not ok or not self.phone:
            return ok
        owners = wall.owners_of(self.cmap, folder=r.get("folder") or "", title=r.get("title") or "",
                                files=files, waiting=True)
        if owners is None:
            return False
        return owners <= {_CLIENT + str(i) for i in self.turn.named}

    def live(self, rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """``{id: row as it is now}`` for ``rows`` whose source still exists.
        RAISES when the sources cannot be asked."""
        return {r["id"]: r for r in as_it_is_now(self.engine, rows)}

    def recap_ok(self, r: dict[str, Any]) -> bool:
        """May this turn be told the row's recap lines? Work in exactly the
        turn's folder (today's rule), or (D-13.1) a row that is ONE confirmed
        client's alone, that client named by the owner's own message, in a
        turn that belongs to no client. Never another app, never the phone.
        Text only; never raises."""
        try:
            t = self.turn
            if self.outside or self.phone or getattr(t, "outside", False):
                return False
            own = str(getattr(t, "folder_key", "") or "")
            if own and keys.folder_key(keys.home_folder(r.get("folder") or "")) == own:
                return True
            if not (t.ok and t.owner is None and t.named):
                return False
            from .clients import wall

            owners = wall.owners_of(self.cmap, folder=r.get("folder") or "", title=r.get("title") or "",
                                    files=[f["path"] for f in r.get("files") or []])
            if not owners or len(owners) != 1:
                return False
            (only,) = tuple(owners)
            return only.startswith(_CLIENT) and only[len(_CLIENT):] in t.named
        except Exception:  # noqa: BLE001 — unsure: no recap
            log.debug("work lookup: a recap could not be cleared; left out", exc_info=True)
            return False


@dataclass
class Found:
    """What one :func:`lookup` may tell.

    ``rows`` newest/best first, each a row as it is now with ``times`` (how
    many repeats it stands for) and ``recap`` (the kept recap's lines, only
    when this turn may be told them and the recap is about the work as it is
    now; else absent). ``held_back`` rows the wall kept out (never named),
    ``matched`` how many it told before the limit, ``building`` a first
    build still running, ``unreadable`` the record or its sources could not
    be read (nothing is told), ``in_client`` / ``wall_ok`` for the wording."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    held_back: int = 0
    matched: int = 0
    building: bool = False
    unreadable: bool = False
    in_client: bool = False
    wall_ok: bool = True
    #: Next leaps 13-b: the ``client`` asked for is not one this turn may
    #: name (nothing was read); ``client_names`` the ones honoured.
    client_refused: bool = False
    client_names: tuple[str, ...] = ()
    #: 13-b review: the lookup did not read or ask again every row in its
    #: reach (a read stopped at ``READ_MAX``, or rows past what it asks
    #: again), so ``matched`` and each row's ``times`` are at least that.
    partial: bool = False


def _match_words(r: dict[str, Any]) -> set[str]:
    """The words a query may find a row by: its title, its file names, its
    folder's own name, and the names along its folder's path (the drive and
    ``Users\\<name>`` left out; "documents", "clients" and the like only as
    the folder's own name)."""
    from .clients import names as _names

    folder = str(r.get("folder") or "")
    stems = [keys.file_name(f["path"]).rsplit(".", 1)[0] for f in r.get("files") or []]
    got = keys.words(" ".join([str(r.get("title") or ""), *stems, keys.folder_name(folder)]))
    try:
        got |= keys.words(" ".join(_names.path_parts(folder))) - keys.GENERIC_FOLDER_WORDS
    except Exception:  # noqa: BLE001 — a path that cannot be read adds nothing
        pass
    return got


def _named_clients_asked(reader: "Reader", query: str) -> list[str]:
    """The confirmed clients the OWNER named (``turn.named``) whose name or
    alias the tool's ``query`` also uses (any case): their own folders are
    searched too, so "Baker" finds Baker's work in ``D:\\Work\\B-1234``. The
    query never adds a client the owner did not name. Never raises."""
    from .clients import names as _names

    try:
        named = sorted(str(i) for i in (reader.turn.named or ()))
        if not named or not str(query or "").strip():
            return []
        said = f" {_names.signature(query)} "
        out = []
        for cid in named:
            entry = (reader.cmap.names or {}).get(cid)
            if not entry:
                continue
            _name, sig, aliases = entry
            if any(s and f" {s} " in said for s in (sig, *tuple(aliases or ()))):
                out.append(cid)
        return out
    except Exception:  # noqa: BLE001 — nothing more is searched
        log.debug("work lookup: the query's client names could not be read", exc_info=True)
        return []


def _client_asked(reader: "Reader", client: str) -> "list[str] | None":
    """The confirmed clients the tool's ``client`` argument names (any case,
    whole words: "the Baker account" names Baker), IF every one is the
    turn's own client or one the OWNER's own words named; ``[]`` when not
    (a name that is no client, a waiting name and a client the owner did
    not name are alike); None when the turn's clients could not be read.
    Never raises."""
    from .clients import names as _names

    try:
        t = reader.turn
        if not t.ok:
            return None
        if reader.outside or getattr(t, "outside", False):
            return []
        said = f" {_names.signature(client)} "
        if not said.strip():
            return []
        ids = sorted(
            cid for cid, (_name, sig, aliases) in (reader.cmap.names or {}).items()
            if any(s and f" {s} " in said for s in (sig, *tuple(aliases or ())))
        )
        allowed = {str(i) for i in (t.named or ())}
        if t.client is not None:
            allowed.add(str(t.client.client_id))
        if not ids or not set(ids) <= allowed:
            return []
        return ids
    except Exception:  # noqa: BLE001 — unsure: not honoured
        log.debug("work lookup: the client asked for could not be read", exc_info=True)
        return []


def _name_words(reader: "Reader", ids: list[str]) -> set[str]:
    """The pointing words of these clients' own names and aliases (they
    chose the client; they choose nothing more among its work)."""
    out: set[str] = set()
    for cid in ids:
        entry = (reader.cmap.names or {}).get(cid)
        if not entry:
            continue
        name, _sig, aliases = entry
        out |= keys.words(" ".join([str(name or ""), *(str(a) for a in aliases or ())]))
    return out


def _client_rows(reader: "Reader", ids: list[str], read: Any, *, target: str,
                 anywhere: Any) -> list[dict[str, Any]]:
    """The rows the wall ATTRIBUTES to one of ``ids`` (``wall.owners_of``:
    in its folders, or naming it), newest first: read from its own folders
    (or only ``target``) and from the work anywhere in range. The wall
    still decides each one afterwards. RAISES on a fault (the caller tells
    nothing)."""
    from .clients import wall

    wanted = {_CLIENT + i for i in ids}
    if target:
        places = [target]
    else:
        places = [root.folder or root.key for cid in ids
                  for root in tuple(reader.cmap.roots_of(cid))[:CLIENT_FOLDERS_READ]]
    pool: dict[str, dict[str, Any]] = {}
    for place in places:
        for r in read(place):
            pool.setdefault(r["id"], r)
    for r in ([] if target else anywhere()):
        pool.setdefault(r["id"], r)
    out = []
    for r in pool.values():
        owners = wall.owners_of(reader.cmap, folder=r.get("folder") or "", title=r.get("title") or "",
                                files=[f["path"] for f in r.get("files") or []])
        if owners and owners & wanted:
            out.append(r)
    out.sort(key=lambda r: (_newest(r), r["id"]), reverse=True)
    return out


def _decisions_href(reader: "Reader", r: dict[str, Any]) -> str:
    """Where the owner reads this piece's recap: its client's page when the
    piece is in a confirmed client's folder, else the Memory page at the
    piece (by its id: no folder in the address)."""
    try:
        root = reader.cmap.client_of(r.get("folder") or "")
    except Exception:  # noqa: BLE001 — the Memory page lists it either way
        root = None
    if root is not None and getattr(root, "client_id", ""):
        return CLIENT_HREF + quote(str(root.client_id), safe="")
    return MEMORY_WORK_HREF + quote(str(r["id"]), safe="")


def _kept_recaps(engine: Any, ids: list[str]) -> dict[str, dict[str, Any]]:
    """The kept recaps of ``ids`` (``recap.recaps_for``); {} when they cannot
    be read (a recap is extra)."""
    from . import recap as _recap

    if not ids:
        return {}
    try:
        with session_scope(engine) as db:
            return _recap.recaps_for(db, ids)
    except Exception:  # noqa: BLE001
        log.debug("work lookup: recaps could not be read", exc_info=True)
        return {}


def _newest(r: dict[str, Any]) -> datetime:
    got = r.get("updated")
    return got if isinstance(got, datetime) else datetime.min


def lookup(platform: Any, *, turn: Any = None, cmap: Any = None, folder: str = "", query: str = "",
           exclude_ref: str = "", exclude_refs: Iterable[str] = (), limit: int = LIMIT,
           outside: bool = False, phone: bool = False, after: "datetime | None" = None,
           before: "datetime | None" = None, kinds: "Iterable[str] | None" = None,
           client: str = "") -> Found:
    """The work this turn may be told about: in ``folder`` (and under it),
    or that ``query``'s words point at, or both; with neither, the turn's own
    folder, else the newest work anywhere. ``turn`` None is the turn in
    flight (``wall.current()``). ``exclude_ref`` / ``exclude_refs``: the
    turn's own conversation and run, never listed as earlier work.

    Next leaps 13-b: ``after`` / ``before`` (naive UTC, inclusive, from
    :func:`bound`), ``kinds`` (record kinds, None = all) and ``client`` (a
    name; see the module docstring). Any of them with no ``folder`` looks
    everywhere the wall allows. BLOCKING. Never raises."""
    try:
        from .clients import wall

        if turn is None and not outside:
            turn = wall.current()
        reader = Reader.open(platform, turn=turn, cmap=cmap, folder=folder, query=query,
                             outside=outside, phone=phone)
        if reader is None:
            return Found(unreadable=True)
        found = Found(in_client=reader.turn.owner is not None, wall_ok=bool(reader.turn.ok),
                      building=bool(reader.wm.building))
        try:
            n = int(limit)
        except (TypeError, ValueError):
            n = LIMIT
        n = max(1, min(LIMIT_MAX, n))
        skip = {str(x) for x in (exclude_ref, *tuple(exclude_refs or ())) if str(x or "").strip()}
        words = keys.words(str(query or "")[:4000])
        first = after if isinstance(after, datetime) else None
        last = before if isinstance(before, datetime) else None
        want = frozenset(str(k) for k in kinds) if kinds is not None else None
        filtered = first is not None or last is not None or want is not None
        # THE CLIENT the model asked for (13-b): only one this turn may name.
        asked: list[str] = []
        if str(client or "").strip():
            got_ids = _client_asked(reader, str(client))
            if not got_ids:
                found.client_refused = True
                if got_ids is None:
                    found.wall_ok = False
                return found
            asked = got_ids
            found.client_names = tuple(
                str((reader.cmap.names.get(cid) or ("",))[0] or "") for cid in asked)
            words -= _name_words(reader, asked)
        place = str(folder or "").strip()
        target = place or ("" if (words or filtered or asked) else str(reader.turn.folder_key or ""))
        # 13-b review: an answer that SAYS HOW MANY (a filter or a client)
        # asks again every row its reads brought back; any read that stopped
        # at its cap, or rows past what is asked, make the lookup partial.
        counting = filtered or bool(asked)
        cut = False

        def read(where: str) -> list[dict[str, Any]]:
            nonlocal cut
            if filtered:
                got = reader.wm.between(first, last, [where], want, READ_MAX)
                cut = cut or len(got) >= READ_MAX
                return got
            got, total = reader.wm.for_folder(where, limit=READ_MAX)
            cut = cut or total > len(got)
            return got

        def anywhere() -> list[dict[str, Any]]:
            nonlocal cut
            if filtered:
                got = reader.wm.between(first, last, None, want, READ_MAX)
                cut = cut or len(got) >= READ_MAX
                return got
            got = list(reader.wm._rows())
            # 13-a's words read the newest LIGHT_MAX rows by design; a count
            # for a client is a claim about all of its work.
            cut = cut or (counting and len(got) >= _index.LIGHT_MAX)
            return got

        if asked:
            rows = _client_rows(reader, asked, read, target=place, anywhere=anywhere)
        elif target:
            rows = read(target)
        else:
            rows = anywhere()
        # A client the OWNER named, that the query names too: its own folders
        # are read as well (its folders need not carry its name).
        theirs: set[str] = set()
        bonus: set[str] = set()
        if asked:
            theirs = {r["id"] for r in rows}  # all the asked client's: no bonus
        elif words and not place:
            seen = {r["id"] for r in rows}
            for cid in _named_clients_asked(reader, query):
                for root in tuple(reader.cmap.roots_of(cid))[:8]:
                    for r in read(root.folder or root.key):
                        theirs.add(r["id"])
                        if r["id"] not in seen:
                            seen.add(r["id"])
                            rows.append(r)
            bonus = theirs
        rows = [r for r in rows if str(r.get("ref") or "") not in skip]
        if words:
            # A recap line counts only where THIS turn may be told it: looked
            # for in this turn's own folder and a named client's folders.
            own = str(reader.turn.folder_key or "")
            pool = [r for r in rows if r["id"] in theirs or (
                own and keys.folder_key(keys.home_folder(r.get("folder") or "")) == own)]
            may_recap = [r for r in pool[:RECAP_SCAN_MAX] if reader.recap_ok(r)]
            kept = _kept_recaps(reader.engine, [r["id"] for r in may_recap])
            scored: list[tuple[int, datetime, dict[str, Any]]] = []
            for r in rows:
                have = _match_words(r)
                got = kept.get(r["id"])
                if got:
                    have |= keys.words(" ".join(got.get("lines") or []))
                hits = len(words & have) + (2 if r["id"] in bonus else 0)
                if hits:
                    scored.append((hits, _newest(r), r))
            scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
            rows = [r for *_s, r in scored]
        ask_max = max(1, int(COUNTED_MAX if counting else CANDIDATES_MAX))
        cut = cut or len(rows) > ask_max
        candidates = rows[:ask_max]
        # Ask the sources again before anything is said (RAISES: nothing told).
        try:
            live = reader.live(candidates)
        except Exception:  # noqa: BLE001
            log.debug("work lookup: the sources could not be asked; nothing is told", exc_info=True)
            found.unreadable = True
            return found
        told: list[dict[str, Any]] = []
        for r in candidates:
            got = live.get(r["id"])
            if got is None or not reader.allowed(got):
                continue
            if not reader.told(got):
                found.held_back += 1
                continue
            told.append(got)
        groups = collapse(told)
        found.matched = len(groups)
        shown = [{**row, "times": times} for row, times in groups[:n]]
        # 13-b: every piece shown has its kept recap read (never for another
        # app or the phone), for how many Open lines it holds and where its
        # decisions are read; the LINES only where ``recap_ok`` allows.
        rich = not (reader.outside or reader.phone)
        recaps = _kept_recaps(reader.engine, [r["id"] for r in shown] if rich else [])
        from . import recap as _recap

        for r in shown:
            got = recaps.get(r["id"])
            if not (got and _recap.current_for(got, r.get("live_updated") or "")):
                continue
            lines = list(got.get("lines") or [])
            r["open"] = sum(1 for ln in lines if _OPEN_RX.match(str(ln)))
            r["decisions"] = _decisions_href(reader, r)
            if reader.recap_ok(r):
                r["recap"] = lines
        found.rows = shown
        found.partial = bool(cut)
        # The plan's measure: how often a lookup matches more than it shows.
        log.info("work lookup: %d matched, %d shown, %d held back%s", found.matched, len(shown),
                 found.held_back, " (not all read: at least)" if cut else "")
        return found
    except Exception:  # noqa: BLE001 — the record is extra; a turn never fails on it
        log.debug("work lookup: the record could not be read", exc_info=True)
        return Found(unreadable=True)


__all__ = [
    "CANDIDATES_MAX", "CLIENT_HREF", "COUNTED_MAX", "KIND_ARGS", "LIMIT", "LIMIT_MAX", "MEMORY_WORK_HREF",
    "READ_MAX", "TURN_REBUILD", "Found", "Reader", "as_it_is_now", "bound", "collapse", "kinds_of", "lookup",
    "withhold_local_only",
]
