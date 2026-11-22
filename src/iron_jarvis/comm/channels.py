"""Concrete communication channels.

Every channel builds its own target URL + JSON payload and delegates the POST to
the injected ``http_post`` callable (see :mod:`.base`), so no real network is
touched in tests. Missing token / url / chat-id yields ``ok=False`` with a clear
``detail`` rather than raising.

``MockChannel`` is the offline default — it records every message in ``.sent``.
"""

from __future__ import annotations

import json
import mimetypes
import re
from pathlib import Path
from typing import Any

from ..core.logging import get_logger
from .base import (
    Channel,
    ChannelAuthError,
    FileNotTaken,
    FileTooLarge,
    InboundFile,
    InboundMessage,
    Press,
    auth_refusal,
    interpret_json,
    interpret_response,
    too_large_words,
    without_signoff,
)

_log = get_logger("comm")

SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
SLACK_API = "https://slack.com/api/"
TELEGRAM_API = "https://api.telegram.org"
#: 10-b: the only address a file going to Slack is uploaded to (the address
#: Slack hands back is checked against it before any bytes are sent).
SLACK_UPLOAD_HOST = "https://files.slack.com/"
#: 10-b: what the sender hears when Slack refuses an upload for a missing
#: permission: the Slack app was made from an older manifest.
SLACK_FILES_WRITE_WORDS = (
    "your Slack app is missing the files:write permission. Update the app from "
    "the manifest (api.slack.com/apps, App Manifest, paste the manifest from "
    "Add a destination, Slack, Save, then reinstall the app)"
)

_MB = 1024 * 1024


def _read_file(path: str, max_bytes: int, service: str) -> tuple[Path, bytes]:
    """The bytes of the file at ``path`` for an upload (10-b): refused in
    words when it is missing or larger than ``service`` takes."""
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError:
        raise FileNotTaken("it is not on this PC any more") from None
    if size > max_bytes:
        raise FileTooLarge(f"it is {size / _MB:.0f} MB, and {service} takes at most {max_bytes // _MB} MB")
    try:
        return p, p.read_bytes()
    except OSError:
        raise FileNotTaken("it could not be read on this PC") from None


def _media_type(name: str) -> str:
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def _answer_dict(resp: Any) -> dict[str, Any]:
    """A Web API answer as a dict (``{}`` when it is not JSON)."""
    if isinstance(resp, dict):
        return resp
    getter = getattr(resp, "json", None)
    if callable(getter):
        try:
            data = getter()
        except Exception:  # noqa: BLE001
            return {}
        return data if isinstance(data, dict) else {}
    return {}


def _status_ok(resp: Any) -> bool:
    ok, _detail = interpret_response(resp)
    return ok


