"""Launch recipes: detect, verify, configure, tokenise — and be honest (v1.238.0).

D18 says a harness adapter must detect the installed version, verify the method
THAT version supports, configure Jarvis MCP, provide the pane-scoped token,
expose only the pane's enabled capabilities, and **surface incompatibility
clearly**. Q04 says the same thing again for Claude Code and adds: if a build
cannot reliably disable an overlapping capability, say so rather than pretend
isolation exists. Q01 says Pi core is not assumed to consume MCP and that nothing
in the Browser architecture may depend on a third-party Pi MCP package.

Those are all statements about what happens when something is NOT available, so
almost every test here drives a degraded build rather than a healthy one. The
things this file refuses to let pass:

* a version probe that raises instead of degrading;
* a flag passed to a CLI that never advertised it (D18's "do not assume CLI
  configuration mechanisms remain static");
* an ``ok=False`` with no sentence a user can read;
* a config file written outside the pane's own folder;
* a token minted into the environment of a pane that grants nothing;
* a Pi path that needs a third-party package;
* a claim that the token reaches the child, asserted against a dict rather than
  against the environment the BACKEND was handed (the v1.217.0 lesson).

Nothing here runs a real CLI: ``recipes._run_probe`` is the module's one
subprocess chokepoint and every test drives it directly, on top of the
session-wide stub in ``conftest.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from iron_jarvis.browser.panetokens import MCP_TOKEN_ENV, MCP_URL_ENV
from iron_jarvis.terminals import ai_clis, pi_adapter, recipes
from iron_jarvis.terminals.backend import FakeBackend
from iron_jarvis.terminals.manager import TerminalManager

URL = "http://127.0.0.1:8787/mcp"
TOKEN = "pane-token-abcdef"

#: A Claude help text with EVERY option advertised. The tests below remove one
#: line at a time, because the interesting builds are the incomplete ones.
FULL_CLAUDE_HELP = """\
Usage: claude [options] [prompt]

Options:
  --mcp-config <file>          Load MCP servers from a JSON file
  --strict-mcp-config          Only use servers from --mcp-config
  --disallowed-tools <names>   Comma-separated tools to disable
  -h, --help                   display help
"""

CODEX_HELP = """\
Usage: codex [options]

Environment:
  CODEX_HOME   Directory holding config.toml
