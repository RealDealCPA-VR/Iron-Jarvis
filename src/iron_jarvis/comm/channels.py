"""Concrete communication channels.

Every channel builds its own target URL + JSON payload and delegates the POST to
the injected ``http_post`` callable (see :mod:`.base`), so no real network is
touched in tests. Missing token / url / chat-id yields ``ok=False`` with a clear
``detail`` rather than raising.

``MockChannel`` is the offline default — it records every message in ``.sent``.
"""

from __future__ import annotations

import re
from typing import Any

from ..core.logging import get_logger
from .base import Channel, ChannelAuthError, InboundMessage, auth_refusal, interpret_json

_log = get_logger("comm")

SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
TELEGRAM_API = "https://api.telegram.org"


class SlackChannel(Channel):
    """Slack via incoming webhook *or* ``chat.postMessage`` with a bot token.

    config:
      * ``{"webhook_url": "..."}`` — posts ``{"text": message}`` to the webhook, or
      * ``{"token_secret": "...", "channel": "#general"}`` — resolves the bot
        token by name and calls ``chat.postMessage`` with the token in an
        ``Authorization: Bearer`` HEADER. Slack's Web API does not accept a
        token as a JSON body field: it answers HTTP **200** with
        ``{"ok": false, "error": "not_authed"}``, so a body token means every
        send is silently dropped while the status code says delivered.
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
            thread_ts = None
            if isinstance(channel, str) and "|" in channel:
                # v1.444.0: "<channel id>|<thread ts>" is the Socket Mode pump's
                # reply address for a message written in the destination's own
                # channel — the answer goes in a thread under that message.
                channel, _, thread_ts = channel.partition("|")
            payload: dict[str, Any] = {"channel": channel, "text": message}
            if thread_ts:
                payload["thread_ts"] = thread_ts
            # The token rides the Authorization header (never the JSON body),
            # and `interpret_response` reads Slack's {"ok": false, "error": …}
            # envelope so a 200-with-error is reported as the failure it is.
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            }
            return self._post(SLACK_POST_MESSAGE_URL, payload, headers)

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
    #: Telegram's hard sendMessage cap — chunked replies fill the full window.
    chunk_limit = 4096

    #: Transport notice for content the text-only bridge cannot carry
    #: (v1.200.0, CONNECT-AUDIT-2026-08-22 §7). Sent at most once per sender
    #: per poll batch, allowlisted senders only.
    MEDIA_NOTICE = (
        "I can't read photos, files, or voice messages here yet — send text, "
        "or use the desktop app for attachments."
    )
    #: Message keys that mean "the sender tried to hand over content" (as
    #: opposed to service updates — joins, pins, title changes — where a
    #: notice would be noise about nothing the sender said).
    _MEDIA_KEYS = (
        "photo",
        "document",
        "voice",
        "audio",
        "video",
        "video_note",
        "sticker",
        "animation",
        "contact",
        "location",
    )

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
        ``max(update_id) + 1``; on any failure returns ``([], offset)`` —
        EXCEPT a refused token: a 401/403 (revoked or rotated bot token)
        raises :class:`ChannelAuthError`, because an empty batch and a dead
        phone must not look the same to the loop (v1.231.0, audit AE8).
        """
        token = self._resolve_secret(self.config.get("token_secret"))
        if not token:
            return [], offset
        url = f"{TELEGRAM_API}/bot{token}/getUpdates"
        params: dict[str, Any] = {"timeout": timeout}
        if offset:
            params["offset"] = offset
        resp = self._get_raw(url, params)
        refused = auth_refusal(resp)
        if refused:
            raise ChannelAuthError(
                f"telegram: getUpdates refused ({refused}) — the bot token was "
                "revoked or rotated; paste the current token from @BotFather"
            )
        data = interpret_json(resp)
        if not data or not data.get("ok"):
            return [], offset

        messages: list[InboundMessage] = []
        next_offset = offset
        noticed: set[str] = set()  # senders already told about dropped media
        for upd in data.get("result", []) or []:
            update_id = upd.get("update_id")
            if isinstance(update_id, int):
                next_offset = max(next_offset, update_id + 1)
            msg = upd.get("message") or upd.get("edited_message") or {}
            text = msg.get("text")
            if not text:
                # Non-text updates are still DROPPED (the brain is text-only;
                # a recorded decision) — but silence was not: the offset
                # advances past them, so a photo from the owner vanished with
                # no reply and no thread entry (v1.200.0). A media message
                # from an ALLOWED sender now gets one honest notice first.
                self._media_notice(msg, noticed)
                continue
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

    def _media_notice(self, msg: Any, noticed: set[str]) -> None:
        """ONE honest reply for a media message the poll is about to drop.

        FAIL-CLOSED POSTURE PRESERVED: only an ALLOWLISTED sender, in their
        own PRIVATE chat, hears anything — strangers and groups still get
        silence, and a bot's message never triggers it (loop protection).

        DELIBERATELY NO CHAT-THREAD ENTRY: this is a transport notice about
        what the channel can carry, not a conversation turn — the update
        never reaches the inbound pipeline, so nothing lands on the desktop
        thread, and a courtesy line masquerading as dialogue would pollute
        the conversation record both sides read back.

        Best-effort by design: a malformed update or a failed send is
        swallowed (logged), because the poll loop must never crash or stall
        over a courtesy message — same discipline as the rest of the module.
        """
        try:
            if not isinstance(msg, dict) or not any(
                k in msg for k in self._MEDIA_KEYS
            ):
                return  # service update (join, pin, ...) — nothing was said
            frm = msg.get("from") or {}
            chat = msg.get("chat") or {}
            if frm.get("is_bot"):
                return
            sender = str(frm.get("id", "") or "")
            chat_id = chat.get("id")
            if not sender or chat_id is None or str(chat_id) != sender:
                return  # group/channel media: replying would broadcast
            if not self.is_authorized(sender):
                return  # strangers still get silence (fail-closed)
            if sender in noticed:
                return  # one notice per sender per batch, not one per photo
            noticed.add(sender)
            self.send(self.MEDIA_NOTICE, chat_id=chat_id)
        except Exception:  # noqa: BLE001 — never break the poll over a notice
            _log.warning("telegram media notice failed", exc_info=True)


