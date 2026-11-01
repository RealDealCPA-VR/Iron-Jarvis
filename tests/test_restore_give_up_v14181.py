"""v1.418.1: a boot restore that never starts is GIVEN UP, never waited on forever.

THE USER BUG (2026-10-31, the installed v1.416.0): Build › Terminal said
"Reopening 3 terminals…" for more than four hours. ``GET /terminals`` listed
no terminal and ``restoring: [3 ids]``; daemon.log had the three "starting
pwsh.exe directly" lines and "3 of 3 terminal panes had not started after
8 s", and none ever opened. The start before it FROZE the whole daemon:
``daemon-freeze.txt`` showed two ``pane-restore`` threads inside
``ctypes.WinDLL`` (the bundled conpty.dll, from ``backend._conpty_api``, which
had no lock) while the boot thread waited in ``threading.Thread.start``.

These pins stand in for Windows with a backend whose ``start`` blocks on an
Event the test controls, and drive time through ``TerminalManager._now`` — no
wall-clock race decides any of them:

* a restore past ``RESTORE_GIVE_UP_S`` leaves ``restoring``, is reported under
  ``restore_failed`` (plain words), releases its slot, and keeps its
  snapshot entry so the next start tries again;
* a shell that starts after the give-up still opens, and the note goes away;
* a Store shell never waits forever behind another one that is stuck
  starting (``_PACKAGED_SPAWN`` with a bound → ``ShellStartStuck``), and a
  restore refused that way is given up with its entry kept;
* ``_conpty_api`` loads ONCE from N threads, and the boot loads it before
  any restore thread starts.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.terminals import TerminalManager
from iron_jarvis.terminals import backend as term_backend
from iron_jarvis.terminals import manager as term_mgr
from iron_jarvis.terminals.backend import FakeBackend, ShellStartStuck

STUCK = "stuck-shell"
BUSY = "busy-shell"


class _Gate:
    def __init__(self) -> None:
        self.release = threading.Event()
        self.entered = threading.Event()


def _backend_factory(gate: _Gate):
    class _MaybeStuck(FakeBackend):
        def start(self, argv, cwd, env, cols, rows):  # noqa: D401 — test double
            if argv and argv[0] == STUCK:
                gate.entered.set()
                gate.release.wait(60)
            if argv and argv[0] == BUSY:
                raise ShellStartStuck("PowerShell is still starting another terminal")
            return super().start(argv, cwd, env, cols, rows)

    return lambda: _MaybeStuck()


def _entries(tmp_path: Path, shells: list[str]) -> list[dict]:
    return [
        {
            "id": f"term_gu_{i}",
            "shell": "sh",
            "argv": [shell],
            "cwd": str(tmp_path),
            "cols": 80,
            "rows": 24,
        }
        for i, shell in enumerate(shells)
    ]


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _manager(tmp_path: Path, monkeypatch, gate: _Gate, shells: list[str], **kw):
    monkeypatch.setattr("iron_jarvis.terminals.session.default_backend", _backend_factory(gate))
    monkeypatch.setattr(term_mgr, "RESTORE_WAIT_S", 0.3)
    sp = tmp_path / "terminals.json"
    entries = _entries(tmp_path, shells)
    sp.write_text(json.dumps({"terminals": entries}), encoding="utf-8")
    m = TerminalManager(state_path=sp, **kw)
    m._pty_ok = True  # the first-spawn verify is not what this pins
    clock = _Clock()
    m._now = clock
    return m, entries, clock


def _saved_ids(path: Path) -> list[str]:
    return [e["id"] for e in json.loads(path.read_text(encoding="utf-8"))["terminals"]]


def _until(cond, timeout: float = 15.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def _past_give_up(clock: _Clock) -> None:
    clock.t += term_mgr.RESTORE_GIVE_UP_S + 1


# --------------------------------------------------------------------------- #
# The give-up                                                                  #
# --------------------------------------------------------------------------- #
def test_a_restore_that_never_starts_is_given_up_after_the_bound(tmp_path, monkeypatch):
    gate = _Gate()
    m, entries, clock = _manager(tmp_path, monkeypatch, gate, ["sh", STUCK, STUCK])
    ok_id, stuck_a, stuck_b = (e["id"] for e in entries)
    try:
        assert m.rehydrate() == 1
        assert gate.entered.is_set()
        # Inside the bound: still reopening, no note.
        clock.t += term_mgr.RESTORE_GIVE_UP_S - 1
        assert m.restoring_ids() == [stuck_a, stuck_b]
        assert m.restore_failed() is None
        assert m._reserved == 2
        # Past it: given up, in plain words, slots released.
        clock.t += 2
        assert m.restoring_ids() == []
        note = m.restore_failed()
        assert note == {
            "count": 2,
            "ids": [stuck_a, stuck_b],
            "reason": "their shell didn't start",
        }
        assert m._reserved == 0
        # The entries are KEPT, so the next start tries again — through every
        # later snapshot, the 30 s one and the shutdown one included.
        m.snapshot()
        assert _saved_ids(m.state_path) == [ok_id, stuck_a, stuck_b]
        m.snapshot_if_changed()
        assert _saved_ids(m.state_path) == [ok_id, stuck_a, stuck_b]
    finally:
        m.kill_all()
        assert _saved_ids(m.state_path) == [ok_id, stuck_a, stuck_b]
        gate.release.set()


def test_one_pane_given_up_reads_its_shell(tmp_path, monkeypatch):
    gate = _Gate()
    m, entries, clock = _manager(tmp_path, monkeypatch, gate, [STUCK])
    try:
        m.rehydrate()
        _past_give_up(clock)
        assert m.restore_failed() == {
            "count": 1,
            "ids": [entries[0]["id"]],
            "reason": "its shell didn't start",
        }
    finally:
        gate.release.set()
        m.kill_all()


def test_the_30_s_snapshot_alone_gives_up_too(tmp_path, monkeypatch):
    """No page open at all: the loop's own snapshot settles the state."""
    gate = _Gate()
    m, entries, clock = _manager(tmp_path, monkeypatch, gate, [STUCK])
    try:
        m.rehydrate()
        _past_give_up(clock)
        m.snapshot_if_changed()
        assert m._restoring == {}
        assert list(m._restore_failed) == [entries[0]["id"]]
        assert m._reserved == 0
    finally:
        gate.release.set()
        m.kill_all()


