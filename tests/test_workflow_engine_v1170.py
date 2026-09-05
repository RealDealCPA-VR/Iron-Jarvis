"""v1.170.0 — P1 engine-core: run inputs (contract 5), origin stamping
(contract 6), tool-step structured data + ``{{Step.data}}`` templating
(contract 7), verified steps / ``expect`` (contract 8), the ``expect`` field's
serialize round-trip, and the resume-from-interrupted seam (contract 4's
engine half).

Offline throughout: agent steps ride the mock provider; tool steps hit the
real registry (plus a registered fake READONLY tool where a controlled
``data``/``created_paths`` payload is needed); resume runs against the real
database.
"""

from __future__ import annotations

# Register workflow tables on SQLModel.metadata BEFORE any platform is built.
import iron_jarvis.workflows.models  # noqa: F401

import json

import pytest

from iron_jarvis.comm.channels import MockChannel
from iron_jarvis.core.db import dumps, session_scope
from iron_jarvis.core.ids import utcnow
from iron_jarvis.core.models import Session
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import Reversibility, Tool, ToolContext, ToolResult
from iron_jarvis.workflows.engine import (
    Step,
    WorkflowDef,
    WorkflowEngine,
    load_workflow,
    render_template,
    seed_inputs,
    step_to_dict,
)
from iron_jarvis.workflows.models import WorkflowRunRecord


def _mock_channel(platform) -> MockChannel:
    return next(
        ch for ch in platform.notifier._channels.values() if isinstance(ch, MockChannel)
    )


class FakeDataTool(Tool):
    """READONLY test tool with a controllable result payload. READONLY rides
    the engine's self-grant path, so no interactive approval is involved."""

    name = "fake_data_tool"
    description = "test-only data-returning tool"
    input_schema = {"type": "object", "properties": {}}
    reversibility = Reversibility.READONLY

    def __init__(self, data=None, created_paths=None, ok=True, output="made stuff"):
        self.calls = 0
        self._data = data
        self._paths = created_paths
        self._ok = ok
        self._output = output

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        self.calls += 1
        if not self._ok:
            return ToolResult(ok=False, error="boom", data=self._data)
        return ToolResult(
            ok=True,
            output=self._output,
            data=self._data,
            created_paths=self._paths,
        )


# --------------------------------------------------------------------------- #
# serialize round-trip: the `expect` field (contract 8 plumbing)
# --------------------------------------------------------------------------- #


def test_expect_round_trips_through_step_to_dict_and_load():
    s = Step(
        name="Prove",
        kind="tool",
        tool="list_folder",
        expect={"files": ["report.md"], "summary_contains": ["done"]},
    )
    d = step_to_dict(s)
    assert d["expect"] == {"files": ["report.md"], "summary_contains": ["done"]}
    wf = load_workflow({"name": "x", "steps": [d]})
    assert wf.steps[0].expect == {"files": ["report.md"], "summary_contains": ["done"]}


def test_expect_coercion_is_lenient_for_old_rows_and_garbage():
    wf = load_workflow(
        {
            "name": "x",
            "steps": [
                {"name": "old"},  # pre-v1.170.0 row: no expect key at all
                {"name": "junk", "expect": "not a dict"},
                {"name": "half", "expect": {"files": "not a list", "summary_contains": [" ok ", "", 7]}},
                {"name": "unknown", "expect": {"weird_key": ["x"]}},
            ],
        }
    )
    assert wf.steps[0].expect == {}
    assert wf.steps[1].expect == {}
    # Recognized key keeps stripped non-blank entries (7 -> "7" is a string).
    assert wf.steps[2].expect == {"summary_contains": ["ok", "7"]}
    assert wf.steps[3].expect == {}


# --------------------------------------------------------------------------- #
# templating: {{Step Name.data}} (contract 7)
# --------------------------------------------------------------------------- #


def test_data_template_resolves_to_the_json_string():
    outs = {"Make": {"status": "completed", "summary": "prose", "data": '{"n": 5}'}}
    assert render_template("payload={{Make.data}}", outs) == 'payload={"n": 5}'
    # The plain reference still renders the summary — unchanged.
    assert render_template("{{Make}}", outs) == "prose"


