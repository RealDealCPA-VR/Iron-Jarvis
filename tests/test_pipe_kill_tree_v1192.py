"""PipeBackend.kill must tear down the whole process TREE on Windows.

`Popen.kill` is TerminateProcess: it kills the shell and nothing else, so an AI
CLI / ffmpeg / build launched inside a degraded pipe terminal survived with no
pane and no way to stop it from the app (TOFIX-2026-08-20 #34). Every test here
drives the mechanism through spies — no real grandchild is ever spawned.
"""

from __future__ import annotations

import subprocess
import sys
import time
import types

import pytest

from iron_jarvis.terminals import backend as bk


class _FakeStdout:
    def read(self, n: int = 0) -> bytes:  # reader thread sees EOF immediately
        return b""


class _FakePopen:
    """Stand-in for a spawned shell (no real process)."""

    instances: list["_FakePopen"] = []

    def __init__(self, *a, **kw) -> None:
        self.pid = 4242
        self._handle = 987654
        self.stdout = _FakeStdout()
        self.stdin = None
        self.killed = 0
        self.returncode = None  # None = still running, like a real Popen
        self.args = a
        self.kwargs = kw
        _FakePopen.instances.append(self)

    def kill(self) -> None:
        self.killed += 1
        self.returncode = 1  # a killed process EXITS — poll() reports it after

    def poll(self):
        return self.returncode


def _backend_with_proc(
    job: int | None, exited: bool = False
) -> tuple[bk.PipeBackend, _FakePopen]:
    b = bk.PipeBackend()
    proc = _FakePopen()
    if exited:
        proc.returncode = 1  # the shell is already gone; its pid may be REUSED
    b._proc = proc  # type: ignore[assignment]
    b._job = job
    return b, proc


@pytest.fixture()
def spies(monkeypatch):
    """Force the Windows branch and spy on both tree-kill mechanisms."""
    calls: dict[str, list] = {"job": [], "taskkill": []}

    def fake_job(job):
        calls["job"].append(job)
        return True

    def fake_taskkill(pid):
        calls["taskkill"].append(pid)
        return True

    monkeypatch.setattr(bk, "_WINDOWS", True)
    monkeypatch.setattr(bk, "_win_terminate_job", fake_job)
    monkeypatch.setattr(bk, "_win_taskkill_tree", fake_taskkill)
    return calls


def test_kill_terminates_the_job_that_owns_the_whole_tree(spies):
    b, proc = _backend_with_proc(job=555)
    b.kill()
    assert spies["job"] == [555]  # the tree died, not just the shell
    assert spies["taskkill"] == []  # job worked — no need for taskkill
    assert proc.killed == 1  # the shell itself is still terminated


def test_kill_falls_back_to_taskkill_tree_when_no_job_exists(spies):
    b, proc = _backend_with_proc(job=None)
    b.kill()
    assert spies["taskkill"] == [proc.pid]
    assert proc.killed == 1


def test_kill_falls_back_to_taskkill_when_the_job_refuses(spies, monkeypatch):
    monkeypatch.setattr(bk, "_win_terminate_job", lambda job: False)
    b, proc = _backend_with_proc(job=555)
    b.kill()
    assert spies["taskkill"] == [proc.pid]


def test_kill_drops_the_job_handle_so_it_is_never_terminated_twice(spies):
    b, _ = _backend_with_proc(job=555)
    b.kill()
    b.kill()
    assert spies["job"] == [555]  # the handle is used once and dropped


def test_second_kill_never_taskkills_a_pid_windows_may_have_recycled(spies):
    """kill() is NOT idempotent by call count — it is called twice for real.

    `purge_dead` retains up to MAX_DEAD_RETAINED dead sessions, so a pane closed
    via DELETE /terminals/{id} is killed AGAIN by kill_all() at daemon shutdown,
    hours later, with `_job` already None. `taskkill /T /F /PID` against a pid
    the OS has since handed to Excel would force-kill the user's whole tree.
    """
    b, proc = _backend_with_proc(job=555)
    b.kill()
    assert proc.poll() is not None  # the shell is dead after the first kill
    b.kill()
    assert spies["taskkill"] == []  # no pid-based kill against a freed pid


def test_first_kill_of_an_already_exited_shell_issues_no_taskkill(spies):
    """Same hazard without a second call: no job, and the shell exited itself.

    `taskkill /T` walks LIVE parent-pid links, so it could not have reached the
    orphans anyway — it could only have hit whoever inherited the pid.
    """
    b, proc = _backend_with_proc(job=None, exited=True)
    b.kill()
    assert spies["taskkill"] == []
    assert spies["job"] == []
    assert proc.killed == 1  # reaping the handle is still safe