def slack_blocks(message: str, buttons: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """The message as plain text with its buttons under it (10-b). The first
    button is the main one (one main action per view); its ``value`` is the
    ``ij:…`` data the press brings back."""
    elements = []
    for i, (label, data) in enumerate(buttons, 1):
        button: dict[str, Any] = {
            "type": "button",
            "text": {"type": "plain_text", "text": str(label)[:75], "emoji": True},
            "action_id": f"ij_{i}",
            "value": str(data)[:2000],
        }
        if i == 1:
            button["style"] = "primary"
        elements.append(button)
    return [
        {"type": "section", "text": {"type": "plain_text", "text": str(message)[:3000], "emoji": True}},
        {"type": "actions", "block_id": "ij_buttons", "elements": elements},
    ]


def telegram_keyboard(buttons: list[tuple[str, str]]) -> dict[str, Any]:
    """Telegram's inline keyboard for ``buttons``, one row (10-b)."""
    return {"inline_keyboard": [[
        {"text": str(label)[:64], "callback_data": str(data)[:64]} for label, data in buttons
    ]]}
#: 10-a: the only address a Slack file is downloaded from with the bot token.
#: A download address in an event that points anywhere else is never fetched
#: (the token would go with it).
SLACK_FILE_HOST = "https://files.slack.com/"
#: 10-a: what the sender hears when Slack answers a download with its sign-in
#: page — the Slack app was made from the older manifest, without files:read.
SLACK_FILES_SCOPE_WORDS = (
    "your Slack app is missing the files:read permission. Update the app from "
    "the manifest (api.slack.com/apps, App Manifest, paste the manifest from "
    "Add a destination, Slack, Save, then reinstall the app)"
)


def _int_or_none(value: Any) -> "int | None":
    try:
        return int(value) if value is not None and str(value).strip() != "" else None
    except (TypeError, ValueError):
        return None


def _looks_like_a_page(data: bytes) -> bool:
    """True when a download is an HTML page (Slack's sign-in page) rather
    than the file that was asked for."""
    head = bytes(data[:512]).lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html")


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
    #: 10-b: files back (files.getUploadURLExternal, the upload,
    #: files.completeUploadExternal) and Block Kit buttons, answered over
    #: Socket Mode (``interactive`` envelopes, ``comm/slack_socket``).
    supports_files = True
    display_name = "Slack"
    #: The most one file sent to Slack may be (the phone's own cap is lower).
    FILE_MAX_BYTES = 50 * _MB

    @property
    def supports_buttons(self) -> bool:  # type: ignore[override]
        """Buttons only where a press can come back: Socket Mode (an app
        token is set). Through the Events API webhook a press would go to an
        address nobody set up, so that destination gets the words alone."""
        return bool(self.config.get("app_token_secret"))

    def _bot_headers(self) -> dict[str, str]:
        token = self._resolve_secret(self.config.get("token_secret"))
        if not token:
            raise FileNotTaken("the Slack bot token is not set on this PC")
        return {"Authorization": f"Bearer {token}"}

    def _call(self, method: str, fields: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        """One Slack Web API call as a form POST (10-b); Slack's error in
        words (:class:`FileNotTaken`) when it answers ``ok: false``."""
        data = _answer_dict(self._post_file(SLACK_API + method, fields, None, headers))
        if data.get("ok"):
            return data
        error = str(data.get("error") or "no answer")
        if error == "missing_scope":
            raise FileNotTaken(SLACK_FILES_WRITE_WORDS)
        if error in ("not_in_channel", "channel_not_found"):
            raise FileNotTaken("Iron Jarvis is not in that Slack conversation")
        raise FileNotTaken(f"Slack refused it ({error[:60]})")

    def send_file(self, path: str, *, chat_id: Any = None, caption: str = "") -> dict[str, Any]:
        """Upload one file to the chat that asked (10-b): a DM (``U…``, its
        conversation opened with ``conversations.open``) or the destination's
        own channel, in the thread under the message (``C…|<ts>``). Slack's
        three steps: ``files.getUploadURLExternal``, the bytes to the address
        it gives (only Slack's own file host), ``files.completeUploadExternal``.
        BLOCKING; every failure in words."""
        try:
            headers = self._bot_headers()
            p, data = _read_file(path, self.FILE_MAX_BYTES, "Slack")
            target = str(chat_id or self.config.get("channel") or "").strip()
            channel, _, thread_ts = target.partition("|")
            if channel[:1] in ("U", "W"):
                opened = self._call("conversations.open", {"users": channel}, headers)
                channel = str(((opened.get("channel") or {}).get("id")) or "")
            if not re.fullmatch(r"[CDG][A-Z0-9]{6,}", channel or ""):
                raise FileNotTaken("Slack needs the conversation's id to take a file")
            got = self._call(
                "files.getUploadURLExternal", {"filename": p.name, "length": len(data)}, headers
            )
            url = str(got.get("upload_url") or "")
            file_id = str(got.get("file_id") or "")
            if not url.startswith(SLACK_UPLOAD_HOST) or not file_id:
                raise FileNotTaken("Slack did not give an upload address")
            if not _status_ok(self._post_file(url, {}, ("file", p.name, data, _media_type(p.name)))):
                raise FileNotTaken("Slack did not take the upload")
            fields: dict[str, Any] = {
                "files": json.dumps([{"id": file_id, "title": p.name}]),
                "channel_id": channel,
            }
            if thread_ts:
                fields["thread_ts"] = thread_ts
            if caption:
                fields["initial_comment"] = caption[:3000]
            self._call("files.completeUploadExternal", fields, headers)
            return {"ok": True, "detail": "sent"}
        except FileNotTaken as exc:
            return self._fail(exc.words)

    def answer_press(self, press: Press, label: str) -> bool:
        """The buttons under the message become ``label`` (10-b,
        ``chat.update``: the message's own text, then one quiet line)."""
        token = self._resolve_secret(self.config.get("token_secret"))
        if not token or not press.chat or not press.message:
            return False
        text = press.text or label
        payload = {
            "channel": press.chat,
            "ts": press.message,
            "text": text,
            "blocks": [
                {"type": "section", "text": {"type": "plain_text", "text": text[:3000], "emoji": True}},
                {"type": "context", "elements": [{"type": "plain_text", "text": label[:150], "emoji": True}]},
            ],
        }
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}
        return bool(self._post(SLACK_API + "chat.update", payload, headers).get("ok"))

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
            buttons = kw.get("buttons")
            if buttons:
                # 10-b: Block Kit buttons under the message (``text`` stays the
                # fallback a notification shows). Plain text only: nothing in
                # the message is read as a link or a mention.
                payload["blocks"] = slack_blocks(message, buttons)
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

    def fetch_file(self, f: InboundFile, max_bytes: int) -> bytes:
        """Download a Slack file (10-a) from Slack's own file host with the
        bot token in the ``Authorization`` header. Any other address is
        refused before anything is sent. A Slack app without ``files:read``
        gets Slack's sign-in page instead of the file: that is said in words
        (:data:`SLACK_FILES_SCOPE_WORDS`), never kept as the file."""
        url = str(f.ref or "")
        if not url.startswith(SLACK_FILE_HOST):
            raise FileNotTaken("Slack did not give a download for it")
        token = self._resolve_secret(self.config.get("token_secret"))
        if not token:
            raise FileNotTaken("the Slack bot token is not set on this PC")
        data = self._get_bytes(url, {"Authorization": f"Bearer {token}"}, max_bytes)
        if _looks_like_a_page(data) and "html" not in (f.mime or "").lower():
            raise FileNotTaken(SLACK_FILES_SCOPE_WORDS)
        return data


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

    #: Transport notice for content the bridge still cannot carry (v1.200.0,
    #: CONNECT-AUDIT-2026-08-22 §7; narrowed by 10-a: photos, files and voice
    #: notes are taken now). Sent at most once per sender per poll batch,
    #: allowlisted senders only.
    MEDIA_NOTICE = (
        "I can't use stickers, contacts or locations. Send text, a photo, "
        "a file or a voice note."
    )
    #: Message keys that mean "the sender tried to hand over something that is
    #: still not taken" (as opposed to service updates — joins, pins, title
    #: changes — where a notice would be noise about nothing the sender said).
    #: ``animation`` first: Telegram sends a GIF with a ``document`` too.
    _MEDIA_KEYS = (
        "sticker",
        "animation",
        "contact",
        "location",
        "venue",
        "poll",
        "dice",
    )
    #: 10-a: the message keys that carry a file that IS taken, in the order
    #: they are read (one message carries one).
    _FILE_KEYS = ("photo", "document", "voice", "audio", "video", "video_note")

    #: 10-b: files back (``sendPhoto`` / ``sendDocument``) and inline buttons
    #: (``callback_query`` comes back through ``getUpdates``).
    supports_files = True
    supports_buttons = True
    display_name = "Telegram"
    #: Telegram's own limits for a bot: a picture by ``sendPhoto`` up to
    #: 10 MB, any file by ``sendDocument`` up to 50 MB.
    PHOTO_MAX_BYTES = 10 * _MB
    FILE_MAX_BYTES = 50 * _MB
    #: The pictures ``sendPhoto`` takes; any other file goes as a document.
    _PHOTO_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                    ".webp": "image/webp"}

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
        payload: dict[str, Any] = {"chat_id": chat_id, "text": message}
        if kw.get("buttons"):
            # 10-b: the buttons under a question (one row).
            payload["reply_markup"] = telegram_keyboard(kw["buttons"])
        return self._post(url, payload)

    def send_file(self, path: str, *, chat_id: Any = None, caption: str = "") -> dict[str, Any]:
        """Send one file to the chat (10-b): ``sendPhoto`` for a picture
        under 10 MB, else ``sendDocument`` (50 MB is Telegram's own limit).
        The address HOLDS THE BOT TOKEN: every failure is reworded and the
        token never reaches a detail, a log or an event. BLOCKING."""
        token = self._resolve_secret(self.config.get("token_secret"))
        if not token:
            return self._fail("Telegram's bot token is not set on this PC")
        chat = chat_id or self.config.get("chat_id")
        if not chat:
            return self._fail("telegram: config needs `chat_id`")
        try:
            p, data = _read_file(path, self.FILE_MAX_BYTES, "Telegram")
            mime = self._PHOTO_TYPES.get(p.suffix.lower())
            if mime and len(data) <= self.PHOTO_MAX_BYTES:
                method, field = "sendPhoto", "photo"
            else:
                method, field, mime = "sendDocument", "document", _media_type(p.name)
            fields: dict[str, Any] = {"chat_id": chat}
            if caption:
                fields["caption"] = caption[:1024]
            resp = self._post_file(f"{TELEGRAM_API}/bot{token}/{method}", fields,
                                   (field, p.name, data, mime))
        except FileNotTaken as exc:
            return self._fail(exc.words)
        ok, detail = interpret_response(resp)
        if ok:
            return {"ok": True, "detail": "sent"}
        said = _answer_dict(resp).get("description")
        why = f"Telegram refused it ({str(said)[:80]})" if said else "Telegram refused it"
        return self._fail(why.replace(token, "***"))

    def answer_press(self, press: Press, label: str) -> bool:
        """Answer a press (10-b): ``answerCallbackQuery`` (the button stops
        spinning, ``label`` shows), then ``editMessageReplyMarkup``: the
        buttons become one answered button saying ``label``. True when the
        message changed."""
        token = self._resolve_secret(self.config.get("token_secret"))
        if not token:
            return False
        if press.ref:
            self._post(f"{TELEGRAM_API}/bot{token}/answerCallbackQuery",
                       {"callback_query_id": press.ref, "text": label[:200]})
        if press.chat is None or not str(press.message or "").isdecimal():
            return False
        from .prompts import DONE_DATA

        res = self._post(f"{TELEGRAM_API}/bot{token}/editMessageReplyMarkup", {
            "chat_id": press.chat,
            "message_id": int(press.message),
            "reply_markup": telegram_keyboard([(label, DONE_DATA)]),
        })
        return bool(res.get("ok"))

    @staticmethod
    def _press_of(upd: dict, update_id: Any) -> "InboundMessage | None":
        """A ``callback_query`` (a button pressed under one of the bot's own
        messages, 10-b) as an :class:`InboundMessage` with ``press`` set and
        no words. Who pressed is ``from``; the chat is the message's. Read
        by the pipeline only after its guards."""
        cq = upd.get("callback_query")
        if not isinstance(cq, dict):
            return None
        data = cq.get("data")
        if not isinstance(data, str) or not data:
            return None
        frm = cq.get("from") or {}
        msg = cq.get("message") or {}
        chat = (msg.get("chat") or {}).get("id") if isinstance(msg, dict) else None
        if chat is None:
            # A press with no message of the bot's under it (inline mode) has
            # no chat to check against the presser: never taken.
            return None
        return InboundMessage(
            sender_id=str(frm.get("id", "")),
            text="",
            update_id=update_id,
            reply_to=chat,
            is_bot=bool(frm.get("is_bot", False)),
            raw=upd,
            press=Press(
                data=data[:64], ref=str(cq.get("id") or ""), chat=chat,
                message=str((msg or {}).get("message_id") or ""),
                text=str((msg or {}).get("text") or "")[:4096],
            ),
        )

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
        #: 10-a: an album (several photos sent together) arrives as one
        #: update per photo with the same ``media_group_id`` and the caption
        #: on one of them: it is ONE message here, so it is ONE chat turn.
        albums: dict[tuple[Any, Any], InboundMessage] = {}
        for upd in data.get("result", []) or []:
            update_id = upd.get("update_id")
            if isinstance(update_id, int):
                next_offset = max(next_offset, update_id + 1)
            try:
                if "callback_query" in upd:
                    # 10-b: a button pressed under one of the bot's messages.
                    pressed = self._press_of(upd, update_id)
                    if pressed is not None:
                        messages.append(pressed)
                    continue
                msg = upd.get("message") or upd.get("edited_message") or {}
                if not isinstance(msg, dict):
                    continue
                files = self._files_of(msg)
                text = msg.get("text") or msg.get("caption") or ""
                if not isinstance(text, str):
                    text = ""
                if not text and not files:
                    # Still not taken (a sticker, a contact): one honest
                    # notice to an ALLOWED sender (v1.200.0), never silence.
                    self._media_notice(msg, noticed)
                    continue
                frm = msg.get("from") or {}
                chat = msg.get("chat") or {}
                group = msg.get("media_group_id")
                key = (chat.get("id"), group)
                if group and key in albums:
                    held = albums[key]
                    held.files.extend(files)
                    if text and not held.text:
                        held.text = text
                    if isinstance(update_id, int):
                        held.update_id = max(int(held.update_id or 0), update_id)
                    continue
                inbound = InboundMessage(
                    sender_id=str(frm.get("id", "")),
                    text=text,
                    update_id=update_id,
                    reply_to=chat.get("id"),
                    is_bot=bool(frm.get("is_bot", False)),
                    raw=upd,
                    files=files,
                )
                if group:
                    albums[key] = inbound
                messages.append(inbound)
            except Exception:  # noqa: BLE001 — one malformed update never breaks the batch
                _log.debug("telegram: an update could not be read", exc_info=True)
        return messages, next_offset

    @classmethod
    def _files_of(cls, msg: dict) -> "list[InboundFile]":
        """The file a Telegram message carries, as :class:`InboundFile` (10-a):
        the largest size of a photo, a document, a voice note, an audio file,
        a video or a round video. Nothing is downloaded here. A GIF
        (``animation``, which also carries ``document``) is not a file."""
        if any(k in msg for k in ("animation", "sticker")):
            return []
        from .media import file_name, kind_of

        for key in cls._FILE_KEYS:
            got = msg.get(key)
            if key == "photo":
                sizes = [s for s in (got or []) if isinstance(s, dict) and s.get("file_id")]
                if not sizes:
                    continue
                sizes.sort(key=lambda s: (int(s.get("file_size") or 0),
                                          int(s.get("width") or 0) * int(s.get("height") or 0)))
                big = sizes[-1]
                return [InboundFile(
                    kind="photo", name="photo.jpg", mime="image/jpeg",
                    size=_int_or_none(big.get("file_size")), ref=str(big["file_id"]),
                    smaller=[(str(s["file_id"]), _int_or_none(s.get("file_size")))
                             for s in reversed(sizes[:-1])],
                )]
            if not isinstance(got, dict) or not got.get("file_id"):
                continue
            mime = str(got.get("mime_type") or "")
            if key == "voice":
                kind, name = "voice", file_name("voice-note", mime or "audio/ogg", "voice-note")
            elif key == "video_note":
                kind, name = "video", file_name("video-note", mime or "video/mp4", "video-note")
            else:
                fallback = {"document": "file", "audio": "audio", "video": "video"}[key]
                name = file_name(str(got.get("file_name") or ""), mime, fallback)
                kind = kind_of(name, mime) if key == "document" else key
                if key == "document" and kind == "photo":
                    kind = "document"  # a picture sent as a file stays a file
            return [InboundFile(kind=kind, name=name, mime=mime,
                                size=_int_or_none(got.get("file_size")), ref=str(got["file_id"]))]
        return []

    def fetch_file(self, f: InboundFile, max_bytes: int) -> bytes:
        """``getFile``, then the download from ``/file/bot<token>/<path>``
        (10-a). That address HOLDS THE BOT TOKEN: it is built here, used once
        and never logged, put in an event or kept in an error; every failure
        is reworded (``Channel._get_bytes``). A photo too large at its full
        size falls back to Telegram's largest smaller copy under the cap."""
        token = self._resolve_secret(self.config.get("token_secret"))
        if not token:
            raise FileNotTaken("Telegram's bot token is not set on this PC")
        ref, size = f.ref, f.size
        if size is not None and size > max_bytes:
            for alt_ref, alt_size in f.smaller:
                if alt_size is not None and alt_size <= max_bytes:
                    ref, size = alt_ref, alt_size
                    break
            else:
                raise FileTooLarge(too_large_words(size, max_bytes))
        info = self._get_json(f"{TELEGRAM_API}/bot{token}/getFile", {"file_id": ref})
        result = (info or {}).get("result") if (info or {}).get("ok") else None
        path = str((result or {}).get("file_path") or "")
        if not path:
            raise FileNotTaken("Telegram did not hand the file over")
        told = _int_or_none((result or {}).get("file_size"))
        if told is not None and told > max_bytes:
            raise FileTooLarge(too_large_words(told, max_bytes))
        return self._get_bytes(f"{TELEGRAM_API}/file/bot{token}/{path}", None, max_bytes)

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
#: A signed e-mail's signature (S/MIME's ``smime.p7s``, OpenPGP's
#: ``signature.asc``) and the public key Thunderbird attaches beside it: the
#: mail program added them, the person did not send a file (R0.3 review).
_SIGNATURE_TYPES = frozenset({
    "application/pkcs7-signature",
    "application/x-pkcs7-signature",
    "application/pgp-signature",
    "application/pgp-keys",
})
#: An encrypted (or opaque-signed) e-mail: a message Iron Jarvis cannot open,
#: not a file, so "the file you sent" would be false. Not counted.
_SEALED_TYPES = frozenset({
    "application/pkcs7-mime",
    "application/x-pkcs7-mime",
    "multipart/encrypted",
})


