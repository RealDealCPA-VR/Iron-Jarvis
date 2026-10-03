"""v1.300.0 — the Claude-subscription adapter speaks the CLI NATIVELY.

Rebuilt on NousResearch's MIT-licensed claude-subscription-directsdk design
(commit ef73726). The old ``ClaudeCliAdapter`` flattened the conversation into
one prompt, emulated tools with a ``--json-schema`` "reply or ONE tool_call"
step, never streamed, refused images, sent ``--model`` as a bare alias and
reported only two token counts. These pins drive the REAL adapter and its
REAL transport against a fake ``claude`` (``tests/fixtures/
fake_claude_v1300.py``) started through a shim, exactly as an npm install
starts the real one; the fake records argv, env, cwd, the frames and the
files argv points at. Pure halves (frames, names, schemas) are pinned
directly. Nothing here reaches the network: the one relay test points the
admission relay at a loopback fake upstream.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import stat
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil
import pytest

from iron_jarvis.providers import budget, cli_auth
from iron_jarvis.providers.adapters import subprocess_cli as sc
from iron_jarvis.providers.adapters.anthropic import AnthropicAdapter
from iron_jarvis.providers.adapters.base import LLMMessage, ProviderError, ToolCall
from iron_jarvis.providers.adapters.claude_native import CARRIER
from iron_jarvis.providers.adapters.claude_native import frames as F
from iron_jarvis.providers.adapters.claude_native import inert_mcp, transport
from iron_jarvis.providers.cli_auth import SIGN_IN_FIX, CliAuthProbe

FAKE = Path(__file__).parent / "fixtures" / "fake_claude_v1300.py"
ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """The inherited environment is the stripping's input: start clean (and
    re-arm the once-per-process log), and give every test its own sign-in
    probe so one refusal never colours the next."""
    for key in [k for k in os.environ if k.startswith("ANTHROPIC_")] + list(transport.BACKEND_SWITCHES):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(transport, "_STRIP_LOGGED", False)
    monkeypatch.setattr(cli_auth, "DEFAULT_PROBE", CliAuthProbe(which=lambda b: None))
    monkeypatch.setattr(transport, "DEFAULT_PROBE", cli_auth.DEFAULT_PROBE)


def _shim(tmp_path: Path) -> str:
    """shim -> python fake: the process TREE an npm ``claude.cmd`` really has."""
    if os.name == "nt":
        shim = tmp_path / "claude.cmd"
        shim.write_text(f'@"{sys.executable}" "{FAKE}" %*\r\n', encoding="utf-8")
    else:
        shim = tmp_path / "claude"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n', encoding="utf-8")
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    return str(shim)


class Fake:
    def __init__(self, tmp_path: Path, monkeypatch, scenario: str = "text", **knobs: str) -> None:
        self.tmp = tmp_path
        self.shim = _shim(tmp_path)
        self.record_path = tmp_path / "record.json"
        monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", scenario)
        monkeypatch.setenv("FAKE_CLAUDE_RECORD", str(self.record_path))
        for key, value in knobs.items():
            monkeypatch.setenv(f"FAKE_CLAUDE_{key.upper()}", value)

    def adapter(self, **kw) -> sc.ClaudeCliAdapter:
        kw.setdefault("model", "claude-sonnet-5")
        return sc.make_claude_cli(which=lambda _b: self.shim, **kw)

    @property
    def record(self) -> dict:
        return json.loads(self.record_path.read_text(encoding="utf-8"))


TOOLS = [
    {"name": "read_file", "description": "Read a file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "custom:x", "description": "A custom tool.", "input_schema": {"type": "object"}},
    {"name": "mcp__a_very_long_server_name_for_tests__a_very_long_tool_name_too", "description": "Pack.",
     "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}}},
]


def _user(text: str = "hi", **kw) -> LLMMessage:
    return LLMMessage(role="user", content=text, **kw)


async def _drain(agen) -> list[dict]:
    return [frame async for frame in agen]


# --------------------------------------------------------------------------- #
# 1. history -> frames (pure)
# --------------------------------------------------------------------------- #
def test_history_maps_roles_merges_users_and_carries_images():
    names = F.ToolNames.build(["read_file"])
    msgs = [
        _user("first"),
        _user("and an image", images=[{"data_b64": "QUJD", "media_type": "image/png"}]),
        LLMMessage(role="assistant", content="Reading.",
                   tool_calls=[ToolCall(id="call_1", name="read_file", arguments={"path": "a.txt"})]),
        LLMMessage(role="tool", content="file body", tool_call_id="call_1", name="read_file"),
        _user("steer: also b.txt"),
    ]
    system, frames = F.history_frames("be brief", msgs, names)
    assert system == "be brief"
    assert [f["type"] for f in frames] == ["user", "assistant", "user"]
    first = frames[0]["message"]["content"]
    assert first[0] == {"type": "text", "text": "first"}
    assert first[1] == {"type": "text", "text": "and an image"}
    assert first[2] == {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}}
    assistant = frames[1]["message"]["content"]
    assert assistant == [
        {"type": "text", "text": "Reading."},
        {"type": "tool_use", "id": "call_1", "name": "mcp__ij__read_file", "input": {"path": "a.txt"}},
    ]
    # The tool result and the steer note are ONE user turn (consecutive merge).
    assert frames[2]["message"]["content"] == [
        {"type": "tool_result", "tool_use_id": "call_1", "content": "file body"},
        {"type": "text", "text": "steer: also b.txt"},
    ]


def test_history_must_end_in_a_user_turn_and_never_sends_an_empty_assistant():
    names = F.ToolNames.build([])
    with pytest.raises(F.HistoryError, match="must end"):
        F.history_frames("", [_user("q"), LLMMessage(role="assistant", content="prefill")], names)
    with pytest.raises(F.HistoryError):
        F.history_frames("", [], names)
    _, frames = F.history_frames("", [_user("a"), LLMMessage(role="assistant", content="  "), _user("b")], names)
    assert len(frames) == 1 and len(frames[0]["message"]["content"]) == 2


def _carrier_turn(text="Writing.", args=None):
    args = args or {"path": "x"}
    native = [{"role": "assistant", "id": "msg_1", "content": [
        {"type": "thinking", "thinking": "private", "signature": "opaque"},
        {"type": "text", "text": text},
        {"type": "tool_use", "id": "toolu_1", "name": "mcp__ij__write_file", "input": args},
    ]}]
    calls = [ToolCall(id="toolu_1", name="write_file", arguments=dict(args))]
    raw = F.carrier(native, text + "\n", [{"id": c.id, "name": c.name, "input": c.arguments} for c in calls])
    return LLMMessage(role="assistant", content=text + "\n", tool_calls=calls, raw_blocks=[raw]), native


def test_unchanged_carrier_replays_native_blocks_verbatim():
    names = F.ToolNames.build(["write_file"])
    turn, native = _carrier_turn()
    _, frames = F.history_frames("", [_user("write"), turn,
                                      LLMMessage(role="tool", content="ok", tool_call_id="toolu_1")], names)
    assert frames[1] == {"type": "assistant", "message": native[0]}
    assert frames[1]["message"]["content"][0]["signature"] == "opaque"
    # Harmless surrounding whitespace is not an edit.
    turn.content = "  Writing.  "
    _, again = F.history_frames("", [_user("write"), turn,
                                     LLMMessage(role="tool", content="ok", tool_call_id="toolu_1")], names)
    assert again[1]["message"] == native[0]


@pytest.mark.parametrize("edit", ["text", "args", "dropped_call"])
def test_an_edited_turn_is_rebuilt_and_never_carries_a_stale_signature(edit):
    names = F.ToolNames.build(["write_file"])
    turn, _ = _carrier_turn()
    if edit == "text":
        turn.content = "Hook replacement"
    elif edit == "args":
        turn.tool_calls[0].arguments = {"path": "x", "content": "a" * 30 + "…[trimmed]"}
    else:
        turn.tool_calls = []
    msgs = [_user("write"), turn] + ([LLMMessage(role="tool", content="ok", tool_call_id="toolu_1")]
                                      if turn.tool_calls else [_user("next")])
    _, frames = F.history_frames("", msgs, names)
    blocks = frames[1]["message"]["content"]
    assert all(b["type"] != "thinking" for b in blocks)
    assert not any("signature" in b for b in blocks)
    if edit == "text":
        assert blocks[0] == {"type": "text", "text": "Hook replacement"}
    if edit == "args":
        assert blocks[-1]["input"] == turn.tool_calls[0].arguments


def test_foreign_and_malformed_carriers_are_ignored():
    names = F.ToolNames.build(["read_file"])
    calls = [ToolCall(id="t1", name="read_file", arguments={"path": "a"})]
    api_blocks = [{"type": "thinking", "thinking": "x", "signature": "api-sig"},
                  {"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "a"}}]
    turn, _ = _carrier_turn()
    two = [turn.raw_blocks[0], copy.deepcopy(turn.raw_blocks[0])]
    v2 = [{**turn.raw_blocks[0], "version": 2}]
    for raw in (api_blocks, two, v2):
        msg = LLMMessage(role="assistant", content="", tool_calls=calls, raw_blocks=raw)
        _, frames = F.history_frames("", [_user(), msg, LLMMessage(role="tool", content="ok", tool_call_id="t1")], names)
        assert frames[1]["message"]["content"] == [
            {"type": "tool_use", "id": "t1", "name": "mcp__ij__read_file", "input": {"path": "a"}}]


def test_rebuilt_tool_ids_are_unique_and_results_follow_their_own_step():
    # The OLD adapter used "cli_0" for every call; replaying two such steps
    # must not hand the API two tool_use blocks with one id.
    names = F.ToolNames.build(["read_file"])
    msgs = [_user("go")]
    for body in ("one", "two"):
        msgs.append(LLMMessage(role="assistant", content="",
                               tool_calls=[ToolCall(id="cli_0", name="read_file", arguments={})]))
        msgs.append(LLMMessage(role="tool", content=body, tool_call_id="cli_0"))
    _, frames = F.history_frames("", msgs, names)
    uses = [f["message"]["content"][0]["id"] for f in frames if f["type"] == "assistant"]
    results = [f["message"]["content"][0]["tool_use_id"] for f in frames[1:] if f["type"] == "user"]
    assert len(set(uses)) == 2 and results == uses


# --------------------------------------------------------------------------- #
# 2. tool names (pure)
# --------------------------------------------------------------------------- #
def test_tool_names_are_safe_bounded_and_reversible():
    long = TOOLS[2]["name"]
    names = F.ToolNames.build(["read_file", "custom:x", "custom_x", long, long + "_other"])
    assert names.forward["read_file"] == "read_file"
    assert names.forward["custom_x"] == "custom_x"  # the already-safe name keeps itself
    assert names.forward["custom:x"] != "custom_x" and names.forward["custom:x"].startswith("custom_x_")
    for ours, ident in names.forward.items():
        native = names.native(ours)
        assert len(native) <= 64 and F._SAFE.fullmatch(ident), native
        assert names.ours(native) == ours  # round trip
    assert names.forward[long] != names.forward[long + "_other"]
    # Order never decides who gets the clean id.
    assert F.ToolNames.build(["custom:x", "custom_x"]).forward == {
        k: v for k, v in names.forward.items() if k in ("custom:x", "custom_x")}


def test_a_tool_outside_the_inventory_is_refused():
    names = F.ToolNames.build(["read_file"])
    for bad in ("mcp__ij__write_file", "read_file", "mcp__other__read_file"):
        with pytest.raises(F.UnknownToolError, match="outside this request"):
            names.ours(bad)


# --------------------------------------------------------------------------- #
# 3. schema normalisation (pure) — their test, our implementation
# --------------------------------------------------------------------------- #
def test_tool_schemas_are_normalized_for_the_native_validator():
    schema = {
        "type": "object",
        "properties": {
            "mode": {"type": "string"},
            "proposal": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "ref": {"oneOf": [{"type": "string"}, {"type": "integer"}]},
            "maybe": {"type": ["integer", "null"], "description": "n"},
            "items": {"type": "array", "items": {"anyOf": [{"type": "object", "properties": {}}, {"type": "null"}]}},
        },
        "required": ["mode"],
        "allOf": [{"if": {"properties": {"mode": {"const": "proposal"}}}, "then": {"required": ["proposal"]}}],
    }
    original = copy.deepcopy(schema)
    manifest, native, _ = F.build_tools([{"name": "probe", "description": "d", "input_schema": schema}])
    for out in (native[0]["input_schema"], manifest[0]["inputSchema"]):
        assert "allOf" not in out and out["required"] == ["mode"]
        assert out["properties"]["proposal"] == {"type": "string"}
        assert out["properties"]["ref"] == {"oneOf": [{"type": "string"}, {"type": "integer"}]}
        assert out["properties"]["maybe"] == {"type": "integer", "description": "n"}
        assert out["properties"]["items"]["items"] == {"type": "object", "properties": {}}
    assert schema == original  # never mutated
    assert F.normalize_input_schema({"anyOf": [{"type": "object"}]}) == {"type": "object", "properties": {}}
    assert F.normalize_input_schema(None) == {"type": "object", "properties": {}}


# --------------------------------------------------------------------------- #
# 4. a stray API route is STRIPPED (coordinator decision, v1.300.0)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("key,value", [
    ("ANTHROPIC_API_KEY", "sk-ant-api03-SECRET-VALUE"),
    ("ANTHROPIC_AUTH_TOKEN", "SECRET-VALUE-token"),
    ("ANTHROPIC_BASE_URL", "https://SECRET-VALUE.example"),
    ("ANTHROPIC_MODEL", "SECRET-VALUE-model"),
    ("CLAUDE_CODE_USE_BEDROCK", "1"),
])
async def test_a_stray_api_route_is_stripped_and_named_once_never_valued(tmp_path, monkeypatch, caplog, key, value):
    """claude-cli IS "use my subscription": an inherited key/endpoint/backend
    switch must never reach the CLI (it would bill the API or another cloud),
    and the user is told WHICH variable was ignored — never its value."""
    fake = Fake(tmp_path, monkeypatch)
    monkeypatch.setenv(key, value)
    caplog.set_level("INFO", logger="ironjarvis.claude_native")
    a = fake.adapter()
    for _ in range(2):
        resp = await a.complete(system="", messages=[_user()], tools=[])
        assert resp.text == "Hello"
        env = fake.record["env"]
        assert key not in env or (key == "ANTHROPIC_BASE_URL" and "/admit/" in env[key])
        if value != "1":
            assert value not in json.dumps(fake.record)
        if key == "ANTHROPIC_BASE_URL":
            assert env[key].startswith("http://127.0.0.1:"), "the relay, never the inherited URL"
    lines = [r.getMessage() for r in caplog.records if "ignoring" in r.getMessage()]
    assert len(lines) == 1, lines  # once per process
    assert key in lines[0] and "subscription" in lines[0]
    if value != "1":
        assert value not in caplog.text


async def test_a_switch_set_to_a_falsy_value_is_not_named(tmp_path, monkeypatch, caplog):
    fake = Fake(tmp_path, monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "0")
    caplog.set_level("INFO", logger="ironjarvis.claude_native")
    resp = await fake.adapter().complete(system="", messages=[_user()], tools=[])
    assert resp.text == "Hello"
    assert "CLAUDE_CODE_USE_VERTEX" not in fake.record["env"]
    assert "ignoring" not in caplog.text


# --------------------------------------------------------------------------- #
# 5. argv + env + files, exactly
# --------------------------------------------------------------------------- #
async def test_argv_env_and_request_files_are_exactly_the_contract(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_EFFORT_LEVEL", "max")  # inherited: must not override --effort
    monkeypatch.setenv("CLAUDE_CODE_EXTRA_BODY", '{"evil": 1}')
    await fake.adapter().complete(system="be brief", messages=[_user()], tools=TOOLS)
    rec = fake.record
    argv = rec["argv"]
    root = Path(argv[argv.index("--settings") + 1]).parent
    from iron_jarvis.providers.claude_models import native_model

    assert argv == [
        "-p", "--model", native_model("claude-sonnet-5"),
        "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
        "--include-partial-messages", "--tools", "",
        "--system-prompt-file", str(root / "system.md"), "--settings", str(root / "settings.json"),
        "--setting-sources", "", "--strict-mcp-config", "--disable-slash-commands",
        "--max-turns", "1", "--permission-mode", "dontAsk", "--no-session-persistence",
        "--mcp-config", str(root / "mcp.json"),
    ]
    assert argv[2].endswith("[1m]"), "the 1M route survives (the old adapter sent 'sonnet')"
    env = rec["env"]
    assert env["ANTHROPIC_BASE_URL"].startswith("http://127.0.0.1:") and "/admit/" in env["ANTHROPIC_BASE_URL"]
    for gone in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_EFFORT_LEVEL", "CLAUDE_CODE_EXTRA_BODY"):
        assert gone not in env
    # Every knob's VALUE, spelled out (comparing to _FIXED_ENV itself was a
    # tautology: CLAUDE_CODE_MAX_RETRIES "0" -> "10" survived it). Zero native
    # retries matters: the relay owns the ONE upstream request.
    knobs = {
        "ENABLE_TOOL_SEARCH": "false",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "CLAUDE_CODE_MAX_RETRIES": "0",
        "DISABLE_AUTO_COMPACT": "1",
        "DISABLE_COMPACT": "1",
        "CLAUDE_CODE_TOTAL_TOKENS_REMINDER": "off",
    }
    assert {k: env.get(k) for k in knobs} == knobs
    assert transport._FIXED_ENV == knobs  # nothing added without a pin
    assert "CLAUDE_CODE_MAX_OUTPUT_TOKENS" not in env
    assert rec["system"] == "be brief"
    body = json.loads(json.loads(rec["settings"])["env"]["CLAUDE_CODE_EXTRA_BODY"])
    natives = [t["name"] for t in body["tools"]]
    assert natives[0] == "mcp__ij__read_file" and all(n.startswith("mcp__ij__") and len(n) <= 64 for n in natives)
    assert "output_config" not in body
    server = json.loads(rec["mcp"])["mcpServers"]["ij"]
    assert [server["command"], *server["args"][:-1]] == inert_mcp.inert_mcp_command("X")[:-1]
    assert [t["name"] for t in rec["manifest"]] == [n[len("mcp__ij__"):] for n in natives]
    assert rec["cwd"] == transport.stable_workdir()
    # The request's private files are gone afterwards; the cwd stays (cache).
    assert not root.exists()
    assert Path(rec["cwd"]).is_dir()


async def test_effort_and_budget_flags(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch)
    a = fake.adapter()
    token = budget.set_run_budget(1.234)
    try:
        await a.complete(system="", messages=[_user()], tools=[], reasoning="high")
    finally:
        budget.reset_run_budget(token)
    argv = fake.record["argv"]
    assert argv[argv.index("--effort") + 1] == "high"
    assert argv[argv.index("--max-budget-usd") + 1] == "1.23"
    body = json.loads(json.loads(fake.record["settings"])["env"]["CLAUDE_CODE_EXTRA_BODY"])
    assert body["output_config"] == {"effort": "high"}
    token = budget.set_run_budget(0.03)  # under the CLI's floor
    try:
        await a.complete(system="", messages=[_user()], tools=[], reasoning="sideways")
    finally:
        budget.reset_run_budget(token)
    argv = fake.record["argv"]
    assert "--effort" not in argv and "--max-budget-usd" not in argv


async def test_placeholder_model_sends_no_model_flag(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch)
    await fake.adapter(model="subscription").complete(system="", messages=[_user()], tools=[])
    assert "--model" not in fake.record["argv"]


async def test_a_refused_model_never_runs_the_cli(tmp_path, monkeypatch):
    """``haiku[1m]`` is a 200K model asked for a 1M route: the adapter says so
    in a sentence and NOTHING runs — not the injected runner, not the real
    process. (A refusal turned into ``None`` would silently run the CLI's own
    default model instead: the reviewer's surviving mutation.)"""
    fake = Fake(tmp_path, monkeypatch)
    ran: list[list[str]] = []

    def runner(argv, stdin):
        ran.append(argv)
        return 0, "", ""

    for streaming in (False, True):
        a = sc.ClaudeCliAdapter(model="claude-haiku-4-5[1m]", which=lambda _b: fake.shim, runner=runner)
        with pytest.raises(RuntimeError) as info:
            if streaming:
                await _drain(a.stream(system="", messages=[_user()], tools=[]))
            else:
                await a.complete(system="", messages=[_user()], tools=[])
        said = str(info.value)
        assert said.startswith("claude-cli: ") and "Haiku 4.5" in said and "200K" in said
        assert "cannot run with [1m]" in said
    assert ran == []
    assert not fake.record_path.exists()  # and the real path never spawned either
    a = sc.ClaudeCliAdapter(model="claude-haiku-4-5[1m]", which=lambda _b: fake.shim)
    with pytest.raises(RuntimeError, match="cannot run with"):
        await a.complete(system="", messages=[_user()], tools=[])
    assert not fake.record_path.exists()


# --------------------------------------------------------------------------- #
# 6. streaming, tools, usage
# --------------------------------------------------------------------------- #
def _same(a, b):
    assert (a.text, a.finish_reason, a.usage, a.raw_blocks) == (b.text, b.finish_reason, b.usage, b.raw_blocks)
    assert [(c.id, c.name, c.arguments) for c in a.tool_calls] == [(c.id, c.name, c.arguments) for c in b.tool_calls]


async def test_text_streams_live_then_a_final_equal_to_complete(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch, "thinking")
    a = fake.adapter()
    frames = await _drain(a.stream(system="", messages=[_user()], tools=[]))
    assert [f["text"] for f in frames if f["type"] == "text"] == ["He", "llo"]  # thinking dropped
    assert frames[-1]["type"] == "final"
    streamed = frames[-1]["response"]
    _same(streamed, await a.complete(system="", messages=[_user()], tools=[]))
    assert streamed.text == "Hello" and streamed.finish_reason == "stop"
    carrier = streamed.raw_blocks[0]
    assert streamed.raw_blocks == [carrier] and carrier["type"] == CARRIER and carrier["version"] == 1
    assert carrier["messages"][0]["content"][0] == {"type": "thinking", "thinking": "hmm", "signature": "sig-1"}
    assert carrier["projection"] == {"content": "Hello", "tool_calls": []}


async def test_two_parallel_tool_calls_come_back_with_our_names(tmp_path, monkeypatch):
    names = F.ToolNames.build([t["name"] for t in TOOLS])
    calls = [[names.native("custom:x"), {"n": 1}], [names.native(TOOLS[2]["name"]), {"q": "rome"}]]
    fake = Fake(tmp_path, monkeypatch, "tools", calls=json.dumps(calls))
    resp = await fake.adapter().complete(system="", messages=[_user("both")], tools=TOOLS)
    assert resp.finish_reason == "tool_use" and resp.text == "Checking both."
    assert [(c.id, c.name, c.arguments) for c in resp.tool_calls] == [
        ("toolu_00", "custom:x", {"n": 1}), ("toolu_01", TOOLS[2]["name"], {"q": "rome"})]
    # The step replays verbatim next time (signed thinking included) ...
    turn = LLMMessage(role="assistant", content=resp.text, tool_calls=resp.tool_calls, raw_blocks=resp.raw_blocks)
    results = [LLMMessage(role="tool", content="r", tool_call_id=c.id) for c in resp.tool_calls]
    _, frames = F.history_frames("", [_user("both"), turn, *results], names)
    assert frames[1]["message"]["content"][0]["signature"] == "sig-abc"
    assert frames[2]["message"]["content"] == [
        {"type": "tool_result", "tool_use_id": "toolu_00", "content": "r"},
        {"type": "tool_result", "tool_use_id": "toolu_01", "content": "r"}]


async def test_usage_carries_cache_buckets_and_the_native_cost(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch)
    resp = await fake.adapter().complete(system="", messages=[_user()], tools=[])
    assert resp.usage == {"input_tokens": 3 + 7 + 11, "output_tokens": 5, "cache_read_input_tokens": 7,
                          "cache_creation_input_tokens": 11, "cost_usd": 0.012345}
    for cost in ("none", "nan", "-1"):
        monkeypatch.setenv("FAKE_CLAUDE_COST", cost)
        resp = await fake.adapter().complete(system="", messages=[_user()], tools=[])
        assert "cost_usd" not in resp.usage, cost


async def test_max_tokens_is_reported(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch, "max_tokens")
    assert (await fake.adapter().complete(system="", messages=[_user()], tools=[])).finish_reason == "max_tokens"


async def test_history_replay_acks_every_past_user_frame(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch)
    msgs = [_user("code word PELICAN"), LLMMessage(role="assistant", content="Noted."), _user("word?")]
    resp = await fake.adapter().complete(system="", messages=msgs, tools=[])
    assert resp.text == "Hello"
    sent = fake.record["frames"]
    assert [f["type"] for f in sent] == ["user", "assistant", "user"]
    assert sent[0]["shouldQuery"] is False
    assert "shouldQuery" not in sent[2] and "shouldQuery" not in sent[1]
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "no_ack")
    with pytest.raises(RuntimeError, match="did not acknowledge history replay"):
        await fake.adapter().complete(system="", messages=msgs, tools=[])


