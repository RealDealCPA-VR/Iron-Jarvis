"""FastAPI daemon (§9).

The single long-running process that owns the Orchestrator and Event Bus and
exposes them over REST + a WebSocket event stream for the dashboard (§4).
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlmodel import select

from .. import __version__
from ..agents.orchestrator import Orchestrator
from ..core.db import session_scope
from ..core.models import AgentType
from ..platform import build_platform
from ..tools.permissions import headless_ask_resolver


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


class SessionCreate(BaseModel):
    task: str
    agent_type: str = "builder"
    provider: str | None = None
    wait: bool = True


class MemoryWrite(BaseModel):
    layer: str = "project"
    key: str
    text: str
    scope_id: str | None = None


class WorkflowRunBody(BaseModel):
    toml: str | None = None
    name: str | None = None
    steps: list[dict] | None = None


class WorkflowSaveBody(BaseModel):
    name: str
    steps: list[dict] = []
    description: str = ""


class SecretSet(BaseModel):
    name: str
    value: str
    kind: str = "generic"
    description: str = ""


class NotifyBody(BaseModel):
    message: str
    channels: list[str] | None = None


class IntegrationConfigBody(BaseModel):
    config: dict = {}


class IntegrationEnableBody(BaseModel):
    enabled: bool = True


class ScheduleAdd(BaseModel):
    name: str
    cron: str | None = None
    run_at: str | None = None
    interval_seconds: int | None = None
    kind: str = "workflow"
    payload: dict = {}


class LTMAppend(BaseModel):
    title: str
    content: str
    source: str | None = None


class LTMSourceBody(BaseModel):
    name: str
    kind: str = "markdown"  # markdown | notion
    path: str = ""
    database_id: str = ""
    token_secret: str = ""


class AgentCreate(BaseModel):
    name: str
    system_prompt: str
    tools: list[str] = []
    description: str = ""
    provider: str = ""
    model: str = ""


class WebhookCreate(BaseModel):
    slug: str
    direction: str = "inbound"  # inbound | outbound
    target_url: str = ""
    event_types: list[str] = []
    secret_name: str = ""


class SpawnBody(BaseModel):
    task: str


def _agent_type(name: str) -> AgentType:
    try:
        return AgentType(name)
    except ValueError:
        return AgentType.BUILDER


def _session_view(session) -> dict[str, Any]:
    return {
        "id": session.id,
        "task": session.task,
        "agent_type": session.agent_type.value,
        "provider": session.provider,
        "model": session.model,
        "status": session.status.value,
        "workspace_path": session.workspace_path,
        "summary": session.summary,
        "created_at": session.created_at.isoformat(),
        "finished_at": session.finished_at.isoformat() if session.finished_at else None,
    }


def create_app(project_root: str | None = None) -> FastAPI:
    # Headless mode: no human can answer an "ask", so wire a resolver that
    # auto-approves only low-risk orchestration (delegate) and keeps dangerous
    # tools (shell) fail-closed. This is what lets supervised sessions delegate.
    platform = build_platform(
        project_root or os.getcwd(), ask_resolver=headless_ask_resolver()
    )
    # Opt-in git-native sessions (run→review→approve over HTTP) via env/--git-native.
    if _env_truthy("IRONJARVIS_GIT_NATIVE"):
        platform.config.git_native = True
    orchestrator = Orchestrator(platform)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:  # start the cron scheduler when the daemon boots
            platform.scheduler.start()
        except Exception:  # pragma: no cover - never block boot
            pass
        try:
            yield
        finally:
            try:
                platform.scheduler.shutdown()
            except Exception:  # pragma: no cover
                pass

    app = FastAPI(title="Iron Jarvis", version=__version__, lifespan=lifespan)
    # Local dashboard (e.g. http://localhost:3000) is cross-origin to the daemon.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.platform = platform
    app.state.orchestrator = orchestrator

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "default_provider": platform.config.default_provider,
            "default_model": platform.config.default_model,
            "providers": platform.providers.health(),
        }

    @app.get("/tools")
    def tools() -> dict[str, Any]:
        return {"tools": platform.registry.specs()}

    @app.get("/providers")
    def providers() -> dict[str, Any]:
        return {"providers": platform.providers.health()}

    @app.post("/sessions")
    async def create_session(body: SessionCreate) -> dict[str, Any]:
        session = await orchestrator.create_session(
            body.task, _agent_type(body.agent_type), body.provider
        )
        if body.wait:
            session = await orchestrator.run_session(session.id)
        else:
            asyncio.create_task(orchestrator.run_session(session.id))
        return _session_view(session)

    @app.get("/sessions")
    def list_sessions() -> dict[str, Any]:
        return {"sessions": [_session_view(s) for s in orchestrator.list_sessions()]}

    @app.get("/sessions/{session_id}")
    def get_session(session_id: str) -> dict[str, Any]:
        session = orchestrator.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        return {
            "session": _session_view(session),
            "transcript": orchestrator.transcript(session_id),
        }

    # --- Observability + Evaluation (§29, §30) ----------------------------

    @app.get("/metrics")
    def metrics() -> dict[str, Any]:
        return platform.observability.metrics()

    @app.get("/sessions/{session_id}/traces")
    def traces(session_id: str) -> dict[str, Any]:
        return {"traces": platform.observability.traces(session_id)}

    @app.get("/sessions/{session_id}/evaluation")
    def evaluation(session_id: str) -> dict[str, Any]:
        ev = platform.evaluator.latest(session_id)
        if ev is None:
            try:
                ev = platform.evaluator.evaluate(session_id)
            except Exception:
                ev = None
        if ev is None:
            raise HTTPException(status_code=404, detail="no evaluation")
        return ev.model_dump()

    # --- Memory (§21, §22) ------------------------------------------------

    @app.get("/memory/search")
    def memory_search(q: str, k: int = 5) -> dict[str, Any]:
        hits = platform.memory.search(q, k=k)
        return {
            "results": [
                {"layer": r.layer, "key": r.key, "text": r.text, "score": score}
                for r, score in hits
            ]
        }

    @app.post("/memory")
    def memory_write(body: MemoryWrite) -> dict[str, Any]:
        try:
            rec = platform.memory.write(
                body.layer, body.key, body.text, scope_id=body.scope_id
            )
        except ValueError as exc:  # unknown layer -> client error, not a 500
            raise HTTPException(status_code=400, detail=str(exc))
        return {"id": rec.id, "layer": rec.layer, "key": rec.key}

    @app.get("/memory/{layer}/{key}")
    def memory_read(layer: str, key: str) -> dict[str, Any]:
        text = platform.memory.read(layer, key)
        if text is None:
            raise HTTPException(status_code=404, detail="not found")
        return {"layer": layer, "key": key, "text": text}

    # --- Skills (§23) -----------------------------------------------------

    @app.get("/skills")
    def skills() -> dict[str, Any]:
        return {
            "skills": [
                {"name": s.name, "description": s.description}
                for s in platform.skills.list()
            ]
        }

    @app.get("/skills/{name}")
    def skill(name: str) -> dict[str, Any]:
        sk = platform.skills.get(name)
        if sk is None:
            raise HTTPException(status_code=404, detail="no such skill")
        return {"name": sk.name, "description": sk.description, "instructions": sk.instructions}

    # --- Artifacts (§26) --------------------------------------------------

    @app.get("/artifacts")
    def artifacts() -> dict[str, Any]:
        return {"artifacts": platform.artifacts.list_names()}

    @app.get("/artifacts/{name}")
    def artifact(name: str) -> dict[str, Any]:
        art = platform.artifacts.latest(name)
        if art is None:
            raise HTTPException(status_code=404, detail="no such artifact")
        try:
            content = platform.artifacts.read(name).decode("utf-8", "replace")
        except Exception:
            content = None
        return {
            "name": art.name,
            "version": art.version,
            "size": art.size,
            "versions": platform.artifacts.versions(name),
            "content": content,
        }

    # --- Browser vault status (§10) ---------------------------------------

    @app.get("/vault")
    def vault() -> dict[str, Any]:
        return {"providers": platform.vault.providers()}

    # --- Workflows (§24, §25) ---------------------------------------------

    @app.post("/workflows/run")
    async def workflow_run(body: WorkflowRunBody) -> dict[str, Any]:
        from ..workflows.engine import WorkflowEngine, load_workflow, load_workflow_toml

        if body.toml:
            wf = load_workflow_toml(body.toml)
        elif body.name and body.steps is not None:
            wf = load_workflow({"name": body.name, "steps": body.steps})
        else:
            raise HTTPException(status_code=400, detail="provide `toml` or `name`+`steps`")
        rec = await WorkflowEngine(platform).run(wf)
        return rec.model_dump()

    @app.get("/workflows/runs")
    def workflow_runs() -> dict[str, Any]:
        from ..workflows.models import WorkflowRunRecord

        with session_scope(platform.engine) as db:
            rows = list(db.exec(select(WorkflowRunRecord)))
        return {"runs": [r.model_dump() for r in rows]}

    # Saved workflow definitions (agents author these; the editor loads/saves them).
    @app.get("/workflows")
    def list_workflows() -> dict[str, Any]:
        from ..workflows.store import WorkflowStore

        return {
            "workflows": [w.model_dump() for w in WorkflowStore(platform.engine).list()]
        }

    @app.post("/workflows")
    def save_workflow(body: WorkflowSaveBody) -> dict[str, Any]:
        from ..workflows.store import WorkflowStore

        rec = WorkflowStore(platform.engine).save(
            body.name, body.steps, description=body.description
        )
        return rec.model_dump()

    @app.get("/workflows/{name}")
    def get_workflow(name: str) -> dict[str, Any]:
        from ..workflows.store import WorkflowStore

        rec = WorkflowStore(platform.engine).get(name)
        if rec is None:
            raise HTTPException(status_code=404, detail="no such workflow")
        return rec.model_dump()

    # --- Review (§27, §28) — approve/reject; agents never auto-merge -------

    @app.get("/sessions/{session_id}/review")
    def get_review(session_id: str) -> dict[str, Any]:
        review = orchestrator.get_review(session_id)
        if review is None:
            raise HTTPException(status_code=404, detail="no review for session")
        return asdict(review)

    @app.post("/reviews/{session_id}/approve")
    def approve_review(session_id: str) -> dict[str, Any]:
        if orchestrator.get_review(session_id) is None:
            raise HTTPException(status_code=404, detail="no review for session")
        return {"merged": orchestrator.approve_review(session_id)}

    @app.post("/reviews/{session_id}/reject")
    def reject_review(session_id: str) -> dict[str, Any]:
        if orchestrator.get_review(session_id) is None:
            raise HTTPException(status_code=404, detail="no review for session")
        orchestrator.reject_review(session_id)
        return {"status": "rejected"}

    # --- Secrets (shared, encrypted) — names/metadata only, never values --

    @app.get("/secrets")
    def list_secrets() -> dict[str, Any]:
        return {"secrets": platform.secrets.list()}

    @app.post("/secrets")
    def set_secret(body: SecretSet) -> dict[str, Any]:
        rec = platform.secrets.set(
            body.name, body.value, kind=body.kind, description=body.description
        )
        return {"name": rec.name, "kind": rec.kind}

    @app.delete("/secrets/{name}")
    def delete_secret(name: str) -> dict[str, Any]:
        return {"deleted": platform.secrets.delete(name)}

    # --- Integrations -----------------------------------------------------

    @app.get("/integrations")
    def list_integrations() -> dict[str, Any]:
        return {"integrations": platform.integrations.list_status()}

    @app.post("/integrations/{iid}/enable")
    def enable_integration(iid: str, body: IntegrationEnableBody) -> dict[str, Any]:
        if platform.integrations.get_spec(iid) is None:
            raise HTTPException(status_code=404, detail="unknown integration")
        platform.integrations.enable(iid, body.enabled)
        return {"id": iid, "enabled": body.enabled}

    @app.post("/integrations/{iid}/configure")
    def configure_integration(iid: str, body: IntegrationConfigBody) -> dict[str, Any]:
        if platform.integrations.get_spec(iid) is None:
            raise HTTPException(status_code=404, detail="unknown integration")
        platform.integrations.configure(iid, body.config)
        return {"id": iid, "configured": True}

    @app.post("/integrations/{iid}/test")
    def test_integration(iid: str) -> dict[str, Any]:
        if platform.integrations.get_spec(iid) is None:
            raise HTTPException(status_code=404, detail="unknown integration")
        return platform.integrations.test(iid, platform.secrets.get)

    # --- Communication channels -------------------------------------------

    @app.get("/comm/channels")
    def comm_channels() -> dict[str, Any]:
        return {"channels": platform.notifier.channels()}

    @app.post("/comm/notify")
    def comm_notify(body: NotifyBody) -> dict[str, Any]:
        return platform.notifier.notify(body.message, body.channels)

    # --- Webhooks (inbound dispatch + listing) ----------------------------

    @app.get("/webhooks")
    def list_webhooks() -> dict[str, Any]:
        from ..webhooks.models import WebhookRecord

        with session_scope(platform.engine) as db:
            rows = list(db.exec(select(WebhookRecord)))
        return {"webhooks": [r.model_dump() for r in rows]}

    @app.post("/webhooks")
    def create_webhook(body: WebhookCreate) -> dict[str, Any]:
        secret = platform.secrets.get(body.secret_name) if body.secret_name else None
        if body.direction == "outbound":
            if not body.target_url:
                raise HTTPException(status_code=400, detail="outbound needs target_url")
            platform.outbound_webhooks.register(
                body.slug, body.target_url, body.event_types, secret=secret
            )
        else:  # inbound: a default handler that emits a webhook.received event
            async def _handler(payload: dict, _slug: str = body.slug) -> dict[str, Any]:
                await platform.event_bus.publish(
                    "webhook.received", {"slug": _slug, "body": payload}
                )
                return {"ok": True, "slug": _slug}

            platform.inbound_webhooks.register(body.slug, _handler, secret=secret)
        return {"slug": body.slug, "direction": body.direction}

    @app.post("/webhooks/{slug}")
    async def inbound_webhook(slug: str, request: Request) -> dict[str, Any]:
        raw = await request.body()
        sig = request.headers.get("X-IronJarvis-Signature") or request.headers.get(
            "X-Signature"
        )
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        return await platform.inbound_webhooks.dispatch(
            slug, body, raw=raw, signature=sig
        )

    # --- File search ------------------------------------------------------

    @app.get("/filesearch/drives")
    def filesearch_drives() -> dict[str, Any]:
        from ..filesearch.service import list_drives

        return {"drives": list_drives()}

    @app.get("/filesearch")
    def filesearch(
        q: str, mode: str = "content", limit: int = 50, root: str | None = None
    ) -> dict[str, Any]:
        roots = [Path(root)] if root else None
        return {
            "results": platform.filesearch.search(q, mode=mode, limit=limit, roots=roots)
        }

    # --- Scheduled tasks --------------------------------------------------

    @app.get("/schedules")
    def list_schedules() -> dict[str, Any]:
        return {"schedules": [t.model_dump() for t in platform.scheduler.list()]}

    @app.post("/schedules")
    def add_schedule(body: ScheduleAdd) -> dict[str, Any]:
        try:
            rec = platform.scheduler.add_task(
                body.name,
                body.cron,
                run_at=body.run_at,
                interval_seconds=body.interval_seconds,
                kind=body.kind,
                payload=body.payload,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return rec.model_dump()

    @app.delete("/schedules/{name}")
    def remove_schedule(name: str) -> dict[str, Any]:
        return {"removed": platform.scheduler.remove(name)}

    @app.post("/schedules/{name}/run")
    async def run_schedule(name: str) -> dict[str, Any]:
        await platform.scheduler.run_now(name)
        return {"ran": name}

    # --- Long-term memory -------------------------------------------------

    @app.get("/ltm/search")
    def ltm_search(q: str, source: str | None = None, k: int = 5) -> dict[str, Any]:
        try:
            return {"results": platform.ltm.search(q, k=k, source=source)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/ltm/append")
    def ltm_append(body: LTMAppend) -> dict[str, Any]:
        try:
            src = body.source or platform.ltm.default_source()
            ref = platform.ltm.append(body.title, body.content, source=src)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ref": ref, "source": src}

    @app.get("/ltm/sources")
    def ltm_sources() -> dict[str, Any]:
        from ..ltm.sources import CustomSourceStore

        return {
            "sources": [s.model_dump() for s in CustomSourceStore(platform.engine).list()],
            "active": platform.ltm.sources(),
        }

    @app.post("/ltm/sources")
    def add_ltm_source(body: LTMSourceBody) -> dict[str, Any]:
        from ..ltm.sources import CustomSourceStore, connector_from_record

        store = CustomSourceStore(platform.engine)
        try:
            rec = store.add(
                body.name,
                body.kind,
                path=body.path,
                database_id=body.database_id,
                token_secret=body.token_secret,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        try:  # register it live so it's searchable without a restart
            conn = connector_from_record(
                rec,
                secret_resolver=platform.secrets.get,
                http_factory=lambda: httpx.Client(timeout=30),
            )
            platform.ltm.register(conn)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=400, detail=f"source saved but not loadable: {exc}"
            )
        return {"name": rec.name, "kind": rec.kind}

    @app.delete("/ltm/sources/{name}")
    def remove_ltm_source(name: str) -> dict[str, Any]:
        from ..ltm.sources import CustomSourceStore

        return {"removed": CustomSourceStore(platform.engine).remove(name)}

    # --- Agents (built-in + dynamic; agents that add agents) --------------

    @app.get("/models")
    def list_models() -> dict[str, Any]:
        from ..agents.dynamic import available_models

        return {"models": available_models()}

    @app.get("/agents")
    def list_agents() -> dict[str, Any]:
        from ..agents.types import _DEFINITIONS

        return {
            "builtin": [t.value for t in _DEFINITIONS],
            "dynamic": [
                {
                    "name": r.name,
                    "description": r.description,
                    "provider": r.provider,
                    "model": r.model,
                }
                for r in platform.agents_registry.list()
            ],
        }

    @app.post("/agents")
    def create_agent(body: AgentCreate) -> dict[str, Any]:
        rec = platform.agents_registry.register(
            body.name,
            body.system_prompt,
            body.tools,
            description=body.description,
            provider=body.provider,
            model=body.model,
        )
        return {"name": rec.name, "provider": rec.provider, "model": rec.model}

    @app.post("/agents/{name}/spawn")
    async def spawn_agent_ep(name: str, body: SpawnBody) -> dict[str, Any]:
        from ..agents.runtime import AgentRuntime
        from ..agents.types import get_agent_definition
        from ..core.ids import utcnow
        from ..core.models import AgentState, AgentType, SessionStatus

        definition = platform.agents_registry.definition(name)
        rec = platform.agents_registry.get(name)
        if definition is None:
            try:
                definition = get_agent_definition(AgentType(name))
            except ValueError:
                raise HTTPException(status_code=404, detail="unknown agent")
        provider = rec.provider if (rec and rec.provider) else None
        session = await orchestrator.create_session(
            body.task, definition.type, provider=provider
        )
        run = await AgentRuntime(platform).run(session, definition)
        session.status = (
            SessionStatus.COMPLETED
            if run.state is AgentState.COMPLETED
            else SessionStatus.FAILED
        )
        session.summary = run.result
        session.finished_at = utcnow()
        orchestrator._save(session)
        return _session_view(session)

    @app.websocket("/events")
    async def events(ws: WebSocket) -> None:
        await ws.accept()
        try:
            async for event in platform.event_bus.subscribe():
                await ws.send_json(event.to_dict())
        except WebSocketDisconnect:
            return

    return app
