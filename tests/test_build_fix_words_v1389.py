"""A coding session's failure says "this session", never "this chat" (v1.389.0).

The harness-gaps review found a failed coding-session reply telling the user
to "pick another model for this chat". A coding session is not a chat: its
model is picked in the session's own box. ``set_build_surface`` (armed by
``routes/build.start_build_turn``) already marked the turn as a Build turn;
only a safety refusal read it. Now the generic fix and the "didn't answer in
time" fix read it too. A chat, a run and the phone keep their words byte for
byte.
"""

from __future__ import annotations

from iron_jarvis.providers import failure_words as fw


class _Timeout(RuntimeError):
    kind = "timeout"
    failed_provider = "claude-cli"


def _in_build(fn):
    token = fw.set_build_surface()
    try:
        return fn()
    finally:
        fw.reset_surface(token)


def test_the_generic_fix_in_a_coding_session_says_this_session():
    words = _in_build(lambda: fw.friendly_error(RuntimeError("boom")))
    assert words["fix"] == fw.GENERIC_FIX_BUILD
    assert "this session" in words["fix"]
    assert "chat" not in words["fix"]


def test_the_generic_fix_in_a_chat_is_unchanged():
    words = fw.friendly_error(RuntimeError("boom"))
    assert words["fix"] == "Press Retry. If it happens again, pick another model for this chat."


def test_a_timeout_in_a_coding_session_says_this_session():
    words = _in_build(lambda: fw.friendly_error(_Timeout("slow")))
    assert words["fix"] == "Press Retry, or pick another model for this session."


def test_a_timeout_in_a_chat_is_unchanged():
    words = fw.friendly_error(_Timeout("slow"))
    assert words["fix"] == "Press Retry, or pick another model for this chat."


def test_a_run_keeps_its_own_words():
    with fw.run_surface():
        words = fw.friendly_error(_Timeout("slow"))
    assert "Settings" in words["fix"]
    assert "session" not in words["fix"] and "chat" not in words["fix"]


def test_the_phone_keeps_the_chat_words():
    with fw.phone_surface():
        words = fw.friendly_error(RuntimeError("boom"))
    assert words["fix"] == fw.GENERIC_FIX


def test_this_place_reads_the_surface():
    assert fw.this_place() == "this chat"
    assert _in_build(fw.this_place) == "this session"
    assert fw.this_place(fw.SURFACE_BUILD) == "this session"
