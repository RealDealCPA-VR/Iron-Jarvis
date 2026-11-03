"""v1.427.0 (D4): ONE ``claude`` process across a coding reply's steps.

The product-path benchmark (12 hard tasks x 3, Sonnet): Iron Jarvis 36/36 =
Claude Code 36/36, but 50.9 s against 40.1 s a task and 1.6 M against 0.6 M
uncached input tokens — every step started a new ``claude`` and replayed the
conversation. Inside an OPEN turn scope a claude-cli call now keeps its process:
the relay HOLDS Claude Code's follow-up after it denies the tools itself, puts
the REAL results in place of the denials (on every later request too), and lets
it go; a text answer parks the process on stdin for the finish note. Anything
else is a miss and a new process replays the conversation, as before.

Driven through the REAL adapter, the REAL transport and the REAL relay against
``fixtures/fake_claude_live_v1427.py`` (the live protocol, started through a
shim as npm starts the real one) and a LOOPBACK fake Anthropic that records
every body that reached it. Nothing reaches the network.
"""

from __future__ import annotations

import asyncio
import contextlib
import http.client
import json
import logging
import os
import stat
import sys
import tempfile
import threading
import time
import types
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import psutil
import pytest

from iron_jarvis.harness import helpers as H
from iron_jarvis.iron_proxy import accounts
from iron_jarvis.providers import budget, cli_auth, turn_scope
from iron_jarvis.providers.adapters import subprocess_cli as sc
from iron_jarvis.providers.adapters.base import LLMMessage
from iron_jarvis.providers.adapters.claude_native import admission as adm_mod
from iron_jarvis.providers.adapters.claude_native import live, transport
from iron_jarvis.providers.cli_auth import CliAuthProbe

FAKE = Path(__file__).parent / "fixtures" / "fake_claude_live_v1427.py"

