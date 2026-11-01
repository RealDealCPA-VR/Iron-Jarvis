"""Compaction in the AGENT lane, where there is nobody to ask (v1.153.0).

Chat gets the choice: it signals at 70% and only acts at the ceiling. A running
agent has no human attached to it, so it compacts on its own — which makes the
guarantees stricter, not looser. Two invariants carry the whole lane:

* the boundary lands between BLOCKS, so a ``tool_use`` is never separated from
  its ``tool_result`` (splitting them makes strict providers reject the entire
  conversation — the same constraint ``agent_window`` exists for);
* ``messages[0]`` — the task — is NEVER covered. A run whose goal survives only
  as a paraphrase inside a summary is a run that can drift off what it was
  actually asked to do, confidently.
"""

from __future__ import annotations

import json

from iron_jarvis.agents.runtime import _effective
from iron_jarvis.context import compaction as C
from iron_jarvis.providers.adapters.base import LLMMessage, ToolCall


def _step(i: int, tool: str = "read_file"):
    call = ToolCall(id=f"c{i}", name=tool, arguments={})
    return [
        LLMMessage(role="assistant", content=f"step {i}", tool_calls=[call]),
        LLMMessage(role="tool", tool_call_id=f"c{i}", name=tool, content=f"result {i}"),
    ]


def _transcript(steps: int):
    msgs = [LLMMessage(role="user", content="THE TASK: reconcile the ledger")]
    for i in range(steps):
        msgs.extend(_step(i))
    return msgs


# --------------------------------------------------------------------------- #
# (1) WHAT MAY BE COVERED.
# --------------------------------------------------------------------------- #
def test_the_task_is_never_covered():
    """The goal is the one thing a summary must not replace."""
    msgs = _transcript(10)
    pairs, covered = C.agent_coverage(msgs, covered=0)
    assert covered > 0, "expected something to be coverable"
    assert not any("THE TASK" in t for _, t in pairs)


def test_coverage_lands_on_block_boundaries():
    """An assistant turn and its tool result move together or not at all."""
    msgs = _transcript(10)
    pairs, covered = C.agent_coverage(msgs, covered=0)
    # Every covered assistant turn must have brought its tool result along.
    roles = [r for r, _ in pairs]
    assert roles.count("assistant") == roles.count("tool"), (
        f"a tool pair was split: {roles}"
    )


def test_a_short_run_is_not_worth_a_model_call():
    pairs, covered = C.agent_coverage(_transcript(2), covered=0)
    assert pairs == [] and covered == 0


def test_coverage_advances_and_never_repeats_work():
    msgs = _transcript(14)
    _first, covered1 = C.agent_coverage(msgs, covered=0)
    pairs2, covered2 = C.agent_coverage(msgs, covered=covered1)
    assert covered2 >= covered1
    if pairs2:
        assert covered2 > covered1, "a second pass must cover NEW messages"


# --------------------------------------------------------------------------- #
# (2) APPLYING IT — without rewriting the run's own history.
# --------------------------------------------------------------------------- #
def test_applying_a_compaction_keeps_the_task_at_the_front():
    msgs = _transcript(10)
    out, system = _effective(msgs, "SYS", "SUMMARY-BODY", 8)
    assert out[0] is msgs[0], "the task must ride at the front verbatim"
    assert "SUMMARY-BODY" in system
    assert len(out) == len(msgs) - 8


def test_applying_a_compaction_does_not_mutate_the_callers_list():
    """The loop owns `messages` and keeps appending to it; that list is the
    run's real history and what gets persisted."""
    msgs = _transcript(10)
    before = list(msgs)
    _effective(msgs, "SYS", "SUMMARY", 6)
    assert msgs == before


def test_no_compaction_means_the_transcript_is_untouched():
    msgs = _transcript(6)
    out, system = _effective(msgs, "SYS", "", 0)
    assert out is msgs and system == "SYS"


