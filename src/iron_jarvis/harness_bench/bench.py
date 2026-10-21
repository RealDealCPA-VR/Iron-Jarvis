"""Running the benchmark: one configuration, its folder, its runs, its report.

Layout under ``<home>/harness-bench/`` (Harness² principle 10: one output
folder per configuration)::

    <runner>-<provider>-<model>-<key>/     one CONFIGURATION
        config.json                        what it is; refuses an incompatible resume
        runs/<task_id>/
            work/                          the agent's fresh copy of the task's repo
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
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .. import __version__
from .catalog import TASKS_DIR, Task, TaskError, load_tasks, materialize, select_tasks, tree_hash
from .failures import INFRASTRUCTURE, Failure, classify_failure
from .grading import Grade, _remove_tree, grade
from .ledger import BudgetLedger, BudgetMismatch
from .report import combined_report, config_report
from .runners import DEFAULT_ROUNDS, RUNNERS, RunOutcome, render_prompt
from .tripwire import HiddenCheckLeak, assert_context_clean

BENCH_DIRNAME = "harness-bench"
MARKER_NAME = "complete.json"
RESULT_NAME = "result.json"
#: Written when a run STARTS; a run folder holding it but no result.json was
#: cut off part-way (the process was killed) and is reported as interrupted.
STARTED_NAME = "started.json"


class BenchRefused(RuntimeError):
    """The benchmark will not run as asked; the message says why in plain words."""


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

    def identity(self) -> dict[str, Any]:
        return {
            "runner": self.runner, "provider": self.provider, "model": self.model,
            "harness": self.harness, "version": self.version,
        }

    @property
    def key(self) -> str:
        return _sha(_canon(self.identity()))

    @property
    def folder_name(self) -> str:
        return f"{self.runner}-{_slug(self.provider)}-{_slug(self.model)}-{self.key[:12]}"

    def fingerprint(self, task: Task) -> str:
        """The run fingerprint: this configuration + the task's content."""
        return _sha(self.key + ":" + task.content_hash)


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


def _snapshot(root: Path, exclude: Path) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    exclude = exclude.resolve()
    for folder, dirs, files in os.walk(root):
        here = Path(folder)
        if here.resolve() == exclude:
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


