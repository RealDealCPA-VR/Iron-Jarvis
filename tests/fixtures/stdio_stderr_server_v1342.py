"""A REAL stdio child for the v1.342.0 stderr-tail pins (no deps, no network).

Each ``--mode`` is one way a pack can die or chatter, chosen so the transport
takes ONE known path (no race between "the write failed" and "the read hit
EOF"):

* ``die``        -> reads ONE stdin line (the transport's ``initialize``, so the
                    write lands), prints ``PLANTED`` + ``--lines N`` filler
                    lines to stderr, exits 1 without ever answering: the EOF
                    path at start-up ("closed the connection (exit code 1)").
* ``after-init`` -> prints ``PLANTED`` to stderr, answers ``initialize`` like a
                    healthy server, then on the NEXT request prints a fatal
                    line and exits 2 without answering: the EOF path mid-call.
                    Killed while it waits for that request, it is the
                    broken-pipe shape.
* ``chatter``    -> never reads stdin, prints a stderr line every 0.1 s for
                    ``--seconds`` (default 120 — longer than any test's hang
                    guard, so a ``close()`` blocked on this child fails the
                    guard instead of passing by luck), exits 0.
* ``long-line``  -> prints one 5,000-character stderr line, exits 1.

Secrets are PLANTED on purpose: the tests assert the VALUES never leave the
transport (``detections.redact.mask`` keeps the shape, masks the value).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

#: Lines every dying child prints first. Two carry secret VALUES.
PLANTED = [
    "Authorization: Bearer abc123",
    "BRAVE_API_KEY=sk-live-secretvalue",
    "Error: the pack needs a key (BRAVE_API_KEY is not set)",
]

#: The mid-call death's last words (``after-init``).
FATAL_LINE = "fatal: lost the database connection"

_out = sys.stdout.buffer


def _say(line: str) -> None:
    sys.stderr.write(line + "\n")
    sys.stderr.flush()


def _send(obj: dict) -> None:
    _out.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
    _out.flush()


def _read_message() -> dict | None:
    """The next stdin JSON-RPC message, or ``None`` at EOF."""
    for raw in sys.stdin.buffer:
        line = raw.decode("utf-8").strip()
        if not line:
            continue
        return json.loads(line)
    return None


def die(lines: int) -> int:
    _read_message()  # the handshake's write must land before we go
    for line in PLANTED:
        _say(line)
    for i in range(lines):
        _say(f"line {i}")
    return 1


def after_init() -> int:
    for line in PLANTED:
        _say(line)
    while True:
        msg = _read_message()
        if msg is None:
            return 0
        mid = msg.get("id")
        if mid is None:
            continue  # a notification takes no reply
        if msg.get("method") == "initialize":
            _send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                "serverInfo": {"name": "stderr-v1342", "version": "1"}}})
            continue
        _say(FATAL_LINE)
        os._exit(2)


def chatter(seconds: float) -> int:
    end = time.monotonic() + seconds
    i = 0
    while time.monotonic() < end:
        _say(f"still here {i}")
        i += 1
        time.sleep(0.1)
    return 0


def long_line() -> int:
    _read_message()
    _say("x" * 5000)
    return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="die",
                        choices=("die", "after-init", "chatter", "long-line"))
    parser.add_argument("--lines", type=int, default=0)
    parser.add_argument("--seconds", type=float, default=120.0)
    ns = parser.parse_args()
    if ns.mode == "die":
        raise SystemExit(die(ns.lines))
    if ns.mode == "after-init":
        raise SystemExit(after_init())
    if ns.mode == "chatter":
        raise SystemExit(chatter(ns.seconds))
    raise SystemExit(long_line())