TOOLS = [
    {"name": "read_file", "description": "Read a file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
]
MORE_TOOLS = TOOLS + [
    {"name": "write_file", "description": "Write a file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}},
]
DENIAL = "Permission to use"


# --------------------------------------------------------------------------- #
# the fake Anthropic: answers by STEP (= assistant messages in the request)
# --------------------------------------------------------------------------- #
def _results_in(body: dict) -> list[str]:
    """Every tool_result content of a request, in order (a list content: its text)."""
    out = []
    for m in body.get("messages") or []:
        if m.get("role") != "user" or not isinstance(m.get("content"), list):
            continue
        for b in m["content"]:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                c = b.get("content")
                if isinstance(c, list):
                    c = "".join(str(x.get("text", "")) for x in c if isinstance(x, dict))
                out.append(str(c))
    return out


def _sse(events: list[dict]) -> bytes:
    return "".join("data: " + json.dumps(e) + "\n\n" for e in events).encode()


class Upstream:
    """``steps``: ``("tools", [(name, input), ...])`` or ``("text", prefix)`` per
    step; a text answer is ``prefix`` + every tool result the request carries.
    A request whose first user text contains ``SLOW`` waits for ``release``."""

    def __init__(self, steps: list) -> None:
        self.steps = steps
        self.bodies: list[dict] = []
        #: Statuses to answer first, in order (an auth refusal body), then 200s.
        self.statuses: list[int] = []
        #: A step answered ONCE with a 429 (a rate-limit body) instead.
        self.limited_steps: set[int] = set()
        #: v1.427.0 fix: a step with two or more tools pauses this long after the
        #: FIRST tool's block is complete (a long second input still streaming).
        self.pause_s = 0.0
        self.reached = threading.Event()
        self.release = threading.Event()
        owner = self

        class Peer(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args) -> None:
                pass

            def do_POST(self) -> None:
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                body = json.loads(raw)
                owner.bodies.append(body)
                if "SLOW" in json.dumps(body["messages"][:1]):
                    owner.reached.set()
                    owner.release.wait(60)
                step = sum(1 for m in body.get("messages") or [] if m.get("role") == "assistant")
                if step in owner.limited_steps:
                    owner.limited_steps.discard(step)
                    data = json.dumps({"type": "error", "error": {
                        "type": "rate_limit_error", "message": "This request would exceed your limit"}}).encode()
                    self.send_response(429)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("retry-after", "7")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if owner.statuses:
                    status = owner.statuses.pop(0)
                    data = json.dumps({"type": "error", "error": {
                        "type": "authentication_error", "message": "OAuth token has expired"}}).encode()
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if owner.pause_s:
                    events = owner.answer_events(body)
                    data = _sse(events)
                    tool_stops = [i for i, e in enumerate(events) if e["type"] == "content_block_stop"
                                  and events[i - 2].get("content_block", {}).get("type") == "tool_use"]
                    # The model is still writing the SECOND tool's input after
                    # the first tool's block is complete.
                    cut = len(_sse(events[:tool_stops[0] + 1])) if len(tool_stops) >= 2 else len(data)
                else:
                    data = owner.answer(body)  # (a subclass may answer in its own words)
                    cut = len(data)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data[:cut])
                self.wfile.flush()
                if cut < len(data):
                    time.sleep(owner.pause_s)
                    self.wfile.write(data[cut:])
                    self.wfile.flush()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Peer)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def answer(self, body: dict) -> bytes:
        return _sse(self.answer_events(body))

    def answer_events(self, body: dict) -> list[dict]:
        step = sum(1 for m in body.get("messages") or [] if m.get("role") == "assistant")
        kind, payload = self.steps[min(step, len(self.steps) - 1)]
        usage = {"input_tokens": 10, "output_tokens": 1, "cache_read_input_tokens": 4,
                 "cache_creation_input_tokens": 2}
        events = [{"type": "message_start", "message": {
            "id": f"msg_{uuid.uuid4().hex[:8]}", "type": "message", "role": "assistant",
            "model": "fake", "content": [], "stop_reason": None, "usage": usage}}]
        if kind == "tools":
            for i, (name, args) in enumerate(payload):
                events += [
                    {"type": "content_block_start", "index": i, "content_block": {
                        "type": "tool_use", "id": f"toolu_{step}{i}_{uuid.uuid4().hex[:8]}",
                        "name": "mcp__ij__" + name, "input": {}}},
                    {"type": "content_block_delta", "index": i,
                     "delta": {"type": "input_json_delta", "partial_json": json.dumps(args)}},
                    {"type": "content_block_stop", "index": i},
                ]
            stop = "tool_use"
        else:
            text = payload + " " + "|".join(_results_in(body))
            events += [
                {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
                {"type": "content_block_stop", "index": 0},
            ]
            stop = "end_turn"
        events += [{"type": "message_delta", "delta": {"stop_reason": stop}, "usage": {"output_tokens": 7}},
                   {"type": "message_stop"}]
        return events

    def stop(self) -> None:
        self.release.set()
        self.server.shutdown()
        self.thread.join(5)
        self.server.server_close()


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    for key in [k for k in os.environ if k.startswith("ANTHROPIC_")] + list(transport.BACKEND_SWITCHES):
        monkeypatch.delenv(key, raising=False)
    for key in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy",
                "API_TIMEOUT_MS"):  # (a Claude Code session running the suite sets it)
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(live.ENV_SWITCH, "on")
    monkeypatch.setattr(cli_auth, "DEFAULT_PROBE", CliAuthProbe(which=lambda b: None))
    monkeypatch.setattr(transport, "DEFAULT_PROBE", cli_auth.DEFAULT_PROBE)
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    monkeypatch.setenv("FAKE_LIVE_LOG", str(tmp_path / "starts.jsonl"))
    adm_mod.POOL.clear()
    live.stop_all()
    yield
    live.stop_all()
    adm_mod.POOL.clear()


@pytest.fixture
def upstream():
    made: list[Upstream] = []

    def make(steps) -> Upstream:
        up = Upstream(steps)
        made.append(up)
        return up

    yield make
    for up in made:
        up.stop()


@pytest.fixture
def trace(monkeypatch):
    rows: list = []
    monkeypatch.setattr(transport, "TRACE", lambda cid, ph, ms, info: rows.append((ph, dict(info))))
    return rows


def _shim(tmp_path: Path) -> str:
    if os.name == "nt":
        shim = tmp_path / "claude.cmd"
        shim.write_text(f'@"{sys.executable}" "{FAKE}" %*\r\n', encoding="utf-8")
    else:
        shim = tmp_path / "claude"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n', encoding="utf-8")
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    return str(shim)


def _adapter(tmp_path: Path, up: Upstream, model: str = "claude-sonnet-5") -> sc.ClaudeCliAdapter:
    shim = _shim(tmp_path)
    return sc.make_claude_cli(model=model, which=lambda _b: shim,
                              env=dict(os.environ, ANTHROPIC_BASE_URL=up.url))


def _starts() -> list[dict]:
    path = Path(os.environ["FAKE_LIVE_LOG"])
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _max_turns(start: dict) -> str:
    argv = start["argv"]
    return argv[argv.index("--max-turns") + 1]


