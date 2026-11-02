"""COPY AS A FIX REQUEST (v1.423.0, Wave 3 lane B, the owner's decision of
2026-11-01): a report of the problems Iron Jarvis's OWN machinery ran into in
the coding sessions and their model team, shaped so the owner can paste it to
the coding agent that works on THIS repository.

The owner: "This will be the specifics I can feed back to the agent working on
the app … structured so that sending it to a coding agent for a fix is a
simple copy and paste of the issues experienced." The reader of the report is
a coding agent working on Iron Jarvis itself — never on the user's project —
so every problem here is a defect (or a likely defect) in the harness: a
command shape the shell could not run, a tool a worker kept calling wrong, a
helper that could not start or failed, a router that found nobody, a reply cut
off at its output limit, a team review that never ran. Bugs in the user's own
code are not Iron Jarvis's to fix and are left out.

TWO HALVES:

* :func:`gather` — BLOCKING (SQLite reads only, bounded), never raises: what
  the app's own records say for a window (default :data:`DEFAULT_DAYS`, any
  of :data:`MIN_DAYS`–:data:`MAX_DAYS`) and, optionally, ONE coding session
  (its ``build:<id>`` calls, its attempts' ``build:<id>~try<n>`` calls and the
  runs of its helpers). A source that cannot be read is NAMED in the report
  (``Could not read: …``), never a 500 and never a silent "nothing found".
* :func:`render` — PURE, deterministic: the Markdown (pinned by a fixture).

WHERE EACH PROBLEM IS READ FROM (one source per incident, so one failure is
never counted three times):

* ``ToolInvocation`` rows with ``ok = 0`` whose session is a coding session
  (``build:<id>``, ``build:<id>~try<n>``), a coding session's HELPER run
  (``harness/helpers.HELPER_ORIGIN`` — its ``options.build_session`` names
  the session) or an objective's TEAMMATE (``agents/team.
  MISSION_MEMBER_ORIGIN``). GROUPED by (who — the lead, or the member's name —
  + its provider and model, tool, the error's SHAPE: the error with quoted
  text, paths, ids and numbers normalised), with "N of M calls of this tool
  failed" (M = every call of that tool by that who in the window).
  A ``delegate`` / ``spawn_agent`` row is read only when NO run started (the
  team router found no one → ``router_refused``; any other refusal →
  ``helper_not_started``): a helper that started and then failed is read off
  its own Session row instead.
* Helper / teammate ``Session`` rows that ended FAILED (``summary`` and
  ``failure_detail``): ran out of steps, a context window too small for the
  piece (``runtime.context_too_small_result``), or any other failure (a
  provider that dropped mid-answer is one: the helper's row holds its words,
  so ``provider.failed`` events are NOT read — they would count it again).
* ``autonomy.nudged`` events of kind ``text_tool_call`` (``core/nudges``): a
  model wrote a tool call as text and had to be told to call it for real.
* A coding session's REPLY LINES (``ChatThreadRecord`` with surface "build",
  ``harness/sessions.LINE_KEYS``): a reply that ended on an error (a
  provider failure in team work; one that kept retrying and gave up says
  "Iron Jarvis tried N times" — ``harness/patience.tried_words``), a reply
  cut off at its output limit (``route.cut_off``, ``truncated``), and the
  finish check's team review that failed, did not run, or ran past its time
  limit (``finish.review``).

THE RULE FOR WHAT IS LEFT OUT (the user's choices and ordinary work are not
defects):

* THE USER'S CHOICE: a card the user declined or left unanswered, a tool the
  user's rules deny, a low-trust refusal (``tool.denied`` kinds "permission
  denied", "paused", "low trust", "needs your OK", the POST lane's "cannot
  ask"), a Stop the user pressed (the registry's cancel / stop notes, a
  helper stopped with its reply, a CANCELLED helper run), Emergency stop, a
  reply the user stopped, a provider's safety refusal, a sign-in or key the
  user has to fix, an account's limit, and a restart that cut a reply off.
* ORDINARY WORK: a command that RAN and exited non-zero — a test command
  that failed because the user's tests fail is the user's code, not Iron
  Jarvis. Only a command that could NOT RUN AT ALL counts: the shell did not
  know the program ("is not recognized", "command not found"), it was
  written for another shell ("was unexpected at this time", the ``;``
  guard), a guard refused it (the git guard, a ``cwd`` outside the folder),
  or it ran until its time limit.
* LOW TRUST: nothing is read from a run that ended at LOW trust (a helper
  whose row says ``trust = low``, or a lead's reply whose line says so — the
  lead's calls inside that reply's time window are skipped too, and a
  low-trust reply with no time window skips that session's lead calls): its
  text may be an injected instruction, and this report is pasted into
  another agent's prompt. The count left out is said.

EVERY value that came from a model, a tool, a file or the user (an error, an
argument, a path, a title, a member's name) is masked
(``providers/failure_words.mask_failure_text`` = ``detections.redact.mask`` +
the shapes it does not know) and then promptguard-scanned
(``core/promptguard.scan_context`` — the report is pasted into a prompt)
BEFORE any cut. The whole report is at most :data:`REPORT_CHARS` characters
and lists at most :data:`MAX_PROBLEMS` problems, most frequent first; what
did not fit is said.

NOT RECORDED ANYWHERE (so not in the report): a helper's own reply cut off at
its output limit (the agent runtime notes it inside the run, nothing is
stored), how long a patience wait lasted when the reply then SUCCEEDED, and
Build › Code's real CLIs (their tools run inside Claude Code / Codex, not
through Iron Jarvis's ledger).
"""

from __future__ import annotations

import json
import logging
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

log = logging.getLogger(__name__)

#: The window, in days.
DEFAULT_DAYS = 7
MIN_DAYS = 1
MAX_DAYS = 30
#: At most this many problems are listed (most frequent first).
MAX_PROBLEMS = 20
#: Evidence rows per problem.
MAX_EVIDENCE = 3
#: Caps (after masking and scanning, never before).
EXAMPLE_CHARS = 400
ARGS_CHARS = 200
ERROR_CHARS = 300
TITLE_CHARS = 80
NAME_CHARS = 60
SHAPE_CHARS = 160
#: The whole report.
REPORT_CHARS = 30_000
#: Reads are bounded: the newest rows of each source.
MAX_FAILED_CALLS = 20_000
MAX_LEAD_CALLS = 50_000
MAX_THREADS = 300
MAX_HELPER_RUNS = 5_000
MAX_EVENTS = 20_000
#: Sessions named per problem, programs named per "not found" problem.
MAX_SESSIONS_SHOWN = 5
MAX_PROGRAMS_SHOWN = 8

#: The report's title and its one instruction for the agent that reads it.
TITLE = "# Fix request: Iron Jarvis coding sessions and model team"
INSTRUCTION = (
    "These are problems Iron Jarvis's coding sessions and model team ran into. Fix them in the "
    "Iron Jarvis repository; each problem says where it happened, what was seen, what was "
    "expected and the evidence."
)


class UnknownSession(LookupError):
    """``session`` names no coding session (the route answers 404)."""


# --------------------------------------------------------------------------- #
# the problem kinds — each with ONE expected sentence and the modules that own it
# --------------------------------------------------------------------------- #

