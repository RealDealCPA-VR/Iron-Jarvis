"""v1.375.0 (IJ harness part 2, Phase 4): a Build session on its OWN git branch.

Driven with REAL ``git`` in a temp folder — a scratch repository and a local
BARE remote — through the REAL app factory (real routes, real store, real
build turns and tools); only the model is a scripted double, and ``gh`` is a
fake on PATH (never the real GitHub). Pinned, the user's decisions 1–3:

* a new session in a repository starts on ``ij/<name>`` in a worktree at
  ``<parent>/<repo>.ij-worktrees/<name>``; the main checkout's files, index,
  HEAD and current branch are byte-for-byte unchanged; the session is listed
  under the repository's folder;
* a folder that is not in a repository is exactly as before, and the "work in
  the folder directly" switch makes no branch at all;
* an agent edit lands in the worktree; the Branch view shows committed AND
  uncommitted changes against the base;
* Commit (a drafted, edited message), Push (the bare remote gets the branch,
  never a force), Merge into the base (refused in words when the main
  checkout is dirty, refused on a conflict with NOTHING half-merged, the ref
  moved without touching any tree when the base is checked out nowhere),
  Discard (the folder in the trash, pruned, the branch still there);
* PATCH / PUT cannot forge the git facts; a rewind works inside a worktree;
  every git call goes through ``core.procs`` off the event loop.

Helpers here are imported by the other v1375 files.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun
from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import branches as B
from iron_jarvis.harness import sessions as S
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_build_sessions_v1367 import (
    Script,
    asgi,
    frames_of,
    messages_of,
    open_turn,
    setup_of,
    wire,
)

GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(GIT is None, reason="git is not installed")
CLAUDE = ("anthropic", "claude-sonnet-4-5")


# --------------------------------------------------------------------------- #
# helpers (imported by the other v1375 files)
# --------------------------------------------------------------------------- #


def git(*args: str, cwd: Path) -> str:
    """The TEST's own git (never the code under test): no optional locks, so
    reading a checkout never rewrites its index."""
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, env=env)
    assert r.returncode == 0, (args, r.stderr.decode("utf-8", "replace"))
    return r.stdout.decode("utf-8", "replace").rstrip()


def git_ok(*args: str, cwd: Path) -> bool:
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, env=env).returncode == 0


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git("init", "-q", "-b", "main", cwd=path)
    git("config", "user.email", "dev@example.com", cwd=path)
    git("config", "user.name", "Dev", cwd=path)
    git("config", "core.autocrlf", "false", cwd=path)
    (path / "app.py").write_bytes(b"x = 1\n")
    (path / "README.md").write_bytes(b"# proj\n")
    git("add", "-A", cwd=path)
    git("commit", "-q", "-m", "first", cwd=path)
    return path


def make_remote(tmp_path: Path, repo: Path) -> Path:
    bare = tmp_path / "remote.git"
    git("init", "-q", "--bare", "-b", "main", str(bare), cwd=tmp_path)
    git("remote", "add", "origin", str(bare), cwd=repo)
    git("push", "-q", "origin", "main", cwd=repo)
    return bare


def snapshot(repo: Path) -> dict:
    """Everything of the main checkout a session must never touch."""
    index = (repo / ".git" / "index").read_bytes()
    head = (repo / ".git" / "HEAD").read_bytes()
    files = {
        p.relative_to(repo).as_posix(): p.read_bytes()
        for p in sorted(repo.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(repo).parts
    }
    return {
        "index": index, "head": head, "files": files,
        "status": git("status", "--porcelain", cwd=repo),
        "branch": git("branch", "--show-current", cwd=repo),
        "main": git("rev-parse", "refs/heads/main", cwd=repo),
    }


def _path_without_gh() -> str:
    keep = []
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if not d:
            continue
        if any(os.path.exists(os.path.join(d, n)) for n in ("gh", "gh.exe", "gh.cmd", "gh.bat")):
            continue
        keep.append(d)
    return os.pathsep.join(keep)


FAKE_GH = r'''
import json, os, sys
path = os.environ["IJ_FAKE_GH_STATE"]
with open(path, encoding="utf-8") as fh:
    st = json.load(fh)
args = sys.argv[1:]
st.setdefault("calls", []).append(args)
def save():
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(st, fh)
if args[:2] == ["auth", "status"]:
    save()
    if st.get("signed_in"):
        print("Logged in to github.com account dev")
        sys.exit(0)
    print("You are not logged into any GitHub hosts.", file=sys.stderr)
    sys.exit(1)
if args[:2] == ["pr", "create"]:
    st["title"] = args[args.index("--title") + 1]
    with open(args[args.index("--body-file") + 1], encoding="utf-8") as fh:
        st["body"] = fh.read()
    st["base"] = args[args.index("--base") + 1]
    st["head"] = args[args.index("--head") + 1]
    save()
    print("https://github.com/acme/proj/pull/7")
    sys.exit(0)
if args[:2] == ["pr", "view"]:
    save()
    print(json.dumps({"url": "https://github.com/acme/proj/pull/7", "number": 7, "state": "OPEN",
                      "isDraft": False, "statusCheckRollup": st.get("checks", [])}))
    sys.exit(0)
save()
sys.exit(2)
'''


class FakeGh:
    """A ``gh`` on PATH that records every call and answers from a state file."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        self.state = folder / "state.json"
        self.state.write_text(json.dumps({"signed_in": True}), encoding="utf-8")
        script = folder / "fake_gh.py"
        script.write_text(FAKE_GH, encoding="utf-8")
        if sys.platform == "win32":
            (folder / "gh.cmd").write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
        else:
            launcher = folder / "gh"
            launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
            launcher.chmod(0o755)

    def set(self, **values) -> None:
        st = json.loads(self.state.read_text(encoding="utf-8"))
        st.update(values)
        self.state.write_text(json.dumps(st), encoding="utf-8")

    def read(self) -> dict:
        return json.loads(self.state.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _no_real_gh(monkeypatch):
    """NEVER the real GitHub CLI: every v1375 test runs with no ``gh`` on PATH
    unless it installs the fake; the remembered sign-in is forgotten."""
    monkeypatch.setenv("PATH", _path_without_gh())
    B.forget_gh_state()
    yield
    B.forget_gh_state()


@pytest.fixture()
def fake_gh(tmp_path, monkeypatch) -> FakeGh:
    gh = FakeGh(tmp_path / "fakegh")
    monkeypatch.setenv("IJ_FAKE_GH_STATE", str(gh.state))
    monkeypatch.setenv("PATH", str(gh.folder) + os.pathsep + os.environ["PATH"])
    B.forget_gh_state()
    return gh


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def repo(tmp_path) -> Path:
    return make_repo(tmp_path / "proj")


async def new_branch_session(app, folder: Path, **extra) -> dict:
    body = {"folder": str(folder), "provider": CLAUDE[0], "model": CLAUDE[1], **extra}
    status, view = await asgi(app, "POST", "/build/sessions", body)
    assert status == 201, view
    return view


async def turn(app, sid: str, text: str, turn_id: str) -> list:
    status, raw = await open_turn(app, sid, {"text": text, "turn_id": turn_id})
    assert status == 200, raw
    frames = frames_of(raw)
    assert frames and frames[-1][0] == "done", frames[-3:]
    return frames


# --------------------------------------------------------------------------- #
# a new session on its own branch; the main checkout untouched
# --------------------------------------------------------------------------- #


async def test_a_session_in_a_repository_starts_on_its_own_branch_beside_it_and_the_checkout_is_untouched(
    app, repo, tmp_path,
):
    before = snapshot(repo)
    view = await new_branch_session(app, repo)
    g = view["git"]
    assert g["branch"].startswith("ij/session-") and g["base"] == "main"
    name = g["branch"][len("ij/"):]
    wt = tmp_path / "proj.ij-worktrees" / name
    assert Path(g["worktree"]) == wt and Path(view["folder"]) == wt
    assert g["checkout"] == str(repo) and g["repo"] == str(repo)
    assert g["base_commit"] == before["main"][:12]
    # The worktree is a real checkout of the branch, from main's commit.
    assert git("branch", "--show-current", cwd=wt) == g["branch"]
    assert (wt / "app.py").read_bytes() == b"x = 1\n"
    assert (wt / ".git").is_file(), "a LINKED worktree (its .git is a file)"
    # Named and grouped by the folder the user picked — never a stray group.
    assert view["folder_name"] == "proj"
    status, listing = await asgi(app, "GET", "/build/sessions")
    assert status == 200
    assert [f["path"] for f in listing["folders"]] == [str(repo)]
    (row,) = listing["folders"][0]["sessions"]
    assert row["branch"] == g["branch"]
    # The main checkout: files, index, HEAD, status, current branch and main's
    # commit — byte for byte.
    assert snapshot(repo) == before
    # Nothing was made INSIDE the repository.
    assert not any(p.name.endswith(".ij-worktrees") for p in repo.rglob("*"))


async def test_a_folder_inside_the_repository_works_in_the_worktrees_copy_of_it(app, repo, tmp_path):
    (repo / "web").mkdir()
    (repo / "web" / "index.html").write_bytes(b"<p>hi</p>\n")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "web", cwd=repo)
    view = await new_branch_session(app, repo / "web")
    wt = Path(view["git"]["worktree"])
    assert wt.parent == tmp_path / "proj.ij-worktrees"
    assert Path(view["folder"]) == wt / "web"
    assert (wt / "web" / "index.html").read_bytes() == b"<p>hi</p>\n"
    status, listing = await asgi(app, "GET", "/build/sessions")
    assert [f["path"] for f in listing["folders"]] == [str(repo / "web")]


