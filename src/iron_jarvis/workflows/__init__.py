"""Workflow Engine + Trigger System (SPEC §24, §25).

Repeatable processes built from ordered steps, each driven by an agent, plus a
trigger layer (manual / cron / webhook / file / email / calendar / api) that
decides *when* a workflow runs.

Importing this package (specifically :mod:`iron_jarvis.workflows.models`) before
``init_db`` registers the ``WorkflowRunRecord`` table on ``SQLModel.metadata``.
"""

from __future__ import annotations

from .engine import Step, WorkflowDef, WorkflowEngine, load_workflow, load_workflow_toml
from .models import WorkflowRunRecord
from .triggers import (
    CronScheduler,
    TriggerSpec,
    parse_triggers,
    validate_cron,
)

__all__ = [
    "Step",
    "WorkflowDef",
    "WorkflowEngine",
    "load_workflow",
    "load_workflow_toml",
    "WorkflowRunRecord",
    "CronScheduler",
    "TriggerSpec",
    "parse_triggers",
    "validate_cron",
]
