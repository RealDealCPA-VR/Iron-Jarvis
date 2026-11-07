"""v1.444.0 — the first ten minutes: connecting a model works the first time.

Three first-run bugs from the 2026-11-07 ease-of-use review (items 1, 3, 4):

1. "Use ChatGPT / Claude for answers" saved a FIXED API model
   (``gpt-4o-mini`` / ``claude-opus-4-8``) for an API name served THROUGH the
   sign-in. Codex only runs its own models (``codex_native`` refuses on
   evidence), so the first question failed. Now every door that makes such a
   name the default — ``POST /onboarding/use-model``, ``POST /connections/{p}
   /default`` and the first-connect promotion — saves the plan's own default
   (``"subscription"``), which both CLIs read as "the plan's default model".
   A real pasted key keeps the table's model exactly as before.
2. A pasted key became the default BEFORE it was tested. ``POST /connections
   /{p}/key?check=1`` (the connect door) tests the key first and stores it only
   when the test passed, and never promotes; the door then chooses it with the
   one explicit press. Without ``check`` the route is unchanged.
3. After signing in to Claude Code / Codex, the door said "not signed in" for
   up to two minutes (``cli_auth.UNSIGNED_RECHECK_S``). A Sign in pane started
   on THIS PC's own login is now watched for the CLI's own "signed in" words
   (``terminals.signin_watch.watch_default_login``), which re-check the shared
   verdict at once. The probe runs ONLY on that evidence — never a test's CLI.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.cli_auth import CliAuthProbe, CliAuthStatus
from iron_jarvis.providers.manager import ProviderManager
from iron_jarvis.terminals import signin_watch as SW
from iron_jarvis.terminals.backend import FakeBackend


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


def _clis(monkeypatch, *, claude: bool = False, codex: bool = False) -> None:
    present = {"claude": claude, "codex": codex}
    monkeypatch.setattr(
        ProviderManager, "_cli_binary_present", staticmethod(lambda b: present.get(b, False))
    )
    monkeypatch.setattr(ProviderManager, "_cli_signed_in", staticmethod(lambda b: True))


def _default(client) -> tuple[str, str]:
    h = client.get("/health").json()
    return h["default_provider"], h["default_model"]


def _use(client, provider: str):
    return client.post("/onboarding/use-model", json={"provider": provider})


# --------------------------------------------------------------------------- #
# FIX 1 — the plan's own default, never a fixed API id
# --------------------------------------------------------------------------- #
def test_the_placeholder_saved_is_one_both_clis_read_as_the_plans_default():
    """Evidence for the value chosen: both CLIs treat it as "my default"."""
    from iron_jarvis.daemon.routes.settings import PLAN_DEFAULT_MODEL
    from iron_jarvis.providers import claude_models, codex_models

    assert PLAN_DEFAULT_MODEL == "subscription"
    assert PLAN_DEFAULT_MODEL in claude_models.DEFAULT_IDS
    assert PLAN_DEFAULT_MODEL in codex_models.PLACEHOLDER_MODELS


def test_use_chatgpt_for_answers_saves_the_plans_default_not_gpt_4o_mini(tmp_path, monkeypatch):
    """MUTATION: the old ``d._PROMOTE_DEFAULT_MODEL[target]`` -> gpt-4o-mini -> red."""
    _clis(monkeypatch, codex=True)
    client = _client(tmp_path)
    assert client.app.state.d.platform.providers.inherited_from("openai") == "codex-cli"

    r = _use(client, "codex-cli")
    assert r.status_code == 200, r.text
    assert r.json()["promoted"] == {"provider": "openai", "model": "subscription"}
    assert _default(client) == ("openai", "subscription")


def test_use_claude_for_answers_saves_the_plans_default(tmp_path, monkeypatch):
    _clis(monkeypatch, claude=True)
    client = _client(tmp_path)
    r = _use(client, "claude-cli")
    assert r.status_code == 200, r.text
    assert r.json()["promoted"] == {"provider": "anthropic", "model": "subscription"}


def test_choosing_the_inherited_api_name_directly_also_gets_the_plans_default(tmp_path, monkeypatch):
    """``{provider: "openai"}`` served through Codex (the else branch).
    MUTATION: the table lookup there -> gpt-4o-mini -> red."""
    _clis(monkeypatch, codex=True)
    client = _client(tmp_path)
    r = _use(client, "openai")
    assert r.status_code == 200, r.text
    assert r.json()["promoted"] == {"provider": "openai", "model": "subscription"}


def test_a_real_key_keeps_the_tables_model(tmp_path, monkeypatch):
    """Anti-vacuity: a stored key is the raw API, so the API id stays."""
    _clis(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    client = _client(tmp_path)
    d = client.app.state.d
    assert d.platform.providers.inherited_from("anthropic") is None
    r = _use(client, "anthropic")
    assert r.status_code == 200, r.text
    assert r.json()["promoted"] == {"provider": "anthropic", "model": d._PROMOTE_DEFAULT_MODEL["anthropic"]}


def test_make_default_on_connections_for_an_inherited_name_gets_the_plans_default(tmp_path, monkeypatch):
    """``POST /connections/openai/default`` (Connections' Make default).
    MUTATION: the old table lookup -> gpt-4o-mini -> red."""
    _clis(monkeypatch, codex=True)
    client = _client(tmp_path)
    r = client.post("/connections/openai/default")
    assert r.status_code == 200, r.text
    assert r.json() == {"default_provider": "openai", "default_model": "subscription"}


def test_the_first_connect_promotion_never_pins_a_fixed_id_for_a_sign_in(tmp_path, monkeypatch):
    """``_maybe_autopromote_default`` for an API name served through the
    sign-in. MUTATION: drop the inherited check -> gpt-4o-mini -> red."""
    _clis(monkeypatch, codex=True)
    client = _client(tmp_path)
    d = client.app.state.d
    assert d._maybe_autopromote_default("openai") is True
    assert _default(client) == ("openai", "subscription")


def test_the_first_connect_promotion_with_a_key_is_unchanged(tmp_path):
    client = _client(tmp_path)
    r = client.post("/connections/anthropic/key", json={"key": "sk-ant-test"})
    assert r.json()["promoted_default"] is True
    assert _default(client) == ("anthropic", "claude-opus-4-8")


# --------------------------------------------------------------------------- #
# FIX 2 — test the key first; promote only after a pass
# --------------------------------------------------------------------------- #
def _prober(ok: bool, calls: list):
    def prober(provider: str, cred: str):
        calls.append((provider, cred))
        if ok:
            return (True, f"{provider} reachable — credential accepted")
        return (False, f"{provider} rejected the credential (401) — the key/token is invalid, revoked, or expired; reconnect it")

    return prober


def test_a_wrong_key_with_check_is_not_saved_and_changes_nothing(tmp_path):
    """MUTATION: store before the check -> the key is in the vault -> red.
    MUTATION: promote on a failed check -> the default leaves the demo -> red."""
    client = _client(tmp_path)
    conns = client.app.state.d.platform.connections
    calls: list = []
    conns._prober = _prober(False, calls)

    r = client.post("/connections/anthropic/key?check=1", json={"key": "sk-proj-wrong-tab"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["saved"] is False and body["promoted_default"] is False
    assert body["test"]["ok"] is False
    assert body["test"]["title"]  # the Test route's own plain words
    assert "sk-proj-wrong-tab" not in r.text  # the key never comes back
    assert calls == [("anthropic", "sk-proj-wrong-tab")]
    assert conns.credential("anthropic") in (None, "")
    assert _default(client)[0] == "mock"


def test_a_good_key_with_check_is_saved_but_not_promoted_by_the_route(tmp_path):
    """The door chooses it with the one press (use-model) — the route never
    does. MUTATION: keep the old autopromote in the check branch -> red."""
    client = _client(tmp_path)
    conns = client.app.state.d.platform.connections
    conns._prober = _prober(True, [])

    r = client.post("/connections/anthropic/key?check=1", json={"key": "sk-ant-good"})
    body = r.json()
    assert body["saved"] is True and body["promoted_default"] is False
    assert body["test"]["ok"] is True
    assert conns.credential("anthropic") == "sk-ant-good"
    assert _default(client)[0] == "mock"

    use = _use(client, "anthropic")
    assert use.json()["promoted"] == {"provider": "anthropic", "model": "claude-opus-4-8"}


def test_without_check_the_route_is_unchanged(tmp_path):
    """Headless callers and the Connections page: saved and promoted, no probe."""
    client = _client(tmp_path)
    conns = client.app.state.d.platform.connections
    calls: list = []
    conns._prober = _prober(False, calls)
    r = client.post("/connections/anthropic/key", json={"key": "sk-ant-x"})
    assert r.json()["promoted_default"] is True
    assert calls == []


def test_a_typed_custom_model_wins_over_the_first_listed_one(tmp_path, monkeypatch):
    """The key door saves the user's model for their own server; use-model
    then chooses THAT one while the server lists it. MUTATION: return
    ``live[0]`` first -> "other-model" -> red."""
    from iron_jarvis.providers import discovery

    monkeypatch.setattr(discovery, "discover_models", lambda *a, **k: ["other-model", "my-model"])
    client = _client(tmp_path)
    client.put(
        "/settings",
        json={"values": {"custom_base_url": "http://127.0.0.1:9/v1", "custom_model": "my-model"}},
    )
    monkeypatch.setattr(ProviderManager, "available", lambda self, n: n in ("custom", "mock"))
    r = _use(client, "custom")
    assert r.status_code == 200, r.text
    assert r.json()["promoted"] == {"provider": "custom", "model": "my-model"}


# --------------------------------------------------------------------------- #
# FIX 3 — the Sign in pane turns the door green by itself
# --------------------------------------------------------------------------- #
class _Session:
    def __init__(self, tail: str = "", alive: bool = True) -> None:
        self.tail = tail
        self.alive = alive
        self.output_seq = 1

    def output_tail(self) -> str:
        return self.tail

    def say(self, text: str) -> None:
        self.tail += text
        self.output_seq += 1


def _probe(signed_after: bool = True):
    runs: list = []

    def runner(argv):
        runs.append(argv)
        if argv[-2:] == ["auth", "status"]:
            return 0, '{"loggedIn": %s}' % ("true" if signed_after else "false"), ""
        return (0, "Logged in using ChatGPT", "") if signed_after else (1, "Not logged in", "")

    return CliAuthProbe(runner=runner, which=lambda b: f"C:/bin/{b}.exe"), runs


@pytest.mark.parametrize(
    "binary,line",
    [
        ("claude", "Login successful."),
        ("claude", "Logged in as me@example.com\nLogin successful. Press Enter to continue…"),
        ("codex", "✓ Signed in with your ChatGPT account"),
        ("codex", "Successfully logged in"),
    ],
)
def test_the_clis_own_success_words_re_check_the_sign_in_at_once(binary, line):
    """MUTATION: never refresh in the step -> the verdict stays unknown -> red."""
    probe, runs = _probe()
    s = _Session("starting…\n")
    state: dict = {}
    assert SW.default_login_step(s, binary, state, probe=probe, now=0.0) is False
    assert runs == []  # nothing on screen yet: no CLI is started
    s.say(line + "\n")
    assert SW.default_login_step(s, binary, state, probe=probe, now=1.0) is True
    assert len(runs) == 1
    assert probe.status(binary).signed_in is True


@pytest.mark.parametrize(
    "binary,line",
    [
        ("claude", "  ⎿  Login successful."),  # a tool result row
        ("claude", "> Login successful."),  # the user's own prompt
        ("claude", "echo Login successful.\nLogin successful."),
        ("codex", "Successfully logged in to MCP server 'github'."),
        ("codex", "Signed in with your ChatGPT account"),  # no tick: not the screen's line
        ("codex", "echo Successfully logged in\nSuccessfully logged in"),
        ("claude", "Successfully logged in"),  # another CLI's words
    ],
)
def test_lookalike_words_never_start_a_probe(binary, line):
    probe, runs = _probe()
    s = _Session(line + "\n")
    SW.default_login_step(s, binary, {}, probe=probe, now=0.0)
    assert runs == []


def test_rechecks_are_spaced_and_bounded():
    """Words on screen but the CLI still says signed out (the credentials file
    not written yet): re-checked at most every RECHECK_GAP_S, at most
    MAX_CHECKS times. MUTATION: drop the gap -> a probe every look -> red."""
    probe, runs = _probe(signed_after=False)
    s = _Session("Login successful.\n")
    state: dict = {}
    t = 0.0
    for _ in range(200):
        stop = SW.default_login_step(s, "claude", state, probe=probe, now=t)
        s.output_seq += 1
        t += 1.0
        if stop:
            break
    assert len(runs) == SW.MAX_CHECKS
    gaps = SW.RECHECK_GAP_S
    assert t >= gaps * (SW.MAX_CHECKS - 1)


def test_the_watch_ends_when_the_pane_closes_or_the_cli_is_signed_in():
    probe, runs = _probe()
    assert SW.default_login_step(_Session("x\n", alive=False), "claude", {}, probe=probe, now=0) is True
    probe._cache["claude"] = CliAuthStatus("claude", True, True, "signed in", 0.0)
    assert SW.default_login_step(_Session("Login successful.\n"), "claude", {}, probe=probe, now=0) is True
    assert runs == []


def test_no_watch_for_a_cli_with_no_known_words():
    assert SW.watch_default_login(_Session(), "grok", start=False) is None


# --- the wiring: the Sign in press's pane is the one watched ---------------
class _RecordingBackend(FakeBackend):
    instances: list = []

    def __init__(self) -> None:
        super().__init__()
        self.env: dict | None = None
        _RecordingBackend.instances.append(self)

    def start(self, argv, cwd, env, cols, rows) -> None:  # type: ignore[override]
        self.env = dict(env) if env is not None else None
        super().start(argv, cwd, env, cols, rows)


@pytest.fixture
def pane_client(tmp_path, monkeypatch):
    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    monkeypatch.setenv("IRONJARVIS_IRON_PROXY_BUNDLE", str(tmp_path / "no-bundle.mjs"))
    monkeypatch.delenv("IRONJARVIS_IRON_PROXY_NODE", raising=False)
    _RecordingBackend.instances = []
    monkeypatch.setattr("iron_jarvis.terminals.session.default_backend", _RecordingBackend)

    async def _no_watch(*_a, **_k):
        await asyncio.Event().wait()

    monkeypatch.setattr("iron_jarvis.iron_proxy.service.watch", _no_watch)
    proj = tmp_path / "proj"
    proj.mkdir()
    with TestClient(create_app(str(proj))) as c:
        yield c


def test_the_sign_in_press_pane_is_watched_on_this_pcs_login(pane_client, monkeypatch):
    """``POST /terminals/launch {cli, account: "default"}`` (the door's and
    Settings' Sign in) starts the watch; a plain launch does not.
    MUTATION: drop the call in launch_pane -> red."""
    seen: list = []
    monkeypatch.setattr(SW, "watch_default_login", lambda session, binary, **k: seen.append(binary))
    for cli in ("claude", "codex"):
        r = pane_client.post("/terminals/launch", json={"cli": cli, "account": "default"})
        assert r.status_code == 200, r.text
    assert seen == ["claude", "codex"]
    r = pane_client.post("/terminals/launch", json={"cli": "claude"})
    assert r.status_code == 200, r.text
    assert seen == ["claude", "codex"]


def test_the_watch_thread_re_checks_when_the_pane_says_signed_in(pane_client, monkeypatch):
    """End to end on a fake shell: the pane prints Claude Code's line, the
    watch thread re-checks the SHARED probe (a fake runner — never the real
    CLI), and its verdict reads signed in."""
    from iron_jarvis.providers import cli_auth

    probe, runs = _probe()
    monkeypatch.setattr(cli_auth, "DEFAULT_PROBE", probe)
    monkeypatch.setattr(SW, "WATCH_TICK_S", 0.05)
    r = pane_client.post("/terminals/launch", json={"cli": "claude", "account": "default"})
    assert r.status_code == 200, r.text
    session = pane_client.app.state.d.platform.terminals.get(r.json()["id"])
    session.write("Login successful.\n")  # the fake shell echoes the line
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and probe.status("claude") is None:
        time.sleep(0.05)
    assert runs, "the watch never re-checked"
    assert probe.status("claude").signed_in is True
    session.kill()
    for t in threading.enumerate():
        if t.name == "signin-watch-claude":
            t.join(timeout=2)