def test_data_template_exact_step_name_wins_the_reference():
    # A step literally named "X.data" owns {{X.data}} — the reserved-name rule.
    outs = {
        "X.data": {"status": "completed", "summary": "OWN"},
        "X": {"status": "completed", "summary": "s", "data": '"json"'},
    }
    assert render_template("{{X.data}}", outs) == "OWN"


def test_data_template_unknown_or_missing_data_renders_empty():
    outs = {"Make": {"status": "completed", "summary": "prose"}}
    assert render_template("a{{Make.data}}b", outs) == "ab"
    assert render_template("a{{Nope.data}}b", outs) == "ab"


# --------------------------------------------------------------------------- #
# run inputs (contract 5)
# --------------------------------------------------------------------------- #


async def test_inputs_seed_template_and_flow_through_a_run(tmp_path):
    platform = build_platform(str(tmp_path))
    mock = _mock_channel(platform)
    engine = WorkflowEngine(platform)
    wf = WorkflowDef(
        name="greet",
        steps=[Step(name="Tell", kind="notify", message="Hi {{Client}}")],
    )
    rec = engine.create_record(wf, inputs={"Client": "Acme LLC"})
    # Seeded at CREATION — the record is honest from its first read.
    seeded = json.loads(rec.outputs_json)["Client"]
    assert seeded == {"status": "completed", "summary": "Acme LLC", "kind": "input"}
    rec = await engine.run_record(rec, wf, inputs={"Client": "Acme LLC"})
    assert rec.status == "completed"
    outs = json.loads(rec.outputs_json)
    assert outs["Client"]["kind"] == "input"
    assert outs["Tell"]["status"] == "completed"
    assert any("Hi Acme LLC" in m for m in mock.sent)


def test_input_values_are_clipped_and_coerced(tmp_path):
    wf = WorkflowDef(name="w", steps=[Step(name="S", kind="notify", message="m")])
    seeded = seed_inputs(wf, {"Big": "x" * 5000, "Num": 42})
    assert len(seeded["Big"]["summary"]) == 4000
    assert seeded["Num"]["summary"] == "42"


def test_input_colliding_with_a_step_name_raises_honestly(tmp_path):
    platform = build_platform(str(tmp_path))
    wf = WorkflowDef(name="w", steps=[Step(name="Gather", task="collect")])
    with pytest.raises(ValueError) as exc:
        WorkflowEngine(platform).create_record(wf, inputs={"Gather": "boom"})
    assert "Gather" in str(exc.value)
    assert "collides" in str(exc.value)


def test_blank_input_name_raises():
    wf = WorkflowDef(name="w", steps=[Step(name="S", task="t")])
    with pytest.raises(ValueError):
        seed_inputs(wf, {"  ": "value"})


async def test_explicit_outputs_win_the_merge_and_coexist_with_inputs(tmp_path):
    # The resume path replays outputs that ALREADY contain the seeds — a
    # re-seed must not overwrite later truth. And a reflex Trigger seed must
    # coexist with inputs.
    platform = build_platform(str(tmp_path))
    engine = WorkflowEngine(platform)
    wf = WorkflowDef(
        name="w",
        steps=[Step(name="Tell", kind="notify", message="{{X}} {{Trigger}}")],
    )
    rec = engine.create_record(wf)
    rec = await engine.run_record(
        rec,
        wf,
        outputs={
            "X": {"status": "completed", "summary": "from-outputs", "kind": "input"},
            "Trigger": {"status": "completed", "summary": "webhook", "kind": "trigger"},
        },
        inputs={"X": "from-input"},
    )
    outs = json.loads(rec.outputs_json)
    assert outs["X"]["summary"] == "from-outputs"
    assert outs["Trigger"]["summary"] == "webhook"


# --------------------------------------------------------------------------- #
# origin stamping (contract 6)
# --------------------------------------------------------------------------- #


