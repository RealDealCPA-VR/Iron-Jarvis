"""Agent self-service tools: schedule_create, webhook_add, workflow_create.

These tools let an agent provision its own scheduled tasks, webhooks, and saved
workflows through the tool loop. Each is constructed with the assembled
``platform`` (like ``DelegateTool``) and acts on a platform subsystem. The tests
run fully offline against a real ``build_platform`` and invoke the tools directly
(bypassing the permission gate, which is exercised elsewhere).
"""

from __future__ import annotations

# Register the WorkflowRecord (+ WorkflowRunRecord) tables on SQLModel.metadata
# BEFORE init_db creates the schema. Must stay at the top.
import iron_jarvis.workflows.models  # noqa: F401

import json
from datetime import datetime, timedelta, timezone

from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.platform import build_platform
from iron_jarvis.scheduling.tools import ScheduleCreateTool, schedule_tools
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.webhooks.models import WebhookRecord
from iron_jarvis.webhooks.tools import WebhookAddTool, webhook_tools
from iron_jarvis.workflows.models import WorkflowRecord
from iron_jarvis.workflows.store import WorkflowStore
from iron_jarvis.workflows.tools import WorkflowCreateTool, workflow_tools


def _platform(tmp_path):
    return build_platform(str(tmp_path))


def _ctx(platform, tmp_path) -> ToolContext:
    return ToolContext(
        workspace=tmp_path,
        session_id="s_test",
        agent_run_id="r_test",
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )


# --- factories -------------------------------------------------------------


def test_factories_build_named_tools(tmp_path):
    platform = _platform(tmp_path)
    assert [t.name for t in schedule_tools(platform)] == ["schedule_create"]
    assert [t.name for t in webhook_tools(platform)] == ["webhook_add"]
    assert [t.name for t in workflow_tools(platform)] == ["workflow_create"]


# --- schedule_create -------------------------------------------------------


async def test_schedule_create_cron_and_run_at(tmp_path):
    platform = _platform(tmp_path)
    tool = ScheduleCreateTool(platform)
    ctx = _ctx(platform, tmp_path)

    # A recurring cron task.
    res = await tool.execute(
        {"name": "nightly", "cron": "0 0 * * *", "kind": "event",
         "payload": {"type": "schedule.fired"}},
        ctx,
    )
    assert res.ok
    assert res.data["next_run"] is not None
    assert "nightly" in {t.name for t in platform.scheduler.list()}

    # A one-time run_at task (ISO string).
    run_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    res2 = await tool.execute(
        {"name": "oneshot", "run_at": run_at, "kind": "event"}, ctx
    )
    assert res2.ok
    assert res2.data["trigger_type"] == "date"
    names = {t.name for t in platform.scheduler.list()}
    assert {"nightly", "oneshot"} <= names


async def test_schedule_create_bad_cron_returns_not_ok(tmp_path):
    platform = _platform(tmp_path)
    tool = ScheduleCreateTool(platform)
    ctx = _ctx(platform, tmp_path)

    res = await tool.execute({"name": "bad", "cron": "not a cron"}, ctx)
    assert res.ok is False
    assert res.error and "cron" in res.error.lower()
    # Nothing persisted for the rejected task.
    assert platform.scheduler.get("bad") is None


# --- webhook_add (inbound) -------------------------------------------------


async def test_webhook_add_inbound_registers_and_dispatches(tmp_path):
    platform = _platform(tmp_path)
    tool = WebhookAddTool(platform)
    ctx = _ctx(platform, tmp_path)

    res = await tool.execute({"slug": "ingest", "direction": "inbound"}, ctx)
    assert res.ok
    assert res.data == {"slug": "ingest", "direction": "inbound"}

    # The registered default handler runs and publishes webhook.received.
    seen: list = []
    platform.event_bus.add_handler(lambda ev: seen.append(ev))
    handled = await platform.inbound_webhooks.dispatch("ingest", {"hello": "world"})
    assert handled.get("ok") is True
    assert any(
        ev.type == "webhook.received" and ev.payload.get("body") == {"hello": "world"}
        for ev in seen
    )

    # A durable WebhookRecord row exists for the inbound slug.
    with session_scope(platform.engine) as db:
        rows = db.exec(
            select(WebhookRecord).where(WebhookRecord.slug == "ingest")
        ).all()
    assert len(rows) == 1
    assert rows[0].direction == "inbound"


async def test_webhook_add_outbound_requires_target_url(tmp_path):
    platform = _platform(tmp_path)
    tool = WebhookAddTool(platform)
    ctx = _ctx(platform, tmp_path)

    # Missing target_url -> ok=False, nothing registered.
    bad = await tool.execute({"slug": "deliver", "direction": "outbound"}, ctx)
    assert bad.ok is False
    assert "target_url" in (bad.error or "")

    # With a target_url it registers an outbound row.
    good = await tool.execute(
        {"slug": "deliver", "direction": "outbound",
         "target_url": "https://example.com/hook",
         "event_types": ["workflow.completed"]},
        ctx,
    )
    assert good.ok
    with session_scope(platform.engine) as db:
        rows = db.exec(
            select(WebhookRecord).where(WebhookRecord.slug == "deliver")
        ).all()
    assert len(rows) == 1
    assert rows[0].direction == "outbound"
    assert rows[0].target_url == "https://example.com/hook"


# --- workflow_create -------------------------------------------------------


async def test_workflow_create_persists_and_upserts(tmp_path):
    platform = _platform(tmp_path)
    tool = WorkflowCreateTool(platform)
    ctx = _ctx(platform, tmp_path)
    store = WorkflowStore(platform.engine)

    steps = [{"name": "gather", "agent": "builder", "task": "collect receipts"}]
    res = await tool.execute(
        {"name": "monthly_close", "steps": steps, "description": "close books"}, ctx
    )
    assert res.ok
    assert res.data == {"name": "monthly_close", "steps": 1, "id": res.data["id"]}

    rec = store.get("monthly_close")
    assert rec is not None
    assert isinstance(rec, WorkflowRecord)
    assert rec.description == "close books"
    assert json.loads(rec.steps_json) == steps

    # Upsert: saving the same name again updates the single row (no duplicate).
    steps2 = steps + [{"name": "review", "agent": "reviewer", "task": "check"}]
    res2 = await tool.execute({"name": "monthly_close", "steps": steps2}, ctx)
    assert res2.ok and res2.data["steps"] == 2

    assert len(store.list()) == 1
    updated = store.get("monthly_close")
    assert json.loads(updated.steps_json) == steps2
    assert updated.id == rec.id  # same row, updated in place
