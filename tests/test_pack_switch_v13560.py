"""v1.356.0, lane 6A (product-readiness wave 6), G78: turn a pack OFF without
losing it.

Before this release the only way to stop an app's tools was Delete (Tools)
or Disconnect (Directory) — both remove the pack AND its saved token, and an
agent with ``mcp:*`` got every connected pack with no way to leave one out.
Now an ``mcp_servers`` row may carry ``enabled = false``:

* the loader never loads it (boot, the deferred boot, a reload) — none of its
  tools reach the registry, so chat, agents and ``mcp:*`` hold none of them —
  and no load record is written for it (nothing reads "failed"/"starting");
* ``PATCH /mcp/servers/{name} {enabled}`` switches it through the one pack
  door (``mcp/pack_door.set_pack_enabled``): OFF unloads its tools, closes
  their client and drops its load record but keeps the row, every vault
  secret and the manifest; ON reloads through the Retry's reload — the
  stored token reaches the server again with nothing typed;
* ``GET /mcp/servers`` rows carry ``enabled`` (and ``state: "off"``);
* the import door adds a server its app had switched off (the preview's
  ``disabled``) as ``enabled = false`` when the user ticks it.

Driven through the REAL app factory against the in-process stand-in server
(``tests/fixtures/github_http_mcp_v13550.py``), which answers 401 to any
token but the one it was given — so "switching back on needs no token" is
proven on the wire, and "a switched-off pack never loads" by the server
seeing no request at all.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import _resolve_connectors
from iron_jarvis.mcp import pack_door
from iron_jarvis.mcp import tools as mcp_tools_mod
from tests.fixtures.github_http_mcp_v13550 import GITHUB_TOOLS, FakeGitHub

TOKEN = "xoxb-LANE6A-slack-0123456789-NEVERSHOWN"
NAME = "slack"


@pytest.fixture(autouse=True)
def _fresh_records():
    """The load records and live clients are process-wide (the v1.325.2
    lesson): clear them so another test's pack never answers for ours."""
    for store in (mcp_tools_mod._LOAD_STATUS, mcp_tools_mod._ANNOUNCED, mcp_tools_mod._LOAD_CFGS):
        store.clear()
    yield
    for store in (mcp_tools_mod._LOAD_STATUS, mcp_tools_mod._ANNOUNCED, mcp_tools_mod._LOAD_CFGS):
        store.clear()
    for name in (NAME, "slack2"):
        mcp_tools_mod.forget_live_client(name)


@pytest.fixture
def server():
    with FakeGitHub(TOKEN) as gh:
        yield gh


@pytest.fixture
def client(tmp_path, server):
    c = TestClient(create_app(str(tmp_path)))
    try:
        yield c
    finally:
        for name in (NAME, "slack2"):
            mcp_tools_mod.unload_pack_tools(c.app.state.platform.registry, name)


def _add(client: TestClient, server: FakeGitHub, name: str = NAME):
    res = client.post(
        "/mcp/servers",
        json={"name": name, "transport": "http", "url": server.url, "auth": {"value": TOKEN}},
    )
    assert res.status_code == 200, res.text
    assert res.json()["tools_loaded"] == len(GITHUB_TOOLS)
    return res


def _entry(platform, name: str = NAME) -> dict | None:
    return next((s for s in platform.config.mcp_servers or [] if s.get("name") == name), None)


def _row(client: TestClient, name: str = NAME) -> dict:
    return next(r for r in client.get("/mcp/servers").json()["servers"] if r["name"] == name)


def _toml(platform) -> str:
    path = Path(platform.config.home) / "config.toml"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _agent_mcp_tools(platform) -> list[str]:
    """What an agent whose allowlist is ``["mcp:*"]`` is shown."""
    return [s["name"] for s in platform.registry.specs(["mcp:*"])]


def _chat_tools(platform, name: str = NAME) -> list[str]:
    """What a chat turn with this app turned on in + › Connections arms."""
    tools, _memory = _resolve_connectors(SimpleNamespace(platform=platform), SimpleNamespace(connectors=[name]))
    return tools


def _requests(server: FakeGitHub) -> int:
    return len(server.seen)


