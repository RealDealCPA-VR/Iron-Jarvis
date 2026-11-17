"""The bridge that gives coding tools Iron Jarvis's tools survives a restart and a
dropped connection (third north-star fix plan, group 22: MC3, MC4, MC7, MC8).

* **MC3.** After a restart of Iron Jarvis (every update) or an hour with no
  call, the door has forgotten the coding tool's session and answers every
  call "no MCP session". The bridge (``mcpserver/stdio_shim.py``) now starts a
  new session by itself, replaying the tool's own ``initialize`` and
  ``notifications/initialized``, and repeats the failed call ONCE; a daemon
  that is not answering at all is retried for a short bounded time (injected
  sleeper: no real sleep here), then the tool reads a plain sentence. A token
  that no longer verifies is answered once, never retried.
* **MC4.** Stopping a tool that left a program holding its output open no
  longer hangs: the code host closes only the pipes whose reader has finished
  (the guard ``hosted/process.py`` already had).
* **MC7.** The bridge never goes through a proxy and never sends the pane's
  token anywhere but this PC.
* **MC8.** A crash while serving a call reaches the coding tool as a plain
  sentence, never as an exception's name.

Nothing here starts a real coding tool or reaches the owner's daemon: the door
is the app's own route on a ``TestClient``; the HTTP stand-ins listen on
ephemeral loopback ports; the one real child process (MC4) is a tiny Python
script, killed in a ``finally``.
"""

from __future__ import annotations

import io
import json
import os
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from iron_jarvis.browser.panetokens import (
    INSTALL_TOKEN_ENV,
    MCP_TOKEN_ENV,
    PANE_TOKEN_EXPIRED_MESSAGE,
    PaneTokenStore,
)
from iron_jarvis.daemon.routes import mcpserver as mcp_routes
from iron_jarvis.mcp.client import PROTOCOL_VERSION as CLIENT_PROTOCOL_VERSION
from iron_jarvis.mcpserver import jsonrpc, stdio_shim
from iron_jarvis.mcpserver.session import SESSION_HEADER, McpSessionRegistry

PANE_ID = "term_g22_pane"
INSTALL_TOKEN = "install-bearer-for-the-whole-app"

INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": CLIENT_PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "pytest-tool", "version": "1"},
    },
}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}
LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}


def _line(payload: dict) -> str:
    return json.dumps(payload)


class FakePane:
    def __init__(self, pane_id: str, capabilities: dict, cwd: str = "") -> None:
        self.id = pane_id
        self.capabilities = dict(capabilities)
        self.cwd = cwd
        self.alive = True


