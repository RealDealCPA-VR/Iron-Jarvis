"""The required files, looked at ON DISK; and the words built from that look
(v1.481.0).

Every sentence here is drawn from the check, never from the model's plan or
its reply: a file is "done" only when it exists, is not empty and opens (a
workbook loads, a document's package is whole, a JSON file parses, a text file
decodes). BLOCKING (stats, opens): the lanes call these off the event loop.
"""

from __future__ import annotations

import json
import os
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .scope import TaskScope, snapshot

#: Past this size a file is not opened (it exists and is not empty: that is
#: said, and that it was not opened).
MAX_OPEN_BYTES = 60 * 1024 * 1024
#: The whole check stops here; what is left is "not checked".
CHECK_DEADLINE_S = 15.0
_TEXT_EXTS = frozenset({
    ".md", ".markdown", ".txt", ".csv", ".tsv", ".log", ".html", ".htm", ".xml",
    ".yaml", ".yml", ".toml", ".ini", ".py", ".js", ".ts", ".css", ".sql", ".rtf",
})
_ZIP_KINDS = {
    ".xlsx": "xl/workbook.xml", ".xlsm": "xl/workbook.xml", ".docx": "word/document.xml",
    ".pptx": "ppt/presentation.xml", ".odt": "content.xml", ".ods": "content.xml",
}
_MAGIC = {
    ".png": b"\x89PNG", ".jpg": b"\xff\xd8", ".jpeg": b"\xff\xd8", ".gif": b"GIF8",
    ".pdf": b"%PDF-", ".zip": b"PK",
}
#: How many named items a list in a note holds before "and N more".
MAX_NAMED = 8

DONE = "ok"


@dataclass(frozen=True)
class FileState:
    rel: str
    path: Path
    status: str   # ok | missing | empty | folder | unreadable | unchecked
    detail: str
    size: int = 0

    @property
    def done(self) -> bool:
        return self.status == DONE


def _size_words(size: int) -> str:
    if size < 1024:
        return f"{size} bytes"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _decode(data: bytes) -> "str | None":
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            return None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


def _opens(path: Path, size: int) -> "tuple[bool, str]":
    """(opens, why not). Never raises."""
    ext = path.suffix.lower()
    if size > MAX_OPEN_BYTES:
        return True, "too large to open here; not opened"
    try:
        if ext in _ZIP_KINDS:
            if not zipfile.is_zipfile(path):
                return False, "not a valid Office file (it is not a package)"
            with zipfile.ZipFile(path) as zf:
                names = set(zf.namelist())
            if _ZIP_KINDS[ext] not in names:
                return False, f"its package has no {_ZIP_KINDS[ext]}"
            if ext in (".xlsx", ".xlsm"):
                import openpyxl

                wb = openpyxl.load_workbook(path, read_only=True)
                try:
                    if not wb.sheetnames:
                        return False, "the workbook has no sheets"
                finally:
                    wb.close()
            return True, ""
        with open(path, "rb") as fh:
            data = fh.read(min(size, 4 * 1024 * 1024))
        magic = _MAGIC.get(ext)
        if magic is not None:
            if not data.startswith(magic):
                return False, f"it does not start like a {ext[1:].upper()} file"
            if ext == ".pdf" and size <= 4 * 1024 * 1024 and b"%%EOF" not in data[-2048:]:
                return False, "the PDF has no end marker (it was cut off)"
            return True, ""
        if ext == ".json":
            text = _decode(data)
            if text is None:
                return False, "it is not UTF-8 text"
            json.loads(text)
            return True, ""
        if ext in _TEXT_EXTS:
            text = _decode(data)
            if text is None:
                return False, "it is not UTF-8 text"
            if not text.strip():
                return False, "it holds only blank space"
            return True, ""
        return True, ""
    except Exception as exc:  # noqa: BLE001 - "does not open" is the answer
        return False, f"it does not open ({type(exc).__name__}: {str(exc)[:120]})"


def check_file(path: Path) -> "tuple[str, str, int]":
    """``(status, detail, size)`` of one required file."""
    try:
        if not path.exists():
            return "missing", "not on disk", 0
        if path.is_dir():
            return "folder", "a folder is there, not a file", 0
        size = path.stat().st_size
    except OSError as exc:
        return "unreadable", f"it cannot be read ({type(exc).__name__})", 0
    if size == 0:
        return "empty", "the file is empty", 0
    ok, why = _opens(path, size)
    if not ok:
        return "unreadable", why, size
    return DONE, (f"{_size_words(size)}, opens" + (f"; {why}" if why else "")), size


