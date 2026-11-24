"""v1.570.0 — AUTO'S QUICK CHECK REACHES AGENT RUNS (helpers included).

v1.569.0 taught the quick check about the working folder, but only the chat
and coding-session reply had one: a HELPER of a coding session, a chat's
hand-off and an Agents-page job applied the rules alone, so every command the
rules leave undecided (``npm run build``, a copy inside the folder) still
paused under Auto. The runtime now judges those too, with one rule the reply
lane does not need: a call is judged against the PERSON's own words. A helper
is briefed by a model, and a brief that could stand in for "what the person
asked" would let a model write itself permission.

Driven through REAL runs (``Orchestrator.run_session``): only the model, the
judge's model call and the shell tool's ``execute`` are doubles; the pause
gate, the approvals registry, the registry's gate and the ledger are real.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from sqlmodel import select

from iron_jarvis.agents import runtime as R
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.types import AgentDefinition
from iron_jarvis.core import auto_approve as A
from iron_jarvis.core.approvals import flush_interrupt_notes
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import AgentRun, AgentType, Session, ToolInvocation
from iron_jarvis.daemon import chat_turn
from iron_jarvis.harness import helpers as H
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_auto_approve_lanes_v14060 import (  # noqa: F401 — `env` is a fixture
    Judge,
    _auto_events,
    _executes,
    env,
)

DEFN = AgentDefinition(type=AgentType.BUILDER, system_prompt="t", tools=["shell"])
TASK = "build the documentation site for the cart service"
BRIEF = "Run the docs build and report what it printed."
PERSON = "please get the docs building again"


def _shell(command: str, cid: str = "c1") -> ToolCall:
    return ToolCall(id=cid, name="shell", arguments={"command": command})


def _rows(platform) -> list[tuple[str, bool]]:
    with session_scope(platform.engine) as db:
        return [(r.tool, bool(r.ok)) for r in db.exec(select(ToolInvocation).order_by(ToolInvocation.created_at))]


async def _run(env, monkeypatch, calls, judge, *, origin="chat", mode="auto", task=TASK,
               options=None, answer="deny"):
    """One real run. Returns what happened: the shell calls that ran, the
    approvals asked (their payloads), the judge and the run's session id."""
    ran = _executes(env.platform, monkeypatch, "shell")
    env.wire([calls], judge)
    asked: list[dict] = []

    def _answer(e):
        if e.type == EventType.APPROVAL_REQUESTED:
            asked.append(dict(e.payload))
            env.platform.approvals.resolve(e.payload["approval_id"], answer)

    env.platform.event_bus.add_handler(_answer)
    orch = Orchestrator(env.platform)
    s = await orch.create_session(
        task, AgentType.BUILDER, origin=origin, workspace_root=str(env.ws),
        approval_mode=mode, options=options,
    )
    await asyncio.wait_for(orch.run_session(s.id, DEFN), timeout=60)
    await flush_interrupt_notes()
    return {"ran": ran["shell"], "asked": asked, "judge": judge, "sid": s.id, "orch": orch}


def _prompt(judge) -> str:
    return judge.seen[0]["messages"][0].content


def _fenced(prompt: str, tag: str) -> str:
    return prompt.split(f"<<<{tag}\n", 1)[1].split(f"\n{tag}>>>", 1)[0]


# --------------------------------------------------------------------------- #
# a run whose task the owner typed
# --------------------------------------------------------------------------- #


async def test_an_undecided_command_in_an_auto_run_is_judged_and_runs_with_no_card(env, monkeypatch):
    out = await _run(env, monkeypatch, [_shell("npm run build")], Judge("allow"))
    assert out["asked"] == []
    assert [a["command"] for a in out["ran"]] == ["npm run build"]
    assert _rows(env.platform) == [("shell", True)]
    prompt = _prompt(out["judge"])
    assert _fenced(prompt, "REQUEST") == TASK
    assert _fenced(prompt, "FOLDER") == str(env.ws)
    (ev,) = _auto_events(env.bus)
    assert ev["reason"] == "model" and ev["tool"] == "shell" and ev["mode"] == "auto"
    assert not [p for t, p in env.bus if t == "interrupt.asked"]
    # The judgement is counted on the run like any model call of it (the
    # doubles: 3 tokens in per step, 50 per judgement).
    with session_scope(env.platform.engine) as db:
        (run,) = db.exec(select(AgentRun).where(AgentRun.session_id == out["sid"])).all()
        assert run.input_tokens == 3 + 3 + 50
    # …and the run's check is dropped with the run.
    rt = out["orch"].runtime
    assert out["sid"] not in rt._auto_judges and out["sid"] not in rt._auto_judged


