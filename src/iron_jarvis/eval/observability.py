"""Observability (SPEC §30).

Read-side views over the persisted event log and evaluations: per-session
traces for replay/debugging and aggregate metrics for dashboards.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import Engine, and_, func, or_
from sqlmodel import select

from ..core.db import session_scope
from ..core.ids import utcnow
from ..core.models import (
    AgentRun,
    EventRecord,
    PermissionMode,
    Session,
    ToolInvocation,
    UndoJournal,
)
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
        def _agent_value(at: object) -> str:
            return getattr(at, "value", at) if at is not None else ""

        try:
            with session_scope(self.engine) as db:
                # Filter runs to this provider (+ model) IN SQL, then pull only the
                # Evaluations for those sessions — never load the whole (unbounded)
                # AgentRun + Evaluation tables into Python. This is the router's hot
                # path; it was O(all rows) per call.
                run_stmt = select(AgentRun.session_id, AgentRun.agent_type).where(
                    AgentRun.provider == provider
                )
                if model is not None:
                    run_stmt = run_stmt.where(AgentRun.model == model)
                sess_types: dict[str, set[str]] = {}
                for sid, at in db.exec(run_stmt):
                    sess_types.setdefault(sid, set()).add(_agent_value(at))
                if not sess_types:
                    return None
                eval_rows = list(
                    db.exec(
                        select(Evaluation.session_id, Evaluation.completion).where(
                            Evaluation.session_id.in_(list(sess_types.keys()))
                        )
                    )
                )
        except Exception:  # pragma: no cover - degrade rather than crash
            return None

        scores: list[float] = [
            float(completion)
            for sid, completion in eval_rows
            if task_class is None or task_class in sess_types.get(sid, set())
        ]

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

    def timeline(self, **filters) -> dict:
        """TX-01 canonical audit timeline (§30).

        Thin delegate to :class:`AuditTimeline` — one time-ordered stream of
        :class:`AuditEntry`-shaped dicts projected from the event log + tool
        invocations. See :meth:`AuditTimeline.query` for the accepted filters.
        """
        return AuditTimeline(self.engine).query(**filters)


# ---------------------------------------------------------------------------
# TX-01 audit timeline: EventRecord + ToolInvocation -> canonical AuditEntry
# ---------------------------------------------------------------------------
#
# Every audited row (a persisted event OR a tool invocation) collapses into ONE
# canonical shape so the dashboard renders a single, unified "what happened"
# feed:
#
#   {id, ts, kind, actor, session_id, project_id, tool, verdict, ok,
#    input_tokens, output_tokens, cost_usd, reversible, undoable, summary,
#    payload}
#
# ``kind`` is one of: action | tool | token | decision | lifecycle.

_KIND_TOOL = "tool"
_KIND_TOKEN = "token"
_KIND_DECISION = "decision"
_KIND_LIFECYCLE = "lifecycle"
_KIND_ACTION = "action"

#: The stored reversibility value that makes an undo *offerable* (see undoable).
_REVERSIBLE = "reversible"

#: A tool call is ALSO carried by its ToolInvocation row (keyed by the payload
#: ``invocation_id``), so these two event types are MERGED into that row rather
#: than listed a second time from the event stream.
_TOOL_EVENT_TYPES = ("tool.executed", "tool.denied")
_TOKEN_EVENT_TYPES = ("llm.completed",)
_LIFECYCLE_EVENT_TYPES = (
    "session.created",
    "session.completed",
    "agent.started",
    "agent.state_changed",
    "agent.completed",
)
#: Decision events that live purely in the event log. ``tool.denied`` is a
#: decision too, but it is projected from the ToolInvocation stream (deny
#: verdict) so it is NOT repeated here.
_DECISION_EVENT_TYPES = (
    "provider.routed",
    "provider.failover",
    "provider.downgraded",
    "autonomy.proposed",
    "autonomy.executed",
)


def _iso(value) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _parse_dt(value) -> "datetime | None":
    """Parse an ISO timestamp into a NAIVE-UTC datetime (matching how SQLite
    stores ``created_at``), or ``None`` when absent/unparseable."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _encode_cursor(created_at, entry_id: str) -> str:
    return f"{_iso(created_at)}|{entry_id}"


def _decode_cursor(cursor) -> "tuple[datetime, str] | None":
    if not cursor or "|" not in str(cursor):
        return None
    ts_s, _, id_s = str(cursor).rpartition("|")
    dt = _parse_dt(ts_s)
    if dt is None or not id_s:
        return None
    return (dt, id_s)


