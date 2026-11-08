"""v1.435.0 (chat-first plan, Wave 3 group I): Goals from Chat, Local only and
the change history, from chat (the owner's decisions D3 and D4).

Every behaviour below is driven through the REAL stream lane (``/chat/
stream``), the real permission gate (a card is answered through ``/chat/
approvals``) and the real Undo (``POST /undo/{id}``, the receipts' door) —
never a stub of the registry. The arming table runs the very function both
the stream lane (``chat_turn.settings_ask_tools``) and ``record_tools_for``
call, both ways: what must arm, and what must arm NO writer.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.ids import utcnow
from iron_jarvis.core.models import ChatThreadRecord, ToolInvocation, UndoJournal
from iron_jarvis.core.trust import LOW_TRUST_DENY
from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers import egress
from iron_jarvis.settings import records as R
from iron_jarvis.settings import records_history as H
from iron_jarvis.settings import schema
from iron_jarvis.tools.base import ToolContext
from tests.test_chat_approvals_v1187 import _asgi_post, _drive_stream
from tests.test_settings_chat_tools_v1321 import _body, _calls_stream, _done, _undo_card

SECRET = "sk-test-NEVER-IN-THE-HISTORY-0123456789"


async def _never(data):  # pragma: no cover - a call that must not card
    raise AssertionError(f"this call must not card: {data}")


def _approver(app, seen, decision="once"):
    async def approve(data):
        seen.append(data)
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": decision})
        assert status == 200

    return approve


def _app(tmp_path, specs):
    app = create_app(str(tmp_path / "home"))
    app.state.platform.router.stream = _calls_stream(specs)
    return app


def _finished(frames, tool):
    return [
        d for ev, d in frames
        if ev == "tool_call" and d.get("status") == "finished" and d.get("tool", d.get("name")) == tool
    ]


def _cards(frames, key=None):
    cards = _done(frames).get("config_cards") or []
    return [c for c in cards if key is None or c.get("key") == key]


def _out(fin):
    return fin.get("output") or fin.get("error") or ""


def _ctx(p, tmp_path, **kw):
    return ToolContext(
        workspace=tmp_path, session_id="chat", agent_run_id="chat",
        config=p.config, event_bus=p.event_bus, engine=p.engine, **kw,
    )


# ===========================================================================
# THE ARMING TABLE (both ways)
# ===========================================================================

LOCAL = "chat_local_only"
LIST = "changes_list"
UNDO = "change_undo"
GU, GD, GL = "goal_update", "goal_delete", "goal_list"
MINE_WRITERS = frozenset({LOCAL, UNDO, GU, GD})

#: request -> what MUST be offered.
REQUESTS: list[tuple[str, tuple[str, ...]]] = [
    ("stop the bookkeeping goal", (GL, GU, GD)),
    ("pause my inbox goal", (GL, GU, GD)),
    ("resume the bookkeeping goal", (GL, GU, GD)),
    ("delete the month-end close goal", (GL, GU, GD)),
    ("drop the docs goal", (GL, GU, GD)),
    ("please cancel the Johnson audit goal", (GL, GU, GD)),
    ("turn off the bookkeeping goal", (GL, GU, GD)),
    ("set the bookkeeping goal to paused", (GL, GU, GD)),
    ("put the payroll goal on hold", (GL, GU, GD)),
    ("restart the bookkeeping goal", (GL, GU, GD)),
    ("what goals do I have", (GL,)),
    ("show me my goals from chat", (GL,)),
    ("keep this chat on my PC", (LOCAL,)),
    ("keep this conversation on my computer", (LOCAL,)),
    ("make this chat local only", (LOCAL,)),
    ("keep this chat local", (LOCAL,)),
    ("turn on local only", (LOCAL,)),
    ("turn local only on for this chat", (LOCAL,)),
    ("turn off local only", (LOCAL,)),
    ("disable local only", (LOCAL,)),
    ("local only for this chat please", (LOCAL,)),
    ("don't send this chat to the cloud", (LOCAL,)),
    ("this conversation should stay on my PC", (LOCAL,)),
    ("let this chat use the cloud again", (LOCAL,)),
    ("stop keeping this chat local", (LOCAL,)),
    ("take this chat off local only", (LOCAL,)),
    ("what did you change today?", (LIST,)),
    ("what have you changed this week?", (LIST,)),
    ("what changes did you make to my settings?", (LIST,)),
    ("show me the changes you made", (LIST,)),
    ("list your recent changes", (LIST,)),
    ("what did Jarvis change yesterday?", (LIST,)),
    ("show me the change history", (LIST,)),
    ("undo the last file edit", (UNDO,)),
    ("undo your last change", (UNDO,)),
    ("revert the last change", (UNDO,)),
    ("roll back my latest change", (UNDO,)),
    ("undo that file edit", (UNDO,)),
    ("undo the change you made to notes.txt", (UNDO,)),
    ("revert what you just did", (UNDO,)),
    ("undo that", (UNDO, "config_undo")),
    ("Show me what you changed today and undo the last file edit.", (LIST, UNDO)),
    ("can you undo the last file edit?", (UNDO,)),
]

#: everyday sentences: NONE of this module's writers (and no reader where the
#: words are not about the app).
NEAR_MISSES: list[str] = [
    "my goal this year is to grow revenue",
    "our goal is to stop smoking by June",
    "help me stop procrastinating on my goals",
    "the goal of this memo is to delete duplicates",
    "what is a good savings goal for a family of four?",
    "set a goal-setting meeting with the partners",
    "keep it local to the office",
    "only buy from local suppliers",
    "is there a local only shop near me?",
    "keep this short",
    "keep this on my desk until Friday",
    "my local bank changed its hours",
    "turn on the lights in the office",
    "what changed in the tax law this year?",
    "what has changed since 2019 in the IRS rules?",
    "list the changes in this contract",
    "show me the changes to the draft in the document",
    "undo the formatting in my letter",
    "undo the damage the storm did",
    "what should I do about the undoing of this contract?",
    "revert the contract to the original wording",
    "how do I undo a change in Excel?",
    "How do I keep this chat on my PC only?",
    "is this chat local only?",
    "how does local only work?",
    "start new chats local only",
    "turn on local only for new chats",
    "make every chat local only by default",
    "the client wants to change the goal posts",
    "local news only please",
    "summarize the K-1 I attached",
    "what did the client change in the spreadsheet?",
]


@pytest.fixture(scope="module")
def lane(tmp_path_factory):
    home = tmp_path_factory.mktemp("history-arming") / "home"
    return SimpleNamespace(platform=create_app(str(home)).state.platform)


def _offered(lane, text):
    return set(CT.settings_ask_tools(lane, text, [], []))


def test_the_requests_arm_what_they_ask_for(lane):
    assert len(REQUESTS) >= 30
    bad = [(t, sorted(set(want) - _offered(lane, t))) for t, want in REQUESTS if set(want) - _offered(lane, t)]
    assert bad == []


def test_no_near_miss_arms_a_writer_of_this_module(lane):
    assert len(NEAR_MISSES) >= 30
    bad = [(t, sorted(_offered(lane, t) & MINE_WRITERS)) for t in NEAR_MISSES if _offered(lane, t) & MINE_WRITERS]
    assert bad == []


def test_a_near_miss_about_the_world_arms_no_reader_either(lane):
    for text in (
        "what changed in the tax law this year?",
        "list the changes in this contract",
        "show me the changes to the draft in the document",
        "what did the client change in the spreadsheet?",
        "keep it local to the office",
    ):
        assert not (_offered(lane, text) & {LIST, LOCAL, UNDO}), text


def test_a_request_offers_only_its_own_family(lane):
    """A Local only sentence offers no undo, an undo sentence no Local only."""
    assert UNDO not in _offered(lane, "keep this chat on my PC")
    assert LOCAL not in _offered(lane, "undo the last file edit")
    assert {GU, GD}.isdisjoint(_offered(lane, "what did you change today?"))


def test_the_new_chat_default_is_the_settings_tools_not_this_chat(lane):
    from iron_jarvis.settings.trigger import settings_ask

    for text in ("start new chats local only", "turn on local only for new chats", "keep new chats on this pc"):
        assert "chat_new_local_only" in settings_ask(text).keys, text
        assert LOCAL not in _offered(lane, text), text


def test_the_matchers_are_linear_on_pathological_input():
    """Every pattern here runs on the event loop: pathological input must
    finish fast (in a child, with a deadline — a quadratic or exponential
    pattern would hang the child, never this worker)."""
    code = (
        "import time\n"
        "from iron_jarvis.settings import records_history as H, records as R\n"
        "bad = ['stop ' + 'my ' * 20000 + 'x', 'undo ' * 20000, 'keep this chat ' * 8000 + '!',\n"
        "       'what did you ' * 10000, 'show me ' + 'the ' * 20000 + 'changes', 'goal ' + 'a ' * 30000,\n"
        "       'pause ' + 'big ' * 30000 + 'thing', 'turn on ' + 'local ' * 20000, ' ' * 50000 + 'goal',\n"
        "       'local-' * 20000 + 'only', 'don\\'t send ' + 'this chat ' * 8000,\n"
        "       'stop' + ' ' * 50000 + 'x', 'keep this chat' + ' ' * 50000 + 'x', 'undo' + ' ' * 50000 + 'x',\n"
        "       'show me' + ' ' * 50000 + 'x', 'what did you' + ' ' * 50000 + 'x',\n"
        "       'goal stop' + ' ' * 50000 + 'x', 'goal' + ' ' * 50000 + 'x to paused',\n"
        "       'this chat undo keep this chat' + ' ' * 50000 + 'x']\n"
        "t = time.perf_counter()\n"
        "for s in bad:\n"
        "    H.history_tools_for(s); R.record_tools_for(s)\n"
        "print(round(time.perf_counter() - t, 3))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert float(out.stdout.strip()) < 15.0, out.stdout


# ===========================================================================
# REGISTRATION
# ===========================================================================


def test_every_new_tool_is_registered_with_its_tier_and_kept_from_low_trust(tmp_path):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    perms = p.permissions
    tiers = {**H.HISTORY_TOOL_TIERS, LOCAL: R.RECORD_TOOL_TIERS[LOCAL]}
    assert tiers == {"changes_list": "allow", "change_undo": "ask", LOCAL: "allow"}
    for name, tier in tiers.items():
        assert p.registry.get(name) is not None, name
        assert perms.authorize(name, {}).allowed is (tier == "allow"), name
    # An undo, or Local only OFF, never from a low-trust run.
    assert {UNDO, LOCAL} <= LOW_TRUST_DENY
    assert LIST not in LOW_TRUST_DENY, "reading the history changes nothing"
    # chat_local_only rides the record cards (the ledger and config_undo see it).
    assert LOCAL in R.RECORD_CARD_TOOLS and LOCAL in R.ledger_tools()
    assert not ({LIST, UNDO} & R.ledger_tools()), "the history tools are not themselves configuration changes"


def test_the_new_chat_default_is_a_schema_row_whose_off_is_ask_floor():
    row = schema.get("chat_new_local_only")
    assert row.type == "bool" and row.store == "config"
    assert row.tier_for(True) == "allow"
    assert row.tier_for(False) == "ask-floor", "turning it off widens what new chats may send"
    assert "chat_new_local_only" in schema.daemon_keys()


def test_get_settings_carries_the_new_chat_default_for_the_page(tmp_path):
    app = create_app(str(tmp_path / "home"))
    client = TestClient(app)
    assert client.get("/settings").json()["settings"]["chat_new_local_only"] is False
    r = client.put("/settings", json={"values": {"chat_new_local_only": True}})
    assert r.status_code == 200, r.text
    assert client.get("/settings").json()["settings"]["chat_new_local_only"] is True


@pytest.mark.asyncio
async def test_start_new_chats_local_only_from_chat_and_off_always_asks(tmp_path):
    """The settings tools change the new-chat default: on applies at once
    (allow), off is the protected tool, carded even in Don't ask."""
    app = _app(tmp_path, [("config_set", {"key": "chat_new_local_only", "value": True})])
    p = app.state.platform
    await _drive_stream(app, _body("start new chats local only"), _never)
    assert p.config.chat_new_local_only is True
    p.router.stream = _calls_stream([("config_change_protected", {"key": "chat_new_local_only", "value": False})])
    seen: list = []
    await _drive_stream(app, _body("stop starting new chats local only", mode="yolo"), _approver(app, seen))
    assert [s["tool"] for s in seen] == ["config_change_protected"]
    assert p.config.chat_new_local_only is False


