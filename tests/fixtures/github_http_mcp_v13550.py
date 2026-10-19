"""v1.355.0 (G44): a stand-in for GitHub's own MCP server, IN PROCESS.

The Directory's GitHub card reaches ``https://api.githubcopilot.com/mcp/``
over Streamable HTTP with ``Authorization: Bearer <token>``. A test must never
touch the network, so this serves the same JSON-RPC shape on 127.0.0.1 and
answers 401 to any other token — what GitHub answers to an expired one —
while recording every request's headers so "the vault value reached the
server" is asserted on the wire.

``github_at(url)`` swaps ONLY the card's address (the card object in
``connectors.catalog.CATALOG``, in place, so every reader of that list — the
gallery, ``get_connector`` — sees it) and puts it back after.
"""

from __future__ import annotations

import dataclasses
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterator

#: Three read-only tools (``readOnlyHint``) and two that change things — the
#: shape of GitHub's own list (get_me / search / list vs create / merge).
GITHUB_TOOLS: list[dict[str, Any]] = [
    {"name": "get_me", "description": "Get your GitHub user.", "annotations": {"readOnlyHint": True},
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "search_repositories", "description": "Search repositories.", "annotations": {"readOnlyHint": True},
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}}},
    {"name": "list_issues", "description": "List issues in a repository.", "annotations": {"readOnlyHint": True},
     "inputSchema": {"type": "object", "properties": {"repo": {"type": "string"}}}},
    {"name": "create_issue", "description": "Open a new issue.",
     "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}}}},
    {"name": "merge_pull_request", "description": "Merge a pull request.", "annotations": {"readOnlyHint": False},
     "inputSchema": {"type": "object", "properties": {"number": {"type": "integer"}}}},
]


class _Handler(BaseHTTPRequestHandler):
    server_version = "fake-github-mcp/1"

    def log_message(self, *args):  # noqa: D401 — keep test output quiet
        pass

    def do_POST(self):  # noqa: N802 — http.server's name
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {}
        headers = {k.lower(): v for k, v in self.headers.items()}
        self.server.seen.append({"method": body.get("method"), "headers": headers})
        want = f"Bearer {self.server.token}"
        if headers.get("authorization") != want:
            payload = b'{"message":"Bad credentials"}'
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        rid = body.get("id")
        if rid is None:  # a notification
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        method = body.get("method")
        if method == "initialize":
            result: dict[str, Any] = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "github-mcp-server", "version": "test"},
            }
        elif method == "tools/list":
            result = {"tools": list(self.server.tools)}
        elif method == "tools/call":
            result = {"content": [{"type": "text", "text": "ok"}], "isError": False}
        else:
            result = {}
        payload = json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Mcp-Session-Id", "sess-v13550")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class FakeGitHub:
    """``with FakeGitHub(token) as gh: gh.url`` — a server that accepts only
    ``Bearer <token>`` (set ``gh.token`` to "expire" the one the user saved)."""

    def __init__(self, token: str, tools: list[dict[str, Any]] | None = None) -> None:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.daemon_threads = True
        self._server.seen = []  # type: ignore[attr-defined]
        self._server.token = token  # type: ignore[attr-defined]
        self._server.tools = list(GITHUB_TOOLS if tools is None else tools)  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/mcp/"

    @property
    def token(self) -> str:
        return self._server.token  # type: ignore[attr-defined]

    @token.setter
    def token(self, value: str) -> None:
        self._server.token = value  # type: ignore[attr-defined]

    @property
    def tools(self) -> list[dict[str, Any]]:
        return self._server.tools  # type: ignore[attr-defined]

    @tools.setter
    def tools(self, value: list[dict[str, Any]]) -> None:
        self._server.tools = list(value)  # type: ignore[attr-defined]

    @property
    def seen(self) -> list[dict[str, Any]]:
        return self._server.seen  # type: ignore[attr-defined]

    def bearer_seen(self) -> list[str]:
        return [r["headers"].get("authorization", "") for r in self.seen]

    def __enter__(self) -> "FakeGitHub":
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()


@contextmanager
def github_at(url: str) -> Iterator[Any]:
    """Swap ONLY the GitHub card's address, in place in ``CATALOG`` (every
    reader of that list — the gallery, ``get_connector`` — sees it), and put
    the real card back after."""
    from iron_jarvis.connectors import catalog

    idx = next(i for i, c in enumerate(catalog.CATALOG) if c.id == "github")
    real = catalog.CATALOG[idx]
    catalog.CATALOG[idx] = dataclasses.replace(real, url=url)
    try:
        yield catalog.CATALOG[idx]
    finally:
        catalog.CATALOG[idx] = real
