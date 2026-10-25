"""A fake ``claude`` CLI that answers a MULTI-ROUND tool loop (v1.384.0).

Speaks the slice of Claude Code's ``-p --input-format stream-json
--output-format stream-json`` protocol the v1.300.0 fixture speaks: it reads
the replayed frames, acknowledges each ``shouldQuery: false`` frame with a
zero-turn ``result``, then answers.

THE ROUND is the number of assistant frames it was sent (round 0 = none).
Before the last round (``FAKE_ROUNDS`` total, default 3) it thinks — a signed
thinking block, ``sig-round-<n>`` — and calls ``mcp__ij__Read`` on
``FAKE_READ_PATH`` (default ``app.py``); on the last round it thinks and
answers in text. Every invocation APPENDS one JSON line to
``FAKE_ROUNDS_LOG``: ``{"round": n, "frames": [...]}`` — the frames exactly as
the adapter sent them — so a test can read whether round n was sent the
EARLIER rounds' signed thinking.
"""

from __future__ import annotations

import json
import os
import sys


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def delta(kind: str, field: str, text: str) -> dict:
    return {"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                                               "delta": {"type": kind, field: text}}}


def usage() -> dict:
    return {"input_tokens": 3, "output_tokens": 5}


def main() -> int:
    frames: list[dict] = []
    emit({"type": "system", "subtype": "init", "model": "fake"})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        frame = json.loads(line)
        frames.append(frame)
        if frame.get("shouldQuery") is False:
            emit({"type": "result", "subtype": "success", "num_turns": 0, "is_error": False})
    rnd = sum(1 for f in frames if f.get("type") == "assistant")
    total = int(os.environ.get("FAKE_ROUNDS", "3"))
    log = os.environ.get("FAKE_ROUNDS_LOG", "")
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"round": rnd, "frames": frames}) + "\n")
    thought = f"thinking for round {rnd}"
    emit(delta("thinking_delta", "thinking", thought))
    blocks: list[dict] = [{"type": "thinking", "thinking": thought, "signature": f"sig-round-{rnd}"}]
    if rnd < total - 1:
        text = f"Reading, round {rnd}."
        blocks.append({"type": "text", "text": text})
        blocks.append({"type": "tool_use", "id": f"toolu_r{rnd}", "name": "mcp__ij__Read",
                       "input": {"file_path": os.environ.get("FAKE_READ_PATH", "app.py")}})
        stop = "tool_use"
    else:
        text = "All read."
        blocks.append({"type": "text", "text": text})
        stop = "end_turn"
    emit(delta("text_delta", "text", text))
    emit({"type": "assistant", "message": {"id": f"msg_r{rnd}", "role": "assistant", "model": "fake",
                                           "content": blocks, "stop_reason": stop, "usage": usage()}})
    emit({"type": "stream_event", "event": {"type": "message_stop"}})
    if stop == "tool_use":
        emit({"type": "result", "subtype": "error_max_turns", "is_error": True, "num_turns": 2,
              "usage": usage()})
        return 1
    emit({"type": "result", "subtype": "success", "is_error": False, "num_turns": 1, "usage": usage()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
