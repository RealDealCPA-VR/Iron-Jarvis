"""Layered memory subsystem (§21 layered memory, §22 retrieval).

Importing this package registers ``MemoryRecord`` on ``SQLModel.metadata`` so
the table auto-creates via ``init_db`` (provided the import runs before
``init_db``). It also exposes the embedder, retriever, layer manager, and tools.
"""

from __future__ import annotations

from .embeddings import Embedder, MockEmbedder
from .layers import MemoryLayers
from .models import MemoryRecord
from .retrieval import Retriever, SqliteVectorRetriever
from .tools import (
    MemoryReadTool,
    MemorySearchTool,
    MemoryWriteTool,
    memory_tools,
)

__all__ = [
    "Embedder",
    "MockEmbedder",
    "MemoryLayers",
    "MemoryRecord",
    "Retriever",
    "SqliteVectorRetriever",
    "MemoryReadTool",
    "MemorySearchTool",
    "MemoryWriteTool",
    "memory_tools",
]
