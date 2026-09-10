"""v1.243.0 — a pane's output stream is WHOLE whoever reads it, the replay has
an end, and a PTY nobody watches keeps flowing.

The user's report: leave Build, come back, and the terminals "disconnect or
show a strange string of characters" and take a while to come back to what
they were working on. The dashboard half keeps each pane's terminal and socket
alive across the page (dashboard/components/terminal/paneHost.ts). This file
pins the daemon half:

- ``read()`` hands every chunk to EVERY attached pane (``OutputSubscription``).
  A bare read used to hand each chunk to exactly one caller, so two attaches
  split the stream, and the background drain (a thread) could take the chunk
  in flight at the instant a pane attached. The pane then rendered the rest of
  a torn escape sequence as text.
- The replay snapshot and the live share are taken under one lock: nothing is
  lost or doubled between them.
- The replay ends with ONE EMPTY binary frame, so the pane knows exactly when
  xterm's answers to replayed queries stop being stale (it used to guess
  800 ms, and a colour-query answer leaked into the shell as typed text).
- The last pane out starts the background drain: a PTY nobody reads fills its
  pipe and the program in it BLOCKS — a Claude left working simply stopped
  while the user was on another page.
FakeBackend only.
"""

from __future__ import annotations

import threading
import time

import anyio
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.terminals.backend import FakeBackend
from iron_jarvis.terminals.session import TerminalSession


def _session() -> TerminalSession:
    return TerminalSession(shell="fake", argv=["fake"], backend=FakeBackend()).start()


