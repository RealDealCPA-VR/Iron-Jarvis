"""The gateway-key fix, reviewed (v1.382.0).

The fix makes Iron Jarvis SEND a saved key on many more requests, so these pins
attack the one outcome that matters most: the key going somewhere it should
not, or a test/scratch daemon still knocking on the user's real gateway.

* A daemon started OUTSIDE pytest's process (the update rehearsal, the e2e
  launcher, a scratch stack) must not read the developer's real OpenCode
  config either — ``IRONJARVIS_OPENCODE_CONFIG_DIR`` points that read at a
  scratch place, and ``tests/conftest.py`` sets it for every child.
* The key only ever goes to the server it was saved for: the matching rule,
  redirects (httpx strips ``Authorization`` on a cross-origin redirect but NOT
  ``x-api-key``), and the voice fallback.
* The key never reaches a log line, an event, ``fleet-routes.json`` or an
  answer.
* Volume: a proxy with no ``/metrics`` is not asked for it on every pass.

The key is a fake test value; failure messages name paths and counts only.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.fixtures.fake_litellm_gateway_v1382 import FakeGateway

REPO = Path(__file__).resolve().parents[1]

#: A FAKE key for the stand-ins (never a real one, never printed).
KEY = "test-gateway-key-review-v1382-not-real"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
    else:
        proc.kill()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        pass


# --------------------------------------------------------------------------- #
# 1. A daemon in ANOTHER process never reads the developer's OpenCode config
# --------------------------------------------------------------------------- #
def test_the_override_is_the_only_place_opencodes_config_is_read(tmp_path):
    """``opencode_config_paths`` is the body of ``_config_paths`` (which
    conftest replaces in this process). With the override set, ONLY the
    override's folder is read — not the home folder, not ``XDG_CONFIG_HOME``."""
    from iron_jarvis.providers import opencode as oc

    env = {"IRONJARVIS_OPENCODE_CONFIG_DIR": str(tmp_path / "oc"), "XDG_CONFIG_HOME": str(tmp_path / "xdg")}
    paths = oc.opencode_config_paths(env)
    assert paths and all(Path(p).parent == tmp_path / "oc" for p in paths), paths
    plain = oc.opencode_config_paths({"XDG_CONFIG_HOME": str(tmp_path / "xdg")})
    assert any(".config" in str(p) for p in plain)  # without it: the user's home
    assert any(str(tmp_path / "xdg") in str(p) for p in plain)
    blank = oc.opencode_config_paths({"IRONJARVIS_OPENCODE_CONFIG_DIR": "  "})
    assert any(".config" in str(p) for p in blank)  # a blank value is "not set"


