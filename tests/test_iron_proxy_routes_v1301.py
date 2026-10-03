"""Iron-Proxy routes on Connections (v1.301.0), driven through the REAL app
factory (``create_app`` + its lifespan) against the fake Iron-Proxy
(``tests/fixtures/fake_iron_proxy_v1301.py``).

Pinned here: the route shapes the dashboard card reads; the 409 sentence when
Iron-Proxy is off or not running; Iron-Proxy's hint passed through as
``detail``; PATCH is partial; the control token never reaches a response; the
enable flag survives a restart; the lifespan never delays boot and its loop
reports its own cycles; the sign-in pane's shell gets the account's env on top
of the shell's own (API keys stripped, the proxy's host vars ignored).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

import psutil
import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.iron_proxy.service import IronProxyService
from iron_jarvis.terminals.backend import FakeBackend

FIXTURE = Path(__file__).parent / "fixtures" / "fake_iron_proxy_v1301.py"
sys.path.insert(0, str(FIXTURE.parent))
from fake_iron_proxy_v1301 import FakeIronProxy  # noqa: E402

OFF = "Iron-Proxy is off; turn it on in Connections first."


def _reap_children_of(data_dir: Path) -> None:
    """A test that fails mid-way must not leave an Iron-Proxy child running:
    kill any process whose command line names this test's data dir."""
    needle = str(data_dir)
    for proc in psutil.process_iter(["cmdline"]):
        try:
            if needle in " ".join(proc.info.get("cmdline") or []):
                proc.kill()
        except (psutil.Error, OSError):
            pass


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    monkeypatch.setenv("IRONJARVIS_IRON_PROXY_BUNDLE", str(tmp_path / "no-bundle.mjs"))
    monkeypatch.delenv("IRONJARVIS_IRON_PROXY_NODE", raising=False)
    for k in ("FAKE_IRON_PROXY_EXIT", "FAKE_IRON_PROXY_START_DELAY"):
        monkeypatch.delenv(k, raising=False)
    yield
    _reap_children_of(tmp_path / "ipd")


class _Bodies:
    """Every response body this test saw — scanned for the token at the end."""

    def __init__(self, client: TestClient) -> None:
        self.c = client
        self.seen: list[str] = []

    def __getattr__(self, verb):
        fn = getattr(self.c, verb)

        def call(*a, **k):
            r = fn(*a, **k)
            self.seen.append(r.text)
            return r

        return call


