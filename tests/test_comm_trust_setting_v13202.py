"""v1.320.2 — "Runs started from inbound messages" really saves.

The Settings page has rendered and PUT ``comm_trust`` since v1.298.0, but the
key was missing from the daemon's ``_SETTINGS_KEYS`` whitelist, so ``PUT
/settings`` silently dropped it: the select said "Full" and every inbound run
stayed low trust (found by the calm-UI redesign audit, AUDIT §9 Q14).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.comm.inbound import InboundPoller
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.schemas import _SETTINGS_KEYS


def _client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


def test_comm_trust_is_whitelisted_and_readable(tmp_path):
    assert "comm_trust" in _SETTINGS_KEYS
    assert _client(tmp_path).get("/settings").json()["settings"]["comm_trust"] == "low"


def test_a_save_changes_it_and_survives_a_restart(tmp_path):
    client = _client(tmp_path)
    r = client.put("/settings", json={"values": {"comm_trust": "full"}})
    assert r.status_code == 200, r.text
    assert client.app.state.platform.config.comm_trust == "full"
    # A fresh daemon on the same home reads it back from config.toml.
    assert _client(tmp_path).get("/settings").json()["settings"]["comm_trust"] == "full"


def test_a_bad_value_is_refused_and_changes_nothing(tmp_path):
    client = _client(tmp_path)
    r = client.put("/settings", json={"values": {"comm_trust": "everything"}})
    assert r.status_code == 400
    assert client.app.state.platform.config.comm_trust == "low"


def test_the_inbound_door_follows_the_saved_value(tmp_path):
    """Anti-vacuity: the saved value is what the door reads, live."""
    client = _client(tmp_path)
    poller = InboundPoller.__new__(InboundPoller)
    poller.platform = client.app.state.platform
    assert poller._comm_trust("telegram")[0] == "low"
    client.put("/settings", json={"values": {"comm_trust": "full"}})
    assert poller._comm_trust("telegram") == (None, "")
