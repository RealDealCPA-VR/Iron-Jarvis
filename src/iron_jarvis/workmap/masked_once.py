"""Rows stored before the work record masked secrets, masked once (v1.532.0,
fourth review OT2, owner decision D1).

A work-record row is rebuilt only when its source's stamp changes, and a
reported chat is never rebuilt, so a title, note or summary stored before
the mask keeps its secret forever. :func:`mask_stored` runs once at start
(``daemon/app.py``, off the event loop, bounded): it reads ``workentry``
(title, note, stamp) and ``reportedchat`` (title, summary, link and, for a
chat keyed by its title's words, ``chat_key``) and writes back ONLY the rows
the mask changes, with the SAME mask the write path uses
(``keys.mask_line``). Nothing is deleted and nothing leaves this PC; the log
gets only the count.

The done-marker records WHICH mask ran (``keys.mask_id``), so a wider mask
in a later release reaches the old rows once more. A second start with the
same mask reads and writes nothing.

BLOCKING (SQLite): the start-up step runs it on a worker thread.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from sqlalchemy import text

from . import keys

log = logging.getLogger(__name__)

#: The key of the done-marker in ``_ironjarvis_meta``.
MARKER = "workmap_masked_with"
#: Rows read per page.
PAGE = 500

_DAY_KEY = re.compile(r"^day:(\d{4}-\d{2}-\d{2}):")


def _marker(engine: Any) -> str:
    from ..core.db import _ensure_meta

    _ensure_meta(engine)
    with engine.connect() as conn:
        row = conn.execute(text("SELECT value FROM _ironjarvis_meta WHERE key=:k"), {"k": MARKER}).first()
    return str(row[0]) if row else ""


def _set_marker(engine: Any, value: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO _ironjarvis_meta(key,value) VALUES(:k,:v) ON CONFLICT(key) DO UPDATE SET value=:v"),
            {"k": MARKER, "v": value},
        )


def _has(engine: Any, table: str) -> bool:
    from sqlalchemy import inspect

    return bool(inspect(engine).has_table(table))


def _pages(engine: Any, sql: str):
    last = ""
    while True:
        with engine.connect() as conn:
            rows = conn.execute(text(sql), {"last": last, "n": PAGE}).all()
        if not rows:
            return
        yield rows
        last = str(rows[-1][0])
        if len(rows) < PAGE:
            return


def _work_rows(engine: Any) -> int:
    changed = 0
    sql = "SELECT id, title, note, stamp FROM workentry WHERE id > :last ORDER BY id LIMIT :n"
    for rows in _pages(engine, sql):
        writes = []
        for rid, title, note, stamp in rows:
            old = (str(title or ""), str(note or ""), str(stamp or ""))
            new = tuple(keys.mask_line(v) if v else v for v in old)
            if new != old:
                writes.append({"id": rid, "t": new[0], "n": new[1], "s": new[2],
                               "ot": title, "on": note, "os": stamp})
        if writes:
            with engine.begin() as conn:
                for w in writes:
                    # Only a row nobody rewrote since it was read.
                    got = conn.execute(text(
                        "UPDATE workentry SET title=:t, note=:n, stamp=:s WHERE id=:id "
                        "AND title IS :ot AND note IS :on AND stamp IS :os"), w)
                    changed += int(got.rowcount or 0)
    return changed


def _reported_rows(engine: Any) -> int:
    from .reported import chat_key

    changed = 0
    sql = ("SELECT id, app, chat_key, title, summary, link FROM reportedchat "
           "WHERE id > :last ORDER BY id LIMIT :n")
    for rows in _pages(engine, sql):
        writes = []
        for rid, app, key, title, summary, link in rows:
            old = (str(title or ""), str(summary or ""), str(link or ""))
            new = tuple(keys.mask_line(v) if v else v for v in old)
            new_key = str(key or "")
            day = _DAY_KEY.match(new_key)
            if day and new[0] != old[0]:
                # A chat keyed by its title's words: keyed again by the masked
                # title (a later report computes the same key from it).
                new_key = chat_key(str(app or ""), new[0], "", datetime.fromisoformat(day.group(1)))
            if new != old or new_key != str(key or ""):
                writes.append({"id": rid, "app": app, "k": new_key, "t": new[0], "su": new[1], "l": new[2],
                               "ok": key, "ot": title, "osu": summary, "ol": link})
        if writes:
            with engine.begin() as conn:
                for w in writes:
                    taken = conn.execute(text(
                        "SELECT 1 FROM reportedchat WHERE app=:app AND chat_key=:k AND id != :id"), w).first()
                    if taken:
                        w["k"] = f"{w['k']}:{w['id']}"  # never two rows on one key
                    got = conn.execute(text(
                        "UPDATE reportedchat SET chat_key=:k, title=:t, summary=:su, link=:l WHERE id=:id "
                        "AND chat_key IS :ok AND title IS :ot AND summary IS :osu AND link IS :ol"), w)
                    changed += int(got.rowcount or 0)
    return changed


def mask_stored(engine: Any) -> int:
    """BLOCKING: mask what the work record and the reported chats stored
    before this mask, once per mask. Returns the rows rewritten. A mask that
    faults RAISES (the marker is then not written, so the next start tries
    again)."""
    if engine is None:
        return 0
    want = keys.mask_id()
    if _marker(engine) == want:
        return 0
    changed = 0
    if _has(engine, "workentry"):
        changed += _work_rows(engine)
    if _has(engine, "reportedchat"):
        changed += _reported_rows(engine)
    _set_marker(engine, want)
    if changed:
        log.info("work record: masked secret-looking words in %d stored rows", changed)
    return changed


__all__ = ["MARKER", "mask_stored"]
