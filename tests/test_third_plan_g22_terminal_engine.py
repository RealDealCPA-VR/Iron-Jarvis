"""Third north-star plan, group 22: the terminal engine (TM7, TM9, TM10, TM11).

* TM7  - opening a terminal in a folder that is gone answered the raw Windows
  error. The folder is now checked (bounded, off the spawn) before any
  process starts, and the refusal is a plain sentence naming the folder.
* TM9  - a terminal an agent opens on a POLLED backend (Linux, Mac, the
  Windows fallbacks) had no reader, so ``pane_read`` said it printed nothing.
* TM10 - the older Windows fallback (pywinpty) closed only the shell, not the
  programs it started.
* TM11 - one shell that exits at once marked the terminal engine broken for
  the daemon's life. The session now says whether a shell that ended ENDED BY
  ITSELF (the terminal worked) or never showed the terminal working.

No real shell is started anywhere here: every backend is a stand-in, or a real
backend refused before it spawns anything (a folder that does not exist).
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

import iron_jarvis.terminals.backend as be
import iron_jarvis.terminals.manager as mgr_mod
import iron_jarvis.terminals.session as sess_mod
from iron_jarvis.terminals import FakeBackend, TerminalManager, TerminalSession
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.pane_tools import PaneReadTool, PaneSpawnTool

#: The longest a test waits for a reader thread to hand out its bytes. Only a
#: RED run ever waits this long; a green run returns the moment it happens.
_BOUND_S = 5.0


def _ctx(tmp_path):
    return ToolContext(
        workspace=tmp_path,
        session_id="s",
        agent_run_id="r",
        config=None,
        event_bus=None,
        engine=None,
    )


def _engine_words(text: str) -> list[str]:
    bad = ("WinError", "Errno", "Error:", "OSError", "FileNotFound", "NotADirectory", "Traceback")
    return [w for w in bad if w in text]


# --------------------------------------------------------------------------- #
# TM7: a folder that is gone                                                   #
# --------------------------------------------------------------------------- #


def test_tm7_opening_a_terminal_in_a_gone_folder_says_so_in_plain_words(tmp_path):
    """Through the REAL ``TerminalManager.create`` and the real pipe backend
    (the universal fallback, so this runs on every OS): the folder check runs
    before ``subprocess.Popen``, so nothing is started, and the refusal is a
    sentence the routes already pass on (``ShellStartStuck`` -> its words)."""
    gone = tmp_path / "a-project-that-was-deleted"
    m = TerminalManager()
    with pytest.raises(Exception) as caught:
        m.create(cwd=str(gone), backend=be.PipeBackend())
    exc = caught.value
    text = str(exc)
    assert isinstance(exc, be.ShellStartStuck), f"{type(exc).__name__}: {text}"
    assert isinstance(exc, be.StartFolderGone)
    assert str(gone) in text
    assert "no longer exists" in text and "no terminal was opened" in text
    assert _engine_words(text) == []
    # Nothing was registered and the cap slot came back.
    assert m.list() == [] and m._reserved == 0 and m._pending_panes == set()


@pytest.mark.parametrize("cls", [be.PipeBackend, be.ConPtyBackend, be.PosixPtyBackend, be.WinPtyBackend])
def test_tm7_every_real_backend_checks_the_folder_before_it_starts_anything(tmp_path, monkeypatch, cls):
    """ONE shared check, at the top of every real backend's ``start``. Every
    way of starting a process is made to explode, so a backend that reached
    one before the check fails this test with that explosion."""

    def _boom(*a, **k):  # pragma: no cover - reached only when the check is missing
        raise AssertionError("a process start was attempted before the folder check")

    monkeypatch.setattr(be.subprocess, "Popen", _boom)
    monkeypatch.setattr(be, "_conpty_api", _boom)
    gone = tmp_path / "gone"
    with pytest.raises(be.StartFolderGone) as caught:
        cls().start(["sh"], str(gone), None, 80, 24)
    assert str(gone) in str(caught.value)


def test_tm7_a_file_is_not_a_folder(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(be.StartFolderGone) as caught:
        be.check_start_folder(str(f))
    text = str(caught.value)
    assert "not a folder" in text and str(f) in text and _engine_words(text) == []


def test_tm7_a_folder_that_does_not_answer_is_refused_within_the_bound(tmp_path):
    """A drive that does not answer must not hang the caller: the stat runs on
    its own thread and the check gives up after its bound, in plain words."""
    release = threading.Event()

    def _hung_stat(path):
        release.wait(_BOUND_S)
        raise FileNotFoundError(path)

    try:
        with pytest.raises(be.StartFolderGone) as caught:
            be.check_start_folder(r"Z:\shared\project", wait_s=0.05, _stat=_hung_stat)
    finally:
        release.set()
    text = str(caught.value)
    assert "not answering" in text and "no terminal was opened" in text
    assert _engine_words(text) == []


def test_tm7_a_folder_that_exists_passes_and_no_folder_means_the_default(tmp_path):
    be.check_start_folder(str(tmp_path))
    be.check_start_folder("")  # the backends start in the daemon's folder then
    be.check_start_folder(None)


# --------------------------------------------------------------------------- #
# TM9: a terminal an agent opens on a polled backend                           #
# --------------------------------------------------------------------------- #


class _PolledBackend(FakeBackend):
    """A backend that must be POLLED (no ``pushes_output``), like the POSIX
    PTY, pywinpty and the pipe shell. It prints a banner the moment it starts
    and says when a reader has taken each line."""

    def __init__(self) -> None:
        super().__init__()
        self.banner_taken = threading.Event()
        self.second_taken = threading.Event()

    def start(self, argv, cwd, env, cols, rows) -> None:
        super().start(argv, cwd, env, cols, rows)
        self._out += b"hello from the new terminal\r\n"

    def read_nonblocking(self, max_bytes: int = 65536) -> bytes:
        data = super().read_nonblocking(max_bytes)
        if b"hello from the new terminal" in data:
            self.banner_taken.set()
        if b"second line" in data:
            self.second_taken.set()
        return data


def _drain_threads(session) -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == f"drain-{session.id}" and t.is_alive()]


async def test_tm9_a_terminal_an_agent_opens_is_read_with_no_page_attached(tmp_path, monkeypatch):
    """``pane_spawn`` through the REAL manager on a polled backend, nobody
    attached: the output is recorded, so ``pane_read`` returns it (it said
    "(the pane has printed nothing yet)" before). Then a page attaches: still
    exactly ONE reader, and the page sees each later line once."""
    backend = _PolledBackend()
    monkeypatch.setattr(sess_mod, "default_backend", lambda: backend)
    m = TerminalManager()
    m._pty_ok = True  # the engine is known good: no first-start check wait
    spawned = await PaneSpawnTool(m).execute({"name": "builder"}, _ctx(tmp_path))
    assert spawned.ok, spawned.error
    session = m.get(spawned.data["pane"]["id"])
    try:
        assert backend.banner_taken.wait(_BOUND_S), "nothing ever read the new terminal"
        with session._read_lock:  # the reader records under this lock
            pass
        read = await PaneReadTool(m).execute({"pane": "builder"}, _ctx(tmp_path))
        assert read.ok
        assert "hello from the new terminal" in read.output
        assert "printed nothing" not in read.output
        assert len(_drain_threads(session)) == 1

        # A page attaches later: no second reader, and each byte once.
        history, sub = session.subscribe()
        assert b"hello from the new terminal" in history
        session.write("second line\n")
        # The page's own pump reads too (as the WebSocket route does); every
        # chunk reaches every attached page once, whoever read it.
        while not backend.second_taken.is_set():
            session.read()
            if backend.second_taken.wait(0.01):
                break
        with session._read_lock:
            pass
        live = sub.take()
        assert live.count(b"second line") == 1
        assert b"hello from the new terminal" not in live
        assert len(_drain_threads(session)) == 1
        session.unsubscribe(sub)  # the last one out: the drain is already there
        assert len(_drain_threads(session)) == 1
    finally:
        session.kill()
    thread = session._drain_thread
    assert thread is not None
    thread.join(_BOUND_S)
    assert not thread.is_alive(), "the reader must stop when the terminal is closed"


def test_tm9_the_reader_keeps_what_a_shell_printed_as_it_ended():
    """A shell that prints its last line and exits between two reads: the
    reader records what is left before it stops, so ``pane_read`` shows it."""

    class _Ends(FakeBackend):
        def start(self, argv, cwd, env, cols, rows) -> None:
            super().start(argv, cwd, env, cols, rows)
            self._out += b"last words\r\n"
            self._alive = False  # ended before any reader ran
            self._exit_code = 0

    s = TerminalSession(cwd="/work", shell="fake", argv=["fake"], backend=_Ends()).start()
    s.start_autodrain()
    s._drain_thread.join(_BOUND_S)
    assert "last words" in s.output_tail()


def test_tm9_a_pushing_backend_gets_no_drain():
    class _Pushing(FakeBackend):
        pushes_output = True

        def set_output_handler(self, on_output, on_eof=None) -> None:
            pass

    s = TerminalSession(cwd="/work", shell="fake", argv=["fake"], backend=_Pushing()).start()
    s.start_autodrain()
    assert s._drain_thread is None
    s.kill()


# --------------------------------------------------------------------------- #
# TM10: the pywinpty fallback closes the whole tree                            #
# --------------------------------------------------------------------------- #


class _WinptyProc:
    """Stands in for ``winpty.PtyProcess``: records what was done to it."""

    def __init__(self, pid: int = 4242, alive: bool = True) -> None:
        self.pid = pid
        self._alive = alive
        self.terminated = 0
        self.exitstatus = None

    def isalive(self) -> bool:
        return self._alive

    def terminate(self, force: bool = False) -> bool:
        self.terminated += 1
        self._alive = False
        return True


def _record_tree_kills(monkeypatch) -> dict[str, list]:
    seen: dict[str, list] = {"job": [], "taskkill": []}
    monkeypatch.setattr(be, "_WINDOWS", True)
    monkeypatch.setattr(be, "_win_terminate_job", lambda job: seen["job"].append(job) or True)
    monkeypatch.setattr(be, "_win_taskkill_tree", lambda pid: seen["taskkill"].append(pid) or True)
    return seen


def test_tm10_closing_a_pywinpty_terminal_kills_what_its_shell_started(monkeypatch):
    """No job (one could not be made): the shell is still running, so the
    tree is ended by its pid (``taskkill /T``), the pipe shell's own way."""
    seen = _record_tree_kills(monkeypatch)
    b = be.WinPtyBackend()
    b._proc = _WinptyProc(pid=4242)
    b.kill()
    assert seen["taskkill"] == [4242], "closing ended only the shell, not its programs"
    assert b._proc.terminated == 1  # and the shell itself, as before


