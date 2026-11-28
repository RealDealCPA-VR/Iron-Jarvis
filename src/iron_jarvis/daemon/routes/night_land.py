"""MERGE ALL THAT PASSED — the routes (25-a; the git work and the words live
in ``harness/landing``). Registered by ``routes/night.register``.

* ``GET /build/night/summaries/{night}/land`` — the PREVIEW: one landing per
  folder, its steps in queue order (branch, commits, files with lines added
  and removed, a clash said before anything is pressed), the rows left out
  and why, the command run after each merge, what blocks it now; and the
  night's merges (newest first). Git reads only, off the loop.
* ``POST /build/night/summaries/{night}/land {preview_id}`` — MERGE: the
  preview is read again and must have exactly that id ("changed since you
  looked" otherwise, nothing changed); refused while Emergency stop is on,
  while any of its sessions runs a reply (or Compact, or another git action),
  while another merge runs in that repository, and while the base's checkout
  has changes. Answers 202 with the merge's record; the merge runs in the
  background (``_run``: every session's git lock held, the sessions held so
  no reply starts), and its record says each step as it goes.
* ``GET /build/night/land/{id}`` — one merge's record, and which Undo it gets
  (D-25.1), worked out from git now.
* ``POST /build/night/land/{id}/undo {how}`` — Undo, exactly as the record's
  plan said (``how``); refused in words with nothing changed otherwise.

Iron Jarvis closing stops a running merge (:func:`stop_all`, the lifespan's
shutdown): its tests are killed and the base is not moved.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import threading
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ...core.ids import new_id, utcnow
from ...harness import branches as B
from ...harness import landing as L
from ...harness import sessions as S

log = logging.getLogger(__name__)

NO_SUMMARY = "There is no summary of that night."
NO_SUCH_MERGE = "There is no such merge."
SESSION_BUSY = (
    "{title} is still answering (or busy with another git action), so nothing was merged. Wait for it, or stop it, "
    "then press Merge again."
)
LOCK_BUSY = "A session of this merge is busy with another git action, so nothing was merged. Try again in a moment."

#: The merges running in THIS process: record id -> its stop switch.
_ACTIVE: dict[str, threading.Event] = {}
#: The repositories a merge or an Undo runs in right now (normcased).
_REPOS: set[str] = set()
_GUARD = threading.Lock()
#: Strong references to the background runs (a pending task is never GC'd).
_TASKS: set["asyncio.Task[Any]"] = set()


class LandBody(BaseModel):
    preview_id: str = Field(default="", max_length=80)


class UndoBody(BaseModel):
    how: str = Field(default="", max_length=20)


def stop_all() -> list[str]:
    """Iron Jarvis is closing: every running merge stops (its test is killed;
    the base is not moved). Thread-safe; never raises."""
    with _GUARD:
        items = list(_ACTIVE.items())
    for _rid, ev in items:
        ev.set()
    return [rid for rid, _ in items]


def _repo_key(repo: str) -> str:
    return os.path.normcase(os.path.normpath(str(repo or ""))).rstrip("\\/")


def _claim_repo(repo: str) -> bool:
    key = _repo_key(repo)
    with _GUARD:
        if key in _REPOS:
            return False
        _REPOS.add(key)
        return True


def _release_repo(repo: str) -> None:
    with _GUARD:
        _REPOS.discard(_repo_key(repo))


def _active_ids() -> set[str]:
    with _GUARD:
        return set(_ACTIVE)


# --------------------------------------------------------------------------- #
# reading the night (BLOCKING)
# --------------------------------------------------------------------------- #


def facts_by_thread(engine: Any, thread_ids: list[str]) -> dict[str, "dict[str, str] | None"]:
    """Each session's git facts as stored NOW (no git call). BLOCKING."""
    from sqlmodel import select

    from ...core.db import session_scope
    from ...core.models import ChatThreadRecord

    out: dict[str, "dict[str, str] | None"] = {}
    ids = [t for t in thread_ids if t]
    if not ids:
        return out
    with session_scope(engine) as db:
        rows = db.exec(select(ChatThreadRecord).where(ChatThreadRecord.id.in_(ids))).all()  # type: ignore[attr-defined]
        for row in rows:
            out[row.id] = B.facts_of(S._setup(row))
    return out


