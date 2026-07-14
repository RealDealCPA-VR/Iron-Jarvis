"""Reflex Loop routes — manage the signal→action rules (the ambient operator).

CRUD over :class:`~iron_jarvis.reflex.models.ReflexRule` plus a manual ``/test``
that fires a rule right now. The durable store lives on ``d.platform.reflex``;
the executing router is stashed on ``app.state.reflex_router`` by ``create_app``.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from ..schemas import ReflexRuleBody, ReflexToggleBody
from ...reflex.models import REFLEX_ACTIONS, REFLEX_SOURCES


def register(app: FastAPI, d) -> None:
    @app.get("/reflex/rules")
    def list_reflex_rules(source: str | None = None) -> dict[str, Any]:
        rules = d.platform.reflex.list(source)
        return {"rules": [r.model_dump() for r in rules]}

    @app.post("/reflex/rules")
    def add_reflex_rule(body: ReflexRuleBody) -> dict[str, Any]:
        if body.source not in REFLEX_SOURCES:
            raise HTTPException(status_code=400, detail=f"source must be one of {REFLEX_SOURCES}")
        if body.action not in REFLEX_ACTIONS:
            raise HTTPException(status_code=400, detail=f"action must be one of {REFLEX_ACTIONS}")
        # Only a WEBHOOK rule requires a `match` (its exact slug). The text-carrying
        # sources (comm/email/calendar/slack) accept an EMPTY match — that means
        # "fire on every signal of this source" (ReflexStore.matching treats an
        # empty keyword as a catch-all), so CX-05 email/calendar/slack rules with a
        # blank match are valid and land here unchanged.
        if body.source == "webhook" and not body.match.strip():
            raise HTTPException(status_code=400, detail="a webhook rule needs a webhook slug in `match`")
        if body.action in ("workflow", "remote_agent") and not body.target.strip():
            raise HTTPException(
                status_code=400,
                detail=f"a '{body.action}' action needs a `target` (the workflow/agent name)",
            )
        rule = d.platform.reflex.add(
            name=body.name,
            source=body.source,
            match=body.match,
            action=body.action,
            target=body.target,
            task_template=body.task_template,
            enabled=body.enabled,
        )
        return rule.model_dump()

    @app.delete("/reflex/rules/{rule_id}")
    def delete_reflex_rule(rule_id: str) -> dict[str, Any]:
        if not d.platform.reflex.remove(rule_id):
            raise HTTPException(status_code=404, detail="no such reflex rule")
        return {"removed": rule_id}

    @app.patch("/reflex/rules/{rule_id}")
    def toggle_reflex_rule(rule_id: str, body: ReflexToggleBody) -> dict[str, Any]:
        rule = d.platform.reflex.set_enabled(rule_id, body.enabled)
        if rule is None:
            raise HTTPException(status_code=404, detail="no such reflex rule")
        return rule.model_dump()

    @app.post("/reflex/rules/{rule_id}/test")
    async def test_reflex_rule(rule_id: str) -> dict[str, Any]:
        """Fire the rule NOW with a synthetic signal — proves the binding works
        (the workflow/agent/session actually starts) without waiting for a real
        webhook or message."""
        rule = d.platform.reflex.get(rule_id)
        if rule is None:
            raise HTTPException(status_code=404, detail="no such reflex rule")
        context = {"text": "(manual test)", "body": "(manual test)", "slug": rule.match}
        return await app.state.reflex_router.execute(rule, context)
