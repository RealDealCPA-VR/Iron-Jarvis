"""THE USER'S MAIN FOLDER IS WATCHED WHILE A NIGHT TASK RUNS (v1.398.0
review of night shift N1).

A night task works on its OWN branch in its own worktree, and its file tools
are confined there (``safe_path``). Its COMMANDS are not: the Build
environment is the user's own (``sandbox/native.build_env``: their PATH and
home), a command can ``cd`` anywhere, and a branch folder's commands even run
with the MAIN folder's ``.venv`` first on PATH (decision 3) — so a
``pip install`` lands in the user's own environment. Nothing short of a
sandbox confines that, and the review does not pretend otherwise.

What this module does instead is DETECT: :func:`snapshot` reads the main
checkout's state before a night task starts and again when it ends, and
:func:`changes` says, in words, what moved:

* its commit (``HEAD``) and its current branch;
* its working files (``git status``, untracked included) and the content of
  its uncommitted changes (``git diff HEAD``);
* its stash;
* the repository's branches and tags (the ``ij/`` branches night tasks make
  are left out);
* the packages in the main folder's ``.venv`` (the one a branch borrows);
* (v1.399.0 review) the repository's git hooks and its ``.git/config`` — a
  hook a command planted runs on Iron Jarvis's own commit of the night's
  work and on every commit the user makes afterwards.

A change ends the task "needs you" with a note no one can miss. What is NOT
checked is said in the record too (:data:`NOT_CONFINED`): folders outside
the repository, the content of untracked files whose names did not change,
files git ignores (other than the ``.venv`` packages), and the network.

Every git call goes through ``branches._g`` (bounded, ``GIT_OPTIONAL_LOCKS=0``
— a read never takes the user's index lock). BLOCKING; never raises.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

#: The record's own sentence about what a night task's commands could reach.
NOT_CONFINED = (
    "Its commands ran as you, so they could reach other folders and the network; Iron Jarvis "
    "checked only your main folder (its commit, branches, files, git hooks and settings, and .venv packages) "
    "afterwards."
)
#: The sentence when the main folder could not be read afterwards.
UNCHECKED = (
    "Iron Jarvis could not read your main folder after this task, so it could not check that "
    "the task left it alone."
)

_WHAT: dict[str, str] = {
    "head": "its latest commit moved",
    "ref": "the branch it is on changed",
    "status": "its files changed (new, changed or deleted files)",
    "diff": "its uncommitted changes changed",
    "stash": "its stash changed",
    "refs": "a branch or tag of the repository changed",
    "env": "the packages in its .venv changed (something was installed or removed)",
    # v1.399.0 review (night shift N2): a hook runs on every commit — Iron
    # Jarvis's own commit of the night's work and every one the user makes.
    "hooks": "its git hooks changed (a hook runs on every commit you make)",
    "config": "its git settings changed (.git/config)",
}


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _site_packages(venv: str) -> "list[Path]":
    root = Path(venv)
    if os.name == "nt":
        cand = [root / "Lib" / "site-packages"]
    else:
        cand = sorted((root / "lib").glob("python*/site-packages"))
    return [p for p in cand if p.is_dir()]


def env_listing(venv: str) -> str:
    """A digest of the main ``.venv``'s installed packages (the NAMES in its
    site-packages: a ``*.dist-info`` carries its version, a ``.pth`` an
    editable install). "" when there is no venv. Never raises."""
    if not venv:
        return ""
    try:
        names: list[str] = []
        for sp in _site_packages(venv):
            names.extend(sorted(os.listdir(sp)))
        return _digest("\n".join(names))
    except OSError:
        return ""


def _git_settings(main: str) -> dict[str, str]:
    """v1.399.0 review (night shift N2): the repository's own git SETTINGS
    (``.git/config``) and HOOKS (the hooks folder git runs — ``core.hooksPath``
    or ``<common dir>/hooks``; names, sizes and times, never contents). A night
    task's commands can plant a hook there, and Iron Jarvis's own commit of
    its work then RUNS it unattended — and so does every commit the user makes
    afterwards in the main folder. ``{}`` when unreadable (nothing is said
    then, never a made-up change). Never raises."""
    from .branches import _g

    try:
        common = _g(["rev-parse", "--path-format=absolute", "--git-common-dir"], main, reads=True)
        if common.code != 0 or not common.out.strip():
            return {}
        common_dir = Path(common.out.strip())
        hooks_path = _g(["config", "--get", "core.hooksPath"], main, reads=True)
        configured = hooks_path.out.strip() if hooks_path.code == 0 else ""
        hooks_dir = Path(configured) if configured else common_dir / "hooks"
        if configured and not hooks_dir.is_absolute():
            hooks_dir = Path(main) / hooks_dir
        listing: list[str] = [f"path={configured}"]
        if hooks_dir.is_dir():
            for entry in sorted(hooks_dir.iterdir(), key=lambda p: p.name):
                if entry.name.endswith(".sample") or not entry.is_file():
                    continue
                st = entry.stat()
                listing.append(f"{entry.name}\0{st.st_size}\0{st.st_mtime_ns}")
        config_file = common_dir / "config"
        config = hashlib.sha256(config_file.read_bytes()).hexdigest() if config_file.is_file() else ""
        return {"hooks": _digest("\n".join(listing)), "config": config}
    except Exception:  # noqa: BLE001 — unreadable: no claim either way
        return {}


def snapshot(main: str, venv: str = "") -> dict[str, str]:
    """The main checkout's state (digests and refs; never file contents).
    ``{"error": "..."}`` when git could not read it. BLOCKING."""
    from .branches import BRANCH_PREFIX, _g

    if not main or not Path(main).is_dir():
        return {"error": "no main folder"}
    try:
        head = _g(["rev-parse", "-q", "--verify", "HEAD"], main, reads=True)
        if head.code != 0:
            return {"error": "git could not read it"}
        ref = _g(["symbolic-ref", "-q", "HEAD"], main, reads=True)
        status = _g(["status", "--porcelain=v1", "-z", "--untracked-files=normal"], main, reads=True)
        diff = _g(["diff", "HEAD", "--no-ext-diff", "--no-color", "--binary"], main, reads=True)
        stash = _g(["rev-parse", "-q", "--verify", "refs/stash"], main, reads=True)
        refs = _g(["for-each-ref", "--format=%(refname) %(objectname)", "refs/heads", "refs/tags"],
                  main, reads=True)
        if status.code != 0 or diff.code != 0 or refs.code != 0:
            return {"error": "git could not read it"}
        kept_refs = [
            line for line in refs.out.splitlines()
            if line.strip() and not line.startswith("refs/heads/" + BRANCH_PREFIX)
        ]
        return {
            "head": head.out.strip(),
            "ref": ref.out.strip() if ref.code == 0 else "(detached)",
            "status": _digest(status.out),
            "diff": _digest(diff.out),
            "stash": stash.out.strip() if stash.code == 0 else "",
            "refs": _digest("\n".join(sorted(kept_refs))),
            "env": env_listing(venv),
            **_git_settings(main),
        }
    except Exception:  # noqa: BLE001 — never raises: unreadable is said, not hidden
        return {"error": "git could not read it"}


def changes(before: "dict[str, str] | None", after: "dict[str, str] | None") -> "list[str] | None":
    """What moved between two snapshots, in words (``[]`` = nothing), or
    None when either could not be read (nothing can be said)."""
    if not isinstance(before, dict) or not isinstance(after, dict):
        return None
    if before.get("error") or after.get("error"):
        return None
    # v1.399.0 review: a key one side does not hold (a snapshot an older Iron
    # Jarvis took before the task started, or settings it could not read) is
    # never a change.
    return [words for key, words in _WHAT.items()
            if (key in before) == (key in after) and str(before.get(key, "")) != str(after.get(key, ""))]


def changed_words(main: str, moved: "list[str]") -> str:
    """The record's note when the main folder changed."""
    return (
        f"YOUR MAIN FOLDER CHANGED while this task ran ({main}): " + "; ".join(moved) + ". "
        "A night task works on its own branch, but its commands are not confined to it, so look at "
        "your main folder before you rely on it (something else, or you, may also have changed it)."
    )


def dumps(state: "dict[str, str] | None") -> str:
    return json.dumps(state or {}, sort_keys=True)


def loads(raw: Any) -> "dict[str, str] | None":
    try:
        got = json.loads(str(raw or ""))
    except (TypeError, ValueError):
        return None
    return {str(k): str(v) for k, v in got.items()} if isinstance(got, dict) and got else None


__all__ = ["NOT_CONFINED", "UNCHECKED", "changed_words", "changes", "dumps", "env_listing", "loads", "snapshot"]