def _positions(engine: Any, task_ids: list[str]) -> dict[str, int]:
    from ...harness import night as N

    out: dict[str, int] = {}
    for tid in task_ids:
        task = N.get_task(engine, tid)
        if task is not None:
            out[tid] = int(task.get("position") or 0)
    return out


def _rows(engine: Any, night: str) -> "list[dict[str, Any]] | None":
    from ...harness import night_morning as M

    summary = M.get_summary(engine, night)
    return None if summary is None else list(summary.get("rows") or [])


def night_land_view(engine: Any, night: str, rows: list[dict[str, Any]],
                    facts: "dict[str, dict[str, Any]] | None" = None) -> dict[str, Any]:
    """What a night's summary carries for Merge all, WITHOUT a git call (the
    summary is read every few seconds while it is on screen): the head line's
    numbers per folder and the night's merges, newest first (a merge a closed
    Iron Jarvis left running is settled as stopped here). BLOCKING."""
    ids = [str(r.get("thread_id") or "") for r in rows]
    git_facts = facts_by_thread(engine, ids)
    records = L.records_for_night(engine, night)
    active = _active_ids()
    records = [L.settle_stale(engine, r, active) for r in records]
    counts = L.night_counts(rows, git_facts, records)
    for g in counts["folders"]:
        g["words"] = L.head_words(g["green"], g["total"])
        g["button"] = L.button_words(g["green"]) if g["green"] else ""
    return {**counts, "merges": [L.view(r) for r in records[:5]]}


def preview_view(engine: Any, night: str) -> "dict[str, Any] | None":
    """The preview of a night (git reads). BLOCKING; None = no such night."""
    rows = _rows(engine, night)
    if rows is None:
        return None
    facts = facts_by_thread(engine, [str(r.get("thread_id") or "") for r in rows])
    positions = _positions(engine, [str(r.get("id") or "") for r in rows])
    records = L.records_for_night(engine, night)
    active = _active_ids()
    records = [L.settle_stale(engine, r, active) for r in records]
    landings = L.preview(engine, night, rows, facts, positions, records)
    for land in landings:
        land["words"] = L.head_words(int(land.get("green") or 0), int(land.get("total") or 0))
        land["button"] = L.button_words(int(land.get("ready") or 0)) if land.get("ready") else ""
        if land.get("blocked"):
            land["button"] = ""
    return {"night": night, "landings": landings, "merges": [L.view(r) for r in records[:5]],
            "emergency_stop": False}


def record_session(engine: Any, thread_id: str, changes: dict[str, Any]) -> None:
    """Write a merge's facts onto a night session's git record, onto the facts
    AS STORED NOW (``build_git._record``'s rule); a None value removes the
    key (Undo). BLOCKING; the caller holds the session's git lock."""
    row = S.get_row(engine, thread_id)
    if row is None:
        return
    facts = B.facts_of(S._setup(row))
    if facts is None:
        return
    merged = dict(facts)
    for key, value in changes.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = str(value)
    S.update(engine, thread_id, setup={B.SETUP_KEY: merged})
    if changes.get("merged_at"):
        settle_kept(engine, thread_id)
    elif "merged_at" in changes or changes.get("merge_reversed"):
        # Undo took the merge back off the base (put back: ``merged_at``
        # removed; reversed: ``merge_reversed`` written), so the work is on
        # no branch the user kept: the rows this merge kept are not decided
        # again (review 3 of 25-a).
        unsettle_kept(engine, thread_id)


def _coding_record() -> Any:
    """28-a's coding record (``harness/scoreboard``), or None in a build
    without it. Only THAT module missing is None: any other import error
    (a module it imports that broke) is logged, never silent."""
    import importlib

    name = "iron_jarvis.harness.scoreboard"
    try:
        SB = importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name != name:
            log.warning("the coding record could not be loaded", exc_info=True)
        return None
    except Exception:  # noqa: BLE001 — a record never breaks the merge
        log.warning("the coding record could not be loaded", exc_info=True)
        return None
    return SB


