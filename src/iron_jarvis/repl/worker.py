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

    def _remember_source(self, code: str, filename: str) -> None:
        """Register this cell's source and evict the oldest past the cap."""
        _register_source(code, filename)
        self._remembered.append(filename)
        while len(self._remembered) > MAX_REMEMBERED_CELLS:
            linecache.cache.pop(self._remembered.popleft(), None)

    def execute(self, code: str) -> dict[str, Any]:
        """Run `code` in the session namespace; return the response body.

        The returned dict has every protocol field except `id`, which belongs
        to the request and is attached by `handle_request`.
        """
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
    return serve(stdin=source, stdout=sink)


if __name__ == "__main__":
    raise SystemExit(main())
