"""Workflow + template routes.

Moved verbatim from daemon/app.py's create_app; closure-local state is
reached through ``d`` (see the deps object built in create_app).
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from sqlmodel import select
from typing import Any

from ..schemas import (
    TemplateCreateBody,
    WorkflowGenerateBody,
    WorkflowRunBody,
    WorkflowSaveBody,
)
from ...core.db import session_scope


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""
    @app.post("/workflows/run")
    async def workflow_run(body: WorkflowRunBody) -> dict[str, Any]:
        from ...workflows.engine import WorkflowEngine, load_workflow, load_workflow_toml

        if body.toml:
            wf = load_workflow_toml(body.toml)
        elif body.name and body.steps is not None:
            # Project pin: an explicit body.project_id wins ("" = force
            # unpinned); otherwise a run of a SAVED def inherits its pin.
            pid = body.project_id
            if pid is None:
                from ...workflows.store import WorkflowStore

                pid = WorkflowStore(d.platform.engine).get_project_id(body.name)
            wf = load_workflow(
                {"name": body.name, "steps": body.steps, "project_id": pid}
            )
        else:
            raise HTTPException(status_code=400, detail="provide `toml` or `name`+`steps`")
        # Create the record synchronously (validating steps), then run it in the
        # BACKGROUND: the HTTP request no longer blocks for the multi-minute run
        # (which was aborting clients into a false "couldn't reach the daemon").
        engine = WorkflowEngine(d.platform, d.orchestrator)
        try:
            rec = engine.create_record(wf)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        d._spawn_bg(rec.id, engine.run_record(rec, wf))
        return rec.model_dump()

    @app.get("/workflows/runs")
    def workflow_runs(limit: int = 50) -> dict[str, Any]:
        from ...workflows.models import WorkflowRunRecord

        limit = max(1, min(200, limit))  # clamp: newest-first, bounded
        with session_scope(d.platform.engine) as db:
            rows = list(
                db.exec(
                    select(WorkflowRunRecord)
                    .order_by(WorkflowRunRecord.started_at.desc())  # type: ignore[attr-defined]
                    .limit(limit)
                )
            )
        return {"runs": [r.model_dump() for r in rows]}

    @app.get("/workflows/runs/{run_id}")
    def workflow_run_detail(run_id: str) -> dict[str, Any]:
        from ...workflows.models import WorkflowRunRecord

        with session_scope(d.platform.engine) as db:
            rec = db.get(WorkflowRunRecord, run_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="no such run")
        return rec.model_dump()

    @app.post("/workflows/runs/{run_id}/cancel")
    def workflow_run_cancel(run_id: str) -> dict[str, Any]:
        """Ask a live run to stop. Flips status to 'cancelling' (the engine
        checks it before each step) AND best-effort cancels the in-flight step
        session. Cancelling a finished run → 409."""
        from ...workflows.models import WorkflowRunRecord

        with session_scope(d.platform.engine) as db:
            rec = db.get(WorkflowRunRecord, run_id)
            if rec is None:
                raise HTTPException(status_code=404, detail="no such run")
            if rec.status in ("completed", "failed", "cancelled", "interrupted"):
                raise HTTPException(status_code=409, detail=f"run already {rec.status}")
            rec.status = "cancelling"
            current = rec.current_session_id
            db.add(rec)
            db.commit()
            db.refresh(rec)
            status = rec.status
        if current:
            try:
                d.orchestrator.cancel_session(current)
            except Exception:  # noqa: BLE001 — best-effort; the pre-step check still stops it
                pass
        return {"id": run_id, "status": status}

    # Saved workflow definitions (agents author these; the editor loads/saves them).
    @app.get("/workflows")
    def list_workflows() -> dict[str, Any]:
        from ...workflows.store import WorkflowStore

        return {
            "workflows": [w.model_dump() for w in WorkflowStore(d.platform.engine).list()]
        }

    @app.post("/workflows")
    def save_workflow(body: WorkflowSaveBody) -> dict[str, Any]:
        from ...workflows.store import WorkflowStore

        store = WorkflowStore(d.platform.engine)
        # None PRESERVES an existing pin (dashboards that don't know about
        # pins re-save the whole def); "" explicitly unpins.
        pid = body.project_id if body.project_id is not None else store.get_project_id(body.name)
        rec = store.save(
            body.name, body.steps, description=body.description, project_id=pid
        )
        out = rec.model_dump()
        out["project_id"] = store.get_project_id(body.name)
        return out

    @app.get("/workflows/{name}")
    def get_workflow(name: str) -> dict[str, Any]:
        from ...workflows.store import WorkflowStore

        store = WorkflowStore(d.platform.engine)
        rec = store.get(name)
        if rec is None:
            raise HTTPException(status_code=404, detail="no such workflow")
        out = rec.model_dump()
        out["project_id"] = store.get_project_id(name)
        return out

    @app.delete("/workflows/{name}")
    def delete_workflow(name: str) -> dict[str, Any]:
        """Delete a saved workflow definition by name (404 when absent)."""
        from ...workflows.store import WorkflowStore

        if not WorkflowStore(d.platform.engine).remove(name):
            raise HTTPException(status_code=404, detail="no such workflow")
        return {"deleted": name}

    @app.post("/workflows/generate")
    async def generate_workflow(body: WorkflowGenerateBody) -> dict[str, Any]:
        """Build (or refine) a workflow from a natural-language description.

        An agent turns the request into a ``{name, description, steps}`` workflow
        (steps = ``{name, agent, task, tool?}``), saves it, and returns it so the
        editor can load it. Refinement: pass ``current`` (the steps in the
        editor) and the new instruction.
        """
        return await d._build_workflow(
            body.description, body.provider, body.model, body.name, body.current
        )

    @app.get("/templates/suggestions")
    def template_suggestions() -> dict[str, Any]:
        """Watch-me-work: task patterns repeated ≥3× in session history that
        aren't templates yet — suggest-only; the user clicks save."""
        from ...templates import TemplateStore

        return {"suggestions": TemplateStore(d.platform.engine).suggest_from_history()}

    # Saved prompts / task templates (one-click re-run of a frequent task).
    @app.get("/templates")
    def list_templates() -> dict[str, Any]:
        from ...templates import TemplateStore

        return {
            "templates": [t.model_dump() for t in TemplateStore(d.platform.engine).list()]
        }

    @app.post("/templates")
    def create_template(body: TemplateCreateBody) -> dict[str, Any]:
        from ...templates import TemplateStore

        if not (body.task or "").strip():
            raise HTTPException(status_code=400, detail="task is required")
        rec = TemplateStore(d.platform.engine).create(
            body.name,
            body.task,
            body.agent_type,
            body.provider,
            body.model,
            description=body.description,
        )
        return rec.model_dump()

    @app.delete("/templates/{prompt_id}")
    def delete_template(prompt_id: str) -> dict[str, Any]:
        from ...templates import TemplateStore

        return {"removed": TemplateStore(d.platform.engine).remove(prompt_id)}
