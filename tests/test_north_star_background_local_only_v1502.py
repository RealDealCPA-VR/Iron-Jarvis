"""North-star 2.3 (v1.502.0): background learning honours Local only.

Three jobs read a finished run's material AFTER the run, outside its Local
only scope, and sent it to the default model, a cloud one included:

* skill learning: the distill sweep on ``session.completed`` sends the run's
  task, summary, tool arguments and output (``skills/learning._session_context``)
  to write a skill draft;
* the reflection coach: evidence quotes of an agent's runs
  (``coach/engine._prompt``) go to ``router.complete``;
* autonomy proposals (``motivation/engine._gather_context``): the finding said
  ``recent_events`` carried run material. It does not — only each event's type
  and time — so that finding did not go red (pinned below so it stays true).

Each job now sends a Local only run's material only inside
``local_only_scope(True)``, so the EXISTING gate (the one-shot gate, the
router's ``_local_only_gate``) refuses a model that leaves this network; work
that is not Local only is unchanged. Proven through those gates, with counting
fakes for the models.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun, AgentType, Session, SessionStatus, ToolInvocation
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMResponse

SECRET = "Hartley refund 4,210.55"

SKILL_MD = (
    "---\nname: vendor-ledger-reconciliation\n"
    "description: Use when reconciling vendor ledgers against bank statements.\n---\n\n"
    "# Steps\n\n1. Pull the ledger with read_file.\n2. Compare with run_code.\n"
    "3. Write the summary with write_file.\n"
)


class _Cloud(LLMAdapter):
    """A counting stand-in for a cloud model (``claude-cli``)."""

    def __init__(self, text: str = SKILL_MD) -> None:
        self.provider, self.model = "claude-cli", "m"
        self.text = text
        self.prompts: list[str] = []

    def capabilities(self):
        return {"tool_use": True, "vision": True}

    async def complete(self, *, system, messages, tools, **kw):
        self.prompts.append(str(messages[-1].content))
        return LLMResponse(text=self.text, tool_calls=[], usage={})

    async def stream(self, *, system, messages, tools, **kw):
        self.prompts.append(str(messages[-1].content))
        yield {"type": "final", "response": LLMResponse(text=self.text, tool_calls=[], usage={})}


def _cloud_default(platform, monkeypatch, cloud):
    platform.providers.register("claude-cli", lambda m=None: cloud)
    real = platform.providers.available
    monkeypatch.setattr(platform.providers, "available", lambda n: n == "claude-cli" or real(n))
    monkeypatch.setattr(platform.config, "default_provider", "claude-cli")
    monkeypatch.setattr(platform.config, "default_model", "")


# --------------------------------------------------------------------------- #
# Skill learning
# --------------------------------------------------------------------------- #
@pytest.fixture()
def app(tmp_path, monkeypatch):
    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.skills import framework

    monkeypatch.setattr(framework, "external_skill_roots", lambda: [])
    monkeypatch.setattr(framework, "marketplace_catalog_dirs", lambda home=None: [])
    return create_app(str(tmp_path))


def _finished_run(platform, *, local_only: bool, task: str) -> str:
    sess = Session(
        task=task, status=SessionStatus.COMPLETED, summary=f"Reconciled. {SECRET}",
        options_json=json.dumps({"local_only": True}) if local_only else None,
    )
    sid = sess.id
    with session_scope(platform.engine) as db:
        db.add(sess)
        for tool in ("read_file", "run_code", "write_file"):
            db.add(ToolInvocation(session_id=sid, agent_run_id="r", tool=tool, ok=True,
                                  args_json=json.dumps({"path": "hartley.xlsx"}),
                                  output=SECRET))
        db.commit()
    return sid


def _candidates(platform):
    from iron_jarvis.skills.learning_models import SkillCandidateRecord

    with session_scope(platform.engine) as db:
        return list(db.exec(select(SkillCandidateRecord)))


def test_a_local_only_runs_skill_draft_is_never_written_by_a_cloud_model(app, monkeypatch):
    platform, d = app.state.platform, app.state.d
    cloud = _Cloud()
    _cloud_default(platform, monkeypatch, cloud)
    learning = platform.skill_learning
    sid = _finished_run(platform, local_only=True,
                        task="reconcile q3 vendor ledger balances against bank statements")
    learning.observe_session(sid)
    assert [c.session_id for c in _candidates(platform)] == [sid]

    out = asyncio.run(learning.distill_candidates(d._skill_distill_complete()))
    assert cloud.prompts == [], "the run's task, summary and tool output went to the cloud"
    assert out["proposals"] == [] and out.get("held_local_only") == 1
    (cand,) = _candidates(platform)
    assert cand.status == "pending" and cand.attempts == 0, "held, not dismissed or spent"


def test_an_ordinary_runs_skill_draft_is_written_as_before(app, monkeypatch):
    platform, d = app.state.platform, app.state.d
    cloud = _Cloud()
    _cloud_default(platform, monkeypatch, cloud)
    learning = platform.skill_learning
    learning.observe_session(_finished_run(
        platform, local_only=False,
        task="reconcile q3 vendor ledger balances against bank statements",
    ))
    out = asyncio.run(learning.distill_candidates(d._skill_distill_complete()))
    assert len(cloud.prompts) == 1 and len(out["proposals"]) == 1


def test_a_held_local_only_candidate_does_not_starve_the_ones_after_it(app, monkeypatch):
    platform, d = app.state.platform, app.state.d
    cloud = _Cloud()
    _cloud_default(platform, monkeypatch, cloud)
    learning = platform.skill_learning
    learning.observe_session(_finished_run(
        platform, local_only=True, task="reconcile q3 vendor ledger balances against bank statements",
    ))
    learning.observe_session(_finished_run(
        platform, local_only=False, task="draft quarterly payroll variance memo for partners",
    ))
    out = asyncio.run(learning.distill_candidates(d._skill_distill_complete(), limit=1))
    assert len(cloud.prompts) == 1 and "payroll" in cloud.prompts[0]
    assert "reconcile" not in cloud.prompts[0] and out.get("held_local_only") == 1


class _Local(_Cloud):
    """A model on THIS PC (Ollama on loopback): the scope lets it write."""

    def __init__(self) -> None:
        super().__init__()
        self.provider = "ollama"
        self._endpoint = "http://127.0.0.1:11434/v1"


def test_a_local_only_runs_draft_written_on_this_pc_waits_for_the_owner(app, monkeypatch):
    """A model on this network may write it — and it is never ADDED unasked
    (auto-approve on): once kept, a skill reaches every later prompt, a cloud
    model's included."""
    platform, d = app.state.platform, app.state.d
    local = _Local()
    platform.providers.register("ollama", lambda m=None: local)
    real = platform.providers.available
    monkeypatch.setattr(platform.providers, "available", lambda n: n == "ollama" or real(n))
    monkeypatch.setattr(platform.config, "default_provider", "ollama")
    monkeypatch.setattr(platform.config, "default_model", "")
    monkeypatch.setattr(platform.config, "skill_learning_auto_approve", True)
    learning = platform.skill_learning
    learning.observe_session(_finished_run(
        platform, local_only=True, task="reconcile q3 vendor ledger balances against bank statements",
    ))
    out = asyncio.run(learning.distill_candidates(d._skill_distill_complete()))
    assert len(local.prompts) == 1 and len(out["proposals"]) == 1
    from iron_jarvis.skills.learning_models import SkillProposalRecord

    with session_scope(platform.engine) as db:
        (prop,) = list(db.exec(select(SkillProposalRecord)))
    assert prop.status == "pending", "a Local only run's skill was added without the owner"