# ===========================================================================
# GOALS FROM CHAT
# ===========================================================================


def _contract(p, name="Bookkeeping catch-up", schedule="0 9 * * *"):
    return p.goal_engine.store.create(
        name=name, contract_text=f"{name}: post every bank line", budget={"max_tokens": 5000}, schedule=schedule
    )


def _contract_row(p, gid):
    from iron_jarvis.goals.models import GoalContractRecord

    with session_scope(p.engine) as db:
        rec = db.get(GoalContractRecord, gid)
        return rec.model_dump() if rec is not None else None


def _but_stamp(row):
    """A goal row without ``updated_at`` (moved on purpose in the review: an
    undo of a pause or a stop puts back the STATE and writes a fresh stamp —
    it never rewinds the books, tests/test_chat_history_review_v14350.py)."""
    return {k: v for k, v in (row or {}).items() if k != "updated_at"}


@pytest.mark.asyncio
async def test_goal_list_shows_goals_from_chat_marked_as_such(tmp_path):
    app = _app(tmp_path, [(GL, {})])
    p = app.state.platform
    p.intent.add_goal("keep my inbox under 20 unread")
    g = _contract(p)
    frames = await _drive_stream(app, _body("what goals do I have"), _never)
    (fin,) = _finished(frames, GL)
    out = _out(fin)
    assert "keep my inbox under 20 unread" in out
    assert "Goals from chat" in out and "[goal from chat, running] Bookkeeping catch-up" in out
    assert g.id in out


