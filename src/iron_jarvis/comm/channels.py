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
    #: An inbound Slack DM fires "slack" reflex rules (CX-05), so a rule can scope
    #: to "a Slack message arrived" distinctly from a generic "comm" message.
    reflex_source = "slack"

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


def _imap_connect(host: str, port: int) -> Any:
    """Open an ``IMAP4_SSL`` connection to ``host:port`` (the inbound seam).

    A module-level function so a test can monkeypatch it with a fake and drive
    :meth:`EmailChannel.poll` without a real server. ``imaplib``/``ssl`` are
    imported lazily here (mirroring :meth:`EmailChannel.send`'s lazy ``smtplib``)
    so the comm package still imports where they're unavailable.
    """
    import imaplib
    import ssl

    return imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context())


def _decode_mime_header(value: str) -> str:
    """Decode an RFC 2047 encoded header (``=?utf-8?..?=``) to plain text."""
    if not value:
        return ""
    from email.header import decode_header, make_header

    try:
        return str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001 — a malformed header must not break polling
        return value


def _decode_part_text(part: Any) -> str:
    """Decode a single MIME part's payload to text using its declared charset."""
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, TypeError):  # unknown/invalid charset -> best-effort utf-8
        return payload.decode("utf-8", errors="replace")


# Cap any body before parsing/stripping — a hostile sender (From is spoofable)
# could otherwise email a huge HTML blob and peg the inbound-poll thread. The
# reflex matcher only looks at the first ~2 KB anyway, so 100 KB is generous.
_MAX_EMAIL_BODY_CHARS = 100_000


def _strip_html(raw_html: str) -> str:
    """LINEAR tag-strip for a text/html-only body (stdlib html.parser — no regex
    backtracking, so a malformed/adversarial body can't cause quadratic blow-up).
    Skips script/style content and collapses whitespace."""
    import re
    from html.parser import HTMLParser

    class _Extract(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)  # unescape entities for us
            self.parts: list[str] = []
            self._skip = 0

        def handle_starttag(self, tag: str, attrs: Any) -> None:
            if tag in ("script", "style"):
                self._skip += 1

        def handle_endtag(self, tag: str) -> None:
            if tag in ("script", "style") and self._skip:
                self._skip -= 1

        def handle_data(self, data: str) -> None:
            if not self._skip:
                self.parts.append(data)

    parser = _Extract()
    try:
        parser.feed(raw_html)
    except Exception:  # noqa: BLE001 — malformed HTML must never raise out of a poll
        pass
    return re.sub(r"\s+", " ", "".join(parser.parts)).strip()


def _email_body(em: Any) -> str:
    """Best-effort readable body: prefer ``text/plain``, fall back to stripped
    HTML. Both are size-capped before any parsing (DoS guard)."""
    plain: str | None = None
    html_body: str | None = None
    if em.is_multipart():
        for part in em.walk():
            if part.is_multipart():
                continue
            disp = str(part.get("Content-Disposition") or "").lower()
            if "attachment" in disp:
                continue
            ctype = part.get_content_type()
            if ctype == "text/plain" and plain is None:
                plain = _decode_part_text(part)
            elif ctype == "text/html" and html_body is None:
                html_body = _decode_part_text(part)
    elif em.get_content_type() == "text/html":
        html_body = _decode_part_text(em)
    else:
        plain = _decode_part_text(em)
    if plain:
        return plain[:_MAX_EMAIL_BODY_CHARS].strip()
    if html_body:
        return _strip_html(html_body[:_MAX_EMAIL_BODY_CHARS])
    return ""


