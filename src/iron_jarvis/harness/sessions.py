"""IJ harness SESSIONS — the store behind Build's IJ harness view (v1.367.0,
plan Phase 4).

REUSE, DON'T REBUILD: an IJ harness session IS a chat thread
(``ChatThreadRecord``) with ``surface = "build"``. So it inherits what a chat
has — durable named turns and rejoin (``daemon/durable_turns``), steering,
the queue (``PUT /chat/threads/{id}/queue``), per-thread usage
(``GET /chat/threads/{id}/usage``), archive's rules, approvals — and only
what is DIFFERENT lives here:

* ONE WRITER. A chat's transcript is written by the page (whole-array PUTs);
  a session's is written by the DAEMON: its turn appends the question when it
  starts and the reply when it ends (:func:`append_lines`), and the /build
  routes change the title and the setup. ``PUT /chat/threads/{id}`` refuses a
  build thread (409), so a session can never become a chat or back.
* NEVER IN CHAT. Every chat list reads ``core.models.chat_surface_only``; a
  session is not indexed for Chat's history search or memory recall.
* ITS FOLDER is its working folder (``setup.workspace_dir``), checked through
  ``fs_policy.root_problem`` when it is created and again before every turn.
* ITS OWN PROCESS SESSION: every tool call of a build turn runs under
  ``build:<thread id>`` (:func:`session_id_for`), never "chat" — the process
  tools and ``core.procs.owned_by`` key on it, so a dev server one session
  started is invisible to every other, and :func:`end_session_programs`
  (archive, delete) and ``procs.end_all`` (daemon shutdown) stop it.
* A MODE: "act" (default, stored as nothing) or "plan" (read-only tools,
  ``plan_update`` and ``exit_plan_mode`` only — ``harness/session_tools``).
* FOLDER DISPLAY NAMES: ONE store (``BuildFolderRecord``) for "this folder
  shows as X". When the folder is an ACTIVE Jarvis project's root, the
  project's name is the display name and renaming the group renames the
  project — one name, never two. The folder on disk is never renamed.

Every function here is BLOCKING (SQLite); routes call them from FastAPI's
threadpool (a sync ``def``) or through ``asyncio.to_thread`` (the v1.153.1
rule). Nothing here imports the route modules at import time.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Iterable

from ..core.db import CONVERSATION_WRITE_LOCK, session_scope
from ..core.ids import utcnow
from ..core.models import (
    BUILD_SURFACE,
    BuildFolderRecord,
    ChatThreadRecord,
    Project,
    is_build_thread,
)

log = logging.getLogger(__name__)

#: The process/tool session id prefix of a build session's calls.
SESSION_PREFIX = "build:"
#: The session modes. "act" is the default and is stored as nothing.
MODE_ACT = "act"
MODE_PLAN = "plan"
MODES = (MODE_ACT, MODE_PLAN)
#: The newest messages a session keeps (the chat PUT route's horizon).
MAX_MESSAGES = 200
#: A session title / a folder display name, at most.
MAX_TITLE_CHARS = 120
MAX_NAME_CHARS = 80
#: The default title until the first question names the session.
NEW_SESSION_TITLE = "New session"
#: The plan kept on a session (``setup.plan``), at most.
MAX_PLAN_CHARS = 20_000
MAX_PLAN_STEPS = 50

#: The keys a line of a session's transcript may carry — the chat page's own
#: ``ChatMessage`` names (camelCase), so Phase 6's screen renders a session
#: with the chat's pieces. Anything else is dropped.
LINE_KEYS: tuple[str, ...] = (
    "role", "content", "at", "turnId",
    # the question
    "attachmentNames", "attachmentPaths", "steer",
    # the reply (the stream lane's done frame, renamed the page's way)
    "route", "toolsUsed", "documents", "deniedTools", "usage", "steps",
    "thinking", "truncated", "interrupted", "error", "folderRules",
    "approved", "refit", "doors", "trust", "trustReason", "trustNote",
    "timing",
    # v1.368.0 (Phase 5): a continuation question the DAEMON wrote ("Resume
    # when ready" / "Choose account" — the screen hides it, like the chat's
    # hidden Continue turn), and the account-limit card a reply ended on.
    "continuation", "accountLimit",
)


# --------------------------------------------------------------------------- #
# ids
# --------------------------------------------------------------------------- #


def session_id_for(thread_id: str) -> str:
    """``build:<thread id>`` — the session id every tool call of this build
    session runs under (``ToolContext.session_id``, ``procs.owned_by``, the
    ledger rows)."""
    return SESSION_PREFIX + str(thread_id or "").strip()


def thread_of(session_id: str) -> str:
    """The thread id of a ``build:<id>`` session id; "" for anything else."""
    sid = str(session_id or "")
    return sid[len(SESSION_PREFIX):] if sid.startswith(SESSION_PREFIX) else ""


def end_session_programs(thread_id: str) -> list[int]:
    """Stop every program this build session started (its ``process_start``
    programs through the end hook, the windowless children its commands left
    running) — ``procs.end_session``. BLOCKING; never raises."""
    from ..core import procs

    try:
        return procs.end_session(session_id_for(thread_id))
    except Exception:  # noqa: BLE001 — never fails the caller
        log.warning("ending build session %s's programs failed", thread_id, exc_info=True)
        return []


# --------------------------------------------------------------------------- #
# folders
# --------------------------------------------------------------------------- #


def folder_key(path: str) -> str:
    """A folder as ``projects/locate`` compares roots: separators folded, no
    trailing slash, case-folded (the daemon speaks Windows paths)."""
    text = str(path or "").strip()
    if not text:
        return ""
    try:
        text = os.path.normpath(text)
    except (TypeError, ValueError):
        pass
    return text.replace("\\", "/").rstrip("/").lower()


def _base_name(path: str) -> str:
    text = str(path or "").replace("\\", "/").rstrip("/")
    return text.rsplit("/", 1)[-1] or text or "Folder"


def _project_roots(db: Any) -> dict[str, Project]:
    """ACTIVE projects by normalised root (an archived project names nothing)."""
    from sqlmodel import select

    out: dict[str, Project] = {}
    for p in db.exec(select(Project)):
        if (getattr(p, "status", "active") or "active") == "archived":
            continue
        key = folder_key(getattr(p, "root", "") or "")
        if key and key not in out:
            out[key] = p
    return out


def folder_names(engine: Any, paths: Iterable[str]) -> dict[str, dict[str, Any]]:
    """``{folder key: {path, name, is_project, project_id}}`` for each folder.

    THE ONE-NAME RULE: a folder that is an active project's ROOT shows the
    project's name (``is_project`` True); otherwise the stored display name;
    otherwise the folder's own name on disk."""
    from sqlmodel import select

    want = {folder_key(p): p for p in paths if folder_key(p)}
    out: dict[str, dict[str, Any]] = {}
    if not want:
        return out
    with session_scope(engine) as db:
        roots = _project_roots(db)
        stored = {
            r.key: r.name
            for r in db.exec(select(BuildFolderRecord).where(BuildFolderRecord.key.in_(list(want))))  # type: ignore[attr-defined]
        }
        for key, path in want.items():
            project = roots.get(key)
            if project is not None:
                out[key] = {
                    "path": path, "name": project.name or _base_name(path),
                    "is_project": True, "project_id": project.id,
                }
            else:
                out[key] = {
                    "path": path, "name": stored.get(key) or _base_name(path),
                    "is_project": False, "project_id": None,
                }
    return out


