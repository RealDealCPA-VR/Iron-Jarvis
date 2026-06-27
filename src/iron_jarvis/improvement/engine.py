"""ImprovementEngine — close the loop so Iron Jarvis visibly gets smarter.

The Evaluator scores sessions; this engine is the missing CONSUMER of those
scores. It does four things, ordered from cheapest/always-on to gated:

1. :meth:`record_outcome` — runs on EVERY session completion (the orchestrator
   hook, right after scoring). Cheap, pure-DB, NEVER raises, never changes
   observable behaviour: it records an :class:`OutcomeRecord` and updates rolling
   per-lesson + per-agent stats.
2. lesson weighting — each lesson gets an effective weight = its static ``weight``
   plus a ``weight_bonus`` this engine maintains: lessons whose sessions score
   BELOW the global baseline decay, those that score ABOVE are rewarded. The
   learning layer orders ``recall_lessons`` / prompt injection by that sum, so a
   helpful lesson surfaces first and a harmful one sinks.
3. :meth:`reflect` — the model-driven step. ON-DEMAND / cadence-gated only (never
   auto-fires by default or in tests). It picks recent LOW-scoring sessions, makes
   ONE lightweight, injectable, mock-safe model call, and returns STRUCTURED
   suggestions (a sharper lesson, a prompt tweak, a missing tool). It applies
   NOTHING — humans decide.
4. :meth:`scan_tool_failures` — clusters recurring tool failures and, past a
   threshold, mints a SUGGEST-ONLY :class:`ProposalRecord` recommending a
   Maintainer fix. It NEVER spawns a session; the existing self-dev path (gated by
   ``config.self_dev_enabled`` + human approval) runs it only if the user approves.

Offline + safe by design: 1 + 2 are deterministic DB logic; 3 is mock-safe with a
heuristic fallback and applies nothing; 4 only writes a proposal row.
"""

from __future__ import annotations

import inspect
import json
from collections import Counter
from typing import Any, Awaitable, Callable

from sqlmodel import select

from ..core.db import session_scope
from ..core.ids import utcnow
from ..core.logging import get_logger
from ..core.models import Session, ToolInvocation
from ..learning.models import LessonRecord
from .models import AgentStatRecord, LessonStatRecord, OutcomeRecord

log = get_logger("improvement")

# A reflector may be injected for tests/offline determinism: it receives the
# gathered context dict and returns a list of suggestions (or None to fall back).
Reflector = Callable[[dict[str, Any]], "Any | None | Awaitable[Any]"]

#: How many active user-scope lessons we attribute a session's outcome to (mirrors
#: LearningEngine.apply_to_prompt's default injection window).
_LESSON_LIMIT = 8
#: Minimum samples before a lesson's outcome stats move its effective weight.
_MIN_SAMPLES = 2
#: Scale + cap for the score->weight adjustment (kept small + bounded).
_BONUS_SCALE = 4.0
_MAX_BONUS = 4.0
#: A session at or below this composite score counts as a "low scorer" worth
#: reflecting on, and a tool failure cluster is interesting past _FAIL_THRESHOLD.
_LOW_SCORE = 0.6
_FAIL_THRESHOLD = 3
#: Cap on the per-agent recent-scores window kept for the quality trend.
_RECENT_MAX = 20


def _clamp_bonus(value: float) -> float:
    return round(max(-_MAX_BONUS, min(_MAX_BONUS, value)), 2)


def _loads(text: str) -> list:
    try:
        data = json.loads(text or "[]")
        return data if isinstance(data, list) else []
    except (TypeError, ValueError):
        return []


