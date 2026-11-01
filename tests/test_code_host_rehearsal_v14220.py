"""v1.422.0 — the UPDATE REHEARSAL's sibling for Build › Code: a REAL daemon
(a subprocess, a free port, a throwaway home) with a Code-view session on the
fake harness (``tests/fixtures/fake_harness_v1409.py`` behind a ``claude.cmd``
on PATH, replaying a RECORDED Claude Code session), HARD-KILLED
(``TerminateProcess`` — the desktop watchdog's kill, a crash) and started again
on the same home, twice:

1. after a reply that finished: the session's tool is the SAME process and the
   next message is answered by it (nothing is started again);
2. in the middle of a reply: the restarted daemon shows the rest of it and
   writes its end exactly once (the log holds the answer once);
3. then a QUIT (``POST /shutdown`` with no body, what the desktop sends):
   no host and no tool process is left.

Windows only, like the product.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from iron_jarvis import codehost as CH
from iron_jarvis.hosted import hostlink as HL
from tests._code_host_helpers_v14220 import FAKE, _text_turn, _turn_split, alive, config_home, write_scenario

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="the product's restart path is Windows"),
    pytest.mark.timeout(420),
]

REPO = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Daemon:
    def __init__(self, root: Path, log: Path, env: dict[str, str]) -> None:
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self._log = open(log, "ab")
        self.proc = subprocess.Popen(
            [sys.executable, "-c", "from iron_jarvis.daemon.cli import app; app()",
             "serve", "--host", "127.0.0.1", "--port", str(self.port), "--root", str(root)],
            cwd=str(REPO), env=env, stdout=self._log, stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            assert self.proc.poll() is None, f"daemon exited: {log.read_text(errors='replace')[-3000:]}"
            try:
                if httpx.get(self.base + "/health", timeout=2).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
        raise AssertionError(f"daemon never answered: {log.read_text(errors='replace')[-3000:]}")

    def kill(self) -> None:
        """A hard kill: TerminateProcess, no lifespan runs."""
        self.proc.kill()
        self.proc.wait(timeout=30)
        self._log.close()

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=30)
        if not self._log.closed:
            self._log.close()


def _frames(text: str) -> list[dict]:
    out = []
    for block in text.split("\n\n"):
        data = "".join(line[5:].strip() for line in block.split("\n") if line.startswith("data:"))
        if data:
            out.append(json.loads(data))
    return out


def _rows(log: Path) -> list[dict]:
    return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()] if log.exists() else []


def _log_events(home: Path, sid: str) -> list[dict]:
    path = home / "hosted" / sid / "events.jsonl"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()] if path.exists() else []


def _code_view_starts(log: Path) -> list[int]:
    """The pids of the Code view's own starts of the tool (the daemon also
    asks ``claude`` for its model list on its own — not a Code-view start)."""
    return [r["pid"] for r in _rows(log)
            if "argv" in r and ("--session-id" in r["argv"] or "--resume" in r["argv"])]


def test_a_real_daemon_killed_twice_carries_its_coding_session_on_and_a_quit_ends_it(tmp_path):
    root, work, bin_ = tmp_path / "root", tmp_path / "work", tmp_path / "bin"
    for d in (root, work, bin_):
        d.mkdir()
    home = config_home(root)
    fake_log = tmp_path / "fake.log"
    start, one, two = _turn_split()
    scenario = write_scenario(tmp_path / "scenario.json",
                              start + one + _text_turn(two, ["Hello ", {"sleep": 8.0}, "World"], "Hello World"))
    # `claude` on PATH is the fake harness (binary_for finds it like the real one).
    (bin_ / "claude.cmd").write_text(f'@"{sys.executable}" "{FAKE}" %*\r\n', encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "IRONJARVIS_TOKEN"}
    env.update({"PATH": str(bin_) + os.pathsep + env.get("PATH", ""), HL.ENV_SWITCH: "on",
                "IJ_FAKE_SCENARIO": str(scenario), "IJ_FAKE_LOG": str(fake_log)})
    dlog = tmp_path / "daemon.log"
    pids: set[int] = set()
    daemons: list[Daemon] = []
    try:
        d1 = Daemon(root, dlog, env)
        daemons.append(d1)
        r = httpx.post(d1.base + "/build/app/sessions", json={"harness": "claude-code", "folder": str(work)},
                       timeout=60)
        assert r.status_code == 201, r.text
        sid = r.json()["id"]
        first = _frames(httpx.post(d1.base + f"/build/app/sessions/{sid}/turn",
                                   json={"text": "one", "turn_id": "t1"}, timeout=120).text)
        assert first[-1]["type"] == "done" and first[-1]["stop"] == "completed", first
        tool = _code_view_starts(fake_log)
        assert len(tool) == 1
        rec = CH.read_record(home)
        pids.update([rec["pid"], tool[0]])
        time.sleep(1.5)  # the host has the session's newest state (kept ~1 s after its last line)

        # 1. hard kill after a finished reply, start again: the SAME process answers
        d1.kill()
        assert alive(tool[0]) and alive(rec["pid"]), "the tool and its host outlived the daemon"
        d2 = Daemon(root, dlog, env)
        daemons.append(d2)
        assert CH.read_record(home)["pid"] == rec["pid"], "the same host"
        follow = httpx.Client(timeout=120)
        import threading

        got: dict = {}

        def second_turn() -> None:
            try:
                got["text"] = follow.post(d2.base + f"/build/app/sessions/{sid}/turn",
                                          json={"text": "two", "turn_id": "t2"}).text
            except httpx.HTTPError as exc:  # the daemon is killed under it on purpose
                got["error"] = repr(exc)

        t = threading.Thread(target=second_turn, daemon=True)
        t.start()
        # 2. mid-reply: wait until the first half is in the log, kill again
        end = time.monotonic() + 60
        while time.monotonic() < end:
            if any(e.get("turn") == "t2" and e.get("delta") == "Hello " for e in _log_events(home, sid)):
                break
            time.sleep(0.1)
        else:
            raise AssertionError("the second reply never began")
        users = [r for r in _rows(fake_log) if "in" in r and r["in"].get("type") == "user"]
        assert len(users) == 2, "the second message reached the one process"
        assert _code_view_starts(fake_log) == tool, "nothing was started again"
        d2.kill()
        t.join(timeout=30)
        follow.close()
        d3 = Daemon(root, dlog, env)
        daemons.append(d3)
        rest = _frames(httpx.get(d3.base + f"/build/app/sessions/{sid}/turns/t2/stream", timeout=120).text)
        assert rest[-1]["type"] == "done" and rest[-1]["stop"] == "completed", rest
        assert "".join(e.get("delta", "") for e in rest if e["type"] == "text") == "Hello World"
        end = time.monotonic() + 10  # the log's writer writes a quarter second behind
        while time.monotonic() < end:
            logged = [e for e in _log_events(home, sid) if e.get("turn") == "t2"]
            if any(e["type"] == "done" for e in logged):
                break
            time.sleep(0.1)
        time.sleep(0.6)
        logged = [e for e in _log_events(home, sid) if e.get("turn") == "t2"]
        assert len([e for e in logged if e["type"] == "done"]) == 1, "its end is written once"
        assert "".join(e.get("delta", "") for e in logged if e["type"] == "text") == "Hello World", \
            "nothing shown twice"
        assert _code_view_starts(fake_log) == tool

        # 3. a QUIT ends it all
        assert httpx.post(d3.base + "/shutdown", timeout=30).json()["detail"] == "Iron Jarvis is shutting down"
        d3.proc.wait(timeout=90)
        end = time.monotonic() + 30
        while time.monotonic() < end and any(alive(p) for p in pids):
            time.sleep(0.2)
        assert not [p for p in pids if alive(p)], "a Quit leaves no host and no tool"
        assert CH.read_record(home) is None
    finally:
        for d in daemons:
            d.close()
        try:
            HL.quit_host(home, 30)
        except Exception:  # noqa: BLE001
            pass
        end = time.monotonic() + 20
        while time.monotonic() < end and any(alive(p) for p in pids):
            time.sleep(0.2)
        assert not [p for p in pids if alive(p)], f"left running: {[p for p in pids if alive(p)]}"
