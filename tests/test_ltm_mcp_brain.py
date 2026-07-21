"""MCP-served brain as a long-term-memory source (kind "mcp", v1.74.0).

The connector discovers the server's search/append tools by name, maps
arguments from each tool's own input schema, normalizes JSON or prose
replies to the uniform hit shape, and connects lazily (registering a source
never touches the network). The /ltm/sources route vaults the bearer token.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.ltm.mcp_brain import McpBrainConnector


class _FakeClient:
    """Stands in for MCPClient: a hermes-brain-ish tool surface."""

    def __init__(self, tools, results=None, error=False):
        self._tools = tools
        self._results = results or {}
        self._error = error
        self.calls: list[tuple[str, dict]] = []

    def list_tools(self):
        return self._tools

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if self._error:
            return {"content": [{"type": "text", "text": "boom"}], "isError": True}
        return {
            "content": [{"type": "text", "text": self._results.get(name, "")}],
            "isError": False,
        }


_TOOLS = [
    {
        "name": "search_notes",
        "description": "Search the vault",
        "inputSchema": {"type": "object", "properties": {"query": {}, "limit": {}}},
    },
    {
        "name": "append_note",
        "description": "Add a note",
        "inputSchema": {"type": "object", "properties": {"title": {}, "content": {}}},
    },
]


def test_search_discovers_tool_maps_args_and_normalizes_json_hits():
    payload = json.dumps(
        {
            "results": [
                {"title": "Client A", "content": "trust acct notes", "path": "clients/a.md"},
                {"title": "Client B", "excerpt": "1031 exchange", "id": "n42"},
            ]
        }
    )
    fake = _FakeClient(_TOOLS, results={"search_notes": payload})
    conn = McpBrainConnector("hermes-brain", client=fake)
    hits = conn.search("trust accounts", k=5)
    name, args = fake.calls[0]
    assert name == "search_notes"
    assert args["query"] == "trust accounts" and args["limit"] == 5
    assert hits[0] == {
        "title": "Client A",
        "snippet": "trust acct notes",
        "ref": "clients/a.md",
        "source": "hermes-brain",
    }
    assert hits[1]["ref"] == "n42"


def test_search_prose_reply_degrades_to_paragraph_hits():
    fake = _FakeClient(_TOOLS, results={"search_notes": "First note about X.\n\nSecond note."})
    hits = McpBrainConnector("b", client=fake).search("x", k=5)
    assert len(hits) == 2 and hits[0]["snippet"].startswith("First note")


def test_append_maps_title_and_content():
    fake = _FakeClient(_TOOLS, results={"append_note": "notes/new.md"})
    ref = McpBrainConnector("b", client=fake).append("Meeting", "Discussed the trust.")
    name, args = fake.calls[0]
    assert name == "append_note"
    assert args == {"title": "Meeting", "content": "Discussed the trust."}
    assert ref == "notes/new.md"


def test_read_only_server_is_honest():
    fake = _FakeClient([_TOOLS[0]])  # search only, no append tool
    conn = McpBrainConnector("b", client=fake)
    try:
        conn.append("t", "c")
        raise AssertionError("expected a read-only error")
    except RuntimeError as exc:
        assert "read-only" in str(exc)


def test_server_error_raises_with_detail():
    fake = _FakeClient(_TOOLS, error=True)
    try:
        McpBrainConnector("b", client=fake).search("x")
        raise AssertionError("expected the server error to surface")
    except RuntimeError as exc:
        assert "boom" in str(exc)


def test_route_adds_mcp_source_lazily_and_vaults_token(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    r = client.post(
        "/ltm/sources",
        json={
            "name": "hermes-brain",
            "kind": "mcp",
            "endpoint_url": "http://127.0.0.1:9/mcp",
            "token": "sk-brain-token",
            "config": {"headers": {"X-Extra": "1"}},
        },
    )
    # Lazy connection: adding must succeed WITHOUT the server being reachable.
    assert r.status_code == 200, r.text
    platform = client.app.state.platform
    assert "hermes-brain" in platform.ltm.sources()
    # The token landed in the vault under the generated name, not on the record.
    assert platform.secrets.get("ltm_hermes_brain_mcp") == "sk-brain-token"
    listed = client.get("/ltm/sources").json()
    rec = next(s for s in listed["sources"] if s["name"] == "hermes-brain")
    assert rec["kind"] == "mcp"
    assert "sk-brain-token" not in json.dumps(rec)