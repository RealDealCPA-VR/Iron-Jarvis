"""Idea 09, phase 09-a: an "Ask you" step inside a block.

An ask inside a For each (or an If) PARKS the run like any ask, with its
path in ``waiting_json`` (``step`` is the path-qualified key, ``at`` where
its definition is). The answer is folded under that key and the run is
driven again from the block, which walks its stored list and its recorded
path past every step already finished, so the next item asks again and
nothing done is done twice. Everything is rebuilt from the database: a
fresh engine (a restart) answers the same parked run.
"""

from __future__ import annotations

# Register the workflow tables BEFORE any platform is built.
import iron_jarvis.workflows.models  # noqa: F401

import json

from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.daemon.app import create_app
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import Reversibility, Tool, ToolContext, ToolResult
from iron_jarvis.workflows.engine import WorkflowEngine, load_workflow
from iron_jarvis.workflows.models import WorkflowRunRecord


class Doer(Tool):
    name = "t_leap09a_send"
    reversibility = Reversibility.READONLY
    input_schema = {"type": "object", "properties": {}}
    description = "test-only"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        self.calls.append(dict(args))
        return ToolResult(ok=True, output=f"sent {args.get('who')}: {args.get('answer')}")


WF = {
    "name": "confirm each",
    "steps": [
        {
            "name": "Each client",
            "kind": "for_each",
            "over": {"from": '["ACME", "Globex", "Initech"]'},
            "steps": [
                {"name": "Send?", "kind": "ask", "message": "Send {{item}} its letter?"},
                {"name": "Send", "kind": "tool", "tool": "t_leap09a_send", "args": {"who": "{{item}}", "answer": "{{Send?}}"}},
            ],
        },
        {"name": "Tell", "kind": "notify", "message": "Letters: {{Each client}}"},
    ],
}


def _setup(tmp_path):
    platform = build_platform(str(tmp_path))
    doer = Doer()
    platform.registry.register(doer)
    return platform, doer


def _engine(platform):
    engine = WorkflowEngine(platform)
    told: list[str] = []
    engine._deliver = lambda message, **kw: (told.append(message) or True, "sent")  # type: ignore[method-assign]
    return engine, told


def _row(platform, run_id) -> WorkflowRunRecord:
    with session_scope(platform.engine) as db:
        return db.get(WorkflowRunRecord, run_id)


async def test_each_item_asks_parks_with_its_path_and_goes_on_after_the_answer(tmp_path):
    platform, doer = _setup(tmp_path)
    engine, told = _engine(platform)
    rec = await engine.run(load_workflow(WF))
    assert rec.status == "waiting"
    w = json.loads(rec.waiting_json)
    assert w == {
        "index": 0,
        "step": "Each client/1/Send?",
        "question": "Send ACME its letter?",
        "at": [0, "steps", 0],
    }
    assert doer.calls == []
    block = json.loads(rec.outputs_json)["Each client"]
    assert block["status"] == "running" and block["list"] == ["ACME", "Globex", "Initech"]

    # A restart: a NEW engine answers, from the database alone.
    engine2, told2 = _engine(platform)
    second = await engine2.resume_after_answer(_row(platform, rec.id), "yes")
    assert second.status == "waiting"
    assert json.loads(second.waiting_json)["step"] == "Each client/2/Send?"
    assert json.loads(second.waiting_json)["question"] == "Send Globex its letter?"
    assert doer.calls == [{"who": "ACME", "answer": "User answered: yes"}]

    third = await engine2.resume_after_answer(_row(platform, rec.id), "not now")
    assert json.loads(third.waiting_json)["step"] == "Each client/3/Send?"
    done = await engine2.resume_after_answer(_row(platform, rec.id), "yes")
    assert done.status == "completed", json.loads(done.outputs_json)
    assert [c["who"] for c in doer.calls] == ["ACME", "Globex", "Initech"], "an item was sent twice"
    assert doer.calls[1]["answer"] == "User answered: not now"
    outs = json.loads(done.outputs_json)
    assert outs["Each client"]["summary"] == "3 of 3 done"
    assert outs["Each client/2/Send?"]["summary"] == "User answered: not now"
    assert told2[-1] == "Letters: 3 of 3 done"
    # Each parked question went out once.
    asked = [t for t in told + told2 if "needs you" in t]
    assert [a.split("needs you: ")[1].split("?")[0] for a in asked] == [
        "Send ACME its letter", "Send Globex its letter", "Send Initech its letter",
    ]


