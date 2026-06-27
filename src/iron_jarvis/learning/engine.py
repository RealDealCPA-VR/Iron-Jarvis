"""Learning Engine — the self-correcting loop.

Captures two signals and turns them into durable, reusable *lessons*:

* explicit **feedback** (thumbs up/down + comment) the user leaves on a session;
* automatic **reflection** the orchestrator runs after each session completes;
* **preferences** the agent infers mid-conversation and chooses to remember.

The payoff is :meth:`apply_to_prompt`: before every run, the accumulated lessons
are appended to the agent's system prompt — so each interaction makes Iron Jarvis
a little better at working the way you want.

Everything here is deterministic and offline (DB rows only, no model calls).
"""

from __future__ import annotations

from sqlalchemy import Engine, func
from sqlmodel import select

from ..core.db import session_scope
from .models import FeedbackRecord, LessonRecord

#: Heading under which lessons are injected into the system prompt.
_LESSONS_HEADING = "\n\n# What I've learned about working with you\n"

#: Keep reflection notes terse — long context defeats the point.
_MAX_NOTE = 240


class LearningEngine:
    """Records feedback/reflections, distils lessons, and injects them into prompts."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # -- feedback -----------------------------------------------------------
    def record_feedback(
        self, session_id: str, rating: str, comment: str = ""
    ) -> FeedbackRecord:
        """Store user feedback and, when it carries signal, distil a lesson.

        A ``down`` rating or any non-empty comment is worth learning from, so it
        is condensed into a high-weight (3) ``feedback`` lesson that future runs
        will see.
        """
        comment = (comment or "").strip()
        with session_scope(self.engine) as db:
            record = FeedbackRecord(
                session_id=session_id, rating=rating, comment=comment
            )
            db.add(record)
            db.commit()
            db.refresh(record)

        if rating == "down" or comment:
            if comment:
                text = (
                    f"Feedback ({rating}) on a past task: {comment}. "
                    "Adjust accordingly."
                )
            else:
                text = (
                    "A past result was rejected; be more careful and ask before "
                    "assuming."
                )
            self._add_lesson(text, source="feedback", weight=3)

        return record

    def feedback_for(self, session_id: str) -> list[FeedbackRecord]:
        """All feedback for a session, newest first."""
        with session_scope(self.engine) as db:
            return list(
                db.exec(
                    select(FeedbackRecord)
                    .where(FeedbackRecord.session_id == session_id)
                    .order_by(FeedbackRecord.created_at.desc())
                )
            )

    # -- preferences --------------------------------------------------------
    def note_preference(self, text: str) -> LessonRecord:
        """Remember an explicit user preference as a top-priority (weight 5) lesson."""
        return self._add_lesson(
            (text or "").strip(), scope="user", source="preference", weight=5
        )

    # -- reflection ---------------------------------------------------------
    def reflect(
        self,
        session_id: str,
        *,
        task: str = "",
        summary: str = "",
        ok: bool = True,
    ) -> LessonRecord | None:
        """Distil a short, reusable lesson from a finished session.

        On failure this always records a note to revisit the approach. On success
        it records a terse domain note only when there is something reusable to
        capture; otherwise it returns ``None``.
        """
        task = (task or "").strip()
        summary = (summary or "").strip()

        if not ok:
            label = task or "a recent task"
            text = f"Task '{label}' did not fully succeed — revisit the approach."
            return self._add_lesson(text, source="reflection", weight=1)

        # Success: only worth storing if there's a reusable nugget.
        note = summary or task
        if not note:
            return None
        if len(note) > _MAX_NOTE:
            note = note[: _MAX_NOTE - 1].rstrip() + "…"
        text = f"Worked well for '{task}': {note}" if task else note
        return self._add_lesson(text, source="reflection", weight=1)

    # -- retrieval / injection ---------------------------------------------
    def lessons(
        self, scope: str | None = "user", limit: int = 12
    ) -> list[LessonRecord]:
        """Lessons ordered for injection: highest EFFECTIVE weight first, newest next.

        The effective weight is the static ``weight`` plus the outcome-driven
        ``weight_bonus`` the ImprovementEngine maintains, so a lesson that has
        actually been helping surfaces ahead of one that has been hurting. (NULL
        bonus on a pre-existing row is coalesced to 0.) ``scope=None`` returns
        lessons across all scopes.
        """
        effective = LessonRecord.weight + func.coalesce(LessonRecord.weight_bonus, 0.0)
        with session_scope(self.engine) as db:
            query = select(LessonRecord)
            if scope is not None:
                query = query.where(LessonRecord.scope == scope)
            query = query.order_by(
                effective.desc(), LessonRecord.created_at.desc()
            ).limit(limit)
            return list(db.exec(query))

    def apply_to_prompt(
        self, system_prompt: str, *, scope: str | None = "user", limit: int = 8
    ) -> str:
        """Append the top lessons to ``system_prompt`` — the self-correction step.

        Returns the prompt unchanged when there is nothing learned yet.
        """
        items = self.lessons(scope=scope, limit=limit)
        if not items:
            return system_prompt
        bullets = "\n".join(f"- {lesson.text}" for lesson in items)
        return f"{system_prompt}{_LESSONS_HEADING}{bullets}"

    # -- internals ----------------------------------------------------------
    def _add_lesson(
        self,
        text: str,
        *,
        scope: str = "user",
        source: str = "reflection",
        weight: int = 1,
    ) -> LessonRecord:
        with session_scope(self.engine) as db:
            record = LessonRecord(
                text=text, scope=scope, source=source, weight=weight
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            return record
