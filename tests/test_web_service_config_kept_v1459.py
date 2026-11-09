"""v1.459.0 — a web service's Configure save keeps how its key is sent.

The page's "Config (JSON)" box opened on "{}" and ``POST
/integrations/{id}/configure`` REPLACED the whole config, so editing the
address dropped ``auth_kind`` / ``auth_header`` / ``auth_user`` (and the key's
vault row name): the next call went out with the wrong way of carrying the
key, or none. Now the route keeps those sign-in keys when the save does not
name them (``null`` removes one), refuses a way the add door would refuse, and
``GET /integrations`` rows carry the saved config (masked) so the box opens on
it.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes.comm import merged_integration_config, shown_integration_config


def _add(client) -> str:
    r = client.post(
        "/integrations",
        json={"name": "Acme CRM", "base_url": "https://api.acme.test", "auth_token": "k-123",
              "auth_kind": "header", "auth_header": "X-API-Key"},
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _stored(app, iid: str) -> dict:
    return app.state.platform.integrations.stored_config(iid)


def test_a_save_that_changes_only_the_address_keeps_the_keys_way(tmp_path):
    app = create_app(str(tmp_path))
    with TestClient(app) as client:
        iid = _add(client)
        before = _stored(app, iid)
        assert before["auth_kind"] == "header" and before.get("auth_secret")
        r = client.post(f"/integrations/{iid}/configure", json={"config": {"base_url": "https://api2.acme.test"}})
        assert r.status_code == 200, r.text
        after = _stored(app, iid)
        assert after["base_url"] == "https://api2.acme.test"
        assert after["auth_kind"] == "header" and after["auth_header"] == "X-API-Key"
        assert after["auth_secret"] == before["auth_secret"]


def test_null_removes_a_kept_key_and_a_bad_way_is_refused_in_words(tmp_path):
    app = create_app(str(tmp_path))
    with TestClient(app) as client:
        iid = _add(client)
        r = client.post(f"/integrations/{iid}/configure", json={"config": {"auth_header": None}})
        assert r.status_code == 400 and "header" in r.json()["detail"].lower()
        assert _stored(app, iid)["auth_header"] == "X-API-Key"  # nothing changed
        r = client.post(
            f"/integrations/{iid}/configure",
            json={"config": {"base_url": "https://api.acme.test", "auth_kind": None, "auth_header": None}},
        )
        assert r.status_code == 200, r.text
        after = _stored(app, iid)
        assert "auth_kind" not in after and "auth_header" not in after and after.get("auth_secret")


def test_the_listing_carries_the_saved_config_masked(tmp_path):
    app = create_app(str(tmp_path))
    with TestClient(app) as client:
        iid = _add(client)
        rows = client.get("/integrations").json()["integrations"]
        row = next(r for r in rows if r["id"] == iid)
        assert row["config"]["base_url"] == "https://api.acme.test"
        assert row["config"]["auth_header"] == "X-API-Key"
        assert "k-123" not in str(rows)


def test_the_helpers():
    assert merged_integration_config({"auth_kind": "basic", "auth_user": "u", "base_url": "a"}, {"base_url": "b"}) == {
        "auth_kind": "basic", "auth_user": "u", "base_url": "b"}
    assert merged_integration_config({}, {"x": None}) == {}
    shown = shown_integration_config({"base_url": "https://bob:hunter2@api.acme.test", "n": 3, "d": {"a": 1}})
    assert "hunter2" not in shown["base_url"] and shown["n"] == 3 and "d" not in shown
