"""Leap 06-a: rehearsals — run a rule or a schedule once, changing nothing.

``POST /rehearsals {kind, id}`` starts one through the subject's own door
(``tools/rehearsal_runs.start``); ``GET /rehearsals?kind=`` the reports its
page shows again (still running, or finished and not kept);
``GET /rehearsals/{id}`` is its report,
built from the ledger every time it is read; ``POST /rehearsals/{id}/keep``
stamps ``rehearsed_at`` on the rule or schedule; ``POST /rehearsals/{id}/stop``
stops its runs. Chat reaches the same door through the ``rehearse`` tool.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, HTTPException

from ...tools import rehearsal_runs as _runs
from ..schemas import RehearsalBody


def _http(exc: "_runs.RehearsalRefused") -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.words)


def register(app: FastAPI, d) -> None:
    @app.post("/rehearsals")
    async def start_rehearsal(body: RehearsalBody) -> dict[str, Any]:
        try:
            return await _runs.start(d.platform, body.kind, body.id)
        except _runs.RehearsalRefused as exc:
            raise _http(exc) from None

    @app.get("/rehearsals")
    async def open_rehearsals(kind: str = "") -> dict[str, Any]:
        """The reports a page shows again (a reload, a second Rehearse):
        each subject's newest, still running or finished and not kept."""
        try:
            return {"rehearsals": await asyncio.to_thread(_runs.open_reports, d.platform, kind)}
        except _runs.RehearsalRefused as exc:
            raise _http(exc) from None

    @app.get("/rehearsals/{rehearsal_id}")
    async def get_rehearsal(rehearsal_id: str) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(_runs.report, d.platform, rehearsal_id)
        except _runs.RehearsalRefused as exc:
            raise _http(exc) from None

    @app.post("/rehearsals/{rehearsal_id}/keep")
    async def keep_rehearsal(rehearsal_id: str) -> dict[str, Any]:
        try:
            return await _runs.keep(d.platform, rehearsal_id)
        except _runs.RehearsalRefused as exc:
            raise _http(exc) from None

    @app.post("/rehearsals/{rehearsal_id}/stop")
    async def stop_rehearsal(rehearsal_id: str) -> dict[str, Any]:
        try:
            return await _runs.stop(d.platform, rehearsal_id)
        except _runs.RehearsalRefused as exc:
            raise _http(exc) from None
