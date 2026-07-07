"""Offline tests for the terminal session backend (FakeBackend only).

These NEVER spawn a real shell: every session is driven through an injected
:class:`FakeBackend`, and the platform-pickers are checked for shape only.
"""

from __future__ import annotations

import sys

import pytest

from iron_jarvis.terminals import (
    FakeBackend,
    PtyBackend,
    TerminalManager,
    TerminalSession,
    available_shells,
    default_backend,
    default_shell,
)
from iron_jarvis.terminals.backend import (
    PipeBackend,
    PosixPtyBackend,
    WinPtyBackend,
)


def _fake_session(**kw) -> TerminalSession:
    s = TerminalSession(cwd="/work", shell="fake", argv=["fake"], backend=FakeBackend(), **kw)
    return s.start()


def test_write_then_read_echoes_line():
    s = _fake_session()
    s.write("hello\n")
    assert s.read() == b"hello\n"
    # nothing left to read
    assert s.read() == b""


def test_partial_line_is_buffered_until_newline():
    s = _fake_session()
    s.write("par")
    assert s.read() == b""  # no newline yet -> nothing flushed
    s.write("tial\n")
    assert s.read() == b"partial\n"


def test_write_accepts_bytes_and_str():
    s = _fake_session()
    s.write(b"raw\n")
    assert s.read() == b"raw\n"


def test_resize_does_not_raise():
    s = _fake_session()
    s.resize(120, 40)  # must not raise
    assert s.cols == 120
    assert s.rows == 40


def test_info_shape():
    s = _fake_session()
    info = s.info()
    for key in ("id", "cwd", "shell", "alive", "created_at"):
        assert key in info
    assert info["id"].startswith("term_")
    assert info["cwd"] == "/work"
    assert info["shell"] == "fake"
    assert info["alive"] is True
    # created_at is an ISO-8601 string
    assert isinstance(info["created_at"], str) and "T" in info["created_at"]


def test_kill_marks_not_alive():
    s = _fake_session()
    assert s.alive is True
    s.kill()
    assert s.alive is False
    assert s.info()["alive"] is False


def test_manager_create_get_list_kill_roundtrip():
    m = TerminalManager()
    s = m.create(cwd="/here", backend=FakeBackend())
    assert m.get(s.id) is s
    listed = m.list()
    assert any(i["id"] == s.id for i in listed)
    assert s.alive is True
    assert m.kill(s.id) is True
    assert s.alive is False
    # killing an unknown id is a clean False
    assert m.kill("term_does_not_exist") is False


def test_manager_resolves_default_cwd_and_shell():
    m = TerminalManager()
    s = m.create(backend=FakeBackend())
    assert s.cwd  # resolved to the user's home
    assert s.shell  # resolved to a concrete shell name
    assert s.argv  # with a runnable argv


def test_session_cap_is_enforced():
    m = TerminalManager(max_sessions=3)
    for _ in range(3):
        m.create(backend=FakeBackend())
    with pytest.raises(RuntimeError):
        m.create(backend=FakeBackend())
    # killing one frees a slot back up
    first = m.list()[0]["id"]
    m.kill(first)
    m.create(backend=FakeBackend())  # must not raise


def test_default_cap_is_twenty():
    assert TerminalManager().max_sessions == 20


def test_kill_all():
    m = TerminalManager()
    a = m.create(backend=FakeBackend())
    b = m.create(backend=FakeBackend())
    m.kill_all()
    assert a.alive is False
    assert b.alive is False


def test_available_shells_sane_for_this_os():
    shells = available_shells()
    assert shells, "expected at least one shell"
    for sh in shells:
        assert "name" in sh and "argv" in sh
        assert isinstance(sh["argv"], list) and sh["argv"]
    if sys.platform == "win32":
        names = {s["name"] for s in shells}
        assert "powershell" in names
        assert "cmd" in names


def test_default_shell_sane():
    d = default_shell()
    assert isinstance(d, dict)
    assert d["name"] and d["argv"]


