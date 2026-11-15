"""North-star fix 1.1, the memory-recall half (v1.501.0): lessons pass the
client wall.

``MemoryFabric._lessons`` read every lesson, task reflections included, and
told them to any turn: "Worked well for 'Hartleys 2026 return': used the cash
basis" reached a chat in Smith's folder. Now recall leaves out
``source == "reflection"`` rows (as ``LearningEngine.apply_to_prompt``
already does, through the same ``exclude_sources`` and
``_PROMPT_EXCLUDED_SOURCES``) and passes every lesson whose evidence names a
source run or chat through ``_walled`` (``workmap.clients.wall``), failing
closed. A lesson with no source (a stated preference, a note on a reply) is
told as before.

Real database, real ``LearningEngine``, real wall; only the client RECORD is
a made-up map read through ``store.load_map`` (as in
``test_clients_wall_v1499.py``). Names and folders are made up.
"""

from __future__ import annotations

import json

import pytest

from iron_jarvis.core.db import init_db, make_engine, session_scope
from iron_jarvis.core.models import ChatThreadRecord, Session, SessionStatus
from iron_jarvis.learning.engine import LearningEngine
from iron_jarvis.learning.models import LessonRecord
from iron_jarvis.memory.fabric import MemoryFabric, _tokens
from iron_jarvis.workmap import keys
from iron_jarvis.workmap.clients import ClientRoot, TurnWall, names, store, wall

H_ID = "client_00000000000a"  # Hartleys
S_ID = "client_00000000000b"  # Smith
H = r"D:\Clients\Hartleys"
S = r"D:\Clients\Smith"
DOWNLOADS = r"C:\Users\Owner\Downloads"

Q = "cash basis return"


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
def scene(tmp_path, monkeypatch):
    engine = make_engine(tmp_path / "lessons.db")
    init_db(engine)
    cmap = _map()
    monkeypatch.setattr(store, "load_map", lambda e: cmap)
    yield engine
    engine.dispose()


def _run(engine, folder: str) -> str:
    with session_scope(engine) as db:
        s = Session(task="Prepare the 2026 return", status=SessionStatus.COMPLETED,
                    workspace_path=folder, options_json="{}")
        db.add(s)
        db.commit()
        return s.id


def _chat(engine, folder: str) -> str:
    with session_scope(engine) as db:
        rec = ChatThreadRecord(title="March", messages_json="[]",
                               setup_json=json.dumps({"workspace_dir": folder}))
        db.add(rec)
        db.commit()
        return rec.id


def _lesson(engine, text: str, *, source: str, link: str = "", weight: int = 3) -> str:
    evidence = json.dumps([{"quote": text[:40], "at": "", "where": "run", "link": link}]) if link else None
    with session_scope(engine) as db:
        row = LessonRecord(text=text, source=source, weight=weight, status="confirmed",
                           evidence_json=evidence)
        db.add(row)
        db.commit()
        return row.id


def _told(engine, turn: TurnWall) -> set[str]:
    fabric = MemoryFabric(engine=engine, learning=LearningEngine(engine))
    with wall.turn_scope(turn):
        return {h.ref for h in fabric._lessons(20, _tokens(Q))}


def _turn(engine, folder: str, words: str = "") -> TurnWall:
    return wall.turn_wall(engine, folder, words)


def test_a_reflection_from_a_run_in_one_clients_folder_is_not_recalled_in_anothers(scene):
    h_run = _run(scene, H + r"\2026")
    note = _lesson(scene, "Worked well for 'Prepare the 2026 return': used the cash basis",
                   source="reflection", link=f"/sessions/{h_run}", weight=1)
    bare = _lesson(scene, "Worked well for 'a return': the cash basis again", source="reflection",
                   weight=1)
    for turn in (_turn(scene, S), _turn(scene, H), TurnWall()):
        got = _told(scene, turn)
        assert note not in got and bare not in got, turn


def test_a_lesson_from_a_clients_run_is_told_only_where_the_wall_allows(scene):
    h_run = _run(scene, H + r"\2026")
    les = _lesson(scene, "For the return use the cash basis on bank lines", source="distilled",
                  link=f"/sessions/{h_run}")
    assert les in _told(scene, _turn(scene, H))
    assert les not in _told(scene, _turn(scene, S))
    assert les not in _told(scene, _turn(scene, DOWNLOADS, "the return"))
    assert les in _told(scene, _turn(scene, DOWNLOADS, "the Hartleys return"))


def test_a_lesson_from_a_clients_chat_is_walled_too(scene):
    tid = _chat(scene, H)
    les = _lesson(scene, "Keep the return on the cash basis", source="preference",
                  link=f"/chat?thread={tid}")
    assert les in _told(scene, _turn(scene, H))
    assert les not in _told(scene, _turn(scene, S))


def test_a_lesson_with_no_source_is_told_as_before(scene):
    les = _lesson(scene, "Answer about the return and cash basis in short lines",
                  source="preference", weight=5)
    for turn in (_turn(scene, S), _turn(scene, H), TurnWall(), TurnWall(ok=False)):
        assert les in _told(scene, turn), turn


def test_a_wall_that_cannot_be_read_tells_no_sourced_lesson(scene, monkeypatch):
    h_run = _run(scene, H)
    sourced = _lesson(scene, "For the return use the cash basis", source="distilled",
                      link=f"/sessions/{h_run}")
    plain = _lesson(scene, "Short answers on the return and cash basis", source="preference")
    got = _told(scene, TurnWall(ok=False, folder_key=_k(S)))
    assert sourced not in got and plain in got

    def unreadable(engine):
        raise RuntimeError("the client tables cannot be read")

    monkeypatch.setattr(store, "load_map", unreadable)
    got = _told(scene, TurnWall())
    assert sourced not in got and plain in got


def test_the_owner_screen_still_sees_the_sourced_lesson(scene):
    h_run = _run(scene, H)
    les = _lesson(scene, "For the return use the cash basis", source="distilled",
                  link=f"/sessions/{h_run}")
    fabric = MemoryFabric(engine=scene, learning=LearningEngine(scene))
    with wall.turn_scope(_turn(scene, S)), wall.owner_screen():
        assert les in {h.ref for h in fabric._lessons(20, _tokens(Q))}
