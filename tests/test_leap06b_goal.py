"""Leap 06-b: rehearse one try of a goal.

A goal's rehearsal runs one try as a rehearsal: every change it would make
is held, and the goal's books are untouched: the budget's spend, the
checkpoint (not even the running marker), the breaker and the state are the
same after it as before, whether the try completes or fails. Driven through
the real daemon and its served routes; the model is a scripted double; the
background try is awaited (its driver task), never slept on.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Project
from iron_jarvis.core.models import Session as SessionRow
from iron_jarvis.daemon.app import create_app
from iron_jarvis.goals.models import GoalContractRecord
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.tools import rehearsal_runs as RR

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(120)]


def _scripted(calls: list[ToolCall], closing: str = "Wrote the plan."):
    state = {"n": 0}

    async def fake_stream(**kw):
        i = state["n"]
        state["n"] += 1
        if i == 0 and calls:
            resp = LLMResponse(text="", tool_calls=list(calls))
        else:
            resp = LLMResponse(text=closing)
            yield {"type": "text", "text": closing}
        yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}

    return fake_stream


def _broken():
    async def broken(**kw):
        raise RuntimeError("the model stopped answering")
        yield  # pragma: no cover - makes this an async generator

    return broken


@pytest.fixture
def daemon(tmp_path):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    events: list[dict[str, Any]] = []
    real = p.event_bus.publish

    async def spy(type, payload=None, session_id=None, **kw):
        events.append({"type": str(getattr(type, "value", type)), "payload": payload or {}})
        return await real(type, payload, session_id=session_id, **kw)

    p.event_bus.publish = spy
    sent: list[Any] = []
    p.notifier.notify = lambda *a, **k: sent.append((a, k)) or {"mock": {"ok": True}}  # type: ignore[method-assign]
    folder = tmp_path / "work"
    folder.mkdir()
    with session_scope(p.engine) as db:
        proj = Project(name="Acme", root=str(folder))
        db.add(proj)
        db.commit()
        pid = proj.id
    return app, p, events, sent, folder, pid


async def _goal(p, pid: str, **kw) -> GoalContractRecord:
    return await p.goal_engine.create_goal(
        name=kw.pop("name", "Weekly plan"),
        contract_text="Write plan.txt with this week's plan.",
        project_id=pid,
        budget={"max_tokens": 100000},
        verifier={"kind": "checks", "checks": [{"files": ["plan.txt"]}]},
        **kw,
    )


def _books(p, goal_id: str) -> dict[str, Any]:
    with session_scope(p.engine) as db:
        g = db.get(GoalContractRecord, goal_id)
        return {
            "spent": g.decoded_spent(),
            "checkpoint": g.decoded_checkpoint(),
            "breaker": g.decoded_breaker(),
            "state": g.state,
            "last_run_at": g.last_run_at,
        }


async def _rehearse(app, ident: str) -> dict:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
        r = await c.post("/rehearsals", json={"kind": "goal", "id": ident})
        assert r.status_code == 200, r.text
        rid = r.json()["id"]
        driver = RR._DRIVERS.get(rid)
        if driver is not None:
            await driver
        r = await c.get(f"/rehearsals/{rid}")
        assert r.status_code == 200, r.text
        return r.json()


async def test_a_goals_try_is_rehearsed_and_its_books_are_unchanged(daemon, monkeypatch):
    app, p, events, sent, folder, pid = daemon
    goal = await _goal(p, pid)
    events.clear()
    before = _books(p, goal.id)
    marks: list = []
    real_set = p.goal_engine.store.set_checkpoint
    monkeypatch.setattr(p.goal_engine.store, "set_checkpoint",
                        lambda *a, **k: marks.append(a) or real_set(*a, **k))
    p.router.stream = _scripted([ToolCall(id="w", name="write_file",
                                          arguments={"path": "plan.txt", "content": "the plan"})])

    view = await _rehearse(app, "weekly plan")

    assert view["status"] == "ready", view
    assert view["subject"] == {"kind": "goal", "id": goal.id, "name": "Weekly plan"}
    assert [w["words"] for w in view["would"] if "plan.txt" in w["words"]] == ["write plan.txt"]
    assert view["sentence"].startswith("This would write 1 file"), view["sentence"]
    assert not (folder / "plan.txt").exists()
    # The books: no spend, no checkpoint (not even the running marker), no
    # breaker, no state.
    assert _books(p, goal.id) == before
    assert marks == [], "a rehearsal wrote the goal's checkpoint"
    assert not [e for e in events if e["type"].startswith("goal.")], "a rehearsal published a goal event"
    assert sent == []
    with session_scope(p.engine) as db:
        rows = list(db.exec(select(SessionRow)))
    assert len(rows) == 1 and json.loads(rows[0].options_json)["rehearsal_id"] == view["id"]
    assert rows[0].origin == f"goal:{goal.id}"


async def test_a_real_try_moves_the_books(daemon):
    """Control: the same try, for real, settles spend and moves the
    checkpoint (so the rehearsal's unchanged books are its own doing)."""
    app, p, events, sent, folder, pid = daemon
    goal = await _goal(p, pid)
    before = _books(p, goal.id)
    p.router.stream = _scripted([ToolCall(id="w", name="write_file",
                                          arguments={"path": "plan.txt", "content": "the plan"})])
    res = await p.goal_engine.run_iteration(goal.id)
    assert res["status"] == "completed", res
    after = _books(p, goal.id)
    assert after["spent"]["iterations"] == before["spent"]["iterations"] + 1
    assert after["checkpoint"] != before["checkpoint"]
    assert (folder / "plan.txt").exists()


async def test_a_failed_rehearsal_strikes_no_breaker(daemon):
    app, p, events, sent, folder, pid = daemon
    goal = await _goal(p, pid)
    before = _books(p, goal.id)
    p.router.stream = _broken()
    view = await _rehearse(app, goal.id)
    assert view["status"] == "failed", view
    assert _books(p, goal.id) == before, "a failed rehearsal touched the goal's books"
    # Control: a real failed try strikes the breaker.
    await p.goal_engine.run_iteration(goal.id)
    assert _books(p, goal.id)["breaker"] != before["breaker"]


async def test_a_paused_goal_can_be_rehearsed_and_stays_paused(daemon):
    app, p, events, sent, folder, pid = daemon
    goal = await _goal(p, pid)
    p.goal_engine.store.transition(goal.id, "paused")
    p.router.stream = _scripted([])
    view = await _rehearse(app, goal.id)
    assert view["status"] == "ready", view
    assert _books(p, goal.id)["state"] == "paused"


async def test_a_try_the_real_run_would_refuse_runs_nothing_and_says_why(daemon):
    app, p, events, sent, folder, pid = daemon
    goal = await _goal(p, pid)
    await p.goal_engine.stop_goal(goal.id)
    events.clear()
    p.router.stream = _broken()
    view = await _rehearse(app, goal.id)
    assert view["status"] == "failed", view
    assert view["error"] == "A real try would not run: goal is stopped"
    assert view["would"] == []
    with session_scope(p.engine) as db:
        assert list(db.exec(select(SessionRow))) == []
    assert not [e for e in events if e["type"].startswith("goal.")], "a refused rehearsal published a goal event"


async def test_a_spent_budget_refuses_the_rehearsal_too(daemon):
    app, p, events, sent, folder, pid = daemon
    goal = await _goal(p, pid)
    p.goal_engine.store.add_spend(goal.id, tokens=100000, iterations=1, session_id="s0")
    view = await _rehearse(app, goal.id)
    assert view["status"] == "failed"
    assert view["error"].startswith("A real try would not run: budget exhausted"), view["error"]


async def test_a_goal_that_reports_to_a_local_only_chat_rehearses_local_only_and_lists_its_report(daemon, monkeypatch):
    from iron_jarvis.agents import chat_delivery as CD
    from iron_jarvis.providers import egress

    app, p, events, sent, folder, pid = daemon
    goal = await _goal(p, pid)
    with session_scope(p.engine) as db:
        g = db.get(GoalContractRecord, goal.id)
        g.chat_thread_id = "thread_example"
        db.add(g)
        db.commit()
    monkeypatch.setattr(CD, "clean_chat_thread_id", lambda v: str(v or ""))
    monkeypatch.setattr(CD, "report_target_of", lambda engine, kind, ident: "thread_example")
    monkeypatch.setattr(egress, "thread_local_only", lambda engine, chat: True)
    reported: list = []
    monkeypatch.setattr(CD, "report_to_chat", lambda *a, **k: reported.append(a))
    created: list[dict] = []
    real_create = p.goal_engine.orch.create_session

    async def spy_create(*a, **kw):
        created.append(dict(kw.get("options") or {}))
        raise RuntimeError("stop here: the options are what this test reads")

    monkeypatch.setattr(p.goal_engine.orch, "create_session", spy_create)
    view = await _rehearse(app, goal.id)
    assert created and created[0].get("local_only") is True, created
    assert "chat_thread_id" not in created[0], "a rehearsal's try was wired to report into the chat"
    assert reported == []
    assert "report this try in the chat the goal was set up from" in [w["words"] for w in view["would"]]
    assert real_create is not None
    assert view["status"] == "failed"


async def test_keep_it_stamps_the_goal(daemon):
    app, p, events, sent, folder, pid = daemon
    goal = await _goal(p, pid)
    p.router.stream = _scripted([])
    view = await _rehearse(app, goal.id)
    assert view["status"] == "ready"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
        r = await c.post(f"/rehearsals/{view['id']}/keep")
    assert r.status_code == 200, r.text
    with session_scope(p.engine) as db:
        assert db.get(GoalContractRecord, goal.id).rehearsed_at is not None
