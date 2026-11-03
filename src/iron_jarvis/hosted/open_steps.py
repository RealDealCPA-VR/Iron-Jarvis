"""A step is "running" only while the coding tool that runs it does (v1.427.2).

A user bug: after Claude Code's reply ended it kept working ON ITS OWN (helper
agents running tests, then a reply it started itself), and Iron Jarvis closed
in the middle. Those steps' rows said ``running`` in the session's log forever:
the boot reconcile only closed a reply the USER started (a ``user`` event with
no ``done``), and nothing closed a step when the tool's process ended with no
reply open. The Code view drew them with a spinner, days later.

Two ways a step is closed now, each with a ``tool`` row of status
``stopped`` ("did not finish"):

* LIVE — :class:`.service.HostedService` notes every step it emits
  (:func:`note`), and when the tool's process ends (it stopped, it was
  stopped, Iron Jarvis is closing) every step still open is closed
  (:func:`closing_rows`). A step outside any reply also gets ONE notice saying
  why (the reply's own error or notice says it for a reply's steps).
* AT BOOT — :func:`close_in_tail` reads a session log's tail the same way
  (``service.reconcile_cut_turns``) for every session the code host did not
  keep running.

Pure: no I/O, never raises on odd events."""

from __future__ import annotations

from typing import Any

from .events import make

#: A step's last status that means it is over.
FINISHED = frozenset({"done", "failed", "stopped"})
#: The status of a step that never finished (the tool stopped first).
STOPPED = "stopped"
#: The most open steps one session tracks (oldest dropped first: a tool that
#: never reports its steps' ends must not grow this forever).
MAX_OPEN = 500
#: What a closing row keeps of the step it closes (enough for a view that
#: trimmed the step's first row to still name it).
_KEEP = ("kind", "name", "title", "parent")


def note(open_steps: dict[str, dict[str, Any]], ev: Any) -> None:
    """Track one emitted event: a running step is open, a finished one is not."""
    if not isinstance(ev, dict) or ev.get("type") != "tool":
        return
    item = str(ev.get("item") or "")
    if not item:
        return
    if str(ev.get("status") or "") in FINISHED:
        open_steps.pop(item, None)
        return
    prev = open_steps.pop(item, None) or {}
    kept = {k: ev.get(k) or prev.get(k) for k in _KEEP if ev.get(k) or prev.get(k)}
    turn = prev.get("turn") or ev.get("turn")
    if turn:
        kept["turn"] = turn
    open_steps[item] = kept
    while len(open_steps) > MAX_OPEN:
        open_steps.pop(next(iter(open_steps)))


def closing_rows(open_steps: dict[str, dict[str, Any]], *, at: str = "") -> list[dict[str, Any]]:
    """One ``stopped`` row per open step, in the order they began. ``at``
    (the boot's case) = when the record ended, never the boot's own time."""
    rows = []
    for item, step in open_steps.items():
        row = make("tool", item=item, kind=step.get("kind") or "other", name=step.get("name") or "",
                   title=step.get("title") or "", input="", status=STOPPED)
        if step.get("parent"):
            row["parent"] = step["parent"]
        if at:
            row["at"] = at
        rows.append(row)
    return rows


def outside(open_steps: dict[str, dict[str, Any]]) -> int:
    """How many open steps the tool ran ON ITS OWN (no reply of the user's
    owned them). A reply's own steps need no notice: its end (the cut
    reply's error, the stop's notice) already says why."""
    return sum(1 for s in open_steps.values() if not s.get("turn"))


def harness_in(events: list[dict[str, Any]]) -> str:
    """The coding tool the log's newest ``session`` event names ("" = none)."""
    for ev in reversed(events):
        if isinstance(ev, dict) and ev.get("type") == "session" and ev.get("harness"):
            return str(ev["harness"])
    return ""


def close_in_tail(events: list[dict[str, Any]], *, label: str = "") -> list[dict[str, Any]]:
    """The rows that close every step a log tail leaves running: one
    ``stopped`` row each, then ONE notice when any of them ran outside a reply
    (:func:`outside`). [] = nothing open.

    ``label`` = the tool's name ("" = read from the tail's ``session`` event)."""
    from . import words as W

    open_steps: dict[str, dict[str, Any]] = {}
    last_at = ""
    for ev in events:
        if not isinstance(ev, dict):
            continue
        note(open_steps, ev)
        if isinstance(ev.get("at"), str) and ev["at"]:
            last_at = ev["at"]
    if not open_steps:
        return []
    rows = closing_rows(open_steps, at=last_at)
    loose = outside(open_steps)
    if loose:
        name = label or W.LABELS.get(harness_in(events), "") or "The coding tool"
        notice = make("notice", text=W.STEPS_CUT_RESTART.format(label=name, steps=W.steps_words(loose)))
        if last_at:
            notice["at"] = last_at
        rows.append(notice)
    return rows
