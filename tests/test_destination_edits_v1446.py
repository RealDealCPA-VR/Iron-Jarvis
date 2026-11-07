"""v1.446.0 — editing a notification destination only changes what you changed
(ease-of-use review item 13).

Before: Edit re-submits ``POST /comm/channels``, and the save rebuilt the
destination from the form alone. So (1) a destination whose alert list had been
narrowed (approval asks turned off, say) came back set to everything, because
the form sent no ``events``; (2) an email destination chat had made two-way
(``channel_connect`` + ``channel_toggle``: ``imap_host``, ``imap_port``,
``inbound_enabled``, ``allowed_senders``) lost every one of those keys, so
two-way email was silently switched off; (3) the page had no boxes for them at
all, although chat sends people there for "Allowed senders"; (4) Add with a
name already in use quietly replaced the old destination, and a bad name was
refused as "invalid channel name".

Pinned here: an edit that sends no ``events`` keeps the saved list and ``[]``
still means everything; every stored key the form does not send is carried
over unless cleared (the last test result is not); the email field list has
the two-way keys EmailChannel reads; two-way email with no reading server is
refused; turning listening off ends a carried chat switch; a reading server
that is not a server name is refused without echoing it; Add's flag refuses a
name in use (a built-in one too) and is never stored, while a call without the
flag keeps replacing; the name rule is said in plain words; the row says why
two-way email cannot hear anyone.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes.comm import NAME_IN_USE, NEW_FLAG, two_way_note

HOOK = "https://discord.com/api/webhooks/1/example"


def _client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


def _save(client, config: dict, name: str = "phone", ctype: str = "discord"):
    return client.post("/comm/channels", json={"name": name, "type": ctype, "config": config})


def _stored(client, name: str = "phone") -> dict:
    return dict((client.app.state.platform.config.comm or {})["channels"][name])


def _plant(client, name: str, cfg: dict) -> None:
    """Store a destination the way chat's tools do (straight into settings)."""
    p = client.app.state.platform
    comm = dict(p.config.comm or {})
    channels = dict(comm.get("channels") or {})
    channels[name] = dict(cfg)
    comm["channels"] = channels
    p.config.comm = comm


def _chat_made_email(client) -> None:
    client.app.state.platform.secrets.set("channel_inbox_password", "pw-for-tests", kind="token")
    _plant(
        client,
        "inbox",
        {
            "type": "email",
            "host": "smtp.example.com",
            "port": "587",
            "username": "me@example.com",
            "password_secret": "channel_inbox_password",
            "from_addr": "me@example.com",
            "to_addr": "me@example.com",
            "imap_host": "imap.example.com",
            "imap_port": 993,
            "mailbox": "Clients",
            "inbound_enabled": True,
            "chat_enabled": True,
            "allowed_senders": ["boss@example.com"],
            "last_test_ok": True,
            "last_test_at": "2026-11-06T10:00:00Z",
        },
    )


# What the email form sent before v1.446.0: the six sending fields only.
OLD_EMAIL_FORM = {
    "host": "smtp.example.com",
    "port": "587",
    "username": "me@example.com",
    "password": "",
    "from_addr": "me@example.com",
    "to_addr": "me@example.com",
}


# --- 1. the alert choice --------------------------------------------------------


def test_an_edit_with_no_events_keeps_the_saved_alert_choice(tmp_path):
    c = _client(tmp_path)
    narrowed = ["review.requested", "session.completed"]
    assert _save(c, {"webhook_url": HOOK, "events": narrowed}).status_code == 200
    # The edit (changing nothing about alerts) sends no `events`.
    r = _save(c, {"webhook_url": ""})
    assert r.status_code == 200, r.text
    assert _stored(c)["events"] == narrowed
    assert "approval.requested" not in _stored(c)["events"]


def test_an_explicit_empty_list_still_means_everything(tmp_path):
    c = _client(tmp_path)
    assert _save(c, {"webhook_url": HOOK, "events": ["review.requested"]}).status_code == 200
    assert _save(c, {"webhook_url": "", "events": []}).status_code == 200
    assert "events" not in _stored(c)


def test_a_new_destination_with_no_events_gets_everything(tmp_path):
    c = _client(tmp_path)
    assert _save(c, {"webhook_url": HOOK, NEW_FLAG: "true"}).status_code == 200
    assert "events" not in _stored(c)


# --- 2. keys the form does not send ---------------------------------------------


def test_an_edit_keeps_every_stored_key_the_form_did_not_send(tmp_path):
    c = _client(tmp_path)
    _chat_made_email(c)
    r = _save(c, dict(OLD_EMAIL_FORM), name="inbox", ctype="email")
    assert r.status_code == 200, r.text
    got = _stored(c, "inbox")
    assert got["imap_host"] == "imap.example.com"
    assert got["imap_port"] == 993
    assert got["mailbox"] == "Clients"
    assert got["inbound_enabled"] is True
    assert got["chat_enabled"] is True
    assert got["allowed_senders"] == ["boss@example.com"]
    assert got["password_secret"] == "channel_inbox_password"
    # The settings changed, so the old test verdict is not carried.
    assert "last_test_ok" not in got and "last_test_at" not in got
    live = c.app.state.platform.notifier.get("inbox")
    assert live.inbound_enabled() and live.is_authorized("BOSS@example.com")


def test_a_cleared_key_stays_cleared(tmp_path):
    c = _client(tmp_path)
    _chat_made_email(c)
    cfg = dict(OLD_EMAIL_FORM, inbound_enabled="false", clear=["mailbox", "allowed_senders"])
    r = _save(c, cfg, name="inbox", ctype="email")
    assert r.status_code == 200, r.text
    got = _stored(c, "inbox")
    assert "mailbox" not in got and "allowed_senders" not in got
    assert got["imap_host"] == "imap.example.com"


