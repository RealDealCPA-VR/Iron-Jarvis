"""Fourth fix plan, group 32, C-PG-1: "Sending outside this PC".

To see everywhere Iron Jarvis sends things, the user had to open six screens
(Webhooks, Notifications, Model servers, Connections, Tools, Memory and
Settings › Voice), and nothing said how each one is asked. ``GET
/settings/access`` (Settings › Permissions & ledger, "What Jarvis can reach")
now carries ``sends``: one row per LIVE outside place, how it is asked, and
the page that changes it. Read-only.

Driven through the REAL app factory and the real stores each page itself
writes (the webhook table, the destinations in ``config.comm``, the model
server list, the web service registry, the extension list, the memory base
table, the settings, and the real Connect press for another app). Nothing is
a stand-in for the route or the readers.

Pinned here:
* each kind appears when it is live, and not when it is switched off, on
  this PC only, or never set up;
* hosts only: no path, no query, no user name or password, no ``://`` and no
  secret-shaped piece of any stored address rides the answer;
* building it reaches no network (every socket call refused while it runs)
  and runs off the event loop (a plain ``def`` handler: FastAPI's threadpool).

Secret-shaped values are ASSEMBLED at run time, never one literal.
"""

from __future__ import annotations

import inspect
import json
import socket

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import access_summary
from iron_jarvis.fleet.models import FleetNode
from iron_jarvis.integrations.hookups import add_hookup
from iron_jarvis.ltm.sources import CustomSourceStore
from iron_jarvis.mcpserver import apps
from iron_jarvis.webhooks.models import WebhookRecord

# Assembled at run time: no literal in this file looks like a credential.
PIECE = "tok" + "en-" + "q7" * 9
PASSWORD = "pass" + "-" + "w9" * 6
USER = "someone"


@pytest.fixture()
def scene(tmp_path, monkeypatch):
    other = tmp_path / "other-apps"
    monkeypatch.setattr(apps, "TEST_ROOT", str(other))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-home"))
    app = create_app(str(tmp_path / "home"))

    class Scene:
        pass

    s = Scene()
    s.app = app
    s.d = app.state.d
    s.platform = app.state.d.platform
    s.client = TestClient(app)
    return s


def _sends(scene) -> dict:
    r = scene.client.get("/settings/access")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "sends" in body, sorted(body)
    return body["sends"]


