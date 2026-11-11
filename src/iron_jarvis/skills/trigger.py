"""Does a chat message ask Iron Jarvis to MAKE a skill? (v1.470.0)

A user asked chat to "create a skill for tax advisement" and got a workflow
card: every chat turn is offered the ``workflow_draft`` exit, while nothing in
chat ever offered ``skill_create``, so the model reached for the nearest tool
it had. :func:`wants_skill_create` is the one reading the stream lane asks
(``chat_turn.settings_ask_tools``) before it ask-arms ``skill_create`` —
visible, never granted, so the user approves the skill on a card.

HOW IT READS (v1.471.0; the first cut looked for a making verb anywhere in
the seven words before "skill", so "set a reminder to practice my piano
skill" and "create a skill-based pay plan" asked for a skill, while "package
these steps into a skill" and "create skills for payroll" did not). Each
sentence is read on its own, and a "skill" / "skills" counts when

* the word AFTER it carries a request on ("for", "that", "called", the end
  of the sentence…), never a word that makes it another noun ("skill set",
  "skill level", "skill mix", "skill-based");
* the words BEFORE it are its own noun phrase back to a determiner ("a",
  "an", "one", "another", "the"; plural "skills" needs none), with no
  possessive on the way ("my piano skill" is the user's);
* and before that determiner, without crossing a joining word ("to", "and",
  "with", "on"…), sits a verb that makes or keeps a thing — or the phrase is
  "as a skill" / "into a skill" after such a verb anywhere in the sentence
  ("remember how to do this as a skill").

Measured on a labelled set of 75 messages written before the change
(``tests/fixtures/matchers_v1471``): 72.0% right before, see the test for
after. Token-based on purpose: one pass over the words per sentence, no regex
over the whole message, so its cost is linear whatever the text holds.
"""

from __future__ import annotations

import re

__all__ = ["wants_skill_create"]

#: Only this much of a message is read (the rest of the arming reads the same).
_MAX_CHARS = 4000

_WORD = re.compile(r"[a-z0-9']+")
#: Sentence ends: a skill request does not run across them.
_SENTENCE = re.compile(r"[.!?;\n]+")
#: "New skill: review lease agreements" — a heading that IS the request.
_HEADING = re.compile(r"\s*(?:a\s+)?new\s+skill\s*[:–—-]", re.IGNORECASE)
#: "Can we save this as a skill?" asks Jarvis to do it, like "can you".
_LETS = re.compile(r"\s*(?:can|could|shall|should)\s+we\b|\s*let'?s\b", re.IGNORECASE)

#: Verbs that ask for a thing to be made or kept.
_MAKE = frozenset({
    "create", "make", "write", "build", "add", "save", "draft", "generate",
    "author", "set", "turn", "need", "want", "like", "teach", "store", "keep",
    "package", "convert", "capture", "remember", "record", "put", "give",
})
#: What may stand right before the skill's own noun phrase.
_DET = frozenset({"a", "an", "one", "another", "the"})
#: Words after "skill" that carry a request on. Anything else makes "skill"
#: part of another noun ("skill set", "skill gap", "skill-based").
_FOLLOWERS = frozenset({
    "for", "that", "which", "to", "called", "named", "so", "out", "from",
    "about", "and", "with", "i", "we", "you", "please", "here", "now", "like",
    # v1.471.1: words that close a request ("save it as a skill instead").
    "instead", "too", "also", "again", "later", "then", "first",
})
#: Words a request does not cross on its way back from "skill" to the verb.
_BREAK = frozenset({
    # possessives: someone else's skill
    "my", "her", "his", "their", "our", "its", "your", "someone's",
    # joining words and prepositions
    "to", "and", "or", "but", "then", "with", "of", "in", "on", "at", "by",
    "about", "for", "from", "without", "during", "where", "when", "while",
    "who", "whose", "because", "if", "sure", "than",
})
#: The skill phrase is the object of "as" / "into" / "in": "save this as a
#: skill", "capture the process above in a new skill".
_AS = frozenset({"as", "into", "in"})
#: Verbs that ask only when the USER is their subject: "I need a skill that
#: reviews leases" asks; "my staff need better research skills" does not.
_WEAK = frozenset({"need", "want", "like"})
_ME = frozenset({"i", "we", "i'd", "we'd", "i'll", "we'll", "i'm", "really", "also", "just", "would"})
#: Words ending in "-ing" that are nouns, not another verb's clause.
_ING_NOUNS = frozenset({"something", "anything", "everything", "nothing", "thing", "morning", "evening"})
#: "skill" followed by one of these is another thing ("skill set", a game's
#: "skill check").
_NOT_A_SKILL_AFTER = frozenset({
    "set", "sets", "check", "checks", "level", "levels", "tree", "trees",
    "gap", "gaps", "test", "tests", "point", "points", "issue", "rating",
    "ratings", "assessment", "based", "mix", "up", "shortage",
})
#: Words near the request that make it somebody else's kind of skill.
_ELSEWHERE = frozenset({
    "alexa", "resume", "cv", "linkedin", "character", "game", "profile",
})
#: A voice assistant's name in the skill's own noun phrase makes it that
#: product's skill ("a Google Assistant skill"). Read only BEFORE "skill": "a
#: skill that searches Google" is ours.
_OTHER_ASSISTANTS = frozenset({"google", "assistant", "siri", "cortana", "bixby", "echo"})
#: How many words back from the determiner the verb may sit ("make the tax
#: advisement thing a skill").
_BACK = 6
#: The longest noun phrase in front of "skill" ("a short tax advisement skill").
_PHRASE = 4
#: How many words after "skill" are read for :data:`_ELSEWHERE`.
_AHEAD = 5


