"""The EXPERIENCE LIBRARY, one per project folder (v1.370.0, IJ harness plan
Phase 7; Harness² principle 9 — ideas only, no code taken).

After a Build session's turn ends having done REAL work (it changed files or
ran commands — read off the ledger, never off the model's words), a reflector
may write ONE evidence-supported lesson for that session's folder, and may
PROPOSE one edit to the folder's harness layer
(``<folder>/.ironjarvis/harness/<file>``, one of the Phase 3 text files).

THE THREE RULES, each the v1.153.0 / v1.297.0 precedent:

* LEDGER-CHECKED (compaction's rule, made stricter): every file path a lesson
  names must appear in that SESSION'S LEDGER (a journal target, a path
  argument, or a path inside a command the session ran), and every quoted
  span (``"…"`` or ``\\`…\\```) must be VERBATIM in the turn's transcript (the
  question, the reply, and what its tool calls were given and answered). A
  line with an unsupported claim is REMOVED; a lesson that survives with no
  line — or with no verified claim at all (it would not be evidence-supported)
  — is not stored. An edit's ADDED lines are checked the same way.
* HONEST MOCK: no real model, no lesson. A turn the demo model answered is not
  reflected on, and a reflection the demo model answered is thrown away —
  this text is read back into every later session in that folder as
  authoritative.
* NOTHING ADOPTED WITHOUT THE USER: every row is minted ``proposed``. An
  accepted LESSON rides later sessions in that folder; an accepted layer EDIT
  is written into the folder's file at that moment (stale-checked, validated
  by Phase 3's validator, all or nothing). Nothing is written into the user's
  folder before an Accept. Lessons themselves live in the app's own database.

AN EDIT is validated like the coach's revisions (:func:`validate_edit`: not
empty, not identical, growth at most +20% or +:data:`MIN_GROWTH_CHARS`, every
never/always line kept) and then as a LAYER: the folder's current harness
files with the edit applied are copied to a scratch folder under the app's
home and read by :func:`components.read_project_layer` — an edit that would
make the layer invalid is never proposed.

THE LIBRARY COMPONENT (:func:`library_lines`): the folder's accepted lessons,
newest first, each through ``core.promptguard`` (a flagged one is dropped),
bounded (:data:`MAX_LESSONS`, :data:`MAX_LIBRARY_CHARS`), rendered by
``components.render`` as its own section of the component tree — read by
``chat_turn._build_harness``, i.e. BEFORE the history planner, like the rest
of the tree.

The reflection is ONE router completion on the model that served the turn,
PINNED (no failover: the user chose that model) and on the session's ONE
account (its ``AccountPin``) and under its Local only scope; billed to the
session's usage. It runs in the background after the turn's reply is written;
it never delays or fails a turn. ``build_reflect`` (a setting) turns it off.

Every database / file function here is BLOCKING; the async ones hop.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from ..core.db import session_scope
from ..core.ids import new_id, utcnow
from .library_models import (
    ACCEPTED,
    DECLINE_SUPPRESS_DAYS,
    DECLINED,
    KIND_LAYER_EDIT,
    KIND_LESSON,
    PROPOSED,
    STALE,
    HarnessLibraryRecord,
)

log = logging.getLogger(__name__)

#: A lesson, at most (after the check).
MAX_LESSON_CHARS = 500
MAX_LESSON_LINES = 5
#: Accepted lessons a session reads, and their total size.
MAX_LESSONS = 10
MAX_LIBRARY_CHARS = 2500
#: Proposals waiting for the user in one folder before the reflector stops
#: asking (the preferences' open limit).
OPEN_LIMIT = 3
#: An edit may grow the file by +20% or this many characters (the coach's
#: rule with a larger floor: a new memory.md starts from nothing).
MAX_GROWTH = 0.20
MIN_GROWTH_CHARS = 400
#: Lines that carry one of these are SAFETY lines an edit must keep.
SAFETY_WORDS = ("never", "always")
#: The files an edit may change (the Phase 3 text components).
EDIT_FILES = ("system_prompt.md", "guardrails.md", "memory.md", "tool_guidance.md")
#: Evidence handed to the reflector, at most.
PROMPT_STEPS = 30
STEP_OUTPUT_CHARS = 600
PROMPT_CHARS = 14_000
LAYER_FILE_CHARS = 4000
#: Ledger rows read for one session's path facts.
LEDGER_ROWS = 2000
#: The tools that count as "ran a command".
COMMAND_TOOLS = frozenset({"shell", "process_start", "process_write", "run_code"})
_FILE_KINDS = frozenset({"file_restore", "file_delete", "files_delete", "files_patch", "file_rename"})

REASON_OFF = "learning from Build sessions is turned off"
REASON_NO_TURN = "the turn's question or reply is not in the session"
REASON_NO_WORK = "the turn changed no file and ran no command"
REASON_MOCK = "the demo model answered — the library never learns from it"
REASON_NO_MODEL = "no model to reflect with"
REASON_FULL = f"{OPEN_LIMIT} suggestions for this folder are already waiting for you"
REASON_MODEL_FAILED = "the reflection call failed"
REASON_NOTHING = "nothing the record supports was left to keep"
REASON_ACCOUNT = "the session's account could not be used for the reflection"
REASON_REWOUND = "the turn was rewound while it was being reflected on"

STALE_SENTENCE = "The file changed since this was suggested, so it was not written. Ask again after your next turn."
INVALID_SENTENCE = "With this edit the folder's harness files would not be valid ({note}), so nothing was written."


class LibraryRefused(Exception):
    """A plain sentence; ``status`` is the HTTP status the route answers."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------- #
