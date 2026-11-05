"""v1.435.0 review (chat-first plan, Wave 3 group I): the defects an
independent review found in Goals from Chat, Local only from chat and the
change history — each pinned red against the builder's commit (9f0451e1)
and green after the fix.

1. Local only and an undo act ONLY in a chat on the Chat page: never from an
   agent run (its card can be answered from the PHONE), never from the
   browser sidebar (no saved chat — "Local only is now on" was a lie).
2. Turning Local only off and an undo are SAFETY cards (``once_reason``
   "privacy" / "undo"): Enter never allows them, the phone never gets them.
3. ``config_undo`` (ask tier, runs with no card under Don't ask) never puts a
   setting back at a value that always asks — "Start new chats local only".
4. "From your next message" holds for a NOTE typed while the reply runs.
5. Two undo calls in one round each undo exactly the change their card named.
6. Undo of a goal from chat never rewinds its books (spend, checkpoint) and
   never gives back an always-allow the user revoked since.
7. The history never tells a turn that may not recall Local only chats the
   NAME of a change made in Local only work.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from pathlib import Path

import pytest
from sqlmodel import select

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.types import AgentDefinition
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.ids import utcnow
from iron_jarvis.core.models import AgentRun, AgentType, ToolInvocation, UndoJournal
from iron_jarvis.core.turns import TURNS, TurnHandle
from iron_jarvis.harness import stall
from iron_jarvis.platform import build_platform
from iron_jarvis.providers import egress
from iron_jarvis.providers import failure_words as FW
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.adapters.mock import MockLLMAdapter
from iron_jarvis.settings import records_history as H
from tests.test_chat_approvals_v1187 import _asgi_post, _drive_stream
from tests.test_chat_history_tools_v14350 import (
    LOCAL,
    UNDO,
    _app,
    _approver,
    _cards,
    _contract,
    _ctx,
    _finished,
    _never,
    _out,
    _setup,
    _thread,
    _three_changes,
    _uploads,
)
from tests.test_settings_chat_tools_v1321 import _body, _calls_stream, _undo_card

ROOT = Path(__file__).resolve().parents[1]


# ===========================================================================
# 1. the Chat page only
# ===========================================================================


@pytest.mark.asyncio
async def test_local_only_from_the_browser_sidebar_changes_nothing_and_says_so(tmp_path):
    """The sidebar's turn has a turn id and no saved chat: the first cut
    remembered the choice for a link that never comes and answered "Local only
    is now on for this chat"."""
    app = _app(tmp_path, [])
    p = app.state.platform
    tool = p.registry.get(LOCAL)
    ctx = _ctx(p, tmp_path, turn_id="panel_turn_1")
    token = FW.set_sidebar_surface()
    try:
        res = await tool.execute({"on": True}, ctx)
        check = await tool.spend_check({"on": False}, ctx)
    finally:
        FW.reset_surface(token)
    assert res.ok is False and "Chat page" in (res.error or ""), res
    assert egress.take_pending_local_only("panel_turn_1") is None, "nothing remembered for a link that never comes"
    assert check is not None and check.ask is False, "no card where nobody can see the chat it changes"


@pytest.mark.asyncio
async def test_an_undo_from_the_browser_sidebar_is_refused(tmp_path):
    app = _app(tmp_path, [])
    p = app.state.platform
    await _three_changes(app, tmp_path)
    tool = p.registry.get(UNDO)
    ctx = _ctx(p, tmp_path, turn_id="panel_turn_2")
    token = FW.set_sidebar_surface()
    try:
        check = await tool.spend_check({"kind": "file"}, ctx)
        res = await tool.execute({"kind": "file"}, ctx)
    finally:
        FW.reset_surface(token)
    assert check is not None and check.ask is False and res.ok is False and "Chat page" in (res.error or "")
    assert (_uploads(p) / "notes.txt").read_text(encoding="utf-8") == "second draft\n"


