"""22-b (R3.4): Build › Code's Commit and Open a pull request, one press each
(``hosted/session_git`` holds every rule). Thin wrappers over the SAME
``harness/branches`` functions ``routes/build_git`` calls for Chat › Code.

* ``GET  /build/app/sessions/{sid}/git`` — ``kind``: ``branch`` (the session
  works on its own branch: ``branches.status`` plus the files not committed),
  ``folder`` (in the folder directly: every changed file with its tick, the
  branch, whether a pull request can be opened and why not, ``pr_url`` only
  while the folder is on that pull request's branch, and ``unpushed``: the
  commits the remote's copy of the branch lacks), or ``none``
  (no repository, no folder yet). Each carries ``draft``, the commit message
  written from the session's record (the user's first message and the files).
* ``GET  …/git/diff`` — a branch session's Branch view (``branch_changes``).
* ``POST …/git/draft {paths?}`` — the draft again, for the files ticked now.
  Never a model call.
* ``POST …/git/commit {message, paths?}`` — a branch session commits
  everything in its own copy of the folder (``branches.commit``); a folder
  session commits ONLY ``paths`` (``session_git.folder_commit``).
* ``POST …/git/push`` — never a force (the row's Push once a pull request is
  open and the branch has commits it lacks; a branch session's Branch view).
* ``POST …/git/pr {title, body}`` / ``GET …/git/pr`` — through the GitHub CLI;
  the link is kept on the row (``git_json``).
* ``POST …/git/merge`` and ``POST …/git/discard {confirm}`` — a branch
  session only.

A press that changes something holds the session (``service._claim``: no
message starts meanwhile) and is refused while a reply runs, while Iron
Jarvis checks the reply's tests, while the tool still works in the
background, or while the conversation is open in a terminal. Every git call
runs in a worker thread under the session's git lock and its folder's lock
(the Changes pane's Revert and the rewind take the same two).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ...core.ids import utcnow
from ...harness import branches as B
from ...hosted import session_git as SG
from ...hosted import service as S
from ...hosted import store
from .build_app import _refused

log = logging.getLogger(__name__)

CONFIRM_DISCARD = "Press Discard twice to confirm."
#: The longest list of ticked files a commit takes (the screen lists at most
#: ``session_git.MAX_LISTED``).
MAX_PATHS = 500


class DraftBody(BaseModel):
    paths: list[str] = Field(default_factory=list, max_length=MAX_PATHS)


class CommitBody(BaseModel):
    message: str = Field(default="", max_length=B.MAX_MESSAGE_CHARS + 100)
    #: A session in the folder directly: the ticked files (each one of the
    #: changed files now). Ignored on a session's own branch.
    paths: list[str] = Field(default_factory=list, max_length=MAX_PATHS)


class PrBody(BaseModel):
    title: str = Field(default="", max_length=B.MAX_TITLE_CHARS + 100)
    body: str = Field(default="", max_length=B.MAX_BODY_CHARS + 100)


class DiscardBody(BaseModel):
    confirm: bool = False


def _git_refused(exc: B.GitRefused) -> JSONResponse:
    content: dict[str, Any] = {"detail": str(exc)}
    if exc.details:
        content["details"] = exc.details
    return JSONResponse(status_code=exc.status, content=content)


def _no(words: str, status: int = 409) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": words})


class _AtFolder:
    """A stand-in row for ``branching._same_folder_running``: this session, at
    another folder (the main checkout a merge writes)."""

    def __init__(self, sid: str, folder: str) -> None:
        self.id = sid
        self.folder = folder


def register(app: FastAPI, d: Any) -> None:
    from ...hosted import branching as BR
    from ...hosted import checkpoints as CK
    from ...hosted import proof as PROOF
    from ...hosted.events import MAX_LINES
    from .build_git import _locked

    def svc() -> S.HostedService:
        return S.service_for(d.platform)

    def locked(sid: str, folder: str, fn: Any) -> Any:
        """``fn()`` under the session's git lock and then its folder's (the
        Revert's and the rewind's own two). BLOCKING: a worker thread."""
        if not folder:
            return _locked(f"app:{sid}", fn)
        return _locked(f"app:{sid}", lambda: CK.folder_locked(folder, fn))

    async def _events(service: S.HostedService, row: Any) -> list[dict[str, Any]]:
        """The session's record (from memory when this Iron Jarvis drove it,
        else its file): the draft's first message, the tool's file steps."""
        got = await service._view_events(row.id, service.live.get(row.id), MAX_LINES)  # noqa: SLF001
        return list(got.get("events") or [])

    async def _read(sid: str, fn: Any) -> Any:
        """A read: the row, then ``fn(service, row)`` (BLOCKING) under the
        locks. Refusals in words."""
        service = svc()
        try:
            row = await service._row(sid)
        except S.Refused as r:
            return _refused(r)
        try:
            return await fn(service, row)
        except B.GitRefused as exc:
            return _git_refused(exc)
        except S.Refused as r:
            return _refused(r)

    async def _held(sid: str, fn: Any, *, files: bool = True) -> Any:
        """A press that changes something: refused while the session is busy,
        then HELD (no message starts meanwhile) while ``fn(service, row,
        live)`` (async) runs."""
        service = svc()
        try:
            row = await service._row(sid)
        except S.Refused as r:
            return _refused(r)
        live = service._live(row)
        if PROOF.checking(service, sid):
            return _no(SG.CHECKING_NOW)
        try:
            service._claim(live, SG.CLAIM)
        except S.Refused as r:
            return _refused(r)
        try:
            if files:
                await BR._files_free(service, row, live)  # noqa: SLF001 — the folder's one busy rule
            return await fn(service, row, live)
        except B.GitRefused as exc:
            return _git_refused(exc)
        except S.Refused as r:
            return _refused(r)
        except CK.RestoreRefused as r:
            return _no(r.sentence, r.status)
        finally:
            if live.claim == SG.CLAIM:
                live.claim = ""

    def _settle(service: S.HostedService, sid: str, verdict: str, how: str) -> None:
        """Next leaps 28-a: the user's press settles every undecided reply of
        the session in the folder's coding record. BLOCKING (the press's own
        worker thread); never fails the press."""
        from ...harness import scoreboard as SB

        SB.settle_session(service.engine, sid, verdict, how)

    def _still_free(service: S.HostedService, row: Any) -> None:
        """Inside the locks, right before git writes: a reply may have started
        while this waited for them."""
        BR._still_free(service, row)  # noqa: SLF001

    # ------------------------------------------------------------------ #
    # reading
    # ------------------------------------------------------------------ #

    @app.get("/build/app/sessions/{sid}/git")
    async def app_git(sid: str) -> Any:
        async def run(service: S.HostedService, row: Any) -> dict[str, Any]:
            events = await _events(service, row)
            facts = SG.facts_of(row)
            folder = str(row.folder or "")
            if facts is None:
                return await asyncio.to_thread(
                    locked, sid, folder, lambda: SG.folder_status(service.home, sid, row, events))

            def branch() -> dict[str, Any]:
                st = B.status(facts)
                files: list[dict[str, Any]] = []
                if st.get("exists"):
                    files = [{"rel": f["rel"], "status": f["status"], "ticked": True}
                             for f in SG.changed_files(facts["worktree"])[:SG.MAX_LISTED]]
                return {**st, "kind": "branch", "files": files,
                        "draft": SG.draft_message(events, [f["rel"] for f in files])}

            return await asyncio.to_thread(locked, sid, folder, branch)

        return await _read(sid, run)

    @app.get("/build/app/sessions/{sid}/git/diff")
    async def app_git_diff(sid: str) -> Any:
        async def run(service: S.HostedService, row: Any) -> Any:
            facts = SG.facts_of(row)
            if facts is None:
                return _no(SG.NO_DIFF_HERE)
            return await asyncio.to_thread(locked, sid, row.folder or "", lambda: B.branch_changes(facts))

        return await _read(sid, run)

    @app.post("/build/app/sessions/{sid}/git/draft")
    async def app_git_draft(sid: str, body: DraftBody | None = None) -> Any:
        """The commit message from the record (no model): the first message
        and the files (``paths``, or a branch session's files not committed)."""
        async def run(service: S.HostedService, row: Any) -> dict[str, Any]:
            events = await _events(service, row)
            facts = SG.facts_of(row)
            paths = list(body.paths) if body and body.paths else []
            if facts is not None and not paths:
                paths = await asyncio.to_thread(SG.uncommitted_names, facts)
            return {"drafted": True, "message": SG.draft_message(events, paths), "note": ""}

        return await _read(sid, run)

    @app.get("/build/app/sessions/{sid}/git/pr")
    async def app_git_pr_status(sid: str) -> Any:
        """The pull request's checks — read when asked (the screen's slow poll
        at most), never in a loop here."""
        async def run(service: S.HostedService, row: Any) -> Any:
            facts = SG.facts_of(row)
            if facts is not None:
                return await asyncio.to_thread(locked, sid, row.folder or "", lambda: B.pr_status(facts))
            pr = SG.direct_pr(row)
            if not pr or not pr.get("branch"):
                return {"url": "", "state": "", "words": "This session has opened no pull request."}
            return await asyncio.to_thread(
                locked, sid, row.folder or "", lambda: SG.folder_pr_status(row.folder or "", pr["branch"]))

        return await _read(sid, run)

    # ------------------------------------------------------------------ #
    # the presses
    # ------------------------------------------------------------------ #

    @app.post("/build/app/sessions/{sid}/git/commit")
    async def app_git_commit(sid: str, body: CommitBody) -> Any:
        async def run(service: S.HostedService, row: Any, live: Any) -> dict[str, Any]:
            facts = SG.facts_of(row)
            folder = str(row.folder or "")

            def go() -> dict[str, Any]:
                _still_free(service, row)
                if facts is not None:
                    done = B.commit(facts, body.message)
                    _settle(service, sid, "kept", "committed")
                    return {**done, "kind": "branch"}
                done = {**SG.folder_commit(folder, body.message, list(body.paths)), "kind": "folder"}
                _settle(service, sid, "kept", "committed")
                return done

            return await asyncio.to_thread(locked, sid, folder, go)

        return await _held(sid, run)

    @app.post("/build/app/sessions/{sid}/git/push")
    async def app_git_push(sid: str) -> Any:
        async def run(service: S.HostedService, row: Any, live: Any) -> dict[str, Any]:
            facts = SG.facts_of(row)
            folder = str(row.folder or "")

            def go() -> dict[str, Any]:
                if facts is not None:
                    done = B.push(facts)
                    store.merge_git(service.engine, sid, {"remote": done["remote"],
                                                          "pushed_at": utcnow().isoformat()})
                    return done
                return SG.folder_push(folder)

            return await asyncio.to_thread(locked, sid, folder, go)

        return await _held(sid, run, files=False)

    @app.post("/build/app/sessions/{sid}/git/pr")
    async def app_git_pr(sid: str, body: PrBody) -> Any:
        async def run(service: S.HostedService, row: Any, live: Any) -> dict[str, Any]:
            facts = SG.facts_of(row)
            folder = str(row.folder or "")

            def go() -> dict[str, Any]:
                if facts is not None:
                    done = B.create_pr(facts, body.title, body.body)
                    store.merge_git(service.engine, sid, {
                        "pr_url": done["url"], "pr_number": str(done["number"]),
                        "pushed_at": utcnow().isoformat() if done.get("pushed") else None})
                    _settle(service, sid, "kept", "pull_request")
                    return done
                done = SG.folder_pr(folder, body.title, body.body)
                store.merge_git(service.engine, sid, {
                    SG.DIRECT: "1", "branch": done["branch"], "pr_url": done["url"],
                    "pr_number": str(done["number"])})
                _settle(service, sid, "kept", "pull_request")
                return done

            return await asyncio.to_thread(locked, sid, folder, go)

        return await _held(sid, run, files=False)

    @app.post("/build/app/sessions/{sid}/git/merge")
    async def app_git_merge(sid: str) -> Any:
        async def run(service: S.HostedService, row: Any, live: Any) -> Any:
            facts = SG.facts_of(row)
            if facts is None:
                return _no(SG.NO_MERGE_HERE)
            # A merge writes the folder the user PICKED: never while another
            # session's reply works there.
            main = _AtFolder(sid, facts.get("checkout", ""))

            def go() -> dict[str, Any]:
                _still_free(service, row)
                if BR._same_folder_running(service, main):  # noqa: SLF001
                    raise CK.RestoreRefused(BR.OTHER_SESSION)
                done = B.merge(facts)
                store.merge_git(service.engine, sid, {
                    "merged_at": utcnow().isoformat(), "merged_into": done["into"], "merge_commit": done["commit"]})
                _settle(service, sid, "kept", "merged")
                return {**done, "offer_remove": True}

            return await asyncio.to_thread(locked, sid, row.folder or "", go)

        return await _held(sid, run)

    @app.post("/build/app/sessions/{sid}/git/discard")
    async def app_git_discard(sid: str, body: DiscardBody) -> Any:
        """The session's own copy of the folder MOVES to Iron Jarvis's trash
        (the tool is stopped first: it works in that folder); git forgets
        that copy only; the BRANCH STAYS; the session is archived."""
        if not body.confirm:
            return _no(CONFIRM_DISCARD, 400)

        async def run(service: S.HostedService, row: Any, live: Any) -> Any:
            facts = SG.facts_of(row)
            if facts is None:
                return _no(SG.NO_DISCARD_HERE)
            # 22-b review: never under another session that works in this copy
            # too ("Branch from here" made it there), archived or not.
            others = await asyncio.to_thread(SG.others_in_copy, service.engine, row)
            if others:
                return _no(SG.shared_refusal(others))
            await service._stop_process(live, why="discarded")  # noqa: SLF001
            home = d.platform.config.home

            def go() -> dict[str, Any]:
                # Asked again inside the locks: one may have been made meanwhile.
                again = SG.others_in_copy(service.engine, row)
                if again:
                    raise B.GitRefused(SG.shared_refusal(again), 409)
                done = B.remove_worktree(home, facts)
                store.merge_git(service.engine, sid, {"removed_at": utcnow().isoformat(),
                                                      "trash": done.get("trash") or None})
                _settle(service, sid, "thrown", "discarded")
                return done

            done = await asyncio.to_thread(locked, sid, row.folder or "", go)
            branch = facts.get("branch", "")
            done["branch"] = branch
            done["note"] = (
                f"Its copy of the folder is in Iron Jarvis's trash and the branch {branch} is kept."
                if done.get("moved") else f"Its copy of the folder was already gone; the branch {branch} is kept."
            )
            if done.get("leftover"):
                done["note"] += (
                    f" Some of its files were in use and stayed at {done['leftover']}; the full copy is in the "
                    "trash, so that folder can be deleted once nothing uses it."
                )
            live.claim = ""
            try:
                await service.archive(sid)
                done["archived"] = True
            except S.Refused as r:
                done["archived"] = False
                done["note"] += f" The session was not archived: {r.sentence}"
            return done

        return await _held(sid, run)


__all__ = ["CommitBody", "DiscardBody", "DraftBody", "PrBody", "register"]
