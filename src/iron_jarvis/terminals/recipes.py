"""Launch recipes: how an EXTERNAL harness in a Build pane reaches Jarvis (D18, Q01, Q04).

Until this ship, launching a CLI was one string typed into a live shell
(``TerminalPane.tsx``), with one server-side exception (Creative Studio) that
appends a flag and sets one variable. Jarvis wrote **no** CLI configuration and
detected **no** CLI version. Both are new machinery, and D18 fixes its shape:

1. detect the installed harness version,
2. verify the integration method *that version* supports,
3. configure Jarvis MCP appropriately,
4. provide the pane-scoped token,
5. expose only the pane's enabled capabilities,
6. **surface incompatibility clearly** when the installed version cannot support
   the required integration.

Step 6 is the one that decides the shape of everything above it. This module is
written to DEGRADE, never to guess:

* :meth:`LaunchRecipe.detect` runs the CLI's own version command through the path
  :func:`iron_jarvis.terminals.ai_clis._find` already resolves, with a short
  timeout, and treats **every** failure — a missing binary, a non-zero exit, a
  timeout, an ``OSError``, a CLI that prints nothing at all — as ``""``. It
  cannot raise. An unknown version is an ordinary state, because it is the state
  a brand-new CLI release puts us in.
* An unknown version means the most conservative method the recipe can *verify*.
  Verification is reading the CLI's own ``--help`` and matching the option names
  it advertises. **No flag in this module is assumed to exist**; D18 forbids
  assuming configuration mechanisms are static, and Q04 says so again for Claude
  Code specifically. A flag name appearing here is a NEEDLE searched for in help
  output, never a string handed to a CLI that never advertised it.
* When a mechanism cannot be verified, the recipe is honest in one of two ways.
  If the *integration itself* cannot be verified, ``ok=False`` and
  :attr:`RecipeResult.limitations` says why in a sentence a user can read — the
  pane then launches exactly as it does today, with no Jarvis capabilities. If
  only an *isolation* mechanism cannot be verified, ``ok=True`` **with** a
  limitation, per Q04's explicit instruction to surface the gap rather than
  pretend isolation exists.

**Every other catalog entry keeps today's behaviour exactly**: a typed command,
no recipe, no token, no configuration written. :func:`recipe_for` returns ``None``
for them and :func:`iron_jarvis.terminals.ai_clis.detect_ai_clis` reports
``recipe: None``. A CLI with no recipe is not broken by this ship; it simply has
no Jarvis capabilities, which is what it has today.

**A pane that grants nothing gets no token.** If the pane's capabilities are all
off, :meth:`LaunchRecipe.prepare` returns ``method="none"`` and an ``env`` with no
``IRONJARVIS_MCP_TOKEN`` in it. The server would refuse every tool anyway (an
empty grant), but minting a credential into a child process that may not use it
is a credential handed out for nothing, and the five-credential rule (plan §7) is
worth obeying on the way out as well as at the door.

**No credential is written to disk, and no file of the user's is taken away.**
A configuration this module writes carries a REFERENCE to the pane token's
environment variable (:data:`CONFIG_TOKEN_REFERENCE`), never the token: the one
file Claude Code's convention says to COMMIT must not hold a credential that
drives the user's logged-in browser. And a ``.mcp.json`` that already exists is
MERGED, not replaced - a developer's own MCP servers survive a Jarvis launch -
while a file that cannot be read as an object is left strictly alone and reported
as a limitation. :func:`remove_config_writes` is the other end of that, ready for the
pane close paths that already revoke the token (``TerminalManager.kill`` /
``purge_dead`` / ``kill_all``) to call with the pane's ``config_writes``. Nothing
depends on that call for safety - what is written holds no credential - so until the
manager records a pane's writes, a leftover entry is inert configuration, not a
credential, and a relaunch rewrites it.

**Blocking.** Probing runs a subprocess and preparing writes files, so every
public entry point here is synchronous and must be called OFF the event loop —
:func:`recipe_state_async` and :func:`prepare_async` are the ``asyncio.to_thread``
wrappers, and a synchronous ``def`` FastAPI route already runs in the threadpool.
A subprocess spawn on the daemon's single loop is the v1.153.1 outage the user
experienced as "Daemon offline".
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

from ..browser.panetokens import MCP_TOKEN_ENV, MCP_URL_ENV, normalise_capabilities
from ..core.logging import get_logger
from .ai_clis import _find

logger = get_logger(__name__)

#: How long a version/help probe may take. Short on purpose: this runs while a
#: user is looking at a Launch menu, and a CLI that has not answered in this long
#: is a CLI we will describe as unknown rather than wait for.
PROBE_TIMEOUT_S = 4.0

#: How long a probe's answer is reused. Detection is host state that changes only
#: when the user installs or upgrades a CLI, and the Launch menu is opened far
#: more often than that. Bounded so an upgrade is noticed without a restart.
PROBE_TTL_S = 300.0

#: The integration methods. ``mcp_http`` is Streamable HTTP straight to
#: ``POST /mcp``; ``mcp_stdio`` is a stdio bridge (Jarvis's own shim, or the Pi
#: adapter) speaking to the same endpoint; ``none`` means nothing was configured.
METHOD_HTTP = "mcp_http"
METHOD_STDIO = "mcp_stdio"
METHOD_NONE = "none"

#: The name Jarvis's server takes in every harness configuration it writes. One
#: constant so a diagnostics panel, a config file and a test cannot disagree.
SERVER_NAME = "iron-jarvis"

#: Q04, verbatim from the plan: the sentence shown when a Claude Code build could
#: not be told to disable its own web tools. It is a constant because it is a
#: promise about what is NOT enforced, and prose that says that must not drift.
CLAUDE_WEB_TOOLS_LIMITATION = (
    "This Claude Code build could not be told to disable its own web tools, so it "
    "may reach the web without Jarvis. Browser calls through Jarvis are still "
    "logged and gated."
)

#: Q04 again, for the half the first sentence does not cover. The flag NAME is
#: feature-detected; the flag's ARGUMENT GRAMMAR and the tool names are not, and
#: cannot be from a help scrape alone. A build that takes its disallowed tools as
#: separate arguments receives one argument spelled ``WebFetch,WebSearch``,
#: matches no tool, and disables nothing - silently, with no limitation line at
#: all, which is precisely Q04's "pretending isolation exists".
CLAUDE_WEB_TOOLS_UNVERIFIED_LIMITATION = (
    "Iron Jarvis could not verify how this Claude Code build wants its disallowed "
    "tools written, so the request to disable its own web tools may not take "
    "effect and it may reach the web without Jarvis. Browser calls through Jarvis "
    "are still logged and gated."
)

#: What goes in the ``Authorization`` header of a written config file: a
#: REFERENCE to the environment variable the pane's shell already carries, never
#: the token itself. ``.mcp.json`` is the file Claude Code's own convention says
#: to commit and share, so a literal there is a credential that drives the user's
#: logged-in browser published in their next ``git push`` - and it outlives the
#: pane that owns it, which is the one property ``browser/panetokens.py`` exists
#: to guarantee. The reference is inert in a commit: it resolves only inside a
#: shell Iron Jarvis started for that pane. If a build does not expand it, the
#: failure is a visible 401 naming the token, not a silent leak - which is the
#: right way round for a credential.
CONFIG_TOKEN_REFERENCE = "${" + MCP_TOKEN_ENV + "}"


# --------------------------------------------------------------------------- #
# RecipeResult
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RecipeResult:
    """What a recipe prepared, and what it could not.

    ``ok=False`` NEVER carries an empty :attr:`limitations` — a refusal the user
    cannot read is indistinguishable from a bug, and this is the one field the
    Launch menu renders when the answer is "no". :meth:`__post_init__` enforces
    that rather than leaving it to every construction site.
    """

    ok: bool
    method: str = METHOD_NONE
    version: str = ""
    env: dict[str, str] = field(default_factory=dict)
    config_writes: list[str] = field(default_factory=list)
    argv_extra: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.ok and not self.limitations:
            raise ValueError(
                "a failed RecipeResult must carry at least one limitation sentence"
            )

    def row(self) -> dict[str, Any]:
        """The shape the Launch menu reads (plan section 13.1)."""
        return {
            "method": self.method,
            "ok": self.ok,
            "limitations": list(self.limitations),
            "detail": self.detail,
        }


@runtime_checkable
class LaunchRecipe(Protocol):
    """The protocol every recipe implements (plan section 13.1)."""

    cli_id: str

    def detect(self) -> str:
        """The installed version, verbatim, or ``""`` when unknown. Never raises."""

    def supports(self, version: str) -> str:
        """The method this version can be VERIFIED to support, or ``""``."""

    def inspect(self, version: str | None = None) -> RecipeResult:
        """A dry run: what would happen, writing nothing and minting nothing."""

    def prepare(
        self,
        pane_id: str,
        token: str,
        url: str,
        capabilities: Mapping[str, Any] | None = None,
    ) -> RecipeResult:
        """Configure this pane's cwd and return the env/argv the spawn needs."""


