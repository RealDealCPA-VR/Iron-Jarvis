"""Build › Code carrying on after an interruption (v1.422.0, Wave 2 "Never
lose work"): the two presses the page has beside what carries on by itself
(``hosted/carry_on``).

* ``POST /build/app/sessions/{sid}/continue`` — "Continue" on a reply a
  restart cut off that was not carried on by itself (a card or a question
  was open, it was already carried on once, Emergency stop was on…). The
  same hidden message the restart pass sends, started by a PERSON. Answers
  ``{ok, turn_id}``; the page then follows the running turn as on any read.
* ``POST /build/app/sessions/{sid}/wait/cancel`` — Cancel on a limit card
  that waits for its account's reset (or looks for another account of its
  group) by itself: the wait ends, the card and its press stay.

Thin: every rule lives in :mod:`iron_jarvis.hosted.carry_on`.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, HTTPException

from ...hosted import carry_on as CO
from ...hosted import service as S


def register(app: FastAPI, d: Any) -> None:
    def svc() -> S.HostedService:
        return S.service_for(d.platform)

    @app.post("/build/app/sessions/{sid}/continue")
    async def app_continue_cut(sid: str) -> dict[str, Any]:
        try:
            return await CO.continue_cut(svc(), sid)
        except S.Refused as r:
            raise HTTPException(status_code=r.status, detail=r.sentence) from None

    @app.post("/build/app/sessions/{sid}/wait/cancel")
    async def app_cancel_wait(sid: str) -> dict[str, Any]:
        service = svc()
        try:
            await service._row(sid)
        except S.Refused as r:
            raise HTTPException(status_code=r.status, detail=r.sentence) from None
        ended = await asyncio.to_thread(CO.cancel_wait, service.home, sid)
        if not ended:
            raise HTTPException(status_code=409, detail=CO.NO_WAIT)
        return {"ok": True, "account_wait": None}
