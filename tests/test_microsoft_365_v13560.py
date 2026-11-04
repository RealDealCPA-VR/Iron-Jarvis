"""v1.356.0 lane 6D — Microsoft 365 / Outlook can be connected (G60).

The acceptance, as a new user meets it: press Connect on "Microsoft 365
(Outlook, Calendar, OneDrive)", answer ONE plain question (work or school /
personal), sign in through a Build pane that shows the web address and the
code, and chat can answer what is on the calendar tomorrow.

v1.428.0 (moved on purpose): the card is LOCKED DOWN (``connectors/ms365.py``,
``connectors/locked_pack.py``). The card also asks for the account's email
(only that account can sign in) and, optionally, the firm's own app ID; the
pack is the program installed once from Iron Jarvis's own lock, run as
``node <entry>`` from its own folder (never ``npx`` at its newest), with read
and draft tools only (``ms365.SAFE_TOOLS``), and marked ``local_only`` +
``locked``. There is ONE sign-in job (``microsoft-365-signin``): it types the
SAVED pack's own program and settings. The pins below keep each purpose
against that contract; ``tests/test_ms365_locked_v14280.py`` pins the lock.

Pinned through the REAL app factory, the real catalog, the real one add door
(``mcp/pack_door``), the real launch route and the real chat arming helper.
Nothing signs in, nothing is installed and nothing touches the network:

* the locked install is a stand-in (``locked_pack.ensure_installed`` writes an
  empty program file where the lock's would go — never npm);
* the pack's process is a stand-in for Softeria's server
  (``tests/fixtures/ms365_mcp_server_v13560.py``) that lists the REAL server's
  119 outlook+onedrive tool names and narrows them with ``ENABLED_TOOLS`` the
  way the real server does — only the program is swapped (``StdioTransport``
  is handed the fixture instead of node); the command, arguments and settings
  the pack was saved with are recorded as they were passed;
* the Build pane is the fake terminal backend: the test reads what was typed
  and the environment the shell was started with.

What is proven: the card saves a locked pack that loads exactly the read and
draft tools (plus the server's sign-in tools), which chat arms as one app (the
calendar's day view included); the Sign in press opens the SAVED pack's OWN
program with ``--login`` and the SAME settings (so the sign-in asks Microsoft
for what the pack will use); an answer off the list is refused; a pack edited
by hand gets no press; a switched-off pack says so.
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
from iron_jarvis.connectors import locked_pack as LP
from iron_jarvis.connectors import ms365
from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes.iron_proxy import shell_line
from iron_jarvis.mcp import client as mcp_client
from iron_jarvis.mcp import pack_door
from iron_jarvis.mcp import tools as mcp_tools_mod
from iron_jarvis.providers import egress
from iron_jarvis.terminals import ai_clis
from iron_jarvis.terminals.backend import FakeBackend

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "ms365_mcp_server_v13560.py"
CONTRACT = ROOT / "dashboard" / "__tests__" / "fixtures" / "microsoft-365-row-v13560.json"
CARD_ID = "microsoft_365"
SIGNIN_TOOLS = ("login", "logout", "verify-login", "list-accounts", "select-account", "remove-account")
#: v1.428.0: the one sign-in job (the saved pack carries the account kind).
SIGNIN_JOB = "microsoft-365-signin"
#: The account the card is answered with (``ms365.EMAIL_ENV`` is required).
EMAIL = "me@example.com"
#: What the stand-in server loads under the locked card's filter: the read and
#: draft tools plus the sign-in tools it always lists (measured: 15 + 6).
# v1.428.0 review (moved on purpose): 15 read and draft tools + 3 of the
# server's 6 sign-in tools (logout, remove-account, select-account are hidden).
TOOLS_LOADED = 18


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


def _stand_in_install(pkg, home):
    """``locked_pack.ensure_installed`` without npm: the program file the lock
    would install, empty (the stand-in server runs in its place). Never npm,
    never the network."""
    entry = LP.entry_path(pkg, home)
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text("// stand-in for the locked program\n", encoding="utf-8")
    return entry


@pytest.fixture
def spawned(monkeypatch):
    """The pack's program is the fixture; what the pack ASKED to run is kept.

    ``resolve_launcher`` answers the command unchanged, so neither the card's
    "Needs Node.js" check nor the load needs Node on this PC. v1.428.0: the
    locked install is the stand-in (``_stand_in_install``)."""
    seen: list[dict] = []
    real = mcp_client.StdioTransport

    def _transport(command, args=None, env=None, cwd=None, request_timeout=None, **_kw):
        # **_kw: v1.415.0 adds direct= (an npx line parsed for a direct start);
        # the program is swapped here, so it is not used.
        seen.append({"command": command, "args": list(args or []), "env": dict(env or {}), "cwd": cwd})
        return real(sys.executable, [str(FIXTURE)], env=env, cwd=cwd, request_timeout=request_timeout)

    monkeypatch.setattr(mcp_tools_mod, "StdioTransport", _transport)
    monkeypatch.setattr(mcp_tools_mod, "resolve_launcher", lambda command: command)
    monkeypatch.setattr(LP, "ensure_installed", _stand_in_install)
    return seen


@pytest.fixture
def client(tmp_path, monkeypatch, spawned):
    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    monkeypatch.setenv("IRONJARVIS_IRON_PROXY_BUNDLE", str(tmp_path / "no-bundle.mjs"))
    monkeypatch.delenv("IRONJARVIS_IRON_PROXY_NODE", raising=False)
    # The daemon's own environment carries none of the card's settings:
    # whatever a pane or a pack has, it got from the card.
    for var in ("ENABLED_TOOLS", catalog.MS365_TENANT_ENV, ms365.EMAIL_ENV, ms365.APP_ENV, ms365.CLOUD_ENV):
        monkeypatch.delenv(var, raising=False)
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


#: v1.428.0 review (moved on purpose): a work account needs the firm's own
#: app; these tests use a made-up one unless they name another.
FIRM_APP = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


def _values(answer: str, email: str = EMAIL, app: str = FIRM_APP) -> dict:
    values = {catalog.MS365_TENANT_ENV: answer, ms365.EMAIL_ENV: email}
    if app:
        values[ms365.APP_ENV] = app
    return values


def _connect(c: TestClient, answer: str, email: str = EMAIL, app: str = FIRM_APP):
    return c.post(f"/connectors/{CARD_ID}/connect", json={"values": _values(answer, email, app)})


# --------------------------------------------------------------------------- #
# The card
# --------------------------------------------------------------------------- #
def test_the_card_is_in_the_directory_with_one_plain_choice(client):
    """A new user finds it by name and is asked ONE question in words.
    v1.428.0 (moved on purpose): the choice is followed by the account's email
    (required: only that account can sign in) and the firm's app ID
    (optional), and the card promises read and draft only.
    MUTATION: drop the card from CATALOG -> StopIteration (red)."""
    row = _row(client)
    assert row["name"] == "Microsoft 365 (Outlook, Calendar, OneDrive)"
    assert row["connect_via"] == "mcp" and row["status"] == "disconnected"
    assert row["enabled"] is True
    assert row["signin"] is None, "nothing to sign in to before it is saved"
    assert row["runtime"] == "Node.js" and row["runtime_ready"] is True
    choice, email, app = row["fields"]
    assert choice["name"] == catalog.MS365_TENANT_ENV and choice["label"] == "Which Microsoft account?"
    assert choice["optional"] is False
    assert [(o["value"], o["label"]) for o in choice["options"]] == [
        ("common", "Work or school account"),
        ("consumers", "Personal account"),
    ]
    for o in choice["options"]:
        assert o["help"], "each answer says what it means"
    assert email["name"] == ms365.EMAIL_ENV and email["optional"] is False and "options" not in email
    assert app["name"] == ms365.APP_ENV and app["optional"] is True and "options" not in app
    # Read and draft only, in words: nothing on the card promises to send
    # mail or change the calendar.
    assert row["scopes"] == [
        "Read calendar events",
        "Read email and save drafts (never sends)",
        "Read contacts",
        "Read OneDrive files",
    ]
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
    v1.428.0 (moved on purpose): the filter the card saves is the locked
    one (``ms365.SAFE_ENABLED_TOOLS``: read and draft only), so that is the
    one checked; nothing that sends or changes the calendar passes it.
    MUTATION: widen the filter to a preset's names -> red."""
    fixture = _fixture_module()
    names = [n for n, _ro in fixture.TOOLS]
    assert len(names) == 119 and "get-calendar-view" in names
    rx = re.compile(ms365.SAFE_ENABLED_TOOLS, re.IGNORECASE)
    # v1.428.0 review: the pack never offers the account-switching tools.
    from iron_jarvis.mcp.tools import LOCKED_HIDDEN_TOOLS

    shown_signin = set(SIGNIN_TOOLS) - LOCKED_HIDDEN_TOOLS
    kept = sorted(n for n in names if n in shown_signin or rx.search(n))
    assert set(kept) == set(ms365.SAFE_TOOLS) | shown_signin
    assert len(kept) == TOOLS_LOADED <= chat_turn._MAX_CONNECTOR_TOOLS
    assert "get-calendar-view" in kept[: chat_turn._MAX_CONNECTOR_TOOLS]
    for never in ("send-mail", "send-draft-message", "reply-mail-message", "create-calendar-event",
                  "update-calendar-event", "delete-mail-message"):
        assert never not in kept, never
    # Every tool the card names is a real one (a renamed tool would vanish).
    assert set(ms365.SAFE_TOOLS) <= set(names)


