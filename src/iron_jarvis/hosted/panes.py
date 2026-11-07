"""THE CODE VIEW'S RIGHT PANES (v1.414.0): Changes, Usage and Session for a
Build › Code session — the same column Chat › Code has (the user: "the
icons for changes, remaining usage, files and sessions to the right just as
in the native harness").

Every reader here is READ-ONLY, BLOCKING (the routes hop it off the loop) and
never starts a process of the coding tool, a model call or a probe.

CHANGES — the honest source. The REAL tool writes files itself, outside Iron
Jarvis's undo journal, so the journal knows nothing. Two sources, said by
name on the pane:

* ``git`` (the folder is inside a repository): every file that differs from
  the commit HEAD was on when this session was STARTED HERE (the newest
  first-parent commit no later than the session's ``created_at``), committed
  since or not, new untracked files included (``.gitignore`` applies). LIMITS,
  said: it includes changes the user or another program made in that folder
  meanwhile; a commit whose date was rewritten can land on the wrong side;
  ignored files are not listed. Files the tool's own file tools reported
  editing are marked ``by_tool``.
* ``tool`` (no repository, or git is not installed): the tool's OWN record of
  its file edits (the ``tool`` events with a path and a diff), each edit's
  diff as the tool reported it at the time. LIMITS, said: changes made by a
  command (a script, ``sed -i``) are not listed; a diff is cut at the event's
  own bound; lines may have moved since.

No Undo on either: Iron Jarvis did not make these writes and keeps no copy of
what was there before (the tool's own undo, or git, is the way back).

USAGE — cached only: the session's Iron-Proxy account row (the cached
snapshot, ``harness.build_accounts``), the windows the TOOL itself last
reported (:mod:`.seen`, kept on the ``usage`` events with their time), and the
session's totals summed from its ``usage`` events. A subscription's cost is a
list-price EQUIVALENT (``list_price``), never spend.

SESSION — the branch the folder is on, the instruction files in it, how full
the conversation was at the tool's last report (:mod:`.seen`), and the
programs running under the tool's own process right now (its helpers
included).
"""

from __future__ import annotations

import os
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import seen as SEEN
from .events import one_line

#: How many changed files are listed (the Branch view's own bound).
MAX_FILES = 50
#: A unified diff of one file is cut here (the Branch view's own bound).
DIFF_CAP = 200_000
#: Programs listed under the tool's process.
MAX_PROGRAMS = 20
#: Instruction files a coding tool may read, looked for in the folder.
RULE_FILES: tuple[str, ...] = (
    "CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", "AGENTS.override.md", ".claude/CLAUDE.md",
)
#: Process names that are plumbing, not programs (Windows' console host).
_PLUMBING = frozenset({"conhost.exe"})

NO_FOLDER = "This session has no folder yet: it is made with your first message."
FOLDER_GONE = "This session's folder is not there any more, so nothing can be compared."
ON_SHARE = "This session's folder is on a network share, so its changes are not read here."
GIT_WORDS = (
    "Every file in this folder that differs from where git was when the session "
    "started here, committed since or not (new files included). It includes "
    "changes you or another program made meanwhile; files git ignores are not listed."
)
TOOL_WORDS = (
    "This folder is not in a git repository, so this is {tool}'s own record of the "
    "files it edited with its file tools. Changes made by commands it ran are not "
    "listed, and lines may have moved since."
)
#: v1.414.0 review: git missing is said as git missing (it read "not in a git
#: repository" of a folder that may well be one).
NO_GIT_WORDS = (
    "git is not installed on this PC, so this is {tool}'s own record of the "
    "files it edited with its file tools. Changes made by commands it ran are not "
    "listed, and lines may have moved since."
)
#: v1.414.0 review: git that did not answer is never "no changes" or "not a
#: repository" — both were what the first cut said.
GIT_UNREADABLE = "git could not read this folder's changes, so none are listed: {why}."
GIT_TIMED_OUT = "it did not answer in time"
#: v1.414.0 review: past this many characters of pathspec (Windows' command
#: line holds 32,767) each changed file is read alone, as the first cut did.
PATHSPEC_CHARS = 16_000
#: A changed file whose diff git did not give (said on its row).
DIFF_UNREAD = "its diff could not be read here"
NO_UNDO = "No Undo here: {tool} made these changes itself, so use git or {tool} to take one back."


def _network(path: str) -> bool:
    from ..harness.sessions import _network_spelling

    return _network_spelling(path)


def _key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _created(row: Any) -> datetime:
    when = getattr(row, "created_at", None)
    if not isinstance(when, datetime):
        return datetime.now(timezone.utc)
    return when if when.tzinfo is not None else when.replace(tzinfo=timezone.utc)


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def tool_paths(events: list[dict[str, Any]], folder: str) -> dict[str, list[dict[str, Any]]]:
    """The files the tool's own file tools reported changing (finished, not
    failed), absolute key -> the events in order."""
    out: dict[str, list[dict[str, Any]]] = {}
    for ev in events:
        if ev.get("type") != "tool" or ev.get("kind") not in ("edit", "write"):
            continue
        if ev.get("status") != "done":
            continue
        path = str(ev.get("path") or "").strip()
        if not path or _network(path):
            continue
        if not os.path.isabs(path):
            if not folder:
                continue
            path = os.path.join(folder, path)
        out.setdefault(_key(path), []).append(ev)
    return out


# --------------------------------------------------------------------------- #
# CHANGES
# --------------------------------------------------------------------------- #


def _git(args: list[str], cwd: str, *, input: bytes | None = None) -> Any:
    from ..harness.branches import _g

    return _g(args, cwd, reads=True, input=input)


class GitUnreadable(Exception):
    """git ran (or may have) in a repository and did not answer: the pane says
    so in words, never "no changes" or "not a repository" (v1.414.0 review)."""

    def __init__(self, got: Any) -> None:
        why = GIT_TIMED_OUT if getattr(got, "timed_out", False) else _git_reason(got)
        super().__init__(why)
        self.words = GIT_UNREADABLE.format(why=why)


def _git_reason(got: Any) -> str:
    """git's own first error line, masked and cut (``fatal:`` dropped)."""
    for raw in str(getattr(got, "err", "") or "").splitlines():
        line = raw.strip()
        if line:
            if line.lower().startswith("fatal:"):
                line = line[6:].strip()
            return one_line(line, 160).rstrip(".")
    return "it stopped with an error"


def _base_commit(folder: str, since: datetime) -> str | None:
    """The commit HEAD was on when the session started (the newest
    first-parent commit no later than ``since``), the empty tree when every
    commit is newer (or there is none yet), None when this is no repository.
    Raises :class:`GitUnreadable` when git did not answer (a time limit, a
    repository it refuses to read — "dubious ownership"): that is not "no
    repository"."""
    inside = _git(["rev-parse", "--is-inside-work-tree"], folder)
    if not inside.ok:
        if not inside.timed_out and "not a git repository" in str(inside.err or "").lower():
            return None
        raise GitUnreadable(inside)
    if inside.out.strip() != "true":
        return None
    # git dates have whole seconds and ``--before`` includes its second: a
    # commit made in the session's first second must not become the base.
    start = since.astimezone(timezone.utc).replace(microsecond=0) - timedelta(seconds=1)
    stamp = start.strftime("%Y-%m-%d %H:%M:%S +0000")
    got = _git(["rev-list", "-1", "--first-parent", f"--before={stamp}", "HEAD"], folder)
    if not got.ok:
        # No commits yet (HEAD names no commit: ``rev-parse --verify -q``
        # exits 1 and says nothing) reads against the empty tree; anything
        # else (a time limit) is not that — the empty tree would list every
        # tracked file as created.
        if got.timed_out:
            raise GitUnreadable(got)
        head = _git(["rev-parse", "--verify", "-q", "HEAD"], folder)
        if head.ok or head.timed_out or head.code != 1:
            raise GitUnreadable(got)
    sha = got.out.strip() if got.ok else ""
    if sha:
        return sha
    empty = _git(["hash-object", "-t", "tree", "--stdin"], folder, input=b"")
    return empty.out.strip() if empty.ok and empty.out.strip() else None


