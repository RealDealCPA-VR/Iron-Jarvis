"""Offline tests for the communication channels module (src/iron_jarvis/comm).

No real network: every channel posts through an injected recorder.
"""

from __future__ import annotations

from typing import Any

import pytest

from iron_jarvis.comm import (
    ConsoleChannel,
    DiscordChannel,
    MockChannel,
    Notifier,
    NotifyTool,
    SlackChannel,
    TelegramChannel,
    build_notifier,
    channel_integrations,
    notify_tools,
)
from iron_jarvis.comm.channels import SLACK_POST_MESSAGE_URL
from iron_jarvis.core.events import Event, EventType
from iron_jarvis.tools.base import ToolContext


class RecordingPost:
    """Injected ``http_post`` that records (url, payload) and returns a 200."""

    def __init__(self, response: Any = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.response = response if response is not None else {"status_code": 200}

    def __call__(self, url: str, payload: dict[str, Any]) -> Any:
        self.calls.append((url, payload))
        return self.response

    @property
    def last(self) -> tuple[str, dict[str, Any]]:
        return self.calls[-1]


# --------------------------------------------------------------------------- #
# MockChannel + Notifier routing
# --------------------------------------------------------------------------- #
def test_mock_channel_records_message():
    ch = MockChannel()
    res = ch.send("hello world")
    assert res["ok"] is True
    assert ch.sent == ["hello world"]


def test_notifier_routes_to_selected_and_all_channels():
    a, b = MockChannel(), MockChannel()
    notifier = Notifier()
    notifier.add_channel("a", a)
    notifier.add_channel("b", b)

    # explicit selection -> only that channel
    res = notifier.notify("ping-a", channels=["a"])
    assert set(res) == {"a"}
    assert a.sent == ["ping-a"] and b.sent == []

    # explicit default_channel set by first add_channel; clear it to fan out to all
    notifier.default_channel = None
    res_all = notifier.notify("broadcast")
    assert set(res_all) == {"a", "b"}
    assert a.sent == ["ping-a", "broadcast"]
    assert b.sent == ["broadcast"]


def test_notifier_default_channel_used_when_unspecified():
    a, b = MockChannel(), MockChannel()
    notifier = Notifier(default_channel="a")
    notifier.add_channel("a", a)
    notifier.add_channel("b", b)
    notifier.notify("hi")  # no channels -> default only
    assert a.sent == ["hi"] and b.sent == []


def test_notifier_unknown_channel_reports_failure():
    notifier = Notifier()
    notifier.add_channel("a", MockChannel())
    res = notifier.notify("x", channels=["nope"])
    assert res["nope"]["ok"] is False
    assert "unknown channel" in res["nope"]["detail"]


# --------------------------------------------------------------------------- #
# HTTP channels — correct URL + payload via the injected transport
# --------------------------------------------------------------------------- #
def test_slack_webhook_posts_text_payload():
    post = RecordingPost()
    ch = SlackChannel({"webhook_url": "https://hooks.slack.com/services/X"}, http_post=post)
    res = ch.send("deploy done")
    assert res["ok"] is True
    url, payload = post.last
    assert url == "https://hooks.slack.com/services/X"
    assert payload == {"text": "deploy done"}


def test_slack_postmessage_uses_token_and_channel():
    post = RecordingPost()
    ch = SlackChannel(
        {"token_secret": "slack_bot", "channel": "#general"},
        http_post=post,
        secret_resolver=lambda name: "xoxb-123" if name == "slack_bot" else None,
    )
    res = ch.send("hi team")
    assert res["ok"] is True
    url, payload = post.last
    assert url == SLACK_POST_MESSAGE_URL
    assert payload == {"channel": "#general", "text": "hi team", "token": "xoxb-123"}


def test_discord_posts_content_payload():
    post = RecordingPost()
    ch = DiscordChannel({"webhook_url": "https://discord.com/api/webhooks/Y"}, http_post=post)
    res = ch.send("hello discord")
    assert res["ok"] is True
    url, payload = post.last
    assert url == "https://discord.com/api/webhooks/Y"
    assert payload == {"content": "hello discord"}


def test_telegram_builds_bot_url_and_payload():
    post = RecordingPost()
    ch = TelegramChannel(
        {"token_secret": "tg_token", "chat_id": 4242},
        http_post=post,
        secret_resolver=lambda name: "BOTTOKEN" if name == "tg_token" else None,
    )
    res = ch.send("alert!")
    assert res["ok"] is True
    url, payload = post.last
    assert url == "https://api.telegram.org/botBOTTOKEN/sendMessage"
    assert payload == {"chat_id": 4242, "text": "alert!"}


def test_no_real_network_calls_made():
    """The recorder is the only transport; channels never reach out themselves."""
    post = RecordingPost()
    SlackChannel({"webhook_url": "u"}, http_post=post).send("m")
    DiscordChannel({"webhook_url": "u"}, http_post=post).send("m")
    assert len(post.calls) == 2  # exactly the two posts we triggered


# --------------------------------------------------------------------------- #
# Missing token / url -> ok=False
# --------------------------------------------------------------------------- #
def test_telegram_missing_token_fails():
    post = RecordingPost()
    ch = TelegramChannel(
        {"token_secret": "tg_token", "chat_id": 1},
        http_post=post,
        secret_resolver=lambda name: None,  # secret not found
    )
    res = ch.send("x")
    assert res["ok"] is False
    assert "did not resolve" in res["detail"]
    assert post.calls == []  # never attempted a post


def test_slack_missing_config_fails():
    res = SlackChannel({}, http_post=RecordingPost()).send("x")
    assert res["ok"] is False
    assert "webhook_url" in res["detail"]


def test_discord_missing_webhook_fails():
    res = DiscordChannel({}, http_post=RecordingPost()).send("x")
    assert res["ok"] is False


def test_http_error_status_yields_not_ok():
    post = RecordingPost(response={"status_code": 500, "text": "boom"})
    res = SlackChannel({"webhook_url": "u"}, http_post=post).send("x")
    assert res["ok"] is False
    assert "500" in res["detail"]


# --------------------------------------------------------------------------- #
# NotifyTool via the registry
# --------------------------------------------------------------------------- #
@pytest.fixture
def ctx(platform, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    return ToolContext(
        workspace=ws,
        session_id="s1",
        agent_run_id="r1",
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )


async def test_notify_tool_sends_through_notifier(platform, ctx):
    mock = MockChannel()
    notifier = Notifier()
    notifier.add_channel("mock", mock)

    for tool in notify_tools(notifier):
        platform.registry.register(tool)

    res = await platform.registry.invoke(
        "notify",
        {"message": "from agent"},
        ctx,
        platform.permissions,
        agent_overrides={"notify": "allow"},
    )
    assert res.ok
    assert mock.sent == ["from agent"]


async def test_notify_tool_requires_message(platform, ctx):
    notifier = Notifier()
    notifier.add_channel("mock", MockChannel())
    platform.registry.register(NotifyTool(notifier))
    res = await platform.registry.invoke(
        "notify", {"message": ""}, ctx, platform.permissions,
        agent_overrides={"notify": "allow"},
    )
    assert not res.ok


# --------------------------------------------------------------------------- #
# Notifier.on_event — formats + sends on match, ignores non-match
# --------------------------------------------------------------------------- #
def test_on_event_alerts_on_matching_type():
    mock = MockChannel()
    notifier = Notifier(event_types={EventType.REVIEW_REQUESTED})
    notifier.add_channel("mock", mock)

    event = Event(type=EventType.REVIEW_REQUESTED, payload={"session": "s9"}, session_id="s9")
    results = notifier.on_event(event)

    assert results is not None and results["mock"]["ok"]
    assert len(mock.sent) == 1
    assert EventType.REVIEW_REQUESTED in mock.sent[0]
    assert "session=s9" in mock.sent[0]


def test_on_event_ignores_non_matching_type():
    mock = MockChannel()
    notifier = Notifier(event_types={EventType.REVIEW_REQUESTED})
    notifier.add_channel("mock", mock)

    ignored = notifier.on_event(Event(type=EventType.TOOL_EXECUTED, payload={"tool": "grep"}))
    assert ignored is None
    assert mock.sent == []


async def test_on_event_attaches_to_event_bus(platform):
    mock = MockChannel()
    notifier = Notifier(event_types={EventType.PROVIDER_FAILED})
    notifier.add_channel("mock", mock)
    platform.event_bus.add_handler(notifier.on_event)

    await platform.event_bus.publish(EventType.PROVIDER_FAILED, {"provider": "mock"})
    await platform.event_bus.publish(EventType.TOOL_EXECUTED, {"tool": "grep"})

    assert len(mock.sent) == 1
    assert "provider=mock" in mock.sent[0]


# --------------------------------------------------------------------------- #
# Config-driven construction + integration specs
# --------------------------------------------------------------------------- #
def test_build_notifier_from_config_offline():
    post = RecordingPost()
    cfg = {
        "default_channel": "slack",
        "channels": {
            "slack": {"type": "slack", "webhook_url": "https://hooks/X"},
            "tg": {"type": "telegram", "token_secret": "t", "chat_id": 7},
        },
    }
    notifier = build_notifier(
        cfg, secret_resolver=lambda n: "TOK", http_post=post
    )
    assert notifier.channels() == ["slack", "tg"]
    notifier.notify("hi", channels=["slack"])
    assert post.last[0] == "https://hooks/X"


def test_build_notifier_falls_back_to_mock():
    notifier = build_notifier(None)
    assert notifier.channels() == ["mock"]
    assert isinstance(notifier.get("mock"), MockChannel)


def test_channel_integrations_cover_all_types():
    names = {spec.name for spec in channel_integrations()}
    assert {"slack", "discord", "telegram", "mock", "console"} <= names
    for spec in channel_integrations():
        assert spec.kind == "communication"


def test_console_channel_is_ok(capsys):
    res = ConsoleChannel().send("hey")
    assert res["ok"] is True
    assert "hey" in capsys.readouterr().out