# folders
# --------------------------------------------------------------------------- #


def folder_key(folder: Any) -> str:
    from .sessions import folder_key as _key

    return _key(str(folder or ""))


def _folder_name(folder: str) -> str:
    text = str(folder or "").replace("\\", "/").rstrip("/")
    return text.rsplit("/", 1)[-1] or text


def _one_line(text: Any) -> str:
    return " ".join(str(text or "").split())


# --------------------------------------------------------------------------- #
# reading: the library component and the views
# --------------------------------------------------------------------------- #


def library_lines(engine: Any, folder: Any, *, event_bus: Any = None, session_id: str | None = "chat") -> list[str]:
    """The folder's ACCEPTED lessons for the component tree: newest first,
    each through promptguard (a flagged lesson is DROPPED, like a flagged
    learning lesson), at most :data:`MAX_LESSONS` and :data:`MAX_LIBRARY_CHARS`
    in all. ``[]`` for no folder / no lessons / any failure. BLOCKING."""
    key = folder_key(folder)
    if not key or engine is None:
        return []
    try:
        from sqlmodel import select

        from ..core.promptguard import publish_blocked, scan_context

        with session_scope(engine) as db:
            rows = list(db.exec(
                select(HarnessLibraryRecord.text)
                .where(HarnessLibraryRecord.folder_key == key)
                .where(HarnessLibraryRecord.kind == KIND_LESSON)
                .where(HarnessLibraryRecord.status == ACCEPTED)
                .order_by(HarnessLibraryRecord.decided_at.desc())  # type: ignore[union-attr]
                .limit(MAX_LESSONS * 2)
            ))
        out: list[str] = []
        total = 0
        for text in rows:
            text = str(text or "").strip()
            if not text:
                continue
            result = scan_context(text, source="Build library lesson", cap=None)
            if result.blocked:
                publish_blocked(event_bus, session_id, result)
                continue
            if total + len(text) > MAX_LIBRARY_CHARS:
                break
            out.append(text)
            total += len(text)
            if len(out) >= MAX_LESSONS:
                break
        return out
    except Exception:  # noqa: BLE001 — a library read must never break a turn
        log.warning("reading the Build library failed", exc_info=True)
        return []


def row_view(row: HarnessLibraryRecord) -> dict[str, Any]:
    try:
        evidence = json.loads(row.evidence_json or "{}")
    except (TypeError, ValueError):
        evidence = {}
    out: dict[str, Any] = {
        "id": row.id,
        "kind": row.kind,
        "status": row.status,
        "text": row.text,
        "evidence": evidence if isinstance(evidence, dict) else {},
        "thread_id": row.thread_id,
        "turn_id": row.turn_id,
        "provider": row.provider,
        "model": row.model,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
    }
    if row.kind == KIND_LAYER_EDIT:
        out.update({
            "file": row.file,
            "path": f".ironjarvis/harness/{row.file}",
            "before": row.before,
            "after": row.after,
            "new_file": not row.before,
        })
    return out


def view(engine: Any, folder: Any) -> dict[str, Any]:
    """``{folder, folder_name, lessons, proposals, open_limit}`` — accepted
    lessons and the proposals waiting for the user, newest first. BLOCKING."""
    from sqlmodel import select

    key = folder_key(folder)
    if not key:
        raise LibraryRefused("Say which folder.", 400)
    with session_scope(engine) as db:
        rows = list(db.exec(
            select(HarnessLibraryRecord)
            .where(HarnessLibraryRecord.folder_key == key)
            .where(HarnessLibraryRecord.status.in_([ACCEPTED, PROPOSED]))  # type: ignore[attr-defined]
            .order_by(HarnessLibraryRecord.created_at.desc())  # type: ignore[attr-defined]
        ))
        for r in rows:
            db.expunge(r)
    return {
        "folder": str(folder),
        "folder_name": _folder_name(str(folder)),
        "lessons": [row_view(r) for r in rows if r.status == ACCEPTED and r.kind == KIND_LESSON],
        "edits": [row_view(r) for r in rows if r.status == ACCEPTED and r.kind == KIND_LAYER_EDIT],
        "proposals": [row_view(r) for r in rows if r.status == PROPOSED],
        "open_limit": OPEN_LIMIT,
    }


def get(engine: Any, item_id: str) -> HarnessLibraryRecord | None:
    with session_scope(engine) as db:
        row = db.get(HarnessLibraryRecord, str(item_id or ""))
        if row is not None:
            db.expunge(row)
        return row


# --------------------------------------------------------------------------- #
# the evidence: the ledger and the turn's transcript
# --------------------------------------------------------------------------- #


@dataclass
class Evidence:
    thread_id: str
    turn_id: str
    folder: str
    question: str
    reply: str
    route: dict[str, Any]
    #: The turn's transcript: every text a quote may be checked against.
    transcript: str
    #: Normalised paths the SESSION's ledger names (see :func:`_norm_path`).
    paths: set[str]
    #: The turn changed a file / ran a command (from the ledger).
    changed_files: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    #: For the prompt: [{tool, args, ok, output}] of the turn.
    steps: list[dict[str, Any]] = field(default_factory=list)

    @property
    def real_work(self) -> bool:
        return bool(self.changed_files or self.commands)


