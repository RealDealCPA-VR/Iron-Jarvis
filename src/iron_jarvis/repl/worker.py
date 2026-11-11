"""The child process behind the persistent Python session namespace.

One long-lived interpreter, one `dict` of globals that survives every request,
newline-delimited JSON over stdin/stdout. Variables set by one request are
visible to the next — that persistence is the entire reason this process
exists, and it is why the namespace is created once in `Session.__init__` and
never rebuilt.

WHY OUTPUT IS CAPPED (`MAX_OUTPUT_CHARS` / `MAX_RESULT_CHARS`)
    Context flooding is the problem this whole feature exists to solve. A model
    that runs `print(df)` on a big frame, or ends a cell with a list of a
    million rows, gets that text pasted straight into its own context window;
    one careless cell then evicts the conversation that made the cell make
    sense. So both the captured streams and the trailing expression's `repr`
    are clipped to a hard character budget before they ever reach the parent.

    Truncation is always VISIBLE and always says how much was dropped. This
    repo's rule is that silent truncation is worse than slow: a model treats
    what it received as complete, so a quietly shortened listing becomes "that
    file does not exist" and a quietly shortened result becomes a wrong answer
    stated confidently. A loud marker turns a lossy read into a known-lossy
    read, which the model can act on (re-run with a filter, page through it).
    The middle is what gets dropped — the head shows what the cell started
    doing and the tail shows how it ended, which is what a print loop is
    actually asked about.

WHY THIS MODULE IS STANDALONE (stdlib only, no `iron_jarvis` imports)
    The packaged desktop app spawns this worker from the PyInstaller-frozen
    binary. Importing the daemon here would be both circular (the daemon owns
    the parent that spawns us) and heavy (FastAPI, SQLModel, the provider
    stack, the whole app's import graph) — paid on every session start, inside
    a process whose only job is `exec`. It would also drag every one of those
    packages into the worker's slice of the frozen bundle. Keep the imports
    below limited to the standard library; `tests/test_repl_worker.py` asserts
    it, and loads this file by path with nothing else importable to prove it.

WHY THE PROTOCOL IS MOVED OFF fd 0 AND fd 1 (`_isolate_protocol_streams`)
    User code owns this interpreter, so `redirect_stdout` — which only rebinds
    the `sys.stdout` OBJECT — protects nothing that reaches the descriptor
    underneath. All of these used to write straight onto the response pipe:
    `os.write(1, ...)`, a `subprocess.run(...)` child inheriting fd 1 (the
    common one — a REPL is where a model runs `git status`), `sys.__stdout__`,
    and any thread that prints after its cell returned and the redirect was
    undone. A stray line desynchronises the parser, and a stray write landing
    mid-response corrupts the response itself — which the parent cannot match
    to its request, so it waits out its timeout and kills the child, reporting
    "still running" about code that finished. The mirror image is worse: fd 0
    is the REQUEST pipe, so `input()` blocked until the parent sent its next
    request, ate that JSON as the user's input, and left it unanswered forever.

    So at startup the real descriptors are dup'd aside for our exclusive use,
    fd 1 is repointed at a pipe we drain ourselves, and fd 0 at `os.devnull`.
    User code then cannot reach the protocol by any route, `input()` fails fast
    with EOFError, and output written out of band is still REPORTED (appended
    to `stdout` with a marker saying it arrived out of order) rather than
    silently dropped. fd 2 is deliberately left alone: the parent keeps the
    child's real stderr as its crash diagnostic, and nothing on stderr can
    corrupt a protocol that lives on stdout.

PROTOCOL (owned by the parent — do not change unilaterally)
    request  {"id": str, "code": str}
    response {"id": str, "ok": bool, "stdout": str, "stderr": str,
              "result": str, "error": str, "truncated": bool}

    `result` is `repr()` of the final expression's value, IPython-style: only
    when the last statement IS an expression, and only when that value is not
    `None` (so a cell ending in `print(x)` reports its output in `stdout` and
    leaves `result` empty, rather than echoing a useless "None").
    `error` is a formatted traceback and `ok` is false when user code raised —
    but `stdout`/`stderr` still carry everything produced BEFORE the raise,
    because partial output is usually the whole diagnosis.
    A malformed line answers with `ok=false` and an explanation; it never kills
    the worker, because a single garbled line must not cost the user the
    namespace they have been building all session.

WHY WRITES ARE CONFINED AND READS ARE NOT (`install_confinement`)
    Every other file tool in this app routes through `core/fs_policy.py`; this
    process routed through nothing, and the gap was measurable — `read_file`
    refused the app's own Fernet key while a REPL cell printed it, and a cell
    writing to an absolute path outside the workspace succeeded while
    `created_paths` reported nothing, because that diff only ever scans INSIDE
    the workspace. A file written somewhere nobody is told about is worse than
    a file written somewhere untidy.

    The policy is deliberately ASYMMETRIC, because it follows how the tool is
    used. READS stay broad: the documents this app exists to work on live all
    over the user's disk, and a REPL that cannot open them is a worse tool than
    no REPL. WRITES pin to the roots the parent hands over (the session
    workspace — which is the grounded project's folder when there is one — plus
    a private scratch dir standing in for the system temp root, which would
    otherwise expose every other program's temp files), so output lands where
    the app is already watching for it and can hand it back with an absolute
    path. Protected roots (the secrets/key dirs)
    are refused in BOTH directions, matching `fs_policy.fs_read_ok` exactly.

    Enforcement is a `sys.addaudithook`, installed before any user code runs.
    Audit hooks cannot be removed once added, and the roots are read out of the
    environment ONCE at install time into a closure, so unsetting the variable
    afterwards changes nothing. Subprocess spawning and `ctypes` are refused
    outright — not as new policy, but because each is a direct way around the
    rule above (a child process inherits none of this, and FFI reaches the
    filesystem without raising a single audit event); refusing them also closes
    the one lifecycle hole this worker had, where a grandchild outlived the
    kill that ended its parent cell.

    BE HONEST ABOUT WHAT THIS IS. It is a guardrail against a careless or
    confused model, not a sandbox: an audit hook lives inside the interpreter
    it polices. Code written to defeat it has options. A boundary that holds
    against hostile input needs OS-level isolation (a separate low-privilege
    account, or a container), which is a different piece of work.

`SystemExit` and `KeyboardInterrupt` raised BY USER CODE are caught and
reported like any other failure, and the session keeps its namespace. `exit()`
is how a model ends a script it pasted into a cell; letting it through killed
the worker mid-request, so the parent got no response at all, waited out its
timeout and told the user their code was still running. The parent's own kill
path is unaffected — it terminates the process or closes the pipe, neither of
which is an exception in here. `os._exit()` genuinely cannot be intercepted;
that one death is the parent's to report as a lost namespace.
"""

from __future__ import annotations

import ast
import builtins
import io
import json
import linecache
import os
import re
import sys
import threading
import traceback
from collections import deque
from contextlib import redirect_stderr, redirect_stdout
from typing import IO, Any

# Hard budget shared by stdout+stderr, in characters. See the module docstring:
# this is the anti-context-flooding limit, not a performance knob.
MAX_OUTPUT_CHARS = 20000

# Same budget again, applied separately to the repr of the trailing expression.
# `repr(list(range(5_000_000)))` is ~48 MB of text and must never be returned.
MAX_RESULT_CHARS = 20000

# The filename compiled code is tagged with, so tracebacks read `File "<repl>"`
# instead of naming this module and confusing the reader about whose bug it is.
# Each cell gets its own `<repl>#N` variant — see `_cell_filename`.
REPL_FILENAME = "<repl>"

# How many cells' sources stay in `linecache` so tracebacks can quote them.
# Bounded because this process is long-lived by design: remembering every cell
# forever is a leak that grows with exactly the sessions the user values most.
# Past the cap the oldest cell loses its source lines, not its traceback.
MAX_REMEMBERED_CELLS = 64

# Bytes of out-of-band fd-1 output held between requests. A subprocess printing
# a gigabyte must cost a bounded buffer, and the overflow is reported.
MAX_RAW_FD_BYTES = 4 * MAX_OUTPUT_CHARS

# How long a request waits for the fd-1 drain to catch up before giving up and
# letting those bytes surface in a later response. Only reached if a writer is
# still holding the pipe, so it never delays an ordinary cell.
RAW_SYNC_TIMEOUT_S = 2.0

