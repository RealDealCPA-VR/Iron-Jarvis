"""v1.455.0 — a failed chat turn on the user's own model server: the words
name the server properly, and the phone alert names the chat and stays quiet
while the user is looking at it (a USER BUG).

The owner's chat on their LiteLLM proxy failed twice; the in-app line said
"The connection to Custom endpoint dropped… Check that endpoint" ("Custom
endpoint" is the registry's SEEDED label, not a name), and the phone got
"Fleet-custom couldn't answer. Press Retry. If it happens again, pick another
model for this chat." — the raw id, no chat named, sent while the user was
looking at that very chat.

The lane pins drive the REAL chat stream lane, the REAL ``custom`` adapter and
a REAL local stand-in server (``tests/fixtures/fake_openai_endpoint_v1387.py``).
"""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest
from sqlmodel import Session as _DB

from iron_jarvis.comm.notifier import (
    CHAT_TURN_FAILED_FIX,
    DEFAULT_ALERT_EVENTS,
    Notifier,
    format_event,
)
from iron_jarvis.core.events import EventBus, EventType
from iron_jarvis.core.models import ChatThreadRecord
from iron_jarvis.core.turn_ref import TurnRef, event_keys, scope
from iron_jarvis.daemon import durable_turns
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import chat as chat_routes
from iron_jarvis.providers import failure_words, reply_budget
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMMessage
from iron_jarvis.providers.router import ModelRouter
from tests.fixtures.fake_openai_endpoint_v1387 import FakeEndpoint, Reply
from tests.test_chat_turn_stop_v1241 import _scope

MODEL = "glm-5.3-flash"
TITLE = "organize all the documents in the folder into a reconciliation excel"


# --------------------------------------------------------------------------- #
# 2. the server's name
# --------------------------------------------------------------------------- #
def test_an_unnamed_own_server_reads_your_model_server():
    """RED before v1.455.0: plain_name answered "fleet-custom" (no manager)
    and "Custom endpoint" (the seeded label) — "Fleet-custom couldn't
    answer" on the phone."""
    pn = failure_words.plain_name
    assert pn("fleet-custom") == "your model server"
    assert pn("fleet-ollama") == "your model server"
    assert pn("custom") == "your model server"

    class _M:
        def __init__(self, label):
            self.label = label

        def get(self, *_a, **_k):
            from types import SimpleNamespace

            return SimpleNamespace(node=SimpleNamespace(label=self.label))

    assert pn("fleet-custom", _M("Custom endpoint")) == "your model server"
    assert pn("fleet-ollama", _M("Ollama endpoint")) == "your model server"
    assert pn("fleet-dgx", _M("Custom endpoint")) == "your model server"
    # A node the user labelled keeps its label.
    assert pn("fleet-custom", _M("DGX Spark")) == "DGX Spark"
    assert pn("fleet-dgx", _M("DGX Spark")) == "DGX Spark"
    # The seed labels are the registry's.
    assert failure_words.SEED_ENDPOINT_LABELS == {
        "custom": "Custom endpoint", "ollama": "Ollama endpoint",
    }


class _Down(LLMAdapter):
    def __init__(self, provider, label=""):
        self.provider, self.model = provider, MODEL
        from types import SimpleNamespace

        self.node = SimpleNamespace(label=label)

    async def complete(self, *, system, messages, tools):
        raise httpx.RemoteProtocolError("Server disconnected")

    async def stream(self, *, system, messages, tools):
        yield {"type": "text", "text": "Building the reconciliation workbook now."}
        raise httpx.ReadTimeout("")


class _Mgr:
    def __init__(self, adapters):
        self.adapters = adapters

    def available(self, p):
        return p in self.adapters

    def has_available_api_provider(self):
        return True

    def get(self, p, model=None):
        return self.adapters[p]


def _msgs():
    return [LLMMessage(role="user", content=TITLE)]


@pytest.fixture
def _no_backoff(monkeypatch):
    """The router's same-adapter retries are real; only their sleeps are
    skipped (NOT for the lane pins: this patches ``asyncio.sleep`` itself)."""
    import iron_jarvis.providers.router as rmod

    async def _sleep(_):
        return None

    monkeypatch.setattr(rmod.asyncio, "sleep", _sleep)


@pytest.mark.parametrize("label, name", [("Custom endpoint", "your model server"),
                                         ("DGX Spark", "DGX Spark")])
async def test_the_refusal_names_the_server_and_says_model_server(label, name, _no_backoff):
    """RED before v1.455.0: "the connection to Custom endpoint dropped …
    Check that endpoint"."""
    mgr = _Mgr({"fleet-custom": _Down("fleet-custom", label)})
    router = ModelRouter(mgr, default_provider="fleet-custom", event_bus=EventBus())
    with pytest.raises(Exception) as err:
        await router.complete(system="", messages=_msgs(), tools=[])
    text = str(err.value)
    assert f"the connection to {name} dropped" in text
    assert "Custom endpoint" not in text and "endpoint" not in text
    assert "Check that model server" in text