def test_a_child_process_inherits_the_suites_scratch_opencode_config():
    """What the update rehearsal and every test that spawns ``serve`` hand
    their child: ``os.environ``. The child's OpenCode config paths are all
    under the suite's scratch folder."""
    scratch = os.environ.get("IRONJARVIS_OPENCODE_CONFIG_DIR", "")
    assert scratch, "conftest did not set IRONJARVIS_OPENCODE_CONFIG_DIR for children"
    code = (
        "import json\n"
        "from iron_jarvis.providers import opencode as oc\n"
        "print(json.dumps([str(p) for p in oc._config_paths()]))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO),
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    paths = json.loads(out.stdout.strip().splitlines()[-1])
    assert paths and all(Path(p).parent == Path(scratch) for p in paths), paths


@pytest.mark.timeout(240)
def test_a_spawned_daemon_never_asks_the_gateway_its_users_opencode_points_at(tmp_path):
    """END TO END in a SEPARATE process, the way the update rehearsal starts
    ``serve``: the child's home folder holds an OpenCode config that points at
    a recording stand-in gateway (the user's real setup). With the suite's
    environment the daemon boots, warms OpenCode — and the stand-in hears
    NOTHING. Without the override this exact boot sent ``GET /model/info``
    with no key."""
    gateway = FakeGateway(KEY)
    gateway.start()
    proc: subprocess.Popen | None = None
    try:
        fake_home = tmp_path / "userhome"
        cfg_dir = fake_home / ".config" / "opencode"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "opencode.json").write_text(
            json.dumps({"provider": {"gw": {"options": {"baseURL": gateway.base + "/v1"}}}}),
            "utf-8",
        )
        root = tmp_path / "root"
        root.mkdir()
        port = _free_port()
        env = {k: v for k, v in os.environ.items() if k != "IRONJARVIS_TOKEN"}
        env["USERPROFILE"] = str(fake_home)
        env["HOME"] = str(fake_home)
        env.pop("XDG_CONFIG_HOME", None)
        env["PYTHONIOENCODING"] = "utf-8"
        log = (tmp_path / "serve.log").open("wb")
        proc = subprocess.Popen(
            [sys.executable, "-c", "from iron_jarvis.daemon.cli import app; app()",
             "serve", "--host", "127.0.0.1", "--port", str(port), "--root", str(root)],
            cwd=str(REPO), env=env, stdout=log, stderr=subprocess.STDOUT,
        )
        import httpx

        deadline = time.monotonic() + 180
        up = False
        while time.monotonic() < deadline and proc.poll() is None:
            try:
                if httpx.get(f"http://127.0.0.1:{port}/health", timeout=2).status_code == 200:
                    up = True
                    break
            except Exception:  # noqa: BLE001 — still booting
                pass
            time.sleep(0.5)
        assert up, (tmp_path / "serve.log").read_text("utf-8", "replace")[-3000:]
        # warm_opencode runs on a boot thread; /health asks available("opencode-cli")
        # which waits for it (single-flight). Give a stray read time to land.
        time.sleep(3.0)
        assert gateway.paths() == [], f"the spawned daemon asked the gateway: {gateway.paths()}"
    finally:
        if proc is not None:
            _kill_tree(proc)
        gateway.stop()


def _text(path: Path) -> str:
    # CRLF rule: a Windows runner checks these out with CRLF.
    return path.read_text("utf-8").replace("\r\n", "\n")


def test_the_e2e_and_scratch_launchers_point_opencode_at_a_scratch_folder():
    """The Playwright e2e's daemon and the browser add-on's scratch daemon are
    started outside pytest, so conftest cannot reach them: each launcher sets
    the override itself."""
    from iron_jarvis.providers.opencode import CONFIG_DIR_ENV

    pw = _text(REPO / "dashboard" / "playwright.config.ts")
    daemon_block = pw.split("webServer:", 1)[1].split("npx next dev", 1)[0]
    assert "ironjarvis serve" in daemon_block
    assert f'{CONFIG_DIR_ENV}: "e2e/.home/' in daemon_block
    sh = _text(REPO / "scripts" / "verify_browser_addon.sh")
    serve_line = next(line for line in sh.splitlines() if "ironjarvis serve" in line and "uv run" in line)
    assert f'{CONFIG_DIR_ENV}="$ROOT/' in serve_line


# --------------------------------------------------------------------------- #
# 2. The key goes ONLY to the server it was saved for
# --------------------------------------------------------------------------- #
def _scratch_app(tmp_path, values: dict, *, key: str = KEY):
    from fastapi.testclient import TestClient

    from iron_jarvis.core.config import persist_config_values, resolve_home
    from iron_jarvis.daemon.app import create_app

    home = resolve_home(Path(tmp_path).resolve())
    home.mkdir(parents=True, exist_ok=True)
    persist_config_values(home, {"fleet_sampling_enabled": False, **values})
    app = create_app(str(tmp_path))
    if key:
        r = TestClient(app).post("/connections/custom/key", json={"key": key})
        assert r.status_code == 200, r.status_code
    return app


