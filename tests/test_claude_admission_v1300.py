"""v1.300.0 — the Claude-subscription LOCAL ADMISSION RELAY (claude_native/admission.py).

Ported in substance from NousResearch/hermes-plugin-claude-subscription-directsdk (MIT,
commit ef73726): tests/test_directsdk_admission.py and tests/test_admission_breakpoint.py.
Everything runs offline against a fake upstream on 127.0.0.1 that serves a canned SSE
Messages stream; the relay is pointed at it as a loopback HTTP fixture.
"""

from __future__ import annotations

import http.client
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

from iron_jarvis.providers.adapters.claude_native.admission import (
    CONSUMED_MESSAGE,
    Admission,
    Capture,
    pin_message_breakpoint,
)

SECRET_BEARER = "Bearer sk-ant-oat01-SECRET-DO-NOT-LOG-7f3a"
SECRET_KEY = "sk-ant-api03-ALSO-SECRET-9c1e"

USAGE_START = {
    "input_tokens": 12,
    "output_tokens": 0,
    "cache_read_input_tokens": 0,
    "cache_creation_input_tokens": 0,
}


def sse(events: list[dict]) -> bytes:
    return "".join("event: " + e["type"] + "\ndata: " + json.dumps(e) + "\n\n" for e in events).encode()


def full_events(stop: str = "tool_use") -> list[dict]:
    """A complete stream: signed thinking, text, a tool_use whose args stream in pieces."""
    return [
        {"type": "message_start", "message": {"id": "msg_1", "type": "message", "role": "assistant",
                                              "model": "claude-sonnet-5", "content": [], "stop_reason": None,
                                              "usage": dict(USAGE_START)}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "Let me "}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "think."}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig-abc=="}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Hello "}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "world"}},
        {"type": "content_block_stop", "index": 1},
        {"type": "content_block_start", "index": 2,
         "content_block": {"type": "tool_use", "id": "toolu_1", "name": "read_file", "input": {}}},
        {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": ""}},
        {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '{"path": "a.'}},
        {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": 'txt", "n": 2}'}},
        {"type": "content_block_stop", "index": 2},
        {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
         "usage": {"output_tokens": 7, "cache_read_input_tokens": 0}},
        {"type": "message_stop"},
    ]


# --------------------------------------------------------------------------- fixtures


class Upstream:
    """A fake api.anthropic.com on loopback. Records every hit; answers per ``mode``."""

    def __init__(self) -> None:
        self.hits: list[dict] = []
        self.mode = "stream"  # stream | chunked | error | truncated | hang
        self.events = full_events()
        self.error_status = 529
        self.error_body = b'{"type":"error","error":{"type":"overloaded_error","message":"prompt is too long: 213000 tokens > 200000 maximum"}}'
        self.entered = threading.Event()
        self.disconnected = threading.Event()
        owner = self

        class Peer(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                owner.hits.append({"path": self.path, "headers": dict(self.headers.items()), "body": body})
                if owner.mode == "hang":
                    owner.entered.set()
                    self.rfile.read(1)  # returns b"" once the relay shuts this socket
                    owner.disconnected.set()
                    return
                if owner.mode == "error":
                    self.send_response(owner.error_status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(owner.error_body)))
                    self.end_headers()
                    self.wfile.write(owner.error_body)
                    return
                data = sse(owner.events)
                if owner.mode == "truncated":
                    data = sse(owner.events[:8])
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("request-id", "req_upstream_123")
                if owner.mode == "chunked":
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    for k in range(0, len(data), 97):  # odd size: frames straddle chunks
                        piece = data[k:k + 97]
                        self.wfile.write(b"%x\r\n%s\r\n" % (len(piece), piece))
                        self.wfile.flush()
                    self.wfile.write(b"0\r\n\r\n")
                else:
                    self.end_headers()
                    self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Peer)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def use_http11(self) -> None:
        self.server.RequestHandlerClass.protocol_version = "HTTP/1.1"

    def stop(self) -> None:
        self.server.shutdown()
        self.thread.join(5)
        self.server.server_close()


