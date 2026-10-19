"""v1.356.0 lane 6D — Microsoft 365 / Outlook can be connected (G60).

The acceptance, as a new user meets it: press Connect on "Microsoft 365
(Outlook, Calendar, OneDrive)", answer ONE plain question (work or school /
personal), sign in through a Build pane that shows the web address and the
code, and chat can answer what is on the calendar tomorrow.

Pinned through the REAL app factory, the real catalog, the real one add door
(``mcp/pack_door``), the real launch route and the real chat arming helper.
Nothing signs in and nothing touches the network:

* the pack's process is a stand-in for Softeria's server
  (``tests/fixtures/ms365_mcp_server_v13560.py``) that lists the REAL server's
  119 outlook+onedrive tool names and narrows them with ``ENABLED_TOOLS`` the
  way the real server does — only the program is swapped (``StdioTransport``
  is handed the fixture instead of npx); the command, arguments and settings
  the pack was saved with are recorded as they were passed;
* the Build pane is the fake terminal backend: the test reads what was typed
  and the environment the shell was started with.

What is proven: the card saves a pack that loads exactly the 24 tools chat
arms as one app (the calendar's day view included); the Sign in press opens
the pack's OWN program with ``--login`` and the SAME settings (so the sign-in
asks Microsoft for what the pack will use); a typed answer to the choice is
refused; a pack edited by hand gets no press; a switched-off pack says so.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.connectors import catalog, service
from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.mcp import client as mcp_client
from iron_jarvis.mcp import pack_door
from iron_jarvis.mcp import tools as mcp_tools_mod
from iron_jarvis.terminals import ai_clis
from iron_jarvis.terminals.backend import FakeBackend

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "ms365_mcp_server_v13560.py"
CONTRACT = ROOT / "dashboard" / "__tests__" / "fixtures" / "microsoft-365-row-v13560.json"
CARD_ID = "microsoft_365"
SIGNIN_TOOLS = ("login", "logout", "verify-login", "list-accounts", "select-account", "remove-account")


def _fixture_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("ms365_fixture_v13560", FIXTURE)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #
class _RecordingBackend(FakeBackend):
    """Keeps what each shell was started with and everything typed into it."""

    instances: list["_RecordingBackend"] = []
    lock = threading.Lock()

    def __init__(self) -> None:
        super().__init__()
        self.env: dict | None = None
        self.written: list[str] = []
        with _RecordingBackend.lock:
            _RecordingBackend.instances.append(self)

    def start(self, argv, cwd, env, cols, rows) -> None:  # type: ignore[override]
        self.env = dict(env) if env is not None else None
        super().start(argv, cwd, env, cols, rows)

    def write(self, data) -> None:  # type: ignore[override]
        self.written.append(data if isinstance(data, str) else data.decode())
        super().write(data)


def _backend_of(pane_id: str) -> _RecordingBackend:
    found = [
        b for b in _RecordingBackend.instances
        if b.env is not None and b.env.get("IRONJARVIS_PANE_ID") == pane_id
    ]
    assert found, f"no shell was started for pane {pane_id}"
    return found[-1]


@pytest.fixture(autouse=True)
def _fresh_records():
    """Load records and live clients are process-wide (the v1.325.2 lesson)."""
    for store in (mcp_tools_mod._LOAD_STATUS, mcp_tools_mod._ANNOUNCED, mcp_tools_mod._LOAD_CFGS):
        store.clear()
    yield
    for store in (mcp_tools_mod._LOAD_STATUS, mcp_tools_mod._ANNOUNCED, mcp_tools_mod._LOAD_CFGS):
        store.clear()
    mcp_tools_mod.forget_live_client(CARD_ID)


@pytest.fixture
def spawned(monkeypatch):
    """The pack's program is the fixture; what the pack ASKED to run is kept.

    ``resolve_launcher`` answers the command unchanged, so neither the card's
    "Needs Node.js" check nor the load needs Node on this PC."""
    seen: list[dict] = []
    real = mcp_client.StdioTransport

    def _transport(command, args=None, env=None, cwd=None, request_timeout=None):
        seen.append({"command": command, "args": list(args or []), "env": dict(env or {})})
        return real(sys.executable, [str(FIXTURE)], env=env, cwd=cwd, request_timeout=request_timeout)

    monkeypatch.setattr(mcp_tools_mod, "StdioTransport", _transport)
    monkeypatch.setattr(mcp_tools_mod, "resolve_launcher", lambda command: command)
    return seen


@pytest.fixture
def client(tmp_path, monkeypatch, spawned):
    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    monkeypatch.setenv("IRONJARVIS_IRON_PROXY_BUNDLE", str(tmp_path / "no-bundle.mjs"))
    monkeypatch.delenv("IRONJARVIS_IRON_PROXY_NODE", raising=False)
    # The daemon's own environment carries neither setting: whatever a pane
    # or a pack has, it got from the card.
    monkeypatch.delenv("ENABLED_TOOLS", raising=False)
    monkeypatch.delenv(catalog.MS365_TENANT_ENV, raising=False)
    _RecordingBackend.instances = []
    monkeypatch.setattr("iron_jarvis.terminals.session.default_backend", _RecordingBackend)

    async def _no_watch(*_a, **_k):
        await asyncio.Event().wait()

    monkeypatch.setattr("iron_jarvis.iron_proxy.service.watch", _no_watch)
    proj = tmp_path / "proj"
    proj.mkdir()
    with TestClient(create_app(str(proj))) as c:
        try:
            yield c
        finally:
            mcp_tools_mod.unload_pack_tools(c.app.state.d.platform.registry, CARD_ID)


def _program_on_path(monkeypatch) -> None:
    """The typed line is the plain command when the program is on the
    daemon's PATH (patched only AFTER the app started, so boot's own CLI
    detection never sees a made-up PATH)."""
    monkeypatch.setattr(ai_clis.shutil, "which", lambda exe: f"/usr/bin/{exe}")


def _platform(c: TestClient):
    return c.app.state.d.platform


def _row(c: TestClient, cid: str = CARD_ID) -> dict:
    return next(r for r in c.get("/connectors").json()["connectors"] if r["id"] == cid)


def _saved(c: TestClient, name: str = CARD_ID) -> dict | None:
    return next((s for s in _platform(c).config.mcp_servers or [] if s.get("name") == name), None)


def _card() -> catalog.Connector:
    card = catalog.get_connector(CARD_ID)
    assert card is not None
    return card


def _connect(c: TestClient, answer: str):
    return c.post(f"/connectors/{CARD_ID}/connect", json={"values": {catalog.MS365_TENANT_ENV: answer}})


# --------------------------------------------------------------------------- #
# The card
# --------------------------------------------------------------------------- #
def test_the_card_is_in_the_directory_with_one_plain_choice(client):
    """A new user finds it by name and is asked ONE question in words.
    MUTATION: drop the card from CATALOG -> StopIteration (red)."""
    row = _row(client)
    assert row["name"] == "Microsoft 365 (Outlook, Calendar, OneDrive)"
    assert row["connect_via"] == "mcp" and row["status"] == "disconnected"
    assert row["enabled"] is True
    assert row["signin"] is None, "nothing to sign in to before it is saved"
    assert row["runtime"] == "Node.js" and row["runtime_ready"] is True
    (field,) = row["fields"]
    assert field["name"] == catalog.MS365_TENANT_ENV and field["label"] == "Which Microsoft account?"
    assert field["optional"] is False
    assert [(o["value"], o["label"]) for o in field["options"]] == [
        ("common", "Work or school account"),
        ("consumers", "Personal account"),
    ]
    for o in field["options"]:
        assert o["help"], "each answer says what it means"
    # No mechanism word on the card (the v1.353.0 G42 rule).
    words = " ".join([row["blurb"], row["unlocks"], *row["scopes"]])
    assert not re.search(r"\b(MCP|npx|stdio|env)\b", words)


def test_every_other_card_keeps_its_field_shape():
    """`options` is added ONLY to a choice field: every typed field's dict is
    what it always was. MUTATION: always add `options` -> red."""
    for c in catalog.CATALOG:
        for f in c.fields:
            d = catalog.field_dict(f)
            if c.id == CARD_ID:
                continue
            assert set(d) == {"name", "label", "help", "kind", "optional"}, (c.id, f.name)


def test_the_tools_fit_the_one_group_chat_arms_for_an_app():
    """Chat arms a connected app's tools as ONE group of at most 24, in name
    order (chat_turn._MAX_CONNECTOR_TOOLS). The server's presets are far wider
    (outlook + onedrive = 119 listed tools) and put the calendar's day view
    past the 24th name — so the card's filter must leave 24 or fewer, the
    day view among them. Checked against the REAL server's tool names.
    MUTATION: widen the filter to a preset's names -> red."""
    fixture = _fixture_module()
    names = [n for n, _ro in fixture.TOOLS]
    assert len(names) == 119 and "get-calendar-view" in names
    rx = re.compile(catalog.MS365_ENABLED_TOOLS, re.IGNORECASE)
    kept = sorted(n for n in names if n in SIGNIN_TOOLS or rx.search(n))
    assert set(kept) == set(catalog.MS365_TOOLS) | set(SIGNIN_TOOLS)
    assert len(kept) == 24 <= chat_turn._MAX_CONNECTOR_TOOLS
    assert "get-calendar-view" in kept[: chat_turn._MAX_CONNECTOR_TOOLS]
    # Every tool the card names is a real one (a renamed tool would vanish).
    assert set(catalog.MS365_TOOLS) <= set(names)


