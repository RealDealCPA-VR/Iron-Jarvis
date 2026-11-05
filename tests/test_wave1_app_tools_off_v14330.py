"""v1.433.0 (Wave 1, A2): turning off "Run app tools without asking" from
CHAT clears every app's own flag too.

The Tools page's switch (``PATCH /mcp/settings``) turned the global flag off
AND popped every MCP server row's own ``auto_approve``: ``mcp_call`` is ONE
shared permission key, so a flag left on one row keeps every app running
without asking at the next start (platform.py's boot resolver,
``mcp.tools.pack_grants_auto``). Chat's ``config_change_protected`` went
through the settings writer alone, which had no step for it — the switch read
Off while the next start still trusted every app. The clear now lives IN the
writer, so every door does it.
"""

from __future__ import annotations

import tomllib

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.connectors.service import auto_approve_on
from iron_jarvis.core.config import persist_config_values
from iron_jarvis.daemon.app import create_app
from tests.test_chat_approvals_v1187 import _asgi_post, _drive_stream
from tests.test_settings_chat_tools_v1321 import _body, _calls_stream, _done

ROWS = [
    {"name": "brave", "command": "no-such-command-v14330", "args": [], "auto_approve": True},
    {"name": "notes", "command": "no-such-command-v14330", "args": []},
    {"name": "files", "command": "no-such-command-v14330", "args": [], "auto_approve": False},
]


def _app_with_rows(tmp_path, *, global_on: bool):
    app = create_app(str(tmp_path))
    cfg = app.state.platform.config
    cfg.mcp_servers = [dict(r) for r in ROWS]
    cfg.mcp_auto_approve = global_on
    persist_config_values(cfg.home, {"mcp_servers": cfg.mcp_servers, "mcp_auto_approve": global_on})
    return app


def _saved(app) -> dict:
    home = app.state.platform.config.home
    return tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))


async def _approve_once(app, seen):
    async def approve(data):
        seen.append(data["tool"])
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "once"})
        assert status == 200

    return approve


def _finished(frames, tool):
    return [d for ev, d in frames if ev == "tool_call" and d.get("status") == "finished" and d.get("name") == tool]


@pytest.mark.asyncio
async def test_chat_turning_it_off_clears_every_apps_own_flag(tmp_path):
    app = _app_with_rows(tmp_path, global_on=True)
    p = app.state.platform
    assert auto_approve_on(p) is True
    p.router.stream = _calls_stream([("config_change_protected", {"key": "mcp_auto_approve", "value": False})])
    seen: list[str] = []
    frames = await _drive_stream(
        app, _body("stop letting app tools run without asking, change that setting"), await _approve_once(app, seen)
    )
    assert seen == ["config_change_protected"], "a protected change asks first"
    assert p.config.mcp_auto_approve is False
    assert all("auto_approve" not in s for s in p.config.mcp_servers), p.config.mcp_servers
    # The rule the next start applies: nothing opens the shared key now.
    assert auto_approve_on(p) is False
    # …and it is what config.toml holds, so the next start agrees.
    saved = _saved(app)
    assert saved["mcp_auto_approve"] is False
    assert all("auto_approve" not in s for s in saved["mcp_servers"]), saved["mcp_servers"]
    # The other settings of each row are kept.
    assert [s["name"] for s in saved["mcp_servers"]] == ["brave", "notes", "files"]
    (card,) = _done(frames)["config_cards"]
    assert card["key"] == "mcp_auto_approve" and card["old"] is True and card["new"] is False
    (done,) = _finished(frames, "config_change_protected")
    assert "2 apps that still ran their tools without asking will ask first too" in done.get("output", "")


@pytest.mark.asyncio
async def test_off_when_already_off_still_clears_a_leftover_app_flag_and_says_so(tmp_path):
    """The global switch was already off, but one app's own flag still let
    every app run unasked — the state the Tools page showed as "effective".
    "It was already Off" is not the whole truth then."""
    app = _app_with_rows(tmp_path, global_on=False)
    p = app.state.platform
    p.config.mcp_servers = [ROWS[0] | {}, ROWS[1] | {}]
    assert auto_approve_on(p) is True
    p.router.stream = _calls_stream([("config_change_protected", {"key": "mcp_auto_approve", "value": "off"})])
    seen: list[str] = []
    frames = await _drive_stream(app, _body("make app tools ask first, change that setting"), await _approve_once(app, seen))
    assert auto_approve_on(p) is False
    assert all("auto_approve" not in s for s in _saved(app)["mcp_servers"])
    (done,) = _finished(frames, "config_change_protected")
    out = done.get("output", "")
    assert "was already Off" in out and "1 app that still ran its tools without asking will ask first too" in out
    assert "restarts" in out


def test_the_writer_clears_on_every_off_and_leaves_rows_alone_on_on(tmp_path):
    app = _app_with_rows(tmp_path, global_on=False)
    p = app.state.platform
    w = p.config_writer
    out = w.apply({"mcp_auto_approve": True}, actor="test")
    assert out.cleared_auto_approve == []
    assert p.config.mcp_servers[0]["auto_approve"] is True, "turning it ON touches no row"
    out = w.apply({"mcp_auto_approve": False}, actor="test")
    assert sorted(out.cleared_auto_approve) == ["brave", "files"]
    assert "cleared_auto_approve" not in out.as_dict(), "every route's answer keeps its shape"
    assert all("auto_approve" not in s for s in p.config.mcp_servers)


def test_the_tools_page_switch_still_clears_and_answers_as_before(tmp_path):
    app = _app_with_rows(tmp_path, global_on=True)
    client = TestClient(app)
    r = client.patch("/mcp/settings", json={"auto_approve": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"auto_approve_global", "auto_approve_effective", "note"}
    assert body["auto_approve_global"] is False and body["auto_approve_effective"] is False
    assert "restart" in body["note"]
    rows = client.get("/mcp/servers").json()
    assert rows["auto_approve_effective"] is False
    assert all("auto_approve" not in s for s in _saved(app)["mcp_servers"])


def test_undo_of_turning_it_off_turns_the_switch_back_on(tmp_path):
    """The Undo puts the global switch back (the writer's own journal row);
    with it on, every app runs without asking again — the state before."""
    app = _app_with_rows(tmp_path, global_on=True)
    client = TestClient(app)
    p = app.state.platform
    action = p.config_writer.apply({"mcp_auto_approve": False}, actor="test").action_id
    assert action
    assert client.post(f"/undo/{action}").status_code == 200
    assert p.config.mcp_auto_approve is True and auto_approve_on(p) is True
    assert _saved(app)["mcp_auto_approve"] is True