"""


@pytest.fixture
def probe(monkeypatch):
    """Drive the recipes' two probes by hand, resolving every CLI to a fake path.

    Returns a setter: ``probe("claude", version="1.2.3", help=FULL_CLAUDE_HELP)``.
    ``_find`` is stubbed too, so a machine where ``claude`` is genuinely
    installed and a CI runner where it is not take the identical path.
    """
    answers: dict[tuple[str, str], str] = {}

    monkeypatch.setattr(recipes, "_find", lambda command: f"/fake/bin/{command}")

    def _run(argv, **kw):
        exe = Path(str(argv[0])).name
        return answers.get((exe, str(argv[1])), "")

    monkeypatch.setattr(recipes, "_run_probe", _run)
    recipes.clear_probe_cache()

    def _set(command: str, *, version: str = "", help: str = "") -> None:
        answers[(command, "--version")] = version
        answers[(command, "--help")] = help
        recipes.clear_probe_cache()

    yield _set
    recipes.clear_probe_cache()


@pytest.fixture
def claude() -> recipes.ClaudeCodeRecipe:
    return recipes.ClaudeCodeRecipe()


@pytest.fixture
def codex() -> recipes.CodexRecipe:
    return recipes.CodexRecipe()


@pytest.fixture
def pi() -> recipes.PiRecipe:
    return recipes.PiRecipe()


def _read(path: str) -> str:
    """Read a file with CRLF normalised at the reader (the v1.232.1 lesson)."""
    return Path(path).read_text(encoding="utf-8").replace("\r\n", "\n")


# --------------------------------------------------------------------------- #
# detect() degrades; it never raises
# --------------------------------------------------------------------------- #
def test_detect_degrades_to_unknown_when_the_spawn_itself_raises(monkeypatch, claude):
    """"Treats EVERY failure as "" rather than raising" — including a raise.

    Driven through the REAL ``_run_probe`` against a ``subprocess.run`` that
    explodes, not through a stub that returns the answer being asserted: a pin
    that feeds itself the value proves nothing about the code under it. This is
    the first version detection in the repository, and it runs while a user is
    looking at a Launch menu — a CLI that was deleted out from under its PATH
    entry must produce "unknown" and not a 500.
    """
    monkeypatch.setattr(recipes, "_find", lambda command: "/fake/bin/claude")

    def _explode(*args, **kw):
        raise OSError("the binary went away between the lookup and the spawn")

    # Put the REAL chokepoint back over conftest's session-wide stub, then break
    # the thing it calls. Nothing here returns the value being asserted.
    monkeypatch.setattr(recipes, "_run_probe", recipes._run_probe_real)
    monkeypatch.setattr(recipes.subprocess, "run", _explode)
    recipes.clear_probe_cache()

    with pytest.raises(OSError):  # the failure is real
        recipes.subprocess.run(["/fake/bin/claude", "--version"])
    assert claude.detect() == ""
    assert claude.inspect().ok is False, "and an unknown build is refused, not guessed"


def test_run_probe_swallows_every_subprocess_failure(monkeypatch):
    """The chokepoint itself, driven against a real ``subprocess.run`` failure."""
    import subprocess

    def _explode(*args, **kw):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=0.1)

    monkeypatch.setattr(recipes.subprocess, "run", _explode)
    assert recipes._run_probe_real(["/fake/bin/claude", "--version"]) == ""
    assert recipes._run_probe_real([]) == ""


def test_detect_reports_the_first_line_verbatim(probe, claude):
    """No parsing, no comparison: whatever the CLI printed, bounded."""
    probe("claude", version="1.2.3 (Claude Code)\nextra noise\n")
    assert claude.detect() == "1.2.3 (Claude Code)"


def test_detect_reads_a_version_printed_on_stderr(monkeypatch, claude):
    """CLIs disagree about which stream a version belongs on; both are read."""

    class _Proc:
        stdout = b""
        stderr = b"claude 9.9.9\n"

    monkeypatch.setattr(recipes, "_run_probe", recipes._run_probe_real)
    monkeypatch.setattr(recipes.subprocess, "run", lambda *a, **kw: _Proc())
    monkeypatch.setattr(recipes, "_find", lambda command: "/fake/bin/claude")
    recipes.clear_probe_cache()
    assert claude.detect() == "claude 9.9.9"


# --------------------------------------------------------------------------- #
# An unverifiable build is ok=False WITH a sentence
# --------------------------------------------------------------------------- #
def test_a_claude_build_with_no_config_option_is_refused_with_a_reason(probe, claude):
    """D18 step 6. The pane still launches — it just gets no capabilities."""
    probe("claude", version="0.0.1", help="Usage: claude\n  -h, --help\n")
    result = claude.inspect()
    assert result.ok is False
    assert result.method == recipes.METHOD_NONE
    assert result.limitations, "ok=False must carry a sentence a user can read"
    assert "without Jarvis capabilities" in result.limitations[0]
    assert result.version == "0.0.1", "the version is reported even when refused"


def test_an_unreadable_help_is_refused_and_says_so_differently(probe, claude):
    """"Could not read the help" and "the help had no such option" are different
    facts, and the user can act on only one of them."""
    probe("claude", version="", help="")
    result = claude.inspect()
    assert result.ok is False
    assert "could not read" in result.limitations[0].lower()
    assert claude.supports("") == ""


def test_a_failed_result_cannot_be_built_without_a_limitation():
    """Enforced in the dataclass, not left to each construction site."""
    with pytest.raises(ValueError):
        recipes.RecipeResult(ok=False, method=recipes.METHOD_NONE)
    ok = recipes.RecipeResult(ok=True)
    assert ok.limitations == []


# --------------------------------------------------------------------------- #
# Feature detection, not hardcoded flags (D18, Q04)
# --------------------------------------------------------------------------- #
def test_the_strict_flag_is_used_only_when_the_build_advertises_it(probe, claude):
    """The flag exists as a NEEDLE in this module; that must not make it an
    argument passed to a build that never named it."""
    partial = FULL_CLAUDE_HELP.replace("  --strict-mcp-config          Only use servers from --mcp-config\n", "")
    assert "--strict-mcp-config" not in partial
    probe("claude", version="1.0.0", help=partial)
    result = claude.inspect()
    assert result.ok is True, "a missing isolation flag is a limitation, not a failure"
    assert "--strict-mcp-config" not in result.argv_extra
    assert any("global MCP servers" in s for s in result.limitations)

    probe("claude", version="1.0.0", help=FULL_CLAUDE_HELP)
    result = claude.inspect()
    assert "--strict-mcp-config" in result.argv_extra
    assert not any("global MCP servers" in s for s in result.limitations)


def test_q04_web_tools_limitation_is_surfaced_verbatim(probe, claude):
    """Q04's explicit instruction: surface the limitation rather than pretend
    isolation exists. The sentence is a constant so it cannot drift."""
    partial = FULL_CLAUDE_HELP.replace(
        "  --disallowed-tools <names>   Comma-separated tools to disable\n", ""
    )
    probe("claude", version="1.0.0", help=partial)
    result = claude.inspect()
    assert result.ok is True
    assert recipes.CLAUDE_WEB_TOOLS_LIMITATION in result.limitations
    assert not any(a.startswith("--disallowed") for a in result.argv_extra)


def test_the_disallow_flag_uses_the_spelling_the_build_advertises(probe, claude):
    """Claude Code has shipped both spellings. The one used is the one FOUND."""
    camel = FULL_CLAUDE_HELP.replace("--disallowed-tools", "--disallowedTools")
    probe("claude", version="1.0.0", help=camel)
    result = claude.inspect()
    assert "--disallowedTools" in result.argv_extra
    assert "--disallowed-tools" not in result.argv_extra
    assert recipes.CLAUDE_WEB_TOOLS_LIMITATION not in result.limitations
    # and the tool names ride as the flag's argument, not as bare words
    idx = result.argv_extra.index("--disallowedTools")
    assert result.argv_extra[idx + 1] == "WebFetch,WebSearch"


def test_q04_says_so_when_only_the_flag_NAME_could_be_verified(probe, claude):
    """Q04: do not pretend isolation exists.

    The flag name is feature-detected; its ARGUMENT GRAMMAR is not, and cannot be
    from a help scrape. Claude Code has shipped builds that take disallowed tools
    as separate space-separated arguments — such a build receives one argument
    spelled `WebFetch,WebSearch`, matches no tool and disables nothing, and until
    this fix the Launch menu showed NO limitation at all, which reads as complete
    isolation.
    """
    probe(
        "claude",
        version="1.0.0",
        help=(
            "Usage: claude [options]\n"
            "  --mcp-config <file>\n"
            "  --strict-mcp-config\n"
            "  --disallowed-tools <names>   tools to disable\n"
        ),
    )
    result = claude.inspect()
    assert result.ok is True
    assert "--disallowed-tools" in result.argv_extra, "best effort is still made"
    assert recipes.CLAUDE_WEB_TOOLS_UNVERIFIED_LIMITATION in result.limitations, (
        "a build whose argument form could not be verified reported silence, "
        "which the Launch menu renders as isolation that is in force"
    )
    assert recipes.CLAUDE_WEB_TOOLS_LIMITATION not in result.limitations, (
        "the flag WAS advertised — the other sentence is for a build that has none"
    )


@pytest.mark.parametrize(
    "line",
    [
        "  --disallowed-tools <names>   Comma-separated tools to disable",
        "  --disallowed-tools <tool1,tool2>   tools to disable",
    ],
    ids=["prose", "metavar"],
)
def test_a_build_that_documents_the_comma_form_earns_silence(probe, claude, line):
    """The build's own help line is the only honest evidence about the argument
    form: either the prose says comma, or the metavar carries one."""
    probe(
        "claude",
        version="1.0.0",
        help=f"Usage: claude\n  --mcp-config <file>\n  --strict-mcp-config\n{line}\n",
    )
    result = claude.inspect()
    assert result.limitations == []


def test_a_fully_capable_build_reports_no_limitations(probe, claude):
    probe("claude", version="1.0.0", help=FULL_CLAUDE_HELP)
    result = claude.inspect()
    assert result.ok is True
    assert result.method == recipes.METHOD_HTTP
    assert result.limitations == []
    assert claude.supports("1.0.0") == recipes.METHOD_HTTP


# --------------------------------------------------------------------------- #
# Configuration is written, confined, and recorded
# --------------------------------------------------------------------------- #
def test_the_claude_recipe_writes_only_into_the_pane_cwd_and_records_the_path(
    probe, claude, tmp_path
):
    probe("claude", version="1.0.0", help=FULL_CLAUDE_HELP)
    result = claude.prepare("term_1", TOKEN, URL, {"browser": True}, cwd=str(tmp_path))

    assert len(result.config_writes) == 1
    written = Path(result.config_writes[0])
    assert written.is_absolute(), "the diagnostics panel shows an absolute path"
    assert written.parent.resolve() == tmp_path.resolve()
    assert written.name == ".mcp.json"

    body = json.loads(_read(str(written)))
    assert list(body["mcpServers"]) == [recipes.SERVER_NAME], "only Jarvis's server"
    assert body["mcpServers"][recipes.SERVER_NAME]["url"] == URL
    # THE TOKEN IS NOT IN THE FILE. `.mcp.json` is the file this CLI's own
    # convention says to COMMIT, so a literal bearer here is a credential that
    # drives the user's logged-in browser, published by their next `git push`
    # and alive until the daemon restarts.
    auth = body["mcpServers"][recipes.SERVER_NAME]["headers"]["Authorization"]
    assert TOKEN not in _read(str(written)), (
        "the pane token is written in plaintext into the pane's project folder"
    )
    assert auth == f"Bearer ${{{MCP_TOKEN_ENV}}}", (
        "the config must reference the environment variable the pane already "
        f"carries, not the secret: got {auth!r}"
    )

    # nothing outside the pane's folder was touched
    assert sorted(p.name for p in tmp_path.iterdir()) == [".mcp.json"]


def test_a_write_outside_the_pane_folder_is_refused(tmp_path):
    inside = recipes._confined(str(tmp_path), ".mcp.json")
    assert inside.parent.resolve() == tmp_path.resolve()
    with pytest.raises(recipes.ConfigWriteRefused):
        recipes._confined(str(tmp_path), "..", "escaped.json")
    with pytest.raises(recipes.ConfigWriteRefused):
        recipes._confined(str(tmp_path), str(Path(tmp_path).parent / "elsewhere.json"))


def test_prepare_without_a_cwd_carries_the_token_and_claims_no_configuration(
    probe, claude
):
    """No folder means no file — and therefore no flags POINTING at that file.

    `--mcp-config .mcp.json` for a `.mcp.json` that was never written launches
    Claude Code straight into a config error, which the user reads as a crashed
    pane. The previous version of this test asserted only `config_writes == []`
    and so blessed the broken combination instead of catching it.
    """
    probe("claude", version="1.0.0", help=FULL_CLAUDE_HELP)
    result = claude.prepare("term_1", TOKEN, URL, {"browser": True})
    assert result.config_writes == []
    assert result.env[MCP_TOKEN_ENV] == TOKEN
    assert "--mcp-config" not in result.argv_extra, (
        "the recipe returned the flag pair for a file it never wrote"
    )
    assert result.argv_extra == []
    assert result.method == recipes.METHOD_NONE
    assert result.limitations and "no working folder" in result.limitations[0]


def test_an_existing_mcp_json_is_merged_never_clobbered(probe, claude, tmp_path):
    """`.mcp.json` is the standard project-scoped Claude Code config, so a
    developer who has one has their OWN servers in it. Taking those away with no
    message anywhere is the same dishonesty this module exists to prevent, done
    to a file instead of to a sentence."""
    probe("claude", version="1.0.0", help=FULL_CLAUDE_HELP)
    path = tmp_path / ".mcp.json"
    path.write_text(
        json.dumps({"mcpServers": {"my-own-server": {"command": "mine"}}}, indent=2),
        encoding="utf-8",
    )

    result = claude.prepare("term_1", TOKEN, URL, {"browser": True}, cwd=str(tmp_path))

    body = json.loads(_read(str(path)))
    assert body["mcpServers"]["my-own-server"] == {"command": "mine"}, (
        "the user's own MCP server was deleted by a Jarvis launch"
    )
    assert recipes.SERVER_NAME in body["mcpServers"]
    assert any("already had a .mcp.json" in s for s in result.limitations), (
        "a file of the user's was changed and nothing said so"
    )


def test_an_unreadable_mcp_json_is_left_strictly_alone(probe, claude, tmp_path):
    """A file we cannot parse is a file we must not overwrite: we cannot know
    what is in it. The refusal is a limitation the user can read, not silence."""
    probe("claude", version="1.0.0", help=FULL_CLAUDE_HELP)
    path = tmp_path / ".mcp.json"
    path.write_text("{ this is not json", encoding="utf-8")

    result = claude.prepare("term_1", TOKEN, URL, {"browser": True}, cwd=str(tmp_path))

    assert _read(str(path)) == "{ this is not json"
    assert result.config_writes == []
    assert result.method == recipes.METHOD_NONE
    assert result.argv_extra == []
    assert result.limitations and "left it alone" in result.limitations[0]


def test_the_pane_config_is_removed_the_way_the_pane_token_is_revoked(
    probe, claude, tmp_path
):
    """A config that outlives its pane points a harness at a credential that no
    longer resolves. `remove_config_writes` is the file half of the revocation
    the three close paths already do for the token — and it takes back ONLY
    Jarvis's own entry."""
    probe("claude", version="1.0.0", help=FULL_CLAUDE_HELP)
    path = tmp_path / ".mcp.json"
    path.write_text(
        json.dumps({"mcpServers": {"my-own-server": {"command": "mine"}}}, indent=2),
        encoding="utf-8",
    )
    result = claude.prepare("term_1", TOKEN, URL, {"browser": True}, cwd=str(tmp_path))

    removed = recipes.remove_config_writes(result.config_writes)

    assert removed == result.config_writes
    body = json.loads(_read(str(path)))
    assert recipes.SERVER_NAME not in body["mcpServers"], "Jarvis's entry survived"
    assert body["mcpServers"]["my-own-server"] == {"command": "mine"}

    # A file that was ONLY ever Jarvis's goes away entirely.
    second = tmp_path / "second"
    second.mkdir()
    only_ours = claude.prepare(
        "term_2", TOKEN, URL, {"browser": True}, cwd=str(second)
    )
    assert (second / ".mcp.json").exists()
    recipes.remove_config_writes(only_ours.config_writes)
    assert list(second.iterdir()) == []

    # And it never raises on a path that is already gone.
    assert recipes.remove_config_writes(only_ours.config_writes) == []


