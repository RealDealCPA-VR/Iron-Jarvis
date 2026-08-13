"""Agent-facing workflow tools (§19 tool interface, §24).

``workflow_create`` lets an agent save its *own* workflow definition through the
tool loop. The workflow persists as a
:class:`~iron_jarvis.workflows.models.WorkflowRecord` (via :class:`WorkflowStore`)
so the user sees it in the dashboard and the engine can re-load + run it.

v1.170.0 adds the other two verbs of the module's tool surface:

* ``workflow_list`` — READ-ONLY discovery: name, description, step count, and
  the project each def is pinned to. Without it a model could *save* workflows
  it could never enumerate, so "run my month-end workflow" started from a guess.
* ``workflow_run`` — start a SAVED workflow by name. The stored steps and the
  def's project pin resolve through :meth:`WorkflowStore.load_def` (the ONE
  stored-record -> def seam), the record is created synchronously, and the run
  is driven in the BACKGROUND — the tool loop is never parked behind a
  multi-minute run, mirroring ``POST /workflows/run``.

Each tool is constructed with the assembled ``platform`` (like
:class:`~iron_jarvis.agents.delegate_tool.DelegateTool`) and acts on
``platform.engine``. ``workflow_tools(platform)`` builds them for registration.
"""

from __future__ import annotations

import json as _json
from typing import Any

from ..tools.base import Reversibility, Tool, ToolContext, ToolResult
from .store import WorkflowStore


class WorkflowCreateTool(Tool):
    """Save a named, ordered workflow so it persists and shows in the dashboard."""

    name = "workflow_create"
    description = (
        "Save a reusable workflow that persists (the user sees it in the "
        "dashboard and it can be scheduled/run later). `steps` is an ordered "
        "list of {name, agent, task} objects — each step runs `agent` on `task`. "
        "Re-using a `name` updates the existing workflow in place. Returns the "
        "saved workflow name and step count."
    )
    permission_key = "workflow_create"
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "agent": {"type": "string"},
                        "task": {"type": "string"},
                    },
                },
            },
            "description": {"type": "string"},
        },
        "required": ["name", "steps"],
    }

    def __init__(self, platform) -> None:
        self.platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        name = args.get("name") or ""
        if not name:
            return ToolResult(ok=False, error="name is required")
        steps = args.get("steps") or []
        rec = WorkflowStore(self.platform.engine).save(
            name, steps, args.get("description", "")
        )
        return ToolResult(
            ok=True,
            output=f"saved workflow '{rec.name}' with {len(steps)} step(s)",
            data={"name": rec.name, "steps": len(steps), "id": rec.id},
        )


class WorkflowListTool(Tool):
    """Read-only discovery of the user's saved workflows (v1.170.0)."""

    name = "workflow_list"
    description = (
        "List the user's saved workflows: each name, description, step count, "
        "and the project it is pinned to (null when unpinned). Read-only — "
        "start one with workflow_run, save one with workflow_create."
    )
    permission_key = "workflow_list"
    reversibility = Reversibility.READONLY
    #: Max entries in the human-readable output (data stays complete).
    OUTPUT_CAP = 50
    input_schema = {"type": "object", "properties": {}}

    def __init__(self, platform) -> None:
        self.platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        store = WorkflowStore(self.platform.engine)
        rows = store.list()
        pins = store.pins()
        # Resolve pinned project ids to their NAMES (what the user calls them).
        names: dict[str, str] = {}
        pin_ids = {pid for pid in pins.values() if pid}
        if pin_ids:
            from ..core.db import session_scope
            from ..core.models import Project

            with session_scope(self.platform.engine) as db:
                for pid in pin_ids:
                    proj = db.get(Project, pid)
                    if proj is not None and (proj.name or "").strip():
                        names[pid] = proj.name
        entries: list[dict[str, Any]] = []
        for r in rows:
            try:
                count = len(_json.loads(r.steps_json or "[]"))
            except (ValueError, TypeError):
                count = 0
            pid = pins.get(r.name)
            entries.append(
                {
                    "name": r.name,
                    "description": r.description or "",
                    "steps": count,
                    # The pinned project's name; a DANGLING pin (project since
                    # deleted) honestly falls back to the raw id rather than
                    # pretending the def is unpinned.
                    "project": (names.get(pid) or pid) if pid else None,
                    "project_id": pid or None,
                }
            )
        if not entries:
            output = "No saved workflows yet."
        else:
            # Defs are agent-mintable (workflow_create) and never pruned, so
            # the human-readable text is BOUNDED — a huge catalog must not dump
            # wholesale into the model context. Truncation is REPORTED (the
            # repo rule: a silently short listing reads as complete);
            # data["workflows"] stays complete for _store_as/repl access.
            shown = entries[: self.OUTPUT_CAP]
            lines = [
                f"• {e['name']} — {e['steps']} step(s)"
                + (f", pinned to {e['project']}" if e["project"] else "")
                for e in shown
            ]
            output = f"{len(entries)} saved workflow(s):\n" + "\n".join(lines)
            hidden = len(entries) - len(shown)
            if hidden:
                output += f"\n(+{hidden} more — data carries the full list)"
        return ToolResult(
            ok=True, output=output, data={"workflows": entries, "count": len(entries)}
        )


