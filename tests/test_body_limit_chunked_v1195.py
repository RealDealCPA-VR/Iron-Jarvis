"""BodyLimitMiddleware must cap a CHUNKED body too (v1.195.0).

The guard used to enforce the cap by scanning the ASGI scope for a
``content-length`` header. An HTTP/1.1 request using ``Transfer-Encoding:
chunked`` carries none, so the loop found nothing, ``break``ed, and the body
passed through UNLIMITED — reproduced against a real uvicorn with the cap at
1 MB, the SAME valid 2 MB JSON body: with ``Content-Length`` -> 413, chunked ->
200 OK, accepted and written to disk.

These tests drive raw ASGI scopes (a ``receive`` yielding several chunks with
``more_body=True`` is what reproduces the bug) plus one end-to-end pass through
httpx's ASGI transport, which really does send an iterator body as chunked with
no content-length header.
"""

from __future__ import annotations

import json
import logging

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect

from iron_jarvis.daemon.auth import BodyLimitMiddleware


def _scope(headers=None, method="POST", path="/documents/upload"):
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": list(headers or []),
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8787),
        "scheme": "http",
    }


class _Recorder:
    """Inner ASGI app that reads the whole body, then answers 200."""

    def __init__(self, *, disconnect_raises: bool = True) -> None:
        self.body = b""
        self.calls = 0
        self.disconnect_raises = disconnect_raises

    async def __call__(self, scope, receive, send) -> None:
        self.calls += 1
        body = b""
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                # This is exactly what Starlette does when the stream is cut
                # (``Request.stream`` raises ClientDisconnect); the middleware
                # must swallow it, having already answered 413.
                if self.disconnect_raises:
                    raise ClientDisconnect()
                break
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        self.body = body
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b'{"ok":true}'})


async def _drive(mw, scope, messages):
    """Feed `messages` through the middleware; return everything it sent."""
    pending = list(messages)
    pulls = {"n": 0}
    sent: list[dict] = []

    async def receive():
        pulls["n"] += 1
        if pending:
            return pending.pop(0)
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(dict(message))

    await mw(scope, receive, send)
    return sent, pulls


def _status(sent):
    starts = [m for m in sent if m["type"] == "http.response.start"]
    # Two http.response.start messages on one scope is an ASGI protocol error;
    # asserting the count is how we prove the 413 didn't race the inner app.
    assert len(starts) == 1, f"expected exactly one response start, got {sent}"
    return starts[0]["status"]


def _body(sent):
    return b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")


def _chunks(total: int, n: int = 4):
    """`n` http.request messages summing to `total` bytes, all but the last with
    more_body=True — the shape a chunked request actually arrives in."""
    per = total // n
    out = []
    left = total
    for i in range(n):
        size = per if i < n - 1 else left
        left -= size
        out.append({"type": "http.request", "body": b"x" * size, "more_body": i < n - 1})
    return out


# --- the bug ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_over_cap_chunked_body_is_refused_413():
    """No content-length header at all: this returned 200 before the fix."""
    app = _Recorder()
    mw = BodyLimitMiddleware(app)
    mw.max_bytes = 1024

    sent, _ = await _drive(mw, _scope(), _chunks(4096))

    assert _status(sent) == 413
    assert b"too large" in _body(sent)
    assert app.body == b"" or len(app.body) <= 4096  # never got a full body through


@pytest.mark.asyncio
async def test_over_cap_chunked_stops_pulling_bytes_off_the_wire():
    """The cut must stop the read, not merely relabel the answer: once the cap is
    crossed the middleware must not call the real receive again."""
    app = _Recorder(disconnect_raises=False)
    mw = BodyLimitMiddleware(app)
    mw.max_bytes = 1024

    # 20 chunks of 512 bytes: the cap is crossed on the third.
    msgs = [
        {"type": "http.request", "body": b"y" * 512, "more_body": i < 19} for i in range(20)
    ]
    sent, pulls = await _drive(mw, _scope(), msgs)

    assert _status(sent) == 413
    assert pulls["n"] <= 4, f"kept reading after the cap: {pulls['n']} receives"


@pytest.mark.asyncio
async def test_inner_app_response_after_413_is_dropped():
    """The inner app answering 200 after the cut must never reach the wire."""

    class _StubbornApp:
        async def __call__(self, scope, receive, send):
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    break
                if not message.get("more_body"):
                    break
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"leaked"})

    mw = BodyLimitMiddleware(_StubbornApp())
    mw.max_bytes = 512
    sent, _ = await _drive(mw, _scope(), _chunks(2048))

    assert _status(sent) == 413  # asserts exactly ONE response start
    assert b"leaked" not in _body(sent)


# --- everything that must stay exactly as it was ---------------------------


