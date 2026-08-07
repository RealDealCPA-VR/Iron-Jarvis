"""The studio boot gate (v1.147.0) — when is a launched CLI actually listening?

REPORTED: picking OpenCode in Create "fires up, but the describe-what-to-create
area stalls out for a bit; it usually comes back on, and sometimes needs a
refresh."

DIAGNOSED FROM THE LIVE DAEMON, not from reading: the user's two most recent
studio terminals reported ``ready=False, phase=exited, exit_code=2`` and their
whole captured tail was a SINGLE 158-character line —

    PS D:\\Videos> opencodeopencodeopencodeOpen-NetGPO…OpencodeOpencodeOpencode

— PowerShell's PSReadLine repainting its prompt line in place with predictive
completions. The old rule measured ``full.splitlines()[2:]`` ("drop the banner
and the echoed command"), which on a one-line capture is ``[]``: the body was
``""``, readiness could never trip, and the composer sat behind the frontend's
45-second safety-net timer on every launch.

That real tail is :data:`REAL_TAIL` below and it drives these tests, because the
trap here is subtle in BOTH directions: the naive fix (count characters instead
of lines) would have declared readiness on 130 characters of shell repaint noise
and sent the user's first brief to the SHELL, where it runs as a command. So the
gate must open sooner for a real TUI and NOT open for that tail.
"""

from __future__ import annotations

import pytest

from iron_jarvis.daemon.routes.creative import (
    _READY_BODY_CHARS,
    derive_phase,
    studio_ready,
)

#: Captured verbatim from the reporting user's daemon (GET /creative/studio/
#: {id}/tail). One line, no newline: the CLI never started.
REAL_TAIL = (
    "PS D:\\Videos> opencodeopencodeopencodeOpen-NetGPOOpen-NetGPOOpen-NetGPO"
    "Open-NetGPOOpen-NetGPOOpen-NetGPOOpen-NetGPOOpencode   OpencodeOpencode"
    "Opencode        "
)

#: What a program emits when it takes the alternate screen buffer.
ALT_SCREEN = b"\x1b[?1049h"


# --------------------------------------------------------------------------- #
# (1) The reported tail: the gate must stay SHUT — the shell is still at its
#     prompt, and a brief typed here would run as a shell command.
# --------------------------------------------------------------------------- #
def test_shell_repaint_noise_is_not_readiness():
    assert studio_ready(REAL_TAIL, command="opencode") is False


def test_shell_repaint_noise_is_long_enough_to_fool_a_character_count():
    """Pins WHY the newline requirement exists: this tail clears the character
    threshold comfortably. Without the newline rule the naive fix ships a bug
    worse than the one it replaces."""
    body = REAL_TAIL.split("opencode", 1)[1]
    assert len(body.strip()) >= _READY_BODY_CHARS
    assert "\n" not in body


# --------------------------------------------------------------------------- #
# (2) The alternate screen: the one signal a shell cannot produce.
# --------------------------------------------------------------------------- #
def test_entering_the_alternate_screen_is_readiness():
    assert studio_ready(REAL_TAIL, raw=ALT_SCREEN, command="opencode") is True


@pytest.mark.parametrize("seq", [b"\x1b[?1049h", b"\x1b[?1047h", b"\x1b[?47h"])
def test_every_alternate_screen_variant_counts(seq):
    assert studio_ready("PS C:\\> tui", raw=b"noise" + seq + b"more", command="tui") is True


def test_leaving_the_alternate_screen_alone_does_not_trip_it():
    """``…l`` is the LEAVE sequence — only entering counts."""
    assert studio_ready(REAL_TAIL, raw=b"\x1b[?1049l", command="opencode") is False


def test_raw_bytes_are_optional():
    """An older session (or a scrollback read that failed) must still work off
    the text signals alone."""
    assert studio_ready("PS C:\\> claude\n? for shortcuts", raw=None) is True


