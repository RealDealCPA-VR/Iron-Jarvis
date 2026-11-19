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

**No credential is written to disk, and nothing is written in the user's
folder** (fourth plan, decision D9 option B, v1.539.0). A file this module
writes carries a REFERENCE to the pane token's environment variable
(:data:`CONFIG_TOKEN_REFERENCE`), never the token, and it lives in Iron
Jarvis's OWN folder (:func:`terminal_files_dir`, ``<home>/terminals-mcp/<terminal
id>.json|.js``), handed to the tool by its own per-run option (Claude Code's
``--mcp-config <abs path>``, Pi's ``-e <abs path>``; Codex needs no file at all,
only ``-c``). Until v1.538 Claude's entry was merged into the project's
``.mcp.json`` and Codex got ``CODEX_HOME=<project>/.jarvis-codex`` — the second
started Codex signed out of the user's account. :func:`leftovers_in` names what
those versions left; nothing here deletes or edits a file of the user's.
:func:`remove_terminal_files` takes a terminal's own files away when it closes,
and :func:`sweep_terminal_files` those of terminals that died with the daemon.
**A recipe never sets an account's home variable** (``CLAUDE_CONFIG_DIR``,
``CODEX_HOME``, ``GROK_HOME``): the terminal's account decides those, and
``TerminalManager._prepare_recipe`` drops one a preparer answers anyway.

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
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

from ..browser.panetokens import MCP_TOKEN_ENV, MCP_URL_ENV, normalise_capabilities
from ..core import procs as _procs
from ..core.logging import get_logger
from ..hosted.jarvis_tools import SERVER_NAME as _CODE_SERVER_NAME
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

#: The name Jarvis's server takes in a Claude Code terminal's own file. One
#: constant so a diagnostics panel, a config file and a test cannot disagree.
#: Fourth plan MS4a: NOT ``iron-jarvis``, the connected app's name
#: (``mcpserver/apps.SERVER_NAME``) in the user's own Claude Code settings —
#: the terminal keeps ``--strict-mcp-config`` and a name of its own.
SERVER_NAME = "iron-jarvis-terminal"

#: Fourth plan TM1: the name Codex's per-run ``-c mcp_servers.<name>.…`` options
#: (and Pi's extension) use — Build › Code's (``hosted/jarvis_tools``), so one
#: name means "this process's Iron Jarvis tools" on both coding surfaces. Never
#: the connected app's ``iron-jarvis``: a with-tools Codex loads the user's own
#: ``config.toml``, where the app's stdio table lives, and Codex MERGES ``-c``
#: into a table of the same name.
CODEX_SERVER_NAME = _CODE_SERVER_NAME

#: The name an older Iron Jarvis (v1.238 to v1.538) gave its entry in a
#: project's ``.mcp.json`` (:func:`leftovers_in`).
OLD_SERVER_NAME = "iron-jarvis"

#: Fourth plan D9 option B: a terminal's own files, under Iron Jarvis's home.
TERMINAL_FILES_DIR = "terminals-mcp"
#: What an older Iron Jarvis wrote in a project folder (named, never removed).
OLD_CODEX_HOME_DIR = ".jarvis-codex"
OLD_PROJECT_FILE = ".mcp.json"
#: The suffixes of a terminal's own files (Claude Code's, Pi's extension, Pi's
#: adapter for a Pi without ``-e``).
_TERMINAL_FILE_SUFFIXES = (".json", ".js", ".mjs")
#: A terminal id that can name a file (every id the manager mints matches).
_FILE_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")

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

