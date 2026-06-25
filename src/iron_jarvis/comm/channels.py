"""Concrete communication channels.

Every channel builds its own target URL + JSON payload and delegates the POST to
the injected ``http_post`` callable (see :mod:`.base`), so no real network is
touched in tests. Missing token / url / chat-id yields ``ok=False`` with a clear
``detail`` rather than raising.

``MockChannel`` is the offline default — it records every message in ``.sent``.
"""

from __future__ import annotations

from typing import Any

from ..core.logging import get_logger
from .base import Channel

_log = get_logger("comm")

SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
TELEGRAM_API = "https://api.telegram.org"


class SlackChannel(Channel):
    """Slack via incoming webhook *or* ``chat.postMessage`` with a bot token.

    config:
      * ``{"webhook_url": "..."}`` — posts ``{"text": message}`` to the webhook, or
      * ``{"token_secret": "...", "channel": "#general"}`` — resolves the bot
        token by name and calls ``chat.postMessage`` (token carried in payload so
        it works with the (url, json)-only transport contract).
    """

    name = "slack"

    def send(self, message: str, **kw: Any) -> dict[str, Any]:
        webhook = self.config.get("webhook_url")
        if webhook:
            return self._post(webhook, {"text": message})

        token_secret = self.config.get("token_secret")
        if token_secret:
            token = self._resolve_secret(token_secret)
            if not token:
                return self._fail(f"slack: token secret '{token_secret}' did not resolve")
            channel = kw.get("channel") or self.config.get("channel")
            if not channel:
                return self._fail("slack: chat.postMessage requires a `channel`")
            payload = {"channel": channel, "text": message, "token": token}
            return self._post(SLACK_POST_MESSAGE_URL, payload)

        return self._fail("slack: config needs `webhook_url` or `token_secret`+`channel`")


class DiscordChannel(Channel):
    """Discord via incoming webhook. config: ``{"webhook_url": "..."}``."""

    name = "discord"

    def send(self, message: str, **kw: Any) -> dict[str, Any]:
        webhook = self.config.get("webhook_url")
        if not webhook:
            return self._fail("discord: config needs `webhook_url`")
        return self._post(webhook, {"content": message})


class TelegramChannel(Channel):
    """Telegram Bot API ``sendMessage``.

    config: ``{"token_secret": "...", "chat_id": 123456}``.
    """

    name = "telegram"

    def send(self, message: str, **kw: Any) -> dict[str, Any]:
        token_secret = self.config.get("token_secret")
        token = self._resolve_secret(token_secret)
        if not token:
            return self._fail(
                f"telegram: token secret '{token_secret}' did not resolve"
                if token_secret
                else "telegram: config needs `token_secret`"
            )
        chat_id = kw.get("chat_id") or self.config.get("chat_id")
        if not chat_id:
            return self._fail("telegram: config needs `chat_id`")
        url = f"{TELEGRAM_API}/bot{token}/sendMessage"
        return self._post(url, {"chat_id": chat_id, "text": message})


class MockChannel(Channel):
    """Offline default — records every sent message in :attr:`sent`; always ok."""

    name = "mock"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.sent: list[str] = []

    def send(self, message: str, **kw: Any) -> dict[str, Any]:
        self.sent.append(message)
        return {"ok": True, "detail": f"recorded ({len(self.sent)})"}


class ConsoleChannel(Channel):
    """Logs/prints the message locally; always ok. Useful as a safe fallback."""

    name = "console"

    def send(self, message: str, **kw: Any) -> dict[str, Any]:
        line = f"[iron-jarvis] {message}"
        _log.info("console notify: %s", message)
        print(line)
        return {"ok": True, "detail": "printed"}


#: registry of channel-type name -> class, for config-driven construction.
CHANNEL_TYPES: dict[str, type[Channel]] = {
    cls.name: cls
    for cls in (
        SlackChannel,
        DiscordChannel,
        TelegramChannel,
        MockChannel,
        ConsoleChannel,
    )
}