def git_changes(folder: str, since: datetime, *, base: str | None = None,
                target: str | None = None) -> dict[str, Any] | None:
    """The folder's changes since the session started, from git (the Branch
    view's shape and bounds); None = not a repository / git missing.

    v1.420.0 (C4.12): *base* and *target* (two TREES, :func:`snapshot_tree`)
    compare a turn's start with the folder now instead — new files are in
    the trees (``git add -A`` into a throwaway index), so no untracked listing
    is read."""
    from ..harness import branches as B

    if B.git_path() is None:
        return None
    if base is None:
        base = _base_commit(folder, since)
        target = None
    if base is None:
        return None
    revs = [base, target] if target else [base]
    wt = Path(folder)
    status = _git(["diff", "--no-color", "--no-ext-diff", "-M", "--relative", "--name-status", "-z", *revs], folder)
    if not status.ok:
        raise GitUnreadable(status)
    numstat = _git(["diff", "--no-color", "--no-ext-diff", "-M", "--relative", "--numstat", "-z", *revs], folder)
    if not numstat.ok:
        # Without the counts every file would read as a binary file.
        raise GitUnreadable(numstat)
    counts: dict[str, tuple[int | None, int | None]] = {}
    if numstat.ok:
        parts = B._split_z(numstat.out)
        i = 0
        while i < len(parts):
            head = parts[i]
            i += 1
            bits = head.split("\t") if head else []
            if len(bits) < 3:
                continue
            added = None if bits[0] == "-" else int(bits[0])
            removed = None if bits[1] == "-" else int(bits[1])
            if bits[2] == "":
                counts[parts[i + 1] if i + 1 < len(parts) else ""] = (added, removed)
                i += 2
            else:
                counts[bits[2]] = (added, removed)
    entries: list[tuple[str, str, str]] = []
    parts = B._split_z(status.out)
    i = 0
    while i < len(parts):
        code = parts[i]
        i += 1
        if not code:
            continue
        if code[:1] in ("R", "C"):
            entries.append((code[:1], parts[i], parts[i + 1] if i + 1 < len(parts) else ""))
            i += 2
        else:
            entries.append((code[:1], parts[i] if i < len(parts) else "", parts[i] if i < len(parts) else ""))
            i += 1
    changes: list[dict[str, Any]] = []
    more = len(entries) > MAX_FILES
    shown = entries[:MAX_FILES]
    for (code, old, new), patch in zip(shown, _patches(folder, base, shown, target)):
        rel = new or old
        added, removed = counts.get(rel, (None, None))
        row: dict[str, Any] = {
            "path": str(wt / rel), "rel": rel, "name": rel.rsplit("/", 1)[-1],
            "status": {"A": "created", "D": "deleted"}.get(code, "modified"),
            "added": added, "removed": removed, "diff": patch or "",
        }
        if code == "R" and old != new:
            row["old_rel"] = old
        notes = [f"renamed from {old}"] if code == "R" and old != new else []
        if added is None and removed is None:
            row.update(binary=True, diff="binary file", size=B._size_now(wt / rel))
        elif patch is None:
            # v1.414.0 review: said, never an empty diff under real counts.
            notes.append(DIFF_UNREAD)
        if notes:
            row["note"] = "; ".join(notes)
        if len(row["diff"]) > DIFF_CAP:
            row["diff"], row["truncated"] = row["diff"][:DIFF_CAP], True
        changes.append(row)
    if target:
        return {"changes": changes, "truncated_files": more, "base": base[:12]}
    untracked = _git(["ls-files", "--others", "--exclude-standard", "-z"], folder)
    if not untracked.ok:
        raise GitUnreadable(untracked)
    for rel in [p for p in B._split_z(untracked.out) if p]:
        if len(changes) >= MAX_FILES:
            more = True
            break
        changes.append(B._new_file_change(wt, rel))
    return {"changes": changes, "truncated_files": more, "base": base[:12]}


def _diff_head(old: str, new: str) -> str | None:
    """The ``diff --git`` line git writes for one entry under
    ``core.quotepath=off``; None when git would C-quote a name (a quote, a
    backslash, a control character) — that file is then read alone."""
    both = old + new
    if any(c in both for c in ('"', "\\")) or any(ord(c) < 32 or ord(c) == 127 for c in both):
        return None
    return f"diff --git a/{old} b/{new}"


def _patches(folder: str, base: str, entries: list[tuple[str, str, str]],
             target: str | None = None) -> list[str | None]:
    """Each entry's unified diff against ``base``, with ONE git child for all
    of them (v1.414.0 review: the first cut ran one per file — 54 children,
    ~3 s, for 50 files, on every 5-second poll while a reply ran). The
    combined output is cut at its ``diff --git`` lines; each piece must START
    with the line predicted for its entry, in order. Anything else (a name git
    quotes, a count that does not match, a pathspec too long for a command
    line) reads that file alone, exactly as the first cut did. None = that
    file's diff could not be read; a combined read that ran out of TIME is not
    retried file by file (50 more waits of up to 30 s each)."""
    if not entries:
        return []
    paths: list[str] = []
    for _code, old, new in entries:
        for p in ([old, new] if old != new else [new]):
            if p and p not in paths:
                paths.append(p)
    pieces: list[str] = []
    if sum(len(p) + 3 for p in paths) <= PATHSPEC_CHARS:
        got = _git(["diff", "--no-color", "--no-ext-diff", "-M", "--relative", base,
                    *([target] if target else []), "--", *paths], folder)
        if got.timed_out:
            return [None] * len(entries)
        if got.ok:
            cur: list[str] = []
            for line in got.out.splitlines(keepends=True):
                if line.startswith("diff --git ") and cur:
                    pieces.append("".join(cur))
                    cur = []
                cur.append(line)
            if cur:
                pieces.append("".join(cur))
    whole = len(pieces) == len(entries)
    out: list[str | None] = []
    for i, (_code, old, new) in enumerate(entries):
        head = _diff_head(old, new)
        piece = pieces[i] if whole else ""
        if head is not None and piece.split("\n", 1)[0].rstrip("\r") == head:
            out.append(piece)
            continue
        alone = [old, new] if old != new else [new]
        one = _git(["diff", "--no-color", "--no-ext-diff", "-M", "--relative", base,
                    *([target] if target else []), "--", *alone], folder)
        out.append(one.out if one.ok else None)
    return out


def _hunks_only(diff: str) -> str:
    """A diff's hunks without its ``---``/``+++`` header (so several edits of
    one file read as one diff: a header after a hunk would read as lines)."""
    lines = str(diff or "").splitlines()
    for i, line in enumerate(lines):
        if line.startswith("@@"):
            return "\n".join(lines[i:])
    return ""


