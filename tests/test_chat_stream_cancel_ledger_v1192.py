"""Stop-mid-generation must still count the rounds the provider already billed.

`/chat/stream` persisted usage on disconnect only at the TOP of each round.
The common Stop case does not land there: the client aborts DURING a round, so
Starlette cancels the generator at its current await (``CancelledError``) or, if
it is parked at a yield, at finalization (``GeneratorExit``). Both are
BaseException-shaped, so they skipped the round-top check AND the
``except Exception`` handler that persists a FAILED row — and every COMPLETED
earlier round (counted at its `final` frame, billed by the provider) vanished
from the Usage page. The non-stream lane has no such gap.

Driven through a HAND-ROLLED ASGI driver, for the reason
``test_chat_approvals_v1187`` documents: ``TestClient`` AND
``httpx.ASGITransport`` both buffer the WHOLE SSE response, so neither can abort
a stream while a round is genuinely in flight. Here the disconnect is delivered
the way uvicorn delivers it — ``receive()`` returns ``http.disconnect`` and
Starlette's ``listen_for_disconnect`` cancels the response task.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun, AgentState
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.tools.base import ToolResult

#: Round 1's billed usage — the tokens the ledger must not lose.
_ROUND1_IN, _ROUND1_OUT = 111, 22


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
    """A LOOPBACK-host POST scope — v1.175.0's DNS-rebinding guard rejects
    anything else, in tests exactly as in production."""
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


async def _drive_until(app, body: dict, *, abort_on=None):
    """Stream /chat/stream frame by frame. When *abort_on* (a predicate over
    ``(event, data)``) matches, the client DISCONNECTS mid-round — the real
    Stop-button path. Returns the frames seen up to that point."""
    raw = json.dumps(body).encode()
    sent_req = {"done": False}
    abort = asyncio.Event()

    async def receive():
        if not sent_req["done"]:
            sent_req["done"] = True
            return {"type": "http.request", "body": raw, "more_body": False}
        if abort_on is None:
            await asyncio.sleep(3600)
        else:
            await abort.wait()
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
                {get, task}, return_when=asyncio.FIRST_COMPLETED, timeout=30
            )
            if get in done:
                msg = get.result()
            else:
                get.cancel()
                if task in done:
                    break
                raise AssertionError("stream produced nothing for 30s")
            if msg["type"] == "http.response.start":
                assert msg["status"] == 200, msg
                continue
            if msg["type"] != "http.response.body":
                continue
            buf += msg.get("body", b"").decode("utf-8", "replace")
            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                for ev, data in _frames(block + "\n\n"):
                    frames.append((ev, data))
                    if abort_on is not None and abort_on(ev, data):
                        abort.set()
            if not msg.get("more_body", False):
                break
    finally:
        await task  # propagate any in-app failure honestly
    return frames


def _chat_runs(platform) -> list[AgentRun]:
    with session_scope(platform.engine) as db:
        return [r for r in db.exec(select(AgentRun)) if r.session_id == "chat"]


def _arm_fake_tool(app):
    """The tool loop only runs a second round when tools are armed; keep the
    call itself deterministic and off the filesystem."""

    async def fake_invoke(name, args, ctx, permissions, overrides=None, *,
                          session_allow=None, **kw):
        return ToolResult(ok=True, output="ok")

    app.state.platform.registry.invoke = fake_invoke


def _two_round_stream(parked: asyncio.Event):
    """Round 1 completes (billed, one tool call); round 2 starts streaming and
    then PARKS — the window in which the user hits Stop."""

    rounds = {"n": 0}

    async def fake_stream(*, provider=None, model=None, system, messages,
                          tools, session_id=None, task_class=None):
        if rounds["n"] == 0:
            rounds["n"] += 1
            resp = LLMResponse(
                text="",
                tool_calls=[ToolCall(id="c1", name="read_file",
                                     arguments={"path": "notes.txt"})],
                usage={"input_tokens": _ROUND1_IN, "output_tokens": _ROUND1_OUT},
            )
            yield {"type": "final", "response": resp,
                   "provider": "mock", "model": "mock"}
        else:
            yield {"type": "text", "text": "second-round-token"}
            await parked.wait()          # never set: the abort lands here
            yield {"type": "final", "response": LLMResponse(text="unreachable"),
                   "provider": "mock", "model": "mock"}

    return fake_stream


@pytest.mark.asyncio
async def test_stop_mid_round_still_counts_the_billed_rounds(tmp_path):
    app = create_app(str(tmp_path))
    _arm_fake_tool(app)
    parked = asyncio.Event()
    app.state.platform.router.stream = _two_round_stream(parked)

    frames = await _drive_until(
        app,
        {"messages": [{"role": "user", "content": "read my notes"}],
         "tools": ["read_file"], "auto_tools": False},
        # Round 2 is streaming — past every round-TOP disconnect check.
        abort_on=lambda ev, d: ev == "token" and d.get("text") == "second-round-token",
    )

    assert not any(ev == "done" for ev, _ in frames), "the turn was not aborted"

    runs = _chat_runs(app.state.platform)
    assert len(runs) == 1, (
        "the completed round was billed by the provider and must appear in the "
        f"ledger exactly once, got {[(r.state, r.input_tokens) for r in runs]}"
    )
    row = runs[0]
    assert row.state == AgentState.CANCELLED
    assert (row.input_tokens, row.output_tokens) == (_ROUND1_IN, _ROUND1_OUT)
    assert row.steps == 1


@pytest.mark.asyncio
async def test_stop_during_the_first_round_writes_no_row(tmp_path):
    """Nothing the ledger could have known: partial-stream token counts never
    arrive without a `final` frame, so a row here would be invented."""
    app = create_app(str(tmp_path))
    parked = asyncio.Event()

    async def fake_stream(*, provider=None, model=None, system, messages,
                          tools, session_id=None, task_class=None):
        yield {"type": "text", "text": "first-round-token"}
        await parked.wait()
        yield {"type": "final", "response": LLMResponse(text="unreachable"),
               "provider": "mock", "model": "mock"}

    app.state.platform.router.stream = fake_stream

    await _drive_until(
        app,
        {"messages": [{"role": "user", "content": "hello"}], "auto_tools": False},
        abort_on=lambda ev, d: ev == "token" and d.get("text") == "first-round-token",
    )

    assert _chat_runs(app.state.platform) == []


@pytest.mark.asyncio
async def test_completed_turn_still_writes_exactly_one_row(tmp_path):
    """The idempotence flag must not cost the normal path its ledger row, and
    must not let the cancellation guard add a second one."""
    app = create_app(str(tmp_path))

    async def fake_stream(*, provider=None, model=None, system, messages,
                          tools, session_id=None, task_class=None):
        yield {"type": "text", "text": "hi"}
        yield {"type": "final",
               "response": LLMResponse(
                   text="hi",
                   usage={"input_tokens": 7, "output_tokens": 3},
               ),
               "provider": "mock", "model": "mock"}

    app.state.platform.router.stream = fake_stream

    frames = await _drive_until(
        app, {"messages": [{"role": "user", "content": "hello"}],
              "auto_tools": False},
    )
    assert any(ev == "done" for ev, _ in frames)

    runs = _chat_runs(app.state.platform)
    assert len(runs) == 1
    assert runs[0].state == AgentState.COMPLETED
    assert (runs[0].input_tokens, runs[0].output_tokens) == (7, 3)
