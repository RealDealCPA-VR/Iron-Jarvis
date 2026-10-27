"""v1.333.0 (Lane B) — the Build module sustains a multi-day workload.

B1, THE USER'S OVERNIGHT BUG ("nonsense typed into a pane"). Claude Code
turned on any-motion SGR mouse tracking and died without turning it off;
xterm kept sending ``ESC[<35;x;yM`` per cell the pointer crossed and
PowerShell printed it. Three cures (terminals/modes.py): a RESTORED pane's
replay ends with the modes switched off; a mouse report typed into a Windows
shell with nothing running in it is dropped; and when the program that turned
tracking on is gone, the pane is told to stop.

B2, THE USER'S DECISION ("Stop them"): closing a pane stops the HIDDEN
background processes it started, never a process with a visible window
(terminals/pane_jobs.py). A daemon crash leaves a record the next boot acts on.

B3: a pane an AGENT opens runs below normal priority; the user's stay Normal.

B5: one busy pane beside five quiet ones writes one scrollback file per
snapshot tick, not all six.

B6: one loop callback per burst of output, pane input written off the loop
and in order, the carried conversation read incrementally, the sign-in watch
reading the tail only after the pane printed.

Route tests drive the REAL app factory; the Windows tests spawn real ConPTY
shells in a temp folder (never the user's panes or ports).
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes.terminals import coalesced_waker
from iron_jarvis.terminals import backend as B
from iron_jarvis.terminals import modes as M
from iron_jarvis.terminals import pane_jobs as J
from iron_jarvis.terminals import session as S
from iron_jarvis.terminals.backend import FakeBackend
from iron_jarvis.terminals.manager import TerminalManager
from iron_jarvis.terminals.resume_failed import FileWatch, check_file
from iron_jarvis.terminals.session import TerminalSession

real_conpty = pytest.mark.skipif(
    sys.platform != "win32" or not B.conpty_available(),
    reason="needs a real Windows ConPTY",
)

ON = b"\x1b[?1000h\x1b[?1002h\x1b[?1003h\x1b[?1006h\x1b[?2004h"
REPORT = "\x1b[<35;12;5M"


def _wait(pred, timeout: float = 30.0, step: float = 0.05) -> bool:
    """A LIVENESS bound (never a speed claim): poll until ``pred`` holds."""
    deadline = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > deadline:
            return False
        time.sleep(step)
    return True


class PidBackend(FakeBackend):
    """A FakeBackend with a process id, recording every write."""

    def __init__(self) -> None:
        super().__init__()
        self.pid = 4242
        self.writes: list = []

    def write(self, data) -> None:
        self.writes.append(data)
        super().write(data)


def _pwsh_session(**kw) -> TerminalSession:
    return TerminalSession(
        shell="pwsh", argv=[r"C:\Program Files\PowerShell\7\pwsh.exe", "-NoLogo"],
        backend=PidBackend(), **kw,
    ).start()


class _Proc:
    """psutil.Process stand-in: a fixed list of children, counted."""

    calls = 0
    kids: list = []

    def __init__(self, pid) -> None:
        self.pid = pid

    def children(self):
        _Proc.calls += 1
        return list(_Proc.kids)


@pytest.fixture
def fake_psutil(monkeypatch):
    import psutil

    _Proc.calls = 0
    _Proc.kids = []
    monkeypatch.setattr(psutil, "Process", _Proc)
    # No console facts: every child counts (a real pid 4242 on this PC must
    # not decide a unit test). The console tests below set their own.
    monkeypatch.setattr(J, "console_host_pid", lambda pid: 0)
    return _Proc


# =========================================================================== #
# B1 — modes a dead program leaves on                                         #
# =========================================================================== #


def test_the_tracker_follows_switches_split_across_two_reads():
    t = M.ModeTracker()
    t.feed(b"hello \x1b[?100")
    assert not t.mouse_on
    t.feed(b"3h world")  # the other half of ESC[?1003h
    assert t.mouse_on
    t.feed(b"\x1b[?1003l")
    assert not t.mouse_on


def test_the_tracker_reads_several_modes_in_one_switch_and_a_full_reset():
    t = M.ModeTracker()
    t.feed(b"\x1b[?1000;1006;2004h")
    assert t.tracking == {1000} and t.paste is True
    t.feed(b"\x1b[?1049h")
    assert t.alt is True
    t.feed(b"\x1bc")  # RIS
    assert not t.mouse_on and t.paste is False and t.alt is False


def test_restore_reset_leaves_the_alternate_screen_only_when_the_tail_ended_on_it():
    # v1.337.2: every reset now ends on a fresh page for the new shell.
    page = M.fresh_page(24)
    plain = M.restore_reset(b"$ ls\r\n" + ON)
    assert plain == M.MODE_RESET + b"\x1b[?25h" + page
    alt = M.restore_reset(b"\x1b[?1049h full screen app")
    assert alt.endswith(b"\x1b[?1049l" + page)
    left = M.restore_reset(b"\x1b[?1049h app \x1b[?1049l$ ")
    assert b"\x1b[?1049l" not in left


def test_a_restored_pane_replays_the_dead_programs_tail_then_switches_its_modes_off(tmp_path):
    sp = tmp_path / "terminals.json"
    m1 = TerminalManager(state_path=sp)
    s = m1.create(cwd=str(tmp_path), backend=FakeBackend())
    s._tail = bytearray(b"claude was here " + ON + b" spinner")
    m1.snapshot()

    m2 = TerminalManager(state_path=sp)
    assert m2.rehydrate(backend=FakeBackend()) == 1
    replay = m2.get(s.id).scrollback_bytes()
    old = b"claude was here " + ON + b" spinner"
    assert replay.startswith(old)
    # The switches come AFTER the old program's last byte: replayed into a
    # fresh xterm, they leave it with mouse tracking and bracketed paste off.
    assert replay[len(old):] == M.MODE_RESET + b"\x1b[?25h" + M.fresh_page(24)  # v1.337.2
    after = M.ModeTracker()
    after.feed(replay)
    assert not after.mouse_on and after.paste is False


def test_a_mouse_report_typed_into_an_idle_windows_shell_is_dropped_and_the_pane_told_to_stop(
    fake_psutil,
):
    s = _pwsh_session()
    _, sub = s.subscribe()
    for _ in range(5):
        s.write_input(REPORT)
    s.write_input("ls" + REPORT + "\r")
    # Nothing that looks like a mouse report reached the shell; the typed
    # text around one did.
    typed = "".join(w if isinstance(w, str) else w.decode() for w in s.backend.writes)
    assert "\x1b[<" not in typed
    assert typed == "ls\r"
    # The pane heard MODE_RESET ONCE, however many reports were in flight.
    got = sub.take()
    assert got.count(M.MODE_RESET) == 1
    assert bytes(s._tail).count(M.MODE_RESET) == 1  # and a later replay ends with it


def test_a_mouse_report_reaches_a_shell_that_runs_a_program(fake_psutil):
    fake_psutil.kids = [object()]  # claude is running in the shell
    s = _pwsh_session()
    s.write_input(REPORT)
    assert s.backend.writes == [REPORT]
    assert M.MODE_RESET not in bytes(s._tail)


def test_a_wsl_or_ssh_pane_never_has_its_mouse_reports_dropped(fake_psutil):
    for argv in (["wsl.exe"], ["ssh", "box"], ["bash"]):
        s = TerminalSession(shell="x", argv=argv, backend=PidBackend()).start()
        s.write_input(REPORT)
        assert s.backend.writes == [REPORT], argv
    assert fake_psutil.calls == 0  # not even asked


def test_the_child_check_reads_the_process_table_at_most_once_a_second(fake_psutil, monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(S.time, "monotonic", lambda: clock["t"])
    s = _pwsh_session()
    for _ in range(50):
        s.write_input(REPORT)
        clock["t"] += 0.01
    assert fake_psutil.calls == 1
    clock["t"] += S.CHILD_CHECK_TTL_S
    s.write_input(REPORT)
    assert fake_psutil.calls == 2
    s.write_input("plain typing\r")  # no report, no question asked
    assert fake_psutil.calls == 2


def test_the_ws_input_path_drops_a_report_for_an_idle_shell_and_writes_off_the_loop(
    tmp_path, monkeypatch
):
    """The REAL app factory: a report typed through the socket never reaches
    the shell, the pane receives MODE_RESET, and every write ran on a worker
    thread (no running loop), in the order it was sent."""
    seen: list = []

    class Recording(FakeBackend):
        pid = 4242

        def write(self, data) -> None:
            try:
                asyncio.get_running_loop()
                on_loop = True
            except RuntimeError:
                on_loop = False
            seen.append((data, on_loop))
            super().write(data)

    monkeypatch.setattr("iron_jarvis.terminals.session.default_backend", Recording)
    monkeypatch.setattr(TerminalSession, "_shell_is_idle", lambda self: True)
    client = TestClient(create_app(str(tmp_path)))
    tid = client.post("/terminals", json={"cwd": str(tmp_path)}).json()["id"]
    got = b""
    with client.websocket_connect(f"/terminals/{tid}/ws") as ws:
        assert ws.receive_bytes() == b""  # the end-of-replay frame
        ws.send_text(REPORT)
        for i in range(20):
            ws.send_text(f"k{i}\n")
        while b"k19" not in got:
            got += ws.receive_bytes()
    assert M.MODE_RESET in got
    typed = [d for d, _ in seen]
    assert REPORT not in typed
    assert typed == [f"k{i}\n" for i in range(20)]
    assert not any(on_loop for _, on_loop in seen)


def test_the_mouse_watcher_switches_tracking_off_when_the_program_that_asked_dies(
    fake_psutil, monkeypatch
):
    monkeypatch.setattr(S, "MOUSE_WATCH_GRACE_S", 0.0)

    class Kid:
        def __init__(self) -> None:
            self.gone = threading.Event()

        def wait(self, timeout=None):
            self.gone.wait(timeout)
            fake_psutil.kids = []  # it exited: the shell has no child now

    kid = Kid()
    fake_psutil.kids = [kid]
    s = _pwsh_session()
    s._ingest(b"TUI " + ON)  # the program turns tracking on
    assert s._modes.mouse_on
    assert s._mouse_watch is not None
    assert M.MODE_RESET not in bytes(s._tail)  # not while it is alive
    kid.gone.set()  # ...and dies without turning it off
    assert _wait(lambda: M.MODE_RESET in bytes(s._tail), 10)
    assert not s._modes.mouse_on


def test_a_program_that_switches_tracking_off_itself_gets_no_reset(fake_psutil, monkeypatch):
    """Anti-vacuity for the test above: the reset comes from the DEAD
    program's leftover mode, not from a child exiting."""
    monkeypatch.setattr(S, "MOUSE_WATCH_GRACE_S", 0.0)
    gone = threading.Event()

    class Kid:
        def wait(self, timeout=None):
            gone.wait(timeout)
            fake_psutil.kids = []

    fake_psutil.kids = [Kid()]
    s = _pwsh_session()
    s._ingest(b"TUI " + ON)
    watcher = s._mouse_watch
    assert watcher is not None  # the watcher really runs in this test
    s._ingest(b"\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1006l bye")  # a clean exit
    gone.set()
    watcher.join(10)
    assert not watcher.is_alive()
    assert M.MODE_RESET not in bytes(s._tail)


