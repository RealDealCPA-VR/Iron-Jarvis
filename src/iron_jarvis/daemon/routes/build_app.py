"""Build's Code view (v1.409.0): the user's REAL Claude Code / Codex, driven
from the dashboard (``iron_jarvis/hosted``; the contract is
``docs/BUILD-APP-VIEW-PLAN.md``). Thin routes: every rule lives in
:mod:`iron_jarvis.hosted.service`. A refusal is one plain sentence
(``detail``) with its status.

The route names keep ``/build/app/…`` (internal); the dashboard calls the view
"Code" and links a session as ``/terminals?view=code&code=<id>``.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ...hosted import service as S


class AppSessionCreate(BaseModel):
    harness: str
    #: v1.412.0: "" (or absent) = no folder chosen: the session's first
    #: message makes it a folder of its own under the chat files folder.
    folder: str = ""
    account: str | None = None
    model: str | None = None
    effort: str | None = None
    mode: str | None = None
    #: Full access needs this (two presses on the page).
    confirm: bool = False
    title: str | None = None
    #: Open a conversation the harness already SAVED for this folder
    #: (``GET /build/app/sessions?folder=…&disk=1``).
    harness_session_id: str | None = None


class AppSessionPatch(BaseModel):
    title: str | None = None
    mode: str | None = None
    confirm: bool = False
    model: str | None = None
    effort: str | None = None


class AppTurn(BaseModel):
    text: str = ""
    turn_id: str = ""
    files: list[str] | None = None


class AppSteer(BaseModel):
    """v1.413.0: a note for the RUNNING reply (typed while it works)."""

    text: str = ""
    files: list[str] | None = None


class AppAnswer(BaseModel):
    request_id: str
    decision: str
    message: str | None = None
    answers: Any = None


class AppContinue(BaseModel):
    account: str | None = None


def _http(r: S.Refused) -> HTTPException:
    return HTTPException(status_code=r.status, detail=r.sentence)


def _refused(r: S.Refused) -> JSONResponse:
    """v1.412.0 review: a refusal that is about one choice of the start
    composer says WHICH (``field``) beside the plain sentence (``detail``), so
    the page puts it on that chip — a refused effort used to land on the
    folder chip."""
    content: dict[str, Any] = {"detail": r.sentence}
    if getattr(r, "field", ""):
        content["field"] = r.field
    if getattr(r, "code", ""):
        content["code"] = r.code
    return JSONResponse(status_code=r.status, content=content)


def register(app: FastAPI, d: Any) -> None:
    from .chat import _follow_response

    def svc() -> S.HostedService:
        return S.service_for(d.platform)

    @app.get("/build/app/harnesses")
    def app_harnesses() -> dict[str, Any]:
        """What the Code view can start: installed, signed in (CACHED verdicts
        only — never a probe), modes, models, efforts, accounts (the cached
        Iron-Proxy snapshot). Sync: a few cached reads, on the threadpool."""
        return {"harnesses": svc().harnesses()}

    @app.post("/build/app/sessions", status_code=201)
    async def app_create(body: AppSessionCreate) -> Any:
        try:
            return await svc().create(body.model_dump())
        except S.Refused as r:
            return _refused(r)

    @app.get("/build/app/sessions")
    async def app_list(archived: str = "", folder: str = "", disk: str = "", account: str = "") -> dict[str, Any]:
        """``disk=1`` + ``folder``: the tools' own saved conversations for that
        folder, read from ``account``'s home ("" = this PC's login)."""
        try:
            return await svc().list_sessions(
                archived=archived in ("1", "true", "yes"), folder=folder,
                disk=disk in ("1", "true", "yes"), account=account,
            )
        except S.Refused as r:
            raise _http(r) from None

    @app.get("/build/app/sessions/{sid}")
    async def app_get(sid: str, limit: int = S.DEFAULT_EVENTS) -> dict[str, Any]:
        try:
            return await svc().view(sid, limit=limit)
        except S.Refused as r:
            raise _http(r) from None

    @app.patch("/build/app/sessions/{sid}")
    async def app_patch(sid: str, body: AppSessionPatch) -> dict[str, Any]:
        try:
            return await svc().patch(sid, body.model_dump(exclude_unset=True))
        except S.Refused as r:
            raise _http(r) from None

    @app.post("/build/app/sessions/{sid}/archive")
    async def app_archive(sid: str) -> dict[str, Any]:
        try:
            return await svc().archive(sid, True)
        except S.Refused as r:
            raise _http(r) from None

    @app.post("/build/app/sessions/{sid}/unarchive")
    async def app_unarchive(sid: str) -> dict[str, Any]:
        try:
            return await svc().archive(sid, False)
        except S.Refused as r:
            raise _http(r) from None

    @app.delete("/build/app/sessions/{sid}")
    async def app_delete(sid: str) -> dict[str, Any]:
        try:
            return await svc().delete(sid)
        except S.Refused as r:
            raise _http(r) from None

    @app.post("/build/app/sessions/{sid}/turn")
    async def app_turn(sid: str, body: AppTurn):
        """The turn as SSE events (the contract). The turn is the DAEMON's:
        closing this response never stops it; ``GET …/turns/{id}/stream``
        rejoins it."""
        try:
            turn = await svc().start_turn(sid, body.text, body.turn_id, body.files)
        except S.Refused as r:
            raise _http(r) from None
        return _follow_response(turn.follow())

    @app.post("/build/app/sessions/{sid}/steer")
    async def app_steer(sid: str, body: AppSteer) -> Any:
        """v1.413.0: a note for the reply that is RUNNING, through the tool's
        own way of taking one; ``{ok, id, state, how}``. 409 ``code:
        not_working`` = no reply runs (send it as a message instead)."""
        try:
            return await svc().steer(sid, body.text, body.files)
        except S.Refused as r:
            return _refused(r)

    @app.get("/build/app/sessions/{sid}/turns/{turn_id}/stream")
    async def app_rejoin(sid: str, turn_id: str):
        service = svc()
        turn = service.turn(turn_id)
        if turn is not None and turn.sid == sid:
            return _follow_response(turn.follow())
        try:
            replay = await service.replay(sid, turn_id)
        except S.Refused as r:
            raise _http(r) from None
        if replay is None:
            raise HTTPException(status_code=404, detail="No such turn in this session.")

        async def once():
            yield replay

        return _follow_response(once())

    @app.get("/build/app/sessions/{sid}/commands")
    async def app_commands(sid: str) -> dict[str, Any]:
        """The harness's own `/` commands (what its process said; never a
        start just to ask)."""
        try:
            return {"commands": await svc().commands(sid)}
        except S.Refused as r:
            raise _http(r) from None

    @app.post("/build/app/sessions/{sid}/answer")
    async def app_answer(sid: str, body: AppAnswer) -> dict[str, Any]:
        try:
            return await svc().answer(sid, body.request_id, body.decision, body.message or "", body.answers)
        except S.Refused as r:
            raise _http(r) from None

    @app.post("/build/app/sessions/{sid}/interrupt")
    async def app_interrupt(sid: str) -> dict[str, Any]:
        try:
            return await svc().interrupt(sid)
        except S.Refused as r:
            raise _http(r) from None

    @app.post("/build/app/sessions/{sid}/open-terminal")
    async def app_open_terminal(sid: str) -> dict[str, Any]:
        try:
            return await svc().open_terminal(sid)
        except S.Refused as r:
            raise _http(r) from None

    @app.post("/build/app/from-terminal/{pane_id}", status_code=201)
    async def app_from_terminal(pane_id: str) -> dict[str, Any]:
        try:
            return await svc().from_terminal(pane_id)
        except S.Refused as r:
            raise _http(r) from None

    @app.post("/build/app/sessions/{sid}/continue-on")
    async def app_continue_on(sid: str, body: AppContinue | None = None) -> dict[str, Any]:
        try:
            return await svc().continue_on(sid, (body.account if body else "") or "")
        except S.Refused as r:
            raise _http(r) from None
