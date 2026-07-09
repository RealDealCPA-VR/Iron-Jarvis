"""The context spine: projects, active-project tagging, and prompt injection."""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app


def _client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def test_project_crud_and_first_becomes_active(tmp_path):
    client = _client(tmp_path)
    r = client.post("/projects", json={"name": "Tax Season", "brief": "2026 returns"})
    assert r.status_code == 200
    pid = r.json()["id"]
    assert r.json()["active"] is True  # first project auto-activates

    listed = client.get("/projects").json()["projects"]
    assert len(listed) == 1 and listed[0]["active"] is True

    # Patch the brief; archive deactivates.
    client.patch(f"/projects/{pid}", json={"brief": "2026 returns + planning"})
    assert client.get(f"/projects/{pid}").json()["project"]["brief"].startswith("2026 returns +")
    client.patch(f"/projects/{pid}", json={"status": "archived"})
    assert client.get("/health").json()["active_project"] is None


def test_sessions_do_not_inherit_the_active_project(tmp_path):
    """A project applies ONLY inside the Projects module: a plain session does
    NOT inherit the globally-active project. Only an explicit project_id tags a
    session, and continue preserves that explicit project."""
    client = _client(tmp_path)
    pid = client.post("/projects", json={"name": "Spine"}).json()["id"]  # auto-active
    # No project_id -> untagged, even though a project is active.
    s = client.post("/sessions", json={"task": "do a thing", "wait": True}).json()
    assert s["project_id"] is None

    # Explicit project_id tags it (this is the in-project path); continue keeps it.
    s2 = client.post("/sessions", json={"task": "x", "wait": True, "project_id": pid}).json()
    assert s2["project_id"] == pid
    c = client.post(f"/sessions/{s2['id']}/continue", json={"message": "more", "wait": True}).json()
    assert c["project_id"] == pid


def test_project_context_injected_when_session_is_in_a_project(tmp_path, monkeypatch):
    """Grounding still works when a session is EXPLICITLY in a project (the
    in-module path) — brief + recent project activity ride into the prompt."""
    client = _client(tmp_path)
    pid = client.post("/projects", json={"name": "Dance App", "brief": "SPINE-MARKER-99"}).json()["id"]
    # Prior activity IN the project (becomes "recent activity" context).
    client.post("/sessions", json={"task": "step one", "wait": True, "project_id": pid})

    captured = {}
    platform = client.app.state.platform
    real_get = platform.providers.get

    def spy_get(p, m=None):
        adapter = real_get(p, m)
        real_complete = adapter.complete

        async def spy(*, system, messages, tools):
            captured["system"] = system
            return await real_complete(system=system, messages=messages, tools=tools)

        adapter.complete = spy
        return adapter

    monkeypatch.setattr(platform.providers, "get", spy_get)
    client.post("/sessions", json={"task": "step two", "wait": True, "project_id": pid})
    assert "SPINE-MARKER-99" in captured["system"]  # project brief injected
    assert "step one" in captured["system"]  # recent project activity injected


def test_activate_validation(tmp_path):
    client = _client(tmp_path)
    assert client.post("/projects/nope/activate").status_code == 404
    pid = client.post("/projects", json={"name": "A"}).json()["id"]
    client.patch(f"/projects/{pid}", json={"status": "archived"})
    assert client.post(f"/projects/{pid}/activate").status_code == 400
    assert client.post("/projects", json={"name": "  "}).status_code == 400


def test_models_carry_available_flag(tmp_path):
    client = _client(tmp_path)
    models = client.get("/models").json()["models"]
    assert models and all("available" in m for m in models)


def test_channel_test_endpoint(tmp_path):
    client = _client(tmp_path)
    r = client.post("/comm/channels/mock/test")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert client.post("/comm/channels/ghost/test").status_code == 404


def test_delete_project_interface_only(tmp_path):
    """DELETE removes the project row + untags sessions; disk files untouched."""
    root = tmp_path / "client-files"
    root.mkdir()
    (root / "keep-me.txt").write_text("important")
    client = _client(tmp_path)
    pid = client.post("/projects", json={"name": "Del", "root": str(root)}).json()["id"]
    s = client.post("/sessions", json={"task": "x", "wait": True, "project_id": pid}).json()
    assert s["project_id"] == pid
    r = client.delete(f"/projects/{pid}").json()
    assert r["deleted"] == pid and r["files_touched"] == 0
    assert (root / "keep-me.txt").read_text() == "important"  # disk untouched
    assert client.get("/health").json()["active_project"] is None  # was active
    listed = client.get("/projects").json()["projects"]
    assert all(p["id"] != pid for p in listed)
    # The session's history survives, just untagged.
    left = client.get("/sessions").json()["sessions"]
    assert any(x["id"] == s["id"] and x["project_id"] is None for x in left)
    assert client.delete(f"/projects/{pid}").status_code == 404
