"""v1.455.0 REVIEW — the defects an independent review found in the
"a buffered tool call no longer drops the reply" change, each pinned red
first.

1. A fleet node (or the custom slot) whose request LEAVES the user's network
   (a cloud API added as a model server, or a name the user's proxy sends on
   to one — ``facts["cloud"]``) got the 10-minute read too: every fleet
   adapter has a reply budget. The owner's rule: cloud keeps 60 s.
2. The turn reference rode into work that is NOT the chat turn: an agent run
   the turn started (``Orchestrator.run_session``) and work that outlives the
   turn (a Build library reflection armed at the turn's end fires ten minutes
   later from the turn's context). Its provider failure named the chat ("Your
   coding session … didn't finish") or was silenced because the chat was
   open.
3. "Your chat … didn't finish" was said for EVERY provider failure inside a
   turn — also when a failover, a patience retry or a one-shot inside the turn
   failed and the turn then finished fine. The line now waits for the turn's
   end and is said only when the turn really ended in an error (and nobody
   was looking).
4. A window MINIMISED (or covered, or the screen locked) on the chat counted
   as "looking", so the phone stayed quiet while nobody could see the chat:
   the chat page now says ``away`` while its window is hidden and ``back``
   when it shows again.
5. The in-turn line always ended "Open the chat and press Retry." — for a
   refused key, a sign-in or an account's usage limit a Retry fails again;
   the classified way out is kept, and a coding session's words say "this
   session".
6. The ``custom`` slot was always "your model server", even when the user
   had named its machine on its fleet twin.
"""

from __future__ import annotations

import asyncio
import json

import httpx

from iron_jarvis.core.turn_ref import CURRENT, TurnRef, event_keys, scope
from iron_jarvis.daemon import durable_turns
from iron_jarvis.providers import reply_budget
from iron_jarvis.providers.adapters.base import LLMMessage
from iron_jarvis.providers.adapters.openai import OpenAIAdapter
from iron_jarvis.providers.reply_budget import ReplyBudget

MODEL = "glm-5.3-flash"
_MSG = [LLMMessage(role="user", content="create a reconciliation excel")]
_SSE = (
    'data: {"choices":[{"index":0,"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\n'
    "data: [DONE]\n\n"
)


# --------------------------------------------------------------------------- #
# 1. a cloud model behind a model server keeps 60 s
# --------------------------------------------------------------------------- #
def _recording_client(seen: list[dict]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.extensions.get("timeout") or {}))
        if json.loads(request.content or b"{}").get("stream"):
            return httpx.Response(200, text=_SSE, headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={
            "id": "m1", "type": "message", "role": "assistant", "model": "x",
            "content": [{"type": "text", "text": "Hello."}], "stop_reason": "end_turn",
            "usage": {"input_tokens": 3, "output_tokens": 2},
        })

    return httpx.AsyncClient(timeout=60.0, transport=httpx.MockTransport(handler))


def _facts(cloud: bool):
    return lambda provider, model: {"cloud": cloud}


async def test_a_cloud_model_through_a_model_server_keeps_the_60_second_read():
    """RED before the review: ``_stream_kw`` checked only that a reply budget
    exists, and every fleet node and config slot has one."""
    for cloud, read in ((True, 60.0), (False, float(reply_budget.LOCAL_STREAM_READ_S))):
        seen: list[dict] = []
        adapter = OpenAIAdapter(
            model="frontier", base_url="http://box.local:4000/v1/chat/completions",
            provider_name="fleet-dgx",
            reply_budget=ReplyBudget("fleet-dgx", "x", facts=_facts(cloud)),
            http=_recording_client(seen),
        )
        async for _ in adapter.stream(system="", messages=_MSG, tools=[]):
            pass
        assert seen[-1]["read"] == read, (cloud, seen)
        assert seen[-1]["connect"] == 60.0


async def test_a_cloud_anthropic_compatible_node_keeps_the_60_second_read():
    from iron_jarvis.fleet.anthropic_compat import AnthropicFleetAdapter
    from iron_jarvis.fleet.registry import FleetNode

    for cloud, read in ((True, 60.0), (False, float(reply_budget.LOCAL_STREAM_READ_S))):
        seen: list[dict] = []
        node = FleetNode(id="mm", label="MiniMax", base_url="https://api.example.com",
                         source="user", routable=True, default_model="m", protocol="anthropic")
        adapter = AnthropicFleetAdapter(
            node=node, credential=lambda: "k", reply_facts=_facts(cloud),
            http=_recording_client(seen),
        )
        try:
            async for _ in adapter.stream(system="", messages=_MSG, tools=[]):
                pass
        except httpx.HTTPError:  # the stand-in answers JSON; the timeout was sent
            pass
        assert seen[-1]["read"] == read, (cloud, seen)


