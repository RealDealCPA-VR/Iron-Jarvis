"""v1.300.0 review — how claude-cli FAILS, in words a user can act on.

The rebuilt adapter sends exactly ONE upstream request per call through the
admission relay, with the CLI's own retries off (``CLAUDE_CODE_MAX_RETRIES=0``
and the relay refuses every later attempt). Three review findings followed:

1. A connection that dropped before any answer (reset, DNS, TLS, timeout, a
   200 cut off before a word) was PERMANENT and worded in engineer-speak
   ("incomplete upstream response (first upstream attempt: status None ...")
   — one dropped connection failed the turn. It is now TRANSIENT (the router
   retries / fails over like any cloud provider) with a plain sentence; the
   detail goes to an INFO log line. A drop AFTER text streamed is unchanged.
2. An upstream 401 (or an auth-shaped 403) never reached the sign-in remedy.
3. The relay ignored HTTPS_PROXY (the Node CLI honoured it).

Everything is offline: loopback fake upstreams, a loopback CONNECT proxy, the
fake ``claude`` (``tests/fixtures/fake_claude_v1300.py``) through a shim, and
a resolver guard that refuses every non-loopback name, so even a mutated
build cannot reach the network.
"""

from __future__ import annotations

import base64
import http.client
import json
import logging
import os
import socket
import ssl
import stat
import struct
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from iron_jarvis.providers import cli_auth
from iron_jarvis.providers.adapters import subprocess_cli as sc
from iron_jarvis.providers.adapters.base import ProviderError
from iron_jarvis.providers.adapters.claude_native import admission as adm_mod
from iron_jarvis.providers.adapters.claude_native import frames as F
from iron_jarvis.providers.adapters.claude_native import transport
from iron_jarvis.providers.adapters.claude_native.admission import Admission, proxy_for
from iron_jarvis.providers.adapters.base import LLMMessage
from iron_jarvis.providers.cli_auth import SIGN_IN_FIX, CliAuthProbe
from iron_jarvis.providers.router import is_transient_error

FAKE = Path(__file__).parent / "fixtures" / "fake_claude_v1300.py"
_RealHTTPConnection = http.client.HTTPConnection
_PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")
#: What the user must never read: the relay's internals.
ENGINEER_SPEAK = ("status None", "capture incomplete", "relay failure", "retries denied",
                  "gaierror", "RemoteDisconnected", "first upstream attempt")


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for key in [k for k in os.environ if k.startswith("ANTHROPIC_")] + list(transport.BACKEND_SWITCHES):
        monkeypatch.delenv(key, raising=False)
    for key in _PROXY_KEYS:
        monkeypatch.delenv(key, raising=False)
        monkeypatch.delenv(key.lower(), raising=False)
    monkeypatch.setattr(transport, "_STRIP_LOGGED", False)
    probe = CliAuthProbe(which=lambda b: None)
    monkeypatch.setattr(cli_auth, "DEFAULT_PROBE", probe)
    monkeypatch.setattr(transport, "DEFAULT_PROBE", probe)
    # The network guard: any name but loopback fails to resolve, in-process.
    real = socket.getaddrinfo

    def loopback_only(host, *args, **kw):
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise socket.gaierror(11001, "test guard: no real network")
        return real(host, *args, **kw)

    monkeypatch.setattr(socket, "getaddrinfo", loopback_only)
    return probe


def _shim(tmp_path: Path) -> str:
    if os.name == "nt":
        shim = tmp_path / "claude.cmd"
        shim.write_text(f'@"{sys.executable}" "{FAKE}" %*\r\n', encoding="utf-8")
    else:
        shim = tmp_path / "claude"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n', encoding="utf-8")
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    return str(shim)


class Fake:
    def __init__(self, tmp_path: Path, monkeypatch, scenario: str = "relay_error", **knobs: str) -> None:
        self.shim = _shim(tmp_path)
        self.record_path = tmp_path / "record.json"
        monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", scenario)
        monkeypatch.setenv("FAKE_CLAUDE_RECORD", str(self.record_path))
        for key, value in knobs.items():
            monkeypatch.setenv(f"FAKE_CLAUDE_{key.upper()}", value)

    def adapter(self, upstream: str) -> sc.ClaudeCliAdapter:
        env = dict(os.environ, ANTHROPIC_BASE_URL=upstream)
        return sc.make_claude_cli(which=lambda _b: self.shim, model="claude-sonnet-5", env=env)

    @property
    def record(self) -> dict:
        return json.loads(self.record_path.read_text(encoding="utf-8"))


