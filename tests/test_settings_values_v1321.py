"""Calm UI redesign S10 — the Settings page reads and writes the ONE store.

``GET /settings/values`` reads every setting (config, this device, the
profile) through the writer the chat tools use; ``PUT /settings/values``
saves any of them through that writer — validated all-or-nothing, one ledger
row, Undo. ``GET /config/ledger`` is the "Changed here or in chat" panel.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.settings import schema
from tests.test_chat_approvals_v1187 import _drive_stream
from tests.test_settings_chat_tools_v1321 import _body, _calls_stream


def test_values_cover_every_store_and_no_family_keys(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    values = client.get("/settings/values", params={"device_id": "dev_desk001"}).json()["values"]
    expected = {d.key for d in schema.SETTINGS if not d.pattern}
    assert set(values) == expected
    assert "permissions.{tool}" not in values
    assert values["autonomy_kill_switch"] is False
    assert "device.theme" in values and "profile.tone" in values


def test_one_save_across_stores_is_one_ledger_row_and_one_undo(tmp_path):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    cfg = app.state.platform.config
    before_steps = cfg.max_agent_steps
    r = client.put(
        "/settings/values",
        json={
            "device_id": "dev_desk001",
            "values": {"max_agent_steps": 41, "device.theme": "mark1", "profile.response_length": "short"},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["updated"]) == {"max_agent_steps", "device.theme", "profile.response_length"}
    assert cfg.max_agent_steps == 41
    w = app.state.platform.config_writer
    assert w.current("device.theme", device_id="dev_desk001") == "mark1"
    assert w.current("profile.response_length") == "short"
    ledger = client.get("/config/ledger").json()["changes"]
    row = next(c for c in ledger if c["action_id"] == body["action_id"])
    assert row["where"] == "here" and row["undoable"] is True
    assert client.post(f"/undo/{body['action_id']}").status_code == 200
    assert cfg.max_agent_steps == before_steps
    assert w.current("device.theme", device_id="dev_desk001") is None
    again = next(c for c in client.get("/config/ledger").json()["changes"] if c["action_id"] == body["action_id"])
    assert again["undone"] is True and again["undoable"] is False


def test_a_bad_value_writes_nothing_and_an_unknown_key_is_refused(tmp_path):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    cfg = app.state.platform.config
    before = cfg.max_agent_steps
    r = client.put("/settings/values", json={"values": {"max_agent_steps": 50, "local_primary_policy": "sometimes"}})
    assert r.status_code == 400
    assert cfg.max_agent_steps == before
    r = client.put("/settings/values", json={"values": {"no_such_setting": 1}})
    assert r.status_code == 400 and "no_such_setting" in r.text
    assert client.put("/settings/values", json={"values": {}}).status_code == 400


@pytest.mark.asyncio
async def test_the_ledger_shows_a_change_made_in_chat_beside_the_page_ones(tmp_path):
    app = create_app(str(tmp_path))
    app.state.platform.router.stream = _calls_stream([("config_set", {"key": "autonomy_dry_run", "value": "on"})])

    async def never(data):  # pragma: no cover
        raise AssertionError(data)

    await _drive_stream(app, _body("turn on dry run mode for autonomy"), never)
    changes = TestClient(app).get("/config/ledger").json()["changes"]
    chat_rows = [c for c in changes if c["tool"] == "config_set"]
    assert chat_rows and chat_rows[0]["where"] == "chat" and chat_rows[0]["undoable"] is True