class RoutePoster:
    """The shim's transport, pointed at the app's REAL ``/mcp`` route."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.calls: list[dict] = []

    def post(self, url, body, headers, timeout):
        sent = json.loads(body.decode("utf-8"))
        self.calls.append({"body": sent, "headers": dict(headers)})
        response = self.client.post("/mcp", content=body, headers=headers)
        return stdio_shim.HttpReply(
            status=response.status_code,
            text=response.text,
            headers={str(k).lower(): str(v) for k, v in response.headers.items()},
        )


def _as_reply(response) -> stdio_shim.HttpReply:
    return stdio_shim.HttpReply(
        status=response.status_code,
        text=response.text,
        headers={str(k).lower(): str(v) for k, v in response.headers.items()},
    )


@pytest.fixture()
def door(platform, monkeypatch):
    """A bare app with the real ``/mcp`` route, token store and session registry."""
    monkeypatch.setenv("IRONJARVIS_TOKEN", INSTALL_TOKEN)
    platform.config.browser_access = "interactive"
    pane = FakePane(PANE_ID, {"browser": True}, cwd=str(platform.config.home))
    tokens = PaneTokenStore(pane_lookup=lambda pid: pane if pid == PANE_ID else None)
    sessions = McpSessionRegistry()
    platform.pane_tokens = tokens
    platform.mcp_sessions = sessions
    app = FastAPI()
    mcp_routes.register(app, SimpleNamespace(platform=platform))
    client = TestClient(app)
    token = tokens.mint(PANE_ID, {"browser": True})
    return SimpleNamespace(platform=platform, tokens=tokens, sessions=sessions,
                           client=client, token=token, pane=pane)


def _forgotten_answer(door) -> stdio_shim.HttpReply:
    """What the REAL route answers a call whose session it has forgotten."""
    response = door.client.post(
        "/mcp", json=LIST,
        headers={"Authorization": f"Bearer {door.token}",
                 "Accept": "application/json, text/event-stream",
                 SESSION_HEADER: "a-session-this-daemon-never-made"},
    )
    assert response.status_code >= 400, response.text
    return _as_reply(response)


class ScriptedDaemon:
    """A stand-in daemon that restarts between two calls.

    ``initialize`` mints ``session-1``, ``session-2``... A call carrying a
    session it no longer knows gets ``forgotten`` (the real route's answer,
    captured once). ``restart()`` forgets every session.
    """

    def __init__(self, forgotten: stdio_shim.HttpReply) -> None:
        self.forgotten = forgotten
        self.known: set[str] = set()
        self.minted = 0
        self.calls: list[dict] = []

    def restart(self) -> None:
        self.known.clear()

    def post(self, url, body, headers, timeout):
        sent = json.loads(body.decode("utf-8"))
        self.calls.append({"body": sent, "headers": dict(headers)})
        method = sent.get("method")
        if method == "initialize":
            self.minted += 1
            sid = f"session-{self.minted}"
            self.known.add(sid)
            return stdio_shim.HttpReply(
                status=200,
                text=json.dumps({"jsonrpc": "2.0", "id": sent.get("id"), "result": {
                    "protocolVersion": CLIENT_PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "iron-jarvis"},
                }}),
                headers={"content-type": "application/json", "mcp-session-id": sid},
            )
        if "id" not in sent:
            return stdio_shim.HttpReply(status=202, text="", headers={})
        if headers.get(SESSION_HEADER) not in self.known:
            return self.forgotten
        return stdio_shim.HttpReply(
            status=200,
            text=json.dumps({"jsonrpc": "2.0", "id": sent.get("id"),
                             "result": {"tools": [{"name": "browser_get_status"}]}}),
            headers={"content-type": "application/json"},
        )

    def methods(self) -> list[str]:
        return [str(c["body"].get("method")) for c in self.calls]


# --------------------------------------------------------------------------- #
# MC3: the bridge starts a new session by itself
# --------------------------------------------------------------------------- #
def test_mc3_after_a_restart_the_bridge_starts_a_new_session_and_repeats_the_call_once(door):
    """Pins ``StdioShim._session_lost`` + ``_reinitialise`` + the one repeat."""
    daemon = ScriptedDaemon(_forgotten_answer(door))
    shim = stdio_shim.StdioShim("http://127.0.0.1:1/mcp", "pane-token-for-tests", poster=daemon)
    assert json.loads(shim.handle_line(_line(INIT)))["result"]
    assert shim.handle_line(_line(INITIALIZED)) is None
    assert shim.session_id == "session-1"

    daemon.restart()
    answer = json.loads(shim.handle_line(_line(LIST)))

    assert "error" not in answer, answer
    assert answer["id"] == 2
    assert answer["result"]["tools"][0]["name"] == "browser_get_status"
    assert daemon.methods() == [
        "initialize", "notifications/initialized",
        "tools/list", "initialize", "notifications/initialized", "tools/list",
    ], daemon.methods()
    # The replay is the tool's own handshake, sent with NO session id.
    replay = daemon.calls[3]
    assert replay["body"]["params"] == INIT["params"]
    assert SESSION_HEADER not in replay["headers"]
    assert daemon.calls[4]["headers"][SESSION_HEADER] == "session-2"
    # The repeated call carried the NEW session, and the same request.
    assert daemon.calls[5]["headers"][SESSION_HEADER] == "session-2"
    assert daemon.calls[5]["body"] == LIST
    assert shim.session_id == "session-2"


def test_mc3_a_call_still_refused_after_the_new_session_is_answered_not_looped(door):
    """One re-initialise per failed call: a daemon that forgets again at once
    gets exactly one repeat, and the tool reads the daemon's own answer."""
    daemon = ScriptedDaemon(_forgotten_answer(door))
    shim = stdio_shim.StdioShim("http://127.0.0.1:1/mcp", "pane-token-for-tests", poster=daemon)
    shim.handle_line(_line(INIT))
    shim.handle_line(_line(INITIALIZED))
    daemon.restart()
    real_post = daemon.post

    def forget_every_time(url, body, headers, timeout):
        reply = real_post(url, body, headers, timeout)
        daemon.restart()
        return reply

    daemon.post = forget_every_time  # type: ignore[method-assign]
    answer = json.loads(shim.handle_line(_line(LIST)))
    assert answer["error"]["code"] == jsonrpc.INVALID_REQUEST, answer
    assert daemon.methods().count("initialize") == 2, daemon.methods()
    assert daemon.methods().count("tools/list") == 2, daemon.methods()