def check_deliverables(scope: TaskScope, *, deadline_s: float = CHECK_DEADLINE_S) -> list[FileState]:
    """Every required file's state. Never raises; bounded."""
    out: list[FileState] = []
    stop = time.monotonic() + deadline_s
    for item in scope.deliverables:
        rel = scope.rel(item.path)
        if time.monotonic() > stop:
            out.append(FileState(rel, item.path, "unchecked", "not checked (the check ran out of time)"))
            continue
        try:
            status, detail, size = check_file(item.path)
        except Exception as exc:  # noqa: BLE001
            status, detail, size = "unreadable", f"it cannot be checked ({type(exc).__name__})", 0
        out.append(FileState(rel, item.path, status, detail, size))
    return out


def outside_changes(scope: TaskScope) -> list[str]:
    """What changed in the task folder OUTSIDE the folders it may write, since
    the turn began: new folders (with how many files), new, changed and removed
    files. [] when the task has no write rule, or nothing changed."""
    if not scope.enforced:
        return []
    skip = tuple(scope.write_roots) + ((scope.scratch,) if scope.scratch else ())
    now, _complete = snapshot(scope.root, skip)
    before = scope.baseline
    added = sorted(set(now) - set(before), key=lambda r: (r.count(os.sep), r))
    removed = sorted(set(before) - set(now))
    changed = sorted(
        r for r in set(now) & set(before)
        if not now[r][0] and (now[r][1], now[r][2]) != (before[r][1], before[r][2])
    )
    words: list[str] = []
    folded: list[str] = []
    for rel in added:
        if any(rel.startswith(f + os.sep) for f in folded):
            continue
        if now[rel][0]:
            inside = sum(1 for r in now if r.startswith(rel + os.sep) and not now[r][0])
            words.append(f"{rel}{os.sep} (a new folder, {inside} file{'s' if inside != 1 else ''})")
            folded.append(rel)
        else:
            words.append(f"{rel} (a new file)")
    words.extend(f"{rel} (changed)" for rel in changed)
    words.extend(f"{rel} (removed)" for rel in removed)
    return words


def _named(items: list[str]) -> str:
    shown = items[:MAX_NAMED]
    rest = len(items) - len(shown)
    return "; ".join(shown) + (f"; and {rest} more" if rest > 0 else "")


def _folders(scope: TaskScope, paths: Iterable[Path]) -> str:
    return ", ".join(scope.rel(p).rstrip("\\/") + os.sep for p in paths)


def scope_block(scope: TaskScope, armed: Iterable[str] = ()) -> str:
    """The system-prompt block for a scoped turn (both chat lanes add it
    before the history planner, so its cost is priced)."""
    armed = set(armed or ())
    writers = [t for t in ("write_document", "write_file") if t in armed]
    lines = ["", "", "# This task's required files and where it may write (read by Iron Jarvis from the user's message)"]
    lines.append(f"Task folder: {scope.root}")
    if scope.rebase:
        lines.append(
            f"The user's paths are relative to the task folder: `{scope.workspace.name}/` means "
            f"{scope.workspace}. Your tools work in the task folder."
        )
    if scope.deliverables:
        lines.append("Required files (Iron Jarvis checks each one on disk before your reply ends):")
        lines.extend(f"- {scope.rel(d.path)}" for d in scope.deliverables)
    if scope.write_roots:
        lines.append(
            f"Write ONLY in: {_folders(scope, scope.write_roots)}. Iron Jarvis refuses a "
            "write anywhere else, and a helper script in that folder."
        )
    if scope.read_only:
        lines.append(f"Read-only (never change anything here): {_folders(scope, scope.read_only)}")
    if writers or not armed:
        lines.append(
            "Write each required file DIRECTLY with "
            + (" or ".join(writers) if writers else "your file tool (write_document or write_file)")
            + ". Do not write a helper script to produce them."
        )
    else:
        lines.append(
            "None of the tools in this reply can write a file directly: if a required file "
            "cannot be written, say so plainly."
        )
    if scope.scratch is not None:
        lines.append(
            f"For a calculation, use a tool script, or save a helper script in the scratch folder "
            f"{scope.scratch} (outside the task folder), run it, and read what it printed."
        )
    lines.append(
        "A step whose output shows an error (a non-zero exit, a traceback, ImportError, "
        "No module named …) has FAILED: fix it or say it failed. Never carry on as if it worked."
    )
    lines.append(
        "Before you finish, every required file must exist. Never call the task complete "
        "while one is missing. Any report you write must list what was produced, what failed "
        "and what is not done."
    )
    return "\n".join(lines)


