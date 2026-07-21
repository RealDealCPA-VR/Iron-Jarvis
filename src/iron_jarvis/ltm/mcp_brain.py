"""An MCP-served brain as a long-term-memory source (kind ``mcp``).

The user's own memory server — an Obsidian brain behind ``mcp-remote``, a
hosted notes MCP, anything speaking the protocol — plugs into the SAME
LongTermMemory surface as the local brain/Obsidian/Notion connectors.

Tool names vary per server, so the connector DISCOVERS them: the first tool
whose name (then description) matches a search-ish pattern serves
:meth:`search`, an append-ish one serves :meth:`append`, and arguments are
mapped from the tool's OWN input schema (query/q/text…, title/name…,
content/body…). Results normalize to the uniform hit shape
``{title, snippet, ref, source}`` whether the server returns JSON lists or
plain text.

Connection is LAZY: registering the source does nothing over the network, so
boot can never hang on a remote brain and a dead server degrades to an honest
error at query time. The bearer token is resolved from the encrypted vault at
connect time — never stored on the record.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Callable

from .base import LTMConnector


def _resolve_maybe_async(value: Any) -> Any:
    """The LTM contract is SYNC; the real MCPClient's methods are ASYNC (test
    fakes are sync). Run a coroutine to completion — from a worker thread
    (FastAPI sync routes, the graph builder) a private loop is safe; if a loop
    is already running (agent tool path) hop to a helper thread."""
    if not asyncio.iscoroutine(value):
        return value
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(value)
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, value).result()

_SEARCH_RX = re.compile(r"search|query|recall|retrieve|find|lookup", re.IGNORECASE)
_APPEND_RX = re.compile(
    r"append|add|create|write|save|store|note|ingest|upsert", re.IGNORECASE
)
_LIST_RX = re.compile(r"\b(list|all|recent|browse|index|enumerate)", re.IGNORECASE)
_QUERY_KEYS = ("query", "q", "text", "search", "keywords", "prompt", "input")
_TITLE_KEYS = ("title", "name", "subject", "summary", "heading", "filename", "path")
_CONTENT_KEYS = ("content", "text", "body", "note", "markdown", "data")
_LIMIT_KEYS = ("k", "limit", "top_k", "max_results", "count")
_RESULT_LIST_KEYS = ("results", "hits", "items", "notes", "documents", "matches")


def _content_text(res: dict[str, Any] | None) -> str:
    """Flatten an MCP tools/call result's text content blocks."""
    parts: list[str] = []
    for c in (res or {}).get("content") or []:
        if isinstance(c, dict) and c.get("type") == "text":
            parts.append(str(c.get("text", "")))
    return "\n".join(parts)


def _hits_from_text(text: str, source: str, k: int) -> list[dict[str, Any]]:
    """Normalize a server's reply to uniform hits — JSON first, prose fallback."""
    data: Any = None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        data = None
    items: list[Any] | None = None
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        for key in _RESULT_LIST_KEYS:
            if isinstance(data.get(key), list):
                items = data[key]
                break
    hits: list[dict[str, Any]] = []
    if items is not None:
        for it in items[:k]:
            if isinstance(it, dict):
                title = str(
                    it.get("title") or it.get("name") or it.get("path")
                    or it.get("id") or "note"
                )
                snippet = str(
                    it.get("snippet") or it.get("content") or it.get("text")
                    or it.get("excerpt") or ""
                )[:500]
                ref = str(
                    it.get("ref") or it.get("path") or it.get("id")
                    or it.get("url") or title
                )
            else:
                s = str(it)
                title, snippet, ref = s[:80], s[:500], s[:200]
            hits.append({"title": title, "snippet": snippet, "ref": ref, "source": source})
        return hits
    # Plain text: paragraphs become hits (an honest degrade, never empty-silent
    # when the server DID answer).
    for block in [b.strip() for b in text.split("\n\n") if b.strip()][:k]:
        first = block.splitlines()[0][:80]
        hits.append({"title": first, "snippet": block[:500], "ref": first, "source": source})
    return hits