def test_mc3_against_the_real_route_a_forgotten_session_gets_tools_again(tmp_path, monkeypatch):
    """The app factory's own ``/mcp`` route and session registry: initialize,
    the registry forgets the session (what a restart or an idle hour does),
    and the bridge's next ``tools/list`` still returns tools."""
    from iron_jarvis.daemon.app import create_app

    monkeypatch.setenv(INSTALL_TOKEN_ENV, INSTALL_TOKEN)
    app = create_app(str(tmp_path))
    platform = app.state.platform
    pane = FakePane(PANE_ID, {"browser": True})
    platform.pane_tokens.pane_lookup = lambda pid: pane if pid == PANE_ID else None
    token = platform.pane_tokens.mint(PANE_ID, {"browser": True})
    poster = RoutePoster(TestClient(app))
    shim = stdio_shim.StdioShim("http://127.0.0.1:1/mcp", token, poster=poster)
    first = json.loads(shim.handle_line(_line(INIT)))
    assert first["result"]["serverInfo"]["name"] == "iron-jarvis", first
    shim.handle_line(_line(INITIALIZED))
    old = shim.session_id
    assert old and platform.mcp_sessions.get(old, pane_id=PANE_ID).initialized

    assert platform.mcp_sessions.revoke_all() >= 1  # the daemon forgot it

    answer = json.loads(shim.handle_line(_line(LIST)))
    assert "error" not in answer, answer
    names = [tool["name"] for tool in answer["result"]["tools"]]
    assert "browser_get_status" in names, names
    assert shim.session_id and shim.session_id != old
    renewed = platform.mcp_sessions.get(shim.session_id, pane_id=PANE_ID)
    assert renewed is not None and renewed.initialized, "the replayed handshake was not completed"
    assert renewed.client_info.get("name") == "pytest-tool"


def test_mc3_a_token_that_no_longer_verifies_is_answered_once_and_never_retried(door):
    """A closed terminal's token: one call, one plain answer, no retry storm."""
    poster = RoutePoster(door.client)
    shim = stdio_shim.StdioShim("http://127.0.0.1:1/mcp", door.token, poster=poster)
    shim.handle_line(_line(INIT))
    shim.handle_line(_line(INITIALIZED))
    door.tokens.revoke_pane(PANE_ID)
    door.sessions.revoke_pane(PANE_ID)
    before = len(poster.calls)
    answer = json.loads(shim.handle_line(_line(LIST)))
    assert len(poster.calls) - before == 1, [c["body"].get("method") for c in poster.calls]
    assert PANE_TOKEN_EXPIRED_MESSAGE in answer["error"]["message"], answer


def test_mc3_a_new_session_refused_by_the_token_check_is_answered_plainly(door):
    """The re-initialise goes through the same token check: refused there,
    the tool reads the refusal and the call is not repeated."""
    forgotten = _forgotten_answer(door)
    door.tokens.revoke_pane(PANE_ID)
    expired = door.client.post(
        "/mcp", json=INIT,
        headers={"Authorization": f"Bearer {door.token}",
                 "Accept": "application/json, text/event-stream"},
    )
    assert expired.status_code == 401, expired.text
    daemon = ScriptedDaemon(forgotten)
    shim = stdio_shim.StdioShim("http://127.0.0.1:1/mcp", "pane-token-for-tests", poster=daemon)
    shim.handle_line(_line(INIT))
    shim.handle_line(_line(INITIALIZED))
    daemon.restart()
    real_post = daemon.post

    def refuse_initialize(url, body, headers, timeout):
        if json.loads(body.decode("utf-8")).get("method") == "initialize":
            daemon.calls.append({"body": json.loads(body.decode("utf-8")), "headers": dict(headers)})
            return _as_reply(expired)
        return real_post(url, body, headers, timeout)

    daemon.post = refuse_initialize  # type: ignore[method-assign]
    answer = json.loads(shim.handle_line(_line(LIST)))
    assert PANE_TOKEN_EXPIRED_MESSAGE in answer["error"]["message"], answer
    assert answer["id"] == 2
    assert daemon.methods()[2:] == ["tools/list", "initialize"], daemon.methods()


class Unreachable:
    """A daemon that is not listening: the connection is refused, nothing sent."""

    def __init__(self, answer_after: int | None = None) -> None:
        self.calls = 0
        self.answer_after = answer_after

    def post(self, url, body, headers, timeout):
        self.calls += 1
        if self.answer_after is not None and self.calls > self.answer_after:
            sent = json.loads(body.decode("utf-8"))
            return stdio_shim.HttpReply(
                status=200,
                text=json.dumps({"jsonrpc": "2.0", "id": sent.get("id"), "result": {"tools": []}}),
                headers={"content-type": "application/json"},
            )
        reply = stdio_shim.HttpReply(status=0, text="the connection was refused", headers={})
        reply.sent = False  # the request certainly never left this PC
        reply.retryable = True
        return reply


