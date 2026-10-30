"""A fake Claude Code / Codex / Pi for the Code-view tests (v1.409.0; Pi in
v1.411.0).

Replays a RECORDED session (``tests/fixtures/build_app_v1409/*.json``, masked
real output of claude 2.1.311 / codex-cli 0.157.0) over stdio, the way the
real harness spoke it:

* ``{"out": frame}`` — written to stdout as one JSON line;
* ``{"await": …}`` — blocks until the host sends the matching message:
  ``user`` (a stream-json user message), ``control`` + ``subtype`` (a
  control_request), ``response`` + ``request_id`` (a control_response to the
  harness's own can_use_tool), ``request`` + ``method`` (a JSON-RPC request),
  ``response`` + ``id`` (a JSON-RPC response to the harness's own request),
  ``command`` + ``command`` (a Pi RPC command of that type), ``ui`` + ``id``
  (Pi: the ``extension_ui_response`` to the dialog Pi sent with that id);
* ``reply_to_control`` / ``reply_to`` / ``reply_to_command``: the frame answers
  the host's request of that subtype / method / Pi command — its id is
  rewritten to the one the host sent.

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
            "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "IRONJARVIS_TOKEN", "IRONJARVIS_MCP_TOKEN",
            "PI_SESSION_ID", "PI_SESSION_FILE", "PI_CODING_AGENT")


def log(obj):
    if not LOG:
        return
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj) + "\n")


log({"argv": sys.argv[1:], "cwd": os.getcwd(), "pid": os.getpid(),
     "env": {k: os.environ.get(k) for k in ENV_KEYS if k in os.environ}})

with open(SCENARIO, encoding="utf-8") as fh:
    steps = json.load(fh)["steps"]

# Claude Code answers under the session id it was STARTED with (`--session-id`
# / `--resume`, measured live): the recorded id is swapped for it, so two
# sessions replaying one recording never share an id.
WANTED = ""
for flag in ("--session-id", "--resume"):
    if flag in sys.argv:
        WANTED = sys.argv[sys.argv.index(flag) + 1]
RECORDED = next((s["out"].get("session_id", "") for s in steps
                 if isinstance(s.get("out"), dict) and s["out"].get("type") == "system"
                 and s["out"].get("subtype") == "init"), "")
# Pi says its session id in the answer to `get_state`.
RECORDED = RECORDED or next((((s["out"].get("data") or {}).get("sessionId") or "") for s in steps
                             if isinstance(s.get("out"), dict) and s["out"].get("type") == "response"
                             and s["out"].get("command") == "get_state"), "")
#: Message types that are NOT Pi commands (Claude's / Codex's / Pi's answers).
NOT_PI = {"user", "control_request", "control_response", "extension_ui_response"}

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
commands = {}  # Pi command type -> [ids]


def matches(step, obj):
    kind = step["await"]
    if kind == "user":
        return obj.get("type") == "user"
    if kind == "control":
        return obj.get("type") == "control_request" and (obj.get("request") or {}).get("subtype") == step["subtype"]
    if kind == "request":
        return obj.get("method") == step["method"] and "id" in obj
    if kind == "command":
        return obj.get("type") == step["command"] and "method" not in obj
    if kind == "ui":
        return obj.get("type") == "extension_ui_response" and obj.get("id") == step["id"]
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
    elif obj.get("type") and obj.get("type") not in NOT_PI and "jsonrpc" not in obj:
        commands.setdefault(obj["type"], []).append(obj.get("id"))


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
    text = json.dumps(obj, ensure_ascii=False)
    if WANTED and RECORDED:
        text = text.replace(RECORDED, WANTED)
    sys.stdout.buffer.write(text.encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


def emit(step):
    obj = json.loads(json.dumps(step["out"]))
    sub = step.get("reply_to_control")
    if sub is not None and controls.get(sub):
        obj["response"]["request_id"] = controls[sub].pop(0)
    method = step.get("reply_to")
    if method and requests.get(method):
        obj["id"] = requests[method].pop(0)
    cmd = step.get("reply_to_command")
    if cmd and commands.get(cmd):
        obj["id"] = commands[cmd].pop(0)
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
    elif obj.get("type") and obj.get("type") not in NOT_PI and "jsonrpc" not in obj:
        # A Pi command the recording did not script: a plain success.
        out({"id": obj.get("id"), "type": "response", "command": obj["type"], "success": True})