async def test_agent_step_sessions_carry_the_workflow_origin(tmp_path):
    platform = build_platform(str(tmp_path))
    wf = WorkflowDef(name="orig-wf", steps=[Step(name="Work", task="do a thing")])
    rec = await WorkflowEngine(platform).run(wf)
    assert rec.status == "completed"
    sids = json.loads(rec.session_ids_json)
    assert len(sids) == 1
    with session_scope(platform.engine) as db:
        session = db.get(Session, sids[0])
    assert session.origin == "workflow:orig-wf"


async def test_origin_is_clipped_to_64_chars(tmp_path):
    platform = build_platform(str(tmp_path))
    long_name = "n" * 100
    wf = WorkflowDef(name=long_name, steps=[Step(name="Work", task="do a thing")])
    rec = await WorkflowEngine(platform).run(wf)
    sids = json.loads(rec.session_ids_json)
    with session_scope(platform.engine) as db:
        session = db.get(Session, sids[0])
    assert session.origin == (f"workflow:{long_name}")[:64]
    assert len(session.origin) == 64


# --------------------------------------------------------------------------- #
# tool-step structured data (contract 7)
# --------------------------------------------------------------------------- #


async def test_tool_step_records_bounded_data_and_created_paths(tmp_path):
    platform = build_platform(str(tmp_path))
    mock = _mock_channel(platform)
    tool = FakeDataTool(
        data={"n": 5, "label": "ok"},
        created_paths=[str(tmp_path / "made.txt")],
    )
    platform.registry.register(tool)
    wf = WorkflowDef(
        name="datawf",
        steps=[
            Step(name="Make", kind="tool", tool="fake_data_tool"),
            Step(name="Tell", kind="notify", message="payload: {{Make.data}}"),
        ],
    )
    rec = await WorkflowEngine(platform).run(wf)
    assert rec.status == "completed"
    outs = json.loads(rec.outputs_json)
    assert json.loads(outs["Make"]["data"]) == {"n": 5, "label": "ok"}
    assert outs["Make"]["created_paths"] == [str(tmp_path / "made.txt")]
    # {{Make.data}} handed the JSON string to the downstream step.
    assert any('"n": 5' in m for m in mock.sent)


async def test_tool_step_data_is_clipped_to_8000_chars(tmp_path):
    platform = build_platform(str(tmp_path))
    platform.registry.register(FakeDataTool(data={"big": "x" * 20000}))
    wf = WorkflowDef(
        name="bigdata", steps=[Step(name="Make", kind="tool", tool="fake_data_tool")]
    )
    rec = await WorkflowEngine(platform).run(wf)
    out = json.loads(rec.outputs_json)["Make"]
    assert len(out["data"]) == 8000


async def test_failed_tool_records_no_data(tmp_path):
    platform = build_platform(str(tmp_path))
    platform.registry.register(FakeDataTool(data={"n": 1}, ok=False))
    wf = WorkflowDef(
        name="faildata", steps=[Step(name="Make", kind="tool", tool="fake_data_tool")]
    )
    rec = await WorkflowEngine(platform).run(wf)
    out = json.loads(rec.outputs_json)["Make"]
    assert out["status"] == "failed"
    assert "data" not in out
    assert "created_paths" not in out


# --------------------------------------------------------------------------- #
# verified steps (contract 8)
# --------------------------------------------------------------------------- #


async def test_tool_expect_files_pass_via_created_paths(tmp_path):
    platform = build_platform(str(tmp_path))
    platform.registry.register(
        FakeDataTool(created_paths=[str(tmp_path / "out" / "report.md")])
    )
    wf = WorkflowDef(
        name="proved",
        steps=[
            Step(
                name="Make",
                kind="tool",
                tool="fake_data_tool",
                expect={"files": ["report.md"], "summary_contains": ["made stuff"]},
            )
        ],
    )
    rec = await WorkflowEngine(platform).run(wf)
    assert rec.status == "completed"
    assert json.loads(rec.outputs_json)["Make"]["status"] == "completed"


