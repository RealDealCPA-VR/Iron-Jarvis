"""v1.195.0 (finding 7) — neither chat lane may stat the user's folder on the loop.

Both lanes resolve the turn's tool workspace before the model is called:
``Path(ws).is_absolute()/is_dir()``, ``fs_path_allowed``, ``is_protected_path``
(each of which ``resolve()``s, and ``_within`` may stat every ancestor) and a
``mkdir``. All of it used to run inline in the ``async def``.

``body.workspace_dir`` is a folder the USER picked — routinely a network share
or an unhydrated OneDrive path, where a single stat stalls for as long as the OS
takes. On the daemon's one event loop that does not look like a slow turn, it
looks like "Daemon offline" (v1.153.1's documented failure shape: the fetch
times out, ``lib/api.ts`` maps a dead fetch to status 0, Retry lands another
request on the same blocked loop).

HONEST SCOPE: this was never measured on real hardware — on a local SSD the
stats are sub-millisecond. So nothing here asserts a duration. Following
``tests/test_event_loop_offload_v1175.py``, each test asserts the OFFLOAD, in
both halves, so deleting it cannot leave the test green:

  * STRUCTURAL — the stat ran with NO running event loop, i.e. on a worker
    thread. (``asyncio.get_running_loop()`` succeeding inside the probe is the
    proof it ran ON the loop; it is the portable form of "not the main thread",
    which cannot be used here because the app is driven by an ASGI task whose
    loop thread is the test's own.)
  * BEHAVIOURAL — the loop kept servicing other work while the stat stalled.

The probe patches ``Path.is_dir`` rather than the policy helpers on purpose:
it is the call BOTH the old and the new code make, so the mutation check
measures the offload and not an incidental refactor.

The two lanes are LOCK-STEP (CLAUDE.md: the ``/chat/stream`` mirror in
``routes/chat.py`` and ``chat_turn.py`` are edited together or not at all), so
every assertion below is driven through BOTH.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import threading
from pathlib import Path

from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.base import ToolResult

#: How long the faked share-stat stalls, and how often the watcher ticks. The
#: stall must dwarf the tick so an offloaded resolution yields many ticks while
#: an inline one yields none. Same shape as test_event_loop_offload_v1175.
_BLOCK_S = 0.30
_TICK_S = 0.01
_MIN_TICKS = 5


def _scope(path: str, body: bytes) -> dict:
    """A LOOPBACK-host POST scope — v1.175.0's DNS-rebinding guard rejects
    anything else, in tests exactly as in production. Copied from
    ``tests/test_chat_stream_cancel_ledger_v1192.py``."""
    return {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"127.0.0.1:8787"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        "server": ("127.0.0.1", 8787), "client": ("127.0.0.1", 51234),
    }


async def _post(app, path: str, body: dict) -> tuple[int, str]:
    """Drive one request straight at the ASGI app, in THIS test's event loop.

    A ``TestClient`` runs the app on its own thread with its own loop, which
    would make both halves of the assertion meaningless — the stall would be
    invisible from here and there would be no loop of ours to starve.
    """
    raw = json.dumps(body).encode()
    sent = {"done": False}

    async def receive():
        if not sent["done"]:
            sent["done"] = True
            return {"type": "http.request", "body": raw, "more_body": False}
        await asyncio.sleep(3600)  # the client never disconnects here
        return {"type": "http.disconnect"}

    status = {"code": 0}
    chunks: list[bytes] = []

    async def send(msg):
        if msg["type"] == "http.response.start":
            status["code"] = msg["status"]
        elif msg["type"] == "http.response.body":
            chunks.append(msg.get("body", b""))

    await asyncio.wait_for(app(_scope(path, raw), receive, send), timeout=30)
    return status["code"], b"".join(chunks).decode("utf-8", "replace")


async def _ticks_during(coro):
    """Await *coro* while counting how many times the event loop got control.

    Verbatim in shape from ``tests/test_event_loop_offload_v1175.py``: an inline
    blocking call starves the ticker and returns ~0; an offloaded one lets it
    run throughout.
    """
    ticks = 0
    stop = False

    async def _ticker():
        nonlocal ticks
        while not stop:
            await asyncio.sleep(_TICK_S)
            ticks += 1

    task = asyncio.ensure_future(_ticker())
    try:
        result = await coro
    finally:
        stop = True
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    return result, ticks


class _SlowShare:
    """Stands in for the user's network share / unhydrated OneDrive folder.

    Only THIS directory stalls; every other ``is_dir()`` in the app (config,
    skills discovery, the uploads scratch dir) keeps the real implementation,
    so the probe measures the workspace resolution and nothing else.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls = 0
        self.on_loop: list[bool] = []
        self.threads: list[str] = []

    def install(self, monkeypatch) -> None:
        real = pathlib.Path.is_dir
        target = str(self.root)
        probe = self

        def _slow_is_dir(self_path, *a, **kw):  # noqa: ANN001
            if str(self_path) != target:
                return real(self_path, *a, **kw)
            probe.calls += 1
            try:
                asyncio.get_running_loop()
                probe.on_loop.append(True)   # ran ON the daemon's event loop
            except RuntimeError:
                probe.on_loop.append(False)  # ran on a worker thread
            probe.threads.append(threading.current_thread().name)
            import time

            time.sleep(_BLOCK_S)
            return real(self_path, *a, **kw)

        monkeypatch.setattr(pathlib.Path, "is_dir", _slow_is_dir)


