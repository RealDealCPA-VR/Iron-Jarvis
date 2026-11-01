"""Independent review of v1.420.0 (C3.1): the shared relay listener and the
keep-alive upstream pool.

The rule the pool must keep: a Messages POST that may have REACHED Anthropic is
never sent a second time. A kept connection may be replaced once only when it
was found closed (the server's keep-alive ran out under the request: a reset, a
close with no status line) -- never after a TIMEOUT waiting for the answer, and
never after a garbled answer: in both cases the server read the whole request
and may be running it on the user's account.

Everything is offline: loopback fake upstreams only.
"""

from __future__ import annotations

import http.client
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

from iron_jarvis.providers.adapters.claude_native import admission as adm_mod
from iron_jarvis.providers.adapters.claude_native.admission import Admission


def _answer(marker: str) -> bytes:
    events = [
        {"type": "message_start", "message": {"id": "m", "type": "message", "role": "assistant",
                                              "model": "m", "content": [], "stop_reason": None,
                                              "usage": {"input_tokens": 1, "output_tokens": 0}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": marker}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}},
        {"type": "message_stop"},
    ]
    return "".join("data: " + json.dumps(e) + "\n\n" for e in events).encode()


class Upstream:
    """Loopback fake Anthropic, HTTP/1.1 keep-alive. ``second``: what it does with
    the SECOND request on one connection -- "answer", "stall" (reads it, never
    answers) or "garble" (reads it, writes a broken status line, closes)."""

    def __init__(self, second: str = "answer") -> None:
        self.lock = threading.Lock()
        self.hits: list[tuple[str, str]] = []  # (authorization, body marker)
        self.connections = 0
        self.release = threading.Event()
        owner = self

        class Peer(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self) -> None:
                with owner.lock:
                    owner.connections += 1
                self.served = 0
                super().setup()

            def log_message(self, *args) -> None:
                pass

            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers["Content-Length"]))
                try:
                    marker = json.loads(body).get("marker", "")
                except ValueError:
                    marker = ""
                with owner.lock:
                    owner.hits.append((self.headers.get("Authorization", ""), marker))
                self.served += 1
                if self.served == 2 and second == "stall":
                    owner.release.wait(10)
                    self.close_connection = True
                    return
                if self.served == 2 and second == "garble":
                    self.wfile.write(b"NOT-HTTP garbage\r\n\r\n")
                    self.wfile.flush()
                    self.close_connection = True
                    return
                data = _answer(marker or "ok")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                self.wfile.flush()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Peer)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def stop(self) -> None:
        self.release.set()
        self.server.shutdown()
        self.thread.join(5)
        self.server.server_close()


@pytest.fixture(autouse=True)
def _pool():
    adm_mod.POOL.clear()
    yield
    adm_mod.POOL.clear()


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


def _post(url: str, *, marker: str = "", auth: str = "", timeout: float = 15.0) -> int:
    parts = urlsplit(url)
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=timeout)
    try:
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = auth
        conn.request("POST", parts.path + "/v1/messages",
                     json.dumps({"marker": marker, "messages": []}).encode(), headers)
        resp = conn.getresponse()
        resp.read()
        return resp.status
    except (OSError, http.client.HTTPException):
        return -1
    finally:
        conn.close()


def _one_call(url: str, timeout: float) -> Admission:
    adm = Admission(url, timeout)
    try:
        _post(adm.url)
    finally:
        adm.close()
    return adm


# --------------------------------------------------------------------------- #
# 1. a request that may have reached the server is never sent again
# --------------------------------------------------------------------------- #
def test_a_kept_connection_that_times_out_after_the_request_is_not_sent_again(upstream):
    up = upstream(second="stall")
    first = _one_call(up.url, 10.0)
    assert first.status == 200 and up.connections == 1
    second = _one_call(up.url, 0.5)  # the kept connection: the server reads it and stalls
    up.release.set()
    assert len(up.hits) == 2, (
        "a timeout waiting for the answer is not a closed connection: the server "
        f"had the whole request, and it was sent again ({len(up.hits)} upstream requests)"
    )
    assert second.failure is not None and second.status is None