@pytest.mark.asyncio
async def test_goal_list_is_unchanged_without_goals_from_chat(tmp_path):
    from iron_jarvis.motivation.tools import GoalListTool

    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    p.intent.add_goal("ship the v2 docs")
    res = await GoalListTool(p).execute({}, _ctx(p, tmp_path))
    assert set(res.data) == {"goals", "count"} and "Goals from chat" not in res.output


@pytest.mark.asyncio
async def test_pausing_a_goal_from_chat_asks_applies_and_undo_restores_the_whole_row(tmp_path):
    app = _app(tmp_path, [])
    p = app.state.platform
    g = _contract(p)
    before = _contract_row(p, g.id)
    p.router.stream = _calls_stream([(GU, {"goal": g.id, "status": "paused"})])
    seen: list = []
    frames = await _drive_stream(app, _body("pause the bookkeeping goal"), _approver(app, seen))
    assert [s["tool"] for s in seen] == [GU], "a goal change asks first"
    assert _contract_row(p, g.id)["state"] == "paused"
    assert p.scheduler.get(f"goal:{g.id}").enabled is False, "the schedule follows the state"
    (card,) = [c for c in _cards(frames) if c.get("title") == "Goal changed"]
    assert card["old"].startswith("running") and card["new"].startswith("paused")
    await _undo_card(app, card)
    assert _but_stamp(_contract_row(p, g.id)) == _but_stamp(before)
    assert p.scheduler.get(f"goal:{g.id}").enabled is True


