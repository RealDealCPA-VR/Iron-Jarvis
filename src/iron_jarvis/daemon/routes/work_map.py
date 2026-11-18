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
* v1.499.0: each ``/work-record/folders`` row also carries ``client:
  {id, name} | null``, the confirmed client whose folder it is (computed
  here from ``workmap/clients/store.load_map``; the dashboard never works
  it out).

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


def _with_clients(platform, folders: list[dict[str, Any]]) -> None:
    """v1.499.0: each folder row says whose it is, ``client: {id, name} |
    null``, from THE resolver (``workmap/clients/store.load_map``); the page
    never works it out itself. Client tables that cannot be read leave every
    row ``null`` (the list is still the work record's)."""
    try:
        from ...workmap.clients import store

        cmap = store.load_map(getattr(platform, "engine", None))
    except Exception:  # noqa: BLE001 — the chip is extra
        cmap = None
    from ...workmap import keys as _keys
    from ...workmap.clients import names as _names

    for row in folders:
        root = cmap.client_of(row.get("folder")) if cmap is not None else None
        row["client"] = {"id": root.client_id, "name": root.name} if root is not None else None
        # "Make this a client" takes the client's name from the folder's own
        # name; a year or a work word ("2026", "Payroll") names nobody, so
        # the press is not offered there (the independent review).
        try:
            own = _keys.folder_name(_keys.home_folder(str(row.get("folder") or "")))
            row["may_make_client"] = bool(_names.display_name(own)) and not _names.is_client_parent(own)
        except Exception:  # noqa: BLE001 — unsure: not offered
            row["may_make_client"] = False


OTHER_TOOLS_UNREADABLE = "Your chats in other tools could not be read right now. Try again in a moment."
APPS_NOT_READY = "Iron Jarvis is still starting. Try again in a moment."
WATCH_GONE = "That session is not on this PC any more, or it is not one Iron Jarvis lists."
APPS_UNKNOWN = "Iron Jarvis connects to Claude desktop or Codex."


def _apps_view(d) -> list[dict[str, Any]]:
    """v1.528.0: each app that can be connected: whether it is set up on this
    PC, whether it is connected, and how many chats it reported. Nothing when
    that cannot be read (the list itself still answers)."""
    from ...workmap import reported

    apps = getattr(d.platform, "app_connections", None)
    if apps is None:
        return []
    try:
        rows = apps.status()
    except Exception:  # noqa: BLE001
        return []
    try:
        counts = reported.stats(getattr(d.platform, "engine", None))
    except Exception:  # noqa: BLE001
        counts = {}
    for row in rows:
        row.update(counts.get(row["app"]) or {"count": 0, "last": ""})
    return rows


async def _apps_change(d, name: str, *, connect: bool) -> dict[str, Any]:
    import asyncio

    from ...mcpserver.apps import APP_CLAUDE, APPS, LABELS, AppConnectError
    from ...platform import own_mcp_url

    apps = getattr(d.platform, "app_connections", None)
    if apps is None:
        raise HTTPException(status_code=503, detail=APPS_NOT_READY)
    if name not in APPS:
        raise HTTPException(status_code=404, detail=APPS_UNKNOWN)
    try:
        if connect:
            row = await asyncio.to_thread(apps.connect, name, own_mcp_url())
        else:
            row = await asyncio.to_thread(apps.disconnect, name)
    except AppConnectError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    label = LABELS[name]
    if connect:
        then = (f"Restart {label} to start using it." if name == APP_CLAUDE
                else "New Codex chats use it; restart Codex if it is open.")
        words = f"{label} is connected. {then}"
    else:
        words = f"{label} is no longer connected. What it recorded stays."
    return {**row, "words": words}


def register(app: FastAPI, d) -> None:
    @app.get("/work-record/other-tools")
    def other_tools() -> dict[str, Any]:
        """v1.518.0: the owner's chats in Claude Code and Codex, by tool and
        by folder (the sidebar's "Other tools" row). The owner's own screen:
        it does not depend on the memory switch, and it lists no chat that is
        already an Iron Jarvis coding session. Names only, never a transcript.
        v1.528.0: also Claude desktop's Chat tab (what Claude reported, and
        its Cowork sessions), and ``apps``: which apps can be connected."""
        from ...core.db import session_scope
        from ...workmap import outside

        try:
            engine = getattr(d.platform, "engine", None)
            used: set[str] = set()
            if engine is not None:
                with session_scope(engine) as db:
                    used = outside.used_ids(db)
            out = outside.listing_view(used, engine)
        except Exception as exc:  # noqa: BLE001 — said, never an empty list
            raise HTTPException(status_code=503, detail=OTHER_TOOLS_UNREADABLE) from exc
        out["apps"] = _apps_view(d)
        return out

    @app.get("/work-record/other-tools/{tool}/{chat_id}/steps")
    async def other_tool_steps(tool: str, chat_id: str) -> dict[str, Any]:
        """v1.530.0: one Claude Code or Codex session's newest steps, read
        again from the end of its own file (Chat › Code's "Running elsewhere"
        watch). Read-only; masked; the session is found among the LISTED ones
        by its id, never by a path."""
        import asyncio

        from ...workmap import outside

        try:
            got = await asyncio.to_thread(outside.watch, tool, chat_id)
        except Exception as exc:  # noqa: BLE001 — said, never an empty view
            raise HTTPException(status_code=503, detail=OTHER_TOOLS_UNREADABLE) from exc
        if got is None:
            raise HTTPException(status_code=404, detail=WATCH_GONE)
        return got

    @app.post("/work-record/other-apps/{name}/connect")
    async def other_app_connect(name: str) -> dict[str, Any]:
        """v1.528.0: the owner's press: add Iron Jarvis to Claude desktop's or
        Codex's own connectors (``mcpserver/apps.py``). Nothing else calls
        this: no tool, no chat. The app must be restarted to use it."""
        return await _apps_change(d, name, connect=True)

    @app.post("/work-record/other-apps/{name}/disconnect")
    async def other_app_disconnect(name: str) -> dict[str, Any]:
        """v1.528.0: the owner's press: remove Iron Jarvis from that app's
        connectors and end its key at once. What it recorded stays."""
        return await _apps_change(d, name, connect=False)

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
        _with_clients(d.platform, folders)
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
