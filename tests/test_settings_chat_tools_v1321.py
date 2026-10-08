"""Calm UI redesign S3/S4 — settings and credentials from chat.

T4 (config parity): every key in the one settings schema has a chat tool for
its tier, and every credential kind resolves through ``config_secret``.

T5 (ledger and undo): a change made in chat — bool, enum, string, and a
connection credential — goes through the real stream lane and the real
permission gate, writes a ledger row, rides the reply as a "Setting changed"
card, and Undo restores the prior value. A protected change is carded even in
Auto-approve and never offered "Always".

T6 (secret handling): a credential set through chat never reaches the
transcript, the ledger, an event, the undo journal or a log.
"""

from __future__ import annotations

import json
import logging

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import EventRecord, ToolInvocation, UndoJournal
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.settings import schema
from iron_jarvis.settings.credentials import resolve_secret
from iron_jarvis.settings.tools import TOOL_FOR_TIER, wants_settings
from tests.test_chat_approvals_v1187 import _asgi_post, _drive_stream

SECRET = "sk-test-NEVER-IN-THE-CHAT-0123456789"


def _calls_stream(specs):
    rounds = {"n": 0}

    async def fake_stream(*, provider=None, model=None, system, messages, tools, session_id=None, task_class=None, **kw):
        fake_stream.tools.append([getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else None) for t in tools or []])
        i = rounds["n"]
        if i < len(specs):
            rounds["n"] += 1
            name, args = specs[i]
            yield {
                "type": "final",
                "response": LLMResponse(text="", tool_calls=[ToolCall(id=f"c{i}", name=name, arguments=args)]),
                "provider": "mock", "model": "mock",
            }
        else:
            yield {"type": "final", "response": LLMResponse(text="Done."), "provider": "mock", "model": "mock"}

    fake_stream.tools = []
    return fake_stream


def _body(text: str, mode: str = "approve_for_me", **extra) -> dict:
    return {"messages": [{"role": "user", "content": text}], "auto_tools": True, "approval_mode": mode, **extra}


def _done(frames):
    return next(d for ev, d in frames if ev == "done")


async def _undo_card(app, card):
    from fastapi.testclient import TestClient

    client = TestClient(app)
    look = client.get(f"/config/changes/{card['change_id']}")
    assert look.status_code == 200, look.text
    r = client.post(f"/undo/{look.json()['action_id']}")
    assert r.status_code == 200, r.text


# ------------------------------------------------------------------ T4


def test_t4_every_schema_key_has_a_chat_tool_for_its_tier(tmp_path):
    app = create_app(str(tmp_path))
    reg = app.state.platform.registry
    missing = []
    for d in schema.SETTINGS:
        for value in (None, *d.floor_when):
            tool = TOOL_FOR_TIER[d.tier if value is None else d.tier_for(value)]
            if reg.get(tool) is None:
                missing.append((d.key, tool))
    assert missing == []
    assert reg.get("config_list") is not None and reg.get("config_secret") is not None
    # Every credential kind resolves to a vault name the card can write.
    for s in schema.SECRETS:
        # An app token resolves only for a real Directory app (S5: it is the
        # vault name that app's pack launches with).
        arg = "notion" if s.name.startswith("app.") else "telegram"
        name = s.name.replace("{provider}", "openai").replace("{name}", arg)
        assert resolve_secret(name)[2]
    assert resolve_secret("app.notion")[2] == "conn_notion_notion_token"
    assert resolve_secret("app.box__BOX_CLIENT_SECRET")[2] == "conn_box_box_client_secret"


def test_t4_config_list_finds_settings_by_words(tmp_path):
    import asyncio

    from iron_jarvis.tools.base import ToolContext

    app = create_app(str(tmp_path))
    p = app.state.platform
    ctx = ToolContext(workspace=tmp_path, session_id="chat", agent_run_id="chat", config=p.config, event_bus=p.event_bus, engine=p.engine)
    res = asyncio.run(p.registry.get("config_list").execute({"query": "telegram"}, ctx))
    assert any(s["key"].startswith("channel.") for s in res.data["secrets"])
    res = asyncio.run(p.registry.get("config_list").execute({"query": "kill switch"}, ctx))
    assert [r["key"] for r in res.data["settings"]] == ["autonomy_kill_switch"]
    assert res.data["settings"][0]["value"] is False


