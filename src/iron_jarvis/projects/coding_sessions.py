"""A project's CODING SESSIONS, from both Code views (v1.414.0).

A coding session never carried a project: Chat › Code rows are chat threads
with ``surface="build"`` and ``project_id=None`` (``harness/sessions.create``),
and Build › Code rows (``hosted/models.HostedSessionRecord``) have no project
field at all. What both DO carry is a folder. So the project is decided at
READ time, by the ONE rule (``projects/locate.best_project``: the most
specific ACTIVE project root containing the folder — the same answer
``project_for_path`` gives), and nothing is stored: a session that moves
under a project's root (a new project made over its folder) shows up there,
one whose project is archived drops out, with no write anywhere.

READ-ONLY on purpose. ``hosted/`` belongs to the Build › Code view and is not
changed here: its rows are read with one plain SELECT of the columns a row
needs (a missing table — the view never used — reads as no rows; the
store's own ``list_rows`` would CREATE the table, a write). Running / waiting
come from the two in-memory registries the views themselves read
(``core/turns.TURNS.thread_states`` for Chat › Code, the hosted service's
``live`` map for Build › Code), copied, never created: a daemon that never
opened the Build › Code view has no hosted service and nothing live there.

BOUNDED: the newest :data:`SCAN_MAX` rows of each kind are looked at
(``updated_at`` is indexed on both tables) and a transcript is never read —
whether a Chat › Code session has a finished reply is one SQLite JSON read of
its LAST line. BLOCKING (SQLite): every caller on the event loop goes through
``asyncio.to_thread``. Never raises: a store that cannot answer is "no coding
sessions".
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Iterable
from urllib.parse import quote

from sqlalchemy import case, func, literal_column
from sqlmodel import select

from ..core.db import session_scope
from ..core.models import BUILD_SURFACE, ChatThreadRecord, Project

log = logging.getLogger(__name__)

#: Newest rows of EACH kind looked at per read.
SCAN_MAX = 300
#: Coding sessions listed per project (newest first).
LIST_MAX = 30
#: The two views a coding session lives in.
VIEW_CHAT_CODE = "chat_code"
VIEW_BUILD_CODE = "build_code"
#: Where each opens — the dashboard's ``buildSessionHref`` (lib/buildLinks,
#: ``/chat?view=code&session=<id>``) and ``buildCodeHref`` (lib/buildApp,
#: ``/terminals?view=code&code=<id>``). Both sides are pinned to these exact
#: strings (tests/test_project_coding_sessions_v1414.py and
#: dashboard/__tests__/project-coding-sessions-v1414.test.tsx).
CHAT_CODE_HREF = "/chat?view=code&session="
BUILD_CODE_HREF = "/terminals?view=code&code="
#: What a row is (the canon word, lib/buildWords CODING_SESSION).
KIND = "coding_session"

#: A row's state, one word each.
WORKING = "working"
WAITING = "waiting"
FINISHED = "finished"
IDLE = "idle"


def link_for(view: str, sid: str) -> str:
    base = BUILD_CODE_HREF if view == VIEW_BUILD_CODE else CHAT_CODE_HREF
    return base + quote(str(sid), safe="")


def naive(value: Any) -> datetime | None:
    """SQLite drops tzinfo; everything compares in naive UTC."""
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _iso(value: Any) -> str | None:
    v = naive(value)
    return v.replace(tzinfo=timezone.utc).isoformat() if v is not None else None


# --------------------------------------------------------------------------- #
# live state (copied, never created)
# --------------------------------------------------------------------------- #
def chat_code_states() -> dict[str, dict[str, bool]]:
    """``{thread id: {running, waiting}}`` of every chat/coding thread with a
    turn in flight — the snapshot ``GET /build/sessions`` reads."""
    try:
        from ..core.turns import TURNS

        return TURNS.thread_states()
    except Exception:  # noqa: BLE001
        return {}


def build_code_states(platform: Any) -> dict[str, dict[str, bool]]:
    """``{session id: {running, waiting}}`` from the Build › Code service's
    ``live`` map — only when the service EXISTS (``getattr``, never
    ``service_for``, which would make one). ``list(dict.items())`` is one
    atomic copy, so a worker thread may take it."""
    svc = getattr(platform, "hosted", None)
    live = getattr(svc, "live", None)
    if not isinstance(live, dict):
        return {}
    try:
        items = list(live.items())
    except RuntimeError:  # changed size mid-copy: say nothing rather than half
        return {}
    out: dict[str, dict[str, bool]] = {}
    for sid, lv in items:
        try:
            turn = getattr(lv, "turn", None)
            running = bool(turn is not None and getattr(turn, "running", False))
            waiting = bool(getattr(lv, "asks", None))
        except Exception:  # noqa: BLE001
            continue
        if running or waiting:
            out[str(sid)] = {"running": running, "waiting": waiting}
    return out


# --------------------------------------------------------------------------- #
# the rows
# --------------------------------------------------------------------------- #
def _projects(db: Any) -> list[Any]:
    return [
        SimpleNamespace(id=p.id, root=p.root or "", status=p.status or "active")
        for p in db.exec(select(Project))
    ]


def _chat_code_rows(db: Any, scan: int) -> list[dict[str, Any]]:
    from ..harness.sessions import NEW_SESSION_TITLE, checkout_of, is_folderless
    from ..providers.egress import setup_says_local_only

    T = ChatThreadRecord
    msgs = T.messages_json
    valid = func.json_valid(msgs) == 1
    # The LAST line only (``$[#-1]``): its role says whether a reply came;
    # an ``error``/``errorTitle`` key says it failed; ``interrupted`` that it
    # was cut off. ``json_type`` answers NULL for a key that is not there.
    last_role = case((valid, func.json_extract(msgs, "$[#-1].role")), else_=None)
    last_err = case(
        (valid, func.coalesce(
            func.json_type(msgs, "$[#-1].error"), func.json_type(msgs, "$[#-1].errorTitle"),
        )),
        else_=None,
    )
    last_cut = case((valid, func.json_extract(msgs, "$[#-1].interrupted")), else_=None)
    stmt = (
        select(T.id, T.title, T.updated_at, T.setup_json, last_role, last_err, last_cut)
        .where(T.surface == BUILD_SURFACE)
        .where(T.archived_at.is_(None))  # type: ignore[union-attr]
        .order_by(T.updated_at.desc(), literal_column("rowid").desc())  # type: ignore[attr-defined]
        .limit(scan)
    )
    out: list[dict[str, Any]] = []
    for tid, title, updated, setup, role, err, cut in db.exec(stmt):
        row = SimpleNamespace(setup_json=setup or "")
        out.append({
            "view": VIEW_CHAT_CODE,
            "id": str(tid),
            "title": str(title or "").strip() or NEW_SESSION_TITLE,
            "tool": "",
            "updated": naive(updated),
            # The folder the session BELONGS to (a worktree session's picked
            # folder — a worktree sits outside the project's root), the rule
            # its own list groups by. A session started with no folder works
            # in its own folder under the chat files folder: no project.
            "folder": "" if is_folderless(row) else checkout_of(row),
            "local_only": setup_says_local_only(setup or ""),
            "_role": str(role or ""),
            "_error": err is not None and str(err) != "null",
            "_cut": bool(cut) and str(cut) not in ("0", "false"),
        })
    return out


def _build_code_rows(db: Any, scan: int) -> list[dict[str, Any]]:
    try:
        from ..hosted.models import HostedSessionRecord as H
        from ..hosted.words import LABELS
    except Exception:  # noqa: BLE001 — no Build › Code view in this build
        return []
    stmt = (
        select(H.id, H.harness, H.folder, H.title, H.updated_at, H.harness_session_id, H.last_error)
        .where(H.archived_at.is_(None))  # type: ignore[union-attr]
        .order_by(H.updated_at.desc(), literal_column("rowid").desc())  # type: ignore[attr-defined]
        .limit(scan)
    )
    try:
        rows = list(db.exec(stmt))
    except Exception:  # noqa: BLE001 — the table does not exist yet: no rows
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return []
    out: list[dict[str, Any]] = []
    for sid, harness, folder, title, updated, hsid, last_error in rows:
        tool = LABELS.get(str(harness or ""), str(harness or ""))
        out.append({
            "view": VIEW_BUILD_CODE,
            "id": str(sid),
            "title": str(title or "").strip() or (f"{tool} session" if tool else "Session"),
            "tool": tool,
            "updated": naive(updated),
            "folder": str(folder or ""),
            # The real Claude Code / Codex talk to their vendor: never local.
            "local_only": False,
            # The tool said its own session id once a conversation existed:
            # the only "it answered" this view keeps without its event log.
            "_role": "assistant" if str(hsid or "").strip() else "",
            "_error": bool(str(last_error or "").strip()),
            "_cut": False,
        })
    return out


def _state(row: dict[str, Any], live: dict[str, bool] | None) -> tuple[str, str | None]:
    live = live or {}
    if live.get("waiting"):
        return WAITING, None
    if live.get("running"):
        return WORKING, None
    if row["_role"] != "assistant":
        return IDLE, None
    if row["_error"]:
        return FINISHED, "failed"
    if row["_cut"]:
        return FINISHED, "cancelled"
    return FINISHED, "completed"


def _view(row: dict[str, Any], live: dict[str, bool] | None) -> dict[str, Any]:
    state, outcome = _state(row, live)
    return {
        "kind": KIND,
        "view": row["view"],
        "id": row["id"],
        "key": f"coding:{row['view']}:{row['id']}",
        "title": row["title"],
        "tool": row["tool"],
        "state": state,
        "outcome": outcome,
        "updated_at": _iso(row["updated"]),
        "link": link_for(row["view"], row["id"]),
        "local_only": bool(row["local_only"]),
        "folder": row["folder"],
        "_updated": row["updated"],
    }


def read_rows(engine: Any, *, scan: "int | None" = None) -> tuple[list[Any], list[dict[str, Any]]]:
    """``(projects, rows)`` — one read of the projects and the newest ``scan``
    rows of each view (:data:`SCAN_MAX`, read at CALL time). BLOCKING."""
    scan = SCAN_MAX if scan is None else int(scan)
    with session_scope(engine) as db:
        projects = _projects(db)
        rows = _chat_code_rows(db, scan) + _build_code_rows(db, scan)
    return projects, rows


def for_project(
    engine: Any,
    project_id: str,
    *,
    platform: Any = None,
    scan: "int | None" = None,
    limit: int = LIST_MAX,
    with_state: bool = True,
) -> list[dict[str, Any]]:
    """The coding sessions (both views) whose folder belongs to
    ``project_id``, newest first, at most ``limit``. Each row: ``{kind,
    view, id, key, title, tool, state, outcome, updated_at, link,
    local_only, folder}`` (plus ``_updated``, a naive datetime, for callers
    that merge by time — :func:`public` drops it). Archived sessions are
    never listed. Never raises."""
    pid = str(project_id or "")
    if not pid or engine is None:
        return []
    from .locate import best_project

    try:
        projects, rows = read_rows(engine, scan=scan)
    except Exception:  # noqa: BLE001 — a store that cannot answer: none
        log.debug("could not read the coding sessions", exc_info=True)
        return []
    chat_live = chat_code_states() if with_state else {}
    app_live = build_code_states(platform) if with_state else {}
    out: list[dict[str, Any]] = []
    for r in rows:
        if not r["folder"]:
            continue
        best = best_project(projects, r["folder"])
        if best is None or best.id != pid:
            continue
        live = (chat_live if r["view"] == VIEW_CHAT_CODE else app_live).get(r["id"])
        out.append(_view(r, live))
    out.sort(key=lambda v: v["_updated"] or datetime.min, reverse=True)
    return out[: max(0, int(limit))]


def public(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """The rows as an API answers them (the private sort key dropped)."""
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]


__all__ = [
    "BUILD_CODE_HREF",
    "CHAT_CODE_HREF",
    "KIND",
    "LIST_MAX",
    "SCAN_MAX",
    "VIEW_BUILD_CODE",
    "VIEW_CHAT_CODE",
    "build_code_states",
    "chat_code_states",
    "for_project",
    "link_for",
    "naive",
    "public",
    "read_rows",
]
