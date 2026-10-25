"""v1.370.0 (IJ harness Phase 7): REWIND a Build session to before a turn.

Driven through the REAL app factory (real build turns, real tools, the real
undo journal and ``POST /undo``'s own code); only the model is a scripted
double. Pinned:

* the files go back to the state before the chosen turn, newest change
  first, through the undo journal — a file the turns CREATED is removed, a
  file they changed gets its earlier bytes back, a chain of edits to one file
  across turns (a text-mode write, then a raw edit) composes;
* the conversation is cut back to before that turn's question (the daemon
  writes it), and LATER turns are rewound too;
* a file changed since is NAMED and NOTHING is touched (files and
  conversation both) — never a silent partial rewind; a revert that fails
  part way says plainly what went back and leaves the conversation;
* refused while a turn runs; commands are reported as not undone; an
  approved plan from a rewound turn goes and plan mode comes back.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation, UndoJournal
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as build_routes
from iron_jarvis.harness import rewind as R
from iron_jarvis.harness import sessions as S
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_build_sessions_v1367 import (
    Script,
    asgi,
    frames_of,
    messages_of,
    new_session,
    open_turn,
    setup_of,
    thread_row,
    wire,
)


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_bytes(b"x = 1\n")
    (folder / "keep.txt").write_bytes(b"untouched\n")
    return folder


async def _turn(app, sid: str, text: str, turn_id: str) -> list:
    status, raw = await open_turn(app, sid, {"text": text, "turn_id": turn_id})
    assert status == 200, raw
    frames = frames_of(raw)
    assert frames and frames[-1][0] == "done", frames[-3:]
    return frames


def _two_turns(app, work):
    """Turn 1 creates hello.py and edits app.py (x = 1 -> 2); turn 2 edits
    app.py again (2 -> 3), creates notes.md and runs a command.

    Part 3, Phase 2: a reply that changed files gets ONE more model round —
    its finish check — so turn one's script answers it ("Checked: …") before
    turn two's moves begin (turn two's is the script's own "Done.")."""
    return wire(app, Script([
        [ToolCall(id="a1", name="Write", arguments={"file_path": "hello.py", "content": "print(1)\n"}),
         ToolCall(id="a2", name="Edit", arguments={
             "file_path": "app.py", "old_string": "x = 1", "new_string": "x = 2"})],
        "Turn one done.",
        "Checked: nothing needed changing.",
        [ToolCall(id="b1", name="Edit", arguments={
            "file_path": "app.py", "old_string": "x = 2", "new_string": "x = 3"}),
         ToolCall(id="b2", name="Write", arguments={"file_path": "notes.md", "content": "# notes\n"}),
         ToolCall(id="b3", name="Bash", arguments={"command": "echo rewind-me"})],
        "Turn two done.",
    ]))


def _ledger(app) -> list[ToolInvocation]:
    with session_scope(app.state.platform.engine) as db:
        rows = list(db.exec(select(ToolInvocation)).all())
        for r in rows:
            db.expunge(r)
        return rows


async def _session_with_two_turns(app, work) -> str:
    sid = new_session(app, work, approval_mode="yolo")["id"]
    _two_turns(app, work)
    await _turn(app, sid, "make hello and bump x", "t1")
    await _turn(app, sid, "bump x again and take notes", "t2")
    assert (work / "app.py").read_bytes() == b"x = 3\n"
    assert (work / "hello.py").exists() and (work / "notes.md").exists()
    return sid


# --------------------------------------------------------------------------- #
# the rewind itself
# --------------------------------------------------------------------------- #


