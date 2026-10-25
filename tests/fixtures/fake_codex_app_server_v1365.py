"""A fake ``codex app-server`` for the IJ harness phase 2 native-adapter tests.

Speaks just enough of Codex 0.157.0's app-server JSON-RPC over stdio (one JSON
object per line): ``initialize``, ``config/read``, ``account/rateLimits/read``,
``thread/start``, ``mcpServerStatus/list``, ``turn/start`` — then plays a
SCRIPT of steps for the turn: agent-message deltas, reasoning deltas, token
usage, rate-limit notifications, ``item/tool/call`` server requests (each
waits for the client's answer), a built-in approval request, an error, a
crash, a hang. Ends with ``turn/completed``.

Invoked as ``<fake> app-server --listen stdio://`` (anything else: a usage
error on stderr and exit 2, like an old codex without the subcommand).

Environment:
  FAKE_CODEX_SCRIPT  path to JSON: {"processes": [proc0, proc1, ...]} — the Nth
                     process started (counted in FAKE_CODEX_STATE) plays
                     proc N (the last one repeats). A proc is
                     {"steps": [...] — or "turns": [[...], [...]], the Nth
                     turn/start of that process playing turns[N] (the last
                     repeats; part 3, Phase 2 review: a thread that goes on
                     after its turn ended) — and optionally "exit_on_start",
                     "initialize_error", "thread_start_error",
                     "thread_start" (fields merged into the thread answer),
                     "result" (fields merged into thread/start's result),
                     "mcp_servers" (names config/read lists), "mcp_tools"
                     ({server: {tool: {}}} listed by mcpServerStatus/list),
                     "model", "hang_before" ({method: seconds} slept
                     before answering that start request)}.
  FAKE_CODEX_STATE   a directory: the process counter lives there.
  FAKE_CODEX_RECORD  path: JSON lines appended per event (start, recv,
                     answer, approval).
  FAKE_CODEX_PIDS    path: "<pid> <grandchild pid>" written at start (the
                     grandchild sleeps 60 s — the tree a kill must reach).

Steps: {"text": s} | {"thinking": s} | {"call": {"tool": codex_name |
"tool_index": i, "args": {...}}} | {"usage": {inputTokens, cachedInputTokens,
outputTokens}} (ADDED to the thread's running total, then reported) |
{"rate_limits": {...}} | {"approval": method} | {"error": TurnError} |
{"die": code} | {"hang": seconds} | {"unknown_tool": name}.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid

RECORD = os.environ.get("FAKE_CODEX_RECORD", "")
SCRIPT = os.environ.get("FAKE_CODEX_SCRIPT", "")
STATE = os.environ.get("FAKE_CODEX_STATE", "")
PIDS = os.environ.get("FAKE_CODEX_PIDS", "")
PID = os.getpid()


_SEQ = [0]


def record(event: str, **data) -> None:
    """One file PER PROCESS (``<RECORD>.<pid>``): two fakes appending to one
    file at once can interleave on Windows. Rows carry a wall-clock stamp and
    a per-process sequence so the test can merge them in order."""
    if not RECORD:
        return
    _SEQ[0] += 1
    row = {"pid": PID, "event": event, "t": time.time(), "seq": _SEQ[0], **data}
    with open(f"{RECORD}.{PID}", "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def read() -> dict | None:
    line = sys.stdin.readline()
    if not line:
        return None
    try:
        msg = json.loads(line)
    except ValueError:
        return {}
    record("recv", msg=msg)
    return msg


def plan() -> dict:
    if not SCRIPT:
        return {"steps": [{"text": "Hello from the fake."}]}
    with open(SCRIPT, encoding="utf-8") as f:
        procs = json.load(f)["processes"]
    n = 0
    if STATE:
        # The Nth process to start plays proc N: count the marker files the
        # earlier ones left (no shared counter to race on), then leave ours.
        os.makedirs(STATE, exist_ok=True)
        n = len(os.listdir(STATE))
        open(os.path.join(STATE, f"started-{PID}"), "w").close()
    return procs[min(n, len(procs) - 1)]


def main() -> int:
    argv = sys.argv[1:]
    if argv[:1] != ["app-server"]:
        sys.stderr.write("error: unrecognized subcommand\n")
        return 2
    proc = plan()
    record("start", argv=argv, cwd=os.getcwd(), env={
        k: os.environ.get(k) for k in ("CODEX_HOME", "CODEX_API_KEY", "OPENAI_API_KEY") if k in os.environ
    })
    if proc.get("exit_on_start"):
        sys.stderr.write("error: unrecognized subcommand 'app-server'\n")
        return 2
    if PIDS:
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        with open(PIDS, "w", encoding="utf-8") as f:
            f.write(f"{PID} {child.pid}")
    model = proc.get("model", "gpt-fake-1")
    thread_id = "thr_" + uuid.uuid4().hex[:12]
    tools: list[str] = []
    total = {"inputTokens": 0, "cachedInputTokens": 0, "cacheWriteInputTokens": 0,
             "outputTokens": 0, "reasoningOutputTokens": 0, "totalTokens": 0}
    nid = [1000]

    def server_request(method: str, params: dict) -> dict | None:
        nid[0] += 1
        rid = nid[0]
        emit({"id": rid, "method": method, "params": params})
        while True:
            msg = read()
            if msg is None:
                return None
            if msg.get("id") == rid and "method" not in msg:
                return msg

    turns_run = [0]

    def run_turn(turn_id: str) -> int:
        emit({"method": "turn/started", "params": {"threadId": thread_id, "turn": {"id": turn_id, "items": [], "status": "inProgress"}}})
        n_item = 0
        if proc.get("turns"):
            steps = proc["turns"][min(turns_run[0], len(proc["turns"]) - 1)]
        else:
            steps = proc.get("steps", [])
        turns_run[0] += 1
        for step in steps:
            n_item += 1
            item_id = f"item_{n_item}"
            if "text" in step:
                text = step["text"]
                half = max(1, len(text) // 2)
                emit({"method": "item/started", "params": {"threadId": thread_id, "turnId": turn_id, "item": {"type": "agentMessage", "id": item_id, "text": ""}}})
                for piece in (text[:half], text[half:]):
                    if piece:
                        emit({"method": "item/agentMessage/delta", "params": {"threadId": thread_id, "turnId": turn_id, "itemId": item_id, "delta": piece}})
                emit({"method": "item/completed", "params": {"threadId": thread_id, "turnId": turn_id, "completedAtMs": 0, "item": {"type": "agentMessage", "id": item_id, "text": text}}})
            elif "thinking" in step:
                emit({"method": "item/reasoning/summaryTextDelta", "params": {"threadId": thread_id, "turnId": turn_id, "itemId": item_id, "summaryIndex": 0, "delta": step["thinking"]}})
            elif "usage" in step:
                for k, v in step["usage"].items():
                    total[k] = total.get(k, 0) + int(v)
                total["totalTokens"] = total["inputTokens"] + total["outputTokens"]
                emit({"method": "thread/tokenUsage/updated", "params": {"threadId": thread_id, "turnId": turn_id, "tokenUsage": {"total": dict(total), "last": dict(step["usage"])}}})
            elif "rate_limits" in step:
                emit({"method": "account/rateLimits/updated", "params": {"rateLimits": step["rate_limits"]}})
            elif "call" in step:
                spec = step["call"]
                name = spec.get("tool") or tools[spec.get("tool_index", 0)]
                call_id = "call_" + uuid.uuid4().hex[:20]
                params = {"threadId": thread_id, "turnId": turn_id, "callId": call_id,
                          "namespace": None, "tool": name, "arguments": spec.get("args", {})}
                emit({"method": "item/started", "params": {"threadId": thread_id, "turnId": turn_id, "item": {"type": "dynamicToolCall", "id": call_id, "tool": name, "arguments": params["arguments"], "status": "inProgress"}}})
                answer = server_request("item/tool/call", params)
                if answer is None:
                    return 0
                record("answer", callId=call_id, tool=name, response=answer.get("result"))
            elif "calls" in step:
                # Several requests outstanding at once (a batch), answered in any order.
                waiting: dict = {}
                for spec in step["calls"]:
                    name = spec.get("tool") or tools[spec.get("tool_index", 0)]
                    call_id = "call_" + uuid.uuid4().hex[:20]
                    nid[0] += 1
                    waiting[nid[0]] = (call_id, name)
                    emit({"id": nid[0], "method": "item/tool/call", "params": {
                        "threadId": thread_id, "turnId": turn_id, "callId": call_id,
                        "namespace": None, "tool": name, "arguments": spec.get("args", {})}})
                while waiting:
                    msg = read()
                    if msg is None:
                        return 0
                    if msg.get("id") in waiting and "method" not in msg:
                        call_id, name = waiting.pop(msg["id"])
                        record("answer", callId=call_id, tool=name, response=msg.get("result"))
            elif "unknown_tool" in step or "foreign_call" in step:
                # A tool nobody offered, or an offered tool asked for by ANOTHER thread.
                call_id = "call_" + uuid.uuid4().hex[:20]
                tool = step.get("unknown_tool") or tools[step["foreign_call"]]
                owner = thread_id if "unknown_tool" in step else "thr_someone_else"
                answer = server_request("item/tool/call", {"threadId": owner, "turnId": turn_id, "callId": call_id, "namespace": None, "tool": tool, "arguments": {}})
                record("answer", callId=call_id, tool=tool, response=(answer or {}).get("result"))
            elif "approval" in step:
                answer = server_request(step["approval"], {"threadId": thread_id, "turnId": turn_id, "itemId": item_id, "command": "rm -rf /"})
                record("approval", method=step["approval"], response=(answer or {}).get("result"))
            elif "error" in step:
                err = step["error"]
                emit({"method": "error", "params": {"threadId": thread_id, "turnId": turn_id, "willRetry": False, "error": err}})
                emit({"method": "turn/completed", "params": {"threadId": thread_id, "turn": {"id": turn_id, "items": [], "status": "failed", "error": err}}})
                return 0
            elif "stderr" in step:
                sys.stderr.write(step["stderr"] + "\n")
                sys.stderr.flush()
            elif "die" in step:
                os._exit(int(step["die"]))
            elif "hang" in step:
                time.sleep(float(step["hang"]))
        emit({"method": "turn/completed", "params": {"threadId": thread_id, "turn": {"id": turn_id, "items": [], "status": "completed"}}})
        return 0

    while True:
        msg = read()
        if msg is None:
            return 0
        method, rid = msg.get("method"), msg.get("id")
        params = msg.get("params") or {}
        if rid is None:
            continue  # a notification (initialized)
        # Review: a start request answered late (a busy PC), {method: seconds}.
        if method in (proc.get("hang_before") or {}):
            time.sleep(float(proc["hang_before"][method]))
        if method == "initialize":
            if proc.get("initialize_error"):
                emit({"id": rid, "error": {"code": -32600, "message": proc["initialize_error"]}})
                continue
            emit({"id": rid, "result": {"userAgent": "fake/0.157.0", "codexHome": os.environ.get("CODEX_HOME", "C:\\fake\\.codex"),
                                         "platformFamily": "windows", "platformOs": "windows"}})
        elif method == "config/read":
            servers = {n: {"command": "x"} for n in proc.get("mcp_servers", [])}
            emit({"id": rid, "result": {"config": {"mcp_servers": servers}}})
        elif method == "account/rateLimits/read":
            emit({"id": rid, "result": {"rateLimits": proc.get("read_limits", {
                "limitId": "codex", "planType": "plus",
                "primary": {"usedPercent": 7, "windowDurationMins": 300, "resetsAt": 1900000000},
                "secondary": {"usedPercent": 3, "windowDurationMins": 10080, "resetsAt": 1900500000}})}})
        elif method == "thread/start":
            if proc.get("thread_start_error"):
                emit({"id": rid, "error": {"code": -32602, "message": proc["thread_start_error"]}})
                continue
            tools[:] = [t.get("name") for t in params.get("dynamicTools") or []]
            thread = {"id": thread_id, "environments": params.get("environments"), "ephemeral": bool(params.get("ephemeral")),
                      "model": model, "status": {"type": "idle"}, "turns": []}
            thread.update(proc.get("thread_start", {}))
            result = {"thread": thread, "model": model, "modelProvider": "openai", "cwd": params.get("cwd"),
                      "sandbox": {"type": "readOnly", "networkAccess": False}, "approvalPolicy": params.get("approvalPolicy"),
                      "instructionSources": []}
            result.update(proc.get("result", {}))
            emit({"id": rid, "result": result})
        elif method == "mcpServerStatus/list":
            data = [{"name": n, "runtimeStatus": "disabled", "tools": {}, "resources": [], "resourceTemplates": [], "authStatus": "unsupported"}
                    for n in proc.get("mcp_servers", [])]
            for n, t in (proc.get("mcp_tools") or {}).items():
                data.append({"name": n, "runtimeStatus": "ready", "tools": t, "resources": [], "resourceTemplates": [], "authStatus": "unsupported"})
            emit({"id": rid, "result": {"data": data, "nextCursor": None}})
        elif method == "turn/start":
            turn_id = "turn_" + uuid.uuid4().hex[:12]
            emit({"id": rid, "result": {"turn": {"id": turn_id, "items": [], "status": "inProgress"}}})
            run_turn(turn_id)
        else:
            emit({"id": rid, "error": {"code": -32601, "message": f"unknown method {method}"}})


if __name__ == "__main__":
    sys.exit(main())
