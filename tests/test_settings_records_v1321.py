"""Calm UI redesign S5 — the user's RECORDS from chat, each with an Undo.

T5 (ledger and undo, "schedule" type): a schedule changed, removed or made
from chat goes through the real stream lane and the real permission gate,
writes a ledger row, rides the reply as a card, and Undo puts the record back
exactly as it was. The same for saved workflows (update / delete / schedule),
a notification channel's two-way switch, and connecting a channel or an app —
where the token arrives through the secure card, never as a tool argument.
"""

from __future__ import annotations

import json

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.settings import records
from tests.test_chat_approvals_v1187 import _asgi_post, _drive_stream
from tests.test_settings_chat_tools_v1321 import _body, _calls_stream, _done, _undo_card

TOKEN = "123456789:AAbbCCddEEffGGhhIIjjKKllMMnnOOpp-NEVER"


async def _approve_all(app, seen):
    async def approve(data):
        seen.append(data["tool"])
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "once"})
        assert status == 200

    return approve


def _app(tmp_path, specs):
    app = create_app(str(tmp_path))
    app.state.platform.router.stream = _calls_stream(specs)
    return app


def _card(frames, title):
    cards = [c for c in _done(frames)["config_cards"] if c.get("title") == title]
    assert len(cards) == 1, _done(frames)["config_cards"]
    return cards[0]


# ------------------------------------------------------------ schedules


@pytest.mark.asyncio
async def test_t5_schedule_change_asks_applies_and_undo_restores_it(tmp_path):
    app = _app(tmp_path, [("schedule_update", {"name": "nightly", "cron": "0 7 * * *", "payload": {"workflow": "other"}})])
    sch = app.state.platform.scheduler
    sch.add_task("nightly", "0 2 * * *", kind="workflow", payload={"workflow": "brief"})
    seen: list[str] = []
    frames = await _drive_stream(app, _body("move my nightly schedule to 7am"), await _approve_all(app, seen))
    assert seen == ["schedule_update"], "a schedule change must ask first"
    assert sch.get("nightly").cron == "0 7 * * *"
    assert sch.get("nightly").decoded_payload() == {"workflow": "other"}
    card = _card(frames, "Schedule changed")
    assert card["old"] == "cron 0 2 * * *" and card["new"] == "cron 0 7 * * *"
    await _undo_card(app, card)
    rec = sch.get("nightly")
    assert rec.cron == "0 2 * * *" and rec.decoded_payload() == {"workflow": "brief"}


@pytest.mark.asyncio
async def test_t5_schedule_removed_from_chat_comes_back_on_undo(tmp_path):
    app = _app(tmp_path, [("schedule_delete", {"name": "weekly"})])
    sch = app.state.platform.scheduler
    sch.add_task("weekly", "0 9 * * 1", kind="workflow", payload={"workflow": "report"}, enabled=False)
    seen: list[str] = []
    frames = await _drive_stream(app, _body("delete the weekly schedule"), await _approve_all(app, seen))
    assert seen == ["schedule_delete"] and sch.get("weekly") is None
    await _undo_card(app, _card(frames, "Schedule removed"))
    rec = sch.get("weekly")
    assert rec is not None and rec.cron == "0 9 * * 1" and rec.enabled is False
    assert rec.decoded_payload() == {"workflow": "report"}


@pytest.mark.asyncio
async def test_t5_a_schedule_created_from_chat_has_an_undo(tmp_path):
    app = _app(tmp_path, [("schedule_create", {"name": "standup", "cron": "30 8 * * 1-5", "kind": "event", "payload": {"type": "x"}})])

    async def never(data):  # pragma: no cover
        raise AssertionError(f"schedule_create is allow tier: {data}")

    frames = await _drive_stream(app, {**_body("every weekday at 8:30 remind me"), "tools": ["schedule_create"]}, never)
    assert app.state.platform.scheduler.get("standup") is not None
    await _undo_card(app, _card(frames, "Schedule created"))
    assert app.state.platform.scheduler.get("standup") is None


# ------------------------------------------------------------ workflows


@pytest.mark.asyncio
async def test_workflow_rename_and_steps_undo_puts_back_name_and_steps(tmp_path):
    from iron_jarvis.workflows.store import WorkflowStore

    app = _app(
        tmp_path,
        [("workflow_update", {"name": "Brief", "new_name": "Morning brief", "steps": [{"name": "s", "agent": "builder", "task": "new"}]})],
    )
    store = WorkflowStore(app.state.platform.engine)
    store.save("Brief", [{"name": "a", "agent": "builder", "task": "old"}], "the old one")
    seen: list[str] = []
    frames = await _drive_stream(app, _body("rename my Brief workflow and change its step"), await _approve_all(app, seen))
    assert seen == ["workflow_update"]
    assert store.get("Brief") is None and json.loads(store.get("Morning brief").steps_json)[0]["task"] == "new"
    await _undo_card(app, _card(frames, "Workflow changed"))
    assert store.get("Morning brief") is None
    back = store.get("Brief")
    assert json.loads(back.steps_json)[0]["task"] == "old" and back.description == "the old one"


@pytest.mark.asyncio
async def test_workflow_delete_and_schedule_both_undo(tmp_path):
    from iron_jarvis.workflows.store import WorkflowStore

    app = _app(
        tmp_path,
        [
            ("workflow_schedule", {"workflow": "Tidy", "cron": "0 6 * * *"}),
            ("workflow_delete", {"name": "Tidy"}),
        ],
    )
    store = WorkflowStore(app.state.platform.engine)
    store.save("Tidy", [{"name": "a", "agent": "builder", "task": "tidy"}], "")
    seen: list[str] = []
    frames = await _drive_stream(app, _body("schedule my Tidy workflow daily, then delete the workflow"), await _approve_all(app, seen))
    assert seen == ["workflow_schedule", "workflow_delete"]
    sched = app.state.platform.scheduler.get("Tidy")
    assert sched is not None and sched.decoded_payload() == {"workflow": "Tidy"}
    assert store.get("Tidy") is None
    await _undo_card(app, _card(frames, "Workflow removed"))
    assert store.get("Tidy") is not None
    await _undo_card(app, _card(frames, "Workflow scheduled"))
    assert app.state.platform.scheduler.get("Tidy") is None


