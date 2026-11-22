"""FU-3a (Build › Code follow-up plan, item 3a, privacy): copies of the
user's files left behind when a delete fails.

A Build › Code session whose folder is in no git repository keeps copies of
the folder's files in a store of Iron Jarvis's own (v1.546.0,
``<home>/workspaces/code-checkpoints/<sid>.git``). When a file in that store
was held open as the session was deleted (an antivirus scan, a ``git gc``
still packing), the delete could not remove it, nothing ever tried again, and
nothing said so.

Now every store of a DELETED session is removed at the daemon's start and
again after a delete that could not remove one (after a wait, a few times); a
store whose row exists, archived or not, is never touched; nothing is removed
while the rows cannot be read or the database was set aside; how many were
removed is logged.

"Deleted" is not "its row is missing" (the review): a backup restored leaves
out the rows of the sessions made since, and undoing the restore brings them
back. A store is a deleted session's when its row is gone AND its delete left
its mark beside it, or the session's own folder (``hosted/<sid>``, which the
delete moves to the trash and a restore keeps) is gone too.

Real git, real routes, the real app start. "Held open" is the real thing on
Windows (an open file cannot be deleted there); elsewhere, where an open file
CAN be deleted, the delete of that one file is refused as Windows refuses it.
"""

from __future__ import annotations

import asyncio
import errno
import logging
import os
import shutil
from pathlib import Path

import pytest

from iron_jarvis import maintenance as M
from iron_jarvis.core import db as DB
from iron_jarvis.core.ids import utcnow
from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import checkpoints as CK
from iron_jarvis.hosted import panes as PN
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from iron_jarvis.hosted.events import session_dir
from iron_jarvis.hosted.models import HostedSessionRecord
from tests._build_app_helpers_v1409 import client, new_session, stop_all, until

SERVICE_LOG = "iron_jarvis.hosted.service"


def _service_lines(caplog, text: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == SERVICE_LOG and text in r.getMessage()]


def _marks(home: Path) -> list[str]:
    base = Path(home).joinpath(*CK.OWN_DIR)
    return sorted(p.name for p in base.glob("*" + CK.OWN_DELETED)) if base.is_dir() else []


@pytest.fixture()
def plain(tmp_path) -> Path:
    """A folder in NO git repository."""
    folder = tmp_path / "plain folder"
    folder.mkdir()
    (folder / "notes.txt").write_bytes(b"line one\r\nline two\r\n")
    (folder / "data").mkdir()
    (folder / "data" / "blob.bin").write_bytes(bytes(range(256)))
    return folder


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


def _home(app) -> Path:
    return Path(app.state.platform.config.home)


class Held:
    """A file in a store held the way another program holds it, until
    :meth:`release`. On Windows a real open file (Windows refuses to delete
    it). Elsewhere an open file CAN be deleted, so the delete of that one file
    is refused the way Windows refuses it, every time it is tried (a
    read-only folder would not do: the removal makes a folder writable when
    its own removal fails, which lets go of the file)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh = None
        self._real = None
        if os.name == "nt":
            self._fh = open(path, "rb")
        else:
            real = self._real = os.unlink
            name, full = path.name, os.path.abspath(path)

            def unlink(p, *args, dir_fd=None, **kwargs):
                held = os.path.basename(os.fspath(p)) == name and (
                    dir_fd is not None or os.path.abspath(os.fspath(p)) == full)
                if held:
                    raise PermissionError(errno.EACCES, "in use by another program (test)", os.fspath(p))
                return real(p, *args, dir_fd=dir_fd, **kwargs)

            os.unlink = unlink

    def release(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
        if self._real is not None:
            os.unlink = self._real
            self._real = None

    def __enter__(self) -> "Held":
        return self

    def __exit__(self, *_exc) -> None:
        self.release()


def _an_object_file(store_dir: Path) -> Path:
    """One of git's own (read-only) object files in the store."""
    return next(p for p in sorted((store_dir / "objects").rglob("*"))
                if p.is_file() and p.parent.name not in ("info", "pack"))


