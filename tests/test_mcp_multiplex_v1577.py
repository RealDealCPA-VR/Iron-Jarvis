"""v1.577.0 — the MCP client works with large real-world servers.

Before this release ``StdioTransport.request`` held one lock for the WHOLE
request, so one long call (a server's ``wait_for_decision`` long-poll while a
person approves something) blocked every other call to that server; a cancel
always killed the server (taking every other call with it); progress never
kept a call's deadline alive; the server's notifications reached nobody but
the progress card; and its ``initialize`` answer (its instructions) was
thrown away.

Every stdio test drives the REAL ``StdioTransport`` / ``MCPClient`` against a
genuine child process (``tests/fixtures/multiplex_mcp_server_v1577.py``, which
answers calls concurrently); only the SERVER is fake. Assertions are on
ORDER and EVENTS (a fast call returns while the slow one is still pending —
the slow one cannot finish until the test releases it), never on wall-time
gaps. The only clocks are hang guards and the transport floors under test.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from pathlib import Path

import httpx
import psutil
import pytest

from iron_jarvis.mcp import client as client_mod
from iron_jarvis.mcp.client import (
    SERVER_INSTRUCTIONS_MAX,
    FakeTransport,
    HttpTransport,
    MCPClient,
    MCPError,
    MCPServerStopped,
    StdioTransport,
)
from iron_jarvis.tools import registry as registry_mod

FIXTURES = Path(__file__).parent / "fixtures"
SERVER = str(FIXTURES / "multiplex_mcp_server_v1577.py")
sys.path.insert(0, str(FIXTURES))
from multiplex_mcp_server_v1577 import INSTRUCTIONS, SERVER_INFO  # noqa: E402

#: A hang guard for polls and waits, never a performance bar.
_GUARD_S = 20.0
#: How long a fixture ``slow`` call may wait for its release: far beyond the
#: guard, so a transport that serialised calls cannot pass by the slow call
#: finishing on its own first.
_SLOW_GUARD_S = 120.0


def _transport(**kw) -> StdioTransport:
    kw.setdefault("request_timeout", None)  # a registry pack's shape
    return StdioTransport(sys.executable, [SERVER], **kw)


def _text(result: dict) -> str:
    return result["content"][0]["text"]


def _alive(pid: int) -> bool:
    try:
        proc = psutil.Process(pid)
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def _wait_until(pred, what: str) -> None:
    deadline = time.monotonic() + _GUARD_S
    while not pred():
        assert time.monotonic() < deadline, f"gave up waiting for {what}"
        time.sleep(0.02)


async def _await_until(pred, what: str) -> None:
    deadline = time.monotonic() + _GUARD_S
    while not pred():
        assert time.monotonic() < deadline, f"gave up waiting for {what}"
        await asyncio.sleep(0.01)


# --------------------------------------------------------------------------- #
# (a) Multiplexing: a fast call is answered while a slow one is in flight.
# --------------------------------------------------------------------------- #
async def test_a_fast_call_answers_while_a_slow_call_is_in_flight():
    """The slow call cannot finish until the test releases it, so a fast
    answer that arrives while it is still pending proves the two were on the
    wire at once. A serialising transport parks the fast call behind the slow
    one: the guard trips and the test goes red."""
    transport = _transport()
    client = MCPClient(transport, name="multi")
    try:
        await client.list_tools()
        slow = asyncio.ensure_future(
            client.call_tool("slow", {"tag": "a", "guard_s": _SLOW_GUARD_S})
        )
        await _await_until(lambda: transport.inflight_count() == 1, "the slow call on the wire")
        fast = await asyncio.wait_for(client.call_tool("fast", {"tag": "b"}), _GUARD_S)
        assert _text(fast) == "fast:b"
        assert not slow.done(), "the slow call finished first: calls were serialised"
        assert transport.inflight_count() == 1
        await client.call_tool("release", {"tag": "a"})
        assert _text(await asyncio.wait_for(slow, _GUARD_S)) == "slow:a"
        assert transport.spawn_count == 1
        assert transport.inflight_count() == 0
    finally:
        transport.close()


async def test_many_concurrent_calls_each_get_their_own_answer():
    transport = _transport()
    client = MCPClient(transport, name="multi")
    try:
        await client.list_tools()
        results = await asyncio.wait_for(asyncio.gather(*[
            client.call_tool("slow" if i % 2 else "fast", {"tag": f"n{i}", "delay_s": 0.01 + 0.01 * (i % 3)})
            for i in range(10)
        ]), _GUARD_S)
    finally:
        transport.close()
    assert [_text(r) for r in results] == [
        f"{'slow' if i % 2 else 'fast'}:n{i}" for i in range(10)
    ]
    assert transport.spawn_count == 1


# --------------------------------------------------------------------------- #
# (b) Cancellation: notifications/cancelled, and no kill while others wait.
# --------------------------------------------------------------------------- #
async def test_a_cancel_sends_notifications_cancelled_and_spares_a_busy_server():
    transport = _transport()
    client = MCPClient(transport, name="multi")
    try:
        await client.list_tools()
        first = asyncio.ensure_future(
            client.call_tool("slow", {"tag": "first", "guard_s": _SLOW_GUARD_S})
        )
        await _await_until(lambda: transport.inflight_count() == 1, "the first call on the wire")
        second = asyncio.ensure_future(
            client.call_tool("slow", {"tag": "second", "guard_s": _SLOW_GUARD_S})
        )
        await _await_until(lambda: transport.inflight_count() == 2, "the second call on the wire")
        pid, spawned = transport.pid, transport.spawn_count

        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        # The other call still waits on this server: it was NOT killed.
        assert transport.pid == pid and _alive(pid)
        assert transport.inflight_count() == 1

        # The server was told, for the right id, before the cancel returned.
        log = json.loads(_text(await client.call_tool("cancelled_log", {})))
        assert [(e["tool"], e["tag"]) for e in log] == [("slow", "second")], log
        assert isinstance(log[0]["requestId"], int)
        assert log[0]["reason"]

        # The cancelled call's late answer is dropped; the server goes on.
        await client.call_tool("release", {"tag": "second"})
        assert _text(await client.call_tool("fast", {"tag": "after"})) == "fast:after"
        await client.call_tool("release", {"tag": "first"})
        assert _text(await asyncio.wait_for(first, _GUARD_S)) == "slow:first"
        assert transport.spawn_count == spawned
        assert transport.pid == pid
    finally:
        transport.close()


async def test_a_cancelled_lone_call_on_a_server_that_is_talking_is_not_killed():
    """A single call whose server has said something since it was written
    (a log line) is busy, not wedged: the cancel spares the server and tells
    it ``notifications/cancelled``."""
    transport = _transport()
    client = MCPClient(transport, name="multi")
    heard = threading.Event()
    client.add_listener("notifications/message", lambda msg: heard.set())
    try:
        await client.list_tools()
        pid = transport.pid
        call = asyncio.ensure_future(client.call_tool("hang_chatty", {"tag": "busy"}))
        await asyncio.to_thread(heard.wait, _GUARD_S)
        assert heard.is_set(), "the server's log line never reached the listener"
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call
        assert transport.pid == pid and _alive(pid)
        log = json.loads(_text(await client.call_tool("cancelled_log", {})))
        assert [(e["tool"], e["tag"]) for e in log] == [("hang_chatty", "busy")]
        assert transport.spawn_count == 1
    finally:
        transport.close()


def test_a_cancel_before_the_write_never_sends_the_request():
    transport = _transport()
    try:
        transport.request("tools/list")
        token = threading.Event()
        token.set()
        with pytest.raises(MCPError, match="cancelled before it was sent"):
            transport.request("tools/call", {"name": "fast", "arguments": {"tag": "x"}},
                              cancel=token)
        assert transport.inflight_count() == 0
        res = transport.request("tools/call", {"name": "cancelled_log", "arguments": {}})
        assert json.loads(_text(res)) == []
    finally:
        transport.close()


# --------------------------------------------------------------------------- #
# (c) Progress keeps the call's registry deadline alive.
# --------------------------------------------------------------------------- #
async def test_progress_for_the_calls_own_token_calls_the_keepalive(monkeypatch):
    """``call_tool`` captures ``tools.registry.deadline_keepalive()`` and the
    dispatcher calls it for every progress notification on THIS call's
    token — not for a foreign token. Without a chat scope the token is a
    fresh ``ij-`` one."""
    kept: list[str] = []
    monkeypatch.setattr(
        registry_mod, "deadline_keepalive",
        lambda: (lambda: kept.append(threading.current_thread().name)),
    )
    transport = _transport()
    client = MCPClient(transport, name="multi")
    try:
        res = await client.call_tool("progress", {"count": 3})
    finally:
        transport.close()
    assert _text(res).startswith("ij-")
    assert len(kept) == 3, kept
    # Called on the transport's dispatcher, never on the event loop.
    assert all(name.startswith("mcp-dispatch-") for name in kept), kept


async def test_without_a_registry_deadline_no_keepalive_is_needed(monkeypatch):
    monkeypatch.setattr(registry_mod, "deadline_keepalive", lambda: None)
    transport = _transport()
    try:
        res = await MCPClient(transport, name="multi").call_tool("progress", {"count": 2})
    finally:
        transport.close()
    assert _text(res).startswith("ij-")


# --------------------------------------------------------------------------- #
# (d) Notification listeners.
# --------------------------------------------------------------------------- #
async def test_listeners_hear_tools_list_changed_off_the_dispatcher():
    transport = _transport()
    client = MCPClient(transport, name="multi")
    got: list[tuple[dict, str]] = []
    heard = threading.Event()

    def on_change(msg):
        got.append((msg, threading.current_thread().name))
        heard.set()

    def broken(msg):
        raise RuntimeError("a listener that fails must not break the pack")

    client.add_listener("notifications/tools/list_changed", broken)
    client.add_listener("notifications/tools/list_changed", on_change)
    try:
        assert _text(await client.call_tool("list_changed", {})) == "sent"
        await asyncio.to_thread(heard.wait, _GUARD_S)
        assert heard.is_set(), "the listener never heard list_changed"
        # The pack still works after a listener raised.
        assert _text(await client.call_tool("fast", {"tag": "ok"})) == "fast:ok"
    finally:
        transport.close()
    msg, thread_name = got[0]
    assert msg["method"] == "notifications/tools/list_changed"
    assert thread_name.startswith("mcp-listeners-")


# --------------------------------------------------------------------------- #
# (e) Server info from initialize.
# --------------------------------------------------------------------------- #
async def test_the_servers_instructions_capabilities_and_info_are_kept():
    transport = _transport()
    client = MCPClient(transport, name="multi")
    assert client.server_instructions == ""  # nothing started yet (lazy spawn)
    try:
        await client.list_tools()
        assert client.server_instructions == INSTRUCTIONS
        assert client.server_capabilities == {"tools": {"listChanged": True}}
        assert client.server_info == SERVER_INFO
    finally:
        transport.close()


def test_server_info_from_a_fake_and_the_instructions_cap():
    fake = FakeTransport(instructions="x" * (SERVER_INSTRUCTIONS_MAX + 50),
                         capabilities={"tools": {}}, server_info={"name": "f"})
    client = MCPClient(fake, name="f")
    assert len(client.server_instructions) == SERVER_INSTRUCTIONS_MAX
    assert client.server_capabilities == {"tools": {}}
    assert client.server_info == {"name": "f"}
    plain = MCPClient(FakeTransport(), name="p")
    assert (plain.server_instructions, plain.server_capabilities, plain.server_info) == ("", {}, {})
    text, caps, info = client_mod._init_fields({"instructions": 5, "capabilities": [], "serverInfo": "x"})
    assert (text, caps, info) == ("", {}, {})


def test_initialize_declares_nothing_new():
    assert client_mod.client_capabilities() == {"elicitation": {}, "sampling": {}}


# --------------------------------------------------------------------------- #
# (f) EOF fails EVERY call in flight; the next call respawns.
# --------------------------------------------------------------------------- #
async def test_a_dying_server_fails_every_call_in_flight_and_the_next_call_respawns():
    transport = _transport()
    client = MCPClient(transport, name="multi")
    try:
        await client.list_tools()
        pid = transport.pid
        slow = asyncio.ensure_future(
            client.call_tool("slow", {"tag": "s", "guard_s": _SLOW_GUARD_S})
        )
        hang = asyncio.ensure_future(client.call_tool("hang", {}))
        await _await_until(lambda: transport.inflight_count() == 2, "both calls on the wire")
        die = asyncio.ensure_future(client.call_tool("die", {}))
        results = await asyncio.wait_for(
            asyncio.gather(slow, hang, die, return_exceptions=True), _GUARD_S
        )
        for res in results:
            assert isinstance(res, MCPServerStopped), res
            text = str(res)
            assert "'multi'" in text and "closed the connection" in text, text
            assert "restarted on the next call" in text, text
        assert any("exit code 3" in str(r) for r in results), results
        assert transport.pid is None, "the dead server must be forgotten at once"
        _wait_until(lambda: not _alive(pid), "the dead server to be gone")

        again = await asyncio.wait_for(client.call_tool("fast", {"tag": "reborn"}), _GUARD_S)
        assert _text(again) == "fast:reborn"
        assert transport.spawn_count == 2, "exactly one respawn, on the NEXT call"
    finally:
        transport.close()


# --------------------------------------------------------------------------- #
# (g) The transport floor still kills a silent, wedged call.
# --------------------------------------------------------------------------- #
def test_a_silent_wedged_lone_call_is_killed_on_its_timeout():
    transport = _transport(request_timeout=0.5)
    client = MCPClient(transport, name="brain")
    try:
        transport.request("tools/list")  # handshake outside the measurement
        pid = transport.pid
        with pytest.raises(MCPServerStopped) as exc:
            asyncio.run(client.call_tool("hang", {}))
        msg = str(exc.value)
        assert "'brain'" in msg and "did not answer within 0.5 s" in msg, msg
        assert "restarted on the next call" in msg, msg
        assert transport.pid is None
        _wait_until(lambda: not _alive(pid), "the wedged server to die")
        res = asyncio.run(client.call_tool("fast", {"tag": "alive"}))
        assert _text(res) == "fast:alive"
        assert transport.spawn_count == 2
    finally:
        transport.close()


def test_a_timeout_spares_a_server_that_kept_talking():
    """The call times out at the floor, but the server said something after
    it was written (a log line): it is busy, not wedged — the call is given
    up (and the server told), the server keeps running."""
    transport = _transport(request_timeout=2.0)
    try:
        transport.request("tools/list")
        pid = transport.pid
        with pytest.raises(MCPError) as exc:
            transport.request("tools/call", {"name": "hang_chatty", "arguments": {"tag": "t"}})
        assert not isinstance(exc.value, MCPServerStopped), exc.value
        assert "did not answer within 2 s" in str(exc.value)
        assert transport.pid == pid and _alive(pid)
        log = json.loads(_text(transport.request(
            "tools/call", {"name": "cancelled_log", "arguments": {}})))
        assert [(e["tool"], e["tag"]) for e in log] == [("hang_chatty", "t")]
        assert transport.spawn_count == 1
    finally:
        transport.close()


# --------------------------------------------------------------------------- #
# HTTP: concurrent requests, keepalive, listeners, server info.
# --------------------------------------------------------------------------- #
def _sse(*messages: dict) -> bytes:
    return "".join(f"event: message\ndata: {json.dumps(m)}\n\n" for m in messages).encode()


class _HttpPack:
    def __init__(self):
        self.release = threading.Event()
        self.inits = 0
        self.lock = threading.Lock()

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        method = body.get("method")
        if method == "initialize":
            with self.lock:
                self.inits += 1
            return httpx.Response(200, headers={"content-type": "application/json",
                                                "mcp-session-id": "s1"},
                                  json={"jsonrpc": "2.0", "id": body["id"], "result": {
                                      "capabilities": {"tools": {"listChanged": True}},
                                      "serverInfo": {"name": "remote"},
                                      "instructions": "remote words"}})
        if "id" not in body:
            return httpx.Response(202)
        assert request.headers.get("mcp-session-id") == "s1"
        rid = body["id"]
        params = body.get("params") or {}
        name = params.get("name")
        token = (params.get("_meta") or {}).get("progressToken")
        if name == "slow":
            assert self.release.wait(_GUARD_S), "the slow call was never released"
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": rid,
                                             "result": {"content": [{"type": "text", "text": "slow"}]}})
        if name == "fast":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": rid,
                                             "result": {"content": [{"type": "text", "text": "fast"}]}})
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=_sse(
            {"jsonrpc": "2.0", "method": "notifications/progress",
             "params": {"progressToken": token, "progress": 1}},
            {"jsonrpc": "2.0", "method": "notifications/progress",
             "params": {"progressToken": "someone-else", "progress": 1}},
            {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"},
            {"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": "done"}]}},
        ))

    def transport(self) -> HttpTransport:
        return HttpTransport("http://pack.test/mcp", client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(self.handler)))


async def test_http_keepalive_listeners_and_server_info(monkeypatch):
    kept: list[int] = []
    monkeypatch.setattr(registry_mod, "deadline_keepalive", lambda: (lambda: kept.append(1)))
    pack = _HttpPack()
    client = MCPClient(pack.transport(), name="remote")
    heard = threading.Event()
    client.add_listener("notifications/tools/list_changed", lambda msg: heard.set())
    res = await client.call_tool("progress", {})
    assert _text(res) == "done"
    assert kept == [1], "progress on the call's own token keeps it alive, a foreign one does not"
    await asyncio.to_thread(heard.wait, _GUARD_S)
    assert heard.is_set()
    assert client.server_instructions == "remote words"
    assert client.server_capabilities == {"tools": {"listChanged": True}}
    assert client.server_info == {"name": "remote"}


async def test_http_calls_run_concurrently_after_one_handshake():
    pack = _HttpPack()
    client = MCPClient(pack.transport(), name="remote")
    try:
        await client.call_tool("fast", {})  # the handshake
        slow = asyncio.ensure_future(client.call_tool("slow", {}))
        fast = await asyncio.wait_for(client.call_tool("fast", {}), _GUARD_S)
        assert _text(fast) == "fast"
        assert not slow.done()
        pack.release.set()
        assert _text(await asyncio.wait_for(slow, _GUARD_S)) == "slow"
    finally:
        pack.release.set()
    assert pack.inits == 1