# --------------------------------------------------------------------------- #
# Probing — the one place this module runs somebody else's binary
# --------------------------------------------------------------------------- #
def _run_probe(argv: list[str], *, timeout: float = PROBE_TIMEOUT_S) -> str:
    """Run ``argv`` and return its combined output, or ``""`` on ANY failure.

    THE ONE SUBPROCESS CHOKEPOINT IN THIS MODULE, deliberately: the test suite
    stubs exactly this function (``tests/conftest.py``) so no test ever runs the
    developer's real ``claude``/``codex``/``pi``, which would make a CI runner
    without those binaries behave differently from this machine — the trap that
    ``GROK_HOME``, the subscription-CLI stub and the OpenCode store are already
    isolated for in that same file.

    stderr is folded into the answer because CLIs disagree about which stream a
    ``--version`` belongs on, and a probe that read only stdout would report
    "unknown" for the ones that chose stderr. A non-zero exit is NOT treated as a
    failure for the same reason: ``--help`` exits non-zero on several CLIs while
    printing the help we came for. Only "no output at all" is unknown.
    """
    if not argv:
        return ""
    try:
        proc = subprocess.run(  # noqa: S603 - argv[0] is a path we resolved
            argv,
            capture_output=True,
            timeout=timeout,
            shell=False,
            stdin=subprocess.DEVNULL,
        )
    except Exception:  # missing binary, timeout, OSError, permission — all unknown
        logger.debug("launch-recipe probe failed: %s", argv[:1], exc_info=True)
        return ""
    out = b"".join(x for x in (proc.stdout, proc.stderr) if x)
    return out.decode("utf-8", "replace")


