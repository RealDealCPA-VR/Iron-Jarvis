"""Running the benchmark: one configuration, its folder, its runs, its report.

Layout under ``<home>/harness-bench/`` (Harness² principle 10: one output
folder per configuration)::

    <runner>-<provider>-<model>-<key>/     one CONFIGURATION
        config.json                        what it is; refuses an incompatible resume
        runs/<task_id>/                    a task's run (repeat 1)
        runs/<task_id>.r<k>/               the same task's repeat k (``--repeats``, k ≥ 2)
            work/                          the agent's copy of the task's repo, moved here
                                           once the run has ended (see "ALONE" below)
            prompt.txt                     what it was told
            grade/                         work + the hidden check, made AFTER the run
            result.json                    the run record
            complete.json                  the COMPLETION MARKER (see below)
        results.json · ledger.json · report.md
    report.md                              every configuration in one table

COMPLETION IS RECORDED, NOT INFERRED (principle 5). ``complete.json`` is
written by the code that watched the runner return, and only when the runner
completed and the budget added up. It carries a FINGERPRINT of (runner,
provider, model, the task's content hash, the harness's identity, Iron Jarvis's
version) and a hash of the finished workspace. A later invocation reuses a
stored result only when the marker exists, its fingerprint is the one this
configuration computes now, and the workspace is unchanged; anything else —
output with no marker, a marker for another configuration or another version
of the task — is run again from a fresh copy.

The configuration's KEY covers the same identity, so a different model,
harness or version lands in a different folder; ``config.json`` additionally
holds the seed, and a resume with a different seed is refused in plain words.

v1.383.0 (IJ harness part 3, Phase 1):

* REPEATS. ``--repeats N`` (1 to 10) runs every task N times. Each repeat is
  its own run with the repeat number in its identity (its fingerprint, its
  folder ``runs/<task_id>.r<k>``, its ledger key), so resume and reuse work
  per repeat. Repeat 1 IS the one-run case, byte for byte: same folder, same
  fingerprint formula, no repeat field in its record or marker — a folder a
  one-run invocation wrote is reused by repeat 1 of any later one. Every
  task's repeat 1 runs before any repeat 2 starts, in the seed's order, so a
  run cut short still holds whole repeats.
* THE EXACT MODEL. A run is refused before any task starts unless the
  runner was given its model (:data:`MODEL_REQUIRED`); the effort joins the
  configuration's identity; every record carries ``model_passed``,
  ``effort_passed`` and ``model_reported`` (``None`` = the run named none).
* A task's SETUP step (:mod:`.setup_step`) makes its tree after the copy and
  before the tripwire; a step that fails is "did not run", never a fail.

v1.396.0 (the long-jobs plan's Phase 7, MEASURED): a THIRD kind of Jarvis
setup, the TEAM (``--runner jarvis-team --team <file>``, :mod:`.team`). A
runner may now say it is ready (``check_ready``: a refusal in words before any
task), make what it needs before the first task (``prepare``) and undo it
after the last (``close``, however the invocation ends), and list more in a
dry run (``dry_run_lines``). A Jarvis run's record carries its PARTS (each
model's share, :mod:`.ledger`), its ACCOUNTS' usage-window readings
(:mod:`.usage`), WHO DID WHAT (its helper rows) and, for a team, the router's
record after the run.

EVERY RUN IS WORKED ALONE (v1.383.0 review). An agent used to work in
``runs/<key>/work`` itself — two folders from repeat 1's finished work of the
same task (and every other finished run of the setup), with that path in its
prompt. Now each run's agent works in a fresh, randomly named folder under the
system temp, OUTSIDE the bench root (:func:`agent_runs_root`; one folder per
run holding the owner marker :data:`OWNER_NAME` and ``run/work/`` — a level
deeper, so the agent's ``../..`` holds only its own run, not another bench's
run going on at the same time). When the run
has ended its ``work/`` is moved into ``runs/<key>/work`` and graded, marked
and reused there exactly as before. The outside-folder snapshot covers the
temporary folder too (a write beside ``work/`` is reported as ``../<file>``).
The runners are handed no path to the bench root (:data:`runners.HIDDEN_PATHS`:
a CLI's environment and a Jarvis turn's Build session lose ``PWD``/``OLDPWD``
and any value naming it). A run cut off in this process removes its folder; a
run whose process was killed leaves one, swept at the next start
(:func:`sweep_agent_roots`: only a folder whose owner process is gone).
"""

from __future__ import annotations

import asyncio
import errno
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .. import __version__
from .catalog import TASKS_DIR, Task, TaskError, load_tasks, materialize, select_tasks, tree_hash
from .failures import INFRASTRUCTURE, Failure, classify_failure
from .grading import Grade, _remove_tree, grade
from .ledger import BudgetLedger, BudgetMismatch, record_key, run_key
from .report import combined_report, config_report
from .runners import DEFAULT_ROUNDS, EFFORTS, HIDDEN_PATHS, RUNNERS, RunOutcome, render_prompt
from .setup_step import SetupFailed, run_setup
from .tripwire import HiddenCheckLeak, assert_context_clean

BENCH_DIRNAME = "harness-bench"
MARKER_NAME = "complete.json"
RESULT_NAME = "result.json"
#: Written when a run STARTS; a run folder holding it but no result.json was
#: cut off part-way (the process was killed) and is reported as interrupted.
STARTED_NAME = "started.json"
#: v1.370.0: what the agent did, step by step (the refine loop's evidence).
TRAJECTORY_NAME = "trajectory.json"
#: v1.383.0 review: the runs THIS invocation planned (written when it
#: starts): a repeat of one of its tasks beyond them, left by an earlier
#: invocation that asked for more, is reported as stale (left out).
PLAN_NAME = "plan.json"
#: v1.383.0: ``--repeats`` runs every task this many times at most.
MAX_REPEATS = 10
#: Said before any task starts when a runner was given no model (a record
#: that cannot say which model ran is not a measurement).
MODEL_REQUIRED = "Name the model with --model, so the record says which one ran."
#: v1.383.0 review: model words that name NO particular model — each means
#: "whatever the runner's own default is" (Claude Code's ``--model default``;
#: Iron Jarvis's Claude adapter reads all three that way), which is exactly
#: the record this phase removed.
NOT_A_MODEL = frozenset({"default", "subscription", "auto"})
#: Said, before any task starts, for one of them.
MODEL_NOT_A_MODEL = (
    "--model {model} names no particular model (it means the runner's own default), so the record "
    "could not say which one ran: name the model itself, for example --model sonnet."
)
#: v1.396.0: the runners that name a PROVIDER (Iron Jarvis's own; the team's
#: is its planner's, from the team file).
PROVIDER_RUNNERS = frozenset({"jarvis", "jarvis-team"})
#: … and when the Jarvis runner was given no provider.
PROVIDER_REQUIRED = (
    "Name the provider with --provider (for example --provider claude-cli), so the record says "
    "which one answered."
)