# --------------------------------------------------------------------------- #
# 2. the turn reference stays in the turn
# --------------------------------------------------------------------------- #
def test_a_turn_that_has_ended_lends_its_reference_to_nothing():
    """Work the turn armed that runs after it (the library reflection, a
    background task) publishes exactly what it did before v1.455.0."""
    alive = {"on": True}
    ref = TurnRef("turn-1", lambda: "chat_1", lambda: True, lambda: alive["on"])
    with scope(ref):
        assert event_keys()["turn_id"] == "turn-1"
        alive["on"] = False
        assert event_keys() == {}


async def test_the_drives_reference_says_nothing_once_its_turn_ended():
    """The reference ``TurnLogs._drive`` lends is alive only while the turn
    runs: a task the turn made that publishes AFTER it (the reflection timer)
    attaches no turn."""
    logs = durable_turns.TurnLogs(None)
    seen: dict = {}

    async def frames():
        seen["ref"] = CURRENT.get()
        seen["during"] = event_keys().get("turn_id")
        yield 'event: token' + chr(10) + 'data: {"text": "hi"}' + chr(10) * 2

    lg = logs.start("turn-x", "chat_1", frames())
    await lg.task
    assert seen["during"] == "turn-x"
    with scope(seen["ref"]):
        assert event_keys() == {}


async def test_an_agent_run_the_turn_starts_is_not_the_turn(tmp_path, monkeypatch):
    """RED before the review: ``run_session`` ran with the chat turn's
    reference, so the run's provider failures named the chat."""
    from iron_jarvis.agents.orchestrator import Orchestrator

    seen: list = []

    async def body(self, session, definition, *, unattended, nested):
        seen.append(CURRENT.get())
        return session

    monkeypatch.setattr(Orchestrator, "_run_session_body", body)

    class _Sess:
        origin = "chat"

    orch = Orchestrator.__new__(Orchestrator)
    monkeypatch.setattr(Orchestrator, "get_session", lambda self, sid: _Sess(), raising=False)
    ref = TurnRef("turn-9", lambda: "chat_1", lambda: True, lambda: True)
    with scope(ref):
        await orch.run_session("s1")
        assert CURRENT.get() is ref, "the turn's own reference comes back after the run"
    assert seen == [None]


# --------------------------------------------------------------------------- #
# 3. "didn't finish" waits for the turn to end in an error
# --------------------------------------------------------------------------- #
class _Rec:
    def __init__(self):
        self.sent: list[str] = []
        self.config: dict = {}

    def send(self, message):
        self.sent.append(message)
        return {"ok": True}


class _OneDown:
    """A manager whose one provider is the user's model server."""

    def __init__(self):
        from types import SimpleNamespace

        self.adapter = SimpleNamespace(provider="fleet-custom", model=MODEL,
                                       node=SimpleNamespace(label="Custom endpoint"))

    def get(self, p, model=None):
        return self.adapter


def _turn_world():
    from iron_jarvis.comm.notifier import Notifier
    from iron_jarvis.core.events import EventBus
    from iron_jarvis.providers.router import ModelRouter

    bus = EventBus()
    notifier = Notifier()
    phone = _Rec()
    notifier.add_channel("phone", phone)
    bus.add_handler(notifier.on_event)
    router = ModelRouter(_OneDown(), default_provider="fleet-custom", event_bus=bus)
    return bus, router, phone


async def _publish_failure(router):
    from iron_jarvis.core.events import EventType

    await router._publish_provider_event(
        EventType.PROVIDER_FAILED,
        {"provider": "fleet-custom", "error": "ReadTimeout: ", "partial": True},
        session_id="chat",
    )


def _frame(event: str, data: dict) -> str:
    return f"event: {event}" + chr(10) + "data: " + json.dumps(data) + chr(10) * 2


async def _settle(phone, want: int, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end and len(phone.sent) < want:
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.2)  # and nothing more arrives