@pytest.fixture
def proj(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    return root


@pytest.fixture
def fake(tmp_path):
    f = FakeIronProxy(tmp_path / "ipd").start()
    yield f
    f.stop()


@pytest.fixture
def app_client(proj):
    with TestClient(create_app(str(proj))) as c:
        yield _Bodies(c)


def _enable(c) -> dict:
    r = c.post("/iron-proxy/enable")
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------- #
# off / not running
# --------------------------------------------------------------------------- #
def test_off_by_default_and_every_account_route_answers_409_one_sentence(app_client):
    c = app_client
    body = c.get("/iron-proxy").json()
    assert body["status"]["enabled"] is False and body["status"]["running"] is False
    assert body["accounts"] == [] and body["discovered"] == []
    assert body["providers_used_by_jarvis"] == {
        "anthropic": "claude-cli", "openai": "codex-cli", "xai": "grok-cli"
    }
    calls = [
        ("post", "/iron-proxy/accounts", {"provider": "anthropic", "title": "A"}),
        ("post", "/iron-proxy/accounts/adopt", {"provider": "anthropic", "home": "x"}),
        ("patch", "/iron-proxy/accounts/p1", {"title": "B"}),
        ("delete", "/iron-proxy/accounts/p1", None),
        ("post", "/iron-proxy/accounts/reorder", {"provider": "anthropic", "ids": []}),
        ("post", "/iron-proxy/accounts/p1/unpark", None),
        ("post", "/iron-proxy/accounts/p1/signin", None),
        ("post", "/iron-proxy/accounts/p1/signout", None),
    ]
    for verb, path, js in calls:
        r = getattr(c, verb)(path, json=js) if js is not None else getattr(c, verb)(path)
        assert r.status_code == 409, (path, r.text)
        assert r.json()["detail"] == OFF


def test_on_but_nothing_running_and_nothing_bundled_is_409_with_the_reason(app_client):
    c = app_client
    body = _enable(c)
    st = body["status"]
    assert st["enabled"] is True and st["running"] is False and st["bundled"] is False
    assert "does not include Iron-Proxy" in st["error"]
    r = c.post("/iron-proxy/accounts", json={"provider": "anthropic", "title": "A"})
    assert r.status_code == 409
    assert r.json()["detail"] == st["error"]


# --------------------------------------------------------------------------- #
# reuse + the view
# --------------------------------------------------------------------------- #
def test_enable_reuses_a_running_proxy_and_the_view_has_the_card_shape(app_client, fake, proj):
    fake.discovered = [
        {"provider": "openai", "binary": "codex", "home": "C:/Users/x/.codex",
         "installed": True, "status": "ok", "suggestedTitle": "Codex (existing login)"},
        {"provider": "anthropic", "binary": "claude", "home": "C:/Users/x/.claude",
         "installed": True, "status": "unauthenticated", "suggestedTitle": "Claude"},
    ]
    c = app_client
    body = _enable(c)
    assert body["status"]["running"] is True and body["status"]["owned"] is False
    assert body["status"]["url"] == fake.url

    r = c.post("/iron-proxy/accounts", json={"provider": "anthropic", "title": "Work Claude"})
    assert r.status_code == 201
    row = r.json()
    assert row["id"] and row["title"] == "Work Claude" and row["lane"] == "cli"
    assert row["home"] and row["home_adopted"] is False
    assert row["state"]["status"] in ("ready", "unknown")

    r = c.post("/iron-proxy/accounts/adopt",
               json={"provider": "anthropic", "home": "C:/Users/x/.claude", "title": "This PC"})
    assert r.status_code == 201
    adopted = r.json()
    assert adopted["home_adopted"] is True and adopted["home"] == "C:/Users/x/.claude"

    # park one so the state chip data shows
    fake.signal(row["id"], {"status": 429, "text": "rate limited"})
    fake.finished(adopted["id"], {})

    view = c.get("/iron-proxy").json()
    by_id = {a["id"]: a for a in view["accounts"]}
    assert set(by_id) == {row["id"], adopted["id"]}
    parked = by_id[row["id"]]["state"]
    assert parked["status"] == "parked" and parked["parkedUntil"]
    assert parked["parkedReason"] == {"kind": "rate-limit", "message": "rate limited"}
    active = by_id[adopted["id"]]
    assert active["state"]["status"] == "active" and active["state"]["served"] == 1
    assert active["usage"] == {"requests_5h": 1, "requests_7d": 1, "parks_7d": 0,
                               "minutes_left": 42}
    # accounts come grouped by provider, in failover order
    assert [a["order"] for a in view["accounts"]] == sorted(a["order"] for a in view["accounts"])
    # discovered: every login, with the account already using it (computed from
    # cli.home when Iron-Proxy does not report adoptedProfileId)
    disc = {d["provider"]: d for d in view["discovered"]}
    assert disc["openai"] == {"provider": "openai", "home": "C:/Users/x/.codex",
                              "signed_in": True, "adopted_profile_id": None,
                              "title": "Codex (existing login)"}
    assert disc["anthropic"]["adopted_profile_id"] == adopted["id"]
    assert disc["anthropic"]["signed_in"] is False

    # the flag is persisted: a fresh app on the same home comes up enabled
    toml = (proj / ".ironjarvis" / "config.toml").read_text(encoding="utf-8")
    assert "iron_proxy_enabled = true" in toml

    for text in c.seen:
        assert fake.token not in text


def test_account_actions_and_partial_patch(app_client, fake):
    c = app_client
    _enable(c)
    a = c.post("/iron-proxy/accounts", json={"provider": "anthropic", "title": "A"}).json()
    b = c.post("/iron-proxy/accounts", json={"provider": "anthropic", "title": "B"}).json()

    # PATCH is PARTIAL: a title-only patch keeps `enabled` (MUTATION: send every
    # field -> enabled is clobbered to null/false)
    r = c.patch(f"/iron-proxy/accounts/{a['id']}", json={"title": "Work"})
    assert r.status_code == 200 and r.json()["title"] == "Work"
    assert r.json()["enabled"] is True
    patches = [call for call in fake.calls if call[0] == "PATCH"]
    assert patches[-1] == ("PATCH", f"/iron/profiles/{a['id']}", {"title": "Work"})
    r = c.patch(f"/iron-proxy/accounts/{a['id']}", json={"enabled": False})
    assert r.json()["enabled"] is False and r.json()["title"] == "Work"
    assert c.patch(f"/iron-proxy/accounts/{a['id']}", json={}).status_code == 400

    assert c.post("/iron-proxy/accounts/reorder",
                  json={"provider": "anthropic", "ids": [b["id"], a["id"]]}).json() == {"ok": True}
    order = [x["id"] for x in c.get("/iron-proxy").json()["accounts"]]
    assert order == [b["id"], a["id"]]

    fake.signal(b["id"], {"status": 429})
    assert c.post(f"/iron-proxy/accounts/{b['id']}/unpark").json() == {"ok": True}
    assert fake.states[b["id"]]["status"] == "ready"
    assert c.post(f"/iron-proxy/accounts/{b['id']}/signout").json() == {"ok": True}
    assert fake.states[b["id"]]["status"] == "unauthenticated"
    assert c.delete(f"/iron-proxy/accounts/{a['id']}").json() == {"ok": True}
    assert a["id"] not in fake.profiles

    for text in c.seen:
        assert fake.token not in text


def test_iron_proxy_errors_pass_their_hint_through_as_detail(app_client, fake):
    c = app_client
    _enable(c)
    r = c.patch("/iron-proxy/accounts/prof_nope", json={"title": "x"})
    assert r.status_code == 404
    _assert_plain(r.json()["detail"])  # the REAL PROFILE_NOT_FOUND hint, rewritten
    r = c.post("/iron-proxy/accounts", json={"provider": "nope", "title": "x"})
    assert r.status_code == 400
    assert r.json()["detail"].startswith("Use one of: anthropic")


def _assert_plain(detail: str) -> None:
    """No Iron-Proxy CLI command or tray-switcher instruction reaches the card;
    it points at the Connections page instead (MUTATION: drop plain_hint)."""
    assert detail
    low = detail.lower()
    assert "iron-proxy profiles" not in low and "iron-proxy login" not in low
    assert "switcher" not in low
    assert "connections" in low


def test_an_account_sign_in_hint_names_the_connections_page(app_client, fake):
    """AUTH_REQUIRED WITH a profileId (an account needing sign-in) carries
    Iron-Proxy's REAL hint; the route answers 409 with it rewritten."""
    from fake_iron_proxy_v1301 import HINT_AUTH_REQUIRED, FakeError

    c = app_client
    _enable(c)
    a = c.post("/iron-proxy/accounts", json={"provider": "anthropic", "title": "A"}).json()
    real = fake.handle

    def needs_sign_in(method, path, headers, body):
        if path.endswith("/unpark"):
            raise FakeError("AUTH_REQUIRED", '"A" needs to sign in.',
                            details={"profileId": a["id"], "title": "A"},
                            hint=HINT_AUTH_REQUIRED)
        return real(method, path, headers, body)

    fake.handle = needs_sign_in  # type: ignore[method-assign]
    r = c.post(f"/iron-proxy/accounts/{a['id']}/unpark")
    assert r.status_code == 409
    _assert_plain(r.json()["detail"])


def test_a_refused_control_token_is_409_never_a_401(app_client, fake):
    """A 401 AUTH_REQUIRED with no profileId is OUR token being refused (not an
    account needing sign-in). The client re-reads proxy.json once; when that
    names the same token the route answers 409 in one sentence — passed through
    as a 401 it would read on the dashboard as the daemon's own sign-in failing."""
    c = app_client
    _enable(c)
    fake.token = "rotated-elsewhere"  # proxy.json still names the old token
    r = c.post("/iron-proxy/accounts", json={"provider": "anthropic", "title": "A"})
    assert r.status_code == 409
    assert r.json()["detail"] == (
        "Iron-Proxy refused Iron Jarvis's access token; turn Iron-Proxy off and on again."
    )


def test_a_restarted_proxy_with_a_new_token_is_picked_up_without_a_toggle(app_client, fake):
    """The proxy restarted on the same port with a fresh token (proxy.json
    rewritten): the refused call re-locates ONCE and succeeds (MUTATION: drop
    the refresh -> 409)."""
    c = app_client
    _enable(c)
    fake.token = "fresh-token-after-restart"
    fake.write_descriptor(os.getpid())
    r = c.post("/iron-proxy/accounts", json={"provider": "anthropic", "title": "A"})
    assert r.status_code == 201, r.text
    assert fake.auth_headers[-1] == "Bearer fresh-token-after-restart"
    for text in c.seen:
        assert "fresh-token-after-restart" not in text


def test_a_proxy_that_went_away_is_reported_not_raised(app_client, fake):
    c = app_client
    _enable(c)
    fake.stop()
    view = c.get("/iron-proxy").json()
    assert view["status"]["running"] is False and view["accounts"] == []
    assert view["status"]["error"]
    r = c.post("/iron-proxy/accounts", json={"provider": "anthropic", "title": "A"})
    assert r.status_code == 409


# --------------------------------------------------------------------------- #
# sign-in pane
# --------------------------------------------------------------------------- #
class _RecordingBackend(FakeBackend):
    last: "_RecordingBackend | None" = None

    def __init__(self) -> None:
        super().__init__()
        self.env: dict | None = None
        self.written: list[str] = []
        _RecordingBackend.last = self

    def start(self, argv, cwd, env, cols, rows) -> None:  # type: ignore[override]
        self.env = dict(env) if env is not None else None
        super().start(argv, cwd, env, cols, rows)

    def write(self, data) -> None:  # type: ignore[override]
        self.written.append(data if isinstance(data, str) else data.decode())
        super().write(data)


def test_signin_opens_a_build_pane_running_the_login_as_that_account(
    app_client, fake, monkeypatch
):
    """The env the BACKEND received (CLAUDE.md: assert what the shell got, not a
    dict): the account's home var on top of the SHELL's own env, the API keys
    gone, the proxy's PATH/CI ignored. MUTATION: drop the env= -> no
    CLAUDE_CONFIG_DIR in the shell."""
    monkeypatch.setattr("iron_jarvis.terminals.session.default_backend", _RecordingBackend)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-reach")
    c = app_client
    _enable(c)
    acct = c.post("/iron-proxy/accounts", json={"provider": "anthropic", "title": "Work Claude"}).json()
    real_login = fake.handle

    def login_with_host_vars(method, path, headers, body):
        status, out = real_login(method, path, headers, body)
        if path.endswith("/login-command"):
            out = {**out, "env": {**out["env"], "PATH": "C:/proxy-only-path", "CI": "1",
                                  "NO_COLOR": "1", "EXTRA_FROM_PROFILE": "yes"}}
        return status, out

    fake.handle = login_with_host_vars  # type: ignore[method-assign]
    r = c.post(f"/iron-proxy/accounts/{acct['id']}/signin")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["name"] == "Sign in: Work Claude" and out["terminal_id"]
    panes = {p["id"]: p for p in c.get("/terminals").json()["terminals"]}
    assert out["terminal_id"] in panes

    be = _RecordingBackend.last
    assert be is not None and be.env is not None
    assert be.env["CLAUDE_CONFIG_DIR"] == acct["home"]
    assert be.env["EXTRA_FROM_PROFILE"] == "yes"
    assert "ANTHROPIC_API_KEY" not in be.env
    assert be.env.get("PATH") == os.environ.get("PATH")  # the shell's, not the proxy's
    assert be.env.get("CI") == os.environ.get("CI")
    assert be.env["IRONJARVIS_PANE_NAME"] == "Sign in: Work Claude"
    typed = "".join(be.written)
    assert typed.endswith("\r") and "auth" in typed and "login" in typed and "claude" in typed


def test_signin_refuses_an_api_key_account_and_an_unknown_id(app_client, fake):
    c = app_client
    _enable(c)
    key = fake.add_profile("openai", "API", "api-key")
    r = c.post(f"/iron-proxy/accounts/{key['id']}/signin")
    assert r.status_code == 400 and "API-key accounts are managed in Iron-Proxy" in r.json()["detail"]
    r = c.post("/iron-proxy/accounts/prof_nope/signin")
    assert r.status_code == 404


def test_command_line_quotes_for_each_shell():
    from iron_jarvis.daemon.routes.iron_proxy import _command_line, _signin_env

    env = {"PATH": ""}
    assert _command_line("powershell", "C:/x y/claude.cmd", ["auth", "login"], env) == (
        "& 'C:/x y/claude.cmd' 'auth' 'login'"
    )
    assert _command_line("pwsh", "it's", [], env) == "& 'it''s'"
    assert _command_line("cmd", "C:/x y/claude.cmd", ["auth"], env) == '"C:/x y/claude.cmd" auth'
    # any cmd metacharacter is quoted, not only a space (MUTATION: spaces only)
    assert _command_line("cmd", "claude", ["a&b", "50%", "x|y", "(z)", "q\"t", "plain"], env) == (
        'claude "a&b" "50%" "x|y" "(z)" "q""t" plain'
    )
    assert _command_line("bash", "claude", ["auth", "a b"], env) == "claude auth 'a b'"
    merged = _signin_env(
        {"CLAUDE_CONFIG_DIR": "/h", "Path": "/proxy", "FORCE_COLOR": "0", "XAI_API_KEY": "k"},
        base={"PATH": "/shell", "OPENAI_API_KEY": "sk", "HOME": "/me"},
    )
    assert merged == {"PATH": "/shell", "HOME": "/me", "CLAUDE_CONFIG_DIR": "/h"}


# --------------------------------------------------------------------------- #
# a REAL child: enable spawns, disable and shutdown stop it
# --------------------------------------------------------------------------- #
@pytest.mark.timeout(90)
def test_enable_spawns_the_bundle_disable_stops_it_and_shutdown_stops_it(
    proj, tmp_path, monkeypatch
):
    monkeypatch.setenv("IRONJARVIS_IRON_PROXY_NODE", sys.executable)
    monkeypatch.setenv("IRONJARVIS_IRON_PROXY_BUNDLE", str(FIXTURE))
    descriptor = tmp_path / "ipd" / "proxy.json"

    def _gone(pid: int) -> bool:
        deadline = time.monotonic() + 10
        while psutil.pid_exists(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        return not psutil.pid_exists(pid)

    with TestClient(create_app(str(proj))) as c:
        st = _enable(c)["status"]
        assert st["running"] is True and st["owned"] is True, st
        pid = json.loads(descriptor.read_text())["pid"]
        st = c.post("/iron-proxy/disable").json()["status"]
        assert st["enabled"] is False and st["running"] is False
        assert _gone(pid)
        assert not descriptor.exists()
        # on again -> a new child; leaving the app (lifespan shutdown) stops it
        assert _enable(c)["status"]["owned"] is True
        pid2 = json.loads(descriptor.read_text())["pid"]
    assert _gone(pid2)


# --------------------------------------------------------------------------- #
# the lifespan: never delays boot, reports its own cycles
# --------------------------------------------------------------------------- #
@pytest.mark.timeout(60)
def test_lifespan_starts_in_the_background_and_reports_on_loop_health(proj, monkeypatch):
    """Enabled at boot: the start runs AFTER boot, OFF the loop (MUTATIONS: await
    the start before the lifespan yields -> boot waits for it and `timed_out`
    is set; call it on the loop -> the thread identity is the loop's)."""
    (proj / ".ironjarvis").mkdir(parents=True)
    (proj / ".ironjarvis" / "config.toml").write_text("iron_proxy_enabled = true\n")
    release = threading.Event()
    state: dict = {"threads": [], "timed_out": False, "fail": False}

    def ensure_running(self):
        state["threads"].append(threading.get_ident())
        if not release.wait(timeout=8):
            state["timed_out"] = True
        if state["fail"]:
            raise RuntimeError("Iron-Proxy did not answer within 10 seconds of starting.")
        return True

    monkeypatch.setattr(IronProxyService, "ensure_running", ensure_running)
    app = create_app(str(proj))
    with TestClient(app) as c:
        # boot finished and the daemon answers while the start is still blocked
        assert c.get("/health").status_code == 200
        deadline = time.monotonic() + 5
        while not state["threads"] and time.monotonic() < deadline:
            time.sleep(0.02)
        assert state["threads"], "the watch loop never ran a cycle"
        loop_health = app.state.d.loop_health
        assert "iron_proxy" not in loop_health  # armed is not healthy
        release.set()
        deadline = time.monotonic() + 5
        while "iron_proxy" not in loop_health and time.monotonic() < deadline:
            time.sleep(0.02)
        assert loop_health["iron_proxy"]["ok"] is True
        assert state["timed_out"] is False
        loop_ident = c.portal.call(lambda: threading.get_ident())
        assert loop_ident not in state["threads"]


@pytest.mark.timeout(60)
def test_a_failing_start_is_named_on_loop_health(proj, monkeypatch):
    (proj / ".ironjarvis").mkdir(parents=True)
    (proj / ".ironjarvis" / "config.toml").write_text("iron_proxy_enabled = true\n")

    def ensure_running(self):
        raise RuntimeError("Iron-Proxy stopped right after starting (exit code 3).")

    monkeypatch.setattr(IronProxyService, "ensure_running", ensure_running)
    app = create_app(str(proj))
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200
        lh = app.state.d.loop_health
        deadline = time.monotonic() + 5
        while "iron_proxy" not in lh and time.monotonic() < deadline:
            time.sleep(0.02)
        assert lh["iron_proxy"]["ok"] is False
        assert "exit code 3" in lh["iron_proxy"]["last_error"]


# --------------------------------------------------------------------------- #
# the REAL vendored bundle (skipped until it is vendored / when node is absent)
# --------------------------------------------------------------------------- #
_REPO = Path(__file__).resolve().parents[1]
_BUNDLE = _REPO / "desktop" / "vendor" / "iron-proxy" / "iron-proxy.mjs"


@pytest.mark.timeout(90)
@pytest.mark.skipif(not _BUNDLE.is_file(), reason="Iron-Proxy bundle not vendored")
@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_real_bundle_starts_answers_and_stops(proj, tmp_path, monkeypatch):
    """The dev fallback (`node` + desktop/vendor/iron-proxy/iron-proxy.mjs)
    against the REAL Iron-Proxy: enable spawns it, an account round-trips, the
    view has the card shape, the version comes from SOURCE.txt, disable kills it."""
    monkeypatch.delenv("IRONJARVIS_IRON_PROXY_BUNDLE", raising=False)
    monkeypatch.delenv("IRONJARVIS_IRON_PROXY_NODE", raising=False)
    descriptor = tmp_path / "ipd" / "proxy.json"
    with TestClient(create_app(str(proj))) as raw:
        c = _Bodies(raw)
        st = _enable(c)["status"]
        assert st["running"] is True and st["owned"] is True and st["bundled"] is True, st
        assert st["version"]
        assert st["error"] is None, "the vendored bundle must advertise executor-v1"
        token = json.loads(descriptor.read_text())["token"]
        pid = json.loads(descriptor.read_text())["pid"]
        r = c.post("/iron-proxy/accounts", json={"provider": "anthropic", "title": "Real"})
        assert r.status_code == 201, r.text
        acct = r.json()
        assert acct["provider"] == "anthropic" and acct["lane"] == "cli" and acct["home"]
        view = c.get("/iron-proxy").json()
        assert [a["id"] for a in view["accounts"]] == [acct["id"]]
        assert view["accounts"][0]["state"]["status"]
        r = c.patch(f"/iron-proxy/accounts/{acct['id']}", json={"title": "Renamed"})
        assert r.status_code == 200 and r.json()["title"] == "Renamed"
        r = c.patch("/iron-proxy/accounts/prof_nope", json={"title": "x"})
        assert r.status_code == 404
        _assert_plain(r.json()["detail"])  # the real bundle's own hint, rewritten
        st = c.post("/iron-proxy/disable").json()["status"]
        assert st["running"] is False
        for text in c.seen:
            assert token not in text
    deadline = time.monotonic() + 10
    while psutil.pid_exists(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not psutil.pid_exists(pid)


# --------------------------------------------------------------------------- #
# reachability: a signed-out default login does not hide a working account
# --------------------------------------------------------------------------- #
@pytest.fixture
def signed_out_cli(monkeypatch):
    """Both CLIs installed, the DEFAULT login (~/.claude, ~/.codex) signed out."""
    from iron_jarvis.providers.manager import ProviderManager

    monkeypatch.setattr(ProviderManager, "_cli_binary_present", staticmethod(lambda b: True))
    monkeypatch.setattr(ProviderManager, "_cli_signed_in", staticmethod(lambda b: False))


def test_a_usable_iron_proxy_account_makes_the_cli_provider_available(
    signed_out_cli, app_client, fake
):
    """Through the REAL ProviderManager: unavailable with no account; the
    account-creating ROUTE refreshes the snapshot so it counts at once
    (MUTATION: no refresh after a mutation -> still unavailable); available()
    never calls Iron-Proxy; an account that needs sign-in, a disabled one, and
    Iron-Proxy turned off all leave it unavailable."""
    c = app_client
    providers = c.c.app.state.platform.providers
    assert providers.available("claude-cli") is False
    _enable(c)
    assert providers.available("claude-cli") is False  # on, but no account
    a = c.post("/iron-proxy/accounts", json={"provider": "anthropic", "title": "A"}).json()
    before = len(fake.calls)
    assert providers.available("claude-cli") is True
    assert providers.available("codex-cli") is False
    assert providers.available("grok-cli") is False
    assert len(fake.calls) == before, "available() must read a cache, never Iron-Proxy"

    c.post("/iron-proxy/accounts", json={"provider": "xai", "title": "G"})
    assert providers.available("grok-cli") is True

    c.patch(f"/iron-proxy/accounts/{a['id']}", json={"enabled": False})
    assert providers.available("claude-cli") is False
    c.patch(f"/iron-proxy/accounts/{a['id']}", json={"enabled": True})
    assert providers.available("claude-cli") is True

    fake.signal(a["id"], {"status": 401})  # needs sign-in; the view re-reads
    c.get("/iron-proxy")
    assert providers.available("claude-cli") is False
    c.post(f"/iron-proxy/accounts/{a['id']}/unpark")
    assert providers.available("claude-cli") is True

    c.post("/iron-proxy/disable")
    assert providers.available("claude-cli") is False
    assert providers.available("grok-cli") is False


def test_the_watch_cycle_refreshes_the_snapshot(signed_out_cli, proj, fake):
    """An account added OUTSIDE Iron Jarvis (tray app) counts after the next
    watch cycle (MUTATION: ensure_running skips refresh_accounts)."""
    (proj / ".ironjarvis").mkdir(parents=True)
    (proj / ".ironjarvis" / "config.toml").write_text("iron_proxy_enabled = true\n")
    fake.add_profile("openai", "Codex")
    app = create_app(str(proj))
    with TestClient(app):
        providers = app.state.platform.providers
        deadline = time.monotonic() + 10
        while not providers.available("codex-cli") and time.monotonic() < deadline:
            time.sleep(0.05)
        assert providers.available("codex-cli") is True
        assert providers.available("claude-cli") is False


def test_an_older_iron_proxy_is_named_on_connections(app_client, tmp_path):
    old = FakeIronProxy(tmp_path / "ipd", features=None).start()
    try:
        st = _enable(app_client)["status"]
        assert st["running"] is True
        assert st["error"].startswith("The Iron-Proxy running on this PC is older")
    finally:
        old.stop()