def test_kill_off_windows_does_not_shell_out(monkeypatch):
    called: list = []
    monkeypatch.setattr(bk, "_WINDOWS", False)
    monkeypatch.setattr(bk, "_win_taskkill_tree", lambda pid: called.append(pid))
    monkeypatch.setattr(bk, "_win_terminate_job", lambda job: called.append(job))
    b, proc = _backend_with_proc(job=None)
    b.kill()
    assert called == []
    assert proc.killed == 1


def test_kill_without_a_process_is_a_noop(spies):
    bk.PipeBackend().kill()  # must not raise
    assert spies["job"] == [] and spies["taskkill"] == []


def test_taskkill_argv_kills_the_tree_forcefully(monkeypatch):
    seen: dict = {}

    def fake_run(argv, **kw):
        seen["argv"] = argv
        seen["kw"] = kw
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(
        bk,
        "subprocess",
        types.SimpleNamespace(run=fake_run, DEVNULL=-3, CREATE_NO_WINDOW=0x08000000),
    )
    assert bk._win_taskkill_tree(1234) is True
    assert seen["argv"] == ["taskkill", "/T", "/F", "/PID", "1234"]
    assert seen["kw"]["timeout"] == 10  # bounded: kill() runs in a worker thread


def test_taskkill_never_raises_when_taskkill_is_missing(monkeypatch):
    def boom(*a, **kw):
        raise FileNotFoundError("taskkill")

    monkeypatch.setattr(
        bk, "subprocess", types.SimpleNamespace(run=boom, DEVNULL=-3)
    )
    assert bk._win_taskkill_tree(1234) is False


def test_start_puts_the_shell_into_a_job_on_windows(monkeypatch):
    made: list = []
    _FakePopen.instances.clear()
    monkeypatch.setattr(bk, "_WINDOWS", True)
    monkeypatch.setattr(
        bk,
        "subprocess",
        types.SimpleNamespace(Popen=_FakePopen, PIPE=-1, STDOUT=-2),
    )
    monkeypatch.setattr(bk, "_win_job_for", lambda h: made.append(h) or 99)
    b = bk.PipeBackend()
    b.start(["powershell"], "", None, 80, 24)
    assert made == [987654]  # the shell's process HANDLE, assigned at spawn
    assert b._job == 99


def test_start_off_windows_creates_no_job(monkeypatch):
    _FakePopen.instances.clear()
    monkeypatch.setattr(bk, "_WINDOWS", False)
    monkeypatch.setattr(
        bk,
        "subprocess",
        types.SimpleNamespace(Popen=_FakePopen, PIPE=-1, STDOUT=-2),
    )
    monkeypatch.setattr(bk, "_win_job_for", lambda h: pytest.fail("no jobs off win32"))
    b = bk.PipeBackend()
    b.start(["bash"], "", None, 80, 24)
    assert b._job is None


@pytest.mark.skipif(sys.platform != "win32", reason="Job objects are Windows-only")
def test_real_job_object_kills_a_real_child():
    """The ctypes struct layout actually works against kernel32."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        job = bk._win_job_for(int(proc._handle))
        assert job is not None, "CreateJobObject/AssignProcessToJobObject failed"
        assert bk._win_terminate_job(job) is True
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and proc.poll() is None:
            time.sleep(0.05)
        assert proc.poll() is not None, "TerminateJobObject did not kill the child"
    finally:
        if proc.poll() is None:  # pragma: no cover - only if the job failed
            proc.kill()
        proc.wait(timeout=5)


# --------------------------------------------------------------------------- #
# POSIX side of the same finding: killpg tears the tree down, and must NEVER   #
# fire twice — the second call would signal a RECYCLED pid's whole group.      #
# --------------------------------------------------------------------------- #


class _FakeOs:
    """Just the four calls `PosixPtyBackend.kill` makes, with a call log.

    A fake module beats monkeypatching the real `os`: `getpgid`/`killpg` do not
    EXIST on win32, where this suite runs.
    """

    def __init__(self, pgid_of, daemon_pgid: int = 1000) -> None:
        self._pgid_of = pgid_of
        self._daemon_pgid = daemon_pgid
        self.calls: list[tuple] = []

    def getpgid(self, pid: int) -> int:
        if pid == 0:
            return self._daemon_pgid
        pgid = self._pgid_of(pid)
        if pgid is None:
            raise ProcessLookupError(pid)
        return pgid

    def killpg(self, pgid: int, sig: int) -> None:
        self.calls.append(("killpg", pgid, sig))

    def kill(self, pid: int, sig: int) -> None:
        self.calls.append(("kill", pid, sig))

    def waitpid(self, pid: int, flags: int):
        self.calls.append(("waitpid", pid, flags))
        return (pid, 0)


@pytest.fixture()
def sigkill(monkeypatch):
    """`signal.SIGKILL` is POSIX-only; give the code something to send."""
    import signal as signal_mod

    monkeypatch.setattr(
        signal_mod, "SIGKILL", getattr(signal_mod, "SIGKILL", 9), raising=False
    )
    return int(getattr(signal_mod, "SIGKILL", 9))


def _posix_backend(pid: int = 4242) -> bk.PosixPtyBackend:
    b = bk.PosixPtyBackend()
    b._pid = pid  # type: ignore[attr-defined]
    return b


def test_posix_kill_signals_the_whole_group_not_just_the_shell(monkeypatch, sigkill):
    """`pty.fork` setsid's the child, so its group holds everything it spawned."""
    fake = _FakeOs(pgid_of=lambda pid: pid)  # the child leads its own group
    monkeypatch.setattr(bk, "os", fake)
    _posix_backend().kill()
    assert ("killpg", 4242, sigkill) in fake.calls
    assert not any(c[0] == "kill" for c in fake.calls)  # group covers the shell
    assert ("waitpid", 4242, 0) in fake.calls  # still reaped