@real_conpty
def test_real_conpty_a_crashed_tui_leaves_no_mouse_mode_behind(tmp_path):
    """cmd.exe in a real ConPTY runs a program that turns any-motion mouse
    tracking on and crashes. The modes pass through ConPTY, and the pane is
    told to switch them off once the program is gone."""
    tui = tmp_path / "tui.py"
    tui.write_text(
        "import os, sys, time\n"
        "sys.stdout.write('\\x1b[?1000h\\x1b[?1003h\\x1b[?1006hTUI-UP\\r\\n')\n"
        "sys.stdout.flush()\n"
        "time.sleep(1.0)\n"
        "os._exit(3)\n",
        encoding="utf-8",
    )
    s = TerminalSession(argv=["cmd.exe"], cwd=str(tmp_path), backend=B.ConPtyBackend()).start()
    try:
        assert _wait(lambda: b">" in bytes(s._tail), 30)
        s.write(f'"{sys.executable}" "{tui}"\r')
        assert _wait(lambda: b"TUI-UP" in bytes(s._tail), 30), bytes(s._tail)[-300:]
        # ConPTY passed the switch through. v1.400.0: WAIT for it — under a
        # loaded full run ConPTY handed the switch over a moment after the
        # text it came before, and a read straight after "TUI-UP" missed it.
        assert _wait(lambda: b"\x1b[?1003h" in bytes(s._tail), 30), bytes(s._tail)[-300:]
        assert _wait(lambda: M.MODE_RESET in bytes(s._tail), 60)
        after = bytes(s._tail)
        assert after.index(M.MODE_RESET) > after.index(b"TUI-UP")
        assert not s._modes.mouse_on
    finally:
        s.kill()


