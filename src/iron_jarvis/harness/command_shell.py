"""Shell lines from a PROJECT's or a PLUGIN's Claude Code command always ask
first (v1.381.0, IJ harness).

THE USER (2026-10-23, final): "Shell lines from a project's or plugin's
command files should always ask before running, even when the session
approves things for me. My own command files can follow the session's
setting."

A Claude Code command file (``harness/claude_commands``) may hold shell lines
(``!`…``` and ```` ```! ```` blocks). Iron Jarvis never runs them itself — the
expansion asks the MODEL to run them with its own shell tool — so under
"Approve for me" or "Don't ask" (yolo) a repository's command file could have
the model run whatever it says with no card. A personal command
(``~/.claude/commands``, honouring ``CLAUDE_CONFIG_DIR``) is the user's own
words and keeps the session's posture; a project's (whatever repository is
open) or a plugin's is not.

THE RULE, armed per turn by ``routes/build.start_build_turn`` as a
``ContextVar`` (:data:`TURN_RULE`, reset in reverse with the account pin, the
model hold and the turn's branch — the turn's driver task copies it, and so
does every helper the turn starts; :mod:`.helpers` also carries it explicitly
into the helper's task, like the pin):

* THE COMMAND'S OWN TURN: when a project's or a plugin's command WITH shell
  lines starts the turn, EVERY command-running call of that turn — ``shell``
  (the registered ``sandbox/shell_tool.SandboxedShellTool``),
  ``process_start``, ``process_write`` — asks first: the tool's
  ``spend_check`` answers :class:`~iron_jarvis.tools.base.SpendCheck` with
  ``ask=True, once_only=True, reason="command"`` (the git guard's route,
  v1.375.0), so the stream lane cards it ALONE in every posture
  (always_ask, approve_for_me, yolo) past every grant (conversation, tab,
  standing, a user "allow" in the permission table), "Allow once" and Deny
  only — a wider answer is read as once; a helper's run pauses the same way;
  the POST lane, which can show no card, refuses it in words
  (``chat_turn.spend_not_asked_invoke_kwargs``). Every command, not only the
  lines: the model is free to reword a line (``cd x && …``, a script it
  writes), and the command's text is what asked for the shell.
* A CONTINUATION of that turn (the daemon's "carry on" after an account
  limit — "Resume when ready", "Continue on another account") is the same
  work, so the same turn-wide rule.
* A LATER turn of the session (the expansion stays in the history the model
  reads, ``commandText``): a command-running call whose text CONTAINS one of
  a recorded command's shell lines asks the same way. The lines are recorded
  on the session (``setup.command_shell``, :func:`clean_record` — written
  only by the /build routes, never taken from a client) and matched after
  :func:`normalize` (case, quotes, backslashes before quotes and runs of
  white space do not count), on word boundaries. Other commands of a later
  turn follow the session's setting: the user typed that turn, not the
  command file.

A command WITHOUT shell lines, and every personal command, change nothing.
Low trust (the shell is kept away and refused) is unchanged; under Local only
the card is still its "Allow once" card, and (review) it now says THIS reason
(``once_reason`` "command"), so the screen treats it as a safety card.
``run_code`` / ``repl`` / the terminal tools are not reachable from a harness
turn (``harness/mode.CODING_TOOLS``), nor from its helpers
(``helpers.helper_definition`` bounds them by the turn's tools); a Build turn
arms no app (MCP) pack tool (its body names no connector).

v1.381.0 REVIEW (each pinned in ``tests/test_build_command_shell_review_
v1381.py``): a carry-on continues the newest question the USER sent — a steer
note is skipped (:func:`continued_turn`; it carries the turn it steered, and
in a carry-on that is the carry-on's id); a command whose record cannot be
written does not start (``routes/build.COMMAND_RECORD_SENTENCE``) — its
carry-on reads only the record; a command whose shell lines are all empty
keeps a record with no lines (for its carry-on; it matches nothing later);
the same command again replaces its older copy, so 16 runs of one command
never push another's lines out; a folder's plugin check whose command would
ask is not run on its own (``chat_turn.harness_check_runner`` — the model is
told to run it, and its own call asks); a line continued onto the next one
reads as one line; a later turn's card names the line that matched.

THE SCREEN: Enter never allows this card (``dashboard/lib/dockAsk.
enterAllows`` — "Click Allow once to run it · Esc to decline"); an Enter
typed twice for the composer allowed one in the live check.

HONEST LIMITS: matching a LATER turn reads the command's text — a line the
model rewrites (other words, PowerShell for bash, a script file, a line
split over several writes to a program) is not caught there; the command's
own turn and its continuations card every command. Only shell LINES count: a
project's command that asks in prose ("run npm test") is not one, and a
folder's or a plugin's SKILL is not a command file. A guardrail for a
careless or a misled model, not a sandbox.
"""

