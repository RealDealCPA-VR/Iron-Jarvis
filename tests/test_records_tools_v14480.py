"""v1.448.0 (Wave A "chat configures everything", item 4): tool servers and
web services from chat — ``settings/records_tools``.

Every change test drives the REAL stream lane (``/chat/stream``), the real
permission gate (an ask-tier call pauses on the approval card, answered
"Allow once" through ``/chat/approvals``), the real ``registry.invoke``, the
one pack door (``mcp/pack_door.add_pack``, reached through the import route's
own ``_apply_specs``) with a REAL stdio MCP child (``tests/fixtures/
argv_mcp_server_v13510.py``: no npx, no network), the real credential store
(``POST /config/secret``) and the real Undo route (``POST /undo/{id}``, found
through the card's ``change_id``).

Pinned here:

* a server's card is ONCE-ONLY and shows the command line it will start;
* a credential found IN the config with a value is refused before any card;
  one left blank is asked on the secure card, and the server is added ONLY
  once that card is saved — the row the add stores is exactly what the plan
  predicted (``Plan.expected`` == ``comparable(row)``), so the Undo's "changed
  since" check is honest;
* Undo removes the server THIS call made, refuses one edited since, and
  withdraws a card not yet saved;
* every refusal in words (a locked card's program, a name in use, the same
  server under another name, a missing launcher, a network folder, a blank
  the model must fill);
* a web service with a private address is saved with ``allow_internal`` and
  its card says where it points; its key only on the secure card; a share or
  device spelling refused by its text; Undo removes exactly it;
* ``POST /integrations`` (the page's door) stores the same shape as before;
* SIX sentinel secrets through every door reach no frame, no ledger row,
  no config.toml and no log line;
* the arming table both ways, the stream lane ASK-arms and the POST lane
  never arms; the matchers are linear on pathological input;
* the honesty note knows the two kinds.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.settings import records as R
from iron_jarvis.settings import records_tools as RT
from iron_jarvis.tools.autoselect import select_ask_tools
from tests.test_chat_approvals_v1187 import _asgi_post, _drive_stream
from tests.test_records_connections_v14380 import _card, _db_text, _finished, _text, _undo
from tests.test_settings_chat_tools_v1321 import _body, _calls_stream, _done, _undo_card

pytestmark = pytest.mark.usefixtures("no_network")

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = str(ROOT / "tests" / "fixtures" / "argv_mcp_server_v13510.py")
WRITERS = ("mcp_server_add", "web_service_add")

#: Six made-up credentials, each built from pieces so GitHub's secret scan
#: never reads a shape it knows (tests/test_no_secret_shaped_values_v14340).
SENTINELS = {
    "env": "sentinel-env-" + "v14480" + "-abc",
    "header": "sentinel-header-" + "v14480" + "-def",
    "auth": "sentinel-auth-" + "v14480" + "-ghi",
    "arg": "sentinel-arg-" + "v14480" + "-jkl",
    "url": "sentinel-url-" + "v14480" + "-mno",
    "service": "sentinel-service-" + "v14480" + "-pqr",
}


# =========================================================================== #
# Harness
# =========================================================================== #
def _app(tmp_path, specs):
    app = create_app(str(tmp_path / "ij"))
    app.state.platform.router.stream = _calls_stream(specs)
    return app


async def _approve_all(app, seen):
    async def approve(data):
        seen.append((data["tool"], data))
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "once"})
        assert status == 200

    return approve


async def _deny_all(app, seen):
    async def deny(data):
        seen.append((data["tool"], data))
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "deny"})
        assert status == 200

    return deny


async def _drive_no_card(app, body):
    seen: list = []
    frames = await _drive_stream(app, body, await _deny_all(app, seen))
    assert seen == [], f"these calls must not card: {[t for t, _ in seen]}"
    return frames


def _stdio_config(name: str = "argv-test", *, env: dict | None = None, extra_args: list | None = None) -> str:
    """A pasted config block (the Claude Desktop shape) that launches the
    REAL stdio child."""
    entry: dict = {"command": sys.executable, "args": [FIXTURE, *(extra_args or [])]}
    if env is not None:
        entry["env"] = env
    return json.dumps({"mcpServers": {name: entry}})


def _everything(app, frames, caplog) -> str:
    p = app.state.platform
    home = Path(p.config.home)
    parts = [_text(frames), _db_text(p), caplog.text]
    toml = home / "config.toml"
    if toml.is_file():
        parts.append(toml.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


@pytest.fixture(autouse=True)
def _fresh_pending():
    with RT._PENDING_LOCK:
        RT._PENDING.clear()
    RT.tools_tools_for.cache_clear()
    yield
    with RT._PENDING_LOCK:
        RT._PENDING.clear()


# =========================================================================== #
# Registration
# =========================================================================== #
def test_every_tool_is_registered_ask_tier_low_trust_denied_and_worded(tmp_path):
    from iron_jarvis.agents import mission
    from iron_jarvis.core.trust import LOW_TRUST_DENY
    from iron_jarvis.daemon import change_claims as CC

    app = create_app(str(tmp_path / "ij"))
    p = app.state.platform
    for name in WRITERS:
        assert p.registry.get(name) is not None, name
        assert R.RECORD_TOOL_TIERS[name] == RT.TOOLS_TOOL_TIERS[name] == "ask"
        assert p.permissions.authorize(name, {}).allowed is False, f"{name} must ask"
        assert name in LOW_TRUST_DENY
        assert name in R.RECORD_CARD_TOOLS
        assert name in mission.ASK_WORDS and name in mission._RUN_TOOL_WORDS
        assert mission._PRESENT.get(mission._RUN_TOOL_WORDS[name][0].split()[0])
    words = (ROOT / "dashboard" / "lib" / "toolWords.ts").read_text(encoding="utf-8")
    run = (ROOT / "dashboard" / "lib" / "runWords.ts").read_text(encoding="utf-8")
    for name in WRITERS:
        assert f'  {name}: "{mission.ASK_WORDS[name]}",' in words
        did = mission._RUN_TOOL_WORDS[name][0]
        assert f'{name}: {{ did: "{did[0].upper()}{did[1:]}"' in run
    for kind in RT.KINDS:
        assert kind in R._SNAP and kind in CC._RECORD_TOOLS and kind in CC._RECORD_NOUN
    assert "mcp_server_add" in CC._RECORD_TOOLS["tool_server"]
    assert "web_service_add" in CC._RECORD_TOOLS["web_service"]
    # Every tool declares its arguments as input_schema (the v1.341.0 rule).
    for name in WRITERS:
        spec = p.registry.get(name).spec()
        assert spec["input_schema"]["type"] == "object" and spec["input_schema"]["properties"]


# =========================================================================== #
# mcp_server_add — the happy path, the card, the Undo
# =========================================================================== #
@pytest.mark.asyncio
async def test_adding_a_server_asks_once_only_shows_the_command_adds_it_and_undo_removes_it(tmp_path):
    app = _app(tmp_path, [("mcp_server_add", {"config": _stdio_config("argv-test", extra_args=["hello"])})])
    p = app.state.platform
    seen: list = []
    frames = await _drive_stream(
        app, _body("add this MCP server to Jarvis: " + _stdio_config()), await _approve_all(app, seen)
    )
    assert [t for t, _ in seen] == ["mcp_server_add"], "adding a server must ask first"
    card_text = _text(seen[0][1])
    assert seen[0][1].get("once_only") is True, "the card is once-only"
    assert "it starts the program" in card_text and "argv_mcp_server_v13510" in card_text
    row = R.snap_app(p, "argv-test")
    assert row is not None and row["command"] == sys.executable and row["args"][-1] == "hello"
    assert p.registry.get("mcp__argv-test__argv") is not None, "its tools loaded through the one door"
    out = _text(_finished(frames, "mcp_server_add"))
    assert "Added the tool server 'argv-test'" in out and "1 tool" in out
    card = _card(frames, "Tool server added")
    assert card["key"] == "tool_server.argv-test"
    # The row the add stored is exactly what the plan predicted.
    plan = RT.Plan(p, {"config": _stdio_config("argv-test", extra_args=["hello"])})
    assert plan.problem == NAME_IN_USE_WORDS(plan)  # a second plan sees the name in use
    await _undo_card(app, card)
    assert R.snap_app(p, "argv-test") is None, "Undo removed the server this call made"
    assert p.registry.get("mcp__argv-test__argv") is None


def NAME_IN_USE_WORDS(plan):
    return RT.NAME_IN_USE.format(name=plan.name or "argv-test")


def test_the_plans_expected_row_is_what_the_door_stores(tmp_path):
    """The Undo's "changed since" check compares the live row with the row
    the plan PREDICTED — pinned equal to what ``add_pack`` really stored."""
    app = create_app(str(tmp_path / "ij"))
    p = app.state.platform
    plan = RT.Plan(p, {"config": _stdio_config("pred", env={"MODE": "fast", "API_KEY": ""}, extra_args=["a", "b"])})
    assert plan.problem == "", plan.problem
    assert plan.slots == ["env__API_KEY"]
    expected = plan.expected()
    result = RT._add_through_door(p, plan.filled_spec({"env__API_KEY": "k-" + "v14480"}))
    assert result and result.get("added"), result
    assert RT.comparable(R.snap_app(p, "pred")) == expected


@pytest.mark.asyncio
async def test_a_key_in_the_config_is_refused_before_any_card_and_reaches_nothing(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    cfg = _stdio_config("leaky", env={"API_KEY": SENTINELS["env"]})
    app = _app(tmp_path, [("mcp_server_add", {"config": cfg, "name": "leaky"})])
    p = app.state.platform
    frames = await _drive_no_card(app, _body("add this MCP server: " + cfg))
    out = _text(_finished(frames, "mcp_server_add"))
    assert "never passed in the chat" in out
    assert R.snap_app(p, "leaky") is None
    assert SENTINELS["env"] not in _everything(app, frames, caplog)


@pytest.mark.asyncio
async def test_a_blank_key_is_asked_on_the_secure_card_and_the_server_is_added_only_once_saved(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    cfg = _stdio_config("keyed", env={"API_KEY": "<your key>", "MODE": "fast"})
    app = _app(tmp_path, [("mcp_server_add", {"config": cfg})])
    p = app.state.platform
    seen: list = []
    frames = await _drive_stream(app, _body("add this MCP server: " + cfg), await _approve_all(app, seen))
    assert [t for t, _ in seen] == ["mcp_server_add"]
    assert "API_KEY" in _text(seen[0][1]) and "secure card" in _text(seen[0][1])
    assert R.snap_app(p, "keyed") is None, "nothing is added before the card is saved"
    assert not (Path(p.config.home) / "config.toml").exists() or "keyed" not in (
        Path(p.config.home) / "config.toml"
    ).read_text(encoding="utf-8")
    out = _text(_finished(frames, "mcp_server_add"))
    assert "Nothing is added yet" in out
    done = _done(frames)
    secure = [c for c in done["config_cards"] if c.get("kind") == "secret"]
    assert [c["name"] for c in secure] == ["tool_server.keyed__env__API_KEY"]
    waiting = _card(frames, "Tool server waiting for its key")
    # The card is saved: the server is added with THAT value, through the door.
    status, raw = await _asgi_post(app, "/config/secret", {"name": secure[0]["name"], "value": SENTINELS["env"]})
    assert status == 200, raw
    answer = json.loads(raw)
    assert answer["tools_loaded"] == 1 and answer["app"] == "keyed", answer
    row = R.snap_app(p, "keyed")
    assert row is not None and row["env_secrets"] == {"API_KEY": "mcp.keyed.API_KEY"} and row["env"] == {"MODE": "fast"}
    assert p.secrets.get("mcp.keyed.API_KEY") == SENTINELS["env"]
    assert p.registry.get("mcp__keyed__argv") is not None
    everything = _everything(app, frames, caplog)
    assert SENTINELS["env"] not in everything, "the value is in the vault and nowhere else"
    # Undo (the card the reply carried) removes the server and its key.
    await _undo_card(app, waiting)
    assert R.snap_app(p, "keyed") is None and p.secrets.get("mcp.keyed.API_KEY") is None


@pytest.mark.asyncio
async def test_undo_before_the_card_is_saved_withdraws_it(tmp_path):
    cfg = _stdio_config("waiting", env={"API_KEY": ""})
    app = _app(tmp_path, [("mcp_server_add", {"config": cfg})])
    p = app.state.platform
    seen: list = []
    frames = await _drive_stream(app, _body("add this MCP server: " + cfg), await _approve_all(app, seen))
    card = _card(frames, "Tool server waiting for its key")
    assert RT.pending_for("waiting") is not None
    status, text = _undo(app, card)
    assert status == 200 and "withdrew the card" in text, text
    assert RT.pending_for("waiting") is None and R.snap_app(p, "waiting") is None
    # A card saved after the withdrawal adds nothing and says so.
    status, raw = await _asgi_post(
        app, "/config/secret", {"name": "tool_server.waiting__env__API_KEY", "value": "late-" + "v14480"}
    )
    assert status == 200
    assert "no longer waiting" in json.loads(raw).get("load_reason", "")
    assert R.snap_app(p, "waiting") is None


@pytest.mark.asyncio
async def test_undo_over_a_server_edited_since_refuses_and_keeps_it(tmp_path):
    app = _app(tmp_path, [("mcp_server_add", {"config": _stdio_config("edited")})])
    p = app.state.platform
    seen: list = []
    frames = await _drive_stream(app, _body("add this MCP server: " + _stdio_config()), await _approve_all(app, seen))
    card = _card(frames, "Tool server added")
    from iron_jarvis.mcp.pack_door import install_config

    row = dict(R.snap_app(p, "edited"))
    row["args"] = [*row["args"], "--verbose"]
    install_config(p, row, load=False)
    status, text = _undo(app, card)
    assert status == 409 and "changed since" in text, text
    assert R.snap_app(p, "edited") is not None, "an edited server is never removed by an Undo"


@pytest.mark.asyncio
async def test_undo_of_a_server_removed_since_says_so(tmp_path):
    app = _app(tmp_path, [("mcp_server_add", {"config": _stdio_config("gone")})])
    p = app.state.platform
    seen: list = []
    frames = await _drive_stream(app, _body("add this MCP server: " + _stdio_config()), await _approve_all(app, seen))
    card = _card(frames, "Tool server added")
    from iron_jarvis.mcp.pack_door import remove_pack

    remove_pack(p, "gone")
    status, text = _undo(app, card)
    assert status == 409 and "never added (or was removed since)" in text, text


# =========================================================================== #
# mcp_server_add — refusals, each in words and with no card
# =========================================================================== #
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args, words",
    [
        ({"config": "", "name": "x"}, "Give the server's config"),
        ({"config": "npx -y ms-365-mcp-server", "name": "m365"}, "set up only from its card"),
        ({"config": json.dumps({"mcpServers": {"nolauncher": {"command": "npx-no-such-launcher-v14480", "args": ["-y", "@acme/server"]}}})}, "cannot start on this PC"),
        ({"config": _stdio_config("netcwd"), "cwd": r"\\fileserver\share\tools"}, "network share"),
        ({"config": "filesystem"}, "leaves folder to be filled in"),
        ({"config": _stdio_config("v"), "values": {"api_key": "x"}}, "is a key or token"),
        ({"config": _stdio_config("v2"), "token": SENTINELS["auth"]}, "never passed in the chat"),
        ({"config": "npx -y @acme/server --api-key " + SENTINELS["arg"], "name": "argkey"}, "never passed in the chat"),
        ({"config": "https://mcp.example.com/sse?api_key=" + SENTINELS["url"], "name": "urlkey"}, "never passed in the chat"),
    ],
)
async def test_a_refused_add_says_why_and_shows_no_card(tmp_path, args, words, caplog):
    caplog.set_level(logging.DEBUG)
    app = _app(tmp_path, [("mcp_server_add", args)])
    p = app.state.platform
    before = list(p.config.mcp_servers or [])
    frames = await _drive_no_card(app, _body("add this MCP server please"))
    out = _text(_finished(frames, "mcp_server_add"))
    assert words in out, out
    assert list(p.config.mcp_servers or []) == before, "nothing was added"
    for value in (SENTINELS["auth"], SENTINELS["arg"], SENTINELS["url"]):
        assert value not in _everything(app, frames, caplog)


@pytest.mark.asyncio
async def test_a_name_in_use_and_the_same_server_under_another_name_are_refused(tmp_path):
    cfg = _stdio_config("first")
    app = _app(tmp_path, [
        ("mcp_server_add", {"config": cfg}),
        ("mcp_server_add", {"config": cfg}),
        ("mcp_server_add", {"config": _stdio_config("second")}),
    ])
    p = app.state.platform
    seen: list = []
    frames = await _drive_stream(app, _body("add this MCP server: " + cfg), await _approve_all(app, seen))
    assert [t for t, _ in seen] == ["mcp_server_add"], "only the first add asks; the two refusals card nothing"
    outs = _finished(frames, "mcp_server_add")
    assert len(outs) == 3
    assert "already added" in _text(outs[1]) and "'first'" in _text(outs[1])
    assert "already added as 'first'" in _text(outs[2])
    assert [s["name"] for s in p.config.mcp_servers] == ["first"]


def test_a_catalog_name_with_its_values_filled_is_a_plan_and_a_bare_one_asks_for_them(tmp_path):
    app = create_app(str(tmp_path / "ij"))
    p = app.state.platform
    bare = RT.Plan(p, {"config": "filesystem"})
    assert "folder" in bare.problem and "values" in bare.problem
    filled = RT.Plan(p, {"config": "filesystem", "values": {"folder": r"C:\Clients"}})
    # npx may or may not be on this PC: either the plan stands, or the
    # launcher's own words refuse it — never a crash, never a key asked.
    assert filled.problem == "" or "cannot start on this PC" in filled.problem
    if not filled.problem:
        assert filled.filled_spec().args[-1] == r"C:\Clients" and filled.slots == []
        assert filled.expected()["args"][-1] == r"C:\Clients"


def test_resolve_names_a_waiting_packs_slot_and_a_bare_pack_its_token():
    with RT._PENDING_LOCK:
        RT._PENDING["my__pack"] = {"plan": None, "change_id": "", "at": 1e18, "saved": []}
    assert RT.resolve_tool_server_secret("my__pack__env__API_KEY") == ("my__pack", "env__API_KEY", "mcp.my__pack.API_KEY")
    assert RT.resolve_tool_server_secret("other__header__X-Api-Key")[2] == "mcp.other.header.X-Api-Key"
    assert RT.resolve_tool_server_secret("other__auth")[2] == "mcp.other.auth.value"
    assert RT.resolve_tool_server_secret("other__arg__2")[2] == "mcp.other.arg.2"
    assert RT.resolve_tool_server_secret("other__url")[2] == "mcp.other.url"
    assert RT.resolve_tool_server_secret("bare")[2] == "mcp.bare.TOKEN"
    with pytest.raises(ValueError):
        RT.resolve_tool_server_secret("other__nowhere__X")


# =========================================================================== #
# web_service_add
# =========================================================================== #
@pytest.mark.asyncio
async def test_adding_a_web_service_asks_says_where_it_points_and_undo_removes_it(tmp_path):
    app = _app(tmp_path, [("web_service_add", {
        "name": "Pricing", "address": "https://api.vrtax.local/v1", "description": "fee lookups",
    })])
    p = app.state.platform
    seen: list = []
    frames = await _drive_stream(
        app, _body("Hook up our firm's internal pricing API at https://api.vrtax.local/v1 so you can look up fees"),
        await _approve_all(app, seen),
    )
    assert [t for t, _ in seen] == ["web_service_add"], "saving a service must ask first"
    assert "on this PC or your own network" in _text(seen[0][1]), "the card says WHERE it points"
    spec = p.integrations.get_spec("pricing")
    assert spec is not None and spec.display_name == "Pricing" and spec.kind == "rest"
    cfg = p.integrations.stored_config("pricing")
    assert cfg == {"base_url": "https://api.vrtax.local/v1", "allow_internal": True}, cfg
    assert p.integrations.get_record("pricing").enabled is True
    assert [c["id"] for c in p.config.custom_integrations] == ["pricing"]
    out = _text(_finished(frames, "web_service_add"))
    assert "Added the web service" in out and "rest_call" in out
    card = _card(frames, "Web service added")
    assert card["key"] == "web_service.pricing"
    await _undo_card(app, card)
    assert p.integrations.get_spec("pricing") is None
    assert p.integrations.get_record("pricing") is None
    assert p.config.custom_integrations == []


@pytest.mark.asyncio
async def test_a_web_service_key_goes_on_the_secure_card_only_and_undo_removes_it_too(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    app = _app(tmp_path, [("web_service_add", {
        "name": "Pricing", "address": "https://api.example.com/v1", "needs_key": True,
    })])
    p = app.state.platform
    seen: list = []
    frames = await _drive_stream(
        app, _body("add a web service called Pricing at https://api.example.com/v1 with a bearer key"),
        await _approve_all(app, seen),
    )
    assert "leaves this PC" in _text(seen[0][1]), "a public address is said to be on the internet"
    cfg = p.integrations.stored_config("pricing")
    assert cfg == {"base_url": "https://api.example.com/v1", "auth_secret": "integration_pricing_token"}
    secure = [c for c in _done(frames)["config_cards"] if c.get("kind") == "secret"]
    assert [c["name"] for c in secure] == ["web_service.pricing"]
    status, raw = await _asgi_post(app, "/config/secret", {"name": "web_service.pricing", "value": SENTINELS["service"]})
    assert status == 200, raw
    assert p.secrets.get("integration_pricing_token") == SENTINELS["service"]
    assert SENTINELS["service"] not in _everything(app, frames, caplog)
    card = _card(frames, "Web service added")
    await _undo_card(app, card)
    assert p.integrations.get_spec("pricing") is None
    assert p.secrets.get("integration_pricing_token") is None, "the key this call asked for goes with it"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args, words",
    [
        ({"name": "Share", "address": r"\\fileserver\share\api"}, "network share or device path"),
        ({"name": "Share", "address": "//fileserver/api"}, "network share or device path"),
        ({"name": "Dev", "address": r"\\?\C:\api"}, "network share or device path"),
        ({"name": "Ftp", "address": "ftp://files.example.com"}, "starting with http:// or https://"),
        ({"name": "Pricing", "address": "https://api.example.com", "token": SENTINELS["service"]}, "never passed in the chat"),
        ({"name": "Pricing", "address": "https://me:" + SENTINELS["service"] + "@api.example.com"}, "password in it"),
        ({"name": "rest api", "address": "https://api.example.com"}, "already saved"),
    ],
)
async def test_a_refused_web_service_says_why_and_shows_no_card(tmp_path, args, words, caplog):
    caplog.set_level(logging.DEBUG)
    app = _app(tmp_path, [("web_service_add", args)])
    p = app.state.platform
    frames = await _drive_no_card(app, _body("hook up our pricing API please"))
    out = _text(_finished(frames, "web_service_add"))
    assert words in out, out
    assert p.config.custom_integrations == [] and p.integrations.get_spec("pricing") is None
    assert SENTINELS["service"] not in _everything(app, frames, caplog)


@pytest.mark.asyncio
async def test_a_name_or_address_already_saved_is_refused_and_undo_over_an_edit_refuses(tmp_path):
    app = _app(tmp_path, [
        ("web_service_add", {"name": "Pricing", "address": "https://api.example.com/v1"}),
        ("web_service_add", {"name": "pricing", "address": "https://other.example.com"}),
        ("web_service_add", {"name": "Fees", "address": "https://api.example.com/v1/"}),
    ])
    p = app.state.platform
    seen: list = []
    frames = await _drive_stream(
        app, _body("add a web service called Pricing at https://api.example.com/v1"), await _approve_all(app, seen)
    )
    assert [t for t, _ in seen] == ["web_service_add"]
    outs = _finished(frames, "web_service_add")
    assert "already saved" in _text(outs[1]) and "already saved as 'Pricing'" in _text(outs[2])
    card = _card(frames, "Web service added")
    p.integrations.configure("pricing", {"base_url": "https://api.example.com/v2"})
    status, text = _undo(app, card)
    assert status == 409 and "changed since" in text, text
    assert p.integrations.get_spec("pricing") is not None


def test_the_pages_door_stores_the_same_shape_as_before(tmp_path):
    """``POST /integrations`` goes through the lifted ``add_hookup``; its
    answer, the stored config, the vault row and the config entry are what
    they were."""
    app = create_app(str(tmp_path / "ij"))
    p = app.state.platform
    client = TestClient(app)
    r = client.post("/integrations", json={"name": "Acme CRM", "base_url": "https://crm.example.com/api", "auth_token": "tok-" + "v14480"})
    assert r.status_code == 200 and r.json() == {"id": "acme_crm", "added": True}
    assert p.integrations.stored_config("acme_crm") == {
        "base_url": "https://crm.example.com/api", "auth_secret": "integration_acme_crm_token",
    }
    assert p.secrets.get("integration_acme_crm_token") == "tok-" + "v14480"
    assert p.config.custom_integrations == [{"id": "acme_crm", "name": "Acme CRM", "description": ""}]
    r = client.post("/integrations", json={"name": "Acme CRM", "base_url": "https://crm.example.com/api"})
    assert r.status_code == 400 and "already exists" in r.text
    r = client.post("/integrations", json={"name": "", "base_url": "https://x.example.com"})
    assert r.status_code == 400 and "name is required" in r.text


# =========================================================================== #
# Arming: the table both ways, and which lane arms
# =========================================================================== #
ARM_ROWS = [
    ("Add the filesystem MCP server from the Claude docs", "mcp_server_add"),
    (r"add this server: npx -y @modelcontextprotocol/server-filesystem C:\Clients", "mcp_server_add"),
    ("connect this tool server at https://mcp.example.com/sse", "mcp_server_add"),
    ("install the fetch MCP server", "mcp_server_add"),
    ("set up an MCP server for GitHub", "mcp_server_add"),
    ("add this MCP server to Jarvis: uvx mcp-server-fetch", "mcp_server_add"),
    ('here is the config for the memory mcp server: {"mcpServers": {"memory": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"]}}}', "mcp_server_add"),
    ("hook up the playwright tool server", "mcp_server_add"),
    (r"please add the git MCP server for C:\repo", "mcp_server_add"),
    ("register this tool server https://tools.example.com/mcp", "mcp_server_add"),
    ("Hook up our firm's internal pricing API at https://api.vrtax.local/v1 so you can look up fees", "web_service_add"),
    ("add a web service called Pricing at https://api.example.com/v1 with a bearer key", "web_service_add"),
    ("connect our CRM API at https://crm.example.com/api so you can pull client records", "web_service_add"),
    ("save the firm's billing API https://billing.vrtax.local as a web service", "web_service_add"),
    ("set up a REST API connection to https://api.example.com for fee lookups", "web_service_add"),
    ("register the internal pricing API at http://10.0.0.5:8080/v1", "web_service_add"),
    ("wire up our rest api at https://api.example.com/v2", "web_service_add"),
    ("let Jarvis call our pricing API at https://api.vrtax.local/v1", "web_service_add"),
    ("give Jarvis access to the company API https://api.example.com", "web_service_add"),
    ("add our in-house API https://intranet.example.com/api as a web service", "web_service_add"),
]
NEAR_MISSES = [
    "the pricing API changed its terms",
    "a server at the restaurant",
    "my office PC is slow",
    "the MCP of the church",
    "what is an MCP server?",
    "remove the GitHub MCP server",
    "restart the filesystem MCP server",
    "the fetch MCP server crashed again",
    "build a REST API for the client portal",
    "call the pricing API and get the fee for a 1040",
    "write an email about our API outage",
    "add an API endpoint to the project",
    "the web service returned a 500",
    "add the QuickBooks server I use in Claude Desktop",
    "how do I add an MCP server?",
    "the server at the office needs a reboot",
    "add a server rack to the inventory",
    "deploy the API to the server",
    "our API pricing changed",
    "connect my laptop to the server",
]


@pytest.fixture(scope="module")
def d(tmp_path_factory):
    home = tmp_path_factory.mktemp("arming") / "home"
    app = create_app(str(home))
    return SimpleNamespace(platform=app.state.platform)


def lanes(d, text: str) -> tuple[list[str], list[str]]:
    body = ChatBody(messages=[{"role": "user", "content": text}], auto_tools=True)
    armed = list(CT._resolve_armed_tools(d, body)[0])
    ask = [t for t in select_ask_tools(text) if t not in armed]
    ask += CT.settings_ask_tools(d, text, armed, ask)
    return armed, ask


@pytest.mark.parametrize("text, tool", ARM_ROWS)
def test_a_request_offers_its_tool_in_the_stream_lane_and_never_in_the_post_lane(d, text, tool):
    assert tool in R.record_tools_for(text)
    armed, ask = lanes(d, text)
    assert tool in ask, (text, ask)
    assert tool not in armed, "the POST lane never arms an ask-tier writer"
    assert not ({"mcp_server_add", "web_service_add"} - {tool}) & set(ask), "never both for one request"


@pytest.mark.parametrize("text", NEAR_MISSES)
def test_a_near_miss_arms_neither(d, text):
    assert not set(WRITERS) & set(R.record_tools_for(text)), text
    armed, ask = lanes(d, text)
    assert not set(WRITERS) & (set(armed) | set(ask)), (text, armed, ask)


_PATHOLOGICAL = r"""
import sys, time
sys.path.insert(0, sys.argv[1])
from iron_jarvis.settings import records_tools as RT
bad = [
    "add " + "the " * 30000 + "mcp server",
    "hook up " + "our " * 30000 + "api at https://x.example.com",
    "add this server: npx -y " + "a" * 120000,
    "connect " + "this " * 25000 + "tool server at https://" + "m" * 20000 + "/sse",
    "{" + "\"command\": \"" * 10000,
    "add a web service called " + "x " * 40000 + "at https://api.example.com",
    "register " + "the internal pricing api " * 5000,
    "https://" + "a" * 100000 + "/mcp add it",
]
t0 = time.perf_counter()
for text in bad:
    RT.tools_tools_for.cache_clear()
    RT.tools_tools_for(text)
    RT.parse_server_config(text[:20000])
