"""v1.88.0: commit a chat to memory + connector visibility/toggles.

* POST /chat/threads/{id}/remember — distill (real adapter) or an HONEST
  verbatim excerpt (offline / mock default); lands in a registered LTM source.
* GET /connectors — the gallery also lists the user's OWN MCP servers and
  custom memory sources (incl. MCP-served brains), with test + disconnect.
* POST /chat connectors=[...] — a toggled MCP connector arms its whole tool
  group (additive to the 6 individually-armed tools); a toggled memory
  connector grounds the turn with that store's top hits.
* Thread setup persists the toggles.

Offline throughout — real adapters are stand-ins injected via the provider
manager; MCP tools are fakes registered straight on the registry.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.tools.base import Tool, ToolResult


def _client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def _seed(client, msgs=None) -> str:
    msgs = msgs or [
        {"role": "user", "content": "What is the S-corp deadline?"},
        {"role": "assistant", "content": "March 16 (the 15th is a Sunday)."},
    ]
    return client.put("/chat/threads/new", json={"messages": msgs}).json()["id"]


class _FakeAdapter:
    """A REAL-adapter stand-in (deliberately not MockLLMAdapter)."""

    provider = "anthropic"
    model = "claude-opus-4-8"

    def __init__(self, text="- Deadline: March 16 for the S-corp filing."):
        self._text = text
        self.calls: list[dict] = []

    async def complete(self, *, system, messages, tools):
        self.calls.append({"system": system, "messages": messages, "tools": tools})
        return LLMResponse(text=self._text)


class _FakeMcpTool(Tool):
    """A trivial in-memory MCP tool for connector-toggle tests."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.description = f"fake {name}"
        self.input_schema = {"type": "object", "properties": {}}
        self.permission_key = "mcp_call"

    async def execute(self, args, ctx) -> ToolResult:  # pragma: no cover
        return ToolResult(ok=True, output="ok")


# --- remember: commit a chat to memory ---------------------------------------


def test_remember_offline_stores_honest_verbatim_excerpt(tmp_path):
    client = _client(tmp_path)
    tid = _seed(client)
    r = client.post(f"/chat/threads/{tid}/remember", json={})
    assert r.status_code == 200
    out = r.json()
    assert out["ok"] is True and out["distilled"] is False
    assert out["source"] == "brain"  # the always-on default brain
    assert "verbatim excerpt" in out["note"]
    # The memory is REAL — the brain finds the conversation content.
    hits = client.app.state.platform.ltm.search("S-corp deadline", source="brain")
    blob = " ".join(str(h) for h in hits)
    assert "S-corp" in blob


def test_remember_distills_with_real_adapter(tmp_path):
    client = _client(tmp_path)
    tid = _seed(client)
    fake = _FakeAdapter()
    client.app.state.platform.providers.get = lambda p, m=None: fake
    r = client.post(
        f"/chat/threads/{tid}/remember", json={"provider": "anthropic"}
    )
    assert r.status_code == 200
    out = r.json()
    assert out["distilled"] is True and out["provider"] == "anthropic"
    assert "note" not in out
    # The model saw the ACTUAL transcript, and its digest is what was stored.
    sent = fake.calls[0]["messages"][0].content
    assert "March 16 (the 15th is a Sunday)." in sent
    hits = client.app.state.platform.ltm.search("S-corp filing", source="brain")
    assert any("March 16" in str(h) for h in hits)


def test_remember_mock_default_falls_back_to_verbatim_not_fabrication(tmp_path):
    """Distill on the mock default with NO real provider: the stored memory is
    the verbatim excerpt — never a mock-fabricated summary."""
    client = _client(tmp_path)
    tid = _seed(client)
    r = client.post(f"/chat/threads/{tid}/remember", json={"mode": "distill"})
    assert r.status_code == 200
    out = r.json()
    assert out["distilled"] is False
    assert "verbatim excerpt" in out["note"]


