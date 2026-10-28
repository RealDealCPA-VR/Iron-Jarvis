"""A reply that ENDS ON AN OFFER is told to do it (v1.402.0, "approve once,
then off to the races", part 4: ask about intent, decide the rest).

On the user's own install 9 of 67 chat replies ended in a question, and the
commonest shape was the OFFER: "Want me to also add tests?", "Shall I
proceed?", "Let me know if you'd like me to wire it into the CLI." The turn
ended there and the user had to type "yes" for work they had, most of the
time, already asked for. Nothing in the app noticed.

:func:`ends_on_offer` is a DETERMINISTIC reading of the reply's last
sentence (no model call): does it offer to do more work and ask permission?
When it does — in a coding session's turn (``harness`` mode, BOTH chat
lanes, lock-step) and at an agent run's final answer (``agents/runtime``),
with tools armed, at least two rounds left, not in plan mode and not in low
trust — the model gets ONE user message, :data:`CLOSING_OFFER_NUDGE`, and the
loop goes on: if the offered work is part of what the user asked (or plainly
needed to finish it) and needs no decision only the user can make, it does
it now; otherwise it ends with that one question and its recommendation. At
most ONCE per reply (per run), and each time it fires the lane publishes ONE
``autonomy.nudged`` event (``core/nudges.CLOSING_QUESTION``).

WHAT IS NOT AN OFFER (each pinned in ``tests/test_closing_offer_v1402.py``):

* a genuine question about INTENT ("Which colour should the button be?",
  "Do you prefer A or B?") — it offers nothing to do;
* a CHOICE between alternatives ("Would you like me to use Postgres or
  SQLite?") — a decision question; only an alternative that merely declines
  or waits ("…, or would you rather review the diff first?") keeps an offer
  an offer;
* an offer to EXPLAIN (explain, clarify, walk you through, answer …) — it is
  not work a tool does;
* a VAGUE offer ("Want me to change anything else?") — there is nothing
  specific to do;
* an idea deferred to LATER ("If you'd like, I can add rate limiting later",
  "… in a follow-up PR") — DECIDED (v1.402.0): a reply that says it is done
  and only parks an idea for another time keeps its ending; the model has
  already judged the idea outside this request;
* a question inside a code block or a quoted line, or earlier in the reply —
  only the LAST sentence of the last paragraph is read.

The reply the user keeps is the first answer, then the continuation
(:meth:`ClosingOffer.combine` — the finish check's ``combine`` shape: one
blank line between, no text twice).
"""

from __future__ import annotations

import re
from typing import Any, Iterable

#: What the model is told, as a user message, when its reply ended on an
#: offer and work is left. The words are the release brief's.
CLOSING_OFFER_NUDGE = (
    "You ended by offering to do more. If it is part of what the user asked, or "
    "plainly needed to finish it, and needs no decision only the user can make, do "
    "it now with your tools and then end with a short summary. If it really needs "
    "the user's decision, end your reply with that one question, with your "
    "recommendation."
)

#: What separates the first answer from the continuation in the reply the user
#: keeps (and the token the stream lane sends between them) — the finish
#: check's own separator, so the two read the same.
SEPARATOR = "\n\n"

#: Rounds that must be LEFT after the reply that ended on the offer: one to
#: do the work, one to say what was done.
MIN_ROUNDS_LEFT = 2

# --------------------------------------------------------------------------- #
# the reading
# --------------------------------------------------------------------------- #

_FENCE_RX = re.compile(r"^\s*(```|~~~)")
_QUOTE_RX = re.compile(r"^\s*>")
#: A list marker or heading opening a line ("- ", "* ", "1. ", "## ").
_LEAD_MARK_RX = re.compile(r"^\s*(?:[-*+•]\s+|\d{1,3}[.)]\s+|#{1,6}\s+)")
#: Markdown emphasis and stray quoting around a sentence.
_EMPHASIS_RX = re.compile(r"[*_`\"“”]+")
#: Sentence ends (the split keeps the punctuation with its sentence).
_SENTENCE_SPLIT_RX = re.compile(r"(?<=[.?!])\s+")