def test_nothing_a_windows_launcher_would_read_rides_the_command_line():
    """npx was a .cmd run through cmd.exe: `|`, `^`, `&`, `<`, `>` in an
    argument would be read by cmd itself. The tool filter is a regex FULL of
    `|`, so it rides the environment.
    v1.428.0 (moved on purpose): the card runs ``node`` (a real program, no
    .cmd) with ONE argument — the locked program's path — and the filter
    still rides the settings the card's rules make, never an argument.
    MUTATION: put the filter in `args` -> red."""
    card = _card()
    assert card.command == "node" and card.args == []
    assert card.env == {}, "the locked card's settings come from its rules, not a fixed env"
    env = ms365.env_for(_values("common"))
    assert env["ENABLED_TOOLS"] == ms365.SAFE_ENABLED_TOOLS
    assert set(ms365.SAFE_ENABLED_TOOLS) & set("|^")


def test_the_pack_settings_are_never_taken_for_secrets():
    """None of the settings may be vaulted: the sign-in pane reads them off
    the saved pack, and a vaulted copy would make the saved pack and the
    sign-in disagree. MUTATION: name the tenant field ..._TOKEN -> red."""
    from iron_jarvis.mcp.secrets import is_secret_key

    for key in ("ENABLED_TOOLS", catalog.MS365_TENANT_ENV, ms365.EMAIL_ENV, ms365.APP_ENV, ms365.CLOUD_ENV):
        assert not is_secret_key(key), key


