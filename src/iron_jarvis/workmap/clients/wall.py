"""THE WALL (v1.499.0, builder B): the one rule for what each turn may be
told about clients' work.

It decides the work block (both parts), memory-recall passages and run
summaries, the history search tool and the client chat tools. It is stricter
than before the moment the update lands, before the owner answers anything:
folders under a Clients-type folder and waiting homes are walled too. If the
client tables cannot be read it fails CLOSED (``TurnWall.ok`` False: the work
block keeps to this exact folder, memory recall drops every chat passage).

The rule (``may_tell``) is written out in the package docstring
(``workmap/clients/__init__.py``, "The wall"). Text only: nothing here
touches the disk (pinned with the disk calls patched to raise).

One addition to the written rule, stricter only: a turn whose folder
STRICTLY HOLDS a row's owned folder (a waiting home, a folder under a
Clients-type folder, a confirmed client's folder) spans it, even when the
map did not say so (``ClientMap.spans`` knows only the folders it holds; the
row's own path proves the Clients-type folder below the turn's). A chat on
``Documents`` is never told the titles under ``Documents\\Clients\\Smith``.

The turn in flight is a ContextVar, :data:`CURRENT`, mirroring
``providers/egress.LOCAL_ONLY`` and ``egress.scoped_frames``: the chat lanes
set it around the whole turn (:func:`turn_scope` for the POST lane,
:func:`scoped_frames` next to ``egress.scoped_frames`` for the stream lane),
and the agent runtime around a run, next to its Local only scope. Unset, it
answers :data:`NO_CLIENT`, so a prompt caller added later gets the no-client
rule (fail safe for facts). ``asyncio.to_thread`` and ``create_task`` copy
the context; ``run_in_executor`` does NOT (wrap it in
``contextvars.copy_context().run``). The owner's own screens (the Memory
page's recall, the search palette) wrap their calls in :func:`owner_screen`
and are not walled: a reader asks :func:`on_owner_screen` FIRST, and
otherwise filters with ``may_tell(current(), …)`` (or
:func:`tell_passages` for a batch of chat and run passages).
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from typing import Any, AsyncIterator, Iterable, Iterator, Sequence

from .. import keys
from . import NO_CLIENT, ClientRoot, TurnWall, names, store
from .store import ClientMap

log = logging.getLogger(__name__)

#: The wall of the turn in flight in THIS context.
CURRENT: ContextVar[TurnWall] = ContextVar("iron_jarvis_client_wall", default=NO_CLIENT)
#: True inside :func:`owner_screen` (the owner's own screens: not walled).
OWNER_SCREEN: ContextVar[bool] = ContextVar("iron_jarvis_client_owner_screen", default=False)

#: The owner words / passage text read for names, at most (linear anyway).
TEXT_MAX = 4000
#: Ids one :func:`thread_owners` / :func:`session_owners` call reads, at most.
OWNERS_MAX = 1000
_IN_CHUNK = 400

#: Passage kinds :func:`tell_passages` knows: a chat thread id, an agent run
#: id; anything else has no folder of its own.
PASSAGE_CHAT = "chat"
PASSAGE_RUN = "session"

_CLIENT = "client:"
_WAITING = "waiting:"


def _key(folder: object) -> str:
    """The folder as the wall compares it (a coding session's own copy is
    its repository). Text only; "" for none."""
    try:
        return keys.folder_key(keys.home_folder(str(folder or "").strip()))
    except Exception:  # noqa: BLE001 — never raises
        return ""


def _owner_of_folder(cmap: ClientMap, folder: object) -> tuple[str | None, str, ClientRoot | None]:
    """``(owner, home key, root)`` of one folder: ``client:<id>`` inside a
    confirmed folder, else ``waiting:<id>`` inside a waiting home, else
    ``folder:<key>`` for the first name segment after a Clients-type segment,
    else None. ``home key`` is the owned folder's own key (the confirmed
    root, the waiting home, the provisional name folder)."""
    key = _key(folder)
    if not key:
        return None, "", None
    shown = keys.home_folder(str(folder or "").strip())
    root = cmap.client_of(shown)
    if root is not None:
        return _CLIENT + root.client_id, root.key or key, root
    waiting = cmap.waiting_of(shown)
    if waiting is not None:
        home = next((h for h in waiting.homes if keys.inside(key, h)), key)
        return _WAITING + waiting.client_id, home, None
    provisional = cmap.provisional_of(shown)
    if provisional:
        return str(provisional), str(provisional).split(":", 1)[-1], None
    return None, "", None


def _stems(files: Iterable[Any]) -> list[str]:
    out: list[str] = []
    for f in files or ():
        path = f.get("path") if isinstance(f, dict) else f
        name = keys.file_name(path)
        if name:
            out.append(name.rsplit(".", 1)[0] if "." in name else name)
    return out


#: File paths whose folders :func:`_path_owners` reads, at most.
FILES_MAX = 50


def parent_folder(path: object) -> str:
    """The folder a file sits in, by TEXT ("" for a bare name)."""
    raw = str(path or "").strip().rstrip("\\/")
    cut = max(raw.rfind("\\"), raw.rfind("/"))
    return raw[:cut] if cut > 0 else ""


def _segment_names(folder: object, cmap: ClientMap, table: Any = None) -> set[str]:
    """The CONFIRMED clients whose name (or alias) is one whole segment of
    ``folder``'s path, in any case (``D:\\Scans\\hartleys`` names Hartleys).
    ``names.path_parts`` already leaves out the drive and ``Users\\<name>``.
    ``table`` another names table (the waiting names, for an outside asker).
    Never raises."""
    try:
        sigs = {names.signature(p) for p in names.path_parts(folder)}
        sigs.discard("")
        if not sigs:
            return set()
        found: set[str] = set()
        for cid, entry in ((cmap.names if table is None else table) or {}).items():
            try:
                _name, sig, aliases = entry
            except (TypeError, ValueError):
                continue
            for want in (sig, *tuple(aliases or ())):
                if " ".join(str(want or "").split()) in sigs:
                    found.add(str(cid))
                    break
        return found
    except Exception:  # noqa: BLE001 — a path that cannot be read names nobody
        log.debug("clients: a path's segments could not be read", exc_info=True)
        return set()


def _path_owners(cmap: ClientMap, folder: object, files: Iterable[Any]) -> set[str]:
    """The owners an UNOWNED row's paths prove (the independent review): a
    confirmed client named by a whole segment of the row's folder, and the
    owner of each file's own folder (``_owner_of_folder``) or a client named
    by one of its segments. A Downloads chat whose files sit in
    ``Clients\\Hartleys`` is Hartleys' work. Text only."""
    out: set[str] = {_CLIENT + i for i in _segment_names(folder, cmap)}
    for n, f in enumerate(files or ()):
        if n >= FILES_MAX:
            break
        path = f.get("path") if isinstance(f, dict) else f
        parent = parent_folder(path)
        if not parent:
            continue
        owner, _home, _root = _owner_of_folder(cmap, parent)
        if owner is not None:
            out.add(owner)
        out.update(_CLIENT + i for i in _segment_names(parent, cmap))
    return out


