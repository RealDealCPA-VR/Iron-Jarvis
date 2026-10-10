"""v1.329.0 (calm chat wave 5, S4): "Fetch available models" for an
Anthropic-compatible server.

``POST /connections/endpoints/models {base_url, api_key?, protocol:
"anthropic"}`` asks the server the Anthropic Models API way: ``GET
<base>/v1/models`` with ``x-api-key`` + ``anthropic-version``, paged by
``after_id`` while ``has_more``. Pinned against a REAL local HTTP server (no
stubbed transport, no network): the list and its display names, paging and
its cap, a refused key, the bound, a page that is not JSON, and that the key
rides one header to that server only (never Authorization, never the
response, never a log, never a redirect).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import connections as conn_routes

PATH = "/connections/endpoints/models"
KEY = "sk-ant-test-SECRET-s4-0123456789"


class _Server:
    """A tiny Anthropic-style model server. ``pages[after_id] = (status,
    body, delay_s)`` answers ``GET /v1/models`` ("" = the first page); any
    other path is a 404. Every request's path, query and auth headers are
    recorded."""

    def __init__(self) -> None:
        self.pages: dict[str, tuple[int, object, float]] = {}
        self.other: dict[str, tuple[int, object, float, dict[str, str]]] = {}
        self.seen: list[dict[str, object]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a, **k):
                return

            def do_GET(self):  # noqa: N802 — http.server's name
                parts = urlsplit(self.path)
                query = {k: v[0] for k, v in parse_qs(parts.query).items()}
                outer.seen.append(
                    {
                        "path": parts.path,
                        "query": query,
                        "x-api-key": self.headers.get("x-api-key", ""),
                        "authorization": self.headers.get("Authorization", ""),
                        "version": self.headers.get("anthropic-version", ""),
                    }
                )
                extra: dict[str, str] = {}
                if parts.path.endswith("/v1/models") and parts.path in outer.models_paths:
                    status, body, delay = outer.pages.get(
                        query.get("after_id", ""), (404, {"error": "no page"}, 0.0)
                    )
                else:
                    status, body, delay, extra = outer.other.get(
                        parts.path, (404, {"error": "not found"}, 0.0, {})
                    )
                if delay:
                    time.sleep(delay)
                raw = body if isinstance(body, (bytes, bytearray)) else json.dumps(body).encode()
                try:
                    self.send_response(status)
                    ctype = "text/html" if isinstance(body, (bytes, bytearray)) else "application/json"
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(raw)))
                    for k, v in extra.items():
                        self.send_header(k, v)
                    self.end_headers()
                    self.wfile.write(raw)
                except OSError:
                    pass  # the probe gave up first (the timeout case)

        self.models_paths = {"/v1/models"}
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def _page(ids, *, has_more=False, names=None, last_id=None):
    names = names or {}
    data = [
        {"type": "model", "id": i, "display_name": names.get(i, i), "created_at": "2026-01-01T00:00:00Z"}
        for i in ids
    ]
    return {
        "data": data,
        "has_more": has_more,
        "first_id": ids[0] if ids else None,
        "last_id": (last_id if last_id is not None else (ids[-1] if ids else None)),
    }


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    return TestClient(create_app(str(tmp_path_factory.mktemp("s4home"))))


@pytest.fixture
def server():
    s = _Server()
    yield s
    s.close()


def _probe(client, **body):
    body.setdefault("protocol", "anthropic")
    return client.post(PATH, json=body)


# --------------------------------------------------------------------------- #
#  The list comes back, the Anthropic way
# --------------------------------------------------------------------------- #


def test_lists_models_with_display_names_and_sends_the_key_as_x_api_key(client, server):
    server.pages[""] = (
        200,
        _page(
            ["claude-opus-9", "claude-haiku-9", "claude-opus-9"],
            names={"claude-opus-9": "Claude Opus 9", "claude-haiku-9": "Claude Haiku 9"},
        ),
        0.0,
    )
    r = _probe(client, base_url=server.base, api_key=KEY)
    assert r.status_code == 200
    data = r.json()
    assert data["models"] == ["claude-opus-9", "claude-haiku-9"]  # order kept, dupes dropped
    assert data["labels"] == {"claude-opus-9": "Claude Opus 9", "claude-haiku-9": "Claude Haiku 9"}
    assert data["protocol"] == "anthropic"
    assert data["partial"] is False
    assert data["error"] is None and data["reason"] is None
    [hit] = server.seen
    assert hit["path"] == "/v1/models"
    assert hit["x-api-key"] == KEY
    assert hit["version"] == "2023-06-01"
    assert hit["authorization"] == ""  # one header carries the key, not two
    assert hit["query"] == {"limit": "100"}
    assert KEY not in r.text


def test_a_display_name_equal_to_the_id_is_not_repeated(client, server):
    server.pages[""] = (200, _page(["m1", "m2"], names={"m2": "Model Two"}), 0.0)
    data = _probe(client, base_url=server.base).json()
    assert data["models"] == ["m1", "m2"]
    assert data["labels"] == {"m2": "Model Two"}


def test_no_key_sends_no_key_header(client, server):
    server.pages[""] = (200, _page(["local-claude"]), 0.0)
    data = _probe(client, base_url=server.base).json()
    assert data["models"] == ["local-claude"]
    assert server.seen[0]["x-api-key"] == ""
    assert server.seen[0]["version"] == "2023-06-01"


@pytest.mark.parametrize(
    ("typed", "asked"),
    [
        ("{base}", "/v1/models"),
        ("{base}/", "/v1/models"),
        ("{base}/v1", "/v1/models"),
        ("{base}/v1/messages", "/v1/models"),
        ("{base}/v1/models", "/v1/models"),
        ("{base}/anthropic", "/anthropic/v1/models"),
    ],
)
def test_the_models_address_follows_the_base(client, server, typed, asked):
    server.models_paths = {asked}
    server.pages[""] = (200, _page(["m1"]), 0.0)
    data = _probe(client, base_url=typed.format(base=server.base)).json()
    assert data["models"] == ["m1"]
    assert [s["path"] for s in server.seen] == [asked]


# --------------------------------------------------------------------------- #
#  Paging
# --------------------------------------------------------------------------- #


def test_pages_are_followed_by_after_id_until_has_more_is_false(client, server):
    server.pages[""] = (200, _page(["a1", "a2"], has_more=True), 0.0)
    server.pages["a2"] = (200, _page(["b1", "b2"], has_more=True), 0.0)
    server.pages["b2"] = (200, _page(["c1"], has_more=False), 0.0)
    data = _probe(client, base_url=server.base, api_key=KEY).json()
    assert data["models"] == ["a1", "a2", "b1", "b2", "c1"]
    assert data["partial"] is False
    assert [s["query"].get("after_id") for s in server.seen] == [None, "a2", "b2"]
    assert all(s["x-api-key"] == KEY for s in server.seen)


def test_paging_stops_at_the_page_cap_and_says_the_list_is_partial(client, server, monkeypatch):
    monkeypatch.setattr(conn_routes, "_ANTHROPIC_MAX_PAGES", 3)
    # An endless server: every page says there is more.
    for n in range(10):
        after = "" if n == 0 else f"p{n - 1}"
        server.pages[after] = (200, _page([f"p{n}"], has_more=True), 0.0)
    data = _probe(client, base_url=server.base).json()
    assert data["models"] == ["p0", "p1", "p2"]
    assert len(server.seen) == 3
    assert data["partial"] is True


def test_a_server_that_repeats_its_cursor_is_not_followed_forever(client, server):
    server.pages[""] = (200, _page(["a1"], has_more=True, last_id="a1"), 0.0)
    server.pages["a1"] = (200, _page(["a1"], has_more=True, last_id="a1"), 0.0)
    data = _probe(client, base_url=server.base).json()
    assert data["models"] == ["a1"]
    assert data["partial"] is True
    assert len(server.seen) == 2


def test_a_later_page_that_fails_keeps_the_first_and_says_partial(client, server):
    server.pages[""] = (200, _page(["a1", "a2"], has_more=True), 0.0)
    server.pages["a2"] = (500, {"error": "boom"}, 0.0)
    data = _probe(client, base_url=server.base).json()
    assert data["models"] == ["a1", "a2"]
    assert data["partial"] is True
    assert data["error"] is None


def test_the_id_ceiling_is_kept_and_said(client, server, monkeypatch):
    monkeypatch.setattr(conn_routes, "_ENDPOINT_PROBE_MAX_MODELS", 3)
    server.pages[""] = (200, _page(["a", "b", "c", "d"]), 0.0)
    data = _probe(client, base_url=server.base).json()
    assert data["models"] == ["a", "b", "c"]
    assert data["partial"] is True


# --------------------------------------------------------------------------- #
#  Plain-words failures
# --------------------------------------------------------------------------- #


def test_a_refused_key_is_said_plainly_and_ends_the_probe(client, server):
    server.pages[""] = (
        401,
        {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}},
        0.0,
    )
    r = _probe(client, base_url=server.base, api_key=KEY)
    data = r.json()
    assert data["models"] == []
    assert data["reason"] == "refused_key"
    assert data["error"] == "The server refused this key. Check the key and try again."
    assert len(server.seen) == 1
    assert KEY not in r.text
    assert "invalid x-api-key" not in r.text  # the server's words never leak through


def test_no_key_on_a_guarded_server_asks_for_one(client, server):
    server.pages[""] = (401, {"type": "error"}, 0.0)
    data = _probe(client, base_url=server.base).json()
    assert data["reason"] == "needs_key"
    assert "asks for a key" in data["error"]


def test_the_probe_is_bounded(client, server, monkeypatch):
    monkeypatch.setattr(conn_routes, "_ENDPOINT_PROBE_TIMEOUT_S", 0.6)
    server.pages[""] = (200, _page(["late"]), 3.0)
    t0 = time.monotonic()
    data = _probe(client, base_url=server.base).json()
    took = time.monotonic() - t0
    assert data["reason"] == "timeout"
    assert data["models"] == []
    assert "did not answer within 1 second." in data["error"]
    assert took < 2.5, f"the probe waited {took:.2f}s on a 0.6s bound"


def test_a_page_that_is_not_json_is_not_a_model_server(client, server):
    server.pages[""] = (200, b"<html><body>Welcome</body></html>", 0.0)
    data = _probe(client, base_url=server.base).json()
    assert data["models"] == []
    assert data["reason"] == "not_model_server"
    assert "does not look like a model server" in data["error"]


def test_json_that_is_not_a_listing_is_not_a_model_server(client, server):
    server.pages[""] = (200, {"hello": "world"}, 0.0)
    data = _probe(client, base_url=server.base).json()
    assert data["reason"] == "not_model_server"


def test_an_empty_listing_says_no_models(client, server):
    server.pages[""] = (200, _page([]), 0.0)
    data = _probe(client, base_url=server.base).json()
    assert data["reason"] == "no_models"


def test_a_server_error_is_said(client, server):
    server.pages[""] = (503, {"error": "overloaded"}, 0.0)
    data = _probe(client, base_url=server.base).json()
    assert data["reason"] == "server_error"


def test_a_redirect_is_not_followed_so_the_key_stays_put(client, server):
    server.models_paths = set()
    server.other["/v1/models"] = (302, {}, 0.0, {"Location": "/elsewhere"})
    server.other["/elsewhere"] = (200, _page(["stolen"]), 0.0, {})
    data = _probe(client, base_url=server.base, api_key=KEY).json()
    assert [s["path"] for s in server.seen] == ["/v1/models"]
    assert data["models"] == []


def test_the_address_and_key_rules_are_the_openai_ones(client, server):
    r = _probe(client, base_url="ftp://box/v1", api_key=KEY)
    assert r.status_code == 400
    assert KEY not in r.text
    r = _probe(client, base_url=server.base, api_key=KEY + "\nX-Evil: 1")
    assert r.status_code == 400
    assert KEY not in r.text
    assert server.seen == []


def test_the_key_never_reaches_a_log(client, server, caplog):
    server.pages[""] = (200, _page(["m"]), 0.0)
    with caplog.at_level(logging.DEBUG):
        _probe(client, base_url=server.base, api_key=KEY)
        server.pages[""] = (401, {}, 0.0)
        _probe(client, base_url=server.base, api_key=KEY)
    assert KEY not in caplog.text
    assert any(s["x-api-key"] == KEY for s in server.seen)  # it DID reach the server


def test_the_openai_way_is_unchanged_by_the_protocol_field(client, server):
    # "openai" never sends the Anthropic headers, and its answer keeps its
    # v1.328.0 shape (no protocol / labels / partial keys).
    server.models_paths = set()
    server.other["/v1/models"] = (200, {"data": [{"id": "gpt-local"}]}, 0.0, {})
    data = _probe(client, base_url=server.base, api_key=KEY, protocol="openai").json()
    assert data == {"models": ["gpt-local"], "error": None, "reason": None}
    assert server.seen[0]["authorization"] == f"Bearer {KEY}"
    assert server.seen[0]["x-api-key"] == ""
    assert server.seen[0]["version"] == ""
