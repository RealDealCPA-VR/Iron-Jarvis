"""Iron Jarvis as an MCP **server**: the outward direction (D17, plan 12.1).

:mod:`iron_jarvis.mcp` is the CLIENT — Jarvis consuming Gmail / Drive / GitHub
servers. This package is the opposite arrow: an external harness (Claude Code,
Codex, Pi) running inside a Build pane consumes Jarvis's own tools over
Streamable HTTP at ``POST /mcp``. It is a sibling package rather than a module
inside ``mcp/`` precisely so those two directions stay distinguishable; a reader
who confuses them ends up asking why "our MCP server" needs an API key.

What makes this ship worth doing is what it deliberately does NOT add:

* **No second execution path.** ``tools/call`` runs through the same
  ``ToolRegistry.invoke(..., allowed_names=...)`` as chat, so an MCP caller meets
  the identical roster gate, permission engine, deny floor, ledger and event
  path. A parallel implementation with its own gates would be a way around every
  rule the app has (the v1.227.0 lesson: the roster is the contract, and the gate
  is in the registry).
* **No new tools.** The same fourteen browser tools reach a second caller.
* **No new dependency.** There is no ``mcp`` SDK in this repository. The wire is
  written by hand against the shape this repository's own client already speaks
  and has been proven against real servers, which also makes the server testable
  in-process by pointing that client at it.
* **No new credential class.** ``/mcp`` accepts exactly one thing: a pane-scoped
  capability token from
  :mod:`iron_jarvis.browser.panetokens`. The install bearer and a browser pairing
  token are refused, each by its own explicit check.

Layout:

``jsonrpc.py``  envelopes and error codes, mirroring ``mcp/client.py``
``session.py``  the ``Mcp-Session-Id`` lifecycle
``server.py``   the four methods (``initialize``, ``notifications/initialized``,
                ``tools/list``, ``tools/call``) — a separate lane
``stdio_shim.py`` the thin stdio bridge for harnesses that cannot speak
                Streamable HTTP — a separate lane

Only the first two are imported here, so this package keeps importing while the
other two are being written and so a CLI path that only needs the envelopes does
not pull in FastAPI.
"""

from __future__ import annotations

from .jsonrpc import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    JSON_CONTENT_TYPE,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    PROTOCOL_VERSION,
    SERVER_INFO,
    SSE_CONTENT_TYPE,
    JsonRpcError,
    JsonRpcRequest,
    accepts_json,
    accepts_sse,
    error_response,
    parse_body,
    parse_request,
    result_response,
    sse_body,
)
from .session import (
    MAX_SESSIONS,
    MAX_SESSIONS_PER_PANE,
    SESSION_HEADER,
    SESSION_IDLE_TTL_S,
    McpSession,
    McpSessionRegistry,
    session_id_from_headers,
)

__all__ = [
    # jsonrpc
    "INTERNAL_ERROR",
    "INVALID_PARAMS",
    "INVALID_REQUEST",
    "JSON_CONTENT_TYPE",
    "METHOD_NOT_FOUND",
    "PARSE_ERROR",
    "PROTOCOL_VERSION",
    "SERVER_INFO",
    "SSE_CONTENT_TYPE",
    "JsonRpcError",
    "JsonRpcRequest",
    "accepts_json",
    "accepts_sse",
    "error_response",
    "parse_body",
    "parse_request",
    "result_response",
    "sse_body",
    # session
    "MAX_SESSIONS",
    "MAX_SESSIONS_PER_PANE",
    "SESSION_HEADER",
    "SESSION_IDLE_TTL_S",
    "McpSession",
    "McpSessionRegistry",
    "session_id_from_headers",
]