def _named(text: object, cmap: ClientMap) -> frozenset[str]:
    """The CONFIRMED clients ``text`` names (ids). Never raises."""
    words = str(text or "")[:TEXT_MAX]
    if not words.strip():
        return frozenset()
    try:
        table = cmap.names
        got = names.named_in(words, table)
        return frozenset(i for i in got if i in table)
    except Exception:  # noqa: BLE001 — a name that cannot be read names nobody
        log.debug("clients: names could not be read", exc_info=True)
        return frozenset()


def _waiting_named(text: object, folder: object, cmap: ClientMap) -> frozenset[str]:
    """The WAITING names ``text`` or a whole segment of ``folder`` names, as
    owners (``waiting:<id>``). Only for an asker that is another app: the
    owner's own turns never use waiting names. Never raises (an error names
    an unknown owner: unsure, not told)."""
    try:
        table = cmap.waiting_names or {}
        if not table:
            return frozenset()
        words = str(text or "")[:TEXT_MAX]
        got = set(names.named_in(words, table)) if words.strip() else set()
        got |= _segment_names(folder, cmap, table)
        return frozenset(_WAITING + str(i) for i in got if i in table)
    except Exception:  # noqa: BLE001 — unsure: owned
        log.debug("clients: waiting names could not be read", exc_info=True)
        return frozenset({_WAITING + "?"})