# --------------------------------------------------------------------------- #
# The acceptance line: off -> gone from chat and agents; on -> back, no token.
# --------------------------------------------------------------------------- #
def test_switching_off_takes_its_tools_from_chat_and_agents_and_on_brings_them_back_without_the_token(
    client, server
):
    p = client.app.state.platform
    _add(client, server)
    assert len(_agent_mcp_tools(p)) == len(GITHUB_TOOLS)
    assert len(_chat_tools(p)) == len(GITHUB_TOOLS)
    live_tool = p.registry.get(p.registry.mcp_names(NAME)[0])
    live = live_tool.client

    res = client.patch(f"/mcp/servers/{NAME}", json={"enabled": False})
    assert res.status_code == 200, res.text
    off = res.json()
    assert off["enabled"] is False and off["state"] == "off" and off["changed"] is True
    assert off["tools_loaded"] == 0 and off["tools_unloaded"] == len(GITHUB_TOOLS)
    assert TOKEN not in res.text
    # Gone from every running surface at once.
    assert p.registry.mcp_names(NAME) == []
    assert _agent_mcp_tools(p) == [], "an agent with mcp:* gets nothing from a switched-off pack"
    assert _chat_tools(p) == [], "a chat with the app turned on arms none of its tools"
    assert getattr(live, "closed", False) is True, "its client was closed, not left running"
    assert mcp_tools_mod.live_client(NAME) is None
    assert mcp_tools_mod.load_status(NAME) is None, "no load record: nothing reads 'failed'"
    # Kept: the row (persisted with enabled = false), the token in the vault.
    entry = _entry(p)
    assert entry is not None and entry["enabled"] is False
    assert entry["auth"]["secret"] == "mcp.slack.auth.value"
    assert p.secrets.get("mcp.slack.auth.value") == TOKEN
    assert "enabled = false" in _toml(p)
    assert TOKEN not in _toml(p)
    row = _row(client)
    assert row["enabled"] is False and row["state"] == "off" and row["tools_loaded"] == 0
    assert row["auth_set"] is True, "the row still says a token is saved"

    # On again: the stored token reaches the server; nothing was typed.
    before = _requests(server)
    res = client.patch(f"/mcp/servers/{NAME}", json={"enabled": True})
    assert res.status_code == 200, res.text
    on = res.json()
    assert on["enabled"] is True and on["changed"] is True and on["reloaded"] is True
    assert on["tools_loaded"] == len(GITHUB_TOOLS) and on["state"] == "ok" and on["note"] is None
    assert TOKEN not in res.text
    after = server.seen[before:]
    assert after and all(r["headers"].get("authorization") == f"Bearer {TOKEN}" for r in after)
    assert len(_agent_mcp_tools(p)) == len(GITHUB_TOOLS)
    assert len(_chat_tools(p)) == len(GITHUB_TOOLS)
    # Back on = the row is what it was before the switch (absent = on).
    assert "enabled" not in _entry(p)
    assert _row(client)["enabled"] is True


def test_switching_to_the_state_it_is_already_in_changes_nothing(client, server):
    p = client.app.state.platform
    _add(client, server)
    before = _requests(server)
    res = client.patch(f"/mcp/servers/{NAME}", json={"enabled": True})
    assert res.status_code == 200
    assert res.json()["changed"] is False and res.json()["enabled"] is True
    assert _requests(server) == before, "an already-on pack is not reconnected"
    assert len(p.registry.mcp_names(NAME)) == len(GITHUB_TOOLS)
    client.patch(f"/mcp/servers/{NAME}", json={"enabled": False})
    again = client.patch(f"/mcp/servers/{NAME}", json={"enabled": False})
    assert again.json()["changed"] is False and again.json()["tools_unloaded"] == 0


def test_an_unknown_pack_is_a_404(client):
    assert client.patch("/mcp/servers/nope", json={"enabled": False}).status_code == 404
    assert client.patch("/mcp/servers/nope", json={"enabled": True}).status_code == 404


def test_a_switch_cannot_rename(client, server):
    _add(client, server)
    res = client.patch(f"/mcp/servers/{NAME}", json={"enabled": False, "name": "other"})
    assert res.status_code == 422
    assert _entry(client.app.state.platform).get("enabled", True) is True


def test_a_bare_auto_approve_flip_keeps_its_answer_byte_for_byte(client, server):
    _add(client, server)
    res = client.patch(f"/mcp/servers/{NAME}", json={"auto_approve": True})
    assert res.json() == {
        "name": NAME,
        "auto_approve": True,
        "note": "saved — restart Iron Jarvis for autonomous agents to pick this up",
    }


