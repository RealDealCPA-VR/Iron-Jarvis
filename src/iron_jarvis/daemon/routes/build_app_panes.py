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
import logging
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ...hosted import panes as PN
from ...hosted import service as S
from ...hosted import session_controls as SC
from ...hosted.events import EventLog

log = logging.getLogger(__name__)

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


def _works_alone(live: Any, now: float) -> bool:
    """v1.510.0: the tool writes in the folder with no reply of the user's
    running — Pi's own run, a reply the tool writes by itself, its helper
    agents (``LiveSession``'s own readings)."""
    if live is None:
        return False
    replying = getattr(live, "replying_alone", None)
    helping = getattr(live, "helpers_working", None)
    return bool(
        getattr(live, "working_alone", False)
        or (callable(replying) and replying(now))
        or (callable(helping) and helping(now))
    )


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

        from ...hosted import handoff as H
        from ...hosted.events import make

        service = svc()
        row, events, live = await _session(sid)
        tool = _tool_name(row.harness)
        if _turn_running(live):
            raise HTTPException(status_code=409, detail=PN.REVERT_RUNNING.format(tool=tool))
        if _works_alone(live, service.clock()):
            # v1.510.0 (H5 guard): work in the folder no reply of the user's
            # owns — the states "Close it now" waits for.
            raise HTTPException(status_code=409, detail=PN.REVERT_TOOL_BUSY.format(tool=tool))
        helpers = await asyncio.to_thread(H._helper_rows, service, sid)
        if any(H._running(service, h.id) for h in helpers):
            # A helper session (Hand off) works in this SAME folder.
            raise HTTPException(status_code=409, detail=PN.REVERT_HELPER_BUSY)

        def act() -> dict[str, Any]:
            return PN.revert_file(
                service.home, sid, row, events, tool, rel=body.path, blob=body.blob,
                scope=PN.SCOPE_TURN if body.scope == PN.SCOPE_TURN else PN.SCOPE_SESSION,
                turn=body.turn, running=_turn_running(live), old_rel=body.old_rel,
            )

        def locked_act() -> dict[str, Any]:
            # v1.543.0: the folder's own lock too (a rewind's whole-folder
            # restore takes it; ``checkpoints.folder_locked``).
            from ...hosted import checkpoints as CK

            folder = str(getattr(row, "folder", "") or "")
            return _locked(f"app:{sid}", (lambda: CK.folder_locked(folder, act)) if folder else act)

        try:
            out = await asyncio.to_thread(locked_act)
        except PN.RevertRefused as r:
            raise HTTPException(status_code=r.status, detail=r.sentence) from None
        except B.GitRefused as g:
            raise HTTPException(status_code=g.status, detail=str(g)) from None
        # v1.510.0 (H5): the session's own conversation says what was put
        # back and where the replaced version is (the log only queues: the
        # write is its writer thread's). Never fails the revert.
        name = str(out.get("rel") or body.path).rsplit("/", 1)[-1]
        kept = str(out.get("moved_to") or "")
        text = PN.REVERT_NOTICE.format(name=name, trash=kept) if kept else PN.REVERT_NOTICE_PLAIN.format(name=name)
        try:
            service._emit(service._live(row), make("notice", text=text))
        except Exception:  # noqa: BLE001 — the file is already back
            log.warning("could not say a revert in a Code-view session", exc_info=True)
        return out

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
        view = await asyncio.to_thread(PN.session_view, row, events, pid, running, d.platform)
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

    @app.post("/build/app/sessions/{sid}/mcp-reload")
    async def app_mcp_reload(sid: str) -> dict[str, Any]:
        """v1.447.0: the tool reads its own MCP settings again (Codex's
        ``config/mcpServer/reload``) — only a tool that offers it, only while
        no reply runs."""
        try:
            return await SC.mcp_reload(svc(), sid)
        except S.Refused as r:
            raise HTTPException(status_code=r.status, detail=r.sentence) from None