def test_nothing_a_windows_launcher_would_read_rides_the_command_line():
    """npx is a .cmd run through cmd.exe: `|`, `^`, `&`, `<`, `>` in an
    argument would be read by cmd itself. The tool filter is a regex FULL of
    `|`, so it rides the environment. MUTATION: put the filter in `args` ->
    red."""
    card = _card()
    for arg in [*card.args, *catalog.signin_argv(card)]:
        assert not set(arg) & set('|^&<>%"'), arg
    assert card.env == {"ENABLED_TOOLS": catalog.MS365_ENABLED_TOOLS}


def test_the_pack_settings_are_never_taken_for_secrets():
    """Neither setting may be vaulted: the sign-in pane reads them off the
    card, and a vaulted copy would make the saved pack and the sign-in
    disagree. MUTATION: name the tenant field ..._TOKEN -> red."""
    from iron_jarvis.mcp.secrets import is_secret_key

    assert not is_secret_key("ENABLED_TOOLS")
    assert not is_secret_key(catalog.MS365_TENANT_ENV)


# --------------------------------------------------------------------------- #
# Connect, through the one add door
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("answer, key", [("consumers", "personal"), ("common", "work")])
def test_connect_saves_the_pack_through_the_one_door_and_loads_the_24_tools(
    client, spawned, monkeypatch, answer, key
):
    """MUTATION: drop `connector.env` from pack_body -> the pack loads all
    119 tools and the stored env misses the filter (red)."""
    door_calls: list[str] = []
    real_add = pack_door.add_pack

    def _spy(platform, body, **kw):
        door_calls.append(body.name)
        return real_add(platform, body, **kw)

    monkeypatch.setattr(pack_door, "add_pack", _spy)
    r = _connect(client, answer)
    assert r.status_code == 200, r.text
    assert r.json()["tools_loaded"] == 24
    assert door_calls == [CARD_ID], "the Directory's Connect goes through mcp/pack_door"

    card = _card()
    cfg = _saved(client)
    assert cfg is not None
    assert cfg["command"] == "npx" and cfg["args"] == ["-y", catalog.MS365_PACKAGE]
    assert cfg["env"] == catalog.signin_env(card, key)
    assert not cfg.get("env_secrets") and not cfg.get("arg_secrets")
    # What the pack's process was started with — the settings, unmasked.
    launched = spawned[-1]
    assert launched["command"] == "npx" and launched["args"] == ["-y", catalog.MS365_PACKAGE]
    assert launched["env"]["ENABLED_TOOLS"] == catalog.MS365_ENABLED_TOOLS
    assert launched["env"][catalog.MS365_TENANT_ENV] == answer
    toml = (Path(_platform(client).config.home) / "config.toml").read_text(encoding="utf-8")
    assert catalog.MS365_TENANT_ENV in toml and "get-calendar-view" in toml


