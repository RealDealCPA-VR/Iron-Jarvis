"""Old "A helper …" lines in Build › Code, read in the words the session's own
record supports (v1.481.1; the owner: "address the risk").

From v1.418.0 to v1.477.1 every task Claude Code said had ended was written
to a session's log as "A helper finished / failed / was stopped: <summary>",
whatever the task was. v1.477.2 fixed the words for new lines
(:func:`.claude.task_ended_words`), but lines already in a log kept them, and a
line carries no field saying what the task was.

What the logs show (counted read-only in the owner's three sessions with such
lines, 618 of them):

* 523 name a SHELL COMMAND that ran in the foreground. Claude Code tracks any
  command that runs for two seconds as a task and ends it with the command's
  description (else its command line) as the summary. That command's own
  step row is in the same log, and shows how it ended.
* 24 are Claude Code's own sentence about a background command
  (``Background command "…" completed (exit code 0)``).
* 2 name a command whose own step says it went to the background.
* 1 is Claude Code's own sentence about a monitor (``Monitor "…" stream
  ended``; read from its program, 2.1.x).
* The other 68 are real helpers: their reports, or Claude Code's own words
  about an agent ("Agent terminated early…", "This agent's report was
  delivered…", ``Background agent "…" didn't finish…``).

So a line is changed only on evidence from the same record, the way a new
line would have been written:

* Claude Code's own background-command sentence is said as it is.
* Claude Code's own monitor sentence is "A background task …".
* A line whose summary is the description or command of a command step
  before it (the nearest one) is that command's:
  - the step's own result says it went to the background (one of Claude
    Code's five sentences for that, read from its program), or its input
    asked for it (``run_in_background``) → "A background command …";
  - the step's ending is in the list and says nothing about the background →
    the line is HIDDEN: the step row already shows how the command ended
    (what a new line does since v1.477.2);
  - the step's ending is not in the list → "A command …".
* Anything else is left exactly as it is: per the counts above, those are
  helpers.

Nothing on disk is changed: this runs where a view or a page of events is
served (:mod:`.history`), on a COPY of each changed line, with up to
``history.REWORD_CONTEXT`` events from just before what is served read for
their command steps (in the owner's logs, 4 of the 311 lines in a view named a
command just before it). A command further back than that is not found, and
its line keeps its old words. A line written by today's code reads "A helper
…" only for a real helper, so it is never matched to a command (its summary
is the helper's report, not a command's description).
"""

from __future__ import annotations

import re
from typing import Any

from . import words as W

#: The old template's three verbs (v1.418.0 .. v1.477.1:
#: ``SYS_TASK_DONE = {"completed": "A helper finished: {summary}", …}``),
#: which are also today's words for the same endings (``SYS_TASK_ENDED``).
OLD_PREFIX = "A helper "
_OLD_LINE = re.compile(r"A helper (finished|failed|was stopped): (.+)", re.S)

#: How Claude Code's command tool says, in its own result, that a command
#: went to the background (read from its program, 2.1.x): started there,
#: on another machine, moved by the user (Ctrl+B), moved so a message could
#: arrive, or moved at its timeout.
BACKGROUND_RESULT = re.compile(
    r"\bCommand (?:running in (?:the )?background\b"
    r"|was manually backgrounded\b"
    r"|was moved to the background\b"
    r"|did not complete within its \S{1,20} timeout and was moved to the background\b)"
)

#: The command's own input asked for the background (the step row keeps the
#: input as one JSON line, keys sorted: ``claude._input_json``).
RUN_IN_BACKGROUND = '"run_in_background": true'

#: Claude Code's own sentence for an ended monitor (its program: `Monitor "…"
#: ended without producing output` / `… stream ended` / `… script failed` /
#: `… stopped`).
MONITOR_SENTENCE = 'Monitor "'

#: A step row whose status is one of these is that step's ending.
_ENDED_STATUSES = frozenset({"done", "failed", "stopped", "interrupted", "error"})


def _norm(text: Any) -> str:
    return " ".join(str(text or "").split())


def _step_texts(ev: dict[str, Any]) -> list[str]:
    """What a command step's line could have been: its description (the
    title) and its command (the subtitle; the title itself when the command
    had no description, written "Run <command>")."""
    title = _norm(ev.get("title"))
    sub = _norm(ev.get("subtitle"))
    out = [t for t in (title, sub) if t]
    if title.startswith("Run ") and not sub:
        out.append(title[4:])
    return out


