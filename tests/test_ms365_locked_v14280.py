"""v1.428.0 — Microsoft 365 LOCKED DOWN for a firm that holds client data.

The user (a CPA firm, Microsoft 365 work accounts, admin rights): "highly
secure", the main mailbox, drafts only, and its mail only ever on their own
models. Pinned here, driving the REAL connect route and a REAL Node MCP
server (the v1.415.0 fixture, standing in for the locked package):

* the card's rules: read and draft only (never send, never change the
  calendar), ONE account, the firm's own app + its own directory when given;
* the lock Iron Jarvis ships pins EVERY package by version and sha512, and
  only keytar's install step may run; the installer runs npm with
  ``--ignore-scripts``, rebuilds keytar alone, checks the version, and leaves
  nothing behind when it fails;
* the saved pack runs ``node <locked copy>`` from its own folder, marked
  ``local_only`` + ``locked``; an edit of its settings is refused;
* its tools refuse outside Local only (and on a once-to-the-cloud message),
  and a chat that read it stays Local only — whatever the page sends later;
* its sign-in pane runs the SAVED pack's own program and settings;
* the chat's ``app_connect`` never sets it up.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.connectors import locked_pack as LP
from iron_jarvis.connectors import ms365 as M
from iron_jarvis.connectors import service
from iron_jarvis.connectors.catalog import get_connector
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers import egress
from iron_jarvis.tools.base import ToolContext

SERVER = Path(__file__).parent / "fixtures" / "direct_launch_v14150" / "server.js"
NODE = shutil.which("node")
CARD = "microsoft_365"
APP_ID = "11111111-2222-4333-8444-555555555555"
GOOD = {M.TENANT_ENV: M.WORK, M.EMAIL_ENV: "VR@RealDealCPA.ai", M.APP_ENV: APP_ID}

needs_node = pytest.mark.skipif(NODE is None, reason="Node.js is not on PATH")


# --------------------------------------------------------------------------- #
# the card's rules
# --------------------------------------------------------------------------- #
def test_read_and_draft_only_one_account_and_the_firms_own_directory(tmp_path):
    env = M.env_for(GOOD)
    assert env == {
        "ENABLED_TOOLS": M.SAFE_ENABLED_TOOLS,
        "MS365_MCP_CLOUD_TYPE": "global",
        "MS365_MCP_EXPECTED_USERNAME": "vr@realdealcpa.ai",
        "MS365_MCP_TENANT_ID": "realdealcpa.ai",
        "MS365_MCP_CLIENT_ID": APP_ID,
    }
    for never in ("send-mail", "create-calendar-event", "update-calendar-event", "delete-mail-message"):
        assert never not in M.SAFE_TOOLS
    assert "create-draft-email" in M.SAFE_TOOLS
    # A personal account: the package's own app, named, and the consumer directory.
    personal = M.env_for({**GOOD, M.TENANT_ENV: M.PERSONAL, M.APP_ENV: ""})
    assert personal[M.TENANT_ENV] == M.PERSONAL and personal[M.APP_ENV] == M.DEFAULT_APP
    # The sign-in lives in the folder given — never the package's shared one.
    kept = M.env_for(GOOD, tmp_path / "signin")
    assert kept[M.CACHE_ENV] == str(tmp_path / "signin" / ".token-cache.json")
    assert kept[M.ACCOUNT_ENV] == str(tmp_path / "signin" / ".selected-account.json")


@pytest.mark.parametrize("values, words", [
    ({**GOOD, M.EMAIL_ENV: ""}, M.BAD_EMAIL),
    ({**GOOD, M.EMAIL_ENV: "not an email"}, M.BAD_EMAIL),
    ({**GOOD, M.EMAIL_ENV: "me@example.com&calc"}, M.BAD_EMAIL),
    ({**GOOD, M.APP_ENV: "my-app"}, M.BAD_APP_ID),
    ({**GOOD, M.TENANT_ENV: "organizations"}, M.BAD_KIND),
    # A work account must use the firm's own app (review: never a shared public app).
    ({**GOOD, M.APP_ENV: ""}, M.NEED_APP),
    ({**GOOD, M.APP_ENV: M.DEFAULT_APP}, M.NEED_APP),
])
def test_an_answer_that_cannot_be_used_is_refused_in_words(values, words):
    with pytest.raises(M.Ms365Refused) as info:
        M.env_for(values)
    assert str(info.value) == words


def test_a_saved_rows_answers_round_trip_to_the_same_settings(tmp_path):
    for values in (
        GOOD,
        {**GOOD, M.TENANT_ENV: M.PERSONAL},
        {**GOOD, M.TENANT_ENV: M.PERSONAL, M.APP_ENV: ""},
    ):
        for signin in (None, tmp_path / "s"):
            env = M.env_for(values, signin)
            assert M.env_for(M.answers_from_env(env), signin) == env


# --------------------------------------------------------------------------- #
# the lock
# --------------------------------------------------------------------------- #
def test_the_lock_pins_every_package_and_only_keytar_installs_anything():
    lock = json.loads((LP.MS365.lock_dir / "package-lock.json").read_text(encoding="utf-8"))
    pkgs = {k: v for k, v in lock["packages"].items() if k}
    assert len(pkgs) > 50
    assert all(str(v.get("integrity", "")).startswith("sha512-") for v in pkgs.values()), "a package with no hash"
    top = pkgs["node_modules/" + LP.MS365.package]
    assert top["version"] == LP.MS365.version
    assert [k for k, v in pkgs.items() if v.get("hasInstallScript")] == ["node_modules/keytar"]
    manifest = json.loads((LP.MS365.lock_dir / "package.json").read_text(encoding="utf-8"))
    assert manifest["dependencies"] == {LP.MS365.package: LP.MS365.version, "keytar": "7.9.0"}


NATIVE_REL = "node_modules/keytar/build/Release/keytar.node"
NATIVE_BYTES = b"checked native file"


def _fake_npm(calls, *, version=LP.MS365.version, fail=None, native=NATIVE_BYTES):
    def run(argv, cwd):
        calls.append([Path(argv[0]).name, *argv[1:]])
        if fail and argv[1] == fail:
            raise LP.LockedInstallError("npm could not install the app's program: boom")
        if argv[1] == "ci":
            pkg = Path(cwd) / "node_modules" / Path(LP.MS365.package)
            (pkg / "dist").mkdir(parents=True)
            (pkg / "package.json").write_text(json.dumps({"version": version}), encoding="utf-8")
            (pkg / "dist" / "index.js").write_text("// program", encoding="utf-8")
        if argv[1] == "rebuild" and native is not None:
            out = Path(cwd) / NATIVE_REL
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(native)
    return run


@pytest.fixture(autouse=True)
def _checked_native(monkeypatch):
    """The installer checks keytar's native file against a pinned sha256; the
    fake npm writes a stand-in whose sha256 this pins in its place."""
    import hashlib

    digest = hashlib.sha256(NATIVE_BYTES).hexdigest()
    monkeypatch.setattr(LP, "native_file", lambda pkg: (NATIVE_REL, digest))


def test_the_real_lock_pins_keytars_native_file_for_this_pc():
    rows = {plat: (rel, digest) for plat, rel, digest in LP.MS365.native_files}
    assert rows["win32-x64"] == (
        NATIVE_REL, "90e35de89ab5e5f9290e4ff1bbadcf221a82b2aa0d9b922187dc980adff3c831",
    )


def test_a_native_file_that_is_not_the_checked_one_installs_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(LP, "_npm", _fake_npm([], native=b"something else"))
    monkeypatch.setattr(LP, "npm_program", lambda: "npm")
    monkeypatch.setattr(LP, "node_program", lambda: "node")
    with pytest.raises(LP.LockedInstallError) as info:
        LP.ensure_installed(LP.MS365, tmp_path)
    assert "native file is not the one Iron Jarvis checked" in str(info.value)
    assert list(LP.install_root(tmp_path).iterdir()) == []


def test_the_installer_ignores_install_steps_rebuilds_keytar_alone_and_installs_once(tmp_path, monkeypatch):
    calls: list = []
    monkeypatch.setattr(LP, "_npm", _fake_npm(calls))
    monkeypatch.setattr(LP, "npm_program", lambda: "C:/node/npm.cmd")
    monkeypatch.setattr(LP, "node_program", lambda: "C:/node/node.exe")
    entry = LP.ensure_installed(LP.MS365, tmp_path)
    assert calls == [
        ["npm.cmd", "ci", "--omit=optional", "--omit=dev", "--ignore-scripts", "--no-audit", "--no-fund"],
        ["npm.cmd", "rebuild", "keytar"],
    ]
    assert entry == LP.entry_path(LP.MS365, tmp_path) and entry.is_file()
    assert LP.installed(LP.MS365, tmp_path)
    assert [p.name for p in LP.install_root(tmp_path).iterdir()] == [entry.parents[4].name], "no temp folder left"
    LP.ensure_installed(LP.MS365, tmp_path)
    assert len(calls) == 2, "an installed lock is never installed again"


@pytest.mark.parametrize("version, fail, words", [
    ("0.171.0", None, "not the 0.170.2 Iron Jarvis locked"),
    (LP.MS365.version, "ci", "boom"),
    (LP.MS365.version, "rebuild", "boom"),
])
def test_a_failed_install_says_why_and_leaves_nothing(tmp_path, monkeypatch, version, fail, words):
    monkeypatch.setattr(LP, "_npm", _fake_npm([], version=version, fail=fail))
    monkeypatch.setattr(LP, "npm_program", lambda: "npm")
    monkeypatch.setattr(LP, "node_program", lambda: "node")
    with pytest.raises(LP.LockedInstallError) as info:
        LP.ensure_installed(LP.MS365, tmp_path)
    assert words in str(info.value)
    assert list(LP.install_root(tmp_path).iterdir()) == []


def test_no_node_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(LP, "npm_program", lambda: "")
    with pytest.raises(LP.LockedInstallError) as info:
        LP.ensure_installed(LP.MS365, tmp_path)
    assert str(info.value) == LP.NO_NODE


# --------------------------------------------------------------------------- #
# the saved pack, its tools, the chat lock
# --------------------------------------------------------------------------- #
@pytest.fixture
def client(tmp_path, monkeypatch):
    home = tmp_path / "home"

    def install(pkg, home_dir):
        entry = LP.entry_path(pkg, home_dir)
        entry.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SERVER, entry)
        return entry

    monkeypatch.setattr(LP, "ensure_installed", install)
    with TestClient(create_app(str(home))) as c:
        yield c


def _new_chat(c, setup=None) -> str:
    """A saved chat ('new' makes one), Local only unless told otherwise."""
    r = c.put("/chat/threads/new", json={"messages": [{"role": "user", "content": "hi"}],
                                         "setup": {"local_only": True} if setup is None else setup})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _connect(c, values=GOOD):
    return c.post(f"/connectors/{CARD}/connect", json={"values": values})


def _row(c) -> dict:
    return next(s for s in c.app.state.platform.config.mcp_servers if s.get("name") == CARD)


def _tool(c):
    reg = c.app.state.platform.registry
    return reg.get(f"mcp__{CARD}__whoami")


def _ctx(c, tmp_path, **kw) -> ToolContext:
    p = c.app.state.platform
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return ToolContext(workspace=ws, session_id="chat", agent_run_id="r", config=p.config,
                       event_bus=p.event_bus, engine=p.engine, **kw)


@needs_node
def test_connect_saves_the_locked_program_from_its_own_folder(client):
    r = _connect(client)
    assert r.status_code == 200, r.text
    row = _row(client)
    home = client.app.state.platform.config.home
    entry = LP.entry_path(LP.MS365, home)
    assert row["command"] == "node" and row["args"] == [str(entry)]
    assert row["cwd"] == str(LP.install_dir(LP.MS365, home))
    assert row["env"] == M.env_for(GOOD, LP.signin_dir(LP.MS365, home))
    assert row["env"][M.CACHE_ENV].startswith(str(LP.install_root(home)))
    assert row["local_only"] is True and row["locked"] == "ms365"
    assert "env_secrets" not in row, "nothing on the card is a secret"
    cards = client.get("/connectors").json()
    card = next(x for x in (cards.get("connectors") or cards.get("items") or cards) if x.get("id") == CARD)
    assert card["signin"] == {"cli": "microsoft-365-signin", "label": "Sign in to Microsoft",
                              "account": "Work or school account"}


@needs_node
def test_the_program_runs_from_its_own_folder_never_the_users(client, tmp_path):
    assert _connect(client).status_code == 200
    tool = _tool(client)
    assert tool is not None and tool.local_only is True and tool.app_name == "Microsoft 365"

    async def run():
        with egress.local_only_scope(True):
            return await tool.execute({}, _ctx(client, tmp_path))

    out = asyncio.run(run())
    assert out.ok, out.error
    info = json.loads(out.output)
    assert Path(info["cwd"]) == LP.install_dir(LP.MS365, client.app.state.platform.config.home)


@pytest.mark.parametrize("values", [{**GOOD, M.EMAIL_ENV: ""}, {**GOOD, M.APP_ENV: "nope"}])
def test_a_card_that_cannot_be_used_saves_nothing(client, values):
    r = _connect(client, values)
    assert r.status_code == 400
    assert not [s for s in client.app.state.platform.config.mcp_servers if s.get("name") == CARD]


@needs_node
def test_its_tools_refuse_outside_local_only_and_on_a_once_to_the_cloud_message(client, tmp_path):
    assert _connect(client).status_code == 200
    tool = _tool(client)

    async def run(on, once=""):
        with egress.local_only_scope(on, once):
            return await tool.execute({}, _ctx(client, tmp_path))

    off = asyncio.run(run(False))
    assert not off.ok and off.error == (
        "Microsoft 365 is set to stay on your own models, so it can't be used in this chat. "
        "Turn on Local only for this chat, choose one of your own models, then ask again."
    )
    once = asyncio.run(run(True, "anthropic"))
    assert not once.ok and "stay on your own models" in once.error
    assert asyncio.run(run(True)).ok


@needs_node
def test_a_chat_that_read_it_stays_local_only_whatever_the_page_sends(client, tmp_path):
    assert _connect(client).status_code == 200
    tid = _new_chat(client)
    tool = _tool(client)

    async def run():
        with egress.local_only_scope(True):
            return await tool.execute({}, _ctx(client, tmp_path, thread_id=tid))

    assert asyncio.run(run()).ok
    engine = client.app.state.platform.engine
    assert client.get(f"/chat/threads/{tid}").json()["setup"] == {
        "local_only": True, egress.HOLDS_KEY: [CARD],
    }
    # The page turns the switch off, and later saves with no setup at all.
    for setup in ({}, None):
        r = client.put(f"/chat/threads/{tid}", json={"messages": [{"role": "user", "content": "hi"}],
                                                      "setup": setup})
        assert r.status_code == 200, r.text
        got = client.get(f"/chat/threads/{tid}").json()["setup"]
        assert got.get("local_only") is True and got.get(egress.HOLDS_KEY) == [CARD], setup
    assert asyncio.run(egress.effective_local_only(engine, False, tid)) is True


@needs_node
def test_a_new_chats_first_turn_holds_the_chat_once_the_page_links_it(client, tmp_path):
    assert _connect(client).status_code == 200
    tool = _tool(client)

    async def run():
        with egress.local_only_scope(True):
            return await tool.execute({}, _ctx(client, tmp_path, turn_id="turn-new-1"))

    assert asyncio.run(run()).ok
    tid = _new_chat(client, {})
    engine = client.app.state.platform.engine
    assert egress.thread_local_only(engine, tid) is False
    r = client.post("/chat/turns/turn-new-1/thread", json={"thread_id": tid})
    assert r.status_code == 200, r.text
    assert egress.thread_local_only(engine, tid) is True
    client.put(f"/chat/threads/{tid}", json={"messages": [{"role": "user", "content": "hi"}], "setup": {}})
    assert egress.thread_local_only(engine, tid) is True


@needs_node
def test_its_settings_are_changed_only_from_its_card(client):
    assert _connect(client).status_code == 200
    r = client.patch(f"/mcp/servers/{CARD}", json={"env": {"ENABLED_TOOLS": ".*"}})
    assert r.status_code == 409 and "Disconnect" in r.json()["detail"]
    home = client.app.state.platform.config.home
    assert _row(client)["env"] == M.env_for(GOOD, LP.signin_dir(LP.MS365, home))
    # The switch still works.
    assert client.patch(f"/mcp/servers/{CARD}", json={"enabled": False}).status_code == 200


@needs_node
def test_its_sign_in_runs_the_saved_packs_own_program_and_settings(client):
    assert service.locked_signin(client.app.state.platform, CARD) is None, "nothing saved yet"
    assert _connect(client).status_code == 200
    argv, env, cwd = service.locked_signin(client.app.state.platform, CARD)
    home = client.app.state.platform.config.home
    assert argv[1:] == [str(LP.entry_path(LP.MS365, home)), "--login"]
    assert Path(argv[0]).stem.lower() == "node"
    assert env == M.env_for(GOOD, LP.signin_dir(LP.MS365, home))
    assert cwd == str(LP.install_dir(LP.MS365, home))


def test_the_sign_in_is_refused_until_the_app_is_connected(client):
    r = client.post("/terminals/launch", json={"cli": "microsoft-365-signin"})
    assert r.status_code == 409
    assert r.json()["detail"].startswith("Connect Microsoft 365 on its card")


def test_the_chat_never_sets_it_up(client):
    from iron_jarvis.settings.records import AppConnectTool

    tool = AppConnectTool(client.app.state.platform)
    check = asyncio.run(tool.spend_check({"app": CARD}, None))
    assert check is not None and check.ask is False
    out = asyncio.run(tool.execute({"app": CARD, "values": GOOD}, None))
    assert out.ok is False and "set up only from its card" in out.error
    assert not [s for s in client.app.state.platform.config.mcp_servers if s.get("name") == CARD]


def test_the_generic_door_refuses_a_locked_card():
    with pytest.raises(ValueError) as info:
        service.pack_body(get_connector(CARD), GOOD)
    assert "set up only from its card" in str(info.value)


def test_backups_leave_the_installed_program_out(tmp_path):
    from iron_jarvis.maintenance import create_backup

    home = tmp_path / "h"
    (home / "mcp-packages" / "ms365-x").mkdir(parents=True)
    (home / "mcp-packages" / "ms365-x" / "big.js").write_text("x", encoding="utf-8")
    (home / "config.toml").write_text("", encoding="utf-8")
    import tarfile

    path, _size = create_backup(home, tmp_path / "backup.tar.gz")
    with tarfile.open(path) as tar:
        names = tar.getnames()
    assert not [n for n in names if "mcp-packages" in n]