def test_a_typed_answer_is_refused_and_so_is_none(client, spawned):
    """The value is what the pack reads; a guessed one ("work") would sign in
    the wrong way. MUTATION: drop the option check in pack_body -> saved."""
    r = _connect(client, "work")
    assert r.status_code == 400
    assert r.json()["detail"] == (
        'Which Microsoft account? Pick "Work or school account" or "Personal account".'
    )
    r = client.post(f"/connectors/{CARD_ID}/connect", json={"values": {}})
    assert r.status_code == 400 and "Which Microsoft account?" in r.json()["detail"]
    assert _saved(client) is None and spawned == []


# --------------------------------------------------------------------------- #
# Sign in, in a Build pane
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "answer, account",
    [("consumers", "Personal account"), ("common", "Work or school account")],
)
def test_sign_in_opens_a_pane_running_the_packs_own_program_with_its_settings(
    client, monkeypatch, answer, account
):
    """The acceptance's middle: one press, a NEW Build pane, the sign-in typed
    AND started, the pane showing whatever the program prints (the web address
    and the code). The login is the pack's own command + arguments + --login
    with the SAME settings, so it asks for the tools (and so the scopes) and
    the Microsoft authority the pack will use. MUTATION: drop the env hand-off
    in launch_pane -> the shell has no ENABLED_TOOLS (red)."""
    _program_on_path(monkeypatch)
    assert _connect(client, answer).status_code == 200
    row = _row(client)
    assert row["status"] == "connected" and row["tools_loaded"] == 24
    signin = row["signin"]
    key = "personal" if answer == "consumers" else "work"
    assert signin == {
        "cli": f"microsoft-365-signin-{key}",
        "label": "Sign in to Microsoft",
        "account": account,
    }

    r = client.post("/terminals/launch", json={"cli": signin["cli"]})
    assert r.status_code == 200, r.text
    pane = r.json()
    assert pane["name"] == f"Microsoft sign-in · {account}"
    assert pane.get("agent_cli") in (None, ""), "an app sign-in is not an AI CLI"

    shell = _backend_of(pane["id"])
    cfg = _saved(client)
    assert shell.written == ["npx -y @softeria/ms-365-mcp-server --login\r"]
    assert shell.written[0].rstrip("\r").split(" ") == [cfg["command"], *cfg["args"], "--login"]
    for k, v in cfg["env"].items():
        assert shell.env.get(k) == v, k
    # The daemon's own environment is still under it (PATH and the rest).
    assert shell.env.get("PATH") == os.environ.get("PATH")