async def test_a_folder_not_in_a_repository_is_exactly_as_before(app, tmp_path, monkeypatch):
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "a.txt").write_bytes(b"a\n")
    started: list = []
    monkeypatch.setattr(B, "start", lambda *a, **k: started.append(a) or {})
    view = await new_branch_session(app, plain)
    assert started == [], "no branch is made for a folder outside a repository"
    assert "git" not in view and "branch_note" not in view
    assert view["folder"] == str(plain)
    # The stored record is exactly what the v1.367.0 path stores.
    old = S.create(app.state.platform.engine, str(plain), "", {"provider": CLAUDE[0], "model": CLAUDE[1]})
    assert setup_of(app, view["id"]) == setup_of(app, old.id)
    status, listing = await asgi(app, "GET", "/build/sessions")
    rows = [s for f in listing["folders"] for s in f["sessions"]]
    assert rows and all("branch" not in r for r in rows)
    # No folder was made beside it.
    assert not any(p.name.endswith(".ij-worktrees") for p in tmp_path.iterdir())
    # A turn's prompt carries no branch paragraph.
    script = wire(app, Script(["ok"]))
    await turn(app, view["id"], "hello", "plain-1")
    assert "own git branch" not in script.calls[0]["system"]


async def test_the_switch_works_in_the_folder_directly_with_no_git_call(app, repo, tmp_path, monkeypatch):
    from iron_jarvis.git import integration

    calls: list = []
    real = integration.run_git
    monkeypatch.setattr(integration, "run_git", lambda *a, **k: calls.append(a) or real(*a, **k))
    monkeypatch.setattr(B, "run_git", lambda *a, **k: calls.append(a) or real(*a, **k))
    before = snapshot(repo)
    view = await new_branch_session(app, repo, branch=False)
    assert calls == [], "the switch off makes no git call at all"
    assert "git" not in view and view["folder"] == str(repo)
    assert not (tmp_path / "proj.ij-worktrees").exists()
    assert snapshot(repo) == before