#: v1.383.0 review: where the agents work while a run is going on — a folder
#: of its own per run under ``<AGENT_RUNS_PARENT or the system temp>/``
#: :data:`AGENT_RUNS_DIRNAME` (never under the bench root). Tests point
#: ``AGENT_RUNS_PARENT`` at a folder of their own.
AGENT_RUNS_DIRNAME = "ij-bench-runs"
AGENT_RUNS_PARENT: Path | None = None
#: In each agent folder: who made it (process id and start time), so a sweep
#: removes only a folder whose owner is gone. No path is written in it: the
#: agent can read it (it sits beside its ``work/``).
OWNER_NAME = "owner.json"
#: How long a finished run's folder is tried to be MOVED home (a program the
#: agent started may hold a file for a moment after it was stopped); after
#: that it is copied.
MOVE_WAIT_S = 15.0

_log = logging.getLogger(__name__)


class BenchRefused(RuntimeError):
    """The benchmark will not run as asked; the message says why in plain words."""


def agent_runs_root() -> Path:
    """The folder every run's agent folder is made in (outside any bench root).

    The parent is RESOLVED (v1.384.1): GitHub's Windows runners set TEMP to an
    8.3 short name (``C:\\Users\\RUNNER~1\\...``), so the agent's prompt named
    its folder one way while the folder resolved to another everywhere else —
    one folder, two names (v1.384.0's gates failed on it). The agent now sees
    the full path, the one every record and comparison uses."""
    parent = Path(AGENT_RUNS_PARENT or tempfile.gettempdir())
    try:
        parent = parent.resolve()
    except OSError:  # an unreadable parent keeps its own name
        pass
    return parent / AGENT_RUNS_DIRNAME


def _process_started(pid: int) -> float | None:
    try:
        import psutil

        return float(psutil.Process(int(pid)).create_time())
    except Exception:  # noqa: BLE001 — gone, or not readable
        return None


def make_agent_root() -> Path:
    """A fresh, randomly named agent folder with its owner marker in it. It
    is made under a hidden name and renamed into place, so every folder a
    sweep can see already names its owner."""
    parent = agent_runs_root()
    parent.mkdir(parents=True, exist_ok=True)
    name = secrets.token_hex(8)
    staging = parent / f".{name}.new"
    staging.mkdir()
    _write_json(staging / OWNER_NAME, {"pid": os.getpid(), "started": _process_started(os.getpid())})
    final = parent / name
    os.replace(staging, final)
    return final


def _owner_gone(folder: Path) -> bool:
    """True only when the folder's owner marker names a process that has
    ended (or a process id now used by another process). No marker, or a
    marker that cannot be read, is never "gone": the folder is not ours to
    judge."""
    owner = _read_json(folder / OWNER_NAME)
    if not isinstance(owner, dict) or not isinstance(owner.get("pid"), int):
        return False
    started = _process_started(owner["pid"])
    if started is None:
        return True
    want = owner.get("started")
    return isinstance(want, (int, float)) and abs(started - float(want)) > 1.0


def discard_agent_root(folder: Path) -> bool:
    """Remove one agent folder (inside :func:`agent_runs_root` only). Never
    raises: a folder a program still holds is left for the next sweep."""
    try:
        _remove_tree(folder, agent_runs_root())
        return True
    except (OSError, ValueError):
        _log.warning("could not remove the agent folder %s yet; the next start sweeps it", folder,
                     exc_info=True)
        return False


def sweep_agent_roots() -> list[str]:
    """Remove the agent folders left by runs whose process was killed (their
    owner process is gone). Returns the names removed. A folder another live
    bench owns, or one with no owner marker, is left alone."""
    root = agent_runs_root()
    if not root.is_dir():
        return []
    removed = []
    for folder in sorted(root.iterdir()):
        if folder.is_dir() and not folder.is_symlink() and _owner_gone(folder) and discard_agent_root(folder):
            removed.append(folder.name)
    return removed


def _bring_home(work: Path, dest: Path) -> str:
    """Move the agent's finished ``work`` folder to ``dest`` (``runs/<key>/work``).
    Returns "" — or a note when it had to be COPIED instead (another drive,
    or a file still held after :data:`MOVE_WAIT_S`)."""
    deadline = time.monotonic() + MOVE_WAIT_S
    while True:
        try:
            os.replace(work, dest)
            return ""
        except OSError as exc:
            other_drive = exc.errno == errno.EXDEV or getattr(exc, "winerror", None) == 17
            if other_drive or time.monotonic() > deadline:
                break
            time.sleep(0.25)
    shutil.copytree(work, dest, symlinks=True)
    return "the run's folder was copied into the results (it could not be moved)"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _slug(text: str) -> str:
    return (re.sub(r"[^a-z0-9.]+", "-", (text or "").lower()).strip("-") or "default")[:40]


