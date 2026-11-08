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


#: The fields an edit may change (v1.449.0, Wave D item 34) — the store's own
#: list minus ``project_id``, which the route reads with its three intents.
_EDIT_FIELDS = ("name", "source", "match", "action", "target", "task_template")


def _check_rule(source: str, match: str, action: str, target: str) -> None:
    """The add-time rules, shared by POST and PATCH (v1.449.0) so an edit can
    never land a rule the add would have refused. Fail NOW, not at fire time."""
    if source not in REFLEX_SOURCES:
        raise HTTPException(status_code=400, detail=f"source must be one of {REFLEX_SOURCES}")
    if action not in REFLEX_ACTIONS:
        raise HTTPException(status_code=400, detail=f"action must be one of {REFLEX_ACTIONS}")
    # Only a WEBHOOK rule requires a `match` (its exact slug). The text-carrying
    # sources (comm/email/calendar/slack) accept an EMPTY match — that means
    # "fire on every signal of this source" (ReflexStore.matching treats an
    # empty keyword as a catch-all), so CX-05 email/calendar/slack rules with a
    # blank match are valid and land here unchanged.
    if source == "webhook" and not match.strip():
        raise HTTPException(status_code=400, detail="a webhook rule needs a webhook slug in `match`")
    if action in ("workflow", "remote_agent") and not target.strip():
        raise HTTPException(
            status_code=400,
            detail=f"a '{action}' action needs a `target` (the workflow/agent name)",
        )


def register(app: FastAPI, d) -> None:
    @app.get("/reflex/rules")
    def list_reflex_rules(source: str | None = None) -> dict[str, Any]:
        rules = d.platform.reflex.list(source)
        return {"rules": [r.model_dump() for r in rules]}

    @app.post("/reflex/rules")
    def add_reflex_rule(body: ReflexRuleBody) -> dict[str, Any]:
        _check_rule(body.source, body.match, body.action, body.target)
        rule = d.platform.reflex.add(
            name=body.name,
            source=body.source,
            match=body.match,
            action=body.action,
            target=body.target,
            task_template=body.task_template,
            enabled=body.enabled,
            # Context spine (v1.200.0): the project this rule's work runs in.
            project_id=body.project_id,
        )
        return rule.model_dump()

    @app.delete("/reflex/rules/{rule_id}")
    def delete_reflex_rule(rule_id: str) -> dict[str, Any]:
        if not d.platform.reflex.remove(rule_id):
            raise HTTPException(status_code=404, detail="no such reflex rule")
        return {"removed": rule_id}

    @app.patch("/reflex/rules/{rule_id}")
    def toggle_reflex_rule(rule_id: str, body: ReflexToggleBody) -> dict[str, Any]:
        """PARTIAL edit (v1.449.0, Wave D item 34; a toggle since v1.200.0):
        every field given replaces the stored one, every field omitted is
        kept, and the MERGED rule passes exactly the add-time rules BEFORE
        anything is written — a bad edit changes nothing, not even the
        switch. A remote-agent action drops the grounding (a remote has no
        grounding seam), as the page's add does."""
        rule = d.platform.reflex.get(rule_id)
        if rule is None:
            raise HTTPException(status_code=404, detail="no such reflex rule")
        edits = {k: getattr(body, k) for k in _EDIT_FIELDS if getattr(body, k) is not None}
        if edits:
            merged = {k: getattr(rule, k) for k in _EDIT_FIELDS}
            merged.update(edits)
            _check_rule(
                str(merged["source"]), str(merged["match"]), str(merged["action"]), str(merged["target"]),
            )
            if merged["action"] == "remote_agent" and body.project_id is None and rule.project_id:
                edits["project_id"] = ""
            rule = d.platform.reflex.update(rule_id, **edits) or rule
        if body.enabled is not None:
            rule = d.platform.reflex.set_enabled(rule_id, body.enabled) or rule
        # Three intents (see ReflexToggleBody): omitted/None = unchanged,
        # "" = clear the grounding, non-empty = re-ground in that project.
        if body.project_id is not None:
            rule = d.platform.reflex.set_project(rule_id, body.project_id) or rule
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
        # v1.446.0 (review item 20): the person pressing Test reads the
        # answer, so a failure is not ALSO announced (bell, phone, tray).
        return await app.state.reflex_router.execute(rule, context, attended=True)