def turn_and_map(engine: Any, folder: object, user_text: object) -> tuple[TurnWall, ClientMap]:
    """:func:`turn_wall` and the map it was read from, for a caller that
    then asks :func:`may_tell` about many rows (the work block). A map that
    cannot be read gives ``ok=False`` and :data:`store.EMPTY_MAP`. BLOCKING;
    never raises."""
    key = _key(folder)
    try:
        cmap = store.load_map(engine)
        if not isinstance(cmap, ClientMap):
            raise TypeError("not a client map")
    except Exception:  # noqa: BLE001 — ClientsUnreadable or anything: fail closed
        log.debug("clients: the map could not be read; the turn fails closed", exc_info=True)
        return TurnWall(ok=False, folder_key=key), store.EMPTY_MAP
    try:
        owner, _home, root = _owner_of_folder(cmap, folder)
        spans = bool(key) and bool(cmap.spans(keys.home_folder(str(folder or "").strip())))
        return (
            TurnWall(
                owner=owner,
                client=root,
                spans=spans,
                named=_named(user_text, cmap),
                ok=True,
                folder_key=key,
            ),
            cmap,
        )
    except Exception:  # noqa: BLE001
        log.debug("clients: the turn's wall could not be worked out", exc_info=True)
        return TurnWall(ok=False, folder_key=key), store.EMPTY_MAP


def outside_and_map(engine: Any) -> tuple[TurnWall, ClientMap]:
    """The wall of an asker that is ANOTHER APP (``work_search`` over the
    connector) and the map: no folder and no words (that app's model wrote
    both, never the owner), marked ``outside``, so :func:`may_tell` rule 2b
    tells it nothing that has an owner of any kind. A map that cannot be
    read answers no to everything (``ok=False`` with no folder). BLOCKING;
    never raises."""
    turn, cmap = turn_and_map(engine, "", "")
    if not turn.ok:
        return TurnWall(ok=False, folder_key="", outside=True), store.EMPTY_MAP
    return replace(turn, outside=True), cmap


def turn_wall(engine: Any, folder: object, user_text: object) -> TurnWall:
    """The wall for one turn: its folder's owner (``client:<id>`` /
    ``waiting:<id>`` / ``folder:<key>`` / None, after ``keys.home_folder`` +
    ``keys.folder_key``), ``client`` for a confirmed owner, ``spans``, and
    ``named`` = the confirmed clients ``names.named_in(user_text,
    cmap.names)`` finds in the OWNER's own words (``_compose_recall_query
    (body.messages)``; for a run, ``session.task``), never the model's. A map
    that cannot be read gives ``ok=False`` with the turn's ``folder_key``.
    BLOCKING (``store.load_map``, cached). NEVER RAISES."""
    return turn_and_map(engine, folder, user_text)[0]


def _row_owners(
    cmap: ClientMap, folder: object, title: object, files: Iterable[Any], snippet: object,
    waiting: bool,
) -> tuple[frozenset[str], str]:
    """``(owners, home key)`` of one row or passage, the way :func:`may_tell`
    reads them: its folder's owner, else NAMED ATTRIBUTION (the confirmed
    clients its title, file stems or snippet name, and the owners its paths
    prove); ``waiting`` also counts the WAITING names (an outside asker).
    RAISES on a fault (the callers fail closed)."""
    owner, home, _root = _owner_of_folder(cmap, folder)
    if owner is not None:
        return frozenset({owner}), home
    files = list(files or ())
    text = " \n ".join([str(title or ""), *_stems(files), str(snippet or "")])
    owners = {_CLIENT + i for i in _named(text, cmap)} | _path_owners(cmap, folder, files)
    if waiting:
        owners |= _waiting_named(text, folder, cmap)
    return frozenset(owners), home


def owners_of(
    cmap: ClientMap,
    *,
    folder: object,
    title: str = "",
    files: Iterable[Any] = (),
    snippet: str = "",
    waiting: bool = False,
) -> frozenset[str] | None:
    """The owners of one row (``client:<id>`` / ``waiting:<id>`` /
    ``folder:<key>``), exactly as :func:`may_tell` reads them; ``waiting``
    also counts the waiting names. For a reader that must know WHOSE a row is
    after the wall let it through (v1.547.0, ``workmap/lookup``: a named
    client's recap lines, the phone's stricter rule). None when they cannot
    be worked out (the caller treats the row as owned). Text only; never
    raises."""
    try:
        cmap = cmap if isinstance(cmap, ClientMap) else store.EMPTY_MAP
        return _row_owners(cmap, folder, title, files, snippet, bool(waiting))[0]
    except Exception:  # noqa: BLE001 — unsure: owned by someone it cannot name
        log.debug("clients: a row's owners could not be read", exc_info=True)
        return None


