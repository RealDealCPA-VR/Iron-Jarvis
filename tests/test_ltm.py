"""Long-term memory tests (§21 external knowledge stores). Fully offline.

Local connectors (Obsidian / markdown brain) hit a real temp filesystem; the
Notion connector is driven by an INJECTED fake HTTP client so no socket opens.
"""

from __future__ import annotations

import pytest

from iron_jarvis.core.db import init_db, make_engine
from iron_jarvis.core.events import EventBus
from iron_jarvis.ltm.brain import MarkdownBrainConnector
from iron_jarvis.ltm.manager import LongTermMemory
from iron_jarvis.ltm.notion import NotionConnector
from iron_jarvis.ltm.obsidian import ObsidianConnector
from iron_jarvis.ltm.sources import CustomSourceStore, load_custom_sources
from iron_jarvis.ltm.tools import ltm_tools
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.permissions import PermissionEngine
from iron_jarvis.tools.registry import ToolRegistry


# --------------------------------------------------------------------------
# Fixtures + fakes
# --------------------------------------------------------------------------
def _seed(directory, name, text):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(text, encoding="utf-8")


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class FakeHTTP:
    """Records calls; routes /query -> search payload, else -> append payload."""

    def __init__(self, search_payload=None, append_payload=None) -> None:
        self.search_payload = search_payload or {"results": []}
        self.append_payload = append_payload or {"id": "page-new"}
        self.calls: list[dict] = []

    def post(self, url, json, headers):
        self.calls.append({"method": "POST", "url": url, "json": json, "headers": headers})
        if url.endswith("/query"):
            return FakeResponse(self.search_payload)
        return FakeResponse(self.append_payload)

    def get(self, url, headers=None):  # pragma: no cover - present for shape parity
        self.calls.append({"method": "GET", "url": url, "headers": headers})
        return FakeResponse({})


def _notion_search_payload() -> dict:
    return {
        "results": [
            {
                "id": "abc123",
                "url": "https://notion.so/abc123",
                "properties": {
                    "Name": {
                        "type": "title",
                        "title": [{"plain_text": "Tax deductions for 2026"}],
                    }
                },
            }
        ]
    }


@pytest.fixture
def engine(tmp_path):
    e = make_engine(str(tmp_path / "t.db"))
    init_db(e)
    return e


@pytest.fixture
def ctx(engine, tmp_path):
    return ToolContext(
        workspace=tmp_path,
        session_id="s1",
        agent_run_id="r1",
        config=None,
        event_bus=EventBus(),
        engine=engine,
    )


# --------------------------------------------------------------------------
# Local connectors
# --------------------------------------------------------------------------
def test_obsidian_search_and_append_roundtrip(tmp_path):
    vault = tmp_path / "vault"
    _seed(vault, "pytest-notes.md", "python testing with pytest and fixtures")
    _seed(vault, "taxes.md", "tax deductions and write-offs for businesses")

    conn = ObsidianConnector(vault)

    hits = conn.search("python pytest")
    assert hits, "expected at least one obsidian hit"
    top = hits[0]
    assert top["source"] == "obsidian"
    assert top["title"] == "pytest-notes"
    assert "pytest" in top["snippet"].lower()
    assert top["ref"].endswith("pytest-notes.md")

    # append creates a new note that is then itself searchable
    ref = conn.append("Docker Layers", "docker containers and image layers explained")
    assert ref.endswith("docker-layers.md")

    found = conn.search("docker image layers")
    assert any(h["title"] == "docker-layers" for h in found)


def test_obsidian_search_is_recursive(tmp_path):
    vault = tmp_path / "vault"
    _seed(vault / "sub", "nested.md", "kubernetes orchestration notes")
    conn = ObsidianConnector(vault)
    hits = conn.search("kubernetes orchestration")
    assert hits and hits[0]["title"] == "nested"


def test_markdown_brain_search_and_append(tmp_path):
    brain_dir = tmp_path / "brain"
    _seed(brain_dir, "groceries.md", "milk eggs bread and coffee for the week")
    conn = MarkdownBrainConnector(brain_dir)

    hits = conn.search("coffee groceries")
    assert hits and hits[0]["source"] == "brain"
    assert hits[0]["title"] == "groceries"

    conn.append("Ideas", "a brilliant idea about long-term memory connectors")
    again = conn.search("long-term memory connectors")
    assert any(h["title"] == "ideas" for h in again)


