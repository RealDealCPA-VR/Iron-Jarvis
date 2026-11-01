"""v1.420.0 (C1.9): Codex on Windows has no "edits run, commands ask" posture.

Read from codex 0.157.0's own ``app-server generate-json-schema`` (no model
call; the module docstring of ``hosted.codex`` holds the reading): every
``approvalPolicy`` is ``untrusted`` | ``on-request`` | ``never`` | ``granular``
(which only switches whole KINDS of prompt off), and the sandbox policies and
permission profiles choose what may be touched, never when to ask. On Windows
only full disk access runs a command (measured, v1.409.0), where
``on-request`` asks nothing and ``untrusted`` asks before every file change
AND every command. So the mode table stays; the chip says the real cause in
plain words.
"""

from __future__ import annotations

import sys

import pytest

from iron_jarvis.hosted import codex as C
from iron_jarvis.hosted import service as S

#: Words a person never reads on a card or chip.
BANNED = ("sandbox", "harness", "acp", "stdio", "mcp", "approvalpolicy", "danger-full-access")

WINDOWS = sys.platform == "win32"


@pytest.mark.skipif(not WINDOWS, reason="the Windows mode table")
def test_the_windows_mode_table_is_unchanged():
    assert C.MODES == {"ask": ("untrusted", "danger-full-access"), "full": ("never", "danger-full-access")}
    assert set(C.HIDDEN_MODES) == {"plan", "edits"}


@pytest.mark.skipif(not WINDOWS, reason="the Windows reason")
def test_auto_edits_says_the_real_cause_in_plain_words():
    why = C.HIDDEN_MODES["edits"]
    assert why == (
        "On Windows, Codex can run commands only with its own file protection switched off, and "
        "then it asks either before every file change and every command, or before none of them. "
        "It has no setting where edits run on their own while commands still ask."
    )
    low = why.lower()
    assert not any(word in low for word in BANNED), why


def test_plan_says_codex_has_one_and_why_it_is_not_offered():
    """Codex 0.157.0 HAS a plan mode (``collaborationMode`` {mode: "plan"}), marked
    experimental — "Codex has no plan mode" was not true."""
    why = C.HIDDEN_MODES["plan"]
    assert why == "Codex's own plan mode is marked experimental in this version, so it is not offered here yet."
    assert not any(word in why.lower() for word in BANNED)


def test_the_reading_is_written_into_the_module():
    doc = C.__doc__ or ""
    assert "READ for v1.420.0 (C1.9" in doc
    for fact in ("granular", "externalSandbox", "permissions", "item/fileChange/requestApproval",
                 "collaborationMode", "on-request"):
        assert fact in doc, fact


def test_the_mode_menu_carries_the_reason():
    rows = {h["id"]: h for h in S.hidden_mode_rows("codex")}
    assert rows["plan"]["reason"] == C.HIDDEN_MODES["plan"]
    if WINDOWS:
        assert rows["edits"]["reason"] == C.HIDDEN_MODES["edits"]
    assert C.MODE_ABOUT["ask"] == "Asks before every command and every file change."
