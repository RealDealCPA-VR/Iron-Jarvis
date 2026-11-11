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
import concurrent.futures
import json
import math
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import deque
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from . import worker

__all__ = [
    "DEFAULT_TIMEOUT_S",
    "confinement_env",
    "isolated_env",
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

#: v1.470.0 — the ISOLATED worker a tool script runs in (see
#: :func:`isolated_env` and ``worker.ENV_ISOLATED``).
#: How long a new isolated worker may take to answer its first request.
ISOLATED_START_TIMEOUT_S = 30.0
#: Committed memory one isolated worker may hold (Windows enforces it).
ISOLATED_MEMORY_BYTES = 2 * 1024 * 1024 * 1024
#: Tool calls one script may make. A bound, not a budget: a loop over a
#: folder of client files fits many times over; a runaway loop does not.
MAX_TOOL_CALLS = 300
#: Characters of one tool's output handed to the script. It stays in the
#: script's memory and never reaches the model unless printed, so the bound
#: is generous; past it the text is cut and the cut is said.
MAX_TOOL_OUTPUT_CHARS = 4_000_000

_TOOLS_OFF_ERROR = "This Python session cannot call tools."
_TOOL_LIMIT_ERROR = (
    f"This script has already made {MAX_TOOL_CALLS} tool calls, the most one "
    "script may make. Finish with what it has, or split the work in two scripts."
)
_STOPPED_ERROR = "The script was stopped, so the tool was not run."

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


def confinement_env(workspace: Path, scratch: Path | None = None) -> dict[str, str]:
    """The child's filesystem policy, as environment variables.

    Computed HERE rather than in the worker because the policy itself lives in
    ``core/fs_policy`` and the worker is deliberately stdlib-only (it is spawned
    from a frozen binary and must not drag the app's import graph in). The
    parent reads the policy; the child only enforces it.

    * writes → the session workspace (the grounded project's folder whenever
      chat resolved one), plus a PRIVATE scratch dir. Libraries write through
      ``tempfile`` constantly and a REPL that cannot would fail in ways that
      read as library bugs — but allowing the whole system temp root would hand
      over every other program's temp files too, so the child gets its own
      directory and ``TMPDIR``/``TEMP``/``TMP`` are pointed at it.
    * reads  → unrestricted unless ``IRONJARVIS_FS_ALLOWLIST`` is set, matching
      ``fs_path_allowed`` exactly. The user's documents live all over the disk.
    * denied → the protected secrets/key roots, both directions, matching
      ``fs_read_ok``. Measured before this existed: ``read_file`` refused the
      Fernet key and a REPL cell printed it.
    """
    from ..core.fs_policy import allowlist_roots, protected_roots

    writable = [str(Path(workspace).resolve())]
    env: dict[str, str] = {}
    if scratch is not None:
        writable.append(str(scratch))
        # Redirect the stdlib's own idea of temp, or `tempfile` would keep
        # handing out paths in the system root that confinement then refuses.
        env["TMPDIR"] = env["TEMP"] = env["TMP"] = str(scratch)
    env[worker.ENV_WRITE_ROOTS] = os.pathsep.join(writable)
    env[worker.ENV_DENY_ROOTS] = os.pathsep.join(str(p) for p in protected_roots())
    env[worker.ENV_READ_ROOTS] = os.pathsep.join(str(p) for p in allowlist_roots())
    return {k: v for k, v in env.items() if v}


def isolated_env(workspace: Path, scratch: Path) -> dict[str, str]:
    """The WHOLE environment of an isolated tool-script worker (v1.470.0).

    Unlike :func:`confinement_env` (a few variables laid over the daemon's own
    environment), this is everything the child gets:

    * no secret — the daemon's environment with every key, token, vendor
      variable and this app's own internals (its install token among them)
      removed by NAME and by VALUE (``sandbox.native.build_env``, the rule a
      coding session's commands already run under);
    * writes → the private scratch folder ONLY. A script gives the user a
      file by handing its content to a file tool, which asks and is undoable;
    * reads → the turn's working folder and the scratch folder (the worker
      adds its own runtime folders). Anything else is read through a tool,
      which applies the app's own file policy and is on the ledger;
    * denied → the protected secrets/key roots, as everywhere;
    * ``worker.ENV_ISOLATED`` — no network, no other program, no registry
      change (enforced in the child; the parent adds a Windows Job that holds
      "no other program" and the memory ceiling even if the child's own
      checks are defeated — ``core.procs.restrict``).
    """
    from ..core.fs_policy import protected_roots
    from ..sandbox.native import build_env

    env = build_env()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # The user's own PYTHONPATH / PYTHONSTARTUP would import code of theirs
    # (or of whoever set it) into a process meant to hold only the script.
    for name in [k for k in env if k.upper() in ("PYTHONPATH", "PYTHONSTARTUP", "PYTHONHOME")]:
        env.pop(name, None)
    env["TMPDIR"] = env["TEMP"] = env["TMP"] = str(scratch)
    env[worker.ENV_ISOLATED] = "1"
    env[worker.ENV_WRITE_ROOTS] = str(scratch)
    env[worker.ENV_READ_ROOTS] = os.pathsep.join(
        [str(Path(workspace).resolve()), str(scratch)]
    )
    deny = os.pathsep.join(str(p) for p in protected_roots())
    if deny:
        env[worker.ENV_DENY_ROOTS] = deny
    return env


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
        isolated: bool = False,
    ) -> None:
        self.session_id = session_id
        self.workspace = Path(workspace)
        #: v1.470.0: an isolated tool-script worker (:func:`isolated_env`).
        self.isolated = bool(isolated)
        #: What Windows itself enforces on this child (``procs.restrict``);
        #: all False until an isolated child is up, and off Windows.
        self.sealed: dict[str, bool] = {}
        #: Seconds this session's scripts have spent waiting on tool calls.
        #: The script's own time limit does not count them.
        self._tool_seconds = 0.0
        #: The tool call in flight (a concurrent future), so a Stop reaches it.
        self._inflight: Any = None
        self._stopped = threading.Event()
        #: Kills handed to the background after a Stop (held so they finish).
        self._reaps: set[Any] = set()
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
        #: Private temp dir for the current child, created by :meth:`_spawn` and
        #: removed by :meth:`_kill` — see :func:`confinement_env`.
        self._scratch: Path | None = None
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

    @property
    def busy(self) -> bool:
        """A cell is running right now (the execute lock is held)."""
        return self._lock.locked()

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
        # NOT for correctness — `importlib` swallows a refused `.pyc` write and
        # imports fine either way (mutation-checked, so the comment cannot drift
        # back into claiming otherwise). It is for tidiness, which is the whole
        # point of confinement: the workspace IS writable, so without this every
        # import from the user's project folder drops a `__pycache__` into it.
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        self._scratch = Path(tempfile.mkdtemp(prefix="ij-repl-"))
        self.sealed = {}
        pipes: dict[str, Any] = dict(
            cwd=str(self.workspace),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        if self.isolated:
            # v1.470.0: its own environment (no secret in it) and its own
            # kill-on-close Job that lets no member leave, created suspended
            # and assigned before it runs (``core.procs.spawn``).
            from ..core import procs

            proc = procs.spawn(
                self.command,
                breakaway_ok=False,
                env=isolated_env(self.workspace, self._scratch),
                **pipes,
            )
        else:
            env.update(confinement_env(self.workspace, self._scratch))
            kwargs: dict[str, Any] = {}
            if sys.platform == "win32":
                # Never flash a console window out of the packaged desktop app.
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            proc = subprocess.Popen(  # noqa: S603 - argv is ours, never user text
                self.command,
                env=env,
                **pipes,
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
        if self.isolated:
            self._seal()

    def _seal(self) -> None:
        """Wait for a new isolated child to answer, then have Windows hold it.

        BLOCKING — worker thread only. The first exchange proves the
        interpreter is up (a virtual environment's launcher has started the
        real one by then), so "no more processes than now" can be set without
        stopping the child's own start. Raises when the child never answers:
        a tool script must not run in a worker that is not what it claims."""
        outcome = self._exchange(uuid.uuid4().hex, "pass", ISOLATED_START_TIMEOUT_S)
        if outcome.get("status") != "ok" or not (outcome.get("msg") or {}).get("ok"):
            note = self._stderr_note()
            self._kill()
            raise RuntimeError("the isolated worker did not start." + note)
        from ..core import procs

        self.sealed = procs.restrict(self._proc, memory_bytes=ISOLATED_MEMORY_BYTES)

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
            # No child, but `_spawn` may still have created a scratch dir before
            # `Popen` raised — cleaning up only on the happy path leaks a
            # directory per failed spawn.
            self._discard_scratch()
            return
        self._end_process(proc)
        # The scratch dir belongs to the child that just died. Removed AFTER the
        # kill, never before: deleting it out from under a live interpreter is
        # how a cell fails with a bewildering "temp file vanished".
        self._discard_scratch()

    @staticmethod
    def _hard_kill(proc: Any) -> None:
        """End the child NOW, without waiting. Fast enough for the loop: one
        or two system calls. A child with a Job (the isolated worker) dies
        with every member of it."""
        try:
            from ..core import procs

            if procs._job_of(proc) is not None:
                procs.kill_tree(proc)
            elif proc.poll() is None:
                proc.kill()
        except Exception:  # noqa: BLE001 — already gone
            pass

    def _abandon(self) -> tuple[Any, Path | None]:
        """Stop path (v1.470.0): detach the child and kill it at once, on the
        caller's thread, and hand back what is left to reap in the background
        (:meth:`_reap`). After this the session holds no child: the next run
        starts a fresh one and says so."""
        proc, self._proc, self._out = self._proc, None, None
        scratch, self._scratch = self._scratch, None
        if proc is not None:
            self._hard_kill(proc)
        return proc, scratch

    def _reap(self, proc: Any, scratch: Path | None) -> None:
        """Finish what :meth:`_abandon` began. BLOCKING — worker thread only."""
        if proc is not None:
            self._end_process(proc)
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)

    @staticmethod
    def _end_process(proc: Any) -> None:
        """Terminate, kill, reap, close the pipes, release the Job. BLOCKING."""
        try:
            from ..core import procs

            jobbed = procs._job_of(proc) is not None
        except Exception:  # noqa: BLE001
            procs, jobbed = None, False  # type: ignore[assignment]
        try:
            if jobbed:
                procs.kill_tree(proc)
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
        if jobbed:
            try:
                procs.release(proc)
            except Exception:  # pragma: no cover
                pass

    def _discard_scratch(self) -> None:
        """Remove this child's private temp dir, if it has one. Never raises."""
        scratch, self._scratch = self._scratch, None
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)

    def _stderr_note(self) -> str:
        tail = [line for line in self._stderr_tail if line.strip()]
        if not tail:
            return ""
        return " Last output from the worker: " + " | ".join(tail[-5:])

    # ------------------------------------------------------------- exchange

    def _answer_call(self, call: dict[str, Any], count: int, handler: Any) -> dict[str, Any]:
        """One tool call a script made, answered. Worker thread; never raises."""
        if handler is None:
            return {"ok": False, "error": _TOOLS_OFF_ERROR}
        if self._stopped.is_set():
            return {"ok": False, "error": _STOPPED_ERROR}
        if count > MAX_TOOL_CALLS:
            return {"ok": False, "error": _TOOL_LIMIT_ERROR}
        name = str(call.get("tool") or "")
        args = call.get("args")
        try:
            reply = handler(name, args if isinstance(args, dict) else {})
            if isinstance(reply, dict):
                return dict(reply)
            return {"ok": False, "error": f"{name} gave no usable answer"}
        except Exception as exc:  # noqa: BLE001 — the script gets a sentence
            return {
                "ok": False,
                "error": f"{name} could not be run ({type(exc).__name__}: {exc})",
            }

    def _exchange(
        self,
        req_id: str,
        code: str,
        timeout: float,
        tools: "list[str] | None" = None,
        handler: Any = None,
    ) -> dict[str, Any]:
        """One request/response round trip. BLOCKING — worker thread only.

        Returns an internal envelope: ``{"status": "ok"|"timeout"|"dead", ...}``.
        The deadline lives in here (not in an ``asyncio.wait_for`` around a
        ``to_thread``) so that the thread cannot outlive the call it belongs to
        while parked on an uninterruptible read.
        """
        proc, out = self._proc, self._out
        if proc is None or out is None or proc.stdin is None:
            return {"status": "dead", "detail": "the worker was not running"}
        request: dict[str, Any] = {"id": req_id, "code": code}
        if tools is not None:
            # A TOOL SCRIPT (v1.470.0): the child binds `tools` for this one
            # request and may send {"id", "call"} lines before its response.
            request["tools"] = list(tools)
        line = json.dumps(request, ensure_ascii=False) + "\n"
        try:
            proc.stdin.write(line)
            proc.stdin.flush()
        except Exception as exc:
            return {"status": "dead", "detail": f"could not reach the worker ({exc})"}

        deadline = time.monotonic() + timeout
        calls = 0
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
            call = msg.get("call")
            if isinstance(call, dict):
                # The script is waiting on a tool. The time the tool takes is
                # the tool's (its own deadline bounds it), not the script's:
                # the script's clock stops until the answer is written back.
                started = time.monotonic()
                calls += 1
                reply = self._answer_call(call, calls, handler)
                reply["reply"] = call.get("n")
                try:
                    proc.stdin.write(_reply_line(reply))
                    proc.stdin.flush()
                except Exception as exc:
                    return {
                        "status": "dead",
                        "detail": f"could not reach the worker ({exc})",
                    }
                waited = time.monotonic() - started
                deadline += waited
                self._tool_seconds += waited
                continue
            return {"status": "ok", "msg": msg}

    # -------------------------------------------------------------- execute

    async def execute(
        self,
        code: str,
        timeout: float = DEFAULT_TIMEOUT_S,
        *,
        tools: "list[str] | None" = None,
        on_tool_call: Any = None,
    ) -> dict[str, Any]:
        """Run ``code`` in this session's namespace. Never raises.

        ``tools`` + ``on_tool_call`` (v1.470.0) make it a TOOL SCRIPT: the
        code may call those tools, and each call is answered by awaiting
        ``on_tool_call(name, args)`` ON THE EVENT LOOP (it returns
        ``{"ok", "output", "data", "error"}``). The caller's coroutine is
        where every gate lives; this class only carries the call across.
        A cancel (the user's Stop, the tool deadline) kills the child and
        cancels the call in flight, then re-raises.

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
                payload = await self._execute_locked(code, timeout, tools, on_tool_call)
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

    def _stop_now(self) -> None:
        """A cancel landed while code was running (v1.470.0). On the loop,
        and nothing here waits: the child is killed at once (so a script
        stops calling tools and a runaway cell stops spinning), the tool call
        in flight is cancelled, and the slow half of the kill goes to a
        background task. The namespace is gone and the next run says so."""
        self._stopped.set()
        inflight = self._inflight
        if inflight is not None:
            try:
                inflight.cancel()
            except Exception:  # noqa: BLE001
                pass
        self._state_lost = True
        proc, scratch = self._abandon()
        try:
            task = asyncio.get_running_loop().create_task(
                asyncio.to_thread(self._reap, proc, scratch)
            )
            self._reaps.add(task)
            task.add_done_callback(self._reaps.discard)
        except Exception:  # noqa: BLE001 — no loop: the kill already landed
            pass

    async def warm(self) -> bool:
        """Start the child now, so the first run does not wait for it
        (v1.470.0). Does nothing when one is up, when a run holds the session
        or after disposal. Returns whether a child is up. Never raises."""
        if self._closed or self._lock.locked():
            return self.alive
        async with self._lock:
            if self._closed or self.alive:
                return self.alive
            try:
                await asyncio.to_thread(self._kill)
                await asyncio.to_thread(self._spawn)
            except Exception:  # noqa: BLE001 — the first run reports it
                return False
            if self._closed:
                await asyncio.to_thread(self._kill)
                return False
            self.last_used = time.monotonic()
            return True

    async def _execute_locked(
        self,
        code: str,
        timeout: float,
        tools: "list[str] | None" = None,
        on_tool_call: Any = None,
    ) -> dict[str, Any]:
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

        handler = None
        if tools is not None and on_tool_call is not None:
            loop = asyncio.get_running_loop()

            def handler(name: str, args: dict[str, Any]) -> Any:
                # Worker thread. The coroutine runs on the loop, in a copy of
                # THIS call's context (Local only, the held model, the
                # account, the turn), so the gate it runs is the turn's own.
                future = asyncio.run_coroutine_threadsafe(on_tool_call(name, args), loop)
                self._inflight = future
                try:
                    return future.result()
                except concurrent.futures.CancelledError:
                    return {"ok": False, "error": _STOPPED_ERROR}
                finally:
                    self._inflight = None

        self._stopped.clear()
        granted = self._tool_seconds
        work = asyncio.ensure_future(
            asyncio.to_thread(self._exchange, req_id, code, timeout, tools, handler)
        )
        # A thread left behind by a timeout still ends (the kill below closes
        # its pipe); its late result must not be reported as never retrieved.
        work.add_done_callback(lambda f: f.cancelled() or f.exception())
        budget = timeout + _EXCHANGE_GRACE_S
        try:
            while True:
                done, _ = await asyncio.wait({work}, timeout=budget)
                if done:
                    outcome = work.result()
                    break
                # Time a script spent waiting on tools is not its own: while a
                # call is in flight, or one finished since the last look, the
                # safety net waits on.
                extra = self._tool_seconds - granted
                if extra > 0 or self._inflight is not None:
                    granted = self._tool_seconds
                    budget = max(extra, 1.0)
                    continue
                outcome = {"status": "timeout"}
                break
        except asyncio.CancelledError:
            self._stop_now()
            raise

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


#: How deep :func:`_plain` follows nested data before it stops.
_PLAIN_DEPTH = 64


def _plain(value: Any, depth: int = 0, seen: "set[int] | None" = None) -> Any:
    """*value* as plain JSON values: keys as text, tuples and sets as lists, a
    container met again inside itself as ``"<loop>"``, anything else as its
    text. For a tool answer :func:`json.dumps` refused."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if depth >= _PLAIN_DEPTH:
        return "<too deep>"
    seen = set() if seen is None else seen
    if id(value) in seen:
        return "<loop>"
    if isinstance(value, dict):
        seen.add(id(value))
        try:
            return {str(k): _plain(v, depth + 1, seen) for k, v in value.items()}
        finally:
            seen.discard(id(value))
    if isinstance(value, (list, tuple, set, frozenset)):
        seen.add(id(value))
        try:
            return [_plain(v, depth + 1, seen) for v in value]
        finally:
            seen.discard(id(value))
    return str(value)