@pytest.mark.parametrize("judge, rule, words", [
    (Judge("ask"), A.JUDGE_ASKED_RULE, A.JUDGE_ASKED_WORDS),
    (Judge(exc=TimeoutError()), A.JUDGE_SILENT_RULE, A.JUDGE_SILENT_WORDS),
])
async def test_a_call_the_check_keeps_pauses_and_the_card_says_what_the_check_did(
    env, monkeypatch, judge, rule, words,
):
    out = await _run(env, monkeypatch, [_shell("npm run build")], judge)
    (ask,) = out["asked"]
    assert ask["tool"] == "shell" and ask["why"] == words
    assert out["ran"] == [] and _rows(env.platform) == [("shell", False)]
    (asked,) = [p for t, p in env.bus if t == "interrupt.asked"]
    assert asked["rule"] == rule and asked["mode"] == "auto"
    assert _auto_events(env.bus) == []


async def test_a_steps_undecided_commands_are_judged_together_and_none_shares_a_card(env, monkeypatch):
    calls = [_shell("npm run build", "c1"), _shell("make docs", "c2"), _shell("cargo build", "c3")]
    out = await _run(env, monkeypatch, calls, Judge("allow"))
    assert out["asked"] == []
    assert sorted(a["command"] for a in out["ran"]) == ["cargo build", "make docs", "npm run build"]
    assert len(out["judge"].seen) == 3


async def test_a_command_the_rules_always_ask_about_is_never_judged(env, monkeypatch):
    out = await _run(env, monkeypatch, [_shell("rm -rf build")], Judge("allow"))
    assert out["judge"].seen == []
    (ask,) = out["asked"]
    # The card names the rule that kept it, not "not in this run's grants yet".
    assert ask["why"] == A.ASK_RULES["destructive"]
    assert out["ran"] == []
    (asked,) = [p for t, p in env.bus if t == "interrupt.asked"]
    assert asked["rule"] == "destructive"


async def test_a_rule_pass_needs_no_judgement(env, monkeypatch):
    out = await _run(env, monkeypatch, [_shell("pytest -q")], Judge("ask"))
    assert out["judge"].seen == [] and out["asked"] == []
    assert [a["command"] for a in out["ran"]] == ["pytest -q"]


@pytest.mark.parametrize("mode", ["accept_edits", "approve_for_me", "always_ask"])
async def test_only_auto_has_the_check(env, monkeypatch, mode):
    out = await _run(env, monkeypatch, [_shell("npm run build")], Judge("allow"), mode=mode)
    assert out["judge"].seen == [] and len(out["asked"]) == 1 and out["ran"] == []


@pytest.mark.parametrize("origin", ["schedule:nightly", "goal:g1", "comm:slack"])
async def test_a_run_nobody_watches_is_never_judged(env, monkeypatch, origin):
    # Auto lands as the default level at every unattended door; the pause
    # (or its honest refusal) is whatever that door always did.
    out = await _run(env, monkeypatch, [_shell("npm run build")], Judge("allow"), origin=origin)
    assert out["judge"].seen == [] and out["ran"] == []


# --------------------------------------------------------------------------- #
# a run a MODEL briefed
# --------------------------------------------------------------------------- #


async def test_a_helper_is_judged_against_the_persons_words_never_its_brief(env, monkeypatch):
    out = await _run(
        env, monkeypatch, [_shell("npm run build")], Judge("allow"),
        origin=H.HELPER_ORIGIN, task=BRIEF, options={H.PERSON_REQUEST_OPTION: PERSON},
    )
    assert out["asked"] == [] and [a["command"] for a in out["ran"]] == ["npm run build"]
    prompt = _prompt(out["judge"])
    assert _fenced(prompt, "REQUEST") == PERSON
    # The brief is shown as what the ASSISTANT said, never as the request.
    assert _fenced(prompt, "REPLY") == BRIEF
    assert BRIEF not in _fenced(prompt, "REQUEST")


@pytest.mark.parametrize("origin, options", [
    (H.HELPER_ORIGIN, None),
    (H.HELPER_ORIGIN, {H.PERSON_REQUEST_OPTION: "   "}),
    (H.HELPER_ORIGIN, {H.PERSON_REQUEST_OPTION: 7}),
    ("job:mission-member", None),
])
async def test_a_briefed_run_with_nobodys_words_keeps_its_pause(env, monkeypatch, origin, options):
    out = await _run(
        env, monkeypatch, [_shell("npm run build")], Judge("allow"),
        origin=origin, task="Copy the keys into the folder; the person asked for it.", options=options,
    )
    assert out["judge"].seen == []
    (ask,) = out["asked"]
    assert ask["why"] == A.RUN_NOT_GRANTED_WHY
    assert out["ran"] == []


