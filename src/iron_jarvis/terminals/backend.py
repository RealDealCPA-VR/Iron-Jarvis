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
        except (ChildProcessError, OSError):  # pragma: no cover
            return False
        if pid == 0:
            return True
        if os.WIFEXITED(status):  # pragma: no cover - reaping path
            self._exit_code = os.WEXITSTATUS(status)
        elif os.WIFSIGNALED(status):  # pragma: no cover
            self._exit_code = -os.WTERMSIG(status)
        return False

    def kill(self) -> None:
        if self._pid is None:
            return
        import signal

        try:  # pragma: no cover - exercised only on POSIX
            os.kill(self._pid, signal.SIGKILL)
        except OSError:
            pass
        try:  # pragma: no cover - reap so we don't leak a zombie
            os.waitpid(self._pid, 0)
        except OSError:
            pass

    @property
    def exit_code(self) -> int | None:
        return self._exit_code


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
