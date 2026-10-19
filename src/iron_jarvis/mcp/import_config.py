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
#: v1.354.0 (G50): Windsurf and Gemini CLI read through the same parser.
SOURCES: tuple[str, ...] = (
    "paste",
    "claude-code",
    "claude-desktop",
    "codex",
    "cursor",
    "vscode",
    "windsurf",
    "gemini-cli",
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


_PACKAGE_PREFIX_RX = re.compile(r"^(?:pip|pypi|npm):", re.IGNORECASE)


def bare_package(pkg: Any) -> str:
    """``pkg`` without a ``pip:`` / ``pypi:`` / ``npm:`` prefix, stripped."""
    return _PACKAGE_PREFIX_RX.sub("", str(pkg or "").strip())


def build_launch(pkg: Any, launcher: Any) -> tuple[str, list[str]]:
    """The ``(command, args)`` that starts package ``pkg`` under ``launcher``
    (lane J, the npm/GitHub door's server half; the dashboard's
    ``lib/mcpImport.buildLaunch`` is its twin and must agree):

    * ``npx``     -> ``npx -y <pkg>``
    * ``uvx``     -> ``uvx <pkg>``
    * ``pipx``    -> ``pipx run <pkg>``
    * ``node``    -> ``node <pkg>``
    * ``python``  -> ``python -m <pkg>``
    * ``command`` -> the text as a command line (first word runs, the rest
      are its args; a quoted word stays one arg)

    An unknown launcher reads as ``npx``. ``("", [])`` when nothing is left
    after the prefix is stripped.
    """
    p = bare_package(pkg)
    kind = str(launcher or "npx").strip().lower()
    if not p:
        return "", []
    if kind == "uvx":
        return "uvx", [p]
    if kind == "pipx":
        return "pipx", ["run", p]
    if kind == "node":
        return "node", [p]
    if kind == "python":
        return "python", ["-m", p]
    if kind == "command":
        words = _split_command_line(p)
        return (words[0] if words else ""), words[1:]
    return "npx", ["-y", p]


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
    #: v1.354.0 (G50): the other app has this server switched OFF
    #: (``"disabled": true`` / ``"enabled": false`` on the entry, a Claude
    #: Code project's ``disabledMcpServers``, Gemini CLI's ``mcp.excluded``,
    #: a Claude Code plugin turned off in ``enabledPlugins``). The preview
    #: row says so and the import door leaves the row unticked. NOT stored:
    #: a server the user ticks anyway is added like any other — the press is
    #: the choice.
    disabled: bool = False
    #: v1.355.0 integrate: where the imported config held a PLACEHOLDER
    #: instead of a value (VS Code's ``${input:token}``, a plugin's
    #: ``${GITHUB_TOKEN}``; never ``${CLAUDE_PLUGIN_ROOT}``, which the import
    #: writes in). Each entry ``{"where": "env"|"header"|"arg", "key": <env
    #: key | header name | arg index>, "template": <the original text>,
    #: "names": {<placeholder text>: <the name the user types>}}``; the value
    #: itself is blanked in ``env`` / ``headers`` so a placeholder never
    #: reaches the vault as if it were the secret. NOT stored: the import
    #: door asks for every name (:attr:`asks`) and fills the template
    #: (:func:`fill_placeholders`).
    fills: list[dict[str, Any]] = field(default_factory=list)

    @property
    def asks(self) -> list[str]:
        """The names the import door asks the user to type, in order."""
        out: list[str] = []
        for fill in self.fills:
            for name in dict(fill.get("names") or {}).values():
                if name and name not in out:
                    out.append(str(name))
        return out

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
        self.disabled = self.disabled is True

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
        ``runtime_ready`` and ``launcher_note`` on top. The command, its
        args and the url are shown MASKED (``detections.redact.mask``): a
        pasted ``docker run -e TOKEN=ghp_... img`` or a ``?api_key=`` url
        carries its secret in the text itself, and the row is read by the
        browser. The spec in memory keeps the real values for the add."""
        from ..detections.redact import mask
        from .secrets import is_secret_key

        env_keys = sorted(self.env)
        return {
            "name": self.name,
            "transport": self.transport,
            "command": mask(self.command),
            "args": [mask(a) for a in self.args],
            "url": mask(self.url),
            "header_names": sorted(self.headers),
            "env_keys": [k for k in env_keys if not is_secret_key(k)],
            "secret_keys": [k for k in env_keys if is_secret_key(k)],
            "auth_set": bool(self.auth and self.auth.get("value")),
            "source": self.source,
            "origin": self.origin,
            # v1.354.0 (G50): switched off in the app it came from.
            "disabled": bool(self.disabled),
            # v1.355.0 integrate: names the config left as placeholders.
            **({"asks": self.asks} if self.fills else {}),
        }


# --------------------------------------------------------------------------- #
# Placeholders (v1.355.0 integrate, the wave-4 follow-up): a value the other
# app fills in at launch is not a value. VS Code asks for ``${input:id}`` and
# reads ``${env:NAME}``; a Claude Code plugin's ``.mcp.json`` reads
# ``${NAME}`` (``${NAME:-default}`` uses the default when NAME is unset).
# ``${CLAUDE_PLUGIN_ROOT}`` is the plugin's folder and the import writes it in
# (import_sources._with_plugin_root), so it is never a question.
# --------------------------------------------------------------------------- #

_PLACEHOLDER_RX = re.compile(r"\$\{([^{}]+)\}")
#: Placeholders the import resolves itself.
_RESOLVED_PLACEHOLDERS: frozenset[str] = frozenset({"CLAUDE_PLUGIN_ROOT"})


def _placeholder_name(inner: str) -> tuple[str, str]:
    """``(name, default)`` of one ``${...}``'s inside: ``input:gh_token`` →
    ``("gh_token", "")``, ``env:HOME`` → ``("HOME", "")``, ``TOKEN:-x`` →
    ``("TOKEN", "x")``."""
    body = inner.strip()
    default = ""
    if ":-" in body:
        body, default = body.split(":-", 1)
    for prefix in ("input:", "env:", "config:"):
        if body.lower().startswith(prefix):
            body = body[len(prefix):]
            break
    return body.strip(), default


def _with_defaults(text: str) -> str:
    """Write in every ``${NAME:-default}`` that has a default (what the
    other app does when NAME is unset); other placeholders stay."""

    def _sub(m: "re.Match[str]") -> str:
        name, default = _placeholder_name(m.group(1))
        if name in _RESOLVED_PLACEHOLDERS or not default:
            return m.group(0)
        return default

    return _PLACEHOLDER_RX.sub(_sub, text)


def _open_placeholders(text: str) -> list[tuple[str, str]]:
    """``[(placeholder text, name)]`` still open in ``text`` (defaults
    already written in; ``${CLAUDE_PLUGIN_ROOT}`` is never open)."""
    out: list[tuple[str, str]] = []
    for m in _PLACEHOLDER_RX.finditer(text):
        name, _default = _placeholder_name(m.group(1))
        if name and name not in _RESOLVED_PLACEHOLDERS:
            out.append((m.group(0), name))
    return out


def mark_placeholders(spec: PackSpec) -> PackSpec:
    """Turn every placeholder in ``spec``'s env values, header values and
    args into a :attr:`PackSpec.fills` entry the import door asks for.

    A value that is WHOLLY one placeholder is asked by its own name (the env
    key, the header name; an argument has none, so its placeholder's name)
    and blanked; a value with text around a placeholder (``Bearer ${TOKEN}``)
    asks for the placeholder's name and keeps the template, so the typed
    value is filled in where it stood. Never raises."""
    try:
        fills: list[dict[str, Any]] = []
        for key in list(spec.env):
            text = _with_defaults(spec.env[key])
            spec.env[key] = text
            found = _open_placeholders(text)
            if not found:
                continue
            whole = len(found) == 1 and text.strip() == found[0][0]
            names = {found[0][0]: key} if whole else {ph: name for ph, name in found}
            fills.append({"where": "env", "key": key, "template": text, "names": names})
            spec.env[key] = ""
        for key in list(spec.headers):
            text = _with_defaults(spec.headers[key])
            spec.headers[key] = text
            found = _open_placeholders(text)
            if not found:
                continue
            whole = len(found) == 1 and text.strip() == found[0][0]
            names = {found[0][0]: key} if whole else {ph: name for ph, name in found}
            fills.append({"where": "header", "key": key, "template": text, "names": names})
            spec.headers[key] = ""
        for i, arg in enumerate(list(spec.args)):
            text = _with_defaults(arg)
            spec.args[i] = text
            found = _open_placeholders(text)
            if found:
                fills.append(
                    {"where": "arg", "key": i, "template": text, "names": {ph: name for ph, name in found}}
                )
        spec.fills = fills
    except Exception:  # noqa: BLE001 — a parser never raises into a route
        pass
    return spec


def fill_placeholders(spec: PackSpec, typed: dict[str, str]) -> list[str]:
    """Fill ``spec``'s placeholders from the values the user ``typed`` (by
    ask name) and answer the names still missing where the pack cannot do
    without them: an ARGUMENT's (it is positional). An env value or a header
    left untyped is dropped (the pack starts without it, as with no
    config value), never sent as the placeholder's text."""
    missing: list[str] = []
    for fill in spec.fills:
        names = dict(fill.get("names") or {})
        text = str(fill.get("template") or "")
        complete = True
        for ph, name in names.items():
            value = typed.get(name)
            if value is None or value == "":
                complete = False
                continue
            text = text.replace(ph, str(value))
        where = fill.get("where")
        key = fill.get("key")
        if where == "env":
            if complete:
                spec.env[str(key)] = text
            else:
                spec.env.pop(str(key), None)
        elif where == "header":
            if complete:
                spec.headers[str(key)] = text
            else:
                spec.headers.pop(str(key), None)
        elif where == "arg" and isinstance(key, int) and 0 <= key < len(spec.args):
            if complete:
                spec.args[key] = text
            else:
                missing.extend(n for ph, n in names.items() if not typed.get(n) and n not in missing)
    return missing


def _with_asks(specs: list[PackSpec]) -> list[PackSpec]:
    return [mark_placeholders(sp) for sp in specs]


# --------------------------------------------------------------------------- #
# The parser (lane G). Every branch is pure: text in, specs out, no I/O,
# no logging, no raising. VALUES (env, headers, an auth token) ride the spec
# in memory only — the preview route lists names, the apply route hands the
# spec to the one add door, which splits the secrets into the vault.
# --------------------------------------------------------------------------- #

#: Marks of Iron Jarvis's OWN MCP server (the Build-pane door, the inert
#: claude-cli server): a config that lists it is pointing back at us, and
#: importing it would make the daemon a client of itself.
_OWN_SERVER_MARKS: tuple[str, ...] = (
    "ironjarvis",
    "iron_jarvis",
    "mcp-stdio",
    "claude-inert-mcp",
)

#: First words that make a bare line a COMMAND line (contract 2). A line
#: whose first word is none of these, has no path separator and no program
#: extension is prose, and prose is "nothing recognisable".
_LAUNCHERS: frozenset[str] = frozenset(
    {
        "npx",
        "npm",
        "pnpm",
        "yarn",
        "bunx",
        "bun",
        "deno",
        "node",
        "uvx",
        "uv",
        "pipx",
        "pip",
        "python",
        "python3",
        "py",
        "docker",
        "podman",
        "dotnet",
        "java",
        "cargo",
        "go",
        "ruby",
        "php",
        "sh",
        "bash",
        "zsh",
        "cmd",
        "powershell",
        "pwsh",
        "mcp-proxy",
        "mcp-remote",
    }
)
_PROGRAM_EXTS: tuple[str, ...] = (".exe", ".cmd", ".bat", ".js", ".mjs", ".cjs", ".py", ".sh", ".ps1")

_URL_RX = re.compile(r"^https?://\S+$", re.IGNORECASE)
_LINE_COMMENT_RX = re.compile(r"//[^\n]*")
_BLOCK_COMMENT_RX = re.compile(r"/\*.*?\*/", re.DOTALL)
_VERSION_SUFFIX_RX = re.compile(r"@[~^]?\d[^/]*$")
_TRANSPORT_WORDS = {
    "stdio": "stdio",
    "http": "http",
    "streamable-http": "http",
    "streamable_http": "http",
    "streamablehttp": "http",
    "sse": "sse",
}
#: Keys that mean "this dict is a server entry" (any config dialect).
_SERVER_KEYS: frozenset[str] = frozenset(
    {"command", "url", "httpUrl", "serverUrl", "transport", "type", "args"}
)
#: Keys of a server entry that NEVER mean "a map of servers".
_ENTRY_META_KEYS: frozenset[str] = frozenset(
    {"disabled", "enabled", "alwaysAllow", "autoApprove", "timeout", "env", "headers"}
)
#: Arguments to ``npx`` that take a value of their own (the value is not
#: the package).
_NPX_VALUE_FLAGS: frozenset[str] = frozenset({"-p", "--package", "-c", "--call", "--shell"})
#: Arguments to ``docker run`` that take a value of their own.
_DOCKER_VALUE_FLAGS: frozenset[str] = frozenset(
    {"-e", "--env", "-v", "--volume", "-p", "--publish", "--name", "-w", "--workdir", "--network", "-u", "--user", "--entrypoint", "--mount", "--env-file", "-h", "--hostname", "--platform", "-l", "--label"}
)


def strip_json_comments(text: str) -> str:
    """Remove ``//`` and ``/* */`` comments OUTSIDE strings (VS Code's
    ``settings.json`` carries both). Trailing commas are left for
    :func:`core.jsonish.loads_object`. String-aware so a ``//`` inside a URL
    value survives."""
    out: list[str] = []
    i, n = 0, len(text)
    in_str = False
    escape = False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "/":
                j = text.find("\n", i)
                i = n if j < 0 else j
                continue
            if nxt == "*":
                j = text.find("*/", i + 2)
                i = n if j < 0 else j + 2
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _is_own_server(command: str, args: list[str]) -> bool:
    hay = " ".join([command, *args]).lower()
    return any(mark in hay for mark in _OWN_SERVER_MARKS)


def _looks_like_server(entry: Any) -> bool:
    return isinstance(entry, dict) and any(k in entry for k in _SERVER_KEYS)


def _looks_like_server_map(obj: Any) -> bool:
    """``{"github": {...}, "notion": {...}}`` with every value a server entry."""
    if not isinstance(obj, dict) or not obj:
        return False
    if any(k in _SERVER_KEYS or k in _ENTRY_META_KEYS for k in obj):
        return False
    return all(_looks_like_server(v) for v in obj.values())


def _str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return _split_command_line(value)
    if isinstance(value, (list, tuple)):
        return [str(a) for a in value]
    return []


def _str_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k): ("" if v is None else str(v)) for k, v in value.items()}


