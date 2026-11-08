"""Comm routes: vault/secrets, integrations, channels, webhooks.

Moved verbatim from daemon/app.py's create_app; closure-local state is
reached through ``d`` (see the deps object built in create_app).
"""

from __future__ import annotations

import json
import re

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
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


def two_way_note(ctype: str, config: dict) -> str | None:
    """v1.427.6: why a destination with two-way ON still cannot hear the user,
    in one sentence — ``None`` when two-way is off or can work. The save keeps
    such a destination (its outbound alerts still work); the Notifications row
    and the save's answer say this sentence instead of "added ✓" alone. The
    user's own report: a Slack destination that sent alerts fine and never
    heard a reply — its allowlist was empty, so the Socket Mode pump never
    even dialled (``SlackSocketMode.candidates``)."""
    if not config.get("inbound_enabled") or ctype not in ("slack", "telegram", "email"):
        return None
    if ctype == "email":
        # v1.446.0: the page now has the email two-way boxes; an empty list
        # lets nobody in (EmailChannel.is_authorized fails closed).
        if not config.get("allowed_senders"):
            return (
                "Listening for incoming mail is on, but no one is on the allowed senders "
                "list, so Jarvis won't act on any mail. Add the email addresses that may "
                "write to Jarvis."
            )
        return None
    if not config.get("allowed_senders"):
        if ctype == "slack":
            return (
                "Two-way is on, but no one is on the allowlist, so Jarvis isn't "
                "listening. Add your Slack member ID: in Slack, click your profile "
                "picture → Profile → ⋯ → Copy member ID."
            )
        return (
            "Two-way is on, but no one is on the allowed senders list, so Jarvis "
            "isn't listening. Add your Telegram user ID (message @userinfobot to find it)."
        )
    if ctype == "slack":
        if not config.get("app_token_secret") and not config.get("signing_secret_secret"):
            return (
                "Two-way is on, but Jarvis has no way to hear Slack: add the App-level "
                "token (xapp-…, from Basic Information → App-Level Tokens, with the "
                "connections:write scope)."
            )
        if not config.get("token_secret"):
            return (
                "Two-way is on, but Jarvis can't answer you in Slack: add the Bot "
                "token (xoxb-…, from OAuth & Permissions)."
            )
    return None


#: v1.444.0: the words for a two-way Slack destination that is connected and
#: has heard nothing yet. Slack only sends what the app subscribes to, and an
#: app made from the older manifest got direct messages only.
NOTHING_HEARD_WORDS = (
    "Connected to Slack, but Slack hasn't sent Jarvis any message yet. If you "
    "wrote to Jarvis and got no answer, your Slack app needs the newer setup: "
    "open it at api.slack.com/apps → App Manifest, paste the manifest from "
    "Add a destination → Slack, Save, then reinstall the app when Slack asks."
)
#: ...and once it has heard: where Jarvis answers.
LISTENING_WORDS = (
    "Listening. Jarvis answers your direct messages, your messages in {channel} "
    "(in a thread; anyone in that channel can read it), and an @mention "
    "anywhere else privately, in your direct messages."
)
LISTENING_DM_ONLY_WORDS = (
    "Listening. Jarvis answers your direct messages (Apps → Iron Jarvis in Slack)."
)


def listening_status(name: str, ctype: str, config: dict) -> dict[str, Any] | None:
    """v1.444.0: what a two-way Slack destination's pump has HEARD, for the
    Notifications row. ``None`` for anything else or when two-way is off; the
    times are what the pump recorded in this daemon (in memory, so a restart
    starts fresh). ``tone`` is ``warn`` when Jarvis cannot hear the user in
    the channel or Slack has sent nothing since it connected."""
    if ctype != "slack" or not config.get("inbound_enabled"):
        return None
    try:
        from ...comm.slack_socket import LISTENING
    except Exception:  # noqa: BLE001 — a row never fails over its status line
        return None
    st = dict(LISTENING.get(name) or {})
    connected = st.get("connected_at")
    note = str(st.get("channel_note") or "")
    channel = str(config.get("channel") or "").strip()
    if channel and not channel.startswith("#") and not channel[:1].isupper():
        channel = f"#{channel}"
    if not connected:
        words, tone = "", "quiet"
    elif note:
        words, tone = note, "warn"
    elif not st.get("last_event_at"):
        words, tone = NOTHING_HEARD_WORDS, "warn"
    elif st.get("own_channel") and channel:
        words, tone = LISTENING_WORDS.format(channel=channel), "quiet"
    else:
        words, tone = LISTENING_DM_ONLY_WORDS, "quiet"
    return {
        "connected_at": connected,
        "last_event_at": st.get("last_event_at"),
        "last_message_at": st.get("last_message_at"),
        "channel_found": bool(st.get("own_channel")) if connected else None,
        "words": words,
        "tone": tone,
    }


