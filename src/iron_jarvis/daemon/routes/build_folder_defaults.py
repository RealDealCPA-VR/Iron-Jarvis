"""A folder's defaults for NEW coding sessions started in it (v1.426.0, Wave 6
lane B, E5) — every rule lives in :mod:`iron_jarvis.harness.folder_defaults`.

``GET /build/folders/defaults?folder=`` answers ``{folder, defaults: {code?,
tool?}}`` (every folder answers 200 — none saved is ``{}``; the screens read a
404 as an older Iron Jarvis). ``PUT /build/folders/defaults {folder, view:
"code" | "tool", defaults: {...} | null}`` saves (or with null clears) one
view's defaults and answers the folder's defaults after it; a default the
folder cannot keep is a 400 in one sentence. Both are plain ``def`` routes:
FastAPI runs them in its thread pool (SQLite is blocking).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ...harness import folder_defaults as FD
from ...harness.sessions import resolved_folder


class FolderDefaultsBody(BaseModel):
    folder: str = Field(min_length=1, max_length=1000)
    view: str = Field(min_length=1, max_length=20)
    defaults: dict[str, Any] | None = None


def register(app: FastAPI, d: Any) -> None:
    def engine() -> Any:
        return d.platform.engine

    @app.get("/build/folders/defaults")
    def get_folder_defaults(folder: str = "") -> dict[str, Any]:
        """What NEW coding sessions in ``folder`` (the folder the user
        picked) start with, per coding view."""
        path = resolved_folder(folder) if str(folder or "").strip() else ""
        if not path:
            return {"folder": "", "defaults": {}}
        return {"folder": path, "defaults": FD.read(engine(), path)}

    @app.put("/build/folders/defaults")
    def put_folder_defaults(body: FolderDefaultsBody) -> dict[str, Any]:
        """Save (``defaults: null`` clears) one view's defaults for the
        folder. A session already made keeps its own settings."""
        path = resolved_folder(body.folder)
        try:
            saved = FD.save(engine(), path, body.view, body.defaults)
        except FD.DefaultsRefused as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from None
        return {"folder": path, "defaults": saved}
