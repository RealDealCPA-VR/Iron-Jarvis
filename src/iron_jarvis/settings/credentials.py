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
    (``conn_<id>_<field>``, connectors.service) — so a token saved on the card
    is the token the pack launches with (redesign S5)."""
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
        if name.startswith("app."):
            # A pack saved by app_connect waits for this token: load it now.
            from .records import reload_app_after_secret

            reload_app_after_secret(self.platform, arg)
        return {"secret": name, "label": sdef.label, "status": status, "action_id": action_id}

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
    parameters = {
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