#: v1.446.0 (ease-of-use review item 13): the flag the Notifications page's
#: Add puts in ``config`` so a name already in use is refused, not replaced.
#: Edit and older callers send no flag and keep the replace-on-same-name rule.
NEW_FLAG = "new_destination"
NAME_RULE_REFUSAL = (
    "That name won't work. Use letters, numbers, - and _, starting with a letter "
    "(up to 40 characters), for example team-alerts."
)
NAME_IN_USE = "You already have “{name}”. Pick another name, or press Edit on it."
NAME_BUILT_IN = "“{name}” is the name of a built-in destination. Pick another name."
#: Stored keys an edit never carries over by itself: the type is the body's,
#: events have their own keep rule, and the last test result belonged to the
#: settings before the edit.
_NOT_CARRIED = frozenset({"type", "events", "last_test_ok", "last_test_at"})
#: The same server-name rule chat's channel_connect uses (settings/records.py).
_MAIL_SERVER_RX = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}")
IMAP_HOST_RULE = (
    "The reading mail server must be a server name such as imap.gmail.com: letters, "
    "digits, dots and dashes only, with the port in its own box. A password never goes in it."
)
IMAP_PORT_RULE = "The reading port must be a number, such as 993."


def _is_true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


#: Plain fields whose stored VALUE the edit form is never handed back: a
#: webhook URL is itself a credential, and the allowlist's ids stay in the
#: daemon (only their count leaves it, as since v1.136.0). The form still
#: learns they are SAVED, so a blank box keeps them.
HIDDEN_VALUES = frozenset({"webhook_url", "allowed_senders"})


def saved_fields(fields: list[dict], spec: dict) -> tuple[list[str], dict[str, str]]:
    """``(saved, values)`` for one stored destination: the field keys that hold
    something (secrets by their vault reference — never the secret), and the
    plain values the edit form may show (everything but :data:`HIDDEN_VALUES`,
    the two switches and lists)."""
    saved: list[str] = []
    values: dict[str, str] = {}
    for f in fields:
        key = str(f.get("key") or "")
        if not key:
            continue
        if f.get("secret"):
            if spec.get(f"{key}_secret"):
                saved.append(key)
            continue
        val = spec.get(key)
        if val is None or val == "" or val == []:
            continue
        saved.append(key)
        if key not in HIDDEN_VALUES and not isinstance(val, (bool, list, dict)):
            values[key] = str(val)
    return saved, values


#: v1.455.0 review: a web service's Username / Header box holding its key.
PLAIN_BOX_HOLDS_KEY = (
    "That looks like the key itself, and the username or header name is saved in plain text. "
    "Paste the key only in the Key box, and give the username or the header's name (for example "
    "X-API-Key) in the other one."
)


