"""Reflex Loop routes — manage the signal→action rules (the ambient operator).

CRUD over :class:`~iron_jarvis.reflex.models.ReflexRule` plus a manual ``/test``
that fires a rule right now. The durable store lives on ``d.platform.reflex``;
the executing router is stashed on ``app.state.reflex_router`` by ``create_app``.

Leap 05-a: a rule may start when a new file lands in a folder (``source``
``file``: ``path``, ``glob``; the rule's own watcher is made, changed and
removed with it) or when a download finishes (``download``: ``match_site``).
Leap 05-b: or when another task ends short (``run_ended``: ``what``,
``what_name``, ``outcome``).
Each row carries its card's four lines (``when_words``, ``does_words``,
``where_words``, ``limits_words``, composed by ``triggers/starts.describe``),
where it reports (``reports_to``), its folder watcher, and its pause
(``paused_at`` / ``paused_reason``): ``POST /reflex/rules/{id}/resume`` and
``POST /reflex/rules/{id}/keep-paused`` are the two answers. Every handler is
a plain ``def`` (FastAPI runs it off the loop); the test press is async.
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


def _start_args(body: Any, rule: Any = None) -> dict[str, Any]:
    """The leap 05-a fields of a body, merged over a stored rule's own."""
    current = rule.decoded_start() if rule is not None else {}
    out: dict[str, Any] = {
        "path": body.path if body.path is not None else current.get("path", ""),
        "glob": body.glob if body.glob is not None else current.get("glob", ""),
        "match_site": body.match_site if body.match_site is not None else current.get("match_site", ""),
    }
    if current.get("per_file") is False:
        out["per_file"] = False
    if body.workspace_root is not None:
        out["folder"] = body.workspace_root
    # Leap 05-b: a "when another task fails" rule's task and outcome.
    for key, stored in (("what", "what"), ("what_name", "name"), ("outcome", "outcome")):
        value = getattr(body, key, None)
        out[key] = value if value is not None else current.get(stored, "")
    # Review: and whether it runs that task once more ("try once more").
    again = getattr(body, "again", None)
    out["again"] = again if again is not None else current.get("again") is True
    return out


