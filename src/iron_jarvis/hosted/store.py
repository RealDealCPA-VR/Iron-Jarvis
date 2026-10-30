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
    acc = account_record(row)
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
        "source": "app",
    }


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
