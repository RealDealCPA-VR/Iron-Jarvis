"""Idea 09, phase 09-a: a workflow with blocks is checked the same way
everywhere, and older workflows load unchanged.

* ``workflows/validate.problems`` walks INTO the "If" and "For each" blocks;
  ``checks.step_problem`` (the ONE function every door that saves calls)
  answers with its first sentence, so the page (``POST /workflows``), the
  chat tool (``workflow_create``) and the model's builder
  (``POST /workflows/generate``) refuse in the same words.
* The engine refuses a block that would run wrongly before the run exists,
  whatever door started it (``POST /workflows/run`` with unsaved steps).
* A step of a kind this version does not know is refused BY NAME when it is
  loaded, never run as an agent step.
* Every door that lets a model write a workflow tells it about the blocks.
"""

from __future__ import annotations

# Register the workflow tables BEFORE any platform is built.
import iron_jarvis.workflows.models  # noqa: F401
import iron_jarvis.workflows.store  # noqa: F401

import json

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import _WORKFLOW_DRAFT_SPEC, _sanitize_draft
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.workflows.checks import step_problem
from iron_jarvis.workflows.engine import (
    STEP_KINDS,
    UnknownStepKind,
    WorkflowEngine,
    load_workflow,
    step_to_dict,
)
from iron_jarvis.workflows.store import WorkflowStore
from iron_jarvis.workflows.tools import WorkflowCreateTool
from iron_jarvis.workflows.validate import BLOCKS_FOR_MODELS, problems


def _each(**over) -> dict:
    return {
        "name": "Each file",
        "kind": "for_each",
        "over": {"files": {"folder": "C:/Intake", "glob": "*.pdf"}},
        "steps": [{"name": "Tell", "kind": "notify", "message": "{{item.name}}"}],
        **over,
    }


def _if(**over) -> dict:
    return {
        "name": "Big?",
        "kind": "if",
        "condition": {"left": "{{Read}}", "op": "over", "right": "5000"},
        "steps": [{"name": "Ask", "kind": "ask", "message": "File it?"}],
        "else_steps": [{"name": "Note", "kind": "notify", "message": "small"}],
        **over,
    }


READ = {"name": "Read", "kind": "notify", "message": "reading"}