# --------------------------------------------------------------------------- #
# Connect, through the one add door
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("answer, key", [("consumers", "personal"), ("common", "work")])
def test_connect_saves_the_pack_through_the_one_door_and_loads_the_24_tools(
    client, spawned, monkeypatch, answer, key
):
    """v1.428.0 (moved on purpose): the one door saves the LOCKED row — the
    program from Iron Jarvis's lock, run from its own folder, the card's rules'
    settings, ``local_only`` + ``locked`` — and the pack loads the read and
    draft tools (``TOOLS_LOADED``), no longer the 24 of the old filter.
    MUTATION: drop the env from locked_body -> the pack loads all 119 tools
    and the stored env misses the filter (red)."""
    door_calls: list[str] = []
    real_add = pack_door.add_pack

    def _spy(platform, body, **kw):
        door_calls.append(body.name)
        return real_add(platform, body, **kw)

    monkeypatch.setattr(pack_door, "add_pack", _spy)
    r = _connect(client, answer)
    assert r.status_code == 200, r.text
    assert r.json()["tools_loaded"] == TOOLS_LOADED
    assert door_calls == [CARD_ID], "the Directory's Connect goes through mcp/pack_door"

    home = _platform(client).config.home
    entry = LP.entry_path(LP.MS365, home)
    cfg = _saved(client)
    assert cfg is not None
    assert cfg["command"] == "node" and cfg["args"] == [str(entry)]
    assert cfg["cwd"] == str(LP.install_dir(LP.MS365, home))
    assert cfg["env"] == ms365.env_for(_values(answer), LP.signin_dir(LP.MS365, home))
    # v1.428.0 review (moved on purpose): a work account signs in at the
    # firm's own directory (its email's domain), never Microsoft's shared one.
    assert cfg["env"][catalog.MS365_TENANT_ENV] == (
        answer if answer == catalog.MS365_PERSONAL else ms365.email_domain(EMAIL)
    )
    assert ms365.kind_of(cfg["env"]) == key
    assert cfg["local_only"] is True and cfg["locked"] == "ms365"
    assert not cfg.get("env_secrets") and not cfg.get("arg_secrets")
    # What the pack's process was started with — the settings, unmasked.
    launched = spawned[-1]
    assert launched["command"] == "node" and launched["args"] == [str(entry)]
    assert launched["cwd"] == cfg["cwd"], "the program runs from its own folder"
    assert launched["env"]["ENABLED_TOOLS"] == ms365.SAFE_ENABLED_TOOLS
    assert launched["env"][catalog.MS365_TENANT_ENV] == (
        answer if answer == catalog.MS365_PERSONAL else ms365.email_domain(EMAIL)
    )
    assert launched["env"][ms365.EMAIL_ENV] == EMAIL
    toml = (Path(home) / "config.toml").read_text(encoding="utf-8")
    assert catalog.MS365_TENANT_ENV in toml and "get-calendar-view" in toml


