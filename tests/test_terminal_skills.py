"""Terminal AI assist: every discovered skill usable by ANY provider (prompt-side)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.terminals.backend import FakeBackend


def _app(tmp_path, monkeypatch):
    # Seed a user skill BEFORE boot so repopulate() discovers it.
    skill_dir = tmp_path / ".ironjarvis" / "skills" / "deploy-dance"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: deploy-dance\ndescription: how to deploy the dance app\n---\n"
        "Step 1: build. Step 2: dance. SECRET-MARKER-42.",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "iron_jarvis.terminals.session.default_backend", lambda: FakeBackend()
    )
    return TestClient(create_app(str(tmp_path)))


def _captured_system(client, monkeypatch):
    """Patch the mock adapter path to capture the system prompt it receives."""
    captured = {}
    platform = client.app.state.platform
    real_get = platform.providers.get

    def spy_get(p, m=None):
        adapter = real_get(p, m)
        real_complete = adapter.complete

        async def spy_complete(*, system, messages, tools):
            captured["system"] = system
            return await real_complete(system=system, messages=messages, tools=tools)

        adapter.complete = spy_complete
        return adapter

    monkeypatch.setattr(platform.providers, "get", spy_get)
    return captured


def test_explicit_skill_injected_for_any_provider(tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch)
    captured = _captured_system(client, monkeypatch)
    term = client.post("/terminals", json={}).json()
    r = client.post(
        f"/terminals/{term['id']}/ai",
        json={"prompt": "deploy please", "skill": "deploy-dance"},
    )
    assert r.status_code == 200
    assert r.json()["skills"] == ["deploy-dance"]
    assert "SECRET-MARKER-42" in captured["system"]


def test_auto_matches_relevant_skill(tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch)
    captured = _captured_system(client, monkeypatch)
    term = client.post("/terminals", json={}).json()
    r = client.post(
        f"/terminals/{term['id']}/ai",
        json={"prompt": "how do I deploy the dance app?"},  # auto-search
    )
    assert r.status_code == 200
    assert "deploy-dance" in r.json()["skills"]
    assert "SECRET-MARKER-42" in captured["system"]


def test_none_disables_injection(tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch)
    captured = _captured_system(client, monkeypatch)
    term = client.post("/terminals", json={}).json()
    r = client.post(
        f"/terminals/{term['id']}/ai",
        json={"prompt": "how do I deploy the dance app?", "skill": "none"},
    )
    assert r.status_code == 200
    assert r.json()["skills"] == []
    assert "SECRET-MARKER-42" not in captured.get("system", "")


def test_unknown_skill_404(tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch)
    term = client.post("/terminals", json={}).json()
    r = client.post(
        f"/terminals/{term['id']}/ai", json={"prompt": "x", "skill": "nope-skill"}
    )
    assert r.status_code == 404