print(time.perf_counter() - t0)
"""


def test_the_matchers_are_linear_on_pathological_input():
    """A child process with a deadline (a hang detector, never a speed bar)."""
    out = subprocess.run(
        [sys.executable, "-c", _PATHOLOGICAL, str(ROOT / "src")], capture_output=True, text=True, timeout=120,
    )
    assert out.returncode == 0, out.stderr
    assert float(out.stdout.strip().splitlines()[-1]) < 10.0


# =========================================================================== #
# The honesty note knows the kinds
# =========================================================================== #
@pytest.mark.parametrize(
    "reply, used, noted",
    [
        ("I added the GitHub MCP server for you.", [], "No tool server was created in this reply."),
        ("I added the GitHub MCP server for you.", ["mcp_server_add"], ""),
        ("I've saved the Pricing web service.", [], "No web service was created in this reply."),
        ("I've saved the Pricing web service.", ["web_service_add"], ""),
        ("I added a tool server called fetch.", ["app_import"], ""),
        ("The mail server was restarted by IT.", [], ""),
    ],
)
def test_the_honesty_note_judges_a_tool_server_and_a_web_service_claim(reply, used, noted):
    from iron_jarvis.daemon.change_claims import claimed_change_note

    note = claimed_change_note(reply, list(used), [])
    if noted:
        assert noted in note, note
    else:
        assert note == "", note
