"""Reflex Loop — the ambient operator.

Inbound signals (webhooks, comm messages) fire durable rules that run workflows,
remote agents, or sessions; a command grammar lets the user operate the machine
from a phone. All opt-in, all through the normal permission engine.
"""

from __future__ import annotations

from .commands import CommandInterpreter
from .models import REFLEX_ACTIONS, REFLEX_SOURCES, ReflexRule
from .router import ReflexRouter
from .store import ReflexStore

__all__ = [
    "CommandInterpreter",
    "ReflexRouter",
    "ReflexStore",
    "ReflexRule",
    "REFLEX_ACTIONS",
    "REFLEX_SOURCES",
]