def _run_env(tmp_path, call: ToolCall):
    p = build_platform(str(tmp_path / "home"))
    p.providers.register(
        "mock",
        lambda model=None: MockLLMAdapter(
            script=[LLMResponse(tool_calls=[call], finish_reason="tool_use"), LLMResponse(text="Done.", finish_reason="stop")]
        ),
    )
    return p, Orchestrator(p)


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["chat", "job:mission-member"])
async def test_an_agent_run_never_parks_local_only_off_where_the_phone_could_answer(tmp_path, origin):
    """An ATTENDED run's ask is an ``approval.requested`` — a default phone
    alert that comm/prompts makes answerable ("reply here: approve"). Before
    the fix the run paused, and "once" from the phone turned the chat's Local
    only off."""
    p, orch = _run_env(tmp_path, ToolCall(id="c1", name=LOCAL, arguments={"on": False}))
    tid = _thread(p, setup={"local_only": True})
    asked: list[dict] = []

    def _answer(e):
        if e.type == EventType.APPROVAL_REQUESTED:
            asked.append(dict(e.payload))
            p.approvals.resolve(e.payload["approval_id"], "once")

    p.event_bus.add_handler(_answer)
    defn = AgentDefinition(type=AgentType.BUILDER, system_prompt="t", tools=[LOCAL])
    s = await orch.create_session("keep going", AgentType.BUILDER, origin=origin, chat_thread_id=tid)
    await asyncio.wait_for(orch.run_session(s.id, defn), timeout=60)
    assert asked == [], "an agent run never asks to turn Local only off"
    assert _setup(p, tid).get("local_only") is True
    with session_scope(p.engine) as db:
        (row,) = db.exec(select(ToolInvocation).where(ToolInvocation.tool == LOCAL)).all()
        assert row.ok is False and "Chat page" in (row.output or "")


@pytest.mark.asyncio
async def test_an_agent_run_never_parks_an_undo_where_the_phone_could_answer(tmp_path):
    p, orch = _run_env(tmp_path, ToolCall(id="c1", name=UNDO, arguments={}))
    with session_scope(p.engine) as db:
        db.add(ToolInvocation(id="tool_w", session_id="chat", agent_run_id="chat", tool="write_file",
                              ok=True, output="wrote", reversibility="reversible"))
        db.add(UndoJournal(action_id="tool_w", session_id="chat", tool="write_file", kind="file_delete",
                           reversible=True, pre_inline=json.dumps({"path": "a.txt", "mode": "text"})))
        db.commit()
    asked: list[dict] = []
    p.event_bus.add_handler(
        lambda e: asked.append(dict(e.payload)) if e.type == EventType.APPROVAL_REQUESTED else None
    )
    defn = AgentDefinition(type=AgentType.BUILDER, system_prompt="t", tools=[UNDO])
    s = await orch.create_session("undo it", AgentType.BUILDER, origin="chat")
    await asyncio.wait_for(orch.run_session(s.id, defn), timeout=60)
    assert asked == []
    with session_scope(p.engine) as db:
        assert db.get(ToolInvocation, "tool_w").undone_at is None


# ===========================================================================
# 2. safety cards
# ===========================================================================


@pytest.mark.asyncio
async def test_turning_local_only_off_is_a_safety_card_enter_and_the_phone_never_answer(tmp_path):
    app = _app(tmp_path, [(LOCAL, {"on": False})])
    p = app.state.platform
    tid = _thread(p, setup={"local_only": True})
    seen: list = []
    await _drive_stream(app, _body("turn off local only", thread_id=tid, local_only=True), _approver(app, seen))
    (card,) = seen
    assert card["once_only"] is True and card.get("once_reason") == H.LOCAL_ONLY_REASON
    assert H.LOCAL_ONLY_REASON in stall.SAFETY_REASONS
    assert not stall.phone_may_ask(once_only=True, local_ask=False, priced=False)


@pytest.mark.asyncio
async def test_an_undo_from_chat_is_a_safety_card(tmp_path):
    app = _app(tmp_path, [])
    p = app.state.platform
    await _three_changes(app, tmp_path)
    p.router.stream = _calls_stream([(UNDO, {"kind": "file"})])
    seen: list = []
    await _drive_stream(app, _body("undo the last file edit"), _approver(app, seen))
    (card,) = seen
    assert card["once_only"] is True and card.get("once_reason") == H.UNDO_REASON
    assert H.UNDO_REASON in stall.SAFETY_REASONS


