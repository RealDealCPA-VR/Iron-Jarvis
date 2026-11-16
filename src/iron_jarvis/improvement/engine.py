"""ImprovementEngine — close the loop so Iron Jarvis visibly gets smarter.

The Evaluator scores sessions; this engine is the missing CONSUMER of those
scores. It does four things, ordered from cheapest/always-on to gated:

1. :meth:`record_outcome` — runs on EVERY session completion (the orchestrator
   hook, right after scoring). Cheap, pure-DB, NEVER raises, never changes
   observable behaviour: it records an :class:`OutcomeRecord` and updates rolling
   per-lesson + per-agent stats. Per-agent stats are keyed by the ROSTER NAME
   (v1.193.0) so a teammate the USER created earns a real track record instead
   of dissolving into its base type; :meth:`record_agent_outcome` is the
   session-less door for work that never opens a ``Session`` (remote asks).
2. lesson weighting — each lesson gets an effective weight = its static ``weight``
   plus a ``weight_bonus`` this engine maintains: lessons whose sessions score
   BELOW the global baseline decay, those that score ABOVE are rewarded. The
   learning layer orders ``recall_lessons`` / prompt injection by that sum, so a
   helpful lesson surfaces first and a harmful one sinks. v1.501.0: only the
   lessons a run was actually told are credited, and the owner's own
   preferences get no bonus (every kept one is told).
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
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

from sqlmodel import select

from ..core.db import session_scope
from ..core.ids import utcnow
from ..core.logging import get_logger
from ..core.models import Session, ToolInvocation
from ..learning.engine import STATED_SOURCES
from ..learning.models import LessonRecord
from .models import AgentStatRecord, LessonStatRecord, OutcomeRecord

log = get_logger("improvement")

# A reflector may be injected for tests/offline determinism: it receives the
# gathered context dict and returns a list of suggestions (or None to fall back).
Reflector = Callable[[dict[str, Any]], "Any | None | Awaitable[Any]"]

#: v1.501.0: a lesson of these sources is the owner's own (``learning.engine.
#: stated_clause``, v1.507.0 ``STATED_SOURCES``); no outcome bonus is written on it.
_STATED_SOURCES = STATED_SOURCES
#: Minimum samples before a lesson's outcome stats move its effective weight.
_MIN_SAMPLES = 2
#: Scale + cap for the score->weight adjustment (kept small + bounded).
_BONUS_SCALE = 4.0
_MAX_BONUS = 4.0
#: A session at or below this composite score counts as a "low scorer" worth
#: reflecting on, and a tool failure cluster is interesting past _FAIL_THRESHOLD.
_LOW_SCORE = 0.6
_FAIL_THRESHOLD = 3
#: v1.507.0 (north-star plan 7.2): the days of failures one scan reads — "a
#: tool that failed repeatedly in a week".
_FAIL_DAYS = 7
#: Ledger rows named in a failure suggestion's task, at most.
_FAIL_LINES = 8

#: v1.507.0 (north-star plan 7.3): the reflector's answer when no real model
#: answered (none connected, the demo model, or the call failed).
_NO_MODEL = object()
NO_MODEL_NOTE = "No model answered, so these suggestions come from the scores alone."
#: The ``status`` of a suggestion written from the scores alone: shown on
#: the page, never stored (``keep_suggestions``).
FROM_SCORES = "from_scores"
UNREADABLE_NOTE = "The model's answer could not be read, so these suggestions come from the scores alone."
#: Cap on the per-agent recent-scores window kept for the quality trend.
_RECENT_MAX = 20


def _clamp_bonus(value: float) -> float:
    return round(max(-_MAX_BONUS, min(_MAX_BONUS, value)), 2)


def _session_roster_name(sess) -> str:
    """The roster name a session row can prove, via the ONE shared predicate.

    ``agents.roster.session_roster_name`` is the single owner of that rule (the
    roster READS it, this engine WRITES it — two copies would drift). Imported
    lazily: ``agents`` pulls in the orchestrator, and improvement is imported
    from the platform build long before that graph is settled.
    """
    try:
        from ..agents.roster import session_roster_name

        return session_roster_name(sess)
    except Exception:  # noqa: BLE001 - no name provable is not an error
        return ""


def _ledger_roster_name(platform, session_id: str) -> str:
    """The roster name the DELEGATION LEDGER proves for this session, or "".

    THE ONLY THING THAT MAKES THE RE-KEY REAL. ``Session`` carries no field for
    who a run was handed to — only the builtin type it executed as — so without
    this read every ``custom:<slug>`` teammate is credited to its base type and
    the roster says "(no runs yet)" about it forever, which is the exact defect
    keying stats by name exists to remove. ``agents.roster`` owns the rule (see
    its "WHO RAN" section); this is the lazy call site.
    """
    try:
        from ..agents.roster import ledger_roster_name

        return ledger_roster_name(platform, session_id)
    except Exception:  # noqa: BLE001 - an unreadable ledger is not an error
        return ""


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
        # Serializes the rolling-stats read-modify-write so a scheduled-workflow
        # session finalizing on the APScheduler thread can't lose an increment
        # against a main-loop session finalizing at the same instant (WAL serializes
        # writers but NOT the read→+1→commit across two deferred transactions).
        import threading

        self._stats_lock = threading.Lock()

    # -- 1 + 2: per-session outcome attribution + lesson weighting ----------

    def record_outcome(
        self,
        session_id: str,
        *,
        lessons_applied: list[str] | None = None,
        agent_name: str | None = None,
    ) -> OutcomeRecord | None:
        """Record a session's outcome + update rolling stats. NEVER raises.

        Called on every session completion (cheap, pure-DB). ``lessons_applied``
        defaults to the lessons currently injected into prompts, i.e. the ones
        that were in effect for this run; callers may pass an explicit set.

        ``agent_name`` is the ROSTER NAME the run should be attributed to
        (``"builder"`` / ``"custom:tax-reader"`` / ``"remote:hermes"``) — pass
        the roster entry's own ``name`` verbatim. OMITTED IS THE NORMAL CASE —
        ``Orchestrator._post_run_learning`` passes only the session id — and it
        is then resolved from the session row plus the DELEGATION LEDGER
        (:func:`~iron_jarvis.agents.roster.ledger_roster_name`), which is what
        lets a ``custom:<slug>`` teammate earn a history at all. A session no
        handoff created (a user's own run) degrades to the bare builtin type,
        exactly the pre-v1.193.0 behaviour.

        v1.507.0 (north-star plan 7.4): called again for a session already
        scored (the run's page sends a rating after it ended), it RE-scores
        that run (:meth:`_rescore`); ``lessons_applied`` and ``agent_name``
        are then the ones recorded the first time.
        """
        try:
            return self._record_outcome(session_id, lessons_applied, agent_name)
        except Exception:  # noqa: BLE001 - the hook must never break a session
            log.exception("record_outcome failed for session %s", session_id)
            return None

    def _record_outcome(
        self,
        session_id: str,
        lessons_applied: list[str] | None,
        agent_name: str | None = None,
    ) -> OutcomeRecord | None:
        # Idempotent: re-scoring the same session (a retry/resume) must not
        # double-count agent/lesson stats. v1.507.0 (north-star plan 7.4): a
        # session already scored is RE-scored instead — a rating given after
        # the run ended moves its score, and the stats by the difference.
        with session_scope(self.engine) as db:
            scored = (
                db.exec(
                    select(OutcomeRecord.id).where(
                        OutcomeRecord.session_id == session_id
                    )
                ).first()
                is not None
            )
        if scored:
            # After the read's own session has closed (lead review): the
            # re-score opens its own, under the stats lock.
            return self._rescore(session_id)
        score, success, agent_type, session_name = self._score_session(session_id)
        # Attribution key: the caller's explicit roster name wins, else whatever
        # the session row itself can prove, else the bare builtin type.
        name = str(agent_name or "").strip() or session_name or agent_type
        tools = self._tools_used(session_id)
        if lessons_applied is None:
            # v1.501.0 (north-star plan 1.8): credit ONLY what this run was
            # told (``LearningEngine.apply_to_prompt`` remembers it per run),
            # never whatever ranks top now — that credited task notes no
            # prompt reads, and lessons added after the run started.
            lessons_applied = self._injected_lesson_ids(session_id)
        lessons_applied = [str(x) for x in lessons_applied]

        now = utcnow()
        record = OutcomeRecord(
            session_id=session_id,
            agent_type=agent_type,
            agent_name=name,
            score=score,
            success=success,
            lessons_applied=json.dumps(lessons_applied),
            tools_used=json.dumps(tools),
        )
        with self._stats_lock, session_scope(self.engine) as db:
            db.add(record)

            # Per-agent rolling stats, keyed by the ROSTER NAME (v1.193.0) —
            # identical to the type string for builtins, so their existing rows
            # keep accumulating and no migration is owed.
            self._bump_agent_stat(db, name, score, success, now)

            # Per-lesson rolling stats.
            for lid in lessons_applied:
                s = db.get(LessonStatRecord, lid) or LessonStatRecord(lesson_id=lid)
                s.applied_count += 1
                s.score_sum += score
                s.success_count += int(success)
                s.last_applied_at = now
                db.add(s)
            db.commit()
            self._rebonus(db, lessons_applied)
            db.commit()
            db.refresh(record)
            return record

    def rescore_if_scored(self, session_id: str) -> OutcomeRecord | None:
        """v1.507.0 (north-star plan 7.4): THE door for a rating given on a
        run's page — re-score the run when it was already scored; a run
        still going is left to its end hook (which reads the rating then).
        NEVER raises."""
        try:
            return self._rescore(session_id)
        except Exception:  # noqa: BLE001 - a rating is never worth a crash
            log.exception("rescore failed for session %s", session_id)
            return None

    @staticmethod
    def _rebonus(db, lesson_ids: list[str]) -> None:
        """Re-derive each touched lesson's effective-weight bonus (inside
        ``db``, after the stats were folded in): reward those whose sessions
        beat the baseline, decay those that trail it."""
        # Global baseline = mean session score across all agents (cheap: a
        # handful of agent rows), computed AFTER this outcome is folded in.
        agents = list(db.exec(select(AgentStatRecord)))
        tot_n = sum(x.session_count for x in agents)
        tot_s = sum(x.score_sum for x in agents)
        baseline = (tot_s / tot_n) if tot_n else 0.0
        for lid in lesson_ids:
            s = db.get(LessonStatRecord, lid)
            lr = db.get(LessonRecord, lid)
            if lr is None:
                continue
            if lr.source in _STATED_SOURCES:
                # v1.501.0 (plan 1.8): the owner's own preference is never
                # ranked by how runs scored; its stats are still kept.
                if lr.weight_bonus:
                    lr.weight_bonus = 0.0
                    db.add(lr)
                continue
            if s is not None and s.applied_count >= _MIN_SAMPLES:
                avg = s.score_sum / s.applied_count
                lr.weight_bonus = _clamp_bonus((avg - baseline) * _BONUS_SCALE)
            else:
                lr.weight_bonus = 0.0
            db.add(lr)

    def _rescore(self, session_id: str) -> OutcomeRecord | None:
        """v1.507.0 (north-star plan 7.4): score an already-scored run again
        — a rating given AFTER it ended. The outcome row takes the new score;
        its agent's and each lesson's sums (and success counts) move by the
        DIFFERENCE, never a second count; the agent's recent window has the
        old score replaced; the lessons' bonuses are re-derived. ``None`` when
        nothing changed (a retry, the same rating again)."""
        score, success, _agent_type, _name = self._score_session(session_id)
        with self._stats_lock, session_scope(self.engine) as db:
            record = db.exec(
                select(OutcomeRecord).where(OutcomeRecord.session_id == session_id)
            ).first()
            if record is None:
                return None
            old_score, old_ok = round(float(record.score), 4), bool(record.success)
            if old_score == score and old_ok == bool(success):
                return None
            d_score = score - old_score
            d_ok = int(bool(success)) - int(old_ok)
            record.score = score
            record.success = bool(success)
            db.add(record)
            a = db.get(AgentStatRecord, record.agent_name or record.agent_type)
            if a is not None:
                a.score_sum += d_score
                a.success_count += d_ok
                recent = _loads(a.recent_json)
                for i in range(len(recent) - 1, -1, -1):
                    if round(float(recent[i]), 4) == old_score:
                        recent[i] = round(score, 4)
                        break
                a.recent_json = json.dumps(recent[-_RECENT_MAX:])
                db.add(a)
            lessons = [str(x) for x in _loads(record.lessons_applied)]
            for lid in lessons:
                s = db.get(LessonStatRecord, lid)
                if s is not None:
                    s.score_sum += d_score
                    s.success_count += d_ok
                    db.add(s)
            db.commit()
            self._rebonus(db, lessons)
            db.commit()
            db.refresh(record)
            return record

    @staticmethod
    def _bump_agent_stat(db, name: str, score: float, success: bool, now) -> None:
        """Fold one run into a roster name's rolling record (inside ``db``)."""
        a = db.get(AgentStatRecord, name) or AgentStatRecord(agent_type=name)
        a.session_count += 1
        a.score_sum += score
        a.success_count += int(success)
        recent = _loads(a.recent_json)
        recent.append(round(score, 4))
        a.recent_json = json.dumps(recent[-_RECENT_MAX:])
        a.last_at = now
        db.add(a)

    def record_agent_outcome(
        self, name: str, *, success: bool, score: float | None = None
    ) -> bool:
        """Give a SESSION-LESS run a track record. NEVER raises.

        A remote agent's ask (``RemoteAgentRegistry.run``) creates no ``Session``
        and therefore no ``OutcomeRecord`` — there is nothing to evaluate — so
        ``record_outcome`` cannot reach it and ``remote:<name>`` would show "(no
        runs yet)" forever. This folds one finished run straight into the rolling
        per-name stats, scoring it 1.0/0.0 from ``success`` when no ``score`` is
        supplied — the SAME degradation ``_score_session`` already applies to a
        session with no evaluation row. It deliberately writes NO
        ``OutcomeRecord``: that table is the per-session attribution substrate,
        and a row with no session would break every reader that joins on one.
        """
        try:
            key = str(name or "").strip()
            if not key:
                return False
            value = float(score) if isinstance(score, (int, float)) else (
                1.0 if success else 0.0
            )
            value = round(max(0.0, min(1.0, value)), 4)
            with self._stats_lock, session_scope(self.engine) as db:
                self._bump_agent_stat(db, key, value, bool(success), utcnow())
                db.commit()
            return True
        except Exception:  # noqa: BLE001 - a track record is never worth a crash
            log.exception("record_agent_outcome failed for %s", name)
            return False

    def _score_session(self, session_id: str) -> tuple[float, bool, str, str]:
        """Composite quality in [0,1] from the Evaluation + any FeedbackRecord.

        Returns ``(score, success, agent_type, roster_name)`` — the last being
        WHO ran: the session row's own name if it carries one, else the name the
        delegation ledger proves this session was handed off as, else empty (the
        caller then falls back to ``agent_type``).
        """
        agent_type = "builder"
        roster_name = ""
        explicit_name = ""
        status_value = ""
        with session_scope(self.engine) as db:
            sess = db.get(Session, session_id)
            if sess is None:
                # Unknown session: do NOT call evaluator.evaluate() (it would
                # synthesize a phantom Evaluation row); score it as a clean miss.
                return 0.0, False, agent_type, roster_name
            agent_type = getattr(sess.agent_type, "value", str(sess.agent_type))
            roster_name = _session_roster_name(sess)
            explicit_name = str(getattr(sess, "agent_name", "") or "").strip()
            status_value = getattr(sess.status, "value", str(sess.status))

        # The ledger read runs OUTSIDE the scope above: ``sess`` is expired the
        # moment it closes, and nesting a second session_scope inside a live one
        # is a lock shape this hook has no reason to take. Skipped entirely when
        # the row named itself — an explicit name is the stronger claim.
        if not explicit_name:
            handed_to = _ledger_roster_name(self.p, session_id)
            if handed_to:
                roster_name = handed_to

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

        return round(max(0.0, min(1.0, score)), 4), bool(success), agent_type, roster_name

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

    def _injected_lesson_ids(self, session_id: str) -> list[str]:
        """The lessons ``apply_to_prompt`` told THIS run (v1.501.0); ``[]``
        when none were told (``skip_memory``) or the daemon restarted since."""
        learning = getattr(self.p, "learning", None)
        injected = getattr(learning, "injected_into", None)
        if injected is None:
            return []
        try:
            return list(injected(session_id))
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
            # v1.305.0: a proposed / declined preference is not a lesson yet.
            from ..learning.engine import confirmed_clause

            lessons = list(db.exec(select(LessonRecord).where(confirmed_clause())))
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
                    # Both keys carry the SAME roster-name string: "name" is what
                    # the roster joins on (v1.193.0), "agent_type" is the pinned
                    # wire key the dashboard already reads — and for a builtin
                    # the two are identical anyway.
                    "name": a.agent_type,
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
        made_by = "scores"
        note = ""
        if low:
            raw = await self._call_reflector(context, reflector)
            # v1.507.0 (north-star plan 7.3): say honestly where the
            # suggestions came from — a demo model's reply is never one.
            if raw is _NO_MODEL:
                raw, note = None, NO_MODEL_NOTE
            suggestions = self._normalize_suggestions(raw)
            if suggestions:
                made_by = "model"
            else:
                note = note or UNREADABLE_NOTE
                suggestions = self._heuristic_suggestions(low)
        out = {
            "reviewed": len(low),
            "threshold": threshold,
            "suggestions": suggestions,
            "applied": False,  # this step NEVER edits a prompt / lesson / source
            # v1.507.0: "model" or "scores" (the heuristic), and the runs read.
            "made_by": made_by,
            "sessions": [o.session_id for o in low],
        }
        if note:
            out["note"] = note
        return out

    def keep_suggestions(self, result: dict[str, Any]) -> dict[str, Any]:
        """v1.507.0 (north-star plan 7.3): store each of :meth:`reflect`'s
        suggestions as a suggestion that waits for the owner — through the
        SAME path as what went wrong in a chat (``preferences.mint_noticed``:
        status proposed, origin "noticed", the never-ask-twice rule and the
        open limit), ``source="reflected"``, the low-scoring runs it came from
        as evidence (each run's task, linked to the run). Each suggestion in
        the returned copy carries ``status`` (``suggested`` with its ``id``,
        or why not: ``already_waiting``, ``already_kept``,
        ``declined_before``, ``full``, ``not_kept``, ``from_scores``) and
        ``_minted`` lists the rows made. Only what a MODEL wrote from the
        runs is stored (``made_by == "model"``). Nothing reaches a prompt
        until kept. BLOCKING."""
        from ..learning.preferences import (
            MAX_TEXT,
            SOURCE_REFLECTED,
            PreferenceInvalid,
            clean_text,
            mint_noticed,
        )
        from ..learning.engine import without_secrets

        evidence = self._runs_evidence(list(result.get("sessions") or []))
        out = dict(result)
        made: list[Any] = []
        items: list[dict] = []
        # Lead review: the fallback written from the scores alone ("Recent
        # builder sessions scored low; capture a sharper lesson …") says
        # nothing a later run could act on. It is shown, never stored: kept,
        # it would be told in prompts as a lesson, and waiting it would take
        # one of the few open places from the owner's own corrections.
        from_model = str(result.get("made_by") or "") == "model"
        for s in result.get("suggestions") or []:
            item = dict(s)
            if not from_model:
                item["status"] = FROM_SCORES
                items.append(item)
                continue
            text = " ".join(without_secrets(str(s.get("suggestion") or "")).split())
            if len(text) > MAX_TEXT:
                text = text[: MAX_TEXT - 1].rstrip() + "…"
            try:
                text = clean_text(text)
            except PreferenceInvalid:
                item["status"] = "not_kept"
                items.append(item)
                continue
            if not evidence:
                item["status"] = "not_kept"
                items.append(item)
                continue
            row, outcome = mint_noticed(self.engine, text, evidence=evidence, source=SOURCE_REFLECTED, weight=2)
            item["status"] = outcome
            if row is not None:
                item["id"] = row.id
                made.append(row)
            items.append(item)
        out["suggestions"] = items
        out["stored"] = len(made)
        out["_minted"] = made
        return out

    def _runs_evidence(self, session_ids: list[str]) -> list[dict[str, Any]]:
        """One evidence entry per low-scoring run: what it was asked, when,
        and the link to the run (the link the client wall reads)."""
        from ..learning.preferences import MAX_EVIDENCE, QUOTE_CHARS, RUN_LINK, WHERE_RUN
        from ..learning.engine import without_secrets

        out: list[dict[str, Any]] = []
        with session_scope(self.engine) as db:
            for sid in session_ids[:MAX_EVIDENCE]:
                row = db.get(Session, sid)
                task = " ".join(without_secrets(str(getattr(row, "task", "") or "")).split()) or "an agent run"
                if len(task) > QUOTE_CHARS:
                    task = task[: QUOTE_CHARS - 1].rstrip() + "…"
                at = getattr(row, "created_at", None)
                out.append({
                    "quote": task,
                    "at": at.isoformat() if at is not None else "",
                    "where": WHERE_RUN,
                    "link": f"{RUN_LINK}{sid}",
                })
        return out

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
                    # WHO ran, not just what shape it was (v1.193.0).
                    "agent": getattr(o, "agent_name", "") or o.agent_type,
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
            return _NO_MODEL
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
            return _NO_MODEL
        if str(getattr(route, "provider", "") or "") == "mock":
            # v1.507.0 (north-star plan 7.3): the demo model's scripted reply
            # is never a suggestion (it would be stored now).
            return _NO_MODEL
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
        for agent in sorted(
            {(getattr(o, "agent_name", "") or o.agent_type) for o in low}
        ):
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
        self,
        *,
        threshold: int = _FAIL_THRESHOLD,
        window: int = 500,
        days: int = _FAIL_DAYS,
        now: datetime | None = None,
    ) -> list[dict]:
        """Cluster recurring tool failures; mint a SUGGEST-ONLY proposal per cluster.

        NEVER spawns a session. Each proposal recommends a Maintainer fix and is
        HIGH risk (so even with autonomy on it can never auto-execute); spawning is
        gated by ``config.self_dev_enabled`` + human approval via the existing
        self-dev path. Returns one summary dict per cluster past ``threshold``.

        v1.507.0 (north-star plan 7.2): run once a day by the daemon
        (``daemon/app._tool_failure_pass``). Only failures of the last
        ``days`` (``now`` is passed in by tests) count, and a call that was
        REFUSED is not a failure of the tool: a ``tool.denied`` call (the
        owner's No, a rule, an ask nobody could answer) and a call of a tool
        that does not exist. The suggestion carries the failures as evidence
        and is made once per week of failures (:meth:`_mint_failure_proposal`).
        """
        try:
            return self._scan_tool_failures(threshold=threshold, window=window, days=days, now=now)
        except Exception:  # noqa: BLE001
            log.exception("scan_tool_failures failed")
            return []

    def _scan_tool_failures(
        self, *, threshold: int, window: int, days: int, now: datetime | None
    ) -> list[dict]:
        from ..core.events import EventType
        from ..core.models import EventRecord, PermissionMode

        moment = now or utcnow()
        since = moment - timedelta(days=max(1, int(days)))
        with session_scope(self.engine) as db:
            rows = list(
                db.exec(
                    select(ToolInvocation)
                    .where(ToolInvocation.ok == False)  # noqa: E712 - SQL boolean
                    .where(ToolInvocation.created_at >= since)
                    .where(ToolInvocation.created_at <= moment)
                    .order_by(ToolInvocation.created_at.desc())
                    .limit(max(1, int(window)))
                )
            )
            refused: set[str] = set()
            denied = db.exec(
                select(EventRecord.payload_json)
                .where(EventRecord.type == EventType.TOOL_DENIED)
                .where(EventRecord.created_at >= since)
            ).all() if rows else []
            for payload in denied:
                try:
                    inv = json.loads(payload or "{}").get("invocation_id")
                except (TypeError, ValueError, AttributeError):
                    inv = None
                if inv:
                    refused.add(str(inv))
        failed: dict[str, list[ToolInvocation]] = {}
        for r in rows:
            if not r.tool or r.id in refused or r.verdict == PermissionMode.DENY:
                continue
            failed.setdefault(r.tool, []).append(r)
        self_dev = bool(getattr(self.p.config, "self_dev_enabled", False))
        out: list[dict] = []
        for tool, fails in sorted(failed.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            if len(fails) < threshold:
                continue
            proposal_id = self._mint_failure_proposal(tool, fails, self_dev, since=since)
            out.append(
                {
                    "tool": tool,
                    "failures": len(fails),
                    "proposal_id": proposal_id,
                    "self_dev_enabled": self_dev,
                }
            )
        return out

    def _mint_failure_proposal(
        self, tool: str, fails: list[ToolInvocation], self_dev: bool, *, since: datetime
    ) -> str | None:
        """Create (or reuse) a suggest-only Maintainer proposal. Never spawns.

        v1.507.0 (north-star plan 7.2): in the app's words (the tool by its own
        name, never its id), with the failures as evidence: how many, where
        (chats, coding sessions, agent runs) and when on screen; the ledger
        rows (never what the tool answered) in the task the maintainer is given. ONE
        per week of failures: one still waiting is reused, and one made (or
        answered) since the window's start is not made again — a "Reject" is
        not asked again for the same failures."""
        intent = getattr(self.p, "intent", None)
        name = _tool_name(tool)
        title = f"Find out why {name} keeps failing"
        count = len(fails)
        if intent is None:
            log.warning("tool-failure cluster (%s x%d) but no intent engine", tool, count)
            return None
        try:
            from ..motivation.models import ProposalRecord

            with session_scope(self.engine) as db:  # one per week of failures
                existing = db.exec(
                    select(ProposalRecord)
                    .where(ProposalRecord.title == title, ProposalRecord.source == "event")
                    .where(
                        (ProposalRecord.status == "pending") | (ProposalRecord.created_at >= since)
                    )
                    .order_by(ProposalRecord.created_at.desc())
                ).first()
                if existing is not None:
                    return existing.id
            rec = intent._create_proposal(
                goal_id=None,
                title=title,
                rationale=_failure_rationale(fails),
                agent_type="maintainer",
                task=(
                    f"Investigate and fix the recurring failures of the '{tool}' tool "
                    "in Iron Jarvis. Keep the change small, match the surrounding "
                    "style, and leave the test suite green.\n\n"
                    + _failure_lines(fails)
                ),
                risk="high",  # high risk => never auto-executes under any dial
                source="event",
            )
            return rec.id
        except Exception:  # noqa: BLE001 - a bad mint must never break the scan
            log.exception("failed to mint tool-failure proposal for %s", tool)
            return None

    # -- cadence helper (run once a day by the daemon) ----------------------

    def sweep(self) -> dict[str, Any]:
        """Cheap, deterministic maintenance pass (no model call): the
        tool-failure scan.

        v1.507.0 (north-star plan 7.2): run by the daemon's daily lesson
        tidy-up loop (``daemon/app._tool_failure_pass``, off the event loop,
        its health reported as ``tool_failures``). It used to be connected to
        nothing. The model-driven :meth:`reflect` stays on-demand
        (POST /improvement/reflect).
        """
        return {"tool_failure_proposals": self.scan_tool_failures()}


def _tool_name(tool: str) -> str:
    """A tool by its own name, as the chat footnote says it ("web fetch",
    "Gmail › search messages"), never its id. Never raises."""
    try:
        from ..agents.mission import tool_own_name

        return tool_own_name(tool) or "a tool"
    except Exception:  # noqa: BLE001 - wording only
        return " ".join(str(tool or "").replace("_", " ").split()) or "a tool"


def _where_word(session_id: str) -> str:
    sid = str(session_id or "")
    if sid == "chat":
        return "chat"
    if sid.startswith("build:"):
        return "coding session"
    return "agent run"


def _failure_rationale(fails: list[ToolInvocation]) -> str:
    """The suggestion's evidence on screen: how many failures, where, when."""
    n = len(fails)
    where = Counter(_where_word(f.session_id) for f in fails)
    parts = [f"{c} in {w}s" if c != 1 else f"1 in a {w}" for w, c in sorted(where.items(), key=lambda kv: -kv[1])]
    last = max((f.created_at for f in fails if f.created_at is not None), default=None)
    when = f", most recently on {last.strftime('%d %b')}" if last is not None else ""
    return (
        f"It failed {n} times in the last {_FAIL_DAYS} days ({', '.join(parts)}){when}. "
        "Approving asks the maintainer agent to find out why and suggest a fix to Iron Jarvis's "
        "own code, which you review before anything changes."
    )


def _failure_lines(fails: list[ToolInvocation]) -> str:
    """The failures for the maintainer: each ledger row, when and where.

    Lead review: what the tool ANSWERED is left out on purpose. This text
    is an agent's TASK, and a tool's output (a fetched page's error text,
    a file's first line) is data, never instructions — in a task it would
    be read as the owner's own words. The rows are named, so the output is
    read from Activity, where it is shown as what a tool answered."""
    lines = ["The failures (Activity rows, newest first):"]
    for f in fails[:_FAIL_LINES]:
        at = f.created_at.isoformat(timespec="minutes") if f.created_at is not None else ""
        lines.append(f"- {f.id} at {at} ({_where_word(f.session_id)})")
    if len(fails) > _FAIL_LINES:
        lines.append(f"- and {len(fails) - _FAIL_LINES} more")
    return "\n".join(lines)


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
