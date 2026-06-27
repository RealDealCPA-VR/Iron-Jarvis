"""Repo-based self-update (git).

Iron Jarvis runs from its OWN git checkout (uv + pnpm). This module lets a user
check for, and apply, updates that were pushed to the repo — pull the new source
(``git pull --ff-only``), re-sync Python deps (``uv sync``) and rebuild the
dashboard (``pnpm install && pnpm build``).

Everything is dependency-injected: each git/build command goes through a
``runner`` callable that defaults to :data:`_subprocess_runner`. Tests inject a
fake runner, so the whole surface is exercisable offline with no real git or
network. :func:`update_status` never raises — on any error it returns a
``{available: False, reason: ...}`` descriptor.

CAVEAT — "the daemon updating its own running code": pulling new files on disk
does NOT reload the already-imported Python (or the dashboard bundle the browser
loaded). Every apply therefore reports ``restart_required: True``; the caller
(CLI/dashboard) tells the user to restart the daemon (and dashboard) so the new
code is actually loaded.
"""

from __future__ import annotations

import shutil
import subprocess
from collections import namedtuple
from pathlib import Path
from typing import Callable

#: The minimal result contract a ``runner`` must return. Any object exposing
#: ``returncode``/``stdout``/``stderr`` works (e.g. ``subprocess.CompletedProcess``);
#: this named tuple is what the default runner and the tests use.
RunResult = namedtuple("RunResult", ["returncode", "stdout", "stderr"])

#: ``runner(cmd, cwd) -> RunResult`` — run *cmd* (a full argv list) in *cwd*.
Runner = Callable[[list[str], Path], RunResult]


def _subprocess_runner(cmd: list[str], cwd: Path) -> RunResult:
    """Default runner: shell out, capturing stdout/stderr as text (never raises)."""
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True, timeout=900
        )
        return RunResult(proc.returncode, proc.stdout or "", proc.stderr or "")
    except FileNotFoundError as exc:  # e.g. git / uv / pnpm not on PATH
        return RunResult(127, "", str(exc))
    except Exception as exc:  # noqa: BLE001 - surface as a failed step, don't crash
        return RunResult(1, "", str(exc))


def _out(res: RunResult) -> str | None:
    """The stripped stdout of a successful command, else ``None``."""
    if getattr(res, "returncode", 1) != 0:
        return None
    text = (res.stdout or "").strip()
    return text or None


def update_status(repo_root: Path, runner: Runner = _subprocess_runner) -> dict:
    """How far behind the upstream branch this checkout is.

    Best-effort ``git fetch`` then computes the commit count ``HEAD..@{u}``, the
    current/remote short SHAs, the branch, and whether the working tree is clean.
    ``available`` is True only when there are upstream commits AND the tree is
    clean (i.e. :func:`apply_update` could run right now). Never raises.
    """
    repo_root = Path(repo_root)
    try:
        # Best-effort: refresh remote-tracking refs. A failure here (offline, no
        # remote) is non-fatal — we still report against whatever we already have.
        try:
            runner(["git", "fetch", "--quiet"], repo_root)
        except Exception:  # noqa: BLE001
            pass

        branch = _out(runner(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo_root))
        current = _out(runner(["git", "rev-parse", "--short", "HEAD"], repo_root))

        rl = runner(["git", "rev-list", "--count", "HEAD..@{u}"], repo_root)
        if getattr(rl, "returncode", 1) != 0:
            return {
                "available": False,
                "behind": 0,
                "current": current,
                "remote": None,
                "branch": branch,
                "clean": True,
                "reason": "no upstream tracking branch configured",
            }
        try:
            behind = int((rl.stdout or "").strip() or "0")
        except ValueError:
            behind = 0

        remote = _out(runner(["git", "rev-parse", "--short", "@{u}"], repo_root))

        st = runner(["git", "status", "--porcelain"], repo_root)
        clean = getattr(st, "returncode", 1) == 0 and not (st.stdout or "").strip()

        available = behind > 0 and clean
        if not clean:
            reason = "working tree has uncommitted changes — commit or stash before updating"
        elif behind > 0:
            reason = f"{behind} commit(s) behind upstream"
        else:
            reason = "up to date"

        return {
            "available": available,
            "behind": behind,
            "current": current,
            "remote": remote,
            "branch": branch,
            "clean": clean,
            "reason": reason,
        }
    except Exception as exc:  # noqa: BLE001 - never raise out of a status probe
        return {
            "available": False,
            "behind": 0,
            "current": None,
            "remote": None,
            "branch": None,
            "clean": False,
            "reason": f"git error: {exc}",
        }


