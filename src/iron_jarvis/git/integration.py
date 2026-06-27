"""Git integration via the real ``git`` binary (§27).

A :class:`GitSession` represents one Iron Jarvis session's working branch as a
linked git *worktree* of a project repository. The worktree gives the agent an
isolated checkout to mutate without ever touching the user's main checkout —
the change only lands in the base branch when a review is explicitly approved
(see :mod:`iron_jarvis.git.review`).

We shell out to ``git`` with :mod:`subprocess` (no GitPython dependency) and
always inject identity defaults via ``-c`` so commits succeed even in a fresh
repo with no configured ``user.email`` / ``user.name``.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..core.ids import utcnow

# Identity defaults injected on every git call so commits work in fresh repos
# (§27 — sessions must be able to commit regardless of host git config).
_DEFAULT_EMAIL = "ironjarvis@local"
_DEFAULT_NAME = "Iron Jarvis"


class GitError(RuntimeError):
    """Raised when an invoked ``git`` command exits non-zero."""


def _git(args: list[str], cwd: Path) -> str:
    """Run ``git`` in *cwd*, return stdout, raise :class:`GitError` on failure."""
    cmd = [
        "git",
        "-c",
        f"user.email={_DEFAULT_EMAIL}",
        "-c",
        f"user.name={_DEFAULT_NAME}",
        *args,
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise GitError(f"git {' '.join(args)} failed ({proc.returncode}): {detail}")
    return proc.stdout


def _git_code(args: list[str], cwd: Path) -> int:
    """Run ``git`` and return its exit code (never raises on non-zero)."""
    cmd = [
        "git",
        "-c",
        f"user.email={_DEFAULT_EMAIL}",
        "-c",
        f"user.name={_DEFAULT_NAME}",
        *args,
    ]
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True).returncode


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
    def start(cls, repo: Path, workspace: Path, slug: str) -> "GitSession":
        """Create a new branch + linked worktree at *workspace*.

        Uses ``git worktree add -b <branch> <workspace> HEAD``. The main repo's
        checkout (and its ``base`` branch) is left untouched. *workspace* must
        not already exist with content (git refuses a non-empty target).
        """
        repo = Path(repo)
        workspace = Path(workspace)

        if not (repo / ".git").exists() and _git_code(["rev-parse", "--git-dir"], repo) != 0:
            raise GitError(f"{repo} is not a git repository")

        if workspace.exists():
            if any(workspace.iterdir()):
                raise GitError(f"workspace {workspace} already exists and is not empty")
            # An empty dir trips up `git worktree add`; let git create it.
            workspace.rmdir()

        base = _git(["rev-parse", "--abbrev-ref", "HEAD"], repo).strip()
        branch = branch_name(slug)

        _git(
            ["worktree", "add", "-b", branch, str(workspace), "HEAD"],
            cwd=repo,
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
