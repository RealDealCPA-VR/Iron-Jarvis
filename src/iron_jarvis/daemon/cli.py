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


def _port_in_use(host: str, port: int) -> bool:
    """True if something is already listening on host:port (a running daemon)."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        try:
            return s.connect_ex((host, port)) == 0
        except OSError:
            return False


def _home_for(root: str) -> Path:
    """The .ironjarvis state home for a root — WITHOUT building the platform, so
    recovery commands work even when the platform/config can't load."""
    return Path(root).resolve() / ".ironjarvis"


def _source_repo_root() -> "Path | None":
    """Locate the git checkout Iron Jarvis runs from, WITHOUT importing the
    platform — so ``rollback`` works even when a bad update broke the daemon."""
    import subprocess

    pkg_dir = Path(__file__).resolve().parents[2]  # .../src (or the package parent)
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(pkg_dir), capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip())
    except Exception:  # noqa: BLE001
        pass
    return None


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


@app.command("self-dev")
def self_dev(
    task: str = typer.Argument(..., help="What to fix or improve in Iron Jarvis itself."),
    enable: bool = typer.Option(
        False, "--enable", help="Opt in: allow this run to edit Iron Jarvis's own source."
    ),
    provider: str = typer.Option(None, help="Override provider (default from config)."),
    root: str = typer.Option(".", help="Project root (for config/state)."),
) -> None:
    """Run a Maintainer that edits Iron Jarvis's OWN source — review-gated.

    Self-development is OFF by default. Pass ``--enable`` (or set
    ``self_dev_enabled`` in config) to allow it. Changes land on a git worktree
    branch and are NOT merged — review the diff (dashboard/API) before approving.
    """
    platform = build_platform(root, ask_resolver=headless_ask_resolver())
    if enable:
        platform.config.self_dev_enabled = True

    from ..core.self_dev import self_dev_status

    st = self_dev_status(platform.config)
    if not st["available"]:
        console.print(f"[red]self-dev unavailable[/red]: {st['reason']}")
        raise typer.Exit(code=1)
    console.print(f"[cyan]Self-dev repo[/cyan] {st['repo_root']}")

    orch = Orchestrator(platform)

    async def _go():
        session = await orch.create_session(task, provider=provider, self_dev=True)
        return await orch.run_session(session.id)

    session = asyncio.run(_go())
    console.print(
        f"[bold]Maintainer session[/bold] {session.id} -> [cyan]{session.status.value}[/cyan]"
    )
    console.print(f"[bold]Worktree[/bold] {session.workspace_path}")
    review = orch.get_review(session.id)
    if review is not None:
        console.print(f"[bold]Review[/bold] branch={review.branch} risk={review.risk}")
        console.print(f"changed: {review.changed_files}")
        console.print("[yellow]Not merged[/yellow] — approve via the dashboard/API to land it.")
    console.print(f"[bold]Summary[/bold] {session.summary}")


@app.command("prune-worktrees")
def prune_worktrees(
    all: bool = typer.Option(False, "--all", help="Prune every orphan, incl. completed pending-review."),
    root: str = typer.Option(".", help="Project root."),
) -> None:
    """Garbage-collect session worktrees orphaned by a restart (failed/missing by default)."""
    platform = build_platform(root, ask_resolver=headless_ask_resolver())
    orch = Orchestrator(platform)
    pruned = orch.prune_orphan_worktrees(include_completed=all)
    console.print(f"[green]pruned[/green] {len(pruned)} orphan worktree(s): {pruned}")


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

    # Pre-flight: if something is already listening on host:port, don't run the
    # whole lifespan startup (auto-backup, rehydration) only to die on bind with a
    # raw WinError 10048. Tell the user plainly and exit cleanly.
    if _port_in_use(host, port):
        console.print(
            f"[yellow]Iron Jarvis is already running[/yellow] on http://{host}:{port} "
            "— not starting a second instance."
        )
        raise typer.Exit(code=0)

    resolved_root = str(Path(root).resolve())
    os.environ["IRONJARVIS_ROOT"] = resolved_root
    # Make the state location obvious: starting `serve` from a different directory
    # uses a DIFFERENT .ironjarvis (DB/secrets/sessions), which otherwise looks like
    # "everything disappeared". Print where state actually lives.
    home = Path(resolved_root) / ".ironjarvis"
    fresh = not home.exists() or not any(home.iterdir())
    console.print(f"[cyan]State home[/cyan] {home}" + ("  [dim](new/empty)[/dim]" if fresh else ""))
    if git_native:
        os.environ["IRONJARVIS_GIT_NATIVE"] = "1"
    from .app import create_app

    uvicorn.run(create_app(resolved_root), host=host, port=port)