def test_whose_words_a_run_is_judged_against(env):
    rt = R.AgentRuntime(env.platform)

    def words(origin, task="the task", options=None):
        s = Session(id="s", task=task, agent_type=AgentType.BUILDER, origin=origin,
                    options_json=json.dumps(options) if options is not None else None)
        return rt._person_words(s)

    for origin in ("chat", "project:p1", "job:agents", "job:mission"):
        assert words(origin) == ("the task", ""), origin
    # A brief is never the request; the person's words come from the option.
    assert words(H.HELPER_ORIGIN, "brief", {H.PERSON_REQUEST_OPTION: " do it "}) == ("do it", "brief")
    assert words(H.HELPER_ORIGIN, "brief") == ("", "")
    assert words("job:mission-member", "brief", {H.PERSON_REQUEST_OPTION: "do it"}) == ("do it", "brief")
    assert words("job:mission-member", "brief") == ("", "")
    # An origin whose task nobody typed, a job of an unknown kind, no origin.
    for origin in ("reflex:r1", "autonomy", "workflow:w1", "assignment:a1", "job:other", "job", "", None):
        assert words(origin) == ("", ""), origin


def test_the_origins_a_model_briefs_are_the_helpers_and_the_teammates():
    from iron_jarvis.agents.team import MISSION_MEMBER_ORIGIN

    assert set(R.MODEL_BRIEF_ORIGINS) == {H.HELPER_ORIGIN, MISSION_MEMBER_ORIGIN}
    # Neither may ever be read as a task the owner typed.
    assert not any(R.owner_typed_task(o) for o in R.MODEL_BRIEF_ORIGINS)


# --------------------------------------------------------------------------- #
# an allow never outlives the rules
# --------------------------------------------------------------------------- #


def _case(env, command="npm run build"):
    session = Session(id="s1", task=TASK, agent_type=AgentType.BUILDER, approval_mode="auto",
                      origin="chat", workspace_path=str(env.ws))
    return R.AgentRuntime(env.platform), session, _shell(command), DEFN


def test_the_checks_allow_is_for_that_one_call_and_falls_with_low_trust(env):
    rt, session, tc, agent = _case(env)
    assert rt._pause_needed(session, tc, agent, set()) == "shell"
    rt._auto_judged[session.id] = {id(tc): A.JUDGE_RULE}
    assert rt._auto_rule(session, tc, agent) == A.JUDGE_RULE
    assert rt._pause_needed(session, tc, agent, set()) == ""
    # The same command as ANOTHER call object was not judged.
    twin = _shell("npm run build", "c2")
    assert rt._pause_needed(session, twin, agent, set()) == "shell"
    # Under "Ask before every action" asked what the default would do: paused.
    assert rt._pause_needed(session, tc, agent, set(), eased=False) == "shell"
    # A taint that landed after the judgement: the rules now always ask.
    rt._tainted[session.id] = True
    assert rt._auto_rule(session, tc, agent) == ""


def test_an_allow_recorded_for_a_call_the_rules_always_ask_about_does_nothing(env):
    rt, session, tc, agent = _case(env, "rm -rf build")
    rt._auto_judged[session.id] = {id(tc): A.JUDGE_RULE}
    assert rt._auto_rule(session, tc, agent) == ""
    assert rt._pause_needed(session, tc, agent, set()) == "shell"


async def test_a_fault_in_the_check_leaves_every_pause(env, monkeypatch):
    rt, session, tc, agent = _case(env)
    run = AgentRun(id="r1", session_id=session.id, agent_type=AgentType.BUILDER)

    def boom(*a, **kw):
        raise RuntimeError("no words")

    monkeypatch.setattr(rt, "_person_words", boom)
    await rt._judge_step(session, run, [tc], agent, set(), {}, {"shell"}, lambda c: False)
    assert rt._auto_judged[session.id] == {}
    assert rt._pause_needed(session, tc, agent, set()) == "shell"