def _email_file_parts(em: Any) -> list[Any]:
    """The parts of an e-mail that are files the sender attached (R0.3; 10-a
    takes them).

    A part is a file when it is marked as an attachment, or when it has a file
    name, is not the body's text, and is not a picture INSIDE the body (a part
    of ``multipart/related``, such as a signature logo the HTML shows). Apple
    Mail marks an attached PDF ``inline`` with a name, so the name counts. An
    attached e-mail is one file; what it carries is not counted again. A
    message that is itself one file (a single non-text part) is one file.
    A signature or the key beside it is not a file, and an encrypted e-mail
    is not one either (``_SIGNATURE_TYPES``, ``_SEALED_TYPES``).
    Never raises: an unreadable message gives what was read before it.
    Nothing is decoded here.
    """
    found: list[Any] = []
    seen = 0
    stack: list[tuple[Any, bool]] = [(em, False)]
    try:
        while stack and seen < _MAX_EMAIL_PARTS:
            part, in_body = stack.pop()
            seen += 1
            ctype = part.get_content_type()
            if ctype in _SIGNATURE_TYPES or ctype in _SEALED_TYPES:
                continue  # neither it nor what it seals is a file
            if part is em and not part.is_multipart():
                return [part] if part.get_content_maintype() != "text" else []
            if part is not em:
                disp = (part.get_content_disposition() or "").lower()
                named = bool(part.get_filename())
                if disp == "attachment" or (
                    named and not in_body and ctype not in _BODY_TEXT_TYPES
                ):
                    found.append(part)
                    continue
            if part.is_multipart():
                subs = part.get_payload()
                if isinstance(subs, list):
                    related = in_body or ctype == "multipart/related"
                    stack.extend((s, related) for s in reversed(subs))
    except Exception:  # noqa: BLE001 — a malformed message must never break a poll
        _log.debug("email: could not read the attachments", exc_info=True)
    return found


