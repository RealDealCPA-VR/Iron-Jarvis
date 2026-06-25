from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app


def test_health_tools_and_session_flow(tmp_path):
    client = TestClient(create_app(str(tmp_path)))

    health = client.get("/health").json()
    assert health["status"] == "ok"
    assert any(p["provider"] == "mock" for p in health["providers"])

    tools = client.get("/tools").json()
    assert any(s["name"] == "write_file" for s in tools["tools"])

    created = client.post("/sessions", json={"task": "make a file", "wait": True}).json()
    assert created["status"] == "completed"

    detail = client.get(f"/sessions/{created['id']}").json()
    assert detail["session"]["id"] == created["id"]
    assert any(t["tool"] == "write_file" for t in detail["transcript"]["tools"])

    listing = client.get("/sessions").json()
    assert any(s["id"] == created["id"] for s in listing["sessions"])
