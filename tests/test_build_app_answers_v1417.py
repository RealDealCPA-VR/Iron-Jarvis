"""v1.417.0 (fix plan wave 1) — Build › Code's cards, answers and ends.

* C1.1: a command card offers "Allow all commands this session"; the answer
  ``allow_commands`` reaches Claude Code as Iron Jarvis's OWN whole-tool
  session rule (never its per-prefix suggestion), and the second command of
  the process asks nothing. Codex maps it to its ``acceptForSession`` only
  when the request offers it; Pi and OpenCode refuse in words.
* C4.1: Claude Code's ExitPlanMode card carries the plan; a deny carries the
  user's own words to Claude Code.
* C4.9: a failure no rule knows names the tool ("Claude Code couldn't finish
  this answer"), with a fix that fits the Retry press.
* C4.7: a process (re)starting for a message is said; a slow id save no
  longer holds the reply's end for 10 s; a note's hold is 5 s.
* C1.11: every card and its answer reach the autonomy metrics (surface
  ``build_app``), counted under "build".

Every scenario replays RECORDED Claude Code frames through the fake harness
(``tests/fixtures/fake_harness_v1409.py``) — the real routes, service,
parser, store and process plumbing.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import claude as C
from iron_jarvis.hosted import codex as X
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from iron_jarvis.hosted import words as W
from iron_jarvis.hosted.driver import Ask
from tests._build_app_helpers_v1409 import client, frames, new_session, sent, stop_all, until, use_fake


@pytest.fixture
def work(tmp_path):
    p = tmp_path / "work"
    p.mkdir()
    return p


@pytest.fixture
def app(tmp_path):
    a = create_app(str(tmp_path / "home"))
    yield a


async def _turn(c, svc, sid, *, text="do it", turn_id="t"):
    return asyncio.ensure_future(c.post(f"/build/app/sessions/{sid}/turn", json={"text": text, "turn_id": turn_id}))


async def _next_ask(svc, sid, seen: set):
    rid = await until(lambda: next((a for a in (svc.live.get(sid).asks if svc.live.get(sid) else {})
                                    if a not in seen), None))
    seen.add(rid)
    return svc.live[sid].asks[rid]


def _responses(log):
    return [m for m in sent(log) if m.get("type") == "control_response"
            and str((m.get("response") or {}).get("request_id") or "").startswith("req-")]


# --------------------------------------------------------------------------- #
# C1.1 — Allow all commands this session
# --------------------------------------------------------------------------- #
async def test_all_commands_reaches_claude_as_a_whole_tool_session_rule_and_asks_once(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_all_commands_v1417", log)
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        sid = (await new_session(c, work))["id"]
        task = await _turn(c, svc, sid)
        ask = await _next_ask(svc, sid, set())
        assert ask.event.get("can_all_commands") is True
        # Claude Code's own suggestion stays, under its own words.
        assert ask.event["can_session"] is True
        assert "Allow similar commands always allows PowerShell(Get-ChildItem *)" in ask.event["detail"]
        r = await c.post(f"/build/app/sessions/{sid}/answer", json={"request_id": ask.id, "decision": "allow_commands"})
        assert r.status_code == 200, r.text
        assert r.json()["decision"] == "allow_commands"
        evs = frames((await asyncio.wait_for(task, 30)).text)
    await stop_all(app)
    resp = _responses(log)
    assert len(resp) == 1
    body = resp[0]["response"]["response"]
    assert body["behavior"] == "allow"
    assert body["updatedPermissions"] == [C.ALL_COMMANDS_RULE]
    # (review) the literal shape, so a change to the shared constant cannot
    # pass by comparing it with itself: BOTH command tools (Claude Code on
    # Windows runs commands through either; this scenario's next command is
    # PowerShell), whole tools (no ruleContent), this session only.
    assert body["updatedPermissions"] == [{
        "type": "addRules", "rules": [{"toolName": "Bash"}, {"toolName": "PowerShell"}],
        "behavior": "allow", "destination": "session",
    }]
    assert body["updatedPermissions"][0]["rules"][0] == {"toolName": "Bash"}
    # The second command of the same process asked nothing.
    assert [e["type"] for e in evs].count("approval") == 1
    assert [e for e in evs if e["type"] == "resolved"][0]["decision"] == "allow_commands"
    assert evs[-1]["type"] == "done" and evs[-1]["stop"] == "completed"


async def test_a_plain_allow_gets_the_second_command_card(app, work, tmp_path, monkeypatch):
    """The control: the scenario's branch is driven by the ANSWER."""
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_all_commands_v1417", log)
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        sid = (await new_session(c, work))["id"]
        task = await _turn(c, svc, sid)
        seen: set = set()
        for _ in range(2):
            ask = await _next_ask(svc, sid, seen)
            r = await c.post(f"/build/app/sessions/{sid}/answer", json={"request_id": ask.id, "decision": "allow"})
            assert r.status_code == 200, r.text
        evs = frames((await asyncio.wait_for(task, 30)).text)
    await stop_all(app)
    assert [e["type"] for e in evs].count("approval") == 2
    assert all("updatedPermissions" not in m["response"]["response"] for m in _responses(log))


