"""v1.514.0 (second review, group 14 part A): what the service that runs the
other coding tools REPORTS is true.

* H4  — a helper whose piece was stopped, cut off or failed is never listed
  "done" once its turn has left memory (ten minutes, or a restart): the
  helper's own log says how the piece ended. ``check_again`` tells a
  helper's piece after a restart too (its guard was already there: pinned).
* H10 — the archive summary counts the user's asks only; Iron Jarvis's own
  carry-on messages are counted on their own line.
* A7  — a Codex ``turn/completed`` / ``error`` / item frame of ANOTHER thread
  (a sub-agent's) or another turn never ends or draws into this reply.
* A9  — Codex's move to another model names the model, and a ``session``
  event carries it so the reply's receipt names the model that answered.
* A13 — an ACP ``stopReason`` Iron Jarvis does not know is a failure in
  plain words; a step Antigravity never reported the result of is not
  "done"; the model Antigravity ANSWERED is the one shown.

No real coding tool is started: the in-process ``FakeDriver`` of the v1.418.0
tests and drivers built directly with their I/O stubbed.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import acp as ACP
from iron_jarvis.hosted import antigravity as A
from iron_jarvis.hosted import archive_summary as AS
from iron_jarvis.hosted import check_again as CA
from iron_jarvis.hosted import codex as X
from iron_jarvis.hosted import handoff as HO
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from iron_jarvis.hosted.events import make

from ._build_app_helpers_v1409 import stop_all
from .test_build_app_history_v1418 import end_turn, fake_tool, until

PIECE = "handoff-0123456789ab"


@pytest.fixture
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


# --------------------------------------------------------------------------- #
# H4: a helper's state once its turn has left memory
# --------------------------------------------------------------------------- #
async def _helper_piece(tmp_path, work, monkeypatch, stop: str | None):
    """A parent and its helper; the helper runs one hand-off piece that ends
    with ``stop`` (None: still running when the daemon goes away)."""
    app = create_app(str(tmp_path / "home"))
    svc = S.service_for(app.state.platform)
    fake_tool(svc, monkeypatch)
    eng = app.state.platform.engine
    parent = store.create(eng, harness="claude-code", folder=str(work), title="Parent")
    helper = store.create(eng, harness="claude-code", folder=str(work), title="Helper: a piece",
                          handed_from=parent.id, harness_session_id="tool-session-1")
    turn = await svc.start_turn(helper.id, "Do this piece.", PIECE)
    svc.handoff_turns[helper.id] = turn.id
    live = svc.live[helper.id]
    await until(lambda: live.driver and live.driver.sent)
    svc._emit(live, make("text_done", item="a1", text="Worked on it."))
    if stop is not None:
        end_turn(svc, helper.id, stop)
        await until(lambda: live.turn is None)
    await asyncio.to_thread(live.log.flush)
    return app, svc, parent.id, helper.id


async def _state(svc, parent: str, helper: str) -> str:
    got = {h["id"]: h["state"] for h in await HO.helpers_view(svc, parent)}
    return got[helper]


@pytest.mark.parametrize("stop, want", [
    ("interrupted", "stopped"),
    ("failed", "failed"),
    ("max_turns", "stopped"),
    ("completed", "done"),
])
async def test_a_helper_reads_its_pieces_ending_after_the_turn_left_memory_and_after_a_restart(
        tmp_path, work, monkeypatch, stop, want):
    app, svc, parent, helper = await _helper_piece(tmp_path, work, monkeypatch, stop)
    fresh = None
    try:
        assert await _state(svc, parent, helper) == want  # the turn is still known
        svc.turns.clear()  # ten minutes after its end (FINISHED_KEEP_S)
        assert await _state(svc, parent, helper) == want, "the turn left memory"
        # A restart: a new service on the same data, nothing in memory.
        fresh = create_app(str(tmp_path / "home"))
        svc2 = S.service_for(fresh.state.platform)
        assert not svc2.handoff_turns and not svc2.turns
        assert await _state(svc2, parent, helper) == want, "after a restart"
        # The parent's own tools read the same: the list and the check.
        got = await HO.check(svc2, parent, helper, wait_s=0)
        assert got["state"] == want
        assert ("stopped" in HO._check_words(got)) == (want == "stopped")
    finally:
        await stop_all(app)
        if fresh is not None:
            await stop_all(fresh)


async def test_a_piece_a_crash_cut_is_not_done(tmp_path, work, monkeypatch):
    """No ``done`` in the helper's log: the daemon went away mid-piece."""
    app, svc, parent, helper = await _helper_piece(tmp_path, work, monkeypatch, None)
    fresh = create_app(str(tmp_path / "home"))
    try:
        svc2 = S.service_for(fresh.state.platform)
        assert await _state(svc2, parent, helper) == "stopped"
    finally:
        await stop_all(app)
        await stop_all(fresh)