def _with_store(home: Path, sid: str, folder: Path) -> Path:
    """A real checkpoint of *folder* in session *sid*'s own store."""
    assert PN.record_turn_start(home, sid, str(folder), "t1", uid="2026-11-22T10:00:00Z")
    assert CK.has_own_store(home, sid)
    return CK.own_store(home, sid)


def _rows(app, folder: Path) -> dict[str, HostedSessionRecord]:
    """A live session, an archived one and a helper, each with a store."""
    engine = app.state.platform.engine
    live = store.create(engine, harness="grok", folder=str(folder), harness_session_id="x-1")
    archived = store.create(engine, harness="grok", folder=str(folder), harness_session_id="x-2")
    store.update(engine, archived.id, archived_at=utcnow())
    helper = store.create(engine, harness="grok", folder=str(folder), harness_session_id="x-3",
                          handed_from=live.id)
    out = {"live": live, "archived": archived, "helper": helper}
    for row in out.values():
        _with_store(_home(app), row.id, folder)
    return out


def _folder_trashed(home: Path, sid: str) -> None:
    """The session's own folder gone, as its delete leaves it (moved to the trash)."""
    shutil.rmtree(session_dir(home, sid))


def _orphan(app, folder: Path) -> Path:
    """A store a delete never removed (an earlier version's: no mark), its
    row and its session's own folder gone."""
    engine = app.state.platform.engine
    row = store.create(engine, harness="grok", folder=str(folder), harness_session_id="x-gone")
    path = _with_store(_home(app), row.id, folder)
    assert store.delete(engine, row.id)
    _folder_trashed(_home(app), row.id)
    return path


# --------------------------------------------------------------------------- #
# the plan's test: held open at the delete, gone once it is let go
# --------------------------------------------------------------------------- #


async def test_a_store_held_open_when_its_session_is_deleted_is_removed_once_the_file_is_let_go(
        app, plain, caplog):
    svc = S.service_for(app.state.platform)
    home, engine = _home(app), app.state.platform.engine
    kept = _rows(app, plain)
    waits: list[float] = []
    go = asyncio.Event()

    async def wait(seconds: float) -> None:  # the retry's wait, as the test lets it pass
        waits.append(seconds)
        await go.wait()

    svc._sleep = wait
    async with client(app) as c:
        sid = (await new_session(c, plain, harness="grok"))["id"]
        store_dir = _with_store(home, sid, plain)
        with Held(_an_object_file(store_dir)) as held:
            with caplog.at_level(logging.WARNING):
                r = await c.delete(f"/build/app/sessions/{sid}")
            assert r.status_code == 200, r.text
            assert store.get(engine, sid) is None
            # The delete could not remove it all, and that is said.
            assert store_dir.exists()
            assert any("could not be removed yet" in m for m in caplog.messages)
            # The delete marked it as a deleted session's (beside it, not in it).
            assert _marks(home) == [store_dir.name + CK.OWN_DELETED]
            task = svc._store_sweep
            assert task is not None and not task.done()
            held.release()  # the other program lets go
            with caplog.at_level(logging.INFO, logger=SERVICE_LOG):
                go.set()
                got = await asyncio.wait_for(task, 60)
        assert not store_dir.exists() and not CK.own_store_left(home, sid)
        assert got == CK.Swept(removed=1, left=0)
        assert waits == [S.STORE_RETRY_S[0]]
        assert any("removed 1 copy" in m for m in caplog.messages)
        assert _marks(home) == []  # (the mark went with the store)
        # A store whose session row exists, archived or a helper, is untouched.
        for row in kept.values():
            assert CK.has_own_store(home, row.id), row.id
        # A copy of files that are still in the folder is never trash.
        assert not list((home / "trash").rglob("code-checkpoints"))
    await stop_all(app)


async def test_a_delete_that_removed_its_store_starts_no_later_sweep(app, plain):
    svc = S.service_for(app.state.platform)
    home = _home(app)
    async with client(app) as c:
        sid = (await new_session(c, plain, harness="grok"))["id"]
        store_dir = _with_store(home, sid, plain)
        assert (await c.delete(f"/build/app/sessions/{sid}")).status_code == 200
        assert not store_dir.exists() and _marks(home) == []
        assert svc._store_sweep is None
        # ...nor does a session that never kept a store of its own.
        other = (await new_session(c, plain, harness="grok"))["id"]
        assert (await c.delete(f"/build/app/sessions/{other}")).status_code == 200
        assert svc._store_sweep is None
    await stop_all(app)


