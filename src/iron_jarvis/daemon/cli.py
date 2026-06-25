"""`ironjarvis` CLI (§9).

In-process commands (`run`, `demo`, `tools`, `sessions`) build the platform
directly — fully offline. `serve` launches the daemon; `status` pings a running
one.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from ..agents.orchestrator import Orchestrator
from ..core.config import write_default_config
from ..core.models import AgentType
from ..platform import build_platform
from .client import DaemonClient

app = typer.Typer(help="Iron Jarvis — local-first AI operating system (slice).")
console = Console()


def _agent_type(name: str) -> AgentType:
    try:
        return AgentType(name)
    except ValueError:
        return AgentType.BUILDER


@app.command()
def init(path: str = typer.Argument(".", help="Project root to initialize.")) -> None:
    """Create .ironjarvis/ and a starter config for a project."""
    platform = build_platform(path)
    cfg = write_default_config(path)
    console.print(f"[green]Initialized[/green] {platform.config.home}")
    console.print(f"  config: {cfg}")


@app.command()
def run(
    task: str = typer.Argument(..., help="The task for the agent."),
    agent: str = typer.Option("builder", help="Agent type."),
    provider: str = typer.Option(None, help="Override provider (default from config)."),
    root: str = typer.Option(".", help="Project root."),
) -> None:
    """Run a single agent session in-process."""
    platform = build_platform(root)
    orch = Orchestrator(platform)
    session = asyncio.run(orch.run(task, _agent_type(agent), provider))
    console.print(f"[bold]Session[/bold] {session.id} -> [cyan]{session.status.value}[/cyan]")
    console.print(f"[bold]Workspace[/bold] {session.workspace_path}")
    console.print(f"[bold]Summary[/bold] {session.summary}")


@app.command()
def demo(root: str = typer.Option(".", help="Project root.")) -> None:
    """Offline end-to-end demo: plan→act→tool→workspace artifact, no network."""
    platform = build_platform(root)
    orch = Orchestrator(platform)
    task = "Create a file summarizing what Iron Jarvis just did."
    session = asyncio.run(orch.run(task, AgentType.BUILDER))

    console.rule("Iron Jarvis - offline demo")
    console.print(f"Session   : {session.id}")
    console.print(f"Provider  : {session.provider} / {session.model}")
    console.print(f"Status    : {session.status.value}")
    console.print(f"Workspace : {session.workspace_path}")

    transcript = orch.transcript(session.id)
    table = Table(title="Tool invocations")
    table.add_column("tool")
    table.add_column("verdict")
    table.add_column("ok")
    table.add_column("output", overflow="fold")
    for t in transcript["tools"]:
        table.add_row(t["tool"], t["verdict"], str(t["ok"]), (t["output"] or "")[:60])
    console.print(table)

    result = Path(session.workspace_path) / "RESULT.md"
    if result.exists():
        console.rule("RESULT.md")
        console.print(result.read_text(encoding="utf-8"))


@app.command()
def tools(root: str = typer.Option(".", help="Project root.")) -> None:
    """List registered tools and their default permission modes."""
    platform = build_platform(root)
    table = Table(title="Tools")
    table.add_column("name")
    table.add_column("permission")
    table.add_column("description", overflow="fold")
    for spec in platform.registry.specs():
        mode = platform.config.permissions.get(spec["name"], "ask")
        table.add_row(spec["name"], mode, spec["description"])
    console.print(table)


@app.command()
def sessions(root: str = typer.Option(".", help="Project root.")) -> None:
    """List past sessions for a project."""
    platform = build_platform(root)
    orch = Orchestrator(platform)
    table = Table(title="Sessions")
    table.add_column("id")
    table.add_column("status")
    table.add_column("task", overflow="fold")
    for s in orch.list_sessions():
        table.add_row(s.id, s.status.value, s.task[:60])
    console.print(table)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8787),
    root: str = typer.Option(".", help="Project root."),
) -> None:
    """Start the daemon (FastAPI) for the dashboard and HTTP clients."""
    import uvicorn

    os.environ["IRONJARVIS_ROOT"] = str(Path(root).resolve())
    from .app import create_app

    uvicorn.run(create_app(os.environ["IRONJARVIS_ROOT"]), host=host, port=port)


@app.command()
def status(url: str = typer.Option("http://127.0.0.1:8787")) -> None:
    """Ping a running daemon."""
    try:
        info = DaemonClient(url).health()
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]daemon unreachable[/red] at {url}: {exc}")
        raise typer.Exit(code=1)
    console.print(info)


if __name__ == "__main__":
    app()
