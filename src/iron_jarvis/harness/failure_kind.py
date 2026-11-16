"""WHOSE failure a refused or failed tool call is, and whether it was put right (v1.508.0).

The ledger of v1.500.2 counted every refused call the same way: an
``edit_file`` whose ``old`` text a model guessed, a probe a model sent on
purpose (``old: "PLACEHOLDER_TO_TRIGGER_ERROR_MESSAGE"``), ``git_read`` in a
folder with no git history, and a real fault in Iron Jarvis's own code. The
"Copy as a fix request" report (``agents/fix_request``) then handed all of
them to the agent that fixes Iron Jarvis as defects. This module tells them
apart, from the call's own words — pure, deterministic, never raises:

* :data:`HARNESS` — Iron Jarvis's own code failed (a Python traceback from
  the app, an internal error). A defect to fix.
* :data:`MODEL` — the model called the tool wrong: text that is not in the
  file, an ambiguous match, an argument the tool does not take, a helper not
  on the team, a command in the wrong shell's words. A defect only when the
  model did NOT put it right (:func:`recovery`).
* :data:`ENVIRONMENT` — the PC or the folder is not what the call assumed: no
  git repository, a file another program holds, a program that is not
  installed. Not the tool's fault and not the model's.
* :data:`PROBE` — an intentional negative test: a placeholder meant to fail,
  or an edit that replaces text with one character to see whether it exists.
* :data:`POLICY` — a person's or a rule's decision (declined, not armed, low
  trust). Never a defect.
* :data:`UNKNOWN` — none of the above: the report keeps treating it as a
  likely defect (fail closed — a fault is never hidden by a guess).
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

HARNESS = "harness"
MODEL = "model"
ENVIRONMENT = "environment"
PROBE = "probe"
POLICY = "policy"
UNKNOWN = "unclassified"
KINDS = (HARNESS, MODEL, ENVIRONMENT, PROBE, POLICY, UNKNOWN)

#: The tools that edit a file by quoting its text.
EDIT_TOOLS = frozenset({"edit_file", "multi_edit", "apply_patch", "notebook_edit"})
#: The tools that run a command line.
COMMAND_TOOLS = frozenset({"shell", "process_start", "bash", "run_command"})
#: The helper doors.
HELPER_TOOLS = frozenset({"delegate", "spawn_agent"})

_PROBE_WORDS = re.compile(
    r"(?:PLACEHOLDER|DOES_?NOT_?EXIST|NONEXISTENT|NON_EXISTENT|TRIGGER_?(?:THE_?)?ERROR|SHOULD_?FAIL"
    r"|INTENTIONAL(?:LY)?_?(?:FAIL|ERROR|MISSING)|NO_?SUCH_?TEXT|NOT_?IN_?(?:THE_?)?FILE)",
    re.IGNORECASE,
)
_POLICY = re.compile(
    r"permission denied by|denied by policy|rejected by user|you declined|declined this call"
    r"|\bnot armed\b|low trust|needs your ok|stopped by (?:you|the user)|emergency stop is on"
    r"|the approval request timed out",
    re.IGNORECASE,
)
_ENVIRONMENT = re.compile(
    r"not in a git repository|not a git repository"
    r"|device or resource busy|being used by another process|resource temporarily unavailable"
    r"|is not recognized as an internal or external command|command not found"
    r"|no space left on device|docker (?:is )?unavailable|network is unreachable"
    r"|could not resolve host|connection refused",
    re.IGNORECASE,
)
_MODEL = re.compile(
    r"`old` text was not found|`old` text occurs \d+ times|`old` is empty|`old` and `new` are the same"
    r"|^NOT RUN: the last \d+ edits|is not on this session's team|^unknown agent"
    r"|This shell is Git for Windows' bash|escapes the session workspace|is outside the"
    r"|is required|missing required|unexpected keyword|not a valid|invalid (?:argument|value|json)"
    r"|has already failed \d+ times|repeated-failure breaker|^`\w+` (?:is|must be) "
    r"|SyntaxError|no such file or directory|does not exist",
    re.IGNORECASE | re.MULTILINE,
)
#: A Python traceback FROM IRON JARVIS (a frame in its own package). A
#: traceback whose frames are the model's own script (``<repl>``) is the
#: model's code failing.
_APP_TRACE = re.compile(r'File "[^"\n]*iron_jarvis[\\/][^"\n]*", line \d+', re.IGNORECASE)
_REPL_FRAME = re.compile(r'File "<repl>', re.IGNORECASE)
_INTERNAL = re.compile(r"internal error|unexpected error in iron jarvis|this is a bug", re.IGNORECASE)


def _args(args: Any) -> dict[str, Any]:
    if isinstance(args, dict):
        return args
    if isinstance(args, str) and args.strip():
        try:
            got = json.loads(args)
        except (TypeError, ValueError):
            return {}
        return got if isinstance(got, dict) else {}
    return {}


def _edits(tool: str, a: dict[str, Any]) -> list[tuple[str, str]]:
    """``(old, new)`` of an edit call (one, or each of ``multi_edit``'s)."""
    if tool == "multi_edit" and isinstance(a.get("edits"), list):
        return [(str(e.get("old", e.get("old_string", ""))), str(e.get("new", e.get("new_string", ""))))
                for e in a["edits"] if isinstance(e, dict)]
    if "old" in a or "old_string" in a:
        return [(str(a.get("old", a.get("old_string", ""))), str(a.get("new", a.get("new_string", ""))))]
    return []


def is_probe(tool: str, args: Any) -> bool:
    """An intentional negative test: placeholder words in the text or path a
    call names, or an edit that swaps text for a single character (a model
    checking whether the text is there). Pure."""
    tool = str(tool or "")
    a = _args(args)
    if tool in EDIT_TOOLS:
        for old, new in _edits(tool, a):
            if _PROBE_WORDS.search(old):
                return True
            # The ledger's probes swapped real-looking text for "x" / "  x"
            # (and restored it after). An edit that DELETES text (new "")
            # is ordinary work, never a probe.
            if len(old.strip()) > 1 and new.strip().lower() == "x":
                return True
    for key in ("path", "file_path", "pattern"):
        if isinstance(a.get(key), str) and _PROBE_WORDS.search(a[key]):
            return True
    return False


def classify(tool: str, args: Any, error: Any, data: Any = None) -> str:
    """The :data:`KINDS` word for ONE failed call. Pure, never raises."""
    try:
        tool = str(tool or "")
        text = str(error or "")
        d = data if isinstance(data, dict) else {}
        if _POLICY.search(text):
            return POLICY
        if is_probe(tool, args):
            return PROBE
        if d.get("not_a_repo"):
            return ENVIRONMENT
        if _APP_TRACE.search(text) and not _REPL_FRAME.search(text):
            return HARNESS
        if _INTERNAL.search(text):
            return HARNESS
        if _ENVIRONMENT.search(text):
            return ENVIRONMENT
        if d.get("gated") or _MODEL.search(text) or _REPL_FRAME.search(text):
            return MODEL
    except Exception:  # noqa: BLE001
        return UNKNOWN
    return UNKNOWN


def target_of(tool: str, args: Any) -> str:
    """What a call acts ON, for "the same thing again": the file for a file
    tool, the program for a command, the helper for a hand-off, "git" for
    git_read. "" when nothing names it. Pure."""
    tool = str(tool or "")
    a = _args(args)
    for key in ("path", "file_path", "notebook_path"):
        if isinstance(a.get(key), str) and a[key].strip():
            return a[key].strip().replace("\\", "/").lower()
    if tool in COMMAND_TOOLS and isinstance(a.get("command"), str):
        # The first program of the line that is not a `cd` or a VAR=value.
        for segment in re.split(r"&&|\|\||;|\|", a["command"]):
            words = [w for w in segment.strip().split() if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w)]
            if words and words[0] != "cd":
                return re.split(r"[\\/]", words[0].strip("\"'"))[-1].lower()
        return ""
    if tool in HELPER_TOOLS:
        for key in ("agent_type", "agent", "role"):
            if isinstance(a.get(key), str) and a[key].strip():
                return "helper"
    if tool == "git_read":
        return "git"
    return ""


def recovery(calls: Iterable[tuple[str, Any, bool, Any]]) -> dict[str, Any]:
    """Recovery over ONE session's calls in order — ``(tool, args, ok,
    error)`` each: how many failed, by kind, and how many of the MODEL's
    mistakes were put right by a later successful call of the same tool on
    the same target. Pure."""
    rows = list(calls)
    by_kind: dict[str, int] = {k: 0 for k in KINDS}
    mistakes = recovered = 0
    for i, (tool, args, ok, error) in enumerate(rows):
        if ok:
            continue
        kind = classify(tool, args, error)
        by_kind[kind] += 1
        if kind != MODEL:
            continue
        mistakes += 1
        target = target_of(tool, args)
        if any(t == tool and k and target_of(t, a) == target for t, a, k, _e in rows[i + 1:]):
            recovered += 1
    return {"failed": sum(by_kind.values()), "by_kind": by_kind, "model_mistakes": mistakes,
            "recovered": recovered, "unrecovered": mistakes - recovered}


__all__ = [
    "ENVIRONMENT", "HARNESS", "KINDS", "MODEL", "POLICY", "PROBE", "UNKNOWN", "classify", "is_probe",
    "recovery", "target_of",
]
