"""Departments substrate: a session/department-scoped shared blackboard.

Sibling sub-agents of one task share ONE board (keyed by their root session id)
so they can post findings and address each other instead of only summarizing
upward. Pure-DB and offline-safe.
"""

from __future__ import annotations

from .models import BlackboardKind, BlackboardRecord
from .store import BlackboardStore, resolve_board_id
from .tools import blackboard_tools

__all__ = [
    "BlackboardKind",
    "BlackboardRecord",
    "BlackboardStore",
    "resolve_board_id",
    "blackboard_tools",
]