@pytest.fixture
def upstream():
    up = Upstream()
    try:
        yield up
    finally:
        up.stop()


@pytest.fixture
def relays():
    made: list[Admission] = []

    def make(url: str, timeout: float = 10.0, queried=None) -> Admission:
        adm = Admission(url, timeout, queried=queried)
        made.append(adm)
        return adm

    try:
        yield make
    finally:
        for adm in made:
            adm.close()


def post(url: str, body: bytes = b'{"model":"m","messages":[]}', headers: dict | None = None,
         path_suffix: str = "/v1/messages", timeout: float = 10.0):
    parts = urlsplit(url)
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=timeout)
    try:
        hdrs = {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}
        hdrs.update(headers or {})
        conn.request("POST", parts.path + path_suffix, body, hdrs)
        resp = conn.getresponse()
        return resp.status, dict(resp.getheaders()), resp.read()
    finally:
        conn.close()


# --------------------------------------------------------------------------- first request


@pytest.mark.parametrize("framing", ["close-delimited", "chunked-http11"])
def test_first_request_is_forwarded_streamed_back_and_captured(upstream, relays, framing):
    if framing == "chunked-http11":
        upstream.mode = "chunked"
        upstream.use_http11()
    adm = relays(upstream.url)
    assert adm.url.startswith("http://127.0.0.1:") and "/admit/" in adm.url

    status, headers, body = post(adm.url, headers={"Authorization": SECRET_BEARER, "x-api-key": SECRET_KEY})

    assert status == 200
    assert body == sse(upstream.events)  # the stream reaches native byte-for-byte
    assert {k.lower(): v for k, v in headers.items()}.get("connection") == "close"
    assert "transfer-encoding" not in {k.lower() for k in headers}
    assert len(upstream.hits) == 1
    hit = upstream.hits[0]
    assert hit["path"] == "/v1/messages"  # the random route never leaks upstream
    sent = {k.lower(): v for k, v in hit["headers"].items()}
    assert sent["authorization"] == SECRET_BEARER and sent["x-api-key"] == SECRET_KEY  # passed through
    assert sent["accept-encoding"] == "identity"
    assert adm.used is True and adm.status == 200 and adm.denied == 0 and adm.failure is None
    assert adm.request_id == "req_upstream_123"

    cap = adm.capture
    assert cap.complete is True
    msg = cap.message
    assert msg["id"] == "msg_1" and msg["role"] == "assistant"
    assert msg["stop_reason"] == "tool_use"
    assert msg["content"][0] == {"type": "thinking", "thinking": "Let me think.", "signature": "sig-abc=="}
    assert msg["content"][1] == {"type": "text", "text": "Hello world"}
    assert msg["content"][2] == {"type": "tool_use", "id": "toolu_1", "name": "read_file",
                                 "input": {"path": "a.txt", "n": 2}}
    assert msg["usage"] == {"input_tokens": 12, "output_tokens": 7,
                            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}


@pytest.mark.parametrize("stop", ["end_turn", "max_tokens", "model_context_window_exceeded"])
def test_every_stop_reason_completes_the_capture(upstream, relays, stop):
    upstream.events = [e for e in full_events(stop) if e.get("index") != 2]
    adm = relays(upstream.url)
    assert post(adm.url)[0] == 200
    assert adm.capture.complete is True
    assert adm.capture.message["stop_reason"] == stop


def test_empty_tool_input_completes_the_capture(upstream, relays):
    """A no-argument tool call streams one empty input_json_delta; the capture still completes."""
    upstream.events = [
        {"type": "message_start", "message": {"id": "m", "role": "assistant", "content": [], "usage": dict(USAGE_START)}},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "tool_use", "id": "toolu_1", "name": "list_things", "input": {}}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": ""}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": dict(USAGE_START)},
        {"type": "message_stop"},
    ]
    adm = relays(upstream.url)
    post(adm.url)
    assert adm.capture.complete is True
    assert adm.capture.message["content"][0]["input"] == {}