def _alive(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


async def _gone(pid: int, within: float = 30.0) -> bool:
    deadline = time.monotonic() + within
    while _alive(pid):
        if time.monotonic() > deadline:
            return False
        await asyncio.sleep(0.05)
    return True


def _user(text: str = "go") -> LLMMessage:
    return LLMMessage(role="user", content=text)


@contextlib.asynccontextmanager
async def _scope():
    scope, token = turn_scope.open_scope()
    try:
        yield scope
    finally:
        await turn_scope.close(scope)
        turn_scope.reset(token)


def _answer(msgs: list, resp, results: dict | None = None) -> list:
    """The lane's bookkeeping after a step: the assistant turn (with its carrier)
    and the REAL result of every call."""
    msgs = msgs + [LLMMessage(role="assistant", content=resp.text, tool_calls=resp.tool_calls,
                              raw_blocks=resp.raw_blocks)]
    for tc in resp.tool_calls:
        text = (results or {}).get(tc.id) or f"REAL-{tc.arguments.get('path')}"
        msgs.append(LLMMessage(role="tool", content=text, tool_call_id=tc.id, name=tc.name))
    return msgs


async def _call(ad, msgs, *, tools=TOOLS, system="sys", reasoning=""):
    return await ad.complete(system=system, messages=msgs, tools=tools, reasoning=reasoning)


async def _reply(ad, msgs, **kw):
    """The lane's loop to the end: ``(msgs, final, responses)``."""
    responses = []
    while True:
        resp = await _call(ad, msgs, **kw)
        responses.append(resp)
        if not resp.tool_calls:
            return msgs, resp, responses
        msgs = _answer(msgs, resp)


def _phases(rows: list, name: str) -> list[dict]:
    return [info for ph, info in rows if ph == name]


THREE = [("tools", [("read_file", {"path": "a"})]), ("tools", [("read_file", {"path": "b"})]),
         ("text", "Done")]


# --------------------------------------------------------------------------- #
# 1. a 3-step reply in an open scope: ONE process, the real results upstream
# --------------------------------------------------------------------------- #
async def test_a_three_step_reply_runs_one_process_and_sends_only_real_results(tmp_path, upstream, trace):
    up = upstream(THREE)
    ad = _adapter(tmp_path, up)
    async with _scope():
        _, final, responses = await _reply(ad, [_user("fix it")])
        starts = _starts()
        assert len(starts) == 1, "every step after the first continued the same process"
        assert _max_turns(starts[0]) == str(live.LIVE_MAX_TURNS)
        assert starts[0]["api_timeout_ms"] == live.API_TIMEOUT_MS == "3600000"
        assert len(up.bodies) == 3
        for body in up.bodies:
            assert DENIAL not in json.dumps(body), "a denial reached Anthropic"
        assert _results_in(up.bodies[1]) == ["REAL-a"]
        # re-application: step 3's request (the CLI's whole history again) still
        # carries step 1's REAL result
        assert _results_in(up.bodies[2]) == ["REAL-a", "REAL-b"]
        assert final.text == "Done REAL-a|REAL-b" and final.finish_reason == "stop"
        # The CLI's own cache_control stays where it put it (no pin in live mode).
        last = up.bodies[1]["messages"][-1]["content"][-1]
        assert last["type"] == "tool_result" and last["cache_control"] == {"type": "ephemeral"}
        assert "is_error" not in last
        for resp in responses:
            assert "cost_usd" not in resp.usage, "a live round never reports the query's cost"
            assert resp.usage["input_tokens"] == 16 and resp.usage["output_tokens"] == 7
        assert [r.finish_reason for r in responses] == ["tool_use", "tool_use", "stop"]
        pid = starts[0]["pid"]
        assert _alive(pid), "the text answer parks the process for the finish note"
        assert [s.state for s in live.live_sessions()] == ["parked_done"]
    assert await _gone(pid), "closing the scope ends the kept process"
    assert live.live_sessions() == []
    assert [i.get("reason") for i in _phases(trace, "live_new")] == ["fresh"]
    assert [i.get("reason") for i in _phases(trace, "live_resume")] == ["tools", "tools"]
    assert _phases(trace, "live_miss") == []


async def test_a_parallel_batch_resumes(tmp_path, upstream):
    up = upstream([("tools", [("read_file", {"path": "a"}), ("read_file", {"path": "b"})]),
                   ("text", "Done")])
    ad = _adapter(tmp_path, up)
    async with _scope():
        _, final, responses = await _reply(ad, [_user()])
        assert len(_starts()) == 1
        assert len(responses[0].tool_calls) == 2
        assert _results_in(up.bodies[1]) == ["REAL-a", "REAL-b"]
        assert final.text == "Done REAL-a|REAL-b"
        assert all(DENIAL not in json.dumps(b) for b in up.bodies)


async def test_the_finish_note_continues_the_parked_process(tmp_path, upstream, trace):
    up = upstream([("tools", [("read_file", {"path": "a"})]), ("text", "Done"), ("text", "Checked")])
    ad = _adapter(tmp_path, up)
    async with _scope():
        msgs, final, _ = await _reply(ad, [_user()])
        msgs = msgs + [LLMMessage(role="assistant", content=final.text, raw_blocks=final.raw_blocks),
                       _user("FINISH CHECK. note")]
        note = await _call(ad, msgs)
        assert len(_starts()) == 1, "the note was written to the kept process's stdin"
        assert note.text == "Checked REAL-a" and "cost_usd" not in note.usage
        last = up.bodies[-1]["messages"][-1]
        assert last["role"] == "user" and last["content"][-1]["text"] == "FINISH CHECK. note"
        assert all(DENIAL not in json.dumps(b) for b in up.bodies)
    assert [i.get("reason") for i in _phases(trace, "live_resume")] == ["tools", "done"]


async def test_blocks_after_the_results_ride_the_held_request_and_every_later_one(tmp_path, upstream):
    """A steer note at a round boundary joins the tool results' user turn; the CLI
    never holds it, so the relay adds it after the results on every request."""
    up = upstream(THREE)
    ad = _adapter(tmp_path, up)
    async with _scope():
        msgs = [_user()]
        first = await _call(ad, msgs)
        msgs = _answer(msgs, first) + [_user("steer: also look at c")]
        second = await _call(ad, msgs)
        msgs = _answer(msgs, second)
        final = await _call(ad, msgs)
        assert len(_starts()) == 1
        for body in up.bodies[1:]:
            turn = body["messages"][2]["content"]
            assert [b["type"] for b in turn] == ["tool_result", "text"]
            assert turn[0]["content"] == "REAL-a" and turn[1]["text"] == "steer: also look at c"
        assert final.text == "Done REAL-a|REAL-b"


# --------------------------------------------------------------------------- #
# 2. every miss falls back to a new process and still answers correctly
# --------------------------------------------------------------------------- #
class _Lease:
    def __init__(self, home: str) -> None:
        self.env_set = {"CLAUDE_CONFIG_DIR": home}
        self.env_unset = ()
        self.profile_id = home


MISSES = ["edited", "tools", "system", "model", "effort", "account", "dead", "reaped",
          "partial", "two-frames"]


@pytest.mark.parametrize("miss", MISSES)
async def test_a_miss_starts_a_new_process_that_replays_and_answers(tmp_path, upstream, trace, monkeypatch, miss):
    steps = THREE
    if miss == "partial":
        steps = [("tools", [("read_file", {"path": "a"}), ("read_file", {"path": "b"})]),
                 ("tools", [("read_file", {"path": "c"})]), ("text", "Done")]
    up = upstream(steps)
    lease = {"home": str(tmp_path / "account-a")}
    if miss == "account":
        monkeypatch.setattr(accounts, "lease", lambda _p: _Lease(lease["home"]))

        async def _reported(*a, **k):
            return None

        monkeypatch.setattr(accounts, "report_success_async", _reported)
    ad = _adapter(tmp_path, up)
    kw: dict = {}
    async with _scope():
        msgs = [_user("fix it")]
        first = await _call(ad, msgs)
        msgs = _answer(msgs, first)
        old = _starts()[0]["pid"]
        if miss == "edited":
            msgs[0] = _user("fix it, please")
        elif miss == "tools":
            kw["tools"] = MORE_TOOLS
        elif miss == "system":
            kw["system"] = "another system"
        elif miss == "model":
            ad = _adapter(tmp_path, up, model="claude-haiku-4-5")
        elif miss == "effort":
            kw["reasoning"] = "high"
        elif miss == "account":
            lease["home"] = str(tmp_path / "account-b")
        elif miss == "dead":
            [s] = live.live_sessions()
            psutil.Process(old).kill()
            deadline = time.monotonic() + 30
            while s.alive():
                assert time.monotonic() < deadline
                await asyncio.sleep(0.05)
        elif miss == "reaped":
            for s in live.POOL.reap_once(now=time.monotonic() + live.PARKED_IDLE_TIMEOUT_S + 1):
                await asyncio.to_thread(s.res.close)
        elif miss == "partial":
            msgs = msgs[:-1]  # the lane answered only one of the two calls
        elif miss == "two-frames":
            msgs = msgs + [LLMMessage(role="assistant", content="An aside."), _user("go on")]
        nxt = await _call(ad, msgs, **kw)
        starts = _starts()
        assert len(starts) == 2, "a miss starts a new process"
        assert await _gone(old), "one conversation never has two live CLIs"
        final = nxt
        if nxt.tool_calls:
            _, final, _ = await _reply(ad, _answer(msgs, nxt), **kw)
        assert final.text.startswith("Done") and "REAL-" in final.text
        assert all(DENIAL not in json.dumps(b) for b in up.bodies)
        if miss == "account":
            assert [s["config_dir"] for s in starts] == [str(tmp_path / "account-a"), str(tmp_path / "account-b")]
    want = {"partial": "remainder", "two-frames": "remainder"}.get(miss, miss)
    assert [i.get("reason") for i in _phases(trace, "live_miss")] == [want]


# --------------------------------------------------------------------------- #
# 3. outside a scope: byte-identical to today (one process per call)
# --------------------------------------------------------------------------- #
async def test_outside_a_scope_each_step_is_its_own_process_as_before(tmp_path, upstream, trace):
    up = upstream(THREE)
    ad = _adapter(tmp_path, up)
    _, final, responses = await _reply(ad, [_user("fix it")])
    await asyncio.wait_for(transport.drain_teardowns(), 60)
    starts = _starts()
    assert len(starts) == 3
    assert [_max_turns(s) for s in starts] == ["1", "1", "1"]
    assert all(s["api_timeout_ms"] is None for s in starts)
    assert final.text == "Done REAL-a|REAL-b"
    assert "cost_usd" in responses[-1].usage, "today's path keeps the CLI's own cost"
    assert live.live_sessions() == []
    assert not [r for r in trace if r[0].startswith("live_")]


async def test_the_switch_a_runner_and_a_budget_keep_todays_path(tmp_path, upstream, monkeypatch):
    up = upstream(THREE)
    ad = _adapter(tmp_path, up)
    call = await asyncio.to_thread(ad._native_call, "sys", [_user()], TOOLS, "", None)
    assert not live.applies(call), "no scope"
    async with _scope():
        assert live.applies(call)
        assert not live.applies(call, runner=lambda argv, stdin: (0, "", ""))
        token = budget.set_run_budget(2.0)
        try:
            limited = await asyncio.to_thread(ad._native_call, "sys", [_user()], TOOLS, "", None)
        finally:
            budget.reset_run_budget(token)
        assert limited.budget_usd > 0 and not live.applies(limited)
        monkeypatch.setenv(live.ENV_SWITCH, "off")
        assert not live.applies(call)
        await _reply(ad, [_user()])
        assert [_max_turns(s) for s in _starts()] == ["1", "1", "1"]


# --------------------------------------------------------------------------- #
# 4. lifetime: the scope, the cap, Stop, a helper, the shutdown
# --------------------------------------------------------------------------- #
def _post(url: str) -> int:
    parts = urlsplit(url)
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=10)
    try:
        conn.request("POST", parts.path + "/v1/messages", b'{"messages":[]}',
                     {"Content-Type": "application/json"})
        resp = conn.getresponse()
        resp.read()
        return resp.status
    finally:
        conn.close()


