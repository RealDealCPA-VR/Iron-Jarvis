"""Where a record reports (idea 39, 39-a: everything reports back to the chat
that asked).

* ``POST /reports/target {kind, id, thread_id}`` → ``{ok, kind, id,
  thread_id, previous}``: point a schedule, watcher, goal, workflow run or
  night task at a saved chat (``thread_id``), or stop its reports
  (``thread_id: ""``, the **Stop reporting** press on its row and on each
  report). ``previous`` is the chat it reported to before, so the page's Undo
  puts it back with the same call. 400 in words for an unknown kind, a record
  that is not there, or a chat that cannot take reports (not saved, a coding
  session, a phone conversation).

Chat reaches the same field through ``schedule_update`` / ``sentinel_update``
(``report_here``), with the usual card and Undo. The write is one SQLite row
(``agents/chat_delivery.set_report_target``), so the handler is a plain
``def`` (FastAPI runs it off the loop).
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel


class ReportTargetBody(BaseModel):
    """``POST /reports/target``: which record, and the chat ("" = none)."""

    kind: str
    id: str
    thread_id: str = ""


def register(app: Any, d: Any) -> None:
    @app.post("/reports/target")
    def set_report_target(body: ReportTargetBody) -> dict[str, Any]:
        from ...agents import chat_delivery as _cd

        if body.kind not in _cd.REPORT_TARGET_KINDS:
            raise HTTPException(status_code=400, detail="That does not report to a chat.")
        out = _cd.set_report_target(d.platform.engine, body.kind, body.id, body.thread_id)
        if not out.get("ok"):
            raise HTTPException(status_code=400, detail=str(out.get("why") or "That could not be changed."))
        return {
            "ok": True,
            "kind": body.kind,
            "id": body.id,
            "thread_id": out.get("thread_id") or "",
            "previous": out.get("previous") or "",
        }