# --------------------------------------------------------------------------- #
# (3) The volume rule, fixed: measured after the ECHOED COMMAND, not by line
#     index — and it must survive a capture with fewer than three lines.
# --------------------------------------------------------------------------- #
def test_a_cli_that_printed_real_lines_is_ready():
    tail = "PS D:\\Videos> opencode\n" + ("opencode v0.4 — session ready\n" * 6)
    assert studio_ready(tail, command="opencode") is True


def test_the_old_line_index_bug_is_gone():
    """The pre-v1.147.0 rule dropped the first TWO lines, so a two-line capture
    always measured "" — this is that exact shape, and it must now be judged on
    what the CLI actually printed."""
    tail = "PS D:\\Videos> opencode\n" + "x" * 200 + "\n"
    assert len(tail.strip().splitlines()[2:]) == 0  # what the old rule saw
    assert studio_ready(tail, command="opencode") is True


def test_volume_without_the_command_recorded_still_works():
    """A session started before this version has no ``_studio_command``."""
    tail = "PS D:\\Videos> opencode\n" + ("real output line\n" * 8)
    assert studio_ready(tail, command="") is True


def test_a_short_capture_never_indexes_past_the_end():
    for tail in ("", "   ", "PS C:\\>", "PS C:\\> opencode"):
        assert studio_ready(tail, command="opencode") is False


# --------------------------------------------------------------------------- #
# (4) The exited case must never read as ready — typing a brief at a shell
#     prompt runs it as a command.
# --------------------------------------------------------------------------- #
def test_a_cli_that_errored_back_to_the_prompt_is_not_ready():
    tail = (
        "PS D:\\Videos> opencode\n"
        + ("error: could not start\n" * 6)
        + "PS D:\\Videos>"
    )
    assert studio_ready(tail, command="opencode") is False
    assert derive_phase(tail, ready=True, output_age=30.0)[0] == "exited"


def test_the_prompt_guard_beats_volume_but_not_the_alternate_screen():
    """A TUI that has taken the screen is up even if an old prompt trails in the
    window; volume alone still defers to the guard."""
    tail = "PS D:\\Videos> opencode\n" + ("output\n" * 20) + "PS D:\\Videos>"
    assert studio_ready(tail, command="opencode") is False
    assert studio_ready(tail, raw=ALT_SCREEN, command="opencode") is True


# --------------------------------------------------------------------------- #
# (5) The known text markers keep working exactly as before.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "marker", ["? for shortcuts", "shift+tab to cycle", "esc to interrupt"]
)
def test_known_tui_markers_still_gate_open(marker):
    assert studio_ready(f"PS C:\\> claude\n{marker}", command="claude") is True


def test_readiness_is_case_insensitive_on_markers():
    assert studio_ready("PS C:\\> claude\n? For Shortcuts", command="claude") is True


# --------------------------------------------------------------------------- #
# (6) Wired end-to-end: the route records the launch command and serves the
#     readiness it computes.
# --------------------------------------------------------------------------- #
def test_the_start_route_records_the_launch_command(tmp_path, monkeypatch):
    """``studio_ready`` needs the command TEXT (it repeats across repaints), so
    a line index can't stand in for it. If the route stopped recording it, the
    volume rule would silently fall back to its weaker form."""
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform

    from iron_jarvis.terminals import ai_clis

    monkeypatch.setattr(
        ai_clis,
        "detect_ai_clis",
        lambda: [
            {
                "id": "opencode",
                "label": "opencode",
                "command": "opencode",
                "provider": "opencode",
                "url": "",
                "installed": True,
                "path": "",
            }
        ],
    )
    r = client.post(
        "/creative/studio/start",
        json={"cli": "opencode", "cwd": str(tmp_path), "autopilot": False},
    )
    assert r.status_code == 200, r.text
    tid = r.json()["terminal_id"]
    session = platform.terminals.get(tid)
    assert getattr(session, "_studio_command", "") == "opencode"
