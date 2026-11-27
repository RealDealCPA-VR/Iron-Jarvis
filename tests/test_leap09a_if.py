"""Idea 09, phase 09-a: "If".

Two paths, one taken, decided once by the app itself from the run's own
record (``workflows/logic``, no model). Through real runs of the real engine:

* only the taken path runs; the other path's steps read "skipped";
* the If's own line says which path and why ("Then: “5,200.00” is over
  “5000”.");
* a comparison that cannot be made (a word where a number is needed) FAILS
  the If with words, and its own "if this fails" decides the rest (halt
  stops the run, skip goes on and the run ends "Part failed");
* a failed step on the taken path follows the same rules;
* a resume never decides again: the recorded path is walked.
"""

from __future__ import annotations

# Register the workflow tables BEFORE any platform is built.
import iron_jarvis.workflows.models  # noqa: F401

import asyncio
import json

from iron_jarvis.core.db import session_scope
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import Reversibility, Tool, ToolContext, ToolResult
from iron_jarvis.workflows.engine import WorkflowEngine, load_workflow
from iron_jarvis.workflows.models import WorkflowRunRecord, reconcile_interrupted_runs


class Value(Tool):
    """Hands the workflow a value to decide on (its data's ``total``)."""

    name = "t_leap09a_value"
    reversibility = Reversibility.READONLY
    input_schema = {"type": "object", "properties": {}}
    description = "test-only"

    def __init__(self) -> None:
        self.total = "5,200.00"

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        return ToolResult(ok=True, output=f"the total is {self.total}", data={"total": self.total})


class Doer(Tool):
    """Records what it was asked to do; fails when told to."""

    reversibility = Reversibility.READONLY
    input_schema = {"type": "object", "properties": {}}
    description = "test-only"

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[dict] = []
        self.fail = False
        self.hold = False
        self.holding = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        self.calls.append(dict(args))
        if self.hold:
            self.holding.set()
            await self.release.wait()
        if self.fail:
            return ToolResult(ok=False, error="the doer could not do it")
        return ToolResult(ok=True, output=f"did {args.get('what', '')}")


def _setup(tmp_path):
    platform = build_platform(str(tmp_path))
    value = Value()
    big, small = Doer("t_leap09a_big"), Doer("t_leap09a_small")
    for t in (value, big, small):
        platform.registry.register(t)
    engine = WorkflowEngine(platform)
    told: list[str] = []
    engine._deliver = lambda message, **kw: (told.append(message) or True, "sent")  # type: ignore[method-assign]
    return platform, engine, value, big, small, told


def _wf(**block) -> dict:
    return {
        "name": "decide",
        "steps": [
            {"name": "Read", "kind": "tool", "tool": "t_leap09a_value"},
            {
                "name": "Check",
                "kind": "if",
                "condition": {"left": "{{Read.data.total}}", "op": "over", "right": "5000"},
                "steps": [
                    {"name": "Big one", "kind": "tool", "tool": "t_leap09a_big", "args": {"what": "big"}},
                    {"name": "Tell big", "kind": "notify", "message": "Big: {{Read.data.total}} ({{Big one}})"},
                ],
                "else_steps": [
                    {"name": "Small one", "kind": "tool", "tool": "t_leap09a_small", "args": {"what": "small"}},
                ],
                **block,
            },
            {"name": "End", "kind": "notify", "message": "Done: {{Check}}"},
        ],
    }


def _outs(rec) -> dict:
    return json.loads(rec.outputs_json)


async def test_only_the_taken_path_runs_and_the_other_reads_skipped(tmp_path):
    _, engine, value, big, small, told = _setup(tmp_path)
    rec = await engine.run(load_workflow(_wf()))
    outs = _outs(rec)
    assert rec.status == "completed", outs
    assert big.calls == [{"what": "big"}] and small.calls == []
    assert outs["Check/then/Big one"]["status"] == "completed"
    assert outs["Check/then/Tell big"]["status"] == "completed"
    assert outs["Check/else/Small one"] == {"status": "skipped", "kind": "tool"}
    check = outs["Check"]
    assert check["status"] == "completed" and check["took"] == "then"
    assert check["summary"] == "Then: “5,200.00” is over “5000”."
    # Inside the path, its own steps' values are there by plain name.
    assert told[0] == "Big: 5,200.00 (did big)"
    assert told[1] == "Done: Then: “5,200.00” is over “5000”."


async def test_the_otherwise_path_runs_when_the_condition_does_not_hold(tmp_path):
    _, engine, value, big, small, told = _setup(tmp_path)
    value.total = "4,999.99"
    rec = await engine.run(load_workflow(_wf()))
    outs = _outs(rec)
    assert rec.status == "completed"
    assert big.calls == [] and small.calls == [{"what": "small"}]
    assert outs["Check/then/Big one"]["status"] == "skipped"
    assert outs["Check/then/Tell big"]["status"] == "skipped"
    assert outs["Check"]["took"] == "else"
    assert outs["Check"]["summary"] == "Otherwise: “4,999.99” is not over “5000”."
    assert told == ["Done: Otherwise: “4,999.99” is not over “5000”."]