def _claude(mode="edits"):
    got: list = []
    d = C.ClaudeDriver(write=None, emit=lambda e: None, ask=got.append, on_session_id=lambda s: None, mode=mode)
    return d, got


def _can_use(name, inp, sugg=None):
    req = {"subtype": "can_use_tool", "tool_name": name, "input": inp, "tool_use_id": "tu1"}
    if sugg:
        req["permission_suggestions"] = sugg
    return {"type": "control_request", "request_id": "r1", "request": req}


@pytest.mark.parametrize("name,offered", [("Bash", True), ("PowerShell", True), ("Write", False), ("WebFetch", False)])
def test_only_a_command_card_offers_all_commands(name, offered):
    d, got = _claude()
    d.on_message(_can_use(name, {"command": "ls", "file_path": "C:/x/a.txt", "url": "https://e.x"}))
    assert bool(got[0].event.get("can_all_commands")) is offered
    assert bool(got[0].data.get("all_commands")) is offered


def test_full_access_offers_no_all_commands():
    d, got = _claude("full")
    d.on_message(_can_use("Bash", {"command": "ls"}))
    assert "can_all_commands" not in got[0].event


@pytest.mark.parametrize("mode", ["auto", "plan"])
def test_no_all_commands_where_claude_code_would_not_keep_a_whole_shell_rule(mode):
    """Review: claude 2.1.316 treats a whole-tool Bash / PowerShell allow as
    "dangerous ... (bypasses classifier)" in Auto — it strips it on entering
    Auto (``stripDangerousPermissionsForAutoMode``) and leaves it out of the
    rules it matches while the classifier runs — so the button would not do
    what it says there. Plan reads and plans only, and Claude Code strips
    the same rule when its plan mode uses Auto's classifier."""
    d, got = _claude(mode)
    d.on_message(_can_use("Bash", {"command": "ls"}, [{"type": "addRules", "rules": [
        {"toolName": "Bash", "ruleContent": "ls *"}], "behavior": "allow", "destination": "localSettings"}]))
    assert "can_all_commands" not in got[0].event
    assert not got[0].data.get("all_commands")
    # Claude Code's own (narrower) allow keeps its usual words.
    assert "Allow for this session always allows Bash(ls *)" in got[0].event["detail"]


def test_all_commands_is_refused_when_the_mode_moved_to_auto_while_the_card_waited():
    d, got = _claude("edits")
    d.on_message(_can_use("Bash", {"command": "ls"}))
    assert got[0].event.get("can_all_commands") is True
    d.mode = "auto"
    with pytest.raises(C.AskRefused):
        d.prepare_answer(got[0], C.ALLOW_COMMANDS, "", None)


def test_all_commands_on_a_card_that_does_not_offer_it_is_refused_before_anything_is_sent():
    d, got = _claude()
    d.on_message(_can_use("Write", {"file_path": "C:/x/a.txt", "content": "x"}))
    with pytest.raises(C.AskRefused) as e:
        d.prepare_answer(got[0], C.ALLOW_COMMANDS, "", None)
    assert "Allow all commands this session" in str(e.value)