#: The real implementation under a second name. ``conftest.py`` replaces
#: :func:`_run_probe` for the whole session so no test runs the developer's real
#: CLIs — which would otherwise leave no way to exercise the chokepoint's own
#: failure handling. This alias is that way. Nothing in this module calls it.
_run_probe_real = _run_probe

_probe_lock = threading.Lock()
_probe_cache: dict[tuple[str, str], tuple[float, str]] = {}


def clear_probe_cache() -> None:
    """Forget every cached probe. Called by tests, and safe to call at any time."""
    with _probe_lock:
        _probe_cache.clear()


def _probe(command: str, flag: str) -> str:
    """``<resolved command> <flag>`` output, memoised for :data:`PROBE_TTL_S`.

    Cached on the RESOLVED PATH, not on the command name: an upgrade that moves
    the binary is a different key and re-probes at once, and a CLI that is not
    installed is never probed at all.
    """
    try:
        path = _find(command)
    except Exception:  # a host with no resolvable home is "not installed", not a 500
        logger.debug("could not resolve %s", command, exc_info=True)
        return ""
    if not path:
        return ""
    key = (path, flag)
    now = time.monotonic()
    with _probe_lock:
        hit = _probe_cache.get(key)
        if hit is not None and hit[0] > now:
            return hit[1]
    text = _run_probe([path, flag])
    with _probe_lock:
        _probe_cache[key] = (now + PROBE_TTL_S, text)
    return text


def _first_version_line(text: str) -> str:
    """The version, verbatim: the first non-empty line, bounded.

    Not parsed into numbers. Nothing in this module compares versions — every
    decision is made by feature-detecting the CLI's own help — so a parser here
    would be a second source of truth that could only ever be wrong about a
    versioning scheme it had not seen.
    """
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line:
            return line[:200]
    return ""


#: Characters that may NOT sit either side of a needle. An option name is a word,
#: and a plain substring test cannot tell ``--mcp`` from ``--mcp-debug`` or
#: ``--no-mcp`` — which is how a build that merely MENTIONS a flag was read as
#: advertising it (v1.238.0 review). ``-`` is in the class deliberately: it is the
#: character that extends one option name into a different one.
_NEEDLE_EDGE = r"[0-9A-Za-z_-]"


def _advertises(help_text: str, *needles: str) -> str:
    """The first needle the CLI's own help advertises, or ``""``.

    This is the whole of "feature detection" in this module: a flag is used only
    if the installed build named it in its own help output. Matching is
    case-insensitive because CLIs disagree about ``--disallowedTools`` versus
    ``--disallowed-tools``, and the ANSWER is the needle as spelled here, so a
    caller gets back the exact spelling it asked about.

    Matching is at a WORD BOUNDARY, not a bare substring. ``--mcp`` is a
    substring of ``--mcp-debug``, of ``--no-mcp`` and of the sentence "this build
    has no --mcp support"; treating any of those as "this build speaks MCP"
    produced a green "Jarvis capabilities over HTTP" headline on a build that
    could not reach Jarvis at all. A needle now matches only where the character
    before it and the character after it are not part of the same word.
    """
    hay = (help_text or "").lower()
    for needle in needles:
        if not needle:
            continue
        pattern = (
            f"(?<!{_NEEDLE_EDGE})" + re.escape(needle.lower()) + f"(?!{_NEEDLE_EDGE})"
        )
        if re.search(pattern, hay):
            return needle
    return ""


def _usage_line(help_text: str, needle: str) -> str:
    """The first help LINE that advertises ``needle`` (boundary-matched), or ``""``.

    The line an option is documented on is the only thing a ``--help`` scrape can
    read about the option's ARGUMENT — its metavar and its prose. Q04 asks for the
    argument form to be verified, not just the flag name.
    """
    pattern = (
        f"(?<!{_NEEDLE_EDGE})" + re.escape((needle or "").lower()) + f"(?!{_NEEDLE_EDGE})"
    )
    if not needle:
        return ""
    for raw in (help_text or "").splitlines():
        if re.search(pattern, raw.lower()):
            return raw.strip()
    return ""


# --------------------------------------------------------------------------- #
# Writing configuration — confined to the pane's own working directory
# --------------------------------------------------------------------------- #
class ConfigWriteRefused(Exception):
    """A recipe was asked to write outside the pane's working directory."""


