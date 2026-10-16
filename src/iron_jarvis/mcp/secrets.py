"""MCP pack env secrets live in the vault, never in config.toml (v1.342.0,
lane C-secrets of "the pack says why").

A pack's ``env`` used to be stored verbatim: ``POST /mcp/servers`` wrote
``{"GITHUB_TOKEN": "ghp_..."}`` into ``config.mcp_servers`` and
``GET /mcp/servers`` echoed it back to every dashboard window. The launcher
(``mcp/tools._build_transport``) already resolves ``env_secrets``
(ENV_KEY -> vault name) through the vault at spawn, so the one missing piece
was the split at the door and a one-time migration of what was already
stored.

The rule is the KEY's name: an env key matching :data:`SECRET_KEY_RX`
(token / key / secret / passw / auth / credential, case-insensitive) is a
secret. Its value goes to the vault as ``mcp.<server>.<KEY>`` and the stored
config keeps ``env_secrets[KEY] = that vault name``; everything else stays a
plain ``env`` entry. Nothing here ever logs, returns or raises a VALUE —
names only.
"""

from __future__ import annotations

import re
from typing import Any

from ..core.config import persist_config_values
from ..core.logging import get_logger

log = get_logger("mcp")

#: An env KEY that names a secret. Matched anywhere in the key, any case:
#: ``GITHUB_TOKEN``, ``api_key``, ``Authorization``, ``DB_PASSWORD``,
#: ``PASSWD``, ``CLIENT_SECRET``, ``AWS_CREDENTIALS``.
SECRET_KEY_RX = re.compile(r"(?i)(token|key|secret|passw|auth|credential)")

#: The vault-name prefix every pack secret shares: ``mcp.<server>.``.
VAULT_PREFIX = "mcp."


def is_secret_key(key: str) -> bool:
    """True when env key ``key`` names a secret by :data:`SECRET_KEY_RX`."""
    return bool(SECRET_KEY_RX.search(str(key or "")))


def vault_name(server: str, key: str) -> str:
    """The vault row a pack's env secret is kept under: ``mcp.<server>.<KEY>``."""
    return f"{VAULT_PREFIX}{server}.{key}"


def vault_prefix(server: str) -> str:
    """Every vault row of pack ``server`` starts with this."""
    return f"{VAULT_PREFIX}{server}."


def split_env(server: str, env: dict[str, Any] | None) -> tuple[dict[str, str], dict[str, str]]:
    """Split a pack's ``env`` into ``(plain_env, secret_values)`` by key name.

    ``secret_values`` maps KEY -> the plain value the caller must put in the
    vault (see :func:`store_env_secrets`); it never reaches the config. An
    empty value is not a secret worth a vault row and is dropped from both
    (an empty env var is never what the user meant).
    """
    plain: dict[str, str] = {}
    secret: dict[str, str] = {}
    for key, value in dict(env or {}).items():
        k = str(key)
        v = "" if value is None else str(value)
        if is_secret_key(k):
            if v:
                secret[k] = v
        else:
            plain[k] = v
    return plain, secret


def _kind_for(key: str) -> str:
    return "api_key" if "key" in key.lower() or "token" in key.lower() else "password"


def store_env_secrets(secrets: Any, server: str, secret_values: dict[str, str]) -> dict[str, str]:
    """Write every entry of ``secret_values`` to the vault and return the
    ``env_secrets`` map (KEY -> vault name) the stored config carries.

    Raises whatever the vault raises — the caller decides whether a pack may
    be saved without its secret (the POST door refuses; the migration skips
    that entry). The exception is re-raised AS IS: the vault's own errors
    carry no value.
    """
    env_secrets: dict[str, str] = {}
    for key, value in secret_values.items():
        name = vault_name(server, key)
        secrets.set(name, value, kind=_kind_for(key), description=f"MCP pack {server}: {key}")
        env_secrets[key] = name
    return env_secrets


def pack_vault_names(secrets: Any, server: str, cfg: dict[str, Any] | None = None) -> list[str]:
    """Every vault row that belongs to pack ``server``: its ``env_secrets``
    values under the pack prefix plus any ``mcp.<server>.*`` row the vault
    still holds (a row a failed migration or an older delete left behind)."""
    names: list[str] = []
    prefix = vault_prefix(server)
    for value in (dict((cfg or {}).get("env_secrets") or {})).values():
        sname = str(value or "")
        if sname.startswith(prefix) and sname not in names:
            names.append(sname)
    try:
        rows = secrets.list() if secrets is not None else []
    except Exception:  # noqa: BLE001 — a vault that cannot list still deletes the known names
        rows = []
    for row in rows:
        sname = str((row or {}).get("name") or "")
        if sname.startswith(prefix) and sname not in names:
            names.append(sname)
    return names