def test_voice_never_hands_the_gateway_key_to_a_different_speech_server(tmp_path):
    """A dedicated speech-to-text server with no key of its own used to be
    sent the CUSTOM endpoint's key ("if the STT endpoint shares it") — a
    different server, possibly a cloud one (the setting names Groq). It now
    gets the key saved for ITS server only; the same gateway still gets it."""
    import base64

    from fastapi.testclient import TestClient

    gateway = FakeGateway(KEY)
    gateway.start()
    stt = FakeGateway("stt-own-key-not-real")
    stt.start()
    try:
        audio = {"audio_b64": base64.b64encode(b"RIFF....").decode(), "mime": "audio/wav"}
        app = _scratch_app(tmp_path / "a", {
            "custom_base_url": gateway.base + "/v1", "custom_model": "glm-5.3-flash",
            "voice_transcribe_base_url": stt.base + "/v1",
        })
        with TestClient(app) as client:
            client.post("/voice/transcribe", json=audio)
        assert stt.paths(), "the speech server was never asked"
        sent_auth = [p for p, r in zip(stt.paths(), stt.log()) if r["any_auth"]]
        assert sent_auth == [], f"a key went to another server: {sent_auth}"
        # the SAME gateway as the STT endpoint: its saved key goes along
        app = _scratch_app(tmp_path / "b", {
            "custom_base_url": gateway.base + "/v1", "custom_model": "glm-5.3-flash",
            "voice_transcribe_base_url": gateway.base + "/v1",
        })
        with TestClient(app) as client:
            got = client.post("/voice/transcribe", json=audio).json()
        assert got.get("text") == "hello from the stand-in"
        assert gateway.unkeyed() == []
    finally:
        gateway.stop()
        stt.stop()


