"""v1.338.0 — the UPDATE REHEARSAL: a real daemon is stopped and started again.

Two user bugs in one week lived ONLY on the restart path, where no test went:
v1.337.1 (every backup failed after v1.335.0 added a locked file to the home)
and v1.337.2 (a terminal restored after an update typed "choppy, only a few
characters"). Unit tests of each piece were green both times — what failed was
the whole app coming back.

So this file runs the REAL daemon (a subprocess on a free port with a
throwaway home), stops it the two ways the user's PC does — the update's
graceful stop (``POST /shutdown``, what the desktop app sends) and a hard kill
(a crash, a power cut) — starts it again on the same home, and checks what the
user would notice:

* a backup is written, without the daemon's own lock file in it, and copied to
  the backup copy folder set BEFORE the restart (settings survive);
* a Build terminal comes back and TYPES CORRECTLY — the bytes are drawn by
  @xterm/headless at the pane's own xterm version (``dashboard/scripts/
  render-terminal.cjs``) when Node and the dashboard packages are there (the
  Release gate installs them before the Python suite), else checked as bytes;
* the work a crash leaves half-done is recovered: a tool call that was running
  is recorded as cut off, a chat answer being written is kept and marked
  interrupted, an agent job is offered for Continue and Continue resumes its
  own conversation;
* (readiness 12A review) a database an OLDER app made gains the columns this
  one added (``session.failure_detail``), nullable, and its rows still read;
* after a hard kill the data folder is not left locked and the terminal is
  still restored;
* (v1.391.0, the long-jobs plan's Phase 2) a CODING SESSION's reply stopped
  mid-way — by the update's stop and by a hard kill — carries on by itself
  after the restart from its last finished step: no finished step runs again
  (the tool ledger, and the model's own record of each call by its id), each
  file is written once, and the folder ends with the files a reply that never
  stopped wrote. A reply that was WAITING on the user is not carried on, and
  a reply stopped again while it carried on is carried on ONCE more, never
  twice.

Windows only, like the product (ConPTY terminals).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import httpx
import psutil
import pytest

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="the product's restart path is Windows"),
    pytest.mark.timeout(300),
]

REPO = Path(__file__).resolve().parents[1]
RENDER = REPO / "dashboard" / "scripts" / "render-terminal.cjs"
HEADLESS = REPO / "dashboard" / "node_modules" / "@xterm" / "headless"
#: Wide enough that the prompt (a pytest temp path, longer under xdist and on
#: CI) plus the typed command never wraps: ConPTY draws a wrapped line as two
#: separate rows, so a wrap would read as a broken line.
COLS, ROWS = 220, 30

FAKE_TUI = """\
import sys, time
for i in range(45):
    sys.stdout.write("old tui line %d\\r\\n" % i)
