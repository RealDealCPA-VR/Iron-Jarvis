"""Observability (SPEC §30).

Read-side views over the persisted event log and evaluations: per-session
traces for replay/debugging and aggregate metrics for dashboards.
"""

from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy import Engine, func
from sqlmodel import select

from ..core.db import session_scope
from ..core.ids import utcnow
from ..core.models import AgentRun, EventRecord, ToolInvocation
from . import pricing
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
            # COUNT/AVG in SQLite (rowid/index-cheap) instead of materializing these
            # tables — ToolInvocation and EventRecord are UNBOUNDED and this endpoint
            # is polled every few seconds by the dashboard. Was O(rows) + full alloc.
            sessions_evaluated = db.scalar(select(func.count()).select_from(Evaluation)) or 0
            avg_completion = db.scalar(select(func.avg(Evaluation.completion)))
            avg_tool_success = db.scalar(select(func.avg(Evaluation.tool_success_rate)))
            avg_latency = db.scalar(select(func.avg(Evaluation.latency_s)))
            tool_count = db.scalar(select(func.count()).select_from(ToolInvocation)) or 0
            event_count = db.scalar(select(func.count()).select_from(EventRecord)) or 0

        return {
            "sessions_evaluated": sessions_evaluated,
            "avg_completion": float(avg_completion or 0.0),
            "avg_tool_success_rate": float(avg_tool_success or 0.0),
            "avg_latency_s": float(avg_latency or 0.0),
            "total_tool_invocations": tool_count,
            "event_count": event_count,
        }

    def local_quality(
        self,
        provider: str,
        task_class: str | None = None,
        min_samples: int = 3,
        model: str | None = None,
    ) -> float | None:
        """Average completion score for evaluated sessions that ran on ``provider``.

        Optionally filtered to a task class (the agent type). Returns ``None`` when
        there aren't at least ``min_samples`` evaluated sessions to judge from —
        the caller treats "not enough evidence" as "don't prefer the local model".
        Read-only and defensive: never raises (a bad/empty DB yields ``None``).

        This is the evidence the self-tuning router (§6) consults: only once a
        local model has *demonstrably* met a quality bar for a class of work do we
        start preferring it for that class.
        """
        try:
            with session_scope(self.engine) as db:
                evals = list(db.exec(select(Evaluation)))
                runs = list(db.exec(select(AgentRun)))
        except Exception:  # pragma: no cover - degrade rather than crash
            return None

        by_session: dict[str, list[AgentRun]] = {}
        for r in runs:
            by_session.setdefault(r.session_id, []).append(r)

        def _agent_value(at: object) -> str:
            return getattr(at, "value", at) if at is not None else ""

        scores: list[float] = []
        for e in evals:
            rs = by_session.get(e.session_id, [])
            if not rs:
                continue
            if not any((r.provider or "") == provider for r in rs):
                continue
            if model is not None and not any((r.model or "") == model for r in rs):
                continue  # don't credit a different local model's track record
            if task_class is not None and not any(
                _agent_value(r.agent_type) == task_class for r in rs
            ):
                continue
            scores.append(float(e.completion))

        if len(scores) < max(1, int(min_samples)):
            return None
        return sum(scores) / len(scores)

    def usage_summary(self, since_days: int = 30) -> dict:
        """Cost/usage analytics over AgentRun rows in the last ``since_days``.

        Aggregates token usage and estimated USD cost (via
        :func:`pricing.cost_for`) over the window, returning per-day and
        per-(provider, model) breakdowns for the dashboard. Never raises; an
        empty or unreadable window yields zeroed totals and empty lists so the
        ``/usage`` endpoint and daemon stay up.

        Returns a dict shaped::

            {
              "since_days": int,
              "totals": {input_tokens, output_tokens, cost_usd, runs},
              "by_day": [{day, input_tokens, output_tokens, cost_usd}, ...],
              "by_model": [
                  {provider, model, input_tokens, output_tokens, cost_usd, runs},
                  ...
              ],
            }
        """
        empty = {
            "since_days": int(since_days),
            "totals": {
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
                "runs": 0,
            },
            "by_day": [],
            "by_model": [],
        }
        try:
            days = max(0, int(since_days))
        except (TypeError, ValueError):
            return empty

        cutoff = utcnow() - timedelta(days=days)

        try:
            with session_scope(self.engine) as db:
                runs = list(
                    db.exec(
                        select(AgentRun).where(AgentRun.created_at >= cutoff)
                    )
                )
        except Exception:  # pragma: no cover - degrade rather than crash
            return empty

        totals = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "runs": 0}
        by_day: dict[str, dict] = {}
        by_model: dict[tuple[str, str], dict] = {}

        for run in runs:
            provider = run.provider or ""
            model = run.model or ""
            in_tok = int(run.input_tokens or 0)
            out_tok = int(run.output_tokens or 0)
            cost = pricing.cost_for(provider, model, in_tok, out_tok)

            totals["input_tokens"] += in_tok
            totals["output_tokens"] += out_tok
            totals["cost_usd"] += cost
            totals["runs"] += 1

            ts = run.created_at
            day = (
                ts.date().isoformat() if hasattr(ts, "date") else str(ts)
            )
            d = by_day.setdefault(
                day,
                {"day": day, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
            )
            d["input_tokens"] += in_tok
            d["output_tokens"] += out_tok
            d["cost_usd"] += cost

            key = (provider, model)
            m = by_model.setdefault(
                key,
                {
                    "provider": provider,
                    "model": model,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost_usd": 0.0,
                    "runs": 0,
                },
            )
            m["input_tokens"] += in_tok
            m["output_tokens"] += out_tok
            m["cost_usd"] += cost
            m["runs"] += 1

        totals["cost_usd"] = round(totals["cost_usd"], 6)
        for d in by_day.values():
            d["cost_usd"] = round(d["cost_usd"], 6)
        for m in by_model.values():
            m["cost_usd"] = round(m["cost_usd"], 6)

        return {
            "since_days": days,
            "totals": totals,
            "by_day": [by_day[k] for k in sorted(by_day)],
            "by_model": sorted(
                by_model.values(),
                key=lambda r: r["cost_usd"],
                reverse=True,
            ),
        }