def test_tm10_the_pywinpty_shell_is_put_in_a_job_and_the_job_is_ended(monkeypatch):
    """At start the shell joins a kill-on-close job (everything it starts
    joins too); closing ends the job, once — a second close (``kill_all`` at
    shutdown) finds no job and, the shell being gone, never kills by pid."""
    import sys
    import types

    seen = _record_tree_kills(monkeypatch)
    proc = _WinptyProc(pid=5151)
    fake = types.ModuleType("winpty")
    fake.PtyProcess = types.SimpleNamespace(spawn=lambda *a, **k: proc)
    proc.fileobj = types.SimpleNamespace(setblocking=lambda flag: None)
    monkeypatch.setitem(sys.modules, "winpty", fake)
    jobs: list[int] = []
    monkeypatch.setattr(be, "_win_job_for_pid", lambda pid: jobs.append(pid) or 777)
    b = be.WinPtyBackend()
    b.start(["shell"], str(Path.cwd()), None, 80, 24)
    assert jobs == [5151]
    b.kill()
    assert seen["job"] == [777] and seen["taskkill"] == []
    b.kill()  # the second close: no job left, the shell is gone
    assert seen["job"] == [777] and seen["taskkill"] == []


def test_tm10_a_shell_that_already_ended_is_never_killed_by_its_old_pid(monkeypatch):
    """Windows reuses pids: ``taskkill`` on a pid freed long ago could end an
    unrelated program, so only a running shell is ever killed by pid."""
    seen = _record_tree_kills(monkeypatch)
    b = be.WinPtyBackend()
    b._proc = _WinptyProc(pid=4242, alive=False)
    b.kill()
    assert seen["taskkill"] == []


