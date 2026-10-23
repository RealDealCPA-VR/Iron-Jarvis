"""v1.375.0 REVIEW (IJ harness part 2, Phase 4): what the AGENT may do with
git on a session's own branch — the guard's holes, closed.

Found in review, each red on the phase's code first:

* the guard read ``cmd /c "git push"`` as ``push"`` and ``bash -lc 'git
  commit'`` as ``commit'`` — on Auto-approve those ran with NO card (and an
  alias given on the line, ``git -c alias.ci=commit ci``, too);
* the plan's "no deleting a branch, no history rewrite" was text in the
  prompt only: on Auto-approve ``reset --hard HEAD~1``, ``rebase``,
  ``branch -D`` and ``worktree prune`` (which forgets every worktree whose
  folder is missing, the user's own included) RAN with no card, and
  ``push origin :branch`` / ``commit --amend`` were carded as an ordinary
  push / commit ("This pushes to origin");
* a refused force push still showed the ordinary shell card first in every
  posture but Auto-approve.

Now: the refused kinds are refused by the tool with NO card in any posture;
a commit, a push, a GitHub change and git that throws away work not
committed card once-only in every posture. Proven on the REAL registry
``invoke`` in the stream lane, the POST lane and a helper's own run (only the
model is a double; git is real, in a temp folder with a local bare remote).
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import branches as B
from iron_jarvis.harness import git_guard as G
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_build_git_v1375 import git, make_remote, make_repo, new_branch_session, no_real_gh, turn
from tests.test_build_sessions_v1367 import Script, turn_frames, until, wire
from tests.test_build_plan_ask_v1367 import _finish, _start
from tests.test_chat_turn_stop_v1241 import _asgi_post

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

#: git by its full path: the agent's shell runs on a scrubbed PATH where a
#: bare ``git`` does not resolve on Windows.
GITX = f'"{shutil.which("git")}"'


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


def _ledger(app, tool: str = "shell") -> list[ToolInvocation]:
    with session_scope(app.state.platform.engine) as db:
        rows = [r for r in db.exec(select(ToolInvocation)) if r.tool == tool]
        for r in rows:
            db.expunge(r)
        return rows


def _count(wt: Path) -> int:
    return int(git("rev-list", "--count", "HEAD", cwd=wt))


# --------------------------------------------------------------------------- #
# the classifier
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("command,kinds", [
    # quoting a wrapper leaves around the last word
    ('cmd /c "git push"', ["push"]),
    ("bash -lc 'git commit'", ["commit"]),
    ('pwsh -c "git push"', ["push"]),
    ('powershell -Command "& git push"', ["push"]),
    ("`git push`", ["push"]),
    ("git -c alias.ci=commit ci -am x", ["commit"]),
    # deleting a remote branch
    ("git push --delete origin ij/x", ["delete_remote"]),
    ("git push -d origin ij/x", ["delete_remote"]),
    ("git push origin :ij/x", ["delete_remote"]),
    ("git push --prune origin", ["delete_remote"]),
    # history rewrites
    ("git commit --amend -m x", ["rewrite"]),
    ("git rebase main", ["rewrite"]),
    ("git pull --rebase", ["rewrite"]),
    ("git reset --hard HEAD~3", ["rewrite"]),
    ("git reset HEAD~1", ["rewrite"]),
    ("git reset --soft abc1234", ["rewrite"]),
    ("git filter-branch --tree-filter x", ["rewrite"]),
    ("git update-ref refs/heads/main abc", ["rewrite"]),
    # branches, tags and worktrees
    ("git branch -D ij/x", ["delete_branch"]),
    ("git branch -m ij/x other", ["delete_branch"]),
    ("git checkout -B main", ["delete_branch"]),
    ("git tag -d v1", ["delete_branch"]),
    ("git worktree prune", ["worktree"]),
    ("git worktree remove ../x", ["worktree"]),
    ("gh repo delete acme/proj --yes", ["delete_repo"]),
    # throwing away work not committed (carded)
    ("git reset --hard", ["discard"]),
    ("git clean -fdx", ["discard"]),
    ("git checkout -- .", ["discard"]),
    ("git restore app.py", ["discard"]),
    ("git stash drop", ["discard"]),
    # GitHub changes (carded)
    ("gh pr close 7", ["gh"]),
    ("gh api -X DELETE repos/a/b/git/refs/heads/x", ["gh"]),
    # a git setting the user's checkout shares (carded)
    ("git config user.email agent@example.com", ["config"]),
    ("git config --global user.name Agent", ["config"]),
    ("git config --unset user.email", ["config"]),
    ("git config set core.autocrlf false", ["config"]),
    ("git config -f other.cfg x.y z", ["config"]),
    ("git config user.email", []),
    ("git config --get user.email", []),
    ("git config -l", []),
    ("git config get user.name", []),
    # left alone
    ("git status && git log --oneline", []),
    ("git checkout main", []),
    ("git checkout -b feature", []),
    ("git switch main", []),
    ("git merge main", []),
    ("git reset", []),
    ("git reset HEAD app.py", []),
    ("git restore --staged app.py", []),
    ("git branch --show-current", []),
    ('git commit -m "handle --amend in the parser"', ["commit"]),
    ("gh pr view 7", []),
    ("gh api repos/a/b/pulls", []),
])
def test_the_guard_reads_what_a_command_does(command, kinds):
    assert [a.kind for a in G.classify(command)] == kinds


def test_a_refused_command_shows_no_card_and_a_carded_one_asks_once():
    token = B.set_turn_branch({"repo": "C:\\r", "worktree": "C:\\w", "branch": "ij/x", "base": "main",
                               "checkout": "C:\\r"}, "C:\\w")
    try:
        for command in ("git push origin :ij/x", "git reset --hard HEAD~1", "git branch -D ij/x",
                        "git worktree prune", "git commit --amend -m x"):
            check = G.guard_check(command)
            assert check.ask is False and check.words == G.refusal(command) != "", command
        for command in ("git clean -fdx", "gh pr close 7", 'cmd /c "git push"'):
            check = G.guard_check(command)
            assert check.ask is True and check.once_only is True, command
        assert "throws away changes on ij/x" in G.guard_check("git reset --hard").words
    finally:
        B.reset_turn_branch(token)
    assert G.guard_check("git reset --hard HEAD~1") is None and G.refusal("git reset --hard HEAD~1") == ""


async def test_the_program_tools_refuse_before_anything_starts_and_card_what_they_type(tmp_path):
    from types import SimpleNamespace

    from iron_jarvis.harness.process import TABLE, ProcessStartTool, ProcessWriteTool
    from iron_jarvis.tools.base import ToolContext

    ctx = ToolContext(workspace=tmp_path, session_id="build:guard", agent_run_id="r",
                      config=SimpleNamespace(home=tmp_path, sandbox={"modify_env": "allow"}),
                      event_bus=None, engine=None)
    start, write = ProcessStartTool(), ProcessWriteTool()
    token = B.set_turn_branch({"repo": "C:\\r", "worktree": str(tmp_path), "branch": "ij/x", "base": "main",
                               "checkout": "C:\\r"}, str(tmp_path))
    try:
        got = await start.execute({"command": "git reset --hard HEAD~1", "wait_ms": 0}, ctx)
        assert got.ok is False and "rewrites the branch's history" in got.error
        assert not TABLE.running("build:guard"), "nothing started"
        assert (await start.spend_check({"command": "git push"}, ctx)).once_only is True
        typed = await write.spend_check({"id": "p1", "input": "git push origin :main\r\n"}, ctx)
        assert typed.ask is False
        got = await write.execute({"id": "p1", "input": "git push origin :main\r\n"}, ctx)
        assert got.ok is False and "deletes a branch on the remote" in got.error
        assert (await write.spend_check({"id": "p1", "input": "git commit -am x\n"}, ctx)).once_only is True
    finally:
        B.reset_turn_branch(token)


# --------------------------------------------------------------------------- #
# the stream lane, on the real registry
# --------------------------------------------------------------------------- #


async def test_on_auto_approve_a_wrapped_or_aliased_commit_and_push_still_card(app, repo, tmp_path):
    bare = make_remote(tmp_path, repo)
    git("config", "push.default", "current", cwd=repo)
    view = await new_branch_session(app, repo, approval_mode="yolo")
    sid, wt = view["id"], Path(view["git"]["worktree"])
    (wt / "app.py").write_bytes(b"x = 2\n")
    start = _count(wt)
    wire(app, Script([
        [ToolCall(id="a1", name="Bash", arguments={"command": f"{GITX} -c alias.ci=commit ci -am wip"})],
        [ToolCall(id="a2", name="Bash", arguments={"command": f'cmd /c "{GITX} push"'})],
        "Done.",
    ]))
    drop, task = await _start(app, sid, "ship it", "gr-wrap")
    for n in range(2):
        cards = await until(lambda: [d for e, d in turn_frames(app, "gr-wrap") if e == "approval"][n:])
        assert cards[0]["once_only"] is True and cards[0]["once_reason"] == "git"
        assert (await _asgi_post(app, f"/chat/approvals/{cards[0]['id']}", {"decision": "deny"}))[0] == 200
    await _finish(app, "gr-wrap", drop, task)
    assert _count(wt) == start, "the aliased commit ran with no card"
    assert view["git"]["branch"] not in git("branch", "--list", cwd=bare), "the wrapped push ran with no card"


async def test_on_auto_approve_a_branch_deletion_and_a_rewrite_are_refused_and_nothing_changes(app, repo, tmp_path):
    bare = make_remote(tmp_path, repo)
    git("push", "-q", "origin", "main:keep-me", cwd=repo)
    view = await new_branch_session(app, repo, approval_mode="yolo")
    sid, wt = view["id"], Path(view["git"]["worktree"])
    (wt / "app.py").write_bytes(b"x = 2\n")
    git("commit", "-q", "-am", "one", cwd=wt)
    head = git("rev-parse", "HEAD", cwd=wt)
    git("worktree", "add", "-q", "-b", "usb", str(tmp_path / "usb"), cwd=repo)
    (tmp_path / "usb").rename(tmp_path / "usb-away")
    wire(app, Script([
        [ToolCall(id="r1", name="Bash", arguments={"command": f"{GITX} push origin :keep-me"})],
        [ToolCall(id="r2", name="Bash", arguments={"command": f"{GITX} reset --hard HEAD~1"})],
        [ToolCall(id="r3", name="Bash", arguments={"command": f"{GITX} worktree prune"})],
        [ToolCall(id="r4", name="Bash", arguments={"command": f"{GITX} commit --amend -m rewritten"})],
        "Done.",
    ]))
    frames = await turn(app, sid, "clean up", "gr-refuse")
    assert not [d for e, d in frames if e == "approval"]
    rows = _ledger(app)
    assert len(rows) == 4 and all(r.output and r.output.startswith("Refused:") for r in rows), [r.output for r in rows]
    assert "keep-me" in git("branch", "--list", cwd=bare), "the remote branch was deleted"
    assert git("rev-parse", "HEAD", cwd=wt) == head, "the branch's history was rewritten"
    assert str(tmp_path / "usb").replace("\\", "/") in git("worktree", "list", "--porcelain", cwd=repo)


@pytest.mark.parametrize("posture", ["", "always_ask"])
async def test_a_refused_force_push_shows_no_card_first_in_any_posture(app, repo, tmp_path, posture):
    bare = make_remote(tmp_path, repo)
    before = git("rev-parse", "main", cwd=bare)
    view = await new_branch_session(app, repo, approval_mode=posture)
    wire(app, Script([[ToolCall(id="f1", name="Bash", arguments={"command": f"{GITX} push --force origin HEAD:main"})],
                      "Done."]))
    frames = await turn(app, view["id"], "force it", f"gr-force-{posture or 'default'}")
    assert not [d for e, d in frames if e == "approval"], "a call the tool refuses asks nothing first"
    (row,) = _ledger(app)
    assert G.FORCE_REFUSAL in (row.output or "")
    assert git("rev-parse", "main", cwd=bare) == before


async def test_throwing_away_work_cards_once_and_denied_keeps_the_work(app, repo):
    view = await new_branch_session(app, repo, approval_mode="yolo")
    sid, wt = view["id"], Path(view["git"]["worktree"])
    (wt / "app.py").write_bytes(b"x = 2  # not committed\n")
    wire(app, Script([[ToolCall(id="d1", name="Bash", arguments={"command": f"{GITX} checkout -- ."})], "Done."]))
    drop, task = await _start(app, sid, "start over", "gr-discard")
    card = (await until(lambda: [d for e, d in turn_frames(app, "gr-discard") if e == "approval"]))[0]
    assert card["once_only"] is True and "throws away changes" in card["cost_note"]
    assert (await _asgi_post(app, f"/chat/approvals/{card['id']}", {"decision": "deny"}))[0] == 200
    await _finish(app, "gr-discard", drop, task)
    assert (wt / "app.py").read_bytes() == b"x = 2  # not committed\n"


# --------------------------------------------------------------------------- #
# the POST lane refuses both kinds
# --------------------------------------------------------------------------- #


async def test_the_post_lane_refuses_a_rewrite_and_cannot_ask_about_a_discard(app, repo):
    from iron_jarvis.daemon.chat_turn import ONCE_NOT_ASKED_TAIL, run_chat_turn
    from iron_jarvis.daemon.schemas import ChatBody
    from iron_jarvis.harness import sessions as S
    from tests.test_build_git_agent_v1375 import _PostRouter

    view = await new_branch_session(app, repo)
    wt = Path(view["git"]["worktree"])
    (wt / "app.py").write_bytes(b"x = 2\n")
    git("commit", "-q", "-am", "one", cwd=wt)
    (wt / "README.md").write_bytes(b"# not committed\n")
    head = git("rev-parse", "HEAD", cwd=wt)
    platform = app.state.platform
    platform.router = router = _PostRouter([
        ToolCall(id="c1", name="shell", arguments={"command": f"{GITX} reset --hard HEAD~1"}),
        ToolCall(id="c2", name="shell", arguments={"command": f"{GITX} checkout -- ."}),
    ])
    token = B.set_turn_branch(S.git_of(S.get_row(platform.engine, view["id"])), str(wt))
    try:
        body = ChatBody(messages=[{"role": "user", "content": "reset"}], tools=["shell"],
                        workspace_dir=str(wt), thread_id="chat_post")
        await run_chat_turn(platform, {}, body)
    finally:
        B.reset_turn_branch(token)
    tool_msgs = [m for m in router.seen[1] if getattr(m, "role", "") == "tool"]
    assert "rewrites the branch's history" in tool_msgs[0].content
    assert ONCE_NOT_ASKED_TAIL in tool_msgs[1].content
    assert git("rev-parse", "HEAD", cwd=wt) == head
    assert (wt / "README.md").read_bytes() == b"# not committed\n"


# --------------------------------------------------------------------------- #
# a helper's own run
# --------------------------------------------------------------------------- #


async def test_a_helpers_commit_asks_once_and_its_rewrite_is_refused_with_no_ask(app, repo):
    from tests.test_harness_helpers_v1372 import Model, _helper_frames, _run
    from tests.test_harness_helpers_v1372 import wire as wire_model

    view = await new_branch_session(app, repo, approval_mode="yolo")
    sid, wt = view["id"], Path(view["git"]["worktree"])
    (wt / "app.py").write_bytes(b"x = 2\n")
    git("commit", "-q", "-am", "one", cwd=wt)
    head = git("rev-parse", "HEAD", cwd=wt)
    (wt / "README.md").write_bytes(b"# changed\n")

    def helper(task, results, rec):
        if not results:
            return [ToolCall(id="h1", name="shell", arguments={"command": f"{GITX} reset --hard HEAD~1"})]
        if len(results) == 1:
            return [ToolCall(id="h2", name="shell",
                             arguments={"command": f"{GITX} -c alias.ci=commit ci -am helper"})]
        return "helper finished"

    model = wire_model(app, Model(main=[[ToolCall(id="k1", name="Agent",
                                                  arguments={"description": "d", "prompt": "Tidy the branch."})],
                                        "ok"], helper=helper))
    task = asyncio.create_task(_run(app, sid, "go", "gr-helper"))
    waiting = await until(lambda: [d for e, d in turn_frames(app, "gr-helper")
                                   if e == "helper" and d.get("state") == "waiting"])
    ask = waiting[0]["ask"]
    assert ask["tool"] == "shell" and "ci -am helper" in ask["args"]["command"], "the rewrite asked nothing"
    assert (await _asgi_post(app, f"/chat/approvals/{ask['id']}", {"decision": "deny"}))[0] == 200
    frames = await asyncio.wait_for(task, 60)
    assert [d for d in _helper_frames(frames) if d.get("ended")]
    assert git("rev-parse", "HEAD", cwd=wt) == head, "the helper rewrote or committed"
    assert (wt / "README.md").read_bytes() == b"# changed\n"
    outputs = [r.output or "" for r in _ledger(app)]
    assert any("rewrites the branch's history" in o for o in outputs), outputs
    # The helper is told it works on the session's branch, like the session.
    helper_prompt = next(c["system"] for c in model.calls if not c["main"])
    assert f"own git branch, {view['git']['branch']}" in helper_prompt


async def test_a_git_setting_the_users_checkout_shares_cards_once_and_denied_changes_nothing(app, repo):
    view = await new_branch_session(app, repo, approval_mode="yolo")
    sid = view["id"]
    wire(app, Script([[ToolCall(id="g1", name="Bash",
                                arguments={"command": f"{GITX} config user.email agent@example.com"})], "Done."]))
    drop, task = await _start(app, sid, "set my email", "gr-config")
    card = (await until(lambda: [d for e, d in turn_frames(app, "gr-config") if e == "approval"]))[0]
    assert card["once_only"] is True and "git setting" in card["cost_note"]
    assert (await _asgi_post(app, f"/chat/approvals/{card['id']}", {"decision": "deny"}))[0] == 200
    await _finish(app, "gr-config", drop, task)
    # The worktree shares the repository's config with the user's checkout.
    assert git("config", "--get", "user.email", cwd=repo) == "dev@example.com"
