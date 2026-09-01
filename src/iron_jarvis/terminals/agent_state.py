"""What the agent in this pane is DOING (v1.217.0).

Build can already start a coding CLI in a pane — the Launch catalog spawns
Claude Code, Codex or Pi — and from that moment the app goes blind. The
session gives us ``alive``, ``exit_code`` and bytes; nothing says whether the
agent is thinking, waiting on YOU, or finished ten minutes ago. So the pane
where real work happens is the pane the app can say least about, and the user
has to open each one to find the stuck one.

The idea (and the vocabulary) is adapted from herdr, a terminal multiplexer
built for coding agents, whose framing is "never hunt for the stuck one" and
whose agent skill defines the states we borrow. Two of its rules are worth
copying verbatim because they are honesty rules, not features:

    "``blocked`` means Herdr recognized an approval or question UI."
    "``unknown`` means an agent is present but Herdr cannot classify it
     confidently; **it does not prove completion**."

That second one is already this codebase's own law in another module — the
roster's liveness note says a missing signal "is NOT 'free' — it is 'no
claim'". A pane we cannot read must never render as finished.

WHY A PURE FUNCTION OVER THE TAIL. `TerminalSession.output_tail()` already
returns ANSI-stripped recent output (herdr calls the equivalent read source
`detection`). Classification therefore needs no new plumbing, no polling loop
and no process introspection: it is a fold over text the session already
keeps, which makes it cheap to call and trivial to test against real captures.

WHAT THIS DELIBERATELY DOES NOT DO. It does not guess. Every pattern here was
written against actual output from these CLIs, and anything unrecognised
returns `UNKNOWN` rather than the state that would be convenient. A wrong
`IDLE` tells the user a pane is free when an agent is mid-edit; a wrong `DONE`
tells them work finished that did not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from .ai_clis import AI_CLIS

__all__ = [
    "AgentState",
    "PaneActivity",
    "classify",
    "known_clis",
]


class AgentState(str, Enum):
    """The lifecycle of an agent occupying a pane.

    Ordered loosest → most settled for display purposes only; nothing depends
    on the ordering.
    """

    #: An agent is present and producing output / running a tool.
    WORKING = "working"
    #: An approval prompt or a question is on screen. The agent is waiting on
    #: the USER, not on the machine. This is the state the whole feature
    #: exists for.
    BLOCKED = "blocked"
    #: At an interactive prompt, ready for input, and the user has seen it.
    IDLE = "idle"
    #: The same underlying ready state as IDLE, reached while the user was
    #: looking somewhere else. Rendered differently because "finished while
    #: you were away" is a different thing to be told than "ready".
    DONE = "done"
    #: Something is running that we cannot classify — including a plain shell.
    #: NEVER means finished.
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PaneActivity:
    """A classification plus the evidence behind it.

    `line` is the output line the decision was made from, so a surface can show
    WHY it says what it says instead of asking the user to trust a badge — the
    same reason the peek strip (v1.213.0) shows the line rather than a dot.
    """

    state: AgentState
    #: The CLI we believe occupies the pane ("claude" / "codex" / "pi"), or
    #: None when the pane is an ordinary shell.
    cli: str | None = None
    #: The evidence line, ANSI-free and clipped. Empty when there is none.
    line: str = ""

    @property
    def busy(self) -> bool:
        """True only when the machine is working. `unknown` is not a claim."""
        return self.state is AgentState.WORKING


#: Everything after this many characters of a tail line is noise for our
#: purposes; the evidence line is also what a badge tooltip shows.
_LINE_CAP = 160

#: How much of the tail to look at. An agent's current state is always in the
#: last few rendered lines; scanning further finds a stale approval prompt the
#: user already answered and reports it as live.
_SCAN_LINES = 24

#: How far back an approval UI may sit and still be the live one. A CLI
#: draws the question, then its options, then sometimes a hint line under
#: them; reaching much further than that starts finding answered prompts.
_BLOCK_LINES = 8


def known_clis() -> tuple[str, ...]:
    """Every CLI the Launch catalog can start, and whose hint we therefore
    trust.

    NOT the same set as `_CLI_MARKS`. Sniffing a CLI out of scrollback needs a
    pattern written against that CLI's real output, and there are three of
    those; the CATALOG, by contrast, knows exactly what it typed into the pane.
    The first cut conflated the two and gated the hint on the sniffable three,
    so launching Grok or Gemini from the catalog — reported by the browser the
    moment it launches — was thrown away, and the pane fell back to "no agent
    here". The user launched a CLI from a menu this app owns and the app said
    it saw a shell.

    Read the catalog rather than restating it: a CLI added to `AI_CLIS` becomes
    launchable and classifiable in one edit, which is the only way the two
    lists cannot drift.
    """
    return tuple(str(cli["id"]) for cli in AI_CLIS)


# --------------------------------------------------------------------------
# Patterns
#
# Every one of these was written against real terminal output from the CLI it
# names. They are deliberately narrow: a broad pattern that catches an extra
# case is not a win here, because the cost of a wrong answer is telling the
# user a pane is idle when an agent is running, or finished when it is stuck.
# --------------------------------------------------------------------------

#: An approval / question UI is on screen. These are the phrasings the three
#: CLIs use when they stop and wait for a human decision.
_BLOCKED = re.compile(
    r"(?:"
    r"\bdo you want to proceed\b"
    r"|\bdo you want to (?:make|allow|continue)\b"
    r"|\ballow this (?:command|tool|edit)\b"
    r"|\bapprove (?:this|the) (?:command|edit|change|tool)\b"
    r"|\bwaiting for (?:your )?approval\b"
    r"|\b(?:y/n|yes/no)\s*[?:]"
    r"|\[y/n\]"
    r"|\bpress (?:enter|y) to (?:continue|approve|confirm)\b"
    r"|\bcontinue\?\s*$"
    r"|❯\s*\d\.\s"          # a numbered choice list with a selection caret
    r")",
    re.I,
)

#: One row of a numbered choice list ("  1. Yes"), caret or not.
_NUMBERED_CHOICE = re.compile(r"^\s*(?:[❯>›*]\s*)?\d+[.)]\s+\S")

#: The agent is mid-turn. Spinner words and tool banners the CLIs print while
#: they work.
_WORKING = re.compile(
    r"(?:"
    # A progress verb STARTING the line, anything after it, ending in an
    # ellipsis: "Analyzing the diff…", "Reading src/app.py...". The first cut
    # anchored the verb to the END of the line, so it caught a bare
    # "Thinking…" and missed every line that named what it was working on —
    # which is most of them.
    r"^\s*[·•*\-]?\s*(?:thinking|working|running|executing|generating|"
    r"analy[sz]ing|searching|reading|writing|editing|planning|compacting)\b"
    r".*(?:\.{3}|…)\s*$"
    r"|\b(?:thinking|working|running|executing|generating|analy[sz]ing|"
    r"searching|reading|writing|editing|planning|compacting)\b\s*[.…]*\s*$"
    r"|\besc to interrupt\b"
    r"|\bctrl\+c to (?:stop|cancel|interrupt)\b"
    r"|^\s*[⠁-⣿]\s"          # a braille spinner frame
    r"|\btokens?\b.*\besc\b"
    r")",
    re.I,
)

#: An interactive prompt is showing and nothing is running. Matching the
#: PROMPT is what distinguishes "ready" from "we cannot tell".
_PROMPT = re.compile(
    r"(?:"
    r"^\s*(?:>|❯|›)\s*$"                       # a bare agent prompt caret
    r"|^\s*(?:>|❯|›)\s+\S.*$"                  # …or one with a draft in it
    r"|\btry \"(?:edit|fix|explain|what)\b"    # Claude Code's idle hint line
    r"|\bctrl\+c to exit\b"
    r"|\b\? for shortcuts\b"
    r")",
    re.I,
)

#: SNIFFING — how we recognise a CLI nobody told us about, because the user
#: typed its name themselves instead of using Launch. A small set on purpose:
#: each pattern is written against that CLI's real output. `known_clis()` is
#: the much longer list of what the catalog can START, and a hint from there
#: beats anything here. Used only to decide `cli`, never the state.
_CLI_MARKS: dict[str, re.Pattern[str]] = {
    "claude": re.compile(r"\b(?:claude code|anthropic|/help for help)\b", re.I),
    "codex": re.compile(r"\bcodex\b", re.I),
    "pi": re.compile(r"\bpi\b(?:\s+v?\d|\s+cli|\s+coding)", re.I),
}


def _lines(tail: str) -> list[str]:
    """The last few non-empty rendered lines, newest last, clipped."""
    out: list[str] = []
    for raw in tail.splitlines():
        line = raw.rstrip()
        if line.strip():
            out.append(line[:_LINE_CAP])
    return out[-_SCAN_LINES:]


def _detect_cli(tail: str, hint: str | None) -> str | None:
    """Which CLI occupies the pane.

    The HINT wins — the Launch catalog knows what it started, and reading it
    back out of the scrollback is guesswork by comparison. Sniffing is the
    fallback for a pane the user started by hand.
    """
    if hint and hint in known_clis():
        return hint
    for name, pat in _CLI_MARKS.items():
        if pat.search(tail):
            return name
    return None


def classify(
    tail: str,
    *,
    cli: str | None = None,
    seen: bool = True,
    alive: bool = True,
) -> PaneActivity:
    """Classify what the pane is doing from its recent output.

    Parameters
    ----------
    tail:
        ANSI-stripped recent output — `TerminalSession.output_tail()`.
    cli:
        What the Launch catalog started here, when it knows. Beats sniffing.
    seen:
        Whether the user has looked at this pane since its last output. This
        is the ONLY input that separates `done` from `idle`; it is a fact
        about the UI, so the caller owns it (herdr's rule: focusing marks a
        pane seen, a programmatic read does not).
    alive:
        A dead pane is never `working`.

    Returns
    -------
    PaneActivity
        With `state` UNKNOWN whenever the output does not clearly say
        otherwise. Absence of evidence is not evidence of readiness.
    """
    which = _detect_cli(tail, cli)
    if not alive:
        return PaneActivity(AgentState.UNKNOWN, which, "")
    lines = _lines(tail)
    if not lines:
        # A pane that has printed nothing at all. Not idle — we have no
        # evidence either way, and a fresh shell looks identical to an agent
        # that has not started drawing yet.
        return PaneActivity(AgentState.UNKNOWN, which, "")

    # BLOCKED WINS, and it is looked for over the last few lines rather than
    # only the final one: the CLIs draw a question and then a selection list
    # under it, so the prompt itself is no longer the last line by the time we
    # look. Two shapes count as an approval UI --
    #
    #   (a) a PHRASE these CLIs use ("do you want to proceed?", "[y/N]"), and
    #   (b) a QUESTION with numbered choices under it:
    #
    #           Edit src/app.py?
    #             1. Yes
    #             2. No
    #
    # (b) exists because driving a real PTY caught (a) missing exactly that,
    # and the single shape rule inside `_BLOCKED` wanted the selection caret --
    # absent on the unselected rows, and sometimes lost with the ANSI. Both
    # halves of (b) are required: an agent's prose ends in a question mark
    # constantly, and a plain numbered list appears without one, so arming on
    # either alone would put a false "needs you" on a chatty pane.
    blocked_at: int | None = None
    window = lines[-_BLOCK_LINES:]
    for i, line in enumerate(window):
        if _BLOCKED.search(line):
            blocked_at = i
            continue
        if line.rstrip().endswith("?") and (
            sum(1 for n in window[i + 1 : i + 5] if _NUMBERED_CHOICE.match(n)) >= 2
        ):
            blocked_at = i

    # ...BUT ONLY WHILE IT IS STILL THE LAST THING THAT HAPPENED. An approval
    # the user already answered stays in the scrollback, and the first cut let
    # it pin the pane to "needs you" for as long as it sat in the window --
    # caught by the same PTY drive, where a resolved prompt outranked a live
    # spinner two commands later. Progress printed BELOW a question means the
    # agent moved on; progress printed ABOVE it is the turn that led up to the
    # question, which is why this reads direction rather than just presence.
    if blocked_at is not None:
        resumed = any(_WORKING.search(n) for n in window[blocked_at + 1 :])
        if not resumed:
            return PaneActivity(AgentState.BLOCKED, which, window[blocked_at])

    # WORKING and IDLE are read from the tail END, because both describe what
    # is on screen NOW. A "thinking…" ten lines up is history.
    for line in reversed(lines[-3:]):
        if _WORKING.search(line):
            return PaneActivity(AgentState.WORKING, which, line)

    # A prompt only means "ready" when we know an agent is there. A bare `>`
    # in an ordinary shell is not an idle agent, it is a shell.
    if which is not None:
        for line in reversed(lines[-3:]):
            if _PROMPT.search(line):
                state = AgentState.IDLE if seen else AgentState.DONE
                return PaneActivity(state, which, line)

    return PaneActivity(AgentState.UNKNOWN, which, lines[-1])
