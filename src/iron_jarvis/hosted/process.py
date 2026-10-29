"""One harness child of the Code view (v1.409.0): spawn, read, write, stop.

PROCESS PLUMBING ONLY — the same rules as every child the daemon starts:

* started through ``core.procs.spawn`` (argv list, no shell, its own
  kill-on-close Job, no console window), so a daemon crash never leaves a
  Claude Code or Codex behind and a stop kills the whole TREE (a tool the
  harness started — a dev server, a test run — goes with it);
* nothing here runs on the event loop: stdout and stderr are read by their own
  threads, each JSON line handed to the loop with ``call_soon_threadsafe``;
  :meth:`HarnessProcess.write` and :meth:`HarnessProcess.stop` BLOCK and are
  called through ``asyncio.to_thread``;
* the environment is a Build terminal's (``terminals.manager._with_pane_env``):
  the daemon's own credentials and a parent Claude Code session's markers
  dropped, the chosen account's HOME applied — and the user's own API keys
  KEPT, as a terminal would see them.

``binary_for`` is the one lookup of the harness's executable (tests point it
at a fake harness — the suite never runs a real one).
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import threading
from collections import deque
from typing import Any, Callable

from ..core import procs

log = logging.getLogger(__name__)

#: harness id -> the command it is started with.
BINARIES: dict[str, str] = {"claude-code": "claude", "codex": "codex"}

#: How long a graceful stop waits for the harness to exit on its own.
EXIT_GRACE_S = 3.0
#: One write to the harness's stdin.
WRITE_TIMEOUT_S = 15.0


def binary_for(harness: str) -> list[str] | None:
    """The argv prefix that starts ``harness`` ([exe]), or None when it is not
    installed. THE chokepoint tests replace."""
    name = BINARIES.get(harness)
    exe = shutil.which(name) if name else None
    return [exe] if exe else None


def build_env(accounts: Any) -> dict[str, str]:
    """A Build terminal's environment with ``accounts`` applied
    (``pane_accounts.PaneAccounts`` or None)."""
    from ..terminals.manager import _with_pane_env

    return _with_pane_env(None, {}, accounts)


def decode_line(raw: bytes) -> Any:
    """One stdout line as JSON (a dict), or None. UTF-8 (measured: both
    harnesses write UTF-8 on Windows); a bad byte is replaced, never fatal."""
    text = raw.decode("utf-8", errors="replace").strip()
    if not text or not text.startswith("{"):
        return None
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


class HarnessGone(RuntimeError):
    """The harness process is not running (it ended, or could not start)."""


class HarnessProcess:
    """One running harness. ``on_message(obj)`` and ``on_exit(code)`` run ON
    THE LOOP the process was started from."""

    def __init__(self, argv: list[str], *, env: dict[str, str], cwd: str, name: str) -> None:
        self.argv = list(argv)
        self.env = env
        self.cwd = cwd
        self.name = name
        self.proc: subprocess.Popen | None = None
        self.stopped = False
        self.exit_code: int | None = None
        self._wlock = threading.Lock()
        self._stop_lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._stderr: deque[bytes] = deque(maxlen=64)

    @property
    def alive(self) -> bool:
        p = self.proc
        return p is not None and p.poll() is None and not self.stopped

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc is not None else None

    # -- start (BLOCKING) ------------------------------------------------------
    def start(
        self,
        loop: asyncio.AbstractEventLoop,
        on_message: Callable[[dict[str, Any]], None],
        on_exit: Callable[[int | None], None],
    ) -> None:
        proc = procs.spawn(  # argv list, no shell; its own kill-on-close Job
            self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=self.cwd, env=self.env,
        )
        self.proc = proc

        def _post(fn: Callable[..., None], *args: Any) -> None:
            try:
                loop.call_soon_threadsafe(fn, *args)
            except RuntimeError:  # the loop closed (shutdown): nobody to tell
                pass

        def read_stdout() -> None:
            try:
                for raw in proc.stdout:  # type: ignore[union-attr]
                    obj = decode_line(raw)
                    if obj is not None:
                        _post(on_message, obj)
            except Exception:  # noqa: BLE001 — a dead pipe ends the stream
                pass
            code: int | None
            try:
                code = proc.wait(timeout=10)
            except Exception:  # noqa: BLE001
                code = proc.poll()
            self.exit_code = code
            _post(on_exit, code)

        def read_stderr() -> None:
            try:
                for chunk in iter(lambda: proc.stderr.read1(4096), b""):  # type: ignore[union-attr]
                    self._stderr.append(chunk)
            except Exception:  # noqa: BLE001
                pass

        for target, suffix in ((read_stdout, "out"), (read_stderr, "err")):
            t = threading.Thread(target=target, name=f"{self.name}-{suffix}", daemon=True)
            t.start()
            self._threads.append(t)

    # -- write (BLOCKING) ------------------------------------------------------
    def write(self, obj: dict[str, Any]) -> None:
        proc = self.proc
        if proc is None or proc.stdin is None or self.stopped or proc.poll() is not None:
            raise HarnessGone("the harness is not running")
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        with self._wlock:
            try:
                proc.stdin.write(data)
                proc.stdin.flush()
            except (BrokenPipeError, ConnectionResetError, OSError, ValueError) as exc:
                raise HarnessGone(f"the harness stopped reading ({type(exc).__name__})") from exc

    def stderr_text(self) -> str:
        return b"".join(self._stderr).decode("utf-8", errors="replace")[-2000:]

    # -- stop (BLOCKING, idempotent) ------------------------------------------
    def stop(self, *, graceful: bool = True) -> None:
        """Stop the harness and everything it started. ``graceful``: its stdin
        is closed first and it gets :data:`EXIT_GRACE_S` to save and exit
        (its session file is complete); then the tree is killed. Never raises."""
        with self._stop_lock:
            if self.stopped:
                return
            self.stopped = True
        proc = self.proc
        if proc is None:
            return
        if graceful and self._wlock.acquire(timeout=1.0):
            try:
                if proc.stdin and not proc.stdin.closed:
                    proc.stdin.close()
            except Exception:  # noqa: BLE001
                pass
            finally:
                self._wlock.release()
            try:
                proc.wait(timeout=EXIT_GRACE_S)
            except Exception:  # noqa: BLE001 — the kill below
                pass
        try:
            if proc.poll() is None:
                procs.kill_tree(proc)
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001 — a wedged reap must not hang a stop
            pass
        procs.release(proc)
        for t in self._threads:
            t.join(timeout=5)
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if pipe and not pipe.closed:
                    pipe.close()
            except Exception:  # noqa: BLE001
                pass
