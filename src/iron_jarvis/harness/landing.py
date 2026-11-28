"""MERGE ALL THAT PASSED (25-a; R3.5 of ``docs/BUILD-OUT-PLAN-2026-11-20.md``,
idea 25 of ``docs/next-leaps/06-coding-proof-council-board-landing.md``,
choices D-25.1 and D-25.3 as the master plan settled them).

In the morning, Last night says "5 of 6 passed their tests. Merge all 5".
The first press shows the PREVIEW (:func:`preview`): every passing night
task of a folder, in the order it was queued on Tonight, each with its
branch, its commits, its files with lines added and removed, and the branch
it goes into; the test command run after each merge; and each row left out,
with why (a row that would clash says so here, before anything is pressed).
The second press MERGES (:func:`run`): one by one in a LANDING COPY of the
folder (``branches.start`` from the base's commit — never the user's
checkout), the repository's hooks off, the folder's tests after each
(``hosted/proof.run_checks``, 22-a's run, with the main folder's
environment); the first clash or failing test stops there and that step is
taken back in the copy only. The base then moves ONCE to the copy's last
good commit — a fast-forward in the user's checkout when the base is checked
out there and clean (their own hooks run there), else ``update-ref`` with
the old value after git's own guard. The base never holds a merge whose
tests did not pass. Nothing is pushed (D-25.3): the base changed on this PC
only.

UNDO (D-25.1, :func:`undo_plan`, :func:`undo`): while the base still points
where the landing left it, no remote branch has that commit and the user's
checkout of it is clean, the base goes back EXACTLY (``reset --keep`` in the
checkout, else ``update-ref`` with the old value). Otherwise one reverse
commit per merge (``git revert -m 1``, newest first) is made in a landing
copy and the base moves forward to it. The plan says which before the
press; a checkout with changes refuses either in words.

What is green (:func:`why_not`): ended done, its tests passed AND trusted
(``night_morning.summary_row``), nothing needs the user, the main folder was
unchanged by it, its session still has its branch (not thrown away), and it
is not merged yet. The preview's ``id`` is a hash of the folder, the base
and its commit, the command and every step's head: a landing recomputes the
preview and refuses ("changed since you looked") when the ids differ, so
consent is exactly the list the card showed.

Every git call goes through ``branches._g`` (``core.procs``, bounded); every
function here is BLOCKING (the routes run them off the event loop); the
session locks, "not while a reply runs" and the background run live in
``daemon/routes/night_land``. Words name no engine part: a branch is merged,
never "landed"; two branches that change the same lines "clash".
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sqlmodel import Field, SQLModel, select

from ..core.db import session_scope
from ..core.ids import new_id, utcnow
from . import branches as B

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# words and bounds
# --------------------------------------------------------------------------- #

#: A landing's states.
RUNNING = "running"
MOVING = "moving"  # the base is being moved (``base_after`` is already written)
MERGED = "merged"
STOPPED = "stopped"
STATES = (RUNNING, MOVING, MERGED, STOPPED)

#: A step's states.
STEP_WAITING = "waiting"
STEP_MERGING = "merging"
STEP_TESTING = "testing"
STEP_MERGED = "merged"
STEP_CLASH = "clash"
STEP_FAILED = "tests_failed"
STEP_NOT_CHECKED = "not_checked"
STEP_MERGE_FAILED = "merge_failed"
STEP_SKIPPED = "skipped"
# A step merged in the merge's copy whose base never moved (Emergency stop,
# closing, an error, a move git refused): it is on the copy's branch only.
STEP_COPY_ONLY = "copy_only"

#: Undo's two ways (D-25.1) and its refusal.
UNDO_PUT_BACK = "put_back"
UNDO_REVERSE = "reverse"
UNDO_REFUSED = "refused"

#: At most this many commits and files are listed per step.
COMMITS_SHOWN = 20
FILES_SHOWN = 50
#: At most this many clashing paths are named in a sentence.
CLASH_NAMED = 5
#: The hidden refs that keep a landing's "before" alive and out of every list.
REF_ROOT = "refs/ironjarvis/landing"
#: How long a landing waits for the folder's other check (22-a) to end.
CHECK_BUSY_WAIT_S = 600.0
#: How often a running landing looks at Emergency stop.
LOOK_S = 0.5

#: Files whose change means the branch may need packages the main folder's
#: ``.venv`` / ``node_modules`` do not have (the landing copy borrows them).
DEPENDENCY_FILES = frozenset({
    "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb", "npm-shrinkwrap.json",
    "pyproject.toml", "uv.lock", "poetry.lock", "pipfile", "pipfile.lock", "setup.py", "setup.cfg",
    "cargo.toml", "cargo.lock", "go.mod", "go.sum", "gemfile", "gemfile.lock", "composer.json",
    "composer.lock",
})

CHANGED_SINCE = "Something changed since you looked (a branch or {base} moved), so nothing was merged. Look again."
NOTHING_READY = "Nothing here is ready to merge, so nothing was merged."
EMERGENCY_ON = "Emergency stop is on, so nothing was merged. Turn it off first."
ALREADY_RUNNING = "A merge is already running in this folder. Wait for it to end."
NO_BASE = "The branch {base} no longer exists in this repository, so there is nothing to merge into."
NOT_HERE = "{base} is checked out at {where}, a folder that is not there right now, so nothing was merged."
CHECKOUT_DIRTY = (
    "Your checkout of {base} at {where} has changes that are not committed. Commit or put them away first; "
    "nothing was merged."
)
PUSH_WORDS = "Nothing was pushed: {base} changed on this PC only."
NO_UNDO = "This merge cannot be undone any more."


def _short(sha: str) -> str:
    return str(sha or "")[:7]


def _plural(n: int, one: str, many: str = "") -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _quote(title: str) -> str:
    t = " ".join(str(title or "").split())
    if len(t) > 80:
        t = t[:79].rstrip() + "…"
    return f"'{t}'"


def _paths_words(paths: list[str]) -> str:
    named = ", ".join(paths[:CLASH_NAMED]) or "some files"
    more = len(paths) - CLASH_NAMED
    return named + (f" and {more} more" if more > 0 else "")


def into_words(base: str, folder: str) -> str:
    """"main in D:\\code\\shop" — the branch a landing goes into."""
    return f"{base} in {folder}" if folder else base


def clash_words(paths: list[str]) -> str:
    """"Merging would clash in a.py." (VOCABULARY.md: clash)."""
    return f"Merging would clash in {_paths_words(paths)}."


# --------------------------------------------------------------------------- #
# the record (a late model, ``core.db._LATE_MODEL_MODULES``)
# --------------------------------------------------------------------------- #


class LandRecord(SQLModel, table=True):
    """ONE press of Merge all (25-a): what was merged, where the base was and
    went, and its Undo."""

    __tablename__ = "landrecord"

    id: str = Field(default_factory=lambda: new_id("land"), primary_key=True)
    #: The night (``night_morning.night_key``) its tasks ran in.
    night: str = Field(default="", index=True)
    #: The folder the tasks worked in (``sessions.folder_key``) and as stored.
    folder_key: str = Field(default="", index=True)
    folder: str = ""
    #: The repository's top folder (the user's checkout side) and the branch.
    repo: str = ""
    base: str = ""
    #: The base's commit before, and the one the landing moved it to ("" =
    #: it did not move).
    base_before: str = ""
    base_after: str = ""
    #: Where the base was checked out ("" = nowhere).
    where: str = ""
    state: str = Field(default=RUNNING, index=True)
    #: ``[{task_id, thread_id, title, branch, head, state, merge_commit,
    #: seconds, line, files}]`` in order.
    steps_json: str = ""
    #: ``{task_id, title, why, words, files}`` of the step it stopped at, or "".
    stopped_json: str = ""
    preview_id: str = ""
    command: str = ""
    #: The landing copy's branch (it stays; the copy goes to the trash).
    landing_branch: str = ""
    landing_trash: str = ""
    #: The result line, in words.
    words: str = ""
    undone_at: datetime | None = None
    undo_how: str = ""
    undo_words: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    ended_at: datetime | None = None


_DB_LOCK = threading.Lock()


def _loads(raw: Any, empty: Any) -> Any:
    try:
        value = json.loads(raw or "")
    except (TypeError, ValueError):
        return empty
    return value if isinstance(value, type(empty)) else empty


def _iso(moment: Any) -> "str | None":
    if not isinstance(moment, datetime):
        return None
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    return moment.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def create_record(engine: Any, **fields: Any) -> dict[str, Any]:
    """Write a new landing's record (state running). BLOCKING."""
    with _DB_LOCK, session_scope(engine) as db:
        rec = LandRecord(**fields)
        db.add(rec)
        db.commit()
        db.refresh(rec)
        return view(rec)