class MockChannel(Channel):
    """Offline default — records every sent message in :attr:`sent`; always ok."""

    name = "mock"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.sent: list[str] = []

    def send(self, message: str, **kw: Any) -> dict[str, Any]:
        self.sent.append(message)
        return {"ok": True, "detail": f"recorded ({len(self.sent)})"}


class DesktopChannel(Channel):
    """"This PC" (v1.118.0) — a destination that needs ZERO setup.

    ``send`` hands the message to a process-wide sink the daemon wires at
    startup (an event-bus publish scheduled thread-safely onto the loop); the
    dashboard's bridge component turns the resulting ``comm.desktop`` event
    into a native OS notification via the Electron preload — WHILE A WINDOW
    EXISTS. Minimised, the window keeps its renderer and the toast shows.
    Closed to the TRAY, the renderer is destroyed (``hideToTray`` in
    desktop/main.js), so a ``comm.desktop`` message sent then raises NO toast:
    it is only recorded in the event log. (Corrected v1.351.0, G03 — this
    docstring used to promise the opposite.) What still reaches a user with no
    window: the desktop app's own ``installAskWatcher`` toasts asks that wait
    on them AND (v1.360.0, lane 10A) unattended runs that failed or fell
    short, in the bell's words (``planRunOutcomeNotifications``), and the bell
    replays the finished and failed unattended runs it missed
    (``session.completed`` and the rest, via ``GET /audit``) the next time a
    window opens. For exactly the run outcomes the bell rings this channel is
    QUIET (``notifier.bell_pings``): one notice per outcome on this PC.

    The sink is a class attribute rather than a constructor arg on purpose:
    channels are built in two places (boot + the add-channel route) through a
    uniform constructor, and threading a loop handle through both would couple
    comm to the daemon. Unset sink (CLI runs, unit tests) fails HONESTLY —
    "configured" must never silently mean "sent nowhere".
    """

    name = "desktop"

    #: set by the daemon lifespan: Callable[[str title, str message], None].
    sink = None  # type: ignore[var-annotated]

    def send(self, message: str, **kw: Any) -> dict[str, Any]:
        sink = type(self).sink
        if sink is None:
            return {
                "ok": False,
                "detail": "desktop notifications need the running app (Iron Jarvis is not running)",
            }
        try:
            sink(str(kw.get("title") or "Iron Jarvis"), message)
        except Exception as exc:  # noqa: BLE001 — a bad sink must not break fan-out
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, "detail": "notification shown on this device"}


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
    elif em.get_content_maintype() == "text":
        plain = _decode_part_text(em)
    # R0.3: an e-mail that is ONE file and nothing else (a single-part PDF or
    # photo) has no words; its bytes are never read as the sender's words.
    # ``_email_file_count`` counts it, and the reply says it was not used.
    if plain:
        return plain[:_MAX_EMAIL_BODY_CHARS].strip()
    if html_body:
        return _strip_html(html_body[:_MAX_EMAIL_BODY_CHARS])
    return ""


