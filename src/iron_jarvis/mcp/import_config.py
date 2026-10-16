"""One pack shape for every door (v1.343.0, "add a pack in one paste").

Every way a pack reaches Iron Jarvis — a pasted JSON blob, a Claude Desktop
or Claude Code config, a Codex ``config.toml``, a Cursor or VS Code file, a
bare ``npx`` line, a URL, the Tools-page form — produces a :class:`PackSpec`,
and every route that stores a pack consumes one. The dict a spec turns into
(:meth:`PackSpec.to_config`) is the shape ``mcp/tools._build_transport``
launches and ``config.mcp_servers`` persists, MINUS the secret split: env and
header values ride the spec in memory only, and the ROUTE (``POST
/mcp/servers``) moves the secret-named ones to the vault before anything is
written. Nothing in this module logs, raises or returns a value.

Lane H owns the dataclass, :func:`safe_name` and :func:`guess_launcher`;
:func:`parse_any` and :func:`parse_file` are documented stubs for lane G,
which owns the parser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: The transports a spec may carry. ``sse`` is the older streaming shape;
#: ``_build_transport`` treats both as an :class:`HttpTransport`.
TRANSPORTS: tuple[str, ...] = ("stdio", "http", "sse")

#: Where a spec came from (the preview and the Tools page say it in words).
SOURCES: tuple[str, ...] = (
    "paste",
    "claude-code",
    "claude-desktop",
    "codex",
    "cursor",
    "vscode",
    "form",
)

#: A pack name: starts with a letter, then letters / digits / ``_`` / ``-``,
#: at most 40 characters — the same rule ``POST /mcp/servers`` has enforced
#: since the first catalog (``^[a-zA-Z][a-zA-Z0-9_-]{0,39}$``).
NAME_RX = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,39}$")
NAME_MAX = 40

_BAD_CHAR_RX = re.compile(r"[^a-zA-Z0-9_-]+")
_DASH_RUN_RX = re.compile(r"-{2,}")
_LEADING_NON_LETTER_RX = re.compile(r"^[^a-zA-Z]+")


def safe_name(raw: Any) -> str:
    """Normalise a pack name to the contract's shape.

    Lowercase; every character outside ``[a-zA-Z0-9_-]`` becomes ``-``; runs
    of ``-`` collapse to one; leading non-letters are stripped (a name must
    start with a letter); at most :data:`NAME_MAX` characters, never ending
    on a ``-``. ``""`` when nothing letter-like survives — the caller decides
    what to do with a nameless pack (the preview reports it, the add refuses).

    >>> safe_name("@modelcontextprotocol/server-github")
    'modelcontextprotocol-server-github'
    >>> safe_name("My Notes (local)")
    'my-notes-local'
    >>> safe_name("123-brave")
    'brave'
    """
    text = str(raw or "").strip().lower()
    text = _BAD_CHAR_RX.sub("-", text)
    text = _DASH_RUN_RX.sub("-", text)
    text = _LEADING_NON_LETTER_RX.sub("", text)
    text = text[:NAME_MAX].rstrip("-")
    return text


def is_safe_name(name: Any) -> bool:
    """True when ``name`` already satisfies :data:`NAME_RX`."""
    return bool(NAME_RX.match(str(name or "")))


#: PyPI-looking package names: ``mcp-server-git``, ``mcp_server_fetch``,
#: ``basic-memory`` — lower-case words joined by ``-``/``_`` with no scope,
#: no slash and no dot. An npm package with a scope (``@x/y``) or a dot in
#: the name (``server.js``) is never read as PyPI.
_PYPI_NAME_RX = re.compile(r"^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)+$")

#: Launchers the npm/GitHub door accepts (``POST /mcp/servers/from-package``).
LAUNCHERS: tuple[str, ...] = ("npx", "uvx", "pipx", "node", "python", "command")


def guess_launcher(pkg: Any) -> str:
    """Guess the launcher for a bare package reference.

    ``uvx`` for a ``pip:`` / ``pypi:`` prefix or a PyPI-looking bare name
    (``mcp-server-git``); ``npx`` for everything else (a scoped npm name, a
    GitHub URL, an ``npm:`` prefix, a name with a dot). The guess is a
    DEFAULT the form may override — a name like ``brave-search`` matches both
    worlds, and the npm registry is where most packs live, so a hyphenated
    name without the ``mcp``/``server`` words stays ``npx``.
    """
    text = str(pkg or "").strip()
    low = text.lower()
    if low.startswith(("pip:", "pypi:")):
        return "uvx"
    if low.startswith(("npm:", "npx:", "@", "github:", "http://", "https://", "git+")):
        return "npx"
    if "/" in low or "." in low or " " in low:
        return "npx"
    if _PYPI_NAME_RX.match(low) and "_" in low:
        # An underscore is not an npm convention; ``mcp_server_fetch`` is PyPI.
        return "uvx"
    if _PYPI_NAME_RX.match(low) and low.startswith("mcp-server-"):
        # The reference servers ship on PyPI as ``mcp-server-<x>`` and on npm
        # as ``@modelcontextprotocol/server-<x>``; the bare hyphenated name
        # is the PyPI one.
        return "uvx"
    return "npx"


@dataclass
class PackSpec:
    """The one shape every door produces and every route consumes.

    ``env`` / ``headers`` / ``auth.value`` carry plain VALUES while the spec
    is in memory. They never reach a preview row (``preview_row`` lists key
    and header NAMES only) and never reach ``config.mcp_servers`` as values:
    the route splits the secret-named ones into the vault first.
    """

    name: str = ""
    transport: str = "stdio"
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str = ""
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    #: ``{"header": "Authorization", "format": "Bearer {value}", "value": "<secret>"}``
    #: or ``None``.
    auth: dict[str, str] | None = None
    source: str = "paste"
    #: The file the spec was read from, or ``"paste"``.
    origin: str = "paste"

    # -- normalisation -------------------------------------------------- #
    def __post_init__(self) -> None:
        self.name = str(self.name or "")
        kind = str(self.transport or "stdio").strip().lower()
        if kind in ("streamable-http", "streamable_http"):
            kind = "http"
        self.transport = kind if kind in TRANSPORTS else "stdio"
        self.command = str(self.command or "").strip()
        self.args = [str(a) for a in (self.args or [])]
        self.env = {str(k): ("" if v is None else str(v)) for k, v in dict(self.env or {}).items()}
        self.cwd = str(self.cwd or "")
        self.url = str(self.url or "").strip()
        self.headers = {
            str(k): ("" if v is None else str(v)) for k, v in dict(self.headers or {}).items()
        }
        if isinstance(self.auth, dict):
            value = str(self.auth.get("value") or "")
            fmt = str(self.auth.get("format") or "Bearer {value}")
            self.auth = {
                "header": str(self.auth.get("header") or "Authorization"),
                "format": fmt if "{value}" in fmt else "Bearer {value}",
                "value": value,
            }
        else:
            self.auth = None
        self.source = str(self.source or "paste")
        self.origin = str(self.origin or "paste")

    # -- the stored / launched shape ------------------------------------ #
    def to_config(self) -> dict[str, Any]:
        """The dict ``_build_transport`` launches and ``config.mcp_servers``
        stores — EXCEPT that secrets are not split here. A stdio spec yields
        exactly ``{name, command, args, env[, cwd]}`` (today's stored shape); an
        http/sse spec yields ``{name, transport, url[, headers][, auth]}`` with
        ``auth`` as ``{header, format, value}``. The route turns ``env``
        secrets into ``env_secrets``, header secrets into ``header_secrets``
        and ``auth.value`` into ``auth.secret`` before persisting.
        """
        if self.transport == "stdio":
            cfg: dict[str, Any] = {
                "name": self.name,
                "command": self.command,
                "args": list(self.args),
                "env": dict(self.env),
            }
            if self.cwd:
                cfg["cwd"] = self.cwd
            return cfg
        cfg = {"name": self.name, "transport": self.transport, "url": self.url}
        if self.headers:
            cfg["headers"] = dict(self.headers)
        if self.auth and self.auth.get("value"):
            cfg["auth"] = dict(self.auth)
        return cfg

    @classmethod
    def from_config(cls, cfg: dict[str, Any], *, source: str = "form", origin: str = "form") -> "PackSpec":
        """A spec from a stored / launched config dict. Vault NAMES
        (``env_secrets``, ``header_secrets``, ``auth.secret``) are not values
        and are left out — a spec built from a stored row carries the plain
        parts only; the route resolves the vault when it launches."""
        cfg = dict(cfg or {})
        auth_raw = cfg.get("auth")
        auth: dict[str, str] | None = None
        if isinstance(auth_raw, dict):
            auth = {
                "header": str(auth_raw.get("header") or "Authorization"),
                "format": str(auth_raw.get("format") or "Bearer {value}"),
                "value": str(auth_raw.get("value") or ""),
            }
        return cls(
            name=str(cfg.get("name") or ""),
            transport=str(cfg.get("transport") or "stdio"),
            command=str(cfg.get("command") or ""),
            args=[str(a) for a in (cfg.get("args") or [])],
            env={str(k): str(v) for k, v in dict(cfg.get("env") or {}).items()},
            cwd=str(cfg.get("cwd") or ""),
            url=str(cfg.get("url") or ""),
            headers={str(k): str(v) for k, v in dict(cfg.get("headers") or {}).items()},
            auth=auth,
            source=source,
            origin=origin,
        )

    # -- what a preview may show --------------------------------------- #
    def preview_row(self) -> dict[str, Any]:
        """The spec with every VALUE removed: env keys, header names and
        whether an auth value is set. The preview route adds ``exists``,
        ``runtime_ready`` and ``launcher_note`` on top."""
        from .secrets import is_secret_key

        env_keys = sorted(self.env)
        return {
            "name": self.name,
            "transport": self.transport,
            "command": self.command,
            "args": list(self.args),
            "url": self.url,
            "header_names": sorted(self.headers),
            "env_keys": [k for k in env_keys if not is_secret_key(k)],
            "secret_keys": [k for k in env_keys if is_secret_key(k)],
            "auth_set": bool(self.auth and self.auth.get("value")),
            "source": self.source,
            "origin": self.origin,
        }


# --------------------------------------------------------------------------- #
# Parser stubs — lane G owns the bodies.
# --------------------------------------------------------------------------- #
def parse_any(text: str) -> list[PackSpec]:
    """Turn pasted text into specs; NEVER raises; ``[]`` for nothing
    recognisable. Recognised shapes (contract 2): JSON with ``mcpServers``
    (Claude Desktop / Claude Code, incl. per-project ``projects`` flattened),
    JSON with ``mcp.servers`` (VS Code), a single server object, TOML
    ``[mcp_servers.<name>]`` tables (Codex), one command line, a bare URL, an
    ``npx mcp-remote <url>`` line. Lane G fills this in."""
    return []


def parse_file(path: Any) -> tuple[list[PackSpec], bool, str]:
    """Read ONE config file read-only → ``(specs, readable, note)``: a
    missing file is ``([], False, "")``; a malformed one is ``([], False,
    "<one line naming the file>")`` and never its contents. Lane G fills
    this in."""
    return ([], False, "")