def test_a_shell_that_starts_after_the_give_up_still_opens_and_the_note_goes(
    tmp_path, monkeypatch
):
    gate = _Gate()
    m, entries, clock = _manager(tmp_path, monkeypatch, gate, [STUCK])
    rid = entries[0]["id"]
    try:
        m.rehydrate()
        _past_give_up(clock)
        assert m.restore_failed()["ids"] == [rid]
        gate.release.set()
        assert _until(lambda: m.get(rid) is not None), "the late pane never registered"
        assert m.get(rid).alive
        assert m.restore_failed() is None
        assert m.restoring_ids() == []
        # Its slot was released at the give-up and is not released twice.
        assert m._reserved == 0
        assert _until(lambda: _saved_ids(m.state_path) == [rid])
    finally:
        gate.release.set()
        m.kill_all()


def test_a_new_terminal_opens_while_a_restore_is_stuck_and_the_cap_frees_at_the_give_up(
    tmp_path, monkeypatch
):
    gate = _Gate()
    m, entries, clock = _manager(tmp_path, monkeypatch, gate, [STUCK], max_sessions=2)
    try:
        m.rehydrate()
        # Room for one more while the stuck one still holds its slot.
        first = m.create(cwd=str(tmp_path), shell="sh")
        assert first.alive
        with pytest.raises(RuntimeError, match="cap"):
            m.create(cwd=str(tmp_path), shell="sh")
        # The give-up releases the stuck pane's slot: a new terminal opens.
        _past_give_up(clock)
        assert m.restore_failed() is not None
        second = m.create(cwd=str(tmp_path), shell="sh")
        assert second.alive
    finally:
        gate.release.set()
        m.kill_all()