@app.command()
def cancel(
    session_id: str, url: str = typer.Option("http://127.0.0.1:8787")
) -> None:
    """Stop a running session on a daemon."""
    try:
        info = DaemonClient(url).cancel(session_id)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]cancel failed[/red]: {exc}")
        raise typer.Exit(code=1)
    console.print(info)


@app.command()
def rerun(
    session_id: str, url: str = typer.Option("http://127.0.0.1:8787")
) -> None:
    """Re-run a past session with the same inputs (on a daemon)."""
    try:
        info = DaemonClient(url).rerun(session_id)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]rerun failed[/red]: {exc}")
        raise typer.Exit(code=1)
    console.print(info)


@app.command("delete-session")
def delete_session(
    session_id: str, url: str = typer.Option("http://127.0.0.1:8787")
) -> None:
    """Delete a session from a daemon."""
    try:
        info = DaemonClient(url).delete(session_id)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]delete failed[/red]: {exc}")
        raise typer.Exit(code=1)
    console.print(info)


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


@app.command()
def feedback(
    session_id: str,
    rating: str = typer.Argument(..., help="up | down | neutral"),
    comment: str = typer.Option("", help="What to adjust."),
    root: str = typer.Option("."),
) -> None:
    """Give feedback on a session — it becomes a lesson the agent applies next time."""
    platform = build_platform(root)
    platform.learning.record_feedback(session_id, rating, comment)
    console.print(f"[green]thanks[/green] — recorded {rating} feedback; I'll learn from it.")


@app.command()
def lessons(root: str = typer.Option(".", help="Project root.")) -> None:
    """Show what Iron Jarvis has learned about working with you."""
    platform = build_platform(root)
    table = Table(title="What I've learned")
    table.add_column("source")
    table.add_column("wt")
    table.add_column("lesson", overflow="fold")
    for lr in platform.learning.lessons(scope=None, limit=30):
        table.add_row(lr.source, str(lr.weight), lr.text)
    console.print(table)


@app.command("doc-read")
def doc_read(path: str, root: str = typer.Option(".")) -> None:
    """Extract text from any document (PDF, Word, Excel, PowerPoint, CSV, MD, ...)."""
    from ..documents import extract_text

    console.print(extract_text(path)[:4000])


@app.command()
def doctor() -> None:
    """Check your environment is ready to run Iron Jarvis."""
    from ..onboarding import doctor as run_doctor

    result = run_doctor()
    table = Table(title="Iron Jarvis doctor")
    table.add_column("")
    table.add_column("check")
    table.add_column("detail", overflow="fold")
    for c in result["checks"]:
        mark = (
            "[green]OK[/green]"
            if c["ok"]
            else ("[yellow]warn[/yellow]" if c.get("level") == "recommended" else "[red]FAIL[/red]")
        )
        detail = c["detail"] + (f"  ->  {c['fix']}" if not c["ok"] else "")
        table.add_row(mark, c["name"], detail)
    console.print(table)
    console.print(
        "[green]All set![/green]" if result["ok"] else "[yellow]Some required checks failed.[/yellow]"
    )


