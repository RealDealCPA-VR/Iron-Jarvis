"""The side panel's conversation — run in the daemon, narrated to the browser.

THE SILENT FAILURE THIS MODULE PREVENTS: a second Jarvis. A chat window docked
in a browser is the easiest place in the product to accidentally build one —
its own loop, its own tool list, its own idea of what the user has allowed —
and the copy would drift from the real one the first time a gate changed. So
this module runs NO model, holds NO agent loop and decides NO policy. It builds
a ``ChatBody``, hands it to the same ``stream_chat_turn`` the dashboard's
``POST /chat/stream`` uses, and translates that turn's SSE frames into
``browser.panel_event`` frames. Everything that decides anything — the persona,
the router, the arming pass, the permission engine, the approval registry, the
usage ledger — is reached, not reimplemented.

THE SIDEBAR IS SCOPED TO THE BROWSER. It is a Jarvis window docked in Chrome,
and what it may reach is the browser and nothing else — reading tabs, reading a
page, and (at ``interactive``, behind the approval card) acting on one.
Anything else the user wants Jarvis to do — write a file, fetch a URL, run a
command, start an agent — belongs in the Iron Jarvis window, where the user is
looking at the surface that owns those capabilities. That is not a UI
preference; it is the security boundary of this whole feature, and it is
enforced by a HARD CEILING passed into the turn, not by hoping the model stays
on topic.

WHY A CEILING WAS NECESSARY, stated plainly because the first cut of this
module shipped without one and was exploited:

    ARMING IS GRANTING. ``routes/chat.py`` writes ``overrides[name] = "allow"``
    for every armed name and calls the result ``armed_grant``; the mid-turn
    card predicate is False for anything in that set. This module passes
    ``auto_tools=True`` with no explicit picks, so the ordinary autoselect pass
    armed whatever the user's SENTENCE suggested — and it was granted. With
    ``browser_access`` at ``read_only``, "write a file called pwn.txt with the
    summary" armed ``write_file`` and wrote a real file with ZERO approval
    cards. "read the open tab and open the url it mentions" armed ``web_fetch``,
    the page's own text named an attacker's URL, and the daemon fetched it —
    also with no card. ``_filter_browser_tools`` stopped neither: it returns
    early unless a ``browser_*`` name is present, so on those turns it
    inspected nothing at all.

    The ceiling is therefore not a second copy of the browser policy. It is a
    bound on the SET OF NAMES a panel turn may arm at all, computed live from
    the registry (:func:`browser_tool_ceiling`) as exactly the ``browser_*``
    family, and handed to the chat lane as ``tool_ceiling``. Anything the
    autoselect pass picks outside it is DROPPED BEFORE ARMING. Membership is
    tested with ``in``, so an unknown name is refused, never allowed, and an
    unreadable registry yields an EMPTY ceiling — a panel turn with no tools,
    which is the fail-closed direction.

``escalate_to_agent`` AND ``workflow_draft`` ARE OUTSIDE THE CEILING, and this
is the half that matters most. Both are appended to the turn as special specs
rather than armed, so a ceiling that filtered only the armed list would leave
them reachable — and ``escalate_to_agent`` hands the request to an agent
session with its OWN full tool set, which walks straight around every bound
this module holds. A sidebar that can start an agent is not scoped to the
browser. Neither name is a ``browser_*`` name, so the ceiling excludes both by
construction; :func:`_translate` still has a branch for the ``done`` payload
they ride in, because a turn that ends with neither text nor an explanation is
an empty answer, and the panel would render nothing at all.

WHAT THE PANEL MAY DO WITHIN THE CEILING FOLLOWS ``browser_access`` EXACTLY,
and it is not this module that enforces it. The turn arms itself through the
ordinary chat path, whose ``_filter_browser_tools`` gate reads the live
setting: ``read_only`` leaves the inspection tools, ``interactive`` leaves the
full set with the deny floor intact, so a page-acting call still pauses for the
approval card. THE CEILING IS AN ADDITIONAL BOUND, NEVER A REPLACEMENT — every
existing gate still runs. ``off`` never gets this far:
``ExtensionBackend._handle_panel`` refuses the frame and says so.

ARMED BY SURFACE, NOT BY SENTENCE (v1.262.0). Until this version the module
picked nothing explicitly and let the ordinary autoselect pass arm whatever the
user's SENTENCE matched — so "book the first available slot", "fill this form
with my details", "go through these listings" matched no rule, no acting tool
was armed, six rounds ran out, and the sidebar was, in the user's words, "a
chat bot next to the window". A sidebar docked in the browser is a browser
surface by definition, so it now hands the chat lane the whole ``browser_*``
family as ``arm_family``: the lane ARMS the read tier (the very grant the
autoselect pass already gave any browser-shaped sentence — READ, three gates
ahead of any disclosure) and ASK-ARMS the acting tier — VISIBLE, NEVER
GRANTED: every page action still pauses for the card, exactly the consent the
card exists to ask for. The access gate then strips the acting tier at
``read_only``, and the ceiling applies unchanged. Nothing in this module
consents to anything on the user's behalf: the grant a card gives is the
grant the user pressed.

TWO MORE BOUNDS THIS MODULE OWNS, both closing demonstrated holes:

* **An approval this panel never saw is not this panel's to answer.**
  ``core.approvals.resolve`` has no ownership check of its own, so before
  v1.242.0 the only thing separating a panel from an agent session's pending
  ask was that ids are unguessable. That is a real defence and it was the
  ENTIRE defence, undocumented and untested. :class:`PanelTurns` now records
  every id it actually emitted (:attr:`PanelTurns._offered`) and refuses
  anything else out loud.
* **A pairing token must not be able to spend money at socket speed.** Before
  the sidebar, that credential could only ANSWER daemon-initiated directives;
  it can now INITIATE paid model turns, from a credential whose whole security
  boundary is one Pair press. Twelve sequential Sends produced twelve billed
  runs with no throttle. See :data:`PANEL_TURNS_PER_WINDOW`.

WHAT STOP AND STEER CANNOT DO — and the panel's own copy says both, because a
surface that implies otherwise is lying about what a click did:

* **Steering cannot interrupt a half-generated sentence.** A note joins the
  conversation at the next TOOL-ROUND boundary, which is the one place the
  turn's loop is already re-entrant and already checks for stop. If the turn
  ends before a boundary comes round, the note was never taken — and this
  module says so (:data:`STEER_NOT_TAKEN`), because the panel marks a note
  pending until it hears otherwise and would otherwise show a correction as
  landed when it never was.
* **Stop does not kill a tool that is already executing.** That tool runs on a
  worker thread (v1.228.0); the thread finishes and its write lands. Stop ends
  the answer being generated and prevents the NEXT round. A file already being
  written is still written.

ONE MORE HONEST EDGE, and it is why ``done`` is emitted from a ``finally``: a
stopped turn returns WITHOUT a terminal ``done`` SSE frame (it persists the
CANCELLED usage row and returns). A translator that only forwarded what it saw
would leave the panel spinning "working…" forever on the one press whose whole
purpose was to end that state.

THE SIDEBAR REMEMBERS (v1.270.0). The user: "it is not keeping the items within
the chat in context and doesn't know the reference." Every Send used to build a
``ChatBody`` with ONE message — the sentence just typed — so "now the second
one" referred to nothing. :attr:`PanelTurns._history` holds the conversation
(user text, assistant answer, per turn, bounded by :data:`PANEL_HISTORY_MESSAGES`)
and every turn carries it in ``messages``, where the chat lane's own history
planner budgets it to the answering model's window exactly as the chat page's is.
The daemon holds it, not the panel: a side panel is destroyed when it closes,
and ``open`` replays the conversation (``history``) so a reopened panel shows
what was said. ``reset`` forgets it. A stopped turn keeps the answer it had
streamed — that is what the user read.

AND IT IS A CHAT (third review SB3, decision G7). Memory alone was forgotten by
every restart (every update) while the panel still showed the conversation, so
"now the second one" was answered with no context and nothing said so; and a
closed panel's conversation was in no chat list and no search. Each exchange is
now ALSO appended, before its terminal frame, to a chat the daemon writes
(``panel_chat``: daemon-owned like a phone chat, marked as the browser
sidebar's, never bound to a phone line so never sent anywhere). ``open`` after
a restart reads it back; ``reset`` leaves it in the chat list and the next
exchange starts a new one; a chat deleted in the chat list, or one that cannot
be read, is said in one sentence (``panel_chat.NOT_AVAILABLE``) and the
conversation starts fresh.

THE ONE SWITCH (v1.270.0). "Instead of permissions in the browser extension,
there should just be a simple toggle for all permissions required while using
the extension so I don't have to keep approving." ``auto_allow {on}`` flips
``TabGrants.grant_all`` (browser/grants.py) — the SAME grant the per-tab card
wrote, widened to every tab — so the chat lane's ordinary card predicate finds
the call covered and never cards. Nothing in the lane changed for this. Turning
it on also answers the ORDINARY card that is up, so the turn the user was
staring at continues. Fourth review BS1: a card answered one action at a time
(a risk card, a Local only card: any card the approval registry holds with a
kind, ``ChatApprovals.once_kind``) stays up and answerable, is drawn ``floor``
with a line saying Auto-allow does not cover it, and refuses "Allow all page
actions"; the registry itself refuses a grant's answer on it. The risk door
inside each acting tool never reads grants, so a
payment, a password, a "delete", a flagged page or an unreadable target still
stop at their own card — the Handbook's promise, kept. The ``state`` frame
carries ``auto_allow`` so the header paints a fact, and ``open`` may carry the
panel's remembered setting, because the flag dies with the browser session and
with Forget while the user's preference does not. Third review SB4: a PRESS is
the switch's one truth. An ``open`` may restore the setting or turn it off, but
never turn it back ON over the last press's OFF (a second window still holding
an old "on" re-sends it on every tab switch), and every open window is told
when it changes. SB6 (G18): a risk card is answered one action at a time, so
"Allow all page actions" (``approve`` with ``scope: "all"``) is refused on it.

THE WORK IS FOLDED (v1.270.0). "The specific detail of the process should go
behind a thinking word in the chat window that is expandable by the user."
This module changes one thing for that: a ``tool`` frame now says whether it is
a step's ``started`` or ``finished`` and whether it went ``ok``, so the panel
can fold the two into one line under a collapsed "Working…" summary rather than
printing each as a separate row. The route notice (a failover or the mock) is
still a visible line — accountability does not fold.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
from types import SimpleNamespace
from typing import Any

from ..core.logging import get_logger
from ..core.turns import STOP_BY_YOU, TURNS
from . import protocol as P

logger = get_logger(__name__)

#: How many messages (user AND assistant, so half as many exchanges) the
#: sidebar's conversation keeps (v1.270.0). Oldest first out. The chat lane's
#: history planner then fits what remains to the answering model's window, so
#: this is a memory bound, not a context bound.
PANEL_HISTORY_MESSAGES = 40

#: v1.454.0: the panel's "/" list. At most this many skills, each name and
#: description cut to these lengths (a list row shows a line, never a body).
PANEL_SKILLS_MAX = 200
PANEL_SKILL_NAME_CHARS = 120
PANEL_SKILL_DESC_CHARS = 200

#: How long a risk-door card waits for the user in the sidebar (v1.276.0).
#: Bounded, unlike the lane's own attended ask, because it runs INSIDE a tool
#: call that the lane's tool deadline (600 s) would otherwise end with a
#: timeout sentence about the tool; a card left unanswered this long is a
#: refusal with its own words.
RISK_ASK_TIMEOUT_S = 300.0

#: Third review SB2: what a card's answer is told when its reply was stopped
#: before it arrived. The card was withdrawn with the reply; nothing was done.
CARD_WITHDRAWN = "That request was withdrawn when the reply was stopped, so nothing was done."

#: Third review SB13: what a reply stopped by the LAST sidebar window closing
#: is written as in the conversation, after what was read (so neither the
#: reopened panel, the chat list nor the next question reads it as finished).
STOPPED_ON_CLOSE = "(Stopped when the sidebar was closed.)"
STOPPED_ON_CLOSE_EMPTY = "(Stopped when the sidebar was closed, before it answered.)"

#: Fourth plan G27 (C-BS-2): a reply the person stopped with Stop (or Esc) is
#: written so, after what was read, never as "(Iron Jarvis gave no answer.)":
#: the next question then does not lead the model to redo the stopped request.
STOPPED_BY_YOU = "(You stopped this reply.)"
STOPPED_BY_YOU_EMPTY = "(You stopped this reply before it answered.)"

#: SB13: how many open sidebar windows are remembered, and the longest window
#: name kept. A bound, not a count anyone reaches: one per browser window.
PANEL_WINDOWS_MAX = 16
PANEL_WINDOW_CHARS = 64

#: Third review SB6 (decision G18): the ``scope`` of the card's "Allow all page
#: actions" answer — allow this one action AND turn the switch on, judged as
#: ONE answer so a risk card can refuse it whole (:meth:`PanelTurns._decide`).
SCOPE_ALL = "all"

#: SB6: what the panel is told when "Allow all page actions" answers a RISK
#: card (a payment or password field, a destructive word, a flagged page). The
#: answer is read as nothing: the card stays, the switch does not change.
RISK_CARD_ALL_REFUSED = (
    "A risky action is allowed one at a time, so nothing was done and Allow all"
    " page actions was not turned on. Choose Allow once or Deny."
)
#: SB6: an older add-on answers "Allow all page actions" with a plain Allow
#: and then the switch; the Allow is Allow once, the switch is refused.
RISK_CARD_ALL_OLDER = (
    "That risky action was allowed once. Allow all page actions was not turned"
    " on: a risky action is always asked about one at a time."
)

#: Fourth review BS1: the kind a risk card is filed with in the approval
#: registry (``ChatApprovals.once_kind``), and the kind of a Local only card
#: (the chat lane's ``once_reason``).
RISK_CARD_KIND = "browser_risk"
LOCAL_ONLY_CARD_KIND = "local_only"

#: BS1: "Allow all page actions" on a Local only card, and on any other card
#: answered one action at a time (read as nothing, like a risk card's).
LOCAL_ONLY_CARD_ALL_REFUSED = (
    "Local only is on, so each page action is allowed one at a time. Nothing"
    " was done and Allow all page actions was not turned on. Choose Allow once"
    " or Deny."
)
ONCE_CARD_ALL_REFUSED = (
    "This action is asked about every time, so nothing was done and Allow all"
    " page actions was not turned on. Choose Allow once or Deny."
)
#: BS1: an older add-on's "Allow all page actions" (a plain Allow, then the
#: switch) on such a card: the action ran once, the switch is refused.
LOCAL_ONLY_CARD_ALL_OLDER = (
    "That action was allowed once. Allow all page actions was not turned on:"
    " Local only is on, so each page action is asked about one at a time."
)
ONCE_CARD_ALL_OLDER = (
    "That action was allowed once. Allow all page actions was not turned on:"
    " this action is asked about every time."
)

#: C-BS-1: the one line under a card the switch does not answer, so a person
#: who turned Auto-allow on is not surprised by it. A risk card says its
#: reason in its own text, so it has none.
LOCAL_ONLY_CARD_NOTE = (
    "Local only is on, so every page action asks first. Auto-allow does not cover it."
)
ONCE_CARD_NOTE = "This is asked about every time. Auto-allow does not cover it."


def _all_refused(kind: str) -> str:
    """BS1: the words refusing "Allow all page actions" on a card of ``kind``."""
    if kind == RISK_CARD_KIND:
        return RISK_CARD_ALL_REFUSED
    if kind == LOCAL_ONLY_CARD_KIND:
        return LOCAL_ONLY_CARD_ALL_REFUSED
    return ONCE_CARD_ALL_REFUSED


def _all_older(kind: str) -> str:
    """BS1: the words for an older add-on's allow-all on a card of ``kind``."""
    if kind == LOCAL_ONLY_CARD_KIND:
        return LOCAL_ONLY_CARD_ALL_OLDER
    if kind and kind != RISK_CARD_KIND:
        return ONCE_CARD_ALL_OLDER
    return RISK_CARD_ALL_OLDER


