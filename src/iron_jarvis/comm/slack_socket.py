"""Slack SOCKET MODE — two-way Slack with ZERO internet exposure.

Instead of Slack calling us (which needs a public URL), the daemon dials OUT:
``apps.connections.open`` (app-level ``xapp-`` token) returns a ``wss://`` URL,
we connect, Slack streams event envelopes down the socket we initiated, and we
ACK each envelope. No inbound port, no tunnel, no exposure — the same
outbound-only posture as Telegram polling, but real-time.

Safety mirrors the inbound poller exactly (it REUSES ``InboundPoller._handle``):
bot-loop protection, FAIL-CLOSED sender allowlist, bounded replies. Transports
are injectable so the test suite runs fully offline.

WHERE JARVIS ANSWERS (v1.444.0, a user report: "me sending IJ a message from
Slack goes into the void"). The pump used to answer DIRECT MESSAGES only, and
the manifest subscribed to ``message.im`` alone, so a message written in the
destination's own channel (#iron-jarvis, where the alerts land) never even
reached Iron Jarvis. Now, for an allowlisted sender:

* a DM runs the full pipeline and is answered in the DM (unchanged);
* a message in the destination's OWN channel (the ``channel`` it posts alerts
  to, resolved to Slack's id once per connect) runs the same pipeline and is
  answered in a THREAD under that message: the user chose that channel as
  Jarvis's place, and the alerts already land there;
* an ``@Iron Jarvis`` mention in ANY OTHER channel is answered PRIVATELY, in a
  DM, so a shared channel never sees the answer;
* any other channel message still fires the channel's ``slack`` reflex rules
  only (no reply).

Slack sends a mention in a subscribed channel twice (``message`` and
``app_mention``); the second copy is dropped by its ``(channel, ts)``.
:data:`LISTENING` keeps, per destination, when the socket connected and when
Slack last sent anything, so the Notifications row can say "connected, but
Slack has sent nothing" instead of leaving the user to guess.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Callable

from .base import InboundMessage

log = logging.getLogger(__name__)

_OPEN_URL = "https://slack.com/api/apps.connections.open"
_API_URL = "https://slack.com/api/"

#: A Slack conversation id as Slack writes it (C... public, G... private/old).
_CHANNEL_ID_RX = re.compile(r"[CG][A-Z0-9]{6,}")
#: ``<@U123>`` / ``<@U123|name>`` mention tokens Slack puts in message text.
_MENTION_RX = re.compile(r"<@[A-Z0-9]+(?:\|[^>]*)?>")
#: How many ``(channel, ts)`` pairs are remembered to drop Slack's second copy.
_SEEN_MAX = 256

#: What each destination's pump has heard, in memory: ``connected_at``,
#: ``last_event_at`` (anything Slack sent), ``last_message_at`` (a message from
#: an allowlisted person that Jarvis acted on), ``own_channel`` (Slack's id for
#: the destination's channel, or None) and ``channel_note`` (why it could not be
#: found or joined, or ""). Read by ``daemon/routes/comm.listening_status``.
LISTENING: dict[str, dict[str, Any]] = {}

#: Sent once to the person, in the DM, before the answer to a mention elsewhere.
MENTION_ELSEWHERE_NOTE = (
    "You mentioned me in a channel. I answer here, in our private chat, so "
    "others in that channel don't see it."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _note(name: str, **fields: Any) -> None:
    LISTENING.setdefault(name, {}).update(fields)


def _default_slack_api(method: str, token: str, params: dict[str, Any]) -> dict[str, Any]:
    """Blocking: one Slack Web API call with the bot token in the header."""
    import httpx

    resp = httpx.post(
        _API_URL + method,
        headers={"Authorization": f"Bearer {token}"},
        data={k: str(v) for k, v in params.items() if v is not None},
        timeout=20,
    )
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001 - a non-JSON answer is an error answer
        return {"ok": False, "error": f"HTTP {resp.status_code}"}
    return data if isinstance(data, dict) else {"ok": False, "error": "bad answer"}


def own_channel_words(channel: str, error: str) -> str:
    """Why Jarvis cannot answer in the destination's own channel, in words."""
    where = channel if channel.startswith("#") else f"#{channel}"
    if error == "missing_scope":
        return (
            f"Jarvis can't tell which Slack channel is {where}: the Slack app is "
            "missing the channels:read permission. Update the app from the "
            "manifest (App Manifest, paste, Save, then reinstall the app). Until "
            "then, message Jarvis directly in Slack (Apps, Iron Jarvis)."
        )
    if error == "not_found":
        return (
            f"Jarvis can't find a Slack channel named {where}. Check the channel "
            "name on this destination."
        )
    if error == "not_in_channel":
        return (
            f"Jarvis isn't a member of {where}, so Slack doesn't send it what you "
            f"write there. In {where}, type /invite @Iron Jarvis. Until then, "
            "message Jarvis directly in Slack."
        )
    return (
        f"Jarvis couldn't look up {where} in Slack ({error}). Message Jarvis "
        "directly in Slack (Apps, Iron Jarvis) in the meantime."
    )