async def test_an_item_that_failed_and_was_skipped_is_not_tried_again_after_an_answer(tmp_path):
    """The answer drives the run again from the block; the items it already
    settled stay as they ended (a failed send is not sent again)."""
    platform, doer = _setup(tmp_path)
    engine, _ = _engine(platform)
    real = doer.execute

    async def fails_for_globex(args, ctx):
        if args.get("who") == "Globex":
            doer.calls.append(dict(args))
            return ToolResult(ok=False, error="the mailbox refused it")
        return await real(args, ctx)

    doer.execute = fails_for_globex  # type: ignore[method-assign]
    wf = {
        "name": "send, then ask at the end",
        "steps": [
            {
                "name": "Each client",
                "kind": "for_each",
                "on_failure": "skip",
                "over": {"from": '["ACME", "Globex", "Initech"]'},
                "steps": [
                    {"name": "Send", "kind": "tool", "tool": "t_leap09a_send", "args": {"who": "{{item}}"}},
                    {
                        "name": "Last?",
                        "kind": "if",
                        "condition": {"left": "{{index}}", "op": "is", "right": "3"},
                        "steps": [{"name": "OK?", "kind": "ask", "message": "All sent. Close the batch?"}],
                    },
                ],
            },
        ],
    }
    rec = await engine.run(load_workflow(wf))
    assert rec.status == "waiting"
    assert [c["who"] for c in doer.calls] == ["ACME", "Globex", "Initech"]
    done = await engine.resume_after_answer(_row(platform, rec.id), "yes")
    assert [c["who"] for c in doer.calls] == ["ACME", "Globex", "Initech"], "a settled item ran again"
    outs = json.loads(done.outputs_json)
    assert done.status == "completed_with_failures"
    assert outs["Each client"]["summary"] == "2 of 3 done, 1 failed"


async def test_an_ask_on_an_if_path_inside_a_for_each(tmp_path):
    platform, doer = _setup(tmp_path)
    engine, _ = _engine(platform)
    wf = {
        "name": "only the big ones",
        "steps": [
            {
                "name": "Each",
                "kind": "for_each",
                "over": {"from": '[{"name": "a", "total": 10}, {"name": "b", "total": 9000}]'},
                "steps": [
                    {
                        "name": "Big?",
                        "kind": "if",
                        "condition": {"left": "{{item.total}}", "op": "at_least", "right": "5000"},
                        "steps": [{"name": "Ok?", "kind": "ask", "message": "{{item.name}} is {{item.total}}. Send?"}],
                        "else_steps": [{"name": "Send", "kind": "tool", "tool": "t_leap09a_send", "args": {"who": "{{item.name}}"}}],
                    },
                ],
            },
        ],
    }
    rec = await engine.run(load_workflow(wf))
    assert rec.status == "waiting"
    w = json.loads(rec.waiting_json)
    assert w["step"] == "Each/2/Big?/then/Ok?" and w["at"] == [0, "steps", 0, "steps", 0]
    assert w["question"] == "b is 9000. Send?"
    assert [c["who"] for c in doer.calls] == ["a"]
    done = await engine.resume_after_answer(_row(platform, rec.id), "yes")
    assert done.status == "completed"
    outs = json.loads(done.outputs_json)
    assert outs["Each/2/Big?/then/Ok?"]["summary"] == "User answered: yes"
    assert outs["Each/2/Big?/else/Send"]["status"] == "skipped"
    assert [c["who"] for c in doer.calls] == ["a"]


async def test_an_answer_that_fails_its_check_asks_once_more_then_stops_the_item(tmp_path):
    platform, doer = _setup(tmp_path)
    engine, told = _engine(platform)
    wf = json.loads(json.dumps(WF))
    wf["steps"][0]["steps"][0].update(expect={"summary_contains": ["yes"]}, on_failure="retry")
    rec = await engine.run(load_workflow(wf))
    again = await engine.resume_after_answer(_row(platform, rec.id), "maybe")
    assert again.status == "waiting"
    w = json.loads(again.waiting_json)
    assert w["step"] == "Each client/1/Send?" and w["expect_retries"] == 1 and w["at"] == [0, "steps", 0]
    final = await engine.resume_after_answer(_row(platform, rec.id), "still maybe")
    outs = json.loads(final.outputs_json)
    assert final.status == "failed", outs
    assert outs["Each client/1/Send?"]["status"] == "failed"
    assert "expectation failed" in outs["Each client/1/Send?"]["summary"]
    assert outs["Each client"]["status"] == "failed"
    assert doer.calls == [], "the item went on after its question failed"
    # It was asked twice, never a third time.
    assert len([t for t in told if "needs you" in t]) == 2


def test_a_run_parked_inside_a_block_is_answered_and_stopped_through_the_routes(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        platform = client.app.state.platform
        platform.registry.register(Doer())
        r = client.post("/workflows/run", json={"name": WF["name"], "steps": WF["steps"]})
        assert r.status_code == 200, r.text
        run_id = r.json()["id"]
        for _ in range(2000):
            row = client.get(f"/workflows/runs/{run_id}").json()
            if row["status"] == "waiting":
                break
        assert row["status"] == "waiting", row
        assert json.loads(row["waiting_json"])["step"] == "Each client/1/Send?"
        stop = client.post(f"/workflows/runs/{run_id}/cancel")
        assert stop.status_code == 200 and stop.json()["status"] == "cancelled"
        row = client.get(f"/workflows/runs/{run_id}").json()
        assert row["status"] == "cancelled"
        # A run that ended never says its block is still working.
        block = json.loads(row["outputs_json"])["Each client"]
        assert block["status"] == "cancelled" and "list" not in block