async def test_an_image_reaches_the_cli_and_vision_is_offered(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch)
    a = fake.adapter()
    assert a.capabilities()["vision"] is True and a.capabilities()["tool_use"] is True
    await a.complete(system="", messages=[_user("what is this", images=[{"data_b64": "QUJD", "media_type": "image/png"}])],
                     tools=[])
    blocks = fake.record["frames"][-1]["message"]["content"]
    assert blocks[1]["type"] == "image" and blocks[1]["source"]["data"] == "QUJD"


# --------------------------------------------------------------------------- #
# 7. failures
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("words", ["Not logged in · Please run /login", "OAuth token has expired"])
async def test_signed_out_maps_to_the_sign_in_remedy(tmp_path, monkeypatch, words):
    """``authentication_failed`` before any upstream request IS signed out,
    whatever words the CLI chose — the second case matches none of the
    sign-in phrases, so only the error CODE can map it."""
    seen = []
    monkeypatch.setattr(cli_auth.DEFAULT_PROBE, "mark_signed_out", lambda b, d="": seen.append(b))
    fake = Fake(tmp_path, monkeypatch, "auth", auth_text=words)
    for streaming in (False, True):
        with pytest.raises(RuntimeError) as info:
            if streaming:
                await _drain(fake.adapter().stream(system="", messages=[_user()], tools=[]))
            else:
                await fake.adapter().complete(system="", messages=[_user()], tools=[])
        assert SIGN_IN_FIX["claude"] in str(info.value) and words in str(info.value)
    assert seen and seen[0] == "claude"