def _default_open_ws(app_token: str) -> str:
    """Blocking: ask Slack for a fresh Socket Mode wss:// URL."""
    import httpx

    resp = httpx.post(
        _OPEN_URL,
        headers={"Authorization": f"Bearer {app_token}"},
        timeout=20,
    )
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"apps.connections.open failed: {data.get('error')}")
    return str(data["url"])


class SlackSocketMode:
    """One outbound WebSocket per inbound-enabled Slack channel."""

    def __init__(
        self,
        poller: Any,  # InboundPoller — we reuse its _handle pipeline verbatim
        notifier: Any,
        secret_resolver: Callable[[str], str | None],
        comm_config: Callable[[], dict],
        *,
        open_ws: Callable[[str], str] | None = None,
        ws_connect: Any = None,  # async ctx factory (url) -> websocket; test seam
        on_tick: Callable[[bool, BaseException | None], None] | None = None,
        slack_api: Callable[[str, str, dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.poller = poller
        self.notifier = notifier
        self.secrets = secret_resolver
        self.comm_config = comm_config
        self._open_ws = open_ws or _default_open_ws
        self._ws_connect = ws_connect
        #: v1.444.0: Web API calls for finding/joining the destination's own
        #: channel and the bot's own user id (test seam; never a model call).
        self._slack_api = slack_api or _default_slack_api
        #: destination name -> Slack id of its own channel (None = unknown).
        self._own: dict[str, str | None] = {}
        #: destination name -> the bot's own Slack user id ("" = unknown).
        self._bot_user: dict[str, str] = {}
        self._seen: OrderedDict[tuple[str, str], None] = OrderedDict()
        #: v1.229.0: loop-health seam — ``(True, None)`` on a real connect,
        #: ``(False, exc)`` on every drop/refused dial. The daemon used to
        #: record ``slack_socket`` healthy when it ARMED this pump, and the
        #: pump reconnects internally, so a socket that never once connected
        #: read ok forever (docs/TODO.md carried it since v1.226.0).
        self._on_tick = on_tick

    def _tick(self, ok: bool, exc: BaseException | None = None) -> None:
        if self._on_tick is None:
            return
        try:
            self._on_tick(ok, exc)
        except Exception:  # noqa: BLE001 — health bookkeeping never kills the pump
            log.debug("slack socket: on_tick raised", exc_info=True)

    # -- discovery -----------------------------------------------------------
    def candidates(self) -> list[tuple[str, str]]:
        """``(channel_name, app_token)`` for every Slack channel that opted in:
        type slack + inbound_enabled + a NON-EMPTY allowlist + an app token."""
        out: list[tuple[str, str]] = []
        channels = (self.comm_config() or {}).get("channels") or {}
        for name, cfg in channels.items():
            if (cfg or {}).get("type") != "slack":
                continue
            ch = self.notifier.get(name)
            if ch is None or not ch.inbound_enabled() or not ch.allowed_senders():
                continue
            secret_name = cfg.get("app_token_secret")
            token = self.secrets(secret_name) if secret_name else None
            if token:
                out.append((name, token))
        return out

    def enabled(self) -> bool:
        return bool(self.candidates())

    # -- the destination's own channel ---------------------------------------
    def resolve_own_channel(self, name: str) -> str | None:
        """BLOCKING: find (and join) the destination's own channel; Slack's id.

        The destination posts to ``channel`` (``#iron-jarvis`` or an id). An id
        is used as it is; a name is looked up with ``conversations.list`` (needs
        ``channels:read``, ``groups:read`` for a private one). A public channel
        the bot is not in is joined (``channels:join``): Slack only sends a
        channel's messages to a member. Every failure is a sentence in
        :data:`LISTENING` and ``None``; the DM path keeps working regardless.
        Also learns the bot's own user id (``auth.test``, no scope) so a reply
        Jarvis posted is never read back as the user's message.
        """
        cfg = ((self.comm_config() or {}).get("channels") or {}).get(name) or {}
        target = str(cfg.get("channel") or "").strip()
        secret_name = cfg.get("token_secret")
        token = self.secrets(secret_name) if secret_name else None
        if not token:
            _note(name, own_channel=None, channel_note="")
            return None
        try:
            who = self._slack_api("auth.test", token, {})
            if who.get("ok"):
                self._bot_user[name] = str(who.get("user_id") or "")
        except Exception:  # noqa: BLE001 - the bot id only sharpens loop protection
            log.debug("slack auth.test failed for %r", name, exc_info=True)
        if not target:
            _note(name, own_channel=None, channel_note="")
            return None
        if _CHANNEL_ID_RX.fullmatch(target):
            _note(name, own_channel=target, channel_note="")
            return target
        wanted = target.lstrip("#").strip().lower()
        found: dict[str, Any] | None = None
        cursor: str | None = None
        try:
            for _page in range(20):
                data = self._slack_api(
                    "conversations.list",
                    token,
                    {
                        "types": "public_channel,private_channel",
                        "exclude_archived": "true",
                        "limit": 200,
                        "cursor": cursor,
                    },
                )
                if not data.get("ok"):
                    words = own_channel_words(target, str(data.get("error") or "error"))
                    _note(name, own_channel=None, channel_note=words)
                    return None
                for conv in data.get("channels") or []:
                    if str(conv.get("name") or "").lower() == wanted:
                        found = conv
                        break
                cursor = ((data.get("response_metadata") or {}).get("next_cursor")) or None
                if found is not None or not cursor:
                    break
        except Exception as exc:  # noqa: BLE001 - a failed look-up is a sentence
            _note(name, own_channel=None, channel_note=own_channel_words(target, str(exc)))
            return None
        if found is None:
            _note(name, own_channel=None, channel_note=own_channel_words(target, "not_found"))
            return None
        cid = str(found.get("id") or "") or None
        note = ""
        if cid and not found.get("is_member"):
            joined: dict[str, Any] = {"ok": False, "error": "not_in_channel"}
            if not found.get("is_private"):
                try:
                    joined = self._slack_api("conversations.join", token, {"channel": cid})
                except Exception:  # noqa: BLE001
                    joined = {"ok": False, "error": "not_in_channel"}
            if not joined.get("ok"):
                note = own_channel_words(target, "not_in_channel")
        _note(name, own_channel=cid, channel_note=note)
        return cid

    def _first_copy(self, channel: str, ts: str) -> bool:
        """True the first time ``(channel, ts)`` is seen; Slack sends a
        mention in a subscribed channel as two events."""
        if not ts:
            return True
        key = (channel, ts)
        if key in self._seen:
            return False
        self._seen[key] = None
        while len(self._seen) > _SEEN_MAX:
            self._seen.popitem(last=False)
        return True

    @staticmethod
    def _clean(text: str) -> str:
        return _MENTION_RX.sub("", text or "").strip()

    # -- envelope processing (pure-ish; unit-testable) -------------------------
    async def process_envelope(self, name: str, envelope: dict) -> dict[str, Any] | None:
        """Handle ONE Socket Mode envelope; returns the handler result or None.

        Where the answer goes is in the module docstring: a DM is answered in
        the DM, the destination's own channel in a thread, a mention elsewhere
        privately in a DM, and any other channel message fires the ``slack``
        reflex rules only. Plain messages/mentions with no subtype only; a
        bot's message (Jarvis's own replies included) is never acted on.
        """
        if envelope.get("type") != "events_api":
            return None
        _note(name, last_event_at=_now())
        event = ((envelope.get("payload") or {}).get("event")) or {}
        etype = event.get("type")
        if etype not in ("message", "app_mention") or event.get("subtype"):
            return None
        ch = self.notifier.get(name)
        if ch is None:
            return None
        user = str(event.get("user") or "")
        is_bot = bool(event.get("bot_id")) or (
            bool(user) and user == self._bot_user.get(name, "")
        )
        if event.get("channel_type") == "im":
            # DM: the FULL supervised pipeline. ``_handle`` carries ``is_bot`` and
            # applies its OWN loop protection + fail-closed allowlist, so a bot's
            # own message is passed through and dropped there (unchanged).
            msg = InboundMessage(
                sender_id=user,
                text=str(event.get("text") or ""),
                update_id=None,
                is_bot=is_bot,
                # Reply to the USER id: chat.postMessage(channel=U...) opens the
                # DM, and it satisfies the poller's private-chat guard (reply_to
                # == sender_id), mirroring Telegram's private-chat semantics.
                reply_to=user,
            )
            if not is_bot and ch.is_authorized(user):
                _note(name, last_message_at=_now())
            return await self.poller._handle(name, ch, msg)  # noqa: SLF001 - by design
        # Channel message / @mention. Loop protection first: a bot's message
        # (Jarvis's own threaded answers included) is never acted on.
        if is_bot or not user:
            return None
        channel = str(event.get("channel") or "")
        ts = str(event.get("ts") or "")
        if not self._first_copy(channel, ts):
            return {"channel": name, "status": "duplicate", "sender": user}
        if not ch.is_authorized(user):
            return None
        # Every channel message from an allowlisted person fires the channel's
        # "slack" reflex rules, as before; WHETHER Jarvis also answers depends on
        # where it was written (module docstring).
        router = getattr(self.poller, "reflex_router", None)
        if router is not None:
            await router.on_slack(
                text=str(event.get("text") or ""), channel=channel, sender=user
            )
        text = self._clean(str(event.get("text") or ""))
        own = self._own.get(name)
        if own and channel == own:
            # The destination's own channel: answered in a thread under the
            # message (a reply inside a thread stays in that thread).
            _note(name, last_message_at=_now())
            msg = InboundMessage(
                sender_id=user,
                text=text,
                update_id=None,
                is_bot=False,
                reply_to=f"{channel}|{event.get('thread_ts') or ts}",
                own_channel=True,
            )
            return await self.poller._handle(name, ch, msg)  # noqa: SLF001 - by design
        if etype == "app_mention":
            # A mention in a shared channel: answered privately, in the DM, so
            # a shared channel never sees the answer.
            _note(name, last_message_at=_now())
            try:
                await asyncio.to_thread(ch.send, MENTION_ELSEWHERE_NOTE, chat_id=user)
            except Exception:  # noqa: BLE001 - the note never blocks the answer
                log.debug("slack mention note failed on %r", name, exc_info=True)
            msg = InboundMessage(
                sender_id=user, text=text, update_id=None, is_bot=False, reply_to=user
            )
            return await self.poller._handle(name, ch, msg)  # noqa: SLF001 - by design
        # Any other channel message: the reflex rules only; no reply.
        if router is None:
            return None
        return {"channel": name, "status": "reflex", "sender": user}

    # -- the pump --------------------------------------------------------------
    async def run_channel(self, name: str, app_token: str, *, stop: asyncio.Event) -> None:
        """Connect + pump envelopes for one channel; reconnects with backoff."""
        try:
            import websockets
        except Exception as exc:  # pragma: no cover — bundled via uvicorn[standard]
            log.error("slack socket mode needs the 'websockets' package")
            self._tick(False, exc)
            return
        connect = self._ws_connect or websockets.connect
        delay = 2.0
        while not stop.is_set():
            try:
                url = await asyncio.to_thread(self._open_ws, app_token)
                async with connect(url) as ws:
                    delay = 2.0  # healthy connection resets the backoff
                    log.info("slack socket mode connected for channel %r", name)
                    self._tick(True)
                    _note(name, connected_at=_now())
                    # Find (and join) the channel the destination posts to, once
                    # per connect, off the loop; a failure is a sentence and the
                    # DM path keeps working.
                    try:
                        self._own[name] = await asyncio.to_thread(
                            self.resolve_own_channel, name
                        )
                    except Exception:  # noqa: BLE001 - never lose the socket over it
                        log.debug("slack own-channel look-up failed", exc_info=True)
                    async for raw in ws:
                        if stop.is_set():
                            break
                        try:
                            envelope = json.loads(raw)
                        except Exception:  # noqa: BLE001
                            continue
                        etype = envelope.get("type")
                        if etype == "disconnect":
                            break  # Slack is rotating the socket — reconnect
                        env_id = envelope.get("envelope_id")
                        if env_id:  # ACK FIRST (3s deadline), then handle
                            await ws.send(json.dumps({"envelope_id": env_id}))
                        try:
                            await self.process_envelope(name, envelope)
                        except Exception:  # noqa: BLE001 — one bad event ≠ dead socket
                            log.exception("slack socket event failed on %r", name)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — reconnect with backoff
                log.warning("slack socket for %r dropped: %s", name, exc)
                self._tick(False, exc)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
                delay = min(delay * 2, 300.0)

    async def run(self, *, stop: asyncio.Event) -> None:
        """Run one pump per candidate channel until ``stop`` is set."""
        tasks = [
            asyncio.create_task(self.run_channel(name, token, stop=stop))
            for name, token in self.candidates()
        ]
        if not tasks:
            return
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for t in tasks:
                t.cancel()
            raise