def test_a_garbled_answer_on_a_kept_connection_is_not_sent_again(upstream):
    up = upstream(second="garble")
    assert _one_call(up.url, 10.0).status == 200
    second = _one_call(up.url, 5.0)
    assert len(up.hits) == 2, (
        "the server read the request and answered (badly): it must not be re-sent "
        f"({len(up.hits)} upstream requests)"
    )
    assert second.failure is not None


def test_a_kept_connection_that_was_closed_is_still_replaced_once(monkeypatch, upstream):
    """The control: the keep-alive race (closed under the request) keeps its one retry."""
    up = upstream()
    assert _one_call(up.url, 10.0).status == 200
    with adm_mod.POOL.lock:
        kept = [conn for v in adm_mod.POOL.idle.values() for conn, _ in v]
    assert len(kept) == 1
    kept[0].sock.shutdown(2)  # dead under the request; the open-check is blinded
    monkeypatch.setattr(adm_mod, "_still_open", lambda conn: True)
    second = _one_call(up.url, 10.0)
    assert second.status == 200 and second.failure is None
    assert len(up.hits) == 2


# --------------------------------------------------------------------------- #
# 2. one listener, eight calls at once: each call reaches only its own answer
# --------------------------------------------------------------------------- #
def test_eight_calls_at_once_each_get_their_own_answer_and_credentials(upstream):
    up = upstream()
    relays = [Admission(up.url, 15.0) for _ in range(8)]
    statuses: dict[int, int] = {}
    start = threading.Barrier(8)

    def go(i: int) -> None:
        start.wait(10)
        statuses[i] = _post(relays[i].url, marker=f"call-{i}", auth=f"Bearer account-{i}")

    threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        assert statuses == {i: 200 for i in range(8)}
        for i, relay in enumerate(relays):
            text = "".join(b.get("text", "") for b in relay.capture.message["content"])
            assert text == f"call-{i}", "a call read another call's answer"
            assert relay.denied == 0 and relay.status == 200
        assert sorted(up.hits) == sorted((f"Bearer account-{i}", f"call-{i}") for i in range(8)), \
            "each account's credentials went with its own request only"
        with adm_mod.POOL.lock:
            idle = sum(len(v) for v in adm_mod.POOL.idle.values())
        assert 1 <= idle <= adm_mod.POOL_MAX
        # A later round reuses a kept connection and still keeps every call apart.
        again = Admission(up.url, 15.0)
        try:
            assert _post(again.url, marker="again", auth="Bearer account-x") == 200
            assert "".join(b.get("text", "") for b in again.capture.message["content"]) == "again"
        finally:
            again.close()
        assert up.connections <= 8
    finally:
        for relay in relays:
            relay.close()


def test_a_route_is_unguessable_and_an_unknown_one_reaches_nothing(upstream):
    up = upstream()
    relay = Admission(up.url, 10.0)
    try:
        token = relay.prefix.rsplit("/", 1)[-1]
        assert len(token) >= 43  # 32 random bytes
        base = relay.url[: -len(relay.prefix)]
        assert _post(base + "/admit/" + token[:-1] + ("x" if token[-1] != "x" else "y")) == 404
        assert _post(base + relay.prefix + "/..") == 404
        assert up.hits == [] and relay.used is False
    finally:
        relay.close()


def test_no_other_socket_can_bind_the_shared_relay_port(monkeypatch):
    """The ONE listener lives as long as the daemon and every call's sign-in header
    goes through it: another socket must not be able to bind the same port and take
    its connections (Windows lets a second SO_REUSEADDR socket do exactly that)."""
    import socket

    monkeypatch.setattr(adm_mod, "_LISTENER", None)
    server = adm_mod.listener()
    try:
        other = socket.socket()
        try:
            other.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            with pytest.raises(OSError):
                other.bind(("127.0.0.1", server.server_port))
        finally:
            other.close()
    finally:
        server.shutdown()
        server.server_close()


