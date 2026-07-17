"""Workflow Engine (SPEC §24).

A workflow is an ordered list of steps. Each step runs an agent on a task (and
may name a tool focus). The engine drives each step through the Orchestrator,
collects the resulting session ids + summaries, persists a
:class:`~iron_jarvis.workflows.models.WorkflowRunRecord`, and publishes a
``WORKFLOW_COMPLETED`` event.

TOML authoring shape (matches SPEC §24's example flavour)::

    name = "monthly_close"
    description = "Close the books"

    [[steps]]
    name = "gather"
    agent = "builder"
    task = "collect this month's receipts"
"""

from __future__ import annotations

import asyncio
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.db import dumps, session_scope
from ..core.events import EventType
from ..core.ids import utcnow
from ..core.models import AgentType, Project, SessionStatus
from .models import WorkflowRunRecord

#: Context-chaining bounds: each earlier-step summary is clipped, and the whole
#: injected block is capped so a long-running workflow can't blow up the prompt.
_MAX_STEP_SUMMARY = 1500
_MAX_CONTEXT = 4000


@dataclass
class Step:
    """One unit of work in a workflow (SPEC §24: step + agent + tool)."""

    name: str
    agent: str = "builder"
    task: str = ""
    tool: str | None = None


@dataclass
class WorkflowDef:
    """A repeatable process: a named, ordered list of steps (SPEC §24)."""

    name: str
    steps: list[Step] = field(default_factory=list)
    description: str = ""
    #: Optional EXPLICIT project pin (context spine): when set, every run is
    #: stamped with it and each step session is grounded in the project's
    #: brief/instructions/knowledge. None = project-agnostic — the globally
    #: active project never leaks into a workflow run.
    project_id: str | None = None


def _agent_type(name: str) -> AgentType:
    """Map a step's ``agent`` string to an :class:`AgentType`, default builder."""
    try:
        return AgentType(name)
    except ValueError:
        return AgentType.BUILDER


def load_workflow(data: dict) -> WorkflowDef:
    """Build a :class:`WorkflowDef` from a parsed mapping (e.g. TOML/JSON)."""
    steps: list[Step] = []
    for raw in data.get("steps", []) or []:
        steps.append(
            Step(
                name=str(raw.get("name", "")),
                agent=str(raw.get("agent", "builder")),
                task=str(raw.get("task", "")),
                tool=raw.get("tool"),
            )
        )
    return WorkflowDef(
        name=str(data.get("name", "")),
        steps=steps,
        description=str(data.get("description", "")),
        # Optional explicit project pin — absent/empty both mean unpinned.
        project_id=(str(data.get("project_id") or "").strip() or None),
    )


def _read_toml_text(path_or_str: str | Path) -> str:
    """Return TOML text from either a filesystem path or a literal string."""
    if isinstance(path_or_str, Path):
        return path_or_str.read_text(encoding="utf-8")
    text = str(path_or_str)
    # Treat the argument as a path only if it actually points at a file; a
    # multi-line TOML string would raise on Path.is_file (guarded below).
    try:
        candidate = Path(text)
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    except (OSError, ValueError):
        pass
    return text


def load_workflow_toml(path_or_str: str | Path) -> WorkflowDef:
    """Load a workflow from a ``.toml`` file path or a raw TOML string."""
    data = tomllib.loads(_read_toml_text(path_or_str))
    return load_workflow(data)


