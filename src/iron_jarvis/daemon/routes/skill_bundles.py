"""Starter skill bundles and skills from GitHub (v1.464.0).

The Templates page's "Starter skills" card. Every route here is a plain
``def`` (FastAPI runs it off the event loop): the work is file copies and, for
GitHub, network reads. See ``skills/bundles.py`` and
``skills/github_import.py`` for the rules each keeps.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ...skills import bundles as B
from ...skills import github_import as G


class BundleLoadBody(BaseModel):
    #: The skills to load; omitted = the whole bundle.
    skills: "list[str] | None" = Field(default=None, max_length=100)
    #: Also replace a starter skill the user edited after loading it.
    replace: bool = False


class GithubPreviewBody(BaseModel):
    url: str = Field(max_length=500)


class GithubLoadBody(BaseModel):
    url: str = Field(max_length=500)
    commit: str = Field(max_length=64)
    paths: list[str] = Field(default_factory=list, max_length=G.MAX_SKILLS)
    #: The second press: the user has read the warning and the list.
    confirm: bool = False


def register(app: FastAPI, d) -> None:
    def _home():
        return d.platform.config.home

    def _taken() -> set[str]:
        try:
            return {s.name for s in d.platform.skills.list()}
        except Exception:  # noqa: BLE001 - an unreadable registry takes no name
            return set()

    def _rescan() -> None:
        try:
            d._rescan_skills()
        except Exception:  # noqa: BLE001 - the files are written; a rescan can be pressed
            pass

    @app.get("/templates/skill-bundles")
    def skill_bundles() -> dict[str, Any]:
        """The starter bundles, their skills, and which are already loaded."""
        return {"bundles": B.list_bundles(_home()), "github_warning": G.WARNING}

    @app.post("/templates/skill-bundles/github/preview")
    def skill_bundles_github_preview(body: GithubPreviewBody) -> dict[str, Any]:
        """Look at a GitHub repository's skills. Writes nothing."""
        try:
            return G.preview(body.url, _home(), _taken())
        except G.ImportRefused as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.post("/templates/skill-bundles/github/load")
    def skill_bundles_github_load(body: GithubLoadBody) -> dict[str, Any]:
        """Load the chosen skills, read again at the commit the preview named."""
        try:
            out = G.load(body.url, body.commit, body.paths, _home(),
                         confirm=body.confirm, taken=_taken())
        except G.ImportRefused as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        if out["loaded"]:
            _rescan()
        return out

    @app.post("/templates/skill-bundles/{bundle_id}/load")
    def skill_bundle_load(bundle_id: str, body: "BundleLoadBody | None" = None) -> dict[str, Any]:
        """Copy a bundle's skills (or the chosen ones) into the user's skills."""
        body = body or BundleLoadBody()
        try:
            out = B.load_bundle(_home(), bundle_id, body.skills, replace=body.replace)
        except B.UnknownBundle as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        if out["loaded"]:
            _rescan()
        return out

    @app.post("/templates/skill-bundles/{bundle_id}/remove")
    def skill_bundle_remove(bundle_id: str) -> dict[str, Any]:
        """Take a bundle's skills back out (moved to the trash folder; a skill
        the user edited is kept)."""
        try:
            out = B.remove_bundle(_home(), bundle_id)
        except B.UnknownBundle as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        if out["removed"]:
            _rescan()
        return out
