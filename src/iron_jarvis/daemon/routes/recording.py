"""A recording's card (idea 33, phase 33-a, "Hand it a recording").

* ``GET  /documents/recording/{recording_id}`` — the card as it is now (how
  far the speech is, then the notes with their times). The card reads it
  only while it is on screen and the job is still working.
* ``POST /documents/recording/{recording_id}/notes`` — "Write the notes with
  <model>?": the transcript goes to THE MODEL THE CARD NAMED (``provider``)
  and to no other, each call pinned to it. If the choice changed since (a
  model on this PC or your network was connected, Local only came on),
  nothing is sent to the named one and the card says what changed. The yes
  is kept for the chat: a later recording in it does not ask again for that
  model (D-33.1).

LOCAL ONLY on the press: the request's flag OR the turn's verdict kept with
the job OR the saved chat's setting. Every blocking step runs off the loop.
Registered from ``routes/documents.register``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, HTTPException

from ...documents import recording as R
from ...documents import recording_jobs as J
from ..schemas import RecordingNotesBody


def env_of(d: Any) -> J.Env:
    platform = d.platform
    return J.Env(engine=platform.engine, event_bus=getattr(platform, "event_bus", None),
                 config=platform.config, router_resolver=lambda: getattr(platform, "router", None))


def register(app: FastAPI, d) -> None:
    @app.get("/documents/recording/{recording_id}")
    async def recording_card(recording_id: str) -> dict[str, Any]:
        if not R.valid_recording_id(recording_id):
            raise HTTPException(status_code=404, detail="There is no such recording.")
        job = J.live(recording_id)
        state = job.state if job is not None else await asyncio.to_thread(
            J.load_state, d.platform.config.home, recording_id)
        if state is None:
            raise HTTPException(status_code=404, detail="That recording is no longer kept.")
        return J.card_of(state)

    @app.post("/documents/recording/{recording_id}/notes")
    async def recording_notes(recording_id: str, body: RecordingNotesBody) -> dict[str, Any]:
        if not R.valid_recording_id(recording_id):
            raise HTTPException(status_code=404, detail="There is no such recording.")
        from ...providers import egress as _egress

        lo = await _egress.effective_local_only(d.platform.engine, body.local_only is True, body.thread_id)
        return await J.notes_after_yes(
            env_of(d), recording_id, provider=str(body.provider or ""), model=body.model or None,
            thread_id=str(body.thread_id or ""), local_only=lo,
        )


__all__ = ["env_of", "register"]
