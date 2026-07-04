"""Chat threads persistence + live model discovery."""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers import discovery


def _client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def test_thread_crud_and_autotitle(tmp_path):
    client = _client(tmp_path)
    r = client.put("/chat/threads/new", json={"messages": [
        {"role": "user", "content": "help me plan the Henderson return workflow"},
        {"role": "assistant", "content": "sure!"},
    ]}).json()
    tid = r["id"]
    assert r["title"].startswith("help me plan the Henderson")
    listed = client.get("/chat/threads").json()["threads"]
    assert listed[0]["id"] == tid and listed[0]["messages"] == 2
    # Update in place (autosave), then load + delete.
    client.put(f"/chat/threads/{tid}", json={"messages": [
        {"role": "user", "content": "a"}, {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
    ], "persona": "accountant"})
    got = client.get(f"/chat/threads/{tid}").json()
    assert len(got["messages"]) == 3 and got["persona"] == "accountant"
    assert client.delete(f"/chat/threads/{tid}").json()["deleted"] == tid
    assert client.get(f"/chat/threads/{tid}").status_code == 404


def test_thread_bad_body_400(tmp_path):
    assert _client(tmp_path).put("/chat/threads/new", json={}).status_code == 400


def test_discovery_merges_and_drops_stale(tmp_path, monkeypatch):
    client = _client(tmp_path)
    client.post("/connections/anthropic/key", json={"key": "sk-ant-test"})
    discovery.clear_cache()
    monkeypatch.setattr(
        discovery, "_anthropic_models",
        lambda key: ["claude-opus-4-8", "claude-brand-new-9"],
    )
    models = client.get("/models").json()["models"]
    anth = {m["model"] for m in models if m["provider"] == "anthropic"}
    assert "claude-brand-new-9" in anth          # new model appears
    assert "claude-sonnet-4-6" not in anth       # stale curated id dropped
    assert "claude-opus-4-8" in anth


def test_discovery_failure_keeps_curated(tmp_path, monkeypatch):
    client = _client(tmp_path)
    client.post("/connections/anthropic/key", json={"key": "sk-ant-test"})
    discovery.clear_cache()
    monkeypatch.setattr(
        discovery, "_anthropic_models",
        lambda key: (_ for _ in ()).throw(RuntimeError("api down")),
    )
    models = client.get("/models").json()["models"]
    anth = {m["model"] for m in models if m["provider"] == "anthropic"}
    assert "claude-opus-4-8" in anth and "claude-sonnet-4-6" in anth  # curated intact
    discovery.clear_cache()
