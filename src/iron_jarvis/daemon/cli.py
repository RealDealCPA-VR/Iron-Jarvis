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
from ..tools.permissions import headless_ask_resolver
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
    # Same headless policy as the daemon: auto-approve low-risk orchestration
    # (delegate) so a `--agent supervisor` run can delegate; shell stays denied.
    platform = build_platform(root, ask_resolver=headless_ask_resolver())
    orch = Orchestrator(platform)
    session = asyncio.run(orch.run(task, _agent_type(agent), provider))
    console.print(f"[bold]Session[/bold] {session.id} -> [cyan]{session.status.value}[/cyan]")
    console.print(f"[bold]Workspace[/bold] {session.workspace_path}")
    console.print(f"[bold]Summary[/bold] {session.summary}")


@app.command()
def demo(root: str = typer.Option(".", help="Project root.")) -> None:
    """Offline end-to-end demo: plan->act->tool->workspace artifact, no network."""
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

    # Showcase the wider platform, fully offline.
    content = result.read_text(encoding="utf-8") if result.exists() else session.summary
    art = platform.artifacts.save("demo-report", content, session_id=session.id)
    platform.memory.write("project", "last-demo", f"Demo {session.id}: {session.summary}")
    m = platform.observability.metrics()
    console.rule("Platform subsystems")
    console.print(f"Artifact   : {art.name} v{art.version} ({art.size} bytes)")
    console.print(f"Skills     : {[s.name for s in platform.skills.list()]}")
    console.print(f"Tools      : {len(platform.registry.names())} registered (incl. sandboxed shell, memory, skills, delegate)")
    console.print(f"Evaluation : sessions_evaluated={m['sessions_evaluated']}, avg_completion={m['avg_completion']}")


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
    git_native: bool = typer.Option(
        False,
        "--git-native/--no-git-native",
        help="Run sessions on isolated git worktree branches with review/approve (§27).",
    ),
) -> None:
    """Start the daemon (FastAPI) for the dashboard and HTTP clients."""
    import uvicorn

    os.environ["IRONJARVIS_ROOT"] = str(Path(root).resolve())
    if git_native:
        os.environ["IRONJARVIS_GIT_NATIVE"] = "1"
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


@app.command()
def metrics(root: str = typer.Option(".", help="Project root.")) -> None:
    """Show evaluation + observability metrics (§29/§30)."""
    platform = build_platform(root)
    console.print(platform.observability.metrics())


@app.command()
def evaluate(session_id: str, root: str = typer.Option(".", help="Project root.")) -> None:
    """Show (or compute) the evaluation for a session (§29)."""
    platform = build_platform(root)
    ev = platform.evaluator.latest(session_id) or platform.evaluator.evaluate(session_id)
    console.print(ev.model_dump())


@app.command("memory-write")
def memory_write(
    key: str,
    text: str,
    layer: str = typer.Option("project"),
    root: str = typer.Option("."),
) -> None:
    """Write a memory entry (§21)."""
    platform = build_platform(root)
    rec = platform.memory.write(layer, key, text)
    console.print(f"[green]wrote[/green] {layer}/{key} ({rec.id})")


@app.command("memory-search")
def memory_search(
    query: str, k: int = typer.Option(5), root: str = typer.Option(".")
) -> None:
    """Semantic search over memory (§22)."""
    platform = build_platform(root)
    table = Table(title="Memory search")
    table.add_column("score")
    table.add_column("layer")
    table.add_column("key")
    table.add_column("text", overflow="fold")
    for rec, score in platform.memory.search(query, k=k):
        table.add_row(f"{score:.3f}", rec.layer, rec.key, rec.text[:80])
    console.print(table)


@app.command()
def skills(root: str = typer.Option(".", help="Project root.")) -> None:
    """List available skills (§23)."""
    platform = build_platform(root)
    table = Table(title="Skills")
    table.add_column("name")
    table.add_column("description", overflow="fold")
    for s in platform.skills.list():
        table.add_row(s.name, s.description)
    console.print(table)


@app.command()
def workflow(file: str, root: str = typer.Option(".", help="Project root.")) -> None:
    """Run a workflow defined in a TOML file (§24)."""
    from ..workflows.engine import WorkflowEngine, load_workflow_toml

    platform = build_platform(root)
    wf = load_workflow_toml(file)
    rec = asyncio.run(WorkflowEngine(platform).run(wf))
    console.print(f"[bold]Workflow[/bold] {rec.workflow_name} -> [cyan]{rec.status}[/cyan]")
    console.print(f"sessions: {rec.session_ids_json}")


@app.command("secret-set")
def secret_set(
    name: str, value: str, kind: str = typer.Option("generic"), root: str = typer.Option(".")
) -> None:
    """Store an encrypted shared secret (API key / OAuth / token)."""
    platform = build_platform(root)
    platform.secrets.set(name, value, kind=kind)
    console.print(f"[green]stored secret[/green] {name} ({kind})")


