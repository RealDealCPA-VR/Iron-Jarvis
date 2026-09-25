"""A REAL stdio MCP server for the v1.291.0 transport pins (no deps, no network).

Like ``echo_mcp_server.py`` it speaks line-delimited JSON-RPC 2.0 exactly as
``iron_jarvis.mcp.client.StdioTransport`` expects, and it writes RAW UTF-8 the
way a Node server does (``JSON.stringify`` never escapes non-ASCII). Each tool
name picks a behaviour the transport must survive:

* ``echo``     -> answer ``arguments.text`` (after ``arguments.delay_s`` seconds
                  when given: a slow-but-healthy server)
* ``hang``     -> never answer (a wedged server)
* ``accented`` -> answer text whose UTF-8 bytes include cp1252's undefined
                  0x81 (Á), the right curly quote (E2 80 9D) and an emoji
* ``die``      -> exit without answering (a crashed server)
* ``pid``      -> answer this process's pid (so a test can prove it was killed)

Run as ``python stdio_mcp_server_v1291.py``; with ``--launcher`` it instead
spawns ITSELF as a grandchild sharing its stdio and waits — the ``npx``/``uvx``
shape where the process the transport spawned is not the one holding the pipe.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

ACCENTED_TEXT = "Cliente: ÁLVAREZ, Íñigo — “1099” señor 😀"

_TOOLS = [
    {"name": n, "description": n, "inputSchema": {"type": "object", "properties": {}}}
    for n in ("echo", "hang", "accented", "die", "pid")
]

_out = sys.stdout.buffer


def _send(obj: dict) -> None:
    _out.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
    _out.flush()


def _text(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _call(name: str, args: dict) -> dict | None:
    if name == "echo":
        delay = float(args.get("delay_s") or 0)
        if delay > 0:
            time.sleep(delay)
        return _text(str(args.get("text", "")))
    if name == "hang":
        while True:
            time.sleep(3600)
    if name == "accented":
        return _text(ACCENTED_TEXT)
    if name == "die":
        os._exit(3)
    if name == "pid":
        return _text(str(os.getpid()))
    return {"content": [{"type": "text", "text": f"unknown tool {name!r}"}], "isError": True}


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
                "serverInfo": {"name": "stdio-v1291", "version": "1"}}})
        elif method == "tools/list":
            _send({"jsonrpc": "2.0", "id": mid, "result": {"tools": _TOOLS}})
        elif method == "tools/call":
            params = msg.get("params") or {}
            result = _call(params.get("name", ""), params.get("arguments") or {})
            _send({"jsonrpc": "2.0", "id": mid, "result": result})
        else:
            _send({"jsonrpc": "2.0", "id": mid, "result": {}})


def launch() -> int:
    """The launcher shape: the real server is a grandchild on the same pipes."""
    child = subprocess.Popen([sys.executable, __file__])  # noqa: S603
    return child.wait()


if __name__ == "__main__":
    if "--launcher" in sys.argv[1:]:
        raise SystemExit(launch())
    serve()