KIND_TABLE: dict[str, dict[str, Any]] = {
    "command_not_found": {
        "title": "A command the shell could not find",
        "expected": (
            "Every command a coding session or its helpers write runs in a shell that has that "
            "program (Git Bash on Windows when it is found), and the model is told which shell "
            "and programs it has before it writes one."
        ),
        "owners": (
            "src/iron_jarvis/harness/command_env.py",
            "src/iron_jarvis/sandbox/native.py",
            "src/iron_jarvis/sandbox/git_bash.py",
        ),
    },
    "command_wrong_shell": {
        "title": "A command written for a different shell",
        "expected": (
            "A command is written in the syntax of the shell that runs it, or Iron Jarvis runs it "
            "in the shell it was written for."
        ),
        "owners": (
            "src/iron_jarvis/harness/command_env.py",
            "src/iron_jarvis/sandbox/cmd_guard.py",
            "src/iron_jarvis/sandbox/git_bash.py",
            "src/iron_jarvis/harness/dialects.py",
        ),
    },
    "command_refused": {
        "title": "A command a guard refused",
        "expected": (
            "The model is told the rule before it writes the command, so a guard's refusal is rare "
            "and says what to do instead."
        ),
        "owners": (
            "src/iron_jarvis/harness/git_guard.py",
            "src/iron_jarvis/sandbox/shell_tool.py",
            "src/iron_jarvis/harness/command_env.py",
        ),
    },
    "command_timed_out": {
        "title": "A command that ran until its time limit",
        "expected": (
            "A long-running command (a server, a watcher, a whole test suite) is started in the "
            "background or given a longer limit; it never sits until the deadline."
        ),
        "owners": (
            "src/iron_jarvis/sandbox/native.py",
            "src/iron_jarvis/harness/process.py",
            "src/iron_jarvis/tools/registry.py",
        ),
    },
    "tool_failed": {
        "title": "A tool call that failed",
        "expected": (
            "A tool is called with arguments that fit what is on disk; when a call fails, the model "
            "reads why and corrects the call instead of repeating it."
        ),
        "owners": ("src/iron_jarvis/tools/registry.py",),
    },
    "tool_not_given": {
        "title": "A tool the model was not given, or called with the wrong arguments",
        "expected": "A model calls only the tools it was given, with the arguments they require.",
        "owners": (
            "src/iron_jarvis/harness/dialects.py",
            "src/iron_jarvis/harness/helpers.py",
            "src/iron_jarvis/tools/registry.py",
        ),
    },
    "ask_not_carried": {
        "title": "A call that needed approval where nothing could ask",
        "expected": (
            "A call that needs the user's approval shows a card, or runs under a grant the user "
            "already gave; it is never refused because nothing could ask."
        ),
        "owners": (
            "src/iron_jarvis/daemon/routes/chat.py",
            "src/iron_jarvis/daemon/chat_turn.py",
            "src/iron_jarvis/agents/runtime.py",
            "src/iron_jarvis/tools/permissions.py",
        ),
    },
    "repeated_call": {
        "title": "The same failing call sent again",
        "expected": (
            "After a failed call the model changes its arguments or its approach; it does not send "
            "the identical call again."
        ),
        "owners": (
            "src/iron_jarvis/agents/runtime.py",
            "src/iron_jarvis/daemon/chat_turn.py",
        ),
    },
    "router_refused": {
        "title": "The team router found no one for a piece",
        "expected": (
            "A piece asked for by role goes to a team member who can take it, or the lead is told "
            "plainly and does it itself."
        ),
        "owners": (
            "src/iron_jarvis/agents/model_team.py",
            "src/iron_jarvis/harness/team.py",
            "src/iron_jarvis/agents/delegate_tool.py",
        ),
    },
    "helper_not_started": {
        "title": "A helper that could not start",
        "expected": "A helper the lead asks for starts, or the refusal says exactly what to change.",
        "owners": (
            "src/iron_jarvis/agents/delegate_tool.py",
            "src/iron_jarvis/agents/agent_tools.py",
            "src/iron_jarvis/harness/helpers.py",
        ),
    },
    "helper_failed": {
        "title": "A helper run that failed",
        "expected": (
            "A helper finishes its piece, or its failure is recovered (waited out, retried) before "
            "the lead has to redo the work."
        ),
        "owners": (
            "src/iron_jarvis/harness/helpers.py",
            "src/iron_jarvis/agents/runtime.py",
            "src/iron_jarvis/providers/router.py",
        ),
    },
    "helper_step_limit": {
        "title": "A helper that ran out of steps",
        "expected": (
            "A helper's piece fits its step budget, or the helper stops early and reports what it "
            "finished."
        ),
        "owners": (
            "src/iron_jarvis/agents/runtime.py",
            "src/iron_jarvis/harness/helpers.py",
            "src/iron_jarvis/agents/model_team.py",
        ),
    },
    "context_too_small": {
        "title": "A piece too big for the member's model",
        "expected": "A piece goes only to a member whose model can hold it beside its tools.",
        "owners": (
            "src/iron_jarvis/agents/model_team.py",
            "src/iron_jarvis/context/agent_window.py",
            "src/iron_jarvis/agents/runtime.py",
        ),
    },
    "text_tool_call": {
        "title": "A tool call written as text",
        "expected": (
            "The model calls the tool through the tool interface; its dialect and its instructions "
            "make that the obvious way."
        ),
        "owners": (
            "src/iron_jarvis/harness/mode.py",
            "src/iron_jarvis/harness/dialects.py",
            "src/iron_jarvis/agents/runtime.py",
        ),
    },
    "cut_off": {
        "title": "A reply cut off at the output limit",
        "expected": (
            "A reply's output budget fits the work (a large file is written in parts), so nothing "
            "is cut off."
        ),
        "owners": (
            "src/iron_jarvis/providers/reply_budget.py",
            "src/iron_jarvis/daemon/chat_turn.py",
        ),
    },
    "reply_failed": {
        "title": "A coding reply that ended on an error",
        "expected": (
            "A passing provider failure is waited out and the reply keeps its work; a lasting one "
            "says plainly what to do."
        ),
        "owners": (
            "src/iron_jarvis/harness/patience.py",
            "src/iron_jarvis/providers/router.py",
            "src/iron_jarvis/providers/failure_words.py",
        ),
    },
    "patience_gave_up": {
        "title": "A reply that kept retrying and then gave up",
        "expected": (
            "A wait that cannot succeed (a model server that is down) ends early and says so; one "
            "that can succeed is given long enough."
        ),
        "owners": (
            "src/iron_jarvis/harness/patience.py",
            "src/iron_jarvis/providers/router.py",
        ),
    },
    "review_not_run": {
        "title": "The team review did not run or did not finish",
        "expected": (
            "Every reply with a team reviewer gets its review within the time limit, and the "
            "verdict is read."
        ),
        "owners": (
            "src/iron_jarvis/harness/finish.py",
            "src/iron_jarvis/harness/helpers.py",
            "src/iron_jarvis/agents/model_team.py",
        ),
    },
}

#: The tool → module table a ``tool_failed`` problem adds to its owners.
TOOL_MODULES: dict[str, str] = {
    "read_file": "src/iron_jarvis/tools/builtins.py",
    "write_file": "src/iron_jarvis/tools/builtins.py",
    "edit_file": "src/iron_jarvis/harness/textedit.py",
    "list_files": "src/iron_jarvis/tools/builtins.py",
    "grep": "src/iron_jarvis/tools/builtins.py",
    "multi_edit": "src/iron_jarvis/harness/tools.py",
    "apply_patch": "src/iron_jarvis/harness/tools.py",
    "glob": "src/iron_jarvis/harness/tools.py",
    "notebook_edit": "src/iron_jarvis/harness/tools.py",
    "plan_update": "src/iron_jarvis/harness/tools.py",
    "process_start": "src/iron_jarvis/harness/process.py",
    "process_read": "src/iron_jarvis/harness/process.py",
    "process_write": "src/iron_jarvis/harness/process.py",
    "process_stop": "src/iron_jarvis/harness/process.py",
    "git_read": "src/iron_jarvis/harness/git_read.py",
    "web_fetch": "src/iron_jarvis/tools/webfetch.py",
    "web_search": "src/iron_jarvis/tools/websearch.py",
    "skill_load": "src/iron_jarvis/skills/tools.py",
    "skill_search": "src/iron_jarvis/skills/tools.py",
}

