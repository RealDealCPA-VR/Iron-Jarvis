"""v1.433.0 (Wave 1, A4): a credential never goes through the model.

A Discord destination's webhook URL IS its credential (anyone holding it can
post as the destination; routes/comm.HIDDEN_VALUES never hands it back to the
Notifications form), yet ``channel_connect`` took it as a plain tool argument:
the model wrote it, and the tool log kept it (no record tool redacted its
arguments). A Postgres connection string with its password went the same way
through ``app_connect``. Now:

* ``channel_connect`` has no ``webhook_url`` argument; one passed anyway is
  refused before any card, and its log row keeps no URL;
* a Discord destination is finished on the SECURE CARD, which writes the URL
  exactly where the Notifications page keeps it (``webhook_url`` in the
  destination's own settings), so the destination works the same;
* ``app_connect`` refuses a connection string WITH a password and shows the
  card for it instead (the Postgres half is pinned in
  tests/test_pack_secrets_in_args_v13510.py);
* both tools' ``redact_args`` mask any secret-shaped value.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.settings import records
from tests.test_chat_approvals_v1187 import _asgi_post, _drive_stream
from tests.test_settings_chat_tools_v1321 import _body, _calls_stream, _done

#: A real-looking Discord webhook: the id and the token in the path ARE the key.
URL = "https://discord.com/api/webhooks/123456789012345678/AbCdEfGh-NEVER-IN-CHAT_0123456789abcdef"
NEW_URL = "https://discord.com/api/webhooks/987654321098765432/ZyXwVu-ALSO-NEVER-IN-CHAT_fedcba9876543210"
SECRET_TAIL = "AbCdEfGh-NEVER-IN-CHAT"
PG = "postgresql://books:pgPassw0rd-NEVER-IN-CHAT@db.example:5432/ledger"


async def _approve_all(app, seen):
    async def approve(data):
        seen.append(data["tool"])
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "once"})
        assert status == 200

    return approve


def _ledger(app, tool: str) -> list[ToolInvocation]:
    with session_scope(app.state.platform.engine) as db:
        return list(db.exec(select(ToolInvocation).where(ToolInvocation.tool == tool)))


def _finished(frames, name: str) -> list[dict]:
    return [d for ev, d in frames if ev == "tool_call" and d.get("status") == "finished" and d.get("name") == name]


def test_channel_connect_offers_the_model_no_webhook_argument(tmp_path):
    app = create_app(str(tmp_path))
    tool = app.state.platform.registry.get("channel_connect")
    assert "webhook_url" not in tool.input_schema["properties"]
    assert "needs `webhook_url`" not in tool.description


@pytest.mark.asyncio
async def test_a_webhook_url_handed_to_channel_connect_is_refused_without_a_card_and_never_logged(tmp_path):
    app = create_app(str(tmp_path))
    p = app.state.platform
    p.router.stream = _calls_stream([("channel_connect", {"name": "team", "type": "discord", "webhook_url": URL})])
    seen: list[str] = []
    frames = await _drive_stream(app, _body("connect my discord channel"), await _approve_all(app, seen))
    assert seen == [], "a call that changes nothing is no approval card"
    (done,) = _finished(frames, "channel_connect")
    assert done["ok"] is False and "secure card" in done["output"]
    assert "team" not in ((p.config.comm or {}).get("channels") or {}), "nothing was saved"
    rows = _ledger(app, "channel_connect")
    assert rows, "the refused call is still in the ledger"
    assert all(SECRET_TAIL not in (r.args_json or "") for r in rows), [r.args_json for r in rows]
    assert SECRET_TAIL not in json.dumps(frames, default=str), "the stream never shows it either"


@pytest.mark.asyncio
async def test_a_discord_destination_is_finished_on_the_secure_card_where_the_notifications_page_keeps_it(tmp_path):
    app = create_app(str(tmp_path))
    p = app.state.platform
    p.router.stream = _calls_stream([("channel_connect", {"name": "team", "type": "discord"})])
    seen: list[str] = []
    frames = await _drive_stream(app, _body("connect my discord channel"), await _approve_all(app, seen))
    assert seen == ["channel_connect"]
    secret = next(c for c in _done(frames)["config_cards"] if c["kind"] == "secret")
    assert secret["name"] == "channel.team"
    assert "webhook URL" in secret["label"]
    assert "webhook_url" not in p.config.comm["channels"]["team"], "the URL is not there until the card"

    status, raw = await _asgi_post(app, "/config/secret", {"name": "channel.team", "value": URL})
    assert status == 200, raw
    assert SECRET_TAIL.encode() not in raw
    # Exactly where the Notifications page keeps it: the destination's own
    # settings (comm.channels.DiscordChannel reads `webhook_url`), not a vault row.
    assert p.config.comm["channels"]["team"] == {"type": "discord", "webhook_url": URL}
    assert p.secrets.get("channel_team_token") is None
    live = p.notifier.get("team")
    assert live is not None and live.config.get("webhook_url") == URL
    # The Notifications page sees it saved, and never shows its value.
    row = next(r for r in TestClient(app).get("/comm/channels").json()["channels"] if r["name"] == "team")
    assert "webhook_url" in row["saved_fields"] and "webhook_url" not in (row.get("values") or {})
    # The ledger row names the credential, never the URL.
    with session_scope(p.engine) as db:
        rows = list(db.exec(select(ToolInvocation).where(ToolInvocation.tool == "set_secret")))
    assert rows and all(SECRET_TAIL not in (r.args_json or "") + (r.output or "") for r in rows)


def test_replacing_a_discord_webhook_on_the_card_is_undone_to_the_old_one(tmp_path):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    p = app.state.platform
    records.apply_channel(p, "team", {"type": "discord", "webhook_url": URL})
    res = client.post("/config/secret", json={"name": "channel.team", "value": NEW_URL})
    assert res.status_code == 200 and res.json()["status"] == "replaced"
    assert p.config.comm["channels"]["team"]["webhook_url"] == NEW_URL
    assert client.post(f"/undo/{res.json()['action_id']}").status_code == 200
    assert p.config.comm["channels"]["team"]["webhook_url"] == URL
    assert p.notifier.get("team").config.get("webhook_url") == URL
    # A card store for a destination that had none: Undo removes it.
    records.apply_channel(p, "fresh", {"type": "discord"})
    res = client.post("/config/secret", json={"name": "channel.fresh", "value": URL})
    assert res.status_code == 200 and res.json()["status"] == "stored"
    assert client.post(f"/undo/{res.json()['action_id']}").status_code == 200
    assert "webhook_url" not in p.config.comm["channels"]["fresh"]


def test_the_card_refuses_what_is_not_a_webhook_address(tmp_path):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    records.apply_channel(app.state.platform, "team", {"type": "discord"})
    res = client.post("/config/secret", json={"name": "channel.team", "value": "not a link"})
    assert res.status_code == 400 and "https://" in res.json()["detail"]
    assert "webhook_url" not in app.state.platform.config.comm["channels"]["team"]


def test_a_telegram_token_still_goes_to_the_vault(tmp_path):
    """The Discord branch is Discord's alone: a Telegram destination's token
    is the vault row it always was."""
    app = create_app(str(tmp_path))
    client = TestClient(app)
    p = app.state.platform
    records.apply_channel(p, "phone", {"type": "telegram", "chat_id": "42", "token_secret": "channel_phone_token"})
    assert client.post("/config/secret", json={"name": "channel.phone", "value": "123:ABC-token"}).status_code == 200
    assert p.secrets.get("channel_phone_token") == "123:ABC-token"
    assert "webhook_url" not in p.config.comm["channels"]["phone"]


def test_both_tools_mask_any_secret_shaped_value_in_their_log_row(tmp_path):
    app = create_app(str(tmp_path))
    reg = app.state.platform.registry
    ch = reg.get("channel_connect").redact_args(
        {"name": "team", "type": "discord", "webhook_url": URL, "channel": URL, "token": "123:ABC"}
    )
    assert SECRET_TAIL not in json.dumps(ch)
    assert ch["name"] == "team" and ch["type"] == "discord" and ch["token"] == "***"
    ap = reg.get("app_connect").redact_args(
        {"app": "postgres", "values": {"connection": PG}}
    )
    # The host the user approves stays readable; the password does not.
    assert ap["values"]["connection"] == "postgresql://books:***@db.example:5432/ledger"
    nt = reg.get("app_connect").redact_args({"app": "notion", "values": {"NOTION_TOKEN": "ntn_abc123secret"}})
    assert nt == {"app": "notion", "values": {"NOTION_TOKEN": "***"}}
    other = reg.get("app_connect").redact_args({"app": "slack", "values": {"SLACK_TEAM_ID": "T0123", "API_KEY": "zzz"}})
    assert other["values"] == {"SLACK_TEAM_ID": "T0123", "API_KEY": "***"}


@pytest.mark.parametrize(
    ("shown", "vaulted", "launched_before"),
    [
        # Connected from the Directory with a password: its own vault row.
        ("postgresql://books:***@old-host:5432/ledger", "postgresql://books:oldPW-v14330@old-host:5432/ledger",
         "postgresql://books:oldPW-v14330@old-host:5432/ledger"),
        # Connected with no password at all: a plain argument.
        ("postgresql://old-host:5432/ledger", None, "postgresql://old-host:5432/ledger"),
    ],
)
def test_the_card_replaces_an_older_rows_connection_and_its_undo_brings_that_one_back(
    tmp_path, monkeypatch, shown, vaulted, launched_before
):
    """A Postgres saved before this change (or from the Directory) names its
    connection in ANOTHER place than the card's row: the card's save points the
    row at what was pasted, and its Undo launches what the row launched before."""
    import sys

    from iron_jarvis.mcp.tools import _build_transport

    monkeypatch.setattr(records, "load_app", lambda p, cfg: 0)  # never an npx launch
    app = create_app(str(tmp_path))
    client = TestClient(app)
    p = app.state.platform
    row = {"name": "postgres", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-postgres", shown]}
    if vaulted is not None:
        p.secrets.set("mcp.postgres.arg.2", vaulted)
        row["arg_secrets"] = {"2": "mcp.postgres.arg.2"}
    p.config.mcp_servers = [row]

    def launched() -> str:
        entry = records.snap_app(p, "postgres")
        return _build_transport(dict(entry, command=sys.executable), p.secrets.get).args[-1]

    assert launched() == launched_before
    res = client.post("/config/secret", json={"name": "app.postgres", "value": PG})
    assert res.status_code == 200 and res.json()["status"] == "replaced"
    assert launched() == PG
    assert records.snap_app(p, "postgres")["arg_secrets"] == {"2": "mcp.postgres.connection"}
    assert client.post(f"/undo/{res.json()['action_id']}").status_code == 200
    assert launched() == launched_before


@pytest.mark.asyncio
async def test_a_connection_string_with_its_password_is_refused_without_a_card_and_never_logged(tmp_path, monkeypatch):
    monkeypatch.setattr(records, "load_app", lambda p, cfg: 0)  # never an npx launch
    app = create_app(str(tmp_path))
    p = app.state.platform
    p.router.stream = _calls_stream([("app_connect", {"app": "postgres", "values": {"connection": PG}})])
    seen: list[str] = []
    frames = await _drive_stream(app, _body("connect my Postgres"), await _approve_all(app, seen))
    assert seen == [], "a refused call is no approval card"
    (done,) = _finished(frames, "app_connect")
    assert done["ok"] is False and "secure card" in done["output"]
    assert records.snap_app(p, "postgres") is None
    rows = _ledger(app, "app_connect")
    assert rows and all("pgPassw0rd" not in (r.args_json or "") for r in rows), [r.args_json for r in rows]
    assert "pgPassw0rd" not in json.dumps(frames, default=str)
