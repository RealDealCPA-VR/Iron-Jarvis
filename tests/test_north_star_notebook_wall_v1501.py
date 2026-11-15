"""North-star fix 1.3 (v1.501.0): an agent's notebook is per folder.

A custom agent's NOTES.md is per AGENT, and the runtime told the whole of it
at the top of every run: a note the agent took while working in Hartleys'
folder ("Hartleys file on the cash basis") rode its next job in Smith's
folder. Now a note written during work in a folder carries that folder (a
marker at the end of its first line, the wall's own folder key), and the
notebook block a run is told keeps only the notes THE CLIENT WALL
(``workmap.clients.wall.may_tell``) lets that run hear. A note with no
folder (written outside any folder, or by the owner on the agent's page) is
told as before. A wall that cannot be read tells no note that has a folder.

Driven through the real notebook tool (append and replace) and the runtime's
own seam (``AgentRuntime._notebook_block``); only the client RECORD is a
made-up map read through ``store.load_map``. Names and folders are made up.
"""

from __future__ import annotations

import pytest

from iron_jarvis.agents.notebook_tool import NotebookTool
from iron_jarvis.agents.runtime import AgentRuntime
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Session
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.workmap import keys
from iron_jarvis.workmap.clients import ClientRoot, TurnWall, names, store, wall

H_ID = "client_00000000000a"  # Hartleys
S_ID = "client_00000000000b"  # Smith
H = r"D:\Clients\Hartleys"
S = r"D:\Clients\Smith"
DOWNLOADS = r"C:\Users\Owner\Downloads"
NAME = "scout"

H_NOTE = "HARTLEYS-NOTE the bank file is on the cash basis"
S_NOTE = "SMITH-NOTE the payroll is run on Fridays"
FREE_NOTE = "FREE-NOTE always say which folder a file was saved in"


def _k(folder: str) -> str:
    return keys.folder_key(keys.home_folder(folder))


def _map() -> store.ClientMap:
    confirmed = [(H_ID, "Hartleys", [H]), (S_ID, "Smith", [S])]
    roots = tuple(ClientRoot(cid, name, _k(f), f) for cid, name, folders in confirmed for f in folders)
    return store.ClientMap(
        version=1, confirmed=roots, waiting=(),
        names={cid: (name, names.signature(name), ()) for cid, name, _f in confirmed},
        waiting_names={},
    )


@pytest.fixture()
def platform(tmp_path, monkeypatch):
    p = build_platform(str(tmp_path / "home"))
    cmap = _map()
    monkeypatch.setattr(store, "load_map", lambda engine: cmap)
    p.agents_registry.register(NAME, "You are Scout.", ["read_file"])
    return p


def _sid(p) -> str:
    with session_scope(p.engine) as db:
        row = Session(task="seeded", agent_name=f"custom:{NAME}")
        db.add(row)
        db.commit()
        return row.id


def _turn(p, folder: str, words: str = "") -> TurnWall:
    return wall.turn_wall(p.engine, folder, words)


async def _note(p, tmp_path, text: str, turn: TurnWall, action: str = "append"):
    ctx = ToolContext(workspace=tmp_path, session_id=_sid(p), agent_run_id="r",
                      config=p.config, event_bus=p.event_bus, engine=p.engine)
    with wall.turn_scope(turn):
        res = await NotebookTool(p).execute({"action": action, "text": text}, ctx)
    assert res.ok, res.error
    return res


def _block(p, turn: TurnWall) -> str:
    with wall.turn_scope(turn):
        return AgentRuntime(p)._notebook_block(NAME, "sess-x")


@pytest.fixture()
async def noted(platform, tmp_path):
    await _note(platform, tmp_path, H_NOTE, _turn(platform, H + r"\2026"))
    await _note(platform, tmp_path, S_NOTE, _turn(platform, S))
    await _note(platform, tmp_path, FREE_NOTE, TurnWall())
    return platform


async def test_a_note_from_one_clients_folder_is_not_told_in_anothers(noted):
    p = noted
    in_h = _block(p, _turn(p, H))
    assert H_NOTE in in_h and S_NOTE not in in_h and FREE_NOTE in in_h
    in_s = _block(p, _turn(p, S))
    assert S_NOTE in in_s and H_NOTE not in in_s and FREE_NOTE in in_s
    plain = _block(p, _turn(p, DOWNLOADS, "tidy the downloads"))
    assert H_NOTE not in plain and S_NOTE not in plain and FREE_NOTE in plain
    named = _block(p, _turn(p, DOWNLOADS, "the Hartleys letter"))
    assert H_NOTE in named and S_NOTE not in named
    # The folder marker is the file's, never the prompt's.
    assert "<!--" not in in_h and "d:/clients" not in in_h.lower()


