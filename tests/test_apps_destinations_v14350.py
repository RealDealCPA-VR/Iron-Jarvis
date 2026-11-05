"""v1.435.0 (chat-first plan, Wave 3 group H): apps and notification
destinations from chat — ``settings/records_apps``.

Every change test drives the REAL stream lane (``/chat/stream``), the real
permission gate (an ask-tier call pauses on the approval card, answered
"Allow once" through ``/chat/approvals``), the real ``registry.invoke`` and
the real Undo route (``POST /undo/{id}``, found through the card's
``change_id``). Each Undo is checked against the stored row as it was, and
— for a removal — against the KEYS it owned, which a removal deletes and the
Undo must bring back.

A credential is never a tool argument, never in a log row, never on a card:
the tests read the frames, the ledger rows and the undo journal for each
planted secret value.

Reading other programs' settings (Claude Desktop) runs under SCRATCH homes
(``USERPROFILE`` / ``HOME`` / ``APPDATA`` swapped); a module-level guard
proves the user's real files untouched. No real app server is ever started:
every saved app's program is a launcher that cannot exist.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.mcp import tools as mcp_tools_mod
from iron_jarvis.settings import records as R
from iron_jarvis.settings import records_apps as RA
from tests.fixtures.real_config_view import real_view
from tests.test_chat_approvals_v1187 import _asgi_post, _drive_stream
from tests.test_settings_chat_tools_v1321 import _body, _calls_stream, _done, _undo_card

ROOT = Path(__file__).resolve().parents[1]

#: A launcher that cannot exist: a load fails at once (no child, no network).
NO_SUCH = "ij-no-such-app-launcher-v14350"
TOKEN = "ntn_SECRET_v14350_never_in_chat"
OLD_TOKEN = "conn_SECRET_v14350_legacy"
TG_TOKEN = "123456789:AAbbCCddEEffGG-v14350-SECRET"
HOOK = "https://discord.com/api/webhooks/1234/SECRET-v14350-hook"
QB_TOKEN = "qb_SECRET_v14350_from_disk"

WRITERS = ("app_switch", "app_disconnect", "app_import", "channel_remove", "channel_alerts")

# --------------------------------------------------------------------------- #
# The user's REAL files, read before any fixture moves HOME.
# --------------------------------------------------------------------------- #
_REAL_HOME = Path(os.path.expanduser("~"))
_REAL_APPDATA = Path(os.environ.get("APPDATA") or "") if os.environ.get("APPDATA") else None
_REAL_FILES: list[Path] = [_REAL_HOME / ".claude.json", _REAL_HOME / ".cursor" / "mcp.json"]
if _REAL_APPDATA is not None:
    _REAL_FILES.append(_REAL_APPDATA / "Claude" / "claude_desktop_config.json")
_REAL_BEFORE = {p: real_view(p) for p in _REAL_FILES}


@pytest.fixture(scope="module", autouse=True)
def _real_files_untouched():
    yield
    changed = [str(p) for p in _REAL_FILES if real_view(p) != _REAL_BEFORE[p]]
    assert not changed, f"a test touched the user's real config files: {changed}"


@pytest.fixture(autouse=True)
def _fresh_load_records():
    """The load record is process-wide (the v1.325.2 lesson)."""
    mcp_tools_mod._LOAD_STATUS.clear()
    mcp_tools_mod._ANNOUNCED.clear()
    mcp_tools_mod._LOAD_CFGS.clear()
    RA.apps_tools_for.cache_clear()
    yield
    mcp_tools_mod._LOAD_STATUS.clear()
    mcp_tools_mod._ANNOUNCED.clear()
    mcp_tools_mod._LOAD_CFGS.clear()


@pytest.fixture
def homes(tmp_path, monkeypatch) -> dict[str, Path]:
    user = tmp_path / "user"
    appdata = tmp_path / "appdata"
    user.mkdir()
    appdata.mkdir()
    monkeypatch.setenv("USERPROFILE", str(user))
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return {"user": user, "appdata": appdata}


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #
def _app(tmp_path, specs):
    app = create_app(str(tmp_path / "ij"))
    app.state.platform.router.stream = _calls_stream(specs)
    return app


async def _approve_all(app, seen):
    async def approve(data):
        seen.append(data["tool"])
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "once"})
        assert status == 200

    return approve


async def _never(data):  # pragma: no cover
    raise AssertionError(f"this call must not card: {data}")


def _card(frames, title):
    cards = [c for c in _done(frames)["config_cards"] if c.get("title") == title]
    assert len(cards) == 1, _done(frames)["config_cards"]
    return cards[0]


def _finished(frames, tool):
    return [
        d for ev, d in frames
        if ev == "tool_call" and d.get("status") == "finished" and d.get("tool", d.get("name")) == tool
    ]


def _text(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _db_text(p) -> str:
    """Every ledger row and undo journal row, as text — where a secret must
    never appear."""
    from sqlmodel import select

    from iron_jarvis.core.models import ToolInvocation, UndoJournal

    with session_scope(p.engine) as db:
        rows = [f"{r.args_json}\n{r.output}" for r in db.exec(select(ToolInvocation))]
        rows += [str(r.pre_inline or "") for r in db.exec(select(UndoJournal))]
    return "\n".join(rows)


def _vault_names(p) -> list[str]:
    return sorted(str(r.get("name")) for r in p.secrets.list())


def _seed_app(p, name, **extra):
    from iron_jarvis.mcp.pack_door import install_config

    cfg = {"name": name, "command": NO_SUCH, "args": ["serve"], "env": {"PLAIN": "kept"}, **extra}
    install_config(p, cfg, load=False)
    return R.snap_app(p, name)


# =========================================================================== #
# app_switch
# =========================================================================== #
@pytest.mark.asyncio
async def test_app_switch_off_asks_keeps_the_keys_and_undo_turns_it_back_on(tmp_path):
    app = _app(tmp_path, [("app_switch", {"app": "github", "on": False})])
    p = app.state.platform
    before = _seed_app(p, "github", env_secrets={"GITHUB_TOKEN": "mcp.github.GITHUB_TOKEN"})
    p.secrets.set("mcp.github.GITHUB_TOKEN", TOKEN, kind="api_key")
    seen: list[str] = []
    frames = await _drive_stream(app, _body("turn off the GitHub app"), await _approve_all(app, seen))
    assert seen == ["app_switch"], "switching an app off must ask first"
    row = R.snap_app(p, "github")
    assert row["enabled"] is False and row["env_secrets"] == before["env_secrets"]
    assert p.secrets.get("mcp.github.GITHUB_TOKEN") == TOKEN, "off keeps the key"
    card = _card(frames, "App switched off")
    assert card["old"] == "on" and card["new"] == "off"
    await _undo_card(app, card)
    assert R.snap_app(p, "github") == before, "Undo puts the row back exactly"


@pytest.mark.asyncio
async def test_app_switch_on_reloads_and_undo_switches_it_off_again(tmp_path):
    app = _app(tmp_path, [("app_switch", {"app": "Notion", "on": True})])
    p = app.state.platform
    before = _seed_app(p, "notion", enabled=False)
    seen: list[str] = []
    frames = await _drive_stream(app, _body("switch Notion back on"), await _approve_all(app, seen))
    assert seen == ["app_switch"]
    assert "enabled" not in R.snap_app(p, "notion"), "on takes the switch off the row"
    out = _finished(frames, "app_switch")[0]
    assert "did not start" in str(out.get("output") or out), "a launcher that cannot run is said, never 'on'"
    await _undo_card(app, _card(frames, "App switched on"))
    assert R.snap_app(p, "notion") == before


@pytest.mark.asyncio
async def test_app_switch_that_changes_nothing_shows_no_card(tmp_path):
    app = _app(tmp_path, [("app_switch", {"app": "github", "on": False}), ("app_switch", {"app": "nope", "on": False})])
    p = app.state.platform
    _seed_app(p, "github", enabled=False)
    frames = await _drive_stream(app, _body("turn off the GitHub app"), _never)
    results = _finished(frames, "app_switch")
    assert len(results) == 2
    assert "already off" in json.dumps(results[0])
    assert "no app called 'nope'" in json.dumps(results[1])
    assert _done(frames)["config_cards"] == []


@pytest.mark.asyncio
async def test_app_switch_never_starts_a_copy_of_a_locked_app(tmp_path):
    """v1.428.0: Microsoft 365 runs only from its locked card. A copy the
    user added another way is never started — refused with no card."""
    app = _app(tmp_path, [("app_switch", {"app": "outlook-copy", "on": True})])
    p = app.state.platform
    _seed_app(p, "outlook-copy", enabled=False, args=["-y", "@softeria/ms-365-mcp-server"])
    frames = await _drive_stream(app, _body("turn the outlook-copy app back on"), _never)
    assert R.snap_app(p, "outlook-copy")["enabled"] is False
    assert "locked down" in _text(_finished(frames, "app_switch"))


@pytest.mark.asyncio
async def test_app_switch_off_of_a_locked_app_is_allowed_and_its_rules_stay(tmp_path):
    """The locked card's switch stays editable (routes/agents.update_mcp_pack
    lets `enabled` through); everything else on the row is kept."""
    app = _app(tmp_path, [("app_switch", {"app": "microsoft_365", "on": False})])
    p = app.state.platform
    before = _seed_app(p, "microsoft_365", locked="ms365", local_only=True)
    seen: list[str] = []
    frames = await _drive_stream(app, _body("turn off the Microsoft 365 app"), await _approve_all(app, seen))
    assert seen == ["app_switch"]
    row = R.snap_app(p, "microsoft_365")
    assert row["enabled"] is False and row["locked"] == "ms365" and row["local_only"] is True
    await _undo_card(app, _card(frames, "App switched off"))
    assert R.snap_app(p, "microsoft_365") == before


# =========================================================================== #
# app_disconnect
# =========================================================================== #
@pytest.mark.asyncio
async def test_app_disconnect_removes_the_app_and_undo_brings_its_keys_back(tmp_path):
    app = _app(tmp_path, [("app_disconnect", {"app": "Notion"})])
    p = app.state.platform
    # One key under the app's own rows, one under an older name (v1.355.0's
    # `conn_*`), and its manifest.
    before = _seed_app(
        p, "notion",
        env_secrets={"NOTION_TOKEN": "mcp.notion.NOTION_TOKEN", "OLD": "conn_notion_token"},
    )
    p.secrets.set("mcp.notion.NOTION_TOKEN", TOKEN, kind="api_key")
    p.secrets.set("conn_notion_token", OLD_TOKEN, kind="api_key")
    from iron_jarvis.mcp.manifest import PackManifest

    manifest = PackManifest(p.config.home).path("notion")
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text('{"tools": {"search": {}}}', encoding="utf-8")
    vault_before = _vault_names(p)

    seen: list[str] = []
    frames = await _drive_stream(app, _body("disconnect Notion"), await _approve_all(app, seen))
    assert seen == ["app_disconnect"], "a disconnect must ask first"
    assert R.snap_app(p, "notion") is None
    assert p.secrets.get("mcp.notion.NOTION_TOKEN") is None and p.secrets.get("conn_notion_token") is None
    assert not manifest.exists()
    # Kept for the Undo — encrypted, never in the journal, the ledger or the stream.
    text = _db_text(p) + json.dumps(frames, default=str)
    assert TOKEN not in text and OLD_TOKEN not in text
    assert any(n.startswith(RA.BACKUP_PREFIX) for n in _vault_names(p))

    await _undo_card(app, _card(frames, "App disconnected"))
    assert R.snap_app(p, "notion") == before
    assert p.secrets.get("mcp.notion.NOTION_TOKEN") == TOKEN
    assert p.secrets.get("conn_notion_token") == OLD_TOKEN
    assert manifest.read_text(encoding="utf-8") == '{"tools": {"search": {}}}'
    assert _vault_names(p) == vault_before, "the backups are gone once used"


@pytest.mark.asyncio
async def test_a_second_undo_of_a_disconnect_is_refused_in_words(tmp_path):
    app = _app(tmp_path, [("app_disconnect", {"app": "sentry"})])
    p = app.state.platform
    _seed_app(p, "sentry")
    seen: list[str] = []
    frames = await _drive_stream(app, _body("remove the Sentry connector"), await _approve_all(app, seen))
    card = _card(frames, "App disconnected")
    await _undo_card(app, card)
    client = TestClient(app)
    action = client.get(f"/config/changes/{card['change_id']}").json()["action_id"]
    r = client.post(f"/undo/{action}")
    assert r.status_code in (409, 410), r.text


@pytest.mark.asyncio
async def test_undo_of_a_disconnect_never_overwrites_the_app_connected_again_since(tmp_path):
    """The user disconnected Notion, then connected it again with a NEW key.
    The old Undo must refuse in words — never put the old row and the old
    key over the ones the user set up since."""
    app = _app(tmp_path, [("app_disconnect", {"app": "notion"})])
    p = app.state.platform
    _seed_app(p, "notion", env_secrets={"NOTION_TOKEN": "mcp.notion.NOTION_TOKEN"})
    p.secrets.set("mcp.notion.NOTION_TOKEN", TOKEN, kind="api_key")
    seen: list[str] = []
    frames = await _drive_stream(app, _body("disconnect Notion"), await _approve_all(app, seen))
    card = _card(frames, "App disconnected")
    again = _seed_app(p, "notion", args=["new-serve"], env_secrets={"NOTION_TOKEN": "mcp.notion.NOTION_TOKEN"})
    p.secrets.set("mcp.notion.NOTION_TOKEN", "ntn_NEW_v14350", kind="api_key")
    client = TestClient(app)
    action = client.get(f"/config/changes/{card['change_id']}").json()["action_id"]
    r = client.post(f"/undo/{action}")
    assert r.status_code == 409 and "added again" in r.text, r.text
    assert R.snap_app(p, "notion") == again
    assert p.secrets.get("mcp.notion.NOTION_TOKEN") == "ntn_NEW_v14350"


@pytest.mark.asyncio
async def test_app_disconnect_of_a_browser_signin_app_names_its_card_and_shows_no_card(tmp_path):
    app = _app(tmp_path, [("app_disconnect", {"app": "google_drive"})])
    frames = await _drive_stream(app, _body("disconnect Google Drive"), _never)
    out = _text(_finished(frames, "app_disconnect"))
    assert "signs in through the browser" in out and "Settings › Connections" in out


@pytest.mark.asyncio
async def test_app_disconnect_refuses_without_an_undo_plan_and_removes_nothing(tmp_path, monkeypatch):
    """Never a removal the Undo cannot reverse: with no plan (the snapshot
    failed), nothing is removed."""
    app = _app(tmp_path, [("app_disconnect", {"app": "sentry"})])
    p = app.state.platform
    _seed_app(p, "sentry")
    monkeypatch.setitem(R._SNAP, "app_removed", lambda platform, name: (_ for _ in ()).throw(OSError("x")))
    seen: list[str] = []
    frames = await _drive_stream(app, _body("remove the Sentry connector"), await _approve_all(app, seen))
    assert R.snap_app(p, "sentry") is not None
    assert RA.NO_UNDO_PLAN in _text(_finished(frames, "app_disconnect"))


# =========================================================================== #
# app_import
# =========================================================================== #
def _claude_desktop(homes, servers: dict) -> Path:
    path = homes["appdata"] / "Claude" / "claude_desktop_config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    return path


QB = {"command": NO_SUCH, "args": ["qb-server"], "env": {"QB_TOKEN": QB_TOKEN, "QB_COMPANY": "C:/Books/acme.qbw"}}


@pytest.mark.asyncio
async def test_app_import_lists_what_other_apps_have_without_a_card_and_without_a_value(tmp_path, homes):
    _claude_desktop(homes, {"quickbooks": QB})
    app = _app(tmp_path, [("app_import", {})])
    frames = await _drive_stream(app, _body("add the QuickBooks server I use in Claude Desktop"), _never)
    out = _text(_finished(frames, "app_import"))
    assert "Claude Desktop (claude-desktop): quickbooks" in out
    assert QB_TOKEN not in json.dumps(frames, default=str)
    assert R.snap_app(app.state.platform, "quickbooks") is None


@pytest.mark.asyncio
async def test_app_import_asks_adds_with_the_key_from_disk_and_undo_removes_it(tmp_path, homes):
    planted = _claude_desktop(homes, {"quickbooks": QB})
    disk_before = planted.read_bytes()
    app = _app(tmp_path, [("app_import", {"name": "quickbooks", "source": "claude-desktop"})])
    p = app.state.platform
    seen: list[str] = []
    frames = await _drive_stream(app, _body("add the QuickBooks server I use in Claude Desktop"), await _approve_all(app, seen))
    assert seen == ["app_import"], "an import must ask first"
    row = R.snap_app(p, "quickbooks")
    assert row is not None and row["command"] == NO_SUCH
    # The key went from the disk straight into the vault — never through the
    # model, the card, the ledger or config.toml.
    assert row["env_secrets"] == {"QB_TOKEN": "mcp.quickbooks.QB_TOKEN"}
    assert p.secrets.get("mcp.quickbooks.QB_TOKEN") == QB_TOKEN
    toml = (Path(p.config.home) / "config.toml").read_text(encoding="utf-8")
    assert QB_TOKEN not in toml
    assert QB_TOKEN not in json.dumps(frames, default=str) + _db_text(p)
    card = _card(frames, "App added")
    assert "Claude Desktop" in card["label"]
    assert planted.read_bytes() == disk_before, "the other app's file is read, never written"
    await _undo_card(app, card)
    assert R.snap_app(p, "quickbooks") is None
    assert p.secrets.get("mcp.quickbooks.QB_TOKEN") is None


@pytest.mark.asyncio
async def test_app_import_of_a_server_whose_settings_leave_a_value_to_type_is_refused_with_no_card(tmp_path, homes):
    _claude_desktop(homes, {"quickbooks": {"command": NO_SUCH, "env": {"QB_TOKEN": "${input:qb_token}"}}})
    app = _app(tmp_path, [("app_import", {"name": "quickbooks"})])
    frames = await _drive_stream(app, _body("add the QuickBooks server I use in Claude Desktop"), _never)
    out = _text(_finished(frames, "app_import"))
    assert "Settings › Tools" in out and "never type it in chat" in out
    assert R.snap_app(app.state.platform, "quickbooks") is None


@pytest.mark.asyncio
async def test_app_import_of_an_app_already_added_or_missing_shows_no_card(tmp_path, homes):
    _claude_desktop(homes, {"quickbooks": QB})
    app = _app(tmp_path, [("app_import", {"name": "quickbooks"}), ("app_import", {"name": "jira"})])
    _seed_app(app.state.platform, "quickbooks")
    frames = await _drive_stream(app, _body("import my MCP servers from Claude Desktop"), _never)
    results = _text(_finished(frames, "app_import"))
    assert "already added" in results
    assert "No other app on this PC has a server called 'jira'" in results


@pytest.mark.asyncio
async def test_app_import_never_adds_a_copy_of_a_locked_app(tmp_path, homes):
    _claude_desktop(homes, {"outlook": {"command": NO_SUCH, "args": ["-y", "@softeria/ms-365-mcp-server"]}})
    app = _app(tmp_path, [("app_import", {"name": "outlook"})])
    seen: list[str] = []
    frames = await _drive_stream(app, _body("import the outlook server from Claude Desktop"), await _approve_all(app, seen))
    assert R.snap_app(app.state.platform, "outlook") is None
    assert "locked down" in _text(_finished(frames, "app_import"))


# =========================================================================== #
# channel_remove
# =========================================================================== #
def _add_channel(app, name, cfg):
    R.apply_channel(app.state.platform, name, cfg)


@pytest.mark.asyncio
async def test_channel_remove_asks_drops_the_keys_and_undo_brings_everything_back(tmp_path):
    app = _app(tmp_path, [("channel_remove", {"name": "telegram"}), ("channel_remove", {"name": "alerts"})])
    p = app.state.platform
    tg = {"type": "telegram", "chat_id": "42", "token_secret": "channel_phone_token", "allowed_senders": ["42"],
          "events": ["provider.failed"]}
    _add_channel(app, "phone", tg)
    p.secrets.set("channel_phone_token", TG_TOKEN, kind="token")
    dc = {"type": "discord", "webhook_url": HOOK}
    _add_channel(app, "alerts", dc)
    vault_before = _vault_names(p)
    seen: list[str] = []
    frames = await _drive_stream(app, _body("stop sending to Telegram and remove the Discord channel"), await _approve_all(app, seen))
    assert seen and set(seen) == {"channel_remove"}, "a removal must ask first"
    channels = p.config.comm.get("channels") or {}
    assert "phone" not in channels and "alerts" not in channels
    assert p.notifier.get("phone") is None and p.notifier.get("alerts") is None
    assert p.secrets.get("channel_phone_token") is None
    text = _db_text(p) + json.dumps(frames, default=str)
    assert TG_TOKEN not in text and HOOK not in text, "a key is kept encrypted, never in the journal"
    for c in [c for c in _done(frames)["config_cards"] if c.get("title") == "Destination removed"]:
        await _undo_card(app, c)
    channels = p.config.comm.get("channels") or {}
    assert channels["phone"] == tg and channels["alerts"] == dc
    assert p.secrets.get("channel_phone_token") == TG_TOKEN
    assert p.notifier.get("phone") is not None and p.notifier.get("alerts") is not None
    assert _vault_names(p) == vault_before, "the backups are gone once used"


@pytest.mark.asyncio
async def test_undo_of_a_removal_never_overwrites_a_destination_added_again_since(tmp_path):
    app = _app(tmp_path, [("channel_remove", {"name": "phone"})])
    p = app.state.platform
    _add_channel(app, "phone", {"type": "telegram", "chat_id": "42", "token_secret": "channel_phone_token"})
    p.secrets.set("channel_phone_token", TG_TOKEN, kind="token")
    seen: list[str] = []
    frames = await _drive_stream(app, _body("delete the telegram destination"), await _approve_all(app, seen))
    card = _card(frames, "Destination removed")
    new = {"type": "telegram", "chat_id": "99", "token_secret": "channel_phone_token"}
    _add_channel(app, "phone", new)
    p.secrets.set("channel_phone_token", "999:NEW-v14350", kind="token")
    client = TestClient(app)
    action = client.get(f"/config/changes/{card['change_id']}").json()["action_id"]
    r = client.post(f"/undo/{action}")
    assert r.status_code == 409 and "added again" in r.text, r.text
    assert p.config.comm["channels"]["phone"] == new
    assert p.secrets.get("channel_phone_token") == "999:NEW-v14350"


@pytest.mark.asyncio
async def test_channel_remove_refuses_a_built_in_one_an_unknown_one_and_an_ambiguous_type(tmp_path):
    app = _app(tmp_path, [
        ("channel_remove", {"name": "this-pc"}),
        ("channel_remove", {"name": "pager"}),
        ("channel_remove", {"name": "slack"}),
    ])
    _add_channel(app, "work", {"type": "slack", "webhook_url": "https://hooks.slack.com/services/a"})
    _add_channel(app, "home", {"type": "slack", "webhook_url": "https://hooks.slack.com/services/b"})
    frames = await _drive_stream(app, _body("remove the Slack channel"), _never)
    out = _text(_finished(frames, "channel_remove"))
    assert "built in" in out
    assert "no destination called 'pager'" in out
    assert "2 slack destinations" in out and "say which one" in out
    assert set(app.state.platform.config.comm["channels"]) == {"work", "home"}


def test_the_notifications_page_remove_goes_through_the_same_removal(tmp_path):
    """DELETE /comm/channels/{name} is `records_apps.remove_destination`."""
    app = create_app(str(tmp_path / "ij"))
    client = TestClient(app)
    p = app.state.platform
    _add_channel(app, "phone", {"type": "telegram", "chat_id": "42", "token_secret": "channel_phone_token"})
    p.secrets.set("channel_phone_token", TG_TOKEN, kind="token")
    calls: list[str] = []
    real = RA.remove_destination

    def spy(platform, name):
        calls.append(name)
        return real(platform, name)

    import iron_jarvis.settings.records_apps as mod

    old = mod.remove_destination
    mod.remove_destination = spy
    try:
        assert client.delete("/comm/channels/phone").json() == {"name": "phone", "removed": True}
    finally:
        mod.remove_destination = old
    assert calls == ["phone"]
    assert p.secrets.get("channel_phone_token") is None
    assert "phone" not in (p.config.comm.get("channels") or {})


# =========================================================================== #
# channel_alerts
# =========================================================================== #
@pytest.mark.asyncio
async def test_channel_alerts_narrows_says_what_it_cannot_do_and_undo_puts_back_every_alert(tmp_path):
    app = _app(tmp_path, [("channel_alerts", {"name": "phone", "alerts": ["provider.failed", "goal.tripped"]})])
    p = app.state.platform
    _add_channel(app, "phone", {"type": "telegram", "chat_id": "42", "token_secret": "channel_phone_token"})
    seen: list[str] = []
    frames = await _drive_stream(app, _body("only send me failures on telegram"), await _approve_all(app, seen))
    assert seen == ["channel_alerts"]
    assert p.config.comm["channels"]["phone"]["events"] == ["provider.failed", "goal.tripped"]
    assert p.notifier.get("phone").config["events"] == ["provider.failed", "goal.tripped"], "the live one follows"
    assert RA.ONLY_FAILED_NOT_YET in _text(_finished(frames, "channel_alerts"))
    card = _card(frames, "Alerts changed")
    assert card["old"] == "every alert" and card["new"].startswith("2 alerts: A model failed")
    await _undo_card(app, card)
    assert "events" not in p.config.comm["channels"]["phone"]


@pytest.mark.asyncio
async def test_channel_alerts_all_and_bad_choices(tmp_path):
    app = _app(tmp_path, [
        ("channel_alerts", {"name": "phone", "alerts": ["session.failed"]}),
        ("channel_alerts", {"name": "phone", "all": True}),
    ])
    p = app.state.platform
    _add_channel(app, "phone", {"type": "telegram", "chat_id": "42", "events": ["provider.failed"]})
    seen: list[str] = []
    frames = await _drive_stream(app, _body("change which alerts Telegram gets"), await _approve_all(app, seen))
    results = _finished(frames, "channel_alerts")
    assert "Not an alert a destination can get: session.failed" in json.dumps(results[0])
    assert "events" not in p.config.comm["channels"]["phone"], "all = every alert"


def test_alert_words_are_the_notifications_pages_words_for_every_default_alert():
    from iron_jarvis.comm.notifier import DEFAULT_ALERT_EVENTS

    assert set(RA.ALERT_WORDS) == {str(e) for e in DEFAULT_ALERT_EVENTS}
    page = (ROOT / "dashboard" / "components" / "settings" / "pages" / "NotificationsPage.tsx").read_text(encoding="utf-8")
    import re

    labels = dict(re.findall(r'\{ key: "([\w.]+)", label: "([^"]+)" \}', page))
    assert labels == RA.ALERT_WORDS
    assert set(RA.PROBLEM_ALERTS) <= set(RA.ALERT_WORDS)


# =========================================================================== #
# channel_connect: email
# =========================================================================== #
@pytest.mark.asyncio
async def test_email_destination_takes_its_password_on_the_secure_card_and_undo_removes_it(tmp_path):
    app = _app(tmp_path, [("channel_connect", {
        "name": "mail", "type": "email", "host": "smtp.example.com", "port": 587,
        "from_addr": "me@example.com", "to_addr": "me@example.com",
    })])
    p = app.state.platform
    seen: list[str] = []
    frames = await _drive_stream(app, _body("add an email destination for alerts to me@example.com"), await _approve_all(app, seen))
    assert seen == ["channel_connect"]
    cfg = p.config.comm["channels"]["mail"]
    assert cfg == {
        "type": "email", "host": "smtp.example.com", "from_addr": "me@example.com", "to_addr": "me@example.com",
        "port": 587, "username": "me@example.com", "password_secret": "channel_mail_token",
    }
    secret = next(c for c in _done(frames)["config_cards"] if c["kind"] == "secret")
    assert secret["name"] == "channel.mail"
    status, raw = await _asgi_post(app, "/config/secret", {"name": "channel.mail", "value": "app-password-v14350"})
    assert status == 200 and b"app-password-v14350" not in raw
    assert p.secrets.get("channel_mail_token") == "app-password-v14350"
    assert p.notifier.get("mail") is not None
    await _undo_card(app, _card(frames, "Channel added"))
    assert "mail" not in (p.config.comm.get("channels") or {})


@pytest.mark.asyncio
async def test_a_password_handed_to_channel_connect_is_refused_before_any_card_and_never_logged(tmp_path):
    app = _app(tmp_path, [("channel_connect", {
        "name": "mail", "type": "email", "host": "smtp.example.com",
        "from_addr": "me@example.com", "to_addr": "me@example.com", "password": "hunter2-v14350",
    })])
    p = app.state.platform
    frames = await _drive_stream(app, _body("add an email destination"), _never)
    assert "A password never goes through chat" in _text(_finished(frames, "channel_connect"))
    assert "mail" not in (p.config.comm.get("channels") or {})
    assert "hunter2-v14350" not in _db_text(p) + json.dumps(frames, default=str)


@pytest.mark.asyncio
async def test_an_email_destination_with_a_bad_address_is_refused(tmp_path):
    app = _app(tmp_path, [("channel_connect", {
        "name": "mail", "type": "email", "host": "smtp.example.com", "from_addr": "me", "to_addr": "me@example.com",
    })])
    seen: list[str] = []
    frames = await _drive_stream(app, _body("add an email destination"), await _approve_all(app, seen))
    assert "`from_addr` must be an email address" in _text(_finished(frames, "channel_connect"))
    assert "mail" not in (app.state.platform.config.comm.get("channels") or {})


# =========================================================================== #
# Registration
# =========================================================================== #
def test_every_tool_is_registered_ask_tier_low_trust_denied_and_worded(tmp_path):
    from iron_jarvis.agents import mission
    from iron_jarvis.core.trust import LOW_TRUST_DENY

    app = create_app(str(tmp_path / "ij"))
    p = app.state.platform
    for name in WRITERS:
        assert p.registry.get(name) is not None, name
        assert R.RECORD_TOOL_TIERS[name] == RA.APPS_TOOL_TIERS[name] == "ask"
        assert p.permissions.authorize(name, {}).allowed is False, f"{name} must ask"
        assert name in LOW_TRUST_DENY
        assert name in R.RECORD_CARD_TOOLS
        assert name in mission.ASK_WORDS and name in mission._RUN_TOOL_WORDS
    words = (ROOT / "dashboard" / "lib" / "toolWords.ts").read_text(encoding="utf-8")
    run = (ROOT / "dashboard" / "lib" / "runWords.ts").read_text(encoding="utf-8")
    for name in WRITERS:
        assert f'  {name}: "{mission.ASK_WORDS[name]}",' in words
        did = mission._RUN_TOOL_WORDS[name][0]
        assert f'{name}: {{ did: "{did[0].upper()}{did[1:]}"' in run


def test_the_engine_words_never_reach_a_refusal():
    """The app's words are "app" and "destination"."""
    import re

    for words in (
        RA.NO_UNDO_PLAN, RA.ONLY_FAILED_NOT_YET, RA.IMPORT_NEEDS_VALUES, R.PASSWORD_NOT_IN_CHAT,
    ):
        assert not re.search(r"\b(?:daemon|pack|mcp|endpoint)s?\b", words, re.I), words