@pytest.mark.asyncio
async def test_resuming_and_reopening_go_through_the_engines_doors(tmp_path):
    app = _app(tmp_path, [])
    p = app.state.platform
    g = _contract(p)
    p.goal_engine.store.transition(g.id, "paused")
    p.router.stream = _calls_stream([(GU, {"goal": g.id, "status": "active"})])
    await _drive_stream(app, _body("resume the bookkeeping goal"), _approver(app, []))
    assert _contract_row(p, g.id)["state"] == "active"
    p.goal_engine.store.transition(g.id, "stopped")
    p.router.stream = _calls_stream([(GU, {"goal": g.id, "status": "active"})])
    frames = await _drive_stream(app, _body("restart the bookkeeping goal"), _approver(app, []))
    assert _contract_row(p, g.id)["state"] == "active"
    assert "Reopened" in _out(_finished(frames, GU)[0])


@pytest.mark.asyncio
async def test_stopping_a_goal_from_chat_says_a_stopped_run_is_not_started_again(tmp_path):
    app = _app(tmp_path, [])
    p = app.state.platform
    g = _contract(p)
    p.goal_engine.store.set_checkpoint(g.id, {"running_session_id": "sess_running"})
    cancelled: list[str] = []
    p.goal_engine._orch = SimpleNamespace(cancel_session=lambda sid: cancelled.append(sid))
    before = _contract_row(p, g.id)
    p.router.stream = _calls_stream([(GU, {"goal": "Bookkeeping catch-up", "status": "stopped"})])
    frames = await _drive_stream(app, _body("stop the bookkeeping goal"), _approver(app, []))
    (fin,) = _finished(frames, GU)
    assert _contract_row(p, g.id)["state"] == "stopped" and cancelled == ["sess_running"]
    assert "does not start that run again" in _out(fin)
    (card,) = [c for c in _cards(frames) if c.get("title") == "Goal changed"]
    look = TestClient(app).get(f"/config/changes/{card['change_id']}").json()
    r = TestClient(app).post(f"/undo/{look['action_id']}")
    assert r.status_code == 200, r.text
    assert "not started again" in r.json()["output"]
    assert _but_stamp(_contract_row(p, g.id)) == _but_stamp(before)


@pytest.mark.asyncio
async def test_deleting_a_goal_from_chat_and_undo_brings_back_row_schedule_and_grants(tmp_path):
    app = _app(tmp_path, [])
    p = app.state.platform
    g = _contract(p)
    grant = p.grants.create("goal", g.id, "write_file", "", "write_file (any arguments)", 30)
    before = _contract_row(p, g.id)
    p.router.stream = _calls_stream([(GD, {"goal": g.id})])
    frames = await _drive_stream(app, _body("delete the bookkeeping goal"), _approver(app, []))
    (fin,) = _finished(frames, GD)
    assert "Deleted the goal from chat" in _out(fin) and "1 always-allow" in _out(fin)
    assert _contract_row(p, g.id) is None and p.scheduler.get(f"goal:{g.id}") is None
    assert p.grants.get(grant.id).revoked_at is not None
    (card,) = [c for c in _cards(frames) if c.get("title") == "Goal removed"]
    await _undo_card(app, card)
    assert _contract_row(p, g.id) == before
    assert p.scheduler.get(f"goal:{g.id}") is not None
    assert p.grants.get(grant.id).revoked_at is None, "the always-allow the delete took is given back"


