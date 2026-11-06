"""Build › Code's right panes (v1.414.0): READ-ONLY reads of one
session — its changes, its usage, its session facts. Every rule lives in
:mod:`iron_jarvis.hosted.panes`; each read runs off the event loop (git, the
events log, a process listing) and never starts the coding tool, a model call
or a probe.

v1.420.0 (C4.12): the Changes read takes ``scope=turn`` (one message's own
changes, ``turn=<id>`` or the newest), and ONE write — ``POST
…/changes/revert`` puts one file back (two presses on the page), under the
session's git lock, refused in words while a reply runs.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ...hosted import panes as PN
from ...hosted import service as S
from ...hosted import session_controls as SC
from ...hosted.events import EventLog

#: How many of the newest events the panes read (the log's own bound is 20k).
EVENTS_READ = 20_000


class RevertBody(BaseModel):
    """One file of the Changes pane to put back (its row's ``rel``, ``blob``
    and, for a rename, ``old_rel``; the scope the pane showed)."""

    path: str = Field(min_length=1, max_length=1_000)
    blob: str = Field(default="", max_length=80)
    old_rel: str = Field(default="", max_length=1_000)
    scope: str = Field(default=PN.SCOPE_SESSION, max_length=20)
    turn: str = Field(default="", max_length=120)


class McpActionBody(BaseModel):
    """v1.441.0 (g1): ``reconnect`` | ``off`` | ``on`` | ``sign_in``."""

    action: str = Field(min_length=1, max_length=20)


def _turn_running(live: Any) -> bool:
    turn = getattr(live, "turn", None) if live is not None else None
    return bool(turn is not None and getattr(turn, "running", False))


def register(app: FastAPI, d: Any) -> None:
    def svc() -> S.HostedService:
        return S.service_for(d.platform)

    async def _session(sid: str) -> tuple[Any, list[dict[str, Any]], Any]:
        service = svc()
        try:
            row = await service._row(sid)
        except S.Refused as r:
            raise HTTPException(status_code=r.status, detail=r.sentence) from None
        live = service.live.get(sid)
        log_ = live.log if live is not None else EventLog(service.home, sid)
        events, _total, _dropped = await asyncio.to_thread(log_.read, EVENTS_READ)
        return row, events, live

    def _tool_name(harness: str) -> str:
        from ...hosted.words import LABELS

        return LABELS.get(harness, "The coding tool")

    @app.get("/build/app/sessions/{sid}/changes")
    async def app_changes(sid: str, scope: str = PN.SCOPE_SESSION, turn: str = "") -> dict[str, Any]:
        """What this session changed, and from WHICH source (``git`` | ``tool``
        | ``none``) with its limits in words. ``scope=turn``: one message's
        own changes (v1.420.0). Revert is the one way back (git only)."""
        row, events, live = await _session(sid)
        return await asyncio.to_thread(
            PN.changes_view, row, events, _tool_name(row.harness),
            scope=PN.SCOPE_TURN if scope == PN.SCOPE_TURN else PN.SCOPE_SESSION,
            turn=str(turn or "")[:120], home=svc().home, sid=sid, running=_turn_running(live),
        )

    @app.post("/build/app/sessions/{sid}/changes/revert")
    async def app_changes_revert(sid: str, body: RevertBody) -> dict[str, Any]:
        """Put ONE file back as it was at the scope's start (v1.420.0, C4.12):
        the branch, its commits and every other file stay. Refused in words
        (409) while a reply runs, outside a repository, or when the file
        changed after the pane read it."""
        from .build_git import _locked
        from ...harness import branches as B

        row, events, live = await _session(sid)
        tool = _tool_name(row.harness)
        if _turn_running(live):
            raise HTTPException(status_code=409, detail=PN.REVERT_RUNNING.format(tool=tool))

        def act() -> dict[str, Any]:
            return PN.revert_file(
                svc().home, sid, row, events, tool, rel=body.path, blob=body.blob,
                scope=PN.SCOPE_TURN if body.scope == PN.SCOPE_TURN else PN.SCOPE_SESSION,
                turn=body.turn, running=_turn_running(live), old_rel=body.old_rel,
            )

        try:
            return await asyncio.to_thread(_locked, f"app:{sid}", act)
        except PN.RevertRefused as r:
            raise HTTPException(status_code=r.status, detail=r.sentence) from None
        except B.GitRefused as g:
            raise HTTPException(status_code=g.status, detail=str(g)) from None

    @app.get("/build/app/sessions/{sid}/usage")
    async def app_usage(sid: str) -> dict[str, Any]:
        """The account's windows (Iron-Proxy's cached snapshot and what the
        tool itself last reported) and the session's totals."""
        row, events, _live = await _session(sid)
        return await asyncio.to_thread(PN.usage_view, d.platform, row, events)

    @app.get("/build/app/sessions/{sid}/context")
    async def app_context(sid: str) -> dict[str, Any]:
        """v1.415.0: the Session tab's Context drop-down — what fills the
        conversation, in the tool's own numbers (Claude Code's ``/context``,
        Pi's stats) while its process runs and no reply is in the way, else
        the total it said after its last reply. Never starts the tool, never
        a model call."""
        row, events, _live = await _session(sid)
        return await svc().context_view(row, events)

    @app.get("/build/app/sessions/{sid}/overview")
    async def app_overview(sid: str) -> dict[str, Any]:
        """The Session tab's facts the page cannot hold: the branch, the
        instruction files, how full (as the tool said), what runs under it.
        v1.441.0 (g1): plus the tool's own background work it can stop
        (``background_work``, only when the tool offers it) and each MCP
        row's ``actions``."""
        row, events, live = await _session(sid)
        running = bool(live is not None and live.running)
        pid = live.proc.pid if running and live.proc is not None else None
        view = await asyncio.to_thread(PN.session_view, row, events, pid, running)
        return await SC.decorate_overview(view, live)

    @app.post("/build/app/sessions/{sid}/background/{work_id}/stop")
    async def app_background_stop(sid: str, work_id: str) -> dict[str, Any]:
        """v1.441.0 (g1): stop ONE of the tool's background tasks through the
        tool's own stop (Claude Code's ``stop_task``, Codex's background
        terminal ``terminate``). The reply, if one runs, goes on; what was
        stopped is said in the conversation."""
        try:
            return await SC.stop_background(svc(), sid, work_id[:80])
        except S.Refused as r:
            raise HTTPException(status_code=r.status, detail=r.sentence) from None

    @app.post("/build/app/sessions/{sid}/mcp/{server}")
    async def app_mcp_action(sid: str, server: str, body: McpActionBody) -> dict[str, Any]:
        """v1.441.0 (g1): one of the tool's own MCP actions (reconnect, turn
        off or on, sign in) — only those the tool offers. A sign-in answers
        the page to open in the user's browser; Iron Jarvis never signs in."""
        try:
            return await SC.mcp_action(svc(), sid, server, body.action)
        except S.Refused as r:
            raise HTTPException(status_code=r.status, detail=r.sentence) from None
