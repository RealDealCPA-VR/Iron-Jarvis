"""Sentinels — always-on watchers that NOTICE things and surface them as work.

A Sentinel watches your machine (today: the filesystem, by POLLING — no
``watchdog`` dependency) and, when it notices a new/changed file, mints a
SUGGEST-ONLY proposal into the Motivation Layer backlog (``source="sentinel"``).
It NEVER executes on its own: the existing autonomy dial + budget + approval
still gate any action, and the whole subsystem is OFF unless the user opts in via
``config.sentinels_enabled``.

Importing this module before ``init_db`` registers :class:`SentinelRecord` on the
shared metadata so it auto-creates (mirrors ``scheduling``/``motivation``).
"""

from __future__ import annotations

from .models import KINDS, SentinelRecord
from .service import SentinelService
from .tools import SentinelAddTool, sentinel_tools
from .watcher import Scanner, default_scanner, diff_state

__all__ = [
    "KINDS",
    "SentinelRecord",
    "SentinelService",
    "SentinelAddTool",
    "sentinel_tools",
    "Scanner",
    "default_scanner",
    "diff_state",
]