def _spec_from_entry(raw_name: str, entry: dict[str, Any], *, source: str, origin: str) -> PackSpec | None:
    """One config-dialect server entry → a spec, or ``None`` for Iron
    Jarvis's own server / an entry with neither a command nor a URL."""
    command = str(entry.get("command") or "").strip()
    args = _str_list(entry.get("args"))
    # Codex's `command = "npx -y pkg"` and VS Code's "command": "npx" both
    # appear; a command carrying spaces is a command LINE.
    if command and not args and " " in command and not _has_path_sep(command):
        words = _split_command_line(command)
        if words:
            command, args = words[0], words[1:]
    url = ""
    url_key = ""
    for key in ("url", "httpUrl", "serverUrl"):
        if isinstance(entry.get(key), str) and entry[key].strip():
            url = entry[key].strip()
            url_key = key
            break
    kind_raw = str(entry.get("transport") or entry.get("type") or "").strip().lower()
    kind = _TRANSPORT_WORDS.get(kind_raw, "")
    if not kind and url:
        # v1.354.0 (G50): Gemini CLI names the transport by the KEY —
        # ``httpUrl`` is streamable HTTP, ``url`` is SSE — so its address
        # need not end in /sse. Everyone else: the /sse suffix rule.
        if url_key == "httpUrl":
            kind = "http"
        elif url_key == "url" and source == "gemini-cli":
            kind = "sse"
    if not kind:
        kind = ("sse" if url.lower().rstrip("/").endswith("/sse") else "http") if url else "stdio"
    if kind == "stdio" and not command and url:
        kind = "sse" if url.lower().rstrip("/").endswith("/sse") else "http"
    if kind == "stdio" and not command:
        return None
    if kind != "stdio" and not url:
        return None
    if _is_own_server(command, args) or any(m in url.lower() for m in _OWN_SERVER_MARKS):
        return None
    env = _str_map(entry.get("env"))
    headers = _str_map(entry.get("headers") or entry.get("http_headers"))
    auth_raw = entry.get("auth")
    auth = dict(auth_raw) if isinstance(auth_raw, dict) and auth_raw.get("value") else None
    cwd = str(entry.get("cwd") or entry.get("working_directory") or "").strip()
    return PackSpec(
        name=safe_name(raw_name),
        transport=kind,
        command=command if kind == "stdio" else "",
        args=args if kind == "stdio" else [],
        env=env if kind == "stdio" else {},
        cwd=cwd if kind == "stdio" else "",
        url=url if kind != "stdio" else "",
        headers=headers if kind != "stdio" else {},
        auth=auth if kind != "stdio" else None,
        source=source,
        origin=origin,
        disabled=entry_switched_off(entry),
    )