def test_remember_full_mode_and_custom_source(tmp_path):
    client = _client(tmp_path)
    tid = _seed(client)
    vault = tmp_path / "vault"
    vault.mkdir()
    client.post(
        "/ltm/sources",
        json={"name": "team_vault", "kind": "markdown", "path": str(vault)},
    )
    r = client.post(
        f"/chat/threads/{tid}/remember", json={"mode": "full", "source": "team_vault"}
    )
    assert r.status_code == 200
    out = r.json()
    assert out["source"] == "team_vault" and out["distilled"] is False
    hits = client.app.state.platform.ltm.search("S-corp", source="team_vault")
    assert any("S-corp" in str(h) for h in hits)


def test_remember_validation(tmp_path):
    client = _client(tmp_path)
    tid = _seed(client)
    assert client.post(
        f"/chat/threads/{tid}/remember", json={"mode": "summarize"}
    ).status_code == 400
    assert client.post(
        f"/chat/threads/{tid}/remember", json={"source": "ghost"}
    ).status_code == 400
    assert client.post("/chat/threads/nope/remember", json={}).status_code == 404
    empty = client.put("/chat/threads/new", json={"messages": []}).json()["id"]
    assert client.post(f"/chat/threads/{empty}/remember", json={}).status_code == 400


# --- /connectors: dynamic entries ---------------------------------------------


def test_connectors_list_includes_user_mcp_server(tmp_path):
    client = _client(tmp_path)
    platform = client.app.state.platform
    platform.config.mcp_servers = [
        {"name": "my_notes", "command": "definitely-not-installed", "args": []}
    ]
    platform.registry.register(_FakeMcpTool("mcp__my_notes__search"), mcp=True)
    out = client.get("/connectors").json()
    assert "Custom" in out["categories"] and "Memory" in out["categories"]
    entry = next(c for c in out["connectors"] if c["id"] == "my_notes")
    assert entry["category"] == "Custom" and entry["connected"] is True
    assert entry["tools_loaded"] == 1 and entry["tool_names"] == ["search"]
    assert entry["source"] == "user"


def test_connectors_list_includes_memory_sources_and_disconnect(tmp_path):
    client = _client(tmp_path)
    vault = tmp_path / "vault"
    vault.mkdir()
    client.post(
        "/ltm/sources",
        json={"name": "obsidian_brain", "kind": "markdown", "path": str(vault)},
    )
    out = client.get("/connectors").json()
    entry = next(c for c in out["connectors"] if c["id"] == "obsidian_brain")
    assert entry["category"] == "Memory" and entry["connect_via"] == "memory"
    assert entry["connected"] is True and entry["kind"] == "markdown"
    # Test works for a live memory source.
    t = client.post("/connectors/obsidian_brain/test").json()
    assert t["ok"] is True and t["kind"] == "memory"
    # Disconnect removes the stored row AND deregisters it live.
    r = client.delete("/connectors/obsidian_brain")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert client.app.state.platform.ltm.get("obsidian_brain") is None
    ids = [c["id"] for c in client.get("/connectors").json()["connectors"]]
    assert "obsidian_brain" not in ids


def test_connectors_disconnect_user_mcp_server(tmp_path):
    client = _client(tmp_path)
    platform = client.app.state.platform
    platform.config.mcp_servers = [
        {"name": "my_notes", "command": "definitely-not-installed", "args": []}
    ]
    platform.registry.register(_FakeMcpTool("mcp__my_notes__search"), mcp=True)
    r = client.delete("/connectors/my_notes")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert platform.config.mcp_servers == []
    assert platform.registry.mcp_names("my_notes") == []
    # A truly unknown id still 404s.
    assert client.delete("/connectors/ghost").status_code == 404
    assert client.post("/connectors/ghost/test").status_code == 404


# --- chat connector toggles ---------------------------------------------------


def _spy_adapter(client, captured):
    platform = client.app.state.platform
    real_get = platform.providers.get

    def spy(p, m=None):
        a = real_get(p, m)
        rc = a.complete

        async def c(*, system, messages, tools):
            captured["system"] = system
            captured["tools"] = tools
            return await rc(system=system, messages=messages, tools=tools)

        a.complete = c
        return a

    platform.providers.get = spy


