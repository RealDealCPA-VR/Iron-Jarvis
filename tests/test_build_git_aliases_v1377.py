"""v1.377.0 review (IJ harness part 2, Phase 6): THE GIT GUARD READS THE
USER'S OWN GIT ALIASES.

v1.375.0's guard reads a command's TEXT, and its docstring named the honest
limit: "an alias in the user's own git config … is not caught". Before Phase
6 that limit was nearly theoretical — the agent's shell had no HOME, so git
read no global config and found none of the user's aliases. Phase 6 hands a
Build session's commands the user's own home folder (the user's decision),
so every alias in ``~/.gitconfig`` is now LIVE in the agent's shell: with
``[alias] pf = push --force``, ``git pf`` force-pushed the session's branch
with no card and no refusal; with ``ci = commit``, ``git ci`` committed
without asking.

The guard now reads ``[alias]`` from the configs git itself would read for
the session (the user's global config — ``GIT_CONFIG_GLOBAL``, else
``$XDG_CONFIG_HOME/git/config`` and ``~/.gitconfig`` — and the repository's
own, following ``[include]`` / ``[includeIf]``), expands an alias (an alias
of an alias too) and a shell alias (``!git push --force``) before it judges.
Reading over-approximates on purpose: an alias can never shadow a built-in
git command, so an extra expansion can only make the guard stricter.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from iron_jarvis.harness import branches as B
from iron_jarvis.harness import git_guard as G

GIT = shutil.which("git")
needs_git = pytest.mark.skipif(GIT is None, reason="git is not installed")


def _arm(tmp_path: Path, monkeypatch, global_cfg: str, repo_cfg: str = "") -> Path:
    """A turn on a branch whose repository and user config carry aliases."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "config").write_text("[core]\n\tbare = false\n" + repo_cfg, encoding="utf-8")
    gcfg = tmp_path / "user.gitconfig"
    gcfg.write_text(global_cfg, encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gcfg))
    token = B.TURN_BRANCH.set(B.TurnBranch(
        checkout=str(repo), folder=str(tmp_path / "wt"), repo=str(repo), worktree=str(tmp_path / "wt"),
        branch="ij/x-1234", base="main",
    ))
    monkeypatch.setattr(G, "_ALIAS_CACHE", {}, raising=False)
    return token  # type: ignore[return-value]


@pytest.fixture()
def armed(tmp_path, monkeypatch):
    tokens = []

    def arm(global_cfg: str, repo_cfg: str = ""):
        tokens.append(_arm(tmp_path, monkeypatch, global_cfg, repo_cfg))

    yield arm
    for t in reversed(tokens):
        B.TURN_BRANCH.reset(t)


USER_ALIASES = """\
[user]
\tname = Someone
[alias]
\tpf = push --force-with-lease
\tci = commit
\tco = checkout
\tundo = reset --hard HEAD~1
\tyolo = !git push --force origin HEAD
\tship = !f() { git add -A && git commit -m wip && git push; }; f
\tsend = pf
\tst = status -sb
"""


@pytest.mark.parametrize("command, kind", [
    ("git pf", "force"),
    ("git ci -qam agent", "commit"),
    ("git undo", "rewrite"),
    ("git yolo", "force"),
    ("git ship", "commit"),
    ("git send", "force"),
    ("git -C sub ci -m x", "commit"),
])
def test_the_users_own_aliases_are_judged_as_what_they_run(armed, command, kind):
    armed(USER_ALIASES)
    kinds = [a.kind for a in G.classify(command)]
    assert kind in kinds, f"{command!r} was read as {kinds}"


def test_a_harmless_alias_and_an_unknown_word_stay_harmless(armed):
    armed(USER_ALIASES)
    assert G.classify("git st") == []
    assert G.classify("git co feature") == []
    assert G.classify("git frobnicate") == []


def test_a_force_alias_is_refused_and_a_commit_alias_asks(armed):
    armed(USER_ALIASES)
    assert G.refusal("git pf") == G.FORCE_REFUSAL
    check = G.guard_check("git ci -m agent")
    assert check is not None and check.ask is True and check.once_only is True


def test_an_alias_in_an_included_file_or_the_repository_counts(armed, tmp_path):
    extra = tmp_path / "aliases.inc"
    extra.write_text("[alias]\n\tfp = push -f\n", encoding="utf-8")
    armed(f"[include]\n\tpath = {extra.as_posix()}\n", repo_cfg="[alias]\n\tsave = commit -a\n")
    assert [a.kind for a in G.classify("git fp")] == ["force"]
    assert [a.kind for a in G.classify("git save -m x")] == ["commit"]


def test_with_no_branch_nothing_is_read_or_judged(tmp_path, monkeypatch):
    gcfg = tmp_path / "user.gitconfig"
    gcfg.write_text(USER_ALIASES, encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gcfg))
    assert B.current() is None
    assert G.guard_check("git pf") is None


@needs_git
async def test_a_force_push_alias_never_reaches_the_remote(tmp_path, monkeypatch):
    """End to end on the REAL registry and a REAL git: the user's global
    alias ``pf = push --force`` run by the agent in a Build session on its
    own branch is refused and the remote keeps its history."""
    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.providers.adapters.base import ToolCall
    from tests.test_build_git_v1375 import git, make_remote, make_repo, new_branch_session, no_real_gh, turn
    from tests.test_build_sessions_v1367 import Script, wire

    no_real_gh(monkeypatch)
    gcfg = tmp_path / "user-gitconfig"
    gcfg.write_text("[user]\n\tname = Phase Six\n\temail = p6@example.test\n"
                    "[alias]\n\tpf = push --force origin HEAD:main\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gcfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    app = create_app(str(tmp_path / "home"))
    repo = make_repo(tmp_path / "proj")
    bare = make_remote(tmp_path, repo)
    before = git("rev-parse", "refs/heads/main", cwd=bare)
    view = await new_branch_session(app, repo, approval_mode="yolo")
    wire(app, Script([[ToolCall(id="f1", name="Bash", arguments={"command": "git pf"})], "Done."]))
    frames = await turn(app, view["id"], "push it", "alias-force-1")
    finished = [d for e, d in frames if e == "tool_call" and d.get("status") == "finished"]
    assert finished and finished[0]["ok"] is False, "the force-push alias ran"
    assert git("rev-parse", "refs/heads/main", cwd=bare) == before
    os.environ.get("PATH")  # (nothing of the environment is printed)


def test_an_alias_never_hides_one_of_gits_own_commands(armed):
    """git ignores an alias named like its own command, and so must the guard:
    a ``push = status`` alias (in the config or on the line with ``-c``) must
    not make a REAL push read as a status."""
    armed("[alias]\n\tpush = status\n\tcommit = log\n")
    assert [a.kind for a in G.classify("git push --force")] == ["force"]
    assert [a.kind for a in G.classify("git -c alias.push=status push -f")] == ["force"]
    assert [a.kind for a in G.classify("git commit -m x")] == ["commit"]