def _verb_before(words: list[str], at: int) -> bool:
    """A making verb within :data:`_BACK` words before index *at*, reached
    without crossing a :data:`_BREAK` word."""
    for j in range(at - 1, max(-1, at - 1 - _BACK), -1):
        w = words[j]
        if w in _MAKE:
            if w in _WEAK:
                # "I need …", "I'd really like …": the user asks.
                return j > 0 and words[j - 1] in _ME
            return True
        if w in _BREAK:
            return False
        if len(w) >= 6 and w.endswith("ing") and w not in _ING_NOUNS:
            # "write a job description LISTING the required skills": the
            # skills belong to another verb's clause.
            return False
    return False


def _asks_in(words: list[str]) -> bool:
    for i, word in enumerate(words):
        if word not in ("skill", "skills"):
            continue
        nxt = words[i + 1] if i + 1 < len(words) else ""
        if nxt in _NOT_A_SKILL_AFTER or (nxt and nxt not in _FOLLOWERS):
            continue
        near = words[max(0, i - _BACK - _PHRASE): i] + words[i + 1: i + 1 + _AHEAD]
        if any(w in _ELSEWHERE for w in near):
            continue
        # "add this to your skills": Jarvis's own skills.
        if word == "skills" and i >= 2 and words[i - 1] == "your" and words[i - 2] in ("to", "into"):
            if _verb_before(words, i - 2):
                return True
            continue
        # Back over the skill's own noun phrase to its determiner.
        det = -1
        for j in range(i - 1, max(-1, i - 1 - _PHRASE), -1):
            w = words[j]
            if w in _DET:
                det = j
                break
            if w in _BREAK or w in _MAKE:
                break
        if det < 0:
            if word != "skills":
                continue
            det = i  # plural: "create skills for payroll"
        if any(w in _OTHER_ASSISTANTS for w in words[det + 1: i]):
            continue
        # "save this as a skill", "remember how to do this as a skill".
        if det > 0 and words[det - 1] in _AS and any(
            w in _MAKE and w not in _WEAK for w in words[: det - 1]
        ):
            return True
        if _verb_before(words, det):
            return True
    return False


def wants_skill_create(text: str) -> bool:
    """True when *text* asks for a skill to be made or saved.

    "create a skill for tax advisement", "save this as a skill", "turn that
    into a reusable skill", "I need a new skill that reviews leases". Not a
    question about skills ("what skills do you have?", "how do I make a
    skill?"), not the user's own abilities ("improve my Excel skills", "my
    piano skill"), not another noun ("a skill-based pay plan"), not another
    product's ("build an Alexa skill"). Never raises."""
    try:
        raw = str(text or "")[:_MAX_CHARS]
        if not raw.strip():
            return False
        asked = bool(_HEADING.match(raw))
        if not asked:
            for sentence in _SENTENCE.split(raw.lower()):
                if _asks_in(_WORD.findall(sentence)):
                    asked = True
                    break
        if not asked:
            return False
        # A question about making one is answered, not acted on ("how do I
        # make a skill?"); "can we save this as a skill?" asks Jarvis to.
        # Judged last: it reads the whole message.
        if _LETS.match(raw):
            return True
        from ..settings.records import is_question

        return not is_question(raw)
    except Exception:  # noqa: BLE001 — a matcher fault arms nothing
        return False
