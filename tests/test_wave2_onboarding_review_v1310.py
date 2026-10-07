"""v1.310.0 (wave 2, track A) -- the review round's three honesty fixes.

1. The "your Claude Code sign-in" door must never bill a pay-per-use key. With
   ANTHROPIC_API_KEY (or a stored OpenAI key) present, ``available('anthropic')``
   is true but the API name is NOT served through the CLI -- a stored key always
   wins -- so the press must promote the CLI itself, not the API name.
2. Step 1 must not promise "one press" when the press has nothing to choose
   (only a Grok sign-in connected), and the press must not say "I don't know a
   model called grok-cli" about a model that is registered.
3. Step 1 says "scripted demo" ONLY while the default is the mock: a chosen
   model that is unreachable is named, because the router refuses (v1.162.0)
   and no mock answers for it.

Real app factory + TestClient on a tmp root; CLI presence / sign-in scripted
at the ProviderManager seam; nothing spawned, no network.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.manager import ProviderManager


def _client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


def _clis(monkeypatch, *, claude: bool = False, codex: bool = False) -> None:
    present = {"claude": claude, "codex": codex}
    monkeypatch.setattr(
        ProviderManager, "_cli_binary_present", staticmethod(lambda b: present.get(b, False))
    )
    monkeypatch.setattr(ProviderManager, "_cli_signed_in", staticmethod(lambda b: True))


def _use(client, provider: str):
    return client.post("/onboarding/use-model", json={"provider": provider})


def _default(client) -> tuple[str, str]:
    h = client.get("/health").json()
    return h["default_provider"], h["default_model"]


def _step1(client) -> dict:
    return next(s for s in client.get("/onboarding").json()["checklist"] if s["key"] == "connect_ai")


# --- 1. the subscription door never bills a raw key ----------------------------


def test_claude_door_with_an_anthropic_key_promotes_the_cli_not_the_key(tmp_path, monkeypatch):
    _clis(monkeypatch, claude=True)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    client = _client(tmp_path)
    pm = client.app.state.d.platform.providers
    # Precondition: the key makes anthropic available ON ITS OWN (not inherited).
    assert pm.available("anthropic") is True
    assert pm.inherited_from("anthropic") is None

    r = _use(client, "claude-cli")
    assert r.status_code == 200, r.text
    assert r.json()["promoted"] == {"provider": "claude-cli", "model": "subscription"}
    assert _default(client) == ("claude-cli", "subscription")


def test_claude_door_without_a_key_still_promotes_the_inherited_name(tmp_path, monkeypatch):
    """Anti-vacuity for the test above: no key -> anthropic IS served through
    the sign-in, so the inherited API name (quality dial) is still chosen."""
    _clis(monkeypatch, claude=True)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = _client(tmp_path)
    assert client.app.state.d.platform.providers.inherited_from("anthropic") == "claude-cli"

    r = _use(client, "claude-cli")
    assert r.status_code == 200, r.text
    assert r.json()["promoted"]["provider"] == "anthropic"


def test_codex_door_with_an_openai_key_promotes_the_cli_not_the_key(tmp_path, monkeypatch):
    _clis(monkeypatch, codex=True)
    real_present = ProviderManager._present

    def _present(self, name):  # a stored OpenAI key (vault), scripted
        return True if name == "openai" else real_present(self, name)

    monkeypatch.setattr(ProviderManager, "_present", _present)
    client = _client(tmp_path)
    assert client.app.state.d.platform.providers.inherited_from("openai") is None

    r = _use(client, "codex-cli")
    assert r.status_code == 200, r.text
    assert r.json()["promoted"] == {"provider": "codex-cli", "model": "subscription"}


# --- 2. no "one press" promise the press can't keep -----------------------------


def _grok_only(monkeypatch) -> None:
    _clis(monkeypatch)
    monkeypatch.setattr(ProviderManager, "_grok_cli_available", lambda self: True)


def test_step1_with_only_an_unpressable_model_points_at_connections(tmp_path, monkeypatch):
    _grok_only(monkeypatch)
    client = _client(tmp_path)
    body = client.get("/onboarding").json()
    rows = {p["provider"]: p for p in client.get("/health").json()["providers"]}
    assert rows["grok-cli"]["available"] is True  # precondition: connected
    assert body["model"]["usable"] == []

    step = _step1(client)
    assert step["done"] is False
    assert "one press" not in step["detail"].lower()
    assert step["action"] != "Choose it for answers"
    assert "connections" in (step["detail"] + step["action"]).lower()


def test_pressing_a_registered_but_unsupported_model_says_so(tmp_path, monkeypatch):
    _grok_only(monkeypatch)
    client = _client(tmp_path)
    r = _use(client, "grok-cli")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "don't know" not in detail.lower()
    assert "connections" in detail.lower()
    assert _default(client)[0] == "mock"


def test_step1_still_offers_the_press_when_one_is_usable(tmp_path, monkeypatch):
    """Anti-vacuity: with a signed-in Claude Code the one-press copy stays."""
    _clis(monkeypatch, claude=True)
    client = _client(tmp_path)
    step = _step1(client)
    assert step["action"] == "Choose it for answers"
    assert "one press" in step["detail"].lower()


# --- 3. "scripted demo" only while the default is the mock ----------------------


def test_step1_names_a_chosen_but_unreachable_model(tmp_path, monkeypatch):
    _clis(monkeypatch)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    client = _client(tmp_path)
    cfg = client.app.state.d.platform.config
    cfg.default_provider = "xai"  # the user's own choice, key since removed
    cfg.default_model = "grok-4"

    step = _step1(client)
    assert step["done"] is False
    assert "demo" not in step["detail"].lower()
    assert "reachable" in step["detail"].lower()
    assert "connections" in step["detail"].lower()


def test_step1_unreachable_choice_is_not_done_even_if_something_else_is_up(tmp_path, monkeypatch):
    """A signed-in Claude Code does not make a dead ``xai`` choice "answering"."""
    _clis(monkeypatch, claude=True)
    client = _client(tmp_path)
    cfg = client.app.state.d.platform.config
    cfg.default_provider = "xai"
    cfg.default_model = "grok-4"

    step = _step1(client)
    assert step["done"] is False
    assert "answers you now" not in step["detail"]


# --- 4. Auto is a real choice, not an unreachable one ---------------------------


def test_step1_auto_with_a_signed_in_cli_is_done(tmp_path, monkeypatch):
    """``available('auto')`` is always False (auto is resolved, never a
    factory), so asking it stranded Auto users on a false "isn't reachable"
    while Auto answered through Claude Code."""
    _clis(monkeypatch, claude=True)
    client = _client(tmp_path)
    cfg = client.app.state.d.platform.config
    cfg.default_provider = "auto"

    step = _step1(client)
    assert step["done"] is True
    assert "reachable" not in step["detail"].lower()
    assert "answers you now" in step["detail"]
    nxt = client.get("/onboarding").json()["next_step"]
    assert nxt is None or nxt["key"] != "connect_ai"


def test_step1_auto_with_nothing_connected_is_a_demo_not_an_answer(tmp_path, monkeypatch):
    """Anti-vacuity: Auto with nothing real connected falls back to the
    offline mock, so it must neither tick nor claim an answer."""
    _clis(monkeypatch)
    client = _client(tmp_path)
    cfg = client.app.state.d.platform.config
    cfg.default_provider = "auto"

    step = _step1(client)
    assert step["done"] is False
    assert "answers you now" not in step["detail"]
    assert "reachable" not in step["detail"].lower()
    assert "demo" in step["detail"].lower()


def test_step1_nothing_connected_never_claims_a_sign_in_exists(tmp_path, monkeypatch):
    """That branch only runs when NO sign-in was found -- so it must not say
    one is "already on this PC"."""
    _clis(monkeypatch)
    client = _client(tmp_path)

    step = _step1(client)
    assert step["done"] is False
    assert "already on this pc" not in step["detail"].lower()
    assert "sign in" in step["detail"].lower()


def test_an_api_name_served_through_a_sign_in_is_named_for_the_sign_in():
    """Review (v1.310.0): the Claude door promotes to the INHERITED name
    (anthropic), and step 1 then read "Anthropic (Claude) answers you now" —
    true, but the user pressed their Claude Code sign-in, and the words now
    sound like a pay-per-use key. A keyless API name says the sign-in; a
    stored key (inherited_from None) keeps its own name."""
    from types import SimpleNamespace

    from iron_jarvis.onboarding.checklist import provider_label

    def plat(via):
        return SimpleNamespace(
            providers=SimpleNamespace(inherited_from=lambda name: via),
            connections=SimpleNamespace(
                get_spec=lambda name: SimpleNamespace(display_name="Anthropic (Claude)")
            ),
        )

    assert provider_label(plat("claude-cli"), "anthropic") == "Claude (your Claude Code sign-in)"
    assert provider_label(plat("codex-cli"), "openai") == "ChatGPT (your Codex sign-in)"
    assert provider_label(plat(None), "anthropic") == "Anthropic (Claude)"
