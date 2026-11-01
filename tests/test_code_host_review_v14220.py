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
3. A connection to the host that BROKE while the host lived (a socket error,
   a frame that could not be read — not a restart) told every session "the
   tool stopped" while each tool kept running in the host; the next message
   reconnected (the host's orphan rules then never run: a daemon is
   attached) and started a SECOND process for the same conversation.
"""

from __future__ import annotations

import asyncio
import socket
import sys
import time
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
    two_turns,
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


# --------------------------------------------------------------------------- #
# 3. a connection that broke while the host lived
# --------------------------------------------------------------------------- #
async def test_a_broken_link_never_leaves_a_second_process_on_one_conversation(hosts, tmp_path, monkeypatch):
    root = tmp_path / "root"
    log = tmp_path / "fake.log"
    hosts.home(config_home(root))
    hosts.logs.append(log)
    use_fake(monkeypatch, two_turns(tmp_path), log)
    work = tmp_path / "work"
    work.mkdir()
    app = create_app(str(root))
    async with client(app) as c:
        sid = (await new_session(c, work))["id"]
        one = frames((await asyncio.wait_for(
            c.post(f"/build/app/sessions/{sid}/turn", json={"text": "one", "turn_id": "t1"}), 60)).text)
        assert one[-1]["stop"] == "completed"
        svc = app.state.platform.hosted
        proc = svc.live[sid].proc
        old = proc.pid
        host = CH.read_record(config_home(root))["pid"]
        hosts.note()
        proc.link.sock.shutdown(socket.SHUT_RDWR)  # the connection breaks; the host lives on
        await until(lambda: not svc.live[sid].running, timeout=20)
        assert psutil.pid_exists(host), "the host itself is fine"
        await until(lambda: not psutil.pid_exists(old), timeout=30)  # the tool the session was told stopped, stopped
        two = frames((await asyncio.wait_for(
            c.post(f"/build/app/sessions/{sid}/turn", json={"text": "two", "turn_id": "t2"}), 60)).text)
    assert two[-1]["type"] == "done", two
    starts = [r for r in fake_rows(log) if "argv" in r]
    assert len(starts) == 2 and "--resume" in starts[1]["argv"], "the next message resumes it"
    assert [s_["pid"] for s_ in starts if psutil.pid_exists(s_["pid"])] == [starts[1]["pid"]],         "one process for the conversation"
    await ADOPT.shutdown(app.state.platform, keep=False)


def test_a_deliberate_close_is_never_taken_for_a_broken_link(hosts, tmp_path):
    """A quit, a restart's detach and a plain close are on purpose: nothing is
    stopped later for them (a restart's tools must keep running)."""
    home = hosts.home(tmp_path / "home")
    tool_py = tmp_path / "tool.py"
    tool_py.write_text("import sys\nfor line in sys.stdin:\n    pass\n", encoding="utf-8")
    import os

    for how in ("detach", "close"):
        proc = HL.RemoteProcess([sys.executable, str(tool_py)], env=dict(os.environ), cwd=str(tmp_path), name="t",
                                home=home, sid=f"s-{how}")
        proc.start(asyncio.new_event_loop(), lambda o: None, lambda c: None)
        hosts.note()
        hosts.pids.add(proc.pid)
        lk = proc.link
        seen: dict = {}
        real_send = lk.send

        def send(header, payload=b"", _real=real_send):
            # The host answers a detach by closing its end at once: the link
            # must already know the close is on purpose when it says detach.
            seen.setdefault(header.get("t"), lk.deliberate)
            return _real(header, payload)

        lk.send = send
        if how == "detach":
            HL.detach_host(home)
            assert seen.get("detach") is True, "deliberate BEFORE the host can close its end"
        else:
            lk.close()
        assert not lk.alive and not HL._LOST.get(HL._key(home)), f"a {how} is not a broken link"
        assert psutil.pid_exists(proc.pid), f"a {how} leaves the tool running"
    # A quit, likewise, before the host can go.
    lk = HL.link_for(home, start=False)
    seen: dict = {}
    real_request = lk.request

    def request(header, payload=b"", timeout=HL.REPLY_TIMEOUT_S, _real=real_request):
        seen.setdefault(header.get("t"), lk.deliberate)
        return _real(header, payload, timeout)

    lk.request = request
    assert HL.quit_host(home, 30)
    assert seen.get("quit") is True and not HL._LOST.get(HL._key(home))


# --------------------------------------------------------------------------- #
# Checked fine: the log's own line stamps never reach a window
# --------------------------------------------------------------------------- #
async def test_the_line_stamps_never_reach_any_build_code_view(hosts, tmp_path, monkeypatch):
    """``htool`` / ``hseq`` are the log's: the session view, a page of
    history, a step's kept texts, and a reply's stream — live, and followed
    again after a restart — never carry them."""
    root, home, log, sid, pid = await _cut_mid_reply(hosts, tmp_path, monkeypatch)
    stamped = [e for e in events_of(home, sid) if "hseq" in e]
    assert stamped and all(e.get("htool") for e in stamped), "the log IS stamped (else this guard is empty)"
    app2 = await boot(root)
    async with client(app2) as c:
        rest = (await asyncio.wait_for(c.get(f"/build/app/sessions/{sid}/turns/t1/stream"), 60)).text
        view = (await c.get(f"/build/app/sessions/{sid}")).text
        page = (await c.get(f"/build/app/sessions/{sid}/events", params={"limit": 50})).text
    for name, body in (("the stream", rest), ("the view", view), ("a page", page)):
        assert "hseq" not in body and "htool" not in body, name
    await ADOPT.shutdown(app2.state.platform, keep=False)


# --------------------------------------------------------------------------- #
# The builder's guarantees that had no pin
# --------------------------------------------------------------------------- #
def test_a_tool_in_the_host_is_started_with_breakaway_refused(tmp_path, monkeypatch):
    """v1.386.0's rule holds in the host too: Git Bash starts every program
    it runs with CREATE_BREAKAWAY_FROM_JOB whenever the Job allows it, and a
    dev server it started would then outlive the session."""
    from iron_jarvis.core import procs

    seen: dict = {}

    def spawn(argv, **kw):
        seen.update(kw)
        raise OSError("not started in this test")

    monkeypatch.setattr(procs, "spawn", spawn)
    host = CH.Host(tmp_path)
    tool = CH.Tool(host, "t1", "s1", [sys.executable, "-c", "pass"], {}, str(tmp_path), False)
    with pytest.raises(OSError):
        tool.start()
    assert seen.get("breakaway_ok") is False


def test_the_host_listens_on_loopback_only_and_keeps_no_credential_in_its_own_environment(hosts, tmp_path,
                                                                                          monkeypatch):
    monkeypatch.setenv("IRONJARVIS_TOKEN", "the-daemons-bearer-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    monkeypatch.setenv("IJ_REVIEW_PLAIN", "kept")
    home = hosts.home(tmp_path / "home")
    assert HL.link_for(home, start=True) is not None
    hosts.note()
    host = psutil.Process(int(CH.read_record(home)["pid"]))
    listening = [c for c in host.net_connections(kind="inet") if c.status == psutil.CONN_LISTEN]
    assert listening and all(c.laddr.ip == "127.0.0.1" for c in listening), listening
    env = host.environ()
    assert env.get("IJ_REVIEW_PLAIN") == "kept", "the host runs in the daemon's environment"
    assert "IRONJARVIS_TOKEN" not in env and "ANTHROPIC_API_KEY" not in env, "but never with its credentials"
    said = CH.log_path(home).read_text(encoding="utf-8")
    assert CH.read_record(home)["token"] not in said, "the host never logs its token"


async def test_a_card_raised_by_a_line_read_again_is_the_same_card_end_to_end(hosts, tmp_path, monkeypatch):
    """The state was last kept BEFORE the card's line (it came, was shown,
    and Iron Jarvis died before keeping the state again): after the restart
    the line is read again and must raise the card the window already shows
    — same id — answerable by that id, the answer reaching the tool once.
    (The id's derivation was pinned only as a unit; an end-to-end run with
    random ids stayed green.)"""
    from tests._code_host_helpers_v14220 import CARD_RID, with_card

    root = tmp_path / "root"
    log = tmp_path / "fake.log"
    hosts.home(config_home(root))
    hosts.logs.append(log)
    use_fake(monkeypatch, with_card(tmp_path), log)
    work = tmp_path / "work"
    work.mkdir()
    app = create_app(str(root))
    c = client(app)
    sid = (await new_session(c, work))["id"]
    monkeypatch.setattr(HL, "SNAP_AFTER_LINE_S", 3600.0)  # the state is kept with the message only
    follow = asyncio.ensure_future(c.post(f"/build/app/sessions/{sid}/turn", json={"text": "go", "turn_id": "t1"}))
    svc = app.state.platform.hosted
    await until(lambda: svc.live.get(sid) and svc.live[sid].asks)
    ask_id = next(iter(svc.live[sid].asks))
    home = config_home(root)
    await until(lambda: any(e.get("request_id") == ask_id for e in events_of(home, sid, app)))
    crash(app)
    hosts.note()
    monkeypatch.setattr(HL, "SNAP_AFTER_LINE_S", 1.0)
    follow.cancel()
    await c.aclose()
    app2 = await boot(root)
    svc2 = app2.state.platform.hosted
    await until(lambda: svc2.live[sid].proc is not None and svc2.live[sid].proc.replay is None)  # lines read again
    assert list(svc2.live[sid].asks) == [ask_id], "the card the window shows, by its own id"
    async with client(app2) as c2:
        r = await c2.post(f"/build/app/sessions/{sid}/answer", json={"request_id": ask_id, "decision": "allow"})
        assert r.status_code == 200, r.text
        rest = frames((await asyncio.wait_for(c2.get(f"/build/app/sessions/{sid}/turns/t1/stream"), 60)).text)
    assert rest[-1]["type"] == "done" and rest[-1]["stop"] == "completed", rest
    answers = [r_["in"] for r_ in fake_rows(log) if "in" in r_ and r_["in"].get("type") == "control_response"
               and (r_["in"].get("response") or {}).get("request_id") == CARD_RID]
    assert len(answers) == 1, "the answer reached it once"
    drawn = [e for e in events_of(home, sid, app2) if e["type"] == "approval"]
    assert [e.get("request_id") for e in drawn] == [ask_id], "drawn once"
    await ADOPT.shutdown(app2.state.platform, keep=False)


def test_a_start_the_host_answered_too_late_never_leaves_a_second_process(hosts, tmp_path):
    """The host did not answer a start in time (a process creation that
    hung): the tool starts as a plain child instead — and the host, which
    may still start it, is told to stop it, so the conversation never has a
    second process the host runs on unknown to Iron Jarvis."""
    import os

    home = hosts.home(tmp_path / "home")
    tool_py = tmp_path / "tool.py"
    tool_py.write_text("import sys\nfor line in sys.stdin:\n    pass\n", encoding="utf-8")
    lk = HL.link_for(home, start=True)
    hosts.note()
    real = lk.request
    late: dict = {}

    def request(header, payload=b"", timeout=HL.REPLY_TIMEOUT_S):
        got = real(header, payload, timeout)
        if header.get("t") == "start":
            late["pid"] = got[0].get("pid")  # it DID start in the host, but answered "too late"
            raise TimeoutError("the code host did not answer 'start'")
        return got

    lk.request = request
    proc = HL.RemoteProcess([sys.executable, str(tool_py)], env=dict(os.environ), cwd=str(tmp_path), name="t",
                            home=home, sid="s1")
    proc.start(asyncio.new_event_loop(), lambda o: None, lambda c: None)
    try:
        assert proc.local is not None and proc.alive, "the tool runs as a plain child instead"
        assert late.get("pid"), "the host started its copy"
        hosts.pids.add(late["pid"])
        end = time.monotonic() + 20
        while time.monotonic() < end and psutil.pid_exists(late["pid"]):
            time.sleep(0.1)
        assert not psutil.pid_exists(late["pid"]), "the host's late copy is stopped: one process"
    finally:
        proc.stop(graceful=False)
