"""22-a (R1.9): the answer to Build › Code's first-time card under a reply,
"Run `npm test` after each reply in this folder?" (``hosted/proof``).

``POST /build/app/sessions/{sid}/checks`` ``{command, run, turn}``:

* ``run: true`` keeps EXACTLY the command the card showed as the folder's
  check (``folder_defaults.save_checks``, source "card") — refused in words
  (409) when the folder now proposes another command, or when the folder's
  own ``finish.toml`` turns the check off; then checks the reply the card
  was under (``check_again.recheck``: the same judge, tests first) when it is
  the session's newest and nothing runs.
* ``run: false`` keeps "not after replies here" for the folder; the card
  goes and the line under that reply says how to turn it back on.

Consent is exactly what the card said (``docs/rules/build.md``, v1.511.0):
a command the folder no longer proposes, or one the screen could not show
in full, is never kept from a press. Everything blocking runs off the loop.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ...hosted import proof as PROOF
from ...hosted import service as S

log = logging.getLogger(__name__)

#: The answer when the command the card showed is no longer the one asked.
COMMAND_CHANGED = (
    "This folder now proposes another check command, so that one was not kept. The next reply asks again "
    "with the new command."
)
#: The folder's own finish.toml has the say.
FOLDER_FILE_OFF = (
    "This folder's .ironjarvis/harness/finish.toml turns the check off, so nothing was kept. Change that "
    "file to let Iron Jarvis run its tests."
)
NOT_SHOWN_IN_FULL = (
    "That command cannot be shown in full on the card, so it is not kept from a press. Ask in chat: "
    "\"run tests after replies in <folder> with <command>\"."
)
NO_FOLDER = "This session has no folder yet, so there is nothing to check."
NOT_ASKED = "Nothing is being asked for this folder now."


class CheckAnswer(BaseModel):
    """The card's answer: the command it showed, yes or no, and the reply it
    was under."""

    command: str = Field(default="", max_length=600)
    run: bool
    turn: str = Field(default="", max_length=200)


def register(app: FastAPI, d: Any) -> None:
    def svc() -> S.HostedService:
        return S.service_for(d.platform)

    @app.post("/build/app/sessions/{sid}/checks")
    async def app_checks(sid: str, body: CheckAnswer) -> dict[str, Any]:
        from ...harness import folder_defaults as FD
        from ...hosted import check_again as CA

        service = svc()
        try:
            row = await service._row(sid)
        except S.Refused as r:
            raise HTTPException(status_code=r.status, detail=r.sentence) from None
        folder = str(getattr(row, "folder", "") or "")
        if not folder:
            raise HTTPException(status_code=409, detail=NO_FOLDER)
        engine = service.engine
        turn = str(body.turn or "").strip()
        if not body.run:
            kept = await asyncio.to_thread(FD.save_checks, engine, folder, {"off": True, "at": PROOF.now_iso()})
            if turn:
                await asyncio.to_thread(_note_off, service, sid, turn)
            return {"ok": True, "checks": kept, "words": PROOF.line_for(_off_record(turn))}
        plan = await asyncio.to_thread(PROOF.decide, engine, folder)
        state = plan.get("state")
        command = str(plan.get("command") or "")
        if state == "off" and plan.get("why") == PROOF.WHY_FOLDER_FILE:
            raise HTTPException(status_code=409, detail=FOLDER_FILE_OFF)
        if state == "run" and command == body.command:
            return {"ok": True, "checks": await asyncio.to_thread(FD.read_checks, engine, folder),
                    "checking": False}
        if state != "ask" or not command:
            raise HTTPException(status_code=409, detail=NOT_ASKED)
        if PROOF.shown_command(command) != command:
            raise HTTPException(status_code=409, detail=NOT_SHOWN_IN_FULL)
        if body.command != command:
            raise HTTPException(status_code=409, detail=COMMAND_CHANGED)
        try:
            kept = await asyncio.to_thread(FD.save_checks, engine, folder, {
                "command": command, "confirmed_at": PROOF.now_iso(), "timeout_s": PROOF.TIMEOUT_S,
                "source": "card",
            })
        except FD.DefaultsRefused as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        started = await CA.recheck(service, sid, turn) if turn else False
        return {"ok": True, "checks": kept, "checking": bool(started)}


def _off_record(turn: str) -> dict[str, Any]:
    return PROOF._record(turn, PROOF.TESTS_OFF, PROOF.WHY_YOU, answered=True)  # noqa: SLF001


def _note_off(service: Any, sid: str, turn: str) -> None:
    """The card under ``turn`` goes: its newest record says the answer."""
    from ...hosted.events import make

    live = service.live.get(sid)
    if live is not None:
        live.log.append(make(PROOF.EVENT, **_off_record(turn)))


__all__ = ["CheckAnswer", "register"]
