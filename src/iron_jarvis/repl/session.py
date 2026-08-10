"""Parent-side manager for persistent per-session Python namespaces.

A ``ReplSession`` owns ONE long-lived child process that holds a Python
namespace between calls, so ``x = 41`` in one call and ``x + 1`` in the next
really do talk to each other. The child is spoken to over newline-delimited
JSON on its stdin/stdout::

    request   {"id": str, "code": str}
    response  {"id": str, "ok": bool, "stdout": str, "stderr": str,
               "result": str, "error": str, "truncated": bool}

Three things this module exists to get right:

1. **Nothing blocking runs on the event loop.** The daemon is one asyncio
   loop; a synchronous pipe read here would freeze every request in the app.
   That does not look like a freeze to the user, it looks like "Daemon
   offline" (the v1.153.1 outage). Every pipe write, every pipe read, every
   spawn and every kill goes through ``asyncio.to_thread``. A dedicated reader
   thread drains the child's stdout into a ``queue.Queue`` so the waiting side
   is an *interruptible* ``Queue.get(timeout=...)`` rather than an
   uncancellable ``readline()``.

2. **A hard timeout that actually kills.** A model will write
   ``while True: pass`` sooner or later. That is the whole reason this is a
   subprocess and not a thread: after ``timeout`` seconds the child is
   terminated (then killed), and the caller gets a plain-language error saying
   the code was killed and the namespace was lost.

3. **Restarts are reported, never hidden.** When a session dies, the next
   ``execute()`` spawns a fresh child and the returned payload carries
   ``restarted: True`` plus a note, so the user can be told their variables
   are gone instead of quietly getting a ``NameError`` later.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from collections.abc import Iterable
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_TIMEOUT_S",
    "IDLE_TTL_S",
    "MAX_SESSIONS",
    "ReplRegistry",
    "ReplSession",
    "worker_command",
]

#: Sessions untouched for this long are disposed by :meth:`ReplRegistry.sweep`.
IDLE_TTL_S = 900.0

#: Hard ceiling on concurrently live child processes.
MAX_SESSIONS = 16

#: Timeout used when a caller does not pass one.
DEFAULT_TIMEOUT_S = 30.0

# Safety net on top of the in-thread deadline: if the worker thread itself
# wedges (a stdin write that never drains), stop awaiting it anyway.
_EXCHANGE_GRACE_S = 10.0

# How long we wait for a terminated child to actually go away before SIGKILL.
_KILL_GRACE_S = 3.0

# Defensive cap on payload fields. The worker truncates too; this is here so a
# runaway `print` in a loop cannot push megabytes into a chat transcript.
_MAX_FIELD_CHARS = 100_000

# Lines of the child's real stderr kept for diagnosing a crash.
_STDERR_TAIL_LINES = 40

_CLOSED_ERROR = (
    "This Python session was closed (it was disposed, or reclaimed after sitting "
    "idle), so it did not run. Its namespace is gone. Start a new session to keep "
    "working."
)

_RESTART_NOTE = (
    "The previous Python session had ended (it was killed or it crashed), so this "
    "ran in a FRESH namespace: variables, imports and open files from earlier runs "
    "are gone."
)


def worker_command() -> list[str]:
    """The argv that launches a REPL worker child.

    Iron Jarvis ships as a PyInstaller-frozen binary, and a packaged install
    has **no Python on PATH** — that is exactly why ``run_code``'s
    ``shutil.which("python")`` lookup returns nothing there and the tool is
    dead on a real user's machine. The REPL must not inherit that weakness, so
    instead of hunting for an interpreter we re-exec *ourselves*: the frozen
    binary already contains a complete Python runtime, and a hidden
    ``repl-worker`` subcommand on the daemon CLI turns it into the worker.

    * frozen: ``[sys.executable, "repl-worker"]`` — ``sys.executable`` is the
      packaged ``.exe``, and the subcommand is the entry point.
    * dev:    ``[sys.executable, "-m", "iron_jarvis.repl.worker"]`` — the
      running interpreter, so the child matches the parent's venv exactly.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, "repl-worker"]
    return [sys.executable, "-m", "iron_jarvis.repl.worker"]


