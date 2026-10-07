"""Pack-tool quarantine (v1.299.0): a write-like MCP tool that APPEARS LATER
than its pack, or changes shape, asks until the user trusts it — whatever the
pack's or the global auto-approve says.

THE GAP. A pack is trusted as a whole (``auto_approve`` / ``mcp_auto_approve``
answer yes for every ``mcp_call``) and its tool list is re-read from the server
at every boot, add and reload, with nothing remembering the previous list. A
pack update that grew ``wipe`` next to ``echo`` handed an autonomous agent a
destructive tool nobody had seen. Now:

* ``mcp/manifest.py`` persists ``<home>/mcp/manifests/<server>.json`` and diffs
  each load against it (first load trusts everything; new/changed write-like
  tools are not trusted; a read-only tool is never quarantined).
* ``MCPRemoteTool.from_spec`` reads the MCP ``annotations`` (readOnlyHint ->
  READ; destructiveHint/absent -> EXTERNAL_COMMIT) so "write-like" is a fact.
* The platform's MCP auto-approve resolver wrapper answers False for a
  quarantined tool (proved through the REAL ``registry.invoke`` path with the
  trusted sibling running as the anti-vacuity control).
* ``GET /mcp/servers`` shows it; ``POST /mcp/servers/{s}/tools/{t}/trust``
  clears it, persistently.

The servers here are REAL stdio children (``tests/fixtures/
mutable_mcp_server_v1299.py``, whose tool list is a JSON file the test rewrites
between boots) or an in-memory ``FakeTransport`` where no process is needed.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.mcp.client import FakeTransport, MCPClient
from iron_jarvis.mcp.manifest import PackManifest, schema_sha
from iron_jarvis.mcp.tools import (
    MCPRemoteTool,
    is_write_like,
    load_status,
    mcp_tools,
    risk_for,
)
from iron_jarvis.tools.base import Reversibility, RiskClass, Tool, ToolContext, ToolResult
from iron_jarvis.tools.registry import ToolRegistry


def _packs_loaded(client, timeout: float = 30.0) -> None:
    """v1.311.0: the daemon no longer waits for MCP handshakes before it
    serves — a configured pack loads in the background after boot and
    ``mcp.loaded`` says when its tools reached the registry. A "restart"
    assertion waits for THAT (the thing asserted), bounded, instead of
    assuming the tools were registered inside ``create_app``."""
    import time as _time

    platform = client.app.state.platform
    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        if any(e.type == "mcp.loaded" for e in platform.event_bus.history):
            return
        _time.sleep(0.02)
    raise AssertionError("the background MCP load never finished")

FIXTURE = str(Path(__file__).parent / "fixtures" / "mutable_mcp_server_v1299.py")

_SCHEMA_A = {"type": "object", "properties": {"text": {"type": "string"}}}
_SCHEMA_B = {
    "type": "object",
    "properties": {"text": {"type": "string"}, "force": {"type": "boolean"}},
}

ECHO = {"name": "echo", "description": "echo", "inputSchema": _SCHEMA_A}  # no annotations
WIPE = {
    "name": "wipe",
    "description": "delete everything",
    "inputSchema": {"type": "object", "properties": {}},
    "annotations": {"destructiveHint": True},
}
LIST_ITEMS = {
    "name": "list_items",
    "description": "read",
    "inputSchema": {"type": "object", "properties": {}},
    "annotations": {"readOnlyHint": True},
}
CREATE_ITEM = {
    "name": "create_item",
    "description": "additive write",
    "inputSchema": {"type": "object", "properties": {}},
    "annotations": {"readOnlyHint": False, "destructiveHint": False},
}


# --------------------------------------------------------------------------- #
# Helpers.
# --------------------------------------------------------------------------- #
def _fake_cfg(name: str, tools: list[dict]) -> dict:
    """A server config carrying an in-memory transport (no process)."""
    transport = FakeTransport(
        {
            "initialize": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}},
            "tools/list": {"tools": tools},
            "tools/call": lambda p: {
                "content": [{"type": "text", "text": f"{p.get('name')}:ok"}],
                "isError": False,
            },
        }
    )
    return {"name": name, "transport_obj": transport}


def _write_tools(path: Path, tools: list[dict]) -> None:
    path.write_text(json.dumps(tools), encoding="utf-8")


def _body(name: str, tools_file: Path, auto_approve: bool = False) -> dict:
    return {
        "name": name,
        "command": sys.executable,
        "args": [FIXTURE, str(tools_file)],
        "auto_approve": auto_approve,
    }


def _ctx(platform, tmp_path: Path) -> ToolContext:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return ToolContext(
        workspace=ws,
        session_id="s1",
        agent_run_id="r1",
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )


def _invoke(platform, tmp_path: Path, name: str, args: dict | None = None) -> ToolResult:
    """The REAL path: ``registry.invoke`` -> ``PermissionEngine.authorize`` ->
    the platform's resolver (no session grant, so the resolver decides)."""
    return asyncio.run(
        platform.registry.invoke(name, args or {}, _ctx(platform, tmp_path), platform.permissions)
    )


