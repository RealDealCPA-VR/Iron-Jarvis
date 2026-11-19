"""Fourth review, group 30 (v1.539.0): terminals stay on your account, and your
folder stays yours.

* TM1: a Codex terminal "with Iron Jarvis's tools" no longer gets a home of
  its own inside the project (``CODEX_HOME=<project>/.jarvis-codex``), which
  started it signed out and left a folder behind. It gets Codex's own per-run
  ``-c`` options (bare words, typed unquoted in every shell), offered only when
  the installed Codex's help names ``--config``. And no recipe can ever move a
  terminal off its account: ``TerminalManager._prepare_recipe`` drops an
  account's home variable from whatever a preparer answers (both shapes).
* D9 option B / MS4a: Claude Code's settings file for the terminal is written
  in Iron Jarvis's own folder (``<home>/terminals-mcp/<id>.json``), passed as
  ``--mcp-config <abs path> --strict-mcp-config``, under the server name
  ``iron-jarvis-terminal`` (never the connected app's ``iron-jarvis``). The
  project folder is untouched. Files of terminals that are gone are swept when
  the manager starts.
* C-TM-3: Pi gets its own ``-e <extension>`` from Iron Jarvis's folder.
* ``leftovers_in``: an older Iron Jarvis's ``.jarvis-codex`` folder or its
  ``iron-jarvis`` entry in a project's ``.mcp.json`` is named, never touched.

Every test goes through the real layer that decides: the real
``TerminalManager`` with the real ``platform.build_recipe_preparer`` and the
real recipes; only the CLI's help text (the probe chokepoint) is stood in.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path

import pytest

from iron_jarvis.browser.panetokens import MCP_TOKEN_ENV, MCP_URL_ENV
from iron_jarvis.hosted import jarvis_tools as JT
from iron_jarvis.iron_proxy import accounts as _acc
from iron_jarvis.mcpserver import apps as _apps
from iron_jarvis.platform import build_recipe_preparer
from iron_jarvis.terminals import recipes
from iron_jarvis.terminals.backend import FakeBackend
from iron_jarvis.terminals.manager import RecipeNotSetUp, TerminalManager
from iron_jarvis.terminals.pane_accounts import PaneAccounts

URL = "http://127.0.0.1:8799/mcp"  # never contacted; never 8787/8788/8765

CLAUDE_HELP = """\
Usage: claude [options] [prompt]
  --mcp-config <file>          Load MCP servers from a JSON file
  --strict-mcp-config          Only use servers from --mcp-config
  --disallowed-tools <names>   Comma-separated tools to disable
"""

#: The installed Codex's help, as measured: ``-c, --config <key=value>``.
CODEX_HELP = """\
Usage: codex [OPTIONS] [PROMPT]
  -c, --config <key=value>  Override a configuration value
  -m, --model <MODEL>       Model the agent should use
"""

PI_HELP = """\
Usage: pi [options]
  -e, --extension <path>  Load an extension for this run
  -h, --help              Show help
"""

#: Every account home a vendor CLI reads (``pane_accounts.SPECS``).
HOMES = {"CLAUDE_CONFIG_DIR", "CODEX_HOME", "GROK_HOME"}

#: A start-line word typed as it is in every shell (routes/terminals._PLAIN_ARG).
PLAIN = re.compile(r"^[A-Za-z0-9_./:=@+-]+$")


@pytest.fixture
def probe(monkeypatch):
    """The recipes' help/version probe answered by hand (no real CLI runs)."""
    answers: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(recipes, "_find", lambda command: f"/fake/bin/{command}")

    def _run(argv, **kw):
        return answers.get((Path(str(argv[0])).name, str(argv[1])), "")

    monkeypatch.setattr(recipes, "_run_probe", _run)
    recipes.clear_probe_cache()

    def _set(command: str, help_text: str, version: str = "1.0.0") -> None:
        answers[(command, "--version")] = version
        answers[(command, "--help")] = help_text
        recipes.clear_probe_cache()

    yield _set
    recipes.clear_probe_cache()


@pytest.fixture(autouse=True)
def _no_owner_claude_setup(monkeypatch):
    """The owner's Claude Code set-up copy is another layer (D4); it reads
    Iron Jarvis's config and must never reach this PC's real folders."""
    from iron_jarvis.iron_proxy import claude_setup

    monkeypatch.setattr(claude_setup, "apply_for_accounts", lambda env, accounts, **kw: env)


