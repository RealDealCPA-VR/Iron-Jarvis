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


# --- usage attribution (v1.102.0) --------------------------------------------


def test_fleet_usage_names_a_removed_endpoint_instead_of_leaving_it_blank(client, monkeypatch):
    """Usage rows OUTLIVE the endpoint that produced them. A node the user
    deleted still has history, and it used to render with an EMPTY label — a
    nameless endpoint they had already removed. The tokens are real and must not
    vanish, but the row has to say the endpoint is gone."""
    monkeypatch.setattr(
        client.app.state.platform.observability,  # type: ignore[attr-defined]
        "usage_summary",
        lambda days: {
            "since_days": days,
            "totals": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "runs": 0},
            "by_day": [],
            "by_model": [
                # 'ollama' is NOT in the registry — the user removed it.
                {"provider": "ollama", "model": "qwen3.6:27b", "input_tokens": 33702,
                 "output_tokens": 6677, "cost_usd": 0.0, "runs": 8},
            ],
        },
    )
    rows = client.get("/fleet/usage").json()["by_node"]
    row = next(r for r in rows if r["node_id"] == "ollama")

    assert row["label"], "a removed endpoint rendered with no name at all"
    assert "removed" in row["label"].lower()
    assert row["retired"] is True
    assert row["input_tokens"] == 33702  # history is preserved, not dropped


def test_fleet_usage_counts_opencode_as_local(client, monkeypatch):
    """The reported symptom: Usage showed ~54M tokens while the Local Fleet page
    showed ~141k, because this route never saw the OpenCode merge."""
    monkeypatch.setattr(
        "iron_jarvis.eval.opencode_usage.opencode_usage",
        lambda *a, **k: {
            "available": True, "note": "n",
            "totals": {"input_tokens": 53_852_959, "output_tokens": 270_176,
                       "cache_tokens": 0, "cost_usd": 0.0, "runs": 14},
            "by_model": [{"provider": "opencode/spark", "model": "fleet",
                          "input_tokens": 53_852_959, "output_tokens": 270_176,
                          "cost_usd": 0.0, "runs": 14}],
            "by_day": [],
        },
    )
    out = client.get("/fleet/usage").json()
    assert out["local_tokens"] >= 54_000_000, "OpenCode work still missing from the fleet"
    assert out["cloud_tokens"] == 0, "own-hardware tokens billed as cloud"
    assert any(r["node_id"] == "opencode/spark" for r in out["by_node"])


def test_opencode_is_named_as_a_source_not_marked_removed(client, monkeypatch):
    """An external source is NOT a deleted endpoint.

    First cut of this fix flagged anything absent from the registry as
    "(removed)", so OpenCode — which was never a fleet node — rendered as
    "opencode/spark (removed)", inventing a deletion that never happened.
    """
    monkeypatch.setattr(
        "iron_jarvis.eval.opencode_usage.opencode_usage",
        lambda *a, **k: {
            "available": True, "note": "n",
            "totals": {"input_tokens": 1000, "output_tokens": 100,
                       "cache_tokens": 0, "cost_usd": 0.0, "runs": 1},
            "by_model": [{"provider": "opencode/spark", "model": "fleet",
                          "input_tokens": 1000, "output_tokens": 100,
                          "cost_usd": 0.0, "runs": 1}],
            "by_day": [],
        },
    )
    row = next(
        r for r in client.get("/fleet/usage").json()["by_node"]
        if r["node_id"] == "opencode/spark"
    )
    assert row["retired"] is False, "OpenCode was never a node — it cannot be removed"
    assert row["external"] is True
    assert "removed" not in row["label"].lower()
    assert row["label"] == "OpenCode · spark"


def test_tests_never_read_the_real_opencode_store(client):
    """conftest isolates it. Without that, this machine's ~54M real tokens leak
    into every usage assertion while CI (no store) sees none — the two would
    silently disagree."""
    out = client.get("/fleet/usage").json()
    assert out["by_node"] == []
    assert out["local_tokens"] == 0


def test_a_rename_is_visible_without_re_probing(tmp_path):
    """Renaming saved correctly and then never appeared (v1.102.1).

    /fleet serves the sampler's snapshots, and a snapshot froze a COPY of the
    node taken when it was last probed. So a PATCH updated the registry while
    the page kept rendering the old label until the daemon restarted — the
    rename control shipped in v1.100.0 looked like a no-op on both pages.

    A snapshot is OBSERVATION; the node is CONFIG ("nothing here is measured").
    Identity now comes from the registry on every read, and the observation is
    left untouched — renaming a box must not blank its status.
    """
    from iron_jarvis.fleet.models import FleetNode, NodeSnapshot
    from iron_jarvis.fleet.sampler import FleetSampler, _NodeState

    class _Reg:
        def __init__(self, node):
            self._node = node

        def nodes(self):
            return [self._node]

    node = FleetNode(id="box", label="old name", base_url="http://x:8000")
    sampler = FleetSampler(_Reg(node))  # type: ignore[arg-type]

    # Seed the cache with a snapshot carrying the node AS IT WAS when probed.
    state = _NodeState()
    state.snapshot = NodeSnapshot(node=node.model_copy(), status="online")
    sampler._state["box"] = state  # noqa: SLF001 — seeding the cache IS the test

    node.label = "new name"  # the registry is edited (what PATCH does)

    out = sampler.snapshots()
    assert out, "no snapshot returned — the test seeded nothing"
    assert out[0].node.label == "new name", "the page would still show the old label"
    assert out[0].status == "online", "renaming clobbered the observation"
