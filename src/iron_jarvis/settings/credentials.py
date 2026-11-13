"""Secrets set from chat WITHOUT the secret ever entering the chat (calm UI
redesign S4, brief non-negotiable 5).

The old ``secret_set`` chat tool took the value as a TOOL ARGUMENT: the model
wrote it, so it sat in the model's context, the transcript and the stream
(only the ledger copy was redacted). Now:

* the model calls ``config_secret {name}`` — it never sees, asks for, or
  passes a value; the tool answers with a ``secret_request`` the chat page
  renders as a secure credential CARD;
* the card posts the value straight to ``POST /config/secret`` (this module's
  :meth:`CredentialStore.store`), which writes the encrypted vault through the
  same paths the Connections / Secrets / Notifications pages use;
* the ledger row says ``stored`` / ``replaced`` — names only, never a value —
  and its ``secret_restore`` undo row holds only VAULT NAMES: a prior value
  is kept encrypted under a short-lived backup name, never decrypted into the
  ledger, a log or a reply.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any

from ..core.ids import new_id
from ..providers.failure_words import ACCOUNTS_PLACE
from ..tools.base import Reversibility, Tool, ToolContext, ToolResult
from . import schema

log = logging.getLogger("ironjarvis.settings")

_ARG = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
BACKUP_PREFIX = "__undo__"


class CredentialError(ValueError):
    pass


def resolve_secret(name: str) -> tuple[schema.SecretDef, str, str]:
    """``channel.telegram`` → (its def, the argument ``telegram``, the vault
    name ``channel_telegram_token``). Refuses anything not in the schema."""
    if name.startswith("app."):
        return _resolve_app(name)
    if name.startswith("tool_server."):
        return _resolve_tool_server(name)
    for s in schema.SECRETS:
        if s.pattern:
            prefix = s.name.split("{", 1)[0]
            if name.startswith(prefix):
                arg = name[len(prefix):]
                if not _ARG.match(arg):
                    break
                placeholder = s.name[s.name.index("{"): s.name.index("}") + 1]
                vault = s.vault_key.replace(placeholder, arg)
                if s.name.startswith("channel."):
                    vault = f"channel_{arg}_token"
                return s, arg, vault
        elif name == s.name:
            return s, "", s.vault_key
    raise CredentialError(f"There is no credential called {name!r}.")


def _resolve_app(name: str) -> tuple[schema.SecretDef, str, str]:
    """``app.<connector>`` (its one token) or ``app.<connector>__<FIELD>``
    (one of several) → the vault name the connector's MCP config reads
    (``connectors.service._secret_name``: ``mcp.<id>.<FIELD>``, or
    ``mcp.<id>.auth.value`` for a remote card's token, since v1.355.0; it was
    ``conn_<id>_<field>`` before) — so a token saved on the card is the token
    the pack launches with (redesign S5)."""
    from ..connectors.catalog import get_connector
    from ..connectors.service import _secret_name
    from .records import card_fields

    sdef = next(s for s in schema.SECRETS if s.name == "app.{name}")
    arg = name[len("app."):]
    if not _ARG.match(arg):
        raise CredentialError(f"There is no credential called {name!r}.")
    cid, _, field = arg.partition("__")
    conn = get_connector(cid)
    # v1.433.0 (Wave 1, A4): a credential-carrying ARGUMENT (Postgres's
    # connection string) is collected by the card too (records.card_fields).
    secrets = card_fields(conn) if conn else []
    if not secrets:
        raise CredentialError(f"There is no app called {cid!r} that takes a token.")
    chosen = next((f for f in secrets if f.name == field), None) if field else secrets[0]
    if chosen is None:
        raise CredentialError(f"{conn.name} has no credential called {field!r}.")
    return sdef, cid, _secret_name(cid, chosen.name)


def _resolve_tool_server(name: str) -> tuple[schema.SecretDef, str, str]:
    """``tool_server.<pack>__<slot>`` (v1.448.0, ``settings/records_tools``):
    a value the chat's ``mcp_server_add`` left for the card — an env key, a
    header, the auth value, a command-line argument or the address's key —
    stored under the vault name the pack door launches with
    (``mcp.<pack>.<KEY>`` and its kin), so the server starts with exactly
    what the card saved. The pack itself is added only once every asked
    value is saved (``records_tools.on_secret_saved``)."""
    from .records_tools import resolve_tool_server_secret

    sdef = next(s for s in schema.SECRETS if s.name == "tool_server.{name}")
    arg = name[len("tool_server."):]
    if not _ARG.match(arg):
        raise CredentialError(f"There is no credential called {name!r}.")
    try:
        _pack, _slot, vault = resolve_tool_server_secret(arg)
    except ValueError:
        raise CredentialError(f"There is no credential called {name!r}.") from None
    return sdef, arg, vault


#: v1.449.0 (item 25): what the card says under a key that failed its check.
NOT_SAVED_TAIL = "Nothing was saved, and what answers you didn't change."


#: v1.449.0 (AN review): the Test route's fixes are written for the
#: Connections page, which has a Test button and IS the place to update a key.
#: The chat's secure card has neither — the key is in the box in front of
#: the user and the button says "Save securely" — so those two sentences
#: are said for the card. Keyed on the route's exact words; an unknown fix
#: is kept as it is.
CARD_FIX_WORDS: dict[str, str] = {
    "then press Test again.": "then save it again.",
    "Press Test again in a moment.": "Save it again in a moment.",
    "Press Test again.": "Save it again.",
    f"Update it in {ACCOUNTS_PLACE}.": "Check the key and paste it again.",
}


def card_fix(fix: str) -> str:
    """The Test route's fix sentence said for the secure card (see
    :data:`CARD_FIX_WORDS`)."""
    out = str(fix or "").strip()
    for page_words, card_words in CARD_FIX_WORDS.items():
        out = out.replace(page_words, card_words)
    return out


def not_saved_words(tested: dict[str, Any]) -> str:
    """One plain sentence for a key that failed its check: the Test's own
    title and fix (the fix said for the card, :func:`card_fix`) when the
    route worded it, else its detail, then :data:`NOT_SAVED_TAIL`. Never
    the key."""
    title = str(tested.get("title") or "").strip().rstrip(".")
    fix = card_fix(tested.get("fix") or "")
    if title:
        head = f"{title}. {fix} ".rstrip() + " "
    else:
        detail = str(tested.get("detail") or "").strip().rstrip(".")
        head = f"Not connected — {detail}. " if detail else "Not connected. "
    return head + NOT_SAVED_TAIL


class CredentialStore:
    def __init__(self, d: Any) -> None:
        self.d = d
        self.platform = d.platform
        self._lock = threading.Lock()

    def store(self, name: str, value: str, *, actor: str = "chat_card") -> dict[str, Any]:
        """Write the credential; journal names only. Blocking."""
        value = (value or "").strip()
        if not value:
            raise CredentialError("Paste the value first.")
        if len(value) > 8192:
            raise CredentialError("That is too long to be a key or token.")
        sdef, arg, vault = resolve_secret(name)
        if name.startswith("channel.") and self._webhook_channel(arg) is not None:
            return self._store_webhook(name, arg, value, sdef, actor)
        if name.startswith("connection."):
            # v1.449.0 (readiness Wave C, item 25): a model provider's key is
            # TESTED before it is stored, as the connect door's
            # ``POST /connections/{p}/key?check=1`` does — a wrong key used to
            # land in the vault (and be made the default) with the card
            # saying "saved securely". A failed check stores nothing, writes
            # no ledger row, and the card gets the Test's words plus "Nothing
            # was saved". Nothing to check with (no prober, an unknown
            # provider) stores as before.
            tested = self._check_key(arg, value)
            if tested is not None and not tested.get("ok"):
                return {
                    "secret": name,
                    "label": sdef.label,
                    "status": "not_saved",
                    "saved": False,
                    "test": tested,
                    "words": not_saved_words(tested),
                }
        secrets = self.platform.secrets
        with self._lock:
            change_id = new_id("sec")
            prior = secrets.get(vault)
            if not prior and name.startswith("app."):
                # v1.355.0 integrate (lane 5B note 3): a pack saved before
                # v1.355.0 reads its token from an OLDER row (``conn_*``);
                # storing here points the pack at this row
                # (records.adopt_card_secrets), so the value the pack used
                # until now is THIS change's prior — an Undo puts it back into
                # the row the pack now reads, instead of emptying it.
                prior = self._pack_row_prior(arg, vault)
            backup = ""
            if prior:
                backup = f"{BACKUP_PREFIX}{change_id}"
                secrets.set(backup, prior, kind="password")
            if name.startswith("connection."):
                rec = self.platform.connections.set_api_key(arg, value)
                promote = getattr(self.d, "_maybe_autopromote_default", None)
                if callable(promote):
                    try:
                        promote(rec.provider)
                    except Exception:  # noqa: BLE001 — the key is stored either way
                        pass
            else:
                secrets.set(vault, value, kind="api_key" if "key" in vault else "password")
            status = "replaced" if prior else "stored"
            action_id = self._journal(name, vault, backup, bool(prior), status, actor, change_id)
        out: dict[str, Any] = {"secret": name, "label": sdef.label, "status": status, "action_id": action_id}
        if name.startswith("model_server."):
            # v1.438.0 (Wave 4 group B): a model server's key just landed —
            # its model list is read again with it (the catalog cache keyed
            # on the old, keyless answer otherwise).
            try:
                from ..daemon.routes.fleet import _catalog_changed

                _catalog_changed(arg)
            except Exception:  # noqa: BLE001 — the key is stored either way
                pass
        if name.startswith("tool_server."):
            # v1.448.0 (settings/records_tools): a tool server the chat left
            # waiting for this value is ADDED now — through the one pack door,
            # with the value read back from the vault — once every asked
            # value is saved; the card reads the same keys an app's does.
            try:
                from .records_tools import on_secret_saved

                out.update(on_secret_saved(self.platform, arg))
            except Exception:  # noqa: BLE001 — the value is stored either way
                log.warning("tool server %s: could not be added after its key was saved", arg, exc_info=True)
        if name.startswith("web_service."):
            # Readiness 0j.5 item 18: a web service's key just landed — its
            # own Test runs with it and the card says whether it answers.
            try:
                from .records_tools import on_web_service_key_saved

                out.update(on_web_service_key_saved(self.platform, arg))
            except Exception:  # noqa: BLE001 — the key is stored either way
                log.warning("web service %s: could not be checked after its key was saved", arg, exc_info=True)
        if name.startswith("app."):
            # A pack saved by app_connect waits for this token: load it now.
            # v1.351.0 (G15): and SAY what happened — the card reads "GitHub
            # connected · 26 tools", "Saved, but its tools did not load", or
            # (an app that takes two keys) what it still needs. Names, counts
            # and plain words only; never a value.
            out.update(self._app_outcome(arg))
        return out

    def _check_key(self, provider: str, value: str) -> dict[str, Any] | None:
        """The connect door's own pre-save check (``routes.connections.
        check_key_first``): the Test route's verdict for a candidate key, or
        None when there is nothing to check with. Never raises and never
        carries the key (the prober scrubs it). Blocking: one small request."""
        try:
            from ..daemon.routes.connections import check_key_first

            return check_key_first(self.d, provider, value)
        except Exception:  # noqa: BLE001 — a check that cannot run stores as before
            return None

    # ----------------------------------------------------------- webhooks
    #
    # v1.433.0 (Wave 1, A4): a Discord destination's webhook URL is its
    # credential. ``channel_connect`` no longer takes it as an argument; this
    # card writes it where the Notifications page keeps it — ``webhook_url`` in
    # the destination's own settings (``config.comm.channels[name]``, which
    # ``comm.channels.DiscordChannel`` reads) — through ``records.apply_channel``
    # (persisted, the live destination rebuilt). The prior URL is kept
    # ENCRYPTED under a backup vault name for the Undo, never in the ledger.

    def _webhook_channel(self, channel: str) -> dict[str, Any] | None:
        """The stored settings of destination ``channel`` when it takes its
        credential as a webhook URL (Discord), else None. Never raises."""
        try:
            from .records import snap_channel

            cfg = snap_channel(self.platform, channel)
        except Exception:  # noqa: BLE001
            return None
        if isinstance(cfg, dict) and str(cfg.get("type") or "") == "discord":
            return cfg
        return None

    def _store_webhook(self, name: str, channel: str, value: str, sdef: Any, actor: str) -> dict[str, Any]:
        if not value.lower().startswith("https://"):
            raise CredentialError("A Discord webhook URL starts with https://.")
        from .records import apply_channel

        secrets = self.platform.secrets
        with self._lock:
            cfg = self._webhook_channel(channel)
            if cfg is None:
                raise CredentialError(f"There is no Discord destination called {channel!r}.")
            change_id = new_id("sec")
            prior = str(cfg.get("webhook_url") or "")
            backup = ""
            if prior:
                backup = f"{BACKUP_PREFIX}{change_id}"
                secrets.set(backup, prior, kind="password")
            apply_channel(self.platform, channel, {**cfg, "webhook_url": value})
            status = "replaced" if prior else "stored"
            action_id = self._journal(
                name, "", backup, bool(prior), status, actor, change_id,
                extra={"channel": channel, "field": "webhook_url"},
            )
        return {"secret": name, "label": sdef.label, "status": status, "action_id": action_id}

    def _restore_webhook(self, name: str, channel: str, backup: str) -> str:
        from .records import apply_channel

        secrets = self.platform.secrets
        cfg = self._webhook_channel(channel)
        if cfg is None:
            return f"undo: the destination {channel} is no longer there"
        if backup:
            prior = secrets.get(backup)
            if prior is None:
                return f"undo: the earlier value of {name} is no longer kept"
            apply_channel(self.platform, channel, {**cfg, "webhook_url": prior})
            secrets.delete(backup)
            return f"undo: restored the earlier {name}"
        cfg = dict(cfg)
        cfg.pop("webhook_url", None)
        apply_channel(self.platform, channel, cfg)
        return f"undo: removed {name}"

    def _pack_row_prior(self, connector_id: str, vault: str) -> str:
        """The value the saved pack row reads for the field stored at
        ``vault`` when that row names a DIFFERENT vault row (an older pack's
        ``conn_<id>_<field>``); "" otherwise. Never raises, never logs a
        value."""
        try:
            from ..connectors.catalog import get_connector
            from ..connectors.service import _secret_name
            from .records import _field_ref, snap_app

            conn = get_connector(connector_id)
            cfg = snap_app(self.platform, connector_id)
            if conn is None or cfg is None:
                return ""
            for f in conn.fields:
                if f.kind != "secret" or _secret_name(conn.id, f.name) != vault:
                    continue
                ref = _field_ref(cfg, conn, f.name)
                if ref and ref != vault:
                    return self.platform.secrets.get(ref) or ""
            # v1.433.0 (Wave 1, A4): an argument the card collects — the value
            # the pack launches with now is this change's prior: the vault row
            # its ``arg_secrets`` names, or the plain argument it holds.
            from ..detections.redact import MASK
            from .records import card_arg_fields, card_arg_index

            for f in card_arg_fields(conn):
                if _secret_name(conn.id, f.name) != vault:
                    continue
                idx = card_arg_index(conn, f)
                ref = str(dict(cfg.get("arg_secrets") or {}).get(str(idx)) or "")
                if ref:
                    return (self.platform.secrets.get(ref) or "") if ref != vault else ""
                args = list(cfg.get("args") or [])
                shown = str(args[idx]) if idx < len(args) else ""
                if shown and shown not in (MASK, f"<{f.name}>"):
                    return shown
        except Exception:  # noqa: BLE001 — a store must not fail on this read
            log.warning("app %s: could not read the pack's older token row", connector_id, exc_info=True)
        return ""

    def _app_outcome(self, connector_id: str) -> dict[str, Any]:
        """``{app, app_name, tools_loaded[, load_reason][, waiting_for]}`` for
        an ``app.*`` credential just stored. Never raises."""
        from .records import app_waiting_for, reload_app_after_secret

        try:
            from ..connectors.catalog import get_connector

            conn = get_connector(connector_id)
            app_name = conn.name if conn is not None else connector_id
        except Exception:  # noqa: BLE001
            app_name = connector_id
        out: dict[str, Any] = {"app": connector_id, "app_name": app_name}
        waiting = app_waiting_for(self.platform, connector_id)
        if waiting:
            # Not launched yet: it cannot start without the rest.
            out["tools_loaded"] = 0
            out["waiting_for"] = waiting
            return out
        loaded = reload_app_after_secret(self.platform, connector_id)
        if loaded is None:
            return out
        out["tools_loaded"] = int(loaded.get("tools_loaded") or 0)
        if not out["tools_loaded"] and loaded.get("reason"):
            out["load_reason"] = str(loaded["reason"])
        return out

    def restore(self, desc: dict[str, Any]) -> str:
        """Undo: put the backed-up value back (or remove a key that did not
        exist before). Never returns or logs a value."""
        name = str(desc.get("secret") or "")
        vault = str(desc.get("vault") or "")
        backup = str(desc.get("backup") or "")
        secrets = self.platform.secrets
        with self._lock:
            if desc.get("field") == "webhook_url":
                # v1.433.0 (Wave 1, A4): a Discord webhook URL lives in the
                # destination's settings, not the vault (see _store_webhook).
                return self._restore_webhook(name, str(desc.get("channel") or ""), backup)
            if backup:
                prior = secrets.get(backup)
                if prior is None:
                    return f"undo: the earlier value of {name} is no longer kept"
                if name.startswith("connection."):
                    self.platform.connections.set_api_key(name.split(".", 1)[1], prior)
                else:
                    secrets.set(vault, prior, kind="api_key" if "key" in vault else "password")
                secrets.delete(backup)
                return f"undo: restored the earlier {name}"
            if name.startswith("connection."):
                try:
                    self.platform.connections.disconnect(name.split(".", 1)[1])
                except Exception:  # noqa: BLE001
                    secrets.delete(vault)
            else:
                secrets.delete(vault)
            return f"undo: removed {name}"

    def _journal(
        self,
        name: str,
        vault: str,
        backup: str,
        had_prior: bool,
        status: str,
        actor: str,
        change_id: str,
        *,
        extra: dict[str, Any] | None = None,
    ) -> str:
        from ..core.db import session_scope
        from ..core.models import PermissionMode, ToolInvocation, UndoJournal

        inv_id = new_id("tool")
        try:
            with session_scope(self.platform.engine) as db:
                db.add(
                    ToolInvocation(
                        id=inv_id,
                        session_id="settings",
                        agent_run_id="",
                        tool="set_secret",
                        args_json=json.dumps({"secret": name, "status": status, "actor": actor}),
                        verdict=PermissionMode.ALLOW,
                        ok=True,
                        output=f"{status} {name}",
                        reversibility=Reversibility.REVERSIBLE.value,
                    )
                )
                db.add(
                    UndoJournal(
                        action_id=inv_id,
                        session_id="settings",
                        agent_run_id="",
                        tool="set_secret",
                        kind="secret_restore",
                        reversible=True,
                        pre_inline=json.dumps(
                            {
                                "secret": name,
                                "vault": vault,
                                "backup": backup,
                                "had_prior": had_prior,
                                "change_id": change_id,
                                **(extra or {}),
                            }
                        ),
                    )
                )
                db.commit()
        except Exception:  # noqa: BLE001
            log.warning("a credential was stored but could not be journaled", exc_info=True)
            return ""
        return inv_id


class ConfigSecretTool(Tool):
    """Ask the user for a credential on a secure card. Never takes a value."""

    name = "config_secret"
    description = (
        "When a key, token, password or private link is needed (\"connect my Notion\", a "
        "provider API key, a Telegram bot token), call this with the credential's name from "
        "config_list. It shows the user a secure card to paste it into. NEVER ask the user to "
        "type a secret into the chat, and never repeat one. It only SAVES a value: it cannot "
        "clear or remove one (an empty value is refused)."
    )
    input_schema = {  # v1.341.0: was `parameters` (never read by Tool.spec()) — see settings/tools.py
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "e.g. connection.openai, channel.telegram, app.notion, voice_transcribe_key",
            },
            "why": {"type": "string", "description": "One short line shown on the card."},
        },
        "required": ["name"],
    }
    reversibility = Reversibility.READONLY

    def __init__(self, platform: Any) -> None:
        self.platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        name = str(args.get("name") or "").strip()
        try:
            sdef, arg, _vault = resolve_secret(name)
        except CredentialError as exc:
            return ToolResult(ok=False, output="", error=str(exc))
        label = sdef.label if not arg else f"{sdef.label}: {arg}"
        request = {
            "name": name,
            "label": label,
            "help": sdef.help,
            "why": str(args.get("why") or "")[:200],
            "request_id": new_id("seq"),
        }
        return ToolResult(
            ok=True,
            output=f"A secure card for {label} is shown to the user. Wait for them to save it; never ask for the value in chat.",
            data={"secret_request": request},
        )