# --------------------------------------------------------------------------- admission


def test_second_request_is_refused_locally_and_never_reaches_upstream(upstream, relays):
    adm = relays(upstream.url)
    assert post(adm.url)[0] == 200

    status, headers, body = post(adm.url, headers={"Authorization": SECRET_BEARER})
    assert status == 400
    assert json.loads(body) == {"type": "error", "error": {"type": "invalid_request_error",
                                                           "message": CONSUMED_MESSAGE}}
    assert adm.denied == 1
    assert len(upstream.hits) == 1  # the retry cost the subscription nothing

    post(adm.url)
    assert adm.denied == 2 and len(upstream.hits) == 1
    assert adm.status == 200 and adm.capture.complete is True  # the first answer stands


def test_abort_before_any_request_refuses_the_first_too(upstream, relays):
    adm = relays(upstream.url)
    adm.abort()
    assert post(adm.url)[0] == 400
    assert adm.denied == 1 and adm.used is False and upstream.hits == []


@pytest.mark.parametrize("suffix,headers", [
    ("/v1/messages/../x", None),
    ("/v1/complete", None),
    ("/v1/messages", {"Origin": "http://evil.example"}),
])
def test_wrong_route_or_browser_origin_is_refused(upstream, relays, suffix, headers):
    adm = relays(upstream.url)
    assert post(adm.url, path_suffix=suffix, headers=headers)[0] == 404
    assert adm.used is False and upstream.hits == []


def test_wrong_route_token_is_refused(upstream, relays):
    adm = relays(upstream.url)
    parts = urlsplit(adm.url)
    forged = f"http://127.0.0.1:{parts.port}/admit/not-the-token"
    assert post(forged)[0] == 404
    assert adm.used is False and adm.denied == 0 and upstream.hits == []
    assert post(adm.url)[0] == 200  # the real route still admits its one request
    assert len(upstream.hits) == 1


def test_route_token_is_random_per_relay(upstream, relays):
    a, b = relays(upstream.url), relays(upstream.url)
    assert a.prefix != b.prefix and len(a.prefix) > len("/admit/") + 40


def test_relay_binds_loopback_only(upstream, relays):
    adm = relays(upstream.url)
    assert adm.server.server_address[0] == "127.0.0.1"


@pytest.mark.parametrize("url", [
    "http://api.anthropic.com",  # plain HTTP off-box
    "ftp://127.0.0.1:9",
    "https://user:pw@api.anthropic.com",
    "https://api.anthropic.com/?x=1",
    "https://api.anthropic.com/#frag",
    "https:///nohost",
])
def test_upstream_must_be_https_or_a_loopback_fixture(url):
    with pytest.raises(ValueError):
        Admission(url, 5.0)


def test_https_upstream_uses_a_verifying_tls_context(monkeypatch, relays):
    """No network: the relay must build a default (certificate-verifying) SSL context."""
    import ssl as _ssl

    import iron_jarvis.providers.adapters.claude_native.admission as mod

    for key in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(key, raising=False)  # direct (the proxy leg: test_claude_native_errors_v1300)
    seen = {}

    class FakeHTTPS:
        def __init__(self, host, port, timeout=None, context=None):
            seen.update(host=host, port=port, context=context)

        def connect(self):
            raise OSError("offline test")

        def close(self):
            pass

    monkeypatch.setattr(mod.http.client, "HTTPSConnection", FakeHTTPS)
    adm = relays("https://api.anthropic.com", timeout=2.0)
    with pytest.raises((http.client.HTTPException, OSError)):
        post(adm.url)
    assert seen["host"] == "api.anthropic.com"
    ctx = seen["context"]
    assert ctx.verify_mode == _ssl.CERT_REQUIRED and ctx.check_hostname is True
    assert adm.failure == "OSError" and adm.used is True