def _plain_box_holds_a_key(value: str, key: str) -> bool:
    """True when a box saved in plain text holds the key (or a piece of it at
    least 6 characters long) or anything shaped like a credential."""
    v = str(value or "").strip()
    k = str(key or "").strip()
    if not v:
        return False
    if k and len(k) >= 6 and (k in v or (len(v) >= 6 and v in k)):
        return True
    try:
        from ...settings.records_tools import _looks_like_a_secret

        return bool(_looks_like_a_secret(v))
    except Exception:  # noqa: BLE001 — unsure: refuse rather than store a key in the clear
        return True


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

        v1.448.0 (Wave A item 4): the body is ``integrations/hookups.add_hookup``
        — the ONE add the chat's ``web_service_add`` goes through too; the
        answers and the stored shape are unchanged byte for byte.
        """
        from ...integrations.builtin import auth_problem
        from ...integrations.hookups import (
            ADDRESS_REQUIRED,
            NAME_IN_USE,
            NAME_REQUIRED,
            add_hookup,
            hookup_id,
        )

        iid = hookup_id(body.name)
        if not iid:
            raise HTTPException(status_code=400, detail=NAME_REQUIRED)
        if not (body.base_url or "").strip():
            raise HTTPException(status_code=400, detail=ADDRESS_REQUIRED)
        # v1.455.0 (Wave H item 31): a key's way of travelling is checked
        # BEFORE the key is written, so a refused add leaves no vault row.
        problem = auth_problem(body.auth_kind, body.auth_header, body.auth_user)
        if problem:
            raise HTTPException(status_code=400, detail=problem)
        # v1.455.0 review: the Username and Header boxes are saved in plain
        # text with the service (they are not the secret). A key pasted into
        # one of them — the key itself, or anything shaped like a credential —
        # is refused BEFORE anything is written, in words that never repeat it.
        kind = str(body.auth_kind or "bearer").strip().lower()
        plain = (body.auth_user if kind == "basic" else body.auth_header if kind == "header" else "") or ""
        if plain.strip() and _plain_box_holds_a_key(plain, body.auth_token or ""):
            raise HTTPException(status_code=400, detail=PLAIN_BOX_HOLDS_KEY)
        if d.platform.integrations.get_spec(iid) is not None:
            raise HTTPException(status_code=400, detail=NAME_IN_USE.format(name=body.name.strip() or iid))
        sname = ""
        if (body.auth_token or "").strip():
            sname = f"integration_{iid}_token"
            d.platform.secrets.set(sname, body.auth_token.strip(), kind="token")
        try:
            add_hookup(
                d.platform,
                name=body.name,
                base_url=body.base_url,
                description=body.description or "",
                auth_secret=sname,
                persist=d._persist_config,
                auth_kind=body.auth_kind,
                auth_header=body.auth_header,
                auth_user=body.auth_user,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
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
            poller = getattr(d, "inbound_poller", None)
            poll_err = (getattr(poller, "poll_errors", None) or {}).get(name) or {}
            # v1.427.6: what the edit form keeps when a box is left blank.
            stype = str(spec.get("type") or "")
            saved, values = saved_fields(d._CHANNEL_TYPE_FIELDS.get(stype, []), spec)
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
                    # v1.231.0 (audit AE8): the LAST failed poll of this
                    # channel (a revoked bot token, a refused IMAP login),
                    # cleared by the next poll that comes back — so a dead
                    # phone reads "failing" here, not "working, tested
                    # three days ago".
                    "last_poll_error": poll_err.get("detail"),
                    "last_poll_error_at": poll_err.get("at"),
                    # v1.427.6: field keys that hold something (names only),
                    # the plain values the edit form may show, and why
                    # two-way cannot hear the user yet ("" = it can, or off).
                    "saved_fields": saved,
                    "values": values,
                    "two_way_note": two_way_note(stype, spec) if spec else None,
                    # v1.444.0: what a two-way Slack pump has heard (None else).
                    "listening": listening_status(name, stype, spec) if spec else None,
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
                        "then install it to your workspace. Copy the Bot token "
                        "(OAuth & Permissions, xoxb-…) and pick a channel for "
                        "alerts. To message Jarvis back: make an App-level token "
                        "with the connections:write scope (Basic Information → "
                        "App-Level Tokens, xapp-…), turn on two-way, and add your "
                        "Slack member ID to the allowlist — no public address "
                        "needed. Jarvis then answers your direct messages, your "
                        "messages in the alerts channel (in a thread), and an "
                        "@mention anywhere else privately. An app made from an "
                        "older manifest only sends direct messages: open it → App "
                        "Manifest, paste this JSON, Save, and reinstall it when "
                        "Slack asks (that also turns on App Home → Messages Tab, "
                        "so you can write to Jarvis directly)."
                        if t == "slack"
                        else None
                    ),
                }
                for t, fields in d._CHANNEL_TYPE_FIELDS.items()
            ]
        }

    def _rearm_slack_socket() -> None:
        """v1.292.0 (platform-04): a Slack channel added, re-tokened or removed
        here re-arms the Socket Mode pump NOW instead of at the next restart.
        This sync route runs in a threadpool, so hop onto the daemon loop (the
        calendar trigger's pattern). Guarded — the lifespan wires it — and a
        re-arm hiccup never fails the write (the config is already persisted)."""
        rearm = getattr(d, "_live_rearm", {}) or {}
        loop = rearm.get("loop")
        fn = rearm.get("slack")
        if loop is not None and fn is not None:
            try:
                loop.call_soon_threadsafe(fn)
            except Exception:  # noqa: BLE001 — a re-arm hiccup must not fail the write
                pass

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
            raise HTTPException(status_code=400, detail=NAME_RULE_REFUSAL)
        body_cfg: dict[str, Any] = dict(body.config or {})
        # v1.446.0 (ease-of-use review item 13): the page's Add says it is
        # making a NEW destination, so a name already in use is refused in
        # words instead of quietly replacing that destination. Edit (and any
        # older caller with no flag) re-submits through here as before. The
        # flag is never stored.
        is_new = _is_true(body_cfg.pop(NEW_FLAG, None))

        # BLANK KEEPS (v1.427.6, the user's report: "when i go back in to
        # edit, i need to re-enter the tokens because they are cleared"). Edit
        # re-submits here, and the form never holds a saved secret, so a blank
        # box used to DROP the saved token, the allowlist and every other
        # field the form could not show. Now, for an existing destination of
        # the SAME type, a blank field keeps what is stored; a key named in
        # ``clear`` is removed; a typed value replaces. (The v1.164.0
        # remote-agent rule: replace / keep / clear are three intents.)
        configured_now = (d.platform.config.comm or {}).get("channels") or {}
        if is_new:
            if name in configured_now:
                raise HTTPException(status_code=409, detail=NAME_IN_USE.format(name=name))
            if name in set(d.platform.notifier.channels()):
                raise HTTPException(status_code=409, detail=NAME_BUILT_IN.format(name=name))
        prior = configured_now.get(name)
        if is_new or not isinstance(prior, dict) or prior.get("type") != ctype:
            prior = None  # a new destination, or another type: nothing carries
        raw_clear = body_cfg.get("clear")
        clear = {str(k) for k in raw_clear} if isinstance(raw_clear, list) else set()
        # Secrets are written only once the whole save is valid: a refused
        # edit must never swap the token a working destination is using.
        pending_secrets: dict[str, str] = {}
        drop_secrets: list[str] = []

        config: dict[str, Any] = {"type": ctype}
        for field in d._CHANNEL_TYPE_FIELDS[ctype]:
            key = field["key"]
            value = body_cfg.get(key)
            if value in (None, ""):
                if prior is None:
                    continue
                if key in clear:
                    old_ref = prior.get(f"{key}_secret")
                    if field.get("secret") and isinstance(old_ref, str) and old_ref:
                        drop_secrets.append(old_ref)
                    continue
                if field.get("secret"):
                    old_ref = prior.get(f"{key}_secret")
                    if isinstance(old_ref, str) and old_ref:
                        config[f"{key}_secret"] = old_ref
                elif key in prior and prior[key] not in (None, ""):
                    config[key] = prior[key]
                continue
            if field.get("secret"):
                secret_name = f"channel_{name}_{key}"
                pending_secrets[secret_name] = str(value)
                config[f"{key}_secret"] = secret_name
                old_ref = (prior or {}).get(f"{key}_secret")
                if isinstance(old_ref, str) and old_ref and old_ref != secret_name:
                    drop_secrets.append(old_ref)
            elif key == "allowed_senders":
                # Comma-separated ids -> the list the fail-closed allowlist reads.
                config[key] = [s.strip() for s in str(value).split(",") if s.strip()]
            elif key in ("inbound_enabled", "chat_enabled"):
                config[key] = str(value).strip().lower() in ("1", "true", "yes", "on")
            else:
                config[key] = value

        # AN EDIT ONLY CHANGES WHAT YOU CHANGED (v1.446.0, ease-of-use review
        # item 13): a destination chat made two-way keeps settings the form
        # has no box for (an email destination's reading server and port,
        # its mailbox, a chat switch). Every stored key the form did not send
        # is carried over, unless the user cleared it. The last test result
        # is not carried: the settings changed, so the old verdict is not
        # this destination's any more (the page tests it right after).
        if prior is not None:
            field_keys = {f["key"] for f in d._CHANNEL_TYPE_FIELDS[ctype]}
            owned = field_keys | {
                f"{f['key']}_secret" for f in d._CHANNEL_TYPE_FIELDS[ctype] if f.get("secret")
            }
            for key, val in prior.items():
                if key in config or key in owned or key in clear or key in _NOT_CARRIED:
                    continue
                config[key] = val
            # Listening turned off by the user's own box ends a chat switch
            # the form has no box for (chat implies listening, below, would
            # otherwise turn listening straight back on).
            if (
                "chat_enabled" not in field_keys
                and str(body_cfg.get("inbound_enabled") or "").strip().lower()
                in ("0", "false", "no", "off")
            ):
                config["chat_enabled"] = False

        # A reading mail server is a server NAME, never a "user:password@"
        # (it is stored in plain settings). Refused without echoing it.
        imap = str(config.get("imap_host") or "").strip()
        if ctype == "email" and imap and not _MAIL_SERVER_RX.fullmatch(imap):
            raise HTTPException(status_code=400, detail=IMAP_HOST_RULE)
        iport = config.get("imap_port")
        if ctype == "email" and iport not in (None, ""):
            try:
                int(str(iport).strip())
            except ValueError:
                raise HTTPException(status_code=400, detail=IMAP_PORT_RULE) from None

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
        # v1.446.0: an EDIT that sends no `events` keeps the saved choice (it
        # used to reset a narrowed list to everything, approval asks the
        # user had turned off included). An explicit empty list still means
        # everything; a new destination with none gets everything.
        if "events" not in body_cfg:
            if prior is not None and isinstance(prior.get("events"), list) and prior["events"]:
                config["events"] = list(prior["events"])
        raw_events = body_cfg.get("events")
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

        for secret_name, secret_value in pending_secrets.items():
            d.platform.secrets.set(secret_name, secret_value, kind="token")

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
        # A secret the user removed (or replaced under another name) leaves
        # the vault once nothing refers to it any more.
        for old_ref in drop_secrets:
            if old_ref in pending_secrets:
                continue
            try:
                d.platform.secrets.delete(old_ref)
            except Exception:  # noqa: BLE001 — a leftover vault row is not a failed save
                pass
        # Two-way Slack (Socket Mode) dials out from a background pump that
        # used to be armed only at boot — connect (or re-token) it now.
        if ctype == "slack":
            _rearm_slack_socket()
        return {
            "name": name,
            "type": ctype,
            "added": True,
            # v1.427.6: why two-way still cannot hear the user ("" when it can).
            "two_way_note": two_way_note(ctype, config) or "",
        }

    @app.delete("/comm/channels/{name}")
    def delete_comm_channel(name: str) -> dict[str, Any]:
        # v1.435.0 (Wave 3 group H): THE removal, shared with the chat's
        # channel_remove (settings/records_apps.remove_destination): the live
        # destination, its persisted settings, every vault row they name, and
        # a two-way Slack listener (re-armed off the settings change).
        from ...settings.records_apps import remove_destination

        removed, cfg = remove_destination(d.platform, name)
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

        from ...comm.inbound import _failure_words_for_phone
        from ...providers.failure_words import phone_surface

        try:
            # v1.378.0 review: the reply goes to the PHONE — worded for it.
            with phone_surface():
                result = await turn(d.platform, personas, ChatBody(messages=history, auto_tools=True))
        except HTTPException as exc:
            # Mirror the inbound pipeline: the honest error IS the reply —
            # appended to the thread, delivered to the phone, rendered by the
            # dashboard like a normal turn (not surfaced as a raw 4xx/5xx).
            # v1.378.0 review: the chat's plain sentence (`words`) like the
            # inbound pipeline, never the vendor's raw `detail` on a phone.
            reply = f"I hit a problem: {_failure_words_for_phone(exc)}"
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

        # EMERGENCY STOP (v1.351.0, G13): the hand-off below starts an agent
        # stamped ``comm:<channel>`` (it may ask back through the phone), which
        # the switch refuses. Say so as the reply — same words the phone door
        # sends — before any "On it" ack, never a 500 after it.
        _stop_why = (
            poller._emergency_stop_reason(channel_name) if result.get("escalate") else ""
        )
        if _stop_why:
            _append_reply(_stop_why)
            sent = await poller.send_chunked(ch, _stop_why, chat_id=sender_id)
            return {**result, "escalate": False, "reply": _stop_why, "sent": sent}

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
            # v1.231.0 (audit N3/AE17): the escalated session carries the
            # thread's project tag exactly as ``comm/inbound.py`` does — this
            # door used to drop it, so the run lost the project's folder,
            # brief and knowledge — and is stamped ``comm:<channel>`` so the
            # runtime may pause it on an ask-tier tool (bell + phone).
            session = await d.orchestrator.create_session(
                task,
                agent_type,
                project_id=str(getattr(rec, "project_id", "") or "") or None,
                origin=f"comm:{channel_name}",
                **_spawn_kwargs,
            )

            async def _finish() -> None:
                try:
                    if dyn_def is not None:
                        s = await poller._run_dynamic_session(session, dyn_def)
                    else:
                        s = await d.orchestrator.run_session(session.id)
                    summary = (s.summary or "(no result)").strip()
                except Exception as exc:  # noqa: BLE001 — deliver, don't vanish
                    # v1.378.0 review: a refusal in words (comm.inbound).
                    # Readiness 12A review (G28): every failure, by the run's
                    # one rule — the provider named as the run's summary does.
                    from ...comm.inbound import _run_problem

                    summary = "I hit a problem: " + _run_problem(
                        exc,
                        manager=getattr(d.platform, "providers", None),
                        provider=str(getattr(session, "provider", "") or ""),
                    )
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
        # Compare BYTES, never str. ``hmac.compare_digest`` refuses non-ASCII
        # ``str`` arguments with TypeError, and ``sig`` is a header straight off
        # the wire on a route that is deliberately TOKEN-EXEMPT — so
        # "X-Slack-Signature: v0=caf\xc3\xa9" turned this fail-closed 403 into an
        # unhandled 500 that any unauthenticated caller could trigger at will
        # (and per this repo's history a 500 is what the dashboard renders as
        # "daemon offline"). Same defect, same fix as daemon/auth.py:
        # token_matches — read its docstring: encode BOTH sides UTF-8 rather than
        # stripping or ASCII-filtering the candidate, so every candidate is
        # comparable and the compare stays constant-time over the encoded forms.
        if not _hmac.compare_digest(expected.encode("utf-8"), sig.encode("utf-8")):
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

    @app.post("/comm/email/compose")
    async def compose_email(body: dict[str, Any]) -> dict[str, Any]:
        """Save a chat draft to the mailbox's Drafts folder, or send it (C-06).

        Called ONLY by the DraftCard's confirm dialog on a person's click —
        this is deliberately not a tool, so no model can reach it. ``mode``
        defaults to ``draft`` (IMAP APPEND, nothing sent); ``send`` goes out
        over SMTP. Attachments must pass the file policy (see
        ``comm/compose.py``). Every attempt is ledgered as
        ``comm.email_composed`` — recipients, subject, attachment names and
        the outcome, never the body.

        409 when no email account is connected (the words say how to add
        one), 400 for something the user can fix, 502 when the mail server
        refused or did not answer within ``COMPOSE_DEADLINE_S``.
        """
        import asyncio

        from ...comm import compose as _compose

        found = _compose.find_email_channel(d.platform.notifier)
        if found is None:
            raise HTTPException(status_code=409, detail=_compose.NO_EMAIL_CHANNEL)
        name, channel = found
        cfg = getattr(channel, "config", None) or {}
        from_addr = str(cfg.get("from_addr") or cfg.get("username") or "").strip()
        if not from_addr:
            raise HTTPException(
                status_code=409,
                detail="This email account has no From address yet — set one "
                "in Channels, then try again.",
            )
        try:
            # Attachment checks stat the disk: off the loop.
            plan = await asyncio.to_thread(_compose.prepare, body)
        except _compose.ComposeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    _compose.run_compose, channel, plan, from_addr=from_addr
                ),
                timeout=_compose.COMPOSE_DEADLINE_S,
            )
        except asyncio.TimeoutError:
            result = {
                "ok": False,
                "timed_out": True,
                "detail": f"the mail server did not answer within "
                f"{int(_compose.COMPOSE_DEADLINE_S)} s — check your Drafts or "
                "Sent folder before trying again",
            }
        await d.platform.event_bus.publish(
            _compose.EMAIL_COMPOSED, _compose.ledger_payload(name, plan, result)
        )
        if not result.get("ok"):
            raise HTTPException(
                status_code=502,
                detail=str(result.get("detail") or "the mail server refused"),
            )
        return {
            "ok": True,
            "mode": plan.mode,
            "channel": name,
            "detail": result.get("detail"),
            "folder": result.get("folder"),
            "refused": result.get("refused") or [],
            "attachments": [p.name for p in plan.attachments],
        }

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
            # v1.292.0 (platform-06): an outbound webhook with no event types
            # would match nothing (on_event needs ``event.type in types``) while
            # the page used to promise "all events" -- refuse it up front, in a
            # sentence the person can act on, instead of saving a dead row.
            try:
                d.platform.outbound_webhooks.register(
                    body.slug,
                    body.target_url,
                    body.event_types,
                    secret=secret,
                    secret_name=body.secret_name or None,  # persist the real vault key
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
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
                # v1.231.0 (audit AE6): a rule that could not start is
                # ``failed: [{rule, error}]``, not a fire.
                from ...reflex.router import summarize_fires

                return {"ok": True, "slug": _slug, **summarize_fires(fired)}

            d.platform.inbound_webhooks.register(
                body.slug, _handler, secret=secret, secret_name=body.secret_name or None
            )
        return {"slug": body.slug, "direction": body.direction}

    @app.delete("/webhooks/{slug}")
    def delete_webhook(slug: str) -> dict[str, Any]:
        """Remove a webhook registration (v1.292.0, platform-06).

        Before this there was no way to take a webhook back out: the row, the
        inbound in-memory handler and the outbound secret cache all stayed for
        good. This drops all three; the vault secret named by ``secret_name`` is
        left alone because another webhook may share it. Unknown slug -> 404.
        """
        from ...webhooks.models import WebhookRecord

        with session_scope(d.platform.engine) as db:
            row = db.exec(select(WebhookRecord).where(WebhookRecord.slug == slug)).first()
            direction = row.direction if row is not None else None
        if direction is None:
            raise HTTPException(status_code=404, detail=f"no webhook named '{slug}'")
        if direction == "outbound":
            d.platform.outbound_webhooks.unregister(slug)
        else:
            d.platform.inbound_webhooks.unregister(slug)
        return {"ok": True, "slug": slug, "direction": direction}

    @app.post("/webhooks/{slug}")
    async def inbound_webhook(slug: str, request: Request) -> Any:
        """Receive an external POST for ``slug``.

        v1.231.0 (audit AE15): a request the dispatcher REFUSES — unknown slug
        (404), bad/replayed signature or an unresolvable secret (401) — no
        longer answers 200 ``{"ok": false}``: the caller gets the real status
        (body keeps ``ok: false`` + ``error``) and the timeline gets
        ``webhook.rejected {slug, reason}``, so a probed or misconfigured secret
        is visible where the user looks."""
        raw = await request.body()
        sig = request.headers.get("X-IronJarvis-Signature") or request.headers.get(
            "X-Signature"
        )
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        result = await d.platform.inbound_webhooks.dispatch(
            slug, body, raw=raw, signature=sig
        )
        rejected = result.get("rejected") if isinstance(result, dict) else None
        if rejected:
            try:
                await d.platform.event_bus.publish(
                    "webhook.rejected", {"slug": slug, "reason": str(rejected)}
                )
            except Exception:  # noqa: BLE001 — the bus must never mask the refusal
                pass
            status = 404 if rejected == "unknown_slug" else 401
            return JSONResponse(status_code=status, content=result)
        return result
