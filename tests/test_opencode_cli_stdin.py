"""opencode-cli sends its prompt on STDIN, never as a command-line argument.

Windows caps a CreateProcess command line at 32,767 chars — 8,191 when the CLI
resolves to an npm ``.cmd`` shim, which spawns through cmd.exe. A flattened
office prompt (extracted PDF + identity/skills system block) clears both, so as
a positional argv element it fails BEFORE the CLI starts: raw ``OSError``
(WinError 206), which this adapter never caught, or ``CLI exited 1: The command
line is too long``. The codex/claude adapters were moved to stdin on 2026-07-20
after exactly that live hit; this one was left behind.

Everything here is offline — the runner and ``which`` are injected, except the
one test that drives the real ``_run`` against this interpreter to prove the
stdin pipe is actually wired.
"""

from __future__ import annotations

import asyncio
import json
import sys

from iron_jarvis.providers.adapters.base import LLMMessage
from iron_jarvis.providers.adapters.opencode_cli import OpencodeCliAdapter, _run

#: Windows' hard ceiling, and the lower one an npm .cmd shim imposes.
_CREATE_PROCESS_LIMIT = 32767
_CMD_SHIM_LIMIT = 8191

_REPLY = json.dumps({"type": "text", "part": {"type": "text", "text": "ok"}})


def _adapter(runner):
    return OpencodeCliAdapter(
        model="spark/fleet",
        allowed=lambda: ["spark/fleet"],
        runner=runner,
        which=lambda _n: "/usr/bin/opencode",
    )


def _complete(adapter, prompt):
    return asyncio.run(
        adapter.complete(
            system="", messages=[LLMMessage(role="user", content=prompt)], tools=[]
        )
    )


def test_a_huge_prompt_never_reaches_the_command_line():
    """The exact failure: >32K of flattened context as argv dies at spawn."""
    seen = {}

    def _runner(argv, stdin=None, **_kw):
        seen["argv"] = argv
        seen["stdin"] = stdin
        return 0, _REPLY, ""

    prompt = "K-1 line item %d\n" % 1 + "x" * 40_000
    resp = _complete(_adapter(_runner), prompt)

    argv = seen["argv"]
    assert prompt not in argv
    # Not just "not the whole prompt": no argv element may carry it in pieces.
    assert not any(len(a) > 4096 for a in argv), "prompt leaked into argv"
    # The command line the OS would actually see, quoting/separators included.
    assert len(" ".join(argv)) < _CMD_SHIM_LIMIT < _CREATE_PROCESS_LIMIT
    # ...and it arrived intact by the other channel, which has no such limit.
    assert prompt in seen["stdin"]
    assert len(seen["stdin"]) > _CREATE_PROCESS_LIMIT
    assert resp.text == "ok"


def test_the_flattened_prompt_rides_stdin_and_the_json_reply_still_parses():
    seen = {}

    def _runner(argv, stdin=None, **_kw):
        seen["argv"] = argv
        seen["stdin"] = stdin
        return 0, "\n".join(
            [
                json.dumps({"type": "text", "part": {"type": "text", "text": "PO"}}),
                json.dumps({"type": "text", "part": {"type": "text", "text": "NG"}}),
                json.dumps(
                    {
                        "type": "step_finish",
                        "part": {"tokens": {"input": 7, "output": 2}},
                    }
                ),
            ]
        ), ""

    resp = _complete(_adapter(_runner), "ping")

    assert seen["argv"] == [
        "/usr/bin/opencode", "run", "--format", "json", "-m", "spark/fleet",
    ]
    assert seen["stdin"] == "USER: ping"
    # Output handling is untouched by the stdin move.
    assert resp.text == "PONG"
    assert resp.usage == {"input_tokens": 7, "output_tokens": 2}


def test_default_runner_actually_pipes_stdin_to_the_child():
    """`_run` grew a stdin parameter — prove it reaches the process, not /dev/null."""
    code, out, _err = _run(
        [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"],
        "hello from stdin",
    )
    assert code == 0
    assert out == "hello from stdin"
