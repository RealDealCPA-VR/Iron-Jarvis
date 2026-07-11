"""FastAPI daemon (§9).

The single long-running process that owns the Orchestrator and Event Bus and
exposes them over REST + a WebSocket event stream for the dashboard (§4).

This module is the factory + glue only: platform build, lifespan (boot
rehydration + background loops), middleware, exception handlers, and the
shared helper closures. The ~170 endpoint handlers live in routes/<domain>.py
(moved verbatim; they reach closure state through the ``d`` deps object built
at the bottom of create_app). Request models live in schemas.py.
"""

from __future__ import annotations

import asyncio
import hmac
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from .. import __version__
from ..agents.orchestrator import Orchestrator
from ..core.config import persist_config_values
from ..core.db import session_scope
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


_CODE_BLOCK_RE = None  # compiled lazily in _first_code_block


def _first_code_block(text: str) -> str:
    """The first fenced code block's content (the AI's suggested command), or ''."""
    global _CODE_BLOCK_RE
    if _CODE_BLOCK_RE is None:
        import re as _re

        _CODE_BLOCK_RE = _re.compile(r"```[a-zA-Z0-9_+-]*\n(.*?)```", _re.DOTALL)
    m = _CODE_BLOCK_RE.search(text or "")
    return m.group(1).strip() if m else ""


#: Transient-failure classification lives with the router (single source of
#: truth — it now retries/fails-over for full agent sessions too).
from ..providers.router import is_transient_error as _is_transient_provider_error  # noqa: E402


async def _complete_with_retry(adapter, *, system, messages, tools, attempts: int = 3):
    """One-shot agent utilities (workflow builder, terminal assist) call the
    adapter directly — retry TRANSIENT failures (rate limit / overloaded) with
    backoff instead of surfacing a raw 429 on the first blip. Non-transient
    errors raise immediately; the last transient error raises after the final
    attempt (callers map it to a clean HTTP 429)."""
    delay = 1.5
    for i in range(attempts):
        try:
            return await adapter.complete(system=system, messages=messages, tools=tools)
        except Exception as exc:  # noqa: BLE001 — classified below
            if not _is_transient_provider_error(exc) or i == attempts - 1:
                raise
            await asyncio.sleep(delay)
            delay *= 2.5


def _provider_error_http(exc: Exception) -> HTTPException:
    """Map a provider failure to an honest, human-readable HTTP error."""
    if _is_transient_provider_error(exc):
        return HTTPException(
            status_code=429,
            detail=(
                "the model is rate-limited right now — wait a minute and try "
                "again, or pick a different model for this pane"
            ),
        )
    return HTTPException(status_code=502, detail=str(exc))


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


def _agent_type(name: str) -> AgentType:
    try:
        return AgentType(name)
    except ValueError:
        return AgentType.BUILDER


