"""Platform wiring — assembles every subsystem into one object.

This is the composition root the Daemon and CLI build once. It owns mutable
global state (§9): config, event bus, persistence, providers/router, tool
registry, and the permission engine.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path

import httpx
from sqlalchemy import Engine

from .core.config import Config, load_config
from .core.db import open_db, persist_event
from .core.events import EventBus
from .core.streams import StreamHub
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
from .agents.delegate_tool import DelegateTool
from .artifacts.store import ArtifactStore
from .eval.evaluation import Evaluator
from .eval.observability import Observability
from .memory.layers import MemoryLayers
from .memory.tools import memory_tools
from .sandbox.shell_tool import SandboxedShellTool
from .skills import SkillRegistry, skill_tools
from .workflows import models as _wf_models  # noqa: F401  (registers WorkflowRunRecord)

# Robust feature set (each importing its package registers any SQLModel tables).
from .agents import dynamic_models as _dyn_models  # noqa: F401
from .agents import remote as _remote_models  # noqa: F401  (registers RemoteAgentRecord)
from .agents.remote import register_remote_agent_tool
from .agents.agent_tools import agent_management_tools
from .agents.dynamic import DynamicAgentRegistry
from .blackboard import BlackboardStore, blackboard_tools
from .blackboard import models as _bb_models  # noqa: F401  (registers BlackboardRecord)
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
from .scheduling import Scheduler
from .scheduling import models as _sched_models  # noqa: F401
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
    scheduler: Scheduler | None = None
    sentinels: "SentinelService | None" = None
    agents_registry: DynamicAgentRegistry | None = None
    tools_registry: "DynamicToolRegistry | None" = None
    intent: "IntentEngine | None" = None
    improvement: "ImprovementEngine | None" = None
    #: The SHARED embedder (real Ollama when reachable, offline mock otherwise;
    #: persistent-cached). Built once and injected into filesearch/ltm — kept on
    #: the platform so later consumers (memory graph, runtime-added LTM sources)
    #: use the SAME one instead of accidentally falling back to the mock.
    embedder: "object | None" = None
    #: The Memory Fabric — one federated ``recall()`` across every store (files,
    #: notes, memory graph, project knowledge, lessons, sessions). Powers the
    #: ``recall`` tool and the auto-grounding folded into chat + agent runs.
    fabric: "MemoryFabric | None" = None
    #: The Reflex Loop's durable rule store (signal→action bindings). The
    #: executing ReflexRouter is built by the daemon (it needs the orchestrator).
    reflex: "object | None" = None
    #: FX-01 ephemeral per-session token/tool stream hub (NOT the event bus — see
    #: core/streams.py). Optional so bare-platform unit tests still construct.
    streams: "StreamHub | None" = None
    #: LOCAL FLEET registry (the user's own inference machines). Seeded from the
    #: two config endpoint slots, so it is populated with zero setup.
    fleet: "object | None" = None


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
    fleet_registry.register_providers(providers)
    # Self-tuning router (§6 phase-1), OFF by default: only when the user opts in
    # (prefer_local_when_capable) AND a local Ollama model is configured AND it has
    # demonstrably met the quality bar for a task class do we prefer it for that
    # class. `observability` is assigned below in this same scope and exists long
    # before this closure is ever invoked (at request time), so the reference is
    # safe. When the flag is off the closure returns None and routing is unchanged.
    def _local_oracle(task_class: str | None) -> tuple[str, str] | None:
        if not getattr(config, "prefer_local_when_capable", False):
            return None
        if not getattr(config, "ollama_base_url", None):
            return None
        bar = float(getattr(config, "local_quality_bar", 0.75))
        min_samples = int(getattr(config, "local_quality_min_samples", 3))
        quality = observability.local_quality(
            "ollama",
            task_class=task_class,
            min_samples=min_samples,
            model=config.ollama_model,  # judge the model that will actually serve
        )
        if quality is not None and quality >= bar:
            return ("ollama", config.ollama_model)
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
        ) or _routing.derive_tiers(connected, latency=_routing.LATENCY.ewma)
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
    )
    registry = default_registry()

    # Phase 4: route the shell tool through the Sandbox Manager (same "shell" name).
    registry.register(SandboxedShellTool())

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
    # results, not more tools).
    skills = SkillRegistry().repopulate(
        config.home, getattr(config, "extra_skill_paths", None)
    )
    for tool in skill_tools(skills):
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

    # The ``recall`` tool is registered further down, once the learning engine
    # exists — it federates EVERY store through the Memory Fabric, not just
    # files + long-term memory (see the MemoryFabric build after LearningEngine).

    # Documents: read/write PDF, Word, Excel, PowerPoint, CSV, Markdown, text
    # (+ markdown-aware RICH creation and cross-format conversion).
    for tool in document_tools():
        registry.register(tool)

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

    # MCP auto-approve: a server the user explicitly marked trusted lets the
    # headless daemon run its tools without an interactive prompt, so autonomous
    # agents (and the Reflex Loop) can actually USE it — not just chat, which
    # already approves-by-arming. Opt-in per server, default off. Coarse by
    # design: mcp_call is one shared perm key, so trusting any server trusts
    # every connected MCP tool (applied at boot; add a server then restart).
    _mcp_auto = any(
        (s or {}).get("auto_approve")
        for s in (getattr(config, "mcp_servers", None) or [])
    )
    if ask_resolver is not None and _mcp_auto:
        _base_ask = ask_resolver

        def ask_resolver(name: str, args: dict, _b=_base_ask) -> bool:  # type: ignore[misc]
            return True if name == "mcp_call" else _b(name, args)

    permissions = PermissionEngine(config.permissions, ask_resolver=ask_resolver)

    platform = Platform(
        config=config,
        event_bus=event_bus,
        streams=streams,
        engine=engine,
        vault=vault,
        providers=providers,
        router=router,
        registry=registry,
        permissions=permissions,
        memory=memory,
        skills=skills,
        artifacts=artifacts,
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
    )

    # Phase 6: the delegate tool needs the assembled platform.
    platform.registry.register(DelegateTool(platform))

    # Departments: the shared, session-scoped blackboard. Sibling sub-agents of
    # one task resolve to ONE board (their root session id) so they can post
    # findings and message each other instead of only summarizing upward.
    platform.blackboard = BlackboardStore(engine)
    for tool in blackboard_tools(platform.blackboard):
        platform.registry.register(tool)

    # Scheduled tasks (cron): a task runs a workflow or emits an event on fire.
    def _run_scheduled(task):
        payload = json.loads(task.payload_json or "{}")
        if task.kind == "workflow":
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
            return WorkflowEngine(platform).run(load_workflow(payload))
        if task.kind == "event":
            return platform.event_bus.publish(
                payload.get("type", "schedule.fired"), payload
            )
        return None

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

    return platform