@pytest.mark.asyncio
async def test_a_goal_from_chat_refuses_what_chat_does_not_change(tmp_path):
    app = _app(tmp_path, [])
    p = app.state.platform
    g = _contract(p)
    for args, words in (
        ({"goal": g.id, "text": "new words"}, "can be paused, resumed, stopped or deleted"),
        ({"goal": g.id, "status": "done"}, "marked done by its own check"),
    ):
        p.router.stream = _calls_stream([(GU, args)])
        frames = await _drive_stream(app, _body("change the bookkeeping goal"), _approver(app, []))
        (fin,) = _finished(frames, GU)
        assert fin["ok"] is False and words in _out(fin)
    assert _contract_row(p, g.id)["state"] == "active"


@pytest.mark.asyncio
async def test_two_goals_with_the_same_words_are_named_by_id(tmp_path):
    app = _app(tmp_path, [])
    p = app.state.platform
    p.intent.add_goal("Bookkeeping catch-up")
    _contract(p)
    p.router.stream = _calls_stream([(GU, {"goal": "Bookkeeping catch-up", "status": "paused"})])
    frames = await _drive_stream(app, _body("pause the bookkeeping goal"), _approver(app, []))
    (fin,) = _finished(frames, GU)
    assert fin["ok"] is False and "2 goals match" in _out(fin)


@pytest.mark.asyncio
async def test_a_standing_goal_is_unchanged_and_stopped_means_abandoned(tmp_path):
    app = _app(tmp_path, [])
    p = app.state.platform
    sg = p.intent.add_goal("keep my inbox under 20 unread")
    p.router.stream = _calls_stream([(GU, {"goal": sg.id, "status": "stopped"})])
    await _drive_stream(app, _body("stop my inbox goal"), _approver(app, []))
    assert p.intent.get_goal(sg.id).status == "abandoned"


def test_the_goal_routes_still_answer_with_the_lifted_doors(tmp_path):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    client = TestClient(app)
    g = _contract(p)
    grant = p.grants.create("goal", g.id, "write_file", "", "write_file (any arguments)", 30)
    assert client.post(f"/goals/{g.id}/pause").json()["goal"]["state"] == "paused"
    assert client.post(f"/goals/{g.id}/resume").json()["goal"]["state"] == "active"
    p.goal_engine.store.transition(g.id, "stopped")
    assert client.post(f"/goals/{g.id}/resume").status_code == 409
    r = client.delete(f"/goals/{g.id}")
    assert r.json() == {"deleted": g.id, "revoked_grants": [grant.id]}
    assert client.delete(f"/goals/{g.id}").status_code == 404


# ===========================================================================
# LOCAL ONLY FOR THIS CHAT (D3)
# ===========================================================================


def _thread(p, tid="chat_lo", setup=None):
    with session_scope(p.engine) as db:
        db.add(ChatThreadRecord(id=tid, messages_json="[]", setup_json=json.dumps(setup or {})))
        db.commit()
    return tid


def _setup(p, tid):
    with session_scope(p.engine) as db:
        return json.loads(db.get(ChatThreadRecord, tid).setup_json or "{}")


@pytest.mark.asyncio
async def test_turning_local_only_on_needs_no_card_and_says_it_starts_next_message(tmp_path):
    app = _app(tmp_path, [(LOCAL, {"on": True})])
    p = app.state.platform
    tid = _thread(p)
    stamp_before = None
    with session_scope(p.engine) as db:
        stamp_before = db.get(ChatThreadRecord, tid).updated_at
    frames = await _drive_stream(app, _body("keep this chat on my PC", thread_id=tid), _never)
    (fin,) = _finished(frames, LOCAL)
    assert fin["ok"] is True
    assert "next message" in _out(fin) and "finishes the way it started" in _out(fin)
    assert _setup(p, tid).get("local_only") is True
    with session_scope(p.engine) as db:
        assert db.get(ChatThreadRecord, tid).updated_at == stamp_before, "the page's save check must not trip"
    (card,) = _cards(frames, H.LOCAL_ONLY_CARD_KEY)
    assert (card["old"], card["new"]) == ("off", "on")
    # The NEXT turn of this chat really is Local only (the daemon reads the
    # saved setup even when the request leaves the flag out).
    scope = await CT.local_only_scope_for(p, SimpleNamespace(local_only=False, thread_id=tid, allow_cloud_once=""))
    assert scope.on is True
    await _undo_card(app, card)
    assert "local_only" not in _setup(p, tid)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["approve_for_me", "yolo", "auto"])
