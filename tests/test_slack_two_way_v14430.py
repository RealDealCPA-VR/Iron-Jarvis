"""v1.443.0: Slack two-way answers where the user writes (a user report).

The user's words: "I can send it messages, but it doesn't send a message back
... me sending IJ a message from Slack goes into the void." Read off the live
install: the Socket Mode pump connected, and not one Slack message ever reached
the inbound pipeline (no ``comm.received``, no ``comm.rejected``, ever). The
manifest subscribed to ``message.im`` alone, and the pump answered DMs only, so
a message in the destination's own channel (#iron-jarvis, where the alerts
land) never left Slack, and if it had, it fired reflex rules only.

Pinned here:

* the destination's own channel is found by name (paged), joined when the bot
  is not a member, and every failure is a sentence (never a crash);
* a message there is answered by the REAL inbound pipeline in a THREAD under
  it, on the chat lane with the default model (no provider is named);
* an @mention elsewhere is answered privately in the DM; a plain message in
  another channel is reflex-only; Slack's second copy of a mention is dropped;
* Jarvis's own threaded answer is never read back as the user's message;
* the private-chat guard still refuses a channel reply for anything else;
* the manifest subscribes to channel messages and mentions;
* the Notifications row says what the pump has heard.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.comm import InboundMessage, Notifier
from iron_jarvis.comm import slack_socket as S
from iron_jarvis.comm.channels import SlackChannel
from iron_jarvis.comm.inbound import InboundPoller
from iron_jarvis.comm.threads import CommThreadStore
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import comm as comm_routes

OWN = "C0OWN0001"


@pytest.fixture(autouse=True)
def _fresh_listening():
    S.LISTENING.clear()
    yield
    S.LISTENING.clear()


class _Api:
    """A stand-in for Slack's Web API, recording every call (no network)."""

    def __init__(self, pages=None, *, member=True, private=False, error="", join_ok=True):
        self.calls: list[tuple[str, dict]] = []
        self.pages = pages if pages is not None else [
            [{"id": "C0OTHER01", "name": "general", "is_member": True}],
            [{"id": OWN, "name": "iron-jarvis", "is_member": member, "is_private": private}],
        ]
        self.error = error
        self.join_ok = join_ok

    def __call__(self, method: str, token: str, params: dict) -> dict:
        self.calls.append((method, dict(params)))
        assert token == "xoxb-test"
        if method == "auth.test":
            return {"ok": True, "user_id": "UBOT"}
        if method == "conversations.list":
            if self.error:
                return {"ok": False, "error": self.error}
            page = int(params.get("cursor") or 0)
            nxt = str(page + 1) if page + 1 < len(self.pages) else ""
            return {
                "ok": True,
                "channels": self.pages[page],
                "response_metadata": {"next_cursor": nxt},
            }
        if method == "conversations.join":
            return {"ok": self.join_ok, "error": "" if self.join_ok else "missing_scope"}
        raise AssertionError(method)


class _Recorder:
    def __init__(self):
        self.calls: list[dict] = []

    async def on_slack(self, *, text: str, channel: str = "", sender: str = "") -> list:
        self.calls.append({"text": text, "channel": channel, "sender": sender})
        return []


def _slack_channel(sent: list, **extra: Any) -> SlackChannel:
    def http_post(url, payload, headers=None):  # noqa: ANN001
        sent.append(dict(payload))
        return {"ok": True}

    cfg = {
        "token_secret": "tok",
        "channel": "#iron-jarvis",
        "inbound_enabled": True,
        "chat_enabled": True,
        "allowed_senders": ["U1"],
        **extra,
    }
    return SlackChannel(cfg, http_post=http_post, secret_resolver=lambda n: "xoxb-test")


def _pump(poller: Any, ch: SlackChannel, api: _Api | None = None) -> S.SlackSocketMode:
    notifier = Notifier()
    notifier.add_channel("hq", ch)
    cfg = {"channels": {"hq": {"type": "slack", **ch.config}}}
    return S.SlackSocketMode(
        poller,
        notifier,
        lambda n: "xoxb-test",
        lambda: cfg,
        slack_api=api or _Api(),
    )


def _env(**event: Any) -> dict:
    return {"type": "events_api", "envelope_id": "e1", "payload": {"event": event}}


class _FakePoller:
    def __init__(self, reflex_router=None):
        self.reflex_router = reflex_router
        self.handled: list[InboundMessage] = []

    async def _handle(self, name, ch, msg):  # noqa: ANN001
        self.handled.append(msg)
        return {"channel": name, "status": "handled"}


