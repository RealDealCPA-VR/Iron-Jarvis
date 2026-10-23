"""Build's EXPERIENCE LIBRARY routes (v1.370.0, IJ harness plan Phase 7).

* ``GET /build/library?folder=<path>`` and ``GET /build/sessions/{id}/library``
  — a project folder's library: the lessons the user KEPT (they ride every
  later session in that folder), the harness edits accepted, and the
  PROPOSALS waiting (``{folder, folder_name, lessons, edits, proposals,
  open_limit}``);
* ``POST /build/library/{id}/accept`` — keep a lesson, or WRITE a proposed
  edit into the folder's harness file (409 in words when the file moved
  since, the folder cannot be written, or the edit would make the folder's
  harness files invalid — nothing is left half-written);
* ``POST /build/library/{id}/decline`` — no (the same suggestion is not made
  again for 14 days);
* ``DELETE /build/library/{id}`` — forget a kept lesson;
* ``POST /build/sessions/{id}/reflect {turn_id?}`` — reflect now on one
  finished turn, or (default) on every turn noted since the last reflection
  plus the newest, answering what was suggested or, in words, why nothing
  was (``{minted: [...], reason}``). The same reflector an idle session runs
  by itself; it costs one model call.

Nothing here writes into a user's folder except an Accept of an edit. Every
blocking step runs off the loop.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ...harness import library as L
from ...harness import sessions as S


class ReflectBody(BaseModel):
    turn_id: str = Field(default="", max_length=80)


def _refused(exc: L.LibraryRefused) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


def register(app: FastAPI, d: Any) -> None:
    engine_of = lambda: d.platform.engine  # noqa: E731 — read live (tests swap the platform)

    def _known_tools() -> "set[str] | None":
        try:
            return set(d.platform.registry.names())
        except Exception:  # noqa: BLE001
            return None

    @app.get("/build/library")
    def build_library(folder: str = "") -> dict[str, Any]:
        try:
            return L.view(engine_of(), S.resolved_folder(folder) if folder.strip() else "")
        except L.LibraryRefused as exc:
            raise _refused(exc) from None

    @app.get("/build/sessions/{thread_id}/library")
    def build_session_library(thread_id: str) -> dict[str, Any]:
        """The session folder's library, plus ``reflection: {due_at,
        running, turns}`` — when this session's next reflection is due (it
        runs once the session has been idle; the screen re-reads then)."""
        row = S.get_row(engine_of(), thread_id)
        if row is None:
            raise HTTPException(status_code=404, detail="no such Build session")
        if S.is_folderless(row):
            # v1.379.0: the experience library belongs to a FOLDER the user
            # works in again; a session with no folder has its own one-off
            # folder, so it neither keeps nor offers lessons.
            return {
                "folder": S.folder_of(row), "folder_name": S.NO_FOLDER_NAME, "lessons": [], "edits": [],
                "proposals": [], "open_limit": L.OPEN_LIMIT, "reflection": L.status_of(thread_id),
            }
        try:
            # v1.375.0: a branch's library is the picked folder's (S.checkout_of).
            return {**L.view(engine_of(), S.checkout_of(row)), "reflection": L.status_of(thread_id)}
        except L.LibraryRefused as exc:
            raise _refused(exc) from None

    @app.post("/build/library/{item_id}/accept")
    def accept_library_item(item_id: str) -> dict[str, Any]:
        try:
            return L.row_view(L.accept(engine_of(), item_id, known_tools=_known_tools()))
        except L.LibraryRefused as exc:
            raise _refused(exc) from None

    @app.post("/build/library/{item_id}/decline")
    def decline_library_item(item_id: str) -> dict[str, Any]:
        try:
            return L.row_view(L.decline(engine_of(), item_id))
        except L.LibraryRefused as exc:
            raise _refused(exc) from None

    @app.delete("/build/library/{item_id}")
    def forget_library_lesson(item_id: str) -> dict[str, Any]:
        try:
            return L.forget(engine_of(), item_id)
        except L.LibraryRefused as exc:
            raise _refused(exc) from None

    @app.post("/build/sessions/{thread_id}/reflect")
    async def reflect_on_turn(thread_id: str, body: ReflectBody | None = None) -> dict[str, Any]:
        from ...harness.rewind import turn_starts
        from .build import _running_turn

        platform = d.platform
        row = await asyncio.to_thread(S.get_row, platform.engine, thread_id)
        if row is None:
            raise HTTPException(status_code=404, detail="no such Build session")
        turn_id = (body.turn_id if body is not None else "").strip()
        turns: list[str] = [turn_id] if turn_id else []
        if not turn_id:
            starts = turn_starts(S._messages(row))
            if not starts:
                raise HTTPException(status_code=409, detail="This session has no turn to learn from yet.")
            turn_id = starts[-1][1]["turnId"]
            # The turns noted since the last reflection (the idle wait's
            # stretch) are reflected on now, in this ONE call.
            turns = L.take_stretch(thread_id) or [turn_id]
            if turn_id not in turns and S.has_turn_reply(row, turn_id):
                turns.append(turn_id)
        if _running_turn(d, thread_id) and not S.has_turn_reply(row, turn_id):
            raise HTTPException(status_code=409, detail="That turn is still answering.")
        out = await L.reflect(platform, thread_id, turns)
        return {"minted": [L.row_view(r) for r in out.minted], "reason": out.reason, "turn_id": turn_id,
                "turns": turns}
