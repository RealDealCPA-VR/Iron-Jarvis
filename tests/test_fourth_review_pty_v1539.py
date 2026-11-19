"""Fourth fix plan, group 30, TM2: on Linux and Mac a terminal whose shell is
missing could not be told from one whose shell ran and ended.

``PosixPtyBackend.start`` forks, and the child's failed ``execvpe`` only ever
became exit code 127, which reads as "the program ended by itself". So a
terminal saved on a shell that was since uninstalled came back at a restart
as a dead pane, and its kept history was deleted at the next save. Windows'
start raises instead, and the restore code keeps and lists such a terminal
with its reason, "Try again" and "Close".

The fix is a close-on-exec pipe in ``PosixPtyBackend.start`` only: the child
writes its exec error number to it, a successful exec closes it unread, so
the parent raises the same ``FileNotFoundError`` / ``PermissionError`` a
Windows start raises.

* Every OS: the shared exit-code rule is NOT where the fix lives
  (``exit_says_pty_failed(127) is False``: a Windows program that returns 127
  ended by itself), the report is decoded into the right error, and the
  parent's side of ``start`` is driven with stand-ins for the fork, the pipe's
  wait and the reap (Windows cannot fork).
* Linux and Mac only: a real fork with a missing shell raises and leaves no
  child; a non-executable file raises ``PermissionError``; a real shell still
  starts (one that exits 127 at once too); and a real restore keeps the
  terminal, lists it in plain words and keeps its history.
"""

from __future__ import annotations

import errno
import json
import os
import sys
import time
from types import SimpleNamespace

import pytest

import iron_jarvis.terminals.backend as be
import iron_jarvis.terminals.manager as mgr_mod

_POSIX_ONLY = pytest.mark.skipif(
    os.name != "posix",
    reason="a real fork and exec: the POSIX terminal backend runs only on Linux and Mac "
    "(proven on a Linux runner)",
)

#: A shell path no machine has.
MISSING = "/nonexistent/iron-jarvis-no-shell"


def _engine_words(text: str) -> list[str]:
    bad = ("WinError", "Errno", "OSError", "PermissionError", "FileNotFound", "Traceback")
    return [w for w in bad if w in text]


# --------------------------------------------------------------------------- #
# Every OS                                                                    #
# --------------------------------------------------------------------------- #


def test_the_shared_exit_code_rule_still_reads_127_as_ended_by_itself():
    """The fix is NOT in the shared reading of exit codes: a program that
    returns 127 (Windows or not) ended by itself (third plan TM11)."""
    assert be.exit_says_pty_failed(127) is False
    assert be.exit_says_pty_failed(0) is False
    assert be.exit_says_pty_failed(1) is False


def test_the_childs_report_is_decoded_into_the_error_a_windows_start_raises():
    missing = be._exec_error(b"2", "/usr/bin/fish")
    assert isinstance(missing, FileNotFoundError)
    assert missing.errno == errno.ENOENT and missing.filename == "/usr/bin/fish"
    refused = be._exec_error(b"13", "/usr/bin/fish")
    assert isinstance(refused, PermissionError) and refused.errno == errno.EACCES
    # Nothing written: the exec succeeded and closed the pipe.
    assert be._exec_error(b"", "/usr/bin/fish") is None
    # A report that is not an error number still means "did not start".
    for odd in (b"0", b"x", b"9" * 40):
        other = be._exec_error(odd, "/usr/bin/fish")
        assert isinstance(other, OSError), odd
        assert "fish could not be started" in str(other) and not _engine_words(str(other))


def test_the_restore_words_the_decoded_error_plainly():
    """The decoded error reaches the words the existing restore list uses."""
    why = mgr_mod._why_not_reopened(be._exec_error(b"2", "/usr/bin/fish"), {"argv": ["/usr/bin/fish"]})
    assert why == "Its program, fish, is no longer on this PC (it may have been uninstalled or moved)."
    why = mgr_mod._why_not_reopened(be._exec_error(b"13", "/usr/bin/fish"), {"argv": ["/usr/bin/fish"]})
    assert why == "This PC did not let Iron Jarvis start its program, fish."


