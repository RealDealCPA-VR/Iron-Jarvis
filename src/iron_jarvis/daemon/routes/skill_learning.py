"""Skill-learning routes (v1.135.0): the suggest-only skill loop's review UI.

Finished sessions mint candidates (orchestrator step 4); a distill sweep turns
them into reviewable draft skills (real provider only — never mock); nothing
lands in the skills directory until approve. These routes serve the Skills
page's "Suggested skills" section: the overview, the proposal lifecycle
(approve / reject), a manual "Distill now", and the two persisted settings.

REGISTRATION ORDER MATTERS: this module registers BEFORE routes/agents.py in
create_app, because agents.py's ``GET /skills/{name}`` catch-all would
otherwise swallow the literal ``/skills/learning`` path (pinned by a test).

Closure-local state is reached through ``d`` (the create_app deps object).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, HTTPException

from ..schemas import SkillLearningSettingsPatch, SkillProposalApproveBody


def _proposal_view(p) -> dict[str, Any]:
    """One proposal, FLAT (the POST /sessions convention) — always carries
    ``status`` so the UI can filter pending itself."""
    try:
        source_ids = json.loads(p.source_session_ids or "[]")
    except (TypeError, ValueError):
        source_ids = []
    return {
        "id": p.id,
        "kind": p.kind,
        "skill_name": p.skill_name,
        "description": p.description,
        "body_md": p.body_md,
        "prev_body_md": p.prev_body_md,
        "source_session_ids": source_ids,
        "status": p.status,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "decided_at": p.decided_at.isoformat() if p.decided_at else None,
    }


def _decision_error(exc: ValueError) -> HTTPException:
    """Map the engine's ValueError: unknown proposal → 404, already-decided →
    409 (a double-click must not read as "it vanished")."""
    detail = str(exc)
    status = 404 if "no such proposal" in detail else 409
    return HTTPException(status_code=status, detail=detail)


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""

    def _engine():
        engine = getattr(d.platform, "skill_learning", None)
        if engine is None:  # pragma: no cover — always built by build_platform
            raise HTTPException(status_code=503, detail="skill learning unavailable")
        return engine

    def _settings_view() -> dict[str, bool]:
        cfg = d.platform.config
        return {
            "enabled": bool(getattr(cfg, "skill_learning_enabled", True)),
            "auto_approve": bool(getattr(cfg, "skill_learning_auto_approve", False)),
        }

    @app.get("/skills/learning")
    def skill_learning_overview() -> dict[str, Any]:
        """Everything the Skills page's learning section reads in one call.
        The UI binds to enabled/auto_approve/proposals/stats/pending_candidates
        exactly (proposals include ``status``; it filters pending itself)."""
        engine = _engine()
        stats = engine.stats()
        return {
            **_settings_view(),
            "proposals": [_proposal_view(p) for p in engine.proposals()],
            "stats": stats.get("skills") or [],
            "pending_proposals": int(stats.get("pending_proposals") or 0),
            "pending_candidates": int(stats.get("pending_candidates") or 0),
        }

    @app.get("/skills/proposals")
    def skill_proposals() -> dict[str, Any]:
        """All proposals, pending first (the GET /sessions {…: [...]} shape)."""
        return {"proposals": [_proposal_view(p) for p in _engine().proposals()]}

    @app.post("/skills/proposals/{proposal_id}/approve")
    def approve_skill_proposal(
        proposal_id: str, body: SkillProposalApproveBody
    ) -> dict[str, Any]:
        """Write the proposed skill to disk (an edited ``body_md`` wins) and
        return the decided proposal FLAT. Create never clobbers an existing
        slug; refine overwrites in place — that IS the update path."""
        try:
            record = _engine().approve(proposal_id, body_md=body.body_md)
        except ValueError as exc:
            raise _decision_error(exc)
        return _proposal_view(record)

    @app.post("/skills/proposals/{proposal_id}/reject")
    def reject_skill_proposal(proposal_id: str) -> dict[str, Any]:
        """Dismiss a proposal FLAT; its signature suppresses re-proposing the
        same procedure ("not this" should stick)."""
        try:
            record = _engine().reject(proposal_id)
        except ValueError as exc:
            raise _decision_error(exc)
        return _proposal_view(record)

    @app.post("/skills/learning/distill")
    async def distill_skills_now() -> dict[str, Any]:
        """Run a distill sweep NOW (the Skills page's "Distill now"). Rides the
        same real-provider-only ``complete`` as the automatic sweep — with only
        the offline mock available this is an honest 400, never a fabricated
        skill (crystallize's rule). The UI reads ``distilled`` exactly."""
        engine = _engine()
        complete = d._skill_distill_complete()
        if complete is None:
            raise HTTPException(
                status_code=400,
                detail="connect a model on the Connections page to distill "
                "skill drafts — finished sessions keep queueing offline",
            )
        result = await engine.distill_candidates(complete)
        out = {
            "distilled": len(result.get("proposals") or []),
            "reviewed": int(result.get("reviewed") or 0),
            "proposals": list(result.get("proposals") or []),
            "dismissed": int(result.get("dismissed") or 0),
        }
        if result.get("note"):
            out["note"] = result["note"]
        return out

    @app.patch("/skills/learning/settings")
    def patch_skill_learning_settings(
        body: SkillLearningSettingsPatch,
    ) -> dict[str, Any]:
        """The Skills page's two learning toggles — real persisted settings
        (the v1.127.0 MCP-auto-approve pattern): ``None`` reads without
        changing, writes persist to config.toml, and the response returns the
        effective state so the checkboxes bind to truth."""
        cfg = d.platform.config
        changed: list[str] = []
        if body.enabled is not None:
            cfg.skill_learning_enabled = bool(body.enabled)
            changed.append("skill_learning_enabled")
        if body.auto_approve is not None:
            cfg.skill_learning_auto_approve = bool(body.auto_approve)
            changed.append("skill_learning_auto_approve")
        if changed:
            d._persist_config(changed)
        return _settings_view()