def _by_kind(sends: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for row in sends["places"]:
        out.setdefault(row["kind"], []).append(row)
    return out


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield str(k)
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def _seed_everything(scene) -> None:
    """One live place of every kind, each next to one that is NOT live."""
    p, cfg = scene.platform, scene.platform.config
    # Webhooks: an outgoing one (live), a switched-off one, an incoming one.
    with session_scope(p.engine) as db:
        db.add(WebhookRecord(slug="deploys", direction="outbound", enabled=True,
                             target_url=f"https://{USER}:{PASSWORD}@hooks.example.com/in/{PIECE}?key={PIECE}"))
        db.add(WebhookRecord(slug="old-hook", direction="outbound", enabled=False,
                             target_url="https://retired.example.com/hook"))
        db.add(WebhookRecord(slug="inbound-one", direction="inbound", enabled=True, target_url=""))
        db.add(WebhookRecord(slug="on-this-pc", direction="outbound", enabled=True,
                             target_url="http://127.0.0.1:9000/hook"))
        db.commit()
    # A destination the user set up (Notifications page).
    cfg.comm = {"channels": {"team-alerts": {
        "type": "slack", "webhook_url": f"https://hooks.slack.example.com/services/{PIECE}"}}}
    # Model servers: one on the user's network that answers chats, one only
    # discovered (not used), one on this PC.
    scene.d.fleet.add(FleetNode(id="dgx", label="DGX", base_url="http://192.168.1.20:1234", routable=True))
    scene.d.fleet.add(FleetNode(id="found", label="Found", base_url="http://192.168.1.21:1234", routable=False))
    scene.d.fleet.add(FleetNode(id="here", label="Here", base_url="http://127.0.0.1:1234", routable=True))
    # Ollama on another machine (a setting that sets a place).
    cfg.ollama_base_url = "http://192.168.1.30:11434"
    # Web services: one saved and on, one switched off.
    add_hookup(p, name="Weather", base_url=f"https://api.weather.example.org/v2/{PIECE}")
    add_hookup(p, name="Paused service", base_url="https://paused.example.org")
    p.integrations.enable("paused_service", False)
    # Extensions: a remote one with credentials in its address, a program on
    # this PC, and one switched off.
    cfg.mcp_servers = [
        {"name": "Notes online", "transport": "http",
         "url": f"https://{USER}:{PASSWORD}@mcp.example.net/sse?key={PIECE}"},
        {"name": "Local files", "transport": "stdio", "command": "node", "args": ["server.js"]},
        {"name": "Switched off", "transport": "http", "url": "https://off.example.net/mcp", "enabled": False},
    ]
    # Memory bases: Notion (cloud) and a folder on this PC.
    store = CustomSourceStore(p.engine)
    store.add("team-notes", "notion", database_id="abc123", token_secret="ltm_team-notes_notion")
    store.add("vault", "markdown", path=str(scene.platform.config.home))
    # The speech server.
    cfg.voice_transcribe_base_url = f"https://speech.example.com/v1/{PIECE}"
    # Claude desktop connected through the real press; Codex never set up.
    path = apps.config_path("claude-desktop")
    path.parent.mkdir(parents=True, exist_ok=True)
    r = scene.client.post("/work-record/other-apps/claude-desktop/connect")
    assert r.status_code == 200, r.text
    assert r.json()["connected"] is True


# --------------------------------------------------------------------------- #
def test_the_route_is_a_plain_def_so_it_runs_off_the_event_loop(scene):
    route = next(r for r in scene.app.routes if getattr(r, "path", "") == "/settings/access")
    assert not inspect.iscoroutinefunction(route.endpoint)


def test_nothing_set_up_lists_no_place_and_reads_everything(scene):
    sends = _sends(scene)
    assert sends["places"] == []
    # Every kind was read (an unread kind is said, never shown as nothing).
    assert sends["unread"] == []


def test_each_live_kind_appears_with_its_host_how_it_is_asked_and_its_page(scene):
    _seed_everything(scene)
    sends = _sends(scene)
    assert sends["unread"] == []
    kinds = _by_kind(sends)

    hooks = kinds["webhook"]
    assert [(h["name"], h["where"]) for h in hooks] == [("deploys", "hooks.example.com")]
    assert hooks[0]["href"] == "/webhooks"
    assert hooks[0]["ask"] == "once"

    dest = kinds["destination"]
    assert [(x["name"], x["where"]) for x in dest] == [("team-alerts", "Slack")]
    assert dest[0]["href"] == "/channels"
    assert "Ask first" in dest[0]["asked"]  # the notify tool's own level, in its own words

    servers = {(x["name"], x["where"], x["href"]) for x in kinds["model_server"]}
    assert ("DGX", "192.168.1.20", "/fleet") in servers
    assert any(w == "192.168.1.30" and h == "/settings?focus=ollama_base_url" for _n, w, h in servers)
    assert not any(w in ("192.168.1.21", "127.0.0.1") for _n, w, _h in servers)

    services = kinds["web_service"]
    assert [(x["name"], x["where"]) for x in services] == [("Weather", "api.weather.example.org")]
    assert services[0]["href"] == "/connections"
    assert "anything else asks every time" in services[0]["asked"]

    exts = {x["name"]: x for x in kinds["extension"]}
    assert set(exts) == {"Notes online", "Local files"}
    assert exts["Notes online"]["where"] == "mcp.example.net"
    assert exts["Local files"]["where"] == "a program on this PC"
    assert all(x["href"] == "/tools" for x in exts.values())

    bases = kinds["memory_base"]
    assert [(x["name"], x["where"]) for x in bases] == [("team-notes", "Notion")]
    assert bases[0]["href"] == "/memory"

    speech = kinds["speech_server"]
    assert [x["where"] for x in speech] == ["speech.example.com"]
    assert speech[0]["href"] == "/settings?focus=voice_transcribe_base_url"
    assert "what you dictate" in speech[0]["asked"]

    connected = kinds["app"]
    assert [(x["name"], x["href"]) for x in connected] == [("Claude desktop", "/chat")]
    assert "never shared" in connected[0]["asked"]

    # Every place that changes where data goes says a new place asks.
    for kind in ("webhook", "model_server", "speech_server"):
        for row in kinds[kind]:
            assert "asks every time" in row["asked"], row


def test_a_place_switched_off_or_removed_leaves_the_row(scene):
    _seed_everything(scene)
    p, cfg = scene.platform, scene.platform.config
    with session_scope(p.engine) as db:
        for row in db.exec(__import__("sqlmodel").select(WebhookRecord)):
            row.enabled = False
            db.add(row)
        db.commit()
    cfg.comm = {}
    scene.d.fleet.remove("dgx")
    cfg.ollama_base_url = None
    p.integrations.enable("weather", False)
    cfg.mcp_servers = []
    CustomSourceStore(p.engine).remove("team-notes")
    cfg.voice_transcribe_base_url = ""
    assert scene.client.post("/work-record/other-apps/claude-desktop/disconnect").status_code == 200
    sends = _sends(scene)
    assert sends["places"] == [], sends["places"]


def test_hosts_only_no_path_query_password_or_secret_rides_the_answer(scene, monkeypatch):
    _seed_everything(scene)
    sends = _sends(scene)
    assert len(sends["places"]) >= 9  # anti-vacuity: every kind is there
    for text in _strings(sends):
        assert PIECE not in text, text
        assert PASSWORD not in text, text
        assert USER + ":" not in text, text
        assert "://" not in text, text
        assert "/v2" not in text and "/sse" not in text and "/services" not in text, text
    # The whole answer, not only `sends` (the raw JSON too).
    raw = scene.client.get("/settings/access").text
    assert PIECE not in raw and PASSWORD not in raw


def test_building_it_reaches_no_network(scene, monkeypatch):
    _seed_everything(scene)
    via_route = _sends(scene)
    calls: list[str] = []

    def refused(*_a, **_k):
        calls.append("network")
        raise AssertionError("the outside-places row reached the network")

    # The route's own builder, called with every way out refused (the test
    # client's event loop itself needs a local socket pair on Windows, so the
    # builder is called here directly, the same function the route calls).
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(socket, "create_connection", refused)
        mp.setattr(socket, "getaddrinfo", refused)
        mp.setattr(socket.socket, "connect", refused)
        sends = access_summary.outside_places(scene.d)
    assert calls == []
    assert sends == via_route
    assert sends["unread"] == []
    assert {"webhook", "destination", "model_server", "web_service", "extension", "memory_base",
            "speech_server", "app"} <= {r["kind"] for r in sends["places"]}


def test_a_store_that_cannot_be_read_is_said_never_nothing(scene, monkeypatch):
    _seed_everything(scene)

    def broken(*_a, **_k):
        raise OSError("the list could not be read")

    monkeypatch.setattr(CustomSourceStore, "list", broken)
    sends = _sends(scene)
    assert "memory_base" in sends["unread"]
    assert "memory_base" not in {r["kind"] for r in sends["places"]}
    # The other kinds are still there.
    assert "webhook" in {r["kind"] for r in sends["places"]}
    json.dumps(sends)  # plain data