def _confined(cwd: str, *parts: str) -> Path:
    """``cwd/parts...`` resolved, refusing anything that escapes ``cwd``.

    A recipe writes into the folder the user opened the pane on. A ``..`` or an
    absolute component in a name would put a Jarvis-authored config file
    somewhere the user never looked, and the diagnostics panel would then report
    a path that is true and still surprising. Refuse instead.
    """
    base = Path(cwd).resolve()
    target = (base / Path(*parts)).resolve()
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise ConfigWriteRefused(
            f"a launch recipe may only write inside the pane's folder ({base})"
        ) from exc
    return target


def _write_config(path: Path, text: str) -> str:
    """Write ``text`` to ``path`` and return the absolute path, as a string.

    The file may hold a pane token, so it is narrowed to 0600 where the platform
    has POSIX modes. On Windows ``chmod`` is close to a no-op and this is said
    plainly rather than claimed: the file lives in the user's own project folder
    under the user's own account, and the token it holds dies with the pane and
    with the process.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except Exception:  # pragma: no cover - platform dependent, never fatal
        pass
    return str(path)


def _merge_mcp_json(path: Path, entry: dict[str, Any]) -> tuple[str, bool]:
    """Put Jarvis's one server into ``path``, KEEPING whatever is already there.

    ``.mcp.json`` is the standard project-scoped Claude Code config, so a
    developer who has one has their own servers in it. Writing over it took those
    servers away with no message anywhere (v1.238.0 review) - the same class of
    dishonesty this module exists to prevent, applied to a file instead of to a
    sentence.

    Returns ``(path, merged)``, where ``merged`` is True when the file already
    held something of the user's own. Raises :class:`ConfigWriteRefused` when the
    existing file cannot be read as an object: a file we cannot parse is a file we
    must not overwrite, because we cannot know what is in it.
    """
    data: dict[str, Any] = {}
    merged = False
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8") or "{}")
        except Exception as exc:
            raise ConfigWriteRefused(
                f"{path} exists and could not be read as JSON, so Iron Jarvis left "
                "it alone rather than overwrite it"
            ) from exc
        if not isinstance(existing, dict):
            raise ConfigWriteRefused(
                f"{path} exists and is not a JSON object, so Iron Jarvis left it "
                "alone rather than overwrite it"
            )
        data = existing
        servers = data.get("mcpServers")
        if servers is not None and not isinstance(servers, dict):
            raise ConfigWriteRefused(
                f"{path} has an mcpServers key that is not an object, so Iron "
                "Jarvis left it alone rather than overwrite it"
            )
        merged = bool(
            [k for k in data if k != "mcpServers"]
            or [k for k in (servers or {}) if k != SERVER_NAME]
        )
    servers = dict(data.get("mcpServers") or {})
    servers[SERVER_NAME] = entry
    data["mcpServers"] = servers
    return _write_config(path, json.dumps(data, indent=2) + "\n"), merged


def remove_config_writes(paths: Any) -> list[str]:
    """Undo what a recipe wrote. For the chokepoints that revoke the pane token.

    A pane's configuration should not outlive the pane: the token it points at is
    revoked the moment the pane dies (``TerminalManager.kill`` / ``purge_dead`` /
    ``kill_all``), and a config left behind points a harness at a credential that
    no longer resolves. This is the file half of that revocation - and it is a
    tidiness measure, NOT a security one, because what gets written carries a
    reference to an environment variable rather than a credential. Say it that way
    round in any doc: the safety is in what is written, not in what is removed.

    It is deliberately narrow:

    * a ``.mcp.json`` loses **only** Jarvis's own server entry, and the file is
      deleted only when nothing else was ever in it - a user's own servers
      survive the pane exactly as they survived its launch;
    * any other file this module writes (the Codex config, the Pi adapter) is
      Jarvis's own, so it is removed outright.

    Never raises: a close path that could fail on a read-only folder would cost
    the user the pane. Returns the paths actually removed or rewritten.
    """
    done: list[str] = []
    for raw in list(paths or []):
        path = Path(str(raw))
        try:
            if not path.exists():
                continue
            if path.name == ClaudeCodeRecipe.config_name:
                data = json.loads(path.read_text(encoding="utf-8") or "{}")
                if not isinstance(data, dict):
                    continue
                servers = data.get("mcpServers")
                if isinstance(servers, dict):
                    servers.pop(SERVER_NAME, None)
                    data["mcpServers"] = servers
                others = [k for k in data if k != "mcpServers"]
                if not others and not data.get("mcpServers"):
                    path.unlink()
                else:
                    path.write_text(
                        json.dumps(data, indent=2) + "\n", encoding="utf-8"
                    )
                done.append(str(path))
                continue
            parent = path.parent
            path.unlink()
            done.append(str(path))
            if parent.name == CodexRecipe.home_dir and not any(parent.iterdir()):
                parent.rmdir()
        except Exception:  # noqa: BLE001 - a close path never raises
            logger.warning(
                "could not remove launch-recipe config %s", raw, exc_info=True
            )
    return done


def _no_folder(version: str, env: dict[str, str]) -> RecipeResult:
    """The pane has no folder, so no configuration could be written - say so.

    Returning the configuration flags for a file that was never written is how a
    harness launches straight into a config error, with the Launch menu having
    said nothing at all about it.
    """
    return RecipeResult(
        ok=True,
        method=METHOD_NONE,
        version=version,
        env=dict(env),
        limitations=[
            "This pane has no working folder, so Iron Jarvis could not write the "
            "configuration that points this CLI at Jarvis. The pane launches as it "
            "does today, without Jarvis capabilities."
        ],
        detail="no pane folder",
    )


def _refused_write(version: str, env: dict[str, str], why: str) -> RecipeResult:
    """A configuration file already there that we would not overwrite."""
    return RecipeResult(
        ok=True,
        method=METHOD_NONE,
        version=version,
        env=dict(env),
        limitations=[
            why
            + ". The pane launches as it does today, without Jarvis capabilities."
        ],
        detail="existing configuration left alone",
    )


def _no_capabilities(cli_id: str, version: str) -> RecipeResult:
    """A pane that grants nothing is configured with nothing, and told so."""
    return RecipeResult(
        ok=True,
        method=METHOD_NONE,
        version=version,
        limitations=[
            "This pane has no capabilities enabled, so no Iron Jarvis MCP server "
            "was configured for it. Tick a capability in the pane's Capabilities "
            "list and relaunch."
        ],
        detail="no pane capabilities",
    )


# --------------------------------------------------------------------------- #
# Claude Code (Q04)
# --------------------------------------------------------------------------- #
class ClaudeCodeRecipe:
    """Claude Code: a project-scoped ``.mcp.json`` plus whatever isolation the
    installed build advertises.

    Three feature detections, all against the build's own ``--help``:

    * a config-file option — the recipe points the CLI at the file it wrote.
      Without it, nothing has been verified, so the recipe refuses rather than
      claim an integration it cannot demonstrate.
    * a strict-config option — stops the pane's server merging with the user's
      global ones. Absent, that is a limitation, not a failure.
    * a disallowed-tools option — Q04's overlapping web path. Absent, that is
      :data:`CLAUDE_WEB_TOOLS_LIMITATION`, and the pane still works.
    """

    cli_id = "claude"
    command = "claude"
    config_name = ".mcp.json"

    #: Needles, in preference order. NOT flags passed unconditionally: one is
    #: used only when the installed build's help advertised that exact spelling.
    #: Both spellings of the tools option are searched because the CLI has
    #: shipped both, and the one that comes back is the one that was found.
    config_flags = ("--mcp-config",)
    strict_flags = ("--strict-mcp-config",)
    disallow_flags = ("--disallowed-tools", "--disallowedTools")

    #: Claude Code's own web-reaching tools, named so the disallow flag has an
    #: argument. Only ever passed when the flag itself was advertised.
    web_tools = ("WebFetch", "WebSearch")

    def detect(self) -> str:
        return _first_version_line(_probe(self.command, "--version"))

    def _help(self) -> str:
        return _probe(self.command, "--help")

    def supports(self, version: str) -> str:
        # `version` is accepted and deliberately not compared: the decision is
        # made from what this build ADVERTISES, which is the same question one
        # version further on. It rides the signature so a recipe that genuinely
        # needs it later changes here and at no call site.
        return METHOD_HTTP if _advertises(self._help(), *self.config_flags) else ""

    def _plan(self, version: str) -> tuple[str, list[str], list[str], str]:
        """``(method, argv_extra, limitations, detail)`` — shared by inspect+prepare."""
        help_text = self._help()
        config_flag = _advertises(help_text, *self.config_flags)
        limitations: list[str] = []
        if not config_flag:
            if not help_text:
                limitations.append(
                    "Iron Jarvis could not read this Claude Code build's help "
                    "output, so it could not verify how to point it at Jarvis. "
                    "The pane launches as it does today, without Jarvis "
                    "capabilities."
                )
            else:
                limitations.append(
                    "This Claude Code build advertises no option for loading an "
                    "MCP configuration file, so Iron Jarvis could not verify how "
                    "to connect it. The pane launches as it does today, without "
                    "Jarvis capabilities."
                )
            return METHOD_NONE, [], limitations, "no verified configuration option"

        argv_extra = [config_flag, self.config_name]
        strict_flag = _advertises(help_text, *self.strict_flags)
        if strict_flag:
            argv_extra.append(strict_flag)
        else:
            limitations.append(
                "This Claude Code build could not be told to ignore your global "
                "MCP servers, so it may also load servers configured outside "
                "this pane."
            )
        disallow_flag = _advertises(help_text, *self.disallow_flags)
        if not disallow_flag:
            limitations.append(CLAUDE_WEB_TOOLS_LIMITATION)
        else:
            # The flag NAME was advertised. Its ARGUMENT FORM was not, unless the
            # build's own help line says so: Claude Code has shipped builds that
            # take disallowed tools as separate space-separated arguments, and
            # such a build receives one argument literally spelled
            # "WebFetch,WebSearch", matches no tool, and disables nothing. Pass
            # the best-effort value either way - it is inert on a build that does
            # not understand it - but do not report silence when the isolation is
            # unverified.
            argv_extra += [disallow_flag, ",".join(self.web_tools)]
            if not self._comma_grammar_advertised(help_text, disallow_flag):
                limitations.append(CLAUDE_WEB_TOOLS_UNVERIFIED_LIMITATION)
        return METHOD_HTTP, argv_extra, limitations, f"configured via {config_flag}"

    def _comma_grammar_advertised(self, help_text: str, flag: str) -> bool:
        """Does this build's OWN help line say the option takes a comma list?

        Read off the line the option is documented on, which is all a help scrape
        can honestly know about an argument: either the prose says "comma", or the
        metavar itself carries a comma (``<tool1,tool2>``). Anything else - a bare
        ``<names>``, a repeated-value form, no metavar at all - is unverified, and
        unverified is said out loud rather than assumed.
        """
        line = _usage_line(help_text, flag).lower()
        if not line:
            return False
        if "comma" in line:
            return True
        return any("," in m for m in re.findall(r"<[^>]*>|\[[^\]]*\]", line))

    def inspect(self, version: str | None = None) -> RecipeResult:
        ver = self.detect() if version is None else version
        method, argv_extra, limitations, detail = self._plan(ver)
        return RecipeResult(
            ok=method != METHOD_NONE,
            method=method,
            version=ver,
            argv_extra=list(argv_extra),
            limitations=list(limitations),
            detail=detail,
        )

    def server_entry(self, url: str) -> dict[str, Any]:
        """Jarvis's own server, as this file records it.

        THE TOKEN IS NOT IN IT. The header carries
        :data:`CONFIG_TOKEN_REFERENCE` - a reference to the environment variable
        the pane's shell already carries - because ``.mcp.json`` is the file this
        CLI's own convention says to commit, and a literal bearer there is a
        credential that drives the user's real, logged-in browser, published by
        their next ``git push`` and alive until the daemon restarts. It also
        outlives the pane, which is the one thing
        :mod:`iron_jarvis.browser.panetokens` is written to prevent. The Codex
        recipe already refuses to write its token; so does this one now.
        """
        return {
            "type": "http",
            "url": url,
            "headers": {"Authorization": f"Bearer {CONFIG_TOKEN_REFERENCE}"},
        }

    def config_text(self, url: str) -> str:
        """The ``.mcp.json`` body for a folder that has none: one server, ours."""
        return json.dumps({"mcpServers": {SERVER_NAME: self.server_entry(url)}}, indent=2)

    def prepare(
        self,
        pane_id: str,
        token: str,
        url: str,
        capabilities: Mapping[str, Any] | None = None,
        *,
        cwd: str = "",
    ) -> RecipeResult:
        enabled = normalise_capabilities(capabilities)
        if not enabled:
            return _no_capabilities(self.cli_id, self.detect())
        base = self.inspect()
        if not base.ok:
            return base
        env = {MCP_URL_ENV: url, MCP_TOKEN_ENV: token}
        if not cwd:
            # argv_extra points at a file in the pane's folder. With no folder
            # there is no file, and handing back `--mcp-config .mcp.json` anyway
            # launches the CLI straight into a config error.
            return _no_folder(base.version, env)
        writes: list[str] = []
        limitations = list(base.limitations)
        path = _confined(cwd, self.config_name)
        try:
            written, merged = _merge_mcp_json(path, self.server_entry(url))
        except ConfigWriteRefused as exc:
            return _refused_write(base.version, env, str(exc))
        writes.append(written)
        if merged:
            limitations.append(
                f"This folder already had a {self.config_name}, so Iron Jarvis "
                "added its own server to it and left everything else in place. "
                "Delete the iron-jarvis entry whenever you like - a launch writes "
                "it again, and it holds no credential."
            )
        return RecipeResult(
            ok=True,
            method=base.method,
            version=base.version,
            env=env,
            config_writes=writes,
            argv_extra=list(base.argv_extra),
            limitations=limitations,
            detail=base.detail,
        )


# --------------------------------------------------------------------------- #
# Codex
# --------------------------------------------------------------------------- #
class CodexRecipe:
    """Codex: a pane-scoped TOML config the CLI is pointed at by env var.

    Codex reads a TOML configuration out of its own home directory, so the
    pane-scoped form is a directory of our own with a ``config.toml`` in it and
    ``CODEX_HOME`` pointing at it. That variable is FEATURE-DETECTED the only way
    an environment variable can be: the installed build's own help must name it.

    A Codex MCP server is launched as a child process, so the method is
    :data:`METHOD_STDIO` through Jarvis's own shim — and the shim's presence is
    CHECKED rather than assumed. A recipe that configured Codex to spawn a module
    this build does not carry would produce a harness that starts, connects to
    nothing, and reports success.
    """

    cli_id = "codex"
    command = "codex"
    home_dir = ".jarvis-codex"
    config_name = "config.toml"
    home_env = "CODEX_HOME"
    shim_module = "iron_jarvis.mcpserver.stdio_shim"

    def detect(self) -> str:
        return _first_version_line(_probe(self.command, "--version"))

    def _help(self) -> str:
        return _probe(self.command, "--help")

    def _shim_available(self) -> bool:
        """Is Jarvis's stdio shim importable in THIS build?"""
        import importlib.util

        try:
            return importlib.util.find_spec(self.shim_module) is not None
        except Exception:
            return False

    def supports(self, version: str) -> str:
        if not _advertises(self._help(), self.home_env):
            return ""
        return METHOD_STDIO if self._shim_available() else ""

    def _plan(self, version: str) -> tuple[str, list[str], str]:
        help_text = self._help()
        if not _advertises(help_text, self.home_env):
            why = (
                "Iron Jarvis could not read this Codex build's help output, so it "
                "could not verify how to give it a pane-scoped configuration."
                if not help_text
                else f"This Codex build does not advertise {self.home_env}, so "
                "Iron Jarvis could not verify how to give it a pane-scoped "
                "configuration."
            )
            return (
                METHOD_NONE,
                [
                    why
                    + " The pane launches as it does today, without Jarvis "
                    "capabilities."
                ],
                f"no verified {self.home_env}",
            )
        if not self._shim_available():
            return (
                METHOD_NONE,
                [
                    "Iron Jarvis's stdio bridge is missing from this build, so it "
                    "could not configure Codex to reach Jarvis. The pane launches "
                    "as it does today, without Jarvis capabilities."
                ],
                "stdio bridge unavailable",
            )
        return METHOD_STDIO, [], f"configured via {self.home_env}"

    def inspect(self, version: str | None = None) -> RecipeResult:
        ver = self.detect() if version is None else version
        method, limitations, detail = self._plan(ver)
        return RecipeResult(
            ok=method != METHOD_NONE,
            method=method,
            version=ver,
            limitations=list(limitations),
            detail=detail,
        )

    def config_text(self, url: str, token: str) -> str:
        """``[mcp_servers."iron-jarvis"]`` spawning Jarvis's own stdio bridge.

        THE TOKEN IS NOT WRITTEN INTO THE FILE: the pane's child environment
        already carries it, the shim reads it from there, and a child of that
        shell inherits it. One copy of a credential is easier to reason about
        than two, and the copy in the environment dies with the process.
        """
        import sys

        exe = json.dumps(sys.executable)
        args = json.dumps(["-m", self.shim_module])
        return (
            "# Written by Iron Jarvis for this Build pane. Safe to delete.\n"
            f"[mcp_servers.{json.dumps(SERVER_NAME)}]\n"
            f"command = {exe}\n"
            f"args = {args}\n"
            f"env = {{ {MCP_URL_ENV} = {json.dumps(url)} }}\n"
        )

    def prepare(
        self,
        pane_id: str,
        token: str,
        url: str,
        capabilities: Mapping[str, Any] | None = None,
        *,
        cwd: str = "",
    ) -> RecipeResult:
        enabled = normalise_capabilities(capabilities)
        if not enabled:
            return _no_capabilities(self.cli_id, self.detect())
        base = self.inspect()
        if not base.ok:
            return base
        writes: list[str] = []
        env = {MCP_URL_ENV: url, MCP_TOKEN_ENV: token}
        if not cwd:
            # Without a folder there is no CODEX_HOME to point at, so the CLI
            # would launch with Jarvis's variables and none of its configuration.
            return _no_folder(base.version, env)
        home = _confined(cwd, self.home_dir)
        path = _confined(cwd, self.home_dir, self.config_name)
        writes.append(_write_config(path, self.config_text(url, token)))
        env[self.home_env] = str(home)
        return RecipeResult(
            ok=True,
            method=base.method,
            version=base.version,
            env=env,
            config_writes=writes,
            limitations=list(base.limitations),
            detail=base.detail,
        )


