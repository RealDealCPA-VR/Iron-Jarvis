"""25-a (R3.5): MERGE ALL THAT PASSED — real git, a scratch repository and
night branches made by the real coding-session route, the real app's routes.

* the preview lists every passing task of the night in the order it was
  QUEUED (not the order it started), each with its commits and its files with
  lines added and removed, the branch it goes into and the command run after
  each merge; a branch that would clash says so BEFORE any press and is left
  out of "Merge all";
* the press merges in that order in a copy of the folder, runs the tests
  after each, STOPS at the first branch whose tests fail after merging it,
  and moves the base ONCE to the last good commit: the base holds only the
  branches before it, the user's checkout changes only at the end, and every
  merged session records ``merged_at`` / ``merged_into``;
* a head that moved after the preview is refused ("changed since you
  looked") with nothing changed, and so is a merge while Emergency stop is
  on; the base checked out nowhere moves by ``update-ref`` with the checkout
  untouched.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from iron_jarvis.core.db import session_scope
from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import folder_defaults as FD
from iron_jarvis.harness import landing as L
from iron_jarvis.harness import night as N
from iron_jarvis.harness import night_morning as M
from iron_jarvis.hosted import proof as P
from tests.test_build_git_v1375 import git, git_ok, make_repo, new_branch_session, no_real_gh, snapshot
from tests.test_build_sessions_v1367 import asgi, setup_of

pytestmark = pytest.mark.usefixtures("no_network")

NIGHT = "2026-11-21"
PY = sys.executable

PASSING = "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_{n}(self):\n        self.assertTrue(True)\n"
FAILING = "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_{n}(self):\n        self.assertEqual(1, 2)\n"


@pytest.fixture(autouse=True)
def _no_gh(monkeypatch):
    no_real_gh(monkeypatch)


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def repo(tmp_path) -> Path:
    r = make_repo(tmp_path / "shop")
    (r / ".gitignore").write_bytes(b"__pycache__/\n")
    git("add", "-A", cwd=r)
    git("commit", "-q", "-m", "ignore caches", cwd=r)
    return r


def command() -> str:
    return f'"{PY}" -m unittest discover -q'


def keep_command(app, repo: Path) -> None:
    FD.save_checks(app.state.platform.engine, str(repo), {
        "command": command(), "confirmed_at": "2026-11-21T07:00:00Z", "source": "chat"})


async def night_branch(app, repo: Path, files: dict[str, str], message: str) -> dict:
    """A coding session on its own branch (the real route) with ``files``
    committed on it, as a night task leaves it."""
    view = await new_branch_session(app, repo)
    wt = Path(view["git"]["worktree"])
    for rel, text in files.items():
        (wt / rel).write_bytes(text.encode("utf-8"))
    git("add", "-A", cwd=wt)
    git("commit", "-q", "-m", message, cwd=wt)
    return {"sid": view["id"], "wt": wt, "branch": view["git"]["branch"]}


def night_task(app, repo: Path, s: dict, title: str, *, position: int, started_min: int,
               state: str = "done", tests: str = "passed") -> str:
    """The night task record of a session, and its morning facts."""
    engine = app.state.platform.engine
    begun = datetime(2026, 11, 21, 23, 0) + timedelta(minutes=started_min)
    with session_scope(engine) as db:
        rec = N.NightTaskRecord(
            folder=str(repo), words=title, position=position, state=state, thread_id=s["sid"],
            branch=s["branch"], night=NIGHT,
            morning_json=json.dumps({"tests": tests, "tests_trusted": True, "files": 1,
                                     "commit": {"sha": "abc1234", "subject": title}}),
            started_at=begun, ended_at=begun + timedelta(minutes=5),
        )
        db.add(rec)
        db.commit()
        return rec.id


def summary(app, task_ids: list[str]) -> None:
    engine = app.state.platform.engine
    rows = [M.summary_row(N.get_task(engine, t)) for t in task_ids]
    assert M.make_summary(engine, NIGHT, rows)


async def preview(app) -> dict:
    status, out = await asgi(app, "GET", f"/build/night/summaries/{NIGHT}/land")
    assert status == 200, out
    return out


async def finished(app, rid: str) -> dict:
    async def read():
        status, rec = await asgi(app, "GET", f"/build/night/land/{rid}")
        assert status == 200, rec
        return rec

    box: dict = {}

    def ended() -> bool:
        return bool(box.get("rec")) and box["rec"]["state"] not in (L.RUNNING, L.MOVING)

    end = time.monotonic() + 240
    while time.monotonic() < end:
        box["rec"] = await read()
        if ended():
            return box["rec"]
        await asyncio.sleep(0.2)
    raise AssertionError(f"the merge did not end: {box.get('rec')}")


def reflog_count(repo: Path, ref: str = "refs/heads/main") -> int:
    return len([ln for ln in git("reflog", "show", "--format=%H", ref, cwd=repo).splitlines() if ln.strip()])


def first_parents(repo: Path, since: str) -> list[str]:
    return git("rev-list", "--first-parent", f"{since}..main", cwd=repo).split()


# --------------------------------------------------------------------------- #
# green, pure
# --------------------------------------------------------------------------- #


def test_green_means_done_tests_passed_and_trusted_nothing_needed_and_its_branch_kept():
    facts = {"branch": "ij/a-1", "base": "main", "repo": "r", "worktree": "w"}
    row = {"state": "done", "tests": "passed", "tests_trusted": True, "needs": [], "main_changed": False}
    assert L.why_not(row, facts) == ""
    assert L.why_not({**row, "state": "failed"}, facts) == "It failed."
    assert L.why_not({**row, "state": "needs_you"}, facts) == "It needs you."
    assert L.why_not({**row, "tests": "failed"}, facts) == "Its tests failed."
    assert L.why_not({**row, "tests": ""}, facts) == "No tests ran for it."
    assert "not trusted" in L.why_not({**row, "tests_trusted": False}, facts)
    assert L.why_not({**row, "needs": ["Needs your OK: git push"]}, facts) == "Needs your OK: git push"
    assert "main folder changed" in L.why_not({**row, "main_changed": True}, facts)
    assert L.why_not(row, None) == "Its session or its branch is gone."
    assert L.why_not(row, {**facts, "removed_at": "x"}) == "Its folder was thrown away."
    assert L.why_not(row, {**facts, "merged_at": "x", "merged_into": "main"}) == "Already merged into main."
    assert L.head_words(5, 6) == "5 of 6 passed their tests."
    assert L.button_words(5) == "Merge all 5"


# --------------------------------------------------------------------------- #
# the run: queue order, the stop, the base moved once
# --------------------------------------------------------------------------- #


async def test_passing_branches_merge_in_queue_order_and_the_first_failing_test_stops_the_run(
        app, repo, monkeypatch):
    keep_command(app, repo)
    a = await night_branch(app, repo, {"a.py": "A = 1\n", "test_a.py": PASSING.format(n="a")}, "Add a")
    b = await night_branch(app, repo, {"b.py": "B = 1\nB2 = 2\n", "test_b.py": PASSING.format(n="b")}, "Add b")
    c = await night_branch(app, repo, {"test_c.py": FAILING.format(n="c")}, "Add c")
    dd = await night_branch(app, repo, {"d.py": "D = 1\n", "test_d.py": PASSING.format(n="d")}, "Add d")
    # Queued A, B, C, D on Tonight; they STARTED in another order.
    ids = {
        "a": night_task(app, repo, a, "Add a", position=1, started_min=30),
        "b": night_task(app, repo, b, "Add b", position=2, started_min=10),
        "c": night_task(app, repo, c, "Add c", position=3, started_min=0),
        "d": night_task(app, repo, dd, "Add d", position=4, started_min=20),
    }
    summary(app, list(ids.values()))

    # The summary's head line, read without git.
    status, nights = await asgi(app, "GET", "/build/night/summaries")
    assert status == 200, nights
    head = nights["nights"][0]["land"]["folders"][0]
    assert (head["green"], head["total"], head["words"], head["button"]) == (
        4, 4, "All 4 tasks passed their tests.", "Merge all 4")

    out = await preview(app)
    [land] = out["landings"]
    assert land["base"] == "main" and land["into"] == f"main in {repo}"
    assert land["command"] == command()
    assert land["command_words"] == f"Tests run after each: `{P.shown_command(command())}`"
    assert [s["task_id"] for s in land["steps"]] == [ids["a"], ids["b"], ids["c"], ids["d"]]
    assert all(s["ready"] for s in land["steps"]) and land["ready"] == 4 and land["button"] == "Merge all 4"
    b_step = land["steps"][1]
    assert b_step["commits"][0]["subject"] == "Add b" and b_step["commit_count"] == 1
    assert {f["path"]: (f["status"], f["added"], f["removed"]) for f in b_step["files"]} == {
        "b.py": ("created", 2, 0), "test_b.py": ("created", 6, 0)}
    assert (b_step["added"], b_step["removed"]) == (8, 0)
    assert land["blocked"] == "" and land["id"].startswith("lp_")

    before_main = git("rev-parse", "main", cwd=repo)
    before = snapshot(repo)
    reflog = reflog_count(repo)
    seen: list[dict] = []
    real = P.run_checks

    def watched(folder, cmd, sid, timeout_s=P.TIMEOUT_S, **kw):
        # The user's checkout is untouched while the steps run.
        seen.append({"folder": folder, "same": snapshot(repo) == before})
        return real(folder, cmd, sid, timeout_s, **kw)

    monkeypatch.setattr(P, "run_checks", watched)
    status, started = await asgi(app, "POST", f"/build/night/summaries/{NIGHT}/land", {"preview_id": land["id"]})
    assert status == 202, started
    rec = await finished(app, started["merge"]["id"])

    assert [s["state"] for s in rec["steps"]] == ["merged", "merged", "tests_failed", "skipped"]
    assert rec["state"] == L.MERGED and rec["merged"] == 2
    assert len(seen) == 3 and all(x["same"] for x in seen), seen
    assert all(Path(x["folder"]) != repo for x in seen), "the tests run in the copy, never the checkout"
    # The base moved ONCE, to a commit holding A and B only.
    assert reflog_count(repo) == reflog + 1
    assert len(first_parents(repo, before_main)) == 2
    assert (repo / "a.py").read_bytes() == b"A = 1\n" and (repo / "b.py").exists()
    assert not (repo / "test_c.py").exists() and not (repo / "d.py").exists()
    assert git("status", "--porcelain", cwd=repo) == ""
    assert git("branch", "--show-current", cwd=repo) == "main"
    after = git("rev-parse", "main", cwd=repo)
    assert rec["words"] == (
        f"Merged 2. Stopped at 'Add c': 1 test failed after merging it. Nothing after it was merged. "
        f"main moved from {before_main[:7]} to {after[:7]}. Nothing was pushed: main changed on this PC only."
    )
    # Each merged session says so; the others do not.
    for s in (a, b):
        facts = setup_of(app, s["sid"])["git"]
        assert facts["merged_into"] == "main" and facts["merged_at"]
    for s in (c, dd):
        assert "merged_at" not in setup_of(app, s["sid"])["git"]
    # Before is kept by a hidden ref; the copy's branch stays, its folder is in the trash.
    assert git("rev-parse", f"{L.REF_ROOT}/{rec['id']}/before", cwd=repo) == before_main
    assert rec["landing_branch"].startswith("ij/merge-")
    assert git_ok("rev-parse", "--verify", f"refs/heads/{rec['landing_branch']}", cwd=repo)
    assert Path(L.get_record(app.state.platform.engine, rec["id"]).landing_trash).is_dir()
    # Every branch of the night stays.
    for s in (a, b, c, dd):
        assert git_ok("rev-parse", "--verify", f"refs/heads/{s['branch']}", cwd=repo)

    # The next look: A and B merged, C stopped the merge (same head), D waits.
    out = await preview(app)
    [land] = out["landings"]
    assert [x["task_id"] for x in land["merged"]] == [ids["a"], ids["b"]]
    assert [s["task_id"] for s in land["steps"]] == [ids["d"]]
    assert any(x["task_id"] == ids["c"] and "1 test failed after merging it" in x["why"] for x in land["left"])
    status, nights = await asgi(app, "GET", "/build/night/summaries")
    assert nights["nights"][0]["land"]["folders"][0]["words"] == "1 of 2 passed their tests."


async def test_a_head_moved_after_the_preview_is_refused_with_nothing_changed(app, repo):
    keep_command(app, repo)
    a = await night_branch(app, repo, {"a.py": "A = 1\n"}, "Add a")
    b = await night_branch(app, repo, {"b.py": "B = 1\n"}, "Add b")
    summary(app, [night_task(app, repo, a, "Add a", position=1, started_min=0),
                  night_task(app, repo, b, "Add b", position=2, started_min=5)])
    [land] = (await preview(app))["landings"]
    (a["wt"] / "a.py").write_bytes(b"A = 2\n")
    git("commit", "-q", "-am", "later", cwd=a["wt"])
    before = snapshot(repo)
    status, out = await asgi(app, "POST", f"/build/night/summaries/{NIGHT}/land", {"preview_id": land["id"]})
    assert status == 409 and out["detail"] == L.CHANGED_SINCE.format(base="main"), out
    assert snapshot(repo) == before
    assert L.records_for_night(app.state.platform.engine, NIGHT) == []
    # A made-up id is the same refusal.
    status, out = await asgi(app, "POST", f"/build/night/summaries/{NIGHT}/land", {"preview_id": "lp_nothing"})
    assert status == 409 and out["detail"] == L.CHANGED_SINCE.format(base="main")


async def test_a_clash_is_said_before_any_press_and_left_out_of_merge_all(app, repo):
    keep_command(app, repo)
    a = await night_branch(app, repo, {"app.py": "x = 'night'\n"}, "Change app")
    b = await night_branch(app, repo, {"b.py": "B = 1\n"}, "Add b")
    summary(app, [night_task(app, repo, a, "Change app", position=1, started_min=0),
                  night_task(app, repo, b, "Add b", position=2, started_min=5)])
    # The user changed the same line on main in the morning.
    (repo / "app.py").write_bytes(b"x = 'morning'\n")
    git("commit", "-q", "-am", "morning", cwd=repo)
    [land] = (await preview(app))["landings"]
    clash, ok = land["steps"]
    assert clash["ready"] is False and clash["clash"] == ["app.py"]
    assert clash["clash_words"] == "Merging would clash in app.py."
    assert ok["ready"] is True and land["ready"] == 1 and land["green"] == 2
    assert land["words"] == "All 2 tasks passed their tests." and land["button"] == "Merge it"
    status, started = await asgi(app, "POST", f"/build/night/summaries/{NIGHT}/land", {"preview_id": land["id"]})
    assert status == 202, started
    rec = await finished(app, started["merge"]["id"])
    assert [s["task_id"] for s in rec["steps"]] == [ok["task_id"]] and rec["merged"] == 1
    assert (repo / "b.py").exists() and (repo / "app.py").read_bytes() == b"x = 'morning'\n"


async def test_the_base_checked_out_nowhere_moves_by_update_ref_and_the_checkout_is_untouched(app, repo):
    a = await night_branch(app, repo, {"a.py": "A = 1\n"}, "Add a")
    summary(app, [night_task(app, repo, a, "Add a", position=1, started_min=0)])
    git("switch", "-q", "-c", "elsewhere", cwd=repo)
    (repo / "mine.txt").write_bytes(b"my own work\n")  # untracked, never touched
    [land] = (await preview(app))["landings"]
    # No command for this folder: said plainly, and the merge still runs.
    assert land["command"] == "" and land["command_words"].startswith("No tests run after each merge")
    before_main = git("rev-parse", "main", cwd=repo)
    before = snapshot(repo)
    status, started = await asgi(app, "POST", f"/build/night/summaries/{NIGHT}/land", {"preview_id": land["id"]})
    assert status == 202, started
    rec = await finished(app, started["merge"]["id"])
    assert rec["state"] == L.MERGED and rec["where"] == ""
    assert git("rev-parse", "main^1", cwd=repo) == before_main
    after = snapshot(repo)
    assert {k: v for k, v in after.items() if k != "main"} == {k: v for k, v in before.items() if k != "main"}
    assert git("branch", "--show-current", cwd=repo) == "elsewhere"


async def test_emergency_stop_refuses_with_nothing_changed(app, repo, monkeypatch):
    a = await night_branch(app, repo, {"a.py": "A = 1\n"}, "Add a")
    summary(app, [night_task(app, repo, a, "Add a", position=1, started_min=0)])
    [land] = (await preview(app))["landings"]
    from iron_jarvis.daemon.routes import night_land as NL

    monkeypatch.setattr(NL, "_emergency", lambda d: True)
    before = snapshot(repo)
    status, out = await asgi(app, "POST", f"/build/night/summaries/{NIGHT}/land", {"preview_id": land["id"]})
    assert status == 409 and out["detail"] == L.EMERGENCY_ON
    assert snapshot(repo) == before and L.records_for_night(app.state.platform.engine, NIGHT) == []


async def test_a_summary_read_while_a_merge_starts_never_takes_it_for_one_left_running(app, repo, monkeypatch):
    """The summary is read every few seconds; a read that lands right after
    the merge's record is written must not settle it as one a closed Iron
    Jarvis left behind ("stopped")."""
    from iron_jarvis.daemon.routes import night_land as NL

    a = await night_branch(app, repo, {"a.py": "A = 1\n"}, "Add a")
    summary(app, [night_task(app, repo, a, "Add a", position=1, started_min=0)])
    [land] = (await preview(app))["landings"]
    real = L.create_record
    seen: dict = {}

    def create_then_read(engine, **fields):
        out = real(engine, **fields)
        NL.night_land_view(engine, NIGHT, M.get_summary(engine, NIGHT)["rows"])
        seen["state"] = L.get_record(engine, out["id"]).state
        return out

    monkeypatch.setattr(L, "create_record", create_then_read)
    status, started = await asgi(app, "POST", f"/build/night/summaries/{NIGHT}/land", {"preview_id": land["id"]})
    assert status == 202, started
    assert seen["state"] == L.RUNNING
    rec = await finished(app, started["merge"]["id"])
    assert rec["state"] == L.MERGED and rec["merged"] == 1


# --------------------------------------------------------------------------- #
# review 1 of 25-a: a running test is KILLED by Emergency stop and by closing;
# the sessions are held while it runs; a busy session or repository refuses
# --------------------------------------------------------------------------- #

SLOW = "import os, sys, time\nopen(sys.argv[1], 'w').write(str(os.getpid()))\ntime.sleep(120)\n"


def slow_command(tmp_path: Path) -> tuple[str, Path]:
    """A folder test command that writes its pid, then sleeps two minutes."""
    script = tmp_path / "slow_check.py"
    script.write_bytes(SLOW.encode("utf-8"))
    pid = tmp_path / "slow_check.pid"
    return f'"{PY}" "{script}" "{pid}"', pid


async def until_file(path: Path, limit: float = 120.0) -> int:
    end = time.monotonic() + limit
    while time.monotonic() < end:
        if path.is_file() and path.read_text(encoding="utf-8").strip().isdigit():
            return int(path.read_text(encoding="utf-8").strip())
        await asyncio.sleep(0.2)
    raise AssertionError("the folder's test never started")


async def gone(pid: int, limit: float = 15.0) -> bool:
    import psutil

    end = time.monotonic() + limit
    while time.monotonic() < end:
        try:
            if not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        await asyncio.sleep(0.2)
    return False


async def slow_merge(app, repo: Path, tmp_path: Path) -> tuple[dict, dict, Path, str]:
    cmd, pid_file = slow_command(tmp_path)
    FD.save_checks(app.state.platform.engine, str(repo), {
        "command": cmd, "confirmed_at": "2026-11-21T07:00:00Z", "source": "chat"})
    a = await night_branch(app, repo, {"a.py": "A = 1\n"}, "Add a")
    summary(app, [night_task(app, repo, a, "Add a", position=1, started_min=0)])
    [land] = (await preview(app))["landings"]
    assert land["command"] == cmd and land["ready"] == 1, land
    status, started = await asgi(app, "POST", f"/build/night/summaries/{NIGHT}/land", {"preview_id": land["id"]})
    assert status == 202, started
    return a, started["merge"], pid_file, land["id"]


async def test_emergency_stop_during_a_test_kills_it_at_once_and_moves_nothing(app, repo, tmp_path, monkeypatch):
    from iron_jarvis.daemon.routes import night_land as NL

    switch = {"on": False}
    monkeypatch.setattr(NL, "_emergency", lambda d: switch["on"])
    before_main = git("rev-parse", "main", cwd=repo)
    before = snapshot(repo)
    a, merge, pid_file, pid_of_preview = await slow_merge(app, repo, tmp_path)
    child = await until_file(pid_file)

    # While its test runs, the sessions are HELD: a second press and a git
    # action of the session are refused in words, and nothing new is written.
    status, again = await asgi(app, "POST", f"/build/night/summaries/{NIGHT}/land", {"preview_id": pid_of_preview})
    assert status == 409 and again["detail"] == NL.SESSION_BUSY.format(title="'Add a'"), again
    status, act = await asgi(app, "POST", f"/build/sessions/{a['sid']}/git/merge", {})
    assert status == 409, act
    assert len(L.records_for_night(app.state.platform.engine, NIGHT)) == 1

    switch["on"] = True
    flipped = time.monotonic()
    rec = await finished(app, merge["id"])
    took = time.monotonic() - flipped
    assert took < 20, f"the record ended {took:.1f} s after Emergency stop (the test was not killed)"
    assert await gone(child), "the folder's test outlived Emergency stop"
    assert rec["state"] == L.STOPPED and rec["merged"] == 0
    assert rec["words"] == "Emergency stop was turned on while merging, so main was not changed."
    assert [s["state"] for s in rec["steps"]] == ["skipped"]
    assert git("rev-parse", "main", cwd=repo) == before_main and snapshot(repo) == before
    assert "merged_at" not in setup_of(app, a["sid"])["git"]


async def test_closing_during_a_test_kills_it_and_moves_nothing(app, repo, tmp_path):
    from iron_jarvis.daemon.routes import night_land as NL

    before_main = git("rev-parse", "main", cwd=repo)
    a, merge, pid_file, _pid = await slow_merge(app, repo, tmp_path)
    child = await until_file(pid_file)
    closed = time.monotonic()
    assert NL.stop_all() == [merge["id"]]
    rec = await finished(app, merge["id"])
    took = time.monotonic() - closed
    assert took < 20, f"the record ended {took:.1f} s after closing (the test was not killed)"
    assert await gone(child), "the folder's test outlived Iron Jarvis closing"
    assert rec["state"] == L.STOPPED
    assert rec["words"] == "Iron Jarvis closed while merging, so main was not changed."
    assert git("rev-parse", "main", cwd=repo) == before_main
    assert "merged_at" not in setup_of(app, a["sid"])["git"]


async def test_a_session_still_answering_refuses_the_merge_with_nothing_written(app, repo):
    from iron_jarvis.daemon.routes import build as BR
    from iron_jarvis.daemon.routes import night_land as NL

    a = await night_branch(app, repo, {"a.py": "A = 1\n"}, "Add a")
    summary(app, [night_task(app, repo, a, "Add a", position=1, started_min=0)])
    [land] = (await preview(app))["landings"]
    before = snapshot(repo)
    BR._STARTING.add(a["sid"])  # a reply is starting in it
    try:
        status, out = await asgi(app, "POST", f"/build/night/summaries/{NIGHT}/land", {"preview_id": land["id"]})
    finally:
        BR._STARTING.discard(a["sid"])
    assert status == 409 and out["detail"] == NL.SESSION_BUSY.format(title="'Add a'"), out
    assert L.records_for_night(app.state.platform.engine, NIGHT) == [] and snapshot(repo) == before


async def test_another_merge_or_undo_in_the_repository_refuses_with_nothing_written(app, repo):
    from iron_jarvis.daemon.routes import night_land as NL

    a = await night_branch(app, repo, {"a.py": "A = 1\n"}, "Add a")
    summary(app, [night_task(app, repo, a, "Add a", position=1, started_min=0)])
    [land] = (await preview(app))["landings"]
    before = snapshot(repo)
    assert NL._claim_repo(land["repo"])  # an Undo (or another merge) runs in this repository
    try:
        status, out = await asgi(app, "POST", f"/build/night/summaries/{NIGHT}/land", {"preview_id": land["id"]})
    finally:
        NL._release_repo(land["repo"])
    assert status == 409 and out["detail"] == L.ALREADY_RUNNING, out
    assert L.records_for_night(app.state.platform.engine, NIGHT) == [] and snapshot(repo) == before


async def test_files_not_in_git_that_the_move_would_overwrite_are_named_in_the_words(app, repo):
    """Review 5 of 25-a: the words name the files (no "Details" that is not on
    screen), and the checkout and its own file are as they were."""
    a = await night_branch(app, repo, {"a.py": "A = 1\n"}, "Add a")
    summary(app, [night_task(app, repo, a, "Add a", position=1, started_min=0)])
    (repo / "a.py").write_bytes(b"my own a.py, not in git\n")
    [land] = (await preview(app))["landings"]
    assert land["blocked"] == "" and land["ready"] == 1, land
    before_main = git("rev-parse", "main", cwd=repo)
    status, started = await asgi(app, "POST", f"/build/night/summaries/{NIGHT}/land", {"preview_id": land["id"]})
    assert status == 202, started
    rec = await finished(app, started["merge"]["id"])
    assert rec["state"] == L.STOPPED, rec
    assert rec["words"].startswith(
        f"Your checkout of main at {repo} has files that are not in git which this would overwrite (a.py), so it "
        "is as it was. Move them away first. The merges that passed are kept on ij/merge-"), rec["words"]
    assert "Details" not in rec["words"]
    assert git("rev-parse", "main", cwd=repo) == before_main
    assert (repo / "a.py").read_bytes() == b"my own a.py, not in git\n"
    assert L.overwritten_files(
        "error: The following untracked working tree files would be overwritten by merge:\n\ta.py\n\tsrc/b.py\n"
        "Please move or remove them before you merge.\nAborting\n") == ["a.py", "src/b.py"]


async def test_six_tasks_five_passed_one_press_merges_five_and_the_sixth_waits_with_its_failing_test(app, repo):
    """The phase's "Done when": six tasks, five passed, one press after the
    preview merges five, the sixth is left out with its failing test said."""
    keep_command(app, repo)
    made = []
    for i, name in enumerate("abcdef", start=1):
        s = await night_branch(app, repo, {f"{name}.py": f"{name.upper()} = 1\n",
                                           f"test_{name}.py": PASSING.format(n=name)}, f"Add {name}")
        made.append((name, s, night_task(app, repo, s, f"Add {name}", position=i, started_min=i,
                                         tests="failed" if name == "c" else "passed")))
    summary(app, [t for _n, _s, t in made])
    status, nights = await asgi(app, "GET", "/build/night/summaries")
    head = nights["nights"][0]["land"]["folders"][0]
    assert (head["words"], head["button"]) == ("5 of 6 passed their tests.", "Merge all 5")
    [land] = (await preview(app))["landings"]
    assert land["ready"] == 5 and land["button"] == "Merge all 5"
    by_id = {t: n for n, _s, t in made}
    assert [by_id[s["task_id"]] for s in land["steps"]] == list("abdef")
    assert [(by_id[x["task_id"]], x["why"]) for x in land["left"]] == [("c", "Its tests failed.")]
    before_main = git("rev-parse", "main", cwd=repo)
    status, started = await asgi(app, "POST", f"/build/night/summaries/{NIGHT}/land", {"preview_id": land["id"]})
    assert status == 202, started
    rec = await finished(app, started["merge"]["id"])
    assert rec["state"] == L.MERGED and rec["merged"] == 5, rec
    assert len(first_parents(repo, before_main)) == 5
    assert all((repo / f"{n}.py").exists() for n in "abdef") and not (repo / "c.py").exists()
    [land] = (await preview(app))["landings"]
    assert [by_id[x["task_id"]] for x in land["merged"]] == list("abdef")
    assert [(by_id[x["task_id"]], x["why"]) for x in land["left"]] == [("c", "Its tests failed.")]
    status, nights = await asgi(app, "GET", "/build/night/summaries")
    assert nights["nights"][0]["land"]["folders"][0]["words"] == "0 of 1 passed their tests."


async def test_a_checkout_with_changes_blocks_the_merge_in_words(app, repo):
    a = await night_branch(app, repo, {"a.py": "A = 1\n"}, "Add a")
    summary(app, [night_task(app, repo, a, "Add a", position=1, started_min=0)])
    (repo / "app.py").write_bytes(b"x = 'not committed'\n")
    [land] = (await preview(app))["landings"]
    assert land["blocked"] == L.CHECKOUT_DIRTY.format(base="main", where=str(repo)) and land["button"] == ""
    status, out = await asgi(app, "POST", f"/build/night/summaries/{NIGHT}/land", {"preview_id": land["id"]})
    assert status == 409 and out["detail"] == land["blocked"]
    assert (repo / "app.py").read_bytes() == b"x = 'not committed'\n"


# --------------------------------------------------------------------------- #
# review 2 of 25-a
# --------------------------------------------------------------------------- #


def test_a_task_that_started_elsewhere_is_left_out_in_words_that_say_what_the_user_does():
    """Nothing merges such a task by itself: the words never say it is."""
    assert L.other_start_words("dev", "main", other_repo=False) == (
        "It started from dev, not main, so Merge all leaves it out: open it to merge it into dev.")
    assert L.other_start_words("main", "main", other_repo=True) == (
        "It is in another repository, so Merge all leaves it out: open it to merge it into main.")


async def test_a_passing_task_from_another_branch_is_left_out_and_never_said_to_merge_on_its_own(app, repo):
    from iron_jarvis.daemon.routes import night_land as NL

    a = await night_branch(app, repo, {"a.py": "A = 1\n"}, "Add a")
    b = await night_branch(app, repo, {"b.py": "B = 1\n"}, "Add b")
    git("branch", "dev", cwd=repo)
    NL.record_session(app.state.platform.engine, b["sid"], {"base": "dev"})  # it started from dev
    ids = [night_task(app, repo, a, "Add a", position=1, started_min=0),
           night_task(app, repo, b, "Add b", position=2, started_min=5)]
    summary(app, ids)
    [land] = (await preview(app))["landings"]
    assert [s["task_id"] for s in land["steps"]] == [ids[0]]
    assert [(x["task_id"], x["why"]) for x in land["left"]] == [
        (ids[1], "It started from dev, not main, so Merge all leaves it out: open it to merge it into dev.")]
    assert all("on its own" not in x["why"] for x in land["left"])


def test_only_a_failing_test_keeps_a_task_out_while_its_head_is_the_same():
    """A stop that says nothing against the branch is offered again; the
    newest record of a task wins."""
    from types import SimpleNamespace

    def rec(tid: str, why: str, *, undone: bool = False) -> SimpleNamespace:
        return SimpleNamespace(undone_at="x" if undone else None,
                               stopped_json=json.dumps({"task_id": tid, "head": "h", "why": why}))

    records = [rec("t1", L.STEP_FAILED), rec("t2", L.STEP_NOT_CHECKED), rec("t3", L.STEP_MERGE_FAILED),
               rec("t4", L.STEP_CLASH), rec("t5", L.STEP_NOT_CHECKED), rec("t5", L.STEP_FAILED),
               rec("t6", L.STEP_FAILED, undone=True)]
    assert sorted(L.stopped_tasks(records)) == ["t1"]


async def test_a_clash_found_while_merging_has_its_time_and_is_offered_again_with_its_clash(app, repo):
    """Two branches each merge cleanly into main but clash with each other:
    the run stops at the second (with its time), and the next look offers it
    again with the clash said on its step, not hidden as 'stopped at it'."""
    a = await night_branch(app, repo, {"app.py": "x = 'a'\n"}, "Change app to a")
    b = await night_branch(app, repo, {"app.py": "x = 'b'\n"}, "Change app to b")
    ids = [night_task(app, repo, a, "Change app to a", position=1, started_min=0),
           night_task(app, repo, b, "Change app to b", position=2, started_min=5)]
    summary(app, ids)
    [land] = (await preview(app))["landings"]
    assert land["ready"] == 2
    status, started = await asgi(app, "POST", f"/build/night/summaries/{NIGHT}/land", {"preview_id": land["id"]})
    assert status == 202, started
    rec = await finished(app, started["merge"]["id"])
    assert [s["state"] for s in rec["steps"]] == ["merged", "clash"], rec
    assert isinstance(rec["steps"][1].get("seconds"), float)
    [land] = (await preview(app))["landings"]
    assert [s["task_id"] for s in land["steps"]] == [ids[1]]
    assert land["steps"][0]["ready"] is False and land["steps"][0]["clash_words"] == "Merging would clash in app.py."


async def test_a_merge_whose_base_never_moved_says_its_steps_are_on_the_copy_only(app, repo, monkeypatch):
    a = await night_branch(app, repo, {"a.py": "A = 1\n"}, "Add a")
    summary(app, [night_task(app, repo, a, "Add a", position=1, started_min=0)])
    [land] = (await preview(app))["landings"]

    def refused(*_a, **_k):
        raise L.B.GitRefused("Git would not move main.")

    monkeypatch.setattr(L, "move_base", refused)
    status, started = await asgi(app, "POST", f"/build/night/summaries/{NIGHT}/land", {"preview_id": land["id"]})
    assert status == 202, started
    rec = await finished(app, started["merge"]["id"])
    assert rec["state"] == L.STOPPED and rec["merged"] == 0, rec
    assert [s["state"] for s in rec["steps"]] == [L.STEP_COPY_ONLY]
    assert "The merges that passed are kept on ij/merge-" in rec["words"]


@pytest.fixture()
def coding_record(app, monkeypatch):
    """28-a's REAL coding record (``harness/scoreboard``): real
    ``CodingOutcome`` rows (a night task's row, unit ``night``, source
    ``work``) and the real ``settle_session``, wrapped only to count its
    calls. Returns ``add`` / ``rows`` (``{row ref: (verdict, how)}``) /
    ``calls``."""
    import types

    from sqlmodel import col, select

    from iron_jarvis.harness import scoreboard as SB

    engine = app.state.platform.engine
    calls: list[tuple[str, str, str]] = []
    real = SB.settle_session

    def counted(engine, session_id, verdict, how):
        calls.append((session_id, verdict, how))
        return real(engine, session_id, verdict, how)

    monkeypatch.setattr(SB, "settle_session", counted)
    refs: list[str] = []

    def add(ref: str, session_id: str, verdict: str = "", how: str = "") -> None:
        with session_scope(engine) as db:
            db.add(SB.CodingOutcome(ref=ref, session_id=session_id, unit=SB.UNIT_NIGHT,
                                    source=SB.SOURCE_WORK, verdict=verdict, how=how))
            db.commit()
        refs.append(ref)

    def rows() -> dict[str, tuple[str, str]]:
        with session_scope(engine) as db:
            return {r.ref: (r.verdict, r.how) for r in db.exec(
                select(SB.CodingOutcome).where(col(SB.CodingOutcome.ref).in_(refs))).all()}

    return types.SimpleNamespace(calls=calls, add=add, rows=rows, engine=engine)


async def _merged_a_with_b_failing(app, repo, coding_record) -> tuple[dict, dict, dict]:
    a = await night_branch(app, repo, {"a.py": "A = 1\n"}, "Add a")
    b = await night_branch(app, repo, {"b.py": "B = 1\n"}, "Add b")
    coding_record.add("a-night", a["sid"])
    coding_record.add("a-committed", a["sid"], "kept", "committed")
    coding_record.add("b-night", b["sid"])
    summary(app, [night_task(app, repo, a, "Add a", position=1, started_min=0),
                  night_task(app, repo, b, "Add b", position=2, started_min=5, tests="failed")])
    [land] = (await preview(app))["landings"]
    status, started = await asgi(app, "POST", f"/build/night/summaries/{NIGHT}/land", {"preview_id": land["id"]})
    assert status == 202, started
    rec = await finished(app, started["merge"]["id"])
    assert rec["state"] == L.MERGED and rec["merged"] == 1, rec
    assert coding_record.calls == [(a["sid"], "kept", "merged")]
    assert coding_record.rows() == {"a-night": ("kept", "merged"), "a-committed": ("kept", "committed"),
                                    "b-night": ("", "")}
    return rec, a, b


async def test_each_task_merge_all_merged_is_kept_and_a_put_back_undo_makes_it_undecided_again(
        app, repo, coding_record):
    """Review 3 of 25-a: Undo took the work back off main, so the coding
    record no longer says it was kept; a Discard after it then counts it
    thrown (never "kept" for work on no branch the user kept)."""
    from iron_jarvis.daemon.routes import night_land as NL

    rec, a, b = await _merged_a_with_b_failing(app, repo, coding_record)
    # Any other fact settles nothing.
    NL.record_session(coding_record.engine, b["sid"], {"base": "main"})
    assert coding_record.rows()["b-night"] == ("", "")
    status, view = await asgi(app, "GET", f"/build/night/land/{rec['id']}")
    assert view["undo"]["how"] == L.UNDO_PUT_BACK, view["undo"]
    status, done = await asgi(app, "POST", f"/build/night/land/{rec['id']}/undo", {"how": L.UNDO_PUT_BACK})
    assert status == 200, done
    assert "merged_at" not in setup_of(app, a["sid"])["git"]
    # The row the merge kept is not decided again; a row kept by the user's own
    # Commit is never touched.
    assert coding_record.rows() == {"a-night": ("", ""), "a-committed": ("kept", "committed"),
                                    "b-night": ("", "")}
    # The user throws it away: the real Discard press decides it
    # (build_git._settle -> scoreboard.settle_session), never "kept".
    status, done = await asgi(app, "POST", f"/build/sessions/{a['sid']}/git/discard", {"confirm": True})
    assert status == 200, done
    assert coding_record.calls[-1] == (a["sid"], "thrown", "discarded")
    assert coding_record.rows() == {"a-night": ("thrown", "discarded"), "a-committed": ("kept", "committed"),
                                    "b-night": ("", "")}


async def test_a_reversed_merge_is_not_counted_kept_either(app, repo, coding_record):
    rec, a, _b = await _merged_a_with_b_failing(app, repo, coding_record)
    (repo / "later.txt").write_bytes(b"later\n")
    git("add", "later.txt", cwd=repo)
    git("commit", "-q", "-m", "later work", cwd=repo)
    status, view = await asgi(app, "GET", f"/build/night/land/{rec['id']}")
    assert view["undo"]["how"] == L.UNDO_REVERSE, view["undo"]
    status, done = await asgi(app, "POST", f"/build/night/land/{rec['id']}/undo", {"how": L.UNDO_REVERSE})
    assert status == 200, done
    assert setup_of(app, a["sid"])["git"]["merge_reversed"]
    assert coding_record.rows()["a-night"] == ("", "")
    assert coding_record.rows()["a-committed"] == ("kept", "committed")