#: Fourth plan TM1: a with-tools Codex now runs on its account's own home, so
#: it loads the user's own Codex settings too — said on the launch row, as
#: Claude Code's row says what ``--strict-mcp-config`` leaves out.
CODEX_OWN_SETTINGS_NOTE = (
    "Codex also starts with your own Codex settings, so any tools you added to "
    "Codex yourself are on in this terminal too."
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
    #: Third plan TM5 (decision G16): can the tool reach Jarvis's tools from
    #: the moment it starts? ``None`` = decided by ``ok`` and ``method``; a
    #: recipe that is set up but still needs a step by hand (Pi's adapter)
    #: says False, so the Launch menu never says "ready" for it.
    ready: bool | None = None

    def __post_init__(self) -> None:
        if not self.ok and not self.limitations:
            raise ValueError(
                "a failed RecipeResult must carry at least one limitation sentence"
            )

    @property
    def set_up(self) -> bool:
        """The recipe really configured something for the tool."""
        return self.ok and self.method != METHOD_NONE

    def row(self) -> dict[str, Any]:
        """The shape the Launch menu reads (plan section 13.1)."""
        return {
            "method": self.method,
            "ok": self.ok,
            "ready": self.set_up if self.ready is None else (self.ready and self.set_up),
            "limitations": list(self.limitations),
            "detail": self.detail,
        }


#: The tail every "could not" limitation carries, about the older plain launch.
_AS_TODAY = "The pane launches as it does today, without Jarvis capabilities."


def why_not(result: Any) -> str:
    """Third plan TM5: the plain reason a recipe could not be set up, for a
    refusal said where the user pressed — the recipe's first limitation
    without its sentence about the plain launch. Never empty."""
    for line in list(getattr(result, "limitations", None) or []):
        text = str(line or "").replace(_AS_TODAY, "").strip()
        if text:
            return text
    return "Iron Jarvis could not prepare this tool to reach its tools on this PC."


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
        # v1.499.3 review: through ``core/procs`` (the daemon's one door for a
        # child), not ``subprocess.run``: on Windows a ``.cmd`` shim's node
        # child kept the pipes open past the timeout kill and parked this
        # probe's thread; here a timeout kills the TREE and the drain after it
        # is bounded. stdin is DEVNULL there too.
        proc = _procs.run(argv, timeout=timeout, shell=False)
    except Exception:  # missing binary, OSError, permission — all unknown
        logger.debug("launch-recipe probe failed: %s", argv[:1], exc_info=True)
        return ""
    if proc.timed_out:
        logger.debug("launch-recipe probe timed out: %s", argv[:1])
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
            f"a launch recipe may only write inside the terminal's folder ({base})"
        ) from exc
    return target


