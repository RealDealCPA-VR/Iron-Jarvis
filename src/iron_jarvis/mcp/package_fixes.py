"""Package names THIS APP shipped wrong, repaired in installed packs (v1.350.1).

The prebuilt MCP catalog in ``daemon/app.py`` offered
``@modelcontextprotocol/server-sequentialthinking`` — a name npm has never
had (the real package is ``server-sequential-thinking``; the Directory's
``connectors/catalog.py`` always had it right). Every pack added from that
row failed at its first call with "npm error 404 … is not in this registry".
The catalog is fixed; this repairs the packs already installed from it, ONCE,
at boot, before any pack launches (``build_platform``, beside
``migrate_mcp_env_secrets``).

Only names in :data:`RENAMED_PACKAGES` are touched — a table of mistakes the
app itself made, never a guess about a user's own pack. An argument is
replaced only when it IS the bad name, or the bad name with an ``@version``
suffix (kept). Names are logged; nothing else in the entry changes.
"""

from __future__ import annotations

from typing import Any

from ..core.config import persist_config_values
from ..core.logging import get_logger

log = get_logger(__name__)

#: wrong name the app shipped -> the real package.
RENAMED_PACKAGES: dict[str, str] = {
    "@modelcontextprotocol/server-sequentialthinking": "@modelcontextprotocol/server-sequential-thinking",
}


def _fixed_arg(arg: Any) -> Any:
    if not isinstance(arg, str):
        return arg
    for wrong, right in RENAMED_PACKAGES.items():
        if arg == wrong:
            return right
        if arg.startswith(wrong + "@"):
            return right + arg[len(wrong):]
    return arg


def repair_known_package_names(*, config: Any) -> list[str]:
    """Rewrite the args of every installed pack that names a package in
    :data:`RENAMED_PACKAGES`; persist and return the repaired pack names.
    Idempotent; never raises (a failed write is logged and the in-memory
    config still launches the right package this boot)."""
    try:
        servers_raw = list(getattr(config, "mcp_servers", None) or [])
    except Exception:  # noqa: BLE001
        return []
    repaired: list[str] = []
    new_servers: list[Any] = []
    for entry in servers_raw:
        if not isinstance(entry, dict) or not isinstance(entry.get("args"), list):
            new_servers.append(entry)
            continue
        args = list(entry["args"])
        fixed = [_fixed_arg(a) for a in args]
        if fixed == args:
            new_servers.append(entry)
            continue
        updated = dict(entry)
        updated["args"] = fixed
        new_servers.append(updated)
        repaired.append(str(entry.get("name") or "?"))
    if not repaired:
        return []
    config.mcp_servers = new_servers
    try:
        persist_config_values(config.home, {"mcp_servers": new_servers})
    except Exception as exc:  # noqa: BLE001 — this boot still launches the right name
        log.error(
            "MCP pack(s) %s: package name repaired in memory but config.toml could not be rewritten (%s)",
            ", ".join(repaired),
            type(exc).__name__,
        )
        return repaired
    log.info("MCP pack(s) %s: repaired a package name the app had shipped wrong", ", ".join(repaired))
    return repaired
