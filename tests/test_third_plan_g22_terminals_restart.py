"""Third north-star plan, group 22: terminals across a restart (TM2, TM3, TM6,
TM8, TM12, TM17, TM18, TM19, and the TM7 status code).

* TM2  - a reopened terminal started again at the page's size waited on
  Windows with no limit: a hung start left the pane blank, deaf to keys, its
  working old shell cut off. The second start is bounded; past the bound the
  old shell stays attached and the pane says so in one line.
* TM3  - a terminal that could not be reopened for any reason but "stuck" was
  dropped with a debug log line, and its kept history deleted at the next
  save. It is now kept and listed with its plain reason.
* TM19 - a terminal the start gave up on could not be dismissed; and the cap
  counted a create in flight twice.
* TM12 - a terminal that was running a program came back as a fresh shell
  with no word that the program ended.
* TM6  - the kept output of every terminal was carried by every backup.
* TM8  - "could not be carried over (PermissionError)" after the conversation
  file WAS copied.
* TM17 - Continue and "start fresh" left no record.
* TM18 - the pane's "ask AI" reads and a resize ran on the event loop.

No real shell is started: every backend is a stand-in (events, no sleeps for
time; a bounded join only ever waits in a RED run).
"""

from __future__ import annotations

import asyncio
import base64
import json
import tarfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import iron_jarvis.terminals.backend as be
import iron_jarvis.terminals.manager as mgr_mod
import iron_jarvis.terminals.session as sess_mod
from iron_jarvis.terminals import FakeBackend, TerminalManager, TerminalSession

#: The longest a test waits for a thread. Only a RED run waits this long.
_BOUND_S = 5.0


def _engine_words(text: str) -> list[str]:
    bad = ("WinError", "Errno", "OSError", "PermissionError", "FileNotFound", "Traceback")
    return [w for w in bad if w in text]


def _on_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def _until(cond, timeout: float = _BOUND_S) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


# --------------------------------------------------------------------------- #
# TM2: the second start of a reopened terminal is bounded                      #
# --------------------------------------------------------------------------- #


def _restored(tmp_path, backend=None) -> TerminalSession:
    m = TerminalManager(state_path=tmp_path / "terminals.json")
    entry = {
        "id": "term_r", "shell": "sh", "argv": ["sh"], "cwd": str(tmp_path),
        "cols": 80, "rows": 24,
        "scrollback_b64": base64.b64encode(b"old prompt> ").decode("ascii"),
    }
    return m.restore(entry, backend=backend or FakeBackend())


def test_tm2_a_restart_windows_never_answers_keeps_the_old_shell_usable(tmp_path, monkeypatch):
    s = _restored(tmp_path)
    old = s.backend
    gate = threading.Event()
    made: list[FakeBackend] = []

    def _hangs(self, argv, cwd, env, cols, rows):
        made.append(self)
        gate.wait(60)  # Windows never answers (until the test lets it)
        FakeBackend.start(self, argv, cwd, env, cols, rows)

    # `restart_at_size` builds type(old)(): make THAT start hang.
    old.__class__ = type("Old", (FakeBackend,), {"start": _hangs})
    monkeypatch.setattr(sess_mod, "RESTART_START_WAIT_S", 0.05, raising=False)
    out: dict = {}
    t = threading.Thread(target=lambda: out.setdefault("r", s.restart_at_size(120, 30)), daemon=True)
    try:
        t.start()
        t.join(_BOUND_S)
        assert not t.is_alive(), "the second start waited on Windows with no limit"
        assert out["r"] is False
        # The old shell is still the pane's, at its old size, and takes keys.
        assert s.backend is old and old.is_alive()
        assert (s.cols, s.rows) == (80, 24)
        s.write("dir\n")
        assert s.read() == b"dir\n"
        # One plain line on screen, and nothing cleared (no fresh page drawn).
        text = s.output_tail()
        assert "could not be started again" in text and "Resize the window to try again" in text
        assert not _engine_words(text)
        # The stray start, when Windows finally answers, is closed again.
        gate.set()
        assert _until(lambda: bool(made) and made[0]._killed), "the late shell was left running"
    finally:
        gate.set()
        s.kill()


