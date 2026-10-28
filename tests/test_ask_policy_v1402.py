"""v1.402.0 (part 4, "ask about intent, decide the rest") — ONE ASKING POLICY,
said wherever a model can ask the user.

The audit (verified before this change): nothing told a model WHAT to ask
about. The Build harness's default layer said "keep going until the task is
done" and nothing about asking; Claude Code's ``AskUserQuestion`` said "Ask
only what is the user's to decide" but Codex's ``request_user_input`` did not;
an agent run had only the voice's "make a sensible assumption and say so";
plan mode, the supervisor and the team prompts said nothing.

Now ``harness/components.ASK_POLICY`` is in:

* the harness's "How you work" for EVERY dialect, act and plan mode (a folder's
  own ``system_prompt.md`` replaces it with the rest of that section —
  omitted-is-inherited, like every default there);
* every agent run's system prompt, attended and unattended, every type;
* in a short form (``chat_turn.CHAT_ASK_POLICY``) the "# Tools" section of BOTH
  plain chat lanes — only when the turn armed a tool that does work. A
  question, a text-only turn and a turn that only reads keep their prompt;
* the question tools: ``dialects.ASK_TOOL_RULE`` ends Claude Code's
  ``AskUserQuestion``, Codex's ``request_user_input`` and Jarvis's own
  ``ask_user``.
"""

from __future__ import annotations

import asyncio

import pytest

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.core.models import AgentType
from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.daemon.chat_stream import stream_chat_turn
from iron_jarvis.daemon.chat_turn import run_chat_turn
from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
from iron_jarvis.harness import components as C
from iron_jarvis.harness import dialects as D
from iron_jarvis.harness import mode as M
from iron_jarvis.harness.session_tools import AskUserTool
from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.adapters.mock import MockLLMAdapter
from tests._fakes.harness_lane import WHO, body, post, stream, wire

POLICY = C.ASK_POLICY


def _how(system: str) -> str:
    return system.split("## How you work\n", 1)[1].split("\n## ", 1)[0]


# --------------------------------------------------------------------------- #
# the harness (a coding session's turn)
# --------------------------------------------------------------------------- #


def test_the_policy_says_what_the_brief_asks_for():
    for words in ("only about what they want or what counts as done", "never about how",
                  "Decide implementation details yourself", "say in your final summary what you assumed",
                  "keep working", "ask once", "with your recommendation", "do not ask again"):
        assert words in POLICY, words


@pytest.mark.parametrize("dialect", sorted(D.DIALECTS))
@pytest.mark.parametrize("planning", [False, True])
def test_every_dialect_carries_it_in_how_you_work_act_and_plan(dialect, planning):
    mode = C.plan_mode_text(exit_name="x", plan_name="") if planning else ""
    text = C.render(C.compose(dialect_section="Your tools: x.", dialect_id=dialect, mode=mode))
    assert POLICY in _how(text) and text.count(POLICY) == 1


def test_a_folders_own_how_you_work_replaces_it_like_every_default():
    layer = C.ProjectLayer(present=True, valid=True, texts={"system_prompt": "Our own way of working."})
    text = C.render(C.compose(dialect_section="Your tools: x.", dialect_id="claude", project=layer))
    assert POLICY not in text


@pytest.fixture()
def plat(tmp_path):
    platform = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.py").write_text("x = 1\n", encoding="utf-8")
    return platform, ws


@pytest.mark.parametrize("dialect", sorted(D.DIALECTS))
async def test_every_dialect_on_the_real_stream_lane_is_told(plat, dialect, monkeypatch):
    platform, ws = plat
    model = wire(platform, monkeypatch, ["Done."])
    await stream(platform, body(ws, "fix the bug in a.py"), harness=dialect)
    assert POLICY in _how(model.calls[0]["system"])
    # The chat's short form is the plain lanes' — a harness turn's Tools
    # section is its component tree, so it never carries it twice.
    assert CT.CHAT_ASK_POLICY not in model.calls[0]["system"]


async def test_plan_mode_on_the_real_lane_is_told(plat, monkeypatch):
    platform, ws = plat
    model = wire(platform, monkeypatch, ["Plan: change a.py."])
    await stream(platform, body(ws, "plan a fix for a.py"), plan_mode=True)
    system = model.calls[0]["system"]
    assert "## Mode\nPlan only" in system and POLICY in _how(system)


async def test_the_post_lane_harness_is_told(plat, monkeypatch):
    platform, ws = plat
    model = wire(platform, monkeypatch, ["Done."])
    await post(platform, body(ws, "fix the bug in a.py"))
    assert POLICY in _how(model.calls[0]["system"])


# --------------------------------------------------------------------------- #
# the question tools
# --------------------------------------------------------------------------- #


def _dialect_tool(dialect: str, name: str):
    return next(t for t in D.DIALECTS[dialect].tools if t.name == name)


