"""v1.291.0 — the stdio MCP transport, hardened (deep review io-02 / io-05 / io-06).

Every test drives the REAL ``StdioTransport`` / ``MCPClient`` / ``MCPRemoteTool``
against a genuine child process (``tests/fixtures/stdio_mcp_server_v1291.py``);
only the SERVER is fake. The two registry tests go through the real app factory
and ``ToolRegistry.invoke`` with ``deadline_s`` — the product path.

THE THREE DEFECTS, measured on the daily driver's code:

* io-02 — ``MCPClient._request`` called ``transport.request`` INLINE, and the
  stdio transport blocks on ``readline`` with no deadline. Every pack call froze
  the daemon's one event loop for its whole duration (chat, Build panes, the
  dashboard and the phone all stopped: "loop ticks during call = 0"), and the
  registry's ``asyncio.timeout`` deadline could not fire during the read ("a
  0.3 s deadline took 2.00 s to act").
* io-05 — ``Popen(text=True)`` with no encoding decodes the pipe with the
  LOCALE codec (cp1252 here). MCP stdio is UTF-8 by spec; Node servers write
  raw UTF-8. Five byte values crashed the call (``'charmap' codec can't decode
  byte 0x81``) and the rest came back as mojibake. The Codex stdio shim had the
  same defect in reverse on ``sys.stdin``.
* io-06 — ``_ensure_started`` returned whenever ``_proc`` was set and never
  ``poll()``ed, so a pack whose server exited once failed with
  ``OSError: [Errno 22] Invalid argument`` on every later call until the app
  restarted.

Liveness is asserted STRUCTURALLY (the blocking request ran off the loop's
thread, and a heartbeat reached a tick target while it ran), never as a
wall-clock gap, per the v1.286.0 rule.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import psutil
import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.mcp.client import MCPClient, MCPServerStopped, StdioTransport
from iron_jarvis.mcp.tools import MCPRemoteTool
from iron_jarvis.tools.base import ToolContext

FIXTURES = Path(__file__).parent / "fixtures"
SERVER = str(FIXTURES / "stdio_mcp_server_v1291.py")
sys.path.insert(0, str(FIXTURES))
from stdio_mcp_server_v1291 import ACCENTED_TEXT  # noqa: E402

#: The slow call sleeps this long; the heartbeat ticks far more often, so an
#: offloaded call yields many ticks while an inline one yields none.
_SLOW_S = 0.6
_TICK_S = 0.01
_MIN_TICKS = 5

#: A hang guard for polls, never a performance bar.
_WAIT_S = 20.0


# --------------------------------------------------------------------------- #
# Helpers.
# --------------------------------------------------------------------------- #
def _transport(*args: str, **kw) -> StdioTransport:
    return StdioTransport(sys.executable, [SERVER, *args], **kw)


def _tool(remote: str, transport: StdioTransport, pack: str = "fake") -> MCPRemoteTool:
    return MCPRemoteTool(MCPClient(transport, name=pack), pack, remote)


def _wait_until(pred, what: str) -> None:
    deadline = time.monotonic() + _WAIT_S
    while not pred():
        assert time.monotonic() < deadline, f"gave up waiting for {what}"
        time.sleep(0.02)


async def _await_until(pred, what: str) -> None:
    deadline = time.monotonic() + _WAIT_S
    while not pred():
        assert time.monotonic() < deadline, f"gave up waiting for {what}"
        await asyncio.sleep(0.01)


class _LockSpy:
    """Wraps the transport's request lock and counts CONTENDED acquires (an
    ``acquire(timeout=)`` that returned False): only a caller parked behind the
    in-flight call ever sees one, so ``contended > 0`` is "the queued call is
    spinning on the lock" — the state the cancel test asserts on."""

    def __init__(self, real: threading.Lock) -> None:
        self._real = real
        self.contended = 0

    def acquire(self, *a, **kw) -> bool:
        ok = self._real.acquire(*a, **kw)
        if not ok:
            self.contended += 1
        return ok

    def release(self) -> None:
        self._real.release()


