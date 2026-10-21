"""The benchmark's tasks: loading, selecting, copying and fingerprinting them.

Each task is a folder under ``harness_bench/tasks/<task_id>/``:

* ``task.md`` — what the agent is told, verbatim (plain words, as a user asks);
* ``repo/`` — the tiny project the agent works in (Python, stdlib only); every
  run gets a FRESH copy of it;
* ``check/`` — HIDDEN grader material. Never copied into the agent's
  workspace; applied only in a separate grading copy after the run
  (:mod:`.grading`). Every file in it carries :data:`HIDDEN_CANARY`, so the
  tripwire (:mod:`.tripwire`) can recognise it anywhere;
* ``task.toml`` — ``category``, ``title``, ``timeout_s``, the ``check`` command
  (an argv list; ``{python}`` is the grading interpreter) and its
  ``check_timeout_s``, and for read-and-explain tasks a ``[rubric]`` table that
  grades an answer file deterministically — never a model call.

Nothing here runs a model or a process.
"""

from __future__ import annotations

import hashlib
import random
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

#: The task folders that ship with Iron Jarvis.
TASKS_DIR = Path(__file__).resolve().parent / "tasks"

#: The kinds of task the set mixes (the IJ harness plan, Phase 0).
CATEGORIES: tuple[str, ...] = (
    "bugfix", "feature", "refactor", "explain", "shell", "trap", "process",
)

#: How hard a task is (round 3). ``core`` — the first twenty: a strong local
#: model aced all of them, so they show a harness that BREAKS, not one that
#: improves. ``hard`` — ten tasks a strong model should not ace (multi-file
#: features, a cause far from its symptom, a parser with many edge cases, a
#: performance bound, a regression the obvious fix reintroduces …), so a
#: later phase can measure "at or above" below the ceiling. A ``task.toml``
#: without ``tier`` is core: the core tasks' files stay byte-identical, so a
#: stored result keeps its fingerprint.
TIERS: tuple[str, ...] = ("core", "hard")

#: Every hidden check file carries this text, so a leak is recognisable even
#: when the file was renamed or pasted into another one.
HIDDEN_CANARY = "IJ-BENCH-HIDDEN-CHECK"

#: Files and folders that are never part of a task's content (bytecode a local
#: run of the task's own tests may have left behind).
_SKIP_NAMES = frozenset({"__pycache__", ".pytest_cache"})


class TaskError(ValueError):
    """A task folder is malformed, or a selection names a task that does not exist."""


@dataclass(frozen=True)
class Task:
    id: str
    path: Path
    category: str
    title: str
    prompt: str
    check: tuple[str, ...]
    check_timeout_s: float
    timeout_s: float
    rubric: dict[str, Any] = field(default_factory=dict)
    content_hash: str = ""
    tier: str = "core"

    @property
    def repo_dir(self) -> Path:
        return self.path / "repo"

    @property
    def check_dir(self) -> Path:
        return self.path / "check"

    def hidden_files(self) -> list[Path]:
        """Everything the agent must never see: the hidden check files and the
        task's own definition (it holds the rubric's expected answers)."""
        out = [self.path / "task.toml"]
        if self.check_dir.is_dir():
            out += sorted(p for p in _walk_files(self.check_dir))
        return out


def _walk_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if any(part in _SKIP_NAMES for part in path.relative_to(root).parts):
            continue
        if path.is_file():
            yield path


def normalized_bytes(path: Path) -> bytes:
    """A file's bytes with CRLF folded to LF, so a checkout that converted line
    endings hashes the same as one that did not."""
    return path.read_bytes().replace(b"\r\n", b"\n")


def content_hash(task_dir: Path) -> str:
    """sha256 over every file of the task (definition, repo and hidden check),
    by relative path and normalized content. Any change to what the agent
    sees or to how it is graded changes it."""
    h = hashlib.sha256()
    for path in _walk_files(task_dir):
        rel = path.relative_to(task_dir).as_posix()
        h.update(rel.encode("utf-8") + b"\0")
        h.update(hashlib.sha256(normalized_bytes(path)).digest())
    return h.hexdigest()


def tree_hash(root: Path) -> str:
    """sha256 over a folder's files (relative path + raw bytes). Used to prove
    a kept workspace was not changed since its run was recorded."""
    h = hashlib.sha256()
    if not root.is_dir():
        return ""
    for path in _walk_files(root):
        rel = path.relative_to(root).as_posix()
        h.update(rel.encode("utf-8") + b"\0")
        try:
            h.update(hashlib.sha256(path.read_bytes()).digest())
        except OSError:
            h.update(b"<unreadable>")
    return h.hexdigest()


