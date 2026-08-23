"""Goal-contract routes (v1.208.0): ``/goals`` is THE public Goals surface.

The path is deliberate: ``/goals`` used to serve the Motivation Layer's
lightweight intent goals; those relocated VERBATIM to ``/autonomy/goals``
(``routes/autonomy.py``) so one public "Goals" concept remains — the
CONTRACTED kind (``goals/``: checkable contract text, hard budget,
deterministic verifier, circuit breaker, restart-surviving lifecycle).

Division of labor, stated once so it cannot drift:

* **Validation with a WHY lives in the store** (deny-floor grants, budget
  rules, verifier rules — ``GoalStore.create`` / ``goals/models.py``). This
  module maps its ``ValueError`` to a 400 with the text VERBATIM and never
  re-derives a rule: two copies of the deny floor is how one of them rots.
* **Transitions are guarded in the store** (``GOAL_TRANSITIONS``); a guard
  refusal maps to 409 with the store's sentence verbatim. Unknown ids are a
  404 checked here first, so "no such goal" never masquerades as a conflict.
* **An honest refusal from ``run_iteration`` is a RESULT, not an error**:
  paused/tripped/budget-exhausted/already-running come back 200 with
  ``{ok: false, refused: true, reason}`` — the caller asked "may an iteration
  run now?" and got a truthful no; a 4xx would teach clients that asking is
  dangerous.
* **Resume is state-aware**: a paused goal resumes via
  ``transition("active")``; a TRIPPED goal resumes via ``reopen`` — the
  breaker is CLEARED, because the dashboard promises "Resume clears it" and a
  resume that leaves stale failures in the window re-trips on the next
  hiccup. Every other resurrection (satisfied/failed/stopped → active) stays
  behind the explicit ``/reopen`` door, exactly as the store's transition
  guard words it.

Off-loop notes (v1.153.1): store calls are single-row SQLite reads/writes —
the sync handlers run in FastAPI's threadpool. ``create_goal`` /
``stop_goal`` / ``run_iteration`` are engine coroutines (they publish events
and, for run, await the whole session) and are awaited directly, per the
engine's own contracts.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from ...goals.models import GOAL_STATES
from ...goals.store import goal_view
from ..schemas import GoalContractCreate


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""

    def _engine():
        eng = getattr(d.platform, "goal_engine", None)
        if eng is None:
            raise HTTPException(
                status_code=503, detail="the goal engine is not available on this build"
            )
        return eng

    def _record_or_404(goal_id: str):
        record = _engine().store.get(goal_id)
        if record is None:
            raise HTTPException(status_code=404, detail="goal not found")
        return record

    @app.get("/goals")
    def list_goal_contracts(state: str | None = None) -> dict[str, Any]:
        if state is not None and state not in GOAL_STATES:
            # An unknown state silently answering [] would read as "no goals";
            # name the vocabulary instead.
            raise HTTPException(
                status_code=400,
                detail=f"unknown goal state {state!r}; expected one of {GOAL_STATES}",
            )
        return {"goals": [goal_view(g) for g in _engine().store.list(state)]}

    @app.post("/goals")
    async def create_goal_contract(body: GoalContractCreate) -> dict[str, Any]:
        try:
            record = await _engine().create_goal(**body.model_dump(), origin="api")
        except ValueError as exc:
            # The store's refusal sentence VERBATIM — it already says why and
            # what to do instead (deny floor, budget rules, verifier rules).
            raise HTTPException(status_code=400, detail=str(exc))
        return {"goal": goal_view(record)}

    @app.post("/goals/{goal_id}/run")
    async def run_goal_contract(goal_id: str) -> dict[str, Any]:
        """One iteration NOW. The engine's result dict is relayed as-is:
        refusals are ``{ok: false, refused: true, reason}`` at 200 — an honest
        refusal is a result, not an error — and an unknown id answers the
        engine's own ``{ok: false, reason: "unknown goal …"}`` shape."""
        return await _engine().run_iteration(goal_id)

    @app.post("/goals/{goal_id}/pause")
    def pause_goal_contract(goal_id: str) -> dict[str, Any]:
        _record_or_404(goal_id)
        try:
            record = _engine().store.transition(goal_id, "paused")
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"goal": goal_view(record)}

    @app.post("/goals/{goal_id}/resume")
    def resume_goal_contract(goal_id: str) -> dict[str, Any]:
        """Paused → active via the guarded transition; TRIPPED → active via
        ``reopen`` so the breaker is cleared (see the module docstring). Any
        other state gets the transition guard's refusal verbatim as a 409."""
        record = _record_or_404(goal_id)
        try:
            if record.state == "tripped":
                record = _engine().store.reopen(goal_id)
            else:
                record = _engine().store.transition(goal_id, "active")
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"goal": goal_view(record)}

    @app.post("/goals/{goal_id}/stop")
    async def stop_goal_contract(goal_id: str) -> dict[str, Any]:
        _record_or_404(goal_id)
        try:
            # Through the ENGINE, not the bare store: stop publishes
            # ``goal.stopped`` so the dashboard surfaces refresh.
            record = await _engine().stop_goal(goal_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"goal": goal_view(record)}

    @app.post("/goals/{goal_id}/reopen")
    def reopen_goal_contract(goal_id: str) -> dict[str, Any]:
        _record_or_404(goal_id)
        try:
            record = _engine().store.reopen(goal_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"goal": goal_view(record)}

    @app.delete("/goals/{goal_id}")
    def delete_goal_contract(goal_id: str) -> dict[str, Any]:
        if not _engine().store.remove(goal_id):
            raise HTTPException(status_code=404, detail="goal not found")
        return {"deleted": goal_id}