def _alive(pid: int) -> bool:
    try:
        proc = psutil.Process(pid)
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def _observed_rpc_thread(monkeypatch) -> dict:
    """Record which thread runs the transport's wire exchange (observe, not stub)."""
    seen: dict = {}
    real = StdioTransport._rpc

    def spy(self, method, params):
        seen["thread"] = threading.current_thread()
        return real(self, method, params)

    monkeypatch.setattr(StdioTransport, "_rpc", spy)
    return seen


async def _ticks_during(coro):
    ticks = 0
    stop = False

    async def _ticker():
        nonlocal ticks
        while not stop:
            await asyncio.sleep(_TICK_S)
            ticks += 1

    task = asyncio.ensure_future(_ticker())
    try:
        result = await coro
    finally:
        stop = True
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    return result, ticks


def _server_body(name: str, *extra: str) -> dict:
    return {
        "name": name,
        "command": sys.executable,
        "args": [SERVER, *extra],
        "auto_approve": True,
    }


def _ctx(client: TestClient, tmp_path: Path) -> ToolContext:
    platform = client.app.state.platform
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return ToolContext(
        workspace=ws,
        session_id="s1",
        agent_run_id="r1",
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )


# --------------------------------------------------------------------------- #
# io-02: off the loop, with a deadline that really stops a hung call.
# --------------------------------------------------------------------------- #
def test_a_slow_pack_call_runs_off_the_loop_through_registry_invoke(tmp_path, monkeypatch):
    """The product path: ``registry.invoke`` → ``MCPRemoteTool`` → the real
    transport. The wire exchange runs on a worker thread and the loop keeps
    ticking for the whole call."""
    seen = _observed_rpc_thread(monkeypatch)
    with TestClient(create_app(str(tmp_path))) as client:
        assert client.post("/mcp/servers", json=_server_body("fake")).json()["tools_loaded"] == 5
        platform = client.app.state.platform
        reg = platform.registry

        async def main():
            loop_thread = threading.current_thread()
            result, ticks = await _ticks_during(
                reg.invoke(
                    "mcp__fake__echo",
                    {"text": "hello", "delay_s": _SLOW_S},
                    _ctx(client, tmp_path),
                    platform.permissions,
                    session_allow=["mcp_call"],
                )
            )
            return result, ticks, loop_thread

        result, ticks, loop_thread = asyncio.run(main())
        reg.get("mcp__fake__echo").client.close()

    assert result.ok is True, result.error
    assert "hello" in result.output
    # STRUCTURAL: the blocking exchange ran on a worker thread, not the loop's.
    assert seen.get("thread") is not None, "the transport never exchanged anything"
    assert seen["thread"] is not loop_thread
    # BEHAVIOURAL: the loop kept serving other work throughout.
    assert ticks >= _MIN_TICKS, f"event loop was starved (only {ticks} ticks)"


def test_the_registry_deadline_stops_a_hung_call_and_the_next_call_restarts_the_pack(tmp_path):
    """A wedged server: the registry's ``deadline_s`` fires (it could not while
    the read blocked the loop), the failed result reads as the deadline, the
    server is KILLED so no thread stays parked on its pipe, and the very next
    call respawns it and succeeds."""
    with TestClient(create_app(str(tmp_path))) as client:
        assert client.post("/mcp/servers", json=_server_body("fake")).json()["tools_loaded"] == 5
        platform = client.app.state.platform
        reg = platform.registry
        transport = reg.get("mcp__fake__hang").client.transport
        assert isinstance(transport, StdioTransport)
        spawned_before = transport.spawn_count
        pid_before = transport.pid
        try:
            hung = asyncio.run(
                reg.invoke(
                    "mcp__fake__hang", {}, _ctx(client, tmp_path), platform.permissions,
                    session_allow=["mcp_call"], deadline_s=0.3,
                )
            )
            assert hung.ok is False
            assert "did not finish within" in (hung.error or ""), hung.error
            # The server was killed, and the worker thread let go of the transport.
            _wait_until(lambda: transport.pid is None, "the hung server to be forgotten")
            _wait_until(lambda: not _alive(pid_before), "the hung server to die")
            assert transport._lock.acquire(timeout=_WAIT_S), "a worker still owns the transport"
            transport._lock.release()

            again = asyncio.run(
                reg.invoke(
                    "mcp__fake__echo", {"text": "back"}, _ctx(client, tmp_path),
                    platform.permissions, session_allow=["mcp_call"], deadline_s=30,
                )
            )
        finally:
            transport.close()
    assert again.ok is True, again.error
    assert "back" in again.output
    assert transport.spawn_count == spawned_before + 1, "exactly one respawn"


