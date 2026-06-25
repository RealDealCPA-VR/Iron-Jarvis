"""Observability (SPEC §30).

Read-side views over the persisted event log and evaluations: per-session
traces for replay/debugging and aggregate metrics for dashboards.
"""

from __future__ import annotations

import json

from sqlalchemy import Engine
from sqlmodel import select

from ..core.db import session_scope
from ..core.models import EventRecord, ToolInvocation
from .models import Evaluation


class Observability:
    """Trace + metric reads over the event log and evaluations (§30)."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def traces(self, session_id: str) -> list[dict]:
        """Ordered event trace for a session, oldest first (§30)."""
        with session_scope(self.engine) as db:
            records = list(
                db.exec(
                    select(EventRecord)
                    .where(EventRecord.session_id == session_id)
                    .order_by(EventRecord.created_at)
                )
            )
        out: list[dict] = []
        for r in records:
            try:
                payload = json.loads(r.payload_json)
            except (ValueError, TypeError):
                payload = {}
            ts = r.created_at
            out.append(
                {
                    "type": r.type,
                    "ts": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                    "payload": payload,
                }
            )
        return out

    def metrics(self) -> dict:
        """Aggregate metrics across every Evaluation + the event log (§30)."""
        with session_scope(self.engine) as db:
            evals = list(db.exec(select(Evaluation)))
            tool_count = len(list(db.exec(select(ToolInvocation))))
            event_count = len(list(db.exec(select(EventRecord))))

        def avg(values: list[float]) -> float:
            return sum(values) / len(values) if values else 0.0

        return {
            "sessions_evaluated": len(evals),
            "avg_completion": avg([e.completion for e in evals]),
            "avg_tool_success_rate": avg([e.tool_success_rate for e in evals]),
            "avg_latency_s": avg([e.latency_s for e in evals]),
            "total_tool_invocations": tool_count,
            "event_count": event_count,
        }
