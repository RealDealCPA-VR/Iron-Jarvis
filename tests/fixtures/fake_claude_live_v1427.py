"""A fake ``claude`` CLI that speaks the LIVE protocol D4 relies on (v1.427.0).

What the coordinator measured on the real Claude Code 2.1.316
(``docs/CLAUDE-LIVE-SESSION.md``), reduced to what a test needs:

* It reads stream-json frames on stdin FOR AS LONG AS STDIN IS OPEN. A
  ``shouldQuery: false`` user frame is history: acknowledged with a zero-turn
  ``result``. An assistant frame is history (no acknowledgment). Any other user
  frame starts a QUERY.
* A query POSTs the CLI's WHOLE history (``messages``; the last block of the
  last message carries ``cache_control``, as native puts it) to
  ``$ANTHROPIC_BASE_URL/v1/messages`` with its own timeout
  (``API_TIMEOUT_MS``), and passes the answer's SSE events through as
  ``stream_event`` lines, then the ``assistant`` message.
* An answer that asks for tools: ``--permission-mode dontAsk`` denies them —
  ``system/permission_denied``, then a ``user`` line with one ``tool_result``
  per call ("Permission to use <tool> has been denied ...", ``is_error``),
  appended to its history — and it POSTs again AT ONCE (the relay holds that
  request). After ``--max-turns`` turns it ends ``error_max_turns`` instead.
* An answer in text ends the query: ``result`` (success, ``total_cost_usd``
  for the WHOLE query), then it waits on stdin for the next message.
* A refused or dropped request: native's own error words + ``result`` is_error.
* stdin EOF: exit 0 (1 after an error or an ``error_max_turns``).
* ``FAKE_LIVE_NO_FOLLOW_UP=1``: after a denial it does NOT post the follow-up
  (it waits on stdin) — a CLI that no longer behaves as measured.
* ``FAKE_LIVE_EAGER=1`` (v1.427.0 fix, what 2.1.316 really does): each tool is
  denied the MOMENT its ``tool_use`` block is complete — one ``user`` line per
  tool — while the model may still be streaming the rest of the message; the
  answer's SSE is read as it arrives. The follow-up still carries ONE user turn
  with every denial.

NO NETWORK beyond the loopback relay in ``ANTHROPIC_BASE_URL``. Every start
APPENDS one JSON line to ``FAKE_LIVE_LOG``: ``{"event": "start", "pid", "argv",
"api_timeout_ms", "config_dir"}``.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import urllib.error
import urllib.request

DENIED = (
    "Permission to use {name} has been denied because Claude Code is running in don't ask mode. "
    "IMPORTANT: You *may* attempt to accomplish this action using other tools that might naturally "
    "be used to accomplish this goal."
)


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def arg(flag: str) -> str | None:
    argv = sys.argv[1:]
    return argv[argv.index(flag) + 1] if flag in argv and argv.index(flag) + 1 < len(argv) else None


def read(flag: str) -> str:
    path = arg(flag)
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def log(entry: dict) -> None:
    path = os.environ.get("FAKE_LIVE_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


def assemble(events: list[dict]) -> dict:
    message: dict = {}
    args: dict[int, str] = {}
    for ev in events:
        kind = ev.get("type")
        if kind == "message_start":
            message = copy.deepcopy(ev["message"])
        elif kind == "content_block_start":
            message["content"].append(copy.deepcopy(ev["content_block"]))
        elif kind == "content_block_delta":
            delta = ev["delta"]
            block = message["content"][ev["index"]]
            if delta["type"] == "text_delta":
                block["text"] = block.get("text", "") + delta["text"]
            elif delta["type"] == "thinking_delta":
                block["thinking"] = block.get("thinking", "") + delta["thinking"]
            elif delta["type"] == "input_json_delta":
                args[ev["index"]] = args.get(ev["index"], "") + delta["partial_json"]
        elif kind == "content_block_stop":
            if ev["index"] in args:
                raw = args.pop(ev["index"])
                message["content"][ev["index"]]["input"] = json.loads(raw) if raw.strip() else {}
        elif kind == "message_delta":
            message.update(ev.get("delta", {}))
            message.setdefault("usage", {}).update(ev.get("usage", {}))
    return message


def main() -> int:
    max_turns = int(arg("--max-turns") or "1")
    model = arg("--model") or "default"
    try:
        extra = json.loads(json.loads(read("--settings"))["env"]["CLAUDE_CODE_EXTRA_BODY"])
    except (ValueError, KeyError, TypeError):
        extra = {}
    system = read("--system-prompt-file")
    timeout = int(os.environ.get("API_TIMEOUT_MS") or "600000") / 1000.0
    log({"event": "start", "pid": os.getpid(), "argv": sys.argv[1:],
         "api_timeout_ms": os.environ.get("API_TIMEOUT_MS"),
         "config_dir": os.environ.get("CLAUDE_CONFIG_DIR")})
    url = os.environ["ANTHROPIC_BASE_URL"] + "/v1/messages"
    history: list[dict] = []
    emit({"type": "system", "subtype": "init", "model": model})

    eager = bool(os.environ.get("FAKE_LIVE_EAGER"))

    def deny(block: dict) -> dict:
        return {"type": "tool_result", "tool_use_id": block["id"],
                "content": DENIED.format(name=block["name"]), "is_error": True}

    def post(body: dict) -> tuple[int | None, bytes, list[dict]]:
        """``(status, error body, events)`` — a 200's SSE events are passed
        through as ``stream_event`` lines AS THEY ARRIVE; in eager mode each
        complete tool_use block is denied at once."""
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        events: list[dict] = []
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                lines: list[str] = []
                for raw in resp:
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    if line:
                        lines.append(line)
                        continue
                    data = "\n".join(x[5:].lstrip(" ") for x in lines if x.startswith("data:"))
                    lines = []
                    if not data:
                        continue
                    ev = json.loads(data)
                    events.append(ev)
                    emit({"type": "stream_event", "event": ev})
                    if eager and ev.get("type") == "content_block_stop":
                        block = assemble(events)["content"][ev["index"]]
                        if block.get("type") == "tool_use":
                            emit({"type": "assistant", "message": {"role": "assistant", "content": [block]}})
                            emit({"type": "system", "subtype": "permission_denied"})
                            emit({"type": "user", "message": {"role": "user", "content": [deny(block)]}})
                return resp.status, b"", events
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), []
        except Exception as exc:  # noqa: BLE001 — the relay hung up
            return None, type(exc).__name__.encode(), []

    def query() -> str:
        turns = 0
        while True:
            messages = copy.deepcopy(history)
            last = messages[-1]["content"]
            if isinstance(last, list) and last and isinstance(last[-1], dict):
                last[-1]["cache_control"] = {"type": "ephemeral"}
            body = {"model": model, "system": system, "messages": messages, "stream": True,
                    "tools": extra.get("tools") or []}
            if extra.get("output_config"):
                body["output_config"] = extra["output_config"]
            status, raw, events = post(body)
            if status != 200:
                text = "API Error: Connection error." if status is None else f"API Error: {status} {raw[:200]!r}"
                emit({"type": "assistant", "error": "unknown", "is_api_error_message": True,
                      "message": {"role": "assistant", "model": "<synthetic>",
                                  "content": [{"type": "text", "text": text}], "stop_reason": "stop_sequence"}})
                emit({"type": "result", "subtype": "success", "is_error": True, "num_turns": turns + 1,
                      "result": text, "total_cost_usd": 0.5})
                return "error"
            message = assemble(events)
            if not eager or message.get("stop_reason") != "tool_use":
                emit({"type": "assistant", "message": message})
            history.append({"role": "assistant", "content": message.get("content") or []})
            turns += 1
            if message.get("stop_reason") == "tool_use":
                uses = [b for b in message.get("content") or [] if b.get("type") == "tool_use"]
                if turns >= max_turns:
                    emit({"type": "result", "subtype": "error_max_turns", "is_error": True,
                          "num_turns": turns, "total_cost_usd": 0.5,
                          "usage": message.get("usage") or {}})
                    return "error_max_turns"
                denials = [deny(b) for b in uses]
                if not eager:  # (eager: each was denied as its block completed)
                    emit({"type": "system", "subtype": "permission_denied"})
                    emit({"type": "user", "message": {"role": "user", "content": denials}})
                emit({"type": "rate_limit_event"})
                history.append({"role": "user", "content": denials})
                if os.environ.get("FAKE_LIVE_NO_FOLLOW_UP"):
                    return "no_follow_up"  # a CLI that no longer behaves as measured
                continue
            text = "".join(b.get("text", "") for b in message.get("content") or [] if b.get("type") == "text")
            emit({"type": "result", "subtype": "success", "is_error": False, "num_turns": turns,
                  "result": text, "total_cost_usd": 0.5, "usage": message.get("usage") or {}})
            return "success"

    last = ""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        frame = json.loads(line)
        msg = frame.get("message") or {}
        history.append({"role": msg.get("role"), "content": copy.deepcopy(msg.get("content"))})
        if frame.get("type") == "assistant":
            continue
        if frame.get("shouldQuery") is False:
            emit({"type": "result", "subtype": "success", "num_turns": 0, "is_error": False})
            continue
        last = query()
    return 1 if last in ("error", "error_max_turns") else 0


if __name__ == "__main__":
    sys.exit(main())
