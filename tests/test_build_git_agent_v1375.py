"""v1.375.0 (IJ harness part 2, Phase 4): what the AGENT may do with git while
a Build session works on its own branch, and where its turn reads from.

* ``git commit`` / ``git push`` / ``gh pr create`` from the agent's command
  tools card in EVERY posture (auto-approve included), ONCE ONLY ("Allow
  once" and Deny; a wider answer is read as once, so the next commit asks
  again) — the stream lane; the POST lane, which cannot card, REFUSES it
  (lock-step); the agent runtime (a helper) pauses the same way;
* a FORCE push is refused outright by the tool, whatever the posture;
* a session that works in its folder directly is exactly as before;
* the turn's prompt names the branch and says the main checkout is the
  user's; the session belongs to its REPOSITORY's project (the worktree is
  outside the project's root) and the project's folder line points at the
  worktree; an UNTRACKED harness layer and rule file in the main checkout
  are read in a worktree session (an untracked file is not in a new
  worktree), and a worktree's own tracked layer wins.

REAL git in a temp folder, the REAL app; the model is the only double.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.grants import StandingGrantRecord
from iron_jarvis.core.models import Project, ToolInvocation
from iron_jarvis.harness import branches as B
from iron_jarvis.harness import git_guard as G
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.daemon.app import create_app
from tests.test_build_git_v1375 import (
    git,
    make_remote,
    make_repo,
    new_branch_session,
    no_real_gh,
    turn,
)
from tests.test_build_sessions_v1367 import Script, asgi, turn_frames, until, wire
from tests.test_chat_turn_stop_v1241 import _asgi_post
from tests.test_build_plan_ask_v1367 import _finish, _start

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


@pytest.fixture(autouse=True)
def _no_real_gh(monkeypatch):
    no_real_gh(monkeypatch)
    yield
    B.forget_gh_state()


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def repo(tmp_path) -> Path:
    return make_repo(tmp_path / "proj")


def _ledger(app, tool: str) -> list[ToolInvocation]:
    with session_scope(app.state.platform.engine) as db:
        rows = [r for r in db.exec(select(ToolInvocation)) if r.tool == tool]
        for r in rows:
            db.expunge(r)
        return rows


def _count(wt: Path) -> int:
    return int(git("rev-list", "--count", "HEAD", cwd=wt))


#: git by its full path, quoted: the agent's ``shell`` runs on a scrubbed PATH
#: (sandbox ``modify_env = "deny"``, the default) where a bare ``git`` does
#: not resolve on Windows — a model that wants git there names it this way.
GITX = f'"{shutil.which("git")}"'


# --------------------------------------------------------------------------- #
# the classifier
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("command,kinds", [
    ("git commit -am wip", ["commit"]),
    ("git -C sub commit -m x", ["commit"]),
    ('cmd /c "git push origin HEAD"', ["push"]),
    ("C:\\Program Files\\Git\\cmd\\git.exe push", ["push"]),
    ('"C:\\Program Files\\Git\\cmd\\git.exe" commit -m x', ["commit"]),
    ("git add -A && git commit -m done && git push -u origin ij/x", ["commit", "push"]),
    ("git push --force origin HEAD", ["force"]),
    ("git push -fu origin HEAD", ["force"]),
    ("git push --force-with-lease", ["force"]),
    ("git push origin +HEAD:main", ["force"]),
    ("git push --mirror", ["force"]),
    ("gh pr create --fill", ["pr"]),
    ("gh pr merge 7", ["pr"]),
    ("git status && git diff", []),
    ("git log --grep=push", []),
    ("python commit.py", []),
    ("gh pr view 7", []),
])
def test_the_guard_reads_a_command_line(command, kinds):
    assert [a.kind for a in G.classify(command)] == kinds


def test_the_guard_reads_text_and_errs_toward_asking():
    """HONEST LIMIT (the module docstring): it reads the command's text, so a
    git push inside an echo still asks — a needless card, never a silent push."""
    assert [a.kind for a in G.classify("echo git push")] == ["push"]


def test_the_guard_says_nothing_outside_a_branch_turn():
    assert B.current() is None
    assert G.guard_check("git push origin HEAD") is None
    assert G.force_refusal("git push --force") == ""


def test_inside_a_branch_turn_a_push_asks_once_and_a_force_is_refused():
    token = B.set_turn_branch({"repo": "C:\\r", "worktree": "C:\\w", "branch": "ij/x", "base": "main",
                               "checkout": "C:\\r"}, "C:\\w")
    try:
        check = G.guard_check("git push origin HEAD")
        assert check.ask is True and check.once_only is True and check.reason == "git"
        assert "ij/x" in check.words and "asks you first" in check.words
        # Review (v1.375.0): a refused command shows NO card in any lane —
        # its check answers "no card" and the tool refuses it before it runs.
        refused = G.guard_check("git push --force")
        assert refused.ask is False and refused.words == G.FORCE_REFUSAL
        assert G.force_refusal("git push --force") == G.FORCE_REFUSAL
        assert G.guard_check("git status") is None
    finally:
        B.reset_turn_branch(token)


# --------------------------------------------------------------------------- #
# the stream lane: every posture cards a commit / push, once only
# --------------------------------------------------------------------------- #


async def test_on_auto_approve_an_agent_commit_still_cards_once_only_and_the_next_one_asks_again(app, repo):
    view = await new_branch_session(app, repo, approval_mode="yolo")
    sid, wt = view["id"], Path(view["git"]["worktree"])
    (wt / "app.py").write_bytes(b"x = 2\n")
    start = _count(wt)
    wire(app, Script([
        [ToolCall(id="c1", name="Bash", arguments={"command": f"{GITX} commit -am one"})],
        [ToolCall(id="c2", name="Bash", arguments={"command": f"{GITX} commit --allow-empty -m two"})],
        "Done.",
    ]))
    drop, task = await _start(app, sid, "commit it", "ag-yolo")
    card = (await until(lambda: [d for e, d in turn_frames(app, "ag-yolo") if e == "approval"]))[0]
    assert card["tool"] == "shell" and card["once_only"] is True and card["once_reason"] == "git"
    assert card["can_always"] is False and "commit" in card["cost_note"]
    # A WIDER answer is read as once: the commit runs, nothing is granted.
    assert (await _asgi_post(app, f"/chat/approvals/{card['id']}", {"decision": "conversation"}))[0] == 200
    second = (await until(lambda: [d for e, d in turn_frames(app, "ag-yolo") if e == "approval"][1:]))[0]
    assert second["once_only"] is True, "the next commit asks again"
    assert (await _asgi_post(app, f"/chat/approvals/{second['id']}", {"decision": "deny"}))[0] == 200
    await _finish(app, "ag-yolo", drop, task)
    resolved = [d["decision"] for e, d in turn_frames(app, "ag-yolo") if e == "approval_resolved"]
    assert resolved == ["once", "deny"]
    assert _count(wt) == start + 1, "the allowed commit ran; the denied one did not"
    with session_scope(app.state.platform.engine) as db:
        assert list(db.exec(select(StandingGrantRecord))) == []


@pytest.mark.parametrize("posture", ["", "always_ask"])
async def test_every_other_posture_cards_a_push_once_only(app, repo, tmp_path, posture):
    bare = make_remote(tmp_path, repo)
    view = await new_branch_session(app, repo, approval_mode=posture)
    sid, branch = view["id"], view["git"]["branch"]
    wire(app, Script([[ToolCall(id="p1", name="Bash", arguments={"command": f"git push -u origin {branch}"})],
                      "Done."]))
    drop, task = await _start(app, sid, "push it", f"ag-{posture or 'default'}")
    card = (await until(lambda: [d for e, d in turn_frames(app, f"ag-{posture or 'default'}") if e == "approval"]))[0]
    assert card["once_only"] is True and card["once_reason"] == "git" and "pushes to origin" in card["cost_note"]
    assert (await _asgi_post(app, f"/chat/approvals/{card['id']}", {"decision": "deny"}))[0] == 200
    await _finish(app, f"ag-{posture or 'default'}", drop, task)
    assert not [r for r in git("branch", "--list", cwd=bare).splitlines() if "ij/" in r]


async def test_a_force_push_is_refused_outright_with_no_card(app, repo, tmp_path):
    bare = make_remote(tmp_path, repo)
    before = git("rev-parse", "main", cwd=bare)
    view = await new_branch_session(app, repo, approval_mode="yolo")
    sid, wt = view["id"], Path(view["git"]["worktree"])
    (wt / "app.py").write_bytes(b"x = 99\n")
    assert (await asgi(app, "POST", f"/build/sessions/{sid}/git/commit", {"message": "new"}))[0] == 200
    wire(app, Script([
        [ToolCall(id="f1", name="Bash", arguments={"command": f"{GITX} push --force origin HEAD:main"})],
        [ToolCall(id="f2", name="Bash", arguments={"command": f"{GITX} push origin +HEAD:main"})],
        "Done.",
    ]))
    frames = await turn(app, sid, "force it", "ag-force")
    assert not [d for e, d in frames if e == "approval"]
    finished = [d for e, d in frames if e == "tool_call" and d.get("status") == "finished"]
    assert len(finished) == 2 and all(d["ok"] is False for d in finished)
    rows = _ledger(app, "shell")
    assert len(rows) == 2 and all(G.FORCE_REFUSAL in (r.output or "") for r in rows)
    assert git("rev-parse", "main", cwd=bare) == before


async def test_a_session_in_its_folder_directly_is_exactly_as_before(app, repo):
    view = await new_branch_session(app, repo, approval_mode="yolo", branch=False)
    (repo / "app.py").write_bytes(b"x = 2\n")
    start = _count(repo)
    wire(app, Script([[ToolCall(id="c1", name="Bash", arguments={"command": f"{GITX} commit -am direct"})],
                      "Done."]))
    frames = await turn(app, view["id"], "commit", "ag-direct")
    assert not [d for e, d in frames if e == "approval"], "no branch: auto-approve runs it as before"
    assert _count(repo) == start + 1


# --------------------------------------------------------------------------- #
# the POST lane cannot card: it refuses (lock-step)
# --------------------------------------------------------------------------- #


class _PostRouter:
    default_provider = "mock"

    def __init__(self, calls):
        self.n = 0
        self.calls = calls
        self.seen: list = []

    async def complete(self, **kw):
        self.seen.append(list(kw.get("messages") or []))
        self.n += 1
        if self.n == 1:
            return RouteResult(LLMResponse(text="", tool_calls=self.calls, usage={}), "mock", "mock")
        return RouteResult(LLMResponse(text="Done.", tool_calls=[], usage={}), "mock", "mock")


async def test_the_post_lane_refuses_an_agent_commit_and_a_force_push_on_a_branch(app, repo):
    from iron_jarvis.daemon.chat_turn import ONCE_NOT_ASKED_TAIL, SPEND_NOT_ASKED_LABEL, run_chat_turn
    from iron_jarvis.daemon.schemas import ChatBody

    view = await new_branch_session(app, repo)
    wt = Path(view["git"]["worktree"])
    (wt / "app.py").write_bytes(b"x = 2\n")
    start = _count(wt)
    platform = app.state.platform
    platform.router = router = _PostRouter([
        ToolCall(id="c1", name="shell", arguments={"command": "git commit -am post"}),
        ToolCall(id="c2", name="shell", arguments={"command": "git push --force origin HEAD"}),
    ])
    from iron_jarvis.harness import sessions as S

    facts = S.git_of(S.get_row(platform.engine, view["id"]))
    token = B.set_turn_branch(facts, str(wt))
    try:
        body = ChatBody(messages=[{"role": "user", "content": "commit it"}], tools=["shell"],
                        workspace_dir=str(wt), thread_id="chat_post")
        res = await run_chat_turn(platform, {}, body)
    finally:
        B.reset_turn_branch(token)
    assert _count(wt) == start, "the POST lane ran a commit it could not ask about"
    tool_msgs = [m for m in router.seen[1] if getattr(m, "role", "") == "tool"]
    assert tool_msgs[0].content.startswith(f"{SPEND_NOT_ASKED_LABEL}: ")
    assert ONCE_NOT_ASKED_TAIL in tool_msgs[0].content
    assert G.FORCE_REFUSAL in tool_msgs[1].content
    assert res["reply"]


# --------------------------------------------------------------------------- #
# the agent runtime (a helper) pauses the same way
# --------------------------------------------------------------------------- #


async def test_the_runtime_asks_once_only_and_reads_a_wider_answer_as_once(app):
    from iron_jarvis.agents.runtime import AgentRuntime
    from iron_jarvis.agents.types import get_agent_definition
    from iron_jarvis.core.events import EventType
    from iron_jarvis.core.models import AgentType
    from iron_jarvis.tools.base import SpendCheck

    platform = app.state.platform
    published: list = []
    real = platform.event_bus.publish

    async def spy(type, payload=None, session_id=None, **kw):
        published.append((str(getattr(type, "value", type)), payload or {}))
        return await real(type, payload, session_id=session_id, **kw)

    platform.event_bus.publish = spy
    rt = AgentRuntime(platform)
    sess = SimpleNamespace(id="s_helper", origin="job:build-helper", approval_mode="", agent_name="",
                           project_id=None, trust="full")
    tc = SimpleNamespace(name="shell", arguments={"command": "git push origin HEAD"})
    allow: set = set()
    spend = SpendCheck(ask=True, words="This pushes to origin.", once_only=True, reason="git")
    task = asyncio.create_task(rt._pause_for_approval(sess, tc, get_agent_definition(AgentType.BUILDER), allow,
                                                       spend=spend))
    ask = await until(lambda: [p for t, p in published if t == str(EventType.APPROVAL_REQUESTED)])
    assert ask[0]["once_only"] is True and ask[0]["once_reason"] == "git" and ask[0]["can_always"] is False
    assert platform.approvals.resolve(ask[0]["approval_id"], "conversation")
    deny, extra = await asyncio.wait_for(task, 10)
    assert deny == "" and "shell" in extra, "this call runs"
    assert allow == set(), "nothing is granted for the rest of the run"
    resolved = [p for t, p in published if t == str(EventType.APPROVAL_RESOLVED)]
    assert resolved[0]["decision"] == "once"


# --------------------------------------------------------------------------- #
# what the turn reads: the branch, the project, the layer and the rules
# --------------------------------------------------------------------------- #


async def test_the_prompt_names_the_branch_and_the_project_is_the_repositorys(app, repo):
    with session_scope(app.state.platform.engine) as db:
        db.add(Project(id="proj_1", name="Proj", root=str(repo), instructions="Use tabs."))
        db.commit()
    view = await new_branch_session(app, repo)
    sid, wt, branch = view["id"], view["git"]["worktree"], view["git"]["branch"]
    assert view["is_project"] is True and view["project_id"] == "proj_1" and view["folder_name"] == "Proj"
    script = wire(app, Script(["ok"]))
    await turn(app, sid, "hello", "ag-prompt")
    system = script.calls[0]["system"]
    assert f"own git branch, {branch} (started from main), in a worktree at {wt}" in system
    assert f"main checkout at {repo} is theirs" in system
    # The REPOSITORY's project grounds the turn (the worktree is outside its
    # root), and its folder line points at the worktree, never the checkout.
    assert "# Project: Proj" in system and "Use tabs." in system
    assert f"Project folder (this session's worktree of it): {wt}" in system
    assert f"Project folder: {repo}" not in system


async def test_an_untracked_layer_and_rule_file_in_the_checkout_are_read_and_a_tracked_layer_wins(app, repo):
    # Untracked in the main checkout: not in a new worktree.
    layer = repo / ".ironjarvis" / "harness"
    layer.mkdir(parents=True)
    (layer / "guardrails.md").write_text("Never touch the vendor folder.\n", encoding="utf-8")
    (repo / "AGENTS.local.md").write_text("Answer in British English.\n", encoding="utf-8")
    view = await new_branch_session(app, repo)
    sid, wt = view["id"], Path(view["git"]["worktree"])
    assert not (wt / ".ironjarvis").exists() and not (wt / "AGENTS.local.md").exists()
    script = wire(app, Script(["ok"]))
    frames = await turn(app, sid, "hello", "ag-layer-1")
    system = script.calls[0]["system"]
    assert "Never touch the vendor folder." in system
    assert "Answer in British English." in system
    done = [d for e, d in frames if e == "done"][-1]
    assert "AGENTS.local.md" in done["folder_rules"]
    # A worktree with its OWN (tracked, on the branch) layer reads that one.
    (wt / ".ironjarvis" / "harness").mkdir(parents=True)
    (wt / ".ironjarvis" / "harness" / "guardrails.md").write_text("Branch rule: keep it small.\n", encoding="utf-8")
    script = wire(app, Script(["ok"]))
    await turn(app, sid, "again", "ag-layer-2")
    system = script.calls[0]["system"]
    assert "Branch rule: keep it small." in system
    assert "Never touch the vendor folder." not in system
