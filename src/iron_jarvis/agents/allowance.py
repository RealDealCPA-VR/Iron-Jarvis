"""A custom agent's monthly allowance and its day off (v1.295.0, the job card).

A custom agent is an EMPLOYEE: it has a job card (``DynamicAgentRecord``), a
monthly allowance (``allowance_tokens`` / ``allowance_usd``, per CALENDAR
month, 0 = unlimited) and a day off (``paused_reason``). This module is the
one place those are READ and judged:

* :func:`month_spend` — what the agent has spent this month, summed from the
  ``Session`` rows stamped with its roster name (``custom:<slug>``, the
  v1.193.0 attribution column) — tokens straight off the rows, dollars off
  ``Session.cost_usd`` (v1.300.0: each completion's own figure, so Claude
  subscription work counts at its list-price equivalent — it used to price at
  $0 and a dollar allowance never saw it), falling back to
  ``eval.pricing.cost_for`` over the tokens for rows older than the column.
* :func:`allowance_state` — the pure judgement: ``unlimited`` / ``ok`` /
  ``warning`` (>= 80%) / ``exhausted`` (>= 100%), the HIGHER ratio of the
  bounds that are set.
* :func:`refusal_for` — the sentence every door says when the agent may not
  take work (``""`` when it may). ``spawn_agent``, ``delegate``, the schedule
  fire and the HTTP spawn route all ask it BEFORE a session exists, so a
  paused or exhausted agent never silently runs as its base builder.
* :func:`after_run` — the post-run hook: an exhausted agent is PAUSED (once)
  and ``agent.paused`` goes out; a warning goes out ONCE per month
  (``allowance_warned_month`` is the dedupe).

Every reader is defensive and never raises — an allowance read sits beside
prompt composition and the post-run tail, and a broken ledger must cost the
allowance feature, not the run.
"""

from __future__ import annotations

import asyncio
import calendar
import inspect
import logging
from datetime import datetime, timezone
from typing import Any

from ..core.ids import utcnow

log = logging.getLogger("iron_jarvis.agents.allowance")

#: The roster-name prefix of a dynamic agent (``roster.py``'s contract).
CUSTOM_PREFIX = "custom:"

#: Thresholds, in percent of the allowance.
WARNING_AT = 80
EXHAUSTED_AT = 100


def month_key(now: datetime | None = None) -> str:
    """``"YYYY-MM"`` of ``now`` (UTC) — the allowance's calendar bucket."""
    moment = now or utcnow()
    return f"{moment.year:04d}-{moment.month:02d}"


def _month_bounds(now: datetime | None) -> tuple[datetime, datetime]:
    """Naive-UTC ``[start, end)`` of the calendar month holding ``now``.

    SQLite drops tzinfo on write, so ``Session.created_at`` reads back NAIVE;
    the bounds are naive too so the comparison is apples to apples.
    """
    moment = now or utcnow()
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    start = moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


def _naive(moment: Any) -> datetime | None:
    if not isinstance(moment, datetime):
        return None
    if moment.tzinfo is not None:
        return moment.astimezone(timezone.utc).replace(tzinfo=None)
    return moment


def roster_name_for(record: Any) -> str:
    """The ledger key a dynamic record's runs are credited under."""
    return f"{CUSTOM_PREFIX}{str(getattr(record, 'name', '') or '').strip()}"


def month_spend(engine, roster_name: str, now: datetime | None = None) -> dict:
    """``{"tokens", "usd", "runs"}`` this calendar month for ``roster_name``.

    Dollars: ``eval.pricing.recorded_cost`` — a row's stored ``cost_usd`` when
    it is > 0 (v1.300.0 — the sum of its completions' ``step_cost``); a
    METERED row with none (older than the column: NULL/0) is priced from its
    tokens, as before; a SUBSCRIPTION row with none is 0 (it was free when it
    ran — re-pricing it would trip the allowance on upgrade day).

    ONE query (by the indexed-by-usage ``agent_name`` column), the month
    filter applied in Python so a naive/aware mismatch in the stored stamp can
    never silently drop rows. Zeros on any error — never raises.
    """
    zero = {"tokens": 0, "usd": 0.0, "runs": 0}
    name = " ".join(str(roster_name or "").split())
    if engine is None or not name:
        return zero
    try:
        from sqlmodel import select

        from ..core.db import session_scope
        from ..core.models import Session
        from ..eval.pricing import recorded_cost

        start, end = _month_bounds(now)
        tokens = 0
        usd = 0.0
        runs = 0
        with session_scope(engine) as db:
            rows = db.exec(select(Session).where(Session.agent_name == name))
            for row in rows:
                created = _naive(getattr(row, "created_at", None))
                if created is None or not (start <= created < end):
                    continue
                try:
                    in_tok = max(0, int(getattr(row, "input_tokens", 0) or 0))
                    out_tok = max(0, int(getattr(row, "output_tokens", 0) or 0))
                except (TypeError, ValueError):
                    in_tok = out_tok = 0
                tokens += in_tok + out_tok
                usd += float(
                    recorded_cost(
                        str(getattr(row, "provider", "") or ""),
                        str(getattr(row, "model", "") or ""),
                        in_tok,
                        out_tok,
                        getattr(row, "cost_usd", 0.0),
                    )
                    or 0.0
                )
                runs += 1
        return {"tokens": int(tokens), "usd": float(usd), "runs": int(runs)}
    except Exception:  # noqa: BLE001 — a broken ledger costs the figure, not the run
        log.debug("month_spend failed for %s", name, exc_info=True)
        return zero