async def test_other_native_errors_keep_their_words(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch, "api_error")
    with pytest.raises(RuntimeError, match="API Error: 529 overloaded") as info:
        await fake.adapter().complete(system="", messages=[_user()], tools=[])
    assert SIGN_IN_FIX["claude"] not in str(info.value)
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "budget")
    with pytest.raises(RuntimeError, match="max-budget-usd"):
        await fake.adapter().complete(system="", messages=[_user()], tools=[])


async def test_a_tool_outside_the_inventory_fails_the_step(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch, "unknown_tool")
    with pytest.raises(RuntimeError, match="outside this request's inventory"):
        await fake.adapter().complete(system="", messages=[_user()], tools=TOOLS)


async def test_final_text_that_differs_from_the_stream_is_an_error(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch, "mismatch")
    with pytest.raises(RuntimeError, match="differs from what it streamed"):
        await fake.adapter().complete(system="", messages=[_user()], tools=[])


async def test_a_silent_cli_hits_the_idle_timeout(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch, "silent")
    t0 = time.monotonic()
    with pytest.raises(ProviderError) as info:
        await fake.adapter(idle_timeout_s=1.5).complete(system="", messages=[_user()], tools=[])
    assert info.value.transient and "no output for 2s" in str(info.value)
    assert time.monotonic() - t0 < 30  # against the fake's 60 s life, not a speed bar


