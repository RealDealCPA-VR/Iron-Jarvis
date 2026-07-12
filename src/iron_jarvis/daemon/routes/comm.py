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
    IntegrationConfigBody,
    IntegrationCreate,
    IntegrationEnableBody,
    NotifyBody,
    SecretSet,
    WebhookCreate,
)
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
        if config.get("host") and config.get("from_addr") and config.get("to_addr"):
            return None
        return "Email needs at least an SMTP host, a From address, and a Send-to address."
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
            out.append({"name": name, "type": (configured.get(name) or {}).get("type", name)})
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
            elif key == "inbound_enabled":
                config[key] = str(value).strip().lower() in ("1", "true", "yes", "on")
            else:
                config[key] = value

        # Reject a channel with no working delivery method up front, with a tip —
        # far better than silently saving one that only fails at test time. (Edit
        # re-submits here, so this also guards a fix that is still incomplete.)
        problem = _channel_config_problem(ctype, config)
        if problem:
            raise HTTPException(status_code=400, detail=problem)

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
        return {"ok": True}

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
        else:  # inbound: a default handler that emits a webhook.received event
            async def _handler(payload: dict, _slug: str = body.slug) -> dict[str, Any]:
                await d.platform.event_bus.publish(
                    "webhook.received", {"slug": _slug, "body": payload}
                )
                return {"ok": True, "slug": _slug}

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