def test_the_helpers_log_is_read_once_while_it_does_not_change(tmp_path, monkeypatch):
    from iron_jarvis.hosted import history as H
    from iron_jarvis.hosted.events import session_dir

    d = session_dir(tmp_path, "h1")
    d.mkdir(parents=True)
    path = d / "events.jsonl"
    path.write_text(
        '{"type": "user", "text": "piece", "turn": "%s"}\n{"type": "done", "stop": "interrupted", "turn": "%s"}\n'
        % (PIECE, PIECE), encoding="utf-8")
    reads: list[str] = []
    real = H._read_events
    monkeypatch.setattr(H, "_read_events", lambda p: reads.append(str(p)) or real(p))
    HO._LOGGED.clear()
    assert [HO.logged_piece_state(tmp_path, "h1") for _ in range(5)] == ["stopped"] * 5
    assert len(reads) == 1
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"type": "user", "text": "more", "turn": "handoff-ba9876543210"}\n'
                 '{"type": "done", "stop": "completed", "turn": "handoff-ba9876543210"}\n')
    assert HO.logged_piece_state(tmp_path, "h1") == "done"
    assert len(reads) == 2


# --------------------------------------------------------------------------- #
# H4 (check_again): a helper's piece after a restart is never sent back
# --------------------------------------------------------------------------- #
async def _claimed_missing(tmp_path, work, monkeypatch, *, helper: bool):
    app = create_app(str(tmp_path / "home"))
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    eng = app.state.platform.engine
    parent = store.create(eng, harness="claude-code", folder=str(work))
    row = store.create(eng, harness="claude-code", folder=str(work),
                       handed_from=parent.id if helper else "")
    await svc.start_turn(row.id, "Do this piece.", PIECE)
    assert not svc.handoff_turns  # as after a restart: the memory map is empty
    await until(lambda: row.id in drivers and drivers[row.id].sent)
    live = svc.live[row.id]
    svc._emit(live, make("tool", item="s1", kind="command", name="Bash", title="Run the build",
                         input="python build.py", status="done"))
    svc._emit(live, make("text_done", item="a1", text="Done! I saved report.xlsx with the totals."))
    replied = live.turn
    svc._emit(live, make("done", stop="completed"))
    await until(lambda: live.turn is not replied)
    for _ in range(20):
        await asyncio.sleep(0)
    await until(lambda: not CA.judging(svc))
    return app, svc, row.id, drivers


async def test_check_again_never_sends_a_helpers_piece_back_after_a_restart(tmp_path, work, monkeypatch):
    app, svc, sid, drivers = await _claimed_missing(tmp_path, work, monkeypatch, helper=True)
    try:
        # A send-back starts its turn before the check ends (``CO._start``):
        # none is running, and nothing more went to the tool.
        assert svc.live[sid].turn is None, "a helper's piece was sent back as if the user asked"
        assert len(drivers[sid].sent) == 1
    finally:
        await stop_all(app)


async def test_check_again_still_sends_back_a_session_that_is_not_a_helper(tmp_path, work, monkeypatch):
    """The control: the same reply in an ordinary session IS sent back."""
    app, svc, sid, drivers = await _claimed_missing(tmp_path, work, monkeypatch, helper=False)
    try:
        await until(lambda: len(drivers[sid].sent) >= 2)
    finally:
        await stop_all(app)


# --------------------------------------------------------------------------- #
# H10: the archive summary counts the user's asks only
# --------------------------------------------------------------------------- #
def test_the_archive_summary_counts_the_users_asks_and_says_the_carry_ons_apart():
    row = SimpleNamespace(harness="claude-code", folder="", title="Fix the website", model="",
                          account="", created_at=None)
    restart = "Iron Jarvis restarted while you were working on my last message. Carry on."
    check = "FROM IRON JARVIS, NOT FROM THE USER: the reply said it saved a file that is not there."
    events = [
        make("user", text="Fix the website's footer"), make("done", stop="completed"),
        {**make("user", text=restart), "carry": {"why": "restart"}}, make("done", stop="completed"),
        make("user", text="Now the header too"), make("done", stop="completed"),
        {**make("user", text=check), "carry": {"why": "check"}}, make("done", stop="completed"),
    ]
    text = AS.build_summary(row, events)
    assert "- Messages sent: 2" in text
    assert "Now the header too" in text
    assert restart[:30] not in text and check[:30] not in text
    assert "- Iron Jarvis carried it on 2 times." in text
    assert AS.build_summary(row, events) == text  # deterministic


# --------------------------------------------------------------------------- #
# A7 / A9: Codex frames of another thread or turn; the reroute
# --------------------------------------------------------------------------- #
MAIN, SUB = "thread-main", "thread-sub"