# =========================================================================== #
# B2 — what a pane started in the background stops when it closes             #
# =========================================================================== #


def _m(pid, ppid, started, host=0, host_parent=0) -> J.Member:
    return J.Member(pid=pid, ppid=ppid, started=started, host=host, host_parent=host_parent)


def test_window_owners_their_helpers_and_their_launchers_are_spared():
    members = {
        m.pid: m
        for m in (
            _m(10, 1, 1.0),            # a hidden dev server
            _m(11, 10, 2.0),           # ...and its worker
            _m(20, 1, 1.0),            # a windowless launcher (venv pythonw)
            _m(21, 20, 2.0),           # ...the program that owns the window
            _m(22, 21, 3.0),           # ...its helper (no window of its own)
            _m(23, 20, 2.5),           # ...the launcher's OTHER child: hidden
        )
    }
    assert J.hidden_ones(members, visible={21}) == [10, 11, 23]


def test_a_console_program_in_a_visible_console_window_is_spared():
    members = {
        m.pid: m
        for m in (
            _m(30, 1, 1.0, host=31, host_parent=30),   # its conhost owns a visible window
            _m(40, 1, 1.0, host=41, host_parent=99),   # a tab in Windows Terminal (99)
            _m(50, 1, 1.0, host=51, host_parent=50),   # Start-Process -WindowStyle Hidden
        )
    }
    assert J.hidden_ones(members, visible={31, 99}) == [50]


def test_a_reused_parent_pid_does_not_spare_a_hidden_process():
    """Its 'parent' pid now names a LATER process (the real parent died and
    the pid was reused by a window owner): that is not its parent."""
    members = {m.pid: m for m in (_m(60, 70, 1.0), _m(70, 1, 5.0))}
    assert J.hidden_ones(members, visible={70}) == [60]


def test_the_escape_hatch_starts_panes_without_a_job(monkeypatch):
    monkeypatch.setenv(J.PANE_JOB_ENV, "off")
    assert J.create_job() is None


def test_rehydrate_stops_the_recorded_leftovers_of_a_crash(tmp_path, monkeypatch):
    sp = tmp_path / "terminals.json"
    entry = {
        "id": "term_crashed", "shell": "sh", "argv": ["sh"], "cwd": str(tmp_path),
        "cols": 80, "rows": 24, "background": [[4321, 1791700000.25], "junk"],
    }
    sp.write_text(json.dumps({"terminals": [entry]}), encoding="utf-8")
    asked: list = []
    monkeypatch.setattr(J, "stop_leftovers", lambda pairs: asked.append(list(pairs)) or [])
    m = TerminalManager(state_path=sp)
    assert m.rehydrate(backend=FakeBackend()) == 1
    assert asked == [[[4321, 1791700000.25]]]


def _ps(cmd: str) -> str:
    return cmd.replace("'", "''")


def _printed(s: TerminalSession, label: str) -> int | None:
    """The number a pane printed as ``LABEL=<n>`` (not the echoed command)."""
    found = re.search(label + r"=(\d+)", s.output_tail())
    return int(found.group(1)) if found else None


