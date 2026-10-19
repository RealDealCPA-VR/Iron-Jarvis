"""v1.355.0, lane 5B (product-readiness wave 5): one add/remove door for
every pack, GitHub's maintained server, Change key, and what a connected app
can actually do.

G44 — The Directory's Connect / Disconnect and the chat's ``app_connect`` /
its Undo kept their OWN copies of the add and remove paths. The removal
skipped the pack's manifest, its live client and its load record, so a pack
disconnected and connected again was diffed against the manifest of the pack
the user had removed (a tool it still had read as "new" and asked forever)
and a removed pack could keep reading "ok". Every door now ends in
``mcp/pack_door`` — the body of ``POST`` / ``DELETE /mcp/servers`` — and
GitHub's card reaches GitHub's OWN maintained server
(``https://api.githubcopilot.com/mcp/``, ``Authorization: Bearer <token>``).

G69 — a token that expired is replaced in place (the card's Change key is the
Tools page's editor: ``PATCH /mcp/servers/{id}``); nothing is disconnected.
The chat's secure card writes the SAME vault row.

G70 — the gallery row of a connected app says how many of its tools can
change things and whether they ask first, read off the live tools.

Everything is driven through the REAL app factory against an in-process
stand-in for GitHub's server (``tests/fixtures/github_http_mcp_v13550.py``)
that answers 401 to any token but the one it was given — the network is
never touched, and "the vault value reached the server" is asserted on the
wire.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.connectors import catalog, service
from iron_jarvis.daemon.app import create_app
from iron_jarvis.mcp import tools as mcp_tools_mod
from iron_jarvis.mcp.manifest import PackManifest
from tests.fixtures.github_http_mcp_v13550 import GITHUB_TOOLS, FakeGitHub, github_at

TOKEN = "ghp_LANE5B_first_0123456789NEVERSHOWN"
NEW_TOKEN = "ghp_LANE5B_second_9876543210NEVERSHOWN"
NEWER_TOKEN = "ghp_LANE5B_third_5555555555NEVERSHOWN"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _fresh_records():
    """The load record and the live clients are process-wide (the v1.325.2
    lesson): clear them so another test's pack never answers for ours."""
    for store in (mcp_tools_mod._LOAD_STATUS, mcp_tools_mod._ANNOUNCED, mcp_tools_mod._LOAD_CFGS):
        store.clear()
    yield
    for store in (mcp_tools_mod._LOAD_STATUS, mcp_tools_mod._ANNOUNCED, mcp_tools_mod._LOAD_CFGS):
        store.clear()
    mcp_tools_mod.forget_live_client("github")


@pytest.fixture
def github():
    with FakeGitHub(TOKEN) as gh, github_at(gh.url):
        yield gh


@pytest.fixture
def client(tmp_path, github):
    c = TestClient(create_app(str(tmp_path)))
    try:
        yield c
    finally:
        mcp_tools_mod.unload_pack_tools(c.app.state.platform.registry, "github")


def _row(client: TestClient, cid: str) -> dict:
    return next(r for r in client.get("/connectors").json()["connectors"] if r["id"] == cid)


def _entry(platform, name: str) -> dict | None:
    return next((s for s in platform.config.mcp_servers or [] if s.get("name") == name), None)


def _toml(platform) -> str:
    path = Path(platform.config.home) / "config.toml"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _connect(client: TestClient, token: str = TOKEN):
    return client.post("/connectors/github/connect", json={"values": {"GITHUB_PERSONAL_ACCESS_TOKEN": token}})


# --------------------------------------------------------------------------- #
# G44: GitHub's maintained server, through the one add door.
# --------------------------------------------------------------------------- #
def test_the_github_card_reaches_githubs_own_server_and_both_catalogs_agree():
    gh = catalog.get_connector("github")
    assert gh.transport == "http" and gh.url == catalog.GITHUB_MCP_URL == "https://api.githubcopilot.com/mcp/"
    assert gh.auth_field == "GITHUB_PERSONAL_ACCESS_TOKEN" and not gh.command and not gh.args
    assert gh.package_note == "", "GitHub's own server is maintained — no 'older package' word"
    assert catalog.is_remote(gh) and not catalog.is_remote(catalog.get_connector("notion"))