async def test_closing_the_scope_kills_a_process_parked_on_a_held_request(tmp_path, upstream):
    up = upstream(THREE)
    ad = _adapter(tmp_path, up)
    async with _scope():
        await _call(ad, [_user()])
        [s] = live.live_sessions()
        assert s.state == "parked_tools" and s.route.held is not None
        url, pid = s.route.url, _starts()[0]["pid"]
    assert await _gone(pid)
    assert live.live_sessions() == [] and s.route.held is None
    assert _post(url) == 404, "the route is gone with it"


async def test_the_cap_evicts_the_oldest_parked_session(tmp_path, upstream, monkeypatch, trace):
    monkeypatch.setattr(live, "MAX_LIVE_SESSIONS", 1)
    up = upstream(THREE)
    ad = _adapter(tmp_path, up)
    async with _scope():
        a = [_user("conversation A")]
        ra = await _call(ad, a)
        pid_a = _starts()[0]["pid"]
        await _call(ad, [_user("conversation B")])
        assert await _gone(pid_a), "B evicted A, the only parked session"
        assert len(live.live_sessions()) == 1
        _, final, _ = await _reply(ad, _answer(a, ra))
        assert final.text == "Done REAL-a|REAL-b"
    assert "cap" in [i.get("reason") for i in _phases(trace, "live_miss")]


