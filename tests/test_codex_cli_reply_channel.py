"""codex-cli reply channel — the greeting/truncation regression (2026-07-20).

The old stdout parse kept only the LAST blank-line block of `codex exec`
output. A codex build whose stdout ends with a footer/next-steps block then
returned THAT ("What would you like help with?") instead of the answer above
it, and even on well-behaved builds a multi-paragraph answer was cut to its
final paragraph. Now the adapter passes --output-last-message and reads the
CLI's final message from a file — deterministic; the stdout parse is only a
fallback and keeps EVERYTHING it doesn't recognize as a banner.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iron_jarvis.providers.adapters.subprocess_cli import (
    _codex_parse,
    make_codex_cli,
)

ANSWER = "Short-term rental rules:\n\n1. The 14-day rule.\n\n2. Schedule E vs C."


def _runner_writing_file(payload: str):
    """A fake runner that honors --output-last-message and prints noise."""

    def run(argv, stdin=None):
        assert "--output-last-message" in argv, "the deterministic flag must be passed"
        out_path = argv[argv.index("--output-last-message") + 1]
        # The flag + file must come BEFORE the positional prompt.
        assert argv.index("--output-last-message") < len(argv) - 1
        Path(out_path).write_text(payload, encoding="utf-8")
        return 0, "banner\n\nWhat would you like help with?", ""

    return run


async def test_reply_comes_from_the_output_file_not_stdout(tmp_path):
    adapter = make_codex_cli(
        runner=_runner_writing_file(ANSWER), which=lambda _: "codex"
    )
    resp = await adapter.complete(system="", messages=[], tools=[])
    # The full multi-paragraph answer survives; the stdout greeting is ignored.
    assert resp.text == ANSWER


async def test_empty_output_file_falls_back_to_full_stdout(tmp_path):
    def run(argv, stdin=None):
        # Honors the flag but writes nothing — e.g. an older codex build.
        return 0, "OpenAI Codex v0\nFirst paragraph.\n\nSecond paragraph.", ""

    adapter = make_codex_cli(runner=run, which=lambda _: "codex")
    resp = await adapter.complete(system="", messages=[], tools=[])
    # Fallback keeps BOTH paragraphs — the last-block truncation is dead.
    assert "First paragraph." in resp.text
    assert "Second paragraph." in resp.text


def test_codex_parse_keeps_every_block():
    out = "OpenAI Codex v1\n[2026-07-20] thinking\nReal answer part one.\n\nPart two.\n\nAlso: a caveat."
    text = _codex_parse(out)
    assert "part one" in text and "Part two." in text and "Also: a caveat." in text


async def test_temp_file_is_cleaned_up(tmp_path, monkeypatch):
    import tempfile as _tf

    made: list[str] = []
    real_mkstemp = _tf.mkstemp

    def tracking_mkstemp(*a, **kw):
        fd, path = real_mkstemp(*a, **kw)
        made.append(path)
        return fd, path

    monkeypatch.setattr(_tf, "mkstemp", tracking_mkstemp)
    adapter = make_codex_cli(
        runner=_runner_writing_file("hi"), which=lambda _: "codex"
    )
    await adapter.complete(system="", messages=[], tools=[])
    assert made and not Path(made[0]).exists()


async def test_nonzero_exit_still_raises(tmp_path):
    def run(argv, stdin=None):
        return 2, "", "usage: unknown flag"

    adapter = make_codex_cli(runner=run, which=lambda _: "codex")
    with pytest.raises(RuntimeError, match="exited 2"):
        await adapter.complete(system="", messages=[], tools=[])


async def test_huge_prompt_rides_stdin_never_argv(tmp_path):
    """The Windows 32,767-char command-line regression (live-hit 2026-07-20:
    'The command line is too long' on a session with an extracted PDF): the
    prompt must reach the CLI via STDIN, with argv staying small."""
    from iron_jarvis.providers.adapters.base import LLMMessage

    seen: dict[str, object] = {}

    def run(argv, stdin=None):
        seen["argv_len"] = sum(len(a) for a in argv)
        seen["stdin"] = stdin
        out = argv[argv.index("--output-last-message") + 1]
        Path(out).write_text("ok", encoding="utf-8")
        return 0, "", ""

    big = "x" * 200_000  # far past the 32,767-char CreateProcess ceiling
    adapter = make_codex_cli(runner=run, which=lambda _: "codex")
    resp = await adapter.complete(
        system="", messages=[LLMMessage(role="user", content=big)], tools=[]
    )
    assert resp.text == "ok"
    assert big in str(seen["stdin"])
    assert int(seen["argv_len"]) < 2_000  # argv holds flags only, never the prompt
