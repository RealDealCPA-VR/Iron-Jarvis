"""v1.351.0 (G15) — an app connected from chat works in that chat and the task
continues: the daemon's half.

"connect my GitHub" used to end at "<label> saved securely": the save
(POST /config/secret) answered only {secret, label, status, action_id}, so
nothing could say whether the pack started; and ``app_connect`` on an app that
was already connected asked for its token again ("waiting for its token").

Now the save answers what the app did — ``app``, ``app_name``,
``tools_loaded`` (and ``load_reason`` when 0, ``waiting_for`` when an app with
two keys still needs one) — and ``app_connect`` on a connected, working app
says so without a card or a record change. The page (chat/page.tsx +
lib/appConnect) turns the app on for the chat from the card's save and from
the call's ``args.app`` on the tool frame, so the NEXT message carries
``connectors: ["github"]`` — pinned here through the real stream lane: that
message is offered GitHub's tools, and one without the switch is not.

GitHub's Directory entry launches ``npx …server-github`` (network); the
``local_github`` fixture points it at a REAL local child
(``fixtures/argv_mcp_server_v13510.py``) — only the launch target changes.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.connectors import catalog
from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.mcp.tools import unload_pack_tools
from iron_jarvis.settings import records
from tests.test_chat_approvals_v1187 import _asgi_post, _drive_stream
from tests.test_settings_chat_tools_v1321 import _body, _calls_stream, _done

FIXTURE = str(Path(__file__).parent / "fixtures" / "argv_mcp_server_v13510.py")
NO_SUCH = "ij-no-such-mcp-launcher-v13510-g15"
TOKEN = "ghp_LANE1E0123456789abcdefNEVERSHOWN"


def _swap_connector(monkeypatch, cid: str, **changes):
    real = catalog.get_connector(cid)
    swapped = dataclasses.replace(real, **changes)
    original = catalog.get_connector
    monkeypatch.setattr(catalog, "get_connector", lambda c: swapped if c == cid else original(c))
    return swapped


@pytest.fixture
def local_github(monkeypatch):
    """GitHub's Directory entry, launched by a real local child (no network)."""
    return _swap_connector(monkeypatch, "github", command=sys.executable, args=[FIXTURE])


async def _approve_all(app, seen):
    async def approve(data):
        seen.append(data["tool"])
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "once"})
        assert status == 200

    return approve


# --------------------------------------------------------------------------- #
# THE ACCEPTANCE (daemon half): connect from chat → N tools → the next turn
# with the app on has its tools.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_connect_my_github_saves_loads_says_how_many_tools_and_the_next_turn_has_them(tmp_path, local_github):
    app = create_app(str(tmp_path))
    p = app.state.platform
    try:
        p.router.stream = _calls_stream([("app_connect", {"app": "github"})])
        seen: list[str] = []
        frames = await _drive_stream(app, _body("connect my GitHub"), await _approve_all(app, seen))
        assert seen == ["app_connect"]
        # What the page reads to turn the app on (lib/appConnect.appConnectIds):
        # the started frame's args name the app, the finished frame says ok.
        started = [d for ev, d in frames if ev == "tool_call" and d.get("name") == "app_connect" and d.get("status") == "started"]
        finished = [d for ev, d in frames if ev == "tool_call" and d.get("name") == "app_connect" and d.get("status") == "finished"]
        assert started and started[0]["args"] == {"app": "github"}, started
        assert finished and finished[0]["ok"] is True, finished
        card = next(c for c in _done(frames)["config_cards"] if c["kind"] == "secret")
        assert card["name"] == "app.github"
        assert p.registry.mcp_names("github") == [], "waiting for its token: not launched yet"

        # The card's save answers WHAT LOADED — never the value.
        status, raw = await _asgi_post(app, "/config/secret", {"name": "app.github", "value": TOKEN})
        assert status == 200, raw
        answer = json.loads(raw)
        assert answer["status"] == "stored" and answer["action_id"]
        assert answer["app"] == "github" and answer["app_name"] == "GitHub"
        assert answer["tools_loaded"] == 1, answer
        assert "load_reason" not in answer and "waiting_for" not in answer
        assert TOKEN not in raw.decode()
        assert p.registry.mcp_names("github") == ["mcp__github__argv"]

        # The NEXT message, with the app ON for the chat (what the page now
        # sends without the "+" menu), is offered GitHub's tools …
        p.router.stream = with_switch = _calls_stream([])
        await _drive_stream(app, _body("list my open GitHub PRs", connectors=["github"]), await _approve_all(app, []))
        assert "mcp__github__argv" in with_switch.tools[0], with_switch.tools
        # … and the same message without it is not (the old dead end).
        p.router.stream = without = _calls_stream([])
        await _drive_stream(app, _body("list my open GitHub PRs"), await _approve_all(app, []))
        assert "mcp__github__argv" not in without.tools[0]
    finally:
        unload_pack_tools(p.registry, "github")


