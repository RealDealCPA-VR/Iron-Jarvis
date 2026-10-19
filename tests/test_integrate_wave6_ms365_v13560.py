"""v1.356.0 integrate (G60): Microsoft 365 connected from CHAT is the same
pack the Directory's Connect saves, so its card offers the same Sign in.

Lane 6D made the Directory's door right (the card's fixed tool filter, the
account kind answered from a list); the chat's own door (``settings/records.
AppConnectTool``, armed by "connect my Outlook") still built the row by hand:
no ``ENABLED_TOOLS`` (the pack loaded every tool its server has and chat
armed the first 24 by name, the calendar view lost), and a typed "work" was
saved as a Microsoft authority, so the card's ``signin`` was None and the
sign-in would fail. Now the chat door writes exactly the Directory's row,
refuses an answer off the list, and the card's Sign in opens a Build pane
(``POST /terminals/launch``) running the pack's OWN command and arguments
plus ``--login`` with the same settings.

Reuses lane 6D's harness (the stand-in server listing the real 119 tool
names, the recording terminal backend) from ``tests/test_microsoft_365_v13560``.
"""

from __future__ import annotations

import asyncio

from iron_jarvis.connectors import catalog, service
from iron_jarvis.mcp import pack_door
from iron_jarvis.settings import records
from tests import test_microsoft_365_v13560 as ms
from tests.test_microsoft_365_v13560 import (
    CARD_ID,
    _backend_of,
    _card,
    _platform,
    _program_on_path,
    _row,
    _saved,
)

# Lane 6D's fixtures, bound here so pytest finds them in this module.
_fresh_records = ms._fresh_records
spawned = ms.spawned
client = ms.client


def _chat_connect(c, answer: str):
    tool = records.AppConnectTool(_platform(c))
    return asyncio.run(tool.execute({"app": CARD_ID, "values": {catalog.MS365_TENANT_ENV: answer}}, None))


def test_connecting_from_chat_saves_the_directorys_row_and_its_sign_in_runs_the_packs_flags(
    client, monkeypatch
):
    calls: list[str] = []
    real_install = pack_door.install_config

    def _spy(platform, cfg, **kw):
        calls.append(str(cfg.get("name")))
        return real_install(platform, cfg, **kw)

    monkeypatch.setattr(pack_door, "install_config", _spy)
    out = _chat_connect(client, catalog.MS365_WORK)
    assert out.ok is True, out.error
    assert calls == [CARD_ID], "the chat's connect goes through the one pack door"
    assert not (out.data or {}).get("secret_requests"), "nothing secret to type"

    cfg = _saved(client)
    card = _card()
    assert cfg["command"] == card.command and list(cfg["args"]) == list(card.args)
    # The SAME settings the Directory's Connect writes for that answer.
    directory = service.pack_body(card, {catalog.MS365_TENANT_ENV: catalog.MS365_WORK})
    assert cfg["env"] == dict(directory.env)
    assert cfg["env"] == catalog.signin_env(card, "work")
    assert cfg["env"]["ENABLED_TOOLS"] == catalog.MS365_ENABLED_TOOLS

    row = _row(client)
    assert row["tools_loaded"] == 24, "the card's filter: exactly what chat arms as one app"
    signin = row["signin"]
    assert signin == {
        "cli": "microsoft-365-signin-work",
        "label": "Sign in to Microsoft",
        "account": "Work or school account",
    }

    # The press: a Build pane running the pack's own program + --login, with
    # the pack's settings — the same flags, never another program.
    _program_on_path(monkeypatch)
    monkeypatch.delenv("IRONJARVIS_PANE_CLI", raising=False)
    r = client.post("/terminals/launch", json={"cli": signin["cli"]})
    assert r.status_code == 200, r.text
    shell = _backend_of(r.json()["id"])
    assert shell.written[0].rstrip("\r").split(" ") == [cfg["command"], *cfg["args"], "--login"]
    for k, v in cfg["env"].items():
        assert shell.env.get(k) == v, k


def test_an_answer_off_the_list_is_refused_from_chat_and_nothing_is_saved(client):
    out = _chat_connect(client, "work")  # the option's KEY, not the authority
    assert out.ok is False
    assert f"`{catalog.MS365_WORK}`" in (out.error or "") and f"`{catalog.MS365_PERSONAL}`" in (out.error or "")
    assert _saved(client) is None
    assert _row(client)["status"] == "disconnected"


def test_a_switched_off_microsoft_365_reads_off_and_chat_does_not_reconnect_it(client):
    assert _chat_connect(client, catalog.MS365_PERSONAL).ok is True
    assert client.patch(f"/mcp/servers/{CARD_ID}", json={"enabled": False}).status_code == 200
    row = _row(client)
    assert row["status"] == "off" and row["enabled"] is False
    out = _chat_connect(client, catalog.MS365_PERSONAL)
    assert out.ok is False and "switched off" in (out.error or "")
    assert _saved(client).get("enabled") is False, "chat never switches it back on behind the user"