# --------------------------------------------------------------------------- #
# 3. eight REAL transport calls at once (fake CLI through a shim, the real relay)
# --------------------------------------------------------------------------- #
async def test_eight_released_calls_at_once_leave_nothing_behind(tmp_path, monkeypatch):
    import asyncio
    import os
    import tempfile
    from pathlib import Path

    from iron_jarvis.providers import cli_auth
    from iron_jarvis.providers.adapters.claude_native import transport
    from iron_jarvis.providers.cli_auth import CliAuthProbe
    from tests import test_claude_native_round_cost_v1420 as lane

    for key in [k for k in os.environ if k.startswith("ANTHROPIC_")] + list(transport.BACKEND_SWITCHES):
        monkeypatch.delenv(key, raising=False)
    for key in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(cli_auth, "DEFAULT_PROBE", CliAuthProbe(which=lambda b: None))
    monkeypatch.setattr(transport, "DEFAULT_PROBE", cli_auth.DEFAULT_PROBE)
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    monkeypatch.setenv("LANE_LINGER", "0.3")
    procs_seen: list = []
    real_spawn = transport._Resources.spawn

    def spawn(self, env):
        proc = real_spawn(self, env)
        procs_seen.append(proc)
        return proc

    monkeypatch.setattr(transport._Resources, "spawn", spawn)
    with adm_mod.listener().routes_lock:
        before = set(adm_mod.listener().routes)
    up = lane.Upstream()
    try:
        ad = lane._adapter(tmp_path, up)
        replies = await asyncio.wait_for(asyncio.gather(*[
            ad.complete(system="", messages=[lane._user(f"conversation {i}")], tools=[])
            for i in range(8)
        ]), 120)
        assert [r.text for r in replies] == ["Hello"] * 8
        await asyncio.wait_for(transport.drain_teardowns(), 120)
        assert len(procs_seen) == 8 and all(p.poll() is not None for p in procs_seen)
        assert transport.pending_teardowns() == 0
        assert [p for p in Path(temp).iterdir() if p.name.startswith("ij-claude-")] == []
        assert up.hits == 8
        with adm_mod.listener().routes_lock:
            assert set(adm_mod.listener().routes) <= before, "every call's route came off the listener"
    finally:
        up.stop()


# --------------------------------------------------------------------------- #
# 4. two conversations that open with the same words share a key: both
#    teardowns are still counted and waited for
# --------------------------------------------------------------------------- #
class _HeldTeardown:
    def __init__(self, chain: str) -> None:
        from iron_jarvis.providers.adapters.claude_native import transport

        self.chain = chain
        self.proc = None
        self.exit_at = None
        self.clock = transport._Clock()
        self.gate = threading.Event()

    def close(self, grace_s: float = 0.0) -> None:
        self.gate.wait(30)


async def test_teardowns_under_one_key_are_all_counted_and_waited_for():
    import asyncio

    from iron_jarvis.providers.adapters.claude_native import transport

    a, b = _HeldTeardown("same-opening"), _HeldTeardown("same-opening")
    transport._teardown_later(a, 0)
    transport._teardown_later(b, 0)
    try:
        assert transport.pending_teardowns() == 2, "an earlier teardown under the same key was forgotten"
        # Both start waiting while both teardowns run; the LATER one ends first.
        waiter = asyncio.ensure_future(transport._after_previous("same-opening"))
        drain = asyncio.ensure_future(transport.drain_teardowns())
        await asyncio.sleep(0.1)
        b.gate.set()
        await asyncio.sleep(0.4)
        assert not waiter.done(), "the next call spawned while that conversation's earlier CLI was still up"
        assert not drain.done(), "drain_teardowns returned with a teardown still running"
        a.gate.set()
        await asyncio.wait_for(asyncio.gather(waiter, drain), 10)
        assert transport.pending_teardowns() == 0
    finally:
        a.gate.set()
        b.gate.set()
        await asyncio.wait_for(transport.drain_teardowns(), 10)
