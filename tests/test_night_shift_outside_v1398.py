"""v1.398.0 review (night shift N1): A NIGHT TASK'S COMMANDS ARE NOT CONFINED,
SO THE USER'S MAIN FOLDER IS WATCHED.

The Build environment is the user's own (their PATH and home) and a command
can ``cd`` anywhere — a branch folder's commands even run with the MAIN
folder's ``.venv`` first on PATH, so ``pip install`` lands in it. The first
cut never looked: a task that changed the main checkout ended "done". Now
the main checkout's state is read when the task starts and again when it
ends (``harness/night_guard``), a change ends it "needs you" with a note of
its own and ``main_changed`` on the list, and every task's record says what
was NOT confined.

Also pinned here: a night task's commands START in its worktree, and the git
guard still refuses a history rewrite and cards (then, at night, declines) a
push. Real git, real venvs, the real stream lane; only the model is scripted.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import night as night_routes
from iron_jarvis.harness import night as N
from iron_jarvis.harness import night_guard as G
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_build_git_v1375 import git, make_repo
from tests.test_build_sessions_v1367 import wire
from tests.test_night_shift_review_v1398 import (  # noqa: F401 — fixtures
    NightScript,
    _ended,
    _frames,
    _git_of,
    _no_gh,
    _queue,
    night,
)

pytestmark = pytest.mark.usefixtures("no_network")


def _repo_with_venv(path: Path) -> Path:
    repo = make_repo(path)
    (repo / ".gitignore").write_bytes(b".venv/\n__pycache__/\n")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "ignore", cwd=repo)
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(repo / ".venv")],
                   check=True, capture_output=True, timeout=240)
    return repo


def _site_packages(repo: Path) -> Path:
    if os.name == "nt":
        return repo / ".venv" / "Lib" / "site-packages"
    return next((repo / ".venv" / "lib").glob("python*/site-packages"))


def _py(code: str) -> str:
    """A command line that runs ``code`` in the session's python (bash and
    cmd read it the same: double quotes, no backslashes inside)."""
    return f'python -c "{code}"'


async def test_a_task_that_changes_the_main_folder_ends_needs_you_and_says_so(tmp_path, monkeypatch, night):
    repo = _repo_with_venv(tmp_path / "proj")
    stray = (repo / "stray.txt").as_posix()
    app = create_app(str(tmp_path / "home"))
    wire(app, NightScript({
        "TASK-STRAY": [
            [ToolCall(id="c1", name="Bash", arguments={"command": _py(f"open('{stray}', 'w').write('x')")})],
            "Done TASK-STRAY.",
        ],
    }))
    task = await _queue(app, repo, "TASK-STRAY: tidy up")
    await night_routes.night_pass(app.state.d)
    ended = await _ended(app, task["id"])
    assert (repo / "stray.txt").exists(), "the scripted command did not run"
    assert ended["state"] == N.NEEDS_YOU, ended
    assert ended["main_changed"] is True
    outside = [n["text"] for n in ended["notes"] if n["kind"] == "outside"]
    assert outside and outside[0].startswith("YOUR MAIN FOLDER CHANGED") and "its files changed" in outside[0]
    assert any(n["text"] == G.NOT_CONFINED for n in ended["notes"])


async def test_a_package_installed_into_the_main_venv_is_seen(tmp_path, monkeypatch, night):
    """``pip install`` from a branch folder lands in the main folder's
    ``.venv`` (it is first on PATH there, decision 3)."""
    repo = _repo_with_venv(tmp_path / "proj")
    planted = (_site_packages(repo) / "planted.pth").as_posix()
    before = git("status", "--porcelain", cwd=repo)
    app = create_app(str(tmp_path / "home"))
    wire(app, NightScript({
        "TASK-PIP": [
            [ToolCall(id="c1", name="Bash", arguments={"command": _py(f"open('{planted}', 'w').write('')")})],
            "Done TASK-PIP.",
        ],
    }))
    task = await _queue(app, repo, "TASK-PIP: add a dependency")
    await night_routes.night_pass(app.state.d)
    ended = await _ended(app, task["id"])
    assert Path(planted).exists()
    assert git("status", "--porcelain", cwd=repo) == before, "git sees nothing: only the .venv check can"
    assert ended["state"] == N.NEEDS_YOU, ended
    outside = [n["text"] for n in ended["notes"] if n["kind"] == "outside"]
    assert outside and "the packages in its .venv changed" in outside[0], ended["notes"]


async def test_a_task_that_leaves_the_main_folder_alone_is_done_and_says_what_was_not_confined(
    tmp_path, monkeypatch, night,
):
    repo = _repo_with_venv(tmp_path / "proj")
    app = create_app(str(tmp_path / "home"))
    script = wire(app, NightScript({
        "TASK-CWD": [
            [ToolCall(id="c1", name="Bash", arguments={"command": _py("import os; print('CWD=' + os.getcwd())")})],
            [ToolCall(id="g1", name="Bash", arguments={"command": "git reset --hard HEAD~1"})],
            [ToolCall(id="g2", name="Bash", arguments={"command": "git push origin HEAD"})],
            "Done TASK-CWD.",
        ],
    }))
    task = await _queue(app, repo, "TASK-CWD: look around")
    await night_routes.night_pass(app.state.d)
    ended = await _ended(app, task["id"])
    worktree = _git_of(app, ended)["worktree"]
    told = "\n".join(str(getattr(m, "content", "")) for msgs in script.seen["TASK-CWD"] for m in msgs)
    # Its commands START in its own worktree.
    cwd_line = next(line for line in told.splitlines() if "CWD=" in line)
    got = cwd_line.split("CWD=", 1)[1].strip()
    assert os.path.normcase(os.path.realpath(got)) == os.path.normcase(os.path.realpath(worktree)), (got, worktree)
    # The git guard: a history rewrite is refused (never carded, never run);
    # a push is carded once-only, and nobody is there to answer it.
    cards = [f for e, f in _frames(app, ended["turn_id"]) if e == "approval"]
    assert [c["tool"] for c in cards] == ["shell"], cards
    assert cards[0]["once_only"] is True
    assert "rewrite" in told.lower() or "history" in told.lower(), told[-2000:]
    # It left the main folder alone: no "outside" note, and the record says
    # plainly what was not confined. (The push was declined: needs you.)
    assert ended["main_changed"] is False
    assert not [n for n in ended["notes"] if n["kind"] == "outside"]
    assert any(n["text"] == G.NOT_CONFINED for n in ended["notes"])
    assert ended["state"] == N.NEEDS_YOU


SLEEPER = (
    'python -c "import os, pathlib, time; pathlib.Path(\'pid.txt\').write_text(str(os.getpid())); '
    'time.sleep(300)"'
)


async def test_a_program_a_night_task_left_running_ends_with_the_task(tmp_path, monkeypatch, night):
    """A dev server or a watcher a night task started in the background ran
    on all night and into the morning (Emergency stop stopped the turn, not
    it). It ends when the task ends, and the record says so."""
    from tests.test_build_attempts_v1397 import _alive
    from tests.test_build_sessions_v1367 import until

    repo = make_repo(tmp_path / "proj")
    app = create_app(str(tmp_path / "home"))
    wire(app, NightScript({
        "TASK-BG": [
            [ToolCall(id="b1", name="Bash", arguments={"command": SLEEPER, "run_in_background": True})],
            "Started the server; done TASK-BG.",
        ],
    }))
    task = await _queue(app, repo, "TASK-BG: start the server")
    await night_routes.night_pass(app.state.d)
    started = N.get_task(app.state.platform.engine, task["id"])
    worktree = Path(_git_of(app, started)["worktree"])

    def _pid() -> int:
        try:
            return int((worktree / "pid.txt").read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return 0

    pid = await until(_pid, 60)
    assert pid and _alive(pid)
    ended = await _ended(app, task["id"])
    assert await until(lambda: not _alive(pid), 30), "the night task's program outlived it"
    assert any("running in the background" in n["text"] for n in ended["notes"]), ended["notes"]


def test_the_main_folder_snapshot_sees_each_kind_of_change_and_never_the_nights_own_branches(tmp_path):
    repo = _repo_with_venv(tmp_path / "proj")
    venv = str(repo / ".venv")
    base = G.snapshot(str(repo), venv)
    assert not base.get("error")
    # The night's own ij/ branches are left out.
    git("branch", "ij/night-task-1", cwd=repo)
    assert G.changes(base, G.snapshot(str(repo), venv)) == []
    # Each kind, one at a time.
    (repo / "app.py").write_bytes(b"x = 2\n")
    moved = G.changes(base, G.snapshot(str(repo), venv))
    assert moved and any("files changed" in m for m in moved) and any("uncommitted" in m for m in moved)
    after_edit = G.snapshot(str(repo), venv)
    (repo / "app.py").write_bytes(b"x = 3\n")  # already dirty: only the content moves
    assert G.changes(after_edit, G.snapshot(str(repo), venv)) == ["its uncommitted changes changed"]
    git("checkout", "-q", "--", "app.py", cwd=repo)
    git("tag", "v1", cwd=repo)
    assert G.changes(base, G.snapshot(str(repo), venv)) == ["a branch or tag of the repository changed"]
    git("tag", "-d", "v1", cwd=repo)
    git("commit", "-q", "--allow-empty", "-m", "moved", cwd=repo)
    moved = G.changes(base, G.snapshot(str(repo), venv))
    assert "its latest commit moved" in moved
    # Unreadable: nothing is claimed.
    assert G.changes(base, {"error": "x"}) is None
    assert json.loads(G.dumps(base)) == base
