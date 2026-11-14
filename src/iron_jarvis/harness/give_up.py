"""A coding reply that ENDS BY GIVING UP is sent back once (v1.493.0).

The owner: "i need some form of loop so it doesnt just stop and so it doesn't
claim it cant be done. Like anything can be … done". A coding session's model
that ends its reply on "I couldn't get this to work" / "this can't be done"
used to end the reply right there. Now, ONCE per reply, Iron Jarvis answers
with :data:`GIVE_UP_NUDGE`: try another way first; only when something outside
the model's reach stops it (a sign-in, a file or a decision only the user has)
may it end, and then with a line that starts "Blocked:" and says what it
tried, exactly what stops it and what the user must do.

WHAT COUNTS (deterministic, English, never a model call; see
:func:`gives_up`): first-person inability to do the WORK ("I can't / couldn't
/ wasn't able to / am unable to" + a work verb), "this can't be done / fixed",
"unable to complete", "you'll have to do this yourself" — read only where a
reply's verdict sits: its LAST paragraph of prose, or its FIRST sentence when
the last paragraph does not say the work succeeded after all. Never inside a
code block or a quote. NOT a give-up: a reply that ends on a question to the
user (it is asking, not stopping), one that already has its "Blocked:" line,
"I can't see any problem", "you can't use await here" (an explanation, not the
model's own inability).

A wrong reading costs one extra round, never a wrong answer: the nudge
authorises nothing, and a model that was right to stop says so on its
"Blocked:" line.

Coding sessions hold one (``harness/mode.HarnessTurn.give_up``); both chat
lanes ask it through the closing offer's seam
(``chat_turn.closing_offer_wanted``), so they stay in lock-step. v1.495.0:
plain chat replies and agent runs read :func:`gives_up` through
``daemon/until_done`` — only after the reply did some work with its tools (a
plain "I can't book flights" to a question is an honest limit). Build › Code
is unchanged.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from .closing_offer import MIN_ROUNDS_LEFT, _last_paragraph

GIVE_UP_NUDGE = (
    "Iron Jarvis wrote this note. Your reply ends by saying this cannot be done, or that you "
    "could not do it. Do not stop there: try another way first with your tools (read the error "
    "again, look at how the folder already does it, take a different approach). Only when "
    "something outside your reach stops you (a sign-in, a file or a decision only the user has) "
    'may you end, and then end with a line that starts "Blocked:" and says what you tried, '
    "exactly what stops you and what the user must do. This note comes once per reply, and it "
    "authorises nothing the user would have to approve."
)

#: How much of a reply's first words and last paragraph are read.
HEAD_CHARS = 400
TAIL_CHARS = 900
#: A reply longer than this is cut before it is read (its start and its end
#: are what is read anyway).
MAX_TEXT_CHARS = 40_000

_FENCE_RX = re.compile(r"^\s*(```|~~~)")
_QUOTE_RX = re.compile(r"^\s*>")
_APOS = r"(?:'|’)"
#: The work a coding reply does (what "I can't …" must be about).
_WORK = (
    r"(?:do|complete|finish|fix|implement|make|get|run|access|create|write|modify|edit|install|"
    r"build|resolve|solve|proceed|continue|perform|execute|verify|test|open|read|reach|connect|"
    r"compile|start|launch|apply|change|update|add|remove|figure|find\s+a\s+way|"
    r"determine\s+(?:why|how|what))"
)
#: "I can't / couldn't / wasn't able to / am unable to … <work verb>" — the
#: model's OWN inability. Every repeat is bounded: linear.
_FIRST_PERSON_RX = re.compile(
    r"\bI(?:\s+(?:just|still|simply|really|unfortunately|currently|also)){0,2}\s*"
    r"(?:"
    r"(?:\s(?:can" + _APOS + r"?t|cannot|can\s+not|could\s+not|couldn" + _APOS + r"t))"
    r"|(?:\s(?:was\s+not|wasn" + _APOS + r"t|am\s+not|won" + _APOS + r"t\s+be|will\s+not\s+be)\s+able\s+to)"
    r"|(?:" + _APOS + r"m\s+not\s+able\s+to)"
    r"|(?:\s+am\s+unable\s+to|" + _APOS + r"m\s+unable\s+to|\s+was\s+unable\s+to)"
    r")"
    r"\s+(?:\w+\s+){0,2}?" + _WORK + r"\b",
    re.IGNORECASE,
)
#: "this can't be done", "unable to complete …", "it is not possible to fix
#: this", "you'll have to do this yourself".
_IMPERSONAL_RX = re.compile(
    r"\b(?:this|that|it)\s+(?:can" + _APOS + r"?t|cannot|can\s+not|could\s+not|couldn" + _APOS + r"t)\s+be\s+"
    r"(?:done|fixed|implemented|completed|solved|achieved|accomplished|made\s+to\s+work)\b"
    r"|\bunable\s+to\s+" + _WORK + r"\b"
    r"|\b(?:is|was)(?:n" + _APOS + r"t|\s+not)\s+(?:currently\s+|really\s+)?possible\s+to\s+" + _WORK + r"\b"
    r"|\bit" + _APOS + r"s\s+not\s+possible\s+to\s+" + _WORK + r"\b"
    r"|\byou(?:" + _APOS + r"ll|\s+will)\s+(?:need|have)\s+to\s+(?:do|fix|run|make|apply|finish)\s+"
    r"(?:this|that|it|these|the\s+rest)\s+(?:yourself|manually|by\s+hand)\b"
    r"|\bI\s+(?:give|gave)\s+up\b|\bI" + _APOS + r"m\s+giving\s+up\b",
    re.IGNORECASE,
)
#: The last paragraph says the work succeeded after all (a reply that OPENS
#: "I couldn't run the tests at first" and ends "all tests pass now").
_SUCCESS_RX = re.compile(
    r"\b(?:fixed|works?\s+now|now\s+(?:works?|pass(?:es)?|runs?)|(?:all\s+)?tests?\s+(?:now\s+)?pass(?:es|ed)?|"
    r"implemented|completed|succeed(?:s|ed)|is\s+done|are\s+done|done\s*[.!]?$)",
    re.IGNORECASE,
)
_BLOCKED_LINE_RX = re.compile(
    r"^[^\S\n]*(?:[-*]\s*)?(?:\*\*\s*)?blocked\s*(?:\*\*\s*)?:", re.IGNORECASE | re.MULTILINE,
)
_FIRST_SENTENCE_RX = re.compile(r"(?<=[.?!])\s")


def _prose(text: str) -> str:
    """``text`` without its fenced code blocks and quoted lines."""
    out: list[str] = []
    in_fence = False
    for line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if _FENCE_RX.match(line):
            in_fence = not in_fence
            continue
        if in_fence or _QUOTE_RX.match(line):
            continue
        out.append(line)
    return "\n".join(out)


def _says(text: str) -> bool:
    return bool(_FIRST_PERSON_RX.search(text) or _IMPERSONAL_RX.search(text))


def gives_up(text: str) -> bool:
    """Does this reply END by giving up on the work? Deterministic; never
    raises (cannot tell = False: the reply ends as it would have)."""
    try:
        raw = str(text or "")
        if len(raw) > MAX_TEXT_CHARS:
            raw = raw[:HEAD_CHARS * 4] + "\n\n" + raw[-TAIL_CHARS * 4:]
        if not raw.strip() or _BLOCKED_LINE_RX.search(raw):
            return False
        last = _last_paragraph(raw)[-TAIL_CHARS:]
        if last.rstrip().endswith("?"):
            # It ends by ASKING the user something: not stopping.
            return False
        if last and _says(last):
            return True
        prose = _prose(raw).strip()
        first = _FIRST_SENTENCE_RX.split(prose[:HEAD_CHARS], maxsplit=1)[0]
        if first and _says(first) and not _SUCCESS_RX.search(last or prose[-TAIL_CHARS:]):
            return True
        return False
    except Exception:  # noqa: BLE001 — a reading never breaks a turn
        return False


class GiveUp:
    """One reply's give-up nudge: whether it fired (once per reply). A coding
    session's turn holds one (``harness/mode.HarnessTurn.give_up``)."""

    def __init__(self) -> None:
        self.fired = False
        #: Set by :meth:`wants`, read by the lanes' shared seam: the nudge
        #: owed now is this one (not the closing offer's).
        self.pending = False
        #: Set by :meth:`take` until the seam has published its event.
        self.just_fired = False

    def wants(
        self,
        reply: str,
        *,
        has_calls: bool,
        round_index: int,
        rounds: int,
        armed: bool,
        truncated: bool = False,
        low_trust: bool = False,
        plan_mode: bool = False,
        after_finish: bool = False,
    ) -> bool:
        """Should this ending get the nudge? ONCE per reply; never with a tool
        call in the round, with nothing armed (it could try nothing), cut for
        length, in low trust, in plan mode, after the finish check has run
        (its check-again loop takes over from there) or without
        ``closing_offer.MIN_ROUNDS_LEFT`` rounds left after this one."""
        self.pending = False
        if self.fired or has_calls or not armed or truncated or low_trust or plan_mode or after_finish:
            return False
        if int(rounds) - 1 - int(round_index) < MIN_ROUNDS_LEFT:
            return False
        self.pending = gives_up(reply)
        return self.pending

    def take(self) -> str:
        """It fires: the nudge's words."""
        self.fired = True
        self.pending = False
        self.just_fired = True
        return GIVE_UP_NUDGE


def nudge_index(messages: Iterable[Any]) -> int:
    """The index of the nudge in a turn's ``messages`` (a reply resumed after
    a restart carries its earlier rounds: a nudge in them counts as fired),
    or -1."""
    found = -1
    for i, m in enumerate(messages or ()):
        if getattr(m, "role", "") == "user" and str(getattr(m, "content", "") or "") == GIVE_UP_NUDGE:
            found = i
    return found


__all__ = ["GIVE_UP_NUDGE", "GiveUp", "gives_up", "nudge_index"]
