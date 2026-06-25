"""Tests for the Workflow Engine + Trigger System (SPEC §24, §25)."""

from __future__ import annotations

import json

# Register the WorkflowRunRecord table on SQLModel.metadata BEFORE any platform
# is built (build_platform -> init_db creates the tables). Must stay at the top.
import iron_jarvis.workflows.models  # noqa: F401

from iron_jarvis.core.db import session_scope
from iron_jarvis.platform import build_platform
from iron_jarvis.workflows.engine import (
    Step,
    WorkflowDef,
    WorkflowEngine,
    load_workflow_toml,
)
from iron_jarvis.workflows.models import WorkflowRunRecord
from iron_jarvis.workflows.triggers import parse_triggers, validate_cron
from sqlmodel import select


async def test_engine_runs_all_steps_and_persists(tmp_path):
    platform = build_platform(str(tmp_path))
    engine = WorkflowEngine(platform)

    wf = WorkflowDef(
        name="bookkeeping",
        steps=[
            Step(name="s1", agent="builder", task="create a summary file"),
            Step(name="s2", agent="builder", task="create a second file"),
        ],
    )
    rec = await engine.run(wf)

    assert rec.status == "completed"

    ids = json.loads(rec.session_ids_json)
    assert len(ids) == 2

    outputs = json.loads(rec.outputs_json)
    assert outputs  # non-empty
    assert set(outputs.keys()) == {"s1", "s2"}

    # A row really landed in the database.
    with session_scope(platform.engine) as db:
        rows = db.exec(
            select(WorkflowRunRecord).where(WorkflowRunRecord.id == rec.id)
        ).all()
    assert len(rows) == 1
    assert rows[0].workflow_name == "bookkeeping"
    assert rows[0].status == "completed"


def test_load_workflow_toml_from_string():
    toml = """
name = "bookkeeping"
description = "monthly close"

[[steps]]
name = "gather"
agent = "builder"
task = "gather the receipts"

[[steps]]
name = "summarize"
agent = "builder"
task = "summarize the books"
"""
    wf = load_workflow_toml(toml)

    assert wf.name == "bookkeeping"
    assert wf.description == "monthly close"
    assert len(wf.steps) == 2
    assert [s.name for s in wf.steps] == ["gather", "summarize"]
    assert wf.steps[0].agent == "builder"


def test_parse_triggers_and_validate_cron():
    specs = parse_triggers(
        {
            "triggers": [
                {"name": "monthly", "schedule": "0 8 1 * *", "workflow": "bookkeeping"}
            ]
        }
    )

    assert len(specs) == 1
    spec = specs[0]
    assert spec.kind == "cron"
    assert spec.schedule == "0 8 1 * *"
    assert spec.name == "monthly"
    assert spec.workflow == "bookkeeping"

    assert validate_cron("0 8 1 * *") is True
    assert validate_cron("not a cron") is False


def test_parse_triggers_defaults_to_manual_without_schedule():
    specs = parse_triggers({"triggers": [{"name": "on_demand", "workflow": "wf"}]})

    assert len(specs) == 1
    assert specs[0].kind == "manual"
    assert specs[0].schedule is None
