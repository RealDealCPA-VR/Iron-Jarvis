"""North-star plan 6.2: THE IMPROVEMENTS PAGE FROM CHAT (v1.506.0).

The Improvements page had no chat tool, no door and no lookup line. Now:

* "What suggestions are waiting?" arms ``suggestion_list`` (reads, no card),
  in both lanes; its answer is the page's own read (``inbox/collect``);
* "keep the one about my email tone" arms ``suggestion_answer`` (stream lane,
  ask tier: the card every time), which answers ONE item THROUGH THAT ITEM'S
  OWN ROUTE — the route's rules hold (an answered item is not answered twice)
  and the inbox decides nothing itself;
* an item answered only on its own page (an offer to stop asking) or blocked
  right now is refused in words, with the door to the page;
* both tools' replies carry the door to /improvements; the lookup has a line
  for each tool and a row for the page;
* ``suggestion_answer`` is never armed where nobody can be asked, nor in low
  trust.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from iron_jarvis.capability_search import index as CI
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.trust import LOW_TRUST_DENY
from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.daemon import doors as D
from iron_jarvis.daemon.app import create_app
from iron_jarvis.inbox import tools as IT
from iron_jarvis.learning.models import LessonRecord
from iron_jarvis.settings.records import record_tools_for
from tests.test_script_power_lanes_v1473 import Model, _body, _call, _post, _rows, _stream

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(300)]

DOOR = {"href": "/improvements", "label": "Open Improvements"}


@pytest.fixture()
def desk(tmp_path):
    app = create_app(str(tmp_path / "home"))
    ws = tmp_path / "work"
    ws.mkdir()
    yield app, ws


def _pref(app, text: str) -> str:
    with session_scope(app.state.platform.engine) as db:
        row = LessonRecord(
            text=text, source="preference", origin="noticed", status="proposed", weight=5, scope="user",
            evidence_json=json.dumps([{"quote": "too formal again", "at": "", "where": "chat"}]),
            signature=f"sig-{text}",
        )
        db.add(row)
        db.commit()
        return row.id


def _status(app, rid: str) -> str:
    with session_scope(app.state.platform.engine) as db:
        return db.get(LessonRecord, rid).status


def _plain(ws, text, **extra):
    body = _body(ws, text, **extra)
    body.pop("workspace_dir", None)
    return body


# --------------------------------------------------------------------------- #
# the words
# --------------------------------------------------------------------------- #
def test_the_words_bring_the_tools():
    assert record_tools_for("What suggestions are waiting?") == ["suggestion_list"]
    got = record_tools_for("keep the one about my email tone")
    assert "suggestion_answer" in got and "suggestion_list" in got, got
    assert "suggestion_answer" in record_tools_for("show me your suggestions")
    for plain in ("give me suggestions for dinner", "make improvements to my essay", "keep it short"):
        assert not {"suggestion_list", "suggestion_answer"} & set(record_tools_for(plain)), plain


def test_the_answer_asks_and_low_trust_keeps_it_away(desk):
    app, _ws = desk
    perms = app.state.platform.permissions
    assert perms.mode_for("suggestion_list").value == "allow"
    assert perms.mode_for("suggestion_answer").value == "ask"
    assert "suggestion_answer" in LOW_TRUST_DENY and "suggestion_list" not in LOW_TRUST_DENY
    d = SimpleNamespace(platform=app.state.platform)
    assert CT.post_record_tools(d, "keep the one about my email tone", []) == ["suggestion_list"]


# --------------------------------------------------------------------------- #
# through the real lanes
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_what_suggestions_are_waiting_lists_them_in_both_lanes(desk, lane):
    app, ws = desk
    rid = _pref(app, "Write client emails in a warmer tone")
    model = Model([_call(1, "suggestion_list"), "One is waiting."])
    body = _plain(ws, "What suggestions are waiting?")
    if lane == "stream":
        _f, cards, done = await _stream(app, body, model)
        assert cards == []
    else:
        done = await _post(app, body, model)
    assert "suggestion_list" in model.offered[0], model.offered[0]
    said = model.tool_texts[1][-1]
    assert f"[preference:{rid}]" in said and "warmer tone" in said, said
    assert DOOR in done["doors"], done["doors"]


@pytest.mark.asyncio
async def test_keep_the_one_about_my_email_tone_answers_through_its_own_route(desk):
    app, ws = desk
    rid = _pref(app, "Write client emails in a warmer tone")
    other = _pref(app, "Use metric units")
    model = Model([
        _call(1, "suggestion_answer", id=f"preference:{rid}", answer="accept"),
        "Kept.",
    ])
    _f, cards, done = await _stream(app, _plain(ws, "keep the one about my email tone"), model, decision="once")
    assert [c.get("tool") for c in cards] == ["suggestion_answer"], "a card first"
    assert _status(app, rid) == "confirmed", "the route kept it"
    assert _status(app, other) == "proposed", "only the one asked for"
    assert DOOR in done["doors"]
    # Declined on the card: nothing changes.
    third = _pref(app, "Sign emails with my first name")
    model = Model([_call(1, "suggestion_answer", id=f"preference:{third}", answer="accept"), "Ok."])
    await _stream(app, _plain(ws, "keep the one about my signature"), model, decision="deny")
    assert _status(app, third) == "proposed"


@pytest.mark.asyncio
async def test_an_item_already_answered_is_not_answered_twice(desk):
    app, ws = desk
    rid = _pref(app, "Write client emails in a warmer tone")
    model = Model([
        _call(1, "suggestion_answer", id=f"preference:{rid}", answer="decline"),
        _call(2, "suggestion_answer", id=f"preference:{rid}", answer="accept"),
        "Done.",
    ])
    _f, _cards, done = await _stream(app, _plain(ws, "decline that one"), model, decision="once")
    assert _status(app, rid) == "declined", _status(app, rid)
    assert "is waiting now" in model.tool_texts[2][-1], model.tool_texts[2]
    assert DOOR in done["doors"]


@pytest.mark.asyncio
async def test_the_route_decides_a_refusal(desk, monkeypatch):
    """The answer goes to the item's own route: a route that refuses is
    said in its own words, and nothing is kept."""
    app, ws = desk
    rid = _pref(app, "Write client emails in a warmer tone")
    seen: list[tuple[str, str]] = []
    from iron_jarvis.daemon.routes import improvements as IR

    real = IR.answer_through_route

    async def spy(d, method, path, body):
        seen.append((method, path))
        return await real(d, method, path, body)

    monkeypatch.setattr(IR, "answer_through_route", spy)
    tool = app.state.platform.registry.get("suggestion_answer")
    ctx = SimpleNamespace(session_id="chat", turn_id="t")
    out = await tool.execute({"id": f"preference:{rid}", "answer": "accept"}, ctx)
    assert out.ok, out.error
    assert seen == [("POST", f"/memory/preferences/{rid}/keep")], seen
    # The route's own refusal comes back as its status and its sentence.
    status, detail = await real(app.state.d, "POST", "/memory/preferences/no-such-pref/keep", {})
    assert status >= 400 and detail, (status, detail)
    assert _status(app, rid) == "confirmed"


@pytest.mark.asyncio
async def test_an_offer_to_stop_asking_is_answered_only_on_its_page(desk, monkeypatch):
    app, _ws = desk
    import importlib

    C = importlib.import_module("iron_jarvis.inbox.collect")
    offer = C.unasked_item(SimpleNamespace(id="goal_1", name="Tidy", contract_text=""), "web_fetch", count=3)
    monkeypatch.setattr(IT, "_read", lambda p: {"items": [offer], "total": 1, "unreadable": []})
    tool = app.state.platform.registry.get("suggestion_answer")
    out = await tool.execute({"id": offer["id"], "answer": "accept"}, SimpleNamespace())
    assert not out.ok and "own page" in out.error
    assert D.failure_door_for("suggestion_answer", {}, out) == DOOR


def test_the_lookup_has_both_tools_and_the_page(desk):
    app, _ws = desk
    ids = {e.id for e in CI.build(app.state.platform, phrasings=False).entries}
    assert {"suggestion_list", "suggestion_answer", "page_improvements"} <= ids
