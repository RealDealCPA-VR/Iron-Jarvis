"""v1.422.0 — helpers for the code host's tests (``iron_jarvis/codehost.py``).

Every test that turns the host on uses :func:`host_session` (a fixture's body): it
switches the host on for the test, and at teardown tells EVERY host the test
started to quit and then checks BY PID that no host, no launcher and no tool
the test started is still running (a process left behind fails the test —
the v1.383.1 rule). Scenarios are the RECORDED Claude Code session
``claude_resume`` (two messages), cut and re-joined here: a pause in the
middle of a reply, a permission card, a background helper.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import psutil

from iron_jarvis import codehost as CH
from iron_jarvis.hosted import hostlink as HL

FIX = Path(__file__).parent / "fixtures" / "build_app_v1409"
FAKE = Path(__file__).parent / "fixtures" / "fake_harness_v1409.py"


# --------------------------------------------------------------------------- #
# Scenarios (built from a recorded Claude Code session)
# --------------------------------------------------------------------------- #
def _resume_steps() -> list[dict[str, Any]]:
    return json.loads((FIX / "claude_resume.json").read_text(encoding="utf-8"))["steps"]


def _turn_split() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """(the start, turn one, turn two) of the recording."""
    steps = _resume_steps()
    users = [i for i, s in enumerate(steps) if s.get("await") == "user"]
    return steps[: users[0]], steps[users[0]: users[1]], steps[users[1]:]


def _text_turn(turn: list[dict[str, Any]], parts: list[Any], whole: str) -> list[dict[str, Any]]:
    """Turn one with its streamed text replaced by ``parts`` (strings, or
    ``{"sleep": s}`` pauses between them) and its whole answer by ``whole``."""
    out: list[dict[str, Any]] = []
    for step in turn:
        o = step.get("out") if isinstance(step.get("out"), dict) else None
        ev = (o or {}).get("event") if o and o.get("type") == "stream_event" else None
        if ev and ev.get("type") == "content_block_delta" and (ev.get("delta") or {}).get("type") == "text_delta":
            for p in parts:
                if isinstance(p, dict):
                    out.append(p)
                else:
                    s = json.loads(json.dumps(step))
                    s["out"]["event"]["delta"]["text"] = p
                    out.append(s)
            continue
        s = json.loads(json.dumps(step))
        if o and o.get("type") == "assistant":
            s["out"]["message"]["content"] = [{"type": "text", "text": whole}]
        if o and o.get("type") == "result":
            s["out"]["result"] = whole
        out.append(s)
    return out


def write_scenario(path: Path, steps: list[dict[str, Any]]) -> Path:
    path.write_text(json.dumps({"harness": "claude-code", "steps": steps}), encoding="utf-8")
    return path


def two_turns(tmp: Path) -> Path:
    return write_scenario(tmp / "two_turns.json", _resume_steps())


def mid_reply(tmp: Path, pause: float) -> Path:
    start, one, two = _turn_split()
    return write_scenario(tmp / "mid_reply.json",
                          start + _text_turn(one, ["Hello ", {"sleep": pause}, "World"], "Hello World") + two)


CARD_RID = "0c2b2706-fc2b-4df7-87e8-a567532802e3"


def with_card(tmp: Path) -> Path:
    """Turn one asks to run a command first (a recorded can_use_tool) and
    waits for the answer."""
    start, one, two = _turn_split()
    allow = json.loads((FIX / "claude_allow.json").read_text(encoding="utf-8"))["steps"]
    ask = next(s for s in allow if "can_use_tool" in json.dumps(s))
    card = [one[0], one[1], ask, {"await": "response", "request_id": CARD_RID}]
    return write_scenario(tmp / "card.json", start + card + _text_turn(one[1:], ["Ran it."], "Ran it.") + two)


def with_helper(tmp: Path) -> Path:
    """Turn one ends with a BACKGROUND helper agent still working."""
    start, one, two = _turn_split()
    sid = next(s["out"]["session_id"] for s in one if isinstance(s.get("out"), dict) and s["out"].get("session_id"))
    started = {"out": {"type": "system", "subtype": "task_started", "task_id": "bg-1", "task_type": "local_agent",
                       "description": "Checking the tests", "is_backgrounded": True, "session_id": sid}}
    return write_scenario(tmp / "helper.json", start + one[:-1] + [started, one[-1]] + two)


def use_fake(monkeypatch, scenario: Path, log: Path) -> None:
    from iron_jarvis.hosted import service as S

    monkeypatch.setattr(S, "binary_for", lambda harness: [sys.executable, str(FAKE)])
    monkeypatch.setenv("IJ_FAKE_SCENARIO", str(scenario))
    monkeypatch.setenv("IJ_FAKE_LOG", str(log))


def fake_rows(log: Path) -> list[dict[str, Any]]:
    if not log.exists():
        return []
    return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]


def fake_pids(log: Path) -> list[int]:
    return [r["pid"] for r in fake_rows(log) if "argv" in r]


# --------------------------------------------------------------------------- #
# The daemon in-process: a crash, a boot
# --------------------------------------------------------------------------- #
def crash(app: Any) -> None:
    """The daemon dies WITHOUT A WORD (the watchdog's kill, a crash): its
    link to the host drops and nothing of the old daemon reacts any more —
    what its log writer had queued is written (a real writer flushes every
    quarter second)."""
    platform = app.state.platform
    svc = getattr(platform, "hosted", None)
    if svc is not None:
        svc._closing = True
        if svc._reaper is not None:
            svc._reaper.cancel()
        for live in svc.live.values():
            live.log.flush()
    key = HL._key(platform.config.home)
    with HL._REG_LOCK:
        lk = HL._LINKS.pop(key, None)
    if lk is None:
        return
    with lk._lock:
        procs = list(lk._procs.values())
        lk._procs.clear()
    for p in procs:
        p._loop = None
        p.snapshot_source = None
        if p._snap_timer is not None:
            p._snap_timer.cancel()
            p._snap_timer = None
    lk.close()


async def boot(home: Path) -> Any:
    """A new daemon on the same home, through the lifespan's own steps for
    the Code view (asked of the host, reconciled with ``keep``, adopted)."""
    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.hosted import adopt as ADOPT
    from iron_jarvis.hosted import service as S

    app = create_app(str(home))
    platform = app.state.platform
    keep = await asyncio.to_thread(ADOPT.keep_ids, platform.config.home)
    await asyncio.to_thread(S.reconcile_cut_turns, platform.config.home, keep)
    if keep:
        await S.service_for(platform).adopt()
    return app


async def kept(app: Any, sid: str) -> None:
    """Wait until the host holds this session's newest state (a snapshot
    sent, then a ping answered — the host reads its connection in order)."""
    svc = app.state.platform.hosted
    proc = svc.live[sid].proc
    end = time.monotonic() + 15
    while proc._snap_timer is not None and time.monotonic() < end:
        await asyncio.sleep(0.05)
    lk = proc.link
    await asyncio.to_thread(lk.request, {"t": "ping"})


def events_of(home_cfg: Any, sid: str, app: Any = None) -> list[dict[str, Any]]:
    """The session's log as written (``app``: its queued lines written first)."""
    svc = getattr(app.state.platform, "hosted", None) if app is not None else None
    if svc is not None and sid in svc.live:
        svc.live[sid].log.flush()
    path = Path(home_cfg) / "hosted" / sid / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


