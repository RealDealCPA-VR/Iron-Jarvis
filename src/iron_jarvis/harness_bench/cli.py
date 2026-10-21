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
    runner: str = typer.Option(..., "--runner", help="jarvis, claude or codex."),
    provider: str = typer.Option("", "--provider", help="Jarvis only: the provider to answer with."),
    model: str = typer.Option("", "--model", help="The model to answer with."),
    tasks: str = typer.Option("", "--tasks", help="Only these tasks, comma-separated."),
    seed: int = typer.Option(None, "--seed", help="Shuffles the task order (same seed, same order)."),
    max_tasks: int = typer.Option(None, "--max-tasks", help="Run at most this many tasks."),
    tier: str = typer.Option("all", "--tier", help="core, hard or all (default all)."),
    rounds: int = typer.Option(None, "--rounds", help="Jarvis only: tool rounds per task (default 60)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="List what would run; no model is called."),
    root: str = typer.Option(".", "--root", help="Where the benchmark keeps its state (a scratch folder)."),
    binary: str = typer.Option("", "--binary", help="claude/codex only: the executable to run."),
    dialect: str = typer.Option(
        "auto", "--dialect",
        help="Jarvis only: auto (the model family's own tool dialect, the default), generic "
        "(Iron Jarvis's own tool names), or a dialect to force: claude, gpt, qwen.",
    ),
) -> None:
    """Run one configuration over the tasks and write JSON + report.md."""
    from .bench import BenchRefused, make_runner, resolve_bench_home, run_bench
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
    if runner != "jarvis" and rounds is not None:
        typer.echo("--rounds applies to the jarvis runner only.")
        raise typer.Exit(code=2)
    try:
        home = resolve_bench_home(root)
        bench_runner = make_runner(
            runner, root=root, provider=provider, model=model,
            rounds=rounds if rounds is not None else DEFAULT_ROUNDS, binary=binary or None,
            dialect=dialect,
        )
        result = asyncio.run(run_bench(
            runner=bench_runner, home=home, names=_split(tasks), seed=seed,
            max_tasks=max_tasks, dry_run=dry_run, echo=typer.echo, tier=tier,
        ))
    except (BenchRefused, ValueError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=2) from exc
    except BudgetMismatch as exc:
        typer.echo(f"The results were written, but {exc}")
        raise typer.Exit(code=1) from exc
    if dry_run:
        for line in result.plan_lines:
            typer.echo(line)
        typer.echo("Nothing was run (dry run).")
        return
    passed = sum(1 for r in result.records if r.get("passed") is True)
    scored = sum(1 for r in result.records if r.get("passed") is not None)
    unscored = len(result.records) - scored
    typer.echo(
        f"{passed} of {scored} scored task(s) passed"
        + (f"; {unscored} unscored" if unscored else "")
        + f". Results: {result.config_dir}"
    )
    if result.report_path is not None:
        typer.echo(f"Report across every setup: {result.report_path}")


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