def entry_switched_off(entry: Any) -> bool:
    """v1.354.0 (G50): is this server entry switched OFF in its app?
    ``"disabled": true`` (Claude Desktop, Windsurf, Cline-style configs) or
    ``"enabled": false`` (Codex and others). Only a real boolean (or its
    plain-word spelling) counts: an entry that says nothing is ON."""
    if not isinstance(entry, dict):
        return False

    def _flag(value: Any) -> bool | None:
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in ("true", "false"):
            return value.strip().lower() == "true"
        return None

    return _flag(entry.get("disabled")) is True or _flag(entry.get("enabled")) is False


def _specs_from_map(
    servers: Any, *, source: str, origin: str, off_names: frozenset[str] = frozenset()
) -> list[PackSpec]:
    """Every entry of a ``{name: entry}`` map as a spec. ``off_names`` (raw
    names, as the app wrote them) are switched off by a list kept BESIDE the
    map (a Claude Code project's ``disabledMcpServers``)."""
    out: list[PackSpec] = []
    if not isinstance(servers, dict):
        return out
    for raw_name, entry in servers.items():
        if not isinstance(entry, dict):
            continue
        spec = _spec_from_entry(str(raw_name), entry, source=source, origin=origin)
        if spec is not None:
            if str(raw_name) in off_names:
                spec.disabled = True
            out.append(spec)
    return out