def update_record(engine: Any, record_id: str, **changes: Any) -> "dict[str, Any] | None":
    """Change a landing's record (``steps`` / ``stopped`` as values). BLOCKING;
    never raises (a record that cannot be written is logged)."""
    try:
        with _DB_LOCK, session_scope(engine) as db:
            rec = db.get(LandRecord, str(record_id))
            if rec is None:
                return None
            for key, value in changes.items():
                if key == "steps":
                    rec.steps_json = json.dumps(value)
                elif key == "stopped":
                    rec.stopped_json = json.dumps(value) if value else ""
                else:
                    setattr(rec, key, value)
            rec.updated_at = utcnow()
            db.add(rec)
            db.commit()
            db.refresh(rec)
            return view(rec)
    except Exception:  # noqa: BLE001
        log.warning("a merge's record %s could not be written", record_id, exc_info=True)
        return None


def get_record(engine: Any, record_id: str) -> "LandRecord | None":
    with session_scope(engine) as db:
        rec = db.get(LandRecord, str(record_id))
        if rec is not None:
            db.expunge(rec)
        return rec


def records_for_night(engine: Any, night: str) -> list[LandRecord]:
    """The night's landings, newest first. BLOCKING."""
    with session_scope(engine) as db:
        recs = db.exec(
            select(LandRecord).where(LandRecord.night == str(night))
            .order_by(LandRecord.created_at.desc())  # type: ignore[attr-defined]
        ).all()
        for r in recs:
            db.expunge(r)
        return list(recs)


def view(rec: Any) -> dict[str, Any]:
    """A landing as the routes answer it (no git call)."""
    steps = _loads(getattr(rec, "steps_json", ""), [])
    stopped = _loads(getattr(rec, "stopped_json", ""), {})
    merged = [s for s in steps if isinstance(s, dict) and s.get("state") == STEP_MERGED]
    return {
        "id": rec.id,
        "night": rec.night,
        "folder": rec.folder,
        "folder_name": os.path.basename(os.path.normpath(rec.folder or "")) or rec.folder,
        "repo": rec.repo,
        "base": rec.base,
        "into": into_words(rec.base, rec.folder),
        "base_before": _short(rec.base_before),
        "base_after": _short(rec.base_after),
        "where": rec.where,
        "state": rec.state if rec.state in STATES else STOPPED,
        "steps": [s for s in steps if isinstance(s, dict)],
        "merged": len(merged),
        "stopped": stopped or None,
        "command": rec.command,
        "landing_branch": rec.landing_branch,
        "words": rec.words,
        "undone": rec.undone_at is not None,
        "undone_at": _iso(rec.undone_at),
        "undo_how": rec.undo_how,
        "undo_words": rec.undo_words,
        "created_at": _iso(rec.created_at),
        "ended_at": _iso(rec.ended_at),
    }


# --------------------------------------------------------------------------- #
# what is green (pure)
# --------------------------------------------------------------------------- #

def merge_reversed(facts: "dict[str, str] | None") -> bool:
    """Was this session's merge reversed by a Merge all's Undo (one reverse
    commit, D-25.1)? Its ``merge_reversed`` names the merge commit it
    reversed; a later merge (a new ``merge_commit``) is not reversed. Pure."""
    if not facts or not facts.get("merged_at"):
        return False
    gone = str(facts.get("merge_reversed") or "")
    return bool(gone) and gone == str(facts.get("merge_commit") or "")[:12]


def reversed_words(facts: "dict[str, str] | None") -> str:
    """"It was merged into main, then Undo reversed it, so Merge all leaves it
    out." (its commits are still in the base's history). Pure."""
    into = str((facts or {}).get("merged_into") or "its branch")
    return f"It was merged into {into}, then Undo reversed it, so Merge all leaves it out."


_STATE_WHY = {
    "failed": "It failed.",
    "needs_you": "It needs you.",
    "running": "It is still running.",
    "stopped": "It was stopped.",
}


def why_not(row: dict[str, Any], facts: "dict[str, str] | None") -> str:
    """Why a Last night row is NOT ready to merge, in words; "" when it is
    green: ended done, its tests passed and trusted, nothing needs the user,
    the main folder unchanged by it, its branch kept, not merged yet. Pure."""
    state = str(row.get("state") or "")
    if state != "done":
        return _STATE_WHY.get(state, "It did not end done.")
    if row.get("main_changed"):
        return "Your main folder changed while it ran."
    tests = str(row.get("tests") or "")
    if tests == "failed":
        return "Its tests failed."
    if tests != "passed":
        return "No tests ran for it."
    if row.get("tests_trusted") is False:
        return "Its tests ran, but the result is not trusted."
    needs = [str(n) for n in (row.get("needs") or []) if str(n).strip()]
    if needs:
        return needs[0]
    if not facts:
        return "Its session or its branch is gone."
    if facts.get("removed_at"):
        return "Its folder was thrown away."
    if merge_reversed(facts):
        return reversed_words(facts)
    if facts.get("merged_at"):
        return f"Already merged into {facts.get('merged_into') or 'its branch'}."
    if not facts.get("base"):
        return "It started from a commit, not from a branch, so there is nothing to merge it into."
    return ""


