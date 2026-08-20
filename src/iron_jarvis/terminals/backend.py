"""PTY backends — the low-level "real shell" abstraction (§ terminal sessions).

A :class:`PtyBackend` owns a single child process attached to a pseudo-terminal
(or a plain pipe fallback). It exposes a *non-blocking* read so a single async
loop in the daemon can fan many sessions out over WebSockets without threads
per session.

Implementations:

* :class:`WinPtyBackend`  — Windows ConPTY via ``pywinpty`` (import ``winpty``).
* :class:`PosixPtyBackend` — stdlib ``pty`` fork + ``select`` non-blocking reads.
* :class:`PipeBackend`     — ``subprocess`` pipes (no real TTY) universal fallback.
* :class:`FakeBackend`     — deterministic, offline, no real process (tests).

All heavy / platform-specific imports are done lazily inside ``start`` (or the
relevant method) so this module imports cleanly on every platform.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
from typing import Protocol, runtime_checkable


@runtime_checkable
class PtyBackend(Protocol):
    """Protocol every terminal backend implements."""

    def start(
        self,
        argv: list[str],
        cwd: str,
        env: dict | None,
        cols: int,
        rows: int,
    ) -> None:
        """Spawn the child process attached to a (pseudo) terminal."""
        ...

    def write(self, data: str | bytes) -> None:
        """Send input to the child's stdin."""
        ...

    def read_nonblocking(self, max_bytes: int = 65536) -> bytes:
        """Return up to ``max_bytes`` of output, or ``b""`` if nothing is ready.

        MUST NOT block.
        """
        ...

    def resize(self, cols: int, rows: int) -> None:
        """Resize the terminal window (no-op for backends without a TTY)."""
        ...

    def is_alive(self) -> bool:
        """True while the child process is running."""
        ...

    def kill(self) -> None:
        """Forcibly terminate the child process."""
        ...

    @property
    def exit_code(self) -> int | None:
        """Exit status once the process has finished, else ``None``."""
        ...


# --------------------------------------------------------------------------- #
# Fake backend (offline, deterministic — used by the test-suite)              #
# --------------------------------------------------------------------------- #
class FakeBackend:
    """A no-real-process backend that line-buffers and echoes its input.

    Whatever is written is echoed back once a newline completes the line, so a
    ``write("hello\\n")`` followed by ``read_nonblocking()`` yields ``b"hello\\n"``.
    Partial lines stay buffered until their newline arrives.
    """

    def __init__(self) -> None:
        self._alive = False
        self._killed = False
        self._out = bytearray()
        self._line = bytearray()
        self._exit_code: int | None = None
        self.cols = 80
        self.rows = 24

    def start(
        self,
        argv: list[str],
        cwd: str,
        env: dict | None,
        cols: int,
        rows: int,
    ) -> None:
        self._alive = True
        self.cols = cols
        self.rows = rows

    def write(self, data: str | bytes) -> None:
        if isinstance(data, str):
            data = data.encode("utf-8")
        for byte in data:
            self._line.append(byte)
            if byte == 0x0A:  # "\n" — flush the completed line
                self._out += self._line
                self._line.clear()

    def read_nonblocking(self, max_bytes: int = 65536) -> bytes:
        if not self._out:
            return b""
        chunk = bytes(self._out[:max_bytes])
        del self._out[:max_bytes]
        return chunk

    def resize(self, cols: int, rows: int) -> None:
        self.cols = cols
        self.rows = rows

    def is_alive(self) -> bool:
        return self._alive

    def kill(self) -> None:
        self._alive = False
        self._killed = True
        if self._exit_code is None:
            self._exit_code = -9  # SIGKILL-ish sentinel

    @property
    def exit_code(self) -> int | None:
        return self._exit_code


