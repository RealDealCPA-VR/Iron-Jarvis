"""v1.342.0 ("the pack says why"), lane D-closes: every door that unloads a
pack's tools CLOSES their MCP client.

The v1.328.1 leak, three more doors. ``DELETE /mcp/servers/{name}`` and the
Retry route close what they unregister since v1.328.1 / v1.311.0; but the
Connections page's disconnect (``connectors.service._disconnect_mcp_server``),
its reconnect (``_connect_mcp``, which REGISTERED the fresh tools over the old
names and never unregistered anything), and the chat ``app_*`` records
(``settings.records.load_app`` / ``apply_app``) only unregistered — so the
pack's stdio child ran on until the app restarted, with no surface left that
could stop it.

The closer now lives in ONE place, ``mcp.tools.close_tools_clients`` (lifted
from ``platform._close_mcp_tools``: a service must not import a route module,
and ``platform`` imports ``mcp.tools``), and ``mcp.tools.unload_pack_tools``
is the unregister-and-close door the three services call. Each test here
registers a fake client with a close() counter as a pack's tools and asserts
the door closes it EXACTLY once — remove the close and every door's test is
red (checked: the old bare-unregister shape reads ``closed == 0``).
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.connectors import service
from iron_jarvis.mcp import tools as mcp_tools_mod
from iron_jarvis.mcp.tools import close_tools_clients, unload_pack_tools
from iron_jarvis.settings import records
from iron_jarvis.tools.registry import ToolRegistry

PACK = "github"  # a catalog MCP connector (connect_via="mcp"), so connect() reaches _connect_mcp
SRC = Path(__file__).resolve().parents[1] / "src" / "iron_jarvis"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


class _Client:
    """A client that counts its close() calls — the real one owns a stdio
    child, so one close is the whole point and two would respawn nothing
    but prove a double-unload path."""

    def __init__(self) -> None:
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


class _PackTool:
    """The registry needs only ``.name``; the closer reads ``.client``."""

    def __init__(self, name: str, client: _Client) -> None:
        self.name = name
        self.client = client


class _Secrets:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.deleted: list[str] = []

    def set(self, name: str, value: str) -> None:
        self.values[name] = value

    def get(self, name: str):
        return self.values.get(name)

    def delete(self, name: str) -> None:
        self.deleted.append(name)
        self.values.pop(name, None)


def _platform(tmp_path: Path, pack: str = PACK) -> SimpleNamespace:
    cfg = {"name": pack, "command": "npx", "args": ["-y", "some-server"]}
    return SimpleNamespace(
        registry=ToolRegistry(),
        config=SimpleNamespace(mcp_servers=[cfg], home=str(tmp_path)),
        secrets=_Secrets(),
    )


def _register_pack(platform: SimpleNamespace, pack: str = PACK) -> _Client:
    """Two tools of one pack sharing ONE client — the real loader wraps every
    tool of a server around the same MCPClient, so a closer that is not
    de-duplicated would close it twice."""
    client = _Client()
    for leaf in ("search", "read"):
        platform.registry.register(_PackTool(f"mcp__{pack}__{leaf}", client), mcp=True)
    assert platform.registry.mcp_names(pack) == [f"mcp__{pack}__read", f"mcp__{pack}__search"]
    return client


@pytest.fixture(autouse=True)
def _no_config_writes(monkeypatch):
    """The doors persist config; keep the test off the disk and off the
    process-wide config lock."""
    monkeypatch.setattr(service, "persist_config_values", lambda *_a, **_k: None)
    monkeypatch.setattr("iron_jarvis.core.config.persist_config_values", lambda *_a, **_k: None)


# --------------------------------------------------------------------------- #
# The lifted helper itself.
# --------------------------------------------------------------------------- #
def test_close_tools_clients_closes_each_distinct_client_once_and_never_raises():
    shared = _Client()
    other = _Client()

    class _Raising:
        def close(self) -> None:
            raise RuntimeError("pipe already gone")

    tools = [
        _PackTool("a", shared),
        _PackTool("b", shared),
        _PackTool("c", other),
        _PackTool("d", _Raising()),  # type: ignore[arg-type]
        SimpleNamespace(name="e"),  # no client at all
    ]
    close_tools_clients(tools)  # the raising close must not escape
    assert shared.closed == 1, "a client shared by two tools was closed more than once"
    assert other.closed == 1
    close_tools_clients(None)  # tolerated
    close_tools_clients([])


def test_unload_pack_tools_unregisters_the_pack_and_closes_its_client_once(tmp_path):
    platform = _platform(tmp_path)
    client = _register_pack(platform)
    bystander = _Client()
    platform.registry.register(_PackTool("mcp__other__ping", bystander), mcp=True)

    assert unload_pack_tools(platform.registry, PACK) == 2
    assert platform.registry.mcp_names(PACK) == []
    assert client.closed == 1
    assert bystander.closed == 0, "another pack's client was closed"
    assert platform.registry.mcp_names("other") == ["mcp__other__ping"]
    # A second unload of a pack with nothing registered is a no-op.
    assert unload_pack_tools(platform.registry, PACK) == 0
    assert client.closed == 1


# --------------------------------------------------------------------------- #
# Door 1: Connections disconnect.
# --------------------------------------------------------------------------- #
def test_disconnect_closes_the_packs_client_exactly_once(tmp_path):
    platform = _platform(tmp_path)
    client = _register_pack(platform)

    out = service.disconnect(platform, PACK)

    assert out == {"ok": True, "disconnected": PACK}
    assert platform.registry.mcp_names(PACK) == []
    assert platform.config.mcp_servers == []
    assert client.closed == 1, "disconnect unregistered the tools but left the stdio child running"


def test_disconnect_of_a_user_added_server_closes_too(tmp_path):
    """The dynamic (non-catalog) path takes the same door."""
    platform = _platform(tmp_path, pack="my-own-server")
    client = _register_pack(platform, pack="my-own-server")

    out = service.disconnect(platform, "my-own-server")

    assert out["ok"] is True
    assert platform.registry.mcp_names("my-own-server") == []
    assert client.closed == 1


# --------------------------------------------------------------------------- #
# Door 2: Connections reconnect (connect() on an already-connected pack).
# --------------------------------------------------------------------------- #
def test_reconnect_closes_the_old_client_once_and_keeps_the_fresh_one_open(tmp_path, monkeypatch):
    platform = _platform(tmp_path)
    old = _register_pack(platform)
    fresh = _Client()
    calls: list[list[dict]] = []

    def _fake_mcp_tools(cfgs, secret_resolver=None, **kw):
        calls.append(cfgs)
        return [_PackTool(f"mcp__{PACK}__search", fresh)]

    monkeypatch.setattr(service, "mcp_tools", _fake_mcp_tools)

    out = service.connect(platform, PACK, {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_x"})

    assert out["ok"] is True and out["tools_loaded"] == 1, out
    assert len(calls) == 1 and calls[0][0]["name"] == PACK
    assert old.closed == 1, "reconnect registered over the old tools and orphaned their client"
    assert fresh.closed == 0, "the fresh connection was closed with the old one"
    live = platform.registry.get(f"mcp__{PACK}__search")
    assert live is not None and live.client is fresh
    # The old pack's second tool (not in the fresh list) is gone, not lingering.
    assert platform.registry.mcp_names(PACK) == [f"mcp__{PACK}__search"]


def test_reconnect_whose_fresh_load_fails_still_closed_the_old_client(tmp_path, monkeypatch):
    """A failed reload leaves nothing registered (the saved config loads on
    restart) — and the old child is still not left running."""
    platform = _platform(tmp_path)
    old = _register_pack(platform)

    def _boom(*_a, **_k):
        raise RuntimeError("launcher missing")

    monkeypatch.setattr(service, "mcp_tools", _boom)

    out = service.connect(platform, PACK, {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_x"})

    assert out["ok"] is True and out["tools_loaded"] == 0
    assert old.closed == 1
    assert platform.registry.mcp_names(PACK) == []


# --------------------------------------------------------------------------- #
# Door 3 + 4: the chat app_* records (load_app / apply_app).
# --------------------------------------------------------------------------- #
def test_load_app_closes_the_old_client_once_before_the_reload(tmp_path, monkeypatch):
    platform = _platform(tmp_path)
    old = _register_pack(platform)
    fresh = _Client()
    monkeypatch.setattr(
        mcp_tools_mod, "mcp_tools", lambda cfgs, secret_resolver=None, **kw: [_PackTool(f"mcp__{PACK}__search", fresh)]
    )

    loaded = records.load_app(platform, platform.config.mcp_servers[0])

    assert loaded == 1
    assert old.closed == 1, "load_app unregistered the old tools but left their client open"
    assert fresh.closed == 0
    assert platform.registry.get(f"mcp__{PACK}__search").client is fresh


def test_apply_app_removal_closes_the_client_exactly_once(tmp_path):
    platform = _platform(tmp_path)
    client = _register_pack(platform)

    assert records.apply_app(platform, PACK, None) == 0

    assert platform.registry.mcp_names(PACK) == []
    assert platform.config.mcp_servers == []
    assert client.closed == 1, "apply_app(None) unregistered the tools but left the stdio child running"


def test_apply_app_with_a_config_goes_through_load_app_and_closes_once(tmp_path, monkeypatch):
    platform = _platform(tmp_path)
    old = _register_pack(platform)
    fresh = _Client()
    monkeypatch.setattr(
        mcp_tools_mod, "mcp_tools", lambda cfgs, secret_resolver=None, **kw: [_PackTool(f"mcp__{PACK}__search", fresh)]
    )
    cfg = dict(platform.config.mcp_servers[0], args=["-y", "some-server", "--new-flag"])

    assert records.apply_app(platform, PACK, cfg) == 1

    assert old.closed == 1
    assert fresh.closed == 0
    assert platform.config.mcp_servers == [cfg]


def test_apply_app_without_load_closes_nothing_and_registers_nothing(tmp_path):
    """``load=False`` is "save only" — the live tools are untouched."""
    platform = _platform(tmp_path)
    client = _register_pack(platform)

    assert records.apply_app(platform, PACK, platform.config.mcp_servers[0], load=False) == 0

    assert client.closed == 0
    assert len(platform.registry.mcp_names(PACK)) == 2


# --------------------------------------------------------------------------- #
# Reuse, not copy: one closer, and no service imports a route or platform.
# --------------------------------------------------------------------------- #
def test_the_services_and_the_routes_share_the_one_closer_in_mcp_tools():
    svc = _read(SRC / "connectors" / "service.py")
    rec = _read(SRC / "settings" / "records.py")
    route = _read(SRC / "daemon" / "routes" / "agents.py")

    for text, label in ((svc, "connectors/service.py"), (rec, "settings/records.py")):
        assert "unload_pack_tools" in text, f"{label} does not go through mcp.tools.unload_pack_tools"
        assert "_close_mcp_tools" not in text, f"{label} reaches the closer through platform"
        assert "routes.agents" not in text and "routes import agents" not in text, f"{label} imports a route module"
        # The bare unregister loop is gone from the pack doors.
        assert not re.search(r"for \w+ in platform\.registry\.mcp_names\([^)]*\):\s*\n\s*platform\.registry\.unregister", text), (
            f"{label} still unregisters a pack's tools without closing them"
        )
    assert "_close_mcp_tools" not in route, "routes/agents.py still reaches the closer through platform"
    assert route.count("close_tools_clients(") >= 2, "the Retry and DELETE routes do not call the lifted closer"