def test_a_typed_answer_is_refused_and_so_is_none(client, spawned):
    """The value is what the pack reads; a guessed one ("work") would sign in
    the wrong way. v1.428.0 (moved on purpose): the card's rules
    (``ms365.env_for``) refuse it in their own words, and an empty card names
    both required answers. MUTATION: drop the kind check in env_for -> saved."""
    r = _connect(client, "work")
    assert r.status_code == 400
    assert r.json()["detail"] == ms365.BAD_KIND
    r = client.post(f"/connectors/{CARD_ID}/connect", json={"values": {}})
    assert r.status_code == 400
    assert "Which Microsoft account?" in r.json()["detail"] and "Your Microsoft email" in r.json()["detail"]
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
    the Microsoft authority the pack will use.
    v1.428.0 (moved on purpose): ONE sign-in job; the line typed is the SAVED
    pack's own program (its path on this PC) quoted for the pane's shell, and
    the pane starts in the program's own folder.
    MUTATION: drop the env hand-off in launch_pane -> the shell has no
    ENABLED_TOOLS (red)."""
    # Whatever pane this suite itself runs in, the sign-in pane's CLI marker
    # must come from the launch alone.
    monkeypatch.delenv("IRONJARVIS_PANE_CLI", raising=False)
    assert _connect(client, answer).status_code == 200
    row = _row(client)
    # v1.358.0 (lane 8D, moved on purpose): its tools load, but nobody has
    # signed in to Microsoft yet, so the card reads "signin" — never the
    # green "connected" it read here before (tests/test_connections_honest_v13580.py).
    assert row["status"] == "signin" and row["tools_loaded"] == TOOLS_LOADED
    signin = row["signin"]
    assert signin == {
        "cli": SIGNIN_JOB,
        "label": "Sign in to Microsoft",
        "account": account,
    }

    r = client.post("/terminals/launch", json={"cli": signin["cli"]})
    assert r.status_code == 200, r.text
    pane = r.json()
    assert pane["name"] == "Microsoft sign-in"
    assert pane.get("agent_cli") in (None, ""), "an app sign-in is not an AI CLI"
    # review: the row's `agent_cli` is the CLASSIFIER's answer (an unknown id
    # reads as None either way), so pin what the launch STORED — the pane is
    # a plain shell (no CLI marker, no Resume offer after a restart).
    # MUTATION: the old `cat.get("agent_cli") or cli_id` -> red.
    session = _platform(client).terminals.get(pane["id"])
    assert session is not None and session.agent_cli is None

    shell = _backend_of(pane["id"])
    assert "IRONJARVIS_PANE_CLI" not in shell.env
    cfg = _saved(client)
    argv = ["node", *cfg["args"], "--login"]
    assert shell.written == [shell_line(session.shell, argv) + "\r"]
    assert session.cwd == cfg["cwd"], "the sign-in runs from the program's own folder"
    for k, v in cfg["env"].items():
        assert shell.env.get(k) == v, k
    # The daemon's own environment is still under it (PATH and the rest).
    assert shell.env.get("PATH") == os.environ.get("PATH")


def test_the_sign_in_terminal_never_inherits_the_daemons_microsoft_settings(client, monkeypatch):
    """v1.428.0 review (b): the Sign in terminal starts from the daemon's
    environment WITHOUT the settings the program must only get from Iron
    Jarvis — a stray ``MS365_MCP_*`` (an OAuth token that skips the account
    lock, a command that stores the sign-in), a wider ENABLED_TOOLS or
    NODE_OPTIONS. MUTATION: build the job's base from ``os.environ`` -> red."""
    stray = {"MS365_MCP_OAUTH_TOKEN": "stray", "MS365_MCP_AUTH_CACHE_COMMAND": "calc.exe",
             "ENABLED_TOOLS": ".*", "NODE_OPTIONS": "--require C:/evil.js"}
    assert _connect(client, "common").status_code == 200
    for k, v in stray.items():
        monkeypatch.setenv(k, v)
    r = client.post("/terminals/launch", json={"cli": SIGNIN_JOB})
    assert r.status_code == 200, r.text
    shell = _backend_of(r.json()["id"])
    got = {str(k).upper(): v for k, v in shell.env.items()}
    saved = _saved(client)["env"]
    for k in stray:
        assert got.get(k.upper()) == saved.get(k.upper()), k
    assert got.get("PATH") == os.environ.get("PATH")