def test_brain_append_appends_to_existing(tmp_path):
    conn = MarkdownBrainConnector(tmp_path / "brain")
    p1 = conn.append("Journal", "first entry")
    p2 = conn.append("Journal", "second entry")
    assert p1 == p2  # same slug file
    from pathlib import Path

    body = Path(p1).read_text(encoding="utf-8")
    assert "first entry" in body and "second entry" in body


def test_brain_no_match_returns_empty(tmp_path):
    _seed(tmp_path / "brain", "cats.md", "all about cats and kittens")
    conn = MarkdownBrainConnector(tmp_path / "brain")
    assert conn.search("quantum chromodynamics") == []


def test_obsidian_optional_embedder_used_for_semantic(tmp_path):
    from iron_jarvis.memory.embeddings import MockEmbedder

    vault = tmp_path / "vault"
    _seed(vault, "a.md", "python testing pytest fixtures")
    _seed(vault, "b.md", "gardening roses and tulips")
    conn = ObsidianConnector(vault, embedder=MockEmbedder())
    hits = conn.search("python testing")
    assert hits[0]["title"] == "a"


# --------------------------------------------------------------------------
# Notion connector (injected fake http)
# --------------------------------------------------------------------------
def test_notion_search_parses_results_and_posts_query():
    http = FakeHTTP(search_payload=_notion_search_payload())
    conn = NotionConnector("db-42", lambda: "secret-token", http)

    hits = conn.search("tax", k=3)

    assert hits == [
        {
            "title": "Tax deductions for 2026",
            "snippet": "Tax deductions for 2026",
            "ref": "https://notion.so/abc123",
            "source": "notion",
        }
    ]
    # assert the outgoing request URL + body + auth header
    call = http.calls[0]
    assert call["url"] == "https://api.notion.com/v1/databases/db-42/query"
    assert call["json"]["page_size"] == 3
    assert call["json"]["filter"] == {
        "property": "Name",
        "title": {"contains": "tax"},
    }
    assert call["headers"]["Authorization"] == "Bearer secret-token"
    assert call["headers"]["Notion-Version"] == "2022-06-28"


def test_notion_append_posts_correct_payload():
    http = FakeHTTP(append_payload={"id": "page-xyz"})
    conn = NotionConnector("db-42", lambda: "secret-token", http)

    ref = conn.append("Meeting notes", "we discussed the roadmap")

    assert ref == "page-xyz"
    call = http.calls[0]
    assert call["url"] == "https://api.notion.com/v1/pages"
    body = call["json"]
    assert body["parent"] == {"database_id": "db-42"}
    assert body["properties"]["Name"]["title"][0]["text"]["content"] == "Meeting notes"
    para = body["children"][0]["paragraph"]["rich_text"][0]["text"]["content"]
    assert para == "we discussed the roadmap"


def test_notion_missing_token_is_graceful():
    http = FakeHTTP()
    conn = NotionConnector("db-42", lambda: None, http)

    assert conn.search("anything") == []  # no crash, no network call
    assert http.calls == []
    with pytest.raises(RuntimeError):
        conn.append("title", "content")


# --------------------------------------------------------------------------
# Manager aggregation + routing
# --------------------------------------------------------------------------
def test_manager_routes_by_source_and_merges(tmp_path):
    vault = tmp_path / "vault"
    brain_dir = tmp_path / "brain"
    _seed(vault, "obs.md", "shared topic alpha lives in obsidian")
    _seed(brain_dir, "brn.md", "shared topic alpha lives in the brain")

    ltm = LongTermMemory()
    ltm.register(ObsidianConnector(vault))
    ltm.register(MarkdownBrainConnector(brain_dir))

    assert set(ltm.sources()) == {"obsidian", "brain"}

    # routed to a single source
    only_obs = ltm.search("alpha", source="obsidian")
    assert only_obs and all(h["source"] == "obsidian" for h in only_obs)

    # merged across all connectors -> both sources represented
    merged = ltm.search("alpha topic", k=5)
    sources = {h["source"] for h in merged}
    assert sources == {"obsidian", "brain"}

    # unknown source is a clear error
    with pytest.raises(ValueError):
        ltm.search("x", source="nope")


