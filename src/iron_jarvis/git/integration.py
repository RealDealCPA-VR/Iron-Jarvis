"""Git integration via the real ``git`` binary (§27).

A :class:`GitSession` represents one Iron Jarvis session's working branch as a
linked git *worktree* of a project repository. The worktree gives the agent an
isolated checkout to mutate without ever touching the user's main checkout —
the change only lands in the base branch when a review is explicitly approved
(see :mod:`iron_jarvis.git.review`).

We shell out to ``git`` through ``core.procs`` (no GitPython dependency) and
always inject identity defaults via ``-c`` so commits succeed even in a fresh
repo with no configured ``user.email`` / ``user.name``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..core import procs
from ..core.ids import utcnow

# Identity defaults injected on every git call so commits work in fresh repos
# (§27 — sessions must be able to commit regardless of host git config).
_DEFAULT_EMAIL = "ironjarvis@local"
_DEFAULT_NAME = "Iron Jarvis"

#: Wall-clock bound on one git call (v1.333.0 — there was none). Generous on
#: purpose: a worktree checkout of a very large repository is slow, but a git
#: waiting on a credential prompt, a lock or a hung hook must not park a
#: worker thread forever.
GIT_TIMEOUT_S = 300.0


class GitError(RuntimeError):
    """Raised when an invoked ``git`` command exits non-zero."""


@dataclass
class GitRun:
    """One finished ``git`` call (:func:`run_git`): exit code, decoded output,
    and whether it ran out of time (its tree was killed)."""

    code: int
    out: str
    err: str
    timed_out: bool = False
    #: v1.399.0 review: stdout as the child wrote it (``run_git(keep_bytes=
    #: True)`` only) — a blob's bytes and size survive (``out`` turns CRLF
    #: into LF).
    stdout: bytes = b""

    @property
    def ok(self) -> bool:
        return self.code == 0 and not self.timed_out


def git_env(extra: "dict[str, str] | None" = None) -> dict[str, str]:
    """The environment a Build session's git calls run with (v1.375.0): this
    process's own (the frozen app inherits the desktop's PATH) plus
    ``GIT_TERMINAL_PROMPT=0`` — git never waits on a password typed into a
    terminal nobody sees. A credential WINDOW the user's own git opens is
    allowed; the caller's time bound covers it."""
    import os

    env = dict(os.environ)
    # Review (v1.375.0): a variable that points git at ANOTHER repository
    # (set for a hook git is running, or by a shell the app was started
    # from) would make every call of a Build session act on that one,
    # whatever its working folder — a worktree added to the wrong repository.
    for key in _ELSEWHERE_VARS:
        env.pop(key, None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    if extra:
        env.update(extra)
    return env


#: git variables that choose WHICH repository a call works on (git sets most
#: of them for the hooks it runs).
_ELSEWHERE_VARS = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_PREFIX", "GIT_CEILING_DIRECTORIES",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM",
)


def run_git(
    args: list[str],
    cwd: Path,
    *,
    timeout: float = GIT_TIMEOUT_S,
    env: "dict[str, str] | None" = None,
    identity: bool = True,
    input: "bytes | None" = None,
    encoding: "str | None" = None,
    on_spawn: "object | None" = None,
    keep_bytes: bool = False,
    breakaway_ok: bool = True,
) -> GitRun:
    """THE ONE place git is spawned (``core.procs``: its own Job, bounded, the
    tree killed on a timeout). ``identity`` injects the defaults so a commit
    succeeds in a fresh repository (the original behaviour); a Build session
    passes False and borrows them only when the user's git names no one
    (:func:`has_identity`). ``env`` None = this process's own environment;
    ``encoding`` None = the locale's (the original decoding) — a Build
    session reads paths and diffs as UTF-8. BLOCKING: a caller on the event
    loop hops it off. ``on_spawn(proc)`` (v1.399.0 review): ``procs.run``'s
    seam — the child's handle is published so a caller can end it early (the
    night's own commit, whose hooks Emergency stop must be able to stop).
    ``breakaway_ok`` False (the same night commit): git's hooks run in Git's
    own ``sh``, whose MSYS2 runtime starts programs OUTSIDE the parent's Job
    when the Job allows it — a ``sleep`` in a hook outlived the kill and held
    the output pipe open (the v1.386.2 bash lesson)."""
    cmd = ["git"]
    if identity:
        cmd += default_identity()
    cmd += list(args)
    extra: dict = {"on_spawn": on_spawn} if on_spawn is not None else {}
    if not breakaway_ok:
        extra["breakaway_ok"] = False
    res = procs.run(cmd, cwd=cwd, timeout=timeout, env=env, input=input, **extra)  # type: ignore[arg-type]
    return GitRun(
        code=-1 if res.timed_out else res.returncode,
        out=procs.text(res.stdout, encoding),
        err=procs.text(res.stderr, encoding),
        timed_out=bool(res.timed_out),
        stdout=(res.stdout or b"") if keep_bytes else b"",
    )


