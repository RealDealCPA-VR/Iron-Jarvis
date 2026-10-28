"""``ironjarvis bench-harness`` — run the harness benchmark, or report on it.

Light at import time (the CLI loads every command module on start): the
benchmark itself is imported inside the commands.
"""

from __future__ import annotations

import asyncio
import sys

import typer

bench_app = typer.Typer(
    help="The harness benchmark: small coding tasks, graded by hidden checks.",
    no_args_is_help=True,
)


def _split(names: str | None) -> list[str] | None:
    if not names:
        return None
    return [n.strip() for n in names.split(",") if n.strip()]


@bench_app.command("run")
def run_command(
    runner: str = typer.Option(
        ..., "--runner",
        help="jarvis, jarvis-team, claude or codex. jarvis-team runs Iron Jarvis with a model team "
        "(--team names its file)."),
    provider: str = typer.Option(
        "", "--provider", help="Jarvis only, and required for it: the provider to answer with."),
    model: str = typer.Option(
        "", "--model",
        help="Required (except for jarvis-team, whose team file names its models): the model to answer "
        "with, passed to the runner explicitly so the record says which one ran (a run without it is "
        "refused; --dry-run may list without it)."),
    effort: str = typer.Option(
        "medium", "--effort",
        help="low, medium or high (default medium), passed to every runner explicitly (Claude Code "
        "--effort, Codex model_reasoning_effort, Jarvis's reasoning level where the model offers one)."),
    repeats: int = typer.Option(
        1, "--repeats",
        help="Run every task this many times (1 to 10, default 1). The report gives each setup its mean "
        "pass rate, the range across repeats, and which tasks passed every time, some of the time or never."),
    tasks: str = typer.Option("", "--tasks", help="Only these tasks, comma-separated."),
    seed: int = typer.Option(None, "--seed", help="Shuffles the task order (same seed, same order)."),
    max_tasks: int = typer.Option(None, "--max-tasks", help="Run at most this many tasks."),
    tier: str = typer.Option(
        "all", "--tier",
        help="core, hard, frontier, team or all (default all), or several tiers joined by commas "
        "(frontier,team: both in one run, in any order)."),
    rounds: int = typer.Option(None, "--rounds", help="Jarvis only: tool rounds per task (default 60)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="List what would run; no model is called."),
    root: str = typer.Option(".", "--root", help="Where the benchmark keeps its state (a scratch folder)."),
    binary: str = typer.Option("", "--binary", help="claude/codex only: the executable to run."),
    dialect: str = typer.Option(
        "auto", "--dialect",
        help="Jarvis only: auto (the model family's own tool dialect, the default), generic "
        "(Iron Jarvis's own tool names), or a dialect to force: claude, gpt, qwen.",
    ),
    team: str = typer.Option(
        "", "--team",
        help="jarvis-team only, and required for it: a TOML file naming the planner ([planner] "
        "provider + model, optional team_first = true to run with \"Hand work to my team first\" on, "
        "optional team = true to run the planner as a locked team lead with the code members as its "
        "workers and the review member as its reviewer) "
        "and the members ([[members]] name, provider, model, roles, optional account). The team is "
        "part of the setup: another team is another results folder."),
    with_alone: bool = typer.Option(
        False, "--with-alone",
        help="jarvis-team only: after the team, run its PLANNER alone (the jarvis runner on the planner's "
        "provider and model, with the same tasks, repeats, seed, effort, rounds and dialect) in the same "
        "home, so one command makes both rows and the report answers whether the team passes as many "
        "tasks while using less of the planner's subscription. They run one after the other, never at "
        "once: both draw on the same account's usage windows."),
    refine: bool = typer.Option(
        False, "--refine",
        help="Jarvis only: improve the harness (Propose-Probe-Compose). A model proposes harness "
        "edits from the runs' evidence, each is probed on the same tasks, the winners are composed, "
        "and a composition that scores at least as well is written as a PROPOSED layer.",
    ),
    k: int = typer.Option(2, "--k", help="--refine: how many candidate harnesses (1 to 8)."),
    mode: str = typer.Option(
        "parallel", "--mode",
        help="--refine: parallel (k candidates proposed side by side from one round of evidence) or "
        "sequential (k rounds, each building on the best so far). Both propose and try one candidate "
        "at a time.",
    ),
) -> None:
    """Run one configuration over the tasks and write JSON + report.md
    (with --refine: run the harness improvement loop instead)."""
    from .bench import (
        BenchRefused,
        check_effort,
        check_repeats,
        make_runner,
        require_model,
        resolve_bench_home,
        run_bench,
    )
    from .ledger import BudgetMismatch
    from .runners import DEFAULT_ROUNDS

    if getattr(sys, "frozen", False):
        # The installed app carries the tasks' data files but not their Python
        # sources (PyInstaller collects data without .py), and has no Python
        # to grade with: a run there would grade broken copies.
        typer.echo(
            "The harness benchmark runs from a source checkout of Iron Jarvis "
            "(uv run ironjarvis bench-harness run ...), not from the installed app."
        )
        raise typer.Exit(code=2)
    if runner not in ("jarvis", "jarvis-team") and rounds is not None:
        typer.echo("--rounds applies to the jarvis and jarvis-team runners only.")
        raise typer.Exit(code=2)
    if refine and runner != "jarvis":
        typer.echo("--refine improves Iron Jarvis's own harness: run it with --runner jarvis.")
        raise typer.Exit(code=2)
    if team and runner != "jarvis-team":
        typer.echo("--team applies to --runner jarvis-team only.")
        raise typer.Exit(code=2)
    if with_alone and runner != "jarvis-team":
        typer.echo("--with-alone applies to --runner jarvis-team only.")
        raise typer.Exit(code=2)
    try:
        # v1.383.0: refused BEFORE any task starts (and before the Jarvis
        # runner builds its platform), in one plain sentence each.
        effort = check_effort(effort)
        repeats = check_repeats(repeats)
        if not dry_run and runner != "jarvis-team":
            # (The team runner's planner and members name their own models,
            # checked when its file is read.)
            require_model(runner, provider, model)
    except BenchRefused as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=2) from exc
    if refine and repeats != 1:
        typer.echo("--repeats does not apply to --refine (each harness runs each task once there).")
        raise typer.Exit(code=2)
    if refine:
        _refine(
            runner=runner, provider=provider, model=model, tasks=tasks, seed=seed,
            max_tasks=max_tasks, tier=tier, rounds=rounds if rounds is not None else DEFAULT_ROUNDS,
            dry_run=dry_run, root=root, dialect=dialect, k=k, mode=mode, effort=effort,
        )
        return
    try:
        home = resolve_bench_home(root)
        bench_runner = make_runner(
            runner, root=root, provider=provider, model=model,
            rounds=rounds if rounds is not None else DEFAULT_ROUNDS, binary=binary or None,
            dialect=dialect, effort=effort, team=team,
        )
    except (BenchRefused, ValueError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=2) from exc
    setups = [bench_runner]
    if with_alone:
        # v1.396.0: the planner ALONE, on the team runner's own platform and
        # settings — the row the plan's question compares the team with. It
        # runs AFTER the team (the team's members are gone by then).
        from .runners import JarvisRunner

        setups.append(JarvisRunner(
            bench_runner.platform, provider=bench_runner.provider, model=bench_runner.model,
            round_budget=bench_runner.round_budget, dialect=bench_runner.dialect, effort=bench_runner.effort,
        ))
    unbalanced = False
    for setup in setups:
        try:
            result = asyncio.run(run_bench(
                runner=setup, home=home, names=_split(tasks), seed=seed,
                max_tasks=max_tasks, dry_run=dry_run, echo=typer.echo, tier=tier, repeats=repeats,
            ))
        except (BenchRefused, ValueError) as exc:
            typer.echo(str(exc))
            raise typer.Exit(code=2) from exc
        except BudgetMismatch as exc:
            # The results were written; the next setup still runs.
            typer.echo(f"The results were written, but {exc}")
            unbalanced = True
            continue
        _say(result, dry_run=dry_run, repeats=repeats)
    if dry_run:
        typer.echo("Nothing was run (dry run).")
    if unbalanced:
        raise typer.Exit(code=1)


