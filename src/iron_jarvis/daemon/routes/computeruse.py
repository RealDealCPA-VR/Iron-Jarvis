"""Computer-use routes: enablement, approvals, runs.

Moved verbatim from daemon/app.py's create_app; closure-local state is
reached through ``d`` (see the deps object built in create_app).
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from typing import Any

from ..schemas import ComputerUseEnable
from ...core.db import session_scope


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""
    @app.get("/computeruse/screen")
    def computeruse_screen() -> dict[str, Any]:
        """The LIVE VIEW: the most recent browser screenshot (b64 + url + time),
        refreshed after every page-changing action — lets the dashboard show
        what the agent's browser sees in near-real-time."""
        screen = getattr(d.platform.computeruse, "last_screen", None)
        return {"screen": screen, "enabled": d.platform.computeruse.policy.enabled}

    @app.get("/computeruse")
    def computeruse_status() -> dict[str, Any]:
        return d._cu_status()

    @app.post("/computeruse/enable")
    def computeruse_enable(body: ComputerUseEnable) -> dict[str, Any]:
        from ...computeruse import ComputerUsePolicy, PlaywrightBrowser

        cu = d.platform.computeruse
        cu.policy = ComputerUsePolicy.from_config(
            {
                "enabled": body.enabled,
                "domain_allowlist": body.domain_allowlist
                if body.domain_allowlist is not None
                else list(cu.policy.domain_allowlist),
                "action_allowlist": body.action_allowlist
                if body.action_allowlist is not None
                else list(cu.policy.action_allowlist),
                "isolation": getattr(cu.policy, "isolation", "isolated"),
                "max_steps": cu.policy.max_steps,
                "max_retries": cu.policy.max_retries,
            }
        )
        # Switch to a real isolated browser when enabling (needs `playwright install`).
        if body.enabled and type(cu.browser).__name__ == "FakeBrowser":
            cu.browser = PlaywrightBrowser()
        return d._cu_status()

    @app.get("/computeruse/approvals")
    def computeruse_approvals() -> dict[str, Any]:
        return {
            "approvals": [a.model_dump() for a in d.platform.computeruse.approvals.pending()]
        }

    @app.post("/computeruse/approvals/{approval_id}/approve")
    def computeruse_approve(approval_id: str) -> dict[str, Any]:
        # 404 on an unknown/stale id instead of faking success — for a
        # human-gated capability, a "approved" reply that recorded nothing is a
        # trust lie (mirrors every other id-based mutation).
        if d.platform.computeruse.approvals.approve(approval_id) is None:
            raise HTTPException(status_code=404, detail="no such approval")
        return {"id": approval_id, "status": "approved"}

    @app.post("/computeruse/approvals/{approval_id}/deny")
    def computeruse_deny(approval_id: str) -> dict[str, Any]:
        if d.platform.computeruse.approvals.deny(approval_id) is None:
            raise HTTPException(status_code=404, detail="no such approval")
        return {"id": approval_id, "status": "denied"}

    @app.get("/computeruse/runs")
    def computeruse_runs(limit: int = 20) -> dict[str, Any]:
        """Recent run HISTORY (newest first) — finished runs stay inspectable
        instead of vanishing when the live status card moves on."""
        from sqlmodel import select

        from ...computeruse.models import ComputerUseRun

        limit = max(1, min(int(limit), 200))
        with session_scope(d.platform.engine) as db:
            rows = db.exec(
                select(ComputerUseRun)
                .order_by(ComputerUseRun.created_at.desc())  # type: ignore[attr-defined]
                .limit(limit)
            ).all()
            runs = [
                {
                    "id": r.id,
                    "task": r.task,
                    "status": r.status,
                    "ok": (
                        True
                        if r.status == "completed"
                        else False
                        if r.status in ("failed", "blocked")
                        else None
                    ),
                    "steps": r.steps,
                    "started_at": r.created_at.isoformat() if r.created_at else None,
                    "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                }
                for r in rows
            ]
        return {"runs": runs}

    @app.get("/computeruse/runs/{run_id}")
    def computeruse_run(run_id: str) -> dict[str, Any]:
        from ...computeruse.models import ComputerUseRun

        with session_scope(d.platform.engine) as db:
            run = db.get(ComputerUseRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="no such run")
        return run.model_dump()
