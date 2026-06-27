"""Saved prompts / task templates store + daemon endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.templates import TemplateStore


def test_template_store_crud(platform):
    store = TemplateStore(platform.engine)
    assert store.list() == []
    rec = store.create("Daily standup", "summarize yesterday", agent_type="researcher")
    assert rec.id and rec.name == "Daily standup" and rec.task == "summarize yesterday"
    assert [t.id for t in store.list()] == [rec.id]
    assert store.get(rec.id) is not None
    assert store.remove(rec.id) is True
    assert store.remove(rec.id) is False
    assert store.list() == []


def test_template_endpoints(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    assert client.get("/templates").json()["templates"] == []
    created = client.post(
        "/templates", json={"name": "Triage", "task": "triage my inbox"}
    ).json()
    assert created["name"] == "Triage"
    assert len(client.get("/templates").json()["templates"]) == 1
    # blank task rejected
    assert client.post("/templates", json={"name": "x", "task": "  "}).status_code == 400
    assert client.delete(f"/templates/{created['id']}").json()["removed"] is True
    assert client.get("/templates").json()["templates"] == []
