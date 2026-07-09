"""Domain route modules for the daemon.

Each module exposes ``register(app, d)``; ``create_app`` (daemon/app.py)
builds the shared deps object ``d`` and calls each register in order.
"""

from . import (  # noqa: F401
    agents,
    autonomy,
    chat,
    comm,
    computeruse,
    connections,
    creative,
    documents,
    fsbrowse,
    knowledge,
    learning,
    projects,
    reflex,
    sessions,
    settings,
    system,
    terminals,
    voice,
    workflows,
)
