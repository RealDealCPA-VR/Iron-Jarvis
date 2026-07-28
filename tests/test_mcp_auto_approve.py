"""Auto-approve is editable after connecting (v1.103.0).

REPORTED: "in the tools module when I select 'let agent use this without asking'
there is no save option, and when I navigate away and come back the box is
unchecked."

Both observations were right, for two different reasons:

1. That checkbox sits above the pack catalogue and looks like a setting, but it
   is a FORM FIELD for the next connect (``POST /mcp/servers`` takes
   ``auto_approve``). Nothing to save, nothing to persist — plain useState, so
   navigation resets it.
2. There was no way to change auto-approve on a pack you had ALREADY connected.
   It could only be chosen at connect time; changing your mind meant deleting
   the pack and adding it again.

The stored flag is also COARSER than the old UI copy admitted: ``mcp_call`` is a
single permission key, so ``auto_approve`` on any one pack lets autonomous
agents run tools from EVERY connected pack (see platform.py's resolver).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app


@pytest.fixture
def client(tmp_path) -> TestClient:
    c = TestClient(create_app(str(tmp_path)))
    c.post("/mcp/servers", json={"name": "brave", "command": "npx", "args": ["-y", "b"]})
    return c


def _flag(client: TestClient, name: str = "brave") -> bool:
    row = next(s for s in client.get("/mcp/servers").json()["servers"] if s["name"] == name)
    return bool(row.get("auto_approve"))


def test_a_connected_pack_defaults_to_asking(client):
    assert _flag(client) is False


def test_auto_approve_can_be_turned_on_after_connecting(client):
    r = client.patch("/mcp/servers/brave", json={"auto_approve": True})
    assert r.status_code == 200
    assert r.json()["auto_approve"] is True
    assert _flag(client) is True


def test_it_can_be_turned_back_off(client):
    """Granting unattended tool use must not be a one-way door."""
    client.patch("/mcp/servers/brave", json={"auto_approve": True})
    assert client.patch("/mcp/servers/brave", json={"auto_approve": False}).status_code == 200
    assert _flag(client) is False


def test_off_removes_the_key_rather_than_storing_false(client):
    """POST omits the key when off, so PATCH must too — otherwise the same
    state is represented two ways in config.toml."""
    client.patch("/mcp/servers/brave", json={"auto_approve": True})
    client.patch("/mcp/servers/brave", json={"auto_approve": False})
    servers = client.app.state.platform.config.mcp_servers  # type: ignore[attr-defined]
    row = next(s for s in servers if s["name"] == "brave")
    assert "auto_approve" not in row


def test_it_survives_a_reload(client, tmp_path):
    """The reported symptom was "it doesn't stick" — so prove persistence, not
    just that the in-memory object changed."""
    client.patch("/mcp/servers/brave", json={"auto_approve": True})
    fresh = TestClient(create_app(str(tmp_path)))
    assert _flag(fresh) is True


def test_an_omitted_field_changes_nothing(client):
    """`None` means "leave alone" — a UI that only flips one field must not
    blank the rest of the record."""
    client.patch("/mcp/servers/brave", json={"auto_approve": True})
    r = client.patch("/mcp/servers/brave", json={})
    assert r.status_code == 200
    assert r.json()["auto_approve"] is True
    assert _flag(client) is True


def test_unknown_pack_is_404(client):
    assert client.patch("/mcp/servers/nope", json={"auto_approve": True}).status_code == 404


def test_the_response_admits_it_needs_a_restart(client):
    """The ask-resolver is built once at boot, so flipping this is NOT live.
    Saying "done" would be a small lie the user discovers the hard way."""
    note = client.patch("/mcp/servers/brave", json={"auto_approve": True}).json()["note"]
    assert "restart" in note.lower()


# --- the honesty of the UI copy ---------------------------------------------


def test_one_pack_trusting_grants_every_pack(client):
    """Pin the behaviour the old copy understated. platform.py builds ONE
    resolver from `any(...)` over all servers, because mcp_call is a single
    permission key — so this is genuinely all-or-nothing."""
    import inspect

    from iron_jarvis import platform as plat

    src = inspect.getsource(plat)
    assert 'if name == "mcp_call"' in src
    assert "any(" in src and "auto_approve" in src


def test_the_ui_says_it_affects_every_connected_pack():
    """The old copy said it "trusts every tool this pack exposes", which reads
    as per-pack. It is not — and a user granting unattended tool execution
    deserves to know the real blast radius."""
    from pathlib import Path

    page = Path(__file__).resolve().parents[1] / "dashboard" / "app" / "tools" / "page.tsx"
    text = page.read_text(encoding="utf-8")
    # v1.113.0 vocabulary pass: the UI says "plug-in" (one name per concept);
    # the wire (/mcp/servers) and this test's behaviour pins are unchanged.
    assert "every" in text and "connected plug-in" in text
    assert "auto-approve on" in text and "auto-approve off" in text  # it's a toggle
