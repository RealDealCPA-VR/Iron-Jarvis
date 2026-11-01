"""The Code view's side of the code host (v1.422.0; ``iron_jarvis/codehost.py``).

:class:`RemoteProcess` is a :class:`.process.HarnessProcess` whose child lives
in the code host instead of the daemon: the same methods (``start``,
``write``, ``stop``, ``stderr_text``, ``note_started``, ``alive``, ``pid``,
``stopped``, ``exit_code``), so the service and every driver use it
unchanged. What it adds:

* every line the tool wrote carries the host's number for it (``seq``).
  Lines are handed to the driver ON THE LOOP one at a time, each followed by
  one loop turn (``asyncio.sleep(0)``) so whatever that line set going (a
  reply future's callback, a task's first step) runs while the line is still
  "current" (:attr:`RemoteProcess.current`) — the service stamps every event
  it logs with the line it came from, and a restarted daemon knows from its
  own log which lines were already shown (``hosted/adopt.py``);
* a permission card's id is derived from that line (``driver.ASK_SEED``), so
  the same line read again after a restart raises the SAME card id;
* about a second after lines arrive, and just after every write, the
  driver's and the session's state is sent to the host (``snapshot``); the
  host keeps the newest and the lines after it, which is all a restarted
  daemon needs to carry on (:meth:`RemoteProcess.attach`).

No code host (it could not start, or ``IRONJARVIS_CODE_HOST=off``): the tool
is started exactly as before (a plain :class:`.process.HarnessProcess`), so a
missing host never costs the user the Code view — only the restart survival,
which the session then says once (:data:`NO_HOST_NOTE`).

Nothing here blocks the loop: :func:`link_for`, :meth:`RemoteProcess.start`,
``write``, ``stop`` and ``attach`` are BLOCKING and called through
``asyncio.to_thread``; the link reads on its own thread and sends through
another, so a slow host never stalls a coroutine.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import json
import logging
import os
import socket
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .. import codehost as CH
from .driver import ASK_SEED
from .process import HarnessGone, HarnessProcess, decode_line

log = logging.getLogger(__name__)

#: ``off`` keeps every tool a child of the daemon (the pre-v1.422.0 way; the
#: test suite's default — a host per test would outlive the test).
ENV_SWITCH = "IRONJARVIS_CODE_HOST"
#: How long a start waits for a new host to answer.
START_WAIT_S = 20.0
CONNECT_TIMEOUT_S = 3.0
REPLY_TIMEOUT_S = 30.0
#: A write is answered once it reached the tool's stdin.
WRITE_REPLY_S = 120.0
#: A stop waits for the tool to save and go (graceful + kill + reap).
STOP_REPLY_S = 60.0
#: A host told to quit (another version) stops its tools gracefully first.
RETIRE_WAIT_S = 45.0
#: State sent to the host this long after lines arrive (debounced) …
SNAP_AFTER_LINE_S = 1.0
#: … and this long after a write (the caller's bookkeeping runs first).
SNAP_AFTER_WRITE_S = 0.05

NO_HOST_NOTE = ("Iron Jarvis could not start the helper that keeps coding tools running across a restart, "
                "so this session stops if Iron Jarvis restarts. Your conversation is kept either way.")
HOST_GONE = "the helper that runs the coding tools stopped"


def enabled() -> bool:
    return os.environ.get(ENV_SWITCH, "").strip().lower() not in ("off", "0", "false", "no")


def _key(home: Any) -> str:
    return os.path.normcase(os.path.abspath(str(home)))


class HostLost(RuntimeError):
    """The connection to the code host is gone."""


# --------------------------------------------------------------------------- #
# The connection
# --------------------------------------------------------------------------- #
class HostLink:
    """One authenticated connection to a code host. Requests are answered by
    ``re`` number; a tool's frames go to its :class:`RemoteProcess`."""

    def __init__(self, home: Any, sock: socket.socket, rfile: Any, welcome: dict[str, Any]) -> None:
        self.home = Path(home)
        self.sock = sock
        self.rfile = rfile
        self.welcome = welcome
        self.pid = int(welcome.get("pid") or 0)
        self.alive = True
        self._n = 0
        self._lock = threading.Lock()
        self._pending: dict[int, list[Any]] = {}
        self._procs: dict[str, "RemoteProcess"] = {}
        self._q: collections.deque[tuple[dict[str, Any], bytes]] = collections.deque()
        self._cv = threading.Condition()
        threading.Thread(target=self._read_loop, name="codehost-link-read", daemon=True).start()
        threading.Thread(target=self._send_loop, name="codehost-link-send", daemon=True).start()

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [t for t in self.welcome.get("tools") or [] if isinstance(t, dict)]

    # -- sending -----------------------------------------------------------------
    def send(self, header: dict[str, Any], payload: bytes = b"") -> None:
        """Queue one message (never blocks)."""
        with self._cv:
            if not self.alive:
                return
            self._q.append((header, payload))
            self._cv.notify()

    def _send_loop(self) -> None:
        while True:
            with self._cv:
                while self.alive and not self._q:
                    self._cv.wait(timeout=5.0)
                if not self.alive:
                    return
                header, payload = self._q.popleft()
            try:
                CH.send_frame(self.sock, header, payload)
            except OSError:
                self._lost()
                return

    def request(self, header: dict[str, Any], payload: bytes = b"",
                timeout: float = REPLY_TIMEOUT_S) -> tuple[dict[str, Any], bytes]:
        """BLOCKING: one message and its answer."""
        done = threading.Event()
        slot: list[Any] = [done, None, b""]
        with self._lock:
            if not self.alive:
                raise HostLost(HOST_GONE)
            self._n += 1
            re = self._n
            self._pending[re] = slot
        self.send({**header, "re": re}, payload)
        if not done.wait(timeout):
            with self._lock:
                self._pending.pop(re, None)
            raise TimeoutError(f"the code host did not answer {header.get('t')!r}")
        if slot[1] is None:
            raise HostLost(HOST_GONE)
        return slot[1], slot[2]

    # -- reading -----------------------------------------------------------------
    def register(self, tool: str, proc: "RemoteProcess") -> None:
        with self._lock:
            self._procs[tool] = proc

    def unregister(self, tool: str) -> None:
        with self._lock:
            self._procs.pop(tool, None)

    def _read_loop(self) -> None:
        try:
            while True:
                got = CH.read_frame(self.rfile)
                if got is None:
                    break
                header, payload = got
                kind = header.get("t")
                if kind == "re":
                    with self._lock:
                        slot = self._pending.pop(header.get("re"), None)
                    if slot is not None:
                        slot[1], slot[2] = header, payload
                        slot[0].set()
                    continue
                with self._lock:
                    proc = self._procs.get(str(header.get("tool") or ""))
                if proc is None:
                    continue
                if kind == "line":
                    proc._line(int(header.get("seq") or 0), payload)
                elif kind == "exit":
                    proc._exit(header.get("code"), str(header.get("stderr") or ""))
                elif kind == "replayed":
                    proc._replayed(int(header.get("seq") or 0))
        except Exception:  # noqa: BLE001 — a broken read is a lost host
            log.debug("the code host link stopped reading", exc_info=True)
        self._lost()

    def _lost(self) -> None:
        with self._lock:
            if not self.alive:
                return
            self.alive = False
            pending, self._pending = self._pending, {}
            procs = list(self._procs.values())
        with self._cv:
            self._cv.notify_all()
        for slot in pending.values():
            slot[0].set()
        for proc in procs:
            proc._link_lost()
        with contextlib.suppress(OSError):
            self.sock.close()
        with _REG_LOCK:
            if _LINKS.get(_key(self.home)) is self:
                _LINKS.pop(_key(self.home), None)

    # -- the host as a whole ------------------------------------------------------
    def quit(self, timeout: float = RETIRE_WAIT_S) -> None:
        """BLOCKING: stop every tool gracefully and let the host go."""
        with contextlib.suppress(Exception):
            self.request({"t": "quit", "graceful": True}, timeout=timeout)
        self.close()

    def detach(self) -> None:
        """BLOCKING (bounded): leave the host and its tools running — a restart."""
        self.send({"t": "detach"})
        end = time.monotonic() + 2.0
        while time.monotonic() < end:
            with self._cv:
                if not self._q:
                    break
            time.sleep(0.02)
        self.close()

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self.sock.shutdown(socket.SHUT_RDWR)
        self._lost()


