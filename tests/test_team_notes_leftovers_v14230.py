"""v1.423.0 (Wave 3 lane E) — two leftovers of the round-2 reviews of Team
notes, each pinned RED on the round-2 code first.

4. A ROLE-BRIEF SUGGESTION IGNORED THE LIBRARY'S OWN SWITCH. A habit two
   members of one role were given becomes a library ``layer_edit`` of
   ``sub_agents/<role>.md`` (``team_notes.role_edit_for`` →
   ``library.propose_role_brief_edit``) — and it was minted with
   ``build_reflect`` ("learn from Build sessions") OFF. The owner's library
   switch off means NO library suggestion of any kind: the library's own
   function now refuses (``REASON_OFF``), whoever calls it.
5. TEAM-NOTES.md WAS OVERWRITTEN BY A SAVE THAT LANDED MID-RENDER.
   ``team_notes.render`` checked for a hand edit, then wrote — with no
   re-read right before the write, so a save the user made in between (an
   editor open on the file) was replaced by Iron Jarvis's text. The file is
   now read AGAIN right before the replace (the ``profile/share.
   _atomic_write(expect=)`` pattern) and the write is abandoned when it
   changed: the user's file is kept and said like any hand edit.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents import team_notes as TN
from iron_jarvis.coach.models import CoachProposalRecord
from iron_jarvis.platform import build_platform
from tests.test_team_notes_v14230 import (
    HABIT,
    WORKER,
    FakeRouter,
    _accepted,
    _add,
    _app,
    _local_ollama,
    _members,
    _seed,
)

OTHER = "Keep a test beside every new function"
USERS = "# Working habits\n\n- my own line, saved while Iron Jarvis was writing\n"


@pytest.fixture
def plat(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    _local_ollama(p)
    _members(p)
    p.router = FakeRouter()
    return p


# --------------------------------------------------------------------------- #
# 4. the library's own switch
# --------------------------------------------------------------------------- #


async def test_with_learning_from_build_sessions_off_no_role_brief_is_suggested(tmp_path):
    """Team notes ON, the library's switch OFF: the habit is accepted for its
    member, and NO edit of the role's brief is proposed (it would be shown
    to every helper of the role) — through the real Accept route."""
    from iron_jarvis.harness import library

    app, p = _app(tmp_path)
    p.agents_registry.register("kimi", "You are kimi.", [], provider="ollama", model="kimi", roles=["code"])
    _seed(p, tmp_path)
    _accepted(p, "kimi", "Run the tests before reporting a piece as done")
    (row,) = (await TN.distill(p, WORKER)).minted
    assert p.config.team_notes is not False
    p.config.build_reflect = False
    r = TestClient(app).post(f"/coach/proposals/{row.id}/accept")
    assert r.status_code == 200, r.text
    assert [n.text for n in TN.active_notes(p.engine, WORKER)] == [HABIT]
    for i in range(2):
        assert library.view(p.engine, str(tmp_path / f"ws{i}"))["proposals"] == []


def test_the_librarys_own_function_refuses_with_its_switch_off(tmp_path):
    from iron_jarvis.harness import library
    from iron_jarvis.harness import sessions as S

    _app_, p = _app(tmp_path)
    folder = tmp_path / "proj"
    folder.mkdir()
    thread = S.create(p.engine, str(folder), "s").id
    kw = dict(thread_id=thread, role="code", line="Run the tests before reporting", why="two coders",
              evidence={})
    p.config.build_reflect = False
    assert library.propose_role_brief_edit(p, **kw) == (None, library.REASON_OFF)
    assert library.view(p.engine, str(folder))["proposals"] == []
    # The control: on, the same call proposes the edit.
    p.config.build_reflect = True
    row, why = library.propose_role_brief_edit(p, **kw)
    assert row is not None and why == "", why


# --------------------------------------------------------------------------- #
# 5. a save that lands mid-render is never overwritten
# --------------------------------------------------------------------------- #


def _second(p) -> CoachProposalRecord:
    return _add(p, CoachProposalRecord(agent=f"custom:{WORKER}", kind="team_note", target=WORKER, after=OTHER))


def test_a_save_landing_while_iron_jarvis_rewrites_its_own_file_is_kept(plat, monkeypatch):
    """TEAM-NOTES.md holds exactly what Iron Jarvis last wrote; a second
    habit is accepted; the user saves the file AFTER the hand-edit check
    (here: while the previous text's revision copy is written) and BEFORE
    the replace. The user's save wins: kept, said, and Iron Jarvis's
    last-written text is unchanged (so it reads as a hand edit)."""
    _accepted(plat)
    path = TN.notes_path(plat, WORKER)
    ours = path.read_bytes()
    sha_before = TN._state(plat.engine, WORKER).written_sha
    real = TN._revision

    def user_saves_meanwhile(p, previous):
        real(p, previous)
        path.write_text(USERS, encoding="utf-8")

    monkeypatch.setattr(TN, "_revision", user_saves_meanwhile)
    out = TN.accept(plat, _second(plat).id)
    assert out.rendered is not None and out.rendered.state == "kept"
    assert TN.KEPT_SENTENCE in out.note
    assert path.read_text(encoding="utf-8").replace("\r\n", "\n") == USERS
    assert path.read_bytes() != ours
    assert TN._state(plat.engine, WORKER).written_sha == sha_before
    assert TN.hand_edited(plat, WORKER) is True
    # Both habits are in force; the file stays the user's on the next render.
    assert {n.text for n in TN.active_notes(plat.engine, WORKER)} == {HABIT, OTHER}
    monkeypatch.undo()
    assert TN.render(plat, WORKER).state == "kept"
    assert path.read_text(encoding="utf-8").replace("\r\n", "\n") == USERS
    # No temp file is left behind.
    assert [f.name for f in path.parent.iterdir() if f.name.endswith(".tmp")] == []


def test_a_file_the_user_creates_while_iron_jarvis_writes_the_first_one_is_kept(plat, monkeypatch):
    """No TEAM-NOTES.md yet: the user creates one between the decision and
    the replace — kept, never written over."""
    path = TN.notes_path(plat, WORKER)
    assert not path.exists()
    real = TN._write_temp

    def user_creates_meanwhile(p, text):
        tmp = real(p, text)
        path.write_text(USERS, encoding="utf-8")
        return tmp

    monkeypatch.setattr(TN, "_write_temp", user_creates_meanwhile, raising=False)
    row = _add(plat, CoachProposalRecord(agent=f"custom:{WORKER}", kind="team_note", target=WORKER, after=HABIT))
    out = TN.accept(plat, row.id)
    assert path.read_text(encoding="utf-8").replace("\r\n", "\n") == USERS
    assert out.rendered is not None and out.rendered.state == "kept"
    assert TN._state(plat.engine, WORKER).written_sha == ""
    assert [f.name for f in path.parent.iterdir() if f.name.endswith(".tmp")] == []


def test_with_no_save_in_between_the_file_is_written_as_before(plat):
    """The control: nothing changed meanwhile — written, and Iron Jarvis's
    text recorded."""
    _accepted(plat)
    out = TN.accept(plat, _second(plat).id)
    assert out.rendered is not None and out.rendered.state == "written"
    text = TN.notes_path(plat, WORKER).read_text(encoding="utf-8")
    assert f"- {HABIT}" in text and f"- {OTHER}" in text
    assert TN.hand_edited(plat, WORKER) is False


def test_an_unreadable_file_is_kept(plat, monkeypatch):
    """A TEAM-NOTES.md that is there but cannot be opened is the user's —
    never written over."""
    _accepted(plat)
    path = TN.notes_path(plat, WORKER)
    real = TN._raw

    def locked(p):
        if p == path:
            raise PermissionError("in use by another program")
        return real(p)

    monkeypatch.setattr(TN, "_raw", locked)
    before = path.read_bytes()
    assert TN.render(plat, WORKER).state == "kept"
    assert path.read_bytes() == before