async def test_turning_local_only_off_always_asks_once_past_every_grant(tmp_path, mode):
    app = _app(tmp_path, [(LOCAL, {"on": False})])
    p = app.state.platform
    tid = _thread(p, setup={"local_only": True})
    seen: list = []
    body = _body("turn off local only", mode=mode, thread_id=tid, granted_tools=[LOCAL], local_only=True)
    frames = await _drive_stream(app, body, _approver(app, seen, decision="conversation"))
    assert [s["tool"] for s in seen] == [LOCAL], f"turning Local only off must card in {mode}"
    assert seen[0].get("once_only") is True and seen[0].get("can_always") is False
    assert "may go to a model off this PC" in seen[0].get("why", "")
    (fin,) = _finished(frames, LOCAL)
    assert fin["ok"] is True and "local_only" not in _setup(p, tid)
    assert "stays Local only until it ends" in _out(fin), "this reply keeps the scope it started with"


@pytest.mark.asyncio
async def test_a_denied_off_card_leaves_the_chat_local_only(tmp_path):
    app = _app(tmp_path, [(LOCAL, {"on": False})])
    p = app.state.platform
    tid = _thread(p, setup={"local_only": True})
    await _drive_stream(app, _body("turn off local only", thread_id=tid, local_only=True), _approver(app, [], decision="deny"))
    assert _setup(p, tid).get("local_only") is True


@pytest.mark.asyncio
async def test_a_chat_held_by_an_app_is_never_turned_off(tmp_path):
    app = _app(tmp_path, [(LOCAL, {"on": False})])
    p = app.state.platform
    tid = _thread(p, setup={"local_only": True, "local_only_holds": ["microsoft_365"]})
    frames = await _drive_stream(app, _body("turn off local only", thread_id=tid, local_only=True), _never)
    (fin,) = _finished(frames, LOCAL)
    assert fin["ok"] is False and "Microsoft 365" in _out(fin) and "cannot be turned off" in _out(fin)
    assert _setup(p, tid)["local_only"] is True and _setup(p, tid)["local_only_holds"] == ["microsoft_365"]
    assert not _cards(frames, H.LOCAL_ONLY_CARD_KEY)


@pytest.mark.asyncio
async def test_nothing_to_change_is_said_never_claimed(tmp_path):
    app = _app(tmp_path, [(LOCAL, {"on": True})])
    p = app.state.platform
    tid = _thread(p, setup={"local_only": True})
    frames = await _drive_stream(app, _body("keep this chat on my PC", thread_id=tid, local_only=True), _never)
    (fin,) = _finished(frames, LOCAL)
    assert fin["ok"] is False and "already Local only" in _out(fin)
    p.router.stream = _calls_stream([(LOCAL, {"on": False})])
    tid2 = _thread(p, tid="chat_plain")
    frames = await _drive_stream(app, _body("turn off local only", thread_id=tid2), _never)
    (fin,) = _finished(frames, LOCAL)
    assert fin["ok"] is False and "not Local only" in _out(fin)


@pytest.mark.asyncio
async def test_a_new_chats_first_turn_applies_when_the_page_links_it(tmp_path):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    tool = p.registry.get(LOCAL)
    ctx = _ctx(p, tmp_path, turn_id="turn_new_1")
    res = await tool.execute({"on": True}, ctx)
    assert res.ok, res.error
    tid = _thread(p, tid="chat_new")
    r = TestClient(app).post("/chat/turns/turn_new_1/thread", json={"thread_id": tid})
    assert r.status_code == 200, r.text
    assert _setup(p, tid).get("local_only") is True
    # Already linked: a later call writes the chat at once — OFF only after
    # its own check showed the card (the lane shows it; here it is called).
    off_ctx = _ctx(p, tmp_path, turn_id="turn_new_1")
    res = await tool.execute({"on": False}, off_ctx)
    assert res.ok is False and "needs the user's OK on a card" in (res.error or "")
    assert _setup(p, tid).get("local_only") is True, "off without its card changes nothing"
    check = await tool.spend_check({"on": False}, off_ctx)
    assert check is not None and check.ask is True and check.once_only is True
    res = await tool.execute({"on": False}, off_ctx)
    assert res.ok and "local_only" not in _setup(p, tid)


@pytest.mark.asyncio
async def test_config_undo_never_turns_local_only_off_uncarded(tmp_path):
    """"Undo that" through config_undo (ask tier: Don't ask runs it with no
    card) refuses a Local only change; change_undo cards it."""
    app = _app(tmp_path, [(LOCAL, {"on": True})])
    p = app.state.platform
    tid = _thread(p)
    await _drive_stream(app, _body("keep this chat on my PC", thread_id=tid), _never)
    assert _setup(p, tid).get("local_only") is True
    p.router.stream = _calls_stream([("config_undo", {})])
    frames = await _drive_stream(app, _body("undo that", mode="yolo", thread_id=tid, local_only=True), _never)
    (fin,) = _finished(frames, "config_undo")
    assert fin["ok"] is False and "change_undo" in _out(fin)
    assert _setup(p, tid).get("local_only") is True
    p.router.stream = _calls_stream([(UNDO, {})])
    seen: list = []
    await _drive_stream(app, _body("undo that", mode="yolo", thread_id=tid, local_only=True), _approver(app, seen))
    assert [s["tool"] for s in seen] == [UNDO] and "Local only" in seen[0]["why"]
    assert "local_only" not in _setup(p, tid)