def test_the_tools_catalog_names_the_same_server_and_fixes_its_copy(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    rows = {r["id"]: r for r in client.get("/mcp/catalog").json()["catalog"]}
    gh = rows["github"]
    assert gh["transport"] == "http" and gh["url"] == catalog.GITHUB_MCP_URL
    assert gh["auth_field"] == "GITHUB_PERSONAL_ACCESS_TOKEN" and gh["env_keys"] == ["GITHUB_PERSONAL_ACCESS_TOKEN"]
    assert gh["runtime_ready"] is True, "a server reached over the network needs nothing installed"
    assert "server-github" not in json.dumps(rows), "neither catalog offers the retired package"
    # The pack is not the app's own memory: it is named as the Directory names it.
    assert rows["memory"]["name"] == "Knowledge graph"
    assert "Long-term memory" not in json.dumps(rows)
    # Box's server reads a client id and secret, never a "Developer Token".
    box = rows["box"]
    assert "Developer Token" not in box["description"]
    assert "client id" in box["description"] and "client secret" in box["description"]
    assert box["env_keys"] == ["BOX_CLIENT_ID", "BOX_CLIENT_SECRET"]


def test_connecting_github_from_the_directory_loads_githubs_server_with_the_token(client, github):
    p = client.app.state.platform
    res = _connect(client)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True and body["tools_loaded"] == len(GITHUB_TOOLS) and body["note"] is None
    # The stored row is the one add door's http shape: the url, and the token
    # by vault NAME only.
    cfg = _entry(p, "github")
    assert cfg["transport"] == "http" and cfg["url"] == github.url
    assert cfg["auth"] == {"header": "Authorization", "format": "Bearer {value}", "secret": "mcp.github.auth.value"}
    assert p.secrets.get("mcp.github.auth.value") == TOKEN
    assert TOKEN not in json.dumps(p.config.mcp_servers) and TOKEN not in _toml(p)
    assert TOKEN not in res.text
    # On the wire: GitHub's server was handed the token as the bearer header.
    assert f"Bearer {TOKEN}" in github.bearer_seen()
    row = _row(client, "github")
    assert row["status"] == "connected" and row["tools_loaded"] == len(GITHUB_TOOLS)
    assert "runtime_ready" not in row, "nothing to install for a remote server"
    assert TOKEN not in json.dumps(row)


def test_disconnect_then_connect_is_a_clean_first_load(client, github):
    p = client.app.state.platform
    home = p.config.home
    assert _connect(client).json()["tools_loaded"] == len(GITHUB_TOOLS)
    manifest = PackManifest(home).path("github")
    assert manifest.is_file()
    assert mcp_tools_mod.load_status("github")["state"] == "ok"
    assert mcp_tools_mod.live_client("github") is not None

    assert client.delete("/connectors/github").json() == {"ok": True, "disconnected": "github"}
    # The one remove door: row, vault row, manifest, tools, live client and
    # load record are ALL gone — a removed pack does not keep reading "ok".
    assert _entry(p, "github") is None
    assert p.secrets.get("mcp.github.auth.value") is None
    assert not manifest.exists(), "Disconnect left the manifest: a re-add would be diffed against it"
    assert mcp_tools_mod.load_status("github") is None, "a removed pack kept its load record"
    assert mcp_tools_mod.live_client("github") is None
    assert p.registry.mcp_names("github") == []
    assert _row(client, "github")["status"] == "disconnected"

    # GitHub ships a new write tool meanwhile. Connecting again is a FIRST
    # load: everything it advertises is what the user is connecting now.
    github.tools = [*GITHUB_TOOLS, {"name": "delete_repository", "inputSchema": {"type": "object", "properties": {}}}]
    assert _connect(client).json()["tools_loaded"] == len(GITHUB_TOOLS) + 1
    held = [n for n in p.registry.mcp_names("github") if p.registry.get(n).quarantined]
    assert held == [], f"a reconnect after Disconnect was diffed against the removed pack: {held}"


def test_connecting_over_a_saved_card_is_a_reconnect_that_keeps_its_manifest(client, github):
    """Control for the test above: WITHOUT a Disconnect, a connect replaces
    the saved pack and the manifest stays, so a write tool that arrived since
    asks until trusted (v1.299.0) — the two doors are not the same thing."""
    p = client.app.state.platform
    assert _connect(client).json()["tools_loaded"] == len(GITHUB_TOOLS)
    github.tools = [*GITHUB_TOOLS, {"name": "delete_repository", "inputSchema": {"type": "object", "properties": {}}}]
    assert _connect(client).json()["tools_loaded"] == len(GITHUB_TOOLS) + 1
    assert p.registry.get("mcp__github__delete_repository").quarantined is True
    assert p.registry.get("mcp__github__create_issue").quarantined is False


def test_a_failed_connect_names_the_real_reason(client, github):
    p = client.app.state.platform
    res = _connect(client, token="ghp_expired_or_wrong")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["tools_loaded"] == 0
    assert body["note"] == service.DID_NOT_START_NOTE  # the card's own next step, unchanged
    assert body["reason"] == "the pack refused our credentials", body
    assert "ghp_expired_or_wrong" not in res.text
    row = _row(client, "github")
    assert row["connected"] is True and row["status"] == "failed"
    assert row["reason"] == "the pack refused our credentials"
    assert _entry(p, "github") is not None, "the connection is saved; Change key or Retry repairs it"


# --------------------------------------------------------------------------- #
# G69: Change key — the token is replaced in place, never disconnected.
# --------------------------------------------------------------------------- #
def test_an_expired_token_is_changed_in_place_and_the_app_works_again(client, github):
    p = client.app.state.platform
    assert _connect(client).json()["tools_loaded"] == len(GITHUB_TOOLS)
    # The token expires: GitHub now accepts only a new one; a Retry fails.
    github.token = NEW_TOKEN
    retry = client.post("/mcp/servers/github/reload").json()
    assert retry["ok"] is False and _row(client, "github")["status"] == "failed"

    # Change key = the card's editor (EditPack): PATCH with the new token only.
    res = client.patch(
        "/mcp/servers/github",
        json={"auth": {"header": "Authorization", "format": "Bearer {value}", "value": NEW_TOKEN}},
    )
    assert res.status_code == 200, res.text
    answer = res.json()
    assert answer["reloaded"] is True and answer["tools_loaded"] == len(GITHUB_TOOLS)
    assert NEW_TOKEN not in res.text
    # Never disconnected: the same row, the same vault NAME, the new value.
    cfg = _entry(p, "github")
    assert cfg["url"] == github.url and cfg["auth"]["secret"] == "mcp.github.auth.value"
    assert p.secrets.get("mcp.github.auth.value") == NEW_TOKEN
    assert NEW_TOKEN not in _toml(p)
    assert github.bearer_seen()[-1] == f"Bearer {NEW_TOKEN}"
    assert _row(client, "github")["status"] == "connected"


def test_the_chat_card_and_change_key_replace_the_same_token(client, github):
    """``config_secret app.github`` (chat's secure card) writes the vault row
    the Directory's pack reads — the one door's name — so a token pasted in
    chat is the token GitHub's server gets."""
    p = client.app.state.platform
    assert _connect(client).json()["tools_loaded"] == len(GITHUB_TOOLS)
    github.token = NEWER_TOKEN
    res = client.post("/config/secret", json={"name": "app.github", "value": NEWER_TOKEN})
    assert res.status_code == 200, res.text
    assert res.json()["tools_loaded"] == len(GITHUB_TOOLS), res.json()
    assert p.secrets.get("mcp.github.auth.value") == NEWER_TOKEN
    assert github.bearer_seen()[-1] == f"Bearer {NEWER_TOKEN}"
    assert NEWER_TOKEN not in res.text


# --------------------------------------------------------------------------- #
# G70: what a connected app can actually do.
# --------------------------------------------------------------------------- #
def test_a_connected_github_card_says_how_many_tools_change_things_and_that_they_ask(client, github):
    p = client.app.state.platform
    _connect(client)
    row = _row(client, "github")
    # create_issue (no hint) and merge_pull_request (readOnlyHint false) can
    # change things; get_me / search / list_issues are read-only.
    assert row["can_change"] == 2
    assert row["asks_first"] is True and row["held_back"] == 0 and row["keyed"] is True
    # The count is the live tools' own flag, the one the Tools row shows.
    write_like = [n for n in p.registry.mcp_names("github") if p.registry.get(n).write_like]
    assert row["can_change"] == len(write_like)

    # "Run app tools without asking" on: they no longer ask first.
    p.config.mcp_auto_approve = True
    assert _row(client, "github")["asks_first"] is False
    p.config.mcp_auto_approve = False
    # A pack's own auto-approve opens the one shared key too (platform.py).
    _entry(p, "github")["auto_approve"] = True
    assert _row(client, "github")["asks_first"] is False


def test_a_card_not_connected_carries_no_counts_and_keeps_its_scopes(client):
    row = _row(client, "github")
    assert row["connected"] is False
    for key in ("can_change", "asks_first", "held_back", "keyed"):
        assert key not in row, key
    assert row["scopes"], "the prose scopes stay for a card not connected yet"


def test_a_pack_the_user_added_says_the_same(client, github):
    """An imported pack has no hand-written scopes at all; its card reads the
    same counts off its tools."""
    p = client.app.state.platform
    res = client.post(
        "/mcp/servers",
        json={
            "name": "mygh",
            "transport": "http",
            "url": github.url,
            "auth": {"header": "Authorization", "format": "Bearer {value}", "value": TOKEN},
        },
    )
    assert res.status_code == 200 and res.json()["tools_loaded"] == len(GITHUB_TOOLS), res.text
    try:
        row = _row(client, "mygh")
        assert row["category"] == service.USER_CATEGORY
        assert row["can_change"] == 2 and row["asks_first"] is True and row["keyed"] is True
    finally:
        mcp_tools_mod.unload_pack_tools(p.registry, "mygh")


# --------------------------------------------------------------------------- #
# G44: the chat's app_connect and its Undo go through the same door.
# --------------------------------------------------------------------------- #
def test_app_connect_saves_githubs_server_and_its_undo_is_the_one_remove(client, github):
    from iron_jarvis.settings import records

    p = client.app.state.platform
    out = asyncio.run(records.AppConnectTool(p).execute({"app": "github"}, None))
    assert out.ok, out.error
    cfg = _entry(p, "github")
    # The row the one add door stores for GitHub's server, waiting for its token.
    assert cfg == {
        "name": "github",
        "transport": "http",
        "url": github.url,
        "auth": {"header": "Authorization", "format": "Bearer {value}", "secret": "mcp.github.auth.value"},
    }
    assert [r["name"] for r in out.data["secret_requests"]] == ["app.github"]
    res = client.post("/config/secret", json={"name": "app.github", "value": TOKEN})
    assert res.json()["tools_loaded"] == len(GITHUB_TOOLS), res.json()
    manifest = PackManifest(p.config.home).path("github")
    assert manifest.is_file()

    # Undo of the connect (restore_app → apply_app(None)) = the one remove.
    assert records.restore_app(p, "github", None).startswith("undo: disconnected")
    assert _entry(p, "github") is None
    assert p.secrets.get("mcp.github.auth.value") is None
    assert not manifest.exists()
    assert mcp_tools_mod.load_status("github") is None
    assert p.registry.mcp_names("github") == []


def test_a_secret_field_whose_name_says_nothing_still_goes_to_the_vault(tmp_path, monkeypatch):
    """Box's "client id" field is a secret field whose NAME the vault rule
    does not recognise; the Directory says which fields are secrets."""
    monkeypatch.setattr(mcp_tools_mod, "mcp_tools", lambda *_a, **_k: [])  # nothing launched
    client = TestClient(create_app(str(tmp_path)))
    p = client.app.state.platform
    res = client.post(
        "/connectors/box/connect",
        json={"values": {"BOX_CLIENT_ID": "box-id-LANE5B", "BOX_CLIENT_SECRET": "box-secret-LANE5B"}},
    )
    assert res.status_code == 200, res.text
    cfg = _entry(p, "box")
    assert cfg["env_secrets"] == {
        "BOX_CLIENT_ID": "mcp.box.BOX_CLIENT_ID",
        "BOX_CLIENT_SECRET": "mcp.box.BOX_CLIENT_SECRET",
    }
    assert "BOX_CLIENT_ID" not in (cfg.get("env") or {})
    text = _toml(p)
    assert "box-id-LANE5B" not in text and "box-secret-LANE5B" not in text
    # The chat card names the same rows.
    from iron_jarvis.settings.credentials import resolve_secret

    assert resolve_secret("app.box__BOX_CLIENT_ID")[2] == "mcp.box.BOX_CLIENT_ID"


# --------------------------------------------------------------------------- #
# Packs saved before v1.355.0 (their token under `conn_<id>_<field>`).
# --------------------------------------------------------------------------- #
def _legacy_notion(p) -> dict:
    cfg = {
        "name": "notion",
        "command": "ij-no-such-launcher-v13550",
        "args": [],
        "env_secrets": {"NOTION_TOKEN": "conn_notion_notion_token"},
    }
    p.config.mcp_servers = [cfg]
    p.secrets.set("conn_notion_notion_token", "ntn_OLD_LANE5B")
    return cfg


def test_disconnecting_an_older_pack_drops_its_old_named_token(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    p = client.app.state.platform
    _legacy_notion(p)
    assert client.delete("/connectors/notion").json() == {"ok": True, "disconnected": "notion"}
    assert p.secrets.get("conn_notion_notion_token") is None, "the old-named token outlived its pack"


def test_a_token_pasted_on_the_chat_card_reaches_an_older_pack(tmp_path, monkeypatch):
    from iron_jarvis.settings import records

    launched: list[dict] = []
    monkeypatch.setattr(records, "load_app", lambda p, cfg: launched.append(cfg) or 0)
    client = TestClient(create_app(str(tmp_path)))
    p = client.app.state.platform
    _legacy_notion(p)
    res = client.post("/config/secret", json={"name": "app.notion", "value": "ntn_NEW_LANE5B"})
    assert res.status_code == 200, res.text
    # The card wrote the one door's row and the older pack now READS it —
    # before, the new token sat unused while the pack kept the old one.
    assert p.secrets.get("mcp.notion.NOTION_TOKEN") == "ntn_NEW_LANE5B"
    assert _entry(p, "notion")["env_secrets"] == {"NOTION_TOKEN": "mcp.notion.NOTION_TOKEN"}
    assert launched and launched[-1]["env_secrets"] == {"NOTION_TOKEN": "mcp.notion.NOTION_TOKEN"}
    assert "ntn_NEW_LANE5B" not in _toml(p)
    # And the app is not "waiting" for a token the older row already had.
    p.secrets.delete("mcp.notion.NOTION_TOKEN")
    _legacy_notion(p)
    assert records.app_waiting_for(p, "notion") == []


# --------------------------------------------------------------------------- #
# One door, by construction.
# --------------------------------------------------------------------------- #
def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_every_door_ends_in_the_pack_door():
    svc = _read("src/iron_jarvis/connectors/service.py")
    rec = _read("src/iron_jarvis/settings/records.py")
    route = _read("src/iron_jarvis/daemon/routes/agents.py")
    # The Directory and the chat records call the door, never a copy of it.
    assert "add_pack(" in svc and "remove_pack(" in svc
    assert "install_config(" in rec and "remove_pack(" in rec and "load_config(" in rec
    assert "platform.config.mcp_servers = " not in svc, "the Directory writes pack rows itself again"
    assert "persist_config_values(platform.config.home, {\"mcp_servers\"" not in rec
    # The routes wrap the same two functions.
    assert "add_pack(d.platform" in route and "remove_pack(d.platform" in route