@real_conpty
def test_real_conpty_closing_a_pane_stops_its_hidden_process_and_never_a_window(tmp_path):
    """A real pane (PowerShell in ConPTY, in its job) starts a HIDDEN ping
    with Start-Process and a VISIBLE Tk window (through the venv's pythonw
    launcher, parked off-screen). Closing the pane stops the ping; the window
    and its launcher keep running. Notepad is not used: on Windows 11 it is
    a packaged app activated OUTSIDE the pane's job, so it would survive for
    the wrong reason."""
    psutil = pytest.importorskip("psutil")
    pytest.importorskip("tkinter")
    pyw = Path(sys.executable).with_name("pythonw.exe")
    if not pyw.is_file():
        pytest.skip("no pythonw.exe beside this interpreter")
    win = tmp_path / "win.pyw"
    win.write_text(
        "import tkinter as t\nr = t.Tk()\nr.geometry('120x40+-3000+-3000')\n"
        "r.title('ij-v1333-test')\nr.after(120000, r.destroy)\nr.mainloop()\n",
        encoding="utf-8",
    )
    s = TerminalSession(
        argv=["powershell.exe", "-NoLogo", "-NoProfile"], cwd=str(tmp_path),
        backend=B.ConPtyBackend(),
    ).start()
    be = s.backend
    ping = tk = None
    try:
        assert be._job is not None, "the pane was not started in a job"
        s.write(
            "$p = Start-Process -WindowStyle Hidden ping -ArgumentList '-n','600','127.0.0.1'"
            " -PassThru; 'PING=' + $p.Id\r"
        )
        s.write(f"$w = Start-Process '{_ps(str(pyw))}' -ArgumentList '\"{_ps(str(win))}\"'"
                " -PassThru; 'TK=' + $w.Id\r")
        assert _wait(lambda: _printed(s, "TK") and _printed(s, "PING"), 60), (
            s.output_tail()[-500:]
        )
        ping, tk = _printed(s, "PING"), _printed(s, "TK")
        # The window is up: some member below the launcher owns it.
        def window_up() -> bool:
            vis = J.visible_window_pids()
            return any(p in vis for p in J.job_pids(be._job))
        assert _wait(window_up, 60), "the Tk window never became visible"
        members = J.job_pids(be._job)
        assert ping in members and tk in members  # Start-Process stays in the job
        s.kill()
        try:
            psutil.Process(ping).wait(timeout=90)  # a liveness bound, not a speed claim
        except psutil.NoSuchProcess:
            pass
        assert not psutil.pid_exists(ping), "the hidden ping outlived its pane"
        assert psutil.pid_exists(tk), "the window's launcher was stopped"
        assert any(c.is_running() for c in psutil.Process(tk).children()), (
            "the window itself was stopped"
        )
    finally:
        s.kill()
        for pid in (ping, tk):
            if pid:
                try:
                    proc = psutil.Process(pid)
                    for c in proc.children(recursive=True):
                        c.kill()
                    proc.kill()
                except psutil.Error:
                    pass


@real_conpty
def test_real_conpty_a_crash_record_names_the_hidden_process_and_the_boot_stops_that_one(
    tmp_path,
):
    """What the snapshot records: the hidden ping (outside the pane's
    console), not a ping still ATTACHED to it (that one dies with the
    console) and not the shell. The boot sweep stops only an EXACT
    (pid, start time) match."""
    psutil = pytest.importorskip("psutil")
    s = TerminalSession(
        argv=["powershell.exe", "-NoLogo", "-NoProfile"], cwd=str(tmp_path),
        backend=B.ConPtyBackend(),
    ).start()
    be = s.backend
    ping = att = None
    try:
        s.write(
            "$p = Start-Process -WindowStyle Hidden ping -ArgumentList '-n','600','127.0.0.1'"
            " -PassThru; 'PING=' + $p.Id\r"
        )
        s.write(
            "$a = Start-Process -NoNewWindow ping -ArgumentList '-n','600','127.0.0.1'"
            " -PassThru; 'ATT=' + $a.Id\r"
        )
        assert _wait(lambda: _printed(s, "ATT") and _printed(s, "PING"), 60), (
            s.output_tail()[-500:]
        )
        ping, att = _printed(s, "PING"), _printed(s, "ATT")
        recorded = be.background_members()
        pids = [p for p, _ in recorded]
        assert ping in pids
        assert att not in pids and be.pid not in pids
        started = dict(recorded)[ping]
        # A wrong start time is a different process: never stopped.
        assert J.stop_leftovers([[ping, started + 5.0]]) == []
        assert psutil.pid_exists(ping)
        assert ping in J.stop_leftovers([[ping, started]])
        try:
            psutil.Process(ping).wait(timeout=90)
        except psutil.NoSuchProcess:
            pass
        assert not psutil.pid_exists(ping)
    finally:
        s.kill()
        for pid in (ping, att):
            if pid:
                try:
                    psutil.Process(pid).kill()
                except psutil.Error:
                    pass


# =========================================================================== #
# B3 — a pane an agent opens runs below normal                                #
# =========================================================================== #


class _FakeBackedManager(TerminalManager):
    def create(self, *a, **kw):
        kw.setdefault("backend", FakeBackend())
        return super().create(*a, **kw)


async def test_a_pane_an_agent_opens_runs_below_normal(tmp_path):
    from iron_jarvis.tools.base import ToolContext
    from iron_jarvis.tools.pane_tools import PaneSpawnTool

    mgr = _FakeBackedManager()
    ctx = ToolContext(
        workspace=tmp_path, session_id="s", agent_run_id="r",
        config=None, event_bus=None, engine=None,
    )
    res = await PaneSpawnTool(mgr).execute({"name": "builder"}, ctx)
    assert res.ok
    session = mgr.get(res.data["pane"]["id"])
    assert session.below_normal is True
    assert session.backend.below_normal is True
    user = mgr.create(cwd=str(tmp_path))  # the user's "+" pane
    assert user.below_normal is False
    assert getattr(user.backend, "below_normal", False) is False