async def test_a_word_where_a_number_is_needed_fails_the_if_with_words(tmp_path):
    _, engine, value, big, small, told = _setup(tmp_path)
    value.total = "pending"
    rec = await engine.run(load_workflow(_wf()))
    outs = _outs(rec)
    assert rec.status == "failed"
    assert big.calls == [] and small.calls == [], "a path ran on a guess"
    check = outs["Check"]
    assert check["status"] == "failed"
    assert check["summary"] == (
        "The If “Check” could not decide: “pending” is not a number, so it cannot be "
        "compared with “is over”. Nothing was guessed."
    )
    assert outs["End"]["status"] == "skipped"
    assert told == []


async def test_an_if_set_to_skip_goes_on_and_the_run_ends_part_failed(tmp_path):
    _, engine, value, big, small, told = _setup(tmp_path)
    value.total = "pending"
    rec = await engine.run(load_workflow(_wf(on_failure="skip")))
    outs = _outs(rec)
    assert rec.status == "completed_with_failures"
    assert outs["Check"]["handled"] == "skipped"
    assert outs["End"]["status"] == "completed"
    assert told[0].startswith("Done: [step Check failed: The If “Check” could not decide")


async def test_a_failed_step_on_the_taken_path_stops_the_run(tmp_path):
    _, engine, value, big, small, told = _setup(tmp_path)
    big.fail = True
    rec = await engine.run(load_workflow(_wf()))
    outs = _outs(rec)
    assert rec.status == "failed"
    assert outs["Check/then/Big one"]["status"] == "failed"
    assert outs["Check/then/Tell big"]["status"] == "skipped"
    assert outs["Check"]["status"] == "failed"
    assert outs["Check"]["summary"].startswith("Then: “5,200.00” is over “5000”. “Big one” failed: the doer could not do it")
    assert outs["End"]["status"] == "skipped"


async def test_a_value_nothing_gave_stops_the_if_by_name(tmp_path):
    _, engine, value, big, small, told = _setup(tmp_path)
    wf = _wf()
    wf["steps"][1]["condition"] = {"left": "{{Client}}", "op": "is", "right": "ACME"}
    rec = await engine.run(load_workflow(wf))
    outs = _outs(rec)
    assert outs["Check"]["summary"] == "could not run: no value for Client"
    assert big.calls == [] and small.calls == []
    # Given as a run input, the same If decides on it.
    _, engine2, _, big2, small2, _ = _setup(tmp_path / "again")
    wf2 = load_workflow(wf)
    rec2 = await asyncio.to_thread(engine2.create_record, wf2, {"Client": "acme"})
    done = await engine2.run_record(rec2, wf2, inputs={"Client": "acme"})
    assert _outs(done)["Check"]["took"] == "then" and big2.calls and not small2.calls


async def test_a_resume_walks_the_recorded_path_and_never_decides_again(tmp_path):
    """A run stopped inside the Then lane resumes in it, even when a step the
    If read is run again on the resume and now gives another value (a step
    that failed and was skipped gets its chance again on a resume)."""
    platform, engine, value, big, small, told = _setup(tmp_path)
    reads = {"n": 0}
    real = value.execute

    async def first_fails(args, ctx):
        reads["n"] += 1
        if reads["n"] == 1:
            return ToolResult(ok=False, error="the reader was busy")
        return await real(args, ctx)

    value.execute = first_fails  # type: ignore[method-assign]
    wf = _wf(condition={"left": "{{Read}}", "op": "contains", "right": "failed"})
    wf["steps"][0]["on_failure"] = "skip"
    wf = load_workflow(wf)
    rec = await asyncio.to_thread(engine.create_record, wf)
    big.hold = True
    driver = asyncio.ensure_future(engine.run_record(rec, wf))
    await asyncio.wait_for(big.holding.wait(), 30)
    driver.cancel()
    try:
        await driver
    except asyncio.CancelledError:
        pass
    reconcile_interrupted_runs(platform.engine)
    with session_scope(platform.engine) as db:
        stopped = db.get(WorkflowRunRecord, rec.id)
    assert _outs(stopped)["Check"]["status"] == "running"
    assert _outs(stopped)["Check"]["took"] == "then"
    big.hold = False
    big.calls.clear()
    done = await engine.resume_interrupted(stopped)
    outs = _outs(done)
    # "Read" ran again and worked this time: deciding again would take Otherwise.
    assert reads["n"] == 2 and outs["Read"]["status"] == "completed"
    assert done.status == "completed", outs
    assert big.calls == [{"what": "big"}] and small.calls == []
    assert outs["Check"]["took"] == "then"
