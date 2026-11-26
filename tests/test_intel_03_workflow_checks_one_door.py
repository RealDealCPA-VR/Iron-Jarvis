"""Intelligence plan, release 3: a saved workflow is checked the same way
everywhere.

Two gaps, both found by reading:

* The Workflows page checks a workflow's steps before it saves
  (``POST /workflows``). The chat tool that saves one (``workflow_create``)
  saved whatever it was handed, so a workflow made in chat that could not
  work was found out at its turn, mid-run.
* A step that refers to a value nothing gave (``{{Client}}`` with no step,
  input or signal of that name) was filled in as an empty text without a
  word, and the step ran on it.

The page is asked through the real route, the tool through the real registry
gate, and the engine through a real run with real tool steps.
"""

from __future__ import annotations

# Register the workflow tables BEFORE any platform is built.
import iron_jarvis.workflows.models  # noqa: F401
import iron_jarvis.workflows.store  # noqa: F401

import json

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import Reversibility, Tool, ToolContext, ToolResult
from iron_jarvis.workflows.checks import missing_values, no_value_words, step_problem
from iron_jarvis.workflows.engine import Step, WorkflowDef, WorkflowEngine
from iron_jarvis.workflows.store import WorkflowStore


# --------------------------------------------------------------------------- #
# (1) One check, two doors, the same words.
# --------------------------------------------------------------------------- #
BAD = {
    "a kind the loader would quietly rewrite": [
        {"name": "Gather", "kind": "tools", "tool": "read_file"},
    ],
    "a tool step with no tool": [
        {"name": "Gather", "kind": "tool"},
    ],
    "a tool this install does not have": [
        {"name": "Gather", "kind": "tool", "tool": "no_such_tool_intel03", "args": {}},
    ],
    "a question with no words": [
        {"name": "Check", "kind": "ask"},
    ],
    "a failure rule that is not one": [
        {"name": "Tell", "kind": "notify", "message": "done", "on_failure": "ignore"},
    ],
    "data of a step that does not exist": [
        {"name": "Scan", "kind": "notify", "message": "scanning"},
        {"name": "Tell", "kind": "notify", "message": "result: {{Scna.data}}"},
    ],
}


def _tool_ctx(platform, tmp_path) -> ToolContext:
    return ToolContext(
        workspace=tmp_path, session_id="s", agent_run_id="r",
        config=platform.config, event_bus=platform.event_bus, engine=platform.engine,
    )


@pytest.mark.parametrize("why", sorted(BAD))
async def test_the_page_and_the_chat_tool_refuse_the_same_workflow_in_the_same_words(
    tmp_path, why
):
    steps = BAD[why]
    with TestClient(create_app(str(tmp_path))) as client:
        platform = client.app.state.platform

        page = client.post("/workflows", json={"name": "from-page", "steps": steps})
        assert page.status_code == 422, page.json()
        said = page.json()["detail"]
        assert said.startswith("steps["), said

        tool = await platform.registry.invoke(
            "workflow_create", {"name": "from-chat", "steps": steps},
            _tool_ctx(platform, tmp_path), platform.permissions,
            session_allow=["workflow_create"],
        )
        assert tool.ok is False, "the chat tool saved a workflow the page refuses"
        assert tool.error == said

        store = WorkflowStore(platform.engine)
        assert store.get("from-page") is None and store.get("from-chat") is None


async def test_a_workflow_that_can_work_is_saved_through_both_doors(tmp_path):
    steps = [
        {"name": "Read", "kind": "tool", "tool": "read_file", "args": {"path": "{{File}}"}},
        {"name": "Tell", "kind": "notify", "message": "read {{Read}}"},
    ]
    with TestClient(create_app(str(tmp_path))) as client:
        platform = client.app.state.platform
        assert client.post("/workflows", json={"name": "from-page", "steps": steps}).status_code == 200
        tool = await platform.registry.invoke(
            "workflow_create", {"name": "from-chat", "steps": steps},
            _tool_ctx(platform, tmp_path), platform.permissions,
            session_allow=["workflow_create"],
        )
        assert tool.ok, tool.error
        store = WorkflowStore(platform.engine)
        assert json.loads(store.get("from-page").steps_json) == steps
        assert json.loads(store.get("from-chat").steps_json) == steps


def test_the_check_reads_the_same_with_or_without_the_live_tool_list(tmp_path):
    """The words come from one function; the route adds nothing to them."""
    platform = build_platform(str(tmp_path))
    for why, steps in BAD.items():
        said = step_problem(steps, platform)
        assert said and said.startswith("steps["), why
    assert step_problem([{"name": "Tell", "kind": "notify", "message": "hi"}], platform) is None
    assert step_problem("not a list", platform).startswith("steps:")