def test_the_dashboard_knows_both_reasons_as_safety_reasons():
    ts = (ROOT / "dashboard" / "lib" / "onceReason.ts").read_text(encoding="utf-8")
    reasons = ts.split("const REASONS")[1].split(";")[0]
    for r in (H.LOCAL_ONLY_REASON, H.UNDO_REASON):
        assert f'"{r}"' in reasons, r


# ===========================================================================
# 3. config_undo never widens a setting that always asks
# ===========================================================================


@pytest.mark.asyncio
async def test_config_undo_never_turns_start_new_chats_local_only_off_uncarded(tmp_path):
    app = _app(tmp_path, [("config_set", {"key": "chat_new_local_only", "value": True})])
    p = app.state.platform
    await _drive_stream(app, _body("start new chats local only"), _never)
    assert p.config.chat_new_local_only is True
    p.router.stream = _calls_stream([("config_undo", {})])
    frames = await _drive_stream(app, _body("undo that", mode="yolo"), _never)
    (fin,) = _finished(frames, "config_undo")
    assert fin["ok"] is False and "change_undo" in _out(fin), _out(fin)
    assert p.config.chat_new_local_only is True, "Don't ask must not turn it off with no card"
    p.router.stream = _calls_stream([(UNDO, {})])
    seen: list = []
    await _drive_stream(app, _body("undo that", mode="yolo"), _approver(app, seen))
    assert [s["tool"] for s in seen] == [UNDO]
    assert p.config.chat_new_local_only is False


@pytest.mark.asyncio
async def test_control_config_undo_still_undoes_an_ordinary_setting(tmp_path):
    app = _app(tmp_path, [("config_set", {"key": "chat_followups", "value": True})])
    p = app.state.platform
    await _drive_stream(app, _body("turn on follow-up questions"), _never)
    assert p.config.chat_followups is True
    p.router.stream = _calls_stream([("config_undo", {})])
    frames = await _drive_stream(app, _body("undo that", mode="yolo"), _never)
    (fin,) = _finished(frames, "config_undo")
    assert fin["ok"] is True, _out(fin)
    assert p.config.chat_followups is False


# ===========================================================================
# 4. a note typed after "keep this chat on my PC" waits for the next message
# ===========================================================================


def _steering_stream(turn_id: str, seen: dict):
    rounds = {"n": 0}

    async def fake_stream(*, provider=None, model=None, system, messages, tools, session_id=None, task_class=None, **kw):
        i = rounds["n"]
        rounds["n"] += 1
        seen.setdefault("messages", []).append(json.dumps([getattr(m, "content", "") for m in messages]))
        if i == 0:
            # A note typed BEFORE the tool runs (queued for this reply).
            seen["before"] = TURNS.steer(turn_id, "NOTE-BEFORE client 123-45-6789")
            yield {
                "type": "final",
                "response": LLMResponse(text="", tool_calls=[ToolCall(id="c0", name=LOCAL, arguments={"on": True})]),
                "provider": "mock", "model": "mock",
            }
        elif i == 1:
            # A note typed AFTER Local only was turned on.
            seen["after"] = TURNS.steer(turn_id, "NOTE-AFTER client 987-65-4321")
            yield {
                "type": "final",
                "response": LLMResponse(text="", tool_calls=[ToolCall(id="c1", name="config_list", arguments={})]),
                "provider": "mock", "model": "mock",
            }
        else:
            yield {"type": "final", "response": LLMResponse(text="Done."), "provider": "mock", "model": "mock"}

    return fake_stream