# --------------------------------------------------------------------------- #
# Only the pane's enabled capabilities (D18 step 5)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "capabilities",
    [None, {}, {"browser": False}, {"browser": "false"}, {"browser": 0}],
    ids=["none", "empty", "false", "string-false", "zero"],
)
def test_a_pane_that_grants_nothing_gets_no_token_and_no_config(
    probe, claude, tmp_path, capabilities
):
    """A credential handed to a child that may not use it is a credential handed
    out for nothing. ``"false"`` is included because capabilities reach this
    module through JSON, where that is an ordinary truthy string."""
    probe("claude", version="1.0.0", help=FULL_CLAUDE_HELP)
    result = claude.prepare("term_1", TOKEN, URL, capabilities, cwd=str(tmp_path))
    assert result.ok is True, "no capabilities is a configuration, not a failure"
    assert result.method == recipes.METHOD_NONE
    assert MCP_TOKEN_ENV not in result.env
    assert result.env == {}
    assert result.config_writes == []
    assert list(tmp_path.iterdir()) == []
    assert result.limitations and "no capabilities enabled" in result.limitations[0]


# --------------------------------------------------------------------------- #
# Codex
# --------------------------------------------------------------------------- #
def test_codex_is_refused_when_its_build_does_not_advertise_its_home_var(probe, codex):
    probe("codex", version="0.5.0", help="Usage: codex [options]\n")
    result = codex.inspect()
    assert result.ok is False
    assert codex.home_env in result.limitations[0]
    assert codex.supports("0.5.0") == ""


