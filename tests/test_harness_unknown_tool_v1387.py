"""IJ harness part 3, Phase 3 — A TOOL THE MODEL DOES NOT HAVE IS A REFUSED
RESULT, NEVER A FAILED TURN.

The Phase 1 measurement (``frontier-csv-encodings`` repeat 2, Jarvis on
Claude) ENDED with "claude-cli: the model called a tool outside this
request's inventory ('python'); nothing was run." — the claude-cli adapter
raised on a ``tool_use`` naming a tool it had not offered, and the whole turn
failed. Now the name reaches the lane as the model wrote it, the registry
refuses it like every invented name ("unknown tool" — ledgered, nothing
run), and a harness turn's model is told the names it CAN call.

Each pin drives the REAL pieces: the claude-cli adapter and its transport
against the fake ``claude`` (``tests/fixtures/fake_claude_v1300.py``), the
real frames builder for the next request, and the real stream lane on a real
platform with the adapter's own answer scripted in.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from iron_jarvis.platform import build_platform
from iron_jarvis.providers import cli_auth
from iron_jarvis.providers.adapters import subprocess_cli as sc
from iron_jarvis.providers.adapters.base import LLMMessage
from iron_jarvis.providers.adapters.claude_native import frames as F
from iron_jarvis.providers.adapters.claude_native import transport
from iron_jarvis.providers.cli_auth import CliAuthProbe
from tests._fakes.harness_lane import body, finished_ids, ledger, no_finish_check, stream, wire

FAKE = Path(__file__).parent / "fixtures" / "fake_claude_v1300.py"
TOOLS = [
    {"name": "read_file", "description": "Read a file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in [k for k in os.environ if k.startswith("ANTHROPIC_")] + list(transport.BACKEND_SWITCHES):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(transport, "_STRIP_LOGGED", False)
    monkeypatch.setattr(cli_auth, "DEFAULT_PROBE", CliAuthProbe(which=lambda b: None))
    monkeypatch.setattr(transport, "DEFAULT_PROBE", cli_auth.DEFAULT_PROBE)


def _adapter(tmp_path: Path, monkeypatch, calls: list) -> sc.ClaudeCliAdapter:
    if os.name == "nt":
        shim = tmp_path / "claude.cmd"
        shim.write_text(f'@"{sys.executable}" "{FAKE}" %*\r\n', encoding="utf-8")
    else:
        shim = tmp_path / "claude"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n', encoding="utf-8")
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "tools")
    monkeypatch.setenv("FAKE_CLAUDE_RECORD", str(tmp_path / "record.json"))
    monkeypatch.setenv("FAKE_CLAUDE_CALLS", json.dumps(calls))
    return sc.make_claude_cli(which=lambda _b: str(shim), model="claude-sonnet-5")


@pytest.mark.parametrize("streaming", [False, True])
async def test_the_claude_cli_adapter_hands_on_a_tool_it_did_not_offer(tmp_path, monkeypatch, streaming):
    """FAILS ON THE OLD CODE: ``assemble`` raised UnknownToolError."""
    adapter = _adapter(tmp_path, monkeypatch, [
        ["python", {"code": "print(1)"}],
        ["mcp__ij__read_file", {"path": "a.txt"}],
        ["mcp__ij__not_offered", {}],
    ])
    msgs = [LLMMessage(role="user", content="hi")]
    if streaming:
        frames = [f async for f in adapter.stream(system="", messages=msgs, tools=TOOLS)]
        resp = next(f["response"] for f in frames if f.get("type") == "final")
    else:
        resp = await adapter.complete(system="", messages=msgs, tools=TOOLS)
    # The offered tool maps back; the others keep the name the model wrote
    # (our own prefix taken off) — the lane's registry refuses those.
    assert [(c.id, c.name, c.arguments) for c in resp.tool_calls] == [
        ("toolu_00", "python", {"code": "print(1)"}),
        ("toolu_01", "read_file", {"path": "a.txt"}),
        ("toolu_02", "not_offered", {}),
    ]
    assert resp.finish_reason == "tool_use"


async def test_the_next_request_replays_the_refused_call_with_its_result(tmp_path, monkeypatch):
    """The step after: the assistant turn (its native carrier) and the
    refusal as its tool result build into the CLI's frames — the API sees a
    real tool_use / tool_result pair, exactly as Claude Code itself answers a
    tool it does not have."""
    adapter = _adapter(tmp_path, monkeypatch, [["python", {"code": "print(1)"}]])
    resp = await adapter.complete(system="", messages=[LLMMessage(role="user", content="hi")], tools=TOOLS)
    history = [
        LLMMessage(role="user", content="hi"),
        LLMMessage(role="assistant", content=resp.text, tool_calls=resp.tool_calls, raw_blocks=resp.raw_blocks),
        LLMMessage(role="tool", tool_call_id="toolu_00", name="python", content="unknown tool 'python'"),
    ]
    _system, frames = F.history_frames("", history, F.ToolNames.build(["read_file"]))
    uses = [b for fr in frames if fr["type"] == "assistant" for b in fr["message"]["content"]
            if b.get("type") == "tool_use"]
    results = [b for fr in frames if fr["type"] == "user" for b in fr["message"]["content"]
               if b.get("type") == "tool_result"]
    assert [(u["id"], u["name"]) for u in uses] == [("toolu_00", "python")]
    assert [(r["tool_use_id"], r["content"]) for r in results] == [("toolu_00", "unknown tool 'python'")]


async def test_the_lane_answers_it_as_a_refused_call_and_the_turn_goes_on(tmp_path, monkeypatch):
    """The adapter's own answer, scripted into the REAL stream lane of a
    Build session (the claude dialect, as Jarvis on Claude): the call is
    refused by the registry (ledgered, nothing run), the model reads the
    refusal WITH the tools it can call — in its own dialect's names — and the
    turn ends with a reply, not an error. The tool list is new: the old lane
    answered "unknown tool 'python'" alone."""
    adapter = _adapter(tmp_path, monkeypatch, [["python", {"code": "print(1)"}]])
    resp = await adapter.complete(system="", messages=[LLMMessage(role="user", content="hi")], tools=TOOLS)
    platform = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir()
    no_finish_check(ws)
    model = wire(platform, monkeypatch, [resp, "Sorry, wrong tool. Done."])
    out = await stream(platform, body(ws), harness="claude")
    assert out["errors"] == [] and out["done"] is not None
    assert out["done"]["reply"].endswith("Done.")
    sent = model.tool_messages(1)
    text = sent["toolu_00"]
    assert text.startswith("unknown tool 'python'\n(The tools you can call in this reply: ")
    names = text.split("call in this reply: ", 1)[1].rstrip(".)").split(", ")
    assert "Bash" in names and "Read" in names and "Edit" in names
    assert "shell" not in names and "read_file" not in names  # the dialect's names, not the real ones
    # Ledgered as a refusal, and nothing ran.
    rows = [(r.tool, r.ok) for r in ledger(platform)]
    assert rows == [("python", False)]
    assert finished_ids(out["frames"]) == ["toolu_00"]


async def test_a_real_tool_this_reply_was_not_given_gets_the_same_answer(tmp_path, monkeypatch):
    """A REAL tool outside the turn's armed set (``not armed``) is answered
    the same way: refused, and the names it can call."""
    from iron_jarvis.providers.adapters.base import ToolCall

    platform = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir()
    model = wire(platform, monkeypatch, [[ToolCall(id="c1", name="memory_write", arguments={"text": "x"})], "Done."])
    await stream(platform, body(ws), harness="generic")
    text = model.tool_messages(1)["c1"]
    assert text.startswith("not armed: ") and "\n(The tools you can call in this reply: read_file, " in text


async def test_plain_chat_keeps_the_registrys_words_alone(tmp_path, monkeypatch):
    """Off the harness nothing changes: the registry's refusal, bare."""
    from iron_jarvis.providers.adapters.base import ToolCall

    platform = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir()
    model = wire(platform, monkeypatch, [[ToolCall(id="c1", name="python", arguments={})], "Done."])
    await stream(platform, body(ws, tools=["read_file"]), harness=None, session_id="chat")
    assert model.tool_messages(1)["c1"] == "unknown tool 'python'"
