"""North-star fix 7.1 (v1.507.0): what went wrong in a chat leaves ONE suggestion.

In chat, the surface the owner uses most, "what went wrong" was captured only
for a correction of STYLE said twice. A correction of substance, an Undo of a
change a reply made and a thumbs-down with a note left nothing anyone read
(the note became a lesson every prompt carried at once, unasked). Now each
leaves ONE suggestion: status proposed, origin "noticed", source
"went_wrong", the turn as evidence with the link to its chat, the owner's own
words, the never-ask-twice rule and the open limit of ``mint_noticed``. It
waits in Improvements and reaches no prompt until kept; once kept it is told
only where the client wall lets its chat be told.

Real app, real routes, real registry and undo journal; the model is a stub
and the client RECORD a made-up map (as in the v1.501 wall tests). Names and
folders are made up.
"""

from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import init_db, make_engine, session_scope
from iron_jarvis.core.models import ChatThreadRecord, ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.learning import corrections, preferences as prefs
from iron_jarvis.learning.engine import LearningEngine
from iron_jarvis.learning.models import FeedbackRecord, LessonRecord
from iron_jarvis.learning.tools import RecallLessonsTool
from iron_jarvis.workmap.clients import TurnWall, store, wall
from tests.test_chat_turn_stop_v1241 import _drive_stream
from tests.test_north_star_lessons_wall_v1501 import H, S, _chat, _map, _turn
from tests.test_preferences_v1305 import _capture_complete, _capture_stream

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _a_generous_budget(monkeypatch):
    # A CORRECTNESS pin (what is suggested), never a timing one.
    monkeypatch.setattr(prefs, "TURN_BUDGET_S", 60.0)


def _app(tmp_path):
    app = create_app(str(tmp_path / "d"))
    platform = app.state.platform
    _capture_complete(platform, [])
    _capture_stream(platform, [])
    return app, platform


def _rows(engine, *, source=prefs.SOURCE_WENT_WRONG):
    with session_scope(engine) as db:
        return list(db.exec(select(LessonRecord).where(LessonRecord.source == source)))


def _turn_body(said: str, *, thread: str = "th_1", before=("prepare the march figures", "Done, in imperial units."), **extra):
    msgs = []
    if before:
        msgs += [{"role": "user", "content": before[0]}, {"role": "assistant", "content": before[1]}]
    msgs.append({"role": "user", "content": said})
    return {"messages": msgs, "auto_tools": False, "thread_id": thread, **extra}


# --------------------------------------------------------------------------- #
# the detector: the smallest reliable signal, and conservative
# --------------------------------------------------------------------------- #

SUBSTANCE = [
    ("no, metric units, not imperial", "Metric units, not imperial."),
    ("No, metric units, not imperial.", "Metric units, not imperial."),
    ("nope - use metric units, not imperial", "Use metric units, not imperial."),
    ("wrong: metric units instead of imperial", "Metric units instead of imperial."),
    ("no, not imperial, metric units", "Not imperial, metric units."),
    ("that's wrong, gross figures rather than net", "Gross figures rather than net."),
]
NOT_SUBSTANCE = [
    "metric units, not imperial",            # no "no," — not said as a correction
    "no metric units",                      # no punctuation after "no": a request
    "no, the other file",                 # a thing being made, and pointing
    "no, this one, not that one",         # pointing at this reply's things
    "no, Tom, not Jane",                  # a name: one person's, not a lesson
    "no, 2023, not 2024",                 # a number or date: a one-off
    "no, I meant metric units, not imperial",  # "I meant": a slip, not a lesson
    "no, metric units, not imperial?",       # a question
    "no, send it to tom, not jane",       # a task
    "no, metric units, not imperial. redo the table",  # more than one sentence
    "no, metric units not imperial for now",  # scoped to now
    "no, shorter",                        # style: the v1.305 path
    "no tables",                          # style
    "no, keep it short, not long",        # style, not substance
    "no, `metric`, not imperial",            # code-shaped
]


@pytest.mark.parametrize("said,text", SUBSTANCE)
def test_a_correction_of_substance_is_read_in_the_owners_own_words(said, text):
    assert corrections.is_substance_correction(said)
    assert corrections.substance_text(said) == text