def _card_note(kind: str) -> str:
    """C-BS-1: the line under a card of ``kind`` ("" for none)."""
    if not kind or kind == RISK_CARD_KIND:
        return ""
    if kind == LOCAL_ONLY_CARD_KIND:
        return LOCAL_ONLY_CARD_NOTE
    return ONCE_CARD_NOTE

#: What the panel is told when a queued steer note was never consumed. The
#: panel renders its own sentence for this from the ``done`` frame; this is the
#: reason carried alongside it so a log or a later surface can say why.
STEER_NOT_TAKEN = "the turn ended before a step boundary came round"

#: Browser access words a panel turn may run under. ``off`` — and an unreadable
#: or unknown setting — is not here, and the omission is the gate: this tuple is
#: consulted with ``in``, so a word nobody has reasoned about fails CLOSED.
PANEL_ACCESS_ALLOWED: tuple[str, ...] = ("read_only", "interactive")

#: The prefix that defines the sidebar's ceiling. ONE string, read against the
#: LIVE registry rather than a list of names written here, for the same reason
#: ``_filter_browser_tools`` uses a prefix: a ceiling that enumerated names
#: would silently stop covering the browser tools a later ship registers, and
#: the failure would be a sidebar that cannot see the page — annoying — or, if
#: someone "fixed" it by widening the ceiling, a sidebar that can write files.
BROWSER_TOOL_PREFIX = "browser_"

#: How long the sidebar's send budget looks back, in seconds, and how many
#: turns fit in it.
#:
#: THE SILENT FAILURE: unbounded spend from a credential whose entire security
#: boundary is one Pair press. Until the sidebar, the pairing token could only
#: ANSWER directives the daemon had initiated; it can now START paid model
#: turns, and a client driving the socket in a loop is limited only by how fast
#: it can write frames — twelve sequential Sends produced twelve billed runs.
#:
#: THE NUMBERS, and why these: a panel is single-turn (a Send while running is
#: refused outright), so the only way to reach the limit is to ask twelve
#: questions in a minute and read none of the answers. A person typing into a
#: sidebar does not do that; a loop does. Twelve is comfortably above any human
#: rate and far below "unbounded", which is the only property that matters —
#: the bound exists so an abusive client hits a wall with a sentence, not so a
#: user is rationed. The window is ROLLING and measured on the event loop's
#: monotonic clock, never on wall-clock time, so a system clock change cannot
#: widen it and nothing here asserts a duration.
PANEL_TURN_WINDOW_S = 60.0
#: How long a Send waits for the reply before it to finish tidying up once
#: its `done` has been shown; past it the Send is refused as before.
END_TIDY_WAIT_S = 5.0
PANEL_TURNS_PER_WINDOW = 12


#: The user's own words for wanting a NEW tab (v1.271.0). Only a sentence that
#: matches one of these puts ``browser_create_tab`` inside a panel turn's ceiling
#: at all; every other sentence works in the tab the user is looking at. "It
#: should simply work in the tab I'm using, not open a new one unless I tell it
#: to." Word-bounded: "a table" is not "a tab". Read-only tab words ("list my
#: tabs", "close this tab") deliberately match nothing here — those tools stay.
NEW_TAB_PATTERNS: tuple[str, ...] = (
    r"\b(?:new|another|second|separate|fresh|extra|other|its own|their own)\s+(?:tab|window)s?\b",
    r"\bopen\s+(?:a|one|up a|one more)\s+tab\b",
    r"\bin\s+(?:a|one)\s+(?:new\s+)?tab\b",
)
_NEW_TAB_RE = re.compile("|".join(NEW_TAB_PATTERNS), re.IGNORECASE)


def wants_new_tab(text: object) -> bool:
    """Whether the user's sentence asks for a new tab (v1.271.0)."""
    return bool(_NEW_TAB_RE.search(" ".join(str(text or "").split())))


def browser_tool_ceiling(platform: Any) -> frozenset[str]:
    """Every ``browser_*`` name this daemon actually registers — the ceiling.

    THE SILENT FAILURE THIS PREVENTS: a sidebar with the general tool set. See
    the module docstring for the demonstration; in one line, a panel turn runs
    ``auto_tools=True`` on a sentence nobody vetted, and arming is granting.

    FAIL CLOSED, twice over. A registry that cannot be read answers an EMPTY
    ceiling — a turn with no tools at all — rather than ``None``, which the
    chat lane reads as "no ceiling" and is precisely the state this function
    exists to prevent. And membership is later tested with ``in``, so a name
    this set has never heard of is dropped rather than allowed.

    Read LIVE, per turn, and never cached: a tool registered after boot (an MCP
    server, a later ship) belongs to the browser family or it does not, and a
    ceiling frozen at install time would answer for a registry that no longer
    exists.
    """
    try:
        names = platform.registry.names()
    except Exception:  # noqa: BLE001 — an unreadable registry is not a grant
        logger.debug("panel tool ceiling unreadable; arming nothing", exc_info=True)
        return frozenset()
    return frozenset(
        str(name) for name in names if str(name).startswith(BROWSER_TOOL_PREFIX)
    )


#: v1.506.0 (north-star plan 6.10): the ONE fixed line a ceiling turn's
#: system prompt carries (``routes/chat.stream_chat_turn`` joins it before the
#: history planner, only when ``tool_ceiling`` is set). The ceiling cuts the
#: turn to the browser's tools, and the model was never told: asked for a
#: schedule or a file in the sidebar it had no tool and no sentence, and said
#: it could not. The two exits :meth:`PanelConversation._empty_answer` words
#: cover only a turn that ended without an answer.
CEILING_LINE = (
    "# Where you are\n"
    "You are answering in the Iron Jarvis sidebar in the user's own browser. Here you can read the "
    "user's browser tabs and pages and, when the browser tools in your list allow it, act on a page. "
    "Everything else Iron Jarvis does (files on this PC, documents, settings, schedules, agents, apps, "
    "memory, coding) is done in the Iron Jarvis window, not in this sidebar: when the user asks for "
    "such a thing, say so plainly and tell them to ask for it in the Iron Jarvis window."
)


#: How much of a typed text or a URL the panel repeats back (v1.262.0). The
#: args are the lane's redacted args already; this is about a card that fits.
_DESCRIBE_CHARS = 60


def _short(value: object) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= _DESCRIBE_CHARS else text[: _DESCRIBE_CHARS - 1] + "…"


#: How much of a failed step's reason the folded line carries (v1.270.1).
STEP_REASON_CHARS = 160


#: Third plan SB9: the step's line when a page action went out and the
#: browser's answer was lost (``browser.errors.UNKNOWN_OUTCOME_MESSAGE`` is
#: the MODEL's sentence for the same case and stays the model's). ``{what}``
#: is the action in words ("click 'Search'").
ANSWER_LOST_STEP = (
    "Iron Jarvis did not hear back from the page after it tried to {what}, so it does "
    "not know whether that happened. Look at the page before trying again."
)


def _step_failed_line(what: str, output: object) -> str:
    """The sidebar's line for a page action that did not succeed: "Could not
    {what}." plus the tool's reason (v1.270.1) — or, when the action went
    out and its answer was lost (third plan SB9), :data:`ANSWER_LOST_STEP`,
    never the model's sentence nor its code.

    Fourth review BS4: the tool's error is written for the model ("PAGE_CHANGED:
    That tab was not the one on screen, so no picture was taken. Call
    browser_screenshot again…"), so the line drops a leading error code and
    keeps only the first sentence; the rest is the model's next step. The
    model's own text is unchanged."""
    from .errors import UNKNOWN_OUTCOME_MESSAGE

    if UNKNOWN_OUTCOME_MESSAGE in str(output or ""):
        return ANSWER_LOST_STEP.format(what=what)
    reason = _short_reason(_first_sentence(_STEP_CODE.sub("", " ".join(str(output or "").split()), count=1)))
    return f"Could not {what}." + (f" {reason}" if reason else "")


#: A leading error code on a tool's error ("PAGE_CHANGED: ", BS4).
_STEP_CODE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+:\s+")
#: The end of a sentence: ". ", "! " or "? " (BS4).
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _first_sentence(text: str) -> str:
    """``text`` up to the end of its first sentence (BS4)."""
    return _SENTENCE_END.split(text, maxsplit=1)[0] if text else text


def _short_reason(output: object) -> str:
    """One line of a failed tool's error for the step: trimmed, capped, no newlines."""
    text = " ".join(str(output or "").split())
    if len(text) > STEP_REASON_CHARS:
        text = text[: STEP_REASON_CHARS - 1].rstrip() + "…"
    return text


def _cap(sentence: str) -> str:
    return sentence[:1].upper() + sentence[1:] if sentence else sentence


#: How many steps of a form a card lists before it says "and N more" (v1.400.9).
#: The tool takes twenty; a card that listed twenty in full would still fit, so
#: this is the whole list — named so a later cap has one place to change.
FORM_CARD_STEPS = 20


def describe_form_call(args: object, *, card: bool = False) -> str:
    """A ``browser_fill_form`` call in words (v1.400.9).

    ``card=False`` is the ONE line a step in the work fold reads ("fill in a
    form: 3 steps"). ``card=True`` is the approval card's text body: every step
    on its own numbered line, so the user approves the list they can read —
    "type 18 characters into 'Email'", never the characters (the args are the
    tool's own redacted ones).
    """
    from .tools import describe_form_step

    a = args if isinstance(args, dict) else {}
    steps = a.get("steps") if isinstance(a.get("steps"), list) else []
    count = len(steps)
    head = f"fill in a form: {count} step{'' if count == 1 else 's'}" if count else "fill in a form"
    if not card or not steps:
        return head
    lines = [f"{n}. {describe_form_step(step)}" for n, step in enumerate(steps[:FORM_CARD_STEPS], 1)]
    if count > FORM_CARD_STEPS:
        lines.append(f"…and {count - FORM_CARD_STEPS} more")
    return head + "\n" + "\n".join(lines)


def describe_browser_call(name: str, args: object, *, card: bool = False) -> str:
    """A browser tool call as the user would say it (v1.262.0): "click 'Search'",
    "type 'flights to denver' into the search box", "open https://…".

    Lower-case, no trailing period, so it composes: "Iron Jarvis wants to
    {this}. Allow it?", "{This}…", "Could not {this}." A name this function has
    never heard of is described as "run <name>" — never dropped, never guessed.
    The args are whatever the lane sent, already redacted by the tool's own
    ``redact_args``; only a bounded excerpt of a text or a URL is repeated.

    ``card`` (v1.400.9) asks for the approval card's wording: the same words
    for every tool but ``browser_fill_form``, whose card lists each step.
    """
    a = args if isinstance(args, dict) else {}
    if name == "browser_fill_form":
        return describe_form_call(a, card=card)
    # v1.274.0: THE REAL ARGS NEST THE TARGET. `browser_click`/`browser_type`
    # carry `{"target": {"element_id"|"role","name"|"css"}}`, and this function
    # read only flat keys — so every card read "click on the page" and the
    # user could not tell a Search button from a Delete button. The target's
    # own keys are read first, then the flat ones (older shapes, tests).
    t = a.get("target") if isinstance(a.get("target"), dict) else {}
    target = _short(
        t.get("text_hint")
        or t.get("label")
        or t.get("name")
        or a.get("text_hint")
        or a.get("label")
        or a.get("name")
        or t.get("css")
        or a.get("selector")
        or (f"element {t['element_id']}" if t.get("element_id") not in (None, "") else "")
        or (f"element {a['element_id']}" if a.get("element_id") not in (None, "") else "")
    )
    quoted = f"'{target}'" if target else ""
    if name == "browser_click":
        return f"click {quoted}" if quoted else "click on the page"
    if name == "browser_type":
        typed = _short(a.get("text"))
        into = f" into {quoted}" if quoted else ""
        enter = " and press Enter" if a.get("press_enter") else ""
        if typed and "REDACTED" in typed:
            # The tool's own redaction (the text is secret to the card too):
            # say that text will be typed, never quote the marker.
            return f"type some text{into}{enter}"
        return f"type '{typed}'{into}{enter}" if typed else f"type{into}{enter}"
    if name == "browser_press_key":
        key = _short(a.get("key"))
        return f"press {key}" if key else "press a key"
    if name == "browser_navigate":
        url = _short(a.get("url"))
        return f"open {url}" if url else "open a page"
    if name == "browser_create_tab":
        url = _short(a.get("url"))
        return f"open a new tab at {url}" if url else "open a new tab"
    if name == "browser_close_tab":
        return "close a tab"
    if name == "browser_activate_tab":
        return "switch to another tab"
    if name == "browser_scroll":
        direction = _short(a.get("direction"))
        return f"scroll {direction}" if direction else "scroll the page"
    if name == "browser_read_page":
        return "read the page"
    if name == "browser_get_elements":
        return "look at the page's controls"
    if name == "browser_screenshot":
        return "take a screenshot of the page"
    if name == "browser_list_tabs":
        return "list your tabs"
    if name == "browser_get_active_tab":
        return "check which tab is active"
    if name == "browser_get_status":
        return "check the browser connection"
    return f"run {name or 'a step'}"


#: The keys of a pointed element the panel passes on (v1.400.9) — the picker's
#: own vocabulary (extensions/chrome/src/content/picker.ts). Anything else is
#: dropped here; the lane's model cuts every value to its bound.
POINTED_KEYS: tuple[str, ...] = (
    "tab_id", "url", "title", "role", "name", "tag", "input_type", "text", "css",
    "unique_name",
)


def pointed_from(raw: object) -> dict[str, Any] | None:
    """The ``pointed`` param of a ``send`` as the lane's field, or ``None``.

    The panel is a page the daemon does not trust with policy, and this is a
    description of a page element, so nothing here grants or decides anything:
    unknown keys are dropped, and a value that is not an object is no pointer
    at all. Never raises.
    """
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {}
    for key in POINTED_KEYS:
        if key not in raw:
            continue
        value = raw[key]
        if key == "tab_id":
            if isinstance(value, int) and not isinstance(value, bool):
                out[key] = value
        elif key == "unique_name":
            out[key] = value is True
        elif isinstance(value, str):
            out[key] = value
    # A pointer with nothing that names an element is no pointer at all.
    if not any(out.get(k) for k in ("role", "name", "css")):
        return None
    return out


def parse_sse(chunk: str) -> list[tuple[str, dict[str, Any]]]:
    """Split one SSE chunk into ``(event, data)`` pairs.

    ``stream_chat_turn`` yields whole frames (``event:``/``data:``/blank line)
    and bare ``": keepalive"`` comments. A comment, an unparseable ``data:``
    and a frame with no ``event:`` all answer NOTHING rather than raising: this
    runs inside the socket's read loop, and a malformed frame must cost the
    panel one missing line, never the connection.
    """
    out: list[tuple[str, dict[str, Any]]] = []
    for block in str(chunk or "").split("\n\n"):
        event = ""
        data = ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
        if not event:
            continue
        try:
            payload = json.loads(data) if data else {}
        except (ValueError, TypeError):
            continue
        out.append((event, payload if isinstance(payload, dict) else {}))
    return out