def other_start_words(task_base: str, base: str, *, other_repo: bool) -> str:
    """Why a passing task is left out of its folder's Merge all because it
    started somewhere else, and what the user does instead. Merge all makes
    ONE merge per folder (into the first passing task's branch), and nothing
    merges the others by itself: the words say so. Pure."""
    if other_repo:
        return ("It is in another repository, so Merge all leaves it out: open it to merge it "
                f"into {task_base or 'its branch'}.")
    return (f"It started from {task_base or 'another branch'}, not {base}, so Merge all leaves it out: "
            f"open it to merge it into {task_base or 'its branch'}.")


def stopped_tasks(records: list[Any]) -> dict[str, dict[str, Any]]:
    """The tasks a landing of a night stopped at because THEIR TESTS FAILED
    after merging them (records newest first; the newest record of a task
    wins), with the head it stopped on: such a task is not offered again
    while its head is the same. A stop that says nothing against the branch
    (its tests did not run, git could not merge it, a clash) is offered
    again: a clash that is still there shows on its step before any press,
    from the preview's own merge check. Pure."""
    out: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for rec in records:
        if getattr(rec, "undone_at", None) is not None:
            continue
        stopped = _loads(getattr(rec, "stopped_json", ""), {})
        tid = str(stopped.get("task_id") or "")
        if not tid or tid in seen:
            continue
        seen.add(tid)
        if stopped.get("why") == STEP_FAILED:
            out[tid] = stopped
    return out


def _queue_key(row: dict[str, Any], positions: dict[str, int]) -> tuple[int, str, str]:
    """The order the tasks were queued on Tonight: the task's position, then
    when it started (a task record pruned since: its start)."""
    pos = positions.get(str(row.get("id") or ""))
    return (pos if isinstance(pos, int) and pos > 0 else 1_000_000, str(row.get("started_at") or ""),
            str(row.get("id") or ""))


def night_counts(rows: list[dict[str, Any]], facts: dict[str, "dict[str, str] | None"],
                 records: list[Any]) -> dict[str, Any]:
    """The head line's numbers WITHOUT a git call (the summary is read every
    few seconds while on screen): per folder, how many of the night's rows
    not merged yet passed their tests (``green``) of how many (``total``).
    The preview (git) has the last word. Pure."""
    from .sessions import folder_key

    stopped = stopped_tasks(records)
    groups: dict[str, dict[str, Any]] = {}
    for r in rows:
        key = folder_key(str(r.get("folder") or ""))
        if not key:
            continue
        f = facts.get(str(r.get("thread_id") or ""))
        g = groups.setdefault(key, {"folder": str(r.get("folder") or ""), "folder_key": key,
                                    "folder_name": str(r.get("folder_name") or ""), "green": 0, "total": 0})
        if f and f.get("merged_at"):
            continue
        g["total"] += 1
        if not why_not(r, f) and str(r.get("id") or "") not in stopped:
            g["green"] += 1
    folders = [g for g in groups.values() if g["total"]]
    return {"folders": folders, "green": sum(g["green"] for g in folders),
            "total": sum(g["total"] for g in folders)}


def head_words(green: int, total: int) -> str:
    """"5 of 6 passed their tests." (the calm note of R3.5)."""
    if total <= 0:
        return ""
    if green == total:
        return f"{'All ' if total > 1 else ''}{_plural(total, 'task')} passed {'their' if total > 1 else 'its'} tests."
    return f"{green} of {total} passed their tests."


def button_words(n: int) -> str:
    """"Merge all 5" (VOCABULARY.md: Merge all that passed)."""
    return "Merge it" if n == 1 else f"Merge all {n}"


# --------------------------------------------------------------------------- #
# the preview (git reads only)
# --------------------------------------------------------------------------- #


def _rev(cwd: Any, ref: str) -> str:
    return B._ref_sha(Path(cwd), ref)


def _commits(cwd: Any, since: str, head: str) -> tuple[list[dict[str, str]], int]:
    got = B._g(["log", "--format=%h%x1f%s%x1e", "-n", str(COMMITS_SHOWN), f"{since}..{head}"], cwd, reads=True)
    out: list[dict[str, str]] = []
    if got.ok:
        for rec in got.out.split("\x1e"):
            bits = rec.strip("\n").split("\x1f")
            if len(bits) >= 2 and bits[0].strip():
                out.append({"sha": bits[0].strip(), "subject": B._masked(bits[1].strip(), 300)})
    count = B._g(["rev-list", "--count", f"{since}..{head}"], cwd, reads=True)
    total = int(count.out.strip() or 0) if count.ok and count.out.strip().isdigit() else len(out)
    return out, total


def _deps(files: list[dict[str, Any]]) -> list[str]:
    return [str(f["path"]) for f in files
            if str(f.get("path") or "").replace("\\", "/").rsplit("/", 1)[-1].lower() in DEPENDENCY_FILES
            or str(f.get("path") or "").replace("\\", "/").rsplit("/", 1)[-1].lower().startswith("requirements")]


def preview_id(night: str, folder: str, repo: str, base: str, base_sha: str, command: str,
               steps: list[dict[str, Any]]) -> str:
    """The preview's id: a hash of everything a landing would act on — the
    folder, the base and its commit, the command and every ready step's task
    and head, in order. Pure."""
    blob = json.dumps([night, folder, repo, base, base_sha, command,
                       [[s["task_id"], s["head"]] for s in steps if s.get("ready")]])
    return "lp_" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:20]


def command_for(engine: Any, folder: str) -> dict[str, Any]:
    """The test command run after each merge: the folder's own (22-a's
    ``proof.decide``: the command the user kept, or the one the folder
    proposes — the preview card shows it, and the press is the yes for this
    merge only; nothing is kept). ``{command, timeout_s, words}``. BLOCKING."""
    from ..hosted import proof as P

    got = P.decide(engine, folder, key=folder)
    state = got.get("state")
    if state in ("run", "ask") and str(got.get("command") or "").strip():
        command = str(got["command"]).strip()
        shown = P.shown_command(command)
        if shown != command:
            return {"command": "", "timeout_s": 0,
                    "words": "The folder's test command cannot be shown whole, so no tests run after each merge."}
        return {"command": command, "timeout_s": int(got.get("timeout_s") or P.TIMEOUT_S),
                "words": f"Tests run after each: `{shown}`"}
    if state == "off":
        why = ("the folder's own settings turn them off" if got.get("why") == P.WHY_FOLDER_FILE
               else "you turned them off for this folder")
        return {"command": "", "timeout_s": 0, "words": f"No tests run after each merge: {why}."}
    return {"command": "", "timeout_s": 0,
            "words": "No tests run after each merge: Iron Jarvis knows no test command for this folder."}


