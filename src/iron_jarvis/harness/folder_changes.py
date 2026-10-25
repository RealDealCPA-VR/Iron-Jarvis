"""WHAT A REPLY CHANGED THROUGH COMMANDS — the finish check's second witness.

The finish check (``harness/finish``) reads what a Build reply changed from
its undo JOURNAL: the files the file tools wrote. A command writes no journal
row. The part 3 Phase 2 measurement lost ``hard-expression-evaluator`` twice
for exactly that: Jarvis on Claude wrote ``calc.py`` with a bash heredoc
through its ``Bash`` tool, the ledger showed no file change, the check never
ran, and the one-argument ``min`` / ``max`` point it exists to catch went
unchecked.

So, BESIDE the journal (never instead of it), the check keeps a SNAPSHOT of
the session folder taken when the reply STARTS — before any tool of it runs —
and compares it with the folder as it is when the model is about to end the
reply: the files created, changed and deleted (a move — the same name, size
and time under another folder — is neither). A file counts as changed by the
reply when the journal says so OR this comparison says so.

What a snapshot holds: every file's size and modification time (ns) under the
folder, walked with ``list_files``' own pruning (``tools/builtins._SKIP_DIRS``
and every folder whose name starts with "." — except the few that hold a
project's own source, :data:`SOURCE_DOT_DIRS`: a CI workflow a command edits
is a change) and its file cap, plus folders the folder's ``.gitignore``
ignores (``finish.GitIgnore``, EVERY nested one read). Its deadline is the
project map's (:data:`WALK_DEADLINE_S`): both walk at the start of every
reply. No file is read and nothing is hashed: a change that keeps a file's
size AND its modification time to the nanosecond (an archive restoring an
older copy with its old time) is not seen.

NOT CHANGES: caches and a tool's droppings — ``__pycache__`` / ``*.pyc``,
``.pytest_cache``, ``.mypy_cache``, ``.ruff_cache``, ``node_modules`` (its
``.cache`` too), ``.next``, coverage output, ``*.egg-info`` — and whatever the
folder's ``.gitignore`` ignores. A reply that only read files and ran its
tests changed nothing.

WHEN THE COMPARISON COUNTS: only when the reply ran something that can change
a file without the journal (:data:`UNJOURNALED_TOOLS`, an app's ``mcp__``
tool, a user-made command tool). A reply that only read files changed nothing
whatever the folder did meanwhile (the user's own editor, a server an earlier
reply started): the snapshot is then not even taken a second time.

AN HONEST FALLBACK: a folder that cannot be compared — more files than the
cap, a walk past the deadline, an unreadable folder, a carried-on reply whose
first snapshot this process no longer has — is never half compared: the
check falls back to the journal alone, and says why (:attr:`Snapshot.problem`).

MIRROR NOTE (lock-step): the two chat lanes reach this module only through
``finish.start`` / ``FinishCheck.prepare`` (``daemon/chat_turn.finish_for_turn``
is the one seam both call).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

log = logging.getLogger(__name__)

#: Tools that can change a file WITHOUT an undo-journal row: a command, code,
#: a long-running program and what is typed into it, a hand-off to an agent
#: or a workflow. Every other member of ``tools.registry._MUTATING_TOOLS`` is
#: a file tool that writes its own journal row (:data:`JOURNALED_TOOLS`) — a
#: pin keeps the two lists covering that registry list, so a new mutating
#: tool must be placed in one of them.
UNJOURNALED_TOOLS = frozenset({
    "shell", "process_start", "process_write", "run_code", "code_run", "repl",
    "delegate", "delegate_remote", "spawn_agent", "workflow_run",
})
#: The registry's mutating tools that write the undo journal themselves.
JOURNALED_TOOLS = frozenset({
    "write_file", "edit_file", "multi_edit", "apply_patch", "notebook_edit",
    "write_document", "convert_document", "redact_pii", "pdf_arrange", "pdf_split",
    "excel_edit", "excel_apply_spec", "batch_documents", "docx_edit", "pdf_form_fill",
    "image_convert", "image_resize", "pixio_generate",
})

#: Folders that are caches or build output beside list_files' own pruning.
_CACHE_DIRS = frozenset({"htmlcov", "lcov-report", "__pypackages__"})
#: Dot-folders that hold the PROJECT's own source — CI, a dev container, git
#: hooks, release notes, a component catalog — walked, unlike every other
#: folder whose name starts with "." (caches, virtualenvs, a tool's state: a
#: test run writes there, the reply did not change the project). Review: a
#: model that edited ``.github/workflows/ci.yml`` with a command changed
#: files the check never saw.
SOURCE_DOT_DIRS = frozenset({
    ".github", ".gitlab", ".circleci", ".devcontainer", ".husky", ".changeset", ".storybook",
})
#: The walk's deadline: the project map's (``project_map.WALK_DEADLINE_S``),
#: not list_files' 10 s for one call — this walk runs at the START of every
#: Build reply, before its first model call, and again at its end (review: on
#: slow storage the user waited up to 10 s before each reply's first word).
WALK_DEADLINE_S = 3.0
#: How long past its deadline a walk is waited for. The deadline is read
#: between folders, so ONE listing that never returns (a network folder gone
#: away) cannot be cut short inside the walk: the reply stops waiting, the
#: walk is left to end on its own thread, and no second walk of that folder
#: starts while it runs (:func:`walk_bounded`).
WALK_GRACE_S = 2.0
_CACHE_DIR_SUFFIXES = (".egg-info",)
#: Files that are a cache, coverage output, a checker's dropping (eslint's
#: and stylelint's caches, the mypy daemon's status file, tsc's
#: ``*.tsbuildinfo`` — written with ``incremental`` / ``composite`` even under
#: ``--noEmit``, so a project's own checker after an edit leaves one) or an
#: operating system's. (mypy's and ruff's own caches are dot-folders, never
#: walked.)
_CACHE_FILES = frozenset({
    ".coverage", "coverage.xml", "lcov.info", "coverage-final.json", "coverage-summary.json",
    "clover.xml", ".eslintcache", ".stylelintcache", ".dmypy.json",
    ".DS_Store", "Thumbs.db", "desktop.ini",
})
_CACHE_FILE_SUFFIXES = (".pyc", ".pyo", ".tsbuildinfo")
_CACHE_FILE_PREFIXES = (".coverage.",)

#: Why a folder could not be compared — said to the model (and logged).
PROBLEM_TOO_MANY = "this folder has more than {limit} files to compare"
PROBLEM_TOO_SLOW = "reading this folder took longer than {seconds:g} s"
PROBLEM_UNREADABLE = "this folder could not be read"
#: (Review: it said "(Iron Jarvis restarted after the reply was cut off)" as
#: fact; a snapshot is also let go when newer replies of other sessions push
#: it out — a carry-on can come hours later.)
PROBLEM_NOT_KEPT = (
    "the folder as it was when this reply began is no longer kept (it is kept only for the "
    "newest replies of a few sessions, and not across a restart)"
)


def _caps() -> tuple[int, float]:
    """The walk's caps: ``list_files``' file count, and the project map's
    start-of-turn deadline (:data:`WALK_DEADLINE_S`)."""
    from ..tools import builtins as B

    return int(B._MAX_WALK_ENTRIES), float(WALK_DEADLINE_S)


def _skip_dirs() -> frozenset[str]:
    from ..tools import builtins as B

    return frozenset(B._SKIP_DIRS)


def cache_dir(name: str) -> bool:
    """Is a folder of this name a cache or build output (never walked)?"""
    return name in _CACHE_DIRS or name.endswith(_CACHE_DIR_SUFFIXES)


def ignore_for(root: Any) -> Any:
    """The folder's ``.gitignore`` rules for a walk or a comparison: EVERY
    nested ``.gitignore`` read (review: ``finish.GitIgnore`` reads at most 64
    folders' for the few files the new-files list asks about; a walk asks
    about every folder, and past the 64th a nested ``.gitignore`` was never
    read — on this PC's own Iron Jarvis checkout ``desktop/.gitignore``'s
    ``release/`` (3,696 files) was walked and the snapshot always said "more
    than 5000 files"). Bounded by the walk itself (its file cap and
    deadline) and by the paths a comparison names."""
    from .finish import GitIgnore

    return GitIgnore(root, max_files=None)


def cache_file(name: str) -> bool:
    """Is a file of this name a cache or a tool's dropping (never a change)?"""
    return (name in _CACHE_FILES or name.endswith(_CACHE_FILE_SUFFIXES)
            or name.startswith(_CACHE_FILE_PREFIXES))


