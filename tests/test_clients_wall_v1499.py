"""The client wall (v1.499.0, vision plan leap 3, Phase 3).

ONE rule (``workmap/clients/wall.py``) decides what each turn may be told
about clients' work: the work block (both parts, the named path and a
client's other folders), memory recall's chat passages and run summaries,
and the history search tool. It is stricter than before the moment the
update lands (folders under a Clients-type folder and waiting names are
walled before the owner answers anything), and it fails CLOSED when the
client tables cannot be read.

Every test here runs on builder A's real record: the wall's own tests build
a real :class:`store.ClientMap` from made-up folders (:func:`make_map`) and
read it through ``store.load_map``; the lane, preference and event tests
make their clients through the real store functions.

All names and paths are made up (Hartleys, Smith, Jones, Lee); nothing here
reads a real folder: a folder is TEXT (the disk calls are patched to raise
in ``test_wall_and_names_never_touch_the_disk``).
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import re
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.core.db import init_db, make_engine, session_scope
from iron_jarvis.core.ids import utcnow
from iron_jarvis.core.models import AgentRun, ChatThreadRecord, Project, Session, SessionStatus
from iron_jarvis.memory.fabric import MemoryFabric
from iron_jarvis.providers import egress
from iron_jarvis.search.models import SearchHit
from iron_jarvis.search.tools import HistorySearchTool
from iron_jarvis.workmap import block as B
from iron_jarvis.workmap import keys
from iron_jarvis.workmap import recap as R
from iron_jarvis.workmap.clients import (
    NO_CLIENT,
    ClientRoot,
    ClientsUnreadable,
    TurnWall,
    Waiting,
    names,
    store,
    wall,
)
from iron_jarvis.workmap.clients import block as client_block_mod
from iron_jarvis.workmap.index import work_map
from iron_jarvis.workmap.models import RECAP_KEPT, WorkRecap

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "iron_jarvis"

H_ID = "client_00000000000a"  # Hartleys
S_ID = "client_00000000000b"  # Smith
J_ID = "client_00000000000c"  # Jones (waiting)

CLIENTS = r"D:\Clients"
H = r"D:\Clients\Hartleys"
H2 = r"E:\Scans\Hartleys"
S = r"D:\Clients\Smith"
JONES = r"D:\Work\Jones"
LEE = r"D:\Clients\Lee"
DOWNLOADS = r"C:\Users\Owner\Downloads"


# --------------------------------------------------------------------------- #
# The record: builder A's real map, built from made-up folders
# --------------------------------------------------------------------------- #
def _k(folder: str) -> str:
    return keys.folder_key(keys.home_folder(folder))


def make_map(confirmed=(), waiting=()) -> store.ClientMap:
    roots = tuple(ClientRoot(cid, name, _k(f), f) for cid, name, folders in confirmed for f in folders)
    wait = tuple(Waiting(cid, name, tuple(_k(h) for h in homes)) for cid, name, homes in waiting)
    return store.ClientMap(
        version=1, confirmed=roots, waiting=wait,
        names={cid: (name, names.signature(name), ()) for cid, name, _f in confirmed},
        waiting_names={cid: (name, names.signature(name), ()) for cid, name, _f in waiting},
    )


#: Hartleys (two folders) and Smith confirmed; Jones waiting.
FIRM = dict(
    confirmed=[(H_ID, "Hartleys", [H, H2]), (S_ID, "Smith", [S])],
    waiting=[(J_ID, "Jones", [JONES])],
)


@pytest.fixture()
def use_map(monkeypatch):
    """``use_map(cmap)``: the record the wall reads is ``cmap``."""

    def use(cmap):
        monkeypatch.setattr(store, "load_map", lambda engine: cmap)
        return cmap

    return use


# --------------------------------------------------------------------------- #
# The scene: a real database, the real work record, made-up folders
# --------------------------------------------------------------------------- #
@pytest.fixture()
def scene(tmp_path, use_map):
    engine = make_engine(tmp_path / "wall.db")
    init_db(engine)
    home = tmp_path / "home"
    home.mkdir()
    s = SimpleNamespace(tmp=tmp_path, engine=engine, home=home)
    s.platform = SimpleNamespace(engine=engine, config=SimpleNamespace(home=home))
    s.wm = work_map(engine, home)
    s.use = use_map
    s.cmap = use_map(make_map(**FIRM))
    yield s
    engine.dispose()


def _chat(scene, title, folder, *, hours_ago=1.0, docs=(), local_only=False, project_id=None,
          said="hi", answer="done") -> str:
    setup: dict = {}
    if folder:
        setup["workspace_dir"] = folder
    if local_only:
        setup["local_only"] = True
    messages = [{"role": "user", "content": said},
                {"role": "assistant", "content": answer, "documents": list(docs)}]
    with session_scope(scene.engine) as db:
        rec = ChatThreadRecord(title=title, project_id=project_id, messages_json=json.dumps(messages),
                               setup_json=json.dumps(setup) if setup else "",
                               updated_at=utcnow() - timedelta(hours=hours_ago))
        db.add(rec)
        db.commit()
        return rec.id


def _run(scene, task, folder, *, summary="All done.", project_id=None, hours_ago=3.0) -> str:
    when = utcnow() - timedelta(hours=hours_ago)
    with session_scope(scene.engine) as db:
        s = Session(task=task, summary=summary, status=SessionStatus.COMPLETED, origin="chat",
                    project_id=project_id, workspace_path=folder, created_at=when, finished_at=when,
                    options_json="{}")
        db.add(s)
        db.commit()
        db.refresh(s)
        db.add(AgentRun(session_id=s.id))
        db.commit()
        return s.id


def _project(scene, pid, root) -> str:
    with session_scope(scene.engine) as db:
        db.add(Project(id=pid, name=pid, root=root))
        db.commit()
    return pid


def _recap(scene, tid, text):
    with session_scope(scene.engine) as db:
        rec = db.get(ChatThreadRecord, tid)
        db.add(WorkRecap(id=f"chat:{tid}", kind="chat", ref=tid, state=RECAP_KEPT, text=text,
                         source_updated=R.stamp_of(rec.updated_at)))
        db.commit()


def _block(scene, *, folder="", query="", **kw) -> str:
    scene.wm.refresh(force=True)
    return B.work_block(scene.platform, folder=folder, query=query, **kw)


def _lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.startswith("- [")]


@pytest.fixture()
def firm(scene):
    """Work in every kind of folder: Hartleys' two, Smith's, a waiting
    name's, a Clients child nobody answered about, Downloads."""
    scene.h_march = _chat(scene, "March reconciliation", H + r"\2026",
                          docs=[H + r"\2026\recon-march.xlsx"], hours_ago=2)
    scene.h_payroll = _chat(scene, "Payroll setup", H2, docs=[H2 + r"\payroll-2026.xlsx"], hours_ago=5)
    scene.s_report = _chat(scene, "Quarterly report", S + r"\2025", docs=[S + r"\2025\report.xlsx"],
                           hours_ago=3)
    scene.j_list = _chat(scene, "Supplier list clean up", JONES, docs=[JONES + r"\suppliers.xlsx"],
                         hours_ago=4)
    scene.lee = _chat(scene, "Engagement letter draft", LEE, hours_ago=6)
    scene.dl_named = _chat(scene, "Hartleys bank letter", DOWNLOADS, hours_ago=7)
    scene.dl_plain = _chat(scene, "Bank letter template", DOWNLOADS, hours_ago=8)
    scene.wm.refresh(force=True)
    return scene