def preview(engine: Any, night: str, rows: list[dict[str, Any]], facts: dict[str, "dict[str, str] | None"],
            positions: dict[str, int], records: list[Any]) -> list[dict[str, Any]]:
    """The preview of night ``night``: one landing per folder (the folder the
    tasks worked in, and the base its first passing task started from), each
    with its ready steps in queue order, every row left out and why, the
    command, and what blocks it now (``blocked``: the base is gone, its
    checkout has changes, …). Reads only: no ref, index or file is written
    (git's merge check writes only tree objects). BLOCKING; a folder git
    cannot read is one landing that says so."""
    from .sessions import folder_key

    stopped = stopped_tasks(records)
    by_folder: dict[str, list[dict[str, Any]]] = {}
    for r in sorted(rows, key=lambda r: _queue_key(r, positions)):
        key = folder_key(str(r.get("folder") or ""))
        if key:
            by_folder.setdefault(key, []).append(r)
    out: list[dict[str, Any]] = []
    for key, group in by_folder.items():
        try:
            land = _preview_folder(engine, night, key, group, facts, stopped)
        except B.GitRefused as exc:
            folder = str(group[0].get("folder") or "")
            land = {"id": "", "night": night, "folder": folder, "folder_key": key,
                    "folder_name": os.path.basename(os.path.normpath(folder)) or folder, "steps": [], "left": [],
                    "merged": [], "ready": 0, "green": 0, "total": len(group), "blocked": str(exc)}
        except Exception as exc:  # noqa: BLE001 — one folder never hides another
            log.warning("the merge preview of %s could not be read", key, exc_info=True)
            folder = str(group[0].get("folder") or "")
            land = {"id": "", "night": night, "folder": folder, "folder_key": key,
                    "folder_name": os.path.basename(os.path.normpath(folder)) or folder, "steps": [], "left": [],
                    "merged": [], "ready": 0, "green": 0, "total": len(group),
                    "blocked": f"This folder could not be read ({type(exc).__name__})."}
        if land.get("total") or land.get("merged") or land.get("left"):
            out.append(land)
    return out


def _preview_folder(engine: Any, night: str, key: str, group: list[dict[str, Any]],
                    facts: dict[str, "dict[str, str] | None"], stopped: dict[str, dict[str, Any]]) -> dict[str, Any]:
    folder = str(group[0].get("folder") or "")
    left: list[dict[str, Any]] = []
    merged: list[dict[str, Any]] = []
    candidates: list[tuple[dict[str, Any], dict[str, str]]] = []
    done = 0  # merged (or merged, then reversed): not counted in the head line
    for r in group:
        f = facts.get(str(r.get("thread_id") or ""))
        item = {"task_id": str(r.get("id") or ""), "thread_id": str(r.get("thread_id") or ""),
                "title": str(r.get("title") or "Night task")}
        if f and f.get("merged_at"):
            done += 1
            if merge_reversed(f):
                left.append({**item, "why": reversed_words(f)})
            else:
                merged.append({**item, "into": f.get("merged_into") or ""})
            continue
        why = why_not(r, f)
        if why:
            left.append({**item, "why": why})
            continue
        candidates.append((r, f))  # type: ignore[arg-type]
    land: dict[str, Any] = {
        "night": night, "folder": folder, "folder_key": key,
        "folder_name": os.path.basename(os.path.normpath(folder)) or folder,
        "left": left, "merged": merged, "steps": [], "total": len(group) - done, "blocked": "",
    }
    if not candidates:
        land.update(id="", ready=0, green=0, repo="", base="", base_sha="", command="", command_words="",
                    into="", timeout_s=0)
        return land
    first = candidates[0][1]
    repo = first.get("main") or first.get("repo") or ""
    checkout = first.get("checkout") or folder
    base = first.get("base") or ""
    if not repo or not Path(repo).is_dir():
        raise B.GitRefused(f"The repository at {repo or folder} is not there right now, so nothing can be merged.")
    base_sha = _rev(repo, f"refs/heads/{base}")
    cmd = command_for(engine, checkout)
    land.update(repo=repo, checkout=checkout, base=base, base_sha=base_sha, into=into_words(base, checkout),
                command=cmd["command"], command_words=cmd["words"], timeout_s=cmd["timeout_s"])
    steps: list[dict[str, Any]] = []
    for r, f in candidates:
        item = {"task_id": str(r.get("id") or ""), "thread_id": str(r.get("thread_id") or ""),
                "title": str(r.get("title") or "Night task"), "branch": f.get("branch", "")}
        other_repo = os.path.normcase(os.path.normpath(f.get("main") or f.get("repo") or "")) != os.path.normcase(
            os.path.normpath(repo))
        if other_repo or (f.get("base") or "") != base:
            left.append({**item, "why": other_start_words(f.get("base") or "", base, other_repo=other_repo)})
            continue
        head = _rev(repo, f"refs/heads/{f.get('branch', '')}")
        if not head:
            left.append({**item, "why": "Its branch is gone."})
            continue
        before = stopped.get(item["task_id"])
        if before and str(before.get("head") or "") == head:
            left.append({**item, "why": f"Your last merge stopped at it: {before.get('words') or 'its tests failed'}"})
            continue
        if not base_sha:
            steps.append({**item, "head": head, "ready": False, "commits": [], "commit_count": 0, "files": [],
                          "file_count": 0, "added": 0, "removed": 0, "clash": [], "deps": []})
            continue
        if B._g(["merge-base", "--is-ancestor", head, base_sha], repo, reads=True).ok:
            left.append({**item, "why": f"{base} already has everything on it."})
            continue
        wt = Path(f.get("worktree", ""))
        if wt.is_dir() and not f.get("removed_at") and B._porcelain(wt):
            left.append({**item, "why": "Its copy of the folder has changes that are not committed yet. Open it "
                                        "and commit them first."})
            continue
        mb = B._g(["merge-base", base_sha, head], repo, reads=True)
        since = mb.out.strip() if mb.ok and mb.out.strip() else base_sha
        commits, commit_count = _commits(repo, base_sha, head)
        files = B.commit_files(repo, since, head, cap=FILES_SHOWN)
        check = B.merge_check(repo, base_sha, head)
        steps.append({
            **item, "head": head, "head_short": _short(head), "ready": bool(check["clean"]),
            "commits": commits, "commit_count": commit_count,
            "files": files["files"], "file_count": files["count"], "added": files["added"],
            "removed": files["removed"],
            "clash": list(check["files"]), "clash_words": "" if check["clean"] else clash_words(check["files"]),
            "deps": _deps(files["files"]),
        })
    ready = [s for s in steps if s.get("ready")]
    land["steps"] = steps
    land["ready"] = len(ready)
    land["green"] = len(steps)
    land["blocked"] = _blocked(repo, base, base_sha)
    land["base_short"] = _short(base_sha)
    if any(s.get("deps") for s in ready):
        land["deps_words"] = (
            "A branch here changes a dependency file. Its tests run with your folder's installed packages, "
            "not new ones."
        )
    land["id"] = preview_id(night, key, repo, base, base_sha, cmd["command"], steps) if ready else ""
    return land


