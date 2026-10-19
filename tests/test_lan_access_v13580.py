"""v1.358.0 lane 8E (G61) — another computer can reach this Iron Jarvis ON
PURPOSE, through ONE setting, and only then.

The re-audit (v1.357.0): "The packaged app listens on 127.0.0.1 only. The
card's reachability note (daemon/routes/agents.py:101-108) tells a desktop-app
user to run 'ironjarvis serve --host <ip>' and set IRONJARVIS_HOST_ALLOWLIST,
which they cannot do from the app."

Now: "Let other computers on my network reach this Iron Jarvis" (`lan_access`)
is a schema row — off by default, ask-floor from chat, restart required,
saved by the ONE settings writer into config.toml. The desktop shell reads it
at every daemon spawn (desktop/main.js readDaemonNetwork) and only when it is
on AND this PC has a private network address does it start the daemon
listening on the network with IRONJARVIS_HOST_ALLOWLIST naming this PC's own
private addresses. Every request still needs the install token; the doors
keep their own tokens. The doors' reachability sentence names this setting,
never a command line.

These pins drive the REAL writer (and the real Settings route) into a real
config.toml and hand THAT file to the desktop's own functions under node —
the seam between the two halves is the file, so it is the file that is
tested. The Host guard and the token are driven through the real app factory.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.auth import TASK_DOOR_PATH
from iron_jarvis.daemon.app import create_app
from iron_jarvis.settings import schema
from tests.test_desktop_reliability_v1249 import _lift, _run, _src, requires_node

ROOT = Path(__file__).resolve().parents[1]
LABEL = "Let other computers on my network reach this Iron Jarvis"
DOOR_URL = f"http://192.168.1.20:8787{TASK_DOOR_PATH}"

#: os.networkInterfaces() as Windows reports it: loopback, a Wi-Fi LAN
#: address, a link-local leftover, IPv6, and a Tailscale-style address.
IFACES = {
    "Loopback Pseudo-Interface 1": [{"address": "127.0.0.1", "family": "IPv4", "internal": True}],
    "Wi-Fi": [
        {"address": "fe80::1", "family": "IPv6", "internal": False},
        {"address": "192.168.1.20", "family": "IPv4", "internal": False},
    ],
    "Ethernet 3": [{"address": "169.254.10.4", "family": "IPv4", "internal": False}],
    "Tailscale": [{"address": "100.101.102.103", "family": "IPv4", "internal": False}],
    "Public": [{"address": "8.8.8.8", "family": "IPv4", "internal": False}],
}

_NET = """
const fs = require("fs");
const path = require("path");
const logs = [];
function desktopLog(level, ...a) { logs.push(a.join(" ")); }
__LIFTED__
const input = JSON.parse(process.argv[2]);
const out = { cases: {} };
for (const [name, c] of Object.entries(input.toml || {})) out.cases[name] = lanAccessOn(c);
if (input.ifaces) out.addresses = lanAddresses(input.ifaces);
if (input.home !== undefined) {
  if (input.ambient !== undefined) process.env.IRONJARVIS_HOST_ALLOWLIST = input.ambient;
  else delete process.env.IRONJARVIS_HOST_ALLOWLIST;
  const net = readDaemonNetwork(input.home, input.homeIfaces);
  out.net = net;
  out.args = daemonServeArgs(net, 8787, input.root || "ROOT");
}
out.key = LAN_ACCESS_KEY;
out.logs = logs;
process.stdout.write(JSON.stringify(out) + "\\n");
"""


def _node(state: dict, tmp_path: Path, name: str = "net") -> dict:
    script = _NET.replace("__LIFTED__", _lift("const LAN_ACCESS_KEY", "function readDaemonNetwork"))
    return _run(script, tmp_path, name, json.dumps(state))


# --------------------------------------------------------------------------- #
# The setting: one schema row, off by default, asks every time from chat
# --------------------------------------------------------------------------- #


def test_the_setting_is_one_explicit_off_by_default_ask_floor_row():
    d = schema.get("lan_access")
    assert d.label == LABEL
    assert d.store == "config" and d.type == "bool" and d.restart is True
    assert d.tier == "ask-floor" and d.tier_for(True) == "ask-floor"
    assert "lan_access" in schema.daemon_keys(), "the Settings page's PUT must accept it"
    assert any(row["key"] == "lan_access" for row in schema.public_schema()["settings"])
    from iron_jarvis.core.config import Config

    assert Config.model_fields["lan_access"].default is False


def test_chat_can_only_change_it_through_the_protected_card(tmp_path):
    """ask-floor: config_set and config_change refuse it, naming the one tool
    that always asks (config_change_protected, never offered "Always")."""
    from iron_jarvis.settings.tools import ALWAYS_CARD_TOOLS, ConfigChangeTool, ConfigSetTool
    from iron_jarvis.tools.base import ToolContext

    app = create_app(str(tmp_path))
    p = app.state.platform
    ctx = ToolContext(
        workspace=tmp_path, session_id="chat", agent_run_id="chat",
        config=p.config, event_bus=p.event_bus, engine=p.engine,
    )
    for tool in (ConfigSetTool(p), ConfigChangeTool(p)):
        res = asyncio.run(tool.execute({"key": "lan_access", "value": True}, ctx))
        assert res.ok is False and "config_change_protected" in (res.error or ""), res
    assert p.config.lan_access is False, "a refused call changed nothing"
    assert "config_change_protected" in ALWAYS_CARD_TOOLS


# --------------------------------------------------------------------------- #
# The desktop half, under node: off is the old spawn; on opens the LAN only
# --------------------------------------------------------------------------- #


@requires_node
def test_the_toml_reader_turns_on_only_for_a_real_top_level_true(tmp_path):
    out = _node(
        {
            "toml": {
                "plain": 'default_provider = "mock"\nlan_access = true\n',
                "crlf_bom_comment": '﻿a = 1\r\nlan_access = true   # on purpose\r\n',
                "quoted": '"lan_access" = true\n',
                "false": "lan_access = false\n",
                "absent": 'default_provider = "mock"\n',
                "inside_a_table": '[permissions]\nlan_access = true\n',
                "after_a_table": 'a = 1\n[sandbox]\nx = 2\nlan_access = true\n',
                "typo": "lan_access = True\n",
                "string": 'lan_access = "true"\n',
                "other_key": "lan_access_extra = true\n",
                "empty": "",
            }
        },
        tmp_path,
    )
    assert out["cases"] == {
        "plain": True,
        "crlf_bom_comment": True,
        "quoted": True,
        "false": False,
        "absent": False,
        "inside_a_table": False,
        "after_a_table": False,
        "typo": False,
        "string": False,
        "other_key": False,
        "empty": False,
    }


@requires_node
def test_only_this_pcs_private_addresses_qualify(tmp_path):
    out = _node(
        {
            "ifaces": {
                **IFACES,
                "edges": [
                    {"address": "10.0.0.5", "family": 4, "internal": False},
                    {"address": "172.16.0.1", "family": "IPv4", "internal": False},
                    {"address": "172.31.255.1", "family": "IPv4", "internal": False},
                    {"address": "172.32.0.1", "family": "IPv4", "internal": False},
                    {"address": "100.128.0.1", "family": "IPv4", "internal": False},
                    {"address": "192.168.1.20", "family": "IPv4", "internal": False},
                    {"address": "192.168.1.999", "family": "IPv4", "internal": False},
                ],
            }
        },
        tmp_path,
    )
    assert out["addresses"] == [
        "192.168.1.20",
        "100.101.102.103",
        "10.0.0.5",
        "172.16.0.1",
        "172.31.255.1",
    ], out["addresses"]


@requires_node
def test_off_is_exactly_the_spawn_it_always_was(tmp_path):
    """No config, or the setting off: 127.0.0.1, no allowlist, no log line —
    and an allowlist the user set themselves is left exactly as it was (the
    spawn env adds nothing)."""
    home = tmp_path / "home"
    home.mkdir()
    out = _node({"home": str(home), "homeIfaces": IFACES, "root": "R"}, tmp_path, "off_none")
    assert out["args"] == ["serve", "--host", "127.0.0.1", "--port", "8787", "--root", "R"]
    assert out["net"]["env"] == {} and out["logs"] == []
    (home / "config.toml").write_text("lan_access = false\n", encoding="utf-8")
    out = _node({"home": str(home), "homeIfaces": IFACES, "ambient": "my-box"}, tmp_path, "off_false")
    assert out["net"] == {"host": "127.0.0.1", "env": {}, "lan": []}


@requires_node
def test_on_with_no_private_address_still_opens_nothing(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text("lan_access = true\n", encoding="utf-8")
    only_public = {"lo": IFACES["Loopback Pseudo-Interface 1"], "pub": IFACES["Public"]}
    out = _node({"home": str(home), "homeIfaces": only_public}, tmp_path, "on_none")
    assert out["net"]["host"] == "127.0.0.1" and out["net"]["env"] == {}
    assert any("no private network address" in line for line in out["logs"]), out["logs"]


# --------------------------------------------------------------------------- #
# The seam: the REAL writer writes the file the desktop REALLY reads
# --------------------------------------------------------------------------- #


@requires_node
def test_saved_through_the_real_settings_route_the_next_spawn_listens_on_the_lan(tmp_path, monkeypatch):
    monkeypatch.delenv("IRONJARVIS_HOST_ALLOWLIST", raising=False)
    app = create_app(str(tmp_path))
    client = TestClient(app)
    home = Path(app.state.platform.config.home)
    before = _node({"home": str(home), "homeIfaces": IFACES}, tmp_path, "before")
    assert before["net"]["host"] == "127.0.0.1", "off by default"

    saved = client.put("/settings/values", json={"values": {"lan_access": True}})
    assert saved.status_code == 200, saved.text
    change = saved.json()
    assert change["updated"] == ["lan_access"] and change["action_id"], "one ledger row with an Undo"
    assert "lan_access = true" in (home / "config.toml").read_text(encoding="utf-8")
    assert client.get("/settings/values").json()["values"]["lan_access"] is True

    on = _node({"home": str(home), "homeIfaces": IFACES, "root": "R"}, tmp_path, "on")
    assert on["args"] == ["serve", "--host", "0.0.0.0", "--port", "8787", "--root", "R"], on["args"]
    assert on["net"]["env"] == {"IRONJARVIS_HOST_ALLOWLIST": "192.168.1.20,100.101.102.103"}
    assert on["key"] == "lan_access" == schema.get("lan_access").key

    # NOTHING OPENED IN THE RUNNING PROCESS: saving changed no environment
    # and the Host guard still refuses the network address until a restart.
    import os

    from iron_jarvis.daemon.auth import _host_ok

    assert "IRONJARVIS_HOST_ALLOWLIST" not in os.environ
    assert _host_ok("192.168.1.20:8787") is False

    # Undo (the ledger row's own inverse) closes it again at the next spawn.
    undone = client.post(f"/undo/{change['action_id']}")
    assert undone.status_code == 200, undone.text
    assert app.state.platform.config.lan_access is False
    after = _node({"home": str(home), "homeIfaces": IFACES}, tmp_path, "undone")
    assert after["net"] == {"host": "127.0.0.1", "env": {}, "lan": []}


@requires_node
def test_an_allowlist_the_user_already_set_is_kept_beside_ours(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text("lan_access = true\n", encoding="utf-8")
    out = _node({"home": str(home), "homeIfaces": IFACES, "ambient": " my-box, 192.168.1.20 "}, tmp_path, "amb")
    assert out["net"]["env"]["IRONJARVIS_HOST_ALLOWLIST"] == "my-box,192.168.1.20,100.101.102.103"


def test_both_spawn_sites_read_the_setting_at_every_spawn():
    """The setting is read INSIDE the spawn closure, so the tray's / Settings'
    "Restart Iron Jarvis" (which calls the stored spawnFn again) applies a
    change — a value read once at startup would need a full relaunch."""
    src = _src()
    packaged = src[src.index("// 1) Frozen daemon.") : src.index("// 2) Next.js standalone server")]
    dev = src[src.index("// 1) Python daemon (FastAPI") : src.index("// 2) Next.js dashboard.")]
    for name, block in (("packaged", packaged), ("dev", dev)):
        closure = block[block.index('startService("daemon", () => {') :]
        assert closure.index("readDaemonNetwork(") < closure.index("spawnChild("), name
        assert "daemonServeArgs(net, DAEMON_PORT" in closure, name
        assert "...net.env" in closure, name
        assert '"--host", "127.0.0.1"' not in block, f"{name}: a hard-coded bind skips the setting"
    assert 'readDaemonNetwork(path.join(stateDir, ".ironjarvis"))' in packaged
    # The ONE place --host is decided.
    assert src.count('"--host"') == 1


def test_the_key_is_one_key_everywhere():
    from iron_jarvis.daemon.routes import agents as routes_agents

    js = _src()
    ts = (ROOT / "dashboard" / "lib" / "anotherJarvis.ts").read_text(encoding="utf-8")
    assert 'const LAN_ACCESS_KEY = "lan_access";' in js
    assert 'export const LAN_SETTING_KEY = "lan_access";' in ts
    assert routes_agents.LAN_ACCESS_KEY == "lan_access"
    assert schema.get(routes_agents.LAN_ACCESS_KEY).label == LABEL


# --------------------------------------------------------------------------- #
# The doors: the sentence points at the setting; the guard and the token hold
# --------------------------------------------------------------------------- #


def test_the_task_door_sentence_points_at_the_setting_never_a_command_line(tmp_path, monkeypatch):
    monkeypatch.delenv("IRONJARVIS_HOST_ALLOWLIST", raising=False)
    client = TestClient(create_app(str(tmp_path)))
    note = client.post("/agents/task-door/enable", json={"url": DOOR_URL}).json()["reachable"]["note"]
    assert f"“{LABEL}” in Settings › Connections" in note, note
    assert "restart" in note.lower() and "token" in note
    for gone in ("ironjarvis serve", "--host", "IRONJARVIS_HOST_ALLOWLIST", "daemon"):
        assert gone not in note, (gone, note)
    assert note.startswith("Iron Jarvis on this computer")

    # Turned on but not yet applied: the sentence says what is left (a restart).
    assert client.put("/settings/values", json={"values": {"lan_access": True}}).status_code == 200
    pending = client.get("/agents/task-door").json()["reachable"]
    assert pending["host_allowed"] is False
    assert "is on and takes effect when Iron Jarvis restarts" in pending["note"], pending["note"]
    assert "--host" not in pending["note"]

    # After the restart the desktop spawned it with the allowlist: reachable, no note.
    monkeypatch.setenv("IRONJARVIS_HOST_ALLOWLIST", "192.168.1.20,100.101.102.103")
    assert client.get("/agents/task-door").json()["reachable"] == {"host_allowed": True, "note": ""}


def test_the_remote_inbound_door_says_the_same_sentence(tmp_path, monkeypatch):
    monkeypatch.delenv("IRONJARVIS_HOST_ALLOWLIST", raising=False)
    client = TestClient(create_app(str(tmp_path)))
    assert client.post(
        "/agents/remote", json={"name": "hermes", "base_url": "http://x:1/task", "kind": "http-task"}
    ).status_code == 200
    lan = "http://192.168.1.20:8787/agents/remote/hermes/inbound"
    note = client.post("/agents/remote/hermes/inbound/enable", json={"url": lan}).json()["reachable"]["note"]
    assert f"“{LABEL}” in Settings › Connections" in note and "--host" not in note
    assert "127.0.0.1" in note, "the same-machine case is still spelled out"


def test_once_open_every_request_still_needs_the_install_token(tmp_path, monkeypatch):
    """What the desktop spawns when the setting is on (the allowlist names
    this PC's private address): a request addressed to that address passes
    the Host guard but still needs the install token; any other name is still
    refused. The task door keeps its OWN token (its leaf is exempt from the
    install token and refuses without the door's)."""
    monkeypatch.setenv("IRONJARVIS_TOKEN", "install-token-123")
    monkeypatch.setenv("IRONJARVIS_HOST_ALLOWLIST", "192.168.1.20")
    client = TestClient(create_app(str(tmp_path)), base_url="http://192.168.1.20:8787")
    lan = {"Host": "192.168.1.20:8787"}
    assert client.get("/sessions", headers=lan).status_code == 401, "the network address alone opens nothing"
    ok = client.get("/sessions", headers={**lan, "Authorization": "Bearer install-token-123"})
    assert ok.status_code == 200, ok.text
    other = client.get(
        "/sessions", headers={"Host": "evil.example:8787", "Authorization": "Bearer install-token-123"}
    )
    assert other.status_code == 403, "a name outside the allowlist is still refused"
    door = client.post(TASK_DOOR_PATH, headers=lan, json={"task": "hi"})
    assert door.status_code in (401, 403), door.text  # door off / no door token: never in


@pytest.mark.parametrize("value", [True, False])
def test_the_writer_validates_it_as_a_switch(tmp_path, value):
    app = create_app(str(tmp_path))
    w = app.state.platform.config_writer
    w.apply({"lan_access": value}, actor="test")
    assert w.current("lan_access") is value
    from iron_jarvis.settings.writer import SettingError

    with pytest.raises(SettingError, match="must be on or off"):
        w.apply({"lan_access": "yes"}, actor="test")
