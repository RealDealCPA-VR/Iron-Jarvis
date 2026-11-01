"""v1.420.0 — three small truths the readiness re-audit left open.

* C6.9: ``/health``'s ``replies`` gains ``waiting`` — how many running replies
  (chat, Chat › Code, Build › Code) are parked on the user — so the orb can
  say "Waiting for you" instead of "Working" (additive; counts only).
* C6.8: Grok has the one Sign in press — its daemon words name the press
  (``cli_auth.SIGN_IN_FIX``) and never "run `grok login`"; a sign-in refusal
  reads as signed out; its /health row carries installed / signed_in.
* C6.10: a chat reply longer than a minute that ended is announced
  (``chat.turn_finished``) from the daemon-owned turn registry, for the tray.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.ids import utcnow
from iron_jarvis.core.models import ChatThreadRecord
from iron_jarvis.daemon import durable_turns as DT
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import system as SYS
from iron_jarvis.providers import cli_detect
from iron_jarvis.providers.cli_auth import SIGN_IN_FIX, is_sign_in_refusal
from iron_jarvis.providers.failure_words import friendly_error


# --------------------------------------------------------------------------- #
# C6.9 — /health.replies.waiting
# --------------------------------------------------------------------------- #
class _Log:
    def __init__(self, thread: str, surface: str = "") -> None:
        self.turn_id = f"t-{thread}"
        self.thread_id = thread
        self.surface = surface
        self.started_at = utcnow()

    def open_asks(self, pending):
        return []


class _Logs:
    def __init__(self, logs):
        self._l = logs

    def running(self):
        return list(self._l)


def test_health_counts_the_replies_that_wait_on_the_user(tmp_path, monkeypatch):
    from iron_jarvis.core import turns as T

    app = create_app(str(tmp_path))
    p = app.state.platform
    p.turn_logs = _Logs([_Log("chat_a"), _Log("chat_b"), _Log("code_c", "build")])
    monkeypatch.setattr(
        T.TURNS, "thread_states",
        lambda: {"chat_a": {"waiting": True}, "code_c": {"waiting": True}},
    )
    # Build › Code: 3 running, 1 of them still working → 2 parked.
    monkeypatch.setattr(SYS, "_code_counts", lambda d: {"running": 3, "working": 1, "tools": []})
    with TestClient(app) as c:
        replies = c.get("/health").json()["replies"]
    assert replies == {"chat": 2, "build": 1, "build_app": 3, "waiting": 4}, replies


def test_health_waiting_is_zero_when_nothing_waits(tmp_path):
    app = create_app(str(tmp_path))
    with TestClient(app) as c:
        replies = c.get("/health").json()["replies"]
    assert replies["waiting"] == 0
    assert set(replies) == {"chat", "build", "build_app", "waiting"}


# --------------------------------------------------------------------------- #
# C6.8 — Grok's sign-in words
# --------------------------------------------------------------------------- #
def test_grok_words_name_the_press_never_a_terminal_recipe():
    fix = SIGN_IN_FIX["grok"]
    assert fix == "Grok CLI isn't signed in. Press Sign in beside Grok CLI in Settings › Connections."
    assert "grok login" not in fix
    assert is_sign_in_refusal(f"grok-cli: {fix}")
    src = (cli_detect.__file__, )
    import iron_jarvis.providers.adapters.grok_cli as G

    for path in (*src, G.__file__):
        text = open(path, encoding="utf-8").read()
        assert "run `grok login`" not in text, path


def test_a_grok_sign_in_refusal_reads_as_signed_out_with_the_press():
    exc = RuntimeError(f"grok-cli: {SIGN_IN_FIX['grok']}")
    words = friendly_error(exc, provider="grok-cli")
    assert words["title"] == "Grok CLI isn't signed in"
    assert "Press Sign in beside Grok CLI" in words["fix"]


def test_a_grok_401_is_about_the_sign_in_not_a_key():
    from iron_jarvis.providers.adapters.base import ProviderError

    exc = ProviderError("grok-cli: refused", status_code=401)
    words = friendly_error(exc, provider="grok-cli")
    assert words["title"] == "Grok CLI refused your sign-in"


def test_the_grok_row_says_installed_and_signed_in(tmp_path, monkeypatch):
    cli_detect._grok_status_memo.clear()
    monkeypatch.setattr(cli_detect, "_grok_binary", lambda: "C:/fake/grok.exe")
    monkeypatch.setattr(cli_detect, "grok_session", lambda: None)
    out = cli_detect.grok_login_status()
    assert out == {"installed": True, "signed_in": False, "sign_in_fix": SIGN_IN_FIX["grok"]}
    cli_detect._grok_status_memo.clear()
    monkeypatch.setattr(cli_detect, "_grok_binary", lambda: None)
    assert cli_detect.grok_login_status()["installed"] is False
    cli_detect._grok_status_memo.clear()

    rows = SYS._with_grok_sign_in([{"provider": "grok-cli", "available": False}, {"provider": "openai"}])
    assert rows[0]["installed"] is False and rows[0]["sign_in_fix"] == SIGN_IN_FIX["grok"]
    assert "installed" not in rows[1]
    cli_detect._grok_status_memo.clear()


# --------------------------------------------------------------------------- #
# C6.10 — chat.turn_finished
# --------------------------------------------------------------------------- #
class _Bus:
    def __init__(self):
        self.events: list = []

    async def publish(self, etype, payload, **kw):
        self.events.append((etype, payload, kw))


@pytest.fixture
def engine(tmp_path):
    app = create_app(str(tmp_path))
    eng = app.state.platform.engine
    with session_scope(eng) as db:
        db.add(ChatThreadRecord(id="chat_q3", title="Q3 numbers"))
        db.commit()
    return eng


async def _run_turn(engine, bus, *, last="done", age_s=180.0, surface="", thread="chat_q3",
                    bind=True):
    if bind:
        DT.bind_event_bus(engine, bus)
    logs = DT.TurnLogs(engine)
    gate = asyncio.Event()

    async def frames():
        await gate.wait()
        yield DT.sse("token", {"text": "hi"})
        if last:
            yield DT.sse(last, {"reply": "hi"} if last == "done" else {"detail": "x"})

    lg = logs.start("turn_q3", thread, frames())
    lg.surface = surface
    lg.started_at = utcnow() - timedelta(seconds=age_s)
    gate.set()
    await lg.task
    return lg


async def test_a_long_chat_reply_that_ended_is_announced(engine):
    bus = _Bus()
    await _run_turn(engine, bus)
    assert len(bus.events) == 1, bus.events
    etype, payload, _kw = bus.events[0]
    assert etype == EventType.CHAT_TURN_FINISHED == "chat.turn_finished"
    assert payload["thread_id"] == "chat_q3" and payload["title"] == "Q3 numbers"
    assert payload["outcome"] == "done" and payload["seconds"] >= 180


async def test_a_failed_long_chat_reply_says_failed(engine):
    bus = _Bus()
    await _run_turn(engine, bus, last="error")
    assert [e[1]["outcome"] for e in bus.events] == ["failed"]


@pytest.mark.parametrize(
    "kw",
    [
        {"age_s": 20.0},                 # a short answer is read where it was asked
        {"surface": "build"},            # Chat › Code has its own notice
        {"last": ""},                    # stopped: not news
        {"thread": ""},                  # no chat to open
        {"bind": False},                 # no bus bound: nothing (and no error)
    ],
)
async def test_no_notice_for_what_is_not_news(engine, kw, monkeypatch):
    import weakref

    bus = _Bus()
    if not kw.get("bind", True):
        monkeypatch.setattr(DT, "_BUSES", weakref.WeakKeyDictionary())
    await _run_turn(engine, bus, **kw)
    assert bus.events == []


def test_the_app_binds_its_bus_for_the_turns(tmp_path):
    app = create_app(str(tmp_path))
    p = app.state.platform
    assert DT._bus_for(p.engine) is p.event_bus