class WorkflowRunTool(Tool):
    """Start a SAVED workflow by name; the run proceeds in the background."""

    name = "workflow_run"
    description = (
        "Run a SAVED workflow by name. The stored steps and the workflow's "
        "project pin are resolved server-side, the run starts in the "
        "BACKGROUND, and the result carries its run_id (the user watches it "
        "on the Workflows page or in chat). Optional `inputs` ({name: value}) "
        "pre-seed named values steps can reference as {{name}}. Use "
        "workflow_list to see what exists."
    )
    permission_key = "workflow_run"
    # Deliberately NOT declared reversible: a run spawns agent steps whose
    # effects (sends, files, notifications) have their own undo stories.
    input_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "the saved workflow's exact name",
            },
            "inputs": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": (
                    "optional named string values pre-seeded into the run; "
                    "steps reference each as {{name}}"
                ),
            },
        },
        "required": ["name"],
    }

    def __init__(self, platform) -> None:
        self.platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from .engine import WorkflowEngine

        name = str(args.get("name") or "").strip()
        if not name:
            return ToolResult(ok=False, error="name is required")
        store = WorkflowStore(self.platform.engine)
        # load_def is the ONE stored-record -> def seam — the project pin
        # rides along for free (the same fix the reflex router got).
        wf = store.load_def(name)
        if wf is None:
            known = [r.name for r in store.list()]
            if known:
                listed = ", ".join(known[:20])
                more = f" (+{len(known) - 20} more)" if len(known) > 20 else ""
                hint = f"; saved workflows: {listed}{more}"
            else:
                hint = (
                    "; no workflows are saved yet — save one with workflow_create"
                )
            return ToolResult(ok=False, error=f"no saved workflow '{name}'{hint}")
        raw = args.get("inputs")
        inputs: dict[str, str] | None = None
        if raw is not None and not isinstance(raw, dict):
            # A malformed `inputs` (a string, a list, …) must be an HONEST
            # error, not a run silently started WITHOUT its inputs — every
            # {{name}} template would go unresolved and nothing would say why.
            return ToolResult(
                ok=False, error="inputs must be an object of {name: value}"
            )
        if isinstance(raw, dict) and raw:
            inputs = {
                str(k): (v if isinstance(v, str) else _json.dumps(v))
                for k, v in raw.items()
            }
        # Pass `inputs` ONLY when the caller provided some: with the kwarg
        # absent this is byte-identical to the pre-v1.170.0 engine calls, so
        # input-less runs cannot regress (contract 5 owns the seeding).
        kwargs: dict[str, Any] = {"inputs": inputs} if inputs is not None else {}
        # The SHARED daemon orchestrator when attached (so the cancel route can
        # reach the in-flight step session); None lets the engine build a
        # throwaway one — same split as the scheduler's workflow dispatch.
        engine = WorkflowEngine(self.platform, self.platform.orchestrator)
        try:
            rec = engine.create_record(wf, **kwargs)
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        task = self._launch(engine.run_record(rec, wf, **kwargs), rec.id)
        if task is None:
            # The managed spawner REFUSED (daemon draining — for a no-session-
            # row workflow id, None never means "parked"). The record was
            # already persisted "running", and unfinished statuses are never
            # pruned — left alone it would spin forever for a run that will
            # never execute. Flip it honestly and tell the model the truth.
            from ..core.ids import utcnow

            detail = "daemon is shutting down — run not started"
            try:
                outs = _json.loads(rec.outputs_json or "{}")
            except (ValueError, TypeError):
                outs = {}
            outs.setdefault(
                "__launch__",
                {"status": "failed", "summary": detail, "kind": "system"},
            )
            engine._update_record(
                rec.id, status="failed", outputs=outs, finished_at=utcnow()
            )
            return ToolResult(ok=False, error=detail)
        return ToolResult(
            ok=True,
            output=(
                f"started workflow '{wf.name}' "
                f"({len(wf.steps)} step(s), run {rec.id})"
            ),
            # Contract 2 (v1.170.0): the chat lanes key their `workflow_run`
            # payload off exactly these three fields.
            data={"run_id": rec.id, "workflow": wf.name, "status": "running"},
        )

    def _launch(self, coro: Any, run_id: str) -> Any:
        """Drive the run in the background. Prefer the daemon's managed spawner
        (task registered for cancellation + graceful shutdown — the same path
        ``POST /workflows/run`` takes via ``_spawn_bg``); fall back to a bare
        task for bare-platform embedding (tests, CLI). Returns the task, or
        ``None`` when the managed spawner refused (daemon draining) — the
        caller flips the already-persisted record to ``failed`` and reports
        honestly instead of claiming a run that will never execute."""
        spawn = getattr(self.platform.orchestrator, "spawn_managed", None)
        if callable(spawn):
            return spawn(run_id, coro)
        import asyncio

        return asyncio.ensure_future(coro)


def workflow_tools(platform) -> list[Tool]:
    """Build the workflow tools bound to the assembled ``platform``."""
    return [
        WorkflowCreateTool(platform),
        WorkflowListTool(platform),
        WorkflowRunTool(platform),
    ]