# --------------------------------------------------------------------------- #
# The fixture: on for the test, nothing left behind
# --------------------------------------------------------------------------- #
class CodeHosts:
    def __init__(self, monkeypatch) -> None:
        self.monkeypatch = monkeypatch
        self.homes: list[Path] = []
        self.pids: set[int] = set()
        self.logs: list[Path] = []
        self._before = {p.pid for p in psutil.process_iter()}

    def home(self, path: Path) -> Path:
        """Register a state home whose hosts must be gone at teardown."""
        self.homes.append(Path(path))
        return Path(path)

    def note(self) -> None:
        for h in self.homes:
            rec = CH.read_record(h)
            if rec and rec.get("pid"):
                self.pids.add(int(rec["pid"]))
                try:
                    parent = psutil.Process(int(rec["pid"])).parent()
                    if parent is not None and parent.pid not in self._before and "python" in parent.name().lower():
                        self.pids.add(parent.pid)  # a venv trampoline in front of the host
                except psutil.Error:
                    pass
        for lg in self.logs:
            self.pids.update(fake_pids(lg))

    def teardown(self) -> list[int]:
        self.note()
        for h in self.homes:
            try:
                HL.quit_host(h, 30)
            except Exception:  # noqa: BLE001
                pass
        end = time.monotonic() + 20
        left = []
        while time.monotonic() < end:
            left = [p for p in self.pids if alive(p)]
            if not left:
                return []
            time.sleep(0.2)
        return left


def alive(pid: int) -> bool:
    """A process with this pid runs (a zombie, or one that just went, does not)."""
    try:
        p = psutil.Process(int(pid))
        return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
    except (psutil.Error, OSError, ValueError):
        return False


def host_session(monkeypatch):
    """The body of a test module's ``hosts`` fixture (``yield from`` it)."""
    monkeypatch.setenv(HL.ENV_SWITCH, "on")
    hosts = CodeHosts(monkeypatch)
    try:
        yield hosts
    finally:
        left = hosts.teardown()
        with HL._REG_LOCK:
            HL._LINKS.clear()
        assert not left, f"processes the test started are still running: {left}"


def config_home(root: Path) -> Path:
    """The state home ``create_app(root)`` uses (``<root>/.ironjarvis``)."""
    from iron_jarvis.core.config import resolve_home

    return Path(resolve_home(str(root)))


__all__ = ["CodeHosts", "boot", "host_session", "config_home", "crash", "events_of", "fake_pids", "fake_rows",
           "kept", "mid_reply", "two_turns", "use_fake", "with_card", "with_helper", "write_scenario", "os"]
