"""v1.355.0 re-review (Wave 5, G44): a RECONNECT through the one add door
drops the vault rows the old row named and the new one does not.

``mcp/pack_door.add_pack(replace=True)`` (the Directory's Connect over a saved
card) deletes "the old pack's leftovers (a renamed key, an old Directory
``conn_<id>_<field>`` name, a secret argument that is gone)". No pin covered
that sweep: deleting it left every wave-5 test green (re-review mutation B30).
The case it exists for is real — every pack the Directory saved before
v1.355.0 names its token ``conn_<id>_<field>``, and connecting it again now
stores the token as ``mcp.<id>.<FIELD>``, so without the sweep the old token
would outlive the row that named it (the v1.342.0 "no orphaned secret" rule).

Driven through the REAL app factory and the real ``/connectors`` route; only
the pack launch is stubbed (nothing is spawned), never the door under test.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.mcp import tools as mcp_tools_mod

OLD = "ntn_RR_OLD_0123456789NEVERSHOWN"
NEW = "ntn_RR_NEW_9876543210NEVERSHOWN"


def _entry(platform, name: str) -> dict | None:
    return next((s for s in platform.config.mcp_servers or [] if s.get("name") == name), None)


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(mcp_tools_mod, "mcp_tools", lambda *_a, **_k: [])  # nothing launched
    return TestClient(create_app(str(tmp_path)))


def test_reconnecting_an_older_pack_moves_its_token_and_drops_the_old_named_row(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    p = client.app.state.platform
    # A Notion pack the Directory saved before v1.355.0: its token under the
    # old name.
    p.config.mcp_servers = [
        {
            "name": "notion",
            "command": "ij-no-such-launcher-v13550",
            "args": [],
            "env_secrets": {"NOTION_TOKEN": "conn_notion_notion_token"},
        }
    ]
    p.secrets.set("conn_notion_notion_token", OLD)

    res = client.post("/connectors/notion/connect", json={"values": {"NOTION_TOKEN": NEW}})
    assert res.status_code == 200, res.text
    assert OLD not in res.text and NEW not in res.text

    # Anti-vacuity: the reconnect really happened through the one door — the
    # row names the door's vault row, which holds the NEW token.
    cfg = _entry(p, "notion")
    assert cfg is not None
    assert cfg["env_secrets"] == {"NOTION_TOKEN": "mcp.notion.NOTION_TOKEN"}
    assert p.secrets.get("mcp.notion.NOTION_TOKEN") == NEW
    # The row the old pack named is gone with it: no secret outlives the row
    # that named it.
    assert p.secrets.get("conn_notion_notion_token") is None, (
        "a reconnect left the older pack's token in the vault"
    )


def test_reconnecting_with_the_same_names_keeps_the_token_it_just_stored(tmp_path, monkeypatch):
    """Control: the sweep removes only rows the NEW row does not name — a
    plain reconnect (same vault names) never deletes the token it stored."""
    client = _client(tmp_path, monkeypatch)
    p = client.app.state.platform
    assert client.post("/connectors/notion/connect", json={"values": {"NOTION_TOKEN": OLD}}).status_code == 200
    assert client.post("/connectors/notion/connect", json={"values": {"NOTION_TOKEN": NEW}}).status_code == 200
    assert _entry(p, "notion")["env_secrets"] == {"NOTION_TOKEN": "mcp.notion.NOTION_TOKEN"}
    assert p.secrets.get("mcp.notion.NOTION_TOKEN") == NEW
