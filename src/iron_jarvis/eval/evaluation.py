"""Evaluation Engine (SPEC §29).

Derives quality metrics for a session purely from persisted DB rows (no model
calls) and persists an :class:`Evaluation`. Deterministic and offline.
"""

from __future__ import annotations

from sqlalchemy import Engine
from sqlmodel import select

from ..core.db import session_scope
from ..core.models import AgentRun, AgentState, ToolInvocation
from .models import Evaluation


class Evaluator:
    """Scores sessions from their persisted runs and tool invocations (§29)."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def evaluate(self, session_id: str) -> Evaluation:
        """Compute and persist an :class:`Evaluation` for ``session_id`` (§29)."""
        with session_scope(self.engine) as db:
            runs = list(
                db.exec(
                    select(AgentRun)
                    .where(AgentRun.session_id == session_id)
                    .order_by(AgentRun.created_at)
                )
            )
            tools = list(
                db.exec(
                    select(ToolInvocation).where(
                        ToolInvocation.session_id == session_id
                    )
                )
            )

            # The last (most recent) run represents the session outcome.
            run = runs[-1] if runs else None

            completion = (
                1.0 if run is not None and run.state is AgentState.COMPLETED else 0.0
            )
            tool_calls = len(tools)
            ok_count = sum(1 for t in tools if t.ok)
            tool_success_rate = (ok_count / tool_calls) if tool_calls else 1.0
            step_count = run.steps if run is not None else 0
            latency_s = (
                (run.finished_at - run.created_at).total_seconds()
                if run is not None and run.finished_at is not None
                else 0.0
            )

            evaluation = Evaluation(
                session_id=session_id,
                agent_run_id=run.id if run is not None else "",
                completion=completion,
                tool_success_rate=tool_success_rate,
                tool_calls=tool_calls,
                step_count=step_count,
                latency_s=latency_s,
            )
            db.add(evaluation)
            db.commit()
            db.refresh(evaluation)
            return evaluation

    def latest(self, session_id: str) -> Evaluation | None:
        """Return the most recent persisted Evaluation for the session (§29)."""
        with session_scope(self.engine) as db:
            return db.exec(
                select(Evaluation)
                .where(Evaluation.session_id == session_id)
                .order_by(Evaluation.created_at.desc())
            ).first()
