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

v1.351.0 (G12): a secret can also sit INSIDE an argument or a URL — an
``mcp-remote`` block's ``--header "Authorization: Bearer …"``, ``--api-key=…``,
``--api-key …``, a ``postgresql://user:pass@host`` connection string, a
``?api_key=`` url. The rule there is the VALUE's shape, the one
``detections.redact.mask`` already knows: an argument ``mask`` changes (or the
value after a secret-named flag) goes to the vault as ``mcp.<server>.arg.<i>``
and the stored ``args[i]`` keeps the MASKED text (``Authorization: Bearer
***``) with ``arg_secrets[str(i)]`` naming the vault row; a url ``mask``
changes goes to ``mcp.<server>.url`` with ``url_secret``. ``mcp/tools.
_build_transport`` puts the real values back at LAUNCH, beside
``env_secrets``/``header_secrets``. And the key rule learned the names it
missed: a ``PAT`` word (``GITHUB_PAT``, never ``PATH``), a ``*_URL`` key
(``DATABASE_URL``), ``DSN`` and ``connection``.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from ..core.config import persist_config_values
from ..core.logging import get_logger
from ..detections.redact import MASK, mask

log = get_logger("mcp")

#: An env KEY that names a secret. Matched anywhere in the key, any case:
#: ``GITHUB_TOKEN``, ``api_key``, ``Authorization``, ``DB_PASSWORD``,
#: ``PASSWD``, ``CLIENT_SECRET``, ``AWS_CREDENTIALS``. v1.351.0 (G12): also
#: ``SENTRY_DSN``, ``DB_CONNECTION_STRING``, a ``PAT`` WORD (``GITHUB_PAT``,
#: ``PAT_VALUE`` — bounded by ``_``/``-``/``.`` or the ends, so ``PATH``,
#: ``PYTHONPATH`` and ``PATTERN`` stay plain) and a key ENDING in ``_URL``
#: (``DATABASE_URL`` — a connection url carries its password).
SECRET_KEY_RX = re.compile(
    r"(?i)(token|key|secret|passw|auth|credential|dsn|connection|(?:^|[_.-])pat(?:$|[_.-])|_url$)"
)

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


# --------------------------------------------------------------------------- #
# v1.351.0 (G12): secrets INSIDE an argument or a url.
# --------------------------------------------------------------------------- #

#: The flag words after which the NEXT argument is a value to keep in the
#: vault (``--api-key VALUE``, ``--token VALUE``, ``--password VALUE``),
#: matched on the flag's own words (split on ``-``/``_``/``.``) so
#: ``--keyword``, ``--keyboard`` and ``--monkey`` are not secret flags.
_SECRET_FLAG_WORDS: frozenset[str] = frozenset(
    {
        "token",
        "key",
        "apikey",
        "secret",
        "password",
        "passwd",
        "pass",
        "pwd",
        "auth",
        "credential",
        "credentials",
        "pat",
        "dsn",
    }
)

#: A secret-named flag whose LAST word is one of these takes a name, a path or
#: a kind, not the secret itself: ``--key-file``, ``--token-path``,
#: ``--auth-type``, ``--auth-header-name``, ``--api-key-env``.
_NOT_A_VALUE_WORDS: frozenset[str] = frozenset(
    {"file", "path", "dir", "type", "mode", "name", "id", "url", "header", "method", "scheme", "env", "var"}
)

#: The vault-name KEY of a pack's url secret: ``mcp.<server>.url``.
URL_KEY = "url"


def _secret_flag(arg: str) -> bool:
    """True when ``arg`` is a flag whose NEXT argument is its secret value."""
    a = str(arg or "")
    if not a.startswith("-") or "=" in a:
        return False
    words = [w for w in re.split(r"[-_.]+", a.lstrip("-").lower()) if w]
    if not words or words[-1] in _NOT_A_VALUE_WORDS:
        return False
    return any(w in _SECRET_FLAG_WORDS for w in words)


