"""v1.353.0 lane 3C (product-readiness wave 3): one Connections home.

G42 — the user's own packs are "Added by you" and say what their tools do; no
       row a card renders says "MCP".
G43 — catalog entries that still install: Puppeteer is no longer offered as an
       app (a LINK card opens the browser add-on's settings instead), Slack
       runs its maintained server with the same two settings, the packages npm
       marks "no longer supported" say so, and a pack installed under the
       Tools catalog's name ("sequentialthinking") lights its OWN Directory
       card instead of showing as a stranger beside it.
G45 — setup needs are on the row BEFORE Connect: Gmail's Google developer
       setup, a drive's while no OAuth client is registered, the helper
       program a pack's launcher needs (the launcher's OWN resolution), and a
       signed-in drive that no memory base uses yet.

Everything goes through the REAL app factory and GET /connectors; only the
launcher resolution (which would read this PC's PATH) and a pack's live tools
(which would spawn npx) are stood in for.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.connectors import catalog as catalog_mod
from iron_jarvis.connectors import service
from iron_jarvis.connectors.catalog import (
    CATALOG,
    LINK_CARDS,
    OLDER_PACKAGE_NOTE,
    get_connector,
)
from iron_jarvis.daemon.app import create_app
from iron_jarvis.tools.base import Tool, ToolContext, ToolResult

REPO = Path(__file__).resolve().parents[1]
DIRECTORY_TSX = REPO / "dashboard" / "components" / "settings" / "pages" / "DirectoryPage.tsx"

#: npm answers "Package no longer supported" for these (npm view, 2026-10-18).
DEPRECATED_ON_NPM = {
    "@modelcontextprotocol/server-github",
    "@modelcontextprotocol/server-postgres",
    "@modelcontextprotocol/server-google-maps",
    "@modelcontextprotocol/server-slack",
    "@modelcontextprotocol/server-puppeteer",
}


class _PackTool(Tool):
    """A registered pack tool with its own description (no process)."""

    def __init__(self, name: str, description: str) -> None:
        self.name = name
        self.description = description
        self.input_schema = {"type": "object", "properties": {}}

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:  # pragma: no cover
        return ToolResult(ok=True, output="")


@pytest.fixture
def client(tmp_path, monkeypatch):
    # The launcher's resolution reads this PC's PATH; every command resolves
    # here unless a test says otherwise.
    import iron_jarvis.mcp.tools as mcp_tools

    monkeypatch.setattr(mcp_tools, "resolve_launcher", lambda cmd: f"/bin/{cmd}")
    with TestClient(create_app(str(tmp_path))) as c:
        yield c


def _rows(client: TestClient) -> list[dict[str, Any]]:
    r = client.get("/connectors")
    assert r.status_code == 200, r.text
    return r.json()["connectors"]


def _row(client: TestClient, cid: str) -> dict[str, Any]:
    return next(r for r in _rows(client) if r["id"] == cid)


# --------------------------------------------------------------------------- #
# G43 — entries that still install
# --------------------------------------------------------------------------- #


def test_puppeteer_is_no_longer_offered_as_an_app(client):
    rows = _rows(client)
    assert all(r["id"] != "puppeteer" for r in rows)
    assert not any("puppeteer" in (r["name"] + r["blurb"] + r["unlocks"]).lower() for r in rows)
    assert all("server-puppeteer" not in " ".join(c.args) for c in CATALOG)
    # Its place: the browser add-on, a LINK card whose one action opens
    # Settings › Connections › Browser — by its old address, because the card
    # sits inside Settings and a /settings?section=… link from there changes
    # only the address bar (SettingsHome reads its section on mount; the App
    # Router keeps the page mounted across a search-param change).
    browser = next(r for r in rows if r["id"] == "browser_addon")
    assert browser["connect_via"] == "link"
    assert browser["href"] == "/computeruse"
    assert not browser["href"].startswith("/settings?")
    relocated = (REPO / "dashboard" / "lib" / "relocated.ts").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert re.search(
        r'"/computeruse":\s*\{\s*base:\s*"/settings",\s*extra:\s*\{\s*section:\s*"connections-browser"\s*\}',
        relocated,
    ), "the old address lands on Settings › Connections › Browser"
    assert browser["action"] == "Set up the browser add-on"
    assert browser["connected"] is False and browser["fields"] == []
    # A link card is not a connector: every door answers it as unknown.
    assert get_connector("browser_addon") is None
    assert client.post("/connectors/browser_addon/connect", json={"values": {}}).status_code == 404
    assert client.post("/connectors/browser_addon/test").status_code == 404
    assert client.delete("/connectors/browser_addon").status_code == 404


def test_a_puppeteer_pack_already_added_keeps_working_as_added_by_you(client):
    p = client.app.state.platform
    p.config.mcp_servers = [{"name": "puppeteer", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-puppeteer"]}]
    p.registry.register(_PackTool("mcp__puppeteer__navigate", "Navigate to a URL."), mcp=True)
    row = _row(client, "puppeteer")
    assert row["category"] == service.USER_CATEGORY
    assert row["status"] == "connected" and row["tools_loaded"] == 1
    assert client.delete("/connectors/puppeteer").status_code == 200
    assert all(s.get("name") != "puppeteer" for s in p.config.mcp_servers)


def test_slack_runs_the_maintained_server_with_the_same_two_settings():
    slack = get_connector("slack")
    assert slack is not None and slack.connect_via == "mcp" and slack.command == "npx"
    assert slack.args == ["-y", "@zencoderai/slack-mcp-server"]
    fields = {f.name: f.kind for f in slack.fields}
    assert fields == {"SLACK_BOT_TOKEN": "secret", "SLACK_TEAM_ID": "env"}
    assert slack.package_note == ""


def test_every_package_npm_retired_says_so_and_none_is_slack_or_puppeteer():
    for c in CATALOG:
        named = {a for a in c.args if a.startswith("@")}
        if named & DEPRECATED_ON_NPM:
            assert c.package_note == OLDER_PACKAGE_NOTE, c.id
            assert not named & {"@modelcontextprotocol/server-slack", "@modelcontextprotocol/server-puppeteer"}, c.id
        else:
            assert c.package_note == "", c.id
    flagged = {c.id for c in CATALOG if c.package_note}
    # v1.355.0 (G44, moved on purpose): GitHub's card reaches GitHub's own
    # maintained server now, so it no longer runs an "older package".
    assert flagged == {"postgres", "google_maps"}


def test_the_running_sequentialthinking_pack_lights_its_own_card(client, monkeypatch):
    p = client.app.state.platform
    p.config.mcp_servers = [
        {"name": "sequentialthinking", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"]}
    ]
    p.registry.register(
        _PackTool("mcp__sequentialthinking__sequentialthinking", "Think step by step."), mcp=True
    )
    rows = _rows(client)
    lit = [r for r in rows if r["name"] == "Sequential Thinking"]
    assert len(lit) == 1, "one card, not a card plus a stranger"
    card = lit[0]
    assert card["status"] == "connected" and card["connected"] is True and card["tools_loaded"] == 1
    # The row speaks the pack's REAL name, so every door finds it: Retry
    # (/mcp/servers/{id}/reload), Test, Disconnect and the chat's toggle.
    assert card["id"] == "sequentialthinking" and card["catalog_id"] == "sequential_thinking"
    assert p.registry.mcp_names(card["id"]), "the chat's connector toggle resolves by this id"
    assert not any(r["id"] == "sequential_thinking" for r in rows)
    assert not any(r["category"] == service.USER_CATEGORY for r in rows)

    # Test / Disconnect by the CATALOG id reach the alias pack too.
    probed: list[str] = []
    monkeypatch.setattr(service, "_probe_mcp", lambda platform, cfg: probed.append(cfg["name"]) or {"ok": True})
    assert service.test(p, "sequential_thinking") == {"ok": True} and probed == ["sequentialthinking"]
    out = service.disconnect(p, "sequential_thinking")
    assert out["ok"] is True
    assert all(s.get("name") != "sequentialthinking" for s in p.config.mcp_servers)


def test_both_names_installed_are_two_packs_and_two_cards(client):
    p = client.app.state.platform
    p.config.mcp_servers = [
        {"name": "sequential_thinking", "command": "npx", "args": []},
        {"name": "sequentialthinking", "command": "npx", "args": []},
    ]
    ids = [r["id"] for r in _rows(client)]
    assert ids.count("sequential_thinking") == 1 and ids.count("sequentialthinking") == 1
    assert _row(client, "sequentialthinking")["category"] == service.USER_CATEGORY


# --------------------------------------------------------------------------- #
# G45 — setup needs before Connect
# --------------------------------------------------------------------------- #


def test_gmail_says_its_google_setup_before_connect(client):
    gmail = _row(client, "gmail")
    assert gmail["connected"] is False
    assert gmail["needs"] == "Needs a one-time Google developer setup"
    assert "OAuth" not in gmail["unlocks"], "the need moved out of the blurb"


def test_drive_says_the_setup_only_while_no_oauth_client_is_registered(client):
    drive = _row(client, "google_drive")
    assert drive["oauth_client_configured"] is False
    assert drive["needs"] == "Needs a one-time Google developer setup"
    assert _row(client, "onedrive")["needs"] == "Needs a one-time Microsoft developer setup"
    assert _row(client, "dropbox")["needs"] == "Needs a one-time Dropbox developer setup"
    # The registry's OWN lookup (the vault's <provider>_oauth_client_id).
    client.app.state.platform.secrets.set("google_drive_oauth_client_id", "client-123")
    drive = _row(client, "google_drive")
    assert drive["oauth_client_configured"] is True and drive["needs"] == ""
    assert _row(client, "onedrive")["needs"], "only Drive's client was registered"


def test_a_signed_in_drive_with_no_memory_base_says_so_and_one_with_a_base_does_not(client):
    from iron_jarvis.ltm.sources import CustomSourceStore

    p = client.app.state.platform
    p.connections._upsert("google_drive", status="connected", method="oauth", account="me@example.com")
    drive = _row(client, "google_drive")
    assert drive["connected"] is True and drive["status"] == "connected"
    assert drive["memory_source"] is False
    assert drive["needs"] == "", "a signed-in drive needs nothing more to connect"
    CustomSourceStore(p.engine).add(name="work_drive", kind="google_drive", path="Projects")
    assert _row(client, "google_drive")["memory_source"] is True
    assert "memory_source" not in _row(client, "pixio"), "only the three drives carry it"


def test_the_drive_kinds_match_the_memory_package():
    from iron_jarvis.ltm.sources import CLOUD_DRIVE_KINDS

    assert service._CLOUD_DRIVE_KINDS == CLOUD_DRIVE_KINDS


def test_a_missing_helper_program_is_said_before_connect_by_the_launchers_own_answer(client, monkeypatch):
    import iron_jarvis.mcp.tools as mcp_tools

    asked: list[str] = []

    def resolve(cmd: str) -> str:
        asked.append(cmd)
        if cmd == "npx":
            raise FileNotFoundError("launcher 'npx' was not found")
        return f"/bin/{cmd}"

    monkeypatch.setattr(mcp_tools, "resolve_launcher", resolve)
    rows = {r["id"]: r for r in _rows(client)}
    # v1.355.0 (G44, moved on purpose): Notion stands in for GitHub here —
    # GitHub's card reaches a server over the network and launches nothing.
    assert rows["notion"]["runtime_ready"] is False
    assert rows["notion"]["runtime"] == "Node.js" and rows["notion"]["runtime_url"] == "https://nodejs.org"
    assert "runtime_ready" not in rows["github"], "a remote server needs no helper program"
    assert rows["fetch"]["runtime_ready"] is True and rows["fetch"]["runtime"] == "uv"
    # Once per command per listing, never once per card.
    assert asked.count("npx") == 1 and asked.count("uvx") == 1
    # OAuth / API-key / link cards launch nothing.
    for cid in ("google_drive", "pixio", "browser_addon"):
        assert "runtime_ready" not in rows[cid], cid


def test_the_card_and_the_tools_catalog_give_the_same_runtime_answer(monkeypatch):
    """One answer: the service reads the launcher's own resolution, exactly
    as ``routes.agents.launcher_ready`` (the Tools catalog, the import
    preview) does — without importing a route module."""
    import iron_jarvis.mcp.tools as mcp_tools
    from iron_jarvis.daemon.routes.agents import launcher_ready

    def resolve(cmd: str) -> str:
        if cmd in ("npx", "broken"):
            raise (FileNotFoundError if cmd == "npx" else PermissionError)(cmd)
        return f"/bin/{cmd}"

    monkeypatch.setattr(mcp_tools, "resolve_launcher", resolve)
    for cmd in ("npx", "uvx", "broken", "C:/tools/x.exe"):
        assert service._launcher_found(cmd) is launcher_ready(cmd), cmd


def test_the_runtime_words():
    assert service._runtime_for("npx") == ("Node.js", "https://nodejs.org")
    assert service._runtime_for("C:\\nodejs\\npx.cmd")[0] == "Node.js"
    assert service._runtime_for("uvx")[0] == "uv"
    assert service._runtime_for("my-tool") == ("my-tool", "")


# --------------------------------------------------------------------------- #
# G42 — "Added by you", what its tools do, and no row says MCP
# --------------------------------------------------------------------------- #


def test_a_pack_the_user_added_is_added_by_you_and_says_what_its_tools_do(client):
    p = client.app.state.platform
    p.config.mcp_servers = [{"name": "notes_pack", "command": "x"}]
    p.registry.register(_PackTool("mcp__notes_pack__read", "Read one note by its title.\nMore detail here."), mcp=True)
    p.registry.register(_PackTool("mcp__notes_pack__search", "**Search** your notes for a phrase. Returns matches."), mcp=True)
    p.registry.register(_PackTool("mcp__notes_pack__zap", "Delete everything."), mcp=True)
    row = _row(client, "notes_pack")
    assert row["category"] == "Added by you"
    assert row["blurb"] == "Read one note by its title. Search your notes for a phrase. 3 tools."


def test_a_tool_description_that_names_the_mechanism_is_skipped(client):
    p = client.app.state.platform
    p.config.mcp_servers = [{"name": "bare", "command": "x"}]
    # The registry's own fallback description reads "Remote MCP tool …".
    from iron_jarvis.mcp.tools import MCPRemoteTool

    p.registry.register(MCPRemoteTool(client=None, server_name="bare", remote_name="go"), mcp=True)  # type: ignore[arg-type]
    assert _row(client, "bare")["blurb"] == "1 tool."
    p.config.mcp_servers = [{"name": "dark", "command": "x"}]
    assert _row(client, "dark")["blurb"] == "A tool you added."


def test_a_blurb_never_carries_a_secret_shaped_string(client):
    p = client.app.state.platform
    p.config.mcp_servers = [{"name": "leaky", "command": "x"}]
    token = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    p.registry.register(_PackTool("mcp__leaky__call", f"Calls GitHub with {token} for you."), mcp=True)
    assert token not in _row(client, "leaky")["blurb"]


def test_no_row_a_card_renders_says_mcp(client):
    from iron_jarvis.ltm.sources import CustomSourceStore

    p = client.app.state.platform
    p.config.mcp_servers = [{"name": "bare", "command": "x"}]
    from iron_jarvis.mcp.tools import MCPRemoteTool

    p.registry.register(MCPRemoteTool(client=None, server_name="bare", remote_name="go"), mcp=True)  # type: ignore[arg-type]
    CustomSourceStore(p.engine).add(name="brain", kind="mcp", config_json="{}")
    rx = re.compile(r"\bMCP\b", re.IGNORECASE)
    rows = _rows(client)
    assert {r["connect_via"] for r in rows} >= {"mcp", "oauth", "api_key", "memory", "link"}
    for r in rows:
        shown = [r["name"], r["category"], r["blurb"], r["unlocks"], *r.get("scopes", []),
                 r.get("needs") or "", r.get("package_note") or "", r.get("action") or "",
                 *(f.get("label", "") + " " + f.get("help", "") for f in r.get("fields", []))]
        for text in shown:
            assert not rx.search(text), (r["id"], text)


def test_every_connect_via_the_daemon_sends_is_one_the_directory_knows(client):
    src = DIRECTORY_TSX.read_text(encoding="utf-8").replace("\r\n", "\n")
    m = re.search(r"type ConnectVia = ([^;]+);", src)
    assert m, "the Directory's ConnectVia type"
    known = set(re.findall(r'"([a-z_]+)"', m.group(1)))
    title = re.search(r"const VIA_TITLE: Record<ConnectVia, string> = \{([^}]+)\}", src)
    assert title, "every mechanism has its tooltip"
    titled = set(re.findall(r"^\s*([a-z_]+):", title.group(1), re.M))
    sent = {r["connect_via"] for r in _rows(client)}
    assert sent <= known and known == titled, (sent, known, titled)


def test_link_cards_stay_out_of_the_connectable_catalog():
    assert {c.id for c in LINK_CARDS}.isdisjoint({c.id for c in CATALOG})
    assert all(c.connect_via == catalog_mod.LINK and c.href and c.action for c in LINK_CARDS)
    assert catalog_mod.LINK not in catalog_mod.CONNECT_VIA