#: The order kinds are listed in when two problems were seen equally often.
_KIND_ORDER = {k: i for i, k in enumerate(KIND_TABLE)}

#: The tools that run a command (their failures are judged by the rule above).
COMMAND_TOOLS = frozenset({"shell", "process_start"})
#: The tools that start a helper.
HELPER_TOOLS = frozenset({"delegate", "spawn_agent"})

#: ``tool.denied`` kinds that are a PERSON's or a policy's decision.
_USER_DENIED_KINDS = frozenset({
    "permission denied", "paused", "low trust", "needs your ok",
    # ``chat_turn.SPEND_NOT_ASKED_LABEL``: the POST lane cannot show a card.
    "not run",
})
#: ``tool.denied`` kinds that are Iron Jarvis refusing a malformed call.
_NOT_GIVEN_KINDS = frozenset({"not armed"})
_REPEAT_KINDS = frozenset({"refused"})

#: Words a user's Stop or answer, a cancel, a restart, Emergency stop or a
#: low-trust refusal leaves as a row's OWN error (never a defect). Read off
#: the tool's error only — a command's output may quote any of them.
_STOP_MARKERS = (
    "stopped by you",
    "was stopped by the user",
    "the user stopped the reply",
    "stopped by the user before the helper finished",
    "the run was cancelled while the tool was running",
    "client disconnected while the tool was running",
    "iron jarvis stopped while this was running",
    "emergency stop is on",
    "the approval request timed out",
    "declined this call when asked",
    "left for the user:",
    "low trust needs the sandbox",
    # the permission engine: the user's own rule, the user's own answer
    "denied by policy",
    "rejected by user",
)
#: The permission engine refusing an ask nothing could put to the user
#: (``tools/permissions.authorize``) — no one was asked, so it is no choice of
#: the user's: a lane or a run that should have carried a card or a grant.
_NOTHING_ASKED = re.compile(
    r"requires approval; no resolver in headless mode|needs approval and nothing here could ask",
    re.IGNORECASE,
)

#: A command that could NOT RUN (the marker line names it).
_NOT_FOUND = re.compile(
    r"is not recognized as an internal or external command"
    r"|is not recognized as the name of a cmdlet"
    r"|command not found"
    r"|is not a valid win32 application"
    r"|cannot execute binary file"
    r"|exec format error"
    r"|python was not found; run without arguments to install",
    re.IGNORECASE,
)
_WRONG_SHELL = re.compile(
    r"was unexpected at this time"
    r"|the syntax of the command is incorrect"
    r"|syntax error near unexpected token"
    r"|unexpected eof while looking for matching"
    r"|is not a valid statement separator"
    r"|does not separate commands",
    re.IGNORECASE,
)
_REFUSED_CMD = re.compile(
    r"^refused:"
    r"|^`cwd` .{0,300}(?:is not a folder|is outside the)",
    re.IGNORECASE,
)
_TIMED_OUT = re.compile(r"^command timed out|did not finish within .{0,40} it was stopped", re.IGNORECASE)
#: The program name a "not recognized" / "command not found" line names.
_PROGRAM = re.compile(
    r"^'([^'\n]{1,80})' is not recognized"
    r"|^(?:bash: )?(?:line \d+: )?([^\s:'\"]{1,80}): command not found",
    re.IGNORECASE,
)

#: A ``delegate`` row whose output shows a run STARTED (its Session row counts it).
_CHILD_RAN = re.compile(
    r"could not finish|reached max steps|agent_run_id=|\bsession=session_", re.IGNORECASE
)
#: The team router's own refusal sentences (``agents/model_team.route_role``,
#: ``harness/team.UNLOCK_NOTE``).
_ROUTER = re.compile(
    r"^(?:no worker could take this|no one on |no team member |iron jarvis could not pick a team member"
    r"|no one on your model team)",
    re.IGNORECASE,
)
_NOT_GIVEN = re.compile(
    r"^unknown tool '|^missing required:|is not one of this agent's tools|^'[^'\n]{0,80}' is not a team role",
    re.IGNORECASE,
)
_REPEAT = re.compile(r"repeated-failure breaker|the same call failed", re.IGNORECASE)

#: Lines Iron Jarvis adds to a command's output (not the command's words).
_ADVISORY = re.compile(
    r"^\s*(?:\[native runtime|\[warning\] sandbox|\[earlier output dropped\])", re.IGNORECASE
)

