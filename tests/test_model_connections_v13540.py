"""v1.354.0 (product readiness, G47) — the model-server card matches what answers.

The audit's live finding: on the user's PC the Custom endpoint card read
"Not connected" while that KEYLESS endpoint was the default and answered every
chat (/connections custom ``connected: false`` beside /health ``available:
true``). ``ConnectionRegistry.status()`` read only the ConnectionRecord, and a
keyless local server never writes one (there is nothing to store).

One truth now, the same one v1.230.0 gave inherited logins: the platform hands
the registry an ``available`` oracle beside ``inherited_from``. A keyless
ENDPOINT_SLOT reads ``connected`` with ``source == ENDPOINT_SOURCE`` exactly
while the router says it answers; a keyed slot keeps its record's answer; an
endpoint saved on the card ("fleet-<id>") counts only once it was SEEN to
answer, so a mistyped address never reads Connected. Test agrees with the row.

Everything below drives the REAL app factory (no lifespan, so no sampler runs:
reachability is set by hand the way the sampler and Verify tools set it).
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import iron_jarvis.connections.models  # noqa: F401  (register table before init_db)
from iron_jarvis.connections import ConnectionRegistry
from iron_jarvis.connections.registry import ENDPOINT_SLOTS, ENDPOINT_SOURCE
from iron_jarvis.core.db import init_db, make_engine
from iron_jarvis.daemon.app import create_app

DEAD = "http://127.0.0.1:9/v1"


def _client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


def _rows(client: TestClient) -> dict[str, dict]:
    r = client.get("/connections")
    assert r.status_code == 200, r.text
    return {c["provider"]: c for c in r.json()["connections"]}


def _health(client: TestClient) -> dict[str, dict]:
    return {p["provider"]: p for p in client.get("/health").json()["providers"]}


def _set_custom_slot(client: TestClient, url: str = DEAD) -> None:
    r = client.put(
        "/settings", json={"values": {"custom_base_url": url, "custom_model": "m-local"}}
    )
    assert r.status_code == 200, r.text


# --- the words ----------------------------------------------------------------


def test_the_source_is_the_acceptance_wording_and_the_dashboard_key_reads_it():
    # The readiness gap's acceptance line, word for word.
    assert ENDPOINT_SOURCE == "your endpoint — no key needed"
    # dashboard/lib/modelServers.isOwnEndpoint keys on the leading words.
    assert ENDPOINT_SOURCE.startswith("your endpoint ")
    assert "custom" in ENDPOINT_SLOTS and "ollama" in ENDPOINT_SLOTS


# --- the real app: the card, /health and Test tell one story -------------------


def test_a_keyless_endpoint_reads_connected_while_the_router_says_it_answers(tmp_path):
    client = _client(tmp_path)
    before = _rows(client)["custom"]
    assert before["connected"] is False
    assert before["source"] == ""

    _set_custom_slot(client)
    row = _rows(client)["custom"]
    health = _health(client)["custom"]
    assert health["available"] is True
    assert row["connected"] is True
    assert row["status"] == "connected"
    assert row["source"] == ENDPOINT_SOURCE
    # Nothing is stored: the row invents no account.
    assert row["account"] == ""
    # The two surfaces cannot disagree.
    assert row["connected"] == health["available"]

    # Test sits under the same row and must not answer "not connected".
    probe = client.post("/connections/custom/test").json()
    assert probe["ok"] is True
    assert "not connected" not in probe["detail"]
    assert "no key" in probe["detail"]


def test_the_card_follows_the_router_when_the_server_stops_answering(tmp_path):
    client = _client(tmp_path)
    _set_custom_slot(client)
    fleet = client.app.state.platform.fleet
    # The sampler's verdict on the seeded slot (node id "custom").
    fleet.set_reachable("custom", False)
    assert _health(client)["custom"]["available"] is False
    row = _rows(client)["custom"]
    assert row["connected"] is False
    assert row["source"] == ""
    probe = client.post("/connections/custom/test").json()
    assert probe["ok"] is False

    fleet.set_reachable("custom", True)
    assert _rows(client)["custom"]["connected"] is True

    # Clearing the address turns the card back off, live.
    _set_custom_slot(client, "")
    assert _rows(client)["custom"]["connected"] is False


def test_a_keyed_slot_keeps_the_records_answer_and_never_says_no_key(tmp_path):
    client = _client(tmp_path)
    _set_custom_slot(client)
    # A key in the vault with no ConnectionRecord (saved on the Secrets page):
    # the endpoint is keyed, so "no key needed" would be a lie.
    r = client.post(
        "/secrets", json={"name": "custom_api_key", "value": "sk-endpoint-xyz", "kind": "api_key"}
    )
    assert r.status_code == 200, r.text
    row = _rows(client)["custom"]
    assert row["source"] != ENDPOINT_SOURCE
    assert row["connected"] is False  # exactly as before this change

    # Connected through the card's key door: the record's own answer.
    r = client.post("/connections/custom/key", json={"key": "sk-endpoint-xyz"})
    assert r.status_code == 200, r.text
    row = _rows(client)["custom"]
    assert row["connected"] is True
    assert row["source"] == "vault"
    assert "sk-endpoint-xyz" not in json.dumps(client.get("/connections").json())


def test_an_endpoint_saved_on_the_card_counts_only_once_it_was_seen_answering(tmp_path):
    client = _client(tmp_path)
    r = client.post(
        "/fleet/nodes",
        json={"base_url": DEAD, "label": "LM Studio", "routable": True, "default_model": "m"},
    )
    assert r.status_code == 200, r.text
    node = r.json()["node"]
    fleet = client.app.state.platform.fleet

    # The add route probed the dead address once and recorded it: a mistyped
    # address is never "Connected" beside the save note's "could not reach".
    assert fleet.reachable(f"fleet-{node['id']}") is False
    assert _rows(client)["custom"]["connected"] is False

    # After a restart nothing is known until the sampler's first pass, and the
    # router's own answer for an unprobed node is the optimistic default. The
    # card claims only what was SEEN to answer.
    restarted = _client(tmp_path)
    fleet2 = restarted.app.state.platform.fleet
    assert fleet2.reachable(f"fleet-{node['id']}") is None
    assert _health(restarted)[f"fleet-{node['id']}"]["available"] is True
    assert _rows(restarted)["custom"]["connected"] is False
    fleet2.set_reachable(node["id"], True)
    assert _rows(restarted)["custom"]["connected"] is True

    # Verify tools / the sampler saw it answer.
    fleet.set_reachable(node["id"], True)
    row = _rows(client)["custom"]
    assert row["connected"] is True
    assert row["source"] == ENDPOINT_SOURCE

    # And it stopped answering.
    fleet.set_reachable(node["id"], False)
    assert _rows(client)["custom"]["connected"] is False

    # A KEYED endpoint seen answering does not claim "no key needed".
    r = client.patch(f"/fleet/nodes/{node['id']}", json={"api_key_name": "endpoint_x_key"})
    assert r.status_code == 200, r.text
    fleet.set_reachable(node["id"], True)
    assert _rows(client)["custom"]["connected"] is False


def test_a_failed_check_records_nothing_so_the_card_stays_not_connected(tmp_path):
    """The dashboard's save note keys on this answer: ``tool_use: null`` with
    the check's own ``error``. And a failed check leaves no reachability, so
    the card cannot read Connected beside it."""
    client = _client(tmp_path)
    node = client.post(
        "/fleet/nodes",
        json={"base_url": DEAD, "label": "typo", "routable": True, "default_model": "m"},
    ).json()["node"]
    v = client.post(f"/fleet/nodes/{node['id']}/verify", json={"model": "m"}).json()
    assert v["tool_use"] is None
    assert v["error"]
    assert _rows(client)["custom"]["connected"] is False


# --- the registry on its own: bounded, never raises, slots only ----------------


class _Secrets:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def set(self, name, value, kind="generic", description=""):
        self.store[name] = value

    def get(self, name):
        return self.store.get(name)

    def set_oauth(self, name, token, description=""):
        self.store[name] = json.dumps(token)

    def get_oauth(self, name):
        raw = self.store.get(name)
        return json.loads(raw) if raw is not None else None

    def delete(self, name):
        return self.store.pop(name, None) is not None


def _registry(tmp_path, available=None) -> ConnectionRegistry:
    engine = make_engine(str(tmp_path / "c.db"))
    init_db(engine)
    return ConnectionRegistry(engine, _Secrets(), available=available)


def test_the_oracle_is_asked_about_endpoint_slots_only(tmp_path, monkeypatch):
    # Pixio honours an env key (its own fallback); this machine may have one.
    monkeypatch.delenv("PIXIO_API_KEY", raising=False)
    asked: list[str] = []

    def oracle(name: str) -> bool:
        asked.append(name)
        return True

    rows = {r["provider"]: r for r in _registry(tmp_path, oracle).status()}
    assert set(asked) <= set(ENDPOINT_SLOTS)
    assert rows["custom"]["connected"] is True
    # An answering oracle never turns a cloud account "connected".
    for cloud in ("anthropic", "openai", "openrouter", "pixio"):
        assert rows[cloud]["connected"] is False, cloud


def test_no_oracle_is_record_only_and_a_fault_reads_not_connected(tmp_path):
    rows = {r["provider"]: r for r in _registry(tmp_path, None).status()}
    assert rows["custom"]["connected"] is False

    def boom(_name):
        raise RuntimeError("oracle exploded")

    other = tmp_path / "faulty"
    other.mkdir()
    reg = _registry(other, boom)
    rows = {r["provider"]: r for r in reg.status()}
    assert rows["custom"]["connected"] is False
    assert rows["custom"]["source"] == ""
    assert reg.test("custom")["ok"] is False


@pytest.mark.parametrize("answers", [True, False])
def test_registry_test_agrees_with_its_row(tmp_path, answers):
    reg = _registry(tmp_path, lambda _n: answers)
    row = {r["provider"]: r for r in reg.status()}["custom"]
    assert reg.test("custom")["ok"] is row["connected"] is answers
