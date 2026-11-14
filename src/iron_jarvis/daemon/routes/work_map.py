"""The work record's routes (v1.491.0, vision plan leap 3, Phase 1).

* ``GET /work-record/folders`` → ``{folders: [{folder, name, count, last_at,
  kinds}], folders_total, total, building}``: the folders Iron Jarvis has
  work on record for, the most recently used first (at most
  :data:`FOLDERS_MAX`; ``folders_total`` says how many there are).
  ``building`` is true while a long first build is still going (the page
  reads again). 503 when the record could not be read and holds nothing: a
  failed read is never answered as "no work".
* ``GET /work-record/entries?folder=&limit=`` → ``{folder, name, entries:
  [{id, kind, ref, title, tool, origin, outcome, local_only, archived,
  files: [{name, path, how}], files_total, note, updated_at, started_at,
  link}], total}``: the work in exactly that folder, newest first.

* v1.492.0: each entry also carries ``recap``: ``null``, or ``{lines: [str],
  written_at, current}`` (a recap written by a model after the work went
  quiet and checked against the work's own record; ``current`` false = the
  conversation changed since). ``/work-record/folders`` also answers
  ``forgotten: [{folder, name, at}]``.
* ``POST /work-record/forget {folder}`` → ``{ok, removed, folder}``: the
  work on record in that folder and under it (and its recaps) leaves the
  record, and none is added while the folder stays forgotten. The chats,
  sessions and files themselves are not touched. 400 in words for no folder
  or the top of a drive.
* ``POST /work-record/remember {folder}`` → ``{ok}``: stop forgetting it;
  its work is read into the record again. 404 when it was not forgotten.

Names, file names, a run's own summary line and a checked recap: never a
transcript, never a file's content. The reads are SQLite, so both handlers
are plain ``def`` (FastAPI runs them off the loop) and neither waits for a
build. Not under ``/memory/``: ``GET /memory/{layer}/{key}`` would answer
for it. The Memory page's "Your work, by folder" section is the reader.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

FOLDERS_MAX = 300
ENTRIES_MAX = 300
UNREADABLE = "Your work record could not be read right now. Try again in a moment."
NO_FOLDER = "Say which folder to forget."
TOO_WIDE = "That is the top of a drive. Pick a folder inside it to forget."
NOT_FORGOTTEN = "That folder was not forgotten."


class FolderBody(BaseModel):
    folder: str = ""


def _map(d):
    from ...workmap.index import work_map

    platform = d.platform
    wm = work_map(
        getattr(platform, "engine", None),
        getattr(getattr(platform, "config", None), "home", None),
    )
    if wm is None:
        raise HTTPException(status_code=503, detail=UNREADABLE)
    return wm


def register(app: FastAPI, d) -> None:
    @app.get("/work-record/folders")
    def work_folders() -> dict[str, Any]:
        wm = _map(d)
        got = wm.refresh()
        try:
            folders, folders_total = wm.folders(limit=FOLDERS_MAX)
            total = wm.count()
        except Exception as exc:  # noqa: BLE001 — said, never an empty list
            raise HTTPException(status_code=503, detail=UNREADABLE) from exc
        if got.get("failed") and not folders:
            raise HTTPException(status_code=503, detail=UNREADABLE)
        try:
            forgotten = wm.forgotten()
        except Exception:  # noqa: BLE001 — the list is extra
            forgotten = []
        return {
            "folders": folders,
            "folders_total": folders_total,
            "total": total,
            "building": bool(wm.building),
            "forgotten": forgotten,
        }

    @app.get("/work-record/entries")
    def work_entries(folder: str, limit: int = 100) -> dict[str, Any]:
        from ...workmap import keys

        wm = _map(d)
        place = str(folder or "").strip()
        if not place:
            raise HTTPException(status_code=400, detail="Say which folder to show.")
        wm.refresh()
        try:
            entries, total = wm.entries(place, limit=max(1, min(int(limit), ENTRIES_MAX)))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=UNREADABLE) from exc
        return {
            "folder": place,
            "name": keys.folder_name(place),
            "entries": entries,
            "total": total,
        }

    @app.post("/work-record/forget")
    def work_forget(body: FolderBody) -> dict[str, Any]:
        """Forget a folder: its work leaves the record and stays out."""
        wm = _map(d)
        place = str(body.folder or "").strip()
        if not place:
            raise HTTPException(status_code=400, detail=NO_FOLDER)
        try:
            got = wm.forget(place)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=UNREADABLE) from exc
        if not got.get("ok"):
            raise HTTPException(status_code=400, detail=TOO_WIDE)
        return {"ok": True, "removed": int(got.get("removed") or 0), "folder": place}

    @app.post("/work-record/remember")
    def work_remember(body: FolderBody) -> dict[str, Any]:
        """Stop forgetting a folder; its work is read into the record again."""
        wm = _map(d)
        place = str(body.folder or "").strip()
        try:
            got = wm.remember(place) if place else {"ok": False}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=UNREADABLE) from exc
        if not got.get("ok"):
            raise HTTPException(status_code=404, detail=NOT_FORGOTTEN)
        wm.refresh(force=True, budget=200)
        return {"ok": True}
