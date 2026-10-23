"""v1.377.0 review (IJ harness part 2, Phase 6): the review's daemon-side
pins that are not the environment, the git guard or the branch point.

* A REWOUND or BRANCHED question's "/" SKILL goes back in the box (the doer
  left it out: the saved question named the skill but not WHERE it came
  from — the folder's own harness skills or the user's — and the box needs
  both to send it again). A question now saves ``skillSource`` ("folder" |
  "yours"); the rewind preview / answer and a question branch's draft carry
  ``skill_source`` beside ``skill``. An older line with no source hands back
  no source (the screen then restores nothing rather than guess).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import sessions as S
from tests.test_build_sessions_v1367 import Script, asgi, asgi_get, messages_of, new_session, open_turn, wire
from tests.test_build_slash_at_v1373 import FOLDER_SKILL_MARK, _folder_skill


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    return folder


async def test_a_folder_skills_question_saves_where_the_skill_came_from(app, work):
    _folder_skill(work, "release-notes", FOLDER_SKILL_MARK)
    sid = new_session(app, work)["id"]
    wire(app, Script(["Notes written."]))
    status, raw = await open_turn(app, sid, {"text": "notes", "turn_id": "sk-f", "folder_skill": "release-notes"})
    assert status == 200, raw
    question = messages_of(app, sid)[0]
    assert question["skill"] == "release-notes" and question["skillSource"] == "folder"


def _lines(app, sid: str, extra: dict) -> None:
    S.append_lines(app.state.platform.engine, sid, [
        {"role": "user", "content": "first", "turnId": "t1"},
        {"role": "assistant", "content": "answer", "turnId": "t1"},
        {"role": "user", "content": "use the skill", "turnId": "t2", **extra},
        {"role": "assistant", "content": "done", "turnId": "t2"},
    ])


@pytest.mark.parametrize("source", ["folder", "yours"])
async def test_rewind_and_a_question_branch_hand_back_the_skill_and_its_source(app, work, source):
    sid = new_session(app, work)["id"]
    _lines(app, sid, {"skill": "tidy", "skillSource": source})
    status, preview = await asgi_get(app, f"/build/sessions/{sid}/rewind?to=t2")
    assert status == 200 and (preview["skill"], preview["skill_source"]) == ("tidy", source)
    status, view = await asgi(app, "POST", f"/build/sessions/{sid}/branch", {"turn_id": "t2", "at": "question"})
    assert status == 201, view
    assert (view["draft"]["skill"], view["draft"]["skill_source"]) == ("tidy", source)
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": "t2"})
    assert status == 200 and (out["skill"], out["skill_source"]) == ("tidy", source)


async def test_an_older_line_with_no_source_hands_back_none(app, work):
    sid = new_session(app, work)["id"]
    _lines(app, sid, {"skill": "tidy"})
    status, preview = await asgi_get(app, f"/build/sessions/{sid}/rewind?to=t2")
    assert status == 200 and preview["skill"] == "tidy" and preview["skill_source"] == ""
    _lines(app, sid, {"skill": "tidy", "skillSource": "elsewhere"})
    status, view = await asgi(app, "POST", f"/build/sessions/{sid}/branch", {"turn_id": "t2", "at": "question"})
    assert status == 201 and view["draft"]["skill_source"] == ""