def test_codex_writes_a_pane_scoped_config_and_points_at_it_by_env(
    probe, codex, tmp_path
):
    probe("codex", version="0.5.0", help=CODEX_HELP)
    result = codex.prepare("term_1", TOKEN, URL, {"browser": True}, cwd=str(tmp_path))
    assert result.ok is True
    assert result.method == recipes.METHOD_STDIO
    home = Path(result.env[codex.home_env])
    assert home.resolve().parent == tmp_path.resolve()
    written = Path(result.config_writes[0])
    assert written.parent.resolve() == home.resolve()
    assert written.name == "config.toml"
    body = _read(str(written))
    assert f"[mcp_servers.{json.dumps(recipes.SERVER_NAME)}]" in body
    assert codex.shim_module in body
    assert TOKEN not in body, "the token rides the environment, never the file"
    assert result.env[MCP_TOKEN_ENV] == TOKEN


def test_codex_is_refused_when_the_stdio_bridge_is_missing(monkeypatch, probe, codex):
    """A build that dropped the shim would otherwise produce a harness that
    starts, connects to nothing, and reports success."""
    probe("codex", version="0.5.0", help=CODEX_HELP)
    monkeypatch.setattr(recipes.CodexRecipe, "_shim_available", lambda self: False)
    result = codex.inspect()
    assert result.ok is False
    assert "stdio bridge" in result.limitations[0]
    assert codex.supports("0.5.0") == ""