# --------------------------------------------------------------------------- #
# Finding / starting the host
# --------------------------------------------------------------------------- #
_LINKS: dict[str, HostLink] = {}
_REG_LOCK = threading.RLock()


def _pid_alive(pid: Any) -> bool:
    try:
        import psutil

        return bool(pid) and psutil.pid_exists(int(pid)) and psutil.Process(int(pid)).is_running()
    except Exception:  # noqa: BLE001
        return False


def _hello(home: Any, rec: dict[str, Any]) -> tuple[socket.socket, Any, dict[str, Any]] | None:
    """BLOCKING: connect and say hello; the welcome, or None."""
    from .. import __version__

    try:
        port = int(rec.get("port") or 0)
        sock = socket.create_connection(("127.0.0.1", port), timeout=CONNECT_TIMEOUT_S)
    except (OSError, ValueError, TypeError):
        return None
    try:
        sock.settimeout(CONNECT_TIMEOUT_S + 2.0)
        CH.send_frame(sock, {"t": "hello", "token": str(rec.get("token") or ""), "protocol": CH.PROTOCOL,
                             "version": __version__})
        rfile = sock.makefile("rb")
        got = CH.read_frame(rfile)
        if got is None or got[0].get("t") != "welcome":
            sock.close()
            return None
        sock.settimeout(None)
        return sock, rfile, got[0]
    except OSError:
        with contextlib.suppress(OSError):
            sock.close()
        return None