class EmailChannel(Channel):
    """Email via SMTP (outbound) + IMAP (inbound trigger, CX-05).

    config: ``{"host": "smtp.gmail.com", "port": 587, "username": "...",
    "password_secret": "...", "from_addr": "...", "to_addr": "...",
    "use_tls": true, "subject": "...", "imap_host": "imap.gmail.com",
    "imap_port": 993, "mailbox": "INBOX", "inbound_enabled": true,
    "allowed_senders": ["boss@acme.com"]}``. The password is resolved from the
    vault by name (never stored in config). ``smtplib``/``imaplib`` are imported
    lazily (in :meth:`send` / the :func:`_imap_connect` seam) so the comm package
    still imports where they're unavailable and tests never touch the network.

    Inbound turns a mailbox into a first-class Reflex trigger: an inbound email
    fires ``"email"`` rules (:attr:`reflex_source`). It is OFF unless
    ``inbound_enabled`` is set, and the From-address allowlist
    (``allowed_senders``) is the fail-closed security boundary — an empty list
    authorizes nobody.
    """

    name = "email"
    #: IMAP gives email a receive leg; the inbound pipeline still gates on
    #: inbound_enabled + the From-address allowlist before anything runs.
    supports_inbound = True
    #: An inbound email fires "email" reflex rules (distinct from generic "comm").
    reflex_source = "email"

    def is_authorized(self, sender_id: Any) -> bool:
        """FAIL-CLOSED From-address allowlist, matched case-insensitively (email
        addresses are effectively case-insensitive, so ``Boss@Acme.com`` in the
        list must still authorize a ``boss@acme.com`` From). Empty list = nobody."""
        allow = {str(a).strip().lower() for a in self.allowed_senders()}
        return bool(allow) and str(sender_id).strip().lower() in allow

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

    def poll(
        self, offset: int = 0, *, timeout: int = 0
    ) -> tuple[list[InboundMessage], int]:
        """Fetch new inbox mail since ``offset`` (an IMAP UID) via IMAP4_SSL.

        Searches for UIDs greater than ``offset`` (``UID {offset+1}:*``), parses
        each message's sender / subject / text body, and returns
        ``(messages sorted by UID asc, max_uid + 1)`` — or ``([], offset)`` when
        there is nothing new. Each message carries ``update_id = UID`` (so the
        durable offset dedupes across restarts) and ``reply_to = sender`` (so the
        poller's private-chat guard passes and a reply reaches the sender via
        :meth:`send`). Loop protection is the From-address allowlist upstream.

        ``imaplib``/``email`` are imported lazily and the whole pass is wrapped:
        on ANY error it returns ``([], offset)`` (mirrors :meth:`Channel.poll`),
        so a transport/parse failure yields no messages and no offset advance.
        """
        cfg = self.config
        username = cfg.get("username")
        password = self._resolve_secret(cfg.get("password_secret"))
        host = cfg.get("imap_host") or cfg.get("host")
        if not username or not password or not host:
            return [], offset
        mailbox = cfg.get("mailbox") or "INBOX"

        conn = None
        try:
            import email
            from email.utils import parseaddr

            # Parse the port INSIDE the guard so a non-numeric `imap_port` config
            # value degrades to no-messages (like any other error) instead of
            # raising out of poll().
            port = int(cfg.get("imap_port") or 993)
            conn = _imap_connect(host, port)
            conn.login(username, password)
            conn.select(mailbox)
            typ, search_data = conn.uid("SEARCH", None, f"UID {offset + 1}:*")
            if typ != "OK":
                return [], offset
            raw_ids = (search_data[0] or b"").split() if search_data else []
            # `UID n:*` clamps to the highest UID when n exceeds it, so filter
            # to strictly-new UIDs (> offset) — otherwise we'd refetch the last.
            uids = sorted({int(x) for x in raw_ids if x.isdigit()})
            uids = [u for u in uids if u > offset]

            messages: list[InboundMessage] = []
            for uid in uids:
                typ, fetch_data = conn.uid("FETCH", str(uid), "(RFC822)")
                if typ != "OK" or not fetch_data:
                    continue
                raw = next(
                    (p[1] for p in fetch_data if isinstance(p, tuple) and len(p) >= 2),
                    None,
                )
                if not raw:
                    continue
                em = email.message_from_bytes(raw)
                sender = parseaddr(em.get("From", ""))[1]
                subject = _decode_mime_header(em.get("Subject", ""))
                body = _email_body(em)
                messages.append(
                    InboundMessage(
                        sender_id=sender,
                        text=subject + "\n" + body,
                        update_id=int(uid),
                        reply_to=sender,
                        is_bot=False,
                        raw={"subject": subject, "from": sender},
                    )
                )
            next_offset = (
                max(int(m.update_id) for m in messages) + 1 if messages else offset
            )
            return messages, next_offset
        except Exception:  # noqa: BLE001 — a poll must never raise to the poller
            return [], offset
        finally:
            if conn is not None:
                try:
                    conn.logout()
                except Exception:  # noqa: BLE001 — best-effort close
                    pass

    def has_credentials(self) -> bool:
        """True only when the vault holds the IMAP password, so an inbound email
        channel with no password is never polled. (The base checks ``token_secret``;
        email keys its secret under ``password_secret``.)"""
        return bool(self._resolve_secret(self.config.get("password_secret")))


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
