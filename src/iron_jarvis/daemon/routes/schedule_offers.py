"""The answers to an offer to run a repeated task by itself (next leaps 19-b,
``scheduling/offers``), waiting in Improvements beside its skill draft.

* ``POST /schedules/offers/{id}/accept`` — the schedule is made, switched
  on, through the add-time rules every door that saves a schedule applies
  (``scheduling/checks``). The page takes two presses (it starts work by
  itself; the item's ``confirm``), chat's ``suggestion_answer`` asks on its
  card. Answers ``{ok, schedule, note}``.
* ``POST /schedules/offers/{id}/decline`` — final for this task: no offer
  for it again. Answers ``{ok, note}``.
* ``POST /schedules/offers/{id}/rehearse`` — 06-a's rehearsal of the
  schedule Accept would make, run once now through the schedule's own door,
  changing nothing (``tools/rehearsal_runs.start_offer``); answers the
  report, read again at ``GET /rehearsals/{id}``.

404 for an offer that does not exist, 409 for one already answered or one
the rules refuse now (in words). Nothing here runs on a clock.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, HTTPException


def register(app: FastAPI, d) -> None:
    """Attach the routes to *app*; ``d`` is the create_app deps object."""

    @app.post("/schedules/offers/{offer_id}/accept")
    async def accept_schedule_offer(offer_id: str) -> dict[str, Any]:
        from ...scheduling import offers as O

        try:
            # Off the loop: the add-time rules may probe a folder, and every
            # read and write here is SQLite.
            return await asyncio.to_thread(O.accept, d.platform, offer_id)
        except O.OfferRefused as exc:
            raise HTTPException(exc.status, exc.words) from None

    @app.post("/schedules/offers/{offer_id}/decline")
    def decline_schedule_offer(offer_id: str) -> dict[str, Any]:
        from ...scheduling import offers as O

        try:
            return O.decline(d.platform.engine, offer_id)
        except O.OfferRefused as exc:
            raise HTTPException(exc.status, exc.words) from None

    @app.post("/schedules/offers/{offer_id}/rehearse")
    async def rehearse_schedule_offer(offer_id: str) -> dict[str, Any]:
        from ...tools import rehearsal_runs as _runs

        try:
            return await _runs.start_offer(d.platform, offer_id)
        except _runs.RehearsalRefused as exc:
            raise HTTPException(exc.status, exc.words) from None
