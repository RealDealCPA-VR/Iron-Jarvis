"""A REAL stdio MCP server whose TOOL LIST CAN CHANGE between loads (v1.299.0).

Like ``echo_mcp_server.py`` it speaks line-delimited JSON-RPC 2.0 exactly as
``iron_jarvis.mcp.client.StdioTransport`` expects — but its ``tools/list`` is
read from the JSON file named by ``argv[1]`` on EVERY request, so a test can
rewrite that file between two boots and stand in for "the pack shipped an
update that grew a new tool". Each tool entry is a raw MCP spec (``name``,
``description``, ``inputSchema``, optional ``annotations``); ``tools/call``
answers ``<name>:<json args>`` for any listed tool.

Run as ``python mutable_mcp_server_v1299.py <tools.json>``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_TOOLS_FILE = Path(sys.argv[1]) if len(sys.argv) > 1 else None


def _tools() -> list[dict]:
    if _TOOLS_FILE is None:
        return []
    try:
        data = json.loads(_TOOLS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _text(text: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _handle(method: str, params: dict) -> dict:
    if method == "initialize":
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "mutable-mcp", "version": "1"},
        }
    if method == "tools/list":
        return {"tools": _tools()}
    if method == "tools/call":
        name = str(params.get("name") or "")
        if name not in {t.get("name") for t in _tools()}:
            return _text(f"unknown tool '{name}'", is_error=True)
        return _text(f"{name}:{json.dumps(params.get('arguments') or {}, sort_keys=True)}")
    return {}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "id" not in msg:  # a notification: no response
            continue
        result = _handle(str(msg.get("method") or ""), msg.get("params") or {})
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
