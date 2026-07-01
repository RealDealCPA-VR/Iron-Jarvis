"""IntentEngine — the Motivation Layer's deliberation loop ("the pulse").

The engine holds standing :class:`GoalRecord` goals and, when the user has opted
in (``config.autonomy_enabled``), periodically :meth:`deliberate` on the single
highest-value next action. Each tick makes ONE lightweight model call (via the
router, mock-friendly + injectable), turns the answer into a
:class:`ProposalRecord`, and — only when the goal's dial + the action's risk +
the remaining budget ALL permit, the kill switch is off, and dry-run is off —
auto-executes it through :meth:`Orchestrator.create_session`. Otherwise the
proposal stays ``pending`` for a human to approve.

SAFETY (see the daemon report for the full model):
  * OFF by default — :meth:`deliberate` no-ops unless ``autonomy_enabled``.
  * SUGGEST by default — every action is a proposal until the dial is raised.
  * Budget + kill switch + dry-run are all enforced in :meth:`_may_autoexecute`
    BEFORE any session is created.
  * Fully offline-safe: with the mock model the call is deterministic, never
    crashes, and always yields a valid proposal (a heuristic fallback covers an
    unparseable / empty model reply).
"""

from __future__ import annotations

import asyncio
import inspect
import json
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

from sqlmodel import select

from ..agents.types import get_agent_definition
from ..core.db import session_scope
from ..core.events import EventType
from ..core.ids import utcnow
from ..core.logging import get_logger
from ..core.models import AgentType
from .models import (
    AUTONOMY_LEVELS,
    GoalRecord,
    ProposalRecord,
    RISKS,
)

log = get_logger("motivation")

# A deliberator may be injected for tests/offline determinism: it receives the
# gathered context dict and returns a decision dict (or None to fall back).
Deliberator = Callable[[dict[str, Any]], "dict[str, Any] | None | Awaitable[Any]"]

#: How far back the global rolling budget window looks (hours).
_BUDGET_WINDOW_HOURS = 24
#: How many recent events to feed the deliberation prompt.
_RECENT_EVENTS = 12
#: Risk levels each dial may auto-execute (high is NEVER auto, by design).
_DIAL_AUTOEXEC: dict[str, set[str]] = {
    "suggest": set(),
    "act_low": {"low"},
    "act_all": {"low", "med"},
}
#: Cap on pending (un-acted) proposals per goal so suggest-mode ticks can't pile
#: up an unbounded backlog; once full, a tick reuses the existing queue.
_MAX_PENDING_PER_GOAL = 5


def _level_rank(level: str) -> int:
    try:
        return AUTONOMY_LEVELS.index(level)
    except ValueError:
        return 0  # unknown -> most restrictive (suggest)