def _norm_path(path: str) -> str:
    text = str(path or "").strip().strip("'\"`").replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.rstrip("/.,;:").lower()


def _strings(value: Any) -> list[str]:
    """Every string inside a decoded JSON value."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def _path_tokens(text: str) -> set[str]:
    from ..context.compaction import _PATH_RX

    return {_norm_path(m.group(0)) for m in _PATH_RX.finditer(str(text or ""))}


def _journal_targets(pre_inline: str | None, kind: str) -> set[str]:
    from ..tools.undo import patch_entries

    try:
        meta = json.loads(pre_inline or "{}")
    except (TypeError, ValueError):
        return set()
    if not isinstance(meta, dict):
        return set()
    rels: list[str] = []
    if kind == "files_delete":
        rels = [str(p) for p in meta.get("paths") or [] if isinstance(p, str)]
    elif kind == "files_patch":
        rels = [str(e["path"]) for e in patch_entries(meta)]
    else:
        for key in ("path", "rename_from"):
            if isinstance(meta.get(key), str) and meta[key]:
                rels.append(meta[key])
    out = {_norm_path(r) for r in rels}
    ws = meta.get("workspace")
    if isinstance(ws, str) and ws:
        out |= {_norm_path(str(Path(ws) / r)) for r in rels}
    return out


def turn_evidence(engine: Any, thread_id: str, turn_id: str) -> Evidence | None:
    """What the record says about one turn of a Build session, or None when
    the session or the turn's question + reply are not there. BLOCKING."""
    from sqlalchemy import literal_column
    from sqlmodel import select

    from ..core.models import ChatThreadRecord, ToolInvocation, UndoJournal, is_build_thread
    from .rewind import _naive_utc
    from .sessions import folder_of, session_id_for

    with session_scope(engine) as db:
        row = db.get(ChatThreadRecord, str(thread_id or ""))
        if row is None or not is_build_thread(row):
            return None
        try:
            messages = json.loads(row.messages_json or "[]")
        except (TypeError, ValueError):
            messages = []
        folder = folder_of(row)
    messages = [m for m in messages if isinstance(m, dict)] if isinstance(messages, list) else []
    question = next((m for m in messages if m.get("turnId") == turn_id and m.get("role") == "user"
                     and not m.get("steer")), None)
    reply = next((m for m in reversed(messages) if m.get("turnId") == turn_id
                  and m.get("role") == "assistant"), None)
    if question is None or reply is None:
        return None
    since = _naive_utc(question.get("at"))
    until = _naive_utc(reply.get("at"))
    sid = session_id_for(thread_id)
    texts: list[str] = [str(question.get("content") or ""), str(reply.get("content") or "")]
    for step in reply.get("steps") or []:
        detail = step.get("detail") if isinstance(step, dict) else None
        if isinstance(detail, dict):
            texts += [str(detail.get("input") or ""), str(detail.get("output") or "")]
    paths: set[str] = set()
    changed: list[str] = []
    commands: list[str] = []
    steps: list[dict[str, Any]] = []
    rowid = literal_column("toolinvocation.rowid")
    with session_scope(engine) as db:
        led = db.exec(
            select(ToolInvocation, UndoJournal)
            .join(UndoJournal, UndoJournal.action_id == ToolInvocation.id, isouter=True)  # type: ignore[arg-type]
            .where(ToolInvocation.session_id == sid)
            .order_by(ToolInvocation.created_at.desc(), rowid.desc())  # type: ignore[attr-defined]
            .limit(LEDGER_ROWS)
        ).all()
        rows = [(inv.tool, inv.args_json, inv.ok, inv.output, _naive_utc(inv.created_at),
                 inv.undone_at is not None, bool(inv.undo_of),
                 None if j is None else (j.kind, j.pre_inline)) for inv, j in led]
    for tool, args_json, ok, output, created, undone, is_undo, journal in reversed(rows):
        try:
            args = json.loads(args_json or "{}")
        except (TypeError, ValueError):
            args = {}
        values = _strings(args)
        for v in values:
            paths |= _path_tokens(v)
        for key in ("path", "file_path", "notebook_path", "cwd"):
            if isinstance(args, dict) and isinstance(args.get(key), str) and args[key]:
                paths.add(_norm_path(args[key]))
        if journal is not None:
            paths |= _journal_targets(journal[1], journal[0])
        in_turn = since is not None and created is not None and created >= since and (
            until is None or created <= until
        )
        if not in_turn or is_undo:
            continue
        texts += values + [str(output or "")]
        if ok and not undone:
            if journal is not None and journal[0] in _FILE_KINDS:
                changed += sorted(_journal_targets(journal[1], journal[0]))[:1] or [tool]
            elif tool in COMMAND_TOOLS:
                commands.append(next((v for v in values if v.strip()), tool))
        if len(steps) < PROMPT_STEPS:
            steps.append({
                "tool": tool,
                "args": {k: (v if len(str(v)) < 300 else str(v)[:300] + "…") for k, v in args.items()}
                if isinstance(args, dict) else {},
                "ok": bool(ok),
                "output": str(output or "")[:STEP_OUTPUT_CHARS],
            })
    route = reply.get("route") if isinstance(reply.get("route"), dict) else {}
    return Evidence(
        thread_id=thread_id, turn_id=turn_id, folder=folder,
        question=str(question.get("content") or ""), reply=str(reply.get("content") or ""),
        route=route, transcript="\n".join(t for t in texts if t),
        paths={p for p in paths if p}, changed_files=changed, commands=commands, steps=steps,
    )