def _blocked(repo: str, base: str, base_sha: str) -> str:
    """What keeps the landing from starting NOW, in words ("" = nothing)."""
    if not base_sha:
        return NO_BASE.format(base=base or "its base")
    where = B._worktree_of_branch(Path(repo), base)
    if where and not Path(where).is_dir():
        return NOT_HERE.format(base=base, where=where)
    if where and B._porcelain(Path(where), untracked=False):
        return CHECKOUT_DIRTY.format(base=base, where=where)
    return ""


# --------------------------------------------------------------------------- #
# the landing (git writes: the copy, then the base once)
# --------------------------------------------------------------------------- #


def _ident(cwd: Any) -> list[str]:
    from ..git.integration import default_identity, git_env, has_identity

    return [] if has_identity(Path(cwd), git_env()) else default_identity()


def _no_hooks() -> list[str]:
    """The repository's own hooks stay out of the landing copy (``-c
    core.hooksPath`` at the null device, as ``git_read`` does): its merges
    run nothing of the repository's; the user's checkout keeps its hooks."""
    return ["-c", f"core.hooksPath={os.devnull}"]


def _back_to(copy: Path, tip: str) -> None:
    """Take a step back in the LANDING COPY only (it is Iron Jarvis's own,
    made for this landing): no merge left half-done, HEAD at ``tip``."""
    B._g(["merge", "--abort"], copy)
    B._g(["reset", "-q", "--hard", tip], copy)


def overwritten_files(err: str) -> list[str]:
    """The files git names in its "… would be overwritten by merge:" refusal
    (each on its own line, indented, until "Please move or remove them …"),
    masked and capped, so the words name them on screen. Pure."""
    out: list[str] = []
    listing = False
    for line in str(err or "").splitlines():
        if "would be overwritten" in line:
            listing = True
            continue
        if not listing:
            continue
        if not line[:1].isspace():
            break
        name = line.strip()
        if name and name not in out:
            out.append(B._masked(name, 300))
        if len(out) >= 50:
            break
    return out


def move_base(repo: str, base: str, old: str, new: str, *, back: bool = False) -> str:
    """Move ``base`` from ``old`` to ``new`` ONCE, never over a moved base.
    Checked out (the user's checkout, clean): ``merge --ff-only`` there (their
    own hooks run) — or, for Undo's put-back (``back``), ``reset --keep``.
    Checked out nowhere: git's own guard (a dry-run fetch, refused while any
    checkout uses the base — a rebase under way) and ``update-ref`` with the
    old value. Returns where it was checked out ("" = nowhere);
    :class:`GitRefused` in words with the base as it was. BLOCKING."""
    rp = Path(repo)
    if _rev(rp, f"refs/heads/{base}") != old:
        raise B.GitRefused(CHANGED_SINCE.format(base=base), 409)
    where = B._worktree_of_branch(rp, base)
    if where:
        place = Path(where)
        if not place.is_dir():
            raise B.GitRefused(NOT_HERE.format(base=base, where=where), 409)
        if B._porcelain(place, untracked=False):
            raise B.GitRefused(CHECKOUT_DIRTY.format(base=base, where=where), 409)
        if _rev(place, "HEAD") != old:
            raise B.GitRefused(CHANGED_SINCE.format(base=base), 409)
        if back:
            got = B._g(["reset", "-q", "--keep", new], place, timeout=B.START_S)
        else:
            got = B._g([*_ident(place), "merge", "--ff-only", "--no-edit", new], place, timeout=B.START_S)
        if not got.ok:
            now = _rev(place, "HEAD")
            if now == old:
                words = f"Git did not move your checkout of {base} at {where}, so it is as it was."
                if "would be overwritten" in (got.err or ""):
                    names = overwritten_files(got.err or "")
                    which = f" ({_paths_words(names)})" if names else ""
                    words = (f"Your checkout of {base} at {where} has files that are not in git which this would "
                             f"overwrite{which}, so it is as it was. Move them away first.")
                raise B.GitRefused(words, 409, B._masked(got.err or got.out))
            if now != new:
                raise B.GitRefused(f"Your checkout of {base} at {where} did not end where it should. Look at it "
                                   "before you go on.", 409, B._masked(got.err or got.out))
        return where
    spec = f"{'+' if back else ''}{new}:refs/heads/{base}"
    guard = B._g(["fetch", "--dry-run", "--no-tags", "--no-write-fetch-head", "--no-recurse-submodules", ".",
                  spec], rp)
    if not guard.ok:
        if "refusing to fetch into branch" in (guard.err or ""):
            raise B.GitRefused(f"{base} is in use in one of your checkouts (a rebase or a bisect may be under way), "
                               "so it was not changed. Finish that first.", 409, B._masked(guard.err))
        raise B.GitRefused(CHANGED_SINCE.format(base=base), 409, B._masked(guard.err or guard.out))
    moved = B._g(["update-ref", "-m", "Iron Jarvis: merge all that passed" if not back else "Iron Jarvis: undo",
                  f"refs/heads/{base}", new, old], rp)
    if not moved.ok:
        raise B.GitRefused(CHANGED_SINCE.format(base=base), 409, B._masked(moved.err))
    return ""


def _step_why(step: dict[str, Any]) -> str:
    """Why a landing stopped at a step, in words (no full stop)."""
    state = step.get("state")
    if state == STEP_CLASH:
        return f"merging it would clash in {_paths_words(list(step.get('clash') or []))}"
    if state == STEP_MERGE_FAILED:
        return "git could not merge it"
    rec = step.get("check") if isinstance(step.get("check"), dict) else {}
    if state == STEP_FAILED:
        failed = rec.get("failed")
        if isinstance(failed, int) and failed > 0:
            return f"{_plural(failed, 'test')} failed after merging it"
        return "its tests ended with an error after merging it"
    if state == STEP_NOT_CHECKED:
        line = str(rec.get("line") or "")
        from ..hosted import proof as P

        if line.startswith(P.NOT_CHECKED):
            line = line[len(P.NOT_CHECKED):].strip()
        return "its tests did not run after merging it" + (f" ({line.rstrip('.')})" if line else "")
    return "it did not merge"


