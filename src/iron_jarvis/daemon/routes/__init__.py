"""Domain route modules for the daemon.

Each module exposes ``register(app, d)``; ``create_app`` (daemon/app.py)
builds the shared deps object ``d`` and calls each register in order.
"""

from . import (  # noqa: F401
    agents,
    audit,
    autonomy,
    chat,
    codelab,
    comm,
    computeruse,
    connections,
    connectors,
    creative,
    documents,
    fleet,
    fsbrowse,
    helpdocs,
    knowledge,
    learning,
    memory_review,
    profile,
    projects,
    reflex,
    routing,
    search,
    sessions,
    settings,
    skill_learning,
    system,
    terminals,
    triggers,
    undo,
    voice,
    workflows,
)