def may_tell(
    turn: TurnWall,
    cmap: ClientMap,
    *,
    folder: object,
    title: str = "",
    files: Iterable[str] = (),
    snippet: str = "",
) -> bool:
    """May ``turn`` be told about a row or passage whose folder is
    ``folder``? The rule, in this order (``title``, ``files`` stems and
    ``snippet`` feed NAMED ATTRIBUTION when the folder has no owner):

    1. ``not turn.ok``: only rows in exactly the turn's folder.
    2. The row owner is None: True.
    2b. ``turn.outside`` (another app asks): False. Its named attribution
       also knows the WAITING names (title, file stems, snippet, segments).
    3. ``turn.owner`` is set: True only if the row owner is exactly
       ``turn.owner`` (a named set must equal ``{turn.owner}``).
    4. The row owner is confirmed: True only if it is in ``turn.named``
       (every confirmed owner, even beside owners of another kind).
    5. ``turn.spans`` (or the turn's folder strictly holds the row's owned
       folder): False.
    6. Otherwise True.

    Pure and text only. NEVER RAISES (an error is False)."""
    try:
        wall = turn if isinstance(turn, TurnWall) else NO_CLIENT
        if not wall.ok:  # 1. fail closed: exactly this folder
            return bool(wall.folder_key) and _key(folder) == wall.folder_key
        cmap = cmap if isinstance(cmap, ClientMap) else store.EMPTY_MAP
        # The folder's owner, else NAMED ATTRIBUTION (an unowned row that
        # names confirmed clients; for another app, waiting names too).
        owners, home = _row_owners(cmap, folder, title, files, snippet, wall.outside)
        if not owners:  # 2.
            return True
        if wall.outside:  # 2b. another app is told no client's work
            return False
        if wall.owner is not None:  # 3.
            return owners == frozenset({wall.owner})
        # 4. pointers only. A row whose owners MIX kinds (a confirmed client
        # and a waiting home, say) still needs every confirmed one named; the
        # other owners then go on to rule 5.
        confirmed = {o[len(_CLIENT):] for o in owners if o.startswith(_CLIENT)}
        if confirmed and not confirmed <= set(wall.named):
            return False
        if len(confirmed) == len(owners):
            return True
        holds = (
            bool(wall.folder_key) and bool(home) and home != wall.folder_key
            and keys.inside(home, wall.folder_key)
        )
        if wall.spans or holds:  # 5.
            return False
        return True  # 6.
    except Exception:  # noqa: BLE001 — unsure: not told
        log.debug("clients: may_tell failed; not told", exc_info=True)
        return False


def tell_passages(
    engine: Any, turn: TurnWall, passages: Sequence[tuple[str, str, str, str]],
) -> list[bool] | None:
    """:func:`may_tell` for a batch of chat and run passages (memory recall,
    history search): each ``(kind, id, title, snippet)``, ``kind``
    :data:`PASSAGE_CHAT` (a chat thread id), :data:`PASSAGE_RUN` (an agent
    run id) or anything else (no folder of its own: a round table). One
    verdict per passage, in order. ``None`` when the turn failed closed or
    the owners cannot be read: the caller then drops EVERY chat and run
    passage. The caller asks :func:`on_owner_screen` first. BLOCKING; never
    raises."""
    items = list(passages or ())
    if not items:
        return []
    wall = turn if isinstance(turn, TurnWall) else NO_CLIENT
    if not wall.ok:
        return None
    try:
        cmap = store.load_map(engine)
        if not isinstance(cmap, ClientMap):
            return None
    except Exception:  # noqa: BLE001
        return None
    chats = [str(i) for k, i, *_ in items if k == PASSAGE_CHAT and i]
    runs = [str(i) for k, i, *_ in items if k == PASSAGE_RUN and i]
    chat_folders = thread_owners(engine, cmap, chats) if chats else {}
    run_folders = session_owners(engine, cmap, runs) if runs else {}
    if chat_folders is None or run_folders is None:
        return None
    # The files the work record keeps for each (a Downloads chat whose files
    # sit in a client's folder is that client's work).
    chat_files = work_files(engine, PASSAGE_CHAT, chats) if chats else {}
    run_files = work_files(engine, PASSAGE_RUN, runs) if runs else {}
    if chat_files is None or run_files is None:
        return None
    out: list[bool] = []
    for kind, pid, title, snippet in items:
        where = chat_folders if kind == PASSAGE_CHAT else run_folders if kind == PASSAGE_RUN else {}
        made = chat_files if kind == PASSAGE_CHAT else run_files if kind == PASSAGE_RUN else {}
        out.append(may_tell(wall, cmap, folder=where.get(str(pid), ""), title=title or "",
                            files=made.get(str(pid), ()), snippet=snippet or ""))
    return out