def _write_config(path: Path, text: str) -> str:
    """Write ``text`` to ``path`` and return the absolute path, as a string.

    The file may hold a pane token, so it is narrowed to 0600 where the platform
    has POSIX modes. On Windows ``chmod`` is close to a no-op and this is said
    plainly rather than claimed: the file lives under the user's own account
    (Iron Jarvis's own folder, :func:`terminal_files_dir`),
    and what it references dies with the pane and with the process.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except Exception:  # pragma: no cover - platform dependent, never fatal
        pass
    return str(path)


def terminal_files_dir(home: Any) -> Path:
    """Fourth plan D9 option B: where a terminal's own files live — under Iron
    Jarvis's home (``<home>/terminals-mcp``), never in the user's folder."""
    return Path(str(home)) / TERMINAL_FILES_DIR


def _terminal_file(home: Any, terminal_id: str, suffix: str) -> Path | None:
    """``<home>/terminals-mcp/<terminal id><suffix>``, or None when there is
    no home or the id cannot name a file (never a path outside the folder)."""
    tid = str(terminal_id or "")
    if not home or not _FILE_SAFE_ID.match(tid):
        return None
    return terminal_files_dir(home) / f"{tid}{suffix}"


def remove_terminal_files(home: Any, terminal_id: str) -> None:
    """A terminal's own files go when it closes for good (Close, delete).

    Never raises: a close path that could fail on a locked file would cost the
    user the close. Nothing here is a credential (the files hold a reference to
    the terminal's environment variable), so this is tidiness, not security."""
    for suffix in _TERMINAL_FILE_SUFFIXES:
        try:
            path = _terminal_file(home, terminal_id, suffix)
            if path is not None:
                path.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001 - a close path never raises
            logger.warning("could not remove a terminal's own file", exc_info=True)


def sweep_terminal_files(
    home: Any, live_ids: Any, *, older_than: float | None = None
) -> int:
    """Remove the files of terminals that are not in ``live_ids`` (those that
    died with the daemon); returns how many went. BLOCKING (a folder listing):
    call it off the event loop. Never raises.

    ``older_than`` (a wall-clock time): a file written at or after it is
    never taken — a terminal opened while the sweep runs keeps its file even
    though its id was not known when ``live_ids`` was read."""
    keep = {str(i) for i in (live_ids or ())}
    removed = 0
    try:
        folder = terminal_files_dir(home) if home else None
        if folder is None or not folder.is_dir():
            return 0
        for path in list(folder.iterdir()):
            try:
                if path.suffix not in _TERMINAL_FILE_SUFFIXES or not path.is_file():
                    continue
                if not _FILE_SAFE_ID.match(path.stem) or path.stem in keep:
                    continue
                if older_than is not None and path.stat().st_mtime >= older_than:
                    continue
                path.unlink(missing_ok=True)
                removed += 1
            except Exception:  # noqa: BLE001 - one file never stops the rest
                logger.warning("could not remove a closed terminal's file", exc_info=True)
    except Exception:  # noqa: BLE001 - never raises
        logger.warning("could not tidy the terminals' own files", exc_info=True)
    return removed


#: How much of a project's ``.mcp.json`` :func:`leftovers_in` reads at most.
_LEFTOVER_READ_MAX = 1024 * 1024


def _old_terminal_entry(entry: Any) -> bool:
    """Is ``entry`` shaped like the one an older Iron Jarvis's Claude Code
    recipe wrote (HTTP, its header a reference to the terminal's key)?"""
    if not isinstance(entry, Mapping) or entry.get("type") != "http":
        return False
    headers = entry.get("headers")
    return (
        isinstance(headers, Mapping)
        and headers.get("Authorization") == f"Bearer {CONFIG_TOKEN_REFERENCE}"
    )


def leftovers_in(folder: Any) -> list[str]:
    """Absolute paths an older Iron Jarvis (v1.238 to v1.538) left in
    ``folder``: a ``.jarvis-codex`` folder, and a ``.mcp.json`` whose
    ``mcpServers`` holds an ``iron-jarvis`` entry shaped like that version's
    terminal entry. Named so the user can decide; never deleted or edited
    here (D9). BLOCKING (reads one small file); never raises; ``[]`` for a
    folder that is not there."""
    found: list[str] = []
    try:
        if not folder:
            return found
        base = Path(str(folder))
        if not base.is_dir():
            return found
        codex_home = base / OLD_CODEX_HOME_DIR
        if codex_home.is_dir():
            found.append(str(codex_home))
        mcp = base / OLD_PROJECT_FILE
        if mcp.is_file() and mcp.stat().st_size <= _LEFTOVER_READ_MAX:
            try:
                data = json.loads(mcp.read_text(encoding="utf-8") or "{}")
            except Exception:  # noqa: BLE001 - a file we cannot read names nothing
                data = None
            servers = data.get("mcpServers") if isinstance(data, dict) else None
            if isinstance(servers, dict) and _old_terminal_entry(servers.get(OLD_SERVER_NAME)):
                found.append(str(mcp))
    except Exception:  # noqa: BLE001 - never raises
        logger.debug("could not look for an older Iron Jarvis's files", exc_info=True)
    return found


def _no_home(version: str, env: dict[str, str]) -> RecipeResult:
    """Fourth plan D9 option B: the tool's file lives in Iron Jarvis's own
    folder, and with none known there is nothing to point the tool at — say so
    rather than hand back an option naming a file that was never written."""
    return RecipeResult(
        ok=True,
        method=METHOD_NONE,
        version=version,
        env=dict(env),
        limitations=[
            "Iron Jarvis could not find its own folder to keep this terminal's "
            "settings in, so it could not give this tool Iron Jarvis's tools. "
            + _AS_TODAY
        ],
        detail="no Iron Jarvis folder",
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
        detail="no terminal capabilities",
    )


# --------------------------------------------------------------------------- #
# Claude Code (Q04)
# --------------------------------------------------------------------------- #
class ClaudeCodeRecipe:
    """Claude Code: a file of the terminal's own in Iron Jarvis's folder
    (``<home>/terminals-mcp/<terminal id>.json``, passed as ``--mcp-config <abs
    path>``; fourth plan D9 option B — nothing in the project) plus whatever
    isolation the installed build advertises.

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
    #: The file's place in a dry run (no terminal id yet); ``prepare`` puts the
    #: real absolute path there.
    config_name = f"{TERMINAL_FILES_DIR}/<terminal>.json"

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
        the pane's shell already carries - so the file holds no credential even
        though it now lives in Iron Jarvis's own folder (until v1.538 it was the
        project's ``.mcp.json``, a file this CLI's convention says to commit). A
        literal bearer would also outlive the pane, which is the one thing
        :mod:`iron_jarvis.browser.panetokens` is written to prevent.
        """
        return {
            "type": "http",
            "url": url,
            "headers": {"Authorization": f"Bearer {CONFIG_TOKEN_REFERENCE}"},
        }

    def config_text(self, url: str) -> str:
        """The terminal's own file: one server, ours, under :data:`SERVER_NAME`."""
        return json.dumps({"mcpServers": {SERVER_NAME: self.server_entry(url)}}, indent=2)

    def prepare(
        self,
        pane_id: str,
        token: str,
        url: str,
        capabilities: Mapping[str, Any] | None = None,
        *,
        cwd: str = "",
        home: str = "",
    ) -> RecipeResult:
        """``home`` is Iron Jarvis's own folder: the terminal's file is written
        under it (:func:`terminal_files_dir`), never in ``cwd`` (D9 option B),
        and ``--mcp-config`` names it by its absolute path."""
        enabled = normalise_capabilities(capabilities)
        if not enabled:
            return _no_capabilities(self.cli_id, self.detect())
        base = self.inspect()
        if not base.ok:
            return base
        env = {MCP_URL_ENV: url, MCP_TOKEN_ENV: token}
        path = _terminal_file(home, pane_id, ".json")
        if path is None:
            # argv_extra points at the terminal's own file. With nowhere to
            # write it, handing back `--mcp-config <file>` anyway launches the
            # CLI straight into a config error.
            return _no_home(base.version, env)
        written = _write_config(path, self.config_text(url) + "\n")
        argv_extra = list(base.argv_extra)
        argv_extra[1] = written  # [config_flag, <the file>, ...] from _plan
        return RecipeResult(
            ok=True,
            method=base.method,
            version=base.version,
            env=env,
            config_writes=[written],
            argv_extra=argv_extra,
            limitations=list(base.limitations),
            detail=base.detail,
        )


# --------------------------------------------------------------------------- #
# Codex
# --------------------------------------------------------------------------- #
class CodexRecipe:
    """Codex: Codex's own per-run options, nothing written anywhere.

    Fourth plan TM1 (v1.539.0), the way Build › Code does it (measured on codex
    0.157.0, ``hosted/jarvis_tools.connect``): ``-c
    mcp_servers.<name>.url=<url> -c
    mcp_servers.<name>.bearer_token_env_var=IRONJARVIS_MCP_TOKEN``, the token
    in the terminal's environment only. Until v1.538 this recipe set
    ``CODEX_HOME=<project>/.jarvis-codex`` — a whole new Codex home, where the
    user's sign-in, settings and history live — so the terminal started signed
    out, its account's home overridden, and a folder was left in the project.
    The terminal now runs on ITS ACCOUNT's home and loads the user's own
    ``config.toml`` (the launch row says so).

    The values are BARE words, not TOML-quoted: Codex takes a value that does
    not parse as TOML as a plain string, and a bare word is typed unquoted in
    PowerShell 5.1 (which strips an embedded ``"`` from a native argument), cmd
    and bash alike. ``-c`` is FEATURE-DETECTED: the installed build's own help
    must name ``--config`` (``-c, --config <key=value>``).
    """

    cli_id = "codex"
    command = "codex"
    #: Needle searched for in Codex's own help (never passed unadvertised).
    config_flags = ("--config",)
    #: Jarvis's stdio bridge, used by the connected app's Codex entry and the
    #: frozen ``mcp-stdio`` subcommand (``test_entry_fast_paths_v1333``); the
    #: terminal recipe no longer starts it (Codex reaches ``/mcp`` over HTTP).
    shim_module = "iron_jarvis.mcpserver.stdio_shim"
    #: The hidden CLI subcommand that runs the same shim from the frozen exe
    #: (``daemon/cli.py:mcp_stdio``) — see :meth:`_shim_launch`.
    shim_command = "mcp-stdio"

    def detect(self) -> str:
        return _first_version_line(_probe(self.command, "--version"))

    def _help(self) -> str:
        return _probe(self.command, "--help")

    @staticmethod
    def _frozen() -> bool:
        """Is this the packaged app (``sys.executable`` is ``ironjarvis.exe``)?"""
        return bool(getattr(sys, "frozen", False))

    def _shim_launch(self) -> list[str]:
        """The argv AFTER ``sys.executable`` that starts Jarvis's stdio bridge.

        Two shapes, because ``sys.executable`` is two different programs:

        * dev:    ``["-m", "iron_jarvis.mcpserver.stdio_shim"]`` — a Python
          interpreter, which knows ``-m``.
        * frozen: ``["mcp-stdio"]`` — the packaged ``ironjarvis.exe``, whose
          entry point is the Typer CLI. Typer rejects ``-m`` ("No such
          option") before any bridge exists, so the installed app used to
          write a config Codex could never start. Same indirection as
          ``repl/session.py`` uses for ``repl-worker``, for the same reason.
        """
        if self._frozen():
            return [self.shim_command]
        return ["-m", self.shim_module]

    def _shim_available(self) -> bool:
        """Is Jarvis's stdio bridge REACHABLE the way :meth:`_shim_launch` starts it?

        The question is not "is the module bundled" — a frozen build bundles it
        and still could not run it. It is "does the launch argv this recipe
        writes actually reach the shim": frozen, the hidden CLI subcommand must
        be registered; in development, the module must be importable.
        """
        if self._frozen():
            try:
                from ..daemon.cli import app as _cli_app

                return any(
                    (info.name or info.callback.__name__.replace("_", "-"))
                    == self.shim_command
                    for info in _cli_app.registered_commands
                )
            except Exception:
                return False
        import importlib.util

        try:
            return importlib.util.find_spec(self.shim_module) is not None
        except Exception:
            return False

    def supports(self, version: str) -> str:
        return METHOD_HTTP if _advertises(self._help(), *self.config_flags) else ""

    def _plan(self, version: str) -> tuple[str, list[str], str]:
        help_text = self._help()
        if not _advertises(help_text, *self.config_flags):
            why = (
                "Iron Jarvis could not read this Codex build's help, so it could "
                "not check how to give it Iron Jarvis's tools for this terminal only."
                if not help_text
                else "This Codex build does not offer the --config option, so Iron "
                "Jarvis could not give it Iron Jarvis's tools for this terminal only."
            )
            return METHOD_NONE, [why + " " + _AS_TODAY], "no verified --config"
        return (
            METHOD_HTTP,
            [CODEX_OWN_SETTINGS_NOTE],
            "configured via --config",
        )

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

    @staticmethod
    def argv_for(url: str) -> list[str]:
        """Codex's two per-run options, values bare (see the class docstring).
        The token is never in them: Codex reads it from the environment
        variable the second option names."""
        key = f"mcp_servers.{CODEX_SERVER_NAME}"
        return [
            "-c", f"{key}.url={url}",
            "-c", f"{key}.bearer_token_env_var={MCP_TOKEN_ENV}",
        ]

    def prepare(
        self,
        pane_id: str,
        token: str,
        url: str,
        capabilities: Mapping[str, Any] | None = None,
        *,
        cwd: str = "",
        home: str = "",
    ) -> RecipeResult:
        """No file and no folder are needed: ``cwd`` and ``home`` are accepted
        for the shared seam and not used. The env holds the address and the
        key, and NEVER ``CODEX_HOME`` (the terminal's account decides it)."""
        enabled = normalise_capabilities(capabilities)
        if not enabled:
            return _no_capabilities(self.cli_id, self.detect())
        base = self.inspect()
        if not base.ok:
            return base
        return RecipeResult(
            ok=True,
            method=base.method,
            version=base.version,
            env={MCP_URL_ENV: url, MCP_TOKEN_ENV: token},
            argv_extra=self.argv_for(url),
            limitations=list(base.limitations),
            detail=base.detail,
        )


def _pi_step(runtime: str, path: str) -> str:
    """The step a Pi without ``-e`` still needs by hand (third plan TM5). With
    ``path`` (the adapter Iron Jarvis wrote for this terminal, fourth plan D9
    B) the line names it, quoted so it types the same in PowerShell, cmd and
    bash; without one (the dry run, before any terminal exists) it says where
    the file will be."""
    if path:
        return (
            "Pi does not load the Iron Jarvis adapter on its own: after it opens, run "
            f'`{runtime} "{path}"` in that terminal to give Pi the Jarvis tools.'
        )
    return (
        "Pi does not load the Iron Jarvis adapter on its own: after it opens, run "
        f"`{runtime}` with the adapter file Iron Jarvis keeps for that terminal in "
        "its own folder (the exact line is shown when the terminal opens) to give "
        "Pi the Jarvis tools."
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

    Fourth plan C-TM-3 (v1.539.0): a Pi whose help offers ``-e`` /
    ``--extension`` gets Build › Code's own extension
    (``hosted/jarvis_tools._pi_extension``: ``pi.registerMcpServer``, its header
    a reference to the terminal's key), written in Iron Jarvis's folder
    (``<home>/terminals-mcp/<terminal id>.js``) and passed as ``-e <abs
    path>``: ready from the start, nothing in the project. Only a Pi without
    ``-e`` still gets the adapter, also in Iron Jarvis's folder
    (``<terminal id>.mjs``), with its hand-typed step naming that path.

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

    #: Fourth plan C-TM-3: Pi's own option that loads an extension for one run
    #: (measured on Pi 1.3.0 by Build › Code, ``hosted/jarvis_tools``). The
    #: extension registers Iron Jarvis's server through Pi's own
    #: ``pi.registerMcpServer``, so Pi has the tools the moment it starts.
    extension_needles = ("-e", "--extension")

    def detect(self) -> str:
        return _first_version_line(_probe(self.command, "--version"))

    def _help(self) -> str:
        return _probe(self.command, "--help")

    def runtime(self) -> str:
        """The Node executable Pi's adapter would run under, or ``""``."""
        from .pi_adapter import pi_runtime

        return pi_runtime()

    def supports(self, version: str) -> str:
        help_text = self._help()
        if _advertises(help_text, *self.extension_needles):
            return METHOD_HTTP
        if _advertises(help_text, *self.native_needles):
            return METHOD_HTTP
        return METHOD_STDIO if self.runtime() else ""

    def _plan(self, version: str) -> tuple[str, list[str], str]:
        help_text = self._help()
        flag = _advertises(help_text, *self.extension_needles)
        if flag:
            return METHOD_HTTP, [], f"Iron Jarvis's extension, loaded with Pi's own {flag}"
        if _advertises(help_text, *self.native_needles):
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
        ready: bool | None = None
        if method == METHOD_STDIO:
            # Third plan TM5: the adapter is placed, but Pi does not start it —
            # said BEFORE the press, and never "ready". Fourth plan D9 B: the
            # file is the terminal's own (no id yet here); ``prepare`` names it.
            ready = False
            limitations = list(limitations) + [_pi_step(self.runtime(), "")]
        return RecipeResult(
            ok=method != METHOD_NONE,
            method=method,
            version=ver,
            limitations=list(limitations),
            detail=detail,
            ready=ready,
        )

    def prepare(
        self,
        pane_id: str,
        token: str,
        url: str,
        capabilities: Mapping[str, Any] | None = None,
        *,
        cwd: str = "",
        home: str = "",
    ) -> RecipeResult:
        """``home`` is Iron Jarvis's own folder: Pi's ``-e`` extension, or for
        a Pi without ``-e`` the adapter, is written under it
        (:func:`terminal_files_dir`), never in ``cwd`` (D9 option B)."""
        from .pi_adapter import adapter_source

        enabled = normalise_capabilities(capabilities)
        if not enabled:
            return _no_capabilities(self.cli_id, self.detect())
        base = self.inspect()
        if not base.ok:
            return base
        env = {MCP_URL_ENV: url, MCP_TOKEN_ENV: token}
        flag = _advertises(self._help(), *self.extension_needles)
        if base.method == METHOD_HTTP and flag:
            # C-TM-3: Build › Code's own extension, in Iron Jarvis's folder.
            from ..hosted.jarvis_tools import _pi_extension

            path = _terminal_file(home, pane_id, ".js")
            if path is None:
                return _no_home(base.version, env)
            written = _write_config(path, _pi_extension(url))
            return RecipeResult(
                ok=True,
                method=base.method,
                version=base.version,
                env=env,
                config_writes=[written],
                argv_extra=[flag, written],
                limitations=list(base.limitations),
                detail=base.detail,
            )
        if base.method == METHOD_HTTP:  # a Pi that speaks MCP itself — no adapter
            return RecipeResult(
                ok=True,
                method=base.method,
                version=base.version,
                env=env,
                limitations=list(base.limitations),
                detail=base.detail,
            )
        # Fourth plan D9 option B: the adapter is the terminal's own file too,
        # in Iron Jarvis's folder (``<home>/terminals-mcp/<id>.mjs``; ``.mjs``
        # so Node reads it as an ES module), never in the user's folder.
        path = _terminal_file(home, pane_id, ".mjs")
        if path is None:
            return _no_home(base.version, env)
        written = _write_config(path, adapter_source())
        # The hand-typed step, now naming the file by its absolute path (the
        # dry run said it without one, before the press: TM5).
        before = _pi_step(self.runtime(), "")
        limitations = [
            _pi_step(self.runtime(), written) if s == before else s
            for s in base.limitations
        ]
        return RecipeResult(
            ok=True,
            method=base.method,
            version=base.version,
            env=env,
            config_writes=[written],
            limitations=limitations,
            detail=base.detail,
            ready=False,
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
    home: str = "",
) -> RecipeResult | None:
    """:meth:`LaunchRecipe.prepare` off the event loop; ``None`` with no recipe."""
    recipe = recipe_for(cli_id)
    if recipe is None:
        return None
    return await asyncio.to_thread(
        lambda: recipe.prepare(pane_id, token, url, capabilities, cwd=cwd, home=home)
    )