#: The sidebar's demo notice when the reply already says it is the demo
#: (v1.359.0 integrate): only what to do, so the demo is named once.
DEMO_SHORT_NOTICE = "Choose a model in Iron Jarvis to get real answers."


def _opens_as_demo(reply: object) -> bool:
    """Does this reply open with the demo model's own first sentence
    (``providers.adapters.mock.DEMO_OPENING``)? Never raises."""
    try:
        from ..providers.adapters.mock import DEMO_OPENING
    except Exception:  # noqa: BLE001 — the full notice is never wrong
        return False
    return bool(DEMO_OPENING) and str(reply or "").lstrip().startswith(DEMO_OPENING)


def _lane_demo_notes() -> tuple[str, ...]:
    """Every note the chat lane can append to a demo answer
    (``chat_turn.demo_note``), made by CALLING it, so a rewording there is
    followed here. Never raises (an empty tuple strips nothing)."""
    try:
        from ..daemon.chat_turn import _FILE_CHANGING_TOOLS, demo_note
        from ..providers.adapters.mock import DEMO_OPENING
    except Exception:  # noqa: BLE001 — wording only
        return ()
    try:
        wrote = sorted(_FILE_CHANGING_TOOLS)[:1]
        notes = (demo_note([], DEMO_OPENING), demo_note(wrote, ""), demo_note([], ""))
    except Exception:  # noqa: BLE001
        return ()
    return tuple(n for n in dict.fromkeys(notes) if n)


def without_lane_demo_note(reply: str) -> str:
    """*reply* without the chat lane's demo note (v1.433.0).

    The sidebar says the demo in its OWN line (:meth:`PanelTurns._route_notice`,
    the accountability line kept visible since v1.267.0). The lane's note says
    the same thing for the chat page — "Choose a model ABOVE", where the chat's
    picker is; the sidebar's picker is below the answer. Shown as well, the
    demo would be named twice (the v1.359.0 "named once" rule)."""
    text = str(reply or "")
    for note in _lane_demo_notes():
        if note in text:
            return text.replace(note, "", 1)
    return text


def settle_answer(shown: str, final: str) -> tuple[bool, str]:
    """How the sidebar's answer bubble, now holding *shown*, comes to read the
    turn's *final* answer (v1.433.0): ``(clear_first, words_to_add)``.

    The lane's ``done`` frame carries the reply the chat page saves. Most of it
    already streamed as tokens; what follows them (a footnote, a note, the
    sentence for a model that wrote nothing after its tools) rides ONLY in
    ``done``. So: the same words add nothing; words that EXTEND what is shown
    add the missing end; anything else clears the bubble and says the whole
    reply (a rewrite that was not kept, a reply the lane rebuilt). Space at
    either end is not a difference worth repainting for. Pure."""
    shown = str(shown or "")
    final = str(final or "")
    if final == shown:
        return False, ""
    if final.startswith(shown):
        return False, final[len(shown):]
    core = shown.strip()
    if not core:
        # Only space on screen: clear it, then the whole answer.
        return bool(shown), final
    lead = final.lstrip()
    if lead.startswith(core):
        return False, lead[len(core):]
    return True, final


#: How much of an error's technical text the sidebar keeps behind "Details".
TECHNICAL_CHARS = 1000


def failure_sentence(title: object, fix: object) -> str:
    """"<title>. <fix>" — the chat's failure line (the dashboard's
    ``lib/failureLine.failureSentence``, the same rule). "" with no title."""
    t = str(title or "").strip()
    if not t:
        return ""
    f = str(fix or "").strip()
    head = t if t[-1] in ".!?" else f"{t}."
    return f"{head} {f}" if f else head


class _ReplyRoute:
    """Third review SB1: "the running reply", as an address for
    :meth:`PanelTurns.emit`. ``origin`` is the connection the frame's caller
    holds (the one the reply started on); the frame goes to whichever
    connections the sidebar is on when it is sent (:meth:`PanelTurns._reply_conns`)."""

    __slots__ = ("origin",)

    def __init__(self, origin: Any) -> None:
        self.origin = origin.origin if isinstance(origin, _ReplyRoute) else origin


#: Third review SB7: the longest a dictation's words may take to come back
#: from a speech service before the sidebar is told it took too long. A bound,
#: so a hung service never holds a dictation open for ever; the add-on's
#: connection is never held by it at all (the transcription is its own task).
VOICE_TRANSCRIBE_TIMEOUT_S = 180.0

#: SB7: a dictation started while the last one is still being written down.
VOICE_BUSY = (
    "Iron Jarvis is still writing down the last dictation. Wait for it to"
    " appear in the box, then try again."
)
#: SB7: the speech service did not answer inside :data:`VOICE_TRANSCRIBE_TIMEOUT_S`.
VOICE_TOO_SLOW = (
    "The speech service took too long to write down the dictation, so it was"
    " stopped. Try again, or type the message."
)


