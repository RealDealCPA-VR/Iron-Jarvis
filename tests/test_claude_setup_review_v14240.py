"""v1.424.0 (review of audit D4): a second Claude account's setup, the defects
the independent review found — each pin red on the first cut.

(a) THE JUNCTION HAZARD. The first cut put DIRECTORY JUNCTIONS in Iron Jarvis's
folder (``<userData>/claude-setup/my/skills/<skill>`` → the owner's real
``~/.claude/skills/<skill>``; ``commands`` and ``agents`` whole). MEASURED on
this PC (2026-11-02, scratch junctions to scratch folders): Python 3.12's
``shutil.rmtree``, Windows PowerShell 5.1 / 7 ``Remove-Item -Recurse -Force``,
``rd /s /q``, Git's ``rm -rf`` and Node 20's ``fs.rmSync`` all remove a junction
and leave its target — but **NSIS 3.0.4.1 ``RMDir /r``** (what the installed
app's uninstaller runs over ``%APPDATA%\\Iron Jarvis`` when it is given
``--delete-app-data``) and **Electron 42's Node 24.17 ``fs.rmSync(…,
{recursive: true})``** (the runtime of the desktop shell, the dashboard server
and Iron-Proxy) both FOLLOWED the junctions and deleted the target's files. A
plugin manifest that names the owner's folders directly (absolute or ``../``
paths) is not loaded by Claude Code 2.1.316 (measured: nothing listed). So the
skills, commands and agents are COPIED into Iron Jarvis's folder instead,
refreshed when the source changes: nothing a recursive delete of that folder
can do reaches the owner's real files, and Claude Code on another account can
no longer WRITE into them either.

(b) PRIVACY: turning it ON from chat asks every time (``floor_when=(True,)``),
and the setting and the card say which data reaches the other account.

(c) Chat's own ``claude`` child kept a ``CLAUDE_CODE_PLUGIN_DIRS`` the user set
themselves: measured, a plugin hook named there RAN under chat's
``--setting-sources ""`` argv. The shared login guard drops it.

(d) A hook or MCP field holding a key ``detections.redact.mask`` misses (a
Google ``AIza…`` key given as ``--key``) was written to disk in plain text; the
managed-policy folder override and a large server-managed cache were misread.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.iron_proxy import accounts as _acc
from iron_jarvis.iron_proxy import claude_setup as CS
from iron_jarvis.iron_proxy import service as _svc
from iron_jarvis.terminals.manager import _with_pane_env
from iron_jarvis.terminals.pane_accounts import SPECS, PaneAccounts

GOOGLE_KEY = "AIzaSyD0123456789abcdefghijklmnopqrstuv"


def _w(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def owner(tmp_path, monkeypatch) -> Path:
    cfg = tmp_path / "owner-claude"
    _w(cfg / ".claude.json", json.dumps({"mcpServers": {
        "plain": {"type": "stdio", "command": "C:\\tools\\plain.exe", "args": ["serve"]},
    }}))
    _w(cfg / "settings.json", json.dumps({"hooks": {"SessionStart": [{"hooks": [
        {"type": "command", "command": "echo hello"},
    ]}]}}))
    _w(cfg / "skills" / "owner-skill" / "SKILL.md", "---\nname: owner-skill\ndescription: d\n---\nbody\n")
    _w(cfg / "skills" / "owner-skill" / "scripts" / "run.py", "print('hi')\n")
    _w(cfg / "commands" / "owner-cmd.md", "---\ndescription: c\n---\nrun\n")
    _w(cfg / "commands" / "team" / "nested-cmd.md", "---\ndescription: n\n---\nrun\n")
    _w(cfg / "agents" / "owner-agent.md", "---\nname: owner-agent\ndescription: a\n---\nyou\n")
    monkeypatch.setenv(CS.OWNER_DIR_ENV, str(cfg))
    monkeypatch.setattr(CS, "managed_dir", lambda: tmp_path / "no-managed")
    monkeypatch.setattr(CS, "_registry_forbids", lambda: False)
    return cfg


@pytest.fixture
def account(tmp_path) -> Path:
    home = tmp_path / "acct-b"
    home.mkdir()
    return home


@pytest.fixture
def current(monkeypatch, tmp_path):
    home = tmp_path / "ijhome"
    home.mkdir()
    cfg = SimpleNamespace(home=str(home), accounts_share_claude_setup=True)
    monkeypatch.setattr(_svc, "_CURRENT", SimpleNamespace(config=cfg))
    return cfg


def _accounts_for(home: Path) -> PaneAccounts:
    out = PaneAccounts()
    out.use_lease("anthropic", _acc.make_lease(SPECS["anthropic"], "acct-b", "Work", str(home), []))
    return out


def _owner_files(owner: Path) -> dict[str, bytes]:
    return {
        p.relative_to(owner).as_posix(): p.read_bytes()
        for p in owner.rglob("*")
        if p.is_file()
    }


def _walk_delete_following_links(folder: Path) -> None:
    """A recursive delete that FOLLOWS junctions — what NSIS 3.0.4.1's ``RMDir
    /r`` and Electron 42's Node 24 ``fs.rmSync(recursive)`` were measured doing.
    Python 3.12's ``os.walk`` descends into a junction, so every file it lists
    is deleted, wherever it really lives."""
    for dirpath, _dirs, files in os.walk(folder, topdown=False):
        for name in files:
            os.unlink(os.path.join(dirpath, name))


# --------------------------------------------------------------------------- #
# (a) no link into the owner's folders, ever
# --------------------------------------------------------------------------- #
def test_a_recursive_delete_that_follows_links_never_reaches_the_owners_files(owner, account, current):
    """MUTATION: link a unit instead of copying it -> the delete empties the
    owner's real folder -> red."""
    before = _owner_files(owner)
    env = _with_pane_env({"PATH": "x"}, {}, _accounts_for(account))
    assert CS.PLUGIN_DIRS_ENV in env
    folder = CS.folder_for(current.home)
    for dirpath, dirs, files in os.walk(folder):
        for name in dirs + files:
            assert not CS._is_link(Path(dirpath) / name), f"a link in Iron Jarvis's folder: {name}"
    _walk_delete_following_links(folder)
    assert _owner_files(owner) == before, "the owner's real files survive any recursive delete of the folder"