def apply_update(
    repo_root: Path,
    build_dashboard: bool = True,
    runner: Runner = _subprocess_runner,
) -> dict:
    """Pull + rebuild this checkout. Refuses on a dirty tree.

    Steps (each captured into ``log`` with its stdout/stderr): ``git pull
    --ff-only`` → ``uv sync --extra dev`` → (optionally, when a ``dashboard/``
    dir exists and ``pnpm`` is on PATH) ``pnpm install`` + ``pnpm build``.

    Returns ``{ok, log, restart_required, reason}``. ``restart_required`` is
    always True once any step ran — the running process keeps the OLD code in
    memory until it is restarted.
    """
    repo_root = Path(repo_root)
    log: list[dict] = []

    def step(name: str, cmd: list[str], cwd: Path) -> bool:
        res = runner(cmd, cwd)
        rc = getattr(res, "returncode", 1)
        ok = rc == 0
        log.append(
            {
                "step": name,
                "cmd": " ".join(cmd),
                "returncode": rc,
                "ok": ok,
                "stdout": (getattr(res, "stdout", "") or "").strip()[-4000:],
                "stderr": (getattr(res, "stderr", "") or "").strip()[-4000:],
            }
        )
        return ok

    try:
        # Refuse on a dirty tree — pulling over uncommitted edits risks a merge
        # mess and would silently lose local changes.
        st = runner(["git", "status", "--porcelain"], repo_root)
        if getattr(st, "returncode", 1) != 0:
            return {
                "ok": False,
                "log": log,
                "restart_required": False,
                "reason": "git status failed — is this a git checkout?",
            }
        if (st.stdout or "").strip():
            return {
                "ok": False,
                "log": log,
                "restart_required": False,
                "reason": "working tree has uncommitted changes — commit or stash before updating",
            }

        if not step("git pull --ff-only", ["git", "pull", "--ff-only"], repo_root):
            return {
                "ok": False,
                "log": log,
                "restart_required": True,
                "reason": "git pull --ff-only failed (branch diverged or no fast-forward)",
            }

        if not step("uv sync --extra dev", ["uv", "sync", "--extra", "dev"], repo_root):
            return {
                "ok": False,
                "log": log,
                "restart_required": True,
                "reason": "uv sync failed",
            }

        dash = repo_root / "dashboard"
        if build_dashboard and dash.is_dir() and shutil.which("pnpm"):
            if step("pnpm install", ["pnpm", "install"], dash):
                step("pnpm build", ["pnpm", "build"], dash)

        ok = all(e["ok"] for e in log)
        return {
            "ok": ok,
            "log": log,
            "restart_required": True,
            "reason": (
                "updated — restart the daemon (and dashboard) to load the new code"
                if ok
                else "update ran but a build step failed — check the log"
            ),
        }
    except Exception as exc:  # noqa: BLE001
        log.append(
            {
                "step": "error",
                "cmd": "",
                "returncode": -1,
                "ok": False,
                "stdout": "",
                "stderr": str(exc),
            }
        )
        return {
            "ok": False,
            "log": log,
            "restart_required": True,
            "reason": f"update failed: {exc}",
        }