# ------------------------------------------------------------- channels


def _add_channel(app, name, cfg):
    records.apply_channel(app.state.platform, name, cfg)


@pytest.mark.asyncio
async def test_channel_two_way_switch_asks_and_undoes(tmp_path):
    app = _app(tmp_path, [("channel_toggle", {"name": "phone", "mode": "chat"})])
    _add_channel(app, "phone", {"type": "telegram", "chat_id": "42", "token_secret": "channel_phone_token", "allowed_senders": ["42"]})
    seen: list[str] = []
    frames = await _drive_stream(app, _body("let me chat with you from telegram"), await _approve_all(app, seen))
    assert seen == ["channel_toggle"]
    ch = app.state.platform.config.comm["channels"]["phone"]
    assert ch["chat_enabled"] is True and ch["inbound_enabled"] is True
    await _undo_card(app, _card(frames, "Channel changed"))
    ch = app.state.platform.config.comm["channels"]["phone"]
    assert not ch.get("chat_enabled") and not ch.get("inbound_enabled")
    assert app.state.platform.notifier.get("phone") is not None


@pytest.mark.asyncio
async def test_channel_two_way_refuses_without_an_allowlist(tmp_path):
    app = _app(tmp_path, [("channel_toggle", {"name": "open", "mode": "two_way"})])
    _add_channel(app, "open", {"type": "telegram", "chat_id": "42", "token_secret": "channel_open_token"})
    seen: list[str] = []
    frames = await _drive_stream(app, _body("turn on two-way for the telegram channel"), await _approve_all(app, seen))
    assert not app.state.platform.config.comm["channels"]["open"].get("inbound_enabled")
    assert _done(frames)["config_cards"] == []


@pytest.mark.asyncio
async def test_channel_connect_token_through_the_card_never_the_model(tmp_path):
    app = _app(tmp_path, [("channel_connect", {"name": "phone", "type": "telegram", "chat_id": "42"})])
    p = app.state.platform
    seen: list[str] = []
    frames = await _drive_stream(app, _body("connect my telegram so you can message me"), await _approve_all(app, seen))
    assert seen == ["channel_connect"]
    cards = _done(frames)["config_cards"]
    secret = next(c for c in cards if c["kind"] == "secret")
    assert secret["name"] == "channel.phone"
    status, raw = await _asgi_post(app, "/config/secret", {"name": "channel.phone", "value": TOKEN})
    assert status == 200 and TOKEN.encode() not in raw
    assert p.secrets.get("channel_phone_token") == TOKEN
    assert p.config.comm["channels"]["phone"]["token_secret"] == "channel_phone_token"
    assert TOKEN not in json.dumps(frames, default=str)
    await _undo_card(app, _card(frames, "Channel added"))
    assert "phone" not in (p.config.comm.get("channels") or {})


# ----------------------------------------------------------------- apps


@pytest.mark.asyncio
async def test_app_connect_waits_for_its_token_then_loads_and_undo_disconnects(tmp_path, monkeypatch):
    loads: list[str] = []
    monkeypatch.setattr(records, "load_app", lambda platform, cfg: loads.append(cfg["name"]) or 3)
    app = _app(tmp_path, [("app_connect", {"app": "notion"})])
    p = app.state.platform
    seen: list[str] = []
    frames = await _drive_stream(app, _body("connect my Notion"), await _approve_all(app, seen))
    assert seen == ["app_connect"]
    cfg = records.snap_app(p, "notion")
    assert cfg is not None and cfg["env_secrets"] == {"NOTION_TOKEN": "conn_notion_notion_token"}
    assert loads == [], "a pack waiting for its token is not launched yet"
    secret = next(c for c in _done(frames)["config_cards"] if c["kind"] == "secret")
    assert secret["name"] == "app.notion"
    status, _ = await _asgi_post(app, "/config/secret", {"name": "app.notion", "value": "ntn_secret_value_123456"})
    assert status == 200 and p.secrets.get("conn_notion_notion_token") == "ntn_secret_value_123456"
    assert loads == ["notion"], "the saved token loads the pack"
    await _undo_card(app, _card(frames, "App connected"))
    assert records.snap_app(p, "notion") is None


# ------------------------------------------------------------- arming


def test_record_tools_arm_by_the_record_the_message_names():
    assert "schedule_update" in records.record_tools_for("move my nightly schedule to 7am")
    assert "workflow_delete" in records.record_tools_for("delete the Tidy workflow")
    assert "channel_toggle" in records.record_tools_for("turn off two-way on telegram")
    assert records.record_tools_for("connect my Notion") == ["app_connect"]
    assert records.record_tools_for("summarize the K-1 I attached") == []


@pytest.mark.asyncio
async def test_the_stream_lane_shows_record_tools_but_never_grants_them(tmp_path):
    app = create_app(str(tmp_path))
    fake = _calls_stream([])
    app.state.platform.router.stream = fake

    async def never(data):  # pragma: no cover
        raise AssertionError(data)

    await _drive_stream(app, _body("delete the weekly schedule"), never)
    assert {"schedule_update", "schedule_delete"} <= set(fake.tools[0])
    perms = app.state.platform.permissions
    assert perms.authorize("schedule_delete", {}).allowed is False
