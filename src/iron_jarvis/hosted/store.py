"""The Code view's session rows (v1.409.0). BLOCKING SQLite — every caller
on the event loop goes through ``asyncio.to_thread``.

Order is newest first by ``updated_at`` and, on a tie (Windows' clock ticks
every 15.6 ms, so two rows written together share a stamp — the v1.286.0
rule), the SQLite ``rowid`` (insertion order), never a random id.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import literal_column
from sqlmodel import select

from ..core.db import session_scope
from ..core.ids import utcnow
from .models import HostedSessionRecord

log = logging.getLogger(__name__)

#: The fields PATCH may change.
EDITABLE = ("title", "model", "effort", "mode")


def ensure_table(engine: Any) -> None:
    """The table exists (a late model on a DB made before this release)."""
    try:
        HostedSessionRecord.__table__.create(bind=engine, checkfirst=True)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 — the next read says what is wrong
        log.debug("could not create the hosted session table", exc_info=True)


def create(engine: Any, **fields: Any) -> HostedSessionRecord:
    ensure_table(engine)
    with session_scope(engine) as db:
        row = HostedSessionRecord(**fields)
        db.add(row)
        db.commit()
        db.refresh(row)
        db.expunge(row)
        return row


def get(engine: Any, sid: str) -> HostedSessionRecord | None:
    ensure_table(engine)
    with session_scope(engine) as db:
        row = db.get(HostedSessionRecord, str(sid or ""))
        if row is not None:
            db.expunge(row)
        return row


def list_rows(engine: Any, *, archived: bool = False, folder: str = "") -> list[HostedSessionRecord]:
    ensure_table(engine)
    with session_scope(engine) as db:
        stmt = select(HostedSessionRecord)
        if not archived:
            stmt = stmt.where(HostedSessionRecord.archived_at == None)  # noqa: E711
        stmt = stmt.order_by(
            HostedSessionRecord.updated_at.desc(),  # type: ignore[attr-defined]
            literal_column("rowid").desc(),
        )
        rows = list(db.exec(stmt))
        for r in rows:
            db.expunge(r)
    if folder:
        from .service import same_folder

        rows = [r for r in rows if same_folder(r.folder, folder)]
    return rows


def update(engine: Any, sid: str, **fields: Any) -> HostedSessionRecord | None:
    """Set ``fields`` on the row and bump ``updated_at`` (unless one is
    given). None when the row is gone."""
    ensure_table(engine)
    with session_scope(engine) as db:
        row = db.get(HostedSessionRecord, str(sid or ""))
        if row is None:
            return None
        for key, value in fields.items():
            setattr(row, key, value)
        if "updated_at" not in fields:
            row.updated_at = utcnow()
        db.add(row)
        db.commit()
        db.refresh(row)
        db.expunge(row)
        return row


def delete(engine: Any, sid: str) -> bool:
    ensure_table(engine)
    with session_scope(engine) as db:
        row = db.get(HostedSessionRecord, str(sid or ""))
        if row is None:
            return False
        db.delete(row)
        db.commit()
        return True


def account_record(row: HostedSessionRecord) -> dict[str, Any]:
    """The recorded account (``pane_accounts`` record) — {} = this PC's login
    with no account recorded."""
    try:
        data = json.loads(row.account_json or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _iso(when: Any) -> str | None:
    if when is None:
        return None
    try:
        from datetime import timezone

        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return when.isoformat().replace("+00:00", "Z")
    except Exception:  # noqa: BLE001
        return None


def view(row: HostedSessionRecord) -> dict[str, Any]:
    """The session as the API shows it — never the account's home folder."""
    from .jarvis_tools import takes

    acc = account_record(row)
    out = _view(row, acc)
    if not takes(row.harness):
        # v1.441.0 (review A5): this tool said it takes no Iron Jarvis tools
        # (Antigravity 1.5.2): the page hides the chip. Absent otherwise.
        out["jarvis_tools"] = False
    return out


def _view(row: HostedSessionRecord, acc: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "harness": row.harness,
        "folder": row.folder,
        # v1.412.0: started with no folder chosen (its own folder is made
        # with its first message; ``folder`` names it once made).
        "no_folder": bool(getattr(row, "no_folder", False)),
        "title": row.title,
        "account": row.account,
        "account_name": str(acc.get("title") or "") if acc else "",
        "model": row.model,
        "effort": row.effort,
        "mode": row.mode,
        "harness_session_id": row.harness_session_id,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "archived": row.archived_at is not None,
        "archived_at": _iso(row.archived_at),
        "last_error": row.last_error,
        # v1.414.0: the five Capabilities (all off by default) — Browser and
        # Memory give the coding tool Iron Jarvis's own tools.
        "capabilities": _capabilities(row),
        # v1.431.0: a Pi session the user told to load its folder's own Pi
        # extensions, skills and settings (``pi --approve``).
        "load_folder_resources": bool(getattr(row, "load_folder_resources", False)),
        "source": "app",
    }


def _capabilities(row: HostedSessionRecord) -> dict[str, bool]:
    from .jarvis_tools import load

    return load(getattr(row, "capabilities_json", "") or "")


def clear_effort_if(engine: Any, sid: str, effort: str) -> bool:
    """v1.441.0 (review A6): clear the session's effort when it is STILL
    ``effort`` (checked and written in one transaction: a level the user
    picked meanwhile is never cleared). True when cleared."""
    ensure_table(engine)
    with session_scope(engine) as db:
        row = db.get(HostedSessionRecord, str(sid or ""))
        if row is None or not effort or (row.effort or "") != effort:
            return False
        row.effort = ""
        row.updated_at = utcnow()
        db.add(row)
        db.commit()
        return True


def set_folder_if_empty(engine: Any, sid: str, path: str) -> HostedSessionRecord | None:
    """v1.412.0: keep ``path`` as the session's OWN folder unless one is kept
    already (first wins). Answers the row as it is after; None when gone."""
    ensure_table(engine)
    with session_scope(engine) as db:
        row = db.get(HostedSessionRecord, str(sid or ""))
        if row is None:
            return None
        if not row.folder:
            row.folder = str(path)
            row.updated_at = utcnow()
            db.add(row)
            db.commit()
            db.refresh(row)
        db.expunge(row)
        return row


def set_title_if_empty(engine: Any, sid: str, title: str) -> bool:
    """v1.439.0 (review): keep ``title`` only while the session has none —
    checked and written in ONE transaction, so a name the user typed a moment
    before is never replaced by the tool's. True when it was kept."""
    ensure_table(engine)
    with session_scope(engine) as db:
        row = db.get(HostedSessionRecord, str(sid or ""))
        if row is None or (row.title or "").strip() or not str(title or "").strip():
            return False
        row.title = str(title)
        row.updated_at = utcnow()
        db.add(row)
        db.commit()
        return True


def clear_folder_if(engine: Any, sid: str, path: str) -> bool:
    """v1.412.0: forget the session's own folder when it is still ``path``
    (it was taken back). True when it was cleared."""
    ensure_table(engine)
    with session_scope(engine) as db:
        row = db.get(HostedSessionRecord, str(sid or ""))
        if row is None or row.folder != str(path) or not getattr(row, "no_folder", False):
            return False
        row.folder = ""
        db.add(row)
        db.commit()
        return True