def test_mc3_a_daemon_that_is_not_answering_is_retried_briefly_then_named_plainly():
    """Pins the bounded backoff in ``StdioShim._post`` and the not-made sentence."""
    slept: list[float] = []
    poster = Unreachable()
    shim = stdio_shim.StdioShim("http://127.0.0.1:1/mcp", "pane-token-for-tests", poster=poster)
    shim.sleep = slept.append
    answer = json.loads(shim.handle_line(_line(LIST)))
    assert poster.calls == len(stdio_shim.RETRY_DELAYS_S) + 1, poster.calls
    assert slept == list(stdio_shim.RETRY_DELAYS_S)
    assert sum(slept) <= 30, "the wait must stay short"
    message = answer["error"]["message"]
    assert "not answering" in message and "was not made" in message, message
    assert "Error" not in message and "refused" not in message, message


def test_mc3_a_daemon_that_comes_back_while_retrying_answers_the_call():
    slept: list[float] = []
    poster = Unreachable(answer_after=2)
    shim = stdio_shim.StdioShim("http://127.0.0.1:1/mcp", "pane-token-for-tests", poster=poster)
    shim.sleep = slept.append
    answer = json.loads(shim.handle_line(_line(LIST)))
    assert answer["result"] == {"tools": []}, answer
    assert poster.calls == 3 and len(slept) == 2


def test_mc3_no_answer_after_sending_is_never_repeated_and_says_it_is_unknown():
    """A call that may have reached the daemon (a read timed out) is not sent
    again: repeating it could run a tool twice."""
    calls: list[int] = []

    class TimedOut:
        def post(self, url, body, headers, timeout):
            calls.append(1)
            return stdio_shim.HttpReply(status=0, text="timed out", headers={})

    shim = stdio_shim.StdioShim("http://127.0.0.1:1/mcp", "pane-token-for-tests", poster=TimedOut())
    shim.sleep = lambda s: calls.append(0)
    answer = json.loads(shim.handle_line(_line({**LIST, "method": "tools/call",
                                                 "params": {"name": "browser_click"}})))
    assert calls == [1]
    message = answer["error"]["message"]
    assert "not known whether" in message, message
    assert "cannot reach Iron Jarvis" in message  # the v1.238.0 pin's words stay


# --------------------------------------------------------------------------- #
# MC7: never a proxy, never off this PC
# --------------------------------------------------------------------------- #
class _Recorder(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - http.server's name
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        self.server.seen.append({"path": self.path,  # type: ignore[attr-defined]
                                 "auth": self.headers.get("Authorization", ""),
                                 "body": body})
        out = json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args):  # quiet
        pass


@pytest.fixture()
def recorders():
    servers = []
    threads = []
    for _ in range(2):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
        server.seen = []  # type: ignore[attr-defined]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(server)
        threads.append(thread)
    try:
        yield servers
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=5)


def _proxy_env(monkeypatch, proxy_url: str) -> None:
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy",
                 "HTTPS_PROXY", "https_proxy"):
        monkeypatch.setenv(name, proxy_url)
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)


def test_mc7_the_bridge_never_sends_its_call_to_a_proxy(monkeypatch, recorders):
    """Pins ``UrllibPoster``'s ``ProxyHandler({})``."""
    proxy, daemon = recorders
    _proxy_env(monkeypatch, f"http://127.0.0.1:{proxy.server_port}")
    url = f"http://127.0.0.1:{daemon.server_port}/mcp"
    reply = stdio_shim.UrllibPoster().post(
        url, json.dumps(LIST).encode(), {"Authorization": "Bearer pane-token-for-tests",
                                         "Content-Type": "application/json"}, 5.0,
    )
    assert proxy.seen == [], "the pane's token went to the proxy"
    assert reply.status == 200, reply
    assert [s["auth"] for s in daemon.seen] == ["Bearer pane-token-for-tests"]


