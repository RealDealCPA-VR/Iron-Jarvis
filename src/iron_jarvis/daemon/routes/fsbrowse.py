"""Filesystem browse routes (/fs/*).

Moved verbatim from daemon/app.py's create_app; closure-local state is
reached through ``d`` (see the deps object built in create_app).
"""

from __future__ import annotations

import threading
import time

from fastapi import FastAPI, HTTPException, Request, Response
from typing import Any

from ..schemas import FsMkdirBody
from ...core.fs_policy import fs_read_ok, is_protected_path

#: How long one /fs/files walk answers repeat requests for the same
#: (folder, depth, limit, hidden) — v1.311.0. The ETag below saves the body and
#: the dashboard's re-render, but the server would still WALK every 4 s tick of
#: every pane looking at that folder; a short memo is what cuts the CPU. Short
#: on purpose: a file a CLI just wrote shows up within this plus one tick.
FILES_MEMO_S = 2.0
_FILES_MEMO_MAX = 32
_files_memo: dict[tuple, tuple[float, dict[str, Any]]] = {}
_files_memo_lock = threading.Lock()
#: One walk at a time per key: two panes on one folder share it (single-flight).
_files_key_locks: dict[tuple, threading.Lock] = {}


def _files_listing(path: str, depth: int, limit: int, show_hidden: bool) -> dict[str, Any]:
    """``list_files_recursive``, memoised for :data:`FILES_MEMO_S`. BLOCKING
    (a directory walk) — the route is a sync ``def``, so this runs on
    Starlette's threadpool, never on the event loop."""
    from ...fsbrowser import list_files_recursive

    key = (path, int(depth), int(limit), bool(show_hidden))
    with _files_memo_lock:
        hit = _files_memo.get(key)
        if hit is not None and time.monotonic() - hit[0] < FILES_MEMO_S:
            return hit[1]
        key_lock = _files_key_locks.setdefault(key, threading.Lock())
    with key_lock:
        with _files_memo_lock:
            hit = _files_memo.get(key)
            if hit is not None and time.monotonic() - hit[0] < FILES_MEMO_S:
                return hit[1]
        try:
            out = list_files_recursive(path, depth=depth, limit=limit, show_hidden=show_hidden)
        except BaseException:
            # A walk that raised (a 404 folder) leaves no memo entry, and the
            # memo's overflow prune is the only other place a lock is dropped
            # — so drop it here, or every bad path keeps its lock for good.
            with _files_memo_lock:
                if key not in _files_memo:
                    _files_key_locks.pop(key, None)
            raise
        with _files_memo_lock:
            _files_memo[key] = (time.monotonic(), out)
            if len(_files_memo) > _FILES_MEMO_MAX:  # drop the oldest answers
                for old in sorted(_files_memo, key=lambda k: _files_memo[k][0])[
                    : len(_files_memo) - _FILES_MEMO_MAX
                ]:
                    _files_memo.pop(old, None)
                    _files_key_locks.pop(old, None)
        return out


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""
    @app.post("/fs/mkdir")
    def fs_mkdir(body: FsMkdirBody) -> dict[str, Any]:
        """Create a folder (e.g. a fresh subfolder for a generation batch).
        Absolute path, parent must already exist — no silent deep trees."""
        from pathlib import Path

        p = Path((body.path or "").strip())
        if not p.is_absolute():
            raise HTTPException(status_code=400, detail="absolute path required")
        # WRITE-side guard: mkdir MODIFIES the tree, so an explicit protected-
        # root refusal (secrets vault / key dirs) comes first with an honest
        # write-flavored error; fs_read_ok below still covers the allowlist.
        if is_protected_path(p):
            raise HTTPException(
                status_code=403,
                detail="refusing to create a folder inside a protected secrets/key directory",
            )
        ok, reason = fs_read_ok(str(p))
        if not ok:
            raise HTTPException(status_code=403, detail=reason)
        if not p.parent.is_dir():
            raise HTTPException(status_code=400, detail="parent folder doesn't exist")
        if p.is_file():
            raise HTTPException(status_code=409, detail="a file with that name exists")
        created = not p.is_dir()
        try:
            p.mkdir(exist_ok=True)
        except OSError as exc:
            raise HTTPException(status_code=400, detail=f"could not create: {exc}")
        return {"path": str(p), "created": created}

    @app.get("/fs/drives")
    def fs_drives() -> dict[str, Any]:
        from ...fsbrowser import drives

        return {"drives": drives()}

    @app.get("/fs/home")
    def fs_home() -> dict[str, Any]:
        from ...fsbrowser import home

        return {"home": home()}

    @app.get("/fs/list")
    def fs_list(
        path: str, show_hidden: bool = False, dirs_only: bool = False
    ) -> dict[str, Any]:
        from ...fsbrowser import list_dir

        ok, reason = fs_read_ok(path)
        if not ok:
            raise HTTPException(status_code=403, detail=reason)
        try:
            return list_dir(path, show_hidden=show_hidden, dirs_only=dirs_only)
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.get("/fs/files")
    def fs_files(
        request: Request,
        path: str,
        depth: int = 4,
        limit: int = 600,
        show_hidden: bool = False,
    ) -> Response:
        """Every FILE under ``path`` (recursive, bounded), NEWEST FIRST — powers
        the Build page's live files panel so a CLI's freshly-created files show at
        the top. Skips heavy/noise dirs (node_modules/.git/…).

        v1.311.0: the walk keeps the newest ``limit`` files of everything it
        scanned (``truncated`` = more were found; ``scan_truncated`` +
        ``scanned`` = the walk itself was cut short), and the answer carries a
        weak ETag — the panel polls every 4 s and sends it back, so an
        unchanged folder costs a bodiless 304 instead of ~130 KB (the
        ``/sessions`` mechanism, ``routes.sessions._etagged_json``). A sync
        ``def`` on purpose: the walk runs on the threadpool, off the loop."""
        from .sessions import _etagged_json

        ok, reason = fs_read_ok(path)
        if not ok:
            raise HTTPException(status_code=403, detail=reason)
        try:
            body = _files_listing(path, depth, limit, show_hidden)
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return _etagged_json(body, request)
