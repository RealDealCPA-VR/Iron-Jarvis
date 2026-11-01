"""v1.422.0 (Wave 2 "Never lose work", B3's fallback): a Build › Code reply a
RESTART cut off carries on BY ITSELF, once, through the coding tool's own
resume — and is offered (a Continue press) whenever it is not.

Driven through the REAL app factory, the real service, the real boot
reconcile (``service.reconcile_cut_turns``) and the real carry-on pass
(``carry_on.carry_on_after_restart``); the process half through the fake
harness (``tests/fixtures/fake_harness_v1409.py``, the real process plumbing)
so the resume is the one the tool really gets (``claude --resume <id>``,
Codex ``thread/resume``). Every process a test starts is checked BY PID to be
gone at its end.

Mirrors Chat › Code's v1.391.0 rules: never for a reply the user stopped, one
that ended, one cut while a card or a question was open (offered: the
question was lost), a session with a newer message, an archived session,
under Emergency stop (the record stays for a later start), beside an
account's limit; at most once (a carry-on that is itself cut does not loop);
sessions a surviving host still runs (``keep=``) are left alone.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import psutil
import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import carry_on as CO
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from iron_jarvis.hosted.events import make, session_dir

from ._build_app_helpers_v1409 import FIX, FAKE, client, fake_log, sent, starts, stop_all, until
from .test_build_app_history_v1418 import fake_tool

CLAUDE_HID = "6b1c2f0e-5d4a-4c3b-9a8e-7f6d5c4b3a21"
CODEX_HID = "01a189c4-9316-7d30-b03e-8a6c95d7bacd"


@pytest.fixture
def root(tmp_path) -> Path:
    return tmp_path / "root"


@pytest.fixture
def app(root):
    return create_app(str(root))


@pytest.fixture
def home(app) -> Path:
    """The app's state home (where ``hosted/<id>/`` lives)."""
    return Path(app.state.platform.config.home)


@pytest.fixture
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


def _log(home: Path, sid: str, events: list[dict]) -> Path:
    d = session_dir(home, sid)
    d.mkdir(parents=True, exist_ok=True)
    path = d / "events.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return path


def _ev(event_type: str, turn: str, **fields) -> dict:
    return {**make(event_type, **fields), "turn": turn}


def _working(turn: str = "t1") -> list[dict]:
    """A reply that was WORKING when the app went (no done: a crash)."""
    return [
        _ev("user", turn, text="build the parser"),
        _ev("text_done", turn, item="a1", text="Reading the files first."),
        _ev("tool", turn, item="x1", kind="read", name="Read", title="Read parser.py", input="", status="done"),
    ]


def _graceful(turn: str = "t1") -> list[dict]:
    """The same reply, ended by Iron Jarvis's own stop (a graceful shutdown)."""
    return _working(turn) + [
        {**S.W.error(S.STOPPED_TITLE, S.STOPPED_FIX, ""), "turn": turn},
        _ev("done", turn, stop="interrupted"),
    ]


def _row(app, work, harness="claude-code", hid=CLAUDE_HID, **extra):
    return store.create(app.state.platform.engine, harness=harness, folder=str(work), harness_session_id=hid, **extra)


def _pids(log: Path) -> list[int]:
    return [r["pid"] for r in fake_log(log) if "pid" in r]


def _gone(pids: list[int]) -> bool:
    for pid in pids:
        try:
            if psutil.Process(pid).status() != psutil.STATUS_ZOMBIE:
                return False
        except psutil.NoSuchProcess:
            continue
    return True


def _scenario_hanging_mid_reply(tmp_path: Path) -> Path:
    """Claude Code that starts answering and is still WORKING when the app
    stops (the recorded resume session, cut after its first words)."""
    steps = json.loads((FIX / "claude_resume.json").read_text(encoding="utf-8"))["steps"]
    cut = next(i for i, s in enumerate(steps)
               if isinstance(s.get("out"), dict) and s["out"].get("type") == "stream_event"
               and (s["out"].get("event") or {}).get("type") == "content_block_delta")
    path = tmp_path / "hang.json"
    path.write_text(json.dumps({"steps": steps[: cut + 1] + [{"await": "user"}]}), encoding="utf-8")
    return path


