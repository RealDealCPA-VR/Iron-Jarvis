"""Chats other apps report through Iron Jarvis's connector (v1.528.0).

Claude desktop keeps its Chat-tab conversations on claude.ai, not on this PC,
so Iron Jarvis cannot read them. Instead, once the owner connects Claude
desktop (``mcpserver/apps.py``), Claude records each chat itself through the
``chat_record`` tool (``workmap/connector_tools.py``), and the record lands
here. Codex can report the same way; its report adds a summary to the Codex
chat already read from Codex's own files.

THE RULES THIS MODULE KEEPS

* The app is the KEY's, never the model's: :func:`record` takes it from the
  grant the call came in on (``ToolContext.session_id`` is ``mcp:app:<app>``).
* Everything here is model-written by another app: bounded, one line where a
  line is wanted, and DATA wherever it is read back. It reaches a prompt only
  through the work record's lines (``workmap/block.py``), which promptguard
  scans; it reaches the screen only as text.
* Paths are kept only when absolute, and are never opened or checked on disk.
* A second report of the same chat updates its row (the app's own chat id
  when given, else the title's key on the same day), so a chat reported at
  each pause is one row.
* Nothing is ever deleted here by a report. The owner's Disconnect leaves the
  rows; ``forget_app`` removes them only when the owner asks.

BLOCKING (SQLite): every caller is off the event loop.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlmodel import select

from ..core.db import session_scope, storable_text
from ..core.ids import new_id, utcnow
from . import keys
from .models import ReportedChat

APP_CLAUDE = "claude-desktop"
APP_CODEX = "codex"
APPS: tuple[str, ...] = (APP_CLAUDE, APP_CODEX)
APP_LABELS = {APP_CLAUDE: "Claude desktop", APP_CODEX: "Codex"}

TITLE_CHARS = 120
SUMMARY_CHARS = 1200
LINK_CHARS = 500
CHAT_ID_CHARS = 200
FOLDERS_MAX = 5
FILES_MAX = 20
PATH_CHARS = 1000

#: The session id an app's calls carry (``mcpserver.server.harness_session_id``
#: of ``app:<app>``).
SESSION_PREFIX = "mcp:app:"

NO_TITLE = "Say what the chat was about (title)."
NOT_AN_APP = "Only a connected app can record a chat here."

_ABSOLUTE = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/]{1,2}[^\\/])")
_SPACES = re.compile(r"\s+")
_LINK = re.compile(r"^https://[^\s]+$")


def app_of_session(session_id: Any) -> str:
    """The connected app a tool call came from, or "" (not an app's call)."""
    text = str(session_id or "")
    if not text.startswith(SESSION_PREFIX):
        return ""
    app = text[len(SESSION_PREFIX):]
    return app if app in APPS else ""


def _line(text: Any, cap: int) -> str:
    return storable_text(_SPACES.sub(" ", str(text or "")).strip())[:cap]


def _lines(text: Any, cap: int) -> str:
    """Plain text with its line breaks, blank runs folded, cut at ``cap``."""
    rows = [_SPACES.sub(" ", r).strip() for r in str(text or "").replace("\r", "\n").split("\n")]
    out = "\n".join(r for r in rows if r)
    return storable_text(out)[:cap]


def _paths(value: Any, most: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in value if isinstance(value, list) else []:
        path = _line(item, PATH_CHARS)
        if not path or not _ABSOLUTE.match(path):
            continue
        key = keys.folder_key(path)
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
        if len(out) >= most:
            break
    return out


def chat_key(app: str, title: str, chat_id: str, when: datetime) -> str:
    """What makes two reports one chat."""
    if chat_id:
        return f"id:{chat_id}"
    words = " ".join(re.findall(r"[a-z0-9]+", title.lower()))[:80]
    return f"day:{when.date().isoformat()}:{words}"


def key_for_args(app: str, args: dict[str, Any], *, now: datetime | None = None) -> str:
    """The ``chat_key`` :func:`record` would use for these arguments ("" when
    it would refuse them)."""
    title = _line(args.get("title"), TITLE_CHARS)
    if app not in APPS or not title:
        return ""
    return chat_key(app, title, _line(args.get("chat_id"), CHAT_ID_CHARS), now or utcnow())


_KEPT = ("title", "summary", "folders_json", "files_json", "link", "reports", "created_at", "updated_at")


def snapshot(engine: Any, app: str, key: str) -> dict[str, Any] | None:
    """The stored columns of one reported chat, for an Undo (None = none)."""
    with session_scope(engine) as db:
        row = db.exec(select(ReportedChat).where(ReportedChat.app == app, ReportedChat.chat_key == key)).first()
        if row is None:
            return None
        out = {k: getattr(row, k) for k in _KEPT}
        for k in ("created_at", "updated_at"):
            out[k] = _iso(out[k])
        return {"id": row.id, **out}


def restore(engine: Any, app: str, key: str, before: dict[str, Any] | None) -> str:
    """Undo one report: remove the row it made, or put back the one it
    replaced. Plain words for the Undo's answer."""
    with session_scope(engine) as db:
        row = db.exec(select(ReportedChat).where(ReportedChat.app == app, ReportedChat.chat_key == key)).first()
        if before is None:
            if row is not None:
                db.delete(row)
                db.commit()
            return "Removed the recorded chat."
        if row is None:
            row = ReportedChat(id=str(before.get("id") or new_id("rchat")), app=app, chat_key=key)
        for k in _KEPT:
            value = before.get(k)
            if k in ("created_at", "updated_at"):
                value = _at(str(value or "")) or utcnow()
            setattr(row, k, value)
        db.add(row)
        db.commit()
        return "Put the recorded chat back as it was."


def record(engine: Any, app: str, args: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Keep one reported chat (or update it). Raises ValueError with a plain
    sentence for a call that cannot be kept."""
    if app not in APPS:
        raise ValueError(NOT_AN_APP)
    title = _line(args.get("title"), TITLE_CHARS)
    if not title:
        raise ValueError(NO_TITLE)
    summary = _lines(args.get("summary"), SUMMARY_CHARS)
    folders = _paths(args.get("folders"), FOLDERS_MAX)
    files = _paths(args.get("files"), FILES_MAX)
    link = _line(args.get("link"), LINK_CHARS)
    if link and not _LINK.match(link):
        link = ""  # only a web address: never a file or a program to start
    given_id = _line(args.get("chat_id"), CHAT_ID_CHARS)
    when = now or utcnow()
    key = chat_key(app, title, given_id, when)
    with session_scope(engine) as db:
        row = db.exec(
            select(ReportedChat).where(ReportedChat.app == app, ReportedChat.chat_key == key)
        ).first()
        updated = row is not None
        if row is None:
            row = ReportedChat(id=new_id("rchat"), app=app, chat_key=key, created_at=when, reports=0)
        row.title = title
        # A later report with no summary or folders keeps what was said before.
        row.summary = summary or row.summary
        row.folders_json = json.dumps(folders, ensure_ascii=True) if folders else (row.folders_json or "[]")
        row.files_json = json.dumps(files, ensure_ascii=True) if files else (row.files_json or "[]")
        row.link = link or row.link
        row.reports = int(row.reports or 0) + 1
        row.updated_at = when
        db.add(row)
        db.commit()
        db.refresh(row)
        return {**view(row), "updated": updated}


def _json_list(text: Any) -> list[str]:
    try:
        data = json.loads(text or "[]")
    except (TypeError, ValueError):
        return []
    return [str(x) for x in data if isinstance(x, str)] if isinstance(data, list) else []


def view(row: ReportedChat) -> dict[str, Any]:
    folders = _json_list(row.folders_json)
    return {
        "id": row.id,
        "app": row.app,
        "title": row.title,
        "summary": row.summary,
        "folder": folders[0] if folders else "",
        "folders": folders,
        "files": _json_list(row.files_json),
        "link": row.link,
        "reports": int(row.reports or 0),
        "started": _iso(row.created_at),
        "ended": _iso(row.updated_at),
    }


def _iso(value: Any) -> str:
    if not isinstance(value, datetime):
        return ""
    v = value if value.tzinfo is None else value.astimezone(timezone.utc).replace(tzinfo=None)
    return v.replace(tzinfo=timezone.utc).isoformat()


def rows(engine: Any, app: str | None = None, *, limit: int = 500) -> list[dict[str, Any]]:
    """Reported chats, newest first. A read that fails RAISES (never "none")."""
    with session_scope(engine) as db:
        stmt = select(ReportedChat)
        if app:
            stmt = stmt.where(ReportedChat.app == app)
        stmt = stmt.order_by(ReportedChat.updated_at.desc()).limit(limit)  # type: ignore[attr-defined]
        return [view(r) for r in db.exec(stmt)]


def stats(engine: Any) -> dict[str, dict[str, Any]]:
    """Per app: how many chats it reported and when it last did."""
    out: dict[str, dict[str, Any]] = {a: {"count": 0, "last": ""} for a in APPS}
    with session_scope(engine) as db:
        for app, updated in db.exec(select(ReportedChat.app, ReportedChat.updated_at)):
            if app not in out:
                continue
            out[app]["count"] += 1
            stamp = _iso(updated)
            if stamp > out[app]["last"]:
                out[app]["last"] = stamp
    return out


def _at(iso: str) -> datetime | None:
    try:
        v = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return v if v.tzinfo is None else v.astimezone(timezone.utc).replace(tzinfo=None)


#: A Codex report belongs to the Codex chat in its folder that was going at
#: the time: started at most this long before the chat began is refused, and
#: at most this long after its last line.
CODEX_MATCH_BEFORE = timedelta(minutes=1)
CODEX_MATCH_AFTER = timedelta(minutes=30)


def match_codex(reports: list[dict[str, Any]], chats: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """``{codex chat id: report}``: each Codex report given to the ONE Codex
    chat in the same folder whose time it falls in (the nearest end wins). A
    report that matches no chat is not shown anywhere: Codex's own files are
    the record of its chats (a report from a Codex that Iron Jarvis itself
    started matches nothing, because those chats are left out)."""
    out: dict[str, dict[str, Any]] = {}
    for rep in reports:
        when = _at(rep.get("ended") or "")
        fkey = keys.folder_key(rep.get("folder") or "")
        if when is None or not fkey:
            continue
        best: tuple[timedelta, str] | None = None
        for chat in chats:
            if keys.folder_key(chat.get("folder") or "") != fkey:
                continue
            start, end = _at(chat.get("started") or ""), _at(chat.get("ended") or "")
            if start is None or end is None:
                continue
            if not (start - CODEX_MATCH_BEFORE <= when <= end + CODEX_MATCH_AFTER):
                continue
            gap = abs(when - end)
            if best is None or gap < best[0]:
                best = (gap, str(chat.get("id") or ""))
        if best is not None and best[1] and best[1] not in out:
            out[best[1]] = rep
    return out


__all__ = [
    "APPS", "APP_CLAUDE", "APP_CODEX", "APP_LABELS", "FILES_MAX", "FOLDERS_MAX", "SUMMARY_CHARS",
    "TITLE_CHARS", "app_of_session", "chat_key", "match_codex", "record", "rows", "stats", "view",
]