@pytest.mark.asyncio
async def test_a_new_chats_held_first_turn_is_never_turned_off(tmp_path):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    egress.note_pending_hold("turn_held_1", "microsoft_365")
    try:
        ctx = _ctx(p, tmp_path, turn_id="turn_held_1")
        res = await p.registry.get(LOCAL).execute({"on": False}, ctx)
        assert res.ok is False and "Microsoft 365" in (res.error or "")
    finally:
        egress.take_pending_holds("turn_held_1")


# ===========================================================================
# THE CHANGE HISTORY AND ITS UNDO (D4)
# ===========================================================================


def _uploads(p) -> Path:
    d = Path(p.config.home) / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


async def _three_changes(app, tmp_path):
    """A file edit in chat, a schedule change and a key saved on its card."""
    p = app.state.platform
    (_uploads(p) / "notes.txt").write_text("first draft\n", encoding="utf-8")
    p.scheduler.add_task("nightly", "0 2 * * *", kind="workflow", payload={"workflow": "brief"})
    p.router.stream = _calls_stream([("write_file", {"path": "notes.txt", "content": "second draft\n"})])
    await _drive_stream(app, {**_body("rewrite notes.txt"), "tools": ["write_file"]}, _never)
    p.router.stream = _calls_stream([("schedule_update", {"name": "nightly", "cron": "0 7 * * *"})])
    await _drive_stream(app, _body("move my nightly schedule to 7am"), _approver(app, []))
    # v1.449.0 (item 25, moved on purpose): the card tests a provider key
    # before storing it — a stand-in that accepts, never the live probe.
    p.connections._prober = lambda provider, cred: (True, f"{provider} reachable — credential accepted")
    status, _ = await _asgi_post(app, "/config/secret", {"name": "connection.openai", "value": SECRET})
    assert status == 200


@pytest.mark.asyncio
async def test_changes_list_names_what_changed_and_never_a_value(tmp_path):
    app = _app(tmp_path, [])
    p = app.state.platform
    await _three_changes(app, tmp_path)
    p.router.stream = _calls_stream([(LIST, {})])
    frames = await _drive_stream(app, _body("what did you change today?"), _never)
    (fin,) = _finished(frames, LIST)
    out = _out(fin)
    assert "notes.txt" in out and "the schedule “nightly”" in out and "openai" in out.lower()
    assert "in chat" in out and "can be undone" in out
    assert "Commands Iron Jarvis ran" in out, "what has no Undo is said, not hidden"
    assert SECRET not in json.dumps(frames) and "second draft" not in out and "first draft" not in out
    # The tool's own data, read straight: kinds and states, never a value.
    res = await p.registry.get(LIST).execute({"kind": "file"}, _ctx(p, tmp_path))
    (row,) = res.data["changes"]
    assert row["kind"] == "file" and row["state"] == "can" and row["where"] == "in chat"
    assert row["what"].endswith("notes.txt")
    keys = await p.registry.get(LIST).execute({"kind": "setting"}, _ctx(p, tmp_path))
    assert keys.data["changes"] and SECRET not in json.dumps(keys.data)


@pytest.mark.asyncio
async def test_change_undo_always_cards_naming_the_change_then_runs_the_receipts_undo(tmp_path):
    app = _app(tmp_path, [])
    p = app.state.platform
    await _three_changes(app, tmp_path)
    # Newest FILE edit (a key and a schedule are newer, of other kinds).
    p.router.stream = _calls_stream([(UNDO, {"kind": "file"})])
    seen: list = []
    frames = await _drive_stream(app, _body("undo the last file edit", mode="yolo"), _approver(app, seen, "conversation"))
    assert [s["tool"] for s in seen] == [UNDO], "an undo cards even in Don't ask"
    assert seen[0]["once_only"] is True and seen[0]["can_always"] is False
    assert "Undo this change" in seen[0]["why"] and "notes.txt" in seen[0]["why"]
    (fin,) = _finished(frames, UNDO)
    assert fin["ok"] is True and "notes.txt" in _out(fin)
    assert (_uploads(p) / "notes.txt").read_text(encoding="utf-8") == "first draft\n"
    # The receipts' Undo marked it: a second undo of it is refused in words.
    with session_scope(p.engine) as db:
        inv = db.exec(select(ToolInvocation).where(ToolInvocation.tool == "write_file")).first()
        assert inv.undone_at is not None
        undo_rows = list(db.exec(select(ToolInvocation).where(ToolInvocation.undo_of == inv.id)))
    assert len(undo_rows) == 1
    p.router.stream = _calls_stream([(UNDO, {"action_id": inv.id})])
    frames = await _drive_stream(app, _body("undo the last file edit"), _never)
    (fin,) = _finished(frames, UNDO)
    assert fin["ok"] is False and "already undone" in _out(fin)