def test_manager_append_routes_and_defaults(tmp_path):
    brain_dir = tmp_path / "brain"
    ltm = LongTermMemory()
    ltm.register(MarkdownBrainConnector(brain_dir))
    assert ltm.default_source() == "brain"

    ref = ltm.append("Note", "content here", source="brain")
    assert ref.endswith("note.md")
    with pytest.raises(ValueError):
        ltm.append("t", "c", source="nope")


# --------------------------------------------------------------------------
# User-configurable custom memory sources (persisted)
# --------------------------------------------------------------------------
def test_custom_source_store_crud(engine, tmp_path):
    src_dir = tmp_path / "mybrain"
    src_dir.mkdir()
    store = CustomSourceStore(engine)

    rec = store.add("mybrain", "markdown", path=str(src_dir))
    assert rec.id.startswith("ltmsrc_")
    assert rec.kind == "markdown"
    assert rec.path == str(src_dir)

    assert [r.name for r in store.list()] == ["mybrain"]
    assert store.get("mybrain").path == str(src_dir)

    # Unknown kind is rejected.
    with pytest.raises(ValueError):
        store.add("bad", "sqlite")

    # Upsert in place (no duplicate).
    store.add("mybrain", "markdown", path=str(src_dir / "nested"))
    assert len(store.list()) == 1
    assert store.get("mybrain").path == str(src_dir / "nested")

    assert store.remove("mybrain") is True
    assert store.get("mybrain") is None
    assert store.remove("mybrain") is False


def test_load_custom_sources_registers_markdown_into_ltm(engine, tmp_path):
    src_dir = tmp_path / "kb"
    _seed(src_dir, "revenue.md", "the quarterly revenue target is two million")
    CustomSourceStore(engine).add("kb", "markdown", path=str(src_dir))

    ltm = LongTermMemory()
    load_custom_sources(
        ltm,
        engine,
        secret_resolver=lambda key: None,
        http_factory=lambda: FakeHTTP(),
    )

    assert "kb" in ltm.sources()
    hits = ltm.search("quarterly revenue", source="kb")
    assert hits and hits[0]["source"] == "kb"
    assert "revenue" in hits[0]["snippet"].lower()


def test_load_custom_sources_notion_uses_secret_and_injected_http(engine):
    CustomSourceStore(engine).add(
        "mynotion", "notion", database_id="db-77", token_secret="notion_token"
    )
    resolved: dict[str, str] = {}

    def resolver(key: str) -> str:
        resolved["key"] = key
        return "secret-abc"

    http = FakeHTTP(search_payload=_notion_search_payload())
    ltm = LongTermMemory()
    load_custom_sources(
        ltm, engine, secret_resolver=resolver, http_factory=lambda: http
    )

    assert "mynotion" in ltm.sources()
    hits = ltm.search("tax", source="mynotion")
    assert hits and hits[0]["source"] == "mynotion"
    # The token was resolved lazily from the configured secret key.
    assert resolved["key"] == "notion_token"
    assert http.calls[0]["headers"]["Authorization"] == "Bearer secret-abc"


# --------------------------------------------------------------------------
# Tools via the registry (permission-gated execution path)
# --------------------------------------------------------------------------
async def test_ltm_tools_via_registry(tmp_path, ctx):
    brain_dir = tmp_path / "brain"
    _seed(brain_dir, "seed.md", "the secret password is hunter2 for the demo")

    ltm = LongTermMemory()
    ltm.register(MarkdownBrainConnector(brain_dir))

    registry = ToolRegistry()
    for tool in ltm_tools(ltm):
        registry.register(tool)
    perms = PermissionEngine({"ltm_search": "allow", "ltm_append": "allow"})

    # search via registry
    res = await registry.invoke(
        "ltm_search", {"query": "secret password"}, ctx, perms
    )
    assert res.ok
    assert res.data["count"] >= 1
    assert res.data["results"][0]["title"] == "seed"

    # append via registry (defaults source -> brain), then it's searchable
    app = await registry.invoke(
        "ltm_append", {"title": "Fresh Note", "content": "a freshly minted idea"}, ctx, perms
    )
    assert app.ok and app.data["source"] == "brain"

    res2 = await registry.invoke(
        "ltm_search", {"query": "freshly minted idea"}, ctx, perms
    )
    assert any(h["title"] == "fresh-note" for h in res2.data["results"])
