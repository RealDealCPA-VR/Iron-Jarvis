"""v1.322.0 — MCP client protocol fixes (backend wave, borrow/wave-a).

Every test drives the REAL ``MCPClient`` / ``HttpTransport`` /
``StdioTransport`` / ``MCPRemoteTool`` code; fakes sit only at the I/O edge:

* HTTP: a real ``httpx.Client`` over ``httpx.MockTransport`` (the server is a
  handler function; httpx's own streaming, timeouts and headers are real);
* stdio: a genuine child process running a tiny server script written to
  ``tmp_path`` (the same style as ``tests/fixtures/stdio_mcp_server_v1291.py``);
* pagination / content: ``FakeTransport`` canned results under the real client.

THE DEFECTS, read in the code before the fix:

1. ``HttpTransport._parse_body`` returned the FIRST SSE ``data:`` JSON, so a
   server that streamed ``notifications/progress`` first made the call "succeed"
   with ``{}`` and empty output.
2. A server→client REQUEST (``ping``) was dropped on both transports, so a
   server that waits for the answer hung the call.
3. ``list_tools`` read only the first ``tools/list`` page.
4. The HTTP read timeout was a flat 30 s under a 600 s registry deadline.
5. An embedded resource's text read "[resource]"; images "[image]".
"""

from __future__ import annotations

import json
import logging
import sys
import textwrap
import threading
from pathlib import Path

import httpx
import pytest

from iron_jarvis.core.db import init_db, make_engine
from iron_jarvis.core.events import EventBus
from iron_jarvis.mcp import client as mcp_client
from iron_jarvis.mcp.client import (
    FakeTransport,
    HttpTransport,
    MCPClient,
    MCPError,
    StdioTransport,
)
from iron_jarvis.mcp.tools import MCPRemoteTool, _build_transport, _content_to_text
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.permissions import PermissionEngine
from iron_jarvis.tools.registry import ToolRegistry

#: A hang guard for the child server, never a performance bar.
_STDIO_TIMEOUT_S = 8.0
#: How long the fake HTTP server waits for the client's reply to its ping.
_SERVER_WAIT_S = 5.0


# --------------------------------------------------------------------------- #
# HTTP edge: a scripted Streamable-HTTP server behind httpx.MockTransport.
# --------------------------------------------------------------------------- #
def _sse(*messages: dict) -> bytes:
    return "".join(f"event: message\ndata: {json.dumps(m)}\n\n" for m in messages).encode()


class _HttpServer:
    """A Streamable-HTTP MCP server as an httpx.MockTransport handler.

    ``tools_call`` decides how a ``tools/call`` is answered (given the request
    id and this server). Every request httpx actually sent is on ``seen``.
    """

    def __init__(self, tools_call):
        self.tools_call = tools_call
        self.seen: list[httpx.Request] = []
        self.client_replies: list[dict] = []
        self.replied = threading.Event()

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        body = json.loads(request.content or b"{}")
        method = body.get("method")
        if method is None:  # the client answering one of OUR requests
            self.client_replies.append(body)
            self.replied.set()
            return httpx.Response(202)
        if method == "initialize":
            return httpx.Response(
                200,
                headers={"content-type": "application/json", "mcp-session-id": "sess-1"},
                json={"jsonrpc": "2.0", "id": body["id"], "result": {
                    "protocolVersion": "2024-11-05", "capabilities": {"tools": {}}}},
            )
        if "id" not in body:  # notifications/initialized
            return httpx.Response(202)
        if method == "tools/call":
            return self.tools_call(body["id"], self)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {}})

    def transport(self, **kw) -> HttpTransport:
        return HttpTransport(
            "http://pack.test/mcp",
            client_factory=lambda: httpx.Client(transport=httpx.MockTransport(self.handler)),
            name="remote",
            **kw,
        )


def _text_result(rid, text: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid,
            "result": {"content": [{"type": "text", "text": text}], "isError": False}}


def _progress_then_answer(rid, server):
    """Progress + a log message + another id's answer BEFORE the real reply."""
    body = _sse(
        {"jsonrpc": "2.0", "method": "notifications/progress",
         "params": {"progressToken": 1, "progress": 0.5}},
        {"jsonrpc": "2.0", "method": "notifications/message",
         "params": {"level": "info", "data": "working"}},
        {"jsonrpc": "2.0", "id": rid + 1000, "result": {"content": [
            {"type": "text", "text": "SOMEONE ELSE'S ANSWER"}]}},
        _text_result(rid, "the real answer"),
    )
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)


