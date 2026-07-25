"""Durable store for code artifacts (v1.95.0).

Mirrors :class:`~iron_jarvis.workflows.store.WorkflowStore`: SQLModel rows, the
refresh-before-detach pattern so a returned record stays usable after the
session closes, and every write bounded so an agent loop cannot grow the DB
without limit.

Dedup matters more here than in the workflow store. An agent often re-runs a
script it is iterating on, and one row per keystroke would bury the useful ones,
so ``save`` UPSERTS on (session_id, name): same script, same session = the same
artifact with a new outcome. A different session always starts a new row —
provenance stays honest.
"""

from __future__ import annotations

from sqlalchemy import Engine
from sqlmodel import select

from ..core.db import session_scope
from ..core.ids import utcnow
from .models import CodeArtifactRecord

#: Stored source cap. run_code is for small scripts; anything larger is a
#: project, not an artifact. Truncation is visible (a marker is appended).
MAX_SOURCE = 100_000
#: Stored output cap — independent of the tool's own 12k display cap.
MAX_OUTPUT = 20_000


def _clip(text: str, limit: int, what: str) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[{what} truncated at {limit} chars]"


class CodeArtifactStore:
    """Persist / list / fetch / re-record / delete saved scripts."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def save(
        self,
        name: str,
        language: str,
        source: str,
        *,
        description: str = "",
        origin: str = "run_code",
        session_id: str | None = None,
        project_id: str | None = None,
        exit_code: int | None = None,
        output: str = "",
        count_run: bool = True,
    ) -> CodeArtifactRecord:
        """Upsert by (session_id, name) — see the module docstring on why.

        Records the run outcome at the same time, so the common case (an agent
        ran a script) is a single call. ``count_run=False`` stores the source
        WITHOUT claiming it executed — a hand-saved script has not run yet, and
        reporting "1 run, exit None" would be a small lie on the card.
        Returns the refreshed record.
        """
        name = (name or "").strip() or "untitled"
        src = _clip(source, MAX_SOURCE, "source")
        out = _clip(output, MAX_OUTPUT, "output")
        now = utcnow()
        with session_scope(self.engine) as db:
            row = db.exec(
                select(CodeArtifactRecord)
                .where(CodeArtifactRecord.name == name)
                .where(CodeArtifactRecord.session_id == session_id)
            ).first()
            if row is None:
                row = CodeArtifactRecord(
                    name=name,
                    language=language,
                    source=src,
                    description=description,
                    origin=origin,
                    session_id=session_id,
                    project_id=project_id,
                )
            else:
                row.language = language
                row.source = src
                if description:
                    row.description = description
                if project_id:
                    row.project_id = project_id
                row.updated_at = now
            if count_run:
                row.run_count += 1
                row.last_run_at = now
                row.last_exit_code = exit_code
                row.last_output = out
            db.add(row)
            db.commit()
            db.refresh(row)  # un-expire attrs so the detached record stays usable
            return row

    def list(self, project_id: str | None = None) -> list[CodeArtifactRecord]:
        """Saved scripts, most recently touched first; optionally one project's."""
        stmt = select(CodeArtifactRecord)
        if project_id:
            stmt = stmt.where(CodeArtifactRecord.project_id == project_id)
        with session_scope(self.engine) as db:
            rows = list(db.exec(stmt))
        return sorted(rows, key=lambda r: r.updated_at, reverse=True)

    def get(self, artifact_id: str) -> CodeArtifactRecord | None:
        with session_scope(self.engine) as db:
            return db.get(CodeArtifactRecord, artifact_id)

    def record_run(
        self, artifact_id: str, exit_code: int, output: str
    ) -> CodeArtifactRecord | None:
        """Stamp the outcome of a RE-RUN (source untouched)."""
        with session_scope(self.engine) as db:
            row = db.get(CodeArtifactRecord, artifact_id)
            if row is None:
                return None
            row.run_count += 1
            row.last_run_at = utcnow()
            row.last_exit_code = exit_code
            row.last_output = _clip(output, MAX_OUTPUT, "output")
            db.add(row)
            db.commit()
            db.refresh(row)
            return row

    def delete(self, artifact_id: str) -> bool:
        """Remove one saved script; False when the id is unknown."""
        with session_scope(self.engine) as db:
            row = db.get(CodeArtifactRecord, artifact_id)
            if row is None:
                return False
            db.delete(row)
            db.commit()
            return True
