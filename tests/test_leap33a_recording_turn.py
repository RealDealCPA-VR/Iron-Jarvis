"""Idea 33-a, end to end: drop a recording into a chat and get the transcript,
the decisions and the open items, each with its time, as files in the chat's
folder.

* Both chat lanes: an attached recording arms ``transcribe_recording`` by its
  type, the attachment block tells the model the words are not in the
  message, the real worker child hears it (the stand-in recognizer: CI has no
  Vosk model), the notes are checked, and the card rides the reply.
* The transcript's lines are kept as ``time`` marks (they stay when the chat
  is deleted).
* One Undo removes both files (a file edited since refuses it).
* A recording that takes longer than the reply comes home to the chat as ONE
  report, also when the chat was new and is linked after the turn.
* The same recording handed again in the same folder makes nothing new.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ChatThreadRecord, ToolInvocation
from iron_jarvis.documents import recording as R
from iron_jarvis.documents import recording_jobs as J
from iron_jarvis.documents import source_store
from iron_jarvis.tools.base import ToolContext
from tests._fakes.recordings import LOCAL_CHOICE, MEETING, Deps, NotesModel, use_fake_vosk, wav

pytestmark = pytest.mark.timeout(300)


def _rig(tmp_path: Path, monkeypatch, *, inline: float = 120.0):
    from iron_jarvis.daemon.app import create_app

    use_fake_vosk(monkeypatch, tmp_path, MEETING)
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    monkeypatch.setattr(J, "_DEPS", Deps(p, model="C:/fake/vosk-model"))
    monkeypatch.setattr(J, "notes_choice", lambda r, c, local_only, chat=None: dict(LOCAL_CHOICE))
    monkeypatch.setattr(J, "INLINE_WAIT_S", inline)
    notes = NotesModel()
    ws = tmp_path / "work"
    ws.mkdir()
    clip = wav(ws / "meeting.wav", 130.0)
    return app, p, ws, clip, notes


@pytest.mark.asyncio
@pytest.mark.parametrize("lane", ["post", "stream"])
async def test_a_recording_dropped_in_a_chat_becomes_a_transcript_and_checked_notes_in_both_lanes(
        tmp_path, monkeypatch, lane):
    from tests.test_chat_approvals_v1187 import _asgi_post
    from tests.test_script_power_lanes_v1473 import Model, _body, _call, _stream

    app, p, ws, clip, notes = _rig(tmp_path, monkeypatch)
    model = Model([_call(1, "transcribe_recording", path=str(clip)), "Here is what was decided."])
    # D-33.1: the chat's OWN model reaches the job from the turn, in both lanes.
    chats: list = []
    monkeypatch.setattr(J, "notes_choice",
                        lambda r, c, local_only, chat=None: chats.append(chat) or dict(LOCAL_CHOICE))

    async def complete(**kw):
        if kw.get("system") == R.NOTES_SYSTEM:
            return await notes.complete(**kw)
        return await model.complete(**kw)

    p.router.complete = complete
    body = _body(ws, "what did we decide?", attachments=[str(clip)], provider="openai", model="gpt-test")
    if lane == "post":
        status, raw = await _asgi_post(app, "/chat", body)
        assert status == 200, raw
        done = json.loads(raw)
    else:
        _frames, _cards, done = await _stream(app, body, model)
    assert "transcribe_recording" in model.offered[0], "armed by the file's type"
    assert "Attached recording: meeting.wav" in model.systems[0]
    assert "Call transcribe_recording with this path" in model.systems[0]

    cards = [c for c in done.get("config_cards") or [] if c.get("kind") == "recording"]
    assert len(cards) == 1, done.get("config_cards")
    card = cards[0]
    assert card["status"] == "done" and card["on_this_pc"] is True
    assert card["counts"] == {"decisions": 1, "you_owe": 1, "they_owe": 1, "dropped": 2}
    assert card["items"]["decisions"][0]["time"] == "01:10"
    assert card["dropped_words"].startswith("2 items were left out")
    assert card["made_words"] == R.SMALL_MODEL_WORDS

    assert sorted(x.name for x in ws.iterdir()) == ["meeting notes.md", "meeting transcript.md", "meeting.wav"]
    transcript = (ws / "meeting transcript.md").read_text(encoding="utf-8")
    assert "[01:10] we decided to move the launch to march" in transcript
    assert "[01:35] i will send the revised plan on friday" in transcript
    assert R.SMALL_MODEL_WORDS in transcript
    said = model.tool_texts[1][0]
    assert str(ws / "meeting transcript.md") in said and str(ws / "meeting notes.md") in said
    assert "1 decision, 1 thing you owe, 1 thing they owe" in said
    assert all(c["pin"] for c in notes.calls)
    assert chats == [("openai", "gpt-test")], "the notes are chosen with the chat's own model"


def _ctx(p, ws: Path, *, thread_id: str = "thr_rec", turn_id: str = "turn-rec") -> ToolContext:
    from iron_jarvis.documents.sources import SourceTurn

    return ToolContext(
        workspace=ws, session_id="chat", agent_run_id="chat", config=p.config,
        event_bus=p.event_bus, engine=p.engine, thread_id=thread_id,
        source_turn=SourceTurn(thread_id=thread_id, turn_id=turn_id, engine=p.engine),
    )


def _invoke(p, ws, clip, **kw):
    return asyncio.run(p.registry.invoke(
        "transcribe_recording", {"path": str(clip)}, _ctx(p, ws, **kw), p.permissions, None,
        allowed_names={"transcribe_recording"},
    ))


def _route_complete(p, notes):
    async def complete(**kw):
        return await notes.complete(**kw)

    p.router.complete = complete


def test_the_minutes_are_kept_and_one_undo_removes_both_files(tmp_path, monkeypatch):
    app, p, ws, clip, notes = _rig(tmp_path, monkeypatch)
    _route_complete(p, notes)
    with TestClient(app) as client:
        r = _invoke(p, ws, clip)
        assert r.ok, r.error
        transcript = ws / "meeting transcript.md"
        with session_scope(p.engine) as db:
            marks = [m for m in db.exec(select(source_store.SourceMark)).all() if m.path == str(transcript)]
        assert [(m.kind, m.kept, m.shown) for m in marks] == [
            ("time", True, "[00:00]"), ("time", True, "[01:10]"), ("time", True, "[01:35]"), ("time", True, "[01:50]")]
        assert json.loads(marks[1].locator_json) == {"line": 2, "time": 70.2}

        with session_scope(p.engine) as db:
            row = next(x for x in db.exec(select(ToolInvocation)).all() if x.tool == "transcribe_recording")
        res = client.post(f"/undo/{row.id}")
        assert res.status_code == 200, res.text
    assert sorted(x.name for x in ws.iterdir()) == ["meeting.wav"], "the recording itself stays"
    with session_scope(p.engine) as db:
        assert not [m for m in db.exec(select(source_store.SourceMark)).all() if m.path == str(transcript)]


def test_a_file_edited_since_refuses_the_whole_undo(tmp_path, monkeypatch):
    app, p, ws, clip, notes = _rig(tmp_path, monkeypatch)
    _route_complete(p, notes)
    with TestClient(app) as client:
        assert _invoke(p, ws, clip).ok
        (ws / "meeting notes.md").write_text("my own notes now", encoding="utf-8")
        with session_scope(p.engine) as db:
            row = next(x for x in db.exec(select(ToolInvocation)).all() if x.tool == "transcribe_recording")
        res = client.post(f"/undo/{row.id}")
        assert res.status_code == 409, res.text
    assert (ws / "meeting transcript.md").exists() and (ws / "meeting notes.md").exists()


def test_the_same_recording_again_in_the_same_folder_makes_nothing_new(tmp_path, monkeypatch):
    app, p, ws, clip, notes = _rig(tmp_path, monkeypatch)
    _route_complete(p, notes)
    with TestClient(app):
        assert _invoke(p, ws, clip).ok
        again = _invoke(p, ws, clip)
    assert again.ok and "already handed" in again.output
    assert sorted(x.name for x in ws.iterdir()) == ["meeting notes.md", "meeting transcript.md", "meeting.wav"]


def _chat(p) -> str:
    rec = ChatThreadRecord(title="Budget meeting", messages_json="[]")
    with session_scope(p.engine) as db:
        db.add(rec)
        db.commit()
        db.refresh(rec)
        return rec.id


def _messages(p, tid) -> list[dict]:
    with session_scope(p.engine) as db:
        return json.loads(db.get(ChatThreadRecord, tid).messages_json or "[]")


def test_a_long_recording_keeps_working_after_the_reply_and_comes_home_as_one_report(tmp_path, monkeypatch):
    app, p, ws, clip, notes = _rig(tmp_path, monkeypatch, inline=0.01)
    _route_complete(p, notes)
    with TestClient(app):
        tid = _chat(p)

        async def _go():
            r = await p.registry.invoke("transcribe_recording", {"path": str(clip)}, _ctx(p, ws, thread_id=tid),
                                        p.permissions, None, allowed_names={"transcribe_recording"})
            job = J.live(r.data["recording_id"])
            await job.task
            return r, job

        r, job = asyncio.run(_go())
    assert r.ok and "keeps working after your reply" in r.output and "Nothing is written yet" in r.output
    assert r.data["recording"]["status"] == "hearing"
    msgs = _messages(p, tid)
    assert len(msgs) == 1
    line = msgs[0]
    assert line["reportFrom"]["kind"] == "recording" and line["reportFrom"]["name"] == "meeting.wav"
    assert line["content"].startswith("From your recording “meeting.wav”.")
    assert "1 decision, 1 thing you owe, 1 thing they owe" in line["content"]
    assert line["documents"] == [str(ws / "meeting transcript.md"), str(ws / "meeting notes.md")]
    assert job.state["homed"] is True


def test_a_new_chats_first_turn_reports_once_the_page_links_it(tmp_path, monkeypatch):
    app, p, ws, clip, notes = _rig(tmp_path, monkeypatch, inline=0.01)
    _route_complete(p, notes)
    with TestClient(app) as client:
        async def _go():
            r = await p.registry.invoke("transcribe_recording", {"path": str(clip)},
                                        _ctx(p, ws, thread_id="", turn_id="turn-new"), p.permissions, None,
                                        allowed_names={"transcribe_recording"})
            await J.live(r.data["recording_id"]).task
            return r

        r = asyncio.run(_go())
        job = J.live(r.data["recording_id"])
        assert job.state["homed"] is False, "no chat to report to yet"
        tid = _chat(p)
        res = client.post("/chat/turns/turn-new/thread", json={"thread_id": tid})
        assert res.status_code == 200, res.text
    msgs = _messages(p, tid)
    assert len(msgs) == 1 and msgs[0]["reportFrom"]["kind"] == "recording"


def test_the_card_is_read_by_its_route_and_an_unknown_id_is_404(tmp_path, monkeypatch):
    app, p, ws, clip, notes = _rig(tmp_path, monkeypatch)
    _route_complete(p, notes)
    with TestClient(app) as client:
        r = _invoke(p, ws, clip)
        res = client.get(f"/documents/recording/{r.data['recording_id']}")
        assert res.status_code == 200 and res.json()["counts"]["decisions"] == 1
        assert client.get("/documents/recording/rec_0000000000000000").status_code == 404
        assert client.get("/documents/recording/..%2Fsecret").status_code == 404


def _interrupted(p, ws: Path, clip: Path, tid: str, *, attempts: int = 1) -> dict:
    """A job the last process was hearing when it stopped (its state says so)."""
    tr = R.Transcriber(R.THIS_PC, "the offline speech model on this PC", model="C:/fake/vosk-model")
    t, n = R.target_paths(ws.resolve(), clip.name)
    st = J.new_state(source=clip.resolve(), sha256=J.file_sha256(clip), workspace=ws.resolve(), thread_id=tid,
                     turn_id="turn-before", local_only=False, transcriber=tr, transcript=t, notes=n)
    st["attempts"] = attempts
    st["heard_s"] = 60.0
    J.save_state(p.config.home, st)
    return st


def _env(p) -> J.Env:
    return J.Env(engine=p.engine, event_bus=p.event_bus, config=p.config, router_resolver=lambda: p.router)


def test_a_recording_interrupted_by_a_restart_is_started_again_once_and_reports(tmp_path, monkeypatch):
    app, p, ws, clip, notes = _rig(tmp_path, monkeypatch)
    _route_complete(p, notes)
    with TestClient(app):
        tid = _chat(p)
        st = _interrupted(p, ws, clip, tid)
        leftover = J.jobs_dir(p.config.home) / "pieces" / "rec_x-1"
        leftover.mkdir(parents=True)
        (leftover / "piece-0000.wav").write_bytes(b"RIFF")

        async def _go():
            n = await J.reconcile_at_boot(_env(p))
            await J.live(st["id"]).task
            return n

        assert asyncio.run(_go()) == 1
    after = J.load_state(p.config.home, st["id"])
    assert after["status"] == J.STATUS_DONE and after["attempts"] == 2 and after["notes_written"] is True
    assert (ws / "meeting transcript.md").is_file() and (ws / "meeting notes.md").is_file()
    assert not leftover.exists(), "pieces a stopped process left are removed"
    msgs = _messages(p, tid)
    assert len(msgs) == 1 and msgs[0]["reportFrom"]["kind"] == "recording"


def test_a_second_interruption_ends_in_words_and_is_not_started_again(tmp_path, monkeypatch):
    app, p, ws, clip, notes = _rig(tmp_path, monkeypatch)
    _route_complete(p, notes)
    with TestClient(app):
        tid = _chat(p)
        st = _interrupted(p, ws, clip, tid, attempts=2)
        assert asyncio.run(J.reconcile_at_boot(_env(p))) == 0
    after = J.load_state(p.config.home, st["id"])
    assert after["status"] == J.STATUS_FAILED
    assert "stopped when Iron Jarvis restarted" in after["error"] and "Ask again" in after["error"]
    assert sorted(x.name for x in ws.iterdir()) == ["meeting.wav"]
    msgs = _messages(p, tid)
    assert len(msgs) == 1 and "stopped when Iron Jarvis restarted" in msgs[0]["content"]
