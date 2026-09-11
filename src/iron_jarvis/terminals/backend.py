"""PTY backends — the low-level "real shell" abstraction (§ terminal sessions).

A :class:`PtyBackend` owns a single child process attached to a pseudo-terminal
(or a plain pipe fallback). It exposes a *non-blocking* read so a single async
loop in the daemon can fan many sessions out over WebSockets. A backend that
sets ``pushes_output`` (v1.248.0) instead delivers every chunk from its own
reader thread to the handler the session installs, and is never polled.

Implementations:

* :class:`ConPtyBackend`  — Windows ConPTY straight from kernel32 (ctypes),
  bytes in and out, pushing (v1.248.0; the Windows default).
* :class:`WinPtyBackend`  — Windows ConPTY via ``pywinpty`` (import ``winpty``),
  the fallback (``IRONJARVIS_PTY_BACKEND=pywinpty`` forces it).
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
        # Delete exactly what was taken: the background drain reads from its
        # own thread, and a write landing between the two lines would
        # otherwise be deleted unread.
        del self._out[: len(chunk)]
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


# --------------------------------------------------------------------------- #
# Windows ConPTY, raw — kernel32 through ctypes (v1.248.0)                    #
# --------------------------------------------------------------------------- #
# WHY NOT pywinpty ANY MORE (it stays as the fallback). Measured on 2026-09-11:
# (1) it hands output over as TEXT and re-encodes it, so a multi-byte
# character split between two reads became U+FFFD (121 per 8.46 MB of mixed
# box-drawing/CJK output in the audit, 3 per 10 MB in the v1.248 bench) — raw
# ConPTY output is UTF-8 bytes and this backend never decodes a byte; (2) it
# can only be POLLED (a loopback socket), which cost every attached pane 100
# wakeups a second — 8 idle panes measured a whole CPU core. Here a blocking
# reader thread per pane hands each chunk to the session the moment it exists
# (``pushes_output``), and an idle pane costs nothing.
#
# WHICH CONSOLE HOST. The three pseudoconsole calls exist twice on this
# machine: in kernel32 (the INBOX conhost, Windows 10 1809+) and in the
# ``conpty.dll`` pywinpty ships beside Windows Terminal's ``OpenConsole.exe``
# (both already in the .spec for the pywinpty backend). Measured 2026-09-11,
# keystroke echo in pwsh (PSReadLine), in-process: inbox conhost 14.8 ms
# median, bundled OpenConsole 0.5 ms (pywinpty 2.0 ms) — the inbox host paints
# on a frame timer. So the bundled host is used when BOTH files are present
# and the inbox one otherwise; ``IRONJARVIS_CONPTY_HOST=inbox`` forces
# kernel32. Either way it is ctypes only: no native dependency, nothing new
# for the .spec.

#: ``IRONJARVIS_PTY_BACKEND``: ``pywinpty`` forces the pywinpty backend,
#: ``pipe`` the pipe shell; anything else (or unset) picks the best available.
PTY_BACKEND_ENV = "IRONJARVIS_PTY_BACKEND"

#: ``IRONJARVIS_CONPTY_HOST=inbox`` makes the raw backend use kernel32's
#: ConPTY (the inbox conhost) even when the bundled OpenConsole is present.
CONPTY_HOST_ENV = "IRONJARVIS_CONPTY_HOST"

#: Set when a ConPTY could not be CREATED in this process (not a bad command
#: or folder — the pseudoconsole itself). Every later pane then skips straight
#: to pywinpty instead of failing the same way; see ``mark_conpty_broken``.
_CONPTY_BROKEN = False

_PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_STARTF_USESTDHANDLES = 0x00000100
_INFINITE = 0xFFFFFFFF
_CONPTY_PIPE_BYTES = 64 * 1024
_CONPTY_READ_BYTES = 64 * 1024

_CONPTY_API = None


class ConPtyUnavailable(OSError):
    """The pseudoconsole itself could not be made — as opposed to a command
    or folder that could not be started, which is the caller's problem and
    must not disable ConPTY for every later pane."""


def _conpty_api():
    """kernel32's ConPTY surface, typed, on a PRIVATE ``WinDLL`` — argtypes set
    here never leak into another ctypes user in the process (``_win_job_for``
    has its own). Built once and lazily, so this module imports on every OS."""
    global _CONPTY_API
    if _CONPTY_API is not None:
        return _CONPTY_API
    import ctypes
    from ctypes import wintypes
    from types import SimpleNamespace

    class COORD(ctypes.Structure):
        _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.c_void_p),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class STARTUPINFOEXW(ctypes.Structure):
        _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    H, D, B = wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL
    vp = ctypes.c_void_p
    k.CreatePipe.argtypes = [ctypes.POINTER(H), ctypes.POINTER(H), vp, D]
    k.CreatePipe.restype = B
    k.CreatePseudoConsole.argtypes = [COORD, H, H, D, ctypes.POINTER(vp)]
    k.CreatePseudoConsole.restype = ctypes.c_long  # HRESULT
    k.ResizePseudoConsole.argtypes = [vp, COORD]
    k.ResizePseudoConsole.restype = ctypes.c_long
    k.ClosePseudoConsole.argtypes = [vp]
    k.ClosePseudoConsole.restype = None
    k.InitializeProcThreadAttributeList.argtypes = [vp, D, D, ctypes.POINTER(ctypes.c_size_t)]
    k.InitializeProcThreadAttributeList.restype = B
    k.UpdateProcThreadAttribute.argtypes = [vp, D, ctypes.c_size_t, vp, ctypes.c_size_t, vp, vp]
    k.UpdateProcThreadAttribute.restype = B
    k.DeleteProcThreadAttributeList.argtypes = [vp]
    k.DeleteProcThreadAttributeList.restype = None
    k.CreateProcessW.argtypes = [
        wintypes.LPCWSTR, vp, vp, vp, B, D, vp, wintypes.LPCWSTR, vp, vp,
    ]
    k.CreateProcessW.restype = B
    k.ReadFile.argtypes = [H, vp, D, ctypes.POINTER(D), vp]
    k.ReadFile.restype = B
    k.WriteFile.argtypes = [H, vp, D, ctypes.POINTER(D), vp]
    k.WriteFile.restype = B
    k.CloseHandle.argtypes = [H]
    k.CloseHandle.restype = B
    k.WaitForSingleObject.argtypes = [H, D]
    k.WaitForSingleObject.restype = D
    k.GetExitCodeProcess.argtypes = [H, ctypes.POINTER(D)]
    k.GetExitCodeProcess.restype = B
    k.TerminateProcess.argtypes = [H, wintypes.UINT]
    k.TerminateProcess.restype = B
    calls, host, host_dir = k, "inbox", None
    host_pref = os.environ.get(CONPTY_HOST_ENV, "").strip().lower()
    if host_pref != "inbox":
        host_dir = _bundled_conpty_dir()
        if host_dir is not None:
            try:
                calls = _BundledConpty(k, ctypes.WinDLL(os.path.join(host_dir, "conpty.dll")))
                host = "bundled"
            except Exception:  # a DLL that will not load: the inbox host still works
                _log().warning("bundled conpty.dll unusable; using the inbox conhost", exc_info=True)
                calls, host_dir = k, None
    _CONPTY_API = SimpleNamespace(
        ctypes=ctypes,
        wintypes=wintypes,
        k32=calls,
        host=host,  # "bundled" (OpenConsole.exe) or "inbox" (kernel32's conhost)
        host_dir=host_dir,
        COORD=COORD,
        STARTUPINFOEXW=STARTUPINFOEXW,
        PROCESS_INFORMATION=PROCESS_INFORMATION,
    )
    return _CONPTY_API


def _bundled_conpty_dir() -> str | None:
    """The folder holding pywinpty's ``conpty.dll`` AND ``OpenConsole.exe``
    (``_internal/winpty`` in the frozen build — the .spec bundles both), or
    None. Half a bundle is no bundle: the DLL without its host exe fails every
    spawn. Found by import SPEC, so nothing is loaded just to look."""
    try:
        import importlib.util

        spec = importlib.util.find_spec("winpty")
        origin = getattr(spec, "origin", None) if spec is not None else None
        if not origin:
            return None
        folder = os.path.dirname(origin)
        if all(
            os.path.isfile(os.path.join(folder, name))
            for name in ("conpty.dll", "OpenConsole.exe")
        ):
            return folder
    except Exception:  # pragma: no cover - defensive
        pass
    return None


class _BundledConpty:
    """kernel32, except that the three pseudoconsole calls are answered by the
    bundled ``conpty.dll`` (which hosts each console in OpenConsole.exe). Same
    signatures; pipes, processes and ReadFile stay kernel32's."""

    def __init__(self, k32, dll) -> None:
        self._k32 = k32
        for mine, theirs in (
            ("CreatePseudoConsole", "ConptyCreatePseudoConsole"),
            ("ResizePseudoConsole", "ConptyResizePseudoConsole"),
            ("ClosePseudoConsole", "ConptyClosePseudoConsole"),
        ):
            fn = getattr(dll, theirs)
            ref = getattr(k32, mine)
            fn.argtypes, fn.restype = ref.argtypes, ref.restype
            setattr(self, mine, fn)

    def __getattr__(self, name):
        return getattr(self._k32, name)


