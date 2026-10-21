"""Grading: the hidden check, applied in a SEPARATE copy after the run.

Harness² principle 4. The agent's final workspace is copied to a grading
folder, the task's hidden ``check/`` files are laid over that copy, and the
task's check command runs there — never in the workspace the agent used, and
never before the agent is done. Read-and-explain tasks are graded by a
deterministic ``[rubric]`` over an answer file instead (or as well): never a
model call.

A grade is ``True`` (passed), ``False`` (failed) or ``None`` — UNSCORED. A
check that could not run at all is unscored, never a fail: no interpreter to
run it with, its process could not start, or it ended with an exit code that
is not a test verdict. Unscored grades leave the pass rate's denominator
(:mod:`.report`), and the report says how many there were.

A check that RAN PAST ITS TIME LIMIT is a FAIL (review round 3): it did run,
and what did not finish is the code it tests — an agent's infinite loop, a
solution far too slow. Counting that as "could not run" dropped exactly those
runs from the pass rate.

The check's exit code is the verdict: 0 passed, 1 failed (``unittest`` exits 1
for failures AND errors, including a module the agent broke so it no longer
imports), anything else unscored (``unittest`` exits 5 when no test ran).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..core import procs
from .catalog import Task

#: Exit codes a check answers with when it ran: the verdict.
PASS_CODE = 0
FAIL_CODE = 1
#: How much of a check's output a grade keeps (the tail, where unittest
#: prints its summary).
OUTPUT_TAIL_CHARS = 4000


@dataclass
class Grade:
    passed: bool | None
    reason: str
    output: str = ""
    parts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def grading_python() -> str | None:
    """The interpreter a check runs with: this one in a source install; a
    Python found on PATH in a packaged one (whose own executable is Iron
    Jarvis, not Python). ``None`` when there is none — every check is then
    unscored, never failed."""
    if not getattr(sys, "frozen", False) and sys.executable:
        return sys.executable
    for name in ("python", "python3", "py"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _remove_tree(path: Path, inside: Path) -> None:
    path = Path(path).resolve()
    inside = Path(inside).resolve()
    if path == inside or inside not in path.parents:
        raise ValueError(f"refusing to remove {path}: it is not inside {inside}")

    def _clear_read_only(func, target, _exc):
        os.chmod(target, 0o700)
        func(target)

    shutil.rmtree(path, onexc=_clear_read_only)


def prepare_grading_copy(task: Task, workspace: Path, grade_dir: Path) -> Path:
    """``grade_dir`` = the agent's final workspace + the hidden check files on
    top. Links are copied as links (never followed out of the folder)."""
    grade_dir = Path(grade_dir)
    if grade_dir.exists():
        _remove_tree(grade_dir, grade_dir.parent)
    shutil.copytree(
        workspace, grade_dir, symlinks=True,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
    )
    if task.check_dir.is_dir():
        for path in sorted(task.check_dir.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            target = grade_dir / path.relative_to(task.check_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    return grade_dir


def _tail(data: bytes) -> str:
    return procs.text(data, "utf-8")[-OUTPUT_TAIL_CHARS:]


def run_check(task: Task, grade_dir: Path, *, python: str | None) -> dict[str, Any]:
    """Run the task's check command in ``grade_dir``. Returns a part:
    ``{"kind": "check", "passed": True|False|None, "reason", "output"}``."""
    part: dict[str, Any] = {"kind": "check", "passed": None, "reason": "", "output": ""}
    needs_python = any("{python}" in word for word in task.check)
    if needs_python and not python:
        part["reason"] = "no Python interpreter was found to run the check with"
        return part
    argv = [word.replace("{python}", python or "") for word in task.check]
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PYTHON")}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        result = procs.run(
            argv, timeout=task.check_timeout_s, cwd=grade_dir, env=env, background=False,
        )
    except OSError as exc:
        part["reason"] = f"the check could not start ({exc})"
        return part
    out = _tail((result.stdout or b"") + b"\n" + (result.stderr or b""))
    part["output"] = out
    if result.timed_out:
        # It ran, and the code under test did not finish: the agent's
        # failure (see the module docstring), never "could not run".
        part["passed"] = False
        part["reason"] = (
            f"the check ran past its {task.check_timeout_s:g} s limit "
            "(the code it tests did not finish)"
        )
        return part
    if result.returncode == PASS_CODE:
        part["passed"] = True
        part["reason"] = "the check passed"
    elif result.returncode == FAIL_CODE:
        part["passed"] = False
        part["reason"] = "the check failed"
    else:
        part["reason"] = f"the check ended with exit code {result.returncode}, which is not a verdict"
    return part


def _same(got: Any, want: Any) -> bool:
    """A rubric value matches: numbers by value, text case- and
    space-insensitively, a bare file name also matching a path to it; a list
    item by item IN ORDER (same length), a table key by key (same keys)."""
    if isinstance(want, list):
        return isinstance(got, list) and len(got) == len(want) and all(
            _same(g, w) for g, w in zip(got, want)
        )
    if isinstance(want, dict):
        return isinstance(got, dict) and set(got) == set(want) and all(
            _same(got[k], want[k]) for k in want
        )
    if isinstance(want, bool):
        return got is want
    if isinstance(want, (int, float)):
        try:
            return not isinstance(got, bool) and float(got) == float(want)
        except (TypeError, ValueError):
            return False
    if not isinstance(got, str):
        return False
    g = " ".join(got.strip().lower().replace("\\", "/").split())
    w = " ".join(str(want).strip().lower().replace("\\", "/").split())
    if g.startswith("./"):
        g = g[2:]
    if g == w:
        return True
    return "/" not in w and "." in w and g.rsplit("/", 1)[-1] == w


def _phrase_found(text: str, entry: Any) -> bool:
    options = entry if isinstance(entry, list) else [entry]
    lowered = " ".join(text.lower().split())
    return any(" ".join(str(o).lower().split()) in lowered for o in options)


def run_rubric(task: Task, grade_dir: Path) -> dict[str, Any]:
    """Grade the answer file by the task's ``[rubric]`` — deterministic.

    ``answer_file`` must exist (missing = failed: the agent did not answer).
    ``json_equals`` (a table): the file is a JSON object and each key equals
    its value. ``required_phrases`` (a list; an entry may itself be a list of
    alternatives): each must appear in the file's text."""
    rubric = task.rubric
    part: dict[str, Any] = {"kind": "rubric", "passed": False, "reason": "", "output": ""}
    answer = Path(grade_dir) / str(rubric.get("answer_file"))
    if not answer.is_file():
        part["reason"] = f"{rubric.get('answer_file')} was not written"
        return part
    text = answer.read_text(encoding="utf-8", errors="replace")
    part["output"] = text[:OUTPUT_TAIL_CHARS]
    wrong: list[str] = []
    expected = rubric.get("json_equals")
    if isinstance(expected, dict) and expected:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            part["reason"] = f"{answer.name} is not valid JSON"
            return part
        if not isinstance(data, dict):
            part["reason"] = f"{answer.name} is not a JSON object"
            return part
        for key, want in expected.items():
            if key not in data:
                wrong.append(f"{key} is missing")
            elif not _same(data[key], want):
                wrong.append(f"{key} is {data[key]!r}")
    for entry in rubric.get("required_phrases") or []:
        if not _phrase_found(text, entry):
            wrong.append(f"no mention of {entry!r}")
    if wrong:
        part["reason"] = "the answer is wrong: " + "; ".join(wrong)
        return part
    part["passed"] = True
    part["reason"] = "the answer is right"
    return part


