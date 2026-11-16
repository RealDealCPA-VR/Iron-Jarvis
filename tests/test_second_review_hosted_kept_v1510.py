"""v1.510.0 (second review, group 10, H5 and H6): two places where a coding
session lost something with no copy.

H5. Revert in the Changes pane ran ``git restore --worktree`` over a changed
file: the coding tool's work on it, and the user's own edits since the pane
read it, were gone from the folder with no copy anywhere. Now the file as it
is is COPIED into Iron Jarvis's trash first (``<home>/trash/<stamp>/
code-revert/<sid>/<path>``) and named in the words and as ``moved_to``; a
copy that cannot be made refuses the revert, touching nothing; the session's
own log says what was reverted and where the replaced version is; and the
route also waits while the tool's helpers, a reply of the tool's own or a
helper session this one started still work in the folder.

H6. ``EventLog.flush`` took the batch out of ``_pending`` and, when the write
failed, logged a warning and dropped it — a reply's ``done`` among it made
the next start read that reply as cut. Now the batch goes back to the FRONT
of ``_pending`` (a partial write is cut off first, so nothing is written
twice and no torn line is left), the retry memory is bounded by the log's own
bounds (oldest dropped and counted), and the session says ONCE per failure
streak, in its in-memory tail, that its record could not be saved.

A fake ``open`` that raises ``OSError`` stands in for a full disk.
"""

from __future__ import annotations

import builtins
import json
import os
import subprocess
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import carry_on as CO
from iron_jarvis.hosted import events as E
from iron_jarvis.hosted import history as HIST
from iron_jarvis.hosted import panes as PN
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted.events import EventLog, make
from tests._build_app_helpers_v1409 import client, new_session, stop_all