def test_codex_request_user_input_gets_the_rule_claudes_question_tool_has():
    codex = _dialect_tool("gpt", "request_user_input").description
    claude = _dialect_tool("claude", "AskUserQuestion").description
    assert codex.endswith(D.ASK_TOOL_RULE) and claude.endswith(D.ASK_TOOL_RULE)
    assert "never how to do it" in D.ASK_TOOL_RULE


def test_jarvis_own_ask_user_keeps_and_strengthens_its_line():
    assert D.ASK_TOOL_RULE in AskUserTool.description
    assert "say what you assumed" in AskUserTool.description


@pytest.mark.parametrize(("dialect", "shown"), [("gpt", "request_user_input"), ("claude", "AskUserQuestion"),
                                                ("generic", "ask_user"), ("qwen", "ask_user")])
def test_the_question_tool_a_build_turn_shows_carries_the_rule(plat, dialect, shown):
    platform, _ws = plat
    turn = M.build(registry=platform.registry, permissions=platform.permissions, requested=dialect,
                   provider=WHO[0], model=WHO[1], stream=True, ask_user=True)
    spec = next(s for s in turn.specs(platform.registry) if s["name"] == shown)
    assert D.ASK_TOOL_RULE in spec["description"]


# --------------------------------------------------------------------------- #
# plain chat: the short form, only when work tools are armed (both lanes)
# --------------------------------------------------------------------------- #


async def _plain(platform, ws, lane: str, tools, monkeypatch) -> str:
    model = wire(platform, monkeypatch, ["Done."])
    kw = {"tools": tools} if tools is not None else {}
    b = ChatBody(messages=[ChatMessageBody(role="user", content="please tidy notes.md")],
                 provider=WHO[0], model=WHO[1], workspace_dir=str(ws), approval_mode="yolo", **kw)
    if lane == "stream":
        gen = await stream_chat_turn(platform, BUILTIN_PERSONAS, b)
        async for _chunk in gen:
            pass
    else:
        await run_chat_turn(platform, BUILTIN_PERSONAS, b)
    return model.calls[0]["system"]


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_chat_turn_that_armed_a_work_tool_gets_the_short_form_after_the_recovery_block(
        plat, lane, monkeypatch):
    platform, ws = plat
    system = await _plain(platform, ws, lane, ["write_file"], monkeypatch)
    assert system.count(CT.CHAT_ASK_POLICY) == 1
    assert CT.RECOVERY_BLOCK + "\n" + CT.CHAT_ASK_POLICY in system
    assert POLICY not in system  # the short form, not the harness's whole policy


@pytest.mark.parametrize("lane", ["stream", "post"])
@pytest.mark.parametrize("tools", [["read_file"], None])
async def test_a_chat_turn_that_only_reads_or_arms_nothing_is_unchanged(plat, lane, tools, monkeypatch):
    platform, ws = plat
    system = await _plain(platform, ws, lane, tools, monkeypatch)
    assert CT.CHAT_ASK_POLICY not in system and POLICY not in system


def test_what_counts_as_work():
    assert CT.arms_work({"write_file"}) and CT.arms_work(["shell"]) and CT.arms_work({"browser_click"})
    assert CT.arms_work({"delegate"}) and CT.arms_work({"edit_file", "read_file"})
    # Readers and lookups are not work — even the ones whose reversibility
    # is the fail-safe "irreversible" (web_search).
    for name in ("read_file", "list_files", "web_search", "web_fetch", "guide_search", "recall"):
        assert not CT.arms_work({name}), name
    assert not CT.arms_work(set()) and not CT.arms_work(None)


# --------------------------------------------------------------------------- #
# every agent run
# --------------------------------------------------------------------------- #


def _agent_system(tmp_path, **session_kw) -> str:
    p = build_platform(str(tmp_path / "home"))
    seen: list[str] = []

    class Scripted(MockLLMAdapter):
        async def complete(self, *, system, messages, tools, **kw):
            seen.append(system)
            return LLMResponse(text="Done.", finish_reason="stop")

    p.providers.register("mock", lambda model=None: Scripted())
    orch = Orchestrator(p)

    async def go():
        s = await orch.create_session("say hello", provider="mock", model="mock-1", **session_kw)
        return await orch.run_session(s.id)

    asyncio.run(go())
    assert seen, "the model was never called"
    return seen[0]


@pytest.mark.parametrize("kw", [
    {},
    {"origin": "chat"},
    {"origin": "schedule:nightly"},
    {"origin": "goal:g1"},
    {"agent_type": AgentType.SUPERVISOR},
    {"agent_type": AgentType.PLANNER},
    {"agent_type": AgentType.REVIEWER},
])
def test_every_agent_run_attended_or_not_carries_the_whole_policy_once(tmp_path, kw):
    system = _agent_system(tmp_path, **kw)
    assert system.count(POLICY) == 1, kw