def arg_key(index: int) -> str:
    """The vault-name KEY of argument ``index``: ``arg.<index>``."""
    return f"arg.{int(index)}"


def split_args(args: list[Any] | None, *, keep: Any = ()) -> tuple[list[str], dict[int, str]]:
    """Split a pack's ``args`` into ``(shown_args, secret_values)``.

    ``shown_args`` is what the config stores and every answer shows: each
    argument through ``detections.redact.mask`` (``Authorization: Bearer
    ***``, ``--api-key=***``, ``postgresql://me:***@host/db``), and the value
    after a secret-named flag (``--api-key VALUE``) as ``***``.
    ``secret_values`` maps INDEX -> the real argument for every one of those —
    the caller puts it in the vault (:func:`store_arg_secrets`); it never
    reaches the config. ``keep`` lists indexes already vaulted (their stored
    text is a placeholder), and an argument that IS the mask is a placeholder
    too, so a second pass moves nothing (idempotent: ``mask`` of a masked
    text is that text)."""
    kept = {int(k) for k in keep if str(k).lstrip("-").isdigit()}
    shown: list[str] = []
    values: dict[int, str] = {}
    prev = ""
    for i, raw in enumerate(list(args or [])):
        a = "" if raw is None else str(raw)
        if i in kept or a == MASK or not a:
            shown.append(a)
            prev = a
            continue
        out = mask(a)
        if out == a and _secret_flag(prev) and not a.startswith("-"):
            out = MASK
        if out != a:
            values[i] = a
        shown.append(out)
        prev = a
    return shown, values


def split_url(url: Any) -> tuple[str, str]:
    """``(shown_url, secret_url)``: the url through ``mask`` (``?api_key=***``,
    ``https://me:***@host``) and, when that changed it, the real url for the
    vault (``""`` otherwise)."""
    u = "" if url is None else str(url)
    shown = mask(u) if u else u
    return shown, (u if shown != u else "")


def store_arg_secrets(secrets: Any, server: str, values: dict[int, str]) -> dict[str, str]:
    """Write each argument secret to the vault as ``mcp.<server>.arg.<i>`` and
    return the ``arg_secrets`` map (``str(i)`` -> vault name) the stored config
    carries. Raises whatever the vault raises (names only), like
    :func:`store_env_secrets`."""
    out: dict[str, str] = {}
    for i in sorted(values):
        name = vault_name(server, arg_key(i))
        secrets.set(name, values[i], kind="api_key", description=f"MCP pack {server}: argument {int(i) + 1}")
        out[str(int(i))] = name
    return out


def store_url_secret(secrets: Any, server: str, value: str) -> str:
    """Write the real url to the vault as ``mcp.<server>.url``; returns that
    name (the stored config's ``url_secret``). Raises what the vault raises."""
    name = vault_name(server, URL_KEY)
    secrets.set(name, value, kind="api_key", description=f"MCP pack {server}: url")
    return name


Resolver = Callable[[str], Any]


def _resolved(resolver: Resolver | None, name: Any) -> str:
    if resolver is None or not name:
        return ""
    try:
        value = resolver(str(name))
    except Exception:  # noqa: BLE001 — a vault miss keeps the placeholder
        return ""
    return str(value) if value else ""


def resolve_args(args: list[Any] | None, arg_secrets: Any, resolver: Resolver | None) -> list[str]:
    """The argv a pack LAUNCHES with: the stored args with each vaulted index
    replaced by its vault value. A vault miss (or an index out of range) keeps
    the stored placeholder — the pack then fails honestly, never with a
    guess."""
    out = ["" if a is None else str(a) for a in list(args or [])]
    if not isinstance(arg_secrets, dict):
        return out
    for idx, sname in arg_secrets.items():
        try:
            i = int(str(idx))
        except ValueError:
            continue
        if not 0 <= i < len(out):
            continue
        value = _resolved(resolver, sname)
        if value:
            out[i] = value
    return out


def resolve_url(url: Any, url_secret: Any, resolver: Resolver | None) -> str:
    """The url a pack CONNECTS to: the vault value of ``url_secret`` when there
    is one, else the stored url."""
    value = _resolved(resolver, url_secret) if url_secret else ""
    return value or ("" if url is None else str(url))