def _connect_existing(home: Any, *, retire_mismatch: bool = True) -> HostLink | None:
    """BLOCKING: a link to the host the record names — a host of another
    protocol or version is told to quit (its tools save and stop; a CLI's own
    resume carries them on) and None is returned."""
    from .. import __version__

    rec = CH.read_record(home)
    if rec is None:
        return None
    if not _pid_alive(rec.get("pid")):
        with contextlib.suppress(Exception):
            CH.remove_record(home, rec.get("pid"))
        return None
    got = _hello(home, rec)
    if got is None:
        return None
    sock, rfile, welcome = got
    lk = HostLink(home, sock, rfile, welcome)
    if welcome.get("protocol") != CH.PROTOCOL or str(welcome.get("version") or "") != __version__:
        if retire_mismatch:
            log.info("a code host of another version (%s) runs: telling it to stop its tools",
                     welcome.get("version"))
            lk.quit()
            end = time.monotonic() + 10.0
            while time.monotonic() < end and _pid_alive(rec.get("pid")):
                time.sleep(0.1)
        else:
            lk.close()
        return None
    return lk


def _host_env() -> dict[str, str]:
    """The host's OWN environment: the daemon's, minus its credentials (each
    tool gets the environment the daemon built for it, in its start)."""
    out = {}
    for k, v in os.environ.items():
        up = k.upper()
        if any(w in up for w in ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "APIKEY")):
            continue
        out[k] = v
    return out


def _launch(home: Any) -> bool:
    """BLOCKING: start the launcher (which starts the host and exits)."""
    try:
        p = CH.popen_detached(CH.host_argv(home, launch=True), flags=CH.CREATE_NO_WINDOW, env=_host_env())
    except OSError:
        log.warning("could not start the code host", exc_info=True)
        return False
    try:
        return p.wait(timeout=15) == 0
    except Exception:  # noqa: BLE001
        return False