def _ping_and_wait(rid, server):
    """Sends a ping INSIDE the stream, then waits (like a real server) for the
    client's reply before it sends the answer. Unanswered → the stream ends
    with no answer at all."""

    def stream():
        yield _sse({"jsonrpc": "2.0", "id": "srv-ping-1", "method": "ping"})
        if server.replied.wait(_SERVER_WAIT_S):
            reply = server.client_replies[-1]
            yield _sse(_text_result(rid, f"ping answered: {json.dumps(reply)}"))

    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream())


def test_http_a_progress_notification_before_the_response_returns_the_real_result():
    server = _HttpServer(_progress_then_answer)
    tool = MCPRemoteTool(MCPClient(server.transport(), name="remote"), "remote", "work")
    import asyncio

    result = asyncio.run(tool.execute({}, ctx=None))  # type: ignore[arg-type]
    assert result.ok is True, result.error
    assert result.output == "the real answer"


def test_http_a_stream_without_our_answer_is_an_error_naming_the_pack():
    def only_notifications(rid, server):
        body = _sse({"jsonrpc": "2.0", "method": "notifications/progress",
                     "params": {"progressToken": 1, "progress": 1}})
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    server = _HttpServer(only_notifications)
    with pytest.raises(MCPError) as info:
        server.transport().request("tools/call", {"name": "x", "arguments": {}})
    assert "pack 'remote'" in str(info.value)
    assert "without answering request" in str(info.value)


def test_http_a_server_ping_inside_the_stream_is_answered_while_the_server_waits():
    server = _HttpServer(_ping_and_wait)
    result = server.transport().request("tools/call", {"name": "x", "arguments": {}})
    text = result["content"][0]["text"]
    assert text.startswith("ping answered"), text
    reply = server.client_replies[-1]
    assert reply == {"jsonrpc": "2.0", "id": "srv-ping-1", "result": {}}
    # The reply rode the session the server issued.
    reply_req = next(r for r in server.seen if json.loads(r.content).get("id") == "srv-ping-1")
    assert reply_req.headers.get("mcp-session-id") == "sess-1"


def test_http_an_unknown_server_request_gets_method_not_found():
    def sampling_and_wait(rid, server):
        def stream():
            yield _sse({"jsonrpc": "2.0", "id": 7, "method": "sampling/createMessage",
                        "params": {}})
            if server.replied.wait(_SERVER_WAIT_S):
                yield _sse(_text_result(rid, "done"))

        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=stream())

    server = _HttpServer(sampling_and_wait)
    result = server.transport().request("tools/call", {"name": "x", "arguments": {}})
    assert result["content"][0]["text"] == "done"
    reply = server.client_replies[-1]
    assert reply["id"] == 7
    assert reply["error"]["code"] == -32601
    assert "result" not in reply


def test_http_plain_json_bodies_still_work():
    def plain(rid, server):
        return httpx.Response(200, json=_text_result(rid, "plain json"))

    server = _HttpServer(plain)
    result = server.transport().request("tools/call", {"name": "x", "arguments": {}})
    assert result["content"][0]["text"] == "plain json"


# --------------------------------------------------------------------------- #
# HTTP timeout: follows the registry deadline (connect stays short).
# --------------------------------------------------------------------------- #
@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    engine = make_engine(str(tmp_path / "ws.db"))
    init_db(engine)
    return ToolContext(
        workspace=tmp_path, session_id="s1", agent_run_id="r1",
        config=None, event_bus=EventBus(), engine=engine,
    )


def _timeout_server():
    seen_timeouts: list[dict] = []

    def answer(rid, server):
        seen_timeouts.append(dict(server.seen[-1].extensions["timeout"]))
        return httpx.Response(200, json=_text_result(rid, "ok"))

    return _HttpServer(answer), seen_timeouts