def _turn(scene, folder, text="") -> TurnWall:
    return wall.turn_wall(scene.engine, folder, text)


# --------------------------------------------------------------------------- #
# The may_tell table
# --------------------------------------------------------------------------- #
_H_TURN = (H + r"\2026", "")
_NONE = (r"D:\Work\Misc", "")
TABLE = [
    # (turn folder, owner's words), (row folder, row title), told?
    ("own client, a subfolder", _H_TURN, (H + r"\2025", "x"), True),
    ("own client, its other folder", _H_TURN, (H2, "x"), True),
    ("own client, the worktree copy", (H + ".ij-worktrees\\fix", ""), (H, "x"), True),
    ("another client", _H_TURN, (S, "x"), False),
    ("another client, even when the owner names it", (H, "Smith's report please"), (S, "x"), False),
    ("unowned, names nobody", _H_TURN, (DOWNLOADS, "Bank letter template"), True),
    ("unowned, names another client", _H_TURN, (DOWNLOADS, "Smith bank letter"), False),
    ("unowned, names this client", _H_TURN, (DOWNLOADS, "Hartleys bank letter"), True),
    ("unowned, names both", _H_TURN, (DOWNLOADS, "Hartleys and Smith letter"), False),
    ("a waiting name's home", _H_TURN, (JONES, "x"), False),
    ("a Clients child nobody answered about", _H_TURN, (LEE, "x"), False),
    ("a look-alike folder", _H_TURN, (r"D:\Clients\Hartleys 2026 old", "x"), False),
    ("a look-alike folder 2", _H_TURN, (r"D:\Clients\Hartleys-old", "x"), False),
    ("no client: a confirmed client", _NONE, (H, "x"), False),
    ("no client, the owner names it", (r"D:\Work\Misc", "what did we do for Hartleys?"), (H, "x"), True),
    ("no client, names one, another's row", (r"D:\Work\Misc", "for Hartleys"), (S, "x"), False),
    ("no client, lower case names nobody", (r"D:\Work\Misc", "for hartleys"), (H, "x"), False),
    ("no client: a waiting name (Phase 1 rule)", _NONE, (JONES, "x"), True),
    ("no client: a Clients child (Phase 1 rule)", _NONE, (LEE, "x"), True),
    ("no client: unowned", _NONE, (DOWNLOADS, "x"), True),
    ("no folder: a confirmed client", ("", ""), (H, "x"), False),
    ("the Clients folder: a Clients child", (CLIENTS, ""), (LEE, "x"), False),
    ("the Clients folder, named: pointers", (CLIENTS, "Hartleys please"), (H + r"\2026", "x"), True),
    ("the Clients folder: its own loose work", (CLIENTS, ""), (CLIENTS, "Firm letterhead"), True),
    ("a parent of a waiting home", (r"D:\Work", ""), (JONES, "x"), False),
    ("a parent of an unknown Clients folder", (r"C:\Users\Owner\Documents", ""),
     (r"C:\Users\Owner\Documents\Clients\Ng\2025", "x"), False),
    ("a waiting home's own subfolder", (JONES, ""), (JONES + r"\2025", "x"), True),
    ("a waiting home: a confirmed client", (JONES, ""), (H, "x"), False),
    ("a Clients child: its own folder", (LEE + r"\2025", ""), (LEE, "x"), True),
    ("a Clients child: a confirmed client", (LEE, ""), (H, "x"), False),
]


@pytest.mark.parametrize("what, turn, row, told", TABLE, ids=[t[0] for t in TABLE])
def test_may_tell_table(scene, what, turn, row, told):
    t = _turn(scene, *turn)
    assert t.ok
    assert wall.may_tell(t, scene.cmap, folder=row[0], title=row[1]) is told, (what, t)


def test_may_tell_fails_closed_to_exactly_the_turns_folder(scene):
    closed = TurnWall(ok=False, folder_key=_k(H))
    assert wall.may_tell(closed, scene.cmap, folder=H) is True
    assert wall.may_tell(closed, scene.cmap, folder=H + r"\2026") is False  # not even a subfolder
    assert wall.may_tell(closed, scene.cmap, folder=DOWNLOADS) is False
    assert wall.may_tell(TurnWall(ok=False), scene.cmap, folder="") is False
    # A coding session's own copy IS its repository's folder.
    assert wall.may_tell(closed, scene.cmap, folder=H + ".ij-worktrees\\fix-1") is True
    # Unset is the no-client rule: a confirmed client's row is not told.
    assert wall.may_tell(wall.current(), scene.cmap, folder=H) is False
    assert wall.may_tell(NO_CLIENT, scene.cmap, folder=DOWNLOADS, title="Bank letter template") is True


