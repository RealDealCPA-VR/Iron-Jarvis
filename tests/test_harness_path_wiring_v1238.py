"""The harness path is REACHABLE from the real platform (v1.238.0, plan 13.3).

Ship 4 built ``terminals/recipes.py``, a pane capability token, an outward
``/mcp`` server and a Launch row that says "Jarvis capabilities over HTTP" — and
wired none of it together. ``build_platform`` constructed the ``TerminalManager``
with no ``recipe_preparer`` and no ``mcp_url``, and built a SECOND
``McpSessionRegistry`` that the manager never saw. The measurable result on a
real install: a pane created with ``recipe="claude"`` got a credential, no
address, no ``.mcp.json``, and its MCP sessions outlived the pane by up to an
hour. Eighty-nine tests were green, because every one of them hand-built a
``TerminalManager`` with the arguments production does not pass.

So every pin in this file goes through ``build_platform`` — the ``platform``
fixture, the same object ``create_app()`` runs on. A test that constructed its
own manager here would be the exact shape that hid this.

Four claims, each one false before this change:

* a recipe pane's child environment carries BOTH ``IRONJARVIS_MCP_URL`` and
  ``IRONJARVIS_MCP_TOKEN``, and the URL is this daemon's own ``/mcp``;
* the recipe actually RAN — its ``.mcp.json`` is on disk in the pane's folder,
  naming the same URL and the same token;
* ``platform.mcp_sessions`` IS ``platform.terminals.mcp_sessions``, so closing a
  pane closes its MCP sessions and not only its token;
* a pane still being created keeps its brand-new token when another pane closes
  underneath it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from iron_jarvis.browser.panetokens import MCP_TOKEN_ENV, MCP_URL_ENV
from iron_jarvis.platform import DEFAULT_DAEMON_PORT, own_mcp_url
from iron_jarvis.terminals import recipes
from iron_jarvis.terminals.backend import FakeBackend

_FAKE_CLAUDE = "/usr/local/bin/claude"


class _RecordingBackend(FakeBackend):
    """Keeps the environment it was STARTED with — the only ordering that counts.

    Anything in ``.env`` was in the child's environment before the shell ran.
    Asserting a dict on the session instead is what let v1.217.0's pane identity
    reach no shell at all while its tests stayed green.
    """

    def __init__(self) -> None:
        super().__init__()
        self.env: dict | None = None

    def start(self, argv, cwd, env, cols, rows) -> None:  # type: ignore[override]
        self.env = dict(env) if env is not None else None
        super().start(argv, cwd, env, cols, rows)


@pytest.fixture
def installed_claude(monkeypatch):
    """A Claude Code build that advertises every option, deterministically.

    Both halves are stubbed. ``conftest._isolate_launch_recipe_probes`` stubs
    ``_run_probe`` only, so ``_find`` still hits this machine's real PATH — on a
    box where ``claude`` is installed the probe caches under a real path, and on
    CI under none. Pinning both makes the answer the same in both places.
    """
    monkeypatch.setattr(
        recipes, "_find", lambda command: _FAKE_CLAUDE if command == "claude" else ""
    )

    def probe(argv, *args, **kw):
        if argv[1] == "--version":
            return "1.2.3 (Claude Code)"
        return (
            "Usage: claude [options]\n"
            "  --mcp-config <file>\n"
            "  --strict-mcp-config\n"
            "  --disallowed-tools <names>\n"
        )

    monkeypatch.setattr(recipes, "_run_probe", probe)
    recipes.clear_probe_cache()
    yield
    recipes.clear_probe_cache()


def _create_claude_pane(platform, cwd: Path):
    cwd.mkdir(parents=True, exist_ok=True)
    backend = _RecordingBackend()
    session = platform.terminals.create(
        cwd=str(cwd),
        backend=backend,
        capabilities={"browser": True},
        recipe="claude",
    )
    return session, backend


def _token_of(store, pane_id: str) -> str:
    """A token the store still holds for ``pane_id``, else one it cannot hold."""
    for record in list(getattr(store, "_records", [])):
        if record.pane_id == pane_id:
            return record.token
    return "no-such-token"


# --------------------------------------------------------------------------- #
# The address
# --------------------------------------------------------------------------- #
def test_a_recipe_pane_from_the_real_platform_gets_an_address_and_a_credential(
    platform, tmp_path, installed_claude
):
    """The half that was missing: the token was minted and had nowhere to go."""
    session, backend = _create_claude_pane(platform, tmp_path / "pane")

    assert backend.env is not None
    url = backend.env.get(MCP_URL_ENV)
    token = backend.env.get(MCP_TOKEN_ENV)
    assert url, f"{MCP_URL_ENV} never reached the child environment"
    assert token, f"{MCP_TOKEN_ENV} never reached the child environment"
    assert url == own_mcp_url()
    assert url.endswith("/mcp")

    # And the credential is real against the store /mcp authorises against.
    grant = platform.pane_tokens.resolve(token)
    assert grant is not None
    assert grant.pane_id == session.id
    assert grant.allows("browser") is True


def test_the_recipe_actually_ran_and_wrote_the_panes_config(
    platform, tmp_path, installed_claude
):
    """``recipes.py`` was unreachable from the app: no preparer, so no config.

    The file is read back and matched against what the child actually holds. The
    header is `${IRONJARVIS_MCP_TOKEN}` rather than the token itself (the
    recipes lane's fix: a live credential in the user's project folder is a
    credential in their next commit), which makes the environment wiring
    LOAD-BEARING rather than convenient — a config that expands an unset
    variable authenticates as nothing at all. So the variable it names is
    asserted present in the child environment and resolving in the store.
    """
    session, backend = _create_claude_pane(platform, tmp_path / "pane")

    config = tmp_path / "pane" / ".mcp.json"
    assert config.is_file(), "no .mcp.json was written into the pane's folder"
    body = json.loads(config.read_text(encoding="utf-8"))
    entry = body["mcpServers"][recipes.SERVER_NAME]
    assert entry["url"] == backend.env[MCP_URL_ENV]

    header = entry["headers"]["Authorization"]
    assert header in (
        "Bearer ${%s}" % MCP_TOKEN_ENV,
        f"Bearer {backend.env[MCP_TOKEN_ENV]}",
    ), header
    if MCP_TOKEN_ENV in header:  # the indirect form: the variable must exist
        grant = platform.pane_tokens.resolve(backend.env[MCP_TOKEN_ENV])
        assert grant is not None and grant.pane_id == session.id


def test_a_pane_with_no_recipe_is_still_prepared_with_nothing(platform, tmp_path):
    """Wiring the seam must not hand a credential to every ordinary shell."""
    backend = _RecordingBackend()
    platform.terminals.create(
        cwd=str(tmp_path), backend=backend, capabilities={"browser": True}
    )

    assert MCP_TOKEN_ENV not in backend.env
    assert MCP_URL_ENV not in backend.env
    assert not (tmp_path / ".mcp.json").exists()
    assert len(platform.pane_tokens) == 0


def test_a_cli_with_no_recipe_of_its_own_still_gets_the_daemons_address(
    platform, tmp_path
):
    """Most of the Launch catalog has no recipe: nothing is written for it.

    This is the pin that carries `manager.mcp_url` on its own. The address
    reaches a RECIPE pane from two independent places — the manager exports it
    and the recipe's own `RecipeResult.env` repeats it — so dropping either one
    alone leaves the test above green. Here there is no recipe to fall back on,
    so the manager's export is the only source and the pin fails when it goes.
    """
    backend = _RecordingBackend()
    platform.terminals.create(
        cwd=str(tmp_path),
        backend=backend,
        capabilities={"browser": True},
        recipe="not-a-real-cli",
    )

    assert backend.env[MCP_TOKEN_ENV]
    assert backend.env[MCP_URL_ENV] == own_mcp_url()
    # (tmp_path also holds the platform fixture's own `.ironjarvis` home, so
    # this names the file rather than asserting an empty directory.)
    assert not (tmp_path / ".mcp.json").exists()


def test_the_url_names_this_daemons_own_port_and_never_leaves_the_box(monkeypatch):
    """``serve --port`` is invisible here; the supervisor's variable is not."""
    monkeypatch.delenv("IJ_DAEMON_PORT", raising=False)
    assert own_mcp_url() == f"http://127.0.0.1:{DEFAULT_DAEMON_PORT}/mcp"

    monkeypatch.setenv("IJ_DAEMON_PORT", "9999")
    assert own_mcp_url() == "http://127.0.0.1:9999/mcp"

    # Garbage is not interpolated into a URL a credential is posted to.
    monkeypatch.setenv("IJ_DAEMON_PORT", "8787 evil.example.com")
    assert own_mcp_url() == f"http://127.0.0.1:{DEFAULT_DAEMON_PORT}/mcp"