def work_files(engine: Any, kind: str, ids: Iterable[Any]) -> dict[str, list[str]] | None:
    """The file paths the work record keeps for each chat thread
    (``kind`` :data:`PASSAGE_CHAT`) or agent run (:data:`PASSAGE_RUN`) in
    ``ids``: ``{ref: [path, …]}``, at most :data:`FILES_MAX` each; a ref with
    no row is left out. One bounded IN query. None when it cannot be read.
    BLOCKING. Never raises."""
    wanted = _wanted(ids)
    if not wanted:
        return {}
    if engine is None:
        return None
    try:
        import json

        from sqlmodel import select

        from ...core.db import session_scope
        from ..models import KIND_CHAT, KIND_CODE, KIND_RUN, WorkEntry

        kinds = [KIND_RUN] if kind == PASSAGE_RUN else [KIND_CHAT, KIND_CODE]
        out: dict[str, list[str]] = {}
        with session_scope(engine) as db:
            for i in range(0, len(wanted), _IN_CHUNK):
                stmt = select(WorkEntry.ref, WorkEntry.files_json).where(
                    WorkEntry.ref.in_(wanted[i : i + _IN_CHUNK]),  # type: ignore[attr-defined]
                    WorkEntry.kind.in_(kinds),  # type: ignore[attr-defined]
                )
                for ref, files_json in db.exec(stmt):
                    try:
                        raw = json.loads(files_json or "[]")
                    except ValueError:
                        raw = []
                    paths = [str(f.get("path") if isinstance(f, dict) else f or "")
                             for f in raw if f][:FILES_MAX]
                    out.setdefault(str(ref), []).extend(p for p in paths if p)
        return out
    except Exception:  # noqa: BLE001 — unreadable: the caller drops every passage
        log.debug("clients: the work record's files could not be read", exc_info=True)
        return None


def current() -> TurnWall:
    """The wall of the turn in flight in THIS context (:data:`NO_CLIENT`
    when nothing set it). Never raises."""
    try:
        wall = CURRENT.get()
    except Exception:  # noqa: BLE001 — never raises
        return NO_CLIENT
    return wall if isinstance(wall, TurnWall) else NO_CLIENT


def inherited() -> TurnWall | None:
    """The wall a caller already set in THIS context (a chat turn, a parent
    run), or None when nothing set one (a run the owner started from a page
    or a schedule). Never raises."""
    try:
        wall = CURRENT.get(None)  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001 — never raises
        return None
    return wall if isinstance(wall, TurnWall) else None


def narrowed(parent: TurnWall, own: TurnWall) -> TurnWall:
    """The wall of work started INSIDE another turn or run (a helper the
    model delegated to, a child run): never wider than ``parent``
    (the independent review). Its ``named`` is the parent's (the owner's
    words) intersected with its own (a task the MODEL wrote widens
    nothing); its owner is the parent's, else its own folder's; two
    different owners, or either side failing closed, fail closed. ``spans``
    is either side's; the folder is its own. Pure; never raises."""
    try:
        p = parent if isinstance(parent, TurnWall) else NO_CLIENT
        o = own if isinstance(own, TurnWall) else TurnWall(ok=False)
        key = o.folder_key or p.folder_key
        if not p.ok or not o.ok:
            return TurnWall(ok=False, folder_key=key)
        if p.owner is not None and o.owner is not None and p.owner != o.owner:
            return TurnWall(ok=False, folder_key=key)
        owner = p.owner if p.owner is not None else o.owner
        client = p.client if p.owner is not None else o.client
        return TurnWall(
            owner=owner,
            client=client,
            spans=bool(p.spans or o.spans),
            named=frozenset(p.named) & frozenset(o.named),
            ok=True,
            folder_key=key,
            outside=bool(p.outside or o.outside),
        )
    except Exception:  # noqa: BLE001 — unsure: closed
        log.debug("clients: a child's wall could not be narrowed", exc_info=True)
        return TurnWall(ok=False)