def _use(monkeypatch, scenario: Path, log: Path) -> None:
    import sys

    monkeypatch.setattr(S, "binary_for", lambda harness: [sys.executable, str(FAKE)])
    monkeypatch.setenv("IJ_FAKE_SCENARIO", str(scenario))
    monkeypatch.setenv("IJ_FAKE_LOG", str(log))


def _view(app, sid):
    async def go():
        async with client(app) as c:
            r = await c.get(f"/build/app/sessions/{sid}")
            assert r.status_code == 200, r.text
            return r.json()
    return go()


# --------------------------------------------------------------------------- #
# the real resume, through real processes
# --------------------------------------------------------------------------- #
async def test_a_reply_a_graceful_restart_cut_off_carries_on_once_through_the_tools_own_resume(
        root, work, tmp_path, monkeypatch):
    log1, log2 = tmp_path / "first.log", tmp_path / "second.log"
    _use(monkeypatch, _scenario_hanging_mid_reply(tmp_path), log1)
    app1 = create_app(str(root))
    home = Path(app1.state.platform.config.home)
    svc1 = S.service_for(app1.state.platform)
    row = store.create(app1.state.platform.engine, harness="claude-code", folder=str(work))
    await svc1.start_turn(row.id, "build the parser", "t1")
    await until(lambda: store.get(app1.state.platform.engine, row.id).harness_session_id)
    await until(lambda: any(e.get("type") == "text" for e in svc1.live[row.id].turn.events))
    hid = store.get(app1.state.platform.engine, row.id).harness_session_id
    first_pids = _pids(log1)
    # The app stops while the reply WORKS (an update): the turn is cut off.
    await stop_all(app1)
    assert _gone(first_pids), "the first tool process outlived the app"

    # The next start: the boot reconcile, then the carry-on pass.
    assert S.reconcile_cut_turns(home) == 0, "a graceful stop already wrote the done"
    rec = CO.read_cut(home, row.id)
    assert rec["turn"] == "t1" and rec["waiting"] is False and rec["carried"] is False and rec["auto"] == ""
    _use(monkeypatch, FIX / "claude_resume.json", log2)
    app2 = create_app(str(root))
    svc2 = S.service_for(app2.state.platform)
    carried = await CO.carry_on_after_restart(svc2)
    assert carried == [row.id]
    turn = svc2.live[row.id].turn
    await until(lambda: turn.finished.is_set())
    assert turn.status == "done"
    # The SAME conversation, through the tool's own resume, told what happened.
    run = starts(log2)
    assert len(run) == 1 and run[0]["argv"][run[0]["argv"].index("--resume") + 1] == hid
    users = [m for m in sent(log2) if m.get("type") == "user"]
    assert users and users[0]["message"]["content"][0]["text"] == CO.CONTINUE_AFTER_RESTART
    # The page: the message is a carry-on line (hidden), the cut is decided.
    view = await _view(app2, row.id)
    carry = [e for e in view["events"] if e["type"] == "user" and e.get("carry")]
    assert carry and carry[-1]["carry"] == {"why": "restart", "from": "t1"}
    assert "cut" not in view, "a reply carried on offers no Continue press"
    assert CO.read_cut(home, row.id)["auto"] == "carried"
    # Never twice: a second pass (another start) finds nothing to do.
    assert await CO.carry_on_after_restart(svc2) == []
    pids = _pids(log2)
    await stop_all(app2)
    assert _gone(pids)