async def test_http_read_timeout_follows_the_registry_deadline(ctx):
    server, seen_timeouts = _timeout_server()
    transport = _build_transport({"name": "remote", "transport": "http",
                                  "url": "http://pack.test/mcp"}, None)
    assert isinstance(transport, HttpTransport)
    transport._client_factory = lambda: httpx.Client(
        transport=httpx.MockTransport(server.handler))
    registry = ToolRegistry()
    registry.register(MCPRemoteTool(MCPClient(transport, name="remote"), "remote", "work"),
                      mcp=True)
    perms = PermissionEngine({})

    result = await registry.invoke("mcp__remote__work", {}, ctx, perms,
                                   session_allow=["mcp_call"], deadline_s=300)
    assert result.ok is True, result.error
    t = seen_timeouts[-1]
    # What httpx was told for THIS request: read = what was left of the 300 s
    # deadline plus the grace; connecting stays at the short bound.
    assert 300 - 5 < t["read"] <= 300 + mcp_client._DEADLINE_GRACE_S, t
    assert t["connect"] == mcp_client.HTTP_CONNECT_TIMEOUT_S == 10.0

    # No deadline configured (registry passes None): unbounded, like stdio.
    result = await registry.invoke("mcp__remote__work", {}, ctx, perms,
                                   session_allow=["mcp_call"], deadline_s=None)
    assert result.ok is True, result.error
    assert seen_timeouts[-1]["read"] is None
    assert seen_timeouts[-1]["connect"] == 10.0


def test_http_a_call_with_no_registry_scope_keeps_the_transport_default():
    server, seen_timeouts = _timeout_server()
    import asyncio

    client = MCPClient(server.transport(timeout=30.0), name="remote")
    asyncio.run(client.call_tool("work", {}))
    assert seen_timeouts[-1]["read"] == 30.0
    assert seen_timeouts[-1]["connect"] == 10.0


def test_http_a_read_timeout_is_an_error_naming_the_pack():
    def slow(rid, server):
        raise httpx.ReadTimeout("timed out", request=server.seen[-1])

    server = _HttpServer(slow)
    with pytest.raises(MCPError) as info:
        server.transport().request("tools/call", {"name": "x", "arguments": {}}, timeout=42.0)
    assert "pack 'remote'" in str(info.value)
    assert "42 s" in str(info.value)


# --------------------------------------------------------------------------- #
# stdio: a real child process that pings / asks / notifies.
# --------------------------------------------------------------------------- #
_STDIO_SERVER = textwrap.dedent(
    '''
    import json, sys

    out = sys.stdout

    def send(obj):
        out.write(json.dumps(obj) + "\\n")
        out.flush()

    def wait_reply(rid):
        for raw in sys.stdin:
            line = raw.strip()
            if not line:
                continue
            msg = json.loads(line)
            if msg.get("id") == rid and "method" not in msg:
                return msg
        sys.exit(0)

    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        msg = json.loads(line)
        mid = msg.get("id")
        if mid is None:
            continue
        method = msg.get("method")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": "2024-11-05", "capabilities": {"tools": {}}}})
        elif method == "tools/call":
            name = msg["params"]["name"]
            if name == "needs_ping":
                send({"jsonrpc": "2.0", "method": "notifications/progress",
                      "params": {"progressToken": 1, "progress": 0.1}})
                send({"jsonrpc": "2.0", "id": "srv-ping-1", "method": "ping"})
                reply = wait_reply("srv-ping-1")
                text = "ping answered: " + json.dumps(reply)
            elif name == "needs_sampling":
                # The server's request id COLLIDES with ours on purpose.
                send({"jsonrpc": "2.0", "id": mid, "method": "sampling/createMessage",
                      "params": {}})
                reply = wait_reply(mid)
                text = "sampling reply: " + json.dumps(reply)
            else:
                text = "unknown"
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": text}], "isError": False}})
        else:
            send({"jsonrpc": "2.0", "id": mid, "result": {}})
    '''
)


@pytest.fixture
def stdio_transport(tmp_path: Path):
    script = tmp_path / "pinging_server.py"
    script.write_text(_STDIO_SERVER, encoding="utf-8")
    transport = StdioTransport(sys.executable, [str(script)], request_timeout=_STDIO_TIMEOUT_S)
    try:
        yield transport
    finally:
        transport.close()


def test_stdio_a_server_ping_is_answered_and_the_call_completes(stdio_transport):
    result = stdio_transport.request("tools/call", {"name": "needs_ping", "arguments": {}})
    text = result["content"][0]["text"]
    assert text.startswith("ping answered: "), text
    reply = json.loads(text[len("ping answered: "):])
    assert reply == {"jsonrpc": "2.0", "id": "srv-ping-1", "result": {}}


