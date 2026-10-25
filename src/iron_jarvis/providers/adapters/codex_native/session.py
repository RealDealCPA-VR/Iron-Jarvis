"""One ``codex app-server`` process per live conversation (IJ harness phase 2).

* SPAWNED through ``core.procs.spawn`` (v1.333.0): its own kill-on-close Job on
  Windows (nothing it started outlives the daemon, even after a crash),
  below-normal priority in the background lane, no console window. Killed as a
  TREE (``procs.kill_tree``, the v1.287.0 discipline) on cancel, timeout, an
  error, the idle reaper and the live-session cap; a turn that ENDS closes its
  stdin first so Codex can exit on its own, then the tree is killed anyway.
* NOTHING BLOCKING ON THE LOOP: a reader thread parses stdout into a
  thread-safe queue (and keeps a bounded stderr tail); the loop is woken
  through a per-call waker, so a session started on one event loop can be
  resumed on another (a schedule fire runs under ``asyncio.run``). Every
  write, spawn and kill hops to a worker thread.
* THE POOL holds a session between the adapter calls of one Codex turn: when
  Codex asks for tools, the session is PARKED under those call ids; the next
  call whose conversation answers exactly those ids CLAIMS it. Bounded: at
  most :data:`MAX_LIVE_SESSIONS` (the oldest PARKED one is stopped to make
  room; when all are streaming a new one is refused in words), and a parked
  session nobody resumes within :data:`PAUSED_IDLE_TIMEOUT_S` is stopped by a
  reaper thread.
* THE FEATURE VERDICT is cached per codex binary (path, size, mtime): ``True``
  once a session started with the posture we asked for, ``False`` (logged ONCE)
  when this Codex does not offer the shapes — the adapter then falls back to
  the text-only ``codex exec`` path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import subprocess
import tempfile
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any

from ....core import procs

log = logging.getLogger("ironjarvis.codex_native")

#: A live session that has had no event for this long while a call is
#: streaming it is a dead call (reset by every event — reasoning summaries
#: and deltas keep a long answer alive). Read at CALL time: tests shrink it.
DEFAULT_IDLE_TIMEOUT_S = 300.0
#: The outer bound on one adapter call (a call that keeps trickling).
TOTAL_TIMEOUT_S = 3600.0
#: initialize / config/read / thread/start / turn/start, each.
START_TIMEOUT_S = 30.0
#: A PARKED session (Codex waiting on tool results) nobody resumed.
PAUSED_IDLE_TIMEOUT_S = 600.0
#: Live sessions at once (one app-server process each).
MAX_LIVE_SESSIONS = 6
#: After Codex asks for a tool: how long to keep reading for more calls of the
#: same batch before handing the batch back. Codex 0.157.0 runs dynamic tools
#: one at a time (``supports_parallel_tool_calls`` is false for them), so a
#: batch is usually one call; the window only gathers requests that are
#: already on their way. It cannot gather the round's token usage: Codex
#: sends that only AFTER the round's tools are answered (core/src/session/
#: turn.rs, "emit token counts only after pending tools resolve"), so it lands
#: on the next call — a turn's TOTAL is exact.
SETTLE_S = 0.05
#: One write to the app-server's stdin.
WRITE_TIMEOUT_S = 30.0
#: How long a finished session's process gets to exit after its stdin closes.
EXIT_GRACE_S = 3.0

_EOF = object()
_EMPTY = object()


class Bad:
    """A stdout line that is not a JSON object."""

    def __init__(self, line: str) -> None:
        self.line = line


class SessionGone(RuntimeError):
    """The app-server process ended (EOF on stdout)."""


class PoolFull(RuntimeError):
    """Every live session is streaming; none may be stopped to make room."""


# --------------------------------------------------------------------------- #
# The channel: reader thread -> whichever loop is reading now
# --------------------------------------------------------------------------- #
class Channel:
    """A thread-safe queue plus a per-call waker. The reader thread puts every
    item in the queue, then wakes the loop that is currently bound (if any);
    nothing is ever lost between calls because the queue IS the store."""

    def __init__(self) -> None:
        self._q: queue.SimpleQueue = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._waker: tuple[asyncio.AbstractEventLoop, asyncio.Event] | None = None

    def post(self, item: Any) -> None:
        self._q.put(item)
        with self._lock:
            waker = self._waker
        if waker is not None:
            loop, event = waker
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:
                pass  # that loop is gone; the next bind finds the item queued

    def bind(self) -> asyncio.Event:
        event = asyncio.Event()
        with self._lock:
            self._waker = (asyncio.get_running_loop(), event)
        return event

    def unbind(self) -> None:
        with self._lock:
            self._waker = None

    def get_nowait(self) -> Any:
        try:
            return self._q.get_nowait()
        except queue.Empty:
            return _EMPTY


def _parse_line(raw: bytes) -> Any:
    line = raw.decode("utf-8", errors="replace").strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except ValueError:
        return Bad(line[:300])
    return obj if isinstance(obj, dict) else Bad(line[:300])


def stable_workdir() -> str:
    """One private working directory for every app-server of this OS user.
    The thread has no environment, so nothing is read or written there; Codex
    only needs an absolute path that exists."""
    path = os.path.join(tempfile.gettempdir(), "ironjarvis-codex-cwd")
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
        return path
    except OSError:
        return tempfile.mkdtemp(prefix="ironjarvis-codex-cwd-")


# --------------------------------------------------------------------------- #
# One session
# --------------------------------------------------------------------------- #
class CodexSession:
    """One app-server process and the one thread/turn it runs."""

    def __init__(self, exe: str, env: dict[str, str] | None, *, lease: Any = None) -> None:
        self.id = uuid.uuid4().hex
        self.exe = exe
        self.env = env
        self.lease = lease
        #: Set by :meth:`spawn` (a worker thread): :func:`stable_workdir` makes
        #: a directory, and a constructor runs on the event loop.
        self.cwd = ""
        self.proc: subprocess.Popen | None = None
        self.channel = Channel()
        self.deferred: deque = deque()
        self.stderr_tail: deque[bytes] = deque(maxlen=64)
        self.threads: list[threading.Thread] = []
        self._wlock = threading.Lock()
        self._id_lock = threading.Lock()
        self._kill_lock = threading.Lock()
        self._next_id = 0
        self.killed = False
        #: new | streaming | paused | done | closed — guarded by the pool lock.
        #: ``done``: its turn ENDED and it is kept for the same lane turn's
        #: next call to continue (``providers.turn_scope``, the finish check).
        self.state = "new"
        self.paused_at = 0.0
        #: The key a ``done`` session is kept under (:meth:`Pool.park_done`).
        self.done_key: Any = None
        self.thread_id = ""
        self.turn_id = ""
        self.names: Any = None
        #: Codex call id -> the server request id waiting on its answer.
        self.pending: dict[str, Any] = {}
        #: The thread's running token total (``ThreadTokenUsage.total``).
        self.usage_total: dict[str, Any] | None = None
        self.rate_limits: dict[str, Any] | None = None
        self.served_model = ""
        #: Our fire-and-forget request ids -> what to do with the answer.
        self.side_requests: dict[Any, str] = {}
        #: Built-ins Codex asked to run (denied) or ran anyway, for the response.
        self.refused_builtins: list[str] = []
        self.builtin_items: list[str] = []

    # -- ids ---------------------------------------------------------------
    def new_id(self) -> int:
        with self._id_lock:
            self._next_id += 1
            return self._next_id

    # -- process (blocking; worker thread only) ------------------------------
    def spawn(self) -> None:
        argv = [self.exe, "app-server", "--listen", "stdio://"]
        if not self.cwd:
            self.cwd = stable_workdir()
        kw: dict[str, Any] = {}
        if self.env is not None:
            kw["env"] = self.env
        proc = procs.spawn(  # argv list, no shell
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=self.cwd, **kw,
        )
        self.proc = proc
        with self._kill_lock:
            killed = self.killed
        if killed:  # stopped while it was starting: honour it here
            procs.kill_tree(proc)
            try:
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                pass
            procs.release(proc)
            raise SessionGone("stopped while starting")
        channel = self.channel

        def read_stdout() -> None:
            try:
                for raw in proc.stdout:  # type: ignore[union-attr]
                    item = _parse_line(raw)
                    if item is not None:
                        channel.post(item)
            except Exception:  # noqa: BLE001 — a dead pipe ends the stream
                pass
            finally:
                channel.post(_EOF)

        def read_stderr() -> None:
            try:
                for chunk in iter(lambda: proc.stderr.read1(4096), b""):  # type: ignore[union-attr]
                    self.stderr_tail.append(chunk)
            except Exception:  # noqa: BLE001
                pass

        for target, name in ((read_stdout, "codex-app-server-out"), (read_stderr, "codex-app-server-err")):
            t = threading.Thread(target=target, daemon=True, name=name)
            t.start()
            self.threads.append(t)

    def write_sync(self, data: bytes) -> None:
        proc = self.proc
        if proc is None or proc.stdin is None:
            raise SessionGone("no process")
        with self._wlock:
            proc.stdin.write(data)
            proc.stdin.flush()

    def stderr_text(self) -> str:
        return b"".join(self.stderr_tail).decode("utf-8", errors="replace")[-2000:]

    def drain_stderr(self, timeout: float) -> None:
        """BLOCKING (worker thread): give the stderr reader up to ``timeout``
        seconds to take the last words of a process that has stopped."""
        for t in self.threads:
            if t.name == "codex-app-server-err":
                t.join(timeout=timeout)

    def kill(self, *, graceful: bool = False) -> None:
        """Stop the process and everything it started; idempotent, never
        raises. ``graceful`` (a turn that ENDED): stdin is closed first and
        Codex gets :data:`EXIT_GRACE_S` to exit before the tree is killed."""
        with self._kill_lock:
            if self.killed:
                return
            self.killed = True
        proc = self.proc
        if proc is None:
            return
        if graceful and self._wlock.acquire(timeout=1.0):  # a wedged writer: just kill
            try:
                if proc.stdin and not proc.stdin.closed:
                    proc.stdin.close()
            except Exception:  # noqa: BLE001 — fall through to the kill
                pass
            finally:
                self._wlock.release()
            try:
                proc.wait(timeout=EXIT_GRACE_S)
            except Exception:  # noqa: BLE001 — fall through to the kill
                pass
        try:
            if proc.poll() is None:
                procs.kill_tree(proc)
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001 — a wedged reap must not hang teardown
            pass
        procs.release(proc)
        for t in self.threads:
            t.join(timeout=5)
        readers_done = not any(t.is_alive() for t in self.threads)
        pipes = (proc.stdin, proc.stdout, proc.stderr) if readers_done else (proc.stdin,)
        for pipe in pipes:
            try:
                if pipe and not pipe.closed:
                    pipe.close()
            except Exception:  # noqa: BLE001
                pass

    # -- JSON-RPC (async) ----------------------------------------------------
    async def send(self, obj: dict[str, Any]) -> None:
        data = (json.dumps(obj, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        try:
            await asyncio.wait_for(asyncio.to_thread(self.write_sync, data), timeout=WRITE_TIMEOUT_S)
        except (BrokenPipeError, ConnectionResetError, OSError, ValueError) as exc:
            raise SessionGone(f"could not write to the app-server ({type(exc).__name__})") from exc
        except TimeoutError as exc:
            raise SessionGone("the app-server stopped reading its input") from exc

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        msg: dict[str, Any] = {"method": method}
        if params is not None:
            msg["params"] = params
        await self.send(msg)

    async def respond(self, request_id: Any, result: dict[str, Any]) -> None:
        await self.send({"id": request_id, "result": result})

    async def respond_error(self, request_id: Any, code: int, message: str) -> None:
        await self.send({"id": request_id, "error": {"code": code, "message": message}})

    async def _raw_next(self, timeout: float) -> Any:
        event = self.channel.bind()
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            event.clear()
            item = self.channel.get_nowait()
            if item is not _EMPTY:
                return item
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            await asyncio.wait_for(event.wait(), remaining)

    async def next_event(self, timeout: float) -> Any:
        """The next message from Codex (``_EOF`` at the end of its output);
        raises ``TimeoutError`` after ``timeout`` seconds of silence."""
        if self.deferred:
            return self.deferred.popleft()
        return await self._raw_next(timeout)

    async def request(self, method: str, params: dict[str, Any] | None, timeout: float) -> dict[str, Any]:
        """Send a request and wait for ITS response; anything else that
        arrives meanwhile is kept, in order, for :meth:`next_event`."""
        rid = self.new_id()
        msg: dict[str, Any] = {"id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        await self.send(msg)
        deadline = time.monotonic() + timeout
        while True:
            item = await self._raw_next(max(0.0, deadline - time.monotonic()))
            if item is _EOF:
                self.deferred.append(item)
                raise SessionGone(f"the app-server exited during {method}")
            if is_response(item) and item.get("id") == rid:
                return item
            self.deferred.append(item)

    async def side_request(self, method: str, params: dict[str, Any] | None, purpose: str) -> None:
        """Send a request whose answer is handled whenever it arrives."""
        rid = self.new_id()
        self.side_requests[rid] = purpose
        msg: dict[str, Any] = {"id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        await self.send(msg)

    def release_channel(self) -> None:
        self.channel.unbind()


def is_eof(item: Any) -> bool:
    return item is _EOF


def is_response(item: Any) -> bool:
    return isinstance(item, dict) and "id" in item and "method" not in item and (
        "result" in item or "error" in item
    )


def is_server_request(item: Any) -> bool:
    return isinstance(item, dict) and "id" in item and isinstance(item.get("method"), str)


def is_notification(item: Any) -> bool:
    return isinstance(item, dict) and "id" not in item and isinstance(item.get("method"), str)


# --------------------------------------------------------------------------- #
# The pool of live sessions
# --------------------------------------------------------------------------- #
class Pool:
    """Live sessions, indexed by the call ids a PARKED one waits on."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self.sessions: dict[str, CodexSession] = {}
        self.by_call: dict[str, str] = {}
        #: ``done`` sessions by the key the lane's next call must present.
        self.by_done: dict[Any, str] = {}
        self._reaper: threading.Thread | None = None

    # all under the lock -----------------------------------------------------
    def _unindex(self, s: CodexSession) -> None:
        for cid in list(s.pending):
            if self.by_call.get(cid) == s.id:
                del self.by_call[cid]
        if s.done_key is not None and self.by_done.get(s.done_key) == s.id:
            del self.by_done[s.done_key]
        s.done_key = None

    def _forget(self, s: CodexSession) -> None:
        self.sessions.pop(s.id, None)
        self._unindex(s)
        s.state = "closed"

    # API --------------------------------------------------------------------
    def admit(self, s: CodexSession) -> list[CodexSession]:
        """Register a new session (state ``streaming``). Returns the PARKED
        sessions stopped to keep under the cap (the caller kills them off the
        loop); raises :class:`PoolFull` when every live one is streaming."""
        cap = max(1, int(MAX_LIVE_SESSIONS))
        victims: list[CodexSession] = []
        with self.lock:
            while len(self.sessions) >= cap:
                parked = sorted(
                    (x for x in self.sessions.values() if x.state in ("paused", "done")),
                    key=lambda x: x.paused_at,
                )
                if not parked:
                    raise PoolFull(
                        f"Codex is already working on {len(self.sessions)} conversations at once "
                        "— try again when one finishes"
                    )
                victim = parked[0]
                self._forget(victim)
                victims.append(victim)
            s.state = "streaming"
            self.sessions[s.id] = s
        return victims

    def park(self, s: CodexSession) -> bool:
        """The session waits for tool results under its pending call ids.
        False when it was stopped meanwhile (it is then not parked)."""
        with self.lock:
            if s.id not in self.sessions or s.killed:
                return False
            s.state = "paused"
            s.paused_at = time.monotonic()
            for cid in s.pending:
                self.by_call[cid] = s.id
            self._ensure_reaper()
            self.cond.notify_all()
        return True

    def park_done(self, s: CodexSession, key: Any) -> bool:
        """Keep a session whose turn ENDED under ``key`` (the lane's next call
        of the same turn scope continues it). A session already kept under
        that key is replaced (returned through :meth:`release_done` by its
        own scope). False when it was stopped meanwhile."""
        with self.lock:
            if s.id not in self.sessions or s.killed:
                return False
            self._unindex(s)
            s.state = "done"
            s.paused_at = time.monotonic()
            s.done_key = key
            self.by_done[key] = s.id
            self._ensure_reaper()
            self.cond.notify_all()
        return True

    def claim_done(self, key: Any) -> CodexSession | None:
        """The ``done`` session kept under ``key`` (now ``streaming``), or
        None."""
        with self.lock:
            sid = self.by_done.get(key)
            s = self.sessions.get(sid) if sid is not None else None
            if s is None or s.state != "done" or s.killed:
                return None
            self._unindex(s)
            s.state = "streaming"
            return s

    def release_done(self, s: CodexSession) -> bool:
        """Forget ``s`` when it is still a kept ``done`` session (its scope
        closed): True when the caller must stop it now."""
        with self.lock:
            if s.id in self.sessions and s.state == "done":
                self._forget(s)
                return True
            return False

    def claim(self, call_ids: list[str]) -> tuple[CodexSession | None, list[CodexSession]]:
        """``(session, stale)``: the PARKED session waiting on exactly
        ``call_ids`` (now ``streaming``, unindexed), or None. ``stale`` are
        parked sessions this conversation's calls point at that can no longer
        be resumed (they were forgotten; the caller kills them)."""
        wanted = set(call_ids)
        with self.lock:
            sids = {self.by_call.get(c) for c in call_ids} - {None}
            if len(sids) == 1:
                s = self.sessions.get(next(iter(sids)))  # type: ignore[arg-type]
                if s is not None and s.state == "paused" and set(s.pending) == wanted and not s.killed:
                    self._unindex(s)
                    s.state = "streaming"
                    return s, []
            stale = []
            for sid in sids:
                s = self.sessions.get(sid)  # type: ignore[arg-type]
                if s is not None and s.state == "paused":
                    self._forget(s)
                    stale.append(s)
            return None, stale

    def release_calls(self, call_ids: list[str]) -> list[CodexSession]:
        """Forget every PARKED session waiting on any of ``call_ids`` (the
        conversation moved past them — an escalation, a nudge, an edit) and
        return them for the caller to kill now rather than at the reaper."""
        with self.lock:
            sids = {self.by_call.get(c) for c in call_ids} - {None}
            gone = []
            for sid in sids:
                s = self.sessions.get(sid)  # type: ignore[arg-type]
                if s is not None and s.state == "paused":
                    self._forget(s)
                    gone.append(s)
            return gone

    def forget(self, s: CodexSession) -> None:
        with self.lock:
            self._forget(s)

    def live(self) -> list[CodexSession]:
        with self.lock:
            return list(self.sessions.values())

    def stop_all(self) -> int:
        """BLOCKING (worker thread): stop EVERY live session — parked or
        streaming — and its process tree; returns how many. The daemon's
        lifespan calls this at shutdown, like the REPL children and the kept
        process jobs: a parked app-server must not wait for the reaper (or for
        the Job handle to close at process exit) when the daemon is stopping."""
        with self.lock:
            every = list(self.sessions.values())
            for s in every:
                self._forget(s)
            self.cond.notify_all()
        for s in every:
            try:
                s.kill()
            except Exception:  # noqa: BLE001 — shutdown never raises
                log.debug("codex-cli: stopping a session at shutdown failed", exc_info=True)
        return len(every)

    # the reaper -------------------------------------------------------------
    def _ensure_reaper(self) -> None:
        if self._reaper is not None and self._reaper.is_alive():
            return
        self._reaper = threading.Thread(target=self._reap_loop, daemon=True, name="codex-session-reaper")
        self._reaper.start()

    def reap_once(self, now: float | None = None) -> list[CodexSession]:
        """Forget every parked session past the idle timeout and return them
        (to be killed outside the lock)."""
        now = time.monotonic() if now is None else now
        limit = float(PAUSED_IDLE_TIMEOUT_S)
        with self.lock:
            expired = [s for s in self.sessions.values()
                       if s.state in ("paused", "done") and now - s.paused_at >= limit]
            for s in expired:
                self._forget(s)
        return expired

    def _reap_loop(self) -> None:
        while True:
            expired = self.reap_once()
            for s in expired:
                log.info("codex-cli: stopping a Codex session nobody resumed (idle)")
                s.kill()
            with self.lock:
                parked = [s for s in self.sessions.values() if s.state in ("paused", "done")]
                if not parked:
                    self._reaper = None
                    return
                limit = float(PAUSED_IDLE_TIMEOUT_S)
                soonest = min(s.paused_at for s in parked) + limit
                self.cond.wait(timeout=max(0.05, min(30.0, soonest - time.monotonic())))