def default_identity() -> list[str]:
    """The ``-c`` pair that lets a commit succeed when git names no one."""
    return ["-c", f"user.email={_DEFAULT_EMAIL}", "-c", f"user.name={_DEFAULT_NAME}"]


def has_identity(cwd: Path, env: "dict[str, str] | None" = None) -> bool:
    """Does the user's own git config name a committer here (``user.name``
    AND ``user.email``)? A Build session commits as the user when it does."""
    for key in ("user.name", "user.email"):
        got = run_git(["config", "--get", key], cwd, timeout=30.0, env=env, identity=False)
        if not got.ok or not got.out.strip():
            return False
    return True


def _git(args: list[str], cwd: Path, *, env: "dict[str, str] | None" = None) -> str:
    """Run ``git`` in *cwd*, return stdout, raise :class:`GitError` on failure."""
    res = run_git(args, cwd, env=env)
    if res.timed_out:
        raise GitError(f"git {' '.join(args)} did not finish within {GIT_TIMEOUT_S:.0f} s")
    if res.code != 0:
        detail = (res.err or res.out or "").strip()
        raise GitError(f"git {' '.join(args)} failed ({res.code}): {detail}")
    return res.out


def _git_code(args: list[str], cwd: Path, *, env: "dict[str, str] | None" = None) -> int:
    """Run ``git`` and return its exit code (never raises on non-zero)."""
    return run_git(args, cwd, env=env).code


def _slugify(value: str) -> str:
    """Lowercase, hyphenate to a git-ref-safe slug; never empty."""
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "session"


def branch_name(slug: str, ts: str | None = None) -> str:
    """Return the canonical session branch name ``ironjarvis/session-<ts>-<slug>``."""
    if ts is None:
        ts = utcnow().strftime("%Y%m%d-%H%M%S")
    return f"ironjarvis/session-{ts}-{_slugify(slug)}"


def list_session_worktrees(repo: Path) -> list[tuple[Path, str]]:
    """Return ``(workspace, branch)`` for each ``ironjarvis/session-*`` worktree.

    Parses ``git worktree list --porcelain`` so the orchestrator can find and
    garbage-collect worktrees orphaned by a daemon restart (review state is held
    in memory). Returns an empty list if *repo* is not a git repository.
    """
    if _git_code(["rev-parse", "--git-dir"], repo) != 0:
        return []
    out = _git(["worktree", "list", "--porcelain"], cwd=repo)
    results: list[tuple[Path, str]] = []
    cur_path: Path | None = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            cur_path = Path(line[len("worktree ") :].strip())
        elif line.startswith("branch ") and cur_path is not None:
            branch = line[len("branch ") :].strip().removeprefix("refs/heads/")
            if branch.startswith("ironjarvis/session-"):
                results.append((cur_path, branch))
    return results


def prune_worktree(repo: Path, workspace: Path, branch: str) -> None:
    """Best-effort removal of a linked worktree + its branch (never raises)."""
    _git_code(["worktree", "remove", "--force", str(workspace)], cwd=repo)
    _git_code(["worktree", "prune"], cwd=repo)
    if _git_code(["rev-parse", "--verify", branch], cwd=repo) == 0:
        _git_code(["branch", "-D", branch], cwd=repo)