# --------------------------------------------------------------------------- #
# One store, not two
# --------------------------------------------------------------------------- #
def test_the_platform_and_the_manager_share_both_credential_stores(platform):
    """A second registry is a set of live rows nothing revokes.

    ``pane_tokens`` was already shared; ``mcp_sessions`` was built separately and
    the manager's stayed ``None``, so all three "CHOKEPOINT" blocks were no-ops
    in the shipped daemon while a unit test injected a registry production never
    injected.
    """
    assert platform.mcp_sessions is not None
    assert platform.terminals.pane_tokens is platform.pane_tokens
    assert platform.terminals.mcp_sessions is platform.mcp_sessions


def test_closing_a_pane_revokes_its_token_AND_its_mcp_sessions(
    platform, tmp_path, installed_claude
):
    """Driven through the real platform, which is where this was false."""
    session, backend = _create_claude_pane(platform, tmp_path / "pane")
    token = backend.env[MCP_TOKEN_ENV]
    mcp = platform.mcp_sessions.open(session.id)

    assert platform.pane_tokens.resolve(token) is not None
    assert platform.mcp_sessions.get(mcp.id) is not None

    assert platform.terminals.kill(session.id) is True

    assert platform.pane_tokens.resolve(token) is None
    assert platform.mcp_sessions.get(mcp.id) is None