# --------------------------------------------------------------------------- #
# the check: every path in the ledger, every quote verbatim
# --------------------------------------------------------------------------- #

_TICK_RX = re.compile(r"`([^`\n]{2,})`")


def _path_supported(claim: str, known: set[str]) -> bool:
    c = _norm_path(claim)
    if not c:
        return True
    for k in known:
        if k == c or k.endswith("/" + c) or c.endswith("/" + k):
            return True
    return False


def check_lines(text: str, evidence: Evidence, *, only: "set[str] | None" = None) -> tuple[str, dict[str, Any]]:
    """``(kept text, {paths, quotes, stripped})``: every line of ``text``
    whose path claims are not in the session's ledger, or whose quoted spans
    are not verbatim in the turn's transcript, is removed. ``only``: check
    just these lines (an edit's ADDED lines); the others are kept as they
    are."""
    from ..context.compaction import _PATH_RX, _QUOTED_RX

    kept: list[str] = []
    paths: list[str] = []
    quotes: list[str] = []
    stripped: list[str] = []
    for line in str(text or "").splitlines():
        probe = line.strip()
        if not probe or (only is not None and line not in only):
            kept.append(line)
            continue
        claim_paths = [m.group(0).rstrip(".,;:") for m in _PATH_RX.finditer(probe)]
        claim_quotes = [m.group(1).strip() for m in _QUOTED_RX.finditer(probe)]
        claim_quotes += [m.group(1).strip() for m in _TICK_RX.finditer(probe)]
        bad = ""
        for p in claim_paths:
            if not _path_supported(p, evidence.paths):
                bad = p
                break
        if not bad:
            for q in claim_quotes:
                if q and q not in evidence.transcript:
                    bad = q
                    break
        if bad:
            stripped.append(bad)
            continue
        paths += [p for p in claim_paths if p not in paths]
        quotes += [q for q in claim_quotes if q and q not in quotes]
        kept.append(line)
    return "\n".join(kept).strip(), {"paths": paths, "quotes": quotes, "stripped": stripped}


# --------------------------------------------------------------------------- #
# edits: the coach's revision rules + the Phase 3 validator
# --------------------------------------------------------------------------- #


def _safety_lines(text: str) -> list[str]:
    return [
        line.strip() for line in str(text or "").splitlines()
        if line.strip() and any(w in line.lower() for w in SAFETY_WORDS)
    ]


def validate_edit(before: str, after: str) -> str | None:
    """Why ``after`` is NOT an acceptable small edit of ``before`` (None = it
    is): the coach's rules (``coach.engine.validate_revision``) with a larger
    growth floor (:data:`MIN_GROWTH_CHARS`)."""
    before = str(before or "")
    after = str(after or "")
    if not after.strip():
        return "the proposed text is empty"
    if after.strip() == before.strip():
        return "the proposed text is the same as the file"
    limit = max(int(len(before) * (1.0 + MAX_GROWTH)), len(before) + MIN_GROWTH_CHARS)
    if len(after) > limit:
        return f"the proposed text grows too much ({len(after)} > {limit} characters)"
    kept = {line.strip() for line in after.splitlines()}
    for line in _safety_lines(before):
        if line not in kept:
            return f"the proposed text drops a never/always line: {line[:80]!r}"
    return None


def _harness_dir(folder: str) -> Path:
    from .components import HARNESS_PARTS

    return Path(folder).joinpath(*HARNESS_PARTS)


def read_layer_file(folder: str, name: str) -> str | None:
    """The folder's harness file ``name`` as text ("" = it does not exist),
    or None when it cannot be read as UTF-8 text. BLOCKING."""
    path = _harness_dir(folder) / name
    try:
        if not path.exists():
            return ""
        data = path.read_bytes()
    except OSError:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return text.removeprefix("﻿").replace("\r\n", "\n").replace("\r", "\n")


def layer_with_edit_problem(home: Any, folder: str, name: str, after: str, known_tools: Any = None) -> str | None:
    """Would the folder's harness layer be VALID with ``name`` = ``after``?
    The current layer is copied to a scratch folder under the app's home, the
    edit applied there, and Phase 3's validator reads it. None = valid; else
    the validator's note. BLOCKING."""
    from .components import HARNESS_PARTS, read_project_layer

    scratch = Path(home) / "tmp" / f"library-check-{new_id('lc')}"
    try:
        target = scratch.joinpath(*HARNESS_PARTS)
        src = _harness_dir(folder)
        if src.is_dir():
            shutil.copytree(src, target, symlinks=True)
        else:
            target.mkdir(parents=True, exist_ok=True)
        (target / name).write_bytes(after.encode("utf-8"))
        layer = read_project_layer(scratch, known_tools=known_tools, session_id=None)
        if layer.used:
            return None
        return layer.note or "the harness files could not be read"
    except OSError as exc:
        return f"the edit could not be checked ({exc.strerror or exc})"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


# --------------------------------------------------------------------------- #
# minting
# --------------------------------------------------------------------------- #