def combine(parts: list[dict[str, Any]]) -> tuple[bool | None, str]:
    """A definite fail anywhere fails the task; otherwise any part that could
    not run leaves it unscored; otherwise it passed."""
    if any(p["passed"] is False for p in parts):
        return False, "; ".join(p["reason"] for p in parts if p["passed"] is False)
    if any(p["passed"] is None for p in parts):
        return None, "; ".join(p["reason"] for p in parts if p["passed"] is None)
    return True, "; ".join(p["reason"] for p in parts)


def grade(task: Task, workspace: Path, grade_dir: Path, *, python: str | None = None) -> Grade:
    """Grade ``workspace`` for ``task`` in ``grade_dir`` (made fresh here)."""
    py = python if python is not None else grading_python()
    try:
        prepare_grading_copy(task, workspace, grade_dir)
    except (OSError, ValueError) as exc:
        return Grade(None, f"the grading copy could not be made ({exc})")
    parts: list[dict[str, Any]] = []
    if task.check:
        parts.append(run_check(task, grade_dir, python=py))
    if task.rubric:
        parts.append(run_rubric(task, grade_dir))
    passed, reason = combine(parts)
    output = "\n".join(p["output"] for p in parts if p.get("output"))[-OUTPUT_TAIL_CHARS:]
    return Grade(passed, reason, output, parts)
