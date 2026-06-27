"""Minimal MCP (Model Context Protocol) client (§ external tool consumption).

Iron Jarvis is an MCP *client*: it consumes tools exposed by external MCP
*servers* (Gmail / Drive / GitHub / ...). This module speaks JSON-RPC 2.0 with
the two methods every MCP server implements:

* ``tools/list``  -> the server's tool catalogue (name / description / inputSchema)
* ``tools/call``  -> invoke one tool and return its ``content`` blocks

The wire protocol is **dependency-injected** through a *transport*: an object
exposing ``request(method, params) -> dict`` (sync or async) that owns the
JSON-RPC framing and returns the MCP *result* payload (or raises). This keeps
the client trivially testable — tests inject :class:`FakeTransport` with canned
responses and **never** spawn a process or open a socket. Two real transports
are provided for production: :class:`StdioTransport` (subprocess, line-delimited
JSON-RPC) and :class:`HttpTransport` (lazy ``httpx``, JSON or SSE body).
"""

from __future__ import annotations

import inspect
import json
import subprocess
from typing import Any, Callable

from ..core.logging import get_logger

log = get_logger("mcp")

#: MCP protocol revision advertised in the ``initialize`` handshake.
PROTOCOL_VERSION = "2024-11-05"
_CLIENT_INFO = {"name": "iron-jarvis", "version": "0"}


# --------------------------------------------------------------------------- #
# JSON-RPC helpers (shared by the real transports).
# --------------------------------------------------------------------------- #
def _envelope(request_id: int, method: str, params: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params or {},
    }


def _extract_result(response: dict[str, Any]) -> dict[str, Any]:
    """Unwrap a JSON-RPC response, raising on a JSON-RPC ``error``."""
    if not isinstance(response, dict):
        raise MCPError(f"malformed JSON-RPC response: {response!r}")
    if response.get("error") is not None:
        err = response["error"]
        if isinstance(err, dict):
            raise MCPError(f"{err.get('code', '?')}: {err.get('message', err)}")
        raise MCPError(str(err))
    result = response.get("result")
    return result if isinstance(result, dict) else {}


class MCPError(RuntimeError):
    """A protocol- or transport-level MCP failure."""


