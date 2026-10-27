"""A Build session's own git BRANCH (v1.375.0, IJ harness part 2 Phase 4).

A session made in a folder inside a git repository works on its own branch
``ij/<name>`` in a worktree beside the repository (``harness/branches`` —
the user's decisions 1–3). These routes are the person's buttons; each one
runs git through ``core.procs`` OFF the event loop, bounded, and answers a
refusal in ONE plain sentence (``detail``) with git's own output, masked,
under ``details``:

* ``GET /build/git/folder?path=`` — New session's question: is this folder
  in a repository, and can a session branch from it (``harness/branches.
  inspect_folder`` — git installed, the repository's top folder, its
  current branch, a note when no branch can be made);
* ``GET /build/sessions/{id}/git`` — the branch: commits ahead of its base,
  files not committed, the remote and whether it has the branch, where the
  base is checked out, the commits, a pull request's prefill, the compare
  link, and whether the GitHub CLI is installed and signed in;
* ``GET /build/sessions/{id}/git/diff`` — the BRANCH VIEW: every file the
  branch changed against its base (where they meet), committed and not, in
  the Changes pane's own shape;
* ``POST …/git/draft`` — the session's OWN model drafts a commit message
  (one pinned call under the session's hold, account pin and Local only
  scope, billed to the session; the demo model drafts nothing and says so);
* ``POST …/git/commit {message}`` — commit everything in the worktree;
* ``POST …/git/push`` — ``git push -u <remote> ij/<name>`` (never a force);
* ``POST …/git/pr {title, body}`` and ``GET …/git/pr`` — a pull request
  through the GitHub CLI (signed in) and its checks; otherwise the compare
  link is what the screen offers;
* ``POST …/git/merge`` — a normal merge into the base, refused in words with
  nothing changed when it cannot go through cleanly;
* ``POST …/git/discard {confirm: true}`` — the worktree folder to the trash,
  git forgets that worktree (only it), the branch kept, the session archived.

A session that works in its folder directly answers 409 to each of these.
Every action that changes something holds the session (no turn, rewind or
Compact starts meanwhile) and is refused while a turn runs.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ...core.ids import utcnow
from ...harness import branches as B
from ...harness import sessions as S

log = logging.getLogger(__name__)

GIT_RUNNING = (
    "This session is still answering. Wait for the reply, or stop it, before you change its branch."
)
CONFIRM_DISCARD = "Press Discard twice to confirm."
DEMO_DRAFT = (
    "The demo model does not draft commit messages. Write the message yourself, or choose a "
    "real model for this session."
)
NO_DRAFT = "The model wrote no message. Write one yourself."
DRAFT_FAILED = "The message could not be drafted. Write one yourself."
#: How long the draft may take.
DRAFT_TIMEOUT_S = 120.0

DRAFT_SYSTEM = (
    "You write git commit messages. Given the changes below, write ONE commit message: a "
    "summary line of at most 72 characters in the imperative mood (\"Add …\", \"Fix …\"), then, "
    "only when it helps, a blank line and a few short lines saying what changed and why. "
    "Plain text only: no code fences, no quotes around it, no sign-off."
)


class CommitBody(BaseModel):
    message: str = Field(default="", max_length=B.MAX_MESSAGE_CHARS + 100)


class PrBody(BaseModel):
    title: str = Field(default="", max_length=B.MAX_TITLE_CHARS + 100)
    body: str = Field(default="", max_length=B.MAX_BODY_CHARS + 100)


class DiscardBody(BaseModel):
    confirm: bool = False
    #: The words only: "remove the worktree" after a merge, or "discard".
    after_merge: bool = False


#: v1.379.0: the git buttons' refusal for a session started with no folder.
NO_FOLDER_GIT = "This session has no folder of its own, so it is not in a repository and has no branch."


def no_branch_words(row: Any) -> str:
    """Why a session has no branch: it works in its folder directly, or
    (v1.379.0) it was started with no folder at all."""
    return NO_FOLDER_GIT if S.is_folderless(row) else B.NO_BRANCH


def _refused(exc: B.GitRefused) -> JSONResponse:
    content: dict[str, Any] = {"detail": str(exc)}
    if exc.details:
        content["details"] = exc.details
    return JSONResponse(status_code=exc.status, content=content)


#: ONE git action at a time per session (the e2e's finding, v1.375.0): the
#: Branch view re-reads the branch the moment Merge answers — a dozen git
#: calls whose working folder is the worktree — while the user presses
#: "Remove the worktree"; a folder some process is IN cannot be renamed on
#: Windows. Reads and actions of one session take its lock (in the worker
#: thread, never on the loop), so a discard waits for a read in flight.
_GIT_LOCKS: dict[str, threading.Lock] = {}
_GIT_LOCKS_GUARD = threading.Lock()
#: The sessions a git ACTION (Commit, Push, Create PR, Merge, Discard) is
#: running for right now — on the loop thread only. Review (v1.375.0): a
#: second press while one ran was refused as "still answering" (the turn's
#: words), because both share ``_STARTING``; it says what is busy now.
_GIT_ACTING: set[str] = set()
#: How long an action waits for the session's lock before it says so.
GIT_LOCK_WAIT_S = 120.0
GIT_BUSY = "This session's branch is busy with another git action. Try again in a moment."


def _locked(thread_id: str, fn: Any) -> Any:
    """``fn()`` under the session's git lock. BLOCKING (a worker thread)."""
    with _GIT_LOCKS_GUARD:
        lock = _GIT_LOCKS.setdefault(thread_id, threading.Lock())
    if not lock.acquire(timeout=GIT_LOCK_WAIT_S):
        raise B.GitRefused(GIT_BUSY, 409)
    try:
        return fn()
    finally:
        lock.release()