def _app(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr("iron_jarvis.terminals.session.default_backend", FakeBackend)
    return TestClient(create_app(str(tmp_path)))


def _recv(ws, timeout: float = 5.0) -> bytes:
    """``ws.receive_bytes()`` with a deadline. The test client's own receive
    blocks forever, so a pane that never gets its chunk — the exact defect
    under test — would HANG the suite instead of failing it."""

    async def _get():
        with anyio.fail_after(timeout):
            return await ws._send_rx.receive()

    message = ws.portal.call(_get)
    ws._raise_on_close(message)
    return message["bytes"]


def _until(ws, needle: bytes, frames: int = 100) -> bytes:
    got = b""
    for _ in range(frames):
        got += _recv(ws)
        if needle in got:
            return got
    raise AssertionError(f"{needle!r} never arrived (got {got!r})")


# --- fan-out -------------------------------------------------------------------


def test_one_read_reaches_every_attached_pane_once():
    s = _session()
    try:
        _, a = s.subscribe()
        _, b = s.subscribe()
        s.write("one\n")
        assert s.read() == b"one\n"  # ONE read...
        assert a.take() == b"one\n"  # ...reaches both panes
        assert b.take() == b"one\n"
        assert a.take() == b""  # and each only once
    finally:
        s.kill()


def test_a_read_on_another_thread_still_reaches_the_pane():
    """The background drain reads on its own thread. Its read used to be a
    steal: the chunk went into the tail and never to the pane."""
    s = _session()
    try:
        _, sub = s.subscribe()
        s.write("read by the drain\n")
        t = threading.Thread(target=s.read)
        t.start()
        t.join()
        assert sub.take() == b"read by the drain\n"
    finally:
        s.kill()


def test_the_replay_and_the_live_share_neither_overlap_nor_gap():
    s = _session()
    try:
        s.write("before\n")
        s.read()
        history, sub = s.subscribe()
        assert history == b"before\n"
        s.write("after\n")
        s.read()
        assert sub.take() == b"after\n"  # not "before" again, and not missing
    finally:
        s.kill()


def test_a_pane_that_falls_too_far_behind_is_cut_loose_not_buffered_forever():
    s = _session()
    try:
        _, slow = s.subscribe(limit=16)
        _, fast = s.subscribe()
        s.write("x" * 40 + "\n")
        s.read()
        assert slow.overflowed is True
        assert slow.take() == b""  # its backlog was dropped, not kept
        assert fast.take() == b"x" * 40 + b"\n"  # the other pane is untouched
    finally:
        s.kill()


def test_unsubscribe_is_idempotent_and_keeps_the_count_balanced():
    s = _session()
    try:
        _, a = s.subscribe()
        _, b = s.subscribe()
        s.unsubscribe(a)
        s.unsubscribe(a)  # a second detach of the same pane changes nothing
        assert s.has_consumer is True  # b is still attached
        s.unsubscribe(b)
        assert s.has_consumer is False
    finally:
        s.kill()


# --- nobody watching -----------------------------------------------------------


def test_the_last_pane_out_starts_the_drain_so_the_program_never_blocks():
    s = _session()
    try:
        _, sub = s.subscribe()
        assert s._drain_thread is None  # a watched pane needs no drain
        s.unsubscribe(sub)
        assert s._drain_thread is not None and s._drain_thread.is_alive()
        s.write("printed while nobody watched\n")  # nobody calls read()
        deadline = time.monotonic() + 2.0
        while (
            "printed while nobody watched" not in s.output_tail()
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        assert "printed while nobody watched" in s.output_tail()
        assert sub.take() == b""  # a detached pane is not fed
    finally:
        s.kill()


def test_a_pane_that_is_still_attached_keeps_the_drain_off():
    s = _session()
    try:
        _, a = s.subscribe()
        _, b = s.subscribe()
        s.unsubscribe(a)
        assert s._drain_thread is None  # b is still reading
        s.unsubscribe(b)
        assert s._drain_thread is not None
    finally:
        s.kill()


def test_a_dead_session_starts_no_drain_on_detach():
    s = _session()
    _, sub = s.subscribe()
    s.kill()
    s.unsubscribe(sub)
    assert s._drain_thread is None


# --- the route -----------------------------------------------------------------


def test_the_replay_ends_with_one_empty_frame(tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch)
    tid = client.post("/terminals", json={}).json()["id"]

    with client.websocket_connect(f"/terminals/{tid}/ws") as ws:
        # A fresh pane has no history: its first frame IS the end marker.
        assert _recv(ws) == b""
        ws.send_text("hello-replay\n")
        _until(ws, b"hello-replay")

    with client.websocket_connect(f"/terminals/{tid}/ws") as ws:
        history = _recv(ws)
        assert b"hello-replay" in history  # the replay first...
        assert _recv(ws) == b""  # ...then the marker, before any live output

    client.delete(f"/terminals/{tid}")


def test_two_attaches_both_see_everything(tmp_path, monkeypatch):
    """A phone and the desktop, or a reload racing its own close. Each pump
    used to win only the reads IT made, so each pane got part of the stream."""
    client = _app(tmp_path, monkeypatch)
    tid = client.post("/terminals", json={}).json()["id"]

    with client.websocket_connect(f"/terminals/{tid}/ws") as a:
        with client.websocket_connect(f"/terminals/{tid}/ws") as b:
            assert _recv(a) == b""
            assert _recv(b) == b""
            for i in range(5):
                a.send_text(f"line-{i}-seen-by-both\n")
            for ws in (a, b):
                got = _until(ws, b"line-4-seen-by-both")
                for i in range(5):
                    assert f"line-{i}-seen-by-both".encode() in got

    client.delete(f"/terminals/{tid}")


def test_detaching_the_last_socket_hands_the_pty_to_the_drain(tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch)
    tid = client.post("/terminals", json={}).json()["id"]
    session = client.app.state.platform.terminals.get(tid)

    with client.websocket_connect(f"/terminals/{tid}/ws") as ws:
        assert _recv(ws) == b""
        assert session._drain_thread is None

    deadline = time.monotonic() + 2.0
    while session.has_consumer and time.monotonic() < deadline:
        time.sleep(0.02)  # the route's finally runs on the server's loop
    assert session._drain_thread is not None
    session.write("after the page closed\n")
    while "after the page closed" not in session.output_tail() and time.monotonic() < deadline + 2:
        time.sleep(0.02)
    assert "after the page closed" in session.output_tail()

    client.delete(f"/terminals/{tid}")
