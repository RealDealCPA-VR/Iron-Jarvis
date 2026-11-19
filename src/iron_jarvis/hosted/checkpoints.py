"""Build › Code: checkpoints that LAST, and the whole folder put back.

Every message of a Code-view session already records the folder as a git tree
(:func:`panes.record_turn_start`). Two things were missing:

* **Nothing kept that tree.** No ref pointed at it, so git's own clean-up
  (``git gc``, by default after about two weeks) could delete it. Now every
  recorded tree gets a hidden ref that points STRAIGHT AT THE TREE:
  ``refs/ironjarvis/checkpoints/<session>/<name>``. No commit is made, so the
  ref is not in ``git log --all``, ``git branch -a`` or ``git tag`` (measured:
  a tree ref survives ``git gc --prune=now``; the same tree with no ref is
  deleted). The refs go with the session (:func:`forget_session`). A plain
  push, fetch or clone does not carry them; ``push --mirror``, ``clone
  --mirror`` and ``bundle --all`` DO (measured), and the Handbook says so.
* **A rewind never touched files.** :func:`plan` reads what putting the folder
  back to a message's checkpoint would do, and :func:`restore` does it.

THE RULE OF THE RESTORE: nothing is removed or written over without a copy.
git itself does not keep that rule — ``git restore`` DELETES a whole folder
that stands where a file must go (with the ignored files, the nested
repository or the junction inside it), deletes a file that stands where a
folder must go, and writes over a file it ignores (review of the first
version: each measured). So, in this order (:func:`_apply`):

1. the plan refuses, before anything changes, a path where something that is
   in no checkpoint stands in the way (:func:`_plan`: ``problems``), and
   LEAVES a file git ignores now (``left``);
2. the files made since are MOVED to ``<trash>/made-since/``;
3. the same "is anything in the way" check runs again on the folder as it now
   is (:func:`_still_clear`);
4. every file git is about to write over is COPIED to ``<trash>/replaced/``
   (no copy, no restore);
5. only then ``git restore --source=<tree> --worktree`` writes, by literal
   paths, through a PRIVATE index (``GIT_INDEX_FILE``): the user's index is
   neither changed nor locked, so a git that is stopped half way leaves no
   ``index.lock`` in their repository.

When the folder's ``.gitignore`` files are among what changes, they go back
FIRST and the files "made since" are then asked of git again
(:func:`_ignored_now`): one that the checkpoint's own rules have git ignore
STAYS where it is — it is in no checkpoint, so nothing can say it was not
there (a ``.env`` the user filled in after the tool un-ignored it).

A failure returns the folder from those copies (exact bytes) and is then
CHECKED against the folder's tree before it is called whole. The folder as it
was just before is also kept as a ``before_rewind`` checkpoint, which is what
:func:`undo` puts back; an undo goes through the same five steps, so what IT
replaces is kept too.

Everything here is BLOCKING (a caller on the event loop hops it off) and the
caller holds the session's and the folder's git locks (the route passes them
in). A refusal is one plain sentence (:class:`RestoreRefused`).

Not restored, and said in the plan: a nested repository (submodule), a link,
a file git ignores (in no checkpoint), a name that changed only in upper or
lower case.

LINE ENDINGS. A checkpoint is the tree git makes with the folder's own
settings, so under ``core.autocrlf`` or a ``.gitattributes`` rule it holds a
file's NORMALISED text, not which line endings the file had; git then writes
the endings its settings give (measured on the owner's PC,
``core.autocrlf=input``: a CRLF ``run.bat`` came back LF). After git writes,
:func:`_keep_endings` therefore gives a restored file the line-ending style of
the version it replaced — the file as it was a moment ago, which is the style
it had at the checkpoint unless the endings themselves were changed since. An
undo goes one better: the rewind's own copy is the exact file
(:func:`_exact_endings`). A file that was deleted since has no version to
read the style from and keeps git's; the plan says all of this (``endings``).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import stat
import uuid
from pathlib import Path
from typing import Any

from . import panes as PN

log = logging.getLogger(__name__)

#: Where the hidden refs live in the user's repository.
REF_ROOT = "refs/ironjarvis/checkpoints"
#: How many checkpoints of each kind one session keeps (newest kept).
MAX_STARTS = 300
MAX_SEEN = 30
MAX_BEFORE = 10
#: A restore of more files than this is refused (use git for it).
RESTORE_MAX_FILES = 5_000
#: How many names of each list a plan shows (the counts are exact).
LIST_SHOWN = 50
#: How long git may take to write the files (a read's 30 s is too short for
#: thousands of files; a git stopped half way is the worst outcome here).
RESTORE_S = 300.0
#: A folder that stands where a file must go is walked at most this far.
WALK_MAX = 20_000
#: An undo makes line endings exact from the rewind's own copies, for files
#: up to this size.
EXACT_MAX_BYTES = 50 * 1024 * 1024

HOW_START = "start"
HOW_SEEN = "seen"
HOW_BEFORE = "before_rewind"

#: The folders inside one rewind's trash folder.
MADE = "made-since"
REPLACED = "replaced"
ROLLED = "rolled-back"

NO_CHECKPOINT = (
    "Iron Jarvis has no checkpoint of the folder for this message, so its files cannot be "
    "put back from here."
)
CHECKPOINT_GONE = (
    "The checkpoint for this message is no longer kept, so nothing was changed."
)
NOT_REPO = "This folder is not in a git repository, so its files cannot be put back from here."
TOO_MANY = (
    "{n} files changed since this message, which is more than Iron Jarvis puts back in one "
    "go. Use git for this one."
)
FOLDER_CHANGED = (
    "The folder changed after this list was read, so nothing was changed. Read it again."
)
NOT_KEPT = (
    "Iron Jarvis could not keep a checkpoint of the folder as it is now, so nothing was changed."
)
PROBLEMS = "Some files cannot be put back right now, so nothing was changed."
FAILED_ROLLED_BACK = "The files could not be put back ({why}), so the folder was left as it was."
FAILED_PARTIAL = (
    "The files could not all be put back ({why}), and the folder could not be returned to how "
    "it was either. What was replaced or moved is in Iron Jarvis's trash ({trash})."
)
NOTHING_TO_UNDO = "There is no rewind to undo."
UNDO_TOO_LATE = "A message was sent after that rewind, so it can no longer be undone from here."
WHY_LOCKED = "it cannot be written right now (another program may have it open)"
WHY_SUBMODULE = "it is a repository of its own"
WHY_LINK = "it is a link"
WHY_PATH = "its path cannot be used here"
WHY_IGNORED = "git ignores it now, so it is in no checkpoint"
WHY_CASE = "only the upper or lower case of its name changed"
WHY_FOLDER = "a folder stands in its place and holds files that are in no checkpoint"
WHY_PARENT = "a file that is in no checkpoint stands where its folder must go"
WHY_NO_COPY = "a copy of a file could not be kept first"
WHY_IN_THE_WAY = "something that is in no checkpoint stands where a file must go"
WHY_GIT_SLOW = "git did not finish in time"
WHY_MOVE = "a file could not be moved"
ENDINGS = (
    "git changes line endings in this folder ({why}). A file that goes back keeps the kind of "
    "line endings it has now, and a file that was deleted comes back with the ones git writes, "
    "so line endings can differ from what they were."
)
DONE = (
    "The folder's files are back as they were when that message started: {back} put back, "
    "{moved} made since moved to Iron Jarvis's trash."
)
DONE_KEPT = " The versions that were replaced are kept there too."
DONE_NOTHING = "The folder's files were already as they were when that message started."
DONE_COMMITS = " {n} commit{s} made since then {are} still in git, so git shows the difference as changes."
DONE_LEFT = " {n} left as {they} ({why})."
DONE_STAYED = " {n} that git ignores again stayed where {they}."
UNDONE = "The rewind's file changes were undone: the folder is as it was just before it."
UNDONE_KEPT = " What the undo replaced is kept in Iron Jarvis's trash."


class RestoreRefused(Exception):
    """A restore that did not happen, in one sentence (and an HTTP status)."""

    def __init__(self, sentence: str, status: int = 409, *, problems: list[dict[str, str]] | None = None) -> None:
        super().__init__(sentence)
        self.sentence = sentence
        self.status = status
        self.problems = problems or []


class _Stopped(Exception):
    """A restore step that did not finish, with the plain reason."""


# --------------------------------------------------------------------------- #
# the refs
# --------------------------------------------------------------------------- #

_SAFE = re.compile(r"[^A-Za-z0-9_-]")
_REPOS: dict[str, str] = {}


def _part(text: Any) -> str:
    """One path part of a ref name: letters, digits, ``_`` and ``-`` only
    (git's rules refuse much more than this ever holds)."""
    return _SAFE.sub("_", str(text or ""))[:120].strip("_-")


def ref_for(sid: Any, name: Any) -> str:
    """The hidden ref for one checkpoint, or "" when either part is empty."""
    a, b = _part(sid), _part(name)
    return f"{REF_ROOT}/{a}/{b}" if a and b else ""


def _usable(path: str) -> bool:
    return bool(path) and not PN._network(path) and os.path.isdir(path)


def repo_of(folder: str) -> str:
    """The repository's own (common) git directory, absolute: where a ref
    lives even after *folder* itself is gone (a removed worktree, a deleted
    subfolder). "" when git did not say. Never raises."""
    key = os.path.normcase(os.path.abspath(folder)) if folder else ""
    if not key:
        return ""
    if key in _REPOS:
        return _REPOS[key]
    out = ""
    try:
        got = PN._git(["rev-parse", "--path-format=absolute", "--git-common-dir"], folder)
        text = got.out.strip() if got.ok else ""
        if not text or text.startswith("--"):  # a git older than 2.31
            got = PN._git(["rev-parse", "--git-common-dir"], folder)
            text = got.out.strip() if got.ok else ""
            if text and not os.path.isabs(text):
                text = os.path.join(folder, text)
        if text and os.path.isdir(text):
            out = os.path.abspath(text)
    except Exception:  # noqa: BLE001
        out = ""
    if out:
        if len(_REPOS) > 500:
            _REPOS.clear()
        _REPOS[key] = out
    return out


def keep(folder: str, ref: str, tree: str) -> bool:
    """Point *ref* at *tree* (never a commit). Never raises."""
    if not ref or not tree:
        return False
    try:
        return bool(PN._g_write(["update-ref", ref, tree], folder).ok)
    except Exception:  # noqa: BLE001 — a ref never fails a message
        log.debug("could not keep a checkpoint's ref", exc_info=True)
        return False


def _delete_refs(names: list[str], *, folder: str = "", repo: str = "") -> None:
    """One ``git update-ref --stdin`` in *folder*, or (the folder is gone)
    straight in the repository's git directory *repo*."""
    from ..harness.branches import _g

    body = "".join(f"delete {r}\n" for r in names).encode("utf-8")
    if _usable(folder):
        _g(["update-ref", "--stdin"], folder, reads=False, input=body)
    elif _usable(repo):
        _g(["--git-dir", repo, "update-ref", "--stdin"], repo, reads=False, input=body)


def drop(folder: str, refs: list[str], repo: str = "") -> None:
    """Delete these refs in one git call. Never raises."""
    names = [r for r in refs if isinstance(r, str) and r.startswith(REF_ROOT + "/")]
    if not names:
        return
    try:
        _delete_refs(names, folder=folder, repo=repo)
    except Exception:  # noqa: BLE001
        log.debug("could not drop checkpoint refs", exc_info=True)


def drop_rows(folder: str, rows: list[dict[str, Any]]) -> None:
    """The refs of rows that left the record (each through its own
    repository when the folder is gone). Never raises."""
    by: dict[str, list[str]] = {}
    for r in rows:
        ref = str(r.get("ref") or "")
        if ref:
            by.setdefault(str(r.get("repo") or ""), []).append(ref)
    for repo, refs in by.items():
        drop(folder, refs, repo)


def forget_session(home: Any, sid: str, folder: str) -> int:
    """A deleted session's checkpoint refs go with it (git then cleans the
    trees up in its own time): looked for in the folder AND in every
    repository its rows name, so a folder that is already gone (a removed
    worktree) leaves nothing behind. Returns how many it named. Never raises.
    Call it BEFORE the session's record is moved away."""
    try:
        root = ref_for(sid, "x").rsplit("/", 1)[0] if ref_for(sid, "x") else ""
        if not root:
            return 0
        from ..harness import branches as B

        if B.git_path() is None:
            return 0
        places: list[tuple[str, str]] = []
        if _usable(folder):
            places.append(("", folder))
        for repo in sorted({str(r.get("repo") or "") for r in PN.read_trees(home, sid)}):
            if _usable(repo):
                places.append((repo, repo))
        named: set[str] = set()
        for gitdir, cwd in places:
            pre = ["--git-dir", gitdir] if gitdir else []
            got = PN._git([*pre, "for-each-ref", "--format=%(refname)", root + "/"], cwd)
            names = [ln.strip() for ln in got.out.splitlines() if ln.strip()] if got.ok else []
            names = [n for n in names if n.startswith(root + "/")]
            if names:
                _delete_refs(names, folder="" if gitdir else cwd, repo=gitdir)
                named.update(names)
        return len(named)
    except Exception:  # noqa: BLE001
        log.debug("could not forget a session's checkpoint refs", exc_info=True)
        return 0


def forget_turns(home: Any, sid: str, folder: str, kept_turns: set[str]) -> int:
    """A conversation rewind dropped messages: their checkpoints go with them
    (no message is left to rewind to). Returns how many. Never raises."""
    try:
        with PN._TREES_LOCK:
            rows = PN.read_trees(home, sid)
            gone = [r for r in rows if r.get("how") == HOW_START and str(r.get("turn") or "") not in kept_turns]
            if gone:
                PN._write_trees(home, sid, [r for r in rows if r not in gone])
        drop_rows(folder, gone)
        return len(gone)
    except Exception:  # noqa: BLE001 — the rewind itself is done
        log.debug("could not forget rewound-away checkpoints", exc_info=True)
        return 0


def tree_kept(folder: str, tree: str) -> bool:
    """Does the repository still hold this tree? (``git gc`` can delete one
    recorded before refs were kept.)"""
    if not tree or not re.fullmatch(r"[0-9a-f]{40,64}", str(tree)):
        return False
    return bool(PN._git(["cat-file", "-e", f"{tree}^{{tree}}"], folder).ok)


def trim(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(kept, dropped)``: each kind of checkpoint has its own limit, so idle
    reads can never push a message's checkpoint out (they shared one limit of
    60). Order is kept."""
    limits = {HOW_START: MAX_STARTS, HOW_SEEN: MAX_SEEN, HOW_BEFORE: MAX_BEFORE}
    seen: dict[str, int] = {}
    keep_at: set[int] = set()
    for i in range(len(rows) - 1, -1, -1):
        how = str(rows[i].get("how") or "")
        n = seen.get(how, 0)
        if n < limits.get(how, MAX_SEEN):
            keep_at.add(i)
            seen[how] = n + 1
    kept = [r for i, r in enumerate(rows) if i in keep_at]
    dropped = [r for i, r in enumerate(rows) if i not in keep_at]
    return kept, dropped


def head_of(folder: str) -> str:
    """The commit HEAD names now ("" when none, or git did not say)."""
    try:
        got = PN._git(["rev-parse", "-q", "--verify", "HEAD^{commit}"], folder)
        return got.out.strip() if got.ok else ""
    except Exception:  # noqa: BLE001
        return ""


# --------------------------------------------------------------------------- #
# the plan
# --------------------------------------------------------------------------- #


def start_row(home: Any, sid: str, turn_id: str) -> dict[str, Any] | None:
    """The checkpoint recorded when *turn_id* started (None when none)."""
    for row in PN.read_trees(home, sid):
        if row.get("how") == HOW_START and row.get("turn") == turn_id:
            return row
    return None


def _raw(folder: str, a: str, b: str) -> list[tuple[str, str, str, str]]:
    """``[(status, src mode, dst mode, rel)]`` between two trees, inside the
    session's folder (renames off: every path is its own row)."""
    got = PN._git(["diff", "--raw", "-z", "--no-renames", "--no-abbrev", "--relative", a, b, "--", "."], folder)
    if not got.ok:
        raise PN.GitUnreadable(got)
    out: list[tuple[str, str, str, str]] = []
    parts = got.out.split("\0")
    i = 0
    while i + 1 < len(parts):
        head, rel = parts[i], parts[i + 1]
        i += 2
        if not head.startswith(":"):
            continue
        cols = head[1:].split(" ")
        if len(cols) < 5 or not rel:
            continue
        out.append((cols[4][:1], cols[0], cols[1], rel))
    return out


def _lstat(path: str) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except (OSError, ValueError):
        return None


def _link(path: str, st: os.stat_result) -> bool:
    from ..harness.code_editor import _is_link

    return _is_link(path, st)


def _writable(path: str) -> bool:
    """Can this existing file be written now? A file another program holds
    open without sharing cannot (Windows). A read-only file is not judged
    (git replaces those itself)."""
    try:
        if not os.path.lexists(path) or os.path.isdir(path):
            return True
        if not os.access(path, os.W_OK):
            return True
        fd = os.open(path, os.O_RDWR | getattr(os, "O_BINARY", 0))
        os.close(fd)
        return True
    except PermissionError:
        return False
    except OSError:
        return True


def _only_new_inside(path: str, rel: str, new: set[str]) -> bool:
    """Does the folder *path* (in a file's place) hold ONLY files that are
    about to be moved to the trash? A link inside, or a file that is in no
    checkpoint (git ignores it, or it sits in a nested repository), answers
    no: git would delete the whole folder, that file with it. Links are never
    followed."""
    seen = 0
    todo = [(path, rel)]
    while todo:
        at, at_rel = todo.pop()
        try:
            entries = list(os.scandir(at))
        except OSError:
            return False
        for e in entries:
            seen += 1
            if seen > WALK_MAX:
                return False
            child_rel = f"{at_rel}/{e.name}"
            st = _lstat(e.path)
            if st is None or _link(e.path, st):
                return False
            if stat.S_ISDIR(st.st_mode):
                todo.append((e.path, child_rel))
            elif child_rel not in new:
                return False
    return True


def _parent_in_the_way(folder: str, rel: str, new: set[str]) -> bool:
    """Is a part of *rel*'s own folder path a FILE now (not one that is about
    to be moved)? git would delete it to make the folder."""
    parts = rel.split("/")[:-1]
    for i in range(len(parts)):
        sub = "/".join(parts[: i + 1])
        st = _lstat(os.path.join(folder, *parts[: i + 1]))
        if st is None:
            return False
        if _link(os.path.join(folder, *parts[: i + 1]), st):
            return True
        if not stat.S_ISDIR(st.st_mode):
            return sub not in new
    return False


def _endings(folder: str) -> str:
    """Why a restored file's line endings can differ from what it had ("" when
    nothing here changes them). Never raises."""
    try:
        got = PN._git(["config", "--get", "core.autocrlf"], folder)
        value = got.out.strip().lower() if got.ok else ""
        if value in ("true", "input"):
            return ENDINGS.format(why=f"core.autocrlf is {value}")
        at = os.path.abspath(folder)
        for _ in range(40):
            if os.path.isfile(os.path.join(at, ".gitattributes")):
                return ENDINGS.format(why="a .gitattributes file")
            if os.path.lexists(os.path.join(at, ".git")) or os.path.dirname(at) == at:
                break
            at = os.path.dirname(at)
    except Exception:  # noqa: BLE001
        return ""
    return ""


def _plan(home: Any, sid: str, folder: str, tree: str, *, head: str = "") -> dict[str, Any]:
    """What putting *folder* back to *tree* would do. Reads only."""
    now = PN.snapshot_tree(folder, keep=PN.kept_index(home, sid))
    left: list[dict[str, str]] = []
    problems: list[dict[str, str]] = []
    rows: list[tuple[str, str]] = []  # (status, clean rel) of the rows that may be acted on
    for status, src, dst, rel in _raw(folder, tree, now):
        if "160000" in (src, dst):
            left.append({"rel": rel, "why": WHY_SUBMODULE})
            continue
        if "120000" in (src, dst):
            left.append({"rel": rel, "why": WHY_LINK})
            continue
        try:
            if "�" in rel:  # a name git's output could not spell here
                raise PN.RevertRefused(PN.REVERT_BAD_PATH, 400)
            rows.append((status, PN._clean_rel(folder, rel)))
        except PN.RevertRefused:
            left.append({"rel": rel, "why": WHY_PATH})
    # The files that would move to the trash, before any of them is judged: a
    # folder or a file in another path's way is fine when it is one of these.
    new_all = {rel for status, rel in rows if status == "A"}
    folded = {rel.casefold(): rel for rel in new_all}
    case_only: set[str] = set()
    back: list[str] = []
    gone: list[str] = []  # the part of ``back`` that is not in the folder now
    for status, clean in rows:
        if status == "A":
            continue
        path = os.path.join(folder, *clean.split("/"))
        st = _lstat(path)
        if st is not None and _link(path, st):
            left.append({"rel": clean, "why": WHY_LINK})
            continue
        if st is not None and stat.S_ISDIR(st.st_mode):
            if not _only_new_inside(path, clean, new_all):
                problems.append({"rel": clean, "why": WHY_FOLDER})
                continue
        elif status == "D" and st is not None:
            twin = folded.get(clean.casefold())
            if twin is not None and twin != clean:
                # The same file under a name that differs only in case (a
                # file system that ignores case): neither is touched.
                case_only.add(twin)
                left.append({"rel": clean, "why": WHY_CASE})
            else:
                # On disk, yet not in the folder's tree: git ignores it now.
                # Its content is in NO checkpoint, so it is never written over.
                left.append({"rel": clean, "why": WHY_IGNORED})
            continue
        if _parent_in_the_way(folder, clean, new_all):
            problems.append({"rel": clean, "why": WHY_PARENT})
            continue
        if not _writable(path):
            problems.append({"rel": clean, "why": WHY_LOCKED})
            continue
        back.append(clean)
        if status == "D":
            gone.append(clean)
    new: list[str] = []
    for status, clean in rows:
        if status != "A" or clean in case_only:
            continue
        if not _writable(os.path.join(folder, *clean.split("/"))):
            problems.append({"rel": clean, "why": WHY_LOCKED})
            continue
        new.append(clean)
    commits = 0
    if head:
        got = PN._git(["rev-list", "--count", f"{head}..HEAD"], folder)
        if got.ok and got.out.strip().isdigit():
            commits = int(got.out.strip())
    plan_id = hashlib.sha1(f"{tree}:{now}".encode("ascii", "replace")).hexdigest()[:16]
    return {
        "id": plan_id, "tree": tree, "now": now,
        "back": back, "gone": gone, "new": new, "left": left, "problems": problems,
        "commits_since": commits,
        "endings": _endings(folder) if back else "",
        "can": not problems and len(back) + len(new) <= RESTORE_MAX_FILES,
    }


def _checked(row: Any) -> str:
    folder = str(getattr(row, "folder", "") or "")
    if not folder:
        raise RestoreRefused(PN.NO_FOLDER)
    if PN._network(folder):
        raise RestoreRefused(PN.ON_SHARE)
    if not os.path.isdir(folder):
        raise RestoreRefused(PN.FOLDER_GONE)
    from ..harness import branches as B

    if B.git_path() is None:
        raise RestoreRefused(NOT_REPO)
    inside = PN._git(["rev-parse", "--is-inside-work-tree"], folder)
    if not inside.ok or inside.out.strip() != "true":
        raise RestoreRefused(NOT_REPO)
    return folder


def public(plan: dict[str, Any]) -> dict[str, Any]:
    """A plan as the screen gets it: counts exact, names capped, no tree ids
    but the plan's own id (what the second press sends back)."""
    def names(rows: list[Any]) -> list[Any]:
        return rows[:LIST_SHOWN]

    n = len(plan["back"]) + len(plan["new"])
    return {
        "id": plan["id"],
        "can": bool(plan["can"]),
        "back": names(plan["back"]), "back_count": len(plan["back"]),
        "new": names(plan["new"]), "new_count": len(plan["new"]),
        "left": names(plan["left"]), "left_count": len(plan["left"]),
        "problems": names(plan["problems"]), "problems_count": len(plan["problems"]),
        "commits_since": int(plan.get("commits_since") or 0),
        "endings": str(plan.get("endings") or ""),
        "too_many": TOO_MANY.format(n=n) if n > RESTORE_MAX_FILES else "",
    }


def plan(home: Any, sid: str, row: Any, turn_id: str) -> dict[str, Any]:
    """What "put the files back to before this message" would do (reads
    only). Raises :class:`RestoreRefused` when there is no usable
    checkpoint."""
    folder = _checked(row)
    entry = start_row(home, sid, turn_id) if turn_id else None
    if entry is None:
        raise RestoreRefused(NO_CHECKPOINT)
    tree = str(entry.get("tree") or "")
    try:
        if not tree_kept(folder, tree):
            raise RestoreRefused(CHECKPOINT_GONE)
        return _plan(home, sid, folder, tree, head=str(entry.get("head") or ""))
    except PN.GitUnreadable as gone:
        raise RestoreRefused(gone.words) from None


# --------------------------------------------------------------------------- #
# the restore
# --------------------------------------------------------------------------- #


def _index_dir(home: Any) -> Path:
    return Path(home).joinpath(*PN.KEPT_INDEX_DIR, "restore")


def _restore_paths(folder: str, tree: str, rels: list[str], index_dir: Any) -> Any:
    """``git restore --source=<tree> --worktree`` for exactly these paths
    (each a literal pathspec), through a PRIVATE index file: the user's own
    index is neither changed nor locked (measured: with their index, git holds
    ``.git/index.lock`` for the whole write and a git stopped half way leaves
    it there, so their next ``git add`` fails)."""
    from ..git.integration import git_env, run_git

    body = "\0".join(PN._literal(r) for r in rels).encode("utf-8")
    where = Path(index_dir)
    where.mkdir(parents=True, exist_ok=True)
    idx = where / f"{uuid.uuid4().hex}.idx"
    env = git_env({"GIT_INDEX_FILE": str(idx)})
    try:
        return run_git(
            ["-c", "core.quotepath=off", "restore", f"--source={tree}", "--worktree",
             "--pathspec-from-file=-", "--pathspec-file-nul"],
            Path(folder), env=env, identity=False, encoding="utf-8", timeout=RESTORE_S, input=body,
        )
    finally:
        for leftover in (idx, idx.with_name(idx.name + ".lock")):
            try:
                leftover.unlink()
            except OSError:
                pass


def _trash_root(home: Any, sid: str) -> Path:
    stamp = PN._now_utc().strftime("%Y%m%d-%H%M%S-%f")
    return Path(home) / "trash" / stamp / "code-rewind" / str(sid)


def _move_new(folder: str, dest_root: Path, rels: list[str], moved: list[tuple[str, str]]) -> None:
    """MOVE the files made since into the trash (never delete). Each move is
    added to *moved* as it happens, so a failure at any point (making the
    trash folder included) leaves the caller knowing exactly what to move
    back. Raises ``OSError``."""
    for rel in rels:
        src = os.path.join(folder, *rel.split("/"))
        if not os.path.lexists(src):
            continue
        dest = dest_root.joinpath(*rel.split("/"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(src, str(dest))
        moved.append((rel, str(dest)))


def _copy_replaced(folder: str, dest_root: Path, rels: list[str], copied: list[tuple[str, str]]) -> None:
    """COPY every file git is about to write over into the trash first (the
    v1.510.0 rule of the per-file Revert: no copy, no restore). Raises
    ``OSError``."""
    for rel in rels:
        src = os.path.join(folder, *rel.split("/"))
        st = _lstat(src)
        if st is None or not stat.S_ISREG(st.st_mode) or _link(src, st):
            continue
        dest = dest_root.joinpath(*rel.split("/"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, str(dest))
        copied.append((rel, str(dest)))


def _still_clear(folder: str, back: list[str], gone: set[str]) -> None:
    """Right before git writes: nothing that is in no checkpoint may stand
    where a file must go (the plan checked this; the folder is looked at
    again as it NOW is, after the new files moved out). Raises
    :class:`_Stopped`."""
    for rel in back:
        path = os.path.join(folder, *rel.split("/"))
        st = _lstat(path)
        if st is not None and (_link(path, st) or stat.S_ISDIR(st.st_mode) or rel in gone):
            raise _Stopped(WHY_IN_THE_WAY)
        at = folder
        for part in rel.split("/")[:-1]:
            at = os.path.join(at, part)
            ast = _lstat(at)
            if ast is None:
                break
            if _link(at, ast) or not stat.S_ISDIR(ast.st_mode):
                raise _Stopped(WHY_IN_THE_WAY)


def _prune_empty(folder: str, rels: list[str]) -> None:
    """Remove the folders the moved files left EMPTY (never one that still
    holds anything, never the session's folder itself)."""
    top = os.path.normcase(os.path.abspath(folder))
    for rel in rels:
        d = os.path.dirname(os.path.join(folder, *rel.split("/")))
        for _ in range(64):
            if os.path.normcase(os.path.abspath(d)) == top or not os.path.isdir(d):
                break
            try:
                os.rmdir(d)  # only an empty folder goes
            except OSError:
                break
            d = os.path.dirname(d)


def _roll_back(folder: str, root: Path, copied: list[tuple[str, str]], made_again: list[str],
               moved: list[tuple[str, str]]) -> bool:
    """Return the folder to how it was after a failed restore: the files git
    wrote over come back from their COPIES (exact bytes, no git needed), the
    ones git made again (they were not there) are MOVED aside into the trash,
    then the new files come back out of the trash. True when every step
    worked."""
    ok = True
    for rel, where in copied:
        dest = os.path.join(folder, *rel.split("/"))
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(where, dest)
        except OSError:
            ok = False
    aside: list[str] = []
    for rel in made_again:
        src = os.path.join(folder, *rel.split("/"))
        if not os.path.lexists(src):
            continue
        dest = root.joinpath(ROLLED, *rel.split("/"))
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(src, str(dest))
            aside.append(rel)
        except OSError:
            ok = False
    _prune_empty(folder, aside)
    for rel, where in reversed(moved):
        dest = os.path.join(folder, *rel.split("/"))
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if os.path.lexists(dest):
                ok = False  # something stands there now: it is never written over
                continue
            shutil.move(where, dest)
        except OSError:
            ok = False
    return ok


def _is_rules(rel: str) -> bool:
    return rel.rsplit("/", 1)[-1] == ".gitignore"


def _ignored_now(folder: str, rels: list[str]) -> set[str]:
    """Which of *rels* the folder's ignore rules cover NOW (by the rules
    alone, whatever any index holds). Unknown answers "none"."""
    if not rels:
        return set()
    try:
        body = "\0".join(rels).encode("utf-8")
        got = PN._git(["check-ignore", "--no-index", "-z", "--stdin"], folder, input=body)
        return {p for p in str(got.out or "").split("\0") if p} & set(rels)
    except Exception:  # noqa: BLE001
        return set()


def _is_as_it_was(home: Any, sid: str, folder: str, tree: str, fallback: bool) -> bool:
    """Is the folder the tree it was before the restore? Read from the folder
    itself (a roll-back that reported a fault may still have left it whole —
    a restore that never started; one that reported none is checked too).
    *fallback* when the folder cannot be read."""
    try:
        return PN.snapshot_tree(folder, keep=PN.kept_index(home, sid)) == tree
    except Exception:  # noqa: BLE001
        return fallback


def _flat(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def _exact_endings(folder: str, rels: list[str], copies: Path) -> set[str]:
    """After an UNDO's git write: a file whose bytes differ from the rewind's
    own copy of it ONLY in line endings gets that copy's exact bytes (git
    wrote the endings its settings give; the copy is the file as it really
    was). Any other difference is left as git wrote it. Returns the files it
    settled (exact now). Never raises."""
    settled: set[str] = set()
    for rel in rels:
        try:
            dest = os.path.join(folder, *rel.split("/"))
            dst_st = _lstat(dest)
            if dst_st is None or not stat.S_ISREG(dst_st.st_mode):
                continue
            for part in (REPLACED, MADE):
                src = copies.joinpath(part, *rel.split("/"))
                st = _lstat(str(src))
                if st is None or not stat.S_ISREG(st.st_mode) or st.st_size > EXACT_MAX_BYTES:
                    continue
                kept, now = src.read_bytes(), Path(dest).read_bytes()
                if _flat(kept) == _flat(now):
                    if kept != now:
                        Path(dest).write_bytes(kept)
                    settled.add(rel)
                break
        except OSError:
            continue
    return settled


def _style(data: bytes) -> str:
    """``crlf`` | ``lf`` when EVERY line of a text ends that way; "" for a
    file with both, with none, or with a NUL byte (not text)."""
    if b"\0" in data:
        return ""
    lines = data.count(b"\n")
    if not lines:
        return ""
    crlf = data.count(b"\r\n")
    if crlf == lines:
        return "crlf"
    return "lf" if crlf == 0 else ""


def _keep_endings(folder: str, copied: list[tuple[str, str]], skip: set[str]) -> int:
    """After git's write: a restored file whose line endings are uniformly
    ONE kind, where the version it replaced was uniformly the OTHER, is
    rewritten in the replaced version's kind (the module docstring says why).
    Only the line endings change; a file with mixed endings, no line at all
    or a NUL byte is left exactly as git wrote it. Returns how many it
    changed. Never raises."""
    changed = 0
    for rel, copy in copied:
        if rel in skip:
            continue
        try:
            dest = Path(os.path.join(folder, *rel.split("/")))
            st, cst = _lstat(str(dest)), _lstat(copy)
            if (st is None or cst is None or not stat.S_ISREG(st.st_mode)
                    or max(st.st_size, cst.st_size) > EXACT_MAX_BYTES):
                continue
            want = _style(Path(copy).read_bytes())
            now = dest.read_bytes()
            have = _style(now)
            if not want or not have or want == have:
                continue
            dest.write_bytes(now.replace(b"\n", b"\r\n") if want == "crlf" else _flat(now))
            changed += 1
        except OSError:
            continue
    return changed


def _apply(home: Any, sid: str, folder: str, tree: str, made: dict[str, Any], *,
           exact_from: Path | None = None) -> dict[str, Any]:
    """Put the folder back to *tree* per the plan *made* (already checked), in
    the module's five steps. A failure rolls back and says whether the folder
    is whole (read from the folder itself)."""
    back, new = list(made["back"]), list(made["new"])
    was_gone = set(made.get("gone") or [])
    root = _trash_root(home, sid)
    moved: list[tuple[str, str]] = []
    copied: list[tuple[str, str]] = []
    written: list[str] = []  # the paths git was asked to write so far
    stayed: list[str] = []

    def step(some_new: list[str], some_back: list[str]) -> None:
        """Steps 2 to 5 for one group of paths."""
        _move_new(folder, root / MADE, some_new, moved)
        _prune_empty(folder, [rel for rel, _ in moved])
        _still_clear(folder, some_back, was_gone)
        try:
            _copy_replaced(folder, root / REPLACED, [r for r in some_back if r not in was_gone], copied)
        except OSError as exc:
            from ..core.failures import plain_failure

            plain_failure(WHY_NO_COPY, exc)  # the raw text is logged, masked
            raise _Stopped(WHY_NO_COPY) from None
        if some_back:
            written.extend(some_back)
            got = _restore_paths(folder, tree, some_back, _index_dir(home))
            if not got.ok:
                raise _Stopped(WHY_GIT_SLOW if getattr(got, "timed_out", False) else PN._git_reason(got))

    try:
        rules_new = [r for r in new if _is_rules(r)]
        rules_back = [r for r in back if _is_rules(r)]
        if rules_new or rules_back:
            # The ignore rules go back first; a "made since" file the
            # checkpoint's own rules ignore is in no checkpoint: it stays.
            step(rules_new, rules_back)
            rest = [r for r in new if not _is_rules(r)]
            keep_here = _ignored_now(folder, rest)
            stayed = [r for r in rest if r in keep_here]
            step([r for r in rest if r not in keep_here], [r for r in back if not _is_rules(r)])
        else:
            step(new, back)
    except Exception as exc:  # noqa: BLE001 — every failure rolls back
        clean = _roll_back(folder, root, copied, [r for r in written if r in was_gone], moved)
        whole = _is_as_it_was(home, sid, folder, str(made["now"]), clean)
        if isinstance(exc, _Stopped):
            why = str(exc)
        else:
            from ..core.failures import plain_failure

            plain_failure(WHY_MOVE, exc)
            why = WHY_MOVE
        if whole:
            raise RestoreRefused(FAILED_ROLLED_BACK.format(why=why), 409) from None
        raise RestoreRefused(FAILED_PARTIAL.format(why=why, trash=str(root)), 500) from None
    settled = _exact_endings(folder, back, exact_from) if exact_from is not None else set()
    _keep_endings(folder, copied, settled)
    kept = bool(moved or copied)
    return {"back": len(back), "moved": len(moved), "replaced": len(copied), "stayed": len(stayed),
            "trash": str(root) if kept else ""}


def _words(result: dict[str, Any], made: dict[str, Any]) -> str:
    if not result["back"] and not result["moved"]:
        words = DONE_NOTHING
    else:
        words = DONE.format(back=result["back"], moved=result["moved"])
        if result.get("replaced"):
            words += DONE_KEPT
    n = int(made.get("commits_since") or 0)
    if n:
        words += DONE_COMMITS.format(n=n, s="" if n == 1 else "s", are="is" if n == 1 else "are")
    left = made.get("left") or []
    if left:
        whys = sorted({str(x.get("why") or "") for x in left})
        words += DONE_LEFT.format(n=len(left), they="it is" if len(left) == 1 else "they are", why="; ".join(whys))
    stayed = int(result.get("stayed") or 0)
    if stayed:
        words += DONE_STAYED.format(n=stayed, they="it is" if stayed == 1 else "they are")
    return words


def _refuse_unless_possible(made: dict[str, Any]) -> None:
    if made["problems"]:
        raise RestoreRefused(PROBLEMS, problems=list(made["problems"][:LIST_SHOWN]))
    if not made["can"]:
        raise RestoreRefused(TOO_MANY.format(n=len(made["back"]) + len(made["new"])))


def restore(home: Any, sid: str, row: Any, turn_id: str, *, expect: str = "") -> dict[str, Any]:
    """Put the session's folder back as it was when *turn_id* started.

    Order: the plan again (and it must still be the one the screen showed:
    *expect*) → a ``before_rewind`` checkpoint of the folder as it is, kept by
    a ref and checked → the files (:func:`_apply`). Returns ``{ok, words,
    back, moved, replaced, trash, before}``; raises :class:`RestoreRefused`."""
    made = plan(home, sid, row, turn_id)
    folder = str(row.folder)
    if expect and made["id"] != expect:
        raise RestoreRefused(FOLDER_CHANGED)
    _refuse_unless_possible(made)
    before = _keep_before(home, sid, folder, str(made["now"]), turn_id)
    try:
        result = _apply(home, sid, folder, str(made["tree"]), made)
    except RestoreRefused as r:
        if r.status == 409:
            # The folder is as it was: this attempt is nothing to undo, and
            # it must not hide an earlier rewind's Undo either (``void`` rows
            # are skipped; review: marking it "undone" ended the search).
            PN.mark_tree(home, sid, before, HOW_BEFORE, void=True)
        raise
    # Where this rewind's own copies are: an undo makes line endings exact
    # from them.
    PN.mark_tree(home, sid, before, HOW_BEFORE, trash=str(result.get("trash") or ""))
    return {"ok": True, "words": _words(result, made), "before": before, **result}


def _keep_before(home: Any, sid: str, folder: str, tree: str, turn_id: str) -> str:
    """Record the folder as it is NOW (the plan's own snapshot) as a
    ``before_rewind`` checkpoint. No ref, no restore."""
    at = PN._iso(PN._now_utc())
    ref = ref_for(sid, "before-" + re.sub(r"[^0-9]", "", at))
    if not keep(folder, ref, tree) or not tree_kept(folder, tree):
        raise RestoreRefused(NOT_KEPT)
    entry = {"how": HOW_BEFORE, "turn": str(turn_id), "tree": tree, "at": at, "ref": ref}
    repo = repo_of(folder)
    if repo:
        entry["repo"] = repo
    drop_rows(folder, PN._add_tree(home, sid, entry))
    return at


def close_undo(home: Any, sid: str) -> None:
    """A message is starting: every rewind made so far can no longer be
    undone. Said HERE, in the send path, and not read off the message's own
    checkpoint row — that row is missing when the folder could not be read in
    time, and a later conversation rewind deletes it (review: Undo came back
    and wrote over the message's work). Never raises."""
    try:
        with PN._TREES_LOCK:
            rows = PN.read_trees(home, sid)
            hit = False
            for r in rows:
                if r.get("how") == HOW_BEFORE and not r.get("closed"):
                    r["closed"] = True
                    hit = True
            if hit:
                PN._write_trees(home, sid, rows)
    except Exception:  # noqa: BLE001
        log.warning("could not close a Code-view session's rewind undo", exc_info=True)


def undo_row(home: Any, sid: str) -> tuple[dict[str, Any] | None, bool]:
    """``(row, late)``: the newest rewind that is still to be undone (one
    already undone, or one that changed nothing, is skipped), and whether a
    message started after it (then it no longer can be)."""
    rows = PN.read_trees(home, sid)
    for i in range(len(rows) - 1, -1, -1):
        r = rows[i]
        if r.get("how") != HOW_BEFORE or r.get("undone") or r.get("void"):
            continue
        late = bool(r.get("closed")) or any(x.get("how") == HOW_START for x in rows[i + 1:])
        return r, late
    return None, False


def can_undo(home: Any, sid: str) -> bool:
    row, late = undo_row(home, sid)
    return row is not None and not late


def undo(home: Any, sid: str, row: Any) -> dict[str, Any]:
    """Undo the newest rewind's FILE changes: the folder goes back to its
    ``before_rewind`` checkpoint, through the same steps as a restore (what
    the undo itself replaces or moves is kept in the trash). The conversation
    is not changed."""
    folder = _checked(row)
    entry, late = undo_row(home, sid)
    if entry is None:
        raise RestoreRefused(NOTHING_TO_UNDO)
    if late:
        raise RestoreRefused(UNDO_TOO_LATE)
    tree = str(entry.get("tree") or "")
    try:
        if not tree_kept(folder, tree):
            raise RestoreRefused(CHECKPOINT_GONE)
        made = _plan(home, sid, folder, tree)
    except PN.GitUnreadable as gone:
        raise RestoreRefused(gone.words) from None
    _refuse_unless_possible(made)
    copies = str(entry.get("trash") or "")
    result = _apply(home, sid, folder, tree, made,
                    exact_from=Path(copies) if copies and os.path.isdir(copies) else None)
    PN.mark_tree(home, sid, str(entry.get("at") or ""), HOW_BEFORE, undone=True)
    words = UNDONE + (UNDONE_KEPT if result.get("trash") else "")
    return {"ok": True, "words": words, **result}
