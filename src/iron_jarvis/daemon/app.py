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
import os
import uuid
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
from ..personas.builtins import BUILTIN_PERSONAS
from ..platform import build_platform
from ..tools.permissions import headless_ask_resolver
from .auth import token_matches as _token_matches

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
    """Constant-time WebSocket bearer-token check (matches the HTTP middleware).

    Shares ``auth.token_matches`` with that middleware precisely so both stay
    non-raising: a non-ASCII ``?token=`` used to blow up inside the handshake of
    /events, /terminals/{id}/ws and /voice/stream (an unhandled exception —
    FastAPI's Exception handler is HTTP-only) instead of the intended 1008
    policy close the callers already perform on a False.
    """
    token = os.environ.get("IRONJARVIS_TOKEN", "").strip()
    if not token:
        return True
    return _token_matches(ws.query_params.get("token") or "", token)


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


def _session_view(session, d=None) -> dict[str, Any]:
    """One session row. v1.227.0 (audit A4/A5): every row carries the job's
    ``outcome`` verdict; ``waiting_on`` (the ask a paused run is parked on) is
    filled when the caller passes the deps object, because the live pause
    lives in the approvals registry, not on the row. Additive — nothing that
    read the old shape changes."""
    waiting_on = None
    if d is not None:
        # Lazy: routes.sessions imports this module (import direction).
        from .routes.sessions import _waiting_on

        waiting_on = _waiting_on(d, session.id)
    return {
        "id": session.id,
        "outcome": getattr(session, "outcome", None) or None,
        "waiting_on": waiting_on,
        "project_id": getattr(session, "project_id", None),
        "task": session.task,
        # Where the session came from (v1.119.0): "schedule:<name>" for
        # schedule-fired runs, "self_dev", or None for user-started work.
        "origin": getattr(session, "origin", None),
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


#: History-search backfill loop tuning. Module-level (and injectable) so the
#: loop is testable without a 60-second wait.
_FTS_INITIAL_DELAY = 60.0
_FTS_IDLE_DELAY = 3600.0
_FTS_BATCH = 200
#: Breath between chunks so a 50k-message history can't monopolise the loop.
_FTS_CHUNK_PAUSE = 0.05


def _backfill_index(platform) -> "Any | None":
    """The ``SearchIndex`` the BACKFILL LOOP writes through — the SHARED one,
    ``core.db.search_index(engine)`` (exposed as ``platform.search_index``),
    exactly like the ``history_search`` tool, ``GET /search/history``, the memory
    fabric, and all five write seams.

    HISTORY, because this was briefly the one exception and the reason matters.
    ``SearchIndex`` used to take its internal ``RLock`` on EVERY write, including
    the ones where it opens its own transaction — so the backfill's order was
    **index lock → SQLite writer**, while a write seam (already inside its own
    ``session_scope``) could be **SQLite writer → index lock**. On one shared
    instance that is an ABBA cycle broken only by SQLite's 30s ``busy_timeout``,
    and it was MEASURED at chat saves p50 33.0 s with 70% of the backfill's docs
    lost to "database is locked". The stopgap was to hand this loop a second
    instance on the same engine, which dodged the cycle by having no shared lock
    to invert — at the cost of the lock meaning nothing between the two writers.

    v1.142.0 fixed the cause instead (``search/index.py`` DECISION 4): the lock
    is now taken ONLY on the ``db=`` path, and a self-owned write opens its
    transaction with ``BEGIN IMMEDIATE`` so it holds SQLite's writer slot across
    its whole read-modify-write and holds no Python lock at all. Nothing can
    invert, and the index write is MORE atomic than the lock ever made it. Same
    bench after the fix: chat saves p50 **1.6 ms** / p95 **3.7 ms**, zero lost
    writes on either side, with the backfill hammering the SAME instance
    (``tests/test_search_index.py::test_a_backfill_sized_write_cannot_stall_a_chat_save``).

    So the loop is back on the shared instance, which is what makes the index's
    ``db=`` lock real again: two instances are two locks, i.e. no lock at all.
    """
    index = getattr(platform, "search_index", None)
    if index is None:
        return None
    try:
        index.available()  # warm the probe off the first chunk (usually already warm)
    except Exception:  # noqa: BLE001 — never let this cost the daemon a boot
        log.warning("history-search backfill index probe failed", exc_info=True)
    return index


def _checkpoint_wal(engine) -> None:
    """Fold the WAL back into the main file at shutdown (v1.226.0).

    Best-effort and skipped for in-memory engines. Without it a hard kill
    right after the drain leaves a -wal sidecar the next boot must replay, and
    a backup/copy taken in between misses every write still in the WAL."""
    if getattr(engine.url, "database", None) in (None, "", ":memory:"):
        return
    try:
        with engine.connect() as conn:
            # This runs ON the loop thread at lifespan exit: a worker still
            # writing must not hold exit for the pool's 30s busy_timeout
            # (Electron force-kills at 5s — the very thing F-B-3 targets).
            conn.exec_driver_sql("PRAGMA busy_timeout=1000")
            conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:  # noqa: BLE001 — best-effort; shutdown never raises
        log.debug("wal checkpoint at shutdown skipped", exc_info=True)


async def _fts_backfill_loop(
    index,
    loop_health: "dict[str, dict[str, Any]]",
    *,
    initial_delay: float = _FTS_INITIAL_DELAY,
    idle_delay: float = _FTS_IDLE_DELAY,
    batch: int = _FTS_BATCH,
    pause: float = _FTS_CHUNK_PAUSE,
) -> None:
    """Index the history that existed BEFORE history search shipped.

    Live sync (Pair S2) only ever sees writes from now on; without this loop a
    user's first search of a two-year-old install would find their newest thread
    and nothing else. Shape mirrors ``_auto_backup_loop``: sleep first (never
    compete with boot), do the work in ``asyncio.to_thread`` (``backfill`` is
    synchronous SQLite), never let one failure kill the daemon, re-raise
    ``CancelledError`` so shutdown is clean.

    Chunked and resumable: each pass feeds the previous pass's keyset cursor
    back in until ``done``, then the loop IDLES for ``idle_delay`` and re-checks
    from ``cursor=None``. The re-check is cheap because ``backfill`` skips
    already-indexed threads, and it is what makes the POISON-CHUNK path safe: a
    chunk that fails PARKS itself (``done: True, error: True``) instead of
    hot-looping, and the hourly restart-from-scratch is the retry — a chunk that
    fails forever costs one bounded pass an hour, not a spinning core.

    ``loop_health["fts_backfill"]`` (surfaced at ``GET /diagnostics`` as
    ``background_loops``) carries ``ok`` / ``done`` / cumulative ``indexed`` /
    ``scanned`` plus timestamps, so a stuck or parked backfill is VISIBLE
    instead of buried in the log.

    ``ok`` is per-SWEEP, not per-pass, and that distinction is the whole value
    of the field. ``backfill`` reports a failed PHASE by skipping to the next
    one (``error: True``) and then finishes the remaining phases normally — so a
    per-pass ``ok`` would be overwritten by the very next pass, and a run that
    silently gave up on every chat thread after a bad page would report
    ``ok: True`` at ``/diagnostics`` while the user's chat history was missing
    from search. The flag therefore latches for the sweep and clears only when
    the next sweep starts, which is exactly the window a human reading
    ``/diagnostics`` is asking about.
    """
    from ..core.ids import utcnow

    if index is None:  # pragma: no cover — always built by build_platform
        return
    await asyncio.sleep(initial_delay)
    cursor: "str | None" = None
    indexed_total = 0
    scanned_total = 0
    sweep_error: "str | None" = None
    while True:
        try:
            result = await asyncio.to_thread(index.backfill, batch, cursor)
            indexed_total += int(result.get("indexed") or 0)
            scanned_total += int(result.get("scanned") or 0)
            done = bool(result.get("done"))
            cursor = result.get("cursor")
            if result.get("error"):
                sweep_error = "backfill chunk failed; retrying next sweep"
            health: dict[str, Any] = {
                "ok": sweep_error is None,
                "done": done,
                "indexed": indexed_total,
                "scanned": scanned_total,
                "last_pass_at": utcnow().isoformat(),
            }
            if sweep_error:
                health["last_error"] = sweep_error
            else:
                health["last_success_at"] = health["last_pass_at"]
            loop_health["fts_backfill"] = health
            if done:
                if indexed_total:
                    log.info("history-search backfill indexed %d doc(s)", indexed_total)
                # Idle, then re-check from the top for anything still unindexed
                # (a parked chunk, or history written while the index was off).
                # The health dict just published KEEPS the sweep's verdict for
                # the whole idle window; only the NEXT sweep starts clean.
                cursor = None
                sweep_error = None
                await asyncio.sleep(idle_delay)
            else:
                await asyncio.sleep(pause)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — never kill the daemon
            log.exception("history-search backfill pass failed")
            loop_health["fts_backfill"] = {
                "ok": False,
                "done": False,
                "indexed": indexed_total,
                "scanned": scanned_total,
                "last_error": f"{type(exc).__name__}: {exc}"[:300],
                "at": utcnow().isoformat(),
            }
            cursor = None
            await asyncio.sleep(idle_delay)


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
    # Task-kind schedules (v1.119.0) fire real agent sessions: the scheduler's
    # dispatcher lives in build_platform, so hand it the orchestrator here.
    platform.orchestrator = orchestrator
    # Goal contracts (v1.208.0): the engine shares THIS orchestrator so goal
    # iterations ride the same session machinery as everything else.
    platform.goal_engine._orch = orchestrator
    # Health of the background loops (auto-backup/autonomy/sentinel/inbound), so a
    # silent failure (e.g. backups failing) is visible in /diagnostics, not just
    # buried in the log. Keyed by loop name.
    loop_health: dict[str, dict[str, Any]] = {}

    def _tick(name: str, ok: bool, err: BaseException | None = None) -> None:
        """v1.226.0: ONE line per loop pass. Before this only auto_backup and
        fts_backfill reported; the other eight loops (autonomy, sentinels,
        calendar, inbound, lesson compaction, slack socket, fleet arm, the
        scheduler start) logged and vanished — a loop that failed every pass
        for a week was invisible on /diagnostics."""
        from ..core.ids import utcnow

        # Never raises: this runs inside every loop's except branch, so an
        # exception whose __str__ itself raises would escape it and kill the
        # loop this helper exists to keep visible.
        try:
            now = utcnow().isoformat()
            if ok:
                loop_health[name] = {"ok": True, "last_success_at": now}
                return
            try:
                detail = f"{type(err).__name__}: {err}" if err is not None else "failed"
            except Exception:  # noqa: BLE001 — __str__ raised; keep the type
                detail = f"{type(err).__name__}: <unprintable>"
            loop_health[name] = {"ok": False, "last_error": detail[:300], "at": now}
        except Exception:  # noqa: BLE001 — health bookkeeping never kills a loop
            loop_health[name] = {"ok": bool(ok), "last_error": "unrecordable"}

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
    from ..comm.threads import CommThreadStore
    from .chat_turn import run_chat_turn

    # FULL CHAT over comm (v1.136.0): the store that owns daemon-side chat
    # threads (identity → thread, atomic appends, chat.thread_updated), plus
    # the injected turn service — a chat-enabled destination runs the SAME
    # engine as POST /chat. ``personas`` is assigned below once the builtin
    # defaults dict exists (it is defined later in this factory).
    comm_thread_store = CommThreadStore(platform.engine, event_bus=platform.event_bus)

    # PENDING PROMPTS (v1.137.0): parked workflow runs become answerable from
    # the phone. The store is durable rows; ``answer_run`` (the atomic-claim
    # answer path, same semantics as POST /workflows/runs/{id}/answer) is
    # attached below once ``_spawn_bg`` exists.
    from ..comm.prompts import PendingPromptStore, handle_workflow_waiting

    pending_prompt_store = PendingPromptStore(platform.engine)

    inbound_poller = InboundPoller(
        platform.notifier,
        orchestrator,
        platform.engine,
        event_bus=platform.event_bus,
        command_interpreter=command_interpreter,
        reflex_router=reflex_router,
        thread_store=comm_thread_store,
        chat_turn=run_chat_turn,
        platform=platform,
        prompt_store=pending_prompt_store,
    )

    def _on_workflow_waiting(event: Any) -> None:
        """workflow.waiting → register answerable prompts on every chat-enabled
        identity's thread (+ send them the question). A sync bus handler (runs
        off the loop via to_thread); the body is fully guarded in comm/prompts."""
        handle_workflow_waiting(
            event,
            store=pending_prompt_store,
            notifier=platform.notifier,
            thread_store=comm_thread_store,
        )

    platform.event_bus.add_handler(_on_workflow_waiting)

    # CX-05 "inbound everything": the calendar trigger poller. Cheap to build (no
    # network); OFF unless calendar_trigger_enabled AND a secret ICS URL is stored
    # (its .enabled() gates loop creation, exactly like inbound_poller). Fires
    # `calendar` reflex rules for events coming due through the same gated path.
    from ..triggers import CalendarPoller

    calendar_poller = CalendarPoller(platform, reflex_router, platform.engine)
    # LOCAL FLEET telemetry sampler. Hybrid cadence: a slow background pass so
    # history exists the moment the page opens, speeding up while someone is
    # actually watching (GET /fleet touches the lease). Constructed here but NOT
    # started until lifespan — a bare create_app() in tests must never poll.
    from ..fleet.sampler import FleetSampler

    fleet_sampler = FleetSampler(
        platform.fleet,
        interval_idle=float(max(5, getattr(platform.config, "fleet_sampling_seconds", 30))),
    )

    # LIVE re-arm bridge: lifespan drops its event loop + the autonomy/sentinel
    # arm functions in here so put_settings (which runs in a threadpool) can
    # re-arm the background loops the moment a toggle changes — no restart.
    _live_rearm: dict[str, Any] = {}

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:  # start the cron scheduler when the daemon boots
            platform.scheduler.start()
            _tick("scheduler", True)
        except Exception as exc:  # noqa: BLE001 — never block boot (v1.226.0: but SAY so)
            log.exception("scheduler failed to start — schedules will not fire")
            _tick("scheduler", False, exc)
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
        # AFTER session reconciliation by contract: a goal stranded mid-iteration
        # reads its session's honest FAILED/interrupted verdict (v1.208.0).
        _rehydrate_step("rehydrate_goals", platform.goal_engine.rehydrate)
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

        def _prune_workflow_runs() -> None:
            """Run-history retention (v1.170.0): nothing ever deleted these rows
            and the bell polls the list app-wide. Boot-time sweep; live/waiting
            rows are untouchable and resumable `interrupted` rows age out only
            past the store's threshold (so a rendered Resume button doesn't
            404 the day after a restart)."""
            from ..workflows.store import prune_runs

            prune_runs(platform.engine, keep=500)

        _rehydrate_step("prune_workflow_runs", _prune_workflow_runs)
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

        # History-search backfill (v1.142.0): index the conversations that
        # already existed before this feature shipped. See _fts_backfill_loop
        # for the chunking / parked-chunk contract, and _backfill_index for the
        # lock-order history behind sharing ONE index with the write seams.
        # Disable with
        # IRONJARVIS_FTS_BACKFILL=off (the index still serves whatever live
        # sync has written — search just won't reach back in time).
        fts_task = None
        if (os.environ.get("IRONJARVIS_FTS_BACKFILL", "on").strip().lower()
                not in {"0", "false", "no", "off"}):
            fts_task = asyncio.create_task(
                _fts_backfill_loop(_backfill_index(platform), loop_health)
            )

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
                        _tick("lesson_compaction", True)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 - a pass must never kill the daemon
                        log.exception("lesson compaction pass failed")
                        _tick("lesson_compaction", False, exc)
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
                    _tick("autonomy", True)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - a tick must never kill the daemon
                    log.exception("autonomy deliberation tick failed")
                    _tick("autonomy", False, exc)
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
                    _tick("sentinels", True)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - a poll must never kill the daemon
                    log.exception("sentinel poll failed")
                    _tick("sentinels", False, exc)
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

        # CX-05 calendar trigger poll loop. GUARDED by calendar_poller.enabled()
        # (calendar_trigger_enabled + a stored ICS URL secret), OFF by default, so
        # the default install + tests create nothing. Mirrors the loops above:
        # rehydration is implicit (the fired-event cursor lives in the DB and is
        # read each pass), sleeps before the first poll (never blocks boot),
        # cancelled on shutdown via bg_tasks, and re-arms live on a settings
        # change. Disable explicitly via IRONJARVIS_CALENDAR=off.
        async def _calendar_loop() -> None:
            try:
                interval = max(30, int(platform.config.calendar_tick_seconds))
            except (TypeError, ValueError):
                interval = 300
            await asyncio.sleep(25)  # let boot settle before the first poll
            while True:
                try:
                    await calendar_poller.poll_once()
                    _tick("calendar", True)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - a poll must never kill the daemon
                    log.exception("calendar trigger poll failed")
                    _tick("calendar", False, exc)
                await asyncio.sleep(interval)

        def _arm_calendar() -> None:
            task = bg_tasks.pop("calendar", None)
            if task is not None:
                task.cancel()
            # v1.226.0: the enabled() probe reads a secret — a boot-time raise
            # here (mismatched key, bad row) aborted the whole lifespan. Same
            # posture as _arm_fleet: a probe failure disarms, never bricks.
            try:
                cal_enabled = bool(calendar_poller.enabled())
            except Exception:  # noqa: BLE001 — a probe never breaks boot
                log.exception("calendar trigger: enabled() probe failed; not armed")
                cal_enabled = False
            if (
                cal_enabled
                and os.environ.get("IRONJARVIS_CALENDAR", "on").strip().lower()
                not in {"0", "false", "no", "off"}
            ):
                bg_tasks["calendar"] = asyncio.create_task(_calendar_loop())
                log.info("calendar trigger loop (re)armed")
            elif task is not None:
                log.info("calendar trigger loop disarmed")

        _arm_calendar()

        # LOCAL FLEET sampler: start it only when telemetry is enabled AND at
        # least one node exists, so a fresh install with no local endpoints
        # never opens a socket. Re-armed live when the fleet settings change.
        def _arm_fleet() -> None:
            try:
                enabled = bool(getattr(platform.config, "fleet_sampling_enabled", True))
                has_nodes = bool(platform.fleet and platform.fleet.nodes())
                if enabled and has_nodes:
                    fleet_sampler.interval_idle = float(
                        max(5, getattr(platform.config, "fleet_sampling_seconds", 30))
                    )
                    bg_tasks["fleet"] = asyncio.create_task(fleet_sampler.start())
                    log.info("fleet sampler (re)armed")
                    _tick("fleet", True)
                else:
                    bg_tasks["fleet"] = asyncio.create_task(fleet_sampler.stop())
            except Exception as exc:  # noqa: BLE001 — telemetry never breaks boot
                log.debug("fleet sampler arm failed", exc_info=True)
                _tick("fleet", False, exc)

        _arm_fleet()

        # Expose the arm functions + this loop to put_settings (threadpool).
        _live_rearm["loop"] = asyncio.get_running_loop()
        # Comm thread appends can happen from sync route threads — hand the
        # store this loop so chat.thread_updated still publishes from there.
        comm_thread_store.set_loop(_live_rearm["loop"])

        # "This PC" destination (v1.118.0): DesktopChannel.send runs in sync
        # route threads, so its sink schedules the bus publish onto THIS loop
        # thread-safely. The dashboard's bridge turns the comm.desktop event
        # into a native OS toast via the Electron preload.
        from ..comm.channels import DesktopChannel

        def _desktop_sink(title: str, message: str) -> None:
            loop = _live_rearm.get("loop")
            if loop is None:
                raise RuntimeError("daemon loop not running")
            asyncio.run_coroutine_threadsafe(
                platform.event_bus.publish(
                    "comm.desktop", {"title": title, "message": message}
                ),
                loop,
            )

        DesktopChannel.sink = staticmethod(_desktop_sink)
        _live_rearm["autonomy"] = _arm_autonomy
        _live_rearm["sentinels"] = _arm_sentinels
        _live_rearm["calendar"] = _arm_calendar
        _live_rearm["fleet"] = _arm_fleet

        # Two-way comm inbound poller — the receive leg. The task is ALWAYS
        # created (v1.136.0 live re-arm) but each pass re-checks
        # poller.enabled() and IDLES when no channel is inbound-enabled +
        # credentialed — so enabling two-way from the Channels page starts
        # polling within one interval, NO RESTART (the old boot-only gate
        # meant a runtime toggle silently did nothing until restart). With
        # nothing enabled the pass is a cheap in-memory check: zero network,
        # zero sessions — the same posture the boot-only gate gave tests +
        # default installs. Disable the whole loop via IRONJARVIS_INBOUND=off
        # (the env kill-switch still gates task CREATION, unchanged).
        inbound_task = None
        if os.environ.get("IRONJARVIS_INBOUND", "on").strip().lower() not in {
            "0", "false", "no", "off",
        }:

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
                        # Idle-sleep re-arm: the enabled() verdict is LIVE —
                        # POST/DELETE /comm/channels changes what the next
                        # pass sees without touching this task.
                        if inbound_poller.enabled():
                            await inbound_poller.poll_once()
                            _tick("inbound", True)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 - a poll must never kill the daemon
                        log.exception("inbound comm poll failed")
                        _tick("inbound", False, exc)
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
            try:  # v1.226.0: a secret read in here must never abort boot
                _socket_enabled = bool(_socket.enabled())
            except Exception:  # noqa: BLE001 — a probe never breaks boot
                log.exception("slack socket mode: enabled() probe failed; not armed")
                _socket_enabled = False
            if _socket_enabled:
                slack_socket_stop = asyncio.Event()

                async def _slack_socket_loop() -> None:
                    await asyncio.sleep(15)  # let boot settle first
                    try:
                        _tick("slack_socket", True)  # armed and dialling out
                        await _socket.run(stop=slack_socket_stop)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 — never kill the daemon
                        log.exception("slack socket mode loop failed")
                        _tick("slack_socket", False, exc)

                slack_socket_task = asyncio.create_task(_slack_socket_loop())
        try:
            yield
        finally:
            _live_rearm.clear()  # daemon going down — no more live re-arms
            # Stop the session-queue governor FIRST (v1.166.0): cancelling a
            # running session below fires its slot-free hook, which would
            # otherwise promote a parked run and start a brand-new agent run
            # mid-shutdown. shutdown_queue() drains + closes parked coroutines.
            try:
                orchestrator.shutdown_queue()
            except Exception:  # noqa: BLE001 — shutdown never raises
                pass
            try:
                await fleet_sampler.stop()  # cancel cleanly, no pending-task warnings
            except Exception:  # noqa: BLE001 — shutdown never raises
                pass
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
            if fts_task is not None:
                fts_task.cancel()
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
            # v1.226.0: last, once nothing above can write any more — checkpoint
            # the WAL, then release every pooled connection. Each best-effort.
            try:
                _checkpoint_wal(platform.engine)
            except Exception:  # noqa: BLE001 — shutdown never raises
                log.debug("wal checkpoint at shutdown failed", exc_info=True)
            try:
                platform.engine.dispose()
            except Exception:  # noqa: BLE001 — shutdown never raises
                pass

    app = FastAPI(title="Iron Jarvis", version=__version__, lifespan=lifespan)
    # Optional bearer-token auth (env IRONJARVIS_TOKEN) — required for a public
    # deployment; no-op locally.
    from .auth import (
        BodyLimitMiddleware,
        ErrorEnvelopeMiddleware,
        HostOriginGuardMiddleware,
        TokenAuthMiddleware,
    )

    # FIRST = innermost (add_middleware stacks outermost-last), so the JSON 500
    # it produces passes back OUT through CORSMiddleware and gets its headers.
    # Without this, an unhandled error is served by ServerErrorMiddleware —
    # outside CORS — and the browser cannot read it, so every 500 in the app
    # surfaced to the user as "daemon offline". See the class docstring.
    app.add_middleware(ErrorEnvelopeMiddleware)

    app.add_middleware(TokenAuthMiddleware)  # inner: token check
    # Reject an oversized request body (413) before it is buffered — DoS guard.
    # ADDED HERE, INSIDE CORS, DELIBERATELY (v1.195.0). It used to be added after
    # the CORS block, which made it OUTERMOST-but-one, so its 413 went back to the
    # browser with NO access-control-allow-origin — the browser then refuses to
    # let the page read the response at all, `fetch` rejects, and `lib/api.ts`
    # maps that to `ApiError(status=0)`, which every page renders as "daemon
    # offline". That is the identical failure ErrorEnvelopeMiddleware above was
    # written to fix, and it went unnoticed because the guard only ever fired on
    # a content-length the dashboard never sent. Now that the chunked path is
    # covered too, a user dropping an oversized file would have been told their
    # daemon was down instead of that the file is too big.
    # Still OUTSIDE TokenAuthMiddleware, so an unauthenticated oversized body is
    # refused before the token check and before the body is buffered — the DoS
    # property this guard exists for is unchanged.
    app.add_middleware(BodyLimitMiddleware)
    # CORS: default to loopback dashboard origins ONLY (never wildcard, since the
    # daemon is RCE-by-design); a public deployment sets IRONJARVIS_CORS_ORIGINS.
    _origins = os.environ.get("IRONJARVIS_CORS_ORIGINS", "").strip()
    # PATCH is required for the autonomy goal controls (PATCH /autonomy/goals/{id} — the
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

    from fastapi.exceptions import RequestValidationError

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):  # noqa: ANN202
        # v1.226.0 (contract C4): pydantic's list-of-dicts detail rendered as
        # "[object Object]" in every dashboard error note. Flatten it to the
        # STRING shape every other error envelope in this app uses:
        # "<field>: <msg>; <field>: <msg>" (the leading body/query/path token
        # of each location is dropped — it names the transport, not the field).
        parts: list[str] = []
        for err in exc.errors():
            loc = [str(x) for x in (err.get("loc") or ())]
            if loc and loc[0] in ("body", "query", "path", "header", "cookie"):
                loc = loc[1:]
            parts.append(f"{'.'.join(loc) or 'request'}: {err.get('msg', 'invalid')}")
        return JSONResponse(
            status_code=422, content={"detail": "; ".join(parts) or "invalid request"}
        )

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
    # Also reachable from platform-bound tools (v1.122.0): the agent-facing
    # webhook_add must install a reflex-firing handler like the route does.
    platform.reflex_router = reflex_router
    app.state.command_interpreter = command_interpreter
    # v1.136.0 messaging surfaces: reachable for tests + diagnostics (the
    # routes go through ``d``; TestClient callers only have app.state).
    app.state.inbound_poller = inbound_poller
    app.state.comm_thread_store = comm_thread_store
    app.state.pending_prompt_store = pending_prompt_store
    # Background session tasks are registered on the orchestrator keyed by
    # session_id (a strong ref preventing premature GC, and the handle the
    # cancel endpoint uses). Exceptions are surfaced (logged), not swallowed.
    # v1.166.0: routed through the orchestrator's concurrency governor —
    # under `max_concurrent_sessions` (0 = unlimited, the default) this is
    # byte-identical to the old create_task+register; at the limit the run is
    # parked FIFO and the session marked QUEUED. Returns None when parked.
    def _spawn_bg(session_id: str, coro) -> "asyncio.Task | None":
        return orchestrator.spawn_managed(session_id, coro)

    # The Reflex Router launches its long-running actions through the daemon's
    # background task launcher (now that it exists), so a webhook POST returns
    # immediately while the bound workflow/session runs in the background.
    reflex_router.spawn_bg = _spawn_bg

    # PENDING PROMPTS: the poller's answer path — the exact atomic-claim +
    # background-resume semantics of POST /workflows/runs/{id}/answer, with
    # the resume launched through the same _spawn_bg the route uses.
    async def _answer_parked_run(run_id: str, answer: str) -> dict[str, Any]:
        from ..comm.prompts import answer_parked_run

        return await answer_parked_run(platform, orchestrator, _spawn_bg, run_id, answer)

    inbound_poller.answer_run = _answer_parked_run

    def _visible_providers() -> list[dict[str, Any]]:
        """Provider health with the internal 'mock' offline model hidden.

        'mock' is the load-bearing offline fallback + the autopromote sentinel,
        so it stays in the ENGINE — but it must not surface as a selectable
        model/tile in the UI (pickers, connections, the switcher). Filtered here
        (and in /models + /connections) rather than removed from the registry."""
        return [p for p in platform.providers.health() if p.get("provider") != "mock"]

    # --- Chat (direct conversation — frontier-chat parity) -----------------

    # The built-in catalog now lives in personas/builtins.py (v1.144.0) so the
    # agent runtime + round table can resolve the user's default persona too —
    # a dict local to this factory was reachable only from HTTP routes. Same
    # object, same `d._PERSONAS` exposure: nothing downstream changes.
    _PERSONAS: dict[str, dict[str, str]] = BUILTIN_PERSONAS

    # The inbound poller's full-chat turns resolve personas from the SAME
    # builtin defaults POST /chat uses (constructed above, before this dict
    # existed — completed here).
    inbound_poller.personas = _PERSONAS

    # --- Voice (server-side dictation fallback) ---------------------------
    # The dashboard prefers the browser's Web Speech engine (free, streaming),
    # but the packaged Electron app has none — these endpoints give the desktop
    # app working dictation via a connected transcription-capable backend.

    _VOICE_MAX_BYTES = 25 * 1024 * 1024  # OpenAI's audio upload cap

    def _voice_backend() -> tuple[str, str, str | None] | None:
        """First available speech-to-text backend as (label, url, api_key).

        Preference order: (1) a DEDICATED transcription endpoint
        (``voice_transcribe_base_url`` — a real whisper server, separate from the
        chat endpoint); (2) an OpenAI API KEY (a ChatGPT OAuth token is
        deliberately NOT used — the audio API rejects account tokens); (3) the
        ``custom`` chat endpoint (works ONLY if it actually serves
        /v1/audio/transcriptions with a whisper model — an Ollama LLM endpoint
        does not). None = no backend, be honest.
        """
        def _v1(raw: str) -> str:
            u = raw.strip().rstrip("/")
            if u.endswith("/chat/completions"):
                u = u[: -len("/chat/completions")]
            if not u.endswith("/v1"):
                u += "/v1"
            return u

        # (1) dedicated STT endpoint wins — the self-hosted-whisper path.
        stt = (getattr(platform.config, "voice_transcribe_base_url", None) or "").strip()
        if stt:
            try:
                skey = platform.secrets.get("voice_transcribe_key")
            except Exception:  # noqa: BLE001
                skey = None
            # Fall back to the custom endpoint's key if the STT endpoint shares it.
            if not skey:
                try:
                    skey = platform.secrets.get("custom_api_key")
                except Exception:  # noqa: BLE001
                    skey = None
            return ("stt", _v1(stt) + "/audio/transcriptions", skey)
        # (2) OpenAI key.
        try:
            key = platform.secrets.get("openai_api_key")
        except Exception:  # noqa: BLE001 - vault miss = not available
            key = None
        if key:
            return ("openai", "https://api.openai.com/v1/audio/transcriptions", key)
        # (3) the custom chat endpoint (may or may not serve transcription).
        base = (getattr(platform.config, "custom_base_url", None) or "").strip()
        if base:
            try:
                ckey = platform.secrets.get("custom_api_key")
            except Exception:  # noqa: BLE001
                ckey = None
            return ("custom", _v1(base) + "/audio/transcriptions", ckey)
        return None

    # --- Bundled OFFLINE speech-to-text (Vosk streaming) --------------------
    # A fully local, real-time dictation backend that ships WITH the desktop app:
    # no API key, no server, no internet. The renderer streams 16 kHz mono PCM
    # over /voice/stream and gets live partial + final text back — the same feel
    # as the browser's speech engine, which the packaged Electron app can't use.
    # OFF unless a model is present (vosk installed + a model dir), so the default
    # source install + the offline test suite are untouched.
    _vosk_state: dict[str, Any] = {}

    def _vosk_model_path() -> str | None:
        """Directory of the bundled/configured Vosk model, or None.

        Delegates to :func:`iron_jarvis.voice.vosk_model_path` — the single
        source of truth shared with the onboarding checklist (v1.197.0). The
        logic used to live inline here, which let the checklist's own copy
        drift into telling desktop users to buy an OpenAI key for a voice
        feature that already worked offline. One function, two callers, no
        second copy to shadow it."""
        from ..voice import vosk_model_path

        return vosk_model_path(platform.config)

    def _vosk_model() -> Any:
        """Lazily load + cache the Vosk model (~1s, ~40 MB RAM). None when
        unavailable (vosk not installed / no model) — the streaming path then
        reports unavailable and nothing else in the daemon is affected."""
        path = _vosk_model_path()
        if not path:
            return None
        if _vosk_state.get("model") is not None and _vosk_state.get("path") == path:
            return _vosk_state["model"]
        try:
            import vosk

            vosk.SetLogLevel(-1)
            model = vosk.Model(path)
        except Exception:  # noqa: BLE001 — missing native lib / bad model dir
            log.exception("vosk model load failed for %r", path)
            return None
        _vosk_state.update(model=model, path=path)
        return model

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

            def _mark_written() -> None:  # v1.226.0: SQLite write off the loop
                with session_scope(platform.engine) as db:
                    row = db.get(LiveDocRecord, doc_id)
                    row.path = str(path)
                    row.updated_at = _now()
                    row.last_error = ""
                    db.add(row)
                    db.commit()

            await asyncio.to_thread(_mark_written)
            return {"id": doc_id, "path": str(path), "ok": True}
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 — record the failure honestly
            err_text = f"{type(exc).__name__}: {exc}"[:300]

            def _mark_failed() -> None:  # v1.226.0: SQLite write off the loop
                with session_scope(platform.engine) as db:
                    row = db.get(LiveDocRecord, doc_id)
                    if row is not None:
                        row.last_error = err_text
                        db.add(row)
                        db.commit()

            await asyncio.to_thread(_mark_failed)
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
            # A LOCAL ENDPOINT THAT NEVER ANSWERED REFUSES — it never fails over.
            # The v1.162.0 privacy rule was enforced only inside ModelRouter,
            # but these one-shot utilities (terminal assist, the workflow
            # builder, livedoc, skill distill, compaction) call the adapter
            # DIRECTLY and then walk _failover_candidates — so a down Ollama /
            # LM-Studio / fleet node still shipped the payload to the first
            # connected CLOUD provider. Every transport-shaped local failure
            # reads TRANSIENT by type, so the guard has to sit ABOVE the
            # transient branch. It delegates to the router's OWN predicate
            # (_refuses_failover -> local_failure_kind: unreachable / timeout /
            # interrupted) rather than re-deriving one here: is_unreachable_error
            # alone would catch only connect-shaped errors and let the MORE
            # COMMON local failure — a box that is up but cold-loading a 30B/70B
            # past the adapter's 60s read timeout — fall through to the cloud.
            # The kind rides along so the refusal cannot claim "isn't connected"
            # about an endpoint that demonstrably accepted the connection.
            # Same predicate, same event and same wording as the router
            # (router.py complete()/stream()), so all three paths behave
            # identically.
            router = platform.router
            primary = getattr(adapter, "provider", "") or provider
            refusal = router._refuses_failover(primary, exc)
            if refusal:
                await router._publish_not_connected(primary, None, kind=refusal)
                raise HTTPException(
                    status_code=502,
                    detail=str(
                        router._unavailable_error(primary, False, kind=refusal)
                    ),
                ) from exc
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

    # --- Skill learning (v1.135.0): distill glue + proposal event ---------

    def _skill_distill_complete():
        """The REAL-provider ``complete`` callable a skill distill sweep rides,
        or ``None`` when only the offline mock is available. Crystallize's
        honest-mock rule: a fabricated skill draft would poison future runs, so
        mock-only installs get None (the handler skips silently; the manual
        route 400s honestly). The returned callable goes through
        ``_one_shot_complete`` — retry-on-transient + cross-provider failover,
        the one-shot hard rule."""
        from ..providers.adapters.mock import MockLLMAdapter

        provider = platform.config.default_provider
        model = platform.config.default_model
        try:
            adapter = platform.providers.get(provider, model)
        except Exception:  # noqa: BLE001 — fall through to failover
            adapter = None
        if adapter is None or isinstance(adapter, MockLLMAdapter):
            adapter, provider = _failover_adapter("mock")
        if adapter is None:
            return None

        from ..providers.adapters.base import LLMMessage

        async def _complete(system: str, prompt: str) -> str:
            resp, _p, _m = await _one_shot_complete(
                provider,
                adapter,
                system=system,
                messages=[LLMMessage(role="user", content=prompt)],
            )
            return resp.text or ""

        return _complete

    def _compaction_complete(provider: str = "", model: str = ""):
        """The ``complete`` callable context COMPACTION rides (v1.153.0), or
        ``None`` when only the offline mock is available.

        Same honest-mock rule as ``_skill_distill_complete`` and for a sharper
        reason: a compaction summary is injected into the SYSTEM prompt of every
        later turn, so mock prose would not merely be useless, it would be read
        back as an authoritative account of the conversation. With no real model
        the caller keeps the deterministic recap, which is exactly what shipped
        before this feature existed.

        Returns ``async (system, user) -> (text, provider, model)`` so the
        record can attribute the summary to the model that actually wrote it —
        including after a cross-provider failover.
        """
        from ..providers.adapters.mock import MockLLMAdapter

        prov = (provider or "").strip() or platform.config.default_provider
        mdl = (model or "").strip() or platform.config.default_model
        try:
            adapter = platform.providers.get(prov, mdl)
        except Exception:  # noqa: BLE001 — fall through to failover
            adapter = None
        if adapter is None or isinstance(adapter, MockLLMAdapter):
            adapter, prov = _failover_adapter("mock")
        if adapter is None:
            return None

        from ..providers.adapters.base import LLMMessage

        async def _complete(system: str, user: str):
            resp, used_provider, used_model = await _one_shot_complete(
                prov,
                adapter,
                system=system,
                messages=[LLMMessage(role="user", content=user)],
            )
            return (resp.text or ""), (used_provider or prov), (used_model or mdl)

        return _complete

    # The agent runtime compacts its own transcript mid-run and cannot reach
    # the deps object, so the factory is published on the PLATFORM. A bare
    # AgentRuntime in a unit test finds nothing here and skips compaction.
    platform._compaction_complete = _compaction_complete

    def _publish_skill_proposal(record) -> None:
        """``on_proposal`` callback: publish ``skill.proposal_created`` so the
        dashboard event feed + Notifications routing can deliver it.
        Best-effort: normally called on the daemon loop (the distill sweep is
        an asyncio task), falls back to the lifespan loop for a foreign-thread
        caller, and drops silently when no loop exists (bare create_app in
        unit tests — the engine logs callback failures itself)."""
        from ..core.events import EventType

        coro = platform.event_bus.publish(
            EventType.SKILL_PROPOSAL_CREATED,
            {
                "proposal_id": record.id,
                "kind": record.kind,
                "skill_name": record.skill_name,
                # status "approved" means the explicit auto-approve setting
                # already wrote the skill to disk — the alert should say so.
                "auto": record.status == "approved",
            },
        )
        try:
            asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            loop = _live_rearm.get("loop")
            if loop is not None and loop.is_running():
                asyncio.run_coroutine_threadsafe(coro, loop)
            else:
                coro.close()  # no loop to publish on — best-effort by design

    if platform.skill_learning is not None:
        platform.skill_learning.on_proposal = _publish_skill_proposal

    def _on_skill_session_completed(event: Any) -> None:
        """SESSION_COMPLETED → schedule a distill sweep (the _on_livedoc_event
        precedent: a SYNC bus handler that schedules async work). Fully guarded
        so it can never break the event bus. Under a mock-only install the
        sweep exits before any model call — never fabricate. Overlapping
        sweeps are debounced by the engine itself."""
        try:
            from ..core.events import EventType

            etype = getattr(event, "type", None) or (
                event.get("type") if isinstance(event, dict) else None
            )
            if etype != EventType.SESSION_COMPLETED:
                return
            engine_ = getattr(platform, "skill_learning", None)
            if engine_ is None:
                return
            if not bool(getattr(platform.config, "skill_learning_enabled", True)):
                return

            async def _sweep() -> None:
                try:
                    complete = _skill_distill_complete()
                    if complete is None:
                        return  # mock-only — candidates keep queueing offline
                    await engine_.distill_candidates(complete)
                except Exception:  # noqa: BLE001 — a sweep must never surface
                    log.exception("skill distill sweep failed")

            # Sync handlers run off the loop (bus._dispatch → to_thread), so
            # hop onto the daemon loop thread-safely; without one (bare
            # create_app in unit tests) skip silently — _on_livedoc_event's rule.
            try:
                asyncio.get_running_loop().create_task(_sweep())
            except RuntimeError:
                loop = _live_rearm.get("loop")
                if loop is not None and loop.is_running():
                    asyncio.run_coroutine_threadsafe(_sweep(), loop)
        except Exception:  # noqa: BLE001 — the bus must survive any handler
            log.exception("skill-learning session handler failed")

    platform.event_bus.add_handler(_on_skill_session_completed)

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
            # ADOPT the failover adapter, don't merely test for one. This copy
            # used to bind the pair to ``_alt_*`` and then call the MOCK anyway,
            # so an install with a real provider connected but ``default_provider
            # = "mock"`` fed the request to the mock, whose non-JSON reply 422s
            # with "try rephrasing" — blaming the user while a working provider
            # sat unused. The sibling routes (share_chat_thread /
            # crystallize_chat_thread / _skill_distill_complete /
            # _compaction_complete) all reassign; this one had drifted.
            adapter, provider = _failover_adapter("mock")
            if adapter is None:
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
        # stray prose, fenced blocks, and braces inside step text), and pass
        # the steps through the SAME sanitizer chat's draft card uses — one
        # hardened shape (deduped names, bounded fields, the engine's own
        # kind/on_failure vocabularies) whichever door a workflow came in by.
        from .chat_turn import _sanitize_draft

        def _steps_from(text: str) -> list[dict[str, Any]]:
            try:
                wf_obj = _extract_workflow_json(text or "")
            except Exception:  # noqa: BLE001 — no parseable object (garbled/truncated)
                return []
            draft = _sanitize_draft(
                {"name": wf_obj.get("name") or name or "x", "steps": wf_obj.get("steps")}
            )
            if draft is None:
                return []
            _steps_from.obj = wf_obj  # type: ignore[attr-defined]
            return draft["steps"]

        _steps_from.obj = {}  # type: ignore[attr-defined]
        steps = _steps_from(resp.text or "")
        if not steps:
            # ONE REPAIR ROUND (v1.225.0). Local models answer the design
            # prompt with a numbered list or an explanation first, then the
            # JSON — or only the list. Hand the reply back and ask for the
            # exact shape once, instead of 422-ing "try rephrasing" at the
            # user, whose phrasing was never the problem. The repair is
            # deterministic in intent: it may only RESTATE what the reply
            # already said, never add steps.
            repair_system = (
                "Convert the workflow described below into ONLY a JSON object "
                "(no prose, no code fence) of the exact shape "
                '{"name": "kebab-case-name", "description": "one line", '
                '"steps": [{"name": "Step name", "agent": "builder", "task": '
                '"instruction", "tool": null}]}. agent MUST be one of: builder, '
                "planner, researcher, reviewer, supervisor. Keep every step the "
                "text describes; add none."
            )
            try:
                resp2, _p2, _m2 = await _one_shot_complete(
                    provider,
                    adapter,
                    system=repair_system,
                    messages=[LLMMessage(role="user", content=(resp.text or "")[:8000])],
                )
                steps = _steps_from(resp2.text or "")
            except Exception:  # noqa: BLE001 — the repair is best-effort
                steps = []
        if not steps:
            raise HTTPException(
                status_code=422,
                detail=(
                    "the model did not return a valid workflow (no steps could be "
                    "read from its reply, even after asking it to restate them as "
                    "JSON) — try again, or describe the steps one per line"
                ),
            )
        wf = _steps_from.obj  # type: ignore[attr-defined]

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
            {"key": "inbound_enabled", "label": "Enable two-way (true/false)", "secret": False,
             "help": "Set to true so Iron Jarvis listens for messages sent to "
                     "the bot (commands like /status, and chat when enabled "
                     "below). Off by default."},
            {"key": "chat_enabled", "label": "Chat with Iron Jarvis (true/false)", "secret": False,
             "help": "Set to true to hold a real conversation with Iron Jarvis "
                     "from this destination — replies remember the thread, and "
                     "the whole conversation shows up live on the desktop. "
                     "Chat implies listening: turning this on also turns "
                     "two-way ON when saved."},
            {"key": "allowed_senders", "label": "Allowlist (Telegram user IDs)", "secret": False,
             "help": "Comma-separated numeric user ids allowed to talk to the "
                     "bot (message @userinfobot to find yours). FAIL-CLOSED: an "
                     "empty allowlist means NOBODY may command or chat."},
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

    # Pre-formatted app manifest (JSON — the format Slack's "Create New App →
    # From an app manifest" editor now expects; it dropped the YAML tab): paste it
    # at api.slack.com/apps and every required scope/setting lands in one step.
    # (Socket Mode is on, so Iron Jarvis dials OUT to Slack — two-way DMs with no
    # public URL; create an App-Level Token with connections:write after install.)
    _CHANNEL_MANIFESTS = {
        "slack": """{
  "display_information": {
    "name": "Iron Jarvis",
    "description": "Notifications and two-way chat from your local Iron Jarvis",
    "background_color": "#0a0c11"
  },
  "features": {
    "bot_user": {
      "display_name": "Iron Jarvis",
      "always_online": true
    }
  },
  "oauth_config": {
    "scopes": {
      "bot": [
        "chat:write",
        "chat:write.public",
        "channels:history",
        "im:history",
        "im:write"
      ]
    }
  },
  "settings": {
    "event_subscriptions": {
      "bot_events": [
        "message.im"
      ]
    },
    "org_deploy_enabled": false,
    "socket_mode_enabled": true,
    "token_rotation_enabled": false
  }
}""",
    }

    # --- External MCP servers (prebuilt catalog + custom) ------------------

    #: Curated, known-good MCP servers (npx-based, cross-platform). The
    #: placeholders in `args` are filled by the user in the UI.
    # Curated, one-click MCP servers. Entries are the servers we can vouch for a
    # RELIABLE run command for: the maintained official reference servers
    # (github.com/modelcontextprotocol/servers) + a few company-official
    # integrations. Anything else is covered by the "add from npm / GitHub" flow
    # on the Tools page, so we never ship a guessed (broken) command here.
    # `needs` names the prerequisite runtime for the plain-language UI hint;
    # `category` groups the cards ("reference" vs "integration"). `<...>` args are
    # placeholders the UI collects before connecting.
    _MCP_CATALOG = [
        # --- Official reference servers (maintained) ---
        {
            "id": "filesystem",
            "name": "Files & folders",
            "description": "Let agents read and write files inside a folder you choose. Official reference server.",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "<folder-path>"],
            "category": "reference",
            "needs": "Node",
        },
        {
            "id": "fetch",
            "name": "Fetch web pages",
            "description": "Pull a web page and hand the agent clean, readable text. Official reference server.",
            "command": "uvx",
            "args": ["mcp-server-fetch"],
            "category": "reference",
            "needs": "Python (uv)",
        },
        {
            "id": "memory",
            "name": "Long-term memory",
            "description": "A persistent knowledge graph the agent can remember things in across chats. Official reference server.",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-memory"],
            "category": "reference",
            "needs": "Node",
        },
        {
            "id": "sequentialthinking",
            "name": "Step-by-step reasoning",
            "description": "A scratchpad that lets the agent work through hard problems in explicit steps. Official reference server.",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-sequentialthinking"],
            "category": "reference",
            "needs": "Node",
        },
        {
            "id": "git",
            "name": "Git repositories",
            "description": "Read the history, diffs, and branches of a local git repo. Official reference server.",
            "command": "uvx",
            "args": ["mcp-server-git", "--repository", "<repo-path>"],
            "category": "reference",
            "needs": "Python (uv)",
        },
        {
            "id": "everything",
            "name": "Demo / connection test",
            "description": "Exercises every MCP feature — handy to confirm the plumbing works before adding a real one. Official reference server.",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-everything"],
            "category": "reference",
            "needs": "Node",
        },
        # --- Popular integrations (company-official) ---
        {
            "id": "github",
            "name": "GitHub",
            "description": "Search repos and read/open issues and pull requests. Needs a GitHub personal access token.",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            "env_keys": ["GITHUB_PERSONAL_ACCESS_TOKEN"],
            "category": "integration",
            "needs": "Node",
        },
        {
            "id": "playwright",
            "name": "Browser control (Playwright)",
            "description": "Drive a real browser — open pages, click, fill forms, screenshot. Microsoft's official Playwright MCP.",
            "command": "npx",
            "args": ["-y", "@playwright/mcp@latest"],
            "category": "integration",
            "needs": "Node",
        },
        {
            "id": "box",
            "name": "Box (cloud files)",
            "description": "Search, read, and manage files in Box. Box's official server — get a Developer Token from a Box custom app (developer.box.com).",
            "command": "uvx",
            "args": ["mcp-server-box"],
            "env_keys": ["BOX_CLIENT_ID", "BOX_CLIENT_SECRET"],
            "category": "integration",
            "needs": "Python (uv)",
        },
    ]

    # --- Domain route modules (routes/) -------------------------------------
    # Handlers moved out of this factory VERBATIM; ``d`` carries the
    # closure-local state their bodies resolve at request time. Register
    # order preserves the original within-prefix route order.
    from types import SimpleNamespace

    from . import routes as _routes

    # Memory housekeeping (v1.143.0): the SUGGEST-ONLY proposal store the review
    # card and the steward file into. Built once here so every request shares an
    # instance (and the table is ensured at boot, not on first click); the route
    # module builds its own if this field is ever missing.
    try:
        from ..memory.proposals import MemoryProposalStore as _MemoryProposalStore

        _memory_proposals = _MemoryProposalStore(
            platform.engine, ltm=platform.ltm, home=platform.config.home
        )
    except Exception:  # noqa: BLE001 — a review card must never block boot
        _memory_proposals = None

    d = SimpleNamespace(
        platform=platform,
        orchestrator=orchestrator,
        # v1.226.0 (contract C5): one random uid per create_app, echoed by
        # /health so the desktop shell can tell its own daemon from a stale one.
        instance=uuid.uuid4().hex,
        loop_health=loop_health,
        inbound_poller=inbound_poller,
        comm_thread_store=comm_thread_store,
        _live_rearm=_live_rearm,
        _loopback_servers=_loopback_servers,
        _spawn_bg=_spawn_bg,
        _visible_providers=_visible_providers,
        _PERSONAS=_PERSONAS,
        _VOICE_MAX_BYTES=_VOICE_MAX_BYTES,
        _voice_backend=_voice_backend,
        _vosk_model=_vosk_model,
        _vosk_model_path=_vosk_model_path,
        _regenerate_livedoc=_regenerate_livedoc,
        _rescan_skills=_rescan_skills,
        _PROMOTE_DEFAULT_MODEL=_PROMOTE_DEFAULT_MODEL,
        _maybe_autopromote_default=_maybe_autopromote_default,
        _cu_status=_cu_status,
        _failover_adapter=_failover_adapter,
        _one_shot_complete=_one_shot_complete,
        _skill_distill_complete=_skill_distill_complete,
        _compaction_complete=_compaction_complete,
        _build_workflow=_build_workflow,
        _goal_view=_goal_view,
        _proposal_view=_proposal_view,
        _persist_config=_persist_config,
        _sentinel_view=_sentinel_view,
        _CHANNEL_TYPE_FIELDS=_CHANNEL_TYPE_FIELDS,
        _CHANNEL_MANIFESTS=_CHANNEL_MANIFESTS,
        _MCP_CATALOG=_MCP_CATALOG,
        # Local fleet: the registry lives on the platform (providers close over
        # it); the sampler is daemon-owned because it needs the event loop.
        fleet=platform.fleet,
        fleet_sampler=fleet_sampler,
        # History search (v1.142.0): the ONE shared index (built in
        # build_platform, capability probe already warmed).
        search_index=getattr(platform, "search_index", None),
        # Memory housekeeping (v1.143.0): the shared suggest-only proposal store.
        memory_proposals=_memory_proposals,
    )
    # search FIRST: nothing else claims a /search prefix today, and registering
    # ahead of every other module makes it impossible for a future
    # ``GET /search/{...}`` catch-all to shadow ``/search/history`` (the
    # /skills/learning lesson below). Pair S4 detects a 404 to switch its
    # palette lane off — see routes/search.py's degradation contract.
    _routes.search.register(app, d)
    _routes.chat.register(app, d)
    _routes.projects.register(app, d)
    _routes.envelope.register(app, d)
    _routes.fsbrowse.register(app, d)
    _routes.helpdocs.register(app, d)
    _routes.guide.register(app, d)
    _routes.voice.register(app, d)
    _routes.sessions.register(app, d)
    _routes.documents.register(app, d)
    # memory_review BEFORE learning: learning.py owns GET /memory/{layer}/{key},
    # and a literal path registered after a same-prefix catch-all is exactly the
    # shape that once swallowed /skills/learning. Pinned by a test.
    _routes.memory_review.register(app, d)
    _routes.profile.register(app, d)
    _routes.learning.register(app, d)
    _routes.computeruse.register(app, d)
    _routes.terminals.register(app, d)
    _routes.workflows.register(app, d)
    _routes.autonomy.register(app, d)
    _routes.goals.register(app, d)
    _routes.settings.register(app, d)
    _routes.knowledge.register(app, d)
    _routes.codelab.register(app, d)
    _routes.creative.register(app, d)
    _routes.connections.register(app, d)
    _routes.connectors.register(app, d)
    _routes.routing.register(app, d)
    _routes.comm.register(app, d)
    # skill_learning BEFORE agents: agents.py's GET /skills/{name} catch-all
    # would otherwise swallow the literal /skills/learning path.
    _routes.skill_learning.register(app, d)
    _routes.agents.register(app, d)
    _routes.reflex.register(app, d)
    _routes.triggers.register(app, d)
    _routes.audit.register(app, d)
    _routes.undo.register(app, d)
    _routes.fleet.register(app, d)
    _routes.system.register(app, d)
    # Worklist (v1.174.0): the durable per-item checkpoints a chunked
    # job reports progress through — without this the store exists and
    # no surface can read it.
    from ..worklist import register as _register_worklist

    _register_worklist(app, d)
    # Capability requests (v1.178.0): the queue an agent files into when the app
    # has no verb for the job — without this the store exists, the tool files
    # rows, and nothing the user can reach ever shows them.
    from ..capability import register as _register_capability

    _register_capability(app, d)
    return app
