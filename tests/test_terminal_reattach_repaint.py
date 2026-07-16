"""Re-attach rendering fixes (live-hit 2026-07-16: a Build pane re-attached to
a mid-flight Grok TUI came back "blocky").

Scrollback replay cannot reconstruct a full-screen app's frame — a TUI paints
only CHANGED cells and its screen-setup sequences roll out of the tail — so
the WS route nudges the app into a FULL repaint with a one-row resize wiggle
on each attach. Truncated tails are also served from a safe byte boundary
(never mid escape sequence / mid UTF-8 code point). FakeBackend only."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.terminals.backend import FakeBackend
from iron_jarvis.terminals.manager import _SNAPSHOT_SCROLLBACK, TerminalManager
from iron_jarvis.terminals.session import (
    TAIL_MAX_BYTES,
    TerminalSession,
    _safe_replay_start,
)


class RecordingBackend(FakeBackend):
    """FakeBackend that records every resize call, in order."""

    def __init__(self):
        super().__init__()
        self.resizes: list[tuple[int, int]] = []

    def resize(self, cols: int, rows: int) -> None:
        self.resizes.append((cols, rows))
        super().resize(cols, rows)


def _app_with_recording_backends(tmp_path, monkeypatch):
    backends: list[RecordingBackend] = []

    def make():
        b = RecordingBackend()
        backends.append(b)
        return b

    monkeypatch.setattr("iron_jarvis.terminals.session.default_backend", make)
    return TestClient(create_app(str(tmp_path))), backends


def _await_echo(ws, line: str) -> None:
    """Type a line and wait for its echo — WS messages are processed in order,
    so seeing the echo proves every earlier message (e.g. a resize) landed."""
    ws.send_text(line + "\n")
    got = b""
    for _ in range(100):
        got += ws.receive_bytes()
        if line.encode() in got:
            return
    raise AssertionError(f"echo of {line!r} never arrived (got {got!r})")


# --- the repaint wiggle --------------------------------------------------------


def test_first_resize_of_an_attach_wiggles_for_a_full_repaint(tmp_path, monkeypatch):
    client, backends = _app_with_recording_backends(tmp_path, monkeypatch)
    tid = client.post("/terminals", json={}).json()["id"]

    with client.websocket_connect(f"/terminals/{tid}/ws") as ws:
        ws.send_text(json.dumps({"type": "resize", "cols": 100, "rows": 30}))
        _await_echo(ws, "ping")
        b = backends[0]
        # First resize = the wiggle pair: one row short, then the real size.
        assert b.resizes[:2] == [(100, 29), (100, 30)]

        # A LATER user resize repaints on its own — no second wiggle.
        ws.send_text(json.dumps({"type": "resize", "cols": 110, "rows": 31}))
        _await_echo(ws, "pong")
        assert b.resizes[2:] == [(110, 31)]

    # The session ends up at the size the pane asked for.
    term = {t["id"]: t for t in client.get("/terminals").json()["terminals"]}[tid]
    assert (term["cols"], term["rows"]) == (110, 31)


def test_every_reattach_rearms_the_wiggle(tmp_path, monkeypatch):
    client, backends = _app_with_recording_backends(tmp_path, monkeypatch)
    tid = client.post("/terminals", json={}).json()["id"]

    with client.websocket_connect(f"/terminals/{tid}/ws") as ws:
        ws.send_text(json.dumps({"type": "resize", "cols": 80, "rows": 24}))
        _await_echo(ws, "one")
    with client.websocket_connect(f"/terminals/{tid}/ws") as ws:
        ws.send_text(json.dumps({"type": "resize", "cols": 80, "rows": 24}))
        _await_echo(ws, "two")

    # Two attaches -> two wiggle pairs, even at an UNCHANGED size (a same-size
    # resize alone would be a ConPTY no-op and repaint nothing).
    assert backends[0].resizes == [(80, 23), (80, 24), (80, 23), (80, 24)]


def test_single_row_pane_skips_the_wiggle(tmp_path, monkeypatch):
    client, backends = _app_with_recording_backends(tmp_path, monkeypatch)
    tid = client.post("/terminals", json={}).json()["id"]
    with client.websocket_connect(f"/terminals/{tid}/ws") as ws:
        ws.send_text(json.dumps({"type": "resize", "cols": 80, "rows": 1}))
        _await_echo(ws, "tiny")
    assert backends[0].resizes == [(80, 1)]  # rows-1 would be 0 — just resize


# --- safe replay boundary ------------------------------------------------------


def test_safe_replay_start_boundaries():
    # Leading UTF-8 continuation bytes are skipped, then ESC is the anchor.
    assert _safe_replay_start(b"\xb0\x81garbage\x1b[31mRED") == 9
    # No ESC in sight: resume just past the first newline.
    assert _safe_replay_start(b"cut line tail\nnext") == 14
    # The EARLIER anchor wins (ESC before the newline).
    assert _safe_replay_start(b"x\x1b[2Jab\ncd") == 1
    # No boundary at all: keep everything.
    assert _safe_replay_start(b"noboundaryatall") == 0
    assert _safe_replay_start(b"") == 0


def test_truncated_tail_replays_from_safe_boundary_untruncated_verbatim():
    s = TerminalSession(shell="fake", argv=["fake"], backend=FakeBackend()).start()
    s._tail = bytearray(b"\x9fmid-sequence junk\nPS C:\\> _")
    s._tail_truncated = True
    assert s.scrollback_bytes() == b"PS C:\\> _"
    # The SAME bytes untruncated are history from byte 0 — served verbatim.
    s._tail_truncated = False
    assert s.scrollback_bytes() == b"\x9fmid-sequence junk\nPS C:\\> _"


def test_tail_cap_trim_marks_the_buffer_truncated():
    s = TerminalSession(shell="fake", argv=["fake"], backend=FakeBackend()).start()
    assert s._tail_truncated is False
    s._tail = bytearray(b"x" * TAIL_MAX_BYTES)
    s.write("overflow\n")
    s.read()  # appends the echo, trims the head back to the cap
    assert s._tail_truncated is True
    assert len(s._tail) == TAIL_MAX_BYTES


def test_restore_flags_a_cap_filling_snapshot_as_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "iron_jarvis.terminals.session.default_backend", lambda: FakeBackend()
    )
    sp = tmp_path / "terminals.json"
    m1 = TerminalManager(state_path=sp)
    s = m1.create(cwd=str(tmp_path), backend=FakeBackend())
    # Enough history to fill the snapshot slice — the stored blob was CUT.
    s._tail = bytearray(b"y" * (_SNAPSHOT_SCROLLBACK * 2))
    m1.snapshot()

    m2 = TerminalManager(state_path=sp)
    assert m2.rehydrate() == 1
    restored = m2.get(s.id)
    assert restored is not None
    assert restored._tail_truncated is True
    assert len(restored._tail) == _SNAPSHOT_SCROLLBACK

    # A small (never-sliced) snapshot replays verbatim from byte 0.
    sp2 = tmp_path / "t2.json"
    m3 = TerminalManager(state_path=sp2)
    small = m3.create(cwd=str(tmp_path), backend=FakeBackend())
    small._tail = bytearray(b"short history\n")
    m3.snapshot()
    m4 = TerminalManager(state_path=sp2)
    assert m4.rehydrate() == 1
    assert m4.get(small.id)._tail_truncated is False