def test_chat_connector_toggle_arms_mcp_tool_group(tmp_path):
    client = _client(tmp_path)
    platform = client.app.state.platform
    platform.registry.register(_FakeMcpTool("mcp__gmail__send"), mcp=True)
    platform.registry.register(_FakeMcpTool("mcp__gmail__list"), mcp=True)
    captured: dict = {}
    _spy_adapter(client, captured)
    r = client.post(
        "/chat",
        json={
            "messages": [{"role": "user", "content": "check my inbox"}],
            "connectors": ["gmail"],
        },
    )
    assert r.status_code == 200
    names = {t.get("name") for t in captured["tools"]}
    assert names == {"mcp__gmail__send", "mcp__gmail__list"}
    assert "Connector tools the user toggled on" in captured["system"]


def test_chat_connector_tools_do_not_eat_the_armed_cap(tmp_path):
    """6 individually-armed tools + a connector group: BOTH reach the model."""
    client = _client(tmp_path)
    platform = client.app.state.platform
    platform.registry.register(_FakeMcpTool("mcp__gh__issues"), mcp=True)
    captured: dict = {}
    _spy_adapter(client, captured)
    armed = ["read_file", "write_file", "list_folder", "file_search",
             "web_search", "read_document"]
    r = client.post(
        "/chat",
        json={
            "messages": [{"role": "user", "content": "work"}],
            "tools": armed,
            "connectors": ["gh"],
        },
    )
    assert r.status_code == 200
    names = {t.get("name") for t in captured["tools"]}
    assert "mcp__gh__issues" in names
    assert len(names) == 7  # the full individual cap PLUS the connector tool


def test_chat_memory_connector_grounds_the_turn(tmp_path):
    client = _client(tmp_path)
    vault = tmp_path / "vault"
    vault.mkdir()
    client.post(
        "/ltm/sources",
        json={"name": "facts_brain", "kind": "markdown", "path": str(vault)},
    )
    platform = client.app.state.platform
    platform.ltm.append("Vault code", "The vault code is 4242.", source="facts_brain")
    captured: dict = {}
    _spy_adapter(client, captured)
    r = client.post(
        "/chat",
        json={
            "messages": [{"role": "user", "content": "what is the vault code?"}],
            "connectors": ["facts_brain"],
        },
    )
    assert r.status_code == 200
    assert "From your connected memory" in captured["system"]
    assert "4242" in captured["system"]
    assert "[facts_brain]" in captured["system"]


def test_chat_unknown_connector_is_skipped_not_an_error(tmp_path):
    client = _client(tmp_path)
    captured: dict = {}
    _spy_adapter(client, captured)
    r = client.post(
        "/chat",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "connectors": ["stale_ghost"],
        },
    )
    assert r.status_code == 200
    assert "From your connected memory" not in captured.get("system", "")


# --- thread setup persists the toggles ----------------------------------------


def test_thread_setup_persists_connectors(tmp_path):
    client = _client(tmp_path)
    tid = client.put(
        "/chat/threads/new",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "setup": {"tools": ["read_file"], "connectors": ["gmail", "facts_brain"],
                      "documents": [r"C:\proj\letter.docx", r"C:\proj\book.xlsx"]},
        },
    ).json()["id"]
    got = client.get(f"/chat/threads/{tid}").json()
    assert got["setup"]["connectors"] == ["gmail", "facts_brain"]
    assert got["setup"]["tools"] == ["read_file"]
    # v1.91.0: generated-document paths persist with the thread (the preview
    # chips survive leaving the page + daemon restarts until dismissed).
    assert got["setup"]["documents"] == [r"C:\proj\letter.docx", r"C:\proj\book.xlsx"]
    # The cap keeps the NEWEST documents.
    client.put(
        f"/chat/threads/{tid}",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "setup": {"documents": [f"C:\\d\\{i}.docx" for i in range(12)]},
        },
    )
    docs = client.get(f"/chat/threads/{tid}").json()["setup"]["documents"]
    assert len(docs) == 8 and docs[-1] == "C:\\d\\11.docx" and docs[0] == "C:\\d\\4.docx"
    # Mistyped payloads are dropped, not stored.
    client.put(
        f"/chat/threads/{tid}",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "setup": {"connectors": "gmail"},
        },
    )
    got = client.get(f"/chat/threads/{tid}").json()
    assert "connectors" not in got["setup"]
