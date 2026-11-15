"""Clients (v1.499.0, vision plan leap 3, Phase 3): NOTICING names.

Iron Jarvis notices names in the work record's own folder paths that look
like clients, with NO model call and NO disk read, and asks about each ONCE:
under one reply, and while it waits, on the Clients page. Pinned here:

* the ``LAYOUTS`` table, both ways (what is asked, what never is);
* a pass calls no model and touches no disk;
* a No is final for every spelling, Ask again puts it back;
* declining a container does not silence the client inside it;
* minting is capped (30 waiting), locked (two passes at once), refreshed and
  withdrawn;
* the question rides one reply only, and only with the switch on;
* nothing inside a confirmed client is asked about; forgotten and archived
  work is never evidence;
* the loop runs on an injected clock; reads never mint.

All names and paths are made up (Hartleys, Smith, C:\\Clients\\…): the paths
are compared as text and never looked up.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import select

from iron_jarvis.core.config import Config
from iron_jarvis.core.db import init_db, make_engine, session_scope
from iron_jarvis.core.ids import new_id, utcnow
from iron_jarvis.core.models import ChatThreadRecord
from iron_jarvis.workmap import keys
from iron_jarvis.workmap.clients import MAX_WAITING, block, detect, names, page, store
from iron_jarvis.workmap.index import work_map
from iron_jarvis.workmap.models import WorkClient

NOW = datetime(2026, 11, 14, 12, 0, 0)


class Bus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def publish(self, type, payload=None, session_id=None):  # noqa: A002
        self.events.append((type, dict(payload or {})))


@pytest.fixture()
def platform(tmp_path, monkeypatch):
    monkeypatch.setitem(store._LOOP, "loop", None)  # no daemon loop: nothing after an answer
    eng = make_engine(tmp_path / "clients.db")
    init_db(eng)
    home = tmp_path / "home"
    home.mkdir()
    p = SimpleNamespace(engine=eng, config=Config(home=home, project_root=home), event_bus=Bus())
    yield p
    eng.dispose()


def _chat(p, folder, *, title="Work", hours_ago=1.0, archived=False, local_only=False, docs=()):
    setup = {"workspace_dir": str(folder)}
    if local_only:
        setup["local_only"] = True
    messages = [{"role": "user", "content": "hi"},
                {"role": "assistant", "content": "done", "documents": [str(d) for d in docs]}]
    with session_scope(p.engine) as db:
        rec = ChatThreadRecord(
            title=title, messages_json=json.dumps(messages), setup_json=json.dumps(setup),
            updated_at=utcnow() - timedelta(hours=hours_ago),
            archived_at=utcnow() if archived else None,
        )
        db.add(rec)
        db.commit()
        return rec.id


def _chats(p, folder, n=1, **kw):
    return [_chat(p, folder, hours_ago=1.0 + i, **kw) for i in range(n)]


def _built(p):
    work_map(p.engine, p.config.home).refresh(force=True)


def _rows(p, status=None):
    with session_scope(p.engine) as db:
        stmt = select(WorkClient)
        if status:
            stmt = stmt.where(WorkClient.status == status)
        out = list(db.exec(stmt))
        for r in out:
            db.expunge(r)
        return out


def _sigs(p, status="proposed"):
    return {r.signature for r in _rows(p, status)}


# --------------------------------------------------------------------------- #
# The LAYOUTS table, both ways (the pure pass)
# --------------------------------------------------------------------------- #
def _row(folder, work=1, *, code=0, local=0, hours_ago=1):
    return (keys.folder_key(folder), folder, work, NOW - timedelta(hours=hours_ago), local, code)


ASKS = "asks"
NEVER = "never"

LAYOUTS = [
    # --- asked -----------------------------------------------------------
    ("clients_by_year", [_row(r"C:\Clients\Hartleys\2025"), _row(r"C:\Clients\Hartleys\2026")], {"hartley"}, {}),
    ("clients_two_chats", [_row(r"C:\Clients\Hartleys", 2)], {"hartley"}, {}),
    ("year_first", [_row(r"C:\Tax\2024\Hartleys", 2), _row(r"C:\Tax\2025\Hartleys")], {"hartley"}, {}),
    ("two_spellings", [_row(r"C:\Users\Pat\OneDrive\Clients\Hartley's 2025"),
                       _row(r"C:\Users\Pat\OneDrive\Clients\Hartleys 2026")], {"hartley"}, {}),
    ("work_words_under_names", [_row(r"C:\Work\Hartleys"), _row(r"C:\Work\Hartleys\Payroll", 2),
                                _row(r"C:\Work\Smith"), _row(r"C:\Work\Smith\Payroll", 2)],
     {"hartley", "smith"}, {}),
    ("short_name_under_clients", [_row(r"C:\Clients\Lee", 2)], {"lee"}, {}),
    ("shared_subfolder_word", [_row(r"C:\Work\Hartleys"), _row(r"C:\Work\Hartleys\Board Minutes", 2),
                               _row(r"C:\Work\Smith"), _row(r"C:\Work\Smith\Board Minutes", 2)],
     {"hartley", "smith"}, {}),
    ("container_is_not_the_client", [_row(r"D:\Acme\Hartleys"), _row(r"D:\Acme\Hartleys\2025", 2),
                                     _row(r"D:\Acme\Smith"), _row(r"D:\Acme\Smith\2025", 2)],
     {"hartley", "smith"}, {}),
    ("nested_name_is_the_outer_ones", [_row(r"D:\Hartleys"), _row(r"D:\Hartleys\Hartleys Farm\2025", 2),
                                       _row(r"D:\Hartleys\Hartleys Farm\2026"), _row(r"D:\Hartleys\Notes")],
     {"hartley"}, {}),
    # --- never asked ------------------------------------------------------
    ("this_repository", [_row(r"C:\Users\Pat\Projects\Iron-Jarvis", 6, code=6),
                         _row(r"C:\Users\Pat\Projects\Iron-Jarvis\dashboard", 3, code=3)], set(), {}),
    ("a_code_project", [_row(r"C:\Users\Pat\Projects\ClosePilot", 5, code=5),
                        _row(r"C:\Users\Pat\Projects\ClosePilot\server", 2, code=2)], set(), {}),
    ("a_code_project_in_chats", [_row(r"C:\Users\Pat\Projects\ClosePilot", 4),
                                 _row(r"C:\Users\Pat\Projects\ClosePilot\server", 2)], set(),
     {"files": [(keys.folder_key(r"C:\Users\Pat\Projects\ClosePilot"),
                 json.dumps([{"path": f"C:/x/f{i}.ts"} for i in range(6)]))]}),
    ("downloads", [_row(r"C:\Users\Pat\Downloads", 40)], set(), {}),
    ("clients_alone", [_row(r"C:\Clients", 5)], set(), {}),
    ("a_year_alone", [_row(r"C:\Tax\2025", 5)], set(), {}),
    ("new_folder", [_row(r"C:\Users\Pat\Desktop\New folder", 4)], set(), {}),
    ("one_folder_only", [_row(r"D:\Hartleys", 5)], set(), {}),
    ("everywhere", [_row(r"D:\Sterling\2021"), _row(r"D:\Sterling\2022"), _row(r"D:\Sterling\2023"),
                    _row(r"D:\Sterling\2024"), _row(r"D:\Notes")], set(), {}),
    ("a_forgotten_folder", [_row(r"C:\Clients\Hartleys", 3)], set(),
     {"forgotten": [keys.folder_key(r"C:\Clients\Hartleys")]}),
    ("a_declined_name", [_row(r"C:\Clients\Hartleys LLC", 3)], set(), {"known": {"hartley": "declined"}}),
    ("inside_a_confirmed_client", [_row(r"D:\Work\Hartleys\Smith Trust\2025", 2),
                                   _row(r"D:\Work\Hartleys\Smith Trust\2026", 2)], set(),
     {"confirmed_keys": [keys.folder_key(r"D:\Work\Hartleys")]}),
]


@pytest.mark.parametrize("layout", LAYOUTS, ids=[x[0] for x in LAYOUTS])
def test_layouts_notice_exactly_these_names(layout):
    _id, rows, want, extra = layout
    got = detect.candidates(
        rows, extra.get("files", []),
        known=extra.get("known", {}),
        confirmed_keys=extra.get("confirmed_keys", []),
        forgotten=extra.get("forgotten", []),
    )
    assert {c.signature for c in got} == want, [(c.signature, c.name, c.rule) for c in got]


def test_the_layouts_say_who_and_where():
    year_first = detect.candidates(
        [_row(r"C:\Tax\2024\Hartleys", 2), _row(r"C:\Tax\2025\Hartleys")], [],
        known={}, confirmed_keys=[], forgotten=[])
    (c,) = year_first
    assert (c.name, c.rule, c.work, c.folders) == ("Hartleys", "several_folders", 3, 2)
    assert sorted(h.folder for h in c.homes) == [r"C:\Tax\2024\Hartleys", r"C:\Tax\2025\Hartleys"]
    under = detect.candidates([_row(r"C:\Clients\Hartleys\2025"), _row(r"C:\Clients\Hartleys\2026")], [],
                              known={}, confirmed_keys=[], forgotten=[])
    (u,) = under
    assert (u.name, u.rule, [h.folder for h in u.homes]) == ("Hartleys", "under_clients", [r"C:\Clients\Hartleys"])
    # A known WAITING name is still returned: the pass refreshes that row.
    again = detect.candidates([_row(r"C:\Clients\Hartleys", 2)], [], known={"hartley": "proposed"},
                              confirmed_keys=[], forgotten=[])
    assert [c.signature for c in again] == ["hartley"]
    # Strongest first: under a Clients folder, more homes, more work.
    ranked = detect.candidates(
        [_row(r"C:\Clients\Hartleys", 9), _row(r"C:\Clients\Smith", 2)], [],
        known={}, confirmed_keys=[], forgotten=[])
    assert [c.name for c in ranked] == ["Hartleys", "Smith"]


# --------------------------------------------------------------------------- #
# A pass: no model, no disk
# --------------------------------------------------------------------------- #
def test_notice_calls_no_model_and_never_touches_the_disk(platform, monkeypatch):
    from iron_jarvis.providers.router import ModelRouter

    _chats(platform, r"C:\Clients\Hartleys\2025", 2)
    _chats(platform, r"C:\Clients\Smith", 2)
    _built(platform)
    touched: list[tuple[str, str]] = []
    me = threading.get_ident()

    def watched(name, real):
        # Recorded on THIS thread only, and passed through: the test runner's
        # own threads use the disk too, and must keep working.
        def _call(*a, **k):
            if threading.get_ident() == me:
                touched.append((name, str(a[0]) if a else ""))
            return real(*a, **k)
        return _call

    async def no_model(*a, **k):
        touched.append(("model", ""))
        raise AssertionError("noticing called a model")

    monkeypatch.setattr(ModelRouter, "complete", no_model)
    monkeypatch.setattr(ModelRouter, "stream", no_model)
    for mod, attr in ((os, "stat"), (os, "lstat"), (os, "listdir"), (os, "scandir"), (os.path, "exists"),
                      (os.path, "isdir"), (os.path, "realpath"), (Path, "exists"), (Path, "resolve")):
        monkeypatch.setattr(mod, attr, watched(f"{getattr(mod, '__name__', mod)}.{attr}", getattr(mod, attr)))
    minted = detect.notice(platform, force=True)
    monkeypatch.undo()
    assert touched == []
    assert minted == 2 and _sigs(platform) == {"hartley", "smith"}
    # The event says WHICH rows, never a name.
    (etype, payload), = platform.event_bus.events
    assert etype == "client.suggested" and sorted(payload) == ["ids"]
    assert "Hartley" not in json.dumps(payload) and len(payload["ids"]) == 2


# --------------------------------------------------------------------------- #
# A No is final; Ask again
# --------------------------------------------------------------------------- #
def test_a_no_is_final_in_every_spelling(platform):
    _chats(platform, r"C:\Clients\Hartleys 2026", 2)
    _built(platform)
    assert detect.notice(platform, force=True) == 1
    (row,) = _rows(platform, "proposed")
    store.decline(platform, row.id)
    for folder in (r"D:\Clients\Hartley's 2026", r"E:\Clients\Hartley", r"F:\Customers\Hartleys LLC"):
        _chats(platform, folder, 3)
    _built(platform)
    assert detect.notice(platform, force=True) == 0
    assert _rows(platform, "proposed") == []
    assert [r.id for r in _rows(platform)] == [row.id]  # no twin, in any spelling


def test_ask_again_puts_it_back(platform):
    _chats(platform, r"C:\Clients\Hartleys", 2)
    _built(platform)
    detect.notice(platform, force=True)
    (row,) = _rows(platform, "proposed")
    assert store.question_for_turn(platform, folder=r"C:\Clients\Hartleys\2026", thread_id="t1")["id"] == row.id
    store.decline(platform, row.id)
    assert store.question_for_turn(platform, folder=r"C:\Clients\Hartleys", thread_id="t2") is None
    got = store.ask_again(platform, row.id)
    assert got["ok"] and got["client"] == {"id": row.id, "name": "Hartleys"}
    (back,) = _rows(platform, "proposed")
    assert back.id == row.id and back.asked_at is None
    # It waits again (asked again under a reply), and a pass never withdraws it.
    assert store.question_for_turn(platform, folder=r"C:\Clients\Hartleys", thread_id="t3")["id"] == row.id
    work_map(platform.engine, platform.config.home).forget(r"C:\Clients\Hartleys")
    detect.notice(platform, force=True)
    assert [r.id for r in _rows(platform, "proposed")] == [row.id]


def test_declining_a_container_does_not_silence_the_client_inside(platform):
    # D:\Firm holds only Hartleys: the OUTER name is asked first (nested rule).
    _chats(platform, r"D:\Firm\Hartleys\2025", 2)
    _chats(platform, r"D:\Firm\Hartleys\2026", 1)
    _built(platform)
    detect.notice(platform, force=True)
    assert _sigs(platform) == {"firm"}
    (firm,) = _rows(platform, "proposed")
    store.decline(platform, firm.id)
    detect.notice(platform, force=True)
    # The No blocks "Firm" only: the client inside it is asked about now.
    assert _sigs(platform) == {"hartley"}
    assert {r.signature for r in _rows(platform, "declined")} == {"firm"}


# --------------------------------------------------------------------------- #
# Minting: capped, locked, refreshed, withdrawn
# --------------------------------------------------------------------------- #
SURNAMES = """
Abbott Barker Carver Dalton Ellison Fenwick Garland Hartley Ingram Jessup Kendall Langley
Merritt Norwood Oakley Pembroke Quimby Radcliffe Sutton Thornton Underwood Vance Whitaker
Yardley Zeller Ashby Brantley Crowley Dunmore Everly Fairchild Greer Holloway Iverson Jarrett
""".split()


def test_minting_is_capped_locked_refreshed_and_withdrawn(platform):
    assert len(SURNAMES) == 35
    for i, name in enumerate(SURNAMES):
        _chats(platform, rf"C:\Clients\{name}", 2 if i else 9)  # Abbott is the strongest
    _built(platform)
    errors: list[BaseException] = []
    minted: list[int] = []

    def run():
        try:
            minted.append(detect.notice(platform, force=True))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert errors == []
    assert sorted(minted) == [0, MAX_WAITING]  # the lock: the second pass found them all minted
    waiting = _rows(platform, "proposed")
    assert len(waiting) == MAX_WAITING == 30
    assert "abbott" in {r.signature for r in waiting}
    ids = [i for t, p in platform.event_bus.events if t == "client.suggested" for i in p["ids"]]
    assert sorted(ids) == sorted(r.id for r in waiting)

    # Refreshed: more work in a waiting name's folder moves its evidence.
    sutton = next(r for r in waiting if r.signature == "sutton")
    assert json.loads(sutton.evidence_json)["work"] == 2
    _chats(platform, r"C:\Clients\Sutton\2026", 3)
    _built(platform)
    assert detect.notice(platform, force=True) == 0  # still capped
    again = next(r for r in _rows(platform, "proposed") if r.id == sutton.id)
    assert json.loads(again.evidence_json)["work"] == 5 and again.strength > sutton.strength

    # Withdrawn: a waiting name that no longer qualifies leaves, and the free
    # place goes to the next strongest name.
    left_out = {r.signature for r in _rows(platform)} ^ {names.signature(n) for n in SURNAMES}
    assert len(left_out) == 5
    work_map(platform.engine, platform.config.home).forget(r"C:\Clients\Sutton")
    assert detect.notice(platform, force=True) == 1
    after = _rows(platform, "proposed")
    assert len(after) == MAX_WAITING and sutton.id not in {r.id for r in after}
    assert len({r.signature for r in after} & left_out) == 1


def test_a_pass_is_throttled_and_off_with_the_switch(platform):
    _chats(platform, r"C:\Clients\Hartleys", 2)
    _built(platform)
    platform.config.notice_clients = False
    assert detect.notice(platform, force=True) == 0 and _rows(platform) == []
    platform.config.notice_clients = True
    assert detect.notice(platform) == 1
    _chats(platform, r"C:\Clients\Smith", 2)
    _built(platform)
    assert detect.notice(platform) == 0  # inside the throttle
    assert detect.notice(platform, force=True) == 1


def test_a_pass_that_cannot_read_says_so(platform, monkeypatch):
    from iron_jarvis.workmap.clients import ClientsUnreadable

    def broken(engine):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(detect, "_read", broken)
    with pytest.raises(ClientsUnreadable):
        detect.notice(platform, force=True)


# --------------------------------------------------------------------------- #
# The question under ONE reply
# --------------------------------------------------------------------------- #
def test_the_question_rides_one_reply_only(platform):
    _chats(platform, r"C:\Clients\Hartleys\2025", 2)
    _chats(platform, r"C:\Clients\Hartleys\2026", 1)
    _chats(platform, r"C:\Clients\Smith", 2)
    _built(platform)
    detect.notice(platform, force=True)
    hartleys = next(r for r in _rows(platform, "proposed") if r.signature == "hartley")
    got = store.question_for_turn(platform, folder=r"C:\Clients\Hartleys\2026\payroll", thread_id="t1")
    assert got == {"id": hartleys.id, "name": "Hartleys", "folders": 2, "work": 3}
    # Asked once: never under another reply, in any of its folders.
    assert store.question_for_turn(platform, folder=r"C:\Clients\Hartleys", thread_id="t2") is None
    assert store.question_for_turn(platform, folder=r"C:\Clients\Hartleys\2025", thread_id="t1") is None
    # A folder that is no waiting name's home asks nothing.
    assert store.question_for_turn(platform, folder=r"C:\Clients", thread_id="t3") is None
    assert store.question_for_turn(platform, folder=r"D:\Elsewhere", thread_id="t3") is None
    # Smith's own folder still gets Smith's question, once.
    assert store.question_for_turn(platform, folder=r"C:\Clients\Smith", thread_id="t4")["name"] == "Smith"
    assert store.question_for_turn(platform, folder=r"C:\Clients\Smith", thread_id="t4") is None


def test_the_question_is_only_for_dashboard_turns_and_the_switch(platform, monkeypatch):
    _chats(platform, r"C:\Clients\Hartleys", 2)
    _built(platform)
    detect.notice(platform, force=True)
    # A turn with no folder (the phone, the round table) is asked nothing.
    assert store.question_for_turn(platform, folder="", thread_id="t1") is None
    # The switch off: nothing is asked, and the row is not used up.
    platform.config.notice_clients = False
    assert store.question_for_turn(platform, folder=r"C:\Clients\Hartleys", thread_id="t1") is None
    platform.config.notice_clients = True
    assert _rows(platform, "proposed")[0].asked_at is None
    # A noticing pass holding the lock: no question (the lane gives up at 1 s,
    # and a question picked after that would be marked asked, never shown).
    held, release = threading.Event(), threading.Event()

    def busy():
        with store.NOTICE_LOCK:
            held.set()
            release.wait(10)

    t = threading.Thread(target=busy)
    t.start()
    held.wait(10)
    try:
        assert store.question_for_turn(platform, folder=r"C:\Clients\Hartleys", thread_id="t1") is None
    finally:
        release.set()
        t.join(10)
    assert _rows(platform, "proposed")[0].asked_at is None
    # A failure is "no question", never an error in the turn.
    monkeypatch.setattr(store, "session_scope", lambda engine: 1 / 0)
    assert store.question_for_turn(platform, folder=r"C:\Clients\Hartleys", thread_id="t1") is None
    monkeypatch.undo()
    monkeypatch.setitem(store._LOOP, "loop", None)
    assert store.question_for_turn(platform, folder=r"C:\Clients\Hartleys", thread_id="t1")["name"] == "Hartleys"


# --------------------------------------------------------------------------- #
# Never asked: inside a confirmed client; forgotten and archived work
# --------------------------------------------------------------------------- #
def test_a_folder_inside_a_confirmed_client_is_never_asked(platform):
    store.create(platform, folder=r"D:\Work\Hartleys", name="Hartleys")
    _chats(platform, r"D:\Work\Hartleys\Smith Trust\2025", 2)
    _chats(platform, r"D:\Work\Hartleys\Smith Trust\2026", 2)
    _chats(platform, r"C:\Clients\Hartleys 2027", 2)  # the confirmed name, spelled anew
    _built(platform)
    assert detect.notice(platform, force=True) == 0
    assert _rows(platform, "proposed") == []
    # A waiting name inside a folder the owner then gives a client is withdrawn.
    _chats(platform, r"E:\Shared\Jones\2025", 2)
    _chats(platform, r"E:\Shared\Jones\2026", 1)
    _built(platform)
    detect.notice(platform, force=True)
    assert _sigs(platform) == {"jone"}
    smith = store.create(platform, folder=r"E:\Shared\Jones", name="Jones Family")
    assert smith["ok"] and _rows(platform, "proposed") == []


def test_forgotten_and_archived_work_is_never_evidence(platform):
    _chats(platform, r"C:\Clients\Smith", 3, archived=True)
    _chats(platform, r"C:\Clients\Jones", 3)
    _chats(platform, r"C:\Clients\Hartleys", 2)
    _built(platform)
    work_map(platform.engine, platform.config.home).forget(r"C:\Clients\Jones")
    detect.notice(platform, force=True)
    assert _sigs(platform) == {"hartley"}
    # The pure pass drops a forgotten folder's rows too (checked again).
    got = detect.candidates([_row(r"C:\Clients\Jones", 3)], [], known={}, confirmed_keys=[],
                            forgotten=[keys.folder_key(r"C:\Clients")])
    assert got == []


# --------------------------------------------------------------------------- #
# The loop: an injected clock, never raising
# --------------------------------------------------------------------------- #
def test_the_loop_runs_on_an_injected_clock(platform, monkeypatch):
    now = [1000.0]
    slept: list[float] = []
    passes: list[float] = []
    ticks: list[tuple[bool, object]] = []
    offs: list[float] = []

    async def sleep(d):
        slept.append(d)
        # The second sleep returns EARLY (Windows timers): the loop waits on.
        now[0] += d if len(slept) != 2 else d / 2
        if len(slept) >= 6:
            raise asyncio.CancelledError

    def fake_notice(p, **k):
        passes.append(now[0])
        if len(passes) == 2:
            raise RuntimeError("database is locked")
        return 0

    monkeypatch.setattr(detect, "notice", fake_notice)

    async def main():
        task = asyncio.create_task(detect.clients_loop(
            platform, clock=lambda: now[0], sleep=sleep,
            on_tick=lambda ok, exc: ticks.append((ok, type(exc).__name__ if exc else None)),
            on_off=lambda: offs.append(now[0])))
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
    assert slept[0] == detect.FIRST_PASS_S == 120.0
    assert passes[0] == 1120.0  # 120 s after start, by the injected clock
    assert slept[1] == detect.EVERY_S and slept[2] == detect.EVERY_S / 2  # woke early: waited the rest
    assert passes[1] == 1120.0 + detect.EVERY_S
    assert ticks[:2] == [(True, None), (False, "RuntimeError")]  # a failed pass is a row, not a crash
    assert len(passes) >= 3

    # Switched off: no pass, the row is popped.
    platform.config.notice_clients = False
    passes.clear()
    slept.clear()
    asyncio.run(main())
    assert passes == [] and len(offs) >= 1


# --------------------------------------------------------------------------- #
# Reads never mint
# --------------------------------------------------------------------------- #
def test_reads_never_mint(platform):
    _chats(platform, r"C:\Clients\Hartleys", 3)
    _chats(platform, r"C:\Clients\Smith\2026", 2)
    _built(platform)
    store.load_map(platform.engine)
    page.clients_list(platform)
    page.client_of_folder(platform, r"C:\Clients\Hartleys")
    block.client_block(platform, folder=r"C:\Clients\Hartleys", local=False)
    store.question_for_turn(platform, folder=r"C:\Clients\Hartleys", thread_id="t1")
    store.preferences(platform.engine, new_id("client"))
    assert _rows(platform) == []
    assert platform.event_bus.events == []
    # A pass is what mints.
    assert detect.notice(platform, force=True) == 2