def _row(client: TestClient, name: str) -> dict:
    return next(s for s in client.get("/mcp/servers").json()["servers"] if s["name"] == name)


# --------------------------------------------------------------------------- #
# (1) Annotations -> risk class / write-likeness.
# --------------------------------------------------------------------------- #
def test_annotations_map_to_risk_class_and_write_likeness():
    client = MCPClient(FakeTransport(), name="t")

    read = MCPRemoteTool.from_spec(client, "srv", LIST_ITEMS)
    assert read.risk_class is RiskClass.READ
    assert read.reversibility is Reversibility.READONLY
    assert read.write_like is False
    assert is_write_like(LIST_ITEMS) is False

    wipe = MCPRemoteTool.from_spec(client, "srv", WIPE)
    assert wipe.risk_class is RiskClass.EXTERNAL_COMMIT
    assert wipe.reversibility is Reversibility.IRREVERSIBLE
    assert wipe.write_like is True

    # No annotations at all: the fail-safe default, and write-like.
    bare = MCPRemoteTool.from_spec(client, "srv", ECHO)
    assert bare.risk_class is RiskClass.EXTERNAL_COMMIT
    assert bare.write_like is True and is_write_like(ECHO) is True

    # An additive write (destructiveHint: false) is still write-like.
    create = MCPRemoteTool.from_spec(client, "srv", CREATE_ITEM)
    assert create.risk_class is RiskClass.PAGE_ACTION
    assert create.write_like is True
    assert risk_for(CREATE_ITEM) == (RiskClass.PAGE_ACTION, Reversibility.IRREVERSIBLE)

    # The hints are kept for display; the permission key is untouched.
    assert wipe.mcp_annotations == {"destructiveHint": True}
    assert wipe.perm_key() == "mcp_call" and wipe.quarantined is False


# --------------------------------------------------------------------------- #
# (2) Manifest round-trip + diff.
# --------------------------------------------------------------------------- #
def test_manifest_round_trip_and_diff(tmp_path):
    pm = PackManifest(tmp_path)
    assert pm.load("srv") is None
    assert pm.diff("srv", [ECHO, WIPE]) == {"new": ["echo", "wipe"], "gone": [], "changed": []}

    doc = pm.record("srv", [ECHO, WIPE])  # first load: everything trusted
    assert (tmp_path / "mcp" / "manifests" / "srv.json").is_file()
    assert {t["name"]: t["trusted"] for t in doc["tools"]} == {"echo": True, "wipe": True}
    assert doc["tools"][0]["schema_sha"] == schema_sha(_SCHEMA_A)

    # A fresh instance reads the same file back.
    again = PackManifest(tmp_path).load("srv")
    assert again is not None and [t["name"] for t in again["tools"]] == ["echo", "wipe"]

    # new / gone / changed (schema hash) / changed (write_like flip).
    echo_b = {**ECHO, "inputSchema": _SCHEMA_B}
    wipe_ro = {**WIPE, "annotations": {"readOnlyHint": True}}
    d = pm.diff("srv", [echo_b, wipe_ro, LIST_ITEMS])
    assert d == {"new": ["list_items"], "gone": [], "changed": ["echo", "wipe"]}
    assert pm.diff("srv", [ECHO])["gone"] == ["wipe"]

    # Recording the later list: new/changed write-like tools lose trust, the
    # read-only one is trusted whatever happened, and quarantined() names
    # exactly the write-like untrusted set.
    pm.record("srv", [echo_b, WIPE, LIST_ITEMS, CREATE_ITEM])
    assert pm.quarantined("srv") == {"echo", "create_item"}  # wipe unchanged -> kept
    assert pm.trust("srv", "echo") is True
    assert pm.quarantined("srv") == {"create_item"}
    assert pm.trust("srv", "nope") is False
    assert pm.trust("other", "echo") is False

    # A server name cannot pick a file outside the manifests folder.
    assert PackManifest(tmp_path).path("../../etc").parent == tmp_path / "mcp" / "manifests"