def _name_list(value: Any) -> frozenset[str]:
    """A list of server names (strings only); anything else is empty."""
    if not isinstance(value, (list, tuple)):
        return frozenset()
    return frozenset(str(v) for v in value if isinstance(v, str) and v.strip())


def _gemini_switched_off(obj: dict[str, Any], specs: list[PackSpec]) -> None:
    """v1.354.0 (G50): Gemini CLI keeps its on/off list BESIDE the servers:
    ``"mcp": {"excluded": [...]}`` switches those names off and a non-empty
    ``"mcp": {"allowed": [...]}`` switches off every name it does not list.
    VS Code's ``"mcp": {"servers": ...}`` carries neither key, so this is a
    no-op for it. Names are compared the way the specs were named
    (:func:`safe_name`)."""
    mcp = obj.get("mcp")
    if not isinstance(mcp, dict):
        return
    excluded = {safe_name(n) for n in _name_list(mcp.get("excluded"))}
    allowed = {safe_name(n) for n in _name_list(mcp.get("allowed"))}
    if not excluded and not allowed:
        return
    for spec in specs:
        if spec.name in excluded or (allowed and spec.name not in allowed):
            spec.disabled = True


def _guess_source(obj: dict[str, Any], default: str) -> str:
    if default:
        return default
    if "projects" in obj or "mcpServers" in obj:
        return "claude-code"
    if "mcp" in obj or "servers" in obj:
        return "vscode"
    return "paste"


