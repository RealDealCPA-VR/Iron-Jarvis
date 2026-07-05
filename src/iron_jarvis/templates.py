"""Durable store for saved prompts / task templates (daily-driver UX).

:class:`TemplateStore` persists :class:`~iron_jarvis.core.models.SavedPromptRecord`
rows so a user can re-run a frequent task with one click instead of retyping it.
Mirrors :class:`~iron_jarvis.workflows.store.WorkflowStore` (refresh-before-detach
so the returned record stays usable after the session closes).
"""

from __future__ import annotations

import re

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
        description: str = "",
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
                description=(description or "").strip(),
            )
            db.add(row)
            db.commit()
            db.refresh(row)  # un-expire attrs so the detached record stays usable
            return row

    def suggest_from_history(
        self, *, min_count: int = 3, limit: int = 5
    ) -> list[dict]:
        """WATCH-ME-WORK mining: find task patterns the user keeps repeating
        in their session history and suggest each as a one-click template.

        Groups session tasks by token-set similarity (Jaccard ≥ 0.55), keeps
        groups with ``min_count``+ occurrences, and drops anything already
        covered by an existing template. Suggest-only — nothing is created
        until the user clicks save."""
        from .core.models import Session as SessionModel

        def toks(text: str) -> frozenset[str]:
            words = re.findall(r"[a-z]{3,}", (text or "").lower())
            stop = {"the", "and", "for", "with", "that", "this", "from", "into",
                    "please", "then", "them", "your", "file", "files"}
            return frozenset(w for w in words if w not in stop)

        def sim(a: frozenset, b: frozenset) -> float:
            if not a or not b:
                return 0.0
            return len(a & b) / len(a | b)

        with session_scope(self.engine) as db:
            tasks = [
                s.task for s in db.exec(select(SessionModel))
                if s.task and "[Continuing an earlier session" not in s.task
            ]
        existing = [toks(t.task) for t in self.list()]
        groups: list[dict] = []  # {sig, example, count}
        for task in tasks:
            sig = toks(task)
            if len(sig) < 3:
                continue
            for g in groups:
                if sim(sig, g["sig"]) >= 0.55:
                    g["count"] += 1
                    if len(task) < len(g["example"]):
                        g["example"] = task  # keep the crispest phrasing
                    break
            else:
                groups.append({"sig": sig, "example": task, "count": 1})
        out = []
        for g in sorted(groups, key=lambda x: -x["count"]):
            if g["count"] < min_count:
                continue
            if any(sim(g["sig"], e) >= 0.5 for e in existing):
                continue  # already a template
            words = [w for w in re.findall(r"[A-Za-z]{3,}", g["example"])][:4]
            out.append({
                "name": " ".join(words).title() or "Repeated task",
                "task": g["example"][:500],
                "count": g["count"],
            })
            if len(out) >= limit:
                break
        return out

    def seed_starters(self) -> int:
        """First-run only: when the store is EMPTY, add a few self-explanatory
        starter templates (each says when to use it). Returns how many were
        added — 0 whenever the user already has any template, so this never
        re-adds deleted starters."""
        if self.list():
            return 0
        starters = [
            (
                "Daily briefing",
                "Summarize my day so far: recent sessions and their outcomes, "
                "anything pending review or approval, and suggest the 3 most "
                "useful next actions.",
                "Use each morning (or after time away) to get oriented in one click.",
            ),
            (
                "Summarize a document",
                "Read the file I mention (or the newest file in my workspace) and "
                "produce a one-page summary: purpose, key numbers, decisions "
                "needed, and action items.",
                "Use when you receive a long PDF/Word/Excel file and want the "
                "essence without reading it all.",
            ),
            (
                "Client follow-up email",
                "Draft a polite, professional follow-up email to a client about "
                "the topic I describe. Under 150 words, warm but direct, with a "
                "clear next step.",
                "Use when a client has gone quiet or you need a quick, "
                "well-worded nudge.",
            ),
        ]
        for name, task, description in starters:
            self.create(name, task, description=description)
        return len(starters)

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
