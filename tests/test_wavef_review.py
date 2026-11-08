"""Review of Wave F (daemon half) — the defects the independent review found,
each pinned red first.

1. A job's room (``home_chat_id`` set) could still be ADOPTED as a chat's
   @-panel room by a stale or crafted ``panel_thread_id``: ``for_chat`` only
   refused a room bound to another chat or to a project, so the report room
   of a job handed to a remote became the chat's @-room (the very thing the
   new column's comment says must never happen).
2. The runtime's ``approval.requested`` event carried the card's ``why``
   RAW — the registry, the session row and the listing mask and cut it, but
   the event itself is persisted and fans out (the bell, the phone, the
   audit) un-masked and uncut.
3. A run that ended FAILED without raising (its step limit) — the normal
   finalize path — said nothing of the files it kept: the builder's pin only
   drove the crash path.
4. ``web_service_add`` under Local only made an uncarded HTTP request to an
   internet address (the tool is not "reaches outside", so Don't ask runs it
   with no card): the check is skipped there and says so.
5. The key-saved check had no bound of its own: a service that trickles its
   answer held the secure card's Save for as long as it liked.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

from iron_jarvis.agents.threads import AgentThreads
from iron_jarvis.core.db import open_db, session_scope

# --------------------------------------------------------------------------- #
# 1. a job's room is never a chat's @-panel room
# --------------------------------------------------------------------------- #


def test_a_jobs_room_is_never_adopted_as_a_chats_panel_room(tmp_path):
    engine = open_db(tmp_path / "ij.db")
    threads = AgentThreads(engine)
    parts = [{"key": "remote:hermes", "source": "remote", "name": "hermes", "role": "helper"}]
    job = threads.create("hermes: summarise", parts, "", "chat_home")
    got = threads.for_chat("chat_home", adopt=job.id)
    assert got.id != job.id
    assert (threads.get(job.id).chat_thread_id or "") == ""
    # ...and from another chat too
    other = threads.for_chat("chat_other", adopt=job.id)
    assert other.id != job.id
    # control: an ordinary unbound room IS adopted
    plain = threads.create("Panel from chat", [])
    assert threads.for_chat("chat_third", adopt=plain.id).id == plain.id


# --------------------------------------------------------------------------- #
# 2. the event's why is masked and cut
# --------------------------------------------------------------------------- #


async def test_the_approval_events_why_is_masked_and_cut(tmp_path):
    from iron_jarvis.agents.orchestrator import Orchestrator
    from iron_jarvis.agents.types import AgentDefinition
    from iron_jarvis.core.approvals import _WHY_MAX
    from iron_jarvis.core.events import EventType
    from iron_jarvis.core.models import AgentType
    from iron_jarvis.platform import build_platform
    from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
    from iron_jarvis.providers.adapters.mock import MockLLMAdapter
    from iron_jarvis.tools.base import SpendCheck, Tool, ToolResult

    secret = "ghp_" + "A" * 36
    long_words = f"it would push with token={secret} " + "and more words " * 60

    class _Priced(Tool):
        name = "priced_thing"
        description = "a stand-in tool whose own check asks"
        input_schema = {"type": "object", "properties": {}}

        async def spend_check(self, args, ctx):
            return SpendCheck(ask=True, words=long_words, cost_note="asks")

        async def execute(self, args, ctx):
            return ToolResult(ok=True, output="ran")

    p = build_platform(str(tmp_path / "home"))
    p.registry.register(_Priced())
    call = LLMResponse(
        tool_calls=[ToolCall(id="c1", name="priced_thing", arguments={})], finish_reason="tool_use"
    )
    p.providers.register(
        "mock",
        lambda model=None: MockLLMAdapter(script=[call, LLMResponse(text="Done.", finish_reason="stop")]),
    )
    seen: dict = {}

    def _on_ask(e):
        if e.type == EventType.APPROVAL_REQUESTED:
            seen["event"] = dict(e.payload)
            seen["pending"] = p.approvals.pending_for(e.session_id)
            p.approvals.resolve(e.payload["approval_id"], "deny")

    p.event_bus.add_handler(_on_ask)
    orch = Orchestrator(p)
    defn = AgentDefinition(type=AgentType.BUILDER, system_prompt="t", tools=["priced_thing"])
    s = await orch.create_session("do the thing", AgentType.BUILDER, origin="job:mission")
    await asyncio.wait_for(orch.run_session(s.id, defn), timeout=60)
    why = seen["event"]["why"]
    assert why.startswith("it would push with token"), why
    assert secret not in why
    assert len(why) <= _WHY_MAX
    listed = seen["pending"][0]["why"]
    assert secret not in listed and len(listed) <= _WHY_MAX


# --------------------------------------------------------------------------- #
# 3. a run that FAILED at its step limit names the file it kept
# --------------------------------------------------------------------------- #


async def test_a_run_that_failed_without_raising_says_the_file_it_kept(tmp_path):
    from iron_jarvis.agents.orchestrator import Orchestrator
    from iron_jarvis.core.models import AgentType, Session, SessionStatus
    from iron_jarvis.platform import build_platform
    from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
    from iron_jarvis.providers.adapters.mock import MockLLMAdapter

    p = build_platform(str(tmp_path / "home"))

    class _Loops(MockLLMAdapter):
        n = 0

        async def complete(self, *a, **kw):  # type: ignore[override]
            _Loops.n += 1
            return LLMResponse(
                tool_calls=[ToolCall(
                    id=f"c{_Loops.n}", name="write_file",
                    arguments={"path": f"part{_Loops.n}.md", "content": "x"},
                )],
                finish_reason="tool_use",
            )

    p.providers.register("mock", lambda model=None: _Loops())
    orch = Orchestrator(p)
    s = await orch.create_session("write the parts", AgentType.BUILDER, provider="mock", max_steps=2)
    await asyncio.wait_for(orch.run_session(s.id), timeout=60)
    with session_scope(p.engine) as db:
        row = Session(**db.get(Session, s.id).model_dump())
    assert row.status is SessionStatus.FAILED, row.summary
    assert "kept: part1.md" in row.summary, row.summary


# --------------------------------------------------------------------------- #
# 4/5. the web service check: Local only, and its own bound
# --------------------------------------------------------------------------- #


async def test_under_local_only_an_internet_service_is_not_tested(tmp_path, monkeypatch):
    from iron_jarvis.platform import build_platform
    from iron_jarvis.providers import egress
    from iron_jarvis.settings import records_tools as RT
    from iron_jarvis.tools.base import ToolContext

    monkeypatch.setattr(RT, "AUTO_CHECK", True)
    p = build_platform(str(tmp_path / "home"))
    tested: list = []
    monkeypatch.setattr(RT, "check_service", lambda platform, iid: tested.append(iid) or (True, "x"))
    ctx = ToolContext(
        workspace=tmp_path, session_id="chat", agent_run_id="r", config=p.config,
        event_bus=p.event_bus, engine=p.engine,
    )
    with egress.local_only_scope(True):
        res = await RT.WebServiceAddTool(p).execute(
            {"name": "Pricing", "address": "https://api.example.com/v1"}, ctx
        )
    assert res.ok, res.error
    assert tested == []
    assert RT.SERVICE_NOT_TESTED_LOCAL_ONLY in res.output
    # control: outside Local only it is tested
    res2 = await RT.WebServiceAddTool(p).execute(
        {"name": "Pricing two", "address": "https://api.example.com/v2"}, ctx
    )
    assert res2.ok and tested, res2.output


def test_the_key_saved_check_is_bounded(tmp_path, monkeypatch):
    from iron_jarvis.settings import records_tools as RT

    monkeypatch.setattr(RT, "AUTO_CHECK", True)
    monkeypatch.setattr(RT, "SERVICE_CHECK_TIMEOUT_S", 0.3)
    gate = threading.Event()

    def slow(platform, iid):
        gate.wait(10)
        return True, "late"

    monkeypatch.setattr(RT, "check_service", slow)

    class _Reg:
        def get_spec(self, iid):
            return object()

    class _P:
        integrations = _Reg()

    t0 = time.monotonic()
    out = RT.on_web_service_key_saved(_P(), "svc")
    took = time.monotonic() - t0
    gate.set()
    assert took < 5, took
    assert out["check"]["answering"] is False
    assert "did not answer in time" in out["check"]["words"]


def test_a_long_why_is_cut_in_the_registry():
    """The builder's 400-character cut was unpinned (a mutation removing it
    stayed green: the runtime's ``card_why`` already cuts its own words) — a
    registry caller that is not the runtime still gets one bounded line."""
    from iron_jarvis.core.approvals import _WHY_MAX, ChatApprovals

    reg = ChatApprovals()

    async def go():
        aid, _f = reg.request("shell", {}, session_id="s1", why="word " * 300, asker="model")
        rows = reg.pending_for("s1")
        reg.pop(aid)
        return rows

    rows = asyncio.run(go())
    assert 0 < len(rows[0]["why"]) <= _WHY_MAX


def test_the_daemons_remote_message_title_is_the_fixtures():
    """``comm/notifier.remote_message_title`` said one fixture held it equal to
    the bell and the tray — nothing read it against the fixture, so the twin
    could drift unseen. It is read here, for every remote case with a title."""
    from pathlib import Path

    from iron_jarvis.comm.notifier import remote_message_title

    fixture = Path(__file__).resolve().parent.parent / "dashboard" / "__tests__" / "fixtures" / "one-notice-v13600.json"
    cases = [
        c for c in json.loads(fixture.read_text(encoding="utf-8"))["cases"]
        if c["event"]["type"] == "remote.message" and c.get("title")
    ]
    assert len(cases) >= 2
    for c in cases:
        assert remote_message_title(c["event"]["payload"]) == c["title"], c["case"]
    assert remote_message_title({"agent": "hermes", "kind": "progress"}) == "hermes sent an update"
    assert remote_message_title({}) == "A remote agent sent a message"


def test_an_older_database_gains_both_new_columns_and_still_reads(tmp_path):
    """The two additive columns reach a database older than this change (the
    reconciler), and a row written before them (NULL there) still reads: the
    room is no job's, the rule has no link."""
    from sqlalchemy import text as _text

    import iron_jarvis.reflex.models  # noqa: F401 — the rule table is mapped

    path = tmp_path / "ij.db"
    engine = open_db(path)
    threads = AgentThreads(engine)
    old_room = threads.create("Panel from chat", [])
    with engine.begin() as conn:
        conn.execute(_text('ALTER TABLE agentthreadrecord DROP COLUMN "home_chat_id"'))
        conn.execute(_text('ALTER TABLE reflexrule DROP COLUMN "last_href"'))
    engine.dispose()
    engine = open_db(path)
    with engine.connect() as conn:
        rooms = {r[1] for r in conn.execute(_text('PRAGMA table_info("agentthreadrecord")'))}
        rules = {r[1] for r in conn.execute(_text('PRAGMA table_info("reflexrule")'))}
    assert "home_chat_id" in rooms and "last_href" in rules
    threads = AgentThreads(engine)
    assert threads.get(old_room.id) is not None
    # the old room (NULL home) is still adopted as an ordinary unbound room
    assert threads.for_chat("chat_a", adopt=old_room.id).id == old_room.id
