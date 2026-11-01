"""v1.422.0 review — the code host's defects an independent review found, each
pinned red first (``iron_jarvis/codehost.py``, ``hosted/adopt.py``).

1. A session the code host kept that could NOT be taken back at boot — its
   adoption raised, the host went away between "what do you keep" and the
   adoption, or the adoption ran out of time — was left with its reply OPEN
   in the log (the boot reconcile skips every kept session) and, on a
   timeout, with its tool still running in the host under an attached Iron
   Jarvis that did not know it (the host's orphan rules never run while a
   daemon is attached): the next message started a SECOND process for the
   same conversation. Now every kept session that is not carried on has its
   tool stopped gracefully and its reply closed in words, once.
2. An unauthenticated connection could make the host allocate any amount of
   memory with its first frame (the payload size was read before the token
   was checked).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import psutil
import pytest

from iron_jarvis import codehost as CH
from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import adopt as ADOPT
from iron_jarvis.hosted import hostlink as HL
from tests._build_app_helpers_v1409 import client, frames, new_session, until
from tests._code_host_helpers_v14220 import (
    boot,
    config_home,
    crash,
    events_of,
    fake_rows,
    host_session,
    kept,
    mid_reply,
    use_fake,
)


@pytest.fixture
def hosts(monkeypatch):
    yield from host_session(monkeypatch)


pytestmark = [pytest.mark.skipif(sys.platform != "win32", reason="the product's restart path is Windows"),
              pytest.mark.timeout(240)]


async def _cut_mid_reply(hosts, tmp_path: Path, monkeypatch):
    """A reply being written when Iron Jarvis dies: (root, home, log, sid, tool pid)."""
    root = tmp_path / "root"
    log = tmp_path / "fake.log"
    hosts.home(config_home(root))
    hosts.logs.append(log)
    use_fake(monkeypatch, mid_reply(tmp_path, 6.0), log)
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    app = create_app(str(root))
    c = client(app)
    sid = (await new_session(c, work))["id"]
    follow = asyncio.ensure_future(c.post(f"/build/app/sessions/{sid}/turn", json={"text": "go", "turn_id": "t1"}))
    svc = app.state.platform.hosted
    await until(lambda: svc.turn("t1") and any(e.get("delta") == "Hello " for e in svc.turn("t1").events))
    await kept(app, sid)
    pid = svc.live[sid].proc.pid
    crash(app)
    hosts.note()
    follow.cancel()
    await c.aclose()
    return root, config_home(root), log, sid, pid


def _closed_once(home: Path, sid: str, app) -> list[dict]:
    logged = [e for e in events_of(home, sid, app) if e.get("turn") == "t1"]
    dones = [e for e in logged if e["type"] == "done"]
    assert len(dones) == 1 and dones[0]["stop"] == "interrupted", \
        f"the reply that could not be carried on is closed, once: {logged}"
    errs = [e for e in logged if e["type"] == "error"]
    assert errs and errs[-1]["title"] == ADOPT.GAVE_UP_TITLE, errs
    return logged


async def _next_message_resumes(app, sid: str, log: Path) -> None:
    async with client(app) as c:
        nxt = frames((await asyncio.wait_for(
            c.post(f"/build/app/sessions/{sid}/turn", json={"text": "two", "turn_id": "t2"}), 60)).text)
    assert nxt[-1]["type"] == "done", nxt
    starts = [r for r in fake_rows(log) if "argv" in r]
    assert len(starts) == 2 and "--resume" in starts[1]["argv"], "the next message resumes with the CLI's own resume"


# --------------------------------------------------------------------------- #
# 1. a kept session that could not be taken back
# --------------------------------------------------------------------------- #
async def test_a_kept_session_whose_adoption_raises_is_closed_in_words_not_left_open(hosts, tmp_path,
                                                                                     monkeypatch):
    root, home, log, sid, pid = await _cut_mid_reply(hosts, tmp_path, monkeypatch)
    from iron_jarvis.hosted import claude as CLAUDE

    def broken(self, kept):
        raise RuntimeError("a kept state this driver cannot read")

    monkeypatch.setattr(CLAUDE.ClaudeDriver, "restore", broken)
    app2 = await boot(root)
    monkeypatch.undo()  # the next start is an ordinary one
    monkeypatch.setenv(HL.ENV_SWITCH, "on")
    use_fake(monkeypatch, mid_reply(tmp_path, 0.0), log)
    svc2 = app2.state.platform.hosted
    await until(lambda: not psutil.pid_exists(pid), timeout=20)
    _closed_once(home, sid, app2)
    assert svc2.turn("t1") is None or not svc2.turn("t1").running
    await _next_message_resumes(app2, sid, log)
    await ADOPT.shutdown(app2.state.platform, keep=False)


async def test_a_kept_session_whose_host_went_away_before_the_adoption_is_closed(hosts, tmp_path, monkeypatch):
    root, home, log, sid, pid = await _cut_mid_reply(hosts, tmp_path, monkeypatch)
    real = HL.live_session_ids

    def then_the_host_dies(h):
        out = real(h)
        rec = CH.read_record(h)
        psutil.Process(int(rec["pid"])).kill()  # the host crashed right after it answered
        psutil.Process(int(rec["pid"])).wait(10)
        return out

    monkeypatch.setattr(HL, "live_session_ids", then_the_host_dies)
    app2 = await boot(root)
    await until(lambda: not psutil.pid_exists(pid), timeout=20)
    _closed_once(home, sid, app2)
    await ADOPT.shutdown(app2.state.platform, keep=False)


async def test_a_kept_session_the_adoption_ran_out_of_time_for_is_stopped_not_left_running(hosts, tmp_path,
                                                                                           monkeypatch):
    root, home, log, sid, pid = await _cut_mid_reply(hosts, tmp_path, monkeypatch)
    real = ADOPT._adopt_one

    async def slow(svc, link, t):
        await asyncio.sleep(30)
        return await real(svc, link, t)

    monkeypatch.setattr(ADOPT, "_adopt_one", slow)
    monkeypatch.setattr(ADOPT, "BOOT_ADOPT_WAIT_S", 1.0, raising=False)
    app2 = await boot(root)
    monkeypatch.setattr(ADOPT, "_adopt_one", real)
    await until(lambda: not psutil.pid_exists(pid), timeout=20)  # never left running unknown to Iron Jarvis
    _closed_once(home, sid, app2)
    await _next_message_resumes(app2, sid, log)
    starts = [r for r in fake_rows(log) if "argv" in r]
    alive = [s["pid"] for s in starts if psutil.pid_exists(s["pid"])]
    assert len(alive) <= 1, f"one process per conversation: {alive}"
    await ADOPT.shutdown(app2.state.platform, keep=False)


def test_the_lifespan_takes_kept_sessions_back_through_the_one_boot_step():
    src = (Path(__file__).parents[1] / "src" / "iron_jarvis" / "daemon" / "app.py").read_text(encoding="utf-8")
    assert "_hosted_adopt.at_boot(platform, _hosted_kept)" in src
    helpers = (Path(__file__).parent / "_code_host_helpers_v14220.py").read_text(encoding="utf-8")
    assert "ADOPT.at_boot(platform, keep)" in helpers, "the tests' boot is the lifespan's"


# --------------------------------------------------------------------------- #
# 2. an unauthenticated first frame is bounded
# --------------------------------------------------------------------------- #
class _Recording:
    """A connection's reader that records how much it was asked to read (a
    real ``rfile.read(n)`` allocates ``n`` bytes before it reads one)."""

    def __init__(self, header: bytes) -> None:
        self.header = header
        self.asked: list[int] = []

    def readline(self, limit: int = -1) -> bytes:
        return self.header

    def read(self, n: int) -> bytes:
        self.asked.append(n)
        return b"x" * min(n, 64)


def test_a_first_frame_cannot_make_the_host_read_a_huge_payload():
    rfile = _Recording(b'{"t": "hello", "token": "x", "n": 8000000000}\n')
    assert CH.read_frame(rfile, max_payload=CH.MAX_HELLO_PAYLOAD) is None, "a hello naming a payload is bad"
    assert rfile.asked == [], "nothing of it is read (nor allocated)"
    small = _Recording(b'{"t": "hello", "token": "x", "n": 10}\n')
    assert CH.read_frame(small, max_payload=CH.MAX_HELLO_PAYLOAD) is not None and small.asked == [10]
    # A trusted connection's frames are not bounded by it.
    big = _Recording(b'{"t": "write", "n": 100000}\n')
    assert CH.read_frame(big) is None and big.asked == [100000]
    src = (Path(__file__).parents[1] / "src" / "iron_jarvis" / "codehost.py").read_text(encoding="utf-8")
    serve = src[src.index("def serve_conn("):src.index("def handle(")]
    assert "read_frame(rfile, max_payload=MAX_HELLO_PAYLOAD)" in serve, "the hello is read with the bound"