@app.command()
def connect(provider: str, key: str, root: str = typer.Option(".")) -> None:
    """Connect an LLM provider with an API key (stored encrypted in the vault)."""
    platform = build_platform(root)
    platform.connections.set_api_key(provider, key)
    console.print(f"[green]connected[/green] {provider} — try a session with --provider {provider}")


@app.command()
def up(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8787),
    root: str = typer.Option("."),
    open_browser: bool = typer.Option(True, "--open/--no-open"),
) -> None:
    """Start the daemon AND the dashboard with one command."""
    import shutil
    import subprocess
    import webbrowser

    import uvicorn

    from .app import create_app

    procs = []
    dash = Path(root) / "dashboard"
    if (dash / ".next").exists() and shutil.which("pnpm"):
        console.print("[cyan]Dashboard[/cyan] -> http://localhost:3000")
        procs.append(subprocess.Popen("pnpm start", cwd=str(dash), shell=True))
        url = "http://localhost:3000"
    else:
        console.print(
            "[yellow]Dashboard not built[/yellow] (cd dashboard && pnpm install && pnpm build). Serving the API only."
        )
        url = f"http://{host}:{port}"
    console.print(f"[cyan]Daemon[/cyan]    -> http://{host}:{port}")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    os.environ["IRONJARVIS_ROOT"] = str(Path(root).resolve())
    try:
        uvicorn.run(create_app(os.environ["IRONJARVIS_ROOT"]), host=host, port=port)
    finally:
        for pr in procs:
            try:
                pr.terminate()
            except Exception:
                pass


@app.command()
def backup(
    out: str = typer.Option(None, help="Output .tar.gz (default ./ironjarvis-backup.tar.gz)."),
    include_keys: bool = typer.Option(
        False, "--include-keys", help="Include the encryption keys (DANGEROUS)."
    ),
    root: str = typer.Option("."),
) -> None:
    """Back up the .ironjarvis state (DB + memory + config) to a tar.gz.

    Does NOT build the platform, so it still works when the app can't boot (a
    corrupt DB just makes the snapshot fall back to a raw file copy)."""
    from ..core.db import make_engine
    from ..maintenance import create_backup

    home = _home_for(root)
    if not home.exists():
        console.print(f"[red]no state to back up[/red] at {home}")
        raise typer.Exit(code=1)
    engine = None
    db_path = home / "ironjarvis.db"
    if db_path.exists():
        try:
            engine = make_engine(db_path)
        except Exception:  # noqa: BLE001 — corrupt DB → snapshot falls back to file copy
            engine = None
    out_path = Path(out) if out else Path("ironjarvis-backup.tar.gz")
    out_path, n = create_backup(home, out_path, engine=engine, include_keys=include_keys)
    note = "" if include_keys else " (encryption keys excluded)"
    console.print(f"[green]backed up[/green] {n} files -> {out_path}{note}")


@app.command()
def restore(
    file: str,
    force: bool = typer.Option(False, "--force", help="Overwrite existing state."),
    root: str = typer.Option("."),
) -> None:
    """Restore .ironjarvis state from a backup tar.gz.

    Does NOT build the platform (which would open the possibly-corrupt DB and
    crash) — it extracts the archive over the home directly, so it works exactly
    when it's most needed: recovering a DB too corrupt to boot."""
    import tarfile

    home = _home_for(root)
    if home.exists() and any(home.iterdir()) and not force:
        console.print("[red]refusing[/red]: .ironjarvis is not empty; pass --force")
        raise typer.Exit(code=1)
    dest = home.parent
    dest.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(file, "r:gz") as tar:
            # filter="data" (Python 3.12+) rejects absolute paths, '..' traversal,
            # and symlink/hardlink escapes.
            tar.extractall(path=dest, filter="data")
    except (tarfile.TarError, OSError) as exc:
        console.print(f"[red]restore failed[/red]: cannot read backup {file}: {exc}")
        raise typer.Exit(code=1)
    console.print(f"[green]restored[/green] from {file} into {home}")