def _session_view(session) -> dict[str, Any]:
    return {
        "id": session.id,
        "project_id": getattr(session, "project_id", None),
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


def _extract_workflow_json(text: str) -> dict[str, Any]:
    """Pull the workflow JSON object out of a model reply.

    Prefers a fenced ```json … ``` block; otherwise scans for the FIRST
    balanced ``{…}`` with a string-aware state machine (so braces or quotes
    inside step text don't corrupt extraction, and a truncated reply that
    never closes simply fails). Raises :class:`ValueError` when nothing
    parseable is found so callers surface the honest error path — never
    returns a partial/garbled object.
    """
    import json as _json
    import re as _re

    text = text or ""
    # 1) A fenced code block is the cleanest signal when present.
    for m in _re.finditer(r"```(?:json)?\s*(.*?)```", text, _re.DOTALL):
        try:
            obj = _json.loads(m.group(1).strip())
        except Exception:  # noqa: BLE001 — fall through to the scanner
            continue
        if isinstance(obj, dict):
            return obj
    # 2) Scan for the first balanced object, ignoring braces inside strings.
    start, depth, in_str, escape = -1, 0, False, False
    for i, ch in enumerate(text):
        if start < 0:
            if ch == "{":
                start, depth = i, 1
            continue
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
        elif in_str:
            if ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = _json.loads(text[start : i + 1])
                    if isinstance(obj, dict):
                        return obj
                except Exception:  # noqa: BLE001 — keep scanning for the next one
                    pass
                start, in_str, escape = -1, False, False
    raise ValueError("no parseable workflow JSON object found")


def _unique_workflow_name(store, base: str, explicit: bool) -> str:
    """Normalized, collision-safe workflow name.

    When the caller is REFINING a named workflow (``explicit``), the name is
    kept so :meth:`WorkflowStore.save` upserts the SAME row. Otherwise a
    GENERATED name that already exists is suffixed ``-2``/``-3``… so we never
    silently clobber a saved workflow (the ``generated-workflow`` fallback
    included).
    """
    import re as _re

    name = (
        _re.sub(r"[^a-zA-Z0-9_-]+", "-", str(base).strip().lower()).strip("-")
        or "workflow"
    )
    if explicit:
        return name
    if store.get(name) is None:
        return name
    n = 2
    while store.get(f"{name}-{n}") is not None:
        n += 1
    return f"{name}-{n}"


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
    # Reflex Loop / Ambient Operator: the router turns an inbound signal (webhook
    # or comm) into a workflow / remote-agent / session run; the interpreter is
    # the phone command grammar. ``spawn_bg`` is set once it's defined below (the
    # router only needs it at fire time, never at construction).
    from ..reflex import CommandInterpreter, ReflexRouter

    reflex_router = ReflexRouter(platform, orchestrator, spawn_bg=None)
    command_interpreter = CommandInterpreter(platform, orchestrator, reflex_router)

    from ..comm import InboundPoller

    inbound_poller = InboundPoller(
        platform.notifier,
        orchestrator,
        platform.engine,
        event_bus=platform.event_bus,
        command_interpreter=command_interpreter,
        reflex_router=reflex_router,
    )

    # LIVE re-arm bridge: lifespan drops its event loop + the autonomy/sentinel
    # arm functions in here so put_settings (which runs in a threadpool) can
    # re-arm the background loops the moment a toggle changes — no restart.
    _live_rearm: dict[str, Any] = {}

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

        def _revalidate_active_project() -> None:
            """The active project (context spine) must still exist and be active
            — a manual DB edit, a restore, or an out-of-band delete can leave
            active_project_id dangling, which would then tag every new session
            into a ghost project. Clear it if it's gone or archived."""
            pid = getattr(platform.config, "active_project_id", None)
            if not pid:
                return
            from ..core.db import session_scope as _scope
            from ..core.models import Project as _Project

            with _scope(platform.engine) as _db:
                proj = _db.get(_Project, pid)
            if proj is None or proj.status != "active":
                platform.config.active_project_id = None
                d._persist_config(["active_project_id"])

        _rehydrate_step("revalidate_active_project", _revalidate_active_project)

        def _reconcile_workflow_runs() -> None:
            """A daemon restart kills any in-flight background workflow run —
            mark stale 'running'/'cancelling' records 'interrupted' so the UI
            never shows a zombie run as live."""
            from ..workflows.models import reconcile_interrupted_runs

            reconcile_interrupted_runs(platform.engine)

        _rehydrate_step("reconcile_workflow_runs", _reconcile_workflow_runs)
        if platform.intent is not None:  # reset proposals stranded 'executing' by a crash
            _rehydrate_step("reconcile_proposals", platform.intent.reconcile_executing_proposals)

        def _make_webhook_handler(slug):
            async def _handler(body, _slug=slug):
                await platform.event_bus.publish(
                    "webhook.received", {"slug": _slug, "body": body}
                )
                # Reflex Loop: fire any rule bound to this webhook (run a
                # workflow / remote agent / session). Best-effort — a reflex
                # failure never fails the webhook ack.
                fired: list[Any] = []
                try:
                    fired = await app.state.reflex_router.on_webhook(_slug, body)
                except Exception:  # noqa: BLE001 — never break the ack
                    log.exception("reflex on_webhook failed for %r", _slug)
                return {"ok": True, "reflexes_fired": len(fired)}

            return _handler

        _rehydrate_step(
            "rehydrate_webhooks",
            lambda: platform.inbound_webhooks.rehydrate(_make_webhook_handler),
        )
        # Terminal panes survive a restart / app update: re-open each persisted
        # session (fresh shell, same id + cwd + prior scrollback shown).
        _rehydrate_step("rehydrate_terminals", platform.terminals.rehydrate)
        # Living documents: their schedules fire event-kind tasks; regenerate
        # in the background when one lands (sync handler → task on the loop).
        def _on_livedoc_event(event: Any) -> None:
            etype = getattr(event, "type", None) or (
                event.get("type") if isinstance(event, dict) else None
            )
            if etype != "livedoc.regenerate":
                return
            payload = getattr(event, "payload", None) or (
                event.get("payload") if isinstance(event, dict) else {}
            ) or {}
            doc_id = payload.get("livedoc_id")
            if not doc_id:
                return

            async def _regen() -> None:
                try:
                    await app.state.regenerate_livedoc(doc_id)
                except Exception:  # noqa: BLE001 — recorded on the doc row
                    log.exception("living-doc regeneration failed for %s", doc_id)

            try:
                asyncio.get_running_loop().create_task(_regen())
            except RuntimeError:  # no loop (unit tests) — skip silently
                pass

        platform.event_bus.add_handler(_on_livedoc_event)

        # First run only: seed a few self-explanatory starter templates so the
        # Templates page (and the Overview "Your apps" tiles) start useful.
        def _seed_templates() -> None:
            from ..templates import TemplateStore

            TemplateStore(platform.engine).seed_starters()

        _rehydrate_step("seed_starter_templates", _seed_templates)
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

        # Lesson compaction — keeps "what I've learned" DISTILLED instead of a
        # pile of session-summary echoes. Deterministic dedup runs daily for
        # everyone (offline, free); MODEL distillation joins the pass only when
        # autonomy is enabled — the user's explicit opt-in to self-initiated
        # model spend (mirrors the suggest-don't-act ethos; the Memory page's
        # "Distill now" button is the anytime manual path). Disable via
        # IRONJARVIS_LESSON_COMPACT=off.
        compact_task = None
        if (os.environ.get("IRONJARVIS_LESSON_COMPACT", "on").strip().lower()
                not in {"0", "false", "no", "off"}):

            async def _lesson_compact_loop() -> None:
                await asyncio.sleep(300)  # never compete with boot
                while True:
                    try:
                        removed = await asyncio.to_thread(platform.learning.dedup)
                        if removed:
                            log.info("lesson dedup removed %d echo(es)", removed)
                        raw = await asyncio.to_thread(
                            platform.learning.raw_reflection_count
                        )
                        if getattr(platform.config, "autonomy_enabled", False) and raw >= 20:
                            adapter, used = _failover_adapter("mock")
                            if adapter is not None:
                                from ..providers.adapters.base import LLMMessage

                                async def _complete(prompt: str) -> str:
                                    resp, _, _ = await _one_shot_complete(
                                        used,
                                        adapter,
                                        system=(
                                            "You distill working notes into short, "
                                            "general, reusable lessons. Reply with "
                                            "ONLY a JSON array of strings."
                                        ),
                                        messages=[LLMMessage(role="user", content=prompt)],
                                    )
                                    return resp.text or ""

                                res = await platform.learning.distill(_complete)
                                log.info("lesson distillation: %s", res)
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001 - a pass must never kill the daemon
                        log.exception("lesson compaction pass failed")
                    await asyncio.sleep(24 * 3600)

            compact_task = asyncio.create_task(_lesson_compact_loop())

        # Motivation Layer deliberation tick — the pulse. GUARDED by
        # config.autonomy_enabled (OFF by default), so by default + in tests the
        # loop is never created and nothing self-initiates. Mirrors the auto-backup
        # loop: sleeps before the first tick (never blocks boot) and is cancelled
        # on shutdown. Armed at boot AND re-armed live from put_settings, so the
        # dashboard toggle takes effect without a daemon restart. Disable
        # explicitly via IRONJARVIS_AUTONOMY=off.
        bg_tasks: dict[str, asyncio.Task] = {}

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

        def _arm_autonomy() -> None:
            """(Re)arm or disarm the pulse to match CURRENT config. Always
            restarts an armed loop so an interval change applies too."""
            task = bg_tasks.pop("autonomy", None)
            if task is not None:
                task.cancel()
            if (
                getattr(platform.config, "autonomy_enabled", False)
                and platform.intent is not None
                and os.environ.get("IRONJARVIS_AUTONOMY", "on").strip().lower()
                not in {"0", "false", "no", "off"}
            ):
                bg_tasks["autonomy"] = asyncio.create_task(_autonomy_loop())
                log.info("autonomy loop (re)armed")
            elif task is not None:
                log.info("autonomy loop disarmed")

        _arm_autonomy()

        # Sentinels ("always-on watchers") polling loop. GUARDED by
        # config.sentinels_enabled (OFF by default), so by default + in tests the
        # loop is never created and nothing is polled. Mirrors the autonomy loop:
        # rehydrates the durable registry, sleeps before the first poll (never
        # blocks boot), is cancelled on shutdown, and re-arms live on a settings
        # change. Each poll diffs every enabled sentinel and mints SUGGEST-ONLY
        # proposals — never a session. Disable explicitly via IRONJARVIS_SENTINELS=off.

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

        def _arm_sentinels() -> None:
            task = bg_tasks.pop("sentinels", None)
            if task is not None:
                task.cancel()
            if (
                getattr(platform.config, "sentinels_enabled", False)
                and platform.sentinels is not None
                and platform.intent is not None
                and os.environ.get("IRONJARVIS_SENTINELS", "on").strip().lower()
                not in {"0", "false", "no", "off"}
            ):
                try:  # restart survival: rehydrate seen-state (never re-fires)
                    platform.sentinels.load()
                except Exception:  # pragma: no cover - never block arming
                    pass
                bg_tasks["sentinels"] = asyncio.create_task(_sentinel_loop())
                log.info("sentinel loop (re)armed")
            elif task is not None:
                log.info("sentinel loop disarmed")

        _arm_sentinels()

        # Expose the arm functions + this loop to put_settings (threadpool).
        _live_rearm["loop"] = asyncio.get_running_loop()
        _live_rearm["autonomy"] = _arm_autonomy
        _live_rearm["sentinels"] = _arm_sentinels

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

        # Slack SOCKET MODE — two-way Slack with zero internet exposure: the
        # daemon dials OUT (wss://) so no public URL is ever needed. GUARDED:
        # only when a slack channel opted in (inbound_enabled + allowlist +
        # app token), so default installs and tests create nothing. Disable
        # explicitly via IRONJARVIS_SLACK_SOCKET=off.
        slack_socket_task = None
        slack_socket_stop = None
        if os.environ.get("IRONJARVIS_SLACK_SOCKET", "on").strip().lower() not in (
            "0", "false", "no", "off",
        ):
            from ..comm.slack_socket import SlackSocketMode

            _socket = SlackSocketMode(
                inbound_poller,
                platform.notifier,
                platform.secrets.get,
                lambda: platform.config.comm or {},
            )
            if _socket.enabled():
                slack_socket_stop = asyncio.Event()

                async def _slack_socket_loop() -> None:
                    await asyncio.sleep(15)  # let boot settle first
                    try:
                        await _socket.run(stop=slack_socket_stop)
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001 — never kill the daemon
                        log.exception("slack socket mode loop failed")

                slack_socket_task = asyncio.create_task(_slack_socket_loop())
        try:
            yield
        finally:
            _live_rearm.clear()  # daemon going down — no more live re-arms
            if slack_socket_stop is not None:
                slack_socket_stop.set()
            if slack_socket_task is not None:
                slack_socket_task.cancel()
            if inbound_task is not None:
                inbound_task.cancel()
            for task in bg_tasks.values():
                task.cancel()
            if compact_task is not None:
                compact_task.cancel()
            if backup_task is not None:
                backup_task.cancel()
            try:
                platform.scheduler.shutdown()
            except Exception:  # pragma: no cover
                pass
            try:
                # Snapshot terminals (fresh scrollback) BEFORE killing them, so an
                # app-update restart re-opens the panes with their latest history.
                platform.terminals.snapshot()
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
    # Reflex Loop: the webhook handler + reflex routes reach these via app.state.
    app.state.reflex_router = reflex_router
    app.state.command_interpreter = command_interpreter
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

    # The Reflex Router launches its long-running actions through the daemon's
    # background task launcher (now that it exists), so a webhook POST returns
    # immediately while the bound workflow/session runs in the background.
    reflex_router.spawn_bg = _spawn_bg

    def _visible_providers() -> list[dict[str, Any]]:
        """Provider health with the internal 'mock' offline model hidden.

        'mock' is the load-bearing offline fallback + the autopromote sentinel,
        so it stays in the ENGINE — but it must not surface as a selectable
        model/tile in the UI (pickers, connections, the switcher). Filtered here
        (and in /models + /connections) rather than removed from the registry."""
        return [p for p in platform.providers.health() if p.get("provider") != "mock"]

    # --- Chat (direct conversation — frontier-chat parity) -----------------

    _PERSONAS: dict[str, dict[str, str]] = {
        "assistant": {
            "description": "Sharp, friendly general assistant (default)",
            "prompt": (
                "You are Iron Jarvis, the user's personal AI running on their own "
                "machine. Answer directly and conversationally — helpful, sharp, "
                "warm, concise but complete. Use markdown when it helps."
            ),
        },
        "developer": {
            "description": "Senior software engineer — code, debugging, architecture",
            "prompt": (
                "You are a pragmatic senior software engineer. Give working code, "
                "concrete diagnoses, and honest trade-offs. Prefer minimal examples "
                "over prose; call out pitfalls."
            ),
        },
        "accountant": {
            "description": "CPA-grade accounting, tax, and business analysis",
            "prompt": (
                "You are a meticulous CPA and business advisor. Be precise with "
                "numbers, cite the relevant rules/forms when applicable, show your "
                "work, and flag anything requiring professional judgment. Never "
                "invent figures."
            ),
        },
        "writer": {
            "description": "Editor and wordsmith — drafts, tone, clarity",
            "prompt": (
                "You are a skilled editor and writer. Produce clean, natural prose "
                "matched to the requested tone and audience; offer sharper "
                "alternatives when the user's draft can be improved."
            ),
        },
        "researcher": {
            "description": "Structured analysis — thorough, sourced, balanced",
            "prompt": (
                "You are a careful researcher. Structure answers, distinguish fact "
                "from inference, state confidence levels, and note what you'd need "
                "to verify. Never present speculation as fact."
            ),
        },
    }

    # --- Voice (server-side dictation fallback) ---------------------------
    # The dashboard prefers the browser's Web Speech engine (free, streaming),
    # but the packaged Electron app has none — these endpoints give the desktop
    # app working dictation via a connected transcription-capable backend.

    _VOICE_MAX_BYTES = 25 * 1024 * 1024  # OpenAI's audio upload cap

    def _voice_backend() -> tuple[str, str, str | None] | None:
        """First available speech-to-text backend as (label, url, api_key).

        An OpenAI API KEY wins (a ChatGPT OAuth token is deliberately NOT used —
        the audio API rejects account tokens); else a configured custom
        OpenAI-compatible endpoint (LocalAI / faster-whisper-server / Speaches
        all serve /v1/audio/transcriptions). None = no backend, be honest.
        """
        try:
            key = platform.secrets.get("openai_api_key")
        except Exception:  # noqa: BLE001 - vault miss = not available
            key = None
        if key:
            return ("openai", "https://api.openai.com/v1/audio/transcriptions", key)
        base = (getattr(platform.config, "custom_base_url", None) or "").strip()
        if base:
            u = base.rstrip("/")
            if u.endswith("/chat/completions"):
                u = u[: -len("/chat/completions")]
            if not u.endswith("/v1"):
                u += "/v1"
            try:
                ckey = platform.secrets.get("custom_api_key")
            except Exception:  # noqa: BLE001
                ckey = None
            return ("custom", u + "/audio/transcriptions", ckey)
        return None

    # --- Living documents (§reports that stay fresh) -----------------------

    async def _regenerate_livedoc(doc_id: str) -> dict[str, Any]:
        """Regenerate one living doc: prompt → model → rewrite the SAME file."""
        from datetime import datetime, timezone

        from ..core.ids import utcnow as _now
        from ..core.models import LiveDocRecord
        from ..documents.writers import write_document
        from ..providers.adapters.base import LLMMessage

        with session_scope(platform.engine) as db:
            doc = db.get(LiveDocRecord, doc_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="no such living document")
        provider = doc.provider or platform.config.default_provider
        model = doc.model or platform.config.default_model
        adapter = platform.providers.get(provider, model)
        system = (
            "You maintain a LIVING DOCUMENT that is regenerated on a schedule. "
            "Produce the complete, current content as clean markdown ('# ' title "
            "first). Today is "
            + datetime.now(timezone.utc).strftime("%Y-%m-%d")
            + ". Output ONLY the document."
        )
        try:
            resp, _p, _m = await _one_shot_complete(
                provider, adapter, system=system,
                messages=[LLMMessage(role="user", content=doc.prompt[:8000])],
            )
            out_dir = platform.config.home / "livedocs"
            out_dir.mkdir(parents=True, exist_ok=True)
            import re as _re

            slug = _re.sub(r"[^a-zA-Z0-9_-]+", "-", doc.name.lower()).strip("-") or "doc"
            path = out_dir / f"{slug}.{doc.format}"
            write_document(path, resp.text or "(empty)")
            with session_scope(platform.engine) as db:
                row = db.get(LiveDocRecord, doc_id)
                row.path = str(path)
                row.updated_at = _now()
                row.last_error = ""
                db.add(row)
                db.commit()
            return {"id": doc_id, "path": str(path), "ok": True}
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 — record the failure honestly
            with session_scope(platform.engine) as db:
                row = db.get(LiveDocRecord, doc_id)
                if row is not None:
                    row.last_error = f"{type(exc).__name__}: {exc}"[:300]
                    db.add(row)
                    db.commit()
            raise HTTPException(status_code=502, detail=str(exc))

    app.state.regenerate_livedoc = _regenerate_livedoc  # for the schedule handler

    # --- Skills (§23) -----------------------------------------------------

    def _rescan_skills() -> dict[str, int]:
        """Rebuild the skill registry IN PLACE from every source and return a
        per-source tally. Shared by boot-adjacent create/rescan endpoints."""
        platform.skills.repopulate(
            platform.config.home, getattr(platform.config, "extra_skill_paths", None)
        )
        counts: dict[str, int] = {}
        for s in platform.skills.list():
            counts[s.source] = counts.get(s.source, 0) + 1
        return counts

    # --- LLM Connections (API key + OAuth2/PKCE) --------------------------

    #: A sane default model per provider, used when auto-promoting the FIRST real
    #: connection away from the out-of-box "mock" default (see _maybe_autopromote).
    _PROMOTE_DEFAULT_MODEL = {
        "anthropic": "claude-opus-4-8",
        "openai": "gpt-4o-mini",
        "google": "gemini-1.5-flash",
        "xai": "grok-4-1-fast",
        "openrouter": "openrouter/auto",
    }

    def _maybe_autopromote_default(provider: str) -> bool:
        """If the default provider is still the offline "mock" when the first REAL
        provider connects, promote that provider (+ a matching default model) so a
        "Default" session uses a real model instead of silently faking output.
        Returns True if it promoted."""
        cfg = platform.config
        if provider == "mock" or cfg.default_provider != "mock":
            return False
        # Only INFERENCE providers may become the default — connecting a
        # non-LLM service (Pixio, a storage source) must never hijack routing.
        try:
            if provider not in platform.providers._factories:  # noqa: SLF001
                return False
        except Exception:  # noqa: BLE001 — be conservative, don't promote
            return False
        cfg.default_provider = provider
        cfg.default_model = _PROMOTE_DEFAULT_MODEL.get(provider, cfg.default_model)
        _persist_config(["default_provider", "default_model"])
        return True

    # One live loopback listener per provider (see connections/loopback.py) —
    # restarted on every new flow, self-expiring on TTL.
    _loopback_servers: dict[str, Any] = {}

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

    # --- One-shot completion utilities (terminal assist / builders) -------

    def _failover_candidates(exclude: str):
        """Ordered, identity-deduped list of AVAILABLE real adapters to absorb a
        one-shot call, sharing the router's CLI-first arbitrage order (import
        ``_FAILOVER_ORDER`` — single source of truth) with the DEFAULT provider
        bumped first. Includes the subscription CLIs (claude-cli/codex-cli/
        grok-cli — $0 marginal) that the old hardcoded list omitted. Never mock.

        Dedup is by RESOLVED provider identity (not the requested name), so the
        inherited alias (openai→codex-cli) that ``exclude`` maps to is skipped."""
        from ..providers.router import _FAILOVER_ORDER

        order = list(_FAILOVER_ORDER)
        dp = platform.config.default_provider
        if dp in order:
            order.remove(dp)
            order.insert(0, dp)
        # Resolve the excluded provider to its ACTUAL adapter identity so the
        # failed provider's inherited alias isn't retried under another name.
        excluded_providers = {exclude}
        try:
            if exclude != "mock" and platform.providers.available(exclude):
                excluded_providers.add(platform.providers.get(exclude).provider)
        except Exception:  # noqa: BLE001
            pass
        out: list[tuple[Any, str]] = []
        seen_ids: set[int] = set()
        seen_providers: set[str] = set()
        for p in order:
            if p == "mock" or p in excluded_providers or not platform.providers.available(p):
                continue
            try:
                adapter = platform.providers.get(p)
            except Exception:  # noqa: BLE001 — try the next one
                continue
            if (
                id(adapter) in seen_ids
                or adapter.provider in seen_providers
                or adapter.provider in excluded_providers
            ):
                continue
            seen_ids.add(id(adapter))
            seen_providers.add(adapter.provider)
            out.append((adapter, p))
        return out

    def _failover_adapter(exclude: str):
        """The single strongest real provider to absorb a rate-limited one-shot
        call. Returns (adapter, provider) or (None, None). Never picks mock."""
        cands = _failover_candidates(exclude)
        return cands[0] if cands else (None, None)

    async def _one_shot_complete(provider: str, adapter, *, system: str, messages):
        """Complete a ONE-SHOT utility call (terminal assist / workflow builder)
        with retry-on-transient, then CROSS-PROVIDER failover when the provider
        stays rate-limited. Mirrors the router: iterate ALL connected candidates
        (CLI-first) until one succeeds, not just the first. Returns
        (response, used_provider, used_model). Raises a clean HTTPException."""
        try:
            resp = await _complete_with_retry(
                adapter, system=system, messages=messages, tools=[]
            )
            return resp, provider, getattr(adapter, "model", None)
        except Exception as exc:  # noqa: BLE001 — classified below
            if not _is_transient_provider_error(exc):
                raise _provider_error_http(exc)
            for alt, alt_provider in _failover_candidates(provider):
                try:
                    resp = await _complete_with_retry(
                        alt, system=system, messages=messages, tools=[]
                    )
                    return resp, alt_provider, getattr(alt, "model", None)
                except Exception:  # noqa: BLE001 — try the next candidate
                    continue
            # Every connected provider is rate-limited: surface the ORIGINAL error.
            raise _provider_error_http(exc)

    # --- Workflows (§24, §25) ---------------------------------------------

    async def _build_workflow(
        description: str,
        provider: str = "",
        model: str = "",
        name: str = "",
        current: list[dict] | None = None,
    ) -> dict[str, Any]:
        """Turn a natural-language ``description`` into a saved ``{name, steps}``
        workflow via an agent. Shared by the chat builder and the
        terminal-session → workflow bridge."""
        import json as _json

        from ..providers.adapters.base import LLMMessage

        provider = provider or platform.config.default_provider
        model = model or platform.config.default_model
        try:
            adapter = platform.providers.get(provider, model)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"provider unavailable: {exc}")
        # Honest offline hint: when the RESOLVED adapter is the built-in offline
        # mock (a fresh/SIMULATED install) and no real provider exists to fail
        # over to, say "connect a model" instead of building a fabricated
        # workflow (and later blaming the reply). Checking the resolved adapter
        # — not the provider NAME — keeps an explicitly supplied working adapter
        # (as tests inject) on the normal path.
        from ..providers.adapters.mock import MockLLMAdapter

        if isinstance(adapter, MockLLMAdapter):
            _alt_adapter, _alt_provider = _failover_adapter("mock")
            if _alt_adapter is None:
                raise HTTPException(
                    status_code=400,
                    detail="connect a model on the Connections page to build workflows",
                )

        system = (
            "You design Iron Jarvis workflows. A workflow is a repeatable, ordered "
            "list of steps. Respond with ONLY a JSON object (no prose, no code "
            "fence) of the exact shape: "
            '{"name": "kebab-case-name", "description": "one line", '
            '"steps": [{"name": "Step name", "agent": "builder", "task": '
            '"a clear instruction for this step", "tool": null}]}. '
            "agent MUST be one of: builder, planner, researcher, reviewer, "
            "supervisor. Keep tasks concrete and self-contained. Prefer 2-6 steps."
        )
        user = f"Create a workflow for this request:\n\n{description}"
        if current:
            user += (
                "\n\nRefine THIS existing workflow (return the full updated "
                f"workflow):\n{_json.dumps(current)}"
            )
        resp, _used_provider, _used_model = await _one_shot_complete(
            provider,
            adapter,
            system=system,
            messages=[LLMMessage(role="user", content=user)],
        )

        # Extract the workflow JSON from the reply (string-aware; tolerant of
        # stray prose, fenced blocks, and braces inside step text).
        try:
            wf = _extract_workflow_json(resp.text or "")
            raw_steps = wf.get("steps")
        except Exception:  # noqa: BLE001 — no parseable object (garbled/truncated)
            raise HTTPException(
                status_code=422,
                detail="the model did not return a valid workflow — try rephrasing",
            )
        if not isinstance(raw_steps, list) or not raw_steps:
            raise HTTPException(
                status_code=422,
                detail="the model did not return a valid workflow — try rephrasing",
            )

        valid_agents = {"builder", "planner", "researcher", "reviewer", "supervisor"}
        steps: list[dict[str, Any]] = []
        for s in raw_steps:
            if not isinstance(s, dict) or not (s.get("task") or s.get("name")):
                continue
            agent = str(s.get("agent") or "builder").lower()
            steps.append(
                {
                    "name": str(s.get("name") or s.get("task") or "step")[:80],
                    "agent": agent if agent in valid_agents else "builder",
                    "task": str(s.get("task") or ""),
                    "tool": s.get("tool") or None,
                }
            )
        if not steps:
            raise HTTPException(status_code=422, detail="no usable steps were generated")

        from ..workflows.store import WorkflowStore

        # Unique name: refinement (explicit ``name``) upserts the SAME workflow;
        # a fresh generated name that collides is suffixed -2/-3… (never clobbers
        # a saved workflow — the "generated-workflow" fallback included).
        store = WorkflowStore(platform.engine)
        explicit = bool(name)
        wf_name = _unique_workflow_name(
            store, name or wf.get("name") or "generated-workflow", explicit
        )
        wf_desc = str(wf.get("description") or description)[:200]

        store.save(wf_name, steps, description=wf_desc)
        return {
            "name": wf_name,
            "description": wf_desc,
            "steps": steps,
            "reply": f"Built **{wf_name}** with {len(steps)} step(s). Loaded into the editor — tweak and Run when ready.",
        }

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

    # --- Sentinels (always-on watchers): suggest-only, never act ----------

    def _sentinel_view(s) -> dict[str, Any]:
        return {
            "id": s.id, "name": s.name, "kind": s.kind,
            "config": s.decoded_config(), "task": s.task,
            "agent_type": s.agent_type, "risk": s.risk, "enabled": s.enabled,
            "last_checked_at": s.last_checked_at.isoformat() if s.last_checked_at else None,
            "created_at": s.created_at.isoformat(),
        }

    # --- Communication channels -------------------------------------------

    #: The user-addable channel types + their form fields. ``secret`` fields are
    #: stored ENCRYPTED in the vault (referenced by name); the rest live in
    #: config.comm. This drives the Channels "add" form.
    _CHANNEL_TYPE_FIELDS = {
        "slack": [
            {"key": "webhook_url", "label": "Incoming webhook URL (option A)", "secret": False,
             "help": "Simplest: Slack app → Incoming Webhooks → Add New Webhook. "
                     "Fill EITHER this, OR the bot token + channel below."},
            {"key": "token", "label": "Bot token (option B)", "secret": True,
             "help": "xoxb-… from your Slack app → OAuth & Permissions → Bot User "
                     "OAuth Token. Needs the chat:write scope (see the app "
                     "manifest below — create the app from it in one paste)."},
            {"key": "channel", "label": "Channel (option B)", "secret": False,
             "help": "Where messages go, e.g. #general or a channel ID (C0123…). "
                     "Invite the bot to the channel: /invite @Iron Jarvis."},
            {"key": "signing_secret", "label": "Signing secret (two-way)", "secret": True,
             "help": "App → Basic Information → Signing Secret. UNLOCKS inbound "
                     "events: point Slack's Event Subscriptions request URL at "
                     "/comm/slack/events/<channel-name> (needs a public URL — "
                     "e.g. a Tailscale funnel); Iron Jarvis verifies every "
                     "request against this secret."},
            {"key": "app_id", "label": "App ID (optional)", "secret": False,
             "help": "Basic Information → App ID (A0…). Stored for reference."},
            {"key": "client_id", "label": "Client ID (optional)", "secret": False,
             "help": "Basic Information → Client ID. Stored (vault) for future "
                     "OAuth installs to other workspaces."},
            {"key": "client_secret", "label": "Client secret (optional)", "secret": True,
             "help": "Basic Information → Client Secret. Stored encrypted for "
                     "future OAuth installs."},
            {"key": "verification_token", "label": "Verification token (optional)", "secret": True,
             "help": "Basic Information → Verification Token (legacy — Slack "
                     "deprecates it in favor of the signing secret). Stored "
                     "encrypted."},
            {"key": "app_token", "label": "App-level token (two-way, no exposure)", "secret": True,
             "help": "xapp-… from Basic Information → App-Level Tokens "
                     "(connections:write scope). POWERS SOCKET MODE: Iron Jarvis "
                     "dials OUT to Slack over a WebSocket — two-way DMs with "
                     "ZERO public URL / internet exposure. Enable Socket Mode in "
                     "the app (the manifest below already does)."},
            {"key": "inbound_enabled", "label": "Enable two-way (true/false)", "secret": False,
             "help": "Set to true to let allowlisted people DM the bot and get "
                     "agent replies. Off by default."},
            {"key": "allowed_senders", "label": "Allowlist (Slack member IDs)", "secret": False,
             "help": "Comma-separated member IDs (U0123…, profile → three dots → "
                     "Copy member ID). FAIL-CLOSED: empty allowlist = nobody may "
                     "command the bot."},
        ],
        "discord": [
            {"key": "webhook_url", "label": "Webhook URL", "secret": False,
             "help": "Channel → Edit → Integrations → Webhooks."},
        ],
        "telegram": [
            {"key": "token", "label": "Bot token", "secret": True,
             "help": "From @BotFather."},
            {"key": "chat_id", "label": "Chat ID", "secret": False,
             "help": "Your numeric chat id (message @userinfobot to find it)."},
        ],
        "email": [
            {"key": "host", "label": "SMTP host", "secret": False, "help": "e.g. smtp.gmail.com"},
            {"key": "port", "label": "SMTP port", "secret": False, "help": "usually 587"},
            {"key": "username", "label": "Username", "secret": False},
            {"key": "password", "label": "Password / app password", "secret": True},
            {"key": "from_addr", "label": "From address", "secret": False},
            {"key": "to_addr", "label": "Send to", "secret": False},
        ],
    }

    # Pre-formatted app manifests (YAML — Slack's canonical format): paste at
    # api.slack.com/apps → "Create New App" → "From an app manifest" and every
    # required scope/setting is configured in one step.
    _CHANNEL_MANIFESTS = {
        "slack": (
            "display_information:\n"
            "  name: Iron Jarvis\n"
            "  description: Notifications and two-way chat from your local Iron Jarvis\n"
            "  background_color: \"#0a0c11\"\n"
            "features:\n"
            "  bot_user:\n"
            "    display_name: Iron Jarvis\n"
            "    always_online: true\n"
            "oauth_config:\n"
            "  scopes:\n"
            "    bot:\n"
            "      - chat:write\n"
            "      - chat:write.public\n"
            "      - channels:history\n"
            "      - im:history\n"
            "      - im:write\n"
            "settings:\n"
            "  event_subscriptions:\n"
            "    bot_events:\n"
            "      - message.im\n"
            "  org_deploy_enabled: false\n"
            "  # Socket Mode = Iron Jarvis dials OUT to Slack; two-way with no\n"
            "  # public URL. Create an App-Level Token (connections:write) after\n"
            "  # installing and paste it into the channel form.\n"
            "  socket_mode_enabled: true\n"
            "  token_rotation_enabled: false\n"
        ),
    }

    # --- External MCP servers (prebuilt catalog + custom) ------------------

    #: Curated, known-good MCP servers (npx-based, cross-platform). The
    #: placeholders in `args` are filled by the user in the UI.
    _MCP_CATALOG = [
        {
            "id": "filesystem",
            "name": "Filesystem",
            "description": "Read/write files in folders you choose (official server).",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "<folder-path>"],
        },
        {
            "id": "fetch",
            "name": "Web Fetch",
            "description": "Fetch and clean web pages for the agent (official server).",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-fetch"],
        },
        {
            "id": "github",
            "name": "GitHub",
            "description": "Repos, issues, PRs. Needs a GitHub personal access token.",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            "env_keys": ["GITHUB_PERSONAL_ACCESS_TOKEN"],
        },
        {
            "id": "memory",
            "name": "Knowledge Graph Memory",
            "description": "A persistent knowledge-graph memory (official server).",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-memory"],
        },
        {
            "id": "box",
            "name": "Box (client files)",
            "description": "Search, read, and manage files in Box — Box's own MCP "
                           "server (Python; needs uv installed). Get a Developer "
                           "Token from a Box custom app (developer.box.com).",
            "command": "uvx",
            "args": ["mcp-server-box"],
            "env_keys": ["BOX_CLIENT_ID", "BOX_CLIENT_SECRET"],
        },
    ]

    # --- Domain route modules (routes/) -------------------------------------
    # Handlers moved out of this factory VERBATIM; ``d`` carries the
    # closure-local state their bodies resolve at request time. Register
    # order preserves the original within-prefix route order.
    from types import SimpleNamespace

    from . import routes as _routes

    d = SimpleNamespace(
        platform=platform,
        orchestrator=orchestrator,
        loop_health=loop_health,
        inbound_poller=inbound_poller,
        _live_rearm=_live_rearm,
        _loopback_servers=_loopback_servers,
        _spawn_bg=_spawn_bg,
        _visible_providers=_visible_providers,
        _PERSONAS=_PERSONAS,
        _VOICE_MAX_BYTES=_VOICE_MAX_BYTES,
        _voice_backend=_voice_backend,
        _regenerate_livedoc=_regenerate_livedoc,
        _rescan_skills=_rescan_skills,
        _PROMOTE_DEFAULT_MODEL=_PROMOTE_DEFAULT_MODEL,
        _maybe_autopromote_default=_maybe_autopromote_default,
        _cu_status=_cu_status,
        _failover_adapter=_failover_adapter,
        _one_shot_complete=_one_shot_complete,
        _build_workflow=_build_workflow,
        _goal_view=_goal_view,
        _proposal_view=_proposal_view,
        _persist_config=_persist_config,
        _sentinel_view=_sentinel_view,
        _CHANNEL_TYPE_FIELDS=_CHANNEL_TYPE_FIELDS,
        _CHANNEL_MANIFESTS=_CHANNEL_MANIFESTS,
        _MCP_CATALOG=_MCP_CATALOG,
    )
    _routes.chat.register(app, d)
    _routes.projects.register(app, d)
    _routes.fsbrowse.register(app, d)
    _routes.voice.register(app, d)
    _routes.sessions.register(app, d)
    _routes.documents.register(app, d)
    _routes.learning.register(app, d)
    _routes.computeruse.register(app, d)
    _routes.terminals.register(app, d)
    _routes.workflows.register(app, d)
    _routes.autonomy.register(app, d)
    _routes.settings.register(app, d)
    _routes.knowledge.register(app, d)
    _routes.creative.register(app, d)
    _routes.connections.register(app, d)
    _routes.connectors.register(app, d)
    _routes.routing.register(app, d)
    _routes.comm.register(app, d)
    _routes.agents.register(app, d)
    _routes.reflex.register(app, d)
    _routes.audit.register(app, d)
    _routes.undo.register(app, d)
    _routes.system.register(app, d)
    return app
