"""v1.406.0 part 6: a pane whose shell never starts must not hold the boot.

THE USER BUG, read off the packaged app's own logs (2026-10-27/28): on 5 of
11 first starts after an update the daemon sat in the lifespan after
"Waiting for application startup" until the desktop killed it at 90 s.
v1.400.4's BootWatch named the step every time it was running:
``rehydrate_terminals``, the boot thread in ``rehydrate`` waiting on the
restore pool, and EVERY ``pane-restore_N`` thread inside
``backend._spawn`` -> ``CreateProcessW`` (the WindowsApps ``pwsh.EXE`` alias
under ConPTY). Windows never returned the call; the restart that followed
started in 2 s.

These pins stand in for that call with a backend whose ``start`` blocks
until the test releases it, and check the boot waits at most
``RESTORE_WAIT_S``, the stuck pane is kept (snapshot + listing), and it opens
the moment its shell starts.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.terminals import TerminalManager
from iron_jarvis.terminals import manager as term_mgr
from iron_jarvis.terminals.backend import FakeBackend

STUCK = "stuck-shell"


class _Gate:
    """Holds every spawn of the STUCK shell until released."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.entered = threading.Event()


def _backend_factory(gate: _Gate):
    class _MaybeStuck(FakeBackend):
        def start(self, argv, cwd, env, cols, rows):  # noqa: D401 — test double
            if argv and argv[0] == STUCK:
                gate.entered.set()
                gate.release.wait(30)
            return super().start(argv, cwd, env, cols, rows)

    return lambda: _MaybeStuck()


def _entries(tmp_path: Path, shells: list[str]) -> list[dict]:
    return [
        {
            "id": f"term_b6_{i}",
            "shell": "sh",
            "argv": [shell],
            "cwd": str(tmp_path),
            "cols": 80,
            "rows": 24,
        }
        for i, shell in enumerate(shells)
    ]


def _manager(tmp_path: Path, monkeypatch, gate: _Gate, shells: list[str]) -> tuple[TerminalManager, list[dict]]:
    monkeypatch.setattr("iron_jarvis.terminals.session.default_backend", _backend_factory(gate))
    monkeypatch.setattr(term_mgr, "RESTORE_WAIT_S", 0.5)
    sp = tmp_path / "terminals.json"
    entries = _entries(tmp_path, shells)
    sp.write_text(json.dumps({"terminals": entries}), encoding="utf-8")
    m = TerminalManager(state_path=sp)
    m._pty_ok = True  # skip the first-spawn liveness verify; not what this pins
    return m, entries


def _saved_ids(path: Path) -> list[str]:
    return [e["id"] for e in json.loads(path.read_text(encoding="utf-8"))["terminals"]]


