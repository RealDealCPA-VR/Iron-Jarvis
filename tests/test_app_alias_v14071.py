"""v1.407.1 (a USER BUG): a terminal pane starts a Microsoft Store app's REAL
program, never its App Execution Alias.

On the user's PC, Build stuck at "Attaching to sessions…" and then "Iron
Jarvis isn't responding": both restored panes were parked in
``CreateProcessW`` on ``WindowsApps\\pwsh.EXE`` (the Store alias, whose
package activation sometimes never returns), and the daemon stopped answering
until the desktop watchdog killed it. ``terminals/app_alias`` reads the
alias's target and every backend starts that instead; the pane's saved argv
keeps the alias so a package update (a new folder name) is read again.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading

import pytest

from iron_jarvis.terminals import app_alias
from iron_jarvis.terminals import backend as B

WINDOWS = sys.platform == "win32"
USER_ALIAS = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\pwsh.exe")


def _buffer(*strings: str, tag: int = app_alias.APPEXECLINK_TAG, version: int = 3) -> bytes:
    body = version.to_bytes(4, "little") + "".join(s + "\x00" for s in strings).encode("utf-16-le")
    return tag.to_bytes(4, "little") + len(body).to_bytes(2, "little") + b"\x00\x00" + body


# --------------------------------------------------------------------------- #
# reading the alias
# --------------------------------------------------------------------------- #


def test_the_target_is_the_third_string_of_the_reparse_data():
    data = _buffer(
        "Microsoft.PowerShell_8wekyb3d8bbwe",
        "Microsoft.PowerShell_8wekyb3d8bbwe!App",
        r"C:\Program Files\WindowsApps\Microsoft.PowerShell_7.6.7.0_x64__8wekyb3d8bbwe\pwsh.exe",
        "0",
    )
    assert app_alias.parse_reparse_data(data) == (
        r"C:\Program Files\WindowsApps\Microsoft.PowerShell_7.6.7.0_x64__8wekyb3d8bbwe\pwsh.exe"
    )


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\x00" * 11,
        _buffer("a", "b", "c", tag=0xA000000C),  # a symlink's tag
        _buffer("a", "b"),  # no target string
        _buffer("a", "b", ""),  # an empty target
        app_alias.APPEXECLINK_TAG.to_bytes(4, "little") + b"\x40\x00\x00\x00" + b"\xff" * 9,
    ],
)
def test_anything_but_a_readable_alias_buffer_names_no_target(data):
    assert app_alias.parse_reparse_data(data) is None


def _fake_alias(monkeypatch, target: str | None) -> None:
    monkeypatch.setattr(app_alias, "is_app_alias", lambda p: p == "ALIAS")
    monkeypatch.setattr(
        app_alias, "_read_reparse", lambda p: _buffer("pkg", "pkg!App", target) if target else None
    )


def test_an_alias_whose_target_exists_resolves_to_it(monkeypatch):
    _fake_alias(monkeypatch, sys.executable)
    assert app_alias.resolve("ALIAS") == sys.executable


@pytest.mark.parametrize(
    "target",
    [None, "relative\\pwsh.exe", os.path.join(os.path.dirname(sys.executable), "missing-x.exe")],
)
def test_the_alias_itself_is_kept_when_its_target_cannot_be_used(monkeypatch, target):
    _fake_alias(monkeypatch, target)
    assert app_alias.resolve("ALIAS") == "ALIAS"


def test_a_target_that_is_not_a_program_keeps_the_alias(monkeypatch, tmp_path):
    not_exe = tmp_path / "pwsh.txt"
    not_exe.write_text("x", encoding="utf-8")
    _fake_alias(monkeypatch, str(not_exe))
    assert app_alias.resolve("ALIAS") == "ALIAS"


def test_an_ordinary_program_is_never_touched(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(app_alias, "_read_reparse", lambda p: seen.append(p))
    assert app_alias.resolve(sys.executable) == sys.executable
    assert seen == []  # not an alias: its reparse data is never read


def test_direct_argv_keeps_the_arguments_and_never_changes_the_callers_list(monkeypatch):
    _fake_alias(monkeypatch, sys.executable)
    saved = ["ALIAS", "-NoLogo", "-NoProfile"]
    out = app_alias.direct_argv(saved)
    assert out == [sys.executable, "-NoLogo", "-NoProfile"]
    assert saved == ["ALIAS", "-NoLogo", "-NoProfile"]  # the pane's saved argv keeps the alias


def test_resolution_never_raises(monkeypatch):
    def boom(p):
        raise RuntimeError("nope")

    monkeypatch.setattr(app_alias, "is_app_alias", boom)
    assert app_alias.resolve("ALIAS") == "ALIAS"
    assert app_alias.direct_argv([]) == []


@pytest.mark.skipif(not (WINDOWS and os.path.lexists(USER_ALIAS)), reason="no Store pwsh alias here")
def test_this_pcs_real_pwsh_alias_resolves_to_a_program_under_windowsapps():
    assert app_alias.is_app_alias(USER_ALIAS)
    real = app_alias.resolve(USER_ALIAS)
    assert real != USER_ALIAS
    assert real.lower().endswith("pwsh.exe") and "\\windowsapps\\microsoft.powershell_" in real.lower()
    assert os.path.isfile(real)


# --------------------------------------------------------------------------- #
# every backend starts the resolved program
# --------------------------------------------------------------------------- #


def test_the_pipe_backend_starts_the_real_program(monkeypatch):
    monkeypatch.setattr(app_alias, "resolve", lambda p: "REAL" if p == "ALIAS" else p)
    seen: dict = {}

    class FakePopen:
        def __init__(self, argv, **kw):
            seen["argv"] = argv
            raise OSError("stop here")

    monkeypatch.setattr(B.subprocess, "Popen", FakePopen)
    with pytest.raises(OSError):
        B.PipeBackend().start(["ALIAS", "-NoLogo"], os.getcwd(), None, 80, 24)
    assert seen["argv"] == ["REAL", "-NoLogo"]


def test_the_pywinpty_backend_starts_the_real_program(monkeypatch):
    monkeypatch.setattr(app_alias, "resolve", lambda p: "REAL" if p == "ALIAS" else p)
    seen: dict = {}

    class FakePty:
        @staticmethod
        def spawn(argv, **kw):
            seen["argv"] = argv
            raise OSError("stop here")

    fake = type(sys)("winpty")
    fake.PtyProcess = FakePty
    monkeypatch.setitem(sys.modules, "winpty", fake)
    with pytest.raises(OSError):
        B.WinPtyBackend().start(["ALIAS", "-NoLogo"], os.getcwd(), None, 80, 24)
    assert seen["argv"] == ["REAL", "-NoLogo"]


@pytest.mark.skipif(not WINDOWS, reason="ConPTY is Windows only")
def test_the_conpty_backend_starts_the_real_program(monkeypatch):
    """A REAL pseudoconsole: the argv names a program that does not exist,
    and the spawn succeeds only because the alias was resolved to cmd.exe."""
    cmd = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")
    monkeypatch.setattr(app_alias, "resolve", lambda p: cmd if p == "NOT-A-PROGRAM-ALIAS" else p)
    b = B.ConPtyBackend()
    eof = threading.Event()
    out = bytearray()

    def on_output(data):
        out.extend(data)
        if b"\x1b[c" in data:
            b.write("\x1b[?1;2c")

    b.set_output_handler(on_output, eof.set)
    b.start(["NOT-A-PROGRAM-ALIAS", "/c", "echo", "alias-resolved"], os.getcwd(), None, 120, 30)
    try:
        assert eof.wait(60), "cmd.exe never exited"
        assert b"alias-resolved" in bytes(out)
    finally:
        b.kill()


def test_an_unresolved_program_still_reaches_the_backend_unchanged(monkeypatch):
    seen: dict = {}

    class FakePopen:
        def __init__(self, argv, **kw):
            seen["argv"] = argv
            raise OSError("stop here")

    monkeypatch.setattr(B.subprocess, "Popen", FakePopen)
    with pytest.raises(OSError):
        B.PipeBackend().start([sys.executable, "-c", "pass"], os.getcwd(), None, 80, 24)
    assert seen["argv"] == [sys.executable, "-c", "pass"]
    assert subprocess.list2cmdline is not None


# --------------------------------------------------------------------------- #
# the review's fix: a Store program is never born in the pane job, and two
# Store programs never start at the same moment
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "path,packaged",
    [
        (r"C:\Program Files\WindowsApps\Microsoft.PowerShell_7.6.7.0_x64__8wekyb3d8bbwe\pwsh.exe", True),
        (r"C:\Users\VR\AppData\Local\Microsoft\WindowsApps\python.exe", True),
        (r"C:\Windows\System32\cmd.exe", False),
        (sys.executable, False),
        ("", False),
    ],
)
def test_a_store_program_is_recognised_by_its_folder(monkeypatch, path, packaged):
    monkeypatch.setattr(app_alias, "is_app_alias", lambda p: False)
    assert app_alias.is_packaged(path) is packaged


def test_a_uwp_alias_is_never_started_directly(monkeypatch):
    monkeypatch.setattr(app_alias, "is_app_alias", lambda p: p == "ALIAS")
    monkeypatch.setattr(app_alias, "_read_reparse", lambda p: _buffer("pkg", "pkg!App", sys.executable, "1"))
    assert app_alias.resolve("ALIAS") == "ALIAS"
    monkeypatch.setattr(app_alias, "_read_reparse", lambda p: _buffer("pkg", "pkg!App", sys.executable, "0"))
    assert app_alias.resolve("ALIAS") == sys.executable


class _SpawnSpy:
    def __init__(self, delay: float = 0.0):
        self.calls: list[tuple[list[str], object]] = []
        self.active = 0
        self.overlap = 0
        self.delay = delay
        self._lock = threading.Lock()

    def __call__(self, backend, api, argv, cwd, env, job=None):
        with self._lock:
            self.active += 1
            self.overlap = max(self.overlap, self.active)
            self.calls.append((list(argv), job))
        try:
            if self.delay:
                import time

                time.sleep(self.delay)
            raise OSError("spy: stop before a real process")
        finally:
            with self._lock:
                self.active -= 1


def _start_conpty(argv):
    b = B.ConPtyBackend()
    b.set_output_handler(lambda d: None, lambda: None)
    try:
        b.start(list(argv), os.getcwd(), None, 80, 24)
    except OSError:
        pass
    return b


@pytest.mark.skipif(not WINDOWS, reason="ConPTY is Windows only")
def test_a_store_program_never_asks_for_the_pane_job(monkeypatch):
    from iron_jarvis.terminals import pane_jobs

    made: list[int] = []
    monkeypatch.setattr(pane_jobs, "create_job", lambda: made.append(1) or 12345)
    monkeypatch.setattr(pane_jobs, "close_job", lambda j: None)
    spy = _SpawnSpy()
    monkeypatch.setattr(B.ConPtyBackend, "_spawn", lambda self, *a, **k: spy(self, *a, **k))
    store = r"C:\Program Files\WindowsApps\Microsoft.PowerShell_7.6.7.0_x64__8wekyb3d8bbwe\pwsh.exe"
    _start_conpty([store, "-NoLogo"])
    assert made == [] and spy.calls == [([store, "-NoLogo"], None)]
    # control: an ordinary program still asks for its job first
    spy.calls.clear()
    _start_conpty([sys.executable, "-c", "pass"])
    assert made == [1] and spy.calls[0][1] == 12345


@pytest.mark.skipif(not WINDOWS, reason="ConPTY is Windows only")
def test_two_store_programs_never_start_at_the_same_moment(monkeypatch):
    from iron_jarvis.terminals import pane_jobs

    monkeypatch.setattr(pane_jobs, "create_job", lambda: None)
    spy = _SpawnSpy(delay=0.3)
    monkeypatch.setattr(B.ConPtyBackend, "_spawn", lambda self, *a, **k: spy(self, *a, **k))
    store = r"C:\Program Files\WindowsApps\Microsoft.PowerShell_7.6.7.0_x64__8wekyb3d8bbwe\pwsh.exe"
    threads = [threading.Thread(target=_start_conpty, args=([store],)) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert len(spy.calls) == 3 and spy.overlap == 1
    # control: ordinary programs are not held to one at a time
    spy2 = _SpawnSpy(delay=0.3)
    monkeypatch.setattr(B.ConPtyBackend, "_spawn", lambda self, *a, **k: spy2(self, *a, **k))
    threads = [threading.Thread(target=_start_conpty, args=([sys.executable],)) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert spy2.overlap > 1


@pytest.mark.skipif(not (WINDOWS and os.path.lexists(USER_ALIAS)), reason="no Store pwsh alias here")
def test_this_pcs_store_pwsh_opens_a_real_pane_without_a_job():
    b = B.ConPtyBackend()
    eof = threading.Event()
    out = bytearray()

    def on_output(data):
        out.extend(data)
        if b"\x1b[c" in data:
            b.write("\x1b[?1;2c")

    b.set_output_handler(on_output, eof.set)
    b.start([USER_ALIAS, "-NoLogo", "-NoProfile", "-Command", "Write-Output pane-ok"], os.getcwd(), None, 120, 30)
    try:
        assert b._job is None
        assert eof.wait(60), "pwsh never exited"
        assert b"pane-ok" in bytes(out)
    finally:
        b.kill()


# --------------------------------------------------------------------------- #
# a frozen loop says where it is stuck, before the watchdog's kill
# --------------------------------------------------------------------------- #


def test_a_frozen_loop_is_reported_once_per_threshold_and_a_beat_ends_it():
    from iron_jarvis.daemon.boot_watch import LoopWatch

    now = [100.0]
    said: list[str] = []
    w = LoopWatch(clock=lambda: now[0], report=said.append)
    w._loop_thread = threading.get_ident()
    w.beat()
    now[0] += 10
    w.check()
    assert said == []
    now[0] += 11  # 21 s frozen
    w.check()
    w.check()
    assert len(said) == 1 and "event loop frozen for 21 s" in said[0] and "loop thread" in said[0]
    now[0] += 25  # 46 s
    w.check()
    assert len(said) == 2
    w.beat()
    now[0] += 21
    w.check()
    assert len(said) == 3  # a NEW freeze after a beat is reported again


def test_the_daemons_loop_task_beats_the_watch():
    src = open(os.path.join(os.path.dirname(__file__), "..", "src", "iron_jarvis", "daemon", "app.py"),
               encoding="utf-8").read()
    start = src.index("async def _loop_lag_loop")
    body = src[start : start + 400]
    assert "_loop_watch.beat()" in body
    assert "LoopWatch().start()" in src and "_loop_watch.stop()" in src
