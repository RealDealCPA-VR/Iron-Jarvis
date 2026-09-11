"""v1.248.0 (B2, daemon half) — the terminal data path is event-driven,
byte-exact, and flow-controlled.

What the 2026-09-11 Build audit measured, and what each test here pins:

- BYTES, NOT TEXT. pywinpty hands its output over as ``str`` and re-encodes
  it, so a multi-byte character split across two reads became U+FFFD — 121
  per 8.46 MB of mixed output. ``ConPtyBackend`` talks to kernel32's ConPTY
  directly (ctypes, no new native dependency) and never decodes anything.
- EVENT-DRIVEN. Output was POLLED: every attached pane's pump woke 100 times
  a second, and every unattended pane had a drain thread sleeping 30-50 ms
  between reads. The ConPTY backend's own blocking reader thread now hands
  each chunk to ``TerminalSession`` the moment it exists, through the same
  single path ``read()`` uses (``_record_locked``: lock, tail, output_seq,
  fan-out, the v1.245 DA1 answer). An idle pane costs nothing.
- FLOW CONTROL. A pane whose renderer falls behind says
  ``{"type":"flow","paused":true}``; the daemon stops SENDING that attach's
  output (it keeps queuing, under the same 8 MB bound → 1013 → fresh replay)
  until ``{"type":"flow","paused":false}``. Other attaches of the same PTY are
  untouched, and a malformed flow frame neither closes the socket nor reaches
  the shell.

The route tests drive the REAL app factory with a pushing fake backend; the
parity tests at the bottom spawn real ConPTY children (Windows only).
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time

import anyio
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from iron_jarvis.daemon.app import create_app
from iron_jarvis.terminals import backend as B
from iron_jarvis.terminals.backend import FakeBackend
from iron_jarvis.terminals.manager import TerminalManager, _with_pane_env
from iron_jarvis.terminals.session import (
    ATTACH_BACKLOG_MAX_BYTES,
    OutputSubscription,
    TerminalSession,
)

real_conpty = pytest.mark.skipif(
    sys.platform != "win32" or not B.conpty_available(),
    reason="needs a real Windows ConPTY",
)


@pytest.fixture(params=["bundled", "inbox"])
def conpty_host(request, monkeypatch):
    """Run a parity test on BOTH console hosts: the bundled OpenConsole (the
    default when present) and kernel32's inbox conhost (the fallback)."""
    if request.param == "bundled" and B._bundled_conpty_dir() is None:
        pytest.skip("pywinpty's conpty.dll/OpenConsole.exe are not installed")
    monkeypatch.setenv(B.CONPTY_HOST_ENV, request.param)
    monkeypatch.setattr(B, "_CONPTY_API", None)  # rebuilt for this host
    yield request.param
    B._CONPTY_API = None  # the next test builds its own again


def real_conpty_each_host(fn):
    return real_conpty(pytest.mark.usefixtures("conpty_host")(fn))


@real_conpty
def test_the_bundled_host_is_preferred_and_the_env_switch_forces_inbox(monkeypatch):
    if B._bundled_conpty_dir() is None:
        pytest.skip("pywinpty's conpty.dll/OpenConsole.exe are not installed")
    monkeypatch.delenv(B.CONPTY_HOST_ENV, raising=False)
    monkeypatch.setattr(B, "_CONPTY_API", None)
    assert B._conpty_api().host == "bundled"
    monkeypatch.setattr(B, "_CONPTY_API", None)
    monkeypatch.setenv(B.CONPTY_HOST_ENV, "inbox")
    assert B._conpty_api().host == "inbox"
    B._CONPTY_API = None


def test_no_bundled_host_means_the_inbox_one(monkeypatch, tmp_path):
    """Half a bundle (a frozen build that lost OpenConsole.exe) is no bundle:
    conpty.dll without its host exe would fail every spawn."""

    class _Spec:
        origin = str(tmp_path / "__init__.py")

    (tmp_path / "conpty.dll").write_bytes(b"")
    monkeypatch.setattr("importlib.util.find_spec", lambda name, *a, **kw: _Spec())
    assert B._bundled_conpty_dir() is None
    (tmp_path / "OpenConsole.exe").write_bytes(b"")
    assert B._bundled_conpty_dir() == str(tmp_path)