async def test_the_later_sweep_is_paced_and_stops_until_the_next_start(app, plain, caplog, monkeypatch):
    """Held for good: one sweep after each wait of STORE_RETRY_S (none at
    once: the delete itself just failed), then it stops (and says so); the
    next start removes it."""
    svc = S.service_for(app.state.platform)
    home = _home(app)
    waits: list[float] = []
    sweeps: list[int] = []
    real = S.sweep_left_stores

    def counted(h, e, **kw):
        sweeps.append(len(waits))  # how many waits had passed at each sweep
        return real(h, e, **kw)

    monkeypatch.setattr(S, "sweep_left_stores", counted)

    async def wait(seconds: float) -> None:
        waits.append(seconds)

    svc._sleep = wait
    async with client(app) as c:
        sid = (await new_session(c, plain, harness="grok"))["id"]
        store_dir = _with_store(home, sid, plain)
        with Held(_an_object_file(store_dir)) as held:
            assert (await c.delete(f"/build/app/sessions/{sid}")).status_code == 200
            with caplog.at_level(logging.WARNING, logger=SERVICE_LOG):
                got = await asyncio.wait_for(svc._store_sweep, 60)
            assert waits == list(S.STORE_RETRY_S)
            assert sweeps == [1, 2, 3]
            assert got == CK.Swept(removed=0, left=1) and store_dir.exists()
            # What each sweep says is true: tried again after the first two,
            # and after the last one only at the next start.
            lines = _service_lines(caplog, "could not be removed yet")
            assert len(lines) == 3, lines
            assert all("tried again later" in m for m in lines[:2]), lines
            assert "at the next start" in lines[2] and "later" not in lines[2], lines
            held.release()
        # The daemon's next start: the same sweep, at once.
        got = await S.sweep_stores_until_gone(home, app.state.platform.engine, sleep=wait)
        assert got == CK.Swept(removed=1, left=0) and not store_dir.exists()
        assert waits == list(S.STORE_RETRY_S)  # (removed at once: no wait)
    await stop_all(app)


async def test_a_second_failed_delete_starts_the_waits_again_and_shutdown_ends_them(app, plain):
    svc = S.service_for(app.state.platform)
    home = _home(app)
    forever = asyncio.Event()

    async def wait(_seconds: float) -> None:
        await forever.wait()

    svc._sleep = wait
    async with client(app) as c:
        a = (await new_session(c, plain, harness="grok"))["id"]
        b = (await new_session(c, plain, harness="grok"))["id"]
        dirs = [_with_store(home, a, plain), _with_store(home, b, plain)]
        with Held(_an_object_file(dirs[0])), Held(_an_object_file(dirs[1])):
            assert (await c.delete(f"/build/app/sessions/{a}")).status_code == 200
            first = svc._store_sweep
            assert (await c.delete(f"/build/app/sessions/{b}")).status_code == 200
            second = svc._store_sweep
            assert first is not None and second is not None and second is not first
            # (Bounded: a wait nobody cancels never ends.)
            await asyncio.wait_for(asyncio.gather(first, return_exceptions=True), 30)
            assert first.cancelled() and not second.done()  # one round of waits at a time
            await svc.shutdown()
            await asyncio.wait_for(asyncio.gather(second, return_exceptions=True), 30)
            assert second.cancelled()
            # Closing: nothing new is started (the next start sweeps).
            svc._sweep_stores_later()
            assert svc._store_sweep is second
    await stop_all(app)


async def test_a_start_that_finds_a_held_store_sweeps_again_after_a_wait(app, plain):
    home, engine = _home(app), app.state.platform.engine
    orphan = _orphan(app, plain)
    waits: list[float] = []
    held = Held(_an_object_file(orphan))

    async def wait(seconds: float) -> None:
        waits.append(seconds)
        held.release()

    try:
        got = await S.sweep_stores_until_gone(home, engine, sleep=wait)
    finally:
        held.release()
    assert waits == [S.STORE_RETRY_S[0]]
    assert got == CK.Swept(removed=1, left=0) and not orphan.exists()


