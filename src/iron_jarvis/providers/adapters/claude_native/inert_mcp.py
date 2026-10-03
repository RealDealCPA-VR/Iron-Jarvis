"""The tool INVENTORY the Claude CLI sees — inert by construction (v1.300.0).

Ported from NousResearch's MIT-licensed claude-subscription-directsdk plugin
(commit ef73726, ``inert_mcp.py``). The CLI is started with
``--strict-mcp-config`` and ONE server: this one. It answers ``initialize``,
lists this request's tools from a manifest file (``tools/list``), and refuses
every ``tools/call`` — the CLI also runs with ``--permission-mode dontAsk``,
so a call is denied twice over. Iron Jarvis's own loop, registry and
permission engine execute every tool; the CLI only ever emits ``tool_use``.

STDLIB ONLY, NO IRON JARVIS IMPORTS, NOTHING ON STDOUT BUT THE PROTOCOL. It is
spawned once per model call, so startup cost is paid on every request; and
stdout IS the JSON-RPC wire. Bytes in, bytes out: a frozen Windows build's
console encoding must never decode (or fail to decode) the CLI's UTF-8.

Run as ``python -m iron_jarvis.providers.adapters.claude_native.inert_mcp
<manifest.json>`` from source, or ``ironjarvis.exe claude-inert-mcp
<manifest.json>`` frozen (``sys.executable`` is then the app itself, whose
entry point is the Typer CLI — see :func:`inert_mcp_command`).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

MODULE = "iron_jarvis.providers.adapters.claude_native.inert_mcp"
SUBCOMMAND = "claude-inert-mcp"
DENIED = "Denied: tools are inert; Iron Jarvis executes tools."


def inert_mcp_command(manifest_path: str) -> list[str]:
    """The argv that starts this server, frozen or from source.

    Frozen (PyInstaller), ``sys.executable`` is ``ironjarvis.exe`` and its
    entry is the Typer app, which rejects ``-m`` — so the hidden subcommand
    (the v1.291.0 ``mcp-stdio`` precedent). From source it is the interpreter.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, SUBCOMMAND, str(manifest_path)]
    return [sys.executable, "-m", MODULE, str(manifest_path)]


def _reply(row: dict, manifest: list) -> dict | None:
    method = row.get("method")
    result: dict = {}
    if method == "initialize":
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "ironjarvis-inert-inventory", "version": "1"},
        }
    elif method == "tools/list":
        result = {"tools": manifest}
    elif method == "tools/call":
        result = {"isError": True, "content": [{"type": "text", "text": DENIED}]}
    if "id" not in row:
        return None  # a notification (notifications/initialized) gets no answer
    return {"jsonrpc": "2.0", "id": row["id"], "result": result}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        sys.stderr.write("usage: inert_mcp <manifest.json>\n")
        return 2
    manifest = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    out = sys.stdout.buffer
    for raw in sys.stdin.buffer:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        reply = _reply(row, manifest)
        if reply is not None:
            # ensure_ascii (the default): pure ASCII on the wire whatever the
            # descriptions hold.
            out.write((json.dumps(reply) + "\n").encode("ascii"))
            out.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