from __future__ import annotations

import re
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Iterable

#: Where a session keeps its recorded commands (``setup``).
SETUP_KEY = "command_shell"
#: ``SpendCheck.reason`` / the approval frame's ``once_reason`` — the card's
#: own line names why it asks every time.
REASON = "command"
#: The ledger's / tool output's short form.
COST_NOTE = "asks every time"
#: At most this many commands are kept on a session (the newest).
MAX_RECORDED = 16
#: A recorded line, and how many per command (``claude_commands`` caps them
#: too: these bound what a stored record may hold).
MAX_LINES = 20
MAX_LINE_CHARS = 400

SOURCE_PROJECT = "project"
SOURCE_PLUGIN = "plugin"
_SOURCES = (SOURCE_PROJECT, SOURCE_PLUGIN)


@dataclass(frozen=True)
class CommandLines:
    """One project's or plugin's command that asked for shell lines."""

    name: str
    source: str
    #: The plugin's name as Claude Code shows it (source "plugin"), else "".
    plugin: str
    #: The turn the command started (its question line's ``turnId``).
    turn_id: str
    lines: tuple[str, ...]

    def record(self) -> dict[str, Any]:
        return {
            "name": self.name, "source": self.source, "plugin": self.plugin,
            "turn_id": self.turn_id, "lines": list(self.lines),
        }


@dataclass(frozen=True)
class TurnRule:
    """What THIS turn asks about. ``started_by``: the command whose turn this
    is (or whose cut-off turn this continues) — every command-running call
    asks. ``earlier``: the session's recorded commands — a call running one
    of their lines asks."""

    started_by: "CommandLines | None" = None
    earlier: tuple[CommandLines, ...] = ()


TURN_RULE: "ContextVar[TurnRule | None]" = ContextVar("ij_command_shell", default=None)


def set_turn_rule(rule: "TurnRule | None") -> "Token[TurnRule | None]":
    return TURN_RULE.set(rule)


def reset_turn_rule(token: "Token[TurnRule | None]") -> None:
    TURN_RULE.reset(token)


def current() -> "TurnRule | None":
    return TURN_RULE.get()


# --------------------------------------------------------------------------- #
# the record a session keeps
# --------------------------------------------------------------------------- #


def _one(raw: Any) -> "CommandLines | None":
    if not isinstance(raw, dict):
        return None
    source = raw.get("source")
    if source not in _SOURCES:
        return None
    name = " ".join(str(raw.get("name") or "").split())[:200]
    if not name:
        return None
    plugin = " ".join(str(raw.get("plugin") or "").split())[:80] if source == SOURCE_PLUGIN else ""
    turn_id = str(raw.get("turn_id") or "").strip()[:80]
    lines_raw = raw.get("lines")
    lines: list[str] = []
    for line in lines_raw if isinstance(lines_raw, list) else []:
        if isinstance(line, str) and line.strip():
            text = line.strip()[:MAX_LINE_CHARS]
            if text not in lines:
                lines.append(text)
        if len(lines) >= MAX_LINES:
            break
    if not lines and not turn_id:
        # v1.381.0 review: a command whose shell lines are all empty (an
        # empty ```! block) still asked in its turn — and its record has to
        # say so, or that turn's CONTINUATION runs with no card. With no
        # lines it matches nothing later; with no turn either it is nothing.
        return None
    return CommandLines(name=name, source=source, plugin=plugin, turn_id=turn_id, lines=tuple(lines))


def entries(raw: Any) -> list[CommandLines]:
    """The commands a stored ``setup.command_shell`` holds (bounded, cleaned;
    [] for anything else). Oldest first."""
    if not isinstance(raw, list):
        return []
    out = [e for e in (_one(x) for x in raw) if e is not None]
    return out[-MAX_RECORDED:]


def clean_record(raw: Any) -> list[dict[str, Any]]:
    """``setup.command_shell`` as stored (``routes/chat._clean_setup``'s
    whitelist): only a list of well-formed records survives. Only the /build
    routes write it — a Build session cannot be PUT and its PATCH has no field
    for it."""
    return [e.record() for e in entries(raw)]