@pytest.mark.asyncio
async def test_a_note_typed_while_the_reply_runs_waits_for_the_next_local_message(tmp_path):
    app = _app(tmp_path, [])
    p = app.state.platform
    tid = _thread(p)
    seen: dict = {}
    p.router.stream = _steering_stream("turn_lo_steer", seen)
    body = {**_body("keep this chat on my PC", thread_id=tid), "turn_id": "turn_lo_steer"}
    frames = await _drive_stream(app, body, _never)
    later = "".join(seen["messages"][1:])
    assert "NOTE-BEFORE" not in later and "NOTE-AFTER" not in later, "no note reached this (cloud) reply"
    assert seen["before"] is True and seen["after"] is False, seen
    (fin,) = _finished(frames, LOCAL)
    assert fin["ok"] is True and "stays in the box" in _out(fin)
    done = next(d for ev, d in frames if ev == "done")
    assert done.get("unread_steers") == ["NOTE-BEFORE client 123-45-6789"], "the queued note goes back to the box"


def test_hold_steers_returns_queued_notes_once_and_takes_none_after():
    h = TurnHandle("t")
    assert h.queue_steer("a") is True
    h.hold_steers()
    assert h.take_steers() == []
    assert h.queue_steer("b") is False
    assert h.close_steers() == ["a"]
    assert h.close_steers() == []


@pytest.mark.asyncio
async def test_control_turning_local_only_on_in_a_local_reply_leaves_notes_alone(tmp_path):
    """A reply already Local only (the page's switch) needs no hold: its notes
    stay on this network."""
    app = _app(tmp_path, [])
    p = app.state.platform
    tid = _thread(p)
    seen: dict = {}
    p.router.stream = _steering_stream("turn_lo_local", seen)
    body = {**_body("keep this chat on my PC", thread_id=tid, local_only=True), "turn_id": "turn_lo_local"}
    await _drive_stream(app, body, _never)
    assert seen["after"] is True


# ===========================================================================
# 5. two undo calls in one round
# ===========================================================================


def _two_calls_stream(calls):
    rounds = {"n": 0}

    async def fake_stream(*, provider=None, model=None, system, messages, tools, session_id=None, task_class=None, **kw):
        i = rounds["n"]
        rounds["n"] += 1
        if i == 0:
            yield {"type": "final", "response": LLMResponse(text="", tool_calls=calls), "provider": "mock", "model": "mock"}
        else:
            yield {"type": "final", "response": LLMResponse(text="Done."), "provider": "mock", "model": "mock"}

    return fake_stream


@pytest.mark.asyncio
async def test_two_undo_calls_in_one_round_undo_only_what_each_card_named(tmp_path):
    app = _app(tmp_path, [])
    p = app.state.platform
    up = _uploads(p)
    (up / "notes.txt").write_text("first draft\n", encoding="utf-8")
    p.router.stream = _calls_stream([("write_file", {"path": "notes.txt", "content": "second draft\n"})])
    await _drive_stream(app, {**_body("rewrite notes.txt"), "tools": ["write_file"]}, _never)
    p.router.stream = _calls_stream([("write_file", {"path": "other.txt", "content": "made\n"})])
    await _drive_stream(app, {**_body("write other.txt"), "tools": ["write_file"]}, _never)
    p.router.stream = _two_calls_stream(
        [ToolCall(id="u1", name=UNDO, arguments={"kind": "file"}), ToolCall(id="u2", name=UNDO, arguments={"kind": "file"})]
    )
    seen: list = []
    frames = await _drive_stream(app, _body("undo the last file edit"), _approver(app, seen))
    assert len(seen) == 2 and all("other.txt" in s["why"] for s in seen), [s["why"] for s in seen]
    assert not (up / "other.txt").exists(), "the change both cards named is undone"
    assert (up / "notes.txt").read_text(encoding="utf-8") == "second draft\n", (
        "the second card named other.txt: it must never undo notes.txt"
    )
    outs = [_out(f) for f in _finished(frames, UNDO)]
    assert any("That change was already undone" in o for o in outs), outs


# ===========================================================================
# 6. goal undo keeps the books and the user's revokes
# ===========================================================================