# --------------------------------------------------------------------------- #
# The client.
# --------------------------------------------------------------------------- #
class MCPClient:
    """Talk to a single external MCP server over an injected ``transport``.

    ``transport.request(method, params)`` may be synchronous or a coroutine; both
    are awaited transparently. It must return the JSON-RPC *result* object for the
    call (the real transports unwrap the envelope; :class:`FakeTransport` returns
    canned results directly).
    """

    def __init__(self, transport: Any, name: str = "server") -> None:
        self.transport = transport
        self.name = name

    async def _request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        result = self.transport.request(method, params or {})
        if inspect.isawaitable(result):
            result = await result
        return result if isinstance(result, dict) else {}

    async def list_tools(self) -> list[dict[str, Any]]:
        """Return the server's tool specs (raw MCP dicts: name/description/inputSchema)."""
        result = await self._request("tools/list", {})
        tools = result.get("tools", [])
        return list(tools) if isinstance(tools, list) else []

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Invoke a remote tool; return the raw MCP result ({content, isError})."""
        return await self._request(
            "tools/call", {"name": name, "arguments": arguments or {}}
        )

    def close(self) -> None:
        closer = getattr(self.transport, "close", None)
        if callable(closer):
            closer()


# --------------------------------------------------------------------------- #
# FakeTransport — offline test double (also handy for demos).
# --------------------------------------------------------------------------- #
class FakeTransport:
    """A canned, in-memory transport. **Never** touches a process or socket.

    ``responses`` maps a JSON-RPC method (``"tools/list"`` / ``"tools/call"``) to
    either a static result dict or a ``callable(params) -> dict``. Methods listed
    in ``raise_on`` raise ``error`` (default :class:`MCPError`) — used to exercise
    the "bad server is skipped" path. Every call is recorded on ``calls``.
    """

    def __init__(
        self,
        responses: dict[str, Any] | None = None,
        *,
        raise_on: object = None,
        error: Exception | None = None,
    ) -> None:
        self.responses: dict[str, Any] = dict(responses or {})
        # raise_on may be a single method name, an iterable of names, or True (all).
        if raise_on is True:
            self.raise_on: object = True
        elif isinstance(raise_on, str):
            self.raise_on = {raise_on}
        elif raise_on:
            self.raise_on = set(raise_on)
        else:
            self.raise_on = set()
        self.error = error or MCPError("fake transport failure")
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((method, dict(params or {})))
        if self.raise_on is True or (
            isinstance(self.raise_on, set) and method in self.raise_on
        ):
            raise self.error
        canned = self.responses.get(method, {})
        if callable(canned):
            return canned(params or {})
        return canned


# --------------------------------------------------------------------------- #
# StdioTransport — real subprocess, line-delimited JSON-RPC. Lazy spawn.
# --------------------------------------------------------------------------- #
class StdioTransport:
    """Run an MCP server as a child process and exchange one JSON message per line.

    The process is spawned **lazily** on the first ``request`` (never at import or
    construction), so merely configuring a server is side-effect-free.
    """

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        *,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> None:
        self.command = command
        self.args = list(args or [])
        self.env = env
        self.cwd = cwd
        self._proc: subprocess.Popen | None = None
        self._id = 0

    # -- lifecycle ----------------------------------------------------------
    def _ensure_started(self) -> None:
        if self._proc is not None:
            return
        self._proc = subprocess.Popen(  # noqa: S603 — command comes from trusted config
            [self.command, *self.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env=self.env,
            cwd=self.cwd,
        )
        # MCP handshake: initialize (request), then the initialized notification.
        self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": _CLIENT_INFO,
            },
        )
        self._write({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    def close(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:  # pragma: no cover - best effort
                pass
            self._proc = None

    # -- io -----------------------------------------------------------------
    def _write(self, message: dict[str, Any]) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        self._proc.stdin.write(json.dumps(message) + "\n")
        self._proc.stdin.flush()

    def _read(self, expected_id: int) -> dict[str, Any]:
        assert self._proc is not None and self._proc.stdout is not None
        while True:
            line = self._proc.stdout.readline()
            if line == "":
                raise MCPError("MCP stdio server closed the connection")
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue  # ignore non-JSON log noise on stdout
            # Skip notifications / responses to other ids.
            if msg.get("id") == expected_id:
                return msg

    def _rpc(self, method: str, params: dict[str, Any] | None) -> dict[str, Any]:
        self._id += 1
        rid = self._id
        self._write(_envelope(rid, method, params))
        return _extract_result(self._read(rid))

    # -- transport interface ------------------------------------------------
    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._ensure_started()
        return self._rpc(method, params)


# --------------------------------------------------------------------------- #
# HttpTransport — real HTTP / streamable-http (JSON or SSE). Lazy httpx.
# --------------------------------------------------------------------------- #
class HttpTransport:
    """POST JSON-RPC to an MCP HTTP endpoint. The ``httpx`` client is created
    lazily on first use so no socket/connection pool exists until a real call.
    """

    def __init__(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.url = url
        self.headers = dict(headers or {})
        self.timeout = timeout
        self._client_factory = client_factory
        self._client: Any | None = None
        self._id = 0

    def _ensure_client(self) -> Any:
        if self._client is None:
            if self._client_factory is not None:
                self._client = self._client_factory()
            else:
                import httpx  # lazy import — keeps module import cheap/offline

                self._client = httpx.Client(timeout=self.timeout)
        return self._client

    def close(self) -> None:
        if self._client is not None:
            closer = getattr(self._client, "close", None)
            if callable(closer):
                closer()
            self._client = None

    @staticmethod
    def _parse_body(response: Any) -> dict[str, Any]:
        ctype = ""
        try:
            ctype = response.headers.get("content-type", "")
        except Exception:  # pragma: no cover - defensive
            ctype = ""
        if "text/event-stream" in ctype:
            # Server-Sent Events: the JSON-RPC payload rides on ``data:`` lines.
            for raw in response.text.splitlines():
                line = raw.strip()
                if line.startswith("data:"):
                    chunk = line[len("data:"):].strip()
                    if chunk and chunk != "[DONE]":
                        try:
                            return json.loads(chunk)
                        except json.JSONDecodeError:
                            continue
            raise MCPError("no JSON-RPC payload in SSE response")
        return response.json()

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        client = self._ensure_client()
        self._id += 1
        payload = _envelope(self._id, method, params)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self.headers,
        }
        response = client.post(self.url, json=payload, headers=headers)
        response.raise_for_status()
        return _extract_result(self._parse_body(response))