class IntentEngine:
    """Standing goals + the deliberate→propose→(maybe)act loop."""

    def __init__(
        self,
        platform,
        orchestrator=None,
        *,
        deliberator: Deliberator | None = None,
    ) -> None:
        self.p = platform
        self.orchestrator = orchestrator
        self._deliberator = deliberator
        # Serializes the budget check->book->execute span so concurrent ticks
        # (background loop + a manual /autonomy/tick) can't both spend the budget.
        self._exec_lock = asyncio.Lock()

    # -- goals -------------------------------------------------------------

    def add_goal(
        self,
        text: str,
        *,
        source: str = "user",
        category: str = "general",
        priority: int = 3,
        autonomy_level: str = "suggest",
    ) -> GoalRecord:
        """Record a standing goal. Recording NEVER acts — acting is gated later by
        the goal's dial + budget + ``config.autonomy_enabled``."""
        text = (text or "").strip()
        if not text:
            raise ValueError("goal text is required")
        if autonomy_level not in AUTONOMY_LEVELS:
            autonomy_level = "suggest"
        if source not in ("user", "inferred", "event"):
            source = "user"
        rec = GoalRecord(
            text=text,
            source=source,
            category=(category or "general").strip() or "general",
            priority=max(1, min(5, int(priority))),
            autonomy_level=autonomy_level,
        )
        with session_scope(self.p.engine) as db:
            db.add(rec)
            db.commit()
            db.refresh(rec)
        return rec

    def list_goals(self, status: str | None = None) -> list[GoalRecord]:
        with session_scope(self.p.engine) as db:
            q = select(GoalRecord)
            if status:
                q = q.where(GoalRecord.status == status)
            q = q.order_by(GoalRecord.priority.desc(), GoalRecord.created_at)
            return list(db.exec(q))

    def get_goal(self, goal_id: str) -> GoalRecord | None:
        with session_scope(self.p.engine) as db:
            return db.get(GoalRecord, goal_id)

    def update_goal(self, goal_id: str, **fields: Any) -> GoalRecord | None:
        """Tune a goal's dial / budget / status / priority. Unknown keys ignored."""
        allowed = {
            "text", "category", "priority", "autonomy_level", "status",
            "action_budget", "spend_budget", "actions_taken", "tokens_spent",
        }
        with session_scope(self.p.engine) as db:
            rec = db.get(GoalRecord, goal_id)
            if rec is None:
                return None
            for key, value in fields.items():
                if key not in allowed or value is None:
                    continue
                if key == "autonomy_level" and value not in AUTONOMY_LEVELS:
                    continue
                # Reject an unknown status (would drop the goal from every
                # list_goals(status=...) filter and make it vanish from the UI).
                if key == "status" and value not in ("active", "paused", "done", "abandoned"):
                    continue
                if key == "priority":
                    value = max(1, min(5, int(value)))
                setattr(rec, key, value)
            db.add(rec)
            db.commit()
            db.refresh(rec)
            return rec

    # -- proposals ---------------------------------------------------------

    def list_proposals(self, status: str | None = None) -> list[ProposalRecord]:
        with session_scope(self.p.engine) as db:
            q = select(ProposalRecord)
            if status:
                q = q.where(ProposalRecord.status == status)
            q = q.order_by(ProposalRecord.created_at.desc())
            return list(db.exec(q))

    def get_proposal(self, proposal_id: str) -> ProposalRecord | None:
        with session_scope(self.p.engine) as db:
            return db.get(ProposalRecord, proposal_id)

    def _create_proposal(
        self,
        *,
        goal_id: str | None,
        title: str,
        rationale: str,
        agent_type: str,
        task: str,
        risk: str,
        source: str = "deliberation",
    ) -> ProposalRecord:
        rec = ProposalRecord(
            goal_id=goal_id,
            title=title[:200],
            rationale=rationale[:2000],
            action_json=json.dumps({"agent_type": agent_type, "task": task}),
            risk=risk if risk in RISKS else "med",
            source=source,
        )
        with session_scope(self.p.engine) as db:
            db.add(rec)
            db.commit()
            db.refresh(rec)
        return rec

    def add_backlog(
        self,
        *,
        title: str,
        task: str,
        risk: str = "low",
        source: str = "sentinel",
        agent_type: str = "builder",
        rationale: str = "",
        goal_id: str | None = None,
        dedupe: bool = True,
    ) -> ProposalRecord | None:
        """Public, thin minting helper for EXTERNAL suggest-only producers.

        Sentinels (and the file trigger) call this to surface a noticed signal as
        a SUGGEST-ONLY :class:`ProposalRecord` in the backlog — exactly like the
        deliberation/event paths, but without reaching into private methods. It
        ONLY proposes: it never spawns a session (execution still flows through
        the autonomy dial + budget + approval).

        When ``dedupe`` (the default), an existing *pending* proposal with the same
        ``title`` + ``source`` is reused (returns it) so a repeating signal can't
        pile up an unbounded backlog. Never raises — returns None on failure so a
        bad signal can't break the producer's loop.
        """
        title = (title or "").strip()
        if not title:
            return None
        try:
            if dedupe:
                with session_scope(self.p.engine) as db:
                    existing = db.exec(
                        select(ProposalRecord).where(
                            ProposalRecord.title == title[:200],
                            ProposalRecord.status == "pending",
                            ProposalRecord.source == source,
                        )
                    ).first()
                    if existing is not None:
                        # Fold the new signal into the pending proposal instead of
                        # silently dropping it: refresh the rationale to the newest
                        # change so an accumulating watcher never loses an update.
                        if rationale and rationale != existing.rationale:
                            existing.rationale = rationale[:2000]
                            db.add(existing)
                            db.commit()
                            db.refresh(existing)
                        return existing
            return self._create_proposal(
                goal_id=goal_id,
                title=title,
                rationale=rationale,
                agent_type=agent_type,
                task=task,
                risk=risk,
                source=source,
            )
        except Exception:  # noqa: BLE001 — a mint failure must not break the caller
            log.exception("add_backlog mint failed for %r", title)
            return None

    # -- budget + governance ----------------------------------------------

    def _global_window_usage(self) -> tuple[int, int]:
        """(actions, tokens) auto-executed across all goals within the window."""
        cutoff = utcnow() - timedelta(hours=_BUDGET_WINDOW_HOURS)
        with session_scope(self.p.engine) as db:
            rows = list(
                db.exec(
                    select(ProposalRecord).where(
                        ProposalRecord.status == "executed",
                        ProposalRecord.created_at >= cutoff,
                    )
                )
            )
        return len(rows), sum(r.tokens for r in rows)

    def effective_level(self, goal: GoalRecord) -> str:
        """The goal's dial, capped by the global ``config.autonomy_level`` ceiling
        so lowering the global dial throttles EVERY goal at once."""
        global_level = getattr(self.p.config, "autonomy_level", "suggest")
        if _level_rank(global_level) < _level_rank(goal.autonomy_level):
            return global_level
        return goal.autonomy_level

    def _may_autoexecute(self, goal: GoalRecord, risk: str) -> tuple[bool, str]:
        """Decide if an action MAY be auto-executed. Returns (ok, reason).

        Every gate must pass: kill switch off, dry-run off, the (effective) dial
        permits this risk, and BOTH the global rolling budget and the per-goal
        budget have headroom. High risk is never auto-executed by any dial.
        """
        cfg = self.p.config
        if getattr(cfg, "autonomy_kill_switch", False):
            return False, "kill_switch"
        if getattr(cfg, "autonomy_dry_run", False):
            return False, "dry_run"
        level = self.effective_level(goal)
        if risk not in _DIAL_AUTOEXEC.get(level, set()):
            return False, f"dial '{level}' does not auto-execute {risk}-risk"
        # Per-goal budget.
        if goal.actions_taken >= goal.action_budget:
            return False, "goal action_budget exhausted"
        if goal.tokens_spent >= goal.spend_budget:
            return False, "goal spend_budget exhausted"
        # Global rolling budget.
        used_actions, used_tokens = self._global_window_usage()
        max_actions = int(getattr(cfg, "autonomy_max_actions_per_day", 5))
        max_tokens = int(getattr(cfg, "autonomy_max_tokens_per_day", 50000))
        if used_actions >= max_actions:
            return False, "global daily action budget exhausted"
        if used_tokens >= max_tokens:
            return False, "global daily token budget exhausted"
        return True, "ok"

    # -- deliberation ------------------------------------------------------

    async def deliberate(self, now: datetime | None = None, *, wait: bool = False) -> dict:
        """One pulse: gather context, make ONE lightweight call, propose / act.

        Returns a structured outcome (always — never raises). With autonomy off
        (the default) it no-ops immediately, so tests and the default install are
        untouched. ``wait`` runs an auto-executed session to completion (used by
        tests); the background tick leaves it False (fire-and-await the session
        inside the tick is acceptable, but tests assert on completion).
        """
        cfg = self.p.config
        if not getattr(cfg, "autonomy_enabled", False):
            return {"ran": False, "reason": "autonomy_disabled"}
        if getattr(cfg, "autonomy_kill_switch", False):
            return {"ran": False, "reason": "kill_switch"}
        goals = self.list_goals(status="active")
        if not goals:
            return {"ran": False, "reason": "no_active_goals"}
        # Skip the (expensive Opus) PLANNER call when every active goal already has a
        # full pending backlog: deliberation could only dedupe or hit the cap, so the
        # call is pure waste. This stops the steady-state token burn on an idle
        # daily driver where suggestions accumulate faster than they're reviewed.
        if self._all_backlogs_full(goals):
            return {"ran": False, "reason": "backlog_full"}

        context = self._gather_context(goals)
        decision = await self._decide(context, goals)
        goal = self._resolve_goal(decision.get("goal_id"), goals)
        proposal, created = self._find_or_create_proposal(goal, decision)
        out: dict[str, Any] = {
            "ran": True,
            "proposal_id": proposal.id,
            "goal_id": goal.id,
            "risk": proposal.risk,
            "executed": False,
            "deduped": not created,
            "auto_reason": "deduped" if not created else "",
            "dry_run": bool(getattr(cfg, "autonomy_dry_run", False)),
        }
        if not created:
            return out  # an equivalent proposal is already queued — don't re-act
        await self.p.event_bus.publish(
            EventType.AUTONOMY_PROPOSED,
            {
                "proposal_id": proposal.id,
                "goal_id": goal.id,
                "title": proposal.title,
                "risk": proposal.risk,
            },
        )
        # Serialize the check->book span so concurrent ticks can't over-spend.
        async with self._exec_lock:
            may, reason = self._may_autoexecute(goal, proposal.risk)
            out["auto_reason"] = reason
            if may and self.orchestrator is not None:
                session = await self._execute(proposal, goal, wait=wait)
                out["executed"] = True
                out["session_id"] = session.id if session else None
        return out

    def _all_backlogs_full(self, goals: list[GoalRecord]) -> bool:
        """True if EVERY active goal already has a full pending backlog — a fresh
        deliberation could only dedupe or be capped, so the LLM call is pure waste."""
        goal_ids = [g.id for g in goals]
        if not goal_ids:
            return False
        with session_scope(self.p.engine) as db:
            rows = list(
                db.exec(
                    select(ProposalRecord).where(
                        ProposalRecord.goal_id.in_(goal_ids),
                        ProposalRecord.status == "pending",
                    )
                )
            )
        counts: dict[str, int] = {}
        for r in rows:
            counts[r.goal_id] = counts.get(r.goal_id, 0) + 1
        return all(counts.get(gid, 0) >= _MAX_PENDING_PER_GOAL for gid in goal_ids)

    def _find_or_create_proposal(
        self, goal: GoalRecord, decision: dict
    ) -> tuple[ProposalRecord, bool]:
        """Dedupe + cap the pending backlog: reuse an identical pending proposal,
        or refuse to grow past the per-goal cap. Returns (proposal, created)."""
        title = decision["title"][:200]
        with session_scope(self.p.engine) as db:
            pending = list(
                db.exec(
                    select(ProposalRecord)
                    .where(
                        ProposalRecord.goal_id == goal.id,
                        ProposalRecord.status == "pending",
                    )
                    .order_by(ProposalRecord.created_at)
                )
            )
        for p in pending:  # identical action already queued — reuse it
            if p.title == title:
                return p, False
        if len(pending) >= _MAX_PENDING_PER_GOAL:  # backlog full — don't pile up
            return pending[0], False
        rec = self._create_proposal(
            goal_id=goal.id,
            title=decision["title"],
            rationale=decision["rationale"],
            agent_type=decision["agent_type"],
            task=decision["task"],
            risk=decision["risk"],
        )
        return rec, True

    def _gather_context(self, goals: list[GoalRecord]) -> dict[str, Any]:
        pending = self.list_proposals(status="pending")
        events = [
            {"type": e.type, "ts": e.ts}
            for e in list(self.p.event_bus.history)[-_RECENT_EVENTS:]
        ]
        used_actions, used_tokens = self._global_window_usage()
        cfg = self.p.config
        return {
            "goals": [
                {
                    "id": g.id, "text": g.text, "priority": g.priority,
                    "category": g.category, "autonomy_level": g.autonomy_level,
                }
                for g in goals
            ],
            "open_proposals": [
                {"id": p.id, "title": p.title, "goal_id": p.goal_id} for p in pending
            ],
            "recent_events": events,
            "budget_remaining": {
                "actions": max(0, int(getattr(cfg, "autonomy_max_actions_per_day", 5)) - used_actions),
                "tokens": max(0, int(getattr(cfg, "autonomy_max_tokens_per_day", 50000)) - used_tokens),
            },
        }

    async def _decide(self, context: dict, goals: list[GoalRecord]) -> dict:
        """Make the single lightweight decision call (injected or via router)."""
        raw: Any = None
        if self._deliberator is not None:
            raw = self._deliberator(context)
            if inspect.isawaitable(raw):
                raw = await raw
        else:
            raw = await self._router_decide(context)
        return self._normalize(raw, goals)

    async def _router_decide(self, context: dict) -> dict | None:
        """One lightweight PLANNER call asking for the highest-value next action.

        No tools are offered (we want a short text/JSON answer, not a session), so
        this is far cheaper than running a full agent loop per tick.
        """
        from ..providers.adapters.base import LLMMessage

        system = (
            get_agent_definition(AgentType.PLANNER).system_prompt
            + "\n\nYou are the Motivation Layer. Given the standing goals, open "
            "proposals, recent events, and remaining budget, choose the SINGLE "
            "highest-value next action. Reply ONLY with a compact JSON object: "
            '{"goal_id": str, "title": str, "rationale": str, '
            '"agent_type": "builder"|"planner"|"researcher"|"automation", '
            '"task": str, "risk": "low"|"med"|"high"}. Prefer low-risk, concrete, '
            "reversible actions; rate anything that touches the outside world or is "
            "hard to undo as high risk."
        )
        prompt = json.dumps(context, default=str)[:6000]
        try:
            route = await self.p.router.complete(
                system=system,
                messages=[LLMMessage(role="user", content=prompt)],
                tools=[],
            )
        except Exception:  # noqa: BLE001 - never let a model error break the tick
            log.exception("deliberation model call failed; using heuristic fallback")
            return None
        return self._parse_json(route.response.text)

    @staticmethod
    def _parse_json(text: str) -> dict | None:
        text = (text or "").strip()
        if not text:
            return None
        # Tolerate a fenced or chatty reply by extracting the first {...} span.
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
            return data if isinstance(data, dict) else None
        except (TypeError, ValueError):
            return None

    def _normalize(self, raw: dict | None, goals: list[GoalRecord]) -> dict:
        """Coerce any decision (incl. None / garbage) into a valid proposal dict.

        The heuristic fallback guarantees the offline/mock path always yields a
        sound proposal: advance the highest-priority active goal with a low-key
        planning action rated medium risk (so it does NOT auto-execute on a bare
        ``act_low`` dial without an explicit risk).
        """
        top = goals[0]  # list_goals is ordered by priority desc
        raw = raw if isinstance(raw, dict) else {}

        agent = str(raw.get("agent_type") or "builder").lower()
        valid_agents = {a.value for a in AgentType}
        if agent not in valid_agents:
            agent = "builder"

        risk = str(raw.get("risk") or "med").lower()
        if risk not in RISKS:
            risk = "med"

        task = str(raw.get("task") or "").strip()
        goal_id = raw.get("goal_id")
        goal = self._resolve_goal(goal_id, goals)
        if not task:
            task = (
                f"Make concrete progress on the standing goal: {goal.text}. "
                "Take one small, reversible step and summarise what you did."
            )
        title = str(raw.get("title") or "").strip() or f"Advance: {goal.text[:80]}"
        rationale = str(raw.get("rationale") or "").strip() or (
            "Highest-priority active goal with no in-flight work toward it."
        )
        return {
            "goal_id": goal.id,
            "title": title,
            "rationale": rationale,
            "agent_type": agent,
            "task": task,
            "risk": risk,
        }

    @staticmethod
    def _resolve_goal(goal_id: Any, goals: list[GoalRecord]) -> GoalRecord:
        if goal_id:
            for g in goals:
                if g.id == goal_id:
                    return g
        return goals[0]

    # -- execution ---------------------------------------------------------

    async def _execute(
        self, proposal: ProposalRecord, goal: GoalRecord, *, wait: bool
    ) -> Any:
        """Spawn the proposal's action as a real session and book the budget.

        Called only after governance has cleared it (auto-exec) or a human has
        approved it. Token usage is read back from the run when ``wait`` so the
        rolling budget reflects real spend.
        """
        action = proposal.decoded_action()
        agent_type = AgentType.BUILDER
        try:
            agent_type = AgentType(action.get("agent_type", "builder"))
        except ValueError:
            agent_type = AgentType.BUILDER
        # A MAINTAINER proposal patches Iron Jarvis's OWN source, so route it onto a
        # self-dev worktree (review-gated, never auto-merge). create_session raises
        # when self_dev_enabled is off, so approval FAILS CLOSED on that gate rather
        # than silently running a maintainer in a throwaway workspace.
        is_self_dev = agent_type == AgentType.MAINTAINER
        session = await self.orchestrator.create_session(
            action.get("task", goal.text), agent_type, self_dev=is_self_dev
        )
        # Book the action against the budget NOW (before the run finishes) so a
        # concurrent tick can't double-spend; mark the proposal executed durably.
        self._book(proposal.id, goal.id, session.id, tokens=0)
        await self.p.event_bus.publish(
            EventType.AUTONOMY_EXECUTED,
            {"proposal_id": proposal.id, "goal_id": goal.id, "session_id": session.id},
            session_id=session.id,
        )

        if wait:
            # Tests + synchronous callers: run to completion and reconcile spend.
            try:
                ran = await self.orchestrator.run_session(session.id)
                tokens = int(getattr(ran, "input_tokens", 0)) + int(
                    getattr(ran, "output_tokens", 0)
                )
                self._reconcile_tokens(proposal.id, goal.id, tokens)
            except Exception:  # noqa: BLE001 - a failed autonomous run still counts
                log.exception("autonomous session %s failed", session.id)
        else:
            # Daemon tick: actually run it, in the background, cancellable like any
            # other session. Reconcile real token spend once it completes.
            async def _run_and_book(_sid=session.id, _pid=proposal.id, _gid=goal.id):
                try:
                    ran = await self.orchestrator.run_session(_sid)
                    self._reconcile_tokens(
                        _pid, _gid,
                        int(getattr(ran, "input_tokens", 0))
                        + int(getattr(ran, "output_tokens", 0)),
                    )
                except asyncio.CancelledError:  # pragma: no cover
                    raise
                except Exception:  # noqa: BLE001
                    log.exception("autonomous session %s failed", _sid)

            try:
                task = asyncio.create_task(_run_and_book())
                self.orchestrator.register_running(session.id, task)
            except RuntimeError:  # pragma: no cover - no running loop
                log.warning("no event loop to run autonomous session %s", session.id)
        return session

    def _book(self, proposal_id: str, goal_id: str, session_id: str, *, tokens: int) -> None:
        """Mark a proposal executed + increment the goal's action budget (durable)."""
        with session_scope(self.p.engine) as db:
            p = db.get(ProposalRecord, proposal_id)
            if p is not None:
                p.status = "executed"
                p.session_id = session_id
                p.tokens = tokens
                db.add(p)
            g = db.get(GoalRecord, goal_id)
            if g is not None:
                g.actions_taken += 1
                g.tokens_spent += tokens
                g.last_acted_at = utcnow()
                db.add(g)
            db.commit()

    def _reconcile_tokens(self, proposal_id: str, goal_id: str, tokens: int) -> None:
        """Add the run's real token spend to the proposal + goal after completion."""
        if tokens <= 0:
            return
        with session_scope(self.p.engine) as db:
            p = db.get(ProposalRecord, proposal_id)
            if p is not None:
                p.tokens += tokens
                db.add(p)
            g = db.get(GoalRecord, goal_id)
            if g is not None:
                g.tokens_spent += tokens
                db.add(g)
            db.commit()

    def reconcile_executing_proposals(self) -> int:
        """Boot recovery for the H1 claim window: a hard crash between approve()'s
        claim (status→executing) and _book (status→executed) leaves a proposal
        durably 'executing' — invisible to the pending list and non-retryable
        (approve/_claim_for_execution accept only pending/approved). Reset any such
        orphan back to 'pending' on boot so it can be approved again (mirrors
        reconcile_interrupted_sessions for sessions). Returns the count reset."""
        with session_scope(self.p.engine) as db:
            rows = list(
                db.exec(select(ProposalRecord).where(ProposalRecord.status == "executing"))
            )
            for p in rows:
                p.status = "pending"
                db.add(p)
            if rows:
                db.commit()
        return len(rows)

    def _claim_for_execution(self, proposal_id: str) -> bool:
        """Atomically transition pending/approved → executing; True iff WE claimed
        it. A compare-and-set so two concurrent approvals of one proposal can't both
        pass the status check and double-run it / double-book the budget."""
        with session_scope(self.p.engine) as db:
            p = db.get(ProposalRecord, proposal_id)
            if p is None or p.status not in ("pending", "approved"):
                return False
            p.status = "executing"
            db.add(p)
            db.commit()
            return True

    def _reset_proposal_status(self, proposal_id: str, status: str) -> None:
        with session_scope(self.p.engine) as db:
            p = db.get(ProposalRecord, proposal_id)
            if p is not None:
                p.status = status
                db.add(p)
                db.commit()

    async def approve(self, proposal_id: str, *, wait: bool = False) -> Any:
        """Human approval path: execute a pending proposal as a real session.

        Bypasses the autonomy DIAL (a human said yes) but still books budget and
        refuses to act when the global kill switch is engaged."""
        proposal = self.get_proposal(proposal_id)
        if proposal is None:
            raise KeyError(f"unknown proposal '{proposal_id}'")
        if proposal.status not in ("pending", "approved"):
            raise ValueError(f"proposal is already {proposal.status}")
        if getattr(self.p.config, "autonomy_kill_switch", False):
            raise PermissionError("autonomy kill switch is engaged")
        if self.orchestrator is None:
            raise RuntimeError("no orchestrator wired to execute the proposal")
        # Serialize the claim→execute span (same lock deliberate() uses) and claim
        # the proposal atomically, so a double-clicked/retried/two-tab Approve runs
        # it exactly ONCE instead of spawning two sessions + double-booking budget.
        async with self._exec_lock:
            if not self._claim_for_execution(proposal_id):
                current = self.get_proposal(proposal_id)
                raise ValueError(
                    f"proposal is already {current.status if current else 'gone'}"
                )
            proposal = self.get_proposal(proposal_id)  # refresh (now 'executing')
            goal = self.get_goal(proposal.goal_id) if proposal.goal_id else None
            if goal is None:  # backlog proposal with no goal — synthesise a throwaway
                goal = GoalRecord(id=proposal.goal_id or "goal_adhoc", text=proposal.title)
            try:
                return await self._execute(proposal, goal, wait=wait)
            except PermissionError as exc:
                # A maintainer (self-mod) proposal when self_dev_enabled is off:
                # release the claim back to pending and tell the approver.
                self._reset_proposal_status(proposal_id, "pending")
                raise PermissionError(
                    f"cannot run this self-modifying proposal: {exc}. "
                    "Enable self_dev_enabled in Settings to let the Maintainer patch "
                    "Iron Jarvis's own source (still review-gated)."
                ) from exc
            except Exception:
                # Any other failure before _book durably marked it executed: release
                # the claim so the proposal is retryable, not stuck 'executing'.
                self._reset_proposal_status(proposal_id, "pending")
                raise

    def reject(self, proposal_id: str) -> ProposalRecord | None:
        with session_scope(self.p.engine) as db:
            rec = db.get(ProposalRecord, proposal_id)
            if rec is None:
                return None
            rec.status = "rejected"
            db.add(rec)
            db.commit()
            db.refresh(rec)
            return rec

    # -- goal sources: EventBus -> suggest-only backlog --------------------

    #: notable signals worth surfacing as a candidate (suggest-only) backlog item.
    _EVENT_BACKLOG: dict[str, tuple[str, str, str]] = {
        # event type -> (title, task, risk)
        EventType.PROVIDER_FAILED: (
            "A model provider just failed",
            "A model provider failed. Investigate why and report whether a "
            "connection needs attention; do not change credentials.",
            "low",
        ),
        EventType.REVIEW_REQUESTED: (
            "A change is awaiting your review",
            "Summarise the pending review so the user can decide quickly.",
            "low",
        ),
    }

    def on_event(self, event: Any) -> ProposalRecord | None:
        """EventBus handler: map a notable signal to a suggest-only backlog item.

        Guarded on ``autonomy_enabled`` so the DEFAULT install + the test suite
        see zero new rows. Cheap + non-spammy: it never executes, and dedupes
        against an existing pending backlog item with the same title.
        """
        if not getattr(self.p.config, "autonomy_enabled", False):
            return None
        etype = getattr(event, "type", None) or (
            event.get("type") if isinstance(event, dict) else None
        )
        spec = self._EVENT_BACKLOG.get(etype)
        if spec is None:
            return None
        title, task, risk = spec
        try:
            with session_scope(self.p.engine) as db:
                existing = db.exec(
                    select(ProposalRecord).where(
                        ProposalRecord.title == title,
                        ProposalRecord.status == "pending",
                        ProposalRecord.source == "event",
                    )
                ).first()
                if existing is not None:
                    return None  # already surfaced — don't spam
            return self._create_proposal(
                goal_id=None,
                title=title,
                rationale=f"Triggered by event {etype}.",
                agent_type="builder",
                task=task,
                risk=risk,
                source="event",
            )
        except Exception:  # noqa: BLE001 - a bad event must never break the bus
            log.exception("autonomy backlog mapping failed for %s", etype)
            return None

    # -- morning briefing --------------------------------------------------

    def briefing(self, notify: bool = False) -> dict:
        """Summarise recent self-activity + pending proposals (optionally pushed)."""
        cutoff = utcnow() - timedelta(hours=_BUDGET_WINDOW_HOURS)
        active = self.list_goals(status="active")
        pending = self.list_proposals(status="pending")
        with session_scope(self.p.engine) as db:
            recent = list(
                db.exec(
                    select(ProposalRecord).where(
                        ProposalRecord.status == "executed",
                        ProposalRecord.created_at >= cutoff,
                    )
                )
            )
        lines = [
            "Iron Jarvis — morning briefing",
            f"- Active goals: {len(active)}",
            f"- Actions I took in the last 24h: {len(recent)}",
            f"- Proposals awaiting your call: {len(pending)}",
        ]
        for p in pending[:5]:
            lines.append(f"  • [{p.risk}] {p.title}")
        text = "\n".join(lines)
        pushed = None
        if notify:
            try:
                pushed = self.p.notifier.notify(text)
            except Exception:  # noqa: BLE001
                log.exception("briefing notify failed")
        return {
            "text": text,
            "active_goals": len(active),
            "recent_actions": len(recent),
            "pending_proposals": len(pending),
            "pushed": pushed,
        }