def _record(
    task: Task, config: Configuration, fingerprint: str, outcome: RunOutcome, grade_: Grade | None,
    *, wall_s: float, started: str, files_outside: list[str], model_calls: int,
    failure: Failure | None = None,
) -> dict[str, Any]:
    failure = failure if failure is not None else classify_failure(
        config.runner, outcome.ok_run, outcome.facts, outcome.error
    )
    passed, grade_reason, unscored_by = _verdict(failure, grade_)
    return {
        "task_id": task.id, "title": task.title, "category": task.category, "tier": task.tier,
        "runner": config.runner, "provider": config.provider, "model": config.model,
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


def _fresh_run_folder(task: Task, run_dir: Path, runs: Path) -> tuple[Path, str]:
    """A clean ``run_dir`` with a fresh copy of the task's repo and the prompt
    the runner will be given. A stale folder (no usable marker) is removed —
    it is the bench's own disposable copy, inside its own ``runs`` folder."""
    if run_dir.exists():
        _remove_tree(run_dir, runs)
    run_dir.mkdir(parents=True)
    _write_json(run_dir / STARTED_NAME, {
        "task_id": task.id, "title": task.title, "category": task.category, "tier": task.tier,
        "started_at": _now(),
    })
    work = materialize(task, run_dir / "work")
    prompt = render_prompt(task, work)
    (run_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    return work, prompt


def _write_marker(run_dir: Path, work: Path, fingerprint: str, config: Configuration, task: Task) -> None:
    _write_json(run_dir / MARKER_NAME, {
        "fingerprint": fingerprint,
        "configuration": config.identity(),
        "task_hash": task.content_hash,
        "work_tree": tree_hash(work),
        "written_at": _now(),
    })


async def run_task(
    task: Task, runner: Any, config: Configuration, cfg_dir: Path, ledger: BudgetLedger,
    *, echo: Callable[[str], None] = lambda _s: None,
) -> dict[str, Any]:
    """Run (or reuse) one task for one configuration and return its record."""
    # Every file step below runs off the event loop (the CLAUDE.md rule): the
    # Jarvis runner's turn lives on this same loop.
    runs = cfg_dir / "runs"
    run_dir = runs / task.id
    fingerprint = config.fingerprint(task)
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
        echo(f"  {task.id}: reused ({word})")
        return {**stored, "reused": True}
    work, prompt = await asyncio.to_thread(_fresh_run_folder, task, run_dir, runs)
    rollout = ledger.begin(task.id, runner.name)
    started = _now()
    try:
        await asyncio.to_thread(assert_context_clean, task, work, prompt)
    except HiddenCheckLeak as exc:
        outcome = RunOutcome(error=str(exc), tokens_in=0, tokens_out=0,
                             cost_metered_usd=0.0, cost_list_price_usd=0.0,
                             facts={"refused": "the agent would have seen the grader"})
        record = _record(task, config, fingerprint, outcome, None, wall_s=0.0, started=started,
                         files_outside=[], model_calls=0)
        ledger.verify_record(record, rollout)
        await asyncio.to_thread(_write_json, run_dir / RESULT_NAME, record)
        echo(f"  {task.id}: refused. {exc}")
        return record
    before = await asyncio.to_thread(_snapshot, cfg_dir, work)
    t0 = time.monotonic()
    echo(f"  {task.id}: running...")
    try:
        outcome = await runner.run(task, work, rollout, timeout_s=task.timeout_s)
    except Exception as exc:  # noqa: BLE001 — a runner bug is a recorded failure, not a crash
        outcome = RunOutcome(error=f"{type(exc).__name__}: {exc}")
        totals = rollout.totals()
        outcome.tokens_in, outcome.tokens_out = totals["tokens_in"], totals["tokens_out"]
        outcome.cost_metered_usd = totals["cost_metered_usd"]
        outcome.cost_list_price_usd = totals["cost_list_price_usd"]
        outcome.usage_source = "ledger"
    wall = time.monotonic() - t0
    after = await asyncio.to_thread(_snapshot, cfg_dir, work)
    outside = changed_outside(before, after)
    g = await asyncio.to_thread(grade, task, work, run_dir / "grade")
    failure = classify_failure(config.runner, outcome.ok_run, outcome.facts, outcome.error)
    record = _record(task, config, fingerprint, outcome, g, wall_s=wall, started=started,
                     files_outside=outside, model_calls=len(rollout.calls), failure=failure)
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
        await asyncio.to_thread(_write_marker, run_dir, work, fingerprint, config, task)
    if failure.infrastructure:
        echo(f"  {task.id}: did not run ({failure.reason})")
    else:
        word = "passed" if record["passed"] else "failed" if record["passed"] is False else "unscored"
        echo(f"  {task.id}: {word} in {wall:.1f} s" + (f" ({outcome.error})" if outcome.error else ""))
    return record


def make_runner(runner: str, *, root: str, provider: str = "", model: str = "",
                rounds: int = DEFAULT_ROUNDS, binary: str | None = None) -> Any:
    if runner not in RUNNERS:
        raise BenchRefused(f"--runner must be one of: {', '.join(RUNNERS)}")
    if runner == "jarvis":
        from ..platform import build_platform

        platform = build_platform(root)
        return RUNNERS["jarvis"](platform, provider=provider, model=model, round_budget=rounds)
    if provider and provider != runner:
        raise BenchRefused(f"--provider does not apply to the {runner} runner (it is always {runner})")
    return RUNNERS[runner](model=model, binary=binary)


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
) -> BenchResult:
    """Run one configuration over the selected tasks."""
    all_tasks = list(tasks) if tasks is not None else await asyncio.to_thread(load_tasks)
    try:
        identity = await asyncio.to_thread(runner.identity)
    except Exception as exc:  # noqa: BLE001 — e.g. the CLI is not installed
        raise BenchRefused(f"could not read the {runner.name} runner's setup ({exc})") from exc
    config = Configuration(runner=runner.name, provider=runner.provider, model=runner.model, harness=identity)
    cfg_dir = bench_root(home) / config.folder_name
    use_seed = await asyncio.to_thread(check_saved_config, cfg_dir, config, seed)
    try:
        selected = select_tasks(all_tasks, names=names, seed=use_seed, max_tasks=max_tasks, tier=tier)
    except TaskError as exc:
        raise BenchRefused(str(exc)) from exc
    result = BenchResult(config_dir=cfg_dir, config=config, planned=[t.id for t in selected], dry_run=dry_run)
    if dry_run:
        result.plan_lines.append(f"{config.folder_name} (seed {use_seed}), {len(selected)} task(s):")
        for t in selected:
            stored = await asyncio.to_thread(reusable_result, cfg_dir / "runs" / t.id, config.fingerprint(t))
            result.plan_lines.append(
                f"  {t.id} [{t.tier}, {t.category}]: " + ("would reuse the stored result" if stored else "would run")
            )
        return result
    await asyncio.to_thread(_save_config, cfg_dir, config, use_seed)
    ledger = BudgetLedger()
    echo(f"{config.folder_name}: {len(selected)} task(s), seed {use_seed}")
    for task in selected:
        result.records.append(await run_task(task, runner, config, cfg_dir, ledger, echo=echo))
    reused = [r["task_id"] for r in result.records if r.get("reused")]
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
            out.append({
                "task_id": run_dir.name, "title": started.get("title", ""),
                "category": started.get("category", ""), "tier": started.get("tier", "core"),
                "interrupted": True,
                "passed": None, "unscored_by": "interrupted", "ok_run": False,
                "error": "interrupted: the run started but was never recorded",
                "started_at": started.get("started_at", ""),
            })
    return out


def write_config_outputs(cfg_dir: Path, reused: Sequence[str] = ()) -> Path:
    """``results.json`` + ``report.md`` for one configuration. ``reused``
    names the tasks THIS invocation took from a stored result: a stored
    record says ``reused: False`` (it was a fresh run when it was written),
    so without it the report would always say "0 reused"."""
    config = _read_json(cfg_dir / "config.json") or {}
    taken = set(reused)
    records = [
        {**r, "reused": True} if r.get("task_id") in taken else r
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
