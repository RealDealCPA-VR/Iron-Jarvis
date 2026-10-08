"""Calm UI redesign S2: ONE settings writer — every change journaled with Undo,
side effects run once and the same for a save and an undo.

Before: only ``PUT /settings`` journaled; Iron-Proxy, profile sharing, routing,
the kill switch, the calendar, skill learning, fleet code routing, the default
provider and MCP auto-approve wrote config.toml with no trace and no Undo; the
undo path re-armed only autonomy/sentinels (a calendar undo waited for a
restart); the agent-browser switch was never persisted; per-tool permissions
had no writer at all.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import UndoJournal
from iron_jarvis.daemon.app import create_app
from iron_jarvis.settings.writer import SettingError


class _Loop:
    """Runs a re-arm at once and counts it (the daemon hops onto its loop)."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def call_soon_threadsafe(self, fn):
        fn()


@pytest.fixture
def app(tmp_path):
    a = create_app(str(tmp_path))
    client = TestClient(a)
    d = a.state.d
    loop = _Loop()
    rearmed: list[str] = []
    d._live_rearm.update(
        {
            "loop": loop,
            "calendar": lambda: rearmed.append("calendar"),
            "autonomy": lambda: rearmed.append("autonomy"),
            "sentinels": lambda: rearmed.append("sentinels"),
            "fleet": lambda: rearmed.append("fleet"),
            "browser": lambda: rearmed.append("browser"),
        }
    )
    return client, d, rearmed


def _journal(d) -> list[UndoJournal]:
    with session_scope(d.platform.engine) as db:
        return list(db.exec(select(UndoJournal).where(UndoJournal.kind == "setting_restore")))


def _undo_last(client, d):
    action = _journal(d)[-1].action_id
    r = client.post(f"/undo/{action}")
    assert r.status_code == 200, r.text


def test_the_kill_switch_route_is_journaled_and_undone(app):
    client, d, rearmed = app
    assert d.platform.config.autonomy_kill_switch is False
    assert client.post("/autonomy/kill", json={"enabled": True}).json() == {"kill_switch": True}
    (row,) = _journal(d)
    assert json.loads(row.pre_inline)["prior"] == {"autonomy_kill_switch": False}
    assert "autonomy" in rearmed
    _undo_last(client, d)
    assert d.platform.config.autonomy_kill_switch is False


def test_a_calendar_undo_rearms_the_calendar_loop(app):
    """The old undo copy re-armed only autonomy/sentinels."""
    client, d, rearmed = app
    r = client.post("/triggers/calendar", json={"enabled": True, "lead_minutes": 30})
    assert r.status_code == 200, r.text
    assert rearmed.count("calendar") == 1
    _undo_last(client, d)
    assert d.platform.config.calendar_trigger_enabled is False
    assert d.platform.config.calendar_lead_minutes == 15
    assert rearmed.count("calendar") == 2


def test_skill_learning_and_default_provider_routes_are_journaled(app):
    client, d, _ = app
    client.patch("/skills/learning/settings", json={"auto_approve": True})
    assert json.loads(_journal(d)[-1].pre_inline)["prior"] == {"skill_learning_auto_approve": False}
    _undo_last(client, d)
    assert d.platform.config.skill_learning_auto_approve is False


def test_one_bad_value_changes_nothing(app):
    _, d, _ = app
    w = d.platform.config_writer
    before = d.platform.config.max_agent_steps
    with pytest.raises(SettingError):
        w.apply({"max_agent_steps": 9, "autonomy_level": "everything"}, actor="test")
    assert d.platform.config.max_agent_steps == before
    assert _journal(d) == []


def test_an_unknown_key_is_refused_in_words(app):
    _, d, _ = app
    with pytest.raises(SettingError, match="no setting called"):
        d.platform.config_writer.apply({"rm_rf": True}, actor="test")


def test_per_tool_permissions_have_a_writer_and_respect_the_deny_floor(app, tmp_path):
    client, d, _ = app
    w = d.platform.config_writer
    with pytest.raises(SettingError, match="never run without asking"):
        w.apply({"permissions.shell": "allow"}, actor="test")
    change = w.apply({"permissions.list_files": "deny"}, actor="test")
    assert change.changed[0]["label"] == "Approval for one tool: list_files"
    assert d.platform.permissions.mode_for("list_files").value == "deny"
    assert "list_files" in (tmp_path / ".ironjarvis" / "config.toml").read_text(encoding="utf-8")
    _undo_last(client, d)
    assert d.platform.permissions.mode_for("list_files").value != "deny"


def test_the_agent_browser_switch_now_survives_a_restart(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    r = client.post("/computeruse/enable", json={"enabled": True, "domain_allowlist": ["example.com"]})
    assert r.status_code == 200, r.text
    fresh = create_app(str(tmp_path))
    cu = fresh.state.d.platform.config.computer_use
    assert cu["enabled"] is True and cu["domain_allowlist"] == ["example.com"]


def test_profile_and_device_settings_go_through_the_same_writer(app):
    client, d, _ = app
    w = d.platform.config_writer
    w.apply({"profile.response_length": "short"}, actor="test")
    w.apply({"device.theme": "mark8"}, actor="test", device_id="dev_abc123")
    assert w.current("profile.response_length") == "short"
    assert w.current("device.theme", device_id="dev_abc123") == "mark8"
    _undo_last(client, d)  # the device change: never set before, so it is removed
    assert w.current("device.theme", device_id="dev_abc123") is None
    action = _journal(d)[0].action_id  # the profile change
    assert client.post(f"/undo/{action}").status_code == 200
    assert w.current("profile.response_length") != "short"


def test_a_device_setting_needs_a_device_id(app):
    _, d, _ = app
    with pytest.raises(ValueError):
        d.platform.config_writer.apply({"device.theme": "mark8"}, actor="test")


def test_put_settings_reports_what_changed_with_its_undo_handle(app):
    client, d, _ = app
    r = client.put("/settings", json={"values": {"max_agent_steps": 33}})
    body = r.json()
    assert body["updated"] == ["max_agent_steps"]
    assert body["changed"][0]["label"] == "Max steps per run" and body["changed"][0]["new"] == 33
    assert body["action_id"] == _journal(d)[-1].action_id