def unjournaled(name: str, custom: "frozenset[str] | set[str]" = frozenset()) -> bool:
    """Can a call of this tool change a file without an undo-journal row?"""
    n = str(name or "")
    return n in UNJOURNALED_TOOLS or n.startswith("mcp__") or n in custom


# --------------------------------------------------------------------------- #
# the snapshot
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Snapshot:
    """The folder's files when it was walked: ``rel posix path -> (size,
    mtime_ns)``. ``problem`` "" = complete; otherwise why it cannot be
    compared (and ``files`` is empty — never a half picture)."""

    root: str
    files: Mapping[str, tuple[int, int]] = field(default_factory=dict)
    #: folders inside it that could not be read (their files are compared
    #: with neither side)
    skipped: frozenset[str] = frozenset()
    problem: str = ""
    seconds: float = 0.0

    @property
    def usable(self) -> bool:
        return not self.problem


def _is_junction(entry: Any) -> bool:
    fn = getattr(entry, "is_junction", None)
    try:
        return bool(fn()) if fn is not None else False
    except OSError:
        return True


def take(
    folder: Any,
    *,
    limit: "int | None" = None,
    deadline_s: "float | None" = None,
    ignore: Any = None,
    clock: Callable[[], float] = time.monotonic,
) -> Snapshot:
    """Walk ``folder`` (BLOCKING; never raises). Bounded by ``list_files``'
    caps unless given: more files than ``limit`` or a walk past
    ``deadline_s`` is a :class:`Snapshot` with a ``problem`` and no files."""
    cap, deadline = _caps()
    limit = cap if limit is None else int(limit)
    deadline = deadline if deadline_s is None else float(deadline_s)
    started = clock()
    try:
        root = Path(str(folder)).resolve()
    except (OSError, RuntimeError, ValueError):
        return Snapshot(root=str(folder), problem=PROBLEM_UNREADABLE)
    try:
        from ..core.fs_policy import fs_read_ok

        if not fs_read_ok(str(root))[0] or not root.is_dir():
            return Snapshot(root=str(root), problem=PROBLEM_UNREADABLE)
        if ignore is None:
            ignore = ignore_for(root)
        skip = _skip_dirs()
        files: dict[str, tuple[int, int]] = {}
        skipped: set[str] = set()
        stack: list[tuple[str, str]] = [("", str(root))]
        while stack:
            rel_dir, path = stack.pop()
            try:
                with os.scandir(path) as it:
                    entries = list(it)
            except OSError:
                if not rel_dir:
                    return Snapshot(root=str(root), problem=PROBLEM_UNREADABLE)
                skipped.add(rel_dir)
                continue
            if not any(e.name == ".gitignore" for e in entries):
                tell = getattr(ignore, "no_file_in", None)
                if tell is not None:
                    tell(rel_dir)  # every folder's rules are read: never open a file that is not there
            for entry in entries:
                name = entry.name
                rel = f"{rel_dir}/{name}" if rel_dir else name
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if _is_junction(entry):
                            continue  # never followed: it can lead outside the folder
                        if name in skip or cache_dir(name) or (
                                name.startswith(".") and name not in SOURCE_DOT_DIRS):
                            continue
                        if ignore is not None and ignore.ignored(rel, is_dir=True):
                            continue
                        stack.append((rel, entry.path))
                        continue
                    if cache_file(name):
                        continue
                    # A file — or a link (to a file or a folder), recorded as
                    # the link itself: never followed.
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                files[rel] = (int(st.st_size), int(st.st_mtime_ns))
                if len(files) > limit:
                    return Snapshot(root=str(root), problem=PROBLEM_TOO_MANY.format(limit=limit),
                                    seconds=clock() - started)
            # `>=`, as list_files: a deadline of 0 means "no time at all".
            if clock() - started >= deadline:
                return Snapshot(root=str(root), problem=PROBLEM_TOO_SLOW.format(seconds=deadline),
                                seconds=clock() - started)
        return Snapshot(root=str(root), files=files, skipped=frozenset(skipped), seconds=clock() - started)
    except Exception:  # noqa: BLE001 — a snapshot never breaks a reply
        log.debug("finish check: walking %s failed", folder, exc_info=True)
        return Snapshot(root=str(folder), problem=PROBLEM_UNREADABLE)