def _specs_from_json(obj: dict[str, Any], *, source: str, origin: str) -> tuple[list[PackSpec], bool]:
    """``(specs, recognised)`` for a parsed JSON object. ``recognised`` is
    True when the object had one of the known shapes, even if every entry
    was dropped (Iron Jarvis's own server, a broken entry)."""
    specs: list[PackSpec] = []
    recognised = False
    if isinstance(obj.get("mcpServers"), dict):
        recognised = True
        top = _specs_from_map(obj["mcpServers"], source=_guess_source(obj, source), origin=origin)
        # v1.354.0 (G50): Gemini CLI's on/off list sits beside its servers.
        _gemini_switched_off(obj, top)
        specs += top
    if isinstance(obj.get("projects"), dict):
        # ~/.claude.json: per-project servers, origin = the project path.
        for project_path, project in obj["projects"].items():
            if isinstance(project, dict) and isinstance(project.get("mcpServers"), dict):
                recognised = True
                specs += _specs_from_map(
                    project["mcpServers"],
                    source=source or "claude-code",
                    origin=str(project_path) or origin,
                    # v1.354.0 (G50): `/mcp` → disable in Claude Code writes
                    # the name into the project's `disabledMcpServers`.
                    off_names=_name_list(project.get("disabledMcpServers")),
                )
    mcp = obj.get("mcp")
    if isinstance(mcp, dict) and isinstance(mcp.get("servers"), dict):
        recognised = True
        specs += _specs_from_map(mcp["servers"], source=source or "vscode", origin=origin)
    if isinstance(obj.get("servers"), dict) and not recognised:
        recognised = True
        specs += _specs_from_map(obj["servers"], source=source or "vscode", origin=origin)
    if isinstance(obj.get("mcp_servers"), dict) and not recognised:
        recognised = True
        specs += _specs_from_map(obj["mcp_servers"], source=source or "codex", origin=origin)
    if recognised:
        return specs, True
    if _looks_like_server(obj):
        spec = _spec_from_entry(_name_for_entry(obj), obj, source=source or "paste", origin=origin)
        return ([spec] if spec is not None else []), True
    if _looks_like_server_map(obj):
        return _specs_from_map(obj, source=source or "paste", origin=origin), True
    return [], False


def _specs_from_toml(data: dict[str, Any], *, source: str, origin: str) -> tuple[list[PackSpec], bool]:
    servers = data.get("mcp_servers")
    if not isinstance(servers, dict):
        return [], False
    return _specs_from_map(servers, source=source or "codex", origin=origin), True


# -- names for a pack that carries none ------------------------------------ #
def _has_path_sep(text: str) -> bool:
    return "/" in text or "\\" in text


