"""v1.420.0 (C3.1): the per-round FIXED COST of a claude-cli call, cut.

The trace (``docs/CLAUDE-NATIVE-ROUND-COST.md``, one real 3-round reply on this
PC) showed, besides the CLI's own ~0.5 s start, three costs paid on EVERY round:

* the answer waited for the CLI to EXIT (30-45 ms after its ``result``) and
  then for the inline teardown (7-53 ms) before the caller had it;
* a new TCP + TLS handshake to Anthropic per call (64-84 ms);
* a new loopback listener (and its thread) per call.

Now the final frame is released at ``result`` (only when the relay holds the
whole 200 answer and the result is clean), the teardown runs in the BACKGROUND
and the next call of the same conversation waits for it before it spawns; ONE
listener, ONE TLS context and a keep-alive upstream pool serve every call.
Every admission rule is unchanged (first request only, the sign-in retry).

These pins drive the REAL transport against a fake CLI started through a shim
(the npm shape) whose answer goes through the REAL relay to a LOOPBACK fake
upstream; nothing reaches the network. The fake CLI can LINGER after its
``result`` — the CLI's own shutdown — so "released before the exit" is read off
a live pid, never a clock.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import logging
import os
import stat
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import psutil
import pytest

from iron_jarvis.providers import cli_auth
from iron_jarvis.providers.adapters import subprocess_cli as sc
from iron_jarvis.providers.adapters.base import LLMMessage
from iron_jarvis.providers.adapters.claude_native import admission as adm_mod
from iron_jarvis.providers.adapters.claude_native import transport
from iron_jarvis.providers.adapters.claude_native.admission import Admission
from iron_jarvis.providers.cli_auth import CliAuthProbe

# --------------------------------------------------------------------------- #
# the fake CLI: init, history acks, ONE relay request, then (optionally) linger
# --------------------------------------------------------------------------- #
FAKE_CLI = r'''
import json, os, subprocess, sys, time, urllib.request

def emit(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()

emit({"type": "system", "subtype": "init"})
for line in sys.stdin:
    line = line.strip()
    if line and json.loads(line).get("shouldQuery") is False:
        emit({"type": "result", "subtype": "success", "num_turns": 0, "is_error": False})
helper = None
if os.environ.get("LANE_HELPER"):
    helper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
pids = os.environ.get("LANE_PIDS")
if pids:
    with open(pids + ".tmp", "w") as f:
        f.write(f"{os.getpid()} {helper.pid if helper else 0}")
    os.replace(pids + ".tmp", pids)
req = urllib.request.Request(os.environ["ANTHROPIC_BASE_URL"] + "/v1/messages",
                             data=b'{"messages":[]}', headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=10) as resp:
    resp.read()
tools = os.environ.get("LANE_TOOLS") == "1"
if not tools:
    emit({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                                            "delta": {"type": "text_delta", "text": "Hello"}}})
emit({"type": "stream_event", "event": {"type": "message_stop"}})
if tools:
    emit({"type": "result", "subtype": "error_max_turns", "is_error": True, "num_turns": 2,
          "usage": {"input_tokens": 1, "output_tokens": 1}})
else:
    emit({"type": "result", "subtype": "success", "is_error": False, "num_turns": 1,
          "usage": {"input_tokens": 1, "output_tokens": 1}, "total_cost_usd": 0.001})
hold = os.environ.get("LANE_HOLD")
if hold:  # stay up (the CLI's own shutdown) until the test says so: no clock
    end = time.time() + 30
    while not os.path.exists(hold) and time.time() < end:
        time.sleep(0.02)
time.sleep(float(os.environ.get("LANE_LINGER", "0")))
sys.exit(1 if tools else 0)
'''

USAGE = {"input_tokens": 10, "output_tokens": 0, "cache_read_input_tokens": 0,
         "cache_creation_input_tokens": 0}


def _answer(tools: bool) -> bytes:
    start = {"type": "message_start", "message": {"id": "m1", "type": "message", "role": "assistant",
                                                  "model": "m", "content": [], "stop_reason": None,
                                                  "usage": dict(USAGE)}}
    if tools:
        body = [
            {"type": "content_block_start", "index": 0,
             "content_block": {"type": "tool_use", "id": "toolu_1", "name": "mcp__ij__read_file", "input": {}}},
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "input_json_delta", "partial_json": '{"path": "a.txt"}'}},
            {"type": "content_block_stop", "index": 0},
        ]
        stop = "tool_use"
    else:
        body = [
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hello"}},
            {"type": "content_block_stop", "index": 0},
        ]
        stop = "end_turn"
    tail = [{"type": "message_delta", "delta": {"stop_reason": stop}, "usage": {"output_tokens": 3}},
            {"type": "message_stop"}]
    return "".join("data: " + json.dumps(e) + "\n\n" for e in [start, *body, *tail]).encode()


class Upstream:
    """A loopback fake Anthropic. ``keep_alive``: HTTP/1.1 with a length (the
    connection can be kept); ``drop_after``: it closes the socket after each
    answer WITHOUT saying so (a server whose keep-alive ran out)."""

    def __init__(self, *, tools: bool = False, keep_alive: bool = True, drop_after: bool = False) -> None:
        self.hits = 0
        self.connections = 0
        owner = self

        class Peer(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1" if keep_alive else "HTTP/1.0"

            def setup(self) -> None:
                owner.connections += 1
                super().setup()

            def log_message(self, *args) -> None:
                pass

            def do_POST(self) -> None:
                self.rfile.read(int(self.headers["Content-Length"]))
                owner.hits += 1
                data = _answer(tools)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                self.wfile.flush()
                if drop_after:
                    self.close_connection = True

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Peer)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def stop(self) -> None:
        self.server.shutdown()
        self.thread.join(5)
        self.server.server_close()


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    for key in [k for k in os.environ if k.startswith("ANTHROPIC_")] + list(transport.BACKEND_SWITCHES):
        monkeypatch.delenv(key, raising=False)
    for key in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(cli_auth, "DEFAULT_PROBE", CliAuthProbe(which=lambda b: None))
    monkeypatch.setattr(transport, "DEFAULT_PROBE", cli_auth.DEFAULT_PROBE)
    # Every temp dir this file's calls make lands HERE, so a leak is countable.
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    adm_mod.POOL.clear()
    yield
    adm_mod.POOL.clear()


def _shim(tmp_path: Path) -> str:
    script = tmp_path / "fake_cli_v1420.py"
    script.write_text(FAKE_CLI, encoding="utf-8")
    if os.name == "nt":
        shim = tmp_path / "claude.cmd"
        shim.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        shim = tmp_path / "claude"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    return str(shim)


def _adapter(tmp_path: Path, up: Upstream) -> sc.ClaudeCliAdapter:
    env = dict(os.environ, ANTHROPIC_BASE_URL=up.url)
    shim = _shim(tmp_path)
    return sc.make_claude_cli(model="claude-sonnet-5", which=lambda _b: shim, env=env)


def _temp_dirs() -> list[str]:
    return [p.name for p in Path(tempfile.gettempdir()).iterdir() if p.name.startswith("ij-claude-")]


def _pids(path: Path) -> tuple[int, int]:
    deadline = time.monotonic() + 30
    while not path.exists():
        assert time.monotonic() < deadline, "the fake CLI never wrote its pids"
        time.sleep(0.02)
    a, b = path.read_text(encoding="utf-8").split()
    return int(a), int(b)


def _alive(pid: int) -> bool:
    if not pid:
        return False
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


def _user(text: str = "hi") -> LLMMessage:
    return LLMMessage(role="user", content=text)


@pytest.fixture
def upstream():
    made: list[Upstream] = []

    def make(**kw) -> Upstream:
        up = Upstream(**kw)
        made.append(up)
        return up

    yield make
    for up in made:
        up.stop()


# --------------------------------------------------------------------------- #
# 1. the answer is released at `result`; the CLI's exit is the teardown's
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("tools", [False, True], ids=["text", "tool-boundary"])
async def test_the_final_arrives_while_the_cli_is_still_exiting(tmp_path, monkeypatch, upstream, tools):
    up = upstream(tools=tools)
    pids = tmp_path / "pids.txt"
    monkeypatch.setenv("LANE_PIDS", str(pids))
    hold = tmp_path / "hold"
    monkeypatch.setenv("LANE_HOLD", str(hold))  # the CLI's own shutdown, held open
    monkeypatch.setenv("LANE_TOOLS", "1" if tools else "0")
    monkeypatch.setattr(transport, "RELEASE_GRACE_S", 120.0)
    ad = _adapter(tmp_path, up)
    resp = await ad.complete(system="", messages=[_user()], tools=[
        {"name": "read_file", "description": "r", "input_schema": {"type": "object"}}])
    cli, _ = _pids(pids)
    assert _alive(cli), "the answer must not wait for the CLI to exit"
    if tools:
        assert resp.finish_reason == "tool_use" and resp.tool_calls[0].name == "read_file"
    else:
        assert resp.text == "Hello" and resp.finish_reason == "stop"
    assert transport.pending_teardowns() == 1
    # The background teardown waits for that exit, then cleans up.
    hold.write_text("go", encoding="utf-8")
    await asyncio.wait_for(transport.drain_teardowns(), 60)
    assert await _gone(cli)
    assert _temp_dirs() == [] and transport.pending_teardowns() == 0


async def test_a_dirty_result_still_waits_for_the_exit(tmp_path, monkeypatch, upstream):
    """No relay answer in hand (the CLI's own refusal) -> read on to the exit and
    tear down inline, exactly as before."""
    up = upstream()
    monkeypatch.setenv("LANE_LINGER", "0")
    ad = _adapter(tmp_path, up)
    up.stop()  # the relay's one request fails: no complete 200 answer
    with pytest.raises(Exception):
        await ad.complete(system="", messages=[_user()], tools=[])
    assert transport.pending_teardowns() == 0 and _temp_dirs() == []


# --------------------------------------------------------------------------- #
# 2. the next call of the SAME conversation waits for that teardown
# --------------------------------------------------------------------------- #
async def test_the_next_round_spawns_only_after_the_previous_cli_is_gone(tmp_path, monkeypatch, upstream):
    up = upstream()
    monkeypatch.setenv("LANE_LINGER", "1.5")
    monkeypatch.setattr(transport, "RELEASE_GRACE_S", 30.0)
    procs_seen: list = []
    at_spawn: list[list[bool]] = []
    real_spawn = transport._Resources.spawn

    def spawn(self, env):
        at_spawn.append([p.poll() is not None for p in procs_seen])
        proc = real_spawn(self, env)
        procs_seen.append(proc)
        return proc

    monkeypatch.setattr(transport._Resources, "spawn", spawn)
    ad = _adapter(tmp_path, up)
    history = [_user("same opening")]
    for _ in range(3):
        await ad.complete(system="", messages=list(history), tools=[])
        history += [LLMMessage(role="assistant", content="Hello"), _user("and again")]
    assert at_spawn == [[], [True], [True, True]], "never two live CLIs for one conversation"
    await asyncio.wait_for(transport.drain_teardowns(), 60)
    assert all(p.poll() is not None for p in procs_seen) and _temp_dirs() == []


async def test_another_conversation_does_not_wait(tmp_path, monkeypatch, upstream):
    up = upstream()
    pids = tmp_path / "pids.txt"
    monkeypatch.setenv("LANE_PIDS", str(pids))
    hold = tmp_path / "hold"
    monkeypatch.setenv("LANE_HOLD", str(hold))
    monkeypatch.setattr(transport, "RELEASE_GRACE_S", 120.0)
    ad = _adapter(tmp_path, up)
    await ad.complete(system="", messages=[_user("first conversation")], tools=[])
    first, _ = _pids(pids)
    pids.unlink()
    monkeypatch.delenv("LANE_HOLD")
    await ad.complete(system="", messages=[_user("a different one")], tools=[])
    assert _alive(first), "a different conversation started while the first CLI was still exiting"
    hold.write_text("go", encoding="utf-8")
    await asyncio.wait_for(transport.drain_teardowns(), 60)
    assert await _gone(first)


# --------------------------------------------------------------------------- #
# 3. no leak: 20 calls, every process and temp dir gone
# --------------------------------------------------------------------------- #
async def test_twenty_released_calls_leave_no_temp_dir_and_no_process(tmp_path, monkeypatch, upstream):
    up = upstream()
    monkeypatch.setenv("LANE_LINGER", "0.05")
    procs_seen: list = []
    real_spawn = transport._Resources.spawn

    def spawn(self, env):
        proc = real_spawn(self, env)
        procs_seen.append(proc)
        return proc

    monkeypatch.setattr(transport._Resources, "spawn", spawn)
    ad = _adapter(tmp_path, up)
    for i in range(20):
        resp = await ad.complete(system="", messages=[_user(f"conversation {i % 3}")], tools=[])
        assert resp.text == "Hello"
    await asyncio.wait_for(transport.drain_teardowns(), 120)
    assert len(procs_seen) == 20 and all(p.poll() is not None for p in procs_seen)
    assert _temp_dirs() == [] and transport.pending_teardowns() == 0
    assert up.hits == 20 and up.connections == 1, "one kept upstream connection served all 20"


async def test_a_released_cli_that_never_exits_is_killed_with_its_tree(tmp_path, monkeypatch, upstream):
    up = upstream()
    pids = tmp_path / "pids.txt"
    monkeypatch.setenv("LANE_PIDS", str(pids))
    monkeypatch.setenv("LANE_HELPER", "1")
    monkeypatch.setenv("LANE_LINGER", "60")
    monkeypatch.setattr(transport, "RELEASE_GRACE_S", 0.3)
    ad = _adapter(tmp_path, up)
    assert (await ad.complete(system="", messages=[_user()], tools=[])).text == "Hello"
    cli, helper = _pids(pids)
    await asyncio.wait_for(transport.drain_teardowns(), 60)
    assert await _gone(cli) and await _gone(helper), "the grace ran out: the TREE dies"
    assert _temp_dirs() == []


# --------------------------------------------------------------------------- #
# 4. ONE listener per process; routes stay separate
# --------------------------------------------------------------------------- #
def _post(url: str, timeout: float = 10.0) -> int:
    parts = urlsplit(url)
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=timeout)
    try:
        conn.request("POST", parts.path + "/v1/messages", b'{"messages":[]}',
                     {"Content-Type": "application/json"})
        resp = conn.getresponse()
        resp.read()
        return resp.status
    finally:
        conn.close()


def test_one_listener_serves_every_relay_and_each_route_admits_once(monkeypatch, upstream):
    up = upstream()
    made: list = []

    class Counted(adm_mod.RelayServer):
        def __init__(self) -> None:
            made.append(self)
            super().__init__()

    monkeypatch.setattr(adm_mod, "_LISTENER", None)
    monkeypatch.setattr(adm_mod, "RelayServer", Counted)
    relays = [Admission(up.url, 10.0) for _ in range(5)]
    try:
        assert len(made) == 1, "one bind for every call"
        assert len({urlsplit(r.url).port for r in relays}) == 1
        assert len({r.prefix for r in relays}) == 5
        assert _post(relays[0].url) == 200
        assert _post(relays[0].url) == 400 and relays[0].denied == 1  # first request only
        assert relays[1].used is False and relays[1].denied == 0  # another route is untouched
        relays[2].close()
        assert _post(relays[2].url) == 404  # a closed route reaches nothing
        assert _post(relays[1].url) == 200  # ... and the listener still serves the rest
        assert up.hits == 2
    finally:
        for r in relays:
            r.close()
        made[0].shutdown()
        made[0].server_close()


# --------------------------------------------------------------------------- #
# 5. ONE TLS context; 6. a keep-alive upstream connection
# --------------------------------------------------------------------------- #
def test_the_tls_context_is_made_once(monkeypatch):
    made: list = []
    real = adm_mod.ssl.create_default_context

    def counting(*a, **kw):
        ctx = real(*a, **kw)
        made.append(ctx)
        return ctx

    contexts: list = []

    class FakeHTTPS:
        def __init__(self, host, port, timeout=None, context=None):
            contexts.append(context)

        def connect(self):
            raise OSError("offline test")

        def close(self):
            pass

    monkeypatch.setattr(adm_mod, "_TLS_CONTEXT", None)
    monkeypatch.setattr(adm_mod.ssl, "create_default_context", counting)
    monkeypatch.setattr(adm_mod.http.client, "HTTPSConnection", FakeHTTPS)
    for _ in range(3):
        adm = Admission("https://api.anthropic.com", 2.0)
        try:
            with pytest.raises((http.client.HTTPException, OSError)):
                _post(adm.url)
        finally:
            adm.close()
        assert adm.failure == "OSError"
    assert len(made) == 1 and len(contexts) == 3, "a failed NEW connection is never retried"
    assert all(c is made[0] for c in contexts)
    assert made[0].verify_mode == adm_mod.ssl.CERT_REQUIRED and made[0].check_hostname


@pytest.mark.parametrize("keep_alive,expected", [(True, 1), (False, 4)], ids=["http11", "http10-control"])
def test_the_upstream_connection_is_kept_across_calls(upstream, keep_alive, expected):
    up = upstream(keep_alive=keep_alive)
    for _ in range(4):
        adm = Admission(up.url, 10.0)
        try:
            assert _post(adm.url) == 200 and adm.capture.complete
        finally:
            adm.close()
    assert up.hits == 4 and up.connections == expected


def test_a_kept_connection_the_server_closed_is_not_used(upstream, caplog):
    up = upstream(drop_after=True)
    caplog.set_level(logging.DEBUG, logger="ironjarvis.claude_native")
    for _ in range(2):
        adm = Admission(up.url, 10.0)
        try:
            assert _post(adm.url) == 200
        finally:
            adm.close()
        time.sleep(0.2)  # the server's close reaches this side
    assert up.hits == 2 and up.connections == 2
    assert "a kept upstream connection was closed" not in caplog.text, "the open-check caught it first"


def test_a_kept_connection_that_dies_under_the_request_is_replaced_once(monkeypatch, upstream, caplog):
    """The race the open-check cannot see (the server closes between the check and
    the request): nothing was answered, so ONE new connection carries it."""
    up = upstream(drop_after=True)
    monkeypatch.setattr(adm_mod, "_still_open", lambda conn: True)
    caplog.set_level(logging.DEBUG, logger="ironjarvis.claude_native")
    adm = Admission(up.url, 10.0)
    try:
        assert _post(adm.url) == 200
    finally:
        adm.close()
    time.sleep(0.2)
    adm = Admission(up.url, 10.0)
    try:
        assert _post(adm.url) == 200
        assert adm.status == 200 and adm.failure is None and adm.capture.complete
    finally:
        adm.close()
    assert up.hits == 2, "the dead connection never delivered the request"
    assert "a kept upstream connection was closed" in caplog.text


# --------------------------------------------------------------------------- #
# 7. the trace hook: off by default, phases in order when on
# --------------------------------------------------------------------------- #
async def test_the_trace_names_every_phase(tmp_path, monkeypatch, upstream):
    assert transport.TRACE is None
    up = upstream()
    monkeypatch.setenv("LANE_LINGER", "0")
    rows: list = []
    monkeypatch.setattr(transport, "TRACE", lambda cid, ph, ms, info: rows.append((ph, ms, info)))
    ad = _adapter(tmp_path, up)
    await ad.complete(system="", messages=[_user()], tools=[])
    await asyncio.wait_for(transport.drain_teardowns(), 60)
    phases = [r[0] for r in rows]
    for name in ("files_written", "relay_bound", "spawned", "history_acked", "init", "relay_request",
                 "relay_connected", "relay_headers", "relay_first_byte", "relay_last_byte", "result",
                 "final_released", "exited", "teardown_done"):
        assert name in phases, name
    ms = dict((r[0], r[1]) for r in rows)
    assert ms["final_released"] <= ms["exited"] <= ms["teardown_done"]
    assert all(isinstance(v, (int, float)) for _, _, info in rows for v in info.values())
