"""v1.441.0 — Antigravity's cards, questions and chips in Build › Code, the
review's findings A1-A6, each pinned red first on v1.439.0's code.

A1. A note sent while a card or a question waits: Antigravity refuses a second
    ``session/prompt`` then (measured on agy 1.5.2 by the review: "the running
    prompt is waiting for a permission or question answer"). The note is refused
    in words BEFORE anything is sent (the steer route's plain 409, so the page
    puts the words back in the box); and a note's prompt Antigravity refuses
    anyway never fails the reply — the turn goes on under the prompt before it
    and the note goes back with the reply's end.
A2. A multi-select question (``type: "array"``, ``items.anyOf``) shows its
    choices and is answered with a LIST of consts (measured on agy 1.5.2 with
    the stand-in model: ``{q1: ["1", "3"], q1_other: "Purple"}`` reached the
    model as "A1: Blue, Green, Purple").
A3. A question form Iron Jarvis cannot show is declined AND said.
A4. The sign-in refusal says "send your message again" once.
A5. Antigravity 1.5.2 takes no Iron Jarvis tools (no ``mcpCapabilities``):
    the view says so (``jarvis_tools: false``), ticking a box never restarts it
    and no token is minted for it.
A6. An effort the model does not take leaves the row too.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import antigravity as A
from iron_jarvis.hosted import jarvis_tools as JT
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from iron_jarvis.hosted import words as W
from iron_jarvis.hosted.driver import Ask, ControlRefused
from tests._build_app_helpers_v1409 import client, frames, new_session, sent, stop_all, until
from tests.test_build_app_antigravity_v1439 import use_fake

#: The form agy 1.5.2 sent for an ``is_multi_select`` question (measured by the
#: review, scratchpad review/antigravity/run.json).
MULTI_SCHEMA = {"type": "object", "properties": {
    "q1": {"type": "array", "title": "Which colors?", "items": {"anyOf": [
        {"const": "1", "title": "Blue"}, {"const": "2", "title": "Red"}, {"const": "3", "title": "Green"}]}},
    "q1_other": {"type": "string", "title": "Other", "description": "Optional free-form answer to: Which colors?"},
    "q2": {"type": "string", "title": "Which size?", "oneOf": [{"const": "1", "title": "S"},
                                                             {"const": "2", "title": "M"}]},
    "q2_other": {"type": "string", "title": "Other", "description": "Optional free-form answer to: Which size?"},
}}


class Wire:
    """A driver's write side: every message kept; requests the driver makes
    can be answered by the test."""

    def __init__(self) -> None:
        self.out: list[dict] = []
        self.answers: dict[str, object] = {}
        self.driver: A.AntigravityDriver | None = None

    async def write(self, obj: dict) -> None:
        self.out.append(obj)
        method = obj.get("method")
        if method in self.answers and "id" in obj and self.driver is not None:
            got = self.answers[method]
            d, rid = self.driver, obj["id"]
            asyncio.get_running_loop().call_soon(
                lambda: d.on_message({"jsonrpc": "2.0", "id": rid, "result": got(obj) if callable(got) else got}))

    def prompts(self) -> list[dict]:
        return [m for m in self.out if m.get("method") == "session/prompt"]


def _driver(wire: Wire, **kw) -> tuple[A.AntigravityDriver, list[dict], list[Ask]]:
    events: list[dict] = []
    asks: list[Ask] = []
    d = A.AntigravityDriver(write=wire.write, emit=events.append, ask=asks.append,
                            on_session_id=lambda s: None, mode="own", **kw)
    d.session_id = "s1"
    wire.driver = d
    return d, events, asks


def _permission(rid: int = 9) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "method": "session/request_permission", "params": {
        "sessionId": "s1", "toolCall": {"toolCallId": "tc1", "title": "run_command", "kind": "execute",
                                        "rawInput": {"CommandLine": "echo hi"}},
        "options": [{"optionId": "allow", "kind": "allow_once"}, {"optionId": "deny", "kind": "reject_once"}]}}


def _question(rid: int = 8, schema: dict | None = None) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "method": "elicitation/create", "params": {
        "sessionId": "s1", "mode": "form", "message": "The agent has a question.",
        "requestedSchema": schema if schema is not None else {"type": "object", "properties": {
            "q1": {"type": "string", "title": "Which color?", "oneOf": [{"const": "1", "title": "Blue"}]}}}}}


# --------------------------------------------------------------------------- #
# A1. a note while a card or a question waits
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("asked", ["card", "question"])
def test_a_note_is_refused_in_words_while_antigravity_waits_on_the_user(asked):
    async def go() -> None:
        wire = Wire()
        d, _events, asks = _driver(wire)
        await d.send_user("run it", [])
        d.on_message(_permission() if asked == "card" else _question())
        await asyncio.sleep(0)
        assert asks, "the card is raised"
        with pytest.raises(ControlRefused) as got:
            await d.steer("n1", "and say banana", [])
        assert str(got.value) == W.ANTIGRAVITY_NOTE_WHILE_ASKING
        assert len(wire.prompts()) == 1, "nothing is sent while it waits on the user"
        assert "n1" not in d.steers
        # Answered: a note goes again.
        prepared = d.prepare_answer(asks[0], "allow", "", {"Which color?": "Blue"})
        await d.write(prepared)
        d.answered(asks[0], "allow")
        assert await d.steer("n2", "and say banana", []) == "step"
        assert len(wire.prompts()) == 2

    asyncio.run(go())


def test_a_stop_that_answers_the_card_lets_notes_through_again():
    async def go() -> None:
        wire = Wire()
        d, _events, asks = _driver(wire)
        await d.send_user("run it", [])
        d.on_message(_permission())
        d.stopping()
        await d.answer(asks[0], "deny", "The user pressed Stop.", None)
        assert not d._asking

    asyncio.run(go())


def _refuse(d: A.AntigravityDriver, rid: int) -> None:
    d.on_message({"jsonrpc": "2.0", "id": rid, "error": {
        "code": -32603, "message": "the running prompt is waiting for a permission or question answer"}})


def test_a_notes_prompt_antigravity_refuses_never_fails_the_reply():
    """The backstop: a card came in while the note went out, and Antigravity
    refused the note's prompt. v1.439.0 failed the whole reply (and every
    later message); the turn now goes on under the message's prompt."""
    async def go() -> None:
        wire = Wire()
        d, events, _asks = _driver(wire)
        await d.send_user("run it", [])
        assert await d.steer("n1", "and say banana", []) == "step"
        note_rid = wire.prompts()[1]["id"]
        message_rid = wire.prompts()[0]["id"]
        _refuse(d, note_rid)
        await asyncio.sleep(0)
        assert not [e for e in events if e["type"] in ("error", "done")], events
        assert d.turn_open
        assert W.ANTIGRAVITY_NOTE_REFUSED in [e.get("text") for e in events]
        assert d.unread_steers() == ["n1"], "the note goes back with the reply's end"
        # The message's own answer ends the turn — as finished work.
        d.on_message({"jsonrpc": "2.0", "id": message_rid, "result": {"stopReason": "end_turn"}})
        await asyncio.sleep(0)
        done = [e for e in events if e["type"] == "done"]
        assert done == [done[0]] and done[0]["stop"] == "completed"
        assert not [e for e in events if e["type"] == "error"]

    asyncio.run(go())