def signature(key: str, kind: str, file: str, text: str) -> str:
    norm = " ".join(str(text or "").lower().split())
    return hashlib.sha256(f"{key}|{kind}|{file}|{norm}".encode("utf-8")).hexdigest()


def _open_count(engine: Any, key: str) -> int:
    from sqlmodel import func, select

    with session_scope(engine) as db:
        return int(db.exec(
            select(func.count()).select_from(HarnessLibraryRecord)
            .where(HarnessLibraryRecord.folder_key == key)
            .where(HarnessLibraryRecord.status == PROPOSED)
        ).one())


def _suppressed(engine: Any, sig: str) -> bool:
    from sqlmodel import select

    from .rewind import _naive_utc

    cutoff = _naive_utc(utcnow() - timedelta(days=DECLINE_SUPPRESS_DAYS))
    with session_scope(engine) as db:
        rows = list(db.exec(select(HarnessLibraryRecord).where(HarnessLibraryRecord.signature == sig)))
    for r in rows:
        if r.status in (PROPOSED, ACCEPTED):
            return True
        decided = _naive_utc(r.decided_at) if r.decided_at else None
        if r.status == DECLINED and decided is not None and cutoff is not None and decided >= cutoff:
            return True
    return False


def _mint(engine: Any, row: HarnessLibraryRecord) -> HarnessLibraryRecord:
    with session_scope(engine) as db:
        db.add(row)
        db.commit()
        db.refresh(row)
        db.expunge(row)
    return row


def _turn_present(engine: Any, thread_id: str, turn_id: str) -> bool:
    """Does the session's transcript still hold ``turn_id``'s reply?"""
    from ..core.models import ChatThreadRecord, is_build_thread

    with session_scope(engine) as db:
        row = db.get(ChatThreadRecord, str(thread_id or ""))
        if row is None or not is_build_thread(row):
            return False
        try:
            messages = json.loads(row.messages_json or "[]")
        except (TypeError, ValueError):
            return False
    return any(
        isinstance(m, dict) and m.get("role") == "assistant" and m.get("turnId") == turn_id
        for m in (messages if isinstance(messages, list) else [])
    )


def _mint_for_turn(engine: Any, row: HarnessLibraryRecord) -> HarnessLibraryRecord | None:
    """Mint ``row`` only while its turn is still in the session — checked and
    inserted under the ONE conversation lock a rewind's cut takes, so a turn
    rewound during the reflection never gets a proposal (a mint that landed
    BEFORE the cut is removed by the rewind, :func:`forget_turns`). None =
    the turn is gone. BLOCKING."""
    from ..core.db import CONVERSATION_WRITE_LOCK

    with CONVERSATION_WRITE_LOCK:
        if not _turn_present(engine, row.thread_id, row.turn_id):
            return None
        return _mint(engine, row)


def forget_turns(engine: Any, thread_id: str, turn_ids: Any) -> int:
    """A REWIND's library half: the PROPOSALS made from the rewound turns go
    (they describe work that is no longer there). Accepted lessons and edits
    were the user's decision and stay. Returns how many went. BLOCKING."""
    from sqlmodel import select

    turns = [str(t) for t in (turn_ids or []) if t]
    if not turns:
        return 0
    with session_scope(engine) as db:
        rows = list(db.exec(
            select(HarnessLibraryRecord)
            .where(HarnessLibraryRecord.thread_id == str(thread_id or ""))
            .where(HarnessLibraryRecord.turn_id.in_(turns))  # type: ignore[attr-defined]
            .where(HarnessLibraryRecord.status == PROPOSED)
        ))
        for r in rows:
            db.delete(r)
        db.commit()
    return len(rows)


# --------------------------------------------------------------------------- #
# the reflector
# --------------------------------------------------------------------------- #

REFLECT_SYSTEM = (
    "You review ONE finished turn of a coding session in a project folder and write down what a "
    "later session in this SAME folder should know. You are given the user's request, the tools "
    "the session called (with their arguments and output), its reply, and the folder's current "
    "harness notes.\n"
    "Write a LESSON of 1 to 3 short lines: concrete facts about THIS project that the record "
    "shows (how its tests are run, which file holds what, a command that failed and the one that "
    "worked). Name files by the paths the tool calls used. Quote a command or an output only in "
    "backticks and only exactly as it appears in the record. No general coding advice, nothing "
    "about the user, nothing about the assistant, its tools or this computer's setup, nothing the "
    "record does not show. If nothing specific was learned, the lesson is \"\".\n"
    "You MAY also propose ONE small edit to one of the folder's harness files "
    "(memory.md, tool_guidance.md, guardrails.md, system_prompt.md): its FULL new text, keeping "
    "what the file already says (and every line that says never or always) unless the record "
    "shows it is now wrong, and growing it only a little. Usually, add the lesson to memory.md. "
    "Propose no edit when the lesson is empty.\n"
    "Answer with ONE JSON object and nothing else: "
    "{\"lesson\": \"...\", \"edit\": null} or "
    "{\"lesson\": \"...\", \"edit\": {\"file\": \"memory.md\", \"text\": \"...\", \"why\": \"...\"}}"
)