# --------------------------------------------------------------------------- #
# (3) First load trusts everything.
# --------------------------------------------------------------------------- #
def test_first_load_trusts_every_tool(tmp_path):
    tools = mcp_tools([_fake_cfg("first", [ECHO, WIPE])], home=tmp_path)
    assert [t.name for t in tools] == ["mcp__first__echo", "mcp__first__wipe"]
    assert all(t.quarantined is False for t in tools)
    assert load_status("first")["quarantined"] == []
    saved = PackManifest(tmp_path).load("first")
    assert saved is not None and all(t["trusted"] for t in saved["tools"])
    # Nothing advertised: no quarantine (and still no crash).
    assert all("quarantined" not in t.spec() for t in tools)


# --------------------------------------------------------------------------- #
# (4) A later load: a new write-like tool is quarantined, a new read-only is not.
# --------------------------------------------------------------------------- #
def test_later_new_write_like_tool_is_quarantined_but_read_only_is_not(tmp_path):
    mcp_tools([_fake_cfg("pack", [ECHO])], home=tmp_path)
    tools = mcp_tools([_fake_cfg("pack", [ECHO, WIPE, LIST_ITEMS])], home=tmp_path)
    by = {t.remote_name: t for t in tools}
    assert by["echo"].quarantined is False  # seen on first load: still trusted
    assert by["wipe"].quarantined is True  # new AND write-like
    assert by["list_items"].quarantined is False  # new but cannot act
    assert load_status("pack")["quarantined"] == ["wipe"]

    # The flag is advertised ONLY when set (additive spec key).
    assert by["wipe"].spec()["quarantined"] is True
    assert "quarantined" not in by["echo"].spec()
    assert "quarantined" not in by["list_items"].spec()
    reg = ToolRegistry()
    for t in tools:
        reg.register(t, mcp=True)
    specs = {s["name"]: s for s in reg.specs(["mcp:*"])}
    assert specs["mcp__pack__wipe"]["quarantined"] is True
    assert "quarantined" not in specs["mcp__pack__echo"]

    # Idempotent: loading the same list again changes nothing (no new trust,
    # no new holds) — boot applies this AFTER a live add already did.
    again = {t.remote_name: t for t in mcp_tools([_fake_cfg("pack", [ECHO, WIPE, LIST_ITEMS])], home=tmp_path)}
    assert again["wipe"].quarantined is True and again["echo"].quarantined is False


def test_a_tool_whose_schema_changed_is_quarantined(tmp_path):
    mcp_tools([_fake_cfg("pack", [ECHO])], home=tmp_path)
    (tool,) = mcp_tools([_fake_cfg("pack", [{**ECHO, "inputSchema": _SCHEMA_B}])], home=tmp_path)
    assert tool.quarantined is True
    assert load_status("pack")["quarantined"] == ["echo"]
    # ...and a tool that is gone is dropped: coming back later is "new" again.
    mcp_tools([_fake_cfg("pack", [LIST_ITEMS])], home=tmp_path)
    (back,) = mcp_tools([_fake_cfg("pack", [ECHO])], home=tmp_path)
    assert back.quarantined is True