def test_the_settings_vocabulary_arms_on_settings_words_only():
    assert wants_settings("turn on telegram notifications")
    assert wants_settings("use the local qwen model for coding tasks")
    assert wants_settings("connect my Notion")
    assert not wants_settings("summarize the K-1 I attached")


# ------------------------------------------------------------------ T5


@pytest.mark.asyncio
async def test_t5_bool_allow_tier_applies_without_a_card_and_undoes(tmp_path):
    app = create_app(str(tmp_path))
    cfg = app.state.platform.config
    cfg.autonomy_dry_run = False
    app.state.platform.router.stream = _calls_stream([("config_set", {"key": "autonomy_dry_run", "value": "on"})])

    async def never(data):  # pragma: no cover
        raise AssertionError(f"an allow-tier setting asked: {data}")

    frames = await _drive_stream(app, _body("turn on dry run mode for autonomy"), never)
    done = _done(frames)
    (card,) = done["config_cards"]
    assert card["kind"] == "change" and card["key"] == "autonomy_dry_run"
    assert card["old"] is False and card["new"] is True and card["change_id"]
    assert cfg.autonomy_dry_run is True
    await _undo_card(app, card)
    assert cfg.autonomy_dry_run is False


@pytest.mark.asyncio
async def test_t5_string_ask_tier_cards_then_applies_and_undoes(tmp_path):
    app = create_app(str(tmp_path))
    cfg = app.state.platform.config
    before = cfg.max_agent_steps
    app.state.platform.router.stream = _calls_stream([("config_change", {"key": "max_agent_steps", "value": "33"})])
    seen = []

    async def approve(data):
        seen.append((data["tool"], data.get("can_always")))
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "once"})
        assert status == 200

    frames = await _drive_stream(app, _body("change the max steps setting to 33"), approve)
    assert [t for t, _ in seen] == ["config_change"]
    assert cfg.max_agent_steps == 33
    (card,) = _done(frames)["config_cards"]
    await _undo_card(app, card)
    assert cfg.max_agent_steps == before


@pytest.mark.asyncio
async def test_t5_enum_protected_value_cards_even_in_auto_approve_and_never_always(tmp_path):
    app = create_app(str(tmp_path))
    cfg = app.state.platform.config
    assert cfg.local_primary_policy == "refuse"
    app.state.platform.router.stream = _calls_stream(
        [("config_change_protected", {"key": "local_primary_policy", "value": "failover"})]
    )
    seen = []

    async def approve(data):
        seen.append((data["tool"], data.get("can_always")))
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "once"})
        assert status == 200

    frames = await _drive_stream(app, _body("let another model answer if my local model fails, change that setting", "yolo"), approve)
    assert seen == [("config_change_protected", False)], "a protected change must card in yolo, without Always"
    assert cfg.local_primary_policy == "failover"
    (card,) = _done(frames)["config_cards"]
    await _undo_card(app, card)
    assert cfg.local_primary_policy == "refuse"


@pytest.mark.asyncio
async def test_a_protected_value_cannot_ride_the_allow_tool(tmp_path):
    app = create_app(str(tmp_path))
    cfg = app.state.platform.config
    cfg.autonomy_kill_switch = True
    app.state.platform.router.stream = _calls_stream([("config_set", {"key": "autonomy_kill_switch", "value": False})])

    async def never(data):  # pragma: no cover
        raise AssertionError(data)

    frames = await _drive_stream(app, _body("turn off the kill switch setting"), never)
    assert cfg.autonomy_kill_switch is True
    finished = next(d for ev, d in frames if ev == "tool_call" and d.get("status") == "finished")
    assert finished["ok"] is False and "config_change_protected" in (finished.get("output") or finished.get("error") or "")
    assert _done(frames)["config_cards"] == []


@pytest.mark.asyncio
async def test_a_device_setting_lands_on_the_device_that_asked(tmp_path):
    app = create_app(str(tmp_path))
    app.state.platform.router.stream = _calls_stream([("config_set", {"key": "device.theme", "value": "mark8"})])

    async def never(data):  # pragma: no cover
        raise AssertionError(data)

    await _drive_stream(app, _body("switch the theme to liquid glass", device_id="dev_phone01"), never)
    w = app.state.platform.config_writer
    assert w.current("device.theme", device_id="dev_phone01") == "mark8"
    assert w.current("device.theme", device_id="dev_desk001") is None


