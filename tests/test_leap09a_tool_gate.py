"""Idea 09, phase 09-a: a block changes nothing about what a tool step may do.

A tool step inside a For each (or an If) is the same tool step: it meets the
tool's permission tier and the tool's OWN "ask every time" check for EVERY
item, through the real registry, the real permission engine and the real
tools (the layer that refuses; nothing stubbed). An outbound webhook asked
for in a loop is refused once per item, ledgered each time, and never made;
an Ask-tier tool is refused in the app's words; a read-only tool runs.
"""

from __future__ import annotations

# Register workflow tables on SQLModel.metadata BEFORE any platform is built.
import iron_jarvis.workflows.models  # noqa: F401

import json

from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.platform import build_platform
from iron_jarvis.webhooks.models import WebhookRecord
from iron_jarvis.workflows.engine import WorkflowEngine, load_workflow

HOST = "hooks.example.test"


def _quiet_outbound(platform) -> list:
    posted: list = []
    platform.outbound_webhooks.allow_internal = True
    platform.outbound_webhooks.http_post = lambda *a, **k: posted.append((a, k))
    return posted


def _rows(platform, run_id: str, tool: str) -> list[ToolInvocation]:
    with session_scope(platform.engine) as db:
        rows = list(db.exec(select(ToolInvocation).where(
            ToolInvocation.session_id == run_id, ToolInvocation.tool == tool)))
        for r in rows:
            db.expunge(r)
        return rows


def _webhooks(platform) -> set[str]:
    with session_scope(platform.engine) as db:
        return {r.slug for r in db.exec(select(WebhookRecord))}


def _loop(step: dict, **block) -> dict:
    return {
        "name": "per client",
        "steps": [
            {
                "name": "Each client",
                "kind": "for_each",
                "over": {"from": '["acme", "globex", "initech"]'},
                "on_failure": "skip",
                "steps": [step],
                **block,
            }
        ],
    }


async def test_an_outbound_webhook_in_a_loop_is_refused_for_every_item_and_never_made(tmp_path):
    platform = build_platform(str(tmp_path))
    _quiet_outbound(platform)
    step = {
        "name": "Wire it",
        "kind": "tool",
        "tool": "webhook_add",
        "args": {
            "slug": "out-{{item}}",
            "direction": "outbound",
            "target_url": f"https://{HOST}/path/{{{{item}}}}",
            "event_types": ["session.completed"],
        },
    }
    rec = await WorkflowEngine(platform).run(load_workflow(_loop(step)))
    outs = json.loads(rec.outputs_json)
    assert not {s for s in _webhooks(platform) if s.startswith("out-")}, "a loop made an outbound webhook unasked"
    for n in (1, 2, 3):
        out = outs[f"Each client/{n}/Wire it"]
        assert out["status"] == "failed"
        assert "every time" in out["summary"] and "from chat" in out["summary"], out["summary"]
    # Ledgered as a refusal for each item (the registry's deny seam), never as a run.
    rows = _rows(platform, rec.id, "webhook_add")
    assert len(rows) == 3 and not any(r.ok for r in rows)
    block = outs["Each client"]
    assert block["summary"] == "0 of 3 done, 3 failed"
    assert rec.status == "completed_with_failures"


async def test_an_ask_tier_tool_in_a_loop_is_refused_in_plain_words(tmp_path):
    platform = build_platform(str(tmp_path))
    step = {"name": "Run it", "kind": "tool", "tool": "shell", "args": {"command": "echo {{item}}"}}
    rec = await WorkflowEngine(platform).run(load_workflow(_loop(step)))
    outs = json.loads(rec.outputs_json)
    for n in (1, 2, 3):
        summary = outs[f"Each client/{n}/Run it"]["summary"]
        assert "Agent step" in summary and "shell" in summary, summary
        for engine_word in ("headless", "auto-approve", "interactive approval"):
            assert engine_word not in summary
    assert not any(r.ok for r in _rows(platform, rec.id, "shell")), "shell ran in a loop"
    assert outs["Each client"]["summary"] == "0 of 3 done, 3 failed"


async def test_a_read_only_tool_in_a_loop_runs_for_every_item(tmp_path):
    platform = build_platform(str(tmp_path))
    for name in ("acme", "globex", "initech"):
        (tmp_path / name).mkdir()
    step = {"name": "List", "kind": "tool", "tool": "list_folder", "args": {"path": str(tmp_path) + "/{{item}}"}}
    rec = await WorkflowEngine(platform).run(load_workflow(_loop(step)))
    outs = json.loads(rec.outputs_json)
    assert rec.status == "completed", outs
    assert outs["Each client"]["summary"] == "3 of 3 done"
    rows = _rows(platform, rec.id, "list_folder")
    assert len(rows) == 3 and all(r.ok for r in rows)


async def test_an_if_path_meets_the_same_gate(tmp_path):
    platform = build_platform(str(tmp_path))
    _quiet_outbound(platform)
    wf = {
        "name": "decide",
        "steps": [
            {
                "name": "Check",
                "kind": "if",
                "condition": {"left": "yes", "op": "is", "right": "yes"},
                "steps": [{
                    "name": "Wire it", "kind": "tool", "tool": "webhook_add",
                    "args": {"slug": "out-if", "direction": "outbound", "target_url": f"https://{HOST}/x",
                             "event_types": ["session.completed"]},
                }],
            }
        ],
    }
    rec = await WorkflowEngine(platform).run(load_workflow(wf))
    outs = json.loads(rec.outputs_json)
    assert "out-if" not in _webhooks(platform)
    assert outs["Check/then/Wire it"]["status"] == "failed"
    assert rec.status == "failed"
    rows = _rows(platform, rec.id, "webhook_add")
    assert len(rows) == 1 and rows[0].ok is False