@pytest.mark.asyncio
async def test_a_grant_from_an_earlier_card_never_covers_the_next_undo(tmp_path):
    app = _app(tmp_path, [])
    p = app.state.platform
    await _three_changes(app, tmp_path)
    p.router.stream = _calls_stream([(UNDO, {}), (UNDO, {})])
    seen: list = []
    body = _body("undo your last change", granted_tools=[UNDO])
    await _drive_stream(app, body, _approver(app, seen, "conversation"))
    assert [s["tool"] for s in seen] == [UNDO, UNDO], "every undo asks"


@pytest.mark.asyncio
async def test_the_card_names_the_change_that_is_undone_even_if_a_newer_one_lands(tmp_path):
    app = _app(tmp_path, [])
    p = app.state.platform
    await _three_changes(app, tmp_path)
    p.router.stream = _calls_stream([(UNDO, {"kind": "file"})])

    async def newer_then_approve(data):
        # A newer FILE change lands while the card waits.
        with session_scope(p.engine) as db:
            db.add(ToolInvocation(id="tool_newer", session_id="chat", agent_run_id="chat", tool="write_file",
                                  ok=True, output="wrote other.txt", reversibility="reversible",
                                  created_at=utcnow() + timedelta(seconds=5)))
            db.add(UndoJournal(action_id="tool_newer", session_id="chat", tool="write_file", kind="file_delete",
                               reversible=True, pre_inline=json.dumps({"path": "other.txt", "mode": "text"})))
            db.commit()
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "once"})
        assert status == 200

    frames = await _drive_stream(app, _body("undo the last file edit"), newer_then_approve)
    (fin,) = _finished(frames, UNDO)
    assert fin["ok"] is True and "notes.txt" in _out(fin)
    with session_scope(p.engine) as db:
        assert db.get(ToolInvocation, "tool_newer").undone_at is None


@pytest.mark.asyncio
async def test_what_the_journal_cannot_reverse_is_refused_before_anything_runs(tmp_path):
    app = _app(tmp_path, [])
    p = app.state.platform
    with session_scope(p.engine) as db:
        db.add(ToolInvocation(id="tool_sent", session_id="chat", agent_run_id="chat", tool="notify",
                              ok=True, output="sent", reversibility="irreversible"))
        db.add(UndoJournal(action_id="tool_sent", session_id="chat", tool="notify", kind="other", reversible=True))
        db.add(ToolInvocation(id="tool_noinv", session_id="chat", agent_run_id="chat", tool="write_file",
                              ok=True, output="wrote", reversibility="reversible"))
        db.add(UndoJournal(action_id="tool_noinv", session_id="chat", tool="write_file", kind="file_restore",
                           reversible=False, pre_inline=json.dumps({"path": "a.txt", "mode": "text"})))
        db.commit()
    for aid, words in (("tool_sent", "left this PC"), ("tool_noinv", "no safe way back"), ("tool_nope", "no change")):
        p.router.stream = _calls_stream([(UNDO, {"action_id": aid})])
        frames = await _drive_stream(app, _body("undo that"), _never)
        (fin,) = _finished(frames, UNDO)
        assert fin["ok"] is False and words in _out(fin), (aid, _out(fin))


@pytest.mark.asyncio
async def test_change_undo_reverses_a_record_and_a_setting_through_the_same_undo(tmp_path):
    app = _app(tmp_path, [])
    p = app.state.platform
    await _three_changes(app, tmp_path)
    assert p.scheduler.get("nightly").cron == "0 7 * * *"
    p.router.stream = _calls_stream([(UNDO, {"kind": "record"})])
    await _drive_stream(app, _body("undo the last change"), _approver(app, []))
    assert p.scheduler.get("nightly").cron == "0 2 * * *"
    p.router.stream = _calls_stream([(UNDO, {"kind": "setting"})])
    await _drive_stream(app, _body("undo the last setting change"), _approver(app, []))
    assert not p.connections.has_credential("openai")


def test_the_history_reads_its_envelope_by_name_only():
    """A file envelope carries the prior BYTES (``data``); a setting's the
    prior VALUES; neither ever reaches the history's words."""
    secret_data = "c2VjcmV0LWJ5dGVz"
    words = H._target_words("file_restore", json.dumps({"path": "notes.txt", "data": secret_data}))
    assert words == "notes.txt"
    words = H._target_words("setting_restore", json.dumps({"prior": {"default_model": "sonnet", "theme": "x"}}))
    assert words == "settings default_model, theme" and "sonnet" not in words
    assert H._target_words("secret_restore", json.dumps({"secret": "connection.openai", "backup": "b"})) == "the key connection.openai"