def reflect_prompt(evidence: Evidence, layer_files: dict[str, str]) -> str:
    """The user message of the ONE reflection call — the turn, bounded."""
    parts = [
        "THE REQUEST:", evidence.question[:3000], "",
        "THE TOOL CALLS (in order):",
    ]
    for i, step in enumerate(evidence.steps, 1):
        parts.append(
            f"{i}. {step['tool']} {json.dumps(step['args'], ensure_ascii=False)[:600]} -> "
            + ("ok" if step["ok"] else "FAILED")
        )
        out = step["output"].strip()
        if out:
            parts.append("   output: " + out.replace("\n", "\n   "))
    parts += ["", "THE REPLY:", evidence.reply[:3000], "", "THE FOLDER'S HARNESS NOTES NOW:"]
    if any(v for v in layer_files.values()):
        for name, text in layer_files.items():
            if text:
                parts += [f"--- {name} ---", text[:LAYER_FILE_CHARS]]
    else:
        parts.append("(none yet)")
    text = "\n".join(parts)
    return text[:PROMPT_CHARS]


def _is_mock(provider: Any, reason: Any = "") -> bool:
    return str(provider or "").strip().lower() == "mock" or str(reason or "").strip().lower() == "mock"


@dataclass
class Reflection:
    minted: list[HarnessLibraryRecord] = field(default_factory=list)
    reason: str = ""


async def _complete(platform: Any, evidence: Evidence, prompt: str) -> tuple[Any, str]:
    """ONE pinned completion on the model that served the turn, on the
    session's account and under its Local only scope. ``(route, reason)``."""
    from ..providers import egress as _egress
    from ..providers.adapters.base import LLMMessage

    router = getattr(platform, "router", None)
    if router is None:
        return None, REASON_NO_MODEL
    provider = str(evidence.route.get("provider") or "")
    model = str(evidence.route.get("model") or "")
    if not provider:
        return None, REASON_NO_MODEL
    from .sessions import _setup, get_row

    row = await asyncio.to_thread(get_row, platform.engine, evidence.thread_id)
    setup = _setup(row) if row is not None else {}
    pin = None
    if setup.get("account"):
        try:
            from ..daemon.routes.build import turn_account

            pin, _prov = await asyncio.to_thread(turn_account, platform, evidence.thread_id, setup)
        except Exception:  # noqa: BLE001 — an unreachable account: no reflection on another
            return None, REASON_ACCOUNT
    from ..iron_proxy.pin import reset_pin, set_pin

    token = set_pin(pin) if pin is not None else None
    try:
        with _egress.local_only_scope(setup.get("local_only") is True):
            route = await router.complete(
                provider=provider, model=model or None, system=REFLECT_SYSTEM,
                messages=[LLMMessage(role="user", content=prompt)], tools=[],
                session_id=None, pin=True,
            )
    except Exception:  # noqa: BLE001 — a failed reflection is a reason, never a crash
        log.info("Build library reflection failed", exc_info=True)
        return None, REASON_MODEL_FAILED
    finally:
        if token is not None:
            reset_pin(token)
    _bill(platform, route, evidence.thread_id)
    return route, ""


def _bill(platform: Any, route: Any, thread_id: str) -> None:
    """The reflection's usage on the session's own total (the follow-ups' way)."""
    try:
        from types import SimpleNamespace

        from ..core.models import AgentState
        from ..daemon.chat_turn import _persist_chat_usage
        from ..eval.pricing import UsageTally

        usage = getattr(getattr(route, "response", None), "usage", None) or {}
        provider = str(getattr(route, "provider", "") or "")
        model = str(getattr(route, "model", "") or "")
        tally = UsageTally()
        tally.add(provider, model, usage)
        _persist_chat_usage(
            SimpleNamespace(platform=platform), provider=provider, model=model,
            state=AgentState.COMPLETED, completions=1, usage_in=tally.input_tokens,
            usage_out=tally.output_tokens, cost_usd=tally.cost_usd, thread_id=thread_id,
        )
    except Exception:  # noqa: BLE001 — accounting never fails the reflection
        log.debug("Build library reflection usage not recorded", exc_info=True)


def _clean_lesson(text: Any) -> str:
    lines = [ln.rstrip() for ln in str(text or "").replace("\r\n", "\n").split("\n") if ln.strip()]
    text = "\n".join(lines[:MAX_LESSON_LINES]).strip()
    return text[:MAX_LESSON_CHARS].rstrip()


async def reflect(platform: Any, thread_id: str, turn_id: str) -> Reflection:
    """Reflect on ONE finished build turn: at most one lesson and one edit
    proposal minted for the session's folder (see the module docstring).
    Never raises; ``reason`` says why nothing was minted."""
    out = Reflection()
    try:
        return await _reflect(platform, thread_id, turn_id, out)
    except Exception:  # noqa: BLE001 — a reflector must never take anything down
        log.warning("Build library reflection failed", exc_info=True)
        out.reason = REASON_MODEL_FAILED
        return out