# --------------------------------------------------------------------------- failures


def test_non_200_upstream_reports_status_and_error_text(upstream, relays):
    upstream.mode = "error"
    adm = relays(upstream.url)
    status, _, body = post(adm.url)
    assert status == 529 and body == upstream.error_body  # native sees the real answer
    assert adm.status == 529
    assert adm.error_text() == "prompt is too long: 213000 tokens > 200000 maximum"
    assert adm.capture.complete is False
    assert post(adm.url)[0] == 400 and adm.denied == 1 and len(upstream.hits) == 1


def test_non_json_error_body_is_returned_raw(upstream, relays):
    upstream.mode = "error"
    upstream.error_status = 502
    upstream.error_body = b"<html>bad gateway</html>"
    adm = relays(upstream.url)
    post(adm.url)
    assert adm.status == 502 and adm.error_text() == "<html>bad gateway</html>"


def test_truncated_stream_is_an_incomplete_capture(upstream, relays):
    upstream.mode = "truncated"
    adm = relays(upstream.url)
    status, _, _ = post(adm.url)
    assert status == 200 and adm.status == 200
    assert adm.capture.complete is False
    assert adm.capture.message["content"][1]["text"] == "Hello "  # what did arrive was captured


def test_upstream_read_timeout_is_a_relay_failure(upstream, relays):
    upstream.mode = "hang"
    adm = relays(upstream.url, timeout=0.5)
    with pytest.raises((http.client.HTTPException, OSError)):
        post(adm.url)
    assert adm.failure == "TimeoutError" and adm.status is None and adm.used is True
    assert upstream.disconnected.wait(3)


# --------------------------------------------------------------------------- close / cancel


def test_close_mid_stream_shuts_the_upstream_socket(upstream, relays):
    upstream.mode = "hang"
    adm = relays(upstream.url, timeout=30.0)
    errors: list[BaseException] = []

    def client():
        try:
            post(adm.url, timeout=30.0)
        except BaseException as exc:  # noqa: BLE001 - the relay hangs up on us; that is the point
            errors.append(exc)

    t = threading.Thread(target=client, daemon=True)
    t.start()
    assert upstream.entered.wait(5)
    started = time.monotonic()
    closer = threading.Thread(target=adm.close, daemon=True)
    closer.start()
    assert upstream.disconnected.wait(3), "close() must shut the active upstream connection"
    closer.join(5)
    assert not closer.is_alive() and time.monotonic() - started < 5
    t.join(5)
    assert not t.is_alive() and errors  # native's connection was cut too
    assert adm.cancelled is True


def test_close_is_idempotent_and_releases_the_port(upstream, relays):
    adm = relays(upstream.url)
    post(adm.url)
    adm.close()
    adm.close()
    with pytest.raises(OSError):
        post(adm.url, timeout=2.0)


# --------------------------------------------------------------------------- secrets never logged


class _Grab(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.name + " " + record.getMessage() + " " + repr(record.__dict__))


def test_headers_bodies_and_route_never_reach_logs(upstream, relays, caplog, capfd):
    """``ironjarvis`` does not propagate once configured, so grab the module logger directly."""
    caplog.set_level(logging.DEBUG)
    logger = logging.getLogger("ironjarvis.claude_native")
    grab, old_level = _Grab(), logger.level
    logger.addHandler(grab)
    logger.setLevel(logging.DEBUG)
    try:
        _drive_relay_with_secrets(upstream, relays, grab, caplog, capfd)
    finally:
        logger.removeHandler(grab)
        logger.setLevel(old_level)