POOL = Pool()


# --------------------------------------------------------------------------- #
# The feature verdict
# --------------------------------------------------------------------------- #
#: A NEGATIVE verdict is checked again after this long (a broken config the
#: user fixed, a fluke at start). Read at call time.
NEGATIVE_VERDICT_TTL_S = 600.0


@dataclass(frozen=True)
class Verdict:
    key: tuple
    ok: bool
    reason: str = ""
    at: float = 0.0

    def expired(self) -> bool:
        return (not self.ok) and time.monotonic() - self.at >= float(NEGATIVE_VERDICT_TTL_S)


_VERDICT: Verdict | None = None
_VERDICT_LOCK = threading.Lock()


def exe_key(exe: str) -> tuple:
    """BLOCKING (a stat): what identifies an installed codex binary."""
    try:
        st = os.stat(exe)
        return (os.path.normcase(os.path.abspath(exe)), st.st_size, st.st_mtime_ns)
    except OSError:
        return (os.path.normcase(os.path.abspath(exe)), None, None)


def verdict() -> Verdict | None:
    with _VERDICT_LOCK:
        return _VERDICT


def verdict_for(key: tuple) -> Verdict | None:
    """The verdict for THIS binary, or None (unknown, or a negative one past
    :data:`NEGATIVE_VERDICT_TTL_S`: check on the next start)."""
    v = verdict()
    return v if v is not None and v.key == key and not v.expired() else None


def set_verdict(key: tuple, ok: bool, reason: str = "") -> None:
    """Record what the last start found; a NEW negative verdict is said ONCE
    (again only after the old one expired and the check failed anew)."""
    global _VERDICT
    with _VERDICT_LOCK:
        before = _VERDICT
        _VERDICT = Verdict(key, ok, reason, time.monotonic())
    said = before is not None and before.key == key and not before.ok and not before.expired()
    if not ok and not said:
        log.warning(
            "codex-cli: this Codex does not offer the app-server shapes Iron Jarvis needs for "
            "native tool calls (%s) — using the text-only `codex exec` path instead",
            reason,
        )


def reset_verdict() -> None:
    """Tests: forget the verdict."""
    global _VERDICT
    with _VERDICT_LOCK:
        _VERDICT = None