def on_owner_screen() -> bool:
    """Is the work in flight one of the owner's own screens
    (:func:`owner_screen`)? Never raises."""
    try:
        return OWNER_SCREEN.get() is True
    except Exception:  # noqa: BLE001 — never raises
        return False


def _reset(var: ContextVar[Any], token: Any, default: Any) -> None:
    try:
        var.reset(token)
    except (ValueError, LookupError, RuntimeError):  # a token from another context
        var.set(default)


@contextmanager
def turn_scope(turn: TurnWall | None) -> Iterator[TurnWall]:
    """Set :data:`CURRENT` to ``turn`` for the body of the ``with`` (reset
    after, however it ends). Not a TurnWall (None) sets :data:`NO_CLIENT`. A
    turn is never an owner screen, so the body also runs with
    :data:`OWNER_SCREEN` off."""
    wall = turn if isinstance(turn, TurnWall) else NO_CLIENT
    token = CURRENT.set(wall)
    screen = OWNER_SCREEN.set(False)
    try:
        yield wall
    finally:
        _reset(OWNER_SCREEN, screen, False)
        _reset(CURRENT, token, NO_CLIENT)


async def scoped_frames(agen: AsyncIterator[Any], turn: TurnWall | None) -> AsyncIterator[Any]:
    """``agen`` iterated with ``turn`` set in the iterating task's context.

    For a turn that is a generator (the stream lane), beside
    ``egress.scoped_frames``: the wall is set when the first frame is asked
    for, so every task the turn creates from then on (the model pump, the tool
    rounds) copies it, and it is reset when the stream ends however it ends.
    Closing this generator closes ``agen`` at once (a dropped connection must
    reach the turn's own ``finally`` blocks, never at garbage collection)."""
    wall = turn if isinstance(turn, TurnWall) else NO_CLIENT
    token = CURRENT.set(wall)
    screen = OWNER_SCREEN.set(False)
    try:
        async for frame in agen:
            yield frame
    finally:
        try:
            close = getattr(agen, "aclose", None)
            if close is not None:
                await close()
        finally:
            _reset(OWNER_SCREEN, screen, False)
            _reset(CURRENT, token, NO_CLIENT)


@contextmanager
def owner_screen() -> Iterator[None]:
    """The owner's own screens (``routes/learning.py`` recall,
    ``routes/search.py`` palette): readers inside the ``with`` filter
    nothing (:func:`on_owner_screen` is True). Reset after."""
    token = OWNER_SCREEN.set(True)
    try:
        yield
    finally:
        _reset(OWNER_SCREEN, token, False)


def _wanted(ids: Iterable[Any]) -> list[str]:
    seen: dict[str, None] = {}
    for i in ids or ():
        s = str(i or "").strip()
        if s and s not in seen:
            seen[s] = None
            if len(seen) >= OWNERS_MAX:
                break
    return list(seen)


def _roots(db: Any, project_ids: Iterable[str]) -> dict[str, str]:
    from sqlmodel import select

    from ...core.models import Project

    wanted = sorted({str(p) for p in project_ids if p})
    out: dict[str, str] = {}
    for i in range(0, len(wanted), _IN_CHUNK):
        stmt = select(Project.id, Project.root).where(
            Project.id.in_(wanted[i : i + _IN_CHUNK])  # type: ignore[attr-defined]
        )
        for pid, root in db.exec(stmt):
            if str(root or "").strip():
                out[str(pid)] = str(root).strip()
    return out