#: A context window too small (``runtime.context_too_small_result``).
_TOO_SMALL = re.compile(r"context window \([\d,]+ tokens\) is too small", re.IGNORECASE)
_STEP_LIMIT = re.compile(r"reached max steps|ran out of steps|step limit", re.IGNORECASE)
#: A reply that waited and gave up (``harness/patience.tried_words``).
_TRIED = re.compile(r"iron jarvis tried \d+ times", re.IGNORECASE)
#: A restart that cut a reply off (``durable_turns.INTERRUPTED_DETAIL``,
#: ``harness/attempts.CUT_OFF_LINE``).
_RESTART = re.compile(r"^iron jarvis stopped while", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# small helpers (pure)
# --------------------------------------------------------------------------- #


def clamp_days(days: Any) -> int:
    """``days`` as an int in [MIN_DAYS, MAX_DAYS] (anything unreadable is the default)."""
    try:
        n = int(days)
    except (TypeError, ValueError):
        return DEFAULT_DAYS
    return max(MIN_DAYS, min(MAX_DAYS, n))


def bare_thread(session: Any) -> str:
    """A coding session's thread id from ``build:<id>`` / ``<id>`` (an
    attempt's ``~try<n>`` dropped)."""
    sid = str(session or "").strip()
    if sid.startswith("build:"):
        sid = sid[len("build:"):]
    return sid.split("~", 1)[0]


def short_id(session: Any) -> str:
    """A session id cut to its kind + 8 characters (``session_65e1c57a``,
    ``chat_382172c4``, ``chat_382172c4~try1``)."""
    sid = str(session or "")
    if sid.startswith("build:"):
        sid = sid[len("build:"):]
    base, sep, tail = sid.partition("~")
    head, us, rest = base.rpartition("_")
    if us and len(rest) > 8:
        base = f"{head}_{rest[:8]}"
    elif not us and len(base) > 16:
        base = base[:16]
    return base + (f"~{tail}" if sep else "")


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def clean(value: Any, limit: int) -> str:
    """ONE line of text that came from a model, a tool, a file or the user:
    masked, then promptguard-scanned (the report is pasted into a prompt),
    then folded to one line and cut — never cut before it is masked."""
    text = str(value or "")
    if not text:
        return ""
    try:
        from ..providers.failure_words import mask_failure_text

        text = mask_failure_text(text)
    except Exception:  # noqa: BLE001 — a mask fault must never let a value through
        return "(left out: it could not be masked)"
    try:
        from ..core.promptguard import scan_context

        text = scan_context(text, source="Iron Jarvis's records", cap=None).text
    except Exception:  # noqa: BLE001 — scan_context never raises; belt and braces
        pass
    text = " ".join(text.split()).replace("`", "'")
    return _cut(text, limit)


_QUOTED = re.compile(r"'[^'\n]{0,200}'|\"[^\"\n]{0,200}\"|`[^`\n]{0,200}`|‘[^’\n]{0,200}’|“[^”\n]{0,200}”")
_UNC = re.compile(r"\\\\[^\s'\"`,;)]+")
_WINPATH = re.compile(r"\b[A-Za-z]:[\\/][^\s'\"`,;)]*")
_SLASHPATH = re.compile(r"(?<![\w<])(?:\.{0,2}[\\/])?[\w.\-]+(?:[\\/][\w.\-]+)+")
_FILE = re.compile(r"\b[\w\-]+\.(?:py|ts|tsx|js|jsx|mjs|cjs|json|md|txt|toml|ya?ml|css|html|sql|sh|ps1|bat|cmd|exe|diff|log|ini|cfg|lock)\b", re.IGNORECASE)
_ID = re.compile(r"\b[a-z]+_[0-9a-f]{6,}\b", re.IGNORECASE)
_HEX = re.compile(r"\b[0-9a-f]{7,}\b", re.IGNORECASE)
_NUM = re.compile(r"\d+(?:[.,]\d+)*")


def shape(text: Any) -> str:
    """The error's SHAPE (the grouping key): masked, then quoted text, paths,
    file names, ids and numbers normalised, spaces folded, cut. Two errors that
    differ only in which file or program they name are one problem."""
    raw = str(text or "")
    try:
        from ..providers.failure_words import mask_failure_text

        raw = mask_failure_text(raw)
    except Exception:  # noqa: BLE001
        raw = ""
    s = _QUOTED.sub("'…'", raw)
    s = _UNC.sub("<path>", s)
    s = _WINPATH.sub("<path>", s)
    s = _SLASHPATH.sub("<path>", s)
    s = _FILE.sub("<file>", s)
    s = _ID.sub("<id>", s)
    s = _HEX.sub("N", s)
    s = _NUM.sub("N", s)
    return _cut(" ".join(s.split()), SHAPE_CHARS)


def split_failure(output: Any) -> tuple[str, list[str]]:
    """``(header, body lines)`` of a failed call's ledger text
    (``registry.compose_failure_text``: the tool's error, then ``[output]``,
    then the output's tail), Iron Jarvis's own advisory lines removed."""
    text = str(output or "")
    head, sep, body = text.partition("\n[output]\n")
    lines = [ln for ln in body.splitlines() if ln.strip() and not _ADVISORY.match(ln)] if sep else []
    return head.strip(), lines


def _first_line(text: str) -> str:
    for ln in str(text or "").splitlines():
        if ln.strip():
            return ln.strip()
    return ""


def classify_call(tool: str, output: Any, denied_kind: str = "") -> "tuple[str, str] | None":
    """``(kind, the line that shows it)`` for one FAILED call, or None when it
    is left out (the module docstring's rule). Pure."""
    tool = str(tool or "")
    text = str(output or "")
    kind_word = str(denied_kind or "").strip().lower()
    head, body = split_failure(text)
    if _NOTHING_ASKED.search(head):
        return "ask_not_carried", head
    if kind_word in _USER_DENIED_KINDS:
        return None
    if any(m in head.lower() for m in _STOP_MARKERS):
        return None
    if kind_word in _REPEAT_KINDS or _REPEAT.search(head):
        return "repeated_call", head
    if kind_word in _NOT_GIVEN_KINDS or _NOT_GIVEN.search(head):
        return "tool_not_given", head
    if tool in HELPER_TOOLS:
        if _CHILD_RAN.search(text):
            return None  # the helper's own Session row is the record
        if _ROUTER.search(head):
            return "router_refused", head
        return "helper_not_started", head
    if tool in COMMAND_TOOLS:
        if _TIMED_OUT.search(head):
            return "command_timed_out", head
        if _REFUSED_CMD.search(head):
            return "command_refused", head
        lines = [head, *body]
        for ln in lines:
            if _WRONG_SHELL.search(ln):
                return "command_wrong_shell", ln.strip()
        for ln in lines:
            if _NOT_FOUND.search(ln):
                return "command_not_found", ln.strip()
        return None  # it RAN and exited non-zero: the user's own work
    return "tool_failed", head or _first_line(text)


def group_shape(kind: str, line: str) -> str:
    """The SHAPE a problem is grouped by: for a command that could not run,
    the marker that showed it ("is not recognized as an internal or external
    command", "was unexpected at this time", …) — never the line, which names
    a different program or token every time; the timeouts and the breaker's
    refusals are one shape each; anything else is :func:`shape` of its line."""
    rx = {"command_not_found": _NOT_FOUND, "command_wrong_shell": _WRONG_SHELL}.get(kind)
    if rx is not None:
        m = rx.search(str(line or ""))
        if m:
            return m.group(0).lower()
    if kind == "command_timed_out":
        return "timed out"
    if kind == "repeated_call":
        return "repeated"
    return shape(line)


def program_of(line: str) -> str:
    """The program a "not recognized" / "command not found" line names."""
    m = _PROGRAM.search(str(line or "").strip())
    if not m:
        return ""
    return (m.group(1) or m.group(2) or "").strip()


def failure_example(output: Any) -> str:
    """A failed call's own words for "What happened": the tool's error and
    the output's tail with Iron Jarvis's advisory lines removed (raw — the
    caller cleans it)."""
    head, body = split_failure(output)
    return " · ".join(p for p in [head, " ".join(ln.strip() for ln in body)] if p)


def args_words(tool: str, args_json: Any) -> str:
    """A call's arguments as one line (raw — the caller cleans it): a
    command tool's command, else the arguments as sorted JSON."""
    try:
        args = json.loads(args_json) if isinstance(args_json, str) else args_json
    except (TypeError, ValueError):
        return str(args_json or "")
    if not isinstance(args, dict):
        return str(args or "")
    if tool in COMMAND_TOOLS and isinstance(args.get("command"), str):
        return args["command"]
    try:
        return json.dumps(args, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(args)


def model_words(provider: Any, model: Any) -> str:
    p, m = str(provider or "").strip(), str(model or "").strip()
    return " · ".join(x for x in (p, m) if x)


def member_name(agent_name: Any, agent_type: Any) -> str:
    """The member a helper / teammate run worked as: a custom agent's name,
    else "a <type> helper"."""
    name = str(agent_name or "").strip()
    for prefix in ("custom:", "remote:", "dynamic:"):
        if name.startswith(prefix):
            return name[len(prefix):] or name
    base = name or str(getattr(agent_type, "value", agent_type) or "").strip().lower()
    return f"a {base} helper" if base else "a helper"


def os_words() -> str:
    """The operating system as the report states it. Never raises (never asks
    WMI: ``sys.getwindowsversion`` only)."""
    try:
        if sys.platform == "win32":
            v = sys.getwindowsversion()
            name = "Windows 11" if v.major == 10 and v.build >= 22000 else f"Windows {v.major}"
            return f"{name} ({v.major}.{v.minor}.{v.build})"
        import platform as _platform

        return " ".join(x for x in (_platform.system(), _platform.release()) if x) or "not known"
    except Exception:  # noqa: BLE001
        return "not known"


def _utc(value: Any) -> "datetime | None":
    """An aware UTC datetime from a SQLite value (naive = UTC), an ISO string
    or epoch milliseconds; None when unreadable."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def when(dt: "datetime | None") -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if dt else "time not recorded"


def _now() -> datetime:
    """The clock (a seam: the route's tests hold it still)."""
    return datetime.now(timezone.utc)


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# what gather() collects
# --------------------------------------------------------------------------- #


@dataclass
class Occurrence:
    """One incident, with its RAW values (cleaned only when shown)."""

    kind: str
    who: str
    model: str
    tool: str
    shape: str
    surface: str
    session: str
    at: "datetime | None"
    example: str = ""
    args: str = ""
    error: str = ""
    program: str = ""
    #: A plain fact for the evidence row that is no error ("in round 3").
    note: str = ""


@dataclass
class Problem:
    kind: str
    who: str
    model: str
    tool: str
    shape: str
    surface: str
    count: int
    first: "datetime | None"
    last: "datetime | None"
    sessions: list[str]
    example: str
    evidence: list[dict[str, str]]
    programs: list[tuple[str, int]] = field(default_factory=list)
    #: ``(total, noun)`` — "N of M <noun>"; None when there is no total.
    out_of: "tuple[int, str] | None" = None


@dataclass
class Gathered:
    version: str
    os: str
    days: int
    since: datetime
    until: datetime
    scope_id: str = ""
    scope_title: str = ""
    sessions: int = 0
    pieces: int = 0
    low_trust_left_out: int = 0
    unreadable: list[str] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# gather — BLOCKING, never raises (except UnknownSession)
# --------------------------------------------------------------------------- #


def _rows(engine: Any, sql: str, **params: Any) -> list[tuple]:
    from sqlalchemy import text

    from ..core.db import session_scope

    with session_scope(engine) as db:
        return [tuple(r) for r in db.exec(text(sql).bindparams(**params)).all()]  # type: ignore[call-overload]


def _naive(dt: datetime) -> str:
    """A bind value SQLite compares as text the way it stores datetimes."""
    return dt.astimezone(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S.%f")


class _LeadModels:
    """The model a coding session's lead used at a given time: the chat
    usage row (``AgentRun``, ``session_id = 'chat'``, ``thread_id`` = the
    session) whose turn covers the moment, else the nearest turn after it,
    else the newest before it, else the session's own setup."""

    def __init__(self, runs: "dict[str, list[tuple[datetime, datetime, str]]]",
                 setups: "dict[str, str]") -> None:
        self.runs = runs
        self.setups = setups

    def at(self, thread: str, dt: "datetime | None") -> str:
        spans = self.runs.get(thread) or []
        if dt is not None and spans:
            for start, end, words in spans:
                if start <= dt <= end:
                    return words
            after = [s for s in spans if s[1] >= dt]
            if after:
                return min(after, key=lambda s: s[1])[2]
            return max(spans, key=lambda s: s[1])[2]
        if spans:
            return spans[-1][2]
        return self.setups.get(thread, "")


def _scope_clause(prefix: str, scope: str) -> tuple[str, dict[str, Any]]:
    """The SQL that keeps a ToolInvocation (alias ``t``) joined to its
    Session (alias ``s``) to the coding sessions and their team."""
    from ..agents.team import MISSION_MEMBER_ORIGIN
    from ..harness.helpers import BUILD_SESSION_OPTION, HELPER_ORIGIN

    if scope:
        build = f"build:{scope}"
        return (
            f"({prefix}t.session_id = :b OR substr({prefix}t.session_id, 1, :tn) = :bt "
            f"OR ({prefix}s.origin = :ho AND json_valid({prefix}s.options_json) "
            f"AND json_extract({prefix}s.options_json, :key) = :thread))"
        ), {"b": build, "bt": build + "~", "tn": len(build) + 1, "ho": HELPER_ORIGIN,
            "key": f"$.{BUILD_SESSION_OPTION}", "thread": scope}
    return (
        f"(substr({prefix}t.session_id, 1, 6) = 'build:' OR {prefix}s.origin IN (:ho, :mo))"
    ), {"ho": HELPER_ORIGIN, "mo": MISSION_MEMBER_ORIGIN}


def _who_of_session(origin: Any, agent_name: Any, agent_type: Any,
                    provider: Any, model: Any) -> tuple[str, str, str]:
    """``(who, model words, surface)`` of a helper / teammate run."""
    from ..agents.team import MISSION_MEMBER_ORIGIN

    surface = "an objective's teammate" if origin == MISSION_MEMBER_ORIGIN else "a coding session's helper"
    return member_name(agent_name, agent_type), model_words(provider, model), surface


def _lead_who(session_id: str) -> str:
    sid = str(session_id or "")
    if "~try" in sid:
        return f"an attempt ({sid.split('~', 1)[1]})"
    return "the lead"


def gather(engine: Any, *, days: Any = DEFAULT_DAYS, session: Any = "", now: "datetime | None" = None,
           version: "str | None" = None, os_name: "str | None" = None) -> Gathered:
    """What the records say (the module docstring). BLOCKING; never raises,
    except :class:`UnknownSession` for a ``session`` that names no coding
    session (when the sessions could be read)."""
    days = clamp_days(days)
    until = (now or _now()).astimezone(timezone.utc)
    since = until - timedelta(days=days)
    if version is None:
        try:
            from .. import __version__ as version  # type: ignore[no-redef]
        except Exception:  # noqa: BLE001
            version = "not known"
    out = Gathered(version=str(version), os=os_name if os_name is not None else os_words(),
                   days=days, since=since, until=until)
    scope = bare_thread(session)
    s_lo, s_hi = _naive(since), _naive(until)
    occurrences: list[Occurrence] = []

    # ---- the coding sessions (and the scoped one) ------------------------ #
    threads: list[tuple] = []
    try:
        if scope:
            found = _rows(engine, "SELECT id, title FROM chatthreadrecord WHERE id = :id AND surface = 'build'",
                          id=scope)
            if not found:
                raise UnknownSession(scope)
            out.scope_id = scope
            out.scope_title = clean(found[0][1], TITLE_CHARS)
            threads = _rows(
                engine,
                "SELECT id, title, messages_json, setup_json FROM chatthreadrecord WHERE id = :id",
                id=scope,
            )
        else:
            threads = _rows(
                engine,
                "SELECT id, title, messages_json, setup_json FROM chatthreadrecord "
                "WHERE surface = 'build' AND updated_at >= :lo ORDER BY updated_at DESC LIMIT :n",
                lo=s_lo, n=MAX_THREADS,
            )
    except UnknownSession:
        raise
    except Exception:  # noqa: BLE001
        log.warning("fix request: coding sessions unreadable", exc_info=True)
        out.unreadable.append("the coding sessions' replies")
        if scope:
            out.scope_id = scope

    setups: dict[str, str] = {}
    lines_by_thread: dict[str, list[dict[str, Any]]] = {}
    for row in threads:
        tid = str(row[0])
        try:
            setup = json.loads(row[3] or "{}") if row[3] else {}
        except (TypeError, ValueError):
            setup = {}
        if isinstance(setup, dict):
            setups[tid] = model_words(setup.get("provider"), setup.get("model"))
        try:
            msgs = json.loads(row[2] or "[]")
        except (TypeError, ValueError):
            msgs = []
        lines_by_thread[tid] = [m for m in msgs if isinstance(m, dict)] if isinstance(msgs, list) else []

    # Low-trust replies: their time windows (the lead's calls inside are
    # never read), or the whole session's lead calls when a window is missing.
    low_windows: dict[str, list[tuple[datetime, datetime]]] = {}
    low_all: set[str] = set()
    replies: list[tuple[str, dict[str, Any], "datetime | None"]] = []
    for tid, msgs in lines_by_thread.items():
        for m in msgs:
            if m.get("role") != "assistant":
                continue
            at = _utc(m.get("at"))
            if at is None or not (since <= at <= until):
                continue
            if str(m.get("trust") or "").strip().lower() == "low":
                out.low_trust_left_out += 1
                timing = m.get("timing") if isinstance(m.get("timing"), dict) else {}
                start, end = _utc(timing.get("startedAt")), _utc(timing.get("endedAt"))
                if start and end:
                    low_windows.setdefault(tid, []).append((start, end))
                else:
                    low_all.add(tid)
                continue
            replies.append((tid, m, at))

    def _in_low(thread: str, dt: "datetime | None") -> bool:
        if thread in low_all:
            return True
        if dt is None:
            return bool(low_windows.get(thread))
        return any(a <= dt <= b for a, b in low_windows.get(thread, ()))

    # ---- the lead's models over time ------------------------------------- #
    runs: dict[str, list[tuple[datetime, datetime, str]]] = {}
    try:
        lo = _naive(since - timedelta(days=1))
        hi = _naive(until + timedelta(days=1))
        if scope:
            got = _rows(engine, "SELECT thread_id, created_at, finished_at, provider, model FROM agentrun "
                                "WHERE session_id = 'chat' AND thread_id = :t AND created_at >= :lo "
                                "AND created_at <= :hi", t=scope, lo=lo, hi=hi)
        else:
            got = _rows(engine, "SELECT thread_id, created_at, finished_at, provider, model FROM agentrun "
                                "WHERE session_id = 'chat' AND thread_id IS NOT NULL AND created_at >= :lo "
                                "AND created_at <= :hi ORDER BY created_at DESC LIMIT :n",
                        lo=lo, hi=hi, n=MAX_LEAD_CALLS)
        for tid, created, finished, provider, model in got:
            start = _utc(created)
            end = _utc(finished) or start
            if start is None or end is None:
                continue
            runs.setdefault(str(tid), []).append((start, end, model_words(provider, model)))
    except Exception:  # noqa: BLE001
        log.debug("fix request: the lead's models unreadable", exc_info=True)
    for spans in runs.values():
        spans.sort(key=lambda s: s[0])
    leads = _LeadModels(runs, setups)

    clause, params = _scope_clause("", scope)
    threads_seen: set[str] = set(lines_by_thread) if scope else {tid for tid, _m, _a in replies}

    # ---- every call in scope (the "N of M" totals) ----------------------- #
    totals: Counter[tuple[str, str, str]] = Counter()
    try:
        got = _rows(
            engine,
            "SELECT t.session_id, t.tool, t.created_at, s.origin, s.agent_name, s.agent_type, "
            "s.provider, s.model, s.trust, CASE WHEN json_valid(s.options_json) "
            "THEN json_extract(s.options_json, '$.build_session') END "
            "FROM toolinvocation AS t LEFT JOIN session AS s ON s.id = t.session_id "
            f"WHERE t.created_at >= :lo AND t.created_at <= :hi AND {clause} "
            "ORDER BY t.created_at DESC LIMIT :n",
            lo=s_lo, hi=s_hi, n=MAX_LEAD_CALLS, **params,
        )
        for sid, tool, created, origin, aname, atype, prov, model, trust, build in got:
            sid = str(sid or "")
            at = _utc(created)
            if sid.startswith("build:"):
                thread = bare_thread(sid)
                threads_seen.add(thread)
                if _in_low(thread, at):
                    continue
                totals[(_lead_who(sid), leads.at(thread, at), str(tool))] += 1
            else:
                if str(trust or "").strip().lower() == "low":
                    continue
                if build:
                    threads_seen.add(str(build))
                who, mw, _surface = _who_of_session(origin, aname, atype, prov, model)
                totals[(who, mw, str(tool))] += 1
    except Exception:  # noqa: BLE001
        log.warning("fix request: the tool-call ledger unreadable", exc_info=True)
        out.unreadable.append("the tool-call ledger")

    # ---- why each refusal was refused (tool.denied events) ---------------- #
    denied: dict[str, str] = {}
    try:
        got = _rows(
            engine,
            "SELECT payload_json FROM eventrecord WHERE type = 'tool.denied' AND created_at >= :lo "
            "AND created_at <= :hi ORDER BY created_at DESC LIMIT :n",
            lo=s_lo, hi=s_hi, n=MAX_EVENTS,
        )
        for (payload,) in got:
            try:
                p = json.loads(payload or "{}")
            except (TypeError, ValueError):
                continue
            if isinstance(p, dict) and p.get("invocation_id"):
                denied[str(p["invocation_id"])] = str(p.get("kind") or "")
    except Exception:  # noqa: BLE001
        log.warning("fix request: refusals unreadable", exc_info=True)
        out.unreadable.append("why calls were refused")

    # ---- failed calls ------------------------------------------------------ #
    try:
        got = _rows(
            engine,
            "SELECT t.id, t.session_id, t.tool, t.output, t.args_json, t.created_at, s.origin, "
            "s.agent_name, s.agent_type, s.provider, s.model, s.trust "
            "FROM toolinvocation AS t LEFT JOIN session AS s ON s.id = t.session_id "
            f"WHERE t.ok = 0 AND t.created_at >= :lo AND t.created_at <= :hi AND {clause} "
            "ORDER BY t.created_at DESC LIMIT :n",
            lo=s_lo, hi=s_hi, n=MAX_FAILED_CALLS, **params,
        )
        for iid, sid, tool, output, args_json, created, origin, aname, atype, prov, model, trust in got:
            sid, tool = str(sid or ""), str(tool or "")
            at = _utc(created)
            if sid.startswith("build:"):
                thread = bare_thread(sid)
                if _in_low(thread, at):
                    continue
                who, mw, surface = _lead_who(sid), leads.at(thread, at), "a coding session"
            else:
                if str(trust or "").strip().lower() == "low":
                    continue
                who, mw, surface = _who_of_session(origin, aname, atype, prov, model)
            verdict = classify_call(tool, output, denied.get(str(iid), ""))
            if verdict is None:
                continue
            kind, line = verdict
            occurrences.append(Occurrence(
                kind=kind, who=who, model=mw, tool=tool, shape=group_shape(kind, line), surface=surface,
                session=sid, at=at, example=failure_example(output), args=args_words(tool, args_json),
                error=failure_example(output),
                program=program_of(line) if kind == "command_not_found" else "",
            ))
    except Exception:  # noqa: BLE001
        log.warning("fix request: failed calls unreadable", exc_info=True)
        if "the tool-call ledger" not in out.unreadable:
            out.unreadable.append("the tool-call ledger")

    # ---- helper / teammate runs -------------------------------------------- #
    from ..agents.team import MISSION_MEMBER_ORIGIN
    from ..harness.helpers import BUILD_SESSION_OPTION, HELPER_ORIGIN

    pieces: Counter[tuple[str, str]] = Counter()
    helper_sessions: dict[str, tuple[str, str, str, bool]] = {}
    try:
        if scope:
            got = _rows(
                engine,
                "SELECT id, origin, agent_name, agent_type, provider, model, status, summary, "
                "failure_detail, trust, created_at, NULL FROM session "
                "WHERE origin = :ho AND json_valid(options_json) AND json_extract(options_json, :key) = :t "
                "AND created_at >= :lo AND created_at <= :hi ORDER BY created_at DESC LIMIT :n",
                ho=HELPER_ORIGIN, key=f"$.{BUILD_SESSION_OPTION}", t=scope, lo=s_lo, hi=s_hi,
                n=MAX_HELPER_RUNS,
            )
        else:
            got = _rows(
                engine,
                "SELECT id, origin, agent_name, agent_type, provider, model, status, summary, "
                "failure_detail, trust, created_at, CASE WHEN json_valid(options_json) "
                "THEN json_extract(options_json, :key) END FROM session "
                "WHERE origin IN (:ho, :mo) AND created_at >= :lo AND created_at <= :hi "
                "ORDER BY created_at DESC LIMIT :n",
                ho=HELPER_ORIGIN, mo=MISSION_MEMBER_ORIGIN, key=f"$.{BUILD_SESSION_OPTION}",
                lo=s_lo, hi=s_hi, n=MAX_HELPER_RUNS,
            )
        for sid, origin, aname, atype, prov, model, status, summary, detail, trust, created, build in got:
            who, mw, surface = _who_of_session(origin, aname, atype, prov, model)
            low = str(trust or "").strip().lower() == "low"
            helper_sessions[str(sid)] = (who, mw, surface, low)
            if build:
                threads_seen.add(str(build))
            if low:
                out.low_trust_left_out += 1
                continue
            pieces[(who, mw)] += 1
            if str(status or "").strip().lower() != "failed":
                continue  # completed (its failed calls are read above) / stopped by the user
            words = str(summary or "")
            if any(m in words.lower() for m in _STOP_MARKERS):
                continue
            kind = ("context_too_small" if _TOO_SMALL.search(words)
                    else "helper_step_limit" if _STEP_LIMIT.search(words)
                    else "helper_failed")
            occurrences.append(Occurrence(
                kind=kind, who=who, model=mw, tool="", shape=shape(_first_line(words)), surface=surface,
                session=str(sid), at=_utc(created), example=words,
                error=str(detail or "") or words,
            ))
    except Exception:  # noqa: BLE001
        log.warning("fix request: helper runs unreadable", exc_info=True)
        out.unreadable.append("the helpers' runs")
    out.pieces = sum(pieces.values())

    # ---- tool calls written as text (autonomy.nudged) --------------------- #
    try:
        got = _rows(
            engine,
            "SELECT session_id, payload_json, created_at FROM eventrecord WHERE type = 'autonomy.nudged' "
            "AND created_at >= :lo AND created_at <= :hi ORDER BY created_at DESC LIMIT :n",
            lo=s_lo, hi=s_hi, n=MAX_EVENTS,
        )
        wanted: list[tuple[str, dict[str, Any], "datetime | None"]] = []
        missing: set[str] = set()
        for sid, payload, created in got:
            try:
                p = json.loads(payload or "{}")
            except (TypeError, ValueError):
                continue
            if not isinstance(p, dict) or p.get("kind") != "text_tool_call":
                continue
            sid = str(sid or "")
            if sid.startswith("build:"):
                if scope and bare_thread(sid) != scope:
                    continue
            elif sid not in helper_sessions:
                missing.add(sid)
            wanted.append((sid, p, _utc(created)))
        if missing and not scope:
            for sid, origin, aname, atype, prov, model, trust in _rows(
                engine,
                "SELECT id, origin, agent_name, agent_type, provider, model, trust FROM session "
                "WHERE origin IN (:ho, :mo) AND id IN (SELECT value FROM json_each(:ids))",
                ho=HELPER_ORIGIN, mo=MISSION_MEMBER_ORIGIN, ids=json.dumps(sorted(missing)[:MAX_EVENTS]),
            ):
                who, mw, surface = _who_of_session(origin, aname, atype, prov, model)
                helper_sessions[str(sid)] = (who, mw, surface, str(trust or "").strip().lower() == "low")
        for sid, p, at in wanted:
            call = str(p.get("call") or "").strip()
            if sid.startswith("build:"):
                thread = bare_thread(sid)
                if _in_low(thread, at):
                    continue
                threads_seen.add(thread)
                who, mw, surface = _lead_who(sid), leads.at(thread, at), "a coding session"
            else:
                known = helper_sessions.get(sid)
                if known is None or known[3]:
                    continue
                who, mw, surface = known[0], known[1], known[2]
            shown = f"wrote a tool call as text ({call})" if call else "wrote a tool call as text"
            rnd = p.get("round")
            occurrences.append(Occurrence(
                kind="text_tool_call", who=who, model=mw, tool="", shape=shape(call or "text"),
                surface=surface, session=sid, at=at, example=shown,
                note=(f"in round {rnd}" if isinstance(rnd, int) and not isinstance(rnd, bool) else ""),
            ))
    except Exception:  # noqa: BLE001
        log.warning("fix request: events unreadable", exc_info=True)
        out.unreadable.append("the event log")

    # ---- the replies --------------------------------------------------------- #
    reply_totals: Counter[tuple[str, str]] = Counter()
    for tid, m, at in replies:
        route = m.get("route") if isinstance(m.get("route"), dict) else {}
        # The billed turn's model first (a failed reply's line has no route),
        # so a session's replies are counted under ONE model name.
        mw = leads.at(tid, at) or model_words(route.get("provider"), route.get("model"))
        who = "the lead"
        reply_totals[(who, mw)] += 1
        sid = f"build:{tid}"
        err = str(m.get("error") or "").strip()
        if err:
            ek = str(m.get("errorKind") or "")
            if ek in ("refusal", "sign_in", "key") or m.get("accountLimit") or _RESTART.search(err):
                pass  # a provider's own safety call, the user's sign-in / key, a limit, a restart
            else:
                title = str(m.get("errorTitle") or "") or _first_line(err)
                kind = "patience_gave_up" if _TRIED.search(err) else "reply_failed"
                occurrences.append(Occurrence(
                    kind=kind, who=who, model=mw, tool="", shape=shape(title), surface="a coding session",
                    session=sid, at=at, example=err, error=str(m.get("errorDetail") or "") or err,
                ))
        cut = route.get("cut_off") if isinstance(route.get("cut_off"), dict) else None
        if (cut and int(cut.get("times") or 0) > 0) or m.get("truncated") is True:
            if cut and int(cut.get("times") or 0) > 0:
                times = int(cut.get("times") or 0)
                picked = "picked up again" if cut.get("recovered") else "not picked up again"
                words = f"cut off at the output limit {times} time{'s' if times != 1 else ''}, {picked}"
            else:
                words = "the reply's text was cut off at the output limit"
            occurrences.append(Occurrence(
                kind="cut_off", who=who, model=mw, tool="", shape=shape(words.split(",")[-1]),
                surface="a coding session", session=sid, at=at, example=words, error=words,
            ))
        fin = m.get("finish") if isinstance(m.get("finish"), dict) else {}
        rv = fin.get("review") if isinstance(fin.get("review"), dict) else None
        if rv:
            state = str(rv.get("state") or "")
            why = str(rv.get("why") or "")
            lw = why.lower()
            keep = (
                state == "failed"
                or (state == "not_run" and "low trust" not in lw and not lw.startswith("permission denied"))
                or (state == "stopped" and "took longer than" in lw)
            )
            if keep:
                member = str(rv.get("member") or "").strip()
                words = f"the team review {'failed' if state == 'failed' else 'did not run' if state == 'not_run' else 'did not finish'}"
                occurrences.append(Occurrence(
                    kind="review_not_run", who=member or "the team reviewer",
                    model=str(rv.get("model") or ""), tool="", shape=shape(f"{state} {why}"),
                    surface="a coding session's finish check", session=sid, at=at,
                    example=f"{words}: {why}" if why else words, error=why or words,
                ))

    out.sessions = len({t for t in threads_seen if t})
    out.problems = _group(occurrences, totals, pieces, reply_totals)
    return out


_PIECE_KINDS = frozenset({"helper_failed", "helper_step_limit", "context_too_small"})
#: Kinds whose heading names the tool ("A tool call that failed: edit_file").
_TITLED_BY_TOOL = frozenset({"tool_failed", "tool_not_given", "repeated_call", "ask_not_carried"})
_REPLY_KINDS = frozenset({"reply_failed", "patience_gave_up", "cut_off"})


def _group(occ: Iterable[Occurrence], totals: Counter, pieces: Counter, reply_totals: Counter) -> list[Problem]:
    groups: dict[tuple[str, str, str, str, str], list[Occurrence]] = {}
    for o in occ:
        groups.setdefault((o.kind, o.who, o.model, o.tool, o.shape), []).append(o)
    problems: list[Problem] = []
    never = datetime.min.replace(tzinfo=timezone.utc)
    for (kind, who, mw, tool, shp), items in groups.items():
        items.sort(key=lambda o: (o.at or never, o.session), reverse=True)
        stamps = [o.at for o in items if o.at is not None]
        sessions: list[str] = []
        for o in items:
            ref = short_id(o.session)
            if ref not in sessions:
                sessions.append(ref)
        newest = items[0]
        evidence = [{
            "at": when(o.at),
            "session": short_id(o.session),
            "tool": clean(o.tool, NAME_CHARS),
            "args": clean(o.args, ARGS_CHARS),
            "error": clean(o.error, ERROR_CHARS),
            "note": clean(o.note, ERROR_CHARS),
        } for o in items[:MAX_EVIDENCE]]
        programs: Counter[str] = Counter()
        for o in items:
            if o.program:
                programs[clean(o.program, NAME_CHARS)] += 1
        if kind in _PIECE_KINDS:
            out_of = (pieces.get((who, mw), 0), "pieces handed to this member")
        elif kind in _REPLY_KINDS:
            out_of = (reply_totals.get((who, mw), 0), "replies")
        elif tool:
            noun = "member" if who != "the lead" else "lead"
            out_of = (totals.get((who, mw, tool), 0), f"{clean(tool, NAME_CHARS)} calls by this {noun}")
        else:
            out_of = None
        if out_of is not None and out_of[0] < len(items):
            out_of = (len(items), out_of[1])
        problems.append(Problem(
            # A tool's name can come from an extension (an MCP server): shown
            # cleaned like any other value that did not come from Iron Jarvis.
            kind=kind, who=clean(who, NAME_CHARS), model=clean(mw, NAME_CHARS),
            tool=clean(tool, NAME_CHARS), shape=shp,
            surface=newest.surface, count=len(items), first=min(stamps) if stamps else None,
            last=max(stamps) if stamps else None, sessions=sessions[:MAX_SESSIONS_SHOWN],
            example=clean(newest.example, EXAMPLE_CHARS), evidence=evidence,
            programs=sorted(programs.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_PROGRAMS_SHOWN],
            out_of=out_of,
        ))
    problems.sort(key=lambda p: (-p.count, -(p.last or never).timestamp(), _KIND_ORDER.get(p.kind, 99),
                                 p.who, p.model, p.tool, p.shape))
    return problems


# --------------------------------------------------------------------------- #
# render — PURE
# --------------------------------------------------------------------------- #


def _window_words(days: int) -> str:
    return "the last day" if days == 1 else f"the last {days} days"


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def nothing_line(g: Gathered) -> str:
    where = (
        f"in this coding session (“{g.scope_title}”)" if g.scope_id and g.scope_title
        else "in this coding session" if g.scope_id
        else "in the coding sessions and their model team"
    )
    extra = f" (could not read: {', '.join(g.unreadable)})" if g.unreadable else ""
    return f"Nothing to report: Iron Jarvis ran into no problems {where} in {_window_words(g.days)}{extra}."


def _problem_block(n: int, p: Problem) -> str:
    table = KIND_TABLE.get(p.kind, KIND_TABLE["tool_failed"])
    title = table["title"]
    if p.tool and p.kind in _TITLED_BY_TOOL:
        title = f"{title}: {p.tool}"
    lines = [f"## {n}. {title}", ""]
    seen = f"- Seen {_plural(p.count, 'time')} (first {when(p.first)}, last {when(p.last)})"
    if p.out_of is not None:
        seen += f"; {p.count} of {p.out_of[0]} {p.out_of[1]} {'failed' if p.tool else 'went'} this way"
    lines.append(seen + ".")
    who = p.who + (f" ({p.model})" if p.model else "")
    where = f"- Where: {p.surface}; who: {who}"
    if p.tool:
        where += f"; tool: {p.tool}"
    if p.sessions:
        where += f"; sessions: {', '.join(p.sessions)}"
    lines.append(where + ".")
    if p.example:
        lines.append(f"- What happened: `{p.example}`")
    if p.programs:
        lines.append("- Programs it could not find: " + ", ".join(f"{name} ({c})" for name, c in p.programs) + ".")
    lines.append(f"- What was expected: {table['expected']}")
    owners = list(table["owners"])
    extra = TOOL_MODULES.get(p.tool) if p.kind == "tool_failed" else None
    if extra and extra not in owners:
        owners.insert(0, extra)
    lines.append("- Where in Iron Jarvis: " + ", ".join(f"`{o}`" for o in owners) + ".")
    lines.append("- Evidence:")
    for ev in p.evidence:
        parts = [ev["at"], ev["session"]]
        if ev["tool"]:
            parts.append(ev["tool"])
        if ev["args"]:
            parts.append(f"args: `{ev['args']}`")
        if ev["error"]:
            parts.append(f"error: `{ev['error']}`")
        elif ev.get("note"):
            parts.append(ev["note"])
        lines.append("  - " + " · ".join(parts))
    return "\n".join(lines)


def render(g: Gathered, *, max_problems: int = MAX_PROBLEMS, max_chars: int = REPORT_CHARS) -> str:
    """The Markdown (deterministic: the same records give the same text)."""
    if not g.problems:
        return nothing_line(g)
    facts = [
        f"- Iron Jarvis version: {g.version}",
        f"- Operating system: {g.os}",
        f"- Window: {_window_words(g.days)} ({when(g.since)} to {when(g.until)})",
    ]
    if g.scope_id:
        named = f"“{g.scope_title}” ({short_id(g.scope_id)})" if g.scope_title else short_id(g.scope_id)
        facts.append(f"- Coding session: {named}")
    facts.append(
        f"- Covers: {_plural(g.sessions, 'coding session')}, "
        f"{_plural(g.pieces, 'piece')} handed to helpers or teammates"
    )
    if g.low_trust_left_out:
        facts.append(
            f"- Left out: {_plural(g.low_trust_left_out, 'reply or helper run', 'replies or helper runs')} "
            "that ended at low trust (Iron Jarvis never reads their text)"
        )
    if g.unreadable:
        facts.append(f"- Could not read: {', '.join(g.unreadable)}")
    head = "\n".join([TITLE, "", INSTRUCTION, "", *facts])
    blocks: list[str] = []
    used = len(head)
    listed = 0
    reserve = 300  # room for the "left out" line
    for i, p in enumerate(g.problems[:max_problems], start=1):
        block = _problem_block(i, p)
        if used + 2 + len(block) + reserve > max_chars:
            break
        blocks.append(block)
        used += 2 + len(block)
        listed += 1
    left = len(g.problems) - listed
    tail: list[str] = []
    if left > 0:
        why = (
            f"the {max_problems} most frequent are listed"
            if listed == min(max_problems, len(g.problems)) and listed == max_problems
            else f"the report is capped at {max_chars:,} characters"
        )
        tail.append(f"Left out: {_plural(left, 'more problem')} ({why}).")
    return "\n\n".join([head, *blocks, *tail])


def build_report(engine: Any, *, days: Any = DEFAULT_DAYS, session: Any = "", now: "datetime | None" = None,
                 version: "str | None" = None, os_name: "str | None" = None) -> dict[str, Any]:
    """``{"text", "problems", "since", "until", "sessions"}`` — the route's
    answer. BLOCKING (call it off the loop); never raises except
    :class:`UnknownSession`. ``problems`` counts every problem found (the
    text lists at most :data:`MAX_PROBLEMS`)."""
    try:
        g = gather(engine, days=days, session=session, now=now, version=version, os_name=os_name)
    except UnknownSession:
        raise
    except Exception:  # noqa: BLE001 — gather never raises; a last guard
        log.warning("fix request failed", exc_info=True)
        until = (now or _now()).astimezone(timezone.utc)
        days_n = clamp_days(days)
        g = Gathered(version=str(version or "not known"), os=os_name or "not known", days=days_n,
                     since=until - timedelta(days=days_n), until=until,
                     unreadable=["Iron Jarvis's records"])
    return {
        "text": render(g),
        "problems": len(g.problems),
        "since": iso_z(g.since),
        "until": iso_z(g.until),
        "sessions": g.sessions,
    }


__all__ = [
    "COMMAND_TOOLS", "DEFAULT_DAYS", "Gathered", "HELPER_TOOLS", "INSTRUCTION", "KIND_TABLE",
    "MAX_DAYS", "MAX_PROBLEMS", "MIN_DAYS", "Occurrence", "Problem", "REPORT_CHARS", "TITLE",
    "TOOL_MODULES", "UnknownSession", "args_words", "bare_thread", "build_report", "clamp_days",
    "classify_call", "clean", "failure_example", "gather", "group_shape", "member_name", "nothing_line", "os_words",
    "program_of", "render", "shape", "short_id", "split_failure",
]
