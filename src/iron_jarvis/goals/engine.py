"""GoalEngine — one honest iteration of a standing goal at a time (G1, v1.208.0).

An *iteration* is one ordinary agent session: same orchestrator, same
permission engine, same ledger, same review posture. What the engine adds is
bookkeeping with teeth, in this order and nowhere else:

1. **Hard budget gate BEFORE spawning.** Any bound already reached refuses the
   iteration with an honest ``goal.iteration_refused`` event — the goal stays
   ``active`` (a budget is not a failure), and nothing is spawned, so a
   refused iteration costs zero. Wallclock is the figure ACCUMULATED across
   iterations; the gate runs pre-spawn, so an iteration that starts under
   budget runs to completion even if it crosses mid-flight (stated, not
   hidden).
2. **One session per iteration** — ``origin="goal:<id>"``, the goal's project
   threaded (context spine), the goal's pre-granted tools attached (the deny
   floor was refused at write time and is re-checked here — one rule set, two
   call sites).
3. **Spend from recorded truth**: the session row's token counts and
   ``eval.pricing.cost_for`` — never the model's own claims.
4. **The verifier is a LADDER, and the doer never grades itself unlabeled**
   (G2). Tier 1 ``"checks"`` is the workflows verified-steps machinery, not a
   rewrite: each check is a workflows ``expect:`` shape evaluated by
   ``WorkflowEngine._expect_failure`` against the session's LEDGER evidence
   (files from ``agents/outcome.session_result``, summary from the recorded
   row) — satisfies only when EVERY check passes. Tier 2 ``"adversarial"``
   runs any checks first (identically), THEN a fresh-context one-shot judge
   briefed to REFUTE satisfaction (``_judge_satisfaction`` — through
   ``platform.router.complete(..., tools=[])``, the consult/decompose seam,
   on the session's OWN provider); satisfied only when the checks pass AND
   the judge fails to refute. Tier 3 ``"judged"`` is the judge alone, and
   ``goal_view`` labels such a satisfaction loudly. No real provider ⇒ the
   judge cannot run ⇒ the iteration records
   :data:`VERIFICATION_PENDING_NOTE` and does NOT satisfy (the honest-mock
   rule — never a fabricated verdict, never a silent demotion to
   checks-only). ``kind:"manual"`` NEVER auto-satisfies.
5. **Checkpoint is deterministic**: session id, ledger-recorded files, the
   session's recorded summary — composed by code, never model-written in G1.
6. **The breaker trips honestly**: 3 failed iterations inside 30 minutes →
   ``state="tripped"`` + a ``goal.tripped`` event naming the reason (the
   renderer-watchdog pattern) — and a tripped goal refuses to iterate until
   explicitly reopened.

NOTHING HERE RUNS ON ITS OWN TIMER. Cadence comes from OUTSIDE: the scheduler
(a ``kind="goal"`` schedule dispatching :meth:`GoalEngine.run_iteration`) or a
manual "run now" route. This module never spawns a loop, never sleeps, never
re-arms itself — a goal that fires when nobody asked it to would be the exact
trust breach "suggest-don't-act" exists to prevent.

Restart survival: :meth:`rehydrate` runs at boot from the daemon lifespan's
``_rehydrate_step`` wiring, AFTER ``orchestrator.reconcile_interrupted_sessions``
(the session layer marks stranded sessions FAILED; this reconciles the goals
that were mid-iteration on top of that truth).

Events (string types, payload conventions per ``core/events.py`` neighbors,
session_id-tagged where a session exists): ``goal.created`` /
``goal.iteration_started`` / ``goal.iteration_completed`` /
``goal.iteration_refused`` / ``goal.satisfied`` / ``goal.tripped`` /
``goal.stopped``.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from ..core.logging import get_logger
from ..core.models import AgentType, Session, SessionStatus
from ..core.db import session_scope
from .models import GoalContractRecord, budget_exceeded, grants_violation
from .store import GoalStore

log = get_logger("goals.engine")

# Canonical goal event names. Plain strings on purpose — the bus takes any
# string; the coordinator may hoist these onto core.events.EventType (a shared
# file this module does not touch).
GOAL_CREATED = "goal.created"
GOAL_ITERATION_STARTED = "goal.iteration_started"
GOAL_ITERATION_COMPLETED = "goal.iteration_completed"
GOAL_ITERATION_REFUSED = "goal.iteration_refused"
GOAL_SATISFIED = "goal.satisfied"
GOAL_TRIPPED = "goal.tripped"
GOAL_STOPPED = "goal.stopped"

#: The standing instruction appended to every iteration task, after the
#: contract and the checkpoint block.
_CONTINUE_INSTRUCTION = (
    "Continue toward the goal; report precisely what remains."
)

#: Bounds on the deterministic checkpoint (it feeds the NEXT iteration's
#: prompt, so it is budgeted like any other injected context).
_CHECKPOINT_MAX_FILES = 50
_CHECKPOINT_MAX_REMAINING = 2000

#: The breaker reason for a goal whose iteration session died with the daemon.
_INTERRUPTED_REASON = "interrupted by a daemon restart"

#: HONEST-MOCK REFUSAL (G2). Recorded on the iteration when the adversarial/
#: judged tier's judge cannot run — no real provider, a route that resolved to
#: mock, or the call itself failing. The goal is NOT satisfied and NOT failed:
#: the work happened, the verdict is simply still owed. Never a fabricated
#: verdict (mock text is scripted), never a silent fallthrough to checks-only
#: (that would quietly demote the tier the user chose).
VERIFICATION_PENDING_NOTE = "verification pending — no model available to judge"

#: Wall-clock ceiling on one judge call (the consult tool's own bound).
_JUDGE_TIMEOUT_S = 120.0

#: The refute framing. The judge is briefed to ATTACK the satisfaction claim
#: from fresh context — the doer never grades itself unlabeled — and to
#: default to REFUTED when uncertain, so ambiguity fails closed.
_JUDGE_SYSTEM = (
    "You are an adversarial verifier with fresh context and no stake in the "
    "outcome. Your only job is to try to REFUTE the claim that a goal is "
    "satisfied. Only ledger-proven evidence counts: the files the session "
    "verifiably created or changed, and its recorded summary — prose claims "
    "without evidence count against satisfaction. Default to REFUTED when "
    "uncertain. Reply with exactly one line first — 'VERDICT: SATISFIED' or "
    "'VERDICT: REFUTED' — followed by a short reason."
)


def _goal_origin(goal_id: str) -> str:
    """The Session origin stamp for a goal iteration. Goal ids are
    ``goal_<hex12>`` (``new_id``), already inside the origin charset
    (``daemon/schemas._ORIGIN_RE``) and well under its 64-char cap."""
    return f"goal:{goal_id}"[:64]


def _after_marker(text: str, index: int) -> str:
    """The judge's reason after its VERDICT marker: separators stripped,
    bounded, first-line-ish. ``""`` when the verdict stood alone."""
    tail = text[index:].strip().lstrip("—–-:. ").strip()
    return tail[:300]


def _agent_type(name: str) -> AgentType:
    """Map the goal's ``agent_type`` string to an AgentType, default builder
    (the workflows convention — an unknown shape degrades, never raises)."""
    try:
        return AgentType(str(name or "builder"))
    except ValueError:
        return AgentType.BUILDER


class GoalEngine:
    """Runs goal iterations via the orchestrator. See the module docstring.

    ``orchestrator`` is the SHARED daemon orchestrator when the daemon wires
    this (cancel/observability reach the live session through it); bare
    callers/tests may pass none and get a lazily-built private one.
    """

    def __init__(self, platform, orchestrator=None) -> None:
        self.p = platform
        self._orch = orchestrator
        # The store carries the scheduler handle because EVERY state door runs
        # through the store (the resume route calls transition/reopen directly,
        # never this engine), and the schedule row must track the state no
        # matter which door was used (D2). A LAZY resolver, not the instance:
        # build_platform constructs this engine two lines BEFORE it attaches
        # platform.scheduler, so an __init__-time getattr binds None forever.
        self.store = GoalStore(
            platform.engine, scheduler=lambda: getattr(platform, "scheduler", None)
        )
        #: Goal ids with an iteration IN FLIGHT in this process — a scheduler
        #: double-fire or an overlapping "run now" is refused honestly instead
        #: of running two sessions against one budget row (the single-writer
        #: premise ``add_spend`` rests on). In-memory on purpose: a restart
        #: releases it, and :meth:`rehydrate` owns the durable half.
        self._iterating: set[str] = set()
        #: Strong refs to fire-and-forget event tasks published from sync
        #: contexts (rehydrate), until they settle — the orchestrator pattern.
        self._event_tasks: set[asyncio.Task] = set()

    # -- doors (the route/scheduler build against these) ----------------------

    @property
    def orch(self):
        if self._orch is None:
            from ..agents.orchestrator import Orchestrator  # lazy — heavy import

            self._orch = Orchestrator(self.p)
        return self._orch

    async def create_goal(self, **kwargs: Any) -> GoalContractRecord:
        """Validate + persist a goal and announce it (``goal.created``).

        Same keyword surface as :meth:`GoalStore.create`; raises ``ValueError``
        with the honest reason (deny-floor grant, unbounded budget, empty
        checks verifier, blank contract, rejected cron) for the route to map
        to a 400. A non-empty ``schedule`` creates the REAL scheduler row
        (D2 — the store owns that lifecycle; a cron the scheduler rejects
        refuses the whole create rather than selling a cadence that never
        fires).
        """
        record = self.store.create(**kwargs)
        await self.p.event_bus.publish(
            GOAL_CREATED,
            {
                "goal_id": record.id,
                "name": record.name,
                "state": record.state,
                "schedule": record.schedule,
                "verifier_kind": record.decoded_verifier()["kind"],
            },
        )
        return record

    async def stop_goal(self, goal_id: str) -> GoalContractRecord:
        """User-initiated terminal stop (valid from every non-terminal state).

        STOP ALWAYS WORKS (D5): if an iteration is IN FLIGHT (the checkpoint's
        ``running_session_id``), its session is CANCELLED through the
        orchestrator — merely preventing future runs while the current one
        keeps calling the model would make "Stop" a half-truth. The transition
        lands FIRST, so the iteration's cancel handler reads ``stopped`` and
        records the honest cancelled result; cancelled-by-stop is NOT a
        breaker failure (the user's decision is not evidence the world broke).
        The store's transition also disabled any schedule row (D2).
        """
        before = self.store.get(goal_id)
        running_sid = ""
        if before is not None:
            running_sid = str(
                before.decoded_checkpoint().get("running_session_id") or ""
            )
        record = self.store.transition(goal_id, "stopped")
        if running_sid:
            try:
                self.orch.cancel_session(running_sid)
            except (KeyError, ValueError):
                pass  # already finished / unknown — nothing left to stop
            except Exception:  # noqa: BLE001 — the stop itself already landed
                log.exception(
                    "could not cancel goal %s's running session %s",
                    goal_id,
                    running_sid,
                )
        await self.p.event_bus.publish(GOAL_STOPPED, {"goal_id": goal_id})
        return record

    async def run_iteration(self, goal_id: str) -> dict[str, Any]:
        """ONE iteration: gate → spawn → account → verify → checkpoint.

        Returns an honest result dict ``{ok, goal_id, state, reason?/status?,
        session_id?, iteration?, spent?, satisfied?, unmet?, note?}`` —
        refusals are ``ok=False`` WITH a ``goal.iteration_refused`` event,
        never silence. A crash ESCAPING the session layer once a session
        exists is caught too (D6): breaker failure with the real reason,
        marker cleared, wallclock/iteration accounted, and the honest failure
        dict returned — nothing re-raised, because a provider outage that
        records nothing could never trip the breaker it exists to trip.
        """
        goal = self.store.get(goal_id)
        if goal is None:
            return {"ok": False, "goal_id": goal_id, "reason": f"unknown goal '{goal_id}'"}

        if goal.state != "active":
            reason = f"goal is {goal.state}"
            if goal.state == "tripped":
                breaker = goal.decoded_breaker()
                why = str(breaker.get("last_reason") or "").strip()
                reason = (
                    "goal is tripped (circuit breaker"
                    + (f": {why}" if why else "")
                    + ") — reopen it explicitly to continue"
                )
            return await self._refuse(goal, reason)

        # Deny-floor re-check at spawn time (one rule set, two call sites —
        # the capability-store pattern): a row from an older build or a hand
        # edit must not arm a floor tool headlessly. Refuse, never strip —
        # silently running with fewer tools than the record claims would make
        # every failure that follows unexplainable.
        grants = goal.decoded_grants()
        floor_problem = grants_violation(grants)
        if floor_problem:
            return await self._refuse(goal, f"allowed_grants are not runnable: {floor_problem}")

        # HARD budget gate, BEFORE anything spawns. The state stays active —
        # an exhausted budget is a boundary, not a failure.
        spent = goal.decoded_spent()
        exhausted = budget_exceeded(goal.decoded_budget(), spent)
        if exhausted:
            return await self._refuse(goal, exhausted)

        if goal.id in self._iterating:
            return await self._refuse(
                goal, "an iteration is already running for this goal"
            )
        self._iterating.add(goal.id)
        try:
            return await self._iterate(goal, iteration=int(spent["iterations"]) + 1)
        finally:
            self._iterating.discard(goal.id)

    # -- the iteration body ----------------------------------------------------

    async def _iterate(self, goal: GoalContractRecord, iteration: int) -> dict[str, Any]:
        started = time.monotonic()
        task_text = self._compose_task(goal)
        session = await self.orch.create_session(
            task_text,
            _agent_type(goal.agent_type),
            project_id=goal.project_id,  # context spine, threaded
            allow_tools=goal.decoded_grants(),
            origin=_goal_origin(goal.id),
        )
        await self.p.event_bus.publish(
            GOAL_ITERATION_STARTED,
            {"goal_id": goal.id, "iteration": iteration, "agent": goal.agent_type},
            session_id=session.id,
        )
        # Durable mid-iteration marker: rehydrate() reconciles this honestly
        # if the daemon dies while the session runs.
        checkpoint = goal.decoded_checkpoint()
        checkpoint["running_session_id"] = session.id
        self.store.set_checkpoint(goal.id, checkpoint)

        try:
            session = await self._run_session(session)
        except asyncio.CancelledError:
            # The user stopped the session mid-iteration. The time was truly
            # spent and any tokens the run recorded before the cancel are on
            # the session ROW (D8) — both are accounted; it is NOT a breaker
            # failure — a cancel is the user's decision, not evidence the
            # world is broken.
            elapsed = time.monotonic() - started
            row = self._session_row(session.id) or session
            self._settle_spend(goal, row, elapsed)
            checkpoint.pop("running_session_id", None)
            checkpoint["last_session_id"] = session.id
            self.store.set_checkpoint(goal.id, checkpoint)
            await self.p.event_bus.publish(
                GOAL_ITERATION_COMPLETED,
                {
                    "goal_id": goal.id,
                    "iteration": iteration,
                    "ok": False,
                    "status": "cancelled",
                },
                session_id=session.id,
            )
            current = self.store.get(goal.id)
            return {
                "ok": False,
                "goal_id": goal.id,
                "session_id": session.id,
                "iteration": iteration,
                "status": "cancelled",
                "state": current.state if current else "active",
            }
        except Exception as exc:  # noqa: BLE001 — D6: an escaping crash (a
            # provider blow-up, a DB error inside run_session) must leave the
            # goal's books TRUE, not just propagate: without this branch the
            # breaker recorded nothing (a provider outage could never trip),
            # the running marker stayed stranded (the NEXT restart would log a
            # false "interrupted by a daemon restart" days later), and the
            # wallclock/iteration spend was lost. The session layer already
            # finalized the session FAILED (run_session's own handler) — this
            # is the goal-side half, and it returns the honest failure dict
            # rather than re-raising, exactly like a failed-but-completed run.
            elapsed = time.monotonic() - started
            reason = f"session {session.id} raised {type(exc).__name__}: {exc}"[:400]
            row = self._session_row(session.id) or session
            self._settle_spend(goal, row, elapsed)
            checkpoint.pop("running_session_id", None)
            checkpoint["last_session_id"] = session.id
            self.store.set_checkpoint(goal.id, checkpoint)
            record, tripped = self.store.record_failure(goal.id, reason)
            if tripped and record is not None:
                await self.p.event_bus.publish(
                    GOAL_TRIPPED,
                    {
                        "goal_id": goal.id,
                        # The phone lines read the NAME — a Telegram ping
                        # saying "goal_3f9a1c tripped" is a lookup chore, not
                        # a notification (the digest agent's finding).
                        "name": goal.name,
                        "reason": reason,
                        "failures": len(record.decoded_breaker().get("failures", [])),
                    },
                    session_id=session.id,
                )
            await self.p.event_bus.publish(
                GOAL_ITERATION_COMPLETED,
                {
                    "goal_id": goal.id,
                    "iteration": iteration,
                    "ok": False,
                    "status": "failed",
                    "error": reason,
                },
                session_id=session.id,
            )
            current = self.store.get(goal.id)
            return {
                "ok": False,
                "goal_id": goal.id,
                "session_id": session.id,
                "iteration": iteration,
                "status": "failed",
                "reason": reason,
                "state": current.state if current else ("tripped" if tripped else "active"),
            }
        elapsed = time.monotonic() - started

        # Spend from RECORDED truth: the session row's token counts + the
        # pricing table. Never the transcript's own claims. The accounted
        # session id lands in the same write (D7 — see GoalStore.add_spend).
        self._settle_spend(goal, session, elapsed)

        completed = session.status is SessionStatus.COMPLETED
        self.store.set_checkpoint(
            goal.id, self._compose_checkpoint(goal, session, iteration)
        )

        # D3 — RE-READ the state before any transition: the user may have
        # stopped/paused the goal WHILE the session ran (stop_goal even
        # cancels, but a fast run can complete first), and "stopped" has no
        # outgoing satisfied edge — transitioning blind would crash AFTER the
        # work happened. The result is recorded either way; the state is not
        # touched, and the note says so.
        live = self.store.get(goal.id)
        live_state = live.state if live else "active"
        note = (
            ""
            if live_state == "active"
            else f"goal was {live_state} during the run — result recorded, state unchanged"
        )

        satisfied = False
        unmet = ""
        pending = ""
        tripped = False
        if completed:
            verifier = goal.decoded_verifier()
            kind = verifier["kind"]
            may_satisfy = False
            if kind == "checks":
                # Tier 1 (G1) — deterministic checks, byte-identical since.
                unmet = self._verify_checks(verifier["checks"], session) or ""
                may_satisfy = not unmet
            elif kind in ("adversarial", "judged"):
                # Tier 2/3 (G2). Adversarial: optional checks FIRST (all must
                # pass, exactly as tier 1 — and a failed check saves the judge
                # call), THEN the refute-framed judge. Judged: the judge alone.
                if kind == "adversarial" and verifier["checks"]:
                    unmet = self._verify_checks(verifier["checks"], session) or ""
                if not unmet:
                    verdict, detail = await self._judge_satisfaction(goal, session)
                    if verdict == "satisfied":
                        may_satisfy = True
                    elif verdict == "pending":
                        pending = detail or VERIFICATION_PENDING_NOTE
                    else:
                        unmet = (
                            f"judge refuted satisfaction: {detail}"
                            if detail
                            else "judge refuted satisfaction"
                        )
            # kind "manual": NEVER auto-satisfies — only the user's explicit
            # decision (the route) moves it. The iteration result still says
            # what happened.
            if may_satisfy and live_state == "active":
                self.store.transition(goal.id, "satisfied")
                satisfied = True
                await self.p.event_bus.publish(
                    GOAL_SATISFIED,
                    # "name" rides along for the notifier's phone lines —
                    # payload.name first, goal_id as the honest fallback.
                    {"goal_id": goal.id, "name": goal.name, "iteration": iteration},
                    session_id=session.id,
                )
        else:
            reason = f"session {session.id} failed: {(session.summary or '')[:200]}"
            record, tripped = self.store.record_failure(goal.id, reason)
            if tripped and record is not None:
                await self.p.event_bus.publish(
                    GOAL_TRIPPED,
                    {
                        "goal_id": goal.id,
                        "name": goal.name,  # the notifier's phone lines
                        "reason": reason,
                        "failures": len(record.decoded_breaker().get("failures", [])),
                    },
                    session_id=session.id,
                )

        await self.p.event_bus.publish(
            GOAL_ITERATION_COMPLETED,
            {
                "goal_id": goal.id,
                "iteration": iteration,
                "ok": completed,
                "status": "completed" if completed else "failed",
                "satisfied": satisfied,
                **({"unmet": unmet[:400]} if unmet else {}),
                **({"pending": pending[:400]} if pending else {}),
                **({"note": note} if note else {}),
            },
            session_id=session.id,
        )
        current = self.store.get(goal.id)
        return {
            "ok": completed,
            "goal_id": goal.id,
            "session_id": session.id,
            "iteration": iteration,
            "status": "completed" if completed else "failed",
            "state": current.state if current else ("tripped" if tripped else "active"),
            "satisfied": satisfied,
            **({"unmet": unmet} if unmet else {}),
            **({"pending": pending} if pending else {}),
            **({"note": note} if note else {}),
            "spent": current.decoded_spent() if current else {},
        }

    def _settle_spend(self, goal: GoalContractRecord, session: Session, elapsed: float) -> None:
        """Bill one iteration from the session ROW's recorded usage — shared by
        the completed, failed, crashed and cancelled endings so no ending can
        lose spend. ``session_id`` rides in the same write (D7 idempotence)."""
        from ..eval import pricing

        in_tok = int(getattr(session, "input_tokens", 0) or 0)
        out_tok = int(getattr(session, "output_tokens", 0) or 0)
        self.store.add_spend(
            goal.id,
            tokens=in_tok + out_tok,
            dollars=pricing.cost_for(
                session.provider or "", session.model or "", in_tok, out_tok
            ),
            wallclock_s=elapsed,
            iterations=1,
            session_id=session.id,
        )

    async def _run_session(self, session: Session) -> Session:
        """Await ONE created session end to end (split out so tests can
        substitute the run). Registered with the orchestrator so a cancel can
        find it — the workflows ``_run_agent_step`` pattern."""
        task = asyncio.ensure_future(self.orch.run_session(session.id))
        self.orch.register_running(session.id, task)
        return await task

    async def _refuse(self, goal: GoalContractRecord, reason: str) -> dict[str, Any]:
        """The honest no: an event naming why, state untouched, nothing spawned."""
        await self.p.event_bus.publish(
            GOAL_ITERATION_REFUSED,
            # "name" for the notifier's phone lines (payload.name preferred,
            # goal_id the honest fallback).
            {"goal_id": goal.id, "name": goal.name, "reason": reason},
        )
        return {
            "ok": False,
            "refused": True,
            "goal_id": goal.id,
            "state": goal.state,
            "reason": reason,
        }

    # -- prompt + checkpoint composition (deterministic, bounded) ---------------

    def _compose_task(self, goal: GoalContractRecord) -> str:
        parts = [goal.contract_text.strip()]
        checkpoint = goal.decoded_checkpoint()
        last_sid = str(checkpoint.get("last_session_id") or "")
        if last_sid:
            lines = [
                "## Progress so far (deterministic checkpoint)",
                f"- previous session: {last_sid} (iteration "
                f"{int(checkpoint.get('iteration') or 0)})",
            ]
            files = checkpoint.get("files")
            if isinstance(files, list) and files:
                shown = [str(f) for f in files[:_CHECKPOINT_MAX_FILES]]
                lines.append("- files recorded by the ledger: " + ", ".join(shown))
            remaining = str(checkpoint.get("remaining") or "").strip()
            if remaining:
                lines.append("- previous report: " + remaining)
            parts.append("\n".join(lines))
        parts.append(_CONTINUE_INSTRUCTION)
        return "\n\n".join(parts)

    def _compose_checkpoint(
        self, goal: GoalContractRecord, session: Session, iteration: int
    ) -> dict[str, Any]:
        """The carry-forward summary, from RECORDED truth only: the session id,
        the ledger's created/changed files (``agents/outcome.session_result``,
        derived from ToolInvocation + UndoJournal), and the session's recorded
        summary. Composed by code — never model-written in G1 (the honest-mock
        rule: no model is asked to summarize, so no model can fabricate)."""
        files: list[str] = []
        try:
            from ..agents import outcome as _outcome

            res = _outcome.session_result(self.p.engine, session.id)
            files = (
                list(res.get("files_created") or []) + list(res.get("files_changed") or [])
            )[:_CHECKPOINT_MAX_FILES]
        except Exception:  # noqa: BLE001 — a checkpoint must not fail the iteration
            log.exception("checkpoint file harvest failed for %s", session.id)
        return {
            "last_session_id": session.id,
            "iteration": iteration,
            "files": [str(f) for f in files],
            "remaining": (session.summary or "")[:_CHECKPOINT_MAX_REMAINING],
            "at": (session.finished_at or session.created_at).isoformat()
            if (session.finished_at or session.created_at)
            else "",
        }

    # -- the verifier (REUSES the workflows verified-steps machinery) -----------

    def _verify_checks(self, checks: list, session: Session) -> str | None:
        """Evaluate every check via ``WorkflowEngine._expect_failure`` — THE
        workflows implementation, not a re-derivation: files match the
        session's ledger-recorded created/changed lists and
        ``summary_contains`` matches the recorded summary, with the exact
        normalization and failure wording workflows users already know.

        Returns the FIRST unmet check's honest detail, or ``None`` when every
        check holds. A check that coerces to nothing cannot pass vacuously —
        write-time validation refuses it, and a hand-edited row gets the same
        refusal here.
        """
        from ..workflows.engine import Step, WorkflowEngine, _coerce_expect

        weng = WorkflowEngine(self.p, self._orch)
        out = {
            "session_id": session.id,
            "status": "completed",
            "summary": session.summary or "",
            "kind": "agent",
        }
        for i, raw in enumerate(checks):
            expect = _coerce_expect(raw)
            if not expect:
                return (
                    f"check {i + 1} has no usable expectation, so it cannot be "
                    "verified (and an unverifiable check must not pass)"
                )
            step = Step(name=f"goal-check-{i + 1}", kind="agent", expect=expect)
            problem = weng._expect_failure(step, out)
            if problem:
                return problem
        return None

    # -- the adversarial judge (tiers 2/3, G2) -----------------------------------

    async def _judge_satisfaction(
        self, goal: GoalContractRecord, session: Session
    ) -> tuple[str, str]:
        """One fresh-context, refute-framed judge call. Returns
        ``(verdict, detail)`` with verdict ``"satisfied" | "refuted" |
        "pending"`` — never raises (a crashed judge is a PENDING verdict, not a
        failed iteration; the session's work already happened).

        THE SEAM, and why: ``platform.router.complete(..., tools=[])`` — the
        agents-layer one-shot door that ``consult`` and ``agents/decompose``
        already use, carrying the router's transient retry, failover, and the
        v1.162.0 rule that an unreachable REAL provider raises rather than
        returning mock prose. The daemon's ``_one_shot_complete`` is a closure
        inside ``daemon/app.py``'s factory — a platform-held engine cannot
        reach it, and ``consult_tool``'s docstring records that the router
        door is the one a non-daemon component can actually use.

        PROVIDER: the SESSION's own (explicit), never a different one — this
        goal's content may be client data, and judging it must not move it to
        another provider as a side effect (the never-auto-switch rule).

        HONEST-MOCK: a route that lands on the mock provider means the judge
        CANNOT run — mock text is scripted, and a scripted verdict is a
        fabricated one — so the verdict is ``pending`` with
        :data:`VERIFICATION_PENDING_NOTE`. The same for a raised/timed-out
        call. An UNREADABLE reply from a real model is ``refuted`` (fail
        closed, per the judge's own default-to-refuted brief), never
        satisfied.

        The evidence brief is the DETERMINISTIC CHECKPOINT — the ledger's
        files + the recorded summary — composed by code, so the judge attacks
        the record, not the doer's prose about the record. The judge's own
        tokens are not billed to the goal budget in G2 (one one-shot per
        iteration; stated, not hidden).
        """
        checkpoint = (self.store.get(goal.id) or goal).decoded_checkpoint()
        files = [str(f) for f in (checkpoint.get("files") or [])][:_CHECKPOINT_MAX_FILES]
        summary = str(checkpoint.get("remaining") or session.summary or "").strip()
        user = (
            f"GOAL CONTRACT:\n{goal.contract_text.strip()}\n\n"
            "LEDGER-PROVEN RESULTS (deterministic checkpoint):\n"
            f"- files created/changed per the ledger: "
            f"{', '.join(files) if files else '(none recorded)'}\n\n"
            f"SESSION REPLY (recorded summary):\n{summary or '(empty)'}\n\n"
            "Try to refute that the goal is satisfied. Default to refuted "
            "when uncertain."
        )
        try:
            from ..providers.adapters.base import LLMMessage

            route = await asyncio.wait_for(
                self.p.router.complete(
                    provider=session.provider or None,
                    model=session.model or None,
                    system=_JUDGE_SYSTEM,
                    messages=[LLMMessage(role="user", content=user)],
                    tools=[],
                    session_id=session.id,
                ),
                _JUDGE_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001 — no judge is a pending verdict
            return (
                "pending",
                f"{VERIFICATION_PENDING_NOTE} ({type(exc).__name__}: {str(exc)[:160]})",
            )
        if str(getattr(route, "provider", "") or "") == "mock":
            return "pending", VERIFICATION_PENDING_NOTE
        text = (getattr(route.response, "text", "") or "").strip()
        upper = text.upper()
        i_ref = upper.find("VERDICT: REFUTED")
        i_sat = upper.find("VERDICT: SATISFIED")
        if i_ref != -1 and (i_sat == -1 or i_ref < i_sat):
            return "refuted", _after_marker(text, i_ref + len("VERDICT: REFUTED"))
        if i_sat != -1:
            return "satisfied", _after_marker(text, i_sat + len("VERDICT: SATISFIED"))
        return (
            "refuted",
            "the judge's verdict was unreadable — refusing to satisfy "
            f"(reply began: {text[:160]!r})",
        )

    # -- restart survival --------------------------------------------------------

    def rehydrate(self) -> int:
        """Boot-time reconciliation of goals left MID-ITERATION by a crash.

        Called from the daemon lifespan's ``_rehydrate_step`` wiring, AFTER
        ``orchestrator.reconcile_interrupted_sessions`` (the session layer has
        already marked stranded sessions FAILED with "interrupted by a daemon
        restart" — this reads that truth, it never invents its own).

        For each active/tripped goal whose checkpoint carries a
        ``running_session_id``:

        * session COMPLETED → the run actually finished before the crash but
          its accounting never landed: accumulate its recorded tokens/cost and
          the checkpoint now (wallclock is unknowable and stays 0 — an honest
          gap, stated, over an invented number). The verifier is deliberately
          NOT re-run here: satisfaction is an ordinary-iteration decision, not
          a boot side effect.
        * anything else (FAILED / CANCELLED / missing / still marked
          ACTIVE-QUEUED because reconcile didn't run) → ONE breaker failure
          with reason "interrupted by a daemon restart"; the goal stays active
          or trips per the 3-in-30-min window, exactly like any other failure.

        Synchronous (the lifespan pattern); events are scheduled onto the
        running loop fire-and-forget. Returns the number of goals reconciled.
        """
        reconciled = 0
        for goal in self.store.list():
            if goal.state not in ("active", "tripped"):
                continue
            checkpoint = goal.decoded_checkpoint()
            sid = str(checkpoint.get("running_session_id") or "")
            if not sid:
                continue
            reconciled += 1
            session = self._session_row(sid)
            checkpoint.pop("running_session_id", None)
            if session is not None and session.status is SessionStatus.COMPLETED:
                # NEVER DOUBLE-BILL (D7): the crash may have landed BETWEEN
                # add_spend and set_checkpoint — spend committed, marker still
                # up. ``spent_json.last_session_id`` was written in the same
                # transaction as the spend, so it is the one flag that cannot
                # disagree with the balance: already stamped → clear the
                # marker only; not stamped → the crash beat add_spend and this
                # is the FIRST billing (wallclock stays 0 — unknowable across
                # a restart; an honest gap over an invented number).
                if self.store.last_accounted_session(goal) != session.id:
                    self._settle_spend(goal, session, 0.0)
                iteration = int(self.store.get(goal.id).decoded_spent()["iterations"])
                self.store.set_checkpoint(
                    goal.id, self._compose_checkpoint(goal, session, iteration)
                )
                continue
            checkpoint["last_session_id"] = sid
            self.store.set_checkpoint(goal.id, checkpoint)
            record, tripped = self.store.record_failure(goal.id, _INTERRUPTED_REASON)
            if tripped and record is not None:
                self._publish_bg(
                    GOAL_TRIPPED,
                    {
                        "goal_id": goal.id,
                        "name": goal.name,  # the notifier's phone lines
                        "reason": _INTERRUPTED_REASON,
                        "failures": len(record.decoded_breaker().get("failures", [])),
                    },
                    session_id=sid,
                )
        return reconciled

    def _session_row(self, session_id: str) -> Session | None:
        try:
            with session_scope(self.p.engine) as db:
                return db.get(Session, session_id)
        except Exception:  # noqa: BLE001 — rehydration must not brick boot
            log.exception("goal rehydrate could not read session %s", session_id)
            return None

    def _publish_bg(self, type_: str, payload: dict, session_id: str | None = None) -> None:
        """Publish from a SYNC caller: schedule on the running loop with a
        strong ref until it settles (the orchestrator's ``_publish_bg``). No
        loop (bare sync test) = no event — boot always has one."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        t = loop.create_task(
            self.p.event_bus.publish(type_, payload, session_id=session_id)
        )
        self._event_tasks.add(t)
        t.add_done_callback(self._event_tasks.discard)