def test_the_transports_own_deadline_kills_a_wedged_server_and_names_the_pack():
    """Callers with no registry deadline (the LTM brain from sync code) are
    bounded by the transport itself: the call ends with an error that names
    the pack and says it will be restarted, the server is gone, the next call
    works."""
    transport = _transport(request_timeout=0.5)
    client = MCPClient(transport, name="brain")
    try:
        transport.request("tools/list")  # handshake outside the measurement
        pid_before = transport.pid
        with pytest.raises(MCPServerStopped) as exc:
            asyncio.run(client.call_tool("hang", {}))
        msg = str(exc.value)
        assert "'brain'" in msg and "restarted on the next call" in msg, msg
        assert "did not answer within 0.5 s" in msg, msg
        assert transport.pid is None
        _wait_until(lambda: not _alive(pid_before), "the wedged server to die")

        res = asyncio.run(client.call_tool("echo", {"text": "alive"}))
        assert res["content"][0]["text"] == "alive"
        assert transport.spawn_count == 2
    finally:
        transport.close()


def test_the_transport_floor_never_undercuts_the_registry_deadline(tmp_path, monkeypatch):
    """REVIEW DEFECT (major): the first cut gave every registry pack a hard
    120 s transport floor while ``config.tool_call_timeout_s`` (the deadline
    the chat lanes and the agent runtime pass to ``registry.invoke``, set in
    Settings) defaults to 600 s — a slow-but-healthy pack call taking 2-10 min
    was killed at the floor and the user's setting silently never applied.

    Pinned through the REAL app: with the module default shrunk to a fraction
    of the echo's delay, a registry call under a generous deadline must still
    succeed, because the registry's transport carries NO floor (the deadline +
    ``abort()`` bound it). The default still applies to a transport built with
    no explicit timeout (the LTM brain), and it is read at CONSTRUCTION time —
    which is what lets this test prove the seam instead of asserting a field.
    """
    from iron_jarvis.mcp import client as client_mod

    floor = 0.2
    delay = floor * 3
    monkeypatch.setattr(client_mod, "DEFAULT_REQUEST_TIMEOUT_S", floor)
    # A deadline-less caller (the LTM brain's shape) DOES get the floor ...
    assert StdioTransport(sys.executable, [SERVER]).request_timeout == floor
    with TestClient(create_app(str(tmp_path))) as client:
        assert client.post("/mcp/servers", json=_server_body("fake")).json()["tools_loaded"] == 5
        platform = client.app.state.platform
        reg = platform.registry
        transport = reg.get("mcp__fake__echo").client.transport
        assert isinstance(transport, StdioTransport)
        try:
            # ... and the registry's pack does not: its deadline is the bound.
            assert transport.request_timeout is None
            res = asyncio.run(
                reg.invoke(
                    "mcp__fake__echo", {"text": "slow but fine", "delay_s": delay},
                    _ctx(client, tmp_path), platform.permissions,
                    session_allow=["mcp_call"], deadline_s=30,
                )
            )
        finally:
            transport.close()
    assert res.ok is True, f"a healthy call was killed by the transport floor: {res.error}"
    assert "slow but fine" in res.output
    assert transport.spawn_count == 1, "the server was restarted mid-call"