def test_the_copies_carry_the_owners_files_and_follow_their_changes(owner, account, current):
    """The plugin holds COPIES: the skill's own files (scripts too), the
    commands (nested folders too) and the agents. An edit, a new skill and a
    removed one follow at the next start.

    MUTATION: skip the refresh when the copy exists -> the edit never arrives -> red."""
    plugin = CS.folder_for(current.home) / CS.PLUGIN_DIR
    _with_pane_env({}, {}, _accounts_for(account))
    assert (plugin / "skills" / "owner-skill" / "SKILL.md").read_bytes() == \
        (owner / "skills" / "owner-skill" / "SKILL.md").read_bytes()
    assert (plugin / "skills" / "owner-skill" / "scripts" / "run.py").is_file()
    assert (plugin / "commands" / "owner-cmd.md").is_file()
    assert (plugin / "commands" / "team" / "nested-cmd.md").is_file()
    assert (plugin / "agents" / "owner-agent.md").is_file()
    # An edit (a different size, so it is seen whatever the clock does).
    _w(owner / "skills" / "owner-skill" / "SKILL.md", "---\nname: owner-skill\ndescription: d\n---\nbody, edited\n")
    _w(owner / "skills" / "second" / "SKILL.md", "---\nname: second\n---\nx\n")
    _with_pane_env({}, {}, _accounts_for(account))
    assert b"edited" in (plugin / "skills" / "owner-skill" / "SKILL.md").read_bytes()
    assert (plugin / "skills" / "second" / "SKILL.md").is_file()
    # The owner removes a skill and a command: their copies go.
    (owner / "skills" / "second" / "SKILL.md").unlink()
    (owner / "skills" / "second").rmdir()
    (owner / "commands" / "team" / "nested-cmd.md").unlink()
    _with_pane_env({}, {}, _accounts_for(account))
    assert not (plugin / "skills" / "second").exists()
    assert not (plugin / "commands" / "team").exists()
    assert (plugin / "commands" / "owner-cmd.md").is_file()