# --------------------------------------------------------------------------- #
# at the daemon's start
# --------------------------------------------------------------------------- #


def test_the_daemons_start_removes_every_store_without_a_session_row_and_keeps_the_rest(
        tmp_path, plain, caplog):
    app = create_app(str(tmp_path / "home"))
    home = _home(app)
    kept = _rows(app, plain)
    orphan = _orphan(app, plain)
    base = orphan.parent
    # A store whose row is missing but whose session's own folder is there
    # (what a backup restored leaves for a session made since): kept.
    missing = store.create(app.state.platform.engine, harness="grok", folder=str(plain),
                           harness_session_id="x-missing")
    _with_store(home, missing.id, plain)
    assert store.delete(app.state.platform.engine, missing.id)
    # A store that was still being made when a session was deleted.
    half = base / "app_gone_too.git.0123abcd.new"
    half.mkdir()
    (half / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    # Nothing under the folder that is not a store is touched.
    (base / "notes.txt").write_text("not a store", encoding="utf-8")
    (base / "not-a-store").mkdir()

    async def boot() -> None:
        async with app.router.lifespan_context(app):
            await until(lambda: not orphan.exists() and not half.exists(), timeout=60)

    with caplog.at_level(logging.INFO, logger=SERVICE_LOG):
        asyncio.run(boot())
    assert not orphan.exists() and not half.exists()
    for row in kept.values():
        assert CK.has_own_store(home, row.id), row.id
    assert CK.has_own_store(home, missing.id)
    assert (base / "notes.txt").read_text(encoding="utf-8") == "not a store"
    assert (base / "not-a-store").is_dir()
    assert any("removed 2 copy" in m for m in caplog.messages)


# --------------------------------------------------------------------------- #
# never the wrong way round
# --------------------------------------------------------------------------- #


def test_nothing_is_removed_when_the_rows_cannot_be_read(app, plain, monkeypatch, caplog):
    home, engine = _home(app), app.state.platform.engine
    kept = _rows(app, plain)
    orphan = _orphan(app, plain)

    def unreadable(_engine):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(store, "all_ids", unreadable)
    with caplog.at_level(logging.WARNING):
        got = S.sweep_left_stores(home, engine)
    assert got == CK.Swept(removed=0, left=0, unread=True)
    assert orphan.exists() and all(CK.has_own_store(home, r.id) for r in kept.values())
    assert any("no checkpoint store was removed" in m for m in caplog.messages)
    # (Never called stores "of deleted sessions": which they are was not known.)
    assert not any("could not be removed yet" in m for m in caplog.messages)


async def test_rows_that_cannot_be_read_are_tried_again_after_a_wait(app, plain, monkeypatch):
    """A database locked for a moment: the start's sweep looks again later."""
    home, engine = _home(app), app.state.platform.engine
    orphan = _orphan(app, plain)
    real = store.all_ids
    calls: list[int] = []

    def locked_once(e):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("database is locked")
        return real(e)

    monkeypatch.setattr(store, "all_ids", locked_once)
    waits: list[float] = []

    async def wait(seconds: float) -> None:
        waits.append(seconds)

    got = await S.sweep_stores_until_gone(home, engine, sleep=wait)
    assert waits == [S.STORE_RETRY_S[0]]
    assert got == CK.Swept(removed=1, left=0) and not orphan.exists()


def test_a_missing_session_table_is_not_no_sessions(app, plain):
    home, engine = _home(app), app.state.platform.engine
    kept = _rows(app, plain)
    HostedSessionRecord.__table__.drop(bind=engine)  # type: ignore[attr-defined]
    got = S.sweep_left_stores(home, engine)
    assert got.removed == 0
    assert all(CK.has_own_store(home, r.id) for r in kept.values())


def test_nothing_is_removed_while_the_database_is_set_aside(app, plain):
    """A corrupt database was set aside and the app runs on a fresh, empty
    one: the sessions' rows may come back with a restore."""
    home, engine = _home(app), app.state.platform.engine
    orphan = _orphan(app, plain)
    DB.write_set_aside_marker(home, home / "ironjarvis.db.corrupt-x", "test")
    assert S.sweep_left_stores(home, engine).removed == 0 and orphan.exists()
    # Answered (restored or kept fresh): the sweep goes on.
    assert DB.answer_set_aside_marker(home, "kept_fresh")
    assert S.sweep_left_stores(home, engine) == CK.Swept(removed=1, left=0) and not orphan.exists()


async def test_a_start_while_the_database_is_set_aside_waits_for_no_retry(app, plain, caplog):
    """No sweep can remove anything until the set-aside is answered, so the
    start says so once and waits for nothing (the review: 4 tracebacks over
    71 minutes, none of which could succeed)."""
    home, engine = _home(app), app.state.platform.engine
    orphan = _orphan(app, plain)
    DB.write_set_aside_marker(home, home / "ironjarvis.db.corrupt-x", "test")
    waits: list[float] = []

    async def wait(seconds: float) -> None:
        waits.append(seconds)

    with caplog.at_level(logging.INFO, logger=SERVICE_LOG):
        got = await S.sweep_stores_until_gone(home, engine, sleep=wait)
    assert waits == [] and got == CK.Swept(removed=0, left=0) and orphan.exists()
    assert len(_service_lines(caplog, "set aside")) == 1
    assert not any(r.exc_info for r in caplog.records if r.name == SERVICE_LOG)
    assert not _service_lines(caplog, "could not be removed yet")


def test_a_store_made_while_the_rows_are_read_is_never_removed(tmp_path, plain):
    """The rows are read AFTER the folder is listed: a session made (and its
    first store) in between is never taken for a deleted one."""
    home = tmp_path / "home"
    gone = _with_store(home, "app_gone", plain)
    _folder_trashed(home, "app_gone")

    def rows():
        ids = {"app_old"}  # the rows as they were read...
        _with_store(home, "app_new", plain)  # ...and a new session's first message meanwhile
        _folder_trashed(home, "app_new")  # (so only the order keeps its store)
        return ids

    assert CK.sweep_own_stores(home, rows) == CK.Swept(removed=1, left=0)
    assert not gone.exists() and CK.has_own_store(home, "app_new")


def test_a_link_under_the_stores_folder_is_never_followed(tmp_path, plain):
    home = tmp_path / "home"
    gone = _with_store(home, "app_gone", plain)
    _folder_trashed(home, "app_gone")
    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "keep.txt").write_text("mine", encoding="utf-8")
    link = gone.parent / "app_link.git"
    try:
        if os.name == "nt":
            import _winapi

            _winapi.CreateJunction(str(target), str(link))
        else:
            os.symlink(target, link, target_is_directory=True)
    except OSError:
        pytest.skip("no link can be made here")
    assert CK.sweep_own_stores(home, lambda: set()) == CK.Swept(removed=1, left=0)
    assert not gone.exists()
    assert (target / "keep.txt").read_text(encoding="utf-8") == "mine"