def clean_name(name: Any, *, cap: int = MAX_NAME_CHARS) -> str:
    """One line, whitespace folded, cut to ``cap``."""
    return " ".join(str(name or "").split())[:cap]


def rename_folder(engine: Any, path: str, name: str) -> dict[str, Any]:
    """Set the DISPLAY NAME of ``path``. When it is an active project's root,
    the PROJECT is renamed (one name, never two) and no display-name row is
    kept for it; otherwise the one store holds it. The folder on disk is
    NEVER touched. ``ValueError`` in words for an empty name or path."""
    key = folder_key(path)
    if not key:
        raise ValueError("Say which folder to rename.")
    clean = clean_name(name)
    if not clean:
        raise ValueError("A folder's name cannot be empty.")
    with CONVERSATION_WRITE_LOCK, session_scope(engine) as db:
        project = _project_roots(db).get(key)
        stale = db.get(BuildFolderRecord, key)
        if project is not None:
            row = db.get(Project, project.id)
            row.name = clean
            db.add(row)
            # A display name stored before the folder became a project would
            # be a second name waiting to come back: it goes.
            if stale is not None:
                db.delete(stale)
            db.commit()
            return {"path": path, "name": clean, "is_project": True, "project_id": project.id}
        if stale is None:
            stale = BuildFolderRecord(key=key, path=str(path), name=clean)
        else:
            stale.name = clean
            stale.path = str(path)
            stale.updated_at = utcnow()
        db.add(stale)
        db.commit()
    return {"path": path, "name": clean, "is_project": False, "project_id": None}