# --------------------------------------------------------------------------- #
# The coach
# --------------------------------------------------------------------------- #
NAME, ROSTER = "scout", "custom:scout"
PROMPT = (
    "You are Scout, a focused research helper.\n"
    "Never send client data to anyone outside the firm.\n"
    "Always cite the file you read a number from.\n"
    "Keep answers short and name the file you changed, and say plainly when a "
    "page was unreadable instead of guessing at what it said."
)


@pytest.fixture()
def coach_platform(tmp_path):
    from iron_jarvis.platform import build_platform

    p = build_platform(str(tmp_path))
    p.agents_registry.register(NAME, PROMPT, ["read_file", "write_file"], description="a scout")
    return p


def _bad_run(platform, *, local_only: bool) -> str:
    sess = Session(
        task="read the K-1 and list the partners", agent_type=AgentType.BUILDER,
        agent_name=ROSTER, status=SessionStatus.COMPLETED, outcome="completed", summary="done",
        options_json=json.dumps({"local_only": True}) if local_only else None,
    )
    sess.finished_at = sess.created_at + timedelta(seconds=30)
    sid = sess.id
    run = AgentRun(session_id=sid, steps=3, agent_type=AgentType.BUILDER)
    with session_scope(platform.engine) as db:
        db.add(sess)
        db.add(run)
        for _ in range(2):
            db.add(ToolInvocation(session_id=sid, agent_run_id=run.id, tool="write_file", ok=False,
                                  output=f"permission denied: {SECRET}",
                                  args_json=json.dumps({"path": "a"}), reversibility="reversible"))
        db.commit()
    return sid


def test_the_coach_never_sends_a_local_only_runs_evidence_to_a_cloud_model(coach_platform, monkeypatch):
    from iron_jarvis.coach import CoachEngine

    cloud = _Cloud(text=f"```\n{PROMPT}\nCheck the file exists before you write to it.\n```")
    _cloud_default(coach_platform, monkeypatch, cloud)
    for _ in range(3):
        _bad_run(coach_platform, local_only=True)
    coach = CoachEngine(coach_platform)
    assert asyncio.run(coach.propose(NAME)) is None
    assert cloud.prompts == [], "the runs' evidence went to the cloud model"
    reason = coach.last_reason_for(NAME)
    assert "Local only" in reason and "Claude" in reason, reason


def test_the_coach_still_asks_the_model_for_ordinary_runs(coach_platform, monkeypatch):
    from iron_jarvis.coach import CoachEngine

    cloud = _Cloud(text=f"```\n{PROMPT}\nCheck the file exists before you write to it.\n```")
    _cloud_default(coach_platform, monkeypatch, cloud)
    for _ in range(3):
        _bad_run(coach_platform, local_only=False)
    coach = CoachEngine(coach_platform)
    record = asyncio.run(coach.propose(NAME))
    assert len(cloud.prompts) == 1 and record is not None, coach.last_reason_for(NAME)


# --------------------------------------------------------------------------- #
# Autonomy proposals: the finding that did not go red
# --------------------------------------------------------------------------- #
def test_the_deliberation_context_carries_no_runs_words_only_event_types(coach_platform):
    """``recent_events`` is each event's type and time — never its payload —
    so a Local only run's summary cannot reach the deliberation prompt. The
    finding (2.3, motivation) is struck; this keeps it true."""
    from iron_jarvis.core.events import EventType
    from iron_jarvis.motivation.engine import IntentEngine

    bus = coach_platform.event_bus
    asyncio.run(bus.publish(EventType.SESSION_COMPLETED, {"summary": SECRET, "status": "completed"},
                            session_id="s_lo"))
    engine = IntentEngine(coach_platform)
    ctx = engine._gather_context([])
    blob = json.dumps(ctx, default=str)
    assert SECRET not in blob
    assert ctx["recent_events"] and set(ctx["recent_events"][-1]) == {"type", "ts"}