def test_turn_wall_reads_the_folder_and_the_owners_words_and_never_raises(scene, monkeypatch):
    t = _turn(scene, H + r"\2026\payroll", "what did Smith say?")
    assert t.owner == f"client:{H_ID}" and t.client is not None and t.client.client_id == H_ID
    assert t.named == frozenset({S_ID}) and t.ok and not t.spans
    assert t.folder_key == _k(H + r"\2026\payroll")
    copy = _turn(scene, H + ".ij-worktrees\\fix-1")
    assert copy.owner == f"client:{H_ID}" and copy.folder_key == _k(H)
    assert _turn(scene, JONES).owner == f"waiting:{J_ID}"
    assert _turn(scene, LEE + r"\2025").owner == "folder:" + _k(LEE)
    assert _turn(scene, CLIENTS).spans and _turn(scene, CLIENTS).owner is None
    assert _turn(scene, "", "") == NO_CLIENT

    def unreadable(engine):
        raise ClientsUnreadable()

    monkeypatch.setattr(store, "load_map", unreadable)
    closed = _turn(scene, H, "Hartleys")
    assert (closed.ok, closed.owner, closed.folder_key) == (False, None, _k(H))
    scene.use(scene.cmap)

    def broken(text, table):
        raise RuntimeError("names on fire")

    monkeypatch.setattr(names, "named_in", broken)
    t = _turn(scene, H, "Smith")
    assert t.ok and t.named == frozenset() and t.owner == f"client:{H_ID}"


# --------------------------------------------------------------------------- #
# The work block
# --------------------------------------------------------------------------- #
def test_another_clients_work_is_never_told_in_a_client_folder(firm):
    told = _block(firm, folder=S, query="Hartleys March reconciliation please")
    assert "Quarterly report" in told  # Smith's own work
    assert "March reconciliation" not in told and "recon-march" not in told, told
    assert "Payroll setup" not in told and "Hartleys bank letter" not in told
    assert B.WALL_NOTE in told
    assert "Hartleys" not in B.WALL_NOTE and "Smith" not in B.WALL_NOTE
    # The same words in Hartleys' own folder find it.
    own = _block(firm, folder=H + r"\2025", query="Hartleys March reconciliation please")
    assert "March reconciliation" in own and B.WALL_NOTE not in own


def test_an_unowned_row_naming_another_client_is_not_told_in_a_client_folder(firm):
    in_smith = _block(firm, folder=S, query="find the bank letter")
    assert "Bank letter template" in in_smith  # names nobody: the Phase 1 rule holds
    assert "Hartleys bank letter" not in in_smith, in_smith
    assert B.WALL_NOTE in in_smith
    in_hartleys = _block(firm, folder=H, query="find the bank letter")
    assert "Hartleys bank letter" in in_hartleys and "Bank letter template" in in_hartleys
    assert B.WALL_NOTE not in in_hartleys


def test_a_parent_folder_chat_is_not_told_owned_titles(firm):
    _chat(firm, "Firm letterhead", CLIENTS, hours_ago=9)
    told = _block(firm, folder=CLIENTS, query="what is the weather like")
    for title in ("March reconciliation", "Quarterly report", "Engagement letter draft"):
        assert title not in told, told
    assert "Firm letterhead" in told  # the parent folder's own loose work
    # Documents above a Clients folder Iron Jarvis knows nothing about yet.
    docs = r"C:\Users\Owner\Documents"
    _chat(firm, "Ng payroll", docs + r"\Clients\Ng\2025", hours_ago=1)
    _chat(firm, "Recipe card", docs + r"\Recipes", hours_ago=2)
    told = _block(firm, folder=docs)
    assert "Recipe card" in told and "Ng payroll" not in told, told


def test_a_folder_under_clients_is_walled_before_it_is_confirmed(scene):
    scene.use(make_map())  # nothing confirmed, nothing waiting: only the path
    _chat(scene, "March reconciliation", r"D:\Clients\Hartleys\2026", hours_ago=2)
    _chat(scene, "Quarterly report", r"D:\Clients\Smith\2025", hours_ago=3)
    in_smith = _block(scene, folder=r"D:\Clients\Smith", query="Hartleys March reconciliation please")
    assert "Quarterly report" in in_smith and "March reconciliation" not in in_smith, in_smith
    assert B.WALL_NOTE in in_smith
    own = _block(scene, folder=r"D:\Clients\Hartleys\2025", query="Hartleys March reconciliation please")
    assert "March reconciliation" in own
    above = _block(scene, folder=r"D:\Clients")
    assert above == "", above


def test_a_waiting_suggestions_folders_are_walled(firm):
    # A chat in the waiting name's home is not told a confirmed client's work...
    told = _block(firm, folder=JONES, query="Hartleys March reconciliation please")
    assert "Supplier list clean up" in told and "March reconciliation" not in told, told
    # ...a client's chat is not told the waiting name's work...
    told = _block(firm, folder=S, query="open the supplier list we cleaned up")
    assert "Supplier list clean up" not in told, told
    # ...nor is a chat in the folder that holds the waiting home.
    told = _block(firm, folder=r"D:\Work")
    assert "Supplier list clean up" not in told, told
    # A plain chat with no folder keeps the Phase 1 rule for a waiting name.
    told = _block(firm, query="open the supplier list we cleaned up")
    assert "Supplier list clean up" in told


def test_a_chat_with_no_client_is_told_a_confirmed_client_only_when_the_owner_names_it(firm):
    _recap(firm, firm.h_march, "Decided: Hartleys use cash basis for 2026")
    assert _block(firm, query="Invoices: how do I number them?") == ""
    assert _block(firm, query="what did we last do for hartleys?") == ""  # not written as a name
    # Words that match a confirmed client's work, but name no client: nothing.
    assert _block(firm, query="redo the March reconciliation") == ""
    assert _block(firm, folder=r"D:\Work\Misc", query="update the quarterly report") == ""
    told = _block(firm, query="what did we last do for Hartleys?")
    assert "March reconciliation" in told and "Payroll setup" in told, told
    assert ", for Hartleys, in " + H + r"\2026" in told
    assert "cash basis" not in told and B.RECAP_NOTE not in told  # pointers, never facts
    assert "Quarterly report" not in told
    assert B.WALL_NOTE not in told


def test_a_named_client_with_two_folders_is_still_found(scene):
    one, two = r"D:\Work\Hartleys", r"D:\Work\Hartleys 2025"
    scene.use(make_map(confirmed=[(H_ID, "Hartleys", [one, two])]))
    _chat(scene, "Engagement letter", one, hours_ago=2)
    _chat(scene, "Bank feed fix", two, hours_ago=3)
    _chat(scene, "Holiday plan", r"D:\Work\Misc", hours_ago=1)
    told = _block(scene, query="what did we last do for Hartleys?")
    assert f"Engagement letter, for Hartleys, in {one}" in told, told
    assert f"Bank feed fix, for Hartleys, in {two}" in told
    assert "Holiday plan" not in told
    assert len(_lines(told)) == 2