async def test_a_failure_the_turn_got_past_is_never_said_as_didnt_finish():
    """RED before the review: the phone got "Your chat didn't finish: … Open
    the chat and press Retry." while the turn went on and finished (a
    failover to another model, a patience retry, a one-shot inside it)."""
    _bus, router, phone = _turn_world()
    logs = durable_turns.TurnLogs(None)

    async def frames():
        await _publish_failure(router)
        yield _frame("token", {"text": "Here it is."})
        yield _frame("done", {"reply": "Here it is."})

    lg = logs.start("turn-ok", "", frames())
    await lg.task
    await _settle(phone, 1, timeout=1.0)
    assert phone.sent == []


async def test_a_turn_that_ended_in_an_error_says_it_once_with_the_newest_failure():
    _bus, router, phone = _turn_world()
    logs = durable_turns.TurnLogs(None)

    async def frames():
        await router._publish_provider_event(
            __import__("iron_jarvis.core.events", fromlist=["EventType"]).EventType.PROVIDER_FAILED,
            {"provider": "fleet-custom", "error": "ConnectError: refused"},
            session_id="chat",
        )
        await _publish_failure(router)
        yield _frame("error", {"detail": "dropped"})

    lg = logs.start("turn-bad", "", frames())
    await lg.task
    await _settle(phone, 1)
    assert len(phone.sent) == 1, phone.sent
    assert phone.sent[0].startswith("Your chat didn't finish: The connection to your model"
                                    " server dropped mid-answer.")


async def test_a_window_showing_the_turn_as_it_ends_keeps_the_phone_quiet():
    _bus, router, phone = _turn_world()
    logs = durable_turns.TurnLogs(None)
    looking = {"now": False}

    async def frames():
        await _publish_failure(router)
        looking["now"] = True  # the user came back to the chat
        yield _frame("error", {"detail": "dropped"})

    lg = logs.start("turn-seen", "", frames())
    lg.looked_at = lambda: looking["now"]  # type: ignore[method-assign]
    await lg.task
    await _settle(phone, 1, timeout=1.0)
    assert phone.sent == []


def test_a_failure_heard_after_its_turn_ended_is_decided_at_once():
    from iron_jarvis.comm.notifier import Notifier

    lg = durable_turns.TurnLog("turn-late", "")
    lg.end("error", False)
    durable_turns._LIVE["turn-late"] = lg
    try:
        n = Notifier()
        phone = _Rec()
        n.add_channel("phone", phone)
        assert n.on_event({"type": "provider.failed", "session_id": "chat", "payload": {
            "provider": "fleet-custom", "error": "ReadTimeout: ", "partial": True,
            "turn_id": "turn-late", "looked_at": False, "label": "your model server",
        }}) == {}
        assert len(phone.sent) == 1
        lg2 = durable_turns.TurnLog("turn-late-ok", "")
        lg2.end("done", False)
        durable_turns._LIVE["turn-late-ok"] = lg2
        n.on_event({"type": "provider.failed", "session_id": "chat", "payload": {
            "provider": "fleet-custom", "error": "ReadTimeout: ", "turn_id": "turn-late-ok",
            "looked_at": False,
        }})
        assert len(phone.sent) == 1
    finally:
        durable_turns._LIVE.pop("turn-late", None)
        durable_turns._LIVE.pop("turn-late-ok", None)


# --------------------------------------------------------------------------- #
# 4. a hidden window is nobody looking (and is seen again when it shows)
# --------------------------------------------------------------------------- #
def test_a_hidden_window_is_away_until_it_shows_the_chat_again(tmp_path):
    """RED before the review: a window MINIMISED (or covered, or the screen
    locked) on the chat still counted as "looking", so the failed turn's
    phone line was kept back while nobody could see the chat."""
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    app = create_app(str(tmp_path))
    logs = durable_turns.TurnLogs(None)
    app.state.platform.turn_logs = logs
    lg = durable_turns.TurnLog("turn-h", "chat_1")
    q: asyncio.Queue = asyncio.Queue()
    lg._followers.add(q)
    lg._viewers[q] = "view-1"
    logs._logs["turn-h"] = lg
    assert lg.looked_at() is True
    with TestClient(app) as c:
        r = c.post("/chat/turns/turn-h/away", json={"viewer": "view-1"})
        assert r.status_code == 200 and r.json() == {"away": True}
        assert lg.looked_at() is False
        r = c.post("/chat/turns/turn-h/away", json={"viewer": "view-1", "back": True})
        assert r.status_code == 200 and r.json() == {"away": True}
        assert lg.looked_at() is True
        # Only a real ``true`` brings it back.
        c.post("/chat/turns/turn-h/away", json={"viewer": "view-1"})
        c.post("/chat/turns/turn-h/away", json={"viewer": "view-1", "back": "yes"})
        assert lg.looked_at() is False