def _package_tail(pkg: str, *, image: bool = False) -> str:
    """``@scope/server-github@1.2.3`` → ``server-github``; with ``image``
    ``ghcr.io/x/img:tag`` → ``img`` (the ``:tag`` cut is for container
    images only — ``C:\\srv\\server.js`` keeps its drive)."""
    text = str(pkg or "").strip()
    text = _VERSION_SUFFIX_RX.sub("", text)
    text = text.rstrip("/\\").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if image and ":" in text:
        text = text.split(":", 1)[0]
    return text


def _name_from_url(url: str) -> str:
    from urllib.parse import urlsplit

    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        host = ""
    if not host:
        return ""
    labels = [p for p in host.split(".") if p]
    if len(labels) >= 2 and not labels[-1].isdigit():
        labels = labels[:-1]  # drop the TLD
    while len(labels) > 1 and labels[0] in ("www", "mcp", "api", "app"):
        labels = labels[1:]
    name = safe_name("-".join(labels))
    # A numeric host (127.0.0.1) leaves nothing letter-led: lead with a word.
    return name or safe_name("host-" + host)


def _name_from_command(command: str, args: list[str]) -> str:
    cmd = command.lower().rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    for ext in (".cmd", ".exe"):
        cmd = cmd.removesuffix(ext)
    rest = list(args)
    pkg = ""
    if cmd in ("npx", "bunx", "pnpm", "yarn", "npm"):
        skip_next = False
        for a in rest:
            if skip_next:
                skip_next = False
                continue
            if a in _NPX_VALUE_FLAGS:
                skip_next = True
                continue
            if a.startswith("-") or a in ("dlx", "exec", "x"):
                continue
            pkg = a
            break
    elif cmd in ("uvx", "pipx", "uv", "pip", "deno", "cargo", "go", "dotnet"):
        skip_next = False
        for a in rest:
            if skip_next:
                skip_next = False
                continue
            if a in ("--from", "--with", "--python", "-p", "--directory"):
                skip_next = True
                continue
            if a.startswith("-") or a in ("run", "tool", "install"):
                continue
            pkg = a
            break
    elif cmd in ("docker", "podman"):
        skip_next = False
        for a in rest[1:] if rest[:1] == ["run"] else rest:
            if skip_next:
                skip_next = False
                continue
            if a in _DOCKER_VALUE_FLAGS:
                skip_next = True
                continue
            if a.startswith("-"):
                continue
            pkg = _package_tail(a, image=True)
            break
    elif cmd in ("node", "python", "python3", "py", "ruby", "php", "java"):
        for a in rest:
            if a in ("-m",):
                continue
            if a.startswith("-"):
                continue
            pkg = a
            break
        pkg = _package_tail(pkg)
        for ext in _PROGRAM_EXTS:
            pkg = pkg.removesuffix(ext)
    else:
        pkg = cmd
        for ext in _PROGRAM_EXTS:
            pkg = pkg.removesuffix(ext)
    tail = _package_tail(pkg) if pkg else cmd
    name = safe_name(tail)
    return name or safe_name(cmd)


def _name_for_entry(entry: dict[str, Any]) -> str:
    for key in ("name", "id"):
        if isinstance(entry.get(key), str) and entry[key].strip():
            return entry[key]
    command = str(entry.get("command") or "").strip()
    args = _str_list(entry.get("args"))
    if command:
        return _name_from_command(command, args)
    for key in ("url", "httpUrl", "serverUrl"):
        if isinstance(entry.get(key), str) and entry[key].strip():
            return _name_from_url(entry[key].strip())
    return ""


# -- one line --------------------------------------------------------------- #
def _split_command_line(line: str) -> list[str]:
    """A command line into words. POSIX quoting; when the line carries a
    backslash (a Windows path) the non-POSIX splitter keeps it and the
    surrounding quotes are stripped by hand."""
    import shlex

    text = str(line or "").strip()
    if not text:
        return []
    try:
        if "\\" not in text:
            return shlex.split(text, posix=True)
        words = shlex.split(text, posix=False)
    except ValueError:
        words = text.split()
    out: list[str] = []
    for w in words:
        if len(w) >= 2 and w[0] == w[-1] and w[0] in ("'", '"'):
            w = w[1:-1]
        out.append(w)
    return out


def _header_pair(text: str) -> tuple[str, str] | None:
    if ":" not in text:
        return None
    name, value = text.split(":", 1)
    name = name.strip()
    if not name:
        return None
    return name, value.strip()