def test_a_restore_refused_because_another_shell_is_stuck_is_kept_not_dropped(
    tmp_path, monkeypatch
):
    """Inside the boot's wait: the refused restore is given up at once."""
    gate = _Gate()
    m, entries, clock = _manager(tmp_path, monkeypatch, gate, ["sh", BUSY])
    ok_id, busy_id = (e["id"] for e in entries)
    try:
        assert m.rehydrate() == 1
        assert m.restoring_ids() == []
        assert m.restore_failed() == {
            "count": 1,
            "ids": [busy_id],
            "reason": "its shell didn't start",
        }
        assert m._reserved == 0
        assert _saved_ids(m.state_path) == [ok_id, busy_id]
    finally:
        gate.release.set()
        m.kill_all()


def test_a_late_restore_refused_because_another_shell_is_stuck_is_kept(tmp_path, monkeypatch):
    """After the boot's wait: the late finisher gives it up, entry kept."""
    gate = _Gate()

    class _LateBusy(FakeBackend):
        def start(self, argv, cwd, env, cols, rows):
            gate.entered.set()
            gate.release.wait(60)
            raise ShellStartStuck("PowerShell is still starting another terminal")

    monkeypatch.setattr("iron_jarvis.terminals.session.default_backend", lambda: _LateBusy())
    monkeypatch.setattr(term_mgr, "RESTORE_WAIT_S", 0.3)
    sp = tmp_path / "terminals.json"
    entries = _entries(tmp_path, ["x"])
    sp.write_text(json.dumps({"terminals": entries}), encoding="utf-8")
    m = TerminalManager(state_path=sp)
    m._pty_ok = True
    rid = entries[0]["id"]
    try:
        m.rehydrate()
        assert m.restoring_ids() == [rid]
        gate.release.set()
        assert _until(lambda: m.restore_failed() is not None)
        assert m.restore_failed()["ids"] == [rid]
        assert m.restoring_ids() == []
        assert m._reserved == 0
        m.snapshot()
        assert _saved_ids(sp) == [rid]
    finally:
        gate.release.set()
        m.kill_all()


# --------------------------------------------------------------------------- #
# The routes                                                                   #
# --------------------------------------------------------------------------- #
@pytest.mark.timeout(120)
def test_get_terminals_reports_the_give_up_in_plain_words(tmp_path, monkeypatch):
    from iron_jarvis.daemon.app import create_app

    gate = _Gate()
    monkeypatch.setattr("iron_jarvis.terminals.session.default_backend", _backend_factory(gate))
    monkeypatch.setattr(term_mgr, "RESTORE_WAIT_S", 0.3)
    home = tmp_path / ".ironjarvis"
    home.mkdir()
    entries = _entries(tmp_path, [STUCK])
    (home / "terminals.json").write_text(json.dumps({"terminals": entries}), encoding="utf-8")
    app = create_app(str(tmp_path))
    rid = entries[0]["id"]
    try:
        with TestClient(app) as client:
            assert gate.entered.is_set()
            body = client.get("/terminals").json()
            assert body.get("restoring") == [rid]
            assert "restore_failed" not in body
            terms = app.state.d.platform.terminals
            clock = _Clock()
            clock.t = terms._now() + term_mgr.RESTORE_GIVE_UP_S + 1
            terms._now = clock
            body = client.get("/terminals").json()
            assert "restoring" not in body
            assert body["restore_failed"] == {
                "count": 1,
                "ids": [rid],
                "reason": "its shell didn't start",
            }
            gate.release.set()

            def _opened() -> bool:
                b = client.get("/terminals").json()
                return [t["id"] for t in b["terminals"]] == [rid] and "restore_failed" not in b

            assert _until(_opened), client.get("/terminals").json()
    finally:
        gate.release.set()


def test_a_new_terminal_refused_behind_a_stuck_shell_is_a_503_in_plain_words(
    tmp_path, monkeypatch
):
    from iron_jarvis.daemon.app import create_app

    app = create_app(str(tmp_path))
    words = "PowerShell is still starting another terminal and has not finished after 15 s"
    with TestClient(app) as client:
        terms = app.state.d.platform.terminals

        def _stuck(*a, **kw):
            raise ShellStartStuck(words)

        monkeypatch.setattr(terms, "create", _stuck)
        r = client.post("/terminals", json={"cwd": str(tmp_path)})
        assert r.status_code == 503, r.text
        assert r.json()["detail"] == words