def test_codex_maps_all_commands_to_accept_for_session_only_when_offered():
    d = X.CodexDriver(write=None, emit=lambda e: None, ask=lambda a: None, on_session_id=lambda s: None, mode="ask")
    offered = Ask("a1", "approval", "shell", 7, {"method": "item/commandExecution/requestApproval"}, {}, "", True)
    assert d.prepare_answer(offered, "allow_commands", "", None)["result"] == {"decision": "acceptForSession"}
    once = Ask("a2", "approval", "shell", 8, {"method": "item/commandExecution/requestApproval"}, {}, "", False)
    with pytest.raises(X.AskRefused) as e:
        d.prepare_answer(once, "allow_commands", "", None)
    assert str(e.value) == W.ALL_COMMANDS_NOT_OFFERED.format(label="Codex")
    edit = Ask("a3", "approval", "apply_patch", 9, {"method": "item/fileChange/requestApproval"}, {}, "", True)
    with pytest.raises(X.AskRefused):
        d.prepare_answer(edit, "allow_commands", "", None)


@pytest.mark.parametrize("harness", ["pi", "opencode"])
async def test_pi_and_opencode_refuse_all_commands_in_words(app, work, harness):
    svc = S.service_for(app.state.platform)
    row = store.create(app.state.platform.engine, harness=harness, folder=str(work))
    live = svc._live(row)

    class _Drv:
        async def answer(self, *a):  # pragma: no cover - must never be reached
            raise AssertionError("sent")

    live.driver = _Drv()
    ask = Ask("ask_x", "approval", "bash", 1, {}, {"type": "approval"}, "", True)
    live.asks[ask.id] = ask
    with pytest.raises(S.Refused) as e:
        await svc.answer(row.id, ask.id, "allow_commands")
    assert e.value.status == 400
    assert e.value.sentence == W.ALL_COMMANDS_NOT_OFFERED.format(label=W.label(harness))
    assert ask.id in live.asks  # still waiting


