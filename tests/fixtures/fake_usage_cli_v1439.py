"""A stand-in for ``claude -p --input-format stream-json …`` and ``codex
app-server`` answering ONE usage question (v1.439.0, "Check usage now").

Never the real CLIs. The shapes are the measured ones (2026-11-06):

* Claude Code 2.1.316: ``control_request`` ``initialize`` then ``get_usage``
  -> ``control_response {response: {subtype: success, request_id, response:
  {rate_limits {five_hour, seven_day {utilization 0..100, resets_at}}}}}``.
* Codex 0.157.0: ``initialize`` (id 1), ``initialized``, then
  ``account/rateLimits/read`` (id 2) -> ``{rateLimits {primary, secondary
  {usedPercent, windowDurationMins, resetsAt}, planType}}``.

Codex mode when ``app-server`` is in argv, else Claude mode. Environment:

* ``FAKE_USAGE_LOG`` — a file; one JSON line per start: argv, cwd, what the
  cwd held, the env names the test checks, the frames it read, and (hang)
  the pid of the grandchild it started.
* ``FAKE_USAGE_MODE`` — ``ok`` (default) | ``error`` (the CLI refuses) |
  ``shape`` (an answer with no windows) | ``hang`` (never answers; starts a
  sleeping grandchild first) | ``exit`` (stops before answering).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

ENV_NAMES = ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL",
             "CLAUDECODE", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC")


def _out(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main() -> int:
    codex = "app-server" in sys.argv
    mode = os.environ.get("FAKE_USAGE_MODE", "ok")
    record: dict = {
        "kind": "codex" if codex else "claude",
        "argv": sys.argv[1:],
        "cwd": os.getcwd(),
        "cwd_items": sorted(os.listdir(os.getcwd())),
        "env": {k: os.environ.get(k) for k in ENV_NAMES},
        "frames": [],
        "grandchild": None,
    }
    log_path = os.environ.get("FAKE_USAGE_LOG")

    def flush_log() -> None:
        if log_path:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")

    if mode == "hang":
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        record["grandchild"] = child.pid
    if mode == "exit":
        flush_log()
        return 0
    flushed = False
    for line in sys.stdin:
        try:
            frame = json.loads(line)
        except ValueError:
            continue
        record["frames"].append(frame)
        if codex:
            method = frame.get("method")
            if method == "initialize":
                _out({"id": frame.get("id"), "result": {"userAgent": "codex_cli_rs/0.157.0"}})
            elif method == "account/rateLimits/read":
                if not flushed:
                    flush_log()
                    flushed = True
                if mode == "hang":
                    time.sleep(120)
                elif mode == "error":
                    _out({"id": frame.get("id"), "error": {"code": -32600, "message": "not signed in"}})
                elif mode == "shape":
                    _out({"id": frame.get("id"), "result": {"something": "else"}})
                else:
                    _out({"id": frame.get("id"), "result": {"rateLimits": {
                        "primary": {"usedPercent": 37, "windowDurationMins": 300, "resetsAt": 4102444800},
                        "secondary": {"usedPercent": 12.5, "windowDurationMins": 10080, "resetsAt": 4102444800},
                        "planType": "plus",
                    }}})
            continue
        if frame.get("type") != "control_request":
            continue
        req = frame.get("request") or {}
        rid = frame.get("request_id")
        if req.get("subtype") == "initialize":
            _out({"type": "control_response",
                  "response": {"subtype": "success", "request_id": rid, "response": {"models": []}}})
        elif req.get("subtype") == "get_usage":
            if not flushed:
                flush_log()
                flushed = True
            if mode == "hang":
                time.sleep(120)
            elif mode == "error":
                _out({"type": "control_response",
                      "response": {"subtype": "error", "request_id": rid, "error": "Not logged in"}})
            elif mode == "shape":
                _out({"type": "control_response",
                      "response": {"subtype": "success", "request_id": rid, "response": {"limits": "?"}}})
            else:
                _out({"type": "control_response", "response": {
                    "subtype": "success", "request_id": rid, "response": {
                        "rate_limits": {
                            "five_hour": {"utilization": 42, "resets_at": "2100-01-01T00:00:00Z"},
                            "seven_day": {"utilization": 63.5, "resets_at": "2100-01-01T00:00:00Z"},
                        },
                        "subscription_type": "max",
                    }}})
    if not flushed:
        flush_log()
    return 0


if __name__ == "__main__":
    sys.exit(main())
