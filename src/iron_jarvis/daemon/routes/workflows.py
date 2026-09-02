"""Workflow + template routes.

Moved verbatim from daemon/app.py's create_app; closure-local state is
reached through ``d`` (see the deps object built in create_app).
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException
from sqlmodel import select
from typing import Any

from ..schemas import (
    TemplateCreateBody,
    TemplateUpdateBody,
    WorkflowGenerateBody,
    WorkflowAnswerBody,
    WorkflowPatchBody,
    WorkflowRunBody,
    WorkflowSaveBody,
)
from ...core.db import session_scope

log = logging.getLogger("iron_jarvis.workflows")


def _workflow_references(d, name: str) -> list[dict[str, Any]]:
    """Every automation that will FIRE the saved workflow ``name`` by name —
    and so fails with "no saved workflow" the moment it is deleted.

    Two stores resolve a workflow by name at fire time: a ``kind="workflow"``
    schedule whose payload names it (``workflow``/``name``) without carrying
    inline ``steps`` (platform ``_run_scheduled_workflow``), and a reflex rule
    with ``action="workflow"`` (``reflex/router.py``). Goals and sessions
    never do. Best-effort per store: a store that cannot answer is logged and
    skipped, and the UI says the check failed rather than "nothing uses it".
    """
    refs: list[dict[str, Any]] = []
    try:
        for row in d.platform.scheduler.list():
            if row.kind != "workflow":
                continue
            payload = row.decoded_payload()
            if payload.get("steps"):
                continue  # inline steps run as given; the name is a label
            if (payload.get("workflow") or payload.get("name")) == name:
                refs.append(
                    {"kind": "schedule", "name": row.name, "enabled": bool(row.enabled)}
                )
    except Exception:  # noqa: BLE001 — a references check must never 500 a delete
        log.exception("workflow references: schedules could not be read")
    try:
        for rule in d.platform.reflex.list():
            if rule.action == "workflow" and rule.target == name:
                refs.append(
                    {
                        "kind": "reflex",
                        "id": rule.id,
                        "name": rule.name,
                        "enabled": bool(rule.enabled),
                    }
                )
    except Exception:  # noqa: BLE001
        log.exception("workflow references: reflex rules could not be read")
    return refs


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""

    def _spawn_run_or_interrupt(rec_id: str, coro) -> None:
        """Spawn a workflow-run coroutine, honestly. During daemon drain the
        governor refuses new background work and returns None (closing the
        coroutine) — the record would then sit 'running'/'resuming' as a
        zombie until the next boot's reconcile. Mark it 'interrupted' NOW so
        every surface tells the truth immediately (v1.170.0 coordinator)."""
        if d._spawn_bg(rec_id, coro) is not None:
            return
        from ...core.ids import utcnow
        from ...workflows.models import WorkflowRunRecord

        with session_scope(d.platform.engine) as db:
            rec = db.get(WorkflowRunRecord, rec_id)
            if rec is not None and rec.status in ("running", "resuming"):
                rec.status = "interrupted"
                rec.finished_at = rec.finished_at or utcnow()
                db.add(rec)
                db.commit()

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
        elif body.name:
            # v1.170.0 — ``name`` ALONE runs the SAVED def: stored steps + the
            # project pin resolve server-side via load_def (the ONE composition
            # point), so every caller — chat's workflow_run tool, the "+" menu,
            # a curl — picks the pin up for free instead of re-posting steps.
            from ...workflows.store import WorkflowStore

            wf = WorkflowStore(d.platform.engine).load_def(body.name)
            if wf is None:
                raise HTTPException(
                    status_code=404, detail=f"no saved workflow named '{body.name}'"
                )
            if body.project_id is not None:
                # Same override rule as the name+steps branch: explicit wins,
                # "" forces an unpinned run.
                wf.project_id = body.project_id.strip() or None
        else:
            raise HTTPException(
                status_code=400,
                detail="provide `toml`, `name`+`steps`, or the `name` of a saved workflow",
            )
        # Create the record synchronously (validating steps), then run it in the
        # BACKGROUND: the HTTP request no longer blocks for the multi-minute run
        # (which was aborting clients into a false "couldn't reach the daemon").
        engine = WorkflowEngine(d.platform, d.orchestrator)
        # v1.170.0 inputs (contract 5): forwarded ONLY when the caller sent
        # them, so the legacy call stays byte-identical to the engine.
        run_kwargs: dict[str, Any] = {}
        if body.inputs is not None:
            run_kwargs["inputs"] = dict(body.inputs)
        try:
            rec = engine.create_record(wf, **run_kwargs)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        _spawn_run_or_interrupt(rec.id, engine.run_record(rec, wf, **run_kwargs))
        return rec.model_dump()

    @app.get("/workflows/runs")
    def workflow_runs(
        limit: int = 50, status: str | None = None, slim: bool = False, offset: int = 0
    ) -> dict[str, Any]:
        """List runs, newest first.

        v1.168.0 ADDITIVE: ``status`` filters server-side — the notification
        bell polls for parked (``waiting``) runs, and a client-side filter over
        a newest-first page is chunk-blind (a parked run older than the page
        silently vanishes from the count). ``slim`` drops the heavy blobs
        (``steps_json``/``outputs_json`` — every step's outputs) so global
        chrome can poll cheaply; ``waiting_json`` survives slim mode because it
        carries the question the bell renders. Defaults unchanged.

        v1.170.0 ADDITIVE: ``offset`` pages past the newest rows (applied after
        the same ordering + ``status`` filter). Default 0 = exactly the old
        response.
        """
        from ...workflows.models import WorkflowRunRecord

        limit = max(1, min(200, limit))  # clamp: newest-first, bounded
        offset = max(0, offset)
        with session_scope(d.platform.engine) as db:
            stmt = (
                select(WorkflowRunRecord)
                .order_by(WorkflowRunRecord.started_at.desc())  # type: ignore[attr-defined]
                .limit(limit)
            )
            if status:
                stmt = stmt.where(WorkflowRunRecord.status == status)
            if offset:
                stmt = stmt.offset(offset)
            rows = list(db.exec(stmt))

        def _dump(r) -> dict[str, Any]:
            out = r.model_dump()
            if slim:
                out.pop("steps_json", None)
                out.pop("outputs_json", None)
            return out

        return {"runs": [_dump(r) for r in rows]}

    @app.post("/workflows/runs/prune")
    def workflow_runs_prune(keep: int = 500) -> dict[str, Any]:
        """Trim run HISTORY (v1.170.0): delete the oldest FINISHED runs beyond
        the newest ``keep``. Live state is untouchable — running, parked
        (waiting), cancelling, and resuming rows survive regardless of age —
        and ``interrupted`` (RESUMABLE, contract 4) rows survive the keep
        window too, pruned only past an age threshold so a rendered Resume
        button never 404s (see :func:`workflows.store.prune_runs`)."""
        from ...workflows.store import prune_runs

        keep = max(0, min(100_000, keep))
        deleted = prune_runs(d.platform.engine, keep=keep)
        return {"deleted": deleted, "keep": keep}

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
        session. A WAITING (parked) run has no engine loop to notice the flag,
        so it cancels directly. Cancelling a finished run → 409."""
        from ...core.ids import utcnow
        from ...workflows.models import WorkflowRunRecord

        with session_scope(d.platform.engine) as db:
            rec = db.get(WorkflowRunRecord, run_id)
            if rec is None:
                raise HTTPException(status_code=404, detail="no such run")
            if rec.status in ("completed", "failed", "cancelled", "interrupted"):
                raise HTTPException(status_code=409, detail=f"run already {rec.status}")
            if rec.status == "waiting":
                rec.status = "cancelled"
                rec.waiting_json = ""
                rec.finished_at = rec.finished_at or utcnow()
            else:
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

    @app.post("/workflows/runs/{run_id}/answer")
    async def workflow_run_answer(run_id: str, body: WorkflowAnswerBody) -> dict[str, Any]:
        """Answer a parked run's question (v1.121.0): the human gate opens and
        the remaining steps run in the background. Returns the resumed record
        immediately (run polling / step events carry the rest, exactly like
        POST /workflows/run)."""
        from ...workflows.engine import WorkflowEngine
        from ...workflows.models import WorkflowRunRecord

        answer = (body.answer or "").strip()
        if not answer:
            raise HTTPException(status_code=400, detail="an answer is required")
        # ATOMIC claim (waiting -> resuming): the gate is answerable from two
        # surfaces at once (the chat card and this page), and a double-submit
        # without a compare-and-set would resume the tail TWICE — duplicate
        # sessions, duplicate notify sends, interleaved record writes.
        from sqlalchemy import update as _sql_update

        with session_scope(d.platform.engine) as db:
            rec = db.get(WorkflowRunRecord, run_id)
            if rec is None:
                raise HTTPException(status_code=404, detail="no such run")
            claimed = db.exec(
                _sql_update(WorkflowRunRecord)
                .where(
                    WorkflowRunRecord.id == run_id,
                    WorkflowRunRecord.status == "waiting",
                )
                .values(status="resuming")
            )
            db.commit()
            if getattr(claimed, "rowcount", 0) != 1:
                raise HTTPException(
                    status_code=409,
                    detail=f"run is {rec.status}, not waiting — it may already be answered",
                )
            db.refresh(rec)
        engine = WorkflowEngine(d.platform, d.orchestrator)
        _spawn_run_or_interrupt(rec.id, engine.resume_after_answer(rec, answer))
        return {"id": run_id, "status": "running", "answered": True}

    @app.post("/workflows/runs/{run_id}/resume")
    async def workflow_run_resume(run_id: str) -> dict[str, Any]:
        """Resume an ``interrupted`` run (v1.170.0) from its first
        non-completed step — a daemon restart mid-run no longer means starting
        the whole workflow (and re-doing its side effects) from scratch.

        ATOMIC claim (interrupted -> resuming), the exact compare-and-set
        /answer uses: Resume buttons render on two surfaces at once, and a
        double-submit without it would drive the tail TWICE. Any other status
        is an honest 409. ``finished_at`` is cleared (the reconciler stamped
        it; a resuming run is not finished) and the engine's resume helper
        rebuilds the def + remaining work from the record alone.
        """
        from ...workflows.engine import WorkflowEngine
        from ...workflows.models import WorkflowRunRecord
        from sqlalchemy import update as _sql_update

        engine = WorkflowEngine(d.platform, d.orchestrator)
        with session_scope(d.platform.engine) as db:
            rec = db.get(WorkflowRunRecord, run_id)
            if rec is None:
                raise HTTPException(status_code=404, detail="no such run")
            # Pre-validate BEFORE claiming (coordinator, v1.170.0): an
            # unreconstructable record (corrupt steps_json) that we claimed
            # first would sit in 'resuming' until the next boot's reconcile.
            # Validate sync, refuse honest, claim only what can actually run.
            if rec.status == "interrupted":
                try:
                    engine.rebuild_run(rec)
                except ValueError as exc:
                    raise HTTPException(
                        status_code=422,
                        detail=f"this run cannot be reconstructed: {exc}",
                    )
            claimed = db.exec(
                _sql_update(WorkflowRunRecord)
                .where(
                    WorkflowRunRecord.id == run_id,
                    WorkflowRunRecord.status == "interrupted",
                )
                .values(status="resuming", finished_at=None, current_session_id=None)
            )
            db.commit()
            if getattr(claimed, "rowcount", 0) != 1:
                raise HTTPException(
                    status_code=409,
                    detail=f"run is {rec.status}, not interrupted — only "
                    f"interrupted runs can be resumed",
                )
            db.refresh(rec)
            out = rec.model_dump()
        _spawn_run_or_interrupt(rec.id, engine.resume_interrupted(rec))
        return out

    # Saved workflow definitions (agents author these; the editor loads/saves them).
    @app.get("/workflows")
    def list_workflows() -> dict[str, Any]:
        from ...workflows.store import WorkflowStore

        return {
            "workflows": [w.model_dump() for w in WorkflowStore(d.platform.engine).list()]
        }

    def _validate_step_shapes(steps: list[dict]) -> None:
        """SAVE-time strictness (v1.170.0): a step whose ``kind``/``on_failure``
        the loader would SILENTLY rewrite to the default is a 422 naming the
        field — a def that says ``kind: "tools"`` and runs as an agent step is
        a misconfiguration discovered mid-run, weeks later. Loading stays
        lenient on purpose (old rows keep working); only the save gate is
        strict. Absent/empty values still mean "the default", exactly as the
        loader treats them.
        """
        from ...workflows.engine import ON_FAILURE, STEP_KINDS

        for i, raw in enumerate(steps or []):
            if not isinstance(raw, dict):
                continue  # the loader ignores non-dict entries; keep parity
            kind_raw = raw.get("kind")
            kind = "agent"
            if kind_raw:  # falsy = absent/empty = the default, never a rewrite
                kind = str(kind_raw).strip().lower()
                if kind not in STEP_KINDS:
                    raise HTTPException(
                        status_code=422,
                        detail=f"steps[{i}].kind: {kind_raw!r} is not one of "
                        + "|".join(STEP_KINDS),
                    )
            # CONTENT (v1.225.0): a step that cannot possibly run is refused at
            # save time, naming the step — an agent step with no task ran an
            # agent on an empty instruction, a tool step with no tool failed
            # at its turn mid-run, an ask with no question parked the run on
            # "Continue past “x”?". Each used to be discovered minutes into a
            # run instead of at the moment of saving.
            label = str(raw.get("name") or "").strip() or f"#{i + 1}"
            has_task = bool(str(raw.get("task") or "").strip())
            has_msg = bool(str(raw.get("message") or "").strip())
            if kind == "agent" and not has_task and not str(raw.get("name") or "").strip():
                raise HTTPException(
                    status_code=422,
                    detail=f"steps[{i}] ({label}): an agent step needs a task",
                )
            if kind == "tool" and not str(raw.get("tool") or "").strip():
                raise HTTPException(
                    status_code=422,
                    detail=f"steps[{i}] ({label}): a tool step needs a tool name",
                )
            if kind in ("ask", "notify") and not has_msg and not has_task:
                raise HTTPException(
                    status_code=422,
                    detail=f"steps[{i}] ({label}): an {kind} step needs a message",
                )
            failure_raw = raw.get("on_failure")
            if failure_raw:
                on_failure = str(failure_raw).strip().lower()
                if on_failure not in ON_FAILURE:
                    raise HTTPException(
                        status_code=422,
                        detail=f"steps[{i}].on_failure: {failure_raw!r} is not "
                        "one of " + "|".join(ON_FAILURE),
                    )

    @app.post("/workflows")
    def save_workflow(body: WorkflowSaveBody) -> dict[str, Any]:
        from ...workflows.store import WorkflowStore

        _validate_step_shapes(body.steps)
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

    @app.patch("/workflows/{name}")
    def patch_workflow(name: str, body: WorkflowPatchBody) -> dict[str, Any]:
        """Rename / re-describe a saved workflow IN PLACE (v1.170.0) — steps
        untouched, project pin MOVED with the name (contract 3). ``None``
        leaves a field alone; 404 unknown; 409 when ``new_name`` is taken.
        Response = the same shape GET /workflows/{name} returns, under the
        NEW name."""
        from ...workflows.store import WorkflowStore

        new_name = body.new_name
        if new_name is not None:
            new_name = new_name.strip()
            if not new_name:
                raise HTTPException(
                    status_code=422, detail="new_name must be non-empty"
                )
        store = WorkflowStore(d.platform.engine)
        try:
            rec = store.patch(name, new_name=new_name, description=body.description)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        if rec is None:
            raise HTTPException(status_code=404, detail="no such workflow")
        out = rec.model_dump()
        out["project_id"] = store.get_project_id(rec.name)
        return out

    @app.get("/workflows/{name}/references")
    def workflow_references(name: str) -> dict[str, Any]:
        """What still fires this saved workflow by name (schedules, reflex
        rules) — the preflight the delete confirm shows, so the user learns
        BEFORE deleting that a nightly schedule is about to start failing.
        404 when the workflow itself does not exist."""
        from ...workflows.store import WorkflowStore

        if WorkflowStore(d.platform.engine).get(name) is None:
            raise HTTPException(status_code=404, detail="no such workflow")
        return {"name": name, "references": _workflow_references(d, name)}

    @app.delete("/workflows/{name}")
    def delete_workflow(name: str) -> dict[str, Any]:
        """Delete a saved workflow definition by name (404 when absent). The
        schedules / reflex rules that still name it are NOT touched — they are
        the user's automations and re-pointing them is their call — but they
        are reported as ``referenced_by`` so an API caller learns what just
        started failing (the dashboard asks the same question up front)."""
        from ...workflows.store import WorkflowStore

        referenced_by = _workflow_references(d, name)
        if not WorkflowStore(d.platform.engine).remove(name):
            raise HTTPException(status_code=404, detail="no such workflow")
        return {"deleted": name, "referenced_by": referenced_by}

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

    # Requirement annotation (v1.128.0): every template says what it needs to
    # actually run (pinned model connected? Pixio key present? email plug-in
    # live?) and WHERE to set it up — before the run fails, not after.
    def _requirement_context() -> dict:
        from ...agents.types import _DEFINITIONS
        from .connections import selectable_models

        try:
            models = selectable_models(d)
        except Exception:  # noqa: BLE001 — annotation must never break listing
            models = []
        try:
            dynamic = [r.name for r in d.platform.agents_registry.list()]
        except Exception:  # noqa: BLE001
            dynamic = []
        return {
            "selectable_models": models,
            "live_tools": list(d.platform.registry.names()),
            "has_secret": d.platform.secrets.get,
            "comm_config": dict(getattr(d.platform.config, "comm", None) or {}),
            "agent_names": [t.value for t in _DEFINITIONS] + dynamic,
        }

    def _annotate(row: dict, ctx: dict) -> dict:
        from ...templates import analyze_requirements

        try:
            reqs = analyze_requirements(
                row.get("task") or "",
                row.get("provider"),
                row.get("model"),
                row.get("agent_type"),
                **ctx,
            )
        except Exception:  # noqa: BLE001 — a checker bug must never hide templates
            reqs = []
        row["requirements"] = reqs
        row["ready"] = all(r["ok"] for r in reqs)
        return row

    # Saved prompts / task templates (one-click re-run of a frequent task).
    @app.get("/templates")
    def list_templates() -> dict[str, Any]:
        from ...templates import TemplateStore

        ctx = _requirement_context()
        return {
            "templates": [
                _annotate(t.model_dump(), ctx)
                for t in TemplateStore(d.platform.engine).list()
            ]
        }

    @app.get("/templates/starters")
    def template_starters() -> dict[str, Any]:
        """The browsable starter library (v1.128.0) — curated templates the
        user can add ANY time, each annotated with what it needs and where to
        set that up. ``already_added`` = a saved template with the same name
        exists (case-insensitive), so the page can offer Add once."""
        from ...templates import STARTER_CATALOG, TemplateStore

        ctx = _requirement_context()
        existing = {
            (t.name or "").strip().lower()
            for t in TemplateStore(d.platform.engine).list()
        }
        starters = []
        for entry in STARTER_CATALOG:
            row = {k: v for k, v in entry.items() if k != "seed"}
            row = _annotate(row, ctx)
            row["already_added"] = entry["name"].strip().lower() in existing
            starters.append(row)
        return {"starters": starters}

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

    @app.patch("/templates/{prompt_id}")
    def update_template(prompt_id: str, body: TemplateUpdateBody) -> dict[str, Any]:
        """Edit a template in place (v1.128.0) — fixing a typo or repointing
        the model used to mean delete + retype everything."""
        from ...templates import TemplateStore

        rec = TemplateStore(d.platform.engine).update(
            prompt_id,
            name=body.name,
            task=body.task,
            agent_type=body.agent_type,
            provider=body.provider,
            model=body.model,
            description=body.description,
            clear_model=body.clear_model,
        )
        if rec is None:
            raise HTTPException(status_code=404, detail="no such template")
        return _annotate(rec.model_dump(), _requirement_context())

    @app.delete("/templates/{prompt_id}")
    def delete_template(prompt_id: str) -> dict[str, Any]:
        from ...templates import TemplateStore

        return {"removed": TemplateStore(d.platform.engine).remove(prompt_id)}
