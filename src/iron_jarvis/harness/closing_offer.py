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
  only the LAST sentence of the last paragraph is read;
* an offer whose action is the USER'S TO DECIDE (v1.402.0 review): sending,
  posting, sharing, publishing, committing, pushing, merging, deploying,
  opening a pull request, paying, buying, deleting, or removing files and
  data ("Want me to send it to the client?", "Shall I push?", "Shall I delete
  the old files?"). Those reach other people or cannot be taken back — the
  app's own git guard asks before EVERY commit and push for the same reason —
  so the reply keeps its question. The nudge's own words say the same thing
  (:data:`CLOSING_OFFER_NUDGE`), for an offer that only points back at one
  ("Next I would push and deploy. Shall I proceed?").

English only: a reply in another language never reads as an offer, and ends
where the model ended it.

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
    "it now with your tools and then end with a short summary. Never send, post, "
    "share, publish, commit, push, merge, deploy, delete, pay for or buy anything "
    "because of this message: those are the user's decisions. If it really needs "
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
    r"|(?:do\s+you\s+)?need\s+me\s+to\b"
    r"|would\s+it\s+(?:help|be\s+(?:helpful|useful))\s+(?:if\s+i|for\s+me\s+to)\b"
    r"|shall\s+(?:i|we)\b"
    r"|should\s+i\s+(?:go\s+ahead|proceed|continue|carry\s+on|keep\s+going|start|begin|also|now|"
    r"do\s+(?:that|this|it|so|the\s+rest)|implement|apply|make\s+(?:the|that|these|this)\b|"
    # review: the commonest "Should I <do the work>?" verbs; "Should I use
    # X?" and "Should I be worried?" stay questions (no verb here).
    r"add|write|fix|update|create|refactor|wire|run|handle|include|extend|cover|rename|move|port|"
    r"convert|build|set\s+up|clean\s+up|split|remove|delete)\b"
    r"|(?:may|can|could)\s+i\s+(?:go\s+ahead|proceed|continue|start|also)\b"
    r"|ready\s+for\s+me\s+to\b"
    r"|(?:is\s+it\s+)?ok(?:ay)?\s+(?:if\s+i|for\s+me\s+to)\b"
    r")",
    re.IGNORECASE,
)
_STATES = re.compile(
    _LEAD + r"(?:"
    r"(?:just\s+)?let\s+me\s+know\s+if\s+you(?:'d|’d|\s+would)?\s+(?:like|want)\s+me\s+to\b"
    r"|(?:just\s+)?let\s+me\s+know\s+if\s+i\s+should\b"
    r"|(?:just\s+)?let\s+me\s+know\s+and\s+i(?:'ll|’ll|\s+will)\b"
    r"|^next,?\s+i\s+(?:can(?!['’]t|not)|could(?!n['’]t|\s+not))\b"
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
    r"|\bwant\s+me\s+to\b)"
    # review: "Happy to add tests for this too." — an offer with no "if".
    r"|" + _LEAD + r"(?:i(?:'m|’m|\s+am)\s+)?(?:happy|glad)\s+to\s+(?:also\s+)?"
    r"(?:add|write|fix|update|create|refactor|wire|run|handle|include|extend|cover|rename|move|port|"
    r"convert|build|set\s+up|clean\s+up|implement|apply|make|do)\b.*\b(?:too|as\s+well)\s*[.!]?$",
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
    r"|\b(?:(?:any|some)\s+)?(?:other|more|further|additional)\s+(?:changes|tweaks|adjustments|edits|help)\b"
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
#: Where an offer's ACTION starts: right after "me to", "shall I", "I can",
#: "happy to", "and I'll" and the like (review, v1.402.0). Every such place
#: in the sentence is read.
_ACTION_LEAD_RX = re.compile(
    r"\b(?:me\s+to|shall\s+(?:i|we)|should\s+i|(?:may|can|could)\s+i|if\s+i|i\s+can(?!['’]t|not)|"
    r"i\s+could(?!n['’]t|\s+not)|i(?:'d|’d|\s+would)\s+be\s+happy\s+to|happy\s+to|glad\s+to|"
    r"i(?:'ll|’ll|\s+will))\s+",
    re.IGNORECASE,
)
#: Words before the action that do not change what it is ("also", "go ahead
#: and", "proceed with").
_ACTION_FILLER_RX = re.compile(
    r"^(?:(?:also|just|then|now|first|quickly|actually|go\s+ahead\s+(?:and|with)|proceed\s+(?:with|to)|"
    r"continue\s+(?:with|by)|carry\s+on\s+with|move\s+on\s+to|get\s+on\s+with|start\s+(?:on|with|by))\s+)+",
    re.IGNORECASE,
)
#: An action that is the USER'S to decide: it reaches other people, money or
#: the shared history, or cannot be taken back (see the module docstring).
#: Read at the start of the action only — "add a POST endpoint" and "update
#: the release notes" are work.
_USER_OWNS_RX = re.compile(
    r"^(?:"
    r"send(?:ing)?(?!\s+(?:a\s+|an\s+|the\s+|some\s+)?(?:test\s+|sample\s+|dummy\s+|http\s+)?"
    r"(?:requests?|quer(?:y|ies)|pings?|signals?|keys?|keystrokes?|inputs?)\b)|"
    r"e-?mail(?:ing)?|forward(?:ing)?|repl(?:y|ying)\s+to|respond(?:ing)?\s+to|"
    r"post(?:ing)?|publish(?:ing)?|tweet(?:ing)?|shar(?:e|ing)|upload(?:ing)?|notify(?:ing)?|"
    r"invit(?:e|ing)|messag(?:e|ing)|"
    r"commit(?:ting)?|(?:force[- ]?)?push(?:ing)?(?!\s+notifications?)|deploy(?:ing)?|"
    r"merg(?:e|ing)(?:\s+(?:it|this|that|them)\b|(?=\s*[.?!]*$)|"
    r"\s+(?:the\s+|this\s+|that\s+|my\s+|your\s+)?(?:pr|pull\s+request|branch(?:es)?|changes|fix)\b|"
    r"\s+(?:\S+\s+){0,4}?into\s+(?:main|master|dev|develop|release|production|prod|the\s+\S+\s+branch)\b)|"
    r"ship(?:ping)?|releas(?:e|ing)(?!\s+notes?)|"
    r"(?:open|opening|create|creating|file|filing|raise|raising|submit|submitting)\s+(?:a\s+|an\s+|the\s+)?"
    r"(?:new\s+)?(?:pr|pull\s+request)s?|submit(?:ting)?|"
    r"pay(?:ing)?|purchas(?:e|ing)|buy(?:ing)?|refund(?:ing)?|"
    r"delet(?:e|ing)|eras(?:e|ing)|wip(?:e|ing)|purg(?:e|ing)|destroy(?:ing)?|uninstall(?:ing)?|discard(?:ing)?|"
    r"drop(?:ping)?\s+(?:the\s+|that\s+|this\s+|those\s+|these\s+)?(?:\w+\s+)?"
    r"(?:tables?|databases?|db|columns?|schemas?|collections?)|"
    r"remov(?:e|ing)\s+(?:\S+\s+){0,3}?(?:files?|folders?|director(?:y|ies)|branch(?:es)?|"
    r"repo(?:sitor(?:y|ies))?s?|backups?|databases?|tables?|records?|rows?|entries|data|accounts?|users?|"
    r"containers?|volumes?)"
    r")\b",
    re.IGNORECASE,
)
#: An action aimed at PRODUCTION (anywhere in it): "Should I run the
#: migration against the production database?" is the user's call too.
_PRODUCTION_RX = re.compile(
    r"\b(?:production|prod|live\s+(?:site|server|database|db|environment|system))\b",
    re.IGNORECASE,
)
#: v1.407.2 (the user's decision 2026-10-29): an offered action is read CLAUSE
#: by clause — "run the tests and then push", "tag v2 and publish the
#: release", "clear the cache and wipe the logs" — so an outward or
#: irreversible step after the first verb is still the user's call. The
#: next-step box (`dashboard/lib/nextStep.ts`) used to patch this with a list
#: of its own; that knowledge lives HERE now and reaches the box through the
#: generated twin (`dashboard/lib/closingOffer.ts`).
_CLAUSE_SPLIT_RX = re.compile(
    r"\s*(?:[,;]|\s[—–-]\s|&&|\band\s+then\b|\bthen\b|\band\b|\bplus\b)\s*",
    re.IGNORECASE,
)
#: Words in front of a clause's verb ("then I'll", "also", "go ahead and").
_CLAUSE_LEAD_RX = re.compile(
    r"^(?:(?:then|also|finally|just|afterwards|after\s+that|go\s+ahead\s+and|"
    r"i(?:'ll|’ll|\s+will|\s+can|\s+could)|we(?:'ll|’ll|\s+will|\s+can)?)\s+)+",
    re.IGNORECASE,
)
#: "run …" / "execute …": the thing run is what is done ("run the deploy
#: script", "run git reset --hard", "run the migration on staging").
_CLAUSE_RUN_RX = re.compile(
    r"^(?:run|re-?run|execute|kick\s+off|trigger|fire\s+off)\s+"
    r"(?:the\s+|a\s+|an\s+|that\s+|this\s+|my\s+|your\s+|our\s+)?",
    re.IGNORECASE,
)
#: A command-line tool in front of its verb ("git push --force", "npm
#: publish", "gh release create").
_CLAUSE_CMD_RX = re.compile(
    r"^(?:git|gh|npm|pnpm|yarn|npx|uv|pip|twine|poetry|cargo|docker|kubectl|helm|terraform|"
    r"heroku|vercel|netlify|fly|flyctl|aws|gcloud|az|firebase|sudo)\s+",
    re.IGNORECASE,
)
#: A clause whose VERB is the user's to decide — beside `_USER_OWNS_RX`:
#: history (push, tag, reset), release, delete / wipe / empty / reset, send
#: (text), money (pay, charge, transfer, wire, order, subscribe, cancel),
#: signing, revoking. Read at the clause's start only, so "add a delete
#: button" and "fix the push notification handler" stay work.
_CLAUSE_OWNS_RX = re.compile(
    r"^(?:"
    r"(?:force[- ]?)?push(?:es|ed|ing)?(?!\s+notifications?)\b|"
    r"deploy\w*|releas(?:e|es|ing)\b(?!\s+notes?)|publish\w*|ship(?:s|ping)?\b|"
    r"tag(?:s|ging)?\s+(?:v?\d|the\s+release|a\s+release|a\s+new\s+release|it\b|this\b|that\b)|"
    r"delet\w*|wip(?:e|es|ing)\b|eras(?:e|es|ing)\b|purg\w*|destroy\w*|truncat\w*|"
    r"empt(?:y|ies|ying)\b|reset(?:s|ting)?\b|uninstall\w*|discard\w*|overwrit\w*|"
    r"rm\b|rmdir\b|del\b|rd\s+/s\b|clean\s+-\w*[fdx]|"
    r"text(?:ing)?\s+(?:him|her|them|me|you|us|the\s+(?:client|customer|team|user|group|owner))\b|"
    r"pay(?:s|ing)?\b|charg(?:e|es|ing)\b|refund\w*|transfer\w*|"
    r"wir(?:e|es|ing)\s+(?:the\s+|a\s+|an\s+|some\s+)?(?:money|funds|payments?|cash|transfers?|\$)|"
    r"plac(?:e|es|ing)\s+(?:the\s+|an?\s+|your\s+|my\s+|this\s+|that\s+)?orders?\b|"
    r"(?:re)?order(?:s|ing)?\s+(?:it|them|this|that|one|more|some|another|a|an)\b|"
    r"buy\w*|purchas\w*|subscrib\w*|unsubscrib\w*|cancel\w*|"
    r"sign(?:s|ing)?\b|revok\w*|"
    r"rotat(?:e|es|ing)\s+(?:\S+\s+){0,2}?(?:keys?|tokens?|secrets?|credentials?|passwords?)\b|"
    r"fil(?:e|es|ing)\s+(?:the\s+|a\s+|an\s+|my\s+|your\s+|this\s+|that\s+|it\b)(?!\s*(?:path|name|system|tree|list|handle)s?\b)"
    r")",
    re.IGNORECASE,
)
#: A migration aimed at STAGING (anywhere in a clause): a shared database the
#: user decides about, like production.
_STAGING_MIGRATION_RX = re.compile(
    r"\bmigrat\w*\b.*\bstaging\b|\bstaging\b.*\bmigrat\w*\b",
    re.IGNORECASE,
)


#: A split clause whose verb is used as a NOUN in front of a piece of the app
#: ("wire up the save and CANCEL BUTTONS", "build the login and SIGN UP
#: pages", "add the loading and EMPTY STATES", "refactor the sign-in and
#: SIGN-OUT code"): ordinary work, never the user's call (review, v1.407.2).
#: The noun follows the verb directly — "cancel the subscription" is not one.
_CLAUSE_NOUN_RX = re.compile(
    r"^\S+(?:[\s-]+(?:up|in|out|on|off))?[\s-]+"
    r"(?:buttons?|pages?|screens?|forms?|flows?|states?|handlers?|logic|code|modals?|dialogs?|"
    r"views?|components?|links?|routes?|endpoints?|functions?|methods?|menus?|icons?|fields?|"
    r"options?|ui|tabs?|widgets?)\b",
    re.IGNORECASE,
)


def _clauses(action: str) -> list[str]:
    """Each clause of an offered action, its lead words and a ``run …`` /
    command-line wrapper taken off (v1.407.2)."""
    out: list[str] = []
    for part in _CLAUSE_SPLIT_RX.split(str(action or "")):
        c = _ACTION_FILLER_RX.sub("", _CLAUSE_LEAD_RX.sub("", part.strip())).strip()
        if not c:
            continue
        out.append(c)
        inner = _CLAUSE_CMD_RX.sub("", _CLAUSE_RUN_RX.sub("", c)).strip()
        if inner and inner != c:
            out.append(inner)
    return out


def _owned(action: str) -> bool:
    """Is this offered ACTION the user's to decide — any clause of it
    (v1.407.2)? The ONE reading ``user_owns``, ``offer_kind`` and
    ``offered_action`` share; the dashboard twin repeats it
    (``lib/closingOffer.ts`` ``owned``)."""
    if _USER_OWNS_RX.match(action) or _PRODUCTION_RX.search(action):
        return True
    return any(
        not _CLAUSE_NOUN_RX.match(c)
        and (_USER_OWNS_RX.match(c) or _CLAUSE_OWNS_RX.match(c) or _STAGING_MIGRATION_RX.search(c))
        for c in _clauses(action)
    )
#: An offer to EXPLAIN, read where the action starts ("Shall I explain …").
_EXPLAIN_START_RX = re.compile(
    r"^(?:explain|clarify|walk\s+you\s+through|go\s+over|go\s+through|elaborate|describe|answer|"
    r"expand\s+on|break\s+(?:it|this|that)\s+down|summari[sz]e)\b",
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


def _actions(sentence: str) -> list[str]:
    """Where each offered action starts in ``sentence`` (the words after
    "me to", "shall I", "I can", …, fillers like "also" taken off)."""
    out = []
    for m in _ACTION_LEAD_RX.finditer(sentence):
        out.append(_ACTION_FILLER_RX.sub("", sentence[m.end():]).strip())
    return out


def user_owns(sentence: str) -> bool:
    """Is an action this sentence offers the USER'S to decide (send, post,
    commit, push, merge, deploy, pay, delete, remove files …)? See the module
    docstring. Never raises."""
    try:
        return any(_owned(a) for a in _actions(str(sentence or "")))
    except Exception:  # noqa: BLE001 — a reading never breaks a turn
        return True


def offer_kind(text: str) -> str:
    """What the reply's ENDING offers (v1.406.0 part 4, the next-step box):
    ``"work"`` — more work the model can just do (:func:`ends_on_offer`);
    ``"user"`` — an offer whose action is the USER'S to decide (send, push,
    delete, …: :func:`user_owns`); ``""`` — no offer. Deterministic, never
    raises. The dashboard's twin is ``dashboard/lib/closingOffer.ts``, held
    equal by ``tests/test_closing_offer_twin_v1406.py``."""
    try:
        sentence = last_sentence(text)
    except Exception:  # noqa: BLE001 — a reading never breaks a turn
        return ""
    if not sentence or len(sentence) > 400:
        return ""
    offered = bool(_ASKS.search(sentence) or _STATES.search(sentence) or _CAN_IF.search(sentence))
    if not offered:
        return ""
    if _EXPLAIN_RX.search(sentence) or _VAGUE_RX.search(sentence) or _LATER_RX.search(sentence):
        return ""
    actions = _actions(sentence)
    if any(_EXPLAIN_START_RX.match(a) for a in actions):
        return ""
    # The USER'S decision (review, v1.402.0): the reply keeps its question.
    if any(_owned(a) for a in actions):
        return "user"
    # A CHOICE between alternatives is a decision question, not an offer —
    # unless the alternative only declines or waits.
    parts = re.split(r",?\s+or\s+", sentence, flags=re.IGNORECASE)
    if len(parts) > 1 and not _DECLINE_ALT_RX.match(parts[-1].strip()):
        return ""
    return "work"


def ends_on_offer(text: str) -> bool:
    """Does the reply END by offering to do MORE WORK and asking permission?
    Deterministic, never raises. See the module docstring for what is not an
    offer."""
    return offer_kind(text) == "work"


#: Where an offered action's words END for the next-step box (a condition, an
#: aside, an alternative, the sentence's own end).
_ACTION_END_RX = re.compile(
    r"\s+if\s+(?:you|that|it)\b|\s+(?:—|–|-)\s+|,|;|\s+or\s+|\s+(?:too|as\s+well)\s*[.?!]*$|[.?!]+",
    re.IGNORECASE,
)
#: At most this many characters of an offered action in the box.
OFFER_ACTION_MAX = 80


def offered_action(text: str) -> str:
    """The words of the action the reply's ending offers ("add tests",
    "send this to the client") for the next-step box — the user's decision's
    action when there is one, else the first — or "". Never raises."""
    try:
        kind = offer_kind(text)
        if not kind:
            return ""
        actions = _actions(last_sentence(text))
        if kind == "user":
            actions = [a for a in actions if _owned(a)] or actions
        for a in actions:
            words = _ACTION_END_RX.split(a, maxsplit=1)[0].strip()
            if words:
                if len(words) > OFFER_ACTION_MAX:
                    words = words[:OFFER_ACTION_MAX].rsplit(" ", 1)[0].strip()
                return words
        return ""
    except Exception:  # noqa: BLE001
        return ""


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
    "offer_kind",
    "offered_action",
    "user_owns",
]