def _until(cond, timeout: float = 10.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def test_a_stuck_pane_does_not_hold_the_boot_and_opens_when_its_shell_starts(tmp_path, monkeypatch):
    gate = _Gate()
    m, entries = _manager(tmp_path, monkeypatch, gate, ["sh", STUCK])
    stuck_id, ok_id = entries[1]["id"], entries[0]["id"]
    try:
        t0 = time.monotonic()
        restored = m.rehydrate()
        took = time.monotonic() - t0
        assert gate.entered.is_set(), "the stuck spawn never ran"
        # The boot waited the bound, not for the stuck shell.
        assert took < 3.0, f"rehydrate waited {took:.1f} s for a shell that never started"
        assert restored == 1
        assert m.get(ok_id) is not None
        assert m.get(stuck_id) is None
        assert m.restoring_ids() == [stuck_id]
        # The stuck pane is still the user's pane: every snapshot keeps it.
        assert _saved_ids(m.state_path) == [ok_id, stuck_id]
        m.snapshot()
        assert _saved_ids(m.state_path) == [ok_id, stuck_id]
        # Its slot stays reserved while it starts (the cap still counts it).
        assert m._reserved == 1

        gate.release.set()
        assert _until(lambda: m.get(stuck_id) is not None), "the late pane never registered"
        assert _until(lambda: m.restoring_ids() == [])
        assert m._reserved == 0
        assert _until(lambda: sorted(_saved_ids(m.state_path)) == sorted([ok_id, stuck_id]))
        assert m.get(stuck_id).alive
    finally:
        gate.release.set()
        m.kill_all()


def test_one_stuck_pane_alone_is_bounded_too(tmp_path, monkeypatch):
    """A single entry used to restore inline on the boot thread — the same
    CreateProcessW hang with nothing to bound it."""
    gate = _Gate()
    m, entries = _manager(tmp_path, monkeypatch, gate, [STUCK])
    try:
        t0 = time.monotonic()
        assert m.rehydrate() == 0
        assert time.monotonic() - t0 < 3.0
        assert m.restoring_ids() == [entries[0]["id"]]
    finally:
        gate.release.set()
        m.kill_all()


def test_restore_threads_are_daemon_threads_so_a_stuck_spawn_cannot_hang_exit(tmp_path, monkeypatch):
    """concurrent.futures workers are joined at interpreter exit: a pool
    worker stuck in CreateProcessW would hang the daemon's own shutdown."""
    gate = _Gate()
    m, _entries_ = _manager(tmp_path, monkeypatch, gate, [STUCK, STUCK])
    try:
        m.rehydrate()
        stuck = [t for t in threading.enumerate() if t.name.startswith("pane-restore")]
        assert stuck, "no restore thread is still running"
        assert all(t.daemon for t in stuck), [(t.name, t.daemon) for t in stuck]
    finally:
        gate.release.set()
        m.kill_all()


def test_a_pane_that_starts_after_shutdown_began_is_closed_not_registered(tmp_path, monkeypatch):
    gate = _Gate()
    m, entries = _manager(tmp_path, monkeypatch, gate, [STUCK])
    stuck_id = entries[0]["id"]
    m.rehydrate()
    m.kill_all()
    # The shutdown snapshot kept the entry for the next boot.
    assert _saved_ids(m.state_path) == [stuck_id]
    gate.release.set()
    assert _until(lambda: m.restoring_ids() == [])
    assert m.get(stuck_id) is None


def test_a_pane_that_starts_in_time_is_registered_with_the_boot(tmp_path, monkeypatch):
    """Control: nothing stuck = every pane is registered before rehydrate
    returns, exactly as before (one snapshot, snapshot order)."""
    gate = _Gate()
    m, entries = _manager(tmp_path, monkeypatch, gate, ["sh", "sh"])
    try:
        assert m.rehydrate() == 2
        assert m.restoring_ids() == []
        assert [s["id"] for s in m.list()] == [e["id"] for e in entries]
        assert m._reserved == 0
    finally:
        m.kill_all()


@pytest.mark.timeout(120)
def test_the_daemon_finishes_starting_while_a_pane_shell_is_stuck(tmp_path, monkeypatch):
    """The user's bug end to end: the lifespan must not wait on the stuck
    shell, GET /terminals names it as restoring, and it joins the list once
    its shell starts. The gate opens by itself after HOLD_S, so code that
    waits for it shows up as a slow start rather than a hang."""
    from iron_jarvis.daemon.app import create_app

    hold_s = 8.0
    gate = _Gate()
    monkeypatch.setattr("iron_jarvis.terminals.session.default_backend", _backend_factory(gate))
    monkeypatch.setattr(term_mgr, "RESTORE_WAIT_S", 2.0)
    home = tmp_path / ".ironjarvis"
    home.mkdir()
    entries = _entries(tmp_path, ["sh", STUCK])
    (home / "terminals.json").write_text(json.dumps({"terminals": entries}), encoding="utf-8")
    opener = threading.Timer(hold_s, gate.release.set)
    opener.daemon = True
    app = create_app(str(tmp_path))
    try:
        opener.start()
        t0 = time.monotonic()
        with TestClient(app) as client:
            started = time.monotonic() - t0
            assert gate.entered.is_set()
            assert started < hold_s - 2, (
                f"startup took {started:.1f} s: the lifespan waited for the stuck pane shell"
            )
            body = client.get("/terminals").json()
            assert [t["id"] for t in body["terminals"]] == [entries[0]["id"]]
            assert body.get("restoring") == [entries[1]["id"]]
            gate.release.set()

            def _both() -> bool:
                b = client.get("/terminals").json()
                return len(b["terminals"]) == 2 and "restoring" not in b

            assert _until(_both, 15), client.get("/terminals").json()
    finally:
        gate.release.set()
        opener.cancel()


def test_the_listing_is_unchanged_when_nothing_is_restoring(tmp_path, monkeypatch):
    from iron_jarvis.daemon.app import create_app

    gate = _Gate()
    monkeypatch.setattr("iron_jarvis.terminals.session.default_backend", _backend_factory(gate))
    with TestClient(create_app(str(tmp_path))) as client:
        assert client.get("/terminals").json() == {"terminals": []}


# --------------------------------------------------------------------------- #
# The first-spawn verify ends when the console has spoken (v1.406.0 part 6)   #
# --------------------------------------------------------------------------- #
class _Pushing(FakeBackend):
    """Pushes output like the raw ConPTY's reader thread."""

    pushes_output = True
    speaks = True

    def __init__(self) -> None:
        super().__init__()
        self._on_output = None

    def set_output_handler(self, on_output, on_eof=None) -> None:
        self._on_output = on_output

    def start(self, argv, cwd, env, cols, rows):
        super().start(argv, cwd, env, cols, rows)
        if self.speaks and self._on_output is not None:
            threading.Thread(target=self._on_output, args=(b"\x1b[1t\x1b[c",), daemon=True).start()

    def read_nonblocking(self, max_bytes: int = 65536) -> bytes:
        return b""


class _Silent(_Pushing):
    speaks = False


def _timed_spawn(tmp_path, monkeypatch, backend_cls) -> tuple[float, TerminalManager]:
    monkeypatch.setattr("iron_jarvis.terminals.session.default_backend", lambda: backend_cls())
    m = TerminalManager(state_path=None)
    t0 = time.monotonic()
    s = m._spawn(str(tmp_path), "sh", ["sh"], 80, 24, None, None)
    took = time.monotonic() - t0
    s.kill()
    return took, m


def test_a_console_that_has_spoken_is_trusted_without_the_full_wait(tmp_path, monkeypatch):
    took, m = _timed_spawn(tmp_path, monkeypatch, _Pushing)
    assert m._pty_ok is True
    assert took < term_mgr._PTY_VERIFY_SECONDS / 2, f"verify still waited {took:.2f} s"


def test_a_silent_pushing_console_still_gets_the_full_wait(tmp_path, monkeypatch):
    took, m = _timed_spawn(tmp_path, monkeypatch, _Silent)
    assert m._pty_ok is True
    assert took >= term_mgr._PTY_VERIFY_SECONDS * 0.9


def test_a_polled_backend_still_gets_the_full_wait(tmp_path, monkeypatch):
    took, m = _timed_spawn(tmp_path, monkeypatch, FakeBackend)
    assert m._pty_ok is True
    assert took >= term_mgr._PTY_VERIFY_SECONDS * 0.9


def test_a_shell_that_dies_still_falls_back_to_the_pipe_shell(tmp_path, monkeypatch):
    class _Dies(_Silent):
        def start(self, argv, cwd, env, cols, rows):
            super().start(argv, cwd, env, cols, rows)
            threading.Timer(0.1, self.kill).start()

    monkeypatch.setattr("iron_jarvis.terminals.session.default_backend", lambda: _Dies())
    m = TerminalManager(state_path=None)
    s = m._spawn(str(tmp_path), "sh", ["sh"], 80, 24, None, None)
    try:
        assert m._pty_ok is False
        assert getattr(s, "degraded", False) is True
    finally:
        s.kill()
