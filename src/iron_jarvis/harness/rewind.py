"""REWIND a Build session to before one of its turns (v1.370.0, IJ harness
plan Phase 7).

Rewinding TO a turn means: the files the session's turns changed from that
turn on are put back the way they were before it, and the conversation is
cut back to before that turn's question. Later turns are rewound too — a
rewind is to a POINT, never a hole in the middle.

THE FILES GO BACK THROUGH THE UNDO JOURNAL, nothing else. Every reversible
file write a build turn makes leaves a ``ToolInvocation`` + ``UndoJournal``
pair under the session's own ``build:<thread id>``; grouping them PER TURN is
a time window — every row recorded at or after the turn's question (its
``at`` stamp, the daemon's own clock, written before the turn starts) belongs
to that turn or a later one, and only one turn of a session runs at a time.
Each action is reverted by the SAME code ``POST /undo/{id}`` runs
(``routes/undo.perform_undo``: the tool's own ``revert``, the permission
engine, the fs policy, the "changed since" guard, the undo written into the
ledger as its own row), newest first — the only order that composes.

NEVER A SILENT PARTIAL REWIND. :func:`plan` decides, BEFORE anything is
touched, whether every action can be reversed: it simulates the newest-first
replay over the files' CURRENT bytes with each action's own "changed since"
rule (the hash its revert will check, in that action's own hash mode), so a
file the user edited after a turn — or a file whose saved copy is gone — is
named in ``problems`` and NOTHING is touched. Only a clean plan is performed.
(Should the disk change between the plan and the revert — a window of
milliseconds — the route stops at the first refusal and says plainly what was
already put back; the conversation is then left as it was.)

WHAT IS NOT REWOUND, said in the answer every time: commands (``shell``, the
process tools) and anything else with no saved inverse — their effects stay
(``commands``); programs a rewound turn started keep running; tools granted
"for this conversation" stay granted; the usage spent stays spent.

BLOCKING (SQLite reads, file reads): the route calls :func:`plan` through
``asyncio.to_thread``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: Ledger rows read for one rewind (a session's turns write a handful each;
#: this only stops a corrupt clock from walking the whole history).
MAX_ROWS = 5000
#: A command's words kept for the answer.
WHAT_CHARS = 160

#: Journal kinds that name files (``routes/chat_changes``' set + renames).
_FILE_KINDS = frozenset({"file_restore", "file_delete", "files_delete", "files_patch", "file_rename"})
#: Kinds ``routes/undo.perform_undo`` reverses WITHOUT asking the tool's
#: permission (it restores what the user had).
_NO_TOOL_KINDS = frozenset({"setting_restore", "secret_restore", "record_restore"})

#: ``apply_patch``'s own words for a patch that changed nothing (its capture
#: returns no copy then, and there is nothing to put back).
NO_CHANGE_NOTE = "(the patch left every file as it was)"

NOT_A_TURN = "That point is not one of this session's turns."
#: A file a rewound turn made that is already gone: nothing to put back.
ALREADY_GONE = "already gone"
NO_TIME = "Iron Jarvis cannot tell when that turn started, so it cannot rewind to it."


class RewindError(Exception):
    """A plain sentence; ``status`` is the HTTP status the route answers."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class RewindPlan:
    to: str
    question: str
    #: The turns being rewound (the target and every later one), oldest first.
    turns: list[str]
    #: Transcript lines kept (everything before the target's question).
    keep: int
    cut_at: datetime
    #: Action ids to revert, NEWEST FIRST.
    actions: list[str] = field(default_factory=list)
    #: One row per file the rewind puts back: {path, rel, change}.
    files: list[dict[str, Any]] = field(default_factory=list)
    #: What would stop the rewind: {path, reason} (or {tool, reason}).
    problems: list[dict[str, Any]] = field(default_factory=list)
    #: What ran in those turns and is NOT undone: {tool, what}.
    commands: list[dict[str, Any]] = field(default_factory=list)
    #: Changes already undone one by one (the reply's Undo) — skipped.
    already_undone: int = 0
    #: action id -> the files its revert puts back (rel), for a partial answer.
    action_files: dict[str, list[str]] = field(default_factory=dict)
    #: v1.377.0: what rode the question besides its words — the pictures and
    #: files it carried, the folder files picked with "@" and the skill picked
    #: with "/" — so a rewind puts them back in the box WITH the words.
    attachments: list[str] = field(default_factory=list)
    picked_files: list[str] = field(default_factory=list)
    skill: str = ""

    def view(self) -> dict[str, Any]:
        return {
            "to": self.to,
            "question": self.question,
            "attachments": list(self.attachments),
            "picked_files": list(self.picked_files),
            "skill": self.skill,
            "turns": list(self.turns),
            "files": list(self.files),
            "problems": list(self.problems),
            "commands": list(self.commands),
            "already_undone": self.already_undone,
            "can_rewind": not self.problems,
        }


