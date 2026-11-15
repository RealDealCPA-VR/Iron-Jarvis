"""North-star fixes 1.2 and 1.11 (v1.501.0): ``memory_write`` keeps its
scope and the client wall, and a write over an existing key can be undone.

1.2: ``memory_write`` dropped ``scope_id``: two runs writing
``("session", "plan")`` shared ONE row (the second replaced the first), and
an entry written while working in one client's folder was recalled into any
other turn. Now the record carries the writing session (``origin_session``)
and the folder the work was in (``origin_folder``, the wall's own key); a
``session`` entry is keyed by its session, a ``project`` entry by the
project (or the folder when there is none), and memory recall
(``MemoryFabric._memory``) keeps an entry to its scope and passes it through
``workmap.clients.wall.may_tell``, failing CLOSED like ``_sessions``. A row
written before this change has no origin and is told as before.

1.11: a ``memory_write`` over an existing key replaced the text with no
record. Now the tool is REVERSIBLE: the earlier text is journaled before the
write and ``POST /undo/{id}`` puts it back (a created entry is removed).

Every write goes through the real tool registry; every refusal through the
real wall (only the client RECORD is a made-up map, read through
``store.load_map``, as in ``test_clients_wall_v1499.py``). Names and folders
are made up.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.memory.fabric import MemoryFabric
from iron_jarvis.memory.models import MemoryRecord
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.workmap import keys
from iron_jarvis.workmap.clients import ClientRoot, TurnWall, names, store, wall

H_ID = "client_00000000000a"  # Hartleys
S_ID = "client_00000000000b"  # Smith
H = r"D:\Clients\Hartleys"
S = r"D:\Clients\Smith"
DOWNLOADS = r"C:\Users\Owner\Downloads"


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
    return p


def _ctx(p, tmp_path, session_id: str, *, thread_id: str = "") -> ToolContext:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return ToolContext(workspace=ws, session_id=session_id, agent_run_id=f"run-{session_id}",
                       config=p.config, event_bus=p.event_bus, engine=p.engine, thread_id=thread_id)


def _turn(p, folder: str, words: str = "") -> TurnWall:
    return wall.turn_wall(p.engine, folder, words)


async def _write(p, ctx, layer, key, text, *, turn: TurnWall | None = None):
    with wall.turn_scope(turn if turn is not None else TurnWall()):
        r = await p.registry.invoke("memory_write", {"layer": layer, "key": key, "text": text},
                                    ctx, p.permissions, allowed_names={"memory_write"})
    assert r.ok, r.error
    return r


async def _read(p, ctx, layer, key, *, turn: TurnWall | None = None):
    with wall.turn_scope(turn if turn is not None else TurnWall()):
        r = await p.registry.invoke("memory_read", {"layer": layer, "key": key},
                                    ctx, p.permissions, allowed_names={"memory_read"})
    assert r.ok, r.error
    return r.output


def _recalled(p, turn: TurnWall, query: str, **kw) -> set[str]:
    fabric = MemoryFabric(memory=p.memory, engine=p.engine)
    with wall.turn_scope(turn):
        return {h.snippet for h in fabric.recall(query, k=10, sources=["memory"], **kw)}


def _rows(p) -> list[MemoryRecord]:
    with session_scope(p.engine) as db:
        return list(db.exec(select(MemoryRecord)))


# --------------------------------------------------------------------------- #
# 1.2: the scope is kept
# --------------------------------------------------------------------------- #
async def test_two_runs_writing_the_same_session_key_do_not_overwrite_each_other(platform, tmp_path):
    a, b = _ctx(platform, tmp_path, "sess-a"), _ctx(platform, tmp_path, "sess-b")
    await _write(platform, a, "session", "plan", "run A: reconcile the bank first")
    await _write(platform, b, "session", "plan", "run B: draft the letter first")
    assert await _read(platform, a, "session", "plan") == "run A: reconcile the bank first"
    assert await _read(platform, b, "session", "plan") == "run B: draft the letter first"
    plans = [r for r in _rows(platform) if r.key == "plan"]
    assert sorted(r.scope_id for r in plans) == ["sess-a", "sess-b"]
    assert {r.origin_session for r in plans} == {"sess-a", "sess-b"}


async def test_two_chats_keep_their_own_session_entry(platform, tmp_path):
    one = _ctx(platform, tmp_path, "chat", thread_id="thread-one")
    two = _ctx(platform, tmp_path, "chat", thread_id="thread-two")
    await _write(platform, one, "session", "plan", "chat one plan")
    await _write(platform, two, "session", "plan", "chat two plan")
    assert await _read(platform, one, "session", "plan") == "chat one plan"
    assert await _read(platform, two, "session", "plan") == "chat two plan"


async def test_a_chat_is_grounded_in_its_own_session_entry_and_no_other_chats(platform, tmp_path):
    """The chat lanes' automatic grounding passes ``memory_session`` (the
    chat as ``memory_write`` keys it): without it a chat was no longer told
    the entry it wrote itself."""
    from iron_jarvis.memory.origin import chat_tag

    one = _ctx(platform, tmp_path, "chat", thread_id="thread-one")
    await _write(platform, one, "session", "plan", "the depreciation schedule goes first")
    fabric = MemoryFabric(memory=platform.memory, engine=platform.engine)

    def grounded(thread: str) -> str:
        with wall.turn_scope(TurnWall()):
            return fabric.ground("depreciation schedule", sources=["memory"],
                                 memory_session=chat_tag(thread))

    assert "depreciation schedule goes first" in grounded("thread-one")
    assert grounded("thread-two") == "" and grounded("") == ""


def test_both_chat_lanes_ground_with_the_chats_own_session():
    """One call site builds both lanes' grounding; it names the chat."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "src" / "iron_jarvis" / "daemon" / "chat_turn.py")
    text = src.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert text.count("_fab.ground(") == 1
    call = text[text.index("_fab.ground("):]
    call = call[: call.index(") or \"\"")]
    assert 'memory_session=_chat_tag(getattr(body, "thread_id", ""))' in call
    routes = (src.parent / "routes" / "chat.py").read_text(encoding="utf-8")
    assert ".ground(" not in routes  # the stream lane has no second call to forget