_TRUNCATION_MARKER = (
    "\n\n[iron-jarvis repl] {what} TRUNCATED: dropped {dropped} of {total} "
    "characters (limit {limit}). The omitted text is the MIDDLE of the output; "
    "what follows is the tail. This output is INCOMPLETE.\n\n"
)

_RAW_OUTPUT_NOTE = (
    "\n[iron-jarvis repl] the text below was written straight to this process's "
    "stdout — by a subprocess, by `os.write(1, ...)`, or by a thread that "
    "outlived the cell that started it — instead of through `print`. It is "
    "real output, but its position relative to the lines above is NOT known.\n"
)

_RAW_DROP_MARKER = (
    "[iron-jarvis repl] raw output TRUNCATED: dropped the oldest {dropped} "
    "bytes (buffer limit {limit}). This output is INCOMPLETE.\n"
)

_SYSTEM_EXIT_NOTE = (
    "\n[iron-jarvis repl] the cell asked the process to exit (`exit()`, "
    "`sys.exit()`, or a bare `SystemExit`). That was NOT honoured: this "
    "interpreter holds the session's namespace, so it is still running and "
    "every variable, import and open file is intact.\n"
)

_INTERRUPT_NOTE = (
    "\n[iron-jarvis repl] the cell was interrupted before it finished. The "
    "session is still running and its namespace is intact.\n"
)

# Written down the fd-1 pipe to mark "everything before this point is mine";
# see `_RawStdoutCapture.sync`. NUL-wrapped so it cannot occur in real output.
_SYNC_PREFIX = b"\x00[ij-repl-sync-"
_SYNC_SUFFIX = b"]\x00"


def _clip(text: str, limit: int, what: str) -> tuple[str, bool]:
    """Return `text` cut to roughly `limit` chars, plus whether it was cut.

    Keeps the head and the tail and elides the middle, with a marker naming the
    exact number of dropped characters. The marker itself is allowed to push
    the string slightly past `limit` — the alternative is to trim the warning
    that says the text is incomplete, which defeats the purpose.
    """
    if len(text) <= limit:
        return text, False
    marker = _TRUNCATION_MARKER.format(
        what=what, dropped=len(text) - limit, total=len(text), limit=limit
    )
    head = limit * 3 // 4
    tail = limit - head
    if tail <= 0:
        return text[:limit] + marker, True
    return text[:head] + marker + text[-tail:], True


