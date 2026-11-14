"""Reads Iron Jarvis's own records into work rows (v1.491.0).

FOUR SOURCES, each a kind of work that has a folder:

* a CHAT bound to a folder (its ``workspace_dir``, else its project's folder);
* a CODING SESSION in Chat › Code (a Build thread): the folder the user
  picked (``harness/sessions.checkout_of``), or the session's own folder;
* a CODING SESSION in a real tool (Build › Code, ``hosted``): its folder;
* an AGENT RUN the user or a schedule started in a folder (never a helper,
  a mission teammate or a delegated child: those are part of the work that
  started them, and that work has its own row).

A conversation counts as work once Iron Jarvis has REPLIED in it (a chat
that holds only the question just asked is not earlier work, and must never
be listed to its own first turn).

What a row keeps comes from columns and the undo journal only: the title,
the folder, when, how it ended, and the FILES the work wrote (a reply's
``documents``, the journal's created / changed files). No file on disk is
opened, no model is called, and a transcript is read only for its roles and
``documents`` lists.

Each source answers CANDIDATES: ``(id, stamp, build)``. The stamp is cheap
(columns of the listing read, plus one grouped count of the journal);
``build`` is called only for a row whose stamp changed since the record was
last built, and answers ``None`` for a source row that is gone or is not
work yet. BLOCKING (SQLite); every caller is off the event loop.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable

from sqlalchemy import func, inspect, literal_column, or_
from sqlmodel import select

from ..core.db import storable_text
from ..core.models import BUILD_SURFACE, AgentRun, ChatThreadRecord, Project, UndoJournal
from ..core.models import Session as RunRow
from . import keys
from .models import KIND_CHAT, KIND_CODE, KIND_RUN, KIND_TOOL_CODE

log = logging.getLogger(__name__)

#: Files kept on one row, newest first (the count stays honest in
#: ``files_total``).
FILES_MAX = 40
TITLE_CHARS = 120
NOTE_CHARS = 240
#: Newest source rows looked at per kind. A row past the bound keeps its work
#: row until its source is really gone (``source_exists``).
SCAN_MAX = 20000
RUN_SCAN_MAX = 20000
#: Journal rows read for one piece of work.
JOURNAL_MAX = 2000
#: A piece of work never given a title.
UNTITLED = "(no title)"

_IN_CHUNK = 400


@dataclass
class Candidate:
    id: str
    kind: str
    ref: str
    stamp: str
    #: ``build(db) -> {column: value}`` for the row (without id/kind/ref/
    #: stamp), or None when the source is gone or is not work yet.
    build: Callable[[Any], "dict[str, Any] | None"]


def _naive(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(value: Any) -> str:
    v = _naive(value)
    return v.isoformat() if v is not None else ""


def _one_line(text: Any, cap: int) -> str:
    words = " ".join(str(text or "").split())
    if len(words) > cap:
        words = words[: cap - 1].rstrip() + "…"
    return storable_text(words)


def _json(text: Any) -> Any:
    try:
        return json.loads(text or "")
    except (TypeError, ValueError):
        return None


def _files_value(pairs: list[tuple[str, str]]) -> tuple[str, int]:
    """``[(path, how)]`` (oldest first) → the JSON kept (NEWEST first) and the
    honest total. A path listed twice is kept once; "made" wins over
    "changed". ASCII JSON: a path can hold a character SQLite cannot store."""
    shown: dict[str, str] = {}
    hows: dict[str, str] = {}
    for path, how in pairs:
        p = str(path or "").strip()
        key = keys.folder_key(p)
        if not key:
            continue
        shown.setdefault(key, p)
        if how == "made" or key not in hows:
            hows[key] = how
    order: list[str] = []
    seen: set[str] = set()
    for path, _how in reversed(pairs):
        key = keys.folder_key(str(path or "").strip())
        if key and key not in seen:
            seen.add(key)
            order.append(key)
    out = [{"path": shown[key], "how": hows[key]} for key in order]
    return json.dumps(out[:FILES_MAX], ensure_ascii=True), len(out)


_ABSOLUTE = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/])")


def _under(base: str, path: str) -> str:
    """``path`` as a whole path: the journal keeps a file inside the working
    folder by its path FROM that folder. Text only."""
    p = str(path or "").strip()
    if not p or not base or _ABSOLUTE.match(p):
        return p
    sep = "\\" if "\\" in base else "/"
    return base.rstrip("\\/") + sep + p


def journal_files(db: Any, session_id: str, base: str = "") -> list[tuple[str, str]]:
    """The files the undo journal says ``session_id`` made or changed, oldest
    first, as ``[(path, "made" | "changed")]`` (a path the journal kept
    relative is put under ``base``, the work's own folder). A change that was
    undone is left out. The journal's own readers (``agents/outcome``) decode
    each row, so this and a run's receipt can never disagree about a kind."""
    from ..agents import outcome as _o

    J = UndoJournal
    stmt = (
        select(J.kind, J.pre_inline, J.applied_at)
        .where(J.session_id == session_id)
        .order_by(J.created_at, literal_column("rowid"))  # type: ignore[arg-type]
        .limit(JOURNAL_MAX)
    )
    out: list[tuple[str, str]] = []
    for kind, pre_inline, applied_at in db.exec(stmt):
        if applied_at is not None:
            continue
        row = SimpleNamespace(pre_inline=pre_inline, kind=kind)
        if kind == _o._CREATED_MANY_KIND:
            out += [(p, "made") for p in _o._envelope_paths(row)]
        elif kind == _o._PATCH_KIND:
            made, edited = _o._patch_paths(row)
            out += [(p, "made") for p in made]
            out += [(p, "changed") for p in edited]
        elif kind == _o._CREATED_KIND:
            out.append((_o._envelope_path(row), "made"))
        elif kind == _o._CHANGED_KIND:
            out.append((_o._envelope_path(row), "changed"))
    return [(_under(base, p), how) for p, how in out if str(p or "").strip()]


def journal_counts(db: Any) -> dict[str, tuple[int, int]]:
    """``{session id: (journal rows, undone rows)}`` in ONE grouped read: part
    of a row's stamp, so a file written or undone AFTER the work ended still
    rebuilds its row."""
    J = UndoJournal
    stmt = select(J.session_id, func.count(), func.count(J.applied_at)).group_by(J.session_id)
    return {str(sid): (int(n or 0), int(u or 0)) for sid, n, u in db.exec(stmt)}


def replied_and_files(messages_json: Any) -> tuple[bool, list[tuple[str, str]]]:
    """``(has Iron Jarvis replied?, [(path, "wrote")])``: the files a
    conversation's replies list as written (each reply's ``documents``),
    oldest first."""
    data = _json(messages_json)
    replied = False
    out: list[tuple[str, str]] = []
    if not isinstance(data, list):
        return False, out
    for m in data:
        if not isinstance(m, dict):
            continue
        if m.get("role") == "assistant":
            replied = True
        docs = m.get("documents")
        if isinstance(docs, list):
            out += [(d, "wrote") for d in docs if isinstance(d, str) and d.strip()]
    return replied, out


def _project_roots(db: Any) -> dict[str, str]:
    return {
        str(p.id): str(p.root or "").strip()
        for p in db.exec(select(Project))
        if str(p.root or "").strip()
    }


# --------------------------------------------------------------------------- #
# chats and Chat › Code sessions (one table)
# --------------------------------------------------------------------------- #
def thread_candidates(db: Any, roots: dict[str, str],
                      journal: dict[str, tuple[int, int]]) -> tuple[list[Candidate], bool]:
    """``(candidates, complete)``; ``complete`` is False when the listing hit
    its bound (a row past it is then removed only once its source is gone)."""
    from ..harness.sessions import NEW_SESSION_TITLE, checkout_of, folder_of
    from ..providers.egress import setup_says_local_only

    T = ChatThreadRecord
    stmt = (
        select(T.id, T.updated_at, T.surface, T.setup_json, T.project_id, T.archived_at,
               T.owner, T.title)
        .order_by(T.updated_at.desc(), literal_column("rowid").desc())  # type: ignore[attr-defined]
        .limit(SCAN_MAX)
    )
    rows = list(db.exec(stmt))
    out: list[Candidate] = []
    for tid, updated, surface, setup_json, project_id, archived, owner, title in rows:
        if str(owner or "") == "daemon":
            continue  # a phone conversation: no folder of its own
        build_thread = str(surface or "") == BUILD_SURFACE
        row = SimpleNamespace(setup_json=setup_json or "")
        works_in = str(folder_of(row) or "").strip()
        if build_thread:
            folder = checkout_of(row) or works_in
        else:
            folder = works_in or roots.get(str(project_id or ""), "")
        folder = str(folder or "").strip()
        if not folder:
            continue
        local = bool(setup_says_local_only(setup_json or ""))
        kind = KIND_CODE if build_thread else KIND_CHAT
        ref = str(tid)
        is_archived = archived is not None
        jn = journal.get(f"build:{ref}", (0, 0)) if build_thread else (0, 0)
        # The title is part of the stamp: a coding session's rename does not
        # move ``updated_at``.
        stamp = (
            f"{_iso(updated)}|{folder}|{int(local)}|{int(is_archived)}|{jn[0]}.{jn[1]}"
            f"|{str(title or '')[:TITLE_CHARS]}"
        )

        def build(db2: Any, *, ref=ref, kind=kind, folder=folder, local=local,
                  project_id=project_id, updated=updated, works_in=works_in,
                  is_archived=is_archived) -> "dict[str, Any] | None":
            got = db2.exec(
                select(T.title, T.messages_json, T.created_at).where(T.id == ref)
            ).first()
            if not got:
                return None  # deleted since it was listed
            title2, messages_json, created = got
            replied, pairs = replied_and_files(messages_json)
            if not replied:
                return None  # only a question so far: not earlier work yet
            if kind == KIND_CODE:
                pairs = journal_files(db2, f"build:{ref}", works_in or folder) + pairs
            files_json, total = _files_value(pairs)
            words = _one_line(title2, TITLE_CHARS)
            if kind == KIND_CODE and words == NEW_SESSION_TITLE:
                words = ""
            return {
                "title": words or UNTITLED,
                "folder": storable_text(folder),
                "folder_key": storable_text(keys.folder_key(folder)),
                "project_id": str(project_id) if project_id else None,
                "local_only": local,
                "archived": is_archived,
                "files_json": files_json,
                "files_total": total,
                "started_at": _naive(created),
                "updated_at": _naive(updated) or _now(),
            }

        out.append(Candidate(f"{kind}:{ref}", kind, ref, stamp, build))
    return out, len(rows) < SCAN_MAX


# --------------------------------------------------------------------------- #
# Build › Code sessions (the real coding tools)
# --------------------------------------------------------------------------- #
def _hosted_table(db: Any) -> bool:
    """Does the Build › Code table exist? (An install that never opened that
    view has none; that is "no rows", not a read that failed.)"""
    return bool(inspect(db.get_bind()).has_table("hostedsessionrecord"))


def hosted_candidates(db: Any) -> tuple[list[Candidate], bool]:
    try:
        from ..hosted.models import HostedSessionRecord as H
        from ..hosted.words import LABELS
        from ..projects.coding_sessions import VENDOR_TOOLS
    except Exception:  # noqa: BLE001 — no Build › Code view in this build
        return [], True
    if not _hosted_table(db):
        return [], True
    stmt = (
        select(H.id, H.harness, H.folder, H.title, H.updated_at, H.created_at, H.handed_from,
               H.archived_at, H.harness_session_id)
        .order_by(H.updated_at.desc(), literal_column("rowid").desc())  # type: ignore[attr-defined]
        .limit(SCAN_MAX)
    )
    rows = list(db.exec(stmt))  # a read that fails RAISES: never "no rows"
    out: list[Candidate] = []
    for sid, harness, folder, title, updated, created, handed_from, archived, tool_sid in rows:
        folder = str(folder or "").strip()
        if not folder or str(handed_from or "").strip():
            continue  # no folder yet, or a helper another session started
        if not str(tool_sid or "").strip():
            continue  # the tool has not answered yet: not earlier work
        ref = str(sid)
        tool = LABELS.get(str(harness or ""), str(harness or ""))
        words = _one_line(title, TITLE_CHARS) or UNTITLED
        keep_home = str(harness or "") not in VENDOR_TOOLS
        is_archived = archived is not None
        stamp = f"{_iso(updated)}|{folder}|{int(is_archived)}|{words}"

        def build(_db: Any, *, folder=folder, tool=tool, words=words, keep_home=keep_home,
                  updated=updated, created=created, is_archived=is_archived) -> dict[str, Any]:
            return {
                "title": words,
                "folder": storable_text(folder),
                "folder_key": storable_text(keys.folder_key(folder)),
                "tool": tool,
                "keep_home": keep_home,
                "archived": is_archived,
                "started_at": _naive(created),
                "updated_at": _naive(updated) or _now(),
            }

        out.append(Candidate(f"{KIND_TOOL_CODE}:{ref}", KIND_TOOL_CODE, ref, stamp, build))
    return out, len(rows) < SCAN_MAX


# --------------------------------------------------------------------------- #
# agent runs
# --------------------------------------------------------------------------- #
def _part_of_other_work() -> list[str]:
    """Origins of runs that are a PART of another piece of work."""
    out = []
    try:
        from ..harness.helpers import HELPER_ORIGIN

        out.append(HELPER_ORIGIN)
    except Exception:  # noqa: BLE001
        out.append("job:build-helper")
    try:
        from ..agents.team import MISSION_MEMBER_ORIGIN

        out.append(MISSION_MEMBER_ORIGIN)
    except Exception:  # noqa: BLE001
        out.append("job:mission-member")
    return out


def _child_sessions(db: Any, ids: list[str]) -> set[str]:
    """The sessions among ``ids`` whose run was started BY another run."""
    out: set[str] = set()
    for i in range(0, len(ids), _IN_CHUNK):
        chunk = ids[i : i + _IN_CHUNK]
        stmt = (
            select(AgentRun.session_id)
            .where(AgentRun.parent_id.is_not(None))  # type: ignore[union-attr]
            .where(AgentRun.session_id.in_(chunk))  # type: ignore[attr-defined]
        )
        out.update(str(s) for s in db.exec(stmt))
    return out


def run_candidates(db: Any, roots: dict[str, str], home: Any,
                   journal: dict[str, tuple[int, int]]) -> tuple[list[Candidate], bool]:
    S = RunRow
    skip = _part_of_other_work()
    stmt = (
        select(S.id, S.status, S.created_at, S.finished_at, S.workspace_path, S.project_id,
               S.origin, S.outcome, S.options_json)
        # Helpers and teammates are left out IN the query, so they never use
        # up the bound.
        .where(or_(S.origin.is_(None), S.origin.not_in(skip)))  # type: ignore[union-attr]
        .order_by(S.created_at.desc(), literal_column("rowid").desc())  # type: ignore[attr-defined]
        .limit(RUN_SCAN_MAX)
    )
    rows = list(db.exec(stmt))
    own_base = keys.folder_key(str(home) + "/workspaces") if home else ""
    kept: list[tuple[Any, ...]] = []
    for row in rows:
        _sid, _status, _created, _finished, ws, project_id, _origin, _outcome, _opts = row
        ws = str(ws or "").strip()
        if ws and not (own_base and keys.inside(keys.folder_key(ws), own_base)):
            folder = ws
        else:
            folder = roots.get(str(project_id or ""), "")
        if not folder:
            continue  # ran in a scratch folder of its own, in no project
        kept.append((*row, folder, ws or folder))
    children = _child_sessions(db, [str(r[0]) for r in kept]) if kept else set()
    out: list[Candidate] = []
    for sid, status, created, finished, _ws, project_id, origin, outcome, opts, folder, base in kept:
        ref = str(sid)
        if ref in children:
            continue
        status_word = str(getattr(status, "value", status) or "")
        options = _json(opts)
        local = isinstance(options, dict) and options.get("local_only") is True
        jn = journal.get(ref, (0, 0))
        stamp = f"{status_word}|{_iso(finished)}|{outcome or ''}|{folder}|{int(local)}|{jn[0]}.{jn[1]}"

        def build(db2: Any, *, ref=ref, folder=folder, project_id=project_id, origin=origin,
                  outcome=outcome, status_word=status_word, local=local, created=created,
                  finished=finished, base=base) -> "dict[str, Any] | None":
            got = db2.exec(select(S.task, S.summary).where(S.id == ref)).first()
            if not got:
                return None  # deleted since it was listed
            task, summary = got
            files_json, total = _files_value(journal_files(db2, ref, base))
            first_line = str(task or "").strip().splitlines()[0] if str(task or "").strip() else ""
            ended = str(outcome or "") or (
                status_word if status_word in ("completed", "failed", "cancelled") else ""
            )
            return {
                "title": _one_line(first_line, TITLE_CHARS) or UNTITLED,
                "folder": storable_text(folder),
                "folder_key": storable_text(keys.folder_key(folder)),
                "project_id": str(project_id) if project_id else None,
                "origin": str(origin or ""),
                "outcome": ended,
                "local_only": local,
                "files_json": files_json,
                "files_total": total,
                "note": _one_line(summary, NOTE_CHARS),
                "started_at": _naive(created),
                "updated_at": _naive(finished) or _naive(created) or _now(),
            }

        out.append(Candidate(f"{KIND_RUN}:{ref}", KIND_RUN, ref, stamp, build))
    return out, len(rows) < RUN_SCAN_MAX


def candidates(db: Any, *, home: Any = None) -> tuple[list[Candidate], set[str]]:
    """Every candidate, and the KINDS whose listing was read whole. A source
    that cannot be read contributes nothing and is not "complete" (its rows
    are then left exactly as they are)."""
    try:
        roots = _project_roots(db)
    except Exception:  # noqa: BLE001
        log.debug("work record: the projects could not be read", exc_info=True)
        roots = {}
    try:
        journal = journal_counts(db)
    except Exception:  # noqa: BLE001
        log.debug("work record: the journal could not be counted", exc_info=True)
        journal = {}
    out: list[Candidate] = []
    complete: set[str] = set()
    try:
        got, whole = thread_candidates(db, roots, journal)
        out += got
        if whole:
            complete.update({KIND_CHAT, KIND_CODE})
    except Exception:  # noqa: BLE001
        log.debug("work record: the chats could not be read", exc_info=True)
    try:
        got, whole = hosted_candidates(db)
        out += got
        if whole:
            complete.add(KIND_TOOL_CODE)
    except Exception:  # noqa: BLE001
        log.debug("work record: the Build coding sessions could not be read", exc_info=True)
    try:
        got, whole = run_candidates(db, roots, home, journal)
        out += got
        if whole:
            complete.add(KIND_RUN)
    except Exception:  # noqa: BLE001
        log.debug("work record: the agent runs could not be read", exc_info=True)
    return out, complete


# --------------------------------------------------------------------------- #
# asking the SOURCE, by id
# --------------------------------------------------------------------------- #
def _split(rows: "list[tuple[str, str]]") -> dict[str, list[str]]:
    by_kind: dict[str, list[str]] = {}
    for kind, ref in rows:
        by_kind.setdefault(kind, []).append(ref)
    return by_kind


def live_state(db: Any, rows: "list[tuple[str, str]]") -> dict[tuple[str, str], dict[str, bool]]:
    """What each ``(kind, ref)`` looks like in its SOURCE right now:
    ``{(kind, ref): {"local_only": bool, "archived": bool}}``. A source row
    that is gone is simply absent. Read by primary key, so a chat deleted or
    switched to Local only a moment ago is never told to another chat from a
    work row that has not been rebuilt yet. Raises when a source cannot be
    read (the caller then shows nothing)."""
    from ..providers.egress import setup_says_local_only

    out: dict[tuple[str, str], dict[str, bool]] = {}
    by_kind = _split(rows)
    T = ChatThreadRecord
    for kind in (KIND_CHAT, KIND_CODE):
        refs = by_kind.get(kind) or []
        for i in range(0, len(refs), _IN_CHUNK):
            stmt = select(T.id, T.setup_json, T.archived_at).where(T.id.in_(refs[i : i + _IN_CHUNK]))  # type: ignore[attr-defined]
            for tid, setup_json, archived in db.exec(stmt):
                out[(kind, str(tid))] = {
                    "local_only": bool(setup_says_local_only(setup_json or "")),
                    "archived": archived is not None,
                }
    refs = by_kind.get(KIND_RUN) or []
    for i in range(0, len(refs), _IN_CHUNK):
        stmt = select(RunRow.id, RunRow.options_json).where(RunRow.id.in_(refs[i : i + _IN_CHUNK]))  # type: ignore[attr-defined]
        for sid, opts in db.exec(stmt):
            options = _json(opts)
            out[(KIND_RUN, str(sid))] = {
                "local_only": isinstance(options, dict) and options.get("local_only") is True,
                "archived": False,
            }
    refs = by_kind.get(KIND_TOOL_CODE) or []
    if refs:
        from ..hosted.models import HostedSessionRecord as H

        for i in range(0, len(refs), _IN_CHUNK):
            stmt = select(H.id, H.archived_at).where(H.id.in_(refs[i : i + _IN_CHUNK]))  # type: ignore[attr-defined]
            for sid, archived in db.exec(stmt):
                out[(KIND_TOOL_CODE, str(sid))] = {"local_only": False, "archived": archived is not None}
    return out


def source_exists(db: Any, rows: "list[tuple[str, str]]") -> set[tuple[str, str]]:
    """The ``(kind, ref)`` among ``rows`` whose source row still exists."""
    return set(live_state(db, rows))


__all__ = [
    "Candidate",
    "FILES_MAX",
    "UNTITLED",
    "candidates",
    "hosted_candidates",
    "journal_counts",
    "journal_files",
    "live_state",
    "replied_and_files",
    "run_candidates",
    "source_exists",
    "thread_candidates",
]