async def test_not_installed_is_said_before_anything_runs():
    with pytest.raises(RuntimeError, match="not installed"):
        await sc.make_claude_cli(which=lambda _b: None).complete(system="", messages=[_user()], tools=[])


# --------------------------------------------------------------------------- #
# 8. cancellation kills the tree
# --------------------------------------------------------------------------- #
def _alive(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


async def _gone(pids: list[int], within: float = 15.0) -> list[int]:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        left = [p for p in pids if _alive(p)]
        if not left:
            return []
        await asyncio.sleep(0.1)
    return [p for p in pids if _alive(p)]


@pytest.mark.parametrize("how", ["cancel", "walk_away"])
async def test_stop_kills_the_cli_tree(tmp_path, monkeypatch, how):
    pids_file = tmp_path / "pids.txt"
    fake = Fake(tmp_path, monkeypatch, "hang", pids=str(pids_file))
    stream = fake.adapter().stream(system="", messages=[_user()], tools=[])
    first = asyncio.Event()

    async def consume():
        async for frame in stream:
            if frame.get("text") == "started":
                first.set()
                if how == "walk_away":
                    break

    task = asyncio.create_task(consume())
    await asyncio.wait_for(first.wait(), 30)
    for _ in range(200):
        if pids_file.exists():
            break
        await asyncio.sleep(0.05)
    pids = [int(p) for p in pids_file.read_text().split()]
    t0 = time.monotonic()
    try:
        if how == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await task
            await stream.aclose()
        left = await _gone(pids)
        assert not left, f"{how}: the CLI tree is still running: {left} of {pids}"
        # Measured against the fake's own 60 s life, never an absolute bar: a
        # tree nobody killed is only "gone" once it ends by itself.
        took = time.monotonic() - t0
        assert took < 30, f"{how}: the tree died of old age ({took:.0f}s), not of the Stop"
    finally:
        for pid in pids:
            try:
                psutil.Process(pid).kill()
            except psutil.Error:
                pass


# --------------------------------------------------------------------------- #
# 9. the admission relay is used (loopback fake upstream only)
# --------------------------------------------------------------------------- #
async def test_the_relay_admits_one_request_and_its_capture_is_the_answer(tmp_path, monkeypatch):
    calls: list[str] = []
    up_usage = {"input_tokens": 100, "output_tokens": 2, "cache_read_input_tokens": 50,
                "cache_creation_input_tokens": 0}

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            calls.append(self.path)
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            events = [
                {"type": "message_start", "message": {"id": "up", "role": "assistant", "model": "m",
                                                      "content": [], "usage": dict(up_usage)}},
                {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "FIRST"}},
                {"type": "content_block_stop", "index": 0},
                {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}},
                {"type": "message_stop"},
            ]
            self.wfile.write("".join("data: " + json.dumps(e) + "\n\n" for e in events).encode())

    peer = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=peer.serve_forever, daemon=True).start()
    try:
        fake = Fake(tmp_path, monkeypatch, "relay")
        env = dict(os.environ, ANTHROPIC_BASE_URL=f"http://127.0.0.1:{peer.server_port}")
        resp = await fake.adapter(env=env).complete(system="", messages=[_user()], tools=[])
    finally:
        peer.shutdown()
        peer.server_close()
    assert len(calls) == 1, "native's retry must never reach the upstream"
    assert fake.record["relay_statuses"] == [200, 400]
    assert resp.text == "FIRST"
    assert resp.usage["input_tokens"] == 150 and resp.usage["cache_read_input_tokens"] == 50
    assert resp.raw_blocks[0]["messages"][0]["id"] == "up"  # the capture, not native's re-render