class MaskedValueLeft(ValueError):
    """An edit sent back a ``***`` this module cannot match to a stored value.
    The message names the ARGUMENT (its position), never a value."""


def _tokens(values: list[str]) -> list[str]:
    return [t for v in values for t in str(v).split()]


def carry_masked_args(
    new_args: list[Any], old_shown: list[Any], old_real: list[Any]
) -> tuple[list[str], set[int]]:
    """An EDIT's args with the real value put back wherever it sent the MASKED
    text of an argument it did not change, and the INDEXES put back.

    The editor shows the stored (masked) args and sends them back when anything
    in the line changed — sometimes split on whitespace (``Authorization:``,
    ``Bearer``, ``***``). Every new argument holding ``***`` is matched to the
    stored argument it shows (whole, else the same whitespace token) and gets
    that argument's REAL text. One that matches nothing raises
    :class:`MaskedValueLeft`: storing a literal ``***`` would silently replace
    a working secret with the mask. The caller vaults every index in the
    returned set whatever ``mask`` says of it: a token split off its
    ``Authorization:`` no longer LOOKS like a secret, and still is one."""
    shown = ["" if a is None else str(a) for a in old_shown]
    real = ["" if a is None else str(a) for a in old_real]
    whole = {s: r for s, r in zip(shown, real) if MASK in s}
    by_token: dict[str, str] = {}
    for s, r in zip(shown, real):
        # Token by token only where the argument splits the same way both
        # times (``mask`` swaps a non-space run for ``***``, so it does).
        s_tok, r_tok = s.split(), r.split()
        if MASK in s and len(s_tok) == len(r_tok):
            for st, rt in zip(s_tok, r_tok):
                if MASK in st and st not in by_token:
                    by_token[st] = rt
    out: list[str] = []
    carried: set[int] = set()
    for n, raw in enumerate(list(new_args or [])):
        a = "" if raw is None else str(raw)
        if MASK in a:
            if a in whole:
                a = whole[a]
            elif a in by_token:
                a = by_token[a]
            else:
                raise MaskedValueLeft(
                    f"argument {n + 1} still shows {MASK} where a hidden value was; "
                    "type the value again, or leave the arguments as they were"
                )
            carried.add(n)
        out.append(a)
    return out, carried


def args_unchanged(new_args: list[Any], old_shown: list[Any]) -> bool:
    """True when an edit sent back the stored args as they are — whole, or,
    when they show a hidden value, split on whitespace (the editor's one-line
    form splits ``Authorization: Bearer ***`` into three; re-storing those
    would turn one secret argument into a plain token). Args with nothing
    hidden keep the old rule: what is sent is what is stored."""
    new = ["" if a is None else str(a) for a in list(new_args or [])]
    shown = ["" if a is None else str(a) for a in list(old_shown or [])]
    if new == shown:
        return True
    return any(MASK in s for s in shown) and new == _tokens(shown)


def pack_vault_names(secrets: Any, server: str, cfg: dict[str, Any] | None = None) -> list[str]:
    """Every vault row that belongs to pack ``server``: its ``env_secrets``
    values under the pack prefix plus any ``mcp.<server>.*`` row the vault
    still holds (a row a failed migration or an older delete left behind).
    v1.351.0: also its ``header_secrets``, ``auth.secret``, ``arg_secrets``
    and ``url_secret`` — so a vault that cannot list still deletes them."""
    names: list[str] = []
    prefix = vault_prefix(server)
    cfg = dict(cfg or {})
    known: list[Any] = list(dict(cfg.get("env_secrets") or {}).values())
    known += list(dict(cfg.get("header_secrets") or {}).values())
    known += list(dict(cfg.get("arg_secrets") or {}).values())
    known.append(cfg.get("url_secret"))
    if isinstance(cfg.get("auth"), dict):
        known.append(cfg["auth"].get("secret"))
    for value in known:
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


