"""v1.400.0 — TEAM-FIRST: the setting.

``Config.team_first`` ("Hand work to my team first"), OFF by default, one row
of the settings schema in Models › Model team beside the team's pay-per-use
limit. Turning it ON moves work and usage onto other models and accounts, so
from chat it asks — every time, past any grant (``floor_when=(True,)``: the
schema's per-value tier, the way ``plan_approval`` and the emergency stop do
it); turning it OFF applies at once. It is saved only through the one settings
writer: the Settings page's ``PUT /settings/values`` and chat's config tools.

The dashboard draws schema rows by itself, so the switch is ONE control, in
the "Model team" section; ``dashboard/__tests__/fixtures/team-first-row-v1400.json``
holds the row exactly as ``GET /settings/schema`` serves it (regenerate with
``IJ_REGEN_TEAM_FIRST_ROW=1``) and ``team-first-setting-v1400.test.tsx``
renders it through the real Settings page.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.config import Config
from iron_jarvis.daemon.app import create_app
from iron_jarvis.settings import schema
from tests.test_chat_approvals_v1187 import _asgi_post, _drive_stream
from tests.test_settings_chat_tools_v1321 import _body, _calls_stream, _done

ROW_FIXTURE = Path(__file__).resolve().parents[1] / "dashboard" / "__tests__" / "fixtures" / "team-first-row-v1400.json"
REGEN = os.environ.get("IJ_REGEN_TEAM_FIRST_ROW") == "1"


def test_the_row_is_in_the_model_team_section_off_by_default_and_asks_only_to_turn_on(tmp_path):
    d = schema.get("team_first")
    assert d.label == "Hand work to my team first"
    assert (d.group, d.section, d.type, d.store) == ("models", "Model team", "bool", "config")
    # Beside the team's other setting.
    assert schema.get("team_paid_cap_usd").section == d.section
    # ON asks (every time, never lifted by a grant); OFF applies at once.
    assert d.tier_for(True) == "ask-floor"
    assert d.tier_for(False) == "allow"
    assert "team_first" in schema.daemon_keys()
    assert Config(home=tmp_path, project_root=tmp_path).team_first is False
    # The help says what it does, in plain words.
    for words in ("a coding session is asked to hand reading, test runs, first drafts and reviews to your "
                  "model team by role", "to keep the planning and the final check on its own model",
                  "Members on your Claude Code or Codex plans use those plans"):
        assert words in d.help, words


def test_the_settings_page_turns_it_on_through_the_one_writer(tmp_path):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    rows = {r["key"]: r for r in client.get("/settings/schema").json()["settings"]}
    assert rows["team_first"]["section"] == "Model team" and rows["team_first"]["type"] == "bool"
    cfg = app.state.platform.config
    assert client.get("/settings/values").json()["values"]["team_first"] is False
    r = client.put("/settings/values", json={"values": {"team_first": True}})
    assert r.status_code == 200, r.text
    assert r.json()["changed"] == [
        {"key": "team_first", "label": "Hand work to my team first", "old": False, "new": True}]
    assert cfg.team_first is True
    # One ledger row, undoable (the writer's).
    (row,) = client.get("/config/ledger").json()["changes"]
    assert row["action_id"] == r.json()["action_id"] and row["summary"] == "changed team_first"
    assert row["undoable"] is True
    # Saved to config.toml (a restart keeps it).
    assert "team_first = true" in (Path(cfg.home) / "config.toml").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_chat_cannot_turn_it_on_with_the_allow_tool(tmp_path):
    app = create_app(str(tmp_path))
    cfg = app.state.platform.config
    app.state.platform.router.stream = _calls_stream([("config_set", {"key": "team_first", "value": True})])

    async def never(data):  # pragma: no cover
        raise AssertionError(data)

    frames = await _drive_stream(app, _body("turn on the hand work to my team first setting"), never)
    assert cfg.team_first is False
    finished = next(d for ev, d in frames if ev == "tool_call" and d.get("status") == "finished")
    assert finished["ok"] is False
    assert "config_change_protected" in (finished.get("output") or finished.get("error") or "")


@pytest.mark.asyncio
async def test_chat_turning_it_on_cards_even_in_yolo_and_never_always(tmp_path):
    app = create_app(str(tmp_path))
    cfg = app.state.platform.config
    app.state.platform.router.stream = _calls_stream(
        [("config_change_protected", {"key": "team_first", "value": True})])
    seen = []

    async def approve(data):
        seen.append((data["tool"], data.get("can_always")))
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "once"})
        assert status == 200

    frames = await _drive_stream(app, _body("turn on the hand work to my team first setting", "yolo"), approve)
    assert seen == [("config_change_protected", False)]
    assert cfg.team_first is True
    (card,) = _done(frames)["config_cards"]
    assert card["key"] == "team_first" and card["old"] is False and card["new"] is True


@pytest.mark.asyncio
async def test_chat_turns_it_off_at_once(tmp_path):
    app = create_app(str(tmp_path))
    cfg = app.state.platform.config
    cfg.team_first = True
    app.state.platform.router.stream = _calls_stream([("config_set", {"key": "team_first", "value": False})])

    async def never(data):  # pragma: no cover
        raise AssertionError(data)

    frames = await _drive_stream(app, _body("turn off the hand work to my team first setting"), never)
    assert cfg.team_first is False
    (card,) = _done(frames)["config_cards"]
    assert card["key"] == "team_first" and card["new"] is False


def test_the_dashboards_row_fixture_is_what_the_daemon_serves():
    want = {"rows": [schema.get("team_paid_cap_usd").public(), schema.get("team_first").public()]}
    if REGEN:
        # (bytes: LF on every OS)
        ROW_FIXTURE.write_bytes((json.dumps(want, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
        pytest.skip("fixture regenerated")
    assert json.loads(ROW_FIXTURE.read_text(encoding="utf-8")) == want