def unavailable(root: Any, problem: str) -> Snapshot:
    """A snapshot that cannot be compared, and why."""
    return Snapshot(root=str(root), problem=str(problem or PROBLEM_UNREADABLE))


def too_slow(root: Any) -> Snapshot:
    """A walk given up on (:func:`walk_bounded`)."""
    return unavailable(root, PROBLEM_TOO_SLOW.format(seconds=_caps()[1]))


#: Folders whose walk was GIVEN UP ON and still runs (normalised path ->
#: that walk's token): one stalled thread per folder, never a pile. A walk
#: that is merely running keeps nobody out (two Build sessions can share a
#: folder: Branch from here).
_STALLED: dict[str, object] = {}
_STALLED_LOCK = threading.Lock()


async def walk_bounded(folder: Any, fn: Callable[..., Any], *args: Any) -> Any:
    """``fn(*args)`` — a BLOCKING walk of ``folder`` — off the loop, waited for
    at most the walk's deadline and :data:`WALK_GRACE_S`; None when it took
    longer (it is left to end on its thread), or when an earlier walk of this
    folder that was given up on is still running (never a second one beside
    it: a folder that stalls once stalls again). Never touches the disk on the
    loop (the folder's name is normalised as text)."""
    import asyncio

    try:
        key = os.path.normcase(os.path.abspath(str(folder)))
    except (OSError, ValueError):
        key = str(folder)
    with _STALLED_LOCK:
        if key in _STALLED:
            log.info("finish check: an earlier walk of %s that was given up on still runs", folder)
            return None
    token = object()
    done = threading.Event()

    def run() -> Any:
        try:
            return fn(*args)
        finally:
            done.set()
            with _STALLED_LOCK:
                if _STALLED.get(key) is token:
                    del _STALLED[key]

    try:
        return await asyncio.wait_for(asyncio.to_thread(run), _caps()[1] + WALK_GRACE_S)
    except asyncio.TimeoutError:
        with _STALLED_LOCK:
            if not done.is_set():
                _STALLED[key] = token
        log.info("finish check: reading %s took longer than its deadline; left to finish on its own", folder)
        return None


