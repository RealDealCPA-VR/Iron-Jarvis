"""Platform wiring — assembles every subsystem into one object.

This is the composition root the Daemon and CLI build once. It owns mutable
global state (§9): config, event bus, persistence, providers/router, tool
registry, and the permission engine.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import httpx
from sqlalchemy import Engine

from .core.config import Config, load_config
from .core.db import init_db, make_engine, persist_event
from .core.events import EventBus
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
from .skills import SkillRegistry, builtin_dir, skill_tools
from .workflows import models as _wf_models  # noqa: F401  (registers WorkflowRunRecord)

# Robust feature set (each importing its package registers any SQLModel tables).
from .agents import dynamic_models as _dyn_models  # noqa: F401
from .agents.agent_tools import agent_management_tools
from .agents.dynamic import DynamicAgentRegistry
from .blackboard import BlackboardStore, blackboard_tools
from .blackboard import models as _bb_models  # noqa: F401  (registers BlackboardRecord)
from .comm import Notifier, build_notifier, httpx_post, notify_tools
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
from .memory.recall import recall_tools
from .scheduling import Scheduler
from .scheduling import models as _sched_models  # noqa: F401
from .secrets import SecretsManager, secret_tools
from .secrets import models as _sec_models  # noqa: F401
from .webhooks import InboundWebhooks, OutboundWebhooks
from .webhooks import models as _whk_models  # noqa: F401

# Documents (all file types) + self-correcting learning loop.
from .documents import document_tools

# Web search (keyless) + MCP client (consume external MCP servers).
from .tools.websearch import web_search_tools
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
    agents_registry: DynamicAgentRegistry | None = None
    tools_registry: "DynamicToolRegistry | None" = None
    intent: "IntentEngine | None" = None
    improvement: "ImprovementEngine | None" = None


def build_platform(
    project_root: str, ask_resolver: AskResolver | None = None
) -> Platform:
    config = load_config(project_root)
    config.ensure_dirs()

    event_bus = EventBus()
    engine = make_engine(config.db_path)
    init_db(engine)

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

    # Secrets vault + LLM Connections (OAuth2/PKCE + API key) — built early so the
    # provider manager resolves live credentials and reports REAL availability.
    secrets = SecretsManager(config.home, engine)
    connections = ConnectionRegistry(
        engine,
        secrets,
        http_factory=lambda: httpx.Client(timeout=30),
        oauth_app=lambda provider: {
            "client_id": secrets.get(f"{provider}_oauth_client_id"),
            "client_secret": secrets.get(f"{provider}_oauth_client_secret"),
            "redirect_uri": (
                secrets.get(f"{provider}_oauth_redirect_uri")
                or f"http://localhost:8787/oauth/{provider}/callback"
            ),
        },
    )

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
    )
    router = ModelRouter(providers, config.default_provider, event_bus)
    registry = default_registry()

    # Phase 4: route the shell tool through the Sandbox Manager (same "shell" name).
    registry.register(SandboxedShellTool())

    # Phase 5: layered memory + retrieval, exposed as tools.
    memory = MemoryLayers(engine, config=config)
    for tool in memory_tools(memory):
        registry.register(tool)

    # Phase 11: skills framework (builtin + project-local), exposed as tools.
    skills = SkillRegistry().discover(builtin_dir(), config.home / "skills")
    for tool in skill_tools(skills):
        registry.register(tool)

    # Phase 8a / 9: artifact store, evaluation + observability.
    artifacts = ArtifactStore(config.artifacts_dir, engine)
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
    )
    for tool in computeruse_tools(computeruse):
        registry.register(tool)

    # Terminals: multiple live shell sessions the dashboard can attach to.
    terminals = TerminalManager()

    # --- Robust feature set ----------------------------------------------

    # Secrets vault (built above) — expose its agent tools.
    for tool in secret_tools(secrets):
        registry.register(tool)

    # Integrations framework + built-in generic/mock integrations.
    integrations = IntegrationRegistry(engine)
    register_builtins(integrations)
    for tool in integration_tools(integrations, secrets.get):
        registry.register(tool)

    # File search across configured roots. The embedder is chosen ONCE here and
    # shared by filesearch + ltm: a real local model (Ollama) when one is
    # reachable, else the deterministic offline MockEmbedder. Wrapping it in the
    # persistent embedding cache (engine) makes re-indexing incremental and
    # survive restarts (§22 Total Recall).
    embedder = build_embedder(config, engine)
    search_roots = [Path(r) for r in config.search_roots] or [config.project_root]
    filesearch = FileSearchService(search_roots, embedder=embedder)
    for tool in filesearch_tools(filesearch):
        registry.register(tool)

    # Communication channels + Notifier (auto-alerts on selected events).
    notifier = build_notifier(
        getattr(config, "comm", None), secret_resolver=secrets.get, http_post=httpx_post
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
    # User-configured custom LTM sources (markdown dirs / Notion DBs), persisted.
    load_custom_sources(
        ltm,
        engine,
        secret_resolver=secrets.get,
        http_factory=lambda: httpx.Client(timeout=30),
    )
    for tool in ltm_tools(ltm):
        registry.register(tool)

    # Total Recall: one semantic "remember anything" tool over the SAME embedder,
    # spanning the indexed file roots + long-term memory.
    for tool in recall_tools(filesearch, ltm):
        registry.register(tool)

    # Documents: read/write PDF, Word, Excel, PowerPoint, CSV, Markdown, text.
    for tool in document_tools():
        registry.register(tool)

    # Web search: keyless DuckDuckGo by default; Brave if a key is in the vault.
    for tool in web_search_tools(secret_resolver=secrets.get):
        registry.register(tool)

    # External MCP servers (Gmail/Drive/GitHub/...) as native tools. Empty
    # config (the default) is a safe no-op; an unreachable server is skipped.
    for tool in mcp_tools(getattr(config, "mcp_servers", None), secret_resolver=secrets.get):
        registry.register(tool)

    # Self-correcting learning loop: feedback + reflections become lessons that
    # get injected into every future agent prompt (gets better each interaction).
    learning = LearningEngine(engine)
    for tool in learning_tools(learning):
        registry.register(tool)

    permissions = PermissionEngine(config.permissions, ask_resolver=ask_resolver)

    platform = Platform(
        config=config,
        event_bus=event_bus,
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

    # ImprovementEngine: the consumer of evaluation scores. Built last so it can
    # reach learning/evaluator/intent. record_outcome() is hooked into the
    # orchestrator (cheap, never-raising, runs on every session completion); the
    # model-driven reflect() stays on-demand (POST /improvement/reflect).
    platform.improvement = ImprovementEngine(platform)

    return platform
