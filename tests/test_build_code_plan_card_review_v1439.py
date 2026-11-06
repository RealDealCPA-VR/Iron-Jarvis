"""v1.439.0 review — Claude Code's plan card in Build › Code.

The builder's card names Allow once by the mode it leaves the session in
("Approve, then ask first"). Claude Code really goes there (its ExitPlanMode
sets ``prePlanMode ?? "default"`` once the plan is allowed while still in
Plan), but the driver, the session row and the mode chip kept saying Plan —
so the button promised one mode and the chip showed another, and the next
process the row starts would be in Plan again. Now an Allow once that leaves
Plan moves the driver and the row to that mode and says so; a deny, or a plan
whose next mode is not known (Auto), leaves Plan as it was.

Also pinned: a plan card raised while the driver is NOT in Plan (Claude Code
entered Plan by itself, through its EnterPlanMode tool) names the mode the
session is in, never a stale earlier one.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import claude as C
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from iron_jarvis.hosted import words as W
from tests._build_app_helpers_v1409 import client, frames, new_session, sent, stop_all, until, use_fake


def _driver(mode: str) -> tuple[C.ClaudeDriver, list]:
    asks: list = []
    d = C.ClaudeDriver(write=None, emit=lambda ev: None, ask=asks.append,
                       on_session_id=lambda sid: None, mode=mode)
    return d, asks


def _plan(d: C.ClaudeDriver, rid: str = "r1") -> None:
    d._on_control_request({"type": "control_request", "request_id": rid, "request": {
        "subtype": "can_use_tool", "tool_name": "ExitPlanMode",
        "input": {"plan": "# Plan\n\n1. Do it.\n"}, "description": "Exit plan mode?",
    }})


def test_allow_once_moves_the_driver_to_the_mode_it_leaves_plan_for():
    d, asks = _driver("plan")
    d.argv(session_id="s", resume=False)
    _plan(d)
    ask = asks[-1]
    assert ask.data.get("leaves_plan_to") == "ask"
    d.answered(ask, "allow")
    assert d.mode == "ask" and d.pre_plan_mode == ""


def test_a_session_allow_with_no_mode_of_its_own_also_leaves_plan():
    # Rules only: Claude Code is still in Plan after the permission, so its
    # ExitPlanMode goes back to the mode before Plan.
    d, asks = _driver("plan")
    d.argv(session_id="s", resume=False)
    d._on_control_request({"type": "control_request", "request_id": "r1", "request": {
        "subtype": "can_use_tool", "tool_name": "ExitPlanMode", "input": {"plan": "p"},
        "permission_suggestions": [{"type": "addRules", "rules": [{"toolName": "Read"}],
                                    "behavior": "allow", "destination": "session"}],
    }})
    d.answered(asks[-1], "allow_session")
    assert d.mode == "ask"


def test_a_session_allow_that_sets_a_mode_wins_over_the_plan_exit():
    d, asks = _driver("plan")
    d.argv(session_id="s", resume=False)
    d._on_control_request({"type": "control_request", "request_id": "r1", "request": {
        "subtype": "can_use_tool", "tool_name": "ExitPlanMode", "input": {"plan": "p"},
        "permission_suggestions": [{"type": "setMode", "mode": "acceptEdits", "destination": "session"}],
    }})
    d.answered(asks[-1], "allow_session")
    assert d.mode == "edits"


def test_a_deny_keeps_plan():
    d, asks = _driver("plan")
    d.argv(session_id="s", resume=False)
    _plan(d)
    d.answered(asks[-1], "deny")
    assert d.mode == "plan"


def test_a_plan_whose_next_mode_is_not_known_keeps_plan():
    d, asks = _driver("auto")

    async def control(*_a: Any, **_k: Any) -> dict:
        return {}

    d.control = control  # type: ignore[method-assign]
    asyncio.run(d.set_mode("plan"))
    _plan(d)
    assert "leaves_plan_to" not in asks[-1].data
    d.answered(asks[-1], "allow")
    assert d.mode == "plan"


def test_claude_codes_own_plan_entry_names_the_mode_the_session_is_in():
    # The driver never saw Plan (Claude Code entered it with EnterPlanMode):
    # a stale mode from an earlier plan must not be named.
    d, asks = _driver("edits")
    d.pre_plan_mode = "full"  # left from an earlier plan
    _plan(d)
    assert asks[-1].event["then_mode"] == "edits"
    # Nothing to move: the row already says it.
    assert "leaves_plan_to" not in asks[-1].data


@pytest.fixture
def work(tmp_path):
    p = tmp_path / "work"
    p.mkdir()
    return p


@pytest.fixture
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


async def _next_ask(svc, sid):
    rid = await until(lambda: next(iter(svc.live.get(sid).asks), None) if svc.live.get(sid) else None)
    return svc.live[sid].asks[rid]


async def test_allow_once_on_the_plan_card_moves_the_row_out_of_plan(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_plan_v1417", log)
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        sid = (await new_session(c, work, mode="plan"))["id"]
        task = asyncio.ensure_future(c.post(f"/build/app/sessions/{sid}/turn", json={"text": "do it", "turn_id": "t"}))
        ask = await _next_ask(svc, sid)
        assert ask.event["then_mode"] == "ask"
        r = await c.post(f"/build/app/sessions/{sid}/answer", json={"request_id": ask.id, "decision": "allow"})
        assert r.status_code == 200, r.text
        evs = frames((await asyncio.wait_for(task, 30)).text)
        row = store.get(app.state.platform.engine, sid)
    await stop_all(app)
    assert row.mode == "ask"
    notes = [e.get("text") for e in evs if e.get("type") == "notice"]
    assert W.MODE_AFTER_PLAN.format(label="Claude Code", mode="Ask first") in notes
    # The answer Claude Code got is the plain allow, as ever.
    resp = [m for m in sent(log) if m.get("type") == "control_response"
            and str((m.get("response") or {}).get("request_id") or "").startswith("req-")]
    assert resp[0]["response"]["response"]["behavior"] == "allow"
    assert "updatedPermissions" not in resp[0]["response"]["response"]


async def test_a_deny_on_the_plan_card_keeps_the_row_in_plan(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_plan_v1417", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        sid = (await new_session(c, work, mode="plan"))["id"]
        task = asyncio.ensure_future(c.post(f"/build/app/sessions/{sid}/turn", json={"text": "do it", "turn_id": "t"}))
        ask = await _next_ask(svc, sid)
        r = await c.post(f"/build/app/sessions/{sid}/answer", json={"request_id": ask.id, "decision": "deny"})
        assert r.status_code == 200, r.text
        await asyncio.wait_for(task, 30)
        row = store.get(app.state.platform.engine, sid)
    await stop_all(app)
    assert row.mode == "plan"
