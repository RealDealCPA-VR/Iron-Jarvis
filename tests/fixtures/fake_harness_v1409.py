"""A fake Claude Code / Codex for the Code-view tests (v1.409.0).

Replays a RECORDED session (``tests/fixtures/build_app_v1409/*.json``, masked
real output of claude 2.1.311 / codex-cli 0.157.0) over stdio, the way the
real harness spoke it:

* ``{"out": frame}`` — written to stdout as one JSON line;
* ``{"await": …}`` — blocks until the host sends the matching message:
  ``user`` (a stream-json user message), ``control`` + ``subtype`` (a
  control_request), ``response`` + ``request_id`` (a control_response to the
  harness's own can_use_tool), ``request`` + ``method`` (a JSON-RPC request),
  ``response`` + ``id`` (a JSON-RPC response to the harness's own request);
* ``reply_to_control`` / ``reply_to``: the frame answers the host's request of
  that subtype / method — its id is rewritten to the one the host sent.

Every line the host sends is appended to ``IJ_FAKE_LOG`` (JSON lines), after a
first line with the argv, the folder and the account-related environment, so
the tests assert on what really reached the harness. Ends on stdin EOF.
"""

import json
import os
import queue
import sys
import threading

SCENARIO = os.environ["IJ_FAKE_SCENARIO"]
LOG = os.environ.get("IJ_FAKE_LOG", "")
ENV_KEYS = ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "CLAUDECODE", "CLAUDE_CODE_SESSION_ID",
            "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "IRONJARVIS_TOKEN", "IRONJARVIS_MCP_TOKEN")


def log(obj):
    if not LOG:
        return
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj) + "\n")


log({"argv": sys.argv[1:], "cwd": os.getcwd(), "pid": os.getpid(),
     "env": {k: os.environ.get(k) for k in ENV_KEYS if k in os.environ}})

with open(SCENARIO, encoding="utf-8") as fh:
    steps = json.load(fh)["steps"]

inbox: "queue.Queue" = queue.Queue()


def read_stdin():
    for raw in sys.stdin.buffer:
        text = raw.decode("utf-8", "replace").strip()
        if not text:
            continue
        try:
            obj = json.loads(text)
        except ValueError:
            continue
        log({"in": obj})
        inbox.put(obj)
    inbox.put(None)


threading.Thread(target=read_stdin, daemon=True).start()
buffer = []
controls = {}  # subtype -> [request ids]
requests = {}  # method -> [ids]


def matches(step, obj):
    kind = step["await"]
    if kind == "user":
        return obj.get("type") == "user"
    if kind == "control":
        return obj.get("type") == "control_request" and (obj.get("request") or {}).get("subtype") == step["subtype"]
    if kind == "request":
        return obj.get("method") == step["method"] and "id" in obj
    if kind == "response":
        if "request_id" in step:
            return obj.get("type") == "control_response" and (obj.get("response") or {}).get("request_id") == step["request_id"]
        return "method" not in obj and obj.get("id") == step["id"]
    return False


def remember(obj):
    if obj.get("type") == "control_request":
        controls.setdefault((obj.get("request") or {}).get("subtype"), []).append(obj.get("request_id"))
    elif "method" in obj and "id" in obj:
        requests.setdefault(obj["method"], []).append(obj["id"])


def wait_for(step):
    while True:
        for i, obj in enumerate(buffer):
            if matches(step, obj):
                buffer.pop(i)
                remember(obj)
                return True
        obj = inbox.get()
        if obj is None:
            return False
        buffer.append(obj)


def out(obj):
    """UTF-8 bytes, as the real harnesses write (measured)."""
    sys.stdout.buffer.write(json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


def emit(step):
    obj = json.loads(json.dumps(step["out"]))
    sub = step.get("reply_to_control")
    if sub is not None and controls.get(sub):
        obj["response"]["request_id"] = controls[sub].pop(0)
    method = step.get("reply_to")
    if method and requests.get(method):
        obj["id"] = requests[method].pop(0)
    out(obj)


for step in steps:
    if "await" in step:
        if not wait_for(step):
            sys.exit(0)
    else:
        emit(step)

# The script is spent: answer anything else plainly until the host closes stdin.
while True:
    obj = buffer.pop(0) if buffer else inbox.get()
    if obj is None:
        sys.exit(0)
    if obj.get("type") == "control_request":
        out({"type": "control_response", "response": {
            "subtype": "success", "request_id": obj.get("request_id"), "response": {}}})
    elif "method" in obj and "id" in obj:
        out({"jsonrpc": "2.0", "id": obj["id"], "result": {}})