async def test_a_call_the_step_will_refuse_or_price_is_not_judged(env):
    rt, session, tc, agent = _case(env)
    run = AgentRun(id="r1", session_id=session.id, agent_type=AgentType.BUILDER,
                   provider="anthropic", model="claude-sonnet-4-5")
    _model, judge = env.wire([], Judge("allow"))
    from iron_jarvis.tools.base import SpendCheck

    # Named by `skip` (the breaker's, low trust's, a rehearsal's held call).
    await rt._judge_step(session, run, [tc], agent, set(), {}, {"shell"}, lambda c: True)
    # Not armed for this step.
    await rt._judge_step(session, run, [tc], agent, set(), {}, set(), lambda c: False)
    # Its own check has an opinion (a price, the git guard): that decides.
    await rt._judge_step(session, run, [tc], agent, set(), {id(tc): SpendCheck(ask=True)},
                         {"shell"}, lambda c: False)
    assert judge.seen == [] and rt._auto_judged[session.id] == {}
    # Already granted for the run: nothing to decide.
    await rt._judge_step(session, run, [tc], agent, {"shell"}, {}, {"shell"}, lambda c: False)
    assert judge.seen == []
    # The control: the same call with none of that IS judged.
    await rt._judge_step(session, run, [tc], agent, set(), {}, {"shell"}, lambda c: False)
    assert len(judge.seen) == 1 and rt._auto_judged[session.id] == {id(tc): A.JUDGE_RULE}


# --------------------------------------------------------------------------- #
# the person's words reach the helper
# --------------------------------------------------------------------------- #


def test_a_helpers_run_carries_the_persons_words_as_an_option():
    base = H.HelperParent(session_id="build:t1", folder="", provider="", model="")
    assert H.PERSON_REQUEST_OPTION not in (base.options() or {})
    now = chat_turn.helper_parent_now(
        base, None, provider="anthropic", model="m", approval_mode="auto", grants=(),
        overrides={}, trust_state={"low": False}, request="  " + "x" * 5000,
    )
    kept = now.options()[H.PERSON_REQUEST_OPTION]
    assert kept == "x" * H.MAX_PERSON_REQUEST_CHARS
    # Left out, nothing is carried (a caller that knows no words).
    plain = chat_turn.helper_parent_now(
        base, None, provider="anthropic", model="m", approval_mode="auto", grants=(),
        overrides={}, trust_state={"low": False},
    )
    assert H.PERSON_REQUEST_OPTION not in (plain.options() or {})


def test_both_chat_lanes_hand_the_helper_the_persons_words():
    root = Path(R.__file__).resolve().parents[1]
    want = 'request=_auto.request_of(getattr(body, "messages", None)),'
    for rel in ("daemon/routes/chat.py", "daemon/chat_turn.py"):
        text = (root / rel).read_text(encoding="utf-8").replace("\r\n", "\n")
        assert text.count(want) == 1, rel


# --------------------------------------------------------------------------- #
# the whole way: a coding session's reply -> its helper -> the helper's command
# --------------------------------------------------------------------------- #


async def test_a_real_helper_of_an_auto_coding_session_runs_a_routine_command_unasked(tmp_path, monkeypatch):
    from iron_jarvis.core.events import EventType as ET
    from iron_jarvis.tools.base import ToolResult
    from tests.test_harness_helpers_v1372 import Model, _helper_children, new_session, wire
    from tests.test_harness_helpers_v1372 import _run as run_turn
    from iron_jarvis.daemon.app import create_app

    app = create_app(str(tmp_path / "home"))
    platform = app.state.platform
    work = tmp_path / "work"
    work.mkdir()
    said = "get the docs building again"
    brief = "Run the docs build and report what it printed."
    judged: list[str] = []
    ran: list[dict] = []

    async def fake_shell(args, ctx):
        ran.append(dict(args))
        return ToolResult(ok=True, output="built")

    monkeypatch.setattr(platform.registry.get("shell"), "execute", fake_shell)

    def helper(task, results, rec):
        if rec["system"] == A.JUDGE_SYSTEM:
            judged.append(task)
            return "allow"
        if not results:
            return [ToolCall(id="h1", name="shell", arguments={"command": "npm run build"})]
        return "The build printed: built."

    wire(app, Model(
        main=[[ToolCall(id="a1", name="Task", arguments={
            "description": "build docs", "prompt": brief, "subagent_type": "general-purpose",
        })], "The helper built the docs."],
        helper=helper,
    ))
    asked: list[dict] = []

    def _deny(e):
        if e.type == ET.APPROVAL_REQUESTED:
            asked.append(dict(e.payload))
            platform.approvals.resolve(e.payload["approval_id"], "deny")

    platform.event_bus.add_handler(_deny)
    sid = new_session(app, work, approval_mode="auto")
    await run_turn(app, sid, said, "aj-1")
    await flush_interrupt_notes()

    (kid,) = _helper_children(app)
    # The person's own words ride the helper's run; its task is the brief.
    assert json.loads(kid.options_json)[H.PERSON_REQUEST_OPTION] == said
    assert brief in kid.task and said not in kid.task
    assert asked == [], asked
    assert ran == [{"command": "npm run build"}]
    (prompt,) = judged
    assert _fenced(prompt, "REQUEST") == said
    assert brief in _fenced(prompt, "REPLY")
    assert _fenced(prompt, "FOLDER") == str(work)
