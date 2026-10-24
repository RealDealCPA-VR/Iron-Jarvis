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