def _cap_output(stdout: str, stderr: str) -> tuple[str, str, bool]:
    """Fit stdout+stderr into one shared `MAX_OUTPUT_CHARS` budget.

    Shared rather than per-stream because the parent pastes both into the same
    context window, so two independently "small enough" streams can still flood
    it. stderr is guaranteed a slice of the budget even when stdout is the hog:
    a traceback or a warning is the part that explains the run.
    """
    if len(stdout) + len(stderr) <= MAX_OUTPUT_CHARS:
        return stdout, stderr, False
    err_budget = min(len(stderr), MAX_OUTPUT_CHARS // 4)
    out_budget = MAX_OUTPUT_CHARS - err_budget
    if len(stdout) < out_budget:
        # stdout fits; hand the rest of the budget to the noisy stderr.
        out_budget = len(stdout)
        err_budget = MAX_OUTPUT_CHARS - out_budget
    clipped_out, cut_out = _clip(stdout, out_budget, "stdout")
    clipped_err, cut_err = _clip(stderr, err_budget, "stderr")
    return clipped_out, clipped_err, (cut_out or cut_err)


def _strip_sync_markers(raw: bytes) -> bytes:
    """Remove any sync sentinels left in the buffer by a timed-out `sync`.

    Normally `sync` consumes its own sentinel. If it gave up waiting, the bytes
    can still arrive later, and they must never be shown to the user as though
    the cell had printed them.
    """
    out = bytearray()
    index = 0
    while True:
        start = raw.find(_SYNC_PREFIX, index)
        if start < 0:
            out += raw[index:]
            return bytes(out)
        end = raw.find(_SYNC_SUFFIX, start)
        if end < 0:  # a partially written sentinel; keep waiting for the rest
            out += raw[index:start]
            return bytes(out)
        out += raw[index:start]
        index = end + len(_SYNC_SUFFIX)


class _RawStdoutCapture:
    """Drains the pipe that fd 1 now points at, so nothing can block on it.

    The drain runs in its own thread for one reason: if nobody reads, a
    subprocess writing more than the OS pipe buffer blocks forever, and the
    cell that spawned it hangs until the parent's timeout kills the session.
    Reading always, and bounding what we keep, makes that impossible.
    """

    def __init__(self, read_fd: int) -> None:
        self._read_fd = read_fd
        self._lock = threading.Lock()
        self._seen = threading.Event()
        self._buffer = bytearray()
        self._dropped = 0
        self._sentinel = b""
        self._counter = 0
        self._thread = threading.Thread(
            target=self._drain, name="repl-raw-stdout", daemon=True
        )
        self._thread.start()

    def _drain(self) -> None:
        while True:
            try:
                block = os.read(self._read_fd, 65536)
            except OSError:  # pragma: no cover - pipe torn down at shutdown
                return
            if not block:
                return
            with self._lock:
                self._buffer += block
                if self._sentinel and self._sentinel in self._buffer:
                    self._buffer = bytearray(
                        bytes(self._buffer).replace(self._sentinel, b"", 1)
                    )
                    self._seen.set()
                overflow = len(self._buffer) - MAX_RAW_FD_BYTES
                if overflow > 0:
                    # Keep the tail: a subprocess's failure is at the end.
                    del self._buffer[:overflow]
                    self._dropped += overflow

    def sync(self) -> None:
        """Block until everything already written to fd 1 has been drained.

        A pipe is FIFO, so any writer that had FINISHED writing before this
        call is queued ahead of the sentinel we now send; seeing the sentinel
        come back proves we hold its bytes. Without this the response for a
        cell that ran `subprocess.run` would race the drain thread and report
        the child's output in some later cell instead — true but useless.
        """
        with self._lock:
            self._counter += 1
            self._sentinel = _SYNC_PREFIX + str(self._counter).encode() + _SYNC_SUFFIX
            self._seen.clear()
        try:
            os.write(1, self._sentinel)
        except OSError:  # pragma: no cover - fd 1 closed by user code
            return
        self._seen.wait(RAW_SYNC_TIMEOUT_S)
        with self._lock:
            self._sentinel = b""

    def take(self) -> str:
        """Hand over everything captured so far and reset the buffer."""
        with self._lock:
            raw = _strip_sync_markers(bytes(self._buffer))
            dropped = self._dropped
            self._buffer = bytearray()
            self._dropped = 0
        if not raw and not dropped:
            return ""
        text = raw.decode("utf-8", "replace")
        if dropped:
            text = _RAW_DROP_MARKER.format(
                dropped=dropped, limit=MAX_RAW_FD_BYTES
            ) + text
        return _RAW_OUTPUT_NOTE + text


#: Installed by `_isolate_protocol_streams`; `None` when isolation was not
#: possible (a build with no real descriptors), in which case the worker keeps
#: its pre-isolation behaviour rather than refusing to start.
_RAW_CAPTURE: _RawStdoutCapture | None = None


def _take_raw_output() -> str:
    """Out-of-band fd-1 output for the request that just finished."""
    capture = _RAW_CAPTURE
    if capture is None:
        return ""
    capture.sync()
    return capture.take()


def _isolate_protocol_streams() -> tuple[IO[str] | None, IO[str] | None]:
    """Move the protocol onto private descriptors. Returns (source, sink).

    Returns `(None, None)` if the descriptors cannot be re-pointed — better a
    worker with the old exposure than one that will not start. See the module
    docstring for what this defends against and why `sys.stderr` is left alone.
    """
    global _RAW_CAPTURE
    try:
        sys.stdout.flush()
        real_in, real_out = sys.stdin.fileno(), sys.stdout.fileno()
    except (AttributeError, ValueError, OSError):  # pragma: no cover
        return None, None
    if (real_in, real_out) != (0, 1):  # pragma: no cover - not our layout
        return None, None
    source_fd = sink_fd = read_fd = -1
    try:
        source_fd = os.dup(0)
        sink_fd = os.dup(1)
        read_fd, write_fd = os.pipe()
        os.dup2(write_fd, 1)
        os.close(write_fd)
        null_fd = os.open(os.devnull, os.O_RDONLY)
        os.dup2(null_fd, 0)
        os.close(null_fd)
    except OSError:  # pragma: no cover - no descriptors to move
        # Half-applied isolation is the one outcome worse than none: fd 1 would
        # point at a pipe nobody drains, and every response would vanish into
        # it. Put the descriptor back before giving up.
        for fd in (sink_fd, read_fd, source_fd):
            if fd < 0:
                continue
            if fd == sink_fd:
                try:
                    os.dup2(sink_fd, 1)
                except OSError:
                    pass
            try:
                os.close(fd)
            except OSError:
                pass
        return None, None

    source = os.fdopen(source_fd, "r", encoding="utf-8", errors="replace")
    # newline="\n" so Windows cannot translate the framing newline into CRLF.
    sink = os.fdopen(sink_fd, "w", encoding="utf-8", errors="replace", newline="\n")

    # Rebind the stream objects too. `sys.__stdout__` is the copy `redirect_*`
    # cannot touch, and it used to be the response pipe itself; both now point
    # at the capture pipe, and stdin at devnull so `input()` fails immediately.
    # buffering=1 (line buffered): fd 1 is a pipe, so the default is an 8 KB
    # block buffer, and a stray `print` from a thread would then sit unflushed
    # until the process ended — captured in theory and invisible in practice.
    sys.stdout = open(
        1, "w", buffering=1, encoding="utf-8", errors="replace", closefd=False
    )
    sys.stdin = open(0, "r", encoding="utf-8", errors="replace", closefd=False)
    sys.__stdout__ = sys.stdout  # type: ignore[misc]
    sys.__stdin__ = sys.stdin  # type: ignore[misc]
    _RAW_CAPTURE = _RawStdoutCapture(read_fd)
    return source, sink


# --------------------------------------------------------------------------- #
# Filesystem confinement. See the module docstring for the reasoning.
# --------------------------------------------------------------------------- #

#: How the parent hands this child its policy. Environment rather than argv so
#: the values never appear in a process listing, and rather than the protocol
#: because the hook must be armed before the first request is read. The parent
#: imports these names (`repl/session.py`) so the two sides cannot drift.
ENV_WRITE_ROOTS = "IRONJARVIS_REPL_WRITE_ROOTS"
ENV_READ_ROOTS = "IRONJARVIS_REPL_READ_ROOTS"
ENV_DENY_ROOTS = "IRONJARVIS_REPL_DENY_ROOTS"
#: "1" = the ISOLATED worker a tool script runs in (v1.470.0, the `tool_script`
#: tool). On top of the rules above it has no network, starts and signals no
#: other program, changes no registry key, and reads only the roots it was
#: handed plus the Python runtime's own folders. What it may write is the
#: private scratch folder alone (the parent hands it that as the one write
#: root). Everything it does for the user it does by calling a tool through
#: the bridge (:class:`_Tools`), where the app's own gates apply.
ENV_ISOLATED = "IRONJARVIS_REPL_ISOLATED"

#: Audit events that create, modify, move or delete something at a path. Each
#: entry maps the event name to the indices of its path-bearing arguments —
#: `os.rename` has two, and BOTH ends matter (renaming a workspace file onto
#: `C:/Windows/...` is a write to `C:/Windows/...`).
_WRITE_EVENTS: dict[str, tuple[int, ...]] = {
    "os.mkdir": (0,),
    "os.rmdir": (0,),
    "os.remove": (0,),          # also raised by os.unlink
    "os.rename": (0, 1),        # also raised by os.replace
    "os.link": (0, 1),
    "os.symlink": (0, 1),
    "os.truncate": (0,),
    "os.chmod": (0,),
    "os.chown": (0,),
    "os.utime": (0,),
    "shutil.copyfile": (1,),
    "shutil.copymode": (1,),
    "shutil.copystat": (1,),
    "shutil.move": (1,),
    "shutil.rmtree": (0,),
    "shutil.unpack_archive": (1,),
}

#: COPIES (v1.473.0 review). On Python 3.12 for Windows `shutil.copy2`,
#: `copytree` and a `move` across drives copy with Windows' own CopyFile2: no
#: `open` event and no `shutil.copyfile` event, only this one. Each entry is
#: (the argument that is READ, the argument that is WRITTEN). Without it a
#: script copied a client file into its scratch folder and read it there
#: unseen, read any file on the disk the same way, and wrote anywhere.
_COPY_EVENTS: dict[str, tuple[int, int]] = {
    "_winapi.CopyFile2": (0, 1),
}

#: Read-ish events worth checking against the protected roots. `open` is
#: handled separately because only it can be either direction.
_READ_EVENTS: dict[str, tuple[int, ...]] = {
    "os.listdir": (0,),
    "os.scandir": (0,),
    # v1.473.0 review: changing into a folder reaches it (a network folder
    # is connected to), and makes every later relative path mean that place.
    "os.chdir": (0,),
}

#: Spawning a process escapes every rule above, because the child inherits the
#: OS's permissions and none of this hook.
_SPAWN_EVENTS = frozenset({
    "subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn",
})

#: FFI reaches the filesystem through the C library, raising no audit event at
#: all, so leaving it open would make the write rule advisory.
_FFI_EVENTS = frozenset({"ctypes.dlopen", "ctypes.dlsym", "ctypes.dlsym/handle"})

#: ISOLATED worker only: anything that opens a network connection or looks a
#: name up. Every `socket.*` event is refused too (see the hook) except
#: `socket.gethostname`, which reaches nothing.
_NETWORK_EVENTS = frozenset({
    "urllib.Request", "http.client.connect", "ftplib.connect", "smtplib.connect",
    "imaplib.open", "poplib.connect", "nntplib.connect", "telnetlib.Telnet.open",
    "webbrowser.open",
})

#: ISOLATED worker only: other ways to start, stop or reconfigure something
#: outside this process.
_OUTSIDE_EVENTS = frozenset({
    "os.startfile", "os.kill", "os.killpg", "os.fork", "os.forkpty",
    "_winapi.CreateProcess", "signal.pthread_kill",
    # v1.473.0 review: Windows' own file and process calls open a file (or
    # a pipe, a junction, another process) with no `open` event at all.
    "_winapi.CreateFile", "_winapi.CreateNamedPipe", "_winapi.CreateJunction",
    "_winapi.OpenProcess", "_winapi.TerminateProcess",
    "os.add_dll_directory",
    # Windows' own query service: every running program and its command line.
    "_wmi.exec_query",
    "winreg.CreateKey", "winreg.DeleteKey", "winreg.DeleteValue",
    "winreg.SetValue", "winreg.SaveKey", "winreg.LoadKey",
    "winreg.EnableReflectionKey", "winreg.DisableReflectionKey",
})

#: Key files refused BY NAME, independent of directory containment — the same
#: belt-and-braces layer `core/fs_policy._PROTECTED_NAME_PREFIXES` carries, and
#: for the same reason: it also covers the `.bak`/`.new`/`.tmp` siblings a
#: rotation leaves behind, which no root list will name.
_PROTECTED_NAMES = (".secrets.key", ".vault.key")

#: `open` flags that mean "this may modify the file".
_WRITE_FLAGS = (
    os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
    | getattr(os, "O_EXCL", 0)
)

_WRITE_BLOCKED = (
    "iron-jarvis repl: BLOCKED — writing outside this session's folder.\n"
    "  tried to write : {target}\n"
    "  allowed folder : {roots}\n"
    "Write it inside the allowed folder and the app reports the file back with "
    "its absolute path, so the user gets a preview and a download. To work "
    "somewhere else, that folder has to be selected as the project (or the "
    "chat's workspace folder) first — the REPL cannot choose it for itself."
)

_PROTECTED_BLOCKED = (
    "iron-jarvis repl: BLOCKED — {target} is inside a protected secrets/key "
    "directory, which is never readable or writable by any tool in this app "
    "(the same rule `read_file` enforces). There is no way around this from "
    "here, and nothing in there is needed to answer a user's question."
)

_SPAWN_BLOCKED = (
    "iron-jarvis repl: BLOCKED — starting a subprocess from the REPL. A child "
    "process inherits none of this session's folder confinement and can outlive "
    "the timeout that ends a cell. Use the `shell` tool instead: it is approved "
    "separately, its output is captured, and it is covered by the undo journal."
)

_FFI_BLOCKED = (
    "iron-jarvis repl: BLOCKED — loading a native library (ctypes/FFI). Native "
    "calls reach the filesystem without passing this session's folder check, so "
    "allowing them would make that check meaningless. Do this in pure Python, "
    "or use the `shell` tool."
)


_NETWORK_BLOCKED = (
    "iron-jarvis tool script: BLOCKED — the network. A tool script runs in an "
    "isolated process that cannot open a connection or look up a name. Reach "
    "the outside through a tool instead: tools.call(\"web_fetch\", url=...) "
    "when that tool is one of this turn's."
)

_OUTSIDE_BLOCKED = (
    "iron-jarvis tool script: BLOCKED — starting, stopping or reconfiguring "
    "something outside this script. A tool script only computes and calls "
    "tools: tools.call(name, ...)."
)

#: ISOLATED worker only (v1.473.0 review). SQLite opens its files itself: no
#: `open` event, so a database read was invisible to the rule "what a script
#: read stays where it was read", a database anywhere on the disk could be
#: read, and one could be written into the working folder. A connection in
#: memory is no better: `ATTACH DATABASE '<any path>'` raises no event either.
_SQLITE_BLOCKED = (
    "iron-jarvis tool script: BLOCKED — opening a SQLite database. A tool "
    "script reads files with open() and this turn's tools; SQLite opens "
    "files in a way the app cannot see or limit. Read the data another way: "
    "a tool of this turn, or an export of the table to CSV."
)

#: ISOLATED worker only (v1.473.0 review). The worker's environment is
#: scrubbed of keys and tokens, but the user's own environment variables are
#: kept in the registry (HKCU\\Environment), and so is much else that is
#: private to this PC. Every registry call is refused; the standard library
#: treats that like a missing key (`PermissionError` is an `OSError`).
_REGISTRY_BLOCKED = (
    "iron-jarvis tool script: BLOCKED — the Windows registry. A tool script "
    "computes and calls this turn's tools; it does not read this PC's settings."
)

#: ISOLATED worker only (v1.473.0 review): a path spelled as a place on
#: ANOTHER computer. Opening it, or even asking whether it exists, makes
#: Windows connect to that computer (and offer it the user's sign-in), with
#: whatever the script put in the path. Judged from the TEXT, before anything
#: touches the path.
_SHARE_BLOCKED = (
    "iron-jarvis tool script: BLOCKED — {target} is on another computer. A "
    "tool script reads files only inside the turn's working folder."
)

#: ISOLATED worker only: Tcl (tkinter) has its own `open`, `socket` and
#: `exec`, none of which raises an event.
_NO_IMPORT = frozenset({"tkinter", "_tkinter"})

#: ISOLATED worker only (v1.473.0 review): WHAT A TOOL SCRIPT MAY IMPORT.
#: Python's standard library, and these few libraries, each pure Python
#: (every file they touch goes through `open`, which the hook sees).
#:
#: An allow-list, because the other direction cannot be kept true: any
#: library with native code can open a file or a connection with no audit
#: event at all. Found in this app's own bundle: lxml (reads and writes any
#: file and fetches a web address by itself), pdf_inspector (reads any PDF),
#: pywin32 (the whole Windows API: files, registry, the clipboard, Excel),
#: PIL's screen and clipboard grab. Each was a way round "what a script read
#: stays where it was read" in three lines of script.
_SCRIPT_MODULES = frozenset({
    "openpyxl", "et_xmlfile", "defusedxml",   # workbooks, read through open()
    "dateutil", "six",                        # dates
    "tzdata",                                 # zoneinfo's data on Windows
})
_STDLIB = frozenset(getattr(sys, "stdlib_module_names", ()) or ())

_IMPORT_BLOCKED = (
    "iron-jarvis tool script: BLOCKED — importing {name}. A tool script may "
    "import Python's standard library (and openpyxl, dateutil). Read and "
    "convert files with this turn's tools instead: "
    "tools.call(\"read_document\", path=...)."
)

_ISOLATED_WRITE_BLOCKED = (
    "iron-jarvis tool script: BLOCKED — writing a file.\n"
    "  tried to write : {target}\n"
    "A tool script can only write temporary files in its own scratch folder "
    "({roots}). To give the user a file, print what it should hold and save "
    "it with a file tool (write_document, write_file) after the script."
)

_ISOLATED_READ_BLOCKED = (
    "iron-jarvis tool script: BLOCKED — reading {target}: a tool script reads "
    "files only inside the turn's working folder. Read anything else through "
    "a tool: tools.call(\"read_document\", path=...)."
)


def _canonical(path: str) -> str:
    """Absolute, symlink-resolved, case-normalised — comparable to a root.

    Mirrors `core/fs_policy._canonical`: Windows device and extended-length
    prefixes are stripped FIRST, because `\\\\?\\C:\\x` resolves with an anchor of
    `\\\\?\\C:\\` and so compares unequal to a normal root while `open()` honours
    it identically — a containment check that misses is a bypass. `realpath`
    rather than `Path.resolve` because it is defined for paths that do not exist
    yet, which is every file a cell is about to create, and because it resolves
    Windows JUNCTIONS as well as symlinks.
    """
    if os.name == "nt":
        text = path.replace("/", "\\")
        low = text.lower()
        if low.startswith("\\\\?\\unc\\"):
            text = "\\\\" + text[len("\\\\?\\UNC\\"):]
        elif low.startswith("\\\\?\\") or low.startswith("\\\\.\\"):
            text = text[4:]
        path = text
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def _within(target: str, root: str) -> bool:
    """Containment, on already-canonical strings."""
    return target == root or target.startswith(root.rstrip(os.sep) + os.sep)


def _network_spelled(path: str) -> bool:
    """Is *path* spelled as a network or device location (``\\\\host\\share``,
    ``//host/share``, ``\\\\?\\UNC\\…``, ``\\\\.\\pipe\\…``)? Text only: nothing is
    resolved. A local drive behind a long-path prefix (``\\\\?\\C:\\…``) is not."""
    text = str(path).strip().replace("/", "\\")
    if not text.startswith("\\\\"):
        return False
    rest = text[2:]
    if (
        rest[:2] in ("?\\", ".\\") and len(rest) >= 4 and rest[2].isalpha() and rest[3] == ":"
        and (len(rest) == 4 or rest[4] == "\\")
    ):
        return False
    return True


def _spelled_under(path: str, roots: tuple[str, ...]) -> bool:
    """Is a network-spelled *path* textually inside one of *roots* (the
    folders this worker was handed, canonical)? True only for a working
    folder that itself lives on a share."""
    text = str(path).strip().replace("/", "\\").lower()
    if text.startswith("\\\\?\\unc\\"):
        text = "\\\\" + text[8:]
    for root in roots:
        low = root.replace("/", "\\").lower().rstrip("\\")
        if low.startswith("\\\\") and (text == low or text.startswith(low + "\\")):
            return True
    return False


#: The path calls that raise NO audit event, by module. Each still makes
#: Windows reach the place the path names.
_UNAUDITED: dict[str, tuple[str, ...]] = {
    "os": ("stat", "lstat", "access", "readlink"),
    "nt": (
        "stat", "lstat", "access", "readlink", "_path_exists", "_path_isdir",
        "_path_isfile", "_path_islink", "_path_isdevdrive", "_getfinalpathname",
        "_getvolumepathname", "_getdiskusage",
    ),
    "ntpath": (
        "exists", "isdir", "isfile", "islink", "isdevdrive", "_getfinalpathname",
        "_getvolumepathname", "_nt_readlink",
    ),
}


def _guard_unaudited(roots: tuple[str, ...]) -> None:
    """ISOLATED worker: refuse a network path in the calls no audit event
    covers (``os.stat``, ``os.path.exists`` …). Each is replaced by a thin
    wrapper that reads the path's TEXT and otherwise calls the real one.
    A guardrail like the hook itself: the real function is still reachable
    by code that goes looking for it."""
    import importlib

    def guard(real: Any) -> Any:
        def guarded(path: Any = None, *args: Any, **kwargs: Any) -> Any:
            text = _as_path(path)
            if text is not None and _network_spelled(text) and not _spelled_under(text, roots):
                raise PermissionError(_SHARE_BLOCKED.format(target=text))
            return real(path, *args, **kwargs)

        guarded.__name__ = getattr(real, "__name__", "guarded")
        guarded.__doc__ = getattr(real, "__doc__", None)
        guarded._ij_guarded = True  # type: ignore[attr-defined]
        return guarded

    for module_name, names in _UNAUDITED.items():
        try:
            module = importlib.import_module(module_name)
        except Exception:  # pragma: no cover - not this platform
            continue
        for name in names:
            real = getattr(module, name, None)
            if callable(real) and not getattr(real, "_ij_guarded", False):
                try:
                    setattr(module, name, guard(real))
                except Exception:  # pragma: no cover - a read-only module
                    pass


def _as_path(value: Any) -> str | None:
    """Coerce an audit argument to a path string, or None when it is not one.

    Audit events hand over whatever the caller passed: `str`, `bytes`, a
    `PathLike`, or an integer file descriptor (`os.truncate(fd, n)`). A
    descriptor names no path we can check, and guessing would be worse than
    admitting it — see `_check_path`'s handling of None.
    """
    if value is None or isinstance(value, (int, bool)):
        return None
    if isinstance(value, bytes):
        try:
            return os.fsdecode(value)
        except Exception:
            return None
    if isinstance(value, str):
        return value
    try:
        return os.fspath(value)  # type: ignore[arg-type]
    except Exception:
        return None


def _install_audit_hook(
    write_roots: tuple[str, ...],
    read_roots: tuple[str, ...],
    deny_roots: tuple[str, ...],
    shown: str = "",
    isolated: bool = False,
    private_roots: tuple[str, ...] = (),
) -> None:
    """Arm the hook. The roots are closed over, so the environment stops mattering.

    ``private_roots`` (v1.473.0, isolated worker only): the turn's working
    folder. A read or a listing inside it raises :data:`_LOCAL_READ`.

    ``shown`` is the writable roots AS THE PARENT SPELLED THEM, used only in
    refusal messages. The comparison roots are ``normcase``-folded, which on
    Windows lowercases the whole path — quoting that back at a model hands it a
    path spelling that does not match anything else it has seen in the
    conversation, in the one message whose entire job is to say where to write.
    """
    shown = shown or " ; ".join(write_roots)
    write_blocked = _ISOLATED_WRITE_BLOCKED if isolated else _WRITE_BLOCKED

    def refuse(message: str) -> None:
        raise PermissionError(message)

    every_root = tuple(dict.fromkeys(write_roots + read_roots))

    def check(target: str | None, *, writing: bool) -> None:
        if target is None:
            return
        if isolated and _network_spelled(target) and not _spelled_under(target, every_root):
            # BEFORE anything resolves it: resolving is itself the connection.
            refuse(_SHARE_BLOCKED.format(target=target))
        if os.path.basename(target.replace("\\", "/")).startswith(_PROTECTED_NAMES):
            # Checked on the RAW name, before any resolution, so a path that
            # `realpath` mangles (see `_canonical`) cannot slip past on a
            # spelling trick — the key file is refused by what it is called.
            refuse(_PROTECTED_BLOCKED.format(target=target))
        try:
            canonical = _canonical(target)
        except Exception:
            # An unresolvable path is refused when it would be a write and
            # allowed when it would be a read: fail-closed on the side that
            # changes the disk, fail-open on the side that only looks.
            if writing:
                refuse(write_blocked.format(target=target, roots=shown))
            return
        for root in deny_roots:
            if _within(canonical, root):
                refuse(_PROTECTED_BLOCKED.format(target=target))
        if writing:
            if write_roots and not any(_within(canonical, r) for r in write_roots):
                refuse(write_blocked.format(target=target, roots=shown))
        elif isolated and read_roots and not any(_within(canonical, r) for r in read_roots):
            refuse(_ISOLATED_READ_BLOCKED.format(target=target))
        elif isolated:
            # An allowed read. One inside the working folder is the user's
            # own data: from here on this worker says so with every call.
            if private_roots and any(_within(canonical, r) for r in private_roots):
                _LOCAL_READ[0] = True
        elif read_roots and not any(_within(canonical, r) for r in read_roots):
            # v1.356.0 integrate (G81): plain words, never the variable's name.
            refuse(
                f"iron-jarvis repl: BLOCKED — reading {target}: that folder isn't one "
                f"Jarvis may read ({' ; '.join(read_roots)})."
            )

    def hook(event: str, args: tuple[Any, ...]) -> None:
        # Ordered by frequency: `open` dominates, everything else is rare, and
        # this function runs on EVERY audited operation in the interpreter.
        if event == "open":
            path = _as_path(args[0]) if args else None
            mode = args[1] if len(args) > 1 else None
            flags = args[2] if len(args) > 2 else None
            writing = False
            if isinstance(mode, str) and mode:
                writing = any(c in mode for c in "wxa+")
            elif isinstance(flags, int):
                writing = bool(flags & _WRITE_FLAGS)
            check(path, writing=writing)
            return
        if event in _WRITE_EVENTS:
            for index in _WRITE_EVENTS[event]:
                if index < len(args):
                    check(_as_path(args[index]), writing=True)
            return
        if event in _READ_EVENTS:
            for index in _READ_EVENTS[event]:
                if index < len(args):
                    check(_as_path(args[index]), writing=False)
            return
        if event in _COPY_EVENTS:
            read_at, write_at = _COPY_EVENTS[event]
            if read_at < len(args):
                check(_as_path(args[read_at]), writing=False)
            if write_at < len(args):
                check(_as_path(args[write_at]), writing=True)
            return
        if event in _SPAWN_EVENTS:
            refuse(_OUTSIDE_BLOCKED if isolated else _SPAWN_BLOCKED)
        elif event in _FFI_EVENTS:
            refuse(_FFI_BLOCKED)
        elif isolated:
            if event in _NETWORK_EVENTS or (
                event.startswith("socket.") and event != "socket.gethostname"
            ):
                refuse(_NETWORK_BLOCKED)
            elif event in _OUTSIDE_EVENTS:
                refuse(_OUTSIDE_BLOCKED)
            elif event.startswith("sqlite3."):
                refuse(_SQLITE_BLOCKED)
            elif event.startswith("winreg."):
                refuse(_REGISTRY_BLOCKED)
            elif event == "import" and args:
                name = str(args[0])
                top = name.partition(".")[0]
                if top in _NO_IMPORT:
                    refuse(_OUTSIDE_BLOCKED)
                # A NATIVE module being loaded from a file (the event then
                # carries the file): every native module of the standard
                # library has a one-word name, so a dotted one is somebody
                # else's code loaded under a borrowed name ("json.etree").
                native = len(args) > 1 and args[1] is not None
                if _STDLIB and (
                    (top not in _STDLIB and top not in _SCRIPT_MODULES)
                    or (native and ("." in name or top not in _STDLIB))
                ):
                    # An ImportError, so a library's own "use it if it is
                    # there" (openpyxl and lxml) falls back as it always does.
                    raise ImportError(_IMPORT_BLOCKED.format(name=name[:80]))

    sys.addaudithook(hook)
    if isolated and os.name == "nt":
        _guard_unaudited(every_root)


def _roots_from_env(name: str) -> tuple[str, ...]:
    raw = os.environ.get(name, "")
    roots: list[str] = []
    for part in raw.split(os.pathsep):
        part = part.strip()
        if not part:
            continue
        try:
            roots.append(_canonical(part))
        except Exception:  # pragma: no cover - unresolvable root, skip it
            continue
    return tuple(dict.fromkeys(roots))


def install_confinement() -> bool:
    """Install the filesystem guardrail from the environment. Returns whether.

    A worker started with no roots configured (a bare `python -m
    iron_jarvis.repl.worker`, or a test) is left completely unrestricted rather
    than being given some invented default — a guardrail that guesses its own
    boundary is one nobody can reason about.
    """
    write_roots = _roots_from_env(ENV_WRITE_ROOTS)
    read_roots = _roots_from_env(ENV_READ_ROOTS)
    deny_roots = _roots_from_env(ENV_DENY_ROOTS)
    isolated = os.environ.get(ENV_ISOLATED, "") == "1"
    if not (write_roots or read_roots or deny_roots):
        return False
    # The working folder alone (the scratch folder holds only what this
    # worker wrote): what the parent handed over to read, minus where it may
    # write. Taken BEFORE the runtime's own folders join the read list.
    private_roots = tuple(r for r in read_roots if r not in write_roots) if isolated else ()
    if isolated:
        # The working folder is the user's (often a client's): a file there
        # named like a library must never be imported in its place, so the
        # folder comes off the import path before any script runs.
        try:
            here = _canonical(os.getcwd())
            sys.path[:] = [p for p in sys.path if p and _canonical(p) != here]
        except Exception:  # pragma: no cover - keep the path as it is
            pass
        # Reads are limited to the roots handed over, so the runtime's own
        # folders are added here (the parent cannot know a frozen build's):
        # without them a later `import statistics` would be refused.
        if read_roots:
            read_roots = tuple(dict.fromkeys(read_roots + _runtime_roots()))
    # Import ctypes BEFORE arming the hook. On Windows `ctypes/__init__.py`
    # evaluates `windll.kernel32.GetLastError` at import time, so a blanket FFI
    # refusal does not merely stop a cell loading a DLL — it makes `import
    # ctypes` itself raise, and with it every library that imports ctypes
    # anywhere in its own import graph. Loading it here means user code finds it
    # already in `sys.modules`, while loading any FURTHER native library is
    # still refused. Honest residual: `windll.kernel32` is cached by that import
    # and stays reachable, so this raises the bar rather than closing the door —
    # which is all an in-process hook can ever claim (see the module docstring).
    try:
        import ctypes  # noqa: F401  (side effect is the point)
    except Exception:  # pragma: no cover - no ctypes in this build
        pass
    shown = " ; ".join(
        part.strip()
        for part in os.environ.get(ENV_WRITE_ROOTS, "").split(os.pathsep)
        if part.strip()
    )
    _install_audit_hook(write_roots, read_roots, deny_roots, shown, isolated, private_roots)
    return True


def _runtime_roots() -> tuple[str, ...]:
    """The folders (and zip files) this interpreter imports from, canonical.

    Read access to them is what lets an isolated script `import` the standard
    library after the hook is armed. Nothing of the user's lives there."""
    raw = [
        sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix,
        getattr(sys, "_MEIPASS", ""), os.path.dirname(sys.executable or ""),
        *sys.path,
    ]
    roots: list[str] = []
    for part in raw:
        if not part:
            continue
        try:
            roots.append(_canonical(part))
        except Exception:  # pragma: no cover - unresolvable entry, skip it
            continue
    return tuple(dict.fromkeys(roots))


def _safe_repr(value: Any) -> str:
    """`repr(value)`, but a broken `__repr__` degrades instead of exploding.

    A half-initialised object whose `__repr__` raises would otherwise turn a
    successful cell into a failed one, which is a lie about what ran.
    """
    try:
        return repr(value)
    except Exception as exc:  # user-defined __repr__, not our bug
        return f"<unreprable {type(value).__name__}: {exc!r}>"


def _cell_filename(cell: int) -> str:
    """The traceback name for one cell, e.g. `<repl>#3`.

    Per-cell rather than one shared `<repl>`, because `linecache` maps a name
    to ONE source: with a single name, a traceback through a function defined
    three cells ago was rendered against the CURRENT cell's text and quoted a
    line that never ran. A wrong line is worse than no line — it is a
    plausible, confident lie about what failed.
    """
    return f"{REPL_FILENAME}#{cell}"


def _register_source(code: str, filename: str) -> None:
    """Teach `linecache` about a cell so tracebacks show the failing line.

    Without this a traceback names `File "<repl>#3", line 3` and prints nothing
    under it, which is exactly the information the caller needs. The `None`
    mtime marks the entry permanently valid so `checkcache` never drops it.
    """
    linecache.cache[filename] = (
        len(code),
        None,
        code.splitlines(keepends=True),
        filename,
    )


def _split_trailing_expression(code: str, filename: str) -> tuple[Any, Any]:
    """Compile `code` into (statements, trailing expression), either may be None.

    IPython-style echoing needs the last expression EVALUATED, not executed, so
    the tree is split before compilation: everything up to the final statement
    is compiled `exec`, and a final `ast.Expr` is recompiled `eval` so its value
    can be captured. Raises `SyntaxError` for unparseable input.
    """
    tree = ast.parse(code, filename=filename, mode="exec")
    body = list(tree.body)
    tail = None
    if body and isinstance(body[-1], ast.Expr):
        tail = body.pop()
    statements = None
    if body:
        module = ast.Module(body=body, type_ignores=list(tree.type_ignores))
        statements = compile(module, filename, "exec")
    expression = None
    if tail is not None:
        expression = compile(ast.Expression(body=tail.value), filename, "eval")
    return statements, expression


def _format_user_traceback(exc: BaseException) -> str:
    """Format `exc` with this module's own frames stripped off the top.

    The first frame is always `Session.execute` calling `exec`; leaving it in
    makes every user error look like a worker bug.
    """
    tb = exc.__traceback__
    while tb is not None and tb.tb_frame.f_code.co_filename == __file__:
        tb = tb.tb_next
    return "".join(traceback.format_exception(type(exc), exc, tb))


class ToolError(Exception):
    """A tool the script called did not run, or failed. The message is the
    app's own sentence (a refusal says why and what to do instead)."""


class ToolValue(dict):
    """What `tools.call` returns: ``{"output": str, "data": ...}``, with
    ``.output`` / ``.data`` for reading. The same shape a `_store_as` value
    has, so a script and a later cell read results one way.

    One of `tools.map`'s results also says whether ITS call worked: ``.ok``,
    ``.error`` (the app's sentence when it did not) and ``.args`` (what that
    call was given). `tools.call` raises instead, so there ``.ok`` is True."""

    @property
    def output(self) -> str:
        return str(self.get("output") or "")

    @property
    def data(self) -> Any:
        return self.get("data")

    @property
    def ok(self) -> bool:
        return bool(self.get("ok", True))

    @property
    def error(self) -> str:
        return str(self.get("error") or "")

    @property
    def args(self) -> Any:
        return self.get("args")

    def __str__(self) -> str:
        return self.output


#: Set once this ISOLATED worker has read a file or listed a folder of the
#: turn's working folder itself (v1.473.0). Every tool call it sends from
#: then on says so, and the app stops such a script reaching outside this PC
#: (``tools/tool_script.py``): client files read here must not leave through
#: an internet tool. Raised by the audit hook, read by :class:`_Tools`; a
#: guardrail like the hook itself, the app's own record of TOOL reads is the
#: half a script cannot touch.
_LOCAL_READ = [False]

#: `tools.map` sends its calls in pieces of this many, so one answer line
#: stays a sensible size and a Stop lands between pieces.
MAP_CHUNK = 32

#: The most characters (as JSON) `tools.keep` hands the app in one value.
MAX_KEEP_CHARS = 8_000_000

#: The name `tools.plan` keeps a script's planned changes under.
PLAN_NAME = "changes"

_KEEP_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")


def _keep_default(value: Any) -> Any:
    """What `tools.keep` makes of a value JSON has no word for. A number
    stays a number: a Decimal that has to be retyped as text is the mistake
    this whole path exists to avoid."""
    import datetime as _dt
    import decimal as _decimal

    if isinstance(value, _decimal.Decimal):
        if not value.is_finite():
            raise ValueError("a number that is not finite (NaN or infinity)")
        whole = value.to_integral_value()
        return int(whole) if value == whole and abs(whole) < 10**15 else float(value)
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    if isinstance(value, os.PathLike):
        return os.fspath(value)
    for method in ("tolist", "item"):  # numpy arrays and numbers
        convert = getattr(value, method, None)
        if callable(convert):
            return convert()
    raise TypeError(f"a {type(value).__name__} cannot be kept")


class _Tools:
    """The `tools` object a tool script sees (v1.470.0).

    ``tools.call("read_document", path="a.pdf")`` — or the short form
    ``tools.read_document(path="a.pdf")`` — sends the call to the app over the
    worker's own private pipe and waits for the answer. THE APP DECIDES: it
    runs the call through the same registry gate as a call the model makes
    itself, and refuses anything this turn was not given or that would need
    the user's approval. A refusal or a failed tool raises :class:`ToolError`.

    PROTOCOL (parent side: ``repl/session.py``)
        child  -> parent  {"id": <request id>, "call": {"n": int, "tool": str,
                           "args": {...}}}
        parent -> child   {"reply": int, "ok": bool, "output": str,
                           "data": any, "error": str}
    One call at a time (a lock), and only while a request that brought a tool
    list is running: a thread that outlives its script cannot call anything.

    v1.473.0, three more things a script can send, each one line and one
    answer like a call:
        {"call": {"n", "batch": [{"tool", "args"}, ...]}}   `tools.map`: the
            app runs them (several at once where that is safe) and answers
            {"reply": n, "ok": true, "results": [{"ok","output","data",
            "error"}, ...]} in the same order;
        {"call": {"n", "keep": {"name", "json", "plan"?}}}   `tools.keep` /
            `tools.plan`: the app holds the value so a file tool can take it
            WITHOUT the model retyping it;
    and every message carries ``"local": 1`` once this worker has read the
    working folder itself (:data:`_LOCAL_READ`).
    """

    def __init__(self) -> None:
        self._source: IO[str] | None = None
        self._sink: IO[str] | None = None
        self._lock = threading.Lock()
        self._request = ""
        self._names: tuple[str, ...] = ()
        self._count = 0
        self._planned: list[dict[str, Any]] = []

    def _attach(self, source: IO[str] | None, sink: IO[str] | None) -> None:
        self._source, self._sink = source, sink

    def _begin(self, request_id: str, names: Any) -> None:
        with self._lock:
            self._request = request_id
            self._names = tuple(str(n) for n in names) if isinstance(names, (list, tuple)) else ()
            self._count = 0
            self._planned = []

    def _end(self) -> None:
        with self._lock:
            self._request = ""
            self._names = ()
            self._planned = []

    @property
    def names(self) -> tuple[str, ...]:
        """The tools this script may call."""
        return self._names

    def _send(self, body: dict[str, Any], what: str) -> dict[str, Any]:
        """One message to the app and its answer. Raises :class:`ToolError`
        when nothing can be sent or the app has gone."""
        with self._lock:
            if not self._request or self._source is None or self._sink is None:
                raise ToolError("no tool script is running, so no tool can be called")
            self._count += 1
            n = self._count
            message = dict(body, n=n)
            if _LOCAL_READ[0]:
                message["local"] = 1
            try:
                line = json.dumps({"id": self._request, "call": message}, ensure_ascii=True)
            except (TypeError, ValueError) as exc:
                raise ToolError(
                    f"the arguments for {what} must be plain values (text, numbers, "
                    f"lists, dicts): {exc}"
                ) from None
            try:
                self._sink.write(line + "\n")
                self._sink.flush()
            except OSError:
                raise ToolError("the app is no longer listening") from None
            while True:
                answer = self._source.readline()
                if not answer:
                    raise ToolError("the app closed the connection before it answered")
                try:
                    msg = json.loads(answer)
                except Exception:  # noqa: BLE001 — not ours; keep waiting
                    continue
                if isinstance(msg, dict) and msg.get("reply") == n:
                    return msg

    def call(self, tool: str, /, **args: Any) -> ToolValue:
        name = str(tool or "").strip()
        if not name:
            raise ToolError("tools.call needs a tool name")
        msg = self._send({"tool": name, "args": args}, name)
        if not msg.get("ok"):
            raise ToolError(str(msg.get("error") or f"{name} failed"))
        return ToolValue(output=str(msg.get("output") or ""), data=msg.get("data"))

    def map(self, tool: str, items: Any, /, arg: str = "", **common: Any) -> list[ToolValue]:
        """The same tool once per item, several at a time: ``tools.map(
        "read_document", [{"path": p} for p in paths])``, or ``tools.map(
        "read_document", paths, arg="path")``. Returns one result per item,
        in order. A call that fails does NOT stop the others and does not
        raise: check ``r.ok`` and read ``r.error``."""
        name = str(tool or "").strip()
        if not name:
            raise ToolError("tools.map needs a tool name")
        if isinstance(items, (str, bytes, dict)) or not hasattr(items, "__iter__"):
            raise ToolError(
                "tools.map needs a LIST of items: [{'path': p} for p in paths], "
                "or a list of values with arg='path'"
            )
        rows: list[dict[str, Any]] = []
        for item in items:
            if isinstance(item, dict):
                rows.append({**common, **{str(k): v for k, v in item.items()}})
            elif arg:
                rows.append({**common, str(arg): item})
            else:
                raise ToolError(
                    "tools.map: each item must be a dict of that call's arguments "
                    "({'path': p}), or pass arg='path' with a list of values"
                )
        out: list[ToolValue] = []
        for start in range(0, len(rows), MAP_CHUNK):
            piece = rows[start:start + MAP_CHUNK]
            msg = self._send({"batch": [{"tool": name, "args": a} for a in piece]}, name)
            if not msg.get("ok"):
                raise ToolError(str(msg.get("error") or f"{name} failed"))
            answers = msg.get("results")
            answers = answers if isinstance(answers, list) else []
            for i, args in enumerate(piece):
                one = answers[i] if i < len(answers) and isinstance(answers[i], dict) else {}
                worked = bool(one.get("ok"))
                out.append(ToolValue(
                    ok=worked,
                    output=str(one.get("output") or "") if worked else "",
                    data=one.get("data") if worked else None,
                    error="" if worked else str(one.get("error") or f"{name} gave no answer"),
                    args=args,
                ))
        return out

    def keep(self, name: str, value: Any, /) -> str:
        """Hand *value* to the app under *name*, so a file tool can take it
        WITHOUT you retyping it: after the script, call the tool directly
        with ``_from_script: {"rows": "<name>"}``. Plain data only (lists,
        dicts, text, numbers; dates become ISO text)."""
        return self._keep(name, value, plan=False)

    def _keep(self, name: Any, value: Any, *, plan: bool) -> str:
        label = str(name or "").strip()
        if not _KEEP_NAME.match(label):
            raise ToolError(
                "tools.keep needs a short plain name (letters, digits, _), "
                "like tools.keep('totals', rows)"
            )
        try:
            text = json.dumps(value, default=_keep_default, allow_nan=False, ensure_ascii=True)
        except (TypeError, ValueError, RecursionError, OverflowError) as exc:
            raise ToolError(
                f"tools.keep('{label}', ...): the value must be plain data (lists, "
                f"dicts, text, finite numbers): {exc}"
            ) from None
        if len(text) > MAX_KEEP_CHARS:
            raise ToolError(
                f"tools.keep('{label}', ...): that is {len(text):,} characters; the "
                f"most one kept value may hold is {MAX_KEEP_CHARS:,}. Keep less "
                "(the rows that matter), or keep it in parts"
            )
        body: dict[str, Any] = {"keep": {"name": label, "json": text}}
        if plan:
            body["keep"]["plan"] = 1
        msg = self._send(body, "tools.keep")
        if not msg.get("ok"):
            raise ToolError(str(msg.get("error") or f"'{label}' could not be kept"))
        return str(msg.get("output") or f"kept '{label}'")

    def plan(self, tool: str, /, **args: Any) -> int:
        """Note ONE change for later instead of making it now: ``tools.plan(
        "rename_file", path=p, new_name=n)``. NOTHING runs. When the script
        ends the list is kept as ``changes``; then call `apply_changes`
        directly with ``_from_script: {"changes": "changes"}`` and the user
        approves all of it once. Returns how many changes are planned."""
        name = str(tool or "").strip()
        if not name:
            raise ToolError("tools.plan needs a tool name")
        try:
            json.dumps(args, default=_keep_default, allow_nan=False)
        except (TypeError, ValueError, RecursionError, OverflowError) as exc:
            raise ToolError(
                f"tools.plan('{name}', ...): the arguments must be plain values: {exc}"
            ) from None
        with self._lock:
            if not self._request:
                raise ToolError("no tool script is running, so nothing can be planned")
            self._planned.append({"tool": name, "args": args})
            return len(self._planned)

    def _flush_plan(self) -> str:
        """Keep what `tools.plan` collected (called once, when the script
        ends well). "" when nothing was planned."""
        with self._lock:
            planned, self._planned = self._planned, []
        if not planned:
            return ""
        return self._keep(PLAN_NAME, planned, plan=True)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)

        def _call(**args: Any) -> ToolValue:
            return self.call(name, **args)

        _call.__name__ = name
        return _call

    def __repr__(self) -> str:
        return f"<tools: {', '.join(self._names) or 'none'}>"


