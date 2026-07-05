"""System routes: health, updates, usage, schedules, blackboard, events WS.

Moved verbatim from daemon/app.py's create_app; closure-local state is
reached through ``d`` (see the deps object built in create_app).
"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from typing import Any

from .. import app as _app
from ..app import _ws_token_ok
from ..schemas import ScheduleAdd, UpdateBody
from ... import __version__
from ...core.db import session_scope


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""
    @app.get("/health")
    def health() -> dict[str, Any]:
        # The ACTIVE project (context spine) rides along so the UI can show
        # "working in: X" everywhere without a second poll.
        active_project = None
        pid = getattr(d.platform.config, "active_project_id", None)
        if pid:
            from ...core.models import Project

            try:
                with session_scope(d.platform.engine) as db:
                    p = db.get(Project, pid)
                if p is not None:
                    active_project = {"id": p.id, "name": p.name, "root": p.root}
            except Exception:  # noqa: BLE001 — health must never fail
                pass
        return {
            "status": "ok",
            "version": __version__,
            "default_provider": d.platform.config.default_provider,
            "default_model": d.platform.config.default_model,
            "active_project": active_project,
            "providers": d._visible_providers(),
        }

    @app.get("/blackboard/{board_id}")
    def blackboard(board_id: str) -> dict[str, Any]:
        """Read a department's shared blackboard (notes + messages) for the UI."""
        from ...blackboard.tools import _to_view

        store = d.platform.blackboard
        if store is None:
            return {"board_id": board_id, "records": []}
        records = store.list(board_id)
        return {"board_id": board_id, "records": _to_view(records)}

    @app.get("/self-dev")
    def self_dev_status() -> dict[str, Any]:
        """Whether agents may edit Iron Jarvis's own source (opt-in, review-gated)."""
        from ...core.self_dev import self_dev_status as _status

        return _status(d.platform.config)

    @app.get("/update/check")
    def update_check() -> dict[str, Any]:
        """Is a newer commit available on this checkout's upstream branch?"""
        from ...core.self_dev import iron_jarvis_repo_root
        from ...core.updates import update_status

        repo = iron_jarvis_repo_root(d.platform.config)
        if repo is None:
            return {
                "available": False,
                "reason": "not a source checkout (running from an installed package)",
            }
        return update_status(repo)

    @app.post("/update/apply")
    def update_apply(body: UpdateBody) -> dict[str, Any]:
        """Pull + rebuild this checkout. Returns the per-step log; restart required.

        NOTE: this updates the FILES on disk only — the daemon keeps running the
        old code until it is restarted (``restart_required`` in the response).
        """
        from ...core.self_dev import iron_jarvis_repo_root
        from ...core.updates import apply_update

        repo = iron_jarvis_repo_root(d.platform.config)
        if repo is None:
            return {
                "ok": False,
                "log": [],
                "restart_required": False,
                "reason": "not a source checkout",
            }
        return apply_update(repo, build_dashboard=body.build_dashboard)

    @app.post("/worktrees/prune")
    def prune_worktrees(all: bool = False) -> dict[str, Any]:
        """GC orphaned session worktrees (failed/missing; pass ?all=true for every orphan)."""
        return {"pruned": d.orchestrator.prune_orphan_worktrees(include_completed=all)}

    @app.get("/metrics")
    def metrics() -> dict[str, Any]:
        return d.platform.observability.metrics()

    @app.get("/usage")
    def usage(days: int = 30) -> dict[str, Any]:
        """Token + $ cost over time (totals, by-day, by-model) from agent runs."""
        return d.platform.observability.usage_summary(days)

    @app.post("/shutdown")
    def shutdown_daemon() -> dict[str, Any]:
        """Gracefully stop the daemon — used by the desktop app on Quit.

        Token-guarded like every other route. The response returns FIRST (the
        Timer defers the signal) so the caller sees the ack instead of a reset
        connection; the desktop app then waits for process exit and only
        force-kills as a fallback.
        """
        import threading as _threading

        _threading.Timer(0.2, _app._graceful_stop).start()
        return {"ok": True, "detail": "daemon shutting down"}

    @app.get("/schedules")
    def list_schedules() -> dict[str, Any]:
        return {"schedules": [t.model_dump() for t in d.platform.scheduler.list()]}

    @app.post("/schedules")
    def add_schedule(body: ScheduleAdd) -> dict[str, Any]:
        try:
            rec = d.platform.scheduler.add_task(
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
        return {"removed": d.platform.scheduler.remove(name)}

    @app.post("/schedules/{name}/run")
    async def run_schedule(name: str) -> dict[str, Any]:
        await d.platform.scheduler.run_now(name)
        return {"ran": name}

    @app.websocket("/events")
    async def events(ws: WebSocket) -> None:
        # BaseHTTPMiddleware can't see WS scope, so guard the token here too.
        if not _ws_token_ok(ws):
            await ws.close(code=1008)
            return
        await ws.accept()
        # Race a receiver against the event stream so a client that disconnects
        # while idle is detected promptly (Starlette only surfaces a disconnect
        # via receive()) — otherwise the coroutine parks at queue.get() forever,
        # leaking the subscriber while publish() keeps appending to its queue.
        it = d.platform.event_bus.subscribe()
        recv_task = asyncio.ensure_future(ws.receive())
        next_task = asyncio.ensure_future(it.__anext__())
        try:
            while True:
                done, _ = await asyncio.wait(
                    {recv_task, next_task}, return_when=asyncio.FIRST_COMPLETED
                )
                if recv_task in done:
                    try:
                        msg = recv_task.result()
                    except WebSocketDisconnect:
                        break
                    if isinstance(msg, dict) and msg.get("type") == "websocket.disconnect":
                        break
                    recv_task = asyncio.ensure_future(ws.receive())  # ignore, keep streaming
                    continue
                if next_task in done:
                    event = next_task.result()
                    await ws.send_json(event.to_dict())
                    next_task = asyncio.ensure_future(it.__anext__())
        except (WebSocketDisconnect, StopAsyncIteration, RuntimeError):
            pass
        finally:
            recv_task.cancel()
            next_task.cancel()
            try:
                await it.aclose()  # runs subscribe()'s finally -> discards subscriber
            except Exception:
                pass
            try:
                await ws.close()
            except Exception:
                pass
