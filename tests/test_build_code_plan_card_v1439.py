"""v1.439.0 — Claude Code's plan card in Build › Code names each allow by the
mode it leaves the session in.

Claude Code's ExitPlanMode, when allowed with no mode of its own, goes back
to ``prePlanMode ?? "default"`` (read from 2.1.316's bundle): the mode the
session left for Plan, or default for a process started in Plan. The driver
keeps that mode (``pre_plan_mode``) and the card carries it as ``then_mode``;
a session-wide allow that switches mode carries it as ``sets_mode`` and the
card no longer says the switch a second time. The answers sent are the same
as ever.
"""

from __future__ import annotations

import asyncio
from typing import Any

from iron_jarvis.hosted.claude import ClaudeDriver


def _driver(mode: str) -> tuple[ClaudeDriver, list, list]:
    asks: list = []
    written: list = []

    async def write(obj: dict[str, Any]) -> None:
        written.append(obj)

    d = ClaudeDriver(write=write, emit=lambda ev: None, ask=asks.append,
                     on_session_id=lambda sid: None, mode=mode)
    return d, asks, written


def _plan_request(d: ClaudeDriver, suggestions: list | None = None) -> dict[str, Any]:
    req: dict[str, Any] = {
        "subtype": "can_use_tool", "tool_name": "ExitPlanMode",
        "input": {"plan": "# Plan\n\n1. Do it.\n"}, "description": "Exit plan mode?",
    }
    if suggestions is not None:
        req["permission_suggestions"] = suggestions
    d._on_control_request({"type": "control_request", "request_id": "r1", "request": req})


def _control_ok(d: ClaudeDriver) -> None:
    async def control(*_a: Any, **_k: Any) -> dict:
        return {}
    d.control = control  # type: ignore[method-assign]


def test_a_process_started_in_plan_goes_back_to_ask_first():
    d, asks, _ = _driver("plan")
    d.argv(session_id="s", resume=False)
    _plan_request(d)
    ev = asks[-1].event
    assert ev["then_mode"] == "ask"
    assert "sets_mode" not in ev


def test_plan_from_auto_edits_goes_back_to_auto_edits():
    d, asks, _ = _driver("edits")
    _control_ok(d)
    asyncio.run(d.set_mode("plan"))
    _plan_request(d)
    assert asks[-1].event["then_mode"] == "edits"


def test_plan_from_auto_is_not_promised():
    # Claude Code may fall back to default when Auto cannot come back.
    d, asks, _ = _driver("auto")
    _control_ok(d)
    asyncio.run(d.set_mode("plan"))
    _plan_request(d)
    assert "then_mode" not in asks[-1].event


def test_leaving_plan_by_the_chip_forgets_the_mode_before_it():
    d, _, _ = _driver("edits")
    _control_ok(d)
    asyncio.run(d.set_mode("plan"))
    asyncio.run(d.set_mode("ask"))
    assert d.pre_plan_mode == ""


def test_a_session_allow_that_switches_mode_carries_it_and_is_not_said_twice():
    d, asks, written = _driver("plan")
    d.argv(session_id="s", resume=False)
    sugg = [{"type": "setMode", "mode": "acceptEdits", "destination": "session"}]
    _plan_request(d, sugg)
    ask = asks[-1]
    assert ask.event["sets_mode"] == "edits"
    assert ask.event["then_mode"] == "ask"
    assert "switches Claude Code" not in ask.event["detail"]
    assert ask.event["detail"] == "Exit plan mode?"
    # The answers are the same as ever.
    once = d.prepare_answer(ask, "allow", "", None)["response"]["response"]
    assert once == {"behavior": "allow", "updatedInput": {"plan": "# Plan\n\n1. Do it.\n"}}
    session = d.prepare_answer(ask, "allow_session", "", None)["response"]["response"]
    assert session["updatedPermissions"] == sugg


def test_any_other_card_keeps_its_words_and_no_mode_keys():
    d, asks, _ = _driver("ask")
    sugg = [{"type": "setMode", "mode": "acceptEdits", "destination": "session"}]
    d._on_control_request({"type": "control_request", "request_id": "r2", "request": {
        "subtype": "can_use_tool", "tool_name": "Edit",
        "input": {"file_path": "a.txt", "old_string": "a", "new_string": "b"},
        "permission_suggestions": sugg,
    }})
    ev = asks[-1].event
    assert "then_mode" not in ev and "sets_mode" not in ev
    assert "switches Claude Code to Auto edits" in ev["detail"]