def test_the_sign_in_needs_no_account_and_says_no_to_one(client):
    """It never runs on an Iron-Proxy account (it is not an AI CLI)."""
    r = client.post("/terminals/launch", json={"cli": "microsoft-365-signin-work", "account": "default"})
    assert r.status_code == 400
    assert "does not run on an Iron-Proxy account" in r.json()["detail"]


def test_a_sign_in_whose_card_cannot_be_read_starts_nothing(client, monkeypatch):
    """A sign-in with the wrong settings would ask Microsoft for the wrong
    account kind: refuse instead. MUTATION: swallow the LookupError -> 200."""
    monkeypatch.setattr(catalog, "get_connector", lambda _cid: None)
    r = client.post("/terminals/launch", json={"cli": "microsoft-365-signin-personal"})
    assert r.status_code == 409
    assert "nothing was started" in r.json()["detail"]
    assert _RecordingBackend.instances == [], "no shell was started"


def test_every_other_launch_starts_exactly_as_before(client, monkeypatch):
    """No extra settings for a job that has none: the starter download and a
    Launch-menu CLI start on the daemon's environment alone."""
    assert ai_clis.launch_env(ai_clis.launch_entry("ollama-starter")) is None
    assert ai_clis.launch_env(ai_clis.launch_entry("claude")) is None
    _program_on_path(monkeypatch)
    r = client.post("/terminals/launch", json={"cli": "ollama-starter"})
    assert r.status_code == 200, r.text
    shell = _backend_of(r.json()["id"])
    assert "ENABLED_TOOLS" not in shell.env and catalog.MS365_TENANT_ENV not in shell.env
    assert r.json()["agent_cli"] == "ollama"


