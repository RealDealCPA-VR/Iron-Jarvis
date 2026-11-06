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
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

from ..core import procs

log = logging.getLogger(__name__)

#: harness id -> the command it is started with.
BINARIES: dict[str, str] = {"claude-code": "claude", "codex": "codex", "pi": "pi", "opencode": "opencode",
                            "grok": "grok", "antigravity": "agy"}

#: How long a graceful stop waits for the harness to exit on its own.
EXIT_GRACE_S = 3.0
#: One write to the harness's stdin.
WRITE_TIMEOUT_S = 15.0
#: How long a stop waits, in all, for the reader threads to finish.
STOP_JOIN_S = 3.0


def binary_for(harness: str) -> list[str] | None:
    """The argv prefix that starts ``harness`` ([exe]), or None when it is not
    installed. THE chokepoint tests replace."""
    name = BINARIES.get(harness)
    exe = shutil.which(name) if name else None
    if exe is None and name in ("pi", "opencode", "grok", "agy"):
        # Pi's installer puts pi.cmd in %LOCALAPPDATA%\pi-node\current and
        # adds it to the USER PATH, which a daemon started before the install
        # never sees — the Launch menu's own lookup (``ai_clis._find``).
        from ..terminals.ai_clis import _find

        exe = _find(name)
    if exe and name == "opencode":
        exe = npm_program(exe)
    return [exe] if exe else None


def npm_program(found: str) -> str:
    """v1.413.0: npm's ``opencode.cmd`` only starts
    ``node_modules/opencode-ai/bin/opencode.exe`` (measured: the shim's
    one line). That program is started DIRECTLY when it is there — no cmd.exe
    between Iron Jarvis and OpenCode reading its arguments, and one process
    less to stop. Anything else is used as found."""
    path = Path(found)
    if path.suffix.lower() != ".cmd":
        return found
    exe = path.parent / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"
    return str(exe) if exe.is_file() else found


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


#: At most this many processes are read below a harness (a bound, not a guess).
MAX_DESCENDANTS = 500


def descendants(pid: int | None) -> list[tuple[int, float]]:
    """BLOCKING: ``(pid, start time)`` of every process below ``pid`` now (the
    parent links as Windows keeps them). [] when it cannot be read.

    v1.416.0 review: Windows keeps a process's parent pid after that parent
    has gone and hands the pid out again, so a link can point at a NEWER
    process that merely got the old parent's number. A child is read only
    when it is not older than ITS OWN parent (psutil's recursive walk checks
    the top process only): the user's own process behind a reused pid is
    never taken for something the harness started."""
    if not pid:
        return []
    try:
        import psutil

        reader = getattr(psutil, "_ppid_map", None)
        if callable(reader):
            links = dict(reader())
        else:  # pragma: no cover - an older psutil
            links = {p.pid: p.ppid() for p in psutil.process_iter()}
        below: dict[int, list[int]] = {}
        for child, parent in links.items():
            below.setdefault(parent, []).append(child)
        out: list[tuple[int, float]] = []
        seen = {pid}
        stack = [(pid, psutil.Process(pid).create_time())]
        while stack and len(out) < MAX_DESCENDANTS:
            parent, parent_made = stack.pop()
            for child in below.get(parent, ()):
                if child in seen:
                    continue
                try:
                    made = psutil.Process(child).create_time()
                except (psutil.Error, OSError):
                    continue
                if made < parent_made:
                    continue  # older than its "parent": that parent's pid was reused
                seen.add(child)
                out.append((child, made))
                stack.append((child, made))
        return out
    except Exception:  # noqa: BLE001 — nothing known: nothing extra is ended
        return []