def _quiet_router(app) -> None:
    """One deterministic, offline round — the turn must reach the workspace
    block and then get out of the way. Tool arming is what makes the block run;
    the tool itself never has to execute."""

    async def _complete(**kwargs):
        return RouteResult(LLMResponse(text="ok"), "mock", "mock")

    async def _stream(**kwargs):
        yield {"type": "text", "text": "ok"}
        yield {
            "type": "final",
            "response": LLMResponse(text="ok"),
            "provider": "mock", "model": "mock", "requested": "", "reason": "mock",
        }

    app.state.platform.router.complete = _complete
    app.state.platform.router.stream = _stream

    async def _invoke(name, args, ctx, permissions, overrides=None, *,
                      session_allow=None, **kw):
        return ToolResult(ok=True, output="ok")

    app.state.platform.registry.invoke = _invoke


def _body(share: Path) -> dict:
    return {
        "messages": [{"role": "user", "content": "list my files"}],
        "tools": ["read_file"],
        "auto_tools": False,
        "workspace_dir": str(share),
    }


async def test_non_stream_lane_resolves_the_workspace_off_the_loop(
    tmp_path, monkeypatch
):
    """POST /chat — chat_turn.run_chat_turn."""
    app = create_app(str(tmp_path))
    _quiet_router(app)
    share = tmp_path / "share"
    share.mkdir()
    probe = _SlowShare(share)
    probe.install(monkeypatch)

    (status, _text), ticks = await _ticks_during(_post(app, "/chat", _body(share)))

    assert status == 200
    assert probe.calls >= 1, "the workspace folder was never stat'ed at all"
    # STRUCTURAL: no running loop inside the stat => it ran on a worker thread.
    assert not any(probe.on_loop), (
        "the user's folder was stat'ed ON the event loop "
        f"(threads: {probe.threads})"
    )
    assert all(t != threading.main_thread().name for t in probe.threads)
    # BEHAVIOURAL: the loop kept serving other work throughout the stall.
    assert ticks >= _MIN_TICKS, f"event loop was starved (only {ticks} ticks)"


async def test_stream_lane_resolves_the_workspace_off_the_loop(
    tmp_path, monkeypatch
):
    """POST /chat/stream — the lock-step mirror, and the lane the user watches
    while the app decides whether to look offline."""
    app = create_app(str(tmp_path))
    _quiet_router(app)
    share = tmp_path / "share"
    share.mkdir()
    probe = _SlowShare(share)
    probe.install(monkeypatch)

    (status, text), ticks = await _ticks_during(
        _post(app, "/chat/stream", _body(share))
    )

    assert status == 200
    assert "event: done" in text or "\"type\"" in text or text, text[:200]
    assert probe.calls >= 1, "the workspace folder was never stat'ed at all"
    assert not any(probe.on_loop), (
        "the user's folder was stat'ed ON the event loop "
        f"(threads: {probe.threads})"
    )
    assert all(t != threading.main_thread().name for t in probe.threads)
    assert ticks >= _MIN_TICKS, f"event loop was starved (only {ticks} ticks)"


async def test_offload_preserves_the_workspace_decision(tmp_path):
    """The thread hop must not change WHICH folder wins.

    Precedence is explicit pick > grounded project root > the uploads scratch
    dir, a refused folder falls back to the scratch dir, and the chosen folder
    is created. Asserted directly on the shared helper both lanes call, so a
    "simplification" of the hop cannot quietly move a turn's files.
    """
    from iron_jarvis.daemon.chat_turn import _resolve_tool_workspace

    default_ws = tmp_path / "uploads"
    picked = tmp_path / "picked"
    picked.mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()

    ws, in_project = await asyncio.to_thread(
        _resolve_tool_workspace, default_ws, str(picked), str(proj)
    )
    assert (ws, in_project) == (picked, True), "the explicit pick must win"

    ws, in_project = await asyncio.to_thread(
        _resolve_tool_workspace, default_ws, "", str(proj)
    )
    assert (ws, in_project) == (proj, True), "the project root is the fallback"

    # A folder that does not exist is not a workspace — the turn quietly runs in
    # the scratch dir rather than failing, exactly as it did inline.
    ws, in_project = await asyncio.to_thread(
        _resolve_tool_workspace, default_ws, str(tmp_path / "gone"), str(proj)
    )
    assert (ws, in_project) == (default_ws, False)
    assert default_ws.is_dir(), "the chosen workspace must be created"

    # A RELATIVE pick is refused (fs_policy.usable_workspace_root requires an
    # absolute path) — and, per the precedence, does NOT fall through to the
    # project root: an explicit pick that is present but unusable is not the
    # same request as no pick at all.
    ws, in_project = await asyncio.to_thread(
        _resolve_tool_workspace, default_ws, "relative/dir", str(proj)
    )
    assert (ws, in_project) == (default_ws, False)
