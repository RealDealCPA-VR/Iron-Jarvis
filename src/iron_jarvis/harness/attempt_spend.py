"""WHAT AN ATTEMPT SPENT, for the model team's pay-per-use limit (v1.397.0
review, ``harness/attempts``).

The long-jobs plan promises that nothing spends pay-per-use money above the
monthly limit the user sets (``Config.team_paid_cap_usd``), and work the user
starts counts toward it (``agents/model_team.pay_refusal``). An attempt on a
pay-per-use model is work the user started ("try this with Gemini and
Codex"), and its usage rows are the chat lane's (``session_id="chat"`` with
the Build thread's id, the same as the session's own turns) — so nothing told
an attempt's money apart, and ``model_team.paid_spend_this_month`` never saw
it: a $5 limit let any number of $5 attempts and team pieces start.

ONE ROW PER ATTEMPT that spent METERED money (never a subscription's
list-price equivalent, never a local model's nothing), written when the
attempt ends (finished, failed, stopped or cut off by a shutdown): a late
model (``core.db._LATE_MODEL_MODULES``). :func:`metered_between` is what the
team's month reads. BLOCKING (SQLite); never raises.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow

log = logging.getLogger(__name__)


class AttemptSpendRecord(SQLModel, table=True):
    """The metered dollars one attempt spent."""

    __tablename__ = "attemptspendrecord"

    id: str = Field(default_factory=lambda: new_id("aspend"), primary_key=True)
    #: The Build session (its thread id) and the turn the attempts ran in.
    thread_id: str = Field(default="", index=True)
    turn_id: str = ""
    #: The attempt's id on the card ("try1").
    attempt: str = ""
    provider: str = ""
    model: str = ""
    cost_usd: float = 0.0
    created_at: datetime = Field(default_factory=utcnow, index=True)


def record(engine: Any, *, thread_id: str, turn_id: str, attempt: str, provider: str, model: str,
           cost_usd: float) -> bool:
    """Keep one attempt's metered spend (nothing for $0). BLOCKING; never
    raises (False when it could not be written)."""
    try:
        cost = float(cost_usd or 0.0)
    except (TypeError, ValueError):
        return False
    if engine is None or not cost > 0:
        return False
    try:
        from ..core.db import session_scope

        with session_scope(engine) as db:
            db.add(AttemptSpendRecord(
                thread_id=str(thread_id or "")[:80], turn_id=str(turn_id or "")[:120],
                attempt=str(attempt or "")[:20], provider=str(provider or "")[:120],
                model=str(model or "")[:200], cost_usd=round(cost, 9),
            ))
            db.commit()
        return True
    except Exception:  # noqa: BLE001 — the turn is never failed over its bookkeeping
        log.warning("recording an attempt's spend failed", exc_info=True)
        return False


def _naive(moment: Any) -> "datetime | None":
    if not isinstance(moment, datetime):
        return None
    if moment.tzinfo is not None:
        return moment.astimezone(timezone.utc).replace(tzinfo=None)
    return moment


def metered_between(engine: Any, start: datetime, end: datetime) -> float:
    """The attempts' metered dollars written in ``[start, end)`` (naive UTC,
    the allowance's month bounds). BLOCKING; 0.0 when it cannot be read."""
    if engine is None:
        return 0.0
    try:
        from sqlmodel import select

        from ..core.db import session_scope

        total = 0.0
        with session_scope(engine) as db:
            rows = db.exec(select(AttemptSpendRecord).where(
                AttemptSpendRecord.created_at >= start, AttemptSpendRecord.created_at < end,  # type: ignore[operator]
            ))
            for row in rows:
                created = _naive(getattr(row, "created_at", None))
                if created is None or not (start <= created < end):
                    continue
                total += max(0.0, float(row.cost_usd or 0.0))
        return total
    except Exception:  # noqa: BLE001
        log.debug("reading the attempts' spend failed", exc_info=True)
        return 0.0


__all__ = ["AttemptSpendRecord", "metered_between", "record"]
