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
from ..schemas import DesktopIncidentBody, ScheduleAdd, UpdateBody
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

    @app.get("/diagnostics/reliability")
    def diagnostics_reliability() -> dict[str, Any]:
        """Reliability signal (read-only, never raises): free disk on the state
        home plus a count of recent provider failures/failovers — the two silent
        degraders (a full disk, a flaky/rate-limited model) a daily driver hits
        that the polled /diagnostics doesn't surface. Additive to /diagnostics."""
        import shutil
        from datetime import timedelta

        from sqlalchemy import func, select

        from ...core.ids import utcnow
        from ...core.models import EventRecord

        out: dict[str, Any] = {}
        # Free disk on the state home — a full disk breaks the DB/backups/artifacts.
        try:
            usage = shutil.disk_usage(str(d.platform.config.home))
            out["disk"] = {"free": usage.free, "total": usage.total}
        except Exception:  # noqa: BLE001 — diagnostics must never raise
            out["disk"] = {"free": 0, "total": 0}
        # Recent provider failures/failovers (last 24h) aggregated into one signal.
        try:
            cutoff = utcnow() - timedelta(hours=24)
            with session_scope(d.platform.engine) as db:
                count = (
                    db.scalar(
                        select(func.count())
                        .select_from(EventRecord)
                        .where(EventRecord.type.in_(("provider.failed", "provider.failover")))
                        .where(EventRecord.created_at >= cutoff)
                    )
                    or 0
                )
            out["recent_provider_failures"] = int(count)
        except Exception:  # noqa: BLE001
            out["recent_provider_failures"] = 0
        return out

    @app.post("/system/incident")
    async def system_incident(body: DesktopIncidentBody) -> dict[str, Any]:
        """Desktop-shell incident intake (v1.130.0). The Electron watchdog
        reports renderer freezes, renderer crashes, and GPU-process deaths
        here; publishing lands them in the persisted event log (type
        ``desktop.incident``) — so the next "the app froze" arrives with
        queryable evidence instead of a shrug. Inputs are clamped: this is
        a log line, not a payload channel."""
        kind = (
            "".join(
                c for c in (body.kind or "").strip().lower() if c.isalnum() or c in "-_"
            )[:40]
            or "unknown"
        )
        detail = " ".join((body.detail or "").split())[:500]
        await d.platform.event_bus.publish(
            "desktop.incident", {"kind": kind, "detail": detail}
        )
        return {"ok": True, "kind": kind}

    @app.get("/blackboard/{board_id}")
    def blackboard(board_id: str) -> dict[str, Any]:
        """Read a department's shared blackboard (notes + messages) for the UI.

        The board is keyed by the ROOT session id, but a TeamTree link lands the
        user on a CHILD session's page (v1.166.0): when this id has no records
        of its own, walk the AgentRun ``parent_id`` chain upward (bounded — the
        delegation depth cap is 3) and serve the root's board instead. The
        response's ``board_id`` reports the board actually served so the client
        is never told a child id owns the root's notes."""
        from sqlmodel import select as _select

        from ...blackboard.tools import _to_view
        from ...core.db import session_scope as _scope
        from ...core.models import AgentRun as _Run

        store = d.platform.blackboard
        if store is None:
            return {"board_id": board_id, "records": []}
        records = store.list(board_id)
        resolved = board_id
        if not records:
            with _scope(d.platform.engine) as db:
                current = board_id
                for _hop in range(4):  # delegation depth cap +1, cycle-proof
                    runs = list(
                        db.exec(_select(_Run).where(_Run.session_id == current))
                    )
                    parent_run_id = next(
                        (r.parent_id for r in runs if r.parent_id), None
                    )
                    if not parent_run_id:
                        break
                    parent_run = db.get(_Run, parent_run_id)
                    if parent_run is None or not parent_run.session_id:
                        break
                    if parent_run.session_id == current:
                        break
                    current = parent_run.session_id
                if current != board_id:
                    up = store.list(current)
                    if up:
                        records, resolved = up, current
        return {"board_id": resolved, "records": _to_view(records)}

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
        """Token + $ cost over time (totals, by-day, by-model) from agent runs
        PLUS the user's OpenCode sessions (read live from OpenCode's own store
        — local-model work done in OpenCode counts, not just in-app runs)."""
        from ...eval.usage_view import merged_usage

        return merged_usage(d.platform, days)

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
        # v1.169.0 (additive): each row also carries the DECODED payload
        # ``project_id`` so the project surface can answer "what runs on my
        # behalf here?" without re-parsing the payload blob client-side.
        # Non-string / unparseable values become "" — never coerced (a
        # stringified number could phantom-match a real project id). The
        # isinstance-dict guard matters: decoded_payload() returns whatever
        # json.loads produced, and VALID-but-non-object JSON ("[]", '"x"',
        # "3") has no .get — one such row must not 500 the whole list.
        rows: list[dict[str, Any]] = []
        for t in d.platform.scheduler.list():
            row = t.model_dump()
            payload = t.decoded_payload()
            pid = payload.get("project_id") if isinstance(payload, dict) else None
            row["project_id"] = pid if isinstance(pid, str) else ""
            rows.append(row)
        return {"schedules": rows}

    @app.post("/schedules")
    def add_schedule(body: ScheduleAdd) -> dict[str, Any]:
        # Fail at ADD time, not at 3am fire time: a task schedule needs its
        # prompt, and a typo'd destination would silently deliver to nobody.
        payload = body.payload or {}
        if body.kind == "task" and not str(payload.get("task") or "").strip():
            raise HTTPException(
                status_code=400, detail="a task schedule needs 'task' text in payload"
            )
        wanted = payload.get("notify_channels")
        if wanted:
            known = set(d.platform.notifier.channels())
            unknown = [c for c in wanted if c not in known]
            if unknown:
                raise HTTPException(
                    status_code=400,
                    detail=f"unknown destination(s): {', '.join(unknown)} — "
                    f"add them on the Notifications page first",
                )
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
        """Fire now and return the OUTCOME (v1.119.0) — testing a schedule
        should feel like testing a destination: you learn how it went, not
        just that a trigger was pulled."""
        await d.platform.scheduler.run_now(name)
        rec = next((t for t in d.platform.scheduler.list() if t.name == name), None)
        return {
            "ran": name,
            "last_status": getattr(rec, "last_status", ""),
            "last_detail": getattr(rec, "last_detail", ""),
            "last_session_id": getattr(rec, "last_session_id", ""),
        }

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
