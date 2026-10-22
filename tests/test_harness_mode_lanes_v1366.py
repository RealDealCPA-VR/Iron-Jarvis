"""v1.366.0 (IJ harness Phase 3): HARNESS MODE on both chat lanes.

``harness=`` is one keyword-only switch on ``run_chat_turn`` (the POST lane)
and ``stream_chat_turn`` / ``chat_stream`` (the stream lane). These pins drive
the REAL lanes on a real platform; only the model (the router) is a double —
and, where a test must not start a process, a tool's own ``execute``. The
registry, the permission engine and the ledger are the real ones, because
"a translated call can never reach a tool that is not armed" is a claim about
the registry's gate, not about a stub (the v1.270.1 lesson).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.chat_stream import stream_chat_turn
from iron_jarvis.daemon.chat_turn import run_chat_turn
from iron_jarvis.daemon.routes import chat as chat_routes
from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
from iron_jarvis.harness import mode as M
from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.base import ToolResult

CLAUDE = ("anthropic", "claude-sonnet-4-5")
GPT = ("openai", "gpt-5.1")


def _frames(chunks: list[str]) -> list[tuple[str, dict]]:
    out = []
    for chunk in chunks:
        for block in chunk.split("\n\n"):
            event, data = "", []
            for line in block.splitlines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].strip())
            if event:
                out.append((event, json.loads("".join(data)) if data else {}))
    return out


class Model:
    """A scripted model on the router's ``stream()`` and ``complete()``. Each
    move is a list of ToolCalls or a reply text; past the script it answers
    "Done." Every call's system prompt, tools and messages are recorded."""

    def __init__(self, provider: str, model: str, moves: list) -> None:
        self.provider, self.model = provider, model
        self.moves = list(moves)
        self.calls: list[dict] = []

    def _next(self, system, messages, tools) -> LLMResponse:
        self.calls.append({"system": system, "tools": [t["name"] for t in tools or []],
                           "specs": list(tools or []), "messages": list(messages)})
        move = self.moves.pop(0) if (self.moves and tools) else "Done."
        if isinstance(move, str):
            return LLMResponse(text=move, usage={"input_tokens": 3, "output_tokens": 1})
        return LLMResponse(text="", tool_calls=list(move), usage={"input_tokens": 3, "output_tokens": 1})

    async def stream(self, *, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        resp = self._next(system, messages, tools)
        if resp.text:
            yield {"type": "text", "text": resp.text}
        yield {"type": "final", "response": resp, "provider": self.provider, "model": self.model}

    async def complete(self, *, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        return RouteResult(self._next(system, messages, tools), self.provider, self.model)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    platform = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.py").write_text("x = 1\n", encoding="utf-8")

    def wire(moves, who=CLAUDE):
        model = Model(*who, moves)
        monkeypatch.setattr(platform.router, "stream", model.stream)
        monkeypatch.setattr(platform.router, "complete", model.complete)
        return model

    return platform, ws, wire


def _body(ws: Path, who=CLAUDE, **kw) -> ChatBody:
    return ChatBody(
        messages=[ChatMessageBody(role="user", content=kw.pop("text", "fix the bug in a.py"))],
        provider=who[0], model=who[1], workspace_dir=str(ws), approval_mode=kw.pop("approval_mode", "yolo"),
        **kw,
    )


async def _stream(platform, body, **kw) -> dict:
    gen = await stream_chat_turn(platform, BUILTIN_PERSONAS, body, **kw)
    frames = _frames([c async for c in gen])
    done = [d for e, d in frames if e == "done"]
    assert done, frames[-4:]
    return {"done": done[0], "frames": frames}


async def _post(platform, body, **kw) -> dict:
    return await run_chat_turn(platform, BUILTIN_PERSONAS, body, **kw)


def _ledger(platform) -> list[ToolInvocation]:
    with session_scope(platform.engine) as db:
        return list(db.exec(select(ToolInvocation).order_by(ToolInvocation.created_at)).all())


def _tool_messages(call: dict) -> list:
    return [m for m in call["messages"] if m.role == "tool"]


# --------------------------------------------------------------------------- #
# Off: byte-identical
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_off_is_byte_identical_whichever_way_it_is_said(env, lane):
    platform, ws, wire = env
    seen = []
    for kw in ({}, {"harness": None}, {"harness": False}):
        model = wire([[ToolCall(id="c1", name="read_file", arguments={"path": "a.py"})]])
        body = _body(ws, tools=["read_file"])
        if lane == "stream":
            out = (await _stream(platform, body, **kw))["done"]
        else:
            out = await _post(platform, body, **kw)
        route = out["route"]
        assert "harness" not in route and list(route)[-1] == "on_this_network"
        seen.append(([(c["system"], c["tools"]) for c in model.calls], out["reply"], out["tools_used"], route))
    assert seen[0] == seen[1] == seen[2]
    system, tools = seen[0][0][0]
    assert "# IJ harness" not in system and chat_turn._ENV_EXIT_LINES in system
    assert "escalate_to_agent" in tools and "read_file" in tools


def test_no_http_field_carries_the_switch():
    assert "harness" not in ChatBody.model_fields
    body = ChatBody.model_validate({"messages": [{"role": "user", "content": "hi"}], "harness": True})
    assert not hasattr(body, "harness")


@pytest.mark.parametrize("bad", ["bogus", 1, "claude-code", [], 0.5])
async def test_a_bad_value_is_refused_before_anything_runs(env, bad):
    platform, ws, wire = env
    model = wire([])
    with pytest.raises(ValueError, match="harness must be"):
        await stream_chat_turn(platform, BUILTIN_PERSONAS, _body(ws), harness=bad)
    with pytest.raises(ValueError, match="harness must be"):
        await _post(platform, _body(ws), harness=bad)
    assert model.calls == []


# --------------------------------------------------------------------------- #
# On: the coding set, the dialect, the tree before the planner, no exits
# --------------------------------------------------------------------------- #


async def test_the_stream_lane_arms_the_coding_set_in_claudes_dialect_past_the_cap(env):
    platform, ws, wire = env
    model = wire([])
    out = await _stream(platform, _body(ws), harness=True)
    tools = model.calls[0]["tools"]
    assert tools == [
        "Read", "Write", "Edit", "MultiEdit", "Glob", "Grep", "LS", "Bash", "BashOutput", "TaskStop",
        "TodoWrite", "NotebookEdit", "WebFetch", "WebSearch", "apply_patch", "process_write",
    ]
    assert len(tools) > chat_turn._MAX_ARMED_TOOLS
    assert "escalate_to_agent" not in tools and "workflow_draft" not in tools
    # The chat's own "# Tools" block names the REAL tools; a harness turn's
    # guidance is its tree, in the dialect's names.
    system = model.calls[0]["system"]
    assert "\n\n# Tools\n" not in system and "## Tools\nYour tools use Claude Code's names" in system
    harness = out["done"]["route"]["harness"]
    assert list(out["done"]["route"])[-1] == "harness"
    assert harness["family"] == "claude" and harness["dialect"] == "claude"
    assert re.fullmatch(r"[0-9a-f]{64}", harness["fingerprint"])
    assert harness["layers"] == ["default", "family:claude"] and harness["nudged"] is False


async def test_the_post_lane_arms_no_ask_tier_tool(env):
    platform, ws, wire = env
    model = wire([])
    out = await _post(platform, _body(ws), harness=True)
    tools = model.calls[0]["tools"]
    assert "Read" in tools and len(tools) > chat_turn._MAX_ARMED_TOOLS
    # shell / process_start / process_write ask first, and this lane can ask
    # nobody: not armed, so Bash is not offered at all.
    assert "Bash" not in tools and "process_write" not in tools
    assert out["route"]["harness"]["dialect"] == "claude"
    assert list(out["route"])[-1] == "harness"


async def test_generic_shows_jarvis_own_names_and_gpt_shows_codexs(env):
    platform, ws, wire = env
    model = wire([])
    await _stream(platform, _body(ws), harness="generic")
    assert {"read_file", "edit_file", "shell", "process_start", "glob"} <= set(model.calls[0]["tools"])
    # `_store_as` needs the repl, which a harness turn does not arm.
    for spec in model.calls[0]["specs"]:
        assert "_store_as" not in spec["input_schema"].get("properties", {}), spec["name"]
    model = wire([], who=GPT)
    out = await _stream(platform, _body(ws, who=GPT), harness=True)
    tools = model.calls[0]["tools"]
    assert {"exec_command", "write_stdin", "apply_patch", "update_plan", "read_file"} <= set(tools)
    assert "shell" not in tools and "edit_file" not in tools
    assert out["done"]["route"]["harness"]["dialect"] == "gpt"


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_the_tree_is_in_the_prompt_before_the_planner_runs(env, monkeypatch, lane):
    platform, ws, wire = env
    wire([])
    planned: list[str] = []
    real = chat_turn._plan_context

    def spy(d, body, system, *a, **kw):
        planned.append(system)
        return real(d, body, system, *a, **kw)

    monkeypatch.setattr(chat_turn, "_plan_context", spy)
    monkeypatch.setattr(chat_routes, "_plan_context", spy)
    body = _body(ws)
    if lane == "stream":
        await _stream(platform, body, harness=True)
    else:
        await _post(platform, body, harness=True)
    assert planned and "# IJ harness" in planned[0]
    assert "## Guardrails" in planned[0] and "taskkill /im" in planned[0]
    assert "Today's date is" in planned[0]


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_harness_turn_hands_work_to_nobody(env, lane):
    platform, ws, wire = env
    model = wire([])
    body = _body(ws)
    if lane == "stream":
        await _stream(platform, body)
        await _stream(platform, _body(ws), harness=True)
    else:
        await _post(platform, body)
        await _post(platform, _body(ws), harness=True)
    off, on = model.calls[0]["system"], model.calls[-1]["system"]
    assert "# Who can take this work" in off and chat_turn._ENV_EXIT_LINES in off
    assert "# Who can take this work" not in on
    assert chat_turn._ENV_EXIT_LINES not in on and M.HARNESS_EXIT_LINES in on


# --------------------------------------------------------------------------- #
# Translation at the boundary: the registry sees the REAL tool
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_dialect_call_runs_the_real_tool_and_the_ledger_names_both(env, lane):
    platform, ws, wire = env
    model = wire([
        [ToolCall(id="r1", name="Read", arguments={"file_path": "a.py"})],
        [ToolCall(id="e1", name="Edit", arguments={"file_path": "a.py", "old_string": "x = 1", "new_string": "x = 2"})],
    ])
    body = _body(ws)
    out = (await _stream(platform, body, harness=True))["done"] if lane == "stream" else await _post(
        platform, body, harness=True)
    assert (ws / "a.py").read_text(encoding="utf-8") == "x = 2\n"
    assert out["tools_used"] == ["read_file", "edit_file"]
    rows = [(r.tool, r.called_as, r.ok) for r in _ledger(platform)]
    assert rows == [("read_file", "Read", True), ("edit_file", "Edit", True)]
    # The model is shown its own names back: its assistant turn and the
    # results carry "Read" / "Edit", never the real names.
    third = model.calls[2]
    assistant = [m for m in third["messages"] if m.role == "assistant" and m.tool_calls]
    assert [c.name for m in assistant for c in m.tool_calls] == ["Read", "Edit"]
    assert [m.name for m in _tool_messages(third)] == ["Read", "Edit"]
    assert "1\tx = 1" in _tool_messages(third)[0].content  # cat -n style, numbered


async def test_a_call_by_the_real_name_is_recorded_with_no_called_as(env):
    platform, ws, wire = env
    wire([[ToolCall(id="r1", name="read_file", arguments={"path": "a.py"})]])
    await _stream(platform, _body(ws), harness=True)
    assert [(r.tool, r.called_as) for r in _ledger(platform)] == [("read_file", "")]


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_translated_call_can_never_reach_a_tool_that_is_not_armed(env, lane):
    """THE GATE. Bash translates to ``shell``; when ``shell`` is not armed (the
    POST lane never arms an ask-tier tool; the stream lane's ceiling leaves it
    out here) the REAL registry refuses it as "not armed", ledgered, and the
    command never runs."""
    platform, ws, wire = env
    model = wire([[ToolCall(id="b1", name="Bash", arguments={"command": "echo hi> made.txt"})]])
    body = _body(ws)
    if lane == "stream":
        ceiling = frozenset(M.CODING_TOOLS) - {"shell", "process_start", "process_write"}
        out = (await _stream(platform, body, harness=True, tool_ceiling=ceiling))["done"]
        assert "Bash" not in model.calls[0]["tools"]
    else:
        out = await _post(platform, body, harness=True)
    assert not (ws / "made.txt").exists()
    assert out["tools_used"] == []
    rows = _ledger(platform)
    assert [(r.tool, r.called_as, r.ok) for r in rows] == [("shell", "Bash", False)]
    assert "not one of this agent's tools" in rows[0].output
    msg = _tool_messages(model.calls[1])[0]
    assert msg.name == "Bash" and "not armed" in msg.content


async def test_a_hidden_real_name_is_not_armed_either(env):
    """Codex's dialect reaches ``process_start`` through exec_command, so
    ``shell`` is not armed at all: the roster is the contract (v1.227.0)."""
    platform, ws, wire = env
    wire([[ToolCall(id="s1", name="shell", arguments={"command": "echo hi> made.txt"})]], who=GPT)
    await _stream(platform, _body(ws, who=GPT), harness=True)
    assert not (ws / "made.txt").exists()
    assert [(r.tool, r.ok) for r in _ledger(platform)] == [("shell", False)]


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_repeated_failing_call_is_answered_under_the_name_the_model_used(env, lane):
    """The third identical failing call is answered, not run (v1.274.0) — and
    that answer, too, goes back under the dialect's name."""
    platform, ws, wire = env
    missing = [ToolCall(id=f"m{i}", name="Read", arguments={"file_path": "missing.py"}) for i in range(3)]
    model = wire([[missing[0]], [missing[1]], [missing[2]]])
    body = _body(ws)
    if lane == "stream":
        await _stream(platform, body, harness=True)
    else:
        await _post(platform, body, harness=True)
    last = _tool_messages(model.calls[3])
    assert [m.name for m in last] == ["Read", "Read", "Read"]
    assert chat_turn.REPEATED_CALL_LIMIT == 2
    assert "failed" in last[-1].content.lower() and len(_ledger(platform)) == 2  # the third never ran


async def test_a_missing_argument_is_explained_in_the_dialects_words(env):
    platform, ws, wire = env
    model = wire([[ToolCall(id="r1", name="Read", arguments={})]])
    await _stream(platform, _body(ws), harness=True)
    msg = _tool_messages(model.calls[1])[0]
    assert msg.name == "Read" and msg.content.startswith("missing required")
    assert "You called Read; it needs file_path." in msg.content


# --------------------------------------------------------------------------- #
# Never escalates
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_hallucinated_hand_off_is_refused_and_the_turn_stays(env, lane):
    platform, ws, wire = env
    wire([[ToolCall(id="x1", name="escalate_to_agent", arguments={"reason": "big job"})]])
    body = _body(ws)
    out = (await _stream(platform, body, harness=True))["done"] if lane == "stream" else await _post(
        platform, body, harness=True)
    assert out["escalate"] is False
    assert [(r.tool, r.ok) for r in _ledger(platform)] == [("escalate_to_agent", False)]


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_at_its_last_round_a_harness_turn_ends_in_chat(env, lane):
    platform, ws, wire = env
    model = wire([[ToolCall(id=f"g{i}", name="Glob", arguments={"pattern": "*.py"})] for i in range(10)])
    body = _body(ws)
    out = (await _stream(platform, body, harness=True, round_budget=3))["done"] if lane == "stream" else await _post(
        platform, body, harness=True, round_budget=3)
    assert out["escalate"] is False
    assert [bool(c["tools"]) for c in model.calls] == [True, True, True, False]


def test_the_knobs_a_harness_turn_takes():
    assert chat_turn.harness_turn_knobs(None, None, False) == (None, False)
    assert chat_turn.harness_turn_knobs(None, 9, False) == (9, False)
    assert chat_turn.harness_turn_knobs("auto", None, False) == (M.HARNESS_DEFAULT_ROUNDS, True)
    assert chat_turn.harness_turn_knobs("generic", 7, False) == (7, True)


# --------------------------------------------------------------------------- #
# A tool call written as text: one nudge per turn
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_text_tool_call_is_never_run_and_gets_one_nudge(env, lane):
    platform, ws, wire = env
    model = wire([
        "<tool_call>list_filespath</tool_call>",
        [ToolCall(id="l1", name="LS", arguments={})],
        "<tool_call>Read<arg_key>file_path</arg_key></tool_call>",
    ])
    body = _body(ws)
    if lane == "stream":
        got = await _stream(platform, body, harness=True)
        out = got["done"]
        resets = [d for e, d in got["frames"] if e == "reset"]
        assert resets == [{"reason": "text_tool_call"}]
    else:
        out = await _post(platform, body, harness=True)
    nudges = [m for m in model.calls[-1]["messages"] if m.role == "user" and m.content == M.TEXT_TOOL_CALL_NUDGE]
    assert len(nudges) == 1, "bounded: once per turn"
    assert len(model.calls) == 3
    # The nudge came right after the text call, and the model then called LS.
    second = model.calls[1]["messages"]
    assert second[-1].content == M.TEXT_TOOL_CALL_NUDGE and second[-2].content.startswith("<tool_call>")
    assert [(r.tool, r.called_as) for r in _ledger(platform)] == [("list_files", "LS")]
    assert out["route"]["harness"]["nudged"] is True


async def test_off_a_text_tool_call_ends_the_turn_as_before(env):
    platform, ws, wire = env
    model = wire(["<tool_call>list_filespath</tool_call>"], who=("scripted", "s"))
    out = (await _stream(platform, _body(ws, who=("scripted", "s"), tools=["read_file"])))["done"]
    assert len(model.calls) == 1 and "harness" not in out["route"]


@pytest.mark.parametrize("text, kind", [
    ("<tool_call>list_filespath</tool_call>", "tag"),
    ('<tool_call>\n{"name": "Read", "arguments": {"file_path": "a"}}\n</tool_call>', "tag"),
    ('<function_calls><invoke name="Read"></invoke></function_calls>', "tag"),
    ('<invoke name="Bash">', "invoke"),
    ('I will call {"name": "Read", "arguments": {"file_path": "a.py"}} now', "json"),
    ('{"name": "weather", "arguments": {}}', ""),
    ("The fix is done; tests pass.", ""),
    ('config = {"name": "app", "version": 2}', ""),
])
def test_what_counts_as_a_tool_call_written_as_text(text, kind):
    assert M.text_tool_call(text, {"Read", "Bash", "read_file"}) == kind


# --------------------------------------------------------------------------- #
# Plugins: rules narrow, checks run through the registry's gate
# --------------------------------------------------------------------------- #


def _write_plugins(ws: Path, text: str) -> None:
    hd = ws / ".ironjarvis" / "harness"
    hd.mkdir(parents=True, exist_ok=True)
    (hd / "plugins.toml").write_text(text, encoding="utf-8")


async def test_a_deny_rule_removes_the_tool_and_narrows_the_overrides(env):
    platform, ws, wire = env
    _write_plugins(ws, '[[rules]]\ntool = "web_fetch"\naction = "deny"\n')
    model = wire([[ToolCall(id="w1", name="web_fetch", arguments={"url": "https://example.com"})]])
    out = (await _stream(platform, _body(ws), harness=True))["done"]
    assert "WebFetch" not in model.calls[0]["tools"]
    assert out["route"]["harness"]["layers"] == ["default", "family:claude", "project"]
    rows = _ledger(platform)
    assert [(r.tool, r.ok) for r in rows] == [("web_fetch", False)]
    assert "- web_fetch: is not available in this folder." in model.calls[0]["system"]


async def test_an_ask_rule_moves_a_tool_to_the_ask_tier(env):
    platform, ws, wire = env
    _write_plugins(ws, '[[rules]]\ntool = "write_file"\naction = "ask"\n')
    model = wire([])
    await _post(platform, _body(ws), harness=True)  # the POST lane arms no ask-tier tool
    assert "Write" not in model.calls[0]["tools"] and "Edit" in model.calls[0]["tools"]


async def _stream_answering(platform, body, answer: str, **kw) -> list[tuple[str, dict]]:
    from types import SimpleNamespace

    approvals = chat_routes._approvals(SimpleNamespace(platform=platform))
    gen = await stream_chat_turn(platform, BUILTIN_PERSONAS, body, **kw)
    frames = []
    async for chunk in gen:
        for event, data in _frames([chunk]):
            frames.append((event, data))
            if event == "approval":
                assert approvals.resolve(str(data["id"]), answer)
    return frames


@pytest.mark.parametrize("rule", [True, False])
async def test_an_ask_rule_puts_the_card_up_for_an_allow_tier_tool(env, rule):
    """The rule reaches the card through the EXISTING overrides: write_file is
    allow-tier, so only the folder's ``ask`` makes the stream lane ask."""
    platform, ws, wire = env
    if rule:
        _write_plugins(ws, '[[rules]]\ntool = "write_file"\naction = "ask"\n')
    wire([[ToolCall(id="w1", name="Write", arguments={"file_path": "new.txt", "content": "hi"})]])
    frames = await _stream_answering(platform, _body(ws, approval_mode="approve_for_me"), "deny", harness=True)
    cards = [d for e, d in frames if e == "approval"]
    if rule:
        assert [c["tool"] for c in cards] == ["write_file"]
        assert not (ws / "new.txt").exists()
        assert [(r.tool, r.called_as, r.ok) for r in _ledger(platform)] == [("write_file", "Write", False)]
    else:
        assert cards == [] and (ws / "new.txt").read_text(encoding="utf-8") == "hi"


@pytest.fixture()
def fake_shell(env, monkeypatch):
    platform, _ws, _wire = env
    ran: list[dict] = []

    async def execute(args, ctx):
        ran.append(dict(args))
        return ToolResult(ok=True, output="checked fine")

    monkeypatch.setattr(platform.registry.get("shell"), "execute", execute)
    return ran


_CHECK = '[[checks]]\nfiles = ["*.py"]\ncommand = "echo checked"\ntimeout_s = 20\n'
_EDIT = [ToolCall(id="e1", name="Edit", arguments={"file_path": "a.py", "old_string": "x = 1", "new_string": "x = 2"})]


async def test_a_check_never_runs_on_a_one_call_grant(env, fake_shell):
    """Auto-approve grants the MODEL's calls one at a time; a check is the
    folder's own command, so it runs only where the shell is already allowed
    beyond one call — here it is refused through the registry and the model
    is told to run it itself."""
    platform, ws, wire = env
    _write_plugins(ws, _CHECK)
    model = wire([_EDIT])
    await _stream(platform, _body(ws, approval_mode="yolo"), harness=True)
    assert fake_shell == []
    msg = _tool_messages(model.calls[1])[0]
    assert "Check not run (running it needs the user's approval)" in msg.content
    rows = [(r.tool, r.called_as, r.ok) for r in _ledger(platform)]
    assert ("shell", "plugin check", False) in rows


async def test_a_check_runs_through_the_registry_once_the_shell_is_allowed(env, fake_shell):
    platform, ws, wire = env
    _write_plugins(ws, _CHECK)
    model = wire([_EDIT])
    await _stream(platform, _body(ws, granted_tools=["shell"]), harness=True)
    assert fake_shell == [{"command": "echo checked", "timeout_s": 20}]
    msg = _tool_messages(model.calls[1])[0]
    assert "Check `echo checked` (passed):" in msg.content and "checked fine" in msg.content
    assert ("shell", "plugin check", True) in [(r.tool, r.called_as, r.ok) for r in _ledger(platform)]


async def test_a_check_never_runs_in_low_trust(env, fake_shell):
    platform, ws, wire = env
    _write_plugins(ws, _CHECK)
    model = wire([_EDIT])
    await _stream(platform, _body(ws, granted_tools=["shell"]), harness=True, trust="low")
    assert fake_shell == []
    assert "Check not run (this turn is in low trust)" in _tool_messages(model.calls[1])[0].content


async def test_a_check_only_follows_matching_files(env, fake_shell):
    platform, ws, wire = env
    _write_plugins(ws, '[[checks]]\nfiles = ["*.md"]\ncommand = "echo checked"\n')
    model = wire([_EDIT])
    await _stream(platform, _body(ws, granted_tools=["shell"]), harness=True)
    content = _tool_messages(model.calls[1])[0].content
    assert fake_shell == [] and "echo checked" not in content and "Check not run" not in content


async def test_an_invalid_layer_is_ignored_with_one_note_on_the_turn(env):
    platform, ws, wire = env
    _write_plugins(ws, '[[rules]]\ntool = "shell"\naction = "allow"\n')
    (ws / ".ironjarvis" / "harness" / "system_prompt.md").write_text("PROJECT PROMPT", encoding="utf-8")
    model = wire([])
    out = (await _stream(platform, _body(ws), harness=True))["done"]
    harness = out["route"]["harness"]
    assert harness["layers"] == ["default", "family:claude"]
    assert harness["note"].startswith("This folder's harness files (.ironjarvis/harness) were not used:")
    assert "PROJECT PROMPT" not in model.calls[0]["system"]


# --------------------------------------------------------------------------- #
# Memory: the folder's instruction files ride once
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_folder_turns_agents_md_rides_once_and_is_referred_to(env, lane):
    platform, ws, wire = env
    (ws / "AGENTS.md").write_text("Always run the tests with -q.", encoding="utf-8")
    model = wire([])
    body = _body(ws)
    out = (await _stream(platform, body, harness=True))["done"] if lane == "stream" else await _post(
        platform, body, harness=True)
    system = model.calls[0]["system"]
    assert system.count("Always run the tests with -q.") == 1
    assert system.count("# Project folder instructions (AGENTS.md)") == 1
    assert "This folder's own instruction files (AGENTS.md)" in system
    assert out["folder_rules"] == ["AGENTS.md"]


@pytest.mark.parametrize("lane", ["stream", "post"])
@pytest.mark.parametrize("bound", [False, True])
async def test_a_project_turns_rule_files_are_not_injected_twice(env, lane, bound):
    """The grounding already carries a PROJECT's AGENTS.md; the harness only
    refers to it (whether or not the turn is also bound to that folder)."""
    from iron_jarvis.core.models import Project

    platform, ws, wire = env
    (ws / "AGENTS.md").write_text("Always run the tests with -q.", encoding="utf-8")
    with session_scope(platform.engine) as db:
        project = Project(name="Ledger", root=str(ws))
        db.add(project)
        db.commit()
        pid = project.id
    model = wire([])
    body = ChatBody(
        messages=[ChatMessageBody(role="user", content="fix it")], provider=CLAUDE[0], model=CLAUDE[1],
        project_id=pid, approval_mode="yolo", **({"workspace_dir": str(ws)} if bound else {}),
    )
    out = (await _stream(platform, body, harness=True))["done"] if lane == "stream" else await _post(
        platform, body, harness=True)
    system = model.calls[0]["system"]
    assert system.count("Always run the tests with -q.") == 1
    assert "This folder's own instruction files (AGENTS.md)" in system
    assert out["folder_rules"] == ["AGENTS.md"]


# --------------------------------------------------------------------------- #
# Low trust and Local only reach the plan
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_text_only_model_gets_the_tree_and_no_tools(env, monkeypatch, lane):
    """v1.330.0 still holds inside harness mode: a picked model that cannot
    use tools is offered none, and the tree says so instead of naming tools."""
    platform, ws, wire = env
    monkeypatch.setattr(chat_turn, "is_text_only_pick", lambda *a, **k: True)
    model = wire([])
    body = _body(ws)
    out = (await _stream(platform, body, harness=True))["done"] if lane == "stream" else await _post(
        platform, body, harness=True)
    assert model.calls[0]["tools"] == []
    assert "You have no tools in this turn: answer in text." in model.calls[0]["system"]
    assert out["route"]["text_only"] is True and out["route"]["harness"]["dialect"] == "claude"


async def test_low_trust_keeps_programs_away(env):
    platform, ws, wire = env
    model = wire([])
    await _stream(platform, _body(ws), harness="generic", trust="low")
    tools = model.calls[0]["tools"]
    assert "process_start" not in tools and "process_write" not in tools and "shell" in tools


async def test_local_only_drops_the_web_tools(env):
    from iron_jarvis.providers import egress

    platform, ws, _wire = env
    d = type("D", (), {"platform": platform})()
    grounding = chat_turn._Grounding()
    with egress.local_only_scope(True, False):
        turn, _rules = await chat_turn._build_harness(
            d, _body(ws), "auto", provider="anthropic", model="claude-x", resolved_proj=None,
            grounding=grounding, stream=True, low_trust=False,
        )
    assert "WebFetch" not in turn.plan.offered_names and "WebSearch" not in turn.plan.offered_names
    assert "web_fetch" not in turn.plan.gate
