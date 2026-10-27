"""v1.350.0 integrate — the plan CONTRACT between the two lanes.

Lane C2 made the daemon answer ``GET /sessions/{id}/plan`` with
``{run_id, steps: [{goal, state}]}`` and (with the OFF-by-default setting
"Ask me to approve an agent's plan before it starts" turned on) put a plan to
the user as an ordinary ``approval.requested`` for the tool ``approve_plan``
with args ``{"steps": [goal, ...]}`` and ``once_only``. Lane C1 made the
dashboard read that route, fold the run's ``plan.*`` events into a checklist,
and draw the ask's steps as a numbered list. Each lane pinned its own half
against shapes it wrote by hand. This file joins them: it drives the REAL
orchestrator -> runtime -> decompose path (only the model is scripted; the
harness is lane C2's) and holds what the daemon really sends to ONE fixture —

    dashboard/__tests__/fixtures/plan-contract-v1350.json

— which ``dashboard/__tests__/plan-contract-v1350.test.tsx`` feeds through the
real chat page (events, the route's bodies, the pending listing) into the
helper-agent row's checklist and the dock's approval card. A change to either
side's shape fails here (the daemon no longer sends what the fixture says) or
there (the dashboard no longer reads it). There is no ``plan.proposed``
event: ``plan.created`` and the ask carry the steps (pinned below).

Regenerate after an INTENDED shape change: ``IJ_REGEN_PLAN_CONTRACT=1 uv run
pytest tests/test_plan_contract_v1350.py`` and commit the fixture.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx

from tests.test_plan_checklist_v1350 import (  # noqa: F401 — `rt` is a fixture
    _GOALS,
    _PLAN,
    _TextOnly,
    _events,
    _finish,
    _get_plan,
    _plan_asks,
    _start,
    _wait_for,
    rt,
)

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "dashboard" / "__tests__" / "fixtures" / "plan-contract-v1350.json"
)
#: The event types the contract covers, in the order the run publishes them.
_KINDS = {"plan.created", "plan.step_started", "plan.step_completed", "approval.requested", "approval.resolved"}


class _Ids:
    """Stable stand-ins for the ids a run mints (the session "s1", its run
    "r1", the ask "ask-1"), so the fixture is byte-stable run to run."""

    def __init__(self, session_id: str, run_id: str, approval_id: str = "") -> None:
        self.map = {session_id: "s1", run_id: "r1"}
        if approval_id:
            self.map[approval_id] = "ask-1"

    def __call__(self, value):
        if isinstance(value, dict):
            return {k: self(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self(v) for v in value]
        if isinstance(value, str):
            return self.map.get(value, value)
        return value


def _run_id(h, session_id: str) -> str:
    created = _events(h, "plan.created", session_id)
    assert created, "anti-vacuity: the run really made a plan"
    return created[0]["payload"]["run_id"]


async def _listing(app) -> dict:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
        return (await c.get("/chat/approvals/pending")).json()


async def _listed(app, session_id: str) -> list[dict]:
    """The listing's rows for this session, read once the ask is LISTED.

    The listing names an ask only once its ``approval.requested`` event row
    exists, and the event bus writes that row AFTER the publish spy has seen
    the ask (the v1.373.1 shape) — so one read right after the spy came back
    empty on a loaded runner (v1.399.3's Tests gate: ``AssertionError: []``).
    Poll until a row is there, bounded at 15 s."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 15.0
    while True:
        rows = [r for r in (await _listing(app))["approvals"] if r.get("session_id") == session_id]
        if rows or loop.time() > deadline:
            return rows
        await asyncio.sleep(0.05)


async def _allow_run(h) -> dict:
    h.platform.config.plan_approval = True
    inner = _TextOnly([_PLAN, "Collected the statements.", "Wrote summary.txt.", "Both done."])
    sess, task = await _start(h, inner, "chat")
    try:
        assert await _wait_for(lambda: bool(_plan_asks(h, sess.id)))
        assert await _wait_for(lambda: bool(_events(h, "approval.requested", sess.id)))
        ask = _events(h, "approval.requested", sess.id)[0]["payload"]
        awaiting = (await _get_plan(h.app, sess.id)).json()
        rows = await _listed(h.app, sess.id)
        assert len(rows) == 1, rows
        assert h.platform.approvals.resolve(ask["approval_id"], "once")
        await _finish(task)
    finally:
        if not task.done():
            task.cancel()
    done = (await _get_plan(h.app, sess.id)).json()
    ids = _Ids(sess.id, _run_id(h, sess.id), ask["approval_id"])
    row = ids(rows[0])
    row["requested_at"] = "2026-10-17T12:00:00"  # the event row's own clock
    return {
        "events": [
            {"type": p["type"], "session_id": ids(p["session_id"]), "payload": ids(p["payload"])}
            for p in h.published
            if p["session_id"] == sess.id and p["type"] in _KINDS
        ],
        "plan_awaiting": ids(awaiting),
        "pending_row": row,
        "plan_done": ids(done),
    }


async def _deny_run(h) -> dict:
    h.platform.config.plan_approval = True
    inner = _TextOnly([_PLAN])
    sess, task = await _start(h, inner, "chat")
    try:
        assert await _wait_for(lambda: bool(_plan_asks(h, sess.id)))
        assert h.platform.approvals.resolve(_plan_asks(h, sess.id)[0]["approval_id"], "deny")
        await _finish(task)
    finally:
        if not task.done():
            task.cancel()
    return _Ids(sess.id, _run_id(h, sess.id))((await _get_plan(h.app, sess.id)).json())


async def _default_run(h) -> dict:
    """The shipped default: the setting is OFF and nothing waits."""
    inner = _TextOnly([_PLAN, "Collected the statements.", "Wrote summary.txt.", "Both done."])
    sess, task = await _start(h, inner, "chat")
    await _finish(task)
    ids = _Ids(sess.id, _run_id(h, sess.id))
    return {
        "setting": h.platform.config.plan_approval,
        "asks": len(_events(h, "approval.requested", sess.id)),
        "plan": ids((await _get_plan(h.app, sess.id)).json()),
    }


def _check_facts(fx: dict) -> None:
    """What makes the fixture worth feeding to the dashboard."""
    assert fx["goals"] == _GOALS
    allow = fx["allow"]
    kinds = [e["type"] for e in allow["events"]]
    # The plan is announced before it is put to the user; no plan.proposed.
    assert kinds[:2] == ["plan.created", "approval.requested"], kinds
    assert "plan.proposed" not in kinds
    assert kinds.count("plan.step_completed") == len(_GOALS)
    ask = allow["events"][1]["payload"]
    assert ask["tool"] == "approve_plan" and ask["args"] == {"steps": _GOALS}
    assert ask["once_only"] is True and ask["can_always"] is False and ask["timeout_s"] == 0
    assert ask["approval_id"] == "ask-1"
    assert allow["events"][0]["payload"] == {"run_id": "r1", "steps": _GOALS}
    # The route: every step waiting while the plan awaits Approve, then done.
    assert allow["plan_awaiting"] == {"run_id": "r1", "steps": [{"goal": g, "state": "waiting"} for g in _GOALS]}
    assert allow["plan_done"] == {"run_id": "r1", "steps": [{"goal": g, "state": "done"} for g in _GOALS]}
    # The listing: a flag, never the steps.
    row = allow["pending_row"]
    assert row["id"] == "ask-1" and row["tool"] == "approve_plan" and row["once_only"] is True
    assert "args" not in row and "steps" not in json.dumps(row)
    # Declined: nothing ran, every step not attempted.
    assert fx["declined"] == {"run_id": "r1", "steps": [{"goal": g, "state": "skipped"} for g in _GOALS]}
    # OFF by default, and then nothing waits.
    assert fx["default"]["setting"] is False and fx["default"]["asks"] == 0
    assert [s["state"] for s in fx["default"]["plan"]["steps"]] == ["done"] * len(_GOALS)


async def test_the_daemon_sends_exactly_what_the_dashboard_test_feeds(rt, tmp_path):  # noqa: F811 — the imported fixture
    fixture = {
        "_comment": (
            "GENERATED by tests/test_plan_contract_v1350.py from the REAL "
            "orchestrator -> runtime -> decompose path (IJ_REGEN_PLAN_CONTRACT=1 "
            "to regenerate). Fed by dashboard/__tests__/plan-contract-v1350.test.tsx."
        ),
        "goals": _GOALS,
        "default": await _default_run(rt),
        "allow": await _allow_run(rt),
        "declined": await _deny_run(rt),
    }
    _check_facts(fixture)
    if os.environ.get("IJ_REGEN_PLAN_CONTRACT") == "1":
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps(fixture, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    on_disk = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert on_disk == fixture, (
        "the daemon's plan shapes no longer match the dashboard's contract "
        "fixture; if the change is intended, regenerate it (see the docstring) "
        "and make the dashboard test agree"
    )


def test_the_fixture_still_proves_something():
    # The fixture is a file anyone can edit: its own facts are pinned too, so
    # it cannot be "fixed" into a harmless shape the daemon never sends.
    _check_facts(json.loads(FIXTURE.read_text(encoding="utf-8")))


def test_anti_vacuity_the_harness_runs_on_this_loop():
    # The allow/deny drives above share one loop with the route (the
    # approvals registry the route reads is the one the run filed in).
    assert asyncio.iscoroutinefunction(_allow_run) and asyncio.iscoroutinefunction(_get_plan)
