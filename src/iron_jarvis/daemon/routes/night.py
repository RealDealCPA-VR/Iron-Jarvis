"""NIGHT SHIFT — the Tonight list and the night loop (v1.398.0, phase N1 of
``docs/NIGHT-SHIFT-PLAN.md``; the rules live in ``harness/night``).

ROUTES (Coding sessions › Tonight):

* ``GET /build/night`` — ``{tasks, window, limits, emergency_stop}``.
* ``POST /build/night {folder, words, provider?, model?, team?, try_on?}`` —
  queue one task LAST (201). Refused in words before anything is kept: a
  folder that cannot be used (``fs_policy.root_problem``) or that is not in
  a git repository (a night task always works on its own branch), a model
  not in the model menu, "try on" with fewer than 2 or more than 3 models,
  "the team" with no team, a model AND "try on", "the team" AND "try on".
* ``PATCH /build/night/{id}`` — partial edit of a QUEUED task (the same
  checks); ``DELETE /build/night/{id}`` — remove one that is not running;
  ``POST /build/night/{id}/move {direction: up|down}``;
  ``POST /build/night/{id}/cancel`` — a queued task never starts (stopped);
  a running one's turn is stopped (it settles as stopped).

THE LOOP (:func:`night_loop`, a lifespan task on the DAEMON's loop —
``bg_tasks["night_shift"]``, ``loop_health["night_shift"]``; never a
scheduler thread, so a turn never dies with a closing loop, the v1.231.0
lesson): every :data:`TICK_S` and whenever a night turn ends, ONE pass
(:func:`night_pass`) first SETTLES the running tasks (a turn that ended →
its state from its reply line, ``night.outcome_of``), then — only inside the
night window, only with Emergency stop off — starts queued tasks in their
order, at most ``night_max_at_once`` at once and never two in one folder,
each holding one ``max_concurrent_sessions`` slot (``Orchestrator.
try_hold_slot``) while it runs. Each starts as a NEW coding session on its
own branch through the ONE create path (``routes/build.create_session_row``)
and its first turn through ``start_build_turn(started_by="night")`` (or the
attempts' start for "try on") — the night posture and the night wait come
with that (``harness/night``). The clock is ``harness/night.CLOCK``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ...harness import night as N
from ...harness import sessions as S

log = logging.getLogger(__name__)

#: The loop's own pace (it also wakes when a night turn ends or the list
#: changes).
TICK_S = 30.0
#: A running task whose turn is still going this long past its time limit is
#: stopped by the loop (the reply cap ends it at a step boundary first; this
#: is the backstop for a step that never returns).
OVERRUN_GRACE_S = 15 * 60.0
#: A task marked running whose session was never made (Iron Jarvis stopped
#: in between) is given up after this long.
NO_SESSION_GIVE_UP_S = 5 * 60.0

#: What a night task with "the team" tells its model (the question line
#: shows the user's words alone).
TEAM_NOTE = (
    "Use your model team for this task: hand pieces of the work to its members by role "
    "(read, code, review, test) with your delegate tool where that helps, and check what they hand back."
)

NOT_A_REPO_NIGHT = (
    "A night task works on its own branch, so its folder must be in a git repository with at "
    "least one commit: {why}"
)
MODEL_UNKNOWN = "That model is not in your model menu: {name}."
TRY_ON_COUNT = "Try it on 2 or 3 models (you picked {n})."
NO_TEAM = "You have no model team yet. Add members in Settings › Models › Model team first."
MODEL_AND_TRY_ON = "Pick one model, or try it on several, not both."
TEAM_AND_TRY_ON = "Use the team, or try it on several models, not both."
MENU_UNREADABLE = "The model menu could not be read, so the model was not checked; try again."

_WAKE: dict[str, Any] = {}
#: Tasks this process is starting right now (their session is being made).
_STARTING_TASKS: set[str] = set()


def wake_night_loop() -> None:
    """Make the night loop look now. Thread-safe; a no-op with no loop."""
    loop, event = _WAKE.get("loop"), _WAKE.get("event")
    if loop is None or event is None:
        return
    try:
        if loop.is_running():
            loop.call_soon_threadsafe(event.set)
    except RuntimeError:
        pass


def _orchestrator(d: Any) -> Any:
    return getattr(d, "orchestrator", None) or getattr(getattr(d, "platform", None), "orchestrator", None)


def _slot_key(task_id: str) -> str:
    return f"night:{task_id}"


def _hold(d: Any, task_id: str) -> bool:
    orch = _orchestrator(d)
    hold = getattr(orch, "try_hold_slot", None)
    if not callable(hold):
        return True
    try:
        return bool(hold(_slot_key(task_id)))
    except Exception:  # noqa: BLE001 — an unreadable governor never stops the night
        return True


def _release(d: Any, task_id: str) -> None:
    orch = _orchestrator(d)
    release = getattr(orch, "release_slot", None)
    if callable(release):
        with contextlib.suppress(Exception):
            release(_slot_key(task_id))


def _keep_held(d: Any, task_id: str) -> None:
    """A task already running keeps its slot (after a restart nothing holds
    it yet): taken whatever the count says."""
    orch = _orchestrator(d)
    governed = getattr(orch, "_governed", None)
    lock = getattr(orch, "_gov_lock", None)
    if governed is None or lock is None:
        return
    with lock:
        governed.add(_slot_key(task_id))


def _emergency(d: Any) -> bool:
    from ...agents.orchestrator import emergency_stop_on

    return emergency_stop_on(getattr(d.platform, "config", None))


def _main_state(git: dict[str, Any], folder: str) -> dict[str, str]:
    """The user's main checkout now (``harness/night_guard.snapshot``), with
    the ``.venv`` this branch borrows. BLOCKING; never raises."""
    from ...harness import night_guard as G
    from ...harness.main_env import find as find_main_env

    borrowed = find_main_env(git.get("checkout", ""), folder, git.get("repo", ""), git.get("worktree", ""))
    return G.snapshot(git.get("main", "") or git.get("repo", ""), borrowed.venv if borrowed else "")


def _keep_main_before(engine: Any, task_id: str, git: dict[str, Any], folder: str) -> None:
    from ...harness import night_guard as G

    N.set_main_before(engine, task_id, G.dumps(_main_state(git, folder)))


def _main_moved(engine: Any, task_id: str, git: dict[str, Any], folder: str) -> "list[str] | None":
    """What moved in the user's main checkout since the task started, or None
    when it cannot be said. BLOCKING; never raises."""
    from ...harness import night_guard as G

    before = G.loads(N.main_before(engine, task_id))
    if before is None:
        return None
    return G.changes(before, _main_state(git, folder))


def _night_pair(platform: Any, task: dict[str, Any]) -> "tuple[str, str]":
    """The model a night task's session runs on: its own pick, else the
    app's default (what the session's first turn holds)."""
    cfg = getattr(platform, "config", None)
    provider = str(task.get("provider") or "") or str(getattr(cfg, "default_provider", "") or "")
    model = str(task.get("model") or "") if task.get("provider") else str(getattr(cfg, "default_model", "") or "")
    return provider, model


def pay_problem(platform: Any, provider: str, model: str) -> str:
    """v1.398.0 review: a night task never SPENDS pay-per-use money beyond
    the user's limit (the plan: "Pay-per-use never goes above your limit
    (default $0)"; decision 4's "spending" floor). A model billed per use —
    an API key, a proxy name that goes to a cloud service, or Auto, which may
    pick one — runs a night task only while the model team's monthly
    pay-per-use limit has room; the sentence when it has none, "" otherwise.
    BLOCKING (this month's spend); never raises (refused when unsure — the
    honest side for money)."""
    from ...agents import model_team as MT

    try:
        if not provider or MT.cost_type(platform, provider, model) != MT.COST_PAY:
            return ""
        try:
            cap = max(0.0, float(getattr(platform.config, "team_paid_cap_usd", 0.0) or 0.0))
        except (TypeError, ValueError):
            cap = 0.0
        name = f"{provider} · {model}" if model else provider
        if cap <= 0:
            return (f"{name} is billed per use (or may be), and your pay-per-use limit is $0 a month "
                    f"({MT.PAY_PLACE}), so a night task does not run on it. Pick a model on your plan "
                    "or on this PC, or raise the limit there.")
        spend = MT.paid_spend_this_month(platform)
        if spend >= cap:
            return (f"{name} is billed per use, and this month's pay-per-use spend (${spend:.2f}) has "
                    f"reached your ${cap:.2f} limit ({MT.PAY_PLACE}), so a night task does not run on it.")
        return ""
    except Exception:  # noqa: BLE001 — unsure: refused
        log.debug("night pay check failed", exc_info=True)
        return "The cost of this task's model could not be checked, so it did not start."


def pay_room(platform: Any, provider: str, model: str) -> "float | None":
    """v1.398.0 review: what is left of the model team's monthly pay-per-use
    limit for a night turn on a model billed per use (its reply's dollar
    cap), or None when the model is not billed per use. 0.0 when there is no
    room (or it cannot be read). BLOCKING; never raises."""
    from ...agents import model_team as MT

    try:
        if not provider or MT.cost_type(platform, provider, model) != MT.COST_PAY:
            return None
        cap = max(0.0, float(getattr(platform.config, "team_paid_cap_usd", 0.0) or 0.0))
        return max(0.0, cap - MT.paid_spend_this_month(platform))
    except Exception:  # noqa: BLE001 — unsure: no room
        return 0.0


def title_of(words: str) -> str:
    first = next((line.strip() for line in str(words or "").splitlines() if line.strip()), "")
    return (first[:77] + "…") if len(first) > 80 else (first or "Night task")


# --------------------------------------------------------------------------- #
# settle
# --------------------------------------------------------------------------- #


def _kept_threads(engine: Any) -> set[str]:
    from ...harness import reply_record as RR

    try:
        return {str(r.get("thread_id") or "") for r in RR.kept(engine) if not r.get("waiting")}
    except Exception:  # noqa: BLE001
        return set()


def _naive(moment: Any):
    from datetime import timezone

    if moment is None:
        return None
    if getattr(moment, "tzinfo", None) is not None:
        return moment.astimezone(timezone.utc).replace(tzinfo=None)
    return moment


def _parse(value: Any):
    from datetime import datetime

    try:
        return _naive(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except (TypeError, ValueError):
        return None


def _cost_kind(platform: Any) -> Any:
    """``(provider, model) -> "free" | "plan" | "pay"`` — the model team's
    own classification (``agents/model_team.cost_type``); "" when unsure."""
    from ...agents import model_team as MT

    def kind(provider: str, model: str) -> str:
        if not provider:
            return ""
        try:
            return str(MT.cost_type(platform, provider, model) or "")
        except Exception:  # noqa: BLE001 — a word, never a crash
            return ""

    return kind


def _branch_numbers(git: "dict[str, str] | None") -> dict[str, Any]:
    """The branch's numbers (``branches.branch_summary``), {} when they
    cannot be read. BLOCKING; never raises."""
    from ...harness import branches as B

    if not git:
        return {}
    try:
        return B.branch_summary(git)
    except Exception:  # noqa: BLE001 — a gone folder, a git that fails: no numbers
        log.debug("night task branch numbers unreadable", exc_info=True)
        return {}


async def _settle_morning(d: Any, task: dict[str, Any], thread: str, git: "dict[str, str] | None",
                          state: str, messages: list[dict[str, Any]], fin: Any, tests_trusted: bool,
                          stopped_by_emergency: bool, notes: "list[tuple[str, str]]", *,
                          local_only: bool = False) -> None:
    """v1.399.0 (N2): one ended night task's COMMIT and its MORNING FACTS.

    * THE COMMIT (decision 2): the task's work is committed to its own branch
      by Iron Jarvis itself (``build_git.commit_for_night`` — the Commit
      button's machinery under the session's git lock; never a push, never a
      merge) with the task's words as the message (``night_morning.
      commit_message``) — whatever it ended as, so a failed or needs-you
      task's work is not lost. Nothing changed: no commit. Emergency stop:
      no commit (it stops what runs by itself; the work stays in its branch
      folder). A commit git refuses is a note in words.
    * THE FACTS the summary reads (``NightTaskRecord.morning_json``): the
      finish check's test verdict (and whether it can be trusted), the
      branch's numbers (``branches.branch_summary``, after the commit), the
      usage per account (each reply line's receipt), the commit.

    Appends its notes to ``notes``. Never raises."""
    from ...harness import night_morning as M
    from .build_git import commit_for_night

    platform = d.platform
    engine = platform.engine
    tid = task["id"]
    branch = str((git or {}).get("branch") or task.get("branch") or "")
    commit: "dict[str, Any] | None" = None
    commit_note = ""
    from ...harness import night_commit as NC

    left_out: list[dict[str, Any]] = []
    try:
        if git:
            if stopped_by_emergency:
                commit_note = M.EMERGENCY_NOT_COMMITTED
            else:
                done = await commit_for_night(d, thread, M.commit_message(task.get("words") or "", state))
                left_out = [x for x in (done.get("left_out") or []) if isinstance(x, dict)]
                if done.get("committed") and done.get("sha"):
                    commit = {"sha": str(done["sha"]), "subject": str(done.get("subject") or ""),
                              "files": int(done.get("files") or 0)}
                    notes.append(("commit", M.commit_note_words(branch, commit["sha"])))
                    if done.get("note"):
                        notes.append(("info", str(done["note"])))
                elif done.get("nothing") and left_out:
                    # v1.399.0 review: everything it changed was left out (a
                    # secret, a huge file): nothing committed — never "Nothing
                    # changed" beside files that did change.
                    commit_note = "Nothing was committed."
                elif done.get("nothing") and task.get("commit_sha"):
                    # Iron Jarvis stopped after this task's commit and before
                    # the task was marked ended: the settle runs again, finds
                    # nothing new, and the commit it made is still its commit.
                    prior = (task.get("morning") or {}).get("commit")
                    commit = dict(prior) if isinstance(prior, dict) and prior.get("sha") else {
                        "sha": str(task["commit_sha"]), "subject": "", "files": 0}
                elif done.get("nothing") and isinstance(done.get("recovered"), dict):
                    # v1.399.0 review: the same, when the stop came before even
                    # the RECORD of the commit was written — the branch's own
                    # newest commit carries the night trailer.
                    commit = dict(done["recovered"])
                    notes.append(("commit", M.commit_note_words(branch, commit["sha"])))
                elif done.get("nothing"):
                    commit_note = M.TRY_ON_NOTE if task.get("try_on") else M.NOTHING_CHANGED
                else:
                    commit_note = str(done.get("note") or "")
            if left_out:
                said = NC.left_out_words(left_out)
                commit_note = f"{commit_note} {said}".strip() if commit_note else said
            if commit_note:
                notes.append(("commit", commit_note))
        numbers = await asyncio.to_thread(_branch_numbers, git)
        usage = await asyncio.to_thread(M.usage_by_account, messages, _cost_kind(platform))
        tests = fin.get("tests") if isinstance(fin, dict) and fin.get("tests") in ("passed", "failed") else ""
        facts: dict[str, Any] = {
            "tests": tests,
            "tests_trusted": bool(tests_trusted),
            "files": int(numbers.get("files") or 0),
            "added": int(numbers.get("added") or 0),
            "removed": int(numbers.get("removed") or 0),
            "ahead": int(numbers.get("ahead") or 0),
            "uncommitted": int(numbers.get("uncommitted") or 0),
            "remote": str(numbers.get("remote") or ""),
            "usage": usage,
            "commit": commit,
            "commit_note": commit_note,
            # v1.399.0 review: what the commit left out (kept in the branch
            # folder, uncommitted), and whether the session was Local only.
            "left_out": left_out[: NC.MAX_LEFT_OUT_KEPT],
            "left_out_count": len(left_out),
            "local_only": bool(local_only),
        }
        await asyncio.to_thread(N.set_morning, engine, tid, facts, commit_sha=(commit or {}).get("sha", ""))
    except Exception:  # noqa: BLE001 — the morning never breaks the settle
        log.warning("night task %s: its morning facts could not be kept", tid, exc_info=True)


#: v1.399.0: a night whose summary is made this long after it (an upgrade
#: finding older nights) is listed but never announced.
ANNOUNCE_WITHIN_DAYS = 1


def _recent(key: str, now: Any) -> bool:
    """Is night ``key`` (a local date) tonight's or last night's? Only those
    are announced."""
    from datetime import timedelta

    try:
        return key >= (now.date() - timedelta(days=ANNOUNCE_WITHIN_DAYS)).isoformat()
    except Exception:  # noqa: BLE001
        return False


async def _announce_night(platform: Any, key: str, rows: list[dict[str, Any]]) -> bool:
    """``build.night_summary`` — the night's ONE notice (the bell, the tray,
    the phone's one message). True when it was published. Never raises."""
    from ...core.events import EventType
    from ...harness import night_morning as M

    try:
        bus = getattr(platform, "event_bus", None)
        if bus is None:
            return False
        await bus.publish(EventType.BUILD_NIGHT_SUMMARY, M.notice_payload(key, rows), session_id=None)
        return True
    except Exception:  # noqa: BLE001 — the night's record stands without its notice
        log.warning("announcing night %s's summary failed", key, exc_info=True)
        return False


def _mark_local_only(engine: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """v1.399.0 review: a row whose session is Local only NOW (the user may
    turn it on after the task ended) is marked so too — the notice never
    carries its words. BLOCKING; never raises."""
    from ...providers.egress import thread_local_only

    out: list[dict[str, Any]] = []
    for r in rows:
        r = dict(r)
        if not r.get("local_only") and r.get("thread_id"):
            try:
                if thread_local_only(engine, str(r["thread_id"])):
                    r["local_only"] = True
            except Exception:  # noqa: BLE001 — unreadable: the safer reading
                r["local_only"] = True
        out.append(r)
    return out


async def _announce_once(platform: Any, key: str) -> bool:
    """Announce night ``key``'s made summary ONCE, if it was not yet
    (v1.399.0 review). The claim is written FIRST (``claim_notice``: a phone
    never gets the night's message twice, even from two passes), and given
    back when the publish fails, so the next pass tries again — a summary
    made but never announced (Iron Jarvis stopped between the two, a claim
    the database refused) is announced on the next pass, not never. A stop
    between the claim and the publish loses that one notice (the Last night
    view still holds the night): at most once, on purpose."""
    from ...harness import night_morning as M

    engine = platform.engine
    summary = await asyncio.to_thread(M.get_summary, engine, key)
    if summary is None or summary.get("notified"):
        return False
    if not await asyncio.to_thread(M.claim_notice, engine, key):
        return False
    rows = await asyncio.to_thread(_mark_local_only, engine, list(summary.get("rows") or []))
    if await _announce_night(platform, key, rows):
        return True
    await asyncio.to_thread(M.release_notice, engine, key)
    return False


async def morning_pass(d: Any, settled: "list[str] | None" = None) -> list[str]:
    """v1.399.0 (N2): make each night's ONE Last night summary when it is
    READY — the night's last task ended (nothing of that night runs, and
    nothing queued can still start in its window: Emergency stop holds every
    queued task), or its window closed — whichever comes first. The rows are
    built from the records (``night_morning.rows_for_night``), never by a
    model. A summary is announced ONCE (``build.night_summary``; only
    tonight's or last night's — older nights an upgrade finds are listed
    quietly), a task of a made night that ends later brings its row up to
    date (no second notice), and the newest ``KEEP_NIGHTS`` are kept.
    Returns the nights made."""
    from ...harness import night_morning as M

    platform = d.platform
    engine = platform.engine
    config = platform.config
    settled_ids = set(settled or [])
    start, end = N.window_of(config)
    now = N.now_local()
    current = M.night_key(now, start, end) if N.window_open(config, now) else ""
    tasks = await asyncio.to_thread(N.list_tasks, engine)
    queued = any(t["state"] == N.QUEUED for t in tasks)
    emergency = _emergency(d)
    by_night: dict[str, list[dict[str, Any]]] = {}
    for t in tasks:
        if t["state"] == N.QUEUED:
            continue
        key = M.task_night(t, start, end)
        if key:
            by_night.setdefault(key, []).append(t)
    made: list[str] = []
    for key in sorted(by_night):
        group = by_night[key]
        rows = M.rows_for_night(group, key, start, end)
        if await asyncio.to_thread(M.has_summary, engine, key):
            # v1.399.0 review: a row is brought up to date when its task ENDED
            # since (or a task of that night is new) — read off the records,
            # not only off this pass's own settle: a stop between a task's
            # end and the pass that would refresh its row left it "still
            # running" in the Last night view for good.
            if any(t["id"] in settled_ids for t in group) or await asyncio.to_thread(
                    M.rows_differ, engine, key, rows):
                await asyncio.to_thread(M.refresh_summary, engine, key, rows)
        else:
            if key == current:
                if any(t["state"] == N.RUNNING for t in group):
                    continue  # the night's work goes on
                if queued and not emergency:
                    continue  # a queued task still starts in this window
            if await asyncio.to_thread(M.make_summary, engine, key, rows):
                made.append(key)
        # v1.399.0 review: a made summary not yet announced is announced here
        # — the one just made, and one a stop or a refused claim left behind.
        if _recent(key, now):
            try:
                await _announce_once(platform, key)
            except Exception:  # noqa: BLE001 — the next pass tries again
                log.warning("night %s's notice could not be sent yet", key, exc_info=True)
    if made:
        await asyncio.to_thread(M.prune, engine)
    return made


def _session_facts(engine: Any, thread_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Each night session's branch as it is NOW (the buttons: pushed, a pull
    request, the folder thrown away), off its stored git facts — no git
    call. BLOCKING."""
    from sqlmodel import select as _select

    from ...core.db import session_scope
    from ...core.models import ChatThreadRecord
    from ...harness import branches as B

    out: dict[str, dict[str, Any]] = {}
    ids = [t for t in thread_ids if t]
    if not ids:
        return out
    with session_scope(engine) as db:
        rows = db.exec(_select(ChatThreadRecord).where(ChatThreadRecord.id.in_(ids))).all()  # type: ignore[attr-defined]
        for row in rows:
            facts = B.facts_of(S._setup(row)) or {}
            out[row.id] = {
                "exists": True,
                "archived": bool(getattr(row, "archived_at", None)),
                "has_branch": bool(facts),
                "removed": bool(facts.get("removed_at")),
                "pushed": bool(facts.get("pushed_at")),
                "pr_url": str(facts.get("pr_url") or ""),
                "merged": bool(facts.get("merged_at")),
            }
    return out


def summaries_view(d: Any) -> dict[str, Any]:
    """``GET /build/night/summaries``: the made summaries, newest night first
    (the newest is "Last night"), each row with its session's branch as it is
    now. BLOCKING."""
    from ...harness import night_morning as M

    engine = d.platform.engine
    nights = M.list_summaries(engine)
    ids = [str(r.get("thread_id") or "") for n in nights for r in n["rows"]]
    live = _session_facts(engine, ids)
    gone = {"exists": False, "archived": False, "has_branch": False, "removed": False, "pushed": False,
            "pr_url": "", "merged": False}
    for n in nights:
        for r in n["rows"]:
            r["session"] = live.get(str(r.get("thread_id") or ""), gone)
            r["usage_words"] = [M.usage_words(u) for u in r.get("usage") or []]
    return {"nights": nights, "keep": M.KEEP_NIGHTS}


async def settle_running(d: Any) -> list[str]:
    """Settle every RUNNING night task whose turn is over: its ended state
    and notes from its reply line (``night.outcome_of``), its slot given
    back. A task whose turn still runs keeps going (stopped by the loop only
    when it runs :data:`OVERRUN_GRACE_S` past its limit); one waiting on its
    account, or on a resume after a restart, keeps waiting (unless Emergency
    stop is on). Returns the ids settled."""
    from ...core.ids import utcnow
    from ...core.turns import TURNS
    from ...harness import branches as B
    from ...harness.main_env import find as find_main_env
    from ...harness.main_env import tests_line
    from .build import _STARTING, _running_turn, _setup_of

    platform = d.platform
    engine = platform.engine
    running = await asyncio.to_thread(N.rows_in, engine, N.RUNNING)
    if not running:
        return []
    # A turn's notes are written off its loop (``NightTurn.note``): the ones
    # still being written land before any task is judged from its notes.
    await asyncio.to_thread(N.flush_notes, 5.0)
    emergency = _emergency(d)
    kept = await asyncio.to_thread(_kept_threads, engine)
    now = _naive(utcnow())
    settled: list[str] = []
    for task in running:
        tid, thread = task["id"], task["thread_id"]
        if tid in _STARTING_TASKS:
            continue
        if not thread:
            started = _parse(task.get("started_at"))
            if started is not None and now is not None and (now - started).total_seconds() < NO_SESSION_GIVE_UP_S:
                continue
            await asyncio.to_thread(N.add_note, engine, tid, "error",
                                    "Iron Jarvis stopped before this task's session was made.")
            await asyncio.to_thread(N.finish_task, engine, tid, N.FAILED)
            _release(d, tid)
            settled.append(tid)
            continue
        turn = _running_turn(d, thread)
        if turn or thread in _STARTING:
            _keep_held(d, tid)
            # The backstop counts THIS turn's own time (a reply carried on
            # after a restart starts a new turn; the hours Iron Jarvis was
            # off are never counted — the reply cap's own rule).
            from .chat import _turn_logs

            lg = _turn_logs(d).get(turn) if turn else None
            started = _naive(getattr(lg, "started_at", None)) if lg is not None else None
            # v1.398.0 review: what is LEFT of the task's limit (its earlier
            # turns' time counts), never a fresh limit per turn.
            limit_s = 60.0 * N.minutes_left(task, platform.config)
            if (turn and started is not None and now is not None
                    and (now - started).total_seconds() > limit_s + OVERRUN_GRACE_S):
                # The backstop: the reply cap ends a reply at a step
                # boundary; a step that never returns is stopped here —
                # v1.398.0 review: the STEP itself too (a turn's Stop waits
                # for the step in flight, so a hung command ran on for its
                # whole deadline).
                from ...harness.stall import running_steps, stop_step

                for call_id in running_steps(turn):
                    stop_step(turn, call_id)
                if TURNS.stop(turn):
                    await asyncio.to_thread(
                        N.add_note, engine, tid, "limit",
                        f"Stopped: it ran past its {N.task_minutes(platform.config)}-minute limit.",
                    )
            continue
        row = await asyncio.to_thread(S.get_row, engine, thread)
        if row is None:
            await asyncio.to_thread(N.add_note, engine, tid, "stopped", "Its coding session was deleted.")
            await asyncio.to_thread(N.finish_task, engine, tid, N.STOPPED)
            _release(d, tid)
            settled.append(tid)
            continue
        setup = _setup_of(row)
        task = await asyncio.to_thread(N.get_task, engine, tid) or task
        stopping = emergency or N.task_has_stop(task)
        # v1.398.0 review: the boot's resume pass may still carry a cut-off
        # reply on — only until that pass has run (a reply it did not carry
        # on would otherwise hold the task "running", its slot and the PC's
        # keep-awake, forever).
        resume_pending = thread in kept and not getattr(platform, "build_resume_done", False)
        if not stopping and resume_pending:
            _keep_held(d, tid)
            continue
        waiting = bool(setup.get("account_wait"))
        if (not stopping and waiting and N.window_open(platform.config)
                and N.minutes_left(task, platform.config) >= 1.0):
            # Waiting on its account's reset (Resume when ready carries it
            # on, inside the window): still the night's task.
            _keep_held(d, tid)
            continue
        forced: "list[tuple[str, str]]" = []
        if waiting:
            # Stopped (Emergency stop, the user's Cancel), the window closed,
            # or its time is used up: its wait must not carry it on later —
            # by day, under the night's posture.
            await asyncio.to_thread(S.update, engine, thread, setup={"account_wait": None})
            if not stopping:
                forced.append(("account", N.WAIT_WINDOW_CLOSED if not N.window_open(platform.config)
                               else N.WAIT_TIME_USED_UP))
        if thread in kept:
            # v1.398.0 review: a cut-off reply nothing carried on is never
            # carried on later (a later boot would run it under the user's
            # own posture, a day late): Retry is on the session's page.
            from ...harness import reply_record as RR

            for rec in await asyncio.to_thread(RR.kept, engine):
                if str(rec.get("thread_id") or "") == thread:
                    await asyncio.to_thread(RR.drop, engine, str(rec.get("turn_id") or ""))
        # The transcript is up to 200 lines of JSON: parsed off the loop.
        messages = await asyncio.to_thread(S._messages, row)
        line = N.last_reply_line(messages)
        state, notes = N.outcome_of(task, line)
        if forced:
            state = N.STOPPED
            notes = [*forced, *notes]
        if emergency and state != N.STOPPED:
            state = N.STOPPED
            notes.append(("emergency", N.EMERGENCY_NOTE))
        git = B.facts_of(setup)
        borrowed = None
        if git:
            borrowed = await asyncio.to_thread(
                find_main_env, git.get("checkout", ""), S.folder_of(row), git.get("repo", ""),
                git.get("worktree", ""),
            )
            if borrowed:
                notes.append(("env", "Tests and checks in its branch folder used "
                              f"{borrowed.words()}; nothing was installed."))
        fin = (line or {}).get("finish") if isinstance(line, dict) else None
        tests_trusted = True
        if isinstance(fin, dict) and fin.get("tests") in ("passed", "failed"):
            words = "Its tests passed." if fin["tests"] == "passed" else "Its tests failed."
            if git and borrowed and tests_line(borrowed):
                words += " They ran with your main folder's environment."
                if borrowed.untrusted:
                    # v1.398.0 review: an editable install of the main
                    # folder's code that could not be pointed at the branch.
                    from ...harness.main_env import untrusted_words

                    words += " " + untrusted_words(borrowed)
                    tests_trusted = False
                    if fin["tests"] == "passed" and state == N.DONE:
                        state = N.NEEDS_YOU  # a pass nobody can rely on
            notes.append(("tests", words))
        if git:
            # v1.398.0 review: the user's MAIN folder, compared with what it
            # was when the task started — its commands are not confined to
            # its branch, so a change there is the one thing the morning must
            # never miss ("needs you", a note of its own, ``main_changed``).
            from ...harness import night_guard as G

            moved = await asyncio.to_thread(_main_moved, engine, tid, git, S.folder_of(row))
            if moved:
                notes.insert(0, ("outside", G.changed_words(git.get("main", "") or git.get("repo", ""), moved)))
                if state == N.DONE:
                    state = N.NEEDS_YOU
            elif moved is None:
                notes.append(("info", G.UNCHECKED))
            notes.append(("info", G.NOT_CONFINED))
        # v1.398.0 review: a night task that ENDED leaves nothing running —
        # a dev server or a watcher its commands started in the background
        # ran on all night and into the morning (and Emergency stop stopped
        # the turn, not them). Its session's programs end with it; a window
        # the user has open is spared (``procs.end_session``).
        ended_pids = await asyncio.to_thread(S.end_session_programs, thread)
        if ended_pids:
            notes.append(("info", f"It left {len(ended_pids)} program(s) running in the background; "
                          "they were stopped when it ended."))
        # v1.399.0 (N2, decision 2): Iron Jarvis COMMITS the task's work to
        # its own branch (the Commit button's machinery; never a push, never a
        # merge) — a failed or needs-you task's too, so nothing is lost — and
        # keeps what the morning summary reads, off the records.
        stopped_by_emergency = emergency or N.has_note(task, "emergency") or any(
            k == "emergency" for k, _t in notes)
        # v1.399.0 review: Local only (the session's switch, or a turn that
        # ran under it) — the morning's notice never carries its words.
        local_only = setup.get("local_only") is True or any(
            isinstance(m, dict) and isinstance(m.get("route"), dict) and m["route"].get("local_only") is True
            for m in messages
        )
        await _settle_morning(d, task, thread, git, state, messages, fin, tests_trusted,
                              stopped_by_emergency, notes, local_only=local_only)
        if git:
            # v1.399.0 review: the commit ran the repository's own HOOKS (a
            # post-commit hook can write anywhere), so the user's main folder
            # is read AGAIN after it — the night's own commit moves only its
            # ij/ branch, which the guard never counts.
            from ...harness import night_guard as G

            after = await asyncio.to_thread(_main_moved, engine, tid, git, S.folder_of(row))
            if after and after != moved:
                said = G.changed_words(git.get("main", "") or git.get("repo", ""), after)
                notes = [(k, t) for k, t in notes if k != "outside" and t != G.UNCHECKED]
                notes.insert(0, ("outside", said))
                if not moved:
                    notes.append(("info", "Your main folder changed while Iron Jarvis committed this task's "
                                  "work (a git hook of the repository runs then)."))
                if state == N.DONE:
                    state = N.NEEDS_YOU
        for kind, text in notes:
            await asyncio.to_thread(N.add_note, engine, tid, kind, text)
        await asyncio.to_thread(N.finish_task, engine, tid, state)
        _release(d, tid)
        settled.append(tid)
    return settled


# --------------------------------------------------------------------------- #
# start
# --------------------------------------------------------------------------- #


async def start_task(d: Any, task: dict[str, Any]) -> bool:
    """Start ONE queued task: its session on a NEW branch (the one create
    path), then its first turn (or its attempts), as Iron Jarvis's own
    (``started_by="night"``). True when it started; a refusal ends the task
    FAILED with the reason in words. The caller holds its slot."""
    from .build import (
        CreateRefused,
        SessionBusy,
        _setup_of,
        _with_initial_account,
        create_session_row,
        start_build_turn,
    )
    from ...harness import branches as B

    platform = d.platform
    engine = platform.engine
    tid = task["id"]
    from ...harness.night_morning import night_key

    # v1.399.0 (N2): the night it runs in (the morning's summary is per night).
    night = night_key(N.now_local(), *N.window_of(platform.config))
    if not await asyncio.to_thread(N.mark_running, engine, tid, night):
        return False  # someone else took it (cancelled, started)
    _STARTING_TASKS.add(tid)
    try:
        try:
            folder = S.resolved_folder(task["folder"])
            problem = await asyncio.to_thread(S.folder_problem, folder)
            if problem:
                raise CreateRefused(f"This folder cannot be used: {problem}")
            if not task.get("try_on"):
                # v1.398.0 review: never pay-per-use beyond the user's limit
                # (attempts and the team apply it to their own models).
                why = await asyncio.to_thread(pay_problem, platform, *_night_pair(platform, task))
                if why:
                    raise CreateRefused(why)
            setup = {k: v for k, v in {"provider": task["provider"], "model": task["model"]}.items() if v}
            setup = await asyncio.to_thread(_with_initial_account, platform, setup, "")
            row, _note = await create_session_row(
                d, folder, title=title_of(task["words"]), setup=setup, branch=True,
            )
            git = B.facts_of(_setup_of(row)) or {}
            await asyncio.to_thread(
                N.set_session, engine, tid, thread_id=row.id, branch=str(git.get("branch") or ""),
            )
            if not git:
                # v1.398.0 review: a night task ALWAYS works on its own
                # branch (the route refused a folder that cannot have one;
                # this is the folder changing in between). Never in the
                # user's folder itself.
                raise CreateRefused("Its folder can no longer have a branch of its own, so it did not start.")
            # v1.398.0 review: the user's MAIN folder as it is now, to compare
            # when the task ends (its commands are not confined to its branch).
            await asyncio.to_thread(_keep_main_before, engine, tid, git, S.folder_of(row))
            turn_id = "bt_" + secrets.token_hex(8)
            picks = task.get("try_on") or []
            if picks:
                from . import build_attempts as BA_routes
                from .build import TryOnPick

                body = SimpleNamespace(
                    try_on=[TryOnPick(provider=p["provider"], model=p.get("model") or "") for p in picks],
                    attachments=[], files=[], skill="", folder_skill="", command="",
                )
                lg = await BA_routes.maybe_start(d, row.id, task["words"], turn_id, body, started_by=N.BY_NIGHT)
                if lg is None:
                    raise CreateRefused("The models to try it on could not be found in your model menu.")
            else:
                await start_build_turn(
                    d, row.id, task["words"], turn_id, started_by=N.BY_NIGHT,
                    model_note=TEAM_NOTE if task.get("team") else "",
                )
            await asyncio.to_thread(N.set_session, engine, tid, turn_id=turn_id)
            return True
        except CreateRefused as exc:
            why = str(exc)
        except HTTPException as exc:
            why = str(exc.detail)
        except SessionBusy:
            why = "Its session was already busy."
        except Exception as exc:  # noqa: BLE001 — one task never stops the night
            log.warning("night task %s did not start", tid, exc_info=True)
            why = f"It could not start ({type(exc).__name__})."
        from .build import UNATTENDED_REFUSAL

        if why == UNATTENDED_REFUSAL:
            # v1.398.0 review: Emergency stop turned on while it was starting
            # — STOPPED by it (as every running task is), never "failed".
            await asyncio.to_thread(N.add_note, engine, tid, "emergency", N.EMERGENCY_NOTE)
            await asyncio.to_thread(N.finish_task, engine, tid, N.STOPPED)
            return False
        await asyncio.to_thread(N.add_note, engine, tid, "error", why)
        await asyncio.to_thread(N.finish_task, engine, tid, N.FAILED)
        return False
    finally:
        _STARTING_TASKS.discard(tid)


def _pass_lock(platform: Any) -> asyncio.Lock:
    """ONE pass at a time per platform (v1.398.0 review): two passes that
    overlapped (the loop's and another caller's) each counted the room before
    either started anything, and together started more than "at most N" — and
    two tasks of one folder."""
    lock = getattr(platform, "_night_pass_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        with contextlib.suppress(Exception):
            platform._night_pass_lock = lock
    return lock


async def night_pass(d: Any) -> dict[str, list[str]]:
    """ONE pass of the night loop (see the module docstring). Serialized per
    platform (:func:`_pass_lock`)."""
    async with _pass_lock(d.platform):
        return await _night_pass(d)


async def _night_pass(d: Any) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"settled": [], "started": [], "summaries": []}
    out["settled"] = await settle_running(d)
    # v1.399.0 (N2): a night whose last task just ended, or whose window has
    # closed, gets its ONE summary now — inside the window or not, Emergency
    # stop or not (the passes below may return early).
    try:
        out["summaries"] = await morning_pass(d, out["settled"])
    except Exception:  # noqa: BLE001 — the morning never stops the night
        log.warning("night summary pass failed", exc_info=True)
    platform = d.platform
    config = platform.config
    if _emergency(d):
        return out
    if not N.window_open(config):
        return out
    engine = platform.engine
    running = await asyncio.to_thread(N.rows_in, engine, N.RUNNING)
    room = N.max_at_once(config) - len(running)
    busy = {S.folder_key(t["folder"]) for t in running}
    if room <= 0:
        return out
    queued = await asyncio.to_thread(N.rows_in, engine, N.QUEUED)
    for task in queued:
        if room <= 0:
            break
        key = S.folder_key(task["folder"])
        if key in busy:
            continue  # one per folder: it waits for the folder's task to end
        if not _hold(d, task["id"]):
            break  # every "Runs at once" slot is busy: nothing more this pass
        started = await start_task(d, task)
        if started:
            busy.add(key)
            room -= 1
            out["started"].append(task["id"])
        else:
            _release(d, task["id"])
    return out


async def night_loop(d: Any, on_tick: Any) -> None:
    """The lifespan loop: a pass every :data:`TICK_S` (and at once when a
    night turn ends or the list changes), each reporting ITS OWN outcome."""
    loop = asyncio.get_running_loop()
    event = asyncio.Event()
    _WAKE.update(loop=loop, event=event)
    try:
        while True:
            event.clear()
            try:
                await night_pass(d)
                on_tick(True, None)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — a failed pass is a row, not a crash
                log.warning("night shift pass failed", exc_info=True)
                on_tick(False, exc)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(event.wait(), timeout=TICK_S)
    finally:
        if _WAKE.get("event") is event:
            _WAKE.clear()


# --------------------------------------------------------------------------- #
# checks (add / edit)
# --------------------------------------------------------------------------- #


class TryOnBody(BaseModel):
    provider: str = Field(min_length=1, max_length=120)
    model: str = Field(default="", max_length=300)


class NightTaskBody(BaseModel):
    folder: str = Field(default="", max_length=1000)
    words: str = Field(default="", max_length=N.MAX_WORDS + 1000)
    provider: str = Field(default="", max_length=120)
    model: str = Field(default="", max_length=300)
    team: bool = False
    try_on: list[TryOnBody] = Field(default_factory=list, max_length=10)


class NightTaskPatch(BaseModel):
    folder: str | None = Field(default=None, max_length=1000)
    words: str | None = Field(default=None, max_length=N.MAX_WORDS + 1000)
    provider: str | None = Field(default=None, max_length=120)
    model: str | None = Field(default=None, max_length=300)
    team: bool | None = None
    try_on: list[TryOnBody] | None = Field(default=None, max_length=10)


class MoveBody(BaseModel):
    direction: str = Field(min_length=1, max_length=10)


async def _check_folder(folder: str) -> str:
    from ...harness import branches as B

    folder = S.resolved_folder(folder)
    if not folder.strip():
        raise N.NightRefused("Choose the folder this task works in.")
    problem = await asyncio.to_thread(S.folder_problem, folder)
    if problem:
        raise N.NightRefused(f"This folder cannot be used: {problem}")
    info = await asyncio.to_thread(B.inspect_folder, folder)
    if not info.get("repo") or not info.get("can_branch"):
        raise N.NightRefused(NOT_A_REPO_NIGHT.format(why=info.get("note") or B.NOT_A_REPO))
    return folder


def _menu_pairs(d: Any) -> "set[tuple[str, str]] | None":
    try:
        from .build_attempts import menu_rows

        rows = menu_rows(d)
    except Exception:  # noqa: BLE001
        return None
    return {(str(r.get("provider") or ""), str(r.get("model") or "")) for r in rows}


async def _check_models(d: Any, provider: str, model: str, team: bool, picks: list[dict[str, str]]) -> None:
    if picks and (provider or model):
        raise N.NightRefused(MODEL_AND_TRY_ON)
    if picks and team:
        raise N.NightRefused(TEAM_AND_TRY_ON)
    if picks and not 2 <= len(picks) <= 3:
        raise N.NightRefused(TRY_ON_COUNT.format(n=len(picks)))
    if model and not provider:
        raise N.NightRefused("Pick the model from the model menu (its provider is missing).")
    if team:
        from ...agents import model_team as MT

        members = await asyncio.to_thread(MT.team_members, d.platform)
        if not members:
            raise N.NightRefused(NO_TEAM)
    wanted = ([(provider, model)] if provider else []) + [(p["provider"], p.get("model") or "") for p in picks]
    if not wanted:
        return
    pairs = await asyncio.to_thread(_menu_pairs, d)
    if pairs is None:
        raise N.NightRefused(MENU_UNREADABLE, 503)
    providers = {p for p, _m in pairs}
    for prov, mod in wanted:
        if (prov, mod) in pairs or (not mod and prov in providers):
            continue
        raise N.NightRefused(MODEL_UNKNOWN.format(name=f"{prov} · {mod}" if mod else prov))


def _picks(raw: "list[TryOnBody] | None") -> list[dict[str, str]]:
    return [{"provider": p.provider.strip(), "model": (p.model or "").strip()} for p in (raw or [])]


def list_view(d: Any) -> dict[str, Any]:
    """``GET /build/night``. BLOCKING."""
    platform = d.platform
    config = platform.config
    return {
        "tasks": N.list_tasks(platform.engine),
        "window": N.window_view(config),
        "limits": {
            "at_once": N.max_at_once(config),
            "task_minutes": N.task_minutes(config),
            "card_wait_minutes": int(N.card_wait_seconds(config) // 60),
        },
        "emergency_stop": bool(_emergency(d)),
    }


def register(app: FastAPI, d: Any) -> None:
    def _refused(exc: N.NightRefused) -> HTTPException:
        return HTTPException(status_code=exc.status, detail=str(exc))

    @app.get("/build/night")
    def night_list() -> dict[str, Any]:
        """The Tonight list: queued tasks in their order, running ones, the
        ended ones of the last days; the window in words; the limits."""
        return list_view(d)

    @app.get("/build/night/summaries")
    def night_summaries() -> dict[str, Any]:
        """v1.399.0 (N2): the Last night view — each night's summary, newest
        first (the last 14), its tasks failed and needs-you first, each with
        its branch as it is now (pushed, a pull request, thrown away)."""
        return summaries_view(d)

    @app.post("/build/night", status_code=201)
    async def night_add(body: NightTaskBody) -> dict[str, Any]:
        try:
            folder = await _check_folder(body.folder)
            picks = _picks(body.try_on)
            await _check_models(d, body.provider.strip(), body.model.strip(), body.team, picks)
            task = await asyncio.to_thread(
                N.add_task, d.platform.engine, folder=folder, words=body.words,
                provider=body.provider.strip(), model=body.model.strip(), team=body.team, try_on=picks,
            )
        except N.NightRefused as exc:
            raise _refused(exc) from None
        wake_night_loop()
        return task

    @app.patch("/build/night/{task_id}")
    async def night_edit(task_id: str, body: NightTaskPatch) -> dict[str, Any]:
        engine = d.platform.engine
        current = await asyncio.to_thread(N.get_task, engine, task_id)
        if current is None:
            raise HTTPException(status_code=404, detail=N.NOT_FOUND)
        if current["state"] == N.RUNNING:
            raise HTTPException(status_code=409, detail=N.RUNNING_EDIT)
        if current["state"] != N.QUEUED:
            raise HTTPException(status_code=409, detail=N.ENDED_EDIT)
        fields = body.model_fields_set
        changes: dict[str, Any] = {}
        try:
            if "folder" in fields and body.folder is not None:
                changes["folder"] = await _check_folder(body.folder)
            for key in ("words", "provider", "model", "team"):
                if key in fields and getattr(body, key) is not None:
                    value = getattr(body, key)
                    changes[key] = value.strip() if isinstance(value, str) else value
            if "try_on" in fields:
                changes["try_on"] = _picks(body.try_on)
            provider = changes.get("provider", current["provider"])
            model = changes.get("model", current["model"])
            team = changes.get("team", current["team"])
            picks = changes.get("try_on", current["try_on"])
            if any(k in changes for k in ("provider", "model", "team", "try_on")):
                await _check_models(d, provider, model, bool(team), list(picks or []))
            task = await asyncio.to_thread(N.edit_task, engine, task_id, changes)
        except N.NightRefused as exc:
            raise _refused(exc) from None
        wake_night_loop()
        return task

    @app.delete("/build/night/{task_id}")
    def night_remove(task_id: str) -> dict[str, Any]:
        try:
            N.remove_task(d.platform.engine, task_id)
        except N.NightRefused as exc:
            raise _refused(exc) from None
        return {"ok": True}

    @app.post("/build/night/{task_id}/move")
    def night_move(task_id: str, body: MoveBody) -> dict[str, Any]:
        try:
            queued = N.move_task(d.platform.engine, task_id, body.direction.strip().lower())
        except N.NightRefused as exc:
            raise _refused(exc) from None
        return {"queued": queued}

    @app.post("/build/night/{task_id}/cancel")
    def night_cancel(task_id: str) -> dict[str, Any]:
        """A queued task never starts; a running one's turn is stopped (it
        settles as stopped, with what it got done)."""
        from ...core.turns import TURNS

        engine = d.platform.engine
        task = N.get_task(engine, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail=N.NOT_FOUND)
        if task["state"] == N.QUEUED and N.cancel_queued(engine, task_id):
            return {"task": N.get_task(engine, task_id), "stopped_turn": ""}
        task = N.get_task(engine, task_id) or task
        if task["state"] != N.RUNNING:
            raise HTTPException(status_code=409, detail="This task has already ended.")
        N.add_note(engine, task_id, "stopped", N.CANCELLED_RUNNING)
        from .build import _running_turn

        turn = _running_turn(d, task["thread_id"]) if task["thread_id"] else ""
        if turn:
            TURNS.stop(turn)
            # v1.398.0 review: and its running step (a hung command ran on
            # for its whole deadline after Stop).
            from ...harness.stall import running_steps, stop_step

            for call_id in running_steps(turn):
                stop_step(turn, call_id)
        wake_night_loop()
        return {"task": N.get_task(engine, task_id), "stopped_turn": turn}


__all__ = [
    "NightTaskBody", "NightTaskPatch", "OVERRUN_GRACE_S", "TEAM_NOTE", "TICK_S", "list_view",
    "morning_pass", "night_loop", "night_pass", "register", "settle_running", "start_task",
    "summaries_view", "title_of", "wake_night_loop",
]
