"""Build › Code's coding tools in a process of their own (v1.422.0, the CODE HOST).

THE PROBLEM (audit gap B3, the owner's decision of 2026-11-01): every tool the
Code view drives (Claude Code, Codex, Pi, OpenCode, Grok) was a child of the
daemon in its own kill-on-close Job, so an Iron Jarvis restart — an update,
the desktop watchdog, the tray's Restart, a crash — killed every one of them:
a 40-minute reply was cut off, a dev server Claude Code started went with it,
and nothing offered to carry on. The owner chose "the option that would allow
long running tasks to be accomplished in the most efficient and effective
manner possible": the tools move into a SEPARATE PROCESS that survives a
restart, and the restarted daemon picks them up again. The CLI's own resume
stays the fallback for when this process itself is gone.

WHAT THIS PROCESS IS: a small relay. It starts each tool exactly as the daemon
did (``core.procs.spawn``: argv list, no shell, no console window, its own
kill-on-close Job with breakaway REFUSED — the v1.386.0 rule, or Git Bash's
dev server would escape it), reads its stdout as raw lines, numbers every
line (``seq``) and hands it to the daemon; the daemon's messages go to the
tool's stdin unchanged. It understands no tool protocol — the daemon's driver
does all of that — and imports nothing heavy (``core.procs`` and
``hosted.process``'s stdlib-only helpers; measured: ~0.2 s import, ~20 MB).

HOW IT SURVIVES THE DAEMON (each way the daemon's death reaches a child was
checked, v1.422.0):

* ``core.procs`` Jobs: the host is NOT started through ``core.procs`` — it
  owns the tools' Jobs itself, so when the daemon dies no Job of its closes
  over the host.
* The desktop's ``killChild`` is ``taskkill /pid <daemon> /T /F`` — a TREE
  kill by parent pid. The daemon therefore starts a short-lived LAUNCHER
  (``codehost --launch``) which starts the host and exits at once: the host's
  parent is the dead launcher, never the daemon (measured: ``taskkill /T`` on
  the daemon leaves it running). Windows hands a dead process's pid out again,
  and a new daemon child with that number would make ``taskkill /T`` walk into
  the host — so the launcher passes the host an INHERITED HANDLE TO ITSELF,
  which the host keeps open for its whole life: a pid is never reused while a
  handle to its process is open.
* Electron puts the daemon in libuv's Job (kill-on-close, silent breakaway):
  a process the daemon starts is not in it; the launcher and the host also ask
  ``CREATE_BREAKAWAY_FROM_JOB`` (retried without when an outer Job refuses —
  then the host simply stays in that Job, as under a test runner).
* Each of the launcher and the host has a HIDDEN console of its own
  (``CREATE_NO_WINDOW``, :data:`HOST_FLAGS`) — never none: a host with no
  console (``DETACHED_PROCESS``) gave a console program it started a NEW,
  VISIBLE one (measured: under ``uv``'s venv launcher, a Windows Terminal
  window opened for the host; closing it ended the host and every tool in it
  with 0xC000013A, mid-reply). A console of its own is still not the
  daemon's: closing the daemon's terminal or a Ctrl-C there never reaches it.

QUIT, UPDATE AND THE INSTALLER STILL END EVERYTHING: the daemon's tidy
shutdown (the desktop's Quit and its update stop, ``POST /shutdown``) sends
``quit``: every tool's stdin is closed first so it saves its conversation,
then it is ended, then the host exits. A daemon that only goes away (killed:
the watchdog, Restart, a crash) never sends it — that is what makes it a
restart. The frozen host is ``ironjarvis.exe`` like the daemon, so the
installer's own ``taskkill /F /T /IM ironjarvis.exe`` and the desktop's
update sweep end it too (it runs from the install folder).

NEVER FOREVER: with no daemon attached the host keeps each tool's output
(at most :data:`MAX_TOOL_BUFFER` bytes per tool — past it the oldest lines
go and the tool is marked ``overflowed``, so the daemon falls back to stop +
the CLI's own resume) and exits by itself: at once when it holds nothing,
after :data:`ORPHAN_GRACE_S` when nothing it holds is working (no output for
:data:`ORPHAN_QUIET_S`, or :data:`ORPHAN_BUSY_QUIET_S` while the daemon last
said the tool was busy), and in any case after :data:`ORPHAN_MAX_S`. Its tools
are stopped gracefully before it goes.

ONE PER STATE HOME: a named mutex (Windows) / a file lock (POSIX), and a small
record ``<home>/hosted/codehost.json`` (pid, start time, loopback port, a
random token, version, protocol number). Only a connection that sends the
token is served; a host of another version or protocol is only ever told to
``quit``. The record holds no tool's environment; the host never logs a
token, an argument or an environment value.

THE WIRE (loopback TCP): each message is one JSON header line, followed by
``n`` raw bytes when the header has ``n``. Daemon -> host: ``hello``,
``start``, ``attach``, ``write``, ``snapshot``, ``stop``, ``note_started``,
``forget``, ``quit``, ``detach``, ``ping``. Host -> daemon: ``welcome``, a
reply ``re`` to each request, and per tool ``line`` / ``exit`` /
``replayed``. ``snapshot`` is the daemon's opaque state of a tool's driver
(read back by ``attach`` after a restart, with the lines that came after it
— see ``hosted/adopt.py``); the host only stores it and drops the lines it
covers.

Run as ``ironjarvis codehost <home>`` (frozen: the entry's fast path; source:
``python -m iron_jarvis.codehost <home>``); ``--launch`` is the launcher.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

#: The wire's version: a host with another one is only ever told to quit.
PROTOCOL = 1
#: ``<home>/hosted/<RECORD>``: how the daemon (and the desktop) find the host.
RECORD = "codehost.json"
LOG_NAME = "codehost.log"
LOG_MAX_BYTES = 1024 * 1024
SUBCOMMAND = "codehost"
LAUNCH_FLAG = "--launch"
#: The inherited handle to the launcher (see the module docstring).
PARENT_HANDLE_ENV = "IJ_CODEHOST_PARENT_HANDLE"


def _knob(name: str, default: float) -> float:
    """A bound, read once (``IRONJARVIS_CODEHOST_<NAME>`` — the tests' way to
    make the orphan rules quick). Never below zero."""
    raw = os.environ.get(f"IRONJARVIS_CODEHOST_{name}", "")
    try:
        return max(0.0, float(raw)) if raw.strip() else default
    except ValueError:
        return default


#: A tool's kept output while nothing has taken it (bytes).
MAX_TOOL_BUFFER = int(_knob("MAX_TOOL_BUFFER", 64 * 1024 * 1024))
#: What may wait to be sent to the daemon before the connection is dropped (a
#: daemon that stopped reading is a daemon that went away).
MAX_QUEUE_BYTES = int(_knob("MAX_QUEUE_BYTES", 256 * 1024 * 1024))
#: One header line, at most.
MAX_HEADER = 4 * 1024 * 1024
#: With no daemon: how long before the host may go when nothing works.
ORPHAN_GRACE_S = _knob("ORPHAN_GRACE_S", 10 * 60.0)
#: A tool that said nothing for this long is not working.
ORPHAN_QUIET_S = _knob("ORPHAN_QUIET_S", 10 * 60.0)
#: ... unless the daemon last said it was busy (a reply, helper agents).
ORPHAN_BUSY_QUIET_S = _knob("ORPHAN_BUSY_QUIET_S", 2 * 60 * 60.0)
#: With no daemon, the host goes after this whatever its tools do.
ORPHAN_MAX_S = _knob("ORPHAN_MAX_S", 3 * 60 * 60.0)
#: Holding nothing and no daemon: gone after this.
EMPTY_EXIT_S = _knob("EMPTY_EXIT_S", 60.0)
#: How often the host looks.
TICK_S = _knob("TICK_S", 1.0)
#: A connection must say hello within this.
HELLO_WAIT_S = 10.0


# --------------------------------------------------------------------------- #
# Small helpers (no app import)
# --------------------------------------------------------------------------- #
def record_path(home: Any) -> Path:
    return Path(home) / "hosted" / RECORD


def log_path(home: Any) -> Path:
    return Path(home) / "hosted" / LOG_NAME


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def host_argv(home: Any, *, launch: bool = False) -> list[str]:
    """How the host (or its launcher) is started: the same program as the
    daemon — ``ironjarvis.exe codehost`` frozen (the entry's fast path),
    ``python -m iron_jarvis.codehost`` from source."""
    tail = ([LAUNCH_FLAG] if launch else []) + [str(home)]
    if frozen():
        return [sys.executable, SUBCOMMAND, *tail]
    return [sys.executable, "-m", "iron_jarvis.codehost", *tail]


def _now() -> float:
    return time.monotonic()


class _Log:
    """The host's own log (pids, tool ids, what happened — never a token, an
    argument or an environment value). Opened per line: never held."""

    def __init__(self, home: Any) -> None:
        self.path = log_path(home)
        self._lock = threading.Lock()

    def __call__(self, msg: str) -> None:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{os.getpid()}] {msg}\n"
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    if self.path.stat().st_size > LOG_MAX_BYTES:
                        os.replace(self.path, self.path.with_suffix(".log.1"))
                except OSError:
                    pass
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(line)
            except OSError:
                pass


def send_frame(sock: socket.socket, header: dict[str, Any], payload: bytes = b"") -> None:
    """BLOCKING: one message (the caller serializes senders)."""
    if payload:
        header = {**header, "n": len(payload)}
    data = json.dumps(header, ensure_ascii=False).encode("utf-8") + b"\n"
    sock.sendall(data + payload if payload else data)


def read_frame(rfile: Any) -> tuple[dict[str, Any], bytes] | None:
    """BLOCKING: one message, or None at the end of the stream / a bad one."""
    line = rfile.readline(MAX_HEADER + 1)
    if not line or not line.endswith(b"\n"):
        return None
    try:
        header = json.loads(line)
    except ValueError:
        return None
    if not isinstance(header, dict):
        return None
    n = header.get("n")
    payload = b""
    if isinstance(n, int) and not isinstance(n, bool) and n > 0:
        payload = rfile.read(n)
        if len(payload) != n:
            return None
    return header, payload


# --------------------------------------------------------------------------- #
# One connection to the daemon
# --------------------------------------------------------------------------- #
class Conn:
    """The daemon's connection. Everything sent goes through ONE sender thread
    (a tool's reader never waits on the socket); past :data:`MAX_QUEUE_BYTES`
    queued the daemon is taken for gone and the connection is dropped."""

    def __init__(self, sock: socket.socket, host: "Host") -> None:
        self.sock = sock
        self.host = host
        self.alive = True
        self.authed = False
        self._q: deque[tuple[dict[str, Any], bytes]] = deque()
        self._qbytes = 0
        self._cv = threading.Condition()
        self._sender = threading.Thread(target=self._send_loop, name="codehost-send", daemon=True)
        self._sender.start()

    def send(self, header: dict[str, Any], payload: bytes = b"") -> None:
        with self._cv:
            if not self.alive:
                return
            self._qbytes += len(payload) + 64
            if self._qbytes > MAX_QUEUE_BYTES:
                self.host.log("the daemon stopped reading (send queue full): dropping its connection")
                self._close_locked()
                return
            self._q.append((header, payload))
            self._cv.notify()

    def _send_loop(self) -> None:
        while True:
            with self._cv:
                while self.alive and not self._q:
                    self._cv.wait(timeout=5.0)
                if not self._q and not self.alive:
                    return
                header, payload = self._q.popleft()
                self._qbytes -= len(payload) + 64
            try:
                send_frame(self.sock, header, payload)
            except OSError:
                self.close()
                return

    def flush(self, timeout: float = 2.0) -> None:
        """Wait (bounded) until what is queued has gone."""
        end = _now() + timeout
        while _now() < end:
            with self._cv:
                if not self._q or not self.alive:
                    return
            time.sleep(0.02)

    def _close_locked(self) -> None:
        if not self.alive:
            return
        self.alive = False
        self._q.clear()
        self._qbytes = 0
        self._cv.notify_all()
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    def close(self) -> None:
        with self._cv:
            self._close_locked()
        self.host.conn_closed(self)


# --------------------------------------------------------------------------- #
# One tool
# --------------------------------------------------------------------------- #
class Tool:
    """One coding tool the host runs, with the lines no daemon has taken."""

    def __init__(self, host: "Host", tid: str, sid: str, argv: list[str], env: dict[str, str], cwd: str,
                 ends_left_behind: bool) -> None:
        self.host = host
        self.id = tid
        self.sid = sid
        self.argv = argv
        self.env = env
        self.cwd = cwd
        self.ends_left_behind = ends_left_behind
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()
        self.seq = 0
        self.lines: deque[tuple[int, bytes]] = deque()
        self.buffered = 0
        self.overflowed = False
        self.snapshot: bytes = b""
        self.snap_seq = 0
        self.busy = False
        self.writes_upto = 0
        self.exit_code: int | None = None
        self.exited = False
        self.last_out = _now()
        self.conn: Conn | None = None
        self.stopping = False
        self._stderr: deque[bytes] = deque(maxlen=64)
        self._writes: "deque[tuple[Any, bytes, Conn | None] | None]" = deque()
        self._wcv = threading.Condition()
        self._threads: list[threading.Thread] = []
        self._started: list[tuple[int, float]] = []

    # -- start (BLOCKING) --------------------------------------------------------
    def start(self) -> int:
        from iron_jarvis.core import procs

        proc = procs.spawn(
            self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=self.cwd or None, env=self.env,
            # NO member may leave the Job (v1.386.0): Git Bash would otherwise
            # start every program it runs outside it.
            breakaway_ok=False,
        )
        self.proc = proc
        for target, suffix in ((self._read_out, "out"), (self._read_err, "err"), (self._write_loop, "in")):
            t = threading.Thread(target=target, name=f"codehost-{self.id[:8]}-{suffix}", daemon=True)
            t.start()
            self._threads.append(t)
        return int(proc.pid)

    def _read_out(self) -> None:
        proc = self.proc
        assert proc is not None
        try:
            for raw in proc.stdout:  # type: ignore[union-attr]
                line = raw.rstrip(b"\r\n")
                if not line.strip():
                    continue
                with self.lock:
                    self.seq += 1
                    seq = self.seq
                    self.lines.append((seq, line))
                    self.buffered += len(line) + 16
                    self.last_out = _now()
                    while self.buffered > MAX_TOOL_BUFFER and self.lines:
                        _, old = self.lines.popleft()
                        self.buffered -= len(old) + 16
                        self.overflowed = True
                    conn = self.conn
                    if conn is not None:
                        conn.send({"t": "line", "tool": self.id, "seq": seq}, line)
        except Exception:  # noqa: BLE001 — a dead pipe ends the stream
            pass
        try:
            code = proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            code = proc.poll()
        with self.lock:
            self.exit_code = code
            self.exited = True
            conn = self.conn
            if conn is not None:
                conn.send(self._exit_frame())
        self.host.log(f"tool {self.id[:8]} exited (code {code})")
        self.host.wake()

    def _exit_frame(self) -> dict[str, Any]:
        return {"t": "exit", "tool": self.id, "code": self.exit_code, "stderr": self.stderr_text()}

    def _read_err(self) -> None:
        proc = self.proc
        try:
            for chunk in iter(lambda: proc.stderr.read1(4096), b""):  # type: ignore[union-attr]
                self._stderr.append(chunk)
        except Exception:  # noqa: BLE001
            pass

    def stderr_text(self) -> str:
        return b"".join(self._stderr).decode("utf-8", errors="replace")[-2000:]

    # -- stdin -------------------------------------------------------------------
    def queue_write(self, re: Any, data: bytes, conn: Conn | None) -> None:
        with self._wcv:
            self._writes.append((re, data, conn))
            self._wcv.notify()

    def _close_stdin_after_writes(self) -> None:
        with self._wcv:
            self._writes.append(None)
            self._wcv.notify()

    def _write_loop(self) -> None:
        proc = self.proc
        assert proc is not None
        while True:
            with self._wcv:
                while not self._writes:
                    self._wcv.wait(timeout=5.0)
                    if self.exited and not self._writes:
                        return
                item = self._writes.popleft()
            if item is None:
                try:
                    if proc.stdin and not proc.stdin.closed:
                        proc.stdin.close()
                except Exception:  # noqa: BLE001
                    pass
                return
            re, data, conn = item
            ok, err = True, ""
            try:
                if proc.poll() is not None or proc.stdin is None or proc.stdin.closed:
                    raise BrokenPipeError("the coding tool is not running")
                proc.stdin.write(data)
                proc.stdin.flush()
            except (BrokenPipeError, ConnectionResetError, OSError, ValueError) as exc:
                ok, err = False, type(exc).__name__
            if conn is not None and re is not None:
                conn.send({"t": "re", "re": re, "ok": ok, "error": err})

    # -- the daemon's state ------------------------------------------------------
    def take_snapshot(self, seq: int, blob: bytes, busy: bool) -> None:
        with self.lock:
            if seq < self.snap_seq:
                return
            self.snapshot = blob
            self.snap_seq = seq
            self.busy = bool(busy)
            while self.lines and self.lines[0][0] <= seq:
                _, old = self.lines.popleft()
                self.buffered -= len(old) + 16

    def summary(self) -> dict[str, Any]:
        with self.lock:
            return {
                "tool": self.id, "sid": self.sid, "pid": self.proc.pid if self.proc else None,
                "alive": not self.exited, "exit_code": self.exit_code, "overflowed": self.overflowed,
                "busy": self.busy, "quiet_s": round(_now() - self.last_out, 1), "seq": self.seq,
                "has_snapshot": bool(self.snapshot), "stopping": self.stopping,
            }

    def attach(self, conn: Conn, re: Any) -> None:
        """Hand the daemon the kept state and every line after it, in order,
        then stream live (one lock hold: no line is lost or sent twice)."""
        with self.lock:
            conn.send({
                "t": "re", "re": re, "ok": True, "snap_seq": self.snap_seq, "writes_upto": self.writes_upto,
                "overflowed": self.overflowed, "alive": not self.exited, "exit_code": self.exit_code,
                "pid": self.proc.pid if self.proc else None, "busy": self.busy, "seq": self.seq,
            }, self.snapshot)
            last = self.snap_seq
            for seq, line in self.lines:
                conn.send({"t": "line", "tool": self.id, "seq": seq}, line)
                last = seq
            conn.send({"t": "replayed", "tool": self.id, "seq": last})
            if self.exited:
                conn.send(self._exit_frame())
            self.conn = conn

    def working(self, now: float) -> bool:
        if self.exited:
            return False
        quiet = now - self.last_out
        return quiet < ORPHAN_QUIET_S or (self.busy and quiet < ORPHAN_BUSY_QUIET_S)

    # -- stop (BLOCKING, idempotent) --------------------------------------------
    def note_started(self) -> None:
        from iron_jarvis.hosted.process import descendants

        proc = self.proc
        if proc is None or proc.poll() is not None:
            return
        seen = {pid for pid, _ in self._started}
        self._started.extend(p for p in descendants(proc.pid) if p[0] not in seen)

    def stop(self, graceful: bool = True) -> int | None:
        """The same stop as ``hosted.process.HarnessProcess.stop``: stdin
        closed first (after what was already sent) and a grace to save and
        exit, then the whole Job; what Grok left outside it ended by pid."""
        from iron_jarvis.core import procs
        from iron_jarvis.hosted.process import EXIT_GRACE_S, STOP_JOIN_S, descendants, kill_left

        with self.lock:
            if self.stopping:
                return self.exit_code
            self.stopping = True
        proc = self.proc
        if proc is None:
            return None
        started = list(self._started) if self.ends_left_behind else []
        if self.ends_left_behind and proc.poll() is None:
            known = {pid for pid, _ in started}
            started += [p for p in descendants(proc.pid) if p[0] not in known]
        if graceful:
            self._close_stdin_after_writes()
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
        end = _now() + STOP_JOIN_S
        for t in self._threads:
            if t.name.endswith("-in"):
                continue
            t.join(timeout=max(0.0, end - _now()))
        with self._wcv:
            self._wcv.notify_all()
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if pipe and not pipe.closed:
                    pipe.close()
            except Exception:  # noqa: BLE001
                pass
        self.host.log(f"tool {self.id[:8]} stopped ({'graceful' if graceful else 'at once'})")
        return proc.poll()


# --------------------------------------------------------------------------- #
# The host
# --------------------------------------------------------------------------- #
class Host:
    def __init__(self, home: Any, *, token: str = "") -> None:
        self.home = Path(home)
        self.token = token or secrets.token_hex(32)
        self.log = _Log(home)
        self.tools: dict[str, Tool] = {}
        self.lock = threading.Lock()
        self.conn: Conn | None = None
        self.started = _now()
        self.detached_since: float | None = self.started
        self.ever_attached = False
        self.exiting = threading.Event()
        self._wake = threading.Event()
        self.port = 0
        #: The listening socket (closed to end the accept loop at once).
        self.srv: socket.socket | None = None

    def wake(self) -> None:
        self._wake.set()

    def finish(self) -> None:
        """Go now: the accept loop ends at once (its socket is closed)."""
        self.exiting.set()
        srv = self.srv
        if srv is not None:
            try:
                srv.close()
            except OSError:
                pass

    # -- connections -------------------------------------------------------------
    def conn_closed(self, conn: Conn) -> None:
        with self.lock:
            # A tool streams to ONE connection; a replaced or dropped one
            # streams to nobody until the next daemon attaches it.
            for tool in self.tools.values():
                with tool.lock:
                    if tool.conn is conn:
                        tool.conn = None
            if self.conn is conn:
                self.conn = None
                self.detached_since = _now()
                if not self.exiting.is_set():
                    self.log("the daemon went away: keeping the tools and waiting for it")
        self.wake()

    def serve_conn(self, sock: socket.socket) -> None:
        sock.settimeout(HELLO_WAIT_S)
        conn = Conn(sock, self)
        rfile = sock.makefile("rb")
        try:
            first = read_frame(rfile)
            if first is None or first[0].get("t") != "hello":
                conn.close()
                return
            hello = first[0]
            if not hmac.compare_digest(str(hello.get("token") or ""), self.token):
                self.log("a connection without the token was refused")
                conn.send({"t": "refused", "why": "token"})
                conn.flush(1.0)
                conn.close()
                return
            sock.settimeout(None)
            same = hello.get("protocol") == PROTOCOL
            with self.lock:
                old = self.conn
                if same:
                    conn.authed = True
                    self.conn = conn
                    self.detached_since = None
                    self.ever_attached = True
                tools = [t.summary() for t in self.tools.values()]
            if old is not None and same:
                old.close()
            from iron_jarvis import __version__

            conn.send({"t": "welcome", "protocol": PROTOCOL, "version": __version__, "pid": os.getpid(),
                       "tools": tools})
            self.log(f"a daemon attached (its protocol {hello.get('protocol')}, version {hello.get('version')})")
            while not self.exiting.is_set():
                got = read_frame(rfile)
                if got is None:
                    break
                header, payload = got
                if not same and header.get("t") not in ("quit", "ping"):
                    conn.send({"t": "re", "re": header.get("re"), "ok": False, "error": "protocol"})
                    continue
                try:
                    self.handle(conn, header, payload)
                except Exception as exc:  # noqa: BLE001 — one bad message never ends the host
                    self.log(f"a message could not be handled ({type(exc).__name__})")
                    if header.get("re") is not None:
                        conn.send({"t": "re", "re": header.get("re"), "ok": False, "error": type(exc).__name__})
        except OSError:
            pass
        finally:
            try:
                rfile.close()
            except OSError:
                pass
            conn.close()

    # -- commands ----------------------------------------------------------------
    def handle(self, conn: Conn, h: dict[str, Any], payload: bytes) -> None:
        kind = h.get("t")
        re = h.get("re")
        if kind == "ping":
            conn.send({"t": "re", "re": re, "ok": True})
        elif kind == "start":
            self._start(conn, h)
        elif kind == "attach":
            tool = self.tools.get(str(h.get("tool") or ""))
            if tool is None:
                conn.send({"t": "re", "re": re, "ok": False, "error": "unknown tool"})
            else:
                tool.attach(conn, re)
        elif kind == "write":
            tool = self.tools.get(str(h.get("tool") or ""))
            if tool is None or tool.exited:
                conn.send({"t": "re", "re": re, "ok": False, "error": "gone"})
                return
            after = h.get("after")
            if isinstance(after, int) and not isinstance(after, bool):
                with tool.lock:
                    tool.writes_upto = max(tool.writes_upto, after)
            snap = h.get("snap")
            if isinstance(snap, int) and not isinstance(snap, bool) and 0 < snap <= len(payload) \
                    and isinstance(h.get("snap_seq"), int):
                # The daemon's state AS IT SENT THIS: kept with the write, in
                # one step (a frame is handled whole or not at all).
                tool.take_snapshot(int(h["snap_seq"]), payload[:snap], bool(h.get("busy")))
                payload = payload[snap:]
            tool.queue_write(re, payload, conn)
        elif kind == "snapshot":
            tool = self.tools.get(str(h.get("tool") or ""))
            seq = h.get("seq")
            if tool is not None and isinstance(seq, int):
                tool.take_snapshot(seq, payload, bool(h.get("busy")))
        elif kind == "stop":
            tool = self.tools.get(str(h.get("tool") or ""))
            graceful = h.get("graceful") is not False

            def run() -> None:
                code = tool.stop(graceful) if tool is not None else None
                with self.lock:
                    if tool is not None:
                        self.tools.pop(tool.id, None)
                conn.send({"t": "re", "re": re, "ok": True, "code": code})
                self.wake()

            threading.Thread(target=run, name="codehost-stop", daemon=True).start()
        elif kind == "note_started":
            tool = self.tools.get(str(h.get("tool") or ""))
            if tool is not None:
                tool.note_started()
            conn.send({"t": "re", "re": re, "ok": True})
        elif kind == "forget":
            with self.lock:
                tool = self.tools.get(str(h.get("tool") or ""))
                if tool is not None and tool.exited:
                    self.tools.pop(tool.id, None)
            self.wake()
        elif kind == "quit":
            self.log("told to quit: stopping every tool gracefully")
            self.stop_all(graceful=h.get("graceful") is not False)
            conn.send({"t": "re", "re": re, "ok": True})
            conn.flush(2.0)
            self.finish()
        elif kind == "detach":
            self.log("the daemon detached (a restart): keeping the tools")
            conn.close()
        elif re is not None:
            conn.send({"t": "re", "re": re, "ok": False, "error": "unknown message"})

    def _start(self, conn: Conn, h: dict[str, Any]) -> None:
        re = h.get("re")
        tid = str(h.get("tool") or "")
        argv = h.get("argv")
        env = h.get("env")
        if not tid or tid in self.tools or not isinstance(argv, list) or not argv \
                or not all(isinstance(a, str) for a in argv) or not isinstance(env, dict):
            conn.send({"t": "re", "re": re, "ok": False, "error": "a bad start"})
            return
        tool = Tool(self, tid, str(h.get("sid") or ""), argv, {str(k): str(v) for k, v in env.items()},
                    str(h.get("cwd") or ""), bool(h.get("ends_left_behind")))
        tool.conn = conn
        try:
            pid = tool.start()
        except OSError as exc:
            conn.send({"t": "re", "re": re, "ok": False, "error": str(exc)[:500],
                       "winerror": getattr(exc, "winerror", None), "errno": exc.errno})
            return
        with self.lock:
            self.tools[tid] = tool
        self.log(f"tool {tid[:8]} started (pid {pid})")
        conn.send({"t": "re", "re": re, "ok": True, "pid": pid})

    def stop_all(self, graceful: bool = True) -> None:
        tools = list(self.tools.values())
        threads = [threading.Thread(target=t.stop, args=(graceful,), daemon=True) for t in tools]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        with self.lock:
            for t in tools:
                self.tools.pop(t.id, None)

    # -- the orphan rules ----------------------------------------------------------
    def why_exit(self, now: float) -> str:
        """Why the host should go now ("" = it stays)."""
        with self.lock:
            if self.conn is not None:
                return ""
            since = self.detached_since if self.detached_since is not None else now
            alive = [t for t in self.tools.values() if not t.exited]
        away = now - since
        if not alive:
            return "it holds no running tool and no Iron Jarvis is attached" if away >= EMPTY_EXIT_S else ""
        if away >= ORPHAN_MAX_S:
            return "no Iron Jarvis came back within the hard bound"
        if away >= ORPHAN_GRACE_S and not any(t.working(now) for t in alive):
            return "no Iron Jarvis came back and nothing it holds is working"
        return ""

    def tick(self) -> None:
        why = self.why_exit(_now())
        if why:
            self.log(f"exiting: {why}")
            self.stop_all(graceful=True)
            self.finish()


# --------------------------------------------------------------------------- #
# Single instance and the record
# --------------------------------------------------------------------------- #
class _Single:
    """One host per state home: a named mutex on Windows (no file — a byte
    lock in the home broke every backup once, v1.337.1), a file lock
    elsewhere."""

    def __init__(self, home: Any) -> None:
        self.home = Path(home)
        self._handle: Any = None
        self._fh: Any = None

    def name(self) -> str:
        key = os.path.normcase(os.path.abspath(str(self.home)))
        return "Local\\IronJarvisCodeHost-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]

    def acquire(self) -> bool:
        if sys.platform == "win32":
            import ctypes

            k = ctypes.WinDLL("kernel32", use_last_error=True)
            k.CreateMutexW.restype = ctypes.c_void_p
            k.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
            h = k.CreateMutexW(None, True, self.name())
            if not h:
                return False
            if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS: another host
                k.CloseHandle.argtypes = [ctypes.c_void_p]
                k.CloseHandle(h)
                return False
            self._handle = h
            return True
        import fcntl

        path = self.home / "hosted" / "codehost.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(path, "a+b")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        return True


def write_record(home: Any, rec: dict[str, Any]) -> None:
    path = record_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(rec), encoding="utf-8")
    os.replace(tmp, path)


def read_record(home: Any) -> dict[str, Any] | None:
    try:
        rec = json.loads(record_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) else None


def remove_record(home: Any, pid: int) -> None:
    rec = read_record(home)
    if rec is not None and rec.get("pid") == pid:
        try:
            record_path(home).unlink()
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# Entry points
# --------------------------------------------------------------------------- #
#: Windows creation flags.
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000
#: How the launcher starts the host: a hidden console of its own (see the
#: module docstring — DETACHED_PROCESS let a window open), its own process
#: group.
HOST_FLAGS = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP


def popen_detached(argv: list[str], *, flags: int, env: dict[str, str] | None = None,
                   handles: list[int] | None = None) -> subprocess.Popen:
    """Start ``argv`` with nothing of ours (DEVNULL streams, no inherited
    handle but ``handles``), leaving any Job that lets it (retried without
    ``CREATE_BREAKAWAY_FROM_JOB`` when an outer Job refuses — measured under
    a test runner: "Access is denied")."""
    kw: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                          "stderr": subprocess.DEVNULL, "close_fds": True}
    if env is not None:
        kw["env"] = env
    if sys.platform != "win32":
        return subprocess.Popen(argv, start_new_session=True, **kw)  # noqa: S603 — our own argv
    if handles:
        si = subprocess.STARTUPINFO()
        si.lpAttributeList = {"handle_list": list(handles)}
        kw["startupinfo"] = si
    try:
        return subprocess.Popen(argv, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB, **kw)  # noqa: S603
    except PermissionError:
        return subprocess.Popen(argv, creationflags=flags, **kw)  # noqa: S603


def _inheritable_self_handle() -> int | None:
    """A handle to THIS process that a child inherits (Windows)."""
    if sys.platform != "win32":
        return None
    import ctypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.GetCurrentProcess.restype = ctypes.c_void_p
    k.DuplicateHandle.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                  ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    me = k.GetCurrentProcess()
    out = ctypes.c_void_p()
    SYNCHRONIZE, QUERY_LIMITED = 0x00100000, 0x1000
    if not k.DuplicateHandle(me, me, me, ctypes.byref(out), SYNCHRONIZE | QUERY_LIMITED, True, 0):
        return None
    return int(out.value or 0) or None


def launch(home: Any) -> int:
    """The LAUNCHER: start the host and exit at once (see the module
    docstring). Exit code 0 when the host was started."""
    env = dict(os.environ)
    handle = _inheritable_self_handle()
    if handle:
        env[PARENT_HANDLE_ENV] = str(handle)
    try:
        popen_detached(host_argv(home), flags=HOST_FLAGS, env=env,
                       handles=[handle] if handle else None)
    except OSError as exc:
        _Log(home)(f"the launcher could not start the host ({type(exc).__name__})")
        return 1
    return 0


def serve(home: Any) -> int:
    """The HOST: run until told to quit or the orphan rules say go."""
    home = Path(home)
    log = _Log(home)
    held = os.environ.pop(PARENT_HANDLE_ENV, "")  # kept OPEN for life: see the module docstring
    single = _Single(home)
    if not single.acquire():
        log("another code host already runs for this home: exiting")
        return 0
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    srv.settimeout(TICK_S if TICK_S > 0 else 1.0)
    host = Host(home)
    host.port = srv.getsockname()[1]
    host.srv = srv
    from iron_jarvis import __version__

    write_record(home, {
        "pid": os.getpid(), "port": host.port, "token": host.token, "version": __version__,
        "protocol": PROTOCOL, "started": time.time(), "exe": sys.executable,
    })
    log(f"started (version {__version__}, protocol {PROTOCOL}, port {host.port}, "
        f"{'holding its launcher' if held else 'no launcher handle'})")
    try:
        while not host.exiting.is_set():
            try:
                sock, _ = srv.accept()
            except (socket.timeout, TimeoutError):
                sock = None
            except OSError:
                break
            if sock is not None:
                threading.Thread(target=host.serve_conn, args=(sock,), name="codehost-conn", daemon=True).start()
            host.tick()
    finally:
        try:
            srv.close()
        except OSError:
            pass
        if not host.exiting.is_set():
            host.stop_all(graceful=True)
        remove_record(home, os.getpid())
        log("exited")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == LAUNCH_FLAG and len(args) == 2:
        return launch(args[1])
    if len(args) == 1 and not args[0].startswith("-"):
        return serve(args[0])
    sys.stderr.write("usage: codehost [--launch] <home>\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
