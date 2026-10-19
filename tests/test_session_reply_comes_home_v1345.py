"""v1.345.0, lane T-sessions — an agent session's reply comes home.

THE BUG (the user's install, 2026-10-16): a hand-off the chat page opened was
finished by the PAGE — ``finalize()`` composed the reply and PUT the whole
array over whatever chat was open. Now every door the page opens from chat
carries ``chat_thread_id`` (``options["chat_thread_id"]`` on the row), and
EVERY terminal path of such a session appends ONE line to that chat through
``daemon.chat_append`` — the reply, its files, its receipt — idempotent by
``sessionId``. The page re-reads instead of composing.

Pinned here, on the real ``Orchestrator`` over ``build_platform`` with the
scripted mock adapter the v1.337.0 resume tests use (real registry, the real
``write_file`` tool, the real undo journal behind ``session_result``):

  T1 the three bodies carry ``chat_thread_id``; ``create_session`` stores it
     only when non-empty (kwarg over options); ``continue_session`` never
     inherits the parent's and may carry its own.
  T2 success appends exactly ONE line = ``line_for_session`` (the page's
     finalize shape under the daemon's keys): the reply, ``agentType``,
     ``sessionId``, ``panelWho`` and the created file's ABSOLUTE path; the
     failure and cancel paths append their sentence; the boot reconcile's
     interrupted outcome appends the row's words; a second finalize does
     not append twice; no ``chat_thread_id`` appends nothing (the thread's
     bytes and stamp untouched, the writer never called); the append runs
     OFF the loop; ``chat.thread_updated`` names the agent.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.agents import chat_delivery
from iron_jarvis.agents.chat_delivery import (
    CHAT_THREAD_OPTION,
    FAILED_SENTENCE,
    NO_RESPONSE,
    STOPPED_SENTENCE,
    content_for_session,
    line_for_session,
    participant_key_for,
)
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.outcome import session_result
from iron_jarvis.agents.types import AgentDefinition
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import (
    AgentType,
    ChatThreadRecord,
    Session,
    SessionStatus,
)
from iron_jarvis.daemon import chat_append
from iron_jarvis.daemon.schemas import ContinueBody, SessionCreate, SpawnBody
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMMessage, LLMResponse, ToolCall
from iron_jarvis.providers.adapters.mock import MockLLMAdapter
from iron_jarvis.scheduling.knobs import session_options


# --------------------------------------------------------------------------- #
# harness (the v1.337.0 shape)
# --------------------------------------------------------------------------- #


class _Recording(MockLLMAdapter):
    seen: list[list[LLMMessage]] = []

    async def complete(self, *, system, messages, tools, **kw):
        _Recording.seen.append(list(messages))
        return await super().complete(system=system, messages=messages, tools=tools, **kw)


def _script(p, responses):
    _Recording.seen = []
    p.providers.register("mock", lambda model=None: _Recording(script=list(responses)))


def _call(name, args, cid="c1"):
    return LLMResponse(tool_calls=[ToolCall(id=cid, name=name, arguments=args)], finish_reason="tool_use")


def _final(text):
    return LLMResponse(text=text, finish_reason="stop")


DEFN = AgentDefinition(type=AgentType.BUILDER, system_prompt="t", tools=["read_file", "write_file"])

SEED = [{"role": "user", "content": "@builder write me a brief", "at": "seed"}]


def _platform(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    return p, Orchestrator(p)


def _thread(engine, messages=None, title="brief") -> str:
    rec = ChatThreadRecord(title=title, messages_json=json.dumps(messages if messages is not None else list(SEED)))
    with session_scope(engine) as db:
        db.add(rec)
        db.commit()
        db.refresh(rec)
        return rec.id


def _row(engine, thread_id):
    with session_scope(engine) as db:
        r = db.get(ChatThreadRecord, thread_id)
        assert r is not None
        return {"messages": json.loads(r.messages_json or "[]"), "updated_at": r.updated_at}


def _session_row(engine, sid) -> Session:
    with session_scope(engine) as db:
        row = db.get(Session, sid)
        assert row is not None
        db.expunge(row)
        return row


async def _events(bus, type_, *, want: int = 1):
    """The events of ``type_`` a sync handler saw — waited for, never slept
    past (a bounded poll; the bus delivers sync handlers off the loop)."""
    seen: list = []
    bus.add_handler(lambda e: seen.append(e) if e.type == type_ else None)

    async def wait():
        for _ in range(200):
            if len(seen) >= want:
                return
            await asyncio.sleep(0.02)

    return seen, wait


# --------------------------------------------------------------------------- #
# T1 — the bodies, the door, the continuation
# --------------------------------------------------------------------------- #


def test_the_three_bodies_carry_chat_thread_id_additively():
    assert SessionCreate(task="x").chat_thread_id == ""
    assert SessionCreate(task="x", chat_thread_id="chat_a").chat_thread_id == "chat_a"
    assert SpawnBody(task="x").chat_thread_id == ""
    assert SpawnBody(task="x", chat_thread_id="chat_b").chat_thread_id == "chat_b"
    assert ContinueBody(message="x").chat_thread_id == ""
    assert ContinueBody(message="x", chat_thread_id="chat_c").chat_thread_id == "chat_c"


async def test_create_session_stores_the_id_only_when_non_empty_and_the_kwarg_wins(tmp_path):
    p, orch = _platform(tmp_path)
    s = await orch.create_session("go", AgentType.BUILDER, chat_thread_id="chat_a")
    assert session_options(s)[CHAT_THREAD_OPTION] == "chat_a"
    # trimmed; a door may put it in options; the kwarg wins over options
    s2 = await orch.create_session("go", AgentType.BUILDER, chat_thread_id="  chat_b  ")
    assert session_options(s2)[CHAT_THREAD_OPTION] == "chat_b"
    s3 = await orch.create_session("go", AgentType.BUILDER, options={CHAT_THREAD_OPTION: "chat_c"})
    assert session_options(s3)[CHAT_THREAD_OPTION] == "chat_c"
    s4 = await orch.create_session(
        "go", AgentType.BUILDER, options={CHAT_THREAD_OPTION: "chat_c"}, chat_thread_id="chat_d"
    )
    assert session_options(s4)[CHAT_THREAD_OPTION] == "chat_d"
    # nothing = no key at all (byte-identical to before); junk in options is dropped
    s5 = await orch.create_session("go", AgentType.BUILDER)
    assert CHAT_THREAD_OPTION not in session_options(s5)
    s6 = await orch.create_session("go", AgentType.BUILDER, chat_thread_id="   ")
    assert CHAT_THREAD_OPTION not in session_options(s6)
    s7 = await orch.create_session("go", AgentType.BUILDER, options={CHAT_THREAD_OPTION: 42})
    assert CHAT_THREAD_OPTION not in session_options(s7)
    # the other options are untouched by the rule
    s8 = await orch.create_session("go", AgentType.BUILDER, options={"skip_memory": True}, chat_thread_id="chat_e")
    assert session_options(s8) == {"skip_memory": True, CHAT_THREAD_OPTION: "chat_e"}


async def test_a_continuation_never_inherits_the_parents_chat_but_may_carry_its_own(tmp_path):
    p, orch = _platform(tmp_path)
    parent = await orch.create_session("go", AgentType.BUILDER, chat_thread_id="chat_parent")
    other = await orch.create_session("go", AgentType.BUILDER, chat_thread_id="chat_parent")
    assert session_options(parent)[CHAT_THREAD_OPTION] == "chat_parent"
    with session_scope(p.engine) as db:
        for sid in (parent.id, other.id):
            row = db.get(Session, sid)
            row.status = SessionStatus.COMPLETED
            db.add(row)
        db.commit()
    bare = await orch.continue_session(parent.id, "and now this")
    assert CHAT_THREAD_OPTION not in session_options(bare), session_options(bare)
    own = await orch.continue_session(other.id, "and this", chat_thread_id="chat_own")
    assert session_options(own)[CHAT_THREAD_OPTION] == "chat_own"


# --------------------------------------------------------------------------- #
# the pure shape — the page's finalize() under the daemon's keys
# --------------------------------------------------------------------------- #


def test_the_line_mirrors_the_pages_finalize_words_and_keys():
    done = SimpleNamespace(id="sess_1", status=SessionStatus.COMPLETED, summary="  Wrote it.  ",
                           agent_type=AgentType.BUILDER, agent_name="")
    result = {"found": True, "session_id": "sess_1", "documents": ["C:/w/brief.md", ""], "outcome": "done"}
    line = line_for_session(done, result, "builtin:builder")
    assert line == {
        "role": "assistant",
        "content": "Wrote it.",
        "sessionId": "sess_1",
        "agentType": "builder",
        "panelWho": "builtin:builder",
        "receipt": result,
        "documents": ["C:/w/brief.md"],
    }
    # the page's fallback words, by status
    empty_done = SimpleNamespace(id="s", status=SessionStatus.COMPLETED, summary="", agent_type=AgentType.BUILDER)
    assert content_for_session(empty_done) == NO_RESPONSE == "(no response)"
    # v1.352.0 (G16), moved ON PURPOSE: a stop is not a failure, and the two
    # endings read differently (the page's finalize says the same, pinned in
    # test_integration_pins_v1345).
    failed = SimpleNamespace(id="s", status=SessionStatus.FAILED, summary=None, agent_type=AgentType.BUILDER)
    assert content_for_session(failed) == FAILED_SENTENCE
    assert content_for_session(failed) == "The agent could not finish this. Try again or open the run."
    cancelled = SimpleNamespace(id="s", status=SessionStatus.CANCELLED, summary="", agent_type=AgentType.BUILDER)
    assert content_for_session(cancelled) == STOPPED_SENTENCE
    assert content_for_session(cancelled) == "The agent was stopped before it finished."
    # a summary wins over the fallback whatever the status (the page's `summary ||`)
    said = SimpleNamespace(id="s", status=SessionStatus.FAILED, summary="Session failed: boom", agent_type=AgentType.BUILDER)
    assert content_for_session(said) == "Session failed: boom"
    # no result (the ledger could not be read / not found) = no receipt, no documents
    bare = line_for_session(done, None, "builtin:builder")
    assert "receipt" not in bare and "documents" not in bare
    assert "receipt" not in line_for_session(done, {"found": False, "documents": ["x"]}, "")
    # no who = no panelWho; an empty document list = no key
    assert "panelWho" not in line_for_session(done, {"found": True, "documents": []}, "")
    assert "documents" not in line_for_session(done, {"found": True, "documents": []}, "x")


def test_the_participant_key_is_the_pages_own():
    """``POST /chat/panel`` answers ``who`` as ``<source>:<name>`` and the
    page maps ``dynamic:<n>`` back to ``custom:<n>`` (sessionTargetOf)."""
    assert participant_key_for(SimpleNamespace(agent_type=AgentType.BUILDER, agent_name="")) == "builtin:builder"
    assert participant_key_for(SimpleNamespace(agent_type=AgentType.RESEARCHER, agent_name=None)) == "builtin:researcher"
    assert participant_key_for(SimpleNamespace(agent_type=AgentType.BUILDER, agent_name="custom:tax-reader")) == "dynamic:tax-reader"
    assert participant_key_for(SimpleNamespace(agent_type=AgentType.BUILDER, agent_name="Custom:Tax ")) == "dynamic:Tax"
    assert participant_key_for(SimpleNamespace(agent_type=AgentType.BUILDER, agent_name="remote:hermes")) == "remote:hermes"
    # a bare builtin roster name is the builtin key
    assert participant_key_for(SimpleNamespace(agent_type=AgentType.BUILDER, agent_name="builder")) == "builtin:builder"


# --------------------------------------------------------------------------- #
# T2 — the success path, end to end on the real runtime
# --------------------------------------------------------------------------- #


async def test_a_session_asked_from_a_chat_appends_exactly_one_line_with_its_file(tmp_path):
    p, orch = _platform(tmp_path)
    tid = _thread(p.engine)
    before = _row(p.engine, tid)
    seen, wait = await _events(p.event_bus, EventType.CHAT_THREAD_UPDATED)
    s = await orch.create_session("write me a brief", AgentType.BUILDER, chat_thread_id=tid)
    _script(p, [_call("write_file", {"path": "brief.md", "content": "# Brief\n\nhello"}),
                _final("Wrote the brief.")])
    done = await asyncio.wait_for(orch.run_session(s.id, DEFN), timeout=60)
    assert done.status is SessionStatus.COMPLETED
    after = _row(p.engine, tid)
    assert len(after["messages"]) == len(before["messages"]) + 1, after["messages"]
    assert after["messages"][:-1] == before["messages"]
    line = after["messages"][-1]
    # the line IS the pure helper's answer for the same session + ledger,
    # stamped by the server (the integrator compares this with the page's own)
    row = _session_row(p.engine, s.id)
    # v1.352.0 (G19), moved ON PURPOSE: the line's card is the RECEIPT read
    # (what GET /sessions/{id}/result answers). The integrator's change hands
    # the orchestrator's provider manager + config through, so the card also
    # says where the run's model ran (`on_this_network`) — exactly the route's.
    receipt = session_result(p.engine, s.id, receipt=True, manager=p.providers, config=p.config)
    assert receipt["on_this_network"] is True  # the mock runs in-process
    expected = line_for_session(row, receipt, "builtin:builder")
    assert line["at"].endswith("+00:00")
    assert {k: v for k, v in line.items() if k != "at"} == expected
    assert line["role"] == "assistant"
    assert line["content"] == "Wrote the brief."
    assert line["sessionId"] == s.id
    assert line["agentType"] == "builder"
    assert line["panelWho"] == "builtin:builder"
    # the created file, ABSOLUTE, and it exists
    assert len(line["documents"]) == 1
    made = Path(line["documents"][0])
    assert made.is_absolute() and made.name == "brief.md" and made.exists()
    assert made == (Path(row.workspace_path) / "brief.md").resolve()
    # the receipt is the page's RunResult card (what GET /sessions/{id}/result answers)
    assert line["receipt"]["found"] is True and line["receipt"]["session_id"] == s.id
    assert line["receipt"]["files_created"] == ["brief.md"]
    # the stamp moved (the page's stale PUT 409s), the event names the agent
    assert after["updated_at"] != before["updated_at"]
    await wait()
    payloads = [e.payload for e in seen]
    assert payloads == [{"thread_id": tid, "who": "builtin:builder", "entries": 2}], payloads


async def test_the_append_runs_off_the_loop_and_before_the_completion_event(tmp_path, monkeypatch):
    p, orch = _platform(tmp_path)
    tid = _thread(p.engine)
    loop_thread = threading.get_ident()
    calls: list[dict] = []
    real = chat_append.append_to_chat_thread

    def spy(engine, thread_id, lines, **kw):
        calls.append({"thread": threading.get_ident(), "thread_id": thread_id, "n": len(lines)})
        return real(engine, thread_id, lines, **kw)

    monkeypatch.setattr(chat_append, "append_to_chat_thread", spy)
    order: list[str] = []
    p.event_bus.add_handler(
        lambda e: order.append("completed") if e.type == EventType.SESSION_COMPLETED else None
    )
    p.event_bus.add_handler(
        lambda e: order.append("appended") if e.type == EventType.CHAT_THREAD_UPDATED else None
    )
    s = await orch.create_session("say hi", AgentType.BUILDER, chat_thread_id=tid)
    _script(p, [_final("hi there")])
    await asyncio.wait_for(orch.run_session(s.id, DEFN), timeout=60)
    assert len(calls) == 1 and calls[0]["thread_id"] == tid and calls[0]["n"] == 1
    assert calls[0]["thread"] != loop_thread, "the append ran ON the event loop"
    for _ in range(200):
        if len(order) >= 2:
            break
        await asyncio.sleep(0.02)
    assert order == ["appended", "completed"], order


async def test_a_second_finalize_does_not_append_twice(tmp_path):
    p, orch = _platform(tmp_path)
    tid = _thread(p.engine)
    s = await orch.create_session("say hi", AgentType.BUILDER, chat_thread_id=tid)
    _script(p, [_final("hi there")])
    await asyncio.wait_for(orch.run_session(s.id, DEFN), timeout=60)
    once = _row(p.engine, tid)
    assert len(once["messages"]) == 2
    row = _session_row(p.engine, s.id)
    # the same terminal step again (a reconcile, a retried finalize): skipped
    again = await orch._deliver_to_chat(row)
    assert again["ok"] is True and again["appended"] == 0 and again["skipped"] == 1, again
    # ...and the failure finalizer over the same row appends nothing either
    await orch._finalize_failed(row, RuntimeError("late crash"))
    twice = _row(p.engine, tid)
    assert twice["messages"] == once["messages"]
    assert twice["updated_at"] == once["updated_at"]


async def test_no_chat_thread_id_appends_nothing_and_never_calls_the_writer(tmp_path, monkeypatch):
    p, orch = _platform(tmp_path)
    tid = _thread(p.engine)
    before = _row(p.engine, tid)
    calls: list = []
    monkeypatch.setattr(chat_append, "append_to_chat_thread", lambda *a, **k: calls.append(a))
    s = await orch.create_session("say hi", AgentType.BUILDER)
    _script(p, [_final("hi there")])
    done = await asyncio.wait_for(orch.run_session(s.id, DEFN), timeout=60)
    assert done.status is SessionStatus.COMPLETED
    row = _session_row(p.engine, s.id)
    await orch._finalize_failed(row, RuntimeError("x"))
    await orch._finalize_cancelled(row)
    assert calls == []
    after = _row(p.engine, tid)
    assert after["messages"] == before["messages"]
    assert after["updated_at"] == before["updated_at"]


# --------------------------------------------------------------------------- #
# T2 — the failure, cancel and interrupted paths append their sentence
# --------------------------------------------------------------------------- #


async def test_the_failure_path_appends_the_failure_sentence(tmp_path):
    p, orch = _platform(tmp_path)
    tid = _thread(p.engine)
    s = await orch.create_session("go", AgentType.BUILDER, chat_thread_id=tid)
    await orch._finalize_failed(s, RuntimeError("the provider blew up"))
    row = _session_row(p.engine, s.id)
    assert row.status is SessionStatus.FAILED
    msgs = _row(p.engine, tid)["messages"]
    assert len(msgs) == 2
    line = msgs[-1]
    # v1.354.0 (G28): plain words, never the exception class.
    assert line["content"] == row.summary == "The run failed: the provider blew up"
    assert line["content"] == content_for_session(row)
    assert line["sessionId"] == s.id and line["panelWho"] == "builtin:builder"
    assert line["receipt"]["status"] == "failed"
    assert "documents" not in line


async def test_the_cancel_path_appends_the_stopped_sentence(tmp_path):
    p, orch = _platform(tmp_path)
    tid = _thread(p.engine)
    s = await orch.create_session("go", AgentType.BUILDER, chat_thread_id=tid)
    await orch._finalize_cancelled(s)
    row = _session_row(p.engine, s.id)
    assert row.status is SessionStatus.CANCELLED
    msgs = _row(p.engine, tid)["messages"]
    assert len(msgs) == 2
    line = msgs[-1]
    # v1.359.0 (wave 9): the shared stopped sentence (orchestrator.
    # STOPPED_SUMMARY == chat_delivery.STOPPED_SENTENCE), never "Session
    # cancelled by the user." — moved on purpose.
    assert line["content"] == row.summary == "The agent was stopped before it finished."
    assert line["sessionId"] == s.id and line["panelWho"] == "builtin:builder"
    # a stop a tool time limit caused keeps its own reason (another session)
    tid2 = _thread(p.engine)
    s2 = await orch.create_session("go", AgentType.BUILDER, chat_thread_id=tid2)
    await orch._finalize_cancelled(s2, reason="stopped by the delegate's time limit")
    assert _row(p.engine, tid2)["messages"][-1]["content"] == "stopped by the delegate's time limit"


async def test_a_cancel_mid_run_lands_the_stopped_line_once(tmp_path):
    """The real cancel shape: the run's task is cancelled while the model is
    answering; ``run_session``'s CancelledError handler finalizes it."""
    p, orch = _platform(tmp_path)
    tid = _thread(p.engine)
    s = await orch.create_session("go", AgentType.BUILDER, chat_thread_id=tid)

    class _Slow(MockLLMAdapter):
        async def complete(self, *, system, messages, tools, **kw):
            await asyncio.sleep(30)
            return LLMResponse(text="too late", finish_reason="stop")

    p.providers.register("mock", lambda model=None: _Slow(script=[]))
    task = asyncio.ensure_future(orch.run_session(s.id, DEFN))
    for _ in range(200):
        if s.id in orch._running:
            break
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    row = _session_row(p.engine, s.id)
    assert row.status is SessionStatus.CANCELLED
    msgs = _row(p.engine, tid)["messages"]
    assert len(msgs) == 2 and msgs[-1]["content"] == "The agent was stopped before it finished."