# ------------------------------------------------------------- T5 + T6


@pytest.mark.asyncio
async def test_t6_a_credential_from_chat_never_touches_the_transcript_ledger_events_or_logs(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    app = create_app(str(tmp_path))
    p = app.state.platform
    app.state.platform.router.stream = _calls_stream([("config_secret", {"name": "connection.openai", "why": "to use GPT"})])

    async def never(data):  # pragma: no cover
        raise AssertionError(data)

    frames = await _drive_stream(app, _body("connect my OpenAI api key"), never)
    (card,) = _done(frames)["config_cards"]
    assert card["kind"] == "secret" and card["name"] == "connection.openai"
    # The user pastes into the CARD, which posts straight to the daemon.
    status, raw = await _asgi_post(app, "/config/secret", {"name": "connection.openai", "value": SECRET})
    body = json.loads(raw)
    assert status == 200, body
    assert body["status"] == "stored" and SECRET not in json.dumps(body)
    assert p.connections.has_credential("openai")
    # T6: nowhere but the encrypted vault.
    with session_scope(p.engine) as db:
        rows = [*db.exec(select(ToolInvocation)), *db.exec(select(UndoJournal)), *db.exec(select(EventRecord))]
        dumped = json.dumps([r.model_dump() for r in rows], default=str)
    assert SECRET not in dumped
    assert SECRET not in json.dumps(frames, default=str)
    assert SECRET not in caplog.text
    # T5 (connection): Undo removes the key that did not exist before.
    with session_scope(p.engine) as db:
        undo = db.exec(select(UndoJournal).where(UndoJournal.kind == "secret_restore")).first()
    from fastapi.testclient import TestClient

    assert TestClient(app).post(f"/undo/{undo.action_id}").status_code == 200
    assert not p.connections.has_credential("openai")


@pytest.mark.asyncio
async def test_t6_replacing_a_credential_keeps_the_old_one_encrypted_for_undo(tmp_path):
    app = create_app(str(tmp_path))
    p = app.state.platform
    p.secrets.set("voice_transcribe_key", "old-value-1", kind="api_key")
    status, raw = await _asgi_post(app, "/config/secret", {"name": "voice_transcribe_key", "value": SECRET})
    assert status == 200 and json.loads(raw)["status"] == "replaced"
    assert p.secrets.get("voice_transcribe_key") == SECRET
    with session_scope(p.engine) as db:
        undo = db.exec(select(UndoJournal).where(UndoJournal.kind == "secret_restore")).first()
        assert "old-value-1" not in (undo.pre_inline or "") and SECRET not in (undo.pre_inline or "")
    from fastapi.testclient import TestClient

    assert TestClient(app).post(f"/undo/{undo.action_id}").status_code == 200
    assert p.secrets.get("voice_transcribe_key") == "old-value-1"
    assert not [s for s in p.secrets.list() if s["name"].startswith("__undo__")]


def test_the_secret_route_refuses_unknown_names_and_never_echoes_input(tmp_path):
    from fastapi.testclient import TestClient

    client = TestClient(create_app(str(tmp_path)))
    r = client.post("/config/secret", json={"name": "../../etc", "value": SECRET})
    assert r.status_code == 400 and SECRET not in r.text
    r = client.post("/config/secret", json={"name": "connection.openai", "value": 12345})
    assert r.status_code == 400 and "12345" not in r.text


@pytest.mark.asyncio
async def test_s4_secret_set_armed_in_chat_becomes_the_secure_card(tmp_path):
    """A chat that arms `secret_set` (the Tools picker) is handed
    `config_secret` instead: in chat a credential never rides a tool argument
    the model writes. Agent runs keep `secret_set`."""
    app = create_app(str(tmp_path))
    fake = _calls_stream([])
    app.state.platform.router.stream = fake

    async def never(data):  # pragma: no cover
        raise AssertionError(data)

    await _drive_stream(app, {**_body("store this for me"), "tools": ["secret_set"], "auto_tools": False}, never)
    offered = fake.tools[0]
    assert "secret_set" not in offered
    assert "config_secret" in offered