def test_a_skills_key_file_and_its_links_are_never_copied(owner, account, current, tmp_path):
    """A skill's ``.env``, a file that holds a key, and a link inside the skill
    stay where they are (the card says what was left out).

    MUTATION: drop the secret-file screen -> the .env is copied -> red."""
    skill = owner / "skills" / "owner-skill"
    _w(skill / ".env", "PIXIO_API_KEY=pk_live_0123456789abcdefABCDEF\n")
    _w(skill / "config.json", json.dumps({"key": GOOGLE_KEY}))
    elsewhere = tmp_path / "elsewhere"
    _w(elsewhere / "outside.txt", "outside")
    CS._make_link(elsewhere, skill / "linked")
    _with_pane_env({}, {}, _accounts_for(account))
    copy = CS.folder_for(current.home) / CS.PLUGIN_DIR / "skills" / "owner-skill"
    assert (copy / "SKILL.md").is_file()
    assert not (copy / ".env").exists() and not (copy / "config.json").exists()
    assert not (copy / "linked").exists(), "a link in the owner's skill is never followed"
    written = b"".join(p.read_bytes() for p in CS.folder_for(current.home).rglob("*") if p.is_file())
    assert GOOGLE_KEY.encode() not in written and b"pk_live_" not in written
    words = CS.card_line(current)["words"]
    assert "owner-skill" in words and "key" in words, words


def test_a_hand_edit_in_a_copy_is_never_overwritten_or_removed(owner, account, current):
    """MUTATION: overwrite the copy whatever its bytes -> the edit is lost -> red."""
    plugin = CS.folder_for(current.home) / CS.PLUGIN_DIR
    _with_pane_env({}, {}, _accounts_for(account))
    mine = b"my own words\n"
    (plugin / "commands" / "owner-cmd.md").write_bytes(mine)
    _w(owner / "commands" / "owner-cmd.md", "---\ndescription: c\n---\nrun, newer\n")
    _with_pane_env({}, {}, _accounts_for(account))
    assert (plugin / "commands" / "owner-cmd.md").read_bytes() == mine
    CS.retire(current.home)
    assert (plugin / "commands" / "owner-cmd.md").read_bytes() == mine, "off never removes a hand edit"
    assert not (plugin / "skills").exists(), "Iron Jarvis's own copies went"


def test_a_copy_edited_by_hand_with_nothing_newer_is_kept_when_turned_off(owner, account, current):
    """The owner's file did NOT change, so the next start reads nothing (the
    copy is still recorded as Iron Jarvis's): turning it off must still see
    the edit by its bytes.

    MUTATION: unlink every recorded file on retire, whatever its bytes -> red."""
    plugin = CS.folder_for(current.home) / CS.PLUGIN_DIR
    _with_pane_env({}, {}, _accounts_for(account))
    mine = b"my agent, my words\n"
    (plugin / "agents" / "owner-agent.md").write_bytes(mine)
    _with_pane_env({}, {}, _accounts_for(account))
    current.accounts_share_claude_setup = False
    _with_pane_env({}, {}, _accounts_for(account))
    assert (plugin / "agents" / "owner-agent.md").read_bytes() == mine
    assert not (plugin / "commands").exists() and not (plugin / "skills").exists()


def test_a_junction_the_first_cut_made_is_taken_away_without_touching_its_target(owner, account, current):
    """A dev or test home that ran the first cut holds Iron Jarvis's own
    junctions (recorded in ``state.json``): they are removed with ``os.rmdir``
    and replaced by copies; the owner's files stay.

    MUTATION: copy into the recorded junction -> the copy lands in the owner's
    folder -> red."""
    folder = CS.folder_for(current.home)
    plugin = folder / CS.PLUGIN_DIR
    before = _owner_files(owner)
    CS._make_link(owner / "commands", plugin / "commands")
    CS._make_link(owner / "skills" / "owner-skill", plugin / "skills" / "owner-skill")
    (folder / CS.STATE_FILE).write_text(json.dumps({"files": {}, "links": {
        "commands": CS._key(owner / "commands"),
        "skills/owner-skill": CS._key(owner / "skills" / "owner-skill"),
    }}), encoding="utf-8")
    _with_pane_env({}, {}, _accounts_for(account))
    assert not CS._is_link(plugin / "commands") and not CS._is_link(plugin / "skills" / "owner-skill")
    assert (plugin / "commands" / "owner-cmd.md").is_file()
    assert _owner_files(owner) == before


