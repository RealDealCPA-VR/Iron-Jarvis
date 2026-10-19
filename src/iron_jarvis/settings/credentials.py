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

    sdef = next(s for s in schema.SECRETS if s.name == "app.{name}")
    arg = name[len("app."):]
    if not _ARG.match(arg):
        raise CredentialError(f"There is no credential called {name!r}.")
    cid, _, field = arg.partition("__")
    conn = get_connector(cid)
    secrets = [f for f in (conn.fields if conn else []) if f.kind == "secret"]
    if not secrets:
        raise CredentialError(f"There is no app called {cid!r} that takes a token.")
    chosen = next((f for f in secrets if f.name == field), None) if field else secrets[0]
    if chosen is None:
        raise CredentialError(f"{conn.name} has no credential called {field!r}.")
    return sdef, cid, _secret_name(cid, chosen.name)


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
        if name.startswith("app."):
            # A pack saved by app_connect waits for this token: load it now.
            # v1.351.0 (G15): and SAY what happened — the card reads "GitHub
            # connected · 26 tools", "Saved, but its tools did not load", or
            # (an app that takes two keys) what it still needs. Names, counts
            # and plain words only; never a value.
            out.update(self._app_outcome(arg))
        return out

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
        self, name: str, vault: str, backup: str, had_prior: bool, status: str, actor: str, change_id: str
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
        "type a secret into the chat, and never repeat one."
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