BAD = {
    "an If with no condition": ([READ, _if(condition={})], "steps[1] (Big?): an If needs a condition"),
    "an unknown comparison": (
        [READ, _if(condition={"left": "{{Read}}", "op": "bigger", "right": "1"})],
        "steps[1] (Big?): the comparison 'bigger' is not one of",
    ),
    "a comparison with nothing to compare with": (
        [READ, _if(condition={"left": "{{Read}}", "op": "over"})],
        "steps[1] (Big?): an If's “is over” check needs a value to compare with",
    ),
    "an empty If": ([READ, _if(steps=[], else_steps=[])], "steps[1] (Big?): an If needs at least one step"),
    "a For each with nothing to work through": (
        [_each(over={})], "steps[0] (Each file): a For each needs a list to work through",
    ),
    "a For each over files with no folder": (
        [_each(over={"files": {"glob": "*.pdf"}})], "steps[0] (Each file): a For each over files needs a folder",
    ),
    "a For each with two lists": (
        [_each(over={"from": "[1]", "files": {"folder": "x"}})], "steps[0] (Each file): a For each works through one list",
    ),
    "an empty For each": ([_each(steps=[])], "steps[0] (Each file): a For each needs at least one step inside it"),
    "a limit out of range": ([_each(max_items=5000)], "steps[0] (Each file): max_items is how many items"),
    "a limit that is not a number": ([_each(max_items="lots")], "steps[0] (Each file): max_items is how many items"),
    # Review: one listing of a folder shows at most 1,000 entries, so a folder
    # limit past it would work through part of a folder as if it were all.
    "a folder limit past one listing": (
        [_each(max_items=1500)],
        "steps[0] (Each file): a For each over a folder works through at most 1,000 files",
    ),
    "too many at once": ([_each(parallel=9)], "steps[0] (Each file): parallel is how many items it works on at once, 1 to 4"),
    "an ask in a For each that works on several at once": (
        [_each(parallel=2, steps=[{"name": "OK?", "kind": "ask", "message": "ok?"}])],
        "steps[0] (Each file): a For each that asks you something works on one item at a time",
    ),
    "a block in a parallel group": ([_each(group="g")], "steps[0] (Each file): a For each runs on its own"),
    "a block set to retry": ([_each(on_failure="retry")], "steps[0] (Each file): a For each can stop the run or skip"),
    "too deep": (
        [_each(steps=[_each(steps=[_each(steps=[_each()])])])],
        "steps[0].steps[0].steps[0].steps[0] (Each file): blocks go at most 3 deep",
    ),
    "a step inside a block that cannot run": (
        [_each(steps=[{"name": "Pick", "kind": "tool"}])],
        "steps[0].steps[0] (Pick): a tool step needs a tool name",
    ),
    "a step on the Otherwise path that cannot run": (
        [READ, _if(else_steps=[{"name": "Say", "kind": "notify"}])],
        "steps[1].else_steps[0] (Say): an notify step needs a message",
    ),
    "two steps of the same name inside a block": (
        [_each(steps=[{"name": "T", "kind": "notify", "message": "a"}, {"name": "T", "kind": "notify", "message": "b"}])],
        "steps[0].steps[1] (T): two steps in the same list are both named “T”",
    ),
    "a value from a step that runs later": (
        [{"name": "Tell", "kind": "notify", "message": "{{Read}}"}, READ],
        "steps[0] (Tell): {{Read}} is the step “Read”, which runs after this one",
    ),
    "a condition on a step inside the If itself": (
        [_if(condition={"left": "{{Ask}}", "op": "empty"})],
        "steps[0] (Big?): {{Ask}} is the step “Ask”, which runs after this one",
    ),
    "the item outside a For each": (
        [{"name": "Tell", "kind": "notify", "message": "{{item.name}}"}],
        "steps[0] (Tell): {{item.name}} is the item a For each is working on",
    ),
    "a field of a step that does not exist": (
        [_each(steps=[{"name": "T", "kind": "notify", "message": "{{Raed.data.total}}"}])],
        "steps[0].steps[0] (T): {{Raed.data.total}} refers to a step named 'Raed'",
    ),
    "a step inside a block named from outside it": (
        [READ, _if(), {"name": "Tell", "kind": "notify", "message": "{{Ask}}"}],
        "steps[2] (Tell): {{Ask}} is the step “Ask” inside “Big?”, which this step cannot see; "
        "a step outside it can use {{Big?}}",
    ),
    "a step on the If's other lane": (
        [READ, _if(steps=[{"name": "Ask", "kind": "ask", "message": "{{Note}}?"}])],
        "steps[1].steps[0] (Ask): {{Note}} is the step “Note” on the other lane of “Big?”",
    ),
    "a step naming itself": (
        [{"name": "Tell", "kind": "notify", "message": "again: {{Tell}}"}],
        "steps[0] (Tell): {{Tell}} is this step itself",
    ),
    "a step named like the steps inside a block": (
        [_each(), {"name": "Each file/1/Tell", "kind": "notify", "message": "x"}],
        "steps: the step name “Each file/1/Tell” starts with “Each file/”",
    ),
    "a kind this version does not know": (
        [{"name": "Wait", "kind": "wait_until"}], "steps[0].kind: 'wait_until' is not one of agent|tool|ask|notify|if|for_each",
    ),
}


@pytest.mark.parametrize("why", sorted(BAD))
def test_each_block_problem_is_refused_in_words(why):
    steps, starts = BAD[why]
    said = step_problem(steps, None)
    assert said is not None and said.startswith(starts), (why, said)
    # Plain words: no engine names, no Python.
    for engine_word in ("Traceback", "KeyError", "None", "dict", "list["):
        assert engine_word not in said, said


