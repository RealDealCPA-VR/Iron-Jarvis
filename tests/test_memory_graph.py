"""Memory graph: nodes across surfaces, similarity edges, manual curation."""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.memory.graph import canonical_pair


def _graph(client, threshold=0.45):
    r = client.get(f"/memory/graph?threshold={threshold}")
    assert r.status_code == 200
    return r.json()


def _pairs(edges, kind=None):
    return {
        canonical_pair(e["a"], e["b"])
        for e in edges
        if kind is None or e["kind"] == kind
    }


def test_graph_gathers_nodes_from_all_three_surfaces(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    client.post("/lessons", json={"text": "Prefer small verified steps"})
    client.post("/memory", json={"layer": "user", "key": "editor", "text": "I use VS Code"})
    client.post("/ltm/append", json={"title": "Deploy notes", "content": "Use the blue pipeline"})

    g = _graph(client)
    groups = {n["group"] for n in g["nodes"]}
    assert groups == {"lesson", "memory", "note"}
    assert g["embedder"]  # says WHAT scored similarity
    assert "note" in g  # offline mock => honesty note
    for n in g["nodes"]:
        assert n["id"] and n["label"] and "snippet" in n


def test_similar_texts_get_an_edge_and_unlink_blocks_it(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    # Near-identical texts — the mock (hashed) embedder scores these high.
    client.post("/memory", json={"layer": "user", "key": "a", "text": "the quarterly tax deadline is april 15"})
    client.post("/memory", json={"layer": "user", "key": "b", "text": "the quarterly tax deadline is april 15!"})

    g = _graph(client, threshold=0.3)
    sim = _pairs(g["edges"], "similar")
    assert len(sim) >= 1
    a, b = next(iter(sim))

    # Disconnect: the similarity edge is blocked and never comes back.
    r = client.post("/memory/graph/unlink", json={"a": a, "b": b}).json()
    assert r == {"removed": "auto", "blocked": True}
    g2 = _graph(client, threshold=0.3)
    assert (a, b) not in _pairs(g2["edges"])


def test_manual_link_connect_and_disconnect(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    client.post("/memory", json={"layer": "user", "key": "coffee", "text": "espresso, no sugar"})
    client.post("/lessons", json={"text": "Always confirm before deleting files"})
    g = _graph(client)
    ids = [n["id"] for n in g["nodes"]]
    wm = next(i for i in ids if i.startswith("wm:"))
    lesson = next(i for i in ids if i.startswith("lesson:"))

    assert client.post("/memory/graph/link", json={"a": wm, "b": lesson}).json()["linked"]
    g2 = _graph(client)
    assert canonical_pair(wm, lesson) in _pairs(g2["edges"], "manual")

    # Linking again is idempotent.
    again = client.post("/memory/graph/link", json={"a": lesson, "b": wm}).json()
    assert again["linked"] and again.get("note") == "already linked"

    # Disconnecting a MANUAL link deletes it (no block — relinking works).
    r = client.post("/memory/graph/unlink", json={"a": wm, "b": lesson}).json()
    assert r == {"removed": "manual", "blocked": False}
    g3 = _graph(client)
    assert canonical_pair(wm, lesson) not in _pairs(g3["edges"])
    assert client.post("/memory/graph/link", json={"a": wm, "b": lesson}).json()["linked"]


def test_link_lifts_block(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    client.post("/memory", json={"layer": "user", "key": "x", "text": "identical text here"})
    client.post("/memory", json={"layer": "user", "key": "y", "text": "identical text here"})
    g = _graph(client, threshold=0.3)
    pair = next(iter(_pairs(g["edges"], "similar")))
    client.post("/memory/graph/unlink", json={"a": pair[0], "b": pair[1]})
    # The user reconnects — block lifted, manual edge shown.
    client.post("/memory/graph/link", json={"a": pair[0], "b": pair[1]})
    g2 = _graph(client, threshold=0.3)
    assert pair in _pairs(g2["edges"], "manual")


def test_link_validation(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    assert client.post("/memory/graph/link", json={"a": "x", "b": "x"}).status_code == 400
    assert client.post("/memory/graph/link", json={"a": "", "b": "y"}).status_code == 400
