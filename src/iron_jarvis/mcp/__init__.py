"""MCP client — consume external Model Context Protocol servers as native tools.

Iron Jarvis is an MCP *client*: it speaks JSON-RPC 2.0 (``tools/list`` /
``tools/call``) to external MCP *servers* (Gmail / Drive / GitHub / ...) and
surfaces each remote tool to agents as an ``mcp__<server>__<tool>`` ``Tool``
gated by the ``mcp_call`` permission. The wire transport is dependency-injected
(:class:`StdioTransport`, :class:`HttpTransport`, or a test :class:`FakeTransport`),
so configuration, import, and tests are all side-effect-free.

Platform wiring is a no-op by default: ``mcp_tools(None)`` -> ``[]``.
"""

from __future__ import annotations

from .client import (
    FakeTransport,
    HttpTransport,
    MCPClient,
    MCPError,
    StdioTransport,
)
from .tools import MCPRemoteTool, SecretResolver, mcp_tools

__all__ = [
    "MCPClient",
    "MCPError",
    "MCPRemoteTool",
    "FakeTransport",
    "StdioTransport",
    "HttpTransport",
    "SecretResolver",
    "mcp_tools",
]