# --------------------------------------------------------------------------- #
# the conversation side
# --------------------------------------------------------------------------- #


def _messages(row: Any) -> list[dict[str, Any]]:
    try:
        data = json.loads(getattr(row, "messages_json", "") or "[]")
    except (TypeError, ValueError):
        data = []
    return [m for m in data if isinstance(m, dict)] if isinstance(data, list) else []


#: At most this many paths of a question go back in the box (the turn route's
#: own cap per list, ``BuildTurnBody``).
MAX_QUESTION_PATHS = 20


def _paths(value: Any) -> list[str]:
    """A question line's saved path list: strings, de-duplicated, bounded."""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for v in value:
        if isinstance(v, str) and v.strip() and v.strip() not in out:
            out.append(v.strip())
    return out[:MAX_QUESTION_PATHS]


def _is_question(m: dict[str, Any]) -> bool:
    """A line that STARTS a turn: the user's question (a daemon-written
    continuation included), never a steer note."""
    return m.get("role") == "user" and bool(m.get("turnId")) and not m.get("steer")


def _naive_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def turn_starts(messages: list[dict[str, Any]]) -> list[tuple[int, dict[str, Any]]]:
    """``[(index, question line)]`` of every turn in the transcript, oldest
    first — the points a session can be rewound to."""
    seen: set[str] = set()
    out: list[tuple[int, dict[str, Any]]] = []
    for i, m in enumerate(messages):
        if _is_question(m) and m["turnId"] not in seen:
            seen.add(m["turnId"])
            out.append((i, m))
    return out


def _turn_record_start(engine: Any, turn_id: str) -> datetime | None:
    from ..core.db import session_scope
    from ..core.models import ChatTurnRecord

    with session_scope(engine) as db:
        row = db.get(ChatTurnRecord, turn_id)
        return _naive_utc(row.started_at) if row is not None else None


# --------------------------------------------------------------------------- #
# the ledger side
# --------------------------------------------------------------------------- #


def _rows_since(engine: Any, session_id: str, since: datetime) -> list[dict[str, Any]]:
    """The session's ledger rows recorded at or after ``since``, NEWEST FIRST,
    with their journal (plain values: SQLModel attributes expire)."""
    from sqlalchemy import literal_column
    from sqlmodel import select

    from ..core.db import session_scope
    from ..core.models import ToolInvocation, UndoJournal

    rowid = literal_column("toolinvocation.rowid")
    out: list[dict[str, Any]] = []
    with session_scope(engine) as db:
        rows = db.exec(
            select(ToolInvocation, UndoJournal)
            .join(UndoJournal, UndoJournal.action_id == ToolInvocation.id, isouter=True)  # type: ignore[arg-type]
            .where(ToolInvocation.session_id == session_id)
            .where(ToolInvocation.created_at >= since)
            .order_by(ToolInvocation.created_at.desc(), rowid.desc())  # type: ignore[attr-defined]
            .limit(MAX_ROWS)
        ).all()
        for inv, j in rows:
            out.append({
                "id": inv.id,
                "tool": inv.tool,
                "called_as": getattr(inv, "called_as", "") or "",
                "ok": bool(inv.ok),
                "output": (inv.output or "")[:4000],
                "args_json": inv.args_json or "{}",
                "reversibility": (inv.reversibility or "").lower(),
                "undone": inv.undone_at is not None,
                "undo_of": inv.undo_of,
                "created_at": _naive_utc(inv.created_at),
                "journal": None if j is None else {
                    "kind": j.kind,
                    "reversible": bool(j.reversible),
                    "pre_ref": j.pre_ref,
                    "pre_inline": j.pre_inline,
                    "pre_sha256": j.pre_sha256,
                    "post_sha256": j.post_sha256,
                    "applied": j.applied_at is not None,
                },
            })
    return out