# --------------------------------------------------------------------------- #
# Finding the destination's own channel
# --------------------------------------------------------------------------- #
def test_the_own_channel_is_found_by_name_across_pages():
    api = _Api()
    sock = _pump(_FakePoller(), _slack_channel([]), api)
    assert sock.resolve_own_channel("hq") == OWN
    assert [m for m, _ in api.calls] == ["auth.test", "conversations.list", "conversations.list"]
    assert sock._bot_user["hq"] == "UBOT"
    assert S.LISTENING["hq"]["own_channel"] == OWN
    assert S.LISTENING["hq"]["channel_note"] == ""


def test_a_public_channel_the_bot_is_not_in_is_joined():
    api = _Api(member=False)
    sock = _pump(_FakePoller(), _slack_channel([]), api)
    assert sock.resolve_own_channel("hq") == OWN
    assert ("conversations.join", {"channel": OWN}) in api.calls
    assert S.LISTENING["hq"]["channel_note"] == ""


def test_a_channel_it_cannot_join_says_to_invite_it():
    api = _Api(member=False, join_ok=False)
    sock = _pump(_FakePoller(), _slack_channel([]), api)
    sock.resolve_own_channel("hq")
    assert "/invite @Iron Jarvis" in S.LISTENING["hq"]["channel_note"]


def test_a_private_channel_is_never_joined_by_itself():
    api = _Api(member=False, private=True)
    sock = _pump(_FakePoller(), _slack_channel([]), api)
    sock.resolve_own_channel("hq")
    assert not any(m == "conversations.join" for m, _ in api.calls)
    assert "/invite" in S.LISTENING["hq"]["channel_note"]


def test_an_app_without_channels_read_is_said_in_words():
    api = _Api(error="missing_scope")
    sock = _pump(_FakePoller(), _slack_channel([]), api)
    assert sock.resolve_own_channel("hq") is None
    note = S.LISTENING["hq"]["channel_note"]
    assert "channels:read" in note and "manifest" in note


def test_a_channel_given_as_an_id_is_used_as_it_is():
    api = _Api()
    sock = _pump(_FakePoller(), _slack_channel([], channel=OWN), api)
    assert sock.resolve_own_channel("hq") == OWN
    assert [m for m, _ in api.calls] == ["auth.test"]


def test_a_look_up_that_raises_is_a_sentence():
    def boom(method, token, params):  # noqa: ANN001
        if method == "auth.test":
            return {"ok": True, "user_id": "UBOT"}
        raise OSError("no network")

    sock = _pump(_FakePoller(), _slack_channel([]), boom)  # type: ignore[arg-type]
    assert sock.resolve_own_channel("hq") is None
    assert "no network" in S.LISTENING["hq"]["channel_note"]


# --------------------------------------------------------------------------- #
# Where each message is answered
# --------------------------------------------------------------------------- #
def _ready(poller: Any, ch: SlackChannel, api: _Api | None = None) -> S.SlackSocketMode:
    sock = _pump(poller, ch, api)
    sock._own["hq"] = sock.resolve_own_channel("hq")
    return sock


def test_a_message_in_the_own_channel_is_answered_in_a_thread():
    poller = _FakePoller(_Recorder())
    sock = _ready(poller, _slack_channel([]))
    env = _env(type="message", channel=OWN, channel_type="channel", user="U1",
               text="what's on today?", ts="1700.1")
    assert asyncio.run(sock.process_envelope("hq", env)) == {"channel": "hq", "status": "handled"}
    msg = poller.handled[0]
    assert msg.reply_to == f"{OWN}|1700.1" and msg.own_channel is True
    assert msg.text == "what's on today?"
    # The channel's reflex rules still hear it, as before.
    assert poller.reflex_router.calls[0]["channel"] == OWN


def test_a_reply_inside_a_thread_stays_in_that_thread():
    poller = _FakePoller()
    sock = _ready(poller, _slack_channel([]))
    env = _env(type="message", channel=OWN, channel_type="channel", user="U1",
               text="and tomorrow?", ts="1700.9", thread_ts="1700.1")
    asyncio.run(sock.process_envelope("hq", env))
    assert poller.handled[0].reply_to == f"{OWN}|1700.1"