class ImprovementEngine:
    """Turns measured outcomes into lesson weights, suggestions, and proposals."""

    def __init__(self, platform, *, reflector: Reflector | None = None) -> None:
        self.p = platform
        self.engine = platform.engine
        self._reflector = reflector

    # -- 1 + 2: per-session outcome attribution + lesson weighting ----------

    def record_outcome(
        self, session_id: str, *, lessons_applied: list[str] | None = None
    ) -> OutcomeRecord | None:
        """Record a session's outcome + update rolling stats. NEVER raises.

        Called on every session completion (cheap, pure-DB). ``lessons_applied``
        defaults to the lessons currently injected into prompts, i.e. the ones
        that were in effect for this run; callers may pass an explicit set.
        """
        try:
            return self._record_outcome(session_id, lessons_applied)
        except Exception:  # noqa: BLE001 - the hook must never break a session
            log.exception("record_outcome failed for session %s", session_id)
            return None

    def _record_outcome(
        self, session_id: str, lessons_applied: list[str] | None
    ) -> OutcomeRecord | None:
        # Idempotent: re-scoring the same session (a retry/resume) must not
        # double-count agent/lesson stats.
        with session_scope(self.engine) as db:
            if (
                db.exec(
                    select(OutcomeRecord).where(
                        OutcomeRecord.session_id == session_id
                    )
                ).first()
                is not None
            ):
                return None
        score, success, agent_type = self._score_session(session_id)
        tools = self._tools_used(session_id)
        if lessons_applied is None:
            lessons_applied = self._active_lesson_ids()
        lessons_applied = [str(x) for x in lessons_applied]

        now = utcnow()
        record = OutcomeRecord(
            session_id=session_id,
            agent_type=agent_type,
            score=score,
            success=success,
            lessons_applied=json.dumps(lessons_applied),
            tools_used=json.dumps(tools),
        )
        with session_scope(self.engine) as db:
            db.add(record)

            # Per-agent rolling stats.
            a = db.get(AgentStatRecord, agent_type) or AgentStatRecord(
                agent_type=agent_type
            )
            a.session_count += 1
            a.score_sum += score
            a.success_count += int(success)
            recent = _loads(a.recent_json)
            recent.append(round(score, 4))
            a.recent_json = json.dumps(recent[-_RECENT_MAX:])
            a.last_at = now
            db.add(a)

            # Per-lesson rolling stats.
            for lid in lessons_applied:
                s = db.get(LessonStatRecord, lid) or LessonStatRecord(lesson_id=lid)
                s.applied_count += 1
                s.score_sum += score
                s.success_count += int(success)
                s.last_applied_at = now
                db.add(s)
            db.commit()

            # Global baseline = mean session score across all agents (cheap: a
            # handful of agent rows), computed AFTER this outcome is folded in.
            agents = list(db.exec(select(AgentStatRecord)))
            tot_n = sum(x.session_count for x in agents)
            tot_s = sum(x.score_sum for x in agents)
            baseline = (tot_s / tot_n) if tot_n else 0.0

            # Re-derive each touched lesson's effective-weight bonus: reward those
            # whose sessions beat the baseline, decay those that trail it.
            for lid in lessons_applied:
                s = db.get(LessonStatRecord, lid)
                lr = db.get(LessonRecord, lid)
                if lr is None:
                    continue
                if s is not None and s.applied_count >= _MIN_SAMPLES:
                    avg = s.score_sum / s.applied_count
                    lr.weight_bonus = _clamp_bonus((avg - baseline) * _BONUS_SCALE)
                else:
                    lr.weight_bonus = 0.0
                db.add(lr)
            db.commit()
            db.refresh(record)
            return record

    def _score_session(self, session_id: str) -> tuple[float, bool, str]:
        """Composite quality in [0,1] from the Evaluation + any FeedbackRecord."""
        agent_type = "builder"
        status_value = ""
        with session_scope(self.engine) as db:
            sess = db.get(Session, session_id)
            if sess is None:
                # Unknown session: do NOT call evaluator.evaluate() (it would
                # synthesize a phantom Evaluation row); score it as a clean miss.
                return 0.0, False, agent_type
            agent_type = getattr(sess.agent_type, "value", str(sess.agent_type))
            status_value = getattr(sess.status, "value", str(sess.status))

        ev = None
        evaluator = getattr(self.p, "evaluator", None)
        if evaluator is not None:
            try:
                ev = evaluator.latest(session_id) or evaluator.evaluate(session_id)
            except Exception:  # noqa: BLE001
                ev = None

        if ev is not None:
            score = 0.6 * float(ev.completion) + 0.4 * float(ev.tool_success_rate)
            success = float(ev.completion) >= 1.0
        else:  # no eval row — degrade to the session's own status
            success = status_value == "completed"
            score = 1.0 if success else 0.0

        # Fold any explicit feedback (rare at completion time, honoured if present).
        learning = getattr(self.p, "learning", None)
        if learning is not None:
            try:
                fbs = learning.feedback_for(session_id)
            except Exception:  # noqa: BLE001
                fbs = []
            ratings = {f.rating for f in fbs}
            if "down" in ratings:
                score *= 0.4
            elif "up" in ratings:
                score = min(1.0, score + 0.1)

        return round(max(0.0, min(1.0, score)), 4), bool(success), agent_type

    def _tools_used(self, session_id: str) -> list[str]:
        with session_scope(self.engine) as db:
            rows = list(
                db.exec(
                    select(ToolInvocation).where(
                        ToolInvocation.session_id == session_id
                    )
                )
            )
        # Stable, de-duplicated order of tool names.
        seen: dict[str, None] = {}
        for t in rows:
            seen.setdefault(t.tool, None)
        return list(seen)

    def _active_lesson_ids(self) -> list[str]:
        learning = getattr(self.p, "learning", None)
        if learning is None:
            return []
        try:
            return [l.id for l in learning.lessons(scope="user", limit=_LESSON_LIMIT)]
        except Exception:  # noqa: BLE001
            return []

    # -- read side: stats + quality trend (GET /improvement) ----------------

    def stats(self) -> dict[str, Any]:
        """Per-lesson + per-agent stats and a quality trend (never raises)."""
        try:
            return self._stats()
        except Exception:  # noqa: BLE001
            log.exception("improvement stats read failed")
            return {"lessons": [], "agents": [], "outcomes": {"count": 0}}

    def _stats(self) -> dict[str, Any]:
        with session_scope(self.engine) as db:
            outcomes = list(db.exec(select(OutcomeRecord)))
            lesson_stats = {
                s.lesson_id: s for s in db.exec(select(LessonStatRecord))
            }
            lessons = list(db.exec(select(LessonRecord)))
            agents = list(db.exec(select(AgentStatRecord)))

        tot_n = sum(a.session_count for a in agents)
        tot_s = sum(a.score_sum for a in agents)
        baseline = round((tot_s / tot_n), 4) if tot_n else 0.0

        lesson_views = []
        for lr in lessons:
            s = lesson_stats.get(lr.id)
            n = s.applied_count if s else 0
            bonus = float(getattr(lr, "weight_bonus", 0.0) or 0.0)
            lesson_views.append(
                {
                    "lesson_id": lr.id,
                    "text": lr.text,
                    "source": lr.source,
                    "base_weight": lr.weight,
                    "weight_bonus": round(bonus, 2),
                    "effective_weight": round(lr.weight + bonus, 2),
                    "applied_count": n,
                    "avg_score": round(s.score_sum / n, 4) if n else None,
                    "success_rate": round(s.success_count / n, 4) if n else None,
                }
            )
        # Surface the most consequential lessons first.
        lesson_views.sort(key=lambda v: v["effective_weight"], reverse=True)

        agent_views = []
        for a in agents:
            n = a.session_count
            recent = _loads(a.recent_json)
            agent_views.append(
                {
                    "agent_type": a.agent_type,
                    "sessions": n,
                    "avg_score": round(a.score_sum / n, 4) if n else None,
                    "success_rate": round(a.success_count / n, 4) if n else None,
                    "trend": _trend(recent),
                    "recent_scores": recent,
                }
            )
        agent_views.sort(key=lambda v: v["agent_type"])

        scores = [o.score for o in outcomes]
        return {
            "lessons": lesson_views,
            "agents": agent_views,
            "outcomes": {
                "count": len(outcomes),
                "avg_score": round(sum(scores) / len(scores), 4) if scores else 0.0,
                "baseline": baseline,
            },
        }

    # -- 3: model reflection over low scorers (on-demand / cadence) ---------

    async def reflect(
        self,
        *,
        limit: int = 5,
        threshold: float = _LOW_SCORE,
        reflector: Reflector | None = None,
    ) -> dict[str, Any]:
        """Reflect over recent LOW-scoring sessions; return suggestions, apply NONE.

        Makes at most ONE lightweight model call (injected ``reflector`` first, the
        router otherwise). Deterministic + safe with the mock model via a heuristic
        fallback. This is ON-DEMAND only — it is never called automatically by the
        default install or the test suite.
        """
        low = self._recent_low_scorers(limit=limit, threshold=threshold)
        context = self._reflection_context(low, threshold)
        suggestions: list[dict] = []
        if low:
            raw = await self._call_reflector(context, reflector)
            suggestions = self._normalize_suggestions(raw) or self._heuristic_suggestions(low)
        return {
            "reviewed": len(low),
            "threshold": threshold,
            "suggestions": suggestions,
            "applied": False,  # this step NEVER edits a prompt / lesson / source
        }

    def _recent_low_scorers(self, *, limit: int, threshold: float) -> list[OutcomeRecord]:
        with session_scope(self.engine) as db:
            return list(
                db.exec(
                    select(OutcomeRecord)
                    .where(OutcomeRecord.score <= threshold)
                    .order_by(OutcomeRecord.created_at.desc())
                    .limit(max(1, int(limit)))
                )
            )

    def _reflection_context(
        self, low: list[OutcomeRecord], threshold: float
    ) -> dict[str, Any]:
        return {
            "threshold": threshold,
            "low_scorers": [
                {
                    "session_id": o.session_id,
                    "agent_type": o.agent_type,
                    "score": o.score,
                    "success": o.success,
                    "tools_used": _loads(o.tools_used),
                    "lessons_applied": _loads(o.lessons_applied),
                }
                for o in low
            ],
        }

    async def _call_reflector(
        self, context: dict, reflector: Reflector | None
    ) -> Any:
        chosen = reflector or self._reflector
        if chosen is not None:
            raw = chosen(context)
            if inspect.isawaitable(raw):
                raw = await raw
            return raw
        return await self._router_reflect(context)

    async def _router_reflect(self, context: dict) -> Any:
        """One lightweight model call asking for improvement suggestions (no tools)."""
        from ..agents.types import get_agent_definition
        from ..core.models import AgentType
        from ..providers.adapters.base import LLMMessage

        router = getattr(self.p, "router", None)
        if router is None:
            return None
        system = (
            get_agent_definition(AgentType.REVIEWER).system_prompt
            + "\n\nYou are Iron Jarvis's self-improvement reviewer. Given recent "
            "LOW-scoring sessions, propose a FEW specific, safe improvements. Reply "
            "ONLY with a compact JSON array; each item: "
            '{"kind": "lesson"|"prompt"|"tool", "target": str, "suggestion": str}. '
            "Do NOT apply anything — only suggest."
        )
        prompt = json.dumps(context, default=str)[:6000]
        try:
            route = await router.complete(
                system=system,
                messages=[LLMMessage(role="user", content=prompt)],
                tools=[],
            )
        except Exception:  # noqa: BLE001 - never let a model error break reflection
            log.exception("reflection model call failed; using heuristic fallback")
            return None
        return self._parse_suggestions(route.response.text)

    @staticmethod
    def _parse_suggestions(text: str) -> Any:
        """Extract the first JSON array/object from a chatty/fenced model reply."""
        text = (text or "").strip()
        if not text:
            return None
        for open_c, close_c in (("[", "]"), ("{", "}")):
            start, end = text.find(open_c), text.rfind(close_c)
            if start != -1 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except (TypeError, ValueError):
                    continue
        return None

    @staticmethod
    def _normalize_suggestions(raw: Any) -> list[dict]:
        """Coerce any model/injected reply into a clean list of suggestion dicts."""
        if isinstance(raw, dict):
            raw = raw.get("suggestions", [raw])
        if not isinstance(raw, list):
            return []
        out: list[dict] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "lesson").lower()
            if kind not in ("lesson", "prompt", "tool"):
                kind = "lesson"
            suggestion = str(item.get("suggestion") or "").strip()
            if not suggestion:
                continue
            out.append(
                {
                    "kind": kind,
                    "target": str(item.get("target") or "").strip(),
                    "suggestion": suggestion[:500],
                }
            )
        return out

    @staticmethod
    def _heuristic_suggestions(low: list[OutcomeRecord]) -> list[dict]:
        """Deterministic offline fallback when the model gives nothing usable."""
        suggestions: list[dict] = []
        for agent in sorted({o.agent_type for o in low}):
            suggestions.append(
                {
                    "kind": "lesson",
                    "target": agent,
                    "suggestion": (
                        f"Recent {agent} sessions scored low; capture a sharper "
                        "lesson about what went wrong so future runs avoid it."
                    ),
                }
            )
        tool_counts = Counter(t for o in low for t in _loads(o.tools_used))
        for tool, count in tool_counts.most_common(3):
            suggestions.append(
                {
                    "kind": "tool",
                    "target": tool,
                    "suggestion": (
                        f"Tool '{tool}' appears in {count} low-scoring session(s); "
                        "review its usage or guidance."
                    ),
                }
            )
        return suggestions

    # -- 4: recurring tool failures -> SUGGEST-ONLY proposal ----------------

    def scan_tool_failures(
        self, *, threshold: int = _FAIL_THRESHOLD, window: int = 500
    ) -> list[dict]:
        """Cluster recurring tool failures; mint a SUGGEST-ONLY proposal per cluster.

        NEVER spawns a session. Each proposal recommends a Maintainer fix and is
        HIGH risk (so even with autonomy on it can never auto-execute); spawning is
        gated by ``config.self_dev_enabled`` + human approval via the existing
        self-dev path. Returns one summary dict per cluster past ``threshold``.
        """
        try:
            return self._scan_tool_failures(threshold=threshold, window=window)
        except Exception:  # noqa: BLE001
            log.exception("scan_tool_failures failed")
            return []

    def _scan_tool_failures(self, *, threshold: int, window: int) -> list[dict]:
        with session_scope(self.engine) as db:
            rows = list(
                db.exec(
                    select(ToolInvocation)
                    .where(ToolInvocation.ok == False)  # noqa: E712 - SQL boolean
                    .order_by(ToolInvocation.created_at.desc())
                    .limit(max(1, int(window)))
                )
            )
        counts = Counter(r.tool for r in rows if r.tool)
        self_dev = bool(getattr(self.p.config, "self_dev_enabled", False))
        out: list[dict] = []
        for tool, count in counts.most_common():
            if count < threshold:
                break  # most_common is descending — nothing below clears the bar
            proposal_id = self._mint_failure_proposal(tool, count, self_dev)
            out.append(
                {
                    "tool": tool,
                    "failures": count,
                    "proposal_id": proposal_id,
                    "self_dev_enabled": self_dev,
                }
            )
        return out

    def _mint_failure_proposal(
        self, tool: str, count: int, self_dev: bool
    ) -> str | None:
        """Create (or reuse) a suggest-only Maintainer proposal. Never spawns."""
        intent = getattr(self.p, "intent", None)
        title = f"Recurring failures in tool '{tool}'"
        if intent is None:
            log.warning("tool-failure cluster (%s x%d) but no intent engine", tool, count)
            return None
        try:
            from ..motivation.models import ProposalRecord

            with session_scope(self.engine) as db:  # dedupe an open suggestion
                existing = db.exec(
                    select(ProposalRecord).where(
                        ProposalRecord.title == title,
                        ProposalRecord.status == "pending",
                        ProposalRecord.source == "event",
                    )
                ).first()
                if existing is not None:
                    return existing.id
            gate = (
                "Self-dev is enabled: on approval this can run the Maintainer on a "
                "worktree of Iron Jarvis's own source (review-gated, never auto-merge)."
                if self_dev
                else "Self-dev is OFF: enable self_dev_enabled before the Maintainer "
                "can patch Iron Jarvis's source; until then, investigate manually."
            )
            rec = intent._create_proposal(
                goal_id=None,
                title=title,
                rationale=(
                    f"Tool '{tool}' failed {count} times recently. Consider a "
                    f"focused fix to its implementation or guidance. {gate}"
                ),
                agent_type="maintainer",
                task=(
                    f"Investigate and fix the recurring failures of the '{tool}' tool "
                    "in Iron Jarvis. Keep the change small, match the surrounding "
                    "style, and leave the test suite green."
                ),
                risk="high",  # high risk => never auto-executes under any dial
                source="event",
            )
            return rec.id
        except Exception:  # noqa: BLE001 - a bad mint must never break the scan
            log.exception("failed to mint tool-failure proposal for %s", tool)
            return None

    # -- cadence helper (NOT auto-registered) -------------------------------

    def sweep(self) -> dict[str, Any]:
        """Cheap, deterministic maintenance pass suitable for a scheduled task.

        Runs the tool-failure scan (no model call). Intended to be wired to the
        Scheduler by a user who wants a cadence; it is NOT registered by default,
        so the default install + tests never fire it. The model-driven
        :meth:`reflect` stays on-demand (POST /improvement/reflect).
        """
        return {"tool_failure_proposals": self.scan_tool_failures()}


def _trend(recent: list[float]) -> float:
    """Late-window minus early-window mean score (>0 improving, <0 regressing)."""
    if len(recent) < 2:
        return 0.0
    mid = len(recent) // 2
    early = recent[:mid]
    late = recent[mid:]
    if not early or not late:
        return 0.0
    return round(sum(late) / len(late) - sum(early) / len(early), 4)