def test_below_normal_survives_a_restart_and_a_user_pane_entry_does_not_carry_it(tmp_path):
    sp = tmp_path / "terminals.json"
    m1 = TerminalManager(state_path=sp)
    agent = m1.create(cwd=str(tmp_path), backend=FakeBackend(), below_normal=True)
    user = m1.create(cwd=str(tmp_path), backend=FakeBackend())
    m1.snapshot()
    entries = {e["id"]: e for e in json.loads(sp.read_text("utf-8"))["terminals"]}
    assert entries[agent.id]["below_normal"] is True
    assert "below_normal" not in entries[user.id]
    m2 = TerminalManager(state_path=sp)
    assert m2.rehydrate(backend=FakeBackend()) == 2
    assert m2.get(agent.id).below_normal is True
    assert m2.get(user.id).below_normal is False


@real_conpty
def test_real_conpty_an_agent_panes_shell_runs_below_normal_and_a_users_normal(
    tmp_path, normal_priority
):
    psutil = pytest.importorskip("psutil")
    low = TerminalSession(
        argv=["cmd.exe"], cwd=str(tmp_path), backend=B.ConPtyBackend(), below_normal=True
    ).start()
    normal = TerminalSession(argv=["cmd.exe"], cwd=str(tmp_path), backend=B.ConPtyBackend()).start()
    try:
        assert psutil.Process(low.shell_pid).nice() == psutil.BELOW_NORMAL_PRIORITY_CLASS
        assert psutil.Process(normal.shell_pid).nice() == psutil.NORMAL_PRIORITY_CLASS
    finally:
        low.kill()
        normal.kill()


# =========================================================================== #
# B5 — one busy pane writes one file                                          #
# =========================================================================== #


def _count_writes(monkeypatch, root: Path) -> dict:
    """Count bytes and files written under ``root`` through pathlib."""
    seen = {"bytes": 0, "files": []}
    real_bytes, real_text = Path.write_bytes, Path.write_text

    def wb(self, data):
        if root in self.parents:
            seen["bytes"] += len(data)
            seen["files"].append(self.name)
        return real_bytes(self, data)

    def wt(self, data, *a, **kw):
        if root in self.parents:
            seen["bytes"] += len(data.encode("utf-8"))
            seen["files"].append(self.name)
        return real_text(self, data, *a, **kw)

    monkeypatch.setattr(Path, "write_bytes", wb)
    monkeypatch.setattr(Path, "write_text", wt)
    return seen


def test_one_busy_pane_beside_five_quiet_ones_writes_one_file_per_tick(tmp_path, monkeypatch):
    m = TerminalManager(state_path=tmp_path / "terminals.json")
    panes = [m.create(cwd=str(tmp_path), backend=FakeBackend()) for _ in range(6)]
    for p in panes:  # every pane has a full 64 KB of history
        p._tail = bytearray(b"x" * 70_000)
    m.snapshot()
    seen = _count_writes(monkeypatch, tmp_path)
    busy = panes[0]
    for tick in range(10):
        busy.write(f"line {tick}\n")
        busy.read()
        assert m.snapshot_if_changed() is True
    # Ten ticks = ten writes of the busy pane's file and nothing else.
    assert seen["files"] == [f"{busy.id}.bin.tmp"] * 10
    assert seen["bytes"] <= 10 * (64 * 1024)
    assert m.snapshot_if_changed() is False  # a quiet tick writes nothing


def test_a_snapshot_written_before_v1333_still_restores_its_inline_scrollback(tmp_path):
    sp = tmp_path / "terminals.json"
    entry = {
        "id": "term_oldformat1", "shell": "sh", "argv": ["sh"], "cwd": str(tmp_path),
        "cols": 80, "rows": 24,
        "scrollback_b64": base64.b64encode(b"history from v1.332\r\n").decode("ascii"),
    }
    sp.write_text(json.dumps({"terminals": [entry]}), encoding="utf-8")
    m = TerminalManager(state_path=sp)
    assert m.rehydrate(backend=FakeBackend()) == 1
    assert m.get("term_oldformat1").scrollback_bytes().startswith(b"history from v1.332\r\n")
    # The next write moves it to its own file, and the index loses the blob.
    saved = json.loads(sp.read_text("utf-8"))["terminals"][0]
    assert "scrollback_b64" not in saved
    assert (tmp_path / "terminals-scrollback" / "term_oldformat1.bin").read_bytes().startswith(
        b"history from v1.332"
    )


def test_a_closed_panes_scrollback_file_is_removed(tmp_path):
    m = TerminalManager(state_path=tmp_path / "terminals.json")
    keep = m.create(cwd=str(tmp_path), backend=FakeBackend())
    gone = m.create(cwd=str(tmp_path), backend=FakeBackend())
    folder = tmp_path / "terminals-scrollback"
    assert (folder / f"{gone.id}.bin").is_file()
    m.kill(gone.id)
    assert not (folder / f"{gone.id}.bin").exists()
    assert (folder / f"{keep.id}.bin").is_file()


def test_an_id_that_cannot_name_a_file_keeps_its_scrollback_inline(tmp_path):
    sp = tmp_path / "terminals.json"
    m = TerminalManager(state_path=sp)
    s = m.create(cwd=str(tmp_path), backend=FakeBackend())
    s.id = "../escape"
    m._sessions = {s.id: s}
    s._tail = bytearray(b"odd id history")
    m.snapshot()
    saved = json.loads(sp.read_text("utf-8"))["terminals"][0]
    assert base64.b64decode(saved["scrollback_b64"]) == b"odd id history"
    assert not (tmp_path / "escape.bin").exists()