# --------------------------------------------------------------------------- #
# TM11: a shell that ends at once is not a broken terminal engine               #
# --------------------------------------------------------------------------- #


class _ExitsAtOnce(FakeBackend):
    def __init__(self, code: int | None, printed: bytes = b"") -> None:
        super().__init__()
        self._code = code
        self._printed = printed

    def start(self, argv, cwd, env, cols, rows) -> None:
        super().start(argv, cwd, env, cols, rows)
        self._out += self._printed
        self._alive = False
        self._exit_code = self._code


def _ended(code, printed=b"") -> TerminalSession:
    return TerminalSession(
        cwd="/work", shell="fake", argv=["fake"], backend=_ExitsAtOnce(code, printed)
    ).start()


def test_tm11_a_shell_that_exits_0_at_once_ended_by_itself():
    assert _ended(0).ended_by_itself() is True
    assert _ended(1).ended_by_itself() is True  # a profile script that failed
    assert _ended(127).ended_by_itself() is True  # a shell path that is wrong


def test_tm11_a_shell_that_printed_before_it_ended_proves_the_terminal_works():
    s = _ended(None, b"PS C:\\work> exit\r\n")
    assert s.ended_by_itself() is True
    assert "exit" in s.output_tail()  # what it left was recorded


def test_tm11_a_silent_death_still_reads_as_a_terminal_that_failed():
    """The v1.190.0 failure the fallback exists for: no console host, the
    shell dies silently. No output and no exit code, or a Windows start
    failure code (0xC0000142: the program could not start in its console)."""
    assert _ended(None).ended_by_itself() is False
    assert _ended(0xC0000142).ended_by_itself() is False


def test_tm11_a_running_or_closed_shell_did_not_end_by_itself():
    s = TerminalSession(cwd="/work", shell="fake", argv=["fake"], backend=FakeBackend()).start()
    assert s.ended_by_itself() is False  # still running
    s.kill()
    assert s.ended_by_itself() is False  # WE ended it


def test_tm11_one_shell_exiting_at_once_does_not_mark_terminals_broken(monkeypatch):
    monkeypatch.setattr(mgr_mod, "_PTY_VERIFY_SECONDS", 0.1)
    monkeypatch.setattr(mgr_mod, "PipeBackend", FakeBackend)  # never a real pipe shell
    monkeypatch.setattr(sess_mod, "default_backend", lambda: _ExitsAtOnce(0))
    m = TerminalManager()
    first = m.create()
    assert m._pty_ok is not False, "a shell's own exit marked the terminal engine broken"
    assert first.degraded is False
    monkeypatch.setattr(sess_mod, "default_backend", lambda: FakeBackend())
    second = m.create()
    assert second.degraded is False
