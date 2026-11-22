"""BEFORE AND AFTER PICTURES under a coding reply — the routes (R2.15, phase
27-a, ``harness/evidence``).

* ``POST /build/sessions/{id}/evidence {turn_id, preview_url}`` — Show me:
  the after picture of the page open in the session's Browser, taken now for
  that reply (its before, when one was taken, is kept), written onto the
  reply's line. Refused in words while a reply runs, for an archived session,
  for a reply that is not in the session, and with no page open. Because the
  picture is of the page AS IT IS NOW, it is taken only for the session's
  NEWEST reply, only one that changed a screen file, and never over an after
  picture the reply already has (:func:`not_offered`; asked again once the
  picture is taken, before it is kept). A picture that cannot be taken is
  said (409), never invented; nothing changes then.
  The answer carries ``offer``: True ONCE ever, while "Take before and after
  pictures by themselves" was never chosen (decision D-27.1: the automatic
  setting is offered once, naming its cost).
* ``GET /build/sessions/{id}/evidence/{turn}/{item}`` — a kept picture
  (``item`` = ``before`` | ``after``), to the user's screen only.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from ...harness import evidence as E
from ...harness import sessions as S

NO_SESSION = "no such coding session"
NO_REPLY = "That reply is not in this session any more."
ARCHIVED = "This session is archived. Bring it back to take pictures."
RUNNING = "A reply is still running in this session. Press Show me when it has finished."
NO_PAGE = "Open the page in the Browser first, then press Show me."
NOT_KEPT = "This picture is no longer kept."
#: Show me pictures the page AS IT IS NOW, so only the reply that ends the
#: conversation may file it as its after picture (an earlier reply's would
#: show the later replies' changes as its own).
NOT_NEWEST = ("Show me takes the page as it is now, so it is offered only under the newest reply. "
              "A later reply has come since this one.")
NO_SCREEN = "That reply changed no screen file, so there is no picture to take for it."
STARTED_MEANWHILE = "A reply started while the picture was being taken, so it was not kept. Press Show me when it has finished."

#: One Show me at a time per session (a second press waits for the first).
_LOCKS: dict[str, asyncio.Lock] = {}


class EvidenceBody(BaseModel):
    turn_id: str = Field(min_length=1, max_length=80)
    #: The Browser's address now (an over-long or non-local one is dropped,
    #: never a 422 — the turn route's own rule).
    preview_url: str = ""


def _reply_line(row: Any, turn_id: str) -> "dict[str, Any] | None":
    for m in reversed(S._messages(row)):
        if m.get("role") == "assistant" and m.get("turnId") == turn_id:
            return m
    return None


def not_offered(row: Any, turn_id: str, *, after_too: bool = True) -> str:
    """Why Show me is NOT for reply ``turn_id`` of this session ("" when it
    is): it must be the reply that ends the conversation (the daemon's own
    hidden "carry on" questions aside, as the screen hides them), it must have
    changed a screen file, and (``after_too``) it must not have its after
    picture yet — that one shows the page as the reply left it, and is kept.
    (Before the picture, ``evidence.take_after_now`` itself refuses a line
    that has its after; after it, this is asked again with ``after_too``.)
    Pure."""
    msgs = [m for m in S._messages(row) if m.get("continuation") is not True]
    line = _reply_line(row, turn_id)
    if line is None:
        return NO_REPLY
    last = msgs[-1] if msgs else None
    if last is None or last.get("role") != "assistant" or last.get("turnId") != turn_id:
        return NOT_NEWEST
    if E.saved_record(line.get("evidence")) is None:
        return NO_SCREEN
    if after_too and E.has_after(line.get("evidence")):
        return E.HAS_AFTER_WORDS
    return ""


def register(app: FastAPI, d: Any) -> None:
    from ...harness.preview import local_address
    from ...providers import egress as _egress
    from . import build as _build

    def engine() -> Any:
        return d.platform.engine

    @app.post("/build/sessions/{thread_id}/evidence")
    async def show_me(thread_id: str, body: EvidenceBody) -> Any:
        """Show me: the after picture of the reply ``turn_id``, now."""
        row = await asyncio.to_thread(S.get_row, engine(), thread_id)
        if row is None:
            return JSONResponse(status_code=404, content={"detail": NO_SESSION})
        if getattr(row, "archived_at", None) is not None:
            return JSONResponse(status_code=409, content={"detail": ARCHIVED})
        if thread_id in _build._STARTING or _build._running_turn(d, thread_id):
            return JSONResponse(status_code=409, content={"detail": RUNNING})
        # The conversation's JSON is read in a worker, never on the loop
        # (a long session's transcript is large; review note).
        line = await asyncio.to_thread(_reply_line, row, body.turn_id)
        if line is None:
            return JSONResponse(status_code=404, content={"detail": NO_REPLY})
        why = await asyncio.to_thread(not_offered, row, body.turn_id, after_too=False)
        if why:
            return JSONResponse(status_code=409, content={"detail": why})
        setup = _build._setup_of(row)
        address = local_address(body.preview_url) or local_address(str(setup.get("preview_url") or ""))
        if not address:
            return JSONResponse(status_code=409, content={"detail": NO_PAGE})
        home = d.platform.config.home

        async def still() -> str:
            # Asked again once the picture is taken, before it is kept: a
            # reply that started meanwhile (the page may be mid-change) or a
            # line that moved means the picture is not this reply's after.
            if thread_id in _build._STARTING or _build._running_turn(d, thread_id):
                return STARTED_MEANWHILE
            now = await asyncio.to_thread(S.get_row, engine(), thread_id)
            if now is None:
                return NO_SESSION
            if getattr(now, "archived_at", None) is not None:
                return ARCHIVED
            return await asyncio.to_thread(not_offered, now, body.turn_id)

        lock = _LOCKS.setdefault(thread_id, asyncio.Lock())
        async with lock:
            # A Local only session's page is opened behind the refusing proxy
            # (nothing it names leaves this PC or your network).
            with _egress.local_only_scope(setup.get("local_only") is True):
                try:
                    # The line as it is NOW (a press that waited on the lock
                    # finds the after the first one kept).
                    now = await asyncio.to_thread(S.get_row, engine(), thread_id)
                    current = await asyncio.to_thread(_reply_line, now, body.turn_id) if now is not None else None
                    rec = await E.take_after_now(
                        home, thread_id, body.turn_id, address,
                        (current or line).get("evidence"), still=still,
                    )
                except E.Refused as exc:
                    return JSONResponse(status_code=409, content={"detail": str(exc)})
                except E.PictureProblem as exc:
                    return JSONResponse(status_code=409, content={"detail": f"No picture: {E._plain(str(exc))}"})
                except Exception:  # noqa: BLE001 — said, never a made-up picture
                    E.log.warning("Show me failed for build session %s", thread_id, exc_info=True)
                    return JSONResponse(status_code=409, content={
                        "detail": "No picture: it could not be taken. Try again in a moment.",
                    })
            # Kept on the line only while it still has no after picture.
            saved = await asyncio.to_thread(
                S.amend_reply, engine(), thread_id, body.turn_id, {"evidence": rec},
                expect=lambda stored: not E.has_after(stored.get("evidence")),
            )
        if not saved:
            return JSONResponse(status_code=409, content={
                "detail": "The picture was taken but could not be kept on the reply. Try again.",
            })
        offer = E.offer_due(d.platform.config)
        if offer:
            await asyncio.to_thread(E.mark_offered, home)
        return {"evidence": rec, "offer": offer}

    @app.get("/build/sessions/{thread_id}/evidence/{turn_id}/{item}")
    async def evidence_picture(thread_id: str, turn_id: str, item: str) -> Any:
        """A kept picture of a reply (to the user's screen only)."""
        row = await asyncio.to_thread(S.get_row, engine(), thread_id)
        if row is None:
            return JSONResponse(status_code=404, content={"detail": NO_SESSION})
        path = await asyncio.to_thread(E.picture_file, d.platform.config.home, thread_id, turn_id, item)
        if path is None:
            return JSONResponse(status_code=404, content={"detail": NOT_KEPT})
        try:
            data = await asyncio.to_thread(path.read_bytes)
        except OSError:
            return JSONResponse(status_code=404, content={"detail": NOT_KEPT})
        return Response(content=data, media_type="image/png")