async def test_with_every_session_streaming_a_call_runs_todays_path(tmp_path, upstream, monkeypatch):
    monkeypatch.setattr(live, "MAX_LIVE_SESSIONS", 1)
    up = upstream(THREE)
    ad = _adapter(tmp_path, up)
    async with _scope():
        slow = asyncio.create_task(_call(ad, [_user("SLOW one")]))
        assert await asyncio.to_thread(up.reached.wait, 30)
        other = await _call(ad, [_user("another")])
        assert other.tool_calls, "answered on today's path"
        assert [_max_turns(s) for s in _starts()] == [str(live.LIVE_MAX_TURNS), "1"]
        up.release.set()
        assert (await slow).tool_calls
    await asyncio.wait_for(transport.drain_teardowns(), 60)


async def test_stop_mid_round_kills_the_tree(tmp_path, upstream):
    up = upstream(THREE)
    ad = _adapter(tmp_path, up)
    async with _scope():
        task = asyncio.create_task(_call(ad, [_user("SLOW please")]))
        assert await asyncio.to_thread(up.reached.wait, 30)
        pid = _starts()[0]["pid"]
        [s] = live.live_sessions()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await _gone(pid), "Stop is a kill"
        assert live.live_sessions() == []
        # The relay thread waiting for the upstream's answer was woken by the
        # abort (on Windows a shutdown alone leaves it blocked, and the teardown
        # then waited out its 10 s bound): nothing of this route is still running.
        assert s.route._active == 0