def _ctx(platform, tmp_path) -> ToolContext:
    return ToolContext(
        workspace=tmp_path, session_id="s", agent_run_id="r",
        config=platform.config, event_bus=platform.event_bus, engine=platform.engine,
    )


@pytest.mark.parametrize("why", ["an empty If", "a step inside a block that cannot run", "too deep", "a value from a step that runs later"])
async def test_the_page_and_the_chat_tool_refuse_a_broken_block_in_the_same_words(tmp_path, why):
    steps, _ = BAD[why]
    with TestClient(create_app(str(tmp_path))) as client:
        platform = client.app.state.platform
        page = client.post("/workflows", json={"name": "from-page", "steps": steps})
        assert page.status_code == 422, page.json()
        said = page.json()["detail"]
        tool = await platform.registry.invoke(
            "workflow_create", {"name": "from-chat", "steps": steps},
            _ctx(platform, tmp_path), platform.permissions, session_allow=["workflow_create"],
        )
        assert tool.ok is False and tool.error == said
        store = WorkflowStore(platform.engine)
        assert store.get("from-page") is None and store.get("from-chat") is None


GOOD = [
    {"name": "List", "kind": "tool", "tool": "list_folder", "args": {"path": "."}},
    {
        "name": "Each file",
        "kind": "for_each",
        "over": {"files": {"folder": "{{Folder}}", "glob": "*.pdf"}},
        "max_items": 50,
        "steps": [
            {"name": "Read", "kind": "tool", "tool": "read_document", "args": {"path": "{{item}}"}},
            {
                "name": "Big?",
                "kind": "if",
                "condition": {"all": [
                    {"left": "{{Read.data.total}}", "op": "over", "right": "5,000"},
                    {"left": "{{List}}", "op": "not_empty"},
                ]},
                "steps": [{"name": "Ask", "kind": "ask", "message": "{{item.name}} is {{Read.data.total}}. File it?"}],
                "else_steps": [{"name": "Note", "kind": "notify", "message": "{{index}}: {{item.name}} filed"}],
            },
        ],
    },
    {"name": "Tell", "kind": "notify", "message": "Done: {{Each file}}"},
]


