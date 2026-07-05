"""Autonomy routes: goals, proposals, autonomy status, sentinels.

Moved verbatim from daemon/app.py's create_app; closure-local state is
reached through ``d`` (see the deps object built in create_app).
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from typing import Any

from ..schemas import GoalBody, GoalPatch, KillBody, SentinelAdd


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""
    @app.get("/goals")
    def list_goals(status: str | None = None) -> dict[str, Any]:
        return {"goals": [d._goal_view(g) for g in d.platform.intent.list_goals(status)]}

    @app.post("/goals")
    def create_goal(body: GoalBody) -> dict[str, Any]:
        try:
            rec = d.platform.intent.add_goal(
                body.text,
                source=body.source,
                category=body.category,
                priority=body.priority,
                autonomy_level=body.autonomy_level,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return d._goal_view(rec)

    @app.patch("/goals/{goal_id}")
    def patch_goal(goal_id: str, body: GoalPatch) -> dict[str, Any]:
        rec = d.platform.intent.update_goal(
            goal_id, **{k: v for k, v in body.model_dump().items() if v is not None}
        )
        if rec is None:
            raise HTTPException(status_code=404, detail="goal not found")
        return d._goal_view(rec)

    @app.get("/proposals")
    def list_proposals(status: str | None = None) -> dict[str, Any]:
        return {
            "proposals": [d._proposal_view(p) for p in d.platform.intent.list_proposals(status)]
        }

    @app.post("/proposals/{proposal_id}/approve")
    async def approve_proposal(proposal_id: str) -> dict[str, Any]:
        try:
            session = await d.platform.intent.approve(proposal_id, wait=False)
        except KeyError:
            raise HTTPException(status_code=404, detail="proposal not found")
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"status": "executed", "session_id": session.id if session else None}

    @app.post("/proposals/{proposal_id}/reject")
    def reject_proposal(proposal_id: str) -> dict[str, Any]:
        rec = d.platform.intent.reject(proposal_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="proposal not found")
        return {"status": rec.status}

    @app.get("/autonomy")
    def autonomy_status() -> dict[str, Any]:
        cfg = d.platform.config
        used_actions, used_tokens = d.platform.intent._global_window_usage()
        return {
            "enabled": getattr(cfg, "autonomy_enabled", False),
            "level": getattr(cfg, "autonomy_level", "suggest"),
            "dry_run": getattr(cfg, "autonomy_dry_run", False),
            "kill_switch": getattr(cfg, "autonomy_kill_switch", False),
            "tick_seconds": getattr(cfg, "autonomy_tick_seconds", 900),
            "max_actions_per_day": getattr(cfg, "autonomy_max_actions_per_day", 5),
            "max_tokens_per_day": getattr(cfg, "autonomy_max_tokens_per_day", 50000),
            "used_actions_24h": used_actions,
            "used_tokens_24h": used_tokens,
            "active_goals": len(d.platform.intent.list_goals(status="active")),
            "pending_proposals": len(d.platform.intent.list_proposals(status="pending")),
        }

    @app.post("/autonomy/kill")
    def autonomy_kill(body: KillBody) -> dict[str, Any]:
        """Global kill switch: engage (default) or release. Persisted to config."""
        d.platform.config.autonomy_kill_switch = bool(body.enabled)
        d._persist_config(["autonomy_kill_switch"])
        return {"kill_switch": d.platform.config.autonomy_kill_switch}

    @app.post("/autonomy/tick")
    async def autonomy_tick(wait: bool = False) -> dict[str, Any]:
        """Run a single deliberation pulse now (no-ops when autonomy is disabled)."""
        return await d.platform.intent.deliberate(wait=wait)

    @app.get("/autonomy/briefing")
    def autonomy_briefing() -> dict[str, Any]:
        """Read-only briefing summary. Pushing it (a side effect) is POST-only so
        the Origin/CSRF guard (which only gates non-GET) actually protects it."""
        return d.platform.intent.briefing(notify=False)

    @app.post("/autonomy/briefing")
    def autonomy_briefing_push() -> dict[str, Any]:
        """Summarise + PUSH the briefing to the configured comm channel(s)."""
        return d.platform.intent.briefing(notify=True)

    @app.get("/sentinels")
    def list_sentinels() -> dict[str, Any]:
        return {
            "enabled": getattr(d.platform.config, "sentinels_enabled", False),
            "sentinels": [d._sentinel_view(s) for s in d.platform.sentinels.list()],
        }

    @app.post("/sentinels")
    def create_sentinel(body: SentinelAdd) -> dict[str, Any]:
        try:
            rec = d.platform.sentinels.add(
                body.name,
                path=body.path,
                glob=body.glob,
                task=body.task,
                kind=body.kind,
                agent_type=body.agent_type,
                risk=body.risk,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return d._sentinel_view(rec)

    @app.delete("/sentinels/{name}")
    def delete_sentinel(name: str) -> dict[str, Any]:
        if not d.platform.sentinels.remove(name):
            raise HTTPException(status_code=404, detail="sentinel not found")
        return {"deleted": name}

    @app.post("/sentinels/poll")
    def poll_sentinels() -> dict[str, Any]:
        """Run one polling sweep now (suggest-only; no-ops when sentinels disabled).

        Mints SUGGEST-ONLY proposals for any noticed changes — never a session.
        Guarded by config.sentinels_enabled so a manual poke can't bypass opt-in.
        """
        if not getattr(d.platform.config, "sentinels_enabled", False):
            return {"ran": False, "reason": "sentinels_disabled", "proposals": []}
        created = d.platform.sentinels.poll_once(d.platform.intent)
        return {"ran": True, "proposals": [p.id for p in created]}
