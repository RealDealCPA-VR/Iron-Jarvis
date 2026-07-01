"""FastAPI daemon (§9).

The single long-running process that owns the Orchestrator and Event Bus and
exposes them over REST + a WebSocket event stream for the dashboard (§4).
"""

from __future__ import annotations

import asyncio
import hmac
import html as _html
import json
import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlmodel import select

from .. import __version__
from ..agents.orchestrator import Orchestrator
from ..core.config import persist_config_values
from ..core.db import session_scope
from ..core.fs_policy import fs_read_ok, is_protected_path
from ..core.logging import get_logger
from ..core.models import AgentType
from ..platform import build_platform
from ..tools.permissions import headless_ask_resolver

log = get_logger("daemon")


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _max_upload_bytes() -> int:
    """Decoded-upload size cap (default 100 MB); override via IRONJARVIS_MAX_UPLOAD_MB."""
    try:
        mb = int(os.environ.get("IRONJARVIS_MAX_UPLOAD_MB", "100"))
    except ValueError:
        mb = 100
    return max(1, mb) * 1024 * 1024


_MAX_UPLOAD_BYTES = _max_upload_bytes()


def _ws_token_ok(ws: WebSocket) -> bool:
    """Constant-time WebSocket bearer-token check (matches the HTTP middleware)."""
    token = os.environ.get("IRONJARVIS_TOKEN", "").strip()
    if not token:
        return True
    candidate = ws.query_params.get("token") or ""
    return hmac.compare_digest(candidate, token)


class SessionCreate(BaseModel):
    task: str
    agent_type: str = "builder"
    provider: str | None = None
    model: str | None = None
    wait: bool = True
    # Opt-in self-development: run a Maintainer on a worktree of Iron Jarvis's
    # OWN source (gated by config.self_dev_enabled; review-gated, never auto-merge).
    self_dev: bool = False


class ContinueBody(BaseModel):
    message: str
    wait: bool = True


class UploadBody(BaseModel):
    filename: str
    content_b64: str


class SettingsBody(BaseModel):
    values: dict[str, Any]


class RepairBody(BaseModel):
    action: str  # db_integrity | db_vacuum | prune_events | backup_now | recheck
    older_than_days: int = 30


#: Whitelist of config keys the Settings UI may read/write (safe, restart-light).
_SETTINGS_KEYS = [
    "default_provider",
    "default_model",
    "max_agent_steps",
    "git_native",
    "self_dev_enabled",
    "self_dev_root",
    "sandbox_runtime",
    "ollama_base_url",
    "ollama_model",
    "event_retention_days",
    # Motivation Layer (the pulse) — all OFF / conservative by default. Toggling
    # autonomy_enabled at runtime is honoured by the manual /autonomy/tick + the
    # endpoints; the background loop is (re)armed on the next daemon restart.
    "autonomy_enabled",
    "autonomy_level",
    "autonomy_dry_run",
    "autonomy_kill_switch",
    "autonomy_tick_seconds",
    "autonomy_max_actions_per_day",
    "autonomy_max_tokens_per_day",
    # Sentinels (always-on watchers) — OFF by default. Toggling sentinels_enabled
    # at runtime is honoured by the manual /sentinels/poll; the background polling
    # loop is (re)armed on the next daemon restart (mirrors autonomy_enabled).
    "sentinels_enabled",
    "sentinels_tick_seconds",
]


class ConnectionKeyBody(BaseModel):
    key: str


class OAuthCompleteBody(BaseModel):
    """Manual-code OAuth completion: the pasted code may embed state (code#state)."""

    code: str
    state: str = ""


def _graceful_stop() -> None:  # pragma: no cover — exercised via monkeypatch
    """Ask uvicorn to exit cleanly: SIGTERM -> lifespan shutdown -> exit 0.

    ``raise_signal`` triggers uvicorn's own signal handler (installed by
    ``uvicorn.run``) so open requests drain and the lifespan shutdown runs —
    the same path as Ctrl+C. Falls back to a hard exit if signaling fails.
    """
    import signal as _signal

    try:
        _signal.raise_signal(_signal.SIGTERM)
    except Exception:
        os._exit(0)


class ComputerUseEnable(BaseModel):
    enabled: bool = False
    domain_allowlist: list[str] | None = None
    action_allowlist: list[str] | None = None


class TerminalCreate(BaseModel):
    cwd: str | None = None
    shell: str | None = None
    cols: int = 80
    rows: int = 24


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


class FeedbackBody(BaseModel):
    rating: str = "up"  # up | down | neutral
    comment: str = ""


class DocWriteBody(BaseModel):
    path: str
    content: str
    kind: str | None = None


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


class SentinelAdd(BaseModel):
    name: str
    path: str
    glob: str | None = None
    task: str = ""
    kind: str = "file"
    agent_type: str = "builder"
    risk: str = "low"  # low | med


class TemplateCreateBody(BaseModel):
    name: str
    task: str
    agent_type: str = "builder"
    provider: str | None = None
    model: str | None = None


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


class CustomToolCreate(BaseModel):
    name: str
    description: str = ""
    parameters: list[dict] = []
    command: list[str] = []
    timeout_seconds: int = 60


class WebhookCreate(BaseModel):
    slug: str
    direction: str = "inbound"  # inbound | outbound
    target_url: str = ""
    event_types: list[str] = []
    secret_name: str = ""


class SpawnBody(BaseModel):
    task: str


class UpdateBody(BaseModel):
    # Whether to rebuild the dashboard (pnpm install && pnpm build) after pulling.
    build_dashboard: bool = True


# --- Motivation Layer (the pulse) -------------------------------------------


class GoalBody(BaseModel):
    text: str
    category: str = "general"
    priority: int = 3
    autonomy_level: str = "suggest"  # suggest | act_low | act_all
    source: str = "user"


class GoalPatch(BaseModel):
    text: str | None = None
    category: str | None = None
    priority: int | None = None
    autonomy_level: str | None = None  # the per-goal dial
    status: str | None = None  # active | paused | done | abandoned
    action_budget: int | None = None
    spend_budget: int | None = None
    actions_taken: int | None = None  # set to 0 to reset the rolling counter
    tokens_spent: int | None = None