# --------------------------------------------------------------------------- #
# (b) privacy: ON from chat asks every time; the words name the data
# --------------------------------------------------------------------------- #
def test_turning_it_on_asks_every_time_and_off_applies_at_once():
    """MUTATION: drop ``floor_when=(True,)`` -> ON is "allow" -> red."""
    from iron_jarvis.settings import schema

    row = schema.get(CS.SETTING)
    assert row.tier_for(True) == "ask-floor"
    assert row.tier_for(False) == "allow"


@pytest.mark.asyncio
async def test_chat_cannot_turn_it_on_with_the_allow_tool(tmp_path):
    from iron_jarvis.daemon.app import create_app
    from tests.test_chat_approvals_v1187 import _drive_stream
    from tests.test_settings_chat_tools_v1321 import _body, _calls_stream

    app = create_app(str(tmp_path))
    cfg = app.state.platform.config
    cfg.accounts_share_claude_setup = False
    app.state.platform.router.stream = _calls_stream([("config_set", {"key": CS.SETTING, "value": True})])

    async def never(data):  # pragma: no cover
        raise AssertionError(data)

    frames = await _drive_stream(app, _body("turn on the use my claude code setup on my other accounts setting"), never)
    assert cfg.accounts_share_claude_setup is False
    finished = next(d for ev, d in frames if ev == "tool_call" and d.get("status") == "finished")
    assert finished["ok"] is False
    assert "config_change_protected" in (finished.get("output") or finished.get("error") or "")


def test_the_setting_and_the_card_say_which_data_reaches_the_other_account(owner, current):
    """MUTATION: the old words (no data named) -> red."""
    from iron_jarvis.settings import schema

    for words in (schema.get(CS.SETTING).help, CS.card_line(current)["words"]):
        low = words.lower()
        assert "what your mcp servers can read" in low, words
        assert "another organization" in low, words


# --------------------------------------------------------------------------- #
# (c) chat's own claude never takes a plugin folder from the environment
# --------------------------------------------------------------------------- #
def test_chats_claude_child_drops_a_user_set_plugin_dirs():
    """Measured: a plugin hook named in ``CLAUDE_CODE_PLUGIN_DIRS`` RAN under
    chat's ``--setting-sources ""`` argv.

    MUTATION: drop the pop -> the variable reaches chat's child -> red."""
    from iron_jarvis.providers import claude_models
    from iron_jarvis.providers.adapters.claude_native import transport as T

    base = {"PATH": "p", CS.PLUGIN_DIRS_ENV: "C:\\plugins\\mine", "claude_code_plugin_dirs": "C:\\x",
            CS.PLACEHOLDER_PREFIX + "ABCDEF0123456789": "owner-secret", "KEEP": "y"}
    for env in (claude_models.child_env(dict(base)), T.child_env(dict(base))):
        assert not any(k.upper() == CS.PLUGIN_DIRS_ENV for k in env), env
        assert not any(k.startswith(CS.PLACEHOLDER_PREFIX) for k in env), env
        assert env["KEEP"] == "y" and env["PATH"] == "p"


