"""Long-term memory (§21) — connectors to external knowledge stores.

Local connectors (Obsidian vault, generic markdown "brain") are real and fully
offline. The Notion connector takes an *injected* HTTP client so tests never hit
the network. :class:`LongTermMemory` aggregates connectors and is exposed to the
agent via the ``ltm_search`` / ``ltm_append`` tools.
"""

from __future__ import annotations

from .base import LTMConnector, MarkdownDirConnector, slugify
from .brain import MarkdownBrainConnector
from .manager import LongTermMemory
from .notion import NotionConnector
from .obsidian import ObsidianConnector
from .sources import (
    CustomSourceStore,
    LTMSourceRecord,
    connector_from_record,
    load_custom_sources,
)
from .tools import LTMAppendTool, LTMSearchTool, ltm_tools

__all__ = [
    "LTMConnector",
    "MarkdownDirConnector",
    "slugify",
    "MarkdownBrainConnector",
    "ObsidianConnector",
    "NotionConnector",
    "LongTermMemory",
    "LTMSourceRecord",
    "CustomSourceStore",
    "connector_from_record",
    "load_custom_sources",
    "LTMSearchTool",
    "LTMAppendTool",
    "ltm_tools",
]
