"""Learning Engine — the self-correcting loop.

Captures two signals and turns them into durable, reusable *lessons*:

* explicit **feedback** (thumbs up/down + comment) the user leaves on a session;
* automatic **reflection** the orchestrator runs after each session completes;
* **preferences** the agent infers mid-conversation and chooses to remember.

The payoff is :meth:`apply_to_prompt`: before every run, the accumulated lessons
are appended to the agent's system prompt — so each interaction makes Iron Jarvis
a little better at working the way you want.

Recording and retrieval are deterministic and offline (DB rows only). Because
reflection captures session summaries fairly literally, the pile is COMPACTED:
:meth:`dedup` (deterministic, offline) collapses echoes, and :meth:`distill`
(model-backed, only ever through a REAL provider the caller supplies) condenses
raw reflections into a few short, generalized lessons — so prompts carry
distilled experience, not a transcript of past summaries.
"""

from __future__ import annotations

import json
import re
from typing import Awaitable, Callable

from sqlalchemy import Engine, func
from sqlmodel import select

from ..core.db import session_scope
from .models import FeedbackRecord, LessonRecord

#: Heading under which lessons are injected into the system prompt.
_LESSONS_HEADING = "\n\n# What I've learned about working with you\n"

#: Keep reflection notes terse — long context defeats the point.
_MAX_NOTE = 240

#: Reflection lessons about the same task ("Worked well for 'X': …") are echoes
#: of each other — only the newest carries information.
_TASK_ECHO = re.compile(r"^worked well for '(?P<task>.+?)':", re.IGNORECASE)

#: Minimum raw reflections before a distillation pass is worth a model call.
_DISTILL_MIN = 5
#: How many raw reflections one distillation pass reads (oldest first).
_DISTILL_BATCH = 40
#: Ceiling on lessons a distillation pass may produce.
_DISTILL_MAX_OUT = 8


def _norm(text: str) -> str:
    """Normalize for duplicate detection: lowercase, collapse non-alphanumerics."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


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

    # -- compaction ----------------------------------------------------------
    def dedup(self) -> int:
        """Deterministically collapse duplicate lessons. Returns rows removed.

        Two collapse rules, applied only to AUTO-CAPTURED lessons (``reflection``
        — explicit ``preference``/``feedback``/user-authored rows are never
        touched, and ``distilled`` output is already compact):

        * normalized-identical text → keep the highest effective weight (newest
          on a tie);
        * "Worked well for 'X': …" echoes about the SAME task X → keep the newest.
        """
        removed = 0
        with session_scope(self.engine) as db:
            rows = list(
                db.exec(
                    select(LessonRecord)
                    .where(LessonRecord.source == "reflection")
                    .order_by(LessonRecord.created_at.desc())
                )
            )
            keep_by_key: dict[str, LessonRecord] = {}
            for row in rows:  # newest first — first seen wins ties on recency
                m = _TASK_ECHO.match(row.text or "")
                key = f"task::{_norm(m.group('task'))}" if m else f"text::{_norm(row.text)}"
                best = keep_by_key.get(key)
                if best is None:
                    keep_by_key[key] = row
                    continue
                # Higher effective weight replaces the kept row (newest wins ties).
                if row.effective_weight > best.effective_weight:
                    db.delete(best)
                    keep_by_key[key] = row
                else:
                    db.delete(row)
                removed += 1
            if removed:
                db.commit()
        return removed

    def raw_reflection_count(self) -> int:
        """How many auto-captured, undistilled lessons are piled up."""
        with session_scope(self.engine) as db:
            return len(
                list(
                    db.exec(
                        select(LessonRecord.id)
                        .where(LessonRecord.source == "reflection")
                        .where(LessonRecord.weight <= 1)
                    )
                )
            )

    async def distill(
        self,
        complete: Callable[[str], Awaitable[str]],
        *,
        batch: int = _DISTILL_BATCH,
        max_out: int = _DISTILL_MAX_OUT,
    ) -> dict:
        """Condense the raw reflection pile into a few durable lessons.

        ``complete`` is an async ``prompt -> reply`` the DAEMON supplies, wired
        to a REAL provider (never mock — a fabricated "lesson" would poison
        every future prompt). The raw rows are replaced by ``source="distilled"``
        weight-2 lessons. Raises when the model reply is unusable; the raw rows
        are only deleted after the replacements are written.
        """
        with session_scope(self.engine) as db:
            raws = list(
                db.exec(
                    select(LessonRecord)
                    .where(LessonRecord.source == "reflection")
                    .where(LessonRecord.weight <= 1)
                    .order_by(LessonRecord.created_at.asc())
                    .limit(batch)
                )
            )
        if len(raws) < _DISTILL_MIN:
            return {"reviewed": 0, "distilled": 0, "removed": 0,
                    "note": f"only {len(raws)} raw reflections — nothing to distill yet"}

        bullets = "\n".join(f"- {r.text}" for r in raws)
        prompt = (
            "Below are raw notes auto-captured from past agent sessions. Distill "
            f"them into at most {max_out} SHORT, general, reusable working lessons "
            "(imperative voice, one sentence each, no task-specific details unless "
            "they will recur). Merge duplicates; drop notes with no reusable "
            "signal. Reply with ONLY a JSON array of strings.\n\n"
            f"{bullets}"
        )
        reply = await complete(prompt)
        distilled = _parse_distilled(reply, max_out=max_out)
        if not distilled:
            raise ValueError("distillation reply contained no usable lessons")

        with session_scope(self.engine) as db:
            for text in distilled:
                db.add(LessonRecord(text=text, scope="user", source="distilled", weight=2))
            for r in raws:
                row = db.get(LessonRecord, r.id)
                if row is not None:
                    db.delete(row)
            db.commit()
        return {"reviewed": len(raws), "distilled": len(distilled), "removed": len(raws)}

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


def _parse_distilled(reply: str, *, max_out: int) -> list[str]:
    """Parse the model's distillation reply: a JSON array of strings, tolerating
    a fenced block or (fallback) plain bullet lines. Empties are dropped."""
    text = (reply or "").strip()
    if not text:
        return []
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
        if isinstance(data, list):
            out = [str(x).strip() for x in data if str(x).strip()]
            return out[:max_out]
    except (ValueError, TypeError):
        pass
    # Fallback: bullet lines ("- …" / "* …" / "1. …").
    out = []
    for line in text.splitlines():
        m = re.match(r"^\s*(?:[-*•]|\d+[.)])\s+(.*\S)\s*$", line)
        if m:
            out.append(m.group(1))
    return out[:max_out]