def test_a_cancelled_call_aborts_only_the_in_flight_request(monkeypatch):
    """Cancel while one call is on the wire and another waits its turn: the
    in-flight server is killed (the waiting caller's abort must not kill a
    server it never owned), and the queued call never sends."""
    transport = _transport(request_timeout=None)
    client = MCPClient(transport, name="fake")
    lock_spy = _LockSpy(transport._lock)
    monkeypatch.setattr(transport, "_lock", lock_spy)

    async def main():
        await client.list_tools()
        hung = asyncio.create_task(client.call_tool("hang", {}))
        # Wait for the state asserted below, never a proxy: the hung call OWNS
        # the server (its cancel token is the in-flight one) ...
        await _await_until(lambda: transport._inflight is not None, "the hung call to reach the wire")
        queued = asyncio.create_task(client.call_tool("echo", {"text": "queued"}))
        # ... and the queued call is spinning on the lock behind it.
        await _await_until(lambda: lock_spy.contended > 0, "the queued call to park on the lock")
        pid = transport.pid
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        # Only the queued call gave up: the in-flight server is untouched.
        assert transport.pid == pid and _alive(pid)
        hung.cancel()
        with pytest.raises(asyncio.CancelledError):
            await hung
        return pid

    try:
        pid = asyncio.run(main())
        _wait_until(lambda: transport.pid is None, "the aborted server to be forgotten")
        _wait_until(lambda: not _alive(pid), "the aborted server to die")
        res = asyncio.run(client.call_tool("echo", {"text": "after"}))
        assert res["content"][0]["text"] == "after"
    finally:
        transport.close()


def test_a_cancel_that_lands_while_the_server_is_spawning_does_not_wedge_the_pack(monkeypatch):
    """REVIEW DEFECT (should-fix): a cancel/deadline that lands while the
    transport is still SPAWNING wedged the pack until the app restarted.
    ``request()`` set ``_inflight`` and spawned with no re-check of the
    token; ``abort()`` ran ``close()`` against a ``_proc`` that was still
    ``None`` (nothing to kill, and it never runs again); the worker then
    swapped the server in, handshook and SENT the cancelled request, and
    with ``request_timeout=None`` (every registry pack) parked in ``_read``
    for ever holding ``_lock`` — every later call spun on the lock until its
    own deadline, none could kill (their token was not ``_inflight``), and
    the parked default-executor thread stalled a tidy stop for 300 s.
    Window = the spawn; trigger = Stop or a client disconnect during a
    pack's first call (an ``npx`` cold start).

    The ordering is HELD, not hoped for: the transport's ``Popen`` is gated so
    the cancel lands exactly while ``_inflight`` is set and ``pid`` is None
    (the reviewer's probe caught the same window on real timing). The
    request is ``hang`` so a request that IS sent parks, as it did live.
    """
    from iron_jarvis.mcp import client as client_mod

    real_popen = subprocess.Popen
    spawning = threading.Event()
    release = threading.Event()
    spawned: list = []

    def gated_popen(*a, **kw):
        # Only the FIRST spawn is held; everything after passes straight through.
        if not spawning.is_set():
            spawning.set()
            assert release.wait(_WAIT_S), "the test never released the spawn"
        proc = real_popen(*a, **kw)
        spawned.append(proc)
        return proc

    monkeypatch.setattr(client_mod.subprocess, "Popen", gated_popen)
    transport = _transport(request_timeout=None)  # a registry pack's shape
    client = MCPClient(transport, name="fake")

    def _lock_free() -> bool:
        if transport._lock.acquire(blocking=False):
            transport._lock.release()
            return True
        return False

    async def main():
        try:
            first = asyncio.create_task(client.call_tool("hang", {}))
            await _await_until(
                lambda: spawning.is_set() and transport._inflight is not None
                and transport.pid is None,
                "the first call to be mid-spawn",
            )
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            # abort() has run against a transport with no server yet.
            assert transport.pid is None
            release.set()
            # The cancelled worker lets go of the transport instead of
            # parking on a request it should never have sent ...
            await _await_until(_lock_free, "the cancelled worker to let go of the transport")
            assert transport._inflight is None
            # ... and the server it spawned is not kept.
            assert transport.pid is None, "the cancelled call's server was kept"
            # The very next call on the same transport works.
            return await client.call_tool("echo", {"text": "after"})
        finally:
            # Also the escape hatch for the RED shape: killing the server is
            # what unparks a wedged worker, so the loop can shut down.
            transport.close()

    res = asyncio.run(main())
    assert res["content"][0]["text"] == "after"
    assert spawned, "the transport never spawned"
    cancelled_pid = spawned[0].pid
    _wait_until(lambda: not _alive(cancelled_pid), "the cancelled call's server to die")
    _wait_until(
        lambda: not any(
            th.is_alive() and th.name == f"mcp-stdio-{cancelled_pid}" for th in threading.enumerate()
        ),
        "the cancelled call's reader thread to end",
    )
    assert transport.spawn_count == 2, "the next call must spawn a fresh server"


