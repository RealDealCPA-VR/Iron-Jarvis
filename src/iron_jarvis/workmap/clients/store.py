"""The client record (v1.499.0, builder A): THE resolver, the ONLY reader of
client preferences, the question under a reply, and every change.

Folder rules (enforced here, one plain sentence each, raised as
``ClientRefused``). Paths are compared as TEXT only (``keys.folder_key``,
``keys.inside``, ``keys.home_folder``); nothing touches the disk. A folder is
refused when it is: a drive, share or relative top (``index._FORGETTABLE``);
only Clients-type, generic or container words ("Downloads holds work for many
people. Pick a folder that is only Hartleys'."); a folder that holds another
client's folder ("C:\\Clients holds Smith's folder. Pick Hartleys' own
folder."); a folder inside another client's folder; a folder that already
belongs to another confirmed client. No nesting, either way: a folder
resolves to at most one client.

A confirmed client that becomes Not a client keeps its folder rows and
preferences, which claim and reach nothing; another client may take its
folders over; Make it a client again restores the folders still free. A
FORGOTTEN folder (``workforgotten``) stays its client's for the wall and for
preferences; only its work is not shown.

Every change bumps the process-local write version (so :func:`load_map`
re-reads), publishes its event with IDS ONLY (``client.confirmed {id}``,
``client.declined {id}``, ``client.changed {id}``), and after a confirm,
decline or confirm-many starts a noticing pass so the next batch is ready
(only in the running daemon: the pass is handed to the loop the noticing
loop runs on, never run inside the request). Two answers at once: the second
raises ``ClientConflict``. Undo (builder C's cards) restores exactly what one
change did through these functions; a step not listed here is agreed with
builder A, never written as SQL elsewhere.

Only this file and ``workmap/models.py`` may name the client-preference
table's class or table (a grep pin).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping, Sequence

from sqlmodel import select

from ...core.db import session_scope
from ...core.ids import new_id, utcnow
from .. import keys
from ..index import _FORGETTABLE, _iso
from ..models import (
    CLIENT_ADDED,
    CLIENT_CONFIRMED,
    CLIENT_DECLINED,
    CLIENT_FOLDER_ADDED,
    CLIENT_FOLDER_FOUND,
    CLIENT_PREF_CONFIRMED,
    CLIENT_PREF_SAID,
    CLIENT_PREF_TYPED,
    CLIENT_PROPOSED,
    WorkClient,
    WorkClientFolder,
    WorkClientPreference,
)
from . import (
    CLIENT_ID_RE,
    CLIENT_PREF_ID_RE,
    MAX_WAITING,
    ClientConflict,
    ClientError,
    ClientNotFound,
    ClientRefused,
    ClientRoot,
    ClientsUnreadable,
    Waiting,
    names,
)

log = logging.getLogger(__name__)

#: A loaded map is re-read when the write version moved, or when it is older
#: than this (another process may have written).
MAP_MAX_AGE_S = 30.0
#: At most this many other spellings per client.
MAX_ALIASES = 5
#: Client preferences kept for one client, at most.
MAX_PREFERENCES = 100
NAME_CHARS = 80
QUOTE_CHARS = 160
#: The question under a reply waits this long for a noticing pass, at most
#: (the lane gives up at 1 s).
QUESTION_LOCK_S = 0.5

try:
    from ...core.db import BUSY_TIMEOUT_MS as _BUSY_MS
except Exception:  # noqa: BLE001
    _BUSY_MS = 30_000

#: One noticing pass, or one question picked, at a time (``detect.notice``
#: and :func:`question_for_turn` share it).
NOTICE_LOCK = threading.RLock()
#: One change at a time (a chat card and a page press racing each other).
WRITE_LOCK = threading.RLock()

_VERSION = 0
_VERSION_LOCK = threading.Lock()
_MAP_ATTR = "_ironjarvis_client_map"
_MAPS: dict[int, tuple[int, float, "ClientMap"]] = {}
#: The daemon loop (``detect.clients_loop`` remembers it): events and the
#: after-answer pass are handed to it from a route's worker thread.
_LOOP: dict[str, Any] = {"loop": None}
_TASKS: set[Any] = set()


def bump_version() -> int:
    """Move the process-local write version: every cached map is stale."""
    global _VERSION
    with _VERSION_LOCK:
        _VERSION += 1
        return _VERSION


def current_version() -> int:
    return _VERSION


def remember_loop(loop: Any) -> None:
    _LOOP["loop"] = loop


def _ancestors(key: str) -> list[str]:
    """``key`` and every folder above it, deepest first (text only)."""
    out = [key]
    i = len(key)
    while True:
        i = key.rfind("/", 0, i)
        if i <= 0:
            break
        out.append(key[:i])
    return out


def _key(folder: object) -> str:
    return keys.folder_key(keys.home_folder(str(folder or "").strip()))


def _strict_inside(key: str, base: str) -> bool:
    return key != base and keys.inside(key, base)


@dataclass(frozen=True, eq=False)
class ClientMap:
    """Every confirmed folder and waiting home, loaded once and cached per
    engine (:func:`load_map`). Text only: no method touches the disk or the
    database. An EMPTY map answers "nothing" to every question."""

    #: The process-local write version it was loaded at.
    version: int = 0
    #: Every claiming folder of every CONFIRMED client (forgotten ones too).
    confirmed: tuple[ClientRoot, ...] = ()
    #: Every proposed (waiting) name.
    waiting: tuple[Waiting, ...] = ()
    #: CONFIRMED clients only: ``id -> (name, signature, aliases)`` (the
    #: table ``names.named_in`` reads for ``TurnWall.named``).
    names: Mapping[str, tuple[str, str, tuple[str, ...]]] = field(default_factory=dict)
    #: WAITING names, the same shape (``remember_preference``'s guard refuses
    #: text naming a confirmed OR waiting client; the wall never uses these
    #: for ``named``).
    waiting_names: Mapping[str, tuple[str, str, tuple[str, ...]]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        by_key: dict[str, ClientRoot] = {}
        by_id: dict[str, list[ClientRoot]] = {}
        for root in self.confirmed:
            if root.key:
                by_key.setdefault(root.key, root)
                by_id.setdefault(root.client_id, []).append(root)
        homes: dict[str, Waiting] = {}
        for w in self.waiting:
            for home in w.homes:
                if home:
                    homes.setdefault(home, w)
        object.__setattr__(self, "_by_key", by_key)
        object.__setattr__(self, "_by_id", {k: tuple(v) for k, v in by_id.items()})
        object.__setattr__(self, "_homes", homes)

    def client_of(self, folder: object) -> ClientRoot | None:
        """The :class:`ClientRoot` whose folder ``folder`` is, or is inside
        (after ``keys.home_folder`` + ``keys.folder_key``). None for "",
        for a folder no confirmed client claims, and on an empty map."""
        key = _key(folder)
        if not key or not self._by_key:  # type: ignore[attr-defined]
            return None
        for anc in _ancestors(key):
            root = self._by_key.get(anc)  # type: ignore[attr-defined]
            if root is not None:
                return root
        return None

    def waiting_of(self, folder: object) -> Waiting | None:
        """The waiting name whose home ``folder`` is, or is inside (and is
        not a confirmed client's). None on an empty map."""
        key = _key(folder)
        if not key or not self._homes or self.client_of(folder) is not None:  # type: ignore[attr-defined]
            return None
        for anc in _ancestors(key):
            w = self._homes.get(anc)  # type: ignore[attr-defined]
            if w is not None:
                return w
        return None

    def provisional_of(self, folder: object) -> str | None:
        """``"folder:<key>"`` for the first name segment after a Clients-type
        segment of ``folder`` (``C:\\Clients\\Hartleys\\2026`` →
        ``folder:c:/clients/hartleys``): walled before the owner answers.
        None when the path has no Clients-type segment with a name under it.
        """
        child = names.client_child(folder)
        if child is None:
            return None
        key = keys.folder_key(child[1])
        return f"folder:{key}" if key else None

    def spans(self, folder: object) -> bool:
        """True when ``folder`` is itself Clients-type, or strictly holds a
        confirmed folder, a waiting home or a Clients-type folder (a parent
        folder: ``C:\\Clients``, ``Documents`` above Clients). False on an
        empty map for a folder with no Clients-type segment."""
        home = keys.home_folder(str(folder or "").strip())
        key = keys.folder_key(home)
        if not key:
            return False
        if names.is_client_parent(keys.folder_name(home)):
            return True
        for other in self._by_key:  # type: ignore[attr-defined]
            if _strict_inside(other, key):
                return True
        for other in self._homes:  # type: ignore[attr-defined]
            if _strict_inside(other, key):
                return True
        for root in self.confirmed:
            for seg, prefix in names.segments(root.folder):
                if names.is_client_parent(seg) and _strict_inside(keys.folder_key(prefix), key):
                    return True
        return False

    def roots_of(self, client_id: str) -> tuple[ClientRoot, ...]:
        """Every claiming folder of one confirmed client, as recorded
        (``()`` for an unknown or not-confirmed id)."""
        return self._by_id.get(str(client_id or ""), ())  # type: ignore[attr-defined]


#: The map with nothing on record.
EMPTY_MAP = ClientMap()


def aliases_of(row: Any) -> tuple[str, ...]:
    """A client row's other signatures (``aliases_json``), never raising."""
    try:
        data = json.loads(getattr(row, "aliases_json", "") or "[]")
    except (TypeError, ValueError):
        return ()
    return tuple(str(a) for a in data if isinstance(a, str) and a.strip()) if isinstance(data, list) else ()


def evidence_of(row: Any) -> dict[str, Any]:
    """A client row's ``evidence_json``, never raising (``{}``)."""
    try:
        data = json.loads(getattr(row, "evidence_json", "") or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def homes_of(row: Any) -> list[dict[str, Any]]:
    """The homes in a row's evidence, each with its ``key`` (re-derived from
    the folder as spelled when it has one)."""
    out = []
    for h in evidence_of(row).get("homes") or []:
        if not isinstance(h, dict):
            continue
        folder = str(h.get("folder") or "").strip()
        key = _key(folder) if folder else str(h.get("key") or "")
        if key:
            out.append({**h, "folder": folder or key, "key": key})
    return out


def _cached(engine: Any) -> "tuple[int, float, ClientMap] | None":
    got = getattr(engine, _MAP_ATTR, None)
    if isinstance(got, tuple):
        return got
    return _MAPS.get(id(engine))


def _cache(engine: Any, entry: "tuple[int, float, ClientMap]") -> None:
    try:
        setattr(engine, _MAP_ATTR, entry)
    except Exception:  # noqa: BLE001 — a slotted / mock engine
        _MAPS[id(engine)] = entry


def load_map(engine: Any) -> ClientMap:
    """THE resolver: every confirmed client's claiming folders and every
    waiting name, cached per engine on the process-local write version and
    re-read when older than :data:`MAP_MAX_AGE_S`. BLOCKING (two small
    reads). Raises ``ClientsUnreadable`` when the tables cannot be read
    (callers fail CLOSED, never "no clients")."""
    if engine is None:
        raise ClientsUnreadable()
    version = _VERSION
    hit = _cached(engine)
    if hit is not None and hit[0] == version and time.monotonic() - hit[1] < MAP_MAX_AGE_S:
        return hit[2]
    try:
        with session_scope(engine) as db:
            rows = list(db.exec(
                select(WorkClient).where(
                    WorkClient.status.in_([CLIENT_CONFIRMED, CLIENT_PROPOSED])  # type: ignore[attr-defined]
                )
            ))
            folder_rows = list(db.exec(
                select(WorkClientFolder.folder_key, WorkClientFolder.client_id, WorkClientFolder.folder)
            ))
    except Exception as exc:  # noqa: BLE001 — fail closed, never "no clients"
        raise ClientsUnreadable() from exc
    confirmed = {r.id: r for r in rows if r.status == CLIENT_CONFIRMED}
    roots = tuple(
        ClientRoot(client_id=str(cid), name=confirmed[cid].name, key=str(fk), folder=str(folder or fk))
        for fk, cid, folder in sorted(folder_rows, key=lambda t: (str(t[1]), str(t[0])))
        if cid in confirmed and fk
    )
    waiting = tuple(
        Waiting(client_id=r.id, name=r.name, homes=tuple(dict.fromkeys(h["key"] for h in homes_of(r))))
        for r in sorted(rows, key=lambda r: (-int(r.strength or 0), r.id))
        if r.status == CLIENT_PROPOSED
    )
    cmap = ClientMap(
        version=version,
        confirmed=roots,
        waiting=waiting,
        names={r.id: (r.name, r.signature, aliases_of(r)) for r in confirmed.values()},
        waiting_names={r.id: (r.name, r.signature, aliases_of(r)) for r in rows if r.status == CLIENT_PROPOSED},
    )
    _cache(engine, (version, time.monotonic(), cmap))
    return cmap


def _pref_view(r: Any) -> dict[str, Any]:
    try:
        evidence = json.loads(r.evidence_json or "[]")
    except (TypeError, ValueError):
        evidence = []
    evidence = [e for e in evidence if isinstance(e, dict)] if isinstance(evidence, list) else []
    return {
        "id": r.id,
        "text": r.text or "",
        "local_only": bool(r.local_only),
        "thread_id": next((str(e.get("thread_id")) for e in evidence if e.get("thread_id")), ""),
        "origin": r.origin or "",
        "evidence": evidence,
        "created_at": _iso(r.created_at),
    }


def preferences(engine: Any, client_id: str) -> list[dict[str, Any]]:
    """The ONLY reader of client preferences: ``[{id, text, local_only,
    thread_id}]`` for one client, newest first, CONFIRMED rows only (the
    status is in the SQL). ``thread_id`` is the evidence's source chat (""
    for a typed one), so the block can ask whether it became Local only.
    Each also carries ``origin``, ``evidence`` and ``created_at`` (the
    page). Raises ``ClientsUnreadable``."""
    if engine is None:
        raise ClientsUnreadable()
    P = WorkClientPreference
    try:
        with session_scope(engine) as db:
            rows = list(db.exec(
                select(P)
                .where(P.client_id == str(client_id or ""), P.status == CLIENT_PREF_CONFIRMED)
                .order_by(P.created_at.desc(), P.id)  # type: ignore[attr-defined]
            ))
    except Exception as exc:  # noqa: BLE001
        raise ClientsUnreadable() from exc
    return [_pref_view(r) for r in rows]


def folder_rows(engine: Any, client_id: str) -> list[dict[str, Any]]:
    """One client's folder rows, as recorded: ``[{key, folder, how,
    created_at}]`` (a Not-a-client client's too). Raises
    ``ClientsUnreadable``."""
    if engine is None:
        raise ClientsUnreadable()
    F = WorkClientFolder
    try:
        with session_scope(engine) as db:
            rows = list(db.exec(
                select(F).where(F.client_id == str(client_id or "")).order_by(F.created_at, F.folder_key)
            ))
    except Exception as exc:  # noqa: BLE001
        raise ClientsUnreadable() from exc
    return [{"key": r.folder_key, "folder": r.folder or r.folder_key, "how": r.how,
             "created_at": _iso(r.created_at)} for r in rows]


#: Kept free of the lane's wait, so a question marked asked is one the lane
#: still takes (the thread's return and the hand-over cost a little).
QUESTION_MARGIN_S = 0.25


def question_for_turn(
    platform: Any, *, folder: str, thread_id: str, budget_s: float | None = None,
) -> dict[str, Any] | None:
    """The one question under a reply: ``{id, name, folders, work}`` or None.

    BLOCKING; the lanes call it after a finished turn, beside
    ``suggest_for_turn``, through ``asyncio.to_thread`` under
    ``wait_for(1.0)``, and only when ``suggest_preferences`` is true
    (dashboard turns) and the reply carries no preference suggestion. Here:
    ``notice_clients`` is on and the turn's ``folder`` is inside a home of a
    proposed row whose ``asked_at`` is NULL; the strongest such row is
    picked and ``asked_at`` set under the notice lock (a question ignored
    under its reply is never put under another). Never raises (None).

    ``budget_s`` (the independent review): the lane's own wait. The write
    that marks the row asked waits on SQLite for what is left of it (minus
    :data:`QUESTION_MARGIN_S`), never the database's 30 s, and is rolled back
    once the time is past: a question the lane gave up on is never marked
    asked, so it rides a later reply."""
    started = time.monotonic()

    def _left() -> float:
        if budget_s is None:
            return float("inf")
        return float(budget_s) - QUESTION_MARGIN_S - (time.monotonic() - started)

    try:
        if not getattr(getattr(platform, "config", None), "notice_clients", True):
            return None
        engine = getattr(platform, "engine", None)
        key = _key(folder)
        if engine is None or not key:
            return None
        # The lane waits 1 s at most: a question picked after it gave up
        # would be marked asked and never shown. A busy pass = no question.
        if not NOTICE_LOCK.acquire(timeout=QUESTION_LOCK_S):
            return None
        try:
            with session_scope(engine) as db:
                rows = list(db.exec(
                    select(WorkClient).where(
                        WorkClient.status == CLIENT_PROPOSED,
                        WorkClient.asked_at == None,  # noqa: E711
                    )
                ))
                hits = [r for r in rows if any(keys.inside(key, h["key"]) for h in homes_of(r))]
                if not hits:
                    return None
                hits.sort(key=lambda r: (int(r.strength or 0), _iso(r.created_at) or ""), reverse=True)
                row = hits[0]
                raw = None
                if budget_s is not None:
                    left = _left()
                    if left <= 0:
                        return None
                    # The mark-asked write waits on SQLite only for what is
                    # left of the lane's wait. Set on the DBAPI connection
                    # itself, so it is put back however the write ends.
                    raw = db.connection().connection.dbapi_connection
                    raw.execute(f"PRAGMA busy_timeout={max(1, int(left * 1000))}")
                try:
                    row.asked_at = utcnow()
                    db.add(row)
                    db.flush()
                    if _left() <= 0:
                        db.rollback()
                        return None
                    db.commit()
                finally:
                    if raw is not None:
                        raw.execute(f"PRAGMA busy_timeout={int(_BUSY_MS)}")
                ev = evidence_of(row)
                return {
                    "id": row.id,
                    "name": row.name,
                    "folders": int(ev.get("folders") or len(homes_of(row)) or 0),
                    "work": int(ev.get("work") or 0),
                }
        finally:
            NOTICE_LOCK.release()
    except Exception:  # noqa: BLE001 — a question is extra; never breaks a turn
        log.debug("clients: the question under a reply could not be picked", exc_info=True)
        return None


#: A question's answer as the dashboard keeps it on the line.
_ANSWER_STATE = {CLIENT_CONFIRMED: "yes", CLIENT_DECLINED: "no"}


def answer_states(engine: Any, ids: Sequence[str]) -> dict[str, str]:
    """``{id: "yes" | "no" | "gone"}`` for client questions asked under
    replies: confirmed → yes, Not a client → no, not on record → gone. A name
    still waiting has no entry (its question stays open). One indexed read.
    Never raises (``{}``: the lines show as they were saved)."""
    wanted = sorted({str(i) for i in ids or () if CLIENT_ID_RE.fullmatch(str(i or ""))})
    if engine is None or not wanted:
        return {}
    try:
        with session_scope(engine) as db:
            got = {str(cid): str(status) for cid, status in db.exec(
                select(WorkClient.id, WorkClient.status).where(WorkClient.id.in_(wanted))  # type: ignore[attr-defined]
            )}
    except Exception:  # noqa: BLE001 — the lines show as saved
        log.debug("clients: the answers of saved questions could not be read", exc_info=True)
        return {}
    out: dict[str, str] = {}
    for cid in wanted:
        status = got.get(cid)
        if status is None:
            out[cid] = "gone"
        elif status in _ANSWER_STATE:
            out[cid] = _ANSWER_STATE[status]
    return out


def fold_question_states(engine: Any, messages: Any) -> Any:
    """A coding session's lines with each reply's ``clientQuestion`` carrying
    its CURRENT answer (:func:`answer_states`), so a reopened session never
    asks again (the daemon writes those lines; the chat page keeps its own).
    The mirror of ``learning.preferences.fold_suggestion_states``. Never
    raises (the lines come back unchanged)."""
    if not isinstance(messages, list):
        return messages
    ids = [
        str(m["clientQuestion"].get("id") or "")
        for m in messages
        if isinstance(m, dict) and isinstance(m.get("clientQuestion"), dict)
    ]
    states = answer_states(engine, ids) if ids else {}
    if not states:
        return messages
    out = []
    for m in messages:
        q = m.get("clientQuestion") if isinstance(m, dict) else None
        state = states.get(str(q.get("id") or "")) if isinstance(q, dict) else None
        out.append({**m, "clientQuestion": {**q, "state": state}} if state else m)
    return out


# --------------------------------------------------------------------------- #
# events and the pass after an answer
# --------------------------------------------------------------------------- #
async def _awaited(awaitable: Any) -> Any:
    return await awaitable


def publish(platform: Any, etype: str, payload: Mapping[str, Any]) -> None:
    """Publish one client event (IDS ONLY) from wherever a change runs: the
    daemon loop, a route's worker thread (handed to the loop the noticing
    loop runs on), or nowhere (a test, the CLI: delivered here). Never
    raises."""
    bus = getattr(platform, "event_bus", None)
    if bus is None:
        return
    try:
        coro = bus.publish(etype, dict(payload))
    except Exception:  # noqa: BLE001 — an event is a courtesy; the change landed
        log.debug("clients: %s could not be published", etype, exc_info=True)
        return
    if not inspect.isawaitable(coro):
        return
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    try:
        if running is not None:
            task = running.create_task(_awaited(coro))
            _TASKS.add(task)
            task.add_done_callback(_TASKS.discard)
            return
        loop = _LOOP.get("loop")
        if loop is not None and loop.is_running() and not loop.is_closed():
            asyncio.run_coroutine_threadsafe(_awaited(coro), loop)
            return
        asyncio.run(_awaited(coro))
    except Exception:  # noqa: BLE001
        log.debug("clients: %s could not be published", etype, exc_info=True)
        try:
            close = getattr(coro, "close", None)
            if close is not None:
                close()
        except Exception:  # noqa: BLE001
            pass


def _quiet_notice(platform: Any) -> None:
    try:
        from .detect import notice

        notice(platform)
    except Exception:  # noqa: BLE001 — the next pass tries again
        log.debug("clients: the pass after an answer failed", exc_info=True)


def _after_answer(platform: Any) -> bool:
    """Start a noticing pass so the next batch is ready: handed to the
    daemon loop (``asyncio.to_thread`` there), never run in the request.
    Nothing when no noticing loop runs (a test, the CLI). Never raises."""
    loop = _LOOP.get("loop")
    try:
        if loop is None or not loop.is_running() or loop.is_closed():
            return False

        def _kick() -> None:
            try:
                task = asyncio.ensure_future(asyncio.to_thread(_quiet_notice, platform))
                _TASKS.add(task)
                task.add_done_callback(_TASKS.discard)
            except Exception:  # noqa: BLE001
                pass

        loop.call_soon_threadsafe(_kick)
        return True
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- #
# helpers for the changes
# --------------------------------------------------------------------------- #
@contextmanager
def _writing(platform: Any) -> Iterator[Any]:
    """One change: the write lock and one session. A database error is
    ``ClientsUnreadable`` (nothing was committed); a ``ClientError`` passes
    through."""
    engine = getattr(platform, "engine", None)
    if engine is None:
        raise ClientsUnreadable()
    with WRITE_LOCK:
        try:
            with session_scope(engine) as db:
                yield db
        except ClientError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.debug("clients: a change could not be written", exc_info=True)
            raise ClientsUnreadable() from exc


def _get(db: Any, client_id: str) -> WorkClient:
    cid = str(client_id or "")
    row = db.get(WorkClient, cid) if CLIENT_ID_RE.match(cid) else None
    if row is None:
        raise ClientNotFound()
    return row


def _ref(row: Any) -> dict[str, str]:
    return {"id": row.id, "name": row.name}


def _done(row: Any, note: str = "", **extra: Any) -> dict[str, Any]:
    return {"ok": True, "client": _ref(row), "note": " ".join(str(note or "").split()), **extra}


def clean_name(name: object) -> str:
    """A client's name as kept: 1–80 chars, scanned, naming SOMEONE (not
    only dates or work words). Raises ``ClientRefused``."""
    from ...core.promptguard import scan_context

    text = " ".join(str(name or "").split())
    if not text:
        raise ClientRefused("Say the client's name.")
    if len(text) > NAME_CHARS:
        raise ClientRefused(f"That name is {len(text)} characters. Keep it to {NAME_CHARS} or fewer.")
    if scan_context(text, source="client name", cap=None).blocked:
        raise ClientRefused(
            "That name reads like an instruction planted for the assistant, so it was not kept. "
            "Say it in your own words."
        )
    if not names.signature(text):
        raise ClientRefused(f"{text} names nobody: it is only dates or work words. Say the client's name.")
    return text


def _by_signature(db: Any, sig: str, *, exclude: str = "") -> WorkClient | None:
    """The row whose signature or one of whose aliases is ``sig``."""
    if not sig:
        return None
    row = db.exec(select(WorkClient).where(WorkClient.signature == sig)).first()
    if row is not None and row.id != exclude:
        return row
    for other in db.exec(select(WorkClient).where(WorkClient.aliases_json.contains(sig))):  # type: ignore[attr-defined]
        if other.id != exclude and sig in aliases_of(other):
            return other
    return None


def _confirmed_names(db: Any) -> dict[str, str]:
    return {
        str(cid): str(name)
        for cid, name in db.exec(
            select(WorkClient.id, WorkClient.name).where(WorkClient.status == CLIENT_CONFIRMED)
        )
    }


def _refusal(sentence: str, kind: str, other: str = "") -> ClientRefused:
    err = ClientRefused(sentence)
    err.kind = kind  # type: ignore[attr-defined]
    err.other = other  # type: ignore[attr-defined]
    return err


def check_folder(db: Any, folder: object, *, client_id: str, name: str) -> tuple[str, str, WorkClientFolder | None]:
    """The folder rules. ``(key, folder as spelled, a Not-a-client client's
    row to take over or None)``; raises ``ClientRefused`` with the one
    sentence that says which rule (``.kind``: top | many | taken | inside |
    holds | own)."""
    shown = str(folder or "").strip().strip('"').strip()
    pos = names.possessive(name) or "the client's"
    if not shown:
        raise _refusal("Say which folder.", "top")
    home = keys.home_folder(shown)
    key = keys.folder_key(home)
    if not key or not _FORGETTABLE.fullmatch(key):
        raise _refusal(
            f"{shown} is the top of a drive or a share, or not a whole folder. "
            f"Pick a folder that is only {pos}.", "top")
    clients = _confirmed_names(db)
    takeover = None
    for row in db.exec(select(WorkClientFolder)):
        rk = str(row.folder_key or "")
        if row.client_id not in clients:  # a Not-a-client client's row claims nothing
            if rk == key:
                takeover = row
            continue
        if row.client_id == client_id:
            if rk == key:
                raise _refusal(f"That folder is {pos} already.", "own")
            if keys.inside(key, rk):
                raise _refusal(f"That folder is inside {pos} folder {row.folder} already.", "own")
            continue
        other = clients[row.client_id]
        if rk == key:
            raise _refusal(
                f"That folder is {names.possessive(other)} already. A folder belongs to one client.",
                "taken", other)
        if keys.inside(key, rk):
            raise _refusal(
                f"{shown} is inside {names.possessive(other)} folder. Pick a folder that is only {pos}.",
                "inside", other)
        if keys.inside(rk, key):
            raise _refusal(
                f"{shown} holds {names.possessive(other)} folder. Pick {pos} own folder.", "holds", other)
    own = keys.folder_name(home)
    ancestors = names.path_parts(home)[:-1]
    if names.is_client_parent(own) or (
        not names.signature(own) and not any(names.is_name_segment(s, under_clients=True) for s in ancestors)
    ):
        raise _refusal(f"{own} holds work for many people. Pick a folder that is only {pos}.", "many")
    return key, shown, takeover


#: The evidence key where a client row remembers the folder rows it took
#: over from a Not-a-client client, so :func:`unconfirm` hands them back.
_TOOK_OVER = "took_over"


def _remember_takeover(row: WorkClient, takeover: Any) -> None:
    """Note on ``row`` whose folder row it is taking over (the independent
    review): an Undo of this Yes gives the row back, never deletes it."""
    ev = evidence_of(row)
    took = [t for t in (ev.get(_TOOK_OVER) or []) if isinstance(t, dict)]
    took.append({"key": str(takeover.folder_key), "from": str(takeover.client_id),
                 "folder": str(takeover.folder or ""), "how": str(takeover.how or "")})
    ev[_TOOK_OVER] = took[-50:]
    row.evidence_json = json.dumps(ev, ensure_ascii=True)


def _link(db: Any, row: WorkClient, key: str, shown: str, how: str, takeover: Any) -> None:
    if takeover is not None:
        if takeover.client_id != row.id:
            _remember_takeover(row, takeover)
            db.add(row)
        takeover.client_id = row.id
        takeover.folder = shown
        takeover.how = how
        takeover.created_at = utcnow()
        db.add(takeover)
    else:
        db.add(WorkClientFolder(folder_key=key, client_id=row.id, folder=shown, how=how))
    db.flush()


def _withdraw_inside(db: Any, folder_keys: Sequence[str], *, keep: str = "") -> int:
    """Proposed rows never answered with a home inside these folders: gone
    (the folders are a client's now)."""
    wanted = [k for k in folder_keys if k]
    if not wanted:
        return 0
    n = 0
    for row in db.exec(select(WorkClient).where(WorkClient.status == CLIENT_PROPOSED)):
        if row.id == keep or row.decided_at is not None:
            continue
        if any(keys.inside(h["key"], k) for h in homes_of(row) for k in wanted):
            db.delete(row)
            n += 1
    return n


def _skip_note(folder: str, err: ClientRefused) -> str:
    kind = getattr(err, "kind", "")
    other = getattr(err, "other", "")
    if kind == "own":
        return ""
    if kind == "taken" and other:
        return f"{folder} is {names.possessive(other)} already, so it was not added."
    if kind in ("inside", "holds") and other:
        return f"{folder} overlaps {names.possessive(other)} folder, so it was not added."
    return f"{folder} was not added: {err.sentence}"


def _link_homes(db: Any, row: WorkClient) -> tuple[list[str], list[str]]:
    """Each evidence home becomes a ``found`` folder; one another client owns
    (or that nests with one) is skipped and named. ``(linked keys, notes)``."""
    linked: list[str] = []
    notes: list[str] = []
    for h in homes_of(row):
        try:
            key, shown, takeover = check_folder(db, h["folder"], client_id=row.id, name=row.name)
        except ClientRefused as err:
            note = _skip_note(h["folder"], err)
            if note:
                notes.append(note)
            continue
        _link(db, row, key, shown, CLIENT_FOLDER_FOUND, takeover)
        linked.append(key)
    return linked, notes


def _no_folder_note(row: WorkClient) -> str:
    return f"{row.name} has no folder yet, so no chat is told about it. Add its folder on its page."


def _event(platform: Any, name: str, payload: Mapping[str, Any]) -> None:
    from ...core.events import EventType

    publish(platform, getattr(EventType, name), payload)


# --------------------------------------------------------------------------- #
# Changes. Each returns a view dict; each raises ClientNotFound (404),
# ClientConflict (409), ClientRefused (400) or ClientsUnreadable (503).
# --------------------------------------------------------------------------- #
def _confirm_one(platform: Any, client_id: str, *, name: str | None = None) -> dict[str, Any]:
    wanted = clean_name(name) if name is not None and str(name).strip() else None
    with _writing(platform) as db:
        row = _get(db, client_id)
        if row.status != CLIENT_PROPOSED:
            raise ClientConflict(f"{row.name} was already answered.")
        if wanted and wanted != row.name:
            sig = names.signature(wanted)
            if sig != row.signature:
                other = _by_signature(db, sig, exclude=row.id)
                if other is not None:
                    raise _refusal(f"{other.name} is already on record with that name.", "name_taken", other.name)
                row.aliases_json = json.dumps(_merge_aliases([row.signature, *aliases_of(row)], sig))
                row.signature = sig
            row.name = wanted
            row.name_key = names.name_key(wanted)
        now = utcnow()
        row.status = CLIENT_CONFIRMED
        row.decided_at = now
        row.confirmed_at = row.confirmed_at or now
        row.updated_at = now
        db.add(row)
        db.flush()
        # Other spellings seen become aliases (one signature per name, so
        # usually none).
        spelled = {names.signature(s) for s in evidence_of(row).get("spellings") or [] if isinstance(s, str)}
        extra = sorted(s for s in spelled if s and s != row.signature and _by_signature(db, s, exclude=row.id) is None)
        if extra:
            row.aliases_json = json.dumps(_merge_aliases(list(aliases_of(row)), *extra))
            db.add(row)
        linked, notes = _link_homes(db, row)
        _withdraw_inside(db, linked, keep=row.id)
        if not linked:
            notes.insert(0, _no_folder_note(row))
        db.commit()
        out = _done(row, " ".join(notes))
    bump_version()
    _event(platform, "CLIENT_CONFIRMED", {"id": out["client"]["id"]})
    return out


def confirm(platform: Any, client_id: str, *, name: str | None = None) -> dict[str, Any]:
    """``proposed → confirmed`` (``POST /clients/{id}/confirm``; the page may
    send ``name``). Sets ``confirmed_at`` and ``decided_at``; each home
    becomes a ``found`` folder row (a home another client owns, or nested
    with one, is skipped and named in ``note``); other spellings become
    aliases; proposed rows now inside its folders are withdrawn. Publishes
    ``client.confirmed {id}``. Returns ``{ok, client: {id, name}, note}``."""
    out = _confirm_one(platform, client_id, name=name)
    _after_answer(platform)
    return out


def decline(platform: Any, client_id: str, *, expect: str | None = None) -> dict[str, Any]:
    """``proposed`` or ``confirmed`` → ``declined`` (Not a client). Folders
    and preferences are kept but claim and reach nothing; the signature and
    aliases are never asked about again. Publishes ``client.declined
    {id}``. Returns ``{ok, client: {id, name}, note}``.

    ``expect`` (the independent review): the status the press was drawn for.
    A one-press No under a reply, in the review or the inbox expects a
    waiting name, so a stale one never takes a confirmed client away
    (409, "already answered"); only the client page's two-press footer
    expects ``confirmed``. None (chat's own tool, which names the client and
    shows a card) declines either."""
    with _writing(platform) as db:
        row = _get(db, client_id)
        if row.status == CLIENT_DECLINED or (expect is not None and row.status != expect):
            raise ClientConflict(f"{row.name} was already answered.")
        was = row.status
        now = utcnow()
        row.status = CLIENT_DECLINED
        row.decided_at = now
        row.updated_at = now
        db.add(row)
        db.commit()
        if was == CLIENT_CONFIRMED:
            note = (f"Chats in {names.possessive(row.name)} folders stop being told about {row.name}. "
                    "Its folders and preferences are kept, unused. You can make it a client again.")
        else:
            note = f"Iron Jarvis won't ask about {row.name} again."
        out = _done(row, note)
    bump_version()
    _event(platform, "CLIENT_DECLINED", {"id": out["client"]["id"]})
    _after_answer(platform)
    return out


def confirm_many(platform: Any, ids: Sequence[str]) -> dict[str, Any]:
    """The bulk review (``POST /clients/confirm-many``, ≤ 30 ids): each row
    re-read and confirmed only if still proposed. Returns ``{confirmed:
    [{id, name}], skipped: [{id, why}]}``."""
    if isinstance(ids, str) or not isinstance(ids, (list, tuple)):
        raise ClientRefused("Say which names are clients.")
    wanted = list(dict.fromkeys(str(i or "") for i in ids))
    if not wanted:
        raise ClientRefused("Say which names are clients.")
    if len(wanted) > MAX_WAITING:
        raise ClientRefused(f"Answer at most {MAX_WAITING} names at once.")
    confirmed: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    for cid in wanted:
        try:
            got = _confirm_one(platform, cid)
        except ClientsUnreadable:
            if not confirmed:
                raise
            skipped.append({"id": cid, "why": ClientsUnreadable.default})
            continue
        except ClientError as err:
            skipped.append({"id": cid, "why": err.sentence})
            continue
        confirmed.append(got["client"])
    if confirmed:
        _after_answer(platform)
    return {"confirmed": confirmed, "skipped": skipped}


def ask_again(platform: Any, client_id: str) -> dict[str, Any]:
    """A declined row that was NEVER confirmed goes back to ``proposed`` with
    ``asked_at`` NULL, even over ``MAX_WAITING``. A once-confirmed one is
    refused (that is :func:`restore`)."""
    with _writing(platform) as db:
        row = _get(db, client_id)
        if row.status == CLIENT_PROPOSED:
            raise ClientConflict(f"{row.name} is waiting for an answer already.")
        if row.status == CLIENT_CONFIRMED:
            raise ClientConflict(f"{row.name} is a client already.")
        if row.confirmed_at is not None:
            raise ClientConflict(f"{row.name} was a client before. Make it a client again instead.")
        row.status = CLIENT_PROPOSED
        row.asked_at = None
        row.updated_at = utcnow()
        db.add(row)
        db.commit()
        out = _done(row, f"{row.name} waits for your answer again.")
    bump_version()
    _event(platform, "CLIENT_SUGGESTED", {"ids": [out["client"]["id"]]})
    return out


def _restore_folders(db: Any, row: WorkClient) -> tuple[list[str], list[str]]:
    """A returning client's own folder rows that are still free claim again;
    one that now nests with another client's folder is dropped and named."""
    clients = _confirmed_names(db)
    others = [
        (str(f.folder_key), clients[f.client_id])
        for f in db.exec(select(WorkClientFolder))
        if f.client_id in clients and f.client_id != row.id
    ]
    kept: list[str] = []
    notes: list[str] = []
    for f in list(db.exec(select(WorkClientFolder).where(WorkClientFolder.client_id == row.id))):
        clash = next((who for k, who in others if keys.inside(f.folder_key, k) or keys.inside(k, f.folder_key)), "")
        if clash:
            notes.append(f"{f.folder} overlaps {names.possessive(clash)} folder now, so it is no longer "
                         f"{names.possessive(row.name)}.")
            db.delete(f)
            continue
        kept.append(str(f.folder_key))
    return kept, notes


def restore(platform: Any, client_id: str) -> dict[str, Any]:
    """Make it a client again: a declined, once-confirmed client becomes
    ``confirmed``, its folders restored where they are still free (the rest
    named in ``note``). Publishes ``client.changed {id}``."""
    with _writing(platform) as db:
        row = _get(db, client_id)
        if row.status != CLIENT_DECLINED:
            raise ClientConflict(
                f"{row.name} is a client already." if row.status == CLIENT_CONFIRMED
                else f"{row.name} is waiting for an answer already.")
        if row.confirmed_at is None:
            raise ClientConflict(f"{row.name} was never a client. Use Ask again instead.")
        kept, notes = _restore_folders(db, row)
        now = utcnow()
        row.status = CLIENT_CONFIRMED
        row.decided_at = now
        row.updated_at = now
        db.add(row)
        db.flush()
        _withdraw_inside(db, kept, keep=row.id)
        if not kept:
            notes.insert(0, _no_folder_note(row))
        db.commit()
        out = _done(row, " ".join(notes))
    bump_version()
    _event(platform, "CLIENT_CHANGED", {"id": out["client"]["id"]})
    return out


#: Where :func:`unconfirm` may put a client back to.
_UNCONFIRM_TO = (CLIENT_PROPOSED, CLIENT_DECLINED, "gone")


def unconfirm(platform: Any, client_id: str, *, back_to: str) -> dict[str, Any]:
    """Undo ONE Yes made from chat (builder C's cards; never a route):
    ``back_to='proposed'`` (a waiting name answered Yes waits again, its
    ``asked_at`` kept), ``'declined'`` (a never-confirmed No answered Yes
    after all is Not a client again) or ``'gone'`` (the row :func:`create`
    made for a name not on record is deleted). The folder rows that Yes
    linked are deleted and ``confirmed_at`` is cleared (it was this Yes's).

    Refused with ``ClientConflict`` when the client is not confirmed, was a
    client before this Yes (``confirmed_at`` is not the Yes's own
    ``decided_at``), holds a kept preference, or has more folders added than
    that Yes made (none for ``proposed``; the one :func:`create` linked
    otherwise). The card's Undo puts back the change's later steps first
    (a preference it kept, a folder it added), so a clean Undo reaches here
    with only what the Yes made. Proposed rows that Yes withdrew come back on
    the next noticing pass. Publishes ``client.changed {id}``. Returns
    ``{ok, client: {id, name}, note}``."""
    if back_to not in _UNCONFIRM_TO:
        raise ClientRefused("That change cannot be undone.")
    with _writing(platform) as db:
        row = _get(db, client_id)
        name = row.name
        if row.status != CLIENT_CONFIRMED:
            raise ClientConflict(f"{name} is not a client now, so there is no Yes to undo.")
        if row.confirmed_at is None or row.confirmed_at != row.decided_at:
            raise ClientConflict(
                f"{name} was a client before this change, so Undo would undo more than it did. "
                "Mark it Not a client on its page instead.")
        P = WorkClientPreference
        if db.exec(select(P.id).where(P.client_id == row.id)).first() is not None:
            raise ClientConflict(
                f"A preference was kept for {name} since, so Undo would lose it. "
                "Mark it Not a client on its page instead.")
        folders = list(db.exec(select(WorkClientFolder).where(WorkClientFolder.client_id == row.id)))
        added = [f for f in folders if f.how == CLIENT_FOLDER_ADDED]
        if len(added) > (0 if back_to == CLIENT_PROPOSED else 1):
            raise ClientConflict(
                f"A folder was added to {name} since, so Undo would take it away too. "
                "Change it on its page instead.")
        given_back = {str(t.get("key")): t for t in (evidence_of(row).get(_TOOK_OVER) or [])
                      if isinstance(t, dict) and t.get("key") and t.get("from")}
        for f in folders:
            back = given_back.get(str(f.folder_key))
            prev = db.get(WorkClient, str(back["from"])) if back else None
            if prev is not None and prev.id != row.id:
                # A folder this Yes took over from a Not-a-client client goes
                # back to it, as it was ("kept, unused").
                f.client_id = prev.id
                f.folder = str(back.get("folder") or f.folder)
                f.how = str(back.get("how") or f.how)
                db.add(f)
                continue
            db.delete(f)
        if back_to != "gone" and given_back:
            ev = evidence_of(row)
            ev.pop(_TOOK_OVER, None)
            row.evidence_json = json.dumps(ev, ensure_ascii=True)
        if back_to == "gone":
            db.delete(row)
            note = f"{name} is no longer on record."
        else:
            now = utcnow()
            row.status = back_to
            row.confirmed_at = None
            if back_to == CLIENT_DECLINED:
                row.decided_at = now
            row.updated_at = now
            db.add(row)
            note = (f"{name} waits for your answer again." if back_to == CLIENT_PROPOSED
                    else f"{name} is marked as not a client again.")
        db.commit()
        out = {"ok": True, "client": {"id": str(client_id), "name": name}, "note": note}
    bump_version()
    _event(platform, "CLIENT_CHANGED", {"id": out["client"]["id"]})
    _after_answer(platform)
    return out


def create(platform: Any, *, folder: str = "", name: str = "", local_only: bool = False) -> dict[str, Any]:
    """An owner-made client (``POST /clients {folder, name?}``, "Make this a
    client", chat's ``client_answer``): ``how = added``, confirmed at once.
    With no name, the folder's own name (``names.display_name``). Reuses a
    proposed or declined row with the same signature (no twin; the owner's
    explicit action overrides an earlier No). Folder rules apply. A client
    with no folder reaches no chat until it has one. Publishes
    ``client.confirmed {id}``. Returns ``{ok, client: {id, name}, note}``.
    ``local_only``: made in a Local only chat (recorded as ``made_local``,
    so a cloud turn is not told its name)."""
    shown = str(folder or "").strip().strip('"').strip()
    given = " ".join(str(name or "").split())
    if not shown and not given:
        raise ClientRefused("Say the client's name or its folder.")
    if not given:
        own = keys.folder_name(keys.home_folder(shown))
        given = names.display_name(own)
        if not given:
            raise ClientRefused(f"{own or shown} does not look like a name. Say the client's name too.")
    clean = clean_name(given)
    sig = names.signature(clean)
    event = "CLIENT_CONFIRMED"
    with _writing(platform) as db:
        row = _by_signature(db, sig)
        if row is not None and row.status == CLIENT_CONFIRMED:
            if not shown:
                raise ClientConflict(f"{row.name} is a client already.")
            key, spelled, takeover = check_folder(db, shown, client_id=row.id, name=row.name)
            _link(db, row, key, spelled, CLIENT_FOLDER_ADDED, takeover)
            _withdraw_inside(db, [key], keep=row.id)
            row.updated_at = utcnow()
            db.add(row)
            db.commit()
            out = _done(row, f"{spelled} is one of {names.possessive(row.name)} folders now.")
            event = "CLIENT_CHANGED"
        else:
            target = row.id if row is not None else ""
            checked = check_folder(db, shown, client_id=target, name=clean) if shown else None
            now = utcnow()
            if row is None:
                homes = []
                if checked is not None:
                    homes = [{"folder": checked[1], "key": checked[0], "work": 0, "last_at": None,
                              "local": bool(local_only)}]
                made = {"rule": "added", "homes": homes, "work": 0, "folders": len(homes)}
                if local_only:
                    made["made_local"] = True
                row = WorkClient(
                    id=new_id("client"), name=clean, name_key=names.name_key(clean), signature=sig,
                    status=CLIENT_CONFIRMED, how=CLIENT_ADDED,
                    evidence_json=json.dumps(made, ensure_ascii=True),
                    confirmed_at=now, decided_at=now,
                )
                db.add(row)
                db.flush()
                was, was_confirmed = None, False
            else:
                was, was_confirmed = row.status, row.confirmed_at is not None
                if name and clean != row.name:
                    row.name = clean
                    row.name_key = names.name_key(clean)
                row.status = CLIENT_CONFIRMED
                row.decided_at = now
                row.confirmed_at = row.confirmed_at or now
                row.updated_at = now
                db.add(row)
                db.flush()
            linked: list[str] = []
            notes: list[str] = []
            if checked is not None:
                _link(db, row, checked[0], checked[1], CLIENT_FOLDER_ADDED, checked[2])
                linked.append(checked[0])
            if was == CLIENT_PROPOSED:
                more, notes = _link_homes(db, row)
                linked += more
            elif was == CLIENT_DECLINED and was_confirmed:
                more, notes = _restore_folders(db, row)
                linked += more
            _withdraw_inside(db, linked, keep=row.id)
            if not linked:
                notes.insert(0, _no_folder_note(row))
            db.commit()
            out = _done(row, " ".join(notes))
    bump_version()
    _event(platform, event, {"id": out["client"]["id"]})
    return out


def _merge_aliases(current: Sequence[str], *more: str) -> list[str]:
    out = [a for a in dict.fromkeys([*current, *more]) if a]
    return out[-MAX_ALIASES:]


def rename(platform: Any, client_id: str, name: str) -> dict[str, Any]:
    """``PATCH /clients/{id} {name}``: 1–80 chars, promptguard-scanned; the
    old signature becomes an alias. Refused when another row already has the
    new signature. Publishes ``client.changed {id}``."""
    clean = clean_name(name)
    sig = names.signature(clean)
    with _writing(platform) as db:
        row = _get(db, client_id)
        if sig != row.signature:
            other = _by_signature(db, sig, exclude=row.id)
            if other is not None:
                raise _refusal(f"{other.name} is already on record with that name.", "name_taken", other.name)
            row.aliases_json = json.dumps(
                _merge_aliases([a for a in aliases_of(row) if a != sig], row.signature))
            _spell(row, {row.signature: row.name})
            row.signature = sig
        row.name = clean
        row.name_key = names.name_key(clean)
        row.updated_at = utcnow()
        db.add(row)
        db.commit()
        out = _done(row)
    bump_version()
    _event(platform, "CLIENT_CHANGED", {"id": out["client"]["id"]})
    return out


def add_folder(platform: Any, client_id: str, folder: str, *, how: str = CLIENT_FOLDER_ADDED) -> dict[str, Any]:
    """``POST /clients/{id}/folders {folder}``: an ``added`` folder row,
    under the folder rules (a free row of a Not-a-client client may be taken
    over). ``how`` is ``found`` only for an Undo that puts back a removed
    found folder. Publishes ``client.changed {id}``."""
    with _writing(platform) as db:
        row = _get(db, client_id)
        if row.status == CLIENT_DECLINED:
            raise ClientRefused(f"{row.name} is not a client now. Make it a client again first.")
        if row.status != CLIENT_CONFIRMED:
            raise ClientRefused(f"Say whether {row.name} is a client first.")
        key, shown, takeover = check_folder(db, folder, client_id=row.id, name=row.name)
        _link(db, row, key, shown, how if how in (CLIENT_FOLDER_ADDED, CLIENT_FOLDER_FOUND) else CLIENT_FOLDER_ADDED,
              takeover)
        _withdraw_inside(db, [key], keep=row.id)
        row.updated_at = utcnow()
        db.add(row)
        db.commit()
        out = _done(row, folder={"folder": shown, "name": keys.folder_name(shown)})
    bump_version()
    _event(platform, "CLIENT_CHANGED", {"id": out["client"]["id"]})
    return out


def remove_folder(platform: Any, client_id: str, folder: str) -> dict[str, Any]:
    """``POST /clients/{id}/folders/remove {folder}``: the folder row is
    deleted (its work stays in the record). Publishes ``client.changed
    {id}``. ``folder`` in the answer says what was removed (its ``how``
    too, so an Undo puts it back as it was)."""
    key = _key(folder)
    with _writing(platform) as db:
        row = _get(db, client_id)
        got = db.get(WorkClientFolder, key) if key else None
        if got is None or got.client_id != row.id:
            raise ClientRefused(f"That folder is not one of {names.possessive(row.name)} folders.")
        removed = {"folder": got.folder or got.folder_key, "name": keys.folder_name(got.folder or got.folder_key),
                   "how": got.how}
        db.delete(got)
        row.updated_at = utcnow()
        db.add(row)
        db.commit()
        out = _done(row, folder=removed)
    bump_version()
    _event(platform, "CLIENT_CHANGED", {"id": out["client"]["id"]})
    return out


#: The evidence key holding how the owner WROTE each alias (``{signature:
#: spelling}``): the page shows the spelling, never a bare signature.
_ALIAS_SPELLINGS = "alias_spellings"


def _spell(row: WorkClient, spellings: Mapping[str, str]) -> None:
    """Remember how each alias signature was written (the independent
    review). Kept to the signatures the row still has."""
    ev = evidence_of(row)
    known = {str(k): str(v) for k, v in (ev.get(_ALIAS_SPELLINGS) or {}).items()
             if isinstance(v, str)} if isinstance(ev.get(_ALIAS_SPELLINGS), dict) else {}
    known.update({str(k): " ".join(str(v).split())[:80] for k, v in spellings.items() if k and v})
    keep = set(aliases_of(row))
    ev[_ALIAS_SPELLINGS] = {k: v for k, v in known.items() if k in keep}
    row.evidence_json = json.dumps(ev, ensure_ascii=True)


def alias_spellings(row: Any) -> dict[str, str]:
    """``{signature: how the owner wrote it}`` for a row's aliases."""
    got = evidence_of(row).get(_ALIAS_SPELLINGS)
    return {str(k): str(v) for k, v in got.items() if isinstance(v, str)} if isinstance(got, dict) else {}


def set_aliases(platform: Any, client_id: str, aliases: Sequence[str]) -> dict[str, Any]:
    """The client's other spellings, at most 5, stored as signatures; one
    that another row already uses is refused. Publishes ``client.changed
    {id}``."""
    from ...core.promptguard import scan_context

    given = [aliases] if isinstance(aliases, str) else list(aliases or ())
    if len(given) > MAX_ALIASES:
        raise ClientRefused(f"Keep it to {MAX_ALIASES} other spellings or fewer.")
    with _writing(platform) as db:
        row = _get(db, client_id)
        sigs: list[str] = []
        spelled: dict[str, str] = {}
        for raw in given:
            text = " ".join(str(raw or "").split())
            if scan_context(text, source="client name", cap=None).blocked:
                raise ClientRefused(f"{text[:40]} reads like an instruction planted for the assistant, so it was not kept.")
            sig = names.signature(text)
            if not sig:
                raise ClientRefused(f"{text or 'That'} names nobody: it is only dates or work words.")
            if sig == row.signature or sig in sigs:
                continue
            other = _by_signature(db, sig, exclude=row.id)
            if other is not None:
                raise _refusal(f"{text} is how {other.name} is written. A name belongs to one client.",
                               "name_taken", other.name)
            sigs.append(sig)
            spelled[sig] = text
        row.aliases_json = json.dumps(sigs)
        _spell(row, spelled)
        row.updated_at = utcnow()
        db.add(row)
        db.commit()
        out = _done(row, aliases=list(sigs))
    bump_version()
    _event(platform, "CLIENT_CHANGED", {"id": out["client"]["id"]})
    return out


def _clean_preference(text: object) -> str:
    from ...learning.preferences import PreferenceInvalid, clean_text

    try:
        return clean_text(text)
    except PreferenceInvalid as exc:
        raise ClientRefused(str(exc)) from None


def _clean_evidence(items: Sequence[Mapping[str, Any]] | None) -> list[dict[str, str]]:
    out = []
    for item in list(items or ())[:3]:
        if not isinstance(item, Mapping):
            continue
        link = str(item.get("link") or "").strip()
        if not link.startswith("/") or link.startswith("//"):
            link = ""
        out.append({
            "quote": " ".join(str(item.get("quote") or "").split())[:QUOTE_CHARS],
            "thread_id": str(item.get("thread_id") or "").strip()[:80],
            "where": " ".join(str(item.get("where") or "").split())[:120],
            "at": str(item.get("at") or "").strip()[:40] or (_iso(utcnow()) or ""),
            "link": link[:300],
        })
    return out


def _pref(db: Any, row: WorkClient, pref_id: str) -> Any:
    pid = str(pref_id or "")
    got = db.get(WorkClientPreference, pid) if CLIENT_PREF_ID_RE.match(pid) else None
    if got is None or got.client_id != row.id or got.status != CLIENT_PREF_CONFIRMED:
        raise ClientNotFound("That preference is not on record.")
    return got


def _confirmed_client(db: Any, client_id: str) -> WorkClient:
    row = _get(db, client_id)
    if row.status != CLIENT_CONFIRMED:
        raise ClientRefused(f"{row.name} is not a client now, so nothing is kept for it.")
    return row


def keep_preference(
    platform: Any,
    client_id: str,
    text: str,
    *,
    origin: str,
    local_only: bool = False,
    evidence: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Keep one preference for a CONFIRMED client, with the owner's yes
    (``origin`` said: a chat card; typed: the page). ``text`` through
    ``learning.preferences.clean_text`` (refused with its sentence when
    flagged); ``evidence`` quotes the OWNER's own message (≤ 160), never the
    model's argument. Publishes ``client.changed {id}``. Returns ``{ok,
    preference: {id, text, origin, local_only, evidence, created_at}}``."""
    if origin not in (CLIENT_PREF_SAID, CLIENT_PREF_TYPED):
        raise ClientRefused("A preference is kept from a chat card or typed on the client's page.")
    clean = _clean_preference(text)
    with _writing(platform) as db:
        row = _confirmed_client(db, client_id)
        P = WorkClientPreference
        kept = list(db.exec(select(P).where(P.client_id == row.id, P.status == CLIENT_PREF_CONFIRMED)))
        if any((p.text or "").casefold() == clean.casefold() for p in kept):
            raise ClientRefused(f"That is kept for {row.name} already.")
        if len(kept) >= MAX_PREFERENCES:
            raise ClientRefused(f"{row.name} has {MAX_PREFERENCES} preferences. Forget one first.")
        now = utcnow()
        pref = P(
            id=new_id("cpref"), client_id=row.id, text=clean, status=CLIENT_PREF_CONFIRMED,
            origin=origin, local_only=bool(local_only),
            evidence_json=json.dumps(_clean_evidence(evidence), ensure_ascii=True),
            created_at=now, updated_at=now,
        )
        db.add(pref)
        row.updated_at = now
        db.add(row)
        db.commit()
        db.refresh(pref)
        view = _pref_view(pref)
        out = {"ok": True, "client": _ref(row), "preference": view}
    bump_version()
    _event(platform, "CLIENT_CHANGED", {"id": out["client"]["id"]})
    return out


def change_preference(
    platform: Any,
    client_id: str,
    pref_id: str,
    text: str,
    *,
    local_only: bool | None = None,
    origin: str | None = None,
    evidence: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """``PATCH /clients/{id}/preferences/{pid}``: new text, same checks as
    :func:`keep_preference`. Publishes ``client.changed {id}``. The answer
    also carries ``before`` (the text it had), so an Undo changes it back,
    and ``before_view`` (the whole row as it was).

    A change said in a chat (the independent review) passes ``local_only``
    (the row's flag OR'd with the turn's Local only), ``origin`` and
    ``evidence`` (the owner's own words for THIS change), so words said in a
    Local only chat are never told to a cloud turn; an Undo passes the old
    ones back. None leaves that field as it is (the page)."""
    clean = _clean_preference(text)
    if origin is not None and origin not in (CLIENT_PREF_SAID, CLIENT_PREF_TYPED):
        raise ClientRefused("A preference is kept from a chat card or typed on the client's page.")
    with _writing(platform) as db:
        row = _get(db, client_id)
        pref = _pref(db, row, pref_id)
        before = pref.text or ""
        before_view = _pref_view(pref)
        pref.text = clean
        if local_only is not None:
            pref.local_only = bool(local_only)
        if origin is not None:
            pref.origin = origin
        if evidence is not None:
            pref.evidence_json = json.dumps(_clean_evidence(evidence), ensure_ascii=True)
        pref.updated_at = utcnow()
        db.add(pref)
        db.commit()
        db.refresh(pref)
        out = {"ok": True, "client": _ref(row), "preference": _pref_view(pref), "before": before,
               "before_view": before_view}
    bump_version()
    _event(platform, "CLIENT_CHANGED", {"id": out["client"]["id"]})
    return out


def forget_preference(platform: Any, client_id: str, pref_id: str) -> dict[str, Any]:
    """``DELETE /clients/{id}/preferences/{pid}``: the row is deleted.
    Publishes ``client.changed {id}``. Returns the forgotten row's view, so
    an Undo can keep it again exactly."""
    with _writing(platform) as db:
        row = _get(db, client_id)
        pref = _pref(db, row, pref_id)
        view = _pref_view(pref)
        db.delete(pref)
        db.commit()
        out = {"ok": True, "client": _ref(row), "preference": view}
    bump_version()
    _event(platform, "CLIENT_CHANGED", {"id": out["client"]["id"]})
    return out


__all__ = [
    "EMPTY_MAP",
    "MAP_MAX_AGE_S",
    "NOTICE_LOCK",
    "ClientMap",
    "add_folder",
    "alias_spellings",
    "aliases_of",
    "answer_states",
    "ask_again",
    "bump_version",
    "change_preference",
    "check_folder",
    "clean_name",
    "confirm",
    "confirm_many",
    "create",
    "decline",
    "evidence_of",
    "fold_question_states",
    "folder_rows",
    "forget_preference",
    "homes_of",
    "keep_preference",
    "load_map",
    "preferences",
    "publish",
    "question_for_turn",
    "remember_loop",
    "remove_folder",
    "rename",
    "restore",
    "set_aliases",
    "unconfirm",
]