def recorded_with(setup: "dict[str, Any] | None", entry: CommandLines) -> list[dict[str, Any]]:
    """The session's record with ``entry`` added (newest last, at most
    :data:`MAX_RECORDED`)."""
    have = entries((setup or {}).get(SETUP_KEY))
    # v1.381.0 review: the SAME command again (same source, name and lines)
    # replaces its older copy — run 16 times, it used to fill every place and
    # push another command's lines out of the record, and a later turn
    # running those lines no longer asked. The newest copy keeps its turn: a
    # carry-on only ever continues the newest question.
    same = _identity(entry)
    have = [e for e in have
            if not (e.turn_id and e.turn_id == entry.turn_id) and _identity(e) != same]
    return [e.record() for e in [*have, entry][-MAX_RECORDED:]]


def _identity(entry: CommandLines) -> tuple[str, str, str, tuple[str, ...]]:
    return (entry.source, entry.plugin.lower(), entry.name.lower(), entry.lines)


def from_expansion(expanded: Any, turn_id: str) -> "CommandLines | None":
    """The record of an expanded command (``claude_commands.Expanded``) that
    asks first — a project's or a plugin's command with shell lines — else
    None (a personal command, or one with no shell lines: nothing changes)."""
    if not getattr(expanded, "asks_first", False):
        return None
    return _one({
        "name": getattr(expanded, "name", ""), "source": getattr(expanded, "source", ""),
        "plugin": getattr(expanded, "plugin", ""), "turn_id": turn_id,
        "lines": list(getattr(expanded, "lines", []) or []),
    }) or CommandLines(
        # A command whose lines all fell to the caps still asks for its turn.
        name=str(getattr(expanded, "name", "") or "command")[:200],
        source=str(getattr(expanded, "source", "")), plugin=str(getattr(expanded, "plugin", "") or "")[:80],
        turn_id=turn_id, lines=(),
    )


def continued_turn(messages: Iterable[Any]) -> str:
    """The turn a daemon's carry-on continues: the newest question the USER
    sent — neither a continuation (the daemon's own "carry on") nor a steer
    note. v1.381.0 review: a steer note typed into a carry-on is saved as a
    user line carrying THAT carry-on's turn id, so the next carry-on found
    it, matched no command and ran every command with no card. A steer note
    always belongs to the turn it steered, whose own question comes first.
    "" when there is none."""
    for line in reversed(list(messages or [])):
        if not isinstance(line, dict) or line.get("role") != "user":
            continue
        if line.get("continuation") or line.get("steer"):
            continue
        return str(line.get("turnId") or "")
    return ""


def rule_for_turn(setup: "dict[str, Any] | None", messages: Iterable[Any], *,
                  started: "CommandLines | None" = None, continuation: bool = False) -> "TurnRule | None":
    """This turn's rule (None = nothing to ask about: the turn is exactly as
    before). ``started``: the command this turn runs. ``continuation``: the
    daemon's own "carry on" — when the turn it continues (the newest question
    that is not a continuation) was a recorded command's, that command's
    rule covers this turn too."""
    earlier = tuple(entries((setup or {}).get(SETUP_KEY)))
    started_by = started
    if started_by is None and continuation:
        turn = continued_turn(messages)
        if turn:
            started_by = next((e for e in reversed(earlier) if e.turn_id == turn), None)
    if started_by is None and not earlier:
        return None
    return TurnRule(started_by=started_by, earlier=earlier)


# --------------------------------------------------------------------------- #
# matching a later turn's command
# --------------------------------------------------------------------------- #

#: Lines that are only shell syntax match nothing (``fi`` would card every
#: ``if`` a later turn runs).
_SYNTAX_ONLY = frozenset({
    "fi", "done", "then", "else", "elif", "do", "esac", "in", "end", "endif", "{", "}", "(", ")",
    ";;", "&&", "||", "|", ";", "&", "\\",
})


#: A line continued onto the next (bash ``\``, cmd ``^``, PowerShell `````).
_CONTINUED = re.compile(r"[\\^`][ \t]*\r?\n")


def normalize(text: Any) -> str:
    """A command's text as matching reads it: lower case, a backslash before
    a quote dropped, every quote (``"``, ``'``, `````) dropped, runs of white
    space one space."""
    t = str(text or "")
    # v1.381.0 review: a line continued onto the next one — bash's ``\``,
    # cmd's ``^``, PowerShell's backtick at a line's end — is one line.
    t = _CONTINUED.sub(" ", t)
    t = re.sub(r"\\(?=[\"'`])", "", t)
    t = re.sub(r"[\"'`]", "", t)
    return " ".join(t.split()).lower()


