"""Durable store for saved prompts / task templates (daily-driver UX).

:class:`TemplateStore` persists :class:`~iron_jarvis.core.models.SavedPromptRecord`
rows so a user can re-run a frequent task with one click instead of retyping it.
Mirrors :class:`~iron_jarvis.workflows.store.WorkflowStore` (refresh-before-detach
so the returned record stays usable after the session closes).
"""

from __future__ import annotations

from sqlalchemy import Engine
from sqlmodel import select

from .core.db import session_scope
from .core.models import AgentType, SavedPromptRecord


class TemplateStore:
    """Persist / list / fetch / remove saved task templates."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create(
        self,
        name: str,
        task: str,
        agent_type: AgentType | str = AgentType.BUILDER,
        provider: str | None = None,
        model: str | None = None,
    ) -> SavedPromptRecord:
        if isinstance(agent_type, str):
            agent_type = AgentType(agent_type)
        with session_scope(self.engine) as db:
            row = SavedPromptRecord(
                name=name.strip() or "Untitled",
                task=task,
                agent_type=agent_type,
                provider=provider,
                model=model,
            )
            db.add(row)
            db.commit()
            db.refresh(row)  # un-expire attrs so the detached record stays usable
            return row

    def list(self) -> list[SavedPromptRecord]:
        """Return every saved template, newest first."""
        with session_scope(self.engine) as db:
            return list(
                db.exec(
                    select(SavedPromptRecord).order_by(
                        SavedPromptRecord.created_at.desc()
                    )
                )
            )

    def get(self, prompt_id: str) -> SavedPromptRecord | None:
        with session_scope(self.engine) as db:
            return db.get(SavedPromptRecord, prompt_id)

    def remove(self, prompt_id: str) -> bool:
        """Delete a template by id; returns False if it was absent."""
        with session_scope(self.engine) as db:
            row = db.get(SavedPromptRecord, prompt_id)
            if row is None:
                return False
            db.delete(row)
            db.commit()
            return True