# --------------------------------------------------------------------------- #
# 5. the chat line keeps a fix that is not "press Retry"
# --------------------------------------------------------------------------- #
def _in_turn_line(error: str, provider: str, **extra) -> str:
    from iron_jarvis.comm.notifier import format_event

    return format_event({"type": "provider.failed", "session_id": "chat", "payload": {
        "provider": provider, "error": error, "turn_id": "t", "thread_id": "",
        "looked_at": False, **extra,
    }})


def test_a_failure_retry_cannot_fix_keeps_its_own_way_out():
    """RED before the review: every in-turn line ended "Open the chat and
    press Retry." — for a refused key or an account's usage limit, a Retry
    fails again the same way; the line said the one thing that does not
    help and dropped the one that does."""
    from iron_jarvis.comm.notifier import CHAT_TURN_FAILED_FIX, CODE_TURN_FAILED_FIX

    key = _in_turn_line("ProviderError: openai API error 401: invalid api key", "openai")
    assert key.startswith("Your chat didn't finish: OpenAI refused your key.")
    assert "Update it in Settings" in key and "press Retry" not in key, key
    # A plain drop keeps the chat's own Retry words.
    drop = _in_turn_line("ReadTimeout: ", "fleet-custom", partial=True,
                         label="your model server")
    assert drop.endswith(CHAT_TURN_FAILED_FIX)
    # A coding session's line is a coding session's, never "this chat".
    code = _in_turn_line("ProviderError: openai API error 503: busy", "openai",
                         chat_surface="build")
    assert code.startswith("Your coding session didn't finish: OpenAI is busy.")
    assert "this chat" not in code, code
    gen = _in_turn_line("RuntimeError: something odd", "fleet-custom", chat_surface="build",
                        label="your model server")
    assert gen.endswith(CODE_TURN_FAILED_FIX), gen


# --------------------------------------------------------------------------- #
# 6. the custom slot keeps the name the user gave it
# --------------------------------------------------------------------------- #
async def test_a_labelled_custom_slot_keeps_its_label(monkeypatch):
    """RED before the review: the router named the ``custom`` slot "your
    model server" even when the user had named its machine ("DGX Spark" on
    its fleet node, the name the receipt and the model menu use)."""
    import iron_jarvis.providers.router as rmod
    from iron_jarvis.comm.notifier import format_event
    from iron_jarvis.core.events import EventBus, EventType
    from iron_jarvis.providers.router import ModelRouter

    from types import SimpleNamespace

    class _Down:
        provider, model = "custom", MODEL

        async def complete(self, *, system, messages, tools):
            raise httpx.ConnectError("refused")

    twin = SimpleNamespace(provider="fleet-custom", model=MODEL,
                           node=SimpleNamespace(label="DGX Spark"))

    class _M:
        def available(self, p):
            return p == "custom"

        def has_available_api_provider(self):
            return True

        def get(self, p, model=None):
            return {"custom": _Down(), "fleet-custom": twin}[p]

    async def _sleep(_):
        return None

    monkeypatch.setattr(rmod.asyncio, "sleep", _sleep)
    bus = EventBus()
    router = ModelRouter(_M(), default_provider="custom", event_bus=bus)
    assert router._endpoint_label("custom") == "DGX Spark"
    try:
        await router.complete(system="", messages=_MSG, tools=[])
    except Exception as exc:  # noqa: BLE001
        assert "DGX Spark" in str(exc), str(exc)
    failed = [e.payload for e in bus.history if e.type == EventType.PROVIDER_FAILED]
    assert failed and failed[-1]["label"] == "DGX Spark"
    line = format_event({"type": "provider.failed", "payload": failed[-1]})
    assert line.startswith("DGX Spark "), line
    # An unnamed slot (no twin, or the seeded label) is "your model server".
    twin.node.label = "Custom endpoint"
    assert router._endpoint_label("custom") == "your model server"


async def test_an_event_handler_never_runs_as_the_chat_turn():
    """The builder's bus clear, pinned (it had no pin): a handler that starts
    work (the skill-distill sweep) is not the turn, while the turn itself
    keeps its reference."""
    from iron_jarvis.core.events import EventBus

    bus = EventBus()
    seen: list = []
    bus.add_handler(lambda e: seen.append(CURRENT.get()))
    ref = TurnRef("turn-h", lambda: "c", lambda: False, lambda: True)
    with scope(ref):
        await bus.publish("session.completed", {"x": 1})
        assert CURRENT.get() is ref
    assert seen == [None]