async def test_tool_expect_missing_file_fails_naming_the_expectation(tmp_path):
    platform = build_platform(str(tmp_path))
    platform.registry.register(FakeDataTool(created_paths=[str(tmp_path / "other.txt")]))
    wf = WorkflowDef(
        name="unproved",
        steps=[
            Step(
                name="Make",
                kind="tool",
                tool="fake_data_tool",
                expect={"files": ["report.md"]},
            ),
            Step(name="Never", kind="notify", message="unreachable"),
        ],
    )
    rec = await WorkflowEngine(platform).run(wf)
    assert rec.status == "failed"  # default on_failure=halt applies as usual
    outs = json.loads(rec.outputs_json)
    assert outs["Make"]["status"] == "failed"
    assert outs["Make"]["summary"].startswith("expectation failed:")
    assert "report.md" in outs["Make"]["summary"]
    assert outs["Make"]["expect_failed"]
    assert outs["Never"]["status"] == "skipped"


async def test_expect_summary_contains_is_case_insensitive(tmp_path):
    platform = build_platform(str(tmp_path))
    wf = WorkflowDef(
        name="casewf",
        steps=[
            Step(
                name="Tell",
                kind="notify",
                message="Hello World",
                expect={"summary_contains": ["hello"]},
            )
        ],
    )
    rec = await WorkflowEngine(platform).run(wf)
    assert rec.status == "completed"


async def test_expect_summary_contains_miss_fails_with_the_needle(tmp_path):
    platform = build_platform(str(tmp_path))
    wf = WorkflowDef(
        name="misswf",
        steps=[
            Step(
                name="Tell",
                kind="notify",
                message="Hello World",
                expect={"summary_contains": ["absent-needle"]},
            )
        ],
    )
    rec = await WorkflowEngine(platform).run(wf)
    assert rec.status == "failed"
    summary = json.loads(rec.outputs_json)["Tell"]["summary"]
    assert "absent-needle" in summary


async def test_files_expectation_on_a_notify_step_fails_honestly(tmp_path):
    platform = build_platform(str(tmp_path))
    wf = WorkflowDef(
        name="notifiles",
        steps=[
            Step(
                name="Tell",
                kind="notify",
                message="hi",
                expect={"files": ["report.md"]},
            )
        ],
    )
    rec = await WorkflowEngine(platform).run(wf)
    assert rec.status == "failed"
    assert "'notify'" in json.loads(rec.outputs_json)["Tell"]["summary"]


async def test_expect_failure_routes_through_on_failure_skip(tmp_path):
    platform = build_platform(str(tmp_path))
    platform.registry.register(FakeDataTool())
    wf = WorkflowDef(
        name="skipwf",
        steps=[
            Step(
                name="Make",
                kind="tool",
                tool="fake_data_tool",
                on_failure="skip",
                expect={"files": ["never.md"]},
            ),
            Step(name="After", kind="notify", message="carried on"),
        ],
    )
    rec = await WorkflowEngine(platform).run(wf)
    assert rec.status == "completed"
    outs = json.loads(rec.outputs_json)
    assert outs["Make"]["status"] == "failed"
    assert outs["Make"]["handled"] == "skipped"
    assert outs["After"]["status"] == "completed"


async def test_expect_failure_routes_through_on_failure_retry(tmp_path):
    platform = build_platform(str(tmp_path))
    tool = FakeDataTool()
    platform.registry.register(tool)
    wf = WorkflowDef(
        name="retrywf",
        steps=[
            Step(
                name="Make",
                kind="tool",
                tool="fake_data_tool",
                on_failure="retry",
                expect={"summary_contains": ["never-there"]},
            )
        ],
    )
    rec = await WorkflowEngine(platform).run(wf)
    assert rec.status == "failed"
    assert tool.calls == 2  # the expectation failure earned a real re-attempt


async def test_agent_expect_files_checks_the_session_ledger(tmp_path, monkeypatch):
    platform = build_platform(str(tmp_path))
    import iron_jarvis.agents.outcome as outcome

    seen: list[str] = []

    def fake_session_result(engine, session_id):
        seen.append(session_id)
        return {"files_created": ["report.md"], "files_changed": ["notes/log.txt"]}

    monkeypatch.setattr(outcome, "session_result", fake_session_result)
    wf = WorkflowDef(
        name="agentproof",
        steps=[
            Step(
                name="Work",
                task="write the report",
                expect={"files": ["report.md", "log.txt"]},
            )
        ],
    )
    rec = await WorkflowEngine(platform).run(wf)
    assert rec.status == "completed"
    # The check read THIS run's session, from the ledger derivation. Since
    # v1.227.0 the orchestrator ALSO reads the ledger once at finalize (the
    # honest ``outcome``), so the spy sees the same id more than once — the
    # assertion is about WHICH session was read, never how many times.
    assert set(seen) == set(json.loads(rec.session_ids_json))