@app.command()
def secrets(root: str = typer.Option(".", help="Project root.")) -> None:
    """List secret names + kinds (never values)."""
    platform = build_platform(root)
    table = Table(title="Secrets")
    table.add_column("name")
    table.add_column("kind")
    table.add_column("description", overflow="fold")
    for s in platform.secrets.list():
        table.add_row(s["name"], s["kind"], s.get("description", ""))
    console.print(table)


@app.command()
def integrations(root: str = typer.Option(".", help="Project root.")) -> None:
    """List available integrations + status."""
    platform = build_platform(root)
    table = Table(title="Integrations")
    table.add_column("id")
    table.add_column("kind")
    table.add_column("enabled")
    for i in platform.integrations.list_status():
        table.add_row(i.get("id", ""), i.get("kind", ""), str(i.get("enabled", False)))
    console.print(table)


@app.command("file-search")
def file_search(
    query: str,
    mode: str = typer.Option("content"),
    limit: int = typer.Option(50),
    root: str = typer.Option("."),
) -> None:
    """Search files across configured roots (name/content/semantic)."""
    platform = build_platform(root)
    for r in platform.filesearch.search(query, mode=mode, limit=limit):
        loc = f"{r['path']}:{r.get('line', '')}".rstrip(":")
        console.print(loc + (f"  {r.get('text', '')}" if r.get("text") else ""))


@app.command("ltm-search")
def ltm_search(
    query: str,
    source: str = typer.Option(None),
    k: int = typer.Option(5),
    root: str = typer.Option("."),
) -> None:
    """Search long-term memory (Obsidian / Notion / brain)."""
    platform = build_platform(root)
    for r in platform.ltm.search(query, k=k, source=source):
        console.print(f"[{r['source']}] {r['title']}: {r['snippet'][:80]}")


@app.command("ltm-append")
def ltm_append(
    title: str, content: str, source: str = typer.Option(None), root: str = typer.Option(".")
) -> None:
    """Append a note to long-term memory."""
    platform = build_platform(root)
    src = source or platform.ltm.default_source()
    ref = platform.ltm.append(title, content, source=src)
    console.print(f"[green]appended[/green] to {src}: {ref}")


@app.command()
def notify(
    message: str, channel: str = typer.Option(None), root: str = typer.Option(".")
) -> None:
    """Send a message through communication channels."""
    platform = build_platform(root)
    result = platform.notifier.notify(message, [channel] if channel else None)
    console.print(result)


@app.command("schedule-add")
def schedule_add(
    name: str,
    cron: str,
    kind: str = typer.Option("workflow"),
    root: str = typer.Option("."),
) -> None:
    """Register a cron-scheduled task."""
    platform = build_platform(root)
    rec = platform.scheduler.add_task(name, cron, kind=kind)
    console.print(f"[green]scheduled[/green] {name} ({cron}); next: {rec.next_run}")


@app.command()
def schedules(root: str = typer.Option(".", help="Project root.")) -> None:
    """List scheduled tasks."""
    platform = build_platform(root)
    table = Table(title="Scheduled tasks")
    table.add_column("name")
    table.add_column("cron")
    table.add_column("kind")
    table.add_column("next_run")
    for t in platform.scheduler.list():
        table.add_row(t.name, t.cron, t.kind, str(t.next_run))
    console.print(table)


@app.command("schedule-run")
def schedule_run(name: str, root: str = typer.Option(".")) -> None:
    """Run a scheduled task now."""
    platform = build_platform(root)
    asyncio.run(platform.scheduler.run_now(name))
    console.print(f"[green]ran[/green] {name}")


@app.command()
def agents(root: str = typer.Option(".", help="Project root.")) -> None:
    """List built-in and dynamic agents."""
    platform = build_platform(root)
    from ..agents.types import _DEFINITIONS

    table = Table(title="Agents")
    table.add_column("name")
    table.add_column("kind")
    for t in _DEFINITIONS:
        table.add_row(t.value, "built-in")
    for r in platform.agents_registry.list():
        table.add_row(r.name, "dynamic")
    console.print(table)


@app.command("create-agent")
def create_agent(
    name: str,
    prompt: str = typer.Option(..., help="System prompt."),
    tool: list[str] = typer.Option(None, help="Tool name (repeatable)."),
    root: str = typer.Option("."),
) -> None:
    """Create a new dynamic agent (agents that add agents)."""
    platform = build_platform(root)
    tools = tool or ["read_file", "write_file", "list_files"]
    platform.agents_registry.register(name, prompt, tools)
    console.print(f"[green]created agent[/green] {name} with tools {tools}")


if __name__ == "__main__":
    app()