def test_a_refused_note_after_the_message_already_answered_still_ends_the_turn():
    """The message's prompt answered end_turn at once (superseded by the note,
    measured) and THEN the note was refused: no prompt is left open, so the
    turn ends on that earlier answer — never wedged, never failed."""
    async def go() -> None:
        wire = Wire()
        d, events, _asks = _driver(wire)
        await d.send_user("run it", [])
        await d.steer("n1", "and say banana", [])
        message_rid, note_rid = (p["id"] for p in wire.prompts())
        d.on_message({"jsonrpc": "2.0", "id": message_rid, "result": {"stopReason": "end_turn",
                                                                        "usage": {"inputTokens": 5}}})
        await asyncio.sleep(0)
        assert d.turn_open
        _refuse(d, note_rid)
        await asyncio.sleep(0)
        assert not d.turn_open
        assert [e["stop"] for e in events if e["type"] == "done"] == ["completed"]
        assert not [e for e in events if e["type"] == "error"]
        assert d.unread_steers() == ["n1"]

    asyncio.run(go())


def test_a_refused_message_prompt_still_fails_the_reply():
    """Control: only a NOTE's refused prompt is the backstop's."""
    async def go() -> None:
        wire = Wire()
        d, events, _asks = _driver(wire)
        await d.send_user("run it", [])
        _refuse(d, wire.prompts()[0]["id"])
        await asyncio.sleep(0)
        assert [e["stop"] for e in events if e["type"] == "done"] == ["failed"]

    asyncio.run(go())


@pytest.fixture
def work(tmp_path):
    p = tmp_path / "work"
    p.mkdir()
    return p