def test_tm2_after_a_failed_restart_the_next_resize_may_try_again(tmp_path, monkeypatch):
    s = _restored(tmp_path)
    old = s.backend
    gate = threading.Event()

    def _hangs(self, argv, cwd, env, cols, rows):
        if not gate.is_set():
            gate.wait(60)
        FakeBackend.start(self, argv, cwd, env, cols, rows)

    old.__class__ = type("Old", (FakeBackend,), {"start": _hangs})
    monkeypatch.setattr(sess_mod, "RESTART_START_WAIT_S", 0.05, raising=False)
    try:
        assert s.restart_at_size(120, 30) is False
        assert s.restart_wanted() is True  # the page may try again
        gate.set()  # Windows answers now
        assert s.restart_at_size(120, 30) is True
        assert s.backend is not old and (s.cols, s.rows) == (120, 30)
        assert s.restart_wanted() is False
    finally:
        gate.set()
        s.kill()


def test_tm2_a_key_typed_while_the_restart_waits_keeps_the_old_shell(tmp_path):
    s = _restored(tmp_path)
    old = s.backend
    entered = threading.Event()
    gate = threading.Event()
    made: list[FakeBackend] = []

    def _slow(self, argv, cwd, env, cols, rows):
        made.append(self)
        entered.set()
        gate.wait(60)
        FakeBackend.start(self, argv, cwd, env, cols, rows)

    old.__class__ = type("Old", (FakeBackend,), {"start": _slow})
    out: dict = {}
    t = threading.Thread(target=lambda: out.setdefault("r", s.restart_at_size(120, 30)), daemon=True)
    try:
        t.start()
        assert entered.wait(_BOUND_S)
        s.write("x")  # NOT blocked behind the start, and it reaches the old shell
        gate.set()
        t.join(_BOUND_S)
        assert out["r"] is False and s.backend is old
        assert made[0]._killed
    finally:
        gate.set()
        s.kill()


# --------------------------------------------------------------------------- #
# TM3 + TM19: a terminal that could not be reopened is kept, retried, closable #
# --------------------------------------------------------------------------- #

GONE = "gone-program"
HANGS = "hangs-program"


def _boot(
    tmp_path,
    monkeypatch,
    shells: list[str],
    gate: threading.Event | None = None,
    *,
    wait_s: float = _BOUND_S,
    start_lag_s: float = 0.0,
    **kw,
):
    """*wait_s* is how long the boot waits for its restores. A restore that
    must END inside the boot (a shell, a gone program) gets the file's bound,
    which only a RED run reaches (the join returns the moment the restores
    end); only a test whose restore HANGS on purpose passes a short one. A
    real 0.3 s for every test failed on a loaded CI runner (Tests run
    45014562546, backend (10): the TM3 test passed only on its retry).
    *start_lag_s* is that runner: every start takes this long (a real wait,
    it makes the timing WORSE, never better)."""
    gate = gate or threading.Event()

    class _Maybe(FakeBackend):
        def start(self, argv, cwd, env, cols, rows):
            if start_lag_s:
                time.sleep(start_lag_s)
            if argv and argv[0] == GONE:
                raise FileNotFoundError(2, "The system cannot find the file specified")
            if argv and argv[0] == HANGS:
                gate.wait(60)
            return super().start(argv, cwd, env, cols, rows)

    monkeypatch.setattr(sess_mod, "default_backend", lambda: _Maybe())
    monkeypatch.setattr(mgr_mod, "RESTORE_WAIT_S", wait_s)
    sp = tmp_path / "terminals.json"
    entries = [
        {"id": f"term_b{i}", "shell": "sh", "argv": [sh], "cwd": str(tmp_path), "cols": 80, "rows": 24,
         "name": f"pane {i}"}
        for i, sh in enumerate(shells)
    ]
    sp.write_text(json.dumps({"terminals": entries}), encoding="utf-8")
    folder = tmp_path / "terminals-scrollback"
    folder.mkdir()
    for e in entries:
        (folder / f"{e['id']}.bin").write_bytes(b"history of " + e["id"].encode())
    m = TerminalManager(state_path=sp, **kw)
    m._pty_ok = True
    clock = SimpleNamespace(t=1000.0)
    m._now = lambda: clock.t
    return m, entries, clock, gate


def _saved_ids(path: Path) -> list[str]:
    return [e["id"] for e in json.loads(path.read_text(encoding="utf-8"))["terminals"]]