# --------------------------------------------------------------------------- #
# (3) THE RUN: it happens, it is recorded, and it never breaks anything.
# --------------------------------------------------------------------------- #
def _compacting_run(tmp_path, *, window: int, task: str, say_chars: int, steps: int):
    """One agent run on a SCRIPTED model that keeps working for ``steps``
    steps — each an assistant turn carrying ``say_chars`` of reasoning and a
    ``list_files`` call — then answers. Returns (platform, session,
    compaction calls). v1.421.0: these pins used to ride the demo model with
    a task bigger than a 2,000-token window — a run whose planner clipped the
    task, so the model never saw its own result and asked again until its
    step limit, compacting along the way. That run now ends at once, saying
    the model is too small for the job (``runtime.ContextTooSmall``), so the
    pins drive a run the window CAN carry: a short roster (the builder's
    ~15k tokens of tool definitions now count against a known window) and a
    model that really takes the steps."""
    import asyncio
    import dataclasses

    from iron_jarvis.agents.orchestrator import Orchestrator
    from iron_jarvis.agents.types import get_agent_definition
    from iron_jarvis.core.models import AgentType
    from iron_jarvis.platform import build_platform
    from iron_jarvis.providers.adapters.base import LLMResponse
    from iron_jarvis.providers.adapters.mock import MockLLMAdapter

    platform = build_platform(str(tmp_path / "home"))
    platform.config.model_context_windows = {"mock": window}
    made = {"steps": 0, "compactions": 0}

    async def fake_complete(system, user):
        # Names only things the transcript really contains, so verification
        # keeps it — the point here is the wiring, not the stripping.
        made["compactions"] += 1
        return "GOAL:\n- note the plain items\nDONE:\n- ran list_files\n", "acme", "acme-1"

    platform._compaction_complete = lambda *a, **k: fake_complete

    class Scripted(MockLLMAdapter):
        async def complete(self, *, system, messages, tools, **kw):
            made["steps"] += 1
            i = made["steps"]
            if i <= steps:
                return LLMResponse(
                    text=("I am still looking at item %d. " % i) * (say_chars // 30),
                    tool_calls=[ToolCall(id=f"c{i}", name="list_files", arguments={"path": "."})],
                    finish_reason="tool_use",
                )
            return LLMResponse(text="done", finish_reason="stop")

    platform.providers.register("mock", lambda model=None: Scripted())
    orch = Orchestrator(platform)
    short = dataclasses.replace(get_agent_definition(AgentType.BUILDER), tools=["list_files"])

    async def go():
        sess = await orch.create_session(task, provider="mock", model="mock-1")
        return await orch.run_session(sess.id, definition=short)

    return platform, asyncio.run(go()), made["compactions"]


def test_a_run_compacts_itself_and_says_so(tmp_path):
    """End to end on a deliberately small window: the run compacts without
    asking, and leaves a PERSISTED record that it did."""
    from sqlmodel import Session, select

    from iron_jarvis.core.models import EventRecord, SessionStatus

    platform, sess, calls = _compacting_run(
        tmp_path, window=8_000, task="Note the plain items in this list.",
        say_chars=3_000, steps=10,
    )
    assert sess.status is SessionStatus.COMPLETED, sess.summary
    with Session(platform.engine) as db:
        rows = db.exec(
            select(EventRecord).where(EventRecord.type == "context.compacted")
        ).all()
    assert rows, "a run over the ceiling must record that it compacted"
    assert calls == len(rows) == 1
    payload = json.loads(rows[0].payload_json)
    assert payload["covers"] > 0
    assert payload["trigger"] == "auto"


def test_a_run_stops_paying_for_compactions_that_do_not_help(tmp_path):
    """The futility guard. When the TASK alone dominates the window, covering
    every step still leaves pressure above the ceiling — and without this the
    run buys another model call every step for no benefit. One attempt, then
    the planner's honest trim path takes over (the task and the newest step
    still fit, so the run goes on to its answer).
    """
    from sqlmodel import Session, select

    from iron_jarvis.core.models import EventRecord, SessionStatus

    platform, sess, calls = _compacting_run(
        tmp_path, window=40_000, task="Note the plain items in this list. " * 3_312,
        say_chars=5_400, steps=9,
    )
    assert sess.status is SessionStatus.COMPLETED, sess.summary
    with Session(platform.engine) as db:
        rows = db.exec(
            select(EventRecord).where(EventRecord.type == "context.compacted")
        ).all()
    assert calls == 1, f"compacted {calls}x when it could not help"
    assert len(rows) == 1


def test_a_run_with_room_to_spare_never_compacts(tmp_path):
    """The event is a signal, not noise."""
    from fastapi.testclient import TestClient
    from sqlmodel import Session, select

    from iron_jarvis.core.models import EventRecord
    from iron_jarvis.daemon.app import create_app

    client = TestClient(create_app(str(tmp_path)))
    client.put("/settings", json={"values": {"model_context_windows": {"mock": 400000}}})
    client.post("/sessions", json={"task": "write a short note", "wait": True})
    with Session(client.app.state.platform.engine) as db:
        rows = db.exec(
            select(EventRecord).where(EventRecord.type == "context.compacted")
        ).all()
    assert rows == []


def test_a_compaction_failure_never_breaks_a_run(tmp_path):
    """Compaction is an optimisation. A provider that throws mid-run must leave
    the run exactly as it was — the deterministic recap still handles overflow."""
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    client = TestClient(create_app(str(tmp_path)))
    client.put("/settings", json={"values": {"model_context_windows": {"mock": 2500}}})

    async def boom(system, user):
        raise RuntimeError("provider exploded")

    client.app.state.platform._compaction_complete = lambda *a, **k: boom

    r = client.post("/sessions", json={"task": "write a report", "wait": True})
    assert r.status_code == 200
    assert r.json()["status"] in ("completed", "failed")  # ran to a real end


def test_a_mock_only_install_runs_without_compacting(tmp_path):
    """No real model: no summary, no crash, no fabricated history."""
    from fastapi.testclient import TestClient
    from sqlmodel import Session, select

    from iron_jarvis.core.models import EventRecord
    from iron_jarvis.daemon.app import create_app

    client = TestClient(create_app(str(tmp_path)))
    client.put("/settings", json={"values": {"model_context_windows": {"mock": 2500}}})
    r = client.post("/sessions", json={"task": "write a report", "wait": True})
    assert r.status_code == 200
    with Session(client.app.state.platform.engine) as db:
        rows = db.exec(
            select(EventRecord).where(EventRecord.type == "context.compacted")
        ).all()
    assert rows == [], "the offline mock must never author a summary"