# --------------------------------------------------------------------------- #
# Pi (Q01)
# --------------------------------------------------------------------------- #
def test_the_pi_recipe_places_a_jarvis_owned_adapter_and_needs_no_mcp_package(
    monkeypatch, probe, pi, tmp_path
):
    """Q01: "Do not make Browser capability depend on a third-party Pi MCP
    package." The adapter is Jarvis's own file, plain Node, no dependencies."""
    probe("pi", version="0.4.0", help="Usage: pi\n  -h, --help\n")
    monkeypatch.setattr(recipes.PiRecipe, "runtime", lambda self: "/fake/bin/node")

    result = pi.prepare("term_1", TOKEN, URL, {"browser": True}, cwd=str(tmp_path))
    assert result.ok is True
    assert result.method == recipes.METHOD_STDIO
    written = Path(result.config_writes[0])
    assert written.parent.resolve() == tmp_path.resolve()
    assert written.name == pi_adapter.ADAPTER_FILENAME

    source = _read(str(written))
    assert "require(" not in source, "no CommonJS dependency loading at all"
    assert "@modelcontextprotocol" not in source
    assert "npm install" not in source
    assert TOKEN not in source, "the token rides the environment, never the file"
    assert pi_adapter.URL_ENV in source and pi_adapter.TOKEN_ENV in source
    # and Pi does not load it by itself — say so rather than imply otherwise
    assert any("does not load" in s for s in result.limitations)


def test_the_adapter_env_names_match_the_pane_token_module():
    """Drift here would leave the adapter reading a variable nothing sets and
    reporting "no token" forever."""
    assert pi_adapter.URL_ENV == MCP_URL_ENV
    assert pi_adapter.TOKEN_ENV == MCP_TOKEN_ENV
    source = pi_adapter.adapter_source()
    assert f'const URL_ENV = "{MCP_URL_ENV}"' in source
    assert f'const TOKEN_ENV = "{MCP_TOKEN_ENV}"' in source


def test_a_pi_that_speaks_mcp_natively_skips_the_adapter_entirely(
    monkeypatch, probe, pi, tmp_path
):
    """Q01's second half: the canonical architecture must not DEPEND on the
    adapter, so a verified native path drops it."""
    probe("pi", version="9.0.0", help="Usage: pi\n  --mcp-config <file>\n")
    monkeypatch.setattr(recipes.PiRecipe, "runtime", lambda self: "/fake/bin/node")
    assert pi.supports("9.0.0") == recipes.METHOD_HTTP
    result = pi.prepare("term_1", TOKEN, URL, {"browser": True}, cwd=str(tmp_path))
    assert result.method == recipes.METHOD_HTTP
    assert result.config_writes == []
    assert list(tmp_path.iterdir()) == []


def test_pi_is_refused_when_no_node_runtime_resolves(monkeypatch, probe, pi):
    probe("pi", version="0.4.0", help="Usage: pi\n")
    monkeypatch.setattr(recipes.PiRecipe, "runtime", lambda self: "")
    result = pi.inspect()
    assert result.ok is False
    assert "Node runtime" in result.limitations[0]


def test_pi_runtime_resolves_through_the_shared_cli_finder(monkeypatch):
    """"Resolve Pi's runtime the way ai_clis already does" — the same ``_find``
    whose Windows fallbacks include %LOCALAPPDATA%/pi-node/current."""
    seen: list[str] = []

    def _find(command):
        seen.append(command)
        return "/pi-node/current/node.exe" if command == "node" else None

    monkeypatch.setattr(pi_adapter, "_find", _find)
    assert pi_adapter.pi_runtime() == "/pi-node/current/node.exe"
    assert seen[0] == "node"

    monkeypatch.setattr(pi_adapter, "_find", lambda command: None)
    assert pi_adapter.pi_runtime() == ""


@pytest.mark.parametrize(
    "help_text",
    [
        "Usage: pi\n  --mcp-debug   print MCP debug logs (unsupported)\n",
        "Usage: pi\n  --no-mcp-config   ignore any MCP config\n",
        "Usage: pi\n  this build has no --mcp support yet\n",
        "Usage: pi\n  --mcp-log-level <level>\n",
    ],
    ids=["mcp-debug", "no-mcp-config", "prose", "mcp-log-level"],
)
def test_a_pi_that_only_mentions_mcp_is_not_declared_to_speak_it(
    monkeypatch, probe, pi, help_text
):
    """`--mcp` was a bare substring test, so `--mcp-debug` — and the sentence
    "this build has no --mcp support" — declared the build native: ok=True,
    mcp_http, zero limitations, no adapter placed. The user saw a green "Jarvis
    capabilities over HTTP" on a Pi that could not reach Jarvis at all."""
    probe("pi", version="0.4.0", help=help_text)
    monkeypatch.setattr(recipes.PiRecipe, "runtime", lambda self: "/fake/bin/node")
    assert pi.supports("0.4.0") == recipes.METHOD_STDIO
    assert pi.inspect().method == recipes.METHOD_STDIO