@pytest.mark.parametrize("said", NOT_SUBSTANCE)
def test_anything_else_is_not_a_correction_of_substance(said):
    assert not corrections.is_substance_correction(said)
    assert corrections.substance_text(said) == ""


# --------------------------------------------------------------------------- #
# (b) a correction of substance in the next message — both lanes
# --------------------------------------------------------------------------- #


def test_the_post_lane_leaves_one_suggestion_with_the_turn_as_evidence(tmp_path):
    app, platform = _app(tmp_path)
    out = TestClient(app).post("/chat", json=_turn_body("no, metric units, not imperial", thread="th_post")).json()
    assert out["suggestion"] is None  # it waits in Improvements, not under the reply
    (row,) = _rows(platform.engine)
    assert (row.text, row.status, row.origin, row.source) == (
        "Metric units, not imperial.", "proposed", "noticed", prefs.SOURCE_WENT_WRONG,
    )
    (ev,) = prefs.evidence_of(row)
    assert ev["quote"] == "no, metric units, not imperial"
    assert ev["where"] == "chat" and ev["link"] == "/chat?thread=th_post"


def test_the_stream_lane_leaves_one_suggestion_too(tmp_path):
    app, platform = _app(tmp_path)
    frames = asyncio.run(_drive_stream(app, _turn_body("no, use metric units, not imperial", thread="th_stream")))
    done = [d for kind, d in frames if kind == "done"]
    assert done and done[0]["suggestion"] is None
    (row,) = _rows(platform.engine)
    assert row.text == "Use metric units, not imperial."
    assert prefs.evidence_of(row)[0]["link"] == "/chat?thread=th_stream"


@pytest.mark.parametrize(
    "body",
    [
        _turn_body("no, metric units, not imperial", before=None),       # nothing was said before it
        _turn_body("no, metric units, not imperial", thread=""),         # a chat that is not saved
        _turn_body("no, the other file"),                             # a fix of this reply only
        _turn_body("no, metric units, not imperial", pane_id="term_x"),  # a Build pane's chat
    ],
    ids=["first-message", "unsaved", "not-substance", "pane"],
)
def test_no_suggestion_without_a_reply_a_saved_chat_or_the_shape(tmp_path, body):
    app, platform = _app(tmp_path)
    TestClient(app).post("/chat", json=body)
    assert _rows(platform.engine) == []


def test_never_asked_twice_and_the_open_limit_holds(tmp_path):
    app, platform = _app(tmp_path)
    client = TestClient(app)
    client.post("/chat", json=_turn_body("no, metric units, not imperial", thread="th_a"))
    client.post("/chat", json=_turn_body("no, metric units, not imperial", thread="th_b"))
    (row,) = _rows(platform.engine)
    assert client.post(f"/memory/preferences/{row.id}/decline").status_code == 200
    client.post("/chat", json=_turn_body("No, metric units, not imperial.", thread="th_c"))
    assert [r.status for r in _rows(platform.engine)] == ["declined"]  # declined is final
    with session_scope(platform.engine) as db:
        for i in range(prefs.MAX_OPEN):
            db.add(LessonRecord(text=f"Waiting marker {i}.", source="preference", status="proposed", origin="noticed"))
        db.commit()
    client.post("/chat", json=_turn_body("no, gross figures, not net", thread="th_d"))
    assert len(_rows(platform.engine)) == 1  # the open limit


# --------------------------------------------------------------------------- #
# (c) a thumbs-down with a note
# --------------------------------------------------------------------------- #