class Session:
    """One persistent namespace. Construct once per worker process.

    `globals` is handed to `exec`/`eval` unchanged on every request, which is
    what makes `x = 5` in one request and `x + 1` in the next work. Recreating
    it — or copying it defensively — silently turns the session back into a
    string of one-shot scripts.
    """

    def __init__(self) -> None:
        self.globals: dict[str, Any] = {
            "__name__": "__repl__",
            "__builtins__": builtins,
            "__doc__": None,
            "__package__": None,
        }
        self._cell = 0
        self._remembered: deque[str] = deque()
        #: The bridge to the app's tools (v1.470.0). `serve` attaches the
        #: private pipes; a request that brings a tool list binds it as
        #: `tools` for that script.
        self.tools = _Tools()

    def _remember_source(self, code: str, filename: str) -> None:
        """Register this cell's source and evict the oldest past the cap."""
        _register_source(code, filename)
        self._remembered.append(filename)
        while len(self._remembered) > MAX_REMEMBERED_CELLS:
            linecache.cache.pop(self._remembered.popleft(), None)

    def execute(
        self,
        code: str,
        *,
        request_id: str = "",
        tools: Any = None,
        variables: Any = None,
    ) -> dict[str, Any]:
        """Run `code` in the session namespace; return the response body.

        The returned dict has every protocol field except `id`, which belongs
        to the request and is attached by `handle_request`.

        ``tools`` (a list of names) makes this a TOOL SCRIPT: `tools` and
        `ToolError` are bound in the namespace and the script may call those
        tools through the bridge until it ends.

        ``variables`` (v1.473.0, a dict of plain names) are bound in the
        namespace before the code runs: a SAVED script's ``params``.
        """
        scripted = isinstance(tools, (list, tuple))
        if scripted:
            self.globals["tools"] = self.tools
            self.globals["ToolError"] = ToolError
            self.tools._begin(request_id, tools)
        if isinstance(variables, dict):
            for key, value in variables.items():
                if isinstance(key, str) and _KEEP_NAME.match(key) and not key.startswith("__"):
                    self.globals[key] = value
        try:
            response = self._execute(code)
            if scripted and response.get("ok"):
                # What `tools.plan` collected is handed over only when the
                # script ended well: half a plan is not a plan.
                try:
                    self.tools._flush_plan()
                except ToolError as exc:
                    response["ok"] = False
                    response["error"] = f"ToolError: {exc}\n"
            return response
        finally:
            if scripted:
                self.tools._end()

    def _execute(self, code: str) -> dict[str, Any]:
        self._cell += 1
        filename = _cell_filename(self._cell)
        try:
            statements, expression = _split_trailing_expression(code, filename)
        except Exception as exc:
            # SyntaxError is the everyday case, but compilation has other ways
            # to refuse input a model can produce: a NUL byte raises ValueError
            # on some versions, deeply nested source raises RecursionError or
            # MemoryError. None of them may reach the read loop, because losing
            # the namespace over an unparseable cell is the failure this whole
            # branch exists to prevent. Nothing ran, so there is no partial
            # output; a full traceback would be worker frames plus one line.
            return {
                "ok": False,
                "stdout": "",
                "stderr": "",
                "result": "",
                "error": "".join(traceback.format_exception_only(type(exc), exc)),
                "truncated": False,
            }

        self._remember_source(code, filename)
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        ok = True
        error = ""
        result = ""
        try:
            with redirect_stdout(out_buf), redirect_stderr(err_buf):
                if statements is not None:
                    exec(statements, self.globals)
                if expression is not None:
                    value = eval(expression, self.globals)
                    # IPython convention: a `None` result is not echoed.
                    if value is not None:
                        result = _safe_repr(value)
        except Exception as exc:
            ok = False
            error = _format_user_traceback(exc)
        except SystemExit as exc:
            # `exit()` is how models end pasted scripts. Honouring it would
            # kill the interpreter holding the namespace, and the parent would
            # never receive a response at all. Report it; stay alive.
            ok = False
            error = _format_user_traceback(exc) + _SYSTEM_EXIT_NOTE
        except KeyboardInterrupt as exc:
            # Same reasoning, and the shape any future "interrupt this cell"
            # will take: the interrupt ends the CELL, never the session.
            ok = False
            error = _format_user_traceback(exc) + _INTERRUPT_NOTE

        raw = _take_raw_output()
        stdout, stderr, cut_streams = _cap_output(
            out_buf.getvalue() + raw, err_buf.getvalue()
        )
        result, cut_result = _clip(result, MAX_RESULT_CHARS, "result")
        return {
            "ok": ok,
            "stdout": stdout,
            "stderr": stderr,
            "result": result,
            "error": error,
            "truncated": cut_streams or cut_result,
        }