# =========================================================================== #
# Arming: requests offer the tool; everyday sentences arm no writer of these
# =========================================================================== #
REQUESTS: list[tuple[str, str]] = [
    ("turn off the GitHub app", "app_switch"),
    ("switch off Notion", "app_switch"),
    ("disable the Sentry connector", "app_switch"),
    ("turn the GitHub app off for now", "app_switch"),
    ("turn GitHub back on", "app_switch"),
    ("pause the Stripe app", "app_switch"),
    ("enable Gmail", "app_switch"),
    ("turn on the QuickBooks server", "app_switch"),
    ("please turn off Dropbox", "app_switch"),
    ("disconnect Notion", "app_disconnect"),
    ("remove the Sentry connector", "app_disconnect"),
    ("uninstall the QuickBooks server", "app_disconnect"),
    ("delete the GitHub app", "app_disconnect"),
    ("remove Notion from my apps", "app_disconnect"),
    ("get rid of the Stripe integration", "app_disconnect"),
    ("disconnect Slack", "app_disconnect"),
    ("add the QuickBooks server I use in Claude Desktop", "app_import"),
    ("import my MCP servers from Cursor", "app_import"),
    ("bring over my Claude Desktop tools", "app_import"),
    ("add QuickBooks from Claude Desktop", "app_import"),
    ("get the GitHub tools from Cursor", "app_import"),
    ("copy over the servers I have in VS Code", "app_import"),
    ("import Codex's servers", "app_import"),
    ("set up the Jira connector I use in Claude Code", "app_import"),
    ("stop sending to Slack", "channel_remove"),
    ("remove the Slack channel", "channel_remove"),
    ("delete the telegram destination", "channel_remove"),
    ("stop sending me notifications on Slack", "channel_remove"),
    ("remove my email destination", "channel_remove"),
    ("disconnect Telegram", "channel_remove"),
    ("get rid of the Discord channel", "channel_remove"),
    ("only send me failures", "channel_alerts"),
    ("only notify me when something fails", "channel_alerts"),
    ("only message me if a task fails on Telegram", "channel_alerts"),
    ("send me only the important alerts", "channel_alerts"),
    ("stop notifying me about finished tasks", "channel_alerts"),
    ("don't send me alerts about skill suggestions", "channel_alerts"),
    ("change which alerts Slack gets", "channel_alerts"),
    ("mute task alerts on telegram", "channel_alerts"),
    ("only email me about errors", "channel_alerts"),
]
NEAR_MISSES: list[str] = [
    "I sent it on Slack",
    "switch apps on my phone",
    "the email destination address",
    "turn on track changes in the document",
    "enable macros in this workbook",
    "turn off the lights",
    "remove the duplicates from this list",
    "delete the GitHub issue",
    "delete my Dropbox files",
    "remove the slack from the rope",
    "copy the text from VS Code",
    "import the settings from VS Code",
    "add a breakpoint in VS Code",
    "set up python in vs code",
    "I use extensions in VS Code",
    "only send me the PDF",
    "don't message me about it",
    "set alerts on my bank account",
    "change the notification settings in Windows",
    "I got too many notifications from my bank",
    "stop sending emails to my client",
    "delete the Slack thread",
    "remove the Slack message I sent",
    "is the GitHub app on?",
    "what alerts does Slack get?",
    "should I disconnect Notion?",
    "connect my Notion",
    "summarize the K-1 I attached",
    "turn the power off on the printer",
    "what's on TV channel 5 tonight",
    "cut me some slack on the deadline",
    "add slack time to the project plan",
    "pause the video",
    "drop the table",
    "remove the header from the document",
    "turn off notifications on my iPhone",
    "use Claude Code to refactor my repo",
    "I moved the file in Cursor",
    "the GitHub app is great",
    "uninstall the printer driver",
    "stop sending me the newsletter",
]


