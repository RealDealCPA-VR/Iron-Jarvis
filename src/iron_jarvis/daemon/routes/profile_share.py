"""Share my profile with Build — the routes (v1.306.0; idea from agent-personalizer, MIT).

* ``GET  /profile/share`` → ``{clis: [{cli, label, vendor, available, on,
  file_name, files: [{path, account, exists, last_written, drift, held,
  created, error}], last_written, drift, accounts_known, chars, omitted}],
  limit}``;
* ``PUT  /profile/share {cli, on}`` → the same view, after the block was
  written into (or taken out of) every file — the switch is the consent;
* ``POST /profile/share/{cli}/overwrite {path?}`` → the view, after our block
  replaced the user's edited one (or went back where they removed it);
* ``POST /profile/share/{cli}/keep {path?}`` → the view, after the user chose
  to keep their own version (that file is no longer updated until Overwrite).

Every file read / write runs off the loop (``asyncio.to_thread``). Refusals
are plain sentences: 404 an unknown CLI or nothing to act on, 409 a CLI that
is not on this PC or a switch that is off.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from ...profile import share as _share


class ShareBody(BaseModel):
    cli: str = ""
    on: bool = False


class PathBody(BaseModel):
    path: str | None = None


def register(app: FastAPI, d) -> None:
    """Attach the profile-share routes; ``d`` is the create_app deps object."""

    def _svc() -> _share.ProfileShare:
        svc = getattr(d.platform, "profile_share", None)
        if svc is None:
            raise HTTPException(status_code=503, detail="Sharing your profile is not available in this daemon.")
        return svc

    def _spec(cli: str) -> _share.CliSpec:
        spec = _share.CLIS.get((cli or "").strip())
        if spec is None:
            raise HTTPException(
                status_code=404,
                detail=f"{cli!r} is not something Jarvis can share with — use claude-code or codex.",
            )
        return spec

    @app.get("/profile/share")
    async def profile_share_view() -> dict[str, Any]:
        """Per CLI: found on this PC, on/off, the files, last written, drift."""
        return await asyncio.to_thread(_svc().state)

    @app.put("/profile/share")
    async def profile_share_set(body: ShareBody) -> dict[str, Any]:
        """Turn sharing with one CLI on (write the block now) or off (take it out)."""
        svc = _svc()
        spec = _spec(body.cli)
        if body.on and not await asyncio.to_thread(svc.available, spec.id):
            raise HTTPException(
                status_code=409,
                detail=f"{spec.label} is not installed on this PC, so there is nothing to share with.",
            )
        await asyncio.to_thread(svc.set_on, spec.id, bool(body.on))
        return await asyncio.to_thread(svc.state)

    @app.post("/profile/share/{cli}/overwrite")
    async def profile_share_overwrite(cli: str, body: PathBody | None = None) -> dict[str, Any]:
        """Put Jarvis's block back over your edit (your explicit press)."""
        svc = _svc()
        spec = _spec(cli)
        if not svc.is_on(spec.id):
            raise HTTPException(
                status_code=409,
                detail=f"Sharing with {spec.label} is off — turn it on first.",
            )
        n = await asyncio.to_thread(svc.overwrite, spec.id, body.path if body else None)
        if n == 0:
            raise HTTPException(status_code=404, detail="There is no edited block to overwrite.")
        return await asyncio.to_thread(svc.state)

    @app.post("/profile/share/{cli}/keep")
    async def profile_share_keep(cli: str, body: PathBody | None = None) -> dict[str, Any]:
        """Keep your own version of the block; Jarvis stops updating that file."""
        svc = _svc()
        spec = _spec(cli)
        n = await asyncio.to_thread(svc.keep_mine, spec.id, body.path if body else None)
        if n == 0:
            raise HTTPException(status_code=404, detail="There is no edited block to keep.")
        return await asyncio.to_thread(svc.state)