# --------------------------------------------------------------------------- #
# (2) A value nothing gave stops the step, by name.
# --------------------------------------------------------------------------- #
class Captures(Tool):
    """Records what it was called with."""

    name = "t_intel03_captures"
    description = "test-only"
    input_schema = {"type": "object", "properties": {}}
    reversibility = Reversibility.READONLY

    def __init__(self):
        self.calls: list[dict] = []

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        self.calls.append(dict(args))
        return ToolResult(ok=True, output="captured", data={"n": len(self.calls)})


def _engine(tmp_path):
    platform = build_platform(str(tmp_path))
    capture = Captures()
    platform.registry.register(capture)
    delivered: list[str] = []
    engine = WorkflowEngine(platform)
    engine._deliver = lambda message, **kw: (delivered.append(message) or True, "sent")  # type: ignore[method-assign]
    return engine, capture, delivered


def _outs(rec) -> dict:
    return json.loads(rec.outputs_json)


async def test_a_tool_step_with_a_value_nothing_gave_does_not_run_and_says_which(tmp_path):
    engine, capture, _ = _engine(tmp_path)
    rec = await engine.run(
        WorkflowDef(
            name="monday",
            steps=[Step(name="Use", kind="tool", tool=capture.name, args={"path": "{{Client}}/ledger.csv"})],
        )
    )
    out = _outs(rec)["Use"]
    assert capture.calls == [], "the tool ran on an empty value"
    assert out["status"] == "failed"
    assert out["summary"] == "could not run: no value for Client"
    assert out["no_value_for"] == ["Client"]
    assert rec.status == "failed", "the run read as finished"


async def test_the_same_step_runs_when_the_value_is_given(tmp_path):
    engine, capture, _ = _engine(tmp_path)
    wf = WorkflowDef(
        name="monday",
        steps=[Step(name="Use", kind="tool", tool=capture.name, args={"path": "{{Client}}/ledger.csv"})],
    )
    # As the run route and the `workflow_run` tool start one: the inputs go
    # to the record AND to the driver.
    given = {"Client": "acme"}
    rec = await engine.run_record(engine.create_record(wf, given), wf, inputs=given)
    assert capture.calls == [{"path": "acme/ledger.csv"}]
    assert _outs(rec)["Use"]["status"] == "completed" and rec.status == "completed"


async def test_a_value_given_as_empty_text_is_a_value(tmp_path):
    """An input the person left blank was GIVEN: the step runs on it."""
    engine, capture, _ = _engine(tmp_path)
    wf = WorkflowDef(
        name="blank",
        steps=[Step(name="Use", kind="tool", tool=capture.name, args={"note": "[{{Note}}]"})],
    )
    given = {"Note": ""}
    rec = await engine.run_record(engine.create_record(wf, given), wf, inputs=given)
    assert capture.calls == [{"note": "[]"}] and rec.status == "completed"


async def test_an_earlier_steps_answer_and_its_data_are_values(tmp_path):
    engine, capture, _ = _engine(tmp_path)
    rec = await engine.run(
        WorkflowDef(
            name="chain",
            steps=[
                Step(name="First", kind="tool", tool=capture.name, args={}),
                Step(name="Second", kind="tool", tool=capture.name,
                     args={"said": "{{First}}", "data": "{{First.data}}"}),
            ],
        )
    )
    assert rec.status == "completed"
    assert capture.calls[1] == {"said": "captured", "data": '{"n": 1}'}


async def test_every_missing_name_is_said_once_in_the_order_written(tmp_path):
    engine, capture, _ = _engine(tmp_path)
    rec = await engine.run(
        WorkflowDef(
            name="several",
            steps=[Step(name="Use", kind="tool", tool=capture.name,
                        args={"a": "{{Month}} {{Client}}", "b": ["{{Month}}", "{{Scan.data}}"]})],
        )
    )
    assert capture.calls == []
    assert _outs(rec)["Use"]["summary"] == "could not run: no value for Month, Client, Scan.data"


async def test_a_message_step_with_a_value_nothing_gave_is_not_sent(tmp_path):
    engine, _, delivered = _engine(tmp_path)
    rec = await engine.run(
        WorkflowDef(name="tell", steps=[Step(name="Tell", kind="notify", message="Done for {{Client}}")])
    )
    assert delivered == [], "a message with a hole in it was sent"
    assert _outs(rec)["Tell"]["summary"] == "could not run: no value for Client"
    assert rec.status == "failed"


