"""v1.370.0 review (IJ harness Phase 7): REWIND, the edges the first cut missed.

* THE TURN BOUNDARY on a coarse clock: the previous turn's last tool row can
  carry the very tick of the next question. It is the previous turn's work
  (it was recorded before that turn's reply line) — rewinding to the next
  turn must keep it, and the checkpoints count it where it belongs.
* A FILE THE SESSION MADE AND THE USER DELETED is already as it was before
  the turn: the rewind goes ahead (it used to refuse the whole rewind as
  "changed since") and says so.
* A REVERT THE PERMISSIONS WOULD REFUSE is named BEFORE anything is touched
  (it used to stop part way, some files back and some not).
* ONLY THE SESSION'S OWN WORK: a chat's write and another Build session's
  write to the same folder, inside the rewound window, are left alone.
* An account switch: the rewind keeps the session's account (it clears only
  the limit card and the wait).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ChatThreadRecord, ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import rewind as R
from tests.test_build_sessions_v1367 import asgi, messages_of, setup_of
from tests.test_harness_rewind_v1370 import _ledger, _session_with_two_turns


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


def _naive(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


async def test_a_previous_turns_row_with_the_next_questions_tick_is_kept(app, work):
    sid = await _session_with_two_turns(app, work)
    eng = app.state.platform.engine
    q2 = next(m for m in messages_of(app, sid) if m["turnId"] == "t2" and m["role"] == "user")
    with session_scope(eng) as db:
        # Turn one's app.py edit (x = 1 -> 2) and its reply line on t2's tick.
        edit = next(r for r in db.exec(select(ToolInvocation)).all()
                    if r.tool == "edit_file" and '"x = 1"' in r.args_json)
        edit.created_at = _naive(q2["at"])
        db.add(edit)
        row = db.get(ChatThreadRecord, sid)
        lines = json.loads(row.messages_json)
        for m in lines:
            if m["turnId"] == "t1" and m["role"] == "assistant":
                m["at"] = q2["at"]
        row.messages_json = json.dumps(lines)
        db.add(row)
        db.commit()
    status, points = await asgi(app, "GET", f"/build/sessions/{sid}/rewind")
    assert [(p["turn_id"], p["files"]) for p in points["checkpoints"]] == [("t1", 2), ("t2", 2)]
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": "t2"})
    assert status == 200, out
    # Turn one's change stands; only turn two's went back.
    assert (work / "app.py").read_bytes() == b"x = 2\n"
    assert out["turns"] == ["t2"]


async def test_a_file_the_session_made_and_the_user_deleted_does_not_stop_the_rewind(app, work):
    sid = await _session_with_two_turns(app, work)
    (work / "notes.md").unlink()  # turn two made it; the user deleted it
    status, preview = await asgi(app, "GET", f"/build/sessions/{sid}/rewind?to=t2")
    assert status == 200 and preview["can_rewind"] is True, preview
    assert ("notes.md", R.ALREADY_GONE) in {(f["rel"], f["change"]) for f in preview["files"]}
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": "t2"})
    assert status == 200, out
    assert (work / "app.py").read_bytes() == b"x = 2\n" and not (work / "notes.md").exists()
    assert [m["turnId"] for m in messages_of(app, sid)] == ["t1", "t1"]


async def test_a_revert_the_permissions_refuse_is_named_before_anything_moves(app, work, monkeypatch):
    sid = await _session_with_two_turns(app, work)
    monkeypatch.setitem(app.state.platform.permissions._base, "edit_file", "deny")
    before = messages_of(app, sid)
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": "t1"})
    assert status == 409, out
    assert out["detail"].startswith("Nothing was rewound") and "edit_file" in out["detail"], out
    assert out.get("partial") is not True
    # NOTHING moved: not the files the other tools could put back, not the talk.
    assert (work / "app.py").read_bytes() == b"x = 3\n"
    assert (work / "hello.py").exists() and (work / "notes.md").exists()
    assert messages_of(app, sid) == before
    assert not any(r.undo_of for r in _ledger(app))


async def test_only_the_sessions_own_writes_are_rewound(app, work):
    from iron_jarvis.tools.base import ToolContext

    sid = await _session_with_two_turns(app, work)
    platform = app.state.platform

    async def write(session_id: str, name: str) -> None:
        ctx = ToolContext(workspace=work, session_id=session_id, agent_run_id=session_id,
                          config=platform.config, event_bus=platform.event_bus, engine=platform.engine)
        res = await platform.registry.invoke("write_file", {"path": name, "content": "theirs\n"}, ctx,
                                             platform.permissions, session_allow=["write_file"])
        assert res.ok, res.error

    # Inside the rewound window, in the same folder: a chat's write and
    # another Build session's write.
    await write("chat", "from_chat.txt")
    await write("build:another", "from_other.txt")
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": "t1"})
    assert status == 200, out
    assert (work / "from_chat.txt").exists() and (work / "from_other.txt").exists()
    assert {f["rel"] for f in out["files"]} == {"app.py", "hello.py", "notes.md"}


async def test_a_rewind_after_an_account_switch_keeps_the_account(app, work):
    from iron_jarvis.harness import sessions as S

    sid = await _session_with_two_turns(app, work)
    S.update(app.state.platform.engine, sid, setup={
        "account": "acc-2", "account_name": "Personal",
        "account_limit": {"account": "acc-1", "kind": "usage"},
    })
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": "t2"})
    assert status == 200, out
    setup = setup_of(app, sid)
    assert setup["account"] == "acc-2" and setup["account_name"] == "Personal"
    assert "account_limit" not in setup