def link_for(home: Any, *, start: bool = True, wait_s: float = START_WAIT_S) -> HostLink | None:
    """BLOCKING: the link to this home's code host — started when ``start``
    and none answers — or None."""
    key = _key(home)
    with _REG_LOCK:
        lk = _LINKS.get(key)
        if lk is not None and lk.alive:
            return lk
        lk = _connect_existing(home)
        if lk is None and start:
            before = (CH.read_record(home) or {}).get("pid")
            if _launch(home):
                end = time.monotonic() + wait_s
                while time.monotonic() < end and lk is None:
                    rec = CH.read_record(home)
                    if rec is not None and rec.get("pid") != before:
                        lk = _connect_existing(home)
                    if lk is None:
                        time.sleep(0.1)
        if lk is not None:
            _LINKS[key] = lk
        return lk


def live_session_ids(home: Any) -> list[str]:
    """BLOCKING (boot): the Code-view sessions a code host still RUNS for
    this home (never starts one; a host of another version is told to quit
    and none of its sessions count)."""
    try:
        lk = link_for(home, start=False)
    except Exception:  # noqa: BLE001
        log.warning("could not ask the code host what it runs", exc_info=True)
        return []
    if lk is None:
        return []
    return sorted({str(t.get("sid") or "") for t in lk.tools if t.get("alive") and t.get("sid")})


def quit_host(home: Any, timeout: float = RETIRE_WAIT_S) -> bool:
    """BLOCKING: Iron Jarvis is QUITTING — the host (ours or one a crash left)
    stops every tool gracefully and exits. True when one was told."""
    with _REG_LOCK:
        lk = _LINKS.pop(_key(home), None)
    if lk is None or not lk.alive:
        # Any host the record names — of this version or another (every host
        # takes ``quit`` whatever its protocol).
        rec = CH.read_record(home)
        got = _hello(home, rec) if rec and _pid_alive(rec.get("pid")) else None
        if got is None:
            return False
        lk = HostLink(home, *got)
    lk.quit(timeout)
    return True


def detach_host(home: Any) -> bool:
    """BLOCKING: Iron Jarvis is RESTARTING — leave the host and its tools."""
    with _REG_LOCK:
        lk = _LINKS.pop(_key(home), None)
    if lk is None or not lk.alive:
        return False
    lk.detach()
    return True


# --------------------------------------------------------------------------- #
# One tool in the host
# --------------------------------------------------------------------------- #
def stamp_of(proc: Any) -> tuple[str, int] | None:
    """``(tool, line)`` an event logged now comes from — the line being read,
    else the newest one read — for a tool in the code host; None otherwise."""
    if not isinstance(proc, RemoteProcess) or not proc.in_host:
        return None
    cur = proc.current
    return proc.tool_id, int(cur if cur is not None else proc.last_seq)


def kept_by_host(proc: Any) -> bool:
    """A tool the code host keeps running across a restart."""
    return isinstance(proc, RemoteProcess) and proc.in_host and proc.alive


def new_process(argv: list[str], *, env: dict[str, str], cwd: str, name: str, ends_left_behind: bool,
                home: Any, sid: str) -> "RemoteProcess":
    return RemoteProcess(argv, env=env, cwd=cwd, name=name, ends_left_behind=ends_left_behind, home=home, sid=sid)