# --------------------------------------------------------------------------- #
# (5) A probe (record=False) never touches the manifest.
# --------------------------------------------------------------------------- #
def test_probe_writes_no_manifest(tmp_path):
    tools = mcp_tools([_fake_cfg("probe", [ECHO, WIPE])], home=tmp_path, record=False)
    assert len(tools) == 2
    assert not (tmp_path / "mcp" / "manifests").exists()
    assert load_status("probe") is None
    # A probe after a real load does not pre-trust a new tool either.
    mcp_tools([_fake_cfg("probe", [ECHO])], home=tmp_path)
    mcp_tools([_fake_cfg("probe", [ECHO, WIPE])], home=tmp_path, record=False)
    assert PackManifest(tmp_path).quarantined("probe") == set()
    assert [t["name"] for t in PackManifest(tmp_path).load("probe")["tools"]] == ["echo"]
    (_, wipe) = mcp_tools([_fake_cfg("probe", [ECHO, WIPE])], home=tmp_path)
    assert wipe.quarantined is True


# --------------------------------------------------------------------------- #
# (6) The REAL app: boot + resolver wrapper + routes.
# --------------------------------------------------------------------------- #
@pytest.fixture
def tools_file(tmp_path) -> Path:
    f = tmp_path / "tools.json"
    _write_tools(f, [ECHO])
    return f


def test_resolver_denies_a_quarantined_tool_under_auto_approve_through_real_invoke(
    tmp_path, tools_file
):
    """The pack is auto-approved. Its ORIGINAL tool runs unprompted (the
    anti-vacuity control); the tool that arrived in the 'update' is refused
    through the same ``registry.invoke`` -> ``authorize`` -> resolver path."""
    root = tmp_path / "root"
    root.mkdir()
    with TestClient(create_app(str(root))) as client:
        add = client.post("/mcp/servers", json=_body("mut", tools_file, auto_approve=True)).json()
        assert add["tools_loaded"] == 1
        saved = PackManifest(client.app.state.platform.config.home).load("mut")
        assert saved is not None and [t["name"] for t in saved["tools"]] == ["echo"]

    # "The pack shipped an update": a destructive tool appears.
    _write_tools(tools_file, [ECHO, WIPE, LIST_ITEMS])

    with TestClient(create_app(str(root))) as client2:
        _packs_loaded(client2)
        platform = client2.app.state.platform
        # The boot resolver is composed with the pack's auto_approve on.
        assert platform.permissions.authorize("mcp_call", {}).allowed is True
        row = _row(client2, "mut")
        assert row["tools_loaded"] == 3
        assert row["quarantined"] == ["wipe"]
        assert {t["name"]: (t["write_like"], t["quarantined"]) for t in row["tools"]} == {
            "echo": (True, False),
            "wipe": (True, True),
            "list_items": (False, False),
        }
        assert load_status("mut")["quarantined"] == ["wipe"]

        # Control: the trusted sibling RUNS, auto-approved, no session grant.
        ok = _invoke(platform, tmp_path, "mcp__mut__echo", {"text": "hi"})
        assert ok.ok is True, ok.error
        assert ok.output.startswith("echo:")
        # The read-only newcomer runs too (it cannot act).
        assert _invoke(platform, tmp_path, "mcp__mut__list_items").ok is True

        # The quarantined tool is refused by the resolver — not by a roster,
        # not by a stub: the real engine answered, the real wrapper said no.
        held = _invoke(platform, tmp_path, "mcp__mut__wipe")
        assert held.ok is False
        assert held.error.startswith("permission denied"), held.error
        assert "approval" in held.error

        # And the ContextVar seam is clean afterwards: a bare question about
        # mcp_call with no tool in flight keeps the blanket grant.
        assert platform.permissions.authorize("mcp_call", {}).allowed is True