@pytest.mark.parametrize("text,tool", REQUESTS)
def test_a_request_offers_its_tool(text, tool):
    assert tool in R.record_tools_for(text), R.record_tools_for(text)


@pytest.mark.parametrize("text", NEAR_MISSES)
def test_an_everyday_sentence_or_a_question_arms_none_of_these_writers(text):
    got = [n for n in R.record_tools_for(text) if n in WRITERS]
    assert got == [], got


def test_the_table_is_big_enough_both_ways():
    assert len(REQUESTS) >= 30 and len(NEAR_MISSES) >= 30
    assert {t for _r, t in REQUESTS} == set(WRITERS)


@pytest.fixture(scope="module")
def d(tmp_path_factory):
    return SimpleNamespace(platform=create_app(str(tmp_path_factory.mktemp("arm") / "home")).state.platform)


@pytest.mark.parametrize("text,tool", REQUESTS[::4])
def test_the_stream_lane_shows_them_and_the_post_lane_never_arms_them(d, text, tool):
    """The stream lane's ask tier (``settings_ask_tools``) offers the tool,
    visible, never granted; the POST lane (``_resolve_armed_tools``, which
    cannot show a card) never arms any of them."""
    body = ChatBody(messages=[{"role": "user", "content": text}], auto_tools=True)
    armed = list(CT._resolve_armed_tools(d, body)[0])
    assert not set(armed) & set(WRITERS), armed
    assert tool in CT.settings_ask_tools(d, text, armed, [])


# --------------------------------------------------------------------------- #
# Linear time: the matchers read pasted text on the event loop.
# --------------------------------------------------------------------------- #
_PATHOLOGICAL = r"""
import sys, time
sys.path.insert(0, sys.argv[1])
from iron_jarvis.settings.records_apps import apps_tools_for
bad = [
    "turn off " + "the " * 5000 + "x",
    "remove " + "a" * 20000,
    "add " + "x " * 9000 + "from my claude desktop",
    "only send me " + "about " * 3000,
    "stop sending me about " + "a " * 9000,
    "change " + "alerts " * 2500 + "slack",
    "disconnect " + "-" * 19000 + " app",
    "import " + "my " * 6000 + "codex servers",
    "stop sending " + " " * 18000 + "to slack",
]
t0 = time.perf_counter()
for text in bad:
    apps_tools_for.cache_clear()
    apps_tools_for(text)
print(time.perf_counter() - t0)
"""


def test_the_matchers_are_linear_on_pathological_input():
    out = subprocess.run(
        [sys.executable, "-c", _PATHOLOGICAL, str(ROOT / "src")],
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert float(out.stdout.strip().splitlines()[-1]) < 3.0