def register(app: FastAPI, d: Any) -> None:
    from .build import _COMPACTING, _STARTING, COMPACTING_SENTENCE, _running_turn, _setup_of

    engine_of = lambda: d.platform.engine  # noqa: E731 — read live (tests swap the platform)

    def _row_facts(thread_id: str) -> "tuple[Any, dict[str, str]]":
        row = S.get_row(engine_of(), thread_id)
        if row is None:
            raise HTTPException(status_code=404, detail="no such Build session")
        facts = B.facts_of(_setup_of(row))
        if facts is None:
            raise HTTPException(status_code=409, detail=no_branch_words(row))
        return row, facts

    def _record(thread_id: str, facts: dict[str, str], **changes: Any) -> None:
        """Write what happened onto the session's facts (the /build routes'
        own writer). BLOCKING. Review (v1.375.0): onto the facts AS STORED
        NOW, not the copy this request read before it waited its turn — a
        Merge recorded over a Create PR that landed meanwhile dropped its
        ``pr_url`` (and a Discard a merge's ``merged_at``)."""
        row = S.get_row(engine_of(), thread_id)
        fresh = (B.facts_of(_setup_of(row)) if row is not None else None) or facts
        merged = {**fresh, **{k: v for k, v in changes.items() if v is not None}}
        S.update(engine_of(), thread_id, setup={B.SETUP_KEY: merged})

    def _busy(thread_id: str) -> "JSONResponse | None":
        if thread_id in _GIT_ACTING:
            return JSONResponse(status_code=409, content={"detail": GIT_BUSY})
        if thread_id in _COMPACTING:
            return JSONResponse(status_code=409, content={"detail": COMPACTING_SENTENCE})
        if thread_id in _STARTING or _running_turn(d, thread_id):
            return JSONResponse(status_code=409, content={"detail": GIT_RUNNING})
        return None

    async def _held(thread_id: str, fn: Any) -> Any:
        """Run ``fn`` (blocking) off the loop while the session is HELD — no
        turn, rewind or Compact starts meanwhile — and answer a refusal in
        words."""
        busy = _busy(thread_id)
        if busy is not None:
            return busy
        _STARTING.add(thread_id)
        _GIT_ACTING.add(thread_id)
        try:
            return await asyncio.to_thread(_locked, thread_id, fn)
        except B.GitRefused as exc:
            return _refused(exc)
        finally:
            _GIT_ACTING.discard(thread_id)
            _STARTING.discard(thread_id)

    # ------------------------------------------------------------------ #
    # reading
    # ------------------------------------------------------------------ #

    @app.get("/build/git/folder")
    def git_folder(path: str = "") -> dict[str, Any]:
        """New session's question about a folder (``branches.inspect_folder``).
        A folder Iron Jarvis may not read answers "not in a repository"."""
        from ...core.fs_policy import fs_read_ok

        folder = S.resolved_folder(path) if path.strip() else ""
        if folder:
            ok, _why = fs_read_ok(folder)
            if not ok:
                return {**B.inspect_folder(""), "git": bool(B.git_path())}
        return B.inspect_folder(folder)

    @app.get("/build/sessions/{thread_id}/git")
    async def git_status(thread_id: str) -> Any:
        """The session's branch as the Branch view shows it."""
        _row, facts = await asyncio.to_thread(_row_facts, thread_id)
        try:
            return await asyncio.to_thread(_locked, thread_id, lambda: B.status(facts))
        except B.GitRefused as exc:
            return _refused(exc)

    @app.get("/build/sessions/{thread_id}/git/diff")
    async def git_diff(thread_id: str) -> Any:
        """The Branch view's files: the branch against its base, committed and
        not (the Changes pane's shape)."""
        _row, facts = await asyncio.to_thread(_row_facts, thread_id)
        try:
            return await asyncio.to_thread(_locked, thread_id, lambda: B.branch_changes(facts))
        except B.GitRefused as exc:
            return _refused(exc)

    # ------------------------------------------------------------------ #
    # commit
    # ------------------------------------------------------------------ #

    @app.post("/build/sessions/{thread_id}/git/draft")
    async def git_draft(thread_id: str) -> Any:
        """The session's own model drafts a commit message for the changes
        not committed yet — ONE pinned call on the session's pair, under its
        hold, account pin and Local only scope, billed to the session. The
        demo model drafts nothing (``drafted`` False and a note in words)."""
        busy = _busy(thread_id)
        if busy is not None:
            return busy
        return await draft_message(d, thread_id)

    @app.post("/build/sessions/{thread_id}/git/commit")
    async def git_commit(thread_id: str, body: CommitBody) -> Any:
        """Commit everything in the worktree on the session's branch with the
        user's (edited) message."""
        _row, facts = await asyncio.to_thread(_row_facts, thread_id)

        def run() -> dict[str, Any]:
            done = B.commit(facts, body.message)
            return {**done, "status": B.status(facts)}

        return await _held(thread_id, run)

    # ------------------------------------------------------------------ #
    # push and pull request
    # ------------------------------------------------------------------ #

    @app.post("/build/sessions/{thread_id}/git/push")
    async def git_push(thread_id: str) -> Any:
        """``git push -u <remote> ij/<name>`` — never a force."""
        _row, facts = await asyncio.to_thread(_row_facts, thread_id)

        def run() -> dict[str, Any]:
            done = B.push(facts)
            _record(thread_id, facts, remote=done["remote"], pushed_at=utcnow().isoformat())
            return {**done, "status": B.status(facts)}

        return await _held(thread_id, run)

    @app.post("/build/sessions/{thread_id}/git/pr")
    async def git_pr_create(thread_id: str, body: PrBody) -> Any:
        """A pull request of the branch against its base through the GitHub
        CLI (the branch is pushed first when the remote lacks its newest
        commit). Refused in words when the CLI is missing or signed out — the
        screen then offers the compare link."""
        _row, facts = await asyncio.to_thread(_row_facts, thread_id)

        def run() -> dict[str, Any]:
            done = B.create_pr(facts, body.title, body.body)
            _record(thread_id, facts, pr_url=done["url"], pr_number=str(done["number"]),
                    pushed_at=utcnow().isoformat() if done.get("pushed") else None)
            return done

        return await _held(thread_id, run)

    @app.get("/build/sessions/{thread_id}/git/pr")
    async def git_pr_status(thread_id: str) -> Any:
        """The branch's pull request and its checks — read when asked (the
        screen's slow poll at most), never in a loop here."""
        _row, facts = await asyncio.to_thread(_row_facts, thread_id)
        try:
            return await asyncio.to_thread(_locked, thread_id, lambda: B.pr_status(facts))
        except B.GitRefused as exc:
            return _refused(exc)

    # ------------------------------------------------------------------ #
    # merge and discard
    # ------------------------------------------------------------------ #

    @app.post("/build/sessions/{thread_id}/git/merge")
    async def git_merge(thread_id: str) -> Any:
        """MERGE INTO <base> (``branches.merge``): a normal merge, or a refusal
        in words with nothing changed. Answers ``offer_remove`` — the screen
        then offers to remove the worktree (the Discard path; the branch
        stays)."""
        _row, facts = await asyncio.to_thread(_row_facts, thread_id)

        def run() -> dict[str, Any]:
            done = B.merge(facts)
            _record(thread_id, facts, merged_at=utcnow().isoformat(), merged_into=done["into"],
                    merge_commit=done["commit"])
            return {**done, "offer_remove": True}

        return await _held(thread_id, run)

    @app.post("/build/sessions/{thread_id}/git/discard")
    async def git_discard(thread_id: str, body: DiscardBody) -> Any:
        """DISCARD (or "remove the worktree" after a merge): the session's
        programs are stopped, the worktree folder MOVES to the trash and git
        forgets that worktree (only it); the BRANCH STAYS. The session is
        then archived. ``confirm`` must be true (the screen's two presses)."""
        if not body.confirm:
            return JSONResponse(status_code=400, content={"detail": CONFIRM_DISCARD})
        _row, facts = await asyncio.to_thread(_row_facts, thread_id)
        home = d.platform.config.home

        def run() -> dict[str, Any]:
            S.end_session_programs(thread_id)
            done = B.remove_worktree(home, facts)
            _record(thread_id, facts, removed_at=utcnow().isoformat(), trash=done["trash"] or None)
            from .chat import set_thread_archived

            set_thread_archived(engine_of(), thread_id, True)
            branch = facts.get("branch", "")
            done["branch"] = branch
            done["archived"] = True
            done["note"] = (
                f"The worktree is in Iron Jarvis's trash and the branch {branch} is kept."
                if done["moved"] else f"The worktree was already gone; the branch {branch} is kept."
            )
            if done.get("leftover"):
                done["note"] += (
                    f" Some of its files were in use and stayed at {done['leftover']}; the full copy "
                    "is in the trash, so that folder can be deleted once nothing uses it."
                )
            return done

        return await _held(thread_id, run)