def _names(summary: str, text: str) -> bool:
    """``summary`` (cut at 300 characters) and a step's ``text`` (a title is
    cut at 90) are the same words — each cut ends in "…"."""
    if not text or not summary:
        return False
    if summary == text:
        return True
    if text.endswith("…"):
        head = text[:-1].rstrip()
        return bool(head) and summary.startswith(head)
    if summary.endswith("…"):
        head = summary[:-1].rstrip()
        return bool(head) and text.startswith(head)
    return False


def _old(ev: dict[str, Any]) -> re.Match[str] | None:
    if ev.get("type") != "notice":
        return None
    text = ev.get("text")
    if not isinstance(text, str) or not text.startswith(OLD_PREFIX):
        return None
    return _OLD_LINE.fullmatch(text)


def _line(subject: str, verb: str, summary: str) -> str:
    return W.SYS_TASK_DONE.format(who=W.SYS_TASK_SUBJECT[subject], ended=verb, summary=summary)


def reword(events: list[dict[str, Any]],
           context: list[dict[str, Any]] | tuple[()] = ()) -> tuple[list[dict[str, Any]], int]:
    """``events`` with each old "A helper …" line read in the words its record
    supports (see the module docstring), and how many lines were hidden.

    ``context`` is what the record holds just BEFORE ``events`` (a view or a
    page starts somewhere in a log): read for its command steps, never
    returned. Without it a line whose command sits just before the first
    event served keeps its old words.

    Never changes a dict it was given (a view's events can be the live log's
    own); a list with no old line is handed back as it is (same object)."""
    if not any(_old(ev) is not None for ev in events):
        return events, 0
    # What each command step's own rows say, wherever they are in the record
    # (its ending can come after the line that names it).
    background: set[str] = set()
    ended: set[str] = set()
    for ev in [*context, *events]:
        if ev.get("type") != "tool" or ev.get("kind") != "command":
            continue
        item = str(ev.get("item") or "")
        if not item:
            continue
        if str(ev.get("status") or "") in _ENDED_STATUSES:
            ended.add(item)
        out = ev.get("output")
        inp = ev.get("input")
        if (isinstance(out, str) and BACKGROUND_RESULT.search(out)) or (
            isinstance(inp, str) and RUN_IN_BACKGROUND in inp
        ):
            background.add(item)
    # Walk in order: a line is matched to the NEAREST command step before it.
    exact: dict[str, str] = {}
    cut: list[tuple[str, str]] = []

    def note_step(ev: dict[str, Any]) -> bool:
        if ev.get("type") != "tool" or ev.get("kind") != "command" or not ev.get("item"):
            return False
        item = str(ev["item"])
        for text in _step_texts(ev):
            if text.endswith("…"):
                cut.append((text, item))
            else:
                exact.pop(text, None)  # re-inserted: the newest stays last
                exact[text] = item
        return True

    for ev in context:
        note_step(ev)
    shown: list[dict[str, Any]] = []
    hidden = 0
    for ev in events:
        if note_step(ev):
            shown.append(ev)
            continue
        m = _old(ev)
        if m is None:
            shown.append(ev)
            continue
        verb, summary = m.group(1), _norm(m.group(2))
        if summary.startswith(W.SYS_TASK_OWN_SENTENCE):
            shown.append({**ev, "text": summary})
            continue
        if summary.startswith(MONITOR_SENTENCE):
            shown.append({**ev, "text": _line("background_task", verb, summary)})
            continue
        item = exact.get(summary)
        if item is None:
            item = next((i for t, i in reversed(cut) if _names(summary, t)), None)
        if item is None and summary.endswith("…"):
            item = next((i for t, i in reversed(list(exact.items())) if _names(summary, t)), None)
        if item is None:
            shown.append(ev)  # not shown to be a command: a helper, per the logs
            continue
        if item in background:
            shown.append({**ev, "text": _line("background_command", verb, summary)})
        elif item in ended:
            hidden += 1  # the command's own step row shows how it ended
        else:
            shown.append({**ev, "text": _line("command", verb, summary)})
    return shown, hidden
