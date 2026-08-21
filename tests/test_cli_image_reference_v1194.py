"""Handing a screen snippet to a RUNNING AI CLI pane (v1.194.0).

A ConPTY pane is a byte stream, so the image travels as a PATH the CLI reads
off disk. ``ai_clis.image_reference`` is the one place that decides what text
goes into the pane. It is pure, it must never raise, and it must never emit a
bare fragment a TUI could mangle — these tests pin exactly that.
"""

from __future__ import annotations

import pytest

from iron_jarvis.terminals import ai_clis
from iron_jarvis.terminals.ai_clis import AI_CLIS, image_reference

WIN = r"C:\Users\VR\AppData\Local\Temp\ironjarvis\snip-1.png"


@pytest.mark.parametrize("cli", [c["id"] for c in AI_CLIS])
def test_every_known_cli_gets_a_quoted_path(cli: str):
    """Claude Code (in-prompt path is the only cross-platform method) and Codex
    (cannot take --image once the TUI is live) both want the plain path; every
    other catalog entry degrades to the same thing."""
    got = image_reference(cli, WIN)
    assert got == f'"{WIN}"'
    assert WIN in got


def test_codex_is_not_handed_a_launch_flag():
    """--image/-i is LAUNCH-time; typed into a running session it is just prompt
    text with a stray flag in it. Guards against a well-meaning 'fix'."""
    got = image_reference("codex", WIN)
    assert "--image" not in got and " -i " not in got


def test_no_at_mention_or_slash_command():
    """@file and /add open interactive completion popups in a TUI; the
    characters after them land in a fuzzy-finder, not in the prompt."""
    for cli in ("gemini", "opencode", "aider", "claude"):
        got = image_reference(cli, WIN)
        assert not got.startswith("@") and not got.startswith("/")


def test_unknown_cli_degrades_and_never_raises():
    for cli in ("nsuchcli", "", "   ", "CLAUDE", "Codex"):
        got = image_reference(cli, WIN)
        assert got == f'"{WIN}"', cli
        assert got  # never empty


def test_path_with_spaces_is_quoted():
    p = r"C:\Users\VR\My Screen Shots\snip 2.png"
    assert image_reference("claude", p) == f'"{p}"'


def test_path_with_a_double_quote_switches_to_single_quotes():
    p = '/tmp/od"d/snip.png'
    got = image_reference("claude", p)
    assert got == "'" + p + "'"
    assert got.count('"') == 1  # the one that was in the path


def test_path_with_both_quote_characters_escapes_the_inner_double():
    p = "/tmp/o'd\"d/snip.png"
    got = image_reference("codex", p)
    assert got == '"/tmp/o\'d\\"d/snip.png"'
    assert got.startswith('"') and got.endswith('"')


def test_control_characters_are_stripped():
    """A newline typed into a live pane SUBMITS the prompt mid-sentence."""
    got = image_reference("claude", "/tmp/a\nb\r\tc.png")
    assert got == '"/tmp/abc.png"'
    assert "\n" not in got and "\r" not in got and "\t" not in got


def test_empty_path_returns_empty_string_not_empty_quotes():
    for bad in ("", "   ", "\n", "\x00"):
        assert image_reference("claude", bad) == ""


def test_accepts_a_pathlike():
    from pathlib import PurePosixPath

    assert image_reference("claude", PurePosixPath("/tmp/snip.png")) == '"/tmp/snip.png"'


def test_never_raises_on_hostile_input():
    class Bad:
        def __fspath__(self):
            raise RuntimeError("nope")

        def __str__(self):
            return "/tmp/fallback.png"

    assert image_reference("claude", Bad()) == '"/tmp/fallback.png"'  # type: ignore[arg-type]
    assert image_reference(None, WIN) == f'"{WIN}"'  # type: ignore[arg-type]
    assert image_reference("claude", None) == ""  # type: ignore[arg-type]


def test_docstring_records_the_windows_evidence():
    """The reasoning is the artifact here — it is what stops the next reader
    from replacing the path with a paste keystroke."""
    doc = image_reference.__doc__ or ""
    assert "Alt+V" in doc
    assert "Win+Shift+S" in doc
    assert "--image" in doc


def test_launch_time_knowledge_is_untouched():
    assert ai_clis.AUTOPILOT_FLAGS["codex"] == "--dangerously-bypass-approvals-and-sandbox"
    assert ai_clis.AUTOPILOT_FLAGS["claude"] == "--dangerously-skip-permissions"
