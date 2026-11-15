"""Clients (v1.499.0, vision plan leap 3, Phase 3): the shared contract.

The tables, the package skeleton and the events five builders code against
at once (``workmap/clients/__init__.py`` states the whole contract). Pinned
here only what stays true after every builder lands: the tables ``init_db``
creates and their keys; the numbers, the id pattern and the error sentences;
that with NOTHING on record every read answers "no client" (so the app keeps
working before and after); that the wall's ContextVar and its three scopes
set and reset; the setting; the four events.

All names and paths are made up (Hartleys, Smith); nothing here touches the
disk outside ``tmp_path``.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from iron_jarvis.core.config import Config
from iron_jarvis.core.db import init_db, make_engine
from iron_jarvis.core.events import EventType
from iron_jarvis.core.ids import new_id
from iron_jarvis.workmap import clients as C
from iron_jarvis.workmap.clients import block, store, wall
from iron_jarvis.workmap.models import WorkClient, WorkClientFolder, WorkClientPreference

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "src" / "iron_jarvis" / "workmap" / "clients"


@pytest.fixture()
def engine(tmp_path):
    eng = make_engine(tmp_path / "clients.db")
    init_db(eng)
    yield eng
    eng.dispose()


# --------------------------------------------------------------------------- #
# The tables
# --------------------------------------------------------------------------- #
def test_init_db_creates_the_three_client_tables_with_their_keys(engine):
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    assert {"workclient", "workclientfolder", "workclientpreference"} <= tables

    def cols(t):
        return {c["name"] for c in insp.get_columns(t)}

    assert cols("workclient") == {
        "id", "name", "name_key", "signature", "aliases_json", "status", "how",
        "evidence_json", "strength", "asked_at", "created_at", "decided_at",
        "confirmed_at", "updated_at",
    }
    assert cols("workclientfolder") == {"folder_key", "client_id", "folder", "how", "created_at"}
    assert cols("workclientpreference") == {
        "id", "client_id", "text", "status", "origin", "local_only", "evidence_json",
        "created_at", "updated_at",
    }
    assert insp.get_pk_constraint("workclient")["constrained_columns"] == ["id"]
    assert insp.get_pk_constraint("workclientfolder")["constrained_columns"] == ["folder_key"]
    assert insp.get_pk_constraint("workclientpreference")["constrained_columns"] == ["id"]

    def indexed(t):
        return {tuple(i["column_names"]): bool(i["unique"]) for i in insp.get_indexes(t)}

    wc = indexed("workclient")
    assert wc.get(("signature",)) is True, wc  # UNIQUE: the never-ask-twice key
    for col in ("name_key", "status", "created_at"):
        assert (col,) in wc, (col, wc)
    assert ("client_id",) in indexed("workclientfolder")
    wp = indexed("workclientpreference")
    assert ("client_id",) in wp and ("created_at",) in wp


def test_a_signature_is_on_record_once_and_the_defaults_are_the_safe_ones(engine):
    with Session(engine) as db:
        db.add(WorkClient(id=new_id("client"), name="Hartleys", signature="hartley"))
        db.add(WorkClientFolder(folder_key="d:/work/hartleys", client_id="client_000000000001"))
        db.add(WorkClientPreference(id=new_id("cpref"), client_id="client_000000000001", text="Uses cash basis."))
        db.commit()
    with Session(engine) as db:
        row = db.exec(select(WorkClient)).one()
        assert (row.status, row.how, row.aliases_json, row.asked_at) == ("proposed", "noticed", "[]", None)
        assert db.exec(select(WorkClientFolder)).one().how == "found"
        pref = db.exec(select(WorkClientPreference)).one()
        assert (pref.status, pref.local_only, pref.evidence_json) == ("confirmed", False, "[]")
    with Session(engine) as db:
        db.add(WorkClient(id=new_id("client"), name="Hartley's", signature="hartley", status="declined"))
        with pytest.raises(IntegrityError):
            db.commit()


# --------------------------------------------------------------------------- #
# The numbers, the id and the sentences
# --------------------------------------------------------------------------- #
def test_the_contract_numbers_and_the_id_pattern():
    assert (C.MAX_WAITING, C.CLIENT_BLOCK_CHARS, C.CLIENT_HOP_MAX_S) == (30, 1600, 5)
    assert C.CLIENT_ID_RE.pattern == r"^client_[0-9a-f]{12}$"
    assert C.CLIENT_ID_RE.match(new_id("client"))
    assert C.CLIENT_PREF_ID_RE.match(new_id("cpref"))
    for bad in ("Hartley's", "client_../confirm", "client_AB12CD34EF56", "client_ab12cd34ef5", "x"):
        assert not C.CLIENT_ID_RE.match(bad), bad
    assert "clients" in C.CLIENT_PARENT_PHRASES and "tax clients" in C.CLIENT_PARENT_PHRASES
    for word in ("payroll", "returns", "return", "statement", "1040", "engagements"):
        assert word in C.WORK_FOLDER_WORDS, word
    assert "hartleys" not in C.WORK_FOLDER_WORDS


def test_every_error_carries_its_status_and_a_plain_sentence():
    assert (C.ClientsUnreadable.status, C.ClientNotFound.status, C.ClientConflict.status,
            C.ClientRefused.status) == (503, 404, 409, 400)
    assert C.ClientsUnreadable().sentence == "Your clients could not be read. Nothing was changed."
    assert C.ClientNotFound().sentence == "That client is not on record."
    assert C.ClientConflict("Hartleys was already answered.").sentence == "Hartleys was already answered."
    err = C.ClientRefused("Downloads holds work for many people.\n Pick a folder that is only Hartleys'.")
    assert err.sentence == "Downloads holds work for many people. Pick a folder that is only Hartleys'."
    assert str(err) == err.sentence
    assert all(issubclass(e, C.ClientError) for e in
               (C.ClientsUnreadable, C.ClientNotFound, C.ClientConflict, C.ClientRefused))


def test_the_value_types_are_frozen_and_no_client_is_the_empty_turn():
    root = C.ClientRoot(client_id="client_000000000001", name="Hartleys", key="d:/work/hartleys",
                        folder=r"D:\Work\Hartleys")
    with pytest.raises(Exception):
        root.name = "Smith"  # type: ignore[misc]
    assert C.Waiting(client_id="client_000000000002", name="Smith").homes == ()
    nc = C.NO_CLIENT
    assert (nc.owner, nc.client, nc.spans, nc.named, nc.ok, nc.folder_key) == (
        None, None, False, frozenset(), True, "")
    assert wall.NO_CLIENT is C.NO_CLIENT and wall.TurnWall is C.TurnWall


def test_the_package_init_is_the_contract_only_and_imports_none_of_its_modules():
    tree = ast.parse((PKG / "__init__.py").read_text(encoding="utf-8"))
    relative = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.level > 0]
    assert relative == [], "clients/__init__ must import none of its modules (no cycle for any caller)"
    doc = ast.get_docstring(tree) or ""
    for must in ("client.suggested", "client_question", "GET /clients/{id}", "may_tell", "NO_CLIENT"):
        assert must in doc, must


# --------------------------------------------------------------------------- #
# Nothing on record: every read answers "no client"
# --------------------------------------------------------------------------- #
def test_with_nothing_on_record_the_map_and_the_wall_answer_no_client(engine):
    cmap = store.load_map(engine)
    for folder in (r"D:\Work\Hartleys 2026", "D:/Clients/Smith/2025", ""):
        assert cmap.client_of(folder) is None
        assert cmap.waiting_of(folder) is None
    assert cmap.roots_of("client_000000000001") == ()
    assert dict(cmap.names) == {} and cmap.confirmed == () and cmap.waiting == ()
    assert store.preferences(engine, "client_000000000001") == []

    turn = wall.turn_wall(engine, r"D:\Work\Hartleys 2026", "what did we do for Hartleys?")
    assert isinstance(turn, C.TurnWall)
    assert (turn.owner, turn.client, turn.named, turn.ok, turn.spans) == (None, None, frozenset(), True, False)
    assert wall.may_tell(turn, cmap, folder=r"D:\Work\Smith", title="Smith payroll") is True
    assert wall.may_tell(C.NO_CLIENT, cmap, folder="") is True


def test_with_nothing_on_record_the_client_block_is_empty(engine):
    platform = type("P", (), {"engine": engine, "config": Config(home=Path(engine.url.database).parent,
                                                                project_root=Path(engine.url.database).parent)})()
    assert block.client_block(platform, folder=r"D:\Work\Hartleys 2026") == ""
    assert block.client_block(platform, folder="", local=False) == ""


# --------------------------------------------------------------------------- #
# The wall's ContextVar
# --------------------------------------------------------------------------- #
HARTLEYS = C.TurnWall(owner="client:client_000000000001",
                      client=C.ClientRoot("client_000000000001", "Hartleys", "d:/work/hartleys", r"D:\Work\Hartleys"),
                      folder_key="d:/work/hartleys")
SMITH = C.TurnWall(owner="client:client_000000000002", folder_key="d:/work/smith")


def test_turn_scope_sets_and_resets_the_wall_and_is_never_an_owner_screen():
    assert wall.current() is C.NO_CLIENT and wall.on_owner_screen() is False
    with wall.turn_scope(HARTLEYS) as got:
        assert got is HARTLEYS and wall.current() is HARTLEYS
        with wall.turn_scope(SMITH):
            assert wall.current() is SMITH
        assert wall.current() is HARTLEYS
    assert wall.current() is C.NO_CLIENT
    with wall.turn_scope(None):
        assert wall.current() is C.NO_CLIENT
    with pytest.raises(RuntimeError):
        with wall.turn_scope(HARTLEYS):
            raise RuntimeError("boom")
    assert wall.current() is C.NO_CLIENT

    with wall.owner_screen():
        assert wall.on_owner_screen() is True
        with wall.turn_scope(HARTLEYS):
            assert wall.on_owner_screen() is False  # a turn is never the owner's screen
        assert wall.on_owner_screen() is True
    assert wall.on_owner_screen() is False


def test_scoped_frames_carries_the_wall_into_the_turn_and_its_tasks_and_resets_it():
    seen: list[object] = []
    closed: list[bool] = []

    async def turn():
        try:
            seen.append(wall.current())
            yield 1
            # a task the turn creates copies the wall (the model pump, tool rounds)
            seen.append(await asyncio.create_task(asyncio.to_thread(wall.current)))
            yield 2
            yield 3
        finally:
            closed.append(True)

    async def main():
        frames = []
        outer = wall.scoped_frames(turn(), HARTLEYS)
        assert wall.current() is C.NO_CLIENT  # nothing is set before the first frame
        async for f in outer:
            frames.append(f)
            if f == 2:
                break
        await outer.aclose()
        return frames, list(closed), wall.current()

    frames, closed_at_once, after = asyncio.run(main())
    assert frames == [1, 2]
    assert seen == [HARTLEYS, HARTLEYS]
    assert closed_at_once == [True]  # closing the wrapper closed the turn AT ONCE, not at shutdown
    assert after is C.NO_CLIENT


# --------------------------------------------------------------------------- #
# The setting and the events
# --------------------------------------------------------------------------- #
def test_notice_clients_is_a_setting_that_starts_on():
    assert Config.model_fields["notice_clients"].default is True


def test_the_four_client_events():
    from iron_jarvis.comm.notifier import EVENT_WORDS

    assert (EventType.CLIENT_SUGGESTED, EventType.CLIENT_CONFIRMED, EventType.CLIENT_DECLINED,
            EventType.CLIENT_CHANGED) == ("client.suggested", "client.confirmed", "client.declined",
                                          "client.changed")
    for etype in ("client.suggested", "client.confirmed", "client.declined", "client.changed"):
        words = EVENT_WORDS[etype]
        assert "Hartley" not in words and "{" not in words, words
