"""A task's SETUP step: the tree the agent starts from, made before it starts.

v1.383.0 (IJ harness part 3, Phase 1). Two ``frontier`` tasks start from a
tree that cannot sit in a task folder: a generated repository of ~2,000 files
(too many to commit) and a repository in the middle of a merge (it needs
history). Each declares a ``[setup]`` table in its ``task.toml`` (see
:mod:`.catalog`); the bench copies ``repo/`` as for any task and then runs the
declared script IN THE COPY, before the tripwire looks at the copy and before
any runner starts.

The rules the step runs under:

* DETERMINISTIC: the script gets the task's own fixed ``seed`` (never the
  bench's ``--seed``, which only orders the tasks), so the same task makes
  the same tree on every run and every machine — pinned in the suite.
* OFFLINE, under the same process rules as everything else: one child
  through :mod:`core.procs` (bounded, the whole tree killed at its limit),
  run by the grading interpreter in isolated mode with no ``PYTHON*``
  variable, no user or system git configuration, and git never prompting.
* HIDDEN: the script lives in the task's ``setup/`` folder, never in the
  copy, and is one of the task's hidden files (it knows what the tree holds);
  the tripwire runs AFTER the step, so a step that put grader material in the
  copy would stop the run.
* A STEP THAT FAILS IS "DID NOT RUN", never a graded fail: the agent never
  had its go (:class:`SetupFailed`, read by ``bench.run_task``).
"""

from __future__ import annotations

import os
from pathlib import Path

from ..core import procs
from .catalog import Task, materialize

#: How much of a failed step's output its error keeps.
_OUTPUT_TAIL = 800
#: Variables that point git at another repository or index (git sets them
#: for its hooks); a setup step makes its own repository, in the copy.
_GIT_REDIRECTS = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_NAMESPACE",
    "GIT_CEILING_DIRECTORIES",
)


class SetupFailed(RuntimeError):
    """The task's setup step did not make its tree; the message says why."""


def setup_env() -> dict[str, str]:
    """The step's environment: this one, minus every ``PYTHON*`` variable and
    every git redirect, with git's own configuration files switched off (a
    user's ``commit.gpgsign`` or hooks path must not change the tree) and
    git never asking for anything."""
    env = {
        k: v for k, v in os.environ.items()
        if not k.upper().startswith("PYTHON") and k.upper() not in _GIT_REDIRECTS
        and not k.upper().startswith("GIT_CONFIG")
    }
    env.update({
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
    })
    return env


def run_setup(task: Task, work: Path, *, python: str | None = None) -> None:
    """Run ``task``'s setup step in ``work`` (a fresh copy of its repo).
    Nothing to do for a task without one. Raises :class:`SetupFailed` in
    plain words when the step cannot run, fails or runs past its limit."""
    if not task.setup_script:
        return
    from .grading import grading_python

    py = python if python is not None else grading_python()
    if not py:
        raise SetupFailed("no Python interpreter was found to run the task's setup step")
    script = task.setup_dir / task.setup_script
    argv = [py, "-I", str(script), str(Path(work)), str(int(task.setup_seed))]
    try:
        result = procs.run(
            argv, timeout=float(task.setup_timeout_s), cwd=Path(work), env=setup_env(), background=False,
        )
    except OSError as exc:
        raise SetupFailed(f"the task's setup step could not start ({exc})") from exc
    if result.timed_out:
        raise SetupFailed(f"the task's setup step ran past its {task.setup_timeout_s:g} s limit")
    if result.returncode != 0:
        said = (procs.text(result.stderr or b"", "utf-8").strip()
                or procs.text(result.stdout or b"", "utf-8").strip())
        raise SetupFailed(
            f"the task's setup step failed (exit code {result.returncode})"
            + (f": {said[-_OUTPUT_TAIL:]}" if said else "")
        )


def prepare_workspace(task: Task, dest: Path, *, python: str | None = None) -> Path:
    """A fresh copy of the task's ``repo/`` at ``dest``, SET UP: the tree the
    agent starts from (and the one the suite grades the untouched repo, the
    reference solution and the decoy in). Raises :class:`SetupFailed`."""
    work = materialize(task, dest)
    run_setup(task, work, python=python)
    return work