class RemoteProcess:
    """A harness running in the code host (see the module docstring)."""

    def __init__(self, argv: list[str], *, env: dict[str, str], cwd: str, name: str,
                 ends_left_behind: bool = False, home: Any, sid: str, tool_id: str = "") -> None:
        import uuid

        self.argv = list(argv)
        self.env = env
        self.cwd = cwd
        self.name = name
        self.ends_left_behind = bool(ends_left_behind)
        self.home = Path(home)
        self.sid = sid
        self.tool_id = tool_id or uuid.uuid4().hex
        self.stopped = False
        self.exit_code: int | None = None
        #: The line being handed to the driver right now (None between lines).
        self.current: int | None = None
        #: The newest line fully handed over.
        self.last_seq = 0
        #: v1.422.0 (adoption): ``(shown, written)`` — lines at or below
        #: ``shown`` were already in the session's log, below ``written``
        #: already acted on, before the restart. None once caught up.
        self.replay: tuple[int, int] | None = None
        #: Called (on the loop) when the lines kept over a restart are read.
        self.on_replayed: Callable[[], None] | None = None
        #: ``() -> (state bytes, busy)`` — what is kept for a restart.
        self.snapshot_source: Callable[[], tuple[bytes, bool]] | None = None
        #: The host could not be reached: a plain child of the daemon.
        self.local: HarnessProcess | None = None
        self.link: HostLink | None = None
        self._pid: int | None = None
        self._gone = False
        self._ended = False
        self._stderr = ""
        self._hold = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._on_message: Callable[[dict[str, Any]], None] | None = None
        self._on_exit: Callable[[int | None], None] | None = None
        self._inbox: collections.deque[tuple[Any, ...]] = collections.deque()
        self._drainer: asyncio.Task | None = None
        self._snap_timer: asyncio.TimerHandle | None = None
        self._snap_due = 0.0
        self._stop_lock = threading.Lock()
        self._snap_failed = False

    # -- what the service reads -----------------------------------------------------
    @property
    def alive(self) -> bool:
        if self.local is not None:
            return self.local.alive
        return (not self.stopped and not self._ended and not self._gone and self.link is not None
                and self.link.alive)

    @property
    def pid(self) -> int | None:
        return self.local.pid if self.local is not None else self._pid

    @property
    def in_host(self) -> bool:
        return self.local is None and self.link is not None

    # -- start (BLOCKING) ------------------------------------------------------------
    def start(self, loop: asyncio.AbstractEventLoop, on_message: Callable[[dict[str, Any]], None],
              on_exit: Callable[[int | None], None]) -> None:
        self._loop, self._on_message, self._on_exit = loop, on_message, on_exit
        lk = None
        try:
            lk = link_for(self.home, start=True)
        except Exception:  # noqa: BLE001 — no host: the tool still starts
            log.warning("the code host could not be reached", exc_info=True)
        if lk is None:
            self._start_local(loop, on_message, on_exit)
            return
        self.link = lk
        lk.register(self.tool_id, self)
        try:
            reply, _ = lk.request({"t": "start", "tool": self.tool_id, "sid": self.sid, "argv": self.argv,
                                   "env": self.env, "cwd": self.cwd, "ends_left_behind": self.ends_left_behind},
                                  timeout=STOP_REPLY_S)
        except (HostLost, TimeoutError):
            lk.unregister(self.tool_id)
            self.link = None
            self._start_local(loop, on_message, on_exit)
            return
        if not reply.get("ok"):
            lk.unregister(self.tool_id)
            err = OSError(str(reply.get("error") or "the program could not be started"))
            if reply.get("winerror") is not None:
                err.winerror = reply.get("winerror")  # type: ignore[attr-defined]
            raise err
        self._pid = int(reply.get("pid") or 0) or None

    def _start_local(self, loop: asyncio.AbstractEventLoop, on_message: Callable[[dict[str, Any]], None],
                     on_exit: Callable[[int | None], None]) -> None:
        self.local = HarnessProcess(self.argv, env=self.env, cwd=self.cwd, name=self.name,
                                    ends_left_behind=self.ends_left_behind)
        self.local.start(loop, on_message, on_exit)

    # -- adopt (BLOCKING) -------------------------------------------------------------
    def attach(self, link: HostLink, loop: asyncio.AbstractEventLoop,
               on_message: Callable[[dict[str, Any]], None],
               on_exit: Callable[[int | None], None]) -> tuple[dict[str, Any], bytes]:
        """Take over a tool the host kept across a restart: its kept state and
        every line after it come in order; nothing is handed to the driver
        until :meth:`release` (the caller restores the driver first)."""
        self._loop, self._on_message, self._on_exit = loop, on_message, on_exit
        self.link = link
        self._hold = True
        link.register(self.tool_id, self)
        try:
            reply, blob = link.request({"t": "attach", "tool": self.tool_id}, timeout=REPLY_TIMEOUT_S)
        except Exception:
            link.unregister(self.tool_id)
            raise
        if not reply.get("ok"):
            link.unregister(self.tool_id)
            raise HostLost(str(reply.get("error") or "the code host does not hold this tool"))
        self._pid = int(reply.get("pid") or 0) or None
        self.last_seq = int(reply.get("snap_seq") or 0)
        return reply, blob

    def release(self, replay: tuple[int, int] | None) -> None:
        """ON THE LOOP: hand the held lines to the driver now."""
        self.replay = replay
        self._hold = False
        self._kick()

    # -- lines (reader thread -> loop) -------------------------------------------------
    def _post(self, item: tuple[Any, ...]) -> None:
        self._inbox.append(item)
        loop = self._loop
        if loop is None:
            return
        with contextlib.suppress(RuntimeError):  # the loop closed: nobody to tell
            loop.call_soon_threadsafe(self._kick)

    def _line(self, seq: int, payload: bytes) -> None:
        self._post(("line", seq, decode_line(payload)))

    def _exit(self, code: Any, stderr: str) -> None:
        self._stderr = stderr or self._stderr
        self._post(("exit", code if isinstance(code, int) else None))

    def _replayed(self, seq: int) -> None:
        self._post(("replayed", seq))

    def _link_lost(self) -> None:
        if self.local is not None or self.stopped or self._ended:
            return
        self._gone = True
        self._stderr = self._stderr or HOST_GONE
        self._post(("exit", None))

    def _kick(self) -> None:
        if self._hold or not self._inbox:
            return
        if self._drainer is None or self._drainer.done():
            assert self._loop is not None
            self._drainer = self._loop.create_task(self._drain())

    async def _drain(self) -> None:
        while self._inbox and not self._hold:
            item = self._inbox.popleft()
            kind = item[0]
            if kind == "line":
                seq, obj = item[1], item[2]
                if obj is not None and self._on_message is not None:
                    self.current = seq
                    token = ASK_SEED.set([f"{self.tool_id[:8]}-{seq}", 0])
                    try:
                        self._on_message(obj)
                    except Exception:  # noqa: BLE001 — a line never ends the session
                        log.warning("a coding tool's line could not be read", exc_info=True)
                    finally:
                        ASK_SEED.reset(token)
                    # One loop turn while this line is still current: what it
                    # set going (a future's callback, a task's first step)
                    # belongs to it.
                    await asyncio.sleep(0)
                    self.current = None
                self.last_seq = max(self.last_seq, seq)
                self._want_snapshot(SNAP_AFTER_LINE_S)
            elif kind == "replayed":
                self.replay = None
                cb, self.on_replayed = self.on_replayed, None
                if cb is not None:
                    try:
                        cb()
                    except Exception:  # noqa: BLE001
                        log.warning("a kept Code-view session could not be caught up", exc_info=True)
                self._want_snapshot(0.0)
            elif kind == "exit":
                if self._ended:
                    continue
                self._ended = True
                self.exit_code = item[1]
                if self.link is not None:
                    self.link.send({"t": "forget", "tool": self.tool_id})
                    self.link.unregister(self.tool_id)
                if self._on_exit is not None:
                    self._on_exit(self.exit_code)

    # -- what is kept for a restart ------------------------------------------------------
    def _want_snapshot(self, delay: float) -> None:
        if self.local is not None or self.snapshot_source is None or self._loop is None:
            return
        due = self._loop.time() + delay
        if self._snap_timer is not None and self._snap_due <= due:
            return
        if self._snap_timer is not None:
            self._snap_timer.cancel()
        self._snap_due = due
        self._snap_timer = self._loop.call_later(delay, self._take_snapshot)

    def _take_snapshot(self) -> None:
        self._snap_timer = None
        if not self.alive or self.snapshot_source is None or self.link is None:
            return
        if self.current is not None or self.replay is not None or self._hold:
            self._want_snapshot(0.02)
            return
        try:
            blob, busy = self.snapshot_source()
        except Exception:  # noqa: BLE001 — a state that cannot be kept: said once in the log
            if not self._snap_failed:
                self._snap_failed = True
                log.warning("could not keep a Code-view session's state for a restart", exc_info=True)
            return
        self.link.send({"t": "snapshot", "tool": self.tool_id, "seq": self.last_seq, "busy": bool(busy)}, blob)

    def snapshot_now(self) -> None:
        """ON THE LOOP: keep the state at once (after a start)."""
        self._want_snapshot(0.0)

    def final_snapshot(self) -> None:
        """ON THE LOOP: Iron Jarvis is restarting — keep the state now (not
        in the middle of a line: then the last kept one stands)."""
        if self._snap_timer is not None:
            self._snap_timer.cancel()
            self._snap_timer = None
        if self.current is None and self.replay is None and not self._hold:
            self._take_snapshot()

    def silent(self) -> bool:
        """ON THE LOOP: the line being read again was ALREADY shown before
        the restart — what it says is not said twice."""
        cur = self.current
        return self.replay is not None and cur is not None and cur <= self.replay[0]

    # -- write ------------------------------------------------------------------------
    def drops_write(self) -> bool:
        """ON THE LOOP: a write a line set going that was ALREADY sent before
        the restart (that line is being read again only to rebuild the
        driver) — dropped, never sent twice."""
        cur = self.current
        return self.replay is not None and cur is not None and cur <= self.replay[1]

    async def awrite(self, obj: dict[str, Any]) -> None:
        """ON THE LOOP: write through the host (the service's way)."""
        if self.local is not None:
            await asyncio.to_thread(self.local.write, obj)
            return
        if self.drops_write():
            return
        cur = self.current
        after = cur if cur is not None else self.last_seq
        await asyncio.to_thread(self._write, obj, after)
        self._want_snapshot(SNAP_AFTER_WRITE_S)

    def write(self, obj: dict[str, Any]) -> None:
        """BLOCKING (HarnessProcess's shape)."""
        if self.local is not None:
            self.local.write(obj)
            return
        self._write(obj, self.last_seq)

    def _write(self, obj: dict[str, Any], after: int) -> None:
        lk = self.link
        if lk is None or not self.alive:
            raise HarnessGone("the coding tool is not running")
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            reply, _ = lk.request({"t": "write", "tool": self.tool_id, "after": int(after)}, data,
                                  timeout=WRITE_REPLY_S)
        except (HostLost, TimeoutError) as exc:
            raise HarnessGone(f"the coding tool stopped reading its input ({type(exc).__name__})") from exc
        if not reply.get("ok"):
            raise HarnessGone(f"the coding tool stopped reading its input ({reply.get('error') or 'gone'})")

    def stderr_text(self) -> str:
        if self.local is not None:
            return self.local.stderr_text()
        return self._stderr[-2000:]

    def note_started(self) -> None:
        if self.local is not None:
            self.local.note_started()
            return
        lk = self.link
        if lk is not None and self.alive:
            with contextlib.suppress(Exception):
                lk.request({"t": "note_started", "tool": self.tool_id}, timeout=15.0)

    # -- stop (BLOCKING, idempotent) -------------------------------------------------------
    def stop(self, *, graceful: bool = True) -> None:
        with self._stop_lock:
            if self.stopped:
                return
            self.stopped = True
        if self.local is not None:
            self.local.stop(graceful=graceful)
            return
        lk = self.link
        if lk is None:
            return
        if lk.alive and not self._ended:
            with contextlib.suppress(Exception):
                reply, _ = lk.request({"t": "stop", "tool": self.tool_id, "graceful": graceful},
                                      timeout=STOP_REPLY_S)
                code = reply.get("code")
                if isinstance(code, int) and self.exit_code is None:
                    self.exit_code = code
        lk.unregister(self.tool_id)
