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

BLOCKING (SQLite): the callers run it off the event loop. :func:`lookup`
never raises.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from ..core.db import session_scope
from . import collect, keys
from .index import LIGHT_MAX, work_map

log = logging.getLogger(__name__)

#: Rows a turn's own refresh may rebuild (a long first build is finished in
#: the background; a turn never waits for it).
TURN_REBUILD = 60
#: Pieces of work one lookup lists by default / at most.
LIMIT = 12
LIMIT_MAX = 20
#: Rows asked again and walled for one lookup, at most (the best first).
CANDIDATES_MAX = 200
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
           outside: bool = False, phone: bool = False) -> Found:
    """The work this turn may be told about: in ``folder`` (and under it),
    or that ``query``'s words point at, or both; with neither, the turn's own
    folder, else the newest work anywhere. ``turn`` None is the turn in
    flight (``wall.current()``). ``exclude_ref`` / ``exclude_refs``: the
    turn's own conversation and run, never listed as earlier work.
    BLOCKING. Never raises."""
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
        target = str(folder or "").strip() or ("" if words else str(reader.turn.folder_key or ""))
        if target:
            rows, _total = reader.wm.for_folder(target, limit=LIGHT_MAX)
        else:
            rows = list(reader.wm._rows())
        # A client the OWNER named, that the query names too: its own folders
        # are read as well (its folders need not carry its name).
        theirs: set[str] = set()
        if words and not str(folder or "").strip():
            seen = {r["id"] for r in rows}
            for cid in _named_clients_asked(reader, query):
                for root in tuple(reader.cmap.roots_of(cid))[:8]:
                    got, _total = reader.wm.for_folder(root.folder or root.key, limit=LIGHT_MAX)
                    for r in got:
                        theirs.add(r["id"])
                        if r["id"] not in seen:
                            seen.add(r["id"])
                            rows.append(r)
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
                hits = len(words & have) + (2 if r["id"] in theirs else 0)
                if hits:
                    scored.append((hits, _newest(r), r))
            scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
            rows = [r for *_s, r in scored]
        candidates = rows[:CANDIDATES_MAX]
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
        recaps = _kept_recaps(reader.engine, [r["id"] for r in shown if reader.recap_ok(r)])
        from . import recap as _recap

        for r in shown:
            got = recaps.get(r["id"])
            if got and _recap.current_for(got, r.get("live_updated") or ""):
                r["recap"] = list(got.get("lines") or [])
        found.rows = shown
        # The plan's measure: how often a lookup matches more than it shows.
        log.info("work lookup: %d matched, %d shown, %d held back", found.matched, len(shown),
                 found.held_back)
        return found
    except Exception:  # noqa: BLE001 — the record is extra; a turn never fails on it
        log.debug("work lookup: the record could not be read", exc_info=True)
        return Found(unreadable=True)


__all__ = [
    "CANDIDATES_MAX", "LIMIT", "LIMIT_MAX", "TURN_REBUILD", "Found", "Reader", "as_it_is_now",
    "collapse", "lookup", "withhold_local_only",
]