@pytest.mark.parametrize("start_lag_s", [0.0, 0.4], ids=["starts-at-once", "each-start-is-slow"])
def test_tm3_a_terminal_whose_program_is_gone_is_kept_and_listed_with_its_reason(
    tmp_path, monkeypatch, start_lag_s
):
    # "each-start-is-slow" is the loaded runner: under the old 0.3 s boot
    # wait it returned 0, not 1 (the shell was still starting).
    m, entries, _clock, _gate = _boot(tmp_path, monkeypatch, ["sh", GONE], start_lag_s=start_lag_s)
    ok_id, gone_id = entries[0]["id"], entries[1]["id"]
    try:
        assert m.rehydrate() == 1
        # Kept through every save, with its history ...
        m.snapshot()
        m.snapshot_if_changed()
        assert _saved_ids(m.state_path) == [ok_id, gone_id]
        assert (tmp_path / "terminals-scrollback" / f"{gone_id}.bin").is_file()
        # ... listed as failed (the page's existing note counts it) ...
        assert m.restore_failed()["ids"] == [gone_id]
        # ... with its own plain reason.
        panes = m.restore_failed_panes()
        assert [p["id"] for p in panes] == [gone_id]
        why = panes[0]["reason"]
        assert GONE in why and "no longer" in why and not _engine_words(why)
        assert panes[0]["name"] == "pane 1"
    finally:
        m.kill_all()


def test_tm3_try_again_reopens_it_with_its_history(tmp_path, monkeypatch):
    m, entries, _clock, _gate = _boot(tmp_path, monkeypatch, [GONE])
    rid = entries[0]["id"]
    try:
        m.rehydrate()
        # Still gone: refused in words, still listed.
        with pytest.raises(mgr_mod.ReopenFailed) as no:
            m.retry_restore(rid)
        assert GONE in no.value.sentence and not _engine_words(no.value.sentence)
        assert [p["id"] for p in m.restore_failed_panes()] == [rid]
        # The program is back: it reopens under its id, with its history.
        e = json.loads(m.state_path.read_text(encoding="utf-8"))["terminals"][0]
        e["argv"] = ["sh"]
        m._restore_failed[rid] = e
        s = m.retry_restore(rid)
        assert s.id == rid and s.alive and m.get(rid) is s
        assert "history of" in s.output_tail()
        assert m.restore_failed_panes() == [] and m.restore_failed() is None
        assert _saved_ids(m.state_path) == [rid]
    finally:
        m.kill_all()


def test_tm19_close_removes_a_terminal_that_could_not_be_reopened_and_its_history(tmp_path, monkeypatch):
    m, entries, _clock, _gate = _boot(tmp_path, monkeypatch, ["sh", GONE])
    ok_id, gone_id = entries[0]["id"], entries[1]["id"]
    try:
        m.rehydrate()
        assert m.forget_restore(gone_id) is True
        assert m.restore_failed_panes() == [] and m.restore_failed() is None
        assert _saved_ids(m.state_path) == [ok_id]
        assert not (tmp_path / "terminals-scrollback" / f"{gone_id}.bin").exists()
        assert m.forget_restore(gone_id) is False  # nothing left to close
    finally:
        m.kill_all()
    # The next start does not try it again.
    assert gone_id not in _saved_ids(m.state_path)


def test_tm19_a_given_up_terminal_closed_then_starting_late_is_not_brought_back(tmp_path, monkeypatch):
    # The restore hangs on purpose: the boot must stop waiting for it.
    m, entries, clock, gate = _boot(tmp_path, monkeypatch, [HANGS], wait_s=0.3)
    rid = entries[0]["id"]
    try:
        m.rehydrate()
        clock.t += mgr_mod.RESTORE_GIVE_UP_S + 1
        assert m.restore_failed()["ids"] == [rid]
        assert m.restore_failed_panes()[0]["reason"] == mgr_mod.RESTORE_GAVE_UP_WHY
        assert m.forget_restore(rid) is True
        gate.set()  # Windows answers after all
        assert _until(lambda: m._reserved == 0 and not any(t.name.startswith("pane-restore") for t in threading.enumerate()))
        assert m.get(rid) is None, "a terminal the user closed came back"
        assert m._reserved == 0
        assert _saved_ids(m.state_path) == []
    finally:
        gate.set()
        m.kill_all()


