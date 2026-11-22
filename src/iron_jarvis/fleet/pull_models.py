"""One row per model download (next leaps 43-a, ``fleet/pulls``).

A download outlives the page that started it and is said again after a
restart: a row left ``downloading`` by an earlier daemon is settled to
``stopped`` with words at the next start (``pulls.PullManager.settle``).
Imported by ``core.db._LATE_MODEL_MODULES`` so an older database gets the
table and any column added later.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow


class ModelDownload(SQLModel, table=True):
    """A model being (or once) downloaded into a model server."""

    __tablename__ = "modeldownload"

    id: str = Field(default_factory=lambda: new_id("dl"), primary_key=True)
    #: The server's id on Model servers (``ollama`` for Ollama on this PC).
    node_id: str = Field(index=True)
    #: The name the server pulls (``qwen3:8b``, ``hf.co/user/repo:Q4_K_M``).
    model: str = ""
    #: How the page names it ("Qwen3 8B").
    name: str = ""
    #: The size before it started: ``measured`` (the registry's manifest) or
    #: ``about`` (the shipped list); ``None`` = not known.
    bytes_total: int | None = None
    size_from: str = ""
    bytes_done: int = 0
    #: ``downloading`` | ``done`` | ``stopped`` (Cancel, or the app closed) |
    #: ``failed``.
    state: str = "downloading"
    #: Who started it: ``page`` (the Download press) or ``chat`` (an approved card).
    started_by: str = "page"
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    #: Why it stopped or failed, in plain words ("" while it runs or once done).
    error: str = ""
