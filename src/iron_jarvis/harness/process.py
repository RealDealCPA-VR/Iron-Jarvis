"""Long-running programs a session starts and talks to (v1.364.0, IJ harness).

Claude Code runs ``Bash`` in the background and reads the output back; Codex's
``exec_command`` returns a session id that ``write_stdin`` polls or types
into. Jarvis had neither: ``shell`` waits for its command to END (a dev
server never does), and the ``pane_*`` tools drive the user's own Build
terminals. These four tools are the missing piece:

* ``process_start`` — start a command in the workspace (or a folder inside
  it) and return what it printed in its first moments;
* ``process_read`` — what it printed since the last read (optionally waiting
  a little for more), and whether it is still running;
* ``process_write`` — type into its input;
* ``process_stop`` — stop it and everything it started.

THE RULES, each from an earlier lesson:

* Every child goes through ``core.procs.spawn`` (its own kill-on-close Job:
  a daemon that dies, for any reason, takes these programs with it), in the
  background lane when the run is unattended.
* Each program belongs to the SESSION that started it: another session can
  neither read nor stop it, and the run's end stops it
  (``procs.end_session`` → :func:`end_session`, registered with
  ``procs.add_end_hook``); daemon shutdown stops them all (``procs.end_all``).
* At most :data:`MAX_RUNNING_PER_SESSION` run at once per session.
* Output is BOUNDED: a reader thread keeps the newest :data:`RING_BYTES`
  bytes, a read returns at most :data:`MAX_READ_CHARS` characters, and a
  read that skipped dropped output says how much.
* Nothing blocks the event loop: the spawn, the stop and a write run in
  worker threads, a wait polls with ``asyncio.sleep``.
"""

from __future__ import annotations

import asyncio
import itertools
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core import procs
from ..tools.base import Reversibility, RiskClass, Tool, ToolContext, ToolResult, safe_path

#: Programs one session may have RUNNING at once.
MAX_RUNNING_PER_SESSION = 4
#: Finished programs kept (readable) per session; the oldest go first.
MAX_KEPT_PER_SESSION = 8
#: Output kept per program (the newest bytes).
RING_BYTES = 1_000_000
#: A read returns at most this many characters (the rest stays unread).
MAX_READ_CHARS = 30_000
#: The longest a call may wait for output.
MAX_WAIT_MS = 30_000
#: ``process_start`` waits this long by default for the first output.
DEFAULT_START_WAIT_MS = 1_000
#: Output that has been quiet this long ends a wait early.
QUIET_S = 0.25
#: How often a wait looks again.
POLL_S = 0.05
#: Input written in one call, at most.
MAX_INPUT_CHARS = 64_000
#: How long a write may take before the call returns (the program may not be
#: reading its input yet; the write finishes in the background).
WRITE_WAIT_S = 5.0
#: How long a stop waits for the reader thread to drain.
STOP_DRAIN_S = 2.0


@dataclass
class ManagedProcess:
    """One program a session started."""

    handle: str
    session_id: str
    command: str
    cwd: str
    proc: Any
    started_at: float = field(default_factory=time.monotonic)
    lock: threading.Lock = field(default_factory=threading.Lock)
    buf: bytearray = field(default_factory=bytearray)
    #: Absolute offset of ``buf[0]`` in everything the program printed.
    base: int = 0
    #: Absolute offset the session has read up to.
    cursor: int = 0
    #: When the newest byte arrived (for the quiet-ends-a-wait rule).
    last_output_at: float = 0.0
    eof: bool = False
    stopped: bool = False
    reader: threading.Thread | None = None
    #: When the program was first SEEN ended (exited or stopped), so "after
    #: N s" is how long it ran, not how long ago it started.
    ended_at: float | None = None

    @property
    def total(self) -> int:
        return self.base + len(self.buf)

    def append(self, chunk: bytes) -> None:
        with self.lock:
            self.buf += chunk
            over = len(self.buf) - RING_BYTES
            if over > 0:
                del self.buf[:over]
                self.base += over
            self.last_output_at = time.monotonic()

    def returncode(self) -> int | None:
        try:
            code = self.proc.poll()
        except Exception:  # noqa: BLE001
            return None
        if code is not None and self.ended_at is None:
            self.ended_at = time.monotonic()
        return code

    def running(self) -> bool:
        return not self.stopped and self.returncode() is None

    def take(self, limit_chars: int = MAX_READ_CHARS) -> tuple[str, int, bool]:
        """``(text, bytes dropped before it, more waiting)`` — the unread
        output, decoded, advancing the cursor. An incomplete UTF-8 character
        at the end waits for its last bytes unless the program has ended."""
        from ..sandbox.native import _as_text

        with self.lock:
            dropped = 0
            if self.cursor < self.base:
                dropped = self.base - self.cursor
                self.cursor = self.base
            start = self.cursor - self.base
            # Bytes are a generous stand-in for characters here; the decode
            # below never returns more characters than bytes.
            chunk = bytes(self.buf[start : start + limit_chars])
            finished = self.eof and not self.running()
            if not finished:
                chunk = chunk[: len(chunk) - _incomplete_tail(chunk)]
            self.cursor += len(chunk)
            more = self.cursor < self.total
        text = _as_text(chunk).replace("\r\n", "\n")
        return text, dropped, more