def test_tm19_the_cap_counts_a_create_in_flight_once(tmp_path, monkeypatch):
    entered, gate = threading.Event(), threading.Event()

    class _Slow(FakeBackend):
        def start(self, argv, cwd, env, cols, rows):
            entered.set()
            gate.wait(60)
            super().start(argv, cwd, env, cols, rows)

    m = TerminalManager(max_sessions=2)
    m._pty_ok = True
    monkeypatch.setattr(sess_mod, "default_backend", lambda: _Slow())
    t = threading.Thread(target=lambda: m.create(cwd=str(tmp_path)), daemon=True)
    try:
        t.start()
        assert entered.wait(_BOUND_S)
        # One terminal is starting; the cap is two: there is room for one more.
        second = m.create(cwd=str(tmp_path), backend=FakeBackend())
        assert second.alive
        with pytest.raises(mgr_mod.SessionCapReached):
            m.create(cwd=str(tmp_path), backend=FakeBackend())
    finally:
        gate.set()
        t.join(_BOUND_S)
        m.kill_all()


def test_tm3_restore_checks_the_folder_within_a_bound(tmp_path, monkeypatch):
    """`restore` asked `Path.is_dir()` with no bound: a folder on a drive that
    does not answer held the boot's restore thread for as long as Windows did."""
    import os as _os

    real_stat = _os.stat
    gate = threading.Event()
    slow = str(tmp_path / "on-a-sleeping-drive")

    def _stat(path, *a, **k):
        if _os.fspath(path) == slow:
            gate.wait(60)
        return real_stat(path, *a, **k)

    monkeypatch.setattr(_os, "stat", _stat)
    monkeypatch.setattr(be, "FOLDER_CHECK_WAIT_S", 0.05)
    m = TerminalManager()
    entry = {"id": "term_slow", "shell": "sh", "argv": ["sh"], "cwd": slow, "cols": 80, "rows": 24}
    out: dict = {}
    t = threading.Thread(target=lambda: out.setdefault("s", m.restore(entry, backend=FakeBackend())), daemon=True)
    try:
        t.start()
        t.join(_BOUND_S)
        assert not t.is_alive(), "the folder check waited on the drive with no limit"
        assert out["s"].cwd == str(Path.home())  # opened in the home folder instead
    finally:
        gate.set()
        m.kill_all()


# --------------------------------------------------------------------------- #
# TM12: a program that was running ended, and the terminal says so            #
# --------------------------------------------------------------------------- #


def test_tm12_the_shutdown_snapshot_names_the_program_running_in_a_terminal(tmp_path, monkeypatch):
    m = TerminalManager(state_path=tmp_path / "terminals.json")
    s = m.create(cwd=str(tmp_path), backend=FakeBackend())
    monkeypatch.setattr(TerminalSession, "running_program", lambda self: "npm", raising=False)
    m.kill_all()  # the shutdown snapshot
    saved = json.loads(m.state_path.read_text(encoding="utf-8"))["terminals"]
    assert saved[0]["id"] == s.id and saved[0].get("running") == "npm"


def test_tm12_a_restored_terminal_says_the_program_ended_and_nothing_was_rerun(tmp_path):
    m = TerminalManager(state_path=tmp_path / "terminals.json")
    entry = {
        "id": "term_p", "shell": "sh", "argv": ["sh"], "cwd": str(tmp_path), "cols": 80, "rows": 24,
        "scrollback_b64": base64.b64encode(b"> npm run dev\r\nlistening\r\n").decode("ascii"),
        "running": "npm",
    }
    s = m.restore(entry, backend=FakeBackend())
    try:
        text = s.output_tail()
        line = mgr_mod.PROGRAM_ENDED_NOTE.format(program="npm")
        assert line in text
        assert text.index("listening") < text.index(line)  # after the old output
        # ...and ABOVE the fresh page the new shell draws on: ConPTY places
        # the cursor by absolute row on a screen it believes is blank, so a
        # line after the clear drew every typed key over the shell's banner.
        raw = bytes(s._tail).decode("utf-8", "replace")
        assert raw.index(line) < raw.rindex("\x1b[2J\x1b[H")
        assert raw.count(line) == 1
    finally:
        s.kill()