def test_the_sign_in_needs_no_account_and_says_no_to_one(client):
    """It never runs on an Iron-Proxy account (it is not an AI CLI).
    v1.428.0 (moved on purpose): connected first — before that, the one job
    answers "Connect Microsoft 365 on its card … first"."""
    assert _connect(client, "common").status_code == 200
    r = client.post("/terminals/launch", json={"cli": SIGNIN_JOB, "account": "default"})
    assert r.status_code == 400
    assert "does not run on an Iron-Proxy account" in r.json()["detail"]


def test_a_sign_in_whose_card_cannot_be_read_starts_nothing(client, monkeypatch):
    """A sign-in with the wrong settings would ask Microsoft for the wrong
    account kind: refuse instead. v1.428.0 (moved on purpose): the one job; a
    card that is not saved yet starts nothing either.
    MUTATION: swallow the LookupError -> 200."""
    r = client.post("/terminals/launch", json={"cli": SIGNIN_JOB})
    assert r.status_code == 409 and r.json()["detail"].startswith("Connect Microsoft 365 on its card")
    monkeypatch.setattr(catalog, "get_connector", lambda _cid: None)
    r = client.post("/terminals/launch", json={"cli": SIGNIN_JOB})
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
    v1.428.0 (moved on purpose): ONE job for the locked card.
    MUTATION: change the typed command in SETUP_LAUNCHES -> red."""
    clis = {c["id"] for c in client.get("/terminals/ai-clis").json()["clis"]}
    rows = catalog.signin_launches()
    assert [r["id"] for r in rows] == [SIGNIN_JOB]
    for r in rows:
        assert r["id"] not in clis
        entry = ai_clis.launch_entry(r["id"])
        assert entry is not None and entry in ai_clis.SETUP_LAUNCHES
        assert {k: v for k, v in entry.items() if k != "agent_cli"} == r
        assert entry["agent_cli"] == "" and entry["choice"] == ""
        assert entry["command"] == " ".join(catalog.signin_argv(_card()))
    assert not {r["id"] for r in rows} & {c["id"] for c in ai_clis.AI_CLIS}


def test_the_sign_in_is_typed_by_the_full_path_of_node_quoted_for_the_shell(client, monkeypatch):
    """v1.428.0 (moved on purpose; was "typed by full path when npx predates
    the daemon"): there is no npx now. Node installed while Iron Jarvis was
    running is still found: the sign-in types the program by the full path
    ``mcp.tools.resolve_launcher`` finds (the pack's own launcher rule), quoted
    for the pane's shell so a path with a space runs that program.
    MUTATION: type the saved command as it is ("node") -> red."""
    assert _connect(client, "common").status_code == 200
    node = r"C:\Program Files\nodejs\node.exe"
    monkeypatch.setattr(mcp_tools_mod, "resolve_launcher", lambda command: node if command == "node" else command)
    cfg = _saved(client)
    argv, env, cwd = service.locked_signin(_platform(client), CARD_ID)
    assert argv == [node, *cfg["args"], "--login"]
    assert env == cfg["env"] and cwd == cfg["cwd"]
    assert shell_line("pwsh", argv).startswith("& '" + node + "' ")
    assert shell_line("cmd", argv).startswith('"' + node + '" ')
    r = client.post("/terminals/launch", json={"cli": SIGNIN_JOB})
    assert r.status_code == 200, r.text
    session = _platform(client).terminals.get(r.json()["id"])
    assert _backend_of(r.json()["id"]).written == [shell_line(session.shell, argv) + "\r"]


# --------------------------------------------------------------------------- #
# Chat can answer what is on the calendar tomorrow
# --------------------------------------------------------------------------- #
def test_chat_arms_every_tool_and_the_calendar_answers_tomorrow(client):
    """The acceptance's end, at the seams chat uses: turning the app on for a
    chat (+ › Connections) arms its whole group — the day view included — and
    the pack's day view answers. The pack was started with the account kind
    the user chose.
    v1.428.0 (moved on purpose): the group is the read and draft tools (no
    sending), and the day view answers only in a turn on the user's own models
    (Local only) — outside it the tool refuses, which is the contract.
    MUTATION: widen the filter past 24 -> the day view falls out of the armed
    group (red)."""
    assert _connect(client, "consumers").status_code == 200
    d = client.app.state.d
    tools, memory = chat_turn._resolve_connectors(d, SimpleNamespace(connectors=[CARD_ID]))
    assert memory == []
    assert len(tools) == TOOLS_LOADED
    day = f"mcp__{CARD_ID}__get-calendar-view"
    assert day in tools
    from iron_jarvis.mcp.tools import LOCKED_HIDDEN_TOOLS

    for name in SIGNIN_TOOLS:
        # v1.428.0 review: the account-switching tools are never offered.
        assert (f"mcp__{CARD_ID}__{name}" in tools) is (name not in LOCKED_HIDDEN_TOOLS)
    assert f"mcp__{CARD_ID}__send-mail" not in tools, "read and draft only"

    tool = _platform(client).registry.get(day)
    assert tool is not None and not tool.write_like, "reading the calendar changes nothing"
    assert tool.local_only is True
    # v1.428.0 review (moved on purpose): a plain chat turn (session "chat").
    outside = asyncio.run(tool.execute({}, SimpleNamespace(event_bus=None, session_id="chat")))
    assert not outside.ok and "stay on your own models" in (outside.error or "")

    async def _local():
        with egress.local_only_scope(True):
            return await tool.execute({}, SimpleNamespace(event_bus=None, session_id="chat"))

    result = asyncio.run(_local())
    assert result.ok, result.error
    answer = json.loads(re.sub(r"(?s)^.*?(\{.*\}).*$", r"\1", result.output))
    assert answer["value"][0]["subject"] == "Dentist appointment"
    assert answer["tenant"] == "consumers", "the pack signs in as the account kind chosen"


# --------------------------------------------------------------------------- #
# What the row says
# --------------------------------------------------------------------------- #
def _cfg(home: Path, **over) -> dict:
    """The row a Connect of the work answer saves (v1.428.0: the locked one)."""
    base = {
        "name": CARD_ID,
        "command": "node",
        "args": [str(LP.entry_path(LP.MS365, home))],
        "env": ms365.env_for(_values("common"), LP.signin_dir(LP.MS365, home)),
        "cwd": str(LP.install_dir(LP.MS365, home)),
        "local_only": True,
        "locked": "ms365",
    }
    base.update(over)
    return base


def test_a_pack_changed_by_hand_gets_no_sign_in_press(tmp_path):
    """The press is offered only while the login would run exactly what the
    pack runs. v1.428.0 (moved on purpose): "exactly" is now the locked row —
    its program from the lock, its own folder, the card's rules' settings,
    ``local_only`` and ``locked``. MUTATION: drop the env equality check -> a
    press for a pack whose filter differs (red)."""
    card = _card()
    assert service.signin_row(card, _cfg(tmp_path)) == {
        "cli": SIGNIN_JOB,
        "label": "Sign in to Microsoft",
        "account": "Work or school account",
    }
    env = ms365.env_for(_values("common"), LP.signin_dir(LP.MS365, tmp_path))
    assert service.signin_row(card, _cfg(tmp_path, env={**env, "ENABLED_TOOLS": ".*"})) is None
    assert service.signin_row(card, _cfg(tmp_path, env={**env, ms365.APP_ENV: "x"})) is None
    assert service.signin_row(card, _cfg(tmp_path, env={**env, catalog.MS365_TENANT_ENV: "my-tenant"})) is None
    assert service.signin_row(card, _cfg(tmp_path, env={**env, ms365.EMAIL_ENV: ""})) is None
    entry = str(LP.entry_path(LP.MS365, tmp_path))
    assert service.signin_row(card, _cfg(tmp_path, args=[entry, "--org-mode"])) is None
    assert service.signin_row(card, _cfg(tmp_path, args=["-y", catalog.MS365_PACKAGE])) is None
    assert service.signin_row(card, _cfg(tmp_path, command="npx")) is None
    assert service.signin_row(card, _cfg(tmp_path, cwd=str(tmp_path))) is None
    assert service.signin_row(card, _cfg(tmp_path, local_only=False)) is None
    assert service.signin_row(card, _cfg(tmp_path, locked="")) is None
    assert service.signin_row(card, _cfg(tmp_path, env_secrets={"X_TOKEN": "mcp.microsoft_365.X_TOKEN"})) is None
    assert service.signin_row(card, None) is None
    assert service.signin_row(catalog.get_connector("notion"), _cfg(tmp_path)) is None


def test_a_saved_pack_is_changed_only_from_its_card_and_an_edit_by_hand_loses_the_press(client):
    """v1.428.0 (new, beside the one above): the Tools page's edit of the
    saved pack's settings is refused (409); a row changed by hand in the
    config (the only way left) gets no Sign in press, and its sign-in job
    starts nothing."""
    assert _connect(client, "common").status_code == 200
    assert _row(client)["signin"]["cli"] == SIGNIN_JOB
    r = client.patch(f"/mcp/servers/{CARD_ID}", json={"env": {"ENABLED_TOOLS": ".*"}})
    assert r.status_code == 409 and "Disconnect" in r.json()["detail"]
    platform = _platform(client)
    platform.config.mcp_servers = [
        dict(s, env={**s["env"], "ENABLED_TOOLS": ".*"}) if s.get("name") == CARD_ID else s
        for s in platform.config.mcp_servers
    ]
    assert _row(client)["signin"] is None
    r = client.post("/terminals/launch", json={"cli": SIGNIN_JOB})
    assert r.status_code == 409 and r.json()["detail"].startswith("Connect Microsoft 365 on its card")


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
    assert row["signin"]["cli"] == SIGNIN_JOB, "signing in does not need it on"
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
def test_the_tools_catalog_never_names_the_locked_package(client):
    """Not mirrored on purpose: the Tools page's add form has no account
    choice and no Sign in, so a row there would add a pack that could never
    sign in from that page. v1.428.0 (moved on purpose; was "agrees if it
    ever names the package"): a row there would also run the package with
    ``npx`` at its newest instead of Iron Jarvis's lock, so it must never
    name it at all."""
    rows = client.get("/mcp/catalog").json()["catalog"]
    for row in rows:
        assert catalog.MS365_PACKAGE not in json.dumps(row), row


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