class _Rec(FakeBackend):
    def __init__(self) -> None:
        super().__init__()
        self.env: dict | None = None

    def start(self, argv, cwd, env, cols, rows) -> None:  # type: ignore[override]
        self.env = dict(env) if env is not None else None
        super().start(argv, cwd, env, cols, rows)


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "ij-home"
    home.mkdir()
    return home


def _manager(home: Path) -> TerminalManager:
    """The platform's own wiring: a state file in Iron Jarvis's home, the real
    preparer, the real recipes."""
    return TerminalManager(
        state_path=home / "terminals.json",
        mcp_url=URL,
        recipe_preparer=build_recipe_preparer(URL),
    )


def _account(provider: str, home: Path, pid: str = "p2") -> PaneAccounts:
    """A terminal on a named account whose home is ``home``."""
    spec = _acc.PROVIDERS[{"openai": "codex-cli", "anthropic": "claude-cli"}[provider]]
    lease = _acc.Lease(
        profile_id=pid, title="Work account", provider=provider,
        env_set={spec.home_var: str(home)}, ij_provider=spec.ij_provider, home_dir=str(home),
    )
    accounts = PaneAccounts()
    accounts.use_lease(provider, lease)
    return accounts


def _listing(folder: Path) -> list[str]:
    return sorted(str(p.relative_to(folder)) for p in folder.rglob("*"))


# --------------------------------------------------------------------------- #
# TM1 (b): no recipe can ever set an account home, in either preparer shape
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("shape", ["mapping", "result"])
@pytest.mark.parametrize("cli", ["claude", "codex", "pi"])
def test_a_recipe_can_never_set_an_account_home(tmp_path, caplog, shape, cli):
    account_home = tmp_path / "account-home"
    account_home.mkdir()
    planted = {
        "CODEX_HOME": str(tmp_path / "project" / ".jarvis-codex"),
        "claude_config_dir": str(tmp_path / "elsewhere"),
        "Grok_Home": str(tmp_path / "elsewhere"),
        "IRONJARVIS_KEEP": "1",
    }

    def preparer(**kw):
        if shape == "mapping":
            return dict(planted)
        return recipes.RecipeResult(ok=True, method=recipes.METHOD_HTTP, env=dict(planted))

    rec = _Rec()
    m = TerminalManager(mcp_url=URL, recipe_preparer=preparer)
    with caplog.at_level(logging.WARNING):
        m.create(
            cwd=str(tmp_path), backend=rec, env={"PATH": "/bin"},
            accounts=_account("openai", account_home), capabilities={"memory": True},
            recipe=cli,
        )
    env = rec.env or {}
    assert env.get("CODEX_HOME") == str(account_home), "a recipe moved the terminal off its account"
    assert not [k for k in env if k.upper() in {"CLAUDE_CONFIG_DIR", "GROK_HOME"}], env
    assert env["IRONJARVIS_KEEP"] == "1", "only the account homes are dropped"
    assert env[MCP_TOKEN_ENV], "the terminal still gets its key"
    assert any("account home" in r.getMessage() for r in caplog.records), "the drop is logged"
    m.kill_all()


def test_the_real_recipes_answer_no_account_home(probe, tmp_path):
    """Each real recipe's own answer holds no home variable at all."""
    probe("claude", CLAUDE_HELP)
    probe("codex", CODEX_HELP)
    probe("pi", PI_HELP)
    home = _home(tmp_path)
    for cli in ("claude", "codex", "pi"):
        result = recipes.recipe_for(cli).prepare(
            "term_x", "t", URL, {"memory": True}, cwd=str(tmp_path), home=str(home)
        )
        assert result.set_up, (cli, result.limitations)
        assert not [k for k in result.env if k.upper() in HOMES], (cli, result.env)


