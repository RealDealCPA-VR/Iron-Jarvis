"""v1.349.0 integrate — the step-detail CONTRACT between the two lanes.

Lane B1d made the daemon put ``detail: {input, output}`` on every FINISHED
``tool_call`` frame (the /chat/stream lane and an agent run's session stream);
lane B1h made the dashboard keep it on the step, save it with the message and
open the step's row to it after a reload; lane B2's helper-agent row draws an
agent run's live tool rows through B1h's ``LiveToolRows``. Each lane pinned its
own half against frames it wrote by hand. This file joins them: it drives the
REAL lanes (the real ``create_app`` platform, the real registry, the real
stream lane and the real runtime; only the model is scripted), and holds the
frames they really send to ONE fixture —

    dashboard/__tests__/fixtures/step-detail-frames-v1349.json

— which ``dashboard/__tests__/step-detail-contract-v1349.test.tsx`` feeds,
byte for byte, through the real stream hook into a saved step that opens after
a reload, and through the real run stream into the helper-agent row. A change
to either side's shape fails here (the daemon no longer sends what the fixture
says) or there (the dashboard no longer reads it).

Regenerate after an INTENDED shape change: ``IJ_REGEN_STEP_FRAMES=1 uv run
pytest tests/test_step_detail_contract_v1349.py`` and commit the fixture.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from iron_jarvis.agents.runtime import AgentRuntime
from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.computeruse.safety import _FENCE_BOTTOM, _FENCE_TOP
from iron_jarvis.core.models import AgentRun, AgentState, AgentType
from iron_jarvis.core.streams import StreamHub
from iron_jarvis.daemon.routes.chat import stream_chat_turn
from iron_jarvis.providers.adapters.base import LLMMessage, LLMResponse
from tests.test_step_detail_v1349 import (
    _FakeTool,
    _Scripted,
    _app,
    _body,
    _call,
    _calls,
    _script,
)

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "dashboard" / "__tests__" / "fixtures" / "step-detail-frames-v1349.json"
)
TOOL = "contract_lookup_fake"
TOKEN = "sk-ant-api03-CONTRACTFAKEFAKEFAKE12"
CARD = "4111111111111111"
RAW = (
    "Pier 9 opens at 7am\n"
    f"key {TOKEN}\n"
    "Authorization: Bearer abc123\n"
    + "\n".join(f"row {i}" for i in range(1, 21))
)
ARGS = {"q": "pier 9 hours", "card": CARD}


def _blocks(raw: str) -> list[tuple[str, str, dict]]:
    """(event, the block's exact wire text, data) for every SSE block."""
    out = []
    for block in raw.split("\n\n"):
        event, data = "message", []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
        if data:
            out.append((event, block + "\n\n", json.loads("\n".join(data))))
    return out


async def _chat_stream_frames(tmp_path) -> dict:
    tool = _FakeTool(TOOL, untrusted=True, text=RAW)
    platform = _app(tmp_path, tool)
    _, stream = _script([_calls(_call("c1", TOOL, **ARGS)), LLMResponse(text="Pier 9 opens at 7am.")])
    platform.router.stream = stream
    gen = await stream_chat_turn(platform, {}, _body(TOOL))
    raw = "".join([c async for c in gen])
    assert tool.calls == [ARGS], "anti-vacuity: the tool really ran in the stream lane"
    calls = [(ev, wire, d) for ev, wire, d in _blocks(raw) if ev == "tool_call" and d.get("id") == "c1"]
    started = [c for c in calls if c[2]["status"] == "started"]
    finished = [c for c in calls if c[2]["status"] == "finished"]
    assert len(started) == 1 and len(finished) == 1, [c[2]["status"] for c in calls]
    return {
        "started": started[0][2],
        "finished": finished[0][2],
        "wire": started[0][1] + finished[0][1],
    }


async def _agent_run_frames(tmp_path) -> dict:
    from iron_jarvis.agents.orchestrator import Orchestrator

    tool = _FakeTool(TOOL, untrusted=True, text=RAW)
    platform = _app(tmp_path, tool)
    adapter = _Scripted([_calls(_call("r1", TOOL, **ARGS)), "done"])
    platform.providers.register(adapter.provider, lambda model=None: adapter)
    session = await Orchestrator(platform).create_session(
        "pier 9 hours", AgentType.BUILDER, provider=adapter.provider,
    )
    run = AgentRun(session_id=session.id, agent_type=AgentType.BUILDER,
                   provider=session.provider, model=session.model, state=AgentState.RUNNING)
    hub = StreamHub()
    q = hub.subscribe(session.id)
    await AgentRuntime(platform).perceive_act(
        run, session, get_agent_definition(AgentType.BUILDER),
        system_prompt="sys", messages=[LLMMessage(role="user", content="pier 9 hours")],
        tool_specs=platform.registry.specs([TOOL]),
        session_allow=set(), sink=hub.sink(session.id, run.id), max_steps=3,
    )
    assert tool.calls == [ARGS], "anti-vacuity: the tool really ran in the runtime"
    frames = []
    while not q.empty():
        f = q.get_nowait()
        if f["event"] == "tool_call" and f["data"].get("id") == "r1":
            frames.append(f["data"])
    started = [f for f in frames if f["status"] == "started"]
    finished = [f for f in frames if f["status"] == "finished"]
    assert len(started) == 1 and len(finished) == 1, frames
    return {"started": started[0], "finished": finished[0]}


def _check_facts(frames: dict) -> None:
    """What makes the fixture worth feeding to the dashboard: the frame's old
    ``output`` really carries the secret and the fence (so the dashboard test
    proves it never saves them), and the detail really does not."""
    for lane in ("chat_stream", "agent_run"):
        started, finished = frames[lane]["started"], frames[lane]["finished"]
        assert started["args"]["card"] == "[card hidden]", lane
        assert TOKEN in finished["output"] and _FENCE_TOP in finished["output"], lane
        detail = finished["detail"]
        assert set(detail) == {"input", "output"}, lane
        assert detail["input"] == '{"card": "[card hidden]", "q": "pier 9 hours"}', lane
        assert detail["output"].startswith("Pier 9 opens at 7am\nkey sk-ant-***\nAuthorization: Bearer ***\n"), lane
        assert detail["output"].endswith("row 20"), lane
        blob = json.dumps(detail)
        for raw in (TOKEN, CARD, "abc123", _FENCE_TOP, _FENCE_BOTTOM):
            assert raw not in blob, (lane, raw)
    # one helper builds both lanes' detail: the same call says the same thing
    assert frames["chat_stream"]["finished"]["detail"] == frames["agent_run"]["finished"]["detail"]
    assert '"detail"' in frames["chat_stream"]["wire"]


async def test_the_daemon_sends_exactly_the_frames_the_dashboard_test_feeds(tmp_path):
    frames = {
        "_comment": (
            "GENERATED by tests/test_step_detail_contract_v1349.py from the REAL "
            "/chat/stream lane and agent runtime (IJ_REGEN_STEP_FRAMES=1 to "
            "regenerate). Fed byte for byte by "
            "dashboard/__tests__/step-detail-contract-v1349.test.tsx. "
            "Every secret here is a fake made for the test."
        ),
        "chat_stream": await _chat_stream_frames(tmp_path / "chat"),
        "agent_run": await _agent_run_frames(tmp_path / "run"),
    }
    _check_facts(frames)
    if os.environ.get("IJ_REGEN_STEP_FRAMES") == "1":
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps(frames, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    on_disk = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert on_disk == frames, (
        "the daemon's tool_call frames no longer match the dashboard's contract "
        "fixture; if the change is intended, regenerate it (see the docstring) "
        "and make the dashboard test agree"
    )


def test_the_fixture_still_proves_something():
    # The fixture is a file anyone can edit: its own facts are pinned too, so
    # it cannot be "fixed" into a harmless shape the daemon never sends.
    _check_facts(json.loads(FIXTURE.read_text(encoding="utf-8")))
