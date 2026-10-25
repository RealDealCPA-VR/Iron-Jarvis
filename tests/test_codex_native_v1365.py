"""IJ harness phase 2 — Codex speaks its own protocol (``codex_native``).

``codex-cli`` used to be TEXT-ONLY (``codex exec``, the conversation
flattened into one prompt). The native adapter drives the user's own ``codex
app-server`` over JSON-RPC: Iron Jarvis's armed tools are offered as Codex
DYNAMIC TOOLS, Codex's own tools that act are switched off, and every tool
call comes back to the lane — which runs it through ``registry.invoke`` and
calls the adapter again; that next call RESUMES the same paused Codex turn.

Driven through the REAL adapter code against a FAKE app-server
(``fixtures/fake_codex_app_server_v1365.py``, behind a ``codex.cmd`` shim: the
process TREE a real install has). No test ever runs the real ``codex``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
import sys
import time
from pathlib import Path

import psutil
import pytest

from iron_jarvis.iron_proxy import accounts
from iron_jarvis.providers import cli_auth
from iron_jarvis.providers.adapters.base import LLMMessage, ProviderError, ToolCall
from iron_jarvis.providers.adapters.codex_native import adapter as A
from iron_jarvis.providers.adapters.codex_native import protocol as P
from iron_jarvis.providers.adapters.codex_native import session as S
from iron_jarvis.providers.cli_auth import SIGN_IN_FIX, CliAuthProbe

FAKE = Path(__file__).parent / "fixtures" / "fake_codex_app_server_v1365.py"


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #
def _kill_all_sessions() -> None:
    for s in S.POOL.live():
        S.POOL.forget(s)
        s.kill()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """Every test starts with no live session, no verdict, no Iron-Proxy, its
    own sign-in probe; every session a test leaves behind is killed."""
    S.reset_verdict()
    _kill_all_sessions()
    # v1.365.0: the fake app-server is Python behind a .cmd shim; on a loaded
    # machine (a full -n auto suite beside other work, or CI's release runner)
    # starting it took longer than the real 30 s start limit, and three tests
    # failed in start-up rather than in what they test. The limit itself is not
    # under test here (the slow-start pins shorten their own target clock).
    monkeypatch.setattr(S, "START_TIMEOUT_S", 120.0)
    monkeypatch.setattr(A, "_ENV_NOTED", False)
    monkeypatch.setattr(A, "_NOTED_REASONS", set())
    probe = CliAuthProbe(which=lambda b: None)
    monkeypatch.setattr(cli_auth, "DEFAULT_PROBE", probe)
    previous = accounts.set_client_source(lambda: None)
    yield probe
    accounts.set_client_source(previous)
    _kill_all_sessions()
    S.reset_verdict()


def _shim(tmp_path: Path) -> str:
    if os.name == "nt":
        shim = tmp_path / "codex.cmd"
        shim.write_text(f'@"{sys.executable}" "{FAKE}" %*\r\n', encoding="utf-8")
    else:
        shim = tmp_path / "codex"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n', encoding="utf-8")
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    return str(shim)


class Fake:
    def __init__(self, tmp_path: Path, monkeypatch, processes: list[dict], *, pids: bool = False) -> None:
        self.tmp = tmp_path
        self.shim = _shim(tmp_path)
        self.record_path = tmp_path / "record.jsonl"
        script = tmp_path / "script.json"
        script.write_text(json.dumps({"processes": processes}), encoding="utf-8")
        monkeypatch.setenv("FAKE_CODEX_SCRIPT", str(script))
        monkeypatch.setenv("FAKE_CODEX_STATE", str(tmp_path / "state"))
        monkeypatch.setenv("FAKE_CODEX_RECORD", str(self.record_path))
        self.pids_path = tmp_path / "pids.txt"
        if pids:
            monkeypatch.setenv("FAKE_CODEX_PIDS", str(self.pids_path))
        else:
            monkeypatch.delenv("FAKE_CODEX_PIDS", raising=False)

    def adapter(self, **kw) -> A.CodexNativeAdapter:
        return A.CodexNativeAdapter(which=lambda _b: self.shim, **kw)

    def rows(self, event: str | None = None) -> list[dict]:
        """Every fake's rows (one file per process), merged in time order."""
        out: list[dict] = []
        for path in self.record_path.parent.glob(self.record_path.name + ".*"):
            out += [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
        out.sort(key=lambda r: (r["t"], r["pid"], r["seq"]))
        return [r for r in out if event is None or r["event"] == event]

    def starts(self) -> list[dict]:
        return self.rows("start")

    def received(self, method: str) -> list[dict]:
        return [r for r in self.rows("recv") if r["msg"].get("method") == method]

    def answers(self) -> list[dict]:
        return self.rows("answer")


TOOLS = [
    {"name": "lookup_fake", "description": "Look a thing up.",
     "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}},
    {"name": "custom:note", "description": "A custom tool.", "input_schema": {"type": "object"}},
    {"name": "mcp__a_very_long_server_name_for_tests__a_very_long_tool_name_too_long", "description": "Pack.",
     "input_schema": {"type": "object", "properties": {"x": {"type": "string"}}}},
]


def _user(text: str = "hi", **kw) -> LLMMessage:
    return LLMMessage(role="user", content=text, **kw)


def _after(messages: list[LLMMessage], resp, results: dict[str, str]) -> list[LLMMessage]:
    """What a lane sends next: the assistant turn with its calls, then one
    ``role="tool"`` message per call (the lanes' shape)."""
    out = list(messages)
    out.append(LLMMessage(role="assistant", content=resp.text, tool_calls=list(resp.tool_calls)))
    for tc in resp.tool_calls:
        out.append(LLMMessage(role="tool", tool_call_id=tc.id, name=tc.name, content=results[tc.name]))
    return out


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


def _session_pids() -> list[int]:
    return [s.proc.pid for s in S.POOL.live() if s.proc is not None]


TWO_ROUNDS = [{"steps": [
    {"text": "Let me look."},
    {"usage": {"inputTokens": 1000, "cachedInputTokens": 200, "outputTokens": 30}},
    {"call": {"tool_index": 0, "args": {"q": "alpha"}}},
    {"usage": {"inputTokens": 1300, "cachedInputTokens": 1000, "outputTokens": 12}},
    {"call": {"tool_index": 1, "args": {"text": "remember"}}},
    {"thinking": "All looked up."},
    {"usage": {"inputTokens": 1500, "cachedInputTokens": 1200, "outputTokens": 40}},
    {"text": "Found alpha and noted it."},
]}]


# --------------------------------------------------------------------------- #
# 1. the multi-round tool loop: one Codex turn across several adapter calls
# --------------------------------------------------------------------------- #
async def test_a_two_round_tool_loop_resumes_the_same_paused_turn(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch, TWO_ROUNDS)
    adapter = fake.adapter()
    msgs = [_user("find alpha")]
    r1 = await adapter.complete(system="You are Jarvis.", messages=msgs, tools=TOOLS)
    assert r1.finish_reason == "tool_use"
    assert [(c.name, c.arguments) for c in r1.tool_calls] == [("lookup_fake", {"q": "alpha"})]
    assert r1.text == "Let me look."
    assert r1.usage["input_tokens"] == 1000 and r1.usage["output_tokens"] == 30
    assert r1.usage["cache_read_input_tokens"] == 200
    # The turn is PAUSED: the process is alive and parked under that call id.
    live = S.POOL.live()
    assert len(live) == 1 and live[0].state == "paused" and set(live[0].pending) == {r1.tool_calls[0].id}

    msgs = _after(msgs, r1, {"lookup_fake": "RESULT-ALPHA"})
    r2 = await adapter.complete(system="You are Jarvis.", messages=msgs, tools=TOOLS)
    assert [(c.name, c.arguments) for c in r2.tool_calls] == [("custom:note", {"text": "remember"})]
    assert r2.usage["input_tokens"] == 1300 and r2.usage["cache_read_input_tokens"] == 1000

    msgs = _after(msgs, r2, {"custom:note": "NOTED"})
    r3 = await adapter.complete(system="You are Jarvis.", messages=msgs, tools=TOOLS)
    assert r3.finish_reason == "stop" and not r3.tool_calls
    assert r3.text == "Found alpha and noted it."
    assert r3.thinking == "All looked up."
    assert r3.usage["input_tokens"] == 1500 and r3.usage["output_tokens"] == 40

    # ONE process served all three calls; each answer reached Codex verbatim.
    assert len(fake.starts()) == 1
    assert [a["response"]["contentItems"][0]["text"] for a in fake.answers()] == ["RESULT-ALPHA", "NOTED"]
    assert all(a["response"]["success"] is True for a in fake.answers())
    # The turn ended: nothing live, the process is gone.
    assert S.POOL.live() == []
    assert await _gone([fake.starts()[0]["pid"]]) == []


async def test_usage_codex_reports_after_the_tools_lands_on_the_next_call_and_the_total_is_exact(
    tmp_path, monkeypatch,
):
    """Measured on the real Codex 0.157.0 (and in its source): a round's token
    count is sent only AFTER that round's tools are answered, so it arrives
    during the NEXT adapter call. Nothing is invented or lost: every call
    reports what Codex reported while it ran, and the turn's total is exact."""
    fake = Fake(tmp_path, monkeypatch, [{"steps": [
        {"call": {"tool_index": 0, "args": {"q": "a"}}},
        {"usage": {"inputTokens": 700, "cachedInputTokens": 0, "outputTokens": 9}},
        {"text": "ok"},
        {"usage": {"inputTokens": 800, "cachedInputTokens": 600, "outputTokens": 4}},
    ]}])
    adapter = fake.adapter()
    r1 = await adapter.complete(system="", messages=[_user()], tools=TOOLS)
    assert (r1.usage["input_tokens"], r1.usage["output_tokens"]) == (0, 0)
    r2 = await adapter.complete(system="", messages=_after([_user()], r1, {"lookup_fake": "R"}), tools=TOOLS)
    assert (r2.usage["input_tokens"], r2.usage["output_tokens"]) == (1500, 13)
    assert r2.usage["cache_read_input_tokens"] == 600


async def test_a_batch_of_waiting_calls_comes_back_together_and_each_is_answered(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch, [{"steps": [
        {"calls": [{"tool_index": 0, "args": {"q": "one"}}, {"tool_index": 1, "args": {"k": 2}}]},
        {"text": "both done"}]}])
    adapter = fake.adapter()
    r1 = await adapter.complete(system="", messages=[_user()], tools=TOOLS)
    assert [(c.name, c.arguments) for c in r1.tool_calls] == [
        ("lookup_fake", {"q": "one"}), ("custom:note", {"k": 2})]
    msgs = _after([_user()], r1, {"lookup_fake": "R1", "custom:note": "R2"})
    r2 = await adapter.complete(system="", messages=msgs, tools=TOOLS)
    assert r2.text == "both done" and len(fake.starts()) == 1
    got = {a["tool"]: a["response"]["contentItems"][0]["text"] for a in fake.answers()}
    assert got == {"lookup_fake": "R1", "custom_note": "R2"}


async def test_stream_gives_text_and_thinking_frames_then_the_same_final(tmp_path, monkeypatch):
    proc = {"steps": [{"thinking": "Weighing it."}, {"text": "Hello there."},
                      {"usage": {"inputTokens": 10, "cachedInputTokens": 0, "outputTokens": 3}}]}
    fake = Fake(tmp_path, monkeypatch, [proc])
    frames = [f async for f in fake.adapter().stream(system="s", messages=[_user()], tools=[])]
    kinds = [f["type"] for f in frames]
    assert kinds[-1] == "final" and kinds.count("final") == 1
    text = "".join(f["text"] for f in frames if f["type"] == "text")
    thought = "".join(f["text"] for f in frames if f["type"] == "thinking")
    final = frames[-1]["response"]
    assert text == final.text == "Hello there."
    # v1.323.0: reasoning is a thinking frame, never answer text.
    assert thought == "Weighing it." and "Weighing" not in final.text and final.thinking == "Weighing it."
    # A turn that ended in its FIRST call closes its session and process too.
    assert S.POOL.live() == []
    assert await _gone([fake.starts()[0]["pid"]]) == []
    again = await fake.adapter().complete(system="s", messages=[_user()], tools=[])
    assert again.text == final.text and again.thinking == final.thinking
    assert again.usage == final.usage


# --------------------------------------------------------------------------- #
# 2. concurrent conversations never cross; a miss starts over honestly
# --------------------------------------------------------------------------- #
async def test_concurrent_conversations_each_resume_their_own_process(tmp_path, monkeypatch):
    proc = {"steps": [{"call": {"tool_index": 0, "args": {"q": "x"}}}, {"text": "done"}]}
    fake = Fake(tmp_path, monkeypatch, [proc])
    adapter = fake.adapter()
    a1, b1 = await asyncio.gather(
        adapter.complete(system="", messages=[_user("A")], tools=TOOLS),
        adapter.complete(system="", messages=[_user("B")], tools=TOOLS),
    )
    assert a1.tool_calls[0].id != b1.tool_calls[0].id
    assert len(S.POOL.live()) == 2
    # Resume in the OTHER order, concurrently.
    b2, a2 = await asyncio.gather(
        adapter.complete(system="", messages=_after([_user("B")], b1, {"lookup_fake": "RESULT-B"}), tools=TOOLS),
        adapter.complete(system="", messages=_after([_user("A")], a1, {"lookup_fake": "RESULT-A"}), tools=TOOLS),
    )
    assert a2.text == b2.text == "done"
    by_call = {a["callId"]: (a["pid"], a["response"]["contentItems"][0]["text"]) for a in fake.answers()}
    assert len({r["pid"] for r in fake.starts()}) == 2, "two conversations, two processes"
    assert by_call[a1.tool_calls[0].id][1] == "RESULT-A"
    assert by_call[b1.tool_calls[0].id][1] == "RESULT-B"
    assert by_call[a1.tool_calls[0].id][0] != by_call[b1.tool_calls[0].id][0]


async def test_a_miss_starts_a_new_thread_with_the_history_as_labelled_text(tmp_path, monkeypatch):
    first = {"steps": [{"text": "Checking."}, {"call": {"tool_index": 0, "args": {"q": "x"}}}, {"text": "never"}]}
    second = {"steps": [{"text": "Picked it up from the replay."}]}
    fake = Fake(tmp_path, monkeypatch, [first, second])
    adapter = fake.adapter()
    msgs = [_user("earlier question"), LLMMessage(role="assistant", content="earlier answer"), _user("now this")]
    r1 = await adapter.complete(system="sys", messages=msgs, tools=TOOLS)
    _kill_all_sessions()  # a daemon restart: nothing live any more
    r2 = await adapter.complete(system="sys", messages=_after(msgs, r1, {"lookup_fake": "RESULT-X"}), tools=TOOLS)
    assert r2.text == "Picked it up from the replay."
    assert len(fake.starts()) == 2
    turn = fake.received("turn/start")[-1]["msg"]["params"]["input"]
    replay = turn[0]["text"]
    assert replay.startswith(P.REPLAY_HEADER)
    for words in ("earlier question", "earlier answer", "now this", "RESULT-X", "lookup_fake", "Checking."):
        assert words in replay, words
    assert turn[1] == {"type": "text", "text": P.CONTINUE_NOTE}
    # The first thread's input was the ask itself, the earlier turns replayed.
    first_input = fake.received("turn/start")[0]["msg"]["params"]["input"]
    assert first_input[-1] == {"type": "text", "text": "now this"}
    assert first_input[0]["text"].startswith(P.REPLAY_HEADER) and "now this" not in first_input[0]["text"]


async def test_a_process_that_died_while_parked_is_a_miss_not_a_crash(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch, [{"steps": [{"call": {"tool_index": 0, "args": {}}}, {"text": "never"}]},
                                        {"steps": [{"text": "carried on"}]}])
    adapter = fake.adapter()
    r1 = await adapter.complete(system="", messages=[_user()], tools=TOOLS)
    parked = S.POOL.live()[0]
    psutil.Process(parked.proc.pid).kill()  # the app-server crashed; the pool still holds it
    await asyncio.to_thread(parked.proc.wait, 10)
    r2 = await adapter.complete(system="", messages=_after([_user()], r1, {"lookup_fake": "R"}), tools=TOOLS)
    assert r2.text == "carried on" and len(fake.starts()) == 2
    assert parked not in S.POOL.live()


async def test_a_conversation_that_moved_past_its_calls_stops_the_parked_session_at_once(tmp_path, monkeypatch):
    """An exit tool (escalate, a workflow draft) or a nudge ends the lane's
    loop without answering the calls; the next call carries the conversation
    on, and the parked app-server is stopped then, not at the reaper."""
    fake = Fake(tmp_path, monkeypatch, [{"steps": [{"call": {"tool_index": 0, "args": {}}}, {"text": "never"}]},
                                        {"steps": [{"text": "a plain answer"}]}])
    adapter = fake.adapter()
    r1 = await adapter.complete(system="", messages=[_user()], tools=TOOLS)
    old = _session_pids()
    moved_on = [_user(), LLMMessage(role="assistant", content=r1.text, tool_calls=list(r1.tool_calls)),
                _user("Answer now, without tools.")]
    r2 = await adapter.complete(system="", messages=moved_on, tools=[])
    assert r2.text == "a plain answer"
    assert await _gone(old, within=10) == []
    assert S.POOL.live() == []


async def test_an_edited_last_turn_is_a_miss_and_the_stale_session_is_stopped(tmp_path, monkeypatch):
    first = {"steps": [{"call": {"tool_index": 0, "args": {"q": "x"}}}, {"text": "never"}]}
    second = {"steps": [{"text": "fresh"}]}
    fake = Fake(tmp_path, monkeypatch, [first, second])
    adapter = fake.adapter()
    r1 = await adapter.complete(system="", messages=[_user()], tools=TOOLS)
    old_pid = _session_pids()[0]
    # The lane answered a DIFFERENT set of calls than Codex waits on (one
    # extra call id): not resumable, and the parked session is stopped at once.
    edited = _after([_user()], r1, {"lookup_fake": "R"})
    edited[-2] = LLMMessage(role="assistant", content="", tool_calls=[
        r1.tool_calls[0], ToolCall(id="call_invented", name="lookup_fake", arguments={})])
    edited.append(LLMMessage(role="tool", tool_call_id="call_invented", name="lookup_fake", content="R2"))
    r2 = await adapter.complete(system="", messages=edited, tools=TOOLS)
    assert r2.text == "fresh" and len(fake.starts()) == 2
    assert await _gone([old_pid]) == []


# --------------------------------------------------------------------------- #
# 3. bounded: cancel kills the tree, the idle reaper, the live-session cap
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("how", ["cancel", "walk_away"])
async def test_stop_kills_the_app_server_tree(tmp_path, monkeypatch, how):
    fake = Fake(tmp_path, monkeypatch, [{"steps": [{"text": "started"}, {"hang": 60}]}], pids=True)
    stream = fake.adapter().stream(system="", messages=[_user()], tools=[])
    first = asyncio.Event()

    async def consume():
        async for frame in stream:
            if frame.get("type") == "text":
                first.set()
                if how == "walk_away":
                    break

    task = asyncio.create_task(consume())
    await asyncio.wait_for(first.wait(), 120)  # includes a loaded start-up
    for _ in range(200):
        if fake.pids_path.exists() and fake.pids_path.read_text().strip():
            break
        await asyncio.sleep(0.05)
    pids = [int(p) for p in fake.pids_path.read_text().split()] + _session_pids()
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
        assert not left, f"{how}: the tree is still running: {left} of {pids}"
        # Measured against the fake's own 60 s life, never an absolute bar.
        assert time.monotonic() - t0 < 30, "the tree died of old age, not of the Stop"
        assert S.POOL.live() == []
    finally:
        for pid in pids:
            try:
                psutil.Process(pid).kill()
            except psutil.Error:
                pass


async def test_a_parked_session_nobody_resumes_is_stopped_by_the_idle_reaper(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "PAUSED_IDLE_TIMEOUT_S", 0.5)
    fake = Fake(tmp_path, monkeypatch, [{"steps": [{"call": {"tool_index": 0, "args": {"q": "x"}}}]},
                                        {"steps": [{"text": "started over"}]}])
    adapter = fake.adapter()
    r1 = await adapter.complete(system="", messages=[_user()], tools=TOOLS)
    pids = _session_pids()
    assert pids and S.POOL.live()[0].state == "paused"
    assert await _gone(pids, within=15) == [], "the parked app-server outlived the idle timeout"
    assert S.POOL.live() == []
    # Coming back after the reaper is a miss, not a crash.
    r2 = await adapter.complete(system="", messages=_after([_user()], r1, {"lookup_fake": "R"}), tools=TOOLS)
    assert r2.text == "started over" and len(fake.starts()) == 2


async def test_the_live_session_cap_stops_the_oldest_parked_one(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "MAX_LIVE_SESSIONS", 1)
    fake = Fake(tmp_path, monkeypatch, [{"steps": [{"call": {"tool_index": 0, "args": {}}}, {"text": "ok"}]}])
    adapter = fake.adapter()
    await adapter.complete(system="", messages=[_user("A")], tools=TOOLS)
    first = _session_pids()
    await adapter.complete(system="", messages=[_user("B")], tools=TOOLS)
    assert len(S.POOL.live()) == 1
    assert await _gone(first) == [], "the cap was not enforced"


# --------------------------------------------------------------------------- #
# 4. Codex executes nothing: built-ins off, posture confirmed, approvals denied
# --------------------------------------------------------------------------- #
async def test_the_thread_starts_with_no_environment_and_every_builtin_off(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch, [{"mcp_servers": ["node_repl", "odd.name"], "steps": [{"text": "hi"}]}])
    await fake.adapter(model="gpt-5.5").complete(system="You are Jarvis.", messages=[_user()], tools=TOOLS)
    init = fake.received("initialize")[0]["msg"]["params"]
    assert init["capabilities"]["experimentalApi"] is True
    params = fake.received("thread/start")[0]["msg"]["params"]
    assert params["environments"] == [] and params["ephemeral"] is True
    assert params["sandbox"] == "read-only" and params["approvalPolicy"] == "untrusted"
    # v1.385.1: a turn WITH tools also tells the model who carries them out
    # (tests/test_codex_sandbox_note_v13851.py).
    assert params["baseInstructions"] == f"You are Jarvis.\n\n{P.SANDBOX_NOTE}" and params["model"] == "gpt-5.5"
    config = params["config"]
    for key, value in P.BUILTINS_OFF.items():
        assert config[key] == value, key
    assert config["mcp_servers"] == {"node_repl": {"enabled": False}, "odd.name": {"enabled": False}}
    names = [t["name"] for t in params["dynamicTools"]]
    assert names[:2] == ["lookup_fake", "custom_note"] and len(names[2]) <= 64
    assert all(t["type"] == "function" for t in params["dynamicTools"])
    # The posture was CHECKED, with no model call, before the turn started.
    order = [r["msg"].get("method") for r in fake.rows("recv") if r["msg"].get("method")]
    assert order.index("mcpServerStatus/list") < order.index("turn/start")


async def test_a_placeholder_model_sends_none_and_the_served_model_is_reported(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch, [{"model": "gpt-5.6-terra", "steps": [{"text": "hi"}]}])
    resp = await fake.adapter(model="subscription").complete(system="", messages=[_user()], tools=[])
    assert "model" not in fake.received("thread/start")[0]["msg"]["params"]
    assert resp.extra["codex"]["model"] == "gpt-5.6-terra"


async def test_a_builtin_approval_request_is_denied_and_reported(tmp_path, monkeypatch, caplog):
    fake = Fake(tmp_path, monkeypatch, [{"steps": [
        {"approval": "execCommandApproval"},
        {"approval": "item/commandExecution/requestApproval"},
        {"approval": "item/fileChange/requestApproval"},
        {"text": "ok"},
    ]}])
    with caplog.at_level(logging.WARNING, logger="ironjarvis.codex_native"):
        resp = await fake.adapter().complete(system="", messages=[_user()], tools=[])
    decisions = {r["method"]: r["response"] for r in fake.rows("approval")}
    # Review: ReviewDecision's deny is a STRUCT variant in 0.157.0
    # (protocol.rs `Denied { rejection: String }`); the bare string "denied"
    # this pin first asserted does not deserialize as one.
    assert decisions["execCommandApproval"] == {
        "decision": {"denied": {"rejection": "Iron Jarvis runs every tool itself"}}}
    assert decisions["item/commandExecution/requestApproval"] == {"decision": "decline"}
    assert decisions["item/fileChange/requestApproval"] == {"decision": "decline"}
    assert resp.text == "ok"
    assert resp.extra["codex_builtins"]["denied"] == list(decisions)
    assert "Iron Jarvis runs every tool itself" in caplog.text


@pytest.mark.parametrize("step", [{"unknown_tool": "shell_out"}, {"foreign_call": 0}],
                         ids=["not-offered", "another-thread"])
async def test_a_tool_outside_the_offered_set_is_refused_not_returned(tmp_path, monkeypatch, step):
    fake = Fake(tmp_path, monkeypatch, [{"steps": [step, {"text": "fine"}]}])
    resp = await fake.adapter().complete(system="", messages=[_user()], tools=TOOLS)
    assert resp.text == "fine" and resp.tool_calls == []
    answer = fake.answers()[0]["response"]
    assert answer["success"] is False and "nothing was run" in answer["contentItems"][0]["text"]


# --------------------------------------------------------------------------- #
# 5. the feature check: fall back to text-only, said ONCE
# --------------------------------------------------------------------------- #
def _text_runner(calls: list):
    def runner(argv, stdin, **kw):
        calls.append(argv)
        return 0, "answer from the text path", ""
    return runner


@pytest.mark.parametrize("proc", [
    {"exit_on_start": True},
    {"initialize_error": "unknown capability"},
    {"thread_start_error": "unknown field `dynamicTools`"},
    {"thread_start": {"environments": [{"environmentId": "local", "cwd": "C:\\x"}]}},
    {"thread_start": {"ephemeral": False}},
    {"result": {"sandbox": {"type": "workspaceWrite"}}},
], ids=["no-app-server", "init-refused", "no-dynamic-tools", "has-environment", "not-ephemeral", "not-read-only"])
async def test_a_codex_without_the_shapes_falls_back_to_text_and_says_so_once(tmp_path, monkeypatch, caplog, proc):
    fake = Fake(tmp_path, monkeypatch, [dict(proc, steps=[{"text": "never"}])])
    text_calls: list = []
    adapter = fake.adapter(runner=_text_runner(text_calls))
    assert adapter.capabilities()["tool_use"] is True  # optimistic until a start says otherwise
    with caplog.at_level(logging.WARNING, logger="ironjarvis.codex_native"):
        r1 = await adapter.complete(system="", messages=[_user()], tools=TOOLS)
        r2 = await adapter.complete(system="", messages=[_user()], tools=[])
    assert r1.text == r2.text == "answer from the text path"
    assert len(text_calls) == 2 and text_calls[0][1] == "exec"
    assert len(fake.starts()) == 1, "the verdict is cached: no second app-server start"
    warnings = [r for r in caplog.records if "text-only `codex exec` path" in r.getMessage()]
    assert len(warnings) == 1, [r.getMessage() for r in caplog.records]
    # From now on the router sees a text-only adapter (it wraps prompted tools itself).
    assert adapter.capabilities()["tool_use"] is False
    assert "turn/start" not in [r["msg"].get("method") for r in fake.rows("recv")]
    assert S.POOL.live() == []


async def test_two_calls_failing_the_check_at_once_still_say_so_once(tmp_path, monkeypatch, caplog):
    fake = Fake(tmp_path, monkeypatch, [{"exit_on_start": True, "steps": []}])
    text_calls: list = []
    adapter = fake.adapter(runner=_text_runner(text_calls))
    with caplog.at_level(logging.WARNING, logger="ironjarvis.codex_native"):
        await asyncio.gather(*(adapter.complete(system="", messages=[_user()], tools=[]) for _ in range(3)))
    assert len(text_calls) == 3
    warnings = [r for r in caplog.records if "text-only `codex exec` path" in r.getMessage()]
    assert len(warnings) == 1, [r.getMessage() for r in caplog.records]


async def test_an_mcp_server_still_offering_tools_is_not_run_natively(tmp_path, monkeypatch, caplog):
    fake = Fake(tmp_path, monkeypatch, [{"mcp_tools": {"plugin_pack": {"do_it": {}}}, "steps": [{"text": "x"}]}])
    text_calls: list = []
    adapter = fake.adapter(runner=_text_runner(text_calls))
    with caplog.at_level(logging.WARNING, logger="ironjarvis.codex_native"):
        resp = await adapter.complete(system="", messages=[_user()], tools=[])
        await adapter.complete(system="", messages=[_user()], tools=[])
    assert resp.text == "answer from the text path"
    assert "turn/start" not in [r["msg"].get("method") for r in fake.rows("recv")]
    said = [r for r in caplog.records if "plugin_pack" in r.getMessage()]
    assert len(said) == 1, "said once, not per call"
    # A fact about this config, not this binary: the next call checks again.
    assert len(fake.starts()) == 2


async def test_a_negative_verdict_is_checked_again_after_a_while(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(S, "NEGATIVE_VERDICT_TTL_S", 0.3)
    fake = Fake(tmp_path, monkeypatch, [{"exit_on_start": True, "steps": []},
                                        {"steps": [{"text": "native again"}]}])
    adapter = fake.adapter(runner=_text_runner([]))
    assert (await adapter.complete(system="", messages=[_user()], tools=[])).text == "answer from the text path"
    assert adapter.capabilities()["tool_use"] is False
    await asyncio.sleep(0.4)
    assert adapter.capabilities()["tool_use"] is True
    assert (await adapter.complete(system="", messages=[_user()], tools=[])).text == "native again"


async def test_a_refusal_about_something_else_is_codex_words_not_a_fallback(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch, [{"thread_start_error": "Invalid params: model 'gpt-nope' is not available",
                                         "steps": []}])
    adapter = fake.adapter(model="gpt-nope", runner=_text_runner([]))
    with pytest.raises(ProviderError) as info:
        await adapter.complete(system="", messages=[_user()], tools=[])
    assert "gpt-nope" in str(info.value) and str(info.value).startswith("codex-cli: ")
    assert adapter.capabilities()["tool_use"] is True, "a bad model id never turns native tools off"


# --------------------------------------------------------------------------- #
# 6. usage, cost and the account's rate limits
# --------------------------------------------------------------------------- #
async def test_the_rate_limit_snapshot_rides_the_response(tmp_path, monkeypatch):
    snap = {"limitId": "codex", "planType": "pro", "rateLimitReachedType": None,
            "primary": {"usedPercent": 42, "windowDurationMins": 300, "resetsAt": 1900001000},
            "secondary": {"usedPercent": 9, "windowDurationMins": 10080, "resetsAt": 1900900000},
            "credits": {"hasCredits": False, "unlimited": False, "balance": "0"}}
    fake = Fake(tmp_path, monkeypatch, [{"steps": [{"text": "hi"}, {"rate_limits": snap}]}])
    resp = await fake.adapter().complete(system="", messages=[_user()], tools=[])
    limits = resp.extra["rate_limits"]
    assert limits["planType"] == "pro"
    assert limits["primary"] == {"usedPercent": 42, "windowDurationMins": 300, "resetsAt": 1900001000}
    assert limits["secondary"]["windowDurationMins"] == 10080
    assert A.latest_rate_limits()["default"]["planType"] == "pro"


async def test_without_a_notification_the_start_time_read_is_kept(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch, [{"steps": [{"text": "hi"}]}])
    resp = await fake.adapter().complete(system="", messages=[_user()], tools=[])
    assert resp.extra["rate_limits"]["planType"] == "plus"
    assert resp.extra["rate_limits"]["primary"]["usedPercent"] == 7
    assert fake.received("account/rateLimits/read"), "the account's limits are read with no model call"


async def test_usage_is_priced_as_a_list_price_equivalent(tmp_path, monkeypatch):
    from iron_jarvis.eval import pricing

    fake = Fake(tmp_path, monkeypatch, [{"model": "gpt-5.5", "steps": [
        {"usage": {"inputTokens": 1_000_000, "cachedInputTokens": 0, "outputTokens": 100_000}},
        {"text": "hi"}]}])
    resp = await fake.adapter().complete(system="", messages=[_user()], tools=[])
    assert resp.usage["cost_usd"] == pytest.approx(1.25 + 1.0)
    assert pricing.is_list_price_equivalent("codex-cli", "gpt-5.5") is True
    assert pricing.recorded_cost("codex-cli", "gpt-5.5", 1000, 1000, 0) == 0.0  # an old row stays free


# --------------------------------------------------------------------------- #
# 7. errors are typed
# --------------------------------------------------------------------------- #
def _err(info, message="boom"):
    return {"error": {"message": message, "codexErrorInfo": info}}


async def test_a_usage_limit_is_a_429_with_the_reset_as_retry_after(tmp_path, monkeypatch):
    reset = int(time.time()) + 600
    snap = {"planType": "plus", "rateLimitReachedType": "rateLimitReached",
            "primary": {"usedPercent": 100, "windowDurationMins": 300, "resetsAt": reset}}
    fake = Fake(tmp_path, monkeypatch, [{"steps": [{"rate_limits": snap},
                                                   _err("usageLimitExceeded", "You've hit your usage limit.")]}])
    with pytest.raises(ProviderError) as info:
        await fake.adapter().complete(system="", messages=[_user()], tools=[])
    err = info.value
    assert err.status_code == 429 and "usage limit" in str(err) and str(err).startswith("codex-cli: ")
    assert 550 <= err.retry_after <= 600
    assert S.POOL.live() == []


async def test_a_refused_sign_in_gives_the_sign_in_remedy_and_tells_the_probe(tmp_path, monkeypatch, _clean):
    from iron_jarvis.providers import failure_words

    fake = Fake(tmp_path, monkeypatch, [{"steps": [_err("unauthorized", "token expired")]}])
    with pytest.raises(ProviderError) as info:
        await fake.adapter().complete(system="", messages=[_user()], tools=[])
    assert SIGN_IN_FIX["codex"] in str(info.value) and info.value.status_code == 401
    assert _clean.verdict("codex") is False
    assert failure_words.classify(info.value)[0] == "Codex isn't signed in"


@pytest.mark.parametrize("steps,partial", [
    ([{"die": 1}], False),
    ([{"text": "half an ans"}, {"die": 1}], True),
])
async def test_a_dead_app_server_is_typed_interrupted(tmp_path, monkeypatch, steps, partial):
    from iron_jarvis.providers.router import is_transient_error

    fake = Fake(tmp_path, monkeypatch, [{"steps": steps}])
    got: list[str] = []
    with pytest.raises(ProviderError) as info:
        async for frame in fake.adapter().stream(system="", messages=[_user()], tools=[]):
            if frame["type"] == "text":
                got.append(frame["text"])
    err = info.value
    assert getattr(err, "kind", "") == "interrupted" and getattr(err, "failed_provider", "") == "codex-cli"
    assert bool(getattr(err, "partial", False)) is partial
    assert is_transient_error(err) is (not partial)
    assert ("".join(got) == "half an ans") is partial
    assert str(err).strip()


async def test_an_app_server_that_dies_saying_not_signed_in_gives_the_sign_in_remedy(tmp_path, monkeypatch, _clean):
    fake = Fake(tmp_path, monkeypatch, [{"steps": [
        {"stderr": "Error: Not logged in. Please run `codex login`."}, {"die": 1}]}])
    with pytest.raises(ProviderError) as info:
        await fake.adapter().complete(system="", messages=[_user()], tools=[])
    assert SIGN_IN_FIX["codex"] in str(info.value) and info.value.status_code == 401
    assert _clean.verdict("codex") is False


@pytest.mark.parametrize("info_name,check", [
    ("contextWindowExceeded", "overflow"),
    ("serverOverloaded", "transient"),
    ("badRequest", "permanent"),
])
async def test_other_turn_failures_keep_codex_words_and_their_class(tmp_path, monkeypatch, info_name, check):
    from iron_jarvis.daemon.chat_turn import is_context_overflow
    from iron_jarvis.providers.router import is_transient_error

    fake = Fake(tmp_path, monkeypatch, [{"steps": [_err(info_name, f"said {info_name}")]}])
    with pytest.raises(ProviderError) as info:
        await fake.adapter().complete(system="", messages=[_user()], tools=[])
    err = info.value
    assert f"said {info_name}" in str(err)
    if check == "overflow":
        assert is_context_overflow(err) and not is_transient_error(err)
    elif check == "transient":
        assert is_transient_error(err) and err.status_code == 503
    else:
        assert not is_transient_error(err) and err.status_code == 400


async def test_an_answer_cut_at_the_output_limit_is_max_tokens_not_an_error(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch, [{"steps": [
        {"text": "a long answ"},
        _err("other", "Incomplete response returned, reason: max_output_tokens")]}])
    resp = await fake.adapter().complete(system="", messages=[_user()], tools=[])
    assert resp.finish_reason == "max_tokens" and resp.text == "a long answ"


async def test_a_silent_app_server_hits_the_idle_timeout(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch, [{"steps": [{"hang": 30}]}])
    with pytest.raises(ProviderError) as info:
        await fake.adapter(idle_timeout_s=0.5).complete(system="", messages=[_user()], tools=[])
    assert getattr(info.value, "kind", "") == "timeout" and info.value.transient is True
    assert S.POOL.live() == []


async def test_not_installed_is_said_before_anything_runs():
    with pytest.raises(RuntimeError, match="not installed"):
        await A.CodexNativeAdapter(which=lambda _b: None).complete(system="", messages=[_user()], tools=[])


# --------------------------------------------------------------------------- #
# 8. Iron-Proxy accounts and the environment
# --------------------------------------------------------------------------- #
async def test_a_session_runs_as_the_leased_account_and_retries_on_its_limit(tmp_path, monkeypatch):
    from tests.test_iron_proxy_accounts_v1301 import FakeIronProxy, _homes

    homes = _homes(tmp_path, "A", "B")
    client = FakeIronProxy("openai", homes)
    accounts.set_client_source(lambda: client)
    fake = Fake(tmp_path, monkeypatch, [
        {"steps": [_err("usageLimitExceeded", "You've hit your usage limit.")]},
        {"steps": [{"call": {"tool_index": 0, "args": {"q": "z"}}},
                   {"usage": {"inputTokens": 50, "cachedInputTokens": 0, "outputTokens": 5}},
                   {"text": "answered on B"}]},
    ])
    monkeypatch.setenv("OPENAI_API_KEY", "sk-INHERITED")
    adapter = fake.adapter()
    r1 = await adapter.complete(system="", messages=[_user()], tools=TOOLS)
    assert r1.tool_calls and r1.tool_calls[0].name == "lookup_fake"
    homes_seen = [r["env"].get("CODEX_HOME") for r in fake.starts()]
    assert homes_seen == [homes["A"], homes["B"]], "the limit was reported and the NEXT account ran"
    assert client.signals[0]["status"] == 429 and client.parked == {"A": "rate-limit"}
    assert all("OPENAI_API_KEY" not in r["env"] for r in fake.starts()), "the account's unset list applied"
    # The resume stays on account B (no new pick, no new process).
    r2 = await adapter.complete(system="", messages=_after([_user()], r1, {"lookup_fake": "R"}), tools=TOOLS)
    assert r2.text == "answered on B" and len(fake.starts()) == 2
    assert client.picks == ["A", "B"]
    assert [d["id"] for d in client.done] == ["B", "B"], "each served segment is reported to its account"
    assert client.done[1]["usage"] == {"inputTokens": 50, "outputTokens": 5}


async def test_codex_own_sign_in_variables_pass_through_and_are_named_once(tmp_path, monkeypatch, caplog):
    fake = Fake(tmp_path, monkeypatch, [{"steps": [{"text": "hi"}]}])
    monkeypatch.setenv("CODEX_API_KEY", "sk-SECRET-VALUE")
    with caplog.at_level(logging.INFO, logger="ironjarvis.codex_native"):
        await fake.adapter().complete(system="", messages=[_user()], tools=[])
        await fake.adapter().complete(system="", messages=[_user()], tools=[])
    assert all(r["env"].get("CODEX_API_KEY") == "sk-SECRET-VALUE" for r in fake.starts())
    named = [r for r in caplog.records if "CODEX_API_KEY" in r.getMessage()]
    assert len(named) == 1 and "sk-SECRET-VALUE" not in caplog.text


# --------------------------------------------------------------------------- #
# 9. end to end through the REAL /chat/stream lane: registry.invoke runs it
# --------------------------------------------------------------------------- #
from iron_jarvis.tools.base import Tool, ToolContext, ToolResult  # noqa: E402


class _LaneTool(Tool):
    input_schema = {"type": "object", "properties": {"q": {"type": "string"}}}

    def __init__(self, name: str, output: str) -> None:
        self.name = name
        self.description = f"lane test tool {name}"
        self._output = output
        self.calls: list[dict] = []

    async def execute(self, args, ctx: ToolContext) -> ToolResult:
        self.calls.append(dict(args))
        return ToolResult(ok=True, output=self._output)


def _sse(raw: str) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for block in raw.split("\n\n"):
        event, data = "message", []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
        if data:
            try:
                out.append((event, json.loads("\n".join(data))))
            except ValueError:
                pass
    return out


async def test_end_to_end_the_chat_stream_lane_runs_every_codex_call_through_the_registry(tmp_path, monkeypatch):
    from sqlmodel import select

    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import ToolInvocation
    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.daemon.routes.chat import stream_chat_turn
    from iron_jarvis.daemon.schemas import ChatBody
    from iron_jarvis.providers.manager import ProviderManager
    from iron_jarvis.tools.permissions import PermissionEngine

    look = _LaneTool("lookup_v1365_fake", "LOOKED-UP-42")
    note = _LaneTool("custom:note_v1365_fake", "NOTE-SAVED")
    other = _LaneTool("other_v1365_fake", "MUST-NOT-RUN")  # registered, NOT armed
    fake = Fake(tmp_path, monkeypatch, [{"steps": [
        {"text": "Looking it up."},
        {"call": {"tool": "lookup_v1365_fake", "args": {"q": "answer"}}},
        {"call": {"tool": "custom_note_v1365_fake", "args": {"q": "42"}}},
        {"usage": {"inputTokens": 900, "cachedInputTokens": 0, "outputTokens": 20}},
        {"text": "The answer is 42, noted."},
    ]}])
    app = create_app(str(tmp_path / "home"))
    platform = app.state.platform
    for tool in (look, note, other):
        platform.registry.register(tool)
    platform.permissions = PermissionEngine(
        {**platform.config.permissions, **{t.name: "allow" for t in (look, note, other)}}
    )
    platform.providers.register(
        "codex-cli", lambda model=None: A.CodexNativeAdapter(model=model or "subscription",
                                                             which=lambda _b: fake.shim)
    )
    monkeypatch.setattr(ProviderManager, "_cli_binary_present", staticmethod(lambda b: True))
    monkeypatch.setattr(ProviderManager, "_cli_signed_in", staticmethod(lambda b: True))

    body = ChatBody(messages=[{"role": "user", "content": "look up the answer and note it"}],
                    tools=[look.name, note.name], approval_mode="yolo", provider="codex-cli")
    gen = await stream_chat_turn(platform, {}, body)
    frames = _sse("".join([chunk async for chunk in gen]))
    done = [d for ev, d in frames if ev == "done"]
    errors = [d for ev, d in frames if ev == "error"]
    assert not errors, errors
    assert len(done) == 1
    assert "The answer is 42, noted." in done[0]["reply"]
    # The tools RAN, through the registry, with the model's arguments...
    assert look.calls == [{"q": "answer"}] and note.calls == [{"q": "42"}] and other.calls == []
    # ...and the ledger names the REAL tools (the Codex-side name of the
    # custom tool was custom_note_v1365_fake; the ledger says custom:note...).
    with session_scope(platform.engine) as db:
        rows = [(r.tool, r.ok) for r in db.exec(select(ToolInvocation))
                if r.tool in (look.name, note.name, other.name)]
    assert rows == [(look.name, True), (note.name, True)]
    # ONE app-server served the whole turn; Codex got each tool's output back.
    assert len(fake.starts()) == 1
    answered = [a["response"]["contentItems"][0]["text"] for a in fake.answers()]
    assert "LOOKED-UP-42" in answered[0] and "NOTE-SAVED" in answered[1]
    assert S.POOL.live() == []
    names = [t["name"] for t in fake.received("thread/start")[0]["msg"]["params"]["dynamicTools"]]
    assert "other_v1365_fake" not in names, "only the ARMED tools are offered"
    assert done[0].get("route", {}).get("provider") == "codex-cli"


async def test_end_to_end_an_agent_run_on_codex_cli_completes_a_tool_loop(tmp_path, monkeypatch):
    """The agent runtime's perceive -> act loop (a Session, not chat): every
    step's tool calls run through the registry under the session's id, the
    resumed Codex turn serves every step, and the run completes."""
    from sqlmodel import select

    from iron_jarvis.agents.orchestrator import Orchestrator
    from iron_jarvis.agents.types import AgentDefinition
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import AgentType, SessionStatus, ToolInvocation
    from iron_jarvis.platform import build_platform
    from iron_jarvis.providers.manager import ProviderManager
    from iron_jarvis.tools.permissions import PermissionEngine

    look = _LaneTool("lookup_v1365_fake", "ROW-COUNT=7")
    fake = Fake(tmp_path, monkeypatch, [{"steps": [
        {"call": {"tool": "lookup_v1365_fake", "args": {"q": "rows"}}},
        {"call": {"tool": "lookup_v1365_fake", "args": {"q": "again"}}},
        {"text": "There are 7 rows."},
    ]}])
    p = build_platform(str(tmp_path / "home"))
    p.registry.register(look)
    p.permissions = PermissionEngine({**p.config.permissions, look.name: "allow"})
    p.providers.register(
        "codex-cli", lambda model=None: A.CodexNativeAdapter(model="subscription", which=lambda _b: fake.shim))
    monkeypatch.setattr(ProviderManager, "_cli_binary_present", staticmethod(lambda b: True))
    monkeypatch.setattr(ProviderManager, "_cli_signed_in", staticmethod(lambda b: True))
    orch = Orchestrator(p)
    s = await orch.create_session("count the rows", AgentType.BUILDER, provider="codex-cli")
    defn = AgentDefinition(type=AgentType.BUILDER, system_prompt="t", tools=[look.name])
    row = await asyncio.wait_for(orch.run_session(s.id, defn), timeout=60)
    assert row.status is SessionStatus.COMPLETED, (row.status, row.summary)
    assert "There are 7 rows." in (row.summary or "")
    assert look.calls == [{"q": "rows"}, {"q": "again"}]
    with session_scope(p.engine) as db:
        rows = [(r.tool, r.ok) for r in db.exec(select(ToolInvocation)) if r.session_id == s.id]
    assert rows == [(look.name, True), (look.name, True)]
    assert len(fake.starts()) == 1, "one Codex turn served every step"


async def test_end_to_end_a_tool_codex_was_not_offered_never_runs(tmp_path, monkeypatch):
    """Codex naming a REGISTERED tool the lane did not arm (so it was never
    offered on the thread) runs nothing and leaves no ledger row; the offered
    tool in the same turn still runs."""
    from sqlmodel import select

    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import ToolInvocation
    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.daemon.routes.chat import stream_chat_turn
    from iron_jarvis.daemon.schemas import ChatBody
    from iron_jarvis.providers.manager import ProviderManager
    from iron_jarvis.tools.permissions import PermissionEngine

    look = _LaneTool("lookup_v1365_fake", "LOOKED-UP")
    fake = Fake(tmp_path, monkeypatch, [{"steps": [
        {"unknown_tool": "other_v1365_fake"},
        {"call": {"tool": "lookup_v1365_fake", "args": {"q": "a"}}},
        {"text": "done"},
    ]}])
    app = create_app(str(tmp_path / "home"))
    platform = app.state.platform
    other = _LaneTool("other_v1365_fake", "MUST-NOT-RUN")
    for tool in (look, other):
        platform.registry.register(tool)
    platform.permissions = PermissionEngine(
        {**platform.config.permissions, look.name: "allow", other.name: "allow"})
    platform.providers.register(
        "codex-cli", lambda model=None: A.CodexNativeAdapter(model="subscription", which=lambda _b: fake.shim))
    monkeypatch.setattr(ProviderManager, "_cli_binary_present", staticmethod(lambda b: True))
    monkeypatch.setattr(ProviderManager, "_cli_signed_in", staticmethod(lambda b: True))
    body = ChatBody(messages=[{"role": "user", "content": "look it up"}], tools=[look.name],
                    approval_mode="yolo", provider="codex-cli")
    gen = await stream_chat_turn(platform, {}, body)
    frames = _sse("".join([chunk async for chunk in gen]))
    assert [d for ev, d in frames if ev == "done"]
    assert other.calls == [] and look.calls == [{"q": "a"}]
    with session_scope(platform.engine) as db:
        assert [r.tool for r in db.exec(select(ToolInvocation)) if r.tool == other.name] == []


# --------------------------------------------------------------------------- #
# 10. the protocol module (pure)
# --------------------------------------------------------------------------- #
def test_tool_names_are_safe_bounded_reversible_and_never_codex_own():
    names = P.ToolNames.build(["read_file", "custom:x", "custom_x", "exec", "wait", "a" * 80, "a" * 80 + "b"])
    for ours, theirs in names.forward.items():
        assert len(theirs) <= 64 and P._SAFE.fullmatch(theirs), (ours, theirs)
        assert names.ours(theirs) == ours
    assert names.forward["read_file"] == "read_file" and names.forward["custom_x"] == "custom_x"
    assert names.forward["custom:x"] != "custom_x"
    assert names.forward["exec"] == "ij_exec" and names.forward["wait"] == "ij_wait"
    assert len(set(names.forward.values())) == len(names.forward)
    assert names.ours("exec") is None and names.ours("anything") is None


def test_answered_calls_needs_exactly_the_last_turns_results():
    a = ToolCall(id="call_1", name="t", arguments={})
    b = ToolCall(id="call_2", name="t", arguments={})
    base = [_user(), LLMMessage(role="assistant", content="", tool_calls=[a, b])]
    both = base + [LLMMessage(role="tool", tool_call_id="call_1", content="r1"),
                   LLMMessage(role="tool", tool_call_id="call_2", content="r2")]
    assert P.answered_calls(both) == (["call_1", "call_2"], {"call_1": "r1", "call_2": "r2"})
    assert P.answered_calls(both[:-1]) is None  # one call unanswered
    assert P.answered_calls(both + [_user("more")]) is None  # the conversation moved on
    assert P.answered_calls([_user()]) is None


def test_usage_between_is_the_delta_of_the_thread_total():
    start = {"inputTokens": 100, "cachedInputTokens": 40, "cacheWriteInputTokens": 0, "outputTokens": 10}
    end = {"inputTokens": 350, "cachedInputTokens": 140, "cacheWriteInputTokens": 5, "outputTokens": 25}
    assert P.usage_between(start, end) == {"input_tokens": 250, "output_tokens": 15,
                                           "cache_read_input_tokens": 100, "cache_creation_input_tokens": 5}
    assert P.usage_between(None, None) == {"input_tokens": 0, "output_tokens": 0}


def test_turn_input_replays_the_earlier_conversation_and_asks_the_last_user_turn():
    names = P.ToolNames.build(["custom:x"])
    msgs = [_user("one"), LLMMessage(role="assistant", content="two",
                                     tool_calls=[ToolCall(id="c", name="custom:x", arguments={"k": 1})]),
            LLMMessage(role="tool", tool_call_id="c", name="custom:x", content="three"),
            LLMMessage(role="system", content="extra rules"),
            _user("four"), _user("five")]
    base, items, replayed = P.turn_input("sys", msgs, names)
    assert base == "sys\n\nextra rules" and replayed is True
    assert items[0]["text"].startswith(P.REPLAY_HEADER)
    assert "custom_x" in items[0]["text"] and '{"k": 1}' in items[0]["text"] and "three" in items[0]["text"]
    assert items[1:] == [{"type": "text", "text": "four"}, {"type": "text", "text": "five"}]
    base, items, replayed = P.turn_input("", [_user("only")], names)
    assert items == [{"type": "text", "text": "only"}] and replayed is False


@pytest.mark.parametrize("change,problem", [
    ({}, ""),
    ({"thread": {"id": "t", "environments": None, "ephemeral": True}}, "no environment"),
    ({"thread": {"id": "t", "environments": [], "ephemeral": False}}, "ephemeral"),
    ({"sandbox": {"type": "dangerFullAccess"}}, "read-only"),
    ({"approvalPolicy": "never"}, "approvals"),
])
def test_start_problem_refuses_any_posture_codex_did_not_confirm(change, problem):
    result = {"thread": {"id": "t", "environments": [], "ephemeral": True},
              "sandbox": {"type": "readOnly"}, "approvalPolicy": "untrusted"}
    result.update(change)
    got = P.start_problem(result)
    assert (problem in got and got) if problem else got == ""


def test_the_builtins_off_config_names_the_switches_read_in_the_source():
    """The keys the 0.157.0 source reads (features/src/lib.rs, config/mod.rs).
    A renamed or dropped switch fails here first."""
    off = P.BUILTINS_OFF
    for feature in ("shell_tool", "unified_exec", "view_image", "image_generation", "memories", "goals",
                    "apps", "plugins", "tool_suggest", "browser_use", "computer_use", "in_app_browser",
                    "hooks", "sleep_tool", "multi_agent", "multi_agent_v2", "skill_mcp_dependency_install",
                    "skill_search"):
        assert off[f"features.{feature}"] is False, feature
    assert off["agents.enabled"] is False and off["web_search"] == "disabled" and off["notify"] == []
    assert off["tools.experimental_request_user_input.enabled"] is False
    assert off["tools.update_plan.enabled"] is False
    assert off["features.code_mode"] == {"enabled": False, "direct_only_tool_namespaces": ["functions"]}
    params = P.thread_start_params(base_instructions="", specs=[], cwd="C:\\x", model=None, mcp_servers=[])
    assert params["environments"] == [] and "mcp_servers" not in params["config"]
    assert "baseInstructions" not in params and "model" not in params


def test_the_manager_serves_codex_cli_natively():
    from iron_jarvis.providers.manager import ProviderManager

    adapter = ProviderManager._make_subprocess_cli(object.__new__(ProviderManager), "codex-cli", None)
    assert isinstance(adapter, A.CodexNativeAdapter)
    assert adapter.capabilities()["tool_use"] is True and adapter.model == "subscription"


# =========================================================================== #
# REVIEW (IJ harness phase 2) — each pin is red without its fix
# =========================================================================== #
import threading  # noqa: E402 — the review section's own needs

from iron_jarvis.core.events import EventBus  # noqa: E402
from iron_jarvis.providers import egress  # noqa: E402
from iron_jarvis.providers import router as R  # noqa: E402
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMResponse  # noqa: E402


def _kill_pids(pids: list[int]) -> None:
    for pid in pids:
        try:
            psutil.Process(pid).kill()
        except psutil.Error:
            pass


async def test_review_stop_all_stops_every_live_session_and_its_tree(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch, [{"steps": [{"call": {"tool_index": 0, "args": {"q": "x"}}}]}],
                pids=True)
    await fake.adapter().complete(system="", messages=[_user()], tools=TOOLS)
    assert [s.state for s in S.POOL.live()] == ["paused"]
    pids = [int(p) for p in fake.pids_path.read_text().split()] + _session_pids()
    try:
        assert await asyncio.to_thread(S.POOL.stop_all) == 1
        assert S.POOL.live() == []
        assert await _gone(pids) == [], "a parked app-server outlived the shutdown"
    finally:
        _kill_pids(pids)


def test_review_the_daemon_stops_live_codex_sessions_at_shutdown(tmp_path, monkeypatch):
    """A parked app-server waits up to 10 minutes for tool results; the
    lifespan's shutdown must stop it like the REPL children and kept jobs."""
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    calls: list[int] = []
    real = S.Pool.stop_all

    def spy(self):
        n = real(self)
        calls.append(n)
        return n

    monkeypatch.setattr(S.Pool, "stop_all", spy)
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        assert client.get("/health").status_code == 200
        assert calls == []
    assert calls == [0], "the lifespan shutdown must stop the live Codex sessions"


async def test_review_a_stop_while_evicting_a_parked_session_leaks_no_pool_slot(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "MAX_LIVE_SESSIONS", 1)
    fake = Fake(tmp_path, monkeypatch, [{"steps": [{"call": {"tool_index": 0, "args": {}}}]},
                                        {"steps": [{"text": "B"}]}])
    adapter = fake.adapter()
    await adapter.complete(system="", messages=[_user("A")], tools=TOOLS)
    (victim,) = S.POOL.live()
    killing = threading.Event()
    real_kill = S.CodexSession.kill

    def slow_kill(self, *, graceful=False):
        if self is victim:
            killing.set()
            time.sleep(1.0)
        return real_kill(self, graceful=graceful)

    monkeypatch.setattr(S.CodexSession, "kill", slow_kill)
    task = asyncio.create_task(adapter.complete(system="", messages=[_user("B")], tools=TOOLS))
    assert await asyncio.to_thread(killing.wait, 15)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # The new session was admitted before the eviction: a Stop there must not
    # leave it holding the only slot ("streaming", never parked, never reaped).
    assert S.POOL.live() == []


@pytest.mark.parametrize("method", ["config/read", "thread/start", "mcpServerStatus/list"])
async def test_review_a_slow_start_falls_back_for_that_call_and_caches_nothing(tmp_path, monkeypatch, method):
    # Only THIS request is given a short clock (initialize keeps its 30 s: a
    # loaded runner can take seconds to start the fake's interpreter).
    real_request = S.CodexSession.request

    async def short_for_target(self, m, params, timeout):
        return await real_request(self, m, params, 0.5 if m == method else timeout)

    monkeypatch.setattr(S.CodexSession, "request", short_for_target)
    fake = Fake(tmp_path, monkeypatch, [{"hang_before": {method: 30}, "steps": [{"text": "never"}]},
                                        {"steps": [{"text": "native"}]}])
    adapter = fake.adapter(runner=_text_runner([]))
    r1 = await adapter.complete(system="", messages=[_user()], tools=TOOLS)
    assert r1.text == "answer from the text path"
    # A busy PC is not a fact about this Codex: a cached negative verdict would
    # make every explicit codex-cli chat a TEXT-ONLY turn for 10 minutes.
    assert S.verdict() is None
    assert adapter.capabilities()["tool_use"] is True
    r2 = await adapter.complete(system="", messages=[_user()], tools=[])
    assert r2.text == "native"


async def test_review_the_working_directory_is_made_off_the_event_loop(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch, [{"steps": [{"text": "hi"}]}])
    seen: list[int] = []
    real = S.stable_workdir

    def spy() -> str:
        seen.append(threading.get_ident())
        return real()

    monkeypatch.setattr(S, "stable_workdir", spy)
    loop_thread = threading.get_ident()
    assert (await fake.adapter().complete(system="", messages=[_user()], tools=[])).text == "hi"
    assert seen and loop_thread not in seen, "a mkdir ran on the event loop"


def test_review_every_builtin_approval_is_a_deny_in_codex_0157_shapes():
    """The deny each request's 0.157.0 response type takes: v2 decisions are
    strings ("decline"), the legacy ``ReviewDecision`` deny is the STRUCT
    variant ``Denied { rejection }`` (protocol/src/protocol.rs), a permissions
    request is answered with an empty grant."""
    d = P.APPROVAL_DENIALS
    assert d["item/commandExecution/requestApproval"] == {"decision": "decline"}
    assert d["item/fileChange/requestApproval"] == {"decision": "decline"}
    for legacy in ("execCommandApproval", "applyPatchApproval"):
        decision = d[legacy]["decision"]
        assert isinstance(decision, dict) and list(decision) == ["denied"], legacy
        assert isinstance(decision["denied"].get("rejection"), str) and decision["denied"]["rejection"]
    assert d["item/permissions/requestApproval"] == {"permissions": {}, "scope": "turn"}


# --------------------------------------------------------------------------- #
# Review: what the router does now that codex-cli calls tools natively
# --------------------------------------------------------------------------- #
class _CannedCodex(A.CodexNativeAdapter):
    """The REAL codex-cli adapter's capabilities; a canned answer."""

    def __init__(self) -> None:
        super().__init__(model="subscription", which=lambda _b: None)
        self.calls = 0

    async def complete(self, *, system, messages, tools, **kw):
        self.calls += 1
        return LLMResponse(text="from codex", tool_calls=[], usage={})

    async def stream(self, *, system, messages, tools, **kw):
        self.calls += 1
        yield {"type": "final", "response": LLMResponse(text="from codex", tool_calls=[], usage={})}


class _Plain(LLMAdapter):
    def __init__(self, provider, *, fail=None, endpoint="", tools=True):
        self.provider, self.model = provider, f"{provider}-m"
        self._endpoint, self.fail, self.tools_ok = endpoint, fail, tools
        self.calls = 0

    def capabilities(self):
        return {"provider": self.provider, "model": self.model, "tool_use": self.tools_ok, "vision": True}

    async def complete(self, *, system, messages, tools, **kw):
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        return LLMResponse(text=f"from {self.provider}", tool_calls=[], usage={})

    async def stream(self, *, system, messages, tools, **kw):
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        yield {"type": "final", "response": LLMResponse(text=f"from {self.provider}", tool_calls=[], usage={})}


class _Mgr:
    def __init__(self, adapters):
        self.adapters = adapters

    def available(self, p):
        return p in self.adapters

    def has_available_api_provider(self):
        return True

    def runtime_provider_names(self):
        return []

    def get(self, p, m=None):
        return self.adapters[p]


async def _route(router, lane: str, provider: str) -> tuple[str, str, str]:
    """``(provider, reason, from)`` of one tool-carrying request."""
    kw = dict(provider=provider, system="s", messages=[_user()], tools=TOOLS)
    if lane == "complete":
        res = await router.complete(**kw)
        return res.provider, res.reason, res.from_provider
    final = [f async for f in router.stream(**kw) if f.get("type") == "final"][-1]
    return final["provider"], final["reason"], final["from"]


@pytest.fixture()
def _no_backoff(monkeypatch):
    async def _sleep(_s):
        return None

    monkeypatch.setattr(R.asyncio, "sleep", _sleep)


@pytest.mark.parametrize("lane", ["complete", "stream"])
async def test_review_a_cloud_primarys_rate_limit_on_a_tool_request_fails_over_to_codex_and_says_so(
    lane, _no_backoff
):
    """NEW in phase 2 (codex-cli was skipped as text-only): sideways failover
    of a CLOUD primary's transient failure may land on codex-cli. Cloud->cloud
    arbitrage is the documented design (v1.162.0 protects LOCAL primaries
    only); the route says failover and from whom."""
    anth = _Plain("anthropic", fail=ProviderError("overloaded", status_code=429))
    codex = _CannedCodex()
    grok = _Plain("grok-cli", tools=False)
    r = R.ModelRouter(_Mgr({"anthropic": anth, "codex-cli": codex, "grok-cli": grok}), "anthropic",
                      EventBus(), local_policy=lambda: "refuse")
    assert await _route(r, lane, "anthropic") == ("codex-cli", "failover", "anthropic")
    assert codex.calls == 1 and grok.calls == 0


@pytest.mark.parametrize("lane", ["complete", "stream"])
async def test_review_local_only_and_a_local_primary_never_reach_codex(lane, _no_backoff):
    local = "http://127.0.0.1:11434/v1"
    # (1) A local primary that never answered refuses (v1.162.0) — codex, now
    # tool-capable and the default, does not stand in.
    dead = _Plain("ollama", endpoint=local, fail=ConnectionError("refused"))
    codex = _CannedCodex()
    r = R.ModelRouter(_Mgr({"ollama": dead, "codex-cli": codex}), "codex-cli", EventBus(),
                      local_policy=lambda: "refuse", deadline_s=0.01)
    with pytest.raises(Exception):  # noqa: B017 — the refusal's own class
        await _route(r, lane, "ollama")
    assert codex.calls == 0
    # (2) Under LOCAL ONLY, a local primary that answered an error with the
    # failover policy on still never moves to codex (the default or sideways)…
    busy = _Plain("ollama", endpoint=local, fail=ProviderError("busy", status_code=503))
    r = R.ModelRouter(_Mgr({"ollama": busy, "codex-cli": codex}), "codex-cli", EventBus(),
                      local_policy=lambda: "failover", deadline_s=0.01)
    with egress.local_only_scope(True):
        with pytest.raises(Exception):  # noqa: B017
            await _route(r, lane, "ollama")
    assert codex.calls == 0
    # …anti-vacuity: outside the scope codex (tool-capable now) stands in.
    assert (await _route(r, lane, "ollama"))[:2] == ("codex-cli", "failover")
    assert codex.calls == 1


@pytest.mark.parametrize("where", ["new-session", "resume"])
async def test_review_a_limit_after_text_reached_the_caller_is_not_retried_on_another_account(
    tmp_path, monkeypatch, where
):
    """v1.301.0: a retry on the next account happens only while NOTHING of
    this call reached the caller. Text then a usage limit: the limit is still
    reported, the error leaves marked no_failover, and no second account runs."""
    from tests.test_iron_proxy_accounts_v1301 import FakeIronProxy, _homes

    homes = _homes(tmp_path, "A", "B")
    client = FakeIronProxy("openai", homes)
    accounts.set_client_source(lambda: client)
    tail = [{"text": "half an answer"}, _err("usageLimitExceeded", "You've hit your usage limit.")]
    first = tail if where == "new-session" else [{"call": {"tool_index": 0, "args": {"q": "z"}}}, *tail]
    fake = Fake(tmp_path, monkeypatch, [{"steps": first}, {"steps": [{"text": "from B"}]}])
    adapter = fake.adapter()
    messages = [_user()]
    if where == "resume":
        r1 = await adapter.complete(system="", messages=messages, tools=TOOLS)
        messages = _after(messages, r1, {"lookup_fake": "R"})
    got: list[str] = []
    with pytest.raises(ProviderError) as info:
        async for frame in adapter.stream(system="", messages=messages, tools=TOOLS):
            if frame["type"] == "text":
                got.append(frame["text"])
    assert "".join(got) == "half an answer"
    assert len(fake.starts()) == 1, "text had reached the caller: no second account may run"
    assert getattr(info.value, "no_failover", False) is True
    assert client.signals and client.signals[0]["status"] == 429, "the limit is still reported"
    assert S.POOL.live() == []


async def test_review_end_to_end_the_post_chat_lane_resumes_one_codex_turn_through_the_registry(
    tmp_path, monkeypatch
):
    """POST /chat (``chat_turn.run_chat_turn``) — the lane the stream test
    above does not drive: two rounds of tool calls, each run by the registry,
    each answer RESUMES the same paused Codex turn (one app-server)."""
    from sqlmodel import select

    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import ToolInvocation
    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.daemon.chat_turn import run_chat_turn
    from iron_jarvis.daemon.schemas import ChatBody
    from iron_jarvis.providers.manager import ProviderManager
    from iron_jarvis.tools.permissions import PermissionEngine

    look = _LaneTool("lookup_v1365_fake", "LOOKED-UP-42")
    note = _LaneTool("custom:note_v1365_fake", "NOTE-SAVED")
    fake = Fake(tmp_path, monkeypatch, [{"steps": [
        {"call": {"tool": "lookup_v1365_fake", "args": {"q": "answer"}}},
        {"call": {"tool": "custom_note_v1365_fake", "args": {"q": "42"}}},
        {"text": "The answer is 42, noted."},
    ]}])
    app = create_app(str(tmp_path / "home"))
    platform = app.state.platform
    for tool in (look, note):
        platform.registry.register(tool)
    platform.permissions = PermissionEngine(
        {**platform.config.permissions, **{t.name: "allow" for t in (look, note)}}
    )
    platform.providers.register(
        "codex-cli", lambda model=None: A.CodexNativeAdapter(model=model or "subscription",
                                                             which=lambda _b: fake.shim)
    )
    monkeypatch.setattr(ProviderManager, "_cli_binary_present", staticmethod(lambda b: True))
    monkeypatch.setattr(ProviderManager, "_cli_signed_in", staticmethod(lambda b: True))

    body = ChatBody(messages=[{"role": "user", "content": "look up the answer and note it"}],
                    tools=[look.name, note.name], approval_mode="yolo", provider="codex-cli")
    out = await run_chat_turn(platform, {}, body)
    assert "The answer is 42, noted." in out["reply"]
    assert look.calls == [{"q": "answer"}] and note.calls == [{"q": "42"}]
    with session_scope(platform.engine) as db:
        rows = [(r.tool, r.ok) for r in db.exec(select(ToolInvocation))
                if r.tool in (look.name, note.name)]
    assert rows == [(look.name, True), (note.name, True)]
    assert len(fake.starts()) == 1, "each round resumed the same Codex turn"
    answered = [a["response"]["contentItems"][0]["text"] for a in fake.answers()]
    assert "LOOKED-UP-42" in answered[0] and "NOTE-SAVED" in answered[1]
    assert S.POOL.live() == []


def test_the_app_servers_last_output_is_masked_before_it_reaches_the_log():
    """v1.365.0 (Phase 2 review finding 8): `_gone` logged the raw tail of
    Codex's stderr; a refused login or a crash can print a token there."""
    from iron_jarvis.providers.adapters.codex_native.adapter import stderr_for_log

    key = "sk-proj-" + "A1b2C3d4E5f6G7h8J9k0" * 3
    said = "error: request failed\nAuthorization: Bearer " + key + "\n" + "x" * 50
    shown = stderr_for_log(said)
    assert key not in shown
    assert "request failed" in shown or len(shown) <= 400
    assert len(stderr_for_log("y" * 10_000)) <= 400