async def test_the_steer_route_refuses_a_note_while_a_card_waits_and_the_reply_goes_on(tmp_path, work, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "a_cmd_allow", log)
    app = create_app(str(tmp_path / "home"))
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        sid = (await new_session(c, work, harness="antigravity"))["id"]
        task = asyncio.ensure_future(c.post(f"/build/app/sessions/{sid}/turn", json={"text": "do it", "turn_id": "t"}))
        ask = await until(lambda: next(iter(svc.live[sid].asks), None) if sid in svc.live else None)
        prompts = len([m for m in sent(log) if m.get("method") == "session/prompt"])
        r = await c.post(f"/build/app/sessions/{sid}/steer", json={"text": "and say banana"})
        # A plain 409 (no ``not_working``: the page would send it as a message).
        assert r.status_code == 409 and r.json() == {"detail": W.ANTIGRAVITY_NOTE_WHILE_ASKING}
        assert len([m for m in sent(log) if m.get("method") == "session/prompt"]) == prompts
        r = await c.post(f"/build/app/sessions/{sid}/answer", json={"request_id": ask, "decision": "allow"})
        assert r.status_code == 200, r.text
        evs = frames((await asyncio.wait_for(task, 30)).text)
    assert evs[-1]["type"] == "done" and evs[-1]["stop"] == "completed"
    assert not [e for e in evs if e["type"] == "error"]
    await stop_all(app)


# --------------------------------------------------------------------------- #
# A2. multi-select questions
# --------------------------------------------------------------------------- #
def test_a_multi_select_question_lists_its_choices():
    qs = A.form_questions(MULTI_SCHEMA)
    assert [(q["key"], q["multi"], [o["label"] for o in q["options"]]) for q in qs] == [
        ("q1", True, ["Blue", "Red", "Green"]), ("q2", False, ["S", "M"])]


@pytest.mark.parametrize("answers, want", [
    # The page's own shape: its picks joined with ", ", the typed words last.
    ({"Which colors?": "Blue, Green, Purple", "Which size?": "M"}, {"q1": ["1", "3"], "q1_other": "Purple", "q2": "2"}),
    ({"Which colors?": ["Red"], "Which size?": "S"}, {"q1": ["2"], "q2": "1"}),
    ({"Which colors?": "Teal"}, {"q1_other": "Teal"}),
])
def test_a_multi_select_answer_is_a_list_of_consts(answers, want):
    assert A.form_answer(A.form_questions(MULTI_SCHEMA), answers) == want


def test_a_choice_whose_label_holds_a_comma_stays_one_choice():
    schema = {"type": "object", "properties": {"q1": {"type": "array", "title": "Which?", "items": {"anyOf": [
        {"const": "a", "title": "Salt, pepper"}, {"const": "b", "title": "Salt"}]}}}}
    assert A.form_answer(A.form_questions(schema), {"Which?": "Salt, pepper"}) == {"q1": ["a"]}
    assert A.form_answer(A.form_questions(schema), {"Which?": "Salt, Salt, pepper"}) == {"q1": ["b", "a"]}


def test_the_card_for_a_multi_select_question_offers_its_choices():
    async def go() -> None:
        wire = Wire()
        d, _events, asks = _driver(wire)
        await d.send_user("ask", [])
        d.on_message(_question(schema=MULTI_SCHEMA))
        rows = asks[0].event["questions"]
        assert rows[0]["multi"] is True and [o["label"] for o in rows[0]["options"]] == ["Blue", "Red", "Green"]
        assert rows[1]["multi"] is False
        got = d.prepare_answer(asks[0], "allow", "", {"Which colors?": "Blue, Red", "Which size?": "S"})
        assert got["result"] == {"action": "accept", "content": {"q1": ["1", "2"], "q2": "1"}}

    asyncio.run(go())


# --------------------------------------------------------------------------- #
# A3. a form Iron Jarvis cannot show
# --------------------------------------------------------------------------- #
def test_a_form_with_no_question_is_declined_and_said():
    async def go() -> None:
        wire = Wire()
        d, events, asks = _driver(wire)
        await d.send_user("ask", [])
        d.on_message(_question(rid=11, schema={"type": "object", "properties": {"name": {"type": "string"}}}))
        await asyncio.sleep(0.01)
        assert not asks
        assert W.ANTIGRAVITY_QUESTION_UNSHOWN in [e.get("text") for e in events]
        assert {"jsonrpc": "2.0", "id": 11, "result": {"action": "decline"}} in wire.out

    asyncio.run(go())