def delete_pack_secrets(secrets: Any, server: str, cfg: dict[str, Any] | None = None) -> list[str]:
    """Delete every vault row of pack ``server`` (see :func:`pack_vault_names`)
    and return the NAMES removed. A row that cannot be deleted is logged by
    name and skipped — the pack's config row is already gone."""
    removed: list[str] = []
    if secrets is None:
        return removed
    for name in pack_vault_names(secrets, server, cfg):
        try:
            if secrets.delete(name):
                removed.append(name)
        except Exception as exc:  # noqa: BLE001
            log.warning("MCP pack %r: vault row %r could not be deleted: %s", server, name, type(exc).__name__)
    return removed


def migrate_mcp_env_secrets(
    platform: Any = None, *, config: Any = None, secrets: Any = None
) -> dict[str, list[str]]:
    """Move every plain-text env secret in ``config.mcp_servers`` to the vault,
    ONCE, before any pack is launched.

    Pass a platform (``.config`` + ``.secrets``) or the two parts — the daemon
    calls this from ``build_platform`` where the platform object does not
    exist yet. Returns ``{"moved": [vault names], "skipped": ["<server>.<KEY>:
    <reason>"]}``; logs NAMES only. Idempotent: a second run finds no plain
    secret key and moves nothing. A vault write that fails leaves THAT
    entry's config untouched (its secret stays where it was rather than
    being lost) and lists it under ``skipped`` with the reason; a vault that
    cannot be used at all leaves the config untouched and logs why.
    """
    if platform is not None:
        config = getattr(platform, "config", None) if config is None else config
        secrets = getattr(platform, "secrets", None) if secrets is None else secrets
    result: dict[str, list[str]] = {"moved": [], "skipped": []}
    if config is None:
        return result
    servers_raw = getattr(config, "mcp_servers", None) or []
    servers = [s for s in servers_raw if isinstance(s, dict)]
    if not servers:
        return result
    if secrets is None or not hasattr(secrets, "set"):
        pending = [
            f"{s.get('name') or '?'}.{k}"
            for s in servers
            for k, v in dict(s.get("env") or {}).items()
            if is_secret_key(str(k)) and v
        ]
        if pending:
            log.warning(
                "MCP env secrets were NOT moved to the vault (no vault available): %s",
                ", ".join(pending),
            )
            result["skipped"].extend(f"{p}: no vault" for p in pending)
        return result

    changed = False
    new_servers: list[dict[str, Any]] = []
    for entry in servers_raw:
        if not isinstance(entry, dict):
            new_servers.append(entry)
            continue
        name = str(entry.get("name") or "")
        env = dict(entry.get("env") or {})
        plain, secret_values = split_env(name, env)
        if not secret_values:
            new_servers.append(entry)
            continue
        if not name:
            new_servers.append(entry)
            result["skipped"].extend(f"?.{k}: pack has no name" for k in secret_values)
            continue
        try:
            env_secrets = store_env_secrets(secrets, name, secret_values)
        except Exception as exc:  # noqa: BLE001 — the entry keeps its plain value
            reason = type(exc).__name__
            result["skipped"].extend(f"{name}.{k}: {reason}" for k in secret_values)
            log.warning(
                "MCP pack %r: env secret(s) %s stay in config.toml — the vault refused the write (%s)",
                name,
                ", ".join(sorted(secret_values)),
                reason,
            )
            new_servers.append(entry)
            continue
        updated = dict(entry)
        # Plain keys keep their values; a secret key with an EMPTY value (not
        # moved, nothing to keep) is dropped like the POST door drops it.
        updated["env"] = plain
        merged = dict(entry.get("env_secrets") or {})
        merged.update(env_secrets)
        updated["env_secrets"] = merged
        new_servers.append(updated)
        result["moved"].extend(env_secrets.values())
        changed = True

    if changed:
        config.mcp_servers = new_servers
        try:
            persist_config_values(config.home, {"mcp_servers": new_servers})
        except Exception as exc:  # noqa: BLE001
            # The in-memory config is already clean; the file is not. Say so
            # by name — the next boot runs this again and tries once more.
            log.error(
                "MCP env secrets moved to the vault but config.toml could not be rewritten (%s): %s",
                type(exc).__name__,
                ", ".join(result["moved"]),
            )
            return result
        log.info("MCP env secrets moved to the vault: %s", ", ".join(result["moved"]))
    return result
