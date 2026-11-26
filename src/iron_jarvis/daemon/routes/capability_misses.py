"""The answers to a thing you asked for that Iron Jarvis can't do yet (next
leaps 20-a, ``capability_search/misses``).

* ``POST /capability/misses/{id}/note`` — "Keep as a note for the next
  version": the group is kept on this PC as a note (20-c lists the notes and
  copies one for the makers). Nothing is sent anywhere (D-20.2).
* ``POST /capability/misses/{id}/decline`` — "Not needed": the group is not
  listed again, and a later ask for the same thing joins it quietly.

Both answer ``{id, status, note}`` (``note`` is the sentence the page and
chat say after the press), 404 for a group that does not exist, 409 for one
already answered. They save the answer and nothing else. The Improvements
item names these routes (``inbox/collect.missing_item``), the page presses
them (``lib/improvements.DECISION_PATHS``), and chat's ``suggestion_answer``
presses the same ones through the app's router. SQLite: plain ``def``
handlers (FastAPI runs them off the loop).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

NOTED_NOTE = "Kept on this PC as a note for the next version of Iron Jarvis. Nothing was sent anywhere."
DECLINED_NOTE = "Iron Jarvis won't list this again."


def register(app: FastAPI, d) -> None:
    """Attach the routes to *app*; ``d`` is the create_app deps object."""

    def _answer(group_id: str, status: str, note: str) -> dict[str, Any]:
        from ...capability_search import misses as M

        try:
            got = M.answer(d.platform.engine, group_id, status)
        except M.NotFound:
            raise HTTPException(404, "That request is not on the list any more.") from None
        except M.AlreadyAnswered:
            raise HTTPException(409, "That request was already answered.") from None
        return {**got, "note": note}

    @app.post("/capability/misses/{group_id}/note")
    def capability_miss_note(group_id: str) -> dict[str, Any]:
        from ...capability_search.miss_models import NOTED

        return _answer(group_id, NOTED, NOTED_NOTE)

    @app.post("/capability/misses/{group_id}/decline")
    def capability_miss_decline(group_id: str) -> dict[str, Any]:
        from ...capability_search.miss_models import DECLINED

        return _answer(group_id, DECLINED, DECLINED_NOTE)