def test_a_needle_matches_a_whole_option_and_not_a_longer_one():
    """The rule under the parametrized case above, stated once."""
    assert recipes._advertises("  --mcp-config <file>", "--mcp-config") == "--mcp-config"
    assert recipes._advertises("  --no-mcp-config", "--mcp-config") == ""
    assert recipes._advertises("  --mcp-debug", "--mcp") == ""
    assert recipes._advertises("  --disallowed-tools=<a,b>", "--disallowed-tools")
    assert recipes._advertises("  CODEX_HOME   dir", "CODEX_HOME") == "CODEX_HOME"
    assert recipes._advertises("  MY_CODEX_HOME_X", "CODEX_HOME") == ""


def test_a_probe_on_a_host_with_no_resolvable_home_is_unknown_not_a_500(monkeypatch):
    """`detect()` says "Never raises", and `_find` sat outside the guard: on a
    host where `Path.home()` raises (some service accounts), the exception left
    `detect()`, left `inspect()`, and took `GET /terminals/ai-clis` — the whole
    Launch menu — down with a 500."""

    def _boom(command):
        raise OSError("no home directory on this host")

    monkeypatch.setattr(recipes, "_find", _boom)
    recipes.clear_probe_cache()
    assert recipes.ClaudeCodeRecipe().detect() == ""
    assert recipes.ClaudeCodeRecipe().inspect().ok is False


# --------------------------------------------------------------------------- #
# The Pi adapter is a PROGRAM, so it is run as one
# --------------------------------------------------------------------------- #
def _real_node() -> str:
    """The developer's / runner's real Node, bypassing the session isolation.

    ``conftest`` stubs ``pi_adapter._find`` so no recipe reads host state; this
    test is the one place that WANTS the host, because a script can only be
    proven by executing it. ``ai_clis._find`` is the unstubbed resolver.
    """
    from iron_jarvis.terminals import ai_clis as _ai_clis

    for candidate in ("node", "node.exe"):
        found = _ai_clis._find(candidate)
        if found:
            return found
    return ""


class _StubMcp(ThreadingHTTPServer):
    daemon_threads = True


def _stub_mcp_server(seen: list[dict]) -> _StubMcp:
    """A minimal ``POST /mcp`` that answers ``initialize`` SLOWLY.

    The delay is the point: it opens the window in which a second stdin line
    arrives while the first relay is still awaiting, which is precisely the state
    the adapter got wrong. Nothing here is timed or asserted on the clock.
    """

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # keep the suite's output clean
            pass

        def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler's spelling
            body = json.loads(
                self.rfile.read(int(self.headers.get("content-length", "0")) or 0)
            )
            seen.append(
                {
                    "method": body.get("method"),
                    "session": self.headers.get("mcp-session-id") or "",
                    "auth": self.headers.get("authorization") or "",
                }
            )
            headers = {}
            if body.get("method") == "initialize":
                time.sleep(0.3)  # a slow handshake, not a timing assertion
                headers["Mcp-Session-Id"] = "sid-1"
            payload = json.dumps(
                {"jsonrpc": "2.0", "id": body.get("id"), "result": {"saw": body.get("method")}}
            ).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(payload)

    return _StubMcp(("127.0.0.1", 0), _Handler)


@pytest.mark.skipif(not _real_node(), reason="no Node runtime on this host")
def test_the_generated_adapter_keeps_order_carries_the_session_and_drains(tmp_path):
    """The adapter RUN, not string-matched (the v1.238.0 review's finding).

    Every other Pi assertion in this file reads ``adapter_source()`` as text, and
    the script was therefore unverified as a program. Run for real it failed
    twice: two overlapping ``data`` handlers sent request 2 before ``initialize``
    had returned a session id -- against the real ``/mcp`` that request is
    rejected, which the user meets as "Pi connected to Jarvis, then every tool
    call fails" -- and ``process.exit(0)`` on ``end`` discarded every in-flight
    request, so a client that wrote and closed stdin got silence.
    """
    node = _real_node()
    seen: list[dict] = []
    server = _stub_mcp_server(seen)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_address[1]}/mcp"
        script = tmp_path / pi_adapter.ADAPTER_FILENAME
        script.write_text(pi_adapter.adapter_source(), encoding="utf-8")

        env = {
            **os.environ,
            pi_adapter.URL_ENV: endpoint,
            pi_adapter.TOKEN_ENV: TOKEN,
        }
        proc = subprocess.Popen(
            [node, str(script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(tmp_path),
            text=True,
        )
        assert proc.stdin is not None
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}) + "\n")
        proc.stdin.flush()
        time.sleep(0.05)  # a second client write while the first is in flight
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n")
        proc.stdin.flush()
        proc.stdin.close()  # and then the client goes away
        out, err = proc.communicate(timeout=60)
    finally:
        server.shutdown()
        server.server_close()

    answers = [json.loads(line) for line in out.splitlines() if line.strip()]
    assert [a["id"] for a in answers] == [1, 2], (
        f"the adapter dropped or reordered answers (stdout={out!r} stderr={err!r})"
    )
    assert [r["method"] for r in seen] == ["initialize", "tools/list"], (
        "the second request overtook the handshake it depends on"
    )
    assert seen[1]["session"] == "sid-1", (
        "the second request went out with no Mcp-Session-Id, which the real /mcp "
        "refuses -- the session header is captured from initialize's RESPONSE, so "
        "a request built before that response lands cannot carry it"
    )
    assert seen[0]["auth"] == f"Bearer {TOKEN}", "the pane token rides the header"
    assert proc.returncode == 0


