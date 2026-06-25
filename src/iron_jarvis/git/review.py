"""Review system (§28).

A session never merges its own work. When the agent finishes, the orchestrator
builds a :class:`ReviewRequest` (diff + changed files + a heuristic risk score)
and emits ``EventType.REVIEW_REQUESTED``. The change lands in the base branch
only when a human calls :func:`approve`; :func:`reject` discards it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .integration import GitSession

# Heuristic thresholds for :func:`risk_assess` (§28 risk scoring).
_HIGH_FILES = 10
_MEDIUM_FILES = 3
_HIGH_DELETIONS = 50
_MEDIUM_DELETIONS = 10


@dataclass
class ReviewRequest:
    """Everything a reviewer needs to approve/reject a session's work (§28)."""

    session_id: str
    branch: str
    base: str
    changed_files: list[str]
    diff: str
    risk: str
    summary: str
    tool_history: list = field(default_factory=list)
    test_results: str | None = None


def risk_assess(changed_files: list[str], diff: str) -> str:
    """Score a change ``"low"`` / ``"medium"`` / ``"high"`` from size & deletions."""
    n_files = len(changed_files)
    deletions = sum(
        1
        for line in diff.splitlines()
        if line.startswith("-") and not line.startswith("---")
    )
    if n_files > _HIGH_FILES or deletions > _HIGH_DELETIONS:
        return "high"
    if n_files > _MEDIUM_FILES or deletions > _MEDIUM_DELETIONS:
        return "medium"
    return "low"


def build_review(
    git_session: GitSession,
    session_id: str,
    summary: str,
    tool_history: list | None = None,
    test_results: str | None = None,
) -> ReviewRequest:
    """Gather the diff + changed files and compute risk. DOES NOT merge."""
    changed_files = git_session.changed_files()
    diff = git_session.diff()
    return ReviewRequest(
        session_id=session_id,
        branch=git_session.branch,
        base=git_session.base,
        changed_files=changed_files,
        diff=diff,
        risk=risk_assess(changed_files, diff),
        summary=summary,
        tool_history=tool_history or [],
        test_results=test_results,
    )


def approve(review: ReviewRequest, git_session: GitSession) -> str:
    """Commit the session's work and merge it into the base branch (§28)."""
    return git_session.merge_into_base()


def reject(review: ReviewRequest, git_session: GitSession) -> None:
    """Discard the session's work: remove the worktree and delete the branch."""
    git_session.discard()


def export_patch(review: ReviewRequest) -> str:
    """Return the review's diff as an applyable patch."""
    return review.diff
