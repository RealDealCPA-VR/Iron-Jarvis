"""The Tools-page auto-approve checkbox is a real persisted setting (v1.127.0).

REPORTED (for the second time): "in the tools module there is a checkbox that
states let the agents use this without asking. When I check the box there is no
way of saving this option and when I leave this module and return the box
remains unchecked."

v1.103.0 answered the first report by adding the per-plug-in PATCH and rewriting
the copy to say the checkbox only applies to the NEXT connect — but it stayed a
plain useState form field, and the user hit the exact same wall again. A control
that looks like a setting must BE a setting:

- ``PATCH /mcp/settings {auto_approve}`` persists a GLOBAL flag in config.
- ``GET /mcp/servers`` reports ``auto_approve_global`` and — what the checkbox
  binds to — ``auto_approve_effective`` (global OR any per-server flag), so the
  box can never show "off" while agents are actually trusted.
- Unchecking clears the per-server flags too: mcp_call is ONE shared permission
  key, so any surviving flag would keep the blanket grant alive behind an
  unchecked box.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app


@pytest.fixture
def client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


def _state(client: TestClient) -> dict:
    r = client.get("/mcp/servers").json()
    return {
        "global": r["auto_approve_global"],
        "effective": r["auto_approve_effective"],
    }


def test_fresh_install_defaults_to_asking(client):
    assert _state(client) == {"global": False, "effective": False}


def test_checking_the_box_saves(client):
    r = client.patch("/mcp/settings", json={"auto_approve": True})
    assert r.status_code == 200
    assert r.json()["auto_approve_global"] is True
    assert _state(client) == {"global": True, "effective": True}


def test_it_sticks_after_leaving_and_returning(client, tmp_path):
    """THE reported symptom. A fresh app over the same state dir must still
    show the box checked — prove config.toml persistence, not useState."""
    client.patch("/mcp/settings", json={"auto_approve": True})
    fresh = TestClient(create_app(str(tmp_path)))
    assert _state(fresh) == {"global": True, "effective": True}


def test_applies_to_plug_ins_connected_later(client):
    """The old form-field only stamped the NEXT connect. The global flag needs
    no stamp: a plug-in connected after checking the box is covered, and the
    dashboard no longer sends per-connect auto_approve at all."""
    client.patch("/mcp/settings", json={"auto_approve": True})
    client.post("/mcp/servers", json={"name": "brave", "command": "npx", "args": ["-y", "b"]})
    assert _state(client)["effective"] is True
    row = next(s for s in client.get("/mcp/servers").json()["servers"] if s["name"] == "brave")
    assert "auto_approve" not in row  # trust lives in the global flag, not the row


def test_a_per_plug_in_flag_shows_as_effective(client):
    """mcp_call is one permission key — if any plug-in row is trusted, agents
    can use EVERY plug-in, and the checkbox must admit it (bind to effective,
    not to the global flag alone)."""
    client.post("/mcp/servers", json={"name": "brave", "command": "npx", "args": ["-y", "b"]})
    client.patch("/mcp/servers/brave", json={"auto_approve": True})
    assert _state(client) == {"global": False, "effective": True}


def test_unchecking_clears_per_plug_in_flags_too(client, tmp_path):
    """An unchecked box must mean "agents ask" — full stop. Leaving an old
    per-row flag behind would keep the blanket grant alive invisibly."""
    client.post("/mcp/servers", json={"name": "brave", "command": "npx", "args": ["-y", "b"]})
    client.patch("/mcp/servers/brave", json={"auto_approve": True})
    client.patch("/mcp/settings", json={"auto_approve": True})

    r = client.patch("/mcp/settings", json={"auto_approve": False})
    assert r.status_code == 200
    assert _state(client) == {"global": False, "effective": False}
    row = next(s for s in client.get("/mcp/servers").json()["servers"] if s["name"] == "brave")
    assert "auto_approve" not in row  # absent == off (same shape as POST/PATCH)

    fresh = TestClient(create_app(str(tmp_path)))  # and the clearing persisted
    assert _state(fresh) == {"global": False, "effective": False}


def test_an_omitted_field_reads_without_changing(client):
    client.patch("/mcp/settings", json={"auto_approve": True})
    r = client.patch("/mcp/settings", json={})
    assert r.status_code == 200
    assert r.json()["auto_approve_global"] is True
    assert r.json()["note"] is None
    assert _state(client)["global"] is True


def test_the_response_admits_it_needs_a_restart(client):
    """The ask-resolver is built once at boot — same honesty rule as the
    per-plug-in PATCH."""
    note = client.patch("/mcp/settings", json={"auto_approve": True}).json()["note"]
    assert "restart" in note.lower()


def test_the_boot_resolver_honours_the_global_flag():
    """Persisting the checkbox is only half the job — the flag must actually
    grant. Pin platform.py's resolver to the global flag the same way the
    v1.103.0 test pinned it to the per-server any(...)."""
    import inspect

    from iron_jarvis import platform as plat

    src = inspect.getsource(plat)
    assert 'getattr(config, "mcp_auto_approve", False)' in src
    assert 'if name == "mcp_call"' in src


def test_the_ui_has_no_unsaved_form_field_left():
    """The regression this file exists to prevent: the control must bind to the
    polled server state and save through /mcp/settings — not a useState default
    that navigation silently resets.

    STILL TRUE, THROUGH A DIFFERENT CONTROL (v1.216.0). The checkbox became a
    `PermissionsPanel`, which takes the state as a PROP and calls back to save;
    it holds no copy of the answer at all, which is a stronger version of the
    same guarantee than "no local useState" was.

    THE ONE BEHAVIOUR CHANGE, and it is a fix rather than a regression: the
    page used to render `auto_approve_effective` (the daemon's `global OR
    any-server` roll-up) inside a control labelled as the GLOBAL switch, so a
    single extension with its own grant made the blanket switch look armed. The
    panel is handed `auto_approve_global` — the raw flag — and shows per-server
    grants as their own rows.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "dashboard"
    page = (root / "app" / "tools" / "page.tsx").read_text(encoding="utf-8")
    assert "/mcp/settings" in page
    # The GLOBAL switch is fed the global flag, not the roll-up.
    assert "globalOn={mcpData?.auto_approve_global ?? false}" in page
    # The roll-up is still read somewhere (the daemon serves it), but never as
    # the value of the global control.
    assert "auto_approve_effective" not in page.split("globalOn=")[1]
    panel = (root / "components" / "tools" / "PermissionsPanel.tsx").read_text(
        encoding="utf-8"
    )
    # No copy of the answer inside the control: it renders the prop and calls
    # back. (`useState` in there is the confirm dialog's open flag only.)
    assert "globalOn," in panel and "onSetGlobal" in panel
    assert "useState(globalOn" not in panel
