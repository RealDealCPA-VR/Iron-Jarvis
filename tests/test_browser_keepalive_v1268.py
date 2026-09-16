"""v1.268.0 — the heartbeat that keeps the add-on's worker, and so its socket, alive.

THE REPORT: "it randomly disconnected and reconnected. This makes for a poor
user experience."

WHAT WAS TRUE. Chromium evicts a Manifest V3 service worker after ~30 s without
events, and the add-on's socket dies with it; the next tab switch woke the
worker and it paired back in. The old note in socket.ts called that "a real
limitation and not a bug to hunt later". It is a bug, and it has a fix that only
the daemon can supply: WebSocket traffic inside the idle window resets the timer
(Chrome 116+), and a timer inside the worker cannot provide it because it dies
with the worker.

WHAT IS PINNED.

* **The daemon pings a paired socket** every ``KEEPALIVE_S`` — driven through the
  REAL ``/browser/ws`` route with a ``TestClient`` socket, the interval shortened
  on the route module (no test waits twenty seconds), the ping read off the wire
  with a bounded reader.
* **A restricted (unpaired) socket gets no ping**: it may receive nothing but its
  pairing frames, and a ping there would be a frame the pairing rule refuses.
* **The pong is recorded and reported**: ``conn.last_pong_at`` and
  ``GET /browser/status``'s ``keepalive`` block.
* **The interval is inside Chromium's window** (20 s < 30 s) and the frames are
  registered in both direction sets and the shape map.
* **The add-on answers**: ``pongFor`` lifted from socket.ts and run under node;
  the switch has a ``FRAME_PING`` branch; the generated protocol carries both
  frames and the interval; the old "real limitation" note is gone.

Nothing here asserts a duration: the bounded reader fails with a name.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from iron_jarvis.browser import protocol as P
from iron_jarvis.browser.extension_backend import ExtensionBackend, ExtensionConnection
from iron_jarvis.browser.pairing import PairingStore
from iron_jarvis.browser.service import BrowserRuntime
from iron_jarvis.daemon.routes import browser as browser_routes
from tests.test_browser_pairing_v1235 import (
    _ADDON_ORIGIN,
    FakeConfig,
    FakeSocket,
    _BoundedReader,
    _RouteDeps,
    open_db,
)

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "extensions" / "chrome" / "src"
SOCKET_TS = ADDON / "bridge" / "socket.ts"
PROTOCOL_TS = ADDON / "protocol.ts"

requires_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _wait_until(predicate, what: str, *, tries: int = 1500) -> None:
    for _ in range(tries):
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(f"never happened: {what}")


def _paired_app(tmp_path):
    store = PairingStore(open_db(tmp_path / "keepalive.db"))
    backend = ExtensionBackend()
    runtime = BrowserRuntime(backend=backend, config=FakeConfig(), pairing=store)
    app = FastAPI()
    browser_routes.register(app, _RouteDeps(runtime))
    return app, backend


# --------------------------------------------------------------------------- #
# The daemon's half
# --------------------------------------------------------------------------- #


def test_the_daemon_pings_a_paired_socket_and_records_the_pong(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_routes, "KEEPALIVE_S", 0.15)
    app, backend = _paired_app(tmp_path)
    with TestClient(app) as client:
        with client.websocket_connect(
            "/browser/ws?pairing=1", headers={"Origin": _ADDON_ORIGIN}
        ) as ws:
            reader = _BoundedReader(ws)
            request_id = reader.frame(P.FRAME_PAIRING_REQUIRED)["request_id"]
            ws.send_json(P.pairing_ack_frame(request_id))
            r = client.post("/browser/pair", json={"request_id": request_id})
            assert r.status_code == 200, r.text
            reader.frame(P.FRAME_PAIRED)
            reader.frame(P.FRAME_READY)
            _wait_until(lambda: backend.connected, "the socket never became authoritative")

            ping = reader.frame(P.FRAME_PING)
            assert isinstance(ping["t"], int) and ping["t"] > 0
            # A second one follows without any help from the add-on.
            reader.frame(P.FRAME_PING)

            before = client.get("/browser/status").json()["keepalive"]
            assert before["interval_s"] == P.KEEPALIVE_S and before["last_pong_at"] is None

            ws.send_json(P.pong_frame(ping["t"]))
            conn = backend.connection
            _wait_until(lambda: conn.last_pong_at is not None, "the pong was never recorded")
            after = client.get("/browser/status").json()["keepalive"]
            assert after["last_pong_at"] is not None
            assert backend.last_error == "", "a pong must never be reported as an error"


def test_a_restricted_socket_is_never_pinged(tmp_path, monkeypatch):
    """A pairing socket may receive nothing but its pairing frames."""
    monkeypatch.setattr(browser_routes, "KEEPALIVE_S", 0.1)
    app, _backend = _paired_app(tmp_path)
    with TestClient(app) as client:
        with client.websocket_connect(
            "/browser/ws?pairing=1", headers={"Origin": _ADDON_ORIGIN}
        ) as ws:
            reader = _BoundedReader(ws)
            reader.frame(P.FRAME_PAIRING_REQUIRED)
            # Six intervals of silence is the proof; the reader's bound is the clock.
            reader.WAIT_S = 0.6
            with pytest.raises(AssertionError, match="never sent"):
                reader.frame(P.FRAME_PING)


async def test_a_pong_on_a_paired_connection_is_recorded_and_on_a_restricted_one_refused():
    backend = ExtensionBackend()
    paired = ExtensionConnection(FakeSocket(), paired=True)
    assert await backend.handle_frame(paired, P.pong_frame(42)) is True
    assert paired.last_pong_at is not None
    assert backend.last_error == ""

    restricted = ExtensionConnection(FakeSocket(), paired=False, pairing_request_id="pair_x")
    assert await backend.handle_frame(restricted, P.pong_frame(42)) is False
    assert restricted.closed and restricted.ws.closed_with == 1008


def test_the_interval_is_inside_chromiums_window_and_the_frames_are_registered():
    assert 5.0 <= P.KEEPALIVE_S < 30.0, "the heartbeat must beat inside the ~30 s idle window"
    assert P.FRAME_PING in P.DAEMON_TO_EXTENSION and P.FRAME_PING not in P.EXTENSION_TO_DAEMON
    assert P.FRAME_PONG in P.EXTENSION_TO_DAEMON and P.FRAME_PONG not in P.DAEMON_TO_EXTENSION
    assert P.FRAME_PONG not in P.RESTRICTED_INBOUND_FRAMES
    assert P.FRAME_SHAPES[P.FRAME_PING] is P.PingFrame and P.FRAME_SHAPES[P.FRAME_PONG] is P.PongFrame
    assert P.ping_frame(7) == {"type": "browser.ping", "t": 7}
    assert P.pong_frame(7) == {"type": "browser.pong", "t": 7}
    assert browser_routes.KEEPALIVE_S == P.KEEPALIVE_S


# --------------------------------------------------------------------------- #
# The add-on's half
# --------------------------------------------------------------------------- #


def test_the_addon_has_a_ping_branch_and_no_longer_calls_the_eviction_a_limitation():
    ts = _src(SOCKET_TS)
    assert re.search(r"case FRAME_PING:[\s\S]{0,300}this\.send\(pongFor\(frame\)\)", ts), (
        "the socket does not answer a ping"
    )
    assert "a real limitation and not a bug to hunt later" not in ts
    assert "KEEPALIVE_S" in ts and "browser.pong" in ts
    generated = _src(PROTOCOL_TS)
    assert 'export const FRAME_PING = "browser.ping";' in generated
    assert 'export const FRAME_PONG = "browser.pong";' in generated
    assert "export const KEEPALIVE_S = 20" in generated, "protocol.ts was not regenerated"


_PONG_HARNESS = """
const FRAME_PONG = "browser.pong";
__FN__
process.stdout.write(JSON.stringify([
  pongFor({ type: "browser.ping", t: 1234 }),
  pongFor({ type: "browser.ping" }).type,
  typeof pongFor({}).t,
]) + "\\n");
"""


@requires_node
def test_pong_for_echoes_the_pings_stamp_and_never_fails_without_one(tmp_path):
    ts = _src(SOCKET_TS)
    start = ts.index("export function pongFor")
    fn = ts[start : ts.index("\n}\n", start) + 3].replace("export function", "function")
    fn = re.sub(r"\): \{ type: string; t: number \}", ")", fn)
    fn = re.sub(r"\(frame: Record<string, unknown>\)", "(frame)", fn)
    fn = fn.replace('(frame["t"] as number)', 'frame["t"]')
    script = _PONG_HARNESS.replace("__FN__", fn)
    f = tmp_path / "pong.js"
    f.write_text(script, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(f)], capture_output=True, text=True, encoding="utf-8", timeout=60, env={**os.environ}
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out[0] == {"type": "browser.pong", "t": 1234}
    assert out[1] == "browser.pong"
    assert out[2] == "number"