def _say(result, *, dry_run: bool, repeats: int) -> None:
    """One setup's lines: its plan (a dry run) or its pass count."""
    if dry_run:
        for line in result.plan_lines:
            typer.echo(line)
        return
    passed = sum(1 for r in result.records if r.get("passed") is True)
    scored = sum(1 for r in result.records if r.get("passed") is not None)
    unscored = len(result.records) - scored
    if repeats > 1:
        from .report import summarize

        s = summarize(result.records)
        rng = s["range"]
        shy = repeats - s["scored_repeats"]
        typer.echo(
            f"{passed} of {scored} scored runs passed across {repeats} repeats"
            + (f" ({shy} had no scored run)" if shy > 0 else "")
            + (f"; {unscored} unscored" if unscored else "")
            + (f"; mean pass rate {s['mean_pass_rate'] * 100:.0f}%, range {rng[0] * 100:.0f}%–{rng[1] * 100:.0f}%"
               if rng else "")
            + f". Results: {result.config_dir}"
        )
    else:
        typer.echo(
            f"{passed} of {scored} scored task(s) passed"
            + (f"; {unscored} unscored" if unscored else "")
            + f". Results: {result.config_dir}"
        )
    if getattr(result.config, "runner", "") == "jarvis-team":
        # v1.400.0: the first number to look for in a team setup.
        from .team_report import handed_line

        handed = handed_line(result.records)
        if handed:
            typer.echo(handed)
    if result.report_path is not None:
        typer.echo(f"Report across every setup: {result.report_path}")