class WorkflowEngine:
    """Runs :class:`WorkflowDef`s step-by-step via the Orchestrator (SPEC §24).

    Execution is ASYNC: :meth:`create_record` persists a ``running`` record up
    front (so a crash mid-run leaves a trace and the HTTP request returns at
    once), then :meth:`run_record` drives the steps IN THE BACKGROUND, updating
    the same record after each one. :meth:`run` (create + await) is kept for the
    synchronous callers (scheduling, the CLI, tests).

    ``orchestrator`` is the SHARED daemon orchestrator when the daemon spawns a
    run — the cancel route reaches the currently-running step session through it.
    Synchronous callers pass nothing and get a throwaway per-run one.
    """

    def __init__(self, platform, orchestrator=None) -> None:
        self.platform = platform
        self.orchestrator = orchestrator

    @staticmethod
    def _has_runnable_steps(workflow: WorkflowDef) -> bool:
        """A workflow is runnable only if it has at least one non-empty step —
        an empty plan must NOT report 'completed' (it masked mis-configuration)."""
        steps = workflow.steps or []
        return any(
            (s.name or "").strip() or (s.task or "").strip() for s in steps
        )

    def create_record(self, workflow: WorkflowDef) -> WorkflowRunRecord:
        """Persist a fresh ``running`` record for *workflow* and return it.

        Raises ``ValueError`` for a zero-/empty-step workflow (the route turns
        that into a 400). ``started_at`` is stamped HERE (at the true start), not
        at the end. The record is refreshed so it stays usable once detached.
        """
        if not self._has_runnable_steps(workflow):
            raise ValueError("workflow has no steps")
        steps_meta = [{"name": s.name, "agent": s.agent} for s in workflow.steps]
        record = WorkflowRunRecord(
            workflow_name=workflow.name,
            status="running",
            # Workflows are their own module — a run is NOT tagged to whatever
            # project is globally active; it carries a project ONLY when the def
            # itself is explicitly pinned to one (None otherwise).
            project_id=workflow.project_id,
            steps_json=dumps(steps_meta),
            session_ids_json="[]",
            outputs_json="{}",
        )
        with session_scope(self.platform.engine) as db:
            db.add(record)
            db.commit()
            db.refresh(record)  # un-expire attrs so the detached record stays usable
        return record

    async def run(self, workflow: WorkflowDef) -> WorkflowRunRecord:
        """Create the record AND run it to completion (synchronous callers)."""
        record = self.create_record(workflow)
        return await self.run_record(record, workflow)

    async def run_record(
        self, record: WorkflowRunRecord, workflow: WorkflowDef
    ) -> WorkflowRunRecord:
        """Drive *workflow*'s steps, updating *record* in place as it goes.

        After each step: ``outputs[name] = {session_id, status, summary, tool}``.
        A failed step stops the run (status ``failed``, the rest ``skipped``); a
        cancel (status flips to ``cancelling`` in the DB, checked before every
        step, plus the in-flight session is cancelled) stops it ``cancelled``.
        Each later step's task is enriched with prior steps' summaries.
        """
        # Lazy import: the orchestrator pulls in the agent runtime, which would
        # create an import cycle if imported at module load time.
        from ..agents.orchestrator import Orchestrator

        orch = self.orchestrator or Orchestrator(self.platform)
        run_id = record.id
        # Resolve the pinned project's folder ONCE for the whole run (None when
        # unpinned, or when the folder is missing on disk — see the helper).
        workspace_root = self._project_workspace_root(workflow.project_id)
        steps = list(workflow.steps)
        session_ids: list[str] = []
        outputs: dict[str, Any] = {}
        completed: list[tuple[str, str]] = []  # (name, summary) for chaining
        final_status = "completed"

        for idx, step in enumerate(steps):
            # Cancellation is cooperative: re-read the authoritative status the
            # cancel route wrote before starting each step.
            current = self._get_record(run_id)
            if current is not None and current.status == "cancelling":
                final_status = "cancelled"
                for s in steps[idx:]:
                    outputs.setdefault(s.name, {"status": "skipped"})
                break

            task_text = step.task + self._context_block(completed)
            # The def's explicit pin (None for unpinned workflows) grounds each
            # step in the project — its instructions/knowledge inject at run
            # time — and, when the project has a valid folder, runs the step
            # directly IN that folder so deliverables land where the user expects.
            session = await orch.create_session(
                task_text,
                _agent_type(step.agent),
                provider=None,
                project_id=workflow.project_id,
                workspace_root=workspace_root,
            )
            session_ids.append(session.id)
            # Record the live session id BEFORE running, so a cancel arriving
            # mid-step can find and stop it.
            self._update_record(
                run_id,
                current_session_id=session.id,
                session_ids=session_ids,
                outputs=outputs,
            )
            task = asyncio.ensure_future(orch.run_session(session.id))
            orch.register_running(session.id, task)
            try:
                session = await task
            except asyncio.CancelledError:
                # The cancel route stopped this step's session (or the daemon is
                # shutting down). Record it cancelled, skip the rest, stop.
                final_status = "cancelled"
                outputs[step.name] = {
                    "session_id": session.id,
                    "status": "cancelled",
                    "summary": "",
                    "tool": step.tool,
                }
                for s in steps[idx + 1:]:
                    outputs.setdefault(s.name, {"status": "skipped"})
                break

            outputs[step.name] = {
                "session_id": session.id,
                "status": session.status.value,
                "summary": session.summary,
                "tool": step.tool,
            }
            self._update_record(
                run_id,
                current_session_id=None,
                session_ids=session_ids,
                outputs=outputs,
            )
            if session.status is SessionStatus.COMPLETED:
                completed.append((step.name, session.summary or ""))
            else:
                # A failed step halts the workflow: the rest never ran.
                final_status = "failed"
                for s in steps[idx + 1:]:
                    outputs.setdefault(s.name, {"status": "skipped"})
                break

        final = self._update_record(
            run_id,
            status=final_status,
            current_session_id=None,
            session_ids=session_ids,
            outputs=outputs,
            finished_at=utcnow(),
        )
        await self.platform.event_bus.publish(
            EventType.WORKFLOW_COMPLETED,
            {
                "workflow": workflow.name,
                "status": final_status,
                "run_id": run_id,
                "sessions": session_ids,
            },
        )
        return final if final is not None else record

    @staticmethod
    def _context_block(completed: list[tuple[str, str]]) -> str:
        """Build the '# Context from earlier steps' block from prior COMPLETED
        steps' summaries — each clipped, the whole thing capped, most-recent
        steps kept when the budget is tight (then re-ordered chronologically)."""
        if not completed:
            return ""
        parts: list[str] = []
        total = 0
        for name, summary in reversed(completed):  # newest first — they win the budget
            block = f"\n## {name}\n{(summary or '')[:_MAX_STEP_SUMMARY]}"
            if total + len(block) > _MAX_CONTEXT:
                break
            parts.append(block)
            total += len(block)
        if not parts:
            return ""
        parts.reverse()  # present oldest -> newest
        return "\n\n# Context from earlier steps" + "".join(parts)

    def _project_workspace_root(self, project_id: str | None) -> str | None:
        """Return the pinned project's folder for step sessions, or None.

        Mirrors the project-task route's validation: the root must be set AND be
        an existing directory. A moved/deleted folder returns None so the step
        degrades to a normal per-session workspace instead of failing the run —
        the pin's project context still applies; only the folder is skipped.
        """
        if not project_id:
            return None
        with session_scope(self.platform.engine) as db:
            project = db.get(Project, project_id)
        if project is None or not (project.root or "").strip():
            return None
        root = Path(project.root)
        return str(root) if root.is_dir() else None

    def _get_record(self, run_id: str) -> WorkflowRunRecord | None:
        with session_scope(self.platform.engine) as db:
            return db.get(WorkflowRunRecord, run_id)

    def _update_record(self, run_id: str, **fields: Any) -> WorkflowRunRecord | None:
        """Apply the given fields to the record and persist. ``session_ids`` and
        ``outputs`` are JSON-encoded; other keys map straight onto the column."""
        with session_scope(self.platform.engine) as db:
            rec = db.get(WorkflowRunRecord, run_id)
            if rec is None:
                return None
            if "status" in fields:
                rec.status = fields["status"]
            if "current_session_id" in fields:
                rec.current_session_id = fields["current_session_id"]
            if "session_ids" in fields:
                rec.session_ids_json = dumps(fields["session_ids"])
            if "outputs" in fields:
                rec.outputs_json = dumps(fields["outputs"])
            if "finished_at" in fields:
                rec.finished_at = fields["finished_at"]
            db.add(rec)
            db.commit()
            db.refresh(rec)
            return rec