# --------------------------------------------------------------------------- #
# Windows ConPTY backend (pywinpty)                                           #
# --------------------------------------------------------------------------- #
class WinPtyBackend:
    """Windows ConPTY backend built on ``pywinpty`` (``import winpty``).

    ``PtyProcess`` runs a daemon reader thread that forwards the PTY output to a
    loopback socket; we read that socket in non-blocking mode so this stays
    cooperative with a single async poll loop.
    """

    def __init__(self) -> None:
        self._proc = None  # winpty.PtyProcess

    def start(
        self,
        argv: list[str],
        cwd: str,
        env: dict | None,
        cols: int,
        rows: int,
    ) -> None:
        import winpty  # lazy: only importable / needed on Windows

        # pywinpty dimensions are (rows, cols).
        self._proc = winpty.PtyProcess.spawn(
            list(argv),
            cwd=cwd or None,
            env=env,
            dimensions=(rows, cols),
        )
        # Make the forwarding socket non-blocking so reads never stall.
        try:
            self._proc.fileobj.setblocking(False)
        except Exception:  # pragma: no cover - defensive
            pass

    def write(self, data: str | bytes) -> None:
        if self._proc is None:
            raise RuntimeError("backend not started")
        if isinstance(data, bytes):
            data = data.decode("utf-8", "replace")
        try:
            self._proc.write(data)
        except (EOFError, OSError):
            # Writing to a DEAD PTY (shell exited/crashed) must not explode —
            # an uncaught EOFError here crashed the whole WS handler, putting
            # the pane into a crash->reconnect loop (live-hit 2026-07-01).
            pass

    def read_nonblocking(self, max_bytes: int = 65536) -> bytes:
        if self._proc is None:
            return b""
        try:
            data = self._proc.fileobj.recv(max_bytes)
        except (BlockingIOError, InterruptedError):
            return b""
        except OSError:  # pragma: no cover - socket torn down
            return b""
        if data == b"0011Ignore":  # pywinpty keep-alive sentinel
            return b""
        return data

    def resize(self, cols: int, rows: int) -> None:
        if self._proc is None:
            return
        try:
            self._proc.setwinsize(rows, cols)  # (rows, cols)
        except Exception:  # pragma: no cover - defensive
            pass

    def is_alive(self) -> bool:
        if self._proc is None:
            return False
        try:
            return bool(self._proc.isalive())
        except Exception:  # pragma: no cover - defensive
            return False

    def kill(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.terminate(force=True)
        except Exception:  # pragma: no cover - defensive
            pass

    @property
    def exit_code(self) -> int | None:
        if self._proc is None:
            return None
        try:
            return self._proc.exitstatus
        except Exception:  # pragma: no cover - defensive
            return None


# --------------------------------------------------------------------------- #
# POSIX PTY backend (stdlib pty + select)                                     #
# --------------------------------------------------------------------------- #
class PosixPtyBackend:
    """POSIX pseudo-terminal backend using ``pty.fork`` + ``select``."""

    def __init__(self) -> None:
        self._pid: int | None = None
        self._fd: int | None = None
        self._exit_code: int | None = None

    def start(
        self,
        argv: list[str],
        cwd: str,
        env: dict | None,
        cols: int,
        rows: int,
    ) -> None:
        import pty as _pty  # lazy: POSIX-only

        argv = list(argv)
        child_env = dict(env) if env is not None else os.environ.copy()
        pid, fd = _pty.fork()
        if pid == 0:  # pragma: no cover - child process, never measured
            try:
                if cwd:
                    os.chdir(cwd)
                os.execvpe(argv[0], argv, child_env)
            except Exception:
                os._exit(127)
        # parent
        self._pid = pid
        self._fd = fd
        try:
            os.set_blocking(fd, False)
        except Exception:  # pragma: no cover - defensive
            pass
        self.resize(cols, rows)

    def write(self, data: str | bytes) -> None:
        if self._fd is None:
            raise RuntimeError("backend not started")
        if isinstance(data, str):
            data = data.encode("utf-8")
        try:
            os.write(self._fd, data)
        except (BlockingIOError, OSError):  # pragma: no cover - pipe closed
            pass

    def read_nonblocking(self, max_bytes: int = 65536) -> bytes:
        if self._fd is None:
            return b""
        import select

        try:
            ready, _, _ = select.select([self._fd], [], [], 0)
        except (OSError, ValueError):  # pragma: no cover - fd closed
            return b""
        if not ready:
            return b""
        try:
            return os.read(self._fd, max_bytes)
        except (BlockingIOError, InterruptedError):
            return b""
        except OSError:  # pragma: no cover - EOF / closed
            return b""

    def resize(self, cols: int, rows: int) -> None:
        if self._fd is None:
            return
        try:  # pragma: no cover - exercised only on POSIX with a real TTY
            import fcntl
            import struct
            import termios

            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self._fd, termios.TIOCSWINSZ, winsize)
        except Exception:
            pass

    def is_alive(self) -> bool:
        if self._pid is None:
            return False
        try:
            pid, status = os.waitpid(self._pid, os.WNOHANG)
        except (ChildProcessError, OSError):
            # Not our child any more (already reaped, or never was) — the pid is
            # not ours to signal, so forget it for the same reason as below.
            self._pid = None
            return False
        if pid == 0:
            return True  # still running: KEEP the pid, it is still valid
        if os.WIFEXITED(status):
            self._exit_code = os.WEXITSTATUS(status)
        elif os.WIFSIGNALED(status):  # pragma: no cover
            self._exit_code = -os.WTERMSIG(status)
        # THIS reap frees the pid, exactly like the one in `kill()`, and it is
        # the COMMON path: a shell that exits by itself is reaped here by the
        # session's background drain thread, `purge_dead` RETAINS the dead
        # session, and `kill_all()` at daemon shutdown calls `kill()` on it with
        # no aliveness check. Forget the pid so that call signals nothing — the
        # OS may have handed it to an unrelated process by then, and the group
        # guard (`pgid == self._pid`) accepts exactly the process-group LEADERS,
        # so a stray killpg would SIGKILL a whole unrelated group the user owns.
        # `exit_code` reads `_exit_code`, which is already recorded above.
        self._pid = None
        return False

    def kill(self) -> None:
        if self._pid is None:
            return
        import signal

        # Snapshot the pid ONCE. `is_alive()` runs on the session's background
        # drain thread and now clears `_pid` too, so re-reading `self._pid` on
        # each line below could hand `os.getpgid` a None mid-call — a TypeError,
        # which the `except OSError` handlers do NOT catch and which would
        # escape `kill()` and abort `kill_all()`'s loop over the other sessions.
        pid = self._pid
        # `pty.fork` calls setsid, so the child LEADS its own session/group and
        # everything it launched is in that group — signal the group so a shell's
        # children die with it instead of orphaning. Only ever when the child is
        # confirmed the group leader: otherwise the group is the DAEMON's own and
        # killpg would take the daemon down with it.
        killed_group = False
        try:
            pgid = os.getpgid(pid)
            if pgid == pid and pgid != os.getpgid(0):
                os.killpg(pgid, signal.SIGKILL)
                killed_group = True
        except OSError:
            pass
        try:
            if not killed_group:
                os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        try:  # reap so we don't leak a zombie
            os.waitpid(pid, 0)
        except OSError:
            pass
        # The pid is FREED by that reap and the OS may hand it to an unrelated
        # process — and `kill()` is called twice for real on every session (the
        # manager kills a closed pane, `purge_dead` RETAINS the dead session and
        # `kill_all` kills it again at shutdown, possibly hours later). Forget it
        # so the second call returns at the guard above and signals nothing:
        # `os.getpgid` on a recycled pid would otherwise pass the
        # `pgid == self._pid` test for exactly the process-group LEADERS and
        # SIGKILL a whole unrelated group the user owns. `is_alive()` already
        # reports False on None and `exit_code` reads `_exit_code`, which this
        # method never set.
        self._pid = None

    @property
    def exit_code(self) -> int | None:
        return self._exit_code


