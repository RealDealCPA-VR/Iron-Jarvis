"""Git-native sessions & review system (§27 Git Integration, §28 Review System).

Every Iron Jarvis session is git-native: Create Branch → Perform Work →
Generate Diff → User Review → Approve → Merge. Agents are PROHIBITED from
merging automatically; a merge happens only on an explicit review approval.
"""

from __future__ import annotations

from .integration import GitError, GitSession, branch_name
from .review import (
    ReviewRequest,
    approve,
    build_review,
    export_patch,
    reject,
    risk_assess,
)

__all__ = [
    "GitError",
    "GitSession",
    "branch_name",
    "ReviewRequest",
    "approve",
    "build_review",
    "export_patch",
    "reject",
    "risk_assess",
]