def result_words(base: str, before: str, after: str, merged: int, stopped: "dict[str, Any] | None",
                 *, moved: bool, note: str = "") -> str:
    """The result line (25-a, with VOCABULARY.md's Merged). Pure."""
    push = PUSH_WORDS.format(base=base)
    if note:
        return note
    if not stopped:
        return f"Merged {merged}: {base} moved from {_short(before)} to {_short(after)}. {push}"
    at = f"Stopped at {_quote(stopped.get('title', ''))}: {stopped.get('reason') or 'it did not merge'}."
    if merged and moved:
        return (f"Merged {merged}. {at} Nothing after it was merged. {base} moved from {_short(before)} to "
                f"{_short(after)}. {push}")
    return f"Nothing was merged. {at} {base} is as it was."


def run(engine: Any, home: Any, record_id: str, land: dict[str, Any], *, stop: threading.Event,
        emergency: Callable[[], bool], record_session: Callable[[str, dict[str, Any]], None]) -> dict[str, Any]:
    """MERGE ALL THAT PASSED for one previewed landing (``land``, a fresh
    :func:`preview` whose id the press named). BLOCKING (minutes: the tests
    run after each merge); never raises — the record says what happened.

    ``stop`` (Iron Jarvis closing) and ``emergency()`` (Emergency stop) end it
    between steps and kill a running test's tree (a watcher thread holds its
    child through ``run_checks``' ``on_proc``, and kills it within
    :data:`LOOK_S`); the base is then NOT moved (the merges made so far stay
    on the landing copy's branch). ``record_session
    (thread_id, changes)`` writes a merged task's ``merged_at`` /
    ``merged_into`` / ``merge_commit`` on its session's git facts (the caller
    holds its git lock)."""
    from ..hosted import proof as P

    repo, base, before = str(land["repo"]), str(land["base"]), str(land["base_sha"])
    checkout = str(land.get("checkout") or land["folder"])
    command, timeout_s = str(land.get("command") or ""), float(land.get("timeout_s") or P.TIMEOUT_S)
    ready = [dict(s) for s in land["steps"] if s.get("ready")]
    steps = [{"task_id": s["task_id"], "thread_id": s["thread_id"], "title": s["title"], "branch": s["branch"],
              "head": s["head"], "state": STEP_WAITING, "merge_commit": "", "seconds": None, "line": "",
              "files": s.get("file_count", 0)} for s in ready]
    update_record(engine, record_id, steps=steps)
    watcher_done = threading.Event()
    halted = {"why": ""}
    # The running test's child (``run_checks``' ``on_proc``): ``stop`` alone
    # only ends the WAIT for it (review 1 of 25-a), so the watcher kills its
    # tree the moment Emergency stop is on or Iron Jarvis closes.
    child: dict[str, Any] = {"proc": None, "killed": None}
    child_lock = threading.Lock()

    def hold(proc: Any) -> None:
        with child_lock:
            child["proc"] = proc

    def kill_child() -> None:
        """Kill the running test's tree, once per child (the watcher's own
        thread: never the loop). Never raises."""
        with child_lock:
            proc = child["proc"]
            if proc is None or child["killed"] is proc:
                return
            child["killed"] = proc
        try:
            from ..core import procs

            procs.kill_tree(proc)
        except Exception:  # noqa: BLE001 — the run's own bound still ends it
            log.debug("a merge's test could not be stopped", exc_info=True)

    def watch() -> None:
        while not watcher_done.wait(LOOK_S):
            try:
                if not stop.is_set() and emergency():
                    halted["why"] = halted["why"] or "emergency"
                    stop.set()
            except Exception:  # noqa: BLE001 — an unreadable switch is read again
                pass
            if stop.is_set():
                kill_child()

    watcher = threading.Thread(target=watch, name="ij-merge-all-watch", daemon=True)
    watcher.start()

    def emergency_now() -> bool:
        try:
            return bool(emergency())
        except Exception:  # noqa: BLE001 — unreadable: read again by the watcher
            return False

    def halt_why() -> str:
        return halted["why"] or ("emergency" if emergency_now() else "closing")

    copy_facts: "dict[str, str] | None" = None
    stopped: "dict[str, Any] | None" = None
    last_good = before
    moved = False
    note = ""
    try:
        copy_facts = B.start(checkout, title=f"merge-{land['night']}", start_point=before, base=base)
        copy = Path(copy_facts["worktree"])
        work = Path(copy_facts.get("folder") or copy_facts["worktree"])
        update_record(engine, record_id, landing_branch=copy_facts["branch"])
        for step in steps:
            if stop.is_set() or emergency_now():
                halted["why"] = halt_why()
                break
            started = time.monotonic()
            step["state"] = STEP_MERGING
            update_record(engine, record_id, steps=steps)
            tip = _rev(copy, "HEAD")
            check = B.merge_check(copy, tip, step["head"])
            if not check["clean"]:
                step.update(state=STEP_CLASH, clash=list(check["files"]),
                            line=clash_words(list(check["files"])), seconds=round(time.monotonic() - started, 2))
                stopped = step
                break
            message = f"Merge branch '{step['branch']}' into {base}"
            got = B._g([*_ident(copy), *_no_hooks(), "merge", "--no-ff", "--no-edit", "-m", message, step["head"]],
                       copy, timeout=B.START_S)
            if not got.ok:
                _back_to(copy, tip)
                step.update(state=STEP_MERGE_FAILED, line="Git could not merge it.",
                            seconds=round(time.monotonic() - started, 2))
                stopped = step
                break
            merged_sha = _rev(copy, "HEAD")
            if command:
                step["state"] = STEP_TESTING
                update_record(engine, record_id, steps=steps)
                res = P.run_checks(str(work), command, f"merge-{record_id}", timeout_s, checkout=checkout,
                                   stop=stop, on_proc=hold, busy_wait_s=CHECK_BUSY_WAIT_S)
                with child_lock:
                    child["proc"] = None
                if res.stopped or stop.is_set():
                    _back_to(copy, tip)
                    step.update(state=STEP_SKIPPED, seconds=round(time.monotonic() - started, 2))
                    halted["why"] = halt_why()
                    break
                proof = P.from_run("", command, res, timeout_s=timeout_s)
                rec = {k: proof.record.get(k) for k in ("tests", "why", "ran", "passed", "failed", "line", "exit")}
                step["check"] = rec
                step["line"] = str(rec.get("line") or "")
                # Only a pass, or a run that found no tests at all (the same as
                # a folder with no test command, said on its line), goes on.
                if rec.get("tests") not in (P.TESTS_PASSED, P.TESTS_NONE):
                    _back_to(copy, tip)
                    step.update(state=STEP_FAILED if rec.get("tests") == P.TESTS_FAILED else STEP_NOT_CHECKED,
                                seconds=round(time.monotonic() - started, 2))
                    stopped = step
                    break
            step.update(state=STEP_MERGED, merge_commit=merged_sha, seconds=round(time.monotonic() - started, 2))
            last_good = merged_sha
            update_record(engine, record_id, steps=steps)
        for step in steps:
            if step["state"] in (STEP_WAITING, STEP_MERGING, STEP_TESTING):
                step["state"] = STEP_SKIPPED
        merged = [s for s in steps if s["state"] == STEP_MERGED]
        if halted["why"]:
            what = ("Emergency stop was turned on" if halted["why"] == "emergency"
                    else "Iron Jarvis closed")
            kept = f" The merges made so far are on {copy_facts['branch']}." if merged else ""
            note = f"{what} while merging, so {base} was not changed.{kept}"
        elif merged:
            B._g(["update-ref", f"{REF_ROOT}/{record_id}/before", before], repo)
            update_record(engine, record_id, state=MOVING, base_after=last_good, steps=steps)
            try:
                where = move_base(repo, base, before, last_good)
                moved = True
                update_record(engine, record_id, where=where)
            except B.GitRefused as exc:
                note = f"{exc} The merges that passed are kept on {copy_facts['branch']}."
                update_record(engine, record_id, base_after="")
            if moved:
                stamp = utcnow().isoformat()
                for s in merged:
                    try:
                        record_session(s["thread_id"], {"merged_at": stamp, "merged_into": base,
                                                        "merge_commit": s["merge_commit"][:12]})
                    except Exception:  # noqa: BLE001 — the base moved; the row's word is the record's
                        log.warning("a merged night task's session could not be marked", exc_info=True)
    except B.GitRefused as exc:
        note = f"Nothing was merged: {str(exc).rstrip()} {base} is as it was."
        log.info("a merge all could not start or go on: %s", exc)
    except Exception as exc:  # noqa: BLE001 — the record says it, never a crash
        log.warning("merge all %s failed", record_id, exc_info=True)
        if not moved:
            note = f"Nothing was merged: the merge stopped with an error ({type(exc).__name__}). {base} is as it was."
    finally:
        watcher_done.set()
    if not moved and stopped is None and not note:
        note = f"Nothing was merged. {base} is as it was."
    if not moved:
        _copy_only(steps)
    stopped_view = None
    if stopped is not None:
        stopped_view = {"task_id": stopped["task_id"], "title": stopped["title"], "head": stopped["head"],
                        "why": stopped["state"], "reason": _step_why(stopped),
                        "words": _step_why(stopped)[:1].upper() + _step_why(stopped)[1:] + ".",
                        "files": list(stopped.get("clash") or [])}
    merged_n = sum(1 for s in steps if s["state"] == STEP_MERGED) if moved else 0
    words = result_words(base, before, last_good, merged_n, stopped_view, moved=moved, note=note)
    trash = ""
    if copy_facts is not None:
        try:
            trash = str(B.remove_worktree(home, copy_facts).get("trash") or "")
        except B.GitRefused as exc:
            words += f" Its working copy is still at {copy_facts['worktree']} ({exc})"
        except Exception:  # noqa: BLE001
            log.warning("a merge's working copy could not be moved to the trash", exc_info=True)
    final = update_record(engine, record_id, state=MERGED if moved else STOPPED, steps=steps, stopped=stopped_view,
                          words=words, landing_trash=trash, ended_at=utcnow(),
                          **({} if moved else {"base_after": ""}))
    return final or {"id": record_id, "words": words}