def test_the_sign_in_jobs_stay_off_the_launch_menu_and_match_the_catalog(client):
    """Launchable by id, never a CLI: the Launch menu, the classifier and the
    bell read AI_CLIS. The terminals module keeps its own copy of the rows
    (it must not import the connectors package): held EQUAL here.
    MUTATION: change the typed command in SETUP_LAUNCHES -> red."""
    clis = {c["id"] for c in client.get("/terminals/ai-clis").json()["clis"]}
    rows = catalog.signin_launches()
    assert [r["id"] for r in rows] == ["microsoft-365-signin-work", "microsoft-365-signin-personal"]
    for r in rows:
        assert r["id"] not in clis
        entry = ai_clis.launch_entry(r["id"])
        assert entry is not None and entry in ai_clis.SETUP_LAUNCHES
        assert {k: v for k, v in entry.items() if k != "agent_cli"} == r
        assert entry["agent_cli"] == ""
        assert entry["command"] == " ".join(catalog.signin_argv(_card()))
    assert not {r["id"] for r in rows} & {c["id"] for c in ai_clis.AI_CLIS}


def test_the_sign_in_is_typed_by_full_path_when_npx_predates_the_daemon(monkeypatch):
    """Node installed while Iron Jarvis was running: the pane's shell inherits
    the daemon's older PATH, so the program is typed by where it is."""
    npx = r"C:\Users\Jo Doe\AppData\Roaming\npm\npx.cmd"
    monkeypatch.setattr(ai_clis.shutil, "which", lambda exe: None)
    monkeypatch.setattr(ai_clis, "_find", lambda exe: npx if exe == "npx" else None)
    entry = ai_clis.launch_entry("microsoft-365-signin-work")
    assert ai_clis.launch_line(entry, "pwsh") == (
        "& '" + npx + "' '-y' '@softeria/ms-365-mcp-server' '--login'"
    )
    assert ai_clis.launch_line(entry, "cmd") == '"' + npx + '" -y @softeria/ms-365-mcp-server --login'


# --------------------------------------------------------------------------- #
# Chat can answer what is on the calendar tomorrow
# --------------------------------------------------------------------------- #
def test_chat_arms_every_tool_and_the_calendar_answers_tomorrow(client):
    """The acceptance's end, at the seams chat uses: turning the app on for a
    chat (+ › Connections) arms its whole group — the day view included — and
    the pack's day view answers. The pack was started with the account kind
    the user chose. MUTATION: widen the filter past 24 -> the day view falls
    out of the armed group (red)."""
    assert _connect(client, "consumers").status_code == 200
    d = client.app.state.d
    tools, memory = chat_turn._resolve_connectors(d, SimpleNamespace(connectors=[CARD_ID]))
    assert memory == []
    assert len(tools) == 24
    day = f"mcp__{CARD_ID}__get-calendar-view"
    assert day in tools
    for name in SIGNIN_TOOLS:
        assert f"mcp__{CARD_ID}__{name}" in tools

    tool = _platform(client).registry.get(day)
    assert tool is not None and not tool.write_like, "reading the calendar changes nothing"
    result = asyncio.run(tool.execute({}, SimpleNamespace(event_bus=None)))
    assert result.ok, result.error
    answer = json.loads(re.sub(r"(?s)^.*?(\{.*\}).*$", r"\1", result.output))
    assert answer["value"][0]["subject"] == "Dentist appointment"
    assert answer["tenant"] == "consumers", "the pack signs in as the account kind chosen"


# --------------------------------------------------------------------------- #
# What the row says
# --------------------------------------------------------------------------- #
def _cfg(**over) -> dict:
    card = _card()
    base = {
        "name": CARD_ID,
        "command": card.command,
        "args": list(card.args),
        "env": catalog.signin_env(card, "work"),
    }
    base.update(over)
    return base