def tool_changes(events: list[dict[str, Any]], folder: str) -> dict[str, Any]:
    """The tool's own record of its file edits (no repository)."""
    changes: list[dict[str, Any]] = []
    more = False
    for key, evs in tool_paths(events, folder).items():
        if len(changes) >= MAX_FILES:
            more = True
            break
        path = str(evs[-1].get("path") or "")
        if not os.path.isabs(path):
            path = os.path.join(folder, path)
        try:
            rel = os.path.relpath(path, folder).replace("\\", "/") if folder else path
        except ValueError:
            rel = path
        bodies = [_hunks_only(str(e.get("diff") or "")) for e in evs if e.get("diff")]
        bodies = [b for b in bodies if b]
        diff = ""
        if bodies:
            diff = f"--- a/{rel}\n+++ b/{rel}\n" + "\n".join(bodies)
        added = sum(1 for b in bodies for ln in b.splitlines() if ln.startswith("+"))
        removed = sum(1 for b in bodies for ln in b.splitlines() if ln.startswith("-"))
        created = all(e.get("kind") == "write" for e in evs) and "--- /dev/null" in str(evs[0].get("diff") or "")
        row: dict[str, Any] = {
            "path": path, "rel": rel, "name": rel.rsplit("/", 1)[-1],
            "status": "created" if created else "modified",
            "added": added if bodies else None, "removed": removed if bodies else None,
            "diff": diff, "by_tool": True,
        }
        if not bodies:
            row["note"] = "the tool sent no diff for this file"
        if len(evs) > 1:
            row["note"] = f"{len(evs)} edits, each as the tool reported it"
        if len(diff) > DIFF_CAP:
            row["diff"], row["truncated"] = diff[:DIFF_CAP], True
        changes.append(row)
    return {"changes": changes, "truncated_files": more}


# --------------------------------------------------------------------------- #
# v1.420.0 (C4.12): THIS MESSAGE'S changes, and Revert one file
# --------------------------------------------------------------------------- #
#: The per-session record of the folder as it was at turn boundaries, beside
#: the events log (``<home>/hosted/<sid>/turn_trees.json``). NOT the events
#: log and NOT meta.json: both are the hosted service's own files, written by
#: its one writer; this record has one writer too (this module, under
#: :data:`_TREES_LOCK`) and the service never reads it.
TREES_FILE = "turn_trees.json"
#: How many recorded trees one session keeps (newest kept).
MAX_TREES = 60
#: An idle read records the folder again only after this long (a pane open
#: on an idle session reads it again on every press of Refresh).
SEEN_EVERY_S = 30.0
_TREES_LOCK = threading.Lock()

SCOPE_SESSION = "session"
SCOPE_TURN = "turn"
TURN_WORDS_START = (
    "What changed in this folder while this message ran: compared with the folder as it "
    "was when the message started (new files included, files git ignores left out). It "
    "includes changes you or another program made meanwhile."
)
TURN_WORDS_SEEN = (
    "What changed in this folder since Iron Jarvis last read it before this message "
    "started ({at}): new files included, files git ignores left out. Changes made between "
    "that read and the message are counted here too."
)
TURN_WORDS_FIRST = (
    "This is the session's first message, so this is everything since the session "
    "started here (the same list as the whole session)."
)
TURN_UNKNOWN = (
    "This message's own changes cannot be told apart: Iron Jarvis did not read the folder "
    "just before it started. The whole session's changes are listed instead."
)
TURN_NOT_GIT = (
    "Changes per message need a git repository; this is the whole session's list."
)
#: The words under a git list (v1.420.0): "No Undo" still, but a file can be
#: put back.
REVERT_WORDS = (
    "No Undo here: {tool} made these changes itself. Revert puts ONE file back as it was "
    "{when}; the branch, its commits and every other file stay as they are. Use git or "
    "{tool} for anything more."
)
WHEN_SESSION = "when the session started here"
WHEN_TURN = "when this message started"
WHEN_SEEN = "when Iron Jarvis last read the folder before this message"
REVERT_NOT_REPO = (
    "This folder is not in a git repository, so a file cannot be put back from here: "
    "use {tool} to take a change back."
)
REVERT_RUNNING = "{tool} is still working on a reply. Revert a file once it has finished."
REVERT_CHANGED = (
    "{name} changed after this list was read, so it was left as it is. Read the changes "
    "again, then revert it if you still want to."
)
REVERT_BAD_PATH = "That file is not in this session's folder."
REVERT_ADDED = (
    "{name} was added to git's staging area, so it is left as it is: use git to take it back."
)
REVERT_NO_TURN = (
    "Iron Jarvis did not read the folder just before this message started, so it cannot "
    "tell what to put {name} back to. Revert it from the whole session's list instead."
)
REVERT_DONE = (
    "{name} is back as it was {when}. Only this file changed: the branch, its commits and "
    "every other file are as they were."
)
REVERT_MOVED = (
    "{name} did not exist {when}, so it was moved to Iron Jarvis's trash ({trash}). The "
    "branch, its commits and every other file are as they were."
)
REVERT_FAILED = "git could not put {name} back: {why}."


class RevertRefused(Exception):
    """A revert that did not happen, in one sentence (and an HTTP status)."""

    def __init__(self, sentence: str, status: int = 409) -> None:
        super().__init__(sentence)
        self.sentence = sentence
        self.status = status


def _trees_path(home: Any, sid: str) -> Path:
    from .events import session_dir

    return session_dir(home, sid) / TREES_FILE


def read_trees(home: Any, sid: str) -> list[dict[str, Any]]:
    """The recorded trees, oldest first ([] when none or unreadable)."""
    import json

    try:
        raw = json.loads(_trees_path(home, sid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [e for e in raw if isinstance(e, dict) and isinstance(e.get("tree"), str)] if isinstance(raw, list) else []


def _add_tree(home: Any, sid: str, entry: dict[str, Any]) -> None:
    import json

    with _TREES_LOCK:
        rows = read_trees(home, sid)[-(MAX_TREES - 1):] + [entry]
        path = _trees_path(home, sid)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows), encoding="utf-8", newline="\n")
        os.replace(tmp, path)


#: v1.425.0 (Wave 5, C3): where a session's KEPT index lives
#: (:func:`kept_index`) — under ``workspaces/``, which a backup leaves out (a
#: cache, rebuilt from the user's own index whenever it is missing).
KEPT_INDEX_DIR = ("workspaces", "code-index")
_KEPT_LOCKS: dict[str, threading.Lock] = {}
_KEPT_LOCKS_LOCK = threading.Lock()


def kept_index(home: Any, sid: str) -> Path | None:
    """v1.425.0 (Wave 5, C3): the private index file :func:`snapshot_tree`
    KEEPS for one session between its messages, or None without a home or
    a session id."""
    import re

    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(sid or ""))[:120].strip(".")
    if home is None or not safe:
        return None
    return Path(str(home)).joinpath(*KEPT_INDEX_DIR, f"{safe}.index")


def forget_kept_index(home: Any, sid: str) -> None:
    """A deleted session's kept index goes with it (a cache: never moved to
    the trash). Never raises."""
    path = kept_index(home, sid)
    if path is None:
        return
    try:
        with _kept_lock(path):
            path.unlink(missing_ok=True)
            _rules_file(path).unlink(missing_ok=True)
    except OSError:
        pass


def _rules_file(path: Path) -> Path:
    """v1.425.0 (review): beside a kept index, the fingerprint of the ignore
    rules it was last brought up to date under (:func:`_rules_print`)."""
    return path.with_name(path.name + ".rules")


def _rules_print(folder: str, exclude: str, top: str) -> str:
    """v1.425.0 (review): the ignore rules ``git add -A .`` reads that the add
    itself does not report — ``.git/info/exclude`` and every ``.gitignore``
    from the folder's parent up to the repository's top — as each file's
    size and time (BLOCKING; a few stats, never raises). The folder's OWN
    ``.gitignore`` files are reported by the add itself (``-v``)."""
    names = [exclude]
    try:
        cur = os.path.normcase(os.path.realpath(folder))
        stop = os.path.normcase(os.path.realpath(top))
        if cur != stop and cur.startswith(stop.rstrip("\\/") + os.sep):
            d = os.path.dirname(cur)
            for _ in range(64):
                names.append(os.path.join(d, ".gitignore"))
                if d == stop or os.path.dirname(d) == d:
                    break
                d = os.path.dirname(d)
    except (OSError, ValueError):
        pass
    parts = []
    for name in names:
        try:
            st = os.stat(name)
            parts.append(f"{os.path.normcase(name)}|{st.st_size}|{st.st_mtime_ns}")
        except OSError:
            parts.append(f"{os.path.normcase(name)}|-")
    return "\n".join(parts)


