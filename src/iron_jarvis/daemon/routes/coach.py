"""The reflection coach's routes (v1.297.0).

* ``GET  /agents/{name}/coach`` → ``{report, proposals: [pending…], last_reason}``
  — the pure ledger report (signals per run + clusters) and what is waiting;
* ``POST /agents/{name}/coach`` → runs ``propose`` (the ledger reads off-loop,
  ONE async model call) → ``{proposal | null, reason}``;
* ``GET  /coach/proposals?agent=&status=``;
* ``POST /coach/proposals/{id}/accept`` | ``/decline`` → ``{proposal}``.

Refusals are plain sentences: 404 for an unknown agent or proposal, 409 for
a proposal already decided or whose agent's instructions moved since it was
written (``stale`` — "ask the coach again"), 422 for a bad status filter,
503 when the daemon has no coach. ``propose`` itself never raises for "not
now" — it answers ``{"proposal": null, "reason": …}`` so the Agents page can
show why, including the honest-mock refusal.

Closure-local state is reached through ``d`` (the create_app deps object),
like every other ``routes/*.py``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, HTTPException

from ...coach.engine import StaleProposal, proposal_view
from ...coach.models import PENDING, STATUSES


def _coach(d):
    coach = getattr(d.platform, "coach", None)
    if coach is None:
        raise HTTPException(
            status_code=503,
            detail="the reflection coach is not available on this daemon — restart it",
        )
    return coach


def _known(d, name: str) -> str:
    """The roster name for ``name`` (a custom agent's bare slug or roster
    form, or a builtin type), or a 404 sentence."""
    from ...core.models import AgentType

    raw = (name or "").strip()
    bare = raw[len("custom:"):] if raw.startswith("custom:") else raw
    if raw in {t.value for t in AgentType}:
        return raw
    rec = None
    try:
        rec = d.platform.agents_registry.get(bare) if bare else None
    except Exception:  # noqa: BLE001 — a broken registry refuses, never guesses
        rec = None
    if rec is None:
        raise HTTPException(
            status_code=404,
            detail=f"no agent named {raw!r} — use a custom agent's name (Agents page)",
        )
    return f"custom:{rec.name}"


def _decision(coach, proposal_id: str, action: str) -> dict[str, Any]:
    try:
        row = getattr(coach, action)(proposal_id)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"no coach proposal {proposal_id!r}"
        ) from None
    except StaleProposal as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return {"proposal": proposal_view(row)}


def register(app: FastAPI, d) -> None:
    """Attach the coach routes to *app*; ``d`` is the create_app deps object."""

    @app.get("/agents/{name}/coach")
    def agent_coach_report(name: str) -> dict[str, Any]:
        coach = _coach(d)
        roster = _known(d, name)
        report = coach.report(roster)
        pending = coach.list(agent=roster, status=PENDING)
        return {
            "report": report,
            "proposals": [proposal_view(p) for p in pending],
            "last_reason": coach.last_reason_for(name),
        }

    @app.post("/agents/{name}/coach")
    async def agent_coach_propose(name: str) -> dict[str, Any]:
        coach = _coach(d)
        roster = _known(d, name)
        record = await coach.propose(roster)
        return {
            "proposal": proposal_view(record) if record is not None else None,
            "reason": coach.last_reason_for(name),
        }

    @app.get("/coach/proposals")
    async def coach_proposals(
        agent: str | None = None, status: str | None = None
    ) -> dict[str, Any]:
        coach = _coach(d)
        if status and status not in STATUSES:
            raise HTTPException(
                status_code=422,
                detail=f"status must be one of {', '.join(STATUSES)} — not {status!r}",
            )
        rows = await asyncio.to_thread(coach.list, agent, status)
        return {"proposals": [proposal_view(p) for p in rows]}

    @app.post("/coach/proposals/{proposal_id}/accept")
    async def coach_accept(proposal_id: str) -> dict[str, Any]:
        coach = _coach(d)
        return await asyncio.to_thread(_decision, coach, proposal_id, "accept")

    @app.post("/coach/proposals/{proposal_id}/decline")
    async def coach_decline(proposal_id: str) -> dict[str, Any]:
        coach = _coach(d)
        return await asyncio.to_thread(_decision, coach, proposal_id, "decline")