def test_concurrent_calls_never_cross_wire(monkeypatch):
    """Calls now arrive from worker threads; the lock keeps ids and the single
    stdout reader in step: wire exchanges NEVER overlap (a sequential fake
    server answering in order can hide an unlocked transport by luck, so the
    structure is asserted, not just the answers), and each caller gets ITS
    answer."""
    real = StdioTransport._rpc
    guard = threading.Lock()
    state = {"active": 0, "peak": 0}

    def spy(self, method, params):
        with guard:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        try:
            return real(self, method, params)
        finally:
            with guard:
                state["active"] -= 1

    monkeypatch.setattr(StdioTransport, "_rpc", spy)
    transport = _transport()
    client = MCPClient(transport, name="fake")

    async def main():
        await client.list_tools()
        return await asyncio.gather(*[
            client.call_tool("echo", {"text": f"n{i}", "delay_s": 0.05 * (i % 3)})
            for i in range(6)
        ])

    try:
        results = asyncio.run(main())
    finally:
        transport.close()
    assert [r["content"][0]["text"] for r in results] == [f"n{i}" for i in range(6)]
    assert state["peak"] == 1, f"{state['peak']} exchanges were on the wire at once"
    assert transport.spawn_count == 1


def test_the_sync_ltm_driver_still_works_from_a_worker_thread():
    """``ltm/mcp_brain._resolve_maybe_async`` runs ``asyncio.run`` on whatever
    thread the LTM contract is called from; the thread hop inside must not
    break that."""
    from iron_jarvis.ltm.mcp_brain import _resolve_maybe_async

    transport = _transport()
    client = MCPClient(transport, name="brain")
    box: dict = {}

    def work():
        try:
            box["tools"] = _resolve_maybe_async(client.list_tools())
            box["res"] = _resolve_maybe_async(client.call_tool("echo", {"text": "sync"}))
        except BaseException as exc:  # noqa: BLE001 — surfaced below
            box["error"] = exc

    th = threading.Thread(target=work)
    th.start()
    th.join(_WAIT_S)
    transport.close()
    assert not th.is_alive(), "the sync driver hung"
    assert "error" not in box, box.get("error")
    assert {t["name"] for t in box["tools"]} >= {"echo", "hang"}
    assert box["res"]["content"][0]["text"] == "sync"


# --------------------------------------------------------------------------- #
# io-05: UTF-8 on the wire, both directions, both programs.
# --------------------------------------------------------------------------- #
def test_non_ascii_results_round_trip_as_utf8():
    """0x81 (Á) is undefined in cp1252 and crashed the call; ” and 😀 carry more
    such bytes; ñ/é decoded to mojibake. The exact string must arrive."""
    transport = _transport()
    tool = _tool("accented", transport)
    try:
        res = asyncio.run(tool.execute({}, None))
    finally:
        transport.close()
    assert res.ok, res.error
    assert res.output == ACCENTED_TEXT


def test_non_ascii_arguments_reach_the_server_intact():
    transport = _transport()
    tool = _tool("echo", transport)
    try:
        res = asyncio.run(tool.execute({"text": ACCENTED_TEXT}, None))
    finally:
        transport.close()
    assert res.ok, res.error
    assert res.output == ACCENTED_TEXT


class _EchoDaemon(BaseHTTPRequestHandler):
    """Stands in for ``POST /mcp``: records the request, echoes the arguments."""

    seen: list = []

    def do_POST(self):  # noqa: N802 — http.server's spelling
        body = self.rfile.read(int(self.headers["Content-Length"]))
        msg = json.loads(body.decode("utf-8"))
        _EchoDaemon.seen.append(msg)
        text = ((msg.get("params") or {}).get("arguments") or {}).get("client", "")
        out = json.dumps(
            {"jsonrpc": "2.0", "id": msg.get("id"),
             "result": {"content": [{"type": "text", "text": text}]}},
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):  # noqa: D102 — quiet
        pass


