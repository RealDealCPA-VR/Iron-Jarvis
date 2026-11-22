"""22-b (R3.4): Build › Code's Commit and Open a pull request, one press each
(``hosted/session_git``, ``routes/build_app_git``).

Real git, a bare repository as the remote (its FETCH address says github.com,
its PUSH address is the bare one, so nothing leaves this PC) and a FAKE
``gh`` on PATH (``tests/test_build_git_v1375``): never the real GitHub, never
a real coding tool (the in-process ``FakeDriver``, ``docs/rules/ci.md``).

Pinned:
* a session on its OWN branch (the start box's yes): a stand-in Codex reply
  is committed and its pull request opened in TWO presses; ``pr_url`` is on
  the hosted row; the user's main checkout is byte for byte unchanged until
  Merge; Discard moves its copy of the folder to the trash, keeps the branch
  and archives the session;
* the commit message is drafted from the session's own record (its first
  message and the files), never by a model;
* a session in the folder directly commits ONLY the ticked files, and what
  the user had staged stays staged; a file the session did not change is
  listed unticked;
* a pull request from the folder directly is offered only off the main
  branch, and its push writes no upstream into the user's config;
* a press is refused while a reply runs, and holds the session while it runs;
* the tool of a branch session runs in its own copy of the folder with the
  main folder's environment;
* (review) a press is refused while Iron Jarvis checks the reply's tests; a
  reply that starts while a commit waits for its locks stops the commit; a
  branch session whose row cannot be written leaves no branch and no copy; a
  branch session's work record and project row name the folder the user
  picked; chat's feature lookup finds where Commit and the pull request are.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import branches as B
from iron_jarvis.harness import folder_defaults as FD
from iron_jarvis.hosted import check_again as CA
from iron_jarvis.hosted import panes as PN
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import session_git as SG
from iron_jarvis.hosted import store
from iron_jarvis.hosted import words as W
from iron_jarvis.hosted.events import make

from ._build_app_helpers_v1409 import client, stop_all
from .test_build_app_history_v1418 import fake_tool, until
from .test_build_git_v1375 import git, git_ok, install_fake_gh, make_repo, no_real_gh, snapshot

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

#: A FAILURE bound for waits on git and the message's start record, never a
#: speed claim (a busy PC is slow).
WAIT_S = 120.0
ASKED = "add a rate limiter to the api"


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


def github_remote(tmp_path: Path, repo: Path) -> Path:
    bare = tmp_path / "remote.git"
    git("init", "-q", "--bare", "-b", "main", str(bare), cwd=tmp_path)
    git("remote", "add", "origin", "https://github.com/acme/proj.git", cwd=repo)
    git("remote", "set-url", "--push", "origin", str(bare), cwd=repo)
    git("push", "-q", "origin", "main", cwd=repo)
    # The remote-tracking copy the fetch address would give.
    git("update-ref", "refs/remotes/origin/main", "refs/heads/main", cwd=repo)
    return bare


async def patient_start(monkeypatch) -> None:
    """The message waits for its start record (the folder's checkpoint) before
    the stand-in tool gets it; a busy PC can take longer than the product
    waits, and then the message's own changes cannot be told apart."""
    real_start = PN.record_turn_start_async

    async def patient(*a, **k):
        k["bound"] = WAIT_S
        return await real_start(*a, **k)

    monkeypatch.setattr(PN, "record_turn_start_async", patient)


async def message(svc, sid: str, drivers, text: str = ASKED, turn: str = "t1") -> None:
    await svc.start_turn(sid, text, turn)
    await until(lambda: sid in drivers and drivers[sid].sent, timeout=WAIT_S)
    await until(lambda: any(e.get("how") == "start" and e.get("turn") == turn
                            for e in PN.read_trees(svc.home, sid)), timeout=WAIT_S)


def wrote(svc, sid: str, folder: Path, rel: str, body: str, *, item: str = "w1") -> None:
    """The stand-in tool writes a file with its own file tool."""
    path = folder / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    svc._emit(svc.live[sid], make("tool", item=item, kind="write", name="Write", title=f"Write {rel}",
                                  path=str(path), status="done",
                                  diff=f"--- /dev/null\n+++ b/{rel}\n@@ -0,0 +1 @@\n+x\n"))


async def reply(svc, sid: str, text: str = "Done: the limiter is in.") -> None:
    live = svc.live[sid]
    replied = live.turn
    svc._emit(live, make("text_done", item="a1", text=text))
    svc._emit(live, make("done", stop="completed"))
    if replied is not None:
        await until(lambda: live.turn is not replied, timeout=WAIT_S)
    for _ in range(20):
        await asyncio.sleep(0)
    await until(lambda: not CA.judging(svc), timeout=WAIT_S)


def no_model(app) -> None:
    """The draft never calls a model: any call fails the test."""
    async def refuse(*_a, **_k):
        raise AssertionError("a model was called")

    app.state.platform.router.complete = refuse


# --------------------------------------------------------------------------- #
# A session on its own branch: two presses
# --------------------------------------------------------------------------- #
async def test_a_branch_session_is_committed_and_its_pull_request_opened_in_two_presses(
        app, repo, tmp_path, monkeypatch):
    bare = github_remote(tmp_path, repo)
    fake = install_fake_gh(tmp_path, monkeypatch)
    no_model(app)
    await patient_start(monkeypatch)
    before = snapshot(repo)
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    async with client(app) as c:
        r = await c.post("/build/app/sessions", json={"harness": "codex", "folder": str(repo), "branch": True})
        assert r.status_code == 201, r.text
        made = r.json()
        sid = made["id"]
        g = made["git"]
        assert g["branch"].startswith("ij/") and g["base"] == "main" and g["checkout"] == str(repo)
        wt = Path(made["folder"])
        assert wt == Path(g["worktree"]) and wt.parent.name == "proj.ij-worktrees"
        assert made["checkout"] == str(repo)
        # Listed under the folder the user PICKED.
        listed = (await c.get("/build/app/sessions", params={"folder": str(repo)})).json()["sessions"]
        assert [s["id"] for s in listed] == [sid]

        await message(svc, sid, drivers)
        wrote(svc, sid, wt, "api/limits.py", "LIMIT = 4\n")
        await reply(svc, sid)

        st = (await c.get(f"/build/app/sessions/{sid}/git")).json()
        assert st["kind"] == "branch" and st["uncommitted"] == 1
        assert st["files"] == [{"rel": "api/limits.py", "status": "created", "ticked": True}]
        draft = st["draft"]
        assert draft.startswith("Add a rate limiter to the api\n") and "- api/limits.py" in draft

        # Press 1: Commit, with the message as drafted.
        r = await c.post(f"/build/app/sessions/{sid}/git/commit", json={"message": draft})
        assert r.status_code == 200, r.text
        assert r.json()["committed"] is True and r.json()["subject"] == "Add a rate limiter to the api"
        assert snapshot(repo) == before, "the main checkout is untouched by a commit"

        # Press 2: Open a pull request, with the branch's own prefill.
        st = (await c.get(f"/build/app/sessions/{sid}/git")).json()
        assert st["pr_draft"]["title"] == "Add a rate limiter to the api"
        r = await c.post(f"/build/app/sessions/{sid}/git/pr", json=st["pr_draft"])
        assert r.status_code == 200, r.text
        assert r.json()["url"] == "https://github.com/acme/proj/pull/7"
        seen = fake.read()
        assert (seen["base"], seen["head"], seen["title"]) == ("main", g["branch"], "Add a rate limiter to the api")
        assert git("rev-parse", f"refs/heads/{g['branch']}", cwd=bare) == git("rev-parse", "HEAD", cwd=wt)
        row = store.get(app.state.platform.engine, sid)
        assert json.loads(row.git_json)["pr_url"] == "https://github.com/acme/proj/pull/7"
        view = (await c.get(f"/build/app/sessions/{sid}")).json()["session"]
        assert view["git"]["pr_url"] == "https://github.com/acme/proj/pull/7"
        assert snapshot(repo) == before, "the main checkout is untouched by a push and a pull request"

        # Merge is the ONE press that changes the main checkout.
        r = await c.post(f"/build/app/sessions/{sid}/git/merge")
        assert r.status_code == 200, r.text
        assert (repo / "api" / "limits.py").read_text(encoding="utf-8") == "LIMIT = 4\n"
        assert snapshot(repo)["main"] != before["main"]

        # Discard: the copy goes to the trash, the branch stays, the session is archived.
        r = await c.post(f"/build/app/sessions/{sid}/git/discard", json={"confirm": False})
        assert r.status_code == 400 and wt.is_dir()
        r = await c.post(f"/build/app/sessions/{sid}/git/discard", json={"confirm": True})
        assert r.status_code == 200, r.text
        assert not wt.exists() and Path(r.json()["trash"]).is_dir()
        assert git_ok("rev-parse", "--verify", f"refs/heads/{g['branch']}", cwd=repo)
        row = store.get(app.state.platform.engine, sid)
        assert row.archived_at is not None and json.loads(row.git_json).get("removed_at")
    await stop_all(app)


async def test_a_branch_session_needs_a_folder_that_can_branch(app, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    async with client(app) as c:
        r = await c.post("/build/app/sessions", json={"harness": "codex", "folder": str(plain), "branch": True})
        assert r.status_code == 400 and r.json()["field"] == "folder"
        assert r.json()["detail"] == B.NOT_A_REPO
        r = await c.post("/build/app/sessions", json={"harness": "codex", "branch": True})
        assert r.status_code == 400 and r.json()["detail"] == SG.BRANCH_NEEDS_FOLDER
        r = await c.post("/build/app/sessions", json={"harness": "codex", "folder": str(plain), "branch": "yes"})
        assert r.status_code == 422, "only a real true starts a branch"
        assert store.list_rows(app.state.platform.engine) == []
    await stop_all(app)


# --------------------------------------------------------------------------- #
# A session in the folder directly: only the ticked files
# --------------------------------------------------------------------------- #
async def test_a_folder_session_commits_only_the_ticked_files_and_the_users_index_is_untouched(
        app, repo, monkeypatch):
    no_model(app)
    await patient_start(monkeypatch)
    # Before the session: the user staged a README change and has a note of
    # their own that git does not know yet.
    (repo / "README.md").write_bytes(b"# proj, staged by the user\n")
    git("add", "README.md", cwd=repo)
    (repo / "notes.txt").write_bytes(b"mine\n")
    staged_before = git("show", ":README.md", cwd=repo)
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    async with client(app) as c:
        made = (await c.post("/build/app/sessions", json={"harness": "codex", "folder": str(repo)})).json()
        sid = made["id"]
        assert "git" not in made and made["folder"] == str(repo) and made["checkout"] == str(repo)
        await message(svc, sid, drivers)
        (repo / "app.py").write_bytes(b"x = 4\n")  # a command changed it
        wrote(svc, sid, repo, "api/limits.py", "LIMIT = 4\n")
        await reply(svc, sid)

        st = (await c.get(f"/build/app/sessions/{sid}/git")).json()
        assert st["kind"] == "folder" and st["branch"] == "main" and st["on_main"] is True
        ticks = {f["rel"]: f["ticked"] for f in st["files"]}
        assert ticks == {"README.md": False, "notes.txt": False, "app.py": True, "api/limits.py": True}
        assert st["can_pr"] is False and SG.ON_MAIN_PR in st["pr_note"]
        assert st["draft"].startswith("Add a rate limiter to the api")
        assert "- app.py" in st["draft"] and "README.md" not in st["draft"]

        head = git("rev-parse", "HEAD", cwd=repo)
        r = await c.post(f"/build/app/sessions/{sid}/git/commit", json={"message": "x", "paths": []})
        assert r.status_code == 400 and r.json()["detail"] == SG.TICK_FIRST
        r = await c.post(f"/build/app/sessions/{sid}/git/commit", json={"message": "x", "paths": ["nope.py"]})
        assert r.status_code == 400 and "nope.py" in r.json()["detail"]
        assert git("rev-parse", "HEAD", cwd=repo) == head, "a refused commit commits nothing"

        r = await c.post(f"/build/app/sessions/{sid}/git/commit",
                         json={"message": st["draft"], "paths": ["app.py", "api/limits.py"]})
        assert r.status_code == 200, r.text
        assert r.json()["kind"] == "folder" and r.json()["files"] == 2
        committed = set(git("show", "--name-only", "--format=", "HEAD", cwd=repo).split())
        assert committed == {"app.py", "api/limits.py"}
        # What the user staged stays staged, exactly; their note stays theirs.
        assert git("diff", "--cached", "--name-only", cwd=repo) == "README.md"
        assert git("show", ":README.md", cwd=repo) == staged_before
        assert git("status", "--porcelain", "--", "notes.txt", cwd=repo) == "?? notes.txt"
        assert git("status", "--porcelain", "--", "app.py", "api/limits.py", cwd=repo) == ""
    await stop_all(app)


async def test_a_new_file_a_commit_refused_is_taken_back_out_of_the_index(app, repo, monkeypatch):
    """A hook that refuses the commit leaves the index as it was (the new
    file told to git "to be added" is taken back)."""
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "pre-commit").write_text("#!/bin/sh\necho no >&2\nexit 1\n", encoding="utf-8", newline="\n")
    os.chmod(hooks / "pre-commit", 0o755)
    (repo / "new.py").write_text("y = 1\n", encoding="utf-8")
    head = git("rev-parse", "HEAD", cwd=repo)
    with pytest.raises(B.GitRefused) as got:
        SG.folder_commit(str(repo), "Add new", ["new.py"])
    assert got.value.status == 409 and got.value.details
    assert git("rev-parse", "HEAD", cwd=repo) == head
    assert git("status", "--porcelain", cwd=repo) == "?? new.py"
    assert git("ls-files", cwd=repo).split() == ["README.md", "app.py"], "the index is as it was"


# --------------------------------------------------------------------------- #
# A pull request from the folder directly
# --------------------------------------------------------------------------- #
async def test_a_folder_on_the_main_branch_is_not_offered_a_pull_request_and_one_on_its_own_branch_is(
        app, repo, tmp_path, monkeypatch):
    bare = github_remote(tmp_path, repo)
    fake = install_fake_gh(tmp_path, monkeypatch)
    svc = S.service_for(app.state.platform)
    fake_tool(svc, monkeypatch)
    async with client(app) as c:
        sid = (await c.post("/build/app/sessions", json={"harness": "claude-code", "folder": str(repo)})).json()["id"]
        r = await c.post(f"/build/app/sessions/{sid}/git/pr", json={"title": "t", "body": ""})
        assert r.status_code == 409 and r.json()["detail"].endswith(SG.ON_MAIN_PR)
        assert not [x for x in fake.read().get("calls", []) if x[:2] == ["pr", "create"]]

        git("switch", "-q", "-c", "feature", cwd=repo)
        (repo / "app.py").write_bytes(b"x = 5\n")
        r = await c.post(f"/build/app/sessions/{sid}/git/commit", json={"message": "Make x five", "paths": ["app.py"]})
        assert r.status_code == 200, r.text
        st = (await c.get(f"/build/app/sessions/{sid}/git")).json()
        assert st["branch"] == "feature" and st["main"] == "main" and st["can_pr"] is True and st["ahead"] == 1
        assert st["pr_draft"]["title"] == "Make x five"
        r = await c.post(f"/build/app/sessions/{sid}/git/pr", json={"title": "Make x five", "body": "One line."})
        assert r.status_code == 200, r.text
        assert r.json()["url"] == "https://github.com/acme/proj/pull/7" and r.json()["pushed"] is True
        seen = fake.read()
        assert (seen["base"], seen["head"]) == ("main", "feature")
        assert git("rev-parse", "refs/heads/feature", cwd=bare) == git("rev-parse", "HEAD", cwd=repo)
        assert not git_ok("config", "--get", "branch.feature.remote", cwd=repo), "no upstream was written"
        row = store.get(app.state.platform.engine, sid)
        kept = json.loads(row.git_json)
        assert kept["pr_url"] == "https://github.com/acme/proj/pull/7" and kept["branch"] == "feature"
        assert SG.facts_of(row) is None, "a folder session's pull request never reads as a branch of its own"
        view = (await c.get(f"/build/app/sessions/{sid}")).json()["session"]
        assert view["pull_request"] == {"url": "https://github.com/acme/proj/pull/7", "number": 7,
                                        "branch": "feature"}
        assert "git" not in view
        pr = (await c.get(f"/build/app/sessions/{sid}/git/pr")).json()
        assert pr["url"] == "https://github.com/acme/proj/pull/7"
    await stop_all(app)


# --------------------------------------------------------------------------- #
# Never while a reply runs; the press holds the session
# --------------------------------------------------------------------------- #
async def test_a_press_is_refused_while_a_reply_runs_and_holds_the_session_while_it_runs(
        app, repo, monkeypatch):
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    (repo / "app.py").write_bytes(b"x = 6\n")
    async with client(app) as c:
        sid = (await c.post("/build/app/sessions", json={"harness": "codex", "folder": str(repo)})).json()["id"]
        await svc.start_turn(sid, ASKED, "t1")
        await until(lambda: sid in drivers and drivers[sid].sent, timeout=WAIT_S)
        r = await c.post(f"/build/app/sessions/{sid}/git/commit", json={"message": "m", "paths": ["app.py"]})
        assert r.status_code == 409 and r.json()["detail"] == W.RUNNING
        await reply(svc, sid)

        held: list[str] = []
        real = SG.folder_commit

        def spy(folder, msg, paths):
            held.append(svc.live[sid].claim)
            return real(folder, msg, paths)

        monkeypatch.setattr(SG, "folder_commit", spy)
        r = await c.post(f"/build/app/sessions/{sid}/git/commit", json={"message": "m", "paths": ["app.py"]})
        assert r.status_code == 200, r.text
        assert held == [SG.CLAIM], "no message starts while the commit runs"
        assert svc.live[sid].claim == "", "and the session is free again after it"
    await stop_all(app)


async def test_merge_waits_while_another_sessions_reply_works_in_the_picked_folder(app, repo, monkeypatch):
    from iron_jarvis.hosted import branching as BR

    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    async with client(app) as c:
        branchy = (await c.post("/build/app/sessions",
                                json={"harness": "codex", "folder": str(repo), "branch": True})).json()
        other = (await c.post("/build/app/sessions", json={"harness": "claude-code", "folder": str(repo)})).json()
        await svc.start_turn(other["id"], "tidy the readme", "o1")
        await until(lambda: other["id"] in drivers and drivers[other["id"]].sent, timeout=WAIT_S)
        before = snapshot(repo)
        r = await c.post(f"/build/app/sessions/{branchy['id']}/git/merge")
        assert r.status_code == 409 and r.json()["detail"] == BR.OTHER_SESSION
        assert snapshot(repo) == before
        await reply(svc, other["id"])
    await stop_all(app)


# --------------------------------------------------------------------------- #
# The tool of a branch session: its own copy, the main folder's environment
# --------------------------------------------------------------------------- #
async def test_a_branch_sessions_tool_runs_in_its_copy_with_the_main_folders_environment(app, repo, monkeypatch):
    (repo / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    git("add", ".gitignore", cwd=repo)
    git("commit", "-q", "-m", "ignore", cwd=repo)
    (repo / "node_modules" / ".bin").mkdir(parents=True)
    svc = S.service_for(app.state.platform)
    seen: dict = {}

    class Spy:
        def __init__(self, argv, *, env, cwd, **_k):
            seen.update(env=env, cwd=cwd)
            raise RuntimeError("spied")

    monkeypatch.setattr(S, "HarnessProcess", Spy)
    monkeypatch.setattr(S.HL, "enabled", lambda: False)
    async with client(app) as c:
        made = (await c.post("/build/app/sessions",
                             json={"harness": "codex", "folder": str(repo), "branch": True})).json()
    row = store.get(app.state.platform.engine, made["id"])
    with pytest.raises(Exception):
        await svc._start_process(svc._live(row), row, ["codex"])
    assert seen["cwd"] == made["folder"] != str(repo)
    path = next(v for k, v in seen["env"].items() if k.upper() == "PATH")
    assert path.split(os.pathsep)[0] == str(repo / "node_modules" / ".bin")
    # A session in the folder directly runs with the PATH as it was.
    seen.clear()
    plain = store.create(app.state.platform.engine, harness="codex", folder=str(repo))
    with pytest.raises(Exception):
        await svc._start_process(svc._live(plain), plain, ["codex"])
    path = next(v for k, v in seen["env"].items() if k.upper() == "PATH")
    assert str(repo / "node_modules" / ".bin") not in path.split(os.pathsep)
    await stop_all(app)


# --------------------------------------------------------------------------- #
# The folder's answer, kept once, and the check's consent on the picked folder
# --------------------------------------------------------------------------- #
async def test_the_folders_answer_is_kept_once_and_the_other_defaults_keep_it(app, repo):
    engine = app.state.platform.engine
    async with client(app) as c:
        got = (await c.get("/build/folders/defaults", params={"folder": str(repo)})).json()
        assert "branch" not in got["defaults"], "not asked yet"
        r = await c.put("/build/folders/defaults",
                        json={"folder": str(repo), "view": "branch", "defaults": {"work": "branch"}})
        assert r.status_code == 200, r.text
        assert r.json()["defaults"]["branch"] == {"work": "branch"}
        r = await c.put("/build/folders/defaults",
                        json={"folder": str(repo), "view": "branch", "defaults": {"work": "maybe"}})
        assert r.status_code == 400
    FD.save(engine, str(repo), "tool", {"harness": "codex"})
    FD.save_checks(engine, str(repo), {"off": True, "at": "2026-11-22T00:00:00Z"})
    assert FD.read(engine, str(repo))["branch"] == {"work": "branch"}
    await stop_all(app)


def test_the_draft_is_the_first_message_masked_and_the_files():
    events = [
        {"type": "user", "text": "Check again: the tests failed", "carry": {"why": "tests_failed"}},
        {"type": "user", "text": "  add a rate limiter to the api, password=plainwords\nand keep it at four  ",
         "turn": "t1"},
        {"type": "user", "text": "later words", "turn": "t2"},
    ]
    got = SG.draft_message(events, ["api/limits.py", "app.py"])
    summary, rest = got.split("\n", 1)
    assert summary == "Add a rate limiter to the api, password=***"
    assert "plainwords" not in got, "a pasted secret never reaches a commit message"
    assert "and keep it at four" in rest and rest.endswith("Files:\n- api/limits.py\n- app.py")
    long = "x " * 100
    assert len(SG.draft_message([{"type": "user", "text": long}], []).split("\n")[0]) <= SG.SUMMARY_CHARS
    assert SG.draft_message([], ["app.py", "b.py"]).startswith("Change app.py and 1 more")
    assert SG.draft_message([], []) == SG.NO_MESSAGE


async def test_the_check_after_a_branch_sessions_reply_reads_the_picked_folders_answer(app, repo, monkeypatch):
    from iron_jarvis.hosted import proof as PROOF

    async with client(app) as c:
        made = (await c.post("/build/app/sessions",
                             json={"harness": "codex", "folder": str(repo), "branch": True})).json()
    svc = S.service_for(app.state.platform)
    row = store.get(app.state.platform.engine, made["id"])
    svc._live(row)
    seen: list = []

    def spy(engine, folder, *, key=""):
        seen.append((folder, key))
        return {"state": "none"}

    monkeypatch.setattr(PROOF, "decide", spy)
    assert await PROOF.check(svc, row.id, "t1", [], row=row) is None
    assert seen == [(made["folder"], str(repo))]
    await stop_all(app)


@pytest.mark.asyncio
async def test_chat_sets_the_folders_new_branch_answer_with_an_undo(tmp_path, repo):
    from iron_jarvis.settings import records_build as RB
    from tests.test_chat_approvals_v1187 import _drive_stream
    from tests.test_records_build_v14380 import _undo
    from tests.test_settings_chat_tools_v1321 import _body
    from tests.test_settings_records_v1321 import _app, _approve_all, _card

    said = f"start new Build › Code sessions in {repo} on their own branch"
    assert "build_defaults_set" in RB.build_tools_for(said), "the sentence arms the folder's tools"
    assert "build_defaults_set" in RB.build_tools_for("stop working on a new branch in D:/code/shop")
    assert "build_defaults_set" in RB.build_tools_for("start new sessions in D:\\code\\shop in the folder, not on a new branch")
    for everyday in ("make a new branch called fix", "create a new branch for the fix and push it",
                     "should new sessions here use a new branch?"):
        assert "build_defaults_set" not in RB.build_tools_for(everyday), everyday
    app = _app(tmp_path / "home", [("build_defaults_set", {"folder": str(repo), "view": "build_code",
                                                           "new_branch": True})])
    p = app.state.platform
    FD.save(p.engine, str(repo), "tool", {"harness": "codex", "mode": "plan"})
    seen: list[str] = []
    frames = await _drive_stream(app, _body(said), await _approve_all(app, seen))
    assert seen == ["build_defaults_set"]
    assert FD.read(p.engine, str(repo)) == {"tool": {"harness": "codex", "mode": "plan"},
                                             "branch": {"work": "branch"}}
    card = _card(frames, "Folder defaults set")
    assert card["new"] == "on a new branch, in their own copy of the folder" and card["old"] == "none"
    await _undo(app, card)
    # Undo puts back exactly what the folder had: no answer (asked again), its tool kept.
    assert FD.read(p.engine, str(repo)) == {"tool": {"harness": "codex", "mode": "plan"}}


async def test_a_branch_sessions_check_is_asked_and_kept_for_the_picked_folder(app, repo):
    from iron_jarvis.hosted import proof as PROOF

    engine = app.state.platform.engine
    (repo / "package.json").write_text(json.dumps({"scripts": {"test": "vitest run"}}), encoding="utf-8")
    (repo / "package-lock.json").write_text("{}", encoding="utf-8")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "js", cwd=repo)
    FD.save_checks(engine, str(repo), {"command": "npm test", "confirmed_at": "2026-11-22T00:00:00Z",
                                       "source": "chat"})
    async with client(app) as c:
        made = (await c.post("/build/app/sessions",
                             json={"harness": "codex", "folder": str(repo), "branch": True})).json()
    wt = made["folder"]
    assert PROOF.decide(engine, wt) == {"state": "ask", "command": "npm test"}, "its own copy alone knows no answer"
    assert PROOF.decide(engine, wt, key=SG.checkout_of(store.get(engine, made["id"]))) == {
        "state": "run", "command": "npm test", "timeout_s": 300}
    # The card's answer under a branch session's reply is the PICKED folder's.
    async with client(app) as c:
        r = await c.post(f"/build/app/sessions/{made['id']}/checks", json={"command": "", "run": False, "turn": ""})
        assert r.status_code == 200, r.text
    assert FD.read_checks(engine, str(repo))["off"] is True
    assert FD.read_checks(engine, wt) is None
    await stop_all(app)


# --------------------------------------------------------------------------- #
# The review's guards: each one pinned through the layer that would refuse
# --------------------------------------------------------------------------- #
async def test_a_press_is_refused_while_iron_jarvis_checks_the_replys_tests(app, repo, monkeypatch):
    from iron_jarvis.hosted import proof as PROOF

    svc = S.service_for(app.state.platform)
    fake_tool(svc, monkeypatch)
    (repo / "app.py").write_bytes(b"x = 7\n")
    async with client(app) as c:
        sid = (await c.post("/build/app/sessions", json={"harness": "codex", "folder": str(repo)})).json()["id"]
        head = git("rev-parse", "HEAD", cwd=repo)
        PROOF.note_pending(svc, sid)  # the reply's end is being judged / its tests run
        try:
            assert PROOF.checking(svc, sid)
            for path, body in ((f"/build/app/sessions/{sid}/git/commit", {"message": "m", "paths": ["app.py"]}),
                               (f"/build/app/sessions/{sid}/git/pr", {"title": "t", "body": ""})):
                r = await c.post(path, json=body)
                assert r.status_code == 409 and r.json()["detail"] == SG.CHECKING_NOW, (path, r.text)
            assert git("rev-parse", "HEAD", cwd=repo) == head, "nothing was committed while it checked"
            assert svc.live[sid].claim == "", "a refused press holds nothing"
        finally:
            PROOF.clear_pending(svc, sid)
        # The control: the same press once the check is over.
        r = await c.post(f"/build/app/sessions/{sid}/git/commit", json={"message": "m", "paths": ["app.py"]})
        assert r.status_code == 200, r.text
        assert git("rev-parse", "HEAD", cwd=repo) != head
    await stop_all(app)


async def test_a_reply_that_starts_while_the_commit_waits_for_the_locks_stops_the_commit(app, repo, monkeypatch):
    """The busy check ran before the wait for the locks; ``_still_free`` asks
    again inside them, right before git writes."""
    from iron_jarvis.hosted import branching as BR

    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    (repo / "app.py").write_bytes(b"x = 8\n")
    async with client(app) as c:
        sid = (await c.post("/build/app/sessions", json={"harness": "codex", "folder": str(repo)})).json()["id"]
        other = (await c.post("/build/app/sessions", json={"harness": "claude-code", "folder": str(repo)})).json()["id"]
        real_free = BR._files_free

        async def free_then_a_reply_starts(service, row, live):
            await real_free(service, row, live)  # free when the press asked...
            await svc.start_turn(other, "tidy the readme", "o1")  # ...then a reply began here
            await until(lambda: other in drivers and drivers[other].sent, timeout=WAIT_S)

        monkeypatch.setattr(BR, "_files_free", free_then_a_reply_starts)
        head = git("rev-parse", "HEAD", cwd=repo)
        r = await c.post(f"/build/app/sessions/{sid}/git/commit", json={"message": "m", "paths": ["app.py"]})
        assert r.status_code == 409 and r.json()["detail"] == BR.OTHER_SESSION, r.text
        assert git("rev-parse", "HEAD", cwd=repo) == head, "nothing was committed"
        # Changed and still not staged: the refused commit touched nothing.
        assert git("status", "--porcelain", "--", "app.py", cwd=repo) == " M app.py"
        monkeypatch.setattr(BR, "_files_free", real_free)
        await reply(svc, other)
    await stop_all(app)


async def test_a_branch_session_whose_row_cannot_be_written_leaves_no_branch_and_no_copy(app, repo, monkeypatch):
    real_create = store.create

    def broken(*_a, **_k):
        raise RuntimeError("the database could not be written")

    monkeypatch.setattr(store, "create", broken)
    async with client(app) as c:
        try:
            r = await c.post("/build/app/sessions", json={"harness": "codex", "folder": str(repo), "branch": True})
            assert r.status_code >= 500, r.text
        except RuntimeError:
            pass  # the app's error came through to the test client
    assert git("branch", "--list", "ij/*", cwd=repo) == "", "the new branch was taken back"
    copies = repo.parent / (repo.name + B.WORKTREES_SUFFIX)
    assert not copies.exists() or not any(copies.iterdir()), "the new copy of the folder was taken back"
    trees = [ln for ln in git("worktree", "list", "--porcelain", cwd=repo).splitlines() if ln.startswith("worktree ")]
    assert len(trees) == 1, trees
    # The control: with the row written, the same start makes both.
    monkeypatch.setattr(store, "create", real_create)
    async with client(app) as c:
        r = await c.post("/build/app/sessions", json={"harness": "codex", "folder": str(repo), "branch": True})
        assert r.status_code == 201, r.text
    assert git("branch", "--list", "ij/*", cwd=repo) != "" and any(copies.iterdir())
    await stop_all(app)


async def test_a_branch_sessions_work_record_and_project_row_name_the_picked_folder(app, repo):
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.workmap import collect

    engine = app.state.platform.engine
    async with client(app) as c:
        made = (await c.post("/build/app/sessions",
                             json={"harness": "codex", "folder": str(repo), "branch": True})).json()
        sid = made["id"]
        assert Path(made["folder"]).parent.name == repo.name + B.WORKTREES_SUFFIX
        store.update(engine, sid, harness_session_id="hs-1")  # the tool answered: earlier work
        with session_scope(engine) as db:
            found, _whole = collect.hosted_candidates(db)
            built = {cand.ref: cand.build(db) for cand in found}
        assert built[sid]["folder"] == str(repo), "the work record names the folder the user picked"
        r = await c.post("/projects", json={"name": "Proj", "root": str(repo)})
        assert r.status_code == 200, r.text
        rows = (await c.get(f"/projects/{r.json()['id']}")).json()["coding_sessions"]
        assert [row["id"] for row in rows] == [sid], "listed on the picked folder's project"
    await stop_all(app)


def test_chat_finds_where_commit_and_the_pull_request_are(tmp_path):
    """Chat does not commit a Build › Code session's work: the feature lookup
    finds the page that does, and its line names both presses."""
    from iron_jarvis.capability_search import index as CI
    from iron_jarvis.capability_search import tool as CT_TOOL

    platform = create_app(str(tmp_path / "home")).state.platform
    idx = CI.build(platform)
    for asked in ("commit what Codex did in my folder", "commit the changes Claude Code made",
                  "open a pull request for my coding session"):
        top = [e.id for _s, e in idx.search(asked, 5)]
        assert "page_build" in top, (asked, top)
    text, data = CT_TOOL.answer(platform, "commit what Codex did / open a pull request for its changes", 8)
    row = next(r for r in data["matches"] if r.get("id") == "page_build")
    assert row["href"] == "/terminals"
    assert "Commit" in text and "Open a pull request" in text
