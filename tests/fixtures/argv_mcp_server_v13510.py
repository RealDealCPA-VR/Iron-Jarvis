"""A REAL stdio MCP server that reports the argv it was LAUNCHED with
(v1.351.0, G12). No deps, no network.

A pack whose secret lives in an argument (an ``mcp-remote`` block's
``--header "Authorization: Bearer …"``) is stored with the MASKED text in
config.toml and the real text in the vault; this server proves the launched
process got the REAL one: its single tool ``argv`` answers ``json.dumps(
sys.argv[1:])``. Speaks line-delimited JSON-RPC 2.0 exactly as
``iron_jarvis.mcp.client.StdioTransport`` expects.
"""

from __future__ import annotations

import json
import sys

_TOOLS = [{"name": "argv", "description": "the argv this server was launched with",
           "inputSchema": {"type": "object", "properties": {}}}]

_out = sys.stdout.buffer


def _send(obj: dict) -> None:
    _out.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
    _out.flush()


def serve() -> None:
    for raw in sys.stdin.buffer:
        line = raw.decode("utf-8").strip()
        if not line:
            continue
        msg = json.loads(line)
        mid = msg.get("id")
        if mid is None:
            continue  # a notification takes no reply
        method = msg.get("method")
        if method == "initialize":
            _send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                "serverInfo": {"name": "argv-v13510", "version": "1"}}})
        elif method == "tools/list":
            _send({"jsonrpc": "2.0", "id": mid, "result": {"tools": _TOOLS}})
        elif method == "tools/call":
            text = json.dumps(sys.argv[1:])
            _send({"jsonrpc": "2.0", "id": mid,
                   "result": {"content": [{"type": "text", "text": text}], "isError": False}})
        else:
            _send({"jsonrpc": "2.0", "id": mid, "result": {}})


if __name__ == "__main__":
    serve()