@app.command("prune-events")
def prune_events_cmd(
    older_than_days: int = typer.Option(30, "--older-than-days"),
    vacuum: bool = typer.Option(False, "--vacuum"),
    root: str = typer.Option("."),
) -> None:
    """Delete persisted events older than N days (retention)."""
    from ..core.db import prune_events

    platform = build_platform(root)
    n = prune_events(platform.engine, older_than_days, vacuum=vacuum)
    extra = " + VACUUM" if vacuum else ""
    console.print(f"[green]pruned[/green] {n} event(s) older than {older_than_days}d{extra}")


@app.command("rotate-keys")
def rotate_keys(root: str = typer.Option(".")) -> None:
    """Re-encrypt the secrets vault + browser vault under fresh keys."""
    platform = build_platform(root)
    s = platform.secrets.rotate_key()
    v = platform.vault.rotate_key()
    console.print(
        f"[green]rotated[/green] {s} secret(s) and {v} browser session(s); "
        "old keys kept as *.bak"
    )


@app.command()
def migrate(root: str = typer.Option(".")) -> None:
    """Apply pending schema migrations (additive changes self-heal automatically)."""
    from ..core.db import get_schema_version, init_db

    platform = build_platform(root)
    init_db(platform.engine)
    console.print(
        f"[green]schema up to date[/green] at version {get_schema_version(platform.engine)}"
    )


@app.command("update-check")
def update_check(root: str = typer.Option(".", help="Project root (for config).")) -> None:
    """Check whether repo updates are available (git fetch + count behind upstream)."""
    from ..core.self_dev import iron_jarvis_repo_root
    from ..core.updates import update_status

    platform = build_platform(root)
    repo = iron_jarvis_repo_root(platform.config)
    if repo is None:
        console.print(
            "[yellow]not a source checkout[/yellow] — Iron Jarvis git repo not found "
            "(running from an installed package?)."
        )
        return
    st = update_status(repo)
    if st.get("available"):
        console.print(
            f"[green]update available[/green]: {st.get('behind')} commit(s) behind "
            f"on [cyan]{st.get('branch')}[/cyan]"
        )
    else:
        console.print(f"[cyan]{st.get('reason', 'up to date')}[/cyan]")
    console.print(
        f"current={st.get('current')}  remote={st.get('remote')}  clean={st.get('clean')}"
    )


@app.command("self-update")
def self_update(
    root: str = typer.Option(".", help="Project root (for config)."),
    no_dashboard: bool = typer.Option(
        False, "--no-dashboard", help="Skip the dashboard (pnpm) build."
    ),
) -> None:
    """Pull the latest Iron Jarvis source and rebuild (git pull + uv sync + pnpm build).

    Refuses if the working tree is dirty. This updates the FILES on disk only —
    you must restart the daemon (and dashboard) afterwards to load the new code.
    """
    from ..core.self_dev import iron_jarvis_repo_root
    from ..core.updates import apply_update

    platform = build_platform(root)
    repo = iron_jarvis_repo_root(platform.config)
    if repo is None:
        console.print(
            "[red]not a source checkout[/red] — can't self-update an installed package."
        )
        raise typer.Exit(code=1)

    console.print(f"[cyan]Updating[/cyan] {repo}")
    result = apply_update(repo, build_dashboard=not no_dashboard)
    for entry in result.get("log", []):
        mark = "[green]OK[/green]" if entry.get("ok") else "[red]FAIL[/red]"
        console.print(f"  {mark} {entry.get('step')} (rc={entry.get('returncode')})")
        err = (entry.get("stderr") or "").strip()
        if err:
            console.print(f"      [dim]{err[:400]}[/dim]")

    if result.get("ok"):
        console.print(f"[green]update complete[/green] — {result.get('reason')}")
    else:
        console.print(f"[red]update failed[/red] — {result.get('reason')}")
    if result.get("restart_required"):
        console.print(
            "[yellow]Restart the daemon (and dashboard) to load the new code:[/yellow] "
            "stop `ironjarvis serve`/`up` and start it again."
        )
    if not result.get("ok"):
        raise typer.Exit(code=1)