# --------------------------------------------------------------------------- #
# 3a. the router's event carries the turn (complete() and stream())
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lane", ["complete", "stream"])
async def test_provider_failed_carries_the_chat_turn_only_inside_one(lane, _no_backoff):
    async def run(router):
        with pytest.raises(Exception):
            if lane == "complete":
                await router.complete(system="", messages=_msgs(), tools=[])
            else:
                async for _ in router.stream(system="", messages=_msgs(), tools=[]):
                    pass

    def failed(bus):
        return [e.payload for e in bus.history if e.type == EventType.PROVIDER_FAILED]

    bus = EventBus()
    router = ModelRouter(_Mgr({"fleet-custom": _Down("fleet-custom", "Custom endpoint")}),
                         default_provider="fleet-custom", event_bus=bus)
    await run(router)
    outside = failed(bus)
    assert outside and all("turn_id" not in p and "looked_at" not in p for p in outside)
    assert all(p["label"] == "your model server" for p in outside)
    assert all(list(p)[-1] == "label" for p in outside)

    bus = EventBus()
    router = ModelRouter(_Mgr({"fleet-custom": _Down("fleet-custom", "Custom endpoint")}),
                         default_provider="fleet-custom", event_bus=bus)
    with scope(TurnRef("turn-9", lambda: "chat_1", lambda: True)):
        await run(router)
    inside = failed(bus)
    assert inside
    for p in inside:
        assert (p["turn_id"], p["thread_id"], p["looked_at"]) == ("turn-9", "chat_1", True)
    downgraded = [e.payload for e in bus.history if e.type == EventType.PROVIDER_DOWNGRADED]
    for p in downgraded:  # label stays LAST (v1.329.0)
        assert p["turn_id"] == "turn-9" and list(p)[-1] == "label"


def test_no_turn_no_keys_and_a_failing_read_never_raises():
    assert event_keys() == {}

    def boom():
        raise RuntimeError("x")

    with scope(TurnRef("t", boom, boom)):
        assert event_keys() == {"turn_id": "t", "thread_id": "", "looked_at": False}


# --------------------------------------------------------------------------- #
# 3b. the notifier
# --------------------------------------------------------------------------- #
class _Rec:
    def __init__(self):
        self.sent: list[str] = []
        self.config: dict = {}

    def send(self, message):
        self.sent.append(message)
        return {"ok": True}


def _notifier():
    n = Notifier()
    ch = _Rec()
    n.add_channel("phone", ch)
    return n, ch


def _failed(**extra):
    payload = {"provider": "fleet-custom", "error": "ReadTimeout: ", "partial": True,
               "label": "your model server", **extra}
    return {"type": "provider.failed", "payload": payload, "session_id": "chat"}


def test_the_phone_line_names_the_server_outside_a_chat():
    """RED before v1.455.0: "Fleet-custom couldn't answer. …"."""
    line = format_event(_failed())
    assert line.startswith("Your model server couldn't answer. ")
    assert "Fleet-custom" not in line and "fleet-custom" not in line
    labelled = format_event({"type": "provider.failed", "payload": {
        "provider": "fleet-dgx", "label": "DGX Spark", "error": "ReadTimeout: "}})
    assert labelled.startswith("DGX Spark couldn't answer.")


def test_the_phone_stays_quiet_while_the_user_looks_at_the_chat():
    """RED before v1.455.0: the alert went to every destination."""
    n, ch = _notifier()
    assert n.on_event(_failed(turn_id="t1", thread_id="chat_1", looked_at=True)) == {}
    assert ch.sent == []


def test_a_turn_being_looked_at_NOW_is_quiet_too(tmp_path):
    """The turn's own log says a window shows it (the event said no)."""
    logs = durable_turns.TurnLogs(None)
    lg = durable_turns.TurnLog("t-live", "chat_1")
    lg._followers.add(asyncio.Queue())
    durable_turns._LIVE["t-live"] = lg
    try:
        assert durable_turns.turn_looked_at("t-live") is True
        n, ch = _notifier()
        assert n.on_event(_failed(turn_id="t-live", looked_at=False)) == {}
        assert ch.sent == []
    finally:
        durable_turns._LIVE.pop("t-live", None)
        del logs
    assert durable_turns.turn_looked_at("nobody") is None


def test_nobody_looking_names_the_chat(tmp_path):
    """The line names the chat by its title (read off the turn's database)
    and says to open it and press Retry."""
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    with _DB(engine) as db:
        db.add(ChatThreadRecord(id="chat_1", title=TITLE, messages_json="[]"))
        db.commit()
    lg = durable_turns.TurnLog("t-away", "chat_1")
    lg.engine = engine
    durable_turns._LIVE["t-away"] = lg
    try:
        n, ch = _notifier()
        n.on_event(_failed(turn_id="t-away", thread_id="chat_1", looked_at=False))
        # v1.455.0 review (moved on purpose): the line waits for the turn's
        # end, and goes out only when it ended in an error.
        assert ch.sent == []
        for hook in lg.end("error", False):
            hook("error", False)
    finally:
        durable_turns._LIVE.pop("t-away", None)
    assert len(ch.sent) == 1
    line = ch.sent[0]
    assert line.startswith("Your chat “organize all the documents in the folder into a reco")
    assert "” didn't finish: The connection to your model server dropped mid-answer. " in line
    assert line.endswith(CHAT_TURN_FAILED_FIX)
    assert "Fleet-custom" not in line