def _clip(value: Any) -> tuple[str, bool]:
    """Coerce a worker field to ``str`` and bound it. Returns (text, clipped)."""
    text = value if isinstance(value, str) else ("" if value is None else str(value))
    if len(text) <= _MAX_FIELD_CHARS:
        return text, False
    return text[:_MAX_FIELD_CHARS] + "\n[output clipped by the REPL manager]", True


class ReplSession:
    """One persistent Python namespace, backed by one child process.

    The child is spawned lazily on the first :meth:`execute` and respawned
    (loudly) after any death. Nothing on this class raises out of
    :meth:`execute` — every failure path returns the response dict shape with
    ``ok=False`` and an error a human can read.
    """

    def __init__(
        self,
        session_id: str,
        workspace: str | os.PathLike[str],
        *,
        command: Iterable[str] | None = None,
    ) -> None:
        self.session_id = session_id
        self.workspace = Path(workspace)
        #: argv for the child. Overridable so tests (and future sandboxing)
        #: can launch something other than the default worker.
        self.command: list[str] = list(command) if command else worker_command()
        self.created_at = time.monotonic()
        self.last_used = self.created_at
        self.executions = 0

        self._lock = asyncio.Lock()
        self._proc: subprocess.Popen[str] | None = None
        self._out: queue.Queue[str | None] | None = None
        self._stderr_tail: deque[str] = deque(maxlen=_STDERR_TAIL_LINES)
        self._spawned_once = False
        # Set when a child dies or is killed; consumed by the next execute()
        # to stamp `restarted` on the payload it returns.
        self._state_lost = False
        #: Set by :meth:`dispose`, and never cleared. A disposed session must
        #: never spawn again — see :meth:`_execute_locked`.
        self._closed = False

    # ---------------------------------------------------------------- state

    @property
    def pid(self) -> int | None:
        proc = self._proc
        return None if proc is None else proc.pid

    @property
    def alive(self) -> bool:
        proc = self._proc
        return proc is not None and proc.poll() is None

    def idle_for(self, now: float | None = None) -> float:
        return (time.monotonic() if now is None else now) - self.last_used

    # -------------------------------------------------------------- payload

    def _payload(
        self,
        *,
        ok: bool,
        stdout: str = "",
        stderr: str = "",
        result: str = "",
        error: str = "",
        truncated: bool = False,
        restarted: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "session_id": self.session_id,
            "ok": ok,
            "stdout": stdout,
            "stderr": stderr,
            "result": result,
            "error": error,
            "truncated": truncated,
            "restarted": restarted,
            "note": _RESTART_NOTE if restarted else "",
        }
        return payload

    # ------------------------------------------------------- child lifetime

    def _spawn(self) -> None:
        """Start the child. BLOCKING — only ever call inside a worker thread."""
        self.workspace.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            # Never flash a console window out of the packaged desktop app.
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(  # noqa: S603 - argv is ours, never user text
            self.command,
            cwd=str(self.workspace),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
            **kwargs,
        )
        out: queue.Queue[str | None] = queue.Queue()
        self._stderr_tail = deque(maxlen=_STDERR_TAIL_LINES)
        threading.Thread(
            target=self._pump_stdout,
            args=(proc.stdout, out),
            name=f"repl-out-{self.session_id}",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._pump_stderr,
            args=(proc.stderr, self._stderr_tail),
            name=f"repl-err-{self.session_id}",
            daemon=True,
        ).start()
        self._proc = proc
        self._out = out
        self._spawned_once = True

    @staticmethod
    def _pump_stdout(stream: Any, out: queue.Queue[str | None]) -> None:
        """Drain the child's stdout into a queue, forever, in its own thread.

        This is what makes the waiting side interruptible: the parent blocks on
        ``Queue.get(timeout=...)``, which honours a deadline, instead of on
        ``readline()``, which does not and cannot be cancelled.
        """
        try:
            if stream is not None:
                for line in stream:
                    out.put(line)
        except Exception:  # pragma: no cover - pipe torn down mid-read
            pass
        finally:
            out.put(None)  # EOF sentinel: the child is gone

    @staticmethod
    def _pump_stderr(stream: Any, tail: deque[str]) -> None:
        try:
            if stream is not None:
                for line in stream:
                    tail.append(line.rstrip("\r\n"))
        except Exception:  # pragma: no cover - pipe torn down mid-read
            pass

    def _kill(self) -> None:
        """Terminate, then kill, then reap. BLOCKING — worker thread only."""
        proc, self._proc, self._out = self._proc, None, None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=_KILL_GRACE_S)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    try:
                        proc.wait(timeout=_KILL_GRACE_S)
                    except subprocess.TimeoutExpired:  # pragma: no cover
                        pass
        except Exception:  # pragma: no cover - already dead / OS refused
            pass
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:  # pragma: no cover
                pass

    def _stderr_note(self) -> str:
        tail = [line for line in self._stderr_tail if line.strip()]
        if not tail:
            return ""
        return " Last output from the worker: " + " | ".join(tail[-5:])

    # ------------------------------------------------------------- exchange

    def _exchange(self, req_id: str, code: str, timeout: float) -> dict[str, Any]:
        """One request/response round trip. BLOCKING — worker thread only.

        Returns an internal envelope: ``{"status": "ok"|"timeout"|"dead", ...}``.
        The deadline lives in here (not in an ``asyncio.wait_for`` around a
        ``to_thread``) so that the thread cannot outlive the call it belongs to
        while parked on an uninterruptible read.
        """
        proc, out = self._proc, self._out
        if proc is None or out is None or proc.stdin is None:
            return {"status": "dead", "detail": "the worker was not running"}
        line = json.dumps({"id": req_id, "code": code}, ensure_ascii=False) + "\n"
        try:
            proc.stdin.write(line)
            proc.stdin.flush()
        except Exception as exc:
            return {"status": "dead", "detail": f"could not reach the worker ({exc})"}

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {"status": "timeout"}
            try:
                item = out.get(timeout=remaining)
            except queue.Empty:
                return {"status": "timeout"}
            if item is None:
                return {"status": "dead", "detail": "the worker exited"}
            raw = item.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except (ValueError, TypeError):
                # Stray non-JSON on stdout (a library printing at import time).
                # Ignore it rather than corrupt the session.
                continue
            if not isinstance(msg, dict) or msg.get("id") != req_id:
                continue
            return {"status": "ok", "msg": msg}

    # -------------------------------------------------------------- execute

    async def execute(self, code: str, timeout: float = DEFAULT_TIMEOUT_S) -> dict[str, Any]:
        """Run ``code`` in this session's namespace. Never raises.

        Every blocking step (spawn, write, read, kill) is offloaded with
        ``asyncio.to_thread``, so the daemon's single event loop keeps serving
        requests for the entire duration of a slow call.
        """
        # `timeout` reaches here from a model-authored tool call, so treat every
        # shape of garbage as expected input. `<= 0` is not enough on its own:
        # `json.loads` accepts the `NaN`/`Infinity` literals and `repl_tool`'s
        # `min(max(...))` clamp passes NaN straight through, yet NaN compares
        # False against everything — `Queue.get(timeout=nan)` never expires and
        # `wait_for`'s `call_later(nan)` never fires, so a NaN deadline silently
        # means NO deadline: the runaway spins forever, a worker thread stays
        # parked, and this session's lock is held for the life of the daemon.
        # `inf` fails louder but no better (`OverflowError` inside `wait_for`,
        # caught by the last-resort net, namespace discarded over an argument).
        try:
            timeout = float(timeout)
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT_S
        if not math.isfinite(timeout) or timeout <= 0:
            timeout = DEFAULT_TIMEOUT_S

        async with self._lock:
            self.last_used = time.monotonic()
            try:
                payload = await self._execute_locked(code, timeout)
            except Exception as exc:  # last line of defence: never raise
                self._state_lost = True
                await asyncio.to_thread(self._kill)
                payload = self._payload(
                    ok=False,
                    error=(
                        f"The Python session failed unexpectedly "
                        f"({exc.__class__.__name__}: {exc}). The namespace was "
                        f"discarded; the next run starts fresh."
                    ),
                )
            self.last_used = time.monotonic()
            return payload

    async def _execute_locked(self, code: str, timeout: float) -> dict[str, Any]:
        # A disposed session is DEAD, not dormant. Respawning here is how a
        # child ends up owned by nobody: `ReplRegistry.execute` hands the
        # session out under the registry lock and then runs outside it (and
        # `tools/repl_tool.py` awaits a workspace snapshot in that same
        # window), so a `dispose()` or an idle `sweep()` can land between the
        # lookup and the call. Without this guard the call would find a dead
        # child, start a fresh one, and hand it to an object the registry no
        # longer holds — unreachable by `dispose_all()` at daemon shutdown, and
        # therefore a Python interpreter that outlives the app.
        if self._closed:
            return self._payload(ok=False, error=_CLOSED_ERROR)
        if not self.alive:
            if self._spawned_once:
                self._state_lost = True
            await asyncio.to_thread(self._kill)  # reap any half-dead child
            try:
                await asyncio.to_thread(self._spawn)
            except Exception as exc:
                self._state_lost = True
                return self._payload(
                    ok=False,
                    error=(
                        f"Could not start a Python session: "
                        f"{exc.__class__.__name__}: {exc}"
                    ),
                )
            if self._closed:
                # Disposed WHILE we were spawning: take the new child with us.
                await asyncio.to_thread(self._kill)
                return self._payload(ok=False, error=_CLOSED_ERROR)

        restarted = self._state_lost
        self._state_lost = False
        self.executions += 1
        req_id = uuid.uuid4().hex

        try:
            outcome = await asyncio.wait_for(
                asyncio.to_thread(self._exchange, req_id, code, timeout),
                timeout=timeout + _EXCHANGE_GRACE_S,
            )
        except TimeoutError:  # asyncio.TimeoutError is this, since 3.11
            outcome = {"status": "timeout"}

        status = outcome.get("status")

        if status == "timeout":
            await asyncio.to_thread(self._kill)
            self._state_lost = True
            return self._payload(
                ok=False,
                restarted=restarted,
                error=(
                    f"Timed out: the code was still running after {timeout:g} "
                    f"seconds, so the Python session was killed. Everything in "
                    f"that namespace — variables, imports, open files — was lost. "
                    f"The next run will start from a fresh session."
                ),
            )

        if status == "dead":
            detail = str(outcome.get("detail") or "the worker exited")
            await asyncio.to_thread(self._kill)
            self._state_lost = True
            return self._payload(
                ok=False,
                restarted=restarted,
                error=(
                    f"The Python session died while running this code ({detail}). "
                    f"Its namespace was lost; the next run will start from a fresh "
                    f"session." + self._stderr_note()
                ),
            )

        msg = outcome.get("msg") or {}
        stdout, c1 = _clip(msg.get("stdout", ""))
        stderr, c2 = _clip(msg.get("stderr", ""))
        result, c3 = _clip(msg.get("result", ""))
        error, c4 = _clip(msg.get("error", ""))
        ok = bool(msg.get("ok", False))
        if not ok and not error:
            error = "The code failed but the worker did not say why."
        return self._payload(
            ok=ok,
            stdout=stdout,
            stderr=stderr,
            result=result,
            error=error,
            truncated=bool(msg.get("truncated")) or c1 or c2 or c3 or c4,
            restarted=restarted,
        )

    # --------------------------------------------------------------- disposal

    async def dispose(self) -> None:
        """Kill the child, permanently. Never raises, safe to call twice.

        ``_closed`` is set BEFORE the kill so that a call already in flight (or
        one that raced past the registry lookup) cannot bring a new child into
        the world behind the kill's back.
        """
        self._closed = True
        try:
            await asyncio.to_thread(self._kill)
        except Exception:  # pragma: no cover
            pass
        self._state_lost = self._spawned_once


