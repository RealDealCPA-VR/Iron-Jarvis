"""Filesystem browse routes (/fs/*).

Moved verbatim from daemon/app.py's create_app; closure-local state is
reached through ``d`` (see the deps object built in create_app).
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from typing import Any

from ...core.fs_policy import fs_read_ok


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""
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
