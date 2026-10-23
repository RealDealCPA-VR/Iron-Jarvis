"""A Build session on its OWN git branch (v1.375.0, IJ harness part 2 Phase 4).

The user's decisions (``docs/IJ-HARNESS-PLAN-2.md``, decisions 1–3, final):

1. When the folder a NEW session works in is inside a git repository, the
   session works on its own branch ``ij/<short-name>`` in a separate git
   WORKTREE by default; New session has a switch to work in the folder
   directly (exactly the old behaviour). A folder that is not in a git
   repository works exactly as before (nothing here runs for it but one
   ``git rev-parse``).
2. Worktrees live BESIDE the repository:
   ``<repo-parent>/<repo>.ij-worktrees/<short-name>`` — never inside it, so
   they never show as changes there.
3. Finishing: **Create PR** (GitHub CLI installed and signed in; otherwise
   the compare link), **Merge into <base>** (a normal merge — never a force
   push, never a rewrite of the base branch, never a deleted branch) and
   **Discard** (the worktree folder MOVES to ``<home>/trash/<stamp>/``,
   git forgets that worktree (only it), and the BRANCH STAYS until the user
   deletes it).

THE FACTS (:func:`clean_git_record`) live on the session's setup under
``git`` — the repository, the folder the user picked (``checkout``), the
worktree, the branch, its base and the base commit at the start — written
ONLY by the /build routes (the v1.367.0/v1.368.0 rule; a PATCH or a chat PUT
cannot forge them). The session's working folder (``setup.workspace_dir``)
IS the worktree (or its copy of the folder the user picked inside it).

EVERY git and gh call goes through ``git.integration.run_git`` /
``core.procs`` (its own Job, bounded, the tree killed on a timeout that says
so in words), with ``GIT_TERMINAL_PROMPT=0`` — nothing waits on a password
typed into a terminal nobody sees. Every function here is BLOCKING: the
routes hop them off the event loop. Technical output never reaches a
sentence: it rides ``details``, masked (``detections.redact.mask`` — a
remote address can carry a token).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import secrets
import shutil
import tempfile
import time
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..core.ids import utcnow
from ..git.integration import (
    GitError,
    GitSession,
    default_identity,
    git_env,
    has_identity,
    run_git,
)

log = logging.getLogger(__name__)

#: Every session branch starts with this (the user's decision 1).
BRANCH_PREFIX = "ij/"
#: The folder beside the repository that holds its session worktrees.
WORKTREES_SUFFIX = ".ij-worktrees"
#: The setup key the facts live under.
SETUP_KEY = "git"

#: Bounds, each said in words when it runs out.
QUICK_S = 30.0  # a read: status, diff, a ref
START_S = 300.0  # making a worktree of a big repository
PUSH_S = 120.0  # a push (a sign-in window may be waiting on the user)
GH_S = 60.0  # one GitHub CLI call
GH_AUTH_S = 20.0  # gh auth status
#: How long "is gh signed in" is remembered (it makes a network call).
GH_AUTH_TTL_S = 60.0

#: The branch view's bounds (the Changes pane's own, routes/chat_changes).
MAX_FILES = 50
READ_CAP = 1_000_000
DIFF_CAP = 200_000
#: What a commit-message draft is shown of the change, at most.
DRAFT_DIFF_CHARS = 12_000
#: A commit message, at most (the box's limit too).
MAX_MESSAGE_CHARS = 5_000
#: A pull request's title / body, at most.
MAX_TITLE_CHARS = 250
MAX_BODY_CHARS = 20_000

NO_GIT = "Git is not installed on this PC, so sessions work in the folder directly."
NOT_A_REPO = "This folder is not in a git repository, so the session works in it directly."
NO_COMMITS = (
    "This repository has no commits yet, so a branch cannot start from it. Make a first "
    "commit, or work in the folder directly."
)
DRIVE_ROOT = (
    "This repository is at the top of a drive, so there is no folder beside it for the "
    "session's branch. Work in the folder directly."
)
BARE_REPO = "This is a bare repository with no files to work on. Work in the folder directly."
SUBMODULE = (
    "This folder is inside a git submodule, so a branch for it would sit inside the repository "
    "that holds it. Work in the folder directly."
)
NO_BRANCH = "This session works in its folder directly; it has no branch of its own."
GONE = "This session's worktree is gone (discarded or removed), so there is nothing to do here."
DETACHED_BASE = (
    "This session started from a commit, not from a branch, so there is no branch to merge "
    "into or open a pull request against."
)
NOTHING_TO_COMMIT = "Nothing to commit: the branch has no changes that are not committed yet."
EMPTY_MESSAGE = "Write a commit message first."
NO_REMOTE = "This repository has no remote to push to. Add one with git, then push again."
NOTHING_TO_MERGE = "Nothing to merge: {base} already has everything on this branch."
UNCOMMITTED_BEFORE_MERGE = (
    "This branch has changes that are not committed yet. Commit them first, or they would "
    "not be part of the merge. Nothing was merged."
)
NOT_A_WORKTREE = "That folder does not look like this session's worktree, so it was left alone."

#: git's own words in a push refusal that means "the remote moved".
_REJECTED_RX = re.compile(r"\[rejected\]|non-fast-forward|fetch first|stale info", re.I)
#: … and in one that means "the remote would not let you in".
_AUTH_RX = re.compile(
    r"authentication failed|could not read username|permission denied|access denied|"
    r"403|401|terminal prompts disabled|could not read from remote",
    re.I,
)


class GitRefused(Exception):
    """A git action refused or failed, in ONE plain sentence. ``status`` is the
    HTTP status a route answers with; ``details`` is git's own output, already
    masked (the screen folds it under Details)."""

    def __init__(self, message: str, status: int = 409, details: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.details = details


# --------------------------------------------------------------------------- #
# tools on PATH
# --------------------------------------------------------------------------- #


def git_path() -> str | None:
    """Where ``git`` is on PATH (the frozen app inherits the desktop's), or
    None — then sessions work in their folder directly."""
    return shutil.which("git")


def gh_path() -> str | None:
    """Where the GitHub CLI (``gh``) is on PATH, or None — then Create PR
    offers the compare link instead."""
    return shutil.which("gh")


def _masked(text: str, cap: int = 4_000) -> str:
    """Technical output for Details: masked (a remote address can carry a
    token), trimmed, never in a sentence."""
    from ..detections.redact import mask

    out = str(mask(str(text or "").strip()))
    return out if len(out) <= cap else out[:cap] + "\n[…]"


def _g(args: list[str], cwd: Any, *, timeout: float = QUICK_S, reads: bool = False,
       identity: bool = False, input: "bytes | None" = None) -> Any:
    """One git call of a Build session: :func:`git_env` (no terminal prompt;
    ``GIT_OPTIONAL_LOCKS=0`` for a read, so a status never takes the index
    lock of a checkout the user works in), UTF-8 output, the user's own
    identity unless ``identity``."""
    env = git_env({"GIT_OPTIONAL_LOCKS": "0"} if reads else None)
    return run_git(["-c", "core.quotepath=off", *args], Path(cwd), timeout=timeout, env=env,
                   identity=identity, input=input, encoding="utf-8")


def _timed_out_words(what: str, seconds: float) -> str:
    mins = int(seconds // 60)
    span = f"{mins} minute{'s' if mins != 1 else ''}" if mins >= 1 else f"{int(seconds)} seconds"
    return f"{what} did not finish within {span}, so it was stopped."


# --------------------------------------------------------------------------- #
# the session's facts (the setup whitelist)
# --------------------------------------------------------------------------- #

#: The keys of ``setup.git`` — the facts at the start, then what happened.
GIT_KEYS: tuple[str, ...] = (
    "repo", "checkout", "main", "worktree", "branch", "base", "base_commit", "name",
    "created_at", "remote", "pushed_at", "pr_url", "pr_number", "merged_at",
    "merged_into", "merge_commit", "removed_at", "trash",
)


def clean_git_record(raw: Any) -> "dict[str, str] | None":
    """``setup.git`` as stored: only :data:`GIT_KEYS`, strings, bounded — and
    nothing at all unless it names a worktree, a repository and an ``ij/``
    branch (the whitelist of ``routes/chat._clean_setup``)."""
    if not isinstance(raw, dict):
        return None
    out: dict[str, str] = {}
    for key in GIT_KEYS:
        v = raw.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and key == "pr_number":
            v = str(int(v))
        if isinstance(v, str) and v.strip():
            out[key] = v.strip()[:1000]
    if not out.get("worktree") or not out.get("repo") or not str(out.get("branch", "")).startswith(BRANCH_PREFIX):
        return None
    return out


def facts_of(setup: Any) -> "dict[str, str] | None":
    """The session's git facts (``setup.git``), or None for a session that
    works in its folder directly."""
    if not isinstance(setup, dict):
        return None
    return clean_git_record(setup.get(SETUP_KEY))


def view(facts: "dict[str, str] | None") -> "dict[str, Any] | None":
    """What a session's view says about its branch (``git`` on
    ``GET /build/sessions/{id}``), or None. No git call."""
    if not facts:
        return None
    return {
        "branch": facts.get("branch", ""),
        "base": facts.get("base", ""),
        "base_commit": facts.get("base_commit", "")[:12],
        "worktree": facts.get("worktree", ""),
        "checkout": facts.get("checkout", ""),
        "repo": facts.get("repo", ""),
        "merged": bool(facts.get("merged_at")),
        "merged_into": facts.get("merged_into", ""),
        "removed": bool(facts.get("removed_at")),
        "pr_url": facts.get("pr_url", ""),
    }


# --------------------------------------------------------------------------- #
# the folder: is it in a repository, and where would its worktrees go
# --------------------------------------------------------------------------- #


def _norm(path: str) -> str:
    """git prints ``C:/Users/…`` on Windows; this app stores ``C:\\Users\\…``."""
    try:
        return os.path.normpath(str(path).strip())
    except (TypeError, ValueError):
        return str(path or "").strip()


def _main_checkout(root: str) -> "tuple[str, bool]":
    """The repository's MAIN working tree (the first entry of ``git worktree
    list``) and whether it is bare. A pick inside a linked worktree still
    gets its session worktree beside the main one."""
    got = _g(["worktree", "list", "--porcelain"], root, reads=True)
    if not got.ok:
        return root, False
    first, bare = "", False
    for line in got.out.splitlines():
        if line.startswith("worktree ") and not first:
            first = _norm(line[len("worktree "):])
        elif first and line.strip() == "bare":
            bare = True
        elif first and not line.strip():
            break
    return (first or root), bare


def worktrees_dir_for(main: str) -> str:
    """``<repo-parent>/<repo>.ij-worktrees`` (the user's decision 2), or ""
    when the repository is a drive's top folder (there is no beside)."""
    p = Path(_norm(main))
    if not p.name or p.parent == p:
        return ""
    return str(p.parent / f"{p.name}{WORKTREES_SUFFIX}")


def inspect_folder(folder: str) -> dict[str, Any]:
    """What New session needs to know about ``folder`` (``GET
    /build/git/folder``): ``git`` (installed), ``repo`` (inside a
    repository), its top folder (``root``), the branch checked out there
    (``branch``, "" when detached), whether it has a commit, where a session
    worktree would go, and — when a branch cannot be made — ``note`` (why, in
    words) with ``can_branch`` False. Never raises."""
    out: dict[str, Any] = {
        "git": False, "repo": False, "root": "", "branch": "", "has_commits": False,
        "worktrees_dir": "", "can_branch": False, "note": "",
    }
    try:
        if not git_path():
            out["note"] = NO_GIT
            return out
        out["git"] = True
        if not folder or not Path(folder).is_dir():
            out["note"] = NOT_A_REPO
            return out
        top = _g(["rev-parse", "--show-toplevel"], folder, reads=True)
        if not top.ok or not top.out.strip():
            out["note"] = NOT_A_REPO
            return out
        root = _norm(top.out.strip())
        out.update(repo=True, root=root)
        head = _g(["symbolic-ref", "--short", "-q", "HEAD"], root, reads=True)
        out["branch"] = head.out.strip() if head.ok else ""
        out["has_commits"] = _g(["rev-parse", "--verify", "-q", "HEAD"], root, reads=True).ok
        main, bare = _main_checkout(root)
        out["worktrees_dir"] = worktrees_dir_for(main)
        # Review (v1.375.0): a SUBMODULE's ``git worktree list`` names its git
        # folder (``<super>/.git/modules/<name>``) as the main checkout, so
        # its session worktrees landed INSIDE the superproject's .git, and a
        # merge then ran in a git folder that has no files.
        sup = _g(["rev-parse", "--show-superproject-working-tree"], root, reads=True)
        if sup.ok and sup.out.strip():
            out["note"] = SUBMODULE
        elif bare and _norm(main) == root:
            out["note"] = BARE_REPO
        elif not out["has_commits"]:
            out["note"] = NO_COMMITS
        elif not out["worktrees_dir"]:
            out["note"] = DRIVE_ROOT
        else:
            out["can_branch"] = True
    except Exception:  # noqa: BLE001 — a folder that cannot be read is "not in a repository"
        log.debug("inspecting %s for git failed", folder, exc_info=True)
        out["can_branch"] = False
        out["note"] = out["note"] or NOT_A_REPO
    return out


# --------------------------------------------------------------------------- #
# starting a session's branch
# --------------------------------------------------------------------------- #

_SLUG_RX = re.compile(r"[^a-z0-9]+")


def short_name(title: str = "") -> str:
    """The session's short name: a slug of its title (``session`` when it has
    none yet) and four hex digits, so two sessions never share one —
    ``session-3f2a``. The branch is ``ij/<it>`` and the worktree folder
    ``<it>``."""
    slug = _SLUG_RX.sub("-", str(title or "").lower()).strip("-")[:30].strip("-") or "session"
    return f"{slug}-{secrets.token_hex(2)}"


def start(folder: str, *, title: str = "", start_point: str = "", base: "str | None" = None) -> dict[str, str]:
    """Make the session's branch and worktree for ``folder`` (the folder the
    user picked; already checked by ``fs_policy.root_problem``). Returns the
    facts to store; raises :class:`GitRefused` in words.

    ``start_point`` / ``base`` (v1.377.0, a session BRANCHED from another's
    message): the new branch starts from that commit and names that base,
    instead of the main checkout's HEAD and current branch.

    ``git worktree add -b ij/<name> <worktree> HEAD`` through
    ``GitSession.start`` (the review system's own start, with this branch
    name): the main checkout's files, index and current branch are not
    touched — git writes only its own records under ``.git`` and the new
    branch ref. The session's working folder is the worktree's copy of the
    folder the user picked (``facts["folder"]`` is returned beside them)."""
    from ..core.fs_policy import root_problem

    info = inspect_folder(folder)
    if not info["git"] or not info["repo"]:
        raise GitRefused(info["note"] or NOT_A_REPO, 400)
    if not info["can_branch"]:
        raise GitRefused(info["note"], 400)
    root = info["root"]
    wt_dir = Path(info["worktrees_dir"])
    made_dir = not wt_dir.exists()
    try:
        wt_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise GitRefused(
            f"The folder for the session's branch could not be made beside the repository ({wt_dir}).",
            400, _masked(str(exc)),
        ) from None
    problem = root_problem(str(wt_dir))
    if problem:
        if made_dir:
            with contextlib.suppress(OSError):
                wt_dir.rmdir()
        raise GitRefused(f"The folder for the session's branch cannot be used: {problem}", 400)
    name, branch = "", ""
    for _ in range(12):
        candidate = short_name(title)
        exists = _g(["show-ref", "--verify", "--quiet", f"refs/heads/{BRANCH_PREFIX}{candidate}"],
                    root, reads=True).ok
        if not exists and not (wt_dir / candidate).exists():
            name, branch = candidate, BRANCH_PREFIX + candidate
            break
    if not name:
        raise GitRefused("No free name for the session's branch was found. Try again.", 409)
    worktree = wt_dir / name
    start_sha = _ref_sha(Path(root), "HEAD")
    try:
        gs = GitSession.start(Path(root), worktree, name, branch=branch, env=git_env(),
                              **({"start_point": start_point} if start_point else {}))
    except GitError as exc:
        left = _undo_start(root, worktree, branch, start_sha)
        if made_dir:
            with contextlib.suppress(OSError):
                wt_dir.rmdir()  # only when it is still empty
        raise GitRefused(
            "Git could not make the session's branch and worktree, so nothing was made."
            if not left else
            f"Git could not make the session's branch and worktree; {left} is left behind.",
            409, _masked(str(exc)),
        ) from None
    base_commit = _g(["rev-parse", "HEAD"], worktree, reads=True).out.strip()
    base_name = info["branch"] or ("" if gs.base == "HEAD" else gs.base)
    if base is not None:
        base_name = base  # v1.377.0: a branched session names its origin's base
    # The folder the user picked, inside the worktree (a pick may be a folder
    # INSIDE the repository; the session then works in the worktree's copy).
    try:
        rel = os.path.relpath(os.path.realpath(folder), os.path.realpath(root))
    except ValueError:  # another drive
        rel = "."
    work = worktree if rel in (".", "") or rel.startswith("..") else worktree / rel
    if not work.is_dir():
        # A folder the repository does not hold (untracked): the worktree
        # gets an empty one in its place, so the session still works there.
        work.mkdir(parents=True, exist_ok=True)
    main, _bare = _main_checkout(root)
    facts = {
        "repo": root,
        "checkout": _norm(folder),
        "main": main,
        "worktree": str(worktree),
        "branch": branch,
        "base": base_name,
        "base_commit": base_commit,
        "name": name,
        "created_at": utcnow().isoformat(),
    }
    return {**facts, "folder": str(work)}


def _undo_start(root: str, worktree: Path, branch: str, start_sha: str) -> str:
    """Review (v1.375.0): ``git worktree add -b`` that FAILS can still leave
    its branch (made first) and even a whole worktree (a ``post-checkout``
    hook that exits non-zero — Git LFS's own, when git-lfs is missing, is
    one — fails the command AFTER the checkout). Each retry left another
    stray ``ij/`` branch and worktree that no session knows. Undone here:
    the just-made worktree (a fresh checkout, nothing of the user's in it)
    and the branch, only while it still points at the commit it was made
    from and is checked out nowhere. Returns what could not be undone, in
    words ("" = nothing left)."""
    left: list[str] = []
    if (worktree / ".git").is_file():
        _g(["worktree", "remove", "--force", str(worktree)], root)
        if worktree.exists():
            left.append(f"the folder {worktree}")
    if branch and start_sha and _ref_sha(Path(root), f"refs/heads/{branch}") == start_sha:
        if not _worktree_of_branch(Path(root), branch):
            _g(["update-ref", "-d", f"refs/heads/{branch}", start_sha], root)
        if _ref_sha(Path(root), f"refs/heads/{branch}"):
            left.append(f"the branch {branch}")
    return " and ".join(left)


# --------------------------------------------------------------------------- #
# this turn's branch (the harness reads it)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TurnBranch:
    """The branch a build turn works on (set by ``start_build_turn`` while the
    turn's driver task is created — the task and its helpers copy it)."""

    checkout: str  # the folder the user picked (in the main checkout)
    folder: str  # the session's working folder (in the worktree)
    repo: str  # the pick's repository top folder (main checkout side)
    worktree: str  # the worktree's top folder
    branch: str
    base: str


TURN_BRANCH: "ContextVar[TurnBranch | None]" = ContextVar("ij_build_branch", default=None)


def set_turn_branch(facts: "dict[str, str] | None", folder: str) -> "Token[TurnBranch | None]":
    """Arm this turn's branch (None for a session that works in its folder)."""
    if not facts or facts.get("removed_at"):
        return TURN_BRANCH.set(None)
    return TURN_BRANCH.set(TurnBranch(
        checkout=facts.get("checkout", ""), folder=str(folder or facts.get("worktree", "")),
        repo=facts.get("repo", ""), worktree=facts.get("worktree", ""),
        branch=facts.get("branch", ""), base=facts.get("base", ""),
    ))


def reset_turn_branch(token: "Token[TurnBranch | None]") -> None:
    TURN_BRANCH.reset(token)


def current() -> "TurnBranch | None":
    return TURN_BRANCH.get()


def _within(child: str, parent: str) -> bool:
    c = os.path.normcase(_norm(child))
    p = os.path.normcase(_norm(parent))
    return c == p or c.startswith(p.rstrip("\\/") + os.sep)


def in_worktree(path: str) -> str:
    """``path`` as this turn's worktree holds it: a folder of the user's
    repository maps to its copy in the worktree; anything else (no branch
    this turn, a folder outside the repository) is returned as it is."""
    tb = current()
    if tb is None or not path or not tb.repo or not tb.worktree:
        return path
    if not _within(path, tb.repo):
        return path
    rel = os.path.relpath(_norm(path), _norm(tb.repo))
    return tb.worktree if rel in (".", "") else str(Path(tb.worktree) / rel)


def prompt_line() -> str:
    """One paragraph for the harness prompt while the turn works on a branch:
    where, from what, and that the main checkout is the user's. "" without a
    branch (the prompt is then exactly as before)."""
    tb = current()
    if tb is None:
        return ""
    base = f" (started from {tb.base})" if tb.base else ""
    return (
        f"\n\nThis session works on its own git branch, {tb.branch}{base}, in a worktree at "
        f"{tb.worktree}. The user's main checkout at {tb.repo} is theirs: make every change "
        "here, never there. Commit or push only when the user asks; every commit, push and pull "
        "request asks the user first, and a force push, deleting a branch or rewriting history is "
        "refused."
    )


# --------------------------------------------------------------------------- #
# reading the branch
# --------------------------------------------------------------------------- #


def _live(facts: dict[str, str]) -> Path:
    """The worktree, or :class:`GitRefused` when it is gone."""
    wt = Path(facts.get("worktree", ""))
    if facts.get("removed_at") or not wt.is_dir():
        raise GitRefused(GONE, 409)
    return wt


def _ref_sha(cwd: Path, ref: str) -> str:
    got = _g(["rev-parse", "--verify", "-q", ref], cwd, reads=True)
    return got.out.strip() if got.ok else ""


def _require_on_branch(facts: dict[str, str], wt: Path) -> None:
    """Review (v1.375.0): every action reads the worktree's HEAD as the
    session's branch. A ``git switch main`` (or a detached checkout) run in
    the worktree moved it — and Commit then committed onto the USER'S
    ``main``. Refused in words, nothing changed, until it is switched back."""
    branch = facts.get("branch", "")
    got = _g(["symbolic-ref", "-q", "HEAD"], wt, reads=True)
    now = got.out.strip() if got.ok else ""
    if now == f"refs/heads/{branch}":
        return
    where = (f"the branch {now[len('refs/heads/'):]}" if now.startswith("refs/heads/")
             else "a single commit (detached)")
    raise GitRefused(
        f"This session's worktree is on {where} instead of its branch {branch}, so nothing was "
        f"changed. Switch it back (git switch {branch}) and try again.", 409,
    )


def merge_base(facts: dict[str, str], wt: Path) -> str:
    """The commit the branch's diff is measured from: where it meets its base
    branch now, else the commit it started from."""
    base = facts.get("base", "")
    if base:
        # One call: a base branch that is gone fails here, and the commit the
        # branch started from stands in.
        got = _g(["merge-base", f"refs/heads/{base}", "HEAD"], wt, reads=True)
        if got.ok and got.out.strip():
            return got.out.strip()
    return facts.get("base_commit", "") or "HEAD"


def branch_start_point(facts: "dict[str, str] | None") -> str:
    """Where a session BRANCHED from another session's message starts its OWN
    new branch (v1.377.0, the decision: from the same base, never the same
    worktree): the base branch as it is now when the repository still has
    it, else the commit the original branch started from. "" when neither
    can be named (the caller then starts from the checkout's HEAD). BLOCKING."""
    if not facts:
        return ""
    base = facts.get("base", "")
    repo = facts.get("repo", "")
    if base and repo and Path(repo).is_dir():
        sha = _ref_sha(Path(repo), f"refs/heads/{base}")
        if sha:
            return sha
    return facts.get("base_commit", "")


#: At most this many commits are named by :func:`commits_since`.
COMMITS_SINCE_MAX = 50


def commits_since(facts: "dict[str, str] | None", since: Any) -> list[dict[str, str]]:
    """The commits ON THE SESSION'S BRANCH made at or after ``since`` (a
    datetime, naive = UTC) — newest first, ``{sha, subject}``, at most
    :data:`COMMITS_SINCE_MAX` (v1.377.0: a REWIND to before a message whose
    work was committed says so — the files go back, the commits stay). Only
    the branch's own commits (from where it meets its base). [] for a session
    with no branch, a gone worktree or any git failure. BLOCKING; never raises."""
    from datetime import datetime, timezone

    try:
        if not facts or facts.get("removed_at") or since is None:
            return []
        wt = Path(facts.get("worktree", ""))
        branch = facts.get("branch", "")
        if not wt.is_dir() or not branch:
            return []
        when = since if isinstance(since, datetime) else datetime.fromisoformat(str(since))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        floor = int(when.timestamp())
        mb = merge_base(facts, wt)
        got = _g(["log", "--format=%h%x1f%ct%x1f%s%x1e", "-n", str(COMMITS_SINCE_MAX),
                  f"{mb}..refs/heads/{branch}"], wt, reads=True)
        if not got.ok:
            return []
        out: list[dict[str, str]] = []
        for rec in got.out.split("\x1e"):
            bits = rec.strip("\n").split("\x1f")
            if len(bits) < 3 or not bits[0].strip():
                continue
            try:
                at = int(bits[1].strip())
            except ValueError:
                continue
            if at >= floor:
                out.append({"sha": bits[0].strip(), "subject": bits[2].strip()[:200]})
        return out
    except Exception:  # noqa: BLE001 — not knowing is no commits named, never a refusal
        return []


def commits_note(commits: list[dict[str, str]], branch: str) -> str:
    """What a rewind says about COMMITTED work (v1.377.0): "" when none."""
    if not commits:
        return ""
    n = len(commits)
    shown = ", ".join(f"“{c['subject']}”" for c in commits[:3] if c.get("subject"))
    more = f" and {n - 3} more" if n > 3 else ""
    what = f"{n} commit{'s' if n != 1 else ''} on {branch}" + (f" ({shown}{more})" if shown else "")
    return (
        f"Some of this work was committed: {what} came after this message. Rewinding puts "
        "the files back, but the commits stay on the branch, so the files show as changes "
        "that are not committed yet."
    )


def pick_remote(wt: Path, branch: str) -> str:
    """The remote a push goes to: the branch's own, else ``origin``, else the
    only one there is; "" for none."""
    own = _g(["config", "--get", f"branch.{branch}.remote"], wt, reads=True)
    if own.ok and own.out.strip():
        return own.out.strip()
    got = _g(["remote"], wt, reads=True)
    remotes = [r.strip() for r in got.out.splitlines() if r.strip()] if got.ok else []
    if "origin" in remotes:
        return "origin"
    return remotes[0] if len(remotes) == 1 else ""


def _remote_url(wt: Path, remote: str) -> str:
    if not remote:
        return ""
    got = _g(["remote", "get-url", remote], wt, reads=True)
    return got.out.strip() if got.ok else ""


_URL_RX = re.compile(
    r"^(?:(?:https?|ssh|git)://(?:[^@/]+@)?(?P<h1>[^/:]+)(?::\d+)?/|(?:[^@]+@)?(?P<h2>[^/:]+):)"
    r"(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"
)


def github_repo(url: str) -> "tuple[str, str, str] | None":
    """``(host, owner, repo)`` for a GitHub remote address (github.com or a
    GitHub Enterprise host whose name says github), else None."""
    m = _URL_RX.match(str(url or "").strip())
    if not m:
        return None
    host = (m.group("h1") or m.group("h2") or "").lower()
    if "github" not in host:
        return None
    return host, m.group("owner"), m.group("repo")


def compare_url(facts: dict[str, str], url: str) -> str:
    """The web page that opens a pull request of the branch against its base
    (``…/compare/<base>...<branch>?expand=1``), or "" when the remote is not
    on GitHub or there is no base branch."""
    gh = github_repo(url)
    base = facts.get("base", "")
    if gh is None or not base:
        return ""
    host, owner, repo = gh
    return (
        f"https://{host}/{owner}/{repo}/compare/{quote(base, safe='/')}..."
        f"{quote(facts.get('branch', ''), safe='/')}?expand=1"
    )


def _worktree_of_branch(cwd: Path, branch: str) -> str:
    """The working tree that has ``branch`` checked out, or ""."""
    got = _g(["worktree", "list", "--porcelain"], cwd, reads=True)
    if not got.ok:
        return ""
    cur = ""
    for line in got.out.splitlines():
        if line.startswith("worktree "):
            cur = _norm(line[len("worktree "):])
        elif line.startswith("branch ") and line[len("branch "):].strip() == f"refs/heads/{branch}":
            return cur
    return ""


def _porcelain(cwd: Path, *, untracked: bool = True) -> list[str]:
    args = ["status", "--porcelain=v1", "-z", "--untracked-files=" + ("all" if untracked else "no")]
    got = _g(args, cwd, reads=True)
    if not got.ok:
        return []
    out: list[str] = []
    parts = got.out.split("\0")
    i = 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if not entry:
            continue
        out.append(entry[3:])
        if entry[:1] in ("R", "C"):
            i += 1  # the rename's old name
    return out


def status(facts: dict[str, str]) -> dict[str, Any]:
    """The branch as the screen shows it: commits ahead of the base, files not
    committed yet, the remote and whether it has the branch, where the base
    is checked out, the commits (newest first, at most 20), a pull request's
    prefill, the compare link and the GitHub CLI's state. BLOCKING; raises
    :class:`GitRefused` only for a gone worktree."""
    out: dict[str, Any] = {**(view(facts) or {})}
    wt = Path(facts.get("worktree", ""))
    out["exists"] = wt.is_dir() and not facts.get("removed_at")
    if not out["exists"]:
        return out
    base = facts.get("base", "")
    mb = merge_base(facts, wt)
    head = _ref_sha(wt, "HEAD")
    count = _g(["rev-list", "--count", f"{mb}..HEAD"], wt, reads=True)
    out["ahead"] = int(count.out.strip() or 0) if count.ok else 0
    base_sha = _ref_sha(wt, f"refs/heads/{base}") if base else ""
    out["base_exists"] = bool(base_sha)
    if base_sha:
        behind = _g(["rev-list", "--count", f"HEAD..refs/heads/{base}"], wt, reads=True)
        out["behind"] = int(behind.out.strip() or 0) if behind.ok else 0
        out["in_base"] = bool(head) and _g(
            ["merge-base", "--is-ancestor", "HEAD", f"refs/heads/{base}"], wt, reads=True,
        ).ok
        out["base_checked_out_at"] = _worktree_of_branch(wt, base)
    else:
        out["behind"] = 0
        out["in_base"] = False
        out["base_checked_out_at"] = ""
    out["uncommitted"] = len(_porcelain(wt))
    branch = facts.get("branch", "")
    remote = pick_remote(wt, branch)
    out["remote"] = remote
    url = _remote_url(wt, remote)
    out["remote_host"] = (github_repo(url) or ("", "", ""))[0] or _host_of(url)
    tracking = _ref_sha(wt, f"refs/remotes/{remote}/{branch}") if remote else ""
    out["pushed"] = bool(tracking) and tracking == head
    if tracking:
        unp = _g(["rev-list", "--count", f"refs/remotes/{remote}/{branch}..HEAD"], wt, reads=True)
        out["unpushed"] = int(unp.out.strip() or 0) if unp.ok else 0
    else:
        out["unpushed"] = out["ahead"]
    log_ = _g(["log", "--format=%h%x1f%s%x1f%b%x1e", "-n", "20", f"{mb}..HEAD"], wt, reads=True)
    commits: list[dict[str, str]] = []
    if log_.ok:
        for rec in log_.out.split("\x1e"):
            bits = rec.strip("\n").split("\x1f")
            if len(bits) >= 2 and bits[0].strip():
                commits.append({"sha": bits[0].strip(), "subject": bits[1].strip()[:300],
                                "body": (bits[2] if len(bits) > 2 else "").strip()[:2000]})
    out["commits"] = [{"sha": c["sha"], "subject": c["subject"]} for c in commits]
    out["pr_draft"] = pr_draft(commits, facts)
    out["compare_url"] = compare_url(facts, url)
    host = (github_repo(url) or ("github.com", "", ""))[0]
    out["gh"] = gh_state(host) if github_repo(url) else {"installed": bool(gh_path()), "signed_in": None,
                                                         "note": "This remote is not on GitHub."}
    return out


def _host_of(url: str) -> str:
    m = re.match(r"^(?:[a-z+]+://)?(?:[^@/]+@)?([^/:]+)", str(url or "").strip(), re.I)
    return m.group(1).lower() if m else ""


def pr_draft(commits: list[dict[str, str]], facts: dict[str, str]) -> dict[str, str]:
    """A pull request's title and body, prefilled from the branch's commits
    (oldest first): one commit = its subject and body; several = the first
    subject and a list of every subject. The user edits both."""
    if not commits:
        return {"title": facts.get("branch", ""), "body": ""}
    ordered = list(reversed(commits))
    if len(ordered) == 1:
        return {"title": ordered[0]["subject"][:MAX_TITLE_CHARS], "body": ordered[0].get("body", "")}
    return {
        "title": ordered[0]["subject"][:MAX_TITLE_CHARS],
        "body": "\n".join(f"- {c['subject']}" for c in ordered),
    }


# --------------------------------------------------------------------------- #
# the Branch view: the whole diff against the base, committed and not
# --------------------------------------------------------------------------- #


def _split_z(text: str) -> list[str]:
    return [p for p in text.split("\0")]


def _name_status(wt: Path, mb: str) -> list[tuple[str, str, str]]:
    """``[(code, old, new)]`` of the working tree against ``mb`` (tracked
    files, committed and not)."""
    got = _g(["diff", "--no-color", "--no-ext-diff", "-M", "--name-status", "-z", mb], wt, reads=True)
    if not got.ok:
        raise GitRefused("The branch's changes could not be read.", 409, _masked(got.err))
    parts = _split_z(got.out)
    out: list[tuple[str, str, str]] = []
    i = 0
    while i < len(parts):
        code = parts[i]
        i += 1
        if not code:
            continue
        if code[:1] in ("R", "C"):
            old, new = parts[i], parts[i + 1] if i + 1 < len(parts) else ""
            i += 2
            out.append((code[:1], old, new))
        else:
            path = parts[i] if i < len(parts) else ""
            i += 1
            out.append((code[:1], path, path))
    return out


def _numstat(wt: Path, mb: str) -> dict[str, tuple[int | None, int | None]]:
    got = _g(["diff", "--no-color", "--no-ext-diff", "-M", "--numstat", "-z", mb], wt, reads=True)
    out: dict[str, tuple[int | None, int | None]] = {}
    if not got.ok:
        return out
    parts = _split_z(got.out)
    i = 0
    while i < len(parts):
        head = parts[i]
        i += 1
        if not head:
            continue
        bits = head.split("\t")
        if len(bits) < 3:
            continue
        added = None if bits[0] == "-" else int(bits[0])
        removed = None if bits[1] == "-" else int(bits[1])
        if bits[2] == "":  # a rename: old and new follow
            new = parts[i + 1] if i + 1 < len(parts) else ""
            i += 2
            out[new] = (added, removed)
        else:
            out[bits[2]] = (added, removed)
    return out


def _file_patch(wt: Path, mb: str, old: str, new: str) -> str:
    paths = [old, new] if old != new else [new]
    got = _g(["diff", "--no-color", "--no-ext-diff", "-M", mb, "--", *paths], wt, reads=True)
    return got.out if got.ok else ""


def _untracked(wt: Path) -> list[str]:
    got = _g(["ls-files", "--others", "--exclude-standard", "-z"], wt, reads=True)
    return [p for p in _split_z(got.out) if p] if got.ok else []


def _new_file_change(wt: Path, rel: str) -> dict[str, Any]:
    path = wt / rel
    row: dict[str, Any] = {
        "path": str(path), "rel": rel, "name": rel.rsplit("/", 1)[-1], "status": "created",
        "added": None, "removed": 0, "diff": "",
    }
    try:
        size = path.stat().st_size
        if size > READ_CAP:
            row.update(note="too large to compare")
            return row
        data = path.read_bytes()
    except OSError:
        row.update(note="could not be read")
        return row
    if b"\0" in data[:8192]:
        row.update(binary=True, diff="binary file", removed=None)
        return row
    text = data.decode("utf-8", errors="replace").replace("\r\n", "\n")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    body = "".join(f"+{ln}\n" for ln in lines)
    diff = f"--- /dev/null\n+++ b/{rel}\n@@ -0,0 +1,{len(lines)} @@\n{body}"
    row["added"] = len(lines)
    if len(diff) > DIFF_CAP:
        diff, row["truncated"] = diff[:DIFF_CAP], True
    row["diff"] = diff
    return row


def branch_changes(facts: dict[str, str]) -> dict[str, Any]:
    """The Branch view (``GET /build/sessions/{id}/git/diff``): every file the
    branch changed against its base — measured from where it meets the base
    (:func:`merge_base`), committed AND not, new files included — in the
    Changes pane's own shape (``routes/chat_changes``: path, rel, name,
    status created/modified/deleted, added/removed, unified diff text,
    binary, truncated). Read-only: no index, ref or file is written (new
    files are read straight off the disk). At most :data:`MAX_FILES` files."""
    wt = _live(facts)
    mb = merge_base(facts, wt)
    entries = _name_status(wt, mb)
    counts = _numstat(wt, mb)
    changes: list[dict[str, Any]] = []
    more = False
    for code, old, new in entries:
        if len(changes) >= MAX_FILES:
            more = True
            break
        status = {"A": "created", "D": "deleted"}.get(code, "modified")
        rel = new or old
        added, removed = counts.get(rel, (None, None))
        diff = _file_patch(wt, mb, old, new)
        row: dict[str, Any] = {
            "path": str(wt / rel), "rel": rel, "name": rel.rsplit("/", 1)[-1],
            "status": status, "added": added, "removed": removed, "diff": diff,
        }
        if added is None and removed is None:
            row.update(binary=True, diff="binary file")
        if code == "R" and old != new:
            row["note"] = f"renamed from {old}"
        if len(row["diff"]) > DIFF_CAP:
            row["diff"], row["truncated"] = row["diff"][:DIFF_CAP], True
        changes.append(row)
    for rel in _untracked(wt):
        if len(changes) >= MAX_FILES:
            more = True
            break
        changes.append(_new_file_change(wt, rel))
    return {
        "changes": changes,
        "files": len(changes),
        "added": sum(int(c.get("added") or 0) for c in changes),
        "removed": sum(int(c.get("removed") or 0) for c in changes),
        "truncated_files": more,
        "base": facts.get("base", ""),
        "merge_base": mb[:12],
    }


# --------------------------------------------------------------------------- #
# commit
# --------------------------------------------------------------------------- #


def draft_material(facts: dict[str, str]) -> str:
    """What the commit-message draft is shown: the uncommitted changes
    (``git status --short`` and the diff against the last commit, new files
    by name), cut to :data:`DRAFT_DIFF_CHARS`. "" when nothing is waiting."""
    wt = _live(facts)
    short = _g(["status", "--short", "--untracked-files=all"], wt, reads=True)
    if not short.ok or not short.out.strip():
        return ""
    diff = _g(["diff", "--no-color", "--no-ext-diff", "HEAD"], wt, reads=True)
    text = "Files:\n" + short.out.strip() + "\n\nChanges:\n" + (diff.out if diff.ok else "")
    if len(text) > DRAFT_DIFF_CHARS:
        text = text[:DRAFT_DIFF_CHARS] + "\n[… the rest of the change is not shown]"
    return text


def clean_message(text: str) -> str:
    """A model's draft as a commit message: no code fence, no quotes around
    it, no trailing spaces, bounded."""
    t = str(text or "").strip()
    fence = re.match(r"^```[a-zA-Z]*\n(.*?)\n```$", t, re.S)
    if fence:
        t = fence.group(1).strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
        t = t[1:-1].strip()
    lines = [ln.rstrip() for ln in t.replace("\r\n", "\n").split("\n")]
    return "\n".join(lines).strip()[:MAX_MESSAGE_CHARS]


def commit(facts: dict[str, str], message: str) -> dict[str, Any]:
    """Commit everything in the worktree on the session's branch (``git add
    -A`` then ``git commit -F -``) as the user (their own git identity; Iron
    Jarvis's default name only when git names no one — said in ``note``).
    Never amends. :class:`GitRefused` for no message, nothing to commit, or a
    commit git refused (a hook, …)."""
    wt = _live(facts)
    msg = str(message or "").replace("\r\n", "\n").strip()
    if not msg:
        raise GitRefused(EMPTY_MESSAGE, 400)
    if len(msg) > MAX_MESSAGE_CHARS:
        raise GitRefused(f"A commit message holds at most {MAX_MESSAGE_CHARS:,} characters.", 400)
    _require_on_branch(facts, wt)
    added = _g(["add", "-A"], wt)
    if not added.ok:
        raise GitRefused("Git could not stage the changes.", 409, _masked(added.err or added.out))
    if _g(["diff", "--cached", "--quiet"], wt).code == 0:
        raise GitRefused(NOTHING_TO_COMMIT, 409)
    own = has_identity(wt, git_env())
    args = ([] if own else default_identity()) + ["commit", "-F", "-"]
    got = _g(args, wt, timeout=START_S, input=(msg + "\n").encode("utf-8"))
    if got.timed_out:
        raise GitRefused(_timed_out_words("The commit", START_S), 504)
    if not got.ok:
        raise GitRefused("Git did not make the commit.", 409, _masked(got.err or got.out))
    sha = _ref_sha(wt, "HEAD")
    files = _g(["diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"], wt, reads=True)
    out: dict[str, Any] = {
        "committed": True, "sha": sha[:12], "subject": msg.split("\n", 1)[0][:300],
        "files": len([f for f in files.out.splitlines() if f.strip()]) if files.ok else 0,
    }
    if not own:
        out["note"] = (
            "Git has no name and e-mail set on this PC, so the commit is signed \"Iron Jarvis\". "
            "Set yours with git config to commit as you."
        )
    return out


# --------------------------------------------------------------------------- #
# push
# --------------------------------------------------------------------------- #


def push(facts: dict[str, str]) -> dict[str, Any]:
    """``git push -u <remote> ij/<name>:ij/<name>`` — NEVER ``--force`` in any
    form (the arguments are fixed here; nothing the user or a model typed is
    passed). A credential window the user's git opens is allowed; the call is
    bounded by :data:`PUSH_S` and says so when it runs out."""
    wt = _live(facts)
    branch = facts.get("branch", "")
    if not branch.startswith(BRANCH_PREFIX):
        raise GitRefused(NO_BRANCH, 409)
    remote = pick_remote(wt, branch)
    if not remote:
        raise GitRefused(NO_REMOTE, 409)
    got = _g(["push", "-u", remote, f"{branch}:{branch}"], wt, timeout=PUSH_S)
    details = _masked((got.err or "") + ("\n" + got.out if got.out.strip() else ""))
    if got.timed_out:
        raise GitRefused(
            _timed_out_words("The push", PUSH_S)
            + " If a sign-in window opened, finish it and push again.", 504, details,
        )
    if not got.ok:
        text = (got.err or "") + (got.out or "")
        if _REJECTED_RX.search(text):
            raise GitRefused(
                f"The remote has commits on {branch} that this branch does not, so nothing was "
                "pushed (a push here would overwrite them).", 409, details,
            )
        if _AUTH_RX.search(text):
            raise GitRefused(
                f"Git could not sign in to the remote \"{remote}\", so nothing was pushed. Check "
                "your git sign-in for it, then push again.", 409, details,
            )
        raise GitRefused("The push did not go through.", 409, details)
    return {"pushed": True, "remote": remote, "branch": branch, "details": details}


# --------------------------------------------------------------------------- #
# the GitHub CLI: pull request and checks
# --------------------------------------------------------------------------- #

_GH_AUTH: dict[tuple[str, str], tuple[float, bool | None]] = {}

#: What cmd.exe reads as syntax in an argument handed to a batch file.
_BATCH_UNSAFE_RX = re.compile(r'["%!^&|<>\r\n]')
GH_BATCH_UNSAFE = (
    "The GitHub CLI on this PC runs through a batch file, which cannot be handed quotes or any "
    "of % ! ^ & | < > safely. Take them out of the title, or install GitHub CLI's own gh.exe."
)


def _gh_batch_unsafe(exe: "str | None", args: list[str]) -> bool:
    """True when ``exe`` is a batch file (Windows) and an argument holds
    something cmd.exe would read as syntax."""
    return bool(
        exe and os.name == "nt" and exe.lower().endswith((".cmd", ".bat"))
        and any(_BATCH_UNSAFE_RX.search(str(a)) for a in args)
    )


def gh_env() -> dict[str, str]:
    return git_env({
        "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1", "NO_COLOR": "1",
        "GH_SPINNER_DISABLED": "1", "CLICOLOR": "0",
    })


def _gh(args: list[str], cwd: Path, *, timeout: float = GH_S) -> Any:
    from ..core import procs
    from ..git.integration import GitRun

    exe = gh_path()
    if not exe:
        raise GitRefused("The GitHub CLI is not installed on this PC.", 409)
    if _gh_batch_unsafe(exe, args):
        # Review (v1.375.0): a gh that is a BATCH FILE runs through cmd.exe,
        # which reads quotes, %, & and | in its arguments as commands — a
        # pull request title `x" & del … & "` RAN them. A title that cannot
        # be handed over safely is refused; nothing runs.
        raise GitRefused(GH_BATCH_UNSAFE, 400)
    res = procs.run([exe, *args], cwd=cwd, timeout=timeout, env=gh_env())
    return GitRun(
        code=-1 if res.timed_out else res.returncode,
        out=procs.text(res.stdout, "utf-8"), err=procs.text(res.stderr, "utf-8"),
        timed_out=bool(res.timed_out),
    )


def forget_gh_state() -> None:
    """Forget the remembered sign-in (tests; a sign-in that just changed)."""
    _GH_AUTH.clear()


def gh_state(host: str = "github.com", *, cwd: "Path | None" = None) -> dict[str, Any]:
    """Is the GitHub CLI installed, and signed in for ``host``? ``signed_in``
    None = it could not tell (it took too long). Remembered
    :data:`GH_AUTH_TTL_S` (``gh auth status`` makes a network call). Never
    raises. ``note`` says, in words, what Create PR does instead."""
    exe = gh_path()
    if not exe:
        return {"installed": False, "signed_in": False,
                "note": "The GitHub CLI (gh) is not installed, so Create PR opens GitHub's own page instead."}
    key = (exe, host)
    now = time.monotonic()
    hit = _GH_AUTH.get(key)
    if hit is not None and now - hit[0] < GH_AUTH_TTL_S:
        signed = hit[1]
    else:
        try:
            got = _gh(["auth", "status", "--hostname", host], cwd or Path.home(), timeout=GH_AUTH_S)
            signed = None if got.timed_out else got.ok
        except Exception:  # noqa: BLE001 — unknown, never a crash
            signed = None
        _GH_AUTH[key] = (now, signed)
    note = "" if signed else (
        "The GitHub CLI is not signed in, so Create PR opens GitHub's own page instead. Sign in "
        "with gh auth login to make pull requests here."
        if signed is False else
        "The GitHub CLI did not answer in time, so Create PR opens GitHub's own page instead."
    )
    return {"installed": True, "signed_in": signed, "note": note}


_PR_URL_RX = re.compile(r"https?://\S+/pull/(\d+)")


def create_pr(facts: dict[str, str], title: str, body: str) -> dict[str, Any]:
    """Open a pull request of the branch against its base with ``gh pr
    create`` (pushing the branch first when the remote does not have its
    newest commit — :func:`push`, never a force). Answers ``{url, number}``;
    a pull request that already exists is answered with its own link."""
    wt = _live(facts)
    base = facts.get("base", "")
    if not base:
        raise GitRefused(DETACHED_BASE, 409)
    title = " ".join(str(title or "").split())[:MAX_TITLE_CHARS]
    if not title:
        raise GitRefused("Give the pull request a title first.", 400)
    body = str(body or "")[:MAX_BODY_CHARS]
    _require_on_branch(facts, wt)
    st = status(facts)
    if st.get("ahead", 0) == 0:
        raise GitRefused(f"This branch has no commits that {base} does not have, so there is nothing "
                         "for a pull request. Commit first.", 409)
    gh = gh_state(st.get("remote_host") or "github.com")
    if not gh["installed"] or not gh["signed_in"]:
        raise GitRefused(gh["note"], 409)
    if _gh_batch_unsafe(gh_path(), [title, base, facts["branch"]]):
        raise GitRefused(GH_BATCH_UNSAFE, 400)  # before the push, so nothing happens
    pushed: dict[str, Any] = {}
    if not st.get("pushed"):
        pushed = push(facts)
    fd, tmp = tempfile.mkstemp(prefix="ij-pr-", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
        got = _gh(["pr", "create", "--base", base, "--head", facts["branch"], "--title", title,
                   "--body-file", tmp], wt)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
    text = (got.out or "") + "\n" + (got.err or "")
    m = _PR_URL_RX.search(text)
    if got.timed_out:
        raise GitRefused(_timed_out_words("Making the pull request", GH_S), 504, _masked(text))
    if not got.ok and not (m and "already exists" in text):
        raise GitRefused("GitHub did not make the pull request.", 409, _masked(text))
    if not m:
        raise GitRefused("GitHub made no pull request link.", 409, _masked(text))
    return {"url": m.group(0).rstrip(".,"), "number": int(m.group(1)),
            "existed": not got.ok, "pushed": bool(pushed)}


def _check_state(item: dict[str, Any]) -> str:
    """One check of ``statusCheckRollup`` as passed | failed | pending."""
    if "conclusion" in item or item.get("__typename") == "CheckRun":
        if str(item.get("status") or "").upper() != "COMPLETED":
            return "pending"
        c = str(item.get("conclusion") or "").upper()
        return "passed" if c in ("SUCCESS", "NEUTRAL", "SKIPPED") else "failed"
    s = str(item.get("state") or "").upper()
    if s == "SUCCESS":
        return "passed"
    if s in ("PENDING", "EXPECTED", ""):
        return "pending"
    return "failed"


def checks_words(passed: int, failed: int, pending: int) -> str:
    total = passed + failed + pending
    if total == 0:
        return "No checks ran on this pull request."
    if failed:
        return f"{failed} of {total} check{'s' if total != 1 else ''} failed."
    if pending:
        return f"{pending} of {total} check{'s' if total != 1 else ''} still running."
    return f"All {total} check{'s' if total != 1 else ''} passed." if total > 1 else "The check passed."


def pr_status(facts: dict[str, str]) -> dict[str, Any]:
    """The branch's pull request and its checks (``gh pr view --json``):
    ``{url, number, state, draft, checks: {passed, failed, pending}, words}``
    — read when the user asks (or on a slow poll), never in a loop here."""
    wt = _live(facts)
    got = _gh(["pr", "view", facts.get("branch", ""), "--json",
               "url,number,state,isDraft,statusCheckRollup"], wt)
    if got.timed_out:
        raise GitRefused(_timed_out_words("Reading the pull request", GH_S), 504)
    if not got.ok:
        text = (got.err or "") + (got.out or "")
        if "no pull requests found" in text.lower():
            return {"url": "", "state": "", "words": "This branch has no pull request yet."}
        raise GitRefused("The pull request could not be read.", 409, _masked(text))
    try:
        data = json.loads(got.out or "{}")
    except ValueError:
        raise GitRefused("The pull request could not be read.", 409, _masked(got.out)) from None
    counts = {"passed": 0, "failed": 0, "pending": 0}
    for item in data.get("statusCheckRollup") or []:
        if isinstance(item, dict):
            counts[_check_state(item)] += 1
    return {
        "url": str(data.get("url") or ""), "number": data.get("number"),
        "state": str(data.get("state") or "").lower(), "draft": bool(data.get("isDraft")),
        "checks": counts, "words": checks_words(counts["passed"], counts["failed"], counts["pending"]),
    }


# --------------------------------------------------------------------------- #
# merge into the base
# --------------------------------------------------------------------------- #


def merge(facts: dict[str, str]) -> dict[str, Any]:
    """MERGE INTO <base> — a normal merge of ``ij/<name>`` into its base.

    Refused, in words, with NOTHING changed: no base branch (the session
    started from a commit, or the base was deleted); changes in the worktree
    not committed yet; nothing to merge; a merge that would CONFLICT (checked
    first with ``git merge-tree --write-tree`` — no working tree is touched
    by the check); the checkout that has the base checked out has changes of
    its own. Then:

    * the base is CHECKED OUT (usually the user's main checkout): ``git merge
      --no-ff`` there — the user's files change, which is what a merge is.
      Should it still fail (a hook), ``git merge --abort`` puts the checkout
      back and the answer says so; a half-merge is never left behind;
    * the base is checked out NOWHERE: the same merge commit is made without
      touching any working tree (``commit-tree`` of the checked tree, then
      ``update-ref`` with the old value, so a base that moved meanwhile is
      not overwritten).

    Never a force, never a rebase, never a deleted branch."""
    wt = _live(facts)
    base, branch = facts.get("base", ""), facts.get("branch", "")
    if not base:
        raise GitRefused(DETACHED_BASE, 409)
    base_sha = _ref_sha(wt, f"refs/heads/{base}")
    if not base_sha:
        raise GitRefused(f"The branch {base} no longer exists, so there is nothing to merge into.", 409)
    _require_on_branch(facts, wt)
    if _porcelain(wt):
        raise GitRefused(UNCOMMITTED_BEFORE_MERGE, 409)
    head = _ref_sha(wt, "HEAD")
    if _g(["merge-base", "--is-ancestor", head, base_sha], wt, reads=True).ok:
        raise GitRefused(NOTHING_TO_MERGE.format(base=base), 409)
    check = _g(["merge-tree", "--write-tree", "--name-only", "--no-messages", base_sha, head], wt, reads=True)
    lines = [ln for ln in check.out.splitlines() if ln.strip()]
    if check.code == 1:
        files = lines[1:9]
        more = "" if len(lines) <= 9 else f" and {len(lines) - 9} more"
        raise GitRefused(
            f"Merging would conflict in {', '.join(files) or 'some files'}{more}. Nothing was changed: "
            f"bring {base}'s changes into this branch and settle them first, then merge.", 409,
        )
    if not check.ok or not lines:
        raise GitRefused("Git could not check whether the merge would go through.", 409,
                         _masked(check.err or check.out))
    tree = lines[0].strip()
    message = f"Merge branch '{branch}' into {base}"
    where = _worktree_of_branch(wt, base)
    if where and not Path(where).is_dir():
        # Review (v1.375.0): a checkout of the base on a drive that is not
        # plugged in (or a folder that moved) used to crash the merge (a git
        # call whose working folder does not exist).
        raise GitRefused(
            f"{base} is checked out at {where}, a folder that is not there right now (a drive that "
            "is not plugged in, or a folder that moved), so nothing was merged. Bring it back, or "
            "merge there with git.", 409,
        )
    if where:
        place = Path(where)
        if _porcelain(place, untracked=False):
            raise GitRefused(
                f"Your checkout of {base} at {where} has changes that are not committed. Commit or "
                "put them away first; nothing was merged.", 409,
            )
        ident = [] if has_identity(place, git_env()) else default_identity()
        got = _g([*ident, "merge", "--no-ff", "--no-edit", "-m", message, branch], place, timeout=START_S)
        if not got.ok:
            _g(["merge", "--abort"], place)
            back = not _ref_sha(place, "MERGE_HEAD") and _ref_sha(place, "HEAD") == base_sha
            words = (
                "The merge did not go through, and your checkout was put back as it was."
                if back else
                f"The merge did not go through. Check your checkout of {base} at {where}."
            )
            if back and "untracked working tree files would be overwritten" in (got.err or ""):
                # Review (v1.375.0): git's own refusal, said in words.
                words = (
                    f"Your checkout of {base} at {where} has files that are not in git which the merge "
                    "would overwrite, so nothing was merged. Move them away first (Details names them)."
                )
            if got.timed_out:
                words = _timed_out_words("The merge", START_S) + " " + words
            raise GitRefused(words, 409, _masked(got.err or got.out))
        new = _ref_sha(place, "HEAD")
    else:
        ident = [] if has_identity(wt, git_env()) else default_identity()
        made = _g([*ident, "commit-tree", tree, "-p", base_sha, "-p", head, "-F", "-"], wt,
                  input=(message + "\n").encode("utf-8"))
        new = made.out.strip()
        if not made.ok or not new:
            raise GitRefused("Git could not make the merge commit.", 409, _masked(made.err))
        # Review (v1.375.0): "checked out nowhere" is read off ``git worktree
        # list``, which shows a checkout in the middle of a REBASE (or a
        # bisect) of the base as "detached" — and moving the base under it
        # broke the user's rebase at its end ("could not update
        # refs/heads/main"). git's own guard knows: a dry-run fetch into the
        # base is refused while any checkout uses it. Nothing is written.
        guard = _g(["fetch", "--dry-run", "--no-tags", "--no-write-fetch-head",
                    "--no-recurse-submodules", ".", f"{new}:refs/heads/{base}"], wt)
        if not guard.ok:
            if "refusing to fetch into branch" in (guard.err or ""):
                raise GitRefused(
                    f"{base} is in use in one of your checkouts (a rebase or a bisect may be under "
                    "way), so nothing was merged. Finish that first, then merge again.", 409,
                    _masked(guard.err),
                )
            raise GitRefused(
                f"{base} changed while merging, so nothing was changed. Merge again.", 409,
                _masked(guard.err or guard.out),
            )
        moved = _g(["update-ref", "-m", message, f"refs/heads/{base}", new, base_sha], wt)
        if not moved.ok:
            raise GitRefused(
                f"{base} changed while merging, so nothing was changed. Merge again.", 409,
                _masked(moved.err),
            )
    return {"merged": True, "into": base, "where": where, "commit": new[:12]}


# --------------------------------------------------------------------------- #
# discard: the worktree folder to the trash, the branch stays
# --------------------------------------------------------------------------- #


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def looks_like_worktree(facts: dict[str, str]) -> bool:
    """A last check before a folder is moved: it is a LINKED worktree (its
    ``.git`` is a file, not a folder) inside a ``<repo>.ij-worktrees`` folder,
    named as the facts name it. A record that points anywhere else is never
    moved."""
    wt = Path(facts.get("worktree", ""))
    name = facts.get("name", "")
    return (
        wt.is_absolute() and wt.parent.name.endswith(WORKTREES_SUFFIX)
        and (not name or wt.name == name) and (wt / ".git").is_file()
    )


#: A worktree held for a moment (a git read still running in it, a virus
#: scanner opening a new file) is retried this many times, this far apart,
#: before Discard says it is in use (the v1.341.1 lesson: a step that must
#: own a file retries a brief hold and names a long one).
RENAME_TRIES = 20
RENAME_RETRY_S = 0.25


def _rename_retrying(src: Path, dst: Path) -> None:
    """``os.rename`` that retries a brief hold; the last failure is raised."""
    for attempt in range(max(1, RENAME_TRIES)):
        try:
            os.rename(src, dst)
            return
        except OSError as exc:
            if _cross_device(exc) or attempt >= RENAME_TRIES - 1:
                raise
            time.sleep(RENAME_RETRY_S)


def _cross_device(exc: OSError) -> bool:
    """A rename that failed only because the trash is on another drive
    (Windows ERROR_NOT_SAME_DEVICE, POSIX EXDEV)."""
    import errno

    return getattr(exc, "winerror", None) == 17 or exc.errno == errno.EXDEV


IN_USE = (
    "Some files in the worktree are in use (a program or a terminal may be open in it), so "
    "nothing was moved. Close it and try again."
)


def _forget_worktree(main: str, wt: Path) -> None:
    """Make git forget THIS worktree only, once its folder has moved away:
    ``git worktree remove <path>`` (no ``--force``) drops just its record in
    the repository — and refuses, deleting nothing, if a folder is back at
    that path. Review (v1.375.0): ``git worktree prune`` forgot EVERY
    worktree whose folder was missing right then, so a user's own worktree
    on an unplugged drive lost its record (its index, its HEAD) and read
    "not a git repository" once plugged back in. On a failure nothing else
    is tried: git keeps listing ours as "prunable", which is harmless."""
    if main and Path(main).is_dir() and not wt.exists():
        _g(["worktree", "remove", str(wt)], main)


def remove_worktree(home: Any, facts: dict[str, str]) -> dict[str, Any]:
    """DISCARD (and "remove the worktree" after a merge): the worktree folder
    MOVES to ``<home>/trash/<stamp>/worktree-<name>`` — never deleted — and
    git forgets that worktree (:func:`_forget_worktree`). THE BRANCH STAYS
    (its commits are kept until the user deletes the branch themself). A
    folder in use (a program or a terminal open in it) is refused in words
    with NOTHING moved.

    The trash on ANOTHER DRIVE (review, v1.375.0): a move across drives is a
    copy and a delete, and the delete used to find a file in use only part
    of the way through — leaving a half-deleted worktree (its ``.git`` file
    gone first, so every later Discard and Delete refused it for good).
    Now the folder is first RENAMED beside itself on its own drive (one
    step, refused while a program holds a file in it, nothing moved), then
    copied to the trash (a failed copy renames it back: nothing moved), and
    only then is the renamed folder deleted; should a file there still be
    held, the full copy is in the trash and the answer names the leftover
    (``leftover``). Answers ``{moved, trash}``."""
    wt = Path(facts.get("worktree", ""))
    main = facts.get("main") or facts.get("repo") or ""
    dest, leftover = "", ""
    if wt.exists():
        if not looks_like_worktree(facts):
            raise GitRefused(NOT_A_WORKTREE, 409)
        stamp = _stamp()
        trash = Path(home) / "trash" / stamp
        target = trash / f"worktree-{wt.name}"
        n = 2
        while target.exists():
            target = trash / f"worktree-{wt.name}-{n}"
            n += 1
        trash.mkdir(parents=True, exist_ok=True)
        try:
            _rename_retrying(wt, target)
        except OSError as exc:
            if not _cross_device(exc):
                raise GitRefused(IN_USE, 409, _masked(str(exc))) from None
            stage = wt.parent / f".{wt.name}.discarding-{stamp}"
            n = 2
            while stage.exists():
                stage = wt.parent / f".{wt.name}.discarding-{stamp}-{n}"
                n += 1
            try:
                _rename_retrying(wt, stage)
            except OSError as exc1:
                raise GitRefused(IN_USE, 409, _masked(str(exc1))) from None
            try:
                shutil.copytree(stage, target, symlinks=True)
            except OSError as exc2:
                with contextlib.suppress(OSError):
                    shutil.rmtree(target)
                try:
                    os.rename(stage, wt)
                except OSError:
                    raise GitRefused(
                        f"The worktree could not be copied to the trash, and it is now at {stage} "
                        f"(rename it back to {wt.name}). Nothing in it was deleted.", 409,
                        _masked(str(exc2)),
                    ) from None
                raise GitRefused("The worktree could not be copied to the trash, so nothing was moved.",
                                 409, _masked(str(exc2))) from None
            try:
                shutil.rmtree(stage)
            except OSError:
                leftover = str(stage)
        dest = str(target)
    _forget_worktree(main, wt)
    out: dict[str, Any] = {"moved": bool(dest), "trash": dest}
    if leftover:
        out["leftover"] = leftover
    return out


__all__ = [
    "BRANCH_PREFIX",
    "GitRefused",
    "TURN_BRANCH",
    "TurnBranch",
    "branch_changes",
    "clean_git_record",
    "commit",
    "compare_url",
    "create_pr",
    "current",
    "facts_of",
    "gh_path",
    "gh_state",
    "git_path",
    "in_worktree",
    "inspect_folder",
    "merge",
    "pr_status",
    "prompt_line",
    "push",
    "remove_worktree",
    "set_turn_branch",
    "start",
    "status",
    "view",
]
