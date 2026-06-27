"""Blackboard store (Departments substrate).

Pure-DB, offline-safe shared space scoped to a *department*. A department is a
root session plus all of its sibling sub-agents; they share ONE board so they
can post findings and address each other instead of only summarizing upward.

The board id is the ROOT session id: :func:`resolve_board_id` walks the
``AgentRun`` ``parent_id`` chain up from the calling agent to the root run and
returns that root run's session id. A supervisor (a root run) and every
descendant sub-agent therefore resolve to the same board, while an unrelated
task — with its own root — resolves to a different board (scoping/isolation).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Engine
from sqlmodel import select

from ..core.db import session_scope
from ..core.models import AgentRun
from .models import BlackboardKind, BlackboardRecord


def resolve_board_id(engine: Engine, session_id: str, agent_run_id: str) -> str:
    """Return the department board id for a running agent.

    Walks the ``AgentRun`` parent chain to the root and returns the root run's
    session id. Falls back to ``session_id`` when the run can't be resolved (e.g.
    a tool invoked outside a persisted run), which keeps each call scoped to at
    least its own session — never a shared/global board.
    """
    seen: set[str] = set()
    with session_scope(engine) as db:
        run = db.get(AgentRun, agent_run_id)
        if run is None:
            return session_id
        while run.parent_id and run.parent_id not in seen:
            seen.add(run.id)
            parent = db.get(AgentRun, run.parent_id)
            if parent is None:
                break
            run = parent
        return run.session_id


class BlackboardStore:
    """Post/read/list over the department-scoped :class:`BlackboardRecord` table."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def board_id_for(self, session_id: str, agent_run_id: str) -> str:
        return resolve_board_id(self.engine, session_id, agent_run_id)

    def post(
        self,
        board_id: str,
        author: str,
        text: str,
        *,
        kind: BlackboardKind = BlackboardKind.NOTE,
        to_agent: str | None = None,
    ) -> BlackboardRecord:
        record = BlackboardRecord(
            board_id=board_id,
            author=author,
            kind=kind,
            to_agent=to_agent,
            text=text,
        )
        # Fresh session per call (no shared Session across concurrent coroutines);
        # add+commit has no await between them, so the write is atomic w.r.t. the
        # cooperative scheduler.
        with session_scope(self.engine) as db:
            db.add(record)
            db.commit()
            db.refresh(record)
        return record

    def list(
        self,
        board_id: str,
        *,
        since: datetime | None = None,
        to_agent: str | None = None,
    ) -> list[BlackboardRecord]:
        with session_scope(self.engine) as db:
            stmt = select(BlackboardRecord).where(BlackboardRecord.board_id == board_id)
            if since is not None:
                stmt = stmt.where(BlackboardRecord.created_at > since)
            if to_agent is not None:
                stmt = stmt.where(BlackboardRecord.to_agent == to_agent)
            # Deterministic chronological order; id is a stable tiebreak.
            stmt = stmt.order_by(BlackboardRecord.created_at, BlackboardRecord.id)
            return list(db.exec(stmt))

    def roster(self, board_id: str) -> list[dict]:
        """The team roster — distinct agents who have posted on this board, with a
        friendly handle (their agent_type) and post count. This is how a sibling
        DISCOVERS teammates to ``message_agent`` (you address by agent_run_id):
        once a teammate contributes a note, it becomes addressable here."""
        with session_scope(self.engine) as db:
            rows = list(
                db.exec(
                    select(BlackboardRecord).where(
                        BlackboardRecord.board_id == board_id
                    )
                )
            )
            counts: dict[str, int] = {}
            for r in rows:
                counts[r.author] = counts.get(r.author, 0) + 1
            out: list[dict] = []
            for run_id, posts in counts.items():
                run = db.get(AgentRun, run_id)
                handle = getattr(run.agent_type, "value", "agent") if run else "agent"
                out.append({"agent_run_id": run_id, "handle": handle, "posts": posts})
            return out