#: R0.3: how many MIME parts the file count looks at before it stops (a
#: hostile sender's message could hold thousands; the count is a courtesy).
_MAX_EMAIL_PARTS = 500
#: The body's own text parts: never a file, whatever name they carry
#: (``_email_body`` may read one as the sender's words).
_BODY_TEXT_TYPES = ("text/plain", "text/html")


def _email_file_count(em: Any) -> int:
    """How many files the sender attached (R0.3). Iron Jarvis cannot take a
    file sent by e-mail yet; this number is what the reply says it left out.

    A part is a file when it is marked as an attachment, or when it has a file
    name, is not the body's text, and is not a picture INSIDE the body (a part
    of ``multipart/related``, such as a signature logo the HTML shows). Apple
    Mail marks an attached PDF ``inline`` with a name, so the name counts. An
    attached e-mail is one file; what it carries is not counted again. A
    message that is itself one file (a single non-text part) is one file.
    Never raises: an unreadable message counts what was read before it.
    """
    count = 0
    seen = 0
    stack: list[tuple[Any, bool]] = [(em, False)]
    try:
        while stack and seen < _MAX_EMAIL_PARTS:
            part, in_body = stack.pop()
            seen += 1
            ctype = part.get_content_type()
            if part is em and not part.is_multipart():
                return 1 if part.get_content_maintype() != "text" else 0
            if part is not em:
                disp = (part.get_content_disposition() or "").lower()
                named = bool(part.get_filename())
                if disp == "attachment" or (
                    named and not in_body and ctype not in _BODY_TEXT_TYPES
                ):
                    count += 1
                    continue
            if part.is_multipart():
                subs = part.get_payload()
                if isinstance(subs, list):
                    related = in_body or ctype == "multipart/related"
                    stack.extend((s, related) for s in reversed(subs))
    except Exception:  # noqa: BLE001 — a malformed message must never break a poll
        _log.debug("email: could not count the attachments", exc_info=True)
    return count


#: The sender id of an inbound email whose From address the receiving provider
#: did not vouch for (v1.499.3). It never matches an allowlist entry.
UNVERIFIED_PREFIX = "unverified:"

#: Senders already logged as unproven (once per address per run, bounded).
_UNPROVEN_LOGGED: set[str] = set()


def _log_unproven_once(sender: str, how: str) -> None:
    key = (sender or "").lower()
    if key in _UNPROVEN_LOGGED:
        return
    if len(_UNPROVEN_LOGGED) < 1000:
        _UNPROVEN_LOGGED.add(key)
    _log.warning(
        "email: a message claiming to be from %r was not acted on: the mail "
        "provider did not confirm the sender (%s)",
        sender,
        how,
    )


def _domain_of(value: str) -> str:
    v = (value or "").strip().strip("<>").lower().rstrip(".")
    return v.rsplit("@", 1)[-1] if "@" in v else v


def _aligned(domain: str, from_domain: str) -> bool:
    """Relaxed alignment: the same domain, or one a sub-domain of the other
    (never a bare top-level name such as ``com``)."""
    if not domain or not from_domain or "." not in domain:
        return False
    return (
        domain == from_domain
        or from_domain.endswith("." + domain)
        or domain.endswith("." + from_domain)
    )


_RESULT_RE = re.compile(r"^\s*([a-z0-9-]+)\s*=\s*([a-z]+)", re.IGNORECASE)
_PROP_RE = re.compile(r"([a-z0-9-]+\.[a-z0-9-]+)\s*=\s*([^\s;]+)", re.IGNORECASE)