async def test_the_file_keeps_every_note_and_says_where_each_was_written(noted):
    raw = noted.agents_registry.files.read_notes(NAME)
    for note in (H_NOTE, S_NOTE, FREE_NOTE):
        assert note in raw
    line = next(ln for ln in raw.splitlines() if H_NOTE in ln)
    assert _k(H + r"\2026") in line


async def test_a_wall_that_cannot_be_read_tells_no_note_with_a_folder(noted, monkeypatch):
    p = noted
    closed = _block(p, TurnWall(ok=False, folder_key=_k(S)))
    assert S_NOTE in closed and H_NOTE not in closed and FREE_NOTE in closed

    def unreadable(engine):
        raise RuntimeError("the client tables cannot be read")

    monkeypatch.setattr(store, "load_map", unreadable)
    got = _block(p, TurnWall())
    assert H_NOTE not in got and S_NOTE not in got and FREE_NOTE in got


async def test_a_replace_written_in_a_clients_folder_is_kept_to_it(platform, tmp_path):
    await _note(platform, tmp_path, FREE_NOTE, TurnWall())
    files = platform.agents_registry.files
    kept = files.read_notes(NAME)
    await _note(platform, tmp_path, kept + "- " + H_NOTE + "\n", _turn(platform, H), action="replace")
    assert H_NOTE in _block(platform, _turn(platform, H))
    in_s = _block(platform, _turn(platform, S))
    assert H_NOTE not in in_s and FREE_NOTE in in_s  # the unchanged line stays free


async def test_the_read_action_shows_only_what_the_run_may_be_told(noted, tmp_path):
    """The tool's ``read`` returned the raw file, so a run in Smith's folder
    could read Hartleys' notes by asking for its notebook."""
    p = noted
    got = (await _note(p, tmp_path, "", _turn(p, S), action="read")).output
    assert S_NOTE in got and FREE_NOTE in got and H_NOTE not in got
    assert "<!--" not in got


async def test_a_replace_keeps_the_notes_the_run_was_not_shown(noted, tmp_path):
    """A run rewrites the notebook it was SHOWN. The notes the wall kept from
    it (another client's) must survive the replace, word for word."""
    p = noted
    files = p.agents_registry.files
    h_line = next(ln for ln in files.read_notes(NAME).splitlines() if H_NOTE in ln)
    await _note(p, tmp_path, "- SMITH-NEW the only note now\n", _turn(p, S), action="replace")
    raw = files.read_notes(NAME)
    assert h_line in raw.splitlines()  # Hartleys' note, marker and all
    assert S_NOTE not in raw and "SMITH-NEW" in raw  # what the run saw, it replaced
    assert H_NOTE in _block(p, _turn(p, H)) and "SMITH-NEW" not in _block(p, _turn(p, H))


async def test_a_note_kept_as_shown_keeps_its_own_folder(noted, tmp_path):
    p = noted
    files = p.agents_registry.files
    before = files.read_notes(NAME)
    shown = (await _note(p, tmp_path, "", _turn(p, S), action="read")).output
    # Rewritten word for word from another of Smith's folders: nothing moves.
    await _note(p, tmp_path, shown, _turn(p, S + r"\Payroll"), action="replace")
    after = files.read_notes(NAME)
    assert sorted(ln for ln in after.splitlines() if ln) == sorted(ln for ln in before.splitlines() if ln)


async def test_a_multi_line_note_is_kept_whole_to_its_folder(platform, tmp_path):
    await _note(platform, tmp_path, "HARTLEYS-HEAD the K-1s\nHARTLEYS-TAIL are in the 2026 folder",
                _turn(platform, H))
    in_s = _block(platform, _turn(platform, S))
    assert "HARTLEYS-HEAD" not in in_s and "HARTLEYS-TAIL" not in in_s
    in_h = _block(platform, _turn(platform, H))
    assert "HARTLEYS-HEAD" in in_h and "HARTLEYS-TAIL" in in_h


async def test_a_notebook_written_before_this_change_is_told_as_before(platform):
    files = platform.agents_registry.files
    files.write_notes(NAME, "- [2026-10-01] OLD-NOTE the K-1s are in /tax\n")
    for turn in (_turn(platform, H), _turn(platform, S), TurnWall(), TurnWall(ok=False)):
        assert "OLD-NOTE" in _block(platform, turn), turn