# --------------------------------------------------------------------------- #
# The registry: everything else keeps today's behaviour EXACTLY
# --------------------------------------------------------------------------- #
def test_only_three_catalog_entries_have_a_recipe():
    assert set(recipes.RECIPES) == {"claude", "codex", "pi"}
    for cli in ai_clis.AI_CLIS:
        if cli["id"] not in {"claude", "codex", "pi"}:
            assert recipes.recipe_for(cli["id"]) is None
            assert recipes.recipe_state(cli["id"]) is None


def test_detect_ai_clis_carries_version_and_recipe_on_every_row(monkeypatch, probe):
    """D18: "The Launch menu must understand the recipe type" — so the state has
    to be on the row BEFORE the user launches."""
    monkeypatch.setattr(ai_clis, "_find", lambda command: f"/fake/bin/{command}")
    probe("claude", version="1.0.0", help=FULL_CLAUDE_HELP)

    rows = {c["id"]: c for c in ai_clis.detect_ai_clis()}
    assert set(rows) == {c["id"] for c in ai_clis.AI_CLIS}
    for row in rows.values():
        assert "version" in row and "recipe" in row
        # today's columns are untouched
        assert set(row) >= {"id", "label", "command", "installed", "path", "autopilot_flag"}

    assert rows["claude"]["version"] == "1.0.0"
    assert rows["claude"]["recipe"]["ok"] is True
    assert rows["claude"]["recipe"]["method"] == recipes.METHOD_HTTP
    assert rows["claude"]["recipe"]["limitations"] == []

    # A CLI with no recipe is NOT a failure state: no recipe, no version, no token.
    assert rows["grok"]["recipe"] is None
    assert rows["grok"]["version"] == ""
    assert rows["aider"]["recipe"] is None


def test_detect_ai_clis_probes_only_installed_clis_that_have_a_recipe(monkeypatch):
    """Rule 6, measured: nothing is prepared for the ten CLIs that have no recipe,
    and nothing is run for a CLI that is not installed.

    Counted on ``inspect`` rather than on ``_run_probe``, because the probe cache
    would hide a second, third and thirteenth visit to the same recipe behind one
    subprocess and the count would then be a measurement of the cache.
    """
    inspected: list[str] = []
    for cls in (recipes.ClaudeCodeRecipe, recipes.CodexRecipe, recipes.PiRecipe):
        original = cls.inspect

        def _spy(self, *args, _cls=cls, _orig=original, **kw):
            inspected.append(_cls.cli_id)
            return _orig(self, *args, **kw)

        monkeypatch.setattr(cls, "inspect", _spy)

    def only_claude(command):
        return "/fake/bin/claude" if command == "claude" else None

    monkeypatch.setattr(ai_clis, "_find", only_claude)
    monkeypatch.setattr(recipes, "_find", only_claude)
    monkeypatch.setattr(recipes, "_run_probe", lambda argv, **kw: "")
    recipes.clear_probe_cache()

    ai_clis.detect_ai_clis()
    assert inspected == ["claude"], f"prepared something it should not have: {inspected}"

    inspected.clear()
    recipes.clear_probe_cache()
    ai_clis.detect_ai_clis(probe=False)
    assert inspected == [], "probe=False inspects no CLI at all"


def test_recipe_state_is_the_shape_the_launch_menu_reads(probe):
    probe("claude", version="1.0.0", help=FULL_CLAUDE_HELP)
    state = recipes.recipe_state("claude")
    assert set(state) == {"method", "ok", "limitations", "detail"}
    assert isinstance(state["limitations"], list)


async def test_the_async_wrappers_run_off_the_event_loop(monkeypatch, probe, tmp_path):
    """Probing spawns a process and preparing writes files: neither may run on
    the daemon's single loop (the v1.153.1 outage)."""
    import threading

    main = threading.get_ident()
    threads: list[int] = []

    original = recipes._run_probe

    def _run(argv, **kw):
        threads.append(threading.get_ident())
        return original(argv, **kw)

    probe("claude", version="1.0.0", help=FULL_CLAUDE_HELP)
    monkeypatch.setattr(recipes, "_run_probe", _run)
    recipes.clear_probe_cache()

    state = await recipes.recipe_state_async("claude")
    assert state["ok"] is True
    assert threads and all(t != main for t in threads)

    assert await recipes.recipe_state_async("aider") is None
    assert await recipes.prepare_async("aider", "term_1", TOKEN, URL, {"browser": True}) is None

    result = await recipes.prepare_async(
        "claude", "term_1", TOKEN, URL, {"browser": True}, cwd=str(tmp_path)
    )
    assert result.env[MCP_TOKEN_ENV] == TOKEN


# --------------------------------------------------------------------------- #
# The token reaches the CHILD, and the config exists before the spawn
# --------------------------------------------------------------------------- #
class _RecordingBackend(FakeBackend):
    """Records the environment it was started with, and what was on disk then.

    Asserting against ``result.env`` would prove only that a dict holds a string.
    The v1.217.0 identity bug passed exactly that shape of test while the shell
    received nothing, so this reads the environment the BACKEND was handed.
    """

    def __init__(self, watch: Path) -> None:
        super().__init__()
        self.env: dict | None = None
        self.watch = watch
        self.config_existed_at_spawn: bool | None = None

    def start(self, argv, cwd, env, cols, rows) -> None:  # type: ignore[override]
        self.env = dict(env) if env is not None else None
        self.config_existed_at_spawn = self.watch.exists()
        super().start(argv, cwd, env, cols, rows)


