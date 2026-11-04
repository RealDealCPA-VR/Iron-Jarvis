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

v1.428.0 (moved on purpose): Microsoft 365 is LOCKED DOWN, and the chat's door
never sets it up at all — a model's arguments are never the account, the
firm's app or the account kind (``LOCKED_ELSEWHERE``). So "one row from every
door" is now "ONE door": the chat saves nothing and says where the card is;
the Directory's Connect is the only row, and its Sign in runs that saved
pack's own program + ``--login`` with its settings. A switched-off app still
reads off and chat still never switches it back on.

Reuses lane 6D's harness (the stand-in server listing the real 119 tool
names, the recording terminal backend) from ``tests/test_microsoft_365_v13560``.
"""

from __future__ import annotations

import asyncio

from iron_jarvis.connectors import catalog, service
from iron_jarvis.connectors.app_names import APPS_PLACE
from iron_jarvis.daemon.routes.iron_proxy import shell_line
from iron_jarvis.mcp import pack_door
from iron_jarvis.settings import records
from tests import test_microsoft_365_v13560 as ms
from tests.test_microsoft_365_v13560 import (
    CARD_ID,
    SIGNIN_JOB,
    _backend_of,
    _connect,
    _platform,
    _row,
    _saved,
    _values,
)

# Lane 6D's fixtures, bound here so pytest finds them in this module.
_fresh_records = ms._fresh_records
spawned = ms.spawned
client = ms.client


def _chat_connect(c, values: dict):
    tool = records.AppConnectTool(_platform(c))
    return asyncio.run(tool.execute({"app": CARD_ID, "values": values}, None))


def test_connecting_from_chat_saves_nothing_and_the_cards_sign_in_runs_the_packs_flags(
    client, monkeypatch
):
    """v1.428.0 (moved on purpose; was "saves the Directory's row"): the
    chat's door never writes the locked row, even with every right answer —
    it goes through no pack door, says where the card is, and changes
    nothing. The Directory's Connect is the one row, and its Sign in opens a
    pane running that saved pack's own program + --login with its settings."""
    calls: list[str] = []
    real_install = pack_door.install_config

    def _spy(platform, cfg, **kw):
        calls.append(str(cfg.get("name")))
        return real_install(platform, cfg, **kw)

    monkeypatch.setattr(pack_door, "install_config", _spy)
    out = _chat_connect(client, _values(catalog.MS365_WORK))
    assert out.ok is False
    assert (out.data or {}) == {"app": CARD_ID, "locked": True}
    assert "set up only from its card" in (out.error or "") and APPS_PLACE in (out.error or "")
    assert calls == [], "the chat's door never saves the locked pack"
    assert _saved(client) is None

    # The one door: the Directory card.
    assert _connect(client, catalog.MS365_WORK).status_code == 200
    assert calls == [CARD_ID]
    cfg = _saved(client)
    signin = _row(client)["signin"]
    assert signin == {
        "cli": SIGNIN_JOB,
        "label": "Sign in to Microsoft",
        "account": "Work or school account",
    }

    # The press: a Build pane running the saved pack's own program + --login,
    # with the pack's settings — the same flags, never another program.
    monkeypatch.delenv("IRONJARVIS_PANE_CLI", raising=False)
    r = client.post("/terminals/launch", json={"cli": signin["cli"]})
    assert r.status_code == 200, r.text
    session = _platform(client).terminals.get(r.json()["id"])
    shell = _backend_of(r.json()["id"])
    assert shell.written == [shell_line(session.shell, [cfg["command"], *cfg["args"], "--login"]) + "\r"]
    for k, v in cfg["env"].items():
        assert shell.env.get(k) == v, k


def test_an_answer_off_the_list_is_refused_from_chat_and_nothing_is_saved(client):
    """v1.428.0 (moved on purpose): from chat EVERY answer is refused (the
    card is the only door) — an answer off the list included — and nothing
    is saved."""
    for answer in ("work", catalog.MS365_WORK, catalog.MS365_PERSONAL):
        out = _chat_connect(client, _values(answer))
        assert out.ok is False, answer
        assert "set up only from its card" in (out.error or ""), answer
    assert _saved(client) is None
    assert _row(client)["status"] == "disconnected"
    # The generic door refuses the card the same way.
    try:
        service.pack_body(catalog.get_connector(CARD_ID), _values(catalog.MS365_WORK))
    except ValueError as exc:
        assert "set up only from its card" in str(exc)
    else:  # pragma: no cover - the contract
        raise AssertionError("pack_body built a locked card")


def test_a_switched_off_microsoft_365_reads_off_and_chat_does_not_reconnect_it(client):
    """v1.428.0 (moved on purpose): connected from its card (the chat can no
    longer connect it); the switch still works and chat still says "switched
    off" without turning it back on."""
    assert _connect(client, catalog.MS365_PERSONAL).status_code == 200
    assert client.patch(f"/mcp/servers/{CARD_ID}", json={"enabled": False}).status_code == 200
    row = _row(client)
    assert row["status"] == "off" and row["enabled"] is False
    out = _chat_connect(client, _values(catalog.MS365_PERSONAL))
    assert out.ok is False and "switched off" in (out.error or "")
    assert _saved(client).get("enabled") is False, "chat never switches it back on behind the user"