# --------------------------------------------------------------------------- #
# TM1 (a) + C-TM-2: Codex with tools keeps the account's home and writes nothing
# --------------------------------------------------------------------------- #
def test_codex_with_tools_keeps_the_accounts_home_and_writes_nothing(probe, tmp_path):
    probe("codex", CODEX_HELP)
    ij = _home(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    account_home = tmp_path / "account-home"
    account_home.mkdir()
    before_project, before_home = _listing(project), _listing(account_home)

    m = _manager(ij)
    rec = _Rec()
    s = m.create(
        cwd=str(project), backend=rec, accounts=_account("openai", account_home),
        capabilities={"memory": True}, recipe="codex", recipe_required=True,
    )
    env = rec.env or {}
    assert env["CODEX_HOME"] == str(account_home), "Codex did not start on its account"
    assert _listing(project) == before_project, "something was written in the project"
    assert _listing(account_home) == before_home, "something was written in the account's home"
    argv = s.recipe_outcome["argv_extra"]
    name = recipes.CODEX_SERVER_NAME
    assert argv == [
        "-c", f"mcp_servers.{name}.url={URL}",
        "-c", f"mcp_servers.{name}.bearer_token_env_var={MCP_TOKEN_ENV}",
    ]
    assert all(PLAIN.match(a) for a in argv), "a word that needs quoting in some shell"
    token = env[MCP_TOKEN_ENV]
    assert token and not any(token in a for a in argv), "the key reached the typed line"
    assert s.recipe_outcome["set_up"] is True
    m.kill_all()


def test_codex_on_this_pcs_login_has_no_home_at_all(probe, tmp_path, monkeypatch):
    probe("codex", CODEX_HELP)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "inherited"))
    accounts = PaneAccounts()
    accounts.use_default("openai")
    rec = _Rec()
    m = _manager(_home(tmp_path))
    m.create(cwd=str(tmp_path), backend=rec, accounts=accounts,
             capabilities={"memory": True}, recipe="codex", recipe_required=True)
    assert not [k for k in (rec.env or {}) if k.upper() == "CODEX_HOME"]
    m.kill_all()


def test_codex_is_offered_only_when_its_help_names_config(probe, tmp_path):
    probe("codex", "Usage: codex [OPTIONS]\n  -m, --model <MODEL>\n  --config-profile <P>\n")
    codex = recipes.CodexRecipe()
    assert codex.supports("") == ""
    assert codex.inspect().ok is False
    m = _manager(_home(tmp_path))
    with pytest.raises(RecipeNotSetUp) as refused:
        m.create(cwd=str(tmp_path), backend=_Rec(), capabilities={"memory": True},
                 recipe="codex", recipe_required=True)
    assert "--config" in refused.value.sentence
    assert len(m.pane_tokens) == 0, "a key was left behind for a terminal never opened"
    assert list(tmp_path.joinpath("project").glob("*")) == []


def test_the_codex_server_is_not_the_connected_apps():
    """The connected app writes ``iron-jarvis`` into the user's own Codex
    settings, which a with-tools Codex now loads; Codex merges ``-c`` into a
    table of the same name, so the terminal's must differ."""
    assert recipes.CODEX_SERVER_NAME == JT.SERVER_NAME
    assert recipes.CODEX_SERVER_NAME != _apps.SERVER_NAME


# --------------------------------------------------------------------------- #
# D9 option B + MS4a: Claude's file lives in Iron Jarvis's folder
# --------------------------------------------------------------------------- #
def test_claude_with_tools_writes_nothing_in_the_project(probe, tmp_path):
    probe("claude", CLAUDE_HELP)
    ij = _home(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    (project / "report.txt").write_text("a report", encoding="utf-8")
    (project / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"my-own": {"command": "mine"}}}), encoding="utf-8")
    before = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
    before_list = _listing(project)

    m = _manager(ij)
    rec = _Rec()
    s = m.create(cwd=str(project), backend=rec, capabilities={"memory": True},
                 recipe="claude", recipe_required=True)

    assert _listing(project) == before_list, "a file was added to the project"
    assert {p: p.read_bytes() for p in project.rglob("*") if p.is_file()} == before
    argv = s.recipe_outcome["argv_extra"]
    path = ij / "terminals-mcp" / f"{s.id}.json"
    assert argv[:3] == ["--mcp-config", str(path), "--strict-mcp-config"], argv
    assert Path(argv[1]).is_absolute()
    body = json.loads(path.read_text(encoding="utf-8"))
    assert list(body["mcpServers"]) == ["iron-jarvis-terminal"]
    assert recipes.SERVER_NAME == "iron-jarvis-terminal" != _apps.SERVER_NAME
    entry = body["mcpServers"]["iron-jarvis-terminal"]
    assert entry["url"] == URL
    assert entry["headers"]["Authorization"] == "Bearer ${" + MCP_TOKEN_ENV + "}"
    token = (rec.env or {})[MCP_TOKEN_ENV]
    assert token not in path.read_text(encoding="utf-8"), "the key was written to a file"
    assert (rec.env or {})[MCP_URL_ENV] == URL
    m.kill_all()