def _user(text: str = "hi") -> LLMMessage:
    return LLMMessage(role="user", content=text)


def sse(events: list[dict]) -> bytes:
    return "".join("event: " + e["type"] + "\ndata: " + json.dumps(e) + "\n\n" for e in events).encode()


PARTIAL = [  # a 200 that dies after the first word
    {"type": "message_start", "message": {"id": "m", "type": "message", "role": "assistant", "model": "x",
                                          "content": [], "stop_reason": None,
                                          "usage": {"input_tokens": 1, "output_tokens": 0}}},
    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hel"}},
]


class Upstream:
    """A misbehaving api.anthropic.com on loopback."""

    def __init__(self, mode: str, status: int = 200, body: bytes = b"", retry_after: str | None = None):
        self.hits = 0
        owner = self

        class Peer(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                owner.hits += 1
                if mode == "close":  # hang up without a word: RemoteDisconnected
                    self.close_connection = True
                    return
                if mode == "rst":  # a hard reset: ConnectionResetError
                    linger = struct.pack("HH" if os.name == "nt" else "ii", 1, 0)
                    self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, linger)
                    self.connection.close()
                    self.close_connection = True
                    return
                if mode == "status":
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    if retry_after is not None:
                        self.send_header("retry-after", retry_after)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                # truncated: close-delimited 200 that stops mid-answer
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(sse(PARTIAL))

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Peer)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def upstreams():
    made: list[Upstream] = []

    def make(*a, **kw) -> Upstream:
        up = Upstream(*a, **kw)
        made.append(up)
        return up

    yield make
    for up in made:
        up.stop()