def _rules_touched(out: str) -> bool:
    """v1.425.0 (review): did ``git add -A -v`` add, change or remove a
    ``.gitignore`` (a line ``add '<path>'`` / ``remove '<path>'``)?"""
    for line in str(out or "").splitlines():
        if (line.startswith("add '") or line.startswith("remove '")) and line.endswith("'"):
            path = line[line.index("'") + 1:-1]
            if path.rsplit("/", 1)[-1] == ".gitignore":
                return True
    return False


def _kept_lock(path: Path) -> threading.Lock:
    key = os.path.normcase(str(path))
    with _KEPT_LOCKS_LOCK:
        lock = _KEPT_LOCKS.get(key)
        if lock is None:
            lock = _KEPT_LOCKS[key] = threading.Lock()
        return lock


def _write_tree(folder: str, idx: str, *, watch_rules: bool = False) -> str:
    """``git add -A .`` into the index file *idx*, then ``git write-tree``.
    Raises :class:`GitUnreadable`. BLOCKING. ``watch_rules`` (a KEPT index,
    v1.425.0 review): "" — and no tree written — when the add changed a
    ``.gitignore`` (the caller starts the kept index again)."""
    from ..git.integration import git_env, run_git
    from ..harness import branches as B

    env = git_env({"GIT_INDEX_FILE": idx, "GIT_OPTIONAL_LOCKS": "0"})

    def run(args: list[str]) -> Any:
        return run_git(["-c", "core.quotepath=off", *args], Path(folder), env=env,
                       identity=False, encoding="utf-8", timeout=B.QUICK_S)

    added = run(["add", "-A", *(["-v"] if watch_rules else []), "--", "."])
    if not added.ok:
        raise GitUnreadable(added)
    if watch_rules and _rules_touched(added.out):
        return ""
    tree = run(["write-tree"])
    if not tree.ok or not tree.out.strip():
        raise GitUnreadable(tree)
    return tree.out.strip()


def snapshot_tree(folder: str, *, keep: "Path | str | None" = None) -> str:
    """The folder as it is NOW, as a git tree: the user's index copied into a
    throwaway file, ``git add -A .`` into THAT copy (the session's folder
    only — new files included, ``.gitignore`` honoured), ``git write-tree``.
    The user's own index, branch and files are never touched (their blobs
    are written to the repository's object store, as ``git stash`` does).
    Raises :class:`GitUnreadable` when git did not answer (bounded by the
    30 s read limit). BLOCKING.

    v1.420.0 review: the copy KEEPS the index file's modified time
    (``copy2``) — git re-reads an entry whose time is not older than the
    index file ("racily clean"; Git for Windows compares whole seconds), and
    a fresh time made a same-size edit in that second read as unchanged, so
    the turn's list missed it and Revert put back the older content. And
    ``.``, not ``:/``: a session in a subfolder of a large repository must
    not hash every file outside it on each message.

    v1.425.0 (Wave 5, C3 — THE MESSAGE WAITS LESS): *keep* (a session's
    :func:`kept_index`) is a private index KEPT between the session's
    messages instead of a fresh copy each time — copied from the user's
    index once (``copy2``, the rule above), then git's own: the files it
    already read keep their recorded size and time, so ``git add -A`` reads
    only what changed (a fresh copy re-read every file the user had not
    staged and every untracked one, on every message). The tree is the same
    one (the folder's content); only outside the session's folder can the
    kept index differ from the user's current one, and every reader of these
    trees diffs inside the folder (``--relative``). Measured on this PC on a
    50,000-file repository (200 untracked files): 525-565 ms per message
    with a fresh copy, 306-416 ms with the kept one. A kept index git cannot
    use (another repository's after the folder moved, a damaged file) is
    copied again from the user's. One read at a time per kept index (a lock;
    git would refuse a second writer).

    v1.425.0 (review — THE KEPT INDEX FOLLOWS THE IGNORE RULES): ``git add
    -A`` never drops an entry an index already holds, so a file a snapshot
    once added as untracked (a ``node_modules`` installed before its
    ``.gitignore`` line) stayed in the kept index after a rule came to ignore
    it — the tree was no longer the fresh copy's, "This message" listed
    ignored files, and every later snapshot re-read them. Whenever a rule
    changed since the kept index was last brought up to date — a
    ``.gitignore`` in the folder (the add reports it, ``-v``), one above it up
    to the repository's top, or ``.git/info/exclude`` (a fingerprint kept
    beside the index, :func:`_rules_print`) — the kept index is copied again
    from the user's: the fresh copy's tree. Not followed (a residual
    difference, said here): the user's own global excludes file, and a change
    of the USER's index inside the folder (``git rm --cached`` of a file
    already ignored, or a file force-added with ``-f``) after the copy."""
    import shutil
    import tempfile

    if keep is not None:
        where = _git(["rev-parse", "--git-path", "index", "--git-path", "info/exclude",
                      "--show-toplevel"], folder)
        lines = [ln.strip() for ln in where.out.splitlines()] if where.ok else []
        if len(lines) < 3 or not lines[0] or not lines[2]:
            raise GitUnreadable(where)
        src = os.path.join(folder, lines[0])
        rules = _rules_print(folder, os.path.join(folder, lines[1]), lines[2])
        path = Path(str(keep))
        with _kept_lock(path):
            # Review (v1.425.0): a git this code ran and then KILLED at its
            # time limit leaves the kept index's lock file behind, and every
            # later ``git add`` refused ("index.lock: File exists") — the
            # session never had a message's tree again. The index is private
            # and this lock is held: no git of ours is using it now, so a lock
            # file here is a leftover.
            try:
                Path(str(path) + ".lock").unlink(missing_ok=True)
            except OSError:
                pass
            try:
                same_rules = _rules_file(path).read_text(encoding="utf-8") == rules
            except OSError:
                same_rules = False
            fresh = not path.is_file() or not same_rules
            if fresh:
                _copy_index(src, path)
            try:
                tree = _write_tree(folder, str(path), watch_rules=not fresh)
            except GitUnreadable:
                if fresh:
                    raise
                tree = ""  # a kept index git refused
            if not tree:
                # A rule changed since the last snapshot, or git refused the
                # kept index: start again from the user's.
                _copy_index(src, path)
                tree = _write_tree(folder, str(path))
            try:
                _rules_file(path).write_text(rules, encoding="utf-8")
            except OSError:
                pass  # the next snapshot copies again: slower, never wrong
            return tree
    where = _git(["rev-parse", "--git-path", "index"], folder)
    if not where.ok or not where.out.strip():
        raise GitUnreadable(where)
    src = os.path.join(folder, where.out.strip())
    tmp = tempfile.mkdtemp(prefix="ij-turn-tree-")
    try:
        idx = os.path.join(tmp, "index")
        if os.path.isfile(src):
            shutil.copy2(src, idx)
        return _write_tree(folder, idx)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _copy_index(src: str, dest: Path) -> None:
    """The user's index copied to *dest* (its time KEPT — ``copy2``, the
    v1.420.0 rule), or *dest* removed when the repository has none yet
    (git then starts the private index empty)."""
    import shutil

    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        dest.unlink(missing_ok=True)
    except OSError:
        pass
    if os.path.isfile(src):
        tmp = dest.with_name(dest.name + ".tmp")
        shutil.copy2(src, tmp)
        os.replace(tmp, dest)