def test_posix_second_kill_issues_no_signal_at_all(monkeypatch, sigkill):
    """THE REGRESSION GUARD. `kill()` is called twice for real on every session.

    The manager kills a closed pane, `purge_dead` RETAINS the dead session, and
    `kill_all()` kills it again at daemon shutdown — possibly hours later. The
    first call REAPS the pid, so on the second call the OS may have handed it to
    an unrelated process; because the group guard is `pgid == self._pid` it
    selects exactly the process-group LEADERS, so a stray killpg would SIGKILL a
    whole unrelated group the user owns. After reaping, the pid is forgotten.
    """
    reused = {"n": 0}

    def pgid_of(pid: int) -> int:
        # After the reap the pid is free; pretend it was handed to a new group
        # LEADER — the worst case, which the `pgid == pid` guard would accept.
        reused["n"] += 1
        return pid

    fake = _FakeOs(pgid_of=pgid_of)
    monkeypatch.setattr(bk, "os", fake)
    b = _posix_backend()
    b.kill()
    first = len(fake.calls)
    assert first > 0
    b.kill()
    assert fake.calls[first:] == []  # no killpg, no kill, no waitpid
    assert b.is_alive() is False


def test_posix_kill_never_signals_the_daemons_own_group(monkeypatch, sigkill):
    """If the child somehow shares the daemon's group, killpg = suicide."""
    fake = _FakeOs(pgid_of=lambda pid: 1000, daemon_pgid=1000)
    monkeypatch.setattr(bk, "os", fake)
    _posix_backend(pid=1000).kill()
    assert not any(c[0] == "killpg" for c in fake.calls)
    assert ("kill", 1000, sigkill) in fake.calls  # the shell alone


def test_posix_kill_falls_back_to_single_pid_when_child_leads_no_group(
    monkeypatch, sigkill
):
    fake = _FakeOs(pgid_of=lambda pid: 77)  # child is NOT the group leader
    monkeypatch.setattr(bk, "os", fake)
    _posix_backend().kill()
    assert not any(c[0] == "killpg" for c in fake.calls)
    assert ("kill", 4242, sigkill) in fake.calls


def test_posix_kill_survives_a_vanished_child(monkeypatch, sigkill):
    fake = _FakeOs(pgid_of=lambda pid: None)  # getpgid raises ProcessLookupError
    monkeypatch.setattr(bk, "os", fake)
    b = _posix_backend()
    b.kill()  # must not raise
    assert ("kill", 4242, sigkill) in fake.calls  # best-effort single kill
    assert b._pid is None  # still forgotten, so a later kill is a no-op


def test_posix_kill_without_a_pid_is_a_noop(monkeypatch, sigkill):
    fake = _FakeOs(pgid_of=lambda pid: pid)
    monkeypatch.setattr(bk, "os", fake)
    bk.PosixPtyBackend().kill()
    assert fake.calls == []


# --------------------------------------------------------------------------- #
# The NATURAL-EXIT arrival path: the shell exits by itself, the session's       #
# background drain thread reaps it via `is_alive()`, and shutdown still calls   #
# `kill()` on the retained dead session. That reap frees the pid too.           #
# --------------------------------------------------------------------------- #