def _drive_relay_with_secrets(upstream, relays, grab, caplog, capfd):
    adm = relays(upstream.url)
    secret_body = json.dumps({"model": "m", "messages": [{"role": "user", "content": "BODY-SECRET-4411"}]}).encode()
    hdrs = {"Authorization": SECRET_BEARER, "x-api-key": SECRET_KEY}
    post(adm.url, body=secret_body, headers=hdrs)
    post(adm.url, body=secret_body, headers=hdrs)  # the denied path too
    post(adm.url, body=secret_body, headers=hdrs, path_suffix="/v1/nope")  # and the 404 path
    assert adm.denied == 1
    out, err = capfd.readouterr()
    token = adm.prefix.rsplit("/", 1)[-1]
    logged = " | ".join(grab.lines)
    for text in (logged, caplog.text, out, err):
        for secret in ("SECRET-DO-NOT-LOG", "ALSO-SECRET", "BODY-SECRET-4411", token):
            assert secret not in text
    # the module does log (status, refusal count) — just never the sensitive parts
    assert "status 200" in logged and "denied=1" in logged


# --------------------------------------------------------------------------- Capture framing


def test_capture_survives_crlf_frames_and_split_multibyte_utf8():
    events = [
        {"type": "message_start", "message": {"id": "m", "role": "assistant", "content": [], "usage": dict(USAGE_START)}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "café ✓ 日本"}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "citations_delta", "citation": {"type": "char_location", "cited_text": "x"}}},
        {"type": "content_block_stop", "index": 0},
        {"type": "ping"},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 3}},
        {"type": "message_stop"},
    ]
    raw = "".join("event: x\r\ndata: " + json.dumps(e, ensure_ascii=False) + "\r\n\r\n" for e in events).encode()
    cap = Capture()
    for k in range(len(raw)):
        cap.feed(raw[k:k + 1])
    assert cap.complete is True
    assert cap.message["content"][0]["text"] == "café ✓ 日本"
    assert cap.message["content"][0]["citations"] == [{"type": "char_location", "cited_text": "x"}]


def test_capture_with_an_unfinished_tool_argument_is_incomplete():
    events = full_events()
    events = [e for e in events if not (e["type"] == "content_block_stop" and e["index"] == 2)]
    cap = Capture()
    cap.feed(sse(events))
    assert cap.complete is False


# --------------------------------------------------------------------------- breakpoint pinning

MARKER = {"type": "ephemeral"}
ASSISTANT = {"role": "assistant", "content": [
    {"type": "thinking", "thinking": "signed", "signature": "sig"},
    {"type": "tool_use", "id": "t1", "name": "mcp__ij__probe", "input": {}}]}
RESULT = {"type": "tool_result", "tool_use_id": "t1", "content": "real output"}
QUESTION = {"type": "text", "text": "the real question"}


def wire(messages):
    return json.dumps({"model": "m", "messages": messages}).encode()


def marks(payload):
    return [(i, j) for i, m in enumerate(json.loads(payload)["messages"])
            for j, b in enumerate(m["content"]) if "cache_control" in b]


def unmarked(payload):
    body = json.loads(payload)
    for m in body["messages"]:
        for b in m["content"]:
            b.pop("cache_control", None)
    return body


def with_marker(block):
    return {**block, "cache_control": MARKER}


