"""Drive `repl/worker.py` as a REAL subprocess, the way the parent will.

Every test here spawns an interpreter and talks newline-delimited JSON down a
pipe. Importing the worker and calling `Session.execute` in-process would be
faster and would prove almost nothing: the whole contract is about a separate
process — that the namespace survives BETWEEN messages, that a traceback comes
back instead of a dead pipe, that a garbled line does not take the interpreter
with it, that EOF exits 0. None of those failure modes exist in-process.

The bootstrap loads worker.py BY PATH under a throwaway module name, so the
`iron_jarvis` package is never imported in the child at all. That is deliberate
double duty: it is how the frozen build will spawn it, and it means an
accidental `from iron_jarvis import ...` in the worker shows up here as a real
failure rather than passing on the strength of the dev virtualenv.
"""

from __future__ import annotations

import ast
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

WORKER_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "iron_jarvis"
    / "repl"
    / "worker.py"
)

# Import by file location under a name that is not an `iron_jarvis` submodule:
# nothing about the package is on the child's import path.
_BOOTSTRAP = (
    "import importlib.util, sys;"
    "spec = importlib.util.spec_from_file_location('_ij_repl_worker', r'{path}');"
    "mod = importlib.util.module_from_spec(spec);"
    "sys.modules['_ij_repl_worker'] = mod;"
    "spec.loader.exec_module(mod);"
    "raise SystemExit(mod.main())"
)

TIMEOUT = 60.0