def record_turn_start(home: Any, sid: str, folder: str, turn_id: str, *,
                      abandon: "threading.Event | None" = None) -> str | None:
    """The folder's tree at the START of a turn, recorded for this message's
    Changes scope (exact). The hosted service calls it (through
    :func:`record_turn_start_async`) right after it writes the turn's
    ``user`` event, before the tool is sent the message. Never raises; None
    when the folder is not a repository or git did not answer. *abandon*
    set (the service stopped waiting): the tree is NOT recorded — the tool
    may already be changing files, so a late tree is not the start."""
    try:
        if not folder or _network(folder) or not os.path.isdir(folder):
            return None
        tree = snapshot_tree(folder, keep=kept_index(home, sid))  # v1.425.0 (C3)
        if abandon is not None and abandon.is_set():
            return None
        _add_tree(home, sid, {"how": "start", "turn": str(turn_id), "tree": tree, "at": _iso(_now_utc())})
        return tree
    except Exception:  # noqa: BLE001 — a record never fails a turn
        return None


#: How long a message waits for its start to be recorded (v1.420.0 review):
#: past it the message goes on and that turn falls back to the idle read.
TURN_START_WAIT_S = 3.0


async def record_turn_start_async(home: Any, sid: str, folder: str, turn_id: str, *,
                                  bound: float = TURN_START_WAIT_S) -> str | None:
    """:func:`record_turn_start` off the event loop, waited for at most
    *bound* seconds (v1.420.0 review, the service's hook). A record that
    comes back later is dropped (*abandon*); a cancel (Stop) drops it too and
    goes on. Never raises anything but the cancel."""
    import asyncio

    if not folder:
        return None
    abandon = threading.Event()
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(record_turn_start, home, sid, folder, turn_id, abandon=abandon), bound)
    except asyncio.CancelledError:
        abandon.set()
        raise
    except Exception:  # noqa: BLE001 — a time limit or a fault never fails a turn
        abandon.set()
        return None


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _at(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        t = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo is not None else t.replace(tzinfo=timezone.utc)


def turns_of(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The session's turns, oldest first: ``{id, start, end}`` read off its
    ``user`` events and their ``done`` (datetimes; ``end`` None while one
    runs)."""
    out: list[dict[str, Any]] = []
    by: dict[str, dict[str, Any]] = {}
    for ev in events:
        tid = str(ev.get("turn") or "")
        if not tid:
            continue
        if ev.get("type") == "user" and tid not in by:
            row = {"id": tid, "start": _at(ev.get("at")), "end": None}
            by[tid] = row
            out.append(row)
        elif ev.get("type") == "done" and tid in by:
            by[tid]["end"] = _at(ev.get("at"))
    return out


def note_seen(home: Any, sid: str, folder: str, events: list[dict[str, Any]]) -> None:
    """An IDLE read records the folder as it is (v1.420.0): the base of the
    next message's own changes when the service did not record the turn's
    start (:func:`record_turn_start`). At most once every
    :data:`SEEN_EVERY_S`, and again at once after a reply ended. Never
    raises."""
    try:
        if not folder or _network(folder) or not os.path.isdir(folder):
            return
        turns = turns_of(events)
        last_end = turns[-1]["end"] if turns else None
        if turns and last_end is None:
            return  # a turn runs: this is not a "before"
        seen = [e for e in read_trees(home, sid) if e.get("how") == "seen"]
        if seen:
            when = _at(seen[-1].get("at"))
            if when is not None and (last_end is None or when >= last_end) and (
                (_now_utc() - when).total_seconds() < SEEN_EVERY_S
            ):
                return
        from ..harness import branches as B

        if B.git_path() is None:
            return
        inside = _git(["rev-parse", "--is-inside-work-tree"], folder)
        if not inside.ok or inside.out.strip() != "true":
            return
        tree = snapshot_tree(folder, keep=kept_index(home, sid))  # v1.425.0 (C3)
        _add_tree(home, sid, {"how": "seen", "tree": tree, "at": _iso(_now_utc())})
    except Exception:  # noqa: BLE001 — a record never fails a read
        return


def turn_base(home: Any, sid: str, events: list[dict[str, Any]], turn_id: str = "") -> tuple[str, str, str, str]:
    """``(turn id, how, tree, at)`` for one turn's own changes (the newest
    turn when *turn_id* is ""): ``start`` — the tree recorded when it
    started; ``seen`` — the newest idle read between the turn before it and
    its start; ``first`` — the session's first turn (the session's own base
    is its base); ``none`` — not known (tree "")."""
    turns = turns_of(events)
    if not turns:
        return "", "none", "", ""
    pick = next((t for t in turns if t["id"] == turn_id), None) if turn_id else turns[-1]
    if pick is None:
        return turn_id, "none", "", ""
    trees = read_trees(home, sid)
    for e in trees:
        if e.get("how") == "start" and e.get("turn") == pick["id"]:
            return pick["id"], "start", str(e["tree"]), str(e.get("at") or "")
    i = turns.index(pick)
    before = turns[i - 1]["end"] if i > 0 else None
    start = pick["start"]
    best: dict[str, Any] | None = None
    for e in trees:
        if e.get("how") != "seen":
            continue
        when = _at(e.get("at"))
        if when is None or start is None or when > start:
            continue
        if before is not None and when < before:
            continue
        if i > 0 and before is None:
            continue
        best = e
    if best is not None:
        return pick["id"], "seen", str(best["tree"]), str(best.get("at") or "")
    if i == 0:
        return pick["id"], "first", "", ""
    return pick["id"], "none", "", ""


def _turn_events(events: list[dict[str, Any]], turn_id: str) -> list[dict[str, Any]]:
    return [e for e in events if str(e.get("turn") or "") == turn_id] if turn_id else []


def _clock(at: str) -> str:
    """A recorded time as the PC's local hour and minute ("14:05")."""
    when = _at(at)
    return when.astimezone().strftime("%H:%M") if when is not None else "earlier"