class _FakeOsReaping(_FakeOs):
    """Adds the wait/status surface `is_alive()` uses.

    `WNOHANG`/`WIFEXITED`/`WEXITSTATUS`/`WTERMSIG` do not exist on win32, where
    this suite runs, so they are supplied here rather than monkeypatched.
    """

    WNOHANG = 1

    def __init__(self, *a, child: str = "exiting", status: int = 3 << 8, **kw) -> None:
        super().__init__(*a, **kw)
        self._child = child  # "exiting" -> reaped once | "running" | "gone"
        self._status = status

    def waitpid(self, pid: int, flags: int):
        self.calls.append(("waitpid", pid, flags))
        if not flags & self.WNOHANG:
            return (pid, 0)
        if self._child == "running":
            return (0, 0)
        if self._child == "gone":
            raise ChildProcessError(pid)
        self._child = "gone"  # reaped: the pid is FREED from here on
        return (pid, self._status)

    def WIFEXITED(self, status: int) -> bool:
        return True

    def WEXITSTATUS(self, status: int) -> int:
        return status >> 8

    def WIFSIGNALED(self, status: int) -> bool:  # pragma: no cover - not taken
        return False

    def WTERMSIG(self, status: int) -> int:  # pragma: no cover - not taken
        return 0


def test_posix_kill_after_a_natural_exit_issues_no_signal(monkeypatch, sigkill):
    """THE MORE COMMON ARRIVAL PATH, and the one the first stale-pid fix missed.

    Nobody calls `kill()` first here: the shell exits on its own, the session's
    drain thread polls `alive` and REAPS it, `purge_dead` retains the dead
    session, and `kill_all()` at daemon shutdown calls `session.kill()` with no
    aliveness check — possibly hours later, on a pid the OS has since handed to
    an unrelated process. Because the group guard is `pgid == self._pid`, a
    recycled pid passes it for exactly the process-group LEADERS, so the daemon
    would SIGKILL a whole unrelated group the user owns.
    """
    fake = _FakeOsReaping(pgid_of=lambda pid: pid)  # worst case: a group LEADER
    monkeypatch.setattr(bk, "os", fake)
    b = _posix_backend()

    assert b.is_alive() is False  # the drain thread observes + reaps the exit
    assert b.exit_code == 3
    after_reap = len(fake.calls)

    b.kill()
    assert fake.calls[after_reap:] == []  # no killpg, no kill, no waitpid
    assert b.exit_code == 3  # and the exit is still reported to the user


def test_posix_is_alive_keeps_the_pid_while_the_child_still_runs(monkeypatch, sigkill):
    """The clearing must be per-branch: a LIVE pane still has to be killable."""
    fake = _FakeOsReaping(pgid_of=lambda pid: pid, child="running")
    monkeypatch.setattr(bk, "os", fake)
    b = _posix_backend()

    assert b.is_alive() is True
    assert b._pid == 4242
    b.kill()
    assert ("killpg", 4242, sigkill) in fake.calls


def test_posix_is_alive_forgets_a_child_that_is_no_longer_ours(monkeypatch, sigkill):
    """`ChildProcessError` = already reaped elsewhere; not ours to signal."""
    fake = _FakeOsReaping(pgid_of=lambda pid: pid, child="gone")
    monkeypatch.setattr(bk, "os", fake)
    b = _posix_backend()

    assert b.is_alive() is False
    assert b._pid is None
    after = len(fake.calls)
    b.kill()
    assert fake.calls[after:] == []


def test_posix_kill_snapshots_the_pid_against_the_concurrent_drain_thread(
    monkeypatch, sigkill
):
    """`is_alive()` clears `_pid` from the drain thread WHILE `kill()` runs.

    Re-reading `self._pid` per line would hand `os.getpgid` a None — a
    TypeError, which the `except OSError` handlers do not catch, so it would
    escape `kill()` and abort `kill_all()`'s loop over the remaining sessions.
    """
    b = _posix_backend()

    def pgid_of(pid: int) -> int:
        b._pid = None  # the drain thread reaps between two lines of kill()
        return pid

    fake = _FakeOsReaping(pgid_of=pgid_of)
    monkeypatch.setattr(bk, "os", fake)

    b.kill()  # must not raise TypeError
    assert ("killpg", 4242, sigkill) in fake.calls
    assert ("waitpid", 4242, 0) in fake.calls
    assert b._pid is None