def _spec_from_remote_line(words: list[str], *, source: str, origin: str) -> PackSpec | None:
    """``npx [-y] mcp-remote <url> [--header 'X: y'] [--transport sse-only]``
    → an http spec carrying the url and the headers."""
    try:
        at = next(i for i, w in enumerate(words) if _package_tail(w).lower() == "mcp-remote")
    except StopIteration:
        return None
    rest = words[at + 1 :]
    url = ""
    headers: dict[str, str] = {}
    kind = ""
    i = 0
    while i < len(rest):
        w = rest[i]
        if w in ("--header", "-H") and i + 1 < len(rest):
            pair = _header_pair(rest[i + 1])
            if pair:
                headers[pair[0]] = pair[1]
            i += 2
            continue
        if w.startswith("--header="):
            pair = _header_pair(w.split("=", 1)[1])
            if pair:
                headers[pair[0]] = pair[1]
            i += 1
            continue
        if w == "--transport" and i + 1 < len(rest):
            kind = "sse" if "sse" in rest[i + 1].lower() else "http"
            i += 2
            continue
        if not url and _URL_RX.match(w):
            url = w
        i += 1
    if not url:
        return None
    if not kind:
        kind = "sse" if url.lower().rstrip("/").endswith("/sse") else "http"
    return PackSpec(
        name=_name_from_url(url),
        transport=kind,
        url=url,
        headers=headers,
        source=source or "paste",
        origin=origin,
    )


def _spec_from_line(line: str, *, source: str, origin: str) -> PackSpec | None:
    text = line.strip()
    if not text:
        return None
    if _URL_RX.match(text):
        kind = "sse" if text.lower().rstrip("/").endswith("/sse") else "http"
        return PackSpec(
            name=_name_from_url(text), transport=kind, url=text, source=source or "paste", origin=origin
        )
    words = _split_command_line(text)
    if not words:
        return None
    if any(_package_tail(w).lower() == "mcp-remote" for w in words):
        return _spec_from_remote_line(words, source=source, origin=origin)
    first = words[0]
    base = first.lower().rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    for ext in (".cmd", ".exe"):
        base = base.removesuffix(ext)
    is_command = (
        base in _LAUNCHERS
        or _has_path_sep(first)
        or first.lower().endswith(_PROGRAM_EXTS)
    )
    if not is_command:
        return None
    command, args = first, words[1:]
    if _is_own_server(command, args):
        return None
    return PackSpec(
        name=_name_from_command(command, args),
        transport="stdio",
        command=command,
        args=args,
        source=source or "paste",
        origin=origin,
    )


def _specs_from_lines(text: str, *, source: str, origin: str) -> tuple[list[PackSpec], bool]:
    """Every non-empty line is tried as a command line or a URL; a line that
    is neither makes the whole paste unrecognised (prose with one URL in it
    is prose)."""
    specs: list[PackSpec] = []
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines or len(lines) > 50:
        return [], False
    for ln in lines:
        spec = _spec_from_line(ln, source=source, origin=origin)
        if spec is None:
            return [], False
        specs.append(spec)
    return specs, True


def unique_names(specs: list[PackSpec]) -> list[PackSpec]:
    """Public name of :func:`_unique_names` (v1.354.0): the Claude Code
    plugins source gathers servers from many files into ONE list, which must
    not hold two packs under one name either."""
    return _unique_names(specs)


def _unique_names(specs: list[PackSpec]) -> list[PackSpec]:
    """Two specs from one paste must not share a name: the second becomes
    ``<name>-2``, the third ``<name>-3`` (within :data:`NAME_MAX`)."""
    seen: dict[str, int] = {}
    for spec in specs:
        base = spec.name
        if not base:
            continue
        if base not in seen:
            seen[base] = 1
            continue
        while True:
            seen[base] += 1
            suffix = f"-{seen[base]}"
            cand = base[: NAME_MAX - len(suffix)].rstrip("-") + suffix
            if cand not in seen:
                seen[cand] = 1
                spec.name = cand
                break
    return specs


def _parse_text(text: Any, *, source: str = "", origin: str = "paste", kind_hint: str = "") -> tuple[list[PackSpec], bool]:
    """The one ladder: JSON (comments stripped, forgiving), TOML, lines.
    ``(specs, recognised)``; never raises."""
    try:
        raw = text if isinstance(text, str) else ("" if text is None else str(text))
        raw = raw.lstrip("﻿").strip()
        if not raw:
            return [], False
        from ..core.jsonish import loads_object

        if kind_hint != "toml" and ("{" in raw):
            obj = loads_object(strip_json_comments(raw))
            if isinstance(obj, dict):
                specs, recognised = _specs_from_json(obj, source=source, origin=origin)
                if recognised:
                    return _with_asks(_unique_names(specs)), True
        if kind_hint == "toml" or "mcp_servers" in raw:
            import tomllib

            try:
                data = tomllib.loads(raw)
            except (tomllib.TOMLDecodeError, ValueError, TypeError):
                data = None
            if isinstance(data, dict):
                specs, recognised = _specs_from_toml(data, source=source, origin=origin)
                if recognised:
                    return _with_asks(_unique_names(specs)), True
        if kind_hint in ("", "paste"):
            specs, recognised = _specs_from_lines(raw, source=source, origin=origin)
            if recognised:
                return _with_asks(_unique_names(specs)), True
        return [], False
    except Exception:  # noqa: BLE001 — a parser never raises into a route
        return [], False


