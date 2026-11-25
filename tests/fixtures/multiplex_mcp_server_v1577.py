"""A REAL stdio MCP server that answers calls CONCURRENTLY, for the v1.577.0
multiplexing pins (no deps, no network).

Every ``tools/call`` runs on a thread of its own, so a slow call does not hold
up a fast one on the SERVER side either: what the pins measure is the client.
Tools (``arguments.tag`` labels a call in the answers and in the log):

* ``fast``          -> answer ``fast:<tag>`` at once
* ``slow``          -> answer ``slow:<tag>`` after ``delay_s`` seconds when
                       given, else once ``release`` is called for the same tag
                       (``guard_s``, default 60, bounds the wait)
* ``release``       -> release the ``slow`` call(s) with ``tag``
* ``progress``      -> ``count`` progress notifications for the call's own
                       ``progressToken`` (and one for a FOREIGN token), then
                       answer the token it saw
* ``list_changed``  -> send ``notifications/tools/list_changed``, then answer
* ``cancelled_log`` -> answer every ``notifications/cancelled`` received, as
                       ``[{requestId, reason, tool, tag}]`` (JSON text)
* ``hang``          -> never answer (a wedged call)
* ``hang_chatty``   -> send a ``notifications/message`` log line, then never
                       answer (busy, not wedged)
* ``die``           -> exit with code 3 without answering

``initialize`` answers with :data:`INSTRUCTIONS`, ``tools.listChanged`` and a
``serverInfo``.
"""

from __future__ import annotations

import json
import os
import sys
import threading

INSTRUCTIONS = "Use fast for quick answers; slow waits until release is called with its tag."
SERVER_INFO = {"name": "multiplex-v1577", "version": "1.0"}

_NAMES = ("fast", "slow", "release", "progress", "list_changed", "cancelled_log",
          "hang", "hang_chatty", "die")
_TOOLS = [
    {"name": n, "description": n, "inputSchema": {"type": "object", "properties": {}}}
    for n in _NAMES
]

_out = sys.stdout.buffer
_wlock = threading.Lock()
_state = threading.Lock()
_events: dict[str, threading.Event] = {}
_calls: dict[object, tuple[str, str]] = {}  # id -> (tool, tag), recorded on arrival
_cancelled: list[dict] = []
_forever = threading.Event()


def _send(obj: dict) -> None:
    data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
    with _wlock:
        _out.write(data)
        _out.flush()


def _text(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _event(tag: str) -> threading.Event:
    with _state:
        return _events.setdefault(tag, threading.Event())


def _tool(name: str, args: dict, token) -> dict:
    tag = str(args.get("tag", ""))
    if name == "fast":
        return _text(f"fast:{tag}")
    if name == "slow":
        delay = args.get("delay_s")
        if delay:
            _forever.wait(float(delay))
        else:
            _event(tag).wait(float(args.get("guard_s") or 60))
        return _text(f"slow:{tag}")
    if name == "release":
        _event(tag).set()
        return _text("released")
    if name == "progress":
        for i in range(int(args.get("count") or 1)):
            _send({"jsonrpc": "2.0", "method": "notifications/progress",
                   "params": {"progressToken": token, "progress": i + 1}})
        _send({"jsonrpc": "2.0", "method": "notifications/progress",
               "params": {"progressToken": "someone-else", "progress": 1}})
        return _text(str(token))
    if name == "list_changed":
        _send({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
        return _text("sent")
    if name == "cancelled_log":
        with _state:
            return _text(json.dumps(list(_cancelled)))
    if name == "hang":
        _forever.wait()
    if name == "hang_chatty":
        _send({"jsonrpc": "2.0", "method": "notifications/message",
               "params": {"level": "info", "data": "working on it"}})
        _forever.wait()
    if name == "die":
        os._exit(3)
    return {"content": [{"type": "text", "text": f"unknown tool {name!r}"}], "isError": True}


def _handle(mid, params: dict) -> None:
    name = str(params.get("name", ""))
    args = params.get("arguments") or {}
    token = (params.get("_meta") or {}).get("progressToken")
    result = _tool(name, args, token)
    _send({"jsonrpc": "2.0", "id": mid, "result": result})


def serve() -> None:
    for raw in sys.stdin.buffer:
        line = raw.decode("utf-8").strip()
        if not line:
            continue
        msg = json.loads(line)
        mid = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}
        if mid is None:
            if method == "notifications/cancelled":
                rid = params.get("requestId")
                with _state:
                    tool, tag = _calls.get(rid, ("?", "?"))
                    _cancelled.append({"requestId": rid, "reason": params.get("reason"),
                                       "tool": tool, "tag": tag})
            continue
        if method == "initialize":
            _send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": SERVER_INFO,
                "instructions": INSTRUCTIONS}})
        elif method == "tools/list":
            _send({"jsonrpc": "2.0", "id": mid, "result": {"tools": _TOOLS}})
        elif method == "tools/call":
            with _state:
                _calls[mid] = (str(params.get("name", "")),
                               str((params.get("arguments") or {}).get("tag", "")))
            threading.Thread(target=_handle, args=(mid, params), daemon=True).start()
        else:
            _send({"jsonrpc": "2.0", "id": mid, "result": {}})


if __name__ == "__main__":
    serve()
