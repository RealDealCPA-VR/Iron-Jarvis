"""Learning Engine — the self-correcting loop.

Captures two signals and turns them into durable, reusable *lessons*:

* explicit **feedback** (thumbs up/down + comment) the user leaves on a session;
* automatic **reflection** the orchestrator runs after each session completes;
* **preferences** the user states in chat (kept at once) or an agent run
  infers (v1.501.0: a suggestion that waits for the user to keep it).

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

import asyncio
import json
import logging
import re
import threading
from collections import OrderedDict
from typing import Any, Awaitable, Callable

from sqlalchemy import Engine, case, func, literal_column, or_
from sqlmodel import select

from ..core.db import session_scope
from ..core.ids import utcnow
from .models import STATUS_CONFIRMED, STATUS_PROPOSED, FeedbackRecord, LessonRecord
from .preferences import SOURCE_WENT_WRONG

log = logging.getLogger(__name__)

#: Heading under which lessons are injected into the system prompt.
_LESSONS_HEADING = "\n\n# What I've learned about working with you\n"

#: Keep reflection notes terse — long context defeats the point.
_MAX_NOTE = 240

#: Lesson sources that are TASK NOTES, not knowledge about the user (v1.279.0):
#: the orchestrator's per-run "Worked well for '<task>': <summary>" rows. On the
#: daily driver 23 of 25 lessons were these, and the top-8 injection under
#: "What I've learned about working with you" was six of them — one recording
#: the offline mock's scripted answer. They still feed ``dedup``/``distill``
#: (which turns them into real lessons) and the Lessons tab; the prompt reads
#: preferences, feedback, user-written and distilled lessons only.
_PROMPT_EXCLUDED_SOURCES: tuple[str, ...] = ("reflection",)

#: Reflection lessons about the same task ("Worked well for 'X': …") are echoes
#: of each other — only the newest carries information.
_TASK_ECHO = re.compile(r"^worked well for '(?P<task>.+?)':", re.IGNORECASE)

#: Minimum raw reflections before a distillation pass is worth a model call.
_DISTILL_MIN = 5
#: How many raw reflections one distillation pass reads (oldest first).
_DISTILL_BATCH = 40
#: Ceiling on lessons a distillation pass may produce.
_DISTILL_MAX_OUT = 8

#: v1.501.0 (north-star plan 1.8): the most characters of lesson lines one
#: prompt carries. EVERY kept preference (``source="preference"``, the
#: owner's own words or a suggestion they kept) is told first (highest weight,
#: then newest) until this is spent; then up to ``limit`` lessons the owner did not state
#: (feedback, condensed lessons) by effective weight, in what is left. It was
#: a count of eight, so a ninth kept preference — or one an unrelated run's
#: score had sunk — reached no prompt while the Memory page listed it as kept.
LESSONS_BUDGET_CHARS = 6000
#: Most kept preferences read for one prompt (each is at most a few hundred
#: characters, so the budget, not this, is the real bound).
_STATED_SCAN = 400
#: Runs whose injected lessons are remembered for outcome credit, and the most
#: lesson ids kept per run (process memory: a restart forgets, and that run
#: credits nothing rather than whatever ranks top at scoring time).
_INJECTED_RUNS = 512
_INJECTED_PER_RUN = 200

#: A tidy-up event names at most this many rows of each kind.
_TIDY_EVENT_ROWS = 50
#: Text kept per row a tidy-up event names.
_TIDY_TEXT = 160


#: v1.501.0: the sources of a row in the owner's own words. v1.507.0
#: (north-star plan 7.1): ``went_wrong`` — their correction, their note, or an
#: undo of a change with their request — joins it (told only where the client
#: wall lets its chat be told, ``preferences.told_lessons``).
STATED_SOURCES: tuple[str, ...] = ("preference", SOURCE_WENT_WRONG)


def stated_clause():
    """The SQL condition "the owner stated (or kept) this preference"
    (v1.501.0): ``source`` in :data:`STATED_SOURCES`. Such a row reaches
    every prompt it may be told in, under the character budget, and no
    outcome bonus moves it — the owner's word is not ranked by how unrelated
    runs scored. (A row an agent run wrote before v1.501.0 was stored the same
    way and cannot be told apart.)"""
    return LessonRecord.source.in_(STATED_SOURCES)  # type: ignore[attr-defined]


def _norm(text: str) -> str:
    """Normalize for duplicate detection: lowercase, collapse non-alphanumerics."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