# --------------------------------------------------------------------------- #
# Undo (D-25.1)
# --------------------------------------------------------------------------- #


def _pushed(repo: str, sha: str) -> bool:
    """Does any remote branch this repository knows of hold ``sha``? (The
    last fetch's knowledge; no network call.)"""
    got = B._g(["for-each-ref", "--contains", sha, "--format=%(refname)", "refs/remotes/"], repo, reads=True)
    return bool(got.ok and got.out.strip())


def undo_plan(rec: Any) -> dict[str, Any]:
    """Which Undo a landing gets, in words, BEFORE the press (D-25.1):
    ``{how: put_back|reverse|refused|"", words, button}``. BLOCKING (reads)."""
    if rec.undone_at is not None or rec.state != MERGED or not rec.base_after:
        return {"how": "", "words": "", "button": ""}
    repo, base = rec.repo, rec.base
    if not repo or not Path(repo).is_dir():
        return {"how": UNDO_REFUSED, "button": "",
                "words": f"The repository at {repo} is not there right now, so this cannot be undone now."}
    steps = [s for s in _loads(rec.steps_json, []) if isinstance(s, dict) and s.get("state") == STEP_MERGED]
    cur = _rev(repo, f"refs/heads/{base}")
    if not cur:
        return {"how": UNDO_REFUSED, "button": "", "words": NO_BASE.format(base=base)}
    where = B._worktree_of_branch(Path(repo), base)
    if where and not Path(where).is_dir():
        return {"how": UNDO_REFUSED, "button": "", "words": NOT_HERE.format(base=base, where=where)
                .replace("so nothing was merged", "so this cannot be undone now")}
    if where and B._porcelain(Path(where), untracked=False):
        return {"how": UNDO_REFUSED, "button": "",
                "words": f"Your checkout of {base} at {where} has changes that are not committed, so Undo cannot "
                         "run now. Commit or put them away first."}
    if not B._g(["merge-base", "--is-ancestor", rec.base_after, cur], repo, reads=True).ok:
        return {"how": UNDO_REFUSED, "button": "",
                "words": f"{base} no longer holds these merges, so there is nothing to undo."}
    if cur == rec.base_after and not _pushed(repo, rec.base_after):
        return {"how": UNDO_PUT_BACK, "button": f"Undo: put {base} back at {_short(rec.base_before)}",
                "words": f"Undo puts {base} back at {_short(rec.base_before)}, exactly as it was before."}
    why = ("something came after it on " + base) if cur != rec.base_after else "it was pushed"
    n = len(steps)
    return {"how": UNDO_REVERSE, "button": f"Undo: add {_plural(n, 'reverse commit')}",
            "words": f"Since {why}, Undo adds {_plural(n, 'commit')} to {base} that reverse "
                     f"{'this merge' if n == 1 else 'these merges'}; nothing is taken out of its history."}


