"""Build › Code's right panes (v1.414.0): three READ-ONLY reads of one
session — its changes, its usage, its session facts. Every rule lives in
:mod:`iron_jarvis.hosted.panes`; each read runs off the event loop (git, the
events log, a process listing) and never starts the coding tool, a model call
or a probe.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, HTTPException

from ...hosted import panes as PN
from ...hosted import service as S
from ...hosted.events import EventLog

#: How many of the newest events the panes read (the log's own bound is 20k).
EVENTS_READ = 20_000


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
    async def app_changes(sid: str) -> dict[str, Any]:
        """What this session changed, and from WHICH source (``git`` | ``tool``
        | ``none``) with its limits in words. No Undo."""
        row, events, _live = await _session(sid)
        return await asyncio.to_thread(PN.changes_view, row, events, _tool_name(row.harness))

    @app.get("/build/app/sessions/{sid}/usage")
    async def app_usage(sid: str) -> dict[str, Any]:
        """The account's windows (Iron-Proxy's cached snapshot and what the
        tool itself last reported) and the session's totals."""
        row, events, _live = await _session(sid)
        return await asyncio.to_thread(PN.usage_view, d.platform, row, events)

    @app.get("/build/app/sessions/{sid}/overview")
    async def app_overview(sid: str) -> dict[str, Any]:
        """The Session tab's facts the page cannot hold: the branch, the
        instruction files, how full (as the tool said), what runs under it."""
        row, events, live = await _session(sid)
        running = bool(live is not None and live.running)
        pid = live.proc.pid if running and live.proc is not None else None
        return await asyncio.to_thread(PN.session_view, row, events, pid, running)