def _state_line(s: FileState) -> str:
    return f"- {'done' if s.done else 'NOT DONE'}: {s.rel} ({s.detail})"


def missing_note(states: list[FileState], scope: TaskScope, *, rounds_left: "int | None" = None) -> str:
    """The note that asks the model, once, to finish what is missing. "" when
    every required file is done."""
    bad = [s for s in states if not s.done]
    if not bad:
        return ""
    head = "From Iron Jarvis, not from the user: "
    if rounds_left is not None:
        head += f"only {rounds_left} steps are left in this reply. "
    head += "Iron Jarvis looked for this task's required files on disk:"
    lines = [head, *(_state_line(s) for s in states)]
    lines.append(
        "Write the missing ones now, directly (not through a script), in the folder the "
        "task allows. Then check they exist. If one cannot be done, say so plainly in your "
        "answer and in any report: do not call the task complete."
    )
    return "\n".join(lines)


def final_note(
    scope: TaskScope,
    states: list[FileState],
    *,
    failed: "list[tuple[str, str]] | None" = None,
    refused: "list[tuple[str, str]] | None" = None,
    never_run: "list[str] | None" = None,
    changes: "list[str] | None" = None,
) -> str:
    """The note under the reply: drawn from the check, never from the plan."""
    failed = list(failed or [])
    refused = list(refused or [])
    never_run = list(never_run or [])
    changes = list(changes or [])
    done = [s for s in states if s.done]
    bad = [s for s in states if not s.done]
    total = len(states)
    quiet = (
        not bad and not failed and not never_run and not changes
        and (total < 2 and not scope.enforced)
    )
    if quiet:
        return ""
    lines = ["", "", "_Deliverables check (Iron Jarvis looked on disk, not at the plan):_"]
    if done:
        lines.append("- Done: " + _named([f"{s.rel} ({s.detail})" for s in done]))
    if bad:
        lines.append("- Not done: " + _named([f"{s.rel} ({s.detail})" for s in bad]))
    if failed:
        lines.append("- Failed and not done again: " + _named([f"{label} ({why})" for label, why in failed]))
    if never_run:
        lines.append("- Helper scripts written and never run: " + _named(never_run))
    if refused:
        lines.append("- Writes Iron Jarvis refused: " + _named([label for label, _why in refused]))
    if changes:
        lines.append("- Changed outside the folders this task may write: " + _named(changes))
    if bad:
        lines.append(f"_The task is NOT complete: {len(bad)} of {total} required file{'s' if total != 1 else ''} "
                     f"{'is' if len(bad) == 1 else 'are'} not done._")
    elif total:
        lines.append(f"_All {total} required file{'s are' if total != 1 else ' is'} on disk and open._")
    return "\n".join(lines)


def turn_note(scope: TaskScope, steplog: Any) -> str:
    """The whole note for a finished turn (BLOCKING; never raises)."""
    try:
        states = check_deliverables(scope)
        changes = outside_changes(scope)
        return final_note(
            scope, states,
            failed=steplog.failed_steps() if steplog is not None else [],
            refused=list(getattr(steplog, "refused", []) or []),
            never_run=steplog.never_run() if steplog is not None else [],
            changes=changes,
        )
    except Exception:  # noqa: BLE001 - a note that cannot be built is no note
        return ""


def missing_now(scope: TaskScope, rounds_left: "int | None" = None) -> str:
    """The ask-to-finish note if anything is missing now, else "" (BLOCKING)."""
    try:
        return missing_note(check_deliverables(scope), scope, rounds_left=rounds_left)
    except Exception:  # noqa: BLE001
        return ""
