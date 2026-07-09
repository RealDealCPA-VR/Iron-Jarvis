"""Offline tests for the Reflex Loop / Ambient Operator.

A :class:`~iron_jarvis.reflex.models.ReflexRule` binds an inbound SIGNAL (an
external webhook firing, or a keyword in an inbound comm message) to an ACTION
(run a saved workflow, delegate to a remote agent, or start a supervised
session). This suite proves, fully offline against a real daemon on a temp root:

  * STORE — durable CRUD + the webhook (exact-slug) / comm (whole-word keyword)
    matching, with disabled rules excluded;
  * ROUTER — a webhook fires the bound workflow (the headline) or session, the
    run-record / Session row is created SYNCHRONOUSLY, an unmatched webhook is a
    no-op, and a rule bound to a missing workflow reports an error (never raises);
  * HTTP — the /reflex/rules CRUD + /test routes and their 400 validation;
  * COMMANDS — the phone command grammar (/help /status /workflows /run …);
  * INBOUND — an authorized "/command" over comm dispatches through the
    interpreter without spawning a session;
  * PERSISTENCE — rules survive a daemon restart (a second app on the same root).

Background workflow/session tasks launched by the router are cancelled at
TestClient teardown; every assertion is on the SYNCHRONOUSLY-created record, not
on run completion.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.comm.base import InboundMessage
from iron_jarvis.comm.inbound import InboundPoller
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Session
from iron_jarvis.daemon.app import create_app
from iron_jarvis.workflows.models import WorkflowRunRecord
from iron_jarvis.workflows.store import WorkflowStore

_STEPS = [{"agent": "builder", "task": "say hi"}]


def _seed_nightly(p) -> None:
    """Persist a saved 'nightly' workflow for reflex/command bindings to run."""
    WorkflowStore(p.engine).save("nightly", _STEPS, description="t")


def _run_count(p, name: str = "nightly") -> int:
    with session_scope(p.engine) as db:
        return len(
            list(
                db.exec(
                    select(WorkflowRunRecord).where(
                        WorkflowRunRecord.workflow_name == name
                    )
                )
            )
        )


def _total_runs(p) -> int:
    with session_scope(p.engine) as db:
        return len(list(db.exec(select(WorkflowRunRecord))))


class _FakeChannel:
    """Minimal authorized channel for the inbound-dispatch test.

    ``_handle`` only touches ``is_authorized`` + ``send`` on the channel, so a
    tiny stand-in is enough (no transport, no network).
    """

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    def is_authorized(self, sender_id: Any) -> bool:
        return True

    def send(self, message: str, **kw: Any) -> dict[str, Any]:
        self.sent.append((message, kw))
        return {"ok": True}


# --------------------------------------------------------------------------- #
# 1. STORE — CRUD + webhook/comm matching (disabled excluded, whole-word).
# --------------------------------------------------------------------------- #
def test_store_crud_and_matching(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        store = client.app.state.platform.reflex

        r = store.add(name="deploy-note", source="webhook", match="deploy", action="session")
        assert store.get(r.id) is not None
        assert any(x.id == r.id for x in store.list())

        # webhook matching is exact-slug + enabled-only.
        assert [x.id for x in store.matching_webhook("deploy")] == [r.id]
        assert store.matching_webhook("other") == []
        store.set_enabled(r.id, False)
        assert store.matching_webhook("deploy") == []  # disabled => excluded
        store.set_enabled(r.id, True)
        assert [x.id for x in store.matching_webhook("deploy")] == [r.id]

        # comm matching is a WHOLE-WORD, case-insensitive keyword.
        c = store.add(name="kw", source="comm", match="deploy", action="session")
        assert c.id in {x.id for x in store.matching_comm("time to DEPLOY now")}
        assert c.id not in {x.id for x in store.matching_comm("redeployment plan")}
        # an empty keyword matches every message.
        catch = store.add(name="catch", source="comm", match="", action="session")
        assert catch.id in {x.id for x in store.matching_comm("literally anything")}

        assert store.remove(r.id) is True
        assert store.get(r.id) is None


# --------------------------------------------------------------------------- #
# 2. WEBHOOK -> WORKFLOW (the headline): a matching webhook starts the workflow.
# --------------------------------------------------------------------------- #
def test_webhook_fires_workflow(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        router = client.app.state.reflex_router
        _seed_nightly(p)
        p.reflex.add(
            name="deploy", source="webhook", match="deploy", action="workflow", target="nightly"
        )

        results = asyncio.run(router.on_webhook("deploy", {"ref": "main"}))

        assert isinstance(results, list) and len(results) == 1
        assert results[0]["ok"] is True
        assert results[0]["kind"] == "workflow"
        assert results[0]["run_id"]
        assert _run_count(p) >= 1  # run-record created synchronously

        # No matching rule => empty result AND no new run created.
        before = _total_runs(p)
        assert asyncio.run(router.on_webhook("nomatch", {"ref": "main"})) == []
        assert _total_runs(p) == before


# --------------------------------------------------------------------------- #
# 3. WEBHOOK -> SESSION: fires a supervised session with a rendered task.
# --------------------------------------------------------------------------- #
def test_webhook_fires_session(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        router = client.app.state.reflex_router
        p.reflex.add(
            name="note", source="webhook", match="note", action="session",
            task_template="Handle: {body}",
        )

        results = asyncio.run(router.on_webhook("note", {"x": 1}))

        assert len(results) == 1
        assert results[0]["kind"] == "session"
        sid = results[0]["session_id"]
        assert sid
        with session_scope(p.engine) as db:
            assert db.get(Session, sid) is not None  # Session row exists


# --------------------------------------------------------------------------- #
# 4. EXECUTE ERROR PATH: a rule targeting a missing workflow reports an error,
#    creates no run, and the router never raises.
# --------------------------------------------------------------------------- #
def test_execute_missing_workflow_errors_without_raising(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        router = client.app.state.reflex_router
        rule = p.reflex.add(
            name="bad", source="webhook", match="x", action="workflow", target="does-not-exist"
        )

        before = _total_runs(p)
        res = asyncio.run(router.execute(rule, {"text": "", "body": "", "slug": "x"}))

        assert res["ok"] is False
        assert res.get("error")
        assert _total_runs(p) == before  # nothing started


# --------------------------------------------------------------------------- #
# 5. HTTP ROUTES — CRUD + /test + validation (400s).
# --------------------------------------------------------------------------- #
def test_http_routes_and_validation(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        _seed_nightly(client.app.state.platform)

        body = {
            "name": "wh", "source": "webhook", "match": "deploy",
            "action": "workflow", "target": "nightly",
        }
        resp = client.post("/reflex/rules", json=body)
        assert resp.status_code == 200
        rid = resp.json()["id"]

        assert any(x["id"] == rid for x in client.get("/reflex/rules").json()["rules"])

        patched = client.patch(f"/reflex/rules/{rid}", json={"enabled": False})
        assert patched.status_code == 200 and patched.json()["enabled"] is False

        tested = client.post(f"/reflex/rules/{rid}/test")
        assert tested.status_code == 200 and "ok" in tested.json()

        before = len(client.get("/reflex/rules").json()["rules"])
        assert client.delete(f"/reflex/rules/{rid}").status_code == 200
        assert len(client.get("/reflex/rules").json()["rules"]) == before - 1

        # Validation: blank webhook match, blank workflow target, bad source/action.
        assert client.post(
            "/reflex/rules", json={"source": "webhook", "match": "", "action": "session"}
        ).status_code == 400
        assert client.post(
            "/reflex/rules",
            json={"source": "comm", "match": "x", "action": "workflow", "target": ""},
        ).status_code == 400
        assert client.post(
            "/reflex/rules", json={"source": "bogus", "match": "x", "action": "session"}
        ).status_code == 400
        assert client.post(
            "/reflex/rules", json={"source": "comm", "match": "x", "action": "bogus"}
        ).status_code == 400


# --------------------------------------------------------------------------- #
# 6. COMMAND GRAMMAR — the phone operator (/help /status /workflows /run …).
# --------------------------------------------------------------------------- #
def test_command_grammar(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        ci = client.app.state.command_interpreter

        assert asyncio.run(ci.interpret("hello")) is None  # non-command => None
        assert "/status" in asyncio.run(ci.interpret("/help"))

        _seed_nightly(p)
        assert "nightly" in asyncio.run(ci.interpret("/workflows"))

        run_reply = asyncio.run(ci.interpret("/run nightly"))
        assert "nightly" in run_reply  # reports it started
        assert _run_count(p) >= 1  # and a run-record appears

        assert "No saved workflow" in asyncio.run(ci.interpret("/run nope"))
        assert isinstance(asyncio.run(ci.interpret("/agents")), str)

        status = asyncio.run(ci.interpret("/status"))
        assert "Iron Jarvis v" in status and "Model:" in status  # version + model line

        assert "Unknown command" in asyncio.run(ci.interpret("/bogus"))


# --------------------------------------------------------------------------- #
# 7. INBOUND COMM dispatches a "/command" without spawning a session.
# --------------------------------------------------------------------------- #
def test_inbound_command_dispatch(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        orch = client.app.state.orchestrator
        ci = client.app.state.command_interpreter
        poller = InboundPoller(p.notifier, orch, p.engine, command_interpreter=ci)

        ch = _FakeChannel()
        # reply_to == sender_id => the sender's own 1:1 chat (passes the private guard).
        msg = InboundMessage(sender_id="777", text="/status", update_id=1, reply_to="777")

        before = len(orch.list_sessions())
        res = asyncio.run(poller._handle("tg", ch, msg))

        assert res["status"] == "command"
        assert res["command"] == "/status"
        assert len(orch.list_sessions()) == before  # NO session spawned
        assert ch.sent  # a reply was sent back over the channel


# --------------------------------------------------------------------------- #
# 8. RESTART SURVIVAL — rules persist across a fresh daemon on the same root.
# --------------------------------------------------------------------------- #
def test_rules_persist_across_restart(tmp_path):
    root = str(tmp_path)
    with TestClient(create_app(root)) as client:
        resp = client.post(
            "/reflex/rules",
            json={"name": "persist", "source": "webhook", "match": "deploy", "action": "session"},
        )
        assert resp.status_code == 200
        rid = resp.json()["id"]

    # A brand-new app on the SAME root (a restart) still lists the rule.
    with TestClient(create_app(root)) as client2:
        rules = client2.get("/reflex/rules").json()["rules"]
        assert any(x["id"] == rid for x in rules)
