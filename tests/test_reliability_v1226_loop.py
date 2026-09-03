"""v1.226.0 reliability wave — event-loop liveness (audit items F-C-1..F-C-5).

Five confirmed loop stalls: workflow notify/ask delivery and the notify tool
sent Slack/Telegram/SMTP synchronously on the loop; project-knowledge
grounding embedded the recall query over HTTP inline in BOTH chat lanes and
the agent lane; the agent lane's memory-fabric grounding never got the
v1.173.0 chat offload; the memory-import preview ran up to 60 embed calls
inline; and every run/record SQLite write sat on the loop, so one long
writer (Settings → Compact runs VACUUM) parked the whole daemon, /health
included.

Liveness is asserted with a heartbeat, never a wall-clock threshold (the
``_run_with_heartbeat`` shape from tests/test_event_loop_liveness_v1167.py).
The slow stubs read the SAME tick counter on entry and on exit: an inline
call runs on the loop thread, so the ticker cannot run during it and the
delta is exactly 0; an offloaded call lets it tick freely (≈ block / 10ms).
The floor (>= 3 ticks inside a 0.4s block) is generous so a slow CI runner
cannot flake it — only putting the call back on the loop can fail it. Each
stub also records whether it ran with a running loop (the v1195 structural
half: ``asyncio.get_running_loop()`` succeeding inside the stub is the proof
it ran ON the loop).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time

from fastapi.testclient import TestClient

from iron_jarvis.comm.channels import MockChannel
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.base import ToolContext, ToolResult

_BLOCK_S = 0.4
_TICK_S = 0.01
_MIN_TICKS = 3


class _Ticks:
    """A same-loop ticker whose count the slow stubs read from inside."""

    def __init__(self) -> None:
        self.n = 0
        self._stop = False
        self.windows: list[int] = []   # ticks observed INSIDE each stub call
        self.on_loop: list[bool] = []  # did the stub run with a running loop?

    async def run(self) -> None:
        while not self._stop:
            await asyncio.sleep(_TICK_S)
            self.n += 1

    def stop(self) -> None:
        self._stop = True

    def block(self, seconds: float = _BLOCK_S) -> None:
        """What every slow stub does: note the ticks before/after the stall."""
        try:
            asyncio.get_running_loop()
            self.on_loop.append(True)
        except RuntimeError:
            self.on_loop.append(False)
        before = self.n
        time.sleep(seconds)
        self.windows.append(self.n - before)


async def _under_heartbeat(ticks: _Ticks, coro):
    t = asyncio.ensure_future(ticks.run())
    try:
        return await coro
    finally:
        ticks.stop()
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass


def _assert_offloaded(ticks: _Ticks, what: str) -> None:
    assert ticks.windows, f"{what}: the slow stub never ran"
    assert max(ticks.windows) >= _MIN_TICKS, (
        f"{what}: the loop starved during the call (ticks inside: {ticks.windows}) — "
        "it is back on the event loop"
    )
    assert not any(ticks.on_loop), f"{what}: ran ON the loop thread"


def _scope(method: str, path: str, body: bytes) -> dict:
    return {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http",
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
    """One request straight at the ASGI app, on THIS loop (a TestClient would
    run the app on its own thread + loop, where a stall is invisible)."""
    raw = json.dumps(body).encode()
    sent = {"done": False}

    async def receive():
        if not sent["done"]:
            sent["done"] = True
            return {"type": "http.request", "body": raw, "more_body": False}
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    status = {"code": 0}
    chunks: list[bytes] = []

    async def send(msg):
        if msg["type"] == "http.response.start":
            status["code"] = msg["status"]
        elif msg["type"] == "http.response.body":
            chunks.append(msg.get("body", b""))

    await asyncio.wait_for(app(_scope("POST", path, raw), receive, send), timeout=60)
    return status["code"], b"".join(chunks).decode("utf-8", "replace")


def _quiet_router(app) -> None:
    """One deterministic offline round for both chat lanes."""

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


def _make_project(app, name: str = "p") -> str:
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import Project

    with session_scope(app.state.platform.engine) as db:
        p = Project(name=name, root="")
        db.add(p)
        db.commit()
        db.refresh(p)
        return p.id


# --- F-C-1: workflow notify delivery + the notify tool ------------------------


class _SlowChannel(MockChannel):
    name = "slow"

    def __init__(self, ticks: _Ticks) -> None:
        super().__init__()
        self._ticks = ticks

    def send(self, message: str, **kw):
        self._ticks.block()
        return super().send(message, **kw)


def test_workflow_notify_step_delivers_off_the_loop(tmp_path):
    from iron_jarvis.workflows.engine import WorkflowEngine, load_workflow

    app = create_app(str(tmp_path))
    platform = app.state.platform
    ticks = _Ticks()
    platform.notifier._channels = {"slow": _SlowChannel(ticks)}  # the ONLY destination
    engine = WorkflowEngine(platform, app.state.orchestrator)
    wf = load_workflow(
        {"name": "n", "steps": [{"name": "Tell", "kind": "notify", "message": "hi"}]}
    )

    async def body():
        rec = engine.create_record(wf)
        return await _under_heartbeat(ticks, engine.run_record(rec, wf))

    final = asyncio.run(body())
    assert final.status == "completed", final.status
    _assert_offloaded(ticks, "notify step")


def test_notify_tool_sends_off_the_loop(tmp_path):
    from iron_jarvis.comm.tools import NotifyTool

    app = create_app(str(tmp_path))
    platform = app.state.platform
    ticks = _Ticks()
    platform.notifier._channels["slow"] = _SlowChannel(ticks)
    ctx = ToolContext(
        workspace=tmp_path, session_id="t", agent_run_id="t", config=platform.config,
        event_bus=platform.event_bus, engine=platform.engine,
    )

    async def body():
        return await _under_heartbeat(
            ticks, NotifyTool(platform.notifier).execute(
                {"message": "x", "channel": "slow"}, ctx
            )
        )

    res = asyncio.run(body())
    assert res.ok, res.error
    _assert_offloaded(ticks, "notify tool")


# --- F-C-2 / F-C-3: grounding in both chat lanes + the agent lane -------------


def _slow_ground(ticks: _Ticks):
    """Stands in for projects.knowledge.ground — the call BOTH the old and new
    code make (its embed round-trip is what stalls)."""

    def ground(platform, project_id, query, *a, **kw):
        ticks.block()
        return "KNOWLEDGE"

    return ground


def test_chat_lane_grounds_project_knowledge_off_the_loop(tmp_path, monkeypatch):
    from iron_jarvis.projects import knowledge as kmod

    app = create_app(str(tmp_path))
    _quiet_router(app)
    pid = _make_project(app)
    ticks = _Ticks()
    monkeypatch.setattr(kmod, "ground", _slow_ground(ticks))

    async def body():
        return await _under_heartbeat(
            ticks,
            _post(app, "/chat", {
                "messages": [{"role": "user", "content": "what about lorem"}],
                "project_id": pid,
            }),
        )

    status, _ = asyncio.run(body())
    assert status == 200
    _assert_offloaded(ticks, "/chat knowledge.ground")


def test_stream_lane_grounds_project_knowledge_off_the_loop(tmp_path, monkeypatch):
    from iron_jarvis.projects import knowledge as kmod

    app = create_app(str(tmp_path))
    _quiet_router(app)
    pid = _make_project(app)
    ticks = _Ticks()
    monkeypatch.setattr(kmod, "ground", _slow_ground(ticks))

    async def body():
        return await _under_heartbeat(
            ticks,
            _post(app, "/chat/stream", {
                "messages": [{"role": "user", "content": "what about lorem"}],
                "project_id": pid,
            }),
        )

    status, _ = asyncio.run(body())
    assert status == 200
    _assert_offloaded(ticks, "/chat/stream knowledge.ground")


def test_agent_lane_grounds_project_context_off_the_loop(tmp_path, monkeypatch):
    from iron_jarvis.projects import knowledge as kmod

    app = create_app(str(tmp_path))
    pid = _make_project(app)
    ticks = _Ticks()
    monkeypatch.setattr(kmod, "ground", _slow_ground(ticks))

    async def body():
        return await _under_heartbeat(
            ticks,
            _post(app, "/sessions", {"task": "say hello", "wait": True, "project_id": pid}),
        )

    status, _ = asyncio.run(body())
    assert status == 200
    _assert_offloaded(ticks, "runtime._project_context")


def test_agent_lane_grounds_memory_fabric_off_the_loop(tmp_path):
    app = create_app(str(tmp_path))
    ticks = _Ticks()

    class _SlowFabric:
        def ground(self, query, **kw):
            ticks.block()
            return ""

    app.state.platform.fabric = _SlowFabric()

    async def body():
        return await _under_heartbeat(
            ticks, _post(app, "/sessions", {"task": "say hello", "wait": True})
        )

    status, _ = asyncio.run(body())
    assert status == 200
    _assert_offloaded(ticks, "runtime fabric.ground")


# --- F-C-4: memory import preview --------------------------------------------


def test_memory_import_preview_dedups_off_the_loop_and_embeds_each_fact_once(tmp_path):
    app = create_app(str(tmp_path))
    platform = app.state.platform
    ticks = _Ticks()
    embedded: list[str] = []

    class _SlowEmbedder:
        model = "slow"

        def embed(self, text):
            embedded.append(text)
            ticks.block(0.02)
            return [1.0, 0.5, 0.25]

    platform.embedder = _SlowEmbedder()
    # Three hits per fact, none containing it → the embedder is consulted.
    platform.ltm.search = lambda q, k=5, **kw: [
        {"title": f"note {i}", "snippet": "the user enjoys other things"} for i in range(3)
    ]
    text = "\n".join(f"- the user likes topic{i}" for i in range(10))

    async def body():
        return await _under_heartbeat(
            ticks, _post(app, "/memory/import/preview", {"text": text})
        )

    status, raw = asyncio.run(body())
    assert status == 200, raw
    assert len(json.loads(raw)["candidates"]) == 10
    assert not any(ticks.on_loop), "the dedup pass ran ON the loop thread"
    # Inline the whole pass yields 0 ticks in total; offloaded, the 60
    # blocked slices (10 facts × 3 hits × 2 embeds × 20ms ≈ 1.2s) yield many.
    assert sum(ticks.windows) >= _MIN_TICKS, ticks.windows
    facts = [t for t in embedded if t.startswith("the user likes")]
    assert len(facts) == len(set(facts)) == 10, "each fact must be embedded ONCE"


# --- F-C-5: SQLite writes behind a long writer -------------------------------


def _hold_write_lock(db_file: str, seconds: float) -> tuple[threading.Thread, threading.Event]:
    held = threading.Event()

    def _hold():
        con = sqlite3.connect(db_file, timeout=1)
        con.isolation_level = None
        con.execute("BEGIN IMMEDIATE")
        held.set()
        time.sleep(seconds)
        con.execute("COMMIT")
        con.close()

    th = threading.Thread(target=_hold, daemon=True)
    th.start()
    held.wait()
    return th, held


def test_workflow_run_insert_waits_for_a_writer_off_the_loop(tmp_path):
    app = create_app(str(tmp_path))
    db_file = app.state.platform.engine.url.database
    ticks = _Ticks()

    async def body():
        th, _ = _hold_write_lock(db_file, 0.5)
        try:
            return await _under_heartbeat(
                ticks,
                _post(app, "/workflows/run", {
                    "name": "n2",
                    "steps": [{"name": "Tell", "kind": "notify", "message": "hi"}],
                }),
            )
        finally:
            th.join()

    (status, _) = asyncio.run(body())
    assert status == 200
    # The stall is the DB's own busy-wait, so the window is the whole POST:
    # inline it parks the loop for the 0.5s the lock is held (≤ ~3 ticks from
    # the request's own awaits); offloaded the ticker runs throughout (≈ 50).
    assert ticks.n >= 10, f"the loop starved behind the writer ({ticks.n} ticks)"


def test_vacuum_and_prune_refuse_while_work_is_in_flight(tmp_path):
    from sqlmodel import select

    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import Session, SessionStatus
    from iron_jarvis.workflows.models import WorkflowRunRecord

    app = create_app(str(tmp_path))
    c = TestClient(app)
    engine = app.state.platform.engine
    with session_scope(engine) as db:
        s = Session(task="t", status=SessionStatus.ACTIVE)
        db.add(s)
        db.commit()
        sid = s.id
    r = c.post("/diagnostics/repair", json={"action": "db_vacuum"})
    assert r.status_code == 409, r.text
    assert "1 session(s) running" in r.json()["detail"]
    r = c.post("/diagnostics/repair", json={"action": "prune_events"})
    assert r.status_code == 409
    with session_scope(engine) as db:
        row = db.get(Session, sid)
        row.status = SessionStatus.COMPLETED
        db.add(row)
        db.add(WorkflowRunRecord(workflow_name="w", status="running"))
        db.commit()
    r = c.post("/diagnostics/repair", json={"action": "db_vacuum"})
    assert r.status_code == 409
    assert "1 workflow run(s) running" in r.json()["detail"]
    with session_scope(engine) as db:
        for rec in db.exec(select(WorkflowRunRecord)):
            rec.status = "waiting"  # parked on a human answer — writes nothing
            db.add(rec)
        db.commit()
    r = c.post("/diagnostics/repair", json={"action": "db_vacuum"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


# --- D1 (review): the per-step AgentRun merge inside perceive_act ------------


def test_perceive_act_step_save_waits_for_a_writer_off_the_loop(tmp_path):
    """The most frequent write in the app: ``_save(run)`` right after every
    LLM step. The lock holder starts from INSIDE the model call, so the very
    next write (that merge) is the one that waits behind it."""
    app = create_app(str(tmp_path))
    platform = app.state.platform
    db_file = platform.engine.url.database
    ticks = _Ticks()
    holders: list[threading.Thread] = []

    async def _stream(**kwargs):  # the agent lane streams when the router can
        yield {"type": "text", "text": "done"}
        if not holders:  # first LLM step only: the lock lands just before _save
            th, _ = _hold_write_lock(db_file, 0.5)
            holders.append(th)
        yield {
            "type": "final",
            "response": LLMResponse(text="done"),
            "provider": "mock", "model": "mock", "requested": "", "reason": "mock",
        }

    platform.router.stream = _stream

    async def body():
        try:
            return await _under_heartbeat(
                ticks, _post(app, "/sessions", {"task": "say hello", "wait": True})
            )
        finally:
            for th in holders:
                th.join()

    status, _ = asyncio.run(body())
    assert status == 200
    assert holders, "the model stub never ran"
    assert ticks.n >= 10, f"the loop starved behind the writer ({ticks.n} ticks)"