#: Offer shapes, read at the start of the last sentence — or after a short
#: lead-in clause ("Next, shall I …", "If that looks right, want me to …").
_LEAD = r"(?:^|[,;:—–]\s*|\s-\s+)"
_ASKS = re.compile(
    _LEAD + r"(?:"
    r"(?:would|do|did|will)\s+you\s+(?:like|want)\s+me\s+to\b"
    r"|you\s+want\s+me\s+to\b"
    r"|(?:want|wanna)\s+me\s+to\b"
    r"|shall\s+(?:i|we)\b"
    r"|should\s+i\s+(?:go\s+ahead|proceed|continue|carry\s+on|keep\s+going|start|begin|also|now|"
    r"do\s+(?:that|this|it|so|the\s+rest)|implement|apply|make\s+(?:the|that|these|this)\b)"
    r"|(?:may|can|could)\s+i\s+(?:go\s+ahead|proceed|continue|start|also)\b"
    r"|ready\s+for\s+me\s+to\b"
    r"|(?:is\s+it\s+)?ok(?:ay)?\s+(?:if\s+i|for\s+me\s+to)\b"
    r")",
    re.IGNORECASE,
)
_STATES = re.compile(
    _LEAD + r"(?:"
    r"(?:just\s+)?let\s+me\s+know\s+if\s+you(?:'d|’d|\s+would)?\s+(?:like|want)\s+me\s+to\b"
    r"|(?:just\s+)?say\s+the\s+word\b"
    r"|if\s+you(?:'d|’d|\s+would)?\s+(?:like|want)(?:\s+me\s+to)?\s*,?\s*(?:i\s+can|i'll|i\s+will|i’ll)\b"
    r"|if\s+you(?:'d|’d|\s+would)?\s+like\s+me\s+to\b"
    r"|if\s+you\s+want\s+me\s+to\b"
    r")",
    re.IGNORECASE,
)
#: "I can also add logging if you want." / "Happy to add tests too if you'd
#: like." / "I can apply the same fix to the others — just let me know."
_CAN_IF = re.compile(
    _LEAD + r"(?:i\s+can(?!['’]t|not)|i\s+could(?!n['’]t|\s+not)|i(?:'d|’d|\s+would)\s+be\s+happy\s+to|"
    r"happy\s+to|glad\s+to)\b.*"
    r"(?:\bif\s+you(?:'d|’d|\s+would)?\s+(?:like|want)\b"
    r"|\bif\s+(?:that'?s|that\s+is|it'?s|it\s+is|it\s+would\s+be|that\s+would\s+be)\s+(?:helpful|useful)\b"
    r"|\b(?:just\s+)?let\s+me\s+know\b"
    r"|\bwant\s+me\s+to\b)",
    re.IGNORECASE,
)

#: An offer to EXPLAIN is not work a tool does.
_EXPLAIN_RX = re.compile(
    r"\bme\s+to\s+(?:explain|clarify|walk\s+you\s+through|go\s+over|go\s+through|elaborate|describe|"
    r"answer|expand\s+on|break\s+(?:it|this|that)\s+down|summari[sz]e)\b"
    r"|\b(?:i\s+can|happy\s+to|glad\s+to)\s+(?:also\s+)?(?:explain|clarify|walk\s+you\s+through|"
    r"go\s+over|elaborate|answer)\b",
    re.IGNORECASE,
)
#: A VAGUE offer: nothing specific to do.
_VAGUE_RX = re.compile(
    r"\bme\s+to\s+(?:(?:change|tweak|adjust|do|add|fix|update|modify|help\s+with)\s+)?"
    r"(?:anything|something)(?:\s+else)?\b"
    r"|\b(?:any|some)\s+(?:other|more|further)\s+(?:changes|tweaks|adjustments|edits|help)\b"
    r"|\bme\s+to\s+make\s+any\s+(?:changes|tweaks|adjustments|edits)\b"
    r"|\bme\s+to\s+help\s+(?:further|more|with\s+anything)\b",
    re.IGNORECASE,
)
#: An idea DEFERRED to another time (decided: not matched — see the module
#: docstring).
_LATER_RX = re.compile(
    r"\b(?:later(?:\s+on)?|in\s+a\s+follow[- ]?up(?:\s+\w+)?|as\s+a\s+follow[- ]?up|next\s+time|"
    r"in\s+the\s+future|at\s+some\s+point|down\s+the\s+road|another\s+time|some\s+other\s+time|"
    r"in\s+a\s+(?:separate|later|future)\s+(?:pr|pull\s+request|change|commit|session|turn))\b",
    re.IGNORECASE,
)
#: What may follow " or " and still leave an offer an offer: declining,
#: waiting, the user looking first.
_DECLINE_ALT_RX = re.compile(
    r"^(?:not|no|wait|hold|leave|stop|pause|keep|let\s+you|review|check|look|"
    r"(?:would|do|did)\s+you|you(?:'d|’d|\s+would)?\s+(?:rather|prefer|like)|are\s+you|"
    r"should\s+i\s+(?:wait|hold|leave|stop))\b",
    re.IGNORECASE,
)


def _last_paragraph(text: str) -> str:
    """The reply's last paragraph of PROSE, or "" when it ends in a code block,
    a quote or nothing at all."""
    lines = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    # A reply that ends INSIDE (or with the close of) a fenced block ends in
    # code: walk the fences so an unclosed one counts as code too.
    in_fence = False
    last_fence_close = -1
    for i, line in enumerate(lines):
        if _FENCE_RX.match(line):
            in_fence = not in_fence
            if not in_fence:
                last_fence_close = i
    if in_fence:
        return ""
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return ""
    if len(lines) - 1 == last_fence_close or _QUOTE_RX.match(lines[-1]):
        return ""
    para: list[str] = []
    for line in reversed(lines):
        if not line.strip() or _FENCE_RX.match(line) or _QUOTE_RX.match(line):
            break
        para.append(line)
    return " ".join(reversed(para)).strip()


