"""A BRANCH FOLDER RUNS WITH THE MAIN FOLDER'S ENVIRONMENT (v1.398.0, night
shift N1 — ``docs/NIGHT-SHIFT-PLAN.md``, the user's decision 3: "the branch's
checks use your main folder's existing .venv / node_modules (nothing is
installed). The summary says so.").

A coding session on its own branch works in a WORKTREE beside the repository
(v1.375.0). git does not copy what ``.gitignore`` keeps out, so the worktree
has no ``.venv`` and no ``node_modules``: its tests failed or tried to
install, and the finish check's verdict could not be trusted (v1.387.0's
open line: "worktree sessions do not see the main checkout's .venv /
node_modules").

So, in a turn whose session is on a branch (``branches.current()``), the
commands' environment (``sandbox/native.build_turn_env``: the shell, the
process tools, the finish check's test run, the project checkers' PATH)
finds the MAIN checkout's environment — read, never copied:

* the main folder's ``.venv`` (else ``venv``): its ``Scripts`` (Windows) /
  ``bin`` folder first on PATH and ``VIRTUAL_ENV`` set to it (and
  ``PYTHONHOME`` dropped), as an activated environment does;
* the main folder's ``node_modules/.bin`` first on PATH.

Each is looked for in the folder the user picked, then up to the
repository's top folder, and used only when the branch folder does not have
its own. NOTHING is made inside the worktree — never a junction or a link:
the session's trash and delete would follow one into the user's main folder.
Nothing is installed. A session that works in its folder directly, and every
other run, is untouched.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

VENV_NAMES = (".venv", "venv")


@dataclass(frozen=True)
class MainEnv:
    """The main folder's environment a branch folder's commands borrow."""

    #: The virtual environment folder ("" = none borrowed).
    venv: str = ""
    #: Its program folder (``Scripts`` on Windows, ``bin`` elsewhere).
    venv_bin: str = ""
    #: The main folder's ``node_modules/.bin`` ("" = none borrowed).
    node_bin: str = ""

    def __bool__(self) -> bool:
        return bool(self.venv_bin or self.node_bin)

    def parts(self) -> list[str]:
        out = []
        if self.venv:
            out.append(self.venv)
        if self.node_bin:
            out.append(str(Path(self.node_bin).parent))
        return out

    def words(self) -> str:
        """"your main folder's environment (C:\\…\\.venv and …\\node_modules)"."""
        return "your main folder's environment (" + " and ".join(self.parts()) + ")"

    def apply(self, env: dict[str, str]) -> dict[str, str]:
        """``env`` with this environment in front (a copy)."""
        out = dict(env)
        key = next((k for k in out if k.upper() == "PATH"), "PATH")
        front = [p for p in (self.venv_bin, self.node_bin) if p]
        rest = out.get(key, "")
        out[key] = os.pathsep.join([*front, rest] if rest else front)
        if self.venv:
            for name in [k for k in out if k.upper() in ("VIRTUAL_ENV", "PYTHONHOME")]:
                out.pop(name, None)
            out["VIRTUAL_ENV"] = self.venv
        return out


def _venv_bin(venv: Path) -> "Path | None":
    if os.name == "nt":
        cand = venv / "Scripts"
        return cand if (cand / "python.exe").is_file() else None
    cand = venv / "bin"
    return cand if (cand / "python").exists() else None


def _real_dir(path: Path) -> bool:
    """A folder that is not a link or a junction (never followed into
    somewhere else)."""
    try:
        if path.is_symlink():
            return False
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            return False
        return path.is_dir()
    except OSError:
        return False


def _up_to(start: Path, top: Path) -> list[Path]:
    """``start`` and its parents up to ``top`` (``start`` alone when it is
    not inside ``top``)."""
    out = [start]
    try:
        start.relative_to(top)
    except ValueError:
        return out
    cur = start
    while cur != top and cur.parent != cur:
        cur = cur.parent
        out.append(cur)
        if cur == top:
            break
    return out


def _has_own(folder: Path, kind: str) -> bool:
    if kind == "venv":
        return any(_venv_bin(folder / name) is not None for name in VENV_NAMES)
    return (folder / "node_modules" / ".bin").is_dir()


def find(checkout: str, folder: str, repo: str = "", worktree: str = "") -> "MainEnv | None":
    """The main folder's environment for a branch session whose user-picked
    folder is ``checkout`` (the main side) and whose working folder is
    ``folder`` (in the worktree). None when there is nothing to borrow.
    BLOCKING (stats only). Never raises."""
    try:
        if not checkout or not folder:
            return None
        main = Path(checkout).resolve()
        work = Path(folder).resolve()
        if os.path.normcase(str(main)) == os.path.normcase(str(work)) or not main.is_dir():
            return None
        top = Path(repo).resolve() if repo else main
        wtop = Path(worktree).resolve() if worktree else work
        venv = venv_bin = node_bin = ""
        # The branch folder (and its own parents up to the worktree's top)
        # keeps what it has of its own.
        own_venv = any(_has_own(p, "venv") for p in _up_to(work, wtop))
        own_node = any(_has_own(p, "node") for p in _up_to(work, wtop))
        for place in _up_to(main, top):
            if not venv and not own_venv:
                for name in VENV_NAMES:
                    cand = place / name
                    if _real_dir(cand):
                        got = _venv_bin(cand)
                        if got is not None:
                            venv, venv_bin = str(cand), str(got)
                            break
            if not node_bin and not own_node:
                cand = place / "node_modules" / ".bin"
                if _real_dir(place / "node_modules") and cand.is_dir():
                    node_bin = str(cand)
        found = MainEnv(venv=venv, venv_bin=venv_bin, node_bin=node_bin)
        return found if found else None
    except (OSError, ValueError):
        return None


def for_current_turn() -> "MainEnv | None":
    """The main folder's environment for THIS coding turn's branch
    (``branches.current()``), or None (no branch, nothing to borrow).
    BLOCKING (stats). Never raises."""
    try:
        from .branches import current

        tb = current()
    except Exception:  # noqa: BLE001
        return None
    if tb is None:
        return None
    return find(tb.checkout, tb.folder, tb.repo, tb.worktree)


def apply_for_turn(env: dict[str, str]) -> dict[str, str]:
    """``env`` with this turn's borrowed environment in front (``env`` itself
    when there is none). BLOCKING; never raises."""
    found = for_current_turn()
    return found.apply(env) if found else env


def tests_line(found: "MainEnv | None") -> str:
    """The finish check's sentence (and the night record's) when the tests
    ran with the main folder's environment; "" otherwise."""
    if not found:
        return ""
    return (
        f"These ran in this branch folder with {found.words()}: nothing was installed in the branch "
        "folder, so a change to the project's dependencies is not in the environment they ran with."
    )


__all__ = ["MainEnv", "VENV_NAMES", "apply_for_turn", "find", "for_current_turn", "tests_line"]