# --------------------------------------------------------------------------- #
# (d) no key the mask misses reaches disk; the policy is read right
# --------------------------------------------------------------------------- #
def test_a_hook_or_server_field_with_a_key_the_mask_misses_is_left_out(owner, account, current):
    """``detections.redact.mask`` does not know a Google key given as
    ``--key AIza…``; the night shift's ``secrets_in`` does.

    MUTATION: screen with ``mask`` alone -> the key is written to disk -> red."""
    settings = json.loads((owner / "settings.json").read_text(encoding="utf-8"))
    settings["hooks"]["SessionStart"][0]["hooks"].append(
        {"type": "command", "command": f"notify.exe --key {GOOGLE_KEY}"})
    _w(owner / "settings.json", json.dumps(settings))
    data = json.loads((owner / ".claude.json").read_text(encoding="utf-8"))
    data["mcpServers"]["keyed"] = {"type": "stdio", "command": f"C:\\t\\x.exe --key {GOOGLE_KEY}"}
    _w(owner / ".claude.json", json.dumps(data))
    os.utime(owner / ".claude.json", ns=(3, 3))
    os.utime(owner / "settings.json", ns=(3, 3))
    _with_pane_env({}, {}, _accounts_for(account))
    folder = CS.folder_for(current.home)
    written = b"".join(p.read_bytes() for p in folder.rglob("*") if p.is_file())
    assert written and GOOGLE_KEY.encode() not in written
    setup = CS.read_owner()
    assert "keyed" not in setup.servers and "plain" in setup.servers
    assert setup.hook_count == 1


def test_a_reference_that_also_carries_a_key_is_held_in_the_environment(owner, account, current):
    """A value holding a ``${VAR}`` reference was written to ``.mcp.json`` as it
    is whenever ``mask`` left it alone — even with a literal key beside the
    reference.

    MUTATION: keep a reference whenever ``mask`` leaves it unchanged -> the key
    is on disk -> red."""
    data = json.loads((owner / ".claude.json").read_text(encoding="utf-8"))
    data["mcpServers"]["mixed"] = {"type": "stdio", "command": r"C:\t\m.exe",
                                   "env": {"OPTS": "${HOME} --key " + GOOGLE_KEY, "PLAIN": "${GITHUB_TOKEN}"}}
    _w(owner / ".claude.json", json.dumps(data))
    os.utime(owner / ".claude.json", ns=(4, 4))
    env = _with_pane_env({}, {}, _accounts_for(account))
    plugin = CS.folder_for(current.home) / CS.PLUGIN_DIR
    mcp = json.loads((plugin / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert GOOGLE_KEY not in json.dumps(mcp)
    ref = mcp["mixed"]["env"]["OPTS"]
    assert ref.startswith("${" + CS.PLACEHOLDER_PREFIX) and env[ref[2:-1]] == "${HOME} --key " + GOOGLE_KEY
    assert mcp["mixed"]["env"]["PLAIN"] == "${GITHUB_TOKEN}", "a plain reference stays one"


def test_a_policy_folder_named_by_the_override_forbids(owner, account, current, tmp_path):
    """``CLAUDE_CODE_MANAGED_SETTINGS_PATH`` names the policy FOLDER (read from
    the binary: "this machine's policy folder (or the one
    CLAUDE_CODE_MANAGED_SETTINGS_PATH names)").

    MUTATION: read the override only as a file -> missed -> red."""
    policy = tmp_path / "policy"
    _w(policy / "managed-settings.json", json.dumps({"disableSideloadFlags": True}))
    assert CS.sideload_forbidden(str(account), {"CLAUDE_CODE_MANAGED_SETTINGS_PATH": str(policy)})
    drop = tmp_path / "policy2"
    _w(drop / "managed-settings.d" / "10-org.json", json.dumps({"disableSideloadFlags": True}))
    assert CS.sideload_forbidden(str(account), {"CLAUDE_CODE_MANAGED_SETTINGS_PATH": str(drop)})


def test_a_large_server_managed_cache_is_still_read(owner, account, current):
    """Claude Code reads ``remote-settings.json`` up to 8 MB (``xo=8388608`` in
    the binary); a cache over the first cut's 1 MB bound read as "not forbidding".

    MUTATION: a 1 MB bound -> missed -> red."""
    doc = {"disableSideloadFlags": True, "padding": "x" * (2 * 1024 * 1024)}
    (account / "remote-settings.json").write_text(json.dumps(doc), encoding="utf-8")
    assert CS.sideload_forbidden(str(account), {})
