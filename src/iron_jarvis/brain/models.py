"""Brain Profile tables (v1.406.0). Late models: listed in
``core.db._LATE_MODEL_MODULES`` so a fresh install and every test DB get them."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow


class BrainProfileRecord(SQLModel, table=True):
    """One Brain Profile. ``primary`` is a memory base NAME (``brain`` = Iron
    Jarvis memory), the identity every other surface uses for a base."""

    id: str = Field(default_factory=lambda: new_id("bp"), primary_key=True)
    name: str = ""
    primary: str = "brain"
    search_first: bool = True
    save_to: str = "primary"  # primary | ask | never
    prefer_update: bool = True
    on_conflict: str = "ask"  # ask | newest | brain
    guide_ref: str = ""  # a note in the primary, loaded as TRUSTED instructions
    skills_json: str = "[]"  # skill names loaded at the start (trusted, as skills always)
    repo_routing: bool = True
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class BrainPendingWrite(SQLModel, table=True):
    """A save that could not reach its memory base — kept, shown, retried by
    the user. Never silently dropped, never silently re-sent."""

    id: str = Field(default_factory=lambda: new_id("bpw"), primary_key=True)
    profile_id: str = Field(default="", index=True)
    profile_name: str = ""
    base: str = ""
    title: str = ""
    content: str = ""
    error: str = ""
    status: str = Field(default="pending", index=True)  # pending | saved | discarded
    created_at: datetime = Field(default_factory=utcnow)