def test_a_mention_in_the_own_channel_is_answered_once_without_the_tag():
    poller = _FakePoller()
    sock = _ready(poller, _slack_channel([]))
    base = dict(channel=OWN, channel_type="channel", user="U1",
                text="<@UBOT> summarize my day", ts="1701.0")
    first = asyncio.run(sock.process_envelope("hq", _env(type="message", **base)))
    second = asyncio.run(sock.process_envelope("hq", _env(type="app_mention", **base)))
    assert first["status"] == "handled" and second["status"] == "duplicate"
    assert len(poller.handled) == 1
    assert poller.handled[0].text == "summarize my day"


def test_a_mention_elsewhere_is_answered_privately_in_the_dm():
    sent: list = []
    poller = _FakePoller(_Recorder())
    sock = _ready(poller, _slack_channel(sent))
    env = _env(type="app_mention", channel="C0OTHER01", user="U1",
               text="<@UBOT> status?", ts="1702.0")
    asyncio.run(sock.process_envelope("hq", env))
    msg = poller.handled[0]
    assert msg.reply_to == "U1" and msg.own_channel is False
    # One short note in the DM first; nothing is posted to the shared channel.
    assert sent == [{"channel": "U1", "text": S.MENTION_ELSEWHERE_NOTE}]


def test_a_plain_message_in_another_channel_is_reflex_only():
    rec = _Recorder()
    poller = _FakePoller(rec)
    sock = _ready(poller, _slack_channel([]))
    env = _env(type="message", channel="C0OTHER01", channel_type="channel", user="U1",
               text="lunch?", ts="1703.0")
    assert asyncio.run(sock.process_envelope("hq", env))["status"] == "reflex"
    assert poller.handled == [] and len(rec.calls) == 1


def test_jarvis_own_answer_is_never_read_back():
    poller = _FakePoller(_Recorder())
    sock = _ready(poller, _slack_channel([]))
    for extra in ({"user": "UBOT"}, {"user": "U1", "bot_id": "B1"}):
        env = _env(type="message", channel=OWN, channel_type="channel",
                   text="Here is your answer", ts="1704.0", **extra)
        assert asyncio.run(sock.process_envelope("hq", env)) is None
    assert poller.handled == [] and poller.reflex_router.calls == []


def test_a_stranger_in_the_own_channel_gets_nothing():
    poller = _FakePoller(_Recorder())
    sock = _ready(poller, _slack_channel([]))
    env = _env(type="message", channel=OWN, channel_type="channel", user="U999",
               text="hi", ts="1705.0")
    assert asyncio.run(sock.process_envelope("hq", env)) is None
    assert poller.handled == [] and poller.reflex_router.calls == []


def test_an_unresolved_own_channel_falls_back_to_reflex_only():
    poller = _FakePoller(_Recorder())
    sock = _ready(poller, _slack_channel([]), _Api(error="missing_scope"))
    env = _env(type="message", channel=OWN, channel_type="channel", user="U1",
               text="hello?", ts="1706.0")
    assert asyncio.run(sock.process_envelope("hq", env))["status"] == "reflex"
    assert poller.handled == []


# --------------------------------------------------------------------------- #
# The real pipeline: chat lane, default model, a threaded reply
# --------------------------------------------------------------------------- #
def _turn(reply: str = "You have two meetings."):
    async def turn(platform, personas, body) -> dict[str, Any]:
        turn.calls.append(body)
        return {
            "reply": reply, "provider": "mock", "model": "m", "tools_used": [],
            "escalate": False, "escalate_reason": "", "documents": [],
        }

    turn.calls = []
    return turn