def _incomplete_tail(data: bytes) -> int:
    """How many bytes at the end of ``data`` start a UTF-8 character whose
    remaining bytes have not arrived (0-3)."""
    for back in range(1, min(4, len(data)) + 1):
        b = data[-back]
        if b & 0b1100_0000 == 0b1000_0000:
            continue  # a continuation byte: look further back
        if b & 0b1000_0000 == 0:
            return 0  # ASCII: complete
        need = 2 if b & 0b1110_0000 == 0b1100_0000 else 3 if b & 0b1111_0000 == 0b1110_0000 else 4
        return back if back < need else 0
    return 0


class ProcessTable:
    """Every managed program, by session. Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_session: dict[str, dict[str, ManagedProcess]] = {}
        self._ids = itertools.count(1)

    def running(self, session_id: str) -> list[ManagedProcess]:
        with self._lock:
            rows = list(self._by_session.get(session_id, {}).values())
        return [r for r in rows if r.running()]

    def get(self, session_id: str, handle: str) -> ManagedProcess | None:
        with self._lock:
            return self._by_session.get(session_id, {}).get(handle)

    def add(self, mp: ManagedProcess) -> None:
        with self._lock:
            rows = self._by_session.setdefault(mp.session_id, {})
            rows[mp.handle] = mp
            finished = [r for r in rows.values() if not r.running()]
            pruned: list[ManagedProcess] = []
            while len(rows) > MAX_RUNNING_PER_SESSION + MAX_KEPT_PER_SESSION and finished:
                oldest = min(finished, key=lambda r: r.started_at)
                finished.remove(oldest)
                rows.pop(oldest.handle, None)
                pruned.append(oldest)
        for old in pruned:
            # A finished program's job is released when it is forgotten.
            try:
                procs.release(old.proc)
            except Exception:  # noqa: BLE001
                pass

    def remove(self, mp: ManagedProcess) -> None:
        with self._lock:
            rows = self._by_session.get(mp.session_id, {})
            rows.pop(mp.handle, None)
            if not rows:
                self._by_session.pop(mp.session_id, None)

    def new_handle(self) -> str:
        return f"p{next(self._ids)}"

    def sessions(self, session_id: str | None) -> list[ManagedProcess]:
        with self._lock:
            if session_id is None:
                return [mp for rows in self._by_session.values() for mp in rows.values()]
            return list(self._by_session.get(session_id, {}).values())


#: The one table (a module-level singleton, like ``procs``' keeper: the
#: run's end and daemon shutdown reach it through ``procs``' end hooks).
TABLE = ProcessTable()


def _stop(mp: ManagedProcess) -> list[int]:
    """Stop ``mp`` and its whole tree; release its job. BLOCKING; never
    raises. Returns the pid it stopped (empty when already gone)."""
    pid = getattr(mp.proc, "pid", None)
    was_running = mp.returncode() is None
    if was_running:
        mp.stopped = True
        mp.ended_at = time.monotonic()
    try:
        procs.kill_tree(mp.proc)
    except Exception:  # noqa: BLE001
        pass
    try:
        mp.proc.wait(timeout=STOP_DRAIN_S)
    except Exception:  # noqa: BLE001
        pass
    for pipe in (getattr(mp.proc, "stdin", None),):
        try:
            if pipe is not None:
                pipe.close()
        except Exception:  # noqa: BLE001
            pass
    if mp.reader is not None:
        mp.reader.join(timeout=STOP_DRAIN_S)
    try:
        procs.release(mp.proc)
    except Exception:  # noqa: BLE001
        pass
    return [int(pid)] if (was_running and pid) else []


def end_session(session_id: str | None) -> list[int]:
    """``procs`` end hook: stop every program of ``session_id`` (``None`` =
    every session, at daemon shutdown) and forget them."""
    stopped: list[int] = []
    for mp in TABLE.sessions(session_id):
        stopped += _stop(mp)
        TABLE.remove(mp)
    return stopped


procs.add_end_hook(end_session)


def _reader(mp: ManagedProcess) -> None:
    stream = getattr(mp.proc, "stdout", None)
    try:
        while stream is not None:
            read1 = getattr(stream, "read1", None)
            chunk = read1(65536) if read1 is not None else stream.read(4096)
            if not chunk:
                break
            mp.append(chunk)
    except Exception:  # noqa: BLE001 — a closed pipe ends the reader
        pass
    finally:
        mp.eof = True


def _status_line(mp: ManagedProcess) -> str:
    code = mp.returncode()
    elapsed = (mp.ended_at or time.monotonic()) - mp.started_at
    if mp.stopped:
        return f"Stopped after {elapsed:.1f} s."
    if code is None:
        return f"Still running after {elapsed:.1f} s."
    return f"Exited with code {code} after {elapsed:.1f} s."


def _wait_ms(args: dict[str, Any], default: int) -> int:
    from ..tools.builtins import _int_arg

    raw = args.get("wait_ms")
    if raw is None:
        return default
    return min(_int_arg(raw, "wait_ms", minimum=0), MAX_WAIT_MS)


async def _wait_for_output(mp: ManagedProcess, wait_ms: int, since: int) -> None:
    """Wait up to ``wait_ms`` for new output past ``since``: return early
    when the program ends, or once output has arrived and gone quiet for
    :data:`QUIET_S`. Polls with ``asyncio.sleep`` (never blocks the loop)."""
    deadline = time.monotonic() + wait_ms / 1000.0
    while time.monotonic() < deadline:
        if not mp.running() and mp.eof:
            return
        if mp.total > since and time.monotonic() - mp.last_output_at >= QUIET_S:
            return
        await asyncio.sleep(POLL_S)


def _render(mp: ManagedProcess, text: str, dropped: int, more: bool, lead: str = "") -> str:
    parts = [lead] if lead else []
    parts.append(_status_line(mp))
    if dropped:
        parts.append(f"[{dropped:,} bytes of earlier output were dropped (only the newest {RING_BYTES:,} are kept)]")
    parts.append("Output:" if text else "No new output.")
    if text:
        parts.append(text.rstrip("\n"))
    if more:
        parts.append(f"[more output is waiting — call process_read on {mp.handle} again]")
    return "\n".join(parts)


def _data(mp: ManagedProcess, text: str, dropped: int, more: bool) -> dict[str, Any]:
    return {
        "id": mp.handle,
        "pid": getattr(mp.proc, "pid", None),
        "running": mp.running(),
        "exit_code": None if mp.stopped else mp.returncode(),
        "stopped": mp.stopped,
        "chars": len(text),
        "dropped_bytes": dropped,
        "more": more,
    }


def _session_of(ctx: ToolContext) -> str:
    return str(ctx.session_id or "")


class ProcessStartTool(Tool):
    name = "process_start"
    description = (
        "Start a long-running program (a dev server, a watcher, a REPL) in the "
        "workspace, or in `cwd` inside it, and keep it running. Returns an id "
        "(p1, p2, …) and what it printed in its first `wait_ms` (default 1000, "
        "at most 30000). Then process_read reads new output, process_write "
        "types into it, process_stop stops it. It keeps running until "
        "process_stop, the end of the agent run that started it, or Iron "
        "Jarvis shutting down. Use shell instead for a command that finishes "
        "on its own."
    )
    permission_key = "process_start"
    risk_class = RiskClass.EXTERNAL_COMMIT
    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "cwd": {"type": "string", "description": "Folder inside the workspace (default: the workspace)."},
            "wait_ms": {"type": "integer", "description": "How long to wait for its first output (default 1000, max 30000)."},
        },
        "required": ["command"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from ..sandbox.native import scrubbed_env
        from ..sandbox.policy import SandboxPolicy

        command = str(args.get("command") or "").strip()
        if not command:
            return ToolResult(ok=False, error="`command` is empty")
        try:
            wait_ms = _wait_ms(args, DEFAULT_START_WAIT_MS)
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        session = _session_of(ctx)
        running = TABLE.running(session)
        if len(running) >= MAX_RUNNING_PER_SESSION:
            ids = ", ".join(mp.handle for mp in running)
            return ToolResult(
                ok=False,
                error=(
                    f"this session already has {len(running)} programs running ({ids}) — "
                    "stop one with process_stop first"
                ),
            )
        raw_cwd = args.get("cwd")

        def _resolve() -> Path:
            if raw_cwd is None or str(raw_cwd).strip() in ("", "."):
                return ctx.workspace.resolve()
            try:
                folder = safe_path(ctx.workspace, str(raw_cwd))
            except PermissionError:
                raise ValueError(f"`cwd` '{raw_cwd}' is outside the session's folder") from None
            if not folder.is_dir():
                raise ValueError(f"`cwd` '{raw_cwd}' is not a folder in the workspace")
            return folder

        try:
            cwd = await asyncio.to_thread(_resolve)
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        policy = SandboxPolicy.from_config(getattr(ctx.config, "sandbox", {}))
        env = scrubbed_env() if policy.modify_env == "deny" else None

        def _spawn() -> Any:
            return procs.spawn(
                command,
                shell=True,
                cwd=str(cwd),
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )

        try:
            proc = await asyncio.to_thread(_spawn)
        except OSError as exc:
            return ToolResult(ok=False, error=f"could not start the program: {exc}")
        mp = ManagedProcess(
            handle=TABLE.new_handle(), session_id=session, command=command, cwd=str(cwd), proc=proc
        )
        mp.reader = threading.Thread(target=_reader, args=(mp,), name=f"ij-proc-{mp.handle}", daemon=True)
        mp.reader.start()
        TABLE.add(mp)
        await _wait_for_output(mp, wait_ms, 0)
        text, dropped, more = mp.take()
        lead = f"Started {mp.handle} (pid {getattr(proc, 'pid', '?')}): {command}"
        data = _data(mp, text, dropped, more)
        # Like the shell tool's native fallback (v1.232.0 T5): a program
        # started here runs on this PC directly, so an isolating sandbox
        # policy is advisory for it - said on the row and in the output.
        from ..sandbox.shell_tool import (
            CONFINEMENT_NATIVE,
            CONFINEMENT_NATIVE_UNCONFINED,
            _is_isolating,
        )

        isolating = _is_isolating(policy)
        data["confinement"] = CONFINEMENT_NATIVE_UNCONFINED if isolating else CONFINEMENT_NATIVE
        if isolating:
            lead += "\n[runs on this PC directly - the sandbox's limits are advisory for it]"
        return ToolResult(ok=True, output=_render(mp, text, dropped, more, lead), data=data)


def _lookup(args: dict[str, Any], ctx: ToolContext) -> tuple[ManagedProcess | None, str]:
    handle = str(args.get("id") or "").strip()
    if not handle:
        return None, "`id` is required (the id process_start returned, like p1)"
    mp = TABLE.get(_session_of(ctx), handle)
    if mp is None:
        mine = ", ".join(r.handle for r in TABLE.sessions(_session_of(ctx))) or "none"
        return None, f"no program {handle!r} in this session (this session's programs: {mine})"
    return mp, ""


class ProcessReadTool(Tool):
    name = "process_read"
    description = (
        "Read what a program started with process_start printed since the last "
        "read, and whether it is still running. `wait_ms` (default 0, max "
        "30000) waits for new output first."
    )
    permission_key = "process_read"
    reversibility = Reversibility.READONLY
    risk_class = RiskClass.READ
    input_schema = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "wait_ms": {"type": "integer"},
        },
        "required": ["id"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        mp, err = _lookup(args, ctx)
        if mp is None:
            return ToolResult(ok=False, error=err)
        try:
            wait_ms = _wait_ms(args, 0)
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        if wait_ms:
            await _wait_for_output(mp, wait_ms, mp.cursor)
        text, dropped, more = mp.take()
        return ToolResult(ok=True, output=_render(mp, text, dropped, more), data=_data(mp, text, dropped, more))


class ProcessWriteTool(Tool):
    name = "process_write"
    description = (
        "Type `input` into a program started with process_start (include \\n "
        "to press Enter); `close_stdin: true` ends its input afterwards. "
        "Returns the output that followed within `wait_ms` (default 500)."
    )
    permission_key = "process_write"
    risk_class = RiskClass.EXTERNAL_COMMIT
    input_schema = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "input": {"type": "string"},
            "close_stdin": {"type": "boolean"},
            "wait_ms": {"type": "integer"},
        },
        "required": ["id"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from ..tools.builtins import _bool_arg

        mp, err = _lookup(args, ctx)
        if mp is None:
            return ToolResult(ok=False, error=err)
        text_in = args.get("input")
        text_in = "" if text_in is None else str(text_in)
        if len(text_in) > MAX_INPUT_CHARS:
            return ToolResult(ok=False, error=f"`input` is {len(text_in):,} characters — at most {MAX_INPUT_CHARS:,} per call")
        try:
            close = False if args.get("close_stdin") is None else _bool_arg(args["close_stdin"], "close_stdin")
            wait_ms = _wait_ms(args, 500)
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        if not text_in and not close:
            return ToolResult(ok=False, error="give `input` to type, or close_stdin: true")
        if not mp.running():
            return ToolResult(ok=False, error=f"{mp.handle} is not running ({_status_line(mp)})")
        stdin = getattr(mp.proc, "stdin", None)
        if stdin is None or stdin.closed:
            return ToolResult(ok=False, error=f"{mp.handle}'s input is already closed")
        payload = text_in.encode("utf-8")
        outcome: dict[str, Any] = {}

        def _write() -> None:
            try:
                if payload:
                    stdin.write(payload)
                    stdin.flush()
                if close:
                    stdin.close()
                outcome["ok"] = True
            except Exception as exc:  # noqa: BLE001 — a closed pipe is reported
                outcome["error"] = str(exc) or type(exc).__name__

        writer = threading.Thread(target=_write, name=f"ij-proc-write-{mp.handle}", daemon=True)
        since = mp.total
        writer.start()
        await asyncio.to_thread(writer.join, WRITE_WAIT_S)
        if "error" in outcome:
            return ToolResult(ok=False, error=f"could not write to {mp.handle}: {outcome['error']}")
        lead = f"Wrote {len(payload):,} bytes to {mp.handle}" + (" and closed its input" if close else "")
        if writer.is_alive():
            lead += " (the program has not read all of it yet; the rest is still being written)"
        if wait_ms:
            await _wait_for_output(mp, wait_ms, since)
        text, dropped, more = mp.take()
        return ToolResult(ok=True, output=_render(mp, text, dropped, more, lead), data=_data(mp, text, dropped, more))


class ProcessStopTool(Tool):
    name = "process_stop"
    description = (
        "Stop a program started with process_start, and everything it started. "
        "Returns its last unread output."
    )
    permission_key = "process_stop"
    risk_class = RiskClass.LOCAL_UI
    input_schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}},
        "required": ["id"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        mp, err = _lookup(args, ctx)
        if mp is None:
            return ToolResult(ok=False, error=err)
        was_running = mp.running()
        await asyncio.to_thread(_stop, mp)
        text, dropped, more = mp.take()
        TABLE.remove(mp)
        lead = f"Stopped {mp.handle}: {mp.command}" if was_running else f"{mp.handle} had already ended: {mp.command}"
        data = _data(mp, text, dropped, more)
        data["was_running"] = was_running
        return ToolResult(ok=True, output=_render(mp, text, dropped, False, lead), data=data)


def process_tools() -> list[Tool]:
    return [ProcessStartTool(), ProcessReadTool(), ProcessWriteTool(), ProcessStopTool()]
