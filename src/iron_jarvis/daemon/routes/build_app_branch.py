"""Build › Code's Branch from here and Rewind to here (v1.441.0): thin routes
over :mod:`iron_jarvis.hosted.branching`, where every rule (and what was
measured on each tool) lives. A refusal is one plain sentence (``detail``),
with ``code: not_offered`` when the tool itself cannot do it.

* ``POST /build/app/sessions/{id}/fork {at?}`` — a NEW session in the same
  folder, on the same tool, model, mode and account: the conversation before
  message ``at`` (its words come back as ``text`` for the box), or all of it.
* ``POST /build/app/sessions/{id}/rewind {to}`` — the conversation drops
  message ``to`` and everything after it, through the tool's own way; files
  are not touched (``note`` says what was done).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from ...hosted import branching as BR
from ...hosted import service as S
from .build_app import _refused


class ForkBody(BaseModel):
    at: str = Field(default="", max_length=200)


class RewindBody(BaseModel):
    to: str = Field(default="", max_length=200)


def register(app: FastAPI, d: Any) -> None:
    def svc() -> S.HostedService:
        return S.service_for(d.platform)

    @app.post("/build/app/sessions/{sid}/fork", status_code=201)
    async def app_fork(sid: str, body: ForkBody | None = None) -> Any:
        try:
            return await BR.fork(svc(), sid, (body.at if body else "") or "")
        except S.Refused as r:
            return _refused(r)

    @app.post("/build/app/sessions/{sid}/rewind")
    async def app_rewind(sid: str, body: RewindBody) -> Any:
        try:
            return await BR.rewind(svc(), sid, body.to)
        except S.Refused as r:
            return _refused(r)