async def test_rewinding_to_the_first_turn_puts_every_file_back_and_cuts_the_conversation(app, work):
    sid = await _session_with_two_turns(app, work)
    status, points = await asgi(app, "GET", f"/build/sessions/{sid}/rewind")
    assert status == 200
    assert [(p["turn_id"], p["files"], p["commands"]) for p in points["checkpoints"]] == [
        ("t1", 2, 0), ("t2", 2, 1),
    ]
    status, preview = await asgi(app, "GET", f"/build/sessions/{sid}/rewind?to=t1")
    assert status == 200 and preview["can_rewind"] is True and preview["problems"] == []
    assert {(f["rel"], f["change"]) for f in preview["files"]} == {
        ("app.py", "put back"), ("hello.py", "removed"), ("notes.md", "removed"),
    }
    assert [c["what"] for c in preview["commands"]] == ["echo rewind-me"]
    # The preview touched nothing.
    assert (work / "app.py").read_bytes() == b"x = 3\n" and len(messages_of(app, sid)) == 4

    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": "t1"})
    assert status == 200, out
    assert out["rewound"] is True and out["turns"] == ["t1", "t2"]
    assert out["question"] == "make hello and bump x"
    assert "echo rewind-me" in out["note"] and "not undone" in out["note"]
    # Every file is as it was before turn one; the rest of the folder is not touched.
    assert (work / "app.py").read_bytes() == b"x = 1\n"
    assert not (work / "hello.py").exists() and not (work / "notes.md").exists()
    assert (work / "keep.txt").read_bytes() == b"untouched\n"
    # The conversation is cut back to before turn one's question.
    assert messages_of(app, sid) == []
    assert thread_row(app, sid).title == S.NEW_SESSION_TITLE
    # Each change went back through the undo journal, as its own ledger row.
    rows = _ledger(app)
    undone = {r.id for r in rows if r.undone_at is not None}
    undos = [r for r in rows if r.undo_of]
    assert len(undos) == 4 and {r.undo_of for r in undos} == undone
    assert all(r.session_id == f"build:{sid}" for r in undos)
    with session_scope(app.state.platform.engine) as db:
        assert all(j.applied_at is not None for j in db.exec(select(UndoJournal)).all())


async def test_rewinding_to_a_later_turn_keeps_the_earlier_one(app, work):
    sid = await _session_with_two_turns(app, work)
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": "t2"})
    assert status == 200, out
    assert out["turns"] == ["t2"]
    # Turn one's changes stay; turn two's are gone.
    assert (work / "app.py").read_bytes() == b"x = 2\n"
    assert (work / "hello.py").read_bytes().replace(b"\r\n", b"\n") == b"print(1)\n"
    assert not (work / "notes.md").exists()
    msgs = messages_of(app, sid)
    assert [(m["role"], m["turnId"]) for m in msgs] == [("user", "t1"), ("assistant", "t1")]
    # The session goes on from there: the next turn's history is turn one only.
    seen: list = []
    real = build_routes.stream_chat_turn

    async def spy(platform, personas, body, **kw):
        seen.append(body)
        return await real(platform, personas, body, **kw)

    build_routes.stream_chat_turn, saved = spy, build_routes.stream_chat_turn
    try:
        wire(app, Script(["After the rewind."]))
        await _turn(app, sid, "carry on", "t3")
    finally:
        build_routes.stream_chat_turn = saved
    # (Turn one's reply line keeps its finish check's answer after its words —
    # part 3, Phase 2.)
    assert [(m.role, m.content) for m in seen[0].messages] == [
        ("user", "make hello and bump x"),
        ("assistant", "Turn one done.\n\nChecked: nothing needed changing."),
        ("user", "carry on"),
    ]


async def test_a_text_write_then_a_raw_edit_of_one_file_rewind_together(app, work):
    """A chain across two turns with two hash modes: write_file journals
    TEXT mode (newline-invariant), edit_file RAW bytes. The plan's simulation
    must judge each revert in its own mode, over the bytes the newer revert
    leaves — else a clean chain reads as "changed since"."""
    (work / "data.txt").write_bytes(b"one\ntwo\n")
    sid = new_session(app, work)["id"]
    wire(app, Script([
        [ToolCall(id="w", name="Write", arguments={"file_path": "data.txt", "content": "alpha\nbeta\n"})],
        "Rewrote it.",
        [ToolCall(id="e", name="Edit", arguments={
            "file_path": "data.txt", "old_string": "beta", "new_string": "gamma"})],
        "Edited it.",
    ]))
    await _turn(app, sid, "rewrite", "w1")
    await _turn(app, sid, "edit", "w2")
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": "w1"})
    assert status == 200, out
    # EXACTLY the bytes it had (v1.370.0: write_file keeps its prior copy
    # exact — the old text-mode copy came back with the OS's line endings).
    assert (work / "data.txt").read_bytes() == b"one\ntwo\n"


