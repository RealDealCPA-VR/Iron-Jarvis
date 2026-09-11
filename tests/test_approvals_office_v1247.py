"""v1.247.0 (C3) — approvals for office work.

The user's direction (2026-09-11): office work should "simply work" in chat.
The measured cause of the worst failures: on 2026-08-23 a rename job asked 31
times and 26 of those asks expired unanswered at 300 s, each recorded as work
not done. Three changes, each pinned here:

1. WAITING, NOT REFUSING. A run a PERSON is watching (origin chat / job /
   project / user) waits on its ask until it is answered, declined or the
   session is cancelled. Unattended doors keep SESSION_APPROVAL_TIMEOUT_S.
   The /chat/stream ask waits too, and Stop still ends a turn parked on it.
2. ONE APPROVAL FOR THE BATCH. Several ask-tier calls in one step (or one
   chat round) that need the SAME permission file ONE ask with a count;
   'once' runs exactly that batch, 'deny' refuses all of it, and every call
   still gets its own ledger row.
3. OFFICE WORK STAYS IN CHAT. A turn holding a document-writing tool gets 12
   rounds (not 6) and, on its last round, ends in chat with an answer instead
   of escalating and discarding its own work.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

import iron_jarvis.agents.runtime as runtime_mod
import iron_jarvis.daemon.routes.chat as chat_routes
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.runtime import PAUSE_TIMEOUT_REASON, AgentRuntime
from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import AgentRun, AgentState, AgentType, ToolInvocation
from iron_jarvis.core.turns import TURNS
from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes.sessions import _waiting_on
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.base import ToolResult

#: A message whose signals arm `shell` at the ask tier (run + command).
_ASK_MSG = "run the command `echo approved-run` in the terminal for me"


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #
@pytest.fixture
def rt(tmp_path):
    app = create_app(str(tmp_path))
    platform = app.state.platform
    published: list[dict] = []
    real_publish = platform.event_bus.publish

    async def spy(type, payload=None, session_id=None, **kw):
        published.append({
            "type": str(getattr(type, "value", type)),
            "payload": payload or {},
            "session_id": session_id,
        })
        return await real_publish(type, payload, session_id=session_id, **kw)

    platform.event_bus.publish = spy
    return SimpleNamespace(
        runtime=AgentRuntime(platform), platform=platform, published=published, app=app
    )


def _session(origin: str, sid: str = "session_test"):
    return SimpleNamespace(id=sid, origin=origin)


def _tc(cmd: str):
    return SimpleNamespace(name="shell", arguments={"command": cmd})


def _events(rt, type_: str, session_id: str | None = None) -> list[dict]:
    return [
        p for p in rt.published
        if p["type"] == type_ and (session_id is None or p["session_id"] == session_id)
    ]


async def _wait_for(pred, *, tries=500, sleep=0.01) -> bool:
    for _ in range(tries):
        if pred():
            return True
        await asyncio.sleep(sleep)
    return False


def _run_state(engine, session_id: str) -> str:
    with session_scope(engine) as db:
        run = db.exec(select(AgentRun).where(AgentRun.session_id == session_id)).first()
        return "" if run is None else getattr(run.state, "value", str(run.state))


def _three_shells_then_done():
    """A router.stream stub: step 0 asks for THREE shell calls, step 1 answers."""
    rounds = {"n": 0}

    async def fake_stream(*, provider=None, model=None, system, messages, tools,
                          session_id=None, task_class=None, **kw):
        i = rounds["n"]
        rounds["n"] += 1
        if i == 0:
            resp = LLMResponse(text="", tool_calls=[
                ToolCall(id=f"c{n}", name="shell", arguments={"command": f"echo item-{n}"})
                for n in (1, 2, 3)
            ])
        else:
            resp = LLMResponse(text="All three done.")
            yield {"type": "text", "text": "All three done."}
        yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}

    return fake_stream


def _frames(raw: str) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for block in raw.split("\n\n"):
        event, data_lines = "message", []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if data_lines:
            try:
                out.append((event, json.loads("\n".join(data_lines))))
            except ValueError:
                pass
    return out


def _scope(path: str, body: bytes) -> dict:
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


async def _asgi_post(app, path: str, payload: dict) -> int:
    body = json.dumps(payload).encode()
    sent = {"done": False}

    async def receive():
        if not sent["done"]:
            sent["done"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    status = {"v": 0}

    async def send(msg):
        if msg["type"] == "http.response.start":
            status["v"] = msg["status"]

    await app(_scope(path, body), receive, send)
    return status["v"]


async def _drive_stream(app, body: dict, decide):
    """Consume /chat/stream AS IT STREAMS (TestClient buffers the whole body,
    which cannot answer a paused turn), handing each approval frame to
    ``decide`` while the turn is parked on it."""
    raw = json.dumps(body).encode()
    sent = {"done": False}

    async def receive():
        if not sent["done"]:
            sent["done"] = True
            return {"type": "http.request", "body": raw, "more_body": False}
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    q: asyncio.Queue = asyncio.Queue()

    async def send(msg):
        await q.put(msg)

    task = asyncio.create_task(app(_scope("/chat/stream", raw), receive, send))
    frames: list[tuple[str, dict]] = []
    buf = ""
    try:
        while True:
            get = asyncio.create_task(q.get())
            done, _ = await asyncio.wait(
                {get, task}, return_when=asyncio.FIRST_COMPLETED, timeout=60
            )
            if get in done:
                msg = get.result()
            else:
                get.cancel()
                if task in done:
                    break
                raise AssertionError("stream produced nothing for 60s")
            if msg["type"] != "http.response.body":
                continue
            buf += msg.get("body", b"").decode("utf-8", "replace")
            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                for ev, data in _frames(block + "\n\n"):
                    frames.append((ev, data))
                    if ev == "approval":
                        await decide(data)
            if not msg.get("more_body", False):
                break
    finally:
        await task
    return frames


def _stream_of_three_shells():
    rounds = {"n": 0}

    async def fake_stream(*, provider=None, model=None, system, messages,
                          tools, session_id=None, task_class=None, **kw):
        if rounds["n"] == 0:
            rounds["n"] += 1
            resp = LLMResponse(text="", tool_calls=[
                ToolCall(id=f"c{n}", name="shell", arguments={"command": f"echo item-{n}"})
                for n in (1, 2, 3)
            ])
            yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}
        else:
            yield {"type": "text", "text": "done."}
            yield {"type": "final", "response": LLMResponse(text="done."),
                   "provider": "mock", "model": "mock"}

    return fake_stream


# --------------------------------------------------------------------------- #
# 1. WAITING, NOT REFUSING — agent runs
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["chat", "job:agents", "project:p1", "user"])
async def test_an_attended_ask_waits_past_the_old_clock(rt, monkeypatch, origin):
    """The old clock is made tiny; an attended ask must ignore it entirely and
    wait for the person. The payload says so: timeout_s 0 = no expiry."""
    monkeypatch.setattr(runtime_mod, "SESSION_APPROVAL_TIMEOUT_S", 0.05)
    agent_def = get_agent_definition(AgentType.BUILDER)
    task = asyncio.create_task(
        rt.runtime._pause_for_approval(_session(origin), _tc("echo a"), agent_def, set())
    )
    assert await _wait_for(lambda: bool(_events(rt, "approval.requested")))
    await asyncio.sleep(0.4)  # eight times the old clock
    assert not task.done(), f"an attended ({origin}) ask expired into a refusal"
    req = _events(rt, "approval.requested")[0]["payload"]
    assert req["timeout_s"] == 0
    assert rt.platform.approvals.resolve(req["approval_id"], "once")
    deny, extra = await task
    assert deny == "" and "shell" in extra
    assert rt.platform.approvals.pending_count() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origin",
    ["goal:g1", "schedule:nightly", "workflow:w1", "reflex:r1", "comm:telegram", "autonomy"],
)
async def test_an_unattended_door_keeps_its_clock(rt, monkeypatch, origin):
    """Nobody may be awake to answer a 3 am schedule: the bound stays, and the
    timeout receipt the trust ladder is built on still lands."""
    monkeypatch.setattr(runtime_mod, "SESSION_APPROVAL_TIMEOUT_S", 0.1)
    deny, extra = await rt.runtime._pause_for_approval(
        _session(origin), _tc("echo a"), get_agent_definition(AgentType.BUILDER), set()
    )
    assert deny == PAUSE_TIMEOUT_REASON and extra == set()
    assert _events(rt, "approval.requested")[0]["payload"]["timeout_s"] > 0
    assert [p["payload"]["decision"] for p in _events(rt, "approval.resolved")] == ["timeout"]


@pytest.mark.asyncio
async def test_cancelling_the_run_releases_a_parked_attended_ask(rt):
    """With no clock, cancel is the way out: the ask is popped and the run is
    RUNNING again (then the cancel settles it)."""
    run = AgentRun(session_id="session_test", state=AgentState.RUNNING)
    rt.runtime._save(run)
    task = asyncio.create_task(rt.runtime._pause_for_approval(
        _session("chat"), _tc("echo a"), get_agent_definition(AgentType.BUILDER), set(), run=run,
    ))
    assert await _wait_for(lambda: _run_state(rt.platform.engine, "session_test") == "waiting")
    assert rt.platform.approvals.pending_count() == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert rt.platform.approvals.pending_count() == 0
    assert await _wait_for(lambda: _run_state(rt.platform.engine, "session_test") == "running")


@pytest.mark.asyncio
async def test_a_daemon_restart_while_waiting_ends_the_run_honestly(tmp_path):
    """A waiting run's ask lives in memory; a restart kills it, and boot
    reconciliation settles the row instead of leaving it WAITING forever."""
    app = create_app(str(tmp_path))
    orch = Orchestrator(app.state.platform)
    sess = await orch.create_session("rename the files", AgentType.BUILDER, origin="chat")
    with session_scope(app.state.platform.engine) as db:
        db.add(AgentRun(session_id=sess.id, state=AgentState.WAITING))
        db.commit()
    Orchestrator(app.state.platform).reconcile_interrupted_sessions()
    with session_scope(app.state.platform.engine) as db:
        run = db.exec(select(AgentRun).where(AgentRun.session_id == sess.id)).first()
        assert run.state == AgentState.FAILED
        assert run.result == "interrupted by a daemon restart"


# --------------------------------------------------------------------------- #
# 2. ONE APPROVAL FOR THE BATCH — agent runs
# --------------------------------------------------------------------------- #
async def _run_batch(rt, decision: str):
    rt.platform.router.stream = _three_shells_then_done()
    orch = Orchestrator(rt.platform)
    sess = await orch.create_session("tidy the three files", AgentType.BUILDER, origin="chat")
    task = asyncio.create_task(orch.run_session(sess.id))
    assert await _wait_for(lambda: bool(_events(rt, "approval.requested", sess.id)))
    await asyncio.sleep(0.2)  # give any (wrong) sibling cards the chance to appear
    reqs = _events(rt, "approval.requested", sess.id)
    assert rt.platform.approvals.resolve(reqs[0]["payload"]["approval_id"], decision)
    await task
    with session_scope(rt.platform.engine) as db:
        rows = [r for r in db.exec(select(ToolInvocation)) if r.session_id == sess.id]
    return sess, reqs, rows


@pytest.mark.asyncio
async def test_three_asks_in_one_step_are_one_card_and_once_runs_all_three(rt):
    sess, reqs, rows = await _run_batch(rt, "once")
    assert len(reqs) == 1, f"one step, one permission, one card — got {len(reqs)}"
    payload = reqs[0]["payload"]
    assert payload["count"] == 3
    assert [e["command"] for e in payload["examples"]] == [
        "echo item-1", "echo item-2", "echo item-3",
    ]
    shell_rows = [r for r in rows if r.tool == "shell"]
    assert len(shell_rows) == 3, "every call keeps its own ledger row"
    assert all(r.ok for r in shell_rows), [r.output for r in shell_rows]
    assert rt.platform.approvals.pending_count() == 0


@pytest.mark.asyncio
async def test_deny_on_the_batch_card_refuses_every_call(rt):
    sess, reqs, rows = await _run_batch(rt, "deny")
    assert len(reqs) == 1
    shell_rows = [r for r in rows if r.tool == "shell"]
    assert len(shell_rows) == 3 and not any(r.ok for r in shell_rows)
    denied = _events(rt, "tool.denied", sess.id)
    assert len(denied) == 3
    assert {d["payload"]["kind"] for d in denied} == {"permission denied"}


@pytest.mark.asyncio
async def test_cancelling_a_run_parked_on_a_batch_card_leaves_nothing_behind(rt):
    """The shared pause has no clock; the step's ``finally`` must cancel it
    when the run is cancelled, or the ask (and WAITING) would outlive the run."""
    rt.platform.router.stream = _three_shells_then_done()
    orch = Orchestrator(rt.platform)
    sess = await orch.create_session("tidy the three files", AgentType.BUILDER, origin="chat")
    task = asyncio.create_task(orch.run_session(sess.id))
    orch._running[sess.id] = task
    assert await _wait_for(lambda: _run_state(rt.platform.engine, sess.id) == "waiting")
    orch.cancel_session(sess.id)
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert await _wait_for(lambda: rt.platform.approvals.pending_count() == 0), (
        "the batch's ask outlived the cancelled run"
    )
    assert _run_state(rt.platform.engine, sess.id) == "cancelled"


# --------------------------------------------------------------------------- #
# 1b + 2b — the /chat/stream lane
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_stream_shows_one_card_for_a_round_of_three(tmp_path):
    app = create_app(str(tmp_path))
    app.state.platform.router.stream = _stream_of_three_shells()

    async def approve(data):
        assert await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "once"}) == 200

    frames = await _drive_stream(
        app, {"messages": [{"role": "user", "content": _ASK_MSG}], "auto_tools": True}, approve
    )
    asks = [d for ev, d in frames if ev == "approval"]
    assert len(asks) == 1, asks
    assert asks[0]["count"] == 3 and len(asks[0]["examples"]) == 3
    assert asks[0]["timeout_s"] == 0, "the stream ask waits for its person"
    ran = [d for ev, d in frames if ev == "tool_call" and d.get("status") == "finished"]
    assert len(ran) == 3 and all(d["ok"] for d in ran), ran
    assert frames[-1][0] == "done"


def _stream_of_one_shell():
    rounds = {"n": 0}

    async def fake_stream(*, provider=None, model=None, system, messages,
                          tools, session_id=None, task_class=None, **kw):
        if rounds["n"] == 0:
            rounds["n"] += 1
            resp = LLMResponse(text="", tool_calls=[
                ToolCall(id="c1", name="shell", arguments={"command": "echo approved-run"}),
            ])
            yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}
        else:
            yield {"type": "text", "text": "done."}
            yield {"type": "final", "response": LLMResponse(text="done."),
                   "provider": "mock", "model": "mock"}

    return fake_stream


@pytest.mark.asyncio
async def test_the_stream_ask_outlasts_its_poll_slices(tmp_path):
    """An answer that lands seconds later is still the answer — not a timeout
    (the parked ask polls for Stop every _ASK_POLL_S; that is not a clock)."""
    app = create_app(str(tmp_path))
    app.state.platform.router.stream = _stream_of_one_shell()

    async def answer_late(data):
        await asyncio.sleep(2.5)  # several poll slices
        assert await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "once"}) == 200

    frames = await _drive_stream(
        app, {"messages": [{"role": "user", "content": _ASK_MSG}], "auto_tools": True},
        answer_late,
    )
    resolved = next(d for ev, d in frames if ev == "approval_resolved")
    assert resolved["decision"] == "once"
    finished = next(d for ev, d in frames if ev == "tool_call" and d.get("status") == "finished")
    assert finished["ok"] is True


@pytest.mark.asyncio
async def test_stop_while_parked_on_an_ask_ends_the_turn(tmp_path):
    """No clock means Stop must still get out: pressed from any window while
    the turn waits on a card, it ends the turn, pops the ask, runs nothing,
    and the billed round is ledgered CANCELLED."""
    app = create_app(str(tmp_path))
    platform = app.state.platform
    platform.router.stream = _stream_of_one_shell()
    turn_id = "t-park-v1247"

    async def press_stop(data):
        assert TURNS.stop(turn_id), "the parked turn was not stoppable"

    frames = await asyncio.wait_for(_drive_stream(
        app,
        {"messages": [{"role": "user", "content": _ASK_MSG}], "auto_tools": True,
         "turn_id": turn_id},
        press_stop,
    ), timeout=20)
    kinds = [ev for ev, _ in frames]
    assert "approval" in kinds
    assert "done" not in kinds and "tool_call" not in kinds, kinds
    assert platform.approvals.pending_count() == 0
    with session_scope(platform.engine) as db:
        states = [r.state for r in db.exec(select(AgentRun)) if r.session_id == "chat"]
    assert AgentState.CANCELLED in states, states


# --------------------------------------------------------------------------- #
# 3. OFFICE WORK STAYS IN CHAT
# --------------------------------------------------------------------------- #
def test_the_round_budget_is_one_helper_with_two_answers():
    assert chat_turn._round_budget({"write_document"}) == chat_turn._DOC_TOOL_ROUNDS == 12
    assert chat_turn._round_budget({"redact_pii", "shell"}) == 12
    assert chat_turn._round_budget({"read_file", "image_info"}) == chat_turn._MAX_TOOL_ROUNDS == 6
    assert chat_turn._round_budget(set()) == 6
    # The stream lane uses the SAME helper, not a copy.
    assert chat_routes._round_budget is chat_turn._round_budget


def test_the_escalation_exit_keeps_office_work_here():
    desc = chat_turn._ESCALATE_SPEC["description"].lower()
    assert "documents, spreadsheets and files are not a reason" in desc
    assert "only when" in desc  # still a last resort (test_chat_escalation)


def _fake_invoke_ok():
    async def fake_invoke(name, args, ctx, permissions, overrides=None, *,
                          session_allow=None, **kw):
        return ToolResult(ok=True, output="wrote report.xlsx")

    return fake_invoke


def _write_call(n: int) -> ToolCall:
    return ToolCall(id=f"w{n}", name="write_document",
                    arguments={"path": "report.xlsx", "content": "a,b"})


_ANSWER = "Built report.xlsx in your folder; two rows still need a date."


def test_an_office_turn_gets_twelve_rounds_and_ends_in_chat(tmp_path, monkeypatch):
    """Non-stream lane. The model keeps writing; after 12 rounds the turn ends
    HERE — no escalation — with one tool-less answer and the honest note."""
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    calls: list[dict] = []

    async def fake_complete(*, provider=None, model=None, system, messages,
                            tools, task_class):
        calls.append({"tools": tools, "last": messages[-1].content})
        if tools == []:
            return RouteResult(LLMResponse(text=_ANSWER), "mock", "mock")
        return RouteResult(LLMResponse(text="", tool_calls=[_write_call(len(calls))]),
                           "mock", "mock")

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    monkeypatch.setattr(platform.registry, "invoke", _fake_invoke_ok())
    r = client.post("/chat", json={
        "messages": [{"role": "user", "content": "make the report"}],
        "tools": ["write_document"],
    })
    assert r.status_code == 200
    body = r.json()
    assert len([c for c in calls if c["tools"]]) == 12
    nudges = [c for c in calls if c["tools"] == []]
    assert len(nudges) == 1 and nudges[0]["last"] == chat_turn.OUT_OF_ROUNDS_INSTRUCTION
    assert body["escalate"] is False, "an office turn must not hand its work away"
    assert body["reply"].startswith(_ANSWER)
    assert "stopped after 11 tool rounds" in body["reply"]


def test_the_stream_lane_gives_an_office_turn_the_same_budget(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    rounds = {"n": 0}

    async def fake_stream(*, provider=None, model=None, system, messages,
                          tools, task_class=None, **kw):
        rounds["n"] += 1
        yield {"type": "final",
               "response": LLMResponse(text="", tool_calls=[_write_call(rounds["n"])]),
               "provider": "mock", "model": "mock"}

    nudges: list[str] = []

    async def fake_complete(*, provider=None, model=None, system, messages,
                            tools, task_class):
        nudges.append(messages[-1].content)
        return RouteResult(LLMResponse(text=_ANSWER), "mock", "mock")

    monkeypatch.setattr(platform.router, "stream", fake_stream)
    monkeypatch.setattr(platform.router, "complete", fake_complete)
    monkeypatch.setattr(platform.registry, "invoke", _fake_invoke_ok())
    r = client.post("/chat/stream", json={
        "messages": [{"role": "user", "content": "make the report"}],
        "tools": ["write_document"],
    })
    done = next(d for ev, d in _frames(r.text) if ev == "done")
    assert rounds["n"] == 12
    assert nudges == [chat_turn.OUT_OF_ROUNDS_INSTRUCTION]
    assert done["escalate"] is False
    assert done["reply"].startswith(_ANSWER)
    assert "stopped after 11 tool rounds" in done["reply"]


# --------------------------------------------------------------------------- #
# the surfaces: numbers ride the listing and the row, never args
# --------------------------------------------------------------------------- #
def test_the_listing_and_the_row_carry_the_count_and_wait_never_args(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    ex = [{"source": f"{n}.pdf"} for n in (1, 2, 3)]

    async def scenario():
        ap_id, _fut = platform.approvals.request(
            "rename_real_file", ex[0], session_id="s-batch", count=8, examples=ex,
        )
        pending = platform.approvals.pending_for("s-batch")
        await platform.event_bus.publish(
            EventType.APPROVAL_REQUESTED,
            {"approval_id": ap_id, "tool": "rename_real_file", "args": ex[0],
             "timeout_s": 0, "count": 8, "examples": ex},
            session_id="s-batch",
        )
        r = client.get("/chat/approvals/pending")
        waiting = _waiting_on(SimpleNamespace(platform=platform), "s-batch")
        platform.approvals.pop(ap_id)
        return ap_id, pending, r, waiting

    ap_id, pending, r, waiting = asyncio.run(scenario())
    assert pending[0]["count"] == 8 and pending[0]["examples"] == ex
    row = r.json()["approvals"][0]
    assert row["id"] == ap_id and row["count"] == 8 and row["timeout_s"] == 0
    assert "args" not in row and "examples" not in row
    assert waiting == {"approval_id": ap_id, "tool": "rename_real_file", "count": 8}