@pytest.mark.parametrize("queried,messages,expected", [
    # 2.1.280 tool round: reminder appended inside the tool_result, then a separate date message
    ([RESULT], [ASSISTANT,
                {"role": "user", "content": [{**RESULT, "content": "real output\n<system-reminder>userEmail</system-reminder>"}]},
                {"role": "system", "content": [with_marker({"type": "text", "text": "Today's date is 2026-09-23."})]}],
     (0, 1)),
    # an annotation after the host content, in wording no heuristic knows
    ([QUESTION], [ASSISTANT,
                  {"role": "user", "content": [QUESTION, with_marker({"type": "text", "text": "Session context: v9 build"})]}],
     (1, 0)),
    # an annotation prepended to the newest turn: nothing of that turn recurs
    ([QUESTION], [ASSISTANT,
                  {"role": "user", "content": [{"type": "text", "text": "Any new preamble"}, with_marker(QUESTION)]}],
     (0, 1)),
    # tool results then the user's own text, native's reminder in that text: the results recur
    ([RESULT, QUESTION], [ASSISTANT,
                          {"role": "user", "content": [RESULT, with_marker({**QUESTION, "text": "the real question\n<system-reminder>x</system-reminder>"})]}],
     (1, 0)),
    # every queried block unchanged, native's date in its own message after them
    ([RESULT], [ASSISTANT,
                {"role": "user", "content": [RESULT]},
                {"role": "system", "content": [with_marker({"type": "text", "text": "Today's date is 2026-09-23."})]}],
     (1, 0)),
    # a block native appends after unchanged tool results: the results recur
    ([RESULT], [ASSISTANT,
                {"role": "user", "content": [RESULT, with_marker({"type": "text", "text": "Session context: v9 build"})]}],
     (1, 0)),
])
def test_breakpoint_moves_to_the_last_block_the_host_itself_sent(queried, messages, expected):
    raw = wire(messages)
    out = pin_message_breakpoint(raw, queried)
    assert marks(out) == [expected]
    assert unmarked(out) == unmarked(raw)  # only the directive moves, never content


@pytest.mark.parametrize("count", [2, 4])
def test_parallel_tool_results_do_not_pin_inside_a_partly_changed_user_message(count):
    results = [{"type": "tool_result", "tool_use_id": f"t{i}", "content": f"output {i}"} for i in range(count)]
    assistant = {"role": "assistant", "content": [
        {"type": "tool_use", "id": f"t{i}", "name": f"probe_{i}", "input": {}} for i in range(count)]}
    changed_last = {**results[-1], "content": results[-1]["content"] + "\n<system-reminder>native note</system-reminder>"}
    raw = wire([assistant, {"role": "user", "content": [*results[:-1], with_marker(changed_last)]}])
    out = pin_message_breakpoint(raw, results)
    assert marks(out) == [(0, count - 1)]
    assert unmarked(out) == unmarked(raw)


def test_thinking_blocks_never_receive_the_breakpoint():
    only_thinking = {"role": "assistant", "content": [
        {"type": "text", "text": "earlier"},
        {"type": "thinking", "thinking": "t", "signature": "s"},
        {"type": "redacted_thinking", "data": "r"}]}
    raw = wire([only_thinking, {"role": "user", "content": [with_marker({"type": "text", "text": "native date"})]}])
    out = pin_message_breakpoint(raw, [QUESTION])
    assert marks(out) == [(0, 0)]  # skips both thinking kinds, lands on the text before them


@pytest.mark.parametrize("payload,queried", [
    # already on the last stable block: never moves later
    (wire([ASSISTANT, {"role": "user", "content": [RESULT, with_marker(QUESTION)]}]), [RESULT, QUESTION]),
    # nothing stable before the marker
    (wire([{"role": "user", "content": [with_marker({"type": "text", "text": "Any preamble"})]}]), [QUESTION]),
    # unparseable payload forwards unchanged
    (b"not json", [QUESTION]),
    # no queried frame: nothing to anchor on
    (wire([ASSISTANT, {"role": "user", "content": [with_marker(RESULT)]}]), None),
    # two markers: not ours to rearrange
    (wire([{"role": "assistant", "content": [with_marker({"type": "text", "text": "a"})]},
           {"role": "user", "content": [with_marker({"type": "text", "text": "b"})]}]), [QUESTION]),
    # no marker at all
    (wire([ASSISTANT, {"role": "user", "content": [RESULT]}]), [RESULT]),
])
def test_stable_breakpoint_or_nothing_to_anchor_forwards_unchanged(payload, queried):
    assert pin_message_breakpoint(payload, queried) is payload


