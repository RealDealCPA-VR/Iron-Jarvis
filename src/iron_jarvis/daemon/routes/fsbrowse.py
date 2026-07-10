"""Filesystem browse routes (/fs/*).

Moved verbatim from daemon/app.py's create_app; closure-local state is
reached through ``d`` (see the deps object built in create_app).
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from typing import Any

from ..schemas import FsMkdirBody
from ...core.fs_policy import fs_read_ok, is_protected_path


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
        path: str, depth: int = 4, limit: int = 600, show_hidden: bool = False
    ) -> dict[str, Any]:
        """Every FILE under ``path`` (recursive, bounded), NEWEST FIRST — powers
        the Build page's live files panel so a CLI's freshly-created files show at
        the top. Skips heavy/noise dirs (node_modules/.git/…)."""
        from ...fsbrowser import list_files_recursive

        ok, reason = fs_read_ok(path)
        if not ok:
            raise HTTPException(status_code=403, detail=reason)
        try:
            return list_files_recursive(
                path, depth=depth, limit=limit, show_hidden=show_hidden
            )
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise HTTPException(status_code=404, detail=str(exc))