def _as_argv(raw: Any, where: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not raw or not all(isinstance(x, str) and x for x in raw):
        raise TaskError(f"{where}: check must be a list of words (the command and its arguments)")
    return tuple(raw)


def load_task(task_dir: Path) -> Task:
    task_dir = Path(task_dir)
    where = f"task {task_dir.name}"
    toml_path = task_dir / "task.toml"
    md_path = task_dir / "task.md"
    if not toml_path.is_file() or not md_path.is_file():
        raise TaskError(f"{where}: needs both task.toml and task.md")
    if not (task_dir / "repo").is_dir():
        raise TaskError(f"{where}: needs a repo/ folder")
    try:
        meta = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise TaskError(f"{where}: task.toml could not be read ({exc})") from exc
    category = str(meta.get("category") or "")
    if category not in CATEGORIES:
        raise TaskError(f"{where}: category must be one of {', '.join(CATEGORIES)}")
    tier = str(meta.get("tier") or "core")
    if tier not in TIERS:
        raise TaskError(f"{where}: tier must be one of {', '.join(TIERS)}")
    check = _as_argv(meta.get("check"), where)
    rubric = meta.get("rubric") or {}
    if not isinstance(rubric, dict):
        raise TaskError(f"{where}: [rubric] must be a table")
    if not check and not rubric:
        raise TaskError(f"{where}: needs a check command, a [rubric], or both")
    if rubric and not str(rubric.get("answer_file") or "").strip():
        raise TaskError(f"{where}: a [rubric] needs answer_file")
    check_dir = task_dir / "check"
    if check_dir.is_dir():
        for path in _walk_files(check_dir):
            if HIDDEN_CANARY not in path.read_text(encoding="utf-8", errors="replace"):
                raise TaskError(f"{where}: hidden file {path.name} must carry {HIDDEN_CANARY}")
    repo_names = {p.name.lower() for p in _walk_files(task_dir / "repo")}
    for hidden in ([*_walk_files(check_dir)] if check_dir.is_dir() else []):
        if hidden.name.lower() in repo_names:
            raise TaskError(f"{where}: hidden file {hidden.name} has the same name as a repo file")
    prompt = md_path.read_text(encoding="utf-8").strip()
    if not prompt:
        raise TaskError(f"{where}: task.md is empty")
    return Task(
        id=task_dir.name,
        path=task_dir,
        category=category,
        title=str(meta.get("title") or task_dir.name),
        prompt=prompt,
        check=check,
        check_timeout_s=float(meta.get("check_timeout_s") or 120),
        timeout_s=float(meta.get("timeout_s") or 900),
        rubric=dict(rubric),
        content_hash=content_hash(task_dir),
        tier=tier,
    )


def load_tasks(tasks_dir: Path | None = None) -> list[Task]:
    """Every task under ``tasks_dir`` (default: the shipped set), sorted by id."""
    root = Path(tasks_dir) if tasks_dir is not None else TASKS_DIR
    out = [load_task(p) for p in sorted(root.iterdir()) if p.is_dir() and p.name not in _SKIP_NAMES]
    if not out:
        raise TaskError(f"no tasks found in {root}")
    return out


def select_tasks(
    tasks: Sequence[Task],
    *,
    names: Sequence[str] | None = None,
    seed: int = 0,
    max_tasks: int | None = None,
    tier: str = "all",
) -> list[Task]:
    """The tasks a run works through, in order.

    The order is the ids sorted, then shuffled by ``random.Random(seed)`` —
    the same seed gives the same order on every machine. ``names`` keeps only
    those tasks (in the shuffled order; an unknown name is an error, never
    silently skipped), ``tier`` (``core`` / ``hard`` / ``all``) keeps only
    that tier, and ``max_tasks`` keeps the first N after that."""
    if tier not in (*TIERS, "all"):
        raise TaskError(f"--tier must be one of {', '.join((*TIERS, 'all'))}")
    by_id = {t.id: t for t in tasks}
    order = sorted(by_id)
    random.Random(int(seed)).shuffle(order)
    if names:
        wanted = [n.strip() for n in names if n and n.strip()]
        unknown = [n for n in wanted if n not in by_id]
        if unknown:
            raise TaskError(
                "no such task: " + ", ".join(unknown) + ". Known tasks: " + ", ".join(sorted(by_id))
            )
        keep = set(wanted)
        order = [i for i in order if i in keep]
    if tier != "all":
        order = [i for i in order if by_id[i].tier == tier]
    if max_tasks is not None:
        if max_tasks < 1:
            raise TaskError("max_tasks must be at least 1")
        order = order[:max_tasks]
    return [by_id[i] for i in order]


def materialize(task: Task, dest: Path) -> Path:
    """A fresh copy of the task's ``repo/`` at ``dest`` — and nothing else.
    The hidden check never comes along (see :func:`grading.grade`)."""
    dest = Path(dest)
    if dest.exists():
        raise FileExistsError(f"{dest} already exists")
    shutil.copytree(task.repo_dir, dest, ignore=shutil.ignore_patterns(*_SKIP_NAMES))
    return dest