class McpBrainConnector(LTMConnector):
    """Long-term memory over an MCP server (HTTP/SSE url or stdio command)."""

    def __init__(
        self,
        name: str,
        *,
        url: str = "",
        headers: "dict[str, str] | None" = None,
        token_resolver: "Callable[[], str | None] | None" = None,
        command: str = "",
        args: "list[str] | None" = None,
        env: "dict[str, str] | None" = None,
        client: Any = None,
    ) -> None:
        self.name = name
        self._url = url
        self._headers = dict(headers or {})
        self._token_resolver = token_resolver
        self._command = command
        self._args = list(args or [])
        self._env = dict(env or {})
        self._client = client  # injected in tests; built lazily otherwise
        self._tools: "list[dict[str, Any]] | None" = None

    # -- lazy connection ----------------------------------------------------
    def _connect(self) -> Any:
        if self._client is None:
            from ..mcp.client import HttpTransport, MCPClient, StdioTransport

            headers = dict(self._headers)
            if self._token_resolver is not None:
                tok = self._token_resolver()
                if tok:
                    headers.setdefault("Authorization", f"Bearer {tok}")
            if self._url:
                transport: Any = HttpTransport(self._url, headers=headers)
            elif self._command:
                transport = StdioTransport(
                    self._command, self._args, env=self._env or None
                )
            else:
                raise RuntimeError(f"{self.name}: no MCP url or command configured")
            self._client = MCPClient(transport, name=self.name)
        if self._tools is None:
            self._tools = _resolve_maybe_async(self._client.list_tools())
        return self._client

    def _pick(
        self, rx: re.Pattern[str], *, exclude: "re.Pattern[str] | None" = None
    ) -> "dict[str, Any] | None":
        """First tool whose NAME (then description) matches *rx* — skipping any
        whose name matches *exclude* (the append pick must never grab
        ``search_notes`` just because "note" appears in it)."""

        def ok(t: dict[str, Any]) -> bool:
            return exclude is None or not exclude.search(str(t.get("name", "")))

        for t in self._tools or []:
            if ok(t) and rx.search(str(t.get("name", ""))):
                return t
        for t in self._tools or []:
            if ok(t) and rx.search(str(t.get("description", ""))):
                return t
        return None

    @staticmethod
    def _schema_keys(tool: dict[str, Any]) -> list[str]:
        props = (tool.get("inputSchema") or {}).get("properties") or {}
        return list(props.keys())

    @staticmethod
    def _map_arg(keys: list[str], prefs: tuple[str, ...], value: str) -> dict[str, Any]:
        for k in prefs:
            if k in keys:
                return {k: value}
        return {keys[0]: value} if keys else {"query": value}

    # -- the LTMConnector contract ------------------------------------------
    def search(self, query: str, k: int = 5) -> list[dict[str, Any]]:
        client = self._connect()
        tool = self._pick(_SEARCH_RX)
        if tool is None:
            raise RuntimeError(
                f"{self.name}: the MCP server exposes no search-like tool"
            )
        keys = self._schema_keys(tool)
        args = self._map_arg(keys, _QUERY_KEYS, query)
        for lk in _LIMIT_KEYS:
            if lk in keys:
                args[lk] = k
                break
        res = _resolve_maybe_async(client.call_tool(str(tool.get("name")), args))
        text = _content_text(res)
        if (res or {}).get("isError"):
            raise RuntimeError(f"{self.name}: {text[:300] or 'search failed'}")
        return _hits_from_text(text, self.name, k)

    def list_items(self, limit: int = 60) -> list[dict[str, Any]]:
        """OPTIONAL enumeration for the Memory list/graph views: when the
        server exposes a list-ish tool (list_notes / get_recent / browse…),
        return up to *limit* items in the uniform hit shape. Raises when the
        server has no such tool — the graph then honestly omits this source
        (exactly like Notion/RAG endpoints), rather than showing a fake
        sample. Search/append are unaffected either way."""
        client = self._connect()
        tool = self._pick(_LIST_RX, exclude=_SEARCH_RX)
        if tool is None:
            raise RuntimeError(
                f"{self.name}: the MCP server exposes no list/browse-style tool"
            )
        keys = self._schema_keys(tool)
        args: dict[str, Any] = {}
        for lk in _LIMIT_KEYS:
            if lk in keys:
                args[lk] = limit
                break
        res = _resolve_maybe_async(client.call_tool(str(tool.get("name")), args))
        text = _content_text(res)
        if (res or {}).get("isError"):
            raise RuntimeError(f"{self.name}: {text[:300] or 'list failed'}")
        return _hits_from_text(text, self.name, limit)

    def append(self, title: str, content: str) -> str:
        client = self._connect()
        tool = self._pick(_APPEND_RX, exclude=_SEARCH_RX)
        if tool is None:
            raise RuntimeError(
                f"{self.name}: read-only — the MCP server exposes no "
                "append/create-style tool"
            )
        keys = self._schema_keys(tool)
        args = self._map_arg(keys, _TITLE_KEYS, title)
        content_key = next(
            (c for c in _CONTENT_KEYS if c in keys and c not in args), None
        )
        if content_key is not None:
            args[content_key] = content
        elif len(keys) >= 2:
            spare = next((k2 for k2 in keys if k2 not in args), None)
            if spare:
                args[spare] = content
        else:
            # Single-argument tool: fold title + content into one payload.
            only = next(iter(args))
            args[only] = f"{title}\n\n{content}"
        res = _resolve_maybe_async(client.call_tool(str(tool.get("name")), args))
        text = _content_text(res)
        if (res or {}).get("isError"):
            raise RuntimeError(f"{self.name}: {text[:300] or 'append failed'}")
        return text.strip()[:200] or title
