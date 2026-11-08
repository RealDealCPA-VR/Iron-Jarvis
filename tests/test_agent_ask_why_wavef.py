"""Readiness 0j.5 item 13 (daemon part) — an agent run's approval ask says
WHO asks and WHY, as the chat stream lane's approval frame does since v1.417.0.

Before this, an agent run's ``approval.requested`` carried the tool, the
(redacted) arguments and the flags — but no ``why`` and no ``asker`` — so the
run page, the bell's listing rows and the objective screen could only draw the
long card. Now the runtime computes the reason line with the chat lane's one
helper (``core/auto_approve.card_why``; a run's default reason is "not in this
run's grants yet", never "this chat's") and carries it on:

* the ``approval.requested`` event;
* the registry entry (``pending_for``), so the session row's ``waiting_on``
  and the mission view's ``waiting_on`` say it;
* the ``/chat/approvals/pending`` agent rows (re-masked, cut).

Words only — never the call's arguments. Driven through the REAL runtime pause
(a real platform, the real orchestrator), read WHILE the run is parked.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient

from iron_jarvis.agents.mission import mission_view
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.types import AgentDefinition
from iron_jarvis.core import auto_approve
from iron_jarvis.core.approvals import ChatApprovals
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import AgentType
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes.sessions import _waiting_on
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.adapters.mock import MockLLMAdapter

from tests.test_generate_media_tool_v1346 import TOOL, _wire
from tests.test_mission_cost_note_v13560 import PRICED, _image_turn


def _platform(tmp_path, responses):
    p = build_platform(str(tmp_path / "home"))
    p.providers.register("mock", lambda model=None: MockLLMAdapter(script=list(responses)))
    return p, Orchestrator(p)


async def _parked(p, orch, defn, task: str, *, decision: str, origin: str = "job:mission"):
    seen: dict = {}

    def _on_ask(e):
        if e.type != EventType.APPROVAL_REQUESTED:
            return
        sid = e.session_id
        seen["event"] = dict(e.payload)
        seen["pending"] = p.approvals.pending_for(sid)
        seen["row"] = _waiting_on(SimpleNamespace(platform=p), sid)
        seen["mission"] = mission_view(p, sid)
        p.approvals.resolve(e.payload["approval_id"], decision)

    p.event_bus.add_handler(_on_ask)
    s = await orch.create_session(task, AgentType.BUILDER, origin=origin)
    await asyncio.wait_for(orch.run_session(s.id, defn), timeout=60)
    return seen


async def test_a_plain_ask_says_it_is_not_in_this_runs_grants(tmp_path):
    shell = LLMResponse(
        tool_calls=[ToolCall(id="c1", name="shell", arguments={"command": "python -c \"print('secret-arg-words')\""})],
        finish_reason="tool_use",
    )
    p, orch = _platform(tmp_path, [shell, LLMResponse(text="Done.", finish_reason="stop")])
    defn = AgentDefinition(type=AgentType.BUILDER, system_prompt="t", tools=["shell"])
    seen = await _parked(p, orch, defn, "print something", decision="deny")
    ev = seen["event"]
    assert ev["why"] == auto_approve.RUN_NOT_GRANTED_WHY
    assert "chat" not in ev["why"]
    assert ev["asker"] == "model"
    assert seen["pending"][0]["why"] == auto_approve.RUN_NOT_GRANTED_WHY
    assert seen["pending"][0]["asker"] == "model"
    assert seen["row"]["why"] == auto_approve.RUN_NOT_GRANTED_WHY
    assert seen["row"]["asker"] == "model"
    waiting = seen["mission"]["coordinator"]["waiting_on"]
    assert waiting["why"] == auto_approve.RUN_NOT_GRANTED_WHY
    assert waiting["asker"] == "model"
    for view in (seen["row"], waiting, seen["pending"][0]["why"]):
        assert "secret-arg-words" not in repr(view)


async def test_a_priced_ask_says_its_own_sentence(tmp_path, monkeypatch):
    p, orch = _platform(
        tmp_path, [_image_turn("a red apple"), LLMResponse(text="Done.", finish_reason="stop")]
    )
    _wire(p, delay_s=120.0)
    monkeypatch.setattr(p.media_jobs, "start_background", lambda spec, **kw: "mj_spy")
    defn = AgentDefinition(type=AgentType.BUILDER, system_prompt="t", tools=[TOOL])
    seen = await _parked(p, orch, defn, "make a picture", decision="once")
    assert seen["event"]["why"] == PRICED
    assert seen["row"]["why"] == PRICED
    assert seen["mission"]["coordinator"]["waiting_on"]["why"] == PRICED


async def test_ask_before_every_action_says_so(tmp_path):
    shell = LLMResponse(
        tool_calls=[ToolCall(id="c1", name="shell", arguments={"command": "python -c \"print(1)\""})],
        finish_reason="tool_use",
    )
    p, orch = _platform(tmp_path, [shell, LLMResponse(text="Done.", finish_reason="stop")])
    defn = AgentDefinition(type=AgentType.BUILDER, system_prompt="t", tools=["shell"])
    seen: dict = {}

    def _on_ask(e):
        if e.type == EventType.APPROVAL_REQUESTED:
            seen["event"] = dict(e.payload)
            p.approvals.resolve(e.payload["approval_id"], "deny")

    p.event_bus.add_handler(_on_ask)
    s = await orch.create_session(
        "print", AgentType.BUILDER, origin="job:mission", approval_mode="always_ask"
    )
    await asyncio.wait_for(orch.run_session(s.id, defn), timeout=60)
    assert seen["event"]["why"] == auto_approve.STRICT_WHY


def test_an_ask_with_no_words_keeps_its_exact_shape():
    reg = ChatApprovals()

    async def go():
        aid, _f = reg.request("write_file", {"path": "a.txt"}, session_id="s1")
        rows = reg.pending_for("s1")
        reg.pop(aid)
        return rows

    rows = asyncio.run(go())
    assert set(rows[0]) == {"approval_id", "tool", "args", "requested_at"}


def test_a_secret_in_the_why_is_masked_and_an_odd_asker_is_dropped():
    reg = ChatApprovals()

    async def go():
        aid, _f = reg.request(
            "shell", {}, session_id="s1",
            why="run it with token=sk-abcdefghijklmnopqrstuvwx1234", asker="someone",
        )
        rows = reg.pending_for("s1")
        reg.pop(aid)
        return rows

    rows = asyncio.run(go())
    assert "sk-abcdefghijklmnopqrstuvwx1234" not in rows[0]["why"]
    assert "asker" not in rows[0]


def test_the_pending_listing_carries_why_and_asker_never_args(tmp_path):
    app = create_app(str(tmp_path))
    p = app.state.platform
    with TestClient(app) as client:

        async def ask():
            aid, _f = p.approvals.request("shell", {"command": "x"}, session_id="s_list")
            await p.event_bus.publish(
                EventType.APPROVAL_REQUESTED,
                {"approval_id": aid, "tool": "shell", "args": {"command": "secret-arg-words"},
                 "timeout_s": 0, "why": auto_approve.RUN_NOT_GRANTED_WHY, "asker": "model"},
                session_id="s_list",
            )
            return aid

        aid = client.portal.call(ask)
        rows = []
        for _ in range(150):
            rows = [r for r in client.get("/chat/approvals/pending").json()["approvals"]
                    if r.get("id") == aid]
            if rows:
                break
            client.portal.call(asyncio.sleep, 0.1)
        assert rows, "the ask was never listed"
        assert rows[0]["why"] == auto_approve.RUN_NOT_GRANTED_WHY
        assert rows[0]["asker"] == "model"
        assert "secret-arg-words" not in repr(rows[0])