def test_trust_route_clears_the_quarantine_and_it_persists(tmp_path, tools_file):
    root = tmp_path / "root"
    root.mkdir()
    with TestClient(create_app(str(root))) as client:
        client.post("/mcp/servers", json=_body("mut", tools_file, auto_approve=True))
    _write_tools(tools_file, [ECHO, WIPE])

    with TestClient(create_app(str(root))) as client2:
        _packs_loaded(client2)
        platform = client2.app.state.platform
        assert _row(client2, "mut")["quarantined"] == ["wipe"]
        assert _invoke(platform, tmp_path, "mcp__mut__wipe").ok is False

        # 404s: unknown pack, unknown tool.
        assert client2.post("/mcp/servers/nope/tools/wipe/trust").status_code == 404
        assert client2.post("/mcp/servers/mut/tools/nope/trust").status_code == 404

        r = client2.post("/mcp/servers/mut/tools/wipe/trust")
        assert r.status_code == 200
        assert r.json() == {"ok": True, "server": "mut", "tool": "wipe"}
        assert _row(client2, "mut")["quarantined"] == []
        assert platform.registry.get("mcp__mut__wipe").quarantined is False
        assert load_status("mut")["quarantined"] == []
        # Live: the pack's auto-approve applies to it from the next call.
        assert _invoke(platform, tmp_path, "mcp__mut__wipe").ok is True

        # A reload re-diffs against the manifest and keeps the trust.
        rl = client2.post("/mcp/servers/mut/reload").json()
        assert rl == {"ok": True, "tools_loaded": 2, "last_error": None}  # v1.229.0 pin kept
        assert _row(client2, "mut")["quarantined"] == []

    # Persisted: a fresh boot still trusts it.
    with TestClient(create_app(str(root))) as client3:
        _packs_loaded(client3)
        assert _row(client3, "mut")["quarantined"] == []
        assert client3.app.state.platform.registry.get("mcp__mut__wipe").quarantined is False


def test_reload_quarantines_a_tool_that_arrived_since(tmp_path, tools_file):
    """Retry on the Tools page is a load too: it must hold back a newcomer."""
    with TestClient(create_app(str(tmp_path / "root"))) as client:
        client.post("/mcp/servers", json=_body("mut", tools_file))
        assert _row(client, "mut")["quarantined"] == []
        _write_tools(tools_file, [ECHO, CREATE_ITEM, LIST_ITEMS])
        rl = client.post("/mcp/servers/mut/reload").json()
        assert rl["tools_loaded"] == 3
        row = _row(client, "mut")
        assert load_status("mut")["quarantined"] == ["create_item"]
        assert row["quarantined"] == ["create_item"]
        assert row["tool_names"] == ["create_item", "echo", "list_items"]  # unchanged key
        # /test is a probe: it sees a new tool but never trusts or holds it.
        _write_tools(tools_file, [ECHO, CREATE_ITEM, LIST_ITEMS, WIPE])
        probe = client.post("/mcp/servers/mut/test").json()
        assert probe["count"] == 4
        assert PackManifest(client.app.state.platform.config.home).quarantined("mut") == {"create_item"}
        assert "wipe" not in [t["name"] for t in PackManifest(client.app.state.platform.config.home).load("mut")["tools"]]


def test_base_tool_spec_has_no_quarantined_key_unless_set():
    class _T(Tool):
        name = "plain"
        description = "d"
        input_schema = {"type": "object", "properties": {}}

        async def execute(self, args, ctx):  # pragma: no cover
            return ToolResult(ok=True)

    t = _T()
    assert t.quarantined is False
    assert "quarantined" not in t.spec()
    t.quarantined = True
    assert t.spec()["quarantined"] is True


def test_connectors_service_connect_quarantines_a_tool_that_arrived_since(
    tmp_path, tools_file, monkeypatch
):
    """The marketplace door (``connectors.service.connect``) is a LOAD too: a
    reconnect of a pack whose list grew a write-like tool registers it
    quarantined NOW — not at the next boot — and the original stays trusted."""
    from iron_jarvis.connectors import service as connectors
    from iron_jarvis.connectors.catalog import Connector

    fake = Connector(
        id="mutpack",
        name="Mutable",
        category="Developer",
        glyph="x",
        blurb="b",
        unlocks="u",
        connect_via="mcp",
        command=sys.executable,
        args=[FIXTURE, str(tools_file)],
    )
    monkeypatch.setattr(connectors, "get_connector", lambda cid: fake if cid == "mutpack" else None)

    with TestClient(create_app(str(tmp_path / "root"))) as client:
        platform = client.app.state.platform
        first = connectors.connect(platform, "mutpack", {})
        assert first["tools_loaded"] == 1
        assert platform.registry.get("mcp__mutpack__echo").quarantined is False

        _write_tools(tools_file, [ECHO, WIPE, LIST_ITEMS])
        again = connectors.connect(platform, "mutpack", {})
        assert again["tools_loaded"] == 3
        reg = platform.registry
        assert reg.get("mcp__mutpack__wipe").quarantined is True
        assert reg.get("mcp__mutpack__echo").quarantined is False
        assert reg.get("mcp__mutpack__list_items").quarantined is False
        assert load_status("mutpack")["quarantined"] == ["wipe"]
        assert PackManifest(platform.config.home).quarantined("mutpack") == {"wipe"}
        assert _row(client, "mutpack")["quarantined"] == ["wipe"]


