"""A fake ``codex`` CLI for the harness benchmark tests (v1.363.0).

Speaks just enough of ``codex exec --json -C <folder> -``: the prompt arrives
on stdin; the fake records argv, cwd and the prompt to ``FAKE_BENCH_RECORD``,
writes a reference solution (``FAKE_BENCH_SOLUTION``) into the ``-C`` folder,
and prints the JSONL events Codex 0.157.0 prints (thread.started,
turn.started, item.completed, turn.completed with usage).

Scenarios (``FAKE_BENCH_SCENARIO``): ``solve`` (default), ``fail`` (a
``turn.failed``: the usage limit), ``hang`` (never answers), ``echo`` (one
reply, then the PROMPT echoed into stderr — as ``codex exec`` does — and exit 1
with no ``ERROR:`` line of its own), ``sandbox`` (every command refused by
Codex's own sandbox — the ERROR line 0.157.0 printed on Windows under
``--sandbox workspace-write`` — then a completed turn whose message says it
could not edit). ``codex login
status`` answers "Logged in using ChatGPT", or — with ``FAKE_CODEX_LOGIN=api``
— an API-key login with part of a (fake) key in it. NO NETWORK.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main() -> int:
    argv = sys.argv[1:]
    if "--version" in argv:
        print("codex-cli 0.0.0-fake")
        return 0
    if argv[:2] == ["login", "status"]:
        if os.environ.get("FAKE_CODEX_LOGIN") == "api":
            print("Logged in using an API key - sk-proj-***FAKE")
        else:
            print("Logged in using ChatGPT")
        return 0
    prompt = sys.stdin.buffer.read().decode("utf-8")
    folder = pathlib.Path(argv[argv.index("-C") + 1]) if "-C" in argv else pathlib.Path.cwd()
    record = os.environ.get("FAKE_BENCH_RECORD", "")
    if record:
        with open(record, "w", encoding="utf-8") as fh:
            json.dump({"argv": argv, "cwd": os.getcwd(), "prompt": prompt,
                       "env": {"CLAUDECODE": os.environ.get("CLAUDECODE")}}, fh)
    emit({"type": "thread.started", "thread_id": "fake-thread"})
    emit({"type": "turn.started"})
    if os.environ.get("FAKE_BENCH_SCENARIO", "solve") == "hang":
        time.sleep(600)
        return 0
    if os.environ.get("FAKE_BENCH_SCENARIO", "solve") == "echo":
        emit({"type": "item.completed", "item": {"id": "i0", "type": "agent_message", "text": "Looking."}})
        sys.stderr.write("user" + chr(10) + prompt + chr(10))
        return 1
    if os.environ.get("FAKE_BENCH_SCENARIO", "solve") == "fail":
        emit({"type": "turn.failed", "error": {"message": "You've hit your usage limit."}})
        return 1
    if os.environ.get("FAKE_BENCH_SCENARIO", "solve") == "sandbox":
        for cmd in ("Get-ChildItem -Force", "Get-Content calendar_util.py"):
            sys.stderr.write(
                "2026-10-21T13:02:11.123456Z ERROR codex_core::tools::router: error=exec_command failed: "
                'CreateProcess { message: "Rejected(\\"`C:\\\\Program Files\\\\PowerShell\\\\7\\\\pwsh.exe '
                + f"-Command '{cmd}'"
                + '` rejected: blocked by policy\\")" }\n'
            )
        sys.stderr.flush()
        emit({"type": "item.completed", "item": {"id": "i0", "type": "agent_message", "text": (
            "I couldn't edit the file: this session is read-only, and the command was blocked by policy.")}})
        emit({"type": "turn.completed", "usage": {
            "input_tokens": 400, "cached_input_tokens": 0, "output_tokens": 40}})
        return 0
    emit({"type": "item.completed", "item": {"id": "i0", "type": "reasoning", "text": "look first"}})
    emit({"type": "item.completed", "item": {
        "id": "i1", "type": "command_execution", "command": "dir", "exit_code": 0}})
    solution = os.environ.get("FAKE_BENCH_SOLUTION", "")
    n = 2
    if solution:
        for path in sorted(pathlib.Path(solution).iterdir()):
            if path.name.endswith(".sol") and not path.name.startswith("_"):
                (folder / path.name[:-4]).write_bytes(path.read_bytes())
                emit({"type": "item.completed", "item": {
                    "id": f"i{n}", "type": "file_change",
                    "changes": [{"path": path.name[:-4], "kind": "update"}], "status": "completed"}})
                n += 1
    emit({"type": "item.completed", "item": {"id": f"i{n}", "type": "agent_message", "text": "Fixed."}})
    emit({"type": "turn.completed", "usage": {
        "input_tokens": 1000, "cached_input_tokens": 200, "output_tokens": 300,
        "reasoning_output_tokens": 50}})
    return 0


if __name__ == "__main__":
    sys.exit(main())
