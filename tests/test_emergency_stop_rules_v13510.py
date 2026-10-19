"""v1.351.0 re-review (G13): Emergency stop stops a RULE, whatever it starts.

THE GAP THE LANES LEFT. Emergency stop's refusal lives in
``Orchestrator.create_session`` — "every unattended door ends here". A reflex
rule's ``session`` action does end there, but its ``workflow`` action starts a
saved workflow (whose non-agent steps never create a session) and its
``remote_agent`` action sends the task to someone else's endpoint (no session
at all). So with the switch on, a webhook, an email or a phone keyword still
started those — while the setting, the Autonomy page and the Handbook all say
"rules … do not start". And the ``session`` action's refusal reached the
rule's row as ``EmergencyStopOn: Emergency stop is on, …`` — a Python class
name on a user surface (``ReflexRouter.execute`` prefixes the exception's
type).

Now ``ReflexRouter.execute`` asks the switch FIRST, for every action, and the
row (and the webhook's ack) carries the switch's own sentence. Driven through
the real app factory, the real router and the real store.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.agents.orchestrator import EMERGENCY_STOP_REFUSAL
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Session
from iron_jarvis.daemon.app import create_app
from iron_jarvis.reflex.models import ReflexRule
from iron_jarvis.workflows.models import WorkflowRunRecord
from iron_jarvis.workflows.store import WorkflowStore

_STEPS = [{"agent": "builder", "task": "say hi"}]


def _runs(p) -> int:
    with session_scope(p.engine) as db:
        return len(list(db.exec(select(WorkflowRunRecord))))


def _sessions(p) -> int:
    with session_scope(p.engine) as db:
        return len(list(db.exec(select(Session))))


def _row(p, rule_id: str) -> ReflexRule:
    with session_scope(p.engine) as db:
        row = db.get(ReflexRule, rule_id)
        assert row is not None
        db.expunge(row)
        return row


def _engage(client: TestClient) -> None:
    r = client.post("/autonomy/kill", json={"enabled": True})
    assert r.status_code == 200 and r.json()["kill_switch"] is True, r.text


def test_a_webhook_rule_does_not_start_its_workflow_while_emergency_stop_is_on(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        router = client.app.state.reflex_router
        WorkflowStore(p.engine).save("nightly", _STEPS, description="t")
        rule = p.reflex.add(
            name="deploy", source="webhook", match="deploy", action="workflow", target="nightly"
        )
        _engage(client)
        before = _runs(p)

        (fired,) = asyncio.run(router.on_webhook("deploy", {"ref": "main"}))

        assert fired["ok"] is False
        assert fired["error"] == EMERGENCY_STOP_REFUSAL
        assert _runs(p) == before, "the workflow was started under Emergency stop"
        row = _row(p, rule.id)
        assert row.last_error == EMERGENCY_STOP_REFUSAL
        assert not row.fire_count, "a refused fire counted as a fire"


def test_a_rule_that_hands_work_to_a_remote_agent_does_not_send_it(tmp_path, monkeypatch):
    from iron_jarvis.agents import remote as remote_mod

    sent: list[str] = []

    class _Rec:
        name = "hermes"

    async def _run(self, record, task, secret_get):  # the remote transport, not under test
        sent.append(task)
        return {"ok": True}

    monkeypatch.setattr(remote_mod.RemoteAgentRegistry, "get", lambda self, name: _Rec())
    monkeypatch.setattr(remote_mod.RemoteAgentRegistry, "run", _run)
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        rule = p.reflex.add(
            name="mail", source="webhook", match="mail", action="remote_agent", target="hermes",
            task_template="answer {text}",
        )
        _engage(client)

        r = client.post(f"/reflex/rules/{rule.id}/test")

        assert r.status_code == 200, r.text
        assert r.json()["ok"] is False and r.json()["error"] == EMERGENCY_STOP_REFUSAL
        assert sent == [], "the task went to the remote agent under Emergency stop"
        assert _row(p, rule.id).last_error == EMERGENCY_STOP_REFUSAL


def test_a_session_rule_is_refused_in_plain_words_never_a_class_name(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        router = client.app.state.reflex_router
        rule = p.reflex.add(
            name="triage", source="webhook", match="triage", action="session",
            task_template="triage {text}",
        )
        _engage(client)
        before = _sessions(p)

        (fired,) = asyncio.run(router.on_webhook("triage", {"text": "x"}))

        assert fired["ok"] is False
        assert fired["error"] == EMERGENCY_STOP_REFUSAL
        assert _sessions(p) == before
        row = _row(p, rule.id)
        assert row.last_error == EMERGENCY_STOP_REFUSAL
        assert "EmergencyStopOn" not in (row.last_error or "")


def test_with_the_switch_off_the_same_rule_fires_as_before(tmp_path):
    """The control: nothing changes while Emergency stop is off."""
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        router = client.app.state.reflex_router
        WorkflowStore(p.engine).save("nightly", _STEPS, description="t")
        rule = p.reflex.add(
            name="deploy", source="webhook", match="deploy", action="workflow", target="nightly"
        )
        before = _runs(p)

        (fired,) = asyncio.run(router.on_webhook("deploy", {"ref": "main"}))

        assert fired["ok"] is True and fired["kind"] == "workflow"
        assert _runs(p) == before + 1
        row = _row(p, rule.id)
        assert row.last_error is None and row.fire_count == 1