# --------------------------------------------------------------------------- #
# Windows process-TREE teardown (used by the pipe fallback)                   #
# --------------------------------------------------------------------------- #
# ``Popen.kill`` is ``TerminateProcess`` on Windows: it kills exactly ONE
# process. A pipe shell's children — an AI CLI launched by Creative Studio,
# ffmpeg, a build — survive as orphans with no pane, no tail and no way to stop
# them from the app; a daemon restart-to-update in degraded mode leaves them
# running unattended. ConPTY has no such hole (closing the pseudoconsole tears
# down the whole attached console tree), so the pipe fallback is the one path
# that must tear the tree down itself.
#
# Preferred mechanism: put the shell in a Job object created with
# KILL_ON_JOB_CLOSE — every descendant it spawns joins the job automatically,
# ``TerminateJobObject`` kills them all at once, and if the daemon dies without
# calling ``kill`` at all the handle closes with the process and the tree still
# goes down. Fallback (a job could not be created): ``taskkill /T /F /PID``.

_WINDOWS = sys.platform == "win32"

_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9  # JOBOBJECTINFOCLASS


def _win_job_for(process_handle: int) -> int | None:
    """Create a kill-on-close Job and assign ``process_handle`` to it.

    Returns the job handle, or ``None`` when jobs are unavailable (old Windows
    refusing a nested job, a locked-down container) — callers fall back to
    ``taskkill``.
    """
    try:  # pragma: no cover - exercised only on a real Windows spawn
        import ctypes

        class _BASIC(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_void_p),
                ("MaximumWorkingSetSize", ctypes.c_void_p),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_void_p),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32),
            ]

        class _IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )]

        class _EXTENDED(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BASIC),
                ("IoInfo", _IO),
                ("ProcessMemoryLimit", ctypes.c_void_p),
                ("JobMemoryLimit", ctypes.c_void_p),
                ("PeakProcessMemoryUsed", ctypes.c_void_p),
                ("PeakJobMemoryUsed", ctypes.c_void_p),
            ]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # HANDLE is pointer-sized: the default c_int restype would truncate it.
        k32.CreateJobObjectW.restype = ctypes.c_void_p
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _EXTENDED()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = k32.SetInformationJobObject(
            ctypes.c_void_p(job),
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if ok:
            ok = k32.AssignProcessToJobObject(
                ctypes.c_void_p(job), ctypes.c_void_p(process_handle)
            )
        if not ok:
            k32.CloseHandle(ctypes.c_void_p(job))
            return None
        return int(job)
    except Exception:  # pragma: no cover - defensive
        return None


def _win_terminate_job(job: int) -> bool:
    """``TerminateJobObject`` + close the handle. True when the job was killed."""
    try:  # pragma: no cover - exercised only on a real Windows spawn
        import ctypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        ok = bool(k32.TerminateJobObject(ctypes.c_void_p(job), 1))
        k32.CloseHandle(ctypes.c_void_p(job))
        return ok
    except Exception:  # pragma: no cover - defensive
        return False


def _win_taskkill_tree(pid: int) -> bool:
    """Fallback tree kill: ``taskkill /T /F /PID <pid>`` (bounded, never raises)."""
    try:
        proc = subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return proc.returncode == 0
    except Exception:  # pragma: no cover - taskkill missing / timed out
        return False


def _tree_kill(proc: subprocess.Popen, job: int | None, alive: bool = True) -> bool:
    """Kill ``proc`` AND everything it launched. No-op off Windows.

    POSIX callers tear the tree down with ``killpg`` instead (see
    :meth:`PosixPtyBackend.kill`).

    ``alive`` must be False once the shell has exited. The job is handle-based
    and so immune to pid reuse, but the ``taskkill /T /F /PID`` fallback is NOT:
    Windows RECYCLES pids, and ``kill()`` is routinely called twice on the same
    session (a pane closed via ``DELETE /terminals/{id}`` is retained by
    ``purge_dead`` and killed again by ``kill_all`` at shutdown), so taskkilling
    a pid freed hours ago would force-kill an unrelated process TREE the user
    owns. Nothing is lost by skipping it: ``/T`` walks LIVE parent-pid links, so
    once the shell is gone its orphaned grandchildren are unreachable that way
    anyway — only the job can still reach them.
    """
    if not _WINDOWS:
        return False
    if job is not None and _win_terminate_job(job):
        return True
    if not alive:
        return False
    return _win_taskkill_tree(proc.pid)


# --------------------------------------------------------------------------- #
# Pipe backend (subprocess; no real TTY) — universal fallback                 #
# --------------------------------------------------------------------------- #
class PipeBackend:
    """Universal fallback: a ``subprocess.Popen`` with merged stdout/stderr.

    There is no real PTY, so ``resize`` is a no-op. A reader thread drains the
    child's output into a queue so ``read_nonblocking`` never blocks.
    """

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._queue: "queue.Queue[bytes]" = queue.Queue()
        self._thread: threading.Thread | None = None
        #: Windows Job handle owning the shell + every process it spawns.
        self._job: int | None = None

    def start(
        self,
        argv: list[str],
        cwd: str,
        env: dict | None,
        cols: int,
        rows: int,
    ) -> None:
        self._proc = subprocess.Popen(
            list(argv),
            cwd=cwd or None,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )
        if _WINDOWS:
            # Assign immediately: a shell takes far longer to reach its prompt
            # than this takes, so nothing it launches escapes the job. The
            # taskkill fallback in `kill` covers a job we could not create.
            handle = getattr(self._proc, "_handle", None)
            if handle is not None:
                self._job = _win_job_for(int(handle))
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()

    def _reader(self) -> None:
        out = self._proc.stdout if self._proc else None
        if out is None:
            return
        try:
            while True:
                chunk = out.read(4096)
                if not chunk:
                    break
                self._queue.put(chunk)
        except Exception:  # pragma: no cover - pipe torn down
            pass

    def write(self, data: str | bytes) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("backend not started")
        if isinstance(data, str):
            data = data.encode("utf-8")
        try:
            self._proc.stdin.write(data)
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError):  # pragma: no cover - child gone
            pass

    def read_nonblocking(self, max_bytes: int = 65536) -> bytes:
        buf = bytearray()
        while len(buf) < max_bytes:
            try:
                buf += self._queue.get_nowait()
            except queue.Empty:
                break
        return bytes(buf)

    def resize(self, cols: int, rows: int) -> None:
        # No TTY behind a pipe — nothing to resize.
        return None

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def kill(self) -> None:
        if self._proc is None:
            return
        # Tear down the TREE first — `Popen.kill` alone leaves every process the
        # shell launched running with no pane and no way to stop it. The
        # pid-based fallback only ever fires while the shell is STILL RUNNING:
        # this method is called again at shutdown on sessions killed long ago
        # and Windows recycles pids (see `_tree_kill`).
        try:
            alive = self._proc.poll() is None
        except Exception:  # pragma: no cover - defensive
            alive = False
        try:
            _tree_kill(self._proc, self._job, alive)
        except Exception:  # pragma: no cover - defensive
            pass
        self._job = None  # handle closed by _win_terminate_job
        try:
            self._proc.kill()
        except Exception:  # pragma: no cover - defensive
            pass

    @property
    def exit_code(self) -> int | None:
        if self._proc is None:
            return None
        return self._proc.poll()


def default_backend() -> PtyBackend:
    """Pick the best backend for this OS *without* spawning anything.

    Windows → :class:`WinPtyBackend` (if ``winpty`` importable) else
    :class:`PipeBackend`; POSIX → :class:`PosixPtyBackend`; otherwise
    :class:`PipeBackend`.
    """
    if sys.platform == "win32":
        try:
            import importlib.util

            if importlib.util.find_spec("winpty") is not None:
                return WinPtyBackend()
        except Exception:  # pragma: no cover - defensive
            pass
        return PipeBackend()
    if os.name == "posix":
        return PosixPtyBackend()
    return PipeBackend()  # pragma: no cover - exotic platforms