# --------------------------------------------------------------------------- #
# sessions
# --------------------------------------------------------------------------- #


def _setup(row: Any) -> dict[str, Any]:
    try:
        data = json.loads(getattr(row, "setup_json", "") or "{}")
    except (TypeError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def _messages(row: Any) -> list[dict[str, Any]]:
    try:
        data = json.loads(getattr(row, "messages_json", "") or "[]")
    except (TypeError, ValueError):
        data = []
    return [m for m in data if isinstance(m, dict)] if isinstance(data, list) else []


def folder_of(row: Any) -> str:
    """A session's working folder (its setup's ``workspace_dir``)."""
    return str(_setup(row).get("workspace_dir") or "").strip()


def mode_of(row: Any) -> str:
    return MODE_PLAN if _setup(row).get("mode") == MODE_PLAN else MODE_ACT


def get_row(engine: Any, thread_id: str) -> ChatThreadRecord | None:
    """The BUILD thread ``thread_id`` (detached), or None — a chat thread is
    not a session and answers None."""
    with session_scope(engine) as db:
        row = db.get(ChatThreadRecord, str(thread_id or ""))
        if row is None or not is_build_thread(row):
            return None
        db.expunge(row)
        return row


def create(engine: Any, folder: str, title: str = "", setup: "dict[str, Any] | None" = None) -> ChatThreadRecord:
    """A new session working in ``folder`` (already checked by the caller
    through ``fs_policy.root_problem``). ``setup`` is merged in through the
    chat's ONE setup whitelist (``routes.chat._clean_setup``)."""
    from ..daemon.routes.chat import _clean_setup

    raw = {**(setup or {}), "workspace_dir": str(folder)}
    row = ChatThreadRecord(
        title=clean_name(title, cap=MAX_TITLE_CHARS) or NEW_SESSION_TITLE,
        surface=BUILD_SURFACE,
        messages_json="[]",
        setup_json=_clean_setup(raw),
        # A session is not a chat in a project: it is grouped by its FOLDER
        # (a project root shows the project's name), and Chat's project lists
        # must never pick it up.
        project_id=None,
    )
    with CONVERSATION_WRITE_LOCK, session_scope(engine) as db:
        db.add(row)
        db.commit()
        db.refresh(row)
        db.expunge(row)
    return row


def _next_stamp(prev: Any) -> Any:
    from ..daemon.chat_append import _next_stamp as bump

    return bump(prev)


def update(engine: Any, thread_id: str, *, title: "str | None" = None,
           setup: "dict[str, Any] | None" = None) -> ChatThreadRecord | None:
    """Change a session's title and/or MERGE ``setup`` keys (an explicit
    ``None`` value removes a key). The folder is never changed here. None =
    no such session."""
    from ..daemon.routes.chat import _clean_setup

    with CONVERSATION_WRITE_LOCK, session_scope(engine) as db:
        row = db.get(ChatThreadRecord, str(thread_id or ""))
        if row is None or not is_build_thread(row):
            return None
        if title is not None:
            row.title = clean_name(title, cap=MAX_TITLE_CHARS) or row.title or NEW_SESSION_TITLE
        if setup:
            merged = _setup(row)
            for key, value in setup.items():
                if key == "workspace_dir":
                    continue  # a session's folder is fixed at creation
                if value is None:
                    merged.pop(key, None)
                else:
                    merged[key] = value
            row.setup_json = _clean_setup(merged)
        # NOT ``updated_at``: renaming a session or switching its mode is not
        # activity, and the list's order must not jump (archive's rule).
        db.add(row)
        db.commit()
        db.refresh(row)
        db.expunge(row)
        return row


def _clean_line(raw: Any, stamp: str) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    line = {k: raw[k] for k in LINE_KEYS if k in raw and raw[k] is not None}
    role = str(line.get("role") or "").strip()
    if role not in ("user", "assistant"):
        return None
    line["role"] = role
    line["content"] = str(line.get("content") or "")
    line.setdefault("at", stamp)
    return line


def append_lines(engine: Any, thread_id: str, lines: list[dict[str, Any]], *,
                 title_from_first: bool = True) -> dict[str, Any]:
    """Append ``lines`` to the session (whitelisted to :data:`LINE_KEYS`,
    stamped, newest :data:`MAX_MESSAGES` kept) under the ONE conversation
    lock, moving ``updated_at`` the PUT route's monotonic way. A session still
    called :data:`NEW_SESSION_TITLE` takes its first question as its title.
    Never indexed for Chat's history search. Returns ``{ok, updated_at,
    count}``; ``ok`` False for an unknown session. Never raises."""
    stamp = utcnow().isoformat()
    cleaned = [c for c in (_clean_line(x, stamp) for x in (lines or [])) if c is not None]
    try:
        with CONVERSATION_WRITE_LOCK, session_scope(engine) as db:
            row = db.get(ChatThreadRecord, str(thread_id or ""))
            if row is None or not is_build_thread(row):
                return {"ok": False, "updated_at": None, "count": 0}
            msgs = (_messages(row) + cleaned)[-MAX_MESSAGES:]
            row.messages_json = json.dumps(msgs)
            if title_from_first and (row.title or NEW_SESSION_TITLE) == NEW_SESSION_TITLE:
                first = next((m["content"] for m in msgs if m.get("role") == "user" and m.get("content")), "")
                if first:
                    words = " ".join(first.split())
                    row.title = words[:48] + ("…" if len(words) > 48 else "")
            row.updated_at = _next_stamp(row.updated_at)
            db.add(row)
            db.commit()
            db.refresh(row)
            return {"ok": True, "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                    "count": len(msgs)}
    except Exception:  # noqa: BLE001 — a turn never fails on its transcript
        log.warning("appending to build session %s failed", thread_id, exc_info=True)
        return {"ok": False, "updated_at": None, "count": 0}


def cut_transcript(engine: Any, thread_id: str, turn_id: str, keep: int, cut_at: Any) -> dict[str, Any]:
    """REWIND's conversation half (v1.370.0): cut the transcript back to the
    ``keep`` lines before turn ``turn_id``'s question, in ONE write under the
    conversation lock. Refuses (``ok`` False, ``reason``) when the line at
    ``keep`` is no longer that question — the transcript moved since the
    rewind was planned, and cutting a guessed place would lose the wrong
    lines.

    The session's own state follows the cut: a plan APPROVED in a rewound
    turn (``setup.plan.approved_at`` at or after ``cut_at``) goes, and the
    session is back in plan mode (the only mode its plan door was offered
    in); an account-limit card and a "Resume when ready" wait go (a
    continuation would carry on work that was just rewound). A session cut
    back to nothing is called :data:`NEW_SESSION_TITLE` again, so its next
    question names it. BLOCKING; never raises."""
    from ..daemon.routes.chat import _clean_setup
    from .rewind import _naive_utc

    try:
        with CONVERSATION_WRITE_LOCK, session_scope(engine) as db:
            row = db.get(ChatThreadRecord, str(thread_id or ""))
            if row is None or not is_build_thread(row):
                return {"ok": False, "reason": "no such Build session"}
            msgs = _messages(row)
            here = msgs[keep] if 0 <= keep < len(msgs) else None
            if (
                here is None or here.get("turnId") != turn_id or here.get("role") != "user"
                or here.get("steer")
            ):
                return {"ok": False, "reason": "the conversation changed while it was being rewound"}
            removed = len(msgs) - keep
            row.messages_json = json.dumps(msgs[:keep])
            if keep == 0:
                row.title = NEW_SESSION_TITLE
            setup = _setup(row)
            plan = setup.get("plan")
            approved = _naive_utc(plan.get("approved_at")) if isinstance(plan, dict) else None
            cut = _naive_utc(cut_at)
            changed_setup = False
            if approved is not None and cut is not None and approved >= cut:
                setup.pop("plan", None)
                setup["mode"] = MODE_PLAN
                changed_setup = True
            for key in ("account_limit", "account_wait"):
                if key in setup:
                    setup.pop(key, None)
                    changed_setup = True
            if changed_setup:
                row.setup_json = _clean_setup(setup)
            row.updated_at = _next_stamp(row.updated_at)
            db.add(row)
            db.commit()
            db.refresh(row)
            return {
                "ok": True, "removed": removed, "count": keep,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                "mode": MODE_PLAN if _setup(row).get("mode") == MODE_PLAN else MODE_ACT,
            }
    except Exception:  # noqa: BLE001 — the route reports it in words
        log.warning("cutting build session %s's transcript failed", thread_id, exc_info=True)
        return {"ok": False, "reason": "the conversation could not be saved"}


def history(row: Any) -> list[dict[str, str]]:
    """What the next turn's model is shown of the conversation: the user's
    questions (steer notes included) and the replies, role + content only —
    a reply that failed with nothing written is left out."""
    out: list[dict[str, str]] = []
    for m in _messages(row):
        role = m.get("role")
        content = str(m.get("content") or "")
        if role not in ("user", "assistant") or not content.strip():
            continue
        out.append({"role": role, "content": content})
    return out


def has_turn_reply(row: Any, turn_id: str) -> bool:
    """Does the transcript already hold the reply of ``turn_id``?"""
    return any(
        m.get("role") == "assistant" and m.get("turnId") == turn_id for m in _messages(row)
    )


# --------------------------------------------------------------------------- #
# views
# --------------------------------------------------------------------------- #


def session_view(row: Any, *, states: "dict[str, dict[str, bool]] | None" = None,
                 names: "dict[str, dict[str, Any]] | None" = None,
                 full: bool = False) -> dict[str, Any]:
    """One session as the routes answer it. ``full`` adds the transcript and
    the whole setup (``GET /build/sessions/{id}``)."""
    setup = _setup(row)
    folder = str(setup.get("workspace_dir") or "")
    state = (states or {}).get(row.id) or {}
    out: dict[str, Any] = {
        "id": row.id,
        "title": row.title or NEW_SESSION_TITLE,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "running": bool(state.get("running")),
        "waiting": bool(state.get("waiting")),
        "archived": getattr(row, "archived_at", None) is not None,
    }
    if not full:
        return out
    info = (names or {}).get(folder_key(folder)) or {
        "path": folder, "name": _base_name(folder), "is_project": False, "project_id": None,
    }
    out.update({
        "folder": folder,
        "folder_name": info["name"],
        "is_project": info["is_project"],
        "project_id": info["project_id"],
        "mode": MODE_PLAN if setup.get("mode") == MODE_PLAN else MODE_ACT,
        "plan": setup.get("plan") or None,
        "setup": {
            k: setup[k] for k in (
                "provider", "model", "reasoning", "approval_mode", "local_only", "granted_tools",
                "account", "account_name",
            ) if k in setup
        },
        "messages": _messages(row),
        "session_id": session_id_for(row.id),
        # v1.368.0 (Phase 5): the last limit the session's account hit (the
        # card) and a pending "Resume when ready" wait — None when neither.
        "account_limit": setup.get("account_limit") or None,
        "account_wait": setup.get("account_wait") or None,
    })
    return out


def list_grouped(engine: Any, *, include_archived: bool = False,
                 states: "dict[str, dict[str, bool]] | None" = None) -> dict[str, Any]:
    """``{folders: [{path, name, is_project, project_id, sessions: [...]}]}``
    — every build session grouped by its folder, sessions newest first,
    folders by their newest session. Archived sessions are left out unless
    ``include_archived``."""
    from types import SimpleNamespace

    from sqlmodel import select

    T = ChatThreadRecord
    with session_scope(engine) as db:
        # ONLY the columns a list row shows (Phase 4 review): the whole row
        # would read every session's transcript — up to 200 replies with
        # their steps and thinking each — to print titles (the v1.311.0
        # GET /chat/threads lesson).
        stmt = select(T.id, T.title, T.updated_at, T.archived_at, T.setup_json).where(
            T.surface == BUILD_SURFACE
        )
        if not include_archived:
            stmt = stmt.where(T.archived_at.is_(None))  # type: ignore[union-attr]
        stmt = stmt.order_by(T.updated_at.desc())  # type: ignore[attr-defined]
        rows = [
            SimpleNamespace(id=i, title=t, updated_at=u, archived_at=a, setup_json=s)
            for i, t, u, a, s in db.exec(stmt)
        ]
    groups: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    paths: dict[str, str] = {}
    for r in rows:
        folder = folder_of(r)
        key = folder_key(folder) or ""
        if key not in groups:
            groups[key] = {"sessions": []}
            order.append(key)
            paths[key] = folder
        groups[key]["sessions"].append(session_view(r, states=states))
    names = folder_names(engine, paths.values())
    folders = []
    for key in order:
        info = names.get(key) or {
            "path": paths[key], "name": _base_name(paths[key]), "is_project": False, "project_id": None,
        }
        folders.append({
            "path": paths[key],
            "name": info["name"],
            "is_project": info["is_project"],
            "project_id": info["project_id"],
            "sessions": groups[key]["sessions"],
        })
    return {"folders": folders}


def waiting_sessions(engine: Any) -> list[tuple[str, dict[str, Any]]]:
    """``[(thread id, setup)]`` of every unarchived session with a "Resume
    when ready" wait (v1.368.0) — the wait loop's one read: the id and setup
    COLUMNS only (never a transcript), build threads only. BLOCKING."""
    from sqlmodel import select

    T = ChatThreadRecord
    out: list[tuple[str, dict[str, Any]]] = []
    with session_scope(engine) as db:
        stmt = select(T.id, T.setup_json).where(T.surface == BUILD_SURFACE).where(
            T.archived_at.is_(None)  # type: ignore[union-attr]
        )
        for tid, raw in db.exec(stmt):
            if not raw or "account_wait" not in raw:
                continue
            try:
                setup = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if isinstance(setup, dict) and isinstance(setup.get("account_wait"), dict):
                out.append((str(tid), setup))
    return out


def take_wait(engine: Any, thread_id: str, armed_at: str, account: str) -> bool:
    """END a "Resume when ready" wait for the continuation that is about to
    start (v1.368.0 review): in ONE locked write, True when the wait armed at
    ``armed_at`` for ``account`` is still the session's (it is removed, and
    the old limit card with it — the continuation's own end writes the next
    one); False when it is gone — the user cancelled it, chose another account
    or carried on themselves while the account was being re-checked, or
    another pass took it. The same lock as :func:`update`, so a Cancel lands
    either before (nothing starts) or after (the continuation already began).
    BLOCKING."""
    from ..daemon.routes.chat import _clean_setup

    if not armed_at or not account:
        return False
    with CONVERSATION_WRITE_LOCK, session_scope(engine) as db:
        row = db.get(ChatThreadRecord, str(thread_id or ""))
        if row is None or not is_build_thread(row) or getattr(row, "archived_at", None) is not None:
            return False
        setup = _setup(row)
        wait = setup.get("account_wait")
        if (
            not isinstance(wait, dict)
            or wait.get("armed_at") != armed_at
            or wait.get("account") != account
            or setup.get("account") != account
        ):
            return False
        setup.pop("account_wait", None)
        setup.pop("account_limit", None)
        row.setup_json = _clean_setup(setup)
        db.add(row)
        db.commit()
        return True


def reconcile_interrupted_turns(engine: Any) -> int:
    """Boot step (after ``durable_turns.reconcile_interrupted_turns``): a
    build turn that a crash or restart cut off has its record marked
    "interrupted" but may have NO reply line in its session (the turn's own
    writer never reached its end). Its line is written now — what it had
    streamed, marked interrupted, with the same sentence the chat's rejoin
    shows — so the next turn's history and the screen both say what
    happened. Only a session whose transcript holds that turn's QUESTION and
    no reply is touched. Returns the lines written. Never raises."""
    from sqlmodel import select

    from ..core.models import ChatTurnRecord
    from ..daemon.durable_turns import INTERRUPTED_DETAIL

    written = 0
    try:
        with session_scope(engine) as db:
            rows = list(db.exec(
                select(ChatTurnRecord.id, ChatTurnRecord.thread_id, ChatTurnRecord.frames_json)
                .where(ChatTurnRecord.status == "interrupted")
                .where(ChatTurnRecord.thread_id != "")
            ))
        for turn_id, thread_id, frames_json in rows:
            row = get_row(engine, thread_id or "")
            if row is None or has_turn_reply(row, turn_id):
                continue
            if not any(m.get("role") == "user" and m.get("turnId") == turn_id for m in _messages(row)):
                continue
            try:
                frames = json.loads(frames_json or "[]")
            except (TypeError, ValueError):
                frames = []
            text = []
            for frame in frames if isinstance(frames, list) else []:
                if isinstance(frame, list) and len(frame) == 2 and frame[0] == "reset":
                    # The lane discarded what it had streamed (a text-call
                    # nudge, a language rewrite) — the live turn's own
                    # writer (_ReplyWatch) drops it too (Phase 4 review).
                    text = []
                elif isinstance(frame, list) and len(frame) == 2 and frame[0] == "token":
                    try:
                        text.append(str(json.loads(frame[1]).get("text") or ""))
                    except (TypeError, ValueError, AttributeError):
                        continue
            line = {
                "role": "assistant", "content": "".join(text), "turnId": turn_id,
                "interrupted": True, "error": INTERRUPTED_DETAIL,
            }
            if append_lines(engine, thread_id, [line], title_from_first=False).get("ok"):
                written += 1
    except Exception:  # noqa: BLE001 — a boot step never blocks the boot
        log.warning("reconciling interrupted build turns failed", exc_info=True)
    return written


def folder_problem(folder: str) -> str | None:
    """Why ``folder`` cannot be a session's working folder, in words, or None.
    THE one rule (``fs_policy.root_problem``). BLOCKING (a writability probe)."""
    from ..core.fs_policy import root_problem

    text = str(folder or "").strip()
    if not text:
        return "Choose the folder this session works in."
    try:
        return root_problem(text)
    except Exception as exc:  # noqa: BLE001 — an unreadable path is a refusal
        return f"that folder cannot be used: {type(exc).__name__}"


def resolved_folder(folder: str) -> str:
    """The folder as stored: absolute and normalised (never resolved through
    links — the user's own spelling of a drive stays)."""
    try:
        return os.path.normpath(str(Path(str(folder).strip())))
    except (TypeError, ValueError):
        return str(folder or "").strip()


__all__ = [
    "LINE_KEYS",
    "MODES",
    "MODE_ACT",
    "MODE_PLAN",
    "NEW_SESSION_TITLE",
    "SESSION_PREFIX",
    "append_lines",
    "create",
    "end_session_programs",
    "folder_key",
    "folder_names",
    "folder_problem",
    "get_row",
    "history",
    "list_grouped",
    "rename_folder",
    "session_id_for",
    "session_view",
    "take_wait",
    "thread_of",
    "update",
    "waiting_sessions",
]
