"""v1.406.0 part 3: a coding session's pull request — its failing checks are
FIXED by the session itself, a CLOUD reviewer approves the fix, and only then
does Iron Jarvis push it (``harness/ci_fix``, ``daemon/routes/ci_watch``).

Driven with REAL ``git`` (a scratch repository, its session's ``ij/`` branch
in a worktree, a local BARE remote the pushes really land in — its fetch
address says github.com, so nothing leaves this PC), the REAL app factory,
real build turns (``start_build_turn(started_by="ci")``) and the real tools;
the model is a scripted double, ``gh`` is a FAKE on PATH (never the real
GitHub) and the reviewer is the ``ci_watch.REVIEWER`` seam (one test runs the
real review path through the runtime with a scripted model).

Pinned, each guarantee mutation-checked:

* no push without an APPROVED review (a review with no verdict, a "changes
  needed" review: nothing reaches the pull request's branch);
* never the base branch, never a force push (``push_refusal`` + the push's
  own argv);
* a Local only session is never reviewed in the cloud and never pushed;
* Emergency stop: nothing starts, a fix turn is stopped, a review in flight is
  cancelled and nothing is pushed;
* at most 3 rounds per failing streak, then Iron Jarvis gives up (news);
* rejected work goes to a side branch and never reaches the PR branch (local
  or remote) until a review approves it;
* a running reply blocks a new fix turn (and nothing is read meanwhile).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from iron_jarvis.core.events import EventType
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import ci_watch as W
from iron_jarvis.harness import branches as B
from iron_jarvis.harness import ci_fix as CI
from iron_jarvis.harness import night as N
from iron_jarvis.harness import sessions as S
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult
from tests.test_build_git_v1375 import git, make_repo, new_branch_session, no_real_gh
from tests.test_build_sessions_v1367 import messages_of, setup_of, until

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

PR_URL = "https://github.com/acme/proj/pull/7"

FAKE_GH = r'''
import json, os, subprocess, sys
path = os.environ["IJ_FAKE_GH_STATE"]
with open(path, encoding="utf-8") as fh:
    st = json.load(fh)
args = sys.argv[1:]
st.setdefault("calls", []).append(args)
def save():
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(st, fh)
def head():
    r = subprocess.run(["git", "--git-dir", st["bare"], "rev-parse", "refs/heads/" + st["branch"]],
                       capture_output=True, text=True)
    return r.stdout.strip()
if args[:2] == ["auth", "status"]:
    save(); print("Logged in"); sys.exit(0)
if args[:2] == ["pr", "view"]:
    save()
    sha = head()
    checks = st.get("checks_by_sha", {}).get(sha, st.get("checks", []))
    print(json.dumps({"url": "https://github.com/acme/proj/pull/7", "number": 7,
                      "state": st.get("pr_state", "OPEN"), "headRefName": st.get("head_ref", st["branch"]),
                      "headRefOid": sha, "baseRefName": st.get("base_ref", "main"),
                      "statusCheckRollup": checks}))
    sys.exit(0)
if args[:2] == ["run", "view"]:
    save()
    print(st.get("logs", {}).get(args[2], ""))
    sys.exit(0)
save()
sys.exit(2)
'''


class FakeGh:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        self.state = folder / "state.json"
        self.state.write_text(json.dumps({}), encoding="utf-8")
        script = folder / "fake_gh.py"
        script.write_text(FAKE_GH, encoding="utf-8")
        if sys.platform == "win32":
            (folder / "gh.cmd").write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
        else:
            launcher = folder / "gh"
            launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
            launcher.chmod(0o755)

    def set(self, **values: Any) -> None:
        st = json.loads(self.state.read_text(encoding="utf-8"))
        st.update(values)
        self.state.write_text(json.dumps(st), encoding="utf-8")

    def read(self) -> dict:
        return json.loads(self.state.read_text(encoding="utf-8"))

    def calls(self, *head: str) -> list[list[str]]:
        return [c for c in self.read().get("calls", []) if c[: len(head)] == list(head)]


FAILED = {"__typename": "CheckRun", "name": "test", "workflowName": "CI", "status": "COMPLETED",
          "conclusion": "FAILURE", "detailsUrl": "https://github.com/acme/proj/actions/runs/111/job/9"}
PASSED = {"__typename": "CheckRun", "name": "test", "workflowName": "CI", "status": "COMPLETED",
          "conclusion": "SUCCESS", "detailsUrl": "https://github.com/acme/proj/actions/runs/222/job/9"}
PENDING = {"__typename": "CheckRun", "name": "test", "workflowName": "CI", "status": "IN_PROGRESS",
           "conclusion": "", "detailsUrl": "https://github.com/acme/proj/actions/runs/333/job/9"}
LOG = "CI\ttest\tFAILED tests/test_app.py::test_x - assert 2 == 3\nghp_" + "A" * 36 + " leaked token\n"


def _text(m: Any) -> str:
    c = getattr(m, "content", "")
    return c if isinstance(c, str) else json.dumps(c, default=str)


class CiModel:
    """The scripted model: a fix turn answers ONE Write of app.py (x = 3, then
    4, …) and then words; the reviewer (its brief opens with the team review's
    lead) answers ``verdict_line``; everything else answers words."""

    def __init__(self, verdict_line: str = "VERDICT: approved") -> None:
        self.n = 3
        self.verdict_line = verdict_line
        self.fix_prompts: list[str] = []
        self.review_tools: list[list[str]] = []

    def _move(self, messages: list, tools: list) -> Any:
        from iron_jarvis.harness.finish import REVIEW_BRIEF_LEAD

        texts = [_text(m) for m in messages]
        if any(REVIEW_BRIEF_LEAD in t for t in texts):
            self.review_tools.append([t["name"] for t in tools or []])
            return "I read app.py.\n" + self.verdict_line
        marks = [i for i, (m, t) in enumerate(zip(messages, texts))
                 if getattr(m, "role", "") == "user"
                 and t.startswith(("Fix the failing checks", "Fix what the review of the check fix"))]
        if tools and marks and not any(getattr(m, "role", "") == "tool" for m in messages[marks[-1]:]):
            self.fix_prompts.append(texts[marks[-1]])
            call = ToolCall(id=f"w{self.n}", name="Write",
                            arguments={"file_path": "app.py", "content": f"x = {self.n}\n"})
            self.n += 1
            return [call]
        return "Fixed it."

    async def stream(self, *, provider=None, model=None, system="", messages=(), tools=None, **kw):
        move = self._move(list(messages), list(tools or []))
        if isinstance(move, str):
            yield {"type": "text", "text": move}
            resp = LLMResponse(text=move, usage={"input_tokens": 3, "output_tokens": 1})
        else:
            resp = LLMResponse(text="", tool_calls=list(move), usage={"input_tokens": 3, "output_tokens": 1})
        yield {"type": "final", "response": resp, "provider": "anthropic", "model": "claude-sonnet-4-5"}

    async def complete(self, *, provider=None, model=None, system="", messages=(), tools=None, **kw):
        move = self._move(list(messages), list(tools or []))
        if isinstance(move, str):
            return RouteResult(LLMResponse(text=move, usage={"input_tokens": 3, "output_tokens": 1}),
                               provider or "anthropic", model or "")
        return RouteResult(LLMResponse(text="", tool_calls=list(move), usage={"input_tokens": 3, "output_tokens": 1}),
                           provider or "anthropic", model or "")


class FakeReviewer:
    def __init__(self, verdicts: list[str]) -> None:
        self.verdicts = list(verdicts)
        self.jobs: list[W.ReviewJob] = []
        self.hold: "asyncio.Event | None" = None

    async def __call__(self, d: Any, job: W.ReviewJob) -> W.ReviewResult:
        self.jobs.append(job)
        if self.hold is not None:
            await self.hold.wait()
        v = self.verdicts.pop(0) if self.verdicts else ""
        report = {"good": "Looks right.\nVERDICT: approved",
                  "bad": "app.py line 1 is still wrong.\nVERDICT: changes needed"}.get(v, "")
        return W.ReviewResult(v, report=report, why="" if v else "its report gave no verdict")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    no_real_gh(monkeypatch)
    W._REVIEWS.clear()
    W._LOCKS.clear()
    # The two reviewers are "connected" (cached answers only).
    monkeypatch.setattr("iron_jarvis.agents.model_team.provider_ready", lambda platform, p: True)
    yield
    for t in list(W._REVIEWS.values()):
        t.cancel()
    W._REVIEWS.clear()
    W._LOCKS.clear()
    B.forget_gh_state()


@pytest.fixture()
def fake(tmp_path, monkeypatch) -> FakeGh:
    gh = FakeGh(tmp_path / "fakegh")
    monkeypatch.setenv("IJ_FAKE_GH_STATE", str(gh.state))
    monkeypatch.setenv("PATH", str(gh.folder) + os.pathsep + os.environ["PATH"])
    B.forget_gh_state()
    return gh


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def model(app) -> CiModel:
    m = CiModel()
    app.state.platform.router.stream = m.stream
    app.state.platform.router.complete = m.complete
    return m


class World:
    def __init__(self, app, sid: str, wt: Path, branch: str, bare: Path, sha0: str, fake: FakeGh) -> None:
        self.app, self.sid, self.wt, self.branch, self.bare, self.sha0, self.fake = app, sid, wt, branch, bare, sha0, fake
        self.d = app.state.d
        self.engine = app.state.platform.engine
        self.events: list[tuple[str, dict]] = []

    def remote_sha(self) -> str:
        return git("--git-dir", str(self.bare), "rev-parse", f"refs/heads/{self.branch}", cwd=self.bare)

    def local_sha(self, ref: str = "") -> str:
        return git("rev-parse", f"refs/heads/{ref or self.branch}", cwd=self.wt)

    def rec(self) -> dict:
        return CI.get(self.engine, self.sid) or {}

    def ci_lines(self) -> list[dict]:
        return [m for m in messages_of(self.app, self.sid) if isinstance(m.get("ci"), dict)]

    def kinds(self) -> list[str]:
        return [m["ci"]["kind"] for m in self.ci_lines()]

    async def step(self) -> dict:
        out = await W.ci_pass(self.d)
        return out

    async def settle_turn(self) -> None:
        from iron_jarvis.daemon.routes.build import _running_turn

        assert await until(lambda: not _running_turn(self.d, self.sid), 30.0)

    async def settle_review(self) -> None:
        task = W._REVIEWS.get(self.sid)
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), 30.0)

    def due_now(self) -> None:
        CI.save(self.engine, self.sid, next_check_at=None)


async def _world(app, tmp_path: Path, fake: FakeGh, **gh: Any) -> World:
    repo = make_repo(tmp_path / "proj")
    bare = tmp_path / "remote.git"
    git("init", "-q", "--bare", "-b", "main", str(bare), cwd=tmp_path)
    git("remote", "add", "origin", "https://github.com/acme/proj.git", cwd=repo)
    git("remote", "set-url", "--push", "origin", str(bare), cwd=repo)
    git("push", "-q", "origin", "main", cwd=repo)
    view = await new_branch_session(app, repo)
    sid, wt, branch = view["id"], Path(view["git"]["worktree"]), view["git"]["branch"]
    (wt / "app.py").write_bytes(b"x = 2\n")
    git("add", "-A", cwd=wt)
    git("commit", "-q", "-m", "Make x two", cwd=wt)
    git("push", "-q", "origin", f"{branch}:{branch}", cwd=wt)
    facts = setup_of(app, sid)["git"]
    S.update(app.state.platform.engine, sid, setup={"git": {
        **facts, "pr_url": PR_URL, "pr_number": "7", "pushed_at": "2026-10-29T10:00:00", "remote": "origin"}})
    sha0 = git("rev-parse", f"refs/heads/{branch}", cwd=wt)
    fake.set(**{"bare": str(bare), "branch": branch, "checks_by_sha": {sha0: [FAILED]}, "checks": [PASSED],
                "logs": {"111": LOG}, **gh})
    w = World(app, sid, wt, branch, bare, sha0, fake)
    bus = app.state.platform.event_bus
    real = bus.publish

    async def spy(etype, payload=None, *a, **k):
        if str(etype).startswith("build.ci_"):
            w.events.append((str(etype), dict(payload or {})))
        return await real(etype, payload, *a, **k)

    bus.publish = spy  # type: ignore[assignment]
    return w


async def _to_review(w: World) -> None:
    """watching → fix due → fixing → (turn ends) → reviewing."""
    await w.step()
    assert w.rec()["state"] == CI.FIX_DUE, w.rec()
    await w.step()
    assert w.rec()["state"] == CI.FIXING, w.rec()
    await w.settle_turn()
    await w.step()


# --------------------------------------------------------------------------- #
# the whole way: failed → fixed → reviewed → pushed → passing
# --------------------------------------------------------------------------- #


async def test_a_failed_check_is_fixed_by_the_session_reviewed_then_pushed(app, tmp_path, fake, model, monkeypatch):
    reviewer = FakeReviewer(["good"])
    monkeypatch.setattr(W, "REVIEWER", reviewer)
    pushes: list[list[str]] = []
    real_run_git = B.run_git

    def spy_run_git(args, *a, **k):
        if "push" in args:
            pushes.append(list(args))
        return real_run_git(args, *a, **k)

    monkeypatch.setattr(B, "run_git", spy_run_git)
    w = await _world(app, tmp_path, fake)
    seen_ci: list[str] = []
    real_note = N.note_unattended_turn

    def note(turn_id, thread_id, by):
        seen_ci.append(by)
        return real_note(turn_id, thread_id, by)

    monkeypatch.setattr(N, "note_unattended_turn", note)
    await _to_review(w)
    assert w.rec()["state"] == CI.REVIEWING
    await w.settle_review()
    # The fix turn was Iron Jarvis's own ("ci": Emergency stop covers it), on
    # the session's hidden continuation question, with the log fenced and
    # MASKED for the model only.
    assert seen_ci == [N.BY_CI]
    prompt = model.fix_prompts[0]
    assert "Fix the failing checks on this session's pull request: CI / test." in prompt
    assert "[UNTRUSTED CONTENT" in prompt and "assert 2 == 3" in prompt
    assert "ghp_" + "A" * 36 not in prompt
    question = [m for m in messages_of(app, w.sid) if m.get("role") == "user"][-1]
    assert question.get("continuation") is True
    # The reviewer read the change from the failing head (the diff) and the
    # verdict line it must end with.
    job = reviewer.jobs[0]
    assert job.provider in CI.REVIEWERS and "-x = 2" in job.task and "+x = 3" in job.task
    assert "VERDICT: approved" in job.task
    # Approved → committed and PUSHED to the PR's own branch, never forced.
    assert w.remote_sha() != w.sha0
    assert git("--git-dir", str(w.bare), "show", f"refs/heads/{w.branch}:app.py", cwd=w.bare) == "x = 3"
    assert pushes and all(not any(a in ("-f", "--force", "--force-with-lease") or a.startswith("+")
                                      or a.startswith("--force") for a in p) for p in pushes)
    assert all(f"{w.branch}:{w.branch}" in p for p in pushes)
    rec = w.rec()
    assert rec["state"] == CI.WATCHING and rec["pushed_sha"] == w.remote_sha()
    assert setup_of(app, w.sid)["git"]["pushed_at"] == rec["seen_pushed_at"]
    # The checks of the pushed fix pass: the news.
    w.due_now()
    await w.step()
    assert w.rec()["state"] == CI.PASSING
    assert w.kinds() == [CI.KIND_FAILED, CI.KIND_REVIEW, CI.KIND_PUSHED, CI.KIND_PASSING]
    done = [p for t, p in w.events if t == EventType.BUILD_CI_DONE]
    assert [p["kind"] for p in done] == [CI.KIND_PASSING]
    steps = [p["kind"] for t, p in w.events if t == EventType.BUILD_CI_STEP]
    assert steps == [CI.KIND_FAILED, CI.KIND_REVIEW, CI.KIND_PUSHED]
    # The PR is never merged by Iron Jarvis.
    assert git("rev-parse", "refs/heads/main", cwd=w.wt) == git(
        "--git-dir", str(w.bare), "rev-parse", "refs/heads/main", cwd=w.bare)
    assert not fake.calls("pr", "merge")


# --------------------------------------------------------------------------- #
# no push without an APPROVED review
# --------------------------------------------------------------------------- #


async def test_a_review_with_no_verdict_never_pushes(app, tmp_path, fake, model, monkeypatch):
    monkeypatch.setattr(W, "REVIEWER", FakeReviewer([""]))
    w = await _world(app, tmp_path, fake)
    await _to_review(w)
    await w.settle_review()
    assert w.remote_sha() == w.sha0
    assert w.local_sha() == w.sha0
    assert w.rec()["state"] == CI.GAVE_UP
    assert [p["kind"] for t, p in w.events if t == EventType.BUILD_CI_DONE] == [CI.KIND_GAVE_UP]


async def test_the_real_review_runs_read_only_and_reads_its_last_line(app, tmp_path, fake, model, monkeypatch):
    """The default REVIEWER: a read-only helper of the session on the
    reviewer's own model — only REVIEW_TOOLS — and its verdict is the report's
    LAST line (a verdict quoted earlier judges nothing)."""
    from iron_jarvis.harness.helpers import REVIEW_TOOLS

    model.verdict_line = "VERDICT: approved\n(quoted from a file) and then nothing"
    w = await _world(app, tmp_path, fake)
    await _to_review(w)
    await w.settle_review()
    assert model.review_tools and set(model.review_tools[0]) <= set(REVIEW_TOOLS) | {
        "Read", "Glob", "Grep", "LS"}
    assert model.review_tools[0], "the reviewer could read nothing"
    assert w.remote_sha() == w.sha0, "a verdict that is not the last line must not push"
    assert w.rec()["state"] == CI.GAVE_UP


async def test_the_real_review_approves_and_the_fix_is_pushed(app, tmp_path, fake, model, monkeypatch):
    w = await _world(app, tmp_path, fake)
    await _to_review(w)
    await w.settle_review()
    assert w.rec()["state"] == CI.WATCHING, w.rec()
    assert w.remote_sha() != w.sha0


# --------------------------------------------------------------------------- #
# rejected work: a side branch, never the PR branch
# --------------------------------------------------------------------------- #


async def test_rejected_work_goes_to_a_side_branch_until_a_review_approves_it(app, tmp_path, fake, model, monkeypatch):
    reviewer = FakeReviewer(["bad", "good"])
    monkeypatch.setattr(W, "REVIEWER", reviewer)
    w = await _world(app, tmp_path, fake)
    await _to_review(w)
    await w.settle_review()
    rec = w.rec()
    side = rec["side_branch"]
    assert rec["state"] == CI.FIX_DUE and side.startswith(w.branch + "-ci-fix-")
    # The PR branch — local and remote — has NOT moved; the side branch holds
    # the fix, committed; the session works on the side branch now.
    assert w.remote_sha() == w.sha0 and w.local_sha() == w.sha0
    assert git("show", f"refs/heads/{side}:app.py", cwd=w.wt) == "x = 3"
    assert CI.head_branch(w.wt) == side
    assert git("--git-dir", str(w.bare), "branch", "--list", side, cwd=w.bare) == ""
    # Round 2 works on what the reviewer found (the report, fenced).
    await w.step()
    assert w.rec()["state"] == CI.FIXING and w.rec()["rounds"] == 2
    await w.settle_turn()
    assert "review of the check fix" in model.fix_prompts[-1] and "still wrong" in model.fix_prompts[-1]
    await w.step()
    await w.settle_review()
    # Approved: the PR branch is FAST-FORWARDED to the side branch and pushed.
    assert w.rec()["state"] == CI.WATCHING
    assert CI.head_branch(w.wt) == w.branch
    assert w.remote_sha() == w.local_sha() == w.local_sha(side)
    assert git("--git-dir", str(w.bare), "show", f"refs/heads/{w.branch}:app.py", cwd=w.bare) == "x = 4"
    # The second review read the WHOLE fix since the failing head.
    assert "-x = 2" in reviewer.jobs[1].task and "+x = 4" in reviewer.jobs[1].task
    assert w.kinds() == [CI.KIND_FAILED, CI.KIND_REVIEW, CI.KIND_SIDE, CI.KIND_FIXING, CI.KIND_REVIEW,
                         CI.KIND_PUSHED]


async def test_three_rounds_then_it_gives_up_and_nothing_reaches_the_pr(app, tmp_path, fake, model, monkeypatch):
    reviewer = FakeReviewer(["bad", "bad", "bad", "good"])
    monkeypatch.setattr(W, "REVIEWER", reviewer)
    w = await _world(app, tmp_path, fake)
    await _to_review(w)
    await w.settle_review()
    for _ in range(2):
        await w.step()
        await w.settle_turn()
        await w.step()
        await w.settle_review()
    assert len(reviewer.jobs) == 3 and len(model.fix_prompts) == 3
    rec = w.rec()
    assert rec["state"] == CI.GAVE_UP and rec["rounds"] == CI.MAX_ROUNDS
    for _ in range(3):
        w.due_now()
        await w.step()
    assert len(reviewer.jobs) == 3 and len(model.fix_prompts) == 3, "a fourth round ran"
    assert w.remote_sha() == w.sha0 and w.local_sha() == w.sha0
    assert git("show", f"refs/heads/{rec['side_branch']}:app.py", cwd=w.wt) == "x = 5"
    assert [p["kind"] for t, p in w.events if t == EventType.BUILD_CI_DONE] == [CI.KIND_GAVE_UP]


async def test_checks_that_keep_failing_after_approved_pushes_stop_after_three_rounds(
        app, tmp_path, fake, model, monkeypatch):
    """The streak counts across Iron Jarvis's OWN pushes: a fix that keeps
    failing CI never gets three more rounds after every push of its own."""
    reviewer = FakeReviewer(["good"] * 5)
    monkeypatch.setattr(W, "REVIEWER", reviewer)
    w = await _world(app, tmp_path, fake, checks=[FAILED])
    shas = [w.sha0]
    for _ in range(CI.MAX_ROUNDS):
        await _to_review(w)
        await w.settle_review()
        assert w.rec()["state"] == CI.WATCHING, w.rec()
        shas.append(w.remote_sha())
        w.due_now()
    assert len(set(shas)) == CI.MAX_ROUNDS + 1  # three reviewed pushes landed
    await w.step()
    assert w.rec()["state"] == CI.GAVE_UP
    for _ in range(2):
        w.due_now()
        await w.step()
    assert len(model.fix_prompts) == CI.MAX_ROUNDS and len(reviewer.jobs) == CI.MAX_ROUNDS
    assert [p["kind"] for t, p in w.events if t == EventType.BUILD_CI_DONE] == [CI.KIND_GAVE_UP]


# --------------------------------------------------------------------------- #
# Local only, Emergency stop, a running reply
# --------------------------------------------------------------------------- #


async def test_a_local_only_session_is_never_reviewed_in_the_cloud_nor_pushed(app, tmp_path, fake, model, monkeypatch):
    reviewer = FakeReviewer(["good"])
    monkeypatch.setattr(W, "REVIEWER", reviewer)
    w = await _world(app, tmp_path, fake)
    S.update(w.engine, w.sid, setup={"local_only": True})
    await _to_review(w)
    await w.settle_review()
    assert reviewer.jobs == []
    assert w.rec()["state"] == CI.ASKING
    assert w.remote_sha() == w.sha0
    assert "Local only" in w.ci_lines()[-1]["content"]


async def test_emergency_stop_starts_nothing(app, tmp_path, fake, model, monkeypatch):
    monkeypatch.setattr(W, "REVIEWER", FakeReviewer(["good"]))
    w = await _world(app, tmp_path, fake)
    app.state.platform.config.autonomy_kill_switch = True
    try:
        for _ in range(3):
            await w.step()
        assert w.rec()["state"] == CI.WATCHING and not fake.calls("pr", "view")
        assert model.fix_prompts == []
    finally:
        app.state.platform.config.autonomy_kill_switch = False


async def test_emergency_stop_cancels_a_review_in_flight_and_nothing_is_pushed(app, tmp_path, fake, model, monkeypatch):
    from iron_jarvis.agents.orchestrator import Orchestrator

    reviewer = FakeReviewer(["good"])
    reviewer.hold = asyncio.Event()
    monkeypatch.setattr(W, "REVIEWER", reviewer)
    w = await _world(app, tmp_path, fake)
    await _to_review(w)
    assert await until(lambda: reviewer.jobs, 10.0)
    app.state.platform.config.autonomy_kill_switch = True
    try:
        stopped = await asyncio.to_thread(Orchestrator(app.state.platform).stop_unattended)
        assert f"ci:{w.sid}" in stopped
        task = W._REVIEWS.get(w.sid)
        if task is not None:
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 10.0)
        assert w.remote_sha() == w.sha0
        assert w.rec()["state"] == CI.STOPPED
        assert "Emergency stop" in w.ci_lines()[-1]["content"]
    finally:
        app.state.platform.config.autonomy_kill_switch = False


async def test_emergency_stop_turned_on_during_the_review_pushes_nothing(app, tmp_path, fake, model, monkeypatch):
    reviewer = FakeReviewer(["good"])
    reviewer.hold = asyncio.Event()
    monkeypatch.setattr(W, "REVIEWER", reviewer)
    w = await _world(app, tmp_path, fake)
    await _to_review(w)
    assert await until(lambda: reviewer.jobs, 10.0)
    app.state.platform.config.autonomy_kill_switch = True
    try:
        reviewer.hold.set()
        await w.settle_review()
        assert w.remote_sha() == w.sha0
        assert w.rec()["state"] == CI.STOPPED
    finally:
        app.state.platform.config.autonomy_kill_switch = False


async def test_a_running_reply_blocks_a_new_fix_turn(app, tmp_path, fake, model, monkeypatch):
    from iron_jarvis.daemon.routes.build import _STARTING

    monkeypatch.setattr(W, "REVIEWER", FakeReviewer(["good"]))
    w = await _world(app, tmp_path, fake)
    await w.step()
    assert w.rec()["state"] == CI.FIX_DUE
    _STARTING.add(w.sid)  # the user's reply is starting
    try:
        before = len(fake.calls())
        for _ in range(3):
            await w.step()
        assert w.rec()["state"] == CI.FIX_DUE
        assert model.fix_prompts == [] and w.ci_lines() == []
        assert len(fake.calls()) == before, "GitHub was read while the session was busy"
    finally:
        _STARTING.discard(w.sid)
    await w.step()
    assert w.rec()["state"] == CI.FIXING


async def test_turning_it_off_watches_nothing(app, tmp_path, fake, model, monkeypatch):
    w = await _world(app, tmp_path, fake)
    app.state.platform.config.ci_autofix = False
    for _ in range(2):
        await w.step()
    assert CI.get(w.engine, w.sid) is None and not fake.calls("pr", "view")


async def test_a_pending_check_is_read_again_later_and_a_user_push_starts_a_new_streak(app, tmp_path, fake, model, monkeypatch):
    w = await _world(app, tmp_path, fake)
    fake.set(checks_by_sha={w.sha0: [PENDING]})
    await w.step()
    rec = w.rec()
    assert rec["state"] == CI.WATCHING and rec["next_check_at"]
    await w.step()
    assert len(fake.calls("pr", "view")) == 1, "a pending check was read again before its time"
    assert CI.next_interval(CI.POLL_FIRST_S) > CI.POLL_FIRST_S
    assert CI.next_interval(10 ** 6) == CI.POLL_MAX_S
    CI.save(w.engine, w.sid, state=CI.GAVE_UP, rounds=3)
    facts = setup_of(app, w.sid)["git"]
    S.update(w.engine, w.sid, setup={"git": {**facts, "pushed_at": "2026-10-29T12:00:00"}})
    await w.step()
    assert w.rec()["rounds"] == 0 and w.rec()["state"] in (CI.WATCHING, CI.FIX_DUE)


# --------------------------------------------------------------------------- #
# the push's guards (unit)
# --------------------------------------------------------------------------- #


async def test_the_push_is_refused_to_the_base_another_branch_or_a_closed_pr(app, tmp_path, fake, model):
    w = await _world(app, tmp_path, fake)
    facts = setup_of(app, w.sid)["git"]

    def pr(**over):
        # v1.406.0 review: GitHub's answer names THE pull request (its url /
        # number) — an answer about another one refuses the push.
        base = dict(state="open", head_ref=w.branch, head_sha=w.sha0, base_ref="main", url=PR_URL, number="7")
        base.update(over)
        return CI.PrChecks(**base)

    assert CI.push_refusal(facts, pr(), w.wt) == ""
    assert CI.push_refusal(facts, pr(base_ref=w.branch), w.wt) == CI.PUSH_THE_BASE
    assert CI.push_refusal(facts, pr(base_ref=""), w.wt) == CI.PUSH_THE_BASE
    assert CI.push_refusal({**facts, "base": w.branch}, pr(), w.wt) == CI.PUSH_THE_BASE
    assert CI.push_refusal(facts, pr(head_ref="ij/other"), w.wt) == CI.PUSH_NOT_THE_PR
    assert CI.push_refusal(facts, pr(state="merged"), w.wt) == CI.PUSH_NOT_OPEN
    assert CI.push_refusal(facts, pr(url=""), w.wt) == CI.PUSH_NOT_THE_PR
    assert CI.push_refusal(facts, pr(number="8"), w.wt) == CI.PUSH_NOT_THE_PR
    assert CI.push_refusal(facts, pr(head_owner="acme", head_repo="proj"), w.wt) == ""
    assert CI.push_refusal(facts, pr(head_owner="evil", head_repo="proj"), w.wt) == CI.PUSH_OTHER_REPO
    assert CI.push_refusal({**facts, "branch": "main"}, pr(head_ref="main"), w.wt) == B.NO_BRANCH
    git("switch", "-q", "-c", "ij/elsewhere", cwd=w.wt)
    assert "not on" in CI.push_refusal(facts, pr(), w.wt)
    with pytest.raises(B.GitRefused):
        CI.push_fix(facts, pr())
    assert w.remote_sha() == w.sha0


def test_the_reviewer_pick_prefers_the_other_vendor_and_names_who_is_missing():
    ready = {"claude-cli": True, "codex-cli": True}
    pick = lambda setting, own: CI.pick_reviewer(None, setting, own, ready=lambda p: ready.get(p))  # noqa: E731
    assert pick("auto", "claude-cli") == ("codex-cli", "")
    assert pick("auto", "anthropic") == ("codex-cli", "")
    assert pick("auto", "codex-cli") == ("claude-cli", "")
    assert pick("auto", "ollama")[0] == "claude-cli"
    ready["codex-cli"] = False
    assert pick("auto", "claude-cli") == ("claude-cli", "")
    assert pick("codex-cli", "claude-cli")[0] == "" and "Codex is not connected" in pick("codex-cli", "")[1]
    ready["claude-cli"] = None
    assert pick("auto", "")[0] == "" and pick("auto", "")[1] == CI.NO_REVIEWER


def test_the_settings_and_the_notices():
    from iron_jarvis.comm.notifier import DEFAULT_ALERT_EVENTS, bell_pings, format_event
    from iron_jarvis.core.config import Config
    from iron_jarvis.settings.schema import BY_KEY as SCHEMA_BY_KEY

    assert Config.model_fields["ci_autofix"].default is True
    assert Config.model_fields["ci_reviewer"].default == "auto"
    row = SCHEMA_BY_KEY["ci_autofix"]
    assert row.tier_for(True) == "ask-floor" and row.tier_for(False) == "allow"
    assert [o[0] for o in SCHEMA_BY_KEY["ci_reviewer"].options] == list(CI.REVIEWER_SETTINGS)
    assert EventType.BUILD_CI_DONE in DEFAULT_ALERT_EVENTS
    assert EventType.BUILD_CI_STEP not in DEFAULT_ALERT_EVENTS
    done = {"type": EventType.BUILD_CI_DONE, "session_id": "build:t", "payload": {
        "thread_id": "t", "kind": "gave_up", "title": "Iron Jarvis stopped fixing your pull request's checks",
        "body": "Fix the parser · Iron Jarvis stopped after 3 rounds."}}
    step = {**done, "type": EventType.BUILD_CI_STEP}
    assert bell_pings(done) is True and bell_pings(step) is False
    assert format_event(done) == ("Iron Jarvis stopped fixing your pull request's checks — Fix the parser · "
                                  "Iron Jarvis stopped after 3 rounds.")


def test_the_loop_has_words_and_the_line_key_is_kept_on_both_sides():
    root = Path(__file__).resolve().parents[1]
    words = (root / "dashboard" / "lib" / "loopWords.ts").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "  ci_watch: {" in words
    assert "ci" in S.LINE_KEYS
    ts = (root / "dashboard" / "lib" / "buildSessions.ts").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert '\n  "ci",\n] as const;' in ts
    app_src = (root / "src" / "iron_jarvis" / "daemon" / "app.py").read_text(encoding="utf-8")
    assert '_tick("ci_watch", ok, exc)' in app_src
