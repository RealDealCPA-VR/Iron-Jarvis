"""A fake ``claude`` CLI for the v1.300.0 native stream-json adapter tests.

Speaks just enough of Claude Code's ``-p --input-format stream-json
--output-format stream-json`` protocol: reads the frames on stdin, answers
every ``shouldQuery: false`` frame with a zero-turn ``result`` (the history
replay acknowledgment, as CLI 2.1.288 does on this machine), then emits a
SCRIPTED answer chosen by ``FAKE_CLAUDE_SCENARIO``. It records what it saw —
argv, the environment keys that matter, cwd, the frames, and the contents of
the files argv points at — to ``FAKE_CLAUDE_RECORD`` (JSON) for the test.

NO NETWORK, except the ``relay`` / ``relay_error`` scenarios, which POST to
the loopback admission relay in ``ANTHROPIC_BASE_URL`` (pointed by the test at
a loopback fake upstream, or at an https URL behind a loopback fake proxy)
exactly like native does.

Scenarios: text (default), tools, thinking, auth, api_error, hang, silent,
mismatch, unknown_tool, no_ack, relay, relay_error, max_tokens, budget.
Knobs: FAKE_CLAUDE_CALLS (JSON [[native_name, input], ...] for ``tools``),
FAKE_CLAUDE_COST ("none" | "nan" | a number), FAKE_CLAUDE_PIDS (a path: the
fake writes "<pid> <grandchild pid>" there in ``hang``), FAKE_CLAUDE_AUTH_TEXT
(the words of the ``auth`` refusal). ``relay_error`` (the relay's first
attempt failed: native renders its own error, never an answer):
FAKE_CLAUDE_NATIVE_ERROR (the assistant ``error`` code, default ``unknown``),
FAKE_CLAUDE_NATIVE_TEXT (its words), FAKE_CLAUDE_STREAMED (text native had
already streamed before the failure, default none).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

SCENARIO = os.environ.get("FAKE_CLAUDE_SCENARIO", "text")
RECORD = os.environ.get("FAKE_CLAUDE_RECORD", "")
ENV_KEYS = (
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
    "ANTHROPIC_FOUNDRY_API_KEY", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_EXTRA_BODY", "CLAUDE_CODE_EFFORT_LEVEL",
    "ENABLE_TOOL_SEARCH", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
    "CLAUDE_CODE_MAX_RETRIES", "DISABLE_AUTO_COMPACT", "DISABLE_COMPACT",
    "CLAUDE_CODE_TOTAL_TOKENS_REMINDER", "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
    "NO_PROXY", "no_proxy", "HTTP_PROXY", "HTTPS_PROXY",
)


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def delta(kind: str, field: str, text: str) -> dict:
    return {"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                                               "delta": {"type": kind, field: text}}}


def arg(flag: str) -> str | None:
    argv = sys.argv[1:]
    return argv[argv.index(flag) + 1] if flag in argv and argv.index(flag) + 1 < len(argv) else None


def read_file(flag: str) -> str | None:
    path = arg(flag)
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def usage(**extra) -> dict:
    u = {"input_tokens": 3, "output_tokens": 5, "cache_read_input_tokens": 7,
         "cache_creation_input_tokens": 11, "output_tokens_details": {"thinking_tokens": 4}}
    u.update(extra)
    return u


def result(subtype: str = "success", *, is_error: bool = False, num_turns: int = 1, **extra) -> dict:
    row = {"type": "result", "subtype": subtype, "is_error": is_error, "num_turns": num_turns,
           "usage": usage()}
    cost = os.environ.get("FAKE_CLAUDE_COST", "0.012345")
    if cost == "nan":
        row["total_cost_usd"] = float("nan")
    elif cost != "none":
        row["total_cost_usd"] = float(cost)
    row.update(extra)
    return row


def main() -> int:
    frames: list[dict] = []
    record: dict = {
        "argv": sys.argv[1:],
        "cwd": os.getcwd(),
        "env": {k: v for k, v in os.environ.items()
                if k in ENV_KEYS or k.startswith(("ANTHROPIC_", "CLAUDE_CODE_"))},
        "settings": read_file("--settings"),
        "system": read_file("--system-prompt-file"),
        "mcp": read_file("--mcp-config"),
        "frames": frames,
        "pid": os.getpid(),
    }
    mcp = record["mcp"]
    if mcp:
        try:
            server = json.loads(mcp)["mcpServers"]["ij"]
            record["manifest"] = json.loads(open(server["args"][-1], encoding="utf-8").read())
        except Exception as exc:  # noqa: BLE001
            record["manifest_error"] = repr(exc)

    def save() -> None:
        if RECORD:
            with open(RECORD + ".tmp", "w", encoding="utf-8") as f:
                json.dump(record, f)
            os.replace(RECORD + ".tmp", RECORD)

    save()
    emit({"type": "system", "subtype": "init", "model": arg("--model") or "default"})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        frame = json.loads(line)
        frames.append(frame)
        if frame.get("shouldQuery") is False:
            turns = 1 if SCENARIO == "no_ack" else 0
            emit({"type": "result", "subtype": "success", "num_turns": turns, "is_error": False})
    save()

    if SCENARIO == "hang":
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        pids = os.environ.get("FAKE_CLAUDE_PIDS")
        if pids:
            with open(pids + ".tmp", "w") as f:
                f.write(f"{os.getpid()} {child.pid}")
            os.replace(pids + ".tmp", pids)
        emit(delta("text_delta", "text", "started"))
        time.sleep(60)
        return 0
    if SCENARIO == "silent":
        time.sleep(60)
        return 0
    if SCENARIO == "auth":
        text = os.environ.get("FAKE_CLAUDE_AUTH_TEXT") or "Not logged in · Please run /login"
        emit({"type": "assistant", "error": "authentication_failed", "is_api_error_message": True,
              "message": {"role": "assistant", "model": "<synthetic>",
                          "content": [{"type": "text", "text": text}], "stop_reason": "stop_sequence"}})
        emit({"type": "result", "subtype": "success", "is_error": True, "num_turns": 1, "result": text})
        return 1
    if SCENARIO == "api_error":
        text = "API Error: 529 overloaded"
        emit({"type": "assistant", "error": "unknown",
              "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}})
        emit({"type": "result", "subtype": "success", "is_error": True, "num_turns": 1, "result": text})
        return 1
    if SCENARIO == "budget":
        emit({"type": "result", "subtype": "error_max_budget_usd", "is_error": True, "num_turns": 1,
              "result": "Reached the --max-budget-usd ceiling"})
        return 1
    if SCENARIO in ("relay", "relay_error"):
        url = os.environ["ANTHROPIC_BASE_URL"] + "/v1/messages"
        statuses = []
        for _ in range(2):  # native "recovery" tries a second request; the relay refuses it
            req = urllib.request.Request(url, data=b'{"messages":[]}',
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    resp.read()
                    statuses.append(resp.status)
            except urllib.error.HTTPError as exc:
                statuses.append(exc.code)
            except Exception as exc:  # noqa: BLE001 — the relay hung up: no status at all
                statuses.append("dropped:" + type(exc).__name__)
        record["relay_statuses"] = statuses
        save()
    if SCENARIO == "relay_error":
        streamed = os.environ.get("FAKE_CLAUDE_STREAMED", "")
        if streamed:
            emit(delta("text_delta", "text", streamed))
        text = os.environ.get("FAKE_CLAUDE_NATIVE_TEXT") or "API Error: Connection error."
        emit({"type": "assistant", "error": os.environ.get("FAKE_CLAUDE_NATIVE_ERROR") or "unknown",
              "is_api_error_message": True,
              "message": {"role": "assistant", "model": "<synthetic>",
                          "content": [{"type": "text", "text": text}], "stop_reason": "stop_sequence"}})
        emit({"type": "result", "subtype": "success", "is_error": True, "num_turns": 1, "result": text})
        return 1
    if SCENARIO == "relay":
        # What native renders after a denied retry: its OWN (different) words.
        emit(delta("text_delta", "text", "FIRST"))
        emit({"type": "assistant", "message": {"id": "native", "role": "assistant",
                                               "content": [{"type": "text", "text": "FIRST"}],
                                               "stop_reason": "end_turn", "usage": usage()}})
        emit({"type": "stream_event", "event": {"type": "message_stop"}})
        emit(result())
        return 0

    stop = "end_turn"
    if SCENARIO == "tools":
        calls = json.loads(os.environ.get("FAKE_CLAUDE_CALLS", "[]"))
        blocks = [{"type": "thinking", "thinking": "private plan", "signature": "sig-abc"},
                  {"type": "text", "text": "Checking both."}]
        blocks += [{"type": "tool_use", "id": f"toolu_{i:02d}", "name": n, "input": inp}
                   for i, (n, inp) in enumerate(calls)]
        streamed = "Checking both."
        stop = "tool_use"
    elif SCENARIO == "unknown_tool":
        blocks = [{"type": "tool_use", "id": "toolu_x", "name": "mcp__ij__not_offered", "input": {}}]
        streamed = ""
        stop = "tool_use"
    elif SCENARIO == "thinking":
        blocks = [{"type": "thinking", "thinking": "hmm", "signature": "sig-1"},
                  {"type": "text", "text": "Hello"}]
        streamed = "Hello"
    elif SCENARIO == "mismatch":
        blocks = [{"type": "text", "text": "xyz"}]
        streamed = "abc"
    elif SCENARIO == "max_tokens":
        blocks = [{"type": "text", "text": "cut"}]
        streamed = "cut"
        stop = "max_tokens"
    else:  # text
        blocks = [{"type": "text", "text": "Hello"}]
        streamed = "Hello"
    for b in blocks:
        if b["type"] == "thinking":
            emit(delta("thinking_delta", "thinking", b["thinking"]))
    if streamed:
        half = max(1, len(streamed) // 2)
        for piece in (streamed[:half], streamed[half:]):
            if piece:
                emit(delta("text_delta", "text", piece))
    emit({"type": "assistant", "message": {"id": "msg_fake", "role": "assistant", "model": "fake",
                                           "content": blocks, "stop_reason": stop, "usage": usage()}})
    emit({"type": "stream_event", "event": {"type": "message_stop"}})
    if SCENARIO in ("tools", "unknown_tool"):
        # --max-turns 1 with a tool batch: error_max_turns + exit 1 (the boundary).
        emit(result("error_max_turns", is_error=True, num_turns=2))
        return 1
    emit(result())
    return 0


if __name__ == "__main__":
    sys.exit(main())
