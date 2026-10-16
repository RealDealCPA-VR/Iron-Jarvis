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
* after a hard kill the data folder is not left locked and the terminal is
  still restored.

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

    second = Daemon(root, log)
    try:
        # Settings survived: the copy folder still gets the backup.
        _assert_backup(second, mirror)

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
