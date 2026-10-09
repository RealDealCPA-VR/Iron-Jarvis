"""What a chat turn changed on disk (v1.328.0, calm chat B5).

``POST /chat/changes`` answers "3 files changed +42 -7" for one chat turn and
hands back each file's diff, so the reply can carry one quiet line and a diff
view without the dashboard ever guessing.

THE SOURCE IS THE UNDO JOURNAL, nothing else. Every reversible file write a
chat turn makes leaves an ``UndoJournal`` row (session id ``"chat"``) whose
envelope names the target (``path``, workspace-relative) and the workspace it
was written against (``workspace``, the v1.166.3 capture-time stamp), and for
an overwrite it holds the PRIOR bytes (inline, or a blob under ``undo/``). So:

* the turn is a time window, ``since`` .. ``until`` (the client's turn start
  and end, with the ledger's 2 s slack, the same rule ``detections/ledger.py``
  uses to give a chat row to its turn). ``paths`` optionally narrows it to the
  files the turn reported, which also keeps a second window's concurrent turn
  out;
* the "before" of a file is its FIRST row in the window: ``file_delete`` /
  ``files_delete`` means the turn created it (before = nothing),
  ``file_restore`` means it overwrote it (before = the journal's prior bytes);
* the "after" is the file on disk NOW. When the LAST row's recorded
  ``post_sha256`` no longer matches the disk, the file changed again after the
  turn and the row says so (``changed_since``) rather than passing a later
  edit off as the turn's. No recorded hash means we cannot tell: ``null``;
* nothing is read that the journal did not record. A client path is only ever
  a FILTER compared against journal targets, never opened. Every target also
  passes the same read gate the file tools use (``fs_read_ok``: protected
  roots, ``IRONJARVIS_FS_ALLOWLIST``);
* rows for failed calls are skipped (a capture runs before the write, so a
  write that then failed still leaves a row), as are kinds with no file
  content to compare (renames, memory, settings) and pre-stamp rows with no
  workspace (resolving those would mean guessing a folder).

BOUNDED: at most :data:`MAX_FILES` files, each side read only up to
:data:`READ_CAP` bytes (bigger says "too large to compare"), the diff text
capped at :data:`DIFF_CAP` characters with ``truncated`` set. The +/- counts
always cover the whole diff even when the text is cut. A side that is not
UTF-8 text says "binary file".

OFF THE EVENT LOOP: the query, the blob loads, the disk reads and difflib all
run in one ``asyncio.to_thread`` hop (the v1.153.1 rule).
"""

from __future__ import annotations

import asyncio
import difflib
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import literal_column
from sqlmodel import select

from ...core.db import session_scope
from ...core.fs_policy import fs_read_ok
from ...core.models import ToolInvocation, UndoJournal
from ...tools.base import safe_path
from ...tools.undo import read_envelope, resolve_prior_bytes, sha256_bytes

#: Both chat lanes ledger (and journal) every tool call under this session id.
CHAT_SESSION = "chat"
#: A tool row can be stamped a moment outside the client's own clock reading
#: of the turn (the ledger allows the same 2 s).
TURN_SLACK = timedelta(seconds=2)
#: Journal rows read for one window. A turn writes a handful; this only stops
#: a wide-open window from walking the whole history.
MAX_ROWS = 2000
#: Files reported for one turn; past this ``truncated_files`` is set.
MAX_FILES = 50
#: Bytes read per side. A bigger file is not compared (difflib is quadratic on
#: a bad day and a multi-MB diff is not a thing anyone reads in a dialog).
READ_CAP = 1_000_000
#: Lines per side difflib is given.
LINE_CAP = 20_000
#: Characters of unified diff text returned per file.
DIFF_CAP = 200_000

#: Journal kinds whose first row means "the turn created this file".
_CREATED_KINDS = frozenset({"file_delete", "files_delete"})
_FILE_KINDS = _CREATED_KINDS | {"file_restore"}

_ROWID = literal_column("toolinvocation.rowid")

BINARY_NOTE = "binary file"


class ChatChangesBody(BaseModel):
    """``POST /chat/changes``. ``since`` is the turn's start, ``until`` its end
    (omitted: now). ``paths`` (absolute, as the turn's done frame reported
    them) narrows the answer to those files."""

    since: datetime
    until: datetime | None = None
    paths: list[str] = Field(default_factory=list, max_length=200)