async def test_a_raw_edit_then_a_text_write_of_one_file_rewind_together(app, work):
    """The other order: an EDIT (raw journal) in turn one, then a WRITE over
    the same file in turn two. Undoing the write must leave the bytes the
    edit left, exactly — else the edit's own "changed since" check refused
    the rewind of a file nobody else had touched."""
    (work / "lf.txt").write_bytes(b"alpha\nbeta\n")
    sid = new_session(app, work)["id"]
    wire(app, Script([
        [ToolCall(id="e", name="Edit", arguments={
            "file_path": "lf.txt", "old_string": "beta", "new_string": "gamma"})],
        "Edited it.",
        [ToolCall(id="w", name="Write", arguments={"file_path": "lf.txt", "content": "replaced\n"})],
        "Rewrote it.",
    ]))
    await _turn(app, sid, "edit", "x1")
    await _turn(app, sid, "rewrite", "x2")
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": "x1"})
    assert status == 200, out
    assert (work / "lf.txt").read_bytes() == b"alpha\nbeta\n"


# --------------------------------------------------------------------------- #
# never a silent partial rewind
# --------------------------------------------------------------------------- #


async def test_a_file_changed_since_is_named_and_nothing_is_touched(app, work):
    sid = await _session_with_two_turns(app, work)
    (work / "app.py").write_bytes(b"x = 3\n# my own edit\n")
    before_msgs = messages_of(app, sid)
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": "t1"})
    assert status == 409, out
    assert out["rewound"] is False and out["detail"].startswith("Nothing was rewound")
    assert "app.py" in out["detail"] and "changed after this session changed it" in out["detail"]
    assert [p["path"].replace("\\", "/").rsplit("/", 1)[-1] for p in out["problems"]] == ["app.py"]
    # NOTHING moved: not the files that could have gone back, not the conversation.
    assert (work / "app.py").read_bytes() == b"x = 3\n# my own edit\n"
    assert (work / "hello.py").exists() and (work / "notes.md").exists()
    assert messages_of(app, sid) == before_msgs
    assert not any(r.undo_of for r in _ledger(app))


async def test_a_revert_that_fails_part_way_says_what_went_back_and_keeps_the_conversation(app, work, monkeypatch):
    sid = await _session_with_two_turns(app, work)
    from fastapi import HTTPException

    from iron_jarvis.daemon.routes import undo as undo_routes

    real = undo_routes.perform_undo
    calls = {"n": 0}

    async def flaky(d, action_id):
        calls["n"] += 1
        if calls["n"] == 2:
            raise HTTPException(status_code=409, detail="target changed since the action")
        return await real(d, action_id)

    monkeypatch.setattr(undo_routes, "perform_undo", flaky)
    before_msgs = messages_of(app, sid)
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": "t1"})
    assert status == 409, out
    assert out["rewound"] is False and out["partial"] is True
    assert out["detail"].startswith("Rewinding stopped part way")
    assert "The conversation was not changed." in out["detail"]
    assert out["restored"] and out["not_restored"]
    assert messages_of(app, sid) == before_msgs


async def test_a_change_with_no_saved_copy_refuses_the_rewind(app, work):
    sid = await _session_with_two_turns(app, work)
    with session_scope(app.state.platform.engine) as db:
        edit = next(r for r in db.exec(select(ToolInvocation)).all()
                    if r.tool == "edit_file" and '"x = 2"' in r.args_json and '"x = 3"' in r.args_json)
        db.delete(db.get(UndoJournal, edit.id))
        db.commit()
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": "t1"})
    assert status == 409 and "no copy was kept" in out["detail"], out
    assert (work / "app.py").read_bytes() == b"x = 3\n"


async def test_a_session_still_answering_is_not_rewound(app, work):
    sid = await _session_with_two_turns(app, work)
    build_routes._STARTING.add(sid)
    try:
        status, out = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": "t1"})
    finally:
        build_routes._STARTING.discard(sid)
    assert status == 409 and out["detail"] == build_routes.REWIND_RUNNING
    assert (work / "app.py").read_bytes() == b"x = 3\n"


