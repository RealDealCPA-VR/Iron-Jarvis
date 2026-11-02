"""v1.424.0 (audit D4): a second Claude account starts with the OWNER's
Claude Code setup (``iron_proxy/claude_setup.py``).

Every test builds a SCRATCH owner (``IRONJARVIS_CLAUDE_OWNER_DIR``, the suite
default being an empty folder) and a SCRATCH account home; nothing here reads
or links to the developer's real ``~/.claude``. The pane path is driven
through the real app factory with the fake Iron-Proxy (``POST /terminals`` on
an account, the env the shell was STARTED with); Build › Code through the real
service and the recorded fake harness (the env the harness process got); chat
through ``claude_native.transport``'s own argv/env builders.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tarfile
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.iron_proxy import accounts as _acc
from iron_jarvis.iron_proxy import claude_setup as CS
from iron_jarvis.iron_proxy import service as _svc
from iron_jarvis.terminals.backend import FakeBackend
from iron_jarvis.terminals.manager import _with_pane_env
from iron_jarvis.terminals.pane_accounts import PaneAccounts, SPECS

FIXTURE = Path(__file__).parent / "fixtures" / "fake_iron_proxy_v1301.py"
sys.path.insert(0, str(FIXTURE.parent))
from fake_iron_proxy_v1301 import FEATURES_PICK_PROFILE, FakeIronProxy  # noqa: E402

#: The owner's secrets — none may appear in any file Iron Jarvis writes.
SECRETS = ("sk-owner-env-secret-0123456789", "Bearer owner-header-token-abcdef",
           "https://example.test/mcp?api_key=owner-url-secret-xyz", "--token=owner-arg-secret-777")


# --------------------------------------------------------------------------- #
# a scratch owner and account
# --------------------------------------------------------------------------- #
def _w(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _owner(base: Path) -> Path:
    """A scratch owner config folder (its ``.claude.json`` inside, the
    override's rule)."""
    cfg = base / "owner-claude"
    _w(cfg / ".claude.json", json.dumps({
        "mcpServers": {
            "spark": {"type": "stdio", "command": "C:\\tools\\spark.exe",
                      "args": ["serve", SECRETS[3]],
                      "env": {"SPARK_KEY": SECRETS[0], "FROM_ENV": "${GITHUB_TOKEN}"}},
            "remote": {"type": "http", "url": SECRETS[2],
                       "headers": {"Authorization": SECRETS[1]}},
            "helper": {"type": "http", "url": "https://h.test/mcp",
                       "headersHelper": "echo Authorization: Bearer sk-ant-api03-secretinhelper0000000000000"},
        },
        # account-private: never crosses
        "projects": {"C:/p": {"mcpServers": {"local-only": {"command": "x"}}}},
        "oauthAccount": {"emailAddress": "owner@example.test"},
    }))
    _w(cfg / ".credentials.json", '{"claudeAiOauth": {"accessToken": "sk-ant-oat01-NEVER-COPY"}}')
    _w(cfg / "settings.json", json.dumps({
        "hooks": {"SessionStart": [{"hooks": [
            {"type": "command", "command": "echo hello"},
            {"type": "command", "command": "curl -H 'Authorization: Bearer sk-live-hooksecret0000000000' x"},
        ]}]},
        "enabledPlugins": {"good@mkt": True, "off@mkt": False},
        "env": {"ANTHROPIC_API_KEY": "sk-ant-api03-settings-env-never"},
        "permissions": {"allow": ["Bash(echo:*)"]},
    }))
    _w(cfg / "skills" / "owner-skill" / "SKILL.md", "---\nname: owner-skill\ndescription: d\n---\nbody\n")
    _w(cfg / "skills" / "synced" / "bucket" / "s" / "SKILL.md", "---\nname: s\n---\nx\n")
    _w(cfg / "commands" / "owner-cmd.md", "---\ndescription: c\n---\nrun\n")
    _w(cfg / "agents" / "owner-agent.md", "---\nname: owner-agent\ndescription: a\n---\nyou\n")
    good = cfg / "plugins" / "cache" / "mkt" / "good" / "1.0.0"
    off = cfg / "plugins" / "cache" / "mkt" / "off" / "1.0.0"
    proj = cfg / "plugins" / "cache" / "mkt" / "proj" / "1.0.0"
    for p, name in ((good, "good"), (off, "off"), (proj, "proj")):
        _w(p / ".claude-plugin" / "plugin.json", json.dumps({"name": name}))
    _w(cfg / "plugins" / "installed_plugins.json", json.dumps({"version": 2, "plugins": {
        "good@mkt": [{"scope": "user", "installPath": str(good)}],
        "off@mkt": [{"scope": "user", "installPath": str(off)}],
        "proj@mkt": [{"scope": "project", "projectPath": "C:/p", "installPath": str(proj)}],
    }}))
    return cfg


@pytest.fixture
def owner(tmp_path, monkeypatch) -> Path:
    cfg = _owner(tmp_path)
    monkeypatch.setenv(CS.OWNER_DIR_ENV, str(cfg))
    # A managed policy on THIS PC must never decide a test.
    monkeypatch.setattr(CS, "managed_dir", lambda: tmp_path / "no-managed")
    monkeypatch.setattr(CS, "_registry_forbids", lambda: False)
    return cfg


@pytest.fixture
def account(tmp_path) -> Path:
    home = tmp_path / "acct-b"
    home.mkdir()
    return home


def _config(tmp_path, on: bool = True) -> SimpleNamespace:
    home = tmp_path / "ijhome"
    home.mkdir(exist_ok=True)
    return SimpleNamespace(home=str(home), accounts_share_claude_setup=on)


def _accounts_for(home: Path) -> PaneAccounts:
    out = PaneAccounts()
    out.use_lease("anthropic", _acc.make_lease(SPECS["anthropic"], "acct-b", "Work", str(home), []))
    return out


@pytest.fixture
def current(monkeypatch, tmp_path):
    """``service.current()`` answering a config (what ``_with_pane_env`` reads)."""
    cfg = _config(tmp_path)
    monkeypatch.setattr(_svc, "_CURRENT", SimpleNamespace(config=cfg))
    return cfg


def _files_under(folder: Path) -> list[Path]:
    out = []
    for dirpath, dirnames, filenames in os.walk(folder):
        # never walk INTO a junction (they lead to the owner's folders)
        dirnames[:] = [d for d in dirnames if not CS._is_link(Path(dirpath) / d)]
        out += [Path(dirpath) / f for f in filenames]
    return out


# --------------------------------------------------------------------------- #
# the environment a second account gets
# --------------------------------------------------------------------------- #
def test_a_second_account_gets_the_plugin_dirs_and_the_values_ride_the_environment(owner, account, current, tmp_path):
    """MUTATION: drop the ``claude_setup.apply_for_accounts`` call in
    ``_with_pane_env`` -> no CLAUDE_CODE_PLUGIN_DIRS -> red."""
    env = _with_pane_env({"PATH": "C:\\bin"}, {"IRONJARVIS_PANE_ID": "p1"}, _accounts_for(account))
    assert env["CLAUDE_CONFIG_DIR"] == str(account)
    dirs = env[CS.PLUGIN_DIRS_ENV].split(os.pathsep)
    folder = CS.folder_for(current.home)
    plugin = folder / CS.PLUGIN_DIR
    assert dirs[0] == str(plugin)
    good = str(owner / "plugins" / "cache" / "mkt" / "good" / "1.0.0")
    assert good in dirs, "an installed, enabled user-scope plugin is passed on"
    assert not any("off" in Path(d).parts or "proj" in Path(d).parts for d in dirs), \
        "a plugin turned off, or a project-scope one, never is"
    # .mcp.json holds references only; every reference has its value in env.
    mcp = json.loads((plugin / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert set(mcp) == {"spark", "remote"}, "a server with a secret in an uncopied field is left out"
    refs = [v for v in json.dumps(mcp).split('"') if v.startswith("${" + CS.PLACEHOLDER_PREFIX)]
    assert refs, "values became references"
    for ref in refs:
        assert env[ref[2:-1]], "every reference is filled from the child's environment"
    assert mcp["spark"]["env"]["FROM_ENV"] == "${GITHUB_TOKEN}", "the owner's own reference stays one"
    assert mcp["spark"]["command"] == "C:\\tools\\spark.exe"
    assert env[mcp["spark"]["env"]["SPARK_KEY"][2:-1]] == SECRETS[0]
    assert env[mcp["remote"]["headers"]["Authorization"][2:-1]] == SECRETS[1]
    assert env[mcp["remote"]["url"][2:-1]] == SECRETS[2]
    assert env[mcp["spark"]["args"][1][2:-1]] == SECRETS[3]
    # hooks: the plain one, never the one that looks like it holds a key
    hooks = json.loads((plugin / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    commands = [h["command"] for g in hooks["SessionStart"] for h in g["hooks"]]
    assert commands == ["echo hello"]
    # the owner's folders are LINKED, never copied: each skill on its own,
    # commands and agents whole
    assert not CS._is_link(plugin / "skills") and (plugin / "skills").is_dir()
    assert CS._key(CS._link_target(plugin / "skills" / "owner-skill")) == CS._key(owner / "skills" / "owner-skill")
    for kind in ("commands", "agents"):
        link = plugin / kind
        assert CS._is_link(link), kind
        assert CS._key(CS._link_target(link)) == CS._key(owner / kind)
    # the pane's identity still wins and nothing else moved
    assert env["IRONJARVIS_PANE_ID"] == "p1" and env["PATH"] == "C:\\bin"


def test_no_secret_and_nothing_account_private_is_written_anywhere(owner, account, current):
    """MUTATION: write a value into .mcp.json instead of a reference (``hold``
    returning ``value``) -> a secret on disk -> red."""
    _with_pane_env({}, {}, _accounts_for(account))
    folder = CS.folder_for(current.home)
    written = b"".join(p.read_bytes() for p in _files_under(folder))
    assert written, "the plugin folder was written"
    for secret in SECRETS + ("NEVER-COPY", "settings-env-never", "owner@example.test",
                             "local-only", "hooksecret", "secretinhelper"):
        assert secret.encode() not in written, secret
    assert "permissions" not in written.decode() and "Bash(echo" not in written.decode()
    # Nothing at all in the account's home.
    assert list(account.iterdir()) == []


def test_the_synced_and_trashed_skills_never_cross(owner, account, current):
    """claude.ai's synced skills are the owner's ACCOUNT's: each skill is linked
    on its own, so ``synced`` and ``.trash`` never reach the other account —
    Iron Jarvis's guarantee, not Claude Code's one-level read.

    MUTATION: drop ``synced`` from ``_NOT_SKILLS`` (or the dot-folder skip) ->
    linked -> red."""
    _w(owner / "skills" / "synced" / "SKILL.md", "---\nname: synced\n---\nx\n")
    _w(owner / "skills" / ".trash" / "SKILL.md", "---\nname: t\n---\nx\n")
    setup = CS.read_owner()
    assert setup.skill_count == 1 and setup.command_count == 1 and setup.agent_count == 1
    _with_pane_env({}, {}, _accounts_for(account))
    linked = sorted(p.name for p in (CS.folder_for(current.home) / CS.PLUGIN_DIR / "skills").iterdir())
    assert linked == ["owner-skill"]


def test_a_skill_the_owner_adds_or_removes_follows_at_the_next_start(owner, account, current):
    """MUTATION: drop the stale-link pass in ``_sync`` -> the removed skill's
    link stays -> red."""
    skills = CS.folder_for(current.home) / CS.PLUGIN_DIR / "skills"
    _with_pane_env({}, {}, _accounts_for(account))
    _w(owner / "skills" / "second" / "SKILL.md", "---\nname: second\n---\nx\n")
    _with_pane_env({}, {}, _accounts_for(account))
    assert sorted(p.name for p in skills.iterdir()) == ["owner-skill", "second"]
    gone = owner / "skills" / "second"
    (gone / "SKILL.md").unlink()
    gone.rmdir()
    _with_pane_env({}, {}, _accounts_for(account))
    assert sorted(p.name for p in skills.iterdir()) == ["owner-skill"]
    assert (owner / "skills" / "owner-skill" / "SKILL.md").is_file()


def test_the_default_account_and_the_owners_own_folder_are_unchanged(owner, current, tmp_path, monkeypatch):
    """An ADOPTED account (``~/.claude``) sets no home variable; an account whose
    home IS the owner's folder reads it already — neither gets anything."""
    profile = tmp_path / "profile"
    (profile / ".claude").mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(profile))
    monkeypatch.setenv("HOME", str(profile))
    adopted = PaneAccounts()
    adopted.use_lease("anthropic", _acc.make_lease(SPECS["anthropic"], "me", "Me", str(profile / ".claude"), []))
    base = {"PATH": "x", "CLAUDE_CONFIG_DIR": "C:\\inherited"}
    want = _with_pane_env(dict(base), {}, None)
    want.pop("CLAUDE_CONFIG_DIR")
    assert _with_pane_env(dict(base), {}, adopted) == want
    same = PaneAccounts()
    same.use_lease("anthropic", _acc.make_lease(SPECS["anthropic"], "o", "Owner", str(owner), []))
    env = _with_pane_env({"PATH": "x"}, {}, same)
    assert CS.PLUGIN_DIRS_ENV not in env and not any(k.startswith(CS.PLACEHOLDER_PREFIX) for k in env)


def test_off_is_byte_identical_and_takes_the_folder_away(owner, account, current, tmp_path):
    """MUTATION: ignore the setting (``is_on`` -> True) -> the variable is set
    with the setting off -> red."""
    accounts = _accounts_for(account)
    base = {"PATH": "x", "OTHER": "y"}
    _with_pane_env(dict(base), {"IRONJARVIS_PANE_ID": "p"}, accounts)  # on: folder written
    folder = CS.folder_for(current.home)
    plugin = folder / CS.PLUGIN_DIR
    assert (plugin / ".mcp.json").is_file()
    current.accounts_share_claude_setup = False
    got = _with_pane_env(dict(base), {"IRONJARVIS_PANE_ID": "p"}, accounts)
    plain = _with_pane_env(dict(base), {"IRONJARVIS_PANE_ID": "p"}, None)
    plain.update({"CLAUDE_CONFIG_DIR": str(account)})
    assert got == plain, "off: exactly the account's environment, nothing added"
    # Iron Jarvis's files and links are gone; the OWNER's folders are intact.
    assert not (plugin / ".mcp.json").exists() and not (plugin / "skills").exists()
    assert (owner / "skills" / "owner-skill" / "SKILL.md").is_file()
    assert (owner / "commands" / "owner-cmd.md").is_file() and (owner / "agents" / "owner-agent.md").is_file()


def test_retiring_never_deletes_through_a_link(owner, account, current):
    """MUTATION: ``_drop_link`` -> ``shutil.rmtree`` (or a delete that follows
    the junction) -> the owner's skill is gone -> red."""
    _with_pane_env({}, {}, _accounts_for(account))
    CS.retire(current.home)
    for kind, name in (("skills", "owner-skill/SKILL.md"), ("commands", "owner-cmd.md"), ("agents", "owner-agent.md")):
        assert (owner / kind / name).is_file(), kind


def test_a_hand_edit_is_never_overwritten_and_the_card_says_so(owner, account, current):
    """MUTATION: drop the hand-edit check in ``_sync`` -> the user's bytes are
    replaced -> red."""
    _with_pane_env({}, {}, _accounts_for(account))
    plugin = CS.folder_for(current.home) / CS.PLUGIN_DIR
    mine = b'{"mcpServers": {"hand": {"command": "mine"}}}'
    (plugin / ".mcp.json").write_bytes(mine)
    _with_pane_env({}, {}, _accounts_for(account))
    assert (plugin / ".mcp.json").read_bytes() == mine
    current.accounts_share_claude_setup = False
    _with_pane_env({}, {}, _accounts_for(account))
    assert (plugin / ".mcp.json").read_bytes() == mine, "off never removes a hand edit either"
    current.accounts_share_claude_setup = True
    line = CS.card_line(current)
    assert "changed by hand" in line["words"], line


def test_a_real_folder_or_a_foreign_link_in_its_place_is_left_alone(owner, account, current, tmp_path):
    """MUTATION: drop the "a link Iron Jarvis did not make" check -> the foreign
    link is replaced -> red."""
    plugin = CS.folder_for(current.home) / CS.PLUGIN_DIR
    (plugin / "commands").mkdir(parents=True)
    (plugin / "commands" / "keep.txt").write_text("mine", encoding="utf-8")
    (plugin / "skills").mkdir(parents=True)
    (plugin / "skills" / "keep.txt").write_text("mine too", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    CS._make_link(elsewhere, plugin / "agents")
    _with_pane_env({}, {}, _accounts_for(account))
    assert (plugin / "commands" / "keep.txt").read_text(encoding="utf-8") == "mine"
    assert not CS._is_link(plugin / "commands")
    assert CS._key(CS._link_target(plugin / "agents")) == CS._key(elsewhere)
    assert list(elsewhere.iterdir()) == [], "nothing was written into the foreign link's folder"
    CS.retire(current.home)
    assert (plugin / "commands" / "keep.txt").is_file() and CS._is_link(plugin / "agents")
    assert (plugin / "skills" / "keep.txt").is_file(), "a file put in the skills folder keeps its folder"
    assert not (plugin / "skills" / "owner-skill").exists(), "Iron Jarvis's own link went"


def test_a_foreign_link_as_the_skills_folder_never_receives_a_skill(owner, account, current, tmp_path):
    """MUTATION: drop the ``skills_blocked`` check -> a skill's link is made
    INSIDE the foreign link's target -> red."""
    plugin = CS.folder_for(current.home) / CS.PLUGIN_DIR
    target = tmp_path / "someone-elses"
    target.mkdir()
    CS._make_link(target, plugin / "skills")
    _with_pane_env({}, {}, _accounts_for(account))
    assert list(target.iterdir()) == []
    assert CS._is_link(plugin / "skills")
    CS.retire(current.home)
    assert CS._is_link(plugin / "skills") and target.is_dir()
    assert "Skills in Iron Jarvis's folder is a link" in CS.card_line(current)["words"]


def test_a_reference_name_changes_with_the_servers_definition(owner, account, current):
    """A terminal started before the owner changed a server must never fill the
    NEW definition with an OLD value: the names are a hash of the whole
    definition (measured: a missing variable stays literal).

    MUTATION: key the names on the server's name only -> the same name for the
    new url -> red."""
    first = CS.read_owner().servers["remote"]["headers"]["Authorization"]
    data = json.loads((owner / ".claude.json").read_text(encoding="utf-8"))
    data["mcpServers"]["remote"]["url"] = "https://another.test/mcp"
    (owner / ".claude.json").write_text(json.dumps(data), encoding="utf-8")
    os.utime(owner / ".claude.json", ns=(1, 1))  # a new mtime for the cache
    second = CS.read_owner().servers["remote"]["headers"]["Authorization"]
    assert first != second


def test_an_organization_that_forbids_added_plugins_gets_nothing(owner, account, current):
    """With ``disableSideloadFlags`` Claude Code would refuse to START while the
    variable is set (read from the binary): the variable is never set.

    MUTATION: skip ``sideload_forbidden`` -> set -> red."""
    (account / "remote-settings.json").write_text(json.dumps({"disableSideloadFlags": True}), encoding="utf-8")
    env = _with_pane_env({"PATH": "x"}, {}, _accounts_for(account))
    assert CS.PLUGIN_DIRS_ENV not in env and not any(k.startswith(CS.PLACEHOLDER_PREFIX) for k in env)


def test_the_users_own_plugin_dirs_are_kept(owner, account, current, tmp_path):
    mine = str(tmp_path / "my-own-plugin")
    env = _with_pane_env({CS.PLUGIN_DIRS_ENV: mine}, {}, _accounts_for(account))
    dirs = env[CS.PLUGIN_DIRS_ENV].split(os.pathsep)
    assert dirs[0] == str(CS.folder_for(current.home) / CS.PLUGIN_DIR) and mine in dirs


def test_an_empty_owner_setup_adds_nothing(account, current):
    """The suite default (an empty owner folder): byte-identical."""
    accounts = _accounts_for(account)
    got = _with_pane_env({"PATH": "x"}, {}, accounts)
    assert CS.PLUGIN_DIRS_ENV not in got and not CS.folder_for(current.home).exists()


def test_it_never_raises_into_a_terminal_start(owner, account, current, monkeypatch):
    def boom(*_a, **_k):
        raise OSError("disk gone")

    monkeypatch.setattr(CS, "_sync", boom)
    env = _with_pane_env({"PATH": "x"}, {}, _accounts_for(account))
    assert env["CLAUDE_CONFIG_DIR"] == str(account) and CS.PLUGIN_DIRS_ENV not in env


# --------------------------------------------------------------------------- #
# the three places Claude Code runs on an account
# --------------------------------------------------------------------------- #
class _RecordingBackend(FakeBackend):
    instances: list["_RecordingBackend"] = []
    lock = threading.Lock()

    def __init__(self) -> None:
        super().__init__()
        self.env: dict | None = None
        with _RecordingBackend.lock:
            _RecordingBackend.instances.append(self)

    def start(self, argv, cwd, env, cols, rows) -> None:  # type: ignore[override]
        self.env = dict(env) if env is not None else None
        super().start(argv, cwd, env, cols, rows)


@pytest.fixture
def pane_world(tmp_path, monkeypatch):
    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    monkeypatch.setenv("IRONJARVIS_IRON_PROXY_BUNDLE", str(tmp_path / "no-bundle.mjs"))
    monkeypatch.delenv("IRONJARVIS_IRON_PROXY_NODE", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    _RecordingBackend.instances = []
    monkeypatch.setattr("iron_jarvis.terminals.session.default_backend", _RecordingBackend)

    async def _no_watch(*_a, **_k):
        await asyncio.Event().wait()

    monkeypatch.setattr("iron_jarvis.iron_proxy.service.watch", _no_watch)
    fake = FakeIronProxy(tmp_path / "ipd", features=list(FEATURES_PICK_PROFILE)).start()
    proj = tmp_path / "proj"
    proj.mkdir()
    from iron_jarvis.daemon.app import create_app

    with TestClient(create_app(str(proj))) as c:
        yield c, fake
    fake.stop()


def _pane_env(c, fake, account_id) -> dict:
    r = c.post("/terminals", json={"accounts": {"anthropic": account_id}})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    found = [b for b in _RecordingBackend.instances if b.env and b.env.get("IRONJARVIS_PANE_ID") == pid]
    assert found
    return found[-1].env


def test_a_build_terminal_on_a_second_account_starts_with_the_owners_setup(owner, pane_world):
    """The real route: an explicit account, the shell's environment."""
    c, fake = pane_world
    assert c.post("/iron-proxy/enable").json()["status"]["running"] is True
    work = fake.add_profile("anthropic", "Work Max")
    env = _pane_env(c, fake, work["id"])
    assert env["CLAUDE_CONFIG_DIR"] == work["cli"]["home"]
    plugin = Path(env[CS.PLUGIN_DIRS_ENV].split(os.pathsep)[0])
    assert plugin.name == CS.PLUGIN_DIR and (plugin / ".mcp.json").is_file()
    assert any(k.startswith(CS.PLACEHOLDER_PREFIX) for k in env)
    # The card's one line for the Claude group.
    view = c.get("/iron-proxy?discover=0").json()
    assert view["claude_setup"]["on"] is True
    assert "MCP servers, skills and commands" in view["claude_setup"]["words"]
    assert "/my:" in view["claude_setup"]["words"]


def test_a_build_terminal_with_the_setting_off_is_todays(owner, pane_world):
    c, fake = pane_world
    assert c.post("/iron-proxy/enable").json()["status"]["running"] is True
    r = c.put("/settings/values", json={"values": {CS.SETTING: False}})
    assert r.status_code == 200, r.text
    work = fake.add_profile("anthropic", "Work Max")
    env = _pane_env(c, fake, work["id"])
    assert CS.PLUGIN_DIRS_ENV not in env and not any(k.startswith(CS.PLACEHOLDER_PREFIX) for k in env)
    view = c.get("/iron-proxy?discover=0").json()
    assert view["claude_setup"]["on"] is False


def test_the_card_line_is_absent_without_a_second_claude_account(owner, pane_world):
    c, fake = pane_world
    assert c.post("/iron-proxy/enable").json()["status"]["running"] is True
    fake.add_profile("openai", "Codex")
    assert "claude_setup" not in c.get("/iron-proxy?discover=0").json()


async def test_a_build_code_start_on_a_second_account_gets_it_too(owner, tmp_path, monkeypatch):
    """The real service, the recorded fake harness: the env the PROCESS got.

    MUTATION: drop the hop/build in ``_start_process`` (``env = {}``-style) or
    the ``_with_pane_env`` call -> the harness never sees the variable -> red."""
    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.hosted import store

    from ._build_app_helpers_v1409 import client, starts, stop_all, use_fake

    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_modes", log)
    work = tmp_path / "work"
    work.mkdir()
    acc_home = tmp_path / "acc-b"
    acc_home.mkdir()
    app = create_app(str(tmp_path / "root"))
    rec = {"id": "p1", "title": "Work", "source": "iron-proxy", "home": str(acc_home), "unset": []}
    row = store.create(app.state.platform.engine, harness="claude-code", folder=str(work), account="p1",
                       account_json=json.dumps(rec))
    plain = store.create(app.state.platform.engine, harness="claude-code", folder=str(work))
    async with client(app) as c:
        for sid, turn in ((row.id, "t1"), (plain.id, "t2")):
            r = await c.post(f"/build/app/sessions/{sid}/turn", json={"text": "ready?", "turn_id": turn})
            assert r.status_code == 200, r.text
    env1, env2 = starts(log)[0]["env"], starts(log)[1]["env"]
    assert env1["CLAUDE_CONFIG_DIR"] == str(acc_home)
    plugin = Path(env1[CS.PLUGIN_DIRS_ENV].split(os.pathsep)[0])
    assert plugin == CS.folder_for(app.state.platform.config.home) / CS.PLUGIN_DIR
    assert any(k.startswith(CS.PLACEHOLDER_PREFIX) for k in env1)
    # This PC's own login: nothing added.
    assert CS.PLUGIN_DIRS_ENV not in env2 and not any(k.startswith(CS.PLACEHOLDER_PREFIX) for k in env2)
    await stop_all(app)


def test_chats_claude_never_gets_it_on_any_account(owner, account, current, tmp_path):
    """Chat's Claude Code loads none of the user's setup on ANY account
    (``--setting-sources ""``, ``--strict-mcp-config``,
    ``--disable-slash-commands``, ``--tools ""``): its argv and env are
    unchanged with the feature on."""
    from iron_jarvis.providers.adapters.claude_native import transport as T

    call = T.NativeCall(
        exe="claude", model_arg="", system="", frames=[], manifest=[], body="", names=None,
        account_env={"CLAUDE_CONFIG_DIR": str(account)}, account_unset=(),
    )
    argv = T.build_argv(call, tmp_path)
    for flag in ("--setting-sources", "--strict-mcp-config", "--disable-slash-commands"):
        assert flag in argv
    env = T.with_account({"PATH": "x"}, call)
    assert CS.PLUGIN_DIRS_ENV not in env and env["CLAUDE_CONFIG_DIR"] == str(account)
    assert not CS.folder_for(current.home).exists(), "the chat path never builds the folder"


# --------------------------------------------------------------------------- #
# the setting, the backup, the storage report
# --------------------------------------------------------------------------- #
def test_the_setting_is_a_schema_row_default_on_allow_tier(tmp_path):
    from iron_jarvis.core.config import Config
    from iron_jarvis.settings import schema

    row = schema.get(CS.SETTING)
    assert row is not None and row.type == "bool" and row.tier == "allow" and row.group == "connections"
    assert Config(project_root=tmp_path, home=tmp_path).accounts_share_claude_setup is True


def test_a_backup_never_follows_the_junctions_into_the_owners_folders(owner, account, current):
    """``rglob`` follows a junction: the folder is matched on the path AS WALKED.

    MUTATION: drop the ``claude_setup_dir`` skip in ``create_backup`` -> the
    owner's skill is archived -> red."""
    from iron_jarvis.maintenance import create_backup

    _with_pane_env({}, {}, _accounts_for(account))
    home = Path(current.home)
    (home / "config.toml").write_text("x = 1\n", encoding="utf-8")
    out, _n = create_backup(home, home.parent / "b.tar.gz")
    with tarfile.open(out) as tar:
        names = tar.getnames()
    assert any(n.endswith("config.toml") for n in names)
    assert not any(CS.FOLDER in n for n in names), names


def test_the_storage_report_never_counts_the_owners_folders(owner, account, current):
    from iron_jarvis.maintenance import storage_report

    home = Path(current.home)
    before = storage_report(home)["total_bytes"]
    big = owner / "skills" / "owner-skill" / "big.bin"
    big.write_bytes(b"x" * 500_000)
    _with_pane_env({}, {}, _accounts_for(account))
    after = storage_report(home)["total_bytes"]
    assert after - before < 100_000, (before, after)


# --------------------------------------------------------------------------- #
# the real CLI (opt-in: IJ_TEST_REAL_CLAUDE=1 and `claude` on PATH; no model call)
# --------------------------------------------------------------------------- #
@pytest.mark.timeout(180)
@pytest.mark.skipif(not os.environ.get("IJ_TEST_REAL_CLAUDE"), reason="opt-in: IJ_TEST_REAL_CLAUDE=1")
def test_the_real_claude_lists_the_owners_setup_on_a_second_account(tmp_path, monkeypatch):
    import shutil
    import subprocess
    import time

    exe = shutil.which("claude")
    if not exe:
        pytest.skip("claude is not installed")
    profile = tmp_path / "profile"
    cfg = profile / ".claude"
    cfg.mkdir(parents=True)
    echo = tmp_path / "echo_mcp.py"
    got = tmp_path / "server-got.txt"
    echo.write_text(
        "import json,os,sys\n"
        "open(sys.argv[1],'w').write(os.environ.get('K','<unset>'))\n"
        "for line in sys.stdin:\n"
        "    m=json.loads(line)\n"
        "    if 'id' not in m: continue\n"
        "    r={'protocolVersion':m.get('params',{}).get('protocolVersion','2025-06-18'),'capabilities':{'tools':{}},"
        "'serverInfo':{'name':'e','version':'1'}} if m.get('method')=='initialize' else ({'tools':[]} if m.get('method')=='tools/list' else {})\n"
        "    print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':r}),flush=True)\n",
        encoding="utf-8",
    )
    _w(profile / ".claude.json", json.dumps({"mcpServers": {"owner-mcp": {
        "type": "stdio", "command": sys.executable, "args": [str(echo), str(got)], "env": {"K": "v-secret"}}}}))
    _w(cfg / "skills" / "owner-skill" / "SKILL.md", "---\nname: owner-skill\ndescription: d\n---\nb\n")
    _w(cfg / "skills" / "synced" / "org_acct" / "synced-skill" / "SKILL.md",
       "---\nname: synced-skill\ndescription: s\n---\nb\n")
    _w(cfg / "commands" / "owner-cmd.md", "---\ndescription: c\n---\nrun\n")
    _w(cfg / "agents" / "owner-agent.md", "---\nname: owner-agent\ndescription: a\n---\nyou\n")
    for k in ("USERPROFILE", "HOME"):
        monkeypatch.setenv(k, str(profile))
    monkeypatch.delenv(CS.OWNER_DIR_ENV, raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(CS, "managed_dir", lambda: tmp_path / "no-managed")
    acct = tmp_path / "acct"
    acct.mkdir()
    cfgobj = _config(tmp_path)
    base = {k: v for k, v in os.environ.items() if not k.upper().startswith(("ANTHROPIC_", "CLAUDE"))}
    base.update({"APPDATA": str(profile / "AppData"), "LOCALAPPDATA": str(profile / "Local"),
                 "DISABLE_AUTOUPDATER": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"})
    accounts = _accounts_for(acct)
    accounts.apply(base)
    env = CS.apply_for_accounts(base, accounts, config=cfgobj)
    assert CS.PLUGIN_DIRS_ENV in env
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    proc = subprocess.Popen(
        [exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=str(cwd),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    lines: list[dict] = []

    def read():
        for raw in proc.stdout:
            try:
                lines.append(json.loads(raw))
            except ValueError:
                pass

    threading.Thread(target=read, daemon=True).start()

    def ask(rid, subtype, wait=40.0):
        proc.stdin.write((json.dumps({"type": "control_request", "request_id": rid,
                                      "request": {"subtype": subtype}}) + "\n").encode())
        proc.stdin.flush()
        end = time.time() + wait
        while time.time() < end:
            for item in lines:
                resp = item.get("response") or {}
                if item.get("type") == "control_response" and resp.get("request_id") == rid:
                    return resp.get("response") or {}
            time.sleep(0.2)
        return {}

    try:
        init = ask("i", "initialize")
        time.sleep(4)
        status = ask("m", "mcp_status")
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    names = {c.get("name") for c in init.get("commands", [])}
    agents = {a.get("name") if isinstance(a, dict) else a for a in init.get("agents", [])}
    servers = {s.get("name"): s.get("status") for s in status.get("mcpServers", [])}
    assert {"my:owner-cmd", "my:owner-skill"} <= names, sorted(names)
    assert not any("synced" in str(n) for n in names), "the owner's account's synced skills never cross"
    assert "my:owner-agent" in agents, agents
    assert servers.get("plugin:my:owner-mcp") == "connected", servers
    plugin = CS.folder_for(cfgobj.home) / CS.PLUGIN_DIR
    assert b"v-secret" not in (plugin / ".mcp.json").read_bytes()
    # The value reached the server through the environment reference.
    assert got.read_text() == "v-secret"


def test_a_value_windows_cannot_pass_on_leaves_its_server_out(owner, account, current):
    """An environment value cannot hold a NUL (the terminal would not start):
    that server is left out and said so; the terminal starts.

    MUTATION: drop the NUL check -> the value reaches the environment -> red."""
    data = json.loads((owner / ".claude.json").read_text(encoding="utf-8"))
    data["mcpServers"]["spark"]["env"]["SPARK_KEY"] = "a\x00b"
    (owner / ".claude.json").write_text(json.dumps(data), encoding="utf-8")
    os.utime(owner / ".claude.json", ns=(2, 2))
    setup = CS.read_owner()
    assert "spark" not in setup.servers and any("spark" in w for w in setup.left_out)
    env = _with_pane_env({"PATH": "x"}, {}, _accounts_for(account))
    assert not any("\x00" in v for v in env.values())