# =========================================================================== #
# B6 — fewer wakeups, no blocking writes on the loop, incremental reads        #
# =========================================================================== #


class _Loop:
    def __init__(self) -> None:
        self.scheduled: list = []

    def call_soon_threadsafe(self, fn) -> None:
        self.scheduled.append(fn)


def test_a_burst_of_pushes_schedules_one_loop_callback():
    loop, event = _Loop(), asyncio.Event()
    wake = coalesced_waker(loop, event)
    for _ in range(100):
        wake()
    assert len(loop.scheduled) == 1
    loop.scheduled.pop()()  # the loop runs it: the event is set...
    assert event.is_set()
    wake()  # ...and the next chunk schedules again — none goes unwoken
    assert len(loop.scheduled) == 1


def test_an_idle_attached_pane_wakes_once_a_minute_not_every_five_seconds():
    src = (Path(__file__).resolve().parents[1] / "src" / "iron_jarvis" / "daemon" / "routes"
           / "terminals.py").read_text(encoding="utf-8")
    assert "IDLE_WAKE_S = 60.0" in src


def _rec(when: str, text: str, *, error: bool) -> dict:
    rec = {
        "type": "assistant", "timestamp": when,
        "message": {"content": [{"type": "text", "text": text}]},
    }
    if error:
        rec.update(isApiErrorMessage=True, error="invalid_request")
    return rec


def _append(path: Path, rec: dict) -> None:
    with open(path, "ab") as fh:
        fh.write((json.dumps(rec) + "\n").encode("utf-8"))


AT = "2026-10-11T08:00:00+00:00"


def test_the_resume_check_reads_only_what_was_appended(tmp_path):
    path = tmp_path / "conv.jsonl"
    for i in range(200):  # an existing conversation, ~30 KB
        _append(path, _rec("2026-10-11T07:00:00+00:00", "old answer " * 10, error=False))
    watch = FileWatch(path, AT)
    assert watch.check() is None
    first = watch.last_read
    assert first == path.stat().st_size
    bad = _rec("2026-10-11T08:05:00+00:00", "API Error: 400 invalid_request_error", error=True)
    _append(path, bad)
    found = watch.check()
    assert found is not None and found.kind == "failed"
    assert watch.last_read == len(json.dumps(bad)) + 1  # only the new record
    assert found == check_file(path, AT)  # the same verdict as a full read
    _append(path, _rec("2026-10-11T08:06:00+00:00", "a real answer", error=False))
    assert watch.check() is None and check_file(path, AT) is None


def test_a_replaced_carried_file_is_read_again_from_its_tail(tmp_path):
    path = tmp_path / "conv.jsonl"
    _append(path, _rec("2026-10-11T08:05:00+00:00", "API Error: 400 invalid_request_error",
                       error=True))
    watch = FileWatch(path, AT)
    assert watch.check() is not None
    fresh = tmp_path / "new.jsonl"
    _append(fresh, _rec("2026-10-11T08:07:00+00:00", "answered after all " * 20, error=False))
    os.replace(fresh, path)  # a rewrite: a new file in the same place
    assert watch.check() is None
    assert watch.last_read == path.stat().st_size


def test_a_sign_in_pane_scans_its_tail_only_after_it_printed():
    from iron_jarvis.terminals.signin_watch import watch_signin

    s = TerminalSession(shell="x", argv=["x"], backend=FakeBackend()).start()
    s.signin_for, s.signin_provider = "acct-1", "anthropic"
    reads = {"n": 0}
    real = s.output_tail

    def counted():
        reads["n"] += 1
        return real()

    s.output_tail = counted

    class Svc:
        asked = 0

        def request_refresh(self, pid, force=False):
            Svc.asked += 1
            return None

    for _ in range(20):  # the 2.5 s activity poll, nothing printed
        watch_signin(s, Svc())
    assert reads["n"] == 1
    s.write("Login successful.\n")
    s.read()
    watch_signin(s, Svc())
    assert reads["n"] == 2
    assert Svc.asked == 1


# =========================================================================== #
# Review (v1.333.0) — what the first cut missed                               #
# =========================================================================== #


