"""A fake ``claude`` CLI for the harness benchmark tests (v1.363.0).

Speaks just enough of ``claude -p --output-format stream-json --verbose``: the
prompt arrives on stdin; the fake records what it was given (argv, cwd, the
prompt, the environment names that matter) to ``FAKE_BENCH_RECORD`` (JSON),
writes the files of a reference solution (``FAKE_BENCH_SOLUTION``: a folder
of ``<name>.sol`` files) into its working folder, and prints stream-json
lines ending in a ``result`` line, as Claude Code does.

Scenarios (``FAKE_BENCH_SCENARIO``): ``solve`` (default), ``error`` (an
``error_during_execution`` result in Claude Code's REAL shape — its words,
529 overloaded, in ``errors`` and no ``result``), ``limit`` (the account's
usage limit), ``auth`` (not signed in), ``max_turns`` (``error_max_turns``),
``hang`` (never answers), ``outside`` (also writes a file next to its working
folder), ``silent`` (does its work, then dies with no result line), ``dead``
(dies before doing anything), ``hang_r2`` (hangs in a task's repeat 2 only),
``leave_server`` (starts the price task's server in the background and never
stops it).

v1.383.0: like the real one, its ``system/init`` line names the model it runs
on — the full id an alias stands for (``--model sonnet`` reports
``claude-sonnet-4-6``), the ``--model`` value itself otherwise, or
``FAKE_BENCH_REPORTED_MODEL`` when set (a run that answered on another model).
NO NETWORK.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time

MARKERS = (
    "CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_SESSION_ID", "CLAUDE_PID", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_EXTRA_BODY",
)


#: What an alias stands for (as Claude Code resolves ``--model sonnet``).
ALIASES = {"sonnet": "claude-sonnet-4-6", "opus": "claude-opus-4-6", "haiku": "claude-haiku-4-5"}


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main() -> int:
    if "--version" in sys.argv[1:]:
        print("9.9.9 (Fake Claude Code)")
        return 0
    scenario = os.environ.get("FAKE_BENCH_SCENARIO", "solve")
    prompt = sys.stdin.buffer.read().decode("utf-8")
    record = os.environ.get("FAKE_BENCH_RECORD", "")
    if record:
        with open(record, "w", encoding="utf-8") as fh:
            json.dump({
                "argv": sys.argv[1:], "cwd": os.getcwd(), "prompt": prompt,
                "env": {k: os.environ.get(k) for k in MARKERS},
            }, fh)
    argv = sys.argv[1:]
    passed = argv[argv.index("--model") + 1] if "--model" in argv else "claude-default-fake"
    reported = os.environ.get("FAKE_BENCH_REPORTED_MODEL") or ALIASES.get(passed, passed)
    emit({"type": "system", "subtype": "init", "session_id": "fake", "tools": ["Read", "Write"],
          "model": reported})
    if scenario == "hang" or (scenario == "hang_r2" and pathlib.Path.cwd().parent.name.endswith(".r2")):
        # ``hang_r2`` (v1.383.0 review): only a task's repeat 2 hangs, so a
        # run can be killed part-way through its second repeat.
        time.sleep(600)
        return 0
    if scenario == "leave_server":
        # v1.383.0 review: the agent starts the task's own server in the
        # background and never stops it (its PID is left in server.pid).
        import subprocess

        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        kid = subprocess.Popen([sys.executable, "priceserver.py", "--port", "0"], stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        pathlib.Path("server.pid").write_text(str(kid.pid), encoding="utf-8")
        deadline = time.monotonic() + 30
        log = pathlib.Path("server.log")
        while time.monotonic() < deadline and "listening" not in (log.read_text("utf-8") if log.exists() else ""):
            time.sleep(0.05)
    if scenario == "dead":
        return 3
    written = []
    solution = os.environ.get("FAKE_BENCH_SOLUTION", "")
    if solution:
        for path in sorted(pathlib.Path(solution).iterdir()):
            if path.name.endswith(".sol") and not path.name.startswith("_"):
                dest = pathlib.Path.cwd() / path.name[:-4]
                dest.write_bytes(path.read_bytes())
                written.append(dest.name)
    for i, name in enumerate(written):
        block = {"type": "tool_use", "id": f"toolu_{i}", "name": "Write", "input": {"file_path": name}}
        message = {"id": f"msg_{i}", "role": "assistant", "content": [block]}
        emit({"type": "assistant", "message": message})
        # Claude Code may repeat a message line; a tool_use is counted once.
        emit({"type": "assistant", "message": message})
        emit({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": f"toolu_{i}", "content": "ok"}]}})
    if scenario == "outside":
        (pathlib.Path.cwd().parent / "stray.txt").write_text("written outside", encoding="utf-8")
    if scenario == "silent":
        return 3
    refusals = {
        "error": ("error_during_execution", "API Error: 529 overloaded"),
        "limit": ("success", "Claude AI usage limit reached|1761062400"),
        "auth": ("success", "Invalid API key · Please run /login"),
        "max_turns": ("error_max_turns", ""),
    }
    if scenario in refusals:
        subtype, words = refusals[scenario]
        line = {"type": "result", "subtype": subtype, "is_error": True,
                "num_turns": 3, "total_cost_usd": 0.001,
                "usage": {"input_tokens": 5, "output_tokens": 0}}
        if subtype == "error_during_execution":
            line["errors"] = [words]  # Claude Code's own shape: no "result"
        else:
            line["result"] = words
        emit(line)
        return 1
    # ``exit1``: a normal (not error) result whose ANSWER mentions a 503, and
    # then a non-zero exit — the answer must never be read as the failure.
    answer = "Fixed the retry on a 503." if scenario == "exit1" else "Done: fixed it."
    emit({"type": "result", "subtype": "success", "is_error": False,
          "num_turns": len(written) + 1, "result": answer, "total_cost_usd": 0.0123,
          "usage": {"input_tokens": 120, "cache_creation_input_tokens": 30,
                    "cache_read_input_tokens": 400, "output_tokens": 55}})
    return 1 if scenario == "exit1" else 0


if __name__ == "__main__":
    sys.exit(main())