sys.stdout.write(
    "\\x1b[?1004h\\x1b[?2004h\\x1b[?1000h\\x1b[?1002h\\x1b[?1003h\\x1b[?1006h"
    "\\x1b[?25l\\x1b[12;5H> type here FAKE-TUI-READY"
)
sys.stdout.flush()
time.sleep(600)
"""


# --------------------------------------------------------------------------- #
# A real daemon in a subprocess
# --------------------------------------------------------------------------- #
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Daemon:
    def __init__(self, root: Path, log: Path) -> None:
        self.root = root
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        env = {k: v for k, v in os.environ.items() if k != "IRONJARVIS_TOKEN"}
        self._log = open(log, "ab")
        self.proc = subprocess.Popen(
            [sys.executable, "-c", "from iron_jarvis.daemon.cli import app; app()",
             "serve", "--host", "127.0.0.1", "--port", str(self.port), "--root", str(root)],
            cwd=str(REPO), env=env, stdout=self._log, stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            assert self.proc.poll() is None, f"daemon exited: {log.read_text(errors='replace')[-2000:]}"
            try:
                if httpx.get(self.base + "/health", timeout=2).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
        raise AssertionError(f"daemon never answered: {log.read_text(errors='replace')[-2000:]}")

    def get(self, path: str, **kw):
        return httpx.get(self.base + path, timeout=30, **kw)

    def post(self, path: str, **kw):
        return httpx.post(self.base + path, timeout=30, **kw)

    def put(self, path: str, **kw):
        return httpx.put(self.base + path, timeout=30, **kw)

    def ws(self, path: str):
        from websockets.sync.client import connect

        return connect(f"ws://127.0.0.1:{self.port}{path}", max_size=None, open_timeout=15)

    def stop(self) -> None:
        """The update's stop: what the desktop app sends before installing."""
        self.post("/shutdown")
        self.proc.wait(timeout=90)
        self._log.close()

    def crash(self) -> list[psutil.Process]:
        """A hard kill. Returns what it leaves running (the panes' shells), for
        the test to clean up — a real crash leaves them too."""
        kids = psutil.Process(self.proc.pid).children(recursive=True)
        self.proc.kill()
        self.proc.wait(timeout=30)
        self._log.close()
        return kids

    def close(self) -> None:
        if self.proc.poll() is None:
            for k in psutil.Process(self.proc.pid).children(recursive=True):
                try:
                    k.kill()
                except psutil.Error:
                    pass
            self.proc.kill()
            self.proc.wait(timeout=30)
        if not self._log.closed:
            self._log.close()


def _collect(c, until, secs: float) -> list[bytes]:
    """Frames from a pane socket until ``until(joined)`` or ``secs``."""
    out: list[bytes] = []
    deadline = time.monotonic() + secs
    while time.monotonic() < deadline:
        try:
            m = c.recv(timeout=max(0.05, deadline - time.monotonic()))
        except TimeoutError:
            break
        out.append(m if isinstance(m, bytes) else m.encode())
        if until(b"".join(out)):
            break
    return out


def _type(c, text: str) -> None:
    for ch in text:
        c.send(ch)
        time.sleep(0.02)


def _run_line(shell: str, script: Path) -> str:
    exe, path = sys.executable, str(script)
    if shell in ("pwsh", "powershell"):
        return f'& "{exe}" "{path}"\r'
    return f'"{exe}" "{path}"\r'