async def test_agent_expect_files_fails_when_the_ledger_disagrees(
    tmp_path, monkeypatch
):
    platform = build_platform(str(tmp_path))
    import iron_jarvis.agents.outcome as outcome

    monkeypatch.setattr(
        outcome,
        "session_result",
        lambda engine, session_id: {"files_created": [], "files_changed": []},
    )
    wf = WorkflowDef(
        name="agentliar",
        steps=[Step(name="Work", task="write it", expect={"files": ["report.md"]})],
    )
    rec = await WorkflowEngine(platform).run(wf)
    assert rec.status == "failed"
    summary = json.loads(rec.outputs_json)["Work"]["summary"]
    assert "report.md" in summary and "expectation failed" in summary


async def test_no_expect_means_zero_behavior_change(tmp_path):
    # The v1.121.0 shape byte-identically: a plain run's outputs carry no new
    # keys and no expectation machinery fires.
    platform = build_platform(str(tmp_path))
    wf = WorkflowDef(
        name="plain", steps=[Step(name="Tell", kind="notify", message="hi")]
    )
    rec = await WorkflowEngine(platform).run(wf)
    assert rec.status == "completed"
    out = json.loads(rec.outputs_json)["Tell"]
    assert "expect_failed" not in out
    assert set(out) == {"status", "summary", "kind"}


# --------------------------------------------------------------------------- #
# resume-from-interrupted (contract 4, engine seam)
# --------------------------------------------------------------------------- #


def _seed_record(platform, *, status, steps, outputs, finished=True):
    rec = WorkflowRunRecord(
        workflow_name="resumable",
        status=status,
        steps_json=dumps([step_to_dict(s) for s in steps]),
        outputs_json=dumps(outputs),
        session_ids_json="[]",
        finished_at=utcnow() if finished else None,
    )
    with session_scope(platform.engine) as db:
        db.add(rec)
        db.commit()
        db.refresh(rec)
    return rec


def test_rebuild_run_computes_the_first_non_completed_index(tmp_path):
    platform = build_platform(str(tmp_path))
    engine = WorkflowEngine(platform)
    steps = [
        Step(name="A", kind="notify", message="a"),
        Step(name="B", kind="notify", message="b"),
        Step(name="C", kind="notify", message="c"),
    ]
    rec = WorkflowRunRecord(
        workflow_name="w",
        status="interrupted",
        steps_json=dumps([step_to_dict(s) for s in steps]),
        outputs_json=dumps(
            {
                "A": {"status": "completed", "summary": "done", "kind": "notify"},
                "B": {"status": "skipped"},
            }
        ),
        session_ids_json="[]",
    )
    wf, outputs, session_ids, start = engine.rebuild_run(rec)
    assert [s.name for s in wf.steps] == ["A", "B", "C"]
    assert start == 1  # skipped is NOT completed — it gets its chance
    assert outputs["A"]["summary"] == "done"
    assert session_ids == []


def test_rebuild_run_all_completed_and_no_steps(tmp_path):
    platform = build_platform(str(tmp_path))
    engine = WorkflowEngine(platform)
    steps = [Step(name="A", kind="notify", message="a")]
    rec = WorkflowRunRecord(
        workflow_name="w",
        status="interrupted",
        steps_json=dumps([step_to_dict(s) for s in steps]),
        outputs_json=dumps({"A": {"status": "completed", "summary": "s"}}),
        session_ids_json="[]",
    )
    assert engine.rebuild_run(rec)[3] == 1  # == len(steps): nothing left to run
    empty = WorkflowRunRecord(workflow_name="w", status="interrupted")
    with pytest.raises(ValueError):
        engine.rebuild_run(empty)