def test_relay_forwards_the_pinned_payload_upstream(upstream, relays):
    """End to end: native's breakpoint on its appended context arrives upstream on host content."""
    messages = [ASSISTANT, {"role": "user", "content": [
        RESULT, with_marker({"type": "text", "text": "<system-reminder>Today's date</system-reminder>"})]}]
    raw = wire(messages)
    adm = relays(upstream.url, queried=[RESULT])
    assert post(adm.url, body=raw)[0] == 200
    forwarded = upstream.hits[0]["body"]
    assert marks(forwarded) == [(1, 0)]
    assert unmarked(forwarded) == unmarked(raw)


def test_relay_without_a_queried_frame_forwards_bytes_untouched(upstream, relays):
    raw = wire([ASSISTANT, {"role": "user", "content": [RESULT, with_marker(QUESTION)]}])
    adm = relays(upstream.url)
    post(adm.url, body=raw)
    assert upstream.hits[0]["body"] == raw


# --------------------------------------------------------------------------- the sign-in retry
# v1.300.0 review, second pass: after a 401 (or a 403 typed authentication_error) the
# real CLI refreshes its sign-in and sends the request AGAIN — measured live. A refusal
# spent nothing, so that ONE retry is admitted; every other retry stays refused.
AUTH_401 = b'{"type":"error","error":{"type":"authentication_error","message":"OAuth token has expired."}}'
PERM_403 = b'{"type":"error","error":{"type":"permission_error","message":"Your organization does not have access to this model."}}'
REVOKED_403 = b'{"type":"error","error":{"type":"authentication_error","message":"OAuth token has been revoked."}}'


@pytest.mark.parametrize("status,body", [(401, AUTH_401), (403, REVOKED_403)])
def test_the_one_retry_after_a_sign_in_refusal_is_admitted_and_counts(upstream, relays, status, body):
    upstream.mode, upstream.error_status, upstream.error_body = "error", status, body
    adm = relays(upstream.url)
    assert post(adm.url)[0] == status
    assert adm.status == status and adm.error_type() == "authentication_error"
    upstream.mode = "stream"  # the refreshed sign-in works
    assert post(adm.url)[0] == 200
    assert len(upstream.hits) == 2 and adm.denied == 0
    # The record is the retry's, not the refusal's.
    assert adm.status == 200 and adm.capture.complete is True and adm.error_body == b""
    assert adm.capture.message["stop_reason"] == "tool_use"
    # ...and that was the only one.
    status3, _, body3 = post(adm.url)
    assert status3 == 400 and CONSUMED_MESSAGE.encode() in body3
    assert len(upstream.hits) == 2 and adm.denied == 1


def test_a_second_sign_in_refusal_is_not_retried_again(upstream, relays):
    upstream.mode, upstream.error_status, upstream.error_body = "error", 401, AUTH_401
    adm = relays(upstream.url)
    assert post(adm.url)[0] == 401
    assert post(adm.url)[0] == 401  # the one retry
    assert post(adm.url)[0] == 400  # no third
    assert len(upstream.hits) == 2 and adm.denied == 1
    assert adm.status == 401 and adm.error_type() == "authentication_error"


@pytest.mark.parametrize("status,body", [
    (403, PERM_403),  # a model-access refusal is not a sign-in problem
    (529, b'{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}'),
    (429, b'{"type":"error","error":{"type":"rate_limit_error","message":"slow down"}}'),
])
def test_no_other_refusal_lets_a_retry_through(upstream, relays, status, body):
    upstream.mode, upstream.error_status, upstream.error_body = "error", status, body
    adm = relays(upstream.url)
    assert post(adm.url)[0] == status
    upstream.mode = "stream"
    assert post(adm.url)[0] == 400
    assert len(upstream.hits) == 1 and adm.denied == 1 and adm.status == status


def test_a_completed_answer_never_lets_a_retry_through(upstream, relays):
    adm = relays(upstream.url)
    assert post(adm.url)[0] == 200
    assert post(adm.url)[0] == 400
    assert len(upstream.hits) == 1