def _render(stream: bytes, tmp: Path) -> dict | None:
    node = shutil.which("node")
    if not node or not HEADLESS.is_dir():
        return None
    f = tmp / "stream.bin"
    f.write_bytes(stream)
    out = subprocess.run(
        [node, str(RENDER), str(f), str(COLS), str(ROWS)],
        cwd=str(REPO / "dashboard"), capture_output=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr.decode(errors="replace")
    return json.loads(out.stdout)


def _open_pane(d: Daemon, work: Path) -> dict:
    row = d.post("/terminals", json={"cwd": str(work)}).json()
    assert row.get("id"), row
    return row


def _start_fake_tui(d: Daemon, pane: dict, tmp: Path) -> None:
    script = tmp / "fake_tui.py"
    script.write_text(FAKE_TUI, encoding="utf-8")
    with d.ws(f"/terminals/{pane['id']}/ws") as c:
        _collect(c, lambda b: b">" in b, 20)  # the shell's prompt
        c.send(json.dumps({"type": "resize", "cols": COLS, "rows": ROWS}))
        _collect(c, lambda b: False, 1.0)
        _type(c, _run_line(pane["shell"], script))
        got = b"".join(_collect(c, lambda b: b"FAKE-TUI-READY" in b, 30))
        assert b"FAKE-TUI-READY" in got, got[-500:]


def _type_into_restored(d: Daemon, pane_id: str) -> bytes:
    """Attach as a Build pane does, type a command, return EVERYTHING the pane
    was sent, in order: replay, the end-of-replay marker, the new shell."""
    with d.ws(f"/terminals/{pane_id}/ws") as c:
        frames = _collect(c, lambda b: False, 2.5)
        assert any(len(f) == 0 for f in frames), "no end-of-replay marker"
        c.send(json.dumps({"type": "resize", "cols": COLS, "rows": ROWS}))
        frames += _collect(c, lambda b: False, 2.0)
        _type(c, "echo rehearsal-ok\r")
        frames += _collect(
            c, lambda b: _ran(b) and b.rstrip().endswith(b">"), 20
        )
    return b"".join(frames)


def _ran(stream: bytes) -> bool:
    """The command's OUTPUT line arrived. (The typed command itself is echoed
    a key at a time with cursor moves between them, so it never appears as
    one run of bytes.)"""
    plain = re.sub(rb"\x1b\[[0-9;?]*[A-Za-z]", b"", stream)
    return re.search(rb"\nrehearsal-ok\r?\n", plain) is not None


def _assert_types_normally(stream: bytes, tmp: Path, *, old_screen_kept: bool = True) -> None:
    assert _ran(stream), stream[-800:]
    view = _render(stream, tmp)
    if view is None:  # no Node here (the Tests workflow's Python job): bytes only
        return
    screen = view["lines"]  # soft-wrapped rows joined: a long prompt wraps
    hits = [i for i, line in enumerate(screen) if re.search(r">\s?echo rehearsal-ok\s*$", line)]
    assert hits, "the typed command is not on screen after a prompt:\n" + "\n".join(screen)
    i = hits[-1]
    assert screen[i + 1].strip() == "rehearsal-ok", "\n".join(screen)
    # The v1.337.2 bug drew the keys over the old program's lines.
    assert not any("old tui line" in line for line in screen), "\n".join(screen)
    if old_screen_kept:  # a crash loses up to the last 30 s of scrollback
        assert any("old tui line 44" in line for line in view["scrollback"]), (
            "the old screen should stay readable in the scrollback"
        )


def _assert_backup(d: Daemon, mirror: Path | None = None) -> None:
    r = d.post("/diagnostics/repair", json={"action": "backup_now"})
    assert r.status_code == 200, r.text
    archive = Path(r.json()["result"])
    with tarfile.open(archive, "r:gz") as tar:
        names = {Path(m.name).name for m in tar.getmembers()}
    assert "ironjarvis.db" in names  # the consistent snapshot, always there
    assert "daemon.lock" not in names and "daemon-owner.json" not in names
    if mirror is not None:
        assert (mirror / archive.name).is_file(), sorted(p.name for p in mirror.iterdir())


# --------------------------------------------------------------------------- #
# What a crash leaves half-done, written while the daemon is down
# --------------------------------------------------------------------------- #
def _plant_cut_off_work(home: Path, workspace: Path) -> dict[str, str]:
    from iron_jarvis.core.db import make_engine, session_scope
    from iron_jarvis.core.ids import new_id
    from iron_jarvis.core.models import (
        AgentRun,
        AgentState,
        ChatTurnRecord,
        PermissionMode,
        RunTranscript,
        Session,
        SessionStatus,
        ToolIntent,
    )

    engine = make_engine(home / "ironjarvis.db")
    try:
        with session_scope(engine) as db:
            sess = Session(task="tidy the notes", status=SessionStatus.ACTIVE,
                           workspace_path=str(workspace))
            db.add(sess)
            db.flush()
            run = AgentRun(session_id=sess.id, state=AgentState.RUNNING)
            db.add(run)
            db.flush()
            msgs = [
                {"role": "user", "content": "tidy the notes"},
                {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "w1", "name": "write_file", "arguments": {"path": "n.txt", "content": "new"}}]},
            ]
            db.add(RunTranscript(run_id=run.id, session_id=sess.id, messages_json=json.dumps(msgs)))
            intent = new_id("tool")
            db.add(ToolIntent(id=intent, session_id=sess.id, agent_run_id=run.id, tool="write_file",
                              args_json='{"path": "n.txt"}', verdict=PermissionMode.ALLOW,
                              reversibility="reversible", replay_safe=False))
            db.add(ChatTurnRecord(id="turn-rehearsal", thread_id="th-rehearsal", status="running",
                                  frames_json=json.dumps([["token", json.dumps({"text": "half an answ"})]])))
            db.commit()
            return {"session": sess.id, "run": run.id, "intent": intent}
    finally:
        engine.dispose()


