"""FU-3c — two Build › Code sessions in NESTED folders that are in no git
repository (item 3c of ``docs/BUILD-CODE-FOLLOW-UP-PLAN-2026-11-20.md``;
``hosted/checkpoints.py`` ``folder_locked``).

Every write of a folder's files takes the folder's lock (``lock_key``). In a
repository that lock is the REPOSITORY's, so a session in ``repo`` and one in
``repo/sub`` share it. Outside one it is the folder's own path, so a session
in ``work`` and one in ``work/sub`` held different locks, and their rewinds
could write the same files at once (a rewind of ``work`` writes ``sub``'s
files too). Now such a write also waits while another holds a folder above or
below its own, and is refused in words when that takes the lock's whole wait:

* while one session's rewind writes ``work``, a rewind in ``work/sub`` is
  refused in the folder's own words and writes nothing, and the reverse; once
  the first is done, the second goes;
* it WAITS meanwhile (proven by order: it is seen waiting, and it writes only
  after the first one has written);
* a folder beside it (``work/other``, ``work-2``) is no reason to wait.

Real git, real routes and service; the coding tool is the recorded fake, and
the test writes the files a tool would have written. The first session's
rewind is held IN its write (inside its locks) until the test lets it go.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build_git as BG
from iron_jarvis.daemon.routes.build_app_branch import FOLDER_BUSY
from iron_jarvis.hosted import checkpoints as CK
from iron_jarvis.hosted import panes as PN
from iron_jarvis.hosted import service as S
from tests._build_app_helpers_v1409 import FAKE, FIX, client, new_session, stop_all

CLAUDE_FIX = FIX / "claude_resume.json"
AS_IT_WAS = {"top.txt": b"top as it was\n", "sub/inner.txt": b"inner as it was\n"}
CHANGED = b"changed by the tool\n"


@pytest.fixture()
def work(tmp_path) -> Path:
    """``work`` and ``work/sub``, in NO git repository, each with a file."""
    folder = tmp_path / "work"
    (folder / "sub").mkdir(parents=True)
    for rel, data in AS_IT_WAS.items():
        (folder / rel).write_bytes(data)
    return folder


@pytest.fixture(autouse=True)
def _a_checkpoint_for_every_message(monkeypatch):
    """(As ``test_build_code_own_checkpoints_v1546``: a message waits for its
    checkpoint, so every one has one.)"""
    real = PN.record_turn_start_async

    async def patient(home, sid, folder, turn_id, *, bound=120.0, uid=""):
        return await real(home, sid, folder, turn_id, bound=120.0, uid=uid)

    monkeypatch.setattr(PN, "record_turn_start_async", patient)


@pytest.fixture()
def app(tmp_path, monkeypatch):
    home = tmp_path / "claude-home"
    home.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    monkeypatch.setattr(S, "binary_for", lambda harness: [sys.executable, str(FAKE)])
    monkeypatch.setenv("IJ_FAKE_SCENARIO", str(CLAUDE_FIX))
    monkeypatch.setenv("IJ_FAKE_LOG", str(tmp_path / "fake.log"))
    return create_app(str(tmp_path / "home"))


class _FirstWriteHeld:
    """``CK.restore`` (a rewind's write, run INSIDE its locks) is held for
    session ``first`` until :meth:`let_go`; every write says in ``order``
    when it has written."""

    def __init__(self, monkeypatch) -> None:
        self.first = ""
        self.order: list[str] = []
        self.inside, self.go = threading.Event(), threading.Event()
        real = CK.restore

        def restore(home, sid, *a, **k):
            if sid == self.first:
                self.inside.set()
                self.go.wait(60)
            out = real(home, sid, *a, **k)
            self.order.append("first wrote" if sid == self.first else "second wrote")
            return out

        monkeypatch.setattr(CK, "restore", restore)

    def let_go(self) -> None:
        self.go.set()


async def _message(c, folder: Path) -> str:
    sid = (await new_session(c, folder))["id"]
    r = await c.post(f"/build/app/sessions/{sid}/turn", json={"text": "first one", "turn_id": f"t1-{sid}"})
    assert r.status_code == 200, r.text
    return sid


async def _preview(c, app, sid: str, back: list[str]) -> tuple[str, str]:
    """The session's one message and the preview's id (``back``: what it puts back)."""
    view = (await c.get(f"/build/app/sessions/{sid}")).json()
    [at] = [e["at"] for e in view["events"] if e["type"] == "user"]
    assert view["branch"]["files_at"] == [at] and CK.has_own_store(Path(S.service_for(app.state.platform).home), sid)
    r = await c.get(f"/build/app/sessions/{sid}/rewind/preview", params={"to": at})
    assert r.status_code == 200, r.text
    assert sorted(r.json()["back"]) == back and r.json()["can"] is True
    return at, r.json()["id"]


def _rewind(c, sid: str, at: str, expect: str):
    return c.post(f"/build/app/sessions/{sid}/rewind/files", json={"to": at, "expect": expect})


def _rel(folder: Path, work: Path, rel: str) -> str:
    """``rel`` (from ``work``) as the session in ``folder`` names it."""
    return (work / rel).relative_to(folder).as_posix()


async def _nested_pair(c, work: Path, first_in: Path, second_in: Path, first_file: str, second_file: str):
    """The SECOND session's message comes first and the tool changes its file;
    then the first session's message, and the tool changes the first's file.
    (So each rewind puts back only its own file, whichever writes first.)"""
    second = await _message(c, second_in)
    (work / second_file).write_bytes(CHANGED)
    first = await _message(c, first_in)
    (work / first_file).write_bytes(CHANGED)
    return first, second


@pytest.mark.parametrize("first_at, second_at, first_file, second_file", [
    # the FIRST session's folder holds the second's…
    ("work", "work/sub", "top.txt", "sub/inner.txt"),
    # …and the other way round.
    ("work/sub", "work", "sub/inner.txt", "top.txt"),
])
async def test_a_rewind_beside_a_rewind_of_a_nested_folder_is_refused_in_words_then_goes(
        app, work, monkeypatch, first_at, second_at, first_file, second_file):
    places = {"work": work, "work/sub": work / "sub"}
    first_in, second_in = places[first_at], places[second_at]
    held = _FirstWriteHeld(monkeypatch)
    async with client(app) as c:
        first, second = await _nested_pair(c, work, first_in, second_in, first_file, second_file)
        held.first = first
        # (The second's own rewind also puts back the first's file when that
        # one is in its folder: it was changed after the second's message.)
        second_back = sorted({_rel(second_in, work, second_file)}
                             | ({_rel(second_in, work, first_file)} if second_at == "work" else set()))
        first_at_msg, first_id = await _preview(c, app, first, [_rel(first_in, work, first_file)])
        second_at_msg, second_id = await _preview(c, app, second, second_back)
        # The two folders' locks are not one lock: that is what made them meet.
        assert CK.lock_key(str(first_in)) != CK.lock_key(str(second_in))
        monkeypatch.setattr(BG, "GIT_LOCK_WAIT_S", 0.5)
        rewinding = asyncio.ensure_future(_rewind(c, first, first_at_msg, first_id))
        try:
            assert await asyncio.to_thread(held.inside.wait, 30), "the first rewind never started writing"
            r = await _rewind(c, second, second_at_msg, second_id)
            assert r.status_code == 409, r.text
            assert r.json()["detail"] == FOLDER_BUSY
            # The second wrote nothing, and its session is free again.
            assert (work / second_file).read_bytes() == CHANGED and held.order == []
            assert S.service_for(app.state.platform).live[second].claim == ""
        finally:
            held.let_go()
        r = await asyncio.wait_for(rewinding, 60)
        assert r.status_code == 200, r.text
        assert (work / first_file).read_bytes() == AS_IT_WAS[first_file]
        # Once the first is done, the second goes (read again: the first
        # rewind may have put back a file it had listed).
        second_at_msg, second_id = await _preview(c, app, second, [_rel(second_in, work, second_file)])
        r = await _rewind(c, second, second_at_msg, second_id)
        assert r.status_code == 200, r.text
        assert {rel: (work / rel).read_bytes() for rel in AS_IT_WAS} == AS_IT_WAS
        assert held.order == ["first wrote", "second wrote"]
    await stop_all(app)


async def test_a_rewind_waits_for_the_nested_rewind_and_writes_only_after_it(app, work, monkeypatch):
    waiting = threading.Event()

    class Seen(threading.Condition):
        """(The test sees a write WAIT for a folder above or below it.)"""

        def wait(self, timeout=None):
            waiting.set()
            return super().wait(timeout)

    monkeypatch.setattr(CK, "_PLAIN_FREED", Seen(), raising=False)
    held = _FirstWriteHeld(monkeypatch)
    async with client(app) as c:
        first, second = await _nested_pair(c, work, work, work / "sub", "top.txt", "sub/inner.txt")
        held.first = first
        first_at, first_id = await _preview(c, app, first, ["top.txt"])
        second_at, second_id = await _preview(c, app, second, ["inner.txt"])
        rewinding_first = asyncio.ensure_future(_rewind(c, first, first_at, first_id))
        try:
            assert await asyncio.to_thread(held.inside.wait, 30), "the first rewind never started writing"
            rewinding_second = asyncio.ensure_future(_rewind(c, second, second_at, second_id))
            seen = await asyncio.to_thread(waiting.wait, 30)
        finally:
            held.let_go()
        r1 = await asyncio.wait_for(rewinding_first, 60)
        r2 = await asyncio.wait_for(rewinding_second, 60)
        assert r1.status_code == 200 and r2.status_code == 200, (r1.text, r2.text)
        assert seen, f"the rewind never waited for the rewind of the folder around it: {held.order}"
        assert held.order == ["first wrote", "second wrote"]
        assert {rel: (work / rel).read_bytes() for rel in AS_IT_WAS} == AS_IT_WAS
    await stop_all(app)


class _Held:
    """Another write holds ``folder``'s files through :func:`CK.folder_locked`
    (what every write takes) until :meth:`end`."""

    def __init__(self, folder: Path) -> None:
        self._in, self._out = threading.Event(), threading.Event()
        self.thread = threading.Thread(target=CK.folder_locked, args=(str(folder), self._hold), daemon=True)
        self.thread.start()
        assert self._in.wait(30), "the other write never got the folder"

    def _hold(self) -> None:
        self._in.set()
        self._out.wait(60)

    def end(self) -> None:
        self._out.set()
        self.thread.join(30)
        assert not self.thread.is_alive()


@pytest.mark.parametrize("session_in, beside, name", [
    ("work/sub", "work/other", "inner.txt"),
    ("work", "work-2", "top.txt"),  # its name begins with "work" too
])
async def test_a_folder_beside_it_is_no_reason_to_wait(app, work, tmp_path, monkeypatch, session_in, beside, name):
    places = {"work": work, "work/sub": work / "sub", "work/other": work / "other", "work-2": tmp_path / "work-2"}
    places[beside].mkdir()
    folder = places[session_in]
    monkeypatch.setattr(BG, "GIT_LOCK_WAIT_S", 0.5)
    async with client(app) as c:
        sid = await _message(c, folder)
        (folder / name).write_bytes(CHANGED)
        at, expect = await _preview(c, app, sid, [name])
        other = _Held(places[beside])
        try:
            r = await _rewind(c, sid, at, expect)
            assert r.status_code == 200, r.text
        finally:
            other.end()
        assert (folder / name).read_bytes() == AS_IT_WAS[(folder / name).relative_to(work).as_posix()]
    await stop_all(app)


def test_which_folders_are_nested():
    p = CK._plain_path
    root = os.path.abspath(os.sep)
    work = os.path.join(root, "x", "work")
    assert CK._nested(p(work), p(work)) and CK._nested(p(work), p(os.path.join(work, "sub")))
    assert CK._nested(p(os.path.join(work, "sub", "deeper")), p(work))
    assert CK._nested(p(work + os.sep), p(work))  # a separator at the end is the same folder
    assert CK._nested(p(root), p(work))  # the drive's (or the disk's) root holds everything
    assert not CK._nested(p(work), p(work + "-2"))
    assert not CK._nested(p(os.path.join(work, "a")), p(os.path.join(work, "b")))
    if os.name == "nt":
        assert CK._nested(p(work.upper()), p(os.path.join(work, "sub")))  # one folder, two spellings


def test_a_folder_in_a_repository_keeps_the_repositorys_one_lock(tmp_path, monkeypatch):
    """Nothing changes there: nested folders already share the repository's
    lock (``test_build_app_rewind_files_v1543``), which is all it takes."""
    keys: list[str] = []
    real = BG._locked
    monkeypatch.setattr(BG, "_locked", lambda key, fn: (keys.append(key), real(key, fn))[1])
    monkeypatch.setattr(CK, "repo_of", lambda folder: str(tmp_path / "repo" / ".git"))
    held_then: list[list[str]] = []
    assert CK.folder_locked(str(tmp_path / "repo" / "sub"),
                            lambda: (held_then.append(list(CK._PLAIN_HELD)), "done")[1]) == "done"
    assert keys == ["code-folder:" + os.path.normcase(str(tmp_path / "repo" / ".git"))]
    assert held_then == [[]]
    # Outside one, the folder is held while its write runs, and let go after…
    monkeypatch.setattr(CK, "repo_of", lambda folder: "")
    plain = CK._plain_path(str(tmp_path / "plain"))
    assert CK.folder_locked(str(tmp_path / "plain"), lambda: list(CK._PLAIN_HELD)) == [plain]
    assert CK._PLAIN_HELD == []

    # …a write that fails too.
    def fails():
        raise RuntimeError("the write failed")

    with pytest.raises(RuntimeError):
        CK.folder_locked(str(tmp_path / "plain"), fails)
    assert CK._PLAIN_HELD == []