@pytest.mark.asyncio
async def test_undo_of_a_paused_goal_keeps_its_spend_and_a_grant_the_user_revoked(tmp_path):
    app = _app(tmp_path, [])
    p = app.state.platform
    g = _contract(p)
    grant = p.grants.create("goal", g.id, "read_file", "", "read files")
    p.router.stream = _calls_stream([("goal_update", {"goal": g.id, "status": "paused"})])
    frames = await _drive_stream(app, _body("pause the bookkeeping goal"), _approver(app, []))
    (card,) = [c for c in _cards(frames) if c.get("key") == f"goal.{g.id}"]
    # Work lands on the goal's books after the change (an iteration that was
    # running settles), and the user revokes its always-allow on a page.
    p.goal_engine.store.add_spend(g.id, tokens=1234, dollars=0.5, iterations=1, session_id="sess_x")
    p.goal_engine.store.set_checkpoint(g.id, {"last_session_id": "sess_x"})
    p.grants.revoke(grant.id)
    await _undo_card(app, card)
    rec = p.goal_engine.store.get(g.id)
    assert rec.state == "active"
    assert rec.decoded_spent()["tokens"] == 1234, "an undo never rewinds what the goal spent"
    assert "running_session_id" not in rec.decoded_checkpoint() and rec.decoded_checkpoint().get("last_session_id") == "sess_x"
    assert p.grants.get(grant.id).revoked_at is not None, "a grant the user revoked stays revoked"


@pytest.mark.asyncio
async def test_undo_of_a_stop_never_puts_back_the_cancelled_runs_marker(tmp_path):
    app = _app(tmp_path, [])
    p = app.state.platform
    g = _contract(p)
    p.goal_engine.store.set_checkpoint(g.id, {"running_session_id": "sess_run"})
    p.router.stream = _calls_stream([("goal_update", {"goal": g.id, "status": "stopped"})])
    frames = await _drive_stream(app, _body("stop the bookkeeping goal"), _approver(app, []))
    (card,) = [c for c in _cards(frames) if c.get("key") == f"goal.{g.id}"]
    # The stopped run's cancel handler clears the marker and bills its spend.
    p.goal_engine.store.add_spend(g.id, tokens=77, iterations=1, session_id="sess_run")
    p.goal_engine.store.set_checkpoint(g.id, {"last_session_id": "sess_run"})
    await _undo_card(app, card)
    rec = p.goal_engine.store.get(g.id)
    assert rec.state == "active"
    assert rec.decoded_checkpoint().get("running_session_id") is None, (
        "a marker for a run that ended reads as interrupted at the next boot (a false breaker strike)"
    )
    assert rec.decoded_spent()["tokens"] == 77


@pytest.mark.asyncio
async def test_control_undo_of_a_delete_still_gives_its_grants_back(tmp_path):
    app = _app(tmp_path, [])
    p = app.state.platform
    g = _contract(p)
    grant = p.grants.create("goal", g.id, "read_file", "", "read files")
    p.router.stream = _calls_stream([("goal_delete", {"goal": g.id})])
    frames = await _drive_stream(app, _body("delete the bookkeeping goal"), _approver(app, []))
    (card,) = [c for c in _cards(frames) if c.get("key") == f"goal.{g.id}"]
    assert p.grants.get(grant.id).revoked_at is not None
    await _undo_card(app, card)
    assert p.goal_engine.store.get(g.id) is not None
    assert p.grants.get(grant.id).revoked_at is None


# ===========================================================================
# 7. the history keeps Local only work's names out of a cloud turn
# ===========================================================================


def _local_work(p):
    """A file edit made in a Local only chat's turn (inside its window)."""
    tid = _thread(p, tid="chat_secret", setup={"local_only": True})
    t0 = utcnow() - timedelta(minutes=5)
    with session_scope(p.engine) as db:
        db.add(AgentRun(id="run_lo", session_id="chat", thread_id=tid, created_at=t0,
                        finished_at=t0 + timedelta(seconds=30)))
        db.add(ToolInvocation(id="tool_lo", session_id="chat", agent_run_id="chat", tool="write_file",
                              ok=True, output="wrote Johnson_K1_amended.xlsx", reversibility="reversible",
                              created_at=t0 + timedelta(seconds=10)))
        db.add(UndoJournal(action_id="tool_lo", session_id="chat", tool="write_file", kind="file_delete",
                           reversible=True,
                           pre_inline=json.dumps({"path": "Johnson_K1_amended.xlsx", "mode": "text"})))
        db.commit()
    return tid


