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
from .base import Channel, InboundMessage

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
    #: Socket Mode gives Slack a receive leg (outbound WebSocket — no public
    #: URL needed); the inbound pipeline gates on inbound_enabled + allowlist.
    supports_inbound = True

    def send(self, message: str, **kw: Any) -> dict[str, Any]:
        # `chat_id` is the inbound pipeline's reply address (a Slack user id —
        # chat.postMessage with channel=U… delivers to that user's DM). When
        # present, prefer the token path so the reply reaches the SENDER
        # instead of the configured broadcast target.
        reply_target = kw.get("chat_id")
        token_secret = self.config.get("token_secret")
        if token_secret and (reply_target or not self.config.get("webhook_url")):
            token = self._resolve_secret(token_secret)
            if not token:
                return self._fail(f"slack: token secret '{token_secret}' did not resolve")
            channel = reply_target or kw.get("channel") or self.config.get("channel")
            if not channel:
                return self._fail("slack: chat.postMessage requires a `channel`")
            payload = {"channel": channel, "text": message, "token": token}
            return self._post(SLACK_POST_MESSAGE_URL, payload)

        webhook = self.config.get("webhook_url")
        if webhook:
            return self._post(webhook, {"text": message})

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
    """Telegram Bot API ``sendMessage`` (outbound) + ``getUpdates`` (inbound).

    config: ``{"token_secret": "...", "chat_id": 123456}`` plus the optional
    two-way fields ``inbound_enabled`` (bool) and ``allowed_senders`` (list of
    Telegram user/chat ids). Inbound is OFF unless ``inbound_enabled`` is set.
    """

    name = "telegram"
    supports_inbound = True

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

    def poll(
        self, offset: int = 0, *, timeout: int = 0
    ) -> tuple[list[InboundMessage], int]:
        """Long-poll ``getUpdates`` and parse text messages.

        Passing ``offset`` confirms (and so DROPS server-side) every update with
        a lower id, which is what makes the durable offset dedupe across
        restarts. Returns ``(messages, next_offset)`` where ``next_offset`` is
        ``max(update_id) + 1``; on any failure returns ``([], offset)``.
        """
        token = self._resolve_secret(self.config.get("token_secret"))
        if not token:
            return [], offset
        url = f"{TELEGRAM_API}/bot{token}/getUpdates"
        params: dict[str, Any] = {"timeout": timeout}
        if offset:
            params["offset"] = offset
        data = self._get_json(url, params)
        if not data or not data.get("ok"):
            return [], offset

        messages: list[InboundMessage] = []
        next_offset = offset
        for upd in data.get("result", []) or []:
            update_id = upd.get("update_id")
            if isinstance(update_id, int):
                next_offset = max(next_offset, update_id + 1)
            msg = upd.get("message") or upd.get("edited_message") or {}
            text = msg.get("text")
            if not text:
                continue  # ignore non-text updates (photos, joins, ...)
            frm = msg.get("from") or {}
            chat = msg.get("chat") or {}
            messages.append(
                InboundMessage(
                    sender_id=str(frm.get("id", "")),
                    text=text,
                    update_id=update_id,
                    reply_to=chat.get("id"),
                    is_bot=bool(frm.get("is_bot", False)),
                    raw=upd,
                )
            )
        return messages, next_offset


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


class EmailChannel(Channel):
    """Email via SMTP.

    config: ``{"host": "smtp.gmail.com", "port": 587, "username": "...",
    "password_secret": "...", "from_addr": "...", "to_addr": "...",
    "use_tls": true, "subject": "..."}``. The password is resolved from the vault
    by name (never stored in config). smtplib is imported lazily inside
    :meth:`send` so the comm package still imports where it's unavailable and
    tests that don't send never touch the network.
    """

    name = "email"

    def send(self, message: str, **kw: Any) -> dict[str, Any]:
        cfg = self.config
        host = cfg.get("host")
        from_addr = cfg.get("from_addr") or cfg.get("username")
        to_addr = kw.get("to") or cfg.get("to_addr")
        if not host or not from_addr or not to_addr:
            return self._fail("email: config needs `host`, `from_addr` and `to_addr`")
        password = self._resolve_secret(cfg.get("password_secret"))
        try:
            import smtplib
            from email.message import EmailMessage

            msg = EmailMessage()
            msg["Subject"] = kw.get("subject") or cfg.get("subject") or "Iron Jarvis"
            msg["From"] = from_addr
            msg["To"] = to_addr
            msg.set_content(message)
            port = int(cfg.get("port") or 587)
            with smtplib.SMTP(host, port, timeout=15) as smtp:
                if cfg.get("use_tls", True):
                    smtp.starttls()
                if cfg.get("username") and password:
                    smtp.login(cfg["username"], password)
                smtp.send_message(msg)
            return {"ok": True, "detail": f"emailed {to_addr}"}
        except Exception as exc:  # noqa: BLE001 — surface, never raise to the notifier
            return self._fail(f"email: {type(exc).__name__}: {exc}")


#: registry of channel-type name -> class, for config-driven construction.
CHANNEL_TYPES: dict[str, type[Channel]] = {
    cls.name: cls
    for cls in (
        SlackChannel,
        DiscordChannel,
        TelegramChannel,
        EmailChannel,
        MockChannel,
        ConsoleChannel,
    )
}