def _blob(path: str) -> str:
    """git's blob id of the file's BYTES as they are now ("" when gone): what
    Revert compares with what the pane showed (a file changed since is never
    put back over).

    v1.420.0 review: a LINK (symlink, junction) is hashed by its target's
    NAME, as git stores a symlink — never opened: opening it follows it to
    wherever it points (a share: an SMB connection, the v1.379.0 lesson),
    and a model can make such a link in the folder."""
    import hashlib

    from ..harness.code_editor import _is_link

    try:
        st = os.lstat(path)
        if _is_link(path, st):
            data = os.readlink(path).encode("utf-8", "surrogateescape")
        else:
            with open(path, "rb") as f:
                data = f.read()
    except (OSError, ValueError):
        return ""
    h = hashlib.sha1(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


def changes_view(row: Any, events: list[dict[str, Any]], tool: str, *, scope: str = SCOPE_SESSION,
                 turn: str = "", home: Any = None, sid: str = "", running: bool = False) -> dict[str, Any]:
    """``GET /build/app/sessions/{id}/changes`` — what this session changed,
    with the source named. BLOCKING.

    v1.420.0 (C4.12): ``scope="turn"`` lists one MESSAGE's changes (the
    newest when *turn* is "") against the folder's tree at its start
    (:func:`turn_base`); with *home* and *sid* an idle read records the
    folder (:func:`note_seen`). Every git row carries ``blob`` (Revert's
    "changed since you read it" check)."""
    folder = str(getattr(row, "folder", "") or "")
    since = _created(row)
    out: dict[str, Any] = {
        "source": "none", "words": "", "since": _iso(since), "changes": [],
        "files": 0, "added": 0, "removed": 0, "truncated_files": False, "by_tool": 0,
        "no_undo": NO_UNDO.format(tool=tool), "scope": SCOPE_SESSION, "can_revert": False,
    }
    if not folder:
        out["words"] = NO_FOLDER
        return out
    # A network spelling is refused from its TEXT, before any stat or git call
    # (a stat of \\host\share is an SMB connection — the v1.379.0 lesson).
    if _network(folder):
        out["words"] = ON_SHARE
        return out
    if not os.path.isdir(folder):
        out["words"] = FOLDER_GONE
        return out
    if home is not None and sid and not running:
        note_seen(home, sid, folder, events)
    scope_note = ""
    turn_id = ""
    try:
        got = None
        if scope == SCOPE_TURN and home is not None and sid:
            turn_id, how, tree, at = turn_base(home, sid, events, turn)
            if how in ("start", "seen"):
                got = git_changes(folder, since, base=tree, target=snapshot_tree(folder, keep=kept_index(home, sid)))
                if got is not None:
                    out.update(scope=SCOPE_TURN, turn=turn_id, turn_how=how)
                    scope_note = TURN_WORDS_START if how == "start" else TURN_WORDS_SEEN.format(
                        at=_clock(at))
            elif how == "first":
                out.update(scope=SCOPE_TURN, turn=turn_id, turn_how="first")
                scope_note = TURN_WORDS_FIRST
            else:
                scope_note = TURN_UNKNOWN if turn_id else ""
        if got is None:
            got = git_changes(folder, since)
            if scope == SCOPE_TURN and got is None:
                scope_note = TURN_NOT_GIT
    except GitUnreadable as gone:
        out["words"] = gone.words
        return out
    if got is not None:
        mine = tool_paths(_turn_events(events, turn_id) if out["scope"] == SCOPE_TURN else events, folder)
        for c in got["changes"]:
            c["by_tool"] = _key(c["path"]) in mine
            c["blob"] = _blob(c["path"]) if c.get("status") != "deleted" else ""
        when = WHEN_SESSION
        if out["scope"] == SCOPE_TURN and out.get("turn_how") in ("start", "seen"):
            when = WHEN_TURN if out["turn_how"] == "start" else WHEN_SEEN
        # A turn's own words, or (the whole session's list in a turn's place)
        # why, before the session's words.
        words = scope_note if out["scope"] == SCOPE_TURN else (
            f"{scope_note} {GIT_WORDS}" if scope_note else GIT_WORDS)
        out.update(source="git", words=words, base=got["base"], can_revert=True,
                   no_undo=REVERT_WORDS.format(tool=tool, when=when))
    else:
        from ..harness import branches as B

        got = tool_changes(events, folder)
        words = NO_GIT_WORDS if B.git_path() is None else TOOL_WORDS
        out.update(source="tool", words=words.format(tool=tool))
    changes = got["changes"]
    out.update(
        changes=changes,
        files=len(changes),
        added=sum(int(c.get("added") or 0) for c in changes),
        removed=sum(int(c.get("removed") or 0) for c in changes),
        truncated_files=bool(got.get("truncated_files")),
        by_tool=sum(1 for c in changes if c.get("by_tool")),
    )
    return out


def _literal(rel: str) -> str:
    """A pathspec that names exactly this file (``*``, ``?``, ``[`` are
    letters here, never a glob)."""
    return f":(literal){rel}"


def _clean_rel(folder: str, rel: Any) -> str:
    """*rel* as a path INSIDE *folder* (forward slashes), read from the TEXT
    — never absolute, never a network or device spelling, never ``..``, no
    ``:`` (a drive or an NTFS stream) — then walked with ``lstat`` one part
    at a time: a link (symlink, junction) BEFORE the last part is refused
    and never followed (v1.420.0 review: ``Path.resolve`` opened the link's
    target — a share would be an SMB connection, the v1.379.0 lesson); the
    last part may be a link, which git and the trash move as the link
    itself. The text is used as sent (a stripped name could be another
    file). Raises :class:`RevertRefused`."""
    from ..harness.code_editor import _is_link

    text = str(rel or "").replace("\\", "/")
    if (
        not text.strip() or len(text) > 1_000 or "\0" in text or "\n" in text or "\r" in text
        or ":" in text or text.startswith("/") or _network(text)
        or any(part in ("..", "") for part in text.split("/"))
    ):
        raise RevertRefused(REVERT_BAD_PATH, 400)
    parts = text.split("/")
    at = folder
    for part in parts[:-1]:
        if part == ".":
            continue
        at = os.path.join(at, part)
        try:
            st = os.lstat(at)
        except OSError:
            break  # not there now: nothing below it can lead anywhere
        if _is_link(at, st):
            raise RevertRefused(REVERT_BAD_PATH, 400)
    return text


def _in_tree(folder: str, base: str, rel: str) -> bool:
    got = _git(["cat-file", "-e", f"{base}:./{rel}"], folder)
    return bool(got.ok)


def revert_file(home: Any, sid: str, row: Any, events: list[dict[str, Any]], tool: str, *,
                rel: Any, blob: Any, scope: str = SCOPE_SESSION, turn: str = "",
                running: bool = False, old_rel: Any = "") -> dict[str, Any]:
    """Revert ONE file of the Changes pane (v1.420.0, C4.12): put it back as
    it was at the scope's start — the session's base commit, or the tree
    recorded at this message's start (:func:`turn_base`). A file that did not
    exist then is MOVED to ``<home>/trash/<stamp>/code-revert/`` (never
    deleted); a renamed one also gets its old name back. Nothing else moves:
    no branch, no commit, no other file, and git's staging area is left as it
    is (``git restore --worktree``). A renamed row's *old_rel* (its row's
    ``old_rel``) is put back too when it is gone now and was there then.

    Refused, in words, before anything is touched: no folder, a network
    folder (by its TEXT), not a repository, a reply running, a path outside
    the folder, a file that changed since the pane read it (*blob* is the
    row's ``blob``), a new file git has staged, or a turn whose start was not
    recorded. BLOCKING; the route runs it under the session's git lock."""
    folder = str(getattr(row, "folder", "") or "")
    if not folder:
        raise RevertRefused(NO_FOLDER)
    if _network(folder):
        raise RevertRefused(ON_SHARE)
    if not os.path.isdir(folder):
        raise RevertRefused(FOLDER_GONE)
    if running:
        raise RevertRefused(REVERT_RUNNING.format(tool=tool))
    clean = _clean_rel(folder, rel)
    name = clean.rsplit("/", 1)[-1]
    from ..harness import branches as B

    if B.git_path() is None:
        raise RevertRefused(REVERT_NOT_REPO.format(tool=tool))
    try:
        if scope == SCOPE_TURN:
            _tid, how, base, _at_ = turn_base(home, sid, events, turn)
            if how == "first":
                base = _base_commit(folder, _created(row)) or ""
                when = WHEN_SESSION
            elif how in ("start", "seen"):
                when = WHEN_TURN if how == "start" else WHEN_SEEN
            else:
                raise RevertRefused(REVERT_NO_TURN.format(name=name))
        else:
            base = _base_commit(folder, _created(row)) or ""
            when = WHEN_SESSION
    except GitUnreadable as gone:
        raise RevertRefused(gone.words) from None
    if not base:
        raise RevertRefused(REVERT_NOT_REPO.format(tool=tool))
    path = os.path.join(folder, *clean.split("/"))
    if _blob(path) != str(blob or ""):
        raise RevertRefused(REVERT_CHANGED.format(name=name))
    old = _clean_rel(folder, old_rel) if old_rel else ""
    moved = ""
    if _in_tree(folder, base, clean):
        got = _g_write(["restore", f"--source={base}", "--worktree", "--", _literal(clean)], folder)
        if not got.ok:
            raise RevertRefused(REVERT_FAILED.format(name=name, why=_git_reason(got)))
    else:
        staged = _git(["ls-files", "--error-unmatch", "--", _literal(clean)], folder)
        if staged.ok:
            raise RevertRefused(REVERT_ADDED.format(name=name))
        if os.path.lexists(path):
            moved = _to_trash(home, sid, folder, clean)
    if old and old != clean and not os.path.lexists(os.path.join(folder, *old.split("/"))) and _in_tree(folder, base, old):
        _g_write(["restore", f"--source={base}", "--worktree", "--", _literal(old)], folder)
    words = REVERT_MOVED.format(name=name, when=when, trash=moved) if moved else REVERT_DONE.format(name=name, when=when)
    return {"ok": True, "rel": clean, "words": words, "moved_to": moved}


def _g_write(args: list[str], cwd: str) -> Any:
    """One git call that WRITES the working folder (never the index)."""
    from ..harness.branches import _g

    return _g(args, cwd, reads=False)


def _to_trash(home: Any, sid: str, folder: str, rel: str) -> str:
    """MOVE a file a scope made into ``<home>/trash/<stamp>/code-revert/<sid>/``
    (keeping its path), never delete it (the v1.256.0 rule). Raises
    :class:`RevertRefused` when it cannot be moved."""
    import shutil

    stamp = _now_utc().strftime("%Y%m%d-%H%M%S")
    dest = Path(home) / "trash" / stamp / "code-revert" / str(sid) / Path(*rel.split("/"))
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest = dest.with_name(f"{dest.name}.{os.getpid()}-{int(_now_utc().timestamp())}")
        shutil.move(os.path.join(folder, *rel.split("/")), str(dest))
    except OSError as exc:
        raise RevertRefused(REVERT_FAILED.format(name=rel.rsplit("/", 1)[-1], why=one_line(str(exc), 160))) from None
    return str(dest)


# --------------------------------------------------------------------------- #
# USAGE
# --------------------------------------------------------------------------- #


def _usage_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e.get("type") == "usage"]