# --------------------------------------------------------------------------- #
# C4.1 — the plan on the card; a deny carries the user's words
# --------------------------------------------------------------------------- #
async def test_the_plan_card_shows_the_plan_and_a_deny_carries_the_users_words(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_plan_v1417", log)
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        sid = (await new_session(c, work))["id"]
        task = await _turn(c, svc, sid)
        ask = await _next_ask(svc, sid, set())
        assert ask.event["title"] == W.PLAN_CARD_TITLE
        assert ask.event["plan"].startswith("# Plan\n\n1. Read `a.txt`.")
        r = await c.post(f"/build/app/sessions/{sid}/answer",
                         json={"request_id": ask.id, "decision": "deny", "message": "Write it in one file instead."})
        assert r.status_code == 200, r.text
        await asyncio.wait_for(task, 30)
    await stop_all(app)
    resp = _responses(log)
    assert resp[0]["response"]["response"] == {"behavior": "deny", "message": "Write it in one file instead."}


def test_a_long_plan_is_masked_then_cut():
    d, got = _claude("plan")
    plan = "Use key sk-ant-api03-" + "A" * 40 + "\n" + "x" * 30_000
    d.on_message(_can_use("ExitPlanMode", {"plan": plan}))
    shown = got[0].event["plan"]
    assert len(shown) == C.PLAN_CHARS and "A" * 40 not in shown


def test_a_plain_deny_still_says_no():
    d, got = _claude()
    d.on_message(_can_use("Bash", {"command": "ls"}))
    resp = d.prepare_answer(got[0], "deny", "", None)["response"]["response"]
    assert resp == {"behavior": "deny", "message": "The user said no to this."}


# --------------------------------------------------------------------------- #
# C4.9 — the failure names the tool
# --------------------------------------------------------------------------- #
async def test_a_failure_no_rule_knows_names_the_tool_and_fits_retry(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_failed_v1417", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        sid = (await new_session(c, work))["id"]
        evs = frames((await asyncio.wait_for(await _turn(c, svc, sid), 30)).text)
    await stop_all(app)
    err = next(e for e in evs if e["type"] == "error")
    assert err["title"] == "Claude Code couldn't finish this answer"
    assert err["fix"].startswith("Press Retry.")
    assert "unexpected end of data" in err["technical"]
    assert evs[-1]["stop"] == "failed"


@pytest.mark.parametrize("harness,name", [("claude-code", "Claude Code"), ("codex", "Codex"),
                                          ("pi", "Pi"), ("opencode", "OpenCode")])
def test_generic_failure_words_name_every_tool(harness, name):
    ev = W.generic_error(harness, "something nobody recognises")
    assert ev["title"] == f"{name} couldn't finish this answer"
    assert "Iron Jarvis" not in ev["title"]


def test_the_tool_label_reaches_the_shared_failure_words():
    from iron_jarvis.providers import failure_words as fw

    exc = W.HostedToolError("Codex", "boom")
    assert fw.friendly_error(exc, surface=fw.SURFACE_BUILD)["title"] == "Codex couldn't finish this answer"
    # Every other failure keeps its words.
    assert fw.friendly_error(RuntimeError("boom"), surface=fw.SURFACE_BUILD)["title"] == fw.GENERIC_TITLE


# --------------------------------------------------------------------------- #
# C4.7 — cold starts said; shorter holds
# --------------------------------------------------------------------------- #
async def test_the_first_message_says_the_tool_is_starting(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_failed_v1417", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        sid = (await new_session(c, work))["id"]
        evs = frames((await asyncio.wait_for(await _turn(c, svc, sid), 30)).text)
    await stop_all(app)
    assert evs[0]["type"] == "user"
    assert evs[1] == {**evs[1], "type": "notice", "text": "Starting Claude Code…"}


@pytest.mark.parametrize("why,resumed,words", [
    ("idle", True, "Resuming Claude Code after 15 minutes idle…"),
    ("evicted", True, "Starting Claude Code again (it was stopped to make room for another session)…"),
    ("", True, "Resuming Claude Code…"),
    ("", False, "Starting Claude Code…"),
])
async def test_a_restart_for_a_message_says_why(app, work, why, resumed, words):
    svc = S.service_for(app.state.platform)
    row = store.create(app.state.platform.engine, harness="claude-code", folder=str(work),
                       harness_session_id="11111111-2222-3333-4444-555555555555" if resumed else "")
    live = svc._live(row)
    live.stopped_why = why
    seen: list = []
    live.log.append = seen.append  # type: ignore[method-assign]
    svc._say_start(live, row)
    assert [e["text"] for e in seen] == [words]
    assert live.stopped_why == ""


async def test_the_reaper_and_the_eviction_say_why_the_process_went(app, work):
    svc = S.service_for(app.state.platform)
    row = store.create(app.state.platform.engine, harness="claude-code", folder=str(work))
    live = svc._live(row)

    class _Proc:
        alive = True
        stopped = False

        def stop(self):
            self.alive = False

    live.proc = _Proc()
    live.last_active = 0.0
    # v1.418.0 (moved on purpose): idle also counts from the tool's last frame.
    live.last_heard = 0.0
    assert await svc.reap_once(now=S.IDLE_S + 1) == [row.id]
    assert live.stopped_why == "idle"


async def test_open_in_terminal_right_after_a_reply_waits_for_the_id_still_being_saved(app, work, tmp_path, monkeypatch):
    """Review: the reply's end now waits at most 2 s for the id save (it was
    10 s). A save slower than that (a busy database) used to be caught by
    the longer wait; "Open in terminal" pressed the moment the reply ends
    must still find the id (the v1.414.4 bug), so it waits for the save
    itself."""
    from types import SimpleNamespace

    from iron_jarvis.daemon.routes import terminals as T

    real = store.update

    def slow(engine, sid, **fields):
        if "harness_session_id" in fields:
            time.sleep(3.5)
        return real(engine, sid, **fields)

    launched: list = []

    def fake_launch(platform, body, *, accounts=None, command=None, claude_session_id=None, **kw):
        launched.append(command)
        return SimpleNamespace(id="term_1")

    monkeypatch.setattr(S.store, "update", slow)
    monkeypatch.setattr(T, "launch_pane", fake_launch)
    use_fake(monkeypatch, "claude_failed_v1417", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        sid = (await new_session(c, work))["id"]
        await asyncio.wait_for(await _turn(c, svc, sid), 30)
        r = await c.post(f"/build/app/sessions/{sid}/open-terminal")
        assert r.status_code == 200, r.text
        hid = store.get(app.state.platform.engine, sid).harness_session_id
    await stop_all(app)
    assert hid and launched == [f"claude --resume {hid}"]


async def test_a_slow_id_save_no_longer_holds_the_reply(app, work, tmp_path, monkeypatch):
    real = store.update

    def slow(engine, sid, **fields):
        if "harness_session_id" in fields:
            time.sleep(6.0)
        return real(engine, sid, **fields)

    monkeypatch.setattr(S.store, "update", slow)
    use_fake(monkeypatch, "claude_failed_v1417", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        sid = (await new_session(c, work))["id"]
        task = await _turn(c, svc, sid)
        turn = await until(lambda: svc.turn("t"))
        await until(lambda: any(e["type"] == "usage" for e in turn.events), timeout=30)
        t0 = time.monotonic()
        await asyncio.wait_for(turn.finished.wait(), 30)
        held = time.monotonic() - t0
        await asyncio.wait_for(task, 30)
    await stop_all(app)
    assert held < 2.5, f"the reply's end waited {held:.1f} s for the id save"


def test_the_waits_are_the_shorter_ones():
    assert S.ID_SAVE_WAIT_S == 2.0
    assert C.HOLD_FOR_NOTE_S == 5.0


# --------------------------------------------------------------------------- #
# C1.11 — the cards reach the autonomy metrics
# --------------------------------------------------------------------------- #
async def test_every_card_and_answer_is_counted_under_build(app, work, tmp_path, monkeypatch):
    from iron_jarvis.eval import autonomy as A

    use_fake(monkeypatch, "claude_all_commands_v1417", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    seen_events: list = []

    async def spy(event_type, payload, **kw):
        seen_events.append((str(event_type), dict(payload)))
        return await real_publish(event_type, payload, **kw)

    bus = app.state.platform.event_bus
    real_publish = bus.publish
    monkeypatch.setattr(bus, "publish", spy)
    async with client(app) as c:
        sid = (await new_session(c, work))["id"]
        task = await _turn(c, svc, sid)
        seen: set = set()
        for _ in range(2):
            ask = await _next_ask(svc, sid, seen)
            await c.post(f"/build/app/sessions/{sid}/answer", json={"request_id": ask.id, "decision": "allow"})
        await asyncio.wait_for(task, 30)
        await until(lambda: sum(1 for t, _ in seen_events if t == "interrupt.answered") >= 2)
    await stop_all(app)
    asked = [p for t, p in seen_events if t == "interrupt.asked"]
    answered = [p for t, p in seen_events if t == "interrupt.answered"]
    assert len(asked) == 2 and len(answered) == 2
    assert {p["surface"] for p in asked + answered} == {"build_app"}
    assert {p["thread_id"] for p in asked} == {sid}
    assert {p["turn_id"] for p in asked} == {"t"}
    assert [(p["decision"], p["actor"]) for p in answered] == [("once", "app"), ("once", "app")]
    events = [{"type": t, "payload": p, "created_at": None} for t, p in seen_events
              if t in ("interrupt.asked", "interrupt.answered")]
    got = A.compute(threads=[], sessions=[], events=events, days=30, now=time.time())
    assert got["interruptions"]["total"] == 2
    assert got["by_surface"]["build"]["interruptions"] == 2
    assert got["interruptions"]["unattributed"] == 0


async def test_the_phone_and_a_stop_are_said_by_who_answered(app, work):
    svc = S.service_for(app.state.platform)
    row = store.create(app.state.platform.engine, harness="claude-code", folder=str(work))
    live = svc._live(row)
    published: list = []

    class _Bus:
        async def publish(self, event_type, payload, **kw):
            published.append((str(event_type), payload))

    app.state.platform.event_bus = _Bus()

    class _Drv:
        async def answer(self, ask, decision, message, answers):
            return decision

        async def interrupt(self):
            return None

    live.driver = _Drv()
    a1 = Ask("ask_1", "approval", "Bash", 1, {}, {"type": "approval"}, "", True)
    svc._ask(live, a1)
    await svc.answer(row.id, a1.id, "allow_commands", actor="phone")
    await asyncio.sleep(0.05)
    answered = [p for t, p in published if t == "interrupt.answered"]
    assert answered[0]["actor"] == "phone" and answered[0]["decision"] == "conversation"
    a2 = Ask("ask_2", "question", "AskUserQuestion", 2, {}, {"type": "question"}, "", False)
    svc._ask(live, a2)
    svc._expire_asks(live)
    await asyncio.sleep(0.05)
    answered = [p for t, p in published if t == "interrupt.answered"]
    assert answered[1]["kind"] == "question" and answered[1]["decision"] == "stopped"


async def test_the_plan_card_is_counted_as_a_plan_approval(app, work):
    """Review: the metrics never count a plan approval as avoidable (it IS the
    "approve once") and report it apart (``by_kind.plan``); Claude Code's
    ExitPlanMode card was counted as an ordinary card."""
    svc = S.service_for(app.state.platform)
    row = store.create(app.state.platform.engine, harness="claude-code", folder=str(work))
    live = svc._live(row)
    published: list = []

    class _Bus:
        async def publish(self, event_type, payload, **kw):
            published.append((str(event_type), payload))

    app.state.platform.event_bus = _Bus()
    svc._ask(live, Ask("ask_p", "approval", "ExitPlanMode", 1, {}, {"type": "approval"}, "", True))
    svc._ask(live, Ask("ask_b", "approval", "Bash", 2, {}, {"type": "approval"}, "", True))
    await asyncio.sleep(0.05)
    kinds = {p["tool"]: p["kind"] for t, p in published if t == "interrupt.asked"}
    assert kinds == {"ExitPlanMode": "plan", "Bash": "card"}


def test_metric_words():
    assert S.metric_decision("approval", "allow") == "once"
    assert S.metric_decision("approval", "allow_session") == "conversation"
    assert S.metric_decision("approval", "allow_commands") == "conversation"
    assert S.metric_decision("approval", "deny") == "deny"
    assert S.metric_decision("question", "allow") == "answered"
    assert S.metric_decision("question", "deny") == "skipped"


def test_build_app_cards_count_but_their_turns_never_lower_the_hands_off_rate():
    """Review: a Build › Code turn is seen here ONLY when it asked something
    (its reply is in the session's own event log), and its success is never
    read — so counting it as a task put an unsuccessful task into every
    denominator: one card dropped a week of hands-off chat work from 100% to
    50%. Its cards count as interruptions; the tasks, the success and
    hands-off rates and "per task" are over the tasks the metric sees whole."""
    from iron_jarvis.eval import autonomy as A

    now = time.time()
    chat = {"id": "c1", "surface": "chat", "created_at": None, "messages": [
        {"role": "user", "content": "rename the files", "at": now - 300},
        {"role": "assistant", "content": "Done: renamed 3 files.", "at": now - 290},
    ]}
    evs = [
        {"type": "interrupt.asked", "payload": {"surface": "build_app", "thread_id": "app_1", "turn_id": "t1",
                                                "ask_id": "a1", "kind": "card", "tool": "Bash", "at": now - 100}},
        {"type": "interrupt.answered", "payload": {"surface": "build_app", "thread_id": "app_1", "turn_id": "t1",
                                                   "ask_id": "a1", "kind": "card", "tool": "Bash",
                                                   "decision": "once", "actor": "app", "at": now - 90}},
    ]
    got = A.compute(threads=[chat], sessions=[], events=evs, days=30, now=now)
    assert got["interruptions"]["total"] == 1
    assert got["by_surface"]["build"]["interruptions"] == 1
    assert got["tasks"] == 1 and got["hands_off"] == 1 and got["hands_off_pct"] == 100.0
    assert got["interruptions"]["per_task"] == 0.0
    assert got["by_surface"]["build"]["tasks"] == 0


def test_build_app_tasks_split_by_turn():
    from iron_jarvis.eval import autonomy as A

    evs = [
        {"type": "interrupt.asked", "payload": {"surface": "build_app", "thread_id": "app_1", "turn_id": "t1", "at": 10.0}},
        {"type": "interrupt.asked", "payload": {"surface": "build_app", "thread_id": "app_1", "turn_id": "t2", "at": 50.0}},
        {"type": "interrupt.asked", "payload": {"surface": "chat", "thread_id": "c1", "at": 20.0}},
    ]
    ts = A.build_app_tasks(evs)
    assert [(t.surface, t.key, t.start, t.end) for t in ts] == [("build", "app_1", 10.0, 50.0),
                                                                ("build", "app_1", 50.0, float("inf"))]
    assert A.build_app_tasks(evs, known={"app_1": []}) == []
    assert json.dumps(A.empty(30)["by_surface"]["build"])  # still a block
