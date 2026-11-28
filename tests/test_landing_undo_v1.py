"""25-a (R3.5), D-25.1: UNDO a Merge all — real git, the real routes.

* nothing came after it and it was not pushed: the base goes back EXACTLY
  (the same commit, the checkout's files as they were), and the sessions no
  longer say merged;
* a commit came after it on the base (or it was pushed): Undo says so
  BEFORE the press and adds one reverse commit per merge, newest first; the
  later commit is kept and nothing is taken out of the history;
* the checkout has changes: Undo is refused in words, before the press and
  at it, with nothing changed;
* Undo goes the way its plan said: a plan that changed since it was shown is
  refused.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iron_jarvis.harness import landing as L
from tests.test_build_git_v1375 import git, make_remote, no_real_gh, snapshot
from tests.test_build_sessions_v1367 import asgi, setup_of
from tests.test_landing_v1 import NIGHT, finished, night_branch, night_task, preview, summary

pytestmark = pytest.mark.usefixtures("no_network")


@pytest.fixture(autouse=True)
def _no_gh(monkeypatch):
    no_real_gh(monkeypatch)


@pytest.fixture()
def app(tmp_path):
    from iron_jarvis.daemon.app import create_app

    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def repo(tmp_path) -> Path:
    from tests.test_build_git_v1375 import make_repo

    r = make_repo(tmp_path / "shop")
    (r / ".gitignore").write_bytes(b"__pycache__/\n")
    git("add", "-A", cwd=r)
    git("commit", "-q", "-m", "ignore caches", cwd=r)
    return r


async def merged_two(app, repo) -> tuple[dict, list[dict], str, dict]:
    a = await night_branch(app, repo, {"a.py": "A = 1\n"}, "Add a")
    b = await night_branch(app, repo, {"b.py": "B = 1\n"}, "Add b")
    summary(app, [night_task(app, repo, a, "Add a", position=1, started_min=0),
                  night_task(app, repo, b, "Add b", position=2, started_min=5)])
    before_main = git("rev-parse", "main", cwd=repo)
    before = snapshot(repo)
    [land] = (await preview(app))["landings"]
    status, started = await asgi(app, "POST", f"/build/night/summaries/{NIGHT}/land", {"preview_id": land["id"]})
    assert status == 202, started
    rec = await finished(app, started["merge"]["id"])
    assert rec["state"] == L.MERGED and rec["merged"] == 2, rec
    return rec, [a, b], before_main, before


async def test_nothing_came_after_it_so_undo_puts_main_back_exactly(app, repo):
    rec, sessions, before_main, before = await merged_two(app, repo)
    assert (repo / "a.py").exists() and (repo / "b.py").exists()
    status, view = await asgi(app, "GET", f"/build/night/land/{rec['id']}")
    assert status == 200, view
    assert view["undo"]["how"] == L.UNDO_PUT_BACK
    assert view["undo"]["words"] == f"Undo puts main back at {before_main[:7]}, exactly as it was before."
    assert view["undo"]["button"] == f"Undo: put main back at {before_main[:7]}"
    status, done = await asgi(app, "POST", f"/build/night/land/{rec['id']}/undo", {"how": L.UNDO_PUT_BACK})
    assert status == 200, done
    assert git("rev-parse", "main", cwd=repo) == before_main
    assert snapshot(repo) == before, "the checkout is exactly as it was"
    assert done["undone"] is True
    assert done["undo_words"] == f"Undone: main is back at {before_main[:7]}, exactly as it was. Nothing was pushed."
    for s in sessions:
        facts = setup_of(app, s["sid"])["git"]
        assert "merged_at" not in facts and "merged_into" not in facts
    # Twice is refused in words.
    status, again = await asgi(app, "POST", f"/build/night/land/{rec['id']}/undo", {"how": L.UNDO_PUT_BACK})
    assert status == 409 and again["detail"] == L.NO_UNDO
    # And the night's branches are offered again.
    [land] = (await preview(app))["landings"]
    assert land["ready"] == 2


async def test_a_commit_after_it_makes_undo_add_reverse_commits_and_keeps_that_commit(app, repo):
    rec, sessions, before_main, _ = await merged_two(app, repo)
    (repo / "later.txt").write_bytes(b"the user's own later work\n")
    git("add", "later.txt", cwd=repo)
    git("commit", "-q", "-m", "later work", cwd=repo)
    later = git("rev-parse", "main", cwd=repo)
    status, view = await asgi(app, "GET", f"/build/night/land/{rec['id']}")
    assert view["undo"]["how"] == L.UNDO_REVERSE
    assert view["undo"]["words"] == (
        "Since something came after it on main, Undo adds 2 commits to main that reverse these merges; nothing is "
        "taken out of its history.")
    assert view["undo"]["button"] == "Undo: add 2 reverse commits"
    # The plan that was shown is the one Undo follows.
    status, wrong = await asgi(app, "POST", f"/build/night/land/{rec['id']}/undo", {"how": L.UNDO_PUT_BACK})
    assert status == 409 and wrong["detail"] == (
        f"Something changed since you looked: {view['undo']['words']} Press Undo again.")
    assert git("rev-parse", "main", cwd=repo) == later
    status, done = await asgi(app, "POST", f"/build/night/land/{rec['id']}/undo", {"how": L.UNDO_REVERSE})
    assert status == 200, done
    new = git("rev-list", "--first-parent", f"{later}..main", cwd=repo).split()
    assert len(new) == 2, "one reverse commit per merge"
    assert git("rev-parse", f"{new[-1]}^", cwd=repo) == later, "on top of the later commit"
    assert not (repo / "a.py").exists() and not (repo / "b.py").exists()
    assert (repo / "later.txt").read_bytes() == b"the user's own later work\n"
    assert git("merge-base", "--is-ancestor", before_main, "main", cwd=repo) == ""
    assert git("status", "--porcelain", cwd=repo) == ""
    # Newest first: the first reverse commit reverses the second merge.
    subjects = git("log", "--format=%s", "-n", "2", "main", cwd=repo).splitlines()
    assert subjects[1].startswith("Revert \"Merge branch '" + sessions[1]["branch"])
    assert subjects[0].startswith("Revert \"Merge branch '" + sessions[0]["branch"])
    # Review 2 of 25-a: reversed, not taken out — the rows say it was merged,
    # then reversed; the head line counts them out (nothing to press), and the
    # preview says why in those words (never "already has everything").
    reversed_why = "It was merged into main, then Undo reversed it, so Merge all leaves it out."
    for s in sessions:
        facts = setup_of(app, s["sid"])["git"]
        assert facts["merged_into"] == "main" and facts["merged_at"] and facts["merge_reversed"]
    status, nights = await asgi(app, "GET", "/build/night/summaries")
    night = nights["nights"][0]
    assert night["land"]["folders"] == [] and night["land"]["green"] == 0
    assert all(r["session"]["merged"] and r["session"]["merge_reversed"] for r in night["rows"])
    [land] = (await preview(app))["landings"]
    assert land["ready"] == 0 and land["steps"] == [] and land["merged"] == [] and land["button"] == ""
    assert [x["why"] for x in land["left"]] == [reversed_why, reversed_why]


async def test_a_merge_left_mid_move_by_a_closed_iron_jarvis_reads_as_merged_and_marks_its_sessions(app, repo):
    """Note 9 of review 1: a ``moving`` record whose base git shows where the
    merge left it is MERGED, and its sessions say so, as the run would have."""
    from iron_jarvis.daemon.routes import night_land as NL

    rec, sessions, _before_main, _ = await merged_two(app, repo)
    engine = app.state.platform.engine
    L.update_record(engine, rec["id"], state=L.MOVING)
    for s in sessions:
        NL.record_session(engine, s["sid"], {"merged_at": None, "merged_into": None, "merge_commit": None})
        assert "merged_at" not in setup_of(app, s["sid"])["git"]
    status, view = await asgi(app, "GET", f"/build/night/land/{rec['id']}")
    assert status == 200 and view["state"] == L.MERGED, view
    merged = [st for st in view["steps"] if st["state"] == "merged"]
    for s, st in zip(sessions, merged):
        facts = setup_of(app, s["sid"])["git"]
        assert facts["merged_into"] == "main" and facts["merged_at"]
        assert facts["merge_commit"] == st["merge_commit"][:12]
    status, nights = await asgi(app, "GET", "/build/night/summaries")
    assert nights["nights"][0]["land"]["folders"] == []


async def test_a_merge_left_mid_move_reads_as_merged_after_a_later_commit_on_main(app, repo):
    """Note 2 of review 3: Iron Jarvis closed after main moved, and the user
    committed on main before the record was read: main still holds the
    merges, so it is MERGED (never "main was not changed")."""
    from iron_jarvis.daemon.routes import night_land as NL

    rec, sessions, _before_main, _ = await merged_two(app, repo)
    engine = app.state.platform.engine
    L.update_record(engine, rec["id"], state=L.MOVING)
    for s in sessions:
        NL.record_session(engine, s["sid"], {"merged_at": None, "merged_into": None, "merge_commit": None})
    (repo / "later.txt").write_bytes(b"the user's own later work\n")
    git("add", "later.txt", cwd=repo)
    git("commit", "-q", "-m", "later work", cwd=repo)
    status, view = await asgi(app, "GET", f"/build/night/land/{rec['id']}")
    assert status == 200 and view["state"] == L.MERGED, view
    assert "was not changed" not in view["words"]
    assert [st["state"] for st in view["steps"]].count("merged") == 2
    for s in sessions:
        assert setup_of(app, s["sid"])["git"]["merged_into"] == "main"


async def test_a_pushed_merge_is_reversed_never_moved_back(app, repo, tmp_path):
    make_remote(tmp_path, repo)
    rec, _sessions, before_main, _ = await merged_two(app, repo)
    git("push", "-q", "origin", "main", cwd=repo)
    status, view = await asgi(app, "GET", f"/build/night/land/{rec['id']}")
    assert view["undo"]["how"] == L.UNDO_REVERSE and view["undo"]["words"].startswith("Since it was pushed,")


async def test_a_checkout_with_changes_refuses_undo_in_words_with_nothing_changed(app, repo):
    rec, _sessions, _before_main, _ = await merged_two(app, repo)
    (repo / "app.py").write_bytes(b"x = 'mine, not committed'\n")
    after = git("rev-parse", "main", cwd=repo)
    status, view = await asgi(app, "GET", f"/build/night/land/{rec['id']}")
    words = (f"Your checkout of main at {repo} has changes that are not committed, so Undo cannot run now. Commit "
             "or put them away first.")
    assert view["undo"] == {"how": L.UNDO_REFUSED, "button": "", "words": words}
    for how in (L.UNDO_PUT_BACK, L.UNDO_REVERSE):
        status, out = await asgi(app, "POST", f"/build/night/land/{rec['id']}/undo", {"how": how})
        assert status == 409 and out["detail"] == words
    assert git("rev-parse", "main", cwd=repo) == after
    assert (repo / "app.py").read_bytes() == b"x = 'mine, not committed'\n"