# --------------------------------------------------------------------------- #
# the comparison
# --------------------------------------------------------------------------- #


@dataclass
class Changes:
    """What differs between two snapshots of one folder (rel posix paths,
    sorted). A move is neither created nor deleted."""

    created: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    moved: list[tuple[str, str]] = field(default_factory=list)

    @property
    def any(self) -> bool:
        return bool(self.created or self.changed or self.deleted or self.moved)

    def paths(self) -> list[str]:
        """Every path this comparison names (a move: both ends)."""
        out = [*self.created, *self.changed, *self.deleted]
        for a, b in self.moved:
            out += [a, b]
        return out


def _under(rel: str, folders: "frozenset[str] | set[str]") -> bool:
    return any(rel == f or rel.startswith(f + "/") for f in folders)


def compare(before: Snapshot, after: Snapshot, ignore: Any = None) -> Changes:
    """The differences between two COMPLETE snapshots of one folder (BLOCKING
    only through ``ignore``, a ``finish.GitIgnore`` of the folder as it is
    now; never raises). A file inside a folder either walk could not read is
    left out (it was not seen both times)."""
    out = Changes()
    if not before.usable or not after.usable:
        return out
    try:
        skipped = set(before.skipped) | set(after.skipped)

        def keep(rel: str) -> bool:
            if _under(rel, skipped) or rel.split("/", 1)[0] == ".git":
                return False
            try:
                return not (ignore is not None and ignore.ignored(rel))
            except Exception:  # noqa: BLE001
                return True

        b, a = before.files, after.files
        created = sorted(r for r in a.keys() - b.keys() if keep(r))
        deleted = sorted(r for r in b.keys() - a.keys() if keep(r))
        out.changed = sorted(r for r in a.keys() & b.keys() if a[r] != b[r] and keep(r))
        # A move keeps the file's name, size and modification time (os.replace,
        # `mv`, a watcher sending a file to done/): pair each one up.
        pending: dict[tuple[str, int, int], list[str]] = {}
        for r in created:
            size, mtime = a[r]
            pending.setdefault((r.rsplit("/", 1)[-1], size, mtime), []).append(r)
        moved_to: set[str] = set()
        gone: list[str] = []
        for r in deleted:
            size, mtime = b[r]
            dests = pending.get((r.rsplit("/", 1)[-1], size, mtime))
            if dests:
                dest = dests.pop(0)
                moved_to.add(dest)
                out.moved.append((r, dest))
            else:
                gone.append(r)
        out.created = [r for r in created if r not in moved_to]
        out.deleted = gone
    except Exception:  # noqa: BLE001
        log.debug("finish check: comparing the folder failed", exc_info=True)
        return Changes()
    return out