def _bounds(record: Any) -> tuple[int, float]:
    try:
        tokens = max(0, int(getattr(record, "allowance_tokens", 0) or 0))
    except (TypeError, ValueError):
        tokens = 0
    try:
        usd = max(0.0, float(getattr(record, "allowance_usd", 0.0) or 0.0))
    except (TypeError, ValueError):
        usd = 0.0
    return tokens, usd


def allowance_state(record: Any, spend: dict | None, now: datetime | None = None) -> dict:
    """The pure judgement of ``spend`` against ``record``'s allowance.

    Returns ``{month, tokens, usd, spent_tokens, spent_usd, runs, pct,
    left_tokens, left_usd, status}``. ``pct`` is the HIGHER ratio among the
    bounds that are set (rounded int); ``status`` is ``unlimited`` (both
    bounds 0, ``pct`` None), ``ok``, ``warning`` (>= 80) or ``exhausted``
    (>= 100). ``left_*`` is None for an unset bound. Never raises.
    """
    tokens, usd = _bounds(record)
    spend = spend or {}
    try:
        spent_tokens = max(0, int(spend.get("tokens") or 0))
    except (TypeError, ValueError):
        spent_tokens = 0
    try:
        spent_usd = max(0.0, float(spend.get("usd") or 0.0))
    except (TypeError, ValueError):
        spent_usd = 0.0
    try:
        runs = max(0, int(spend.get("runs") or 0))
    except (TypeError, ValueError):
        runs = 0
    ratios: list[float] = []
    if tokens > 0:
        ratios.append(spent_tokens / tokens)
    if usd > 0:
        ratios.append(spent_usd / usd)
    if ratios:
        # FLOOR, never round: 995 of 1,000 tokens is 99%, not "used up", and
        # $7.996 of $10 is 79%, not a warning — the status follows the TRUE
        # ratio and the shown pct never outruns it (a rounded 100 refused an
        # agent with budget left, in the words "has used its allowance").
        pct: int | None = int(max(ratios) * 100 + 1e-9)
        if pct >= EXHAUSTED_AT:
            status = "exhausted"
        elif pct >= WARNING_AT:
            status = "warning"
        else:
            status = "ok"
    else:
        pct = None
        status = "unlimited"
    return {
        "month": month_key(now),
        "tokens": tokens,
        "usd": usd,
        "spent_tokens": spent_tokens,
        "spent_usd": spent_usd,
        "runs": runs,
        "pct": pct,
        "left_tokens": max(0, tokens - spent_tokens) if tokens > 0 else None,
        "left_usd": max(0.0, usd - spent_usd) if usd > 0 else None,
        "status": status,
    }


def state_for(record: Any, engine, now: datetime | None = None) -> dict:
    """:func:`allowance_state` over the live ledger for ``record``."""
    return allowance_state(record, month_spend(engine, roster_name_for(record), now), now)


def _next_month_name(now: datetime | None) -> str:
    moment = now or utcnow()
    return calendar.month_name[1 if moment.month == 12 else moment.month + 1]


def _spent_words(state: dict) -> str:
    """``"1,200 of 1,000 tokens / $4.10 of $4.00"`` — only the bounds that are set."""
    parts: list[str] = []
    if state.get("tokens"):
        parts.append(f"{int(state['spent_tokens']):,} of {int(state['tokens']):,} tokens")
    if state.get("usd"):
        parts.append(f"${float(state['spent_usd']):.2f} of ${float(state['usd']):.2f}")
    return " / ".join(parts)


