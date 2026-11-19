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

v1.543.0, the folder's files (``hosted/checkpoints``; each write under the
session's git lock, in a worker thread):

* ``GET  …/rewind/preview?to=`` — what putting the files back to before that
  message would do (reads only): the files that go back, the new ones that
  move to the trash, what is left as it is, and an ``id``.
* ``POST …/rewind {to, files: true, expect}`` — conversation AND files
  (``expect`` = the preview's ``id``).
* ``POST …/rewind/files {to, expect}`` — the files only (every tool).
* ``POST …/rewind/undo`` — undo the newest rewind's file changes.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ...harness import branches as B
from ...hosted import branching as BR
from ...hosted import service as S
from .build_app import _refused


class ForkBody(BaseModel):
    at: str = Field(default="", max_length=200)


class RewindBody(BaseModel):
    to: str = Field(default="", max_length=200)
    #: v1.543.0: also put the folder's files back (the preview's ``id`` in
    #: ``expect``). Left out, nothing in the folder is touched.
    files: bool = False
    expect: str = Field(default="", max_length=80)


class RewindFilesBody(BaseModel):
    to: str = Field(default="", max_length=200)
    expect: str = Field(default="", max_length=80)


def _files_refused(r: S.Refused) -> JSONResponse:
    """A refusal, with the files that stood in the way when there are any."""
    out = _refused(r)
    problems = getattr(r, "problems", None)
    if problems:
        return JSONResponse(status_code=r.status, content={"detail": r.sentence, "problems": list(problems)})
    return out


def register(app: FastAPI, d: Any) -> None:
    def svc() -> S.HostedService:
        return S.service_for(d.platform)

    @app.post("/build/app/sessions/{sid}/fork", status_code=201)
    async def app_fork(sid: str, body: ForkBody | None = None) -> Any:
        try:
            return await BR.fork(svc(), sid, (body.at if body else "") or "")
        except S.Refused as r:
            return _refused(r)

    def _lock(sid: str) -> Any:
        """``lock(fn, folder)``: the session's git lock (the Changes pane's
        Revert takes the same one), then one for the FOLDER — two sessions
        that work in one folder never write it at the same time."""
        import os

        from .build_git import _locked

        def run(fn: Any, folder: str = "") -> Any:
            key = os.path.normcase(os.path.abspath(folder)) if folder else ""
            if not key:
                return _locked(f"app:{sid}", fn)
            return _locked(f"app:{sid}", lambda: _locked(f"code-folder:{key}", fn))

        return run

    def _busy(g: B.GitRefused) -> JSONResponse:
        return JSONResponse(status_code=g.status, content={"detail": str(g)})

    @app.post("/build/app/sessions/{sid}/rewind")
    async def app_rewind(sid: str, body: RewindBody) -> Any:
        try:
            return await BR.rewind(svc(), sid, body.to, files=bool(body.files), expect=body.expect,
                                   lock=_lock(sid))
        except S.Refused as r:
            return _files_refused(r)
        except B.GitRefused as g:
            return _busy(g)

    @app.get("/build/app/sessions/{sid}/rewind/preview")
    async def app_rewind_preview(sid: str, to: str = "") -> Any:
        try:
            return await BR.preview(svc(), sid, str(to or "")[:200], lock=_lock(sid))
        except S.Refused as r:
            return _files_refused(r)
        except B.GitRefused as g:
            return _busy(g)

    @app.post("/build/app/sessions/{sid}/rewind/files")
    async def app_rewind_files(sid: str, body: RewindFilesBody) -> Any:
        try:
            return await BR.rewind_files(svc(), sid, body.to, body.expect, lock=_lock(sid))
        except S.Refused as r:
            return _files_refused(r)
        except B.GitRefused as g:
            return _busy(g)

    @app.post("/build/app/sessions/{sid}/rewind/undo")
    async def app_rewind_undo(sid: str) -> Any:
        try:
            return await BR.undo_files(svc(), sid, lock=_lock(sid))
        except S.Refused as r:
            return _files_refused(r)
        except B.GitRefused as g:
            return _busy(g)
