"""Platform wiring — assembles every subsystem into one object.

This is the composition root the Daemon and CLI build once. It owns mutable
global state (§9): config, event bus, persistence, providers/router, tool
registry, and the permission engine.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine

from .core.config import Config, load_config
from .core.db import init_db, make_engine, persist_event
from .core.events import EventBus
from .core.logging import get_logger
from .providers.manager import ProviderManager
from .providers.router import ModelRouter
from .providers.vault import BrowserVault
from .tools.builtins import default_registry
from .tools.permissions import AskResolver, PermissionEngine
from .tools.registry import ToolRegistry


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
    providers = ProviderManager(vault=vault, default_model=config.default_model)
    router = ModelRouter(providers, config.default_provider, event_bus)
    registry = default_registry()
    permissions = PermissionEngine(config.permissions, ask_resolver=ask_resolver)

    return Platform(
        config=config,
        event_bus=event_bus,
        engine=engine,
        vault=vault,
        providers=providers,
        router=router,
        registry=registry,
        permissions=permissions,
    )