def kill_left(started: list[tuple[int, float]]) -> list[int]:
    """BLOCKING: end each process of ``started`` that is STILL the same
    process (same pid AND exactly the same start time — a reused pid is never
    touched) and shows the user nothing: a process with a visible window, or a
    Windows-GUI program (Notepad, a browser), is the user's now and is spared
    (``core.procs``' keep-windows rule, v1.333.0). Returns the pids ended.
    Never raises."""
    if not started:
        return []
    ended = []
    try:
        import psutil
    except Exception:  # noqa: BLE001
        return []
    try:
        windowed = procs._pids_with_visible_windows()
    except Exception:  # noqa: BLE001
        windowed = set()
    for pid, created in started:
        try:
            p = psutil.Process(pid)
            if p.create_time() != created:
                continue
            if pid in windowed or procs._is_window_program(pid):
                continue
            p.kill()
            ended.append(pid)
        except (psutil.Error, OSError):
            continue
    return ended


class HarnessGone(RuntimeError):
    """The harness process is not running (it ended, or could not start)."""


class HarnessProcess:
    """One running harness. ``on_message(obj)`` and ``on_exit(code)`` run ON
    THE LOOP the process was started from."""

    def __init__(self, argv: list[str], *, env: dict[str, str], cwd: str, name: str,
                 ends_left_behind: bool = False) -> None:
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
        #: v1.416.0: (pid, start time) of what the harness started, noted before
        #: a Stop (:meth:`note_started`).
        self._started: list[tuple[int, float]] = []
        #: v1.416.0 review: only a harness that asks for it (Grok, whose
        #: commands run outside the Job) has what it left outside its Job
        #: ended at the stop; every other tool stops exactly as before.
        self.ends_left_behind = bool(ends_left_behind)

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
            # NO member may leave the Job: Claude Code's Bash tool runs Git
            # Bash (MSYS2), which starts every Windows program with
            # CREATE_BREAKAWAY_FROM_JOB whenever the Job allows it — a dev
            # server it started then outlived the session (v1.386.0's rule).
            breakaway_ok=False,
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
            raise HarnessGone("the coding tool is not running")
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        with self._wlock:
            try:
                proc.stdin.write(data)
                proc.stdin.flush()
            except (BrokenPipeError, ConnectionResetError, OSError, ValueError) as exc:
                raise HarnessGone(f"the coding tool stopped reading its input ({type(exc).__name__})") from exc

    def stderr_text(self) -> str:
        return b"".join(self._stderr).decode("utf-8", errors="replace")[-2000:]

    def note_started(self) -> None:
        """BLOCKING: remember what the harness has started RIGHT NOW (v1.416.0)
        — read before it is told to stop a turn: Grok's cancel ends the shell
        it ran a command in and leaves the command itself running with no
        parent, where a later read of the tree no longer finds it (measured).
        :meth:`stop` ends whatever of it is still the same process."""
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return
        seen = {pid for pid, _ in self._started}
        self._started.extend(p for p in descendants(proc.pid) if p[0] not in seen)

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
        # v1.416.0: what the harness started, read BEFORE it is told to go (a
        # child is re-parented away from the tree once its parent exits).
        # Grok runs each command as its own `pwsh -Command …` that is NOT a
        # member of the Job (measured: a `ping` outlived the Job's close), so
        # whatever of it is still alive after the Job is ended by name below.
        started = list(self._started) if self.ends_left_behind else []
        if self.ends_left_behind and proc.poll() is None:
            known = {pid for pid, _ in started}
            started += [p for p in descendants(proc.pid) if p[0] not in known]
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
        kill_left(started)
        # Bounded: a reader still blocked means something still holds the pipe;
        # closing a pipe another thread is reading can block on Windows, so
        # only the pipes whose reader has finished are closed.
        end = time.monotonic() + STOP_JOIN_S
        for t in self._threads:
            t.join(timeout=max(0.0, end - time.monotonic()))
        alive = {t.name.rsplit("-", 1)[-1] for t in self._threads if t.is_alive()}
        pipes = [proc.stdin]
        if "out" not in alive:
            pipes.append(proc.stdout)
        if "err" not in alive:
            pipes.append(proc.stderr)
        for pipe in pipes:
            try:
                if pipe and not pipe.closed:
                    pipe.close()
            except Exception:  # noqa: BLE001
                pass