def conpty_available() -> bool:
    """Does this OS offer ConPTY (Windows 10 1809+)? Probes; never spawns."""
    if sys.platform != "win32":
        return False
    try:
        _conpty_api()
        return True
    except Exception:  # AttributeError: kernel32 has no CreatePseudoConsole
        return False


def mark_conpty_broken() -> None:
    """Stop offering ConPTY for the rest of this process (see ``_CONPTY_BROKEN``)."""
    global _CONPTY_BROKEN
    _CONPTY_BROKEN = True


def _env_block(api, env: dict):
    """``env`` as a CreateProcessW UNICODE environment block (sorted, NUL-separated,
    double-NUL-terminated). Built from bytes so embedded NULs survive."""
    items = sorted(((str(k), str(v)) for k, v in env.items()), key=lambda kv: kv[0].upper())
    text = "".join(f"{k}={v}\0" for k, v in items if k and "=" not in k[1:]) + "\0"
    raw = text.encode("utf-16-le")
    return api.ctypes.create_string_buffer(raw, len(raw) + 2)


class ConPtyBackend:
    """Windows ConPTY straight from kernel32 (v1.248.0): BYTES in and out, and
    a reader thread that PUSHES each chunk to the session.

    ``pushes_output`` tells :class:`~iron_jarvis.terminals.session.TerminalSession`
    to hand over its ingest callback (``set_output_handler``) before ``start``;
    nothing polls ``read_nonblocking`` (it serves only a handler-less caller).

    Lifecycle. A WAITER thread blocks on the shell's process handle; when the
    shell exits (on its own, or through :meth:`kill`) it records the exit code
    and closes the pseudoconsole. That closes the console for every process
    still attached to it — a CLI the shell started, a build — which is the
    tree kill ConPTY gives for free (the pipe backend needs a Job for it), and
    it ends the READER: ConPTY holds the output pipe open until it is closed,
    so the reader drains the last output and then sees EOF
    (``output_finished``). A GUI app launched from the pane (``code .``) has
    its own window and no console, and is deliberately left running.
    """

    pushes_output = True

    def __init__(self) -> None:
        self._hpc = None  # HPCON (ctypes.c_void_p), made in start()
        self._in_write: int | None = None
        self._out_read: int | None = None
        self._hproc: int | None = None
        self.pid: int | None = None
        self._exit_code: int | None = None
        self._started = False
        self._exited = threading.Event()
        self._eof = threading.Event()
        # `_lock`: the process handle, the pty's one close, resize.
        # `_in_lock`: the input handle — a write that blocks (a hung console)
        # must never block kill(), which is how the user gets out of it.
        self._lock = threading.Lock()
        self._in_lock = threading.Lock()
        self._pty_closed = False
        self._on_output = None
        self._on_eof = None
        self._queue: "queue.Queue[bytes]" = queue.Queue()

    def set_output_handler(self, on_output, on_eof=None) -> None:
        """``on_output(bytes)`` is called from the reader thread for every
        chunk; ``on_eof()`` once, when the output has ended. Set before start."""
        self._on_output = on_output
        self._on_eof = on_eof

    @property
    def output_finished(self) -> bool:
        """The reader has seen EOF: every byte the PTY produced was delivered."""
        return self._eof.is_set()

    def start(
        self,
        argv: list[str],
        cwd: str,
        env: dict | None,
        cols: int,
        rows: int,
    ) -> None:
        if sys.platform != "win32":
            raise ConPtyUnavailable("ConPTY is Windows-only")
        try:
            api = _conpty_api()
        except Exception as exc:  # no CreatePseudoConsole on this Windows
            raise ConPtyUnavailable(f"ConPTY is unavailable: {exc}") from exc
        ctypes, wintypes, k = api.ctypes, api.wintypes, api.k32
        in_read, in_write = wintypes.HANDLE(), wintypes.HANDLE()
        out_read, out_write = wintypes.HANDLE(), wintypes.HANDLE()
        if not k.CreatePipe(ctypes.byref(in_read), ctypes.byref(in_write), None, _CONPTY_PIPE_BYTES):
            raise ConPtyUnavailable(f"CreatePipe failed ({ctypes.get_last_error()})")
        if not k.CreatePipe(ctypes.byref(out_read), ctypes.byref(out_write), None, _CONPTY_PIPE_BYTES):
            err = ctypes.get_last_error()
            k.CloseHandle(in_read)
            k.CloseHandle(in_write)
            raise ConPtyUnavailable(f"CreatePipe failed ({err})")
        self._hpc = ctypes.c_void_p()
        hr = k.CreatePseudoConsole(
            api.COORD(_clamp_dim(cols), _clamp_dim(rows)), in_read, out_write, 0,
            ctypes.byref(self._hpc),
        )
        # The pseudoconsole holds its own duplicates of these two ends.
        k.CloseHandle(in_read)
        k.CloseHandle(out_write)
        if hr != 0:
            k.CloseHandle(in_write)
            k.CloseHandle(out_read)
            raise ConPtyUnavailable(
                f"CreatePseudoConsole failed (HRESULT 0x{hr & 0xFFFFFFFF:08X})"
            )
        self._in_write = in_write.value
        self._out_read = out_read.value
        try:
            self._spawn(api, argv, cwd, env)
        except BaseException:
            k.ClosePseudoConsole(self._hpc)
            self._pty_closed = True
            k.CloseHandle(self._in_write)
            k.CloseHandle(self._out_read)
            self._in_write = self._out_read = None
            raise
        self._started = True
        threading.Thread(target=self._read_loop, name="conpty-read", daemon=True).start()
        threading.Thread(target=self._wait_loop, name="conpty-wait", daemon=True).start()

    def _spawn(self, api, argv, cwd, env) -> None:
        ctypes, k = api.ctypes, api.k32
        size = ctypes.c_size_t(0)
        k.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        attrs = ctypes.create_string_buffer(size.value)
        if not k.InitializeProcThreadAttributeList(attrs, 1, 0, ctypes.byref(size)):
            raise ConPtyUnavailable(
                f"InitializeProcThreadAttributeList failed ({ctypes.get_last_error()})"
            )
        try:
            # The VALUE is the HPCON itself, not a pointer to it.
            if not k.UpdateProcThreadAttribute(
                attrs, 0, _PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE, self._hpc,
                ctypes.sizeof(ctypes.c_void_p), None, None,
            ):
                raise ConPtyUnavailable(
                    f"UpdateProcThreadAttribute failed ({ctypes.get_last_error()})"
                )
            si = api.STARTUPINFOEXW()
            si.StartupInfo.cb = ctypes.sizeof(api.STARTUPINFOEXW)
            # NULL std handles + STARTF_USESTDHANDLES: the child gets the
            # pseudoconsole's handles — never the daemon's own stdout/stderr,
            # which the desktop app redirects to a log and a child would
            # otherwise inherit, printing into the log instead of the pane.
            si.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
            si.lpAttributeList = ctypes.cast(attrs, ctypes.c_void_p)
            pi = api.PROCESS_INFORMATION()
            cmdline = ctypes.create_unicode_buffer(subprocess.list2cmdline(list(argv)))
            block = _env_block(api, env) if env is not None else None
            flags = _EXTENDED_STARTUPINFO_PRESENT | _CREATE_UNICODE_ENVIRONMENT
            if not k.CreateProcessW(
                None, cmdline, None, None, False, flags, block, cwd or None,
                ctypes.byref(si), ctypes.byref(pi),
            ):
                # The command or the folder — NOT a ConPTY failure.
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            k.DeleteProcThreadAttributeList(attrs)
        k.CloseHandle(pi.hThread)
        self._hproc = pi.hProcess
        self.pid = int(pi.dwProcessId)

    def _read_loop(self) -> None:
        api = _conpty_api()
        ctypes, k = api.ctypes, api.k32
        buf = ctypes.create_string_buffer(_CONPTY_READ_BYTES)
        n = api.wintypes.DWORD(0)
        handle = self._out_read
        try:
            while True:
                # Blocks without the GIL (ctypes releases it) until ConPTY
                # writes — no poll, no sleep.
                if not k.ReadFile(handle, buf, _CONPTY_READ_BYTES, ctypes.byref(n), None):
                    break  # ERROR_BROKEN_PIPE: the pseudoconsole was closed
                if not n.value:
                    continue
                data = ctypes.string_at(buf, n.value)
                handler = self._on_output
                if handler is None:
                    self._queue.put(data)
                    continue
                try:
                    handler(data)
                except Exception:  # noqa: BLE001 — a consumer bug must not end the stream
                    _log().debug("conpty output handler raised", exc_info=True)
        finally:
            self._out_read = None
            k.CloseHandle(handle)
            self._eof.set()
            eof = self._on_eof
            if eof is not None:
                try:
                    eof()
                except Exception:  # noqa: BLE001
                    _log().debug("conpty eof handler raised", exc_info=True)

    def _wait_loop(self) -> None:
        api = _conpty_api()
        k = api.k32
        k.WaitForSingleObject(self._hproc, _INFINITE)
        code = api.wintypes.DWORD(0)
        if k.GetExitCodeProcess(self._hproc, api.ctypes.byref(code)):
            self._exit_code = int(code.value)
        self._exited.set()
        self._close_pty()
        with self._lock:
            handle, self._hproc = self._hproc, None
        if handle:
            k.CloseHandle(handle)

    def _close_pty(self) -> None:
        """Close the pseudoconsole exactly once: every console process still
        attached goes down, and the reader drains to EOF."""
        with self._lock:
            if self._pty_closed or self._hpc is None:
                return
            self._pty_closed = True
        api = _conpty_api()
        # Older Windows blocks here until the output pipe is drained — the
        # reader thread is doing exactly that, so it returns.
        api.k32.ClosePseudoConsole(self._hpc)
        with self._in_lock:  # a write blocked on the dead console has returned now
            handle, self._in_write = self._in_write, None
        if handle:
            api.k32.CloseHandle(handle)

    def write(self, data: str | bytes) -> None:
        if isinstance(data, str):
            data = data.encode("utf-8")
        if not data:
            return
        api = _conpty_api() if sys.platform == "win32" else None
        if api is None:
            return
        ctypes, k = api.ctypes, api.k32
        written = api.wintypes.DWORD(0)
        with self._in_lock:
            handle = self._in_write
            if not handle:
                return  # closed or never started: a dead PTY swallows input
            view = memoryview(data)
            while view:
                chunk = bytes(view[:_CONPTY_PIPE_BYTES])
                if not k.WriteFile(handle, chunk, len(chunk), ctypes.byref(written), None):
                    return  # the console is going away — same as pywinpty's EOFError path
                view = view[written.value:]

    def read_nonblocking(self, max_bytes: int = 65536) -> bytes:
        """Only for a caller that installed no handler — the session never polls."""
        buf = bytearray()
        while len(buf) < max_bytes:
            try:
                buf += self._queue.get_nowait()
            except queue.Empty:
                break
        return bytes(buf)

    def resize(self, cols: int, rows: int) -> None:
        with self._lock:
            if self._pty_closed or self._hpc is None:
                return
            api = _conpty_api()
            api.k32.ResizePseudoConsole(self._hpc, api.COORD(_clamp_dim(cols), _clamp_dim(rows)))

    def is_alive(self) -> bool:
        return self._started and not self._exited.is_set()

    def kill(self) -> None:
        """Terminate the shell; the waiter then closes the pseudoconsole, which
        takes every console process attached to it. Idempotent — the manager
        kills a closed pane and ``kill_all`` kills it again at shutdown."""
        if not self._started:
            return
        with self._lock:
            handle = self._hproc
            alive = handle is not None and not self._exited.is_set()
            if alive:
                _conpty_api().k32.TerminateProcess(handle, 1)
        if not alive:
            self._close_pty()

    @property
    def exit_code(self) -> int | None:
        return self._exit_code