def settle_kept(engine: Any, thread_id: str) -> None:
    """A night task Merge all merged is KEPT in the folder's coding record,
    exactly as a Merge press settles it (28-a: ``scoreboard.settle_session``
    with ``kept`` / ``merged``; only undecided rows change). Both writers of
    ``merged_at`` come here: the run and a merge a closed Iron Jarvis left
    mid-move. An Undo takes it back (:func:`unsettle_kept`). BLOCKING;
    never raises."""
    SB = _coding_record()
    if SB is None:
        return
    try:
        SB.settle_session(engine, thread_id, SB.KEPT, SB.HOW_MERGED)
    except Exception:  # noqa: BLE001 — a record never breaks the merge (a renamed seam is logged)
        log.warning("the coding record could not settle night task %s", thread_id, exc_info=True)


def unsettle_kept(engine: Any, thread_id: str) -> int:
    """Merge all's Undo took the merge back off the base: the session's rows
    a merge kept (``kept`` / ``merged``) are not decided again (verdict, how
    and decided_at cleared, as ``scoreboard.unrewound`` puts back the rows a
    rewind threw), so the next Merge all or a Discard decides them; a row
    decided any other way (a Commit, a Discard) is never touched. Returns how
    many. BLOCKING; never raises."""
    SB = _coding_record()
    if SB is None or engine is None or not thread_id:
        return 0
    try:
        from sqlmodel import select

        from ...core.db import session_scope

        table = SB.CodingOutcome
        with SB._LOCK, session_scope(engine) as db:  # noqa: SLF001 — the record's own write lock
            rows = db.exec(select(table).where(
                table.session_id == str(thread_id), table.verdict == SB.KEPT, table.how == SB.HOW_MERGED,
            )).all()
            for row in rows:
                row.verdict, row.how, row.decided_at = "", "", None
                db.add(row)
            db.commit()
            return len(rows)
    except Exception:  # noqa: BLE001 — a record never breaks the undo
        log.warning("the coding record could not take back night task %s", thread_id, exc_info=True)
        return 0


def _locked_record(engine: Any, thread_id: str, changes: dict[str, Any]) -> None:
    """:func:`record_session` under the session's git lock (a merge a closed
    Iron Jarvis left mid-move, settled on a read). BLOCKING."""
    from .build_git import _locked

    if thread_id:
        _locked(thread_id, lambda: record_session(engine, thread_id, changes))


# --------------------------------------------------------------------------- #
# the background run
# --------------------------------------------------------------------------- #


def _locks(thread_ids: list[str]) -> "list[threading.Lock] | None":
    """Every session's git lock (``build_git._locked``'s), taken in one order
    so two callers never wait on each other; None (nothing held) when one
    cannot be had in time. BLOCKING."""
    from .build_git import _GIT_LOCKS, _GIT_LOCKS_GUARD, GIT_LOCK_WAIT_S

    held: list[threading.Lock] = []
    for tid in sorted(set(t for t in thread_ids if t)):
        with _GIT_LOCKS_GUARD:
            lock = _GIT_LOCKS.setdefault(tid, threading.Lock())
        if not lock.acquire(timeout=GIT_LOCK_WAIT_S):
            for h in reversed(held):
                h.release()
            return None
        held.append(lock)
    return held


def _run_locked(d: Any, record_id: str, land: dict[str, Any], stop: threading.Event,
                thread_ids: list[str]) -> dict[str, Any]:
    """BLOCKING: the merge, under every session's git lock."""
    engine = d.platform.engine
    held = _locks(thread_ids)
    if held is None:
        out = L.update_record(engine, record_id, state=L.STOPPED, words=LOCK_BUSY, ended_at=utcnow())
        return out or {}
    try:
        return L.run(engine, d.platform.config.home, record_id, land, stop=stop, emergency=lambda: _emergency(d),
                     record_session=lambda tid, ch: record_session(engine, tid, ch))
    finally:
        for h in reversed(held):
            h.release()