class ReplRegistry:
    """``session_id -> ReplSession``, with a session cap and an idle sweeper."""

    def __init__(
        self,
        *,
        command: Iterable[str] | None = None,
        max_sessions: int = MAX_SESSIONS,
        idle_ttl_s: float = IDLE_TTL_S,
    ) -> None:
        self._command = list(command) if command else None
        self.max_sessions = max(1, int(max_sessions))
        self.idle_ttl_s = float(idle_ttl_s)
        self._sessions: dict[str, ReplSession] = {}
        self._lock = asyncio.Lock()

    def __len__(self) -> int:
        return len(self._sessions)

    def __contains__(self, session_id: object) -> bool:
        return session_id in self._sessions

    @property
    def session_ids(self) -> list[str]:
        return list(self._sessions)

    def get(self, session_id: str) -> ReplSession | None:
        return self._sessions.get(session_id)

    def describe(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        return [
            {
                "session_id": s.session_id,
                "workspace": str(s.workspace),
                "pid": s.pid,
                "alive": s.alive,
                "executions": s.executions,
                "idle_s": round(s.idle_for(now), 1),
            }
            for s in self._sessions.values()
        ]

    async def _acquire(
        self, session_id: str, workspace: str | os.PathLike[str]
    ) -> tuple[ReplSession | None, str]:
        """Return (session, error). Exactly one of the two is meaningful."""
        async with self._lock:
            existing = self._sessions.get(session_id)
            if existing is not None:
                return existing, ""
            if len(self._sessions) >= self.max_sessions:
                # Be generous before refusing: reclaim anything already idle.
                await self._sweep_locked()
            if len(self._sessions) >= self.max_sessions:
                return None, (
                    f"Too many Python sessions are open ({len(self._sessions)} of "
                    f"{self.max_sessions}). Close one first — no new session was "
                    f"started, because spawning processes without a limit is how a "
                    f"machine ends up unusable."
                )
            session = ReplSession(session_id, workspace, command=self._command)
            self._sessions[session_id] = session
            return session, ""

    async def execute(
        self,
        session_id: str,
        code: str,
        *,
        workspace: str | os.PathLike[str],
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> dict[str, Any]:
        """Run ``code`` in ``session_id``'s namespace, creating it if needed."""
        session, error = await self._acquire(session_id, workspace)
        if session is None:
            return {
                "session_id": session_id,
                "ok": False,
                "stdout": "",
                "stderr": "",
                "result": "",
                "error": error,
                "truncated": False,
                "restarted": False,
                "note": "",
            }
        return await session.execute(code, timeout)

    async def _sweep_locked(self) -> list[str]:
        now = time.monotonic()
        stale = [
            sid
            for sid, s in self._sessions.items()
            if s.idle_for(now) > self.idle_ttl_s
        ]
        for sid in stale:
            session = self._sessions.pop(sid, None)
            if session is not None:
                await session.dispose()
        return stale

    async def sweep(self) -> list[str]:
        """Dispose sessions idle beyond the TTL. Returns the ids disposed."""
        async with self._lock:
            return await self._sweep_locked()

    async def dispose(self, session_id: str) -> bool:
        """Kill one session's child. Returns whether it existed."""
        async with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        await session.dispose()
        return True

    async def dispose_all(self) -> None:
        """Kill every child. Safe to call at daemon shutdown."""
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            await session.dispose()
