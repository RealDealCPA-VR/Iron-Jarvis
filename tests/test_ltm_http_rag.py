"""Generic offsite RAG HTTP connector — fully offline via httpx.MockTransport.

Every test drives a real ``httpx.AsyncClient`` whose transport is a mock handler
returning a chosen JSON shape/status. No socket is ever opened.
"""

from __future__ import annotations

import json as jsonlib

import httpx
import pytest

from iron_jarvis.ltm.http_rag import HttpRagConfig, HttpRagConnector

ENDPOINT = "https://rag.example.com/search"
INGEST = "https://rag.example.com/ingest"


def _client(handler):
    """An async httpx client backed by an in-memory mock transport."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _json_handler(payload, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return handler


def _connector(handler, *, token_resolver=None, config=None, name="myrag"):
    return HttpRagConnector(
        name,
        ENDPOINT,
        _client(handler),
        token_resolver=token_resolver,
        config=config or HttpRagConfig(),
    )


# --- response shapes ---------------------------------------------------------


def test_results_shape():
    payload = {
        "results": [
            {"title": "Alpha", "text": "alpha body", "url": "http://a", "score": 0.9},
            {"title": "Beta", "text": "beta body", "url": "http://b", "score": 0.5},
        ]
    }
    hits = _connector(_json_handler(payload)).search("q")
    assert hits == [
        {"title": "Alpha", "snippet": "alpha body", "ref": "http://a", "source": "myrag"},
        {"title": "Beta", "snippet": "beta body", "ref": "http://b", "source": "myrag"},
    ]


def test_documents_shape():
    payload = {"documents": [{"title": "Doc", "text": "doc body", "url": "http://d"}]}
    hits = _connector(_json_handler(payload)).search("q")
    assert hits[0] == {
        "title": "Doc",
        "snippet": "doc body",
        "ref": "http://d",
        "source": "myrag",
    }


def test_data_shape():
    payload = {"data": [{"title": "D1", "text": "d1 body", "url": "http://d1"}]}
    hits = _connector(_json_handler(payload)).search("q")
    assert hits[0]["title"] == "D1"
    assert hits[0]["snippet"] == "d1 body"
    assert hits[0]["ref"] == "http://d1"


def test_pinecone_matches_shape():
    # No top-level title/text; they live in metadata. ref falls back to id.
    payload = {
        "matches": [
            {"id": "vec-1", "score": 0.8, "metadata": {"text": "meta text", "title": "MT"}},
        ]
    }
    hits = _connector(_json_handler(payload)).search("q")
    assert hits[0] == {
        "title": "MT",
        "snippet": "meta text",
        "ref": "vec-1",
        "source": "myrag",
    }


def test_bare_array_shape():
    payload = [{"title": "Bare", "text": "bare body", "url": "http://x"}]
    hits = _connector(_json_handler(payload)).search("q")
    assert hits[0]["title"] == "Bare"
    assert hits[0]["snippet"] == "bare body"


def test_bare_array_of_strings():
    payload = ["just a plain string chunk"]
    hits = _connector(_json_handler(payload)).search("q")
    assert hits[0]["snippet"] == "just a plain string chunk"
    assert hits[0]["source"] == "myrag"


def test_custom_field_map_and_results_path():
    payload = {"response": {"docs": [{"heading": "H", "body": "B", "link": "L"}]}}
    config = HttpRagConfig(
        results_path="response.docs",
        title_field="heading",
        text_field="body",
        ref_field="link",
    )
    hits = _connector(_json_handler(payload), config=config).search("q")
    assert hits[0] == {"title": "H", "snippet": "B", "ref": "L", "source": "myrag"}


# --- auth --------------------------------------------------------------------


def test_bearer_auth_header_applied():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"results": []})

    _connector(handler, token_resolver=lambda: "sekret").search("q")
    assert seen["auth"] == "Bearer sekret"


def test_custom_header_auth_scheme():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get("X-API-Key")
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"results": []})

    config = HttpRagConfig(auth_scheme="header", auth_header="X-API-Key")
    _connector(handler, token_resolver=lambda: "abc123", config=config).search("q")
    assert seen["key"] == "abc123"
    assert seen["auth"] is None  # not a bearer header


def test_no_auth_header_when_token_absent():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"results": []})

    # token_resolver returns None (unauthenticated endpoint) -> no header, no crash
    _connector(handler, token_resolver=lambda: None).search("q")
    assert seen["auth"] is None


# --- request wiring ----------------------------------------------------------


def test_query_and_k_sent_in_body():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = jsonlib.loads(request.content.decode())
        seen["method"] = request.method
        return httpx.Response(200, json={"results": []})

    _connector(handler).search("hello world", k=3)
    assert seen["method"] == "POST"
    assert seen["body"] == {"query": "hello world", "k": 3}


def test_get_method_sends_query_params():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"results": []})

    config = HttpRagConfig(method="GET", query_field="q", top_k_field="limit")
    _connector(handler, config=config).search("hi", k=7)
    assert seen["method"] == "GET"
    assert seen["params"] == {"q": "hi", "limit": "7"}


def test_k_respected():
    payload = {"results": [{"text": f"chunk {i}", "title": f"T{i}"} for i in range(10)]}
    hits = _connector(_json_handler(payload)).search("q", k=4)
    assert len(hits) == 4
    assert hits[0]["title"] == "T0"


# --- failure tolerance -------------------------------------------------------


def test_non_200_returns_empty_no_raise():
    hits = _connector(_json_handler({"error": "boom"}, status=500)).search("q")
    assert hits == []


def test_malformed_json_returns_empty_no_raise():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json at all {{{")

    assert _connector(handler).search("q") == []


def test_unexpected_shape_returns_empty():
    # A dict with no recognised results array and no configured path.
    hits = _connector(_json_handler({"totally": "unexpected"})).search("q")
    assert hits == []


def test_request_exception_returns_empty():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    assert _connector(handler).search("q") == []


# --- append ------------------------------------------------------------------


def test_read_only_append_raises_clear_message():
    conn = _connector(_json_handler({"results": []}))
    with pytest.raises(RuntimeError) as excinfo:
        conn.append("Title", "Content")
    msg = str(excinfo.value).lower()
    assert "read-only" in msg and "myrag" in msg


def test_configured_ingest_url_posts_note():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["body"] = jsonlib.loads(request.content.decode())
        return httpx.Response(200, json={"id": "note-42"})

    config = HttpRagConfig(ingest_url=INGEST)
    conn = _connector(handler, config=config)
    ref = conn.append("My Note", "the body")
    assert seen["url"] == INGEST
    assert seen["method"] == "POST"
    assert seen["body"] == {"title": "My Note", "content": "the body"}
    assert ref == "note-42"


def test_ingest_non_200_raises():
    config = HttpRagConfig(ingest_url=INGEST)
    conn = _connector(_json_handler({"error": "nope"}, status=422), config=config)
    with pytest.raises(RuntimeError):
        conn.append("t", "c")


# --- client injection variants ----------------------------------------------


def test_sync_client_also_works():
    # The connector tolerates a sync httpx.Client too (awaitable detection).
    payload = {"results": [{"title": "S", "text": "sync body", "url": "http://s"}]}
    client = httpx.Client(transport=httpx.MockTransport(_json_handler(payload)))
    conn = HttpRagConnector("sync", ENDPOINT, client)
    hits = conn.search("q")
    assert hits[0]["snippet"] == "sync body"


def test_client_factory_is_invoked_and_closed():
    payload = {"results": [{"title": "F", "text": "factory body", "url": "http://f"}]}
    created = []

    def factory():
        c = httpx.Client(transport=httpx.MockTransport(_json_handler(payload)))
        created.append(c)
        return c

    conn = HttpRagConnector("fac", ENDPOINT, factory)
    hits = conn.search("q")
    assert hits[0]["snippet"] == "factory body"
    assert len(created) == 1
    assert created[0].is_closed  # factory-built client closed after use


def test_config_from_dict_ignores_unknown_keys():
    config = HttpRagConfig.from_dict(
        {"method": "GET", "title_field": "name", "bogus": "ignored", "timeout": 5.0}
    )
    assert config.method == "GET"
    assert config.title_field == "name"
    assert config.timeout == 5.0
