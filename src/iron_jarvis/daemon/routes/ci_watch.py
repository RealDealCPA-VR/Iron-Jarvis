"""THE PULL-REQUEST WATCH (v1.406.0 part 3): Iron Jarvis watches the checks
of a pull request a coding session opened, fixes a failure in that session,
has a CLOUD reviewer approve the fix, and only then pushes it.

The loop (``ci_loop``, the daemon lifespan's ``bg_tasks["ci_watch"]``, its
own outcome on ``loop_health["ci_watch"]``) runs a pass every :data:`TICK_S`
(and at once when a fix turn ends). A pass looks at every coding session that
opened a pull request (``setup.git.pr_url``, not merged, not removed, not
archived) and moves its watch (``harness/ci_fix.CiWatchRecord``) one step:

1. **watching** — when it is time (60 s while a check runs, then less often up
   to 10 minutes; at most 6 hours), ``gh pr view`` reads the checks. All
   passed: the watch rests (``passing``), and when the head is the fix Iron
   Jarvis pushed, the bell rings "the checks pass now" (one of the two NEWS
   kinds). A check failed (and none still runs): a fix is due.
2. **fix due** — when the session is FREE (no reply running — a running reply
   always blocks a new fix turn, and nothing is read from GitHub meanwhile),
   with Emergency stop off: the failed runs' logs are read (masked, scanned,
   fenced) and a coding turn starts IN THAT SESSION by itself
   (``start_build_turn(started_by="ci")`` — the session's own model, posture,
   account pin, model hold, Local only and trust; a hidden continuation
   question naming the failed checks, the log for the model only).
3. **fixing** — when that turn ended: a stopped or failed turn stops the
   watch; no change gives up; a Local only session, a low-trust turn or no
   connected reviewer WAITS FOR THE USER (nothing is pushed by itself);
   otherwise the change goes to the reviewer.
4. **reviewing** — the reviewer (``ci_reviewer``: Claude Code or Codex) reads
   the change as the v1.401.0 team review does (a read-only helper, its tools
   ``REVIEW_TOOLS`` only, bounded by ``finish.REVIEW_DEADLINE_S``, its verdict
   the LAST line of its report). APPROVED → Iron Jarvis commits (the night
   shift's screened commit) and PUSHES to the pull request's own ``ij/``
   branch without a card (the ONE exception; ``ci_fix.push_refusal``).
   NOT APPROVED → the fix is committed to a NEW side branch, never pushed, and
   the session works on what the reviewer found (the next hidden turn) until
   an approval lands it on the PR branch (a fast-forward) and pushes it.
   At most :data:`~iron_jarvis.harness.ci_fix.MAX_ROUNDS` rounds per failing
   streak; then it gives up (NEWS) and leaves everything on its branches.

Never: a push to the base branch, a force push, a history rewrite, a merge of
the pull request (that stays the user's Merge press), a fix while the user's
own reply runs, anything while Emergency stop is on (its fix turns are
stopped by ``night.stop_unattended_turns``; a review in flight by
:func:`stop_all`).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pydantic import Field as PydField

from ...harness import branches as B
from ...harness import ci_fix as CI
from ...harness import pr_comments as PC
from ...harness import sessions as S

log = logging.getLogger(__name__)

#: How often a pass runs (each watch keeps its own next read).
TICK_S = 20.0
#: Who starts a fix turn (``start_build_turn(started_by=)``; Emergency stop
#: covers it — ``harness/night.BY_CI``).
BY_CI = "ci"

#: The sentences a watch ends with (each a line on the session and a row in
#: the bell).
EMERGENCY_WORDS = (
    "Emergency stop was turned on, so Iron Jarvis stopped fixing this pull request's checks; "
    "nothing more was pushed."
)
LOCAL_ONLY_WORDS = (
    "This session is Local only, so no cloud reviewer can check the fix and nothing was pushed by "
    "itself. The fix is in the session's folder: look at it, then commit and push it yourself."
)
LOW_TRUST_WORDS = (
    "The fix turn read something flagged and went to low trust, so Iron Jarvis did not push it by "
    "itself. The fix is in the session's folder: look at it, then commit and push it yourself."
)
BUSY_WORDS = (
    "You started a reply while the fix was being reviewed, so Iron Jarvis did not commit or push "
    "it. The fix is in the session's folder: look at it, then commit and push it yourself."
)
NO_CHANGE_WORDS = (
    "The fix turn changed no files, so there was nothing to review or push. Iron Jarvis stopped "
    "fixing this pull request's checks."
)
STOPPED_FIX_WORDS = "The fix stopped before it finished, so nothing was reviewed or pushed."
#: v1.406.0 review: the user's own reply came after the fix turn — its
#: changes are the user's, and are never committed or pushed by themselves.
USER_REPLY_WORDS = (
    "You sent a message after the fix turn, so Iron Jarvis did not have the fix reviewed or push it. "
    "The fix is in the session's folder: look at it, then commit and push it yourself."
)
#: v1.406.0 review: the session changed while the fix was reviewed.
GONE_WORDS = "The session was archived or removed while the fix was reviewed, so nothing was pushed."
OFF_WORDS = ("Fixing a pull request's checks was turned off while the fix was reviewed, so nothing was "
             "pushed. The fix is in the session's folder.")
#: A Local only session's NEWS carries no words of its own to another
#: device (the night shift's rule).
LOCAL_ONLY_NOTICE = "A Local only coding session: open Iron Jarvis on this PC to see it."

#: v1.406.0 part 3 follow-ups: a step of the fix turn the night's guarded
#: posture carded and nobody (or the user) let run.
DECLINED_WORDS = (
    "A step of the fix needed your OK and was not done ({steps}), so Iron Jarvis did not have the fix "
    "reviewed or push it. The fix is in the session's folder: look at it, then finish it yourself."
)
#: Second review D1 (v1.513.0): the push step committed nothing new — the
#: pull request's branch still ends on the failing commit — so there is
#: nothing of this fix to push.
NOTHING_COMMITTED_WORDS = (
    "The approved fix left nothing git could commit, so the pull request's branch is still at the failing "
    "commit and nothing was pushed. The fix is in the session's folder: look at it, then commit and push it "
    "yourself."
)
#: …and the branch's tip is not a commit Iron Jarvis made for this fix.
NOT_THIS_FIX_WORDS = (
    "The pull request's branch does not end on the commit Iron Jarvis made for this fix, so nothing was "
    "pushed. Look at the branch, then push it yourself."
)
#: How often the push step looks at the Emergency stop switch while it runs.
STOP_POLL_S = 0.25

_WAKE: dict[str, Any] = {}
#: v1.406.0 part 3 follow-ups: the notes each fix turn's night wait wrote
#: (``NightTurn.on_note``): a card nobody answered, declined.
_DECLINED: dict[str, list[str]] = {}
#: The reviews in flight, by session (each a task on the daemon's loop).
_REVIEWS: dict[str, "asyncio.Task[Any]"] = {}
#: ONE watch step at a time per session.
_LOCKS: dict[str, asyncio.Lock] = {}


def wake_ci_loop() -> None:
    """Make the loop look now. Thread-safe; a no-op with no loop."""
    loop, event = _WAKE.get("loop"), _WAKE.get("event")
    if loop is None or event is None:
        return
    with contextlib.suppress(RuntimeError):
        if loop.is_running():
            loop.call_soon_threadsafe(event.set)


def _note_for(turn_id: str) -> Any:
    """The ``on_note`` a fix turn's night wait calls (any thread)."""

    def on_note(kind: str, text: str) -> None:
        if kind in ("declined", "unanswered"):
            _DECLINED.setdefault(turn_id, []).append(str(text or "")[:300])

    return on_note


def _lock_for(thread_id: str) -> asyncio.Lock:
    lock = _LOCKS.get(thread_id)
    if lock is None:
        lock = _LOCKS[thread_id] = asyncio.Lock()
    return lock


def _left_out_words(left: "list[dict[str, Any]]", *, committed: bool) -> str:
    """Second review D1 (v1.513.0): the screened commit left files out, so
    the fix is not pushed — which files, and why, in the night shift's words."""
    from ...harness import night_commit as NC

    said = NC.left_out_words(left, where="the session's folder")
    kept = ("The rest of the fix is committed on the session's branch, not pushed." if committed
            else "The fix held nothing else, so nothing was committed.")
    return ("Iron Jarvis's commit of the reviewed fix had to leave files out, so the fix was not pushed. "
            f"{said} {kept}")


def _not_done_words(fin: Any) -> str:
    """Second review D4 (v1.513.0): why a fix whose own finish check says it
    is not done waits for the user (``night.finish_needs_you``)."""
    from ...harness import night as N

    if N.finish_tests(fin) == "failed":  # as last seen, the night's one reading
        why = "The fix's own tests still fail"
    else:
        n = N.finish_unmet(fin)
        why = (f"{n} point{'s' if n != 1 else ''} of the request {'are' if n != 1 else 'is'} still open" if n
               else "The fix's own check says it is not finished")
    return (f"{why}, so Iron Jarvis did not have the fix reviewed, and nothing was pushed. The fix is in the "
            "session's folder: look at it, then finish it yourself.")


def autofix_on(config: Any) -> bool:
    """``ci_autofix`` (default ON): anything but a real ``False`` is on."""
    return getattr(config, "ci_autofix", True) is not False


def _emergency(d: Any) -> bool:
    from ...agents.orchestrator import emergency_stop_on

    return emergency_stop_on(getattr(d.platform, "config", None))