async def _run(d: Any, record_id: str, land: dict[str, Any], thread_ids: list[str], stop: threading.Event) -> None:
    """The background merge. ``record_id`` is in ``_ACTIVE`` from BEFORE its
    record was written (a summary read meanwhile never takes it for one a
    closed Iron Jarvis left running) until it ends."""
    from .build import _STARTING
    from .build_git import _GIT_ACTING

    job = asyncio.ensure_future(asyncio.to_thread(_run_locked, d, record_id, land, stop, thread_ids))
    try:
        await asyncio.shield(job)
    except asyncio.CancelledError:
        stop.set()
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(asyncio.shield(job), timeout=15.0)
        raise
    except Exception:  # noqa: BLE001 — the record says it
        log.warning("merge all %s ended with an error", record_id, exc_info=True)
    finally:
        with _GUARD:
            _ACTIVE.pop(record_id, None)
        _release_repo(str(land.get("repo") or ""))
        for tid in thread_ids:
            _GIT_ACTING.discard(tid)
            _STARTING.discard(tid)


def _emergency(d: Any) -> bool:
    from ...agents.orchestrator import emergency_stop_on

    try:
        return bool(emergency_stop_on(getattr(d.platform, "config", None)))
    except Exception:  # noqa: BLE001 — unreadable: carry on as off
        return False


def _busy_title(d: Any, steps: list[dict[str, Any]]) -> str:
    """The first session of ``steps`` that may not be merged now (a reply,
    Compact or another git action runs in it), by its task's title; ""."""
    from .build import _COMPACTING, _STARTING, _running_turn
    from .build_git import _GIT_ACTING

    for s in steps:
        tid = str(s.get("thread_id") or "")
        if tid and (tid in _GIT_ACTING or tid in _COMPACTING or tid in _STARTING or _running_turn(d, tid)):
            return L._quote(str(s.get("title") or "A task"))
    return ""


async def land(d: Any, night: str, preview_id: str) -> Any:
    """POST …/land: see the module's words. On the loop: the claims; off it:
    every read and write."""
    from .build import _STARTING
    from .build_git import _GIT_ACTING

    engine = d.platform.engine
    if _emergency(d):
        return JSONResponse(status_code=409, content={"detail": L.EMERGENCY_ON})
    fresh = await asyncio.to_thread(preview_view, engine, night)
    if fresh is None:
        return JSONResponse(status_code=404, content={"detail": NO_SUMMARY})
    target = next((x for x in fresh["landings"] if preview_id and x.get("id") == preview_id), None)
    if target is None:
        base = next((x.get("base") for x in fresh["landings"] if x.get("base")), "") or "your branch"
        return JSONResponse(status_code=409, content={"detail": L.CHANGED_SINCE.format(base=base),
                                                      "preview": fresh})
    if target.get("blocked"):
        return JSONResponse(status_code=409, content={"detail": target["blocked"], "preview": fresh})
    steps = [s for s in target["steps"] if s.get("ready")]
    if not steps:
        return JSONResponse(status_code=409, content={"detail": L.NOTHING_READY})
    busy = _busy_title(d, steps)
    if busy:
        return JSONResponse(status_code=409, content={"detail": SESSION_BUSY.format(title=busy)})
    if not _claim_repo(target["repo"]):
        return JSONResponse(status_code=409, content={"detail": L.ALREADY_RUNNING})
    thread_ids = [str(s["thread_id"]) for s in steps if s.get("thread_id")]
    for tid in thread_ids:
        _STARTING.add(tid)
        _GIT_ACTING.add(tid)
    rid = new_id("land")
    stop = threading.Event()
    with _GUARD:
        _ACTIVE[rid] = stop
    try:
        rec = await asyncio.to_thread(
            L.create_record, engine, id=rid, night=night, folder_key=target["folder_key"],
            folder=target.get("checkout") or target["folder"], repo=target["repo"], base=target["base"],
            base_before=target["base_sha"], preview_id=preview_id, command=target.get("command") or "",
            words=f"Merging {L._plural(len(steps), 'branch', 'branches')} into {target['base']}…",
        )
    except Exception:
        with _GUARD:
            _ACTIVE.pop(rid, None)
        _release_repo(target["repo"])
        for tid in thread_ids:
            _GIT_ACTING.discard(tid)
            _STARTING.discard(tid)
        raise
    task = asyncio.create_task(_run(d, rid, target, thread_ids, stop), name=f"ij-merge-all-{rid}")
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return JSONResponse(status_code=202, content={"merge": rec})