def _args(row: dict[str, Any]) -> dict[str, Any]:
    try:
        data = json.loads(row.get("args_json") or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _what(row: dict[str, Any]) -> str:
    """A command's own words for the answer ("npm test"), else the tool."""
    args = _args(row)
    for key in ("command", "cmd", "argv", "path", "file_path"):
        value = args.get(key)
        if isinstance(value, list):
            value = " ".join(str(v) for v in value)
        if isinstance(value, str) and value.strip():
            text = " ".join(value.split())
            return text[:WHAT_CHARS] + ("…" if len(text) > WHAT_CHARS else "")
    return row.get("called_as") or row.get("tool") or ""


def _key(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


# --------------------------------------------------------------------------- #
# the simulation: what each revert will check, over the bytes it will see
# --------------------------------------------------------------------------- #

_ABSENT = None


class _Disk:
    """The files the replay touches, as bytes (``None`` = absent): read from
    disk the first time, then as the simulated reverts leave them."""

    def __init__(self) -> None:
        self._state: dict[str, bytes | None] = {}

    def get(self, path: Path) -> bytes | None:
        k = _key(path)
        if k not in self._state:
            try:
                self._state[k] = path.read_bytes() if path.is_file() else _ABSENT
            except OSError:
                self._state[k] = _ABSENT
        return self._state[k]

    def set(self, path: Path, data: bytes | None) -> None:
        self._state[_key(path)] = data


def _hash(data: bytes | None, mode: str) -> str | None:
    """The hash ``tools/undo.sha256_target`` would compute over ``data``:
    ``text`` mode decodes UTF-8 with universal newlines (``read_text``)."""
    from ..tools.undo import sha256_bytes

    if data is None:
        return None
    if mode == "text":
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return None
        return sha256_bytes(text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8"))
    return sha256_bytes(data)


def _written(prior: bytes, mode: str, exact: bool = False) -> bytes:
    """The bytes a ``file_restore`` revert leaves: the saved bytes as they
    are (raw mode, or an ``exact`` text copy — v1.370.0); an older ``text``
    copy is written with ``write_text`` (newlines become the OS's)."""
    if mode == "text" and not exact:
        return prior.decode("utf-8").replace("\n", os.linesep).encode("utf-8")
    return prior


def _changed_since(rel: str) -> str:
    return (
        f"{rel} changed after this session changed it, so putting it back would "
        "overwrite the newer version"
    )


def _simulate(row: dict[str, Any], disk: _Disk, home: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(files, problems)`` for ONE file action, advancing ``disk`` to what
    its revert will leave. Mirrors ``tools/undo.revert_workspace_file`` and
    ``ApplyPatchTool.revert`` check for check."""
    from ..tools.base import safe_path
    from ..tools.undo import (
        guard_unchanged,
        patch_entries,
        patch_entry_before,
        read_envelope,
        resolve_prior_bytes,
    )

    desc = row["journal"]
    kind = desc["kind"]
    meta = read_envelope(desc)
    ws = meta.get("workspace")
    files: list[dict[str, Any]] = []
    problems: list[dict[str, Any]] = []
    if not isinstance(ws, str) or not ws:
        problems.append({"path": str(meta.get("path") or row["tool"]),
                         "reason": "the change was recorded before Iron Jarvis kept its folder, so it cannot be put back"})
        return files, problems

    def target_of(rel: str) -> Path | None:
        try:
            return safe_path(Path(ws), rel)
        except Exception:  # noqa: BLE001 — the revert would refuse it too
            problems.append({"path": rel, "reason": "it is outside the folder the change was made in"})
            return None

    if kind == "files_patch":
        entries = patch_entries(meta)
        staged: list[tuple[Path, bytes | None, str]] = []
        for entry in entries:
            rel = str(entry.get("path") or "")
            target = target_of(rel)
            if target is None:
                continue
            if _hash(disk.get(target), "raw") != entry.get("post_sha256"):
                problems.append({"path": str(target), "reason": _changed_since(rel)})
                continue
            try:
                before = patch_entry_before(home, entry)
            except (OSError, ValueError):
                problems.append({"path": str(target), "reason": f"the saved copy of {rel} is gone"})
                continue
            if before is None and entry.get("existed"):
                problems.append({"path": str(target), "reason": f"no saved copy of {rel} was kept"})
                continue
            staged.append((target, before, rel))
        if not problems:
            for target, before, rel in staged:
                disk.set(target, before)
                files.append({"path": str(target), "rel": rel.replace("\\", "/"),
                              "change": "removed" if before is None else "put back"})
        return files, problems

    if kind == "files_delete":
        for rel in [str(p) for p in (meta.get("paths") or []) if isinstance(p, str) and p]:
            target = target_of(rel)
            if target is None:
                continue
            disk.set(target, None)
            files.append({"path": str(target), "rel": rel.replace("\\", "/"), "change": "removed"})
        return files, problems

    rel = str(meta.get("path") or "")
    if not rel:
        problems.append({"path": row["tool"], "reason": "the change did not record which file it was"})
        return files, problems
    target = target_of(rel)
    if target is None:
        return files, problems

    if kind == "file_rename":
        origin = str(meta.get("rename_from") or "")
        back = target_of(origin) if origin else None
        if back is None:
            if not origin:
                problems.append({"path": str(target), "reason": "the rename did not record the old name"})
            return files, problems
        if disk.get(target) is None:
            problems.append({"path": str(target), "reason": f"{rel} is no longer there"})
        elif disk.get(back) is not None:
            problems.append({"path": str(back), "reason": f"{origin} exists again, so the old name is taken"})
        else:
            disk.set(back, disk.get(target))
            disk.set(target, None)
            files.append({"path": str(back), "rel": origin.replace("\\", "/"), "change": "renamed back"})
        return files, problems

    mode = str(meta.get("mode") or "raw")
    if kind == "file_delete" and disk.get(target) is None:
        # A file this session MADE that is already gone (the user deleted
        # it): it is already as it was before the turn. Nothing to put back
        # — and the revert would refuse it as "changed since" (review,
        # v1.370.0: that used to refuse the whole rewind).
        files.append({"path": str(target), "rel": rel.replace("\\", "/"), "change": ALREADY_GONE})
        return files, problems
    if guard_unchanged(_hash(disk.get(target), mode), desc.get("post_sha256")) is not None:
        problems.append({"path": str(target), "reason": _changed_since(rel)})
        return files, problems
    if kind == "file_delete":
        disk.set(target, None)
        files.append({"path": str(target), "rel": rel.replace("\\", "/"), "change": "removed"})
        return files, problems
    if kind == "file_restore":
        try:
            prior = resolve_prior_bytes(home, desc, meta)
        except (OSError, ValueError):
            prior = None
        if prior is None:
            problems.append({"path": str(target), "reason": f"the saved copy of {rel} is gone"})
            return files, problems
        try:
            disk.set(target, _written(prior, mode, bool(meta.get("exact"))))
        except UnicodeDecodeError:
            problems.append({"path": str(target), "reason": f"the saved copy of {rel} could not be read"})
            return files, problems
        files.append({"path": str(target), "rel": rel.replace("\\", "/"), "change": "put back"})
        return files, problems
    problems.append({"path": str(target), "reason": f"Iron Jarvis does not know how to put back a {kind} change"})
    return files, problems


# --------------------------------------------------------------------------- #
# the plan
# --------------------------------------------------------------------------- #


def plan(engine: Any, home: Any, registry: Any, thread_id: str, to: str,
         permissions: Any = None) -> RewindPlan:
    """What rewinding the session to before turn ``to`` would do. Reads only;
    raises :class:`RewindError` (404) for a session or turn that is not
    there. ``permissions``: the permission engine ``POST /undo`` asks before
    each revert — a tool it would refuse is NAMED here, so the rewind is
    refused before anything is touched (review, v1.370.0: it used to stop
    part way). BLOCKING."""
    from ..core.db import session_scope
    from ..core.models import ChatThreadRecord, is_build_thread
    from ..tools.base import Reversibility
    from .sessions import session_id_for

    with session_scope(engine) as db:
        row = db.get(ChatThreadRecord, str(thread_id or ""))
        if row is None or not is_build_thread(row):
            raise RewindError("no such Build session", 404)
        messages = _messages(row)
    starts = turn_starts(messages)
    index = next((i for i, m in starts if m["turnId"] == to), None)
    if index is None:
        raise RewindError(NOT_A_TURN, 404)
    question = messages[index]
    cut_at = _naive_utc(question.get("at")) or _turn_record_start(engine, to)
    if cut_at is None:
        raise RewindError(NO_TIME)
    turns = [m["turnId"] for i, m in starts if i >= index]
    # The line before the question (the previous turn's reply): a row stamped
    # at or before it is the previous turn's work, even when a coarse clock
    # gave it the question's very tick (review, v1.370.0).
    prev_at = _naive_utc(messages[index - 1].get("at")) if index > 0 else None
    out = RewindPlan(
        to=to, question=str(question.get("content") or ""), turns=turns, keep=index, cut_at=cut_at,
        attachments=_paths(question.get("attachmentPaths")),
        picked_files=_paths(question.get("filePaths")),
        skill=str(question.get("skill") or "").strip()[:200] if isinstance(question.get("skill"), str) else "",
    )
    disk = _Disk()
    seen_files: dict[str, dict[str, Any]] = {}
    allowed: dict[str, str] = {}

    def refused(tool_name: str) -> str:
        """Why ``POST /undo`` would refuse to revert this tool ("" = it would
        not)."""
        if permissions is None or registry is None:
            return ""
        if tool_name not in allowed:
            tool = registry.get(tool_name)
            try:
                decision = permissions.authorize(tool.perm_key(), {}) if tool is not None else None
                allowed[tool_name] = "" if decision is None or decision.allowed else str(decision.reason or "")
            except Exception:  # noqa: BLE001 — the revert will say so itself
                allowed[tool_name] = ""
        return allowed[tool_name]

    for led in _rows_since(engine, session_id_for(thread_id), cut_at):
        if prev_at is not None and led["created_at"] is not None and led["created_at"] <= prev_at:
            continue
        if led["undo_of"]:
            continue  # an Undo the user pressed: the row that made it is skipped too
        if led["undone"]:
            out.already_undone += 1
            continue
        rev = led["reversibility"]
        desc = led["journal"]
        if desc is None:
            if not led["ok"] or rev == Reversibility.READONLY.value:
                continue
            if rev == Reversibility.REVERSIBLE.value:
                if NO_CHANGE_NOTE in led["output"]:
                    continue  # apply_patch said it changed nothing: nothing to put back
                # A file tool whose copy was never taken (the capture failed):
                # its change can only be lost by a rewind.
                out.problems.append({
                    "path": _what(led),
                    "reason": "no copy was kept when this session changed it, so it cannot be put back",
                })
            else:
                out.commands.append({"tool": led["called_as"] or led["tool"], "what": _what(led)})
            continue
        if not led["ok"] or desc.get("applied"):
            continue  # a call that failed changed nothing the journal can name
        if not desc.get("reversible") or rev == Reversibility.IRREVERSIBLE.value:
            out.commands.append({"tool": led["called_as"] or led["tool"], "what": _what(led)})
            continue
        if registry is not None and registry.get(led["tool"]) is None and desc["kind"] in _FILE_KINDS:
            out.problems.append({
                "path": _what(led),
                "reason": f"the tool that made this change ({led['tool']}) is no longer available",
            })
            continue
        # (a settings / secret / record restore is reversed without the tool's
        # permission — perform_undo's own rule.)
        why = "" if desc["kind"] in _NO_TOOL_KINDS else refused(led["tool"])
        if why:
            out.problems.append({
                "path": _what(led),
                "reason": ("putting this back needs " + led["tool"]
                           + ", and your permissions do not allow it (" + why + ")"),
            })
            continue
        if desc["kind"] in _FILE_KINDS:
            files, problems = _simulate(led, disk, home)
            out.problems += problems
            for f in files:
                seen_files[_key(Path(f["path"]))] = f  # the OLDEST action's word wins
            if files and not problems and all(f["change"] == ALREADY_GONE for f in files):
                continue  # nothing to put back, and its revert would refuse
            out.action_files[led["id"]] = [f["rel"] for f in files]
        out.actions.append(led["id"])
    out.files = sorted(seen_files.values(), key=lambda f: f["rel"].lower())
    # One problem per file: an older change of a file that already cannot go
    # back is judged against the same newer bytes and would name it again.
    unique: dict[str, dict[str, Any]] = {}
    for problem in out.problems:
        unique.setdefault(_key(Path(str(problem.get("path") or ""))), problem)
    out.problems = list(unique.values())
    return out


def _problem_line(problem: dict[str, Any]) -> str:
    reason = str(problem.get("reason") or "")
    path = str(problem.get("path") or "")
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    if not name or name in reason:
        return reason
    return f"{name}: {reason}"


#: How many problems / commands a sentence names before "and N more".
SENTENCE_ITEMS = 5


def problems_sentence(problems: list[dict[str, Any]]) -> str:
    """The refusal in words, naming each file: "Nothing was rewound: …"."""
    lines = [_problem_line(p) for p in problems[:SENTENCE_ITEMS]]
    more = len(problems) - len(lines)
    tail = f"; and {more} more" if more > 0 else ""
    return "Nothing was rewound: " + "; ".join(lines) + tail + "."


def commands_note(commands: list[dict[str, Any]]) -> str:
    """What a rewind leaves as it is, in words ("" when nothing ran)."""
    if not commands:
        return ""
    names = [f"`{c.get('what') or c.get('tool')}`" for c in commands[:SENTENCE_ITEMS]]
    more = len(commands) - len(names)
    tail = f" and {more} more" if more > 0 else ""
    return (
        "Commands are not undone: " + ", ".join(names) + tail
        + " ran in the rewound turns, and whatever they changed stays as it is."
    )


def checkpoints(engine: Any, thread_id: str) -> list[dict[str, Any]]:
    """Every turn of the session as a rewind point, oldest first:
    ``{turn_id, question, at, continuation, files, commands}`` — the files a
    turn changed and the commands it ran, counted from the ledger in that
    turn's window. BLOCKING."""
    from ..core.db import session_scope
    from ..core.models import ChatThreadRecord, is_build_thread
    from ..tools.base import Reversibility
    from .sessions import session_id_for

    with session_scope(engine) as db:
        row = db.get(ChatThreadRecord, str(thread_id or ""))
        if row is None or not is_build_thread(row):
            raise RewindError("no such Build session", 404)
        messages = _messages(row)
    starts = turn_starts(messages)
    if not starts:
        return []
    points: list[dict[str, Any]] = []
    for i, m in starts:
        points.append({
            "turn_id": m["turnId"],
            "question": " ".join(str(m.get("content") or "").split())[:200],
            "at": m.get("at"),
            "continuation": bool(m.get("continuation")),
            "files": 0,
            "commands": 0,
            "_since": _naive_utc(m.get("at")),
            "_prev": _naive_utc(messages[i - 1].get("at")) if i > 0 else None,
        })
    first = next((p["_since"] for p in points if p["_since"] is not None), None)
    rows = _rows_since(engine, session_id_for(thread_id), first) if first is not None else []
    for led in rows:
        if led["undo_of"] or led["undone"] or not led["ok"]:
            continue
        at = led["created_at"]
        owner = None
        for p in points:
            if p["_since"] is not None and at is not None and at >= p["_since"] and (
                p["_prev"] is None or at > p["_prev"]
            ):
                owner = p
        if owner is None:
            continue
        desc = led["journal"]
        if desc is not None and desc.get("kind") in _FILE_KINDS and desc.get("reversible"):
            owner["files"] += 1
        elif led["reversibility"] not in (Reversibility.READONLY.value, ""):
            owner["commands"] += 1
    for p in points:
        p.pop("_since", None)
        p.pop("_prev", None)
    return points


__all__ = [
    "RewindError", "RewindPlan", "checkpoints", "commands_note", "plan",
    "problems_sentence", "turn_starts",
]