async def test_resume_interrupted_runs_only_the_remaining_steps(tmp_path):
    platform = build_platform(str(tmp_path))
    mock = _mock_channel(platform)
    engine = WorkflowEngine(platform)
    rec = _seed_record(
        platform,
        status="interrupted",
        steps=[
            Step(name="One", kind="notify", message="first message"),
            Step(name="Two", kind="notify", message="second message"),
        ],
        outputs={"One": {"status": "completed", "summary": "already sent", "kind": "notify"}},
    )
    resumed = await engine.resume_interrupted(rec)
    assert resumed.status == "completed"
    outs = json.loads(resumed.outputs_json)
    assert outs["One"]["summary"] == "already sent"  # NOT re-run
    assert outs["Two"]["status"] == "completed"
    assert any("second message" in m for m in mock.sent)
    assert not any("first message" in m for m in mock.sent)
    assert resumed.finished_at is not None


async def test_resume_interrupted_reparks_on_a_pending_ask(tmp_path):
    platform = build_platform(str(tmp_path))
    engine = WorkflowEngine(platform)
    rec = _seed_record(
        platform,
        status="interrupted",
        steps=[
            Step(name="Approve", kind="ask", message="Go?"),
            Step(name="Send", kind="notify", message="sent"),
        ],
        outputs={},
    )
    resumed = await engine.resume_interrupted(rec)
    assert resumed.status == "waiting"  # the human gate re-parks honestly
    assert json.loads(resumed.waiting_json)["step"] == "Approve"


async def test_resume_interrupted_never_resurrects_a_cancel(tmp_path):
    platform = build_platform(str(tmp_path))
    mock = _mock_channel(platform)
    engine = WorkflowEngine(platform)
    rec = _seed_record(
        platform,
        status="interrupted",
        steps=[Step(name="Tell", kind="notify", message="zombie message")],
        outputs={},
    )
    # A cancel lands between the route's claim and the background resume.
    with session_scope(platform.engine) as db:
        row = db.get(WorkflowRunRecord, rec.id)
        row.status = "cancelled"
        db.add(row)
        db.commit()
    result = await engine.resume_interrupted(rec)
    assert result.status == "cancelled"
    assert not any("zombie message" in m for m in mock.sent)


# --------------------------------------------------------------------------- #
# reviewer-confirmed: resume must NEVER re-run a completed step
# --------------------------------------------------------------------------- #


async def test_resume_never_reruns_a_completed_step_after_a_handled_failure(
    tmp_path,
):
    # The confirmed repro: B failed with on_failure=skip (handled), C completed
    # AFTER it, the daemon died during D. The first non-completed index is B —
    # resume must give B its chance but must NOT re-deliver C's notification.
    platform = build_platform(str(tmp_path))
    mock = _mock_channel(platform)
    engine = WorkflowEngine(platform)
    rec = _seed_record(
        platform,
        status="interrupted",
        steps=[
            Step(name="B", kind="notify", message="msg-B", on_failure="skip"),
            Step(name="C", kind="notify", message="msg-C"),
            Step(name="D", kind="notify", message="msg-D"),
        ],
        outputs={
            "B": {
                "status": "failed",
                "summary": "delivery failed: down",
                "kind": "notify",
                "handled": "skipped",
            },
            "C": {"status": "completed", "summary": "notified: msg-C", "kind": "notify"},
        },
    )
    resumed = await engine.resume_interrupted(rec)
    assert resumed.status == "completed"
    assert any("msg-B" in m for m in mock.sent)  # the failed step re-attempted
    assert not any("msg-C" in m for m in mock.sent)  # completed = NOT re-run
    assert any("msg-D" in m for m in mock.sent)  # the remaining step ran
    outs = json.loads(resumed.outputs_json)
    assert outs["C"]["summary"] == "notified: msg-C"  # recorded output kept


# --------------------------------------------------------------------------- #
# reviewer-confirmed: expect on an ask step gates the ANSWER (contract 8)
# --------------------------------------------------------------------------- #


