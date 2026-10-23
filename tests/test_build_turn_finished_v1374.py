"""v1.374.0 (IJ harness part 2, Phase 3): a long Build turn's FINISH NOTICE.

``build.turn_finished`` {thread_id, turn_id, title, seconds, outcome} is
published ONCE when a Build turn that ran longer than a minute ENDS (done or
failed), after its reply line is written — never for a shorter turn, a turn
the user stopped, a turn a shutdown cut off, or a turn that ended on its
account's limit card (that card is the same news). The one-notice rules: the
bell rings it (``comm/notifier.bell_pings`` agrees), "This PC" stays quiet for
it, a phone hears it only when the user subscribed the type, and the desktop
app says it from the tray (the one-notice fixture holds the words; see
tests/test_one_notice_v13600.py).

The clock is the build routes' own ``time`` (a stand-in the test advances):
a minute is never waited out for real.
"""

from __future__ import annotations

import asyncio
import json
import time as _real_time
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.comm.channels import DesktopChannel, MockChannel
from iron_jarvis.comm.notifier import Notifier, bell_attention, bell_pings, format_event
from iron_jarvis.core.events import EventType
from iron_jarvis.daemon import durable_turns as _durable
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as build_routes
from tests.test_build_sessions_v1367 import messages_of, new_session, open_turn, until, wire
from tests.test_build_turns_v1367 import Gated, GatedScript
from tests.test_chat_turn_stop_v1241 import _asgi_post
from tests.test_one_notice_v13600 import _NOTICES, _PLAN, _cases, _node, requires_node


class Clock:
    """The build routes' ``time``, ``offset`` seconds ahead of the real one."""

    def __init__(self) -> None:
        self.offset = 0.0

    def time(self) -> float:
        return _real_time.time() + self.offset

    def __getattr__(self, name):
        return getattr(_real_time, name)


@pytest.fixture()
def clock(monkeypatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(build_routes, "time", c)
    return c


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    return folder


def finished(app) -> list:
    return [e for e in app.state.platform.event_bus.history if e.type == EventType.BUILD_TURN_FINISHED]


async def _long_turn(app, sid: str, clock: Clock, turn_id: str, seconds: float) -> None:
    """A real build turn that 'takes' ``seconds``: the model holds on a gate
    while the clock moves on, then finishes."""
    gate = asyncio.Event()
    wire(app, GatedScript([Gated(gate, "working on it, ", "done.")]))
    drop = asyncio.Event()
    task = asyncio.create_task(open_turn(app, sid, {"text": "do the long thing", "turn_id": turn_id}, drop=drop))
    lg = await until(lambda: app.state.platform.turn_logs.get(turn_id) if getattr(app.state.platform, "turn_logs", None) else None)
    assert lg is not None
    clock.offset += seconds
    gate.set()
    await until(lambda: lg.status != "running")
    drop.set()
    await task


# --------------------------------------------------------------------------- #
# the event: past a minute, once, after the reply is written
# --------------------------------------------------------------------------- #


async def test_a_turn_longer_than_a_minute_is_announced_once_after_its_reply_is_written(app, work, clock):
    sid = new_session(app, work, title="Fix the nav bar")["id"]
    seen_reply: list[bool] = []

    def on_event(ev) -> None:
        if ev.type == EventType.BUILD_TURN_FINISHED:
            # The reply line is already on the session when the bell hears it.
            seen_reply.append(any(
                m["role"] == "assistant" and m.get("turnId") == "bt-long" for m in messages_of(app, sid)
            ))

    app.state.platform.event_bus.add_handler(on_event)
    await _long_turn(app, sid, clock, "bt-long", 75)
    [ev] = finished(app)
    assert ev.session_id == f"build:{sid}"
    assert ev.payload["thread_id"] == sid and ev.payload["turn_id"] == "bt-long"
    assert ev.payload["outcome"] == "done" and ev.payload["title"] == "Fix the nav bar"
    assert 75 <= ev.payload["seconds"] <= 80
    assert seen_reply == [True]
    # Following the finished turn again (a rejoin) announces nothing more.
    status, _ = await _asgi_get_stream(app, "bt-long")
    assert status == 200
    await asyncio.sleep(0.05)
    assert len(finished(app)) == 1
    # The bell rings it, counts it — and the one-notice rule agrees.
    as_dict = {"type": ev.type, "session_id": ev.session_id, "payload": ev.payload}
    assert bell_pings(as_dict) and bell_attention(as_dict)


async def _asgi_get_stream(app, turn_id: str):
    from tests.test_build_sessions_v1367 import asgi_get

    return await asgi_get(app, f"/chat/turns/{turn_id}/stream")


async def test_a_turn_under_a_minute_is_not_announced(app, work, clock):
    sid = new_session(app, work)["id"]
    await _long_turn(app, sid, clock, "bt-short", 55)
    assert finished(app) == []


async def test_a_turn_the_user_stopped_is_not_announced(app, work, clock):
    sid = new_session(app, work)["id"]
    gate = asyncio.Event()
    wire(app, GatedScript([Gated(gate, "half an ans", "wer.")]))
    drop = asyncio.Event()
    await open_turn(app, sid, {"text": "go", "turn_id": "bt-stop"}, drop=drop, stop_after="half an ans")
    clock.offset += 120
    assert (await _asgi_post(app, "/chat/turns/bt-stop/stop"))[0] == 200
    gate.set()
    lg = app.state.platform.turn_logs.get("bt-stop")
    await until(lambda: lg.status != "running")
    assert lg.status == "stopped"
    await asyncio.sleep(0.05)
    assert finished(app) == []


# --------------------------------------------------------------------------- #
# recorded_turn on its own: failed, cut off, an account's limit
# --------------------------------------------------------------------------- #


async def _frames(clock: Clock, seconds: float, last: tuple[str, dict]):
    yield _durable.sse("token", {"text": "partly "})
    clock.offset += seconds
    yield _durable.sse(*last)


async def _drive(gen) -> None:
    async for _ in gen:
        pass


async def test_a_failed_long_turn_is_announced_as_failed(app, work, clock):
    sid = new_session(app, work, title="Move the tests")["id"]
    platform = app.state.platform
    gen = build_routes.recorded_turn(
        platform.engine, sid, "bt-err", _frames(clock, 90, ("error", {"detail": "The model failed."})),
        platform=platform,
    )
    await _drive(gen)
    [ev] = finished(app)
    assert ev.payload["outcome"] == "failed" and ev.payload["title"] == "Move the tests"


async def test_a_long_turn_cut_off_by_a_shutdown_is_not_announced(app, work, clock):
    sid = new_session(app, work)["id"]
    platform = app.state.platform
    hold = asyncio.Event()

    async def frames():
        yield _durable.sse("token", {"text": "partly "})
        clock.offset += 200
        await hold.wait()

    task = asyncio.create_task(_drive(build_routes.recorded_turn(platform.engine, sid, "bt-cut", frames(), platform=platform)))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.05)
    assert finished(app) == []