async def test_a_question_with_a_value_nothing_gave_is_not_asked(tmp_path):
    """The run is not parked on half a question: the step fails by name."""
    engine, _, delivered = _engine(tmp_path)
    rec = await engine.run(
        WorkflowDef(name="ask", steps=[Step(name="Check", kind="ask", message="Send {{File}} to the client?")])
    )
    assert rec.status == "failed", rec.status
    assert not (rec.waiting_json or ""), "the run parked on a question with a hole in it"
    assert _outs(rec)["Check"]["summary"] == "could not run: no value for File"
    assert delivered == []


async def test_a_helper_step_with_a_value_nothing_gave_starts_no_helper(tmp_path):
    engine, _, _ = _engine(tmp_path)
    rec = await engine.run(
        WorkflowDef(name="agent", steps=[Step(name="Write", agent="builder", task="Summarise {{Report}}")])
    )
    out = _outs(rec)["Write"]
    assert out["summary"] == "could not run: no value for Report"
    assert not out.get("session_id") and json.loads(rec.session_ids_json or "[]") == []
    assert rec.status == "failed"


async def test_the_steps_own_failure_rule_still_decides_what_the_run_does(tmp_path):
    engine, capture, _ = _engine(tmp_path)
    rec = await engine.run(
        WorkflowDef(
            name="skip",
            steps=[
                Step(name="Use", kind="tool", tool=capture.name, args={"p": "{{Client}}"}, on_failure="skip"),
                Step(name="Then", kind="tool", tool=capture.name, args={"p": "after"}),
            ],
        )
    )
    outs = _outs(rec)
    assert outs["Use"]["summary"] == "could not run: no value for Client"
    assert outs["Use"].get("handled") == "skipped"
    assert capture.calls == [{"p": "after"}]
    assert rec.status == "completed_with_failures"


async def test_a_missing_value_is_never_tried_twice(tmp_path):
    engine, capture, _ = _engine(tmp_path)
    rec = await engine.run(
        WorkflowDef(
            name="retry",
            steps=[Step(name="Use", kind="tool", tool=capture.name, args={"p": "{{Client}}"}, on_failure="retry")],
        )
    )
    assert capture.calls == [] and rec.status == "failed"


async def test_a_failed_steps_data_is_still_the_recorded_empty_answer(tmp_path):
    """Unchanged on purpose (v1.231.0): a step that FAILED and was skipped
    answered; its ``.data`` is empty and the next step still runs. Only a
    name that matches nothing stops a step."""

    class Fails(Tool):
        name = "t_intel03_fails"
        description = "test-only"
        input_schema = {"type": "object", "properties": {}}
        reversibility = Reversibility.READONLY

        async def execute(self, args, ctx):
            return ToolResult(ok=False, error="disk full")

    engine, capture, _ = _engine(tmp_path)
    engine.platform.registry.register(Fails())
    rec = await engine.run(
        WorkflowDef(
            name="handoff",
            steps=[
                Step(name="Scan", kind="tool", tool="t_intel03_fails", on_failure="skip"),
                Step(name="Use", kind="tool", tool=capture.name, args={"path": "{{Scan.data}}"}),
            ],
        )
    )
    assert capture.calls == [{"path": ""}]
    assert rec.status == "completed_with_failures"


def test_what_counts_as_missing_follows_what_each_kind_of_step_reads():
    outs = {"Given": {"status": "completed", "summary": "x"}}
    # A tool step never reads its `task`: a name there stops nothing.
    tool = Step(name="T", kind="tool", tool="x", task="{{Nope}}", args={"a": "{{Given}}"})
    assert missing_values(tool, outs) == []
    # A message step reads its message, else its task.
    assert missing_values(Step(name="N", kind="notify", message="{{Given}}", task="{{Nope}}"), outs) == []
    assert missing_values(Step(name="N", kind="notify", task="{{Nope}}"), outs) == ["Nope"]
    assert missing_values(Step(name="A", task="{{ Nope }} and {{Given}}"), outs) == ["Nope"]
    assert missing_values(Step(name="A", task="plain words"), None) == []
    assert no_value_words(["A", "B"]) == "could not run: no value for A, B"


# --------------------------------------------------------------------------- #
# (3) Review follow-ups: the other doors that save, and a signal with no words.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("why", sorted(BAD))
async def test_changing_a_workflow_in_chat_is_refused_in_the_pages_words_too(tmp_path, why):
    """``workflow_update`` is the tool behind "change my workflow so step 1
    is ...": it saved whatever it was handed."""
    steps = BAD[why]
    good = [{"name": "Tell", "kind": "notify", "message": "hi"}]
    with TestClient(create_app(str(tmp_path))) as client:
        platform = client.app.state.platform
        assert client.post("/workflows", json={"name": "triage", "steps": good}).status_code == 200
        said = client.post("/workflows", json={"name": "other", "steps": steps}).json()["detail"]

        out = await platform.registry.invoke(
            "workflow_update", {"name": "triage", "new_name": "triage-two", "steps": steps},
            _tool_ctx(platform, tmp_path), platform.permissions,
            session_allow=["workflow_update"],
        )
        assert out.ok is False, "the chat tool changed a workflow to steps the page refuses"
        assert out.error == said
        store = WorkflowStore(platform.engine)
        # Nothing changed: not the steps, and not the name either.
        assert json.loads(store.get("triage").steps_json) == good
        assert store.get("triage-two") is None