def register(app: FastAPI, d) -> None:
    from ...settings import records as _records
    from ...triggers import starts as _starts

    def _view(rule: Any, titles: dict[str, str], watchers: dict[str, Any],
              held: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
        from ...agents import chat_delivery as _cd
        from ...reflex import limits as _limits

        row = rule.model_dump()
        row.update(_starts.describe_for(d.platform, rule))
        row["reports_to"] = _cd.reports_to_view(rule.chat_thread_id, titles)
        start = rule.decoded_start()
        row["start"] = {
            k: start[k]
            for k in ("path", "glob", "match_site", "per_file", "what", "name", "outcome", "again")
            if k in start
        }
        w = watchers.get(rule.id)
        row["watcher"] = {"name": w.name, "last_error": w.last_error} if w is not None else None
        # Leap 05-c: its kept Limits (the folders), the last run that stopped
        # at them ("Needs you"), and every OK kept on it, each with Remove.
        kept = _limits.decoded(rule)
        row["limits"] = {"folders": kept["folders"], "kept_at": kept.get("kept_at") or ""} if kept else None
        row["limits_stop"] = _limits.stop_of(rule)
        row["standing_grants"] = (held or {}).get(rule.id, [])
        for key in ("recent_starts_json", "start_json", "limits_json", "limits_stop_json"):
            row.pop(key, None)
        return row

    def _held_oks() -> dict[str, list[dict[str, Any]]]:
        store = getattr(d.platform, "grants", None)
        held: dict[str, list[dict[str, Any]]] = {}
        if store is None:
            return held
        try:
            for g in store.list("rule"):
                held.setdefault(g.scope_id, []).append(store.as_dict(g))
        except Exception:  # noqa: BLE001 — a store fault lists none, never breaks the page
            return {}
        return held

    def _views(rules: list[Any]) -> list[dict[str, Any]]:
        from ...agents import chat_delivery as _cd

        held = _held_oks()
        titles = _cd.chat_titles(d.platform.engine, [r.chat_thread_id for r in rules if r.chat_thread_id])
        sentinels = getattr(d.platform, "sentinels", None)
        watchers: dict[str, Any] = {}
        if sentinels is not None and any(r.source == "file" for r in rules):
            for w in sentinels.list():
                if getattr(w, "mode", "") == "rule" and w.rule_id:
                    watchers[w.rule_id] = w
        return [_view(r, titles, watchers, held) for r in rules]

    def _one(rule_id: str) -> dict[str, Any]:
        rule = d.platform.reflex.get(rule_id)
        if rule is None:
            raise HTTPException(status_code=404, detail="no such reflex rule")
        return _views([rule])[0]

    @app.get("/reflex/rules")
    def list_reflex_rules(source: str | None = None) -> dict[str, Any]:
        rules = d.platform.reflex.list(source)
        return {"rules": _views(rules)}

    @app.post("/reflex/rules")
    def add_reflex_rule(body: ReflexRuleBody) -> dict[str, Any]:
        _check_rule(body.source, body.match, body.action, body.target)
        start, root, problem = _records.rule_start_from_args(d.platform, body.source, _start_args(body))
        if problem:
            raise HTTPException(status_code=400, detail=problem)
        agent = (body.agent_name or "").strip()
        if agent:
            problem = _records.rule_agent_problem(d.platform, agent)
            if problem:
                raise HTTPException(status_code=400, detail=problem)
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
            start=start or None,
            agent_name=agent,
            workspace_root=root,
            max_fires_per_hour=body.max_fires_per_hour,
        )
        if rule.source == "file":
            problem = _records.sync_rule_watcher(d.platform, rule.id)
            if problem:
                d.platform.reflex.remove(rule.id)
                raise HTTPException(status_code=400, detail=problem)
            _records.rearm_folder_watching(d.platform)
        return _one(rule.id)

    @app.delete("/reflex/rules/{rule_id}")
    def delete_reflex_rule(rule_id: str) -> dict[str, Any]:
        # v1.504.0 (north-star plan 4.8): recorded with its Undo, the way
        # chat's reflex_delete is (settings/page_records).
        from ...settings import page_records as _pr

        prior = _pr.snapshot(d.platform, "reflex", rule_id)
        if not d.platform.reflex.remove(rule_id):
            raise HTTPException(status_code=404, detail="no such reflex rule")
        # Leap 05-a: its watcher went with it (the store's hook).
        if prior is not None and prior.get("source") == "file":
            _records.rearm_folder_watching(d.platform)
        action_id = ""
        if prior is not None:
            action_id = _pr.journal_removal(
                d.platform, tool="reflex_delete", record="reflex", name=rule_id, prior=prior,
                summary=f"Removed the rule '{prior.get('name') or rule_id}'.",
            )
        return {"removed": rule_id, "action_id": action_id}

    @app.patch("/reflex/rules/{rule_id}")
    def toggle_reflex_rule(rule_id: str, body: ReflexToggleBody) -> dict[str, Any]:
        """PARTIAL edit (v1.449.0, Wave D item 34; a toggle since v1.200.0):
        every field given replaces the stored one, every field omitted is
        kept, and the MERGED rule passes exactly the add-time rules BEFORE
        anything is written — a bad edit changes nothing, not even the
        switch. A remote-agent action drops the grounding (a remote has no
        grounding seam), as the page's add does. Leap 05-a: a file rule's
        folder and a download rule's site are merged and checked the same
        way; turning a rule ON (from off) starts it fresh, and an edit never
        resumes a rule that paused itself (only Resume does)."""
        rule = d.platform.reflex.get(rule_id)
        if rule is None:
            raise HTTPException(status_code=404, detail="no such reflex rule")
        was_on = bool(rule.enabled)
        edits: dict[str, Any] = {k: getattr(body, k) for k in _EDIT_FIELDS if getattr(body, k) is not None}
        merged = {k: getattr(rule, k) for k in _EDIT_FIELDS}
        merged.update(edits)
        if edits:
            _check_rule(
                str(merged["source"]), str(merged["match"]), str(merged["action"]), str(merged["target"]),
            )
        touches_start = "source" in edits or any(
            getattr(body, k) is not None
            for k in ("path", "glob", "match_site", "workspace_root", "what", "what_name", "outcome", "again")
        )
        if touches_start:
            start, root, problem = _records.rule_start_from_args(
                d.platform, str(merged["source"]), _start_args(body, rule)
            )
            if problem:
                raise HTTPException(status_code=400, detail=problem)
            edits["start_json"] = start
            if body.workspace_root is not None or (str(merged["source"]) == "file" and not rule.workspace_root):
                edits["workspace_root"] = root
        if body.agent_name is not None:
            agent = body.agent_name.strip()
            if agent:
                problem = _records.rule_agent_problem(d.platform, agent)
                if problem:
                    raise HTTPException(status_code=400, detail=problem)
            edits["agent_name"] = agent
        if body.max_fires_per_hour is not None:
            edits["max_fires_per_hour"] = body.max_fires_per_hour
        if edits:
            if merged["action"] == "remote_agent" and body.project_id is None and rule.project_id:
                edits["project_id"] = ""
            rule = d.platform.reflex.update(rule_id, **edits) or rule
        if "start_json" in edits:
            problem = _records.sync_rule_watcher(d.platform, rule_id)
            if problem:
                raise HTTPException(status_code=400, detail=problem)
        if body.enabled is True and not was_on:
            # Turning a rule ON (it was off: switched off, or Keep it paused)
            # starts it fresh. Review: an edit of a rule that is already on
            # carries enabled too (the page's form sends every field), and it
            # never resumes a pause by itself: only Resume does.
            rule = d.platform.reflex.resume(rule_id) or rule
        elif body.enabled is False:
            rule = d.platform.reflex.set_enabled(rule_id, False) or rule
        # Three intents (see ReflexToggleBody): omitted/None = unchanged,
        # "" = clear the grounding, non-empty = re-ground in that project.
        if body.project_id is not None:
            rule = d.platform.reflex.set_project(rule_id, body.project_id) or rule
        if rule.source == "file" or "start_json" in edits:
            _records.rearm_folder_watching(d.platform)
        return _one(rule_id)

    @app.post("/reflex/rules/{rule_id}/resume")
    def resume_reflex_rule(rule_id: str) -> dict[str, Any]:
        """Leap 05-a: Resume a rule that paused itself (its hourly limit):
        it starts again on the next signal; a file rule's waiting files go
        on the next scan."""
        if d.platform.reflex.resume(rule_id) is None:
            raise HTTPException(status_code=404, detail="no such reflex rule")
        return _one(rule_id)

    @app.post("/reflex/rules/{rule_id}/keep-paused")
    def keep_reflex_rule_paused(rule_id: str) -> dict[str, Any]:
        """Leap 05-a: Keep it paused — the pause notice's stop answer: the
        rule is switched off (it starts nothing and says nothing) until it is
        turned on again."""
        if d.platform.reflex.keep_paused(rule_id) is None:
            raise HTTPException(status_code=404, detail="no such reflex rule")
        return _one(rule_id)

    @app.post("/reflex/rules/{rule_id}/test")
    async def test_reflex_rule(rule_id: str) -> dict[str, Any]:
        """Fire the rule NOW with a synthetic signal — proves the binding works
        (the workflow/agent/session actually starts) without waiting for a real
        webhook or message."""
        import asyncio

        rule = await asyncio.to_thread(d.platform.reflex.get, rule_id)
        if rule is None:
            raise HTTPException(status_code=404, detail="no such reflex rule")
        context = {"text": "(manual test)", "body": "(manual test)", "slug": rule.match}
        # v1.446.0 (review item 20): the person pressing Test reads the
        # answer, so a failure is not ALSO announced (bell, phone, tray).
        return await app.state.reflex_router.execute(rule, context, attended=True)