def _as_older_schema(home: Path) -> None:
    """The database an older app left: no ``session.failure_detail`` column
    (readiness 12A added it). Written while the daemon is down."""
    con = sqlite3.connect(home / "ironjarvis.db", timeout=10)
    try:
        con.execute('ALTER TABLE "session" DROP COLUMN "failure_detail"')
        con.commit()
    finally:
        con.close()
    assert "failure_detail" not in _session_columns(home), "anti-vacuity: the older shape"


def _session_columns(home: Path) -> dict[str, int]:
    """``{column: notnull}`` of the session table as it is on disk."""
    con = sqlite3.connect(f"file:{home / 'ironjarvis.db'}?mode=ro", uri=True, timeout=10)
    try:
        return {r[1]: r[3] for r in con.execute('PRAGMA table_info("session")')}
    finally:
        con.close()


def _ledger_row(home: Path, inv_id: str):
    con = sqlite3.connect(f"file:{home / 'ironjarvis.db'}?mode=ro", uri=True, timeout=10)
    try:
        return con.execute("select ok, output from toolinvocation where id = ?", (inv_id,)).fetchone()
    finally:
        con.close()


def _options(home: Path, session_id: str) -> dict:
    con = sqlite3.connect(f"file:{home / 'ironjarvis.db'}?mode=ro", uri=True, timeout=10)
    try:
        row = con.execute("select options_json from session where id = ?", (session_id,)).fetchone()
    finally:
        con.close()
    return json.loads((row[0] if row else "") or "{}")