# --------------------------------------------------------------------------- #
# a carried-on reply's first snapshot
# --------------------------------------------------------------------------- #

#: A reply an account limit cut off goes on in a NEW turn (Resume when ready /
#: Continue on another account) that covers the whole reply from the question
#: it continues — so it must compare with the folder as it was when THAT reply
#: began. The newest reply's first snapshot is kept per Build session, in this
#: process only: after a restart the carried-on reply falls back to the
#: journal alone, and says so (:data:`PROBLEM_NOT_KEPT`).
MAX_KEPT_REPLIES = 4

_KEPT: "OrderedDict[str, tuple[str, Snapshot, Any]]" = OrderedDict()
_KEPT_LOCK = threading.Lock()


def keep(session_id: str, turn_id: str, snapshot: Snapshot, extra: Any = None) -> None:
    """Keep a reply's first snapshot (with ``extra``, the check's other
    first readings) for a carry-on of it. A session keeps one: its newest
    reply's. Nothing is kept without a turn id."""
    sid, turn = str(session_id or ""), str(turn_id or "")
    if not sid or not turn:
        return
    with _KEPT_LOCK:
        _KEPT[sid] = (turn, snapshot, extra)
        _KEPT.move_to_end(sid)
        while len(_KEPT) > MAX_KEPT_REPLIES:
            _KEPT.popitem(last=False)


def kept(session_id: str, turn_id: str, root: Any) -> "tuple[Snapshot, Any] | None":
    """The first snapshot of the reply ``turn_id`` of this session, taken of
    this ``root`` — or None."""
    sid, turn = str(session_id or ""), str(turn_id or "")
    if not sid or not turn:
        return None
    with _KEPT_LOCK:
        got = _KEPT.get(sid)
    if got is None or got[0] != turn:
        return None
    try:
        same = os.path.normcase(str(Path(str(root)).resolve())) == os.path.normcase(got[1].root)
    except (OSError, RuntimeError, ValueError):
        same = False
    return (got[1], got[2]) if same else None


def forget_kept() -> None:
    """Forget every kept snapshot (a restart; tests)."""
    with _KEPT_LOCK:
        _KEPT.clear()


__all__ = [
    "Changes",
    "JOURNALED_TOOLS",
    "PROBLEM_NOT_KEPT",
    "PROBLEM_TOO_MANY",
    "PROBLEM_TOO_SLOW",
    "PROBLEM_UNREADABLE",
    "SOURCE_DOT_DIRS",
    "Snapshot",
    "UNJOURNALED_TOOLS",
    "WALK_DEADLINE_S",
    "WALK_GRACE_S",
    "cache_dir",
    "cache_file",
    "compare",
    "forget_kept",
    "ignore_for",
    "keep",
    "kept",
    "take",
    "too_slow",
    "unavailable",
    "unjournaled",
    "walk_bounded",
]
