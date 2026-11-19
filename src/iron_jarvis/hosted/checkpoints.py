"""Build › Code: checkpoints that LAST, and the whole folder put back.

Every message of a Code-view session already records the folder as a git tree
(:func:`panes.record_turn_start`). Two things were missing:

* **Nothing kept that tree.** No ref pointed at it, so git's own clean-up
  (``git gc``, by default after about two weeks) could delete it. Now every
  recorded tree gets a hidden ref that points STRAIGHT AT THE TREE:
  ``refs/ironjarvis/checkpoints/<session>/<name>``. No commit is made, so the
  ref is not in ``git log --all``, ``git branch -a`` or ``git tag`` (measured:
  a tree ref survives ``git gc --prune=now``; the same tree with no ref is
  deleted). The refs go with the session (:func:`forget_session`).
* **A rewind never touched files.** :func:`plan` reads what putting the folder
  back to a message's checkpoint would do, and :func:`restore` does it: the
  files that changed are written back by their exact paths
  (``git restore --source=<tree> --worktree``; the user's staging area,
  branch and commits are never touched), the files made since are MOVED to
  Iron Jarvis's trash. The folder as it was just before is recorded first
  (a ``before_rewind`` checkpoint), so :func:`undo` can put it back.

Everything here is BLOCKING (a caller on the event loop hops it off) and the
caller holds the session's git lock (``build_git._locked("app:<sid>")``).
A refusal is one plain sentence (:class:`RestoreRefused`).

Not restored, and said in the plan: a nested repository (submodule), a link,
and every file git ignores (they are in no checkpoint).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
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

HOW_START = "start"
HOW_SEEN = "seen"
HOW_BEFORE = "before_rewind"

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
FAILED_ROLLED_BACK = "git could not put the files back ({why}), so the folder was left as it was."
FAILED_PARTIAL = (
    "git could not put every file back ({why}), and the folder could not be returned to how "
    "it was either. Press Undo rewind to try again."
)
NOTHING_TO_UNDO = "There is no rewind to undo."
UNDO_TOO_LATE = "A message was sent after that rewind, so it can no longer be undone from here."
WHY_LOCKED = "it cannot be written right now (another program may have it open)"
WHY_SUBMODULE = "it is a repository of its own"
WHY_LINK = "it is a link"
WHY_PATH = "its path cannot be used here"
DONE = (
    "The folder's files are back as they were when that message started: {back} put back, "
    "{moved} made since moved to Iron Jarvis's trash."
)
DONE_NOTHING = "The folder's files were already as they were when that message started."
DONE_COMMITS = " {n} commit{s} made since then {are} still in git, so git shows the difference as changes."
DONE_LEFT = " {n} left as {they} ({why})."
UNDONE = "The rewind's file changes were undone: the folder is as it was just before it."


class RestoreRefused(Exception):
    """A restore that did not happen, in one sentence (and an HTTP status)."""

    def __init__(self, sentence: str, status: int = 409, *, problems: list[dict[str, str]] | None = None) -> None:
        super().__init__(sentence)
        self.sentence = sentence
        self.status = status
        self.problems = problems or []


# --------------------------------------------------------------------------- #
# the refs
# --------------------------------------------------------------------------- #

_SAFE = re.compile(r"[^A-Za-z0-9_-]")


def _part(text: Any) -> str:
    """One path part of a ref name: letters, digits, ``_`` and ``-`` only
    (git's rules refuse much more than this ever holds)."""
    return _SAFE.sub("_", str(text or ""))[:120].strip("_-")


def ref_for(sid: Any, name: Any) -> str:
    """The hidden ref for one checkpoint, or "" when either part is empty."""
    a, b = _part(sid), _part(name)
    return f"{REF_ROOT}/{a}/{b}" if a and b else ""


def keep(folder: str, ref: str, tree: str) -> bool:
    """Point *ref* at *tree* (never a commit). Never raises."""
    if not ref or not tree:
        return False
    try:
        return bool(PN._g_write(["update-ref", ref, tree], folder).ok)
    except Exception:  # noqa: BLE001 — a ref never fails a message
        log.debug("could not keep a checkpoint's ref", exc_info=True)
        return False


def drop(folder: str, refs: list[str]) -> None:
    """Delete these refs in one git call. Never raises."""
    names = [r for r in refs if isinstance(r, str) and r.startswith(REF_ROOT + "/")]
    if not names or not folder:
        return
    try:
        if PN._network(folder) or not os.path.isdir(folder):
            return
        body = "".join(f"delete {r}\n" for r in names).encode("utf-8")
        from ..harness.branches import _g

        _g(["update-ref", "--stdin"], folder, reads=False, input=body)
    except Exception:  # noqa: BLE001
        log.debug("could not drop checkpoint refs", exc_info=True)


def forget_session(home: Any, sid: str, folder: str) -> int:
    """A deleted session's checkpoint refs go with it (git then cleans the
    trees up in its own time). Returns how many it named. Never raises."""
    try:
        root = ref_for(sid, "x").rsplit("/", 1)[0] if ref_for(sid, "x") else ""
        if not root or not folder or PN._network(folder) or not os.path.isdir(folder):
            return 0
        from ..harness import branches as B

        if B.git_path() is None:
            return 0
        got = PN._git(["for-each-ref", "--format=%(refname)", root + "/"], folder)
        names = [ln.strip() for ln in got.out.splitlines() if ln.strip()] if got.ok else []
        drop(folder, names)
        return len(names)
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
        drop(folder, [str(r.get("ref") or "") for r in gone])
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


def _plan(home: Any, sid: str, folder: str, tree: str, *, head: str = "") -> dict[str, Any]:
    """What putting *folder* back to *tree* would do. Reads only."""
    now = PN.snapshot_tree(folder, keep=PN.kept_index(home, sid))
    back: list[str] = []
    gone: list[str] = []  # the part of ``back`` that is not in the folder now
    new: list[str] = []
    left: list[dict[str, str]] = []
    problems: list[dict[str, str]] = []
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
            clean = PN._clean_rel(folder, rel)
        except PN.RevertRefused:
            left.append({"rel": rel, "why": WHY_PATH})
            continue
        path = os.path.join(folder, *clean.split("/"))
        if not _writable(path):
            problems.append({"rel": clean, "why": WHY_LOCKED})
            continue
        if status == "A":
            new.append(clean)
        else:
            back.append(clean)
            if status == "D":
                gone.append(clean)
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

    return {
        "id": plan["id"],
        "can": bool(plan["can"]),
        "back": names(plan["back"]), "back_count": len(plan["back"]),
        "new": names(plan["new"]), "new_count": len(plan["new"]),
        "left": names(plan["left"]), "left_count": len(plan["left"]),
        "problems": names(plan["problems"]), "problems_count": len(plan["problems"]),
        "commits_since": int(plan.get("commits_since") or 0),
        "too_many": len(plan["back"]) + len(plan["new"]) > RESTORE_MAX_FILES,
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


def _restore_paths(folder: str, tree: str, rels: list[str]) -> Any:
    """``git restore --source=<tree> --worktree`` for exactly these paths
    (each a literal pathspec; the staging area is not touched)."""
    body = "\0".join(PN._literal(r) for r in rels).encode("utf-8")
    from ..harness.branches import _g

    return _g(["restore", f"--source={tree}", "--worktree", "--pathspec-from-file=-", "--pathspec-file-nul"],
              folder, reads=False, input=body)


def _trash_root(home: Any, sid: str) -> Path:
    stamp = PN._now_utc().strftime("%Y%m%d-%H%M%S-%f")
    return Path(home) / "trash" / stamp / "code-rewind" / str(sid)


def _move_new(folder: str, root: Path, rels: list[str]) -> list[tuple[str, str]]:
    """MOVE the files made since into the trash (never delete). Returns
    ``[(rel, where)]`` for what moved; raises ``OSError`` at the first that
    cannot move (the caller rolls back)."""
    moved: list[tuple[str, str]] = []
    for rel in rels:
        src = os.path.join(folder, *rel.split("/"))
        if not os.path.lexists(src):
            continue
        dest = root.joinpath(*rel.split("/"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(src, str(dest))
        except OSError as exc:
            exc.moved = moved  # type: ignore[attr-defined]
            raise
        moved.append((rel, str(dest)))
    return moved


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


class _GitFailed(Exception):
    """git's own reason for a restore that did not finish."""


def _roll_back(folder: str, root: Path, before: str, changed: list[str], gone: list[str],
               moved: list[tuple[str, str]]) -> bool:
    """Return the folder to *before* after a failed restore: the files git
    wrote over go back from *before*, the ones it made again (they were not
    there) are MOVED aside into the trash, then the new files come back out of
    the trash. True when the folder is whole again."""
    ok = True
    if changed:
        try:
            ok = bool(_restore_paths(folder, before, changed).ok)
        except Exception:  # noqa: BLE001
            ok = False
    aside: list[str] = []
    for rel in gone:
        src = os.path.join(folder, *rel.split("/"))
        if not os.path.lexists(src):
            continue
        dest = root.joinpath("rolled-back", *rel.split("/"))
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
                ok = False
                continue
            shutil.move(where, dest)
        except OSError:
            ok = False
    return ok


def _apply(home: Any, sid: str, folder: str, tree: str, made: dict[str, Any]) -> dict[str, Any]:
    """Put the folder back to *tree* per the plan *made* (already checked).

    The files made since move out FIRST (a file that is a folder in the
    checkpoint, or the other way round, then goes back cleanly), then git
    writes the rest. A failure rolls back to ``made["now"]``."""
    back, new = list(made["back"]), list(made["new"])
    was_gone = set(made.get("gone") or [])
    root = _trash_root(home, sid)
    moved: list[tuple[str, str]] = []
    wrote = False
    try:
        moved = _move_new(folder, root, new)
        _prune_empty(folder, [rel for rel, _ in moved])
        if back:
            wrote = True
            got = _restore_paths(folder, tree, back)
            if not got.ok:
                raise _GitFailed(PN._git_reason(got))
    except Exception as exc:  # noqa: BLE001 — every failure rolls back
        moved = getattr(exc, "moved", moved)
        whole = _roll_back(
            folder, root, str(made["now"]),
            [r for r in back if r not in was_gone] if wrote else [],
            [r for r in back if r in was_gone] if wrote else [],
            moved,
        )
        why = str(exc) if isinstance(exc, _GitFailed) else "a file could not be moved"
        words = (FAILED_ROLLED_BACK if whole else FAILED_PARTIAL).format(why=why)
        if not isinstance(exc, _GitFailed):
            from ..core.failures import plain_failure

            words = plain_failure(words, exc)  # the raw text is logged, masked
        raise RestoreRefused(words, 409 if whole else 500) from None
    return {"back": len(back), "moved": len(moved), "trash": str(root) if moved else ""}


def _words(result: dict[str, Any], made: dict[str, Any]) -> str:
    if not result["back"] and not result["moved"]:
        words = DONE_NOTHING
    else:
        words = DONE.format(back=result["back"], moved=result["moved"])
    n = int(made.get("commits_since") or 0)
    if n:
        words += DONE_COMMITS.format(n=n, s="" if n == 1 else "s", are="is" if n == 1 else "are")
    left = made.get("left") or []
    if left:
        whys = sorted({str(x.get("why") or "") for x in left})
        words += DONE_LEFT.format(n=len(left), they="it is" if len(left) == 1 else "they are", why="; ".join(whys))
    return words


def restore(home: Any, sid: str, row: Any, turn_id: str, *, expect: str = "") -> dict[str, Any]:
    """Put the session's folder back as it was when *turn_id* started.

    Order: the plan again (and it must still be the one the screen showed:
    *expect*) → a ``before_rewind`` checkpoint of the folder as it is, kept by
    a ref and checked → the files. Returns ``{ok, words, back, moved, trash,
    before}``; raises :class:`RestoreRefused` with nothing changed."""
    made = plan(home, sid, row, turn_id)
    folder = str(row.folder)
    if expect and made["id"] != expect:
        raise RestoreRefused(FOLDER_CHANGED)
    if made["problems"]:
        raise RestoreRefused(PROBLEMS, problems=list(made["problems"][:LIST_SHOWN]))
    if not made["can"]:
        raise RestoreRefused(TOO_MANY.format(n=len(made["back"]) + len(made["new"])))
    before = _keep_before(home, sid, folder, str(made["now"]), turn_id)
    try:
        result = _apply(home, sid, folder, str(made["tree"]), made)
    except RestoreRefused as r:
        if r.status == 409:
            # The folder was returned to how it was: there is nothing to undo.
            # (A partial return keeps the row: Undo rewind is the way to try again.)
            PN.mark_tree(home, sid, before, HOW_BEFORE, undone=True)
        raise
    return {"ok": True, "words": _words(result, made), "before": before, **result}


def _keep_before(home: Any, sid: str, folder: str, tree: str, turn_id: str) -> str:
    """Record the folder as it is NOW (the plan's own snapshot) as a
    ``before_rewind`` checkpoint. No ref, no restore."""
    at = PN._iso(PN._now_utc())
    ref = ref_for(sid, "before-" + re.sub(r"[^0-9]", "", at))
    if not keep(folder, ref, tree) or not tree_kept(folder, tree):
        raise RestoreRefused(NOT_KEPT)
    dropped = PN._add_tree(home, sid, {"how": HOW_BEFORE, "turn": str(turn_id), "tree": tree, "at": at, "ref": ref})
    drop(folder, [str(r.get("ref") or "") for r in dropped])
    return at


def undo_row(home: Any, sid: str) -> tuple[dict[str, Any] | None, bool]:
    """``(row, late)``: the newest rewind that can still be undone, and
    whether a message started after it (then it no longer can)."""
    rows = PN.read_trees(home, sid)
    for i in range(len(rows) - 1, -1, -1):
        r = rows[i]
        if r.get("how") != HOW_BEFORE:
            continue
        if r.get("undone"):
            return None, False
        late = any(x.get("how") == HOW_START for x in rows[i + 1:])
        return r, late
    return None, False


def can_undo(home: Any, sid: str) -> bool:
    row, late = undo_row(home, sid)
    return row is not None and not late


def undo(home: Any, sid: str, row: Any) -> dict[str, Any]:
    """Undo the newest rewind's FILE changes: the folder goes back to its
    ``before_rewind`` checkpoint. The conversation is not changed."""
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
    if made["problems"]:
        raise RestoreRefused(PROBLEMS, problems=list(made["problems"][:LIST_SHOWN]))
    if not made["can"]:
        raise RestoreRefused(TOO_MANY.format(n=len(made["back"]) + len(made["new"])))
    result = _apply(home, sid, folder, tree, made)
    PN.mark_tree(home, sid, str(entry.get("at") or ""), HOW_BEFORE, undone=True)
    return {"ok": True, "words": UNDONE, **result}