@dataclass
class GitSession:
    """A session's working branch, materialised as a linked git worktree."""

    repo: Path
    workspace: Path
    branch: str
    base: str

    # --- lifecycle --------------------------------------------------------

    @classmethod
    def start(cls, repo: Path, workspace: Path, slug: str, *, branch: "str | None" = None,
              env: "dict[str, str] | None" = None, start_point: str = "HEAD") -> "GitSession":
        """Create a new branch + linked worktree at *workspace*.

        Uses ``git worktree add -b <branch> <workspace> HEAD``. The main repo's
        checkout (and its ``base`` branch) is left untouched. *workspace* must
        not already exist with content (git refuses a non-empty target).
        ``branch`` and ``env`` (v1.375.0, a Build session): the new branch's
        name (``ij/<name>``) and the environment (:func:`git_env` — no
        terminal prompt); omitted = :func:`branch_name` and this process's own,
        the review system's original call. ``start_point`` (v1.377.0, a
        session BRANCHED from another): the commit the new branch starts from
        instead of HEAD.
        """
        repo = Path(repo)
        workspace = Path(workspace)

        if not (repo / ".git").exists() and _git_code(["rev-parse", "--git-dir"], repo, env=env) != 0:
            raise GitError(f"{repo} is not a git repository")

        if workspace.exists():
            if any(workspace.iterdir()):
                raise GitError(f"workspace {workspace} already exists and is not empty")
            # An empty dir trips up `git worktree add`; let git create it.
            workspace.rmdir()

        base = _git(["rev-parse", "--abbrev-ref", "HEAD"], repo, env=env).strip()
        branch = branch or branch_name(slug)

        _git(
            ["worktree", "add", "-b", branch, str(workspace), start_point or "HEAD"],
            cwd=repo,
            env=env,
        )
        return cls(repo=repo, workspace=workspace, branch=branch, base=base)

    # --- inspection -------------------------------------------------------

    def _has_staged_changes(self) -> bool:
        # `git diff --cached --quiet` exits 1 when there ARE staged changes.
        return _git_code(["diff", "--cached", "--quiet"], cwd=self.workspace) != 0

    def changed_files(self) -> list[str]:
        """Stage everything in the worktree and list the changed paths."""
        _git(["add", "-A"], cwd=self.workspace)
        out = _git(["diff", "--cached", "--name-only"], cwd=self.workspace)
        return [line for line in out.splitlines() if line.strip()]

    def diff(self) -> str:
        """Unified diff of all changes (including new files) in the worktree."""
        _git(["add", "-A"], cwd=self.workspace)
        return _git(["diff", "--cached"], cwd=self.workspace)

    def export_patch(self) -> str:
        """Alias for :meth:`diff` — the change as an applyable patch."""
        return self.diff()

    # --- mutation ---------------------------------------------------------

    def commit(self, message: str) -> str:
        """Stage and commit the worktree's changes. No-op if nothing changed."""
        _git(["add", "-A"], cwd=self.workspace)
        if not self._has_staged_changes():
            return ""
        return _git(["commit", "-m", message], cwd=self.workspace)

    def merge_into_base(self, message: str | None = None) -> str:
        """Commit pending work, then merge the session branch into *base*.

        Called ONLY by an explicit review approval (§28) — never by an agent.
        Runs in the main repo: checks out ``base`` and ``git merge --no-ff``.
        Restores the main checkout's original branch afterwards so an approval
        never leaves the developer's working tree parked on a surprise branch
        (a no-op in the normal case where HEAD already points at ``base``).
        """
        self.commit(f"Iron Jarvis session {self.branch}")
        original = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=self.repo).strip()
        _git(["checkout", self.base], cwd=self.repo)
        merge_msg = message or f"Merge {self.branch} into {self.base}"
        try:
            return _git(
                ["merge", "--no-ff", "-m", merge_msg, self.branch],
                cwd=self.repo,
            )
        except GitError:
            # A conflicting merge (base moved) leaves unmerged paths in the index,
            # which makes `git checkout original` refuse and would strand the
            # developer's main checkout mid-conflict. Abort so the tree is clean,
            # then propagate so the approval surfaces as a failure (no merge landed).
            _git_code(["merge", "--abort"], cwd=self.repo)
            raise
        finally:
            if original and original not in (self.base, "HEAD"):
                _git_code(["checkout", original], cwd=self.repo)

    def discard(self) -> None:
        """Remove the worktree and delete the branch; the base is untouched."""
        _git(["worktree", "remove", "--force", str(self.workspace)], cwd=self.repo)
        # Branch is now checked out nowhere, so a forced delete is safe.
        if _git_code(["rev-parse", "--verify", self.branch], cwd=self.repo) == 0:
            _git(["branch", "-D", self.branch], cwd=self.repo)

    def cleanup_after_merge(self) -> None:
        """Remove the (now-merged) worktree + branch so they don't accumulate.

        Called after :meth:`merge_into_base` lands the work on ``base``. Without
        this, every approved session would leave a registered git worktree and a
        dangling branch behind, leaking disk and cluttering ``git worktree list``.
        Best-effort: a failure here never undoes the completed merge.
        """
        if self.workspace.exists():
            _git_code(["worktree", "remove", "--force", str(self.workspace)], cwd=self.repo)
        _git_code(["worktree", "prune"], cwd=self.repo)
        if _git_code(["rev-parse", "--verify", self.branch], cwd=self.repo) == 0:
            _git_code(["branch", "-D", self.branch], cwd=self.repo)