def _classify_event(etype: str) -> str:
    if etype in _TOKEN_EVENT_TYPES:
        return _KIND_TOKEN
    if etype in _LIFECYCLE_EVENT_TYPES:
        return _KIND_LIFECYCLE
    if etype in _DECISION_EVENT_TYPES:
        return _KIND_DECISION
    return _KIND_ACTION


def _event_summary(etype: str, payload: dict) -> str:
    if etype in _TOKEN_EVENT_TYPES:
        return (
            f"{payload.get('provider', '')}/{payload.get('model', '')} "
            f"· {int(payload.get('input_tokens') or 0)}+"
            f"{int(payload.get('output_tokens') or 0)} tok"
        )
    if etype == "agent.state_changed":
        return f"agent {payload.get('from', '')}→{payload.get('to', '')}"
    if etype in _DECISION_EVENT_TYPES:
        detail = (
            payload.get("model")
            or payload.get("action")
            or payload.get("tier")
            or ""
        )
        return f"{etype} {detail}".strip()
    return etype


class AuditTimeline:
    """TX-01 read-model (§30): one time-ordered stream of canonical audit rows.

    Projects the (unbounded) event log + tool-invocation ledger into a single
    ``AuditEntry`` shape, newest-first, with keyset pagination on
    ``(created_at, id)``. All filtering happens IN SQL over indexed columns
    (following the :meth:`Observability.metrics`/``traces`` discipline) — the
    two source tables are never materialized into Python; at most ``2 * limit``
    already-filtered rows cross the boundary per page.

    A tool call appears exactly ONCE: its ToolInvocation row is the canonical
    entry (id == the event payload's ``invocation_id``), and the
    ``tool.executed``/``tool.denied`` events are excluded from the event stream.
    """

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def query(
        self,
        *,
        session_id: str | None = None,
        type: str | None = None,
        tool: str | None = None,
        actor: str | None = None,
        kind: str | None = None,
        since=None,
        until=None,
        limit: int = 100,
        before=None,
    ) -> dict:
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 100
        limit = max(1, min(limit, 500))

        since_dt = _parse_dt(since)
        until_dt = _parse_dt(until)
        cursor = _decode_cursor(before)
        want_total = cursor is None

        kind = (kind or "").strip().lower() or None
        etype = (type or "").strip() or None

        # Decide which source streams contribute + how, from the kind filter.
        include_tools = True
        include_events = True
        tool_verdict: str | None = None  # None=both | "allow" | "deny"
        event_types_in: tuple | None = None
        event_types_ex: tuple = _TOOL_EVENT_TYPES

        if kind == _KIND_TOOL:
            include_events = False
            tool_verdict = "allow"
        elif kind == _KIND_DECISION:
            tool_verdict = "deny"
            event_types_in = _DECISION_EVENT_TYPES
        elif kind == _KIND_TOKEN:
            include_tools = False
            event_types_in = _TOKEN_EVENT_TYPES
        elif kind == _KIND_LIFECYCLE:
            include_tools = False
            event_types_in = _LIFECYCLE_EVENT_TYPES
        elif kind == _KIND_ACTION:
            include_tools = False
            event_types_in = None
            event_types_ex = (
                _TOOL_EVENT_TYPES
                + _TOKEN_EVENT_TYPES
                + _LIFECYCLE_EVENT_TYPES
                + _DECISION_EVENT_TYPES
            )
        elif kind is not None:
            return {"entries": [], "next_cursor": None, "total": 0}

        # A raw event-type filter further constrains the streams (and can map a
        # tool.* type back onto the ToolInvocation stream).
        if etype is not None:
            if etype == "tool.executed":
                include_events = False
                include_tools = include_tools and tool_verdict != "deny"
                tool_verdict = "allow"
            elif etype == "tool.denied":
                include_events = False
                include_tools = include_tools and tool_verdict != "allow"
                tool_verdict = "deny"
            else:
                include_tools = False
                if event_types_in is None:
                    if etype in event_types_ex:
                        include_events = False
                    else:
                        event_types_in = (etype,)
                elif etype in event_types_in:
                    event_types_in = (etype,)
                else:
                    include_events = False

        # A tool-name filter only makes sense against the tool-invocation stream
        # (event rows carry no canonical tool column), so it excludes events.
        if tool:
            include_events = False

        entries: list[dict] = []
        tool_len = 0
        event_len = 0
        total_tools = 0
        total_events = 0

        with session_scope(self.engine) as db:
            if include_tools:
                base = self._tool_conditions(
                    session_id, tool, actor, since_dt, until_dt, tool_verdict
                )
                stmt = (
                    select(
                        ToolInvocation.id,
                        ToolInvocation.created_at,
                        ToolInvocation.session_id,
                        ToolInvocation.agent_run_id,
                        ToolInvocation.tool,
                        ToolInvocation.verdict,
                        ToolInvocation.ok,
                        ToolInvocation.output,
                        ToolInvocation.reversibility,
                        ToolInvocation.undone_at,
                        Session.origin,
                        Session.project_id,
                        UndoJournal.action_id,
                    )
                    .join(
                        Session,
                        ToolInvocation.session_id == Session.id,
                        isouter=True,
                    )
                    .join(
                        UndoJournal,
                        UndoJournal.action_id == ToolInvocation.id,
                        isouter=True,
                    )
                )
                conds = list(base)
                if cursor is not None:
                    conds.append(
                        self._keyset(
                            ToolInvocation.created_at, ToolInvocation.id, cursor
                        )
                    )
                if conds:
                    stmt = stmt.where(and_(*conds))
                stmt = stmt.order_by(
                    ToolInvocation.created_at.desc(), ToolInvocation.id.desc()
                ).limit(limit)
                rows = list(db.exec(stmt))
                tool_len = len(rows)
                for r in rows:
                    entries.append(self._tool_entry(r))
                if want_total:
                    total_tools = self._count_tools(db, base, actor)

            if include_events:
                base = self._event_conditions(
                    session_id,
                    actor,
                    since_dt,
                    until_dt,
                    event_types_in,
                    event_types_ex,
                )
                stmt = select(
                    EventRecord.id,
                    EventRecord.created_at,
                    EventRecord.type,
                    EventRecord.session_id,
                    EventRecord.payload_json,
                    Session.origin,
                    Session.project_id,
                ).join(
                    Session, EventRecord.session_id == Session.id, isouter=True
                )
                conds = list(base)
                if cursor is not None:
                    conds.append(
                        self._keyset(EventRecord.created_at, EventRecord.id, cursor)
                    )
                if conds:
                    stmt = stmt.where(and_(*conds))
                stmt = stmt.order_by(
                    EventRecord.created_at.desc(), EventRecord.id.desc()
                ).limit(limit)
                rows = list(db.exec(stmt))
                event_len = len(rows)
                for r in rows:
                    entries.append(self._event_entry(r))
                if want_total:
                    total_events = self._count_events(db, base, actor)

        # Merge the two already-sorted, bounded streams and truncate to one page.
        entries.sort(key=lambda e: e["_sort"], reverse=True)
        page = entries[:limit]
        # A next page may exist if the merge overflowed OR either stream returned
        # a full window (rows may lie beyond the truncation boundary). Erring
        # toward an extra empty page never drops a row.
        has_more = len(entries) > limit or tool_len == limit or event_len == limit
        next_cursor = None
        if page and has_more:
            last = page[-1]
            next_cursor = _encode_cursor(last["_sort"][0], last["id"])
        for e in page:
            e.pop("_sort", None)

        out: dict = {"entries": page, "next_cursor": next_cursor}
        if want_total:
            out["total"] = int(total_tools + total_events)
        return out

    # -- condition builders (shared by the page query + the total count) -----

    @staticmethod
    def _keyset(ts_col, id_col, cursor):
        c_ts, c_id = cursor
        return or_(ts_col < c_ts, and_(ts_col == c_ts, id_col < c_id))

    @staticmethod
    def _tool_conditions(session_id, tool, actor, since_dt, until_dt, verdict):
        conds = []
        if session_id:
            conds.append(ToolInvocation.session_id == session_id)
        if tool:
            conds.append(ToolInvocation.tool == tool)
        if actor:
            conds.append(Session.origin == actor)
        if since_dt is not None:
            conds.append(ToolInvocation.created_at >= since_dt)
        if until_dt is not None:
            conds.append(ToolInvocation.created_at <= until_dt)
        if verdict == "deny":
            conds.append(ToolInvocation.verdict == PermissionMode.DENY)
        elif verdict == "allow":
            conds.append(ToolInvocation.verdict != PermissionMode.DENY)
        return conds

    @staticmethod
    def _event_conditions(
        session_id, actor, since_dt, until_dt, types_in, types_ex
    ):
        conds = []
        if session_id:
            conds.append(EventRecord.session_id == session_id)
        if actor:
            conds.append(Session.origin == actor)
        if since_dt is not None:
            conds.append(EventRecord.created_at >= since_dt)
        if until_dt is not None:
            conds.append(EventRecord.created_at <= until_dt)
        if types_in is not None:
            # An explicit include-set REPLACES the exclude-set (it is already a
            # subset of the non-merged types); it is always non-empty here.
            conds.append(EventRecord.type.in_(tuple(types_in)))
        elif types_ex:
            conds.append(EventRecord.type.notin_(tuple(types_ex)))
        return conds

    @staticmethod
    def _count_tools(db, base, actor) -> int:
        stmt = select(func.count()).select_from(ToolInvocation)
        if actor:
            stmt = stmt.join(
                Session, ToolInvocation.session_id == Session.id, isouter=True
            )
        if base:
            stmt = stmt.where(and_(*base))
        return int(db.scalar(stmt) or 0)

    @staticmethod
    def _count_events(db, base, actor) -> int:
        stmt = select(func.count()).select_from(EventRecord)
        if actor:
            stmt = stmt.join(
                Session, EventRecord.session_id == Session.id, isouter=True
            )
        if base:
            stmt = stmt.where(and_(*base))
        return int(db.scalar(stmt) or 0)

    # -- row -> AuditEntry projection ----------------------------------------

    @staticmethod
    def _tool_entry(row) -> dict:
        (
            tid,
            created,
            sid,
            run_id,
            tname,
            verdict,
            ok,
            output,
            reversibility,
            undone_at,
            origin,
            project_id,
            undo_present,
        ) = row
        verdict_val = getattr(verdict, "value", verdict)
        is_deny = verdict_val == PermissionMode.DENY.value
        reversible = reversibility == _REVERSIBLE
        undoable = bool(
            reversible and undone_at is None and undo_present is not None
        )
        kind = _KIND_DECISION if is_deny else _KIND_TOOL
        if is_deny:
            summary = f"denied {tname}: {(output or '')[:120]}"
        else:
            summary = f"{tname} {'ok' if ok else 'error'}"
        # A settings change is journaled with session_id=="settings" and no Session
        # row, so origin is NULL — without this it would mislabel a USER settings
        # change as an agent action on the trust timeline.
        actor = origin or ("you" if sid == "settings" else "agent")
        return {
            "id": tid,
            "ts": _iso(created),
            "_sort": (created, tid),
            "kind": kind,
            "actor": actor,
            "session_id": sid,
            "project_id": project_id,
            "tool": tname,
            "verdict": verdict_val,
            "ok": bool(ok),
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
            "reversible": bool(reversible),
            "undoable": undoable,
            # Explicit: has THIS action's inverse actually been applied? The UI must
            # not infer "reversed" from reversible-and-not-undoable — a reversible
            # action whose capture returned no inverse (or that failed) is not-
            # undoable yet was never reversed.
            "undone": undone_at is not None,
            "summary": summary,
            "payload": {
                "agent_run_id": run_id,
                "invocation_id": tid,
                "output": (output or "")[:200],
            },
        }

    @staticmethod
    def _event_entry(row) -> dict:
        (eid, created, etype, sid, payload_json, origin, project_id) = row
        try:
            payload = json.loads(payload_json)
            if not isinstance(payload, dict):
                payload = {}
        except (ValueError, TypeError):
            payload = {}
        kind = _classify_event(etype)
        in_tok = out_tok = 0
        cost = 0.0
        if kind == _KIND_TOKEN:
            in_tok = int(payload.get("input_tokens") or 0)
            out_tok = int(payload.get("output_tokens") or 0)
            cost = payload.get("cost_usd")
            if cost is None:
                cost = pricing.cost_for(
                    payload.get("provider", ""),
                    payload.get("model", ""),
                    in_tok,
                    out_tok,
                )
            cost = round(float(cost or 0.0), 6)
        return {
            "id": eid,
            "ts": _iso(created),
            "_sort": (created, eid),
            "kind": kind,
            "actor": origin or etype,
            "session_id": sid,
            "project_id": project_id,
            "tool": payload.get("tool") or "",
            "verdict": payload.get("mode"),
            "ok": bool(payload.get("ok", True)),
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cost_usd": cost,
            "reversible": False,
            "undoable": False,
            "summary": _event_summary(etype, payload),
            "payload": payload,
        }