class _Catcher:
    """A second server: records every request that reaches it (path and
    whether ANY auth header came — never a value)."""

    def __init__(self) -> None:
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        self.requests: list[tuple[str, bool]] = []
        catcher = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a):
                return

            def _any(self):
                n = int(self.headers.get("Content-Length") or 0)
                if n:
                    self.rfile.read(n)
                auth = bool(self.headers.get("Authorization") or self.headers.get("x-api-key"))
                catcher.requests.append((f"{self.command} {self.path.split('?')[0]}", auth))
                raw = b'{"data": [], "object": "list"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_GET = do_POST = _any

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self._srv.daemon_threads = True
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self._srv.server_address[1]}"

    def stop(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()


class _Redirector:
    """A "gateway" that answers EVERY request with 307 to the catcher (same
    path) — a hijacked or misconfigured server. Records what it received."""

    def __init__(self, to: str) -> None:
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        self.requests: list[tuple[str, bool]] = []
        red = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a):
                return

            def _any(self):
                n = int(self.headers.get("Content-Length") or 0)
                if n:
                    self.rfile.read(n)
                auth = bool(self.headers.get("Authorization") or self.headers.get("x-api-key"))
                red.requests.append((f"{self.command} {self.path.split('?')[0]}", auth))
                self.send_response(307)
                self.send_header("Location", to + self.path)
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_GET = do_POST = _any

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self._srv.daemon_threads = True
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self._srv.server_address[1]}"

    def stop(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()


def test_no_keyed_caller_follows_a_redirect_to_another_server(tmp_path, monkeypatch):
    """httpx strips ``Authorization`` on a cross-origin redirect but NOT
    ``x-api-key``: a keyed call that followed redirects would hand an
    Anthropic-style key to whatever server the gateway (or anyone who took
    over its address) pointed at. EVERY keyed caller is driven against a
    gateway that redirects everything to a second server, which must hear
    NOTHING."""
    import asyncio
    import base64
    import functools
    import importlib

    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.routes import connections as conn_routes
    from iron_jarvis.daemon.routes import envelope as envelope_routes
    from iron_jarvis.envelope.profile import CapabilityProfile
    from iron_jarvis.envelope.seed import seed_profile
    from iron_jarvis.fleet import probes
    from iron_jarvis.fleet.adapter import adapter_for
    from iron_jarvis.fleet.endpoint_auth import auth_headers
    from iron_jarvis.fleet.models import FleetNode
    from iron_jarvis.fleet.upstreams import read_routes
    from iron_jarvis.providers import discovery
    from iron_jarvis.providers import opencode as oc
    from iron_jarvis.providers.adapters.base import LLMMessage
    from iron_jarvis.providers.router import ModelRouter

    catcher = _Catcher()
    red = _Redirector(catcher.base)
    seeded: list[str] = []

    async def fake_battery(profile, transport, **kw):
        seeded.append(profile.model_id)
        return CapabilityProfile(model_id=profile.model_id, provider=profile.provider, source="probed")

    monkeypatch.setattr(envelope_routes, "run_quick_battery", fake_battery)
    discovery.clear_cache()
    try:
        bearer, xkey = auth_headers(KEY), auth_headers(KEY, "anthropic")
        # -- the readers, each with its own transport -------------------------
        for kind in ("litellm", "unknown", "vllm", "ollama", "openai-compat"):
            probes.probe_node(FleetNode(id="r", base_url=red.base + "/v1", kind=kind), headers=bearer)
        probes.probe_node(FleetNode(id="a", base_url=red.base, protocol="anthropic"), headers=xkey)
        probes.detect_kind(red.base, headers=bearer)
        read_routes(FleetNode(id="r", base_url=red.base), headers=bearer)
        discovery._fetch("fleet-a", lambda: KEY, red.base, "anthropic")
        discovery._fetch("fleet-r", lambda: KEY, red.base + "/v1", "openai", True)
        discovery._fetch("custom", lambda: KEY, red.base + "/v1", "openai")
        try:
            discovery.list_endpoint_models(red.base + "/v1", KEY, ollama_ok=True)
        except Exception:  # noqa: BLE001 — a 307 is not a listing
            pass
        asyncio.run(seed_profile("custom", "vision", red.base + "/v1", headers=bearer))
        asyncio.run(seed_profile("fleet-a", "m", red.base, headers=xkey, ollama=True))
        oc._passthrough_aliases(red.base + "/v1", None, bearer)
        conn_routes._probe_endpoint_models(red.base + "/v1", KEY, timeout_s=5.0)
        conn_routes._probe_anthropic_models(red.base, KEY, timeout_s=5.0)
        # -- the router's liveness probe, through each adapter's own client --
        for protocol in ("openai", "anthropic"):
            adapter = adapter_for(
                FleetNode(id="r", base_url=red.base + "/v1", protocol=protocol, routable=True),
                credential=lambda: KEY,
            )
            router = ModelRouter.__new__(ModelRouter)
            router._answered_at = {}
            router._clock = time.monotonic
            asyncio.run(router._local_liveness(adapter))
        # -- the app's own doors, configured at the redirecting gateway -------
        app = _scratch_app(tmp_path, {
            "custom_base_url": red.base + "/v1", "custom_model": "vision",
            "default_provider": "custom", "default_model": "vision",
            "fleet_nodes": [{"id": "anth", "base_url": red.base, "protocol": "anthropic",
                             "api_key_name": "custom_api_key", "routable": True}],
        })
        d = app.state.d
        with TestClient(app) as client:
            d.fleet_sampler.sample_once(force=True)
            client.post("/fleet/nodes/custom/detect")
            client.post("/fleet/probe", json={"base_url": red.base})
            client.get("/models")
            client.post("/connections/endpoints/models", json={"base_url": red.base + "/v1"})
            client.post("/connections/endpoints/models", json={"base_url": red.base, "protocol": "anthropic"})
            client.post("/providers/endpoint-models", json={"base_url": red.base + "/v1", "api_key": ""})
            assert client.post("/envelope/custom/vision/probe").status_code == 200
            end = time.monotonic() + 20
            while not seeded and time.monotonic() < end:
                time.sleep(0.05)
            client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}],
                                       "provider": "custom", "model": "vision"})
            importlib.import_module("iron_jarvis.onboarding.doctor").runtime_checks(d.platform)
            client.post("/voice/transcribe", json={
                "audio_b64": base64.b64encode(b"RIFF....").decode(), "mime": "audio/wav"})
            try:
                client.portal.call(functools.partial(
                    d.platform.router.complete, provider="fleet-anth", model="m",
                    system="", messages=[LLMMessage(role="user", content="hi")], tools=[]))
            except Exception:  # noqa: BLE001 — a 307 is not an answer
                pass
        keyed = [p for p, auth in red.requests if auth]
        assert len(keyed) >= 20, f"too few keyed requests reached the gateway to prove anything: {len(keyed)}"
        assert any(p.startswith("POST") for p, auth in red.requests if auth)
        assert catcher.requests == [], f"a redirect was followed to another server: {catcher.requests}"
    finally:
        discovery.clear_cache()
        red.stop()
        catcher.stop()