def test_mc7_a_target_off_this_pc_gets_nothing_not_even_through_a_proxy(monkeypatch, recorders):
    """Pins ``is_loopback_url`` in ``UrllibPoster.post`` and in ``main``."""
    proxy, _ = recorders
    _proxy_env(monkeypatch, f"http://127.0.0.1:{proxy.server_port}")
    reply = stdio_shim.UrllibPoster().post(
        "http://192.0.2.10/mcp", json.dumps(LIST).encode(),
        {"Authorization": "Bearer pane-token-for-tests"}, 5.0,
    )
    assert proxy.seen == [], "the pane's token left this PC through the proxy"
    assert reply.status == 0 and getattr(reply, "sent", None) is False, reply
    assert getattr(reply, "retryable", True) is False
    err = io.StringIO()
    code = stdio_shim.main(["shim", "http://192.0.2.10/mcp"],
                           env={MCP_TOKEN_ENV: "pane-token-for-tests"}, stderr=err)
    assert code == 2 and "this computer" in err.getvalue(), err.getvalue()
    for url in ("http://127.0.0.1:8/mcp", "http://localhost:8/mcp", "http://[::1]:8/mcp"):
        assert stdio_shim.is_loopback_url(url), url
    for url in ("http://192.0.2.10/mcp", "http://127.0.0.1.example.com/mcp", "file:///x", ""):
        assert not stdio_shim.is_loopback_url(url), url


# --------------------------------------------------------------------------- #
# MC8: a crash reaches the coding tool as a plain sentence
# --------------------------------------------------------------------------- #
def test_mc8_a_crash_while_serving_a_call_is_a_plain_sentence(door, monkeypatch, caplog):
    async def explode(*args, **kwargs):
        raise KeyError("path")

    monkeypatch.setattr(mcp_routes, "dispatch", explode)
    response = door.client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 9, "method": "tools/call",
              "params": {"name": "browser_get_status", "arguments": {}}},
        headers={"Authorization": f"Bearer {door.token}",
                 "Accept": "application/json, text/event-stream"},
    )
    assert response.status_code == 500, response.text
    error = response.json()["error"]
    assert error["code"] == jsonrpc.INTERNAL_ERROR
    message = error["message"]
    assert "KeyError" not in message and "'path'" not in message, message
    assert message == mcp_routes.TOOL_CALL_CRASH_MESSAGE, message
    assert response.json()["id"] == 9


# --------------------------------------------------------------------------- #
# MC4: a stop never hangs on a pipe another program holds open
# --------------------------------------------------------------------------- #
GRANDCHILD = """
import subprocess, sys
kw = {"creationflags": 0x08000000} if sys.platform == "win32" else {"start_new_session": True}
child = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(25)"],
    stdin=subprocess.DEVNULL, stdout=sys.stdout.fileno(), stderr=sys.stderr.fileno(), **kw,
)
print(child.pid, flush=True)
"""


def test_mc4_stopping_a_tool_whose_output_is_held_open_does_not_hang(tmp_path, monkeypatch):
    """A tool that exits while a program it started keeps its output open.

    With a Job (Windows, when one can be made) the tree kill ends that
    program too; with none (Linux, Mac, or a Job that could not be made) it
    survives, and closing a pipe its reader is blocked on waits for it.
    Pins the reader-alive guard in ``codehost.Tool.stop``."""
    import iron_jarvis.hosted.process as hosted_process
    from iron_jarvis import codehost
    from iron_jarvis.core import procs

    monkeypatch.setattr(hosted_process, "STOP_JOIN_S", 0.5)
    if sys.platform == "win32":
        # The documented "no Job could be made" path of ``procs.spawn``.
        monkeypatch.setattr(procs._Job, "create", classmethod(lambda cls, **kw: None))
    script = tmp_path / "leaves_a_holder.py"
    script.write_text(GRANDCHILD, encoding="utf-8")
    host = SimpleNamespace(log=lambda *a, **k: None, wake=lambda: None)
    tool = codehost.Tool(host, "g22mc4tool000000", "sid", [sys.executable, str(script)],
                         dict(os.environ), str(tmp_path), False)
    holder = 0
    stopper = threading.Thread(target=tool.stop, kwargs={"graceful": False}, daemon=True)
    try:
        tool.start()
        tool.proc.wait(timeout=20)
        seen = threading.Event()
        for _ in range(400):  # waits for the very line asserted, bounded
            with tool.lock:
                if tool.lines:
                    holder = int(tool.lines[0][1].decode().strip())
                    break
            seen.wait(0.05)
        assert holder, "the stand-in never said which program holds its output"
        stopper.start()
        stopper.join(timeout=8)
        assert not stopper.is_alive(), "stopping the tool hung on a pipe held open"
    finally:
        if holder:
            try:
                os.kill(holder, signal.SIGTERM)
            except OSError:
                pass
        if tool.proc is not None:
            procs.kill_tree(tool.proc)
            procs.release(tool.proc)
        if stopper.is_alive():
            stopper.join(timeout=10)