def test_the_real_pipeline_answers_in_the_thread_on_the_default_model(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    sent: list = []
    ch = _slack_channel(sent)
    notifier = Notifier()
    notifier.add_channel("hq", ch)
    turn = _turn()
    poller = InboundPoller(
        notifier, Orchestrator(platform), platform.engine,
        event_bus=platform.event_bus, thread_store=CommThreadStore(platform.engine),
        chat_turn=turn, personas={}, platform=platform,
    )
    cfg = {"channels": {"hq": {"type": "slack", **ch.config}}}
    sock = S.SlackSocketMode(poller, notifier, lambda n: "xoxb-test", lambda: cfg,
                             slack_api=_Api())
    sock._own["hq"] = sock.resolve_own_channel("hq")
    env = _env(type="message", channel=OWN, channel_type="channel", user="U1",
               text="what's on today?", ts="1800.5")

    async def go():
        res = await sock.process_envelope("hq", env)
        await poller.drain()
        return res

    res = asyncio.run(go())
    assert res["status"] != "non_private", res
    # The chat turn ran with no provider named: the default model answers.
    assert len(turn.calls) == 1
    body = turn.calls[0]
    assert not getattr(body, "provider", "")
    assert body.messages[-1].content == "what's on today?"
    # The answer went into the thread under the user's message.
    replies = [p for p in sent if p.get("thread_ts") == "1800.5"]
    assert replies and replies[0]["channel"] == OWN
    assert "two meetings" in replies[0]["text"]


def test_the_private_chat_guard_still_refuses_a_channel_reply_without_the_flag(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    sent: list = []
    ch = _slack_channel(sent)
    notifier = Notifier()
    notifier.add_channel("hq", ch)
    turn = _turn()
    poller = InboundPoller(
        notifier, Orchestrator(platform), platform.engine,
        event_bus=platform.event_bus, thread_store=CommThreadStore(platform.engine),
        chat_turn=turn, personas={}, platform=platform,
    )
    msg = InboundMessage(sender_id="U1", text="hi", reply_to=f"{OWN}|1.0")
    res = asyncio.run(poller._handle("hq", ch, msg))
    assert res["status"] == "non_private"
    assert turn.calls == [] and sent == []


def test_slack_send_puts_a_thread_address_in_thread_ts():
    sent: list = []
    ch = _slack_channel(sent)
    ch.send("answer", chat_id=f"{OWN}|1900.1")
    ch.send("dm answer", chat_id="U1")
    assert sent[0] == {"channel": OWN, "text": "answer", "thread_ts": "1900.1"}
    assert sent[1] == {"channel": "U1", "text": "dm answer"}


# --------------------------------------------------------------------------- #
# The manifest and the Notifications row
# --------------------------------------------------------------------------- #
def test_the_manifest_sends_channel_messages_and_mentions(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    types = client.get("/comm/channel-types").json()["types"]
    slack = next(t for t in types if t["type"] == "slack")
    man = json.loads(slack["manifest"])
    events = set(man["settings"]["event_subscriptions"]["bot_events"])
    assert {"message.im", "message.channels", "message.groups", "app_mention"} <= events
    scopes = set(man["oauth_config"]["scopes"]["bot"])
    assert {"channels:read", "channels:join", "groups:read", "groups:history",
            "app_mentions:read", "channels:history"} <= scopes
    assert "App Manifest" in slack["manifest_help"]


def test_the_row_says_what_slack_has_sent():
    cfg = {"inbound_enabled": True, "channel": "#iron-jarvis"}
    assert comm_routes.listening_status("hq", "slack", {**cfg, "inbound_enabled": False}) is None
    assert comm_routes.listening_status("hq", "telegram", cfg) is None
    # Not connected yet: nothing to say.
    assert comm_routes.listening_status("hq", "slack", cfg)["words"] == ""
    S.LISTENING["hq"] = {"connected_at": "2026-11-07T12:00:00+00:00", "own_channel": OWN,
                         "channel_note": ""}
    st = comm_routes.listening_status("hq", "slack", cfg)
    assert st["words"] == comm_routes.NOTHING_HEARD_WORDS and st["tone"] == "warn"
    S.LISTENING["hq"]["last_event_at"] = "2026-11-07T12:01:00+00:00"
    st = comm_routes.listening_status("hq", "slack", cfg)
    assert "#iron-jarvis" in st["words"] and st["tone"] == "quiet"
    S.LISTENING["hq"]["channel_note"] = "Jarvis isn't a member of #iron-jarvis."
    st = comm_routes.listening_status("hq", "slack", cfg)
    assert st["words"].startswith("Jarvis isn't a member") and st["tone"] == "warn"


def test_the_channels_route_carries_listening(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    r = client.post("/comm/channels", json={"type": "slack", "name": "hq", "config": {
        "token": "xoxb-test", "app_token": "xapp-test", "channel": "#iron-jarvis",
        "inbound_enabled": "true", "allowed_senders": "U1",
    }})
    assert r.status_code == 200, r.text
    S.LISTENING["hq"] = {"connected_at": "2026-11-07T12:00:00+00:00"}
    rows = {c["name"]: c for c in client.get("/comm/channels").json()["channels"]}
    assert rows["hq"]["listening"]["words"] == comm_routes.NOTHING_HEARD_WORDS