# --------------------------------------------------------------------------- #
# 3. Volume: a keyed request that cannot teach anything is not repeated
# --------------------------------------------------------------------------- #
def _sampler_at(gateway, *, kind="litellm"):
    from types import SimpleNamespace

    from iron_jarvis.fleet.models import FleetNode
    from iron_jarvis.fleet.registry import FleetRegistry
    from iron_jarvis.fleet.sampler import FleetSampler
    from iron_jarvis.fleet.upstreams import read_routes

    node = FleetNode(id="gw", base_url=gateway.base + "/v1", kind=kind, api_key_name="gw_key", routable=True)
    cfg = SimpleNamespace(home=".", fleet_nodes=[node.model_dump()], custom_base_url="",
                          custom_model="", ollama_base_url="")
    reg = FleetRegistry(cfg, persist=lambda *a, **k: None)
    reg.set_secret_resolver({"gw_key": KEY}.get)
    now = [1000.0]
    sampler = FleetSampler(
        reg, clock=lambda: now[0],
        route_reader=lambda n: read_routes(n, headers=reg.headers_for(n)),
    )
    return sampler, now


def _hour(sampler, now, interval: float, seconds: float = 3600.0) -> None:
    t = 0.0
    while t < seconds:
        sampler.sample_once()
        now[0] += interval
        t += interval


def test_a_proxy_without_metrics_is_not_asked_for_them_every_pass():
    """The user's proxy has no ``/metrics``; once the sampler carries the key
    every pass used to ask anyway and get a keyed 404 — 1,800 an hour while
    the Fleet page is open (measured). A 404 is remembered for 10 minutes;
    the routing table read keeps its own 60 s clock under the 2 s cadence."""
    gateway = FakeGateway(KEY)
    gateway.start()
    try:
        sampler, now = _sampler_at(gateway)
        _hour(sampler, now, 2.0)
        paths = gateway.paths()
        metrics = paths.count("GET /metrics")
        assert 1 <= metrics <= 7, f"/metrics asked {metrics} times in an hour"
        # the probe's own read (liveness + topology, every pass) and the
        # routing-table read (60 s TTL): 1,800 + 60
        assert paths.count("GET /model/info") <= 1800 + 61, paths.count("GET /model/info")
        assert gateway.unkeyed() == []
        snap = next(s for s in sampler.snapshots() if s.node.id == "gw")
        assert snap.metrics_reason == "LiteLLM /metrics is not enabled on this proxy"
    finally:
        gateway.stop()


def test_a_refresh_or_re_detect_asks_for_metrics_again():
    """The remembered 404 never outlives the user asking: a forced Refresh
    (and Re-detect, which calls the same forget) asks ``/metrics`` again — a
    user who just turned metrics on is not told "not enabled" for 10 minutes."""
    gateway = FakeGateway(KEY)
    gateway.start()
    try:
        sampler, now = _sampler_at(gateway)
        sampler.sample_once()
        sampler.sample_once()
        assert gateway.paths().count("GET /metrics") == 1
        sampler.sample_once(force=True)
        assert gateway.paths().count("GET /metrics") == 2
        sampler.forget_metrics(sampler.registry.get("gw"))
        sampler.sample_once()
        assert gateway.paths().count("GET /metrics") == 3
        now[0] += 601.0
        sampler.sample_once()
        assert gateway.paths().count("GET /metrics") == 4
    finally:
        gateway.stop()


def test_a_proxy_whose_metrics_answer_is_not_a_404_is_still_asked_each_pass(monkeypatch):
    """Only a 404 (the endpoint does not exist) is remembered: a refused key
    or a server error is asked again next pass, as before."""
    from iron_jarvis.fleet import probes
    from iron_jarvis.fleet.models import FleetNode

    calls: list[str] = []

    class R:
        def __init__(self, code, body=None):
            self.status_code = code
            self._body = body or {}
            self.text = ""

        def json(self):
            return self._body

    def get(url, headers=None):
        calls.append(url.rsplit("/", 1)[-1])
        if url.endswith("/model/info"):
            return R(200, {"data": []})
        return R(503)

    memo = probes.MetricsMemo(clock=lambda: 0.0)
    node = FleetNode(id="gw", base_url="http://gw:4000", kind="litellm")
    for _ in range(3):
        probes.probe_node(node, get=get, metrics_memo=memo)
    assert calls.count("metrics") == 3