def test_default_backend_picks_without_spawning():
    b = default_backend()
    # No process is started: nothing has an exit code yet, and it is not alive.
    assert b.exit_code is None
    assert b.is_alive() is False
    # The required surface is present (Protocol membership).
    assert isinstance(b, PtyBackend)
    for method in ("start", "write", "read_nonblocking", "resize", "kill"):
        assert callable(getattr(b, method))
    if sys.platform == "win32":
        assert isinstance(b, (WinPtyBackend, PipeBackend))
    else:
        assert isinstance(b, (PosixPtyBackend, PipeBackend))


def test_fake_backend_read_respects_max_bytes():
    s = _fake_session()
    s.write("abcdef\n")
    assert s.read(max_bytes=3) == b"abc"
    assert s.read(max_bytes=3) == b"def"
    assert s.read() == b"\n"


# --- background auto-drain (Creative Studio has NO WebSocket to pump reads) ------


def test_autodrain_captures_output_with_no_reader():
    """The Studio drives the CLI over HTTP with no pane attached, so nothing
    calls read(). start_autodrain() must keep the PTY drained on its own —
    without it the tail stays blank, auto-mode detection is blind, and a
    full-screen TUI stalls once its output buffer fills."""
    import time

    s = _fake_session()
    s.start_autodrain()
    try:
        s.write("hello from the cli\n")  # no manual read() anywhere
        deadline = time.monotonic() + 2.0
        while "hello from the cli" not in s.output_tail() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert "hello from the cli" in s.output_tail()
    finally:
        s.kill()


def test_autodrain_yields_to_an_attached_consumer():
    """While a live Build pane is attached it does the reading; the background
    drain must step aside so the two never race for the same bytes."""
    import time

    s = _fake_session()
    s.add_consumer()  # a Build-page pane is attached BEFORE the drain starts
    s.start_autodrain()
    try:
        s.write("owned by the pane\n")
        time.sleep(0.3)  # generous window for the drain to (wrongly) grab it
        assert "owned by the pane" not in s.output_tail()  # it yielded
        assert s.read() == b"owned by the pane\n"  # the pane's read gets them
    finally:
        s.remove_consumer()
        s.kill()


def test_autodrain_stops_on_kill():
    """kill() must halt the background reader (no thread left spinning)."""
    import time

    s = _fake_session()
    s.start_autodrain()
    s.kill()
    time.sleep(0.1)
    assert s._drain_thread is not None
    assert not s._drain_thread.is_alive()


# --- dead-PTY -> pipe fallback (frozen build missing the ConPTY host exe) -------


class _DyingBackend(FakeBackend):
    """Spawns but reports dead immediately — the ConPTY-host-missing failure."""

    def is_alive(self) -> bool:
        return False


def test_dead_pty_falls_back_to_pipe_and_caches(monkeypatch):
    import iron_jarvis.terminals.manager as mgr
    import iron_jarvis.terminals.session as sess

    monkeypatch.setattr(mgr, "_PTY_VERIFY_SECONDS", 0.1)
    monkeypatch.setattr(sess, "default_backend", lambda: _DyingBackend())
    # The pipe fallback path must NOT spawn a real process in the test.
    monkeypatch.setattr(mgr, "PipeBackend", FakeBackend)

    m = TerminalManager()
    s = m.create()  # backend=None -> verify -> dead -> pipe fallback
    assert s.degraded is True
    assert m._pty_ok is False
    assert s.info()["degraded"] is True

    # The verdict is cached: the next create goes straight to pipe (no re-verify).
    s2 = m.create()
    assert s2.degraded is True


def test_healthy_pty_verified_once_no_degrade(monkeypatch):
    import iron_jarvis.terminals.manager as mgr
    import iron_jarvis.terminals.session as sess

    monkeypatch.setattr(mgr, "_PTY_VERIFY_SECONDS", 0.15)
    monkeypatch.setattr(sess, "default_backend", lambda: FakeBackend())  # stays alive

    m = TerminalManager()
    s = m.create()
    assert s.degraded is False
    assert m._pty_ok is True
    assert s.info()["degraded"] is False