class PanelTurns:
    """One panel conversation: start it, narrate it, stop it, answer its asks.

    Process-local and single-turn on purpose, for the reason the approval and
    turn registries are: there is exactly ONE add-on socket
    (``ExtensionBackend`` holds one connection), so there is exactly one panel
    to narrate to. State that outlived the process would only manufacture
    orphans — a turn id nothing is running.
    """

    def __init__(self, platform: Any, personas: dict | None = None, deps: Any = None) -> None:
        self.platform = platform
        self.personas = personas or {}
        #: v1.269.0: the app's deps object, for the voice backend the daemon
        #: already owns (``_voice_backend``, ``_vosk_model``). Optional: a panel
        #: built without it simply has no microphone, and says so.
        self.deps = deps
        #: The dictation in progress, or None (v1.269.0).
        self._voice_session: _VoiceSession | None = None
        #: The running turn's task and id, or ``(None, "")``.
        self._task: asyncio.Task | None = None
        self._turn_id: str = ""
        #: Notes queued by ``steer`` and not yet consumed, oldest first.
        self._steers: list[dict[str, str]] = []
        #: The connection the running turn is narrating to.
        self._conn: Any = None
        #: Approval ids THIS panel emitted a card for. ``resolve`` has no
        #: ownership check of its own, so without this the panel could answer
        #: an ask filed by an agent session on another thread — proven, and the
        #: only thing that had ever stopped it was that ids are unguessable.
        #: Fourth review BS1: which of them the switch may answer is read from
        #: the registry's own mark (``ChatApprovals.once_kind``), the one field
        #: — the risk door's cards and the lane's Local only cards included.
        self._offered: set[str] = set()
        #: Monotonic timestamps of the turns started inside the rolling
        #: window. See :data:`PANEL_TURNS_PER_WINDOW`.
        self._starts: list[float] = []
        #: Whether the running turn has streamed a single visible token yet.
        #: The panel renders from ``delta`` frames, so a turn that ends with
        #: none of them renders as an EMPTY answer unless this module says
        #: something true instead (:meth:`_translate`, the ``done`` branch).
        self._delta_seen = False
        #: v1.433.0: the words the panel's CURRENT answer bubble holds — the
        #: delta frames sent since the last frame the panel ends a bubble on
        #: (a step, a reset, the end of the turn). Kept by :meth:`emit`, which
        #: sends every one of those frames, so it follows the panel's own
        #: state machine exactly. The ``done`` branch settles the bubble on the
        #: lane's reply from it (:func:`settle_answer`).
        self._segment: list[str] = []
        #: v1.267.0: the model list, cached with the loop time it was read at.
        #: ``open`` is posted on every tab switch now, and the list probes live
        #: providers; a minute is long enough that a switch never re-probes and
        #: short enough that a provider connected in Jarvis shows up soon.
        self._models_cache: tuple[float, dict[str, Any]] | None = None
        self.models_ttl_s: float = 60.0
        #: v1.274.0: the task sending the last `models` frame (kept so it is not
        #: garbage-collected mid-flight; read by tests).
        self._models_task: asyncio.Task | None = None
        #: v1.270.0: the conversation, oldest first — ``{"role", "content"}`` rows
        #: in the chat lane's own shape. Every turn carries it; ``reset`` empties it.
        self._history: list[dict[str, str]] = []
        self.history_limit: int = PANEL_HISTORY_MESSAGES
        #: The running turn's streamed answer, piece by piece, so what the user
        #: READ is what the conversation remembers — including a stopped turn's
        #: half-answer and the empty-answer sentence.
        self._answer: list[str] = []
        #: The running turn's question, and whether its exchange has been written
        #: to the conversation yet. Written ONCE, and BEFORE the terminal frame
        #: (``done`` / the stop's ``done`` / an ``error``) leaves — a panel that
        #: reopens on that frame must replay a complete conversation, and the
        #: parallel suite caught the opposite order: ``open`` racing the turn's
        #: own ``finally`` and replaying a question with no answer.
        self._turn_text: str = ""
        #: The running reply's `done` frame has left (see ``_send``).
        self._end_sent: bool = False
        self._remembered: bool = True
        #: SB3 (third review): the conversation is ALSO a chat the daemon
        #: writes (``panel_chat``), so a restart restores it and the chat list
        #: and history search find it. The chat's id ("" until the first
        #: exchange is saved, and after Reset), the store (built on first use,
        #: ``None`` with no database), whether the restore has been done, and
        #: whether the panel has been told its conversation is gone (once).
        self._chat_id: str = ""
        self._chat: Any = None
        self._chat_built = False
        self._restored = False
        self._unavailable_said = False
        #: The restore found no conversation to carry on (until Reset or the
        #: next saved exchange).
        self._lost = False
        #: One read or write of the chat at a time (a save still in flight
        #: and the next question's check must not cross).
        self._chat_lock = asyncio.Lock()
        #: SB2 (third review): the running (or last) reply was asked to stop
        #: from this sidebar — its cards are withdrawn: an Allow, a Deny or the
        #: switch pressed after Stop answers nothing (:meth:`_stop_requested`).
        self._stop_asked = False
        #: SB13: …and the stop was the last sidebar window closing, so the
        #: conversation says so (:meth:`_remember_exchange`).
        self._closed_stop = False
        #: SB13: the sidebar windows that are open, by the name each panel
        #: gives itself (``window`` on ``open``, ``send`` and ``close``). All
        #: windows share the add-on's one socket, so only that name tells them
        #: apart. Oldest first; bounded by :data:`PANEL_WINDOWS_MAX`. The value
        #: is the connection the window last spoke on (SB4: every open window
        #: is told when the switch changes, :meth:`_tell_every_window`).
        self._windows: dict[str, Any] = {}
        #: SB4 (third review): what the last PRESS of the switch set it to in
        #: this daemon's life (``None`` before any). A press is the only thing
        #: that turns the switch ON over a press that turned it OFF: a window
        #: that still holds an old "on" re-sends it with every ``open`` (each
        #: tab switch), and that must never undo the person's "off".
        self._switch_pressed: bool | None = None
        #: SB6: this panel just allowed a RISK card once with an answer that
        #: named no window (an older add-on). Read by the very next action
        #: only: an older add-on's "Allow all page actions" is exactly that
        #: Allow followed by the switch, which must not turn on. Fourth review
        #: BS1: the KIND of the card so allowed (``ChatApprovals.once_kind``:
        #: "browser_risk", "local_only", …), ``""`` for none.
        self._risk_allowed_unnamed = ""
        #: Third review SB1: the cards of the running reply that are up, by id,
        #: with the frame each was shown with — so a panel that opens (or
        #: re-opens on a new connection) while one waits is shown it again and
        #: can answer it. An id leaves when it is answered or its reply ends.
        self._cards: dict[str, dict[str, Any]] = {}
        #: Third review SB7: the dictation being written down by a speech
        #: service, as its own task (never on the socket's read loop).
        self._voice_task: asyncio.Task | None = None

    # --- narration --------------------------------------------------------

    @property
    def running(self) -> bool:
        task = self._task
        return task is not None and not task.done()

    async def emit(self, conn: Any, event: str, payload: dict[str, Any], *,
                   ends_turn: bool = False) -> None:
        """Send one ``browser.panel_event``. Never raises — a dead socket is an
        expected outcome mid-turn (the user closed the browser), and an
        exception here would take down the read loop that owns this call.

        Third plan SB8: an ``error`` that does NOT end the turn on the panel
        goes out marked ``ends: false`` — the panel shows the line and keeps
        the reply "running" until the reply's own end frame. ``ends_turn``:
        the error IS that end (the reply's own failure, or a Send refused
        before its reply started); it goes out unmarked, exactly as every
        error did before, so an older add-on and a newer one both end the turn
        on it. A dictation's errors are never marked (the panel's voice
        branch never ends a turn)."""
        if (
            event == P.PANEL_EVENT_ERROR
            and not ends_turn
            and not str((payload or {}).get("reason") or "").startswith("voice")
        ):
            payload = {**(payload or {}), "ends": False}
        # v1.433.0: what the panel's current answer bubble holds, by the
        # panel's own rules (sidepanel.ts): a delta appends to it; a step, a
        # reset, the end of a turn, an error (not a dictation's: that one
        # leaves the answer alone) and a not-running state start a new one.
        body = payload or {}
        if event == P.PANEL_EVENT_DELTA:
            self._segment.append(str(body.get("text") or ""))
        elif event in (P.PANEL_EVENT_TOOL, P.PANEL_EVENT_RESET, P.PANEL_EVENT_DONE):
            self._segment = []
        elif event == P.PANEL_EVENT_ERROR and not str(body.get("reason") or "").startswith("voice"):
            self._segment = []
        elif event == P.PANEL_EVENT_STATE and body.get("running") is not True:
            self._segment = []
        elif event == P.PANEL_EVENT_HISTORY and isinstance(body.get("live"), dict):
            # v1.433.0 review: a panel opened mid-turn paints the turn's whole
            # answer so far as the bubble being written (``paintHistory``) —
            # every round's words, not only the current one's. From here the
            # daemon reads that bubble the same way, so the final reply is
            # settled onto what the panel really holds (never glued on). A
            # panel that was already showing the turn ignores the frame; for
            # it the worst case is one repaint of its last bubble at the end.
            self._segment = [str(body["live"].get("answer") or "")]
        if conn is None:
            return
        # Third review SB1: a running reply's frames go to the connection(s)
        # the sidebar is on NOW, looked up at send time — never only the one the
        # reply started on, which a restarted worker or a replaced socket left
        # behind. The bookkeeping above runs once whatever the count.
        targets = self._reply_conns(conn.origin) if isinstance(conn, _ReplyRoute) else [conn]
        for target in targets:
            try:
                await target.send(P.panel_event_frame(event, payload))
            except Exception:  # noqa: BLE001 — narration must never break the socket
                logger.debug("panel event send failed (%s)", event, exc_info=True)

    def _reply_conns(self, origin: Any) -> list[Any]:
        """Third review SB1: where a running reply's frame goes at this moment.

        The connection the reply started on (or a panel re-opened on since,
        :attr:`_conn`), every open sidebar window's connection, and the
        add-on's live connection as the browser backend holds it now; each
        once, and none that is closed. Every window of one browser shares the
        add-on's one socket (its worker hands a frame to every window), so the
        usual answer is exactly one connection: the live one."""
        out: list[Any] = []
        for target in (origin, self._conn, *self._windows.values(), self._live_conn()):
            if target is None or getattr(target, "closed", False) is True:
                continue
            if any(target is seen for seen in out):
                continue
            out.append(target)
        return out

    def _live_conn(self) -> Any:
        """SB1: the add-on's authoritative, paired, open connection, or None."""
        backend = getattr(getattr(self.platform, "browser", None), "backend", None)
        if backend is None:
            return None
        from .extension_backend import ExtensionConnection

        try:
            conn = backend.connection
        except Exception:  # noqa: BLE001 — a stand-in backend: no live socket
            return None
        if isinstance(conn, ExtensionConnection) and conn.paired and not conn.closed:
            return conn
        return None

    # --- the action vocabulary -------------------------------------------

    async def handle(self, conn: Any, action: str, params: dict[str, Any]) -> None:
        """Apply one ``browser.panel`` action.

        Every branch ANSWERS. An action this daemon does not know is reported
        to the panel rather than dropped: a panel from a newer add-on pressing
        a button that does nothing, silently, is the failure this whole module
        is written against.
        """
        act = str(action or "")
        params = params if isinstance(params, dict) else {}
        # SB6: only the action right after an older add-on's Allow on a risk
        # card can be the second half of its "Allow all page actions".
        risk_allowed_unnamed, self._risk_allowed_unnamed = self._risk_allowed_unnamed, ""
        if act in (P.PANEL_ACTION_OPEN, P.PANEL_ACTION_SEND):
            # SB13: a window that opens or sends is a window that is open.
            self._window_open(params.get("window"), conn)
        if act == P.PANEL_ACTION_OPEN:
            # Third review SB1: a panel opening while a reply runs is where that
            # reply talks from now on (the connection it started on may be gone:
            # a restarted worker, a replaced socket).
            if self.running:
                self._conn = conn
            # v1.270.0: the panel's remembered switch setting rides the open
            # (a bool, or absent). Applied BEFORE the state frame so the header
            # paints the setting the user chose, not a flag the browser restart
            # reset a moment ago. SB4: an open may restore it, or turn it off,
            # but never turn it back ON over the last press's OFF.
            changed = (
                self._restore_switch(bool(params["auto_allow"]))
                if isinstance(params.get("auto_allow"), bool)
                else False
            )
            if changed:
                await self._tell_every_window(conn, P.PANEL_EVENT_STATE, self._state())
            else:
                await self.emit(conn, P.PANEL_EVENT_STATE, self._state())
            # SB3: after a restart the conversation is read back from its chat
            # first, so the replay below and the next question carry it. The
            # panel says how many messages it shows (`shown`): when it shows a
            # conversation that cannot be carried on, it is told so, once.
            shown = params.get("shown")
            notice = await self._restore(
                shown if isinstance(shown, int) and not isinstance(shown, bool) else 0
            )
            # v1.270.0: the conversation so far, so a reopened panel shows it —
            # BEFORE the model list, because that list may probe providers and
            # shell out to CLIs (seconds on a busy machine), and a reopened
            # panel must not sit empty while the picker is being filled. The
            # parallel suite found exactly that: a replay that arrived after
            # the probe, past the reader's budget.
            view = self.history_view()
            if notice:
                view["notice"] = notice
            await self.emit(conn, P.PANEL_EVENT_HISTORY, view)
            # SB1: a card the running reply waits on is shown again, so it can
            # be answered from this panel (the daemon still holds it).
            await self._show_waiting_cards(conn)
            # v1.454.0: the skills the "/" list offers. A dictionary read of
            # the registry (no file, no probe), so it rides the open inline.
            await self.emit(conn, P.PANEL_EVENT_SKILLS, self.skills_view())
            # v1.267.0: the model list rides the open, so the picker is filled
            # before the user reaches for it. v1.274.0: OFF THE READ LOOP. This
            # handler is awaited inline by the socket's frame loop, and the
            # catalog probes providers and shells out to CLIs — so an `open`
            # (posted on every tab switch) used to stall the whole add-on
            # socket for seconds: no command response could be read, no pong
            # either, and a click in flight could run to ACTION_TIMEOUT. The
            # list is now sent from its own task; `open` returns at once.
            self._models_task = asyncio.ensure_future(self._emit_models(conn))
            return
        if act == P.PANEL_ACTION_RESET:
            await self._reset(conn)
            return
        if act == P.PANEL_ACTION_AUTO_ALLOW:
            if (
                risk_allowed_unnamed
                and params.get("on") is True
                and not self._window_name(params.get("window"))
            ):
                # SB6: an older add-on's "Allow all page actions" on a risk
                # card — the Allow went through as Allow once; the switch does
                # not turn on. Said, and the header repainted with the truth.
                # Fourth review BS1: any card answered one at a time, in the
                # words for its kind.
                await self.emit(
                    conn, P.PANEL_EVENT_ERROR,
                    {"text": _all_older(risk_allowed_unnamed), "reason": "risk_card_all"},
                )
                await self.emit(conn, P.PANEL_EVENT_STATE, self._state())
                return
            self._window_open(params.get("window"), conn)
            await self._auto_allow(conn, params)
            return
        if act == P.PANEL_ACTION_SEND:
            await self._send(
                conn,
                str(params.get("text") or "").strip(),
                provider=str(params.get("provider") or "").strip(),
                model=str(params.get("model") or "").strip(),
                pointed=pointed_from(params.get("pointed")),
                skill=str(params.get("skill") or "").strip()[:PANEL_SKILL_NAME_CHARS],
                window=self._window_name(params.get("window")),
            )
            return
        if act == P.PANEL_ACTION_STOP:
            await self._stop(conn)
            return
        if act == P.PANEL_ACTION_STEER:
            await self._steer(conn, params)
            return
        if act in (P.PANEL_ACTION_APPROVE, P.PANEL_ACTION_DENY):
            await self._decide(
                conn, act, str(params.get("id") or ""), str(params.get("scope") or ""),
                named=bool(self._window_name(params.get("window"))),
            )
            return
        if act == P.PANEL_ACTION_VOICE:
            await self._voice(conn, params)
            return
        if act == P.PANEL_ACTION_RECORD_SAVE:
            await self._record_save(conn, params)
            return
        if act == P.PANEL_ACTION_CLOSE:
            # The panel is gone: a turn narrating to nobody keeps billing.
            # Stop is cooperative, so this is a request, not a kill.
            # SB13 (third review): every sidebar window shows the one
            # conversation over the add-on's one socket, so a window closing
            # stops the reply only when no other window is still open on it.
            # A close that names no window (an older add-on) stops as before.
            if self._window_closed(params.get("window")):
                return
            # SB7: a dictation nobody can see any more is dropped, its
            # transcription with it.
            self._cancel_voice()
            # G27 (BS2): a close right after Send stops the reply too, even
            # before it registered (the Stop is kept and applied then).
            if self._stop_own_turn():
                self._stop_asked = True
                self._closed_stop = True
            return
        await self.emit(
            conn,
            P.PANEL_EVENT_ERROR,
            {"text": f"Iron Jarvis does not know the sidebar action {act!r}."},
        )

    # --- which windows are open, and whether Stop was asked (third review) ----

    @staticmethod
    def _window_name(raw: object) -> str:
        return str(raw or "").strip()[:PANEL_WINDOW_CHARS] if isinstance(raw, str) else ""

    def _window_open(self, raw: object, conn: Any = None) -> None:
        """SB13: remember a sidebar window as open (no-op without a name), and
        (SB4) the connection it spoke on, so a switch change reaches it."""
        name = self._window_name(raw)
        if not name:
            return
        self._windows.pop(name, None)
        self._windows[name] = conn
        while len(self._windows) > PANEL_WINDOWS_MAX:
            self._windows.pop(next(iter(self._windows)))

    def _window_closed(self, raw: object) -> bool:
        """SB13: forget a closing window; True when ANOTHER window of the
        sidebar is still open (so its reply must keep running). A close that
        names no window answers False: the reply stops, as it always did."""
        name = self._window_name(raw)
        if not name:
            return False
        self._windows.pop(name, None)
        return bool(self._windows)

    async def _tell_every_window(self, conn: Any, event: str, payload: dict[str, Any]) -> None:
        """SB4: send one frame to ``conn`` and to every other open sidebar
        window's connection, each connection once. Windows of one browser share
        the add-on's one socket (its worker hands a frame to every window);
        a second connection is told separately."""
        sent: list[Any] = []
        for target in (conn, *self._windows.values()):
            if target is None or any(target is s for s in sent):
                continue
            sent.append(target)
            await self.emit(target, event, payload)

    def _restore_switch(self, on: bool) -> bool:
        """SB4: apply the setting a panel remembered (sent with ``open``).

        Turning the switch OFF always applies: a permission turned off wins
        over anything stale. Turning it ON applies only when the last press in
        this daemon's life did not turn it off; that "on" restores the person's
        choice after a browser restart or Forget cleared the daemon's flag.
        Over a press's OFF it is a window still holding an old value, and it
        is read as a report only: the ``state`` frame tells that window the
        switch's real value. Returns whether the switch changed."""
        before = self._auto_allowed()
        if on and self._switch_pressed is False:
            return False
        self._set_auto_allow(on)
        return self._auto_allowed() != before

    async def _show_waiting_cards(self, conn: Any) -> None:
        """SB1 (third review): show ``conn`` every card the running reply is
        still waiting on, exactly as it was first shown. A card already
        answered, or of a reply asked to stop, is not shown again."""
        if not self._cards:
            return
        if not self.running or self._stop_requested():
            self._cards.clear()
            return
        from ..daemon.routes.chat import _approvals

        try:
            waiting = set(_approvals(SimpleNamespace(platform=self.platform)).pending_ids())
        except Exception:  # noqa: BLE001 — an unreadable registry shows nothing again
            logger.debug("panel: waiting cards could not be read", exc_info=True)
            return
        for approval_id, payload in list(self._cards.items()):
            if approval_id not in waiting:
                self._cards.pop(approval_id, None)
                continue
            await self.emit(conn, P.PANEL_EVENT_APPROVAL, dict(payload))

    def _stop_requested(self) -> bool:
        """SB2: was the running (or last) reply asked to stop — from this
        sidebar, or through the turn's own stop from anywhere else?"""
        if self._stop_asked:
            return True
        handle = TURNS.handle(self._turn_id) if self._turn_id else None
        return bool(handle is not None and handle.stopped)

    async def resolve_risk_ask(
        self, req: Any, ctx: Any = None, *, name: str = "", args: Any = None
    ) -> bool:
        """The browser risk door's resolver for THIS panel's turn (v1.276.0).

        The four floor cases — a payment or password field, the destructive
        vocabulary, a flagged page, a target the daemon could not read — stop
        inside the tool (``_ActingTool._require_approval``), after the lane's
        own card (or the switch) already let the call through. Without a
        resolver that stop was a queue row and a refusal telling the model to
        have the user "approve it in Iron Jarvis, then make the identical call
        again" — from the sidebar, a task that could not be completed. Now the
        stop is a card HERE, with the door's own reason, answered through the
        same registry as every other card, and one Allow authorises exactly
        one call (the door approves and consumes in one breath).

        Only for THIS panel's running turn: ``ctx.turn_id`` must be the id this
        panel minted. Anything else — the chat page's turn, no turn at all —
        is answered False, which leaves the door's original behaviour intact.
        """
        if not self.running or self._conn is None:
            return False
        if str(getattr(ctx, "turn_id", "") or "") != self._turn_id:
            return False
        from ..core import risk_door as _risk_door

        # SB2 (third review): a reply already stopped puts up no card at all.
        handle = TURNS.handle(self._turn_id)
        if self._stop_requested():
            return _risk_door.Verdict(False, _risk_door.STOPPED_TAIL)
        from ..daemon.routes.chat import _approvals

        from ..core.approvals import InterruptNote, settled_actor

        registry = _approvals(SimpleNamespace(platform=self.platform))
        safe_args = args if isinstance(args, dict) else {}
        # Fourth review BS1: filed with its kind, so no grant (the switch)
        # answers it and every reader of the registry knows what it is.
        ap_id, fut = registry.request(
            name or "browser action", safe_args, once_only=True, once_reason=RISK_CARD_KIND,
        )
        self._offered.add(ap_id)
        # v1.402.0 review (the autonomy metrics): this card, for COUNTING
        # ONLY, like every other card — a browser risk check is a once-only
        # SAFETY card (never "avoidable"). The sidebar keeps no saved
        # conversation, so it is counted as an ask no task holds.
        note = InterruptNote(
            getattr(self.platform, "event_bus", None),
            kind="card", tool=name or "browser action", surface="chat",
            ask_id=ap_id, turn_id=self._turn_id, once_only=True, reason="browser_risk",
        )
        what = describe_browser_call(name, safe_args)
        reason = " ".join(str(getattr(req, "reason", "") or "").split())
        card = {
            "id": ap_id,
            "tool": name,
            "text": (
                f"Iron Jarvis wants to {what}, and stops to ask because {reason}. Allow it?"
                if reason
                else f"Iron Jarvis wants to {what}. Allow it?"
            ),
            "floor": True,
        }
        # SB1 (third review): kept so a panel that opens while it waits is
        # shown it again; sent to the connection the sidebar is on NOW. It
        # leaves ``_cards`` once the registry no longer holds it.
        self._cards[ap_id] = dict(card)
        await self.emit(_ReplyRoute(self._conn), P.PANEL_EVENT_APPROVAL, card)
        note.asked()
        # SB2 (third review): the wait ends on the card's answer OR the
        # reply's Stop, whichever comes first — one wait on both, woken by the
        # turn's own stop event (``TurnHandle.wait_stopped``, the wake-up a
        # Stop already sets), not a poll. The chat lane's card wait polls
        # every second because it must also notice a dropped connection and
        # beat a keep-alive; this wait has neither, so an event is the faithful
        # and immediate form. Before this the wait looked at nothing but the
        # answer: Stop left the reply "running" up to five minutes, and Allow
        # pressed after Stop still did the action.
        stop_wait = asyncio.ensure_future(handle.wait_stopped()) if handle is not None else None
        try:
            await asyncio.wait(
                {fut} if stop_wait is None else {fut, stop_wait},
                timeout=RISK_ASK_TIMEOUT_S,
                return_when=asyncio.FIRST_COMPLETED,
            )
        except asyncio.CancelledError:
            note.answered("stopped", "app")
            registry.pop(ap_id)
            return False
        finally:
            if stop_wait is not None:
                stop_wait.cancel()
        if self._stop_requested():
            # Stop wins over an answer that landed in the same instant: after
            # Stop, nothing is done. The card is withdrawn — its id leaves the
            # registry (a late Allow resolves nothing and is told so by
            # ``_decide``); the reply's ``done`` frame takes it off the panel;
            # the door refuses with its "stopped" words and denies the row.
            note.answered("stopped", settled_actor(registry, ap_id, "stopped"))
            registry.pop(ap_id)
            return _risk_door.Verdict(False, _risk_door.STOPPED_TAIL)
        if not fut.done():
            note.answered("timeout", "timeout")
            registry.pop(ap_id)
            return False
        try:
            decision = fut.result()
        except (asyncio.CancelledError, Exception):  # noqa: BLE001 — an answer that failed to arrive is a refusal
            note.answered("stopped", "app")
            registry.pop(ap_id)
            return False
        note.answered(str(decision), settled_actor(registry, ap_id, str(decision)))
        registry.pop(ap_id)
        return str(decision) in ("once", "conversation", "tab")

    async def _emit_models(self, conn: Any) -> None:
        """Send the ``models`` frame from its own task; never raises (v1.274.0)."""
        try:
            await self.emit(conn, P.PANEL_EVENT_MODELS, await self.models())
        except Exception:  # noqa: BLE001 — a probe that fails must not become a socket error
            logger.debug("panel model list failed", exc_info=True)

    # --- the skills list (v1.454.0) ---------------------------------------

    def skills_view(self) -> dict[str, Any]:
        """The ``skills`` payload: the rows the chat composer's "/" menu offers.

        The same registry ``GET /skills`` reads, projected to what a list
        needs (``name``, ``description``, ``argument_hint``) and without the
        skills marked not to be offered in a "/" menu (``user-invocable:
        false``). Never a skill's body. A registry that cannot be read is an
        empty list with the reason, never a guessed one; never raises.
        """
        reg = getattr(self.platform, "skills", None)
        try:
            rows = list(reg.list()) if reg is not None else []
        except Exception as exc:  # noqa: BLE001 — an unreadable registry is said, not invented
            logger.debug("panel skills list failed", exc_info=True)
            return {"skills": [], "error": f"{type(exc).__name__}: {exc}"[:200]}
        out: list[dict[str, str]] = []
        for sk in rows:
            if not bool(getattr(sk, "user_invocable", True)):
                continue
            name = str(getattr(sk, "name", "") or "").strip()
            if not name:
                continue
            out.append({
                "name": name[:PANEL_SKILL_NAME_CHARS],
                "description": " ".join(str(getattr(sk, "description", "") or "").split())[:PANEL_SKILL_DESC_CHARS],
                "argument_hint": str(getattr(sk, "argument_hint", "") or "")[:80],
            })
        out.sort(key=lambda row: row["name"].lower())
        return {"skills": out[:PANEL_SKILLS_MAX]}

    async def _record_save(self, conn: Any, params: dict[str, Any]) -> None:
        """v1.458.0: save a task the user recorded in this browser as a skill
        (``browser/recording.py``), answer ``recorded`` in plain words, then
        send the "/" list again so the new skill is there at once."""
        from .recording import RecordingRefused, save_and_publish

        try:
            saved = await save_and_publish(
                self.platform, params, replace=params.get("replace") is True
            )
        except RecordingRefused as exc:
            await self.emit(conn, P.PANEL_EVENT_RECORDED, {"ok": False, "text": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001 — said, never a dead button
            logger.warning("recording save failed", exc_info=True)
            await self.emit(
                conn,
                P.PANEL_EVENT_RECORDED,
                {"ok": False, "text": f"Iron Jarvis could not save the recording ({type(exc).__name__}). Try again."},
            )
            return
        await self.emit(
            conn, P.PANEL_EVENT_RECORDED, {"ok": True, "name": saved.name, "text": saved.words()}
        )
        await self.emit(conn, P.PANEL_EVENT_SKILLS, self.skills_view())

    def _skill_refusal(self, name: str) -> str:
        """``""`` when ``name`` may run from the panel, else the sentence why not."""
        if not name:
            return ""
        reg = getattr(self.platform, "skills", None)
        try:
            sk = reg.get(name) if reg is not None else None
        except Exception:  # noqa: BLE001 — unreadable reads as missing
            sk = None
        if sk is None:
            return (
                f"There is no skill named “{name}” any more. Type / to pick"
                " from the skills you have now."
            )
        if not bool(getattr(sk, "user_invocable", True)):
            return f"The skill “{name}” is not offered in a / list, so it cannot be picked here."
        return ""

    # --- the model list (v1.267.0) -----------------------------------------

    #: The keys a model row carries to the panel. Everything else the catalog
    #: knows (base URLs, exec paths, context windows) is Jarvis's business.
    MODEL_ROW_KEYS = ("provider", "model", "name", "available", "kind")

    async def models(self, *, force: bool = False) -> dict[str, Any]:
        """The ``models`` payload: THE catalog every Iron Jarvis picker reads, projected.

        ``selectable_models`` (the function behind ``GET /models``) is the one
        source, so the sidebar can never offer a model the app would not, and
        never miss one it would. It probes live providers, so it runs off the
        loop and is cached for :attr:`models_ttl_s`. A failure is reported as an
        EMPTY list with the reason — the select then offers Default alone, which
        is true — never as a guessed list.
        """
        now = asyncio.get_running_loop().time()
        cached = self._models_cache
        if cached is not None and not force and now - cached[0] < self.models_ttl_s:
            return cached[1]
        cfg = getattr(self.platform, "config", None)
        default = {
            "provider": str(getattr(cfg, "default_provider", "") or ""),
            "model": str(getattr(cfg, "default_model", "") or ""),
        }
        try:
            rows = await asyncio.to_thread(self._catalog)
            payload: dict[str, Any] = {
                "models": [
                    {k: row[k] for k in self.MODEL_ROW_KEYS if k in row}
                    for row in rows
                    if row.get("provider") and row.get("model")
                ],
                "default": default,
                # v1.269.0: whether the microphone can do anything, and via what.
                "voice": self.voice_view(),
            }
        except Exception as exc:  # noqa: BLE001 — an unreadable catalog is reported, not invented
            logger.debug("panel model list failed", exc_info=True)
            payload = {
                "models": [],
                "default": default,
                "voice": self.voice_view(),
                "error": f"{type(exc).__name__}: {exc}"[:200],
            }
        self._models_cache = (now, payload)
        return payload

    def _catalog(self) -> list[dict[str, Any]]:
        """Blocking: the same rows ``GET /models`` answers with."""
        from ..daemon.routes.connections import selectable_models

        deps = SimpleNamespace(
            platform=self.platform, fleet=getattr(self.platform, "fleet", None)
        )
        return list(selectable_models(deps))

    # --- dictation (v1.269.0) --------------------------------------------------

    #: One PCM chunk may carry this much (a quarter second at 16 kHz is 8 KB;
    #: this is generous, and a frame far past it is not a chunk).
    VOICE_MAX_CHUNK_BYTES = 64 * 1024
    #: A whole dictation, in PCM bytes: ~13 minutes at 16 kHz. Reached, the
    #: daemon stops it and answers with what it heard, rather than growing.
    VOICE_MAX_SESSION_BYTES = 25 * 1024 * 1024

    def voice_view(self) -> dict[str, Any]:
        """``{available, backend, hint}`` — the same answer ``GET /voice/status`` gives."""
        deps = self.deps
        if deps is None:
            return {"available": False, "backend": None, "hint": "Voice is not wired in this copy of Iron Jarvis."}
        try:
            from ..daemon.routes.voice import voice_capability

            cap = voice_capability(deps)
            return {
                "available": bool(cap.get("available")),
                "backend": cap.get("backend"),
                "hint": str(cap.get("hint") or ""),
            }
        except Exception as exc:  # noqa: BLE001 — a broken probe is reported, never a guess
            logger.debug("panel voice capability failed", exc_info=True)
            return {"available": False, "backend": None, "hint": f"Voice could not be checked: {exc}"[:200]}

    async def _voice(self, conn: Any, params: dict[str, Any]) -> None:
        """One ``voice`` action: start, chunk, stop or cancel a dictation.

        THE AUDIO GOES WHERE THE APP'S OWN DICTATION GOES. The offline Vosk
        model when it is bundled — streaming, partial words as they form, no
        network — else the HTTP transcription backend the user configured, fed
        one WAV clip at stop. Never a browser vendor's speech service: the
        add-on runs beside the user's signed-in tabs, and what they say to it
        is theirs.
        """
        op = str(params.get("op") or "")
        if op == "start":
            await self._voice_start(conn)
        elif op == "chunk":
            await self._voice_chunk(conn, params)
        elif op == "stop":
            await self._voice_stop(conn)
        elif op == "cancel":
            self._cancel_voice()
        else:
            await self.emit(
                conn,
                P.PANEL_EVENT_ERROR,
                {"text": f"Iron Jarvis does not know the voice step {op!r}.", "reason": "voice_failed"},
            )

    def _cancel_voice(self) -> None:
        """Drop the dictation in progress and (SB7) a transcription still
        running for the last one: nothing of either reaches the box."""
        self._voice_session = None
        task, self._voice_task = self._voice_task, None
        if task is not None and not task.done():
            task.cancel()

    def _local_only(self) -> bool:
        """Third review SB12 (decision G20): "Start new chats local only"
        (``chat_new_local_only``) covers the sidebar: its replies run Local
        only and its dictation never goes to a speech service off this
        network. Read live, so a change in Settings applies to the next reply."""
        cfg = getattr(self.platform, "config", None)
        return getattr(cfg, "chat_new_local_only", False) is True

    def _voice_kept_here(self) -> str:
        """SB12: under Local only, the sentence refusing a speech service off
        this network ("" when dictation may go where it is set to). The
        bundled offline model never leaves this PC; anything else is judged
        by the voice route's own rule (``routes.voice._refuse_off_network``),
        in its own words."""
        if not self._local_only():
            return ""
        backend_of = getattr(self.deps, "_voice_backend", None)
        backend = backend_of() if callable(backend_of) else None
        if not backend:
            return ""
        from ..daemon.routes.voice import SpeechKeptHere, _refuse_off_network

        try:
            _refuse_off_network(str(backend[0] or ""), str(backend[1] or ""))
        except SpeechKeptHere as exc:
            return str(exc)
        return ""

    async def _voice_start(self, conn: Any) -> None:
        self._voice_session = None
        task = self._voice_task
        if task is not None and not task.done():
            # SB7: one dictation writes the box at a time. The last one's words
            # are still on their way; a second must not race them.
            await self.emit(
                conn, P.PANEL_EVENT_ERROR, {"text": VOICE_BUSY, "reason": "voice_busy"}
            )
            return
        cap = self.voice_view()
        if not cap.get("available"):
            await self.emit(
                conn,
                P.PANEL_EVENT_ERROR,
                {
                    "text": cap.get("hint") or "Voice is not set up in Iron Jarvis yet.",
                    "reason": "voice_unavailable",
                },
            )
            return
        use_local = cap.get("backend") == "local"
        if not use_local:
            # SB12: under "Start new chats local only", never a speech service
            # off this network: the built-in offline model when it is here,
            # else refused before a word is recorded.
            kept = self._voice_kept_here()
            if kept:
                path_of = getattr(self.deps, "_vosk_model_path", None)
                if callable(path_of) and path_of() is not None:
                    use_local = True
                else:
                    await self.emit(
                        conn, P.PANEL_EVENT_ERROR, {"text": kept, "reason": "voice_unavailable"}
                    )
                    return
        if use_local:
            loader = getattr(self.deps, "_vosk_model", None)
            model = await asyncio.to_thread(loader) if callable(loader) else None
            if model is None:
                await self.emit(
                    conn,
                    P.PANEL_EVENT_ERROR,
                    {"text": "The offline speech model could not be loaded.", "reason": "voice_unavailable"},
                )
                return
            import vosk  # lazy: only where a model exists

            recognizer = await asyncio.to_thread(vosk.KaldiRecognizer, model, 16000)
            self._voice_session = _VoiceSession("stream", "local", recognizer)
        else:
            self._voice_session = _VoiceSession("clip", str(cap.get("backend") or ""))
        await self.emit(
            conn,
            P.PANEL_EVENT_TRANSCRIPT,
            {"text": "", "partial": "", "final": False, "listening": True, "backend": self._voice_session.backend},
        )

    async def _voice_chunk(self, conn: Any, params: dict[str, Any]) -> None:
        session = self._voice_session
        if session is None:
            return  # a chunk after cancel/stop is ordinary, not an error
        import base64

        try:
            raw = base64.b64decode(str(params.get("pcm_b64") or ""), validate=False)
        except Exception:  # noqa: BLE001
            raw = b""
        if not raw:
            return
        if len(raw) > self.VOICE_MAX_CHUNK_BYTES:
            self._voice_session = None
            await self.emit(
                conn,
                P.PANEL_EVENT_ERROR,
                {"text": "The add-on sent an audio chunk larger than a chunk can be.", "reason": "voice_failed"},
            )
            return
        session.total += len(raw)
        if session.mode == "stream":
            done = await asyncio.to_thread(session.rec.AcceptWaveform, raw)
            if done:
                segment = str(json.loads(session.rec.Result()).get("text") or "").strip()
                if segment:
                    session.text = f"{session.text} {segment}".strip()
                session.partial = ""
            else:
                session.partial = str(json.loads(session.rec.PartialResult()).get("partial") or "")
            await self.emit(
                conn,
                P.PANEL_EVENT_TRANSCRIPT,
                {"text": session.text, "partial": session.partial, "final": False, "backend": session.backend},
            )
        else:
            session.chunks.append(raw)
        if session.total >= self.VOICE_MAX_SESSION_BYTES:
            # Third review SB14: the daemon ends it at its size limit, and the
            # final frame says so (`limit`), so the panel releases the
            # microphone and tells the person why.
            await self._voice_stop(conn, limit=True)

    async def _voice_stop(self, conn: Any, *, limit: bool = False) -> None:
        session = self._voice_session
        self._voice_session = None
        if session is None:
            return
        extra: dict[str, Any] = {"limit": True} if limit else {}
        if session.mode == "stream":
            final = str(json.loads(await asyncio.to_thread(session.rec.FinalResult)).get("text") or "").strip()
            text = f"{session.text} {final}".strip()
            await self.emit(
                conn,
                P.PANEL_EVENT_TRANSCRIPT,
                {"text": text, "partial": "", "final": True, "backend": session.backend, **extra},
            )
            return
        pcm = b"".join(session.chunks)
        if not pcm:
            await self.emit(
                conn,
                P.PANEL_EVENT_TRANSCRIPT,
                {"text": "", "partial": "", "final": True, "backend": session.backend, **extra},
            )
            return
        # Third review SB7: the speech service is asked from ITS OWN TASK. This
        # handler is awaited by the add-on socket's read loop, so waiting here
        # held every other frame on that connection (a page action another
        # chat was running could time out) for as long as the service took.
        # The words are sent when they come back, to the connection the
        # sidebar is on then; a new start waits for them (`_voice_start`).
        self._voice_task = asyncio.ensure_future(
            self._transcribe(_ReplyRoute(conn), pcm, session.backend, extra)
        )

    async def _transcribe(
        self, route: "_ReplyRoute", pcm: bytes, backend: str, extra: dict[str, Any]
    ) -> None:
        """SB7: one dictation clip through the shared transcriber, bounded by
        :data:`VOICE_TRANSCRIBE_TIMEOUT_S`; its words (or why there are none)
        are the dictation's final frame. Never raises."""
        import base64

        from fastapi import HTTPException

        from ..daemon.routes import voice as _voice_routes
        from ..providers.egress import LocalOnlyRefused

        kwargs: dict[str, Any] = {}
        if self._local_only():
            # SB12: "Start new chats local only" covers the sidebar's dictation.
            kwargs["local_only"] = True
        try:
            result = await asyncio.wait_for(
                _voice_routes.transcribe_clip(
                    self.deps, audio_b64=base64.b64encode(_wav_bytes(pcm)).decode("ascii"),
                    mime="audio/wav", **kwargs,
                ),
                VOICE_TRANSCRIBE_TIMEOUT_S,
            )
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            await self.emit(route, P.PANEL_EVENT_ERROR, {"text": VOICE_TOO_SLOW, "reason": "voice_failed"})
            return
        except HTTPException as exc:
            await self.emit(route, P.PANEL_EVENT_ERROR, {"text": str(exc.detail), "reason": "voice_failed"})
            return
        except LocalOnlyRefused as exc:
            # SB12: the voice route's own sentence (nothing was sent).
            await self.emit(route, P.PANEL_EVENT_ERROR, {"text": str(exc), "reason": "voice_failed"})
            return
        except Exception as exc:  # noqa: BLE001 — an honest failure, never silence
            logger.debug("panel dictation failed", exc_info=True)
            await self.emit(
                route, P.PANEL_EVENT_ERROR, {"text": f"Transcription failed: {exc}"[:300], "reason": "voice_failed"}
            )
            return
        await self.emit(
            route,
            P.PANEL_EVENT_TRANSCRIPT,
            {
                "text": str(result.get("text") or "").strip(),
                "partial": "",
                "final": True,
                "backend": str(result.get("backend") or backend),
                **extra,
            },
        )

    async def _refuse_pick(
        self, conn: Any, provider: str, model: str, *, mark: dict[str, Any] | None = None
    ) -> bool:
        """Whether a (provider, model) pick must be refused — and say why if so.

        Checked against the daemon's own list BEFORE a turn starts. The router
        would refuse an unreachable explicit pick on its own (v1.162.0: never a
        silent substitute), but that refusal arrives as a failed turn; here the
        user gets one sentence and their message stays in the box.
        """
        if not provider and not model:
            return False
        catalog = await self.models()
        rows = catalog.get("models") or []
        match = next(
            (r for r in rows if r.get("provider") == provider and r.get("model") == model),
            None,
        )
        if match is None:
            await self.emit(
                conn,
                P.PANEL_EVENT_ERROR,
                {
                    "text": f"{model or provider} is not on Iron Jarvis's model list any"
                    " more. Pick another model in the sidebar.",
                    "reason": "model_unknown",
                    **(mark or {}),
                },
                ends_turn=True,  # SB8: the Send it refuses never started
            )
            return True
        if match.get("available") is False:
            await self.emit(
                conn,
                P.PANEL_EVENT_ERROR,
                {
                    "text": f"{model} ({provider}) is not connected right now. Pick"
                    " another model, or connect it in Jarvis → Connections.",
                    "reason": "model_unavailable",
                    **(mark or {}),
                },
                ends_turn=True,  # SB8: the Send it refuses never started
            )
            return True
        return False

    async def _send(
        self,
        conn: Any,
        text: str,
        *,
        provider: str = "",
        model: str = "",
        pointed: dict[str, Any] | None = None,
        skill: str = "",
        window: str = "",
    ) -> None:
        # Third review SB10: every refusal of this Send says it was this Send,
        # from which window, so that window puts the words back in its box and
        # takes back the bubble it drew (every window gets the frame).
        mark: dict[str, Any] = {"send_refused": True}
        if window:
            mark["window"] = window
        if skill and not text:
            # A skill picked alone, the chat page's own words for it (the lane
            # reads them as "no arguments", ``chat_turn.skill_invocation_args``).
            from ..harness.sessions import SKILL_ALONE

            text = SKILL_ALONE.format(name=skill)
        if not text:
            await self.emit(
                conn, P.PANEL_EVENT_ERROR, {"text": "There was nothing to send.", **mark},
                ends_turn=not self.running,  # SB8: the Send it refuses never started
            )
            return
        if self.running and self._end_sent:
            # The reply's END HAS BEEN SHOWN (its `done` left); only its
            # tidy-up is still running (since the third fix plan the lane
            # waits for its usage row's write after `done`). A Send in that
            # instant waits for it, bounded, instead of being refused as
            # "still working on the last message".
            task = self._task
            if task is not None:
                await asyncio.wait({task}, timeout=END_TIDY_WAIT_S)
        if self.running:
            # REFUSED, not queued. A second turn would race the first for the
            # same panel and the user would watch two answers interleave with
            # no way to tell which question either belonged to.
            await self.emit(
                conn,
                P.PANEL_EVENT_ERROR,
                {
                    "text": "Iron Jarvis is still working on the last message."
                    " Stop it first.",
                    **mark,
                },
            )
            return
        refusal = self._over_budget()
        if refusal:
            await self.emit(conn, P.PANEL_EVENT_ERROR, {"text": refusal,
                                                        "reason": "rate_limited", **mark},
                            ends_turn=True)  # SB8: the Send it refuses never started
            return
        # v1.267.0: the pick is checked before anything is spent on the turn.
        if await self._refuse_pick(conn, provider, model, mark=mark):
            return
        # v1.454.0: so is the skill — the lane's own refusal of an unknown
        # skill is an HTTP 404, which here would read as a crash.
        skill_refusal = self._skill_refusal(skill)
        if skill_refusal:
            await self.emit(
                conn, P.PANEL_EVENT_ERROR,
                {"text": skill_refusal, "reason": "skill_unknown", **mark},
                ends_turn=True,  # SB8: the Send it refuses never started
            )
            return
        # SB3: the conversation this question continues is its chat. Read back
        # first if nothing has been (a send before any open), then checked: a
        # chat deleted in the chat list is not carried to the model any more,
        # the panel is told in one sentence, and this question starts a new one.
        await self._restore(0)
        gone = await self._chat_gone()
        if gone:
            await self.emit(conn, P.PANEL_EVENT_HISTORY, {"turns": [], "notice": gone})
        self._conn = conn
        self._turn_id = f"panel_{secrets.token_hex(8)}"
        self._steers.clear()
        # A new question is a new set of cards. Ids from the last turn cannot
        # be answered any more anyway (their turn has gone), and keeping them
        # would only widen what this panel may resolve.
        self._offered.clear()
        self._cards.clear()
        # SB2/SB13: a new reply has been asked to stop by nobody yet.
        self._stop_asked = False
        self._closed_stop = False
        self._delta_seen = False
        self._answer = []
        self._segment = []
        self._turn_text = text
        self._remembered = False
        await self.emit(conn, P.PANEL_EVENT_STATE, self._state(running=True))
        self._end_sent = False
        self._task = asyncio.ensure_future(
            self._run(
                conn, text, self._turn_id, provider=provider, model=model, pointed=pointed,
                skill=skill,
            )
        )

    def _state(self, *, running: bool | None = None) -> dict[str, Any]:
        """The ``state`` frame: whether a turn runs, and whether THIS tab is allowed.

        ``tab_allowed`` (v1.266.0) is about the tab the user is looking at — the
        daemon's cached active tab — and it is a FACT the header paints, not a
        promise: the grant is recorded before any frame says so, and the panel
        asks again on every tab switch.
        """
        return {
            "running": self.running if running is None else bool(running),
            "tab_allowed": self._tab_allowed(),
            # v1.270.0: the one switch, as the daemon holds it.
            "auto_allow": self._auto_allowed(),
            # Third review SB12 (G20): "Start new chats local only" covers the
            # sidebar; the panel shows it is on (it is changed in Settings).
            "local_only": self._local_only(),
        }

    def _tab_allowed(self) -> bool:
        runtime = getattr(self.platform, "browser", None)
        try:
            return bool(runtime is not None and runtime.active_tab_allowed())
        except Exception:  # noqa: BLE001 — a header line must never break the socket
            return False

    # --- the conversation and the switch (v1.270.0) -----------------------------

    def history_view(self) -> dict[str, Any]:
        """The ``history`` payload: the conversation as the panel paints it.

        v1.339.0: while a turn RUNS, ``live`` carries its question and the
        answer streamed so far. The turn keeps running when the side panel is
        closed (it lives here, not in the panel), but a reopened panel was
        replayed only the finished exchanges — the deltas that followed landed
        under no question, starting mid-sentence."""
        view: dict[str, Any] = {
            "turns": [
                {"role": row["role"], "text": row["content"]} for row in self._history
            ]
        }
        if self.running and not self._remembered and self._turn_text:
            view["live"] = {"question": self._turn_text, "answer": "".join(self._answer)}
        return view

    def _remember(self, role: str, text: str) -> None:
        """Append one message and keep the bound. Empty text is not a message."""
        text = str(text or "").strip()
        if not text:
            return
        self._history.append({"role": role, "content": text})
        if self.history_limit > 0:
            del self._history[: -self.history_limit]

    async def _remember_exchange(
        self, *, stopped_on_close: bool = False, stopped_by_you: bool = False
    ) -> None:
        """Write the running turn's question and what the user READ, once.

        ``stopped_on_close`` (SB13, third review): the reply was stopped by the
        last sidebar window closing, so the answer is written with
        :data:`STOPPED_ON_CLOSE` after it (nobody was watching it end).
        ``stopped_by_you`` (fourth plan G27, C-BS-2): the person pressed Stop,
        so it is written with :data:`STOPPED_BY_YOU` (a close wins: its words).

        Called at every exit — the ``done`` branch of :meth:`_translate` (before
        the frame is sent), the failure branch and the ``finally`` of
        :meth:`_run` — and idempotent, so the first caller wins and a stopped
        turn (no ``done`` frame at all) is still written before its stop frame.

        SB3: written to the conversation in memory at once (an ``open`` racing
        this replays it), then to its chat, off the event loop, before this
        returns, so the terminal frame that follows leaves after both.
        """
        if self._remembered:
            return
        self._remembered = True
        question = str(self._turn_text or "").strip()
        read = "".join(self._answer).strip()
        if stopped_on_close:
            answer = f"{read}\n\n{STOPPED_ON_CLOSE}" if read else STOPPED_ON_CLOSE_EMPTY
        elif stopped_by_you:
            answer = f"{read}\n\n{STOPPED_BY_YOU}" if read else STOPPED_BY_YOU_EMPTY
        else:
            answer = ("".join(self._answer) or "(Iron Jarvis gave no answer.)").strip()
        self._remember("user", question)
        self._remember("assistant", answer)
        self._answer = []
        await self._save_exchange(question, answer)

    # --- the conversation as a chat (SB3, third review) -----------------------

    def _sidebar_chat(self) -> Any:
        """The sidebar's chat writer (``panel_chat.SidebarChat``), or ``None``
        when this platform has no database (a stand-in in a unit test): the
        conversation then lives in memory only, exactly as before."""
        if self._chat_built:
            return self._chat
        self._chat_built = True
        try:
            from sqlalchemy.engine import Engine

            engine = getattr(self.platform, "engine", None)
            if not isinstance(engine, Engine):
                return None
            from .panel_chat import SidebarChat

            store = getattr(self.deps, "comm_thread_store", None)
            if store is None:
                from ..comm.threads import CommThreadStore

                store = CommThreadStore(
                    engine, event_bus=getattr(self.platform, "event_bus", None)
                )
            self._chat = SidebarChat(store, engine)
        except Exception:  # noqa: BLE001 — no chat is still a working sidebar
            logger.warning("the sidebar's chat could not be set up", exc_info=True)
            self._chat = None
        return self._chat

    async def _save_exchange(self, question: str, answer: str) -> None:
        """Append one exchange to the sidebar's chat (created on the first).
        Never raises: the conversation in memory is kept either way."""
        chat = self._sidebar_chat()
        if chat is None:
            return
        async with self._chat_lock:
            try:
                self._chat_id = await asyncio.to_thread(
                    chat.save_exchange, self._chat_id, question, answer
                )
                # A conversation exists now: nothing may be restored over it.
                self._restored = True
                self._unavailable_said = False
                self._lost = False
            except Exception:  # noqa: BLE001 — the reply itself was delivered
                logger.warning("the sidebar's exchange could not be saved as a chat",
                               exc_info=True)

    def _say_unavailable(self, shown: int) -> str:
        """The one sentence for a panel that shows a conversation the daemon
        cannot carry on, said once; "" when the panel shows nothing."""
        if shown <= 0 or self._unavailable_said:
            return ""
        from .panel_chat import NOT_AVAILABLE

        self._unavailable_said = True
        return NOT_AVAILABLE

    async def _restore(self, shown: int) -> str:
        """After a restart, read the conversation back from its chat (once).

        Returns the sentence to show when the panel shows ``shown`` messages
        that cannot be carried on (the chat was deleted, or the database could
        not be read), else "". A read fault is tried again at the next ``open``.
        """
        if self._restored:
            # Nothing was found to restore and nothing has been said since: a
            # panel (another window's, or this one after a reconnect) that
            # shows a conversation is told it cannot be carried on.
            if self._lost and not self._history and not self._chat_id:
                return self._say_unavailable(shown)
            return ""
        chat = self._sidebar_chat()
        if chat is None or self.running or self._history or self._chat_id:
            self._restored = True
            return ""
        from .panel_chat import SidebarChatUnreadable

        async with self._chat_lock:
            try:
                found = await asyncio.to_thread(chat.current)
            except SidebarChatUnreadable:
                return self._say_unavailable(shown)
            except Exception:  # noqa: BLE001 — a stand-in that raises otherwise
                logger.warning("the sidebar's chat could not be read", exc_info=True)
                return self._say_unavailable(shown)
            self._restored = True
            if found is None:
                self._lost = True
                return self._say_unavailable(shown)
            if self._history or self._chat_id:
                return ""  # a conversation began meanwhile: it stands
            self._chat_id, rows = found
            for row in rows:
                self._remember(row["role"], row["content"])
            return ""

    async def _chat_gone(self) -> str:
        """Before a question: is the conversation's chat still there? When it
        was deleted in the chat list, the conversation is forgotten here too
        (it is not carried to the model again) and the sentence to show is
        returned; "" otherwise."""
        chat = self._sidebar_chat()
        if chat is None or not self._chat_id:
            return ""
        async with self._chat_lock:
            chat_id = self._chat_id
            if not chat_id or await asyncio.to_thread(chat.exists, chat_id):
                return ""
        if self._chat_id != chat_id:
            return ""
        from .panel_chat import NOT_AVAILABLE

        self._chat_id = ""
        self._history.clear()
        return NOT_AVAILABLE

    async def _reset(self, conn: Any) -> None:
        """Forget the conversation. Refused while a turn runs — its answer would
        land in a conversation the user just emptied.

        SB3: its chat stays in the chat list, whole; it is no longer the
        sidebar's conversation, so the next exchange starts a new chat and a
        restart restores nothing from before the Reset."""
        if self.running:
            await self.emit(
                conn,
                P.PANEL_EVENT_ERROR,
                {
                    "text": "Iron Jarvis is still working on the last message."
                    " Stop it before starting a new conversation.",
                    "reason": "still_running",
                },
            )
            return
        self._history.clear()
        self._chat_id = ""
        self._restored = True
        self._unavailable_said = False
        self._lost = False
        chat = self._sidebar_chat()
        if chat is not None:
            async with self._chat_lock:
                try:
                    await asyncio.to_thread(chat.close_all)
                except Exception:  # noqa: BLE001 — the new conversation starts anyway
                    logger.warning("the sidebar's chat could not be closed", exc_info=True)
        await self.emit(conn, P.PANEL_EVENT_HISTORY, self.history_view())

    def _auto_allowed(self) -> bool:
        runtime = getattr(self.platform, "browser", None)
        try:
            return bool(runtime is not None and runtime.auto_allowed())
        except Exception:  # noqa: BLE001 — a header line must never break the socket
            return False

    def _set_auto_allow(self, on: bool) -> bool:
        runtime = getattr(self.platform, "browser", None)
        if runtime is None:
            return False
        try:
            return bool(runtime.set_auto_allow(on))
        except Exception:  # noqa: BLE001
            logger.debug("auto-allow could not be set", exc_info=True)
            return False

    def _once_kind(self, approval_id: str) -> str:
        """Fourth review BS1: the kind of card ``approval_id`` is, read from
        the approval registry (``ChatApprovals.once_kind``): ``""`` for an
        ordinary card the switch may answer, else "browser_risk",
        "local_only", … — a card answered one action at a time. A registry
        that cannot say (a stand-in) reads as ordinary; the real one refuses
        a grant's answer on such a card by itself."""
        from ..daemon.routes.chat import _approvals

        try:
            read = getattr(_approvals(SimpleNamespace(platform=self.platform)), "once_kind", None)
            return str(read(approval_id) or "") if callable(read) else ""
        except Exception:  # noqa: BLE001 — a card's kind never breaks the socket
            logger.debug("panel: card kind could not be read", exc_info=True)
            return ""

    async def _auto_allow(self, conn: Any, params: dict[str, Any]) -> None:
        """Flip the one switch, answer the card that is up, and say what is true.

        The switch is ``TabGrants.grant_all`` — the grant the per-tab card
        already wrote, widened to every tab — so the chat lane's card predicate
        skips the ordinary card with no change of its own. Turning it ON also
        resolves every ORDINARY card this panel has offered and not yet
        answered, as ``once``: the user pressed "always" while looking at one,
        and a switch that left that card waiting would look broken. Fourth
        review BS1: a card answered one action at a time (a risk card, a Local
        only card, any card the registry holds with a kind) is left up and
        answerable — only the card itself answers it, and the registry
        refuses the switch's answer anyway. Ids already answered resolve
        False and are ignored. The ``state`` frame that follows is what the
        header paints from — a fact, recorded before it is said.
        """
        on = bool(params.get("on"))
        # SB4: a press is the switch's one truth (see :meth:`_restore_switch`).
        self._switch_pressed = on
        state = self._set_auto_allow(on)
        # SB2: the cards of a reply asked to stop are withdrawn; the switch
        # still turns on (for the next reply) but answers none of them.
        if state and not self._stop_requested():
            from ..core.approvals import resolve_as as _resolve_as
            from ..daemon.routes.chat import _approvals

            registry = _approvals(SimpleNamespace(platform=self.platform))
            # v1.402.0: never a RISK card (payment, password, a destructive
            # word, a flagged page): turning the switch on while one waits
            # approved it — the one card the switch must never answer.
            # Fourth review BS1: nor any other card with a kind (a Local only
            # card): the registry's mark is the one field.
            for approval_id in list(self._offered):
                if self._once_kind(approval_id):
                    continue
                try:
                    # v1.402.0: settled by the switch the person just turned
                    # on — a grant, not a second answer (the autonomy metrics).
                    _resolve_as(registry, approval_id, "once", actor="grant")
                    self._cards.pop(approval_id, None)  # SB1: answered
                except Exception:  # noqa: BLE001 — an already-answered card
                    logger.debug("auto-allow: card %s not resolvable", approval_id, exc_info=True)
        # SB4: every open window paints the new value, not only the one pressed.
        await self._tell_every_window(conn, P.PANEL_EVENT_STATE, self._state())

    @staticmethod
    def _route_notice(route: Any, reply: str = "") -> str:
        """One sentence for a failover or a mock answer; ``""`` for everything else.

        ``reply`` (v1.359.0 integrate, lane 9A review): the demo model's own
        reply opens by saying what it is (``mock.DEMO_OPENING``), so the
        notice above it then says only what to do — the demo is named ONCE,
        as the chat's ``demo_note`` does."""
        if not isinstance(route, dict):
            return ""
        reason = str(route.get("reason") or "")
        provider = str(route.get("provider") or "")
        model = str(route.get("model") or "")
        answered = "/".join(p for p in (provider, model) if p) or "another model"
        if reason == "failover":
            asked = str(route.get("from") or route.get("requested") or "the model you picked")
            why = str(route.get("why") or "").strip()
            tail = f" ({why})" if why else ""
            return f"Answered by {answered} — {asked} was unreachable{tail}."
        if reason == "mock":
            # v1.357.0 (G76): the demo's one name and sentence. Not "no real
            # model is connected": the default can still be the demo model
            # while a real one is connected.
            if _opens_as_demo(reply):
                return DEMO_SHORT_NOTICE
            return (
                "Answered by the demo model: a scripted demo, not real answers. "
                "Choose a model in Iron Jarvis to get real answers."
            )
        return ""

    def _over_budget(self) -> str:
        """"" if this Send fits the rolling budget, else the sentence to say.

        THE SILENT FAILURE: a paired browser billing the user at socket speed.
        The refusal NAMES the limit and when it lifts, because a rate limit the
        user cannot see reads as the sidebar being broken — they press Send,
        nothing happens, and nothing says why.

        Monotonic, off the event loop's own clock. A timestamp list rather than
        a counter+reset: a fixed bucket lets a client spend the whole allowance
        at the end of one window and the whole of the next immediately after,
        which is twice the rate the constant claims.
        """
        try:
            now = asyncio.get_running_loop().time()
        except RuntimeError:  # pragma: no cover — no loop means no turn either
            return ""
        self._starts = [t for t in self._starts if now - t < PANEL_TURN_WINDOW_S]
        if len(self._starts) < PANEL_TURNS_PER_WINDOW:
            self._starts.append(now)
            return ""
        wait_s = int(PANEL_TURN_WINDOW_S - (now - self._starts[0])) + 1
        return (
            f"The sidebar is limited to {PANEL_TURNS_PER_WINDOW} messages a"
            f" minute, and that many have just been sent. Try again in about"
            f" {wait_s} seconds, or use the Iron Jarvis window."
        )

    def _stop_own_turn(self) -> bool:
        """Stop this sidebar's reply as the person's own Stop; True when it
        was stopped (fourth plan G27, BS2).

        ``TURNS.stop`` knows only a registered turn, and the sidebar's reply
        registers several awaits after its Send was answered: a Stop (or the
        last window closing) in that gap was lost and the reply ran to its
        end. ``stop_or_keep`` KEEPS such a Stop and the registry applies it
        when the turn registers, so the reply never starts. "stopped" and
        "pending" are stopped; "refused" (refused before it ran), "ended" and
        anything else are nothing running."""
        if not self._turn_id:
            return False
        return TURNS.stop_or_keep(self._turn_id, by=STOP_BY_YOU) in ("stopped", "pending")

    async def _stop(self, conn: Any) -> None:
        if self._stop_own_turn():
            # SB2: from here every card of this reply is withdrawn.
            self._stop_asked = True
            return
        # Nothing addressable is running. Say so plainly rather than
        # leaving the panel's Stop button looking like it worked on
        # something: the state frame is what flips it back to idle.
        await self.emit(conn, P.PANEL_EVENT_STATE, self._state())

    async def _steer(self, conn: Any, params: dict[str, Any]) -> None:
        note = str(params.get("text") or "").strip()
        note_id = str(params.get("id") or "")
        if not note:
            return
        if not self.running:
            # A note with no turn to join was never taken, and the panel marks
            # it pending until something says otherwise. `done` is the frame
            # that flushes pending notes as not taken.
            await self.emit(
                conn, P.PANEL_EVENT_DONE, {"reason": STEER_NOT_TAKEN, "text": ""}
            )
            return
        self._steers.append({"id": note_id, "text": note})

    async def _decide(
        self, conn: Any, action: str, approval_id: str, scope: str = "", *, named: bool = False
    ) -> None:
        """Answer a mid-turn approval through THE approval registry.

        ``scope`` (v1.262.0): ``"task"`` on an approve answers ``conversation``
        — the chat lane's grant for the REMAINING ROUNDS OF THIS TURN, which is
        exactly one task in the sidebar: every Send is a fresh turn with a fresh
        grant set and a fresh ``_offered``. Anything else answers ``once``. A
        ten-step task used to be ten cards; the user's words for the result
        were "a chat bot next to the window".

        The same one ``POST /chat/approvals/{id}`` answers and the same one the
        waiting turn is parked on — reached through ``routes.chat._approvals``
        rather than read off the platform here, because that helper owns the
        fallback a test double needs and a second reading of it is how two
        surfaces come to answer different copies. A chat-lane ask is announced
        ONLY on its own stream (it is deliberately absent from
        ``GET /chat/approvals/pending``), which is why the panel is told down
        this socket and answers back down it.

        A plain ``approve`` resolves ``once``: the panel shows one card for one
        call, and widening a grant the user was not asked to widen is a consent
        nobody gave. The wider grant exists only behind its own button.

        AN ID THIS PANEL NEVER OFFERED IS REFUSED, and that check is new.
        ``resolve`` has no ownership check of its own — proven by answering an
        ask filed by a separate agent session, on another thread, with a
        ``panel.approve``, and getting no error at all. The only thing that had
        ever stopped it was that ids are unguessable, which is a real defence
        and was also the ENTIRE defence, undocumented and untested. The
        registry is process-local and shared by chat, the agent runtime and
        MCP, so the panel must bring its own answer to "was this card mine?":
        :attr:`_offered`, written at the one place a card is emitted
        (:meth:`_translate`). The refusal is SPOKEN, like every other path out
        of this class — a button that silently does nothing is the failure this
        module is written against.
        """
        from ..daemon.routes.chat import _approvals

        if approval_id not in self._offered:
            await self.emit(
                conn,
                P.PANEL_EVENT_ERROR,
                {
                    "text": "That request was not asked in this sidebar, so"
                    " the sidebar will not answer it.",
                    "reason": "not_this_panel",
                },
            )
            return
        if self._stop_requested():
            # SB2 (third review): the reply was stopped, so its cards were
            # withdrawn. An answer pressed after Stop resolves NOTHING — the
            # chat lane's card wait notices Stop only at its next look (up to
            # a second later), and an Allow passed to it in between would still
            # do the action. Said, never silent.
            await self.emit(
                conn, P.PANEL_EVENT_ERROR, {"text": CARD_WITHDRAWN, "reason": "withdrawn"}
            )
            return
        # Fourth review BS1: the card's kind, from the registry's one mark (a
        # risk card, a Local only card, …); "" for an ordinary card.
        kind = self._once_kind(approval_id)
        allow_all = action == P.PANEL_ACTION_APPROVE and scope == SCOPE_ALL
        if allow_all and kind:
            # SB6 (third review, decision G18): a RISK card is answered one
            # action at a time. "Allow all page actions" on it is read as
            # nothing — not run, the switch unchanged, the card still up —
            # whatever add-on sent it, and the panel is told why. BS1: so is
            # every card with a kind, in the words for its kind.
            await self.emit(
                conn, P.PANEL_EVENT_ERROR,
                {"text": _all_refused(kind), "reason": "risk_card_all"},
            )
            return
        if action != P.PANEL_ACTION_APPROVE:
            decision = "deny"
        elif allow_all:
            # SB6: this one action (``once``), then the switch below.
            decision = "once"
        elif scope == "task":
            decision = "conversation"
        elif scope == "tab":
            # v1.266.0: ONE APPROVAL PER TAB. The chat lane records the grant
            # for the tab this call acts on and runs the call as "once"; every
            # later page action in that tab — this message or the next — runs
            # without a card until the tab closes (browser/grants.py).
            decision = "tab"
        else:
            decision = "once"
        try:
            ok = _approvals(SimpleNamespace(platform=self.platform)).resolve(
                approval_id, decision
            )
        except Exception:  # noqa: BLE001 — an answer must never break the socket
            logger.debug("panel approval resolve failed", exc_info=True)
            ok = False
        if not ok:
            await self.emit(
                conn,
                P.PANEL_EVENT_ERROR,
                {"text": "That request was already answered or has expired."},
            )
            return
        self._cards.pop(approval_id, None)  # SB1: answered, never shown again
        if kind and decision == "once" and not named:
            # SB6: an older add-on's "Allow all page actions" is this Allow
            # and then the switch; the next action reads this mark (BS1: the
            # card's kind, for the words).
            self._risk_allowed_unnamed = kind
        if allow_all:
            # SB6: the card's "Allow all page actions" is a press of the switch.
            await self._auto_allow(conn, {"on": True})

    # --- the turn ---------------------------------------------------------

    async def take_steer(self) -> str:
        """Hand the turn every queued note, and tell the panel each landed.

        Called BY the turn at its tool-round boundary, so being called is what
        makes a note consumed — there is no second flag to drift. The
        ``steered`` frames are emitted here, at the moment of consumption, for
        the same reason: the panel shows a note as pending until this frame,
        and any earlier emission would tell the user their correction landed
        before it had.
        """
        if not self._steers:
            return ""
        taken = self._steers[:]
        self._steers.clear()
        for note in taken:
            await self.emit(
                _ReplyRoute(self._conn),  # SB1: wherever the sidebar is now
                P.PANEL_EVENT_STEERED,
                {"id": note["id"], "text": note["text"]},
            )
        return "\n".join(note["text"] for note in taken)

    async def _run(
        self,
        conn: Any,
        text: str,
        turn_id: str,
        *,
        provider: str = "",
        model: str = "",
        pointed: dict[str, Any] | None = None,
        skill: str = "",
    ) -> None:
        """Run one turn and translate its SSE frames into panel events.

        ``provider``/``model`` (v1.267.0) are the panel's pick, already checked
        against the catalog; empty means the app's default, exactly as on the
        chat page. They ride the same ``ChatBody`` fields, so the lane's routing,
        its refusal of an unreachable explicit pick and its route disclosure are
        all the chat page's, unchanged.
        """
        from ..daemon.chat_stream import stream_chat_turn
        from ..daemon.schemas import ChatBody, ChatMessageBody

        # v1.270.0: THE CONVERSATION RIDES EVERY TURN. The lane's own history
        # planner fits it to the model's window and reports what it dropped,
        # exactly as for the chat page; a single-message body is what made
        # "now the second one" refer to nothing.
        body = ChatBody(
            messages=[
                *(
                    ChatMessageBody(role=row["role"], content=row["content"])
                    for row in self._history
                ),
                ChatMessageBody(role="user", content=text),
            ],
            # NO explicit tool picks: arming is granting (see the module
            # docstring). `auto_tools` runs the ordinary selection pass, so the
            # read tier is armed and the page-acting tier is armed
            # VISIBLE-BUT-UNGRANTED and pauses for the card.
            auto_tools=True,
            turn_id=turn_id,
            provider=provider,
            model=model,
            # v1.400.9: the element the user pointed at, for THIS turn only —
            # the lane fences and scans it (``chat_turn._pointed_section``).
            # Not remembered with the conversation: its words are the page's.
            pointed=pointed,
            # v1.454.0: the skill picked from the panel's "/" list, for THIS
            # turn only. The lane applies it exactly as for the chat page
            # (``chat_turn.skill_playbook``: the skill's body with the turn's
            # words as its arguments, scanned when it is not the user's own).
            skill=skill,
            # Third review SB12 (decision G20): "Start new chats local only"
            # covers the sidebar — the same field a new Chat page chat sends,
            # so the lane's Local only routing and its refusals (in its own
            # words, through the error frame) are the chat page's, unchanged.
            local_only=self._local_only(),
        )
        # THE CEILING. Computed here, per turn, from the live registry, and
        # passed to the chat lane — which drops anything outside it BEFORE the
        # armed list becomes `armed_grant`. Without it the autoselect pass
        # armed (and thereby granted) whatever the user's sentence suggested:
        # `write_file` wrote a real file from a read_only sidebar with no card.
        ceiling = browser_tool_ceiling(self.platform)
        # v1.271.0: THE TAB THE USER IS LOOKING AT. "It should simply work in the
        # tab I'm using, not open a new one unless I tell it to." The new-tab
        # tool is outside the ceiling — so nothing, not the autoselect pass and
        # not the family, can arm it — unless the sentence asks for a new tab.
        # The prompt and the tools' own descriptions say the same thing to the
        # model; this is the bound that holds when words do not.
        if not wants_new_tab(text):
            ceiling = frozenset(ceiling - {"browser_create_tab"})
        emitted_done = False
        # Third plan (G20 follow-up): the reply ENDED on its own error frame —
        # its end, never a Stop (the trailing `done` below says "failed").
        ended_on_error = False
        # v1.433.0 review: THE SIDEBAR'S OWN WORDS. Since the sidebar shows the
        # lane's end-of-turn words and its failure line, they are worded for
        # the sidebar (``failure_words.SURFACE_SIDEBAR``): it has no Retry
        # button and no Edit, so "Press Retry" and "edit an earlier message"
        # would name presses that are not there. This turn is its own task, and
        # every task the lane starts inside it copies this context.
        from ..providers.failure_words import reset_surface, set_sidebar_surface

        surface_token = set_sidebar_surface()
        # G27 (BS2): the lane handed back its stream, so the turn registered.
        # Before that, a raise is the lane refusing it before it ran.
        started = False
        try:
            gen = await stream_chat_turn(
                self.platform,
                self.personas,
                body,
                steer_source=self.take_steer,
                tool_ceiling=ceiling,
                # v1.262.0: THE WHOLE FAMILY, BY SURFACE. The same set as the
                # ceiling — the chat lane arms its read tier (the grant the
                # autoselect pass already gave any browser-shaped sentence) and
                # ask-arms its acting tier (visible, never granted: every page
                # action still pauses for the card), then applies the access
                # gate and this ceiling exactly as before. Without it a panel
                # turn was armed by the SENTENCE, and "book the first slot"
                # matched no rule — the sidebar could only talk.
                arm_family=ceiling,
            )
            started = True
            async for chunk in gen:
                for event, data in parse_sse(chunk):
                    if event == "done":
                        emitted_done = True
                        self._end_sent = True
                    elif event == "error":
                        ended_on_error = True
                    await self._translate(conn, event, data)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — an honest failure, never silence
            logger.debug("panel turn failed", exc_info=True)
            if not started:
                # G27 (BS2): refused before it ran. A Stop kept for it is
                # dropped now, and a Stop from here on is "nothing running".
                TURNS.note_refused(turn_id)
            # v1.270.0: what was read is remembered BEFORE the panel is told.
            await self._remember_exchange()
            # v1.516.0 (second review B12): never the exception's own text on
            # the sidebar. A sentence the app wrote is kept after the colon;
            # anything else is logged (masked) and the plain line stands.
            from ..core.failures import plain_failure

            plain = "Iron Jarvis could not finish that."
            words = plain_failure(plain, exc)
            await self.emit(
                _ReplyRoute(conn),  # SB1: wherever the sidebar is now
                P.PANEL_EVENT_ERROR,
                {"text": plain if words == plain else f"Iron Jarvis could not finish that: {words}"},
                ends_turn=True,  # SB8: the reply's own end
            )
            emitted_done = True
        finally:
            reset_surface(surface_token)
            self._task = None
            self._turn_id = ""
            # SB1: the reply's cards go with it.
            self._cards.clear()
            # v1.270.0: a STOPPED turn reaches here with no `done` frame sent
            # and its exchange unwritten — the half-answer the user read is
            # written now, before the stop's own terminal frame below.
            # SB13: a reply the last sidebar window's closing stopped is
            # written as stopped, so nothing reads it as finished.
            # G27 (C-BS-2): so is one the person stopped with Stop.
            stopped_here = self._stop_asked and not emitted_done and not ended_on_error
            await self._remember_exchange(
                stopped_on_close=self._closed_stop and stopped_here,
                stopped_by_you=stopped_here,
            )
            if not emitted_done:
                # A STOPPED turn returns with no terminal `done` frame — it
                # writes the CANCELLED usage row and returns. Without this the
                # panel would spin "working…" forever on the one press whose
                # entire purpose was to end that state.
                # Third plan (G20 follow-up): a reply that ended on its OWN
                # error frame was not stopped — the panel printed "Stopped."
                # under the error. Its `done` still goes (it ends the turn on
                # an older add-on and says a pending steer was not taken), but
                # never as stopped.
                await self.emit(
                    _ReplyRoute(conn), P.PANEL_EVENT_DONE,  # SB1: wherever the sidebar is now
                    {"reason": "failed" if ended_on_error else "stopped", "text": ""},
                )
            # Anything still queued was never read by anybody. The `done`
            # above is what the panel turns into "not taken".
            self._steers.clear()

    def _empty_answer(self, data: dict[str, Any]) -> str:
        """What to say when a turn ends having said nothing (F5).

        THE SILENT FAILURE: a sidebar that swallows a question. Two of the
        three ways a turn can end wordless are DECLARED EXITS the panel does
        not implement — ``escalate_to_agent`` and ``workflow_draft`` ride in
        the ``done`` payload rather than in tokens — and neither is offered to
        a panel turn any more, because the ceiling excludes both (module
        docstring). This method still handles them, for two reasons: the
        ``escalate`` flag is ALSO set by the chat lane's own tool-round
        exhaustion, which no ceiling can prevent; and a surface whose honesty
        depends on a bound in another module is one refactor from lying again.

        Every sentence names the Iron Jarvis window, because that is where the
        work the sidebar declined to do can actually be done.
        """
        if data.get("escalate"):
            reason = str(data.get("escalate_reason") or "").strip()
            tail = f" ({reason})" if reason else ""
            return (
                "This needs the full Iron Jarvis agent, which the sidebar does"
                f" not start{tail}. Ask again in the Iron Jarvis window."
            )
        if data.get("workflow_draft"):
            return (
                "Iron Jarvis drafted a reusable workflow for this. The sidebar"
                " cannot show it — open the Iron Jarvis window to review and"
                " save it."
            )
        return "Iron Jarvis finished this turn without an answer."

    async def _translate(self, conn: Any, event: str, data: dict[str, Any]) -> None:
        """One SSE frame -> the ``browser.panel_event`` frames it means (most
        often one; ``done`` may also settle the answer and add the route line).

        The mapping is deliberately narrow. ``round`` and ``approval_resolved``
        are the turn's own bookkeeping and have no panel vocabulary; forwarding
        them as something else would invent a frame the panel would have to
        guess the meaning of.

        ``reset`` DOES have one since v1.433.0: the lane sends it to take back
        words it streamed — a reply rewritten into the user's language, a tool
        call the model wrote as text after a cut-off. Ignored, the sidebar
        showed both versions, or the raw markup, and remembered them.

        Third review SB1: every frame here belongs to the running reply, so it
        goes to the connection(s) the sidebar is on when it is sent
        (:class:`_ReplyRoute`), not only the one the reply started on.
        """
        conn = _ReplyRoute(conn)
        if event == "reset":
            # The lane's `drop` (a retried round of a coding session takes back
            # only its own words, counted in code points) keeps the rest; with
            # no `drop` every word of the answer so far goes, as on the chat
            # page (`useChatStream`'s reset).
            drop = data.get("drop")
            kept_answer = kept_shown = ""
            if isinstance(drop, int) and not isinstance(drop, bool) and drop > 0:
                joined = "".join(self._answer)
                kept_answer = joined[: max(0, len(joined) - drop)]
                shown = "".join(self._segment)
                kept_shown = shown[: max(0, len(shown) - drop)]
            self._answer = [kept_answer] if kept_answer else []
            await self.emit(conn, P.PANEL_EVENT_RESET, {"reason": str(data.get("reason") or "")})
            if kept_shown:
                await self.emit(conn, P.PANEL_EVENT_DELTA, {"text": kept_shown})
            return
        if event == "token":
            piece = str(data.get("text") or "")
            if piece:
                self._delta_seen = True
                self._answer.append(piece)
            await self.emit(conn, P.PANEL_EVENT_DELTA, {"text": piece})
            return
        if event == "tool_call":
            name = str(data.get("name") or "a step")
            # v1.262.0: in WORDS — "Clicking 'Search'…", not "Running
            # browser_click…". The args here are the lane's REDACTED args.
            what = describe_browser_call(name, data.get("args"))
            finished = data.get("status") == "finished"
            if finished and data.get("ok"):
                text = f"{_cap(what)} — done."
            elif finished:
                # v1.270.1: WHY, in the step itself. The lane's finished frame
                # carries the tool's error as `output`; a step that says only
                # "Could not click" leaves the user to guess whether the page,
                # the risk door or a permission refused — the live report was
                # a permission refusal the user only met through the model's
                # paraphrase. Capped: a reason is a sentence, not a dump.
                # Third plan SB9: an action whose answer was lost has its own
                # sentence for the user (``_step_failed_line``).
                text = _step_failed_line(what, data.get("output"))
            else:
                text = f"{_cap(what)}…"
            # v1.270.0: `status`/`ok` ride along so the panel folds a step's
            # start and end into ONE line under its collapsed summary.
            await self.emit(
                conn,
                P.PANEL_EVENT_TOOL,
                {
                    "name": name,
                    "text": text,
                    "status": "finished" if finished else "started",
                    "ok": bool(data.get("ok")) if finished else None,
                },
            )
            return
        if event == "approval":
            tool = str(data.get("tool") or "a step")
            approval_id = str(data.get("id") or "")
            # THE ONE PLACE A CARD IS EMITTED, so the one place ownership is
            # recorded. `_decide` refuses anything absent from this set.
            if approval_id:
                self._offered.add(approval_id)
            what = describe_browser_call(tool, data.get("args"), card=True)
            # A card that lists steps (a form, v1.400.9) ends its list on its
            # own line; the question goes under it, not after the last step.
            ask = "\nAllow it?" if "\n" in what else ". Allow it?"
            card = {
                "id": approval_id,
                "tool": tool,
                "text": f"Iron Jarvis wants to {what}{ask}",
            }
            # Fourth review BS1: a card the lane filed with a kind (Local
            # only, …) is answered one action at a time: `floor`, which every
            # add-on since v1.522.0 draws as Allow once and Deny only, and
            # (C-BS-1) one line saying why Auto-allow does not cover it.
            kind = self._once_kind(approval_id) if approval_id else ""
            if kind:
                card["floor"] = True
                note = _card_note(kind)
                if note:
                    card["note"] = note
            if approval_id:
                # SB1: shown again to a panel that opens while it waits.
                self._cards[approval_id] = dict(card)
            await self.emit(conn, P.PANEL_EVENT_APPROVAL, card)
            return
        if event == "approval_resolved":
            self._cards.pop(str(data.get("id") or ""), None)
            # v1.266.0: an "Allow for this tab" answer changes what the header
            # says about this tab. The lane records the grant BEFORE it emits
            # this frame, so the state painted here is a fact.
            if str(data.get("decision") or "") == "tab":
                await self.emit(conn, P.PANEL_EVENT_STATE, self._state())
            return
        if event == "done":
            # THE REPLY (v1.433.0). The lane's `done` frame carries the turn's
            # answer as `reply` — the text the chat page saves. This branch read
            # a `text` key the frame has never had, so everything that rides
            # ONLY here never reached the sidebar: the sentence for a model
            # that wrote nothing after its tools, the refusal footnotes (worded
            # for the sidebar since v1.387.0, and never once shown in it), the
            # stopped / language / context notes, the honesty notes about
            # files, the cut-off end. The panel paints its words from `delta`
            # frames, so the reply is settled onto the answer bubble AS deltas
            # (and a `reset` when the bubble shows something the reply does not
            # extend) — and the conversation remembers the reply itself.
            route = data.get("route")
            reply = str(data.get("reply") or "")
            if isinstance(route, dict) and str(route.get("reason") or "") == "mock":
                reply = without_lane_demo_note(reply)
            shown = "".join(self._segment)
            # NEVER AN EMPTY ANSWER (F5). An `escalate` and a `workflow_draft`
            # end on an exit the sidebar cannot follow; when nothing of the
            # model's own is on screen, the lane's reply for that case describes
            # an empty answer, so the panel's own sentence says what happened.
            # A turn that ended with no words at all gets one too.
            exit_taken = bool(data.get("escalate") or data.get("workflow_draft"))
            if not shown.strip() and (exit_taken or not reply.strip()):
                final = self._empty_answer(data)
            elif reply.strip():
                final = reply
            else:
                # No reply, but words on screen: they stay as they are.
                final = ""
            if final:
                clear, add = settle_answer(shown, final)
                if clear:
                    await self.emit(conn, P.PANEL_EVENT_RESET, {"reason": "reply"})
                    add = final
                if add.strip():
                    await self.emit(conn, P.PANEL_EVENT_DELTA, {"text": add})
                self._answer = [final]
            # THE ROUTE, WHEN IT IS NOT WHAT WAS ASKED (v1.267.0). The lane's
            # route disclosure rides this frame; the dashboard renders it as the
            # receipt under every reply. The panel prints nothing for the
            # ordinary case — the select already says which model — and one
            # muted line for the two cases the receipt paints amber: a failover
            # (a different model answered) and the mock (no real model at all).
            # Silence there would be the v1.165.0 accountability hole, one
            # surface over. AFTER the answer (v1.433.0): a `tool` frame ends
            # the panel's answer bubble, so the reply is settled first.
            notice = self._route_notice(route, "".join(self._answer))
            if notice:
                await self.emit(conn, P.PANEL_EVENT_TOOL, {"name": "route", "text": notice})
            # v1.270.0: the exchange is written BEFORE `done` leaves, so an
            # `open` that arrives on that frame replays a complete conversation.
            await self._remember_exchange()
            await self.emit(conn, P.PANEL_EVENT_DONE, {"text": final})
            return
        if event == "error":
            # v1.433.0: the lane's error frame says what went wrong in plain
            # words — `title` and `fix` (`chat_turn.error_payload`, the chat's
            # own failure line) — beside the raw `detail`. The sidebar showed
            # only the raw text. The technical text rides along for the panel
            # to fold behind "Details"; a frame with no title (an older daemon,
            # a wording fault) reads exactly as before.
            sentence = failure_sentence(data.get("title"), data.get("fix"))
            payload: dict[str, Any] = {
                "text": sentence or str(data.get("detail") or "Iron Jarvis could not finish that.")
            }
            technical = str(data.get("technical") or "").strip()
            if sentence and technical and technical != sentence:
                payload["technical"] = technical[:TECHNICAL_CHARS]
            # What was read is remembered BEFORE the terminal frame leaves.
            await self._remember_exchange()
            # SB8: the reply's own failure is its end on the panel.
            await self.emit(conn, P.PANEL_EVENT_ERROR, payload, ends_turn=True)
            return


class _VoiceSession:
    """One dictation in progress (v1.269.0): where the audio goes, and what was heard so far."""

    def __init__(self, mode: str, backend: str, recognizer: Any = None) -> None:
        #: ``"stream"`` (offline Vosk, partials as words form) or ``"clip"`` (an HTTP
        #: backend fed one WAV at stop).
        self.mode = mode
        self.backend = backend
        self.rec = recognizer
        self.chunks: list[bytes] = []
        self.total = 0
        self.text = ""
        self.partial = ""


def _wav_bytes(pcm: bytes, *, rate: int = 16000) -> bytes:
    """Wrap raw PCM16 mono in a WAV container — what every transcription backend accepts."""
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def install(deps: Any) -> PanelTurns | None:
    """Give the add-on socket a panel handler, and return it.

    Wired HERE and not in the transport because ``ExtensionBackend`` holds no
    policy and knows nothing about chat — the same arrangement
    ``access_reader`` and ``snapshot_invalidator`` already use. ``None`` when
    there is no browser backend to install onto (a test app built without
    one), never an exception: a missing capability must not break boot.
    """
    platform = getattr(deps, "platform", None)
    backend = getattr(getattr(platform, "browser", None), "backend", None)
    if backend is None:
        return None
    turns = PanelTurns(platform, getattr(deps, "_PERSONAS", {}) or {}, deps=deps)
    try:
        backend.panel_handler = turns.handle
    except Exception:  # noqa: BLE001 — a stand-in backend that refuses the attribute
        logger.debug("browser backend takes no panel_handler", exc_info=True)
        return None
    # v1.276.0: the risk door's card reaches the sidebar. Only where nothing
    # else resolves (a test runtime may bring its own).
    runtime = getattr(platform, "browser", None)
    if runtime is not None and getattr(runtime, "approval_resolver", None) is None:
        try:
            runtime.approval_resolver = turns.resolve_risk_ask
        except Exception:  # noqa: BLE001
            logger.debug("browser runtime takes no approval_resolver", exc_info=True)
    return turns