def test_a_pack_changed_by_hand_gets_no_sign_in_press():
    """The press is offered only while the login would run exactly what the
    pack runs. MUTATION: drop the env equality check -> a press for a pack
    whose filter differs (red)."""
    card = _card()
    assert service.signin_row(card, _cfg()) == {
        "cli": "microsoft-365-signin-work",
        "label": "Sign in to Microsoft",
        "account": "Work or school account",
    }
    env = catalog.signin_env(card, "work")
    assert service.signin_row(card, _cfg(env={**env, "ENABLED_TOOLS": ".*"})) is None
    assert service.signin_row(card, _cfg(env={**env, "MS365_MCP_CLIENT_ID": "x"})) is None
    assert service.signin_row(card, _cfg(env={**env, catalog.MS365_TENANT_ENV: "my-tenant"})) is None
    assert service.signin_row(card, _cfg(args=["-y", catalog.MS365_PACKAGE, "--org-mode"])) is None
    assert service.signin_row(card, _cfg(command="node")) is None
    assert service.signin_row(card, _cfg(env_secrets={"X_TOKEN": "mcp.microsoft_365.X_TOKEN"})) is None
    assert service.signin_row(card, None) is None
    assert service.signin_row(catalog.get_connector("notion"), _cfg()) is None


def test_only_a_card_that_signs_in_carries_a_signin_key(client):
    rows = client.get("/connectors").json()["connectors"]
    with_key = {r["id"] for r in rows if "signin" in r}
    assert with_key == {CARD_ID}


def test_a_switched_off_pack_says_so_and_carries_enabled_false(client):
    """G78's switch reads `enabled` on the row; a pack the user switched off
    reads "off" with plain words — never "isn't answering, press Retry".
    MUTATION: drop the off branch -> status no_tools (red)."""
    assert _connect(client, "common").status_code == 200
    platform = _platform(client)
    servers = list(platform.config.mcp_servers)
    servers = [dict(s, enabled=False) if s.get("name") == CARD_ID else s for s in servers]
    servers.append({"name": "mypack", "command": "mypack-server", "enabled": False})
    servers.append({"name": "onpack", "command": "onpack-server"})
    platform.config.mcp_servers = servers
    mcp_tools_mod.unload_pack_tools(platform.registry, CARD_ID)

    row = _row(client)
    assert row["enabled"] is False and row["status"] == "off" and row["connected"] is True
    assert row["detail"] == "It's switched off, so its tools are not loaded."
    assert row["signin"]["cli"] == "microsoft-365-signin-work", "signing in does not need it on"
    mine = _row(client, "mypack")
    assert mine["enabled"] is False and mine["status"] == "off"
    on = _row(client, "onpack")
    assert on["enabled"] is True and on["status"] != "off"


def test_a_saved_pack_with_no_switch_reads_on():
    """Every row saved before the switch existed has no `enabled` key."""
    assert service.pack_on({"name": "x"}) is True
    assert service.pack_on({"name": "x", "enabled": False}) is False
    assert service.pack_on(None) is True


# --------------------------------------------------------------------------- #
# The two catalogs, and the dashboard's contract
# --------------------------------------------------------------------------- #
def test_the_tools_catalog_agrees_if_it_ever_names_the_package(client):
    """Not mirrored on purpose: the Tools page's add form has no account
    choice and no Sign in, so a row there would add a pack that could never
    sign in from that page. If one is ever added, it must start what the card
    starts."""
    rows = client.get("/mcp/catalog").json()["catalog"]
    card = _card()
    for row in rows:
        if catalog.MS365_PACKAGE in json.dumps(row):
            assert row["command"] == card.command and row["args"] == card.args


def test_the_dashboard_fixture_is_the_daemons_row(client):
    """`dashboard/__tests__/fixtures/microsoft-365-row-v13560.json` holds the
    row the REAL daemon sends (before and after Connect); the dashboard's
    pins feed it through the real components. Regenerate with
    IJ_REGEN_MS365_ROW=1 after an intended change."""
    keys = ("id", "name", "connect_via", "fields", "status", "connected", "enabled", "signin")
    before = {k: _row(client)[k] for k in keys}
    assert _connect(client, "common").status_code == 200
    after = {k: _row(client)[k] for k in keys}
    data = {"before": before, "after": after}
    if os.environ.get("IJ_REGEN_MS365_ROW") == "1":
        CONTRACT.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    assert json.loads(CONTRACT.read_text(encoding="utf-8")) == data