GIT = ["git", "-c", "core.autocrlf=false", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
NOW = datetime.now(timezone.utc)


def _git(cwd: Path, *args: str, when: str | None = None) -> str:
    env = dict(os.environ)
    if when:
        env["GIT_COMMITTER_DATE"] = when
        env["GIT_AUTHOR_DATE"] = when
    return subprocess.run([*GIT, *args], cwd=cwd, check=True, capture_output=True, env=env).stdout.decode()


def _row(folder: Path | str) -> types.SimpleNamespace:
    return types.SimpleNamespace(folder=str(folder), created_at=NOW - timedelta(hours=1), harness="claude-code")


@pytest.fixture()
def repo(tmp_path) -> Path:
    folder = tmp_path / "repo"
    folder.mkdir()
    _git(folder, "init", "-q", "-b", "main")
    (folder / "a.py").write_text("a = 0\n", encoding="utf-8")
    (folder / "b.py").write_text("b = 0\n", encoding="utf-8")
    _git(folder, "add", "-A")
    _git(folder, "commit", "-q", "-m", "first", when="2020-01-01T00:00:00+00:00")
    return folder


def _by(view: dict) -> dict:
    return {c["rel"]: c for c in view["changes"]}


# --------------------------------------------------------------------------- #
# H5: a revert keeps the version it replaced
# --------------------------------------------------------------------------- #


def test_a_revert_keeps_the_version_it_replaced_in_the_trash(tmp_path, repo):
    """MUTATION: drop the copy before ``git restore`` -> ``moved_to`` is
    empty and the tool's "a = 2" is nowhere (red)."""
    home = tmp_path / "home"
    (repo / "a.py").write_text("a = 2\n", encoding="utf-8")
    view = PN.changes_view(_row(repo), [], "Claude Code", home=home, sid="s1")
    got = PN.revert_file(home, "s1", _row(repo), [], "Claude Code", rel="a.py", blob=_by(view)["a.py"]["blob"])
    assert (repo / "a.py").read_text(encoding="utf-8") == "a = 0\n"
    kept = Path(got["moved_to"] or "")
    assert got["moved_to"] and kept.is_file(), got
    assert kept.read_text(encoding="utf-8") == "a = 2\n"
    assert str(home / "trash") in str(kept) and kept.name == "a.py"
    assert "code-revert" in kept.parts and "s1" in kept.parts
    assert got["words"] == PN.REVERT_KEPT.format(name="a.py", when=PN.WHEN_SESSION, trash=str(kept))
    assert "The version it replaced is kept in" in got["words"] and str(kept) in got["words"]


def test_a_revert_whose_copy_cannot_be_made_is_refused_and_touches_nothing(tmp_path, repo):
    """``<home>/trash`` is a FILE, so nothing can be kept there. MUTATION:
    restore without the copy -> a.py reads "a = 0" and the edit is lost (red)."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "trash").write_text("not a folder\n", encoding="utf-8")
    (repo / "a.py").write_text("a = mine\n", encoding="utf-8")
    view = PN.changes_view(_row(repo), [], "Codex", home=home, sid="s1")
    with pytest.raises(PN.RevertRefused) as no:
        PN.revert_file(home, "s1", _row(repo), [], "Codex", rel="a.py", blob=_by(view)["a.py"]["blob"])
    assert no.value.sentence.startswith("a.py was left as it is"), no.value.sentence
    assert (repo / "a.py").read_text(encoding="utf-8") == "a = mine\n"


def test_a_deleted_file_comes_back_with_nothing_to_keep(tmp_path, repo):
    home = tmp_path / "home"
    (repo / "b.py").unlink()
    got = PN.revert_file(home, "s1", _row(repo), [], "Pi", rel="b.py", blob="")
    assert (repo / "b.py").read_text(encoding="utf-8") == "b = 0\n"
    assert got["moved_to"] == "" and got["words"] == PN.REVERT_DONE.format(name="b.py", when=PN.WHEN_SESSION)


def test_the_pane_words_say_the_replaced_version_is_kept():
    words = PN.REVERT_WORDS.format(tool="Codex", when=PN.WHEN_SESSION)
    assert "keeps the version it replaces in Iron Jarvis's trash" in words
    assert "No Undo here: Codex made these changes itself." in words


async def test_the_route_says_the_revert_in_the_sessions_own_log(tmp_path, repo):
    """MUTATION: drop the route's notice -> the log has no line about the
    revert (red)."""
    app = create_app(str(tmp_path / "home"))
    async with client(app) as c:
        sid = (await new_session(c, repo))["id"]
        (repo / "b.py").write_text("b = 9\n", encoding="utf-8")
        row = _by((await c.get(f"/build/app/sessions/{sid}/changes")).json())["b.py"]
        r = await c.post(f"/build/app/sessions/{sid}/changes/revert", json={"path": "b.py", "blob": row["blob"]})
        assert r.status_code == 200, r.text
        kept = r.json()["moved_to"]
        assert kept and Path(kept).read_text(encoding="utf-8") == "b = 9\n"
        svc = S.service_for(app.state.platform)
        import asyncio

        events, _t, _d = await asyncio.to_thread(svc.live[sid].log.read, 200)
    await stop_all(app)
    said = [e for e in events if e.get("type") == "notice" and "Changes tab" in str(e.get("text"))]
    assert len(said) == 1, events
    assert said[0]["text"] == PN.REVERT_NOTICE.format(name="b.py", trash=kept)


@pytest.mark.parametrize("state", ["replying_alone", "helpers_working", "working_alone"])
async def test_the_route_waits_while_the_tool_works_in_the_folder_on_its_own(tmp_path, repo, state):
    """A reply the tool writes by itself, its helper agents, Pi's own run:
    each writes the same folder with no reply of the user's running.
    MUTATION: check ``_turn_running`` only -> the revert goes through (red)."""
    app = create_app(str(tmp_path / "home"))
    async with client(app) as c:
        sid = (await new_session(c, repo))["id"]
        (repo / "b.py").write_text("b = 9\n", encoding="utf-8")
        svc = S.service_for(app.state.platform)
        svc.live[sid] = types.SimpleNamespace(
            turn=None, log=EventLog(svc.home, sid), running=True, proc=None,
            working_alone=state == "working_alone",
            replying_alone=lambda now: state == "replying_alone",
            helpers_working=lambda now: state == "helpers_working",
        )
        try:
            r = await c.post(f"/build/app/sessions/{sid}/changes/revert",
                             json={"path": "b.py", "blob": PN._blob(str(repo / "b.py"))})
        finally:
            svc.live.pop(sid, None)
    await stop_all(app)
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == PN.REVERT_TOOL_BUSY.format(tool="Claude Code")
    assert (repo / "b.py").read_text(encoding="utf-8") == "b = 9\n"


async def test_the_route_waits_while_a_helper_session_works_in_the_folder(tmp_path, repo, monkeypatch):
    """A helper session (Hand off) works in the SAME folder. MUTATION: skip
    the helper check -> the revert goes through (red)."""
    from iron_jarvis.hosted import handoff as H

    app = create_app(str(tmp_path / "home"))
    async with client(app) as c:
        sid = (await new_session(c, repo))["id"]
        (repo / "b.py").write_text("b = 9\n", encoding="utf-8")
        svc = S.service_for(app.state.platform)
        monkeypatch.setattr(H, "_helper_rows", lambda _svc, parent: [types.SimpleNamespace(id="helper-1")]
                            if parent == sid else [])
        svc.live["helper-1"] = types.SimpleNamespace(turn=types.SimpleNamespace(running=True))
        try:
            r = await c.post(f"/build/app/sessions/{sid}/changes/revert",
                             json={"path": "b.py", "blob": PN._blob(str(repo / "b.py"))})
        finally:
            svc.live.pop("helper-1", None)
    await stop_all(app)
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == PN.REVERT_HELPER_BUSY
    assert (repo / "b.py").read_text(encoding="utf-8") == "b = 9\n"


# --------------------------------------------------------------------------- #
# H6: a failed write of the session log keeps the batch
# --------------------------------------------------------------------------- #


class _Torn:
    """A file whose write puts the first ``n`` bytes down, then fails."""

    def __init__(self, real, n: int) -> None:
        self._real = real
        self._n = n

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self._real.close()

    def write(self, data: bytes) -> int:
        self._real.write(data[: self._n])
        self._real.flush()
        raise OSError(28, "No space left on device")

    def __getattr__(self, name):
        return getattr(self._real, name)


class _Disk:
    """``open`` as the events module sees it: an append to ``path`` fails
    while ``full`` (or tears after ``torn`` bytes)."""

    def __init__(self, path: Path) -> None:
        self.path = str(path)
        self.full = True
        self.torn: int | None = None

    def __call__(self, file, mode="r", *a, **k):
        if str(file) == self.path and "a" in mode and self.full:
            if self.torn is None:
                raise OSError(28, "No space left on device")
            return _Torn(builtins.open(file, mode, *a, **k), self.torn)
        return builtins.open(file, mode, *a, **k)


def _quiet(log_: EventLog) -> EventLog:
    """No writer thread: the test flushes by hand."""
    log_._stop = True
    return log_


def _disk(monkeypatch, log_: EventLog) -> _Disk:
    disk = _Disk(log_.path)
    monkeypatch.setattr(E, "open", disk, raising=False)
    return disk


def _lines(path: Path) -> list[dict]:
    out = []
    for raw in path.read_bytes().splitlines():
        if raw.strip():
            out.append(json.loads(raw))  # a torn line fails here
    return out


def _turn_rows(tid: str = "t1") -> list[dict]:
    return [make("user", text="do it", turn=tid), make("notice", text="step one", turn=tid),
            make("done", stop="completed", turn=tid)]


def test_a_failed_write_keeps_the_rows_and_the_next_flush_writes_them_once_in_order(tmp_path, monkeypatch):
    """MUTATION: drop the requeue -> the second flush writes nothing and the
    file has no ``done`` (red)."""
    log_ = _quiet(EventLog(tmp_path, "s1"))
    disk = _disk(monkeypatch, log_)
    for ev in _turn_rows():
        log_.append(ev)
    log_.flush()
    assert not log_.path.exists() or not log_.path.read_bytes().strip()
    log_.flush()  # still full: kept again, never doubled
    disk.full = False
    log_.append(make("notice", text="after"))
    log_.flush()
    rows = _lines(log_.path)
    texts = [(r["type"], r.get("text") or r.get("stop")) for r in rows]
    assert texts[:3] == [("user", "do it"), ("notice", "step one"), ("done", "completed")], texts
    assert [t for t in texts if t == ("done", "completed")] == [("done", "completed")]
    assert ("notice", "after") in texts


def test_a_done_that_failed_to_write_is_there_after_the_retry_so_the_reply_is_not_cut(tmp_path, monkeypatch):
    """The restart pass reads a reply with no ``done`` as cut by a crash and
    may carry it on by itself. MUTATION: drop the requeue -> the tail has a
    user row and no done -> ``analyse_tail`` reads it as cut (red)."""
    log_ = _quiet(EventLog(tmp_path, "s1"))
    disk = _disk(monkeypatch, log_)
    log_.append(make("user", text="do it", turn="t1"))
    disk.full = False
    log_.flush()
    disk.full = True
    log_.append(make("done", stop="completed", turn="t1"))
    log_.flush()
    disk.full = False
    log_.flush()
    tail = CO._tail_events(log_.path)
    assert [e["type"] for e in tail if e.get("turn") == "t1"] == ["user", "done"], tail
    assert CO.analyse_tail(tail, stopped_title="Stopped by Iron Jarvis") is None


def test_a_torn_write_is_cut_off_so_no_row_is_written_twice_and_no_line_is_torn(tmp_path, monkeypatch):
    """The disk takes the first row and part of the second, then fails.
    MUTATION: requeue without cutting the partial write off -> the first row
    is in the file twice and a torn line joins the next one (red)."""
    log_ = _quiet(EventLog(tmp_path, "s1"))
    disk = _disk(monkeypatch, log_)
    log_.append(make("notice", text="earlier"))
    disk.full = False
    log_.flush()
    rows = _turn_rows()
    first = len(json.dumps(rows[0], ensure_ascii=False).encode("utf-8")) + 1
    disk.full, disk.torn = True, first + 7
    for ev in rows:
        log_.append(ev)
    log_.flush()
    disk.full = False
    log_.flush()
    got = [(r["type"], r.get("text") or r.get("stop")) for r in _lines(log_.path)]
    assert got == [("notice", "earlier"), ("user", "do it"), ("notice", "step one"), ("done", "completed")] + [
        g for g in got[4:]], got
    assert got.count(("user", "do it")) == 1 and got.count(("done", "completed")) == 1


def test_a_torn_write_that_cannot_be_cut_off_at_once_is_cut_off_before_the_retry(tmp_path, monkeypatch):
    log_ = _quiet(EventLog(tmp_path, "s1"))
    disk = _disk(monkeypatch, log_)
    log_.append(make("notice", text="earlier"))
    disk.full = False
    log_.flush()
    rows = _turn_rows()
    disk.full, disk.torn = True, 9
    real = os.truncate
    calls: list[int] = []

    def once(path, size):
        calls.append(size)
        if len(calls) == 1:
            raise OSError(13, "busy")
        return real(path, size)

    monkeypatch.setattr(os, "truncate", once)
    for ev in rows:
        log_.append(ev)
    log_.flush()
    disk.full = False
    log_.flush()
    assert len(calls) == 2 and calls[0] == calls[1]
    got = [(r["type"], r.get("text") or r.get("stop")) for r in _lines(log_.path)]
    assert got[:4] == [("notice", "earlier"), ("user", "do it"), ("notice", "step one"), ("done", "completed")], got
    assert got.count(("user", "do it")) == 1


def test_a_torn_last_line_left_by_a_crash_never_swallows_the_next_row(tmp_path):
    """A process that died mid-write left half a line with no newline. The
    next process's first row starts on a line of its own."""
    log_ = _quiet(EventLog(tmp_path, "s1"))
    log_.dir.mkdir(parents=True)
    log_.path.write_bytes(json.dumps(make("notice", text="whole")).encode() + b"\n" + b'{"type": "tex')
    log_.append(make("done", stop="completed", turn="t1"))
    log_.flush()
    events, _total, _dropped = log_.read(100)
    assert [(e["type"], e.get("text") or e.get("stop")) for e in events] == [
        ("notice", "whole"), ("done", "completed")], events


def test_the_session_is_told_once_per_failure_streak_in_its_in_memory_tail(tmp_path, monkeypatch):
    """MUTATION: drop the notice -> the page's tail says nothing (red); say
    it on every failed flush -> three notices (red)."""
    log_ = _quiet(HIST.TailLog(tmp_path, "s1"))
    disk = _disk(monkeypatch, log_)
    log_.seed()
    log_.append(make("user", text="do it", turn="t1"))
    for _ in range(3):
        log_.flush()

    def said(text: str) -> int:
        served = log_.served(200) or {}
        return sum(1 for e in served.get("events", []) if e.get("type") == "notice" and e.get("text") == text)

    assert said(E.SAVE_FAILED) == 1
    disk.full = False
    log_.flush()
    assert said(E.SAVE_BACK) == 1
    log_.flush()
    disk.full = True
    log_.append(make("notice", text="later"))
    log_.flush()
    log_.flush()
    assert said(E.SAVE_FAILED) == 2  # a new streak says it again, once
    disk.full = False
    log_.flush()
    log_.flush()
    on_disk = [r for r in _lines(log_.path) if r.get("type") == "notice"]
    assert [r["text"] for r in on_disk].count(E.SAVE_FAILED) == 2


def test_the_retry_memory_holds_the_logs_line_bound_and_drops_the_oldest(tmp_path, monkeypatch):
    """MUTATION: no bound -> 40 rows wait in memory (red)."""
    monkeypatch.setattr(E, "MAX_LINES", 5)
    log_ = _quiet(EventLog(tmp_path, "s1"))
    disk = _disk(monkeypatch, log_)
    for i in range(40):
        log_.append(make("notice", text=f"n{i}"))
        if i % 10 == 9:
            log_.flush()
            assert len(log_._pending) <= E.MAX_LINES + 1, len(log_._pending)
    waiting = [r["text"] for r in log_._pending]
    assert waiting == [f"n{i}" for i in range(35, 40)], waiting  # the newest, in order
    # 40 rows and the one "could not save" notice went in; at most the
    # bound waits, so the rest were dropped and counted.
    lost = log_._lost
    assert lost == 41 - len(waiting), (lost, waiting)
    disk.full = False
    log_.flush()
    log_.flush()
    # (The FILE's own bound, patched to 5 lines here too, trims it as usual.)
    texts = [r["text"] for r in _lines(log_.path)]
    kept = [t for t in texts if t.startswith("n")]
    assert kept == sorted(kept, key=lambda t: int(t[1:])) and kept[-1] == "n39", texts
    back = [t for t in texts if t.startswith(E.SAVE_BACK)]
    assert back == [E.SAVE_BACK + E.SAVE_LOST.format(n=lost)], texts


def test_the_retry_memory_holds_the_logs_byte_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "MAX_BYTES", 600)
    log_ = _quiet(EventLog(tmp_path, "s1"))
    _disk(monkeypatch, log_)
    for i in range(30):
        log_.append(make("notice", text=f"row {i} " + "x" * 80))
    log_.flush()
    log_.flush()
    size = sum(len(json.dumps(r, ensure_ascii=False).encode("utf-8")) + 1 for r in log_._pending)
    assert size <= E.MAX_BYTES + 400, size  # the bound, plus the one notice
    assert any("row 29 " in str(r.get("text")) for r in log_._pending)
