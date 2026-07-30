"""v1.122.0 — W-C: real triggers.

Two long-standing gaps closed:
  1. a freshly created inbound webhook now fires bound reflex rules
     IMMEDIATELY (the create-time handler used to skip reflexes until the
     next daemon restart installed the lifespan handler);
  2. the trigger's payload rides the workflow run as a synthetic
     ``{{Trigger}}`` output instead of being dropped.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.comm.channels import MockChannel
from iron_jarvis.daemon.app import create_app
from iron_jarvis.platform import build_platform
from iron_jarvis.reflex.router import ReflexRouter
from iron_jarvis.workflows.models import WorkflowRunRecord
from iron_jarvis.workflows.store import WorkflowStore


@pytest.fixture
def client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


def test_created_webhook_fires_reflexes_without_restart(client):
    # The classic trap: create webhook + create rule + POST → 200 ack,
    # nothing fires, and only a daemon restart "fixed" it.
    client.post(
        "/workflows",
        json={
            "name": "hook-target",
            "steps": [{"name": "Say", "kind": "notify", "message": "fired"}],
        },
    )
    r = client.post(
        "/reflex/rules",
        json={
            "name": "on-github",
            "source": "webhook",
            "match": "gh-push",
            "action": "workflow",
            "target": "hook-target",
        },
    )
    assert r.status_code == 200
    client.post("/webhooks", json={"slug": "gh-push", "direction": "inbound"})
    hit = client.post("/webhooks/gh-push", json={"text": "push to master"}).json()
    assert hit["ok"] is True
    assert hit["reflexes_fired"] == 1  # NOT zero-until-restart


async def test_trigger_injection_steps_aside_for_a_real_trigger_step(tmp_path):
    # Reserved-name guard: a def with an ACTUAL step named "Trigger" must not
    # have that step pre-marked completed by the synthetic injection.
    platform = build_platform(str(tmp_path))
    WorkflowStore(platform.engine).save(
        "collide",
        [
            {"name": "Trigger", "kind": "notify", "message": "I am a real step"},
        ],
    )
    router = ReflexRouter(platform, Orchestrator(platform), spawn_bg=None)
    router.store.add(
        name="hook2", source="webhook", match="c", action="workflow", target="collide"
    )
    fired = await router.on_webhook("c", {"text": "payload"})
    run_id = fired[0]["run_id"]
    from iron_jarvis.core.db import session_scope

    for _ in range(200):
        await asyncio.sleep(0.02)
        with session_scope(platform.engine) as db:
            status = db.get(WorkflowRunRecord, run_id).status
        if status != "running":
            break
    assert status == "completed"
    with session_scope(platform.engine) as db:
        outs = json.loads(db.get(WorkflowRunRecord, run_id).outputs_json)
    # The REAL step's own output won — no phantom pre-completed entry.
    assert outs["Trigger"]["kind"] == "notify"
    assert "I am a real step" in outs["Trigger"]["summary"]


async def test_agent_created_webhook_fires_reflexes_too(tmp_path):
    # The same skip-until-restart bug lived in the agent-facing webhook_add
    # tool; its handler must fire reflexes when platform.reflex_router is set.
    from iron_jarvis.tools.base import ToolContext
    from iron_jarvis.webhooks.tools import WebhookAddTool

    platform = build_platform(str(tmp_path))
    platform.reflex_router = ReflexRouter(platform, Orchestrator(platform), spawn_bg=None)
    WorkflowStore(platform.engine).save(
        "agent-hooked",
        [{"name": "Say", "kind": "notify", "message": "via agent webhook"}],
    )
    platform.reflex_router.store.add(
        name="agent-rule", source="webhook", match="agent-slug",
        action="workflow", target="agent-hooked",
    )
    tool = WebhookAddTool(platform)
    ctx = ToolContext(
        workspace=tmp_path, session_id="s", agent_run_id="r",
        config=platform.config, event_bus=platform.event_bus, engine=platform.engine,
    )
    res = await tool.execute({"slug": "agent-slug", "direction": "inbound"}, ctx)
    assert res.ok
    out = await platform.inbound_webhooks.dispatch("agent-slug", {"text": "go"})
    assert out.get("reflexes_fired") == 1


async def test_trigger_payload_reaches_workflow_steps(tmp_path):
    platform = build_platform(str(tmp_path))
    mock = next(
        ch for ch in platform.notifier._channels.values() if isinstance(ch, MockChannel)
    )
    WorkflowStore(platform.engine).save(
        "announce-push",
        [{"name": "Tell", "kind": "notify", "message": "Got: {{Trigger}}"}],
    )
    router = ReflexRouter(platform, Orchestrator(platform), spawn_bg=None)
    router.store.add(
        name="hook",
        source="webhook",
        match="gh",
        action="workflow",
        target="announce-push",
    )
    fired = await router.on_webhook("gh", {"text": "hello from github"})
    assert fired and fired[0]["ok"] is True
    run_id = fired[0]["run_id"]
    # spawn_bg=None → ensure_future on this loop; let it drive to completion.
    from iron_jarvis.core.db import session_scope

    for _ in range(200):
        await asyncio.sleep(0.02)
        with session_scope(platform.engine) as db:
            rec = db.get(WorkflowRunRecord, run_id)
            status = rec.status
        if status not in ("running",):
            break
    assert status == "completed"
    # The payload flowed through the {{Trigger}} template into the step.
    assert any("Got: hello from github" in m for m in mock.sent)
    # And the record's outputs carry the synthetic Trigger output honestly.
    with session_scope(platform.engine) as db:
        rec = db.get(WorkflowRunRecord, run_id)
        outs = json.loads(rec.outputs_json)
    assert outs["Trigger"]["summary"] == "hello from github"