def _failure(request_id: str, message: str) -> dict[str, Any]:
    """A well-formed response for input we could not run at all."""
    return {
        "id": request_id,
        "ok": False,
        "stdout": "",
        "stderr": "",
        "result": "",
        "error": message,
        "truncated": False,
    }


def handle_request(session: Session, line: str) -> dict[str, Any]:
    """Turn one input line into one response dict. Never raises for bad input.

    Correlation is best-effort: an unparseable line has no `id` to echo, so the
    response carries `""` and the parent has to match it by arrival order. That
    is strictly better than staying silent and leaving the parent blocked on a
    read forever, which is how this kind of bug usually presents.
    """
    try:
        payload = json.loads(line)
    except Exception as exc:
        # Not just ValueError/TypeError: a deeply nested line makes `json`
        # raise RecursionError, which used to escape this guard and take the
        # process — the exact "one garbled line costs the namespace" outcome
        # the docstring above promises cannot happen.
        return _failure("", f"malformed request: not valid JSON ({exc})")
    if not isinstance(payload, dict):
        return _failure(
            "", f"malformed request: expected a JSON object, got {type(payload).__name__}"
        )
    raw_id = payload.get("id", "")
    request_id = raw_id if isinstance(raw_id, str) else str(raw_id)
    code = payload.get("code")
    if not isinstance(code, str):
        kind = type(code).__name__ if code is not None else "missing"
        return _failure(
            request_id, f"malformed request: 'code' must be a string ({kind})"
        )
    tools = payload.get("tools")
    variables = payload.get("vars")
    if isinstance(tools, list):
        if isinstance(variables, dict):
            response = session.execute(
                code, request_id=request_id, tools=tools, variables=variables
            )
        else:
            response = session.execute(code, request_id=request_id, tools=tools)
    else:
        response = session.execute(code)
    response["id"] = request_id
    return response