async def test_a_workflow_with_blocks_that_can_work_is_saved_through_both_doors(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        platform = client.app.state.platform
        assert step_problem(GOOD, platform) is None
        assert client.post("/workflows", json={"name": "from-page", "steps": GOOD}).status_code == 200
        tool = await platform.registry.invoke(
            "workflow_create", {"name": "from-chat", "steps": GOOD},
            _ctx(platform, tmp_path), platform.permissions, session_allow=["workflow_create"],
        )
        assert tool.ok, tool.error
        store = WorkflowStore(platform.engine)
        assert json.loads(store.get("from-page").steps_json) == GOOD
        assert json.loads(store.get("from-chat").steps_json) == GOOD
        # It loads back with its blocks, and a run's record keeps them whole.
        wf = store.load_def("from-chat")
        assert [s.kind for s in wf.steps] == ["tool", "for_each", "notify"]
        each = wf.steps[1]
        assert each.max_items == 50 and [s.name for s in each.steps] == ["Read", "Big?"]
        assert each.steps[1].else_steps[0].name == "Note"
        again = load_workflow({"name": "x", "steps": [step_to_dict(s) for s in wf.steps]})
        assert [step_to_dict(s) for s in again.steps] == [step_to_dict(s) for s in wf.steps]


def test_the_engine_refuses_a_block_that_would_run_wrongly_before_the_run_exists(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        steps = [_each(parallel=2, steps=[{"name": "OK?", "kind": "ask", "message": "ok?"}])]
        r = client.post("/workflows/run", json={"name": "unsaved", "steps": steps})
        assert r.status_code == 400, r.text
        assert r.json()["detail"].startswith(
            "This workflow cannot run: steps[0] (Each file): a For each that asks you something"
        )
        assert client.get("/workflows/runs").json()["runs"] == []
    # The engine itself, whatever door calls it.
    engine = WorkflowEngine(build_platform(str(tmp_path / "bare")))
    with pytest.raises(ValueError, match="This workflow cannot run"):
        engine.create_record(load_workflow({"name": "w", "steps": [_each(max_items=0)]}))


def test_a_step_of_a_kind_this_version_does_not_know_is_refused_by_name(tmp_path):
    with pytest.raises(UnknownStepKind) as raised:
        load_workflow({"name": "w", "steps": [{"name": "Hold on", "kind": "wait_until", "task": "x"}]})
    words = str(raised.value)
    assert words.startswith("The step “Hold on” is a “wait_until” step, which this version of Iron Jarvis does not know")
    assert "Workflows page" in words
    with TestClient(create_app(str(tmp_path))) as client:
        r = client.post("/workflows/run", json={"name": "w", "steps": [{"name": "Hold on", "kind": "wait_until"}]})
        assert r.status_code == 400 and r.json()["detail"] == words
        assert client.get("/workflows/runs").json()["runs"] == []


#: Step lists as they were stored before blocks existed (every shape the
#: engine took: agent-only, tool + args, ask/notify, expect, a group, every
#: on_failure, a blank kind, an upper-case kind).
OLD = [
    [{"name": "Gather", "agent": "researcher", "task": "collect"}, {"name": "Write", "agent": "builder", "task": "write", "tool": None}],
    [
        {"name": "List", "kind": "tool", "tool": "list_folder", "args": {"path": "."}, "on_failure": "retry"},
        {"name": "A", "kind": "agent", "agent": "planner", "task": "plan {{List}}", "group": "g"},
        {"name": "B", "kind": "agent", "agent": "builder", "task": "build", "group": "g", "on_failure": "skip"},
        {"name": "OK?", "kind": "ask", "message": "Send {{A}}?", "expect": {"summary_contains": ["yes"]}},
        {"name": "Tell", "kind": "notify", "message": "done", "expect": {"files": ["out.md"]}},
    ],
    [{"name": "Blank", "kind": "", "task": "do it"}, {"name": "Upper", "kind": "Tool ", "tool": "list_folder", "args": {}}],
]


@pytest.mark.parametrize("steps", OLD, ids=["agents", "every-kind", "loose-kinds"])
def test_older_workflows_load_unchanged(tmp_path, steps):
    wf = load_workflow({"name": "old", "steps": steps})
    for raw, s in zip(steps, wf.steps):
        d = step_to_dict(s)
        # The same ten fields as before blocks existed, nothing added.
        assert set(d) == {"name", "agent", "task", "tool", "kind", "on_failure", "group", "args", "message", "expect"}
        assert d["kind"] == (str(raw.get("kind") or "agent").strip().lower() or "agent")
    assert problems(steps, None) == []
    engine = WorkflowEngine(build_platform(str(tmp_path)))
    rec = engine.create_record(wf)  # an older workflow is refused for nothing new
    assert json.loads(rec.steps_json) == [step_to_dict(s) for s in wf.steps]


def test_every_door_that_lets_a_model_write_a_workflow_tells_it_about_the_blocks():
    assert "for_each" in STEP_KINDS and "if" in STEP_KINDS
    assert BLOCKS_FOR_MODELS in _WORKFLOW_DRAFT_SPEC["input_schema"]["properties"]["steps"]["description"]
    item = _WORKFLOW_DRAFT_SPEC["input_schema"]["properties"]["steps"]["items"]["properties"]
    assert {"condition", "over", "steps", "else_steps", "max_items"} <= set(item)
    assert BLOCKS_FOR_MODELS in WorkflowCreateTool.description
    create_item = WorkflowCreateTool.input_schema["properties"]["steps"]["items"]["properties"]
    assert {"kind", "tool", "args", "message", "condition", "over", "steps", "else_steps", "max_items", "parallel"} <= set(create_item)
    for word in ("for_each", "if:", "{{item}}", "else_steps", "over"):
        assert word in BLOCKS_FOR_MODELS


def test_a_chat_draft_keeps_its_blocks_and_their_steps():
    d = _sanitize_draft({"name": "intake", "steps": GOOD})
    assert d is not None
    each = d["steps"][1]
    assert each["kind"] == "for_each" and each["over"] == GOOD[1]["over"] and each["max_items"] == 50
    assert [s["name"] for s in each["steps"]] == ["Read", "Big?"]
    big = each["steps"][1]
    assert big["condition"] == GOOD[1]["steps"][1]["condition"]
    assert big["steps"][0]["message"] == "{{item.name}} is {{Read.data.total}}. File it?"
    assert big["else_steps"][0]["kind"] == "notify"
    # What the draft keeps passes the save check as it is.
    assert step_problem(d["steps"], None) is None
    # A block without a name is named for what it is; the lists stay bounded.
    nameless = _sanitize_draft({"name": "w", "steps": [{"kind": "if", "condition": {"left": "1", "op": "is", "right": "1"},
                                                         "steps": [f"say hello number {i}" for i in range(30)]}]})
    assert nameless["steps"][0]["name"] == "If" and len(nameless["steps"][0]["steps"]) == 12


def test_a_chat_draft_never_turns_an_unknown_kind_into_an_agent_step():
    """Review: 'for-each' became one agent step whose task was the block's
    name, its folder and steps gone, and the save check passed."""
    inner = [{"name": "Summarize", "agent": "researcher", "task": "Summarize {{item}}"}]
    over = {"files": {"folder": "C:/Intake", "glob": "*.pdf"}}
    # The plain spellings of a block are that block, with everything inside.
    for spelling in ("for-each", "foreach", "forEach", "For each", "FOR_EACH"):
        d = _sanitize_draft({"name": "w", "steps": [
            {"name": "Each PDF", "kind": spelling, "over": over, "steps": inner},
        ]})
        each = d["steps"][0]
        assert each["kind"] == "for_each", spelling
        assert each["over"] == over and [s["name"] for s in each["steps"]] == ["Summarize"]
        assert step_problem(d["steps"], None) is None
    d = _sanitize_draft({"name": "w", "steps": [
        {"name": "Check", "kind": "if-else", "condition": {"left": "1", "op": "is", "right": "1"},
         "steps": [{"name": "Tell", "kind": "notify", "message": "yes"}]},
    ]})
    assert d["steps"][0]["kind"] == "if" and d["steps"][0]["steps"][0]["name"] == "Tell"
    # Any other word is kept as written and refused BY NAME at save.
    d = _sanitize_draft({"name": "w", "steps": [
        {"name": "Each PDF", "kind": "loop", "over": over, "steps": inner},
    ]})
    assert d["steps"][0]["kind"] == "loop"
    problem = step_problem(d["steps"], None) or ""
    assert problem.startswith("steps[0].kind: 'loop' is not one of"), problem
    # An empty kind is still an agent step.
    d = _sanitize_draft({"name": "w", "steps": [{"name": "Draft", "task": "draft it", "kind": ""}]})
    assert d["steps"][0]["kind"] == "agent"


def test_workflow_create_says_what_a_for_each_with_an_agent_inside_costs():
    """D-09.1, review note: the agent lane's door says the cost line too, so a
    workflow saved by an agent run is not the one place it goes unsaid."""
    from iron_jarvis.workflows.tools import each_cost_note

    agent_inside = [_if(steps=[_each(steps=[{"name": "Sum", "agent": "researcher", "task": "sum {{item}}"}])])]
    assert "one model run per item" in each_cost_note(agent_inside)
    assert each_cost_note([_each()]) == ""  # a notify step inside costs no model run
    assert each_cost_note([{"name": "A", "agent": "builder", "task": "t"}]) == ""  # no For each


class _FakeAdapter:
    def __init__(self, text: str):
        self.text = text
        self.systems: list[str] = []

    async def complete(self, *, system, messages, tools):
        self.systems.append(system)
        return LLMResponse(text=self.text)


def test_the_builder_writes_blocks_and_saves_them_through_the_one_check(tmp_path):
    adapter = _FakeAdapter(json.dumps({"name": "Intake", "description": "PDFs", "steps": GOOD}))
    app = create_app(str(tmp_path))
    app.state.platform.providers.get = lambda *a, **k: adapter
    with TestClient(app) as client:
        r = client.post("/workflows/generate", json={"description": "for each PDF in intake…"})
        assert r.status_code == 200, r.text
        assert BLOCKS_FOR_MODELS in adapter.systems[0]
        saved = json.loads(client.get("/workflows/intake").json()["steps_json"])
        assert saved[1]["kind"] == "for_each" and saved[1]["steps"][1]["else_steps"][0]["name"] == "Note"
        # A block the model got wrong is refused in the check's words; nothing saved.
        bad = _FakeAdapter(json.dumps({"name": "Broken", "steps": [READ, _if(steps=[], else_steps=[])]}))
        app.state.platform.providers.get = lambda *a, **k: bad
        r2 = client.post("/workflows/generate", json={"description": "x"})
        assert r2.status_code == 422
        assert "an If needs at least one step" in r2.json()["detail"]
        assert client.get("/workflows/broken").status_code == 404


async def test_a_stored_workflow_a_rule_or_a_schedule_cannot_run_is_said_in_its_own_sentence(tmp_path):
    """Review (note): a hand-made or very old row a rule or a schedule runs,
    with a kind this version does not know or a block that would run
    wrongly, put the class name on the row ("UnknownStepKind: …"), or the
    schedule's generic words. Both now say the refusal's own sentence."""
    from sqlmodel import select

    from iron_jarvis.agents.orchestrator import Orchestrator
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.reflex.router import ReflexRouter
    from iron_jarvis.scheduling.models import ScheduledTaskRecord

    platform = build_platform(str(tmp_path))
    store = WorkflowStore(platform.engine)  # the store is lenient; the doors are not
    store.save("typo", [{"name": "s", "task": "t", "kind": "toool"}])
    store.save("zero", [_each(max_items=0)])
    router = ReflexRouter(platform, Orchestrator(platform), spawn_bg=None)
    for target in ("typo", "zero"):
        router.store.add(name=f"hook-{target}", source="webhook", match=target, action="workflow", target=target)
    typo = (await router.on_webhook("typo", {"text": "x"}))[0]
    zero = (await router.on_webhook("zero", {"text": "x"}))[0]
    assert typo["ok"] is False and typo["error"].startswith("The step “s” is a “toool” step"), typo
    assert zero["ok"] is False and zero["error"].startswith("This workflow cannot run: steps[0] (Each file)"), zero
    with session_scope(platform.engine) as db:
        for target in ("typo", "zero"):
            db.add(ScheduledTaskRecord(
                name=f"sched-{target}", cron="0 9 * * *", trigger_type="cron", kind="workflow",
                payload_json=json.dumps({"workflow": target, "notify": False}),
            ))
        db.commit()
    for target in ("typo", "zero"):
        await platform.scheduler.run_now(f"sched-{target}")
    with session_scope(platform.engine) as db:
        rows = {r.name: r for r in db.exec(select(ScheduledTaskRecord)).all()}
    assert "“s” is a “toool” step" in rows["sched-typo"].last_detail, rows["sched-typo"].last_detail
    assert "This workflow cannot run: steps[0] (Each file)" in rows["sched-zero"].last_detail, rows["sched-zero"].last_detail
    assert not any("Refused" in r.last_detail or "Kind:" in r.last_detail for r in rows.values())