def _refine(*, runner: str, provider: str, model: str, tasks: str, seed: int | None,
            max_tasks: int | None, tier: str, rounds: int, dry_run: bool, root: str,
            dialect: str, k: int, mode: str, effort: str = "") -> None:
    from .bench import BenchRefused, resolve_bench_home
    from .ledger import BudgetMismatch
    from .refine import SMALL_SAMPLE, RefineSettings, run_refine
    from .tripwire import HiddenCheckLeak

    if runner != "jarvis":
        typer.echo("--refine improves Iron Jarvis's own harness: run it with --runner jarvis.")
        raise typer.Exit(code=2)
    settings = RefineSettings(
        provider=provider, model=model, dialect=dialect or "auto", rounds=rounds, tier=tier,
        tasks=tuple(_split(tasks) or ()), max_tasks=max_tasks, k=k, mode=mode, effort=effort,
    )
    try:
        home = resolve_bench_home(root)
        from ..daemon.chat_turn import turn_harness
        from ..platform import build_platform

        turn_harness(settings.dialect)
        platform = build_platform(root)
        result = asyncio.run(run_refine(
            platform=platform, home=home, settings=settings, seed=seed, dry_run=dry_run, echo=typer.echo,
        ))
    except (BenchRefused, ValueError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=2) from exc
    except BudgetMismatch as exc:
        typer.echo(f"The results were written, but {exc}")
        raise typer.Exit(code=1) from exc
    except HiddenCheckLeak as exc:
        typer.echo(f"Stopped: {exc}.")
        raise typer.Exit(code=1) from exc
    if dry_run:
        for line in result.plan_lines:
            typer.echo(line)
        typer.echo("Nothing was run (dry run).")
        return
    for c in result.candidates:
        typer.echo(
            f"{c.get('label')}: {c.get('status')}"
            + (f" ({c.get('reason')})" if c.get("reason") else "")
            + (f", changed {', '.join(c.get('changed') or [])}" if c.get("changed") else "")
        )
    if result.adopted:
        a = result.adopted
        typer.echo(
            f"Adopted as a proposed layer ({'better than' if a['better'] else 'the same as'} the current "
            f"harness: {a['passed']} vs {a['current_passed']} on {a['compared']} task(s)). {a['how_to_use']}"
        )
        typer.echo(SMALL_SAMPLE)
    else:
        typer.echo("Nothing was adopted: the current harness stays.")
    if result.ledger is not None:
        t = result.ledger.grand_totals()
        typer.echo(
            f"Ledger: {t['rollouts']} rollouts, {t['model_calls']} model calls, {t['tokens_in']} tokens in, "
            f"{t['tokens_out']} tokens out; every stage adds up."
        )
    typer.echo(f"Report: {result.report_path}")


@bench_app.command("adopt")
def adopt_command(
    source: str = typer.Option(..., "--from", help="A refine folder (its adopted layer) or a folder holding .ironjarvis/harness."),
    folder: str = typer.Option(..., "--folder", help="The project folder to write the harness files into."),
) -> None:
    """Copy an adopted harness layer into a project folder (your decision; a
    file the folder already has and that differs is never overwritten)."""
    from pathlib import Path

    from .bench import BenchRefused
    from .refine import adopt_into

    try:
        written = adopt_into(Path(source), Path(folder))
    except BenchRefused as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=2) from exc
    if written:
        typer.echo("Written into " + str(Path(folder) / ".ironjarvis" / "harness") + ": " + ", ".join(written))
    else:
        typer.echo("The folder already had these files; nothing needed copying.")


@bench_app.command("report")
def report_command(
    root: str = typer.Option(".", "--root", help="Where the benchmark keeps its state."),
) -> None:
    """Write one combined report across every configuration found."""
    from .bench import BenchRefused, resolve_bench_home, write_combined_report

    try:
        home = resolve_bench_home(root)
    except BenchRefused as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=2) from exc
    path = write_combined_report(home)
    typer.echo(path.read_text(encoding="utf-8"))
    typer.echo(f"Written to {path}")
