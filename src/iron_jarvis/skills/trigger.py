"""Does a chat message ask Iron Jarvis to MAKE a skill? (v1.470.0)

A user asked chat to "create a skill for tax advisement" and got a workflow
card: every chat turn is offered the ``workflow_draft`` exit, while nothing in
chat ever offered ``skill_create``, so the model reached for the nearest tool
it had. :func:`wants_skill_create` is the one reading the stream lane asks
(``chat_turn.settings_ask_tools``) before it ask-arms ``skill_create`` —
visible, never granted, so the user approves the skill on a card.

Token-based on purpose: one pass over the words, no regex over the whole
message, so its cost is linear whatever the text holds.
"""

from __future__ import annotations

import re

__all__ = ["wants_skill_create"]

#: Only this much of a message is read (the rest of the arming reads the same).
_MAX_CHARS = 4000

_WORD = re.compile(r"[a-z0-9']+")

#: Verbs that ask for a thing to be made or kept.
_MAKE = frozenset({
    "create", "make", "write", "build", "add", "save", "draft", "generate",
    "author", "set", "turn", "need", "want",
})
#: A word between the verb and "skill" that marks ONE skill being asked for.
_ONE = frozenset({"a", "an", "new", "another", "reusable", "as", "into"})
#: "skill" followed by one of these is another thing ("skill set", a game's
#: "skill check").
_NOT_A_SKILL_AFTER = frozenset({
    "set", "sets", "check", "checks", "level", "levels", "tree", "trees",
    "gap", "gaps", "test", "tests", "point", "points", "issue", "rating",
    "assessment",
})
#: Words near the request that make it somebody else's kind of skill.
_ELSEWHERE = frozenset({
    "alexa", "resume", "cv", "linkedin", "character", "game", "profile",
})
#: How many words back from "skill" the verb may sit ("create a short tax
#: advisement skill").
_BACK = 7
#: How many words after "skill" are read for :data:`_ELSEWHERE`.
_AHEAD = 5


def wants_skill_create(text: str) -> bool:
    """True when *text* asks for a skill to be made or saved.

    "create a skill for tax advisement", "save this as a skill", "turn that
    into a reusable skill", "I need a new skill that reviews leases". Not a
    question about skills ("what skills do you have?", "how do I make a
    skill?"), not the user's own abilities ("improve my Excel skills"), not
    another product's ("build an Alexa skill"). Never raises."""
    try:
        raw = str(text or "")[:_MAX_CHARS]
        if not raw.strip():
            return False
        from ..settings.records import is_question

        words = _WORD.findall(raw.lower())
        for i, word in enumerate(words):
            if word != "skill":
                continue
            if i + 1 < len(words) and words[i + 1] in _NOT_A_SKILL_AFTER:
                continue
            back = words[max(0, i - _BACK): i]
            near = back + words[i + 1: i + 1 + _AHEAD]
            if any(w in _ELSEWHERE for w in near):
                continue
            verb_at = max((n for n, w in enumerate(back) if w in _MAKE), default=-1)
            if verb_at < 0:
                continue
            if not any(w in _ONE for w in back[verb_at + 1:]):
                continue
            # A question about making one is answered, not acted on. Judged
            # last: it reads the whole message.
            return not is_question(raw)
        return False
    except Exception:  # noqa: BLE001 — a matcher fault arms nothing
        return False
