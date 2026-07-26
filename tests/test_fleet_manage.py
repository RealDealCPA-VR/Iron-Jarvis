"""Fleet endpoints can be renamed and removed (v1.100.0).

The Local Fleet page was ADD-ONLY: it imported `get, post` and called exactly
three endpoints (probe / add / refresh). `PATCH` and `DELETE` had existed on the
daemon the whole time and nothing called them, so a box you retired stayed
pinned to the page forever with no way to rename or remove it.

The two config-seeded slots were worse than unexposed — `remove()` refused them
outright ("managed in Settings"), which is exactly the endpoint a user most
wants gone after moving from Ollama to vLLM. They are DERIVED from
`ollama_base_url` / `custom_base_url` on every read, so clearing those keys IS
the removal; deleting a stored row alone would let the node reappear.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app


@pytest.fixture
def client(tmp_path) -> TestClient:
    app = create_app(str(tmp_path))
    c = TestClient(app)
    c.app = app  # type: ignore[attr-defined]
    return c


def _cfg(client: TestClient):
    return client.app.state.platform.config  # type: ignore[attr-defined]


def _ids(client: TestClient) -> list[str]:
    """Node ids straight from the registry.

    Deliberately NOT `GET /fleet` — that returns the sampler's last snapshots,
    and the sampler's background loop doesn't run under TestClient, so it would
    report an empty fleet and every assertion here would pass vacuously.
    """
    return [n.id for n in client.app.state.platform.fleet.nodes()]  # type: ignore[attr-defined]


# --- rename ------------------------------------------------------------------


def test_a_user_added_endpoint_can_be_renamed(client):
    node = client.post(
        "/fleet/nodes", json={"base_url": "http://10.0.0.5:8000", "label": "box-a"}
    ).json()["node"]
    r = client.patch(f"/fleet/nodes/{node['id']}", json={"label": "Studio vLLM"})
    assert r.status_code == 200
    assert r.json()["node"]["label"] == "Studio vLLM"


def test_a_config_seeded_endpoint_can_be_renamed_too(client):
    """A seed is derived from config, so renaming has to PROMOTE it to a stored
    row — otherwise the new name evaporates on the next read."""
    _cfg(client).custom_base_url = "http://127.0.0.1:8000"

    r = client.patch("/fleet/nodes/custom", json={"label": "Spark GB10"})
    assert r.status_code == 200
    assert r.json()["node"]["label"] == "Spark GB10"

    # Survives a re-read (the point of promotion).
    again = client.app.state.platform.fleet.get("custom")  # type: ignore[attr-defined]
    assert again is not None and again.label == "Spark GB10"
    assert again.base_url == "http://127.0.0.1:8000"  # still config-driven


# --- removal -----------------------------------------------------------------


def test_removing_a_user_added_endpoint_touches_no_settings(client):
    node = client.post("/fleet/nodes", json={"base_url": "http://10.0.0.9:8000"}).json()["node"]
    r = client.delete(f"/fleet/nodes/{node['id']}")
    assert r.status_code == 200
    assert r.json()["cleared_settings"] == []
    assert node["id"] not in _ids(client)


def test_removing_the_ollama_slot_actually_removes_it(client):
    """THE BUG. Moving Ollama -> vLLM left a dead endpoint that could not be
    deleted: the slot is re-derived from config on every read, and remove()
    used to refuse it. Clearing the backing keys is the removal."""
    cfg = _cfg(client)
    cfg.ollama_base_url = "http://127.0.0.1:11434"
    cfg.ollama_model = "llama3"
    assert "ollama" in _ids(client)

    r = client.delete("/fleet/nodes/ollama")
    assert r.status_code == 200
    assert set(r.json()["cleared_settings"]) == {"ollama_base_url", "ollama_model"}

    assert "ollama" not in _ids(client), "it re-derived from config — not removed"
    assert cfg.ollama_base_url == ""


def test_a_renamed_seed_still_removes_cleanly(client):
    """Renaming promotes the seed to a stored row. Removal must clear BOTH that
    row and the config keys, or the node comes back under its old name."""
    cfg = _cfg(client)
    cfg.ollama_base_url = "http://127.0.0.1:11434"
    client.patch("/fleet/nodes/ollama", json={"label": "Retired box"})

    r = client.delete("/fleet/nodes/ollama")
    assert r.status_code == 200
    assert "ollama_base_url" in r.json()["cleared_settings"]
    assert "ollama" not in _ids(client)


def test_removal_is_reversible_by_re_entering_the_url(client):
    """Clearing settings is a big hammer — it must not be a one-way door."""
    cfg = _cfg(client)
    cfg.ollama_base_url = "http://127.0.0.1:11434"
    client.delete("/fleet/nodes/ollama")
    assert "ollama" not in _ids(client)

    cfg.ollama_base_url = "http://127.0.0.1:11434"
    assert "ollama" in _ids(client)


def test_removing_an_unknown_node_is_404(client):
    assert client.delete("/fleet/nodes/nope").status_code == 404
    assert client.patch("/fleet/nodes/nope", json={"label": "x"}).status_code == 404


# --- the UI wiring that was missing ------------------------------------------


def test_the_fleet_page_now_calls_patch_and_delete():
    """The whole defect was a UI that never called these. Assert the page wires
    them, so a refactor that drops the controls fails here rather than silently
    returning the fleet to add-only."""
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "dashboard" / "app" / "fleet" / "page.tsx"
    text = src.read_text(encoding="utf-8")
    assert "/fleet/nodes/${encodeURIComponent(node.id)}" in text
    assert "patch(" in text and "del<" in text
    assert "ConfirmButton" in text, "removal must be confirm-gated"