def test_tm12_a_coding_tool_offered_for_resume_gets_no_such_line(tmp_path):
    m = TerminalManager(state_path=tmp_path / "terminals.json")
    entry = {
        "id": "term_c", "shell": "sh", "argv": ["sh"], "cwd": str(tmp_path), "cols": 80, "rows": 24,
        "agent_cli": "codex", "running": "node",
    }
    s = m.restore(entry, backend=FakeBackend())
    try:
        assert "has ended" not in s.output_tail()
        assert s.resume_cli == "codex"
    finally:
        s.kill()


def test_tm12_running_program_names_the_shells_first_child(monkeypatch):
    s = TerminalSession(cwd="/w", shell="sh", argv=["sh"], backend=FakeBackend()).start()
    monkeypatch.setattr(TerminalSession, "shell_pid", property(lambda self: 4242))

    class _Kid:
        def __init__(self, n):
            self._n = n

        def name(self):
            return self._n

    monkeypatch.setattr(TerminalSession, "_console_children", lambda self, shell: [_Kid("conhost.exe"), _Kid("node.exe")])
    import psutil

    monkeypatch.setattr(psutil, "Process", lambda pid: SimpleNamespace(pid=pid))
    try:
        assert s.running_program() == "node"
        monkeypatch.setattr(TerminalSession, "_console_children", lambda self, shell: [])
        assert s.running_program() is None
    finally:
        s.kill()


# --------------------------------------------------------------------------- #
# TM6: kept output stays out of backups; a closed terminal's is deleted       #
# --------------------------------------------------------------------------- #


def test_tm6_a_backup_keeps_the_list_of_terminals_but_not_their_output(tmp_path):
    from iron_jarvis.maintenance import create_backup

    home = tmp_path / ".ironjarvis"
    home.mkdir()
    (home / "terminals-scrollback").mkdir()
    (home / "terminals-scrollback" / "term_a.bin").write_bytes(b"what the program printed")
    (home / "terminals.json").write_text(json.dumps({"terminals": [
        {"id": "term_a", "cwd": "/w", "shell": "sh",
         "scrollback_b64": base64.b64encode(b"inline history").decode("ascii")},
    ]}), encoding="utf-8")
    (home / "config.toml").write_text("x = 1\n", encoding="utf-8")
    out, _n = create_backup(home, tmp_path / "b.tar.gz")
    with tarfile.open(out) as tar:
        names = tar.getnames()
        assert not [n for n in names if "terminals-scrollback" in n], names
        member = next(n for n in names if n.endswith("terminals.json"))
        saved = json.loads(tar.extractfile(member).read().decode("utf-8"))
    assert [e["id"] for e in saved["terminals"]] == ["term_a"]  # reopens after a restore
    assert "scrollback_b64" not in saved["terminals"][0]  # with empty history
    assert any(n.endswith("config.toml") for n in names)


def test_tm6_closing_a_terminal_deletes_its_kept_output(tmp_path):
    m = TerminalManager(state_path=tmp_path / "terminals.json")
    s = m.create(cwd=str(tmp_path), backend=FakeBackend())
    s._ingest(b"secret-ish output\r\n")
    m.snapshot()
    kept = tmp_path / "terminals-scrollback" / f"{s.id}.bin"
    assert kept.is_file()
    m.kill(s.id)
    assert not kept.exists()


# --------------------------------------------------------------------------- #
# TM8: what is said after a carry that copied the conversation               #
# --------------------------------------------------------------------------- #


def test_tm8_a_side_folder_that_cannot_be_copied_still_carries_the_conversation(tmp_path, monkeypatch):
    from iron_jarvis.terminals import continue_on

    src_dir = tmp_path / "a" / "projects" / "C--work"
    src_dir.mkdir(parents=True)
    conv = src_dir / "abc.jsonl"
    conv.write_text('{"type":"user"}\n', encoding="utf-8")
    (src_dir / "abc").mkdir()
    (src_dir / "abc" / "side.txt").write_text("x", encoding="utf-8")
    next_home = tmp_path / "b"
    next_home.mkdir()

    def _refuse(*_a, **_k):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(continue_on, "_copy_missing_tree", _refuse)
    got = continue_on.carry_over(conv, str(next_home), "Second", trash_root=tmp_path / "trash")
    assert (next_home / "projects" / "C--work" / "abc.jsonl").is_file()  # it WAS copied
    assert got.resumed is True and got.session_id == "abc"
    assert "could not be carried over" not in got.note
    assert not _engine_words(got.note)
    assert "Second" in got.note