def _write_json(path: Path, data: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


@dataclass
class Configuration:
    runner: str
    provider: str
    model: str
    harness: dict[str, Any]
    version: str = __version__
    #: v1.383.0: the effort asked for ("" = none — absent from the identity,
    #: so a configuration that never had one keeps its key).
    effort: str = ""

    def identity(self) -> dict[str, Any]:
        return {
            "runner": self.runner, "provider": self.provider, "model": self.model,
            "harness": self.harness, "version": self.version,
            **({"effort": self.effort} if self.effort else {}),
        }

    @property
    def key(self) -> str:
        return _sha(_canon(self.identity()))

    @property
    def folder_name(self) -> str:
        return f"{self.runner}-{_slug(self.provider)}-{_slug(self.model)}-{self.key[:12]}"

    def fingerprint(self, task: Task, repeat: int = 1) -> str:
        """The run fingerprint: this configuration + the task's content (+
        the repeat number from repeat 2 on — repeat 1 keeps the one-run
        formula, so a one-run result is repeat 1's)."""
        base = self.key + ":" + task.content_hash
        return _sha(base if int(repeat or 1) <= 1 else f"{base}:repeat:{int(repeat)}")


def bench_root(home: Path) -> Path:
    return Path(home) / BENCH_DIRNAME


def check_saved_config(cfg_dir: Path, config: Configuration, seed: int | None) -> int:
    """The seed this invocation uses. Raises :class:`BenchRefused` when the
    folder's saved configuration does not match (an incompatible resume)."""
    saved = _read_json(cfg_dir / "config.json")
    if saved is None:
        return 0 if seed is None else int(seed)
    if saved.get("key") != config.key:
        raise BenchRefused(
            f"The folder {cfg_dir} holds results for a different setup, so they cannot be "
            "resumed with this one. Move that folder away to start it again."
        )
    saved_seed = int(saved.get("seed", 0))
    if seed is not None and int(seed) != saved_seed:
        raise BenchRefused(
            f"This setup was started with --seed {saved_seed}, and you asked for --seed {seed}. "
            f"Run it with --seed {saved_seed} to resume it, or move {cfg_dir} away to start again."
        )
    return saved_seed


def reusable_result(run_dir: Path, fingerprint: str) -> dict[str, Any] | None:
    """The stored record, when this run is COMPLETE for this fingerprint: a
    marker exists, its fingerprint matches, the record matches it, and the
    workspace is the one the marker saw. Otherwise None (run again)."""
    marker = _read_json(run_dir / MARKER_NAME)
    if not isinstance(marker, dict) or marker.get("fingerprint") != fingerprint:
        return None
    record = _read_json(run_dir / RESULT_NAME)
    if not isinstance(record, dict) or record.get("fingerprint") != fingerprint:
        return None
    if tree_hash(run_dir / "work") != marker.get("work_tree"):
        return None
    return record


def _snapshot(root: Path, exclude: Path | None = None) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    exclude = exclude.resolve() if exclude is not None else None
    for folder, dirs, files in os.walk(root):
        here = Path(folder)
        if exclude is not None and here.resolve() == exclude:
            dirs[:] = []
            continue
        for name in files:
            path = here / name
            try:
                st = path.stat()
            except OSError:
                continue
            out[path.relative_to(root).as_posix()] = (st.st_size, st.st_mtime_ns)
    return out


def changed_outside(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    """Files made, changed or removed between two snapshots."""
    changed = [p for p in after if p not in before or before[p] != after[p]]
    removed = [p for p in before if p not in after]
    return sorted(changed) + [f"{p} (removed)" for p in sorted(removed)]


@dataclass
class BenchResult:
    config_dir: Path
    config: Configuration
    planned: list[str]
    records: list[dict[str, Any]] = field(default_factory=list)
    dry_run: bool = False
    plan_lines: list[str] = field(default_factory=list)
    report_path: Path | None = None


def _verdict(failure: Failure, grade_: Grade | None) -> tuple[bool | None, str, str | None]:
    """(passed, grade_reason, unscored_by). An INFRASTRUCTURE failure is
    unscored whatever the check would have said ("did not run: <reason>");
    otherwise the grade decides, and a check that could not run is unscored
    by the grader."""
    if failure.infrastructure:
        return None, f"did not run: {failure.reason}", INFRASTRUCTURE
    if grade_ is None:
        return None, "", "grader"
    return grade_.passed, grade_.reason, ("grader" if grade_.passed is None else None)


def passed_facts(runner: Any, config: Configuration) -> dict[str, Any]:
    """``{"model_passed", "effort_passed"}`` — what the runner passes (its
    own :meth:`passed_facts`), else the configuration's model and effort."""
    own = getattr(runner, "passed_facts", None)
    if callable(own):
        try:
            facts = dict(own())
            return {"model_passed": facts.get("model_passed"), "effort_passed": facts.get("effort_passed")}
        except Exception:  # noqa: BLE001 — a stub without it reads the configuration
            pass
    return {"model_passed": config.model, "effort_passed": config.effort or None}


def _record(
    task: Task, config: Configuration, fingerprint: str, outcome: RunOutcome, grade_: Grade | None,
    *, wall_s: float, started: str, files_outside: list[str], model_calls: int,
    failure: Failure | None = None, passed_as: Mapping[str, Any] | None = None, repeat: int = 1,
) -> dict[str, Any]:
    failure = failure if failure is not None else classify_failure(
        config.runner, outcome.ok_run, outcome.facts, outcome.error
    )
    passed, grade_reason, unscored_by = _verdict(failure, grade_)
    passed_as = dict(passed_as or {"model_passed": config.model, "effort_passed": config.effort or None})
    return {
        "task_id": task.id, "title": task.title, "category": task.category, "tier": task.tier,
        # v1.383.0: the repeat number, from repeat 2 on (repeat 1's record is
        # a one-run record, unchanged).
        **({"repeat": int(repeat)} if int(repeat or 1) > 1 else {}),
        "runner": config.runner, "provider": config.provider, "model": config.model,
        # v1.383.0: the model and effort as PASSED to the runner, and the
        # model the run REPORTED (None = it named none; never "default").
        "model_passed": passed_as.get("model_passed"),
        "effort_passed": passed_as.get("effort_passed"),
        "model_reported": outcome.model_reported,
        "effort_reported": outcome.effort_reported,
        "answered_provider": outcome.answered_provider, "answered_model": outcome.answered_model,
        "ok_run": outcome.ok_run, "error": outcome.error,
        "failure_kind": failure.kind, "failure_reason": failure.reason,
        "facts": dict(outcome.facts),
        "passed": passed,
        "unscored_by": unscored_by,
        "grade_reason": grade_reason,
        # What the hidden check said, even when the run is unscored for
        # infrastructure (evidence only; never counted).
        "check_verdict": grade_.passed if grade_ else None,
        "grade_output": (grade_.output if grade_ else "")[-2000:],
        "grade_parts": [
            {k: p.get(k) for k in ("kind", "passed", "reason")} for p in (grade_.parts if grade_ else [])
        ],
        "tool_calls": outcome.tool_calls, "model_rounds": outcome.model_rounds,
        "model_calls": model_calls,
        "wall_s": round(wall_s, 3),
        # v1.396.0 (Jarvis runners): each model's share of the run (the totals
        # below are their sum), every account's usage-window readings, who did
        # what, and (a team) the router's record after the run.
        **({"parts": outcome.parts} if outcome.parts is not None else {}),
        **({"accounts": outcome.accounts} if outcome.accounts is not None else {}),
        **({"helpers": outcome.helpers} if outcome.helpers is not None else {}),
        **({"team_records": outcome.team_records} if outcome.team_records is not None else {}),
        "tokens_in": outcome.tokens_in, "tokens_out": outcome.tokens_out,
        "tokens_cached": outcome.tokens_cached,
        "cost_metered_usd": outcome.cost_metered_usd,
        "cost_list_price_usd": outcome.cost_list_price_usd,
        "usage_source": outcome.usage_source,
        "files_outside": files_outside,
        "final_reply": outcome.final_reply,
        "notes": list(outcome.notes),
        "extra": outcome.extra,
        "fingerprint": fingerprint, "task_hash": task.content_hash,
        "version": config.version, "reused": False,
        "started_at": started, "finished_at": _now(),
    }


def _fresh_run_folder(task: Task, run_dir: Path, runs: Path, repeat: int = 1) -> tuple[Path, str, Path]:
    """A clean ``run_dir`` (its start noted) and, OUTSIDE the bench root, a
    fresh agent folder (:func:`make_agent_root`) holding a copy of the task's
    repo — SET UP when the task declares a setup step — plus the prompt the
    runner will be given. Returns ``(work, prompt, agent_root)``. A stale
    ``run_dir`` (no usable marker) is removed — it is the bench's own
    disposable copy, inside its own ``runs`` folder. Raises
    :class:`SetupFailed` when the setup step did not make the tree (its agent
    folder removed)."""
    if run_dir.exists():
        _remove_tree(run_dir, runs)
    run_dir.mkdir(parents=True)
    _write_json(run_dir / STARTED_NAME, {
        "task_id": task.id, "title": task.title, "category": task.category, "tier": task.tier,
        **({"repeat": int(repeat)} if int(repeat or 1) > 1 else {}),
        "started_at": _now(),
    })
    agent_root = make_agent_root()
    try:
        # Two levels down: the agent's ``..`` and ``../..`` are its own.
        work = materialize(task, agent_root / "run" / "work")
        run_setup(task, work)
    except BaseException:
        discard_agent_root(agent_root)
        raise
    prompt = render_prompt(task, work)
    (run_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    return work, prompt, agent_root


def _write_marker(run_dir: Path, work: Path, fingerprint: str, config: Configuration, task: Task,
                  repeat: int = 1) -> None:
    _write_json(run_dir / MARKER_NAME, {
        "fingerprint": fingerprint,
        "configuration": config.identity(),
        "task_hash": task.content_hash,
        **({"repeat": int(repeat)} if int(repeat or 1) > 1 else {}),
        "work_tree": tree_hash(work),
        "written_at": _now(),
    })


async def run_task(
    task: Task, runner: Any, config: Configuration, cfg_dir: Path, ledger: BudgetLedger,
    *, echo: Callable[[str], None] = lambda _s: None, repeat: int = 1, label: str | None = None,
) -> dict[str, Any]:
    """Run (or reuse) one task — its repeat ``repeat`` — for one
    configuration and return its record. ``label`` is how the progress lines
    name it (the task id by default)."""
    # Every file step below runs off the event loop (the CLAUDE.md rule): the
    # Jarvis runner's turn lives on this same loop.
    repeat = int(repeat or 1)
    name = label or task.id
    runs = cfg_dir / "runs"
    key = run_key(task.id, repeat)
    run_dir = runs / key
    fingerprint = config.fingerprint(task, repeat)
    passed_as = passed_facts(runner, config)
    stored = await asyncio.to_thread(reusable_result, run_dir, fingerprint)
    if stored is not None:
        if stored.get("passed") is None and stored.get("unscored_by") != INFRASTRUCTURE:
            # Grading is deterministic and costs no model call: an unscored
            # grade is tried again (the check may be able to run now). The
            # run itself is NOT repeated.
            g = await asyncio.to_thread(grade, task, run_dir / "work", run_dir / "grade")
            stored.update({
                "passed": g.passed, "grade_reason": g.reason, "grade_output": g.output[-2000:],
                "grade_parts": [{k: p.get(k) for k in ("kind", "passed", "reason")} for p in g.parts],
            })
            await asyncio.to_thread(_write_json, run_dir / RESULT_NAME, stored)
        word = {True: "passed", False: "failed"}.get(stored.get("passed"), "unscored")
        echo(f"  {name}: reused ({word})")
        return {**stored, "reused": True}
    started = _now()
    try:
        work, prompt, agent_root = await asyncio.to_thread(_fresh_run_folder, task, run_dir, runs, repeat)
    except SetupFailed as exc:
        # The tree the agent was to start from was never made: "did not
        # run" (infrastructure), no marker, and the runner never starts.
        rollout = ledger.begin(key, runner.name)
        outcome = RunOutcome(error=str(exc), tokens_in=0, tokens_out=0,
                             cost_metered_usd=0.0, cost_list_price_usd=0.0,
                             facts={"setup_failed": str(exc)})
        record = _record(task, config, fingerprint, outcome, None, wall_s=0.0, started=started,
                         files_outside=[], model_calls=0, passed_as=passed_as, repeat=repeat)
        ledger.verify_record(record, rollout)
        await asyncio.to_thread(_write_json, run_dir / RESULT_NAME, record)
        echo(f"  {name}: did not run ({exc})")
        return record
    try:
        return await _run_in_agent_folder(
            task, runner, config, cfg_dir, ledger, run_dir=run_dir, key=key, work=work, prompt=prompt,
            agent_root=agent_root, fingerprint=fingerprint, passed_as=passed_as, repeat=repeat,
            started=started, name=name, echo=echo,
        )
    finally:
        # However the run was left (finished, refused, cut off), its agent
        # folder goes: its work/ has been moved home, or is not wanted. A
        # folder a program still holds is left for the next start's sweep.
        if agent_root.exists():
            discard_agent_root(agent_root)


async def _run_in_agent_folder(
    task: Task, runner: Any, config: Configuration, cfg_dir: Path, ledger: BudgetLedger, *,
    run_dir: Path, key: str, work: Path, prompt: str, agent_root: Path, fingerprint: str,
    passed_as: Mapping[str, Any], repeat: int, started: str, name: str, echo: Callable[[str], None],
) -> dict[str, Any]:
    """The run itself, in its agent folder outside the bench root; its
    ``work/`` is moved into ``run_dir`` before it is graded."""
    rollout = ledger.begin(key, runner.name)
    try:
        await asyncio.to_thread(assert_context_clean, task, work, prompt)
    except HiddenCheckLeak as exc:
        outcome = RunOutcome(error=str(exc), tokens_in=0, tokens_out=0,
                             cost_metered_usd=0.0, cost_list_price_usd=0.0,
                             facts={"refused": "the agent would have seen the grader"})
        record = _record(task, config, fingerprint, outcome, None, wall_s=0.0, started=started,
                         files_outside=[], model_calls=0, passed_as=passed_as, repeat=repeat)
        ledger.verify_record(record, rollout)
        # The refused copy is kept for inspection, where a run's work lives.
        await asyncio.to_thread(_bring_home, work, run_dir / "work")
        await asyncio.to_thread(_write_json, run_dir / RESULT_NAME, record)
        echo(f"  {name}: refused. {exc}")
        return record
    # What the agent may change outside its own work/: this setup's folder,
    # and its agent folder (a write beside work/ is reported as ``../<file>``).
    before = await asyncio.to_thread(_snapshot, cfg_dir)
    before_beside = await asyncio.to_thread(_snapshot, agent_root, work)
    t0 = time.monotonic()
    echo(f"  {name}: running...")
    # No path to the bench root reaches the runner (its children's
    # environment, a Jarvis turn's Build session): the setup's state home.
    hidden = HIDDEN_PATHS.set((str(cfg_dir.parent.parent),))
    try:
        outcome = await runner.run(task, work, rollout, timeout_s=task.timeout_s)
    except Exception as exc:  # noqa: BLE001 — a runner bug is a recorded failure, not a crash
        outcome = RunOutcome(error=f"{type(exc).__name__}: {exc}")
        totals = rollout.totals()
        outcome.tokens_in, outcome.tokens_out = totals["tokens_in"], totals["tokens_out"]
        outcome.cost_metered_usd = totals["cost_metered_usd"]
        outcome.cost_list_price_usd = totals["cost_list_price_usd"]
        outcome.usage_source = "ledger"
    finally:
        HIDDEN_PATHS.reset(hidden)
    wall = time.monotonic() - t0
    after = await asyncio.to_thread(_snapshot, cfg_dir)
    after_beside = await asyncio.to_thread(_snapshot, agent_root, work)
    outside = changed_outside(before, after) + [
        # Named from the agent's folder: ``../stray.txt`` is beside its work/.
        Path(os.path.relpath(agent_root / p.removesuffix(" (removed)"), work)).as_posix()
        + (" (removed)" if p.endswith(" (removed)") else "")
        for p in changed_outside(before_beside, after_beside)
    ]
    # The run has ended: its work/ comes home, then everything below reads it
    # there (grading, the marker's workspace hash, a later reuse).
    note = await asyncio.to_thread(_bring_home, work, run_dir / "work")
    if note:
        outcome.notes.append(note)
    work = run_dir / "work"
    # v1.370.0: the run's trajectory (Jarvis runner) is kept beside the
    # record, not inside it — evidence for the refine loop, never a column.
    # Written AFTER the "outside its folder" snapshot: it is the bench's own
    # file, not something the agent touched.
    trajectory = outcome.extra.pop("trajectory", None) if isinstance(outcome.extra, dict) else None
    if trajectory is not None:
        await asyncio.to_thread(_write_json, run_dir / TRAJECTORY_NAME, trajectory)
    g = await asyncio.to_thread(grade, task, work, run_dir / "grade")
    failure = classify_failure(config.runner, outcome.ok_run, outcome.facts, outcome.error)
    record = _record(task, config, fingerprint, outcome, g, wall_s=wall, started=started,
                     files_outside=outside, model_calls=len(rollout.calls), failure=failure,
                     passed_as=passed_as, repeat=repeat)
    ledger_ok = True
    try:
        ledger.verify_record(record, rollout)
    except BudgetMismatch as exc:
        ledger_ok = False
        record["error"] = (record["error"] + "; " if record["error"] else "") + str(exc)
    record["ledger_ok"] = ledger_ok
    await asyncio.to_thread(_write_json, run_dir / RESULT_NAME, record)
    if not failure.infrastructure and ledger_ok:
        # THE COMPLETION MARKER: written here, by the code that watched the
        # runner return, and only for a run that MEASURED the agent (it
        # finished, or stopped on its own: a time or round limit, part-way) and whose
        # budget added up. An infrastructure failure runs again next time.
        await asyncio.to_thread(_write_marker, run_dir, work, fingerprint, config, task, repeat)
    if failure.infrastructure:
        echo(f"  {name}: did not run ({failure.reason})")
    else:
        word = "passed" if record["passed"] else "failed" if record["passed"] is False else "unscored"
        echo(f"  {name}: {word} in {wall:.1f} s" + (f" ({outcome.error})" if outcome.error else ""))
    return record


#: v1.396.0 review: the longest a folder a program is STARTED in may be.
#: Windows refuses a working folder of MAX_PATH (260) or more even with long
#: paths turned on (``[WinError 267] The directory name is invalid``); the
#: margin leaves room for the drive's own rules.
MAX_RUN_DIR_CHARS = 248


def run_folder_too_deep(cfg_dir: Path, keys: Iterable[str]) -> str:
    """One sentence when a planned run's folders would be too long to start a
    program in — a run's grading folder (where its hidden check is started)
    or the agent's own work folder under the system temp — else "". Reads no
    disk."""
    if os.name != "nt":
        return ""
    keys = list(keys)
    if not keys:
        return ""
    try:
        base = Path(cfg_dir).resolve()
        grade = max((str(base / "runs" / k / "grade") for k in keys), key=len)
        work = str(agent_runs_root() / ("0" * 16) / "run" / "work")
    except (OSError, ValueError):
        return ""
    for folder, words in ((grade, "a run's grading folder"), (work, "the agent's work folder")):
        if len(folder) > MAX_RUN_DIR_CHARS:
            return (f"{words[0].upper() + words[1:]} would be {len(folder)} characters long ({folder}), past the "
                    f"{MAX_RUN_DIR_CHARS} Windows allows for a folder a program starts in, so every check would "
                    "fail to start. Use a shorter --root (for example C:\\ijbench)"
                    + (" and a shorter TEMP folder." if words.startswith("the agent") else "."))
    return ""


async def _crashed_run(
    task: Task, runner: Any, config: Configuration, cfg_dir: Path, ledger: BudgetLedger, *,
    repeat: int, label: str, exc: BaseException, echo: Callable[[str], None],
) -> dict[str, Any]:
    """The record of a run the bench ITSELF crashed on (v1.396.0 review):
    "did not run" (infrastructure, no completion marker) with the crash in
    words, its numbers what the ledger saw for it (its rollout when it had
    begun, else a new empty one) so the plan still adds up. Never raises."""
    key = run_key(task.id, repeat)
    rollout = next((r for r in reversed(ledger.rollouts) if r.task_id == key), None) or ledger.begin(
        key, getattr(runner, "name", ""))
    totals = rollout.totals()
    detail = " ".join(str(exc).split())[:200]
    reason = f"the benchmark itself failed ({type(exc).__name__}: {detail})" if detail else (
        f"the benchmark itself failed ({type(exc).__name__})")
    outcome = RunOutcome(error=reason, tokens_in=totals["tokens_in"], tokens_out=totals["tokens_out"],
                         cost_metered_usd=totals["cost_metered_usd"],
                         cost_list_price_usd=totals["cost_list_price_usd"], usage_source="ledger",
                         facts={"bench_failed": reason})
    record = _record(task, config, config.fingerprint(task, repeat), outcome, None, wall_s=0.0, started=_now(),
                     files_outside=[], model_calls=len(rollout.calls), passed_as=passed_facts(runner, config),
                     repeat=repeat)
    try:
        ledger.verify_record(record, rollout)
        record["ledger_ok"] = True
    except BudgetMismatch as mismatch:
        record["ledger_ok"] = False
        record["error"] = record["error"] + "; " + str(mismatch)
    try:
        await asyncio.to_thread(_write_json, cfg_dir / "runs" / key / RESULT_NAME, record)
    except Exception:  # noqa: BLE001 — the record still reaches results.json
        pass
    echo(f"  {label}: did not run ({reason})")
    return record


def check_effort(effort: str) -> str:
    """The effort, lowered — or :class:`BenchRefused` in plain words."""
    value = (effort or "").strip().lower()
    if value not in EFFORTS:
        raise BenchRefused(f"--effort must be one of {', '.join(EFFORTS)}.")
    return value


def check_repeats(repeats: Any) -> int:
    """``repeats`` as a whole number from 1 to :data:`MAX_REPEATS`, or
    :class:`BenchRefused` in plain words."""
    try:
        n = int(repeats)
    except (TypeError, ValueError):
        n = 0
    if isinstance(repeats, bool) or n != repeats or not 1 <= n <= MAX_REPEATS:
        raise BenchRefused(f"--repeats must be a whole number from 1 to {MAX_REPEATS}.")
    return n


def require_model(runner_name: str, provider: str, model: str) -> None:
    """:class:`BenchRefused` (one plain sentence) unless the model — and, for
    the Jarvis runner, the provider — was named. A word that names no model
    (:data:`NOT_A_MODEL`, "default") is refused like a missing one."""
    if not (model or "").strip():
        raise BenchRefused(MODEL_REQUIRED)
    if model.strip().lower() in NOT_A_MODEL:
        raise BenchRefused(MODEL_NOT_A_MODEL.format(model=model.strip()))
    if runner_name in PROVIDER_RUNNERS and not (provider or "").strip():
        raise BenchRefused(PROVIDER_REQUIRED)


#: Every ``--runner`` (v1.396.0: the team's beside the three of :data:`RUNNERS`).
RUNNER_NAMES: tuple[str, ...] = ("jarvis", "jarvis-team", "claude", "codex")


def make_runner(runner: str, *, root: str, provider: str = "", model: str = "",
                rounds: int = DEFAULT_ROUNDS, binary: str | None = None,
                dialect: str = "auto", effort: str = "", team: str = "") -> Any:
    if runner not in RUNNER_NAMES:
        raise BenchRefused(f"--runner must be one of: {', '.join(RUNNER_NAMES)}")
    effort = check_effort(effort) if effort else ""
    if runner == "jarvis-team":
        # v1.396.0: the team file names the planner and the members.
        from .team import JarvisTeamRunner, load_team_file

        if provider or model:
            raise BenchRefused(
                "--provider and --model do not apply to --runner jarvis-team: the team file names "
                "the planner's provider and model."
            )
        if not (team or "").strip():
            raise BenchRefused("Name the team file with --team (it names the planner and the members).")
        spec = load_team_file(team)
        from ..daemon.chat_turn import turn_harness
        from ..platform import build_platform

        try:
            turn_harness(dialect or "auto")
        except ValueError as exc:
            raise BenchRefused(f"--dialect: {exc}") from exc
        return JarvisTeamRunner(build_platform(root), team=spec, round_budget=rounds,
                                dialect=dialect or "auto", effort=effort)
    if (team or "").strip():
        raise BenchRefused("--team applies to --runner jarvis-team only.")
    if runner == "jarvis":
        from ..daemon.chat_turn import turn_harness
        from ..platform import build_platform

        try:
            turn_harness(dialect or "auto")
        except ValueError as exc:
            raise BenchRefused(f"--dialect: {exc}") from exc
        platform = build_platform(root)
        return RUNNERS["jarvis"](
            platform, provider=provider, model=model, round_budget=rounds, dialect=dialect or "auto",
            effort=effort,
        )
    if dialect and dialect != "auto":
        raise BenchRefused("--dialect applies to the jarvis runner only (Claude Code and Codex speak their own)")
    if provider and provider != runner:
        raise BenchRefused(f"--provider does not apply to the {runner} runner (it is always {runner})")
    return RUNNERS[runner](model=model, binary=binary, effort=effort)


def answer_key_root() -> Path:
    """The folder that holds the benchmark's answer keys: the source checkout
    (its hidden checks under ``src/…/tasks`` AND the reference solutions under
    ``tests/fixtures``) when this is one, else the installed package."""
    package = TASKS_DIR.parent.parent
    checkout = package.parent.parent
    return checkout if (checkout / "pyproject.toml").is_file() else package


def resolve_bench_home(root: str) -> Path:
    """The state home this benchmark writes under, refusing the running app's
    own home (a benchmark run never shares the app's database) and any folder
    inside the Iron Jarvis source (the agents' copies would sit a few ``..``
    away from the hidden checks and reference solutions, inside a git project
    whose own context — its git status, its AGENTS.md — the CLIs read)."""
    from ..core.config import resolve_home

    if os.environ.get("IRONJARVIS_HOME", "").strip():
        raise BenchRefused(
            "IRONJARVIS_HOME is set, so --root would be ignored and the benchmark would write "
            f"into {os.environ['IRONJARVIS_HOME']}. Unset it to run the benchmark in a scratch folder."
        )
    home = resolve_home(root)
    keys = answer_key_root().resolve()
    if home.resolve() == keys or keys in home.resolve().parents:
        raise BenchRefused(
            f"That folder is inside {keys}, where the benchmark's hidden checks and reference "
            "solutions live, so an agent working there could read them. Run the benchmark with "
            "--root pointing at a scratch folder outside it."
        )
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        live = (Path(appdata) / "Iron Jarvis" / ".ironjarvis").resolve()
        if home.resolve() == live:
            raise BenchRefused(
                "That is the Iron Jarvis app's own folder. Run the benchmark with --root pointing "
                "at a scratch folder instead."
            )
    return home


async def run_bench(
    *,
    runner: Any,
    home: Path,
    tasks: Sequence[Task] | None = None,
    names: Sequence[str] | None = None,
    seed: int | None = None,
    max_tasks: int | None = None,
    dry_run: bool = False,
    echo: Callable[[str], None] = lambda _s: None,
    tier: str = "all",
    ledger: BudgetLedger | None = None,
    repeats: int = 1,
) -> BenchResult:
    """Run one configuration over the selected tasks, ``repeats`` times each
    (v1.383.0; every task's repeat 1 before any repeat 2). ``ledger``
    (v1.370.0): the refine loop passes its own, to enter this probe's
    rollouts in its stage; omitted, a fresh one (as before). A real run (not
    a dry run) is refused before any task starts when the runner has no
    model (:data:`MODEL_REQUIRED`)."""
    repeats = check_repeats(repeats)
    if not dry_run:
        require_model(runner.name, str(getattr(runner, "provider", "") or ""),
                      str(getattr(runner, "model", "") or ""))
    all_tasks = list(tasks) if tasks is not None else await asyncio.to_thread(load_tasks)
    try:
        identity = await asyncio.to_thread(runner.identity)
    except Exception as exc:  # noqa: BLE001 — e.g. the CLI is not installed
        raise BenchRefused(f"could not read the {runner.name} runner's setup ({exc})") from exc
    config = Configuration(runner=runner.name, provider=runner.provider, model=runner.model, harness=identity,
                           effort=str(getattr(runner, "effort", "") or ""))
    cfg_dir = bench_root(home) / config.folder_name
    use_seed = await asyncio.to_thread(check_saved_config, cfg_dir, config, seed)
    try:
        selected = select_tasks(all_tasks, names=names, seed=use_seed, max_tasks=max_tasks, tier=tier)
    except TaskError as exc:
        raise BenchRefused(str(exc)) from exc
    plan = [(t, k) for k in range(1, repeats + 1) for t in selected]
    result = BenchResult(config_dir=cfg_dir, config=config, planned=[run_key(t.id, k) for t, k in plan],
                         dry_run=dry_run)
    # v1.396.0 review: a --root so deep that a run's grading folder (the
    # check's working folder) passes Windows's limit for a program's folder
    # made EVERY check "could not start" — a whole night unscored. Refused
    # here, before any task, in words.
    deep = run_folder_too_deep(cfg_dir, result.planned)
    if deep:
        raise BenchRefused(deep)
    # v1.396.0: a runner refuses in words, before any task, a home it cannot
    # measure in (a single-model row beside someone else's model team).
    ready = getattr(runner, "check_ready", None)
    if callable(ready):
        await asyncio.to_thread(ready)
    if dry_run:
        result.plan_lines.append(
            f"{config.folder_name} (seed {use_seed}), {len(selected)} task(s)"
            + (f", {repeats} repeats each:" if repeats > 1 else ":")
        )
        lines = getattr(runner, "dry_run_lines", None)
        if callable(lines):
            result.plan_lines += list(lines())
        for t, k in plan:
            stored = await asyncio.to_thread(
                reusable_result, cfg_dir / "runs" / run_key(t.id, k), config.fingerprint(t, k)
            )
            result.plan_lines.append(
                f"  {t.id} [{t.tier}, {t.category}]" + (f" repeat {k}" if repeats > 1 else "") + ": "
                + ("would reuse the stored result" if stored else "would run")
            )
        return result
    await asyncio.to_thread(_save_config, cfg_dir, config, use_seed)
    await asyncio.to_thread(_write_json, cfg_dir / PLAN_NAME, {"planned": result.planned, "at": _now()})
    # A run whose process was killed left its agent folder behind: swept now
    # (only a folder whose owner process is gone).
    swept = await asyncio.to_thread(sweep_agent_roots)
    if swept:
        _log.info("removed %d agent folder(s) left by a run that was killed", len(swept))
    ledger = ledger if ledger is not None else BudgetLedger()
    echo(f"{config.folder_name}: {len(selected)} task(s), seed {use_seed}"
         + (f", {repeats} repeats" if repeats > 1 else ""))
    # v1.396.0 review: what the checks before the first task could only say
    # (a CLI's sign-in not known yet, Iron-Proxy off, a role nobody holds).
    for note in getattr(runner, "ready_notes", None) or ():
        echo(f"  note: {note}")
    # v1.396.0: what the runner needs before its first task (a team's
    # members), undone however the invocation ends.
    prepare, close = getattr(runner, "prepare", None), getattr(runner, "close", None)
    try:
        if callable(prepare):
            await asyncio.to_thread(prepare)
        for task, k in plan:
            label = f"{task.id} (repeat {k} of {repeats})" if repeats > 1 else task.id
            try:
                record = await run_task(task, runner, config, cfg_dir, ledger, echo=echo,
                                        repeat=k, label=label)
            except Exception as exc:  # noqa: BLE001 — one run's crash never ends the invocation
                # v1.396.0 review: a crash in the bench's OWN step for one run
                # (grading, bringing the work home — a file a leftover program
                # holds) used to end the whole invocation: an unattended night
                # stopped at that task. The run is "did not run" (no marker,
                # tried again next time) and the next task runs.
                record = await _crashed_run(task, runner, config, cfg_dir, ledger, repeat=k,
                                            label=label, exc=exc, echo=echo)
            result.records.append(record)
    finally:
        if callable(close):
            await asyncio.to_thread(close)
    reused = [record_key(r) for r in result.records if r.get("reused")]
    try:
        ledger.verify_plan(result.planned, reused, result.records)
    finally:
        result.report_path = await asyncio.to_thread(
            _write_outputs, cfg_dir, home, result.planned, reused, ledger
        )
    return result


def _save_config(cfg_dir: Path, config: Configuration, seed: int) -> None:
    cfg_dir.mkdir(parents=True, exist_ok=True)
    created = (_read_json(cfg_dir / "config.json") or {}).get("created_at") or _now()
    _write_json(cfg_dir / "config.json", {
        "key": config.key, "folder": config.folder_name, "seed": seed,
        **config.identity(), "created_at": created,
    })


_REPEAT_DIR = re.compile(r"^(?P<task>.+)\.r(?P<k>[0-9]+)$")


def _run_dir_identity(run_dir: Path, started: Mapping[str, Any]) -> tuple[str, int]:
    """``(task_id, repeat)`` of a run folder that holds no record: from its
    ``started.json``, else from the folder's name (``<task>.r<k>``)."""
    task_id = str(started.get("task_id") or "")
    repeat = int(started.get("repeat") or 0)
    m = _REPEAT_DIR.match(run_dir.name)
    if not task_id:
        task_id = m.group("task") if m else run_dir.name
    if not repeat:
        repeat = int(m.group("k")) if m else 1
    return task_id, repeat


def _write_outputs(
    cfg_dir: Path, home: Path, planned: list[str], reused: list[str], ledger: BudgetLedger
) -> Path:
    """The ledger history, this configuration's results + report, and the
    combined report. Returns the combined report's path."""
    history = _read_json(cfg_dir / "ledger.json") or {"invocations": []}
    history.setdefault("invocations", []).append({
        "at": _now(), "planned": planned, "reused": reused, **ledger.to_dict(),
    })
    _write_json(cfg_dir / "ledger.json", history)
    write_config_outputs(cfg_dir, reused=reused)
    return write_combined_report(home)


def _latest_records(cfg_dir: Path) -> list[dict[str, Any]]:
    out = []
    runs = cfg_dir / "runs"
    if not runs.is_dir():
        return out
    for run_dir in sorted(p for p in runs.iterdir() if p.is_dir()):
        record = _read_json(run_dir / RESULT_NAME)
        if isinstance(record, dict):
            out.append(record)
        elif (run_dir / STARTED_NAME).exists() or (run_dir / "prompt.txt").exists():
            # Started and never recorded: the process was killed part-way.
            # Listed as INTERRUPTED rather than left out of the report.
            started = _read_json(run_dir / STARTED_NAME) or {}
            task_id, repeat = _run_dir_identity(run_dir, started)
            out.append({
                "task_id": task_id, **({"repeat": repeat} if repeat > 1 else {}),
                "title": started.get("title", ""),
                "category": started.get("category", ""), "tier": started.get("tier", "core"),
                "interrupted": True,
                "passed": None, "unscored_by": "interrupted", "ok_run": False,
                "error": "interrupted: the run started but was never recorded",
                "started_at": started.get("started_at", ""),
            })
    return _mark_stale(cfg_dir, out)


def _mark_stale(cfg_dir: Path, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mark ``stale`` (v1.383.0 review) every run of a task the latest
    invocation planned (:data:`PLAN_NAME`) whose repeat is beyond the ones it
    planned: left by an earlier invocation that asked for more repeats. A task
    the latest invocation did not run keeps every run (a setup run in
    batches)."""
    plan = _read_json(cfg_dir / PLAN_NAME)
    planned = plan.get("planned") if isinstance(plan, dict) else None
    if not isinstance(planned, list):
        return records
    most: dict[str, int] = {}
    for key in planned:
        m = _REPEAT_DIR.match(str(key))
        task_id, k = (m.group("task"), int(m.group("k"))) if m else (str(key), 1)
        most[task_id] = max(most.get(task_id, 0), k)
    for r in records:
        task_id = str(r.get("task_id") or "")
        if task_id in most and int(r.get("repeat") or 1) > most[task_id]:
            r["stale"] = True
    return records


def write_config_outputs(cfg_dir: Path, reused: Sequence[str] = ()) -> Path:
    """``results.json`` + ``report.md`` for one configuration. ``reused``
    names the tasks THIS invocation took from a stored result: a stored
    record says ``reused: False`` (it was a fresh run when it was written),
    so without it the report would always say "0 reused"."""
    config = _read_json(cfg_dir / "config.json") or {}
    taken = set(reused)
    records = [
        {**r, "reused": True} if record_key(r) in taken else r
        for r in _latest_records(cfg_dir)
    ]
    _write_json(cfg_dir / "results.json", {"config": config, "records": records})
    path = cfg_dir / "report.md"
    path.write_text(config_report(config, records), encoding="utf-8")
    return path


def write_combined_report(home: Path) -> Path:
    root = bench_root(home)
    root.mkdir(parents=True, exist_ok=True)
    configs = []
    for cfg_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        config = _read_json(cfg_dir / "config.json")
        if isinstance(config, dict):
            configs.append((config, _latest_records(cfg_dir)))
    path = root / "report.md"
    path.write_text(combined_report(configs), encoding="utf-8")
    _write_json(root / "report.json", [{"config": c, "records": r} for c, r in configs])
    return path
