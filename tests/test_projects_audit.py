"""Projects-module audit round: root/model validation, delete cascade,
deliverable verification, the enriched list, and artifact project-tagging."""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app


def _client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


# --- root validation (a typo silently degraded every file task) ----------------


def test_create_rejects_nonexistent_or_relative_root(tmp_path):
    client = _client(tmp_path)
    assert client.post("/projects", json={"name": "A", "root": "relative/dir"}).status_code == 400
    ghost = tmp_path / "does-not-exist"
    assert client.post("/projects", json={"name": "A", "root": str(ghost)}).status_code == 400
    # A real folder is accepted and normalised.
    real = tmp_path / "real"
    real.mkdir()
    r = client.post("/projects", json={"name": "A", "root": str(real)})
    assert r.status_code == 200 and r.json()["root"]


def test_patch_rejects_bad_root_and_unknown_provider(tmp_path):
    client = _client(tmp_path)
    pid = client.post("/projects", json={"name": "P"}).json()["id"]
    assert client.patch(f"/projects/{pid}", json={"root": str(tmp_path / "ghost")}).status_code == 400
    assert (
        client.patch(f"/projects/{pid}", json={"default_provider": "totally-not-a-provider"}).status_code
        == 400
    )
    # Clearing the pin (empty provider) is allowed.
    assert client.patch(f"/projects/{pid}", json={"default_provider": ""}).status_code == 200
    # A real folder can be SET after creation (the workspace gap).
    real = tmp_path / "later"
    real.mkdir()
    assert client.patch(f"/projects/{pid}", json={"root": str(real)}).status_code == 200


# --- delete cascade (knowledge/threads/workflows must not orphan) ---------------


def test_delete_cascades_knowledge_and_untags_threads(tmp_path):
    client = _client(tmp_path)
    pid = client.post("/projects", json={"name": "Cascade"}).json()["id"]
    client.post(f"/projects/{pid}/knowledge", json={"name": "n", "text": "grounding fact"})
    assert client.get(f"/projects/{pid}/knowledge").json()["count"] == 1
    # A chat thread tagged to the project.
    client.put("/chat/threads/new", json={"messages": [], "title": "t", "project_id": pid})

    r = client.delete(f"/projects/{pid}").json()
    assert r["deleted"] == pid and r["knowledge_deleted"] == 1
    # Knowledge is gone (project 404s, so query the store directly).
    from iron_jarvis.projects.knowledge import list_knowledge

    assert list_knowledge(client.app.state.platform, pid) == []
    # Threads that were tagged are untagged, not dangling at a dead project.
    threads = client.get(f"/chat/threads?project_id={pid}").json()["threads"]
    assert threads == []


# --- honest deliverable verification -------------------------------------------


def test_deliverable_check(tmp_path):
    client = _client(tmp_path)
    root = tmp_path / "work"
    root.mkdir()
    pid = client.post("/projects", json={"name": "D", "root": str(root)}).json()["id"]
    made = root / "report.md"
    made.write_text("# real output")
    r = client.get(f"/projects/{pid}/deliverable?path={made}").json()
    assert r["exists"] is True and r["size"] > 0
    missing = client.get(f"/projects/{pid}/deliverable?path={root / 'ghost.md'}").json()
    assert missing["exists"] is False
    # Outside the project folder is refused (never stat arbitrary disk).
    outside = tmp_path / "elsewhere.md"
    outside.write_text("x")
    assert client.get(f"/projects/{pid}/deliverable?path={outside}").status_code == 400


# --- enriched list (knowledge_count + root_exists, no N+1) ----------------------


def test_list_reports_counts_and_root_existence(tmp_path):
    client = _client(tmp_path)
    real = tmp_path / "here"
    real.mkdir()
    pid = client.post("/projects", json={"name": "Rich", "root": str(real)}).json()["id"]
    client.post(f"/projects/{pid}/knowledge", json={"name": "k", "text": "fact"})
    row = next(p for p in client.get("/projects").json()["projects"] if p["id"] == pid)
    assert row["knowledge_count"] == 1
    assert row["root_exists"] is True
    # A vanished folder is flagged (the tile warns before a task fails on it).
    import shutil

    shutil.rmtree(real)
    row2 = next(p for p in client.get("/projects").json()["projects"] if p["id"] == pid)
    assert row2["root_exists"] is False


# --- artifact project-tagging (creative media joins the spine) ------------------


def test_artifact_inherits_session_project(tmp_path):
    """An artifact from a project TASK (a session EXPLICITLY in a project)
    inherits that project — the in-module path, still scoped to the workspace."""
    client = _client(tmp_path)
    pid = client.post("/projects", json={"name": "Media"}).json()["id"]
    s = client.post("/sessions", json={"task": "make", "wait": True, "project_id": pid}).json()
    assert s["project_id"] == pid
    platform = client.app.state.platform
    platform.artifacts.save("clip", b"\x89PNG bytes", kind="image", filename="clip.png", session_id=s["id"])
    # The gallery scoped to the project shows it; a different project doesn't.
    scoped = client.get(f"/creative/items?project_id={pid}").json()["items"]
    assert any(i["name"] == "clip" and i["project_id"] == pid for i in scoped)
    other = client.post("/projects", json={"name": "Empty"}).json()["id"]
    assert client.get(f"/creative/items?project_id={other}").json()["items"] == []


def test_direct_creative_ingest_is_project_agnostic(tmp_path):
    """Creative is its OWN module: a direct ingest/upload does NOT get tagged to
    whatever project happens to be active — only in-project TASK output is
    scoped to a project."""
    client = _client(tmp_path)
    pid = client.post("/projects", json={"name": "Active"}).json()["id"]  # auto-active
    src = tmp_path / "gen.png"
    src.write_bytes(b"\x89PNG synthetic")
    out = client.post("/creative/ingest", json={"path": str(src)}).json()
    # Present in the un-scoped gallery, but NOT tagged to the active project.
    assert any(i["name"] == out["name"] for i in client.get("/creative/items").json()["items"])
    assert client.get(f"/creative/items?project_id={pid}").json()["items"] == []