async def test_a_helpers_answer_is_not_kept(tmp_path, upstream):
    up = upstream(THREE)
    ad = _adapter(tmp_path, up)
    async with _scope():
        token = H.HELPER_PARENT.set(types.SimpleNamespace())
        try:
            _, final, _ = await _reply(ad, [_user()])
        finally:
            H.HELPER_PARENT.reset(token)
        starts = _starts()
        assert len(starts) == 1, "a helper's own steps still share one process"
        assert final.text == "Done REAL-a|REAL-b"
        assert live.live_sessions() == []
        await asyncio.wait_for(transport.drain_teardowns(), 60)
        assert await _gone(starts[0]["pid"]), "its CLI exits on stdin EOF"


async def test_stop_all_ends_every_kept_process(tmp_path, upstream):
    up = upstream(THREE)
    ad = _adapter(tmp_path, up)
    async with _scope():
        await _call(ad, [_user("one")])
        await _call(ad, [_user("two")])
        pids = [s["pid"] for s in _starts()]
        assert await asyncio.to_thread(live.stop_all) == 2
        for pid in pids:
            assert await _gone(pid)


# --------------------------------------------------------------------------- #
# 5. the relay route on its own; a CLI that no longer behaves as measured
# --------------------------------------------------------------------------- #
def test_the_route_admits_one_request_per_message_and_one_sign_in_retry(upstream):
    up = upstream(THREE)
    up.statuses = [401]
    notes: list = []
    route = live.LiveRoute(up.url, 10.0, notes.append)
    try:
        assert _post(route.url) == 401, "the first request goes upstream"
        assert _post(route.url) == 200, "the CLI's ONE retry after a sign-in refusal"
        assert _post(route.url) == 400, "anything else is refused locally (CONSUMED)"
        route.admit_one()
        assert _post(route.url) == 200, "one more per stdin message"
        assert _post(route.url) == 400
        assert len(up.bodies) == 3
        assert [r.reauth for r in route.records] == [True, False]
        assert [r.denied for r in route.records] == [1, 1]
        assert notes == [], "nothing held: no request answered tools it was asked for"
    finally:
        route.close()
    assert _post(route.url) == 404


async def test_a_live_round_fills_the_attempt_iron_proxy_reads(tmp_path, upstream):
    up = upstream(THREE)
    ad = _adapter(tmp_path, up)
    call = await asyncio.to_thread(ad._native_call, "sys", [_user()], TOOLS, "", None)
    attempt = transport.Attempt()
    async with _scope():
        frames = [f async for f in live.run(call, attempt=attempt)]
        assert frames[-1]["type"] == "final" and frames[-1]["response"].tool_calls
    assert attempt.relay_used and attempt.status == 200 and attempt.limit_report() is None