def _email_file_count(em: Any) -> int:
    """How many files the sender attached (R0.3): :func:`_email_file_parts`
    counted. Never raises."""
    return len(_email_file_parts(em))


#: 10-a: the most attachments listed from one e-mail (the pipeline takes the
#: first five and names the rest by count).
_MAX_EMAIL_FILES = 50


def _base64_size(text: str) -> int:
    """The size of what base64 ``text`` decodes to, without decoding it:
    three bytes for every four characters, leaving out the line breaks and
    spaces between its lines and the ``=`` padding at its end (counting them
    made a file just under the cap look over it)."""
    chars = len(text) - sum(text.count(c) for c in ("\r", "\n", " ", "\t"))
    tail = text[-16:].rstrip()
    chars -= len(tail) - len(tail.rstrip("="))
    return max(0, chars) * 3 // 4


def _email_attachments(em: Any, max_count: int = _MAX_EMAIL_FILES) -> "list[InboundFile]":
    """The sender's attachments as :class:`InboundFile` (10-a), at most
    ``max_count``. Each keeps its part as ``handle``; nothing is decoded until
    :meth:`EmailChannel.fetch_file`. ``size`` is read from the encoded
    payload (base64: three bytes for four characters, :func:`_base64_size`),
    so a file over the cap is refused before it is decoded. Never raises."""
    from .media import file_name, kind_of

    out: list[InboundFile] = []
    for n, part in enumerate(_email_file_parts(em)[: max(0, int(max_count))], 1):
        try:
            ctype = part.get_content_type()
            if ctype == "message/rfc822":
                name = file_name("forwarded-email.eml", ctype, "forwarded-email.eml")
            else:
                name = file_name(_decode_mime_header(part.get_filename() or ""), ctype, f"attachment-{n}")
            raw = part.get_payload()
            size: int | None = None
            if isinstance(raw, str):
                enc = str(part.get("Content-Transfer-Encoding") or "").strip().lower()
                if enc == "base64":
                    size = _base64_size(raw)
                elif enc in ("", "7bit", "8bit", "binary"):
                    size = len(raw)
                # quoted-printable: not known before the decode; the cap is
                # checked on the decoded bytes (EmailChannel.fetch_file).
            out.append(InboundFile(kind=kind_of(name, ctype), name=name, mime=ctype,
                                   size=size, ref=str(n), handle=part))
        except Exception:  # noqa: BLE001 — a malformed part is left out
            _log.debug("email: an attachment could not be listed", exc_info=True)
    return out


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

    #: 10-b: files back go as attachments of ONE email, 20 MB in all.
    supports_files = True
    display_name = "email"
    ALL_FILES_MAX_BYTES = 20 * _MB

    def send(self, message: str, **kw: Any) -> dict[str, Any]:
        cfg = self.config
        host = cfg.get("host")
        from_addr = cfg.get("from_addr") or cfg.get("username")
        to_addr = kw.get("to") or cfg.get("to_addr")
        if not host or not from_addr or not to_addr:
            return self._fail("email: config needs `host`, `from_addr` and `to_addr`")
        try:
            from email.message import EmailMessage

            msg = EmailMessage()
            msg["Subject"] = kw.get("subject") or cfg.get("subject") or "Iron Jarvis"
            msg["From"] = from_addr
            msg["To"] = to_addr
            msg.set_content(message)
            self._smtp_send(msg)
            return {"ok": True, "detail": f"emailed {to_addr}"}
        except Exception as exc:  # noqa: BLE001 — surface, never raise to the notifier
            return self._fail(f"email: {type(exc).__name__}: {exc}")

    def _smtp_send(self, msg: Any) -> None:
        """Send one built message over SMTP. BLOCKING; raises on failure."""
        import smtplib

        cfg = self.config
        password = self._resolve_secret(cfg.get("password_secret"))
        port = int(cfg.get("port") or 587)
        with smtplib.SMTP(cfg.get("host"), port, timeout=15) as smtp:
            if cfg.get("use_tls", True):
                smtp.starttls()
            if cfg.get("username") and password:
                smtp.login(cfg["username"], password)
            smtp.send_message(msg)

    def send_file(self, path: str, *, chat_id: Any = None, caption: str = "") -> dict[str, Any]:
        return self.send_files([path], chat_id=chat_id, caption=caption)[0]

    def send_files(
        self, paths: list[str], *, chat_id: Any = None, caption: str = ""
    ) -> list[dict[str, Any]]:
        """The files as attachments of ONE email (10-b), at most
        :data:`ALL_FILES_MAX_BYTES` in all: a file past that is not attached
        and says so. It goes where the reply's own email goes (``to_addr``,
        as :meth:`send`: ``chat_id`` is not an address here). One answer per
        path, in order. BLOCKING."""
        cfg = self.config
        to_addr = cfg.get("to_addr")
        from_addr = cfg.get("from_addr") or cfg.get("username")
        if not cfg.get("host") or not from_addr or not to_addr:
            return [self._fail("email: config needs `host`, `from_addr` and `to_addr`") for _ in paths]
        from email.message import EmailMessage

        out: list[dict[str, Any]] = []
        files: list[tuple[Path, bytes]] = []
        total = 0
        for path in paths:
            try:
                p, data = _read_file(path, self.ALL_FILES_MAX_BYTES, "email")
            except FileNotTaken as exc:
                out.append(self._fail(exc.words))
                continue
            if total + len(data) > self.ALL_FILES_MAX_BYTES:
                out.append(self._fail(f"one email carries at most {self.ALL_FILES_MAX_BYTES // _MB} MB in all"))
                continue
            total += len(data)
            files.append((p, data))
            out.append({"ok": True, "detail": "sent"})
        if not files:
            return out
        msg = EmailMessage()
        msg["Subject"] = "Iron Jarvis: your files"
        msg["From"] = from_addr
        msg["To"] = str(to_addr)
        msg.set_content(caption or ("The files you asked for: " + ", ".join(p.name for p, _ in files) + "."))
        for p, data in files:
            maintype, _, subtype = _media_type(p.name).partition("/")
            msg.add_attachment(data, maintype=maintype or "application",
                               subtype=subtype or "octet-stream", filename=p.name)
        try:
            self._smtp_send(msg)
        except Exception as exc:  # noqa: BLE001 — every attached file failed together
            why = f"the email could not be sent ({type(exc).__name__})"
            return [self._fail(why) if r.get("ok") else r for r in out]
        return out

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
                # 10-a: the attachments are listed (never decoded here) only
                # for a sender the provider vouched for AND on the allowlist.
                files = self._files_for(em, sender, verified=verified)
                messages.append(
                    InboundMessage(
                        # An unproven sender never matches the allowlist, so it
                        # gets no commands, approvals, rules or sessions.
                        sender_id=sender if verified else UNVERIFIED_PREFIX + sender,
                        text=subject + "\n" + body,
                        # 10-a: the body alone is the sender's own words (a
                        # shared voice memo's subject is the recording's name),
                        # without the line the mail app adds ("Sent from my
                        # iPhone") and the earlier e-mail a reply quotes. It
                        # only decides whether a recording is heard; ``text``
                        # above is what the turn reads.
                        own_words=without_signoff(body, quoted_reply=True),
                        update_id=int(uid),
                        reply_to=sender,
                        is_bot=False,
                        raw={
                            "subject": subject,
                            "from": sender,
                            "sender_verified": verified,
                        },
                        files=files,
                        # R0.3: anything that could not be listed (a stranger's
                        # files are never listed; the pipeline refuses them).
                        files_not_read=0 if files else _email_file_count(em),
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

    def _files_for(self, em: Any, sender: str, *, verified: bool) -> "list[InboundFile]":
        """The e-mail's attachments (10-a), listed only when the receiving
        provider vouched for ``sender`` and the sender is on the allowlist;
        ``[]`` otherwise, so a stranger's file is never even looked at."""
        if not verified or not self.is_authorized(sender):
            return []
        return _email_attachments(em)

    def fetch_file(self, f: InboundFile, max_bytes: int) -> bytes:
        """Decode one attachment (10-a): the part was read with the e-mail, so
        nothing is downloaded; the estimate is checked against the cap before
        the decode, the bytes after it. An attached e-mail is kept whole."""
        part = f.handle
        if part is None:
            raise FileNotTaken("the attachment could not be read")
        if f.size is not None and f.size > max_bytes:
            raise FileTooLarge(too_large_words(f.size, max_bytes))
        try:
            if part.get_content_type() == "message/rfc822":
                inner = part.get_payload()
                data = inner[0].as_bytes() if isinstance(inner, list) and inner else part.as_bytes()
            else:
                data = part.get_payload(decode=True)
        except Exception:  # noqa: BLE001 — a malformed part is a sentence
            raise FileNotTaken("the attachment could not be read") from None
        if not isinstance(data, (bytes, bytearray)):
            raise FileNotTaken("the attachment could not be read")
        if len(data) > max_bytes:
            raise FileTooLarge(too_large_words(len(data), max_bytes))
        return bytes(data)


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
