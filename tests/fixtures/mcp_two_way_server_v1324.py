"""A REAL stdio MCP server that TALKS BACK, for the v1.324.0 two-way pins.

No deps, no network; line-delimited JSON-RPC 2.0 as
``iron_jarvis.mcp.client.StdioTransport`` speaks it. Tools:

* ``caps``    -> the ``capabilities`` the client declared in ``initialize``
* ``ask``     -> during the call: ``notifications/progress`` for the call's own
                 token AND for a foreign token, ``elicitation/create`` (form),
                 ``sampling/createMessage`` (with an id that COLLIDES with the
                 client's own request id), one more progress; then answers
                 with JSON of everything the client sent back
* ``url_ask`` -> an ``elicitation/create`` in url mode; answers with the reply
* ``spam``    -> twelve progress notifications for the call's token at once

Prompts/resources: ``prompts/list`` and ``resources/list`` are NOT served
(-32601), the way a tools-only server answers.
"""

from __future__ import annotations

import json
import sys

_out = sys.stdout.buffer
_caps: dict = {}


def _send(obj: dict) -> None:
    _out.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
    _out.flush()


def _wait_reply(rid) -> dict:
    """Read until the CLIENT's answer to our request ``rid`` arrives."""
    for raw in sys.stdin.buffer:
        line = raw.decode("utf-8").strip()
        if not line:
            continue
        msg = json.loads(line)
        if msg.get("id") == rid and "method" not in msg:
            return msg
    sys.exit(0)


def _text(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _progress(token, progress, total=None, message=""):
    params = {"progressToken": token, "progress": progress}
    if total is not None:
        params["total"] = total
    if message:
        params["message"] = message
    _send({"jsonrpc": "2.0", "method": "notifications/progress", "params": params})


def _call(mid, name: str, params: dict) -> dict:
    meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
    token = meta.get("progressToken")
    if name == "caps":
        return _text(json.dumps(_caps))
    if name == "ask":
        if token is not None:
            _progress(token, 1, 3, "starting")
        _progress("someone-else", 2, 3, "not yours")
        _send({"jsonrpc": "2.0", "id": "srv-e1", "method": "elicitation/create", "params": {
            "message": "Which client is this for?",
            "requestedSchema": {
                "type": "object",
                "properties": {
                    "client": {"type": "string", "title": "Client name", "minLength": 1},
                    "year": {"type": "integer", "minimum": 2000, "maximum": 2100},
                },
                "required": ["client"],
            },
        }})
        elicited = _wait_reply("srv-e1")
        # The server's request id COLLIDES with the client's own on purpose.
        _send({"jsonrpc": "2.0", "id": mid, "method": "sampling/createMessage", "params": {
            "messages": [{"role": "user", "content": {"type": "text", "text": "Say hi"}}],
            "maxTokens": 50,
        }})
        sampled = _wait_reply(mid)
        if token is not None:
            _progress(token, 3, 3, "done")
        return _text(json.dumps({"token": token, "elicitation": elicited, "sampling": sampled}))
    if name == "url_ask":
        _send({"jsonrpc": "2.0", "id": "srv-u1", "method": "elicitation/create", "params": {
            "mode": "url", "message": "Sign in", "url": "https://example.com/login",
            "elicitationId": "e-1",
        }})
        return _text(json.dumps(_wait_reply("srv-u1")))
    if name == "spam":
        for i in range(12):
            _progress(token, i, 12)
        return _text("spammed")
    return {"content": [{"type": "text", "text": f"unknown tool {name!r}"}], "isError": True}


def serve() -> None:
    global _caps
    for raw in sys.stdin.buffer:
        line = raw.decode("utf-8").strip()
        if not line:
            continue
        msg = json.loads(line)
        mid = msg.get("id")
        if mid is None:
            continue  # a notification takes no reply
        method = msg.get("method")
        if method is None:
            continue  # a stray reply
        params = msg.get("params") or {}
        if method == "initialize":
            _caps = params.get("capabilities")
            _send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                "serverInfo": {"name": "two-way-v1324", "version": "1"}}})
        elif method == "tools/list":
            _send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [
                {"name": n, "description": n, "inputSchema": {"type": "object", "properties": {}}}
                for n in ("caps", "ask", "url_ask", "spam")
            ]}})
        elif method == "tools/call":
            _send({"jsonrpc": "2.0", "id": mid,
                   "result": _call(mid, params.get("name"), params)})
        else:
            _send({"jsonrpc": "2.0", "id": mid,
                   "error": {"code": -32601, "message": f"Method not found: {method}"}})


if __name__ == "__main__":
    serve()
