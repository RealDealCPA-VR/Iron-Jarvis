"""v1.420.0 (C3.2, decision D5): a follow-up turn REMEMBERS the previous
turn's tool work.

A coding session's next turn is built from the transcript's text only (role +
content, no native carrier — so a session moves between accounts and vendors,
v1.368.0). Before this change the model forgot what it had read, edited and
run one message ago and read the same files again. Now the NEWEST reply in the
history carries one deterministic note built from the steps its reply line
saved ("read a.py; edited cart.py; ran `python -m pytest` (failed)"):

* names, targets and verdicts only — never a step's OUTPUT, masked, ≤ 1,500;
* the newest previous reply only; every older one is exactly as before;
* a Compact summary standing in for older turns never repeats it;
* every other reader of ``history_of`` (the compaction's own material) keeps
  the plain rule.

Driven through the REAL build route with a scripted model.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import compact as C
from iron_jarvis.harness import sessions as S
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_build_sessions_v1367 import Script, frames_of, messages_of, new_session, open_turn, wire

@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    (folder / "trace.py").write_text("def trace():\n    return 'a -> b -> c'\n", encoding="utf-8")
    return folder


SECRET = "sk-ant-api03-THISISASECRETVALUE1234567890abcd"


def _steps() -> list[dict]:
    return [
        {"name": "read_file", "ok": True, "target": "a.py",
         "detail": {"input": '{"path": "a.py"}', "output": "OUTPUT-BODY-NEVER-SHOWN"}},
        {"name": "read_file", "ok": True, "target": "b.py"},
        {"name": "read_file", "ok": True, "target": "a.py"},
        {"name": "edit_file", "ok": True, "target": "cart.py"},
        {"name": "shell", "ok": False, "target": "python",
         "detail": {"input": json.dumps({"command": "python -m pytest -q"}), "output": "1 failed"}},
        {"name": "shell", "ok": True, "check": "finish",
         "detail": {"input": '{"command": "pytest -q --token=' + SECRET}},
        {"name": "plan_update", "ok": True},
    ]


# --------------------------------------------------------------------------- #
# the note
# --------------------------------------------------------------------------- #


def test_the_note_says_what_was_read_changed_and_run_never_an_output():
    note = S.last_work_note(_steps())
    assert note.startswith("\n\n[" + S.LAST_WORK_LEAD)
    assert "read a.py, b.py" in note, "each file once, in order"
    assert "edited cart.py" in note
    assert "ran `python -m pytest -q` (failed)" in note
    assert "(Iron Jarvis's own check)" in note
    assert "OUTPUT-BODY-NEVER-SHOWN" not in note and "1 failed" not in note, "never a step's output"
    assert SECRET not in note, "masked"
    assert "plan_update" not in note, "bookkeeping is left out"
    assert note.count("`") % 2 == 0, "a masked command keeps its closing backtick"


def test_the_note_is_bounded_and_empty_for_a_reply_with_no_tools():
    assert S.last_work_note(None) == "" and S.last_work_note([]) == ""
    assert S.last_work_note([{"name": "plan_update", "ok": True}]) == ""
    many = [{"name": "read_file", "ok": True, "target": f"file_{i:04d}_with_a_long_name.py"} for i in range(400)]
    many += [{"name": "shell", "ok": True, "detail": {"input": json.dumps({"command": f"echo {i}" * 30})}}
             for i in range(50)]
    note = S.last_work_note(many)
    assert 0 < len(note) <= S.LAST_WORK_MAX_CHARS
    assert note.endswith(".]") and "more step" in note


def test_only_the_newest_reply_carries_it_and_only_when_asked():
    msgs = [
        {"role": "user", "content": "one", "turnId": "t1"},
        {"role": "assistant", "content": "First.", "turnId": "t1", "steps": _steps()},
        {"role": "user", "content": "two", "turnId": "t2"},
        {"role": "assistant", "content": "Second.", "turnId": "t2",
         "steps": [{"name": "read_file", "ok": True, "target": "z.py"}]},
    ]
    plain = S.history_of(msgs)
    assert [m["content"] for m in plain] == ["one", "First.", "two", "Second."], "every other reader: unchanged"
    got = S.history_of(msgs, last_work=True)
    assert got[1]["content"] == "First.", "an older reply is exactly as before"
    assert got[3]["content"].startswith("Second.\n\n[") and "read z.py" in got[3]["content"]
    assert sum(S.LAST_WORK_LEAD in m["content"] for m in got) == 1


def test_a_compact_summary_never_repeats_the_note():
    msgs = []
    for i in range(1, 6):
        msgs.append({"role": "user", "content": f"q{i}", "turnId": f"t{i}"})
        msgs.append({"role": "assistant", "content": f"a{i}", "turnId": f"t{i}",
                     "steps": [{"name": "read_file", "ok": True, "target": f"f{i}.py"}]})
    rec = {"summary": "GOAL: x.\nFACTS:\nq1 and q2 were answered.", "through_turn": "t2", "covers": 4}
    row = SimpleNamespace(messages_json=json.dumps(msgs), setup_json=json.dumps({"compaction": rec}))
    history, summary = C.history_for_turn(row)
    assert [m["content"].split("\n")[0] for m in history] == ["q3", "a3", "q4", "a4", "q5", "a5"]
    assert sum(S.LAST_WORK_LEAD in m["content"] for m in history) == 1
    assert "read f5.py" in history[-1]["content"], "the newest reply carries it"
    assert S.LAST_WORK_LEAD not in summary, "the summary stands in for older turns, the note is not in it"
    # With no summary the newest reply still carries it, once.
    row2 = SimpleNamespace(messages_json=json.dumps(msgs), setup_json="{}")
    history2, summary2 = C.history_for_turn(row2)
    assert summary2 == "" and sum(S.LAST_WORK_LEAD in m["content"] for m in history2) == 1


# --------------------------------------------------------------------------- #
# through the real build route: the second turn does not read again
# --------------------------------------------------------------------------- #


def _reads(app, sid: str) -> list[ToolInvocation]:
    with session_scope(app.state.platform.engine) as db:
        rows = [r for r in db.exec(select(ToolInvocation)).all()
                if r.tool == "read_file" and r.session_id == f"build:{sid}"]
        for r in rows:
            db.expunge(r)
        return rows


async def _run(app, sid: str, text: str, turn_id: str):
    status, raw = await open_turn(app, sid, {"text": text, "turn_id": turn_id})
    assert status == 200, raw
    frames = frames_of(raw)
    assert frames and frames[-1][0] == "done", frames[-3:]
    return frames


def _told_model(script_box: dict):
    """The scripted model's SECOND turn: it reads trace.py again only when its
    history does not say it already read it (the bench's hard-explain-trace
    shape: explain a trace, then answer a follow-up about it)."""

    async def move(script):
        seen = script.calls[-1]["messages"]
        told = any(m.role == "assistant" and "read trace.py" in (m.content or "") for m in seen)
        script_box["told"] = told
        if told:
            return "It goes a -> b -> c, as I read in trace.py."
        return [ToolCall(id="r2", name="Read", arguments={"file_path": "trace.py"})]

    return move


async def _two_turns(app, work, box: dict):
    sid = new_session(app, work)["id"]
    wire(app, Script([
        [ToolCall(id="r1", name="Read", arguments={"file_path": "trace.py"})],
        "trace() returns the path a -> b -> c.",
        _told_model(box),
        "It goes a -> b -> c.",
    ]))
    await _run(app, sid, "explain the trace in trace.py", "lw-1")
    first_reads = len(_reads(app, sid))
    await _run(app, sid, "and which step comes second?", "lw-2")
    return sid, first_reads


async def test_a_second_turn_is_told_what_the_first_read_and_does_not_read_again(app, work):
    box: dict = {}
    sid, first_reads = await _two_turns(app, work, box)
    assert first_reads == 1
    assert box.get("told") is True, "the second turn's history names the file the first turn read"
    assert len(_reads(app, sid)) == 1, "no second read of trace.py"
    # The transcript the user sees is untouched: the note rides only what the
    # model is sent.
    msgs = messages_of(app, sid)
    assert all(S.LAST_WORK_LEAD not in m.get("content", "") for m in msgs)


async def test_control_without_the_note_the_second_turn_reads_again(app, work, monkeypatch):
    """Anti-vacuity: the scripted model really re-reads when it is not told."""
    monkeypatch.setattr(S, "last_work_note", lambda steps: "")
    box: dict = {}
    sid, _first = await _two_turns(app, work, box)
    assert box.get("told") is False
    assert len(_reads(app, sid)) == 2