def _clamp_dim(n) -> int:
    """A COORD field is a SHORT; a nonsense size must not wrap negative."""
    try:
        return max(1, min(int(n), 32767))
    except (TypeError, ValueError):
        return 80


def _log():
    import logging

    return logging.getLogger(__name__)


def _winpty_importable() -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec("winpty") is not None
    except Exception:  # pragma: no cover - defensive
        return False


def default_backend() -> PtyBackend:
    """Pick the best backend for this OS *without* spawning anything.

    Windows → :class:`ConPtyBackend` (v1.248.0: kernel32's ConPTY, bytes,
    event-driven) when the OS has it and it has not failed to START in this
    process; else :class:`WinPtyBackend` (pywinpty) if importable; else
    :class:`PipeBackend`. ``IRONJARVIS_PTY_BACKEND=pywinpty`` forces pywinpty
    — the escape hatch if the raw backend misbehaves on some machine — and
    ``=pipe`` forces the pipe shell. POSIX → :class:`PosixPtyBackend`;
    otherwise :class:`PipeBackend`.
    """
    if sys.platform == "win32":
        forced = os.environ.get(PTY_BACKEND_ENV, "").strip().lower()
        if forced == "pipe":
            return PipeBackend()
        winpty_ok = _winpty_importable()
        if forced in ("pywinpty", "winpty") and winpty_ok:
            return WinPtyBackend()
        if not _CONPTY_BROKEN and conpty_available():
            return ConPtyBackend()
        if winpty_ok:
            return WinPtyBackend()
        return PipeBackend()
    if os.name == "posix":
        return PosixPtyBackend()
    return PipeBackend()  # pragma: no cover - exotic platforms
