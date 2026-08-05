"""Comm routes: vault/secrets, integrations, channels, webhooks.

Moved verbatim from daemon/app.py's create_app; closure-local state is
reached through ``d`` (see the deps object built in create_app).
"""

from __future__ import annotations

import json

from fastapi import FastAPI, HTTPException, Request
from sqlmodel import select
from typing import Any

from ..schemas import (
    ChannelCreate,
    ChatBody,
    CommThreadSendBody,
    IntegrationConfigBody,
    IntegrationCreate,
    IntegrationEnableBody,
    NotifyBody,
    SecretSet,
    WebhookCreate,
)
# Referenced as a module-global at call time so tests can monkeypatch
# ``iron_jarvis.daemon.routes.comm.run_chat_turn`` (same idiom as chat.py);
# production normally rides d.inbound_poller.chat_turn (the injected seam).
from ..chat_turn import run_chat_turn
from ...core.db import session_scope


def _channel_config_problem(ctype: str, config: dict) -> str | None:
    """A human, actionable message when a channel has no working delivery method
    yet, or ``None`` when it is good to go. Catches a misconfigured channel at
    ADD time (with a tip) instead of silently saving one that only fails later at
    test time. ``config`` is the post-processing dict (secret fields already
    resolved to ``<key>_secret``)."""
    if ctype == "slack":
        if config.get("webhook_url") or (config.get("token_secret") and config.get("channel")):
            return None
        return (
            "Slack has no way to deliver messages yet. Add ONE of these: an "
            "Incoming Webhook URL (simplest — Slack app → Incoming Webhooks → Add "
            "New Webhook), OR a Bot token plus a channel (e.g. #general). Tip: use "
            "the one-paste app manifest above to create the app in seconds."
        )
    if ctype == "discord":
        if config.get("webhook_url"):
            return None
        return (
            "Discord needs an Incoming Webhook URL — in Discord: the channel → "
            "Edit Channel → Integrations → Webhooks → New Webhook, then Copy URL."
        )
    if ctype == "telegram":
        if config.get("token_secret") and config.get("chat_id"):
            return None
        return (
            "Telegram needs a Bot token (from @BotFather) and your Chat ID "
            "(message @userinfobot to find it)."
        )
    if ctype == "email":
        if not (config.get("host") and config.get("from_addr") and config.get("to_addr")):
            return "Email needs at least an SMTP host, a From address, and a Send-to address."
        # Two-way (inbound) email READS a mailbox over IMAP, which SMTP settings
        # alone can't do — require an IMAP host + a mailbox password, fail-closed
        # like the other per-type checks. Outbound-only email is unaffected.
        if config.get("inbound_enabled") and not (
            config.get("imap_host") and config.get("password_secret")
        ):
            return (
                "Email two-way (inbound) needs an IMAP host and a mailbox "
                "password. Add an IMAP host (e.g. imap.gmail.com) and a password "
                "so Iron Jarvis can read the inbox, or turn two-way off."
            )
        return None
    return None


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""
    @app.get("/vault")
    def vault() -> dict[str, Any]:
        return {"providers": d.platform.vault.providers()}

    @app.get("/secrets")
    def list_secrets() -> dict[str, Any]:
        return {"secrets": d.platform.secrets.list()}

    @app.post("/secrets")
    def set_secret(body: SecretSet) -> dict[str, Any]:
        rec = d.platform.secrets.set(
            body.name, body.value, kind=body.kind, description=body.description
        )
        return {"name": rec.name, "kind": rec.kind}

    @app.delete("/secrets/{name}")
    def delete_secret(name: str) -> dict[str, Any]:
        return {"deleted": d.platform.secrets.delete(name)}

    @app.get("/integrations")
    def list_integrations() -> dict[str, Any]:
        return {"integrations": d.platform.integrations.list_status()}

    @app.post("/integrations")
    def add_integration(body: IntegrationCreate) -> dict[str, Any]:
        """Add a custom REST integration (base URL + optional bearer token).

        Registers it live (so it appears + tests immediately), stores the token
        in the vault, and persists the spec to config so it survives restart.
        """
        import re as _re

        from ...integrations.base import IntegrationSpec
        from ...integrations.builtin import REST_SPEC, RestApiIntegration

        iid = _re.sub(r"[^a-z0-9_]+", "_", (body.name or "").strip().lower()).strip("_")
        if not iid:
            raise HTTPException(status_code=400, detail="integration name is required")
        if not (body.base_url or "").strip():
            raise HTTPException(status_code=400, detail="base URL is required")
        if d.platform.integrations.get_spec(iid) is not None:
            raise HTTPException(status_code=400, detail=f"'{iid}' already exists")

        d.platform.integrations.register(
            IntegrationSpec(
                id=iid,
                kind="rest",
                display_name=body.name.strip(),
                description=(body.description or "").strip(),
                required_secrets=[],
                config_schema=REST_SPEC.config_schema,
            ),
            lambda cfg, resolver: RestApiIntegration(cfg, resolver),
        )
        config = {"base_url": body.base_url.strip()}
        if (body.auth_token or "").strip():
            sname = f"integration_{iid}_token"
            d.platform.secrets.set(sname, body.auth_token.strip(), kind="token")
            config["auth_secret"] = sname
        d.platform.integrations.configure(iid, config)
        d.platform.integrations.enable(iid, True)

        customs = [c for c in (d.platform.config.custom_integrations or []) if c.get("id") != iid]
        customs.append({"id": iid, "name": body.name.strip(), "description": (body.description or "").strip()})
        d.platform.config.custom_integrations = customs
        d._persist_config(["custom_integrations"])
        return {"id": iid, "added": True}

    @app.post("/integrations/{iid}/enable")
    def enable_integration(iid: str, body: IntegrationEnableBody) -> dict[str, Any]:
        if d.platform.integrations.get_spec(iid) is None:
            raise HTTPException(status_code=404, detail="unknown integration")
        d.platform.integrations.enable(iid, body.enabled)
        return {"id": iid, "enabled": body.enabled}

    @app.post("/integrations/{iid}/configure")
    def configure_integration(iid: str, body: IntegrationConfigBody) -> dict[str, Any]:
        if d.platform.integrations.get_spec(iid) is None:
            raise HTTPException(status_code=404, detail="unknown integration")
        d.platform.integrations.configure(iid, body.config)
        return {"id": iid, "configured": True}

    @app.post("/integrations/{iid}/test")
    def test_integration(iid: str) -> dict[str, Any]:
        if d.platform.integrations.get_spec(iid) is None:
            raise HTTPException(status_code=404, detail="unknown integration")
        return d.platform.integrations.test(iid, d.platform.secrets.get)

    @app.get("/comm/channels")
    def comm_channels() -> dict[str, Any]:
        # Cross-reference the live channels with their configured type so the UI
        # can label + delete them (built-in mock/console have no config row).
        configured = (d.platform.config.comm or {}).get("channels") or {}
        out = []
        for name in d.platform.notifier.channels():
            spec = configured.get(name) or {}
            # Built-ins have no config row; their TYPE identity lives on the
            # live channel instance (this-pc -> "desktop"), not in config.
            live = d.platform.notifier.get(name)
            # v1.136.0 — two-way/chat badging, read from the LIVE channel so
            # the verdict is the one the poller actually uses. Only the COUNT
            # of allowlisted senders leaves the daemon, never the ids.
            try:
                inbound_on = bool(live is not None and live.inbound_enabled())
                chat_on = bool(live is not None and live.chat_enabled())
                allowed_count = len(live.allowed_senders()) if live is not None else 0
            except Exception:  # noqa: BLE001 — a config quirk never breaks the list
                inbound_on, chat_on, allowed_count = False, False, 0
            out.append(
                {
                    "name": name,
                    "type": spec.get("type") or getattr(live, "name", None) or name,
                    # v1.118.0 — the row's honesty surface: built-ins need no
                    # config; configured rows carry their LAST REAL test result
                    # so "green" provably means "worked", not "saved".
                    "builtin": name not in configured,
                    "last_test_ok": spec.get("last_test_ok"),
                    "last_test_at": spec.get("last_test_at"),
                    "events": spec.get("events") or [],
                    "inbound_enabled": inbound_on,
                    "chat_enabled": chat_on,
                    "allowed_senders_count": allowed_count,
                }
            )
        return {"channels": out}

    @app.get("/comm/channel-types")
    def comm_channel_types() -> dict[str, Any]:
        return {
            "types": [
                {
                    "type": t,
                    "fields": fields,
                    "manifest": d._CHANNEL_MANIFESTS.get(t),
                    "manifest_help": (
                        "Create the Slack app in one paste: api.slack.com/apps → "
                        "Create New App → From an app manifest → paste this JSON, "
                        "then install it to your workspace and copy the Bot token "
                        "(plus the Signing Secret from Basic Information for "
                        "two-way events — point Slack's Event Subscriptions "
                        "request URL at /comm/slack/events/<channel-name> once "
                        "this machine is reachable, e.g. via a Tailscale funnel)."
                        if t == "slack"
                        else None
                    ),
                }
                for t, fields in d._CHANNEL_TYPE_FIELDS.items()
            ]
        }

    @app.post("/comm/channels")
    def add_comm_channel(body: ChannelCreate) -> dict[str, Any]:
        """Add a comm channel (Slack/Discord/Telegram/email).

        Secret fields go to the ENCRYPTED vault (referenced by ``<field>_secret``
        in the channel config); non-secret fields live in config.comm. The
        channel is added LIVE (so a Send-test works at once) and persisted so it
        survives restart.
        """
        from ...comm import CHANNEL_TYPES, httpx_get, httpx_post

        ctype = (body.type or "").strip().lower()
        if ctype not in d._CHANNEL_TYPE_FIELDS or ctype not in CHANNEL_TYPES:
            raise HTTPException(status_code=400, detail=f"unknown channel type '{ctype}'")
        import re as _re

        name = (body.name or "").strip()
        if not _re.match(r"^[a-zA-Z][a-zA-Z0-9_-]{0,39}$", name):
            raise HTTPException(status_code=400, detail="invalid channel name")

        config: dict[str, Any] = {"type": ctype}
        for field in d._CHANNEL_TYPE_FIELDS[ctype]:
            key = field["key"]
            value = (body.config or {}).get(key)
            if value in (None, ""):
                continue
            if field.get("secret"):
                secret_name = f"channel_{name}_{key}"
                d.platform.secrets.set(secret_name, str(value), kind="token")
                config[f"{key}_secret"] = secret_name
            elif key == "allowed_senders":
                # Comma-separated ids -> the list the fail-closed allowlist reads.
                config[key] = [s.strip() for s in str(value).split(",") if s.strip()]
            elif key in ("inbound_enabled", "chat_enabled"):
                config[key] = str(value).strip().lower() in ("1", "true", "yes", "on")
            else:
                config[key] = value

        # CHAT IMPLIES LISTENING: normalize at save time so stored config and
        # the effective verdict can never disagree. Without this, chat=true +
        # two-way=false would be stored as-is, GET /comm/channels (which reads
        # the EFFECTIVE state off the live channel) would report chat OFF, and
        # the edit form — seeded from GET — would silently persist chat=false
        # on the next save (the v1.127 "control that reads as a setting must
        # BE the setting" bug class).
        if config.get("chat_enabled") is True:
            config["inbound_enabled"] = True

        # Reject a channel with no working delivery method up front, with a tip —
        # far better than silently saving one that only fails at test time. (Edit
        # re-submits here, so this also guards a fix that is still incomplete.)
        problem = _channel_config_problem(ctype, config)
        if problem:
            raise HTTPException(status_code=400, detail=problem)

        # Per-destination event routing (v1.118.0): an optional list of event
        # types this destination receives; absent/empty = everything (the old
        # behaviour). Validated against the alert set so a typo can't silently
        # subscribe a destination to nothing.
        raw_events = (body.config or {}).get("events")
        if raw_events:
            from ...comm.notifier import DEFAULT_ALERT_EVENTS

            wanted = [str(e).strip() for e in raw_events if str(e).strip()]
            unknown = [e for e in wanted if e not in DEFAULT_ALERT_EVENTS]
            if unknown:
                raise HTTPException(
                    status_code=400,
                    detail=f"unknown alert event(s): {', '.join(unknown)} — "
                           f"valid: {', '.join(sorted(DEFAULT_ALERT_EVENTS))}",
                )
            config["events"] = wanted

        # Persist to config.comm.channels (survives restart) + atomic write.
        comm = dict(d.platform.config.comm or {})
        channels = dict(comm.get("channels") or {})
        channels[name] = config
        comm["channels"] = channels
        d.platform.config.comm = comm
        d._persist_config(["comm"])

        # Add it LIVE so a test message works immediately (no restart needed).
        channel = CHANNEL_TYPES[ctype](
            config,
            http_post=httpx_post,
            http_get=httpx_get,
            secret_resolver=d.platform.secrets.get,
        )
        d.platform.notifier.add_channel(name, channel)
        return {"name": name, "type": ctype, "added": True}

    @app.delete("/comm/channels/{name}")
    def delete_comm_channel(name: str) -> dict[str, Any]:
        removed = d.platform.notifier.remove_channel(name)
        comm = dict(d.platform.config.comm or {})
        channels = dict(comm.get("channels") or {})
        cfg = channels.pop(name, None)
        if cfg is not None:
            comm["channels"] = channels
            d.platform.config.comm = comm
            d._persist_config(["comm"])
            # Best-effort: drop any vault secrets this channel owned.
            for key, val in cfg.items():
                if key.endswith("_secret") and isinstance(val, str):
                    try:
                        d.platform.secrets.delete(val)
                    except Exception:  # noqa: BLE001
                        pass
        return {"name": name, "removed": removed or cfg is not None}

    @app.post("/comm/threads/{thread_id}/send")
    async def comm_thread_send(thread_id: str, body: CommThreadSendBody) -> dict[str, Any]:
        """Desktop reply fan-out (v1.136.0): the dashboard composer for a
        DAEMON-owned comm thread posts here instead of autosaving via PUT.

        Runs EXACTLY the inbound free-form pipeline (append user → history →
        chat turn → append reply) and ALSO sends the reply out the thread's
        bound destination, chunked to its size cap — a desktop reply in a
        phone conversation lands on the phone too. ``escalate: true`` acks
        immediately and runs the supervised session in the BACKGROUND (the
        HTTP request never blocks on an agent); the summary arrives on the
        thread via chat.thread_updated. Response: the chat-turn dict +
        ``{"sent": bool}``.
        """
        from ...comm.inbound import ESCALATE_ACK, RATE_LIMIT_REPLY
        from ...core.models import ChatThreadRecord

        store = d.comm_thread_store
        poller = d.inbound_poller
        text = (body.text or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="text is required")
        with session_scope(d.platform.engine) as db:
            rec = db.get(ChatThreadRecord, thread_id)
            # Migrated rows read owner as NULL — that's "user" (coalesced).
            owner = (rec.owner or "user") if rec is not None else ""
        if rec is None:
            raise HTTPException(status_code=404, detail="no such chat thread")
        if owner != "daemon":
            raise HTTPException(
                status_code=409,
                detail="this thread is not managed by a messaging destination — "
                "save it from the chat page like any other thread",
            )
        binding = store.thread_channel(thread_id)
        if binding is None:
            raise HTTPException(
                status_code=409,
                detail="This conversation is no longer linked to a destination.",
            )
        channel_name, sender_id = binding
        ch = d.platform.notifier.get(channel_name)
        if ch is None:
            raise HTTPException(
                status_code=409,
                detail=f"the destination '{channel_name}' no longer exists — "
                "this conversation has nowhere to deliver",
            )
        if not ch.has_credentials():
            raise HTTPException(
                status_code=409,
                detail=f"the destination '{channel_name}' has no working "
                "credentials — fix it on the Channels page first",
            )
        # SHARED per-identity flood guard (same counter the poller uses).
        if not poller.rate_ok(channel_name, sender_id):
            raise HTTPException(status_code=429, detail=RATE_LIMIT_REPLY)

        try:
            store.append(thread_id, "user", text)
        except ValueError:
            # Vanished between the read above and the append (dashboard
            # delete racing this request) — honest 404, nothing half-done.
            raise HTTPException(status_code=404, detail="no such chat thread")
        history = store.history_body(thread_id, limit=30) or [
            {"role": "user", "content": text}
        ]
        # The injected seam when wired (production: run_chat_turn; tests may
        # swap either the poller's callable or this module's global).
        turn = poller.chat_turn or run_chat_turn
        personas = poller.personas or d._PERSONAS

        def _append_reply(content: str) -> None:
            try:
                store.append(thread_id, "assistant", content)
            except Exception:  # noqa: BLE001 — the reply must still deliver
                pass

        try:
            result = await turn(d.platform, personas, ChatBody(messages=history, auto_tools=True))
        except HTTPException as exc:
            # Mirror the inbound pipeline: the honest error IS the reply —
            # appended to the thread, delivered to the phone, rendered by the
            # dashboard like a normal turn (not surfaced as a raw 4xx/5xx).
            reply = f"I hit a problem: {exc.detail}"
            _append_reply(reply)
            sent = await poller.send_chunked(ch, reply, chat_id=sender_id)
            return {
                "reply": reply,
                "provider": "",
                "model": "",
                "tools_used": [],
                "escalate": False,
                "error": str(exc.detail),
                "sent": sent,
            }

        if result.get("escalate"):
            _append_reply(ESCALATE_ACK)
            sent = await poller.send_chunked(ch, ESCALATE_ACK, chat_id=sender_id)
            task = poller.recap_task(history, text)
            # v1.139.0 informed delegation — MIRRORS comm/inbound.py's escalate
            # path: a turn that NAMED who should take it (``escalate_agent``,
            # re-validated through the roster by ``_escalate_plan``) overrides
            # the hard-coded supervisor default; builtin + dynamic targets are
            # honored (a dynamic record's pinned provider/model included),
            # remote targets keep the supervisor default for the same rationale
            # (a remote ask returns bare text, not a supervised session), and
            # None keeps the default byte-for-byte.
            agent_type, dyn_def, esc_provider, esc_model = poller._escalate_plan(result)
            _spawn_kwargs: dict[str, Any] = {}
            if esc_provider:
                _spawn_kwargs["provider"] = esc_provider
            if esc_model:
                _spawn_kwargs["model"] = esc_model
            session = await d.orchestrator.create_session(
                task, agent_type, **_spawn_kwargs
            )

            async def _finish() -> None:
                try:
                    if dyn_def is not None:
                        s = await poller._run_dynamic_session(session, dyn_def)
                    else:
                        s = await d.orchestrator.run_session(session.id)
                    summary = (s.summary or "(no result)").strip()
                except Exception as exc:  # noqa: BLE001 — deliver, don't vanish
                    summary = f"I hit a problem: {type(exc).__name__}: {exc}"
                _append_reply(summary)
                await poller.send_chunked(ch, summary, chat_id=sender_id)

            # BACKGROUND: the HTTP request returns the ack-shaped response now;
            # the session summary lands via chat.thread_updated when done.
            d._spawn_bg(session.id, _finish())
            return {**result, "reply": ESCALATE_ACK, "session_id": session.id, "sent": sent}

        reply = str(result.get("reply") or "").strip() or "(no reply)"
        _append_reply(reply)
        sent = await poller.send_chunked(ch, reply, chat_id=sender_id)
        return {**result, "sent": sent}

    # Slack redelivers an event on any non-2xx reply or network blip. This bounded
    # per-process ring dedups by Slack's `event_id` so a redelivery can NOT
    # double-fire an autonomous action. (Calendar/email use durable at-most-once
    # cursors; Slack's retry window is short, so an in-memory ring is sufficient.)
    from collections import deque as _deque

    _slack_seen_order: _deque = _deque()
    _slack_seen: set[str] = set()

    def _slack_event_is_new(event_id: str) -> bool:
        if not event_id:
            return True  # can't dedup without an id — process (rare)
        if event_id in _slack_seen:
            return False
        _slack_seen.add(event_id)
        _slack_seen_order.append(event_id)
        while len(_slack_seen_order) > 2048:
            _slack_seen.discard(_slack_seen_order.popleft())
        return True

    @app.post("/comm/slack/events/{name}")
    async def slack_events(name: str, request: Request) -> dict[str, Any]:
        """Slack Events API receiver for channel ``name``.

        The path is token-exempt because Slack cannot carry our bearer — the
        SLACK SIGNATURE is the auth: fail-closed on the channel's stored
        signing secret (v0 HMAC-SHA256 over "v0:{ts}:{body}", ±5 min replay
        window). Handles Slack's url_verification challenge, then publishes
        real events onto the event bus for the rest of the platform to react.
        """
        import hashlib
        import hmac as _hmac
        import time as _time

        raw = await request.body()
        cfg = (((d.platform.config.comm or {}).get("channels")) or {}).get(name) or {}
        if cfg.get("type") != "slack":
            raise HTTPException(status_code=404, detail="no such slack channel")
        secret_name = cfg.get("signing_secret_secret")
        signing = d.platform.secrets.get(secret_name) if secret_name else None
        if not signing:
            raise HTTPException(
                status_code=403,
                detail="this channel has no signing secret configured — add it "
                "on the Channels page before enabling Event Subscriptions",
            )
        ts = request.headers.get("X-Slack-Request-Timestamp") or ""
        sig = request.headers.get("X-Slack-Signature") or ""
        try:
            if abs(_time.time() - float(ts)) > 300:
                raise HTTPException(status_code=403, detail="stale slack timestamp")
        except ValueError:
            raise HTTPException(status_code=403, detail="bad slack timestamp")
        base = f"v0:{ts}:".encode() + raw
        expected = "v0=" + _hmac.new(signing.encode(), base, hashlib.sha256).hexdigest()
        if not _hmac.compare_digest(expected, sig):
            raise HTTPException(status_code=403, detail="invalid slack signature")

        body = json.loads(raw or b"{}")
        if body.get("type") == "url_verification":
            return {"challenge": body.get("challenge", "")}
        event = body.get("event") or {}
        # Observability: keep emitting the raw event for anything watching the
        # bus (dashboard feed / debugging). The real trigger wiring is below.
        await d.platform.event_bus.publish(
            "slack.event",
            {
                "channel_name": name,
                "event_type": str(event.get("type") or ""),
                "text": str(event.get("text") or "")[:2000],
                "user": str(event.get("user") or ""),
                "slack_channel": str(event.get("channel") or ""),
            },
        )

        # CX-05 — turn a real inbound Slack event into agent work. A plain user
        # message (``message`` with no subtype) or an ``@mention`` becomes a
        # trigger; edits/joins/bot chatter are a no-op ack. Ignore bot messages
        # (loop protection: never react to our own posts). We ACK Slack FAST
        # ({"ok": True}) and never raise a 500 here — a 500 makes Slack retry-storm.
        from ...comm import InboundMessage
        from ...core.events import EventType
        from ...core.logging import get_logger

        _log = get_logger("comm.slack")
        etype = str(event.get("type") or "")
        user = str(event.get("user") or "")
        if (
            body.get("type") == "event_callback"
            and etype in ("message", "app_mention")
            and not event.get("subtype")
            and user
            and not event.get("bot_id")
            # Idempotency: a Slack redelivery of an already-processed event_id must
            # not fire the action twice (duplicate autonomous work breaks trust).
            and _slack_event_is_new(str(body.get("event_id") or ""))
        ):
            channel_type = str(event.get("channel_type") or "")
            slack_channel = str(event.get("channel") or "")
            msg = InboundMessage(
                sender_id=user,
                text=str(event.get("text") or ""),
                update_id=None,
                reply_to=(user if channel_type == "im" else slack_channel),
                is_bot=bool(event.get("bot_id")),
            )
            ch = d.platform.notifier.get(name)
            if ch is not None and channel_type == "im":
                # DM: reuse the FULL inbound pipeline (fail-closed allowlist +
                # command / reflex / session + reply). It may run a whole agent
                # session, so run it in the BACKGROUND and ack Slack immediately
                # (3s deadline) rather than block this request.
                async def _run_dm() -> None:
                    try:
                        await d.inbound_poller._handle(name, ch, msg)
                    except Exception:  # noqa: BLE001 — never surface as a 500 to Slack
                        _log.exception("slack DM handling failed on %r", name)

                d._spawn_bg(f"slack-events-{name}", _run_dm())
            elif ch is not None and ch.is_authorized(user):
                # Channel message / @mention from an AUTHORIZED sender: fire the
                # "slack" reflex rules only — NO auto-reply into a shared channel
                # (that would broadcast agent output to non-allowlisted members).
                # on_slack is bounded (it creates records + backgrounds the run),
                # so awaiting it keeps well within Slack's ack window.
                await d.platform.event_bus.publish(
                    EventType.COMM_RECEIVED,
                    {
                        "channel": name,
                        "sender": user,
                        "slack_channel": slack_channel,
                        "text": msg.text[:2000],
                    },
                )
                try:
                    await app.state.reflex_router.on_slack(
                        text=msg.text, channel=slack_channel, sender=user
                    )
                except Exception:  # noqa: BLE001 — a bad rule never 500s Slack
                    _log.exception("slack channel reflex failed on %r", name)
        return {"ok": True}

    @app.post("/comm/telegram/detect-chat")
    def telegram_detect_chat(body: dict) -> dict[str, Any]:
        """The chat-id wall, removed (v1.118.0): paste the bot token, send the
        bot any message, and this reads ``getUpdates`` ONCE to list the chats
        that messaged it — the UI polls while showing "waiting for your
        message…" and fills the id on the first hit. The token rides the body
        over loopback exactly like the add-channel form's secret fields; it is
        vaulted at save time, not here."""
        from ...comm import httpx_get

        token = str((body or {}).get("token") or "").strip()
        if not token:
            raise HTTPException(status_code=400, detail="bot token required")
        try:
            resp = httpx_get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                {"timeout": 0, "limit": 20},
            )
        except Exception as exc:  # noqa: BLE001 — offline is an answer, not a 500
            raise HTTPException(
                status_code=502, detail=f"could not reach Telegram: {exc}"
            )
        data = resp if isinstance(resp, dict) else {}
        if hasattr(resp, "json"):
            try:
                data = resp.json()
            except Exception:  # noqa: BLE001
                data = {}
        if not data.get("ok"):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Telegram rejected that token — check it against @BotFather "
                    f"({(data.get('description') or 'no detail')})"
                ),
            )
        chats: dict[int, str] = {}
        for upd in data.get("result") or []:
            msg = upd.get("message") or upd.get("edited_message") or {}
            chat = msg.get("chat") or {}
            cid = chat.get("id")
            if cid is None:
                continue
            label = (
                chat.get("title")
                or " ".join(
                    x for x in (chat.get("first_name"), chat.get("last_name")) if x
                )
                or chat.get("username")
                or str(cid)
            )
            chats[int(cid)] = str(label)
        return {
            "chats": [{"id": cid, "label": label} for cid, label in chats.items()],
        }

    @app.post("/comm/channels/{name}/test")
    def test_comm_channel(name: str) -> dict[str, Any]:
        """Send a REAL test message through one channel and report honestly —
        so 'configured' provably means 'working' before the user relies on it."""
        if d.platform.notifier.get(name) is None:
            raise HTTPException(status_code=404, detail=f"no channel named '{name}'")
        results = d.platform.notifier.notify(
            "✅ Iron Jarvis test — this channel is wired up and working.", [name]
        )
        r = results.get(name) or {"ok": False, "detail": "channel produced no result"}
        # Persist the outcome on the CONFIGURED row (v1.118.0) so the state dot
        # survives reloads — a Slack app that died in March must not look green
        # in July. Built-ins have no config row; their state is definitional.
        comm = dict(d.platform.config.comm or {})
        channels = dict(comm.get("channels") or {})
        if name in channels:
            from ...core.ids import utcnow as _now

            row = dict(channels[name])
            row["last_test_ok"] = bool(r.get("ok"))
            row["last_test_at"] = _now().isoformat()
            channels[name] = row
            comm["channels"] = channels
            d.platform.config.comm = comm
            d._persist_config(["comm"])
        return {"name": name, **r}

    @app.post("/comm/notify")
    def comm_notify(body: NotifyBody) -> dict[str, Any]:
        return d.platform.notifier.notify(body.message, body.channels)

    @app.get("/webhooks")
    def list_webhooks() -> dict[str, Any]:
        from ...webhooks.models import WebhookRecord

        with session_scope(d.platform.engine) as db:
            rows = list(db.exec(select(WebhookRecord)))
        return {"webhooks": [r.model_dump() for r in rows]}

    @app.post("/webhooks")
    def create_webhook(body: WebhookCreate) -> dict[str, Any]:
        secret = d.platform.secrets.get(body.secret_name) if body.secret_name else None
        if body.direction == "outbound":
            if not body.target_url:
                raise HTTPException(status_code=400, detail="outbound needs target_url")
            d.platform.outbound_webhooks.register(
                body.slug,
                body.target_url,
                body.event_types,
                secret=secret,
                secret_name=body.secret_name or None,  # persist the real vault key
            )
        else:  # inbound: publish the event AND fire any bound reflex rules.
            # v1.122.0 fix: the create-time handler used to only publish, so a
            # freshly created webhook silently skipped reflexes until the next
            # daemon restart installed the lifespan handler — "create webhook,
            # create rule, POST, nothing happens" with a 200 ack.
            async def _handler(payload: dict, _slug: str = body.slug) -> dict[str, Any]:
                await d.platform.event_bus.publish(
                    "webhook.received", {"slug": _slug, "body": payload}
                )
                fired: list[Any] = []
                try:
                    router = getattr(app.state, "reflex_router", None)
                    if router is not None:
                        fired = await router.on_webhook(_slug, payload)
                except Exception:  # noqa: BLE001 — a reflex failure never breaks the ack
                    from ...core.logging import get_logger

                    get_logger("webhooks").exception(
                        "reflex on_webhook failed for %r", _slug
                    )
                return {"ok": True, "slug": _slug, "reflexes_fired": len(fired)}

            d.platform.inbound_webhooks.register(
                body.slug, _handler, secret=secret, secret_name=body.secret_name or None
            )
        return {"slug": body.slug, "direction": body.direction}

    @app.post("/webhooks/{slug}")
    async def inbound_webhook(slug: str, request: Request) -> dict[str, Any]:
        raw = await request.body()
        sig = request.headers.get("X-IronJarvis-Signature") or request.headers.get(
            "X-Signature"
        )
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        return await d.platform.inbound_webhooks.dispatch(
            slug, body, raw=raw, signature=sig
        )