def test_retry_on_a_switched_off_pack_says_it_is_off_and_starts_nothing(client, server):
    _add(client, server)
    client.patch(f"/mcp/servers/{NAME}", json={"enabled": False})
    before = _requests(server)
    res = client.post(f"/mcp/servers/{NAME}/reload")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False and body["tools_loaded"] == 0
    assert body["last_error"] == mcp_tools_mod.PACK_OFF_ERROR
    assert _requests(server) == before, "a Retry never starts a pack behind its switch"
    assert client.app.state.platform.registry.mcp_names(NAME) == []
    assert mcp_tools_mod.load_status(NAME) is None


def test_an_edit_to_a_switched_off_pack_is_saved_and_stays_off(client, server):
    p = client.app.state.platform
    _add(client, server)
    client.patch(f"/mcp/servers/{NAME}", json={"enabled": False})
    before = _requests(server)
    res = client.patch(f"/mcp/servers/{NAME}", json={"headers": {"X-Team": "ops"}})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["reloaded"] is False and body["enabled"] is False and body["state"] == "off"
    assert "switched off" in body["note"]
    assert _requests(server) == before, "the edit loaded nothing behind the switch"
    assert _entry(p)["enabled"] is False and _entry(p)["headers"] == {"X-Team": "ops"}
    # Switching on loads the EDITED row.
    on = client.patch(f"/mcp/servers/{NAME}", json={"enabled": True}).json()
    assert on["tools_loaded"] == len(GITHUB_TOOLS)
    assert server.seen[-1]["headers"].get("x-team") == "ops"


def test_an_edit_and_a_switch_on_in_one_body_loads_the_edited_row_once(client, server):
    p = client.app.state.platform
    _add(client, server)
    client.patch(f"/mcp/servers/{NAME}", json={"enabled": False})
    before = _requests(server)
    res = client.patch(f"/mcp/servers/{NAME}", json={"headers": {"X-Team": "ops"}, "enabled": True})
    assert res.status_code == 200, res.text
    assert res.json()["enabled"] is True and res.json()["tools_loaded"] == len(GITHUB_TOOLS)
    assert all(r["headers"].get("x-team") == "ops" for r in server.seen[before:])
    assert "enabled" not in _entry(p)


def test_removing_a_switched_off_pack_still_removes_everything(client, server):
    p = client.app.state.platform
    _add(client, server)
    client.patch(f"/mcp/servers/{NAME}", json={"enabled": False})
    res = client.delete(f"/mcp/servers/{NAME}")
    assert res.status_code == 200
    assert _entry(p) is None and p.secrets.get("mcp.slack.auth.value") is None


# --------------------------------------------------------------------------- #
# The loader: boot (both shapes) and the probe.
# --------------------------------------------------------------------------- #
def test_a_switched_off_pack_is_never_loaded_at_boot_and_its_token_survives(tmp_path, server):
    from iron_jarvis.platform import build_platform, load_deferred_mcp

    first = TestClient(create_app(str(tmp_path)))
    try:
        _add(first, server)
        assert first.patch(f"/mcp/servers/{NAME}", json={"enabled": False}).status_code == 200
    finally:
        mcp_tools_mod.unload_pack_tools(first.app.state.platform.registry, NAME)
    mcp_tools_mod._LOAD_STATUS.clear()
    before = _requests(server)

    # The synchronous boot (every CLI caller).
    plat = build_platform(str(tmp_path))
    assert plat.registry.mcp_names(NAME) == []
    assert mcp_tools_mod.load_status(NAME) is None
    assert _entry(plat)["enabled"] is False
    assert plat.secrets.get("mcp.slack.auth.value") == TOKEN

    # The daemon's deferred boot: never marked "starting", never loaded.
    deferred = build_platform(str(tmp_path), defer_mcp=True)
    assert NAME not in mcp_tools_mod.starting_packs()
    asyncio.run(load_deferred_mcp(deferred))
    assert deferred.registry.mcp_names(NAME) == []
    assert mcp_tools_mod.load_status(NAME) is None
    assert _requests(server) == before, "no boot shape ever connected to a switched-off pack"