def test_claude_with_tools_starts_on_the_named_account(probe, tmp_path):
    """C-TM-2's recipe side: the account named at create is the home the
    with-tools terminal starts on."""
    probe("claude", CLAUDE_HELP)
    account_home = tmp_path / "claude-account"
    account_home.mkdir()
    rec = _Rec()
    m = _manager(_home(tmp_path))
    m.create(cwd=str(tmp_path), backend=rec, accounts=_account("anthropic", account_home),
             capabilities={"memory": True}, recipe="claude", recipe_required=True)
    assert (rec.env or {})["CLAUDE_CONFIG_DIR"] == str(account_home)
    m.kill_all()


def test_without_its_own_folder_claude_is_not_set_up(probe, tmp_path):
    probe("claude", CLAUDE_HELP)
    m = TerminalManager(mcp_url=URL, recipe_preparer=build_recipe_preparer(URL))
    with pytest.raises(RecipeNotSetUp):
        m.create(cwd=str(tmp_path), backend=_Rec(), capabilities={"memory": True},
                 recipe="claude", recipe_required=True)
    assert list(tmp_path.iterdir()) == []
    assert len(m.pane_tokens) == 0


# --------------------------------------------------------------------------- #
# C-TM-3: Pi's own -e, from Iron Jarvis's folder
# --------------------------------------------------------------------------- #
def test_pi_gets_its_own_extension_from_iron_jarviss_folder(probe, tmp_path):
    probe("pi", PI_HELP)
    ij = _home(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    assert recipes.PiRecipe().inspect().row()["ready"] is True, "Pi is ready the moment it starts"
    m = _manager(ij)
    rec = _Rec()
    s = m.create(cwd=str(project), backend=rec, capabilities={"memory": True},
                 recipe="pi", recipe_required=True)
    path = ij / "terminals-mcp" / f"{s.id}.js"
    assert s.recipe_outcome["argv_extra"] == ["-e", str(path)]
    assert list(project.iterdir()) == [], "a file was put in the project"
    source = path.read_text(encoding="utf-8")
    assert "registerMcpServer" in source and URL in source
    assert (rec.env or {})[MCP_TOKEN_ENV] not in source
    recipes.remove_terminal_files(ij, s.id)
    assert not path.exists()
    m.kill_all()


def test_a_pi_without_its_own_extension_leaves_the_project_empty(probe, tmp_path, monkeypatch):
    """D9 option B for a Pi whose help has no ``-e``: the adapter is the
    terminal's own file in Iron Jarvis's folder, and the hand-typed step names
    it by its absolute path."""
    probe("pi", "Usage: pi [options]\n  -h, --help   Show help\n")
    monkeypatch.setattr(recipes.PiRecipe, "runtime", lambda self: "/fake/bin/node")
    ij = _home(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    m = _manager(ij)
    rec = _Rec()
    s = m.create(cwd=str(project), backend=rec, capabilities={"memory": True}, recipe="pi")
    path = ij / "terminals-mcp" / f"{s.id}.mjs"
    assert list(project.iterdir()) == [], "the adapter was put in the project"
    assert path.is_file()
    assert (rec.env or {})[MCP_TOKEN_ENV] not in path.read_text(encoding="utf-8")
    assert (
        "Pi does not load the Iron Jarvis adapter on its own: after it opens, run "
        f'`/fake/bin/node "{path}"` in that terminal to give Pi the Jarvis tools.'
    ) in s.recipe_outcome["limitations"]
    recipes.remove_terminal_files(ij, s.id)
    assert not path.exists()
    m.kill_all()

    # With no Iron Jarvis folder: not set up, and still nothing in the project.
    bare = TerminalManager(mcp_url=URL, recipe_preparer=build_recipe_preparer(URL))
    with pytest.raises(RecipeNotSetUp):
        bare.create(cwd=str(project), backend=_Rec(), capabilities={"memory": True},
                    recipe="pi", recipe_required=True)
    assert list(project.iterdir()) == []


# --------------------------------------------------------------------------- #
# The sweep: files of terminals that are gone, and only those
# --------------------------------------------------------------------------- #
def test_the_sweep_removes_only_files_of_terminals_not_alive(tmp_path):
    home = _home(tmp_path)
    d = recipes.terminal_files_dir(home)
    assert d == home / "terminals-mcp"
    d.mkdir()
    for name in ("term_live.json", "term_live.js", "term_live.mjs", "term_gone.json",
                 "term_gone.js", "term_gone.mjs", "notes.txt", "term_x.json.tmp"):
        (d / name).write_text("{}", encoding="utf-8")
    assert recipes.sweep_terminal_files(home, {"term_live"}) == 3
    assert sorted(p.name for p in d.iterdir()) == [
        "notes.txt", "term_live.js", "term_live.json", "term_live.mjs", "term_x.json.tmp"]
    # never raises: no folder at all, or a home that is not there
    assert recipes.sweep_terminal_files(tmp_path / "missing", set()) == 0
    recipes.remove_terminal_files(tmp_path / "missing", "term_live")
    recipes.remove_terminal_files(home, "../ij-home/terminals")  # not an id: nothing
    assert (home / "terminals-mcp" / "term_live.json").exists()


def test_the_manager_sweeps_at_start_and_keeps_the_terminals_it_restores(tmp_path):
    home = _home(tmp_path)
    sp = home / "terminals.json"
    m1 = TerminalManager(state_path=sp)
    s = m1.create(cwd=str(tmp_path), backend=FakeBackend())
    m1.snapshot()
    d = recipes.terminal_files_dir(home)
    d.mkdir()
    (d / f"{s.id}.json").write_text("{}", encoding="utf-8")
    (d / "term_gone.json").write_text("{}", encoding="utf-8")
    old = time.time() - 60
    for p in d.iterdir():
        os.utime(p, (old, old))

    m2 = TerminalManager(state_path=sp)
    assert m2.rehydrate(backend=FakeBackend()) == 1
    m2._files_sweep.join(10)
    assert sorted(p.name for p in d.iterdir()) == [f"{s.id}.json"]
    m2.kill_all()


def test_the_sweep_never_takes_a_file_newer_than_the_start(tmp_path):
    """A with-tools terminal opened while the sweep runs keeps its file."""
    home = _home(tmp_path)
    d = recipes.terminal_files_dir(home)
    d.mkdir()
    (d / "term_new.json").write_text("{}", encoding="utf-8")
    start = time.time() - 60
    assert recipes.sweep_terminal_files(home, set(), older_than=start) == 0
    assert (d / "term_new.json").exists()


# --------------------------------------------------------------------------- #
# leftovers_in: named, never touched
# --------------------------------------------------------------------------- #
def _old_entry() -> dict:
    return {"type": "http", "url": "http://127.0.0.1:8787/mcp",
            "headers": {"Authorization": "Bearer ${IRONJARVIS_MCP_TOKEN}"}}


def test_leftovers_in_names_an_old_codex_folder_and_an_old_entry_and_nothing_else(tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    (folder / ".jarvis-codex").mkdir()
    (folder / ".jarvis-codex" / "config.toml").write_text("x = 1\n", encoding="utf-8")
    mcp = folder / ".mcp.json"
    mcp.write_text(json.dumps({"mcpServers": {
        "iron-jarvis": _old_entry(), "my-own": {"command": "mine"}}}), encoding="utf-8")
    before = {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()}

    found = recipes.leftovers_in(str(folder))
    assert found == [str(folder / ".jarvis-codex"), str(mcp)]
    assert all(Path(p).is_absolute() for p in found)
    assert {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()} == before

    # nothing else is named
    for case, body in {
        "own-stdio": {"mcpServers": {"iron-jarvis": {"command": "ironjarvis", "args": ["mcp-stdio"]}}},
        "others-only": {"mcpServers": {"my-own": {"command": "mine"}}},
        "not-an-object": [1, 2],
    }.items():
        other = tmp_path / case
        other.mkdir()
        (other / ".mcp.json").write_text(json.dumps(body), encoding="utf-8")
        assert recipes.leftovers_in(str(other)) == [], case
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / ".mcp.json").write_text("{ not json", encoding="utf-8")
    (broken / ".jarvis-codex").write_text("a file, not our folder", encoding="utf-8")
    assert recipes.leftovers_in(str(broken)) == []
    assert recipes.leftovers_in(str(tmp_path / "missing")) == []
    assert recipes.leftovers_in("") == []
