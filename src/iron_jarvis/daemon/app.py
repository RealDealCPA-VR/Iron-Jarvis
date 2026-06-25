"""FastAPI daemon (§9).

The single long-running process that owns the Orchestrator and Event Bus and
exposes them over REST + a WebSocket event stream for the dashboard (§4).
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from .. import __version__
from ..agents.orchestrator import Orchestrator
from ..core.models import AgentType
from ..platform import build_platform


class SessionCreate(BaseModel):
    task: str
    agent_type: str = "builder"
    provider: str | None = None
    wait: bool = True


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
    platform = build_platform(project_root or os.getcwd())
    orchestrator = Orchestrator(platform)

    app = FastAPI(title="Iron Jarvis", version=__version__)
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

    @app.websocket("/events")
    async def events(ws: WebSocket) -> None:
        await ws.accept()
        try:
            async for event in platform.event_bus.subscribe():
                await ws.send_json(event.to_dict())
        except WebSocketDisconnect:
            return

    return app
