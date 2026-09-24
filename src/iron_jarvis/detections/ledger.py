"""This app's tool ledger as detection EVENTS (v1.290.0).

Maps ``ToolInvocation`` rows onto the shared EVENT shape (source
``"ironjarvis"``):

* the command runners — ``shell``, ``run_code`` (its CODE is the command the
  rules read), ``repl``, ``pane_send`` and every custom tool (a
  ``DynamicToolRecord`` name) -> ``command.executed`` with the command text;
* the file readers / writers -> ``file.read`` / ``file.write`` with the path
  (a tool that reads TWO files — ``compare_documents``,
  ``excel_accounts_diff`` — yields one event per file). No registered tool
  deletes a file, so nothing maps to ``file.delete`` today;
* the web tools (``web_fetch``, ``browse``, ``web_extract``, ``web_look``,
  ``browser_navigate``, ``browser_create_tab``) and ``pixio_upload`` (which
  sends a local file out) -> ``network.request`` with the url (and path);
* a REFUSED call -> ``approval.denied``, still carrying the command / path /
  url it asked for, so a correlation rule can compare it with what ran next;
* anything else -> ``tool.called``.

A refusal is known from the ``tool.denied`` event the registry publishes for
it (persisted to ``EventRecord`` with the row's ``invocation_id``) — the
authoritative record, because a user's "no" on an ask-tier tool leaves the
row's verdict at ``ask``, not ``deny``. A ``deny``-verdict row counts too
(except the registry's "unknown tool" rows, which refused nothing), and so do
the registry's own refusal sentences on an ``ask`` row whose event was pruned.

CHAT IS MANY SESSIONS, NOT ONE. Both chat lanes ledger every tool call under
session id ``"chat"`` (agent_run_id ``"chat"`` too), so read raw, every chat
conversation the user ever had would be ONE session to a correlation rule.
Each chat turn does leave one ``AgentRun`` row (``session_id="chat"``,
``created_at`` = the turn's start, ``finished_at`` = its end — written by
``chat_turn._persist_chat_usage`` in both lanes), so a chat row is assigned to
the turn whose window holds it and reported as ``"chat:<run id>"``. Two turns
running at the same moment in two windows overlap; a row is then given to the
latest-starting turn whose window holds it. A row no turn covers stays
``"chat"``.

Every event carries ``text`` = the tool's output (the ledger already caps it
at 4000 chars) so the injection rules can read what came back.

SYNC and blocking (SQLite): route callers hop through ``asyncio.to_thread``.
"""

from __future__ import annotations

import json
from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import literal_column
from sqlmodel import select

from ..core.db import session_scope
from ..core.models import (
    AgentRun,
    DynamicToolRecord,
    EventRecord,
    PermissionMode,
    ToolInvocation,
)

SOURCE = "ironjarvis"
TEXT_CAP = 4000

#: Hard ceiling on one session's events (a runaway loop can ledger a lot) —
#: the NEWEST ones are kept.
SESSION_EVENT_CAP = 20000

#: The session id both chat lanes ledger under, and the prefix a chat TURN is
#: reported with (``"chat:<AgentRun id>"``).
CHAT_SESSION = "chat"
CHAT_TURN_PREFIX = "chat:"
#: A tool row is recorded a moment before the turn's run row is written.
_TURN_SLACK = timedelta(seconds=2)

#: Insertion order breaks ``created_at`` ties (the clock ticks every 15.6 ms
#: on Windows; a random id would shuffle a tie).
_ROWID = literal_column("toolinvocation.rowid")

COMMAND_TOOLS: dict[str, tuple[str, ...]] = {
    "shell": ("command",),
    "run_code": ("code",),
    "repl": ("code", "source", "expression"),
    "pane_send": ("text",),
}

FILE_READ_TOOLS = frozenset(
    {
        "read_file",
        "grep",
        "read_document",
        "extract_pdf",
        "excel_read",
        "excel_profile",
        "excel_query",
        "excel_formula_check",
        "excel_sheet_spec",
        "excel_accounts_diff",
        "pdf_form_fields",
        "view_image",
        "image_info",
        "redact_scan",
        "file_search",
        "code_search",
        "compare_documents",
        "batch_documents",
    }
)
FILE_WRITE_TOOLS = frozenset(
    {
        "write_file",
        "edit_file",
        "rename_file",
        "write_document",
        "docx_edit",
        "excel_edit",
        "excel_apply_spec",
        "pdf_arrange",
        "pdf_split",
        "pdf_form_fill",
        "convert_document",
        "image_convert",
        "image_resize",
        "redact_pii",
    }
)
NETWORK_TOOLS = frozenset(
    {
        "web_fetch",
        "browse",
        "web_extract",
        "web_look",
        "browser_navigate",
        "browser_create_tab",
        "pixio_upload",
    }
)
#: Tools that read a SECOND file, under this argument.
SECOND_PATH = {"compare_documents": "path_b", "excel_accounts_diff": "path_b"}