def _frames(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.split("\n\n"):
        ev = data = None
        for line in block.splitlines():
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
        if ev and data:
            out.append((ev, json.loads(data)))
    return out


# --------------------------------------------------------------------------- #
# The rehearsals
# --------------------------------------------------------------------------- #
def test_an_update_restart_brings_everything_back(tmp_path):
    root, work, mirror = tmp_path / "root", tmp_path / "work", tmp_path / "mirror"
    for p in (root, work, mirror):
        p.mkdir()
    home = root / ".ironjarvis"
    log = tmp_path / "daemon.log"

    first = Daemon(root, log)
    try:
        r = first.put("/settings", json={"values": {"backup_mirror_dir": str(mirror)}})
        assert r.status_code == 200, r.text
        pane = _open_pane(first, work)
        _start_fake_tui(first, pane, tmp_path)
        _assert_backup(first, mirror)  # the v1.337.1 bug: a LIVE daemon's backup
        first.stop()
    finally:
        first.close()

    planted = _plant_cut_off_work(home, work)
    # Readiness 12A review: the update comes FROM an older app, whose database
    # has no `session.failure_detail` (the column 12A added): the restarted
    # daemon's boot reconciler must add it, and every row an older app wrote
    # must read as "nothing folded" (NULL), never a failed read.
    _as_older_schema(home)

    second = Daemon(root, log)
    try:
        # Settings survived: the copy folder still gets the backup.
        _assert_backup(second, mirror)

        # The older database gained the new column (nullable) and its rows read.
        assert _session_columns(home)["failure_detail"] == 0, "added, and nullable"
        row = second.get(f"/sessions/{planted['session']}")
        assert row.status_code == 200, row.text
        assert row.json()["session"]["failure_detail"] == ""
        assert second.get("/sessions").status_code == 200

        # The terminal came back and types like a fresh one.
        rows = second.get("/terminals").json()
        rows = rows if isinstance(rows, list) else rows["terminals"]
        assert [r["id"] for r in rows if r["alive"]] == [pane["id"]]
        _assert_types_normally(_type_into_restored(second, pane["id"]), tmp_path)

        # The tool call that was running is on the record as cut off.
        row = _ledger_row(home, planted["intent"])
        assert row is not None and row[0] == 0 and "may have landed" in row[1], row

        # The chat answer being written is kept and says it was cut off.
        turns = second.get("/chat/turns", params={"thread_id": "th-rehearsal"}).json()["turns"]
        assert [(t["turn_id"], t["status"]) for t in turns] == [("turn-rehearsal", "interrupted")]
        frames = _frames(second.get("/chat/turns/turn-rehearsal/stream").text)
        assert frames[0] == ("token", {"text": "half an answ"})
        assert frames[-1][0] == "error"

        # The job is offered for Continue, and Continue resumes its conversation.
        offered = [s["id"] for s in second.get("/sessions/interrupted").json()["sessions"]]
        assert planted["session"] in offered
        new = second.post(f"/sessions/{planted['session']}/continue",
                          json={"message": "Continue where you left off", "wait": False, "resume": True})
        assert new.status_code in (200, 201), new.text
        assert _options(home, new.json()["id"]).get("resume_run") == planted["run"]
        second.stop()
    finally:
        second.close()


def test_a_crash_leaves_the_data_folder_usable(tmp_path):
    root, work = tmp_path / "root", tmp_path / "work"
    root.mkdir()
    work.mkdir()
    log = tmp_path / "daemon.log"

    first = Daemon(root, log)
    leftovers: list[psutil.Process] = []
    try:
        pane = _open_pane(first, work)
        _start_fake_tui(first, pane, tmp_path)
        leftovers = first.crash()
    finally:
        first.close()

    try:
        # The lock died with the process: the next daemon starts at once.
        second = Daemon(root, log)
        try:
            rows = second.get("/terminals").json()
            rows = rows if isinstance(rows, list) else rows["terminals"]
            assert [r["id"] for r in rows if r["alive"]] == [pane["id"]]
            # The pane's size was saved before it was resized (a crash inside
            # the 30 s snapshot window), so this also proves the restored shell
            # is started again at the pane's real size (restart_at_size).
            _assert_types_normally(
                _type_into_restored(second, pane["id"]), tmp_path, old_screen_kept=False
            )
            _assert_backup(second)
            second.stop()
        finally:
            second.close()
    finally:
        for p in leftovers:
            try:
                p.kill()
            except psutil.Error:
                pass


# --------------------------------------------------------------------------- #
# v1.391.0: a coding reply carries on by itself after the restart
# --------------------------------------------------------------------------- #
#: Each reply's files, in the order it writes them (one tool call a step).
_CODING_FILES = {
    "A": [("one.txt", "1"), ("two.txt", "2"), ("three.txt", "3")],  # stopped mid-way
    "B": [("waits.txt", "w")],                                        # parked on a card
    "C": [("one.txt", "1"), ("two.txt", "2"), ("three.txt", "3")],  # never stopped
}


def _coding_work(tmp: Path, name: str) -> Path:
    folder = tmp / f"work-{name}"
    (folder / ".ironjarvis" / "harness").mkdir(parents=True)
    # The finish check would add one round of its own: off, so each reply is
    # exactly its files (the finish check is not what this rehearses).
    (folder / ".ironjarvis" / "harness" / "finish.toml").write_text("enabled = false\n", encoding="utf-8")
    return folder


def _use_the_coder(d: Daemon, coder) -> None:
    r = d.put("/settings", json={"values": {
        "custom_base_url": coder.url + "/v1", "custom_model": "scripted",
        "default_provider": "custom", "default_model": "scripted",
    }})
    assert r.status_code == 200, r.text


def _coding_session(d: Daemon, folder: Path, **extra) -> str:
    r = d.post("/build/sessions", json={"folder": str(folder), "provider": "custom", "model": "scripted",
                                        "branch": False, **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _ask(d: Daemon, sid: str, text: str, turn_id: str) -> None:
    """Send a message; the reply is the DAEMON's (it runs on after the
    request closes), so the request is let go at its first bytes."""
    with httpx.stream("POST", f"{d.base}/build/sessions/{sid}/turn",
                      json={"text": text, "turn_id": turn_id}, timeout=30) as r:
        assert r.status_code == 200, r.read()
        for _ in r.iter_bytes():
            break


def _until(pred, secs: float, what: str):
    deadline = time.monotonic() + secs
    while time.monotonic() < deadline:
        got = pred()
        if got:
            return got
        time.sleep(0.2)
    raise AssertionError(f"timed out waiting for {what}")


def _ro(home: Path):
    return sqlite3.connect(f"file:{home / 'ironjarvis.db'}?mode=ro", uri=True, timeout=10)


def _lines(home: Path, sid: str) -> list[dict]:
    con = _ro(home)
    try:
        row = con.execute("select messages_json from chatthreadrecord where id = ?", (sid,)).fetchone()
    finally:
        con.close()
    return json.loads((row[0] if row else "") or "[]")


def _written(home: Path, sid: str) -> list[str]:
    """Every write_file that RAN for the session (the tool ledger), by file."""
    con = _ro(home)
    try:
        rows = con.execute(
            "select args_json from toolinvocation where session_id = ? and tool = 'write_file' and ok = 1",
            (f"build:{sid}",),
        ).fetchall()
    finally:
        con.close()
    return sorted(Path(json.loads(a or "{}").get("path", "")).name for (a,) in rows)


def _files(folder: Path) -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in folder.iterdir() if p.is_file()}


def _reply_done(home: Path, sid: str, turn_id: str = ""):
    for m in _lines(home, sid):
        if m.get("role") == "assistant" and not m.get("error") and (not turn_id or m.get("turnId") == turn_id) \
                and str(m.get("content") or "").startswith("Made "):
            return m
    return None


def _running(d: Daemon) -> list[dict]:
    try:
        return d.get("/chat/turns/running").json().get("replies") or []
    except httpx.HTTPError:
        return []


def _carry_ons(lines: list[dict]) -> list[str]:
    """The cut-off turns the app carried on, one entry per carry-on."""
    return [m["resumedFrom"] for m in lines if m.get("role") == "user" and m.get("resumedFrom")]


def _model_saw(coder, marker: str, call_id: str) -> bool:
    """Was the model sent call ``call_id``'s own result (the step was never
    asked for again — it was SHOWN, in its conversation)?"""
    return any(
        m.get("role") == "tool" and m.get("tool_call_id") == call_id and "wrote" in str(m.get("content") or "")
        for msgs in coder.chats(marker) for m in msgs
    )


def test_an_update_stop_carries_a_coding_reply_on_by_itself(tmp_path):
    """Three boots on one home. Boot 1: reply A works and is stopped by the
    update's stop while it asks the model for its third step; reply B is
    parked on a card; reply C runs to the end (the reply that never stopped).
    Boot 2: A carries on by itself, B does not — and A is stopped AGAIN while
    it carries on. Boot 3: A carries on once more, ONCE, and finishes."""
    from tests.fixtures.fake_coding_model_v1391 import ScriptedCoder

    root = tmp_path / "root"
    root.mkdir()
    home = root / ".ironjarvis"
    log = tmp_path / "daemon.log"
    work_a, work_b, work_c = (_coding_work(tmp_path, n) for n in "abc")
    with ScriptedCoder(_CODING_FILES) as coder:
        first_hold = coder.hold("A", 2, resumed=False)
        second_hold = coder.hold("A", 3, resumed=True)

        first = Daemon(root, log)
        try:
            _use_the_coder(first, coder)
            a = _coding_session(first, work_a)
            b = _coding_session(first, work_b, approval_mode="always_ask")
            c = _coding_session(first, work_c)
            _ask(first, c, "[C] make three files", "c-1")
            _until(lambda: _reply_done(home, c, "c-1"), 60, "the reply that never stopped")
            _ask(first, b, "[B] make a file", "b-1")
            _until(lambda: any(r["thread_id"] == b and r["waiting"] for r in _running(first)), 60,
                   "the card that parks B")
            _ask(first, a, "[A] make three files", "a-1")
            assert first_hold.reached.wait(60), "A never asked for its third step"
            assert _written(home, a) == ["one.txt", "two.txt"]
            first.stop()
        finally:
            first_hold.release.set()
            first.close()

        second = Daemon(root, log)
        try:
            assert second_hold.reached.wait(90), "A was not carried on by itself after the restart"
            assert (work_a / "three.txt").read_text(encoding="utf-8") == "3"
            # B was waiting on the user: never carried on by itself.
            assert _carry_ons(_lines(home, b)) == []
            assert not (work_b / "waits.txt").exists()
            second.stop()
        finally:
            second_hold.release.set()
            second.close()

        third = Daemon(root, log)
        try:
            done = _until(lambda: _reply_done(home, a), 90, "A to finish")
            lines = _lines(home, a)
            # Carried on twice in all — once per stop, NEVER twice for one stop.
            carried = _carry_ons(lines)
            assert len(carried) == 2 and carried[0] == "a-1" and len(set(carried)) == 2, carried
            assert done.get("resumedFrom") == carried[1]
            # No finished step ran again: each file written once (the ledger) …
            assert _written(home, a) == ["one.txt", "three.txt", "two.txt"]
            # … and each was SHOWN to the model by its own call id afterwards.
            assert _model_saw(coder, "A", "A1") and _model_saw(coder, "A", "A2") and _model_saw(coder, "A", "A3")
            # The same files a reply that never stopped wrote.
            assert _files(work_a) == _files(work_c) == {"one.txt": "1", "two.txt": "2", "three.txt": "3"}
            # The first cut-off reply's line stays, saying it was cut off.
            cut = next(m for m in lines if m.get("role") == "assistant" and m.get("turnId") == "a-1")
            assert cut["interrupted"] is True
            assert _carry_ons(_lines(home, b)) == []
            third.stop()
        finally:
            third.close()


def test_a_crash_carries_a_coding_reply_on_and_keeps_its_timeline(tmp_path):
    """A hard kill while the reply asks the model for its third step: the
    restarted app rebuilds the cut-off reply's line WITH its steps and times
    (the crash wrote none) and carries the reply on from its last round."""
    from tests.fixtures.fake_coding_model_v1391 import ScriptedCoder

    root = tmp_path / "root"
    root.mkdir()
    home = root / ".ironjarvis"
    log = tmp_path / "daemon.log"
    work_a, work_c = _coding_work(tmp_path, "a"), _coding_work(tmp_path, "c")
    with ScriptedCoder(_CODING_FILES) as coder:
        hold = coder.hold("A", 2, resumed=False)
        first = Daemon(root, log)
        leftovers: list[psutil.Process] = []
        try:
            _use_the_coder(first, coder)
            a = _coding_session(first, work_a)
            c = _coding_session(first, work_c)
            _ask(first, c, "[C] make three files", "c-1")
            _until(lambda: _reply_done(home, c, "c-1"), 60, "the reply that never stopped")
            _ask(first, a, "[A] make three files", "a-1")
            assert hold.reached.wait(60), "A never asked for its third step"
            time.sleep(1.5)  # the turn's log is written to disk about once a second
            leftovers = first.crash()
        finally:
            hold.release.set()
            first.close()
        try:
            second = Daemon(root, log)
            try:
                done = _until(lambda: _reply_done(home, a), 90, "A to be carried on and finish")
                lines = _lines(home, a)
                assert _carry_ons(lines) == ["a-1"] and done.get("resumedFrom") == "a-1"
                assert _written(home, a) == ["one.txt", "three.txt", "two.txt"]
                assert _model_saw(coder, "A", "A1") and _model_saw(coder, "A", "A2")
                assert _files(work_a) == _files(work_c)
                # The crash wrote no reply line: the boot rebuilt it from the
                # stored frames, with what ran and when (the Timeline's open
                # item, v1.391.0).
                cut = next(m for m in lines if m.get("role") == "assistant" and m.get("turnId") == "a-1")
                assert cut["interrupted"] is True and isinstance(cut.get("timing"), dict)
                assert [s["name"] for s in cut.get("steps") or []] == ["write_file", "write_file"]
                assert all(isinstance(s.get("start"), int) for s in cut["steps"])
                second.stop()
            finally:
                second.close()
        finally:
            for p in leftovers:
                try:
                    p.kill()
                except psutil.Error:
                    pass