# --------------------------------------------------------------------------- #
# Pi (Q01)
# --------------------------------------------------------------------------- #
class PiRecipe:
    """Pi: a Jarvis-OWNED adapter, and no dependency on a third-party package.

    Q01 is explicit that Pi core is not assumed to consume MCP and that the
    Browser capability must not depend on a third-party Pi MCP package. So the
    recipe places :mod:`iron_jarvis.terminals.pi_adapter`'s script in the pane's
    working directory, names it in ``config_writes``, and reports honestly that
    Pi does not load it by itself — a limitation the user can act on, rather than
    a claim of an integration nobody verified.

    If a later Pi advertises native MCP in its own help, :meth:`supports` returns
    :data:`METHOD_HTTP` and the adapter is skipped entirely — which is the second
    half of Q01: the canonical architecture must not depend on the adapter.

    Pi's runtime is resolved exactly the way ``ai_clis`` already resolves Pi
    itself: through ``_find``, whose Windows fallbacks include
    ``%LOCALAPPDATA%/pi-node/current`` — the bundled Node that a GUI-launched
    daemon's inherited PATH never sees. That fallback exists *because* of Pi, and
    reusing it is the least invasive attachment point rather than a second
    discovery path.
    """

    cli_id = "pi"
    command = "pi"

    #: Needles for a Pi that speaks MCP ITSELF. Each is a whole option name, and
    #: :func:`_advertises` matches at a word boundary: the bare ``--mcp`` that
    #: used to sit here is a substring of ``--mcp-debug``, ``--mcp-log-level``,
    #: ``--no-mcp`` and of the sentence "this build has no --mcp support", so a
    #: build that merely MENTIONED MCP was declared to speak it - ok=True,
    #: mcp_http, zero limitations, no adapter placed, and a green "Jarvis
    #: capabilities over HTTP" on a Pi that cannot reach Jarvis at all.
    native_needles = ("--mcp-config", "--mcp-server")

    def detect(self) -> str:
        return _first_version_line(_probe(self.command, "--version"))

    def _help(self) -> str:
        return _probe(self.command, "--help")

    def runtime(self) -> str:
        """The Node executable Pi's adapter would run under, or ``""``."""
        from .pi_adapter import pi_runtime

        return pi_runtime()

    def supports(self, version: str) -> str:
        if _advertises(self._help(), *self.native_needles):
            return METHOD_HTTP
        return METHOD_STDIO if self.runtime() else ""

    def _plan(self, version: str) -> tuple[str, list[str], str]:
        if _advertises(self._help(), *self.native_needles):
            return METHOD_HTTP, [], "native MCP advertised by this Pi build"
        runtime = self.runtime()
        if not runtime:
            return (
                METHOD_NONE,
                [
                    "Iron Jarvis could not find the Node runtime Pi ships with, so "
                    "it could not place its Pi adapter. The pane launches as it "
                    "does today, without Jarvis capabilities."
                ],
                "no Pi runtime resolved",
            )
        return METHOD_STDIO, [], f"Jarvis-owned Pi adapter under {runtime}"

    def inspect(self, version: str | None = None) -> RecipeResult:
        ver = self.detect() if version is None else version
        method, limitations, detail = self._plan(ver)
        return RecipeResult(
            ok=method != METHOD_NONE,
            method=method,
            version=ver,
            limitations=list(limitations),
            detail=detail,
        )

    def prepare(
        self,
        pane_id: str,
        token: str,
        url: str,
        capabilities: Mapping[str, Any] | None = None,
        *,
        cwd: str = "",
    ) -> RecipeResult:
        from .pi_adapter import ADAPTER_FILENAME, adapter_source

        enabled = normalise_capabilities(capabilities)
        if not enabled:
            return _no_capabilities(self.cli_id, self.detect())
        base = self.inspect()
        if not base.ok:
            return base
        env = {MCP_URL_ENV: url, MCP_TOKEN_ENV: token}
        if base.method == METHOD_HTTP:  # a Pi that speaks MCP itself — no adapter
            return RecipeResult(
                ok=True,
                method=base.method,
                version=base.version,
                env=env,
                limitations=list(base.limitations),
                detail=base.detail,
            )
        if not cwd:
            return _no_folder(base.version, env)
        writes: list[str] = []
        limitations = list(base.limitations)
        path = _confined(cwd, ADAPTER_FILENAME)
        writes.append(_write_config(path, adapter_source()))
        limitations.append(
            "Pi does not load the Iron Jarvis adapter on its own: run "
            f"`{self.runtime()} {ADAPTER_FILENAME}` in this pane to give Pi "
            "the Jarvis tools."
        )
        return RecipeResult(
            ok=True,
            method=base.method,
            version=base.version,
            env=env,
            config_writes=writes,
            limitations=limitations,
            detail=base.detail,
        )