class PushingFakeBackend(FakeBackend):
    """A FakeBackend that PUSHES its output from another thread, the way
    ``ConPtyBackend``'s reader thread does, and is never polled."""

    pushes_output = True

    def __init__(self) -> None:
        super().__init__()
        self._on_output = None
        self._on_eof = None
        self._eof = threading.Event()

    def set_output_handler(self, on_output, on_eof=None) -> None:
        self._on_output = on_output
        self._on_eof = on_eof

    @property
    def output_finished(self) -> bool:
        return self._eof.is_set()

    def emit(self, data: bytes) -> None:
        """Deliver one chunk from a thread that is not the caller's."""
        t = threading.Thread(target=self._on_output, args=(data,))
        t.start()
        t.join()

    def write(self, data) -> None:
        super().write(data)
        chunk = super().read_nonblocking(1 << 30)
        if chunk and self._on_output is not None:
            self.emit(chunk)

    def read_nonblocking(self, max_bytes: int = 65536) -> bytes:
        return b""  # a pushing backend has nothing to poll

    def kill(self) -> None:
        super().kill()
        self._eof.set()
        if self._on_eof is not None:
            self._on_eof()


class EarlyBannerBackend(PushingFakeBackend):
    """Prints while it is still starting — before a late handler could exist."""

    def start(self, *a, **kw) -> None:
        super().start(*a, **kw)
        self.emit(b"banner\r\n")


def _pushing_session(backend=None) -> TerminalSession:
    return TerminalSession(
        shell="fake", argv=["fake"], backend=backend or PushingFakeBackend()
    ).start()