class WorkerProc:
    """A spawned worker plus a reader thread, so no test can hang forever."""

    def __init__(self) -> None:
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        env["PYTHONIOENCODING"] = "utf-8"
        self.proc = subprocess.Popen(
            [sys.executable, "-c", _BOOTSTRAP.format(path=str(WORKER_PATH))],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=env,
        )
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()
        self._counter = 0

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self._lines.put(line)
        self._lines.put(None)

    def send_raw(self, line: str) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(line)
        self.proc.stdin.flush()

    def read_raw(self, timeout: float = TIMEOUT) -> str:
        """The next line off the pipe, undecoded — desync is visible here.

        Tests that ask "did user code corrupt the framing?" must look at the
        bytes the parent would actually read; `read_response` parses them and
        so would report a desync as a JSON error somewhere else entirely.
        """
        try:
            line = self._lines.get(timeout=timeout)
        except queue.Empty:  # pragma: no cover - only on a real hang
            pytest.fail(f"worker produced no response within {timeout}s")
        if line is None:  # pragma: no cover - only when the worker died
            pytest.fail(
                "worker exited instead of answering; stderr:\n" + self.drain_stderr()
            )
        return line

    def read_response(self, timeout: float = TIMEOUT) -> dict:
        line = self.read_raw(timeout)
        try:
            return json.loads(line)
        except ValueError:  # pragma: no cover - fails loudly on desync
            pytest.fail(
                "the protocol stream carried a line that is not a response: "
                f"{line[:200]!r}"
            )

    def send(self, code: str, request_id: str) -> None:
        self.send_raw(json.dumps({"id": request_id, "code": code}) + "\n")

    def run(self, code: str, request_id: str | None = None) -> dict:
        self._counter += 1
        rid = request_id if request_id is not None else f"r{self._counter}"
        self.send(code, rid)
        response = self.read_response()
        assert response["id"] == rid, f"id mismatch: {response['id']!r} != {rid!r}"
        return response

    def pending_lines(self) -> int:
        """How many lines are sitting unread on the protocol stream."""
        return self._lines.qsize()

    def close_stdin(self) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.close()

    def drain_stderr(self) -> str:
        assert self.proc.stderr is not None
        try:
            return self.proc.stderr.read() or ""
        except ValueError:  # pragma: no cover - stream already closed
            return ""

    def terminate(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(timeout=TIMEOUT)


@pytest.fixture()
def worker():
    proc = WorkerProc()
    try:
        yield proc
    finally:
        proc.terminate()


def _envelope_keys() -> set[str]:
    return {"id", "ok", "stdout", "stderr", "result", "error", "truncated"}


def test_response_carries_the_whole_envelope(worker: WorkerProc) -> None:
    """The parent destructures every field; a missing key is a parent crash."""
    response = worker.run("1 + 1")
    assert set(response) == _envelope_keys()
    assert response["ok"] is True
    assert response["truncated"] is False


def test_state_persists_across_requests(worker: WorkerProc) -> None:
    """The point of the whole feature: one namespace, many requests."""
    first = worker.run("x = 5")
    assert first["ok"] is True, first
    second = worker.run("x + 1")
    assert second["ok"] is True, second
    assert second["result"] == "6"


def test_state_persists_for_imports_and_functions(worker: WorkerProc) -> None:
    """Not just names: modules and defs bound in one request stay bound."""
    worker.run("import math\ndef area(r):\n    return math.pi * r * r")
    response = worker.run("round(area(2), 4)")
    assert response["ok"] is True, response
    assert response["result"] == "12.5664"


def test_trailing_expression_fills_result(worker: WorkerProc) -> None:
    response = worker.run("a = 2\nb = 3\na * b")
    assert response["ok"] is True, response
    assert response["result"] == "6"


def test_trailing_statement_leaves_result_empty(worker: WorkerProc) -> None:
    """An assignment is not an expression, so there is nothing to echo."""
    response = worker.run("a = 2\nb = a * 3")
    assert response["ok"] is True, response
    assert response["result"] == ""


def test_trailing_none_expression_is_not_echoed(worker: WorkerProc) -> None:
    """IPython convention: a `None` value is silence, not the text 'None'."""
    response = worker.run("def f():\n    pass\nf()")
    assert response["ok"] is True, response
    assert response["result"] == ""


def test_print_output_arrives_in_stdout(worker: WorkerProc) -> None:
    response = worker.run("print('hello from the namespace')")
    assert response["ok"] is True, response
    assert "hello from the namespace" in response["stdout"]
    assert response["stderr"] == ""
    # The protocol stream must stay clean JSON — the print must not have
    # escaped onto the pipe as a bare line.
    assert response["result"] == ""


def test_stderr_is_captured_separately(worker: WorkerProc) -> None:
    response = worker.run("import sys\nsys.stderr.write('warned\\n')\n41 + 1")
    assert response["ok"] is True, response
    assert "warned" in response["stderr"]
    assert "warned" not in response["stdout"]
    assert response["result"] == "42"


def test_exception_returns_traceback_and_worker_survives(worker: WorkerProc) -> None:
    """The important one: a raise must not cost the user the session.

    Three claims at once — the failure is reported as a failure, the traceback
    is real enough to debug from, and the SAME process still answers afterwards
    with the state it had before the crash.
    """
    worker.run("keep_me = 'still here'")
    boom = worker.run("def go():\n    raise ValueError('kaboom')\ngo()")
    assert boom["ok"] is False, boom
    assert "ValueError" in boom["error"]
    assert "kaboom" in boom["error"]
    assert "Traceback" in boom["error"]
    assert "<repl>" in boom["error"]
    assert "go" in boom["error"]

    survivor = worker.run("keep_me")
    assert survivor["ok"] is True, survivor
    assert survivor["result"] == "'still here'"
    assert worker.proc.poll() is None


def test_partial_output_survives_an_exception(worker: WorkerProc) -> None:
    """Output produced before the raise is usually the whole diagnosis."""
    response = worker.run("print('step one')\nraise RuntimeError('later')")
    assert response["ok"] is False, response
    assert "step one" in response["stdout"]
    assert "RuntimeError" in response["error"]


def test_syntax_error_is_reported_and_worker_survives(worker: WorkerProc) -> None:
    response = worker.run("def broken(:\n    pass")
    assert response["ok"] is False, response
    assert "SyntaxError" in response["error"]
    assert worker.run("1 + 1")["result"] == "2"


def test_huge_stdout_is_truncated_with_a_visible_marker(worker: WorkerProc) -> None:
    """Context flooding is the failure this cap exists to prevent."""
    from iron_jarvis.repl.worker import MAX_OUTPUT_CHARS

    produced = 40000
    response = worker.run(f"print('x' * {produced})")
    assert response["ok"] is True, response
    assert response["truncated"] is True
    assert len(response["stdout"]) < MAX_OUTPUT_CHARS + 1000
    assert "TRUNCATED" in response["stdout"]
    # The marker has to state the damage, not merely hint at it.
    assert str(produced - MAX_OUTPUT_CHARS + 1) in response["stdout"]
    assert str(MAX_OUTPUT_CHARS) in response["stdout"]


def test_many_printed_lines_are_truncated(worker: WorkerProc) -> None:
    """The realistic shape of the bug: a loop, not one enormous string."""
    response = worker.run("for i in range(20000):\n    print('line', i, 'x' * 40)")
    assert response["ok"] is True, response
    assert response["truncated"] is True
    assert "TRUNCATED" in response["stdout"]
    # Head and tail both survive; only the middle is dropped.
    assert response["stdout"].startswith("line 0 ")
    assert response["stdout"].rstrip().endswith("x" * 40)


def test_output_under_the_cap_is_untouched(worker: WorkerProc) -> None:
    """The cap must not fire on ordinary output — that would be its own lie."""
    response = worker.run("print('a' * 100)")
    assert response["truncated"] is False
    assert response["stdout"] == "a" * 100 + "\n"
    assert "TRUNCATED" not in response["stdout"]


def test_huge_result_repr_is_truncated(worker: WorkerProc) -> None:
    """A giant list's repr must never come back whole."""
    from iron_jarvis.repl.worker import MAX_RESULT_CHARS

    response = worker.run("list(range(400000))")
    assert response["ok"] is True, response
    assert response["truncated"] is True
    assert len(response["result"]) < MAX_RESULT_CHARS + 1000
    assert "TRUNCATED" in response["result"]
    assert response["result"].startswith("[0, 1, 2,")


def test_malformed_line_does_not_kill_the_worker(worker: WorkerProc) -> None:
    """A garbled line costs one request, never the accumulated namespace."""
    worker.run("marker = 'alive'")

    worker.send_raw("this is not json at all\n")
    bad = worker.read_response()
    assert bad["ok"] is False, bad
    assert bad["error"]
    assert "JSON" in bad["error"]
    assert set(bad) == _envelope_keys()

    still = worker.run("marker")
    assert still["result"] == "'alive'"
    assert worker.proc.poll() is None


@pytest.mark.parametrize(
    "line",
    [
        "[1, 2, 3]",  # valid JSON, wrong shape
        '"just a string"',
        '{"id": "n1"}',  # no code
        '{"id": "n2", "code": 42}',  # code is not source
        "{not: valid",
    ],
)
def test_every_bad_request_shape_answers_instead_of_hanging(
    worker: WorkerProc, line: str
) -> None:
    worker.send_raw(line + "\n")
    response = worker.read_response()
    assert response["ok"] is False, response
    assert "malformed request" in response["error"]
    assert worker.run("'ok'")["result"] == "'ok'"


def test_blank_lines_are_framing_not_requests(worker: WorkerProc) -> None:
    """A stray newline must not desynchronise the parent's response pairing."""
    worker.send_raw("\n\n")
    response = worker.run("7")
    assert response["result"] == "7"


def test_eof_exits_cleanly(worker: WorkerProc) -> None:
    worker.run("x = 1")
    worker.close_stdin()
    assert worker.proc.wait(timeout=TIMEOUT) == 0
    assert worker.drain_stderr() == ""


def test_worker_imports_only_the_standard_library() -> None:
    """Static proof of the standalone rule the frozen build depends on.

    The subprocess tests already load worker.py by path, but the dev
    virtualenv has `iron_jarvis` installed, so an accidental import could
    resolve there and pass. This reads the source instead.
    """
    tree = ast.parse(WORKER_PATH.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                pytest.fail("worker.py must not use relative imports")
            assert node.module is not None
            roots.add(node.module.split(".")[0])
    assert "iron_jarvis" not in roots
    non_stdlib = roots - set(sys.stdlib_module_names)
    assert not non_stdlib, f"worker.py must be stdlib-only, found: {sorted(non_stdlib)}"


# ---------------------------------------------------------------------------
# Protocol integrity: user code owns the interpreter, so it can reach the same
# file descriptors the protocol runs on. Every test below started life as a
# reproduction of a real desync, not as a hypothetical.
# ---------------------------------------------------------------------------


def test_raw_writes_to_fd_1_cannot_corrupt_the_protocol(worker: WorkerProc) -> None:
    """`os.write(1, ...)` bypasses `redirect_stdout` entirely.

    `redirect_stdout` only rebinds the `sys.stdout` OBJECT; the file descriptor
    underneath is still the pipe the parent parses. A single raw write used to
    put a bare line on that pipe, and from then on every response the parent
    read belonged to the previous request.
    """
    worker.send("import os\nos.write(1, b'RAW-BYTES-ON-THE-WIRE\\n')", "raw1")
    line = worker.read_raw()
    assert line.lstrip().startswith("{"), f"non-response line on the pipe: {line!r}"
    response = json.loads(line)
    assert response["id"] == "raw1"
    # Not lost, either: bytes the cell really wrote come back, marked as having
    # arrived out of band rather than silently dropped.
    assert "RAW-BYTES-ON-THE-WIRE" in response["stdout"]


def test_subprocess_output_does_not_corrupt_the_protocol(worker: WorkerProc) -> None:
    """The realistic shape of the same bug: a child process inherits fd 1.

    A model in a REPL runs `subprocess.run([...])` constantly. The grandchild
    writes to the inherited descriptor, which was the protocol pipe — so a
    plain `git status` desynchronised the session. It must be captured and
    reported instead.
    """
    worker.send(
        "import subprocess, sys\n"
        "subprocess.run([sys.executable, '-c', \"print('CHILD-SPOKE')\"])\n"
        "'done'",
        "sub1",
    )
    line = worker.read_raw()
    assert line.lstrip().startswith("{"), f"non-response line on the pipe: {line!r}"
    response = json.loads(line)
    assert response["id"] == "sub1"
    assert response["result"] == "'done'"
    assert "CHILD-SPOKE" in response["stdout"]


def test_out_of_band_output_that_overflows_its_buffer_says_so(
    worker: WorkerProc,
) -> None:
    """The capture buffer is bounded, so its overflow is a truncation too.

    Same rule as every other cap in this file: a shortened stream that does not
    say it was shortened is read as complete, and the reader draws a confident
    wrong conclusion from it.
    """
    response = worker.run(
        "import subprocess, sys\n"
        "subprocess.run([sys.executable, '-c', \"print('z' * 300000)\"])\n"
        "'done'",
        "ovf1",
    )
    assert response["ok"] is True, response
    assert response["result"] == "'done'"
    assert response["truncated"] is True, response
    assert "raw output TRUNCATED" in response["stdout"], response["stdout"][:400]


def test_the_saved_stdout_object_is_not_the_protocol_stream(
    worker: WorkerProc,
) -> None:
    """`sys.__stdout__` survives `redirect_stdout` and used to BE the pipe."""
    worker.send(
        "import sys\nsys.__stdout__.write('DUNDER-BYTES\\n')\nsys.__stdout__.flush()",
        "dun1",
    )
    line = worker.read_raw()
    assert line.lstrip().startswith("{"), f"non-response line on the pipe: {line!r}"
    assert json.loads(line)["id"] == "dun1"


def test_a_thread_printing_after_the_response_cannot_corrupt_it(
    worker: WorkerProc,
) -> None:
    """`redirect_stdout` is undone the moment the cell returns.

    A background thread started by one cell prints AFTER that, straight onto
    whatever `sys.stdout` is then — which was the protocol pipe, mid-response.
    """
    first = worker.run(
        "import threading, time\n"
        "def later():\n    time.sleep(0.5)\n    print('LATE-THREAD')\n"
        "threading.Thread(target=later, daemon=True).start()\n"
        "'started'",
        "th1",
    )
    assert first["result"] == "'started'"
    time.sleep(1.2)  # let the thread fire while nothing is being written
    assert worker.pending_lines() == 0, "a stray line reached the protocol stream"
    second = worker.run("2 + 2", "th2")
    assert second["result"] == "4"
    # Captured, not discarded: the late line rides along with the next answer.
    assert "LATE-THREAD" in second["stdout"], second


def test_input_cannot_steal_the_next_request(worker: WorkerProc) -> None:
    """`input()` used to read the PROTOCOL stream and hang the session.

    fd 0 is the request pipe. A cell calling `input()` blocked until the parent
    sent its next request, ate that JSON line as the user's "input", and left
    that request unanswered forever — the worst failure this protocol has, and
    one line of ordinary-looking code away.
    """
    worker.send("value = input()\nvalue", "in1")
    worker.send("'second request'", "in2")
    first = worker.read_response(timeout=20.0)
    assert first["id"] == "in1", f"the first request was answered with {first!r}"
    assert first["ok"] is False, first
    assert "EOF" in first["error"] or "EOFError" in first["error"], first
    second = worker.read_response(timeout=20.0)
    assert second["id"] == "in2", second
    assert second["result"] == "'second request'"


def test_sys_exit_reports_instead_of_destroying_the_session(
    worker: WorkerProc,
) -> None:
    """`exit()` is how models end scripts; it must not end the namespace.

    Letting `SystemExit` propagate killed the worker mid-request: the parent
    got NO response at all, waited out its timeout, then reported the code as
    still running. One `sys.exit(0)` cost the whole session and lied about why.
    """
    worker.run("keep_me = 'survivor'", "x0")
    response = worker.run("import sys\nsys.exit(0)", "x1")
    assert response["ok"] is False, response
    assert "SystemExit" in response["error"], response
    assert worker.proc.poll() is None, "the worker died on sys.exit()"
    assert worker.run("keep_me", "x2")["result"] == "'survivor'"


def test_keyboard_interrupt_in_user_code_is_reported_not_fatal(
    worker: WorkerProc,
) -> None:
    """Same argument, and the shape a future 'interrupt this cell' will take."""
    response = worker.run("raise KeyboardInterrupt()", "k1")
    assert response["ok"] is False, response
    assert "KeyboardInterrupt" in response["error"], response
    assert worker.proc.poll() is None
    assert worker.run("'alive'", "k2")["result"] == "'alive'"


def test_pathological_json_line_does_not_kill_the_worker(worker: WorkerProc) -> None:
    """`json.loads` raises RecursionError, which is neither ValueError nor TypeError.

    The module promises that a malformed line costs one request and never the
    namespace. Deep nesting escaped the only two exception types the parser
    guarded against and took the process with it.
    """
    worker.run("still_here = 1", "p0")
    worker.send_raw("[" * 20000 + "]" * 20000 + "\n")
    response = worker.read_response()
    assert response["ok"] is False, response
    assert response["error"]
    assert set(response) == _envelope_keys()
    assert worker.run("still_here", "p1")["result"] == "1"


def test_traceback_points_at_the_cell_that_actually_failed(
    worker: WorkerProc,
) -> None:
    """A traceback that quotes the WRONG line is worse than quoting none.

    Every cell was registered in `linecache` under the same `<repl>` name, so
    the source shown for a function defined earlier was whatever the CURRENT
    cell happened to have on that line number. A model debugging it is handed
    a line that never ran.
    """
    worker.run("def go():\n    raise ValueError('from the first cell')\n", "t1")
    boom = worker.run("z = 1\nz = 2\nz = 3\nz = 4\nz = 5\ngo()", "t2")
    assert boom["ok"] is False, boom
    assert "ValueError: from the first cell" in boom["error"]
    assert "raise ValueError('from the first cell')" in boom["error"], boom["error"]
    # The decoy line from the failing cell must NOT be presented as the raise.
    assert "z = 2" not in boom["error"], boom["error"]
    assert "go()" in boom["error"], boom["error"]


def test_remembered_cell_sources_are_bounded(worker: WorkerProc) -> None:
    """Per-cell source registration must not become a per-session leak.

    Keeping every cell's text alive so tracebacks can quote it is exactly the
    kind of "silently retain everything forever" this process cannot afford —
    it is long-lived by design.
    """
    from iron_jarvis.repl.worker import MAX_REMEMBERED_CELLS

    for i in range(MAX_REMEMBERED_CELLS + 40):
        worker.run(f"{i} + 0", f"c{i}")
    entries = worker.run("import linecache\nlen(linecache.cache)", "cN")
    assert int(entries["result"]) <= MAX_REMEMBERED_CELLS + 5, entries


def test_the_namespace_does_not_accumulate_per_request_state(
    worker: WorkerProc,
) -> None:
    """The globals dict is the user's; nothing of ours may pile up in it."""
    worker.run("import sys", "n0")
    before = int(worker.run("len(globals())", "n1")["result"])
    for i in range(60):
        worker.run(f"{i} * 3", f"n{i + 2}")
    after = int(worker.run("len(globals())", "nz")["result"])
    assert after == before, f"globals grew from {before} to {after}"


def test_unicode_output_survives_the_json_round_trip(worker: WorkerProc) -> None:
    """Windows console codepages must never be able to break the protocol.

    Responses go out `ensure_ascii=True` for exactly this reason: an emoji, a
    CRLF, or a lone surrogate that cannot be encoded would otherwise raise
    mid-write and leave half a response on the wire.
    """
    response = worker.run(
        "print('emoji \\U0001F600 done\\r\\nsecond line')\n'\\U0001F600'", "u1"
    )
    assert response["ok"] is True, response
    assert "\U0001f600" in response["stdout"]
    assert "second line" in response["stdout"]
    assert response["result"] == "'\U0001f600'"

    lone = worker.run("print('\\ud800')\n'ok'", "u2")
    assert lone["ok"] is True, lone
    assert lone["result"] == "'ok'"
    # Escaped on the wire, so it arrives EXACTLY as produced. Encoding it as
    # UTF-8 instead would either raise mid-response or quietly swap in U+FFFD,
    # and a REPL that edits the user's output is a REPL that cannot be trusted.
    assert lone["stdout"] == "\ud800\n", lone
    assert worker.proc.poll() is None


def test_an_enormous_result_is_capped_before_it_reaches_the_wire(
    worker: WorkerProc,
) -> None:
    """Capping after serialisation would mean a 40 MB JSON line was built first."""
    from iron_jarvis.repl.worker import MAX_RESULT_CHARS

    worker.send("blob = 'y' * 40_000_000\nblob", "big1")
    line = worker.read_raw(timeout=120.0)
    assert len(line) < MAX_RESULT_CHARS + 5000, f"wire line was {len(line)} chars"
    response = json.loads(line)
    assert response["ok"] is True, response
    assert response["truncated"] is True
    assert "TRUNCATED" in response["result"]