def _naive_utc(dt: datetime) -> datetime:
    """The DB stores naive UTC (``core.ids.utcnow``); an ISO stamp from a
    browser arrives tz-aware (``...Z``)."""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _key(path: str | Path) -> str:
    """Comparison key for a path: normalised, case-folded where the OS is."""
    return os.path.normcase(os.path.normpath(str(path)))


def _targets(meta: dict[str, Any], kind: str) -> list[str]:
    """The workspace-relative targets one journal envelope names."""
    if kind == "files_delete":
        paths = meta.get("paths")
        return [str(p) for p in paths if isinstance(p, str) and p] if isinstance(paths, list) else []
    rel = meta.get("path")
    return [str(rel)] if isinstance(rel, str) and rel else []


def _text_of(data: bytes) -> str | None:
    """The bytes as text, or ``None`` when they are not UTF-8 text."""
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _hash_now(data: bytes, mode: str) -> str:
    """Hash the current bytes the way the journal hashed them after the write
    (``tools/undo.sha256_target``): ``text`` mode is the decoded text with
    universal newlines, re-encoded, so a CRLF file matches its LF write."""
    if mode == "text":
        text = _text_of(data)
        if text is not None:
            text = text.replace("\r\n", "\n").replace("\r", "\n")
            return sha256_bytes(text.encode("utf-8"))
    return sha256_bytes(data)


def unified(before: str, after: str, name: str) -> tuple[str, int, int, bool]:
    """``(diff text, lines added, lines removed, truncated)`` for two texts.

    The counts cover the WHOLE diff; only the returned text is capped at
    :data:`DIFF_CAP` (cut on a line end)."""
    lines = list(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=f"a/{name}",
            tofile=f"b/{name}",
            lineterm="",
            n=3,
        )
    )
    added = removed = 0
    for i, line in enumerate(lines):
        if i < 2:  # the ---/+++ header pair
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    text = "\n".join(lines)
    truncated = False
    if len(text) > DIFF_CAP:
        cut = text.rfind("\n", 0, DIFF_CAP)
        text = text[: cut if cut > 0 else DIFF_CAP]
        truncated = True
    return text, added, removed, truncated


def _read_capped(path: Path) -> tuple[bytes | None, bool]:
    """``(bytes, too_large)``; ``(None, False)`` when the file is not there."""
    try:
        if not path.is_file():
            return None, False
        if path.stat().st_size > READ_CAP:
            return None, True
        return path.read_bytes(), False
    except OSError:
        return None, False


def _journal_rows(engine, lo: datetime, hi: datetime) -> list[dict[str, Any]]:
    """The window's successful chat file-write rows, oldest first, as plain
    values (SQLModel attributes expire after the session closes)."""
    out: list[dict[str, Any]] = []
    with session_scope(engine) as db:
        rows = db.exec(
            select(UndoJournal, ToolInvocation)
            .where(UndoJournal.action_id == ToolInvocation.id)
            .where(UndoJournal.session_id == CHAT_SESSION)
            .where(ToolInvocation.created_at >= lo)
            .where(ToolInvocation.created_at <= hi)
            .order_by(ToolInvocation.created_at, _ROWID)  # type: ignore[arg-type]
            .limit(MAX_ROWS)
        ).all()
        for j, inv in rows:
            if not inv.ok or j.kind not in _FILE_KINDS:
                continue
            out.append(
                {
                    "kind": j.kind,
                    "pre_ref": j.pre_ref,
                    "pre_inline": j.pre_inline,
                    "post_sha256": j.post_sha256,
                    "undone": inv.undone_at is not None,
                    "tool": j.tool,
                }
            )
    return out