@pytest.mark.asyncio
async def test_the_history_never_names_local_only_work_to_a_cloud_turn(tmp_path):
    app = _app(tmp_path, [("changes_list", {})])
    p = app.state.platform
    _local_work(p)
    frames = await _drive_stream(app, _body("what did you change today?"), _never)
    (fin,) = _finished(frames, "changes_list")
    assert "Johnson" not in _out(fin) and "Local only chat" in _out(fin), _out(fin)
    # A Local only turn may name it (its words stay on this network).
    p.router.stream = _calls_stream([("changes_list", {})])
    frames = await _drive_stream(app, _body("what did you change today?", local_only=True), _never)
    (fin,) = _finished(frames, "changes_list")
    assert "Johnson_K1_amended.xlsx" in _out(fin)


@pytest.mark.asyncio
async def test_an_undo_tells_a_cloud_turn_what_was_done_never_the_local_name(tmp_path):
    app = _app(tmp_path, [(UNDO, {"action_id": "tool_lo"})])
    p = app.state.platform
    _local_work(p)
    (_uploads(p) / "Johnson_K1_amended.xlsx").write_text("x", encoding="utf-8")
    seen: list = []
    frames = await _drive_stream(app, _body("undo that"), _approver(app, seen))
    assert "Johnson_K1_amended.xlsx" in seen[0]["why"], "the person's card names it"
    (fin,) = _finished(frames, UNDO)
    assert "Johnson" not in json.dumps(fin), fin


@pytest.mark.asyncio
async def test_control_a_cloud_chats_own_change_is_named(tmp_path):
    app = _app(tmp_path, [])
    p = app.state.platform
    await _three_changes(app, tmp_path)
    p.router.stream = _calls_stream([("changes_list", {})])
    frames = await _drive_stream(app, _body("what did you change today?"), _never)
    (fin,) = _finished(frames, "changes_list")
    assert "notes.txt" in _out(fin) and "Local only chat" not in _out(fin)