async def _reflect(platform: Any, thread_id: str, turn_id: str, out: Reflection) -> Reflection:
    from ..core.promptguard import scan_context
    from .components import TEXT_FILES

    if not bool(getattr(getattr(platform, "config", None), "build_reflect", True)):
        out.reason = REASON_OFF
        return out
    engine = platform.engine
    evidence = await asyncio.to_thread(turn_evidence, engine, thread_id, turn_id)
    if evidence is None or not evidence.folder:
        out.reason = REASON_NO_TURN
        return out
    if _is_mock(evidence.route.get("provider"), evidence.route.get("reason")):
        out.reason = REASON_MOCK
        return out
    if not evidence.real_work:
        out.reason = REASON_NO_WORK
        return out
    key = folder_key(evidence.folder)
    if await asyncio.to_thread(_open_count, engine, key) >= OPEN_LIMIT:
        out.reason = REASON_FULL
        return out
    layer_files = {}
    for name in EDIT_FILES:
        layer_files[name] = await asyncio.to_thread(read_layer_file, evidence.folder, name) or ""
    route, why = await _complete(platform, evidence, reflect_prompt(evidence, layer_files))
    if route is None:
        out.reason = why
        return out
    if _is_mock(getattr(route, "provider", ""), getattr(route, "reason", "")):
        out.reason = REASON_MOCK
        return out
    from ..core import jsonish

    text = str(getattr(getattr(route, "response", None), "text", "") or "")
    answer = jsonish.loads_object(text) or {}
    provider = str(getattr(route, "provider", "") or "")
    model = str(getattr(route, "model", "") or "")

    # THE LESSON
    lesson = _clean_lesson(answer.get("lesson"))
    kept_lesson = ""
    if lesson and not scan_context(lesson, source="Build library lesson", cap=None).blocked:
        kept_lesson, ev = check_lines(lesson, evidence)
        if kept_lesson and (ev["paths"] or ev["quotes"]):
            sig = signature(key, KIND_LESSON, "", kept_lesson)
            if not await asyncio.to_thread(_suppressed, engine, sig):
                minted = await asyncio.to_thread(_mint_for_turn, engine, HarnessLibraryRecord(
                    folder_key=key, folder=evidence.folder, kind=KIND_LESSON, text=kept_lesson,
                    evidence_json=json.dumps(ev), signature=sig, thread_id=thread_id,
                    turn_id=turn_id, provider=provider, model=model,
                ))
                if minted is None:
                    out.reason = REASON_REWOUND
                    return out
                out.minted.append(minted)
        else:
            kept_lesson = ""

    # THE EDIT (only beside a lesson the record supports)
    edit = answer.get("edit")
    if kept_lesson and isinstance(edit, dict) and str(edit.get("file") or "") in EDIT_FILES:
        name = str(edit["file"])
        before = await asyncio.to_thread(read_layer_file, evidence.folder, name)
        after = str(edit.get("text") or "").replace("\r\n", "\n").replace("\r", "\n")
        if before is not None and after.strip():
            if not after.endswith("\n"):
                after += "\n"
            added = {ln for ln in after.splitlines() if ln not in set(before.splitlines())}
            after_checked, ev = check_lines(after, evidence, only=added)
            after_checked = after_checked + "\n" if after_checked else ""
            problem = validate_edit(before, after_checked)
            scanned = scan_context(after_checked, source=f"Build library edit {name}", cap=None)
            if problem is None and not scanned.blocked and name in TEXT_FILES:
                try:
                    known = set(platform.registry.names())
                except Exception:  # noqa: BLE001
                    known = None
                invalid = await asyncio.to_thread(
                    layer_with_edit_problem, platform.config.home, evidence.folder, name, after_checked, known,
                )
                why_line = _one_line(edit.get("why"))[:200] or f"Keep this lesson in {name}."
                sig = signature(key, KIND_LAYER_EDIT, name, after_checked)
                if invalid is None and not await asyncio.to_thread(_suppressed, engine, sig):
                    minted = await asyncio.to_thread(_mint_for_turn, engine, HarnessLibraryRecord(
                        folder_key=key, folder=evidence.folder, kind=KIND_LAYER_EDIT,
                        text=why_line, file=name, before=before, after=after_checked,
                        evidence_json=json.dumps(ev), signature=sig, thread_id=thread_id,
                        turn_id=turn_id, provider=provider, model=model,
                    ))
                    if minted is not None:
                        out.minted.append(minted)
    if not out.minted:
        out.reason = out.reason or REASON_NOTHING
    for row in out.minted:
        await _publish(platform, row)
    return out


async def _publish(platform: Any, row: HarnessLibraryRecord) -> None:
    bus = getattr(platform, "event_bus", None)
    if bus is None:
        return
    from ..core.events import EventType
    from .sessions import session_id_for

    summary = row.text.splitlines()[0][:160] if row.kind == KIND_LESSON and row.text else row.file
    try:
        await bus.publish(EventType.LIBRARY_PROPOSAL, {
            "id": row.id, "kind": row.kind, "folder_name": _folder_name(row.folder),
            "thread_id": row.thread_id, "summary": summary,
        }, session_id=session_id_for(row.thread_id))
    except Exception:  # noqa: BLE001 — a bus hiccup never loses the row
        log.debug("library.proposal publish failed", exc_info=True)


#: The turn's end schedules a reflection by itself (production). The test
#: suite turns this off for every bare test (``tests/conftest.py``) so a
#: background model call never lands in another test's exact counts.
AUTO_REFLECT = True

#: Reflections running in the background (kept so they are not collected,
#: and so tests and the shutdown can wait for them).
_PENDING: set[asyncio.Task] = set()
_RUNNING_KEYS: set[str] = set()
#: "thread:turn" -> its running reflection (so a rewind can stop it).
_TASKS: dict[str, asyncio.Task] = {}