def _busy(d: Any, thread_id: str) -> bool:
    """A reply (or a git action, a Compact, a rewind) holds the session."""
    from .build import _COMPACTING, _STARTING, _running_turn

    return bool(thread_id in _STARTING or thread_id in _COMPACTING or _running_turn(d, thread_id))


def _git_locked(thread_id: str, fn: Any) -> Any:
    from .build_git import _locked

    return _locked(thread_id, fn)


# --------------------------------------------------------------------------- #
# words: the session's line and the bell's row
# --------------------------------------------------------------------------- #


def _headline(kind: str, *, reviewer: str = "", verdict: str = "") -> str:
    label = CI.reviewer_label(reviewer)
    return {
        CI.KIND_FAILED: "Checks failed on your pull request",
        CI.KIND_FIXING: "Fixing what the review found",
        CI.KIND_REVIEW: (f"{label} approved the check fix" if verdict == "good"
                         else f"{label} asked for changes to the check fix"),
        CI.KIND_PUSHED: "Pushed the reviewed fix to your pull request",
        CI.KIND_SIDE: "The check fix is on a side branch until it is approved",
        CI.KIND_ASKING: "The check fix waits for you",
        CI.KIND_STOPPED: "Stopped watching your pull request's checks",
        CI.KIND_GAVE_UP: "Iron Jarvis stopped fixing your pull request's checks",
        CI.KIND_PASSING: "Your pull request's checks pass now",
    }.get(kind, "Your pull request's checks")


async def _say(d: Any, thread_id: str, title: str, kind: str, words: str, *, round_: int = 0,
               reviewer: str = "", verdict: str = "", local_only: bool = False) -> None:
    """ONE step said: a line on the session (``ci: {kind, round}``) and a
    bell row (``build.ci_step``, quiet; ``build.ci_done`` for the two NEWS
    kinds). Never raises."""
    from ...core.events import EventType

    engine = d.platform.engine
    line = {"role": "assistant", "content": words, "ci": {"kind": kind, "round": int(round_)}}
    try:
        await asyncio.to_thread(S.append_lines, engine, thread_id, [line], title_from_first=False)
    except Exception:  # noqa: BLE001 — a line never stops the watch
        log.warning("writing a pull-request line on %s failed", thread_id, exc_info=True)
    news = kind in CI.NEWS_KINDS
    session_title = " ".join(str(title or "").split())[:120]
    body = LOCAL_ONLY_NOTICE if (news and local_only) else " · ".join(b for b in (session_title, words) if b)
    payload = {
        "thread_id": thread_id, "kind": kind,
        "title": _headline(kind, reviewer=reviewer, verdict=verdict),
        "body": body, "round": int(round_), "session_title": "" if (news and local_only) else session_title,
    }
    try:
        bus = getattr(d.platform, "event_bus", None)
        if bus is not None:
            await bus.publish(EventType.BUILD_CI_DONE if news else EventType.BUILD_CI_STEP, payload,
                              session_id=S.session_id_for(thread_id))
    except Exception:  # noqa: BLE001 — the record stands without its row
        log.warning("announcing a pull-request step on %s failed", thread_id, exc_info=True)


async def _end(d: Any, thread_id: str, title: str, state: str, kind: str, words: str, *,
               local_only: bool = False, **changes: Any) -> None:
    """The watch rests in ``state`` with ``words`` said."""
    await asyncio.to_thread(CI.save, d.platform.engine, thread_id, state=state, words=words, turn_id="",
                            due_text="", due_note="", **changes)
    await _say(d, thread_id, title, kind, words, local_only=local_only)


# --------------------------------------------------------------------------- #
# the review (the v1.401.0 team review's machinery, on a cloud reviewer)
# --------------------------------------------------------------------------- #


@dataclass
class ReviewJob:
    thread_id: str
    folder: str
    provider: str
    task: str
    #: v1.406.0 review: WHAT the reviewer read — the failing head the change
    #: is measured from, ``ci_fix.change_fingerprint`` of the worktree and its
    #: HEAD when the change was read. The push step refuses a worktree that
    #: no longer matches (a file changed or added, a commit made since).
    base_sha: str = ""
    fingerprint: str = ""
    head: str = ""


@dataclass
class ReviewResult:
    verdict: str  # good | bad | ""
    report: str = ""
    why: str = ""


async def run_review(d: Any, job: ReviewJob) -> ReviewResult:
    """The reviewer's run: a READ-ONLY helper of the session
    (``helpers.HelperParent(review_only=True)``: ``REVIEW_TOOLS`` and nothing
    else, the runtime arms nothing on top) on the reviewer's own model
    (``pinned``: its hold; its provider's normal account), in the session's
    folder, bounded by ``finish.REVIEW_DEADLINE_S``. The verdict is the LAST
    line of its report (``team_record.verdict_of``); a report the runtime
    wrote for a reviewer that ended silently judges nothing."""
    from ...agents.orchestrator import Orchestrator
    from ...agents.runtime import RUNTIME_WROTE_RESULT, AgentRuntime
    from ...agents.team_record import REVIEW_GOOD, verdict_of
    from ...agents.types import get_agent_definition
    from ...core.models import AgentType
    from ...harness import helpers as H
    from ...harness.finish import REVIEW_DEADLINE_S

    platform = d.platform
    parent = H.HelperParent(
        session_id=S.session_id_for(job.thread_id), folder=job.folder, provider=job.provider, model="",
        tools=tuple(H.REVIEW_TOOLS), review_only=True, own_model=True,
    )
    orch = Orchestrator(platform)
    session = await orch.create_session(
        job.task, AgentType.REVIEWER, provider=job.provider, model=None, workspace_root=job.folder,
        origin=H.HELPER_ORIGIN, agent_name=CI.REVIEWER_AGENT, options=parent.options(),
    )
    with H.pinned(parent):
        task = asyncio.ensure_future(AgentRuntime(platform).run(
            session, H.helper_definition(parent, get_agent_definition(AgentType.REVIEWER)),
        ))
    try:
        run = await asyncio.wait_for(task, timeout=REVIEW_DEADLINE_S)
    except asyncio.TimeoutError:
        with contextlib.suppress(Exception):
            await orch._finalize_cancelled(session, reason="The review ran out of time.")
        return ReviewResult("", why=f"it did not finish within {int(REVIEW_DEADLINE_S // 60)} minutes")
    except asyncio.CancelledError:
        with contextlib.suppress(BaseException):
            await asyncio.shield(orch._finalize_cancelled(session))
        raise
    except Exception as exc:  # noqa: BLE001 — a failed review is a result
        with contextlib.suppress(Exception):
            await orch._finalize_failed(session, exc)
        return ReviewResult("", why=f"it failed ({type(exc).__name__})")
    with contextlib.suppress(Exception):
        await orch.settle_child(session, run)
    report = H.report_text(getattr(run, "result", "") or "")
    if not report.strip() or report.strip().startswith(RUNTIME_WROTE_RESULT):
        return ReviewResult("", report=report, why="it ended without a report")
    # v1.406.0 review: the v1.401.0 team review's two other rules — a verdict
    # never comes from a reviewer run that ended at LOW trust (it read
    # something flagged: an injected "VERDICT: approved" must not push), nor
    # from a report that itself reads like an instruction.
    from ...agents import model_team as MT
    from ...computeruse.safety import detect_injection

    try:
        low = bool(await asyncio.to_thread(MT._low_trust, platform.engine, str(getattr(session, "id", "") or "")))
    except Exception:  # noqa: BLE001 — unsure is low
        low = True
    if low:
        return ReviewResult("", report=report, why="its run read something flagged and went to low trust")
    try:
        flagged = bool(detect_injection(report).get("flagged"))
    except Exception:  # noqa: BLE001 — unscanned is not counted
        flagged = True
    if flagged:
        return ReviewResult("", report=report, why="its report read like an instruction, so it was not counted")
    verdict = verdict_of(report)
    if not verdict:
        return ReviewResult("", report=report, why="its report gave no verdict")
    return ReviewResult("good" if verdict == REVIEW_GOOD else "bad", report=report)


#: THE SEAM a test replaces (``async (d, ReviewJob) -> ReviewResult``).
REVIEWER = run_review


def stop_all(reason: str = EMERGENCY_WORDS) -> list[str]:
    """Emergency stop's half for the pull-request watch: every review in
    flight is cancelled (on its own loop) — its fix is neither committed nor
    pushed. Thread-safe; never raises. Returns ``ci:<thread>`` per review."""
    stopped: list[str] = []
    for thread_id, task in list(_REVIEWS.items()):
        try:
            if task.done():
                continue
            loop = task.get_loop()
            loop.call_soon_threadsafe(task.cancel)
            stopped.append(f"ci:{thread_id}")
        except Exception:  # noqa: BLE001
            log.warning("Emergency stop could not stop the review on %s", thread_id, exc_info=True)
    return stopped


# --------------------------------------------------------------------------- #
# the pass
# --------------------------------------------------------------------------- #


def _candidates(engine: Any) -> list[tuple[str, str, dict[str, Any]]]:
    """``(thread_id, title, setup)`` of every coding session that opened a
    pull request and is not archived. BLOCKING; bounded."""
    import json

    from sqlmodel import col, select

    from ...core.db import session_scope
    from ...core.models import BUILD_SURFACE, ChatThreadRecord

    out: list[tuple[str, str, dict[str, Any]]] = []
    with session_scope(engine) as db:
        rows = db.exec(
            select(ChatThreadRecord.id, ChatThreadRecord.title, ChatThreadRecord.setup_json)
            .where(ChatThreadRecord.surface == BUILD_SURFACE)
            .where(col(ChatThreadRecord.archived_at).is_(None))
            .where(col(ChatThreadRecord.setup_json).contains('"pr_url"'))
            .limit(200)
        ).all()
    for tid, title, raw in rows:
        try:
            setup = json.loads(raw or "{}")
        except (TypeError, ValueError):
            continue
        if isinstance(setup, dict):
            out.append((str(tid), str(title or ""), setup))
    return out