def test_the_boot_reconcile_tells_the_chat_the_run_was_interrupted(tmp_path):
    p, orch = _platform(tmp_path)
    tid = _thread(p.engine)
    quiet = _thread(p.engine)
    with session_scope(p.engine) as db:
        cut = Session(task="go", status=SessionStatus.ACTIVE, agent_type=AgentType.BUILDER,
                      workspace_path=str(p.config.workspaces_dir / "x"),
                      options_json=json.dumps({CHAT_THREAD_OPTION: tid}))
        kept = Session(task="go", status=SessionStatus.ACTIVE, agent_type=AgentType.BUILDER,
                       summary="half done", workspace_path=str(p.config.workspaces_dir / "y"),
                       options_json=json.dumps({CHAT_THREAD_OPTION: tid}))
        nobody = Session(task="go", status=SessionStatus.ACTIVE, agent_type=AgentType.BUILDER,
                         workspace_path=str(p.config.workspaces_dir / "z"))
        db.add(cut)
        db.add(kept)
        db.add(nobody)
        db.commit()
        cut_id, kept_id = cut.id, kept.id
    assert orch.reconcile_interrupted_sessions() == 3
    msgs = _row(p.engine, tid)["messages"]
    assert len(msgs) == 3, msgs
    by_session = {m["sessionId"]: m for m in msgs[1:]}
    assert by_session[cut_id]["content"] == "Stopped when Iron Jarvis restarted."  # v1.354.0 G30: no "daemon" in a chat line
    assert by_session[kept_id]["content"] == "half done"
    for line in by_session.values():
        assert line["panelWho"] == "builtin:builder" and line["receipt"]["status"] == "failed"
    # the session with no chat told nobody
    assert _row(p.engine, quiet)["messages"] == SEED
    # the reconcile is idempotent for the chat too
    assert orch.reconcile_interrupted_sessions() == 0
    assert len(_row(p.engine, tid)["messages"]) == 3