def test_a_turn_this_process_does_not_hold_still_says_your_chat():
    n, ch = _notifier()
    n.on_event(_failed(turn_id="gone", thread_id="", looked_at=False))
    assert ch.sent == [f"Your chat didn't finish: The connection to your model server"
                       f" dropped mid-answer. {CHAT_TURN_FAILED_FIX}"]


def test_a_long_chat_reply_finishing_is_not_a_phone_alert():
    assert "chat.turn_finished" not in DEFAULT_ALERT_EVENTS
    n, ch = _notifier()
    assert n.on_event({"type": "chat.turn_finished", "payload": {"thread_id": "c"}}) is None
    assert ch.sent == []


# --------------------------------------------------------------------------- #
# 1 + 3 through the REAL chat lane
# --------------------------------------------------------------------------- #
class _Short60(httpx.AsyncClient):
    """The adapter's real 60-second client, scaled to 0.5 s (only a client
    built with exactly that default)."""

    def __init__(self, *a, **kw):
        if kw.get("timeout") == 60.0:
            kw["timeout"] = 0.5
        super().__init__(*a, **kw)


def _app(tmp_path, url):
    app = create_app(str(tmp_path))
    platform = app.state.platform
    platform.config.custom_base_url = url
    platform.config.custom_model = MODEL
    platform.providers.configure_local(custom_base_url=url, custom_model=MODEL)
    return app


async def _post(app, body):
    raw = json.dumps(body).encode()
    sent = {"done": False}

    async def receive():
        if not sent["done"]:
            sent["done"] = True
            return {"type": "http.request", "body": raw, "more_body": False}
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    got: list[str] = []

    async def send(msg):
        if msg["type"] == "http.response.body":
            got.append(msg.get("body", b"").decode("utf-8", "replace"))

    await asyncio.wait_for(app(_scope("/chat/stream", raw), receive, send), timeout=30)
    return "".join(got)


async def test_the_chat_lane_waits_out_the_silence_and_keeps_the_page_alive(tmp_path, monkeypatch):
    """A reply that goes silent after its first words (the buffered call):
    the stream keeps sending the heartbeat the page's 60 s stall watch reads
    as life, and the reply arrives whole. RED before v1.455.0 (client base
    read scaled to 0.5 s): the turn ended in an error."""
    monkeypatch.setattr(httpx, "AsyncClient", _Short60)
    monkeypatch.setattr(reply_budget, "LOCAL_STREAM_READ_S", 10)
    monkeypatch.setattr(chat_routes, "_SSE_HEARTBEAT_S", 0.1)

    def handler(body):
        return Reply(text="Building the reconciliation workbook now.", pause_s=1.5)

    with FakeEndpoint(handler) as srv:
        app = _app(tmp_path, srv.url)
        raw = await _post(app, {"messages": [{"role": "user", "content": TITLE}],
                                "provider": "custom", "model": MODEL, "turn_id": "turn-live"})
    first = raw.index("event: token")
    done = raw.index("event: done")
    assert raw.count(": keepalive", first, done) >= 5, raw[first:done]
    data = json.loads(raw[done:].split("data:", 1)[1].split("\n\n", 1)[0])
    assert "Building the reconciliation workbook now." in data["reply"]
    assert "event: error" not in raw


async def test_a_dropped_turn_names_itself_and_spares_the_watching_user(tmp_path, monkeypatch):
    """The failure the owner had, inside a NAMED turn the page follows: the
    router's provider.failed carries the turn, says a window was showing it,
    names the server "your model server", and no destination is sent a line."""
    monkeypatch.setattr(reply_budget, "LOCAL_STREAM_READ_S", 0.3)

    def handler(body):
        return Reply(text="Building the reconciliation workbook now.", pause_s=3.0)

    with FakeEndpoint(handler) as srv:
        app = _app(tmp_path, srv.url)
        platform = app.state.platform
        phone = _Rec()
        platform.notifier.add_channel("phone-test", phone)
        raw = await _post(app, {"messages": [{"role": "user", "content": TITLE}],
                                "provider": "custom", "model": MODEL, "turn_id": "turn-drop"})
    assert "event: error" in raw
    end = time.monotonic() + 5
    failed = []
    while time.monotonic() < end and not failed:
        failed = [e.payload for e in platform.event_bus.history
                  if e.type == EventType.PROVIDER_FAILED]
        await asyncio.sleep(0.02)
    assert failed, "no provider.failed"
    p = failed[-1]
    assert p["turn_id"] == "turn-drop"
    assert p["looked_at"] is True
    assert p.get("label") in (None, "your model server")
    assert phone.sent == []
    error = raw[raw.index("event: error"):]
    assert "Custom endpoint" not in error and "Check that endpoint" not in error