def test_the_named_path_takes_at_most_three_of_a_clients_newest(scene):
    scene.use(make_map(confirmed=[(H_ID, "Hartleys", [H])]))
    for i in range(5):
        _chat(scene, f"Piece {i}", H + rf"\{2020 + i}", hours_ago=i + 1)
    told = _block(scene, query="anything new for Hartleys?")
    assert [ln.split("] ", 1)[1].split(",")[0] for ln in _lines(told)] == ["Piece 0", "Piece 1", "Piece 2"]


def test_a_recap_stays_in_its_exact_folder_within_one_client(scene):
    scene.use(make_map(confirmed=[(H_ID, "Hartleys", [H])]))
    march = _chat(scene, "March reconciliation", H + r"\2026", hours_ago=2)
    feed = _chat(scene, "Bank feed", H + r"\2025", hours_ago=3)
    _recap(scene, march, "Decided: cash basis for 2026")
    _recap(scene, feed, "Decided: the feed starts in May")
    told = _block(scene, folder=H + r"\2026")
    assert "  Decided: cash basis for 2026" in told and B.RECAP_NOTE in told
    assert "Bank feed" in told and "the feed starts in May" not in told, told
    at_root = _block(scene, folder=H)
    assert "March reconciliation" in at_root and "Bank feed" in at_root
    assert "Decided:" not in at_root and B.RECAP_NOTE not in at_root


def test_same_client_other_folders_are_titles_only_and_withhold_local_only(firm):
    _chat(firm, "Private notes", H2, local_only=True, hours_ago=1)
    _run(firm, "Import the statements", H2, summary="Imported 212 lines.", hours_ago=6)
    _chat(firm, "Old scans", H2, hours_ago=40)
    _chat(firm, "Oldest scans", H2, hours_ago=60)
    told = _block(firm, folder=H + r"\2026", query="hello")
    head = "In Hartleys' other folders, newest first:"
    assert head in told, told
    part = told.split(head, 1)[1]
    assert [ln.split("] ", 1)[1] for ln in _lines(part)] == [
        f"Payroll setup, in {H2}", f"Import the statements, in {H2}", f"Old scans, in {H2}",
    ]  # titles only, three at most, newest first
    assert "payroll-2026.xlsx" not in told and "Result:" not in told
    assert "Private notes" not in told  # Local only stays home
    assert "Oldest scans" not in told
    assert "Supplier list" not in told and "Quarterly report" not in told
    with egress.local_only_scope(True):
        kept_home = B.work_block(firm.platform, folder=H + r"\2026", query="hello")
    assert "Private notes" in kept_home
    # Smith's chat hears none of it.
    assert head not in _block(firm, folder=S, query="hello")


def test_a_forgotten_client_folder_keeps_its_wall_and_shows_no_work(firm):
    assert firm.wm.forget(H2)["ok"]
    # The forgotten folder is still Hartleys' (the map keeps it): a chat there
    # is not a no-client chat that hears Smith's work.
    told = _block(firm, folder=H2, query="update the quarterly report for Smith")
    assert "Quarterly report" not in told and B.WALL_NOTE in told, told
    # Its work is not on record, so no other part shows it.
    assert "Payroll setup" not in _block(firm, folder=H + r"\2026", query="payroll setup")


def test_unreadable_client_tables_fail_closed(firm, monkeypatch):
    loose = _chat(firm, "Engagement letter", H, hours_ago=1)
    assert loose

    def unreadable(engine):
        raise ClientsUnreadable()

    monkeypatch.setattr(store, "load_map", unreadable)
    told = _block(firm, folder=H, query="update the quarterly report for Smith")
    assert "Engagement letter" in told  # exactly this folder
    assert "March reconciliation" not in told  # not even its subfolders
    assert "Quarterly report" not in told and "Bank letter template" not in told
    assert _block(firm, query="open the supplier list we cleaned up") == ""
    # Memory recall drops every conversation.
    fabric = _fabric(firm, [_chit(firm.dl_plain, "Bank letter template", "a plain letter")])
    with wall.turn_scope(wall.turn_wall(firm.engine, H, "letter")):
        assert fabric._chats("letter", 5) == []
    # The client block says nothing.
    assert client_block_mod.client_block(firm.platform, folder=H) == ""


# --------------------------------------------------------------------------- #
# Memory recall and history search
# --------------------------------------------------------------------------- #
class _Index:
    mode = "fts"

    def __init__(self, hits, engine=None):
        self.hits, self.engine = hits, engine

    def search(self, query, *, kinds=None, **kw):
        want = set([kinds] if isinstance(kinds, str) else kinds or [])
        return [h for h in self.hits if not want or h.kind in want]


def _chit(tid, title, snippet, kind="chat", score=0.9):
    return SearchHit(kind=kind, ref=tid, thread_id=tid, title=title, snippet=snippet, score=score)


def _rhit(sid, title, snippet, score=0.9):
    return SearchHit(kind="session", ref=sid, thread_id="", title=title, snippet=snippet, score=score)


def _fabric(scene, hits):
    return MemoryFabric(engine=scene.engine, search=_Index(hits, scene.engine))


@pytest.fixture()
def talks(firm):
    firm.hits = [
        _chit(firm.h_march, "March reconciliation", "Hartleys use cash basis for 2026"),
        _chit(firm.s_report, "Quarterly report", "Smith wants accrual"),
        _chit(firm.dl_plain, "Bank letter template", "a plain letter about the basis"),
    ]
    return firm


@pytest.mark.parametrize("how", ["set", "unset", "named", "unreadable"])
def test_memory_recall_does_not_bring_another_clients_chat_passage(talks, how):
    fabric = _fabric(talks, talks.hits)
    turns = {
        "set": wall.turn_wall(talks.engine, S, "what basis?"),
        "unset": None,
        "named": wall.turn_wall(talks.engine, "", "what basis did Hartleys pick?"),
        "unreadable": TurnWall(ok=False, folder_key=_k(S)),
    }
    expected = {
        "set": {talks.s_report, talks.dl_plain},
        "unset": {talks.dl_plain},
        "named": {talks.h_march, talks.dl_plain},
        "unreadable": set(),
    }[how]

    def recall():
        # Two stores: the jobs run in the fabric's pool, in a COPY of this context.
        return {h.ref for h in fabric.recall("basis", k=6, sources=["chats", "sessions"])}

    if how == "unset":
        assert recall() == expected
    else:
        with wall.turn_scope(turns[how]):
            assert recall() == expected
    # The owner's own screen is not walled.
    with wall.owner_screen():
        assert recall() == {h.ref for h in talks.hits}