# --------------------------------------------------------------------------- #
# Through the routes: TM7's status, the cap's words, TM3/TM19, TM17, TM18     #
# --------------------------------------------------------------------------- #


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    from iron_jarvis.daemon.app import create_app

    monkeypatch.setattr(sess_mod, "default_backend", FakeBackend)
    root = tmp_path / "root"
    root.mkdir()
    app = create_app(str(root))
    with TestClient(app) as client:
        client.app.state.platform.terminals._pty_ok = True
        yield client


def test_tm7_a_gone_folder_is_the_users_400_with_its_sentence(app_client, tmp_path):
    # A stand-in backend does not check: the real backends do (`check_start_folder`).
    terms = app_client.app.state.platform.terminals

    def _gone(*_a, **_k):
        raise be.StartFolderGone("The folder 'nope' no longer exists, so no terminal was opened.")

    terms.create = _gone
    r = app_client.post("/terminals", json={"cwd": str(tmp_path / "nope"), "cols": 80, "rows": 24})
    assert r.status_code == 400, r.text
    assert r.json()["detail"].startswith("The folder 'nope' no longer exists")
    r = app_client.post("/terminals/launch", json={"cli": "claude", "cwd": str(tmp_path / "nope")})
    assert r.status_code == 400, r.text


def test_only_the_real_cap_reads_too_many_terminals(app_client):
    terms = app_client.app.state.platform.terminals

    def _other(*_a, **_k):
        raise RuntimeError("something else went wrong inside")

    terms.create = _other
    r = app_client.post("/terminals", json={"cols": 80, "rows": 24})
    assert r.status_code != 429, r.text
    assert "Too many terminals" not in r.json()["detail"]
    assert "something else" not in r.json()["detail"]

    def _cap(*_a, **_k):
        raise mgr_mod.SessionCapReached("terminal session cap reached (20)")

    terms.create = _cap
    r = app_client.post("/terminals", json={"cols": 80, "rows": 24})
    assert r.status_code == 429 and "Too many terminals" in r.json()["detail"]


def test_tm3_the_routes_list_try_again_and_close_a_terminal_that_could_not_be_reopened(app_client, tmp_path):
    terms = app_client.app.state.platform.terminals
    entry = {"id": "term_x", "shell": "sh", "argv": [GONE], "cwd": str(tmp_path), "name": "notes"}
    with terms._lock:
        terms._restore_failed["term_x"] = entry
        terms._restore_failed_why["term_x"] = f"Its program, {GONE}, is no longer on this PC."
    body = app_client.get("/terminals").json()
    assert body["restore_failed"]["ids"] == ["term_x"]
    from iron_jarvis.terminals.shells import default_shell

    # Fourth plan C-TM-1: a row whose program is gone also names the usual
    # shell its "Open with …" press reopens it on.
    assert body["restore_failed_panes"] == [
        {"id": "term_x", "name": "notes", "cwd": str(tmp_path),
         "reason": f"Its program, {GONE}, is no longer on this PC.",
         "usual_shell": default_shell()["label"]},
    ]
    r = app_client.post("/terminals/not-reopened/term_x/retry")
    assert r.status_code == 200, r.text
    assert r.json()["id"] == "term_x" and r.json()["alive"] is True
    assert "restore_failed_panes" not in app_client.get("/terminals").json()
    # Close: a second one, dismissed.
    with terms._lock:
        terms._restore_failed["term_y"] = dict(entry, id="term_y")
    assert app_client.delete("/terminals/not-reopened/term_y").json() == {"closed": True}
    assert "restore_failed" not in app_client.get("/terminals").json()
    assert app_client.delete("/terminals/not-reopened/term_y").status_code == 404