async def test_a_change_that_can_work_is_still_made_in_chat(tmp_path):
    good = [{"name": "Tell", "kind": "notify", "message": "hi"}]
    better = [{"name": "Tell", "kind": "notify", "message": "hello {{Name}}"}]
    with TestClient(create_app(str(tmp_path))) as client:
        platform = client.app.state.platform
        assert client.post("/workflows", json={"name": "triage", "steps": good}).status_code == 200
        out = await platform.registry.invoke(
            "workflow_update", {"name": "triage", "steps": better},
            _tool_ctx(platform, tmp_path), platform.permissions,
            session_allow=["workflow_update"],
        )
        assert out.ok, out.error
        assert json.loads(WorkflowStore(platform.engine).get("triage").steps_json) == better


def test_a_workflow_a_model_built_for_the_page_is_checked_before_it_is_saved(tmp_path):
    """``POST /workflows/generate`` saves steps a model wrote."""
    from iron_jarvis.providers.adapters.base import LLMResponse

    class _Writes:
        def __init__(self, text):
            self._text = text

        async def complete(self, *, system, messages, tools):
            return LLMResponse(text=self._text)

    def client_with(reply):
        app = create_app(str(tmp_path))
        app.state.platform.providers.get = lambda *a, **k: _Writes(json.dumps(reply))
        return TestClient(app)

    bad = {"name": "Night Sweep", "description": "x", "steps": [
        {"name": "Scan", "agent": "builder", "task": "scan the folder"},
        {"name": "Tell", "kind": "notify", "message": "result: {{Scna.data}}"},
    ]}
    client = client_with(bad)
    r = client.post("/workflows/generate", json={"description": "a nightly sweep"})
    assert r.status_code == 422, r.json()
    detail = r.json()["detail"]
    assert "steps[1] (Tell)" in detail and "nothing was saved" in detail
    assert client.get("/workflows/night-sweep").status_code == 404

    good = {**bad, "steps": [bad["steps"][0], {"name": "Tell", "kind": "notify", "message": "result: {{Scan}}"}]}
    client = client_with(good)
    r = client.post("/workflows/generate", json={"description": "a nightly sweep"})
    assert r.status_code == 200, r.json()
    assert client.get("/workflows/night-sweep").status_code == 200


async def test_a_signal_that_brought_no_words_still_gives_trigger_a_value(tmp_path):
    """A rule or a phone command can start a workflow with nothing after it.
    The signal still fired: ``{{Trigger}}`` is an empty value that was given,
    as an input given as "" is. Without that the step would stop with
    "no value for Trigger"."""
    from iron_jarvis.agents.orchestrator import Orchestrator
    from iron_jarvis.reflex.router import ReflexRouter

    platform = build_platform(str(tmp_path))
    WorkflowStore(platform.engine).save(
        "greet", [{"name": "Tell", "kind": "notify", "message": "Started. Note: {{Trigger}}"}], ""
    )
    started: list = []
    router = ReflexRouter(platform, Orchestrator(platform), spawn_bg=lambda sid, coro: started.append(coro))
    sent: list[str] = []
    orig = WorkflowEngine._deliver
    WorkflowEngine._deliver = lambda self, message, **kw: (sent.append(message) or True, "sent")  # type: ignore[method-assign]
    try:
        seen = {}
        for words in ("", "do the thing"):
            out = await router.start("workflow", target="greet", task=words)
            assert out.get("ok"), out
            rec = await started.pop()
            seen[words] = (rec.status, json.loads(rec.outputs_json))
    finally:
        WorkflowEngine._deliver = orig  # type: ignore[method-assign]

    status, outs = seen[""]
    assert status == "completed", outs
    assert outs["Trigger"]["summary"] == "" and outs["Trigger"]["kind"] == "trigger"
    # The posture rides only a signal that brought words (there is no outside
    # text in an empty one): unchanged.
    assert "trust" not in outs["Trigger"]
    assert seen["do the thing"][0] == "completed"
    assert sent == ["Started. Note:", "Started. Note: do the thing"]
