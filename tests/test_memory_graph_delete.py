"""Deleting a memory-graph node (v1.115.0).

REQUESTED: "it should be easy to delete a node that is irrelevant or connect
it to another node." Connect already existed (/memory/graph/link); delete did
not — lessons had a route, working memory had nothing, and long-term notes
are FILES in the user's own bases, which a canvas click must never reach into.
"""

from __future__ import annotations

import pytest

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app


@pytest.fixture
def client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


def _delete(client, node_id):
    return client.post("/memory/graph/node/delete", json={"id": node_id})


def _graph_ids(client):
    return {n["id"] for n in client.get("/memory/graph").json()["nodes"]}


# --- lessons -----------------------------------------------------------------


def test_a_lesson_node_deletes_and_leaves_the_graph(client):
    lid = client.post("/lessons", json={"text": "always confirm the tax year"}).json()["id"]
    node = f"lesson:{lid}"
    assert node in _graph_ids(client)
    r = _delete(client, node)
    assert r.status_code == 200
    assert r.json()["deleted"] == node
    assert node not in _graph_ids(client)


# --- working memory ----------------------------------------------------------


def test_a_working_memory_node_deletes(client):
    client.post("/memory", json={"layer": "user", "key": "client-focus", "text": "Alvarez"})
    node = "wm:user:-:client-focus"
    assert node in _graph_ids(client)
    assert _delete(client, node).status_code == 200
    assert node not in _graph_ids(client)


def test_a_key_containing_colons_survives_the_id_parse(client):
    """The wm id is colon-delimited AND the key may contain colons — the parse
    must split exactly three times or 'notes: 2025: q1' becomes layer garbage."""
    client.post("/memory", json={"layer": "user", "key": "notes: 2025: q1", "text": "x"})
    node = "wm:user:-:notes: 2025: q1"
    assert node in _graph_ids(client)
    assert _delete(client, node).status_code == 200
    assert node not in _graph_ids(client)


# --- long-term notes are refused, honestly -----------------------------------


def test_ltm_nodes_refuse_with_the_base_named(client):
    r = _delete(client, "ltm:brain:some-note.md")
    assert r.status_code == 400
    assert "'brain' memory base" in r.json()["detail"]
    assert "manage it there" in r.json()["detail"]


# --- edges are swept ---------------------------------------------------------


def test_deleting_a_node_sweeps_its_links(client):
    a = client.post("/lessons", json={"text": "alpha"}).json()["id"]
    b = client.post("/lessons", json={"text": "beta"}).json()["id"]
    na, nb = f"lesson:{a}", f"lesson:{b}"
    assert client.post("/memory/graph/link", json={"a": na, "b": nb}).status_code == 200
    edges = client.get("/memory/graph").json()["edges"]
    assert any(e["kind"] == "manual" for e in edges)

    r = _delete(client, na)
    assert r.status_code == 200
    assert r.json()["links_removed"] == 1
    edges_after = client.get("/memory/graph").json()["edges"]
    assert not any(na in (e["a"], e["b"]) for e in edges_after)


# --- honest failure shapes ---------------------------------------------------


def test_missing_nodes_are_404_not_silent_success(client):
    """A no-op 200 would leave the node on screen after refresh — the exact
    'is it broken or am I confused' moment this app tries never to create."""
    assert _delete(client, "lesson:nope").status_code == 404
    assert _delete(client, "wm:user:-:nope").status_code == 404


def test_garbage_ids_are_400(client):
    assert _delete(client, "").status_code == 400
    assert _delete(client, "sess:123").status_code == 400
    assert _delete(client, "wm:onlytwo").status_code == 400


# --- the sidecar's full-text read (v1.116.0) ---------------------------------


def _detail(client, node_id):
    return client.post("/memory/graph/node", json={"id": node_id})


def test_detail_returns_the_FULL_text_not_the_graph_snippet(client):
    """The graph payload clips snippets to ~220 chars; the sidecar exists to
    show what is actually in the node."""
    long_text = "always confirm the year. " * 30  # ~750 chars, well past the clip
    lid = client.post("/lessons", json={"text": long_text}).json()["id"]
    body = _detail(client, f"lesson:{lid}").json()
    assert body["partial"] is False
    assert len(body["text"]) > 500
    assert body["text"].startswith("always confirm the year.")


def test_detail_reads_working_memory_with_colon_keys(client):
    client.post("/memory", json={"layer": "user", "key": "q: one", "text": "full body here"})
    body = _detail(client, "wm:user:-:q: one").json()
    assert body["text"] == "full body here"
    assert body["meta"]["layer"] == "user"


def test_detail_is_honest_about_ltm_nodes(client):
    body = _detail(client, "ltm:clientA:note.md").json()
    assert body["partial"] is True
    assert body["meta"]["base"] == "clientA"


def test_detail_404s_and_400s_match_delete_semantics(client):
    assert _detail(client, "lesson:nope").status_code == 404
    assert _detail(client, "wm:user:-:nope").status_code == 404
    assert _detail(client, "junk:1").status_code == 400
    assert _detail(client, "").status_code == 400
