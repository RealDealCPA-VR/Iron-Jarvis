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
import re
from dataclasses import dataclass
from pathlib import Path

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
    #: v1.398.0 review: the BRANCH's copies of the folders an EDITABLE install
    #: in the borrowed ``.venv`` points at in the MAIN folder (a ``.pth`` line,
    #: setuptools' editable finder) — put first on ``PYTHONPATH`` so the
    #: branch's tests import the BRANCH's code, never the main folder's.
    pythonpath: tuple[str, ...] = ()
    #: v1.398.0 review: an editable install of the main folder's code that
    #: could NOT be pointed at the branch (no copy of its folder in the
    #: branch, a finder that cannot be read) — the reason, in words; "" when
    #: there is none. A test result is then not to be trusted.
    untrusted: str = ""

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
        if self.pythonpath:
            # PYTHONPATH comes BEFORE site-packages on sys.path (and an
            # editable finder sits after the path finder), so the branch's
            # copy is imported in place of the main folder's.
            pkey = next((k for k in out if k.upper() == "PYTHONPATH"), "PYTHONPATH")
            prior = out.get(pkey, "")
            out[pkey] = os.pathsep.join([*self.pythonpath, prior] if prior else list(self.pythonpath))
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


#: How much of one ``.pth`` / editable finder file is read (they are a few
#: lines; a bigger one is not an editable install's).
_EDITABLE_READ_MAX = 256 * 1024
#: ``.pth`` files that are a venv's own plumbing, never an editable install.
_PLUMBING_PTH = ("_virtualenv.pth", "distutils-precedence.pth")


def _site_packages_of(venv: Path) -> list[Path]:
    if os.name == "nt":
        cand = [venv / "Lib" / "site-packages"]
    else:
        cand = sorted((venv / "lib").glob("python*/site-packages"))
    return [p for p in cand if p.is_dir()]


def _inside(path: Path, top: Path) -> bool:
    try:
        a, b = os.path.normcase(str(path)), os.path.normcase(str(top))
        return os.path.commonpath([a, b]) == b
    except ValueError:  # another drive
        return False


def _read_small(path: Path) -> str:
    try:
        if path.stat().st_size > _EDITABLE_READ_MAX:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _string_paths(source: str) -> list[str]:
    """Every absolute path written as a string literal in a small module (an
    editable finder's ``MAPPING`` / an ``editables`` redirect). Never runs it."""
    import ast

    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value.strip()
            if v and len(v) < 1000 and os.path.isabs(v):
                out.append(v)
    return out


def editable_redirects(venv: str, main_top: str, work_top: str) -> "tuple[tuple[str, ...], str]":
    """v1.398.0 review: an EDITABLE install in the main folder's ``.venv``
    (``pip install -e .``, ``uv pip install -e``, ``uv sync`` of the project
    itself) imports the MAIN folder's code, so a branch's tests that borrow
    the venv test the wrong code and pass. Returns ``(branch_paths, reason)``:
    the branch's copies of the import roots the install points at inside the
    main repository (to put first on ``PYTHONPATH``), and — when one cannot
    be pointed at the branch — the reason in words ("" when none). Reads
    ``.pth`` lines, the modules a ``.pth`` imports (setuptools' editable
    finder, an ``editables`` redirect: their string paths, never run), and
    ``direct_url.json`` (``dir_info.editable``) as the marker. BLOCKING
    (small reads); never raises."""
    try:
        main = Path(main_top).resolve()
        work = Path(work_top).resolve()
        venv_dir = Path(venv).resolve() if venv else None
    except (OSError, ValueError):
        return (), ""
    if venv_dir is None:
        return (), ""
    roots: list[Path] = []
    missing: list[str] = []
    marked: list[str] = []

    def _take(raw: str, base: Path, *, package: bool) -> None:
        try:
            p = Path(raw)
            p = (p if p.is_absolute() else base / p).resolve()
        except (OSError, ValueError):
            return
        if not _inside(p, main) or _inside(p, venv_dir):
            return
        # A package folder or a module file: its PARENT is the import root.
        root = p.parent if (package or p.suffix == ".py") else p
        try:
            rel = root.relative_to(main)
        except ValueError:
            return
        copy = work / rel
        if copy.is_dir():
            if copy not in roots:
                roots.append(copy)
        elif str(rel) not in missing:
            missing.append(str(rel) or ".")

    for sp in _site_packages_of(venv_dir):
        try:
            names = sorted(os.listdir(sp))
        except OSError:
            continue
        for name in names:
            path = sp / name
            low = name.lower()
            if low.endswith(".pth") and low not in _PLUMBING_PTH:
                for line in _read_small(path).splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith(("import ", "import\t")):
                        for mod in re.findall(r"\bimport\s+([A-Za-z_][A-Za-z0-9_]*)", line):
                            for raw in _string_paths(_read_small(sp / f"{mod}.py")):
                                _take(raw, sp, package=True)
                        continue
                    _take(line, sp, package=False)
            elif low.endswith(".dist-info"):
                direct = _read_small(path / "direct_url.json")
                if '"editable"' in direct:
                    try:
                        import json
                        from urllib.parse import unquote, urlparse

                        info = json.loads(direct)
                        url = urlparse(str(info.get("url") or ""))
                        target = unquote(url.path or "")
                        if os.name == "nt" and re.match(r"^/[A-Za-z]:", target):
                            target = target[1:]
                        if (info.get("dir_info") or {}).get("editable") and target and _inside(Path(target).resolve(), main):
                            marked.append(name.split("-")[0])
                    except (ValueError, TypeError, AttributeError, OSError):
                        continue
    reason = ""
    if missing:
        reason = (
            "an editable install in your main folder's .venv points at "
            + ", ".join(missing[:3])
            + " in your main folder, which this branch does not have, so its tests may have run "
            "your main folder's code"
        )
    elif marked and not roots:
        reason = (
            "an editable install of your main folder's code (" + ", ".join(sorted(set(marked))[:3])
            + ") could not be pointed at this branch, so its tests may have run your main folder's code"
        )
    return tuple(str(r) for r in roots), reason


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
        paths: tuple[str, ...] = ()
        untrusted = ""
        if venv:
            paths, untrusted = editable_redirects(venv, str(top), str(wtop))
        found = MainEnv(venv=venv, venv_bin=venv_bin, node_bin=node_bin, pythonpath=paths, untrusted=untrusted)
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
    line = (
        f"These ran in this branch folder with {found.words()}: nothing was installed in the branch "
        "folder, so a change to the project's dependencies is not in the environment they ran with."
    )
    if found.untrusted:
        line += " " + untrusted_words(found)
    elif found.pythonpath:
        line += (
            " An editable install in that .venv points at your main folder's code; this branch's copy "
            "was put first on the import path, so they tested this branch's code."
        )
    return line


def untrusted_words(found: "MainEnv | None") -> str:
    """"NOT TRUSTED: …" when an editable install of the main folder's code
    could not be pointed at the branch; "" otherwise."""
    if not found or not found.untrusted:
        return ""
    return f"NOT TRUSTED: {found.untrusted}."


__all__ = [
    "MainEnv", "VENV_NAMES", "apply_for_turn", "editable_redirects", "find", "for_current_turn",
    "tests_line", "untrusted_words",
]