# --------------------------------------------------------------------------- #
# The registry
# --------------------------------------------------------------------------- #
#: Every CLI that HAS a recipe. Everything else in the catalog keeps today's
#: behaviour exactly: a typed command, no recipe, no token, nothing written.
RECIPES: dict[str, Any] = {
    ClaudeCodeRecipe.cli_id: ClaudeCodeRecipe(),
    CodexRecipe.cli_id: CodexRecipe(),
    PiRecipe.cli_id: PiRecipe(),
}


def recipe_for(cli_id: str) -> Any | None:
    """The recipe for ``cli_id``, or ``None`` — which is most of the catalog."""
    return RECIPES.get((cli_id or "").strip())


def recipe_state(cli_id: str) -> dict[str, Any] | None:
    """``{method, ok, limitations, detail}`` for the Launch menu, or ``None``.

    BLOCKING (it probes). ``None`` means "this CLI has no recipe", which the menu
    renders as today's behaviour rather than as a failure.
    """
    recipe = recipe_for(cli_id)
    if recipe is None:
        return None
    return recipe.inspect().row()


async def recipe_state_async(cli_id: str) -> dict[str, Any] | None:
    """:func:`recipe_state` off the event loop."""
    return await asyncio.to_thread(recipe_state, cli_id)


async def prepare_async(
    cli_id: str,
    pane_id: str,
    token: str,
    url: str,
    capabilities: Mapping[str, Any] | None = None,
    *,
    cwd: str = "",
) -> RecipeResult | None:
    """:meth:`LaunchRecipe.prepare` off the event loop; ``None`` with no recipe."""
    recipe = recipe_for(cli_id)
    if recipe is None:
        return None
    return await asyncio.to_thread(
        lambda: recipe.prepare(pane_id, token, url, capabilities, cwd=cwd)
    )
