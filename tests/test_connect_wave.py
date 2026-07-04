"""v1.15 connectivity wave: starter templates, tool/MCP generation, bulk clear, slack."""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app


def _client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def test_starter_templates_seeded_once(tmp_path):
    # Seeding happens in the LIFESPAN, so enter the client context.
    with TestClient(create_app(str(tmp_path))) as client:
        t = client.get("/templates").json()["templates"]
        assert len(t) == 3 and all(x["description"] for x in t)
        # Deleting one then rebooting must NOT re-seed (user intent respected).
        client.delete(f"/templates/{t[0]['id']}")
    with TestClient(create_app(str(tmp_path))) as client2:
        assert len(client2.get("/templates").json()["templates"]) == 2


def test_template_create_carries_description(tmp_path):
    client = _client(tmp_path)
    r = client.post(
        "/templates",
        json={"name": "X", "task": "do x", "description": "use when x-ing"},
    ).json()
    assert r["description"] == "use when x-ing"


def test_sessions_bulk_clear(tmp_path):
    client = _client(tmp_path)
    a = client.post("/sessions", json={"task": "a", "wait": True}).json()
    b = client.post("/sessions", json={"task": "b", "wait": True}).json()
    r = client.post("/sessions/clear", json={"statuses": ["completed"]}).json()
    assert r["cleared"] >= 2
    left = client.get("/sessions").json()["sessions"]
    assert all(s["id"] not in (a["id"], b["id"]) for s in left)
    # 'active' is never clearable.
    assert client.post("/sessions/clear", json={"statuses": ["active"]}).status_code == 400


def test_slack_fields_and_manifest(tmp_path):
    client = _client(tmp_path)
    types = client.get("/comm/channel-types").json()["types"]
    slack = next(t for t in types if t["type"] == "slack")
    keys = {f["key"] for f in slack["fields"]}
    assert {"webhook_url", "token", "channel"} <= keys
    assert "chat:write" in (slack["manifest"] or "")
    assert "app manifest" in (slack["manifest_help"] or "")


def test_mcp_catalog_add_delete(tmp_path):
    client = _client(tmp_path)
    cat = client.get("/mcp/catalog").json()["catalog"]
    assert any(c["id"] == "filesystem" for c in cat)
    r = client.post(
        "/mcp/servers",
        json={"name": "test-fs", "command": "definitely-not-a-real-cmd", "args": ["x"]},
    ).json()
    assert r["added"] is True  # persisted even if live-load failed
    assert any(
        s["name"] == "test-fs" for s in client.get("/mcp/servers").json()["servers"]
    )
    # Duplicate rejected; delete works.
    assert (
        client.post("/mcp/servers", json={"name": "test-fs", "command": "x"}).status_code
        == 400
    )
    assert client.delete("/mcp/servers/test-fs").json()["removed"] == "test-fs"
    assert client.delete("/mcp/servers/ghost").status_code == 404


def test_tool_generate_via_llm(tmp_path, monkeypatch):
    client = _client(tmp_path)
    platform = client.app.state.platform
    real_get = platform.providers.get

    def spy_get(p, m=None):
        adapter = real_get(p, m)

        async def canned(*, system, messages, tools):
            from iron_jarvis.providers.adapters.base import LLMResponse

            return LLMResponse(
                text='{"name": "word_count", "description": "Count words in text", '
                '"parameters": [{"name": "text", "type": "string", "required": true, '
                '"description": "the text"}], '
                '"command": ["python", "-c", "print(len(\\"{text}\\".split()))"], '
                '"timeout_seconds": 30}',
                tool_calls=[],
                usage={},
            )

        adapter.complete = canned
        return adapter

    monkeypatch.setattr(platform.providers, "get", spy_get)
    r = client.post("/tools/custom/generate", json={"description": "count words"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "word_count"
    # It is LIVE in the custom tool registry.
    listed = client.get("/tools/custom").json()
    names = [t["name"] for t in listed.get("tools", listed.get("custom", []))]
    assert "word_count" in names