def test_re_detect_on_the_fleet_page_asks_for_metrics_again(tmp_path):
    from fastapi.testclient import TestClient

    gateway = FakeGateway(KEY)
    gateway.start()
    try:
        app = _scratch_app(tmp_path, {
            "custom_base_url": gateway.base + "/v1", "custom_model": "vision",
            "fleet_nodes": [{"id": "gw", "base_url": gateway.base, "kind": "litellm",
                             "api_key_name": "custom_api_key", "routable": True}],
        })
        d = app.state.d
        with TestClient(app) as client:
            d.fleet_sampler.sample_once()
            d.fleet_sampler.sample_once()
            assert gateway.paths().count("GET /metrics") == 1
            assert client.post("/fleet/nodes/gw/detect").json()["kind"] == "litellm"
            d.fleet_sampler.sample_once()
            assert gateway.paths().count("GET /metrics") == 2
        assert gateway.unkeyed() == []
    finally:
        gateway.stop()


# --------------------------------------------------------------------------- #
# 4. The key never reaches a log line, an event, a saved file or an answer
# --------------------------------------------------------------------------- #
class _EchoGateway:
    """A gateway that REJECTS the key it is sent and echoes it back in the
    401 body ("Invalid key: <what you sent>") — some servers do. Whatever a
    background read does with an error, the key must not ride it out."""

    def __init__(self) -> None:
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        self.keyed = 0

        gw = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a):
                return

            def _any(self):
                n = int(self.headers.get("Content-Length") or 0)
                if n:
                    self.rfile.read(n)
                sent = self.headers.get("Authorization") or self.headers.get("x-api-key") or ""
                if sent:
                    gw.keyed += 1
                if self.path.startswith("/api/"):
                    code, body = 404, {"detail": "Not Found"}
                else:
                    code, body = 401, {"error": {"message": f"Invalid key: {sent}", "code": "401"}}
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_GET = do_POST = _any

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self._srv.daemon_threads = True
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self._srv.server_address[1]}"

    def stop(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()


class _Capture:
    """Every log record from every logger, formatted, at DEBUG."""

    def __init__(self) -> None:
        import logging

        self.lines: list[str] = []
        cap = self

        class H(logging.Handler):
            def emit(self, record):
                try:
                    text = record.getMessage()
                except Exception:  # noqa: BLE001
                    text = str(record.msg)
                if record.exc_info:
                    import traceback

                    text += "".join(traceback.format_exception(*record.exc_info))
                cap.lines.append(f"{record.name}: {text}")

        self.handler = H(level=logging.DEBUG)
        self.names = ["", "ironjarvis", "iron_jarvis", "httpx"]
        self._old: dict[str, int] = {}

    def __enter__(self):
        import logging

        for name in self.names:
            lg = logging.getLogger(name)
            self._old[name] = lg.level
            lg.setLevel(logging.DEBUG)
            lg.addHandler(self.handler)
        return self

    def __exit__(self, *exc):
        import logging

        for name in self.names:
            lg = logging.getLogger(name)
            lg.removeHandler(self.handler)
            lg.setLevel(self._old[name])


def _drive_every_door(client, d, base: str) -> list[str]:
    """Every gateway door; returns every answer body as text."""
    import base64
    import importlib

    out: list[str] = []

    def keep(resp):
        out.append(resp.text)
        return resp

    d.fleet_sampler.sample_once(force=True)
    keep(client.post("/fleet/nodes/custom/detect"))
    d.fleet_sampler.sample_once(force=True)
    keep(client.post("/fleet/probe", json={"base_url": base, "api_key_name": "custom_api_key"}))
    keep(client.post("/fleet/probe", json={"base_url": base}))
    keep(client.get("/models"))
    keep(client.post("/connections/endpoints/models", json={"base_url": base + "/v1"}))
    keep(client.post("/providers/endpoint-models", json={"base_url": base + "/v1", "api_key": ""}))
    keep(client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}],
                                    "provider": "custom", "model": "vision"}))
    out.append(json.dumps(importlib.import_module("iron_jarvis.onboarding.doctor").runtime_checks(d.platform)))
    keep(client.post("/voice/transcribe", json={
        "audio_b64": base64.b64encode(b"RIFF....").decode(), "mime": "audio/wav"}))
    for path in ("/fleet", "/fleet/nodes/custom", "/models", "/connections", "/health",
                 "/diagnostics", "/settings", "/envelope/custom/vision"):
        keep(client.get(path))
    return out