# --------------------------------------------------------------------------- #
# The save says what happened when it did NOT work.
# --------------------------------------------------------------------------- #
def test_a_token_for_an_app_that_does_not_start_says_its_tools_did_not_load(tmp_path, monkeypatch):
    _swap_connector(monkeypatch, "github", command=NO_SUCH, args=[])
    client = TestClient(create_app(str(tmp_path)))
    p = client.app.state.platform
    out = asyncio.run(records.AppConnectTool(p).execute({"app": "github"}, None))
    assert out.ok and out.data["secret_requests"], out
    res = client.post("/config/secret", json={"name": "app.github", "value": TOKEN})
    assert res.status_code == 200, res.text
    answer = res.json()
    assert answer["app_name"] == "GitHub" and answer["tools_loaded"] == 0
    assert answer["load_reason"], "the load record's plain words ride the answer"
    assert TOKEN not in res.text


def test_an_app_with_two_keys_waits_for_the_second_before_it_is_started(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    p = client.app.state.platform
    launched: list[str] = []
    monkeypatch.setattr(records, "load_app", lambda platform, cfg: launched.append(cfg["name"]) or 4)
    out = asyncio.run(records.AppConnectTool(p).execute({"app": "box"}, None))
    assert [r["name"] for r in out.data["secret_requests"]] == ["app.box__BOX_CLIENT_ID", "app.box__BOX_CLIENT_SECRET"]

    first = client.post("/config/secret", json={"name": "app.box__BOX_CLIENT_ID", "value": "box-id-LANE1E"}).json()
    assert first["tools_loaded"] == 0 and first["waiting_for"] == ["Box client secret"], first
    assert launched == [], "it cannot start without its second key, so it is not tried"

    second = client.post("/config/secret", json={"name": "app.box__BOX_CLIENT_SECRET", "value": "box-secret-LANE1E"}).json()
    assert second["tools_loaded"] == 4 and "waiting_for" not in second, second
    assert launched == ["box"]


def test_a_key_saved_before_its_app_was_set_up_names_the_app_and_claims_no_load(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    answer = client.post("/config/secret", json={"name": "app.notion", "value": "ntn_LANE1E_value_123456"}).json()
    assert answer["app"] == "notion" and answer["app_name"] == "Notion"
    assert "tools_loaded" not in answer, "no saved pack, nothing was loaded — and nothing is claimed"


def test_a_credential_that_is_not_an_apps_keeps_its_old_answer(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    res = client.post("/config/secret", json={"name": "channel.phone", "value": "123456789:AAbbCCddEEffGGhhIIjjKKllMMnnOOpp"})
    assert res.status_code == 200, res.text
    assert set(res.json()) == {"secret", "label", "status", "action_id"}


# --------------------------------------------------------------------------- #
# app_connect on an app that is already connected and working.
# --------------------------------------------------------------------------- #
@pytest.mark.timeout(120)
def test_app_connect_on_a_connected_app_says_so_with_no_card_and_no_record_change(tmp_path, local_github):
    client = TestClient(create_app(str(tmp_path)))
    p = client.app.state.platform
    try:
        asyncio.run(records.AppConnectTool(p).execute({"app": "github"}, None))
        assert client.post("/config/secret", json={"name": "app.github", "value": TOKEN}).json()["tools_loaded"] == 1
        before = records.snap_app(p, "github")

        out = asyncio.run(records.AppConnectTool(p).execute({"app": "github"}, None))
        assert out.ok, out.error
        assert out.output.startswith("GitHub is already connected (1 tool). It is on for this chat now"), out.output
        assert "waiting for its token" not in out.output
        assert "record_change" not in out.data and "secret_requests" not in out.data
        assert out.data["app"] == "github" and out.data["already_connected"] is True
        # No card reaches the page: nothing asks for a token it already has.
        assert chat_turn.config_cards_from_result("app_connect", out) == []
        assert records.snap_app(p, "github") == before, "nothing about the app changed"
        assert p.registry.mcp_names("github") == ["mcp__github__argv"], "its tools are still loaded"

        # Values passed still reconnect it, as before (a new setting).
        again = asyncio.run(records.AppConnectTool(p).execute({"app": "github", "values": {"x": "1"}}, None))
        assert again.ok and again.data.get("secret_requests"), "a reconnect with values asks for its token again"
    finally:
        unload_pack_tools(p.registry, "github")