async def test_a_custom_agents_line_is_attributed_as_the_page_does(tmp_path):
    p, orch = _platform(tmp_path)
    tid = _thread(p.engine)
    s = await orch.create_session(
        "go", AgentType.BUILDER, agent_name="custom:tax-reader", chat_thread_id=tid
    )
    _script(p, [_final("read it")])
    await asyncio.wait_for(orch.run_session(s.id, DEFN), timeout=60)
    line = _row(p.engine, tid)["messages"][-1]
    assert line["panelWho"] == "dynamic:tax-reader"
    assert line["agentType"] == "builder"


async def test_a_delivery_fault_never_fails_the_run(tmp_path, monkeypatch):
    p, orch = _platform(tmp_path)
    tid = _thread(p.engine)
    monkeypatch.setattr(
        chat_delivery, "deliver_session_line",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("the chat store is wedged")),
    )
    s = await orch.create_session("say hi", AgentType.BUILDER, chat_thread_id=tid)
    _script(p, [_final("hi there")])
    done = await asyncio.wait_for(orch.run_session(s.id, DEFN), timeout=60)
    assert done.status is SessionStatus.COMPLETED
    assert _row(p.engine, tid)["messages"] == SEED
    row = _session_row(p.engine, s.id)
    # each layer on its own: the sync step answers a fault in words...
    out = orch._deliver_to_chat_sync(row)
    assert out["ok"] is False and out["why"] == "delivery failed"
    # ...and the async step holds even when the sync step itself raises
    monkeypatch.setattr(
        orch, "_deliver_to_chat_sync",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("the thread hop blew up")),
    )
    out = await orch._deliver_to_chat(row)
    assert out["ok"] is False and out["why"] == "delivery failed"
    # a chat that no longer exists is said, not raised
    s2 = await orch.create_session("say hi", AgentType.BUILDER, chat_thread_id="chat_gone")
    monkeypatch.undo()
    out = await orch._deliver_to_chat(_session_row(p.engine, s2.id))
    assert out["ok"] is False and out["why"] == "no such chat"