def test_sessions_and_build_rows_of_local_only_work_are_withheld(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    tid = _thread(p, tid="chat_code", setup={"local_only": True})
    from iron_jarvis.core.models import Session

    with session_scope(p.engine) as db:
        db.add(Session(id="sess_lo", task="t", options_json=json.dumps({"local_only": True})))
        db.add(Session(id="sess_cloud", task="t", options_json="{}"))
        db.commit()
        rows = [
            ToolInvocation(id="a", session_id="sess_lo", agent_run_id="r", tool="write_file"),
            ToolInvocation(id="b", session_id="sess_cloud", agent_run_id="r", tool="write_file"),
            ToolInvocation(id="c", session_id=f"build:{tid}", agent_run_id="r", tool="write_file"),
            ToolInvocation(id="d", session_id="build:other", agent_run_id="r", tool="write_file"),
            ToolInvocation(id="e", session_id="settings", agent_run_id="r", tool="update_settings"),
        ]
        assert H.local_only_rows(db, p.engine, rows) == {"a", "c"}


# ===========================================================================
# sentinels: no value, secret or file content in any frame of the history
# ===========================================================================


@pytest.mark.asyncio
async def test_no_value_secret_or_content_reaches_any_frame_of_the_history_tools(tmp_path):
    secret = "sk-test-SENTINEL-KEY-0123456789abcdef"
    url_pw = "https://alice:PWSENTINEL99@gw.example/v1"
    url_two = "https://VALUESENTINEL.example/v1"
    body_text = "FILESENTINEL contents of the client letter\n"
    app = _app(tmp_path, [])
    p = app.state.platform
    (_uploads(p) / "letter.txt").write_text("old letter\n", encoding="utf-8")
    p.router.stream = _calls_stream([("write_file", {"path": "letter.txt", "content": body_text})])
    await _drive_stream(app, {**_body("rewrite letter.txt"), "tools": ["write_file"]}, _never)
    for value in (url_pw, url_two):
        p.router.stream = _calls_stream([("config_change", {"key": "custom_base_url", "value": value})])
        await _drive_stream(app, _body(f"set my custom endpoint url to {value}"), _approver(app, []))
    assert p.config.custom_base_url == url_two
    status, _ = await _asgi_post(app, "/config/secret", {"name": "connection.openai", "value": secret})
    assert status == 200
    frames: list = []
    p.router.stream = _calls_stream([("changes_list", {"hours": 24})])
    frames += await _drive_stream(app, _body("what did you change today?"), _never)
    seen: list = []
    p.router.stream = _calls_stream([(UNDO, {"kind": "setting"})])
    frames += await _drive_stream(app, _body("undo the last setting change"), _approver(app, seen))
    dump = json.dumps(frames) + json.dumps(seen)
    for sentinel in (secret, "PWSENTINEL99", "VALUESENTINEL", "FILESENTINEL", "old letter"):
        assert sentinel not in dump, sentinel
    out = _out(_finished(frames, "changes_list")[0])
    assert "letter.txt" in out and "custom_base_url" in out, out


def test_a_running_local_only_turn_covers_its_rows_before_its_window_is_saved(tmp_path):
    """A Local only chat's turn that is still running has no AgentRun window
    yet (it is written at the turn's end); its durable turn record covers it."""
    from iron_jarvis.core.models import ChatTurnRecord

    p = build_platform(str(tmp_path / "home"))
    tid = _thread(p, tid="chat_live", setup={"local_only": True})
    t0 = utcnow() - timedelta(seconds=30)
    with session_scope(p.engine) as db:
        db.add(ChatTurnRecord(id="turn_live", thread_id=tid, status="running", started_at=t0, updated_at=t0))
        db.add(ChatTurnRecord(id="turn_old", thread_id="chat_cloud", status="done",
                              started_at=t0 - timedelta(hours=1), updated_at=t0 - timedelta(minutes=50)))
        db.commit()
        rows = [
            ToolInvocation(id="in_live", session_id="chat", agent_run_id="chat", tool="write_file",
                           created_at=utcnow() - timedelta(seconds=5)),
            ToolInvocation(id="before", session_id="chat", agent_run_id="chat", tool="write_file",
                           created_at=t0 - timedelta(minutes=10)),
        ]
        assert H.local_only_rows(db, p.engine, rows) == {"in_live"}


@pytest.mark.asyncio
async def test_undo_of_an_old_on_card_never_turns_off_a_chat_an_app_held_since(tmp_path):
    """Turned on from chat, then Microsoft 365 held the chat: the Undo on the
    old "on" card puts nothing back that the hold forbids, and says so."""
    from fastapi.testclient import TestClient

    app = _app(tmp_path, [(LOCAL, {"on": True})])
    p = app.state.platform
    tid = _thread(p)
    frames = await _drive_stream(app, _body("keep this chat on my PC", thread_id=tid), _never)
    (card,) = _cards(frames, H.LOCAL_ONLY_CARD_KEY)
    assert egress.hold_local_only(p.engine, tid, "microsoft_365") is True
    client = TestClient(app)
    action = client.get(f"/config/changes/{card['change_id']}").json()["action_id"]
    r = client.post(f"/undo/{action}")
    assert r.status_code == 200, r.text
    assert "stays Local only" in r.json()["output"], r.json()
    setup = _setup(p, tid)
    assert setup.get("local_only") is True and setup.get("local_only_holds") == ["microsoft_365"]


@pytest.mark.asyncio
async def test_a_denied_undo_card_never_lends_its_change_to_a_later_card(tmp_path):
    """Card 1 names notes.txt and is DENIED; a newer file change lands; card 2
    names that newer change and is allowed: exactly it is undone."""
    app = _app(tmp_path, [])
    p = app.state.platform
    up = _uploads(p)
    (up / "notes.txt").write_text("first draft\n", encoding="utf-8")
    p.router.stream = _calls_stream([("write_file", {"path": "notes.txt", "content": "second draft\n"})])
    await _drive_stream(app, {**_body("rewrite notes.txt"), "tools": ["write_file"]}, _never)
    p.router.stream = _calls_stream([(UNDO, {"kind": "file"}), (UNDO, {"kind": "file"})])
    seen: list = []

    async def deny_then_allow(data):
        seen.append(data)
        if len(seen) == 1:
            # A newer file change lands (another window) before the model asks again.
            (up / "fresh.txt").write_text("fresh\n", encoding="utf-8")
            with session_scope(p.engine) as db:
                db.add(ToolInvocation(id="tool_fresh", session_id="chat", agent_run_id="chat", tool="write_file",
                                      ok=True, output="wrote fresh.txt", reversibility="reversible",
                                      created_at=utcnow() + timedelta(seconds=5)))
                db.add(UndoJournal(action_id="tool_fresh", session_id="chat", tool="write_file",
                                   kind="file_delete", reversible=True,
                                   pre_inline=json.dumps({"path": "fresh.txt", "mode": "text",
                                                          "workspace": str(up)})))
                db.commit()
            decision = "deny"
        else:
            decision = "once"
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": decision})
        assert status == 200

    await _drive_stream(app, _body("undo the last file edit"), deny_then_allow)
    assert "notes.txt" in seen[0]["why"] and "fresh.txt" in seen[1]["why"], [s["why"] for s in seen]
    assert (up / "notes.txt").read_text(encoding="utf-8") == "second draft\n", "the denied card's change stays"
    with session_scope(p.engine) as db:
        assert db.get(ToolInvocation, "tool_fresh").undone_at is not None, "the allowed card's change is undone"


@pytest.mark.asyncio
async def test_a_card_whose_change_was_undone_on_a_page_never_falls_back_to_an_older_one(tmp_path):
    """Card 1 names notes.txt (DENIED); card 2 names a newer change, which the
    user then undoes on a page before answering: Allow on card 2 refuses in
    words — it never undoes notes.txt (the newest change by then)."""
    app = _app(tmp_path, [])
    p = app.state.platform
    up = _uploads(p)
    (up / "notes.txt").write_text("first draft\n", encoding="utf-8")
    p.router.stream = _calls_stream([("write_file", {"path": "notes.txt", "content": "second draft\n"})])
    await _drive_stream(app, {**_body("rewrite notes.txt"), "tools": ["write_file"]}, _never)
    p.router.stream = _calls_stream([(UNDO, {"kind": "file"}), (UNDO, {"kind": "file"})])
    seen: list = []

    async def deny_then_undo_on_page_then_allow(data):
        seen.append(data)
        if len(seen) == 1:
            (up / "fresh.txt").write_text("fresh\n", encoding="utf-8")
            with session_scope(p.engine) as db:
                db.add(ToolInvocation(id="tool_fresh", session_id="chat", agent_run_id="chat", tool="write_file",
                                      ok=True, output="wrote fresh.txt", reversibility="reversible",
                                      created_at=utcnow() + timedelta(seconds=5)))
                db.add(UndoJournal(action_id="tool_fresh", session_id="chat", tool="write_file",
                                   kind="file_delete", reversible=True,
                                   pre_inline=json.dumps({"path": "fresh.txt", "mode": "text",
                                                          "workspace": str(up)})))
                db.commit()
            decision = "deny"
        else:
            status, _ = await _asgi_post(app, "/undo/tool_fresh", {})  # the user, on a page
            assert status == 200
            decision = "once"
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": decision})
        assert status == 200

    frames = await _drive_stream(app, _body("undo the last file edit"), deny_then_undo_on_page_then_allow)
    assert "fresh.txt" in seen[1]["why"]
    assert (up / "notes.txt").read_text(encoding="utf-8") == "second draft\n", "never the older change"
    (fin,) = [f for f in _finished(frames, UNDO) if f.get("ok") is not None][-1:]
    assert "That change was already undone" in _out(fin), _out(fin)