def test_a_pane_that_dies_on_its_own_loses_its_mcp_session_too(
    platform, tmp_path, installed_claude
):
    """CHOKEPOINT 2: nobody called kill(), so nothing else would ever notice."""
    session, backend = _create_claude_pane(platform, tmp_path / "pane")
    token = backend.env[MCP_TOKEN_ENV]
    mcp = platform.mcp_sessions.open(session.id)

    session.kill()  # the shell exited; the manager was not told
    platform.terminals.purge_dead()

    assert platform.pane_tokens.resolve(token) is None
    assert platform.mcp_sessions.get(mcp.id) is None


# --------------------------------------------------------------------------- #
# The mint happens before the pane exists
# --------------------------------------------------------------------------- #
def test_a_pane_being_created_keeps_its_token_when_another_pane_closes(
    platform, tmp_path, installed_claude
):
    """The token is minted before the spawn; the session row lands after it.

    ``purge_dead`` swept by "every pane in ``_sessions``", so a close landing
    during a spawn — ordinary, since every /terminals handler is a sync ``def``
    on Starlette's threadpool and a real ConPTY spawn is the slowest thing in
    ``create`` — revoked the credential the child was about to be handed. The
    harness's first call then answered "the pane token expired when Iron Jarvis
    restarted", naming a restart that never happened, with relaunching the only
    remedy.

    Reproduced deterministically: the purge runs INSIDE the spawn.
    """
    manager = platform.terminals
    doomed, doomed_backend = _create_claude_pane(platform, tmp_path / "a")
    doomed_token = doomed_backend.env[MCP_TOKEN_ENV]
    doomed.kill()

    class _PurgingBackend(_RecordingBackend):
        def start(self, argv, cwd, env, cols, rows):  # type: ignore[override]
            manager.purge_dead()  # a sibling pane closes mid-spawn
            super().start(argv, cwd, env, cols, rows)

    backend = _PurgingBackend()
    (tmp_path / "b").mkdir()
    session = manager.create(
        cwd=str(tmp_path / "b"),
        backend=backend,
        capabilities={"browser": True},
        recipe="claude",
    )
    token = backend.env[MCP_TOKEN_ENV]

    grant = platform.pane_tokens.resolve(token)
    assert grant is not None, "the in-flight pane's token was swept mid-spawn"
    assert grant.pane_id == session.id
    assert grant.allows("browser") is True
    # And the pane that really did close still lost its credential.
    assert platform.pane_tokens.resolve(doomed_token) is None
    assert _token_of(platform.pane_tokens, doomed.id) == "no-such-token"
