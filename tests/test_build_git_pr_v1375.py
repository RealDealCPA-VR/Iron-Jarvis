"""v1.375.0 (IJ harness part 2, Phase 4): Create PR and its checks through the
GitHub CLI — against a FAKE ``gh`` on PATH (signed in, signed out, missing);
never the real GitHub. The remote's FETCH address says github.com (so the
compare link is GitHub's), its PUSH address is a local bare repository (so a
push really lands somewhere, and nothing leaves this PC).

Pinned (the user's decision 3): Create PR only when ``gh`` is installed AND
signed in, pushing the branch first (never a force), with the user's title
and body; the PR link is kept on the session; the checks are read when asked;
signed out or no ``gh`` → a refusal in words, and the Branch view offers the
compare link instead; the sign-in check is remembered (it is a network call).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import branches as B
from iron_jarvis.onboarding.doctor import check_gh
from tests.test_build_git_v1375 import (
    FakeGh,
    git,
    install_fake_gh,
    make_repo,
    new_branch_session,
    no_real_gh,
)
from tests.test_build_sessions_v1367 import asgi, setup_of

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


@pytest.fixture(autouse=True)
def _no_real_gh(monkeypatch):
    no_real_gh(monkeypatch)
    yield
    B.forget_gh_state()


@pytest.fixture()
def fake_gh(tmp_path, monkeypatch) -> FakeGh:
    return install_fake_gh(tmp_path, monkeypatch)


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def repo(tmp_path) -> Path:
    return make_repo(tmp_path / "proj")


def _github_remote(tmp_path: Path, repo: Path) -> Path:
    bare = tmp_path / "remote.git"
    git("init", "-q", "--bare", "-b", "main", str(bare), cwd=tmp_path)
    git("remote", "add", "origin", "https://github.com/acme/proj.git", cwd=repo)
    git("remote", "set-url", "--push", "origin", str(bare), cwd=repo)
    git("push", "-q", "origin", "main", cwd=repo)
    return bare


async def _committed(app, repo: Path) -> tuple[str, Path, str]:
    view = await new_branch_session(app, repo)
    sid, wt = view["id"], Path(view["git"]["worktree"])
    (wt / "app.py").write_bytes(b"x = 2\n")
    status, done = await asgi(app, "POST", f"/build/sessions/{sid}/git/commit", {"message": "Make x two"})
    assert status == 200, done
    return sid, wt, view["git"]["branch"]


async def test_create_pr_pushes_first_and_opens_it_with_the_users_words(app, repo, tmp_path, fake_gh):
    bare = _github_remote(tmp_path, repo)
    sid, wt, branch = await _committed(app, repo)
    status, st = await asgi(app, "GET", f"/build/sessions/{sid}/git")
    assert status == 200, st
    assert st["gh"] == {"installed": True, "signed_in": True, "note": ""}
    assert st["compare_url"] == f"https://github.com/acme/proj/compare/main...{branch}?expand=1"
    assert st["pr_draft"] == {"title": "Make x two", "body": ""}
    status, pr = await asgi(app, "POST", f"/build/sessions/{sid}/git/pr",
                            {"title": "Make x two", "body": "Changes x.\n\n- one line"})
    assert status == 200, pr
    assert pr == {"url": "https://github.com/acme/proj/pull/7", "number": 7, "existed": False, "pushed": True}
    seen = fake_gh.read()
    assert (seen["title"], seen["body"], seen["base"], seen["head"]) == (
        "Make x two", "Changes x.\n\n- one line", "main", branch,
    )
    # The branch was pushed first (to the push address — never a force).
    assert git("rev-parse", f"refs/heads/{branch}", cwd=bare) == git("rev-parse", "HEAD", cwd=wt)
    kept = setup_of(app, sid)["git"]
    assert kept["pr_url"] == pr["url"] and kept["pr_number"] == "7"
    status, view = await asgi(app, "GET", f"/build/sessions/{sid}")
    assert view["git"]["pr_url"] == pr["url"]


async def test_the_checks_are_read_when_asked_and_said_in_words(app, repo, tmp_path, fake_gh):
    _github_remote(tmp_path, repo)
    sid, _wt, _b = await _committed(app, repo)
    fake_gh.set(checks=[
        {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"},
        {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "FAILURE"},
        {"__typename": "StatusContext", "state": "PENDING"},
    ])
    status, pr = await asgi(app, "GET", f"/build/sessions/{sid}/git/pr")
    assert status == 200, pr
    assert pr["url"] == "https://github.com/acme/proj/pull/7" and pr["state"] == "open"
    assert pr["checks"] == {"passed": 1, "failed": 1, "pending": 1}
    assert pr["words"] == "1 of 3 checks failed."
    fake_gh.set(checks=[{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"}] * 2)
    status, pr = await asgi(app, "GET", f"/build/sessions/{sid}/git/pr")
    assert pr["words"] == "All 2 checks passed."
    assert B.checks_words(0, 0, 0) == "No checks ran on this pull request."
    assert B.checks_words(0, 0, 2) == "2 of 2 checks still running."


async def test_signed_out_refuses_in_words_and_the_compare_link_stays(app, repo, tmp_path, fake_gh):
    _github_remote(tmp_path, repo)
    fake_gh.set(signed_in=False)
    sid, _wt, branch = await _committed(app, repo)
    status, st = await asgi(app, "GET", f"/build/sessions/{sid}/git")
    assert st["gh"]["installed"] is True and st["gh"]["signed_in"] is False
    assert "not signed in" in st["gh"]["note"] and "gh auth login" in st["gh"]["note"]
    assert st["compare_url"].endswith(f"/compare/main...{branch}?expand=1")
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/git/pr", {"title": "t", "body": ""})
    assert status == 409 and body["detail"] == st["gh"]["note"]
    assert not [c for c in fake_gh.read()["calls"] if c[:2] == ["pr", "create"]]


async def test_no_github_cli_says_so_and_offers_the_compare_link(app, repo, tmp_path):
    _github_remote(tmp_path, repo)
    sid, _wt, _b = await _committed(app, repo)
    assert B.gh_path() is None
    status, st = await asgi(app, "GET", f"/build/sessions/{sid}/git")
    assert st["gh"]["installed"] is False and "not installed" in st["gh"]["note"]
    assert st["compare_url"].startswith("https://github.com/acme/proj/compare/main...")
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/git/pr", {"title": "t", "body": ""})
    assert status == 409 and "not installed" in body["detail"]
    status, body = await asgi(app, "GET", f"/build/sessions/{sid}/git/pr")
    assert status == 409 and "not installed" in body["detail"]
    row = check_gh()
    assert row["ok"] is False and "Create PR opens GitHub's own page instead" in row["detail"]


async def test_a_remote_not_on_github_has_no_compare_link(app, repo, tmp_path, fake_gh):
    bare = tmp_path / "remote.git"
    git("init", "-q", "--bare", "-b", "main", str(bare), cwd=tmp_path)
    git("remote", "add", "origin", str(bare), cwd=repo)
    sid, _wt, _b = await _committed(app, repo)
    status, st = await asgi(app, "GET", f"/build/sessions/{sid}/git")
    assert st["compare_url"] == "" and st["gh"]["note"] == "This remote is not on GitHub."
    assert not [c for c in fake_gh.read().get("calls", []) if c[:2] == ["auth", "status"]]


async def test_the_sign_in_check_is_remembered(app, repo, tmp_path, fake_gh):
    _github_remote(tmp_path, repo)
    sid, _wt, _b = await _committed(app, repo)
    for _ in range(3):
        await asgi(app, "GET", f"/build/sessions/{sid}/git")
    assert len([c for c in fake_gh.read()["calls"] if c[:2] == ["auth", "status"]]) == 1


def test_compare_links_for_the_shapes_a_remote_address_takes():
    facts = {"base": "main", "branch": "ij/fix-3f2a"}
    for url in ("https://github.com/acme/proj.git", "git@github.com:acme/proj.git",
                "ssh://git@github.com/acme/proj", "https://token123@github.com/acme/proj"):
        assert B.compare_url(facts, url) == "https://github.com/acme/proj/compare/main...ij/fix-3f2a?expand=1", url
    assert B.compare_url(facts, "https://gitlab.com/acme/proj.git") == ""
    assert B.compare_url({"base": "", "branch": "ij/x"}, "https://github.com/a/b") == ""