# --------------------------------------------------------------------------- #
# A4. the sign-in words
# --------------------------------------------------------------------------- #
def test_the_sign_in_refusal_says_send_your_message_again_once():
    assert "send your message again" not in W.ANTIGRAVITY_SIGN_IN.lower()
    golden = json.loads((Path(__file__).parents[1] / "dashboard" / "__tests__" / "fixtures"
                         / "build-app-events-v1439.json").read_text(encoding="utf-8"))
    errs = [e for ev in golden["scenarios"].values() for e in ev if isinstance(e, dict)
            and e.get("type") == "error" and "signed in" in str(e.get("title"))]
    assert errs
    for e in errs:
        said = f"{e['title']} {e['fix']}".lower()
        assert said.count("send your message again") == 1, said


# --------------------------------------------------------------------------- #
# A5. Iron Jarvis's own tools
# --------------------------------------------------------------------------- #
@pytest.fixture
def takes_reset():
    saved = dict(JT._TAKES)
    yield
    JT._TAKES.clear()
    JT._TAKES.update(saved)


def test_antigravity_takes_no_jarvis_tools_until_it_says_it_does(takes_reset):
    assert JT.takes("antigravity") is False
    assert JT.takes("opencode") is True and JT.takes("claude-code") is True
    wire = Wire()
    d, events, _asks = _driver(wire)
    d.mcp_servers = [{"type": "http", "name": JT.SERVER_NAME, "url": "http://x", "headers": []}]
    assert d.acp_servers({}) == []
    assert JT.takes("antigravity") is False
    assert d.acp_servers({"mcpCapabilities": {"http": True}}) == d.mcp_servers
    assert JT.takes("antigravity") is True


async def test_the_view_hides_the_chip_and_a_box_never_restarts_antigravity(tmp_path, work, takes_reset):
    app = create_app(str(tmp_path / "home"))
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        sid = (await new_session(c, work, harness="antigravity"))["id"]
        claude = (await new_session(c, work))["id"]
        view = (await c.get(f"/build/app/sessions/{sid}")).json()["session"]
        assert view["jarvis_tools"] is False
        assert "jarvis_tools" not in (await c.get(f"/build/app/sessions/{claude}")).json()["session"]
        live = svc._live(store.get(app.state.platform.engine, sid))
        live.proc = SimpleNamespace(alive=True)  # "running"
        r = await c.patch(f"/build/app/sessions/{sid}", json={"capabilities": {"memory": True}})
        assert r.status_code == 200, r.text
        assert r.json()["applied"]["capabilities"] == "now"
        assert live.restart is False, "Antigravity is never restarted for a box it cannot use"
        live.proc = None
    await stop_all(app)


# --------------------------------------------------------------------------- #
# A6. an effort the model does not take
# --------------------------------------------------------------------------- #
def _session_new(obj: dict) -> dict:
    return {"sessionId": "s1", "configOptions": [
        {"id": "model", "currentValue": "gemini-3.8-flash",
         "options": [{"value": "gemini-3.8-flash", "name": "Gemini 3.8 Flash"}]},
        {"id": "thought_level", "currentValue": "low", "options": [{"value": "low"}, {"value": "high"}]}]}


def test_an_effort_the_model_does_not_take_is_dropped_and_said():
    async def go() -> None:
        wire = Wire()
        wire.answers = {"initialize": {"agentCapabilities": {}}, "session/new": _session_new}
        d, events, _asks = _driver(wire, effort="max")
        dropped: list[str] = []
        d.on_effort_dropped = dropped.append
        await asyncio.wait_for(d.open(session_id="", resume=False), 5)
        assert dropped == ["max"] and d.effort == ""
        assert W.ANTIGRAVITY_NO_SUCH_EFFORT.format(effort="max") in [e.get("text") for e in events]
        assert not [m for m in wire.out if (m.get("params") or {}).get("configId") == "thought_level"]

    asyncio.run(go())


def test_the_row_loses_only_the_effort_it_still_holds(tmp_path):
    from iron_jarvis.core.db import open_db

    engine = open_db(tmp_path / "x.db")
    row = store.create(engine, harness="antigravity", folder=str(tmp_path), effort="max")
    assert store.clear_effort_if(engine, row.id, "high") is False
    assert store.get(engine, row.id).effort == "max"
    assert store.clear_effort_if(engine, row.id, "max") is True
    assert store.get(engine, row.id).effort == ""