def test_the_loader_skips_a_switched_off_row_but_a_probe_still_reaches_it(server):
    cfg = {"name": NAME, "transport": "http", "url": server.url, "headers": {"Authorization": f"Bearer {TOKEN}"}, "enabled": False}
    mcp_tools_mod._LOAD_STATUS[NAME] = {"state": "failed", "last_error": "old"}
    assert mcp_tools_mod.mcp_tools([cfg]) == []
    assert mcp_tools_mod.load_status(NAME) is None, "an old failure never outlives the switch"
    assert server.seen == []
    probed = mcp_tools_mod.mcp_tools([cfg], record=False)
    try:
        assert len(probed) == len(GITHUB_TOOLS), "Test is a press that registers nothing; it may connect"
    finally:
        mcp_tools_mod.close_tools_clients(probed)
    assert mcp_tools_mod.mark_starting([cfg]) == []


@pytest.mark.parametrize(
    "value,on",
    [(False, False), ("false", False), ("off", False), ("No", False), ("0", False), (True, True), ("true", True), (None, True)],
)
def test_pack_enabled_reads_the_switch(value, on):
    cfg = {"name": "x"} if value is None else {"name": "x", "enabled": value}
    assert mcp_tools_mod.pack_enabled(cfg) is on


def test_the_switch_goes_through_the_one_pack_door(client, server, monkeypatch):
    """A pack is added, changed or removed ONLY through mcp/pack_door (the
    v1.355.0 rule): the route's switch is the door's ``set_pack_enabled``."""
    _add(client, server)
    calls: list[tuple[str, bool]] = []
    real = pack_door.set_pack_enabled

    def spy(platform, name, enabled, **kw):
        calls.append((name, enabled))
        return real(platform, name, enabled, **kw)

    monkeypatch.setattr(pack_door, "set_pack_enabled", spy)
    client.patch(f"/mcp/servers/{NAME}", json={"enabled": False})
    client.patch(f"/mcp/servers/{NAME}", json={"enabled": True})
    assert calls == [(NAME, False), (NAME, True)]


# --------------------------------------------------------------------------- #
# The import door: a server its app had switched off arrives switched off.
# --------------------------------------------------------------------------- #
def test_a_ticked_server_that_was_off_in_its_app_is_added_switched_off(client, server):
    p = client.app.state.platform
    paste = json.dumps(
        {
            "mcpServers": {
                "slack2": {"url": server.url, "headers": {"Authorization": f"Bearer {TOKEN}"}, "disabled": True}
            }
        }
    )
    preview = client.post("/mcp/import/preview", json={"text": paste}).json()["servers"]
    assert [r["disabled"] for r in preview] == [True]
    res = client.post("/mcp/import/apply", json={"text": paste, "names": ["slack2"]})
    assert res.status_code == 200, res.text
    added = res.json()["added"]
    assert [a["name"] for a in added] == ["slack2"]
    assert added[0]["state"] == "off" and added[0]["tools_loaded"] == 0
    assert "switched off" in (added[0]["note"] or "")
    assert TOKEN not in res.text
    assert server.seen == [], "added switched off: nothing was started"
    entry = _entry(p, "slack2")
    assert entry["enabled"] is False
    assert TOKEN not in json.dumps(entry), "the token is in the vault, never the row"
    row = _row(client, "slack2")
    assert row["enabled"] is False and row["state"] == "off"
    # Turning it on later works with the token the import stored.
    on = client.patch("/mcp/servers/slack2", json={"enabled": True}).json()
    assert on["tools_loaded"] == len(GITHUB_TOOLS)
    assert server.seen[-1]["headers"].get("authorization") == f"Bearer {TOKEN}"


def test_a_server_that_was_on_in_its_app_is_added_on_as_before(client, server):
    paste = json.dumps({"mcpServers": {"slack2": {"url": server.url, "headers": {"Authorization": f"Bearer {TOKEN}"}}}})
    res = client.post("/mcp/import/apply", json={"text": paste, "names": ["slack2"]})
    added = res.json()["added"]
    assert added[0]["state"] == "ok" and added[0]["tools_loaded"] == len(GITHUB_TOOLS)
    assert "enabled" not in _entry(client.app.state.platform, "slack2")


def test_an_ordinary_add_answer_carries_no_enabled_key(client, server):
    """The add door's answer is unchanged for every pack added ON."""
    res = _add(client, server)
    assert "enabled" not in res.json()
