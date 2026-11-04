"""v1.427.6 — a destination's Edit keeps what is saved, and two-way says why
it cannot hear the user.

The user's report: "when i go back in to edit, i need to re-enter the tokens
because they are cleared when i go to edit" and "i was able to get a message
on slack from IJ, but i couldnt message it back". Edit re-submits
``POST /comm/channels``, which rebuilt the config from the form alone: the form
never holds a saved secret, so every blank box DROPPED its token — and the
allowlist, whose ids the form is never handed, went with it. With the allowlist
empty the Slack Socket Mode pump is never armed (``candidates()``), so a DM to
the bot reached nobody, and nothing on the page said so.

Pinned here: a blank field KEEPS (secrets by their vault reference, plain values
as stored), ``clear`` removes (and the vault row goes), a typed value replaces;
a refused edit never swaps the vault's token; another type carries nothing;
``GET /comm/channels`` names what is saved without handing back a secret, the
webhook or the allowlist's ids; ``two_way_note`` explains a destination that
cannot hear the user; the Slack manifest turns on the Messages tab; Slack has
the chat switch.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from iron_jarvis.comm.slack_socket import SlackSocketMode
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes.comm import two_way_note

BOT = "xoxb-bot-111"
APP = "xapp-app-222"


def _client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


def _save(client, config: dict, name: str = "hq", ctype: str = "slack"):
    return client.post("/comm/channels", json={"name": name, "type": ctype, "config": config})


def _two_way(client, **over):
    cfg = {
        "token": BOT,
        "app_token": APP,
        "channel": "#general",
        "inbound_enabled": "true",
        "allowed_senders": "U111",
    }
    cfg.update(over)
    r = _save(client, cfg)
    assert r.status_code == 200, r.text
    return r.json()


def _stored(client, name: str = "hq") -> dict:
    return dict((client.app.state.platform.config.comm or {})["channels"][name])


def _secret(client, ref: str):
    return client.app.state.platform.secrets.get(ref)


def _blank_edit(**over) -> dict:
    """What the Edit form sends when the user changes nothing: every field,
    secrets and hidden values blank, the switches as the row reported them."""
    cfg = {
        "webhook_url": "",
        "token": "",
        "channel": "#general",
        "signing_secret": "",
        "app_id": "",
        "client_id": "",
        "client_secret": "",
        "verification_token": "",
        "app_token": "",
        "inbound_enabled": "true",
        "chat_enabled": "false",
        "allowed_senders": "",
    }
    cfg.update(over)
    return cfg


def test_a_blank_edit_keeps_every_token_and_the_allowlist(tmp_path):
    client = _client(tmp_path)
    _two_way(client)
    r = _save(client, _blank_edit(channel="#ops"))
    assert r.status_code == 200, r.text
    cfg = _stored(client)
    assert cfg["channel"] == "#ops"  # a typed value replaces
    assert _secret(client, cfg["token_secret"]) == BOT
    assert _secret(client, cfg["app_token_secret"]) == APP
    assert cfg["allowed_senders"] == ["U111"]
    assert cfg["inbound_enabled"] is True


def test_after_a_blank_edit_the_slack_socket_still_has_its_channel(tmp_path):
    client = _client(tmp_path)
    _two_way(client)
    assert _save(client, _blank_edit()).status_code == 200
    platform = client.app.state.platform
    socket = SlackSocketMode(
        object(), platform.notifier, platform.secrets.get, lambda: platform.config.comm or {}
    )
    assert socket.candidates() == [("hq", APP)]


def test_clear_removes_a_saved_secret_and_its_vault_row(tmp_path):
    client = _client(tmp_path)
    _two_way(client)
    ref = _stored(client)["app_token_secret"]
    r = _save(client, {**_blank_edit(), "clear": ["app_token", "allowed_senders"]})
    assert r.status_code == 200, r.text
    cfg = _stored(client)
    assert "app_token_secret" not in cfg
    assert "allowed_senders" not in cfg
    assert _secret(client, ref) is None
    # The bot token was not named: it stays.
    assert _secret(client, cfg["token_secret"]) == BOT


def test_a_typed_secret_replaces_the_saved_one(tmp_path):
    client = _client(tmp_path)
    _two_way(client)
    assert _save(client, _blank_edit(token="xoxb-new-333")).status_code == 200
    assert _secret(client, _stored(client)["token_secret"]) == "xoxb-new-333"


def test_a_refused_edit_never_swaps_the_saved_token(tmp_path):
    client = _client(tmp_path)
    _two_way(client)
    before = _stored(client)
    # No webhook and the bot token removed: Slack has no way to deliver, so
    # the save is refused — and the app token typed with it must not land.
    r = _save(client, {**_blank_edit(app_token="xapp-typed-999"), "clear": ["token"]})
    assert r.status_code == 400
    assert "no way to deliver" in r.json()["detail"]
    assert _stored(client) == before
    assert _secret(client, before["app_token_secret"]) == APP
    assert _secret(client, before["token_secret"]) == BOT


def test_another_type_under_the_same_name_carries_nothing(tmp_path):
    client = _client(tmp_path)
    _two_way(client, webhook_url="https://hooks.slack.com/services/T/B/X")
    r = _save(client, {"webhook_url": ""}, ctype="discord")
    # Discord needs its webhook: the Slack row's webhook (a field both types
    # have) must not be borrowed by a blank box of another type.
    assert r.status_code == 400
    r = _save(client, {"webhook_url": "https://discord.test/hook"}, ctype="discord")
    assert r.status_code == 200, r.text
    cfg = _stored(client)
    assert cfg == {"type": "discord", "webhook_url": "https://discord.test/hook"}


def test_a_new_destination_starts_from_nothing(tmp_path):
    client = _client(tmp_path)
    r = _save(client, {"token": "", "channel": "#x"})
    assert r.status_code == 400  # nothing to keep: no token, no webhook


def test_the_list_names_what_is_saved_and_never_hands_back_a_secret(tmp_path):
    client = _client(tmp_path)
    _two_way(client, webhook_url="https://hooks.slack.com/services/T/B/SECRETPART")
    body = client.get("/comm/channels").json()
    row = next(c for c in body["channels"] if c["name"] == "hq")
    assert {"token", "app_token", "channel", "allowed_senders", "webhook_url"} <= set(
        row["saved_fields"]
    )
    assert row["values"] == {"channel": "#general"}
    raw = json.dumps(body)
    for leak in (BOT, APP, "SECRETPART", "U111"):
        assert leak not in raw
    assert not row["two_way_note"]


def test_two_way_with_nobody_allowed_says_so_on_the_save_and_the_row(tmp_path):
    client = _client(tmp_path)
    saved = _two_way(client, allowed_senders="")
    assert "no one is on the allowlist" in saved["two_way_note"]
    row = next(c for c in client.get("/comm/channels").json()["channels"] if c["name"] == "hq")
    assert "Copy member ID" in row["two_way_note"]


def test_two_way_note_names_each_missing_piece():
    full = {
        "inbound_enabled": True,
        "allowed_senders": ["U1"],
        "app_token_secret": "a",
        "token_secret": "t",
    }
    assert two_way_note("slack", full) is None
    assert two_way_note("slack", {**full, "inbound_enabled": False, "allowed_senders": []}) is None
    no_ear = {k: v for k, v in full.items() if k != "app_token_secret"}
    assert "App-level token" in two_way_note("slack", no_ear)
    # A signing secret is the other way to hear Slack (the events URL).
    assert two_way_note("slack", {**no_ear, "signing_secret_secret": "s"}) is None
    no_voice = {k: v for k, v in full.items() if k != "token_secret"}
    assert "Bot token" in two_way_note("slack", no_voice)
    assert "@userinfobot" in two_way_note(
        "telegram", {"inbound_enabled": True, "token_secret": "t", "allowed_senders": []}
    )
    assert two_way_note("discord", {"inbound_enabled": True}) is None


def test_the_manifest_turns_on_the_messages_tab_and_slack_can_chat(tmp_path):
    client = _client(tmp_path)
    types = client.get("/comm/channel-types").json()["types"]
    slack = next(t for t in types if t["type"] == "slack")
    home = json.loads(slack["manifest"])["features"]["app_home"]
    assert home["messages_tab_enabled"] is True
    assert home["messages_tab_read_only_enabled"] is False
    assert "chat_enabled" in {f["key"] for f in slack["fields"]}
    assert "Messages Tab" in slack["manifest_help"]


def test_slack_chat_turns_listening_on_and_a_blank_edit_keeps_it(tmp_path):
    client = _client(tmp_path)
    _two_way(client, inbound_enabled="false", chat_enabled="true")
    live = client.app.state.platform.notifier.get("hq")
    assert live.chat_enabled() and live.inbound_enabled()
    # The form left both switches blank (an older page): kept, not switched off.
    assert _save(client, _blank_edit(inbound_enabled="", chat_enabled="")).status_code == 200
    cfg = _stored(client)
    assert cfg["chat_enabled"] is True and cfg["inbound_enabled"] is True