def _ask_gate_def(name, *, on_failure="halt", expect=None):
    return WorkflowDef(
        name=name,
        steps=[
            Step(
                name="Approve",
                kind="ask",
                message="Go?",
                on_failure=on_failure,
                expect=expect or {"summary_contains": ["approve"]},
            ),
            Step(name="Send", kind="notify", message="downstream-message"),
        ],
    )


async def test_ask_expect_summary_contains_gates_the_answer_and_passes(tmp_path):
    platform = build_platform(str(tmp_path))
    mock = _mock_channel(platform)
    engine = WorkflowEngine(platform)
    rec = await engine.run(_ask_gate_def("gate-pass"))
    assert rec.status == "waiting"
    resumed = await engine.resume_after_answer(rec, "APPROVED, go ahead")
    assert resumed.status == "completed"
    outs = json.loads(resumed.outputs_json)
    assert outs["Approve"]["status"] == "completed"
    assert any("downstream-message" in m for m in mock.sent)


async def test_ask_expect_failure_halts_the_run_with_honest_detail(tmp_path):
    platform = build_platform(str(tmp_path))
    mock = _mock_channel(platform)
    engine = WorkflowEngine(platform)
    rec = await engine.run(_ask_gate_def("gate-halt"))
    resumed = await engine.resume_after_answer(rec, "no way")
    assert resumed.status == "failed"
    outs = json.loads(resumed.outputs_json)
    assert outs["Approve"]["status"] == "failed"
    assert outs["Approve"]["summary"].startswith("expectation failed:")
    assert "approve" in outs["Approve"]["summary"]
    assert outs["Send"]["status"] == "skipped"
    assert resumed.finished_at is not None
    assert resumed.waiting_json == ""
    assert not any("downstream-message" in m for m in mock.sent)


async def test_ask_expect_failure_with_skip_continues_visibly(tmp_path):
    platform = build_platform(str(tmp_path))
    mock = _mock_channel(platform)
    engine = WorkflowEngine(platform)
    rec = await engine.run(_ask_gate_def("gate-skip", on_failure="skip"))
    resumed = await engine.resume_after_answer(rec, "no way")
    assert resumed.status == "completed"
    outs = json.loads(resumed.outputs_json)
    assert outs["Approve"]["status"] == "failed"
    assert outs["Approve"]["handled"] == "skipped"
    assert outs["Send"]["status"] == "completed"
    assert any("downstream-message" in m for m in mock.sent)


async def test_ask_expect_retry_reparks_the_question_once_then_fails(tmp_path):
    platform = build_platform(str(tmp_path))
    mock = _mock_channel(platform)
    engine = WorkflowEngine(platform)
    rec = await engine.run(_ask_gate_def("gate-retry", on_failure="retry"))
    first = await engine.resume_after_answer(rec, "nope")
    # A retry of an ask is asking AGAIN — re-parked, attempt tracked durably.
    assert first.status == "waiting"
    waiting = json.loads(first.waiting_json)
    assert waiting["step"] == "Approve"
    assert waiting["expect_retries"] == 1
    assert any("needs you again" in m for m in mock.sent)
    second = await engine.resume_after_answer(first, "still nope")
    assert second.status == "failed"  # one re-attempt only — never a loop
    outs = json.loads(second.outputs_json)
    assert outs["Approve"]["summary"].startswith("expectation failed:")
    assert not any("downstream-message" in m for m in mock.sent)


async def test_ask_expect_files_fails_honestly_on_the_answer(tmp_path):
    platform = build_platform(str(tmp_path))
    engine = WorkflowEngine(platform)
    rec = await engine.run(
        _ask_gate_def("gate-files", expect={"files": ["report.md"]})
    )
    resumed = await engine.resume_after_answer(rec, "done")
    assert resumed.status == "failed"
    summary = json.loads(resumed.outputs_json)["Approve"]["summary"]
    assert "'ask'" in summary  # an ask produces no files — named honestly


async def test_ask_without_expect_folds_the_answer_unchanged(tmp_path):
    # The v1.121.0 shape byte-identically when no expect is declared.
    platform = build_platform(str(tmp_path))
    engine = WorkflowEngine(platform)
    wf = WorkflowDef(
        name="plain-gate",
        steps=[
            Step(name="Approve", kind="ask", message="Go?"),
            Step(name="Send", kind="notify", message="after-answer"),
        ],
    )
    rec = await engine.run(wf)
    resumed = await engine.resume_after_answer(rec, "anything at all")
    assert resumed.status == "completed"
    out = json.loads(resumed.outputs_json)["Approve"]
    assert out == {
        "status": "completed",
        "summary": "User answered: anything at all",
        "kind": "ask",
    }