@pytest.mark.parametrize("server", ["accepts", "echoes"])
def test_the_key_never_reaches_a_log_an_event_a_file_or_an_answer(tmp_path, monkeypatch, server):
    from fastapi.testclient import TestClient
    from sqlmodel import Session, select

    from iron_jarvis.core.models import EventRecord

    gw = FakeGateway(KEY) if server == "accepts" else _EchoGateway()
    if server == "accepts":
        gw.start()
    try:
        app = _scratch_app(tmp_path, {
            "custom_base_url": gw.base + "/v1", "custom_model": "vision",
            "default_provider": "custom", "default_model": "vision",
        })
        d = app.state.d
        with _Capture() as cap, TestClient(app) as client:
            answers = _drive_every_door(client, d, gw.base)
            with Session(d.platform.engine) as s:
                events = [r.payload_json for r in s.exec(select(EventRecord)).all()]
        if server == "accepts":
            assert gw.log() and gw.unkeyed() == []
            assert (tmp_path / ".ironjarvis" / "fleet-routes.json").exists() or any(
                p.name == "fleet-routes.json" for p in tmp_path.rglob("fleet-routes.json"))
        else:
            assert gw.keyed > 5, "the echoing gateway was never sent the key: the drive proved nothing"
        leaks = []
        for i, text in enumerate(answers):
            if KEY in text:
                leaks.append(f"answer #{i}")
        leaks += [f"log line {line.split(':', 1)[0]}" for line in cap.lines if KEY in line]
        leaks += ["an event payload" for p in events if KEY in (p or "")]
        for f in tmp_path.rglob("*"):
            if f.is_file():
                try:
                    if KEY.encode() in f.read_bytes():
                        leaks.append(f"file {f.relative_to(tmp_path)}")
                except OSError:
                    pass
        assert leaks == [], f"the key reached: {sorted(set(leaks))}"
        routes = list(tmp_path.rglob("fleet-routes.json"))
        for f in routes:
            assert "api_base" not in f.read_text("utf-8")
    finally:
        gw.stop()


def test_an_error_text_never_carries_a_credential_the_request_sent():
    """The scrub works off the REQUEST's own headers (whatever the adapter
    sent), takes the bare key out of "Bearer <key>", and catches a key the
    caller's 300-char cut sliced in half."""
    import httpx

    from iron_jarvis.providers.adapters.base import provider_error_from_response, scrub_sent_credentials

    for headers in ({"Authorization": f"Bearer {KEY}"}, {"x-api-key": KEY}, {"api-key": KEY},
                    {"x-goog-api-key": KEY}):
        req = httpx.Request("POST", "http://gw:4000/v1/chat/completions", headers=headers)
        resp = httpx.Response(401, request=req, text="nope")
        err = provider_error_from_response("custom", resp, f"Invalid key: Bearer {KEY} (and {KEY})")
        assert KEY not in str(err) and "[key hidden]" in str(err), headers
        body = ("x" * 280) + f"Invalid key: {KEY}"
        cut = body[:300]
        assert KEY[:4] in cut and KEY not in cut  # the cut sliced the key
        assert KEY[: 300 - 280 - len("Invalid key: ")] not in scrub_sent_credentials(cut, resp)[280:]
    # a fake response with no request: unchanged, never raises
    assert scrub_sent_credentials("text", object()) == "text"
    bare = httpx.Response(401, text="x")
    assert scrub_sent_credentials("text", bare) == "text"