#: v1.399.0 (night shift N2): a night task's own commit could not run because
#: its session was busy (a turn, a rewind, Compact or another git action).
NIGHT_COMMIT_BUSY = "Its session was busy, so Iron Jarvis did not commit its work; it is still in its branch folder."


async def commit_for_night(d: Any, thread_id: str, message: str) -> dict[str, Any]:
    """IRON JARVIS'S OWN COMMIT of a night task's work when the task ends
    (v1.399.0, night shift N2 — the user's decision 2): everything in the
    session's worktree, on its OWN branch, with the task's words as the
    message — the Commit button's own machinery (:func:`branches.commit`,
    the user's git identity, hooks run as they always do) under the
    session's git lock and HELD like every git action here (no turn, rewind
    or Compact starts meanwhile). It NEVER pushes and never merges, and it
    never runs on the user's main checkout (the worktree's HEAD must be on
    the session's branch — ``branches._require_on_branch``).

    Answers ``{committed, sha, subject, files, nothing, note, left_out,
    stopped}``: ``nothing`` True when nothing changed (no commit is made);
    ``note`` the plain words when the commit did not go through (a hook
    refused it, a file was held); ``left_out`` what the screen kept out of it.
    NEVER raises — the task's settle carries on whatever git says.

    v1.399.0 review — three things the first cut missed:

    * WHAT IT TAKES: ``git add -A`` takes everything, and a night task can
      leave a ``.env``, a key it pasted into a config file, a 200 MB artefact
      or a ``node_modules`` it installed. The night's commit is SCREENED
      (``harness/night_commit.screen``): those are unstaged, stay in the
      branch folder, and are named on the record (``left_out``).
    * ITS HOOKS: they run the repository's own code unattended, so the
      commit is bounded by :data:`NIGHT_COMMIT_S` (not the Commit button's
      five minutes — the night loop waits for it) and EMERGENCY STOP ends it:
      a watcher kills the commit's process tree the moment the switch is on,
      and a cancelled settle (Iron Jarvis stopping) kills it too (measured:
      git leaves no index lock behind a commit killed in its hook, so the
      morning's Commit button works — pinned, never "cleaned up" here).
    * A CRASH between git making the commit and the record saying so: the
      settle runs again and finds nothing to commit — the branch's newest
      commit carrying Iron Jarvis's own night trailer is still the task's
      (``recovered``)."""
    from ...harness import night_commit as NC
    from .build import _COMPACTING, _STARTING, _running_turn, _setup_of

    out: dict[str, Any] = {"committed": False, "sha": "", "subject": "", "files": 0, "nothing": False, "note": "",
                           "left_out": [], "stopped": False}
    facts: "dict[str, str] | None" = None
    try:
        row = await asyncio.to_thread(S.get_row, d.platform.engine, thread_id)
        facts = B.facts_of(_setup_of(row)) if row is not None else None
        if facts is None:
            out["note"] = "Its session has no branch of its own, so there was nothing to commit to."
            return out
        if (thread_id in _GIT_ACTING or thread_id in _COMPACTING or thread_id in _STARTING
                or _running_turn(d, thread_id)):
            out["note"] = NIGHT_COMMIT_BUSY
            return out
        if _emergency_on(d):
            out.update(stopped=True, note=NIGHT_COMMIT_EMERGENCY)
            return out
        box: dict[str, Any] = {"proc": None, "stop": False}
        box_lock = threading.Lock()

        def on_spawn(proc: Any) -> bool:
            with box_lock:
                box["proc"] = proc
                return bool(box["stop"])

        def kill() -> None:
            from ...core import procs

            with box_lock:
                box["stop"] = True
                proc = box["proc"]
            if proc is not None:
                with contextlib.suppress(Exception):
                    procs.kill_tree(proc)

        _STARTING.add(thread_id)
        _GIT_ACTING.add(thread_id)
        try:
            job = asyncio.ensure_future(asyncio.to_thread(
                _locked, thread_id,
                lambda: B.commit(facts, message, screen=NC.screen, timeout=NIGHT_COMMIT_S, on_spawn=on_spawn),
            ))
            try:
                while True:
                    finished, _ = await asyncio.wait({job}, timeout=NIGHT_STOP_POLL_S)
                    if finished:
                        break
                    if _emergency_on(d) and not box["stop"]:
                        await asyncio.to_thread(kill)
            except asyncio.CancelledError:
                # Iron Jarvis is stopping: the commit (and its hooks) end with it.
                await asyncio.shield(asyncio.to_thread(kill))
                with contextlib.suppress(BaseException):
                    await asyncio.shield(job)
                raise
            try:
                done = job.result()
            except B.GitRefused:
                if box["stop"]:
                    out.update(stopped=True, note=NIGHT_COMMIT_EMERGENCY)
                    return out
                raise
            if box["stop"] and not done.get("sha"):
                out.update(stopped=True, note=NIGHT_COMMIT_EMERGENCY)
                return out
        finally:
            _GIT_ACTING.discard(thread_id)
            _STARTING.discard(thread_id)
        out.update(committed=True, sha=str(done.get("sha") or ""), subject=str(done.get("subject") or ""),
                   files=int(done.get("files") or 0), left_out=list(done.get("left_out") or []))
        if done.get("note"):
            out["note"] = str(done["note"])
        return out
    except B.GitRefused as exc:
        if str(exc) == B.NOTHING_TO_COMMIT:
            out["nothing"] = True
            out["left_out"] = list(getattr(exc, "left_out", None) or [])
            if not out["left_out"]:
                recovered = await asyncio.to_thread(_night_commit_on_branch, facts)
                if recovered:
                    out.update(recovered=recovered)
            return out
        detail = next((ln.strip() for ln in str(exc.details or "").splitlines() if ln.strip()), "")
        words = f"Iron Jarvis could not commit its work: {exc}"
        if detail:
            words += f" ({detail[:200]})"
        out["note"] = words + " Its changes are still in its branch folder."
        return out
    except Exception as exc:  # noqa: BLE001 — a commit never breaks the settle
        log.warning("a night task's commit failed (%s)", thread_id, exc_info=True)
        out["note"] = (f"Iron Jarvis could not commit its work ({type(exc).__name__}); its changes are still in "
                       "its branch folder.")
        return out