def _app(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(
        "iron_jarvis.terminals.session.default_backend", PushingFakeBackend
    )
    return TestClient(create_app(str(tmp_path)))


def _recv(ws, timeout: float = 5.0) -> bytes:
    """``receive_bytes`` with a deadline (the test client's own never times out)."""

    async def _get():
        with anyio.fail_after(timeout):
            return await ws._send_rx.receive()

    message = ws.portal.call(_get)
    ws._raise_on_close(message)
    return message["bytes"]


def _until(ws, needle: bytes, frames: int = 100, timeout: float = 5.0) -> bytes:
    got = b""
    for _ in range(frames):
        got += _recv(ws, timeout)
        if needle in got:
            return got
    raise AssertionError(f"{needle!r} never arrived (got {got!r})")


def _wait(pred, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


def _flow(ws, paused) -> None:
    ws.send_text(json.dumps({"type": "flow", "paused": paused}))


# --- one output path, fed by a pushing reader ------------------------------------


def test_a_pushed_chunk_takes_the_one_output_path():
    """No read() anywhere: the reader thread's chunk reaches the pane, the
    tail, output_seq and the output clock through the same code read() uses."""
    b = PushingFakeBackend()
    s = _pushing_session(b)
    try:
        _, sub = s.subscribe()
        seq = s.output_seq
        b.emit(b"hello\r\n")
        assert sub.take() == b"hello\r\n"
        assert b"hello" in s.scrollback_bytes()
        assert s.output_seq == seq + 1
        assert s.last_output_at > 0
        assert s.read() == b""  # there is nothing to poll on a pushing backend
    finally:
        s.kill()


def test_a_pushed_da1_query_is_answered_only_when_no_pane_is_attached():
    b = PushingFakeBackend()
    s = _pushing_session(b)
    written: list = []
    b.write = written.append  # capture what the session types into the PTY
    try:
        b.emit(b"\x1b[c")
        assert written == ["\x1b[?1;2c"]  # nobody attached: the session answers
        written.clear()
        _, sub = s.subscribe()
        b.emit(b"\x1b[c")
        assert written == []  # an attached xterm answers for itself
        s.unsubscribe(sub)
    finally:
        s.kill()


def test_output_printed_while_the_backend_starts_is_not_lost():
    """The handler must be wired BEFORE the backend spawns, or a shell's
    first bytes land on a reader nobody is listening to."""
    s = _pushing_session(EarlyBannerBackend())
    try:
        assert b"banner" in s.scrollback_bytes()
    finally:
        s.kill()


def test_a_pushing_session_needs_no_drain_thread():
    """The reader thread IS the drain: it never stops reading, so a PTY
    nobody watches cannot fill up — and nothing sleeps in a poll loop."""
    s = _pushing_session()
    try:
        s.start_autodrain()
        assert s._drain_thread is None
        _, sub = s.subscribe()
        s.unsubscribe(sub)  # the last pane out used to start the drain
        assert s._drain_thread is None
        s.write("printed while nobody watched\n")
        assert "printed while nobody watched" in s.output_tail()
    finally:
        s.kill()


def test_a_subscription_wakes_its_waker_on_every_push_and_on_eof():
    b = PushingFakeBackend()
    s = _pushing_session(b)
    wakes: list = []
    _, sub = s.subscribe()
    sub.set_waker(lambda: wakes.append(1))
    b.emit(b"x")
    assert len(wakes) == 1
    s.kill()  # EOF must wake too, or a pump sleeps through its shell's exit
    assert len(wakes) >= 2


# --- the route: event-driven pump ------------------------------------------------


def test_an_idle_attach_on_a_pushing_backend_does_not_poll(tmp_path, monkeypatch):
    """The pump used to run `read(); take(); sleep(0.01)` forever — 100
    wakeups a second per idle pane. On a pushing backend it sleeps until the
    reader thread wakes it."""
    calls = {"n": 0}
    real_take = OutputSubscription.take

    def counting_take(self):
        calls["n"] += 1
        return real_take(self)

    monkeypatch.setattr(OutputSubscription, "take", counting_take)
    client = _app(tmp_path, monkeypatch)
    tid = client.post("/terminals", json={}).json()["id"]
    with client.websocket_connect(f"/terminals/{tid}/ws") as ws:
        assert _recv(ws) == b""
        time.sleep(0.1)
        calls["n"] = 0
        time.sleep(1.0)
        idle = calls["n"]
        ws.send_text("still-live\n")
        _until(ws, b"still-live")
    client.delete(f"/terminals/{tid}")
    assert idle <= 3, f"the idle pump took {idle} times in one second"


def test_a_shell_exit_reaches_the_pane_without_waiting_out_a_timer(tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch)
    tid = client.post("/terminals", json={}).json()["id"]
    session = client.app.state.platform.terminals.get(tid)
    with client.websocket_connect(f"/terminals/{tid}/ws") as ws:
        assert _recv(ws) == b""
        session.kill()
        assert b"shell exited" in _until(ws, b"shell exited", timeout=2.0)
        with pytest.raises(WebSocketDisconnect) as closed:
            _recv(ws, 2.0)
        assert closed.value.code == 4000


# --- the route: flow control ------------------------------------------------------


def test_a_paused_attach_is_sent_nothing_until_it_resumes(tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch)
    tid = client.post("/terminals", json={}).json()["id"]
    session = client.app.state.platform.terminals.get(tid)
    with client.websocket_connect(f"/terminals/{tid}/ws") as ws:
        assert _recv(ws) == b""
        _flow(ws, True)
        ws.send_text("held-back\n")  # frames are handled in order: pause first
        sub = session._subscribers[0]
        assert _wait(lambda: bool(sub._chunks))
        time.sleep(0.3)  # the pump's chance to (wrongly) send it
        assert sub._chunks, "a paused attach was sent its output anyway"
        _flow(ws, False)
        # Well inside the pump's 5 s safety net: the resume itself must wake it.
        assert b"held-back" in _until(ws, b"held-back", timeout=2.0)
    client.delete(f"/terminals/{tid}")


def test_pausing_one_attach_leaves_the_other_untouched(tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch)
    tid = client.post("/terminals", json={}).json()["id"]
    session = client.app.state.platform.terminals.get(tid)
    with client.websocket_connect(f"/terminals/{tid}/ws") as a:
        assert _recv(a) == b""
        with client.websocket_connect(f"/terminals/{tid}/ws") as b:
            assert _recv(b) == b""
            _flow(a, True)
            a.send_text("sync-a\n")  # proves a's pause has been handled
            sub_a = session._subscribers[0]
            assert _wait(lambda: bool(sub_a._chunks))
            b.send_text("for-both\n")
            assert b"for-both" in _until(b, b"for-both")  # b is not held
            assert b"for-both" in b"".join(sub_a._chunks)  # a is queued, not sent
            _flow(a, False)
            got = _until(a, b"for-both")
            assert b"sync-a" in got and b"for-both" in got
            assert got.index(b"sync-a") < got.index(b"for-both")  # in order
    client.delete(f"/terminals/{tid}")


def test_a_malformed_flow_frame_is_ignored_not_typed_and_not_a_close(tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch)
    tid = client.post("/terminals", json={}).json()["id"]
    with client.websocket_connect(f"/terminals/{tid}/ws") as ws:
        assert _recv(ws) == b""
        ws.send_text('{"type": "flow"}')
        ws.send_text('{"type": "flow", "paused": "yes"}')
        ws.send_text('{"type": "flow", "paused": 1}')
        ws.send_text("still-open\n")
        got = _until(ws, b"still-open")  # open, and not paused by garbage
        assert b"flow" not in got  # none of it reached the shell
    client.delete(f"/terminals/{tid}")


def test_only_the_exact_flow_frame_is_consumed_everything_else_is_typed(tmp_path, monkeypatch):
    """An older daemon TYPED every non-resize text frame into the shell. Now
    exactly one extra shape is consumed — a JSON object with type "flow" and a
    boolean `paused` (a flow-ish frame with a bad `paused` is dropped too) —
    and everything else is typed byte-for-byte, JSON-looking text included."""
    client = _app(tmp_path, monkeypatch)
    tid = client.post("/terminals", json={}).json()["id"]
    session = client.app.state.platform.terminals.get(tid)
    typed: list = []
    session.write = typed.append  # what the input handler hands the PTY
    user_json = '{"type": "note", "text": "a user pasted this"}'
    with client.websocket_connect(f"/terminals/{tid}/ws") as ws:
        assert _recv(ws) == b""
        _flow(ws, True)
        _flow(ws, False)
        ws.send_text('{"type": "flow", "paused": "no"}')
        ws.send_text(user_json)
        ws.send_text('{"type": "flowchart"}')
        ws.send_text("plain words\r")
        assert _wait(lambda: "plain words\r" in typed)
    assert typed == [user_json, '{"type": "flowchart"}', "plain words\r"]
    client.delete(f"/terminals/{tid}")


def test_a_paused_attach_on_a_polling_backend_keeps_reading(tmp_path, monkeypatch):
    """Hold the SEND, never the READ: on a polling backend (the pywinpty
    fallback) a paused sole attach must still read the PTY, or the program in
    it blocks on a full pipe."""
    monkeypatch.setattr("iron_jarvis.terminals.session.default_backend", FakeBackend)
    client = TestClient(create_app(str(tmp_path)))
    tid = client.post("/terminals", json={}).json()["id"]
    session = client.app.state.platform.terminals.get(tid)
    with client.websocket_connect(f"/terminals/{tid}/ws") as ws:
        assert _recv(ws) == b""
        _flow(ws, True)
        ws.send_text("while-paused\n")
        assert _wait(lambda: "while-paused" in session.output_tail())  # it was READ
        sub = session._subscribers[0]
        assert b"while-paused" in b"".join(sub._chunks)  # ...and held, not sent
        _flow(ws, False)
        assert b"while-paused" in _until(ws, b"while-paused", timeout=2.0)
    client.delete(f"/terminals/{tid}")


def test_a_paused_attach_that_falls_too_far_behind_gets_a_fresh_replay(tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch)
    tid = client.post("/terminals", json={}).json()["id"]
    session = client.app.state.platform.terminals.get(tid)
    with client.websocket_connect(f"/terminals/{tid}/ws") as ws:
        assert _recv(ws) == b""
        _flow(ws, True)
        ws.send_text("sync\n")
        sub = session._subscribers[0]
        assert _wait(lambda: bool(sub._chunks))
        session.backend.emit(b"x" * (ATTACH_BACKLOG_MAX_BYTES + 1))
        with pytest.raises(WebSocketDisconnect) as closed:
            _recv(ws, 5.0)
        assert closed.value.code == 1013  # the pane reconnects to a fresh replay
    client.delete(f"/terminals/{tid}")


# --- backend choice ---------------------------------------------------------------


@real_conpty
def test_windows_uses_raw_conpty_by_default(monkeypatch):
    monkeypatch.delenv("IRONJARVIS_PTY_BACKEND", raising=False)
    assert isinstance(B.default_backend(), B.ConPtyBackend)


@real_conpty
def test_the_env_switch_forces_pywinpty(monkeypatch):
    pytest.importorskip("winpty")
    monkeypatch.setenv("IRONJARVIS_PTY_BACKEND", "pywinpty")
    assert isinstance(B.default_backend(), B.WinPtyBackend)


@real_conpty
def test_a_conpty_that_cannot_start_falls_back_and_stays_off(monkeypatch):
    """A ConPTY spawn that RAISES must not cost the user their pane: the
    manager retries on the next backend (pywinpty, here a FakeBackend stand-in)
    and every later pane skips ConPTY instead of failing the same way."""
    monkeypatch.delenv("IRONJARVIS_PTY_BACKEND", raising=False)
    monkeypatch.setattr(B, "_CONPTY_BROKEN", False)

    def refuse(self, *a, **kw):
        raise B.ConPtyUnavailable("CreatePseudoConsole failed (HRESULT 0x80070005)")

    monkeypatch.setattr(B.ConPtyBackend, "start", refuse)
    monkeypatch.setattr(B, "WinPtyBackend", FakeBackend)
    monkeypatch.setattr("importlib.util.find_spec", lambda name, *a, **kw: object())
    m = TerminalManager()
    s = m.create()
    assert isinstance(s.backend, FakeBackend)
    assert s.degraded is False  # a real TTY backend, not the pipe fallback
    assert B._CONPTY_BROKEN is True
    assert isinstance(B.default_backend(), FakeBackend)
    m.kill_all()


# --- real ConPTY parity (Windows) -------------------------------------------------


class _Sink:
    """Collects a raw ConPtyBackend's pushes; answers DA1 like an xterm would."""

    def __init__(self) -> None:
        self.buf = bytearray()
        self.types: set = set()
        self.eof = threading.Event()
        self.backend = None
        self._lock = threading.Lock()

    def on_output(self, data) -> None:
        self.types.add(type(data))
        with self._lock:
            self.buf += data
        if b"\x1b[c" in data and self.backend is not None:
            self.backend.write("\x1b[?1;2c")

    def text(self) -> str:
        with self._lock:
            return bytes(self.buf).decode("utf-8", "replace")


def _raw(argv, *, cwd=None, env=None, cols=200, rows=50):
    b = B.ConPtyBackend()
    sink = _Sink()
    sink.backend = b
    b.set_output_handler(sink.on_output, sink.eof.set)
    b.start(list(argv), cwd or os.getcwd(), env, cols, rows)
    return b, sink


@real_conpty_each_host
def test_real_conpty_delivers_bytes_and_never_mangles_a_character(tmp_path):
    """~2 MB of box-drawing, CJK and symbols, flushed in bursts: every chunk
    is bytes, and the whole stream decodes with ZERO replacement characters
    (pywinpty measured 121 per 8.46 MB)."""
    child = tmp_path / "bulk.py"
    child.write_text(
        "import sys\n"
        "sys.stdout.reconfigure(encoding='utf-8')\n"
        "line = ('abc \\u2500\\u2502\\u250c\\u2510 \\u23fa \\u273b \\u65e5\\u672c\\u8a9e ' * 8)[:140] + '\\n'\n"
        "for _ in range(60):\n"
        "    sys.stdout.write(line * 150)\n"
        "    sys.stdout.flush()\n",
        encoding="utf-8",
    )
    b, sink = _raw([sys.executable, str(child)])
    try:
        assert sink.eof.wait(90), "the stream never reached EOF after the child exited"
        text = sink.text()
        assert sink.types == {bytes}
        assert len(sink.buf) > 1_000_000
        assert text.count("\u65e5\u672c\u8a9e") > 10_000
        assert text.count("\ufffd") == 0
        assert b.exit_code == 0
        assert b.is_alive() is False
    finally:
        b.kill()


@real_conpty
def test_the_reader_hands_over_raw_bytes_even_mid_character():
    """The reader must deliver EXACTLY the bytes it read, even when a chunk
    ends inside a character — decoding per chunk is precisely how pywinpty
    minted U+FFFD. A real pipe, written in pieces that split two CJK
    characters, stands in for ConPTY's output end."""
    api = B._conpty_api()
    ctypes, w = api.ctypes, api.wintypes
    rd, wr = w.HANDLE(), w.HANDLE()
    assert api.k32.CreatePipe(ctypes.byref(rd), ctypes.byref(wr), None, 0)
    b = B.ConPtyBackend()
    chunks: list = []
    done = threading.Event()
    b.set_output_handler(chunks.append, done.set)
    b._out_read = rd.value
    reader = threading.Thread(target=b._read_loop, daemon=True)
    reader.start()
    n = w.DWORD(0)
    for piece in (b"\xe6\x97", b"\xa5\xe6", b"\x9c\xac"):  # 日本, cut mid-character
        assert api.k32.WriteFile(wr, piece, len(piece), ctypes.byref(n), None)
        time.sleep(0.05)
    api.k32.CloseHandle(wr)
    assert done.wait(5)
    assert b"".join(chunks) == "日本".encode("utf-8")

    def whole(chunk: bytes) -> bool:
        try:
            chunk.decode("utf-8")
            return True
        except UnicodeDecodeError:
            return False

    # Anti-vacuity: at least one delivered chunk really ended mid-character.
    assert not all(whole(c) for c in chunks)


@real_conpty_each_host
def test_real_conpty_child_gets_the_pane_env_cwd_and_path(tmp_path, monkeypatch):
    """The environment the SHELL receives (the v1.217.0 lesson: assert what the
    child got, not what a dict held) — pane identity merged onto the base env,
    PATH intact, the install bearer stripped, and the pane's own folder."""
    monkeypatch.setenv("IRONJARVIS_TOKEN", "install-bearer-must-not-leak")
    child = tmp_path / "who.py"
    child.write_text(
        "import os\n"
        "print('PANE=' + os.environ.get('IRONJARVIS_PANE_ID', '-'))\n"
        "print('CWD=' + os.getcwd())\n"
        "print('PATH=' + str(bool(os.environ.get('PATH'))))\n"
        "print('BEARER=' + os.environ.get('IRONJARVIS_TOKEN', '-'))\n",
        encoding="utf-8",
    )
    env = _with_pane_env(None, {"IRONJARVIS_PANE_ID": "term_parity"})
    s = TerminalSession(
        cwd=str(tmp_path), argv=[sys.executable, str(child)], cols=250, rows=40,
        backend=B.ConPtyBackend(),
    ).start(env=env)
    try:
        assert _wait(lambda: s.backend.output_finished, 30)
        tail = s.output_tail()
        assert "PANE=term_parity" in tail
        assert os.path.normcase(f"CWD={tmp_path}") in os.path.normcase(tail)
        assert "PATH=True" in tail
        assert "BEARER=-" in tail
    finally:
        s.kill()


@real_conpty_each_host
def test_real_conpty_reports_exit_code_and_tolerates_late_calls(tmp_path):
    child = tmp_path / "seven.py"
    child.write_text("import sys\nprint('bye')\nsys.exit(7)\n", encoding="utf-8")
    b, sink = _raw([sys.executable, str(child)])
    assert sink.eof.wait(30)
    assert _wait(lambda: b.exit_code is not None, 10)
    assert b.exit_code == 7
    assert b.is_alive() is False
    b.write("after the end\r")  # a dead PTY swallows input, like pywinpty's EOFError path
    b.resize(90, 20)
    b.kill()
    b.kill()  # twice: the manager kills a closed pane and kill_all kills it again


@real_conpty_each_host
def test_real_conpty_resize_reaches_the_child(tmp_path):
    child = tmp_path / "size.py"
    child.write_text(
        "import os, sys\n"
        "for _ in range(2):\n"
        "    sys.stdin.readline()\n"
        "    c = os.get_terminal_size()\n"
        "    print(f'SIZE {c.columns}x{c.lines}', flush=True)\n",
        encoding="utf-8",
    )
    b, sink = _raw([sys.executable, str(child)], cols=100, rows=30)
    try:
        b.write("\r")
        assert _wait(lambda: "SIZE 100x30" in sink.text(), 20), sink.text()[-400:]
        b.resize(132, 40)
        b.write("\r")
        assert _wait(lambda: "SIZE 132x40" in sink.text(), 20), sink.text()[-400:]
    finally:
        b.kill()


@real_conpty_each_host
def test_real_conpty_kill_takes_the_whole_console_tree(tmp_path):
    """Closing a pane must not orphan what its shell started: a console
    grandchild (a CLI, a build) goes down with it."""
    psutil = pytest.importorskip("psutil")
    child = tmp_path / "tree.py"
    child.write_text(
        "import subprocess, sys, time\n"
        "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        "print(f'GRANDCHILD={g.pid}', flush=True)\n"
        "time.sleep(120)\n",
        encoding="utf-8",
    )
    b, sink = _raw([sys.executable, str(child)])
    assert _wait(lambda: "GRANDCHILD=" in sink.text(), 30), sink.text()[-400:]
    gpid = int(sink.text().split("GRANDCHILD=")[1].split()[0])
    child_pid = b.pid
    b.kill()
    for pid in (child_pid, gpid):
        try:
            psutil.Process(pid).wait(timeout=20)
        except psutil.NoSuchProcess:
            pass
    assert not psutil.pid_exists(gpid) or psutil.Process(gpid).status() == psutil.STATUS_ZOMBIE
    assert sink.eof.wait(20)


@real_conpty
def test_the_studio_guard_sees_a_live_bare_prompt_with_no_pane_attached(tmp_path, monkeypatch):
    """The Studio refuses to type a brief at a bare shell prompt (it would run
    as a shell command). On pywinpty, with no pane attached and no drain,
    NOTHING read the PTY — the tail stayed empty and the guard saw nothing to
    refuse. The reader thread keeps the tail live, so the guard now sees
    exactly what it was built to see."""
    from iron_jarvis.daemon.routes.creative import _PROMPT_AT_END_RE

    monkeypatch.delenv("IRONJARVIS_PTY_BACKEND", raising=False)
    client = TestClient(create_app(str(tmp_path)))
    tid = client.post("/terminals", json={"cwd": str(tmp_path)}).json()["id"]
    session = client.app.state.platform.terminals.get(tid)
    try:
        assert session.pushes_output and not session.has_consumer
        assert _wait(
            lambda: bool(_PROMPT_AT_END_RE.search(session.output_tail()[-300:].rstrip())),
            30,
        ), session.output_tail()[-300:]
        r = client.post(
            f"/creative/studio/{tid}/say", json={"text": "make a video", "first": True}
        )
        assert r.status_code == 409
        assert "bare shell prompt" in r.json()["detail"]
    finally:
        client.delete(f"/terminals/{tid}")


@real_conpty_each_host
def test_real_conpty_session_pushes_without_anyone_reading(tmp_path):
    """Through TerminalSession: the pane's subscription fills with nobody
    calling read() and no drain thread — the reader thread is the only reader."""
    child = tmp_path / "hi.py"
    child.write_text("print('pushed-ok', flush=True)\n", encoding="utf-8")
    s = TerminalSession(
        argv=[sys.executable, str(child)], backend=B.ConPtyBackend()
    )
    _, sub = s.subscribe()
    s.start()
    got = bytearray()
    try:
        def arrived() -> bool:
            got.extend(sub.take())
            if b"\x1b[c" in got and b"answered" not in got:
                s.write("\x1b[?1;2c")  # the attached "pane" answers DA1
                got.extend(b"answered")
            return b"pushed-ok" in got

        assert _wait(arrived, 30), bytes(got)[-300:]
        assert s._drain_thread is None
    finally:
        s.unsubscribe(sub)
        s.kill()
