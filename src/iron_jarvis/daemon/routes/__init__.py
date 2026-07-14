"""Domain route modules for the daemon.

Each module exposes ``register(app, d)``; ``create_app`` (daemon/app.py)
builds the shared deps object ``d`` and calls each register in order.
"""

from . import (  # noqa: F401
    agents,
    audit,
    autonomy,
    chat,
    comm,
    computeruse,
    connections,
    connectors,
    creative,
    documents,
    fsbrowse,
    knowledge,
    learning,
    projects,
    reflex,
    routing,
    sessions,
    settings,
    system,
    terminals,
    triggers,
    undo,
    voice,
    workflows,
)