async def test_a_long_turn_that_ended_on_its_accounts_limit_is_said_by_that_card_alone(app, work, clock, monkeypatch):
    sid = new_session(app, work)["id"]
    platform = app.state.platform

    async def no_news(*a, **kw):
        return None

    monkeypatch.setattr(build_routes, "_announce_account_limit", no_news)
    pin = SimpleNamespace(
        limit={"account": "acc1", "account_name": "Work", "provider": "claude-cli", "kind": "usage"},
        leased=None, profile_id="acc1", title="Work", thinking_dropped=False,
    )
    gen = build_routes.recorded_turn(
        platform.engine, sid, "bt-limit", _frames(clock, 300, ("error", {"detail": "limit"})),
        pin=pin, platform=platform,
    )
    await _drive(gen)
    assert finished(app) == []


# --------------------------------------------------------------------------- #
# the one-notice rules: This PC quiet, the phone only when subscribed
# --------------------------------------------------------------------------- #


def _event() -> dict:
    return {
        "type": EventType.BUILD_TURN_FINISHED,
        "session_id": "build:bs_long",
        "payload": {"thread_id": "bs_long", "turn_id": "bt1", "title": "Fix the nav bar", "seconds": 192,
                    "outcome": "done"},
    }


def test_this_pc_never_repeats_the_bell_and_a_subscribed_phone_hears_it_in_the_bells_words(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(DesktopChannel, "sink", lambda title, message: sent.append(message))
    # Not a default alert: nothing anywhere from the notifier.
    n = Notifier()
    phone = MockChannel()
    n.add_channel("phone", phone)
    n.add_channel("this-pc", DesktopChannel({"type": "desktop"}))
    n.on_event(_event())
    assert phone.sent == [] and sent == []
    # Subscribed by the user: the phone hears it; "This PC" still never does.
    n2 = Notifier(event_types={EventType.BUILD_TURN_FINISHED})
    phone2 = MockChannel()
    n2.add_channel("phone", phone2)
    n2.add_channel("this-pc", DesktopChannel({"type": "desktop"}))
    n2.on_event(_event())
    assert sent == []
    assert phone2.sent == ["Your Build session finished — Fix the nav bar · took 3 min 12 s"]
    failed = dict(_event(), payload={**_event()["payload"], "outcome": "failed", "seconds": 120})
    assert format_event(failed) == "Your Build session could not finish — Fix the nav bar · took 2 min"


@requires_node
def test_in_the_tray_the_desktop_app_says_it_once_and_never_after_the_bell(tmp_path):
    """desktop/main.js (the window closed to the tray): the finished turn is
    ONE notice in the bell's words, opening Build on that session; a second
    read says nothing; with a window open the bell owns it."""
    case = next(c for c in _cases() if c["case"] == "build-turn-finished")
    entries = [case["event"]]
    res = _node(_PLAN, tmp_path, {"steps": [{"entries": entries}, {"entries": entries}]})
    assert res["out"] == [["Your Build session finished"], []]
    res = _node(_PLAN, tmp_path, {"steps": [{"entries": entries, "windowOpen": True}, {"entries": entries}]})
    assert res["out"] == [[], []]
    notice = _node(_NOTICES, tmp_path, {"cases": [case]})["out"][0]
    assert notice["href"] == "/terminals?session=bs_long" and notice["body"] == "Fix the nav bar · took 3 min 12 s"


def test_the_event_is_typed_and_its_words_match_the_fixture():
    path = Path(__file__).resolve().parents[1] / "dashboard" / "__tests__" / "fixtures" / "one-notice-v13600.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    cases = {c["case"]: c for c in fixture["cases"]}
    ev = cases["build-turn-finished"]["event"]
    assert ev["type"] == EventType.BUILD_TURN_FINISHED == "build.turn_finished"
    assert set(ev["payload"]) == {"thread_id", "turn_id", "title", "seconds", "outcome"}
    assert build_routes.FINISH_NOTICE_AFTER_S == 60.0