# --------------------------------------------------------------------------- #
# deleted, not just missing (the review)
# --------------------------------------------------------------------------- #


def test_a_backup_restored_and_undone_keeps_the_store_of_a_session_made_since(tmp_path, plain):
    """The review's round trip. Session A is in last night's backup, B was
    made today. Restoring last night's backup takes B's row away (the stores
    are in no backup, and a restore keeps what the archive does not carry);
    the start's sweep then must not take B's store, because undoing the
    restore (the archive written just before it) brings B's row back, and
    every "Rewind to here" of B needs that store."""
    app = create_app(str(tmp_path / "home"))
    home, engine = _home(app), app.state.platform.engine
    a = store.create(engine, harness="grok", folder=str(plain), harness_session_id="x-a")
    _with_store(home, a.id, plain)
    night, _n = M.create_backup(home, tmp_path / "last-night.tar.gz", engine=engine)
    b = store.create(engine, harness="grok", folder=str(plain), harness_session_id="x-b")
    _with_store(home, b.id, plain)
    before = M.write_safety_backup(home, engine=engine)  # as the restore route does
    engine.dispose()
    M.restore_backup_live(home, night)
    engine = DB.open_db(home / M._DB_NAME)  # the restart
    try:
        assert store.get(engine, a.id) is not None and store.get(engine, b.id) is None
        got = S.sweep_left_stores(home, engine)
        assert got == CK.Swept(removed=0, left=0)
        assert CK.has_own_store(home, a.id) and CK.has_own_store(home, b.id)
    finally:
        engine.dispose()
    M.restore_backup_live(home, before)  # the restore undone
    engine = DB.open_db(home / M._DB_NAME)
    try:
        assert store.get(engine, b.id) is not None
        assert CK.has_own_store(home, b.id) and PN.read_trees(home, b.id)
        assert S.sweep_left_stores(home, engine) == CK.Swept(removed=0, left=0)
    finally:
        engine.dispose()