def paused_sentence(record: Any) -> str:
    """``"<name> is paused: <reason>"`` (or without the colon when the reason
    is empty); ``""`` when the record is not paused."""
    reason = " ".join(str(getattr(record, "paused_reason", "") or "").split())
    if not reason:
        return ""
    name = str(getattr(record, "name", "") or "this agent")
    if reason.lower() == "paused":  # set_paused("") stores the bare marker
        return f"{name} is paused"
    return f"{name} is paused: {reason}"


def exhausted_sentence(record: Any, state: dict, now: datetime | None = None) -> str:
    name = str(getattr(record, "name", "") or "this agent")
    return (
        f"{name} has used its monthly allowance ({_spent_words(state)}) — raise it "
        f"on the Agents page or wait for {_next_month_name(now)}"
    )


def refusal_for(record: Any, engine, now: datetime | None = None) -> str:
    """``""`` when ``record`` may run; otherwise ONE plain-words sentence.

    Paused wins over exhausted (a paused agent says why it is paused, which
    for an auto-pause already names the allowance). Never raises — on any
    error the agent may run, because a broken ledger must not be a lockout.
    """
    try:
        if record is None:
            return ""
        paused = paused_sentence(record)
        if paused:
            return paused
        tokens, usd = _bounds(record)
        if tokens <= 0 and usd <= 0:
            return ""
        state = state_for(record, engine, now)
        if state.get("status") == "exhausted":
            return exhausted_sentence(record, state, now)
        return ""
    except Exception:  # noqa: BLE001
        log.debug("refusal_for failed", exc_info=True)
        return ""


def _slug(roster_name: str) -> str:
    text = " ".join(str(roster_name or "").split())
    if not text.lower().startswith(CUSTOM_PREFIX):
        return ""
    return text[len(CUSTOM_PREFIX):].strip()


async def _publish(event_bus, type_: str, payload: dict) -> None:
    """Publish through the bus whatever shape it has (async bus, sync double)."""
    if event_bus is None:
        return
    result = event_bus.publish(type_, payload)
    if inspect.isawaitable(result):
        await result


async def after_run(
    registry, engine, event_bus, roster_name: str, now: datetime | None = None
) -> dict | None:
    """The post-run allowance step for a ``custom:<slug>`` run. Never raises.

    Recomputes the state; when EXHAUSTED and not already paused → pauses the
    record with a plain reason and publishes ``agent.paused``; when WARNING and
    this month's warning has not gone out → publishes
    ``agent.allowance_warning`` and records the month. Returns the state dict
    (with ``"action"``: ``"paused"`` | ``"warned"`` | ``""``), or ``None`` when
    the name is not a custom agent or nothing could be read. The ledger read
    runs off the loop.
    """
    try:
        slug = _slug(roster_name)
        if not slug or registry is None:
            return None
        record = registry.get(slug)
        if record is None:
            return None
        tokens, usd = _bounds(record)
        if tokens <= 0 and usd <= 0:
            return None
        spend = await asyncio.to_thread(month_spend, engine, roster_name_for(record), now)
        state = allowance_state(record, spend, now)
        state["name"] = slug
        state["action"] = ""
        month = state["month"]
        base = {
            "name": slug,
            "spent_tokens": state["spent_tokens"],
            "spent_usd": state["spent_usd"],
            "allowance_tokens": state["tokens"],
            "allowance_usd": state["usd"],
            "month": month,
        }
        from ..core.events import EventType

        if state["status"] == "exhausted":
            if not str(getattr(record, "paused_reason", "") or "").strip():
                reason = f"monthly allowance used up ({_spent_words(state)})"
                # A committed SQLite write — off the loop, like the read above.
                await asyncio.to_thread(registry.set_paused, slug, reason)
                state["action"] = "paused"
                await _publish(event_bus, EventType.AGENT_PAUSED, {**base, "reason": reason})
        elif state["status"] == "warning":
            warned = str(getattr(record, "allowance_warned_month", "") or "").strip()
            if warned != month:
                await asyncio.to_thread(registry.set_warned_month, slug, month)
                state["action"] = "warned"
                await _publish(
                    event_bus,
                    EventType.AGENT_ALLOWANCE_WARNING,
                    {**base, "pct": state["pct"]},
                )
        return state
    except Exception:  # noqa: BLE001 — the allowance tail must never break a run
        log.exception("allowance after_run failed for %s", roster_name)
        return None