# --------------------------------------------------------------------------- #
# reviewer-confirmed: origin is charset-LAUNDERED, not just clipped
# --------------------------------------------------------------------------- #


async def test_origin_is_laundered_to_the_origin_charset(tmp_path):
    platform = build_platform(str(tmp_path))
    wf = WorkflowDef(
        name="Café & re/view", steps=[Step(name="Work", task="do a thing")]
    )
    rec = await WorkflowEngine(platform).run(wf)
    sids = json.loads(rec.session_ids_json)
    with session_scope(platform.engine) as db:
        session = db.get(Session, sids[0])
    assert session.origin == "workflow:Caf_ _ re_view"
    # The stamped value must round-trip through the HTTP schema's validator.
    from iron_jarvis.daemon.schemas import _ORIGIN_RE

    assert _ORIGIN_RE.fullmatch(session.origin)


# --------------------------------------------------------------------------- #
# reviewer-confirmed: a checker crash is named, never a raw step failure
# --------------------------------------------------------------------------- #


async def test_expect_checker_crash_is_reported_honestly(tmp_path, monkeypatch):
    platform = build_platform(str(tmp_path))
    import iron_jarvis.agents.outcome as outcome

    def boom(engine, session_id):
        raise RuntimeError("db is on fire")

    monkeypatch.setattr(outcome, "session_result", boom)
    wf = WorkflowDef(
        name="crashcheck",
        steps=[Step(name="Work", task="write it", expect={"files": ["report.md"]})],
    )
    rec = await WorkflowEngine(platform).run(wf)
    assert rec.status == "failed"  # on_failure=halt still applies as usual
    summary = json.loads(rec.outputs_json)["Work"]["summary"]
    assert summary.startswith("expectation failed: could not verify")
    assert "db is on fire" in summary
    assert "the step itself completed" in summary


# --------------------------------------------------------------------------- #
# reviewer-confirmed: caller-seeded outputs persist BEFORE the first batch
# --------------------------------------------------------------------------- #


class PeekRecordTool(Tool):
    """READONLY tool that reads its own run's record MID-first-step — proving
    what the database held before any post-batch write."""

    name = "peek_record_tool"
    description = "test-only run-record peeker"
    input_schema = {"type": "object", "properties": {}}
    reversibility = Reversibility.READONLY

    def __init__(self, db_engine):
        self._engine = db_engine
        self.seen: dict | None = None

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        with session_scope(self._engine) as db:
            rec = db.get(WorkflowRunRecord, ctx.session_id)
            self.seen = json.loads(rec.outputs_json or "{}")
        return ToolResult(ok=True, output="peeked")


async def test_trigger_seed_is_persisted_before_the_first_batch(tmp_path):
    # A reflex {{Trigger}} rides the outputs kwarg. If the daemon dies during
    # the FIRST step, the resumed run must still see it — so it must be in
    # outputs_json before batch 1, not only after it settles.
    platform = build_platform(str(tmp_path))
    engine = WorkflowEngine(platform)
    peek = PeekRecordTool(platform.engine)
    platform.registry.register(peek)
    wf = WorkflowDef(
        name="trig",
        steps=[Step(name="First", kind="tool", tool="peek_record_tool")],
    )
    rec = engine.create_record(wf)
    await engine.run_record(
        rec,
        wf,
        outputs={
            "Trigger": {
                "status": "completed",
                "summary": "webhook body",
                "kind": "trigger",
            }
        },
    )
    assert peek.seen is not None
    assert peek.seen.get("Trigger", {}).get("summary") == "webhook body"


# --------------------------------------------------------------------------- #
# cleanup: the dead "active" default
# --------------------------------------------------------------------------- #


def test_run_record_default_status_is_running():
    assert WorkflowRunRecord().status == "running"
