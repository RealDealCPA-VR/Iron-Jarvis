"""Wrap remote MCP tools as native Iron Jarvis ``Tool`` objects (§19).

Each tool advertised by an external MCP server is exposed to agents as an
:class:`MCPRemoteTool` named ``mcp__<server>__<tool>`` and gated by the single
``mcp_call`` permission key. ``mcp_tools`` is the builder the platform calls: it
connects to each configured server, lists its tools, and returns the wrapped
``Tool`` objects — defaulting to a **no-op empty list** when nothing is
configured, and skipping (never crashing on) any server it cannot reach.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from typing import Any, Callable

from ..core.logging import get_logger
from ..tools.base import Tool, ToolContext, ToolResult
from .client import FakeTransport, HttpTransport, MCPClient, StdioTransport

log = get_logger("mcp")

#: A resolver for secret-referenced auth: ``name -> plaintext value`` (or None).
SecretResolver = Callable[[str], "str | None"]


def _content_to_text(content: Any) -> str:
    """Flatten MCP ``content`` blocks into plain text for ``ToolResult.output``."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):  # a single block
        content = [content]
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict):
            if block.get("type") == "text" and "text" in block:
                parts.append(str(block["text"]))
            elif "text" in block:
                parts.append(str(block["text"]))
            else:  # image / resource / other — describe, don't drop
                parts.append(f"[{block.get('type', 'content')}]")
        else:
            parts.append(str(block))
    return "\n".join(parts)


class MCPRemoteTool(Tool):
    """A native ``Tool`` that proxies to one remote MCP tool via an ``MCPClient``."""

    permission_key = "mcp_call"

    def __init__(
        self,
        client: MCPClient,
        server_name: str,
        remote_name: str,
        description: str = "",
        input_schema: dict[str, Any] | None = None,
    ) -> None:
        self.client = client
        self.server_name = server_name
        self.remote_name = remote_name
        self.name = f"mcp__{server_name}__{remote_name}"
        self.description = description or (
            f"Remote MCP tool '{remote_name}' from server '{server_name}'."
        )
        self.input_schema = input_schema or {"type": "object", "properties": {}}

    @classmethod
    def from_spec(
        cls, client: MCPClient, server_name: str, spec: dict[str, Any]
    ) -> "MCPRemoteTool":
        return cls(
            client,
            server_name,
            spec.get("name", "tool"),
            spec.get("description", ""),
            spec.get("inputSchema") or spec.get("input_schema"),
        )

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            result = await self.client.call_tool(self.remote_name, args)
        except Exception as exc:  # a remote failure must never crash the runtime
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")

        result = result if isinstance(result, dict) else {}
        text = _content_to_text(result.get("content"))
        if result.get("isError"):
            return ToolResult(
                ok=False, output=text, error=text or "remote MCP tool error", data=result
            )
        return ToolResult(ok=True, output=text, data=result)


# --------------------------------------------------------------------------- #
# Builder.
# --------------------------------------------------------------------------- #
def _run_sync(coro: Any) -> Any:
    """Drive an async coroutine to completion from synchronous platform wiring.

    Platform assembly is synchronous, but the client API is async. If we are
    already inside a running event loop, run the coroutine on a worker thread to
    avoid nesting; otherwise just ``asyncio.run`` it.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _build_transport(cfg: dict[str, Any], secret_resolver: SecretResolver | None) -> Any:
    """Construct (or accept an injected) transport from a server config dict."""
    # Direct injection wins (tests / advanced use): a pre-built transport object.
    injected = cfg.get("transport_obj")
    if injected is not None:
        return injected
    raw = cfg.get("transport")
    if raw is not None and not isinstance(raw, str) and hasattr(raw, "request"):
        return raw

    kind = (raw if isinstance(raw, str) else "stdio").lower()

    if kind in ("http", "streamable-http", "streamable_http", "sse"):
        headers = dict(cfg.get("headers") or {})
        auth = cfg.get("auth")
        if isinstance(auth, dict):
            value = None
            if auth.get("secret") and secret_resolver is not None:
                value = secret_resolver(auth["secret"])
            elif auth.get("value"):
                value = auth["value"]
            if value:
                header = auth.get("header", "Authorization")
                fmt = auth.get("format", "Bearer {value}")
                headers[header] = fmt.format(value=value)
        return HttpTransport(cfg["url"], headers=headers)

    # Default: stdio subprocess.
    return StdioTransport(
        cfg["command"],
        cfg.get("args"),
        env=cfg.get("env"),
        cwd=cfg.get("cwd"),
    )


def mcp_tools(
    server_configs: list[dict[str, Any]] | None,
    secret_resolver: SecretResolver | None = None,
) -> list[Tool]:
    """Build the wrapped MCP tools for every configured server.

    * Empty / ``None`` config (the default — no MCP servers) -> ``[]`` so platform
      wiring is a safe no-op.
    * Each server is connected, ``tools/list``-ed, and its tools wrapped.
    * A server that cannot be reached (connection / list error) is **skipped**
      with a warning so one bad server never breaks boot.
    """
    if not server_configs:
        return []

    tools: list[Tool] = []
    for cfg in server_configs:
        name = cfg.get("name") or "mcp"
        try:
            transport = _build_transport(cfg, secret_resolver)
            client = MCPClient(transport, name=name)
            specs = _run_sync(client.list_tools())
        except Exception as exc:  # skip the bad server; keep booting
            log.warning("skipping MCP server %r: %s: %s", name, type(exc).__name__, exc)
            continue
        for spec in specs:
            if not isinstance(spec, dict) or not spec.get("name"):
                continue
            tools.append(MCPRemoteTool.from_spec(client, name, spec))
    return tools


__all__ = [
    "MCPRemoteTool",
    "SecretResolver",
    "mcp_tools",
    "FakeTransport",
    "MCPClient",
    "StdioTransport",
    "HttpTransport",
]