def test_the_codex_stdio_shim_relays_non_ascii_both_ways():
    """The real shim as a child process, the way a Rust/Node harness runs it,
    with NO UTF-8 hint in its environment: what the harness writes reaches the
    daemon byte-for-byte, and the daemon's answer reaches the harness as UTF-8."""
    _EchoDaemon.seen = []
    httpd = HTTPServer(("127.0.0.1", 0), _EchoDaemon)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        env = {**os.environ, "IRONJARVIS_MCP_URL": f"http://127.0.0.1:{port}/mcp",
               "IRONJARVIS_MCP_TOKEN": "tok"}
        env.pop("PYTHONUTF8", None)
        env.pop("PYTHONIOENCODING", None)
        line = json.dumps(
            {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
             "params": {"name": "search", "arguments": {"client": ACCENTED_TEXT}}},
            ensure_ascii=False,
        ) + "\n"
        proc = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "iron_jarvis.mcpserver.stdio_shim"],
            input=line.encode("utf-8"), capture_output=True, env=env, timeout=60,
        )
    finally:
        httpd.shutdown()
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert _EchoDaemon.seen, "the shim never reached the daemon"
    assert _EchoDaemon.seen[0]["params"]["arguments"]["client"] == ACCENTED_TEXT
    answer = json.loads(proc.stdout.decode("utf-8").strip().splitlines()[-1])
    assert answer["result"]["content"][0]["text"] == ACCENTED_TEXT
    assert not proc.stdout.endswith(b"\r\n"), "CRLF on a JSON line"


# --------------------------------------------------------------------------- #
# io-06: a server that exits is restarted on the next call, never mid-call.
# --------------------------------------------------------------------------- #
def test_a_server_that_exits_is_named_and_restarted_on_the_next_call():
    transport = _transport()
    die = _tool("die", transport, pack="brave_search")
    echo = _tool("echo", transport, pack="brave_search")
    try:
        transport.request("tools/list")
        pid_before = transport.pid
        first = asyncio.run(die.execute({}, None))
        assert first.ok is False
        assert "'brave_search'" in (first.error or ""), first.error
        assert "restarted on the next call" in (first.error or ""), first.error
        assert "exit code 3" in (first.error or ""), first.error
        assert transport.pid is None, "the dead server must be forgotten at once"

        second = asyncio.run(echo.execute({"text": "reborn"}, None))
    finally:
        transport.close()
    assert second.ok is True, f"pack stays dead after its server exited: {second.error}"
    assert second.output == "reborn"
    assert transport.spawn_count == 2, "exactly one respawn, on the NEXT call"
    assert transport.pid != pid_before


def test_a_server_that_died_quietly_is_respawned_by_ensure_started():
    """The exit is noticed even when no call saw the EOF: ``poll()`` on entry."""
    transport = _transport()
    try:
        transport.request("tools/list")
        proc = transport._proc
        proc.kill()
        proc.wait(timeout=_WAIT_S)
        res = transport.request("tools/call", {"name": "echo", "arguments": {"text": "hi"}})
    finally:
        transport.close()
    assert res["content"][0]["text"] == "hi"
    assert transport.spawn_count == 2