async def test_a_session_entry_is_recalled_only_in_its_own_session(platform, tmp_path):
    await _write(platform, _ctx(platform, tmp_path, "sess-a"), "session", "plan",
                 "the depreciation schedule goes first")
    assert _recalled(platform, TurnWall(), "depreciation schedule", session_id="sess-a")
    assert not _recalled(platform, TurnWall(), "depreciation schedule", session_id="sess-b")
    assert not _recalled(platform, TurnWall(), "depreciation schedule")


async def test_a_project_entry_written_in_one_clients_folder_is_not_recalled_in_anothers(
    platform, tmp_path,
):
    ctx = _ctx(platform, tmp_path, "sess-h")
    await _write(platform, ctx, "project", "method", "Hartleys use the cash basis",
                 turn=_turn(platform, H + r"\2026"))
    row = next(r for r in _rows(platform) if r.key == "method")
    assert row.origin_folder == _k(H + r"\2026")
    assert _recalled(platform, _turn(platform, H + r"\2026"), "cash basis")
    assert not _recalled(platform, _turn(platform, S), "cash basis")
    # Two clients' folders keep two project entries under one key.
    await _write(platform, _ctx(platform, tmp_path, "sess-s"), "project", "method",
                 "Smith uses accrual", turn=_turn(platform, S))
    assert await _read(platform, ctx, "project", "method",
                       turn=_turn(platform, H + r"\2026")) == "Hartleys use the cash basis"


async def test_a_user_entry_follows_the_wall(platform, tmp_path):
    await _write(platform, _ctx(platform, tmp_path, "sess-h"), "user", "basis",
                 "the cash basis for the bank statements", turn=_turn(platform, H))
    q = "cash basis bank statements"
    assert _recalled(platform, _turn(platform, H), q)
    assert not _recalled(platform, _turn(platform, S), q)
    # A no-client chat hears it only when the owner's own words name the client.
    assert not _recalled(platform, _turn(platform, DOWNLOADS, "the bank statements"), q)
    assert _recalled(platform, _turn(platform, DOWNLOADS, "Hartleys bank statements"), q)
    # The owner's own screens are not walled.
    fabric = MemoryFabric(memory=platform.memory, engine=platform.engine)
    with wall.turn_scope(_turn(platform, S)), wall.owner_screen():
        assert fabric.recall(q, k=5, sources=["memory"])


async def test_a_wall_that_cannot_be_read_fails_closed(platform, tmp_path, monkeypatch):
    await _write(platform, _ctx(platform, tmp_path, "sess-h"), "user", "basis",
                 "the cash basis for the bank statements", turn=_turn(platform, H))
    q = "cash basis bank statements"
    assert not _recalled(platform, TurnWall(ok=False, folder_key=_k(S)), q)
    assert _recalled(platform, TurnWall(ok=False, folder_key=_k(H)), q)  # exactly its folder

    def unreadable(engine):
        raise RuntimeError("the client tables cannot be read")

    monkeypatch.setattr(store, "load_map", unreadable)
    assert not _recalled(platform, TurnWall(), q)