def last_sentence(text: str) -> str:
    """The last sentence of the reply's last paragraph of prose (markdown
    emphasis, a list marker and trailing emoji taken off), or ""."""
    para = _last_paragraph(text)
    if not para:
        return ""
    # A list item or heading: only its own words.
    para = _LEAD_MARK_RX.sub("", para)
    para = _EMPHASIS_RX.sub("", para)
    # Trailing emoji, closing brackets, stray marks — never the punctuation.
    para = re.sub(r"[^\w.?!]+$", "", para).strip()
    if not para:
        return ""
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RX.split(para) if s.strip()]
    if not sentences:
        return ""
    last = sentences[-1]
    # A sentence that is only a closer ("Thanks!", "Cheers.") hides nothing:
    # the offer, if any, is the one before it.
    if len(sentences) > 1 and re.fullmatch(r"(?:thanks|thank\s+you|cheers|enjoy|hope\s+this\s+helps)[.!]*",
                                           last, re.IGNORECASE):
        last = sentences[-2]
    return _LEAD_MARK_RX.sub("", last).strip()


def ends_on_offer(text: str) -> bool:
    """Does the reply END by offering to do MORE WORK and asking permission?
    Deterministic, never raises. See the module docstring for what is not an
    offer."""
    try:
        sentence = last_sentence(text)
    except Exception:  # noqa: BLE001 — a reading never breaks a turn
        return False
    if not sentence or len(sentence) > 400:
        return False
    offered = bool(_ASKS.search(sentence) or _STATES.search(sentence) or _CAN_IF.search(sentence))
    if not offered:
        return False
    if _EXPLAIN_RX.search(sentence) or _VAGUE_RX.search(sentence) or _LATER_RX.search(sentence):
        return False
    # A CHOICE between alternatives is a decision question, not an offer —
    # unless the alternative only declines or waits.
    parts = re.split(r",?\s+or\s+", sentence, flags=re.IGNORECASE)
    if len(parts) > 1 and not _DECLINE_ALT_RX.match(parts[-1].strip()):
        return False
    return True


# --------------------------------------------------------------------------- #
# one reply's state
# --------------------------------------------------------------------------- #


class ClosingOffer:
    """One reply's closing-offer nudge: whether it fired, and the answer it
    fired on (the head of the reply the user keeps). A coding session's turn
    holds one (``harness/mode.HarnessTurn.closing_offer``)."""

    def __init__(self) -> None:
        self.fired = False
        self.head = ""

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
        call in the round, with nothing armed, cut for length, in low trust, in
        plan mode (the plan's own approval ends that reply), after the finish
        check has run (its answer ends the reply), or without
        :data:`MIN_ROUNDS_LEFT` rounds left after this one."""
        if self.fired or has_calls or not armed or truncated or low_trust or plan_mode or after_finish:
            return False
        if int(rounds) - 1 - int(round_index) < MIN_ROUNDS_LEFT:
            return False
        return ends_on_offer(reply)

    def take(self, reply: str) -> str:
        """It fires: keep the answer it fired on and give the nudge's words."""
        self.fired = True
        self.head = str(reply or "")
        return CLOSING_OFFER_NUDGE

    def combine(self, final: str) -> str:
        """The reply the user keeps: the answer that ended on the offer, then
        what came after it — the finish check's ``combine`` shape."""
        if not self.fired:
            return final
        return join(self.head, final)


def join(head: str, final: str) -> str:
    """``head`` then ``final``, one blank line between; never the same text
    twice, never a dangling separator."""
    h = str(head or "").rstrip()
    t = str(final or "").strip()
    if not h:
        return t if t else str(final or "")
    if not t:
        return h
    if t.startswith(h.strip()):
        # The continuation IS the answer, or restated it whole first: once.
        return t
    return h + SEPARATOR + t


def nudge_index(messages: Iterable[Any]) -> int:
    """The index of the nudge in an agent run's ``messages`` (the runtime's
    record that it fired — it survives a resume and a second invocation of
    the loop), or -1."""
    found = -1
    for i, m in enumerate(messages or ()):
        if getattr(m, "role", "") == "user" and str(getattr(m, "content", "") or "") == CLOSING_OFFER_NUDGE:
            found = i
    return found


__all__ = [
    "CLOSING_OFFER_NUDGE",
    "ClosingOffer",
    "MIN_ROUNDS_LEFT",
    "SEPARATOR",
    "ends_on_offer",
    "join",
    "last_sentence",
    "nudge_index",
]
