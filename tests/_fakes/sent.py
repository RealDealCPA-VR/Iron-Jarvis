"""What a chat reply was SENT for its newest message (the intelligence plan,
release 7).

Until that release everything Iron Jarvis found for a message (memory, the
attached file, the page, a pointed-to chat, the "/" skill) rode at the end of
the SYSTEM prompt, and a test that pinned "the model was sent X for this
message" read the system prompt. For a plain chat reply those sections now
ride BESIDE the newest user message, after the conversation
(``iron_jarvis.context.prefix``), so two replies in a row share every byte
before it.

:func:`with_beside` is where such a test reads now: the system prompt and,
after it, what was placed beside the newest user message (never the person's
own words). A test whose promise is about WHERE a section rides reads the two
apart (:func:`beside`, and the system prompt itself).
"""

from __future__ import annotations

from typing import Any

from iron_jarvis.context import prefix as P


def _text(message: Any) -> tuple[str, str]:
    if isinstance(message, (tuple, list)) and len(message) == 2:
        return str(message[0]), str(message[1] or "")
    if isinstance(message, dict):
        return str(message.get("role") or ""), str(message.get("content") or "")
    return str(getattr(message, "role", "") or ""), str(getattr(message, "content", "") or "")


def beside(messages: Any) -> str:
    """What Iron Jarvis placed beside the NEWEST user message: the text kept
    with it (an attached file) and the once-only block. "" when there is none
    (a coding session, an older lane)."""
    for message in reversed(list(messages or [])):
        role, text = _text(message)
        if role != "user":
            continue
        starts = [i for i in (text.find("\n\n" + P.KEPT_TOP), text.find("\n\n" + P.ONCE_TOP)) if i >= 0]
        return text[min(starts):] if starts else ""
    return ""


def own_words(content: Any) -> str:
    """A user message's content WITHOUT what Iron Jarvis placed beside it:
    the person's own words, as they typed them."""
    text = str(content or "")
    starts = [i for i in (text.find("\n\n" + P.KEPT_TOP), text.find("\n\n" + P.ONCE_TOP)) if i >= 0]
    return text[: min(starts)] if starts else text


def with_beside(system: Any, messages: Any) -> str:
    """The system prompt, then what rides beside the newest user message."""
    return str(system or "") + beside(messages)


__all__ = ["beside", "own_words", "with_beside"]