class LearningEngine:
    """Records feedback/reflections, distils lessons, and injects them into prompts."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        # v1.501.0 (plan 1.8): {session id: [lesson ids apply_to_prompt told
        # that run]} — the ONLY lessons an outcome is credited to.
        self._injected: "OrderedDict[str, list[str]]" = OrderedDict()
        self._injected_lock = threading.Lock()

    # -- feedback -----------------------------------------------------------
    def record_feedback(
        self,
        session_id: str,
        rating: str,
        comment: str = "",
        *,
        lesson_on_bare_down: bool = True,
        lesson_on_note: bool = True,
    ) -> FeedbackRecord:
        """Store user feedback and, when it carries signal, distil a lesson.

        A ``down`` rating or any non-empty comment is worth learning from, so it
        is condensed into a high-weight (3) ``feedback`` lesson that future runs
        will see.

        ``lesson_on_bare_down=False`` (v1.320.0, a chat reply's 👎 with no
        note): the rating is recorded but no lesson is written — "a past
        result was rejected; be more careful" says nothing about WHAT to do
        differently, and a thumbs-down on one reply must not add a vague rule
        to every later prompt. The note, when given, is the lesson.

        ``lesson_on_note=False`` (v1.507.0, north-star plan 7.1, a chat
        reply's 👎 WITH a note): no lesson is written here either — the note
        waits in Improvements as a suggestion (``learning.went_wrong``) and
        reaches no prompt until the owner keeps it.
        """
        comment = (comment or "").strip()
        with session_scope(self.engine) as db:
            record = FeedbackRecord(
                session_id=session_id, rating=rating, comment=comment
            )
            db.add(record)
            db.commit()
            db.refresh(record)

        if comment and not lesson_on_note:
            return record
        if comment or (rating == "down" and lesson_on_bare_down):
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
        """Remember an explicit user preference as a top-priority (weight 5) lesson.

        v1.305.0: the user SAID it, so it is ``confirmed`` with origin
        ``said`` straight away (no question), and its signature is stored so
        a later repeated correction of the same thing is never proposed."""
        from .corrections import signature

        # v1.499.3: a preference reaches EVERY later prompt, so a key or a
        # password typed into one is kept masked (``sk-***``), never verbatim.
        clean = without_secrets((text or "").strip())
        row = self._add_lesson(
            clean, scope="user", source="preference", weight=5,
            status=STATUS_CONFIRMED, origin="said", signature=signature(clean) or None,
        )
        # v1.306.0: a switched-on "share with Build" re-renders (debounced).
        from ..profile.share import notify_changed

        notify_changed()
        return row

    def suggest_preference(self, text: str, *, evidence: list[dict]) -> "tuple[LessonRecord | None, str]":
        """v1.501.0 (decision D2): a preference an agent run (a schedule, a
        mission, a helper) GUESSED — the owner did not say it in a chat turn
        — waits in Improvements as a suggestion with the run as ``evidence``
        and reaches no prompt until the owner keeps it. Secrets are masked as
        in :meth:`note_preference`. Returns ``(row, outcome)``; see
        ``preferences.mint_noticed``."""
        from .preferences import mint_noticed

        return mint_noticed(self.engine, without_secrets((text or "").strip()), evidence=evidence)

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
            return self._add_lesson(text, source="reflection", weight=1, **_run_evidence(session_id, text))

        # Success: only worth storing if there's a reusable nugget.
        note = summary or task
        if not note:
            return None
        if len(note) > _MAX_NOTE:
            note = note[: _MAX_NOTE - 1].rstrip() + "…"
        text = f"Worked well for '{task}': {note}" if task else note
        return self._add_lesson(text, source="reflection", weight=1, **_run_evidence(session_id, text))

    # -- retrieval / injection ---------------------------------------------
    def lessons(
        self,
        scope: str | None = "user",
        limit: int = 12,
        *,
        exclude_sources: tuple[str, ...] = (),
    ) -> list[LessonRecord]:
        """Lessons ordered for injection: highest EFFECTIVE weight first, newest next.

        CONFIRMED rows only (v1.305.0). Every prompt reader
        (``apply_to_prompt``, ``recall_lessons``, the memory fabric, the Build
        assist, agents, the round table) comes through here, so a ``proposed``
        or ``declined`` preference can reach NO prompt: it is filtered at the
        one query they all share. A NULL status (a row older than v1.305.0)
        reads as confirmed.

        The effective weight is the static ``weight`` plus the outcome-driven
        ``weight_bonus`` the ImprovementEngine maintains, so a lesson that has
        actually been helping surfaces ahead of one that has been hurting. (NULL
        bonus on a pre-existing row is coalesced to 0.) ``scope=None`` returns
        lessons across all scopes.

        v1.501.0 (plan 1.8): the bonus counts only for a lesson the owner did
        NOT state (:func:`stated_clause`); a kept preference is ordered by its
        own weight, whatever bonus an older release wrote on it.
        """
        effective = LessonRecord.weight + case(
            (stated_clause(), 0.0), else_=func.coalesce(LessonRecord.weight_bonus, 0.0)
        )
        with session_scope(self.engine) as db:
            query = select(LessonRecord)
            if scope is not None:
                query = query.where(LessonRecord.scope == scope)
            if exclude_sources:
                # v1.279.0: the prompt asks for everything BUT task reflections.
                query = query.where(LessonRecord.source.not_in(list(exclude_sources)))
            query = query.where(confirmed_clause())
            query = query.order_by(
                effective.desc(), LessonRecord.created_at.desc()
            ).limit(limit)
            return list(db.exec(query))

    def _stated(self, scope: str | None) -> list[LessonRecord]:
        """Every kept preference (:func:`stated_clause`, confirmed), the
        highest weight first, newest next — what each prompt tells first."""
        with session_scope(self.engine) as db:
            query = select(LessonRecord).where(stated_clause()).where(confirmed_clause())
            if scope is not None:
                query = query.where(LessonRecord.scope == scope)
            query = query.order_by(LessonRecord.weight.desc(), LessonRecord.created_at.desc()).limit(_STATED_SCAN)
            return list(db.exec(query))

    def apply_to_prompt(
        self,
        system_prompt: str,
        *,
        scope: str | None = "user",
        limit: int = 8,
        event_bus=None,
        session_id: "str | None" = None,
    ) -> str:
        """Append what was learned to ``system_prompt`` — the self-correction step.

        Returns the prompt unchanged when there is nothing learned yet.

        v1.501.0 (plan 1.8): EVERY kept preference is told (the owner's own
        words come first, never ranked out by a count), then up to ``limit``
        lessons the owner did not state by effective weight; all of it inside
        :data:`LESSONS_BUDGET_CHARS`. The ids told are remembered against
        ``session_id`` (:meth:`injected_into`), so a run's outcome is credited
        only to what that run was actually told.

        v1.298.0: each lesson line is SCANNED for prompt injection
        (``core/promptguard``, source "lessons") and a flagged lesson is
        DROPPED, not placeholder'd — a ``- [BLOCKED: …]`` bullet under "what
        I've learned" would read as a lesson. A lesson is model-distilled
        from a session's text, which is how an attack would get here.
        """
        from ..core.promptguard import publish_blocked, scan_context
        from .preferences import told_lessons

        stated_all = self._stated(scope)
        stated_ids = {r.id for r in stated_all}
        # v1.507.0 (north-star plan 7.1 / 7.3): a kept suggestion from what
        # went wrong in a chat, or from Reflect's low-scoring runs, is told
        # only where the client wall lets its chat or runs be told.
        stated = told_lessons(self.engine, stated_all)
        others = [
            r for r in told_lessons(self.engine, self.lessons(
                scope=scope, limit=limit + len(stated_ids), exclude_sources=_PROMPT_EXCLUDED_SOURCES
            ))
            if r.id not in stated_ids
        ][: max(0, int(limit))]
        if not stated and not others:
            return system_prompt
        kept: list[str] = []
        told: list[str] = []
        told_rows: list[tuple[Any, str]] = []
        spent = 0
        dropped = None
        for lesson in [*stated, *others]:
            result = scan_context(lesson.text, source="lessons")
            if result.blocked:
                if dropped is None:
                    dropped = result
                else:
                    dropped.blocked.extend(result.blocked)
                continue
            line = f"- {result.text}"
            cost = len(line) + (1 if kept else 0)
            if spent + cost > LESSONS_BUDGET_CHARS:
                continue  # a shorter one later may still fit
            spent += cost
            kept.append(line)
            told.append(lesson.id)
            told_rows.append((lesson, result.text))
        if dropped is not None:
            publish_blocked(event_bus, session_id, dropped)
        if session_id:
            self._remember_injected(str(session_id), told)
        if not kept:
            return system_prompt
        self._note_told(told_rows, scope)
        bullets = "\n".join(kept)
        return f"{system_prompt}{_LESSONS_HEADING}{bullets}"

    @staticmethod
    def _note_told(rows: "list[tuple[Any, str]]", scope: str | None) -> None:
        """Next leaps 15-a: each lesson just told, as an item of the reply's
        ``used`` list (``memory.used.note``; nothing without a collector). A
        kept preference of the owner's own list (scope ``user``: the Memory
        page's kept preferences, every source but task notes) can be
        corrected or forgotten from the pop-up. Never raises."""
        try:
            from ..memory import used as _used

            if not _used.collecting():
                return
            fix = _used.FIXES if scope == "user" else ()
            for lesson, text in rows:
                _used.note(
                    "preference", getattr(lesson, "id", ""), text,
                    when=getattr(lesson, "created_at", None), href=_used.PREFERENCES_HREF,
                    fix=fix, text=text,
                )
        except Exception:  # noqa: BLE001 — the list is extra; the prompt is not
            log.debug("the lessons told could not be listed", exc_info=True)

    def _remember_injected(self, session_id: str, ids: list[str]) -> None:
        with self._injected_lock:
            prior = self._injected.pop(session_id, [])
            merged = list(dict.fromkeys([*prior, *ids]))[:_INJECTED_PER_RUN]
            self._injected[session_id] = merged
            while len(self._injected) > _INJECTED_RUNS:
                self._injected.popitem(last=False)

    def injected_into(self, session_id: str, *, forget: bool = True) -> list[str]:
        """The lesson ids :meth:`apply_to_prompt` told run ``session_id``
        (v1.501.0) — ``[]`` when it was told none (``skip_memory``) or the
        daemon restarted since. ``forget`` drops the entry (the outcome is
        recorded once)."""
        with self._injected_lock:
            if forget:
                return list(self._injected.pop(str(session_id), []))
            return list(self._injected.get(str(session_id), []))

    def counts_by_source(self, scope: str | None = "user") -> dict[str, int]:
        """How many lessons each source holds (v1.279.0) — the Memory page's
        "what Jarvis knows" card says how much of the pile is task notes."""
        with session_scope(self.engine) as db:
            query = (
                select(LessonRecord.source, func.count())
                .where(confirmed_clause())
                .group_by(LessonRecord.source)
            )
            if scope is not None:
                query = query.where(LessonRecord.scope == scope)
            return {str(src or ""): int(n) for src, n in db.exec(query)}

    # -- compaction ----------------------------------------------------------
    def dedup(self, *, report: "list[dict] | None" = None) -> int:
        """Deterministically collapse duplicate lessons. Returns rows removed.

        Two collapse rules, applied only to AUTO-CAPTURED lessons (``reflection``
        — explicit ``preference``/``feedback``/user-authored rows are never
        touched, and ``distilled`` output is already compact):

        * normalized-identical text → keep the highest effective weight (newest
          on a tie);
        * "Worked well for 'X': …" echoes about the SAME task X → keep the newest.

        v1.501.0: a task note a condensed lesson names is never touched (it is
        kept until the owner answers that suggestion), and ``report`` (when
        given) receives ``{id, text, into}`` for every row removed — the
        record the tidy-up event publishes (:func:`tidy_pass`).
        """
        from .preferences import named_notes

        removed = 0
        with session_scope(self.engine) as db:
            named = set().union(*named_notes(db).values())
            # v1.510.0: two notes written in the same clock tick (Windows'
            # clock steps in whole milliseconds or more) tie on ``created_at``,
            # and which one was "newest" was then the database's choice: the
            # tidy-up merged either into the other. The row written LATER is
            # the newer one (``rowid``), so the order is always the same.
            rows = [
                r for r in db.exec(
                    select(LessonRecord)
                    .where(LessonRecord.source == "reflection")
                    .order_by(
                        LessonRecord.created_at.desc(),
                        literal_column("lessonrecord.rowid").desc(),
                    )
                )
                if r.id not in named
            ]
            keep_by_key: dict[str, LessonRecord] = {}
            gone: list[tuple[LessonRecord, str]] = []
            for row in rows:  # newest first — first seen wins ties on recency
                m = _TASK_ECHO.match(row.text or "")
                key = f"task::{_norm(m.group('task'))}" if m else f"text::{_norm(row.text)}"
                best = keep_by_key.get(key)
                if best is None:
                    keep_by_key[key] = row
                    continue
                # Higher effective weight replaces the kept row (newest wins ties).
                if row.effective_weight > best.effective_weight:
                    gone.append((best, key))
                    db.delete(best)
                    keep_by_key[key] = row
                else:
                    gone.append((row, key))
                    db.delete(row)
                removed += 1
            if report is not None:
                # Merged into the row its key finally kept.
                report.extend(
                    {"id": row.id, "text": _clip(row.text, _TIDY_TEXT), "into": keep_by_key[key].id}
                    for row, key in gone
                )
            if removed:
                db.commit()
        return removed

    def raw_reflection_count(self) -> int:
        """How many auto-captured, undistilled lessons are piled up (v1.501.0:
        a note a condensed lesson already names — waiting, kept or declined —
        is not counted: it is never condensed again)."""
        from .preferences import named_notes

        with session_scope(self.engine) as db:
            named = set().union(*named_notes(db).values())
            ids = db.exec(
                select(LessonRecord.id)
                .where(LessonRecord.source == "reflection")
                .where(LessonRecord.weight <= 1)
            )
            return sum(1 for i in ids if i not in named)

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
        every future prompt). Raises when the model reply is unusable.

        v1.501.0 (decision D3): a condensed lesson is a model's summary, not
        the owner's word, so each is written ``proposed`` (origin
        ``noticed``, ``source="distilled"``, weight 2) with the task notes it
        came from — and their runs — as evidence, and waits in Improvements
        and on the Memory page. NOTHING is deleted here: the notes stay until
        the owner answers (``preferences.settle_condensed``: kept, the notes
        go; declined, they stay and are never condensed again). One batch at
        a time: while one waits, no new pass runs. The result's
        ``suggested`` lists ``{id, text}`` of each row made.
        """
        from .preferences import _blocked, named_notes, SOURCE_DISTILLED

        def _read():  # off the loop (v1.501.0: it reads the condensed rows too)
            with session_scope(self.engine) as db:
                waiting = db.exec(
                    select(LessonRecord.id)
                    .where(LessonRecord.source == SOURCE_DISTILLED)
                    .where(LessonRecord.status == STATUS_PROPOSED)
                    .limit(1)
                ).first()
                named = set().union(*named_notes(db).values())
                raws = [
                    r for r in db.exec(
                        select(LessonRecord)
                        .where(LessonRecord.source == "reflection")
                        .where(LessonRecord.weight <= 1)
                        .order_by(LessonRecord.created_at.asc())
                    )
                    if r.id not in named
                ][:batch]
                return waiting, raws

        waiting, raws = await asyncio.to_thread(_read)
        if waiting is not None:
            return {"reviewed": 0, "distilled": 0, "removed": 0, "suggested": [],
                    "note": "the lessons condensed last time still wait for your answer in Improvements"}
        if len(raws) < _DISTILL_MIN:
            return {"reviewed": 0, "distilled": 0, "removed": 0, "suggested": [],
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

        evidence = json.dumps([_note_evidence(r) for r in raws])

        def _commit() -> list[dict]:  # v1.226.0: SQLite write off the loop
            from .corrections import signature

            made: list[dict] = []
            with session_scope(self.engine) as db:
                for text in distilled:
                    clean = without_secrets(text)
                    sig = signature(clean)
                    if sig and _blocked(db, sig):
                        continue  # already kept, waiting or declined in other words
                    row = LessonRecord(
                        text=clean, scope="user", source=SOURCE_DISTILLED, weight=2,
                        status=STATUS_PROPOSED, origin="noticed",
                        evidence_json=evidence, signature=sig or None,
                    )
                    db.add(row)
                    db.flush()
                    made.append({"id": row.id, "text": _clip(row.text, _TIDY_TEXT)})
                db.commit()
            return made

        made = await asyncio.to_thread(_commit)
        return {
            "reviewed": len(raws), "distilled": len(made), "removed": 0, "suggested": made,
            "note": (
                f"the {len(made)} condensed lesson{'' if len(made) == 1 else 's'} wait in Improvements "
                "as suggestions; the notes they came from are kept until you answer"
            ) if made else "nothing new to suggest",
        }

    # -- internals ----------------------------------------------------------
    def _add_lesson(
        self,
        text: str,
        *,
        scope: str = "user",
        source: str = "reflection",
        weight: int = 1,
        **extra,
    ) -> LessonRecord:
        with session_scope(self.engine) as db:
            record = LessonRecord(
                text=without_secrets(text), scope=scope, source=source, weight=weight, **extra
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            return record


def _clip(text: Any, cap: int) -> str:
    one = " ".join(str(text or "").split())
    return one if len(one) <= cap else one[: cap - 1].rstrip() + "…"


def _run_evidence(session_id: Any, text: str) -> dict[str, Any]:
    """``{"evidence_json": …}`` naming the run a task note came from
    (v1.501.0), so a lesson condensed from it can name its runs; ``{}`` when
    there is no run id."""
    from .preferences import QUOTE_CHARS, RUN_LINK, WHERE_RUN

    sid = str(session_id or "").strip()
    if not sid:
        return {}
    return {"evidence_json": json.dumps([{
        "quote": _clip(without_secrets(text), QUOTE_CHARS),
        "at": utcnow().isoformat(),
        "where": WHERE_RUN,
        "link": f"{RUN_LINK}{sid}",
    }])}


def _note_evidence(row: LessonRecord) -> dict[str, Any]:
    """One evidence entry of a condensed lesson: the task note itself (its
    words, its run's link when the note recorded one) and its id — the id is
    what ``preferences.settle_condensed`` removes once the owner keeps it."""
    from .preferences import QUOTE_CHARS, WHERE_RUN

    link = ""
    try:
        first = (json.loads(row.evidence_json or "[]") or [{}])[0]
        if isinstance(first, dict):
            link = str(first.get("link") or "")
    except (TypeError, ValueError, IndexError):
        link = ""
    created = getattr(row, "created_at", None)
    out = {
        "quote": _clip(row.text, QUOTE_CHARS),
        "at": created.isoformat() if created is not None else "",
        "where": WHERE_RUN,
        "lesson_id": row.id,
    }
    if link:
        out["link"] = link
    return out


async def tidy_pass(
    learning: "LearningEngine",
    *,
    event_bus: Any = None,
    complete: "Callable[[str], Awaitable[str]] | None" = None,
    min_notes: int = 0,
) -> dict[str, Any]:
    """ONE lesson tidy-up pass (v1.501.0, north-star plan 1.7): dedup (off
    the loop), then — when ``complete`` (a REAL provider) is given and at
    least ``min_notes`` task notes are left after dedup — condense the task
    notes into suggestions (:meth:`LearningEngine.distill`). It
    publishes ONE ``memory.updated`` event (``kind: "lesson_tidy"``) naming
    every note merged away (``merged: [{id, text, into}]``) and every
    suggestion made (``suggested: [{id, text}]``), so the owner can review in
    the record what changed; a pass that changed nothing publishes nothing.
    A failed condensing step raises AFTER the dedup record is published."""
    merged: list[dict] = []
    deduped = await asyncio.to_thread(lambda: learning.dedup(report=merged))
    result: dict[str, Any] = {"deduped": deduped, "merged": merged, "suggested": []}
    error: BaseException | None = None
    if complete is not None and (
        min_notes <= 0 or await asyncio.to_thread(learning.raw_reflection_count) >= min_notes
    ):
        try:
            res = await learning.distill(complete)
            result["distill"] = res
            result["suggested"] = list(res.get("suggested") or [])
        except Exception as exc:  # noqa: BLE001 — the dedup record still goes out
            error = exc
    if (merged or result["suggested"]) and event_bus is not None:
        from ..core.events import EventType

        try:
            await event_bus.publish(EventType.MEMORY_UPDATED, {
                "kind": "lesson_tidy",
                "merged": merged[:_TIDY_EVENT_ROWS],
                "merged_count": len(merged),
                "suggested": result["suggested"][:_TIDY_EVENT_ROWS],
                "suggested_count": len(result["suggested"]),
            })
        except Exception:  # noqa: BLE001 — a bus hiccup never undoes the pass
            log.warning("lesson tidy-up: the record could not be published", exc_info=True)
    if error is not None:
        raise error
    return result


def without_secrets(text: str) -> str:
    """*text* with every secret-looking value masked (``detections.redact.mask``:
    ``sk-***``, ``password=***``, ``Bearer ***``) — what a lesson keeps, since a
    lesson is injected into later prompts (v1.499.3)."""
    if not isinstance(text, str) or not text:
        return text
    from ..detections.redact import mask

    return mask(text)


def confirmed_clause():
    """The SQL condition "this lesson may reach a prompt" (v1.305.0): status
    is ``confirmed`` or NULL (a row from before statuses existed)."""
    return or_(LessonRecord.status.is_(None), LessonRecord.status == STATUS_CONFIRMED)


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