_PATH_KEYS = (
    "path", "file_path", "file", "path_a", "source", "input_path", "src",
    "folder", "root", "dir", "directory",
)
_WRITE_PATH_KEYS = ("new_path", "output_path", "out_path", "dest", "destination")

#: The registry's own refusal sentences (tools/permissions.py + registry.py),
#: for an ``ask`` row whose ``tool.denied`` event has been pruned.
_REFUSAL_MARKERS = (
    "rejected by user",
    "requires approval",
    "needs approval and nothing here could ask",
    "is not one of this agent's tools",
    "denied by policy",
)


def _args(row: ToolInvocation) -> dict[str, Any]:
    try:
        value = json.loads(row.args_json or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _first(args: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _ts(value: Any) -> str | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _custom_tools(db) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for rec in db.exec(select(DynamicToolRecord)).all():
        try:
            argv = [str(a) for a in json.loads(rec.argv_json or "[]")]
        except (TypeError, ValueError):
            argv = []
        out[rec.name] = argv
    return out


def _denied_ids(db, session_id: str | None, since: datetime | None) -> set[str]:
    stmt = select(EventRecord.payload_json).where(EventRecord.type == "tool.denied")
    if session_id is not None:
        stmt = stmt.where(EventRecord.session_id == session_id)
    if since is not None:
        stmt = stmt.where(EventRecord.created_at >= since)
    ids: set[str] = set()
    for payload in db.exec(stmt).all():
        try:
            inv = json.loads(payload or "{}").get("invocation_id")
        except (TypeError, ValueError, AttributeError):
            continue
        if inv:
            ids.add(str(inv))
    return ids


def is_denial(row: ToolInvocation, denied_ids: set[str]) -> bool:
    """Was this call REFUSED (never ran)? See the module docstring."""
    if row.id in denied_ids:
        return True
    output = row.output or ""
    verdict = getattr(row.verdict, "value", row.verdict)
    if verdict == PermissionMode.DENY.value:
        return not output.startswith("unknown tool '")
    if verdict == PermissionMode.ASK.value and not row.ok:
        return any(marker in output for marker in _REFUSAL_MARKERS)
    return False


def to_events(
    row: ToolInvocation,
    *,
    denied: bool = False,
    custom: dict[str, list[str]] | None = None,
    session_id: str | None = None,
) -> list[dict[str, Any]]:
    """One ledger row -> its normalized EVENT(s) (two for a two-file read)."""
    args = _args(row)
    name = row.tool or ""
    event: dict[str, Any] = {
        "session_id": session_id or row.session_id,
        "source": SOURCE,
        "ts": _ts(row.created_at),
        "tool": name,
        "ok": bool(row.ok),
        "ref": row.id,
        "text": (row.output or "")[:TEXT_CAP],
    }
    action = "tool.called"
    custom = custom or {}
    if name in COMMAND_TOOLS or name in custom:
        if name in COMMAND_TOOLS:
            command = _first(args, COMMAND_TOOLS[name])
        else:
            # A custom tool runs its argv template filled from its params:
            # the template plus the values is the command it asked for.
            values = " ".join(str(v) for v in args.values() if v not in (None, ""))
            command = " ".join(custom[name] + ([values] if values else []))
        if name == "run_code" and args.get("language"):
            event["language"] = str(args.get("language"))
        event["command"] = command
        action = "command.executed"
    elif name in FILE_READ_TOOLS:
        event["path"] = _first(args, _PATH_KEYS)
        action = "file.read"
    elif name in FILE_WRITE_TOOLS:
        event["path"] = _first(args, _WRITE_PATH_KEYS + _PATH_KEYS)
        action = "file.write"
    elif name in NETWORK_TOOLS:
        event["url"] = _first(args, ("url",))
        path = _first(args, _PATH_KEYS)
        if path:
            event["path"] = path
        action = "network.request"
    else:
        path = _first(args, _PATH_KEYS)
        if path:
            event["path"] = path
    if denied:
        action = "approval.denied"
    event["action"] = action
    events = [event]
    second = SECOND_PATH.get(name)
    if second:
        other = _first(args, (second,))
        if other and other != event.get("path"):
            events.append({**event, "path": other})
    return events


def to_event(row: ToolInvocation, **kw) -> dict[str, Any]:
    """The first event of :func:`to_events` (the common one-event case)."""
    return to_events(row, **kw)[0]


class _Turns:
    """The chat turns (AgentRun rows of session "chat") — see module doc."""

    def __init__(self, db, lo: datetime | None = None, hi: datetime | None = None):
        stmt = select(AgentRun.id, AgentRun.created_at, AgentRun.finished_at).where(
            AgentRun.session_id == CHAT_SESSION
        )
        if lo is not None:
            stmt = stmt.where(AgentRun.finished_at >= lo - _TURN_SLACK)
        if hi is not None:
            stmt = stmt.where(AgentRun.created_at <= hi + _TURN_SLACK)
        rows = sorted(
            (r for r in db.exec(stmt).all() if r[1] is not None and r[2] is not None),
            key=lambda r: r[1],
        )
        self._ids = [r[0] for r in rows]
        self._starts = [r[1] - _TURN_SLACK for r in rows]
        self._ends = [r[2] + _TURN_SLACK for r in rows]

    def session_for(self, created_at: datetime | None) -> str:
        if created_at is None or not self._ids:
            return CHAT_SESSION
        i = bisect_right(self._starts, created_at) - 1
        for k in range(i, max(-1, i - 16), -1):  # overlapping turns: look back a few
            if self._ends[k] >= created_at:
                return CHAT_TURN_PREFIX + self._ids[k]
        return CHAT_SESSION


def _rows_to_events(
    db, rows: list[ToolInvocation], denied_ids: set[str], *, fixed: str | None = None
) -> list[dict]:
    custom = _custom_tools(db)
    turns: _Turns | None = None
    if fixed is None and any(r.session_id == CHAT_SESSION for r in rows):
        stamps = [r.created_at for r in rows if r.session_id == CHAT_SESSION]
        turns = _Turns(db, min(stamps), max(stamps))
    out: list[dict] = []
    for r in rows:
        sid = fixed
        if sid is None and turns is not None and r.session_id == CHAT_SESSION:
            sid = turns.session_for(r.created_at)
        out.extend(
            to_events(r, denied=is_denial(r, denied_ids), custom=custom, session_id=sid)
        )
    return out


def _newest(db, stmt, cap: int) -> list[ToolInvocation]:
    """The newest *cap* rows of *stmt*, handed back oldest first."""
    rows = list(
        db.exec(
            stmt.order_by(ToolInvocation.created_at.desc(), _ROWID.desc()).limit(int(cap))
        ).all()
    )
    rows.reverse()
    return rows


def events_for_session(engine, session_id: str) -> list[dict]:
    """The ledgered tool calls of *session_id* (the newest
    ``SESSION_EVENT_CAP``), oldest first, as EVENTS.

    ``"chat:<run id>"`` is ONE chat turn (the rows inside that run's window);
    plain ``"chat"`` is every chat row, each reported under its turn."""
    with session_scope(engine) as db:
        if session_id.startswith(CHAT_TURN_PREFIX):
            run = db.get(AgentRun, session_id[len(CHAT_TURN_PREFIX):])
            if run is None or run.session_id != CHAT_SESSION or run.finished_at is None:
                return []
            lo, hi = run.created_at - _TURN_SLACK, run.finished_at + _TURN_SLACK
            rows = _newest(
                db,
                select(ToolInvocation).where(
                    ToolInvocation.session_id == CHAT_SESSION,
                    ToolInvocation.created_at >= lo,
                    ToolInvocation.created_at <= hi,
                ),
                SESSION_EVENT_CAP,
            )
            return _rows_to_events(
                db, rows, _denied_ids(db, CHAT_SESSION, lo), fixed=session_id
            )
        rows = _newest(
            db,
            select(ToolInvocation).where(ToolInvocation.session_id == session_id),
            SESSION_EVENT_CAP,
        )
        since = rows[0].created_at if rows else None
        return _rows_to_events(db, rows, _denied_ids(db, session_id, since))


def recent_events(engine, since_hours: float = 24, limit: int = 5000) -> list[dict]:
    """The newest *limit* tool calls of the last *since_hours*, oldest first."""
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        hours=float(since_hours)
    )
    with session_scope(engine) as db:
        rows = _newest(
            db, select(ToolInvocation).where(ToolInvocation.created_at >= since), limit
        )
        return _rows_to_events(db, rows, _denied_ids(db, None, since))
