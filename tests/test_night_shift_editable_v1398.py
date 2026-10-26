"""v1.398.0 review (night shift N1, decision 3): AN EDITABLE INSTALL IN THE
MAIN FOLDER'S ``.venv`` MUST NOT TEST THE MAIN FOLDER'S CODE FROM A BRANCH.

A branch folder borrows the main folder's ``.venv`` (nothing is installed in
the branch). When that venv holds an EDITABLE install of the project itself
(``uv sync``, ``pip install -e .``), ``import thepackage`` resolves to the MAIN
folder's source — so the branch's tests ran against the wrong code, and a
pass said nothing about the branch. Now the branch's copy of each import root
the install points at goes first on ``PYTHONPATH`` (``harness/main_env.
editable_redirects``); a root the branch does not have makes the result
"NOT TRUSTED" in the words.

The proof uses a REAL editable install (``uv pip install --offline -e``, the
same backend the project builds with), skipped only where uv cannot make one
offline.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import night as night_routes
from iron_jarvis.harness import main_env as ME
from iron_jarvis.harness import night as N
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_build_git_v1375 import git, make_repo
from tests.test_build_sessions_v1367 import wire
from tests.test_night_shift_review_v1398 import (  # noqa: F401 — fixtures
    NightScript,
    _ended,
    _git_of,
    _no_gh,
    _queue,
    night,
)

pytestmark = pytest.mark.usefixtures("no_network")

PYPROJECT = b"""[project]
name = "mypkg"
version = "0.1"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/mypkg"]
"""


def _project(path: Path) -> Path:
    repo = make_repo(path)
    (repo / "src" / "mypkg").mkdir(parents=True)
    (repo / "src" / "mypkg" / "__init__.py").write_bytes(b'VALUE = "main"\n')
    (repo / "pyproject.toml").write_bytes(PYPROJECT)
    (repo / ".gitignore").write_bytes(b".venv/\n__pycache__/\n*.egg-info/\n")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "package", cwd=repo)
    return repo


def _real_editable_install(repo: Path) -> None:
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("uv is not on PATH")
    venv = repo / ".venv"
    made = subprocess.run([uv, "venv", "-q", "--python", sys.executable, str(venv)],
                          capture_output=True, timeout=240)
    if made.returncode != 0:
        pytest.skip("uv could not make a venv here: " + made.stderr.decode("utf-8", "replace")[-300:])
    py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    got = subprocess.run([uv, "pip", "install", "--offline", "-q", "-e", str(repo), "--python", str(py)],
                         capture_output=True, timeout=300)
    if got.returncode != 0:
        pytest.skip("uv could not make an editable install offline: "
                    + got.stderr.decode("utf-8", "replace")[-300:])
    # The real thing: from the MAIN folder, mypkg is the main folder's source.
    out = subprocess.run([str(py), "-c", "import mypkg; print(mypkg.__file__)"],
                         capture_output=True, timeout=120, cwd=str(repo.parent))
    assert str(repo / "src" / "mypkg").lower() in out.stdout.decode("utf-8", "replace").lower()


async def test_a_branchs_python_imports_the_branchs_code_not_the_main_folders_editable_install(
    tmp_path, monkeypatch, night,
):
    repo = _project(tmp_path / "proj")
    _real_editable_install(repo)
    app = create_app(str(tmp_path / "home"))
    script = wire(app, NightScript({
        "TASK-EDIT": [
            [ToolCall(id="w1", name="Write", arguments={
                "file_path": "src/mypkg/__init__.py", "content": 'VALUE = "branch"\n'})],
            [ToolCall(id="c1", name="Bash", arguments={
                "command": 'python -c "import mypkg; print(\'VALUE=\' + mypkg.VALUE)"'})],
            "Done TASK-EDIT.",
        ],
    }))
    task = await _queue(app, repo, "TASK-EDIT: change the value")
    await night_routes.night_pass(app.state.d)
    ended = await _ended(app, task["id"])
    told = "\n".join(str(getattr(m, "content", "")) for msgs in script.seen["TASK-EDIT"] for m in msgs)
    assert "VALUE=branch" in told and "VALUE=main" not in told, told[-1500:]
    worktree = Path(_git_of(app, ended)["worktree"])
    found = ME.find(str(repo), str(worktree), str(repo), str(worktree))
    assert found is not None and found.pythonpath == (str((worktree / "src").resolve()),), found
    assert not found.untrusted


def test_editable_installs_are_read_never_run_and_a_missing_branch_copy_is_not_trusted(tmp_path):
    main = tmp_path / "main"
    work = tmp_path / "work"
    for top in (main, work):
        (top / "src" / "pkg").mkdir(parents=True)
        (top / "lib").mkdir()
    (main / "only_main").mkdir()
    venv = main / ".venv"
    sp = venv / ("Lib/site-packages" if os.name == "nt" else "lib/python3.12/site-packages")
    sp.mkdir(parents=True)
    # uv / hatchling: a path line. setuptools: an import line + a finder
    # whose MAPPING names the package folder (read as text, never run — the
    # finder below would raise if it ran).
    (sp / "_editable_impl_pkg.pth").write_text(str(main / "src") + "\n", encoding="utf-8")
    (sp / "__editable__.other-1.0.pth").write_text("import __editable___other_1_0_finder; "
                                                    "__editable___other_1_0_finder.install()\n", encoding="utf-8")
    (sp / "__editable___other_1_0_finder.py").write_text(
        "raise SystemExit('never run')\n"
        f"MAPPING = {{'other': {str(main / 'lib' / 'other')!r}}}\n", encoding="utf-8")
    (sp / "_virtualenv.pth").write_text("import _virtualenv\n", encoding="utf-8")
    (sp / "inside_venv.pth").write_text(str(venv / "extras") + "\n", encoding="utf-8")
    paths, reason = ME.editable_redirects(str(venv), str(main), str(work))
    assert set(paths) == {str((work / "src").resolve()), str((work / "lib").resolve())}, paths
    assert reason == ""
    # A root the branch does not have: NOT TRUSTED, said in words.
    (sp / "_editable_impl_more.pth").write_text(str(main / "only_main") + "\n", encoding="utf-8")
    _paths, reason = ME.editable_redirects(str(venv), str(main), str(work))
    assert "only_main" in reason and "your main folder's code" in reason
    env = ME.MainEnv(venv=str(venv), venv_bin=str(venv), pythonpath=(str(work / "src"),)).apply(
        {"PATH": "x", "PYTHONPATH": "y"})
    assert env["PYTHONPATH"] == os.pathsep.join([str(work / "src"), "y"])
    line = ME.tests_line(ME.MainEnv(venv=str(venv), venv_bin=str(venv), untrusted=reason))
    assert "NOT TRUSTED:" in line


async def test_a_pass_that_cannot_be_trusted_says_so_and_ends_needs_you(tmp_path, monkeypatch, night):
    """The main folder's venv points an install at a folder the branch does
    not have (an ignored, generated one): the pass is said NOT TRUSTED, in
    the finish check's words and the night record, and the task needs you."""
    repo = make_repo(tmp_path / "proj")
    (repo / ".gitignore").write_bytes(b".venv/\ngenerated/\n__pycache__/\n")
    (repo / "test_ok.py").write_bytes(b"import unittest\n\n\nclass T(unittest.TestCase):\n"
                                      b"    def test_ok(self):\n        self.assertTrue(True)\n")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "tests", cwd=repo)
    (repo / "generated").mkdir()
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(repo / ".venv")],
                   check=True, capture_output=True, timeout=240)
    sp = repo / ".venv" / ("Lib/site-packages" if os.name == "nt" else "")
    if os.name != "nt":
        sp = next((repo / ".venv" / "lib").glob("python*/site-packages"))
    (sp / "_editable_impl_gen.pth").write_text(str(repo / "generated") + "\n", encoding="utf-8")
    app = create_app(str(tmp_path / "home"))
    script = wire(app, NightScript({
        "TASK-TRUST": [
            [ToolCall(id="w1", name="Write", arguments={"file_path": "note.txt", "content": "x\n"})],
            [ToolCall(id="c1", name="Bash", arguments={"command": "python -m unittest -q test_ok"})],
            "Done TASK-TRUST.",
        ],
    }))
    task = await _queue(app, repo, "TASK-TRUST: add a note and run the tests")
    await night_routes.night_pass(app.state.d)
    ended = await _ended(app, task["id"])
    tests = [n["text"] for n in ended["notes"] if n["kind"] == "tests"]
    assert tests and tests[0].startswith("Its tests passed.") and "NOT TRUSTED:" in tests[0], ended["notes"]
    assert ended["state"] == N.NEEDS_YOU, ended
    told = "\n".join(str(getattr(m, "content", "")) for msgs in script.seen["TASK-TRUST"] for m in msgs)
    assert "NOT TRUSTED:" in told