def _reply_line(reply: dict[str, Any]) -> str:
    """One answer to a script's tool call as a protocol line (v1.471.0).

    ASCII JSON, so no character can fail the pipe's encoding (a lone
    surrogate in a tool's text used to). Data the encoder refuses (a tuple
    key, a dict that contains itself) is made plain; if even that fails, the
    script gets a sentence instead of the data. Before, any of these killed
    the worker mid-script ("could not reach the worker") and lost every
    variable. Never raises."""
    try:
        return json.dumps(reply, ensure_ascii=True, default=str) + "\n"
    except (TypeError, ValueError, RecursionError):
        pass
    try:
        return json.dumps(_plain(reply), ensure_ascii=True, default=str) + "\n"
    except Exception:  # noqa: BLE001 — the last word is a plain refusal
        return json.dumps({
            "reply": reply.get("reply") if isinstance(reply, dict) else None,
            "ok": False,
            "error": "the tool answered, but its answer could not be passed to the script",
        }, ensure_ascii=True) + "\n"


def namespace_key(
    session_id: str, workspace: str | os.PathLike[str], isolated: bool = False
) -> str:
    """The registry key: one namespace per (session, FOLDER) pair.

    Keying on ``session_id`` alone was wrong in a way confinement turned from
    untidy into blocking. Chat runs every turn under the literal session id
    ``"chat"`` (``daemon/chat_turn.py``), while its tool workspace follows the
    grounded project — so the FIRST chat turn fixed the namespace's folder and
    every later one reused it. Before v1.160.0 that meant a cell's relative
    writes silently landed in the previous project's folder; now the workspace
    is also the write root, so they would be refused outright, naming a folder
    the user had already navigated away from.

    Including the folder also gives the property worth having on its own: work
    in a project comes back to the same namespace, and two projects never see
    each other's variables. The idle sweeper and ``MAX_SESSIONS`` still bound
    the total, so the cost of the extra keys is reclaimed automatically.
    """
    try:
        folder = os.path.normcase(os.path.realpath(os.fspath(workspace)))
    except Exception:  # pragma: no cover - unresolvable, key on the raw text
        folder = os.path.normcase(str(workspace))
    # v1.470.0: a tool script's isolated worker is a namespace of its own —
    # never the `repl` tool's, which has the network and the working folder.
    return f"{session_id}@{folder}" + ("#tools" if isolated else "")