def test_a_thumbs_down_note_waits_as_a_suggestion_and_is_no_lesson_yet(tmp_path):
    app, platform = _app(tmp_path)
    r = TestClient(app).post(
        "/chat/feedback", json={"rating": "down", "comment": "  use metric units in every table  ", "thread_id": "th_9"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["suggested"] is True and body["remembered"] is False
    assert body["words"] == "Noted. Your note waits in Improvements as a suggestion; it is used once you keep it."
    (row,) = _rows(platform.engine)
    assert row.text == "Use metric units in every table." and row.status == "proposed"
    (ev,) = prefs.evidence_of(row)
    assert ev["quote"] == "use metric units in every table" and ev["link"] == "/chat?thread=th_9"
    assert _rows(platform.engine, source="feedback") == []  # nothing reaches a prompt unasked
    assert "metric units" not in platform.learning.apply_to_prompt("")
    with session_scope(platform.engine) as db:
        (fb,) = list(db.exec(select(FeedbackRecord)))
    assert (fb.session_id, fb.rating) == ("chat:th_9", "down")  # the rating is still recorded


def test_a_note_in_an_unsaved_chat_says_so_and_a_bare_thumbs_down_suggests_nothing(tmp_path):
    app, platform = _app(tmp_path)
    client = TestClient(app)
    r = client.post("/chat/feedback", json={"rating": "down", "comment": "use the metric units"}).json()
    assert r["suggested"] is False and "not saved yet" in r["words"]
    r = client.post("/chat/feedback", json={"rating": "down", "thread_id": "th_1"}).json()
    assert r["suggested"] is False
    assert _rows(platform.engine) == []


# --------------------------------------------------------------------------- #
# (a) the owner undoes a change a chat reply made
# --------------------------------------------------------------------------- #


def _undo_client(platform) -> TestClient:
    from iron_jarvis.daemon.routes import undo as undo_routes

    app = FastAPI()
    undo_routes.register(app, SimpleNamespace(platform=platform))
    return TestClient(app)


def _chat_write(platform, ws: Path, name: str = "memo.txt") -> str:
    from iron_jarvis.tools.base import ToolContext

    ws.mkdir(parents=True, exist_ok=True)
    (ws / name).write_text("ORIGINAL", encoding="utf-8")
    ctx = ToolContext(workspace=ws, session_id="chat", agent_run_id="chat", config=platform.config,
                      event_bus=platform.event_bus, engine=platform.engine)
    res = asyncio.run(platform.registry.invoke(
        "write_file", {"path": name, "content": "NEW"}, ctx, platform.permissions, session_allow=["write_file"],
    ))
    assert res.ok, res.error
    with session_scope(platform.engine) as db:
        return [r.id for r in db.exec(select(ToolInvocation).where(ToolInvocation.tool == "write_file"))][-1]


def _saved_chat(platform, request: str, reply: dict) -> str:
    msgs = [{"role": "user", "content": request}, {"role": "assistant", "content": "Done.", **reply}]
    with session_scope(platform.engine) as db:
        rec = ChatThreadRecord(title="memo", messages_json=json.dumps(msgs))
        db.add(rec)
        db.commit()
        return rec.id


def test_undoing_a_file_a_chat_reply_wrote_leaves_one_suggestion(tmp_path):
    app, platform = _app(tmp_path)
    ws = tmp_path / "uploads"
    action = _chat_write(platform, ws)
    tid = _saved_chat(platform, "update the memo for march", {"documents": [str(ws / "memo.txt")]})
    r = _undo_client(platform).post(f"/undo/{action}")
    assert r.status_code == 200, r.text
    assert (ws / "memo.txt").read_text(encoding="utf-8") == "ORIGINAL"  # the undo itself is unchanged
    (row,) = _rows(platform.engine)
    assert row.status == "proposed" and row.origin == "noticed"
    assert row.text.startswith("This was undone: ") and "“update the memo for march”" in row.text
    (ev,) = prefs.evidence_of(row)
    assert ev["quote"] == "update the memo for march" and ev["link"] == f"/chat?thread={tid}"


def test_a_reply_that_carries_the_changes_id_is_found_by_it(tmp_path):
    app, platform = _app(tmp_path)
    action = _chat_write(platform, tmp_path / "uploads")
    tid = _saved_chat(platform, "store the key", {"configCards": [{"kind": "secret", "action_id": action}]})
    assert _undo_client(platform).post(f"/undo/{action}").status_code == 200
    (row,) = _rows(platform.engine)
    assert prefs.evidence_of(row)[0]["link"] == f"/chat?thread={tid}"


def test_no_suggestion_when_the_reply_cannot_be_told_for_certain(tmp_path):
    app, platform = _app(tmp_path)
    ws = tmp_path / "uploads"
    action = _chat_write(platform, ws)
    for _ in range(2):  # two replies list the same file: which one is unknown
        _saved_chat(platform, "update the memo", {"documents": [str(ws / "memo.txt")]})
    assert _undo_client(platform).post(f"/undo/{action}").status_code == 200
    assert _rows(platform.engine) == []


# --------------------------------------------------------------------------- #
# nothing reaches a prompt until kept; kept, it is walled by its chat
# --------------------------------------------------------------------------- #


@pytest.fixture()
def walled(tmp_path, monkeypatch):
    engine = make_engine(tmp_path / "w.db")
    init_db(engine)
    cmap = _map()
    monkeypatch.setattr(store, "load_map", lambda e: cmap)
    yield engine
    engine.dispose()


def _kept_correction(engine, thread_id: str, text: str = "Metric units, not imperial.") -> str:
    body = SimpleNamespace(
        messages=[SimpleNamespace(role="user", content="prepare it"), SimpleNamespace(role="assistant", content="Done."),
                  SimpleNamespace(role="user", content="no, " + text.rstrip(".").lower())],
        thread_id=thread_id, pane_id="",
    )
    wrong = importlib.import_module("iron_jarvis.learning.went_wrong")
    row = wrong.suggest_from_correction(engine, body)
    assert row is not None
    return row.id


def _prompt(engine, turn: TurnWall) -> str:
    with wall.turn_scope(turn):
        return LearningEngine(engine).apply_to_prompt("")


def _recalled(engine, turn: TurnWall) -> str:
    async def go():
        ctx = SimpleNamespace(session_id="chat")
        with wall.turn_scope(turn):
            return (await RecallLessonsTool(LearningEngine(engine)).execute({}, ctx)).output

    return asyncio.run(go())


def test_a_waiting_suggestion_reaches_no_prompt_and_a_kept_one_is_walled_by_its_chat(walled):
    h_chat = _chat(walled, H)
    pid = _kept_correction(walled, h_chat)
    for turn in (_turn(walled, H), _turn(walled, S), TurnWall()):
        assert "Metric units" not in _prompt(walled, turn)  # waiting: no prompt at all
    prefs.keep(walled, pid)
    assert "Metric units, not imperial." in _prompt(walled, _turn(walled, H))
    assert "Metric units, not imperial." in _recalled(walled, _turn(walled, H))
    for turn in (_turn(walled, S), TurnWall()):  # another client's folder; no client
        assert "Metric units" not in _prompt(walled, turn), turn
        assert "Metric units" not in _recalled(walled, turn), turn
    assert "Metric units" in _prompt(walled, _turn(walled, r"C:\Users\Owner\Downloads", "the Hartleys books"))


def test_a_kept_correction_from_a_chat_with_no_client_is_told_everywhere(walled):
    plain = _chat(walled, r"C:\Users\Owner\Documents")
    prefs.keep(walled, _kept_correction(walled, plain, "Gross figures, not net."))
    for turn in (_turn(walled, H), _turn(walled, S), TurnWall()):
        assert "Gross figures, not net." in _prompt(walled, turn), turn


def test_a_wall_that_cannot_be_read_tells_none_of_them(walled):
    plain = _chat(walled, r"C:\Users\Owner\Documents")
    prefs.keep(walled, _kept_correction(walled, plain, "Gross figures, not net."))
    assert "Gross figures" not in _prompt(walled, TurnWall(ok=False))
    with wall.owner_screen():  # the owner's own screens are not walled
        assert "Gross figures" in LearningEngine(walled).apply_to_prompt("")


def test_the_improvements_item_says_where_a_kept_one_is_told(tmp_path):
    collect = importlib.import_module("iron_jarvis.inbox.collect")
    item = collect.preference_item({
        "id": "p1", "text": "Metric units, not imperial.", "source": prefs.SOURCE_WENT_WRONG,
        "evidence": [{"quote": "no, metric units, not imperial", "where": "chat", "link": "/chat?thread=t"}],
    })
    assert item["evidence"] == ["You said, in a chat: “no, metric units, not imperial”"]
    assert "every later chat" not in item["accept"]["note"]
    assert "another client" in item["accept"]["note"]
