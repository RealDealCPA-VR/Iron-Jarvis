"""v1.288.0 (agents-05) — a remote agent's ``timeout_s`` is a TOTAL deadline.

httpx's ``timeout=`` is per phase: the read timeout is the gap between
chunks, not the whole reply. A remote (or a gateway sending keep-alive
whitespace) that trickles bytes never tripped it, so ``@hermes`` in chat never
finished and, from the phone, the poller stalled for every channel.

Driven against a REAL local HTTP server (ephemeral port) through the REAL
``RemoteAgentRegistry.run``. Timings are asserted as ratios of the configured
``timeout_s`` — never an absolute wall-clock figure.
"""

from __future__ import annotations

import asyncio
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from iron_jarvis.agents.remote import RemoteAgentRecord, RemoteAgentRegistry

#: The trickle: valid JSON, one byte per STEP seconds. Each gap is far under
#: the per-read timeout, and the whole body takes far longer than TIMEOUT_S.
STEP = 0.25
BODY = b'{"result": "ok"' + b" " * 60 + b"}"
TIMEOUT_S = 1


class _Trickle(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # quiet
        pass

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        self.rfile.read(n)
        fast = self.path.startswith("/fast")
        body = b'{"result": "fast ok"}' if fast else BODY
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        try:
            if fast:
                self.wfile.write(body)
                return
            for i in range(len(body)):
                self.wfile.write(body[i : i + 1])
                self.wfile.flush()
                time.sleep(STEP)
        except Exception:  # noqa: BLE001 — the client hung up (the point)
            pass


@pytest.fixture(scope="module")
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Trickle)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _run(rec: RemoteAgentRecord, guard_s: float):
    async def go():
        t0 = time.monotonic()
        try:
            res = await asyncio.wait_for(
                RemoteAgentRegistry.run(None, rec, "hi", lambda n: ""), timeout=guard_s
            )
        except TimeoutError:
            res = "OUTER GUARD FIRED"
        return time.monotonic() - t0, res

    return asyncio.run(go())


def test_trickling_reply_is_cut_at_the_total_deadline(server):
    rec = RemoteAgentRecord(
        name="slow", base_url=server + "/task", kind="http-task", timeout_s=TIMEOUT_S
    )
    full_body_s = len(BODY) * STEP  # what the per-read timeout alone would wait
    # Sanity on the fixture itself: the trickle outlasts the deadline many times.
    assert full_body_s > 10 * TIMEOUT_S
    took, res = _run(rec, guard_s=full_body_s / 2)
    assert res != "OUTER GUARD FIRED", "run() ignored its timeout_s and ran until the guard"
    assert isinstance(res, dict) and res["ok"] is False
    assert res["detail"] == f"slow did not answer within {TIMEOUT_S}s"
    # Ratio, not an absolute figure: it ended near the deadline, far from the body.
    assert took < full_body_s / 3, (took, full_body_s)


def test_a_reply_inside_the_deadline_is_unchanged(server):
    rec = RemoteAgentRecord(name="quick", base_url=server + "/fast", kind="http-task", timeout_s=5)
    _took, res = _run(rec, guard_s=30)
    assert res["ok"] is True and res["result"] == "fast ok"


def test_a_timeout_error_the_transport_raises_keeps_its_own_words(monkeypatch):
    """Only the deadline scope expiring earns the deadline wording (the
    v1.228.0 lesson: on 3.11+ ``asyncio.TimeoutError`` IS the builtin, so a
    TimeoutError raised from inside the call must keep its own message)."""
    import httpx

    async def boom(self, *a, **k):
        raise TimeoutError("socket said no")

    monkeypatch.setattr(httpx.AsyncClient, "post", boom)
    rec = RemoteAgentRecord(name="r", base_url="http://127.0.0.1:9/x", kind="http-task", timeout_s=5)
    res = asyncio.run(RemoteAgentRegistry.run(None, rec, "hi", lambda n: ""))
    assert res["ok"] is False
    assert "did not answer within" not in res["detail"]
    assert "socket said no" in res["detail"]
