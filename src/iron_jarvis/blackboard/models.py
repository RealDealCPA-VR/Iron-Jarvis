"""Blackboard persistence model (Departments substrate).

A :class:`BlackboardRecord` is one entry on a *department* board — a finding
posted by an agent (``kind=note``) or a directed message to a sibling
(``kind=message``). The board is scoped by ``board_id`` (the department's root
session id, see :func:`iron_jarvis.blackboard.store.resolve_board_id`) so the
sibling sub-agents of one task share ONE board and never see another team's notes.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow


class BlackboardKind(str, enum.Enum):
    NOTE = "note"  # a posted finding, visible to the whole department
    MESSAGE = "message"  # a directed note addressed to a specific sibling


class BlackboardRecord(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("bb"), primary_key=True)
    #: department scope — the root session id shared by all sibling sub-agents.
    board_id: str = Field(index=True)
    author: str = ""  # the agent_run_id of the posting agent
    kind: BlackboardKind = BlackboardKind.NOTE
    to_agent: str | None = None  # recipient agent_run_id, for directed messages
    text: str = ""
    created_at: datetime = Field(default_factory=utcnow)
