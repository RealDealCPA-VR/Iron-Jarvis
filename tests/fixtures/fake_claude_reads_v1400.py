"""A fake ``claude`` CLI that READS what the real one would (v1.400.0 review).

It is started exactly as the claude-cli adapter starts Claude Code
(``-p --input-format stream-json --output-format stream-json
--system-prompt-file <f> --settings <f> --mcp-config <f> ...``) and does what
Claude Code does with those files before it asks the model anything:

* reads the system prompt from ``--system-prompt-file``;
* reads the tools Iron Jarvis advertises to the API from ``--settings``
  (``env.CLAUDE_CODE_EXTRA_BODY``);
* STARTS the one MCP server ``--mcp-config`` names (Iron Jarvis's inert
  inventory) and asks it ``tools/list`` — the tool descriptions the model is
  shown arrive through that listing.

It APPENDS one JSON line to ``FAKE_READS_LOG`` — ``{"system", "body_tools",
"listed"}`` — and answers in text. Stdlib only; it calls no model.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def arg(flag: str) -> str:
    argv = sys.argv
    return argv[argv.index(flag) + 1] if flag in argv else ""


def listed_tools(mcp_config: str) -> list[dict]:
    with open(mcp_config, encoding="utf-8") as f:
        servers = json.load(f)["mcpServers"]
    (server,) = servers.values()
    rows = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    done = subprocess.run([server["command"], *server.get("args", [])],
                          input="".join(json.dumps(r) + "\n" for r in rows).encode("utf-8"),
                          capture_output=True, timeout=60)
    for line in done.stdout.decode("utf-8").splitlines():
        reply = json.loads(line)
        if reply.get("id") == 2:
            return reply["result"]["tools"]
    raise SystemExit("the inert server listed no tools: " + done.stderr.decode("utf-8", "replace"))


def main() -> int:
    emit({"type": "system", "subtype": "init", "model": "fake"})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        frame = json.loads(line)
        if frame.get("shouldQuery") is False:
            emit({"type": "result", "subtype": "success", "num_turns": 0, "is_error": False})
    with open(arg("--system-prompt-file"), encoding="utf-8") as f:
        system = f.read()
    with open(arg("--settings"), encoding="utf-8") as f:
        body = json.loads(json.load(f)["env"]["CLAUDE_CODE_EXTRA_BODY"])
    log = os.environ.get("FAKE_READS_LOG", "")
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"system": system, "body_tools": body.get("tools") or [],
                                "listed": listed_tools(arg("--mcp-config"))}) + "\n")
    text = "Done."
    usage = {"input_tokens": 3, "output_tokens": 1}
    emit({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                                             "delta": {"type": "text_delta", "text": text}}})
    emit({"type": "assistant", "message": {"id": "msg_1", "role": "assistant", "model": "fake",
                                           "content": [{"type": "text", "text": text}],
                                           "stop_reason": "end_turn", "usage": usage}})
    emit({"type": "stream_event", "event": {"type": "message_stop"}})
    emit({"type": "result", "subtype": "success", "is_error": False, "num_turns": 1, "usage": usage})
    return 0


if __name__ == "__main__":
    sys.exit(main())