def collect_changes(
    engine, home: str | Path, since: datetime, until: datetime | None, paths: list[str]
) -> dict[str, Any]:
    """THE body of ``POST /chat/changes`` (sync; the route offloads it)."""
    lo = _naive_utc(since) - TURN_SLACK
    hi = _naive_utc(until or datetime.now(timezone.utc)) + TURN_SLACK
    if hi < lo:
        raise HTTPException(status_code=422, detail="until is before since")
    wanted = {_key(p) for p in paths if isinstance(p, str) and p.strip()}

    # Group the rows by resolved target, keeping first + last per file.
    files: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in _journal_rows(engine, lo, hi):
        meta = read_envelope(row)
        ws = meta.get("workspace")
        if not isinstance(ws, str) or not ws:
            continue  # pre-stamp row: the folder would be a guess
        for rel in _targets(meta, row["kind"]):
            try:
                target = safe_path(Path(ws), rel)
            except Exception:  # noqa: BLE001 — escaped its workspace: never read it
                continue
            k = _key(target)
            if wanted and k not in wanted:
                continue
            entry = files.get(k)
            if entry is None:
                entry = files[k] = {
                    "target": target,
                    "rel": rel.replace("\\", "/"),
                    "first": row,
                    "first_meta": meta,
                    "last": row,
                    "last_meta": meta,
                    "undone": False,
                }
                order.append(k)
            entry["last"], entry["last_meta"] = row, meta
            entry["undone"] = entry["undone"] or row["undone"]

    truncated_files = len(order) > MAX_FILES
    changes = [_one_change(home, files[k]) for k in order[:MAX_FILES]]
    return {
        "changes": changes,
        "files": len(changes),
        "added": sum(c["added"] or 0 for c in changes),
        "removed": sum(c["removed"] or 0 for c in changes),
        "truncated_files": truncated_files,
    }


def _one_change(home: str | Path, entry: dict[str, Any]) -> dict[str, Any]:
    target: Path = entry["target"]
    first, first_meta = entry["first"], entry["first_meta"]
    last, last_meta = entry["last"], entry["last_meta"]
    out: dict[str, Any] = {
        "path": str(target),
        "rel": entry["rel"],
        "name": target.name,
        "status": "modified",
        "added": None,
        "removed": None,
        "diff": "",
        "truncated": False,
        "binary": False,
        "changed_since": None,
        "undone": bool(entry["undone"]),
        "note": "",
    }
    ok, reason = fs_read_ok(target)
    if not ok:
        out["note"] = f"Not shown: {reason}."
        return out

    created = first["kind"] in _CREATED_KINDS
    prior: bytes | None = b"" if created else None
    prior_known = created
    prior_large = False
    if not created:
        try:
            prior = resolve_prior_bytes(home, first, first_meta)
        except OSError:
            prior = None
        prior_known = prior is not None
        if prior is not None and len(prior) > READ_CAP:
            prior_large = True

    now, now_large = _read_capped(target)
    exists = now is not None or now_large

    if created:
        out["status"] = "created"
    elif not exists:
        out["status"] = "deleted"

    post = last.get("post_sha256")
    if not exists:
        out["changed_since"] = True
    elif post is not None and now is not None:
        out["changed_since"] = _hash_now(now, str(last_meta.get("mode") or "raw")) != post

    if not exists and created:
        out["note"] = "The file is no longer there."
        return out
    if not prior_known:
        out["note"] = "The earlier version is no longer kept, so there is nothing to compare."
        return out
    if now_large or prior_large:
        out["note"] = "Too large to compare here."
        out["truncated"] = True
        return out

    before_text = _text_of(prior or b"")
    after_text = _text_of(now or b"")
    if before_text is None or after_text is None:
        out["binary"] = True
        out["diff"] = BINARY_NOTE
        return out
    if (
        before_text.count("\n") > LINE_CAP or after_text.count("\n") > LINE_CAP
    ):
        out["note"] = "Too large to compare here."
        out["truncated"] = True
        return out
    diff, added, removed, truncated = unified(before_text, after_text, entry["rel"])
    out.update(diff=diff, added=added, removed=removed, truncated=truncated)
    return out


def register(app: FastAPI, d) -> None:
    @app.post("/chat/changes")
    async def chat_changes(body: ChatChangesBody) -> dict[str, Any]:
        """What the chat turn between ``since`` and ``until`` changed on disk,
        one row per file with its unified diff. Read-only; journal-sourced."""
        platform = d.platform
        return await asyncio.to_thread(
            collect_changes,
            platform.engine,
            platform.config.home,
            body.since,
            body.until,
            list(body.paths),
        )