#: v1.399.0 review: how long the night's own commit (its hooks) may run. The
#: night loop waits for it, so it is shorter than the Commit button's five
#: minutes; a hook that needs longer is said in words and the work stays in
#: the branch folder for the morning's Commit.
NIGHT_COMMIT_S = 120.0
#: How often the night's commit looks at Emergency stop while it runs.
NIGHT_STOP_POLL_S = 0.25
#: The note when Emergency stop ended (or came before) the night's commit.
NIGHT_COMMIT_EMERGENCY = (
    "Emergency stop was on, so Iron Jarvis did not commit its work (a commit already running, and its "
    "hooks, were stopped); it is still in its branch folder."
)


def _emergency_on(d: Any) -> bool:
    from ...agents.orchestrator import emergency_stop_on

    try:
        return bool(emergency_stop_on(getattr(d.platform, "config", None)))
    except Exception:  # noqa: BLE001 — unreadable: carry on as off
        return False


def _night_commit_on_branch(facts: "dict[str, str] | None") -> "dict[str, Any] | None":
    """The branch's newest commit when it is Iron Jarvis's own night commit
    (its message carries ``night_morning.TRAILER``) — the settle's record of a
    commit a crash kept it from writing down. BLOCKING; never raises."""
    if not facts:
        return None
    try:
        wt = B._live(facts)
        mb = B.merge_base(facts, wt)
        got = B._g(["log", "-1", "--format=%H%x00%s%x00%B", f"{mb}..HEAD"], wt, reads=True)
        if not got.ok or not got.out.strip():
            return None
        sha, subject, body = (got.out.split("\0", 2) + ["", ""])[:3]
        from ...harness.night_morning import TRAILER

        if TRAILER not in body:
            return None
        files = B._g(["diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"], wt, reads=True)
        return {"sha": sha.strip()[:12], "subject": subject.strip()[:300],
                "files": len([f for f in files.out.splitlines() if f.strip()]) if files.ok else 0}
    except Exception:  # noqa: BLE001
        return None