async def test_a_rewind_holds_the_session_so_no_turn_starts_during_it(app, work, monkeypatch):
    import threading

    from fastapi import HTTPException

    sid = await _session_with_two_turns(app, work)
    started, release = threading.Event(), threading.Event()

    def slow_plan(*a, **k):
        started.set()
        release.wait(10)
        raise R.RewindError("stopped for the test", 409)

    monkeypatch.setattr(R, "plan", slow_plan)
    task = asyncio.create_task(build_routes.rewind_session(app.state.d, sid, "t1"))
    for _ in range(500):
        if started.is_set():
            break
        await asyncio.sleep(0.01)
    assert started.is_set() and sid in build_routes._STARTING
    with pytest.raises(build_routes.SessionBusy):
        await build_routes.start_build_turn(app.state.d, sid, "x", "t9")
    release.set()
    with pytest.raises(HTTPException):
        await asyncio.wait_for(task, 10)
    assert sid not in build_routes._STARTING


async def test_an_unknown_turn_is_a_404_in_words(app, work):
    sid = await _session_with_two_turns(app, work)
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": "nope"})
    assert status == 404 and out["detail"] == R.NOT_A_TURN
    status, out = await asgi(app, "POST", "/build/sessions/nosuch/rewind", {"to": "t1"})
    assert status == 404


# --------------------------------------------------------------------------- #
# the session's own state follows the cut
# --------------------------------------------------------------------------- #


async def test_a_plan_approved_in_a_rewound_turn_goes_and_plan_mode_comes_back(app, work):
    sid = await _session_with_two_turns(app, work)
    msgs = messages_of(app, sid)
    t2_at = next(m["at"] for m in msgs if m.get("turnId") == "t2" and m["role"] == "user")
    S.update(app.state.platform.engine, sid, setup={
        "plan": {"text": "the plan", "steps": [], "approved_at": t2_at},
        "account_limit": {"account": "acc-1", "kind": "limit"},
    })
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": "t2"})
    assert status == 200, out
    setup = setup_of(app, sid)
    assert setup.get("mode") == "plan" and "plan" not in setup and "account_limit" not in setup
    assert out["mode"] == "plan"


async def test_a_plan_approved_before_the_point_stays(app, work):
    sid = await _session_with_two_turns(app, work)
    msgs = messages_of(app, sid)
    t1_at = next(m["at"] for m in msgs if m.get("turnId") == "t1" and m["role"] == "user")
    S.update(app.state.platform.engine, sid, setup={
        "plan": {"text": "the plan", "steps": [], "approved_at": t1_at},
    })
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": "t2"})
    assert status == 200, out
    setup = setup_of(app, sid)
    assert setup.get("plan", {}).get("text") == "the plan" and setup.get("mode") != "plan"


def test_the_cut_refuses_when_the_conversation_moved(tmp_path):
    app = create_app(str(tmp_path / "home"))
    work = tmp_path / "w"
    work.mkdir()
    sid = new_session(app, work)["id"]
    eng = app.state.platform.engine
    S.append_lines(eng, sid, [{"role": "user", "content": "q", "turnId": "a"},
                              {"role": "assistant", "content": "r", "turnId": "a"}])
    out = S.cut_transcript(eng, sid, "b", 0, "2026-01-01T00:00:00+00:00")
    assert out["ok"] is False and "changed" in out["reason"]
    assert len(json.loads(thread_row(app, sid).messages_json)) == 2


async def test_a_rewind_drops_the_library_proposals_of_the_turns_it_rewound(app, work):
    """The experience library's half: a PROPOSAL made from a rewound turn
    describes work that is no longer there, so it goes; an ACCEPTED lesson
    was the user's decision and stays; a proposal from a kept turn stays."""
    from iron_jarvis.harness import library as L
    from iron_jarvis.harness.library_models import HarnessLibraryRecord

    sid = await _session_with_two_turns(app, work)
    eng = app.state.platform.engine
    key = L.folder_key(work)

    def row(turn, status, text):
        return L._mint(eng, HarnessLibraryRecord(folder_key=key, folder=str(work), kind="lesson", text=text,
                                                 status=status, signature=text, thread_id=sid, turn_id=turn))

    gone = row("t2", "proposed", "from turn two, waiting")
    kept = row("t2", "accepted", "from turn two, kept by the user")
    other = row("t1", "proposed", "from turn one, waiting")
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": "t2"})
    assert status == 200, out
    assert out["library_dropped"] == 1
    assert L.get(eng, gone.id) is None
    assert L.get(eng, kept.id) is not None and L.get(eng, other.id) is not None
