"""v1.339.0 — Continue after a restart resumes a PLANNED-STEPS job from its
steps (the last part of the pi-durable borrow).

v1.337.0 made Continue resume an agent run's own conversation — but only the
flat loop keeps one. A job that split itself into planned steps
(``agents/decompose.py``) runs each step in a fresh mini-loop, so it kept
nothing, and Continue re-planned from a one-paragraph recap and did the
finished steps again. Now the lane writes its plan and each finished step
(``RunPlan``); a resumed run skips those steps, carries their results, and
re-runs the step the stop cut off with a note to check what already happened.
"""

from __future__ import annotations

import asyncio
import json

from iron_jarvis.agents import transcript as tr
from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.agents.decompose import PlanStep, StepResult
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import (
    AgentRun,
    AgentType,
    RunPlan,
    Session,
    SessionStatus,
)
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMMessage, LLMResponse

#: Long enough (> 200 chars) to engage the planned-steps lane, and free of
#: bulk words ("every", "all", "each"), which route to the worklist plan.
_LONG_TASK = (
    "Create a file named statements.txt that notes the three quarterly "
    "statements, and after that produce a second file named summary.txt "
    "holding a short note about anything that looked out of place, keeping "
    "both files inside the session workspace so they can be reviewed later."
)

_PLAN = json.dumps({"steps": [{"goal": "collect the statements"}, {"goal": "write the summary"}]})


class _TextOnly(LLMAdapter):
    """Scripted text-only adapter (the router wraps it in the prompted
    scaffold, which is what engages the planned-steps lane)."""

    def __init__(self, replies, provider="local-plan"):
        self.provider = provider
        self.model = "m"
        self._replies = list(replies)
        self.calls: list[list[LLMMessage]] = []

    def capabilities(self):
        return {"provider": self.provider, "model": self.model, "tool_use": False, "vision": False}

    async def complete(self, *, system, messages, tools):
        self.calls.append(list(messages))
        return LLMResponse(text=self._replies.pop(0), usage={"input_tokens": 1, "output_tokens": 1})


def _plan_row(engine, run_id: str) -> RunPlan | None:
    with session_scope(engine) as db:
        row = db.get(RunPlan, run_id)
        if row is not None:
            db.expunge(row)
        return row


async def test_a_planned_run_keeps_its_plan_and_every_finished_step(platform, orchestrator):
    inner = _TextOnly([_PLAN, "Collected all three statements.", "Wrote summary.txt.", "Both done."])
    platform.providers.register(inner.provider, lambda model=None: inner)
    session = await orchestrator.run(_LONG_TASK, AgentType.BUILDER, provider=inner.provider)
    assert session.status is SessionStatus.COMPLETED
    run_id = orchestrator.transcript(session.id)["runs"][0]["id"]
    row = _plan_row(platform.engine, run_id)
    assert row is not None and row.session_id == session.id
    assert [s["goal"] for s in json.loads(row.plan_json)] == ["collect the statements", "write the summary"]
    done = json.loads(row.results_json)
    assert [(r["index"], r["ok"]) for r in done] == [(0, True), (1, True)]
    assert done[0]["output"] == "Collected all three statements."


def _seed_cut_off_plan(platform) -> tuple[str, str]:
    """A planned-steps job a restart cut off during its SECOND step."""
    with session_scope(platform.engine) as db:
        sess = Session(task=_LONG_TASK, status=SessionStatus.FAILED, provider="local-plan",
                       workspace_path=str(platform.config.workspaces_dir / "x"))
        db.add(sess)
        db.flush()
        run = AgentRun(session_id=sess.id, provider="local-plan")
        db.add(run)
        db.commit()
        sid, rid = sess.id, run.id
    tr._write_plan(
        platform.engine, rid, sid,
        json.dumps([{"goal": "collect the statements", "success_criteria": "", "tools": []},
                    {"goal": "write the summary", "success_criteria": "", "tools": []}]),
        json.dumps([{"index": 0, "goal": "collect the statements", "ok": True,
                     "output": "Collected Q1, Q2 and Q3."}]),
    )
    return sid, rid


async def test_continue_resumes_at_the_step_the_stop_cut_off(platform, orchestrator):
    sid, rid = _seed_cut_off_plan(platform)
    # NO plan reply: a resumed run must not plan again. Step 2, then assemble.
    inner = _TextOnly(["Wrote summary.txt listing Q1-Q3.", "All done."])
    platform.providers.register("local-plan", lambda model=None: inner)
    new = await orchestrator.continue_session(sid, "Continue where you left off", resume=True)
    assert json.loads(new.options_json or "{}").get("resume_run") == rid
    done_row = await asyncio.wait_for(
        orchestrator.run_session(new.id, get_agent_definition(AgentType.BUILDER)), timeout=30
    )
    assert done_row.status is SessionStatus.COMPLETED
    row = orchestrator.transcript(new.id)
    assert len(inner.calls) == 2, [m[0].content[:80] for m in inner.calls]
    step_prompt = inner.calls[0][-1].content
    assert tr.STEP_RESUME_NOTE in step_prompt
    assert "CURRENT step (2 of 2): write the summary" in step_prompt
    assert "Collected Q1, Q2 and Q3." in step_prompt  # the finished step's result rides along
    # The resumed run keeps its own record, both steps now finished.
    new_run = row["runs"][0]["id"]
    done = json.loads(_plan_row(platform.engine, new_run).results_json)
    assert [r["index"] for r in done] == [0, 1]


def test_a_broken_or_empty_record_resumes_nothing(platform):
    sid, rid = _seed_cut_off_plan(platform)
    tr._write_plan(platform.engine, rid, sid, "not json", "[]")
    assert tr.load_plan(platform.engine, rid) is None
    tr._write_plan(platform.engine, rid, sid, "[]", "[]")
    assert tr.load_plan(platform.engine, rid) is None
    assert tr.load_plan(platform.engine, "run_missing") is None


def test_the_record_round_trips_the_step_types(platform):
    sid, rid = _seed_cut_off_plan(platform)
    plan, done = tr.load_plan(platform.engine, rid)
    assert plan == [PlanStep(goal="collect the statements"), PlanStep(goal="write the summary")]
    assert done == [StepResult(index=0, goal="collect the statements", ok=True,
                               output="Collected Q1, Q2 and Q3.")]


class _Native(_TextOnly):
    """A natively tool-capable model: the planned-steps gate says NO for it."""

    def capabilities(self):
        return {"provider": self.provider, "model": self.model, "tool_use": True, "vision": False}


async def test_a_resumed_plan_stays_in_its_lane_even_where_a_fresh_run_would_not(
    platform, orchestrator
):
    """A fresh run on a native tool-caller never plans; a run that WAS planned
    and was cut off still carries on in its plan (not a flat restart)."""
    sid, rid = _seed_cut_off_plan(platform)
    inner = _Native(["Wrote summary.txt.", "All done."])
    platform.providers.register("local-plan", lambda model=None: inner)
    new = await orchestrator.continue_session(sid, "Continue where you left off", resume=True)
    done_row = await asyncio.wait_for(
        orchestrator.run_session(new.id, get_agent_definition(AgentType.BUILDER)), timeout=30
    )
    assert done_row.status is SessionStatus.COMPLETED
    assert "CURRENT step (2 of 2): write the summary" in inner.calls[0][-1].content