async def test_a_cli_that_never_sends_its_follow_up_still_answers(tmp_path, upstream, monkeypatch, trace):
    monkeypatch.setenv("FAKE_LIVE_NO_FOLLOW_UP", "1")
    monkeypatch.setattr(live, "HELD_SETTLE_S", 0.3)
    up = upstream(THREE)
    ad = _adapter(tmp_path, up)
    async with _scope():
        _, final, responses = await _reply(ad, [_user()])
        assert final.text == "Done REAL-a|REAL-b"
        starts = _starts()
        assert len(starts) == 3, "no step could park: each later step replayed to a new process"
        for s in starts[:2]:
            assert await _gone(s["pid"])
        assert all("cost_usd" not in r.usage for r in responses)
    assert len(_phases(trace, "live_unheld")) == 2


# --------------------------------------------------------------------------- #
# 6. a walk-away or a cancel between the steps never leaves a process behind
# --------------------------------------------------------------------------- #
async def test_walking_away_mid_answer_ends_the_process_at_once(tmp_path, upstream):
    up = upstream([("text", "Hello there")])
    ad = _adapter(tmp_path, up)
    async with _scope():
        agen = ad.stream(system="sys", messages=[_user()], tools=TOOLS)
        first = await agen.__anext__()
        assert first == {"type": "text", "text": "Hello there "}
        await agen.aclose()
        assert live.live_sessions() == [], "closed by the walk-away itself, not by a later GC"
        assert await _gone(_starts()[0]["pid"])


async def test_a_cancel_while_the_match_is_read_ends_the_session_it_claimed(tmp_path, upstream, monkeypatch):
    up = upstream(THREE)
    ad = _adapter(tmp_path, up)
    real = live._find
    entered, gate = threading.Event(), threading.Event()

    def slow_find(*a, **k):
        entered.set()
        gate.wait(30)
        return real(*a, **k)

    async with _scope():
        msgs = [_user()]
        first = await _call(ad, msgs)
        pid = _starts()[0]["pid"]
        monkeypatch.setattr(live, "_find", slow_find)
        task = asyncio.create_task(_call(ad, _answer(msgs, first)))
        assert await asyncio.to_thread(entered.wait, 30)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        gate.set()  # the thread finishes its claim after the caller left
        assert await _gone(pid), "the session the abandoned match claimed is ended"
        deadline = time.monotonic() + 30
        while live.live_sessions():
            assert time.monotonic() < deadline
            await asyncio.sleep(0.05)


async def test_a_cancel_before_the_round_starts_ends_the_claimed_session(tmp_path, upstream, monkeypatch):
    up = upstream([("tools", [("read_file", {"path": "a"})]), ("text", "Done"), ("text", "Checked")])
    ad = _adapter(tmp_path, up)
    async with _scope():
        msgs, final, _ = await _reply(ad, [_user()])
        pid = _starts()[0]["pid"]
        stuck = asyncio.Event()

        async def write(self, frame, bound, t=None):
            stuck.set()
            await asyncio.sleep(3600)

        monkeypatch.setattr(live.LiveSession, "write", write)
        msgs = msgs + [LLMMessage(role="assistant", content=final.text, raw_blocks=final.raw_blocks),
                       _user("FINISH CHECK. note")]
        task = asyncio.create_task(_call(ad, msgs))
        await asyncio.wait_for(stuck.wait(), 30)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert [(x.state, x.driven, x.pid) for x in live.live_sessions()] == [], pid
        assert await _gone(pid)


# --------------------------------------------------------------------------- #
# 7. an upstream refusal mid-reply: the same error as today, the session ends
# --------------------------------------------------------------------------- #
async def test_a_rate_limit_mid_reply_raises_what_todays_path_raises_and_ends_the_session(tmp_path, upstream):
    from iron_jarvis.providers.adapters.base import ProviderError

    up = upstream(THREE)
    ad = _adapter(tmp_path, up)
    msgs = [_user("fix it")]
    async with _scope():
        first = await _call(ad, msgs)
        pid = _starts()[0]["pid"]
        up.limited_steps = {1}
        attempt = transport.Attempt()
        call = await asyncio.to_thread(ad._native_call, "sys", _answer(msgs, first), TOOLS, "", None)
        with pytest.raises(ProviderError) as live_err:
            async for _ in live.run(call, attempt=attempt):
                pass
        assert live_err.value.status_code == 429
        assert attempt.limit_report() == {"status": 429, "headers": {"retry-after": "7"},
                                          "text": "This request would exceed your limit"}
        assert live.live_sessions() == [] and await _gone(pid)
    # Today's path, same refusal: the same words.
    up.limited_steps = {1}
    with pytest.raises(ProviderError) as today_err:
        await _call(ad, _answer(msgs, first))
    assert str(today_err.value) == str(live_err.value)
    assert today_err.value.retry_after == live_err.value.retry_after