def test_turning_listening_off_ends_a_carried_chat_switch(tmp_path):
    c = _client(tmp_path)
    _chat_made_email(c)
    r = _save(c, dict(OLD_EMAIL_FORM, inbound_enabled="false"), name="inbox", ctype="email")
    assert r.status_code == 200, r.text
    got = _stored(c, "inbox")
    assert got["inbound_enabled"] is False
    assert got["chat_enabled"] is False


def test_another_type_carries_nothing(tmp_path):
    c = _client(tmp_path)
    _chat_made_email(c)
    r = _save(c, {"webhook_url": HOOK}, name="inbox", ctype="discord")
    assert r.status_code == 200, r.text
    assert set(_stored(c, "inbox")) == {"type", "webhook_url"}


# --- 3. the email two-way boxes -------------------------------------------------


def test_the_email_form_has_the_two_way_keys_the_channel_reads(tmp_path):
    c = _client(tmp_path)
    types = {t["type"]: t for t in c.get("/comm/channel-types").json()["types"]}
    keys = [f["key"] for f in types["email"]["fields"]]
    assert keys[:6] == ["host", "port", "username", "password", "from_addr", "to_addr"]
    assert {"imap_host", "imap_port", "inbound_enabled", "allowed_senders"} <= set(keys)


def test_two_way_email_from_the_form_saves_and_listens(tmp_path):
    c = _client(tmp_path)
    cfg = dict(
        OLD_EMAIL_FORM,
        password="pw-for-tests",
        imap_host="imap.example.com",
        imap_port="993",
        inbound_enabled="true",
        allowed_senders="boss@example.com, Client@Example.com",
    )
    cfg[NEW_FLAG] = "true"
    r = _save(c, cfg, name="inbox", ctype="email")
    assert r.status_code == 200, r.text
    got = _stored(c, "inbox")
    assert got["allowed_senders"] == ["boss@example.com", "Client@Example.com"]
    assert got["imap_host"] == "imap.example.com" and got["inbound_enabled"] is True
    assert NEW_FLAG not in got
    row = next(x for x in c.get("/comm/channels").json()["channels"] if x["name"] == "inbox")
    assert row["inbound_enabled"] is True and row["allowed_senders_count"] == 2
    assert "imap_host" in row["saved_fields"] and row["values"]["imap_host"] == "imap.example.com"
    assert "allowed_senders" not in row["values"]  # the ids never leave the daemon


def test_two_way_email_with_no_reading_server_is_refused(tmp_path):
    c = _client(tmp_path)
    cfg = dict(OLD_EMAIL_FORM, password="pw-for-tests", inbound_enabled="true",
               allowed_senders="boss@example.com")
    r = _save(c, cfg, name="inbox", ctype="email")
    assert r.status_code == 400
    assert "needs an IMAP host" in r.json()["detail"]


def test_a_reading_server_that_is_not_a_name_is_refused_unechoed(tmp_path):
    c = _client(tmp_path)
    cfg = dict(OLD_EMAIL_FORM, password="pw-for-tests", imap_host="me:hunter@imap.example.com")
    r = _save(c, cfg, name="inbox", ctype="email")
    assert r.status_code == 400
    assert "server name" in r.json()["detail"]
    assert "hunter" not in r.text
    bad_port = dict(OLD_EMAIL_FORM, password="pw-for-tests", imap_host="imap.example.com",
                    imap_port="nine")
    r = _save(c, bad_port, name="inbox", ctype="email")
    assert r.status_code == 400 and "number" in r.json()["detail"]


def test_two_way_email_with_no_allowed_senders_says_why():
    note = two_way_note("email", {"inbound_enabled": True})
    assert note and "allowed senders" in note
    assert two_way_note("email", {"inbound_enabled": True, "allowed_senders": ["a@example.com"]}) is None
    assert two_way_note("email", {}) is None


# --- 4. names --------------------------------------------------------------------


def test_add_refuses_a_name_already_in_use_in_plain_words(tmp_path):
    c = _client(tmp_path)
    assert _save(c, {"webhook_url": HOOK, NEW_FLAG: "true"}).status_code == 200
    r = _save(c, {"webhook_url": "https://discord.com/api/webhooks/2/other", NEW_FLAG: "true"})
    assert r.status_code == 409
    assert r.json()["detail"] == NAME_IN_USE.format(name="phone")
    assert "press Edit on it" in r.json()["detail"]
    # The old destination is untouched.
    assert _stored(c)["webhook_url"] == HOOK


def test_add_refuses_a_built_in_name(tmp_path):
    c = _client(tmp_path)
    builtin = next(n for n in c.app.state.platform.notifier.channels()
                   if n not in ((c.app.state.platform.config.comm or {}).get("channels") or {}))
    r = _save(c, {"webhook_url": HOOK, NEW_FLAG: "true"}, name=builtin)
    assert r.status_code == 409 and "built-in" in r.json()["detail"]


def test_a_save_without_the_flag_still_replaces(tmp_path):
    """Edit, chat's tools and older API callers send no flag: unchanged."""
    c = _client(tmp_path)
    assert _save(c, {"webhook_url": HOOK}).status_code == 200
    other = "https://discord.com/api/webhooks/2/other"
    assert _save(c, {"webhook_url": other}).status_code == 200
    assert _stored(c)["webhook_url"] == other


def test_a_bad_name_answer_says_the_rule(tmp_path):
    c = _client(tmp_path)
    r = _save(c, {"webhook_url": HOOK}, name="my phone")
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "invalid channel name" not in detail
    assert "Use letters, numbers, - and _, starting with a letter" in detail