async def test_a_repository_with_no_commits_is_refused_on_the_switch_and_worked_in_directly_by_default(
    app, tmp_path,
):
    empty = tmp_path / "fresh"
    empty.mkdir()
    git("init", "-q", "-b", "main", cwd=empty)
    status, body = await asgi(app, "POST", "/build/sessions", {"folder": str(empty), "branch": True})
    assert status == 400 and body["detail"] == B.NO_COMMITS
    view = await new_branch_session(app, empty)
    assert "git" not in view and view["branch_note"] == B.NO_COMMITS


async def test_new_session_asks_the_folder_and_says_why_in_words(app, repo, tmp_path, monkeypatch):
    status, info = await asgi(app, "GET", f"/build/git/folder?path={repo}")
    assert status == 200 and info["repo"] is True and info["can_branch"] is True
    assert info["branch"] == "main" and info["worktrees_dir"] == str(tmp_path / "proj.ij-worktrees")
    plain = tmp_path / "plain"
    plain.mkdir()
    status, info = await asgi(app, "GET", f"/build/git/folder?path={plain}")
    assert info["repo"] is False and info["git"] is True and info["note"] == B.NOT_A_REPO
    monkeypatch.setattr(B, "git_path", lambda: None)
    status, info = await asgi(app, "GET", f"/build/git/folder?path={repo}")
    assert info["git"] is False and info["can_branch"] is False and info["note"] == B.NO_GIT
    # And a session made without git works in the folder directly.
    view = await new_branch_session(app, repo)
    assert "git" not in view and view["folder"] == str(repo)