@pytest.mark.asyncio
async def test_under_cap_multi_chunk_body_is_untouched():
    payload = json.dumps({"blob": "z" * 4000}).encode()
    app = _Recorder()
    mw = BodyLimitMiddleware(app)
    mw.max_bytes = 1024 * 1024

    msgs = [
        {"type": "http.request", "body": payload[:1000], "more_body": True},
        {"type": "http.request", "body": payload[1000:2500], "more_body": True},
        {"type": "http.request", "body": payload[2500:], "more_body": False},
    ]
    sent, _ = await _drive(mw, _scope(), msgs)

    assert _status(sent) == 200
    assert app.body == payload  # byte-for-byte
    assert _body(sent) == b'{"ok":true}'


@pytest.mark.asyncio
async def test_request_with_no_body_still_succeeds():
    app = _Recorder()
    mw = BodyLimitMiddleware(app)
    mw.max_bytes = 1024

    sent, _ = await _drive(
        mw,
        _scope(method="GET", path="/health"),
        [{"type": "http.request", "body": b"", "more_body": False}],
    )

    assert _status(sent) == 200
    assert app.body == b""


@pytest.mark.asyncio
async def test_content_length_precheck_refuses_before_reading_a_byte():
    """The fast path is the reason the class exists; the streaming counter is an
    addition, not a replacement."""
    app = _Recorder()
    mw = BodyLimitMiddleware(app)
    mw.max_bytes = 1024

    sent, pulls = await _drive(
        mw,
        _scope(headers=[(b"content-length", b"999999")]),
        _chunks(999999, n=2),
    )

    assert _status(sent) == 413
    assert pulls["n"] == 0, "body was read despite an oversized content-length"
    assert app.calls == 0


@pytest.mark.asyncio
async def test_non_http_scopes_are_passed_through_verbatim():
    seen = {}

    async def inner(scope, receive, send):
        seen["scope"] = scope
        seen["receive"] = receive
        seen["send"] = send

    mw = BodyLimitMiddleware(inner)
    mw.max_bytes = 1

    async def receive():  # pragma: no cover - identity is what's asserted
        return {"type": "websocket.connect"}

    async def send(message):  # pragma: no cover - ditto
        return None

    for kind in ("websocket", "lifespan"):
        seen.clear()
        await mw({"type": kind, "headers": []}, receive, send)
        # Identity, not equality: a wrapped receive/send on a websocket would
        # count bytes that are not a request body at all.
        assert seen["receive"] is receive
        assert seen["send"] is send


# --- end to end through a real client that really sends chunked ------------


def _app_with_limit(max_bytes: int) -> FastAPI:
    app = FastAPI()

    @app.post("/echo")
    async def echo(request: Request):  # noqa: ANN202
        raw = await request.body()
        return {"len": len(raw)}

    app.add_middleware(BodyLimitMiddleware)
    # add_middleware constructs lazily on first request; reach the instance the
    # same way the daemon would run it, then pin the cap for the test.
    app.build_middleware_stack()
    return app


def _pin_cap(client: TestClient, max_bytes: int) -> None:
    stack = client.app.middleware_stack
    while stack is not None and not isinstance(stack, BodyLimitMiddleware):
        stack = getattr(stack, "app", None)
    assert isinstance(stack, BodyLimitMiddleware), "middleware not in the stack"
    stack.max_bytes = max_bytes


def _stream(total: int, chunk: int = 4096):
    def gen():
        left = total
        while left > 0:
            n = min(chunk, left)
            left -= n
            yield b"q" * n

    return gen()


def test_end_to_end_chunked_upload_is_413_and_small_one_is_200():
    """httpx sends a generator body as chunked with NO content-length — the
    exact wire shape that used to sail past the guard."""
    app = _app_with_limit(64 * 1024)
    with TestClient(app) as client:
        _pin_cap(client, 64 * 1024)

        over = client.post("/echo", content=_stream(256 * 1024))
        assert over.status_code == 413, over.text
        assert "too large" in over.text

        under = client.post("/echo", content=_stream(8 * 1024))
        assert under.status_code == 200, under.text
        assert under.json()["len"] == 8 * 1024


def test_full_middleware_stack_answers_one_clean_413():
    """The real install order: BodyLimit sits OUTSIDE two BaseHTTPMiddleware
    layers (daemon/app.py adds ErrorEnvelope innermost, then TokenAuth). Cutting
    the body makes Starlette raise ClientDisconnect inside them, and
    ErrorEnvelope turns that into a 500 — which must NOT reach the client on top
    of our 413 (two http.response.start on one scope is a protocol error)."""
    from iron_jarvis.daemon.auth import ErrorEnvelopeMiddleware, TokenAuthMiddleware

    app = FastAPI()

    @app.post("/echo")
    async def echo(request: Request):  # noqa: ANN202
        return {"len": len(await request.body())}

    app.add_middleware(ErrorEnvelopeMiddleware)
    app.add_middleware(TokenAuthMiddleware)
    app.add_middleware(BodyLimitMiddleware)

    with TestClient(app) as client:
        _pin_cap(client, 64 * 1024)
        over = client.post("/echo", content=_stream(256 * 1024))
        assert over.status_code == 413, over.text
        assert "too large" in over.text
        assert "internal error" not in over.text  # the swallowed 500 didn't leak
        # A plain JSON body (content-length, under cap) is unaffected.
        ok = client.post("/echo", json={"a": 1})
        assert ok.status_code == 200, ok.text