def totals(events: list[dict[str, Any]]) -> dict[str, Any]:
    """The session's totals over its ``usage`` events (each one reply)."""
    rows = _usage_events(events)

    def n(e: dict[str, Any], k: str) -> int:
        v = e.get(k)
        return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0

    costs = [float(e["cost_usd"]) for e in rows
             if isinstance(e.get("cost_usd"), (int, float)) and not isinstance(e.get("cost_usd"), bool)]
    by = sorted({str(e.get("cost_reported_by") or "") for e in rows if e.get("cost_reported_by")})
    return {
        "replies": len(rows),
        "input": sum(n(e, "input") for e in rows),
        "output": sum(n(e, "output") for e in rows),
        "cache_read": sum(n(e, "cache_read") for e in rows),
        "cost_usd": round(sum(costs), 6) if costs else None,
        "list_price": any(e.get("list_price") is True for e in rows),
        "cost_reported_by": by[0] if len(by) == 1 else "",
    }


def seen_windows(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The windows the TOOL last reported (newest ``usage`` event that had
    them), with when."""
    for e in reversed(_usage_events(events)):
        w = e.get("windows")
        if isinstance(w, list) and w:
            clean = []
            now = _now()
            for x in w:
                if not isinstance(x, dict) or not isinstance(x.get("percent"), (int, float)):
                    continue
                # v1.414.0 review: a window whose reset has passed is over —
                # its old percent is not what is left (Iron-Proxy's windows
                # are dropped the same way in account_usage).
                ends = _when(x.get("resets_at"))
                if ends is not None and ends <= now:
                    continue
                clean.append({
                    "key": str(x.get("key") or "")[:40],
                    "label": str(x.get("label") or "")[:60],
                    "percent": int(x["percent"]),
                    "resets_at": x.get("resets_at") if isinstance(x.get("resets_at"), str) else None,
                })
            if clean:
                return {"windows": clean, "at": e.get("at"), "plan": str(e.get("plan") or "")[:40]}
    return None


def _now() -> datetime:
    """Now, UTC (a seam: a recorded frame's windows are judged against the
    time they were recorded in a test)."""
    return datetime.now(timezone.utc)


def _when(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        t = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo is not None else t.replace(tzinfo=timezone.utc)


def seen_context(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for e in reversed(_usage_events(events)):
        c = e.get("context")
        if isinstance(c, dict):
            used, window = c.get("used"), c.get("window")
            if isinstance(used, int) and isinstance(window, int) and window > 0 and used >= 0:
                return {
                    "used": used, "window": window,
                    "percent": max(0, min(100, int(round(100 * used / window)))),
                    "at": e.get("at"),
                }
    return None


def account_usage(platform: Any, row: Any) -> dict[str, Any] | None:
    """The session's Iron-Proxy account from the CACHED snapshot: name, plan,
    status and EVERY window Iron-Proxy holds. None = this PC's own login."""
    from ..harness import build_accounts as BA
    from . import store

    account = str(getattr(row, "account", "") or "")
    if not account:
        return None
    svc = getattr(platform, "iron_proxy", None)
    rec = store.account_record(row)
    acc = BA.session_account(svc, account, str(rec.get("title") or "")) or {"id": account}
    out: dict[str, Any] = {
        "id": account,
        "name": str(acc.get("name") or rec.get("title") or account),
        "plan_label": acc.get("plan_label"),
        "status": acc.get("status"),
        "resets_at": acc.get("resets_at"),
        "windows": [],
    }
    now = _now()
    for w in BA.state_of(svc, account).get("windows") or []:
        if not isinstance(w, dict):
            continue
        used = w.get("usedPercent")
        if isinstance(used, bool) or not isinstance(used, (int, float)):
            continue
        resets = BA._parse(w.get("resetsAt"))
        if resets is not None and resets <= now:
            continue
        out["windows"].append({
            "label": SEEN.window_label(w.get("windowMins"), w.get("kind")),
            "percent": int(round(float(used))),
            "resets_at": BA._iso(resets),
            # v1.414.0 review: when Iron-Proxy SAW this number (its own
            # ``at``) — a number from a call hours ago is shown with its age.
            "at": BA._iso(BA._parse(w.get("at"))),
        })
    seen = [w["at"] for w in out["windows"] if w["at"]]
    # The account's age is its OLDEST number's: never younger than any shown.
    out["at"] = min(seen, key=lambda a: BA._parse(a)) if seen else None
    # v1.439.0: "Check usage now" on the Usage tab — the last read of this
    # account from its own CLI, and whether one can be made (in memory).
    from ..iron_proxy import usage_read as U

    out["usage_read_at"] = U.last_ok_at(account)
    out["can_refresh"] = U.can_refresh(acc, svc)
    return out


def usage_view(platform: Any, row: Any, events: list[dict[str, Any]]) -> dict[str, Any]:
    """``GET /build/app/sessions/{id}/usage``. Cached reads only. BLOCKING."""
    return {
        "harness": str(getattr(row, "harness", "") or ""),
        "account": account_usage(platform, row),
        "seen": seen_windows(events),
        "totals": totals(events),
        "context": seen_context(events),
    }


# --------------------------------------------------------------------------- #
# SESSION
# --------------------------------------------------------------------------- #


def branch_of(folder: str) -> dict[str, Any]:
    """``{repo: False}`` | ``{repo: True, branch, detached}``."""
    from ..harness import branches as B

    if not folder or _network(folder) or not os.path.isdir(folder) or B.git_path() is None:
        return {"repo": False}
    got = _git(["rev-parse", "--abbrev-ref", "HEAD"], folder)
    if not got.ok:
        inside = _git(["rev-parse", "--is-inside-work-tree"], folder)
        if inside.ok and inside.out.strip() == "true":
            return {"repo": True, "branch": "", "detached": False, "no_commits": True}
        return {"repo": False}
    name = got.out.strip()
    return {"repo": True, "branch": "" if name == "HEAD" else name, "detached": name == "HEAD"}


def rule_files(folder: str) -> list[str]:
    if not folder or _network(folder):
        return []
    out = []
    for name in RULE_FILES:
        try:
            if os.path.isfile(os.path.join(folder, name)):
                out.append(name)
        except OSError:
            continue
    return out


def program_listing(pid: int | None) -> dict[str, Any]:
    """The programs running under the tool's own process right now (its
    helpers included), names and a masked command line, as ``{programs,
    known, more}``. v1.414.0 review: ``known`` False = the listing could not
    be read (never "nothing is running"); ``more`` = cut at
    :data:`MAX_PROGRAMS`."""
    if not pid:
        return {"programs": [], "known": True, "more": False}
    try:
        import psutil
    except ImportError:  # pragma: no cover - a dependency
        return {"programs": [], "known": False, "more": False}
    try:
        kids = psutil.Process(int(pid)).children(recursive=True)
    except (psutil.Error, OSError, ValueError):
        return {"programs": [], "known": False, "more": False}
    from ..harness.process import _shown_command

    out: list[dict[str, Any]] = []
    more = False
    for p in kids:
        try:
            name = p.name()
            if name.lower() in _PLUMBING:
                continue
            if len(out) >= MAX_PROGRAMS:
                more = True
                break
            try:
                cmd = subprocess.list2cmdline(p.cmdline())
            except (psutil.Error, OSError):
                cmd = ""
            # v1.414.0 review: the Session tab's v1.404.0 rule — the word after
            # a secret-named flag (--token VALUE, --api-key VALUE) hidden, the
            # known shapes masked, THEN the cut (a key never shows).
            out.append({"pid": p.pid, "name": name[:80], "command": _shown_command(cmd)})
        except (psutil.Error, OSError):
            continue
    return {"programs": out, "known": True, "more": more}


def programs_under(pid: int | None) -> list[dict[str, Any]]:
    """:func:`program_listing`'s programs alone."""
    return program_listing(pid)["programs"]


def session_view(
    row: Any, events: list[dict[str, Any]], pid: int | None, running: bool, platform: Any = None
) -> dict[str, Any]:
    """``GET /build/app/sessions/{id}/overview``. BLOCKING."""
    folder = str(getattr(row, "folder", "") or "")
    present = bool(folder) and not _network(folder) and os.path.isdir(folder)
    listing = program_listing(pid) if running else {"programs": [], "known": True, "more": False}
    return {
        "folder": folder,
        "folder_exists": present,
        "on_share": bool(folder) and _network(folder),
        "git": branch_of(folder) if present else {"repo": False},
        "rules": rule_files(folder) if present else [],
        "context": seen_context(events),
        "tool_running": bool(running),
        "programs": listing["programs"],
        "programs_known": listing["known"],
        "programs_more": listing["more"],
        # v1.426.0 (E4): the tool's OWN MCP servers, as it reported them.
        "mcp": _tool_setup_view(row, events, running),
        # v1.448.0 (Wave A, A3): where the tool's model answers — the cloud,
        # or this network — so the Session tab says it like a chat receipt.
        "where": where_view(row, platform),
    }


#: v1.448.0: the tools whose model is ALWAYS someone else's service — their
#: inference runs on the vendor's cloud whatever model is picked.
CLOUD_TOOLS: frozenset[str] = frozenset({"claude-code", "codex", "grok", "antigravity"})

#: The tools that take a ``provider/model`` pick of their own (Pi, OpenCode):
#: whether the answer leaves this network depends on that provider.
PICKS_PROVIDER_TOOLS: frozenset[str] = frozenset({"pi", "opencode"})

#: A Pi / OpenCode provider prefix that names a cloud service (the tool's own
#: spelling). Anything not listed here or in :data:`_LOCAL_PREFIXES` is "not
#: known" — never a guess.
_CLOUD_PREFIXES: frozenset[str] = frozenset(
    {
        "anthropic", "openai", "openai-codex", "google", "google-vertex", "gemini", "xai", "grok",
        "openrouter", "groq", "mistral", "deepseek", "together", "togetherai", "fireworks",
        "fireworks-ai", "cerebras", "cohere", "perplexity", "amazon-bedrock", "bedrock", "azure",
        "vertex", "github-copilot", "opencode", "huggingface", "moonshotai", "zai", "zhipuai",
        "minimax", "alibaba", "baseten", "deepinfra", "sambanova", "vercel", "v0", "morph",
    }
)

#: A Pi / OpenCode provider prefix that names a model server of the user's
#: own, mapped onto Iron Jarvis's provider id of the same server — read
#: through ``egress.stays_on_this_network`` (the chat receipt's own rule), so
#: the answer is the address Iron Jarvis knows for it, never a guess.
_LOCAL_PREFIXES: dict[str, str] = {
    "ollama": "ollama",
    "lmstudio": "lmstudio",
    "lm-studio": "lmstudio",
    "llamacpp": "llamacpp",
    "llama.cpp": "llamacpp",
    "llama-cpp": "llamacpp",
}

WHERE_CLOUD = "cloud"
WHERE_NETWORK = "on this network"


def where_view(row: Any, platform: Any = None) -> dict[str, Any]:
    """v1.448.0 (Wave A, A3): ``{on_this_network: bool | None, words}`` for a
    session's tool and model. Claude Code, Codex, Grok and Antigravity run
    their models on the vendor's service: ``cloud`` whatever the pick. Pi and
    OpenCode name a ``provider/model`` of their own: a cloud provider's prefix
    is ``cloud``; a model server of the user's (ollama, LM Studio, llama.cpp)
    is judged by Iron Jarvis's own address for that server through
    ``providers.egress.stays_on_this_network``; anything else — a prefix not
    known, no model, no platform to ask — is ``None`` ("not known") and the
    tab draws nothing. Never raises."""
    try:
        harness = str(getattr(row, "harness", "") or "").strip().lower()
        if harness in CLOUD_TOOLS:
            return {"on_this_network": False, "words": WHERE_CLOUD}
        if harness not in PICKS_PROVIDER_TOOLS:
            return {"on_this_network": None, "words": ""}
        model = str(getattr(row, "model", "") or "").strip()
        if "/" not in model:
            return {"on_this_network": None, "words": ""}
        prefix = model.split("/", 1)[0].strip().lower()
        if prefix in _CLOUD_PREFIXES:
            return {"on_this_network": False, "words": WHERE_CLOUD}
        ours = _LOCAL_PREFIXES.get(prefix)
        if ours is None or platform is None:
            return {"on_this_network": None, "words": ""}
        from ..providers import egress

        local = bool(
            egress.stays_on_this_network(
                ours, getattr(platform, "providers", None), getattr(platform, "config", None)
            )
        )
        return {"on_this_network": local, "words": WHERE_NETWORK if local else WHERE_CLOUD}
    except Exception:  # noqa: BLE001 — a reading never breaks the tab
        return {"on_this_network": None, "words": ""}


def _tool_setup_view(row: Any, events: list[dict[str, Any]], running: bool) -> dict[str, Any]:
    """v1.426.0 (E4): ``hosted/tool_setup.view`` (never raises)."""
    from . import tool_setup

    try:
        return tool_setup.view(str(getattr(row, "harness", "") or ""), events, running)
    except Exception:  # noqa: BLE001 — a reading never breaks the tab
        return {"known": False, "servers": [], "note": ""}