def _matchable(line: str) -> str:
    n = normalize(line)
    if not n or n.startswith("#") or n in _SYNTAX_ONLY or len(n) < 2:
        return ""
    return n


def matched_line(command: Any, entry: CommandLines) -> str:
    """The first of ``entry``'s shell lines that ``command`` (a shell line, a
    program's command, text typed into one) contains — on word boundaries,
    after :func:`normalize` — or ""."""
    text = normalize(command)
    if not text:
        return ""
    for line in entry.lines:
        n = _matchable(line)
        if n and re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", text):
            return line
    return ""


def runs_a_line(command: Any, entry: CommandLines) -> bool:
    """Whether ``command`` contains one of ``entry``'s shell lines
    (:func:`matched_line`)."""
    return bool(matched_line(command, entry))


# --------------------------------------------------------------------------- #
# the words and the check
# --------------------------------------------------------------------------- #


def whose(entry: CommandLines) -> str:
    """"the project's /deploy command" / "the tools plugin's /tools:lint
    command"."""
    if entry.source == SOURCE_PLUGIN:
        owner = f"the {entry.plugin} plugin's" if entry.plugin else "a plugin's"
    else:
        owner = "the project's"
    return f"{owner} /{entry.name} command"


#: How much of a matched line the card quotes.
QUOTE_CHARS = 80


def turn_words(entry: CommandLines) -> str:
    """The card's line for a command-running call of the command's own turn
    (and of its carry-on). The card's own rule line says the rule; this one
    says why THIS call asks — every command of the turn, not only the
    command's lines (the model may reword a line)."""
    return (f"This turn runs {whose(entry)}, which has shell lines, so every command in it "
            "asks you first.")


def line_words(entry: CommandLines, line: str = "") -> str:
    """The card's line for a later turn's call that runs one of the
    command's lines: WHICH line matched (masked, cut to
    :data:`QUOTE_CHARS`), so the user sees why a command they did not
    expect to be asked about is."""
    shown = " ".join(str(line or "").split())
    try:
        from ..detections.redact import mask

        shown = mask(shown)
    except Exception:  # noqa: BLE001 — wording only
        pass
    if len(shown) > QUOTE_CHARS:
        shown = shown[: QUOTE_CHARS - 1].rstrip() + "\u2026"
    if not shown:
        return f"This runs a shell line from {whose(entry)}."
    return f"This runs \u201c{shown}\u201d, a shell line from {whose(entry)}."


def command_check(command: Any) -> Any:
    """``SpendCheck`` for a command tool's call (``shell``, ``process_start``,
    ``process_write``) under this turn's rule: ask once, every time, for any
    command of the command's own turn and for a later turn's command that runs
    one of a recorded command's lines; None otherwise — and always None when
    the turn has no rule (every session that never ran such a command, every
    chat turn) or the call carries no command."""
    rule = current()
    if rule is None:
        return None
    if not str(command or "").strip():
        return None
    from ..tools.base import SpendCheck

    if rule.started_by is not None:
        return SpendCheck(ask=True, words=turn_words(rule.started_by), cost_note=COST_NOTE,
                          once_only=True, reason=REASON)
    for entry in reversed(rule.earlier):
        line = matched_line(command, entry)
        if line:
            return SpendCheck(ask=True, words=line_words(entry, line), cost_note=COST_NOTE,
                              once_only=True, reason=REASON)
    return None


def tool_check(command: Any) -> Any:
    """The ONE spend check every command tool answers: the git guard's
    verdict first (a refusal it makes must stay a refusal, and its words say
    what the git command does), else :func:`command_check`."""
    from .git_guard import guard_check

    git = guard_check(command)
    return git if git is not None else command_check(command)


__all__ = [
    "COST_NOTE", "CommandLines", "MAX_RECORDED", "REASON", "SETUP_KEY", "TURN_RULE", "TurnRule",
    "clean_record", "command_check", "continued_turn", "current", "entries", "from_expansion",
    "line_words", "matched_line", "normalize", "recorded_with", "reset_turn_rule", "rule_for_turn",
    "runs_a_line",
    "set_turn_rule", "tool_check", "turn_words", "whose",
]