# --------------------------------------------------------------------------- #
# the facts cannot be forged
# --------------------------------------------------------------------------- #


async def test_patch_and_put_cannot_forge_the_git_facts(app, repo, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    view = await new_branch_session(app, plain)
    sid = view["id"]
    forged = {"repo": str(repo), "worktree": str(repo), "branch": "ij/evil", "base": "main", "name": "proj"}
    status, _ = await asgi(app, "PATCH", f"/build/sessions/{sid}", {"title": "x", "git": forged})
    assert status == 200
    assert "git" not in setup_of(app, sid)
    status, _ = await asgi(app, "PUT", f"/chat/threads/{sid}", {"messages": [], "setup": {"git": forged}})
    assert status == 409
    assert "git" not in setup_of(app, sid)
    status, _ = await asgi(app, "POST", f"/build/sessions/{sid}/git/discard", {"confirm": True})
    assert status == 409, "a session with no branch has nothing to discard"
    # A CHAT thread holding a forged record is never a session: every git
    # route answers 404, and nothing is moved.
    status, saved = await asgi(app, "PUT", "/chat/threads/new",
                               {"messages": [{"role": "user", "content": "hi"}], "setup": {"git": forged}})
    assert status == 200
    for path in ("git", "git/diff"):
        assert (await asgi(app, "GET", f"/build/sessions/{saved['id']}/{path}"))[0] == 404
    status, _ = await asgi(app, "POST", f"/build/sessions/{saved['id']}/git/discard", {"confirm": True})
    assert status == 404
    assert (repo / "app.py").exists()


def test_a_record_pointing_anywhere_but_a_session_worktree_is_never_moved(tmp_path, repo):
    facts = {"repo": str(repo), "worktree": str(repo), "branch": "ij/evil", "name": "proj"}
    with pytest.raises(B.GitRefused) as err:
        B.remove_worktree(tmp_path / "home", facts)
    assert str(err.value) == B.NOT_A_WORKTREE
    assert (repo / "app.py").exists() and not (tmp_path / "home" / "trash").exists()


# --------------------------------------------------------------------------- #
# the agent works in the worktree; the Branch view
# --------------------------------------------------------------------------- #


async def test_an_agent_edit_lands_in_the_worktree_and_the_branch_view_shows_committed_and_not(
    app, repo, tmp_path,
):
    before = snapshot(repo)
    view = await new_branch_session(app, repo, approval_mode="yolo")
    sid, wt = view["id"], Path(view["git"]["worktree"])
    wire(app, Script([
        [ToolCall(id="e1", name="Edit", arguments={"file_path": "app.py", "old_string": "x = 1", "new_string": "x = 2"})],
        "Changed x.",
    ]))
    await turn(app, sid, "make x two", "g1")
    assert (wt / "app.py").read_bytes() == b"x = 2\n"
    assert snapshot(repo) == before, "the agent's edit never touched the main checkout"
    # Commit it, then leave a second change UNCOMMITTED and a new file untracked.
    status, done = await asgi(app, "POST", f"/build/sessions/{sid}/git/commit", {"message": "Make x two"})
    assert status == 200, done
    (wt / "README.md").write_bytes(b"# proj\nmore\n")
    (wt / "notes.md").write_bytes(b"one\ntwo\n")
    status, diff = await asgi(app, "GET", f"/build/sessions/{sid}/git/diff")
    assert status == 200, diff
    rows = {c["rel"]: c for c in diff["changes"]}
    assert set(rows) == {"app.py", "README.md", "notes.md"}
    assert rows["app.py"]["status"] == "modified" and (rows["app.py"]["added"], rows["app.py"]["removed"]) == (1, 1)
    assert "-x = 1" in rows["app.py"]["diff"] and "+x = 2" in rows["app.py"]["diff"]
    assert rows["README.md"]["status"] == "modified" and "+more" in rows["README.md"]["diff"]
    assert rows["notes.md"]["status"] == "created" and rows["notes.md"]["added"] == 2
    assert rows["notes.md"]["path"] == str(wt / "notes.md")
    assert diff["base"] == "main" and diff["merge_base"] == before["main"][:12]
    # Reading the Branch view wrote nothing: the worktree's index is unchanged.
    assert git("status", "--porcelain", cwd=wt).splitlines() == [" M README.md", "?? notes.md"]


async def test_the_status_counts_commits_files_and_where_the_base_is(app, repo):
    view = await new_branch_session(app, repo)
    sid, wt = view["id"], Path(view["git"]["worktree"])
    (wt / "app.py").write_bytes(b"x = 3\n")
    status, st = await asgi(app, "GET", f"/build/sessions/{sid}/git")
    assert status == 200, st
    assert st["exists"] is True and st["ahead"] == 0 and st["uncommitted"] == 1
    assert st["base_checked_out_at"] == str(repo) and st["remote"] == "" and st["pushed"] is False
    await asgi(app, "POST", f"/build/sessions/{sid}/git/commit", {"message": "x three"})
    status, st = await asgi(app, "GET", f"/build/sessions/{sid}/git")
    assert st["ahead"] == 1 and st["uncommitted"] == 0
    assert [c["subject"] for c in st["commits"]] == ["x three"]
    assert st["pr_draft"] == {"title": "x three", "body": ""}


# --------------------------------------------------------------------------- #
# commit: a drafted message, edited; nothing to commit says so
# --------------------------------------------------------------------------- #


async def test_commit_takes_a_drafted_message_the_user_edits_and_nothing_to_commit_says_so(app, repo):
    view = await new_branch_session(app, repo)
    sid, wt = view["id"], Path(view["git"]["worktree"])
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/git/draft", {})
    assert status == 409 and body["detail"] == B.NOTHING_TO_COMMIT
    (wt / "app.py").write_bytes(b"x = 5\n")
    script = wire(app, Script([]))
    script.complete_text = "```\nSet x to five\n\nThe answer moved.\n```"

    async def complete(**kw):
        script.calls.append({"system": kw.get("system"), "messages": kw.get("messages"), "kw": kw})
        from iron_jarvis.providers.adapters.base import LLMResponse
        from iron_jarvis.providers.router import RouteResult

        return RouteResult(LLMResponse(text=script.complete_text, usage={"input_tokens": 50, "output_tokens": 9}),
                           CLAUDE[0], CLAUDE[1])

    app.state.platform.router.complete = complete
    status, draft = await asgi(app, "POST", f"/build/sessions/{sid}/git/draft", {})
    assert status == 200, draft
    assert draft == {"drafted": True, "message": "Set x to five\n\nThe answer moved.",
                     "provider": CLAUDE[0], "model": CLAUDE[1]}
    # ONE pinned call on the session's own model, shown the change.
    (call,) = script.calls
    assert call["kw"]["pin"] is True and call["kw"]["provider"] == CLAUDE[0]
    assert "+x = 5" in call["messages"][0].content
    # Billed to the session.
    with session_scope(app.state.platform.engine) as db:
        assert [r for r in db.exec(select(AgentRun)) if getattr(r, "thread_id", "") == sid]
    # The user edits it, then commits.
    status, done = await asgi(app, "POST", f"/build/sessions/{sid}/git/commit",
                              {"message": "Set x to five (edited)\n\nThe answer moved."})
    assert status == 200, done
    assert done["committed"] is True and done["subject"] == "Set x to five (edited)"
    assert git("log", "-1", "--format=%s%n%b", cwd=wt).splitlines()[0] == "Set x to five (edited)"
    # As the user's own git identity (the repo's config), never "Iron Jarvis".
    assert git("log", "-1", "--format=%an <%ae>", cwd=wt) == "Dev <dev@example.com>"
    assert "note" not in done
    status, again = await asgi(app, "POST", f"/build/sessions/{sid}/git/commit", {"message": "again"})
    assert status == 409 and again["detail"] == B.NOTHING_TO_COMMIT
    status, empty = await asgi(app, "POST", f"/build/sessions/{sid}/git/commit", {"message": "  "})
    assert status == 400 and empty["detail"] == B.EMPTY_MESSAGE


async def test_the_demo_model_drafts_nothing_and_says_so(app, repo):
    view = await new_branch_session(app, repo, provider="mock", model="")
    sid, wt = view["id"], Path(view["git"]["worktree"])
    (wt / "app.py").write_bytes(b"x = 9\n")
    called: list = []

    async def complete(**kw):
        called.append(kw)
        raise AssertionError("the demo model is never asked")

    app.state.platform.router.complete = complete
    status, draft = await asgi(app, "POST", f"/build/sessions/{sid}/git/draft", {})
    assert status == 200 and draft["drafted"] is False and draft["message"] == ""
    assert "demo model" in draft["note"].lower()
    assert called == []


# --------------------------------------------------------------------------- #
# push
# --------------------------------------------------------------------------- #


async def test_push_lands_the_branch_on_the_remote_and_never_forces(app, repo, tmp_path, monkeypatch):
    bare = make_remote(tmp_path, repo)
    view = await new_branch_session(app, repo)
    sid, wt, branch = view["id"], Path(view["git"]["worktree"]), view["git"]["branch"]
    (wt / "app.py").write_bytes(b"x = 7\n")
    await asgi(app, "POST", f"/build/sessions/{sid}/git/commit", {"message": "seven"})
    seen: list[list[str]] = []
    real = B.run_git
    monkeypatch.setattr(B, "run_git", lambda args, *a, **k: seen.append(list(args)) or real(args, *a, **k))
    status, done = await asgi(app, "POST", f"/build/sessions/{sid}/git/push", {})
    assert status == 200, done
    assert done["pushed"] is True and done["remote"] == "origin"
    assert git("rev-parse", f"refs/heads/{branch}", cwd=bare) == git("rev-parse", "HEAD", cwd=wt)
    pushes = [a for a in seen if "push" in a]
    assert pushes and all(not any(t.startswith("-f") or "force" in t or t.startswith("+") for t in a)
                          for a in pushes)
    assert done["status"]["pushed"] is True and done["status"]["unpushed"] == 0
    assert setup_of(app, sid)["git"]["remote"] == "origin"


async def test_push_with_no_remote_says_so_in_words(app, repo):
    view = await new_branch_session(app, repo)
    status, body = await asgi(app, "POST", f"/build/sessions/{view['id']}/git/push", {})
    assert status == 409 and body["detail"] == B.NO_REMOTE


# --------------------------------------------------------------------------- #
# merge into the base
# --------------------------------------------------------------------------- #


async def _committed_session(app, repo, *, content=b"x = 2\n", message="two") -> tuple[str, Path, str]:
    view = await new_branch_session(app, repo)
    sid, wt = view["id"], Path(view["git"]["worktree"])
    (wt / "app.py").write_bytes(content)
    status, done = await asgi(app, "POST", f"/build/sessions/{sid}/git/commit", {"message": message})
    assert status == 200, done
    return sid, wt, view["git"]["branch"]


async def test_merge_brings_the_branch_into_the_checked_out_base_and_the_branch_stays(app, repo):
    sid, wt, branch = await _committed_session(app, repo)
    old_main = git("rev-parse", "main", cwd=repo)
    status, done = await asgi(app, "POST", f"/build/sessions/{sid}/git/merge", {})
    assert status == 200, done
    assert done["merged"] is True and done["into"] == "main" and done["where"] == str(repo)
    assert done["offer_remove"] is True
    # The user's checkout of main now has the work — a real merge commit.
    assert (repo / "app.py").read_bytes() == b"x = 2\n"
    parents = git("rev-list", "--parents", "-n", "1", "main", cwd=repo).split()[1:]
    assert parents == [old_main, git("rev-parse", "HEAD", cwd=wt)]
    assert git("status", "--porcelain", cwd=repo) == ""
    assert git("branch", "--show-current", cwd=repo) == "main"
    assert git_ok("rev-parse", "--verify", f"refs/heads/{branch}", cwd=repo), "the branch stays"
    assert setup_of(app, sid)["git"]["merged_into"] == "main"
    status, again = await asgi(app, "POST", f"/build/sessions/{sid}/git/merge", {})
    assert status == 409 and again["detail"] == B.NOTHING_TO_MERGE.format(base="main")


async def test_merge_is_refused_in_words_when_the_main_checkout_has_changes(app, repo):
    sid, _wt, _b = await _committed_session(app, repo)
    (repo / "README.md").write_bytes(b"# proj\nmine, not committed\n")
    before = snapshot(repo)
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/git/merge", {})
    assert status == 409
    assert "has changes that are not committed" in body["detail"] and "nothing was merged" in body["detail"]
    assert snapshot(repo) == before


async def test_a_merge_that_would_conflict_is_refused_and_nothing_is_left_half_merged(app, repo):
    sid, _wt, _b = await _committed_session(app, repo, content=b"x = 2\n")
    (repo / "app.py").write_bytes(b"x = 100\n")
    git("commit", "-q", "-am", "main moved", cwd=repo)
    before = snapshot(repo)
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/git/merge", {})
    assert status == 409, body
    assert "would conflict in app.py" in body["detail"] and "Nothing was changed" in body["detail"]
    assert snapshot(repo) == before
    assert not (repo / ".git" / "MERGE_HEAD").exists()


async def test_a_merge_whose_git_merge_fails_is_aborted_and_the_checkout_put_back(app, repo, monkeypatch):
    """Belt and braces: the conflict check passed but git's own merge still
    failed (a hook) — the checkout is put back, nothing half-merged."""
    sid, _wt, _b = await _committed_session(app, repo)
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    hook = hooks / "pre-merge-commit"
    hook.write_bytes(b"#!/bin/sh\necho no merges today >&2\nexit 1\n")
    hook.chmod(0o755)
    before = snapshot(repo)
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/git/merge", {})
    assert status == 409, body
    assert body["detail"] == "The merge did not go through, and your checkout was put back as it was."
    assert "no merges today" in body.get("details", "")
    after = snapshot(repo)
    # The abort rewrites the index file itself (fresh stat data); everything a
    # person can see is back as it was.
    for key in ("files", "status", "branch", "main", "head"):
        assert after[key] == before[key], key
    assert not (repo / ".git" / "MERGE_HEAD").exists()


async def test_a_base_checked_out_nowhere_is_moved_safely_without_touching_any_tree(app, repo):
    sid, wt, _b = await _committed_session(app, repo)
    git("switch", "-q", "-c", "other", cwd=repo)
    before = snapshot(repo)
    old_main = before["main"]
    status, done = await asgi(app, "POST", f"/build/sessions/{sid}/git/merge", {})
    assert status == 200, done
    assert done["where"] == ""
    new_main = git("rev-parse", "refs/heads/main", cwd=repo)
    assert git("rev-list", "--parents", "-n", "1", new_main, cwd=repo).split()[1:] == [
        old_main, git("rev-parse", "HEAD", cwd=wt),
    ]
    assert git("show", f"{new_main}:app.py", cwd=repo) == "x = 2"
    after = snapshot(repo)
    assert {k: v for k, v in after.items() if k != "main"} == {k: v for k, v in before.items() if k != "main"}


async def test_merge_wants_the_branchs_changes_committed_first(app, repo):
    sid, wt, _b = await _committed_session(app, repo)
    (wt / "app.py").write_bytes(b"x = 3\n")
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/git/merge", {})
    assert status == 409 and body["detail"] == B.UNCOMMITTED_BEFORE_MERGE


# --------------------------------------------------------------------------- #
# discard and delete: to the trash, pruned, the branch kept
# --------------------------------------------------------------------------- #


async def test_discard_takes_two_presses_moves_the_worktree_to_the_trash_and_keeps_the_branch(app, repo, tmp_path):
    sid, wt, branch = await _committed_session(app, repo)
    (wt / "draft.txt").write_bytes(b"not committed\n")
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/git/discard", {})
    assert status == 400 and body["detail"] == "Press Discard twice to confirm."
    assert wt.exists()
    status, done = await asgi(app, "POST", f"/build/sessions/{sid}/git/discard", {"confirm": True})
    assert status == 200, done
    assert done["moved"] is True and done["archived"] is True and done["branch"] == branch
    trash = Path(done["trash"])
    assert not wt.exists()
    assert trash.parent.parent == Path(app.state.platform.config.home) / "trash"
    assert (trash / "draft.txt").read_bytes() == b"not committed\n" and (trash / "app.py").exists()
    # git forgot the worktree; the branch (and its commit) is kept.
    assert str(wt) not in git("worktree", "list", "--porcelain", cwd=repo).replace("/", os.sep)
    assert git_ok("rev-parse", "--verify", f"refs/heads/{branch}", cwd=repo)
    # The session is archived and remembers it.
    status, v = await asgi(app, "GET", f"/build/sessions/{sid}")
    assert v["archived"] is True and v["git"]["removed"] is True
    status, st = await asgi(app, "GET", f"/build/sessions/{sid}/git")
    assert st["exists"] is False
    assert (await asgi(app, "GET", f"/build/sessions/{sid}/git/diff"))[0] == 409


async def test_delete_moves_the_worktree_to_the_trash_and_a_busy_one_is_refused_with_nothing_deleted(
    app, repo, tmp_path, monkeypatch,
):
    sid, wt, branch = await _committed_session(app, repo)

    def busy(src, dst):
        raise PermissionError(32, "The process cannot access the file because it is being used")

    monkeypatch.setattr(B.os, "rename", busy)
    status, body = await asgi(app, "DELETE", f"/build/sessions/{sid}")
    assert status == 409 and "The session was not deleted." in body["detail"] and "in use" in body["detail"]
    assert wt.exists() and (await asgi(app, "GET", f"/build/sessions/{sid}"))[0] == 200
    monkeypatch.undo()
    status, done = await asgi(app, "DELETE", f"/build/sessions/{sid}")
    assert status == 200, done
    assert done["deleted"] == sid and done["worktree"]["moved"] is True and done["worktree"]["branch"] == branch
    assert not wt.exists() and Path(done["worktree"]["trash"]).is_dir()
    assert git_ok("rev-parse", "--verify", f"refs/heads/{branch}", cwd=repo)


async def test_archive_keeps_the_worktree(app, repo):
    view = await new_branch_session(app, repo)
    wt = Path(view["git"]["worktree"])
    status, _ = await asgi(app, "POST", f"/build/sessions/{view['id']}/archive", {})
    assert status == 200 and wt.is_dir()
    status, _ = await asgi(app, "POST", f"/build/sessions/{view['id']}/unarchive", {})
    assert status == 200 and wt.is_dir()


# --------------------------------------------------------------------------- #
# rewind inside a worktree
# --------------------------------------------------------------------------- #


async def test_a_rewind_works_inside_the_worktree(app, repo):
    before = snapshot(repo)
    view = await new_branch_session(app, repo, approval_mode="yolo")
    sid, wt = view["id"], Path(view["git"]["worktree"])
    wire(app, Script([
        [ToolCall(id="a1", name="Write", arguments={"file_path": "hello.py", "content": "print(1)\n"}),
         ToolCall(id="a2", name="Edit", arguments={"file_path": "app.py", "old_string": "x = 1", "new_string": "x = 2"})],
        "one",
        [ToolCall(id="b1", name="Edit", arguments={"file_path": "app.py", "old_string": "x = 2", "new_string": "x = 3"})],
        "two",
    ]))
    await turn(app, sid, "first", "r1")
    await turn(app, sid, "second", "r2")
    assert (wt / "app.py").read_bytes() == b"x = 3\n" and (wt / "hello.py").exists()
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": "r1"})
    assert status == 200, out
    assert (wt / "app.py").read_bytes() == b"x = 1\n" and not (wt / "hello.py").exists()
    assert messages_of(app, sid) == []
    assert snapshot(repo) == before


# --------------------------------------------------------------------------- #
# off the loop, through core.procs
# --------------------------------------------------------------------------- #


async def test_every_git_call_runs_off_the_loop_through_core_procs(app, repo, tmp_path, monkeypatch):
    from iron_jarvis.core import procs

    make_remote(tmp_path, repo)
    loop_thread = threading.current_thread()
    seen: list[tuple[str, bool]] = []
    real = procs.run

    def watched(argv, **kw):
        if argv and Path(str(argv[0])).stem.lower() in ("git", "gh"):
            seen.append((" ".join(map(str, argv[:4])), threading.current_thread() is loop_thread))
        return real(argv, **kw)

    monkeypatch.setattr(procs, "run", watched)
    view = await new_branch_session(app, repo)
    sid, wt = view["id"], Path(view["git"]["worktree"])
    (wt / "app.py").write_bytes(b"x = 4\n")
    for method, path, body in (
        ("GET", "git", None), ("GET", "git/diff", None),
        ("POST", "git/commit", {"message": "four"}), ("POST", "git/push", {}),
        ("POST", "git/merge", {}), ("POST", "git/discard", {"confirm": True}),
    ):
        status, out = await asgi(app, method, f"/build/sessions/{sid}/{path}", body)
        assert status == 200, (path, out)
    assert len(seen) > 20
    assert not [c for c, on_loop in seen if on_loop], "a git call ran on the event loop"
