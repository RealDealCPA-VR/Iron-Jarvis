"""Workflow Engine + Trigger System (SPEC §24, §25).

Repeatable processes built from ordered steps, each driven by an agent. What
starts one is a schedule (``scheduling/``) or a rule (``reflex/``), through the
one start hub (``triggers/starts``); ``triggers`` here only reads a
``[[triggers]]`` block and checks a cron line.

Importing this package (specifically :mod:`iron_jarvis.workflows.models`) before
``init_db`` registers the ``WorkflowRunRecord`` table on ``SQLModel.metadata``.
"""

from __future__ import annotations

from .engine import Step, WorkflowDef, WorkflowEngine, load_workflow, load_workflow_toml
from .models import WorkflowRunRecord
from .triggers import (
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
    "TriggerSpec",
    "parse_triggers",
    "validate_cron",
]