def test_a_missing_row_alone_is_not_a_deleted_session(tmp_path, plain):
    home = tmp_path / "home"
    missing = _with_store(home, "app_missing", plain)  # its own folder is there
    gone = _with_store(home, "app_gone", plain)
    _folder_trashed(home, "app_gone")
    # ...and a row that exists keeps its store even with no folder of its own.
    _with_store(home, "app_row", plain)
    _folder_trashed(home, "app_row")
    got = CK.sweep_own_stores(home, lambda: {"app_row"})
    assert got == CK.Swept(removed=1, left=0)  # (a kept one is not "left")
    assert CK.has_own_store(home, "app_missing") and missing.is_dir() and not gone.exists()
    assert CK.has_own_store(home, "app_row")


async def test_a_store_its_delete_marked_goes_even_when_the_session_folder_stayed(app, plain, monkeypatch):
    """The delete marks the store before it removes it, so a delete whose
    move of the session's folder to the trash ALSO failed (or a crash in the
    middle of it) still leaves a store the sweep knows was deleted."""
    svc = S.service_for(app.state.platform)
    home = _home(app)

    async def wait(_seconds: float) -> None:
        return None

    svc._sleep = wait
    monkeypatch.setattr(svc, "_trash_events", lambda _sid: "")  # the move to the trash failed
    async with client(app) as c:
        sid = (await new_session(c, plain, harness="grok"))["id"]
        store_dir = _with_store(home, sid, plain)
        with Held(_an_object_file(store_dir)):
            assert (await c.delete(f"/build/app/sessions/{sid}")).status_code == 200
            assert store_dir.exists() and session_dir(home, sid).is_dir()
            assert _marks(home) == [store_dir.name + CK.OWN_DELETED]
            await asyncio.wait_for(asyncio.gather(svc._store_sweep, return_exceptions=True), 60)
        got = await S.sweep_stores_until_gone(home, app.state.platform.engine, sleep=wait)
        assert got == CK.Swept(removed=1, left=0) and not store_dir.exists()
        assert _marks(home) == []
    await stop_all(app)


def test_a_mark_with_no_store_is_tidied_and_a_restored_rows_store_is_kept(tmp_path, plain):
    home = tmp_path / "home"
    base = Path(home).joinpath(*CK.OWN_DIR)
    # A delete that failed, then a backup restored that has the row: kept.
    back = _with_store(home, "app_back", plain)
    (base / (back.name + CK.OWN_DELETED)).write_text("app_back\n", encoding="utf-8")
    # A mark whose store is gone.
    (base / ("app_old.git" + CK.OWN_DELETED)).write_text("app_old\n", encoding="utf-8")
    assert CK.sweep_own_stores(home, lambda: {"app_back"}) == CK.Swept(removed=0, left=0)
    assert CK.has_own_store(home, "app_back")
    assert _marks(home) == [back.name + CK.OWN_DELETED]
    # No store left at all: no mark either (and the rows are not read for it).
    assert CK._remove_store(back)

    def unread():
        raise AssertionError("the rows were read")

    assert CK.sweep_own_stores(home, unread) == CK.Swept(removed=0, left=0)
    assert _marks(home) == []