async def draft_message(d: Any, thread_id: str) -> Any:
    """The commit-message draft (``POST …/git/draft``) — the same kind of
    one-shot as Compact (``harness/compact``): the session's OWN pair
    (``routes/build._turn_pair``), its account pin and hold armed, its Local
    only scope, ``router.complete(pin=True)``, billed to the session
    (``library._bill``). HONEST MOCK: the demo model drafts nothing."""
    from ...daemon.routes.build import _turn_pair, turn_account
    from ...harness.library import _bill
    from ...iron_proxy.pin import reset_pin, set_pin
    from ...projects.locate import project_for_path
    from ...providers import egress as _egress
    from ...providers.adapters.base import LLMMessage
    from ...providers.hold import reset_hold, set_hold

    platform = d.platform
    engine = platform.engine
    row = await asyncio.to_thread(S.get_row, engine, thread_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no such Build session")
    setup = S._setup(row)
    facts = B.facts_of(setup)
    if facts is None:
        raise HTTPException(status_code=409, detail=no_branch_words(row))
    try:
        material = await asyncio.to_thread(_locked, thread_id, lambda: B.draft_material(facts))
    except B.GitRefused as exc:
        return _refused(exc)
    if not material:
        return JSONResponse(status_code=409, content={"detail": B.NOTHING_TO_COMMIT})
    pin, account_provider = await asyncio.to_thread(turn_account, platform, thread_id, setup)
    project = await asyncio.to_thread(project_for_path, engine, S.checkout_of(row))
    asked = SimpleNamespace(
        provider=account_provider or str(setup.get("provider") or ""), model=str(setup.get("model") or ""),
    )
    provider, model = _turn_pair(platform, asked, project)
    if not provider or provider.strip().lower() == "mock":
        return {"drafted": False, "message": "", "note": DEMO_DRAFT}
    asks = [str(m.get("content") or "")[:400] for m in S._messages(row)
            if m.get("role") == "user" and not m.get("steer") and not m.get("continuation")]
    asked_text = "\n".join(f"- {a}" for a in asks[-3:] if a.strip()) or "- (no question recorded)"
    user = f"What the session was asked:\n{asked_text}\n\n{material}"
    route = None
    pin_token = set_pin(pin) if pin is not None else None
    hold_token = set_hold(provider, model)
    try:
        with _egress.local_only_scope(setup.get("local_only") is True):
            async with asyncio.timeout(DRAFT_TIMEOUT_S):
                route = await platform.router.complete(
                    provider=provider, model=model or None, system=DRAFT_SYSTEM,
                    messages=[LLMMessage(role="user", content=user)], tools=[],
                    session_id=None, pin=True,
                )
    except _egress.LocalOnlyRefused:
        raise
    except TimeoutError:
        return JSONResponse(status_code=504, content={"detail": DRAFT_FAILED})
    except Exception as exc:  # noqa: BLE001 — the model's failure, in words
        from ...providers.failure_words import friendly_error

        words = friendly_error(exc, provider=provider)
        said = " ".join(p for p in (words.get("title", ""), words.get("fix", "")) if p).strip()
        return JSONResponse(status_code=502, content={"detail": said or DRAFT_FAILED})
    finally:
        reset_hold(hold_token)
        if pin_token is not None:
            reset_pin(pin_token)
    served = str(getattr(route, "provider", "") or "")
    if served.lower() == "mock" or str(getattr(route, "reason", "") or "").lower() == "mock":
        return {"drafted": False, "message": "", "note": DEMO_DRAFT}
    with contextlib.suppress(Exception):
        await asyncio.to_thread(_bill, platform, route, thread_id)
    text = str(getattr(getattr(route, "response", None), "text", "") or "")
    message = B.clean_message(text)
    if not message:
        return {"drafted": False, "message": "", "note": NO_DRAFT}
    return {"drafted": True, "message": message, "provider": served, "model": str(getattr(route, "model", "") or "")}


__all__ = ["commit_for_night", "draft_message", "register"]
