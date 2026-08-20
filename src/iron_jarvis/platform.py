"""Platform wiring — assembles every subsystem into one object.

This is the composition root the Daemon and CLI build once. It owns mutable
global state (§9): config, event bus, persistence, providers/router, tool
registry, and the permission engine.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
from dataclasses import dataclass
from pathlib import Path

import httpx
from sqlalchemy import Engine

from .core.config import Config, load_config
from .codelab.store import CodeArtifactStore
from .core.db import open_db, persist_event, search_index as shared_search_index
from typing import TYPE_CHECKING

from .core.db import session_scope
from .core.events import EventBus
from .core.streams import StreamHub

if TYPE_CHECKING:  # annotation only — the namespace is optional at runtime and
    # is imported lazily inside build_platform so a platform that cannot start
    # it still boots (see the ReplRegistry block below).
    from .repl.session import ReplRegistry
from .core.fs_policy import register_protected_root
from .core.logging import get_logger
from .providers.manager import ProviderManager
from .providers.router import ModelRouter
from .providers.vault import BrowserVault
from .tools.builtins import default_registry
from .tools.dynamic import DynamicToolRegistry, dynamic_tool_tools
from .tools.permissions import AskResolver, PermissionEngine
from .tools.registry import ToolRegistry

# Subsystem imports. Importing the model-bearing packages at module load time
# registers their SQLModel tables on the shared metadata BEFORE init_db runs.
from .agents.consult_tool import ConsultTool
from .agents.delegate_tool import DelegateTool
from .artifacts.store import ArtifactStore
from .eval.evaluation import Evaluator
from .eval.observability import Observability
from .memory.layers import MemoryLayers
from .memory.tools import memory_tools
from .sandbox.shell_tool import SandboxedShellTool
from .skills import SkillLearningEngine, SkillRegistry, skill_tools
from .workflows import models as _wf_models  # noqa: F401  (registers WorkflowRunRecord)

# Robust feature set (each importing its package registers any SQLModel tables).
from .agents import dynamic_models as _dyn_models  # noqa: F401
from .agents import remote as _remote_models  # noqa: F401  (registers RemoteAgentRecord)
from .agents.remote import register_remote_agent_tool
from .agents.agent_tools import agent_management_tools
from .agents.dynamic import DynamicAgentRegistry
from .blackboard import BlackboardStore, blackboard_tools
from .blackboard import models as _bb_models  # noqa: F401  (registers BlackboardRecord)
from .worklist import WORKLIST_TOOL_NAMES, WorklistStore, worklist_tools
from .worklist import models as _wl_models  # noqa: F401  (registers WorklistItem)
from .comm import Notifier, build_notifier, httpx_get, httpx_post, notify_tools
from .comm import models as _comm_models  # noqa: F401  (registers InboundOffsetRecord)
from .filesearch import FileSearchService, filesearch_tools
from .integrations import IntegrationRegistry, integration_tools
from .integrations import models as _intg_models  # noqa: F401
from .integrations.builtin import register_builtins
from .ltm import (
    LongTermMemory,
    MarkdownBrainConnector,
    NotionConnector,
    ObsidianConnector,
    load_custom_sources,
    ltm_tools,
)
from .ltm import sources as _ltm_sources  # noqa: F401  (registers LTMSourceRecord)
from .memory.embeddings import build_embedder
from .memory.fabric import MemoryFabric
from .memory.recall import recall_tools
from .search import SearchIndex
from .search.tools import history_search_tools
from .scheduling import Scheduler
from .scheduling import models as _sched_models  # noqa: F401
from .templates import MEMORY_REVIEW_SCHEDULE
from .reflex import models as _reflex_models  # noqa: F401  (registers ReflexRule)
from .personas import models as _persona_models  # noqa: F401  (registers PersonaRecord)
from .sentinels import SentinelService, sentinel_tools
from .sentinels import models as _sentinel_models  # noqa: F401
from .secrets import SecretsManager, secret_tools
from .secrets import models as _sec_models  # noqa: F401
from .webhooks import InboundWebhooks, OutboundWebhooks
from .webhooks import models as _whk_models  # noqa: F401

# Documents (all file types) + self-correcting learning loop.
from .documents import document_tools

# Web search (keyless) + page fetch + MCP client (consume external MCP servers).
from .tools.websearch import web_search_tools
from .tools.webfetch import web_fetch_tools
from .mcp import mcp_tools
from .learning import LearningEngine, learning_tools
from .learning import models as _learn_models  # noqa: F401

# ImprovementEngine: measured outcomes feed back into lesson weights + proposals.
from .improvement import ImprovementEngine
from .improvement import models as _improve_models  # noqa: F401

# Motivation Layer ("the pulse"): standing goals + off-by-default deliberation.
from .motivation import IntentEngine, goal_tools
from .motivation import models as _motiv_models  # noqa: F401

# LLM Connections (API key + OAuth2/PKCE).
from .connections import ConnectionRegistry
from .connections import models as _conn_models  # noqa: F401

# Computer use (opt-in, gated, traced).
from .computeruse import (
    ApprovalQueue,
    ComputerUsePolicy,
    CUContext,
    FakeBrowser,
    PlaywrightBrowser,
    TraceRecorder,
    computeruse_tools,
)
from .computeruse import models as _cu_models  # noqa: F401

# Terminals (multi-session PTY manager for the dashboard).
from .terminals import TerminalManager


#: Module logger for the wiring itself. ``build_platform`` keeps a LOCAL ``log``
#: bound to the event-bus logger, so anything that is not an event uses this one.
_log = get_logger("platform")


@dataclass
class Platform:
    config: Config
    event_bus: EventBus
    engine: Engine
    vault: BrowserVault
    providers: ProviderManager
    router: ModelRouter
    registry: ToolRegistry
    permissions: PermissionEngine
    memory: MemoryLayers
    skills: SkillRegistry
    artifacts: ArtifactStore
    #: Saved, re-runnable scripts agents wrote (v1.95.0). Distinct from
    #: ``artifacts`` (generated MEDIA files): this holds source code.
    code_artifacts: CodeArtifactStore
    evaluator: Evaluator
    observability: Observability
    secrets: SecretsManager
    integrations: IntegrationRegistry
    notifier: Notifier
    inbound_webhooks: InboundWebhooks
    outbound_webhooks: OutboundWebhooks
    filesearch: FileSearchService
    ltm: LongTermMemory
    learning: LearningEngine
    connections: ConnectionRegistry
    computeruse: CUContext
    terminals: TerminalManager
    blackboard: "BlackboardStore | None" = None
    #: The department's durable WORKLIST (v1.174.0) — which units of a bulk job
    #: exist, which are claimed, which are finished and what each produced. Same
    #: scope as ``blackboard`` (the root session id), so a supervisor and its
    #: subagents share one list.
    worklist: "WorklistStore | None" = None
    scheduler: Scheduler | None = None
    sentinels: "SentinelService | None" = None
    agents_registry: DynamicAgentRegistry | None = None
    tools_registry: "DynamicToolRegistry | None" = None
    intent: "IntentEngine | None" = None
    improvement: "ImprovementEngine | None" = None
    #: Skill learning (v1.135.0): finished sessions feed a suggest-only loop
    #: that distils repeatable procedures into reviewable draft skills.
    skill_learning: "SkillLearningEngine | None" = None
    #: The SHARED embedder (real Ollama when reachable, offline mock otherwise;
    #: persistent-cached). Built once and injected into filesearch/ltm — kept on
    #: the platform so later consumers (memory graph, runtime-added LTM sources)
    #: use the SAME one instead of accidentally falling back to the mock.
    embedder: "object | None" = None
    #: The Memory Fabric — one federated ``recall()`` across every store (files,
    #: notes, memory graph, project knowledge, lessons, sessions). Powers the
    #: ``recall`` tool and the auto-grounding folded into chat + agent runs.
    fabric: "MemoryFabric | None" = None
    #: FTS5 history search (v1.142.0) — ONE ranked index over every conversation
    #: (chat threads, messaging threads, round-tables, agent sessions). Built
    #: once here and shared: the capability probe is cached and the writes are
    #: serialized by the index's own lock, so every consumer (the
    #: ``history_search`` tool, ``GET /search/history``, the memory fabric, the
    #: daemon's backfill loop) must use THIS instance, never its own.
    #:
    #: CANONICAL ACCESSOR: ``core.db.search_index(engine)``. This attribute is
    #: the same object — ``build_platform`` obtains it from that accessor rather
    #: than constructing one — so the write seams (which reach it through
    #: ``core.db``, without a platform in scope) and everything holding a
    #: platform share ONE lock. Never write ``SearchIndex(engine)`` outside
    #: ``core.db.search_index``.
    search_index: "SearchIndex | None" = None
    #: The Reflex Loop's durable rule store (signal→action bindings). The
    #: executing ReflexRouter is built by the daemon (it needs the orchestrator).
    reflex: "object | None" = None
    #: FX-01 ephemeral per-session token/tool stream hub (NOT the event bus — see
    #: core/streams.py). Optional so bare-platform unit tests still construct.
    streams: "StreamHub | None" = None
    #: Per-session Python namespaces (v1.159.0). Tool results can be bound to
    #: variables here instead of being pasted into the model's context; the
    #: `repl` tool then reaches them by name. None on a platform built without
    #: it — every caller treats the namespace as an optimisation, never a
    #: precondition.
    repl: "ReplRegistry | None" = None
    #: The agent orchestrator, attached by the daemon after it builds one
    #: (v1.119.0) — task-kind schedules fire real agent sessions through it.
    #: Optional so bare-platform unit tests still construct; the dispatcher
    #: raises an honest error when a task schedule fires without it.
    orchestrator: "object | None" = None
    #: LOCAL FLEET registry (the user's own inference machines). Seeded from the
    #: two config endpoint slots, so it is populated with zero setup.
    fleet: "object | None" = None
    #: The memory steward (v1.143.0) — ONE shared instance, so the weekly review
    #: schedule (``_dispatch_scheduled``) and the Memory page's review card window
    #: the same history and write the same run ledger instead of each building a
    #: private steward. Optional so bare-platform unit tests still construct; every
    #: reader treats ``None`` as "this build has no curation" rather than failing.
    memory_steward: "object | None" = None
    #: Capability requests (v1.178.0) — the suggest-only queue an agent files
    #: into when the app has no verb for the job. ONE shared store so the tool
    #: and the review routes cannot diverge. Optional: every reader treats
    #: ``None`` as "this build cannot take requests", never as a failure.
    capabilities: "object | None" = None
    #: Mid-turn tool approvals (v1.187.0 chat, v1.189.0 sessions) — the pending
    #: asks a human can answer while a turn or run is PAUSED on them. ONE
    #: registry here so the chat route and the agent runtime share it, and
    #: ``POST /chat/approvals/{id}`` answers a pause wherever it happened.
    #: Optional for bare-platform tests; build_platform always attaches one.
    approvals: "object | None" = None


def build_platform(
    project_root: str, ask_resolver: AskResolver | None = None
) -> Platform:
    config = load_config(project_root)
    config.ensure_dirs()

    event_bus = EventBus()
    streams = StreamHub()  # FX-01: ephemeral token/tool stream side-channel
    # open_db self-heals a corrupt DB (quarantine + fresh) so the daemon always
    # boots instead of wedging on a malformed file.
    engine = open_db(config.db_path)

    # Observability (§30): persist every event + log it.
    log = get_logger("events")
    event_bus.add_handler(lambda ev: persist_event(engine, ev))
    event_bus.add_handler(
        lambda ev: log.info("%s %s", ev.type, {k: v for k, v in ev.payload.items() if k != "content"})
    )

    vault = BrowserVault(config.browser_dir)

    # Never let an agent file tool (read_document/extract_pdf/file_search) read
    # the Fernet key material, regardless of the FS allowlist (security).
    register_protected_root(config.home / "secrets")
    register_protected_root(config.browser_dir)
    # TX-01 undo journal pre-images can hold prior file content (incl. from the
    # user's real folders) — never let an agent file tool read them back out.
    register_protected_root(config.home / "undo")
    # The app SQLite DB is NOT an fs allowlist root, but an agent file tool could
    # still name it by absolute path — and it holds INLINE undo pre-images (<8KB)
    # plus the plaintext tool/event ledger. Protect the DB file and its WAL/SHM
    # sidecars by exact path (files, not dirs, so nothing else under home is
    # affected — workspaces + the user's real folders stay readable).
    register_protected_root(config.db_path)
    register_protected_root(Path(str(config.db_path) + "-wal"))
    register_protected_root(Path(str(config.db_path) + "-shm"))

    # Secrets vault + LLM Connections (OAuth2/PKCE + API key) — built early so the
    # provider manager resolves live credentials and reports REAL availability.
    secrets = SecretsManager(config.home, engine)
    from .connections.probe import live_probe

    def _oauth_app(provider: str) -> dict:
        """Resolve user-registered OAuth app credentials from the vault.

        The daemon-callback redirect default applies ONLY to a user-registered
        custom app (which the user registers WITH that callback). Embedded
        public clients (e.g. Claude Code's) only accept their OWN registered
        redirects — sending the daemon's localhost callback gets a hard
        "Redirect URI ... is not supported by client" — so with no custom
        client id the redirect is left empty and the registry falls back to
        ``spec.oauth_redirect_uri``.
        """
        client_id = secrets.get(f"{provider}_oauth_client_id")
        redirect = secrets.get(f"{provider}_oauth_redirect_uri") or (
            f"http://localhost:8787/oauth/{provider}/callback" if client_id else ""
        )
        return {
            "client_id": client_id,
            "client_secret": secrets.get(f"{provider}_oauth_client_secret"),
            "redirect_uri": redirect,
        }

    connections = ConnectionRegistry(
        engine,
        secrets,
        http_factory=lambda: httpx.Client(timeout=30),
        # Real network reachability for the Connections "Test" button so a bad key
        # is caught at Test, not silently at first session.
        prober=live_probe,
        oauth_app=_oauth_app,
    )
    # One-time migration: Anthropic/OpenAI are now API-key-only, with the
    # subscription inherited from the `claude`/`codex` CLI. Purge any account
    # OAuth token this app minted under the retired in-app login flow so it can
    # never reach the raw API. Idempotent; safe every boot.
    try:
        connections.purge_app_minted_oauth()
    except Exception:  # noqa: BLE001 — migration must never block boot
        pass

    def _grok_cli_available() -> bool:
        """True when the local Grok CLI is installed AND has a valid on-disk
        account session. Cheap (reads two small JSON files under ~/.grok) and
        never raises, so it's safe on the availability/health hot path."""
        try:
            from .providers.cli_detect import grok_session

            return grok_session() is not None
        except Exception:  # noqa: BLE001
            return False

    # Built BEFORE the manager: the manager's availability oracle closes over it,
    # and register_providers() runs the moment the manager exists.
    from .fleet.registry import FleetRegistry

    fleet_registry = FleetRegistry(config)

    def _opencode_allowed() -> list[str]:
        """LOCAL OpenCode models only — never its hosted tier, never a paid
        passthrough alias sitting behind a local-looking proxy."""
        from .providers.opencode import allowed_models

        return allowed_models(config)

    providers = ProviderManager(
        vault=vault,
        default_model=config.default_model,
        credential_resolver=connections.credential,
        # Presence-only availability check — never triggers a (blocking) OAuth
        # token refresh on the event loop from /health, routing, or onboarding.
        presence_resolver=connections.has_credential,
        # Local OpenAI-compatible (Ollama) endpoint — "network optional" local LLM.
        ollama_base_url=config.ollama_base_url,
        ollama_model=config.ollama_model,
        # Custom OpenAI-compatible endpoint (Ollama Cloud / LM Studio / vLLM...).
        custom_base_url=config.custom_base_url,
        custom_model=config.custom_model,
        # Locally-installed Grok CLI: live on-disk session probe (binary present
        # + a valid ~/.grok account session). Injected here so the manager stays
        # hermetic in unit tests; in the real app grok-cli lights up the moment
        # the CLI is installed + logged in, no restart.
        grok_cli_available=_grok_cli_available,
        # Inherit the logged-in `claude`/`codex` CLI login when Claude/OpenAI is
        # requested without an API key (the sanctioned subscription path). The
        # raw API-key path is unaffected.
        inherit_cli_logins=True,
        # Local fleet: availability for the runtime-registered fleet-* providers
        # comes from the sampler's CACHED last-known reachability (never a live
        # probe — this is called per provider per request).
        dynamic_available=lambda name: fleet_registry.reachable(name),
        # OpenCode is offered LOCAL-ONLY: it can also reach hosted/paid models,
        # and the user asked for their own hardware only. See providers/opencode.
        opencode_allowed=lambda: _opencode_allowed(),
    )
    # LOCAL FLEET — the user's own inference machines. The registry derives nodes
    # from the two config endpoint slots (so it works with zero setup) plus any
    # the user added, and registers one provider per ROUTABLE node. Topology
    # children discovered behind a proxy are observability-only: they are already
    # reachable through the proxy's alias, and registering them again would show
    # the same GPU twice in every picker.
    fleet_registry.register_providers(providers, secret_resolver=secrets.get)
    # Self-tuning router (§6 phase-1), OFF by default: only when the user opts in
    # (prefer_local_when_capable) AND a local Ollama model is configured AND it has
    # demonstrably met the quality bar for a task class do we prefer it for that
    # class. `observability` is assigned below in this same scope and exists long
    # before this closure is ever invoked (at request time), so the reference is
    # safe. When the flag is off the closure returns None and routing is unchanged.
    def _local_oracle(task_class: str | None) -> tuple[str, str] | None:
        """Prefer the SMALLEST local model that has demonstrably done this class
        of work well (§6 phase-1, generalized in v1.148.0).

        THE BUG THIS FIXES: every path here was hardwired to Ollama — the
        ``ollama_base_url`` gate, the provider name, and the model. A user whose
        hardware is a fleet node (``fleet-custom``), an LM Studio/vLLM endpoint
        (``custom``), or OpenCode got ``None`` on every call, so
        ``prefer_local_when_capable`` could not fire for them no matter what they
        set. That is most local-fleet users, and it is exactly the group the
        setting exists for.

        Now: walk the local ladder smallest-first (``providers.local``) and take
        the first rung that clears the quality bar for this task class. Smallest
        first is the point — "the smallest model likely to complete the task" —
        and the bar is still evidence, not optimism: a model with fewer than
        ``local_quality_min_samples`` evaluated sessions returns None from
        ``local_quality`` and is skipped, so a fresh install changes nothing
        until the evidence exists.
        """
        if not getattr(config, "prefer_local_when_capable", False):
            return None
        bar = float(getattr(config, "local_quality_bar", 0.75))
        min_samples = int(getattr(config, "local_quality_min_samples", 3))
        try:
            from .providers.local import local_ladder

            rungs = local_ladder(providers, config)
        except Exception:  # noqa: BLE001 — routing must never break on this
            return None
        for rung in rungs:
            quality = observability.local_quality(
                rung["provider"],
                task_class=task_class,
                min_samples=min_samples,
                # Judge the model that will ACTUALLY serve, not the provider in
                # aggregate: one endpoint can serve a 14B and a 120B, and their
                # track records are not interchangeable.
                model=rung["model"] or None,
            )
            if quality is not None and quality >= bar:
                return (rung["provider"], rung["model"])
        return None

    async def _auto_route(system, messages, tools, task_class):
        """The routing model at work (§6): decide a difficulty tier for the
        request (a zero-cost heuristic first, else a cheap classifier call to the
        user's routing model) and map it to the best CONNECTED model. Returns
        ``{provider, model, tier, classifier}`` or ``None`` to let the router fall
        back. Never raises — the router treats a raised/None result as fallback."""
        from .providers import routing as _routing
        from .providers.adapters.base import LLMMessage

        connected = _routing.connected_real_models(providers, config)
        if not connected:
            return None
        # LATENCY-AWARE tie-break: among equally-cheap models for a tier, the
        # faster-observed one (router-maintained EWMA) wins. A manual
        # routing_tiers_json override still takes precedence over the derived map.
        tiers = _routing.parse_tiers_json(
            getattr(config, "routing_tiers_json", "") or ""
        ) or _routing.derive_tiers(
            connected,
            latency=_routing.LATENCY.ewma,
            # v1.148.0: with local-first on, every tier is drawn from the user's
            # own hardware and cloud is reached only by escalation. A manual
            # routing_tiers_json override above still wins — an explicit map is
            # a stronger statement than a preference.
            local_first=bool(getattr(config, "prefer_local_when_capable", False)),
        )
        if not tiers:
            return None
        classifier = _routing.parse_pm(getattr(config, "routing_model", "") or "")
        used_classifier = ""
        tier = _routing.heuristic_tier(messages, tools, task_class)
        if tier is None:
            # Ambiguous → ask the cheap routing model to classify.
            if classifier and providers.available(classifier[0]):
                try:
                    adapter = providers.get(classifier[0], classifier[1] or None)
                    resp = await adapter.complete(
                        system=_routing.CLASSIFY_SYSTEM,
                        messages=[
                            LLMMessage(role="user", content=_routing.classify_input(messages))
                        ],
                        tools=[],
                    )
                    tier = _routing.parse_tier(resp.text)
                    used_classifier = _routing.format_pm(classifier)
                except Exception:  # classifier hiccup → sensible default
                    tier = "standard"
            else:
                tier = "standard"
        target = tiers.get(tier) or tiers.get("standard") or tiers.get("heavy")
        if target is None:
            return None
        return {
            "provider": target[0],
            "model": target[1] or "",
            "tier": tier,
            "classifier": used_classifier,
        }

    # Pass the default provider as a LIVE callable so a model switch in the UI
    # (PUT /settings mutates config) reaches provider-less callers — routing and
    # the motivation/improvement loops — without a daemon restart.
    router = ModelRouter(
        providers,
        lambda: config.default_provider,
        event_bus,
        local_oracle=_local_oracle,
        auto_route=_auto_route,
        # Live: an explicitly-picked provider must answer (or fail honestly)
        # while the pin is on — no substitution. Settings toggle, no restart.
        strict_pin=lambda: bool(getattr(config, "strict_model_pin", False)),
    )
    registry = default_registry()

    # Phase 4: route the shell tool through the Sandbox Manager (same "shell" name).
    registry.register(SandboxedShellTool())
    # v1.90.0: disposable code execution — the agent's escape hatch when no
    # tool reliably fits (same "ask" trust tier as shell).
    # v1.95.0: every run is ALSO recorded in the Code Lab store. Execution is
    # unchanged (still disposable in the workspace) — this only keeps the
    # SOURCE, which otherwise died with the session workspace, so the user can
    # browse and re-run what agents built from the Artifacts page.
    from .tools.runcode import RunCodeTool

    code_artifacts = CodeArtifactStore(engine)

    def _code_sink(name, language, code, session_id, exit_code, output, purpose=""):  # noqa: ANN001
        """Persist an executed script. Resolves the producing session's project
        so a script written during project work is scoped to it (context spine),
        and settles the one-line USE CASE shown on the gallery tile: what the
        agent stated, else what the code's own header says, else nothing (never
        invented)."""
        from .codelab.purpose import purpose_for

        project_id = None
        if session_id:
            try:
                from .core.models import Session as _Session

                with session_scope(engine) as db:
                    parent = db.get(_Session, session_id)
                    project_id = parent.project_id if parent is not None else None
            except Exception:  # noqa: BLE001 — provenance is a nice-to-have
                project_id = None
        code_artifacts.save(
            name,
            language,
            code,
            description=purpose_for(code, language, purpose),
            session_id=session_id,
            project_id=project_id,
            exit_code=exit_code,
            output=output,
        )

    registry.register(RunCodeTool(sink=_code_sink))

    # --- Session namespace (v1.159.0) -------------------------------------
    # The answer to context flooding, from the other end. Tool output is what
    # fills a window; `_store_as` puts a result in a VARIABLE and returns a
    # receipt, and `repl` runs code against those variables in a persistent
    # subprocess. Registered here beside run_code because they are siblings:
    # run_code is a DISPOSABLE script (fresh process, nothing survives), this
    # is a LIVING namespace (state persists across steps of one session).
    #
    # Best-effort: a platform that cannot start it keeps working exactly as it
    # did, minus the optimisation.
    try:
        from .repl.session import ReplRegistry
        from .tools.repl_tool import ReplTool

        repl_registry = ReplRegistry()
        registry.register(ReplTool(repl_registry))
        registry.attach_repl(repl_registry)
    except Exception:  # noqa: BLE001 — never let an optimisation break boot
        repl_registry = None

    # v1.97.0: close the loop — agents can now FIND and REUSE what they already
    # wrote instead of re-deriving it. search/load are read-only ("allow");
    # code_run executes saved code and stays gated like run_code/shell.
    from .codelab.tools import code_tools

    for tool in code_tools(code_artifacts, config.codelab_dir):
        registry.register(tool)

    # The SHARED embedder is chosen ONCE here and reused across every semantic
    # surface — layered memory (below), file search, and ltm — so they all rank
    # against the SAME vectors: a real local model (Ollama) when reachable, else
    # the deterministic offline MockEmbedder, wrapped in the persistent embedding
    # cache (engine) so re-indexing is incremental and survives restarts (§22).
    embedder = build_embedder(config, engine)

    # Phase 5: layered memory + retrieval, exposed as tools. Pass the shared
    # embedder so working-memory semantic search uses real vectors too.
    memory = MemoryLayers(engine, embedder=embedder, config=config)
    for tool in memory_tools(memory):
        registry.register(tool)

    # Phase 11: skills framework — builtin + user + external Claude/Codex skills
    # (recursively discovered) + any user-configured extra paths, exposed as the
    # search/load tools (which read the registry live, so more skills = richer
    # results, not more tools) + skill_create (v1.90.0: the agent KEEPS a
    # proven solution as a durable skill; config gives it the home + rescan).
    skills = SkillRegistry().repopulate(
        config.home, getattr(config, "extra_skill_paths", None)
    )
    for tool in skill_tools(skills, config):
        registry.register(tool)

    # Phase 8a / 9: artifact store, evaluation + observability.
    artifacts = ArtifactStore(config.artifacts_dir, engine)

    def _announce_artifact(artifact, session_id=None):  # noqa: ANN001
        """Publish ``artifact.generated`` for every save — the dashboard's event
        stream has listened for this type since day one, but nothing emitted it.
        Saves happen on the loop, in to_thread workers, and in the CLI (no loop);
        mirror the scheduler's pattern for each case."""
        coro = event_bus.publish(
            "artifact.generated",
            {
                "name": artifact.name,
                "version": artifact.version,
                "kind": artifact.kind,
                "path": str(artifact.path),
                "size": artifact.size,
            },
            session_id=session_id,
        )
        try:
            asyncio.get_running_loop().create_task(coro)
        except RuntimeError:  # off-loop (worker thread / CLI): run to completion
            asyncio.run(coro)

    artifacts.on_save = _announce_artifact
    evaluator = Evaluator(engine)
    observability = Observability(engine)

    # Computer use (safety best practices) — OFF by default. Built either way so
    # status/approvals are available, but a real (Playwright) browser is only
    # constructed when the user explicitly enables it; reads stay gated on policy.
    cu_policy = ComputerUsePolicy.from_config(getattr(config, "computer_use", None))
    cu_browser = PlaywrightBrowser() if cu_policy.enabled else FakeBrowser({})
    computeruse = CUContext(
        cu_policy,
        cu_browser,
        ApprovalQueue(engine),
        trace=TraceRecorder(artifacts=artifacts),
        # Vision for `web_look`: screenshots go to whichever vision-capable
        # model is connected, via the router (lazy so tests can swap it).
        router_resolver=lambda: router,
    )
    for tool in computeruse_tools(computeruse):
        registry.register(tool)

    # Terminals: multiple live shell sessions the dashboard can attach to. The
    # snapshot file lets them survive a daemon restart / app update — on boot the
    # panes come back (same id + cwd + prior scrollback, fresh shell).
    terminals = TerminalManager(state_path=config.home / "terminals.json")

    # --- Robust feature set ----------------------------------------------

    # Secrets vault (built above) — expose its agent tools.
    for tool in secret_tools(secrets):
        registry.register(tool)

    # Integrations framework + built-in generic/mock integrations.
    integrations = IntegrationRegistry(engine)
    register_builtins(integrations)
    # Re-register user-added REST integrations so they survive restart (their
    # config + enabled state live in the IntegrationRecord table already).
    from .integrations.base import IntegrationSpec as _IntgSpec
    from .integrations.builtin import REST_SPEC as _REST_SPEC
    from .integrations.builtin import RestApiIntegration as _RestIntg

    for custom in config.custom_integrations or []:
        cid = str(custom.get("id") or "").strip()
        if not cid or integrations.get_spec(cid) is not None:
            continue
        integrations.register(
            _IntgSpec(
                id=cid,
                kind="rest",
                display_name=str(custom.get("name") or cid),
                description=str(custom.get("description") or ""),
                required_secrets=[],
                config_schema=_REST_SPEC.config_schema,
            ),
            lambda cfg, resolver: _RestIntg(cfg, resolver),
        )
    for tool in integration_tools(integrations, secrets.get):
        registry.register(tool)

    # File search across configured roots, sharing the SAME embedder built above
    # so filesearch + ltm + layered memory all rank against identical vectors.
    search_roots = [Path(r) for r in config.search_roots] or [config.project_root]
    filesearch = FileSearchService(search_roots, embedder=embedder)
    for tool in filesearch_tools(filesearch):
        registry.register(tool)

    # Communication channels + Notifier (auto-alerts on selected events). The
    # inbound (receive) leg is wired in the daemon lifespan; build the channels
    # with a GET transport too so the poller can long-poll them.
    notifier = build_notifier(
        getattr(config, "comm", None),
        secret_resolver=secrets.get,
        http_post=httpx_post,
        http_get=httpx_get,
    )
    for tool in notify_tools(notifier):
        registry.register(tool)
    event_bus.add_handler(notifier.on_event)

    # Webhooks: inbound dispatch + outbound delivery on matching events.
    inbound_webhooks = InboundWebhooks(engine, secret_resolver=secrets.get)
    outbound_webhooks = OutboundWebhooks(
        engine,
        http_post=lambda url, payload, headers: httpx.post(
            url, json=payload, headers=headers, timeout=httpx.Timeout(10, connect=2.0)
        ),
        # SSRF defense: outbound targets resolving to private/loopback/metadata
        # addresses are refused unless explicitly opted in (local dev/testing).
        allow_internal=os.environ.get("IRONJARVIS_WEBHOOK_ALLOW_INTERNAL", "").strip().lower()
        in {"1", "true", "yes", "on"},
        # Resolve signing/verify secrets from the vault at use-time so they
        # survive a daemon restart (the in-memory cache does not).
        secret_resolver=secrets.get,
    )
    event_bus.add_handler(outbound_webhooks.on_event)

    # Long-term memory: built-in markdown brain + optional Obsidian / Notion.
    ltm = LongTermMemory()
    ltm.register(MarkdownBrainConnector(config.home / "brain", embedder=embedder))
    if getattr(config, "obsidian_vault", None):
        ltm.register(ObsidianConnector(Path(config.obsidian_vault), embedder=embedder))
    if secrets.get("notion_token") and getattr(config, "notion_database_id", None):
        ltm.register(
            NotionConnector(
                config.notion_database_id,
                token_resolver=lambda: secrets.get("notion_token"),
                http=httpx.Client(timeout=30),
            )
        )
    # User-configured custom LTM sources (markdown dirs / Notion DBs / cloud
    # drives / offsite RAG), persisted. Cloud drives resolve their OAuth token
    # through the Connections registry (auto-refreshing) and rank downloaded
    # files with the SAME shared embedder used by file-search + Total Recall.
    load_custom_sources(
        ltm,
        engine,
        secret_resolver=secrets.get,
        http_factory=lambda: httpx.Client(timeout=30),
        credential_resolver=connections.credential,
        embedder=embedder,
    )
    for tool in ltm_tools(ltm):
        registry.register(tool)

    # Memory housekeeping (v1.143.0): the ONE way an agent can FILE a
    # suggest-only cleanup proposal. Registered here, right after ltm_tools,
    # because it is the SUGGEST half of the same pair — ltm_append adds memory
    # directly (append-only, undoable), memory_propose queues everything that
    # would change or remove a note the user already has. Without this
    # registration the steward's whole review queue is unreachable from an
    # agent session and can only ever be filled by a test.
    from .memory.proposal_tools import memory_proposal_tools
    from .memory.proposals import MemoryProposalStore

    for tool in memory_proposal_tools(
        MemoryProposalStore(engine, ltm=ltm, home=config.home)
    ):
        registry.register(tool)

    # The ``recall`` tool is registered further down, once the learning engine
    # exists — it federates EVERY store through the Memory Fabric, not just
    # files + long-term memory (see the MemoryFabric build after LearningEngine).

    # Documents: read/write PDF, Word, Excel, PowerPoint, CSV, Markdown, text
    # (+ markdown-aware RICH creation and cross-format conversion).
    for tool in document_tools(router_resolver=lambda: router):
        registry.register(tool)
    # read_file's office/binary redirect reads documents too, so it needs the
    # same OCR reach (v1.174.0). It is built in default_registry() BEFORE the
    # router exists, so re-register it here with the resolver — otherwise an
    # agent that opens a scanned PDF with read_file gets silence while
    # read_document transcribes the same file, which is exactly the
    # which-tool-did-you-happen-to-pick lottery this wave removes.
    from .tools.builtins import ReadFileTool

    registry.register(ReadFileTool(router_resolver=lambda: router))

    # Images: view_image gives any agent EYES (vision via the router — works
    # with whichever vision-capable model is connected), plus convert/resize/
    # info via Pillow. The router resolver is lazy so tests can swap it.
    from .tools.images import image_tools

    for tool in image_tools(lambda: router):
        registry.register(tool)

    # Web search: keyless DuckDuckGo by default; Brave if a key is in the vault.
    for tool in web_search_tools(secret_resolver=secrets.get):
        registry.register(tool)

    # Web fetch: read one result page so answers ground in content, not snippets.
    for tool in web_fetch_tools():
        registry.register(tool)

    # Pixio: generative media (image/video/audio) — the creative arm. Key from
    # the vault secret 'pixio' (or env PIXIO_API_KEY); the pixio-skill in the
    # skill library teaches agents the workflow. Tools are safe no-ops without
    # a key (a clear "not configured" error, never a crash).
    from .tools.pixio import pixio_tools

    def _creative_sink(name, blob, filename, kind, session_id=None):  # noqa: ANN001
        """Every generation lands DURABLY in the Creative gallery (artifacts) —
        the workspace copy dies with the session. save() fires artifact.generated,
        so the gallery updates live."""
        artifacts.save(name, blob, kind=kind, filename=filename, session_id=session_id)

    for tool in pixio_tools(
        key_resolver=lambda: secrets.get("pixio") or os.environ.get("PIXIO_API_KEY"),
        artifact_sink=_creative_sink,
    ):
        registry.register(tool)

    # External MCP servers (Gmail/Drive/GitHub/...) as native tools. Empty
    # config (the default) is a safe no-op; an unreachable server is skipped.
    # Registered with mcp=True so the ``mcp:*`` allowlist sentinel reaches them
    # from the agent loop AND so they survive a restart identically to how they
    # were live-loaded when first added (previously boot-loaded MCP tools were
    # registered plain — invisible to every agent's tool loadout).
    for tool in mcp_tools(getattr(config, "mcp_servers", None), secret_resolver=secrets.get):
        registry.register(tool, mcp=True)

    # Self-correcting learning loop: feedback + reflections become lessons that
    # get injected into every future agent prompt (gets better each interaction).
    learning = LearningEngine(engine)
    for tool in learning_tools(learning):
        registry.register(tool)

    # Memory Fabric: ONE federated recall over every store (files, notes, memory
    # graph, project knowledge, lessons, past sessions), sharing the same
    # embedder. Powers the ``recall`` tool below AND the auto-grounding folded
    # into chat + agent runs — so every surface remembers everything at once.
    fabric = MemoryFabric(
        filesearch=filesearch,
        ltm=ltm,
        memory=memory,
        learning=learning,
        embedder=embedder,
        engine=engine,
    )
    for tool in recall_tools(fabric):
        registry.register(tool)

    # History search (v1.142.0): the FTS5 index over every conversation. ONE
    # instance per ENGINE for the whole process, obtained from the CANONICAL
    # accessor ``core.db.search_index`` — never ``SearchIndex(engine)`` here.
    #
    # That accessor is not a stylistic preference, it is the only thing that
    # makes the index's internal write lock mean anything. The five write seams
    # (chat save/delete, comm append, round append, orchestrator post-run,
    # prune_events) and the memory fabric all reach the index through
    # ``core.db.search_index(engine)``; a second instance built here would carry
    # its OWN ``threading.RLock``, so the daemon's backfill loop and a chat
    # autosave could each believe they were serialized while actually racing —
    # two half-open delete-then-insert transactions on the same thread's docs,
    # resolved by SQLite's busy_timeout and a silently dropped index write.
    # One instance, one lock, one capability probe. Pinned by
    # ``tests/test_history_search.py::test_one_shared_index_serves_tool_route_and_seams``.
    #
    # available() is called RIGHT HERE, at build time, on purpose: the first
    # call is the probe (a query, and on a fresh DB a CREATE VIRTUAL TABLE), and
    # the first caller would otherwise pay for it — possibly from inside another
    # subsystem's open write transaction (Pair S2 syncs inside its own
    # session_scope). Warming it here keeps the probe off every hot path.
    search_index: "SearchIndex | None" = shared_search_index(engine)
    if search_index is not None:
        search_index.available()
        for tool in history_search_tools(search_index):
            registry.register(tool)

    # MCP auto-approve: a server the user explicitly marked trusted lets the
    # headless daemon run its tools without an interactive prompt, so autonomous
    # agents (and the Reflex Loop) can actually USE it — not just chat, which
    # already approves-by-arming. Opt-in per server, default off. Coarse by
    # design: mcp_call is one shared perm key, so trusting any server trusts
    # every connected MCP tool (applied at boot; add a server then restart).
    # v1.127.0: the Tools page checkbox persists as the GLOBAL flag; the older
    # per-server flags still grant too (either path opens the one shared key).
    _mcp_auto = bool(getattr(config, "mcp_auto_approve", False)) or any(
        (s or {}).get("auto_approve")
        for s in (getattr(config, "mcp_servers", None) or [])
    )
    if ask_resolver is not None and _mcp_auto:
        _base_ask = ask_resolver

        def ask_resolver(name: str, args: dict, _b=_base_ask) -> bool:  # type: ignore[misc]
            return True if name == "mcp_call" else _b(name, args)

        # Carry the wrapped resolver's `interactive` marker across (v1.154.2).
        # Without this, turning MCP auto-approve on silently replaced the
        # headless resolver with an unmarked wrapper, and every refusal went
        # back to claiming the USER rejected it — the exact false message that
        # release exists to remove, resurrected by an unrelated setting.
        ask_resolver.interactive = getattr(_base_ask, "interactive", True)  # type: ignore[attr-defined]

    permissions = PermissionEngine(config.permissions, ask_resolver=ask_resolver)

    platform = Platform(
        config=config,
        event_bus=event_bus,
        streams=streams,
        repl=repl_registry,
        engine=engine,
        vault=vault,
        providers=providers,
        router=router,
        registry=registry,
        permissions=permissions,
        memory=memory,
        skills=skills,
        artifacts=artifacts,
        code_artifacts=code_artifacts,
        evaluator=evaluator,
        observability=observability,
        secrets=secrets,
        integrations=integrations,
        notifier=notifier,
        inbound_webhooks=inbound_webhooks,
        outbound_webhooks=outbound_webhooks,
        filesearch=filesearch,
        ltm=ltm,
        learning=learning,
        connections=connections,
        computeruse=computeruse,
        terminals=terminals,
        fleet=fleet_registry,
        embedder=embedder,
        fabric=fabric,
        search_index=search_index,
    )

    # Mid-turn approvals (v1.189.0): built HERE so the chat route and the
    # agent runtime share one registry — a pause answered on the wrong copy
    # would leave the waiting side waiting forever.
    from .core.approvals import ChatApprovals as _Approvals

    platform.approvals = _Approvals()

    # Phase 6: the delegate tool needs the assembled platform.
    platform.registry.register(DelegateTool(platform))

    # ASK A TEAMMATE WITHOUT SPAWNING ONE (v1.193.0). `delegate` above is the
    # only other agent-to-agent door and it is a whole SESSION — workspace,
    # AgentRun, budget, learning loop — held open while the parent blocks.
    # `consult` is one question and one answer: no session, no files, and no
    # recursion (the consulted agent answers with tools=[], so it cannot
    # consult back). Same assembled platform for the same reason delegate needs
    # it — the roster, the registries and the router all hang off it.
    platform.registry.register(ConsultTool(platform))
    # Declared beside the registration exactly as the worklist keys are below,
    # and for the identical fail-closed reason: the permission engine resolves
    # an unknown key to "ask", and a headless "ask" (no resolver) is a DENY, so
    # without a default no agent run and no scheduled run could ever consult —
    # and the user's existing config.toml, dumped from an older default set,
    # will never carry the key. "allow" is the right tier: consulting reads a
    # teammate's opinion, writes nothing, touches no host resource, and is
    # capped per run — strictly below `delegate` ("ask"), which spends a whole
    # session. BOTH copies are seeded because PermissionEngine snapshots the
    # mapping at construction; `setdefault` keeps a user-set value winning.
    # The canonical home is `core/config.py`'s default permissions (owned
    # elsewhere this wave) — this seeding is what makes the tool reachable on
    # an install whose config predates it.
    platform.permissions._base.setdefault("consult", "allow")
    platform.config.permissions.setdefault("consult", "allow")

    # Departments: the shared, session-scoped blackboard. Sibling sub-agents of
    # one task resolve to ONE board (their root session id) so they can post
    # findings and message each other instead of only summarizing upward.
    platform.blackboard = BlackboardStore(engine)
    for tool in blackboard_tools(platform.blackboard):
        platform.registry.register(tool)

    # The department's durable WORKLIST (v1.174.0). Same scope as the board
    # above (the root session id) and for a complementary reason: the
    # blackboard is prose between teammates, this is state two teammates can
    # never disagree about — `worklist_next` CLAIMS its chunk with a
    # compare-and-swap, so a chunked bulk job cannot hand the same file to two
    # subagents, and a run that hit its step ceiling resumes from what is
    # genuinely still pending.
    # `config` is passed for ONE question the board id depends on: whether a
    # session's workspace is a folder the USER named (part of the job's
    # identity, and the same across a re-run) or a disposable managed workspace
    # (a fresh path every run). Without it the store cannot tell them apart and
    # falls back to the task text alone — see `WorklistStore.board_for_root`.
    platform.worklist = WorklistStore(engine, config=config)
    for tool in worklist_tools(platform.worklist):
        platform.registry.register(tool)
    # Declared HERE, beside the registration, exactly as `workflow_list` is
    # above: the permission engine fail-closes an unknown key to "ask", and a
    # headless "ask" (no resolver) is a DENY — so without these four defaults no
    # agent and no scheduled run could ever record its progress, and the whole
    # feature would be invisible-dead on the user's install (whose config.toml,
    # dumped from an older default set, will never carry the keys). Bookkeeping
    # in the app's own database, no host reach: the same "allow" tier as the
    # blackboard tools. BOTH copies are seeded because the PermissionEngine
    # snapshots the mapping at construction (its `_base` is a dict COPY), and
    # seeding one side alone makes the settings display and the enforcement
    # disagree. `setdefault`, so a user-set config.toml value always wins.
    for _wl_key in WORKLIST_TOOL_NAMES:
        platform.permissions._base.setdefault(_wl_key, "allow")
        platform.config.permissions.setdefault(_wl_key, "allow")

    # Memory curation (v1.143.0): ONE shared steward, attached here because the
    # scheduled-fire dispatcher below asks it for the window each weekly review
    # covers. Construction touches no database (every method resolves the engine
    # at call time), so this can neither slow nor wedge a boot — and it is still
    # guarded, because a platform that failed to assemble over an optional
    # curation feature would be a far worse bug than a missing review card.
    try:
        from .memory.steward import MemorySteward

        platform.memory_steward = MemorySteward(platform)
    except Exception:  # noqa: BLE001 - the card degrades; the daemon does not
        _log.warning("the memory steward is unavailable in this build", exc_info=True)
        platform.memory_steward = None

    # Scheduled fires (v1.119.0): a fire runs an agent TASK (the primary kind),
    # a saved workflow, or emits an event — then records how it went on the row
    # and delivers the result to the user's destinations. The recording is what
    # turns "it fired at 9:02" into "here is what happened".
    def _record_outcome(name: str, status: str, detail: str, session_id: str = "") -> None:
        from sqlmodel import select

        from .scheduling.models import ScheduledTaskRecord

        with session_scope(engine) as db:
            rec = db.exec(
                select(ScheduledTaskRecord).where(ScheduledTaskRecord.name == name)
            ).first()
            if rec is None:
                return  # deleted mid-fire — nothing to stamp
            rec.last_status = status
            rec.last_detail = " ".join((detail or "").split())[:300]
            rec.last_session_id = session_id or ""
            db.add(rec)
            db.commit()

    def _deliver_outcome(task, payload: dict, status: str, detail: str) -> None:
        # A schedule is explicit intent, so the default audience is EVERY
        # destination ("This PC" included) — the pre-v1.119 default (default
        # channel only) meant results landed on the mock channel and nobody
        # ever saw them. payload.notify_channels narrows the audience;
        # notify=False silences it. Delivery failure never fails the fire:
        # the row already recorded the truth.
        if payload.get("notify") is False:
            return
        excerpt = " ".join((detail or "").split())
        if len(excerpt) > 200:
            excerpt = excerpt[:200] + "…"
        head = "done" if status == "ok" else "FAILED"
        message = f"Schedule “{task.name}”: {head}" + (f" — {excerpt}" if excerpt else "")
        try:
            targets = payload.get("notify_channels") or platform.notifier.channels()
            platform.notifier.notify(message, list(targets))
        except Exception:  # noqa: BLE001
            pass

    # -- the weekly memory review (v1.143.0) --------------------------------
    # The memory-review schedule is the ONE task schedule whose prompt must not
    # be the durable text stored on the row. A static prompt re-reads history
    # with no cursor, so every week's fire offers the same conversations and the
    # session re-writes the same notes; the steward's whole point is the window.
    # So this fire asks the steward to PLAN it, and closes the bookkeeping loop
    # afterwards. Everything below is best-effort by construction: a steward
    # failure returns None and the fire falls back to the stored prompt exactly
    # as before — the scheduler thread never sees an exception from here (the
    # v1.119 ``_run_scheduled`` discipline).
    def _is_memory_review(task, payload: dict) -> bool:
        """Is THIS fire the memory-review schedule?

        By NAME (the scheduler's unique key, and the same key the review card's
        "installed" check and ``_reconcile_unrecorded_reviews``' origin filter
        use), never by matching the task TEXT: a user who edits one word of the
        prompt must not silently lose their windowed reviews, and an unrelated
        schedule that happens to quote the template must not start moving the
        review cursor. ``template``/``template_id`` in the payload is honoured
        too, so a renamed schedule can still declare what it is.
        """
        try:
            wanted = {
                str(MEMORY_REVIEW_SCHEDULE["name"]).strip().lower(),
                str(MEMORY_REVIEW_SCHEDULE["id"]).strip().lower(),
            }
            if str(getattr(task, "name", "") or "").strip().lower() in wanted:
                return True
            declared = payload.get("template") or payload.get("template_id") or ""
            return str(declared).strip().lower() in wanted
        except Exception:  # noqa: BLE001 - an unreadable row is just "not it"
            return False

    def _memory_review_plan(task, payload: dict) -> "dict | None":
        """The steward's windowed plan for this fire, or None to fire as usual.

        None means "this is not a memory review, or the steward could not plan
        one" — both of which must leave the pre-v1.143 behaviour untouched. When
        the steward degrades, the fire still runs on the stored prompt and
        ``routes/memory_review.py::_reconcile_unrecorded_reviews`` still makes
        the run visible on the card; only the WINDOW is lost, and it is lost
        loudly in the log rather than silently.
        """
        if not _is_memory_review(task, payload):
            return None
        steward = getattr(platform, "memory_steward", None)
        planner = getattr(steward, "plan", None)
        if not callable(planner):
            return None
        try:
            plan = planner()
        except Exception:  # noqa: BLE001 - a plan failure must not skip the fire
            _log.warning("the memory steward could not plan this review", exc_info=True)
            return None
        if not isinstance(plan, dict):
            return None
        return {**plan, "steward": steward}

    def _record_memory_review(
        review: "dict | None", session_id: str, ok: bool, outcome: str
    ) -> None:
        """Close the steward's loop for a scheduled review. NEVER raises.

        The counts are READ off the session's own ledgers with the SAME helpers
        the manual lane uses (``memory/steward.py``), so a review recorded here
        and one recorded by ``POST /memory/review/run`` can never report
        different numbers for the same work. ``record_run`` advances the review
        cursor only when ``ok`` — that rule is structural, not repeated here.
        """
        if not review:
            return
        recorder = getattr(review.get("steward"), "record_run", None)
        if not callable(recorder):
            return
        try:
            from .memory.steward import count_notes_added, count_proposals_raised

            recorder(
                ok=bool(ok),
                cursor=str(review.get("cursor") or ""),
                since=str(review.get("since") or ""),
                conversations=int(review.get("conversations") or 0),
                docs=int(review.get("docs") or 0),
                notes_added=count_notes_added(engine, session_id),
                proposals_raised=count_proposals_raised(engine, session_id),
                outcome=str(outcome or "")[:400],
                session_id=session_id,
                refs=list(review.get("refs") or []),
            )
        except Exception:  # noqa: BLE001 - bookkeeping must not fail the fire
            _log.warning("could not record the scheduled memory review", exc_info=True)

    async def _dispatch_scheduled(task, payload: dict, fired: dict) -> str:
        """Run one fire; returns the human detail. ``fired['session_id']`` is
        set as soon as a task-kind session exists so a mid-run failure still
        records WHICH session to look at."""
        if task.kind == "task":
            prompt = str(payload.get("task") or "").strip()
            review = _memory_review_plan(task, payload)
            if review is not None:
                if review.get("empty"):
                    # The steward's own rule: an EMPTY window must NOT fire a
                    # session. Asking a model to curate nothing is how memory
                    # fills with invented facts, so the week is skipped and the
                    # row records why — no session, no run, no cursor move.
                    # A switched-OFF steward lands here too, and must not be
                    # reported as "nothing new": the user turned curation off,
                    # and firing the stored prompt would review anyway.
                    if review.get("enabled") is False:
                        return "memory review is switched off, so nothing ran"
                    reason = str(review.get("reason") or "").strip()
                    return "nothing new to review" + (f" — {reason}" if reason else "")
                prompt = str(review.get("task") or "").strip() or prompt
            if not prompt:
                raise ValueError("scheduled task has no 'task' text")
            if platform.orchestrator is None:
                raise RuntimeError(
                    "task schedules need the daemon's agent orchestrator"
                )
            # v1.171.0 (contract 3): a schedule may name WHO does the work.
            # Resolution mirrors POST /agents/{name}/spawn exactly — the
            # DYNAMIC record first (resolved through the registry at FIRE
            # time, so edits to the agent apply to later fires), then a
            # builtin AgentType — a schedule and a manual spawn must never
            # disagree about what a name means. Absent = builder, exactly
            # as before this wave.
            from .core.models import AgentType as _AgentType

            # The SAME isinstance guard as the GET /schedules decode: a
            # non-string payload value (legacy/corrupt row inserted below the
            # ADD validation) is treated as ABSENT, never str()-coerced —
            # "123" could phantom-match a dynamic agent literally named
            # "123", and the fire must agree with what the list shows.
            _raw_agent = payload.get("agent_type")
            agent_name = _raw_agent.strip() if isinstance(_raw_agent, str) else ""
            agent_type = _AgentType.BUILDER
            definition = None
            provider = payload.get("provider") or None
            model = payload.get("model") or None
            if agent_name:
                registry = getattr(platform, "agents_registry", None)
                definition = (
                    registry.definition(agent_name) if registry is not None else None
                )
                if definition is not None:
                    if definition.type is _AgentType.SUPERVISOR:
                        # Mirrors the spawn route's 409 (v1.166.0): run_session
                        # reroutes SUPERVISOR-typed sessions to the builtin
                        # supervisor, which would silently discard this
                        # record's custom system prompt.
                        raise RuntimeError(
                            f"dynamic agent '{agent_name}' is based on "
                            "'supervisor' and cannot take a schedule — "
                            "re-create it with a non-supervisor base type"
                        )
                    agent_type = definition.type
                    # Parity with the spawn route: an explicit payload
                    # provider/model wins; the record's pinned pair is the
                    # fallback.
                    rec = registry.get(agent_name)
                    provider = provider or (
                        rec.provider if (rec and rec.provider) else None
                    )
                    model = model or (rec.model if (rec and rec.model) else None)
                else:
                    try:
                        agent_type = _AgentType(agent_name)
                    except ValueError:
                        # A dynamic agent deleted AFTER scheduling: fail the
                        # fire HONESTLY (recorded on the row + delivered) —
                        # never silently degrade to the builder, which would
                        # run the task without the prompt/tools the user
                        # scheduled it for.
                        raise ValueError(
                            f"scheduled agent '{agent_name}' no longer exists "
                            "— it may have been deleted; re-create it or "
                            "re-add the schedule with another agent"
                        )
            session = await platform.orchestrator.create_session(
                prompt,
                agent_type,
                provider=provider,
                model=model,
                project_id=payload.get("project_id") or None,
                origin=f"schedule:{task.name}",
            )
            fired["session_id"] = session.id
            try:
                # The definition kwarg is passed ONLY when a dynamic agent
                # resolved one: the absent-agent path stays call-signature
                # byte-identical to pre-v1.171 (callers/stubs that accept
                # only session_id keep working).
                if definition is not None:
                    done = await platform.orchestrator.run_session(
                        session.id, definition=definition
                    )
                else:
                    done = await platform.orchestrator.run_session(session.id)
            except Exception as exc:  # noqa: BLE001 - recorded, then re-raised
                # A review that died mid-run is a FAILED run, not an absent one:
                # the card must show it, and a failed run structurally cannot
                # advance the cursor. The schedule's own error path is unchanged.
                _record_memory_review(
                    review, session.id, False, f"{type(exc).__name__}: {exc}"
                )
                raise
            status = getattr(done.status, "value", str(done.status))
            summary = (done.summary or "").strip()
            _record_memory_review(
                review, session.id, status == "completed", summary or f"session {status}"
            )
            if status != "completed":
                raise RuntimeError(
                    f"session ended {status}: {(done.summary or 'no summary')[:200]}"
                )
            return summary or "session completed"
        if task.kind == "workflow":
            result = _run_scheduled_workflow(payload)
            if inspect.isawaitable(result):
                result = await result
            ref = payload.get("workflow") or payload.get("name") or "inline"
            # The run RECORD is the truth (v1.121.0): a gated workflow that
            # parked is not "done", and a failed run is not a success — the
            # old blind "ran" stamped ok the instant either happened.
            status = getattr(result, "status", None)
            if status == "failed":
                raise RuntimeError(f"workflow “{ref}” run failed — see its run history")
            if status == "waiting":
                return f"workflow “{ref}” is waiting for your answer (Workflows page)"
            return f"workflow “{ref}” ran"
        if task.kind == "event":
            etype = payload.get("type", "schedule.fired")
            result = platform.event_bus.publish(etype, payload)
            if inspect.isawaitable(result):
                await result
            return f"event {etype} published"
        raise ValueError(f"unknown schedule kind {task.kind!r}")

    def _run_scheduled(task):
        async def _fire():
            payload = json.loads(task.payload_json or "{}")
            fired: dict = {"session_id": ""}
            try:
                detail = await _dispatch_scheduled(task, payload, fired)
            except Exception as exc:  # noqa: BLE001
                # Record + deliver the failure, then swallow: the row and the
                # notification ARE the error surface. Re-raising would only
                # skip last_run stamping and dump a traceback into the
                # scheduler thread nobody watches.
                _record_outcome(
                    task.name, "error", f"{type(exc).__name__}: {exc}", fired["session_id"]
                )
                _deliver_outcome(task, payload, "error", str(exc))
                return
            _record_outcome(task.name, "ok", detail, fired["session_id"])
            _deliver_outcome(task, payload, "ok", detail)

        return _fire()

    def _run_scheduled_workflow(payload: dict):
        from .workflows.engine import WorkflowEngine, load_workflow
        from .workflows.store import WorkflowStore

        # The UI can only express a SAVED workflow by name; resolve it to its
        # stored steps. (Inline steps in the payload still work for API callers.)
        ref = payload.get("workflow") or payload.get("name")
        steps = payload.get("steps")
        if ref and not steps:
            store = WorkflowStore(platform.engine)
            rec = store.get(ref)
            if rec is None:
                raise ValueError(f"scheduled workflow {ref!r} not found")
            payload = {
                "name": rec.name,
                "steps": json.loads(rec.steps_json or "[]"),
                # A scheduled SAVED workflow keeps its project pin — the
                # whole point of pinning is recurring in-project work.
                "project_id": store.get_project_id(rec.name),
            }
        # Never silently "complete" a zero-step workflow — that masked every
        # mis-configured schedule as a success.
        if not payload.get("steps"):
            raise ValueError(
                "scheduled workflow has no steps — set a 'workflow' name or "
                "inline 'steps' in the schedule payload"
            )
        # The SHARED orchestrator (attached in v1.119.0), so the cancel route
        # can find and stop a scheduled run's step sessions — a throwaway
        # orchestrator made cancels silently ineffective.
        return WorkflowEngine(platform, platform.orchestrator).run(
            load_workflow(payload)
        )

    platform.scheduler = Scheduler(engine, _run_scheduled)

    # Dynamic agents (agents that add agents): load persisted + expose tools.
    platform.agents_registry = DynamicAgentRegistry(engine).load()
    for tool in agent_management_tools(platform, platform.agents_registry):
        platform.registry.register(tool)

    # Remote agents (agents the user runs ELSEWHERE — a Hermes on another box,
    # an OpenAI-compatible endpoint): expose the delegate_remote tool so an
    # agent can hand a task to a registered, enabled remote and get its result.
    register_remote_agent_tool(platform)

    # Dynamic tools (agents that author REUSABLE tools): load persisted custom
    # tools into the live registry (marked custom, so every agent reaches them via
    # the "custom:*" allowlist sentinel), then expose the create/list/delete tools.
    platform.tools_registry = DynamicToolRegistry(engine).load()
    for record in platform.tools_registry.list():
        platform.registry.register(
            platform.tools_registry.build_tool(record), custom=True
        )
    for tool in dynamic_tool_tools(platform):
        platform.registry.register(tool)

    # Capability requests (v1.178.0, P4): the agent asks for the tool it needs
    # instead of working around the gap in silence. Registered HERE, directly
    # after the dynamic-tool registry, because that is what an APPROVED request
    # is created through — the store calls the live `tool_create`, so this line
    # has to come after `tools_registry` exists or approval would fall back to a
    # second, unvalidated construction path.
    from .capability import CapabilityProposalStore, capability_proposal_tools

    platform.capabilities = CapabilityProposalStore(engine, platform=platform)
    for tool in capability_proposal_tools(platform.capabilities):
        platform.registry.register(tool)

    # Reflex Loop: the durable rule store (signal→action bindings). The executing
    # ReflexRouter is built by the daemon (it needs the orchestrator + task
    # launcher); the store is enough for CRUD + rule matching.
    from .reflex.store import ReflexStore

    platform.reflex = ReflexStore(engine)

    # Agent self-service: create schedules / webhooks / workflows (needs scheduler).
    from .scheduling.tools import schedule_tools
    from .webhooks.tools import webhook_tools
    from .workflows.tools import workflow_tools

    for tool in (
        *schedule_tools(platform),
        *webhook_tools(platform),
        *workflow_tools(platform),
    ):
        platform.registry.register(tool)
    # v1.170.0: workflow_list is READ-ONLY (same tier as tool_list), but the
    # permission engine fail-closes unknown keys to "ask" and a headless "ask"
    # is a DENY — so with no declared mode no agent/scheduled run could ever
    # list workflows, and live installs' config.toml (dumped from older
    # defaults at first boot) will never carry the key. Declared HERE, beside
    # the tool's registration, via setdefault so an explicit user-set mode
    # (config.toml [permissions]) always wins. workflow_run deliberately gets
    # NO entry: unknown resolves to "ask", which is exactly its intended gate.
    # BOTH copies get the default: the PermissionEngine snapshots the mapping
    # at construction (its _base is a dict COPY), so seeding only one side
    # would make display surfaces reading config.permissions (`ironjarvis
    # tools`, GET /settings) report "ask" while the engine enforces "allow" —
    # display-vs-enforcement drift. config.permissions is never persisted by
    # put_settings, so this stays in-memory; a user-set config.toml value
    # already occupies the key and setdefault leaves it alone.
    platform.permissions._base.setdefault("workflow_list", "allow")
    platform.config.permissions.setdefault("workflow_list", "allow")

    # Motivation Layer ("the pulse"): standing goals + off-by-default deliberation.
    # The orchestrator (the executor) is wired in by the daemon after build; the
    # engine is safe with it unset (deliberation stays propose-only). Its EventBus
    # subscriber maps notable signals to suggest-only backlog items, but ONLY when
    # autonomy is enabled — so the default install + tests see zero new behaviour.
    platform.intent = IntentEngine(platform)
    for tool in goal_tools(platform):
        platform.registry.register(tool)
    event_bus.add_handler(platform.intent.on_event)

    # Sentinels ("always-on watchers"): durable, suggest-only filesystem watchers
    # that NOTICE changes and mint suggest-only proposals into the Motivation Layer
    # backlog. The registry is built always (so the API/tool work), but the polling
    # runner is created ONLY when config.sentinels_enabled (OFF by default), so the
    # default install + tests see zero new behaviour. A fired Sentinel never spawns
    # a session — execution still flows through the autonomy dial + budget + approval.
    platform.sentinels = SentinelService(engine)
    for tool in sentinel_tools(platform):
        platform.registry.register(tool)

    # ImprovementEngine: the consumer of evaluation scores. Built last so it can
    # reach learning/evaluator/intent. record_outcome() is hooked into the
    # orchestrator (cheap, never-raising, runs on every session completion); the
    # model-driven reflect() stays on-demand (POST /improvement/reflect).
    platform.improvement = ImprovementEngine(platform)

    # SkillLearningEngine (v1.135.0): observes every session completion (hooked
    # in the orchestrator, cheap + never-raising) and turns qualifying runs into
    # reviewable draft skills. Its tables auto-registered when .skills imported
    # above. ``on_proposal`` stays None here — publishing the minted-proposal
    # event is daemon wiring (the daemon owns the event-loop scheduling).
    platform.skill_learning = SkillLearningEngine(platform)

    return platform
