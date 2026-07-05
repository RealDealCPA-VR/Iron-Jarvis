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
            wf = load_workflow({"name": body.name, "steps": body.steps})
        else:
            raise HTTPException(status_code=400, detail="provide `toml` or `name`+`steps`")
        rec = await WorkflowEngine(d.platform).run(wf)
        return rec.model_dump()

    @app.get("/workflows/runs")
    def workflow_runs() -> dict[str, Any]:
        from ...workflows.models import WorkflowRunRecord

        with session_scope(d.platform.engine) as db:
            rows = list(db.exec(select(WorkflowRunRecord)))
        return {"runs": [r.model_dump() for r in rows]}

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

        rec = WorkflowStore(d.platform.engine).save(
            body.name, body.steps, description=body.description
        )
        return rec.model_dump()

    @app.get("/workflows/{name}")
    def get_workflow(name: str) -> dict[str, Any]:
        from ...workflows.store import WorkflowStore

        rec = WorkflowStore(d.platform.engine).get(name)
        if rec is None:
            raise HTTPException(status_code=404, detail="no such workflow")
        return rec.model_dump()

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
