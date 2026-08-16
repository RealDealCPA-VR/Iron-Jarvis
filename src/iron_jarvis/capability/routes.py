"""HTTP for the capability-request queue (v1.178.0, P4).

``GET  /capability/proposals``              — everything the review card reads.
``POST /capability/proposals/{id}/approve`` — create it (the ONLY creation path).
``POST /capability/proposals/{id}/reject``  — turn it down; the ask is suppressed.

Deliberately the same shapes as ``routes/memory_review.py``: proposals come back
FLAT carrying ``status``, an unknown id is 404, an already-decided one is 409 (a
double-click must not read as "it vanished"), and a request approval cannot
satisfy is an honest 409 with the row left pending rather than a silent
"approved" that created nothing.

A SEPARATE MODULE from ``__init__`` on purpose: ``core.db._LATE_MODEL_MODULES``
imports ``capability.models`` at every boot, which runs this package's
``__init__`` — and that must not drag FastAPI in behind a table registration.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from .store import CapabilityProposalStore, proposal_view


def _decision_error(exc: ValueError) -> HTTPException:
    """Store ``ValueError`` -> HTTP: unknown proposal 404, already-decided 409."""
    detail = str(exc)
    status = 404 if "no such proposal" in detail else 409
    return HTTPException(status_code=status, detail=detail)


def register(app, d) -> None:
    """Attach the three routes to *app*; ``d`` is the create_app deps object.

    Every handler is a SYNC ``def`` on purpose. FastAPI runs those in a worker
    thread, which is what keeps the SQLite reads — and, for approve, a whole
    ``tool_create`` — off the daemon's single event loop (v1.153.1).
    ``CapabilityProposalStore.approve`` refuses loudly if it ever finds itself
    on the loop, but not being there is the actual fix.
    """

    def _store() -> CapabilityProposalStore:
        """The shared store, or a fresh one bound to this platform.

        Stateless apart from the engine + platform, so the fallback costs
        nothing and keeps this lane green on a daemon whose platform was built
        without the capability wiring.
        """
        cached = getattr(d, "capability_proposals", None)
        if cached is not None:
            return cached
        platform = getattr(d, "platform", None)
        engine = getattr(platform, "engine", None)
        if engine is None:  # pragma: no cover — every real platform has one
            raise HTTPException(
                status_code=503, detail="capability requests are unavailable"
            )
        store = getattr(platform, "capabilities", None) or CapabilityProposalStore(
            engine, platform=platform
        )
        d.capability_proposals = store
        return store

    @app.get("/capability/proposals")
    def list_capability_proposals(status: str | None = None) -> dict[str, Any]:
        """Pending requests first, with what approving each one would actually do.

        ``can_apply`` / ``kind_note`` / ``blocked`` come from the LIVE platform,
        so the card can say "Iron Jarvis can't add an MCP server for you" BEFORE
        the user clicks rather than after.
        """
        store = _store()
        proposals = [proposal_view(row, store) for row in store.list(status)]
        return {
            "proposals": proposals,
            "pending": sum(1 for p in proposals if p["status"] == "pending"),
            "stats": store.stats(),
        }

    @app.post("/capability/proposals/{proposal_id}/approve")
    def approve_capability_proposal(proposal_id: str) -> dict[str, Any]:
        """Create the capability — the only thing in this feature that creates.

        A request approval cannot satisfy (an MCP server, a connection, anything
        naming a deny-floor tool) answers 409 with the reason and leaves the row
        pending.
        """
        store = _store()
        try:
            record, result = store.approve(proposal_id)
        except ValueError as exc:
            raise _decision_error(exc)
        if not result.ok:
            raise HTTPException(
                status_code=409, detail=result.error or "could not create it"
            )
        return {**proposal_view(record, store), "applied": result.to_dict()}

    @app.post("/capability/proposals/{proposal_id}/reject")
    def reject_capability_proposal(proposal_id: str) -> dict[str, Any]:
        """Turn a request down. It leaves the queue and the ask is suppressed,
        so a model that re-derives the same gap every run cannot re-file it."""
        store = _store()
        try:
            record = store.reject(proposal_id)
        except ValueError as exc:
            raise _decision_error(exc)
        return proposal_view(record, store)