def test_stdio_an_unknown_server_request_gets_method_not_found(stdio_transport):
    result = stdio_transport.request("tools/call", {"name": "needs_sampling", "arguments": {}})
    text = result["content"][0]["text"]
    assert text.startswith("sampling reply: "), text
    reply = json.loads(text[len("sampling reply: "):])
    assert reply["error"]["code"] == -32601
    assert "result" not in reply


# --------------------------------------------------------------------------- #
# tools/list pagination.
# --------------------------------------------------------------------------- #
def _paged(pages: dict):
    def tools_list(params):
        return pages[params.get("cursor")]

    return tools_list


def test_list_tools_follows_next_cursor_across_three_pages():
    import asyncio

    pages = {
        None: {"tools": [{"name": "a"}], "nextCursor": "p2"},
        "p2": {"tools": [{"name": "b"}], "nextCursor": "p3"},
        "p3": {"tools": [{"name": "c"}]},
    }
    fake = FakeTransport({"tools/list": _paged(pages)})
    tools = asyncio.run(MCPClient(fake, name="paged").list_tools())
    assert [t["name"] for t in tools] == ["a", "b", "c"]
    assert fake.calls == [("tools/list", {}), ("tools/list", {"cursor": "p2"}),
                          ("tools/list", {"cursor": "p3"})]


def test_list_tools_is_capped_and_says_so(caplog):
    import asyncio

    counter = {"n": 0}

    def endless(params):
        counter["n"] += 1
        return {"tools": [{"name": f"t{counter['n']}"}], "nextCursor": f"c{counter['n']}"}

    fake = FakeTransport({"tools/list": endless})
    with caplog.at_level(logging.WARNING):
        tools = asyncio.run(MCPClient(fake, name="endless").list_tools())
    assert len(fake.calls) == mcp_client.MAX_TOOL_LIST_PAGES == 50
    assert len(tools) == 50
    assert any("still had more pages" in r.getMessage() and "'endless'" in r.getMessage()
               for r in caplog.records), [r.getMessage() for r in caplog.records]


# --------------------------------------------------------------------------- #
# Content flattening.
# --------------------------------------------------------------------------- #
async def test_embedded_resource_text_reaches_the_tool_output(ctx):
    fake = FakeTransport({"tools/call": {"content": [
        {"type": "text", "text": "Summary follows."},
        {"type": "resource", "resource": {"uri": "file:///notes/q3.md",
                                          "mimeType": "text/markdown",
                                          "text": "Q3 revenue was 1.2M"}},
        {"type": "resource", "resource": {"uri": "file:///scan.pdf",
                                          "mimeType": "application/pdf",
                                          "blob": "QUJDRA=="}},
        {"type": "image", "data": "iVBORw0KGgo=", "mimeType": "image/png"},
    ], "isError": False}})
    tool = MCPRemoteTool(MCPClient(fake, name="docs"), "docs", "read")
    result = await tool.execute({}, ctx)
    assert result.ok is True
    lines = result.output.splitlines()
    assert lines[0] == "Summary follows."
    assert "[resource: file:///notes/q3.md (text/markdown)]" in lines
    assert "Q3 revenue was 1.2M" in lines
    assert "[resource: file:///scan.pdf (application/pdf, 4 bytes, binary content not shown)]" in lines
    assert "[image: image/png]" in lines
    # The output is NOT self-fenced: the runtime/chat lanes fence it because the
    # tool declares its content untrusted (fencing twice would double-wrap).
    assert MCPRemoteTool.returns_untrusted_content is True
    assert "UNTRUSTED" not in result.output.upper()


async def test_structured_content_is_appended_when_no_text_came_back(ctx):
    fake = FakeTransport({"tools/call": {
        "content": [], "structuredContent": {"temperature": 21.5, "unit": "C"},
        "isError": False}})
    tool = MCPRemoteTool(MCPClient(fake, name="wx"), "wx", "now")
    result = await tool.execute({}, ctx)
    assert result.ok is True
    assert json.loads(result.output) == {"temperature": 21.5, "unit": "C"}
    # With real text present, the structured copy is not repeated.
    assert _content_to_text([{"type": "text", "text": "21.5 C"}], {"t": 21.5}) == "21.5 C"