def undo(engine: Any, home: Any, record_id: str, *, how: str,
         record_session: Callable[[str, dict[str, Any]], None]) -> dict[str, Any]:
    """UNDO a landing the way :func:`undo_plan` said (``how`` must still be
    the plan's: a plan that changed since it was shown refuses in words).
    :class:`GitRefused` in words with nothing changed. BLOCKING."""
    rec = get_record(engine, record_id)
    if rec is None:
        raise B.GitRefused("There is no such merge.", 404)
    plan = undo_plan(rec)
    if not plan["how"]:
        raise B.GitRefused(NO_UNDO, 409)
    if plan["how"] == UNDO_REFUSED:
        raise B.GitRefused(plan["words"], 409)
    if plan["how"] != how:
        raise B.GitRefused(f"Something changed since you looked: {plan['words']} Press Undo again.", 409)
    repo, base = rec.repo, rec.base
    steps = [s for s in _loads(rec.steps_json, []) if isinstance(s, dict) and s.get("state") == STEP_MERGED]
    if how == UNDO_PUT_BACK:
        move_base(repo, base, rec.base_after, rec.base_before, back=True)
        words = f"Undone: {base} is back at {_short(rec.base_before)}, exactly as it was. Nothing was pushed."
        commit = rec.base_before
    else:
        cur = _rev(repo, f"refs/heads/{base}")
        checkout = rec.folder if Path(rec.folder or "").is_dir() else repo
        copy_facts = B.start(checkout, title=f"undo-{rec.night}", start_point=cur, base=base)
        copy = Path(copy_facts["worktree"])
        try:
            for s in reversed(steps):
                got = B._g([*_ident(copy), *_no_hooks(), "revert", "-m", "1", "--no-edit", s["merge_commit"]],
                           copy, timeout=B.START_S)
                if not got.ok:
                    B._g(["revert", "--abort"], copy)
                    raise B.GitRefused(
                        f"Reversing {_quote(s.get('title', ''))} would clash with what came after it on {base}, "
                        f"so nothing was changed. {base} is as it was.", 409, B._masked(got.err or got.out))
            commit = _rev(copy, "HEAD")
            move_base(repo, base, cur, commit)
        finally:
            with contextlib.suppress(Exception):
                B.remove_worktree(home, copy_facts)
        words = (f"Undone: {_plural(len(steps), 'commit')} on {base} reverse "
                 f"{'the merge' if len(steps) == 1 else 'the merges'}. Nothing was pushed.")
    for s in steps:
        with contextlib.suppress(Exception):
            if how == UNDO_PUT_BACK:
                # The base is back where it was: the branch is not on it any
                # more, so it is offered again.
                record_session(s["thread_id"], {"merged_at": None, "merged_into": None, "merge_commit": None,
                                                "merge_reversed": None})
            else:
                # Reversed, not taken out: its commits are still in the base's
                # history (git cannot merge them again), so the row says it
                # was merged and then reversed (review 2 of 25-a).
                record_session(s["thread_id"], {"merge_reversed": str(s.get("merge_commit") or "")[:12]})
    out = update_record(engine, record_id, undone_at=utcnow(), undo_how=how, undo_words=words)
    return {**(out or {}), "undo_commit": _short(commit)}


def _copy_only(steps: list[Any]) -> list[Any]:
    """The base did not move: a step merged in the copy says it is on the
    copy's branch only, never plain "merged" beside "main was not changed"."""
    for s in steps:
        if isinstance(s, dict) and s.get("state") == STEP_MERGED:
            s["state"] = STEP_COPY_ONLY
    return steps


def settle_stale(engine: Any, rec: Any, active: "set[str]", *, git: bool = False,
                 record_session: "Callable[[str, dict[str, Any]], None] | None" = None) -> Any:
    """A landing whose run is not in this process (Iron Jarvis closed during
    it): ``running`` never moved the base — it is stopped, said so; a
    ``moving`` one is read from git (``git``): the base where the landing
    left it = merged (each merged step's session then gets ``merged_at`` /
    ``merged_into`` / ``merge_commit`` through ``record_session``, as the run
    would have written them), else stopped. Returns the record as it is now.
    BLOCKING; never raises."""
    try:
        if rec.id in active or rec.state not in (RUNNING, MOVING):
            return rec
        if rec.state == MOVING and not git:
            return rec
        # The merges are on the base when the commit the landing left it at
        # is in the base's history now: a commit the user made on it since
        # (before this was read) never makes it read as "not changed" (review
        # 3 of 25-a; undo_plan's own read).
        cur = _rev(rec.repo, f"refs/heads/{rec.base}") if rec.state == MOVING and rec.base_after else ""
        moved = bool(cur) and (cur == rec.base_after or B._g(
            ["merge-base", "--is-ancestor", rec.base_after, cur], rec.repo, reads=True).ok)
        if moved:
            merged_steps = [s for s in _loads(rec.steps_json, [])
                            if isinstance(s, dict) and s.get("state") == STEP_MERGED]
            words = rec.words or result_words(rec.base, rec.base_before, rec.base_after, len(merged_steps),
                                              None, moved=True)
            if record_session is not None:
                stamp = (rec.updated_at or utcnow()).isoformat()
                for s in merged_steps:
                    try:
                        record_session(str(s.get("thread_id") or ""),
                                       {"merged_at": stamp, "merged_into": rec.base,
                                        "merge_commit": str(s.get("merge_commit") or "")[:12]})
                    except Exception:  # noqa: BLE001 — the record still says merged
                        log.warning("a merged night task's session could not be marked", exc_info=True)
            update_record(engine, rec.id, state=MERGED, words=words, ended_at=utcnow())
        else:
            update_record(engine, rec.id, state=STOPPED, base_after="", ended_at=utcnow(),
                          steps=_copy_only(_loads(rec.steps_json, [])),
                          words=f"Iron Jarvis closed while merging, so {rec.base} was not changed.")
        return get_record(engine, rec.id) or rec
    except Exception:  # noqa: BLE001
        log.warning("a merge left by a closed Iron Jarvis could not be settled", exc_info=True)
        return rec


__all__ = [
    "LandRecord", "MERGED", "MOVING", "RUNNING", "STOPPED", "UNDO_PUT_BACK", "UNDO_REFUSED", "UNDO_REVERSE",
    "button_words", "clash_words", "command_for", "create_record", "get_record", "head_words", "into_words",
    "move_base", "night_counts", "preview", "preview_id", "records_for_night", "result_words", "run",
    "settle_stale", "stopped_tasks", "undo", "undo_plan", "update_record", "view", "why_not",
]