def test_memory_recall_drops_every_chat_when_the_chats_folders_cannot_be_read(talks, monkeypatch):
    fabric = _fabric(talks, talks.hits)
    monkeypatch.setattr(wall, "thread_owners", lambda engine, cmap, ids: None)
    with wall.turn_scope(wall.turn_wall(talks.engine, S, "basis")):
        assert fabric._chats("basis", 5) == []


def test_memory_recall_does_not_bring_another_clients_run_summary(firm):
    proj = _project(firm, "proj_hartleys", H)
    h_run = _run(firm, "Prepare the 2026 return", H + r"\2026", summary="Used cash basis")
    s_run = _run(firm, "Prepare the 2025 return", S, summary="Used accrual")
    scratch = _run(firm, "Draft the letter", str(firm.home / "workspaces" / "sess_1"),
                   summary="Hartleys letter drafted", project_id=proj)
    plain = _run(firm, "Tidy the downloads", "", summary="Moved 12 files")
    hits = [_rhit(h_run, "Prepare the 2026 return", "Used cash basis"),
            _rhit(s_run, "Prepare the 2025 return", "Used accrual"),
            _rhit(scratch, "Draft the letter", "letter drafted"),
            _rhit(plain, "Tidy the downloads", "Moved 12 files")]
    fabric = _fabric(firm, hits)
    with wall.turn_scope(wall.turn_wall(firm.engine, S, "the return")):
        got = {h.ref for h in fabric._sessions("return", 6, set())}
    assert got == {s_run, plain}, got
    with wall.turn_scope(wall.turn_wall(firm.engine, H, "the return")):
        got = {h.ref for h in fabric._sessions("return", 6, set())}
    assert got == {h_run, scratch, plain}, got
    with wall.turn_scope(TurnWall(ok=False)):
        assert fabric._sessions("return", 6, set()) == []


def _search(tool, query, turn=None, *, screen=False):
    async def go():
        if screen:
            with wall.owner_screen():
                return await tool.execute({"query": query}, SimpleNamespace())
        with wall.turn_scope(turn):
            return await tool.execute({"query": query}, SimpleNamespace())

    return asyncio.run(go())


def test_history_search_is_walled_by_the_owners_words_not_the_models(talks):
    tool = HistorySearchTool(_Index(talks.hits, talks.engine))
    # The model words its query to name Hartleys; the owner never did.
    quiet = wall.turn_wall(talks.engine, "", "what did we decide about the basis?")
    out = _search(tool, "Hartleys cash basis", quiet)
    assert "Hartleys use cash basis" not in out.output and "Smith wants" not in out.output
    assert "a plain letter" in out.output
    assert out.data["count"] == 1 and out.data["left_out"] == 2
    assert "2 results from clients' work were left out of this chat" in out.output
    assert "Hartleys" not in out.output.split("\n")[-1]
    # The owner named Hartleys: its conversation may be found.
    asked = wall.turn_wall(talks.engine, "", "what did we decide for Hartleys?")
    out = _search(tool, "cash basis", asked)
    assert "Hartleys use cash basis" in out.output and "Smith wants" not in out.output
    # In Smith's folder, even the owner's own words naming Hartleys do not reach it.
    in_smith = wall.turn_wall(talks.engine, S, "what did Hartleys decide?")
    out = _search(tool, "Hartleys cash basis", in_smith)
    assert "Hartleys use cash basis" not in out.output and "Smith wants accrual" in out.output
    assert "1 result from other clients' work was left out of this client's chat." in out.output
    # Everything was another client's: said, never "nothing matched".
    only = HistorySearchTool(_Index(talks.hits[:1], talks.engine))
    out = _search(only, "Hartleys", in_smith)
    assert out.ok and out.data["count"] == 0 and "No past conversation matched" not in out.output
    assert out.output == "1 result from other clients' work was left out of this client's chat."
    # Unreadable: nothing, and it says why.
    out = _search(tool, "basis", TurnWall(ok=False))
    assert out.data["count"] == 0 and "could not be read" in out.output
    # The owner's own screen: everything.
    out = _search(tool, "basis", screen=True)
    assert out.data["count"] == 3 and "left_out" not in out.data


# --------------------------------------------------------------------------- #
# Owners of chats and runs
# --------------------------------------------------------------------------- #
def test_thread_and_session_owners_read_the_saved_folder_else_the_project_root(firm, monkeypatch):
    proj = _project(firm, "proj_smith", S)
    in_project = _chat(firm, "Smith letter", "", project_id=proj)
    loose = _chat(firm, "No folder", "")
    got = wall.thread_owners(firm.engine, firm.cmap, [firm.h_march, in_project, loose, "round_x", ""])
    assert got == {firm.h_march: H + r"\2026", in_project: S, loose: "", "round_x": ""}
    assert wall.thread_owners(firm.engine, firm.cmap, []) == {}
    assert wall.thread_owners(None, firm.cmap, ["x"]) is None
    in_h = _run(firm, "a", H)
    scratch_p = _run(firm, "b", str(firm.home / "workspaces" / "s1"), project_id=proj)
    scratch = _run(firm, "c", str(firm.home / "workspaces" / "s2"))
    got = wall.session_owners(firm.engine, firm.cmap, [in_h, scratch_p, scratch, "sess_gone"])
    assert got == {in_h: H, scratch_p: S, scratch: "", "sess_gone": ""}

    from iron_jarvis.core import db as core_db

    def broken(*a, **k):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(core_db, "session_scope", broken)
    assert wall.thread_owners(firm.engine, firm.cmap, ["x"]) is None
    assert wall.session_owners(firm.engine, firm.cmap, ["x"]) is None
    assert wall.tell_passages(firm.engine, NO_CLIENT, [(wall.PASSAGE_CHAT, "x", "", "")]) is None