# --------------------------------------------------------------------------- #
# The Store-shell lock is bounded                                              #
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not term_backend.conpty_available(), reason="needs Windows ConPTY")
def test_a_store_shell_never_waits_forever_behind_a_stuck_one(monkeypatch):
    """`_PACKAGED_SPAWN` is held by a start Windows never returned; a new
    PowerShell terminal is refused in plain words after the bound — and no
    process is started for it, and ConPTY is not marked broken."""
    monkeypatch.setattr(term_backend, "PACKAGED_SPAWN_WAIT_S", 0.2)
    spawned: list[object] = []
    monkeypatch.setattr(
        term_backend.ConPtyBackend, "_spawn", lambda self, *a, **k: spawned.append(a)
    )
    fake_store_pwsh = r"C:\Program Files\WindowsApps\Microsoft.PowerShell_7\pwsh.exe"
    assert term_backend._PACKAGED_SPAWN.acquire(timeout=5)
    box: dict[str, BaseException] = {}

    def run() -> None:
        try:
            term_backend.ConPtyBackend().start([fake_store_pwsh], "C:\\", None, 80, 24)
        except BaseException as exc:  # noqa: BLE001
            box["exc"] = exc

    t = threading.Thread(target=run, daemon=True)
    try:
        t.start()
        t.join(10)
        assert not t.is_alive(), "a Store shell waited forever behind a stuck one"
    finally:
        term_backend._PACKAGED_SPAWN.release()
    exc = box.get("exc")
    assert isinstance(exc, ShellStartStuck), exc
    assert not isinstance(exc, OSError)  # never read as a broken ConPTY
    assert str(exc).startswith("PowerShell is still starting another terminal")
    assert spawned == []
    assert term_backend._CONPTY_BROKEN is False
    # The lock is free again for the next start.
    assert term_backend._PACKAGED_SPAWN.acquire(timeout=1)
    term_backend._PACKAGED_SPAWN.release()


# --------------------------------------------------------------------------- #
# The ConPTY DLLs load once, before the restore threads                       #
# --------------------------------------------------------------------------- #
def test_conpty_api_called_from_many_threads_loads_once(monkeypatch):
    monkeypatch.setattr(term_backend, "_CONPTY_API", None)
    entered = threading.Event()
    release = threading.Event()
    builds: list[str] = []

    def _fake_build():
        builds.append(threading.current_thread().name)
        entered.set()
        release.wait(30)
        term_backend._CONPTY_API = object()
        return term_backend._CONPTY_API

    monkeypatch.setattr(term_backend, "_build_conpty_api", _fake_build)
    results: list[object] = []
    threads = [
        threading.Thread(target=lambda: results.append(term_backend._conpty_api()), daemon=True)
        for _ in range(6)
    ]
    threads[0].start()
    assert entered.wait(10)
    for t in threads[1:]:
        t.start()
    # Give an unlocked build every chance to run a second time.
    _until(lambda: len(builds) > 1, timeout=1.0)
    release.set()
    for t in threads:
        t.join(10)
    assert len(builds) == 1, builds
    assert len(results) == 6 and all(r is results[0] for r in results)


def test_the_boot_loads_conpty_before_any_restore_thread_starts(tmp_path, monkeypatch):
    gate = _Gate()
    calls: list[tuple[str, list[str]]] = []

    def _warm() -> bool:
        calls.append(
            (
                threading.current_thread().name,
                [t.name for t in threading.enumerate() if t.name.startswith("pane-restore")],
            )
        )
        return True

    monkeypatch.setattr(term_backend, "warm_conpty", _warm)
    m, entries, clock = _manager(tmp_path, monkeypatch, gate, ["sh", "sh"])
    try:
        assert m.rehydrate() == 2
        assert len(calls) == 1, calls
        thread_name, restore_threads = calls[0]
        assert thread_name == threading.current_thread().name
        assert restore_threads == []
    finally:
        gate.release.set()
        m.kill_all()