# --------------------------------------------------------------------------- #
# 10. the API adapter never replays our carrier
# --------------------------------------------------------------------------- #
def test_the_anthropic_api_adapter_ignores_the_carrier():
    turn, _ = _carrier_turn()
    out = AnthropicAdapter._to_anthropic_messages([_user("write"), turn,
                                                    LLMMessage(role="tool", content="ok", tool_call_id="toolu_1")])
    blocks = out[1]["content"]
    assert blocks == [{"type": "text", "text": "Writing.\n"},
                      {"type": "tool_use", "id": "toolu_1", "name": "write_file", "input": {"path": "x"}}]
    assert "mcp__ij__" not in json.dumps(out) and "opaque" not in json.dumps(out)
    # Its own blocks still replay verbatim when they sit beside ours.
    own = [{"type": "thinking", "thinking": "t", "signature": "api"}, {"type": "text", "text": "x"}]
    turn.raw_blocks = own + turn.raw_blocks
    assert AnthropicAdapter._to_anthropic_messages([_user(), turn])[1]["content"] == own


# --------------------------------------------------------------------------- #
# 11. the inert inventory, from source and frozen
# --------------------------------------------------------------------------- #
def _speak(argv: list[str], manifest: Path) -> list[dict]:
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "read_file", "arguments": {}}},
    ]
    out = subprocess.run(argv + [str(manifest)], input="".join(json.dumps(x) + "\n" for x in lines).encode(),
                         capture_output=True, timeout=120, cwd=str(ROOT))
    assert out.returncode == 0, out.stderr.decode(errors="replace")[-800:]
    return [json.loads(line) for line in out.stdout.decode().splitlines() if line.strip()]