def schedule(platform: Any, thread_id: str, turn_id: str) -> "asyncio.Task | None":
    """Start the reflection for a finished turn in the background (at most one
    per turn). Must be called on the loop. Never raises."""
    if not AUTO_REFLECT:
        return None
    key = f"{thread_id}:{turn_id}"
    if key in _RUNNING_KEYS:
        return None
    try:
        _RUNNING_KEYS.add(key)
        task = asyncio.get_running_loop().create_task(reflect(platform, thread_id, turn_id))
    except RuntimeError:
        _RUNNING_KEYS.discard(key)
        return None
    _PENDING.add(task)
    _TASKS[key] = task

    def _done(t: asyncio.Task) -> None:
        _PENDING.discard(t)
        _RUNNING_KEYS.discard(key)
        if _TASKS.get(key) is t:
            _TASKS.pop(key, None)

    task.add_done_callback(_done)
    return task


def cancel_reflections(thread_id: str, turn_ids: Any) -> int:
    """Stop the background reflections of these turns (a rewind took them
    away). Returns how many were stopped. Call on the loop."""
    stopped = 0
    for turn_id in turn_ids or []:
        task = _TASKS.get(f"{thread_id}:{turn_id}")
        if task is not None and not task.done():
            task.cancel()
            stopped += 1
    return stopped


async def drain(timeout: float = 30.0) -> None:
    """Wait for the background reflections started so far."""
    tasks = [t for t in list(_PENDING) if not t.done()]
    if tasks:
        await asyncio.wait(tasks, timeout=timeout)


# --------------------------------------------------------------------------- #
# the user's decisions
# --------------------------------------------------------------------------- #


def _decide(engine: Any, item_id: str, status: str) -> HarnessLibraryRecord:
    with session_scope(engine) as db:
        row = db.get(HarnessLibraryRecord, str(item_id or ""))
        if row is None:
            raise LibraryRefused("No such suggestion.", 404)
        if row.status != PROPOSED:
            raise LibraryRefused(f"This suggestion was already {row.status}.", 409)
        row.status = status
        row.decided_at = utcnow()
        db.add(row)
        db.commit()
        db.refresh(row)
        db.expunge(row)
        return row


def decline(engine: Any, item_id: str) -> HarnessLibraryRecord:
    return _decide(engine, item_id, DECLINED)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.ij-{new_id('w')}")
    tmp.write_bytes(text.encode("utf-8"))
    os.replace(tmp, path)


def accept(engine: Any, item_id: str, *, known_tools: Any = None) -> HarnessLibraryRecord:
    """Accept a proposal. A LESSON is kept (it rides the next sessions in
    that folder). A LAYER EDIT is written into the folder's harness file now —
    refused honestly when the file moved since it was proposed (``stale``),
    when the folder cannot be written, or when the folder's harness layer
    would not be valid with it (the file is put back as it was; nothing is
    left half-written). BLOCKING."""
    from .components import read_project_layer
    from .sessions import folder_problem

    row = get(engine, item_id)
    if row is None:
        raise LibraryRefused("No such suggestion.", 404)
    if row.status != PROPOSED:
        raise LibraryRefused(f"This suggestion was already {row.status}.", 409)
    if row.kind == KIND_LESSON:
        return _decide(engine, item_id, ACCEPTED)
    if row.file not in EDIT_FILES:
        raise LibraryRefused("This suggestion names a file Iron Jarvis does not edit.", 409)
    problem = folder_problem(row.folder)
    if problem:
        raise LibraryRefused(f"The folder cannot be written now: {problem}", 409)
    current = read_layer_file(row.folder, row.file)
    if current is None or current != row.before:
        _decide(engine, item_id, STALE)
        raise LibraryRefused(STALE_SENTENCE, 409)
    path = _harness_dir(row.folder) / row.file
    existed = path.exists()
    try:
        old_bytes = path.read_bytes() if existed else None
    except OSError as exc:
        raise LibraryRefused(f"The file could not be read ({exc.strerror or exc}).", 409) from None
    try:
        _atomic_write(path, row.after)
    except OSError as exc:
        raise LibraryRefused(f"The file could not be written ({exc.strerror or exc}).", 409) from None
    layer = read_project_layer(row.folder, known_tools=known_tools, session_id=None)
    if not layer.used:
        # All or nothing: put the file back exactly as it was.
        try:
            if old_bytes is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(old_bytes)
        except OSError:
            log.warning("putting %s back failed", path, exc_info=True)
        raise LibraryRefused(INVALID_SENTENCE.format(note=layer.note or "it could not be read"), 409)
    return _decide(engine, item_id, ACCEPTED)


def forget(engine: Any, item_id: str) -> dict[str, Any]:
    """Forget an ACCEPTED lesson (the row is deleted; it no longer rides any
    session). A proposal is declined, not forgotten; an accepted edit lives in
    the folder's own file now (edit or delete that file). BLOCKING."""
    with session_scope(engine) as db:
        row = db.get(HarnessLibraryRecord, str(item_id or ""))
        if row is None:
            raise LibraryRefused("No such lesson.", 404)
        if row.kind != KIND_LESSON or row.status != ACCEPTED:
            raise LibraryRefused("Only a kept lesson can be forgotten.", 409)
        db.delete(row)
        db.commit()
    return {"forgotten": item_id}


__all__ = [
    "EDIT_FILES",
    "Evidence",
    "LibraryRefused",
    "accept",
    "check_lines",
    "decline",
    "drain",
    "forget",
    "library_lines",
    "reflect",
    "schedule",
    "turn_evidence",
    "validate_edit",
    "view",
]
