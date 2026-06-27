"""Motivation Layer ("the pulse") — standing goals + autonomous deliberation.

Iron Jarvis today only acts when given a task or when a pre-set schedule fires.
This package adds the ability to hold *standing goals*, periodically DELIBERATE
on the single highest-value next action, and either propose it or — within strict,
off-by-default governance (per-goal autonomy dial + rolling budget + global kill
switch + dry-run) — do it, so the system can run ahead of the user.

Importing this module before ``init_db`` registers :class:`GoalRecord` and
:class:`ProposalRecord` on the shared metadata so they auto-create.
"""

from __future__ import annotations

from .engine import IntentEngine
from .models import GoalRecord, ProposalRecord
from .tools import GoalAddTool, GoalListTool, goal_tools

__all__ = [
    "IntentEngine",
    "GoalRecord",
    "ProposalRecord",
    "GoalAddTool",
    "GoalListTool",
    "goal_tools",
]