def test_tm17_continue_leaves_a_record_of_who_what_and_how_it_ended(app_client, monkeypatch, tmp_path):
    from iron_jarvis.daemon.routes import terminals as routes
    from iron_jarvis.iron_proxy import accounts as _acc
    from iron_jarvis.terminals import continue_on
    from iron_jarvis.terminals import pane_accounts as _pa

    c = app_client
    pane = c.post("/terminals", json={"cols": 80, "rows": 24}).json()
    bus = c.app.state.platform.event_bus

    def _refused(*_a, **_k):
        raise continue_on.ContinueRefused("No other Claude account can take over right now.")

    monkeypatch.setattr(continue_on, "prepare", _refused)
    assert c.post(f"/terminals/{pane['id']}/continue-on-next").status_code == 409
    ev = [e for e in bus.history if e.type == routes.TERMINAL_CONTINUED][-1]
    assert ev.payload["terminal"] == pane["id"] and ev.payload["outcome"] == "refused"
    assert ev.payload["detail"] == "No other Claude account can take over right now."

    lease = _acc.make_lease(_pa.SPECS["anthropic"], "p2", "Second", str(tmp_path / "b"), [])
    plan = continue_on.Plan(
        from_rec={"id": "p1", "title": "First"}, lease=lease, parked=True, told="",
        carried=continue_on.Carried(True, "abc", "Carried your conversation over.", tmp_path / "x.jsonl"),
    )
    monkeypatch.setattr(continue_on, "prepare", lambda *_a, **_k: plan)
    new = TerminalSession(cwd=str(tmp_path), shell="sh", argv=["sh"], backend=FakeBackend()).start()
    monkeypatch.setattr(routes, "launch_pane", lambda *_a, **_k: new)
    r = c.post(f"/terminals/{pane['id']}/continue-on-next")
    assert r.status_code == 200, r.text
    ev = [e for e in bus.history if e.type == routes.TERMINAL_CONTINUED][-1]
    assert ev.payload == {
        "terminal": pane["id"], "new_terminal": new.id, "from": "First", "to": "Second",
        "resumed": True, "parked": True, "outcome": "continued",
    }
    new.kill()


def test_tm17_start_fresh_leaves_a_record(app_client, tmp_path):
    from iron_jarvis.daemon.routes import terminals as routes

    c = app_client
    pane = c.post("/terminals", json={"cols": 80, "rows": 24}).json()
    # No carried conversation: refused, and that is recorded too.
    assert c.post(f"/terminals/{pane['id']}/start-fresh-with-handoff").status_code == 409
    ev = [e for e in c.app.state.platform.event_bus.history if e.type == routes.TERMINAL_STARTED_FRESH][-1]
    assert ev.payload["terminal"] == pane["id"] and ev.payload["outcome"] == "refused"
    assert "conversation" in ev.payload["detail"]


def test_tm18_the_assists_reads_run_off_the_event_loop(app_client, monkeypatch):
    c = app_client
    p = c.app.state.platform
    seen: dict[str, bool] = {}

    def _apply(system):
        seen["lessons"] = _on_loop()
        return system

    def _search(prompt, k=2):
        seen["skills"] = _on_loop()
        return []

    monkeypatch.setattr(p.learning, "apply_to_prompt", _apply)
    monkeypatch.setattr(p.skills, "search", _search)

    async def _complete(provider, adapter, *, system, messages):
        return SimpleNamespace(text="ok"), provider, "m"

    monkeypatch.setattr(c.app.state.d, "_one_shot_complete", _complete)
    monkeypatch.setattr(p.providers, "get", lambda *_a, **_k: object())
    pane = c.post("/terminals", json={"cols": 80, "rows": 24}).json()
    r = c.post(f"/terminals/{pane['id']}/ai", json={"prompt": "what failed?"})
    assert r.status_code == 200, r.text
    assert seen == {"lessons": False, "skills": False}


def test_tm18_a_resize_runs_off_the_event_loop(app_client, monkeypatch):
    c = app_client
    pane = c.post("/terminals", json={"cols": 80, "rows": 24}).json()
    session = c.app.state.platform.terminals.get(pane["id"])
    seen: list[bool] = []
    done = threading.Event()
    real = session.backend.resize

    def _resize(cols, rows):
        seen.append(_on_loop())
        real(cols, rows)
        if (cols, rows) == (100, 30):
            done.set()

    session.backend.resize = _resize
    with c.websocket_connect(f"/terminals/{pane['id']}/ws") as ws:
        ws.send_text(json.dumps({"type": "resize", "cols": 100, "rows": 30}))
        assert done.wait(_BOUND_S)
    assert seen and not any(seen), "a resize ran on the event loop"
