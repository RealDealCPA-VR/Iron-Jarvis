"""Sentinel persistence model — always-on watchers that NOTICE, never act.

A :class:`SentinelRecord` is one durable watcher: it describes WHAT to watch
(``kind`` + ``config_json``) and WHAT to suggest when it notices a change
(``task`` + ``agent_type`` + ``risk``). A fired Sentinel mints a SUGGEST-ONLY
:class:`~iron_jarvis.motivation.models.ProposalRecord` (``source="sentinel"``)
into the Motivation Layer's backlog — it NEVER spawns a session. Any execution
still flows through the autonomy dial + budget + approval.

It is a plain SQLModel table; importing this module before ``init_db`` registers
the table on ``SQLModel.metadata`` so it auto-creates (mirrors
``scheduling.models``). ``last_state_json`` is the watcher's durable memory (e.g.
the file paths + mtimes it has already seen) so a restart rehydrates the registry
WITHOUT re-firing for changes already observed.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow

# The watcher kinds a Sentinel may use. Only ``file`` is wired in this slice
# (polling-based, dependency-light). email/calendar are intentionally left out
# until the integration/network layer exists (they are NOT faked here).
KINDS: tuple[str, ...] = ("file",)


class SentinelRecord(SQLModel, table=True):
    """A durable always-on watcher (registry row for the SentinelService)."""

    id: str = Field(default_factory=lambda: new_id("sentinel"), primary_key=True)
    name: str = Field(index=True, unique=True)
    kind: str = "file"  # file (only kind wired in this slice)
    # What to watch — kind-specific. For ``file``: {"path": ..., "glob": ...}.
    config_json: str = "{}"
    # The suggested agent task minted into the backlog when this Sentinel fires.
    task: str = ""
    agent_type: str = "builder"
    risk: str = "low"  # low | med (a noticed signal is never auto-high)
    enabled: bool = True
    last_checked_at: datetime | None = None
    # The watcher's durable memory, e.g. {"seen": {path: mtime}}. Compared on
    # each check so already-observed changes never re-fire across restarts.
    last_state_json: str = "{}"
    created_at: datetime = Field(default_factory=utcnow)

    def decoded_config(self) -> dict:
        """Parse ``config_json`` into a dict (the watch spec)."""
        try:
            data = json.loads(self.config_json or "{}")
            return data if isinstance(data, dict) else {}
        except (TypeError, ValueError):
            return {}

    def decoded_state(self) -> dict:
        """Parse ``last_state_json`` into a dict (the watcher's seen state)."""
        try:
            data = json.loads(self.last_state_json or "{}")
            return data if isinstance(data, dict) else {}
        except (TypeError, ValueError):
            return {}
