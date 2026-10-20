"""v1.342.0 ("the pack says why"), lane A-stderr: the stdio transport KEEPS the
child's stderr and puts it where the model and the Tools page can read it.

Before this, ``StdioTransport`` spawned every pack with ``stderr=DEVNULL``, so
a pack that died at start saying "BRAVE_API_KEY is not set" reached the user as
"the MCP server closed the connection (exit code 1)" and nothing else. Now a
daemon reader thread drains stderr into a bounded ring (40 lines / 8 KB / a
line cut at 1,000 chars), ``StdioTransport.stderr_tail()`` hands it out MASKED
and cut to 2,000 chars, ``MCPClient.stderr_tail()`` reaches it (an HTTP or
fake transport answers ""), and the EOF / broken-pipe ``MCPServerStopped``
carries "; its last output: <tail>" plus the fixed Settings sentence.

Every child here is REAL (``tests/fixtures/stdio_stderr_server_v1342.py``);
only the server is fake. Waits are liveness guards (a bounded poll on the
state asserted), never performance bars.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import psutil
import pytest

from iron_jarvis.mcp import client as client_mod
from iron_jarvis.mcp.client import (
    SETTINGS_FIX_SENTENCE,
    STDERR_ERROR_MAX,
    STDERR_LINE_MAX,
    STDERR_RING_BYTES,
    STDERR_RING_LINES,
    STDERR_TAIL_MAX,
    FakeTransport,
    HttpTransport,
    MCPClient,
    MCPServerStopped,
    StdioTransport,
    _StderrRing,
)
from iron_jarvis.mcp.tools import MCPRemoteTool

FIXTURES = Path(__file__).parent / "fixtures"
SERVER = str(FIXTURES / "stdio_stderr_server_v1342.py")
sys.path.insert(0, str(FIXTURES))
from stdio_stderr_server_v1342 import FATAL_LINE, PLANTED  # noqa: E402

#: A hang guard for polls and joins, never a performance bar.
_WAIT_S = 20.0

#: Every secret VALUE the fixture plants; none may leave the transport.
_SECRET_VALUES = ("abc123", "sk-live-secretvalue")


# --------------------------------------------------------------------------- #
# Helpers.
# --------------------------------------------------------------------------- #
def _transport(*args: str, **kw) -> StdioTransport:
    return StdioTransport(sys.executable, [SERVER, *args], **kw)


def _wait_until(pred, what: str) -> None:
    deadline = time.monotonic() + _WAIT_S
    while not pred():
        assert time.monotonic() < deadline, f"gave up waiting for {what}"
        time.sleep(0.02)


def _alive(pid: int) -> bool:
    try:
        proc = psutil.Process(pid)
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def _err_reader_alive(pid: int) -> bool:
    return any(
        th.is_alive() and th.name == f"mcp-stderr-{pid}" for th in threading.enumerate()
    )


def _assert_masked(text: str) -> None:
    for value in _SECRET_VALUES:
        assert value not in text, f"secret value {value!r} leaked: {text!r}"
    assert "Authorization: Bearer ***" in text, text
    assert "BRAVE_API_KEY=***" in text, text


# --------------------------------------------------------------------------- #
# The ring: bounded three ways (pure, no child).
# --------------------------------------------------------------------------- #
def test_the_ring_keeps_the_last_40_lines():
    ring = _StderrRing()
    for i in range(200):
        ring.append(f"line {i}\n")
    kept = ring.lines()
    assert len(kept) == STDERR_RING_LINES == 40
    assert kept[0] == "line 160" and kept[-1] == "line 199"


def test_the_ring_is_bounded_by_bytes_oldest_first():
    ring = _StderrRing()
    for i in range(30):
        ring.append(f"{i:02d}" + "x" * 498)  # 500 bytes each, 15,000 in all
    assert ring.byte_size() <= STDERR_RING_BYTES == 8 * 1024
    kept = ring.lines()
    assert len(kept) < 30, "nothing was dropped for the byte bound"
    assert kept[-1].startswith("29"), "the NEWEST line must be kept"
    assert not any(ln.startswith("00") for ln in kept), "the oldest must go first"


def test_a_line_longer_than_1000_chars_is_cut():
    ring = _StderrRing()
    ring.append("y" * 5000)
    (only,) = ring.lines()
    assert len(only) == STDERR_LINE_MAX == 1000


def test_the_text_is_masked_and_cut_to_2000_chars():
    ring = _StderrRing()
    for _ in range(5):
        ring.append("Authorization: Bearer abc123 " + "z" * 900)
    text = ring.text()
    assert len(text) <= STDERR_TAIL_MAX == 2000
    assert "abc123" not in text
    assert text.endswith("z" * 100), "the tail keeps the NEWEST characters"
    assert _StderrRing().text() == "", "nothing kept = an empty string, never None"


# --------------------------------------------------------------------------- #
# A real child that dies at start: the error says why, masked.
# --------------------------------------------------------------------------- #
def test_a_pack_that_dies_at_start_says_why_in_the_error_masked():
    transport = _transport("--mode", "die")
    try:
        with pytest.raises(MCPServerStopped) as exc:
            transport.request("tools/list")
    finally:
        transport.close()
    msg = str(exc.value)
    # The words that stood here before v1.342.0, byte for byte, then the tail.
    assert msg.startswith(
        "the MCP server closed the connection (exit code 1); its last output: "
    ), msg
    assert "the pack needs a key (BRAVE_API_KEY is not set)" in msg, msg
    assert "; it will be restarted on the next call. " in msg, msg
    assert msg.endswith(SETTINGS_FIX_SENTENCE), msg
    assert "Settings → Tools" in SETTINGS_FIX_SENTENCE
    _assert_masked(msg)
    # The ring outlives the server: a load that failed is closed before anyone
    # asks why, and the answer must still be there.
    assert transport.pid is None
    tail = transport.stderr_tail()
    assert tail, "stderr_tail() is empty after the child printed its reason"
    assert "the pack needs a key" in tail
    _assert_masked(tail)
    # The shape of every planted line stays (the reader still sees WHAT was
    # sent); only the values go. PLANTED is the fixture's own list, read here
    # so a fixture edit cannot silently weaken the pin.
    assert len(PLANTED) == 3 and tail.count("\n") == 2, tail


def test_200_stderr_lines_keep_only_the_last_40_through_a_real_child():
    transport = _transport("--mode", "die", "--lines", "200")
    try:
        with pytest.raises(MCPServerStopped):
            transport.request("tools/list")
    finally:
        transport.close()
    lines = transport.stderr_tail().splitlines()
    assert 0 < len(lines) <= 40, len(lines)
    assert lines[-1] == "line 199"
    assert "line 160" in lines and "line 159" not in lines, lines[:3]


def test_a_5000_char_stderr_line_is_cut_and_the_error_clause_is_bounded():
    transport = _transport("--mode", "long-line")
    try:
        with pytest.raises(MCPServerStopped) as exc:
            transport.request("tools/list")
    finally:
        transport.close()
    tail = transport.stderr_tail()
    assert tail == "x" * STDERR_LINE_MAX
    msg = str(exc.value)
    clause = msg.split("; its last output: ", 1)[1].split("; it will be restarted", 1)[0]
    assert clause == "x" * STDERR_ERROR_MAX, len(clause)


# --------------------------------------------------------------------------- #
# Mid-call death: the tail reaches the MODEL through the client and the tool.
# --------------------------------------------------------------------------- #
def test_the_tail_reaches_the_model_through_the_client_and_the_tool():
    transport = _transport("--mode", "after-init")
    client = MCPClient(transport, name="brave")
    tool = MCPRemoteTool(client, "brave", "search")
    try:
        res = asyncio.run(tool.execute({"q": "x"}, None))
    finally:
        transport.close()
    assert res.ok is False
    err = res.error or ""
    assert "extension 'brave': the MCP server closed the connection (exit code 2)" in err, err
    assert "; its last output: " in err and FATAL_LINE in err, err
    assert err.rstrip().endswith(SETTINGS_FIX_SENTENCE), err
    _assert_masked(err)
    # The whole tail (start-up lines included) is one readable line in the error ...
    assert "\n" not in err.split("its last output: ", 1)[1].split("; it will be", 1)[0]
    # ... and the client reaches the transport's tail for the load record.
    tail = client.stderr_tail()
    assert FATAL_LINE in tail and "the pack needs a key" in tail
    _assert_masked(tail)


def test_a_broken_pipe_carries_the_tail():
    """The child is killed while it waits for a request and its stdin is
    closed under the transport (the first thing ``_kill`` does to a server);
    the next WRITE fails in ``_write``'s ``except (OSError, ValueError)`` —
    the broken-pipe / EINVAL / closed-file branch — and that error carries
    the lines the child printed before it died.

    Closing stdin ourselves makes the failure DETERMINISTIC: measured here,
    a write into a killed fixture's pipe sometimes still lands on Windows
    (the kernel releases the read end a moment after ``wait`` returns), and
    a pin that only sometimes reaches the branch pins nothing."""
    transport = _transport("--mode", "after-init")
    try:
        transport.request_timeout = None
        transport._ensure_started()  # the handshake; the child then parks on stdin
        _wait_until(lambda: "the pack needs a key" in transport.stderr_tail(),
                    "the start-up lines to reach the ring")
        proc = transport._proc
        assert proc is not None and proc.stdin is not None
        proc.kill()
        proc.wait(timeout=_WAIT_S)
        proc.stdin.close()
        with pytest.raises(MCPServerStopped) as exc:
            transport._write({"jsonrpc": "2.0", "id": 99, "method": "ping"})
    finally:
        transport.close()
    msg = str(exc.value)
    assert msg.startswith("the MCP server stopped ("), msg
    assert "); its last output: " in msg, msg
    assert "the pack needs a key" in msg, msg
    assert "; it will be restarted on the next call. " in msg, msg
    assert msg.endswith(SETTINGS_FIX_SENTENCE), msg
    _assert_masked(msg)
    assert transport.pid is None


# --------------------------------------------------------------------------- #
# Liveness: a child that only talks to stderr never blocks close().
# --------------------------------------------------------------------------- #
def test_a_chattering_child_does_not_block_the_transports_own_deadline():
    """The product path: the child never answers, the transport's deadline
    kills it. The reader is a DAEMON thread, the call ends well inside the
    hang guard although the child would chatter for 120 s, the child is
    dead, the reader thread ends with the pipe, and the tail is kept."""
    transport = _transport("--mode", "chatter", request_timeout=0.5)
    box: dict = {}

    def work():
        try:
            transport.request("tools/list")
        except BaseException as exc:  # noqa: BLE001 — surfaced below
            box["exc"] = exc

    th = threading.Thread(target=work)
    th.start()
    _wait_until(lambda: transport.pid is not None or not th.is_alive(), "the spawn")
    reader = transport._err_reader
    pid = transport.pid
    if reader is not None:
        assert reader.daemon, "the stderr reader must not keep the process alive"
    th.join(_WAIT_S)
    try:
        assert not th.is_alive(), "close() was blocked by the chattering child"
        assert isinstance(box.get("exc"), MCPServerStopped), box.get("exc")
        assert "did not answer within 0.5 s" in str(box["exc"])
        assert transport.pid is None
        if pid is not None:
            _wait_until(lambda: not _alive(pid), "the chattering child to die")
            _wait_until(lambda: not _err_reader_alive(pid), "the stderr reader to end")
        assert "still here" in transport.stderr_tail()
    finally:
        transport.close()


def test_close_returns_while_a_deadline_less_call_is_parked_on_the_chatterer():
    """The abort shape: a registry pack (no transport deadline) is parked in
    its handshake read while the child chatters; ``close()`` from another
    thread must return and unpark the worker. A 120 s child against a 20 s
    guard: a close that waited for the stderr reader to finish would fail."""
    transport = _transport("--mode", "chatter", request_timeout=None)
    box: dict = {}

    def work():
        try:
            transport.request("tools/list")
        except BaseException as exc:  # noqa: BLE001 — surfaced below
            box["exc"] = exc

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    _wait_until(lambda: transport.pid is not None, "the spawn")
    _wait_until(lambda: "still here" in transport.stderr_tail(), "stderr to reach the ring")
    pid = transport.pid
    assert pid is not None and _err_reader_alive(pid)

    closer = threading.Thread(target=transport.close, daemon=True)
    closer.start()
    closer.join(_WAIT_S)
    assert not closer.is_alive(), "close() did not return within the guard"
    worker.join(_WAIT_S)
    assert not worker.is_alive(), "the parked worker was not unparked by close()"
    assert isinstance(box.get("exc"), MCPServerStopped), box.get("exc")
    _wait_until(lambda: not _alive(pid), "the chattering child to die")
    _wait_until(lambda: not _err_reader_alive(pid), "the stderr reader to end")
    assert transport.pid is None
    assert "still here" in transport.stderr_tail(), "the ring must outlive close()"


def test_close_never_waits_for_a_stderr_reader_that_does_not_end():
    """The parked case the live children cannot show: a tree kill that missed
    a grandchild leaves the stderr reader parked on a pipe that stays open.
    ``_close_stderr`` must still RETURN (bounded join) and must leave the
    stream to the reader — closing a buffered pipe under a parked ``readline``
    hangs the closer on the buffer lock (the stdout rule in ``_kill``)."""
    gate = threading.Event()
    parked = threading.Thread(target=gate.wait, daemon=True)
    parked.start()

    class _Stream:
        closed = False

        def close(self):
            self.closed = True

    proc = types.SimpleNamespace(stderr=_Stream())
    closer = threading.Thread(
        target=StdioTransport._close_stderr, args=(proc, parked), daemon=True
    )
    closer.start()
    closer.join(_WAIT_S)
    try:
        assert not closer.is_alive(), "_close_stderr waited for a reader that never ends"
        assert not proc.stderr.closed, "the stream under a parked reader must be left to it"
    finally:
        gate.set()
    # And once the reader HAS ended, the stream is closed.
    parked.join(_WAIT_S)
    StdioTransport._close_stderr(proc, parked)
    assert proc.stderr.closed


# --------------------------------------------------------------------------- #
# The client-level accessor: "" where there is no stderr.
# --------------------------------------------------------------------------- #
def test_an_http_or_fake_transport_answers_an_empty_tail():
    assert MCPClient(HttpTransport("http://127.0.0.1:1/mcp"), name="h").stderr_tail() == ""
    assert MCPClient(FakeTransport(), name="f").stderr_tail() == ""
    assert MCPClient(StdioTransport(sys.executable, [SERVER]), name="s").stderr_tail() == ""

    class _Boom:
        def request(self, *a, **k):  # pragma: no cover - never called
            return {}

        def stderr_tail(self):
            raise RuntimeError("no")

    assert MCPClient(_Boom(), name="b").stderr_tail() == "", "a fault reads as nothing"


def test_the_child_is_spawned_with_stderr_piped_not_discarded(monkeypatch):
    """The source-of-truth pin: the spawn must hand stderr to a PIPE. A
    revert to DEVNULL keeps every other test's child quiet and could pass a
    weak assertion on an empty tail; this one reads the Popen call itself."""
    seen: dict = {}
    real_popen = subprocess.Popen

    def spy(*a, **kw):
        seen["stderr"] = kw.get("stderr")
        return real_popen(*a, **kw)

    monkeypatch.setattr(client_mod.subprocess, "Popen", spy)
    transport = _transport("--mode", "die")
    try:
        with pytest.raises(MCPServerStopped):
            transport.request("tools/list")
    finally:
        transport.close()
    assert seen["stderr"] == subprocess.PIPE