# --------------------------------------------------------------------------- #
# 8. EAGER denials (the real 2.1.316): one denial line per tool, the first while
#    the model is still writing the rest of the message
# --------------------------------------------------------------------------- #
TWO_TOOLS = [("tools", [("read_file", {"path": "a"}), ("read_file", {"path": "b" * 4000})]),
             ("text", "Done")]


async def test_an_eager_denial_while_the_answer_still_streams_does_not_end_the_step(
        tmp_path, upstream, monkeypatch, trace):
    """The benchmark's frontier-inventory-filter on real Sonnet (and get_number +
    a 900-word save_note on real Haiku): Claude Code denied the first tool the
    moment its block was complete; the model was still writing the second
    tool's long input 2 s later, so the step ended "unheld" with the relay's
    capture incomplete and raised DROPPED — every attempt, every retry."""
    monkeypatch.setenv("FAKE_LIVE_EAGER", "1")
    monkeypatch.setattr(live, "HELD_SETTLE_S", 0.3)
    up = upstream(TWO_TOOLS)
    up.pause_s = 1.5  # five times the settle: the second input is still streaming
    ad = _adapter(tmp_path, up)
    async with _scope():
        _, final, responses = await _reply(ad, [_user()])
        assert len(_starts()) == 1, "the step parked and the next one resumed"
        assert len(responses[0].tool_calls) == 2
        assert _results_in(up.bodies[1]) == ["REAL-a", "REAL-" + "b" * 4000]
        assert all(DENIAL not in json.dumps(b) for b in up.bodies)
        assert final.text.startswith("Done REAL-a|REAL-b")
    assert _phases(trace, "live_unheld") == []
    assert [i.get("reason") for i in _phases(trace, "live_resume")] == ["tools"]


async def test_a_two_tool_step_with_one_denial_line_per_tool_ends_at_once(
        tmp_path, upstream, monkeypatch, trace):
    """Eager denials arrive one line per tool: the ids are gathered across the
    step's lines, so the step ends when the relay holds the follow-up — it never
    waits out HELD_SETTLE_S (set far longer than any test here)."""
    monkeypatch.setenv("FAKE_LIVE_EAGER", "1")
    monkeypatch.setattr(live, "HELD_SETTLE_S", 60.0)
    up = upstream(TWO_TOOLS)
    ad = _adapter(tmp_path, up)
    async with _scope():
        _, final, responses = await _reply(ad, [_user()])
        assert len(_starts()) == 1 and final.text.startswith("Done REAL-a|REAL-b")
    parked = _phases(trace, "live_parked")
    assert parked and parked[0]["tools"] == 2
    assert parked[0]["waited"] == 0, "the step waited out HELD_SETTLE_S"


async def test_each_miss_is_one_info_line_naming_only_its_reason(tmp_path, upstream, caplog):
    """The benchmark's log is how the misses are counted: one INFO line per miss,
    the reason WORD only (never a path, a body or an id); a resume and a new
    session are DEBUG."""
    caplog.set_level(logging.DEBUG, logger="ironjarvis.claude_native")
    up = upstream(THREE)
    ad = _adapter(tmp_path, up)
    async with _scope():
        msgs = [_user("fix it")]
        msgs = _answer(msgs, await _call(ad, msgs))
        msgs = _answer(msgs, await _call(ad, msgs))  # a resume
        msgs[0] = _user("fix it, please")
        await _call(ad, msgs)  # an edited history: a miss
    rows = [(r.levelno, r.getMessage()) for r in caplog.records if "live session" in r.getMessage()]
    misses = [m for lvl, m in rows if lvl == logging.INFO]
    assert misses == ["claude-cli: live session not reused (edited); starting a new process"]
    assert (logging.DEBUG, "claude-cli: live session reused (tools)") in rows
    assert (logging.DEBUG, "claude-cli: new live session (fresh)") in rows
    assert (logging.DEBUG, "claude-cli: new live session (edited)") in rows