class _Grab(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(f"{record.levelname} {record.getMessage()}")


@pytest.fixture
def grab():
    logger = logging.getLogger("ironjarvis.claude_native")
    handler, old = _Grab(), logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    yield handler
    logger.removeHandler(handler)
    logger.setLevel(old)


async def _fail(adapter, streaming: bool = False) -> tuple[BaseException, list[str]]:
    texts: list[str] = []
    with pytest.raises(RuntimeError) as info:
        if streaming:
            async for frame in adapter.stream(system="", messages=[_user()], tools=[]):
                if frame.get("type") == "text":
                    texts.append(frame["text"])
        else:
            await adapter.complete(system="", messages=[_user()], tools=[])
    return info.value, texts


def _plain(exc: BaseException) -> None:
    for words in ENGINEER_SPEAK:
        assert words not in str(exc), str(exc)


# --------------------------------------------------------------------------- #
# 1. a dropped connection before any answer is TRANSIENT, in plain words
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mode", ["close", "rst", "truncated"])
async def test_a_dropped_connection_before_any_answer_is_transient_and_plain(
        tmp_path, monkeypatch, upstreams, grab, mode):
    up = upstreams(mode)
    fake = Fake(tmp_path, monkeypatch)
    for streaming in (False, True):
        exc, texts = await _fail(fake.adapter(up.url), streaming)
        assert texts == []
        assert isinstance(exc, ProviderError) and exc.transient is True and exc.status_code is None
        assert str(exc) == transport.DROPPED
        assert is_transient_error(exc), "the router must retry / fail over, as for any cloud drop"
        _plain(exc)
    assert up.hits == 2  # once per call: native's retry never reached the upstream
    statuses = fake.record["relay_statuses"]
    assert statuses[1] == 400  # the relay refused native's retry locally
    assert (statuses[0] == 200) if mode == "truncated" else str(statuses[0]).startswith("dropped:")
    # The technical detail is logged at INFO — never in the sentence.
    info = [line for line in grab.lines if line.startswith("INFO") and "first upstream attempt" in line]
    assert info and "native retries denied: 1" in info[-1]
    assert ("capture incomplete" in info[-1]) and (mode == "truncated" or "relay failure" in info[-1])


async def test_a_drop_after_text_streamed_keeps_the_committed_behaviour(tmp_path, monkeypatch, upstreams):
    """Part of the answer reached the user: the router's committed path owns
    it, so this stays permanent and keeps its v1.300.0 wording."""
    up = upstreams("truncated")
    fake = Fake(tmp_path, monkeypatch, streamed="Hel")
    exc, texts = await _fail(fake.adapter(up.url), streaming=True)
    assert texts == ["Hel"]
    # Not flagged transient (the router's phrase fallback may still read native's
    # "Connection error" — unchanged; once a frame was yielded the route is
    # committed and no retry can happen anyway).
    assert isinstance(exc, ProviderError) and exc.transient is False and exc.status_code is None
    assert str(exc).startswith("claude-cli: incomplete upstream response (first upstream attempt: status 200")


class _Raising:
    """Stands in for http.client's connection INSIDE the relay: the request
    is sent, then the response raises ``exc`` (a real relay, a real post)."""

    exc: BaseException = OSError()
    at = "getresponse"

    def __init__(self, host, port, timeout=None, context=None):
        self.sock = None

    def connect(self):
        if self.at == "connect":
            raise self.exc

    def request(self, *args, **kw):
        if self.at == "request":
            raise self.exc

    def getresponse(self):
        raise self.exc

    def close(self):
        pass


def _post(url: str) -> None:
    parts = urlsplit(url)
    conn = _RealHTTPConnection(parts.hostname, parts.port, timeout=10)
    try:
        conn.request("POST", parts.path + "/v1/messages", b'{"messages":[]}', {"Content-Type": "application/json"})
        conn.getresponse().read()
    except (OSError, http.client.HTTPException):
        pass  # the relay hung up on us: that is the case under test
    finally:
        conn.close()


def _relay_failing_with(monkeypatch, exc: BaseException, at: str = "getresponse") -> Admission:
    monkeypatch.setattr(_Raising, "exc", exc)
    monkeypatch.setattr(_Raising, "at", at)
    monkeypatch.setattr(adm_mod.http.client, "HTTPConnection", _Raising)
    adm = Admission("http://127.0.0.1:9", 5.0)
    try:
        _post(adm.url)
    finally:
        adm.close()
    return adm


def _assemble(adm: Admission, **got) -> BaseException:
    got.setdefault("native_error", "API Error: Connection error.")
    got.setdefault("native_error_code", "unknown")
    with pytest.raises(RuntimeError) as info:
        transport.assemble(transport.Collected(**got), returncode=1, admission=adm, names=F.ToolNames.build([]))
    return info.value


@pytest.mark.parametrize("exc", [
    ConnectionResetError(10054, "reset"),
    ConnectionRefusedError(10061, "refused"),
    http.client.RemoteDisconnected("Remote end closed connection without response"),
    http.client.IncompleteRead(b"", 10),
    ssl.SSLError(1, "record layer failure"),
    ssl.SSLCertVerificationError(1, "certificate verify failed"),
    socket.gaierror(11001, "getaddrinfo failed"),
    TimeoutError("timed out"),
], ids=lambda e: type(e).__name__)
def test_every_network_failure_without_a_status_is_transient(monkeypatch, exc):
    adm = _relay_failing_with(monkeypatch, exc)
    assert adm.used and adm.status is None and adm.failure == type(exc).__name__
    assert adm.failure_network is True
    err = _assemble(adm)
    assert isinstance(err, ProviderError) and err.transient and str(err) == transport.DROPPED
    assert is_transient_error(err)


def test_a_local_relay_bug_is_not_dressed_up_as_a_dropped_connection(monkeypatch):
    """A bad header value is OUR bug, not the network: retrying would repeat it."""
    adm = _relay_failing_with(monkeypatch, ValueError("Invalid header value"), at="request")
    assert adm.failure == "ValueError" and adm.failure_network is False
    err = _assemble(adm)
    assert isinstance(err, ProviderError) and not err.transient
    assert "internal relay error, not your connection" in str(err)
    _plain(err)


# --------------------------------------------------------------------------- #
# 1b. Anthropic answered with a status: kept, worded plainly, with its words
# --------------------------------------------------------------------------- #
def _err_body(kind: str, message: str) -> bytes:
    return json.dumps({"type": "error", "error": {"type": kind, "message": message}}).encode()


@pytest.mark.parametrize("status,kind,message,lead,transient", [
    (529, "overloaded_error", "Overloaded", "Anthropic is busy or briefly unavailable (HTTP 529)", True),
    (500, "api_error", "Internal server error", "Anthropic is busy or briefly unavailable (HTTP 500)", True),
    (429, "rate_limit_error", "Number of requests has exceeded your rate limit",
     "Anthropic is rate-limiting this account right now", True),
    (400, "invalid_request_error", "prompt is too long: 213000 tokens > 200000 maximum",
     "Anthropic refused the request (HTTP 400)", False),
    (403, "permission_error", "Your organization does not have access to this model",
     "Anthropic refused the request (HTTP 403)", False),
])
async def test_an_upstream_status_keeps_its_status_and_says_why_plainly(
        tmp_path, monkeypatch, upstreams, status, kind, message, lead, transient):
    up = upstreams("status", status=status, body=_err_body(kind, message), retry_after="7")
    fake = Fake(tmp_path, monkeypatch, native_text=f"API Error: {status} {message}")
    exc, _ = await _fail(fake.adapter(up.url))
    assert isinstance(exc, ProviderError) and exc.status_code == status
    assert exc.transient is transient and is_transient_error(exc) is transient
    assert str(exc).startswith(f"claude-cli: {lead}") and str(exc).endswith(f"Anthropic said: {message}")
    assert exc.retry_after == 7.0
    assert SIGN_IN_FIX["claude"] not in str(exc)
    _plain(exc)
    assert transport.DEFAULT_PROBE.status("claude") is None  # not a sign-in problem


# --------------------------------------------------------------------------- #
# 2. a 401 (or an auth-shaped 403) is the sign-in remedy, and the probe is told
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("status,kind,message,native_code", [
    (401, "authentication_error", "OAuth token has expired. Please obtain a new token.", "authentication_failed"),
    (401, "authentication_error", "invalid x-api-key", "unknown"),  # the status alone decides
    (403, "authentication_error", "OAuth authentication is currently not supported.", "unknown"),
    (403, "permission_error", "OAuth token has been revoked. Please obtain a new token.", "unknown"),
])
async def test_an_upstream_auth_refusal_is_the_sign_in_remedy(
        tmp_path, monkeypatch, upstreams, status, kind, message, native_code):
    up = upstreams("status", status=status, body=_err_body(kind, message))
    native = "Failed to authenticate · Please run /login" if native_code == "authentication_failed" else "API Error"
    fake = Fake(tmp_path, monkeypatch, native_error=native_code, native_text=native)
    for streaming in (False, True):
        exc, _ = await _fail(fake.adapter(up.url), streaming)
        assert SIGN_IN_FIX["claude"] in str(exc), str(exc)
        assert str(exc).startswith("claude-cli: ")
        assert isinstance(exc, ProviderError) and exc.status_code == status
        assert not exc.transient and not is_transient_error(exc)  # signing in again is the fix
        _plain(exc)
    verdict = transport.DEFAULT_PROBE.status("claude")
    assert verdict is not None and verdict.signed_in is False, "Connections must turn amber at once"


# --------------------------------------------------------------------------- #
# 3. the upstream leg honours the standard proxy environment
# --------------------------------------------------------------------------- #
class ConnectProxy:
    """A loopback CONNECT proxy that RECORDS the tunnel request and refuses it
    (407): nothing ever leaves this machine."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                conn.settimeout(5)
                data = b""
                try:
                    while b"\r\n\r\n" not in data:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        data += chunk
                except OSError:
                    continue
                head = data.split(b"\r\n\r\n", 1)[0].decode("latin-1").split("\r\n")
                headers = dict(line.split(": ", 1) for line in head[1:] if ": " in line)
                self.requests.append({"line": head[0], "headers": headers})
                try:
                    conn.sendall(b"HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\n\r\n")
                except OSError:
                    pass

    def stop(self) -> None:
        self.sock.close()


@pytest.fixture
def connect_proxy():
    proxy = ConnectProxy()
    yield proxy
    proxy.stop()


async def test_the_relay_tunnels_through_https_proxy_and_the_cli_skips_it_for_loopback(
        tmp_path, monkeypatch, connect_proxy):
    """HTTPS_PROXY (with credentials) carries the upstream leg as a CONNECT to
    api.anthropic.com:443; HTTP_PROXY points at a DEAD port, so the CLI's own
    plain-HTTP request to the loopback relay only arrives because 127.0.0.1 was
    added to its NO_PROXY. The proxy refuses: a dropped connection, transient."""
    dead = socket.socket()
    dead.bind(("127.0.0.1", 0))
    dead_port = dead.getsockname()[1]
    dead.close()
    monkeypatch.setenv("HTTPS_PROXY", f"http://ij%40user:p%3Ass@127.0.0.1:{connect_proxy.port}")
    monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{dead_port}")
    monkeypatch.setenv("NO_PROXY", "intranet.example")
    fake = Fake(tmp_path, monkeypatch)
    exc, _ = await _fail(fake.adapter("https://api.anthropic.com"))
    assert [r["line"] for r in connect_proxy.requests] == ["CONNECT api.anthropic.com:443 HTTP/1.1"]
    auth = connect_proxy.requests[0]["headers"].get("Proxy-Authorization", "")
    assert auth == "Basic " + base64.b64encode(b"ij@user:p:ss").decode("ascii")
    rec = fake.record
    assert rec["relay_statuses"][1] == 400, "the CLI reached the loopback relay (not the dead proxy)"
    no_proxy = rec["env"].get("NO_PROXY") or rec["env"].get("no_proxy") or ""
    assert no_proxy.split(",")[:3] == ["intranet.example", "127.0.0.1", "localhost"]
    assert isinstance(exc, ProviderError) and exc.transient and str(exc) == transport.DROPPED


def _target(url: str):
    return urlsplit(url)


def test_proxy_for_reads_only_the_environment(monkeypatch):
    target = _target("https://api.anthropic.com")
    assert proxy_for(target) is None  # nothing set (the registry is never read)
    monkeypatch.setenv("HTTPS_PROXY", "proxy.corp:3128")
    assert proxy_for(target) == ("proxy.corp", 3128, {})
    assert proxy_for(_target("http://127.0.0.1:9")) is None  # a loopback fixture is never proxied
    monkeypatch.setenv("NO_PROXY", "localhost,.anthropic.com")
    assert proxy_for(target) is None
    monkeypatch.delenv("NO_PROXY")
    monkeypatch.delenv("HTTPS_PROXY")
    monkeypatch.setenv("ALL_PROXY", "http://u:pw@10.0.0.1")
    host, port, headers = proxy_for(target)
    assert (host, port) == ("10.0.0.1", 80)
    assert headers == {"Proxy-Authorization": "Basic " + base64.b64encode(b"u:pw").decode()}
    monkeypatch.setenv("ALL_PROXY", "http://[::1")  # malformed: ignored, never a crash
    assert proxy_for(target) is None


def test_the_tunnel_keeps_tls_verification_on_the_upstream_name(monkeypatch):
    seen: dict = {}

    class FakeHTTPS:
        def __init__(self, host, port, timeout=None, context=None):
            seen.update(host=host, port=port, context=context)

        def set_tunnel(self, host, port=None, headers=None):
            seen.update(tunnel=(host, port), headers=headers)

        def connect(self):
            raise OSError("offline test")

        def close(self):
            pass

    monkeypatch.setattr(adm_mod.http.client, "HTTPSConnection", FakeHTTPS)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:3128")
    adm = Admission("https://api.anthropic.com", 2.0)
    try:
        _post(adm.url)
    finally:
        adm.close()
    assert (seen["host"], seen["port"]) == ("127.0.0.1", 3128)
    assert seen["tunnel"] == ("api.anthropic.com", 443) and seen["headers"] is None
    assert seen["context"].verify_mode == ssl.CERT_REQUIRED and seen["context"].check_hostname
    assert adm.failure == "OSError" and adm.failure_network is True


def test_loopback_unproxied_only_touches_a_proxied_env():
    assert transport.loopback_unproxied({"PATH": "x"}) == {"PATH": "x"}
    env = transport.loopback_unproxied({"https_proxy": "http://p:1", "no_proxy": "a,127.0.0.1"})
    assert env["no_proxy"] == "a,127.0.0.1,localhost" and "NO_PROXY" not in env
    env = transport.loopback_unproxied({"HTTP_PROXY": "http://p:1"})
    assert env["NO_PROXY"] == "127.0.0.1,localhost"


def test_stub_admission_shapes_match_the_real_relay():
    """The duck-typed fields upstream_failure reads exist on a real relay."""
    adm = Admission("http://127.0.0.1:9", 1.0)
    try:
        for name in ("status", "failure", "failure_network", "retry_after", "capture", "denied"):
            assert hasattr(adm, name)
        assert adm.error_type() == "" and adm.error_text() == ""
        adm.error_body = _err_body("authentication_error", "nope")
        assert adm.error_type() == "authentication_error" and adm.error_text() == "nope"
    finally:
        adm.close()


# --------------------------------------------------------------------------- #
# Second review pass (measured against the REAL CLI 2.1.288 on a loopback fake
# upstream): the CLI stamps `authentication_failed` on EVERY upstream 403, and
# after a refused sign-in retry its text is the relay's refusal marker.
# --------------------------------------------------------------------------- #
async def test_a_model_access_403_is_not_signed_out_even_when_the_cli_says_authentication_failed(
        tmp_path, monkeypatch, upstreams):
    message = "Your organization does not have access to this model. Please choose a different model."
    up = upstreams("status", status=403, body=_err_body("permission_error", message))
    fake = Fake(tmp_path, monkeypatch, native_error="authentication_failed",
                native_text=f"Failed to authenticate. API Error: 403 {message}")
    for streaming in (False, True):
        exc, _ = await _fail(fake.adapter(up.url), streaming)
        assert SIGN_IN_FIX["claude"] not in str(exc), str(exc)
        assert str(exc).startswith("claude-cli: Anthropic refused the request (HTTP 403)")
        assert str(exc).endswith(f"Anthropic said: {message}")
    assert transport.DEFAULT_PROBE.status("claude") is None, "a signed-in user must stay signed in"


async def test_the_sign_in_remedy_quotes_anthropic_never_the_relays_marker(tmp_path, monkeypatch, upstreams):
    from iron_jarvis.providers.adapters.claude_native.admission import CONSUMED_MESSAGE

    up = upstreams("status", status=401, body=_err_body("authentication_error", "Invalid bearer token"))
    fake = Fake(tmp_path, monkeypatch, native_text=f"API Error: 400 {CONSUMED_MESSAGE}")
    exc, _ = await _fail(fake.adapter(up.url))
    assert SIGN_IN_FIX["claude"] in str(exc)
    assert CONSUMED_MESSAGE not in str(exc), str(exc)
    assert "Invalid bearer token" in str(exc)


async def test_a_403_typed_authentication_error_is_sign_in_whatever_its_words(tmp_path, monkeypatch, upstreams):
    # Words that match no sign-in phrase: only Anthropic's error TYPE can decide.
    up = upstreams("status", status=403, body=_err_body("authentication_error", "Access denied."))
    fake = Fake(tmp_path, monkeypatch, native_text="API Error")
    exc, _ = await _fail(fake.adapter(up.url))
    assert SIGN_IN_FIX["claude"] in str(exc), str(exc)


async def test_the_sign_in_remedy_prefers_anthropics_words_to_the_clis(tmp_path, monkeypatch, upstreams):
    up = upstreams("status", status=401, body=_err_body("authentication_error", "Invalid bearer token"))
    fake = Fake(tmp_path, monkeypatch, native_error="authentication_failed",
                native_text="Failed to authenticate. API Error: 401 something the CLI rephrased")
    exc, _ = await _fail(fake.adapter(up.url))
    assert "Invalid bearer token" in str(exc) and "rephrased" not in str(exc), str(exc)


async def test_with_no_words_from_anthropic_the_marker_is_still_never_shown(tmp_path, monkeypatch, upstreams):
    from iron_jarvis.providers.adapters.claude_native.admission import CONSUMED_MESSAGE

    up = upstreams("status", status=401, body=b"")
    fake = Fake(tmp_path, monkeypatch, native_text=f"API Error: 400 {CONSUMED_MESSAGE}")
    exc, _ = await _fail(fake.adapter(up.url))
    assert SIGN_IN_FIX["claude"] in str(exc)
    assert CONSUMED_MESSAGE not in str(exc) and "HTTP 401" in str(exc), str(exc)