# --- the refusal must be QUIET as well as correct --------------------------


def _stacked_app() -> FastAPI:
    """The real install order (daemon/app.py): ErrorEnvelope innermost, then
    TokenAuth, then BodyLimit."""
    from iron_jarvis.daemon.auth import ErrorEnvelopeMiddleware, TokenAuthMiddleware

    app = FastAPI()

    @app.post("/echo")
    async def echo(request: Request):  # noqa: ANN202
        return {"len": len(await request.body())}

    app.add_middleware(ErrorEnvelopeMiddleware)
    app.add_middleware(TokenAuthMiddleware)
    app.add_middleware(BodyLimitMiddleware)
    return app


def test_refused_chunked_request_logs_no_error(caplog):
    """A correctly-refused over-cap upload must not write an ERROR + traceback.

    Cutting the body makes Starlette raise ClientDisconnect inside the route,
    and ErrorEnvelopeMiddleware used to log that with ``.exception()``:
    ``ERROR unhandled error on POST /echo`` plus a ~50-line traceback, per
    refused request. The line is FALSE (the request WAS handled — by us, on
    purpose), it is the exact wrong-diagnosis shape ErrorEnvelope's own
    docstring exists to warn about, and it turns this DoS guard into a log
    amplifier: N refused uploads cost N x ~2 KB of ERROR log where before the
    chunked fix they cost nothing.
    """
    app = _stacked_app()
    with TestClient(app) as client:
        _pin_cap(client, 64 * 1024)
        with caplog.at_level(logging.DEBUG, logger="iron_jarvis.daemon"):
            over = client.post("/echo", content=_stream(256 * 1024))

    assert over.status_code == 413, over.text
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not errors, f"refusing a chunked body logged an error: {[r.getMessage() for r in errors]}"
    assert not [r for r in caplog.records if r.exc_info], "a traceback was logged"
    assert "unhandled error" not in caplog.text
    # Silence is not the goal — HONESTY is. The disconnect is still reported,
    # once, at INFO, naming the request.
    assert any(
        "client disconnected" in r.getMessage() and r.levelno == logging.INFO
        for r in caplog.records
    ), caplog.text


def test_under_cap_request_logs_nothing_at_all(caplog):
    """The quiet path stays quiet: no INFO chatter on ordinary traffic."""
    app = _stacked_app()
    with TestClient(app) as client:
        _pin_cap(client, 64 * 1024)
        with caplog.at_level(logging.DEBUG, logger="iron_jarvis.daemon"):
            ok = client.post("/echo", json={"a": 1})

    assert ok.status_code == 200, ok.text
    assert [r for r in caplog.records if r.name == "iron_jarvis.daemon"] == []


@pytest.mark.asyncio
async def test_failure_to_deliver_the_413_is_reported_not_swallowed(caplog):
    """If the 413 send itself fails, the caller gets NOTHING — say so.

    ``refused`` is set BEFORE the send (it has to be: a half-sent refusal must
    still bar the inner app from starting a second response), so a failed
    refusal reaches ``except Exception: if not refused: raise`` and is swallowed
    looking exactly like a successful one. The report therefore lives where the
    failure happens. It cannot live in that ``except`` branch: ErrorEnvelope
    sits INSIDE this middleware and catches the exception first, so in the real
    stack — which is what this test builds — the branch never even runs.
    """

    class _Boom(RuntimeError):
        pass

    app = _stacked_app()
    with TestClient(app):  # build the stack + pin the cap the way the app runs it
        pass
    stack = app.middleware_stack
    while stack is not None and not isinstance(stack, BodyLimitMiddleware):
        stack = getattr(stack, "app", None)
    assert isinstance(stack, BodyLimitMiddleware)
    stack.max_bytes = 1024

    sent: list[dict] = []

    async def send(message):
        sent.append(dict(message))
        if message["type"] == "http.response.start":
            raise _Boom("transport gone")

    pending = list(_chunks(8192))

    async def receive():
        if pending:
            return pending.pop(0)
        return {"type": "http.disconnect"}

    # A REAL route: on an unmatched path the app never calls receive, so the cap
    # is never crossed and this would exercise nothing.
    scope = _scope(path="/echo")
    scope["root_path"] = ""
    with caplog.at_level(logging.DEBUG, logger="iron_jarvis.daemon"):
        await stack(scope, receive, send)

    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "could not deliver the 413" in r.getMessage()
    ]
    assert warnings, f"a caller got no response and nothing said so: {caplog.text}"
    msg = warnings[0].getMessage()
    assert "POST" in msg and "/echo" in msg  # names the request
    assert "_Boom" in msg  # and why it failed
    # Nothing was written to the wire past the failed start, and no second one.
    assert [m for m in sent if m["type"] == "http.response.start"] == sent[:1]
