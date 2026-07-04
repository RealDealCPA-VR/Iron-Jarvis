"""Slack SOCKET MODE — two-way Slack with ZERO internet exposure.

Instead of Slack calling us (which needs a public URL), the daemon dials OUT:
``apps.connections.open`` (app-level ``xapp-`` token) returns a ``wss://`` URL,
we connect, Slack streams event envelopes down the socket we initiated, and we
ACK each envelope. No inbound port, no tunnel, no exposure — the same
outbound-only posture as Telegram polling, but real-time.

Safety mirrors the inbound poller exactly (it REUSES ``InboundPoller._handle``):
bot-loop protection, FAIL-CLOSED sender allowlist, private (DM) chats only,
supervised sessions, bounded replies. Transports are injectable so the test
suite runs fully offline.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable

from .base import InboundMessage

log = logging.getLogger(__name__)

_OPEN_URL = "https://slack.com/api/apps.connections.open"


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
    ) -> None:
        self.poller = poller
        self.notifier = notifier
        self.secrets = secret_resolver
        self.comm_config = comm_config
        self._open_ws = open_ws or _default_open_ws
        self._ws_connect = ws_connect

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

    # -- envelope processing (pure-ish; unit-testable) -------------------------
    async def process_envelope(self, name: str, envelope: dict) -> dict[str, Any] | None:
        """Handle ONE Socket Mode envelope; returns the handler result or None.

        Only private DMs (``channel_type == "im"``), plain messages (no
        subtype), and non-bot senders reach the poller pipeline — which then
        applies its own fail-closed allowlist + supervision.
        """
        if envelope.get("type") != "events_api":
            return None
        event = ((envelope.get("payload") or {}).get("event")) or {}
        if event.get("type") != "message" or event.get("subtype"):
            return None
        if event.get("channel_type") != "im":
            return None  # private-only, like the Telegram leg
        ch = self.notifier.get(name)
        if ch is None:
            return None
        user = str(event.get("user") or "")
        msg = InboundMessage(
            sender_id=user,
            text=str(event.get("text") or ""),
            update_id=None,
            is_bot=bool(event.get("bot_id")),
            # Reply to the USER id — chat.postMessage(channel=U…) opens the DM,
            # and it satisfies the poller's private-chat guard (reply_to ==
            # sender_id), mirroring Telegram's private-chat semantics.
            reply_to=user,
        )
        return await self.poller._handle(name, ch, msg)  # noqa: SLF001 — by design

    # -- the pump --------------------------------------------------------------
    async def run_channel(self, name: str, app_token: str, *, stop: asyncio.Event) -> None:
        """Connect + pump envelopes for one channel; reconnects with backoff."""
        try:
            import websockets
        except Exception:  # pragma: no cover — bundled via uvicorn[standard]
            log.error("slack socket mode needs the 'websockets' package")
            return
        connect = self._ws_connect or websockets.connect
        delay = 2.0
        while not stop.is_set():
            try:
                url = await asyncio.to_thread(self._open_ws, app_token)
                async with connect(url) as ws:
                    delay = 2.0  # healthy connection resets the backoff
                    log.info("slack socket mode connected for channel %r", name)
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