# --------------------------------------------------------------------------- #
# (7) Review pins: a card's OWN answer runs a quarantined tool; the shared key
#     never does; a stale "asking about" never outlives the invoke.
# --------------------------------------------------------------------------- #
def test_a_cards_own_answer_runs_a_quarantined_tool_but_the_shared_key_never_does(
    tmp_path, tools_file
):
    """The runtime's and the stream lane's "once"/"conversation" hand the
    registry ``{<tool name>, "mcp_call"}`` — the user saw THAT tool's card.
    That is the one lift a quarantined tool keeps. The blanket (``mcp_call``
    alone: a sibling's card, the pack's switch) and a sibling's own name lift
    nothing — through the REAL ``registry.invoke`` -> ``authorize``."""
    root = tmp_path / "root"
    root.mkdir()
    with TestClient(create_app(str(root))) as client:
        client.post("/mcp/servers", json=_body("mut", tools_file, auto_approve=True))
    _write_tools(tools_file, [ECHO, WIPE])
    with TestClient(create_app(str(root))) as client2:
        _packs_loaded(client2)
        platform = client2.app.state.platform
        ctx = _ctx(platform, tmp_path)
        assert platform.registry.get("mcp__mut__wipe").quarantined is True

        def run(**kw):
            return asyncio.run(
                platform.registry.invoke("mcp__mut__wipe", {}, ctx, platform.permissions, **kw)
            )

        assert run().ok is False, "the pack's auto-approve lifts nothing"
        assert run(session_allow={"mcp_call"}).ok is False, "the shared key lifts nothing"
        assert run(session_allow={"mcp__mut__echo", "mcp_call"}).ok is False, "a sibling's card lifts nothing"
        ok = run(session_allow={"mcp__mut__wipe", "mcp_call"})
        assert ok.ok is True, f"the card's own answer must run the call: {ok.error}"
        assert ok.output.startswith("wipe:")
        # ...and the engine's own verdict names the task grant, not a standing one.
        dec = platform.permissions.authorize(
            "mcp_call", {}, None, {"mcp__mut__wipe", "mcp_call"},
            call_name="mcp__mut__wipe", quarantined=True,
        )
        assert dec.allowed is True and dec.grant_id == "" and "task" in dec.reason
        # The seam is clean after the lifted call (the wrapper was never
        # consulted, so nothing else cleared it): a bare question keeps the
        # blanket grant.
        assert platform.permissions.authorize("mcp_call", {}).allowed is True


def test_a_stale_asking_about_never_outlives_an_invoke_that_raised(tmp_path, tools_file, monkeypatch):
    """``perm_key()`` records the tool for the resolver wrapper; if the engine
    raises between that read and the wrapper, the next bare question about
    ``mcp_call`` must not answer about the dead call."""
    root = tmp_path / "root"
    root.mkdir()
    with TestClient(create_app(str(root))) as client:
        client.post("/mcp/servers", json=_body("mut", tools_file, auto_approve=True))
    _write_tools(tools_file, [ECHO, WIPE])
    with TestClient(create_app(str(root))) as client2:
        _packs_loaded(client2)
        platform = client2.app.state.platform
        ctx = _ctx(platform, tmp_path)
        real_mode_for = platform.permissions.mode_for
        calls = {"n": 0}

        def boom(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("engine fault before the resolver")
            return real_mode_for(*a, **k)

        monkeypatch.setattr(platform.permissions, "mode_for", boom)

        async def same_task():
            # ONE task context: a ContextVar set inside ``invoke`` is visible
            # to the next question in the same task (the stream lane's shape).
            with pytest.raises(RuntimeError):
                await platform.registry.invoke("mcp__mut__wipe", {}, ctx, platform.permissions)
            return platform.permissions.authorize("mcp_call", {}).allowed

        assert asyncio.run(same_task()) is True, (
            "the quarantined call that raised must not poison the next question"
        )