class ReplRegistry:
    """``(session, folder) -> ReplSession``, with a cap and an idle sweeper.

    The public surface still speaks in SESSION IDS — callers outside this
    module have no reason to know about the composite key — so ``dispose`` and
    ``get`` take a session id and cover every folder that session has used.
    """

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
        #: Session ids with an agent run in progress (v1.333.0 review), with
        #: a count. The timed idle sweep leaves their namespaces alone: a run
        #: parked on an ask the user has not answered (attended asks have no
        #: clock) or on a long model call is idle, not finished, and its next
        #: step expects its variables. The run's end disposes them instead.
        self._held: dict[str, int] = {}

    def hold(self, session_id: str) -> None:
        """A run of *session_id* has started: :meth:`sweep` spares it."""
        self._held[session_id] = self._held.get(session_id, 0) + 1

    def release(self, session_id: str) -> None:
        """That run has ended (pairs with :meth:`hold`)."""
        left = self._held.get(session_id, 0) - 1
        if left > 0:
            self._held[session_id] = left
        else:
            self._held.pop(session_id, None)

    def __len__(self) -> int:
        return len(self._sessions)

    def __contains__(self, session_id: object) -> bool:
        return any(s.session_id == session_id for s in self._sessions.values())

    @property
    def session_ids(self) -> list[str]:
        return list(dict.fromkeys(s.session_id for s in self._sessions.values()))

    def get(self, session_id: str) -> ReplSession | None:
        """The most recently used namespace for *session_id*, across folders.

        Best effort by design: a session that has worked in two projects has
        two namespaces, and there is no single right answer. Callers that need
        a specific one go through :meth:`execute`, which knows the folder.
        """
        candidates = [s for s in self._sessions.values() if s.session_id == session_id]
        if not candidates:
            return None
        return max(candidates, key=lambda s: s.last_used)

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
        self, session_id: str, workspace: str | os.PathLike[str], isolated: bool = False
    ) -> tuple[ReplSession | None, str]:
        """Return (session, error). Exactly one of the two is meaningful."""
        key = namespace_key(session_id, workspace, isolated)
        async with self._lock:
            existing = self._sessions.get(key)
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
            session = ReplSession(
                session_id, workspace, command=self._command, isolated=isolated
            )
            self._sessions[key] = session
            return session, ""

    async def execute(
        self,
        session_id: str,
        code: str,
        *,
        workspace: str | os.PathLike[str],
        timeout: float = DEFAULT_TIMEOUT_S,
        isolated: bool = False,
        tools: "list[str] | None" = None,
        on_tool_call: Any = None,
    ) -> dict[str, Any]:
        """Run ``code`` in ``session_id``'s namespace, creating it if needed.

        ``isolated`` picks the session's isolated worker (a namespace of its
        own); ``tools`` + ``on_tool_call`` make the run a tool script
        (:meth:`ReplSession.execute`)."""
        session, error = await self._acquire(session_id, workspace, isolated)
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
        if tools is None:
            return await session.execute(code, timeout)
        return await session.execute(
            code, timeout, tools=tools, on_tool_call=on_tool_call
        )

    async def warm(
        self,
        session_id: str,
        workspace: str | os.PathLike[str],
        *,
        isolated: bool = True,
    ) -> bool:
        """Start ``session_id``'s worker ahead of its first run (v1.470.0),
        so a tool script the model is about to write does not wait for a
        process to start. Returns whether one is up. Never raises."""
        try:
            session, _ = await self._acquire(session_id, workspace, isolated)
            return False if session is None else await session.warm()
        except Exception:  # noqa: BLE001 — warming is a courtesy
            return False

    async def _sweep_locked(self, *, spare_held: bool = False) -> list[str]:
        now = time.monotonic()
        # A namespace in the middle of a cell is not idle, however long the
        # cell has run (``last_used`` is stamped when it STARTS): v1.333.0
        # sweeps on a timer, so the check is no longer only reached at the
        # 17th namespace. The timer also spares a run still in progress
        # (``spare_held``); the cap's last-resort reclaim in ``_acquire`` does
        # not, exactly as before.
        stale = [
            key
            for key, s in self._sessions.items()
            if s.idle_for(now) > self.idle_ttl_s
            and not s.busy
            and not (spare_held and s.session_id in self._held)
        ]
        # Reported as SESSION IDS, never as the internal composite key: every
        # other method on this class speaks session ids, and a caller that
        # logged `chat@c:\users\...` would be leaking a folder path into a
        # place that has only ever carried an id.
        disposed: list[str] = []
        for key in stale:
            session = self._sessions.pop(key, None)
            if session is not None:
                disposed.append(session.session_id)
                await session.dispose()
        return list(dict.fromkeys(disposed))

    async def sweep(self) -> list[str]:
        """Dispose sessions idle beyond the TTL. Returns the ids disposed.

        A session with a run in progress (:meth:`hold`) is never swept here."""
        async with self._lock:
            return await self._sweep_locked(spare_held=True)

    async def dispose(self, session_id: str) -> bool:
        """Kill EVERY namespace this session owns. Returns whether any existed.

        Every folder, not just the current one: a chat that moved between two
        projects holds a child for each, and leaving one behind at session end
        is an interpreter nobody can reach — the exact leak the ``_closed``
        guard exists to prevent, arriving by a different road.
        """
        async with self._lock:
            keys = [
                k for k, s in self._sessions.items() if s.session_id == session_id
            ]
            sessions = [self._sessions.pop(k) for k in keys]
        for session in sessions:
            await session.dispose()
        return bool(sessions)

    async def rekey(self, old: str, new: str) -> int:
        """Hand every ISOLATED namespace of session *old* to session *new*
        (v1.471.0) and return how many moved. A new chat's first message runs
        its scripts under the turn's id (the chat has no id yet); once the
        chat is saved, its later messages run under the chat's id, and the
        first message's variables were silently gone. A namespace *new*
        already has is kept (the old one is left to the idle sweep). The
        process, its variables and a script still running are untouched:
        only the name it is found by changes."""
        if not old or not new or old == new:
            return 0
        moved = 0
        async with self._lock:
            for key, session in list(self._sessions.items()):
                if session.session_id != old or not session.isolated:
                    continue
                target = namespace_key(new, session.workspace, True)
                if target in self._sessions:
                    continue
                self._sessions.pop(key, None)
                session.session_id = new
                self._sessions[target] = session
                moved += 1
        return moved

    async def dispose_all(self) -> None:
        """Kill every child. Safe to call at daemon shutdown."""
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            await session.dispose()