async def test_an_entry_written_before_this_change_is_told_as_before(platform, tmp_path):
    platform.memory.write("user", "tone", "short answers about the cash basis")
    platform.memory.write("session", "old", "an old shared session note on the cash basis",
                          scope_id="sess-old")
    q = "cash basis"
    for turn in (_turn(platform, S), _turn(platform, H), TurnWall(), TurnWall(ok=False)):
        got = _recalled(platform, turn, q)
        assert "short answers about the cash basis" in got, turn
        assert "an old shared session note on the cash basis" in got, turn


async def test_memory_search_and_read_follow_the_same_wall(platform, tmp_path):
    await _write(platform, _ctx(platform, tmp_path, "sess-h"), "user", "basis",
                 "the cash basis for the bank statements", turn=_turn(platform, H))
    other = _ctx(platform, tmp_path, "sess-s")
    with wall.turn_scope(_turn(platform, S)):
        r = await platform.registry.invoke("memory_search", {"query": "cash basis bank"}, other,
                                           platform.permissions, allowed_names={"memory_search"})
    assert r.ok and "cash basis" not in (r.output or ""), r.output
    assert await _read(platform, other, "user", "basis", turn=_turn(platform, S)) == ""
    assert "cash basis" in await _read(platform, other, "user", "basis", turn=_turn(platform, H))


async def test_a_write_over_another_clients_entry_is_refused(platform, tmp_path):
    await _write(platform, _ctx(platform, tmp_path, "sess-h"), "user", "basis",
                 "the cash basis for the bank statements", turn=_turn(platform, H))
    with wall.turn_scope(_turn(platform, S)):
        r = await platform.registry.invoke(
            "memory_write", {"layer": "user", "key": "basis", "text": "accrual"},
            _ctx(platform, tmp_path, "sess-s"), platform.permissions, allowed_names={"memory_write"})
    assert r.ok is False and "Hartleys" not in (r.error or "")
    assert platform.memory.read("user", "basis") == "the cash basis for the bank statements"


# --------------------------------------------------------------------------- #
# 1.11: a write over an existing key can be undone
# --------------------------------------------------------------------------- #
def _last_action(p, tool: str) -> str:
    with session_scope(p.engine) as db:
        rows = [r for r in db.exec(select(ToolInvocation)).all() if r.tool == tool]
    rows.sort(key=lambda r: r.created_at)
    return rows[-1].id


def _client(p) -> TestClient:
    """The real ``/undo`` routes over this platform (test_undo_list_paths_v1168's way)."""
    from types import SimpleNamespace

    from fastapi import FastAPI

    from iron_jarvis.daemon.routes import undo as undo_routes

    app = FastAPI()
    undo_routes.register(app, SimpleNamespace(platform=p))
    return TestClient(app)


async def test_undo_of_an_overwrite_puts_the_earlier_text_back(platform, tmp_path):
    ctx = _ctx(platform, tmp_path, "sess-a")
    await _write(platform, ctx, "user", "tone", "short answers, please")
    await _write(platform, ctx, "user", "tone", "long answers with every detail")
    assert platform.memory.read("user", "tone") == "long answers with every detail"
    action = _last_action(platform, "memory_write")
    r = _client(platform).post(f"/undo/{action}")
    assert r.status_code == 200, r.text
    assert platform.memory.read("user", "tone") == "short answers, please"
    assert len([x for x in _rows(platform) if x.key == "tone"]) == 1


async def test_undo_of_a_new_entry_removes_it(platform, tmp_path):
    ctx = _ctx(platform, tmp_path, "sess-a")
    await _write(platform, ctx, "user", "fresh", "a brand new note")
    r = _client(platform).post(f"/undo/{_last_action(platform, 'memory_write')}")
    assert r.status_code == 200, r.text
    assert platform.memory.read("user", "fresh") is None


async def test_undo_refuses_when_the_entry_changed_since(platform, tmp_path):
    ctx = _ctx(platform, tmp_path, "sess-a")
    await _write(platform, ctx, "user", "tone", "short answers, please")
    await _write(platform, ctx, "user", "tone", "long answers")
    first_overwrite = _last_action(platform, "memory_write")
    platform.memory.write("user", "tone", "the owner changed it by hand")
    r = _client(platform).post(f"/undo/{first_overwrite}")
    assert r.status_code == 409, r.text
    assert platform.memory.read("user", "tone") == "the owner changed it by hand"