class _Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def test_reading_the_report_collects_it_until_the_pipe_closes():
    chunks = [b"1", b"3", b""]  # the report, then EOF
    seen: list[float] = []

    def _select(r, w, x, timeout):
        seen.append(timeout)
        return (r, [], [])

    out = be._read_exec_report(7, 15.0, _select=_select, _read=lambda fd, n: chunks.pop(0), _clock=_Clock())
    assert out == b"13"
    assert seen and all(0 < t <= 15.0 for t in seen)


def test_reading_the_report_gives_up_at_its_bound_and_treats_the_shell_as_started():
    clock = _Clock()

    def _select(r, w, x, timeout):  # nothing ever arrives; time passes
        clock.t += timeout
        return ([], [], [])

    def _read(fd, n):  # pragma: no cover - reached only if the wait were wrong
        raise AssertionError("read without anything ready")

    assert be._read_exec_report(7, 15.0, _select=_select, _read=_read, _clock=clock) == b""
    assert clock.t == pytest.approx(115.0)


def test_reading_the_report_survives_a_broken_wait():
    def _select(r, w, x, timeout):
        raise OSError(errno.EBADF, "bad file descriptor")

    assert be._read_exec_report(7, 15.0, _select=_select, _read=lambda fd, n: b"2", _clock=_Clock()) == b""


def _drive_parent(monkeypatch, report: bytes):
    """Run the PARENT's side of the real ``PosixPtyBackend.start`` with
    stand-ins for what Windows cannot do: ``pty.fork`` (answers as the parent
    would hear it), the wait on the report pipe (always ready) and the reap.
    The report pipe is a REAL ``os.pipe``; the stand-in only writes what the
    child would have written before handing it over."""
    real_pipe, real_close = os.pipe, os.close
    master_r, master_w = real_pipe()  # stands in for the terminal's master side
    made: dict = {"closed": [], "waited": []}

    def _pipe():
        r, w = real_pipe()
        if report:
            os.write(w, report)
        made["report"] = (r, w)
        return r, w

    def _close(fd):
        made["closed"].append(fd)
        real_close(fd)

    def _waitpid(pid, flags):
        made["waited"].append(pid)
        return pid, 127 << 8

    import select as select_mod

    monkeypatch.setattr(os, "pipe", _pipe)
    monkeypatch.setattr(os, "close", _close)
    monkeypatch.setattr(os, "waitpid", _waitpid)
    monkeypatch.setattr(os, "WNOHANG", 1, raising=False)
    monkeypatch.setattr(select_mod, "select", lambda r, w, x, t=None: (list(r), [], []))
    monkeypatch.setitem(sys.modules, "pty", SimpleNamespace(fork=lambda: (4242, master_r)))
    made["master"] = (master_r, master_w)
    return made


def _close_quietly(*fds):
    for fd in fds:
        try:
            os.close(fd)
        except OSError:
            pass


def test_the_parent_raises_and_reaps_when_the_child_reports_a_missing_shell(monkeypatch):
    made = _drive_parent(monkeypatch, b"2")
    b = be.PosixPtyBackend()
    try:
        with pytest.raises(FileNotFoundError) as caught:
            b.start(["/usr/bin/fish"], "", None, 80, 24)
        assert caught.value.errno == errno.ENOENT and caught.value.filename == "/usr/bin/fish"
        # The child that could not start was reaped, nothing is kept for it ...
        assert made["waited"] and made["waited"][0] == 4242
        assert b._pid is None and b._fd is None and not b.is_alive()
        # ... and both ends of the report pipe and the terminal are closed.
        r, w = made["report"]
        assert {r, w, made["master"][0]} <= set(made["closed"])
    finally:
        monkeypatch.undo()
        _close_quietly(made["master"][1])


def test_the_parent_starts_as_before_when_the_report_pipe_closes_unread(monkeypatch):
    made = _drive_parent(monkeypatch, b"")
    b = be.PosixPtyBackend()
    try:
        b.start(["/usr/bin/fish"], "", None, 80, 24)
        assert b._pid == 4242 and b._fd == made["master"][0]
        assert made["waited"] == []  # nothing reaped: the shell is running
        r, w = made["report"]
        assert {r, w} <= set(made["closed"]) and made["master"][0] not in made["closed"]
    finally:
        b._pid = None  # the pid is a stand-in: never signal it
        monkeypatch.undo()
        _close_quietly(*made["master"])


# --------------------------------------------------------------------------- #
# Linux and Mac: the real fork and exec                                       #
# --------------------------------------------------------------------------- #