def _codex():
    seen: list[dict] = []
    d = X.CodexDriver(write=None, emit=seen.append, ask=lambda a: None, on_session_id=lambda s: None, mode="ask")
    d.session_id = MAIN
    d.turn_open = True
    d.turn_id = "turn-1"
    return d, seen


def _note(d, method, params):
    d.on_message({"jsonrpc": "2.0", "method": method, "params": params})


def _types(seen):
    return [e["type"] for e in seen]


ERR = {"message": "Something broke in the sub-agent", "codexErrorInfo": "other"}


def test_a_codex_turn_end_or_error_of_another_thread_does_not_end_the_reply():
    d, seen = _codex()
    _note(d, "turn/completed", {"threadId": SUB, "turn": {"id": "turn-sub", "status": "completed"}})
    _note(d, "error", {"threadId": SUB, "turnId": "turn-sub", "willRetry": False, "error": ERR})
    _note(d, "item/completed", {"threadId": SUB, "turnId": "turn-sub",
                                "item": {"id": "m1", "type": "agentMessage", "text": "sub-agent words"}})
    _note(d, "item/agentMessage/delta", {"threadId": SUB, "turnId": "turn-sub", "itemId": "m1", "delta": "x"})
    assert d.turn_open and "done" not in _types(seen) and "error" not in _types(seen)
    assert "text_done" not in _types(seen) and "text" not in _types(seen)


def test_a_codex_turn_end_of_another_turn_of_this_thread_does_not_end_the_reply():
    d, seen = _codex()
    _note(d, "turn/completed", {"threadId": MAIN, "turn": {"id": "turn-0", "status": "completed"}})
    _note(d, "error", {"threadId": MAIN, "turnId": "turn-0", "willRetry": False, "error": ERR})
    assert d.turn_open and "done" not in _types(seen) and "error" not in _types(seen)


def test_the_same_frames_of_this_thread_do_end_the_reply():
    d, seen = _codex()
    _note(d, "item/completed", {"threadId": MAIN, "turnId": "turn-1",
                                "item": {"id": "m1", "type": "agentMessage", "text": "my words"}})
    _note(d, "error", {"threadId": MAIN, "turnId": "turn-1", "willRetry": False, "error": ERR})
    _note(d, "turn/completed", {"threadId": MAIN, "turn": {"id": "turn-1", "status": "failed", "error": ERR}})
    assert _types(seen).count("text_done") == 1 and "error" in _types(seen)
    assert not d.turn_open and seen[-1]["type"] == "done" and seen[-1]["stop"] == "failed"


def test_a_codex_frame_naming_no_thread_is_taken_as_before():
    d, seen = _codex()
    _note(d, "turn/completed", {"turn": {"id": "turn-1", "status": "completed"}})
    assert not d.turn_open and seen[-1] == {**seen[-1], "type": "done", "stop": "completed"}


def test_codex_moving_a_turn_names_the_model_and_the_receipt_reads_it():
    d, seen = _codex()
    _note(d, "model/rerouted", {"threadId": MAIN, "turnId": "turn-1", "fromModel": "model-a",
                                "toModel": "model-b", "reason": "highRiskCyberActivity"})
    notices = [e["text"] for e in seen if e["type"] == "notice"]
    assert notices == [X.REROUTED_TO_WORDS.format(model="model-b")] and "model-b" in notices[0]
    sessions = [e for e in seen if e["type"] == "session"]
    assert len(sessions) == 1 and sessions[0]["model"] == "model-b"
    assert sessions[0]["harness"] == "codex" and sessions[0]["harness_session_id"] == MAIN


def test_codex_moving_a_sub_agents_turn_is_not_this_replys():
    d, seen = _codex()
    _note(d, "model/rerouted", {"threadId": SUB, "turnId": "turn-sub", "fromModel": "model-a",
                                "toModel": "model-b", "reason": "highRiskCyberActivity"})
    assert seen == []


def test_codex_moving_a_turn_without_a_model_keeps_the_plain_sentence():
    d, seen = _codex()
    _note(d, "model/rerouted", {"threadId": MAIN, "turnId": "turn-1"})
    assert [e["text"] for e in seen if e["type"] == "notice"] == [X.REROUTED_WORDS]
    assert not [e for e in seen if e["type"] == "session"]


async def test_the_next_codex_turn_starts_on_the_chosen_model_again(monkeypatch):
    d, seen = _codex()
    d.model = "model-a"
    _note(d, "model/rerouted", {"threadId": MAIN, "turnId": "turn-1", "toModel": "model-b"})
    _note(d, "turn/completed", {"threadId": MAIN, "turn": {"id": "turn-1", "status": "completed"}})

    async def no_turn(items):
        return None

    monkeypatch.setattr(d, "_start_codex_turn", no_turn)
    seen.clear()
    await d.send_user("next message", [])
    assert [e["model"] for e in seen if e["type"] == "session"] == ["model-a"]


