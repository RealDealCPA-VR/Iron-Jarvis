"""The Skill Curator's HTTP doors (v1.297.0).

* ``GET  /skills/curator``                 — status + candidates + archived
* ``POST /skills/curator/run``             — ``{dry_run?: bool}`` → sweep result
* ``POST /skills/curator/{name}/pin``      — exempt from the sweep
* ``POST /skills/curator/{name}/unpin``
* ``POST /skills/curator/{name}/archive``  — by hand, ANY user-root skill
  (the user's own included: only the automatic sweep spares them)
* ``POST /skills/curator/{name}/restore``  — back from ``.archive``

Every action answers ``{ok, skill}``; an unknown name is a 404 and a refused
restore (the folder is taken) is a 409, each in one plain sentence. Every
disk op runs off the loop (``asyncio.to_thread``).

Registration order matters: ``routes/agents.py`` owns ``GET /skills/{name}``,
a catch-all that would swallow the literal ``/skills/curator`` path — register
this module BEFORE ``agents`` (the same reason ``skill_learning`` is).
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


class CuratorRunBody(BaseModel):
    dry_run: bool = False


def _curator(d):
    cur = getattr(d.platform, "skill_curator", None)
    if cur is None:
        raise HTTPException(
            status_code=503,
            detail="the skill curator is not available on this daemon — restart it",
        )
    return cur


def register(app: FastAPI, d) -> None:
    @app.get("/skills/curator")
    async def skills_curator_overview() -> dict[str, Any]:
        """Status, the skills the next sweep would archive (with reasons), and
        everything parked in the archive."""
        return await asyncio.to_thread(_curator(d).overview)

    @app.post("/skills/curator/run")
    async def skills_curator_run(body: CuratorRunBody | None = None) -> dict[str, Any]:
        """Run the sweep now. ``dry_run`` moves nothing and answers what a
        real run would archive."""
        dry = bool(body.dry_run) if body is not None else False
        return await asyncio.to_thread(_curator(d).sweep, dry_run=dry)

    async def _act(verb: str, name: str) -> dict[str, Any]:
        cur = _curator(d)
        try:
            view = await asyncio.to_thread(getattr(cur, verb), name)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        if view is None:
            where = "the archive" if verb == "restore" else "your skills folder"
            raise HTTPException(
                status_code=404, detail=f"there is no skill named '{name}' in {where}"
            )
        return {"ok": True, "skill": view}

    @app.post("/skills/curator/{name}/pin")
    async def skills_curator_pin(name: str) -> dict[str, Any]:
        """Exempt a skill from the automatic sweep."""
        return await _act("pin", name)

    @app.post("/skills/curator/{name}/unpin")
    async def skills_curator_unpin(name: str) -> dict[str, Any]:
        return await _act("unpin", name)

    @app.post("/skills/curator/{name}/archive")
    async def skills_curator_archive(name: str) -> dict[str, Any]:
        """Archive one skill by hand — the user's own included."""
        return await _act("archive", name)

    @app.post("/skills/curator/{name}/restore")
    async def skills_curator_restore(name: str) -> dict[str, Any]:
        """Bring an archived skill back into service."""
        return await _act("restore", name)