@pytest.mark.parametrize("door", ["module", "hidden_subcommand"])
def test_the_inert_server_lists_and_refuses(tmp_path, door):
    manifest = tmp_path / "tools.json"
    manifest.write_text(json.dumps([{"name": "read_file", "description": "Read · a file",
                                     "inputSchema": {"type": "object", "properties": {}}}]), encoding="utf-8")
    if door == "module":
        argv = inert_mcp.inert_mcp_command("X")[:-1]
    else:
        argv = [sys.executable, "-c", "from iron_jarvis.daemon.cli import app; app()", "claude-inert-mcp"]
    replies = _speak(argv, manifest)
    assert [r["id"] for r in replies] == [1, 2, 3]  # the notification got no answer; stdout is ONLY protocol
    assert replies[0]["result"]["capabilities"] == {"tools": {}}
    assert replies[1]["result"]["tools"][0]["name"] == "read_file"
    assert replies[1]["result"]["tools"][0]["description"] == "Read · a file"
    assert replies[2]["result"]["isError"] is True and "inert" in replies[2]["result"]["content"][0]["text"]


def test_the_frozen_command_is_the_hidden_subcommand(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Program Files\Iron Jarvis\ironjarvis.exe")
    assert inert_mcp.inert_mcp_command("m.json") == [
        r"C:\Program Files\Iron Jarvis\ironjarvis.exe", "claude-inert-mcp", "m.json"]
    monkeypatch.setattr(sys, "frozen", False)
    assert inert_mcp.inert_mcp_command("m.json")[1:] == ["-m", inert_mcp.MODULE, "m.json"]


def test_the_inert_module_imports_nothing_heavy():
    """It starts once per model call: importing it must not pull the app in."""
    code = ("import sys, iron_jarvis.providers.adapters.claude_native.inert_mcp as m; "
            "print(sorted(k for k in sys.modules if k.startswith('iron_jarvis')))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    loaded = json.loads(out.stdout.replace("'", '"'))
    assert loaded == ["iron_jarvis", "iron_jarvis.providers", "iron_jarvis.providers.adapters",
                      "iron_jarvis.providers.adapters.claude_native",
                      "iron_jarvis.providers.adapters.claude_native.inert_mcp"]