# --------------------------------------------------------------------------- #
# A13: the ACP stop reason, Antigravity's unreported step and its model
# --------------------------------------------------------------------------- #
def _agy(seen):
    return A.AntigravityDriver(write=None, emit=seen.append, ask=lambda a: None,
                               on_session_id=lambda s: None, mode="own")


@pytest.mark.parametrize("tool", ["antigravity", "opencode"])
def test_an_unknown_acp_stop_reason_is_a_failure_in_plain_words(tool):
    from iron_jarvis.hosted.opencode import OpenCodeDriver

    seen: list[dict] = []
    d = _agy(seen) if tool == "antigravity" else OpenCodeDriver(
        write=None, emit=seen.append, ask=lambda a: None, on_session_id=lambda s: None, mode="ask")
    loop = asyncio.new_event_loop()
    try:
        fut = loop.create_future()
        d.turn_open = True
        d._pending = {1: ""}
        d._message_prompt = 1
        d._latest = 1  # Antigravity's newest prompt
        fut.set_result({"stopReason": "something_new"})
        d._prompt_answered(1, fut)
    finally:
        loop.close()
    assert seen[-1]["type"] == "done" and seen[-1]["stop"] == "failed"
    errors = [e for e in seen if e["type"] == "error"]
    assert len(errors) == 1
    assert errors[0]["title"] == ACP.UNKNOWN_STOP_TITLE.format(label=d.LABEL, reason="something new")
    assert "does not know: something new" in errors[0]["title"]


def test_a_known_acp_stop_reason_is_unchanged():
    d = _agy([])
    assert d.stop_of("end_turn", {}) == "completed"
    assert d.stop_of("cancelled", {}) == "interrupted"
    assert d.stop_of("max_turn_requests", {}) == "max_turns"


def test_an_antigravity_step_it_never_reported_is_not_done_nor_a_changed_file():
    seen: list[dict] = []
    d = _agy(seen)
    d._tools["e1"] = ("replace_file_content", "edit", {"TargetFile": "C:/work/site/index.html"})
    d.end_turn_tools()
    rows = [e for e in seen if e["type"] == "tool"]
    assert len(rows) == 1 and rows[0]["status"] == "stopped" and rows[0]["output"] == A.UNREPORTED_TOOL
    # What a hand-off and the receipt count as changed: finished edits only.
    assert HO.summarize([{**rows[0], "turn": "t1"}], "t1")["files"] == []


def _config(model: str) -> list[dict]:
    return [
        {"id": "model", "currentValue": model,
         "options": [{"value": "model-a", "name": "Model A"}, {"value": "model-b", "name": "Model B"}]},
        {"id": "thought_level", "currentValue": "low", "options": [{"value": "low"}, {"value": "high"}]},
    ]


async def test_antigravity_shows_the_model_it_answered_not_the_one_asked_for(monkeypatch):
    monkeypatch.setattr(A, "LAST_MODELS", [])
    monkeypatch.setattr(A, "LAST_DEFAULT", [])
    d = _agy([])
    d.session_id = "s1"

    async def answered(method, params=None, timeout=None):
        return {"configOptions": _config("model-a")}

    d.request = answered
    await d._set_model("model-b")
    assert d._current("model") == "model-a"


async def test_antigravity_shows_the_model_asked_for_when_its_answer_names_none(monkeypatch):
    monkeypatch.setattr(A, "LAST_MODELS", [])
    monkeypatch.setattr(A, "LAST_DEFAULT", [])
    d = _agy([])
    d.session_id = "s1"
    d._take_options(_config("model-a"))

    async def silent(method, params=None, timeout=None):
        return {}

    d.request = silent
    await d._set_model("model-b")
    assert d._current("model") == "model-b"


def test_a_codex_sub_agents_step_is_still_drawn_but_its_words_are_not():
    """Lead review of A7. A sub-agent thread's command or file change is work
    done for this reply and stays in it (a helper's changes are reported);
    its own message is not the reply's text."""
    from iron_jarvis.hosted import codex as C

    out: list[dict] = []
    d = C.CodexDriver.__new__(C.CodexDriver)
    d.session_id, d.turn_id, d._items, d._out = "thread-main", "turn-1", {}, {}
    d.emit = out.append  # type: ignore[method-assign]
    d._tool_event = lambda item, status: {"type": "tool", "id": item.get("id"), "status": status}  # type: ignore[method-assign]

    d._on_notification("item/completed", {
        "threadId": "thread-sub",
        "item": {"id": "c1", "type": "commandExecution", "command": "pytest", "status": "completed", "exitCode": 0},
    })
    d._on_notification("item/completed", {
        "threadId": "thread-sub", "item": {"id": "m1", "type": "agentMessage", "text": "sub-agent words"},
    })
    assert [e["type"] for e in out] == ["tool"], out