def _pass_lock(platform: Any) -> asyncio.Lock:
    lock = getattr(platform, "_ci_pass_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        with contextlib.suppress(Exception):
            platform._ci_pass_lock = lock
    return lock


async def ci_pass(d: Any) -> dict[str, list[str]]:
    """ONE pass (serialized per platform). Returns what it did, by session.

    26-a: a pull request's REVIEW COMMENTS are read in the same pass on their
    own clock (:func:`_read_comments`) — also while ``ci_autofix`` is off:
    that setting is about fixing failed checks, and reading the comments
    changes nothing anywhere."""
    async with _pass_lock(d.platform):
        out: dict[str, list[str]] = {"read": [], "fixing": [], "reviewing": [], "pushed": [], "ended": [],
                                     "comments": []}
        fixing = autofix_on(getattr(d.platform, "config", None))
        rows = await asyncio.to_thread(_candidates, d.platform.engine)
        for thread_id, title, setup in rows:
            if thread_id in _REVIEWS and not _REVIEWS[thread_id].done():
                continue
            lock = _lock_for(thread_id)
            if lock.locked():
                continue
            async with lock:
                try:
                    await _tend(d, thread_id, title, setup, out, fixing=fixing)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 — one session never stops the watch
                    log.warning("the pull-request watch of %s failed", thread_id, exc_info=True)
        return out


async def _tend(d: Any, thread_id: str, title: str, setup: dict[str, Any], out: dict[str, list[str]],
                *, fixing: bool = True) -> None:
    engine = d.platform.engine
    facts = B.facts_of(setup)
    if not facts or not facts.get("pr_url") or facts.get("merged_at") or facts.get("removed_at"):
        return
    rec = await asyncio.to_thread(CI.ensure, engine, thread_id, facts)
    # 26-a: an Answer these turn that ended with no one left to settle it (a
    # restart) is settled from the record here; then the comments are read.
    # Nothing is read while a fix turn runs (or its review), nor of a pull
    # request the checks watch already saw merged or closed. A fault here
    # never stops the checks' own watch below.
    try:
        await _settle_answers(d, thread_id, facts)
        if rec["state"] not in (CI.FIXING, CI.REVIEWING, CI.CLOSED):
            await _read_comments(d, thread_id, title, facts, rec, out, local_only=setup.get("local_only") is True)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — the checks are watched all the same
        log.warning("reading the review comments of %s failed", thread_id, exc_info=True)
    if not fixing:
        # 26-a: the record may exist only for the comments' clock. While
        # fixing is off the checks are not watched, so their streak has not
        # begun (``watched_too_long`` would count from the day the record was
        # made): it begins when fixing is turned on, just below.
        if rec["state"] == CI.WATCHING and rec.get("watch_since"):
            await asyncio.to_thread(CI.save, engine, thread_id, watch_since=None)
        return
    if rec["state"] == CI.WATCHING and not rec.get("watch_since"):
        await asyncio.to_thread(CI.save, engine, thread_id, watch_since=CI.later(0))
        rec = await asyncio.to_thread(CI.get, engine, thread_id) or rec
    pushed_at = str(facts.get("pushed_at") or "")
    if pushed_at != str(rec.get("seen_pushed_at") or "") and rec["state"] not in (CI.FIXING, CI.REVIEWING):
        # A push of the branch the USER made: a new head, a new streak.
        await asyncio.to_thread(
            CI.save, engine, thread_id, seen_pushed_at=pushed_at, state=CI.WATCHING, rounds=0, base_sha="",
            pushed_sha="", turn_id="", due_text="", due_note="", own_commits=[], interval_s=CI.POLL_FIRST_S,
            next_check_at=None, watch_since=CI.later(0), read_fails=0,
        )
        rec = await asyncio.to_thread(CI.get, engine, thread_id) or rec
    state = rec["state"]
    local_only = setup.get("local_only") is True
    if state == CI.WATCHING:
        await _check(d, thread_id, title, facts, rec, out, local_only=local_only)
    elif state == CI.FIX_DUE:
        await _start_fix(d, thread_id, title, facts, setup, rec, out)
    elif state in (CI.FIXING, CI.REVIEWING):
        # REVIEWING with no review in flight: a restart cut it off — the fix
        # turn has ended, so it is judged (and reviewed) again.
        await _after_fix(d, thread_id, title, facts, setup, rec, out)


async def _check(d: Any, thread_id: str, title: str, facts: dict[str, str], rec: dict[str, Any],
                 out: dict[str, list[str]], *, local_only: bool) -> None:
    engine = d.platform.engine
    if not CI.due(rec) or _emergency(d):
        return
    try:
        pr = await asyncio.to_thread(_git_locked, thread_id, lambda: CI.read_pr(facts))
    except B.GitRefused as exc:
        fails = int(rec.get("read_fails") or 0) + 1
        if fails >= CI.READ_FAILS_MAX:
            await _end(d, thread_id, title, CI.STOPPED, CI.KIND_STOPPED,
                       f"The pull request's checks could not be read {fails} times in a row, so Iron Jarvis "
                       f"stopped watching them: {exc}", read_fails=fails, local_only=local_only)
            return
        wait = CI.next_interval(rec.get("interval_s"))
        await asyncio.to_thread(CI.save, engine, thread_id, read_fails=fails, interval_s=wait,
                                next_check_at=CI.later(wait))
        return
    out["read"].append(thread_id)
    if pr.state in ("merged", "closed"):
        await asyncio.to_thread(CI.save, engine, thread_id, state=CI.CLOSED, read_fails=0)
        return
    if not pr.settled:
        if CI.watched_too_long(rec):
            await _end(d, thread_id, title, CI.STOPPED, CI.KIND_STOPPED,
                       f"The pull request's checks were still running after {int(CI.WATCH_MAX_S // 3600)} "
                       "hours, so Iron Jarvis stopped watching them.", local_only=local_only)
            return
        wait = CI.next_interval(rec.get("interval_s")) if rec.get("next_check_at") else CI.POLL_FIRST_S
        await asyncio.to_thread(CI.save, engine, thread_id, read_fails=0, interval_s=wait,
                                next_check_at=CI.later(wait))
        return
    failed = CI.failed_names(pr)
    if not failed:
        ours = bool(rec.get("pushed_sha")) and pr.head_sha.startswith(str(rec["pushed_sha"]))
        await asyncio.to_thread(CI.save, engine, thread_id, state=CI.PASSING, rounds=0, read_fails=0,
                                failed=[], side_branch="", own_commits=[], words=pr.words())
        if ours:
            await _say(d, thread_id, title, CI.KIND_PASSING,
                       "The checks pass now with the reviewed fix Iron Jarvis pushed.", local_only=local_only)
        return
    if int(rec.get("rounds") or 0) >= CI.MAX_ROUNDS:
        await _end(d, thread_id, title, CI.GAVE_UP, CI.KIND_GAVE_UP,
                   f"The checks still fail after {CI.MAX_ROUNDS} rounds of fixes, so Iron Jarvis stopped "
                   "fixing them. Nothing more was pushed; look at the pull request yourself.",
                   failed=failed, local_only=local_only)
        return
    await asyncio.to_thread(CI.save, engine, thread_id, expect_state=CI.WATCHING, state=CI.FIX_DUE,
                            base_sha=pr.head_sha, failed=failed, read_fails=0, due_text="", due_note="")


async def _start_fix(d: Any, thread_id: str, title: str, facts: dict[str, str], setup: dict[str, Any],
                     rec: dict[str, Any], out: dict[str, list[str]]) -> None:
    from ...harness import night as N
    from .build import UNATTENDED_REFUSAL, SessionBusy, start_build_turn

    engine = d.platform.engine
    platform = d.platform
    local_only = setup.get("local_only") is True
    if _emergency(d):
        return
    if _busy(d, thread_id):
        # A running reply ALWAYS blocks a new fix turn — and nothing is read
        # from GitHub meanwhile: the fix waits for the session to be free.
        return
    round_ = int(rec.get("rounds") or 0) + 1
    if round_ > CI.MAX_ROUNDS:
        await _end(d, thread_id, title, CI.GAVE_UP, CI.KIND_GAVE_UP,
                   f"Iron Jarvis stopped after {CI.MAX_ROUNDS} rounds of fixes; nothing more was pushed.",
                   local_only=local_only)
        return
    session_id = S.session_id_for(thread_id)
    bus = getattr(platform, "event_bus", None)
    revising = bool(rec.get("due_note"))
    low_trust = ""
    if revising:
        text, note = str(rec.get("due_text") or CI.revise_text()), str(rec["due_note"])
        low_trust = str(rec.get("due_low_trust") or "")
        kind = CI.KIND_FIXING
        words = (f"Working on what the review found (round {round_} of {CI.MAX_ROUNDS}). The fix stays on "
                 f"the side branch {rec.get('side_branch') or ''} until a review approves it.").replace("  ", " ")
    else:
        try:
            pr = await asyncio.to_thread(_git_locked, thread_id, lambda: CI.read_pr(facts))
        except B.GitRefused:
            await asyncio.to_thread(CI.save, engine, thread_id, state=CI.WATCHING,
                                    next_check_at=CI.later(CI.POLL_FIRST_S))
            return
        if pr.state != "open" or pr.head_sha != rec.get("base_sha") or not pr.failed or not pr.settled:
            # The pull request moved on (a new push, its checks re-run): watch it.
            await asyncio.to_thread(CI.save, engine, thread_id, state=CI.WATCHING, next_check_at=None)
            return
        logs = await asyncio.to_thread(_git_locked, thread_id, lambda: CI.failed_logs(facts, pr))
        names = CI.failed_names(pr)
        text = CI.fix_text(names)
        flagged: list[str] = []
        note = CI.fix_note(logs, event_bus=bus, session_id=session_id, flagged=flagged)
        low_trust = flagged[0] if flagged else ""
        kind = CI.KIND_FAILED
        number = f" (#{facts['pr_number']})" if facts.get("pr_number") else ""
        words = (f"Checks failed on this session's pull request{number}: {CI.names_words(names)}. Iron Jarvis "
                 f"is fixing them by itself (round {round_} of {CI.MAX_ROUNDS}); a reviewer checks the fix "
                 "before anything is pushed.")
    turn_id = "bt_" + secrets.token_hex(8)
    claimed = await asyncio.to_thread(CI.save, engine, thread_id, expect_state=CI.FIX_DUE, state=CI.FIXING,
                                      turn_id=turn_id, rounds=round_, due_text="", due_note="", due_low_trust="",
                                      words=words)
    if not claimed:
        return
    await _say(d, thread_id, title, kind, words, round_=round_)
    try:
        lg = await start_build_turn(d, thread_id, text, turn_id, continuation=True,
                                    started_by=N.BY_CI, model_note=note, low_trust=low_trust,
                                    on_night_note=_note_for(turn_id))
    except SessionBusy:
        # The user's own message won the race: the fix waits for it to end.
        await asyncio.to_thread(CI.save, engine, thread_id, state=CI.FIX_DUE, turn_id="", rounds=round_ - 1,
                                due_text=text if revising else "", due_note=note if revising else "",
                                due_low_trust=low_trust if revising else "")
        return
    except HTTPException as exc:
        if exc.detail == UNATTENDED_REFUSAL:
            await _end(d, thread_id, title, CI.STOPPED, CI.KIND_STOPPED, EMERGENCY_WORDS, local_only=local_only)
        else:
            await _end(d, thread_id, title, CI.STOPPED, CI.KIND_STOPPED,
                       f"The fix could not start: {exc.detail}", local_only=local_only)
        return
    out["fixing"].append(thread_id)
    with contextlib.suppress(Exception):
        lg.task.add_done_callback(lambda _t: wake_ci_loop())


async def _after_fix(d: Any, thread_id: str, title: str, facts: dict[str, str], setup: dict[str, Any],
                     rec: dict[str, Any], out: dict[str, list[str]]) -> None:
    from ...harness import night as N

    engine = d.platform.engine
    platform = d.platform
    local_only = setup.get("local_only") is True
    if _busy(d, thread_id):
        return  # its fix turn (or a reply the user started after it) still runs
    if _emergency(d):
        await _end(d, thread_id, title, CI.STOPPED, CI.KIND_STOPPED, EMERGENCY_WORDS, local_only=local_only)
        return
    row = await asyncio.to_thread(S.get_row, engine, thread_id)
    if row is None:
        await asyncio.to_thread(CI.forget, engine, thread_id)
        return
    messages = await asyncio.to_thread(S._messages, row)
    turn_id = str(rec.get("turn_id") or "")
    line = N.last_reply_line(messages, {turn_id}) if turn_id else None
    if line is None or line.get("interrupted") or line.get("error"):
        why = STOPPED_FIX_WORDS
        if isinstance(line, dict) and line.get("error") and not line.get("interrupted"):
            why = f"The fix turn failed ({line.get('errorTitle') or line.get('error')}), so nothing was pushed."
        await _end(d, thread_id, title, CI.STOPPED, CI.KIND_STOPPED, why, local_only=local_only)
        return
    # v1.406.0 review: a reply of the user's after the fix turn — its changes
    # are in the folder too, and the user's own work is never pushed by itself.
    at = next((i for i in range(len(messages) - 1, -1, -1) if messages[i] is line), -1)
    if at >= 0 and any(isinstance(m, dict) and m.get("role") == "user" for m in messages[at + 1:]):
        await _end(d, thread_id, title, CI.ASKING, CI.KIND_ASKING, USER_REPLY_WORDS, local_only=local_only)
        return
    # v1.406.0 part 3 follow-ups: a step the guarded posture carded and that
    # was not done (nobody answered within the night's card wait, or the user
    # declined it) — the fix is not what was asked for: the user decides.
    declined = _DECLINED.pop(turn_id, [])
    denied = [str(t) for t in (line.get("deniedTools") or []) if str(t).strip()]
    if declined or denied:
        steps = "; ".join(dict.fromkeys(declined)) or ", ".join(dict.fromkeys(denied))
        await _end(d, thread_id, title, CI.ASKING, CI.KIND_ASKING, DECLINED_WORDS.format(steps=steps[:400]),
                   local_only=local_only)
        return
    base_sha = str(rec.get("base_sha") or "")
    try:
        # Second review D9 (v1.512.0): a worktree deleted in Explorer, or on
        # a drive that is away, ends the watch in words (as ``_check`` and
        # ``_start_fix`` do) — it stayed "fixing" or "reviewing" for ever,
        # every pass logging the same refusal and nothing said.
        wt = B._live(facts)

        def read_change() -> tuple[str, str, str, "list[str] | None"]:
            # ONE locked read: the diff the reviewer gets, the fingerprint the
            # push must still match, HEAD then, and the files that are not text.
            return (CI.change_for_review(wt, base_sha), CI.change_fingerprint(wt, base_sha),
                    CI.ref_sha(wt, "HEAD"), CI.binary_changes(wt, base_sha))

        change, fingerprint, head, binaries = await asyncio.to_thread(_git_locked, thread_id, read_change)
    except B.GitRefused as exc:
        await _end(d, thread_id, title, CI.STOPPED, CI.KIND_STOPPED,
                   f"Iron Jarvis stopped fixing this pull request's checks, and nothing was pushed: {exc}",
                   local_only=local_only)
        return
    if not change.strip():
        await _end(d, thread_id, title, CI.GAVE_UP, CI.KIND_GAVE_UP, NO_CHANGE_WORDS, local_only=local_only)
        return
    # Second review D4 (v1.513.0): the fix turn's OWN finish check says the
    # work is not done (its tests still fail, points of the request are still
    # open) — the reviewer cannot run tests, so such a fix is never reviewed
    # or pushed by itself (the night shift's reading of the same record).
    # A line with no finish record goes on as before.
    if N.finish_needs_you(line.get("finish")):
        await _end(d, thread_id, title, CI.ASKING, CI.KIND_ASKING, _not_done_words(line.get("finish")),
                   local_only=local_only)
        return
    if binaries is None or binaries:
        # v1.406.0 part 3 follow-ups: the reviewer cannot read a file that is
        # not text — such a fix is never pushed by itself.
        files = ", ".join(binaries[:5]) if binaries else "git could not tell which files are text"
        await _end(d, thread_id, title, CI.ASKING, CI.KIND_ASKING, CI.BINARY_WORDS.format(files=files),
                   local_only=local_only)
        return
    if local_only:
        await _end(d, thread_id, title, CI.ASKING, CI.KIND_ASKING, LOCAL_ONLY_WORDS, local_only=True)
        return
    if str(line.get("trust") or "full").lower() == "low":
        await _end(d, thread_id, title, CI.ASKING, CI.KIND_ASKING, LOW_TRUST_WORDS)
        return
    reviewer, why = CI.pick_reviewer(platform, getattr(platform.config, "ci_reviewer", "auto"),
                                     str(setup.get("provider") or ""))
    if not reviewer:
        await _end(d, thread_id, title, CI.ASKING, CI.KIND_ASKING,
                   why + " The fix is in the session's folder: look at it, then commit and push it yourself.")
        return
    claimed = await asyncio.to_thread(CI.save, engine, thread_id, expect_state=rec["state"], state=CI.REVIEWING,
                                      reviewer=reviewer)
    if not claimed:
        return
    request = (CI.fix_text(rec.get("failed") or []) + " The checks ran on GitHub; the fix must make them "
               "pass without breaking anything else.")
    task = CI.review_task(request, str(line.get("content") or ""), change,
                          event_bus=getattr(platform, "event_bus", None), session_id=S.session_id_for(thread_id))
    job = ReviewJob(thread_id=thread_id, folder=str(wt), provider=reviewer, task=task, base_sha=base_sha,
                    fingerprint=fingerprint, head=head)
    out["reviewing"].append(thread_id)
    _REVIEWS[thread_id] = asyncio.create_task(_review_and_land(d, thread_id, title, facts, job))


async def _review_and_land(d: Any, thread_id: str, title: str, facts: dict[str, str], job: ReviewJob) -> None:
    engine = d.platform.engine
    label = CI.reviewer_label(job.provider)
    try:
        async with _lock_for(thread_id):
            rec = await asyncio.to_thread(CI.get, engine, thread_id) or {}
            round_ = int(rec.get("rounds") or 0)
            try:
                result = await REVIEWER(d, job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — a failed review is a result
                log.warning("the check-fix review on %s failed", thread_id, exc_info=True)
                result = ReviewResult("", why=f"it failed ({type(exc).__name__})")
            if _emergency(d):
                await _end(d, thread_id, title, CI.STOPPED, CI.KIND_STOPPED, EMERGENCY_WORDS)
                return
            if result.verdict not in ("good", "bad"):
                await _end(d, thread_id, title, CI.GAVE_UP, CI.KIND_GAVE_UP,
                           f"{label}'s review of the fix could not finish ({result.why or 'no verdict'}), so "
                           "nothing was pushed. The fix is in the session's folder: look at it, then commit "
                           "and push it yourself.")
                return
            approved = result.verdict == "good"
            await _say(d, thread_id, title, CI.KIND_REVIEW,
                       (f"{label} reviewed the fix and approved it." if approved
                        else f"{label} reviewed the fix and asked for changes."),
                       round_=round_, reviewer=job.provider, verdict=result.verdict)
            if approved:
                await _land_and_push(d, thread_id, title, facts, rec, job)
            else:
                await _keep_on_side(d, thread_id, title, facts, rec, result.report)
    except asyncio.CancelledError:
        # Emergency stop (``stop_all``) — wherever it landed (waiting for the
        # session's lock, in the review, before the push): the watch rests
        # STOPPED, so a later pass never reviews it again by itself and
        # nothing is pushed. A shutdown keeps the watch where it was: after
        # the restart the ended fix is reviewed again (its push re-checks the
        # pull request's head first).
        if _emergency(d):
            with contextlib.suppress(BaseException):
                cur = await asyncio.shield(asyncio.to_thread(CI.get, engine, thread_id)) or {}
                # v1.406.0 part 3 follow-ups: a push step that was cut off
                # wrote what REALLY happened (read back from the remote):
                # never overwritten with "stopped".
                if cur.get("state") == CI.REVIEWING:
                    await asyncio.shield(_end(d, thread_id, title, CI.STOPPED, CI.KIND_STOPPED, EMERGENCY_WORDS))
        raise
    except Exception as exc:  # noqa: BLE001 — the watch rests, said once
        # Second review D9 (v1.512.0): an unexpected error after the review
        # (keeping the fix on its side branch, recording the push) left the
        # watch REVIEWING, and the next pass paid for ANOTHER review of the
        # same fix with no round counted. It rests in "gave up" now — only
        # while the record still says reviewing (never over what the step
        # already wrote), and without saying whether anything was pushed.
        log.warning("the check-fix step after the review on %s failed", thread_id, exc_info=True)
        from ...core.failures import plain_failure

        with contextlib.suppress(Exception):
            cur = await asyncio.to_thread(CI.get, engine, thread_id) or {}
            if cur.get("state") == CI.REVIEWING:
                await _end(d, thread_id, title, CI.GAVE_UP, CI.KIND_GAVE_UP, plain_failure(
                    "Something went wrong after the fix was reviewed, so Iron Jarvis stopped fixing this pull "
                    "request's checks. Look at the pull request and the session's branch before you go on.", exc))
    finally:
        if _REVIEWS.get(thread_id) is asyncio.current_task():
            _REVIEWS.pop(thread_id, None)
        wake_ci_loop()


def _commit_message(rec: dict[str, Any]) -> str:
    names = [str(x) for x in rec.get("failed") or []]
    return (f"Fix failing checks: {CI.names_words(names)}\n\n"
            "Reviewed before it was pushed. Made by Iron Jarvis.")


async def _land_and_push(d: Any, thread_id: str, title: str, facts: dict[str, str], rec: dict[str, Any],
                         job: "ReviewJob | None" = None) -> None:
    """APPROVED: commit (screened), land a side branch, push to the PR's own
    branch — the session HELD meanwhile (no turn starts).

    v1.406.0 review: only what the reviewer READ is pushed — the worktree must
    still match ``job``'s fingerprint and HEAD right before the commit and
    the same fingerprint right after it — and only while the session is still
    there, not archived, not Local only, with the setting still on."""
    from .build import _STARTING
    from .build_git import _GIT_ACTING

    engine = d.platform.engine
    if _busy(d, thread_id):
        await _end(d, thread_id, title, CI.ASKING, CI.KIND_ASKING, BUSY_WORDS)
        return
    side = str(rec.get("side_branch") or "")
    base_sha = str(rec.get("base_sha") or "")
    config = getattr(d.platform, "config", None)
    # v1.406.0 part 3 follow-ups: Emergency stop's hold on the commit (its
    # hooks) and the push — and whether the push itself had begun.
    kill = CI.Kill()
    box: dict[str, Any] = {"pushing": False}
    own = [str(x) for x in rec.get("own_commits") or []]

    def run() -> dict[str, Any]:
        from ...agents.orchestrator import emergency_stop_on
        from .build import _setup_of

        row = S.get_row(engine, thread_id)
        if row is None or getattr(row, "archived_at", None) is not None:
            raise B.GitRefused(GONE_WORDS, 409)
        setup_now = _setup_of(row)
        if isinstance(setup_now, dict) and setup_now.get("local_only") is True:
            raise B.GitRefused(LOCAL_ONLY_WORDS, 409)
        if not autofix_on(config):
            raise B.GitRefused(OFF_WORDS, 409)
        wt = B._live(facts)
        reviewed = job.fingerprint if job is not None else ""
        if CI.changed_since_review(wt, base_sha, reviewed, job.head if job is not None else ""):
            raise B.GitRefused(CI.CHANGED_SINCE_REVIEW, 409)
        on = CI.head_branch(wt)
        on_side = bool(side) and on == side
        if emergency_stop_on(config) or kill.stop:
            raise B.GitRefused(EMERGENCY_WORDS, 409)
        left: list[dict[str, str]] = []
        committed = CI.commit_fix(facts, side if on_side else facts["branch"], _commit_message(rec),
                                  on_spawn=kill.on_spawn, left_out=left)
        if committed:
            own.append(CI.ref_sha(wt, "HEAD"))
        # Second review D1 (v1.513.0): the screen left a file out (a secret's
        # name or shape, a huge file, a dependency folder) — the commit is not
        # what the reviewer read, and CI would fail on the same head again.
        # Never pushed; the user is told which file and why.
        if left:
            raise CI.AskUser(_left_out_words(left, committed=bool(committed)), 409)
        if CI.changed_since_review(wt, base_sha, reviewed):
            raise B.GitRefused(CI.CHANGED_SINCE_REVIEW + " (The commit was made and kept, not pushed.)", 409)
        if on_side:
            CI.land_side(facts, side)
        # v1.406.0 part 3 follow-ups: ONLY Iron Jarvis's own screened commits
        # between the failing head and what is pushed — any other commit (the
        # fix turn's model, a hook, the user) waits for the user, named.
        foreign = CI.foreign_commits(wt, base_sha, own, f"refs/heads/{facts['branch']}")
        if foreign is None or foreign:
            raise CI.AskUser(CI.FOREIGN_COMMITS_WORDS.format(
                commits=", ".join(foreign[:5]) if foreign else "git could not list them"), 409)
        # Second review D1 (v1.513.0): "Pushed the reviewed fix" is said only
        # of a commit made for THIS fix — a branch still at the failing head
        # (nothing new committed) or ending on any other commit is not pushed.
        tip = CI.ref_sha(wt, f"refs/heads/{facts['branch']}")
        if not tip or (base_sha and tip == base_sha):
            raise CI.AskUser(NOTHING_COMMITTED_WORDS, 409)
        if tip.lower() not in {str(o).lower() for o in own if o}:
            raise CI.AskUser(NOT_THIS_FIX_WORDS, 409)
        # …and nothing that is not text (the reviewer could not read it).
        binaries = CI.binary_changes(wt, base_sha)
        if binaries is None or binaries:
            raise CI.AskUser(CI.BINARY_WORDS.format(
                files=", ".join(binaries[:5]) if binaries else "git could not tell which files are text"), 409)
        pr = CI.read_pr(facts)
        if base_sha and pr.head_sha and pr.head_sha != base_sha:
            raise B.GitRefused("The pull request changed while the fix was reviewed, so nothing was pushed; "
                               "the fix is committed on the session's branch.", 409)
        if emergency_stop_on(config) or kill.stop:
            raise B.GitRefused(EMERGENCY_WORDS, 409)
        box["pushing"] = True
        done = CI.push_fix(facts, pr, on_spawn=kill.on_spawn)
        done["committed"] = committed
        return done

    _STARTING.add(thread_id)
    _GIT_ACTING.add(thread_id)
    try:
        push_job = asyncio.ensure_future(asyncio.to_thread(_git_locked, thread_id, run))
        try:
            while True:
                finished, _ = await asyncio.wait({push_job}, timeout=STOP_POLL_S)
                if finished:
                    break
                if _emergency(d) and not kill.stop:
                    await asyncio.to_thread(kill.kill)
        except asyncio.CancelledError:
            # Emergency stop's ``stop_all`` (or a shutdown): the commit, its
            # hooks and the push end NOW; then what really happened is read
            # back and written — never "stopped" over a push that went out.
            with contextlib.suppress(BaseException):
                await asyncio.shield(asyncio.to_thread(kill.kill))
            with contextlib.suppress(BaseException):
                await asyncio.shield(push_job)
            with contextlib.suppress(BaseException):
                await asyncio.shield(_settle_push(d, thread_id, title, facts, rec, push_job, box, kill, own))
            raise
    finally:
        _GIT_ACTING.discard(thread_id)
        _STARTING.discard(thread_id)
    await _settle_push(d, thread_id, title, facts, rec, push_job, box, kill, own)


async def _settle_push(d: Any, thread_id: str, title: str, facts: dict[str, str], rec: dict[str, Any],
                       job: "asyncio.Future[Any]", box: dict[str, Any], kill: Any, own: "list[str]") -> None:
    """What the push step REALLY did, written once: pushed (its own answer,
    or — a push that was cut off or failed — the remote's ref read back
    equal to the fix's commit), waiting for the user, stopped, or given up."""
    exc = job.exception() if job.done() and not job.cancelled() else None
    done = job.result() if job.done() and not job.cancelled() and exc is None else None
    if done is not None:
        await _record_pushed(d, thread_id, title, facts, rec, str(done.get("sha") or ""),
                             str(done.get("remote") or ""))
        return
    if isinstance(exc, CI.AskUser):
        await _end(d, thread_id, title, CI.ASKING, CI.KIND_ASKING, str(exc))
        return
    if box.get("pushing"):
        # The push had begun: the remote says whether it went out.
        def read_back() -> "tuple[str | None, str]":
            wt = B._live(facts)
            return CI.remote_branch_sha(wt, facts), CI.ref_sha(wt, f"refs/heads/{facts['branch']}")

        try:
            remote, tip = await asyncio.to_thread(read_back)
        except Exception:  # noqa: BLE001 — unreadable is said as unreadable
            remote, tip = None, ""
        base = str(rec.get("base_sha") or "")
        if remote and tip and remote == tip and tip != base:
            await _record_pushed(d, thread_id, title, facts, rec, tip, "", late=True)
            return
        if remote is None:
            await _end(d, thread_id, title, CI.STOPPED, CI.KIND_STOPPED,
                       "The push was cut off and Iron Jarvis could not read the remote afterwards, so it cannot "
                       "say whether the fix went out. Look at the pull request; the fix is committed on the "
                       "session's branch.")
            return
        stopped_by = "Emergency stop stopped the push" if kill.stop else "The push did not go through"
        await _end(d, thread_id, title, CI.STOPPED if kill.stop else CI.GAVE_UP,
                   CI.KIND_STOPPED if kill.stop else CI.KIND_GAVE_UP,
                   f"{stopped_by}: the pull request's branch still has commit {remote[:12] or 'none'}, and the "
                   "fix is committed on the session's branch, not pushed.")
        return
    if kill.stop or (isinstance(exc, B.GitRefused) and str(exc) == EMERGENCY_WORDS):
        await _end(d, thread_id, title, CI.STOPPED, CI.KIND_STOPPED, EMERGENCY_WORDS)
        return
    if exc is not None and not isinstance(exc, B.GitRefused):
        log.warning("the push step of %s failed", thread_id, exc_info=exc)
    words = str(exc) if isinstance(exc, B.GitRefused) else "The push step failed, so nothing was pushed."
    await _end(d, thread_id, title, CI.GAVE_UP, CI.KIND_GAVE_UP, words)


async def _record_pushed(d: Any, thread_id: str, title: str, facts: dict[str, str], rec: dict[str, Any],
                         sha: str, remote: str, *, late: bool = False) -> None:
    from ...core.ids import utcnow

    engine = d.platform.engine
    pushed_at = utcnow().isoformat()

    def record() -> None:
        row = S.get_row(engine, thread_id)
        from .build import _setup_of

        fresh = (B.facts_of(_setup_of(row)) if row is not None else None) or facts
        S.update(engine, thread_id, setup={B.SETUP_KEY: {**fresh, "remote": remote or fresh.get(
            "remote", ""), "pushed_at": pushed_at}})

    await asyncio.to_thread(record)
    words = (f"Pushed the reviewed fix to {facts['branch']} (commit {sha[:12]}). Watching the pull request's "
             "checks again.")
    if late:
        words = (f"The push was cut off, but it had already gone out: the reviewed fix is on {facts['branch']} "
                 f"(commit {sha[:12]}). Watching the pull request's checks again.")
    await asyncio.to_thread(
        CI.save, engine, thread_id, state=CI.WATCHING, pushed_sha=sha, seen_pushed_at=pushed_at, side_branch="",
        base_sha="", own_commits=[], interval_s=CI.POLL_FIRST_S, next_check_at=CI.later(CI.POLL_FIRST_S),
        watch_since=CI.later(0), read_fails=0, words=words,
    )
    await _say(d, thread_id, title, CI.KIND_PUSHED, words, round_=int(rec.get("rounds") or 0))


async def _keep_on_side(d: Any, thread_id: str, title: str, facts: dict[str, str], rec: dict[str, Any],
                        report: str) -> None:
    """NOT APPROVED: the fix committed on a side branch (made from the PR
    branch, which does not move), never pushed; the next round works on what
    the reviewer found — or, past the last round, Iron Jarvis gives up."""
    from .build import _STARTING
    from .build_git import _GIT_ACTING

    engine = d.platform.engine
    platform = d.platform
    if _busy(d, thread_id):
        await _end(d, thread_id, title, CI.ASKING, CI.KIND_ASKING, BUSY_WORDS)
        return
    known_side = str(rec.get("side_branch") or "")
    base_sha = str(rec.get("base_sha") or "")

    own = [str(x) for x in rec.get("own_commits") or []]

    def run() -> str:
        wt = B._live(facts)
        on = CI.head_branch(wt)
        if on == facts["branch"]:
            if base_sha and CI.ref_sha(wt, f"refs/heads/{facts['branch']}") != base_sha:
                raise CI.AskUser(
                    "Commits were made on the pull request's branch during the fix, so Iron Jarvis left them "
                    "where they are; nothing was pushed.", 409)
            side = CI.side_name(facts, wt)
            CI.move_to_side(facts, side)
        elif on and on == known_side:
            side = on
        else:
            raise B.GitRefused("The session's worktree is on another branch now, so the fix was left as it "
                               "is; nothing was pushed.", 409)
        if CI.commit_fix(facts, side, _commit_message(rec) + "\n\nNot approved yet: kept off the pull request."):
            own.append(CI.ref_sha(wt, "HEAD"))
        return side

    _STARTING.add(thread_id)
    _GIT_ACTING.add(thread_id)
    try:
        side = await asyncio.to_thread(_git_locked, thread_id, run)
    except CI.AskUser as exc:
        await _end(d, thread_id, title, CI.ASKING, CI.KIND_ASKING, str(exc))
        return
    except B.GitRefused as exc:
        await _end(d, thread_id, title, CI.GAVE_UP, CI.KIND_GAVE_UP, str(exc))
        return
    finally:
        _GIT_ACTING.discard(thread_id)
        _STARTING.discard(thread_id)
    rounds = int(rec.get("rounds") or 0)
    label = CI.reviewer_label(str(rec.get("reviewer") or ""))
    if rounds >= CI.MAX_ROUNDS:
        await _end(d, thread_id, title, CI.GAVE_UP, CI.KIND_GAVE_UP,
                   f"Iron Jarvis stopped after {CI.MAX_ROUNDS} rounds: {label} did not approve the fix. The "
                   f"work is on the side branch {side}; none of it was pushed to the pull request.",
                   side_branch=side)
        return
    flagged: list[str] = []
    note = CI.revise_note(report, event_bus=getattr(platform, "event_bus", None),
                          session_id=S.session_id_for(thread_id), flagged=flagged)
    words = (f"The fix is kept on the side branch {side} and was not pushed to the pull request. Working on "
             f"what {label} found next (round {rounds + 1} of {CI.MAX_ROUNDS}).")
    await asyncio.to_thread(CI.save, engine, thread_id, state=CI.FIX_DUE, side_branch=side, own_commits=own,
                            due_text=CI.revise_text(), due_note=note, due_low_trust=flagged[0] if flagged else "",
                            turn_id="", words=words)
    await _say(d, thread_id, title, CI.KIND_SIDE, words, round_=rounds)


# --------------------------------------------------------------------------- #
# 26-a: review comments arrive in the session
# --------------------------------------------------------------------------- #

#: The bell row's headline.
COMMENTS_HEADLINE = "Review comments arrived"
#: The settling tasks in flight (a task the loop holds only weakly).
_SETTLING: "set[asyncio.Task[Any]]" = set()
#: The Answer these turns being started: claimed, not yet listed as running
#: (the transcript has no reply of theirs yet). Never settled meanwhile.
_ANSWER_STARTING: "set[str]" = set()
#: Said once when reading rests because GitHub kept refusing (it is tried
#: again a day later, and a day apart after that, until a read works).
COMMENTS_FAILED_WORDS = (
    "The review comments on this session's pull request could not be read {n} times in a row, so Iron Jarvis "
    "will try again in a day: {why}"
)


def _arrived_line(new: "list[dict[str, Any]]") -> dict[str, Any]:
    """The session's line: Iron Jarvis's own words only (a count — the
    authors' names ride the structured field, so nothing a person wrote on
    GitHub reaches a later turn's history as words)."""
    n = len(new)
    authors = list(dict.fromkeys(str(r.get("author") or "someone") for r in new))[:10]
    return {
        "role": "assistant",
        "content": f"{n} review comment{'s' if n != 1 else ''} arrived on this session's pull request.",
        "prComments": {"ids": [str(r["id"]) for r in new][: PC.MAX_KEPT], "n": n, "authors": authors},
    }


async def _say_comments(d: Any, thread_id: str, title: str, new: "list[dict[str, Any]]", *, bell: bool,
                        local_only: bool) -> None:
    """New comments said: a line on the session, and — at most once a day per
    pull request, never after "Stop telling me about comments here" — a QUIET
    bell row (``build.pr_comments``). Never raises."""
    from ...core.events import EventType

    engine = d.platform.engine
    try:
        await asyncio.to_thread(S.append_lines, engine, thread_id, [_arrived_line(new)], title_from_first=False)
    except Exception:  # noqa: BLE001 — a line never stops the watch
        log.warning("writing a review-comments line on %s failed", thread_id, exc_info=True)
    if not bell:
        return
    session_title = " ".join(str(title or "").split())[:120]
    words = PC.arrived_words(new)
    payload = {
        "thread_id": thread_id, "count": len(new), "title": COMMENTS_HEADLINE,
        # A Local only session's news carries no words of its own.
        "body": LOCAL_ONLY_NOTICE if local_only else " · ".join(b for b in (session_title, words) if b),
        "session_title": "" if local_only else session_title,
    }
    try:
        bus = getattr(d.platform, "event_bus", None)
        if bus is not None:
            await bus.publish(EventType.BUILD_PR_COMMENTS, payload, session_id=S.session_id_for(thread_id))
    except Exception:  # noqa: BLE001 — the record stands without its row
        log.warning("announcing review comments on %s failed", thread_id, exc_info=True)


async def _read_comments(d: Any, thread_id: str, title: str, facts: dict[str, str], rec: dict[str, Any],
                         out: dict[str, list[str]], *, local_only: bool) -> None:
    """26-a: read the pull request's review comments when their clock says so
    (first :data:`~iron_jarvis.harness.pr_comments.READ_FIRST_S` after the
    watch first sees the pull request, then less often up to an hour, back to
    the first wait after a new comment; ended by merged or closed, or by
    ``READ_FOR_DAYS`` with no new comment and no push from Iron Jarvis (said
    once; a push after that reads again); ``READ_FAILS_MAX`` failed reads in a
    row rest it for ``READ_AGAIN_S``, then it reads again). NOTHING is read
    under Emergency stop or while the session is busy (a reply of the
    user's, a fix turn): the comments wait for it."""
    engine = d.platform.engine
    if _emergency(d) or _busy(d, thread_id):
        return
    at = PC.now()
    if PC.first_wait(rec):
        await asyncio.to_thread(CI.save, engine, thread_id, comments_next_at=PC.later(PC.READ_FIRST_S, at),
                                comments_interval_s=PC.READ_FIRST_S, comments_since=at, comments_read_fails=0,
                                comments_done="")
        return
    # A push of the branch from Iron Jarvis is new work on the pull request:
    # the quiet days count from it, and after they ended it reads again.
    pushed_at = facts.get("pushed_at")
    if PC.read_again(rec, at, pushed_at):
        await asyncio.to_thread(CI.save, engine, thread_id, comments_done="", comments_since=at,
                                comments_next_at=PC.later(PC.READ_FIRST_S, at),
                                comments_interval_s=PC.READ_FIRST_S, comments_read_fails=0)
        return
    if not PC.due(rec, at):
        return
    if PC.read_too_long(rec, at, pushed_at):
        # Ended, and SAID once (``due`` is never true again until a push):
        # a review that comes later must not go unread with nothing said.
        await asyncio.to_thread(CI.save, engine, thread_id, comments_done=PC.DONE_QUIET, comments_next_at=None)
        line = {"role": "assistant", "content": PC.QUIET_WORDS,
                "prComments": {"ids": [], "n": 0, "authors": [], "stopped": True}}
        with contextlib.suppress(Exception):
            await asyncio.to_thread(S.append_lines, engine, thread_id, [line], title_from_first=False)
        return
    try:
        got = await asyncio.to_thread(_git_locked, thread_id, lambda: PC.read(facts))
    except B.GitRefused as exc:
        fails = int(rec.get("comments_read_fails") or 0) + 1
        if fails >= PC.READ_FAILS_MAX:
            # Rests, never ends: tried again a day later (and a day apart
            # after that) until a read works. Said ONCE, when it first rests.
            resting = str(rec.get("comments_done") or "") == PC.DONE_FAILED
            await asyncio.to_thread(CI.save, engine, thread_id, comments_done=PC.DONE_FAILED,
                                    comments_read_fails=fails, comments_next_at=PC.later(PC.READ_AGAIN_S, at))
            if resting:
                return
            line = {"role": "assistant", "content": COMMENTS_FAILED_WORDS.format(n=fails, why=exc),
                    "prComments": {"ids": [], "n": 0, "authors": [], "stopped": True}}
            with contextlib.suppress(Exception):
                await asyncio.to_thread(S.append_lines, engine, thread_id, [line], title_from_first=False)
            return
        wait = PC.interval_after(rec.get("comments_interval_s"), fresh=False)
        await asyncio.to_thread(CI.save, engine, thread_id, comments_read_fails=fails, comments_interval_s=wait,
                                comments_next_at=PC.later(wait, at))
        return
    out.setdefault("comments", []).append(thread_id)
    if got.state in (PC.DONE_MERGED, PC.DONE_CLOSED):
        await asyncio.to_thread(CI.save, engine, thread_id, comments_done=got.state, comments_next_at=None,
                                comments_read_fails=0)
        return
    if _busy(d, thread_id):
        # A reply the user started while GitHub was being read (up to three
        # gh calls): the line must never land between its question and its
        # reply. Nothing is kept and the clock is not moved, so the next pass
        # the session is free reads again.
        return
    pr_url = str(facts.get("pr_url") or "")
    # Off the loop: every kept comment's words are made plain and masked
    # (every rule), which takes seconds on a busy pull request.
    rows, cut = await asyncio.to_thread(PC.rows_from, thread_id, pr_url, got.comments)
    new = await asyncio.to_thread(PC.store, engine, thread_id, pr_url, rows, cut=cut, at=at)
    wait = PC.interval_after(rec.get("comments_interval_s"), fresh=bool(new))
    # A read that works ends a rest after failed reads ("" = it goes on).
    changes: dict[str, Any] = {"comments_read_fails": 0, "comments_interval_s": wait,
                               "comments_next_at": PC.later(wait, at), "comments_done": ""}
    # "Stop telling me about comments here" stops the BELL ROW (``may_tell``).
    # The line still comes: it is where the comments are, and answered.
    bell = bool(new) and PC.may_tell(rec, at)
    if new:
        changes["comments_since"] = at
    if bell:
        changes["comments_told_at"] = at
    await asyncio.to_thread(CI.save, engine, thread_id, **changes)
    if new:
        await _say_comments(d, thread_id, title, new, bell=bell, local_only=local_only)


async def _settle_answer(d: Any, thread_id: str, turn_id: str, facts: "dict[str, str] | None" = None) -> None:
    """An Answer these turn ended: each of its comments is settled FROM THE
    RECORD (``pr_comments.judge``). A turn still running waits. Never
    raises."""
    from ...harness import night as N
    from .build import _running_turn, _setup_of

    engine = d.platform.engine
    try:
        # Still starting (between the press's claim and the turn being
        # listed), or running: there is no reply to settle from yet.
        if turn_id in _ANSWER_STARTING or _running_turn(d, thread_id) == turn_id:
            return
        row = await asyncio.to_thread(S.get_row, engine, thread_id)
        if row is None:
            return
        if facts is None:
            facts = B.facts_of(_setup_of(row)) or {}
        root = str(facts.get("worktree") or getattr(row, "folder", "") or "")
        messages = await asyncio.to_thread(S._messages, row)
        line = N.last_reply_line(messages, {turn_id})
        ended = line is not None and not line.get("interrupted") and not line.get("error")
        await asyncio.to_thread(PC.judge, engine, thread_id, turn_id, root, ended=ended)
    except Exception:  # noqa: BLE001 — the next pass settles it
        log.warning("settling the answered review comments of %s failed", thread_id, exc_info=True)


async def _settle_answers(d: Any, thread_id: str, facts: dict[str, str]) -> None:
    turns = await asyncio.to_thread(PC.answering_turns, d.platform.engine, thread_id)
    for turn_id in turns:
        await _settle_answer(d, thread_id, turn_id, facts)


def _comment_view(row: dict[str, Any], root: str) -> dict[str, Any]:
    """One comment as the screen reads it (its words are the record's: plain,
    masked, cut)."""
    import os

    keep = ("id", "kind", "author", "author_kind", "ticked", "path", "line", "start_line", "side", "outdated",
            "in_reply_to", "body", "quote", "url", "state", "answer_files", "github_created_at")
    view = {k: row.get(k) for k in keep}
    view["place"] = PC.place_words(row)
    # Where the Changes tab pins it: the file's full path in the session's
    # worktree (the review's own spelling of a path).
    view["abs"] = os.path.normpath(os.path.join(root, row["path"])) if root and row.get("path") else ""
    return view


def _checks_never_read(rec: dict[str, Any]) -> bool:
    """26-a: the watch's checks have not been read in this streak (no next
    look set, no fix round): what a record made for the review comments while
    fixing checks is off looks like. A read of the checks always sets the next
    look or moves the state on."""
    return rec.get("state") == CI.WATCHING and not int(rec.get("rounds") or 0) and not rec.get("next_check_at")


class PrAnswerBody(BaseModel):
    ids: list[str] = PydField(default_factory=list, max_length=200)


class PrAsideBody(BaseModel):
    aside: bool = True


class PrQuietBody(BaseModel):
    quiet: bool = True


def _register_comments(app: FastAPI, d: Any) -> None:
    """The routes of 26-a. Nothing here calls GitHub: the comments are the
    record the watcher keeps, and an Answer these turn changes only the
    session's own folder."""
    from fastapi.responses import JSONResponse

    async def _session(thread_id: str) -> tuple[Any, dict[str, str]]:
        from ...core.models import is_build_thread
        from .build import _setup_of

        row = await asyncio.to_thread(S.get_row, d.platform.engine, thread_id)
        if row is None or not is_build_thread(row):
            raise HTTPException(status_code=404, detail="no such coding session")
        return row, B.facts_of(_setup_of(row)) or {}

    @app.get("/build/sessions/{thread_id}/pr-comments")
    async def pr_comments_view(thread_id: str) -> dict[str, Any]:
        """The review comments Iron Jarvis read from the session's pull
        request, oldest first, and how the reading stands. No GitHub call."""
        row, facts = await _session(thread_id)
        engine = d.platform.engine
        pr_url = str(facts.get("pr_url") or "")
        rows = await asyncio.to_thread(PC.listing, engine, thread_id, pr_url) if pr_url else []
        rec = await asyncio.to_thread(CI.get, engine, thread_id) if pr_url else None
        root = str(facts.get("worktree") or getattr(row, "folder", "") or "")
        return {
            "pr_url": pr_url,
            "comments": [_comment_view(r, root) for r in rows],
            "quiet": bool((rec or {}).get("comments_quiet")),
            "done": str((rec or {}).get("comments_done") or ""),
            "next_read_at": (rec or {}).get("comments_next_at"),
            "max_answer": PC.MAX_ANSWER,
        }

    @app.post("/build/sessions/{thread_id}/pr-comments/answer")
    async def pr_comments_answer(thread_id: str, body: PrAnswerBody):
        """ANSWER THESE: one coding turn in this session, started by the
        person's press (their posture, model, account and Local only), whose
        question is Iron Jarvis's own sentence and whose model input holds
        the comments only inside the fence (``pr_comments.block``). A comment
        the scan flagged starts it at LOW trust. Answers ``{turn_id}``; the
        screen follows the turn as it follows any."""
        from .build import RUNNING_SENTENCE, SessionBusy, _running_turn, start_build_turn

        ids = list(dict.fromkeys(str(i).strip() for i in body.ids if str(i or "").strip()))
        if not ids:
            raise HTTPException(status_code=400, detail=PC.NOTHING_TO_ANSWER)
        if len(ids) > PC.MAX_ANSWER:
            raise HTTPException(status_code=400, detail=PC.TOO_MANY.format(n=PC.MAX_ANSWER))
        row, facts = await _session(thread_id)
        engine = d.platform.engine
        rows = await asyncio.to_thread(PC.get_rows, engine, thread_id, ids)
        if len(rows) != len(ids) or any(r.get("pr_url") != facts.get("pr_url") for r in rows):
            raise HTTPException(status_code=404, detail=PC.NOT_HERE)
        turn_id = "bt_" + secrets.token_hex(8)

        async def give_back() -> None:
            await asyncio.to_thread(PC.set_state, engine, thread_id, ids, PC.NEW, turn_id="", expect=(PC.ANSWERING,))

        # Listed as STARTING from before the claim until the turn is listed as
        # running (or refused): a watcher pass in between finds no reply of
        # it yet, and must not give its comments back (``_settle_answer``).
        _ANSWER_STARTING.add(turn_id)
        try:
            # All or none, compare-and-set: a second press (another window)
            # never answers a comment that is being answered, nor one that
            # was answered or set aside after that window's card was drawn.
            if not await asyncio.to_thread(PC.claim, engine, thread_id, ids, turn_id):
                now_rows = await asyncio.to_thread(PC.get_rows, engine, thread_id, ids)
                busy = any(r.get("state") == PC.ANSWERING for r in now_rows)
                raise HTTPException(status_code=409, detail=PC.ALREADY_ANSWERING if busy else PC.NOT_NEW)
            bus = getattr(d.platform, "event_bus", None)
            try:
                note, flagged = await asyncio.to_thread(PC.block, rows, event_bus=bus,
                                                        session_id=S.session_id_for(thread_id))
            except Exception:
                await give_back()
                raise
            try:
                lg = await start_build_turn(d, thread_id, PC.answer_text(len(ids)), turn_id, model_note=note,
                                            low_trust=PC.FLAGGED_TRUST if flagged else "")
            except SessionBusy:
                await give_back()
                return JSONResponse(status_code=409,
                                    content={"detail": RUNNING_SENTENCE, "turn_id": _running_turn(d, thread_id)})
            except Exception:
                await give_back()
                raise
        finally:
            _ANSWER_STARTING.discard(turn_id)
        loop = asyncio.get_running_loop()

        def _settle_soon() -> None:
            task = asyncio.ensure_future(_settle_answer(d, thread_id, turn_id, facts))
            _SETTLING.add(task)
            task.add_done_callback(_SETTLING.discard)

        def _ended(_t: Any) -> None:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(_settle_soon)

        with contextlib.suppress(Exception):
            lg.task.add_done_callback(_ended)
        return {"turn_id": turn_id, "answering": ids, "low_trust": bool(flagged)}

    @app.post("/build/sessions/{thread_id}/pr-comments/{comment_id}/set-aside")
    async def pr_comment_set_aside(thread_id: str, comment_id: str, body: PrAsideBody | None = None):
        """Set a comment aside. ``aside: false`` is the card's Bring back: a
        comment set aside, or one answered with no change to its file, is new
        again (offered to Answer these and pinned on its line); an answered
        one stays answered. Only the record changes; GitHub is not told."""
        await _session(thread_id)
        aside = True if body is None else body.aside
        moved = await asyncio.to_thread(
            PC.set_state, d.platform.engine, thread_id, [comment_id], PC.SET_ASIDE if aside else PC.NEW,
            turn_id=None if aside else "",
            expect=(PC.NEW, PC.UNCHANGED, PC.ANSWERED) if aside else (PC.SET_ASIDE, PC.UNCHANGED),
        )
        if not moved:
            rows = await asyncio.to_thread(PC.get_rows, d.platform.engine, thread_id, [comment_id])
            if not rows:
                raise HTTPException(status_code=404, detail=PC.NOT_HERE)
        return {"id": comment_id, "state": PC.SET_ASIDE if aside else PC.NEW, "changed": bool(moved)}

    @app.post("/build/sessions/{thread_id}/pr-comments/quiet")
    async def pr_comments_quiet(thread_id: str, body: PrQuietBody):
        """"Stop telling me about comments here" (``quiet: true``) and its
        Undo (``quiet: false``): no bell row for this pull request's new
        comments; they still arrive in the session (its line), where they
        are answered."""
        _row, facts = await _session(thread_id)
        if not facts.get("pr_url"):
            raise HTTPException(status_code=409, detail="This session has no pull request.")
        was = await asyncio.to_thread(set_quiet, d.platform.engine, thread_id, facts, body.quiet)
        return {"quiet": bool(body.quiet), "was": was}


def set_quiet(engine: Any, thread_id: str, facts: dict[str, str], quiet: bool) -> bool:
    """Turn "Stop telling me about comments here" on or off for the session's
    pull request; returns what it was. BLOCKING."""
    rec = CI.ensure(engine, thread_id, facts)
    was = bool(rec.get("comments_quiet"))
    CI.save(engine, thread_id, comments_quiet=bool(quiet))
    return was


async def ci_loop(d: Any, on_tick: Any) -> None:
    """The lifespan loop: a pass every :data:`TICK_S` (and at once when a
    fix turn or a review ends), each reporting ITS OWN outcome."""
    loop = asyncio.get_running_loop()
    event = asyncio.Event()
    _WAKE.update(loop=loop, event=event)
    try:
        while True:
            event.clear()
            try:
                await ci_pass(d)
                on_tick(True, None)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — a failed pass is a row, not a crash
                log.warning("pull-request watch pass failed", exc_info=True)
                on_tick(False, exc)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(event.wait(), timeout=TICK_S)
    finally:
        if _WAKE.get("event") is event:
            _WAKE.clear()
        for task in list(_REVIEWS.values()):
            if not task.done():
                task.cancel()


# --------------------------------------------------------------------------- #
# the route: where a session's watch stands
# --------------------------------------------------------------------------- #


def register(app: FastAPI, d: Any) -> None:
    @app.get("/build/sessions/{thread_id}/ci")
    async def ci_view(thread_id: str) -> dict[str, Any]:
        """Where the session's pull-request watch stands (no GitHub call)."""
        rec = await asyncio.to_thread(CI.get, d.platform.engine, thread_id)
        on = autofix_on(getattr(d.platform, "config", None))
        if rec is None or (not on and _checks_never_read(rec)):
            # 26-a: while fixing checks is off the record may exist only for
            # the review comments' clock. Its checks were never read, so the
            # watch of them says nothing: no line, as before that record.
            return {"watch": None, "on": on}
        keep = ("state", "rounds", "side_branch", "failed", "reviewer", "words", "next_check_at", "pr_url")
        view = {k: rec.get(k) for k in keep}
        view["max_rounds"] = CI.MAX_ROUNDS
        return {"watch": view, "on": on}

    _register_comments(app, d)


__all__ = [
    "BY_CI", "EMERGENCY_WORDS", "LOCAL_ONLY_WORDS", "REVIEWER", "ReviewJob", "ReviewResult", "TICK_S",
    "ci_loop", "ci_pass", "register", "run_review", "stop_all", "wake_ci_loop",
]