def thread_owners(engine: Any, cmap: ClientMap, ids: Iterable[Any]) -> dict[str, str] | None:
    """The FOLDER of each chat thread in ``ids`` (``{thread_id: folder}``,
    "" for one with none), for ``may_tell(folder=…)``: one bounded IN query
    over the saved ``setup_json`` folder (``harness.sessions.folder_of``; a
    Chat › Code session's picked folder, ``checkout_of``), else the thread's
    project root (the work record's own rule, ``collect.thread_candidates``).
    An id that is not a saved chat (a round table) is "". None when it
    cannot be read: the caller then drops every chat passage. BLOCKING.
    Never raises."""
    wanted = _wanted(ids)
    if not wanted:
        return {}
    if engine is None:
        return None
    try:
        from types import SimpleNamespace

        from sqlmodel import select

        from ...core.db import session_scope
        from ...core.models import BUILD_SURFACE, ChatThreadRecord as T
        from ...harness.sessions import checkout_of, folder_of

        found: dict[str, tuple[str, str]] = {}
        projects: dict[str, str] = {}
        with session_scope(engine) as db:
            for i in range(0, len(wanted), _IN_CHUNK):
                stmt = select(T.id, T.setup_json, T.project_id, T.surface).where(
                    T.id.in_(wanted[i : i + _IN_CHUNK])  # type: ignore[attr-defined]
                )
                for tid, setup_json, project_id, surface in db.exec(stmt):
                    row = SimpleNamespace(setup_json=setup_json or "")
                    works_in = str(folder_of(row) or "").strip()
                    if str(surface or "") == BUILD_SURFACE:
                        works_in = str(checkout_of(row) or "").strip() or works_in
                    found[str(tid)] = (works_in, str(project_id or ""))
                    if not works_in and project_id:
                        projects[str(tid)] = str(project_id)
            roots = _roots(db, projects.values()) if projects else {}
        out: dict[str, str] = {}
        for tid in wanted:
            works_in, project_id = found.get(tid, ("", ""))
            out[tid] = works_in or roots.get(project_id, "")
        return out
    except Exception:  # noqa: BLE001 — unreadable: the caller drops every chat
        log.debug("clients: chat folders could not be read", exc_info=True)
        return None


def session_owners(engine: Any, cmap: ClientMap, ids: Iterable[Any]) -> dict[str, str] | None:
    """The same for agent runs (``{session_id: folder}``: the run's
    ``workspace_path``, else its project root; a run in its own scratch
    folder under ``<home>/workspaces`` is its project's root, else "").
    None when it cannot be read. BLOCKING. Never raises."""
    wanted = _wanted(ids)
    if not wanted:
        return {}
    if engine is None:
        return None
    try:
        from sqlmodel import select

        from ...core.db import session_scope
        from ...core.models import Session as S
        from ..index import work_map

        wm = work_map(engine)
        home = getattr(wm, "home", None) if wm is not None else None
        scratch = keys.folder_key(str(home) + "/workspaces") if home else ""
        found: dict[str, tuple[str, str]] = {}
        with session_scope(engine) as db:
            for i in range(0, len(wanted), _IN_CHUNK):
                stmt = select(S.id, S.workspace_path, S.project_id).where(
                    S.id.in_(wanted[i : i + _IN_CHUNK])  # type: ignore[attr-defined]
                )
                for sid, ws, project_id in db.exec(stmt):
                    ws = str(ws or "").strip()
                    if ws and scratch and keys.inside(keys.folder_key(ws), scratch):
                        ws = ""  # its own scratch folder: no folder of the owner's
                    found[str(sid)] = (ws, str(project_id or ""))
            roots = _roots(db, [p for w, p in found.values() if not w and p])
        out: dict[str, str] = {}
        for sid in wanted:
            ws, project_id = found.get(sid, ("", ""))
            out[sid] = ws or roots.get(project_id, "")
        return out
    except Exception:  # noqa: BLE001 — unreadable: the caller drops every run
        log.debug("clients: run folders could not be read", exc_info=True)
        return None


__all__ = [
    "CURRENT",
    "NO_CLIENT",
    "OWNER_SCREEN",
    "PASSAGE_CHAT",
    "PASSAGE_RUN",
    "TurnWall",
    "current",
    "inherited",
    "may_tell",
    "narrowed",
    "on_owner_screen",
    "outside_and_map",
    "owners_of",
    "parent_folder",
    "owner_screen",
    "scoped_frames",
    "session_owners",
    "tell_passages",
    "thread_owners",
    "turn_and_map",
    "turn_scope",
    "turn_wall",
    "work_files",
]
