"""v1.344.0 ("Jarvis looks after itself"), lane L-tools: the self-care tools.

``tools/selfcare.py`` gives a chat turn or an agent run the app's OWN health
(``app_health``, ``pack_status``, ``run_doctor``) and the two repairs a user
otherwise presses by hand (``pack_restart`` = the Tools page's Retry,
``provider_rescan`` = the Connections page's Re-detect). Three lifts make
each tool and its route ONE truth: ``routes.settings.diagnostics_snapshot``,
``mcp.tools.reload_pack`` and ``routes.connections.rescan_providers``.

What is pinned here, against the REAL platform (``create_app``):

* every tool advertises an object ``input_schema`` (the v1.341.0 rule), the
  readers are READONLY + replay-safe, the repairs are irreversible;
* ``app_health`` names a failed pack with reason + fix, a failing loop and
  the recent errors, MASKED (planted tokens never reach the answer), reads
  OFF the loop, and survives a broken reader by NAMING it (ok stays True);
* ``pack_status`` for one pack and for all, never a value (env, args);
* ``pack_restart`` reloads through ``reload_pack`` (a real stdio fixture
  server lists two tools; a bad launcher answers the real error);
* ``provider_rescan`` calls the lifted rescan and drops paths;
* ``run_doctor`` answers the runtime check rows;
* the three routes answer byte-identically through the lifted functions;
* the repairs are ASK tier with no grant and REFUSED under low trust through
  the real ``registry.invoke``; the readers are allow tier.
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient

from iron_jarvis.core import logging as core_logging
from iron_jarvis.core.models import PermissionMode
from iron_jarvis.core.trust import LOW_TRUST_DENY, low_trust_overrides, low_trust_refusal
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import connections as connections_mod
from iron_jarvis.daemon.routes.connections import rescan_providers
from iron_jarvis.daemon.routes.settings import diagnostics_snapshot
from iron_jarvis.mcp import FakeTransport
from iron_jarvis.mcp import tools as mcp_tools_mod
from iron_jarvis.mcp.tools import load_status, mcp_tools, reload_pack, unload_pack_tools
from iron_jarvis.onboarding.doctor import runtime_checks
from iron_jarvis.tools import selfcare
from iron_jarvis.tools.base import Reversibility, RiskClass, ToolContext, replay_safe
from iron_jarvis.tools.selfcare import SELFCARE_READERS, SELFCARE_REPAIRS, SELFCARE_TOOLS

FIXTURES = Path(__file__).parent / "fixtures"
#: A real, healthy stdio server (advertises `echo` + `add`).
ECHO_SERVER = str(FIXTURES / "echo_mcp_server.py")
#: A stdio command that cannot exist (the live "npx not found" shape).
NO_SUCH_COMMAND = "ij-no-such-mcp-launcher-v1344"

TOOLS_LIST = {
    "tools": [
        {"name": "search", "description": "Search.", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "fetch", "description": "Fetch.", "inputSchema": {"type": "object", "properties": {}}},
    ]
}

#: Every secret VALUE planted below; none may reach a tool answer.
PLANTED = (
    "sk-live-plantedenvvalue1234",  # a pack env value
    "sk-ant-plantedargvalue5678",  # a pack arg
    "abc123plantedlooptoken",  # a loop's last_error (token=...)
    "ghp_plantedringtoken12345",  # a ring error line (Bearer ...)
)

_SRC = Path(__file__).resolve().parents[1] / "src" / "iron_jarvis"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


@pytest.fixture(autouse=True)
def _fresh_load_records():
    mcp_tools_mod._LOAD_STATUS.clear()
    yield
    mcp_tools_mod._LOAD_STATUS.clear()


def _app(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    d = client.app.state.d
    return client, platform, d


def _ctx(platform, tmp_path, sid: str = "chat") -> ToolContext:
    return ToolContext(
        workspace=tmp_path,
        session_id=sid,
        agent_run_id="r1",
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )


async def _run(platform, tmp_path, name: str, args: dict | None = None):
    tool = platform.registry.get(name)
    assert tool is not None, f"{name} is not registered"
    return await tool.execute(dict(args or {}), _ctx(platform, tmp_path))


def _flat(res) -> str:
    """Everything a result hands out, as one string."""
    return "\n".join([res.output or "", res.error or "", json.dumps(res.data, default=str)])


# --- specs -------------------------------------------------------------------


def test_every_selfcare_tool_is_registered_with_an_object_schema(platform):
    registry = platform.registry
    for name in SELFCARE_TOOLS:
        tool = registry.get(name)
        assert tool is not None, f"{name} is not registered on the real platform"
        spec = tool.spec()
        assert spec["name"] == name
        assert spec["input_schema"]["type"] == "object", name
        assert spec["description"]
    for name in SELFCARE_READERS:
        tool = registry.get(name)
        assert tool.reversibility is Reversibility.READONLY, name
        assert tool.risk_class is RiskClass.READ, name
        assert replay_safe(tool) is True, name
    for name in SELFCARE_REPAIRS:
        tool = registry.get(name)
        assert tool.reversibility is Reversibility.IRREVERSIBLE, name
        assert replay_safe(tool) is False, name
    assert registry.get("pack_restart").spec()["input_schema"]["required"] == ["name"]
    assert "name" in registry.get("pack_status").spec()["input_schema"]["properties"]
    assert "required" not in registry.get("pack_status").spec()["input_schema"]


# --- app_health ---------------------------------------------------------------


def _plant_failed_pack(platform):
    platform.config.mcp_servers = [
        {
            "name": "brave",
            "command": NO_SUCH_COMMAND,
            "args": ["--token", PLANTED[1]],
            "env": {"BRAVE_API_KEY": PLANTED[0]},
        }
    ]
    # Boot-shaped load of the configured list; it leaves the failure record.
    assert mcp_tools(platform.config.mcp_servers, secret_resolver=platform.secrets.get) == []
    assert load_status("brave")["state"] == "failed"


@pytest.fixture
def planted_ring_error():
    row = {
        "ts": "2026-10-16T00:00:00.000+00:00",
        "level": "ERROR",
        "logger": "iron_jarvis.test",
        "message": f"call refused\nAuthorization: Bearer {PLANTED[3]}\nsecond line",
    }
    core_logging._RECENT_ERRORS.append(row)
    yield row
    try:
        core_logging._RECENT_ERRORS.remove(row)
    except ValueError:
        pass


@pytest.mark.asyncio
async def test_app_health_names_a_failed_pack_a_failing_loop_and_recent_errors_masked(
    tmp_path, planted_ring_error, monkeypatch
):
    client, platform, d = _app(tmp_path)
    _plant_failed_pack(platform)
    d.loop_health["sampler"] = {"ok": False, "last_error": f"sampler died: token={PLANTED[2]}", "at": "now"}
    # The doctor rows are read OFF the loop: record the reader's thread.
    seen: list[int] = []
    real_doctor = selfcare.read_doctor_rows

    def spy_doctor(p):
        seen.append(threading.get_ident())
        return real_doctor(p)

    monkeypatch.setattr(selfcare, "read_doctor_rows", spy_doctor)
    loop_thread = threading.get_ident()

    res = await _run(platform, tmp_path, "app_health")
    assert res.ok is True
    out, data = res.output, res.data
    everything = _flat(res)
    # The failed pack, with its classified reason and the fix, by NAME.
    assert data["packs_by_state"]["failed"] == ["brave"]
    pack = data["packs"][0]
    assert pack["reason"] and pack["fix"]
    assert "brave failed:" in out and "Fix:" in out
    assert "Settings > Tools" in out
    # The failing loop, masked.
    assert data["failing_loops"] == [{"name": "sampler", "last_error": "sampler died: token=***", "at": "now"}]
    assert "Failing background loops: sampler (sampler died: token=***)." in out
    # The ring: one line per error, masked, newlines folded.
    ring = [e for e in data["recent_errors"] if "iron_jarvis.test" in e]
    assert len(ring) == 1
    assert ring[0] == "2026-10-16T00:00:00.000+00:00 ERROR iron_jarvis.test: call refused | Authorization: Bearer *** | second line"
    assert "\n" not in ring[0]
    assert f"- {ring[0]}" in out
    # The doctor rows that are not ok name the pack too; nothing is unreadable.
    assert "mcp" in [r["name"] for r in data["doctor_not_ok"]]
    assert data["unreadable"] == []
    assert data["disk"]["free_bytes"] > 0 and data["disk"]["total_bytes"] > 0
    assert data["interrupted_sessions"] == 0
    assert data["version"] and out.startswith(f"Iron Jarvis {data['version']}.")
    assert data["db_liveness"] == "ok" and data["secrets_key_valid"] is True
    assert data["model"]["provider"] == platform.config.default_provider
    assert "circuits_open" in data
    # NOT ONE planted value reaches the answer, in words or in data.
    for secret in PLANTED:
        assert secret not in everything, secret
    # Off the loop: the doctor reader ran on a worker thread.
    assert seen and all(t != loop_thread for t in seen)


@pytest.mark.asyncio
async def test_app_health_survives_broken_readers_and_names_each(tmp_path, monkeypatch):
    client, platform, d = _app(tmp_path)

    def boom(*_a, **_k):
        raise RuntimeError("ring exploded token=abc123planted")

    monkeypatch.setattr(selfcare, "read_recent_errors", boom)
    monkeypatch.setattr(platform.providers, "health", boom)
    monkeypatch.setattr(selfcare, "read_interrupted_count", boom)

    res = await _run(platform, tmp_path, "app_health")
    assert res.ok is True, res.error
    data = res.data
    broken = {u["section"]: u["error"] for u in data["unreadable"]}
    assert set(broken) == {"recent errors", "model", "interrupted jobs"}
    for err in broken.values():
        assert err.startswith("RuntimeError: ring exploded")
        assert "abc123planted" not in err  # masked even on the error path
    assert "Could not read: " in res.output
    for section in ("recent errors", "model", "interrupted jobs"):
        assert section in res.output
    # The rest still answered.
    assert "recent_errors" not in data and "model" not in data and "interrupted_sessions" not in data
    assert data["disk"]["free_bytes"] > 0
    assert "packs" in data and "doctor_not_ok" in data and data["db_liveness"] == "ok"


@pytest.mark.asyncio
async def test_app_health_reads_the_same_snapshot_as_the_diagnostics_route(tmp_path, monkeypatch):
    client, platform, d = _app(tmp_path)
    calls: list = []
    real = selfcare.read_diagnostics

    def spy(obj):
        calls.append(obj)
        return real(obj)

    monkeypatch.setattr(selfcare, "read_diagnostics", spy)
    res = await _run(platform, tmp_path, "app_health")
    assert res.ok is True
    assert calls == [d], "app_health must read the daemon's deps, the same object the route reads"
    # and the reader IS the route's function (one truth, by source).
    src = _read(_SRC / "tools" / "selfcare.py")
    assert "from ..daemon.routes.settings import diagnostics_snapshot" in src
    route_src = _read(_SRC / "daemon" / "routes" / "settings.py")
    assert "return diagnostics_snapshot(d)" in route_src


# --- pack_status --------------------------------------------------------------


@pytest.mark.asyncio
async def test_pack_status_for_one_and_all_packs_never_a_value(tmp_path):
    client, platform, d = _app(tmp_path)
    _plant_failed_pack(platform)
    good = FakeTransport({"tools/list": TOOLS_LIST})
    platform.config.mcp_servers.append(
        {"name": "srv", "transport_obj": good, "env_secrets": {"API_TOKEN": "mcp.srv.API_TOKEN"}}
    )
    for tool in mcp_tools([platform.config.mcp_servers[1]]):
        platform.registry.register(tool, mcp=True)
    assert platform.registry.mcp_names("srv") == ["mcp__srv__fetch", "mcp__srv__search"]

    res = await _run(platform, tmp_path, "pack_status")
    assert res.ok is True
    rows = {p["name"]: p for p in res.data["packs"]}
    assert set(rows) == {"brave", "srv"}
    assert rows["brave"]["state"] == "failed"
    assert rows["brave"]["env_keys"] == ["BRAVE_API_KEY"] and rows["brave"]["secret_keys"] == []
    assert rows["brave"]["reason"] and rows["brave"]["fix"] and rows["brave"]["last_error"]
    assert rows["srv"]["state"] == "ok" and rows["srv"]["tools_loaded"] == 2
    assert rows["srv"]["tool_names"] == ["fetch", "search"]
    assert rows["srv"]["secret_keys"] == ["API_TOKEN"]
    assert "srv: ok, 2 tools loaded." in res.output
    assert "brave: did not start." in res.output and "Fix:" in res.output
    for secret in PLANTED:
        assert secret not in _flat(res), secret
    for key in ("args", "env", "env_secrets", "cwd", "headers", "auth"):
        assert key not in rows["brave"], key

    one = await _run(platform, tmp_path, "pack_status", {"name": "srv"})
    assert one.ok is True and [p["name"] for p in one.data["packs"]] == ["srv"]
    assert one.output == "srv: ok, 2 tools loaded."

    none = await _run(platform, tmp_path, "pack_status", {"name": "nope"})
    assert none.ok is False
    assert "no pack named 'nope'" in none.error and "brave, srv" in none.error

    platform.config.mcp_servers = []
    empty = await _run(platform, tmp_path, "pack_status")
    assert empty.ok is True and empty.data == {"packs": []}


# --- pack_restart -------------------------------------------------------------


@pytest.mark.asyncio
async def test_pack_restart_reloads_through_reload_pack_with_a_real_stdio_server(tmp_path, monkeypatch):
    client, platform, d = _app(tmp_path)
    platform.config.mcp_servers = [
        {"name": "echo", "command": sys.executable, "args": [ECHO_SERVER]},
        {"name": "bad", "command": NO_SUCH_COMMAND, "args": []},
    ]
    calls: list = []
    real = mcp_tools_mod.reload_pack

    def spy(p, name, **kw):
        calls.append((p, name, kw))
        return real(p, name, **kw)

    monkeypatch.setattr(mcp_tools_mod, "reload_pack", spy)
    try:
        res = await _run(platform, tmp_path, "pack_restart", {"name": "echo"})
        assert res.ok is True, res.error
        assert res.output == "Restarted echo: 2 tools loaded."
        assert res.data["name"] == "echo" and res.data["tools_loaded"] == 2 and res.data["last_error"] is None
        assert platform.registry.mcp_names("echo") == ["mcp__echo__add", "mcp__echo__echo"]
        # The tool holds the build-time forwarder (`_LazyPlatform`); it must
        # resolve to THIS platform's registry and config.
        assert len(calls) == 1 and calls[0][1] == "echo"
        assert calls[0][0].registry is platform.registry and calls[0][0].config is platform.config
        assert calls[0][2] == {"home": platform.config.home}
        # A second restart replaces, never doubles.
        again = await _run(platform, tmp_path, "pack_restart", {"name": "echo"})
        assert again.ok is True
        assert len(platform.registry.mcp_names("echo")) == 2
    finally:
        unload_pack_tools(platform.registry, "echo")

    bad = await _run(platform, tmp_path, "pack_restart", {"name": "bad"})
    assert bad.ok is False
    assert "FileNotFoundError" in bad.error and bad.error.startswith("bad did not start: ")
    assert bad.data["reason"] and bad.data["fix"]
    assert "Fix:" in bad.error and "Settings > Tools" in bad.error
    # The same words the Tools row shows (one load record).
    assert load_status("bad")["last_error"] == bad.data["last_error"]

    unknown = await _run(platform, tmp_path, "pack_restart", {"name": "nope"})
    assert unknown.ok is False and "configured packs: bad, echo" in unknown.error
    blank = await _run(platform, tmp_path, "pack_restart", {"name": ""})
    assert blank.ok is False and "needs the pack's name" in blank.error


def test_reload_route_answers_byte_identically_through_reload_pack(tmp_path):
    client, platform, d = _app(tmp_path)
    good = FakeTransport({"tools/list": TOOLS_LIST})
    platform.config.mcp_servers = [
        {"name": "brave_search", "command": NO_SUCH_COMMAND, "args": []},
        {"name": "srv", "transport_obj": good},
    ]
    # The route's v1.229.0 answer, byte for byte.
    assert client.post("/mcp/servers/srv/reload").json() == {"ok": True, "tools_loaded": 2, "last_error": None}
    r = client.post("/mcp/servers/brave_search/reload").json()
    assert set(r) == {"ok", "tools_loaded", "last_error"}
    assert r["ok"] is False and "FileNotFoundError" in r["last_error"]
    assert client.post("/mcp/servers/nope/reload").status_code == 404
    # The lifted function carries the three plus reason/fix/stderr_tail.
    full = reload_pack(platform, "brave_search", home=platform.config.home)
    assert set(full) == {"ok", "tools_loaded", "last_error", "reason", "fix", "stderr_tail"}
    assert {k: full[k] for k in ("ok", "tools_loaded", "last_error")} == r
    assert full["reason"] and full["fix"]
    with pytest.raises(KeyError):
        reload_pack(platform, "nope")
    # The route CALLS the lift (a second copy of the loop would drift).
    src = _read(_SRC / "daemon" / "routes" / "agents.py")
    assert "r = reload_pack(d.platform, name, home=d.platform.config.home)" in src


# --- /diagnostics ---------------------------------------------------------------


DIAGNOSTICS_KEYS = {
    "db_liveness",
    "db_integrity",
    "db_bytes",
    "wal_bytes",
    "secrets_key_present",
    "secrets_key_valid",
    "running_sessions",
    "pending_reviews",
    "background_loops",
    "startup",
    "mcp_servers",
    "tracked_worktrees",
    "providers",
}


def test_diagnostics_route_is_the_lifted_snapshot(tmp_path):
    client, platform, d = _app(tmp_path)
    d.loop_health["sampler"] = {"ok": False, "last_error": "boom", "at": "now"}
    platform.config.mcp_servers = [{"name": "brave", "command": NO_SUCH_COMMAND, "args": []}]
    mcp_tools(platform.config.mcp_servers, secret_resolver=platform.secrets.get)
    body = client.get("/diagnostics").json()
    assert set(body) == DIAGNOSTICS_KEYS
    assert body == jsonable_encoder(diagnostics_snapshot(d))
    assert body["background_loops"]["sampler"]["last_error"] == "boom"
    assert body["mcp_servers"][0]["name"] == "brave" and body["mcp_servers"][0]["state"] == "failed"
    # A BARE platform answers the same shape with the daemon-only parts empty.
    bare = diagnostics_snapshot(platform)
    assert set(bare) == DIAGNOSTICS_KEYS
    assert bare["background_loops"] == {}
    assert bare["startup"] == {"total_ms": None, "at": None, "steps_ms": {}}
    assert bare["mcp_servers"] == body["mcp_servers"]
    assert bare["db_liveness"] == "ok"


# --- provider_rescan ------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_rescan_calls_the_lifted_function_and_drops_paths(tmp_path, monkeypatch):
    client, platform, d = _app(tmp_path)
    private_path = "C:/Users/someone/AppData/Roaming/npm/codex.cmd"
    calls: list = []

    def fake_rescan(deps):
        calls.append(deps)
        return {
            "detected": [
                {
                    "provider": "codex-cli",
                    "model": "subscription",
                    "name": "Codex CLI",
                    "available": False,
                    "source": "cli",
                    "base_url": None,
                    "exec_path": private_path,
                    "context_window": None,
                    # The probe's own words (a CLI's stderr) are PROVIDER text:
                    # masked before they reach the answer.
                    "detail": f"codex is installed but not signed in. Authorization: Bearer {PLANTED[3]}\nRun codex login",
                }
            ]
        }

    monkeypatch.setattr(connections_mod, "rescan_providers", fake_rescan)
    res = await _run(platform, tmp_path, "provider_rescan")
    assert res.ok is True, res.error
    assert len(calls) == 1 and calls[0].platform is platform
    assert calls[0] is d, "the tool hands the rescan the daemon's deps, the same object the route hands it"
    assert res.data["detected"] == [
        {
            "provider": "codex-cli",
            "name": "Codex CLI",
            "model": "subscription",
            "available": False,
            "source": "cli",
            "detail": "codex is installed but not signed in. Authorization: Bearer *** | Run codex login",
        }
    ]
    assert private_path not in _flat(res) and "exec_path" not in _flat(res)
    for secret in PLANTED:
        assert secret not in _flat(res), secret
    assert "codex-cli: codex is installed but not signed in. Authorization: Bearer *** | Run codex login" in res.output
    assert res.output.startswith("Available now: ")
    assert "changed" in res.data and "available_now" in res.data
    assert isinstance(res.data["available_now"], list)


@pytest.mark.asyncio
async def test_provider_rescan_reports_what_changed(tmp_path, monkeypatch):
    client, platform, d = _app(tmp_path)
    states = iter([{"openai": False, "ollama": False}, {"openai": True, "ollama": False}])
    monkeypatch.setattr(selfcare.ProviderRescanTool, "_availability", staticmethod(lambda p: next(states)))
    monkeypatch.setattr(connections_mod, "rescan_providers", lambda deps: {"detected": []})
    res = await _run(platform, tmp_path, "provider_rescan")
    assert res.ok is True
    assert res.data["changed"] == [{"provider": "openai", "was": False, "now": True}]
    assert res.data["available_now"] == ["openai"]
    assert "Changed: openai (now available)." in res.output
    assert res.output.startswith("Available now: openai.")


def test_rescan_route_answers_through_the_lifted_function(tmp_path):
    client, platform, d = _app(tmp_path)
    body = client.post("/providers/rescan").json()
    assert set(body) == {"detected"}
    direct = rescan_providers(d)
    assert set(direct) == {"detected"}
    assert [r["provider"] for r in body["detected"]] == [r["provider"] for r in direct["detected"]]
    src = _read(_SRC / "daemon" / "routes" / "connections.py")
    assert "return rescan_providers(d)" in src


# --- run_doctor -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_doctor_answers_the_runtime_check_rows(tmp_path, monkeypatch):
    client, platform, d = _app(tmp_path)
    _plant_failed_pack(platform)
    seen: list[int] = []
    real = selfcare.read_doctor_rows

    def spy(p):
        seen.append(threading.get_ident())
        return real(p)

    monkeypatch.setattr(selfcare, "read_doctor_rows", spy)
    res = await _run(platform, tmp_path, "run_doctor")
    assert res.ok is True, res.error
    expected = [r["name"] for r in runtime_checks(platform)]
    assert [c["name"] for c in res.data["checks"]] == expected
    for c in res.data["checks"]:
        assert set(c) == {"name", "ok", "detail", "fix", "level"}
    not_ok = {c["name"] for c in res.data["not_ok"]}
    assert "mcp" in not_ok
    assert res.output.startswith(f"{len(expected)} runtime checks, {len(res.data['not_ok'])} not ok.")
    assert "- mcp [NOT OK, recommended]:" in res.output and "Fix:" in res.output
    for secret in PLANTED:
        assert secret not in _flat(res), secret
    assert seen and all(t != threading.get_ident() for t in seen)


@pytest.mark.asyncio
async def test_the_readers_never_probe_the_network_or_start_a_process(tmp_path, monkeypatch):
    """REVIEW FINDING (v1.344.0 L-review). ``runtime_checks`` is the Settings
    doctor: with a custom endpoint configured (the daily driver's shape) it
    ``httpx.get``s the gateway's model list, and it reads the CLI sign-in
    verdicts OUTSIDE ``cli_auth.cached_verdicts`` (a stale verdict starts a
    ``claude auth status`` child). ``app_health`` and ``run_doctor`` are
    allow-tier readers advertised as "nothing is probed" — the v1.334.0
    system-map rule: no process start, no network. They read the doctor
    with ``probe=False``; the Settings doctor itself is the CONTROL."""
    import subprocess

    import httpx

    from iron_jarvis.providers import cli_auth

    client, platform, d = _app(tmp_path)
    platform.config.custom_base_url = "http://127.0.0.1:9/v1"
    platform.config.custom_model = "brain"
    gets: list[str] = []

    def no_get(url, *a, **k):
        gets.append(str(url))
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", no_get)
    started: list[object] = []
    real_init = subprocess.Popen.__init__

    def spy(self, *a, **k):
        started.append(a[0] if a else k.get("args"))
        return real_init(self, *a, **k)

    monkeypatch.setattr(subprocess.Popen, "__init__", spy)
    cached_seen: list[bool] = []

    def login_status(name):
        cached_seen.append(cli_auth._CACHED_ONLY.get())
        return {"installed": False, "signed_in": None, "detail": "not installed"}

    monkeypatch.setattr(platform.providers, "cli_login_status", login_status)

    for name in ("app_health", "run_doctor"):
        res = await _run(platform, tmp_path, name)
        assert res.ok is True, (name, res.error)
        assert gets == [], f"{name} called the gateway: {gets}"
        assert started == [], f"{name} started a process: {started}"
    assert cached_seen and all(cached_seen), "the readers must read CLI verdicts in the cached view"
    rows = {c["name"] for c in (await _run(platform, tmp_path, "run_doctor")).data["checks"]}
    assert "custom_endpoint_model" not in rows
    # CONTROL: the Settings doctor (probe=True, the default) DOES call the
    # gateway, and its own CLI block reads the verdicts OUTSIDE the cached
    # view (the reads inside ``providers.health()`` are cached either way).
    cached_seen.clear()
    runtime_checks(platform)
    assert gets == ["http://127.0.0.1:9/v1/models"]
    assert cached_seen and False in cached_seen
    # CONTROL: the spy does see a process start.
    subprocess.run(["cmd", "/c", "exit", "0"] if sys.platform == "win32" else ["true"], check=False)
    assert len(started) == 1


# --- tiers and trust ------------------------------------------------------------


def test_repairs_are_in_the_low_trust_deny_list_and_ask_tier_with_no_grant(tmp_path):
    client, platform, d = _app(tmp_path)
    engine = platform.permissions
    for name in SELFCARE_REPAIRS:
        assert name in LOW_TRUST_DENY, name
        decision = engine.authorize(name, {"name": "x"}, None)
        assert decision.mode is PermissionMode.ASK and decision.allowed is False, name
        # A user's answer to the card lifts it; low trust never does.
        assert engine.authorize(name, {"name": "x"}, None, session_allow={name}).allowed is True
        low = low_trust_overrides({name: "allow"})
        denied = engine.authorize(name, {"name": "x"}, low, session_allow={name})
        assert denied.allowed is False and denied.mode is PermissionMode.DENY, name


def test_readers_are_allow_tier(tmp_path):
    """The readers run unasked in a headless agent run (the permissions
    engine's defaults in ``core/config.py``); a fail-closed ``ask`` would
    deny every scheduled run its health check."""
    client, platform, d = _app(tmp_path)
    engine = platform.permissions
    for name in SELFCARE_READERS:
        assert engine.mode_for(name) is PermissionMode.ALLOW, name
        assert engine.authorize(name, {}, None).allowed is True, name


@pytest.mark.asyncio
async def test_repairs_are_refused_under_low_trust_through_the_real_registry(tmp_path, monkeypatch):
    client, platform, d = _app(tmp_path)
    ran: list[str] = []

    async def never(self, args, ctx):
        ran.append(self.name)
        raise AssertionError("execute must not run under low trust")

    monkeypatch.setattr(selfcare.PackRestartTool, "execute", never)
    monkeypatch.setattr(selfcare.ProviderRescanTool, "execute", never)
    ctx = _ctx(platform, tmp_path)
    for name, args in (("pack_restart", {"name": "echo"}), ("provider_rescan", {})):
        low = low_trust_overrides({name: "allow"})
        res = await platform.registry.invoke(
            name, args, ctx, platform.permissions, agent_overrides=low, session_allow={name}
        )
        assert res.ok is False, name
        assert res.error.startswith("permission denied: "), res.error
        assert ran == [], f"{name} ran under low trust"
        # CONTROL: the same call at full trust reaches execute (the registry
        # turns the planted exception into a failed result, never a raise).
        control = await platform.registry.invoke(
            name, args, ctx, platform.permissions, agent_overrides={name: "allow"}, session_allow={name}
        )
        assert control.ok is False and "must not run" in (control.error or ""), control
        assert ran == [name]
        ran.clear()


@pytest.mark.asyncio
async def test_low_trust_refusal_words_ride_the_chat_lanes_deny_seam(tmp_path):
    """The chat lanes refuse a kept-away call with ``low_trust_refusal`` as
    ``deny_reason`` + ``deny_label="low trust"``; the registry keeps the
    caller's label."""
    client, platform, d = _app(tmp_path)
    ctx = _ctx(platform, tmp_path)
    res = await platform.registry.invoke(
        "pack_restart",
        {"name": "echo"},
        ctx,
        platform.permissions,
        deny_reason=low_trust_refusal("pack_restart", False),
        deny_label="low trust",
    )
    assert res.ok is False
    assert res.error == "low trust: this run is in low trust — pack_restart is kept away"


# --- the deps door ----------------------------------------------------------------


def test_daemon_deps_reaches_the_daemons_object_or_a_shim(tmp_path):
    client, platform, d = _app(tmp_path)
    assert selfcare.daemon_deps(platform) is d
    bare = SimpleNamespace(config=platform.config)
    shim = selfcare.daemon_deps(bare)
    assert shim.platform is bare
    # A lazy holder that is still empty is a shim too, never a crash.
    from iron_jarvis.platform import _LazyPlatform

    lazy = _LazyPlatform([])
    assert selfcare.daemon_deps(lazy).platform is lazy


def test_one_line_masks_folds_and_cuts():
    s = selfcare.one_line(f"Authorization: Bearer {PLANTED[3]}\n\n  api_key = {PLANTED[0]}  \nend")
    assert s == "Authorization: Bearer *** | api_key = *** | end"
    assert selfcare.one_line(None) == "" and selfcare.one_line("") == ""
    long = selfcare.one_line("x" * 500, 50)
    assert len(long) == 50 and long.endswith("\u2026")