def record_view(engine: Any, record_id: str) -> "dict[str, Any] | None":
    """One merge's record with its Undo plan (git reads). BLOCKING."""
    rec = L.get_record(engine, record_id)
    if rec is None:
        return None
    rec = L.settle_stale(engine, rec, _active_ids(), git=True, record_session=lambda tid, ch: _locked_record(
        engine, tid, ch))
    out = L.view(rec)
    out["undo"] = L.undo_plan(rec) if rec.state == L.MERGED else {"how": "", "words": "", "button": ""}
    return out


async def undo(d: Any, record_id: str, how: str) -> Any:
    from .build import _STARTING
    from .build_git import _GIT_ACTING

    engine = d.platform.engine
    rec = await asyncio.to_thread(L.get_record, engine, record_id)
    if rec is None:
        return JSONResponse(status_code=404, content={"detail": NO_SUCH_MERGE})
    if rec.id in _active_ids():
        return JSONResponse(status_code=409, content={"detail": "This merge is still running."})
    steps = [s for s in L._loads(rec.steps_json, []) if isinstance(s, dict) and s.get("state") == L.STEP_MERGED]
    busy = _busy_title(d, steps)
    if busy:
        return JSONResponse(status_code=409, content={"detail": SESSION_BUSY.format(title=busy)
                                                      .replace("nothing was merged", "nothing was undone")
                                                      .replace("press Merge again", "press Undo again")})
    if not _claim_repo(rec.repo):
        return JSONResponse(status_code=409, content={"detail": L.ALREADY_RUNNING})
    thread_ids = [str(s.get("thread_id") or "") for s in steps if s.get("thread_id")]
    for tid in thread_ids:
        _STARTING.add(tid)
        _GIT_ACTING.add(tid)

    def run() -> dict[str, Any]:
        held = _locks(thread_ids)
        if held is None:
            raise B.GitRefused(LOCK_BUSY.replace("nothing was merged", "nothing was undone"), 409)
        try:
            done = L.undo(engine, d.platform.config.home, record_id, how=str(how or ""),
                          record_session=lambda tid, ch: record_session(engine, tid, ch))
            view = record_view(engine, record_id) or {}
            return {**view, "undo_commit": done.get("undo_commit", "")}
        finally:
            for h in reversed(held):
                h.release()

    try:
        return await asyncio.to_thread(run)
    except B.GitRefused as exc:
        content: dict[str, Any] = {"detail": str(exc)}
        if exc.details:
            content["details"] = exc.details
        return JSONResponse(status_code=exc.status, content=content)
    finally:
        _release_repo(rec.repo)
        for tid in thread_ids:
            _GIT_ACTING.discard(tid)
            _STARTING.discard(tid)


def register(app: FastAPI, d: Any) -> None:
    engine_of = lambda: d.platform.engine  # noqa: E731 — read live (tests swap the platform)

    @app.get("/build/night/summaries/{night}/land")
    async def night_land_preview(night: str) -> Any:
        """25-a: the preview of Merge all that passed (git reads only)."""
        out = await asyncio.to_thread(preview_view, engine_of(), night)
        if out is None:
            raise HTTPException(status_code=404, detail=NO_SUMMARY)
        out["emergency_stop"] = _emergency(d)
        return out

    @app.post("/build/night/summaries/{night}/land")
    async def night_land_start(night: str, body: LandBody) -> Any:
        """25-a: Merge all that passed — exactly the previewed list."""
        return await land(d, night, body.preview_id.strip())

    @app.get("/build/night/land/{record_id}")
    async def night_land_record(record_id: str) -> Any:
        """25-a: one merge's record and which Undo it gets (D-25.1)."""
        out = await asyncio.to_thread(record_view, engine_of(), record_id)
        if out is None:
            raise HTTPException(status_code=404, detail=NO_SUCH_MERGE)
        return out

    @app.post("/build/night/land/{record_id}/undo")
    async def night_land_undo(record_id: str, body: UndoBody) -> Any:
        """25-a: Undo a merge the way its plan said (D-25.1)."""
        return await undo(d, record_id, body.how.strip())


__all__ = ["facts_by_thread", "land", "night_land_view", "preview_view", "record_session", "record_view",
           "settle_kept",
           "register", "stop_all", "undo"]
