"""The work record: kept up to date, and read (v1.491.0).

KEEPING IT UP TO DATE. :meth:`WorkMap.refresh` compares each source's cheap
stamp with the row it built last time and rebuilds only what changed; a
source row that is gone (a deleted chat, a deleted run) takes its work row
with it, also when it lies past a listing's bound (its source is then asked
for by id). A plain refresh is throttled, never waits for another refresh,
and builds a bounded number of rows inline; what is left (the first build of
a long history) is finished by ONE background thread that scans once and
writes in short transactions. Nothing wakes on a timer: the record is
refreshed when it is about to be read.

One row that cannot be built or stored never stops the others: it is skipped
until its source changes.

READING. Folder reads are SQL over the whole table; :meth:`WorkMap.matching`
looks at the newest :data:`LIGHT_MAX` rows. Everything here is BLOCKING
(SQLite) and is called off the event loop.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, or_
from sqlmodel import select

from ..core.db import session_scope
from . import collect, keys
from .models import KIND_CHAT, KIND_RUN, KIND_TOOL_CODE, WorkEntry

log = logging.getLogger(__name__)

#: A read refreshes the record at most this often.
REFRESH_EVERY_S = 20.0
#: Rows a plain refresh rebuilds inline; the rest go to the background build.
REBUILD_PER_REFRESH = 60
#: Rows written per transaction (short write locks).
_WRITE_CHUNK = 25
#: Newest rows :meth:`WorkMap.matching` looks at.
LIGHT_MAX = 3000
#: A message with more pointing words than this is content (a pasted
#: document), not a pointer at earlier work: nothing is matched.
QUERY_WORDS_MAX = 30
#: Where each kind of work opens (the dashboard's own addresses).
CHAT_HREF = "/chat?thread="
RUN_HREF = "/sessions/"

_ATTR = "_ironjarvis_work_map"
_ATTR_LOCK = threading.Lock()


def link_for(kind: str, ref: str) -> str:
    """Where the dashboard opens this piece of work."""
    if kind == KIND_CHAT:
        return CHAT_HREF + quote(str(ref), safe="")
    if kind == KIND_RUN:
        return RUN_HREF + quote(str(ref), safe="")
    from ..projects.coding_sessions import BUILD_CODE_HREF, CHAT_CODE_HREF

    base = BUILD_CODE_HREF if kind == KIND_TOOL_CODE else CHAT_CODE_HREF
    return base + quote(str(ref), safe="")


def _iso(value: Any) -> str | None:
    if not isinstance(value, datetime):
        return None
    v = value if value.tzinfo is None else value.astimezone(timezone.utc).replace(tzinfo=None)
    return v.replace(tzinfo=timezone.utc).isoformat()


def _files(files_json: Any) -> list[dict[str, str]]:
    try:
        data = json.loads(files_json or "[]")
    except (TypeError, ValueError):
        return []
    out = []
    for f in data if isinstance(data, list) else []:
        if isinstance(f, dict) and str(f.get("path") or "").strip():
            out.append({"path": str(f["path"]), "how": str(f.get("how") or "wrote")})
    return out


def _row(e: WorkEntry) -> dict[str, Any]:
    files = _files(e.files_json)
    return {
        "id": e.id, "kind": e.kind, "ref": e.ref, "title": e.title or "",
        "folder": e.folder or "", "folder_key": e.folder_key or "",
        "project_id": e.project_id, "tool": e.tool or "", "origin": e.origin or "",
        "outcome": e.outcome or "", "local_only": bool(e.local_only),
        "keep_home": bool(e.keep_home), "archived": bool(e.archived), "files": files,
        "files_total": int(e.files_total or len(files)), "note": e.note or "",
        "updated": e.updated_at, "started": e.started_at,
    }


class WorkMap:
    """One per database engine (:func:`work_map`)."""

    def __init__(self, engine: Any, home: Any = None) -> None:
        self.engine = engine
        self.home = home
        self._lock = threading.Lock()
        #: When the last refresh ended (monotonic), None = never.
        self._last: float | None = None
        #: Moves whenever rows were written or removed; the light cache
        #: follows it.
        self.version = 0
        self._light: tuple[int, list[dict[str, Any]]] | None = None
        #: ``{id: stamp}`` of candidates not to try again until their source
        #: changes: a row that could not be built or stored, or a source that
        #: is not work yet (a chat with no reply).
        self._skip: dict[str, str] = {}
        #: Rows the last scan found still to build.
        self.pending = 0
        self._builder: threading.Thread | None = None
        self.refreshed_at: datetime | None = None

    # -- keeping it up to date ---------------------------------------------
    @property
    def building(self) -> bool:
        """Is a first build still going?"""
        t = self._builder
        return self.pending > 0 or (t is not None and t.is_alive())

    def refresh(self, *, force: bool = False, budget: int | None = None) -> dict[str, Any]:
        """Bring the record up to date. Never raises.

        ``force``: wait for a refresh already running, ignore the throttle and
        build EVERYTHING inline unless ``budget`` says less (the tests, a
        caller that needs the record whole). A plain call returns at once when
        one ran in the last :data:`REFRESH_EVERY_S` or is running now, builds
        at most ``budget`` (:data:`REBUILD_PER_REFRESH`) rows inline, and
        hands what is left to the background build."""
        if self.engine is None:
            return {"skipped": True}
        if not force and self._last is not None and time.monotonic() - self._last < REFRESH_EVERY_S:
            return {"skipped": True}
        if not self._lock.acquire(blocking=force):
            return {"busy": True}
        try:
            limit = budget if budget is not None else (None if force else REBUILD_PER_REFRESH)
            stats = self._refresh(limit)
        except Exception:  # noqa: BLE001 — the record is extra; never break a caller
            log.debug("work record: refresh failed", exc_info=True)
            stats = {"failed": True}
        finally:
            self._last = time.monotonic()
            self.refreshed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            self._lock.release()
        if not force and stats.get("pending"):
            self._start_builder()
        return stats

    def _start_builder(self) -> None:
        t = self._builder
        if t is not None and t.is_alive():
            return
        t = threading.Thread(target=self._build_rest, name="work-record-build", daemon=True)
        self._builder = t
        try:
            t.start()
        except Exception:  # noqa: BLE001 — cannot start a thread (shutting down)
            self._builder = None

    def _build_rest(self) -> None:
        """The background build: ONE scan, then everything still to build, in
        short transactions with a breath between them."""
        with self._lock:
            try:
                self._refresh(None, breathe=True)
            except Exception:  # noqa: BLE001
                log.debug("work record: the background build failed", exc_info=True)
            finally:
                self._last = time.monotonic()

    def wait_built(self, timeout: float = 30.0) -> bool:
        """Wait for a background build to end (tests). True when none runs."""
        t = self._builder
        if t is not None:
            t.join(timeout)
            return not t.is_alive()
        return True

    def _refresh(self, budget: int | None, *, breathe: bool = False) -> dict[str, Any]:
        with session_scope(self.engine) as db:
            existing = {str(i): (str(s or ""), str(k or ""), str(r or "")) for i, s, k, r in db.exec(
                select(WorkEntry.id, WorkEntry.stamp, WorkEntry.kind, WorkEntry.ref)
            )}
            cands, complete = collect.candidates(db, home=self.home)
            wanted = {c.id for c in cands}
            unlisted = [i for i in existing if i not in wanted]
            gone = [i for i in unlisted if existing[i][1] in complete]
            # Past a listing's bound (or a source that could not be listed):
            # removed only when the source row itself is gone.
            beyond = [i for i in unlisted if existing[i][1] not in complete]
            if beyond:
                try:
                    alive = collect.source_exists(db, [(existing[i][1], existing[i][2]) for i in beyond])
                    gone += [i for i in beyond if (existing[i][1], existing[i][2]) not in alive]
                except Exception:  # noqa: BLE001 — unreadable source: leave its rows be
                    log.debug("work record: could not ask the sources by id", exc_info=True)
        # Forget skips whose source moved on (or left).
        stamps = {c.id: c.stamp for c in cands}
        self._skip = {i: s for i, s in self._skip.items() if stamps.get(i) == s}
        changed = [
            c for c in cands
            if existing.get(c.id, ("\0",))[0] != c.stamp and self._skip.get(c.id) != c.stamp
        ]
        todo = changed if budget is None else changed[: max(0, int(budget))]
        self.pending = len(changed) - len(todo)
        built = 0
        for start in range(0, len(todo), _WRITE_CHUNK):
            got, dropped = self._write(todo[start : start + _WRITE_CHUNK], existing)
            built += got
            gone += dropped
            if got:
                self.version += 1
            if breathe:
                self.pending = len(changed) - min(len(changed), start + _WRITE_CHUNK)
                time.sleep(0.005)
        removed = 0
        for start in range(0, len(gone), 400):
            chunk = gone[start : start + 400]
            with session_scope(self.engine) as db:
                db.exec(sa_delete(WorkEntry).where(WorkEntry.id.in_(chunk)))  # type: ignore[attr-defined]
                db.commit()
            removed += len(chunk)
            self.version += 1
        return {"built": built, "removed": removed, "pending": self.pending}

    def _write(self, chunk: list[collect.Candidate], existing: dict[str, Any]) -> tuple[int, list[str]]:
        """Build and store ``chunk``: ``(rows written, ids to remove)``. One
        transaction for the chunk; if that fails, row by row, so one row
        SQLite refuses never costs the others."""
        entries: list[tuple[collect.Candidate, WorkEntry]] = []
        dropped: list[str] = []
        with session_scope(self.engine) as db:
            for cand in chunk:
                try:
                    values = cand.build(db)
                except Exception:  # noqa: BLE001 — one bad row never stops the rest
                    log.debug("work record: %s could not be built", cand.id, exc_info=True)
                    self._skip[cand.id] = cand.stamp
                    continue
                if values is None:  # gone, or not work yet
                    self._skip[cand.id] = cand.stamp
                    if cand.id in existing:
                        dropped.append(cand.id)
                    continue
                entries.append((cand, WorkEntry(id=cand.id, kind=cand.kind, ref=cand.ref,
                                                stamp=cand.stamp, **values)))
            try:
                for _cand, entry in entries:
                    db.merge(entry)
                db.commit()
                return len(entries), dropped
            except Exception:  # noqa: BLE001 — retry one by one below
                db.rollback()
        written = 0
        for cand, entry in entries:
            try:
                with session_scope(self.engine) as db:
                    db.merge(WorkEntry(**entry.model_dump()))
                    db.commit()
                written += 1
            except Exception:  # noqa: BLE001
                log.debug("work record: %s could not be stored", cand.id, exc_info=True)
                self._skip[cand.id] = cand.stamp
        return written, dropped

    # -- reading ------------------------------------------------------------
    def _rows(self) -> list[dict[str, Any]]:
        """The newest :data:`LIGHT_MAX` rows, newest first, each with its
        pointing words; cached until a refresh changes something."""
        cached = self._light
        if cached is not None and cached[0] == self.version:
            return cached[1]
        version = self.version
        W = WorkEntry
        with session_scope(self.engine) as db:
            stmt = select(W).order_by(W.updated_at.desc(), W.id).limit(LIGHT_MAX)  # type: ignore[attr-defined]
            got = [_row(e) for e in db.exec(stmt)]
        for row in got:
            stems = [keys.file_name(f["path"]).rsplit(".", 1)[0] for f in row["files"]]
            row["_words"] = keys.words(" ".join([row["title"], *stems]))
            row["_folder_words"] = keys.words(keys.folder_name(row["folder"])) - keys.GENERIC_FOLDER_WORDS
        self._light = (version, got)
        return got

    def count(self) -> int:
        with session_scope(self.engine) as db:
            return int(db.exec(select(func.count()).select_from(WorkEntry)).one() or 0)

    def for_folder(self, folder: str, *, limit: int = 6, exclude_ref: str = "",
                   under: bool = True) -> tuple[list[dict[str, Any]], int]:
        """The work on record in ``folder`` (and, with ``under``, in folders
        under it), newest first: ``(rows[:limit], how many there are)``. A
        coding session's own copy of a repository counts as the repository's
        folder."""
        base = keys.folder_key(keys.home_folder(folder))
        if not base:
            return [], 0
        W = WorkEntry
        where = W.folder_key == base
        if under:
            prefix = base if base.endswith("/") else base + "/"
            where = or_(where, W.folder_key.startswith(prefix, autoescape=True))  # type: ignore[attr-defined]
        conds = [where]
        if exclude_ref:
            conds.append(W.ref != exclude_ref)
        with session_scope(self.engine) as db:
            total = int(db.exec(select(func.count()).select_from(W).where(*conds)).one() or 0)
            stmt = (
                select(W).where(*conds)
                .order_by(W.updated_at.desc(), W.id)  # type: ignore[attr-defined]
                .limit(max(0, int(limit)))
            )
            rows = [_row(e) for e in db.exec(stmt)]
        return rows, total

    def matching(self, query: str, *, limit: int = 3, skip_folder: str = "",
                 exclude_ref: str = "") -> list[dict[str, Any]]:
        """Work on record that the words of ``query`` point at, best first.

        A row counts when TWO of the query's pointing words are in its title,
        its file names or its folder's name AND at least one of them is RARE
        (it belongs to few folders on record: "reconciliation" names nothing
        in a record full of reconciliations, "hartleys" does). One word alone
        counts only when it is written as a NAME in the message ("…for
        Hartleys") and is the name of exactly one folder on record. Bare
        numbers, file endings and short words never count
        (:func:`keys.words`), and a long message is content, not a pointer.
        Rows in ``skip_folder`` are left out (the caller lists that folder
        itself)."""
        q = keys.words(query)
        if not q or len(q) > QUERY_WORDS_MAX:
            return []
        rows = self._rows()
        names = keys.named_words(query) & q
        skip = keys.folder_key(keys.home_folder(skip_folder)) if skip_folder else ""
        # How many FOLDERS each word belongs to (work in one client's folder
        # shares its words; that is one place, not many).
        places: dict[str, set[str]] = {}
        named_by: dict[str, set[str]] = {}
        for r in rows:
            for w in r["_words"] | r["_folder_words"]:
                places.setdefault(w, set()).add(r["folder_key"])
            for w in r["_folder_words"]:
                named_by.setdefault(w, set()).add(r["folder_key"])
        few = max(1, len({r["folder_key"] for r in rows}) // 10)
        scored: list[tuple[int, int, Any, dict[str, Any]]] = []
        for r in rows:
            if exclude_ref and r["ref"] == exclude_ref:
                continue
            if skip and keys.inside(r["folder_key"], skip):
                continue
            both = q & (r["_words"] | r["_folder_words"])
            rare = [w for w in both if len(places.get(w, ())) <= few]
            named = [w for w in names & r["_folder_words"] if len(named_by.get(w, ())) == 1]
            if (len(both) >= 2 and rare) or named:
                scored.append((len(rare) + (2 if named else 0), len(both), r["updated"], r))
        scored.sort(key=lambda t: (t[0], t[1], t[2] or datetime.min), reverse=True)
        return [r for *_score, r in scored[: max(0, int(limit))]]

    def folders(self, *, limit: int = 300) -> tuple[list[dict[str, Any]], int]:
        """Every folder with work on record, the most recently used first:
        ``(folders[:limit], how many folders there are)``. A folder's count is
        the work in exactly that folder."""
        W = WorkEntry
        last = func.max(W.updated_at)
        with session_scope(self.engine) as db:
            total = int(db.exec(select(func.count(func.distinct(W.folder_key)))).one() or 0)
            top = list(db.exec(
                select(W.folder_key, func.count(), last)
                .group_by(W.folder_key)
                .order_by(last.desc(), W.folder_key)
                .limit(max(0, int(limit)))
            ))
            wanted = [str(k) for k, _n, _l in top]
            detail: dict[str, dict[str, Any]] = {}
            for i in range(0, len(wanted), 400):
                stmt = (
                    select(W.folder_key, func.min(W.folder), W.kind, func.count())
                    .where(W.folder_key.in_(wanted[i : i + 400]))  # type: ignore[attr-defined]
                    .group_by(W.folder_key, W.kind)
                )
                for key, shown, kind, n in db.exec(stmt):
                    d = detail.setdefault(str(key), {"folder": str(shown or ""), "kinds": {}})
                    d["kinds"][str(kind)] = int(n or 0)
        out = []
        for key, n, when in top:
            d = detail.get(str(key), {"folder": str(key), "kinds": {}})
            out.append({
                "folder": d["folder"], "name": keys.folder_name(d["folder"]),
                "count": int(n or 0), "last_at": _iso(when), "kinds": d["kinds"],
            })
        return out, total

    def entries(self, folder: str, *, limit: int = 100) -> tuple[list[dict[str, Any]], int]:
        """The work in exactly ``folder``, as the API answers it."""
        rows, total = self.for_folder(folder, limit=limit, under=False)
        return [public(r) for r in rows], total


def public(row: dict[str, Any]) -> dict[str, Any]:
    """One row as a route answers it (names, never a transcript)."""
    return {
        "id": row["id"], "kind": row["kind"], "ref": row["ref"], "title": row["title"],
        "folder": row["folder"], "tool": row["tool"], "origin": row["origin"],
        "outcome": row["outcome"], "local_only": row["local_only"], "archived": row["archived"],
        "files": [{"name": keys.file_name(f["path"]), "path": f["path"], "how": f["how"]}
                  for f in row["files"]],
        "files_total": row["files_total"], "note": row["note"],
        "updated_at": _iso(row["updated"]), "started_at": _iso(row["started"]),
        "link": link_for(row["kind"], row["ref"]),
    }


def work_map(engine: Any, home: Any = None) -> WorkMap | None:
    """THE work record for ``engine`` (one per engine, like the history
    index). ``home`` is remembered the first time it is given. None when
    there is no engine. Never raises."""
    if engine is None:
        return None
    try:
        got = getattr(engine, _ATTR, None)
        if got is None:
            with _ATTR_LOCK:
                got = getattr(engine, _ATTR, None)
                if got is None:
                    got = WorkMap(engine, home)
                    try:
                        setattr(engine, _ATTR, got)
                    except Exception:  # noqa: BLE001 — a slotted / mock engine
                        pass
        if home is not None and got.home is None:
            got.home = home
        return got
    except Exception:  # noqa: BLE001
        return None


__all__ = [
    "CHAT_HREF", "LIGHT_MAX", "QUERY_WORDS_MAX", "REBUILD_PER_REFRESH", "REFRESH_EVERY_S",
    "RUN_HREF", "WorkMap", "link_for", "public", "work_map",
]