def parse_any(text: str) -> list[PackSpec]:
    """Turn pasted text into specs; NEVER raises; ``[]`` for nothing
    recognisable. Recognised shapes (contract 2): JSON with ``mcpServers``
    (Claude Desktop / Claude Code, incl. per-project ``projects`` flattened,
    origin = the project path), JSON with ``mcp.servers`` (VS Code), a
    single server object, TOML ``[mcp_servers.<name>]`` tables (Codex), one
    command line (or one per line), a bare URL (``/sse`` → sse), an
    ``npx mcp-remote <url> [--header X]`` line → http with the headers.
    Iron Jarvis's own server entries are dropped."""
    specs, _recognised = _parse_text(text, source="paste", origin="paste")
    return specs


#: A config file larger than this is not read (a settings.json is a few KB;
#: ~/.claude.json with a year of projects is well under a megabyte).
FILE_CAP_BYTES = 2 * 1024 * 1024


def _source_for_path(path: str) -> str:
    low = path.replace("\\", "/").lower()
    name = low.rsplit("/", 1)[-1]
    if name == "claude_desktop_config.json":
        return "claude-desktop"
    if name == "config.toml" or "/.codex/" in low:
        return "codex"
    if "/.cursor/" in low:
        return "cursor"
    if name in ("settings.json", "mcp.json") and ("/code/user/" in low or "/code - insiders/user/" in low):
        return "vscode"
    if name == "mcp.json" and "/.vscode/" in low:
        return "vscode"
    # v1.354.0 (G50): Windsurf and Gemini CLI.
    if name == "mcp_config.json" and "/.codeium/" in low:
        return "windsurf"
    if name == "settings.json" and "/.gemini/" in low:
        return "gemini-cli"
    if name in (".claude.json", ".mcp.json") or "/.claude/" in low:
        return "claude-code"
    return "paste"


def parse_file(path: Any, *, source: str = "") -> tuple[list[PackSpec], bool, str]:
    """Read ONE config file read-only → ``(specs, readable, note)``.

    A missing file (or a path that is not a file) is ``([], False, "")``; a
    file over :data:`FILE_CAP_BYTES`, one that cannot be opened, or one that
    parses as nothing recognisable is ``([], False, "<one line naming the
    file>")`` — never its contents. A readable file with no servers in it
    is ``([], True, "")``. Bytes are decoded UTF-8 with replacement; the
    dialect comes from ``source`` (or the path: ``.codex`` / ``config.toml``
    → codex, ``Claude/claude_desktop_config.json`` → claude-desktop,
    ``.cursor`` → cursor, ``Code/User/settings.json`` → vscode, else
    claude-code); ``origin`` is the path (a per-project entry in
    ``~/.claude.json`` keeps the project path). Never raises.
    """
    import os

    try:
        text_path = os.fspath(path)
    except TypeError:
        return ([], False, "")
    text_path = str(text_path or "")
    if not text_path:
        return ([], False, "")
    file_name = text_path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or text_path
    try:
        if not os.path.isfile(text_path):
            return ([], False, "")
        with open(text_path, "rb") as fh:
            data = fh.read(FILE_CAP_BYTES + 1)
    except OSError:
        return ([], False, f"{file_name} could not be opened")
    if len(data) > FILE_CAP_BYTES:
        return ([], False, f"{file_name} is larger than 2 MB and was not read")
    text = data.decode("utf-8", errors="replace")
    kind_hint = "toml" if text_path.lower().endswith(".toml") else "json"
    which = source or _source_for_path(text_path)
    specs, recognised = _parse_text(text, source=which, origin=text_path, kind_hint=kind_hint)
    if recognised:
        return (specs, True, "")
    # A JSON file that parses but holds no server block is readable and
    # empty (a fresh ~/.claude.json, a settings.json with no "mcp" key).
    try:
        from ..core.jsonish import loads_object

        if kind_hint == "json" and isinstance(loads_object(strip_json_comments(text)), dict):
            return ([], True, "")
        if kind_hint == "toml":
            import tomllib

            tomllib.loads(text.lstrip("﻿"))
            return ([], True, "")
    except Exception:  # noqa: BLE001 — malformed: say so by name
        pass
    return ([], False, f"{file_name} could not be read as a config file")