def _auth_results(header: str) -> list[tuple[str, str, dict[str, str]]]:
    """``[(method, result, {property: value})]`` from one Authentication-Results
    header (RFC 8601; Microsoft's form without an authserv-id is read too)."""
    text = re.sub(r"\([^()]*\)", " ", header or "")  # drop comments
    out: list[tuple[str, str, dict[str, str]]] = []
    for part in text.split(";"):
        m = _RESULT_RE.match(part)
        if not m:
            continue  # the authserv-id, or noise
        props = {k.lower(): v.strip('"') for k, v in _PROP_RE.findall(part[m.end():])}
        out.append((m.group(1).lower(), m.group(2).lower(), props))
    return out


def sender_proof(em: Any, sender: str) -> tuple[bool, str]:
    """Whether the receiving provider vouched for the From address.

    Only the TOPMOST Authentication-Results header counts (the receiving
    provider adds it above anything the sender wrote). Proof is dkim=pass with
    ``header.d`` / ``header.i`` aligned to the From domain, dmarc=pass (for that
    From domain), or spf=pass with ``smtp.mailfrom`` aligned. Returns
    ``(proven, words)``; never raises.
    """
    try:
        from_domain = _domain_of(sender)
        if not from_domain:
            return False, "the message has no From address"
        headers = em.get_all("Authentication-Results") or []
        if not headers:
            return False, "the message carries no Authentication-Results"
        results = _auth_results(str(headers[0]))
        for method, result, props in results:
            if result != "pass":
                continue
            if method == "dkim":
                signer = props.get("header.d") or _domain_of(props.get("header.i", ""))
                if _aligned(_domain_of(signer), from_domain):
                    return True, "dkim"
            elif method == "dmarc":
                said = _domain_of(props.get("header.from", ""))
                if not said or said == from_domain:
                    return True, "dmarc"
            elif method == "spf":
                if _aligned(_domain_of(props.get("smtp.mailfrom", "")), from_domain):
                    return True, "spf"
        return False, "no aligned dkim, dmarc or spf pass"
    except Exception:  # noqa: BLE001 — unreadable proof is no proof
        return False, "the proof could not be read"


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
        list must still authorize a ``boss@acme.com`` From). Empty list = nobody.
        A sender :meth:`poll` could not prove (``unverified:…``) is nobody too."""
        sid = str(sender_id).strip().lower()
        if sid.startswith(UNVERIFIED_PREFIX):
            return False
        allow = {str(a).strip().lower() for a in self.allowed_senders()}
        return bool(allow) and sid in allow

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
        so a transport/parse failure yields no messages and no offset advance —
        except a refused LOGIN, which raises :class:`ChannelAuthError` (AE8).
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
            try:
                conn.login(username, password)
            except Exception as exc:  # noqa: BLE001 — imaplib.IMAP4.error, mostly
                # A refused LOGIN is a dead credential, not an empty inbox
                # (v1.231.0, audit AE8): raise so the poller records it and
                # the loop stops reporting a healthy poll.
                raise ChannelAuthError(
                    f"email: IMAP login refused for {username} at {host}: "
                    f"{str(exc)[:200] or type(exc).__name__}"
                ) from exc
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
                # v1.499.3: the From header alone is the sender's CLAIM; the
                # receiving provider's proof decides whether it is the sender.
                verified, how = sender_proof(em, sender)
                if not verified:
                    _log_unproven_once(sender, how)
                messages.append(
                    InboundMessage(
                        # An unproven sender never matches the allowlist, so it
                        # gets no commands, approvals, rules or sessions.
                        sender_id=sender if verified else UNVERIFIED_PREFIX + sender,
                        text=subject + "\n" + body,
                        update_id=int(uid),
                        reply_to=sender,
                        is_bot=False,
                        raw={
                            "subject": subject,
                            "from": sender,
                            "sender_verified": verified,
                        },
                        # R0.3: the attachments are still not read; the
                        # pipeline tells a proven, allowed sender so.
                        files_not_read=_email_file_count(em),
                    )
                )
            next_offset = (
                max(int(m.update_id) for m in messages) + 1 if messages else offset
            )
            return messages, next_offset
        except ChannelAuthError:
            raise
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
        DesktopChannel,
        MockChannel,
        ConsoleChannel,
    )
}
