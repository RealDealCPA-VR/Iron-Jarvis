"""A JOB YOU REPEAT CAN RUN WITH NO MODEL (the intelligence plan, release 11,
on top of next leaps 19-b).

19-b offers a schedule that asks a model to do a repeated task again each
time. When the record shows the three times were done in the SAME STEPS,
this module turns them into FIXED STEPS instead: a workflow of tool steps
(no model), with the one value that changed each time as a named input.

HOW "THE SAME STEPS" IS DECIDED (:func:`plan`, each rule pinned in
``tests/test_intel_11_fixed_steps.py``): the ledger rows of each time (the
calls that RAN and were not refused, in order; a rehearsal's rows are never
read) are the steps. They are the same when every time made the same number
of calls, to the same tools, in the same order, with the same argument
names. A time with an Undo in it is not the same (what it did was taken
back). Every tool must be in :data:`FIXED_TOOLS`: tools that never ask a
model and never reach the network (a file it reads must be a plain-text
kind, :data:`TEXT_KINDS`, so no scanned page can be sent to a model to be
read).

HOW THE INPUT IS FOUND: every argument that had the same value each time is
kept as it was. Arguments whose value DIFFERED must all be file paths that,
within one time, held one and the same file: that file is the ONE input,
``file``, asked in the owner's words as "Which file?". It is filled at each
run by code, never by a model: the NEWEST file of that kind in that folder
(:func:`resolve_inputs`), and only when each of the three times used the
file that was newest when it was asked for (:func:`newest_each_time`, read
from the disk). More than one thing that changed, a value that is not a
file, or files that were not the newest each time: no fixed steps, and
19-b's offer is made as before.

THE LEAK RULE (:func:`leak_problem`): the saved steps carry no value from
any one time's work that the others did not share (that value is the input,
and only its folder and kind are kept); a value the three times shared is
kept only when ALL of these hold:

* the three times were no client's work (the client wall's owner of each is
  "no client"): a client's job is never saved as fixed steps, so no client's
  folder, file name or figure reaches the Workflows page;
* release 10's checks pass on the steps' whole text: nothing the mask would
  change and no contact detail (``draft_check.secret_shaped``), nothing read
  as an instruction, and no client's name in ANY case
  (``draft_check.client_named_any_case``; a client list that cannot be read
  counts as naming one);
* every path is inside the chats' one folder and is kept RELATIVE to it.

THE SENTENCES the offer says are true by construction: "uses no model" only
because every step is a :data:`FIXED_TOOLS` tool (and a step that asks a
model is never one); "works offline" only when the folder is on this PC
(:func:`folder_is_local`: never a network share or a mapped network drive),
since no fixed tool reaches the network; "Nothing was changed" only from
06-b's rehearsal of these very steps.

Every function that reads the disk or the database is BLOCKING.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

#: The one input a set of fixed steps may have, and its question.
INPUT_NAME = "file"
QUESTION = "Which file?"
#: The kind of an input rule: the newest file of a kind in a folder.
NEWEST = "newest"

#: File kinds a fixed step may READ with a tool that would otherwise send an
#: unreadable scan to a model (``read_file``, ``convert_document``): plain
#: text only. Never a PDF, a picture or an Office file (a scan named .xlsx is
#: found by its content and read by a model, ``documents/ocr.is_pdf_file``).
TEXT_KINDS = frozenset({"csv", "tsv", "txt", "md", "json", "xml", "yaml", "yml", "log", "ini", "toml", "html",
                        "htm"})


@dataclass(frozen=True)
class ToolRule:
    """What a fixed step of one tool may hold: the argument names that are
    paths, which of them it READS (and must then be :data:`TEXT_KINDS` when
    ``text_only``), and the one it WRITES (checked after the step:
    ``expect.files``, when its result names the file it wrote)."""

    paths: tuple[str, ...] = ()
    reads: tuple[str, ...] = ()
    text_only: bool = False
    writes: str = ""


#: The tools a fixed step may use: each runs on this PC, asks no model and
#: reaches no network. A tool not listed here (a web page, an app, mail, a
#: program, a model, a reader that could send a scan to a model) makes the
#: steps not fixed.
FIXED_TOOLS: dict[str, ToolRule] = {
    "read_file": ToolRule(paths=("path",), reads=("path",), text_only=True),
    "list_folder": ToolRule(paths=("path",)),
    "excel_read": ToolRule(paths=("path",), reads=("path",)),
    "write_file": ToolRule(paths=("path",), writes="path"),
    "write_document": ToolRule(paths=("path",), writes="path"),
    "convert_document": ToolRule(paths=("source", "target"), reads=("source",), text_only=True, writes="target"),
    "calculate": ToolRule(),
}

#: The app's own control keys a ledger row may carry (``core/grants``): never
#: part of a step.
_CONTROL = frozenset({"_store_as", "_isolate"})
#: Files a folder is looked through for, at most (the newest-file rule).
LIST_MAX = 5000
#: A file used at a time may have been written up to this long after the
#: request (a download that finished as the chat began).
SLACK_S = 120.0


class NotFixed(Exception):
    """Why these times cannot be fixed steps, in words for the record."""


class InputMissing(Exception):
    """An input that cannot be filled now, in words for a person."""


@dataclass
class Plan:
    """Fixed steps: the workflow's steps (``step_to_dict`` shape), its
    inputs' rules (``{"file": {"newest": {"in": "exports", "kind": "csv"}}}``
    or ``{}``), the folder they work in, and whether they work offline."""

    steps: list[dict[str, Any]]
    inputs: dict[str, Any] = field(default_factory=dict)
    folder: str = ""
    offline: bool = False


# --------------------------------------------------------------------------- #
# paths (pure)
# --------------------------------------------------------------------------- #
def _kind(path: str) -> str:
    name = PurePosixPath(str(path or "")).name
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def relative(folder: str, value: Any) -> str:
    """``value`` (a path as the call gave it: absolute, or relative to the
    chat's folder) as a POSIX path relative to ``folder``. RAISES
    :class:`NotFixed` for one outside the folder, or not a path. Pure (no
    disk)."""
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise NotFixed("a step's path was not a path")
    root = os.path.normpath(os.path.abspath(folder))
    raw = value.strip()
    full = os.path.normpath(raw if os.path.isabs(raw) else os.path.join(root, raw))
    try:
        rel = os.path.relpath(full, root)
    except ValueError:  # another drive
        raise NotFixed("a step used a file outside the chats' folder") from None
    if rel == os.curdir:
        return "."
    if rel.startswith(os.pardir + os.sep) or rel == os.pardir or os.path.isabs(rel):
        raise NotFixed("a step used a file outside the chats' folder")
    return PurePosixPath(*Path(rel).parts).as_posix()


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(a) == os.path.normcase(b)


# --------------------------------------------------------------------------- #
# the calls of one time (pure)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Call:
    tool: str
    args: dict[str, Any]


def calls_of(rows: Iterable[Any]) -> list[Call]:
    """The calls one time made, in order: every ledger row that ran and was
    not refused (``ok``). RAISES :class:`NotFixed` when one of them was
    undone or is an Undo (what it did was taken back), uses a tool that is
    not a :data:`FIXED_TOOLS` tool, or carries an argument of the app's own
    that is not a step's. Pure."""
    out: list[Call] = []
    for row in rows or ():
        if getattr(row, "rehearsed", False):
            continue
        if getattr(row, "undo_of", None) or getattr(row, "undone_at", None):
            raise NotFixed("one of the times was undone")
        if not bool(getattr(row, "ok", False)):
            continue
        tool = str(getattr(row, "tool", "") or "")
        if tool not in FIXED_TOOLS:
            raise NotFixed(f"a step used {tool or 'a tool'}, which is not one Iron Jarvis can repeat with no model")
        try:
            args = json.loads(getattr(row, "args_json", "") or "{}")
        except (TypeError, ValueError):
            raise NotFixed("a step's record could not be read") from None
        if not isinstance(args, dict):
            raise NotFixed("a step's record could not be read")
        kept: dict[str, Any] = {}
        for k, v in args.items():
            key = str(k)
            if key in _CONTROL:
                continue
            if key.startswith("_"):
                raise NotFixed("a step carried a value kept by a script")
            kept[key] = v
        out.append(Call(tool, kept))
    if not out:
        raise NotFixed("no step was recorded")
    return out


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


# --------------------------------------------------------------------------- #
# the plan
# --------------------------------------------------------------------------- #
def plan(platform: Any, times: list[tuple[float, list[Any]]], folder: str, *, owners: Iterable[str] = ()) -> Plan:
    """The fixed steps three (or more) times make, or :class:`NotFixed` in
    words (module docstring). ``times``: ``(when it was asked for, its ledger
    rows)``, any order; ``folder``: the one folder the chats worked in;
    ``owners``: the client wall's owner of each time ("" = no client).
    BLOCKING (the disk, the client list, the permissions)."""
    if any(str(o or "") for o in owners):
        raise NotFixed("it was a client's work")
    root = str(folder or "").strip()
    if not root or not os.path.isdir(root):
        raise NotFixed("the chats did not all work in one folder")
    if len(times) < 2:
        raise NotFixed("there were not enough times")
    ordered = sorted(times, key=lambda t: t[0])
    each = [calls_of(rows) for _start, rows in ordered]
    shape = [(c.tool, tuple(sorted(c.args))) for c in each[0]]
    for calls in each[1:]:
        if [(c.tool, tuple(sorted(c.args))) for c in calls] != shape:
            raise NotFixed("the steps were not the same each time")
    # Paths relative to the folder (so "C:/Work/a.csv" and "a.csv" agree).
    norm: list[list[dict[str, Any]]] = []
    for calls in each:
        mine = []
        for c in calls:
            rule = FIXED_TOOLS[c.tool]
            args = dict(c.args)
            for key in rule.paths:
                if key in args:
                    args[key] = relative(root, args[key])
            mine.append(args)
        norm.append(mine)
    # What stayed the same, and what changed.
    steps_args: list[dict[str, Any]] = []
    varying: list[tuple[int, str]] = []
    for i, (tool, _keys) in enumerate(shape):
        kept: dict[str, Any] = {}
        for key in sorted(norm[0][i]):
            values = [n[i][key] for n in norm]
            if key in FIXED_TOOLS[tool].paths:
                same = all(_same_path(v, values[0]) for v in values)
            else:
                same = all(_canon(v) == _canon(values[0]) for v in values)
            if same:
                kept[key] = values[0]
            else:
                if key not in FIXED_TOOLS[tool].paths:
                    raise NotFixed("something that changed each time was not a file")
                varying.append((i, key))
                kept[key] = "{{" + INPUT_NAME + "}}"
        steps_args.append(kept)
    inputs: dict[str, Any] = {}
    if varying:
        per_time: list[str] = []
        for n in norm:
            vals = [n[i][k] for i, k in varying]
            if any(not _same_path(v, vals[0]) for v in vals):
                raise NotFixed("more than one thing changed each time")
            per_time.append(vals[0])
        parents = {os.path.normcase(str(PurePosixPath(v).parent)) for v in per_time}
        kinds = {_kind(v) for v in per_time}
        if len(parents) != 1 or len(kinds) != 1 or not next(iter(kinds)):
            raise NotFixed("the file that changed was not the same kind of file in one folder")
        kind = next(iter(kinds))
        sub = str(PurePosixPath(per_time[0]).parent)
        if not newest_each_time(root, sub, kind, [(start, v) for (start, _rows), v in zip(ordered, per_time)]):
            raise NotFixed("the file that changed was not the newest of its kind each time")
        inputs = {INPUT_NAME: {NEWEST: {"in": sub, "kind": kind}}}
    # Read only plain text where a scan could reach a model.
    for i, (tool, _keys) in enumerate(shape):
        rule = FIXED_TOOLS[tool]
        if not rule.text_only:
            continue
        for key in rule.reads:
            if key not in steps_args[i]:
                continue
            value = steps_args[i][key]
            kind = inputs[INPUT_NAME][NEWEST]["kind"] if value == "{{" + INPUT_NAME + "}}" else _kind(value)
            if kind not in TEXT_KINDS:
                raise NotFixed("a step read a kind of file that may need a model to read")
    steps: list[dict[str, Any]] = []
    for i, (tool, _keys) in enumerate(shape):
        step: dict[str, Any] = {
            "name": f"Step {i + 1}", "kind": "tool", "tool": tool, "args": steps_args[i],
            "on_failure": "halt",
        }
        target = FIXED_TOOLS[tool].writes
        wrote = steps_args[i].get(target) if target else None
        if isinstance(wrote, str) and "{{" not in wrote:
            # The step's own check (contract 8): the file it writes is there.
            step["expect"] = {"files": [wrote]}
        steps.append(step)
    problem = leak_problem(getattr(platform, "engine", None), steps)
    if problem:
        raise NotFixed(problem)
    asks = asks_first(platform, steps)
    if asks:
        raise NotFixed(asks)
    return Plan(steps=steps, inputs=inputs, folder=root, offline=folder_is_local(root))


def leak_problem(engine: Any, steps: list[dict[str, Any]]) -> str:
    """Why these steps may not be saved (the leak rule), or "". Release 10's
    checks on the steps' whole text: a secret-shaped value or a contact
    detail, a line read as an instruction, a client's name in any case.
    BLOCKING (the client list)."""
    from ..skills import draft_check as C
    from ..skills.learning import names_a_client

    text = json.dumps(steps, ensure_ascii=False, sort_keys=True)
    for value in _strings(steps):
        if C.secret_shaped(value):
            return "a step held something that looks like a secret or a contact detail"
    if C.read_as_instruction(text):
        return "a step held words that read as an instruction"
    if names_a_client(engine, text) or C.client_named_any_case(engine, text):
        return "a step named a client"
    return ""


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def asks_first(platform: Any, steps: list[dict[str, Any]]) -> str:
    """Would any step's tool ask the owner first (its permission is not
    Allow)? A workflow's tool step cannot ask, so such steps are not saved
    to run by themselves (each run would be refused). "" when none would.
    BLOCKING."""
    reg = getattr(platform, "registry", None)
    perms = getattr(platform, "permissions", None)
    if reg is None or perms is None:
        return "the tools could not be checked"
    for step in steps:
        tool = reg.get(step["tool"])
        if tool is None:
            return f"the tool {step['tool']} is not on this install"
        try:
            ok = bool(perms.authorize(tool.perm_key(), dict(step.get("args") or {}), None).allowed)
        except Exception:  # noqa: BLE001 — unsure: it would ask
            ok = False
        if not ok:
            return "a step asks you before it runs each time, so it cannot run by itself"
    return ""


# --------------------------------------------------------------------------- #
# the input: the newest file of a kind in a folder (BLOCKING)
# --------------------------------------------------------------------------- #
def _files_of(root: str, sub: str, kind: str) -> list[tuple[float, str]]:
    """``(modified, path relative to root)`` of the files of ``kind`` in
    ``root/sub`` (not in folders inside it), at most :data:`LIST_MAX`."""
    where = Path(root) / sub
    out: list[tuple[float, str]] = []
    try:
        with os.scandir(where) as it:
            for n, entry in enumerate(it):
                if n >= LIST_MAX:
                    break
                if not entry.is_file() or _kind(entry.name) != kind:
                    continue
                rel = entry.name if sub in ("", ".") else f"{sub}/{entry.name}"
                out.append((entry.stat().st_mtime, rel))
    except OSError:
        return []
    return out


def newest_each_time(root: str, sub: str, kind: str, used: list[tuple[float, str]]) -> bool:
    """Was each file used the NEWEST file of its kind in that folder when it
    was asked for? Read from the disk now: each file is still there, was
    there by then (written no later than :data:`SLACK_S` after the request),
    and no other file of that kind was written between it and the request.
    A file that is gone, or was changed since, cannot be shown: False."""
    files = _files_of(root, sub, kind)
    by_name = {os.path.normcase(rel): mtime for mtime, rel in files}
    for start, rel in used:
        mtime = by_name.get(os.path.normcase(rel))
        if mtime is None or mtime > start + SLACK_S:
            return False
        if any(mtime < other <= start + SLACK_S and not _same_path(name, rel) for other, name in files):
            return False
    return True


def check_inputs(inputs: Any) -> str:
    """Why an inputs rule may not be saved on a schedule, or "" (the add-time
    rule, ``scheduling/checks.check_payload``). Pure."""
    if inputs in (None, {}):
        return ""
    if not isinstance(inputs, dict):
        return "inputs must name each input and how it is filled"
    for name, rule in inputs.items():
        if not isinstance(name, str) or not name.strip():
            return "an input needs a name"
        if isinstance(rule, str):
            continue
        newest = rule.get(NEWEST) if isinstance(rule, dict) else None
        if not isinstance(newest, dict):
            return f"the input “{name}” says neither a value nor which file to use"
        sub, kind = newest.get("in"), newest.get("kind")
        if not isinstance(sub, str) or not isinstance(kind, str) or not kind.isalnum():
            return f"the input “{name}” does not say which folder and which kind of file"
        if os.path.isabs(sub) or ".." in PurePosixPath(sub).parts:
            return f"the input “{name}” names a folder outside the workflow's own"
    return ""


def resolve_inputs(inputs: Any, root: "str | None") -> dict[str, str]:
    """The values of a schedule's inputs NOW, filled by code (no model):
    a plain value as it is; ``{"newest": {"in", "kind"}}`` the newest file
    of that kind in that folder of the workflow's folder ``root``. RAISES
    :class:`InputMissing` in words when one cannot be filled. BLOCKING."""
    out: dict[str, str] = {}
    if not isinstance(inputs, dict):
        return out
    problem = check_inputs(inputs)
    if problem:
        raise InputMissing(problem)
    for name, rule in inputs.items():
        if isinstance(rule, str):
            out[name] = rule
            continue
        newest = rule[NEWEST]
        sub, kind = str(newest["in"]), str(newest["kind"]).lower()
        if not root or not os.path.isdir(root):
            raise InputMissing("the folder it works in is not there, so it did not run")
        files = _files_of(root, sub, kind)
        if not files:
            where = "its folder" if sub in ("", ".") else f"“{sub}”"
            raise InputMissing(f"no .{kind} file is in {where}, so it did not run")
        files.sort(key=lambda t: (t[0], t[1]))
        out[name] = files[-1][1]
    return out


def input_words(inputs: Any) -> str:
    """"Which file? Each time: the newest .csv file in “exports”." "" when
    there is no input. Pure."""
    if not isinstance(inputs, dict) or INPUT_NAME not in inputs:
        return ""
    rule = inputs[INPUT_NAME]
    newest = rule.get(NEWEST) if isinstance(rule, dict) else None
    if not isinstance(newest, dict):
        return ""
    sub = str(newest.get("in") or "")
    where = "its folder" if sub in ("", ".") else f"“{sub}” in its folder"
    return f"{QUESTION} Each time: the newest .{newest.get('kind')} file in {where}."


# --------------------------------------------------------------------------- #
# the folder: on this PC?
# --------------------------------------------------------------------------- #
def folder_is_local(folder: str) -> bool:
    """Is ``folder`` on this PC (so the steps need no network)? Never a
    share (``\\\\host\\share``) or, on Windows, a mapped network drive.
    Unsure = False (the offer then does not say "works offline")."""
    text = str(folder or "")
    if not text or text.startswith(("\\\\", "//")):
        return False
    if os.name != "nt":
        return True
    try:
        import ctypes

        drive = os.path.splitdrive(os.path.abspath(text))[0]
        if not drive:
            return False
        kind = ctypes.windll.kernel32.GetDriveTypeW(drive + "\\")  # type: ignore[attr-defined]
        # 2 removable, 3 fixed, 6 RAM disk; 4 = remote, 0/1 = unknown.
        return kind in (2, 3, 6)
    except Exception:  # noqa: BLE001 — unsure: not said
        return False


def step_lines(steps: list[dict[str, Any]], inputs: Any) -> str:
    """The steps in plain words, one per line ("1. read file: the file you
    pick each time", "2. write document: summary.xlsx"). Pure."""
    try:
        from ..agents.mission import tool_own_name
    except Exception:  # noqa: BLE001 — wording only
        def tool_own_name(t: str) -> str:  # type: ignore[misc]
            return " ".join(str(t).replace("_", " ").split())
    tag = "{{" + INPUT_NAME + "}}"
    lines = []
    for n, step in enumerate(steps, 1):
        rule = FIXED_TOOLS.get(str(step.get("tool") or ""), ToolRule())
        args = step.get("args") or {}
        said = []
        for key in rule.paths:
            if key in args:
                said.append("the newest file" if args[key] == tag else str(args[key]))
        what = f": {', '.join(said)}" if said else ""
        lines.append(f"{n}. {tool_own_name(str(step.get('tool') or ''))}{what}")
    return "\n".join(lines)


__all__ = [
    "FIXED_TOOLS", "INPUT_NAME", "InputMissing", "NEWEST", "NotFixed", "Plan", "QUESTION", "TEXT_KINDS",
    "asks_first", "calls_of", "check_inputs", "folder_is_local", "input_words", "leak_problem",
    "newest_each_time", "plan", "relative", "resolve_inputs", "step_lines",
]