def _recording_fork(monkeypatch) -> list[int]:
    """Wrap the REAL ``pty.fork`` only to learn the child's pid."""
    import pty

    real_fork = pty.fork
    pids: list[int] = []

    def _fork():
        pid, fd = real_fork()
        if pid:
            pids.append(pid)
        return pid, fd

    monkeypatch.setattr(pty, "fork", _fork)
    return pids


def _no_such_child(pid: int) -> bool:
    try:
        os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        return True
    return False


@_POSIX_ONLY
def test_posix_a_missing_shell_raises_and_leaves_no_child(tmp_path, monkeypatch):
    pids = _recording_fork(monkeypatch)
    b = be.PosixPtyBackend()
    began = time.monotonic()
    with pytest.raises(FileNotFoundError) as caught:
        b.start([MISSING], str(tmp_path), None, 80, 24)
    assert time.monotonic() - began < 10.0, "the start waited for its bound"
    assert caught.value.errno == errno.ENOENT and caught.value.filename == MISSING
    assert b._pid is None and b._fd is None and not b.is_alive()
    assert len(pids) == 1 and _no_such_child(pids[0]), "the child that failed was not reaped"


@_POSIX_ONLY
def test_posix_a_file_that_cannot_be_run_raises_permission_error(tmp_path, monkeypatch):
    pids = _recording_fork(monkeypatch)
    not_runnable = tmp_path / "notes.txt"
    not_runnable.write_text("not a program\n", encoding="utf-8")
    os.chmod(not_runnable, 0o644)
    with pytest.raises(PermissionError) as caught:
        be.PosixPtyBackend().start([str(not_runnable)], str(tmp_path), None, 80, 24)
    assert caught.value.errno == errno.EACCES
    assert len(pids) == 1 and _no_such_child(pids[0])


@_POSIX_ONLY
def test_posix_a_real_shell_still_starts(tmp_path):
    b = be.PosixPtyBackend()
    b.start(["sh"], str(tmp_path), None, 80, 24)
    try:
        assert b.is_alive()
        b.write("echo iron-jarvis-$((40+2))\n")
        seen = b""
        end = time.monotonic() + 20
        while b"iron-jarvis-42" not in seen and time.monotonic() < end:
            seen += b.read_nonblocking()
            time.sleep(0.05)
        assert b"iron-jarvis-42" in seen
    finally:
        b.kill()


@_POSIX_ONLY
def test_posix_a_program_that_ran_and_returned_127_is_not_a_start_failure(tmp_path):
    """The pipe tells "could not start" from "ran and ended with 127"."""
    b = be.PosixPtyBackend()
    b.start(["sh", "-c", "exit 127"], str(tmp_path), None, 80, 24)  # does not raise
    end = time.monotonic() + 20
    while b.is_alive() and time.monotonic() < end:
        b.read_nonblocking()
        time.sleep(0.05)
    assert not b.is_alive() and b.exit_code == 127


@_POSIX_ONLY
def test_posix_a_restore_keeps_and_lists_a_terminal_whose_shell_is_gone(tmp_path):
    """The real restore with the real default backend: the terminal is kept,
    listed with its plain reason, and its history survives a save."""
    sp = tmp_path / "terminals.json"
    entry = {
        "id": "term_gone", "shell": "fish", "argv": ["/nonexistent/fish"], "cwd": str(tmp_path),
        "cols": 80, "rows": 24, "name": "a website",
    }
    sp.write_text(json.dumps({"terminals": [entry]}), encoding="utf-8")
    folder = tmp_path / "terminals-scrollback"
    folder.mkdir()
    (folder / "term_gone.bin").write_bytes(b"history of the website")
    m = mgr_mod.TerminalManager(state_path=sp)
    m._pty_ok = True
    try:
        assert m.rehydrate() == 0
        panes = m.restore_failed_panes()
        assert [p["id"] for p in panes] == ["term_gone"]
        assert panes[0]["reason"] == (
            "Its program, fish, is no longer on this PC (it may have been uninstalled or moved)."
        )
        m.snapshot()
        assert (folder / "term_gone.bin").is_file()
        assert [e["id"] for e in json.loads(sp.read_text(encoding="utf-8"))["terminals"]] == ["term_gone"]
    finally:
        m.kill_all()
