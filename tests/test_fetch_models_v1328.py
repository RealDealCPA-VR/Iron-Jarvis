"""v1.328.0 (calm chat B6): "Fetch available models" for a custom endpoint.

``POST /connections/endpoints/models {base_url, api_key?, protocol}`` asks the
server the user typed for its own model list. Pinned here against a REAL local
HTTP server (no stubbed transport): the paths it tries, the plain-words
failures, the bound, the worker thread, and that the key goes to that server
in one header and nowhere else (not the response, not a log, not a redirect).
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import connections as conn_routes

PATH = "/connections/endpoints/models"
KEY = "sk-test-SECRET-b6-0123456789"


class _Server:
    """A tiny model server. ``routes[path] = (status, body, delay_s, headers)``;
    every request's path + Authorization header is recorded."""

    def __init__(self) -> None:
        self.routes: dict[str, tuple[int, object, float, dict[str, str]]] = {}
        self.seen: list[tuple[str, str]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a, **k):  # keep the test output quiet
                return

            def do_GET(self):  # noqa: N802 — http.server's name
                outer.seen.append((self.path, self.headers.get("Authorization", "")))
                status, body, delay, extra = outer.routes.get(
                    self.path, (404, {"error": "not found"}, 0.0, {})
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

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def paths(self) -> list[str]:
        return [p for p, _ in self.seen]

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    return TestClient(create_app(str(tmp_path_factory.mktemp("b6home"))))


@pytest.fixture
def server():
    s = _Server()
    yield s
    s.close()


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _probe(client, **body):
    return client.post(PATH, json=body)


# --------------------------------------------------------------------------- #
#  The list comes back
# --------------------------------------------------------------------------- #


def test_bare_host_lists_openai_models_and_sends_the_key_to_that_server_only(client, server):
    server.routes["/v1/models"] = (
        200,
        {"data": [{"id": "qwen3-coder"}, {"id": "llama3.2"}, {"id": "qwen3-coder"}, {"id": ""}]},
        0.0,
        {},
    )
    r = _probe(client, base_url=server.base, api_key=KEY)
    assert r.status_code == 200
    data = r.json()
    assert data["models"] == ["qwen3-coder", "llama3.2"]  # order kept, dupes + blanks dropped
    assert data["error"] is None
    assert server.seen == [("/v1/models", f"Bearer {KEY}")]
    assert KEY not in r.text


def test_a_v1_base_asks_v1_models_once(client, server):
    server.routes["/v1/models"] = (200, {"data": [{"id": "m1"}]}, 0.0, {})
    r = _probe(client, base_url=f"{server.base}/v1/")
    assert r.json()["models"] == ["m1"]
    assert server.paths() == ["/v1/models"]
    assert server.seen[0][1] == ""  # no key typed: no Authorization header


def test_a_pasted_chat_url_is_cut_back_to_its_base(client, server):
    server.routes["/v1/models"] = (200, {"data": [{"id": "m1"}]}, 0.0, {})
    r = _probe(client, base_url=f"{server.base}/v1/chat/completions")
    assert r.json()["models"] == ["m1"]
    assert server.paths() == ["/v1/models"]


def test_falls_back_to_base_models_when_v1_is_missing(client, server):
    server.routes["/models"] = (200, {"data": [{"id": "gw-model"}]}, 0.0, {})
    r = _probe(client, base_url=server.base)
    assert r.json()["models"] == ["gw-model"]
    assert server.paths() == ["/v1/models", "/models"]


def test_falls_back_to_ollama_tags(client, server):
    server.routes["/api/tags"] = (
        200,
        {"models": [{"name": "llama3:8b"}, {"name": "qwen2.5:7b"}]},
        0.0,
        {},
    )
    r = _probe(client, base_url=server.base)
    assert r.json()["models"] == ["llama3:8b", "qwen2.5:7b"]
    assert server.paths()[-1] == "/api/tags"


# --------------------------------------------------------------------------- #
#  Plain-words failures
# --------------------------------------------------------------------------- #


def test_refused_key_is_said_plainly_and_ends_the_probe(client, server):
    server.routes["/v1/models"] = (401, {"error": "bad key"}, 0.0, {})
    data = _probe(client, base_url=server.base, api_key=KEY).json()
    assert data["models"] == []
    assert data["reason"] == "refused_key"
    assert data["error"] == "The server refused this key. Check the key and try again."
    assert server.paths() == ["/v1/models"]  # a refusal is decisive


def test_no_key_on_a_guarded_server_asks_for_one(client, server):
    server.routes["/v1/models"] = (403, {"error": "forbidden"}, 0.0, {})
    data = _probe(client, base_url=server.base).json()
    assert data["reason"] == "needs_key"
    assert "asks for a key" in data["error"]


def test_unreachable_is_said_plainly(client):
    data = _probe(client, base_url=f"http://127.0.0.1:{_closed_port()}").json()
    assert data["models"] == []
    assert data["reason"] == "unreachable"
    assert data["error"].startswith("Could not reach that address.")
    assert "127.0.0.1" not in data["error"]  # never the URL, never exception text


def test_a_web_page_is_not_a_model_server(client, server):
    page = b"<html><body>Welcome</body></html>"
    for p in ("/v1/models", "/models", "/api/tags"):
        server.routes[p] = (200, page, 0.0, {})
    data = _probe(client, base_url=server.base).json()
    assert data["reason"] == "not_model_server"
    assert "does not look like a model server" in data["error"]


def test_an_empty_listing_says_no_models(client, server):
    server.routes["/v1/models"] = (200, {"data": []}, 0.0, {})
    data = _probe(client, base_url=server.base).json()
    assert data["reason"] == "no_models"
    assert "lists no models" in data["error"]


def test_a_server_error_is_said(client, server):
    server.routes["/v1/models"] = (500, {"error": "boom"}, 0.0, {})
    data = _probe(client, base_url=server.base).json()
    assert data["reason"] == "server_error"


def test_the_probe_is_bounded(client, server, monkeypatch):
    monkeypatch.setattr(conn_routes, "_ENDPOINT_PROBE_TIMEOUT_S", 0.6)
    server.routes["/v1/models"] = (200, {"data": [{"id": "late"}]}, 3.0, {})
    t0 = time.monotonic()
    data = _probe(client, base_url=server.base).json()
    took = time.monotonic() - t0
    assert data["reason"] == "timeout"
    assert data["models"] == []
    assert "did not answer within 1 second." in data["error"]
    assert took < 2.5, f"the probe waited {took:.2f}s on a 0.6s bound"


def test_a_redirect_is_not_followed_so_the_key_stays_put(client, server):
    server.routes["/v1/models"] = (302, {}, 0.0, {"Location": "/elsewhere"})
    server.routes["/elsewhere"] = (200, {"data": [{"id": "stolen"}]}, 0.0, {})
    data = _probe(client, base_url=server.base, api_key=KEY).json()
    assert "/elsewhere" not in server.paths()
    assert data["models"] == []


# --------------------------------------------------------------------------- #
#  Input the form should not send
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("url", ["", "   ", "localhost:1234", "ftp://box/v1", "http://"])
def test_a_missing_or_odd_address_is_a_400_sentence(client, url):
    r = _probe(client, base_url=url, api_key=KEY)
    assert r.status_code == 400
    assert r.json()["detail"].startswith("Enter the endpoint address first")
    assert KEY not in r.text


def test_a_body_without_base_url_never_echoes_the_key(client):
    r = client.post(PATH, json={"api_key": KEY})
    assert r.status_code == 400
    assert KEY not in r.text


def test_an_unknown_protocol_is_refused_before_any_request(client, server):
    # v1.329.0: "anthropic" is listed too (tests/test_endpoint_models_anthropic_v1329.py);
    # any other word is still refused before a request goes out.
    r = _probe(client, base_url=server.base, protocol="gemini")
    assert r.status_code == 400
    assert "OpenAI-compatible" in r.json()["detail"]
    assert server.seen == []  # refused before any request went out


def test_a_key_a_header_cannot_carry_is_refused_without_echo(client, server):
    bad = KEY + "\nX-Evil: 1"
    r = _probe(client, base_url=server.base, api_key=bad)
    assert r.status_code == 400
    assert KEY not in r.text
    assert server.seen == []


# --------------------------------------------------------------------------- #
#  Off the loop, and never logged
# --------------------------------------------------------------------------- #


def test_the_probe_runs_off_the_event_loop(client, monkeypatch):
    where: dict[str, object] = {}

    def fake_probe(base_url, key, *, timeout_s):
        try:
            asyncio.get_running_loop()
            where["loop"] = True
        except RuntimeError:
            where["loop"] = False
        where["thread"] = threading.current_thread().name
        return {"models": ["x"], "error": None, "reason": None}

    monkeypatch.setattr(conn_routes, "_probe_endpoint_models", fake_probe)
    r = _probe(client, base_url="http://127.0.0.1:1")
    assert r.json()["models"] == ["x"]
    assert where["loop"] is False, "the blocking probe ran ON the event loop"


def test_the_key_never_reaches_a_log(client, server, caplog):
    server.routes["/v1/models"] = (200, {"data": [{"id": "m"}]}, 0.0, {})
    with caplog.at_level(logging.DEBUG):
        _probe(client, base_url=server.base, api_key=KEY)
        server.routes["/v1/models"] = (401, {}, 0.0, {})
        _probe(client, base_url=server.base, api_key=KEY)
        _probe(client, base_url=f"http://127.0.0.1:{_closed_port()}", api_key=KEY)
    assert KEY not in caplog.text
    assert any(a == f"Bearer {KEY}" for _, a in server.seen)  # it DID reach the server


def test_model_ids_parser_shapes():
    ids = conn_routes._model_ids  # noqa: SLF001
    assert ids({"data": [{"id": "a"}, {"name": "b"}, {"model": "c"}]}) == ["a", "b", "c"]
    assert ids(["a", "b"]) == ["a", "b"]
    assert ids({"data": []}) == []
    assert ids({"hello": "world"}) is None
    assert ids({"data": [1, 2]}) is None  # a list of nothing that names a model
    assert ids({"data": [{"id": "ok"}, {"id": "bad\nid"}, {"id": "x" * 300}]}) == ["ok"]
