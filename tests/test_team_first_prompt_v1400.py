"""v1.400.0 — TEAM-FIRST: what the planner of a coding session is told.

The 2026-10-26 live team benchmark (12 frontier tasks x 3 repeats, a Claude
Sonnet planner, a model team of glm and Codex): the planner handed ZERO pieces
to a member in all 36 runs. It WAS shown the team — "# Your model team" and a
helper tool that takes ``role:<id>`` — but every word only PERMITTED handing
work out. Team-first (``Config.team_first``, OFF by default) tells it the user
wants this model's usage saved:

* the team section gets ONE short instruction after the member lines, naming
  only the roles the listed members hold (``model_team.team_first_words``);
* the helper tool's description LEADS with one sentence in that tool's own
  argument words (``model_team.team_first_tool_sentence``): Claude Code's
  ``Agent``, Codex's ``spawn_agent``, Jarvis's own ``delegate`` /
  ``spawn_agent`` (the generic dialect and Qwen Code's, which has no helper
  tool of its own).

Both only in a harness turn whose team section is SHOWN (it is built only when
the turn can start a helper — the stream lane; MIRROR NOTE: both lanes build
it in ``chat_turn._build_harness``). Off, or with no team, the system prompt
and every tool spec are BYTE-IDENTICAL to v1.399.4:
``fixtures/team_first_off_golden_v1400.json`` was CAPTURED FROM a9b8cd50
(v1.399.4, before any team-first code) by running this file against that tree
with ``IJ_REGEN_TEAM_FIRST_OFF=1``. Regenerate ONLY for an intended change to
what a coding session's planner is shown, and say so in the commit.

REGENERATED ONCE, v1.402.0 (part 4, "ask about intent, decide the rest"): the
harness's "How you work" gained the asking policy (``components.ASK_POLICY``)
for every dialect. Checked before it was written: every key differs from the
v1.399.4 capture by exactly ``"\\n\\n" + ASK_POLICY`` in the system prompt,
and the tool specs are identical (``tests/test_ask_policy_v1402.py`` holds
that one change to its place).

The capture is machine-independent: the temporary folder and the home folder
become placeholders, today's date becomes ``<TODAY>``, and a command tool's
"How commands run here" note (it names the shell this PC has) becomes
``<RUNTIME NOTE>``.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

from iron_jarvis.daemon.chat_stream import stream_chat_turn
from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.router import RouteResult

FIXTURE = Path(__file__).parent / "fixtures" / "team_first_off_golden_v1400.json"
REGEN = os.environ.get("IJ_REGEN_TEAM_FIRST_OFF") == "1"
WHO = ("anthropic", "claude-sonnet-4-5")
#: Every dialect a coding session's planner can speak.
DIALECTS = ("claude", "gpt", "generic", "qwen")
#: The helper tools each dialect SHOWS (a real tool shown under its own name
#: in the generic and Qwen dialects).
HELPER_TOOLS = {"claude": ["Agent"], "gpt": ["spawn_agent"], "generic": ["delegate", "spawn_agent"],
                "qwen": ["delegate", "spawn_agent"]}


class _Model:
    """A scripted planner on the router: it records what it was shown and
    answers in words (no tool call)."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def _next(self, system, tools) -> LLMResponse:
        self.calls.append({"system": system, "specs": list(tools or [])})
        return LLMResponse(text="Done.", usage={"input_tokens": 3, "output_tokens": 1})

    async def stream(self, *, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        resp = self._next(system, tools)
        yield {"type": "text", "text": resp.text}
        yield {"type": "final", "response": resp, "provider": WHO[0], "model": WHO[1]}

    async def complete(self, *, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        return RouteResult(self._next(system, tools), *WHO)


def _register_team(platform) -> None:
    reg = platform.agents_registry
    reg.register("glm", "You are glm.", [], description="reads and drafts code", provider="mock",
                 model="glm-5.3-flash", roles=["read", "code"])
    reg.register("codex", "You are codex.", [], description="reviews a change", provider="codex-cli",
                 model="gpt-6-astra", roles=["review"])


_NOTE_RX = re.compile(r"\n\nHow commands run here: .*\Z", re.DOTALL)


def _normalise(value: Any, subs: list[tuple[str, str]]) -> Any:
    if isinstance(value, str):
        for raw, mark in subs:
            value = value.replace(raw, mark)
        return value.replace("<TMP>\\", "<TMP>/")
    if isinstance(value, list):
        return [_normalise(v, subs) for v in value]
    if isinstance(value, dict):
        out = {k: _normalise(v, subs) for k, v in value.items()}
        if isinstance(out.get("description"), str) and "name" in out:
            out["description"] = _NOTE_RX.sub("\n\n<RUNTIME NOTE>", out["description"])
        return out
    return value


async def capture(tmp_path: Path, monkeypatch, *, team: bool, team_first: "bool | None" = None,
                  lanes: tuple[str, ...] = ("stream",)) -> dict[str, Any]:
    """Per dialect: the planner's system prompt and its tool specs."""
    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    platform = build_platform(str(tmp_path / "home"))
    monkeypatch.setattr(platform.providers, "inherited_from", lambda name: None)
    if team:
        _register_team(platform)
    if team_first is not None:
        platform.config.team_first = team_first
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    (ws / "a.py").write_text("x = 1\n", encoding="utf-8")
    out: dict[str, Any] = {}
    for dialect in DIALECTS:
        for lane in lanes:
            model = _Model()
            monkeypatch.setattr(platform.router, "stream", model.stream)
            monkeypatch.setattr(platform.router, "complete", model.complete)
            body = ChatBody(
                messages=[ChatMessageBody(role="user", content="fix the bug in a.py")],
                provider=WHO[0], model=WHO[1], approval_mode="approve_for_me", workspace_dir=str(ws),
            )
            if lane == "stream":
                gen = await stream_chat_turn(platform, BUILTIN_PERSONAS, body, harness=dialect)
                async for _chunk in gen:
                    pass
            else:
                from iron_jarvis.daemon.chat_turn import run_chat_turn

                await run_chat_turn(platform, BUILTIN_PERSONAS, body, harness=dialect)
            assert model.calls, f"{dialect}/{lane}: the planner was never called"
            first = model.calls[0]
            out[f"{lane}:{dialect}"] = {"system": first["system"], "specs": first["specs"]}
    subs = [(_dt.date.today().isoformat(), "<TODAY>")]
    for root, mark in ((tmp_path, "<TMP>"), (Path.home(), "<HOME>")):
        for form in dict.fromkeys((root, root.resolve())):
            subs += [(str(form), mark), (form.as_posix(), mark)]
    return json.loads(json.dumps(_normalise(out, subs), default=str))


async def test_a_team_turn_with_team_first_off_is_what_v1399_4_sent(tmp_path, monkeypatch):
    """The setting OFF (and never set): the planner's system prompt and every
    tool spec, per dialect, byte for byte as v1.399.4 sent them."""
    got = await capture(tmp_path, monkeypatch, team=True)
    if REGEN:
        # (bytes: LF on every OS)
        FIXTURE.write_bytes((json.dumps(got, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))
        pytest.skip("fixture regenerated")
    golden = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert set(got) == set(golden)
    for key in sorted(golden):
        assert "# Your model team" in golden[key]["system"], key  # the team IS shown
        assert got[key]["system"] == golden[key]["system"], f"{key}: the system prompt changed"
        assert got[key]["specs"] == golden[key]["specs"], f"{key}: the tool specs changed"


async def test_team_first_set_off_is_what_v1399_4_sent_too(tmp_path, monkeypatch):
    """The setting explicitly OFF (the default) reads exactly as never set."""
    got = await capture(tmp_path, monkeypatch, team=True, team_first=False)
    golden = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert got == golden


# --------------------------------------------------------------------------- #
# ON: exactly two additions, and only where a team is shown
# --------------------------------------------------------------------------- #

#: The instruction, word for word, for the team above (glm: read + code;
#: codex: review — no member holds "test", so it is not named).
WORDS = (
    "Work as a team: the user wants to save this model's usage. Hand pieces to the team by role: "
    "reading and summarizing files (role:read), first drafts of separate parts (role:code) and "
    "reviewing a change (role:review). Start several helpers in one message when their pieces do "
    "not depend on each other. Keep the plan, the hard decisions and the final check yourself, and "
    "check what a helper hands back."
)
#: The sentence each helper tool LEADS with, in its own argument words.
SENTENCES = {
    "Agent": ("Your model team is listed under 'Your model team': for a piece that fits a role, "
              "give subagent_type role:read, role:code or role:review."),
    "spawn_agent@gpt": ("Your model team is listed under 'Your model team': for a piece that fits a "
                        "role, give agent_type role:read, role:code or role:review."),
    "delegate": ("Your model team is listed under 'Your model team': for a piece that fits a role, "
                 "set role to read, code or review."),
    "spawn_agent": ("Your model team is listed under 'Your model team': for a piece that fits a role, "
                    "set role to read, code or review."),
}


def _sentence(dialect: str, name: str) -> str:
    return SENTENCES.get(f"{name}@{dialect}") or SENTENCES[name]


async def test_team_first_on_adds_the_instruction_after_the_members_and_leads_every_helper_tool(
        tmp_path, monkeypatch):
    golden = json.loads(FIXTURE.read_text(encoding="utf-8"))
    got = await capture(tmp_path, monkeypatch, team=True, team_first=True)
    assert set(got) == set(golden)
    for key in sorted(golden):
        dialect = key.split(":", 1)[1]
        want, have = golden[key], got[key]
        # The system prompt: the off prompt plus the instruction, ONCE, right
        # after the last member line — nothing else moved.
        assert have["system"].count(WORDS) == 1, key
        assert "(no runs yet)\n\n" + WORDS in have["system"], key
        assert have["system"].replace("\n\n" + WORDS, "", 1) == want["system"], key
        # The tool specs: the same tools in the same order; each helper tool's
        # description LEADS with the sentence; every other spec unchanged.
        assert [s["name"] for s in have["specs"]] == [s["name"] for s in want["specs"]], key
        helpers = [s["name"] for s in have["specs"] if s["name"] in HELPER_TOOLS[dialect]]
        assert helpers == HELPER_TOOLS[dialect], key
        for h, w in zip(have["specs"], want["specs"]):
            if h["name"] in HELPER_TOOLS[dialect]:
                assert h["description"] == _sentence(dialect, h["name"]) + " " + w["description"], (key, h["name"])
                assert h["input_schema"] == w["input_schema"], (key, h["name"])
            else:
                assert h == w, (key, h["name"])


def _same(a: dict[str, Any], b: dict[str, Any], a_dir: str, b_dir: str) -> bool:
    return (json.dumps(a, sort_keys=True).replace(f"<TMP>/{a_dir}", "<TMP>/x")
            == json.dumps(b, sort_keys=True).replace(f"<TMP>/{b_dir}", "<TMP>/x"))


async def test_team_first_on_with_no_team_changes_nothing(tmp_path, monkeypatch):
    off = await capture(tmp_path / "off", monkeypatch, team=False, team_first=False)
    on = await capture(tmp_path / "on", monkeypatch, team=False, team_first=True)
    for key in off:
        assert "# Your model team" not in off[key]["system"], key
    assert _same(on, off, "on", "off")
    text = json.dumps(on)
    assert "Work as a team" not in text and "Your model team is listed under" not in text


async def test_the_post_lane_shows_no_team_so_team_first_changes_nothing_there(tmp_path, monkeypatch):
    # The POST lane cannot card a helper door, so it never shows the team.
    off = await capture(tmp_path / "off", monkeypatch, team=True, team_first=False, lanes=("post",))
    on = await capture(tmp_path / "on", monkeypatch, team=True, team_first=True, lanes=("post",))
    for key in off:
        assert "# Your model team" not in off[key]["system"], key
    assert _same(on, off, "on", "off")


async def test_the_planner_budgets_the_instruction(tmp_path, monkeypatch):
    """The team section — and the instruction in it — is in the system prompt
    the HISTORY PLANNER prices (the budget rule), never added after it."""
    from iron_jarvis.daemon.routes import chat as chat_routes

    seen: list[str] = []
    real = chat_routes._plan_context

    def spy(d, body, system, provider, model, messages=None):
        seen.append(system)
        return real(d, body, system, provider, model, messages=messages)

    monkeypatch.setattr(chat_routes, "_plan_context", spy)
    await capture(tmp_path, monkeypatch, team=True, team_first=True)
    assert len(seen) == len(DIALECTS)
    assert all(WORDS in s for s in seen)


# --------------------------------------------------------------------------- #
# the words: only the roles the LISTED members hold
# --------------------------------------------------------------------------- #


def test_the_words_name_only_the_roles_the_listed_members_hold(tmp_path, monkeypatch):
    from iron_jarvis.agents import model_team as MT
    from iron_jarvis.providers import egress

    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    platform = build_platform(str(tmp_path / "home"))
    monkeypatch.setattr(platform.providers, "inherited_from", lambda name: None)
    _register_team(platform)
    platform.agents_registry.register("tester", "t", [], description="runs the tests", provider="codex-cli",
                                      model="gpt-6-astra", roles=["test"])
    text, roles = MT.team_section(platform, team_first=True)
    assert roles == ("read", "code", "review", "test")
    assert text.endswith(
        "\n\nWork as a team: the user wants to save this model's usage. Hand pieces to the team by role: "
        "reading and summarizing files (role:read), running tests and fixing what they find (role:test), "
        "first drafts of separate parts (role:code) and reviewing a change (role:review). " + MT.TEAM_FIRST_TAIL
    )
    # Off: exactly team_block's text, and no roles.
    assert MT.team_section(platform, team_first=False) == (MT.team_block(platform), ())
    assert MT.team_block(platform, team_first=True) == text
    # Under Local only the cloud members are not listed, so neither are their
    # roles (a role nobody listed holds would be refused when asked for).
    with egress.local_only_scope(True):
        text, roles = MT.team_section(platform, team_first=True)
    assert roles == ("read", "code")
    last = text.split("\n\n")[-1]
    assert "role:review" not in last and "role:test" not in last
    assert "(role:read)" in last and "(role:code)" in last
    # No team at all: nothing, on or off.
    empty = build_platform(str(tmp_path / "empty"))
    assert MT.team_section(empty, team_first=True) == ("", ())


def test_the_tool_sentence_uses_each_tools_own_argument():
    from iron_jarvis.agents import model_team as MT

    def schema(*keys):
        return {"type": "object", "properties": {k: {"type": "string"} for k in keys}}

    assert MT.team_first_tool_sentence(schema("description", "prompt", "subagent_type"), ["code"]) == (
        "Your model team is listed under 'Your model team': for a piece that fits a role, "
        "give subagent_type role:code.")
    assert MT.team_first_tool_sentence(schema("message", "agent_type"), ["read", "test"]) == (
        "Your model team is listed under 'Your model team': for a piece that fits a role, "
        "give agent_type role:read or role:test.")
    # Jarvis's own delegate takes BOTH agent_type and role: role wins.
    assert MT.team_first_tool_sentence(schema("agent_type", "task", "role"), ["review", "read"]) == (
        "Your model team is listed under 'Your model team': for a piece that fits a role, "
        "set role to read or review.")
    assert MT.team_first_tool_sentence(schema("task"), ["read"]) == ""
    assert MT.team_first_tool_sentence(schema("role"), []) == ""


def test_a_turn_with_roles_but_no_team_shown_leads_no_tool(tmp_path, monkeypatch):
    """The tool sentence points at the team's list: a turn that holds roles
    but shows no team section (a caller's slip) changes no description."""
    from iron_jarvis.harness import mode

    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    platform = build_platform(str(tmp_path / "home"))
    turn = mode.build(registry=platform.registry, permissions=platform.permissions, requested="claude",
                      provider=WHO[0], model=WHO[1], stream=True, today="")
    plain = turn.specs(platform.registry)
    turn.team_first = ("read", "code")
    assert turn.specs(platform.registry) == plain
    turn.team_section = "# Your model team\n- custom:glm"
    led = {s["name"]: s["description"] for s in turn.specs(platform.registry)}
    assert led["Agent"].startswith("Your model team is listed under 'Your model team': for a piece that fits "
                                   "a role, give subagent_type role:read or role:code. Starts a helper agent")


def test_the_setting_is_read_live_and_only_a_real_true_counts():
    from types import SimpleNamespace

    from iron_jarvis.agents import model_team as MT

    assert MT.team_first_on(SimpleNamespace(config=SimpleNamespace(team_first=True))) is True
    for value in (False, None, "true", 1):
        assert MT.team_first_on(SimpleNamespace(config=SimpleNamespace(team_first=value))) is False
    assert MT.team_first_on(SimpleNamespace()) is False