def _is_http_entry(entry: dict[str, Any]) -> bool:
    t = entry.get("transport")
    return isinstance(t, str) and t.lower() in ("http", "sse", "streamable-http", "streamable_http")


def _place_secrets(entry: dict[str, Any]) -> tuple[list[str], dict[int, str], str, str]:
    """A stored entry's ``(shown_args, arg_values, shown_url, url_value)``:
    the argument secrets of a stdio pack not yet in the vault (indexes in its
    ``arg_secrets`` are placeholders already) and the url secret of an http
    pack with no ``url_secret`` yet."""
    if _is_http_entry(entry):
        if entry.get("url_secret"):
            return [], {}, "", ""
        shown_url, url_value = split_url(entry.get("url"))
        return [], {}, shown_url, url_value
    raw_args = entry.get("args")
    if not isinstance(raw_args, list):
        return [], {}, "", ""
    shown, values = split_args(raw_args, keep=dict(entry.get("arg_secrets") or {}).keys())
    return shown, values, "", ""


def _place_secret_keys(entry: dict[str, Any]) -> list[str]:
    _, arg_values, _, url_value = _place_secrets(entry)
    return [arg_key(i) for i in sorted(arg_values)] + ([URL_KEY] if url_value else [])


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

    v1.351.0 (G12): the same pass moves an entry's ARGUMENT secrets
    (``mcp.<server>.arg.<i>``, the arg keeps its masked text, ``arg_secrets``
    names the row) and an http pack's URL secret (``mcp.<server>.url``,
    ``url_secret``) — together with its env secrets, all or nothing per entry.
    A ``skipped`` line names ``<server>.arg.<i>`` / ``<server>.url``.
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
        pending: list[str] = []
        for s in servers:
            sname = s.get("name") or "?"
            pending += [f"{sname}.{k}" for k, v in dict(s.get("env") or {}).items() if is_secret_key(str(k)) and v]
            pending += [f"{sname}.{k}" for k in _place_secret_keys(s)]
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
        # v1.351.0 (G12): the argument and url secrets of the same entry,
        # moved in the SAME all-or-nothing step as its env secrets.
        shown_args, arg_values, shown_url, url_value = _place_secrets(entry)
        keys = list(secret_values) + [arg_key(i) for i in sorted(arg_values)] + ([URL_KEY] if url_value else [])
        if not keys:
            new_servers.append(entry)
            continue
        if not name:
            new_servers.append(entry)
            result["skipped"].extend(f"?.{k}: pack has no name" for k in keys)
            continue
        try:
            env_secrets = store_env_secrets(secrets, name, secret_values)
            arg_secrets = store_arg_secrets(secrets, name, arg_values)
            url_secret = store_url_secret(secrets, name, url_value) if url_value else ""
        except Exception as exc:  # noqa: BLE001 — the entry keeps its plain value
            reason = type(exc).__name__
            result["skipped"].extend(f"{name}.{k}: {reason}" for k in keys)
            log.warning(
                "MCP pack %r: secret(s) %s stay in config.toml — the vault refused the write (%s)",
                name,
                ", ".join(keys),
                reason,
            )
            new_servers.append(entry)
            continue
        updated = dict(entry)
        if secret_values:
            # Plain keys keep their values; a secret key with an EMPTY value (not
            # moved, nothing to keep) is dropped like the POST door drops it.
            updated["env"] = plain
            merged = dict(entry.get("env_secrets") or {})
            merged.update(env_secrets)
            updated["env_secrets"] = merged
        if arg_secrets:
            updated["args"] = shown_args
            merged_args = {str(k): str(v) for k, v in dict(entry.get("arg_secrets") or {}).items()}
            merged_args.update(arg_secrets)
            updated["arg_secrets"] = merged_args
        if url_secret:
            updated["url"] = shown_url
            updated["url_secret"] = url_secret
        new_servers.append(updated)
        result["moved"].extend([*env_secrets.values(), *arg_secrets.values(), *([url_secret] if url_secret else [])])
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
