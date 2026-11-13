"""Chat can set an agent's job card: the tools it must never use and how it
asks first (v1.484.1).

Seen live: "change what the insurance helper is allowed to do, so it can only
read the letters and not send anything out" had no way in from chat, so the
model said no such option exists. ``agent_update`` and ``agent_create`` now take
``never_use_tools`` (the route's ``deny_tools``) and ``approvals`` (its
``approval_mode``), through the Agents page's own route and checks:

* a name that is not one of Iron Jarvis's tools is refused in words before
  anything is sent (an agent told never to use a made-up name would still hold
  the real tool, while the card said it could not);
* ``yolo`` is not offered (the route refuses it for an agent);
* the card says what changed, and Undo puts back every column.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.settings import records_projects as RP
from tests.test_chat_approvals_v1187 import _drive_stream
from tests.test_records_projects_v14350 import _agent_row, _undo
from tests.test_settings_chat_tools_v1321 import _body
from tests.test_settings_records_v1321 import _app, _approve_all, _card

pytestmark = pytest.mark.usefixtures("no_network")


def test_the_job_card_fields_are_offered_and_map_to_the_routes_fields():
    props = RP.AgentUpdateTool.input_schema["properties"]
    assert props["never_use_tools"]["type"] == "array"
    assert props["approvals"]["enum"] == ["", "approve_for_me", "always_ask"]
    assert "yolo" not in props["approvals"]["enum"]
    assert RP._agent_fields({"never_use_tools": [" notify ", "web_fetch"], "approvals": " always_ask "}) == {
        "deny_tools": ["notify", "web_fetch"], "approval_mode": "always_ask"}
    assert "never_use_tools" in RP.AgentCreateTool.input_schema["properties"]
    assert "reviewer = only reads" in RP.AgentUpdateTool.description


@pytest.mark.asyncio
async def test_never_use_and_approvals_land_and_undo_restores_them(tmp_path):
    app = _app(tmp_path / "home", [("agent_update", {
        "agent": "insurance-helper", "never_use_tools": ["notify", "web_fetch"], "approvals": "always_ask"})])
    p = app.state.platform
    TestClient(app).post("/agents", json={"name": "insurance-helper", "system_prompt": "Read the letters."})
    before = _agent_row(p, "insurance-helper")
    frames = await _drive_stream(app, _body("change the insurance-helper agent so it asks before every action"),
                                 await _approve_all(app, []))
    now = _agent_row(p, "insurance-helper")
    assert now["approval_mode"] == "always_ask"
    assert sorted(__import__("json").loads(now["deny_tools_json"])) == ["notify", "web_fetch"]
    assert now["system_prompt"] == before["system_prompt"]
    card = _card(frames, "Agent changed")
    assert "never uses notify, web_fetch" in card["new"] and "asks before every action" in card["new"]
    assert "may use all its tools" in card["old"]
    await _undo(app, card)
    assert _agent_row(p, "insurance-helper") == before


@pytest.mark.asyncio
async def test_a_made_up_tool_name_is_refused_and_nothing_changes(tmp_path):
    app = _app(tmp_path / "home", [("agent_update", {
        "agent": "insurance-helper", "never_use_tools": ["send_letters_by_post"]})])
    p = app.state.platform
    TestClient(app).post("/agents", json={"name": "insurance-helper", "system_prompt": "Read the letters."})
    before = _agent_row(p, "insurance-helper")
    frames = await _drive_stream(app, _body("change the permissions of the insurance-helper agent"),
                                 await _approve_all(app, []))
    finished = [d for ev, d in frames if ev == "tool_call" and d.get("status") == "finished"]
    assert finished and finished[-1].get("ok") is False
    assert "no tool called" in str(finished[-1].get("output") or finished[-1].get("error") or "")
    assert _agent_row(p, "insurance-helper") == before
