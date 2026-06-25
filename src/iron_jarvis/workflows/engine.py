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

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.db import dumps, session_scope
from ..core.events import EventType
from ..core.ids import utcnow
from ..core.models import AgentType, SessionStatus
from .models import WorkflowRunRecord


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
    """Runs :class:`WorkflowDef`s step-by-step via the Orchestrator (SPEC §24)."""

    def __init__(self, platform) -> None:
        self.platform = platform

    async def run(self, workflow: WorkflowDef) -> WorkflowRunRecord:
        # Lazy import: the orchestrator pulls in the agent runtime, which would
        # create an import cycle if imported at module load time.
        from ..agents.orchestrator import Orchestrator

        session_ids: list[str] = []
        outputs: dict[str, Any] = {}
        all_completed = True

        for step in workflow.steps:
            session = await Orchestrator(self.platform).run(
                step.task, _agent_type(step.agent), provider=None
            )
            session_ids.append(session.id)
            outputs[step.name] = {
                "session_id": session.id,
                "status": session.status.value,
                "summary": session.summary,
                "tool": step.tool,
            }
            if session.status is not SessionStatus.COMPLETED:
                all_completed = False

        status = "completed" if all_completed else "failed"
        record = WorkflowRunRecord(
            workflow_name=workflow.name,
            status=status,
            session_ids_json=dumps(session_ids),
            outputs_json=dumps(outputs),
            finished_at=utcnow(),
        )
        with session_scope(self.platform.engine) as db:
            db.add(record)
            db.commit()
            db.refresh(record)  # un-expire attrs so the detached record stays usable

        await self.platform.event_bus.publish(
            EventType.WORKFLOW_COMPLETED,
            {
                "workflow": workflow.name,
                "status": status,
                "run_id": record.id,
                "sessions": session_ids,
            },
        )
        return record