# --------------------------------------------------------------------------- #
# The owner's own screens, and every caller's wall mode
# --------------------------------------------------------------------------- #
def test_the_owners_own_screens_are_not_walled(tmp_path):
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    app = create_app(str(tmp_path / "home"))
    client = TestClient(app)
    platform = app.state.platform
    seen: list[tuple[str, bool]] = []

    def recall(*a, **k):
        seen.append(("recall", wall.on_owner_screen()))
        return []

    platform.fabric.recall = recall
    index = platform.search_index
    real = index.search

    def search(*a, **k):
        seen.append(("history", wall.on_owner_screen()))
        return real(*a, **k)

    index.search = search
    assert client.get("/memory/recall", params={"q": "cash basis"}).status_code == 200
    assert seen == [("recall", True)], seen
    seen.clear()
    assert client.get("/search/all", params={"q": "cash basis for the return"}).status_code == 200
    assert ("recall", True) in seen and ("history", True) in seen, seen  # lanes in threads
    seen.clear()
    assert client.get("/search/history", params={"q": "cash basis"}).status_code == 200
    assert seen == [("history", True)], seen
    assert wall.on_owner_screen() is False


#: Every caller of memory recall and history search, and the wall it runs
#: under: "turn" = the ContextVar a lane or the agent runtime sets (unset is
#: the no-client rule); "owner_screen" = the owner's own screen, not walled.
WALL_MODES = {
    "agents/runtime.py": "turn",  # the run's ground(), inside the run's wall
    "daemon/chat_turn.py": "turn",  # the chat lanes' fabric hop
    "memory/recall.py": "turn",  # the model's recall tool
    "platform.py": "turn",  # registers history_search: a tool in a turn
    "daemon/routes/learning.py": "owner_screen",  # GET /memory/recall (the Memory page)
    "search/unified.py": "owner_screen",  # GET /search/all (the palette)
}


def test_every_recall_and_history_search_caller_runs_under_a_known_wall_mode():
    rx = re.compile(r"\b(?:fabric|_fab)\.(?:recall|ground)\b|\bhistory_search_tools\(|\bHistorySearchTool\(")
    found = set()
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        if rel in ("memory/fabric.py", "search/tools.py"):
            continue  # the definitions
        if rx.search(path.read_text(encoding="utf-8").replace("\r\n", "\n")):
            found.add(rel)
    assert found == set(WALL_MODES), found
    for rel, mode in WALL_MODES.items():
        if mode == "owner_screen":
            route = "daemon/routes/search.py" if rel == "search/unified.py" else rel
            text = (SRC / route).read_text(encoding="utf-8")
            assert "wall.owner_screen()" in text, route
    fabric = (SRC / "memory/fabric.py").read_text(encoding="utf-8")
    assert fabric.count("self._walled(") == 2  # chats and sessions
    tools = (SRC / "search/tools.py").read_text(encoding="utf-8")
    assert "wall.current()" in tools and "on_owner_screen()" in tools


def test_only_the_store_reads_client_preferences():
    allowed = {"workmap/models.py", "workmap/clients/store.py"}
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        if rel in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        assert "WorkClientPreference" not in text and "workclientpreference" not in text, rel