@app.command()
def repair(
    root: str = typer.Option(".", help="Project root (for state location)."),
    no_sync: bool = typer.Option(False, "--no-sync", help="Skip re-syncing Python deps."),
) -> None:
    """OFFLINE recovery: re-sync deps + check/compact the database.

    Works WITHOUT loading the platform, so you can run it when the daemon won't
    boot (broken deps after an update, a bloated/corrupt DB). Idempotent."""
    import sqlite3

    # 1. Restore Python deps (the most common post-update breakage).
    if not no_sync:
        repo = _source_repo_root()
        if repo is not None:
            from ..core.updates import _subprocess_runner

            res = _subprocess_runner(["uv", "sync", "--extra", "dev"], repo)
            mark = "[green]OK[/green]" if res.returncode == 0 else "[red]FAIL[/red]"
            console.print(f"  {mark} uv sync (rc={res.returncode})")
            if res.returncode != 0 and (res.stderr or "").strip():
                console.print(f"      [dim]{res.stderr.strip()[:400]}[/dim]")
        else:
            console.print("  [yellow]skip uv sync[/yellow] — not a source checkout")

    # 2. Database integrity + recovery (no platform needed; raw sqlite3).
    home = _home_for(root)
    db = home / "ironjarvis.db"
    if not db.exists():
        console.print(f"  [yellow]no database[/yellow] at {db}")
        return
    try:
        con = sqlite3.connect(str(db))
        try:
            integ = con.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001 — header/not-a-database corruption
        integ = f"unreadable: {exc}"

    if integ == "ok":
        con = sqlite3.connect(str(db))
        con.isolation_level = None  # VACUUM cannot run inside a transaction
        con.execute("VACUUM")
        con.close()
        console.print("  [green]OK[/green] database integrity: ok; vacuumed")
        console.print("[green]repair complete[/green]")
        return

    # Corrupt DB → recover. Prefer restoring the newest backup; else quarantine the
    # corrupt file and let the next boot create a fresh DB (daemon recovers, data
    # lost only if there was no backup).
    console.print(f"  [red]FAIL[/red] database integrity: {integ}")
    backups_dir = home / "backups"
    snaps = sorted(
        backups_dir.glob("ironjarvis-backup-*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True
    ) if backups_dir.exists() else []
    if snaps:
        import tarfile

        newest = snaps[0]
        console.print(f"  restoring newest backup: {newest.name}")
        from ..core.db import quarantine_db

        quarantine_db(db, "corrupt (pre-restore)")
        try:
            with tarfile.open(newest, "r:gz") as tar:
                tar.extractall(path=home.parent, filter="data")
            con = sqlite3.connect(str(db))
            ok = con.execute("PRAGMA integrity_check").fetchone()[0]
            con.close()
            if ok == "ok":
                console.print(f"  [green]OK[/green] restored a healthy DB from {newest.name}")
                console.print("[green]repair complete[/green] — restart the daemon.")
                return
            console.print(f"  [red]restored DB still fails integrity[/red]: {ok}")
        except Exception as exc:  # noqa: BLE001
            console.print(f"  [red]restore failed[/red]: {exc}")
        raise typer.Exit(code=1)

    from ..core.db import quarantine_db

    dead = quarantine_db(db, f"corrupt: {integ}")
    console.print(
        f"  [yellow]no backup to restore[/yellow] — quarantined the corrupt DB"
        + (f" as {dead.name}" if dead else "")
        + "; the next `ironjarvis serve` will start with a fresh (empty) DB."
    )
    console.print("[green]repair complete[/green] — restart the daemon.")


@app.command()
def rollback(
    to: str = typer.Option(
        "", "--to", help="Commit to reset to (default: the pre-update commit)."
    ),
) -> None:
    """OFFLINE: undo a bad self-update — git reset --hard to the pre-update commit.

    Resolves the target in order: --to if given → the ``ironjarvis/pre-update`` tag
    that self-update writes before it pulls → git's ``ORIG_HEAD`` (set by the pull)
    → ``HEAD@{1}``. This targets the ACTUAL last-known-good commit rather than a
    fragile reflog position."""
    repo = _source_repo_root()
    if repo is None:
        console.print("[red]not a source checkout[/red] — nothing to roll back.")
        raise typer.Exit(code=1)
    from ..core.updates import _subprocess_runner, _out

    def _resolves(ref: str) -> str | None:
        res = _subprocess_runner(["git", "rev-parse", "--verify", "--quiet", ref], repo)
        return _out(res)

    target = to.strip()
    if not target:
        for cand in ("ironjarvis/pre-update", "ORIG_HEAD", "HEAD@{1}"):
            if _resolves(cand):
                target = cand
                break
    resolved = _resolves(target) if target else None
    if not resolved:
        console.print(
            "[red]cannot roll back[/red] — no pre-update commit found "
            "(no ironjarvis/pre-update tag, ORIG_HEAD, or HEAD@{1}). "
            "Pass --to <known-good-sha>."
        )
        raise typer.Exit(code=1)
    head = _resolves("HEAD")
    if resolved == head:
        console.print(f"[cyan]already at[/cyan] {resolved[:10]} — nothing to roll back.")
        return

    console.print(f"[cyan]Rolling back[/cyan] {repo}: {target} -> {resolved[:10]}")
    reset = _subprocess_runner(["git", "reset", "--hard", resolved], repo)
    if reset.returncode != 0:
        console.print(f"  [red]FAIL[/red] git reset --hard (rc={reset.returncode})")
        console.print(f"      [dim]{(reset.stderr or '').strip()[:400]}[/dim]")
        raise typer.Exit(code=1)
    # Verify HEAD actually moved to the target before claiming success.
    if _resolves("HEAD") != resolved:
        console.print("[red]rollback did not land[/red] — HEAD is not at the target.")
        raise typer.Exit(code=1)
    console.print(f"  [green]OK[/green] git reset --hard -> {resolved[:10]}")
    sync = _subprocess_runner(["uv", "sync", "--extra", "dev"], repo)
    console.print(
        f"  {'[green]OK[/green]' if sync.returncode == 0 else '[red]FAIL[/red]'} uv sync"
    )
    console.print(
        "[green]rolled back[/green] — restart the daemon (and dashboard) to load the restored code."
    )


@app.command("reset-config")
def reset_config(
    root: str = typer.Option(".", help="Project root."),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
) -> None:
    """OFFLINE: back up config.toml to .bak and write fresh defaults.

    Recovers a daemon that won't boot because of a corrupt/invalid config —
    operates on the file directly, never loading the (possibly broken) config."""
    import shutil

    home = _home_for(root)
    path = home / "config.toml"
    if path.exists() and not yes:
        console.print(f"[yellow]Will reset[/yellow] {path} to defaults (a .bak is kept).")
        console.print("Re-run with [bold]--yes[/bold] to proceed.")
        raise typer.Exit(code=1)
    home.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_name("config.toml.bak"))
        console.print(f"  backed up old config -> {path.with_name('config.toml.bak')}")
    from ..core.config import atomic_write_toml, default_permissions, default_sandbox_policy

    atomic_write_toml(
        path,
        {
            "default_provider": "mock",
            "default_model": "claude-opus-4-8",
            "max_agent_steps": 12,
            "permissions": default_permissions(),
            "sandbox": default_sandbox_policy(),
        },
    )
    console.print(f"[green]wrote fresh config[/green] {path}")


if __name__ == "__main__":
    app()