class _Kid:
    """A child process stand-in: ``wait`` blocks until ``gone`` is set."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.gone = threading.Event()
        self.entered = threading.Event()

    def wait(self, timeout=None):
        self.entered.set()
        if not self.gone.wait(timeout):
            import psutil

            raise psutil.TimeoutExpired(timeout)


def test_a_program_that_left_the_console_does_not_count_as_running_in_the_pane(
    fake_psutil, monkeypatch
):
    """A hidden dev server started with Start-Process stays the SHELL'S
    CHILD for hours but owns another console: it cannot read what the pane
    types, so a dead program's mouse reports are still dropped. A child on
    the pane's own console (a program really running in it) is never
    filtered."""
    consoles = {4242: 900, 77: 901, 78: 900}
    monkeypatch.setattr(J, "console_host_pid", lambda pid: consoles.get(pid, 0))
    fake_psutil.kids = [_Kid(77)]  # Start-Process -WindowStyle Hidden npm run dev
    s = _pwsh_session()
    s.write_input(REPORT)
    assert s.backend.writes == []
    assert M.MODE_RESET in bytes(s._tail)
    fake_psutil.kids = [_Kid(77), _Kid(78)]  # ...and now claude runs in the pane
    s2 = _pwsh_session()
    s2.write_input(REPORT)
    assert s2.backend.writes == [REPORT]


@real_conpty
def test_real_conpty_a_hidden_background_child_leaves_the_shell_idle_and_an_attached_one_does_not(
    tmp_path,
):
    psutil = pytest.importorskip("psutil")
    s = TerminalSession(
        argv=["powershell.exe", "-NoLogo", "-NoProfile"], cwd=str(tmp_path),
        backend=B.ConPtyBackend(),
    ).start()
    hid = att = None
    try:
        s.write(
            "$p = Start-Process -WindowStyle Hidden ping -ArgumentList '-n','600','127.0.0.1'"
            " -PassThru; 'HID=' + $p.Id\r"
        )
        assert _wait(lambda: _printed(s, "HID"), 60), s.output_tail()[-500:]
        hid = _printed(s, "HID")
        assert hid in [c.pid for c in psutil.Process(s.shell_pid).children()]
        s._child_check = None
        assert s._shell_is_idle() is True  # the hidden ping is not IN the pane
        s.write(
            "$a = Start-Process -NoNewWindow ping -ArgumentList '-n','600','127.0.0.1'"
            " -PassThru; 'ATT=' + $a.Id\r"
        )
        assert _wait(lambda: _printed(s, "ATT"), 60), s.output_tail()[-500:]
        att = _printed(s, "ATT")
        s._child_check = None
        assert s._shell_is_idle() is False  # this one shares the pane's console
    finally:
        s.kill()
        for pid in (hid, att):
            if pid:
                try:
                    psutil.Process(pid).kill()
                except psutil.Error:
                    pass


def test_a_program_relaunched_during_the_grace_is_watched_not_reset(monkeypatch):
    """The first program dies; within the grace a new one starts and turns
    mouse tracking on again. The watcher must watch THAT one, not reset the
    mode under it, and reset once it dies too."""
    import psutil

    monkeypatch.setattr(S, "MOUSE_WATCH_GRACE_S", 0.0)
    monkeypatch.setattr(J, "console_host_pid", lambda pid: 0)
    first, second = _Kid(1), _Kid(2)
    first.gone.set()  # it has already exited when the watcher looks
    script = [[first], [], [second], [second], [], []]

    class Shell:
        pid = 4242

        def __init__(self, pid) -> None:
            pass

        def children(self):
            return script.pop(0) if len(script) > 1 else script[0]

    monkeypatch.setattr(psutil, "Process", Shell)
    s = _pwsh_session()
    s._ingest(b"TUI " + ON)
    assert _wait(second.entered.is_set, 10)
    assert M.MODE_RESET not in bytes(s._tail)  # the relaunched program keeps its mode
    second.gone.set()
    assert _wait(lambda: M.MODE_RESET in bytes(s._tail), 10)


def test_a_switch_on_while_the_watcher_finishes_gets_a_watcher_of_its_own(
    fake_psutil, monkeypatch
):
    """The race the lock and the restart close: tracking is OFF when the
    watcher decides to finish, a program turns it ON before the watcher has
    deregistered (its start finds the old watcher and starts none), and the
    finishing watcher starts the new one."""
    monkeypatch.setattr(S, "MOUSE_WATCH_GRACE_S", 0.0)

    class Kid(_Kid):
        def wait(self, timeout=None):
            super().wait(timeout)
            fake_psutil.kids = []  # it exited

    kid = Kid(5)
    fake_psutil.kids = [kid]
    s = _pwsh_session()
    real_lock = s._watch_lock
    calls = {"n": 0}

    class RacingLock:
        def __enter__(self):
            calls["n"] += 1
            if calls["n"] == 2:  # the FIRST watcher's finally, about to deregister
                s._ingest(b"next program " + ON)  # its start finds the old watcher
            return real_lock.__enter__()

        def __exit__(self, *exc):
            return real_lock.__exit__(*exc)

    s._watch_lock = RacingLock()
    s._ingest(b"TUI " + ON)  # the first watcher starts (lock use 1)...
    s._ingest(b"\x1b[?1000l\x1b[?1002l\x1b[?1003l bye")  # ...the program turns it off
    kid.gone.set()  # and exits cleanly: the watcher finishes with tracking OFF
    assert _wait(lambda: calls["n"] >= 5, 10)  # a SECOND watcher registered and finished
    # No child ever ran, the second program's mode is still on with nothing
    # running in the pane: the second watcher switched it off.
    assert _wait(lambda: M.MODE_RESET in bytes(s._tail), 10)
    assert not s._modes.mouse_on


def test_a_console_a_terminal_draws_counts_as_a_window_even_when_its_tab_is_behind():
    """Measured on this PC: Windows 11 hands ``Start-Process cmd`` to Windows
    Terminal; the program owns no visible window and its conhost none either,
    only a PseudoConsoleWindow tied to it. That console is SHOWN. A hidden
    conhost window is not, and the pane's OWN console never is."""
    members = {
        m.pid: m
        for m in (
            _m(80, 1, 1.0, host=81, host_parent=80),  # cmd in a Terminal tab
            _m(82, 80, 2.0, host=81, host_parent=80),  # ...the ping it runs
            _m(90, 1, 1.0, host=91, host_parent=90),  # Start-Process -WindowStyle Hidden
            _m(95, 1, 1.0, host=99, host_parent=5),   # attached to the pane's console
        )
    }
    consoles = {81: True, 91: False, 99: True}
    assert J.hidden_ones(members, visible=set(), consoles=consoles, own_host=99) == [90, 95]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows consoles")