def test_concurrent_closers_kill_the_server_exactly_once(monkeypatch):
    """REVIEW DEFECT (minor): ``abort()`` (the cancelled caller's thread), the
    in-flight worker's own timeout/EOF path and a respawn can all close at
    once. A second kill on the same Windows Job handle runs
    ``TerminateJobObject`` + ``CloseHandle`` on an already-closed handle value
    that Windows may have recycled for ANOTHER Job — another pack's, a
    terminal pane's — and kills that tree. So exactly ONE closer may receive
    the live (proc, out, job, reader) tuple.

    The race is INJECTED, not hoped for (a barrier alone never shows a
    twenty-bytecode window on an idle machine, even at a 1 µs switch
    interval): while the closers run, the transport's ``_proc`` read stalls
    the FIRST reader for a bounded moment so a second closer can read it too.
    With the swap locked the second closer cannot enter until the first has
    nulled the fields, so it reads ``None``; without the lock both read the
    live process and both kill it. The respawn branch is then shown to go
    through the same swap-first ``close()``.
    """
    live_kills: list = []
    real_kill = StdioTransport._kill

    def kill_spy(proc, out, job, reader):
        if proc is not None:
            live_kills.append(proc)
        return real_kill(proc, out, job, reader)

    monkeypatch.setattr(StdioTransport, "_kill", staticmethod(kill_spy))
    closes = {"n": 0}
    real_close = StdioTransport.close

    def close_spy(self):
        closes["n"] += 1
        return real_close(self)

    monkeypatch.setattr(StdioTransport, "close", close_spy)

    gate = {"racing": False, "readers": 0}
    gate_lock = threading.Lock()
    second_reader = threading.Event()

    class _RacyTransport(StdioTransport):
        """``_proc`` is a property so the read inside close() can be stalled."""

        @property
        def _proc(self):
            value = self.__dict__.get("_proc_value")  # what THIS reader saw
            if gate["racing"]:
                with gate_lock:
                    gate["readers"] += 1
                    order = gate["readers"]
                if order == 1:
                    second_reader.wait(0.5)  # a bounded chance for a second reader
                elif order == 2:
                    second_reader.set()
            return value

        @_proc.setter
        def _proc(self, value):
            self.__dict__["_proc_value"] = value

    transport = _RacyTransport(sys.executable, [SERVER])
    n = 4
    barrier = threading.Barrier(n)

    def closer():
        barrier.wait(_WAIT_S)
        transport.close()

    transport.request("tools/list")
    first_proc = transport._proc
    gate["racing"] = True
    try:
        threads = [threading.Thread(target=closer) for _ in range(n)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(_WAIT_S)
        assert not any(th.is_alive() for th in threads), "a closer hung"
    finally:
        gate["racing"] = False
    assert gate["readers"] >= n, "the closers never read the live fields"
    assert live_kills == [first_proc], f"the live server was handed to {len(live_kills)} killers"
    assert transport.pid is None

    # The respawn branch: a server that died quietly is forgotten through the
    # same swap-first close(), never by a bare _kill on the live fields.
    try:
        transport.request("tools/list")
        second_proc = transport._proc
        second_proc.kill()
        second_proc.wait(timeout=_WAIT_S)
        before = closes["n"]
        res = transport.request("tools/call", {"name": "echo", "arguments": {"text": "hi"}})
        assert res["content"][0]["text"] == "hi"
        assert closes["n"] == before + 1, "the respawn did not go through close()"
        assert live_kills == [first_proc, second_proc]
    finally:
        transport.close()


# --------------------------------------------------------------------------- #
# close() kills the TREE (npx's server is a grandchild of cmd.exe).
# --------------------------------------------------------------------------- #
def test_a_kill_reaches_the_launchers_wedged_grandchild_that_holds_the_pipe():
    """The ``npx`` shape: the process the transport spawned is a launcher and
    the server holding the pipe is its grandchild. Killing the launcher alone
    leaves a WEDGED server (one that is not reading stdin, so the EOF from the
    closed pipe never reaches it) alive on the pipe for ever; the deadline's
    kill — and ``close()`` — must take the whole tree down."""
    transport = _transport("--launcher", request_timeout=0.5)
    try:
        transport.request("tools/list")
        launcher_pid = transport.pid
        res = transport.request("tools/call", {"name": "pid", "arguments": {}})
        server_pid = int(res["content"][0]["text"])
        assert server_pid != launcher_pid, "the fixture must run the server as a grandchild"
        assert _alive(server_pid)
        with pytest.raises(MCPServerStopped):
            transport.request("tools/call", {"name": "hang", "arguments": {}})
    finally:
        transport.close()
    _wait_until(lambda: not _alive(launcher_pid), "the launcher to die")
    _wait_until(lambda: not _alive(server_pid), "the wedged grandchild server to die")
    assert transport.pid is None


def test_the_reader_thread_does_not_outlive_a_closed_server():
    transport = _transport()
    transport.request("tools/list")
    reader = transport._reader
    assert reader is not None and reader.is_alive()
    transport.close()
    reader.join(_WAIT_S)
    assert not reader.is_alive(), "the stdout reader stayed parked after close()"