class KillBody(BaseModel):
    enabled: bool = True  # engage (True) or release (False) the global kill switch


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
        "input_tokens": getattr(session, "input_tokens", 0),
        "output_tokens": getattr(session, "output_tokens", 0),
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
    # Health of the background loops (auto-backup/autonomy/sentinel/inbound), so a
    # silent failure (e.g. backups failing) is visible in /diagnostics, not just
    # buried in the log. Keyed by loop name.
    loop_health: dict[str, dict[str, Any]] = {}
    # Wire the executor into the Motivation Layer so an auto-approved (or
    # human-approved) proposal can become a real session. The engine is safe
    # with this unset; setting it does NOT enable autonomy (that's config-gated).
    if platform.intent is not None:
        platform.intent.orchestrator = orchestrator
    # Two-way comm: the inbound poller. Constructed always (cheap), but it only
    # does anything when a channel has inbound_enabled + credentials; the loop
    # below is created ONLY when poller.enabled() — off-by-default, no network.
    from ..comm import InboundPoller

    inbound_poller = InboundPoller(
        platform.notifier,
        orchestrator,
        platform.engine,
        event_bus=platform.event_bus,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:  # start the cron scheduler when the daemon boots
            platform.scheduler.start()
        except Exception:  # pragma: no cover - never block boot
            pass
        # Restart survival. Each step is INDEPENDENT: a failure in one (e.g. a
        # review rehydrate tripping on a bad worktree) must NOT skip the others —
        # previously a single try-block meant a session/review failure silently
        # left every inbound webhook un-armed until the next restart, with no
        # signal. Record each in loop_health so a silent skip is visible in
        # /diagnostics.
        def _rehydrate_step(name, fn):
            try:
                fn()
                loop_health[name] = {"ok": True}
            except Exception as exc:  # noqa: BLE001 - never block boot
                loop_health[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                log.exception("boot rehydration step %s failed", name)

        _rehydrate_step("reconcile_sessions", orchestrator.reconcile_interrupted_sessions)
        _rehydrate_step("rehydrate_reviews", orchestrator.rehydrate_reviews)
        if platform.intent is not None:  # reset proposals stranded 'executing' by a crash
            _rehydrate_step("reconcile_proposals", platform.intent.reconcile_executing_proposals)

        def _make_webhook_handler(slug):
            async def _handler(body, _slug=slug):
                await platform.event_bus.publish(
                    "webhook.received", {"slug": _slug, "body": body}
                )
                return {"ok": True}

            return _handler

        _rehydrate_step(
            "rehydrate_webhooks",
            lambda: platform.inbound_webhooks.rehydrate(_make_webhook_handler),
        )
        try:  # GC worktrees orphaned by a prior restart (failed/missing sessions)
            orchestrator.prune_orphan_worktrees()
        except Exception:  # pragma: no cover - never block boot
            pass
        try:  # event-log retention sweep (config.event_retention_days > 0)
            days = int(getattr(platform.config, "event_retention_days", 0) or 0)
            if days > 0:
                from ..core.db import prune_events

                pruned = prune_events(platform.engine, days)
                if pruned:
                    # Surface it — the 90-day default means the first boot after an
                    # upgrade from keep-forever prunes old trace history; don't do it
                    # silently. Set event_retention_days=0 in config.toml to disable.
                    log.warning(
                        "event-log retention: pruned %d event(s) older than %d days "
                        "(set event_retention_days=0 to keep forever)",
                        pruned, days,
                    )
        except Exception:  # pragma: no cover - never block boot
            pass
        # Periodic auto-backup safety net — a daily driver shouldn't depend on the
        # user remembering to run `ironjarvis backup`. Disable with
        # IRONJARVIS_AUTO_BACKUP=off; tune via *_HOURS (default 24) / *_KEEP (7).
        backup_task = None
        if (os.environ.get("IRONJARVIS_AUTO_BACKUP", "on").strip().lower()
                not in {"0", "false", "no", "off"}):

            async def _auto_backup_loop() -> None:
                from ..core.ids import utcnow
                from ..maintenance import run_auto_backup

                try:
                    hours = float(os.environ.get("IRONJARVIS_AUTO_BACKUP_HOURS", "24"))
                except ValueError:
                    hours = 24.0
                try:
                    keep = int(os.environ.get("IRONJARVIS_AUTO_BACKUP_KEEP", "7"))
                except ValueError:
                    keep = 7
                interval = max(3600.0, hours * 3600.0)
                await asyncio.sleep(60)  # don't slow boot; first snapshot ~1 min in
                while True:
                    try:
                        await asyncio.to_thread(
                            run_auto_backup,
                            platform.config.home,
                            engine=platform.engine,
                            keep=keep,
                        )
                        log.info("auto-backup written (keep=%d)", keep)
                        loop_health["auto_backup"] = {
                            "ok": True, "last_success_at": utcnow().isoformat()
                        }
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 - never kill the daemon
                        log.exception("auto-backup failed")
                        loop_health["auto_backup"] = {
                            "ok": False,
                            "last_error": f"{type(exc).__name__}: {exc}"[:300],
                            "at": utcnow().isoformat(),
                        }
                    await asyncio.sleep(interval)

            backup_task = asyncio.create_task(_auto_backup_loop())

        # Motivation Layer deliberation tick — the pulse. GUARDED by
        # config.autonomy_enabled (OFF by default), so by default + in tests the
        # loop is never created and nothing self-initiates. Mirrors the auto-backup
        # loop: sleeps before the first tick (never blocks boot) and is cancelled
        # on shutdown. Disable explicitly via IRONJARVIS_AUTONOMY=off.
        autonomy_task = None
        if (
            getattr(platform.config, "autonomy_enabled", False)
            and platform.intent is not None
            and os.environ.get("IRONJARVIS_AUTONOMY", "on").strip().lower()
            not in {"0", "false", "no", "off"}
        ):

            async def _autonomy_loop() -> None:
                try:
                    interval = max(60, int(platform.config.autonomy_tick_seconds))
                except (TypeError, ValueError):
                    interval = 900
                await asyncio.sleep(30)  # let boot settle before the first pulse
                while True:
                    try:
                        await platform.intent.deliberate()
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001 - a tick must never kill the daemon
                        log.exception("autonomy deliberation tick failed")
                    await asyncio.sleep(interval)

            autonomy_task = asyncio.create_task(_autonomy_loop())

        # Sentinels ("always-on watchers") polling loop. GUARDED by
        # config.sentinels_enabled (OFF by default), so by default + in tests the
        # loop is never created and nothing is polled. Mirrors the autonomy loop:
        # rehydrates the durable registry, sleeps before the first poll (never
        # blocks boot), and is cancelled on shutdown. Each poll diffs every enabled
        # sentinel and mints SUGGEST-ONLY proposals — never a session. Disable
        # explicitly via IRONJARVIS_SENTINELS=off.
        sentinel_task = None
        if (
            getattr(platform.config, "sentinels_enabled", False)
            and platform.sentinels is not None
            and platform.intent is not None
            and os.environ.get("IRONJARVIS_SENTINELS", "on").strip().lower()
            not in {"0", "false", "no", "off"}
        ):
            try:  # restart survival: rehydrate seen-state (never re-fires)
                platform.sentinels.load()
            except Exception:  # pragma: no cover - never block boot
                pass

            async def _sentinel_loop() -> None:
                try:
                    interval = max(15, int(platform.config.sentinels_tick_seconds))
                except (TypeError, ValueError):
                    interval = 300
                await asyncio.sleep(30)  # let boot settle before the first poll
                while True:
                    try:
                        await asyncio.to_thread(
                            platform.sentinels.poll_once, platform.intent
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001 - a poll must never kill the daemon
                        log.exception("sentinel poll failed")
                    await asyncio.sleep(interval)

            sentinel_task = asyncio.create_task(_sentinel_loop())

        # Two-way comm inbound poller — the receive leg. GUARDED by
        # poller.enabled() (True only when a channel has inbound_enabled +
        # credentials), so by default + in tests the loop is NEVER created and no
        # network happens. Mirrors the loops above: sleeps before the first poll
        # (never blocks boot) and is cancelled on shutdown. Disable explicitly via
        # IRONJARVIS_INBOUND=off.
        inbound_task = None
        if (
            inbound_poller.enabled()
            and os.environ.get("IRONJARVIS_INBOUND", "on").strip().lower()
            not in {"0", "false", "no", "off"}
        ):

            async def _inbound_loop() -> None:
                # 15s default (was 3s): a 3s short-poll is ~28,800 round-trips/day that
                # keep a laptop's event loop from idling; 15s stays responsive for an
                # inbound message while cutting idle wakeups ~5x. Override for faster.
                try:
                    interval = max(
                        1, int(os.environ.get("IRONJARVIS_INBOUND_INTERVAL", "15"))
                    )
                except ValueError:
                    interval = 15
                await asyncio.sleep(20)  # let boot settle before the first poll
                while True:
                    try:
                        await inbound_poller.poll_once()
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001 - a poll must never kill the daemon
                        log.exception("inbound comm poll failed")
                    await asyncio.sleep(interval)

            inbound_task = asyncio.create_task(_inbound_loop())
        try:
            yield
        finally:
            if inbound_task is not None:
                inbound_task.cancel()
            if sentinel_task is not None:
                sentinel_task.cancel()
            if autonomy_task is not None:
                autonomy_task.cancel()
            if backup_task is not None:
                backup_task.cancel()
            try:
                platform.scheduler.shutdown()
            except Exception:  # pragma: no cover
                pass
            try:
                platform.terminals.kill_all()
            except Exception:  # pragma: no cover
                pass
            try:  # close any launched computer-use browser (Chromium + driver)
                br = getattr(platform.computeruse, "browser", None)
                if br is not None and hasattr(br, "aclose"):
                    await br.aclose()
            except Exception:  # pragma: no cover
                pass

    app = FastAPI(title="Iron Jarvis", version=__version__, lifespan=lifespan)
    # Optional bearer-token auth (env IRONJARVIS_TOKEN) — required for a public
    # deployment; no-op locally.
    from .auth import BodyLimitMiddleware, HostOriginGuardMiddleware, TokenAuthMiddleware

    app.add_middleware(TokenAuthMiddleware)  # inner: token check
    # CORS: default to loopback dashboard origins ONLY (never wildcard, since the
    # daemon is RCE-by-design); a public deployment sets IRONJARVIS_CORS_ORIGINS.
    _origins = os.environ.get("IRONJARVIS_CORS_ORIGINS", "").strip()
    # PATCH is required for the autonomy goal controls (PATCH /goals/{id} — the
    # per-goal dial + pause/activate). Without it the browser preflight fails and
    # the call surfaces as a misleading "daemon offline".
    _methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    if _origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[o.strip() for o in _origins.split(",") if o.strip()],
            allow_methods=_methods,
            allow_headers=["*"],
        )
    else:
        # A browser can only present a loopback Origin from a locally-served page,
        # so any loopback origin may read responses; evil.com cannot.
        app.add_middleware(
            CORSMiddleware,
            allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
            allow_methods=_methods,
            allow_headers=["*"],
        )
    # Reject an oversized request body (413) before it is buffered — DoS guard.
    app.add_middleware(BodyLimitMiddleware)
    # OUTERMOST (added last): reject non-loopback Host (DNS rebinding) + untrusted
    # cross-origin browser requests (drive-by RCE) before anything — covers WS.
    app.add_middleware(HostOriginGuardMiddleware)

    # Exception handling: an endpoint that raises an UNHANDLED error should return
    # a clean, actionable message — input/parse errors as 400, everything else as a
    # logged 500 — instead of an opaque "Internal Server Error". The input-error
    # types are registered as SPECIFIC handlers so they're served by Starlette's
    # ExceptionMiddleware WITHOUT an ERROR-level "Exception in ASGI application"
    # traceback (a routine bad-TOML/unknown-name 400 shouldn't spam the log); only
    # genuinely-unexpected exceptions hit the Exception handler + log.exception.
    import json as _json
    import tomllib as _tomllib

    from fastapi.responses import JSONResponse

    async def _input_error(request: Request, exc: Exception):  # noqa: ANN202
        return JSONResponse(status_code=400, content={"detail": f"{type(exc).__name__}: {exc}"})

    for _exc_type in (ValueError, KeyError, _tomllib.TOMLDecodeError, _json.JSONDecodeError):
        app.add_exception_handler(_exc_type, _input_error)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):  # noqa: ANN202
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": f"internal error: {type(exc).__name__}: {exc}"},
        )

    app.state.platform = platform
    app.state.orchestrator = orchestrator
    # Background session tasks are registered on the orchestrator keyed by
    # session_id (a strong ref preventing premature GC, and the handle the
    # cancel endpoint uses). Exceptions are surfaced (logged), not swallowed.
    def _spawn_bg(session_id: str, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        orchestrator.register_running(session_id, task)

        def _done(t: asyncio.Task) -> None:
            orchestrator._running.pop(session_id, None)
            try:
                t.result()
            except asyncio.CancelledError:  # pragma: no cover - expected on cancel
                pass
            except Exception:  # noqa: BLE001
                log.exception("background session %s failed", session_id)

        task.add_done_callback(_done)
        return task

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
        try:
            session = await orchestrator.create_session(
                body.task,
                _agent_type(body.agent_type),
                body.provider,
                model=body.model,
                self_dev=body.self_dev,
            )
        except (PermissionError, RuntimeError) as exc:  # self-dev gating
            raise HTTPException(status_code=400, detail=str(exc))
        if body.wait:
            session = await orchestrator.run_session(session.id)
        else:
            _spawn_bg(session.id, orchestrator.run_session(session.id))
        return _session_view(session)

    @app.post("/sessions/{session_id}/cancel")
    def cancel_session(session_id: str) -> dict[str, Any]:
        try:
            session = orchestrator.cancel_session(session_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="session not found")
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return _session_view(session)

    @app.post("/sessions/{session_id}/rerun")
    async def rerun_session(session_id: str, wait: bool = True) -> dict[str, Any]:
        try:
            session = await orchestrator.rerun_session(session_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="session not found")
        except (PermissionError, RuntimeError) as exc:  # self-dev gating on a maintainer rerun
            raise HTTPException(status_code=400, detail=str(exc))
        if wait:
            session = await orchestrator.run_session(session.id)
        else:
            _spawn_bg(session.id, orchestrator.run_session(session.id))
        return _session_view(session)

    @app.post("/sessions/{session_id}/continue")
    async def continue_session(session_id: str, body: ContinueBody) -> dict[str, Any]:
        try:
            session = await orchestrator.continue_session(session_id, body.message)
        except KeyError:
            raise HTTPException(status_code=404, detail="session not found")
        if body.wait:
            session = await orchestrator.run_session(session.id)
        else:
            _spawn_bg(session.id, orchestrator.run_session(session.id))
        return _session_view(session)

    @app.delete("/sessions/{session_id}")
    def delete_session(session_id: str) -> dict[str, Any]:
        try:
            orchestrator.delete_session(session_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="session not found")
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"deleted": session_id}

    @app.get("/sessions/{session_id}/export")
    def export_session(session_id: str, format: str = "md"):
        session = orchestrator.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        transcript = orchestrator.transcript(session_id)
        try:
            ev = platform.evaluator.latest(session_id)
        except Exception:  # noqa: BLE001
            ev = None
        view = _session_view(session)
        if format == "json":
            return {
                "session": view,
                "transcript": transcript,
                "evaluation": ev.model_dump() if ev is not None else None,
            }
        from fastapi.responses import PlainTextResponse

        lines = [
            f"# Iron Jarvis session — {session.task}",
            "",
            f"- id: {session.id}",
            f"- status: {session.status.value}",
            f"- provider/model: {session.provider} / {session.model}",
            f"- created: {session.created_at}",
            f"- finished: {session.finished_at}",
            "",
            "## Summary",
            session.summary or "(none)",
            "",
            "## Tool calls",
        ]
        for t in transcript.get("tools", []):
            lines.append(
                f"- `{t.get('tool', '')}` ({t.get('verdict', '')}) ok={t.get('ok')}: "
                f"{(t.get('output') or '')[:200]}"
            )
        if ev is not None:
            lines += [
                "",
                "## Evaluation",
                "```json",
                json.dumps(ev.model_dump(), indent=2, default=str),
                "```",
            ]
        return PlainTextResponse("\n".join(lines), media_type="text/markdown")

    @app.get("/sessions")
    def list_sessions(limit: int = 200) -> dict[str, Any]:
        # Bounded window (default 200 most-recent) so the polled list stays cheap as
        # sessions accumulate over weeks; clients page for more via ?limit=.
        lim = None if limit <= 0 else limit
        return {"sessions": [_session_view(s) for s in orchestrator.list_sessions(limit=lim)]}

    @app.get("/sessions/{session_id}")
    def get_session(session_id: str) -> dict[str, Any]:
        session = orchestrator.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        return {
            "session": _session_view(session),
            "transcript": orchestrator.transcript(session_id),
        }

    @app.get("/blackboard/{board_id}")
    def blackboard(board_id: str) -> dict[str, Any]:
        """Read a department's shared blackboard (notes + messages) for the UI."""
        from ..blackboard.tools import _to_view

        store = platform.blackboard
        if store is None:
            return {"board_id": board_id, "records": []}
        records = store.list(board_id)
        return {"board_id": board_id, "records": _to_view(records)}

    @app.get("/self-dev")
    def self_dev_status() -> dict[str, Any]:
        """Whether agents may edit Iron Jarvis's own source (opt-in, review-gated)."""
        from ..core.self_dev import self_dev_status as _status

        return _status(platform.config)

    # --- Repo-based self-update (git pull + uv sync + pnpm build) ---------

    @app.get("/update/check")
    def update_check() -> dict[str, Any]:
        """Is a newer commit available on this checkout's upstream branch?"""
        from ..core.self_dev import iron_jarvis_repo_root
        from ..core.updates import update_status

        repo = iron_jarvis_repo_root(platform.config)
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
        from ..core.self_dev import iron_jarvis_repo_root
        from ..core.updates import apply_update

        repo = iron_jarvis_repo_root(platform.config)
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
        return {"pruned": orchestrator.prune_orphan_worktrees(include_completed=all)}

    @app.post("/documents/upload")
    def documents_upload(body: UploadBody) -> dict[str, Any]:
        """Accept a base64 file and store it under <home>/uploads (no multipart dep)."""
        import base64
        import re

        # Cap the decoded size so a giant upload can't OOM-kill the whole daemon
        # (which would take down every session/terminal with it). 4/3 accounts for
        # base64 expansion; reject BEFORE decoding so we never buffer the bytes.
        approx_bytes = (len(body.content_b64) * 3) // 4
        if approx_bytes > _MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"upload too large (~{approx_bytes // (1024 * 1024)} MB); "
                    f"limit is {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB"
                ),
            )
        name = re.sub(r"[^A-Za-z0-9._-]", "_", body.filename).strip("._") or "upload"
        uploads = platform.config.home / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        target = uploads / name
        try:
            data = base64.b64decode(body.content_b64, validate=False)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"invalid base64: {exc}")
        target.write_bytes(data)
        return {"path": str(target), "name": name, "bytes": len(data)}

    @app.get("/settings")
    def get_settings() -> dict[str, Any]:
        cfg = platform.config
        return {"settings": {k: getattr(cfg, k, None) for k in _SETTINGS_KEYS}}

    @app.put("/settings")
    def put_settings(body: SettingsBody) -> dict[str, Any]:
        cfg = platform.config
        candidates = {k: v for k, v in body.values.items() if k in _SETTINGS_KEYS}
        # Validate ALL keys on a throwaway copy first, so one bad value can't
        # partially mutate (and then persist) the live config — which previously
        # could brick the next boot or break in-flight sessions.
        trial = cfg.model_copy(deep=True)
        for key, value in candidates.items():
            try:
                setattr(trial, key, value)
            except Exception:  # noqa: BLE001 - pydantic validation
                raise HTTPException(status_code=400, detail=f"invalid value for {key}")
        # Everything validated — commit to the running config.
        updated: list[str] = []
        for key, value in candidates.items():
            setattr(cfg, key, value)
            updated.append(key)
        # Persist atomically (temp + os.replace) so a crash mid-write can't leave a
        # torn config.toml that aborts the next boot.
        persist_config_values(cfg.home, {k: getattr(cfg, k, None) for k in updated})
        return {
            "settings": {k: getattr(cfg, k, None) for k in _SETTINGS_KEYS},
            "updated": updated,
        }

    @app.get("/diagnostics")
    def diagnostics() -> dict[str, Any]:
        """Read-only health of the running state (never raises)."""
        from sqlalchemy import text

        cfg = platform.config
        out: dict[str, Any] = {}
        try:
            with platform.engine.connect() as conn:
                # Cheap liveness probe only — a full PRAGMA integrity_check is a
                # whole-DB page scan (hundreds of ms on a large DB) and this endpoint
                # is polled ~every 15s app-wide (NotificationBell). Deep integrity is
                # on-demand via POST /diagnostics/repair {db_integrity}.
                conn.execute(text("SELECT 1")).scalar()
            out["db_integrity"] = "ok"
        except Exception as exc:  # noqa: BLE001
            out["db_integrity"] = f"error: {exc}"
        try:
            db_path = cfg.db_path
            out["db_bytes"] = db_path.stat().st_size if db_path.exists() else 0
            wal = Path(str(db_path) + "-wal")
            out["wal_bytes"] = wal.stat().st_size if wal.exists() else 0
        except Exception:  # noqa: BLE001
            pass
        out["secrets_key_present"] = (cfg.home / "secrets" / ".secrets.key").exists()
        # Real decryptability check (not mere file existence): catches a lost /
        # mismatched key (e.g. a key-less restore) that would silently break every
        # stored credential while still reading as "present".
        try:
            out["secrets_key_valid"] = platform.secrets.key_valid()
        except Exception:  # noqa: BLE001 — diagnostics must never raise
            out["secrets_key_valid"] = False
        out["running_sessions"] = len(orchestrator._running)
        out["pending_reviews"] = len(orchestrator._reviews)
        out["background_loops"] = dict(loop_health)  # silent-failure visibility
        out["tracked_worktrees"] = len(orchestrator._git_sessions)
        try:
            out["providers"] = platform.providers.health()
        except Exception:  # noqa: BLE001
            out["providers"] = []
        return out

    @app.post("/diagnostics/repair")
    def diagnostics_repair(body: RepairBody) -> dict[str, Any]:
        """Gated, idempotent, in-app remediation — let the app FIX (not just report)
        the common infrastructure problems a daily driver hits, without dropping to
        a shell. Each action is logged and safe to re-run."""
        from sqlalchemy import text

        action = body.action
        if action == "db_integrity":
            with platform.engine.connect() as conn:
                res = conn.execute(text("PRAGMA integrity_check")).scalar()
            return {"action": action, "ok": res == "ok", "result": res}
        if action == "db_vacuum":
            # Standalone VACUUM (compact/defragment) — run outside a transaction
            # via the raw DBAPI connection in autocommit, as the offline CLI does.
            raw = platform.engine.raw_connection()
            try:
                dbapi = getattr(raw, "dbapi_connection", None) or raw.connection
                old_iso = dbapi.isolation_level
                dbapi.isolation_level = None  # VACUUM cannot run inside a transaction
                dbapi.execute("VACUUM")
                dbapi.isolation_level = old_iso
            finally:
                raw.close()
            return {"action": action, "ok": True, "result": "vacuumed"}
        if action == "prune_events":
            from ..core.db import prune_events

            n = prune_events(platform.engine, body.older_than_days, vacuum=True)
            return {"action": action, "ok": True, "result": f"pruned {n} event(s) + vacuumed"}
        if action == "backup_now":
            from ..maintenance import run_auto_backup

            p = run_auto_backup(platform.config.home, engine=platform.engine)
            return {"action": action, "ok": True, "result": str(p)}
        if action == "recheck":
            from ..onboarding import doctor as _doctor

            return {"action": action, "ok": True, "result": _doctor(platform)}
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown repair action '{action}' "
                "(db_integrity | db_vacuum | prune_events | backup_now | recheck)"
            ),
        )

    # --- Observability + Evaluation (§29, §30) ----------------------------

    @app.get("/metrics")
    def metrics() -> dict[str, Any]:
        return platform.observability.metrics()

    @app.get("/usage")
    def usage(days: int = 30) -> dict[str, Any]:
        """Token + $ cost over time (totals, by-day, by-model) from agent runs."""
        return platform.observability.usage_summary(days)

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

    # --- Self-correcting learning loop ------------------------------------

    @app.post("/sessions/{session_id}/feedback")
    def session_feedback(session_id: str, body: FeedbackBody) -> dict[str, Any]:
        fb = platform.learning.record_feedback(session_id, body.rating, body.comment)
        return {"id": fb.id, "rating": fb.rating}

    @app.get("/lessons")
    def lessons(scope: str | None = "user", limit: int = 20) -> dict[str, Any]:
        return {
            "lessons": [
                lr.model_dump() for lr in platform.learning.lessons(scope=scope, limit=limit)
            ]
        }

    # --- ImprovementEngine: outcomes feed back into lessons + proposals ----

    @app.get("/improvement")
    def improvement_stats() -> dict[str, Any]:
        """Per-lesson + per-agent outcome stats and quality trend."""
        if platform.improvement is None:
            raise HTTPException(status_code=503, detail="improvement engine unavailable")
        return platform.improvement.stats()

    @app.post("/improvement/reflect")
    async def improvement_reflect(limit: int = 5) -> dict[str, Any]:
        """Run model reflection over recent low-scoring sessions (on-demand).

        Returns structured suggestions; applies NOTHING (no prompt/lesson/source
        edits). Safe + deterministic offline via the mock model + heuristic fallback.
        """
        if platform.improvement is None:
            raise HTTPException(status_code=503, detail="improvement engine unavailable")
        return await platform.improvement.reflect(limit=limit)

    # --- Documents (all file types) ---------------------------------------

    @app.get("/documents/read")
    def documents_read(path: str) -> dict[str, Any]:
        from ..documents import extract_text

        ok, reason = fs_read_ok(path)
        if not ok:
            raise HTTPException(status_code=403, detail=reason)
        try:
            text = extract_text(path)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"cannot read: {exc}")
        return {"path": path, "text": text[:20000]}

    @app.post("/documents/write")
    def documents_write(body: DocWriteBody) -> dict[str, Any]:
        from ..documents import write_document

        base = (platform.config.home / "documents").resolve()
        target = (base / body.path).resolve()
        if target != base and not target.is_relative_to(base):
            raise HTTPException(status_code=400, detail="path escapes documents dir")
        out = write_document(target, body.content, kind=body.kind)
        return {
            "path": str(out.relative_to(base)).replace("\\", "/"),
            "bytes": out.stat().st_size,
        }

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

    # --- LLM Connections (API key + OAuth2/PKCE) --------------------------

    @app.get("/connections")
    def connections() -> dict[str, Any]:
        return {"connections": platform.connections.status()}

    #: A sane default model per provider, used when auto-promoting the FIRST real
    #: connection away from the out-of-box "mock" default (see _maybe_autopromote).
    _PROMOTE_DEFAULT_MODEL = {
        "anthropic": "claude-opus-4-8",
        "openai": "gpt-4o-mini",
        "google": "gemini-1.5-flash",
        "xai": "grok-2-latest",
    }

    def _maybe_autopromote_default(provider: str) -> bool:
        """If the default provider is still the offline "mock" when the first REAL
        provider connects, promote that provider (+ a matching default model) so a
        "Default" session uses a real model instead of silently faking output.
        Returns True if it promoted."""
        cfg = platform.config
        if provider == "mock" or cfg.default_provider != "mock":
            return False
        cfg.default_provider = provider
        cfg.default_model = _PROMOTE_DEFAULT_MODEL.get(provider, cfg.default_model)
        _persist_config(["default_provider", "default_model"])
        return True

    @app.post("/connections/{provider}/key")
    def connect_key(provider: str, body: ConnectionKeyBody) -> dict[str, Any]:
        try:
            rec = platform.connections.set_api_key(provider, body.key)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        promoted = _maybe_autopromote_default(rec.provider)
        return {"provider": rec.provider, "status": rec.status, "promoted_default": promoted}

    @app.post("/connections/{provider}/test")
    async def connect_test(provider: str) -> dict[str, Any]:
        # test() may do a real network probe (when wired) → run it off the event
        # loop so a slow provider can't stall the daemon.
        return await asyncio.to_thread(platform.connections.test, provider)

    @app.delete("/connections/{provider}")
    def connect_disconnect(provider: str) -> dict[str, Any]:
        platform.connections.disconnect(provider)
        return {"provider": provider, "status": "disconnected"}

    # One live loopback listener per provider (see connections/loopback.py) —
    # restarted on every new flow, self-expiring on TTL.
    _loopback_servers: dict[str, Any] = {}

    @app.get("/oauth/{provider}/start")
    def oauth_start(provider: str) -> dict[str, Any]:
        try:
            out = platform.connections.start_oauth(provider)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        # RFC 8252 loopback: embedded public clients registered against a FIXED
        # localhost port (OpenAI's :1455) need a one-shot listener to catch the
        # redirect — it completes the flow server-side, then shuts down.
        loop = platform.connections.loopback_redirect(provider)
        if loop:
            from ..connections.loopback import OAuthLoopbackServer

            port, cb_path = loop
            old = _loopback_servers.pop(provider, None)
            if old:
                old.stop()

            def _complete(code: str, state: str, _p: str = provider) -> None:
                platform.connections.complete_oauth(_p, code=code, state=state)
                _maybe_autopromote_default(_p)

            srv = OAuthLoopbackServer(
                port=port, path=cb_path, provider=provider, on_code=_complete
            )
            try:
                srv.start()
            except OSError:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"port {port} is busy (another app — e.g. Codex CLI — is "
                        "using it). Close it and try again."
                    ),
                )
            _loopback_servers[provider] = srv
        return out

    @app.post("/oauth/{provider}/complete")
    def oauth_complete(provider: str, body: OAuthCompleteBody) -> dict[str, Any]:
        """Manual-code OAuth completion (e.g. Anthropic's paste-the-code flow).

        The provider showed the user an authorization code (``code#state``);
        the Connections page posts it here instead of a browser redirect ever
        reaching the daemon.
        """
        try:
            rec = platform.connections.complete_oauth(
                provider, code=body.code, state=body.state
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        promoted = _maybe_autopromote_default(provider)
        return {
            "provider": rec.provider,
            "status": rec.status,
            "promoted_default": promoted,
        }

    @app.get("/oauth/{provider}/callback")
    def oauth_callback(provider: str, code: str = "", state: str = "") -> HTMLResponse:
        try:
            platform.connections.complete_oauth(provider, code=code, state=state)
            _maybe_autopromote_default(provider)
            msg, ok = f"Connected to {provider}. You can close this window.", True
        except Exception as exc:  # noqa: BLE001
            msg, ok = f"Connection failed: {exc}", False
        color = "#22d3ee" if ok else "#fb7185"
        # SECURITY: this route is auth-exempt and `provider`/exception text are
        # attacker-influenced — a reflected-XSS sink. Escape every interpolated
        # value and build the postMessage payload as a JS-safe string literal.
        safe_msg = _html.escape(msg)
        payload = json.dumps(
            {"type": "ironjarvis-oauth", "provider": provider, "ok": ok}
        ).replace("<", "\\u003c")
        html = (
            "<!doctype html><meta charset=utf-8><title>Iron Jarvis</title>"
            "<body style='background:#0a0a0f;color:#e5e7eb;font-family:system-ui;"
            "display:grid;place-items:center;height:100vh;margin:0'>"
            f"<div style='text-align:center'><div style='font-size:42px;color:{color}'>"
            f"{'✓' if ok else '✕'}</div><p>{safe_msg}</p></div>"
            "<script>try{window.opener&&window.opener.postMessage("
            f"JSON.parse({json.dumps(payload)}),'*');"
            "setTimeout(()=>window.close(),1200)}catch(e){}</script></body>"
        )
        return HTMLResponse(
            html,
            headers={
                "Content-Security-Policy": (
                    "default-src 'none'; script-src 'unsafe-inline'; "
                    "style-src 'unsafe-inline'"
                ),
                "X-Content-Type-Options": "nosniff",
            },
        )

    # --- Graceful shutdown (desktop Quit) ----------------------------------

    @app.post("/shutdown")
    def shutdown_daemon() -> dict[str, Any]:
        """Gracefully stop the daemon — used by the desktop app on Quit.

        Token-guarded like every other route. The response returns FIRST (the
        Timer defers the signal) so the caller sees the ack instead of a reset
        connection; the desktop app then waits for process exit and only
        force-kills as a fallback.
        """
        import threading as _threading

        _threading.Timer(0.2, _graceful_stop).start()
        return {"ok": True, "detail": "daemon shutting down"}

    # --- Onboarding / first-run / doctor ----------------------------------

    @app.get("/onboarding")
    def onboarding() -> dict[str, Any]:
        from ..onboarding import readiness

        return readiness(platform)

    @app.get("/doctor")
    def doctor_ep() -> dict[str, Any]:
        from ..onboarding import doctor

        # Pass the live platform so doctor also runs RUNTIME checks (model
        # connected, secrets key valid, DB integrity) — the failures a daily
        # driver actually hits, not just machine prerequisites.
        return doctor(platform)

    # --- Computer use (opt-in; gated by allowlists + human approval) ------

    def _cu_status() -> dict[str, Any]:
        p = platform.computeruse.policy
        return {
            "enabled": p.enabled,
            "domain_allowlist": list(p.domain_allowlist),
            "action_allowlist": list(p.action_allowlist),
            "isolation": getattr(p, "isolation", "isolated"),
            "max_steps": p.max_steps,
            "max_retries": p.max_retries,
            "pending_approvals": len(platform.computeruse.approvals.pending()),
        }

    @app.get("/computeruse")
    def computeruse_status() -> dict[str, Any]:
        return _cu_status()

    @app.post("/computeruse/enable")
    def computeruse_enable(body: ComputerUseEnable) -> dict[str, Any]:
        from ..computeruse import ComputerUsePolicy, PlaywrightBrowser

        cu = platform.computeruse
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
        return _cu_status()

    @app.get("/computeruse/approvals")
    def computeruse_approvals() -> dict[str, Any]:
        return {
            "approvals": [a.model_dump() for a in platform.computeruse.approvals.pending()]
        }

    @app.post("/computeruse/approvals/{approval_id}/approve")
    def computeruse_approve(approval_id: str) -> dict[str, Any]:
        # 404 on an unknown/stale id instead of faking success — for a
        # human-gated capability, a "approved" reply that recorded nothing is a
        # trust lie (mirrors every other id-based mutation).
        if platform.computeruse.approvals.approve(approval_id) is None:
            raise HTTPException(status_code=404, detail="no such approval")
        return {"id": approval_id, "status": "approved"}

    @app.post("/computeruse/approvals/{approval_id}/deny")
    def computeruse_deny(approval_id: str) -> dict[str, Any]:
        if platform.computeruse.approvals.deny(approval_id) is None:
            raise HTTPException(status_code=404, detail="no such approval")
        return {"id": approval_id, "status": "denied"}

    @app.get("/computeruse/runs/{run_id}")
    def computeruse_run(run_id: str) -> dict[str, Any]:
        from ..computeruse.models import ComputerUseRun

        with session_scope(platform.engine) as db:
            run = db.get(ComputerUseRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="no such run")
        return run.model_dump()

    # --- Terminals (multiple live shell sessions) -------------------------

    @app.get("/terminals")
    def list_terminals() -> dict[str, Any]:
        return {"terminals": platform.terminals.list()}

    @app.get("/terminals/shells")
    def terminal_shells() -> dict[str, Any]:
        from ..terminals import available_shells

        return {"shells": available_shells()}

    @app.post("/terminals")
    def create_terminal(body: TerminalCreate) -> dict[str, Any]:
        try:
            session = platform.terminals.create(
                cwd=body.cwd, shell=body.shell, cols=body.cols, rows=body.rows
            )
        except RuntimeError as exc:  # session cap reached
            raise HTTPException(status_code=429, detail=str(exc))
        return session.info()

    @app.delete("/terminals/{term_id}")
    def kill_terminal(term_id: str) -> dict[str, Any]:
        return {"killed": platform.terminals.kill(term_id)}

    @app.websocket("/terminals/{term_id}/ws")
    async def terminal_ws(ws: WebSocket, term_id: str) -> None:
        if not _ws_token_ok(ws):
            await ws.close(code=1008)
            return
        session = platform.terminals.get(term_id)
        if session is None:
            await ws.close(code=1008)
            return
        await ws.accept()

        async def pump_output() -> None:  # PTY -> client
            # 10ms idle poll: measured end-to-end, the shell's own echo is
            # ~50ms (ConPTY/PowerShell), so our added worst-case latency should
            # stay well under it. 100 wakeups/s per idle terminal is noise.
            while True:
                data = session.read()
                if data:
                    await ws.send_bytes(data)
                elif not session.alive:
                    break
                else:
                    await asyncio.sleep(0.01)

        out = asyncio.create_task(pump_output())
        try:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                text = msg.get("text")
                if text is not None:
                    try:
                        obj = json.loads(text)
                    except (ValueError, TypeError):
                        obj = None
                    if isinstance(obj, dict) and obj.get("type") == "resize":
                        session.resize(int(obj["cols"]), int(obj["rows"]))
                    else:
                        session.write(text)
                elif msg.get("bytes") is not None:
                    session.write(msg["bytes"])
        except WebSocketDisconnect:
            pass
        finally:
            out.cancel()
            try:
                await ws.close()
            except Exception:
                pass

    # --- Filesystem tree (directory browser for the terminals panel) ------

    @app.get("/fs/drives")
    def fs_drives() -> dict[str, Any]:
        from ..fsbrowser import drives

        return {"drives": drives()}

    @app.get("/fs/home")
    def fs_home() -> dict[str, Any]:
        from ..fsbrowser import home

        return {"home": home()}

    @app.get("/fs/list")
    def fs_list(
        path: str, show_hidden: bool = False, dirs_only: bool = False
    ) -> dict[str, Any]:
        from ..fsbrowser import list_dir

        ok, reason = fs_read_ok(path)
        if not ok:
            raise HTTPException(status_code=403, detail=reason)
        try:
            return list_dir(path, show_hidden=show_hidden, dirs_only=dirs_only)
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise HTTPException(status_code=404, detail=str(exc))

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

    # Saved prompts / task templates (one-click re-run of a frequent task).
    @app.get("/templates")
    def list_templates() -> dict[str, Any]:
        from ..templates import TemplateStore

        return {
            "templates": [t.model_dump() for t in TemplateStore(platform.engine).list()]
        }

    @app.post("/templates")
    def create_template(body: TemplateCreateBody) -> dict[str, Any]:
        from ..templates import TemplateStore

        if not (body.task or "").strip():
            raise HTTPException(status_code=400, detail="task is required")
        rec = TemplateStore(platform.engine).create(
            body.name, body.task, body.agent_type, body.provider, body.model
        )
        return rec.model_dump()

    @app.delete("/templates/{prompt_id}")
    def delete_template(prompt_id: str) -> dict[str, Any]:
        from ..templates import TemplateStore

        return {"removed": TemplateStore(platform.engine).remove(prompt_id)}

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

    # --- Motivation Layer (the pulse): standing goals + proposals ---------

    def _goal_view(g) -> dict[str, Any]:
        return {
            "id": g.id, "text": g.text, "source": g.source, "category": g.category,
            "priority": g.priority, "autonomy_level": g.autonomy_level,
            "status": g.status, "action_budget": g.action_budget,
            "spend_budget": g.spend_budget, "actions_taken": g.actions_taken,
            "tokens_spent": g.tokens_spent,
            "last_acted_at": g.last_acted_at.isoformat() if g.last_acted_at else None,
            "created_at": g.created_at.isoformat(),
        }

    def _proposal_view(p) -> dict[str, Any]:
        return {
            "id": p.id, "goal_id": p.goal_id, "title": p.title,
            "rationale": p.rationale, "action": p.decoded_action(), "risk": p.risk,
            "source": p.source, "status": p.status, "session_id": p.session_id,
            "tokens": p.tokens, "created_at": p.created_at.isoformat(),
        }

    def _persist_config(keys: list[str]) -> None:
        """Persist whitelisted config keys to the project config.toml (atomic +
        restart-safe via temp-file + os.replace)."""
        cfg = platform.config
        persist_config_values(cfg.home, {k: getattr(cfg, k, None) for k in keys})

    @app.get("/goals")
    def list_goals(status: str | None = None) -> dict[str, Any]:
        return {"goals": [_goal_view(g) for g in platform.intent.list_goals(status)]}

    @app.post("/goals")
    def create_goal(body: GoalBody) -> dict[str, Any]:
        try:
            rec = platform.intent.add_goal(
                body.text,
                source=body.source,
                category=body.category,
                priority=body.priority,
                autonomy_level=body.autonomy_level,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return _goal_view(rec)

    @app.patch("/goals/{goal_id}")
    def patch_goal(goal_id: str, body: GoalPatch) -> dict[str, Any]:
        rec = platform.intent.update_goal(
            goal_id, **{k: v for k, v in body.model_dump().items() if v is not None}
        )
        if rec is None:
            raise HTTPException(status_code=404, detail="goal not found")
        return _goal_view(rec)

    @app.get("/proposals")
    def list_proposals(status: str | None = None) -> dict[str, Any]:
        return {
            "proposals": [_proposal_view(p) for p in platform.intent.list_proposals(status)]
        }

    @app.post("/proposals/{proposal_id}/approve")
    async def approve_proposal(proposal_id: str) -> dict[str, Any]:
        try:
            session = await platform.intent.approve(proposal_id, wait=False)
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
        rec = platform.intent.reject(proposal_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="proposal not found")
        return {"status": rec.status}

    @app.get("/autonomy")
    def autonomy_status() -> dict[str, Any]:
        cfg = platform.config
        used_actions, used_tokens = platform.intent._global_window_usage()
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
            "active_goals": len(platform.intent.list_goals(status="active")),
            "pending_proposals": len(platform.intent.list_proposals(status="pending")),
        }

    @app.post("/autonomy/kill")
    def autonomy_kill(body: KillBody) -> dict[str, Any]:
        """Global kill switch: engage (default) or release. Persisted to config."""
        platform.config.autonomy_kill_switch = bool(body.enabled)
        _persist_config(["autonomy_kill_switch"])
        return {"kill_switch": platform.config.autonomy_kill_switch}

    @app.post("/autonomy/tick")
    async def autonomy_tick(wait: bool = False) -> dict[str, Any]:
        """Run a single deliberation pulse now (no-ops when autonomy is disabled)."""
        return await platform.intent.deliberate(wait=wait)

    @app.get("/autonomy/briefing")
    def autonomy_briefing() -> dict[str, Any]:
        """Read-only briefing summary. Pushing it (a side effect) is POST-only so
        the Origin/CSRF guard (which only gates non-GET) actually protects it."""
        return platform.intent.briefing(notify=False)

    @app.post("/autonomy/briefing")
    def autonomy_briefing_push() -> dict[str, Any]:
        """Summarise + PUSH the briefing to the configured comm channel(s)."""
        return platform.intent.briefing(notify=True)

    # --- Sentinels (always-on watchers): suggest-only, never act ----------

    def _sentinel_view(s) -> dict[str, Any]:
        return {
            "id": s.id, "name": s.name, "kind": s.kind,
            "config": s.decoded_config(), "task": s.task,
            "agent_type": s.agent_type, "risk": s.risk, "enabled": s.enabled,
            "last_checked_at": s.last_checked_at.isoformat() if s.last_checked_at else None,
            "created_at": s.created_at.isoformat(),
        }

    @app.get("/sentinels")
    def list_sentinels() -> dict[str, Any]:
        return {
            "enabled": getattr(platform.config, "sentinels_enabled", False),
            "sentinels": [_sentinel_view(s) for s in platform.sentinels.list()],
        }

    @app.post("/sentinels")
    def create_sentinel(body: SentinelAdd) -> dict[str, Any]:
        try:
            rec = platform.sentinels.add(
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
        return _sentinel_view(rec)

    @app.delete("/sentinels/{name}")
    def delete_sentinel(name: str) -> dict[str, Any]:
        if not platform.sentinels.remove(name):
            raise HTTPException(status_code=404, detail="sentinel not found")
        return {"deleted": name}

    @app.post("/sentinels/poll")
    def poll_sentinels() -> dict[str, Any]:
        """Run one polling sweep now (suggest-only; no-ops when sentinels disabled).

        Mints SUGGEST-ONLY proposals for any noticed changes — never a session.
        Guarded by config.sentinels_enabled so a manual poke can't bypass opt-in.
        """
        if not getattr(platform.config, "sentinels_enabled", False):
            return {"ran": False, "reason": "sentinels_disabled", "proposals": []}
        created = platform.sentinels.poll_once(platform.intent)
        return {"ran": True, "proposals": [p.id for p in created]}

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
                body.slug,
                body.target_url,
                body.event_types,
                secret=secret,
                secret_name=body.secret_name or None,  # persist the real vault key
            )
        else:  # inbound: a default handler that emits a webhook.received event
            async def _handler(payload: dict, _slug: str = body.slug) -> dict[str, Any]:
                await platform.event_bus.publish(
                    "webhook.received", {"slug": _slug, "body": payload}
                )
                return {"ok": True, "slug": _slug}

            platform.inbound_webhooks.register(
                body.slug, _handler, secret=secret, secret_name=body.secret_name or None
            )
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
        if root:
            ok, reason = fs_read_ok(root)
            if not ok:
                raise HTTPException(status_code=403, detail=reason)
        roots = [Path(root)] if root else None
        results = platform.filesearch.search(q, mode=mode, limit=limit, roots=roots)
        # Filter protected/out-of-allowlist hits (a default-root search can reach
        # them) — same as the agent file_search tool.
        results = [
            r
            for r in results
            if not is_protected_path(r.get("path", "")) and fs_read_ok(r.get("path", ""))[0]
        ]
        return {"results": results}

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

    # Custom (agent/user-authored) reusable tools.
    @app.get("/tools/custom")
    def list_custom_tools() -> dict[str, Any]:
        import json as _json

        def _load(s: str):
            try:
                return _json.loads(s or "[]")
            except (TypeError, ValueError):
                return []

        return {
            "tools": [
                {
                    "name": r.name,
                    "description": r.description,
                    "parameters": _load(r.params_json),
                    "command": _load(r.argv_json),
                    "timeout_seconds": r.timeout_seconds,
                    "created_by": r.created_by,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in platform.tools_registry.list()
            ]
        }

    @app.post("/tools/custom")
    def create_custom_tool(body: CustomToolCreate) -> dict[str, Any]:
        import re as _re

        name = (body.name or "").strip()
        if not _re.match(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$", name):
            raise HTTPException(status_code=400, detail="invalid tool name")
        if platform.registry.get(name) is not None and name not in set(
            platform.registry.custom_names()
        ):
            raise HTTPException(status_code=400, detail=f"'{name}' is a built-in tool")
        if not body.command:
            raise HTTPException(status_code=400, detail="command (argv) is required")
        try:
            rec = platform.tools_registry.register(
                name, body.description, body.parameters, body.command, body.timeout_seconds
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        platform.registry.register(platform.tools_registry.build_tool(rec), custom=True)
        return {"name": rec.name}

    @app.delete("/tools/custom/{name}")
    def delete_custom_tool(name: str) -> dict[str, Any]:
        removed = platform.tools_registry.remove(name)
        platform.registry.unregister(name)
        return {"removed": removed}

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
        # BaseHTTPMiddleware can't see WS scope, so guard the token here too.
        if not _ws_token_ok(ws):
            await ws.close(code=1008)
            return
        await ws.accept()
        # Race a receiver against the event stream so a client that disconnects
        # while idle is detected promptly (Starlette only surfaces a disconnect
        # via receive()) — otherwise the coroutine parks at queue.get() forever,
        # leaking the subscriber while publish() keeps appending to its queue.
        it = platform.event_bus.subscribe()
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

    return app
