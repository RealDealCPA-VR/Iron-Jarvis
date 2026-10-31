"""v1.416.0 (a USER BUG): a message that starts with "/" must never reach
Claude Code as one of ITS slash commands.

"/goal complete what remains and push" in a coding session ended "Claude Code
couldn't finish this answer" three times in a row: claude 2.1.309 ran its own
/goal (a synthetic reply, then a goal check needing more requests than the
one-request relay admits). Measured through the real adapter on this PC:
"/goal …" and "/help …" failed every time; " /goal …" answered.
"""

from __future__ import annotations

from types import SimpleNamespace

from iron_jarvis.providers.adapters.claude_native import frames as F


def _user(text: str):
    return SimpleNamespace(role="user", content=text, images=None)


def _texts(frame: dict) -> list[str]:
    return [b["text"] for b in frame["message"]["content"] if b.get("type") == "text"]


def test_a_message_starting_with_a_slash_is_sent_as_text_not_a_command():
    _, frames = F.history_frames("sys", [_user("/goal complete what remains and push")], F.ToolNames([]))
    assert _texts(frames[-1]) == [" /goal complete what remains and push"]


def test_every_user_turn_is_guarded_not_only_the_last():
    msgs = [
        _user("/help me plan this"),
        SimpleNamespace(role="assistant", content="Here is a plan.", tool_calls=[], raw_blocks=None),
        _user("/goal ship it"),
    ]
    _, frames = F.history_frames("", msgs, F.ToolNames([]))
    users = [f for f in frames if f["type"] == "user"]
    assert [_texts(f) for f in users] == [[" /help me plan this"], [" /goal ship it"]]


def test_ordinary_words_are_sent_exactly_as_typed():
    for text in ("say hi", " /already spaced", "path C:/x and /etc", "a/b"):
        _, frames = F.history_frames("", [_user(text)], F.ToolNames([]))
        assert _texts(frames[-1]) == [text]


def test_the_guard_is_the_same_on_every_replay():
    one = F.history_frames("", [_user("/goal x")], F.ToolNames([]))
    two = F.history_frames("", [_user("/goal x")], F.ToolNames([]))
    assert one == two