def serve(
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
    session: Session | None = None,
) -> int:
    """Read requests until EOF, writing one JSON line per request. Returns 0.

    The output handle is captured ONCE, up front, and used directly for every
    response. Responses must not travel through `sys.stdout`: user code is free
    to rebind it (or to still be inside our own `redirect_stdout` if this ever
    grows a streaming mode), and a single stray `print` landing in the protocol
    stream desynchronises the parser for the rest of the session. `main` goes
    further and hands in handles user code cannot reach at all.
    """
    source = sys.stdin if stdin is None else stdin
    sink = sys.stdout if stdout is None else stdout
    state = Session() if session is None else session
    bridge = getattr(state, "tools", None)
    if bridge is not None:
        bridge._attach(source, sink)
    while True:
        line = source.readline()
        if not line:
            break  # EOF: the parent closed the pipe. Exit cleanly.
        if not line.strip():
            continue  # Bare newlines are framing, not a malformed request.
        try:
            response = handle_request(state, line)
        except Exception as exc:  # last line of defence, never reached by design
            # A bug in OUR request handling must still cost one request rather
            # than the session: the namespace is the only thing here that
            # cannot be rebuilt.
            response = _failure(
                "",
                "the worker could not handle this request "
                f"({type(exc).__name__}: {exc})",
            )
        # ensure_ascii keeps the wire pure ASCII, so a console codepage on
        # Windows can never fail to encode a response mid-protocol.
        try:
            sink.write(json.dumps(response, ensure_ascii=True) + "\n")
            sink.flush()
        except OSError:
            break  # The parent is gone. Leave quietly; there is no one to tell.
    return 0


def main(argv: list[str] | None = None) -> int:
    """Process entry point: `python <path to worker.py>` or `-m`."""
    del argv  # accepted for symmetry with other entry points; nothing to parse
    for stream in (sys.stdin, sys.stdout):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    # Take the protocol private BEFORE any user code can run. On the fallback
    # path (no real descriptors) `serve` keeps using sys.stdin/sys.stdout, which
    # is the old, exposed behaviour — running is better than refusing to start.
    source, sink = _isolate_protocol_streams()
    # AFTER isolation (which legitimately opens devnull and moves descriptors)
    # and BEFORE `serve` reads its first request, so no cell can ever run in an
    # unguarded interpreter. Once added, an audit hook cannot be removed.
    install_confinement()
    return serve(stdin=source, stdout=sink)


if __name__ == "__main__":
    raise SystemExit(main())