async def test_a_codex_reply_a_crash_cut_off_carries_on_through_thread_resume(home, app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    row = _row(app, work, harness="codex", hid=CODEX_HID)
    path = _log(home, row.id, _working())
    assert S.reconcile_cut_turns(home) == 1, "a crash leaves no done: the reconcile writes it"
    tail = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    assert [e["type"] for e in tail[-2:]] == ["error", "done"] and tail[-1]["stop"] == "interrupted"
    _use(monkeypatch, FIX / "codex_resume.json", log)
    svc = S.service_for(app.state.platform)
    assert await CO.carry_on_after_restart(svc) == [row.id]
    turn = svc.live[row.id].turn
    await until(lambda: turn.finished.is_set())
    msgs = sent(log)
    assert not any(m.get("method") == "thread/start" for m in msgs)
    assert next(m for m in msgs if m.get("method") == "thread/resume")["params"]["threadId"] == CODEX_HID
    start = next(m for m in msgs if m.get("method") == "turn/start")["params"]
    assert any(i.get("text") == CO.CONTINUE_AFTER_RESTART for i in start["input"])
    pids = _pids(log)
    await stop_all(app)
    assert _gone(pids)


# --------------------------------------------------------------------------- #
# the rules (no process needed: the tool is a fake driver)
# --------------------------------------------------------------------------- #
async def test_the_bell_hears_it_carried_on_after_a_restart(home, app, work, monkeypatch):
    row = _row(app, work)
    _log(home, row.id, _working())
    S.reconcile_cut_turns(home)
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    seen: list = []
    bus = app.state.platform.event_bus
    real = bus.publish

    async def spy(kind, payload, **kw):
        seen.append((kind, dict(payload), kw.get("session_id")))
        return await real(kind, payload, **kw)

    monkeypatch.setattr(bus, "publish", spy)
    await CO.carry_on_after_restart(svc)
    await until(lambda: row.id in drivers and drivers[row.id].sent)
    assert drivers[row.id].sent[0][0] == CO.CONTINUE_AFTER_RESTART
    resumed = [p for k, p, s in seen if k == "build.resumed"]
    assert resumed and resumed[0]["reason"] == "restart" and resumed[0]["surface"] == "build_app"
    assert resumed[0]["thread_id"] == row.id and resumed[0]["resumed_from"] == "t1"
    assert [s for k, _p, s in seen if k == "build.resumed"] == [f"build_app:{row.id}"]
    # The carry-on's words are the app's: they never name the session.
    assert resumed[0]["title"] == "" and store.get(app.state.platform.engine, row.id).title == ""
    S.service_for(app.state.platform)._emit(svc.live[row.id], make("done", stop="completed"))
    await stop_all(app)


@pytest.mark.parametrize("case", ["stopped_by_user", "ended", "failed"])
async def test_a_reply_that_was_not_cut_by_a_restart_gets_no_record(home, app, work, monkeypatch, case):
    row = _row(app, work)
    end = {"stopped_by_user": "interrupted", "ended": "completed", "failed": "failed"}[case]
    _log(home, row.id, _working() + [_ev("done", "t1", stop=end)])
    assert S.reconcile_cut_turns(home) == 0
    assert CO.read_cut(home, row.id) == {}
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    assert await CO.carry_on_after_restart(svc) == []
    assert row.id not in drivers
    view = await _view(app, row.id)
    assert "cut" not in view


@pytest.mark.parametrize("how", ["crash", "graceful"])
async def test_a_reply_cut_while_a_card_was_open_is_offered_never_carried(home, app, work, monkeypatch, how):
    row = _row(app, work)
    ask = [_ev("approval", "t1", request_id="r1", kind="command", title="Run tests", detail="")]
    if how == "crash":
        events = _working() + ask
    else:
        events = _working() + ask + [
            _ev("resolved", "t1", request_id="r1", decision="expired"),
            {**S.W.error(S.STOPPED_TITLE, S.STOPPED_FIX, ""), "turn": "t1"},
            _ev("done", "t1", stop="interrupted"),
        ]
    _log(home, row.id, events)
    S.reconcile_cut_turns(home)
    assert CO.read_cut(home, row.id)["waiting"] is True
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    assert await CO.carry_on_after_restart(svc) == []
    assert row.id not in drivers, "a question that was lost must not be answered by a carry-on"
    view = await _view(app, row.id)
    assert view["cut"]["waiting"] is True and view["cut"]["why_not"] == CO.WHY_WAITING
    assert view["cut"]["resumable"] is True


async def test_a_card_answered_before_the_cut_does_not_hold_it_back(home, app, work, monkeypatch):
    """The card was answered (or a Pi dialog timed out) and the reply went on
    working: it was WORKING when the app went."""
    row = _row(app, work)
    _log(home, row.id, _working() + [
        _ev("approval", "t1", request_id="r1", kind="command", title="Run tests", detail=""),
        _ev("resolved", "t1", request_id="r1", decision="allow"),
        _ev("question", "t1", request_id="q1", questions=[]),
        _ev("resolved", "t1", request_id="q1", decision="expired"),
        _ev("text_done", "t1", item="a2", text="Running the tests now."),
    ])
    S.reconcile_cut_turns(home)
    assert CO.read_cut(home, row.id)["waiting"] is False
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    assert await CO.carry_on_after_restart(svc) == [row.id]
    await until(lambda: drivers.get(row.id) and drivers[row.id].sent)
    svc._emit(svc.live[row.id], make("done", stop="completed"))
    await stop_all(app)


@pytest.mark.parametrize("how", ["record_kept", "record_lost", "long_reply"])
async def test_a_carry_on_that_is_cut_again_is_never_carried_on_twice(root, home, app, work, monkeypatch, how):
    row = _row(app, work)
    _log(home, row.id, _working())
    S.reconcile_cut_turns(home)
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    assert await CO.carry_on_after_restart(svc) == [row.id]
    await until(lambda: drivers.get(row.id) and drivers[row.id].sent)
    second = svc.live[row.id].turn.id
    if how == "long_reply":
        # The carry-on worked long enough that its message is out of the
        # boot's tail read: the record's own `to` still knows it.
        for i in range(4):
            svc._emit(svc.live[row.id], make("text_done", item=f"big{i}", text="x" * 100_000))
    # The carry-on is cut by ANOTHER restart (a crash: no done).
    await asyncio.to_thread(svc.live[row.id].log.flush)
    if how == "record_lost":
        # Each of the two ways it knows (the record's `to`, the message's own
        # `carry`) is enough on its own.
        CO.drop_cut(home, row.id)
    app2 = create_app(str(root))
    S.reconcile_cut_turns(home)
    rec = CO.read_cut(home, row.id)
    assert rec["turn"] == second and rec["carried"] is True
    svc2 = S.service_for(app2.state.platform)
    drivers2 = fake_tool(svc2, monkeypatch)
    assert await CO.carry_on_after_restart(svc2) == []
    assert row.id not in drivers2
    view = await _view(app2, row.id)
    assert view["cut"]["why_not"] == CO.WHY_CARRIED
    svc._emit(svc.live[row.id], make("done", stop="completed"))
    await stop_all(app)


async def test_a_newer_message_an_archived_session_or_an_unsaved_conversation_is_not_carried(
        home, app, work, monkeypatch):
    eng = app.state.platform.engine
    newer = _row(app, work)
    _log(home, newer.id, _working())
    archived = _row(app, work)
    _log(home, archived.id, _working())
    unsaved = _row(app, work, hid="")
    _log(home, unsaved.id, _working())
    S.reconcile_cut_turns(home)
    # After the reconcile, the user sent another message to `newer` (its log
    # has a newer turn), and archived the other one.
    with open(session_dir(home, newer.id) / "events.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(_ev("user", "t9", text="never mind, do this")) + "\n")
    store.update(eng, archived.id, archived_at=store.utcnow())
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    assert await CO.carry_on_after_restart(svc) == []
    assert drivers == {}
    assert CO.read_cut(home, newer.id)["why_not"] == CO.WHY_NEWER
    # The page offers no Continue on a cut reply that is not the newest one.
    assert "cut" not in await _view(app, newer.id)
    async with client(app) as c:
        r = await c.post(f"/build/app/sessions/{newer.id}/continue")
    assert r.status_code == 409 and r.json()["detail"] == CO.NOTHING_CUT
    assert CO.read_cut(home, archived.id)["why_not"] == CO.WHY_ARCHIVED
    assert CO.read_cut(home, unsaved.id)["why_not"] == CO.WHY_NOT_SAVED
    assert (await _view(app, unsaved.id))["cut"]["resumable"] is False


async def test_emergency_stop_carries_nothing_on_and_keeps_the_record_for_a_later_start(home, app, work, monkeypatch):
    row = _row(app, work)
    _log(home, row.id, _working())
    S.reconcile_cut_turns(home)
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    app.state.platform.config.autonomy_kill_switch = True
    try:
        assert await CO.carry_on_after_restart(svc) == []
        assert drivers == {} and CO.read_cut(home, row.id)["auto"] == ""
        # The PRESS is a person's: Emergency stop does not refuse it.
        async with client(app) as c:
            r = await c.post(f"/build/app/sessions/{row.id}/continue")
            assert r.status_code == 200, r.text
        await until(lambda: drivers.get(row.id) and drivers[row.id].sent)
        assert drivers[row.id].sent[0][0] == CO.CONTINUE_AFTER_RESTART
    finally:
        app.state.platform.config.autonomy_kill_switch = False
    svc._emit(svc.live[row.id], make("done", stop="completed"))
    await stop_all(app)


async def test_a_reply_cut_beside_an_account_limit_is_left_to_its_wait(home, app, work, monkeypatch):
    row = _row(app, work)
    _log(home, row.id, _working() + [
        _ev("error", "t1", title="Claude Code hit this account's limit", fix="", technical="", fix_kind="account_limit"),
    ])
    S.reconcile_cut_turns(home)
    assert CO.read_cut(home, row.id)["limit"] is True
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    assert await CO.carry_on_after_restart(svc) == []
    # v1.422.0 review (moved on purpose): nothing WAITS here (a crash after the
    # limit wrote no wait), so the words say it had stopped on the limit —
    # never "it waits" (test_build_app_carry_review_v14220 pins both words).
    assert drivers == {} and CO.read_cut(home, row.id)["why_not"] == CO.WHY_LIMIT_CARD


def test_keep_leaves_the_sessions_a_surviving_host_still_runs_alone(home, app, work):
    kept = _row(app, work)
    other = _row(app, work)
    kept_path = _log(home, kept.id, _working())
    _log(home, other.id, _working())
    before = kept_path.read_bytes()
    assert S.reconcile_cut_turns(home, keep=[kept.id]) == 1
    assert kept_path.read_bytes() == before, "a kept session's log was written"
    assert CO.read_cut(home, kept.id) == {} and CO.read_cut(home, other.id)["turn"] == "t1"
    # The plain call still answers how many it closed (the boot's caller).
    assert S.reconcile_cut_turns(home) == 1


async def test_continue_refuses_when_nothing_was_cut(home, app, work):
    row = _row(app, work)
    _log(home, row.id, _working() + [_ev("done", "t1", stop="completed")])
    async with client(app) as c:
        r = await c.post(f"/build/app/sessions/{row.id}/continue")
    assert r.status_code == 409 and r.json()["detail"] == CO.NOTHING_CUT


async def test_the_users_own_message_supersedes_the_cut(home, app, work, monkeypatch):
    row = _row(app, work)
    _log(home, row.id, _working())
    S.reconcile_cut_turns(home)
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    await svc.start_turn(row.id, "do something else instead", "t2")
    await until(lambda: drivers.get(row.id) and drivers[row.id].sent)
    await until(lambda: CO.read_cut(home, row.id) == {})
    assert await CO.carry_on_after_restart(svc) == []
    svc._emit(svc.live[row.id], make("done", stop="completed"))
    await stop_all(app)