def test_the_token_is_in_the_child_environment_and_the_config_is_already_written(
    probe, claude, tmp_path
):
    """Plan section 13.3: the config writes happen BEFORE the spawn and the env
    reaches the child, because the pane id is minted before the shell starts.

    Driven through the MANAGER'S OWN recipe seam rather than by handing `create`
    a pre-built env, because that is the only path a real launch takes: the
    manager mints the pane token, merges the preparer's answer into `pane_env`,
    and only then spawns. (It also strips an inherited `IRONJARVIS_MCP_TOKEN`
    from the daemon's environment, so an env handed in at the door no longer
    carries one — which is exactly why this must be asserted at the seam that
    survives.)
    """
    probe("claude", version="1.0.0", help=FULL_CLAUDE_HELP)
    backend = _RecordingBackend(tmp_path / ".mcp.json")
    seen: list[dict] = []

    def _preparer(**kw):
        seen.append(kw)
        return claude.prepare(
            kw["pane_id"], kw["token"], URL, kw["capabilities"], cwd=str(tmp_path)
        ).env

    manager = TerminalManager(mcp_url=URL, recipe_preparer=_preparer)
    session = manager.create(
        cwd=str(tmp_path),
        backend=backend,
        capabilities={"browser": True},
        recipe="claude",
    )

    assert seen and seen[0]["capabilities"]["browser"] is True
    assert seen[0]["recipe"] == "claude"
    assert backend.env is not None
    minted = backend.env[MCP_TOKEN_ENV]
    assert minted, "the child was spawned without a pane token"
    grant = manager.pane_tokens.resolve(minted)
    assert grant is not None and grant.pane_id == session.id, (
        "the token in the child's environment does not resolve to this pane"
    )
    assert backend.env[MCP_URL_ENV] == URL, "a credential with no address"
    assert backend.env["IRONJARVIS_PANE_ID"] == session.id, "the pane identity survives"
    assert "PATH" in backend.env or "Path" in backend.env, "the shell keeps its PATH"
    assert backend.config_existed_at_spawn is True
    manager.kill_all()


def test_a_pane_that_grants_nothing_gets_no_token_from_the_recipe_or_by_inheritance(
    monkeypatch, probe, claude, tmp_path
):
    """Two ways a credential could reach a pane that grants nothing, and neither
    may: the recipe must contribute none, and the daemon's own environment must
    not leak one in. (The daemon is often started from a shell inside Build, so
    `os.environ` really can carry another pane's live token.)"""
    probe("claude", version="1.0.0", help=FULL_CLAUDE_HELP)
    result = claude.prepare("term_1", TOKEN, URL, {}, cwd=str(tmp_path))
    assert result.env == {}, "the recipe minted a credential for a pane granting none"

    monkeypatch.setenv(MCP_TOKEN_ENV, "another-panes-live-token")
    backend = _RecordingBackend(tmp_path / ".mcp.json")
    manager = TerminalManager()
    manager.create(cwd=str(tmp_path), backend=backend, capabilities={})
    assert MCP_TOKEN_ENV not in backend.env, (
        "the pane inherited a pane token from the daemon's own environment"
    )
    manager.kill_all()


# --------------------------------------------------------------------------- #
# Isolation: the suite never runs the developer's real CLI
# --------------------------------------------------------------------------- #
def test_the_session_fixture_really_holds_all_three_host_seams():
    """BEHAVIOUR, not a source grep (the v1.238.0 review found the grep worthless:
    every string it looked for also appears in the fixture's own docstring, so
    deleting the assignment lines that ARE the isolation left it green).

    Each assertion below goes red on ANY host the moment its line is removed from
    the fixture, because without the fixture each name is the real one imported
    from its own module — a different object, whatever that object then answers.
    """
    from iron_jarvis.terminals import ai_clis as _ai_clis

    assert recipes._run_probe is not recipes._run_probe_real, (
        "the subprocess chokepoint is unstubbed: this run would spawn the "
        "developer's real claude/codex/pi"
    )
    assert recipes._find is not _ai_clis._find, (
        "recipes._find is the real resolver, so whether a CLI is probed at all "
        "depends on what is installed on this box"
    )
    assert pi_adapter._find is not _ai_clis._find, (
        "pi_adapter._find is the real resolver, so PiRecipe's ok/detail are read "
        "off this developer's filesystem"
    )


def test_under_the_bare_fixture_every_recipe_answers_the_ci_answer():
    """The consequence of the three stubs, measured — and the reason they exist.

    On this machine claude, codex, pi and node all resolve; on a CI runner none
    do. With the fixture whole, both hosts get the identical refusal row, and the
    Pi row in particular carries no filesystem path of the developer's.
    """
    recipes.clear_probe_cache()
    assert pi_adapter.pi_runtime() == ""
    for cli_id in ("claude", "codex", "pi"):
        state = recipes.recipe_state(cli_id)
        assert state["ok"] is False, f"{cli_id} answered off host state"
        assert state["method"] == recipes.METHOD_NONE
        assert state["limitations"], "a refusal the user cannot read"
    assert recipes.recipe_state("pi")["detail"] == "no Pi runtime resolved"