def test_real_console_windows_tell_a_minimized_console_from_a_hidden_one():
    """A MINIMIZED console (it does not take focus) is a window the user can
    bring back; a hidden one is not. Neither is touched by the test beyond
    reading it."""
    import subprocess

    psutil = pytest.importorskip("psutil")

    def start(show: int):
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = show
        return subprocess.Popen(
            ["ping", "-n", "600", "127.0.0.1"],
            creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=si,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    hidden = start(0)          # SW_HIDE
    shown = start(7)           # SW_SHOWMINNOACTIVE
    try:
        def facts():
            got = J.console_windows()
            return (
                got.get(J.console_host_pid(hidden.pid)),
                got.get(J.console_host_pid(shown.pid)),
            )

        # A process that has just started still reports its PARENT'S console
        # host for a moment (measured): wait for its own (a liveness bound).
        assert _wait(lambda: facts() == (False, True), 30), facts()
        members = J.describe([hidden.pid, shown.pid])
        doomed = J.hidden_ones(members, J.visible_window_pids(), J.console_windows())
        assert hidden.pid in doomed and shown.pid not in doomed
    finally:
        for p in (hidden, shown):
            try:
                psutil.Process(p.pid).kill()
            except psutil.Error:
                pass


def test_the_boot_sweep_spares_a_launcher_whose_window_opened_after_the_snapshot(monkeypatch):
    """The crash record named a hidden dev server (10) and a windowless
    launcher (20). The launcher's program (21) opened its window AFTER the
    last snapshot, so it is not recorded. It still spares its launcher, and
    it is never stopped itself: only recorded processes are."""
    facts = {
        10: _m(10, 1, 1.0),
        20: _m(20, 1, 1.0),
        21: _m(21, 20, 2.0),
    }
    monkeypatch.setattr(J, "_WINDOWS", True)
    monkeypatch.setattr(J, "describe", lambda pids: {p: facts[p] for p in pids if p in facts})
    monkeypatch.setattr(J, "_descendants", lambda pids: {21} if 20 in pids else set())
    monkeypatch.setattr(J, "visible_window_pids", lambda: {21})
    monkeypatch.setattr(J, "console_windows", lambda: {})
    stopped: list = []
    monkeypatch.setattr(J, "_stop", lambda pids, members: stopped.extend(pids) or list(pids))
    assert J.stop_leftovers([[10, 1.0], [20, 1.0]]) == [10]
    assert stopped == [10]


def test_a_pane_whose_shell_ended_on_its_own_is_swept_when_it_is_dropped():
    """Nobody closed it (the user typed ``exit``), so no kill() ran; when the
    retained-dead list drops it, its job is still swept."""
    swept = threading.Event()

    class Exited(FakeBackend):
        def stop_background(self) -> None:
            swept.set()

    m = TerminalManager(max_dead_retained=0)
    s = m.create(cwd=os.getcwd(), backend=Exited())
    s.backend._alive = False  # the shell exited by itself
    assert m.purge_dead() == 1
    assert swept.wait(10)


def test_the_shutdown_snapshot_keeps_the_history_inline_for_an_older_app(tmp_path):
    """An older Iron Jarvis reads only ``scrollback_b64``. The 30 s snapshot
    leaves it out (per-pane files); the shutdown one (kill_all) puts it back,
    and a restore prefers it."""
    sp = tmp_path / "terminals.json"
    m = TerminalManager(state_path=sp)
    s = m.create(cwd=str(tmp_path), backend=FakeBackend())
    s._tail = bytearray(b"history the old app must see\r\n")
    m.snapshot()
    assert "scrollback_b64" not in json.loads(sp.read_text("utf-8"))["terminals"][0]
    m.kill_all()
    saved = json.loads(sp.read_text("utf-8"))["terminals"][0]
    assert base64.b64decode(saved["scrollback_b64"]) == b"history the old app must see\r\n"
    m2 = TerminalManager(state_path=sp)
    assert m2.rehydrate(backend=FakeBackend()) == 1
    assert m2.get(s.id).scrollback_bytes().startswith(b"history the old app must see\r\n")


def test_an_overlapping_snapshot_never_deletes_a_held_panes_file(tmp_path):
    """A snapshot that listed the panes BEFORE this one existed writes its
    index after this pane's file was written: the file stays. While a pane
    is being created or restored nothing is pruned at all."""
    sp = tmp_path / "terminals.json"
    m = TerminalManager(state_path=sp)
    s = m.create(cwd=str(tmp_path), backend=FakeBackend())
    folder = tmp_path / "terminals-scrollback"
    assert (folder / f"{s.id}.bin").is_file()
    m._write_index([], json.dumps({"terminals": []}))  # the older listing lands last
    assert (folder / f"{s.id}.bin").is_file()
    stale = folder / "term_gone.bin"
    stale.write_bytes(b"x")
    m._reserved += 1
    try:
        m._write_index([], json.dumps({"terminals": []}))
        assert stale.is_file()  # not while a pane is being made
    finally:
        m._reserved -= 1
    m._write_index([], json.dumps({"terminals": []}))
    assert not stale.exists()  # an orphan still goes


def test_a_pane_restored_twice_without_new_output_carries_one_reset(tmp_path):
    sp = tmp_path / "terminals.json"
    old = b"claude " + ON
    once = old + M.restore_reset(old)
    entry = {
        "id": "term_twice", "shell": "sh", "argv": ["sh"], "cwd": str(tmp_path),
        "cols": 80, "rows": 24, "scrollback_b64": base64.b64encode(once).decode("ascii"),
    }
    sp.write_text(json.dumps({"terminals": [entry]}), encoding="utf-8")
    m = TerminalManager(state_path=sp)
    assert m.rehydrate(backend=FakeBackend()) == 1
    assert bytes(m.get("term_twice").scrollback_bytes()) == once