# --------------------------------------------------------------------------- #
# Text only, and each wall proven by removing it
# --------------------------------------------------------------------------- #
def test_wall_and_names_never_touch_the_disk(scene, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the disk was asked")

    for name in ("stat", "lstat", "listdir", "scandir"):
        monkeypatch.setattr(os, name, boom)
    for name in ("realpath", "exists", "isdir", "isfile", "abspath"):
        monkeypatch.setattr(os.path, name, boom)
    monkeypatch.setattr(pathlib.Path, "resolve", boom)
    for turn_folder, text in [(H + r"\2026", "for Smith"), (CLIENTS, "Hartleys"), ("", ""),
                              (H + ".ij-worktrees\\x", ""), (r"\\server\share\Clients\Lee", "")]:
        t = wall.turn_wall(scene.engine, turn_folder, text)
        assert t.ok
        for what, _turn_row, row, _told in TABLE:
            wall.may_tell(t, scene.cmap, folder=row[0], title=row[1], files=[row[0] + r"\a.xlsx"],
                          snippet="Hartleys")
    # Builder A's text helpers.
    for folder in (H + r"\2026", r"\\server\share\Clients\Lee", "~/Clients/Ng", ""):
        names.path_parts(folder)
        names.name_key(folder)
        names.signature(folder)
        names.is_client_parent(folder)
        names.is_name_segment(folder)
        names.named_in(folder, scene.cmap.names)
        for cmap in (store.EMPTY_MAP, store.ClientMap(confirmed=scene.cmap.confirmed,
                                                      waiting=scene.cmap.waiting)):
            cmap.client_of(folder)
            cmap.waiting_of(folder)
            cmap.provisional_of(folder)
            cmap.spans(folder)


def _leaks(scene) -> list[str]:
    """Each surface asked, in Smith's folder, for Hartleys' work."""
    leaks = []
    told = _block(scene, folder=scene.smith, query="Hartleys March reconciliation please")
    if "March reconciliation" in told:
        leaks.append("work block")
    hits = [_chit(scene.h_march, "March reconciliation", "the March figures"),
            _chit(scene.s_report, "Quarterly report", "the quarter's figures")]
    with wall.turn_scope(wall.turn_wall(scene.engine, scene.smith, "figures")):
        if scene.h_march in {h.ref for h in _fabric(scene, hits)._chats("figures", 5)}:
            leaks.append("memory recall")
    out = _search(HistorySearchTool(_Index(hits, scene.engine)), "figures",
                  wall.turn_wall(scene.engine, scene.smith, "figures"))
    if "the March figures" in out.output:
        leaks.append("history search")
    return leaks


@pytest.fixture()
def unplaced(scene):
    """Hartleys and Smith in folders with NO Clients-type parent, titles that
    name nobody: only the folder records keep them apart."""
    hart, scene.smith = r"D:\Work\Hartleys", r"D:\Work\Smith"
    scene.use(make_map(confirmed=[(H_ID, "Hartleys", [hart]), (S_ID, "Smith", [scene.smith])]))
    scene.h_march = _chat(scene, "March reconciliation", hart, hours_ago=2)
    scene.s_report = _chat(scene, "Quarterly report", scene.smith, hours_ago=3)
    return scene


def test_each_wall_goes_red_without_it(unplaced, monkeypatch):
    assert _leaks(unplaced) == []
    with monkeypatch.context() as m:
        m.setattr(wall, "may_tell", lambda *a, **k: True)
        assert _leaks(unplaced) == ["work block", "memory recall", "history search"]
    with monkeypatch.context() as m:
        m.setattr(store.ClientMap, "client_of", lambda self, folder: None)
        assert _leaks(unplaced) == ["work block", "memory recall", "history search"]
    assert _leaks(unplaced) == []


# --------------------------------------------------------------------------- #
# Builder A's record: its store, names and client block, nothing faked
# --------------------------------------------------------------------------- #
SD_ID = "client_00000000000d"  # Smith Dental
NAMED_CASES = [
    ("what did we last do for Hartleys?", {H_ID}),
    ("Hartley's 2026 return", {H_ID}),
    ("Hartleys wants cash basis", {H_ID}),
    ("the Hartley file", {H_ID}),
    ("what did we do for hartleys?", set()),  # a lower-case single word names nobody
    ("Invoices: how do I number them?", set()),
    ("Hartleyson Corp called", set()),  # a whole word only
    ("smith dental wants a call", {SD_ID}),  # several words: any case
    ("Smith called about dental", set()),  # not side by side
    ("HRT wants accrual", {H_ID}),  # an alias
]


@pytest.mark.parametrize("text, expected", NAMED_CASES, ids=[c[0] for c in NAMED_CASES])
def test_named_in_both_ways(text, expected):
    table = {
        H_ID: ("Hartleys", names.signature("Hartleys"), (names.signature("HRT"),)),
        SD_ID: ("Smith Dental", names.signature("Smith Dental"), ()),
    }
    assert names.named_in(text, table) == frozenset(expected)


def _app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon import chat_turn
    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.terminals.backend import FakeBackend

    monkeypatch.setattr(chat_turn, "GROUNDING_SOFT_S", 60.0)
    monkeypatch.setattr("iron_jarvis.terminals.session.default_backend", lambda: FakeBackend())
    app = create_app(str(tmp_path / "home"))
    return app, TestClient(app), app.state.platform


def _real_client(platform, folder, name) -> str:
    """A confirmed client made by builder A's store (an owner-made client)."""
    Path(folder).mkdir(parents=True, exist_ok=True)
    got = store.create(platform, folder=str(folder), name=name)
    return got["client"]["id"]


def _keep(platform, client_id, text, **kw):
    kw.setdefault("origin", "typed")
    return store.keep_preference(platform, client_id, text, **kw)


def _capture(platform, seen: list):
    from iron_jarvis.providers.adapters.base import LLMResponse
    from iron_jarvis.providers.router import RouteResult

    async def fake_complete(*, provider=None, model=None, system, messages, tools=None, task_class=None, **kw):
        seen.append(system)
        return RouteResult(LLMResponse(text="Done."), "mock", "mock")

    async def fake_stream(*, provider=None, model=None, system, messages, tools=None, session_id=None,
                          task_class=None, **kw):
        seen.append(system)
        yield {"type": "text", "text": "Done."}
        yield {"type": "final", "response": LLMResponse(text="Done."), "provider": "mock", "model": "mock"}

    platform.router.complete = fake_complete
    platform.router.stream = fake_stream


def _both_lanes(client, platform, text, folder) -> list[str]:
    seen: list[str] = []
    _capture(platform, seen)
    body = {"messages": [{"role": "user", "content": text}], "auto_tools": False}
    if folder:
        body["workspace_dir"] = str(folder)
    r = client.post("/chat", json=body)
    assert r.status_code == 200, r.text
    post_system = seen[-1]
    r = client.post("/chat/stream", json=body)
    assert r.status_code == 200, r.text
    return [post_system, seen[-1]]


_PREF = "Use cash basis for every Hartleys return."
REACH = [
    ("Hartleys", "{h}", True),
    ("a year folder", "{h}/2026", True),
    ("a folder under it", "{h}/2026/payroll", True),
    ("its coding copy", "{h}.ij-worktrees/fix-1", True),
    ("Smith's folder", "{s}/2025", False),
    ("the Clients folder", "{c}", False),
    ("a look-alike", "{c}/Hartleys 2026 old", False),
    ("a look-alike 2", "{c}/Hartleys-old", False),
    ("another Hartleys folder", "{o}/Hartleys", False),
    ("no folder", "", False),
]


@pytest.mark.parametrize("what, where, told", REACH, ids=[r[0] for r in REACH])
def test_a_client_preference_reaches_only_its_own_folders_in_both_lanes(tmp_path, monkeypatch,
                                                                       what, where, told):
    app, client, platform = _app(tmp_path, monkeypatch)
    c = tmp_path / "Clients"
    hid = _real_client(platform, c / "Hartleys", "Hartleys")
    _real_client(platform, c / "Smith", "Smith")
    _keep(platform, hid, _PREF)
    folder = where.format(h=c / "Hartleys", s=c / "Smith", c=c, o=tmp_path / "Other")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
    for system in _both_lanes(client, platform, "what should I do next?", folder):
        assert (_PREF in system) is told, (what, folder)


def test_a_declined_clients_preference_reaches_no_lane(tmp_path, monkeypatch):
    app, client, platform = _app(tmp_path, monkeypatch)
    hart = tmp_path / "Clients" / "Hartleys"
    hid = _real_client(platform, hart, "Hartleys")
    _keep(platform, hid, _PREF)
    store.decline(platform, hid)
    for system in _both_lanes(client, platform, "what should I do next?", hart):
        assert _PREF not in system and "# Client:" not in system


def _spy_adapters(platform, seen: dict):
    real_get = platform.providers.get

    def spy_get(p, m=None):
        adapter = real_get(p, m)
        real_complete = adapter.complete

        async def spy(*, system, messages, tools, **kw):
            seen.setdefault("systems", []).append(system)
            return await real_complete(system=system, messages=messages, tools=tools, **kw)

        adapter.complete = spy
        return adapter

    platform.providers.get = spy_get


def test_agent_run_and_terminal_assist_get_only_their_own_clients_block(tmp_path, monkeypatch):
    app, client, platform = _app(tmp_path, monkeypatch)
    c = tmp_path / "Clients"
    hid = _real_client(platform, c / "Hartleys", "Hartleys")
    _real_client(platform, c / "Smith", "Smith")
    _keep(platform, hid, _PREF)
    for sub in (c / "Hartleys" / "2026", c / "Smith" / "2025"):
        sub.mkdir(parents=True, exist_ok=True)
    seen: dict = {}
    _spy_adapters(platform, seen)
    for folder, told in ((c / "Hartleys" / "2026", True), (c / "Smith" / "2025", False)):
        seen.clear()
        r = client.post("/sessions", json={"task": "prepare the return", "wait": True,
                                           "workspace_root": str(folder)})
        assert r.status_code == 200, r.text
        assert seen.get("systems"), "the run made no model call"
        assert any(_PREF in s for s in seen["systems"]) is told, ("agent run", folder)
        seen.clear()
        term = client.post("/terminals", json={"cwd": str(folder)}).json()
        r = client.post(f"/terminals/{term['id']}/ai", json={"prompt": "what happened?"})
        assert r.status_code == 200, r.text
        assert (_PREF in seen["systems"][-1]) is told, ("terminal help", folder)


def test_client_preferences_never_reach_a_global_reader(tmp_path, monkeypatch):
    from iron_jarvis.learning.preferences import list_preferences
    from iron_jarvis.learning.tools import RecallLessonsTool
    from iron_jarvis.memory.fabric import _tokens
    from iron_jarvis.memory.overview import memory_overview
    from iron_jarvis.profile import share

    app, client, platform = _app(tmp_path, monkeypatch)
    hid = _real_client(platform, tmp_path / "Clients" / "Hartleys", "Hartleys")
    _keep(platform, hid, _PREF)
    platform.learning.note_preference("from now on, answer shorter")  # a global one, for contrast
    learning = platform.learning

    def clean(blob: str, where: str) -> None:
        assert "cash basis" not in blob, where

    clean(learning.apply_to_prompt("SYS"), "apply_to_prompt")
    clean(" ".join(r.text for r in learning.lessons(scope=None, limit=500)), "lessons")

    class _Ctx:
        session_id = "s"

    for args in ({"limit": 50}, {"limit": 50, "scope": "user"}, {"limit": 50, "scope": "client"}):
        clean(asyncio.run(RecallLessonsTool(learning).execute(args, _Ctx())).output or "",
              f"recall_lessons {args}")
    fabric = MemoryFabric(learning=learning)
    clean(" ".join(h.snippet for h in fabric._lessons(20, _tokens("cash basis hartleys return"))),
          "memory recall lessons")
    clean(json.dumps(share.render_content(platform.engine)), "the Build share")
    listed = list_preferences(platform.engine)
    for part in ("kept", "suggested", "never"):
        clean(json.dumps(listed.get(part) or [], default=str), f"list_preferences {part}")
    clean(json.dumps(memory_overview(platform), default=str), "the memory overview")
    clean(client.get("/lessons", params={"limit": 100}).text, "GET /lessons")
    clean(client.get("/lessons", params={"limit": 100, "scope": "client"}).text, "GET /lessons client")


def test_a_proposed_client_preference_reaches_no_prompt(tmp_path, monkeypatch):
    from iron_jarvis.core.ids import new_id
    from iron_jarvis.workmap.models import WorkClientPreference

    app, client, platform = _app(tmp_path, monkeypatch)
    hart = tmp_path / "Clients" / "Hartleys"
    hid = _real_client(platform, hart, "Hartleys")
    _keep(platform, hid, _PREF)
    with session_scope(platform.engine) as db:
        db.add(WorkClientPreference(id=new_id("cpref"), client_id=hid, text="Proposed: use accrual.",
                                    status="proposed"))
        db.commit()
    assert [p["text"] for p in store.preferences(platform.engine, hid)] == [_PREF]
    told = client_block_mod.client_block(platform, folder=str(hart), local=False)
    assert _PREF in told and "accrual" not in told
    for system in _both_lanes(client, platform, "next step?", hart):
        assert _PREF in system and "accrual" not in system


def test_a_local_only_client_preference_is_withheld_from_a_cloud_turn_and_told_to_a_local_turn(
        tmp_path, monkeypatch):
    app, client, platform = _app(tmp_path, monkeypatch)
    hart = tmp_path / "Clients" / "Hartleys"
    hid = _real_client(platform, hart, "Hartleys")
    _keep(platform, hid, "Cloud is fine for this one.")
    _keep(platform, hid, "Kept on this computer: the partner's salary split.", origin="said",
          local_only=True)
    cloud = client_block_mod.client_block(platform, folder=str(hart), local=False)
    assert "Cloud is fine" in cloud and "salary split" not in cloud
    assert "salary split" not in client_block_mod.client_block(platform, folder=str(hart))
    with egress.local_only_scope(True):
        assert "salary split" in client_block_mod.client_block(platform, folder=str(hart))
    assert "salary split" in client_block_mod.client_block(platform, folder=str(hart), local=True)


def test_a_preference_whose_source_chat_became_local_only_is_withheld_at_once(tmp_path, monkeypatch):
    app, client, platform = _app(tmp_path, monkeypatch)
    hart = tmp_path / "Clients" / "Hartleys"
    hid = _real_client(platform, hart, "Hartleys")
    with session_scope(platform.engine) as db:
        rec = ChatThreadRecord(title="Hartleys basis", messages_json="[]",
                               setup_json=json.dumps({"workspace_dir": str(hart)}))
        db.add(rec)
        db.commit()
        tid = rec.id
    _keep(platform, hid, _PREF, origin="said",
          evidence=[{"quote": "Hartleys wants cash basis", "thread_id": tid, "where": "Hartleys",
                     "at": utcnow().isoformat(), "link": f"/chat?thread={tid}"}])
    assert _PREF in client_block_mod.client_block(platform, folder=str(hart), local=False)
    with session_scope(platform.engine) as db:
        rec = db.get(ChatThreadRecord, tid)
        rec.setup_json = json.dumps({"workspace_dir": str(hart), "local_only": True})
        db.add(rec)
        db.commit()
    assert _PREF not in client_block_mod.client_block(platform, folder=str(hart), local=False)


def test_events_carry_no_client_name(tmp_path, monkeypatch):
    app, client, platform = _app(tmp_path, monkeypatch)
    c = tmp_path / "Clients"
    hid = _real_client(platform, c / "Hartleys", "Hartleys")
    _keep(platform, hid, _PREF)
    scans = tmp_path / "Scans" / "Hartleys"
    scans.mkdir(parents=True, exist_ok=True)
    store.add_folder(platform, hid, str(scans))
    store.rename(platform, hid, "Hartleys Farm")
    store.decline(platform, hid)
    store.restore(platform, hid)
    events = [e for e in platform.event_bus.history if str(e.type).startswith("client.")]
    assert {str(e.type) for e in events} >= {"client.confirmed", "client.changed", "client.declined"}
    for e in events:
        blob = json.dumps(e.payload, default=str)
        assert "Hartley" not in blob and "Clients" not in blob and "cash basis" not in blob, (e.type, blob)
