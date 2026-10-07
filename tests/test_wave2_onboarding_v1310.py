"""v1.310.0 (wave 2, "the first five minutes") -- track A, onboarding backend.

Pins the four backend contracts the wizard, the Overview and the chat empty
state build on:

* W2-1 ``POST /onboarding/use-model {"provider": ...}`` -- the ONE explicit
  "use this for answers" press. It replaces ONLY the untouched ``mock`` (or
  empty) default, never a user's own choice; a subscription CLI is promoted to
  the INHERITED API name the existing "Make default" path uses (claude-cli ->
  anthropic, codex-cli -> openai) so the quality dial keeps working; ollama /
  custom take the first DISCOVERED model (not ``config.ollama_model``).
  Unknown or unavailable -> 409 with one plain sentence naming it.
* W2-2 ``GET /onboarding`` gains a ``model`` block (cheap: no network probe,
  no integrity check) and its environment checks drop the developer-toolchain
  rows (the ``ironjarvis doctor`` CLI keeps them); every remaining row carries
  ``level`` and a plain ``label``.
* W2-3 /health rows for claude-cli / codex-cli carry ``installed``,
  ``signed_in`` (null = unknown) and ``sign_in_fix`` (cli_auth.SIGN_IN_FIX).
* W2-4 the checklist tells the truth: "Teach it your style" counts only
  CONFIRMED non-reflection lessons (or any FeedbackRecord), its example phrase
  really arms ``remember_preference``, and step 1 never calls the offline mock
  "working" nor ticks "connected" while every answer still comes from mock.

Every test drives the REAL app factory (``create_app`` + ``TestClient``) on an
isolated tmp root. CLI presence / sign-in are scripted at the ProviderManager
seam the session conftest already stubs (``_cli_binary_present`` /
``_cli_signed_in``) -- no CLI is ever spawned, no network is touched.
"""

from __future__ import annotations

import importlib
import re

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers import discovery
from iron_jarvis.providers.cli_auth import SIGN_IN_FIX
from iron_jarvis.providers.manager import ProviderManager

#: The developer-toolchain rows the end-user /onboarding card must not show.
DEV_ROWS = {"python", "uv", "git", "node", "pnpm"}

#: An endpoint nothing listens on -- discovery is scripted, so it is never hit.
DEAD_LOCAL_URL = "http://127.0.0.1:9/v1"


# --- helpers -------------------------------------------------------------------


def _client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


def _clis(monkeypatch, *, claude: bool = False, codex: bool = False, signed_in=True):
    """Script which subscription CLIs are installed and their sign-in verdict
    (``True`` / ``False`` / ``None`` = unknown) -- the conftest forces both
    absent; this overrides for one test."""
    present = {"claude": claude, "codex": codex}
    monkeypatch.setattr(
        ProviderManager, "_cli_binary_present", staticmethod(lambda b: present.get(b, False))
    )
    monkeypatch.setattr(ProviderManager, "_cli_signed_in", staticmethod(lambda b: signed_in))


def _health(client) -> dict:
    return client.get("/health").json()


def _health_rows(client) -> dict[str, dict]:
    return {p["provider"]: p for p in _health(client)["providers"]}


def _default(client) -> tuple[str, str]:
    h = _health(client)
    return h["default_provider"], h["default_model"]


def _promote_model(client, provider: str) -> str:
    """The model the existing "Make default" path pairs with *provider* -- the
    one table use-model must agree with (read live, never hard-coded)."""
    d = client.app.state.d
    return d._PROMOTE_DEFAULT_MODEL.get(provider, d.platform.config.default_model)


def _use(client, provider: str):
    return client.post("/onboarding/use-model", json={"provider": provider})


def _step(client, key: str) -> dict:
    steps = client.get("/onboarding").json()["checklist"]
    return next(s for s in steps if s["key"] == key)


def _script_discovery(monkeypatch, models: list[str]) -> list[tuple]:
    """Every model-listing path for a local endpoint answers *models*; returns
    the call log (so a test can also assert NOTHING was probed)."""
    calls: list[tuple] = []

    def _fake(*a, **k):
        calls.append(a)
        return list(models)

    monkeypatch.setattr(discovery, "_ollama_models", _fake)
    monkeypatch.setattr(discovery, "_openai_compatible_models", _fake)
    discovery.clear_cache()
    return calls


def _configure_local(client, provider: str) -> None:
    key = "ollama_base_url" if provider == "ollama" else "custom_base_url"
    r = client.put("/settings", json={"values": {key: DEAD_LOCAL_URL}})
    assert r.status_code == 200, r.text
    # Precondition, not the behaviour under test: configured = available here.
    assert _health_rows(client)[provider]["available"] is True


def _add_lesson(client, **fields) -> None:
    from iron_jarvis.learning.models import LessonRecord

    with session_scope(client.app.state.platform.engine) as db:
        db.add(LessonRecord(text=fields.pop("text", "keep answers short"), **fields))
        db.commit()


# === W2-1: POST /onboarding/use-model ===========================================


def test_use_model_claude_cli_promotes_the_inherited_anthropic_default(tmp_path, monkeypatch):
    """A signed-in Claude Code user presses "use this for answers": the default
    leaves mock for ``anthropic`` (served through the CLI) + the Make-default
    model, so the quality dial keeps working."""
    _clis(monkeypatch, claude=True)
    client = _client(tmp_path)
    assert _default(client)[0] == "mock"  # the trap this press exists to clear

    r = _use(client, "claude-cli")
    assert r.status_code == 200, r.text
    want_model = _promote_model(client, "anthropic")
    assert r.json() == {"promoted": {"provider": "anthropic", "model": want_model}, "reason": ""}
    assert _default(client) == ("anthropic", want_model)


def test_use_model_codex_cli_promotes_the_inherited_openai_default(tmp_path, monkeypatch):
    _clis(monkeypatch, codex=True)
    client = _client(tmp_path)

    r = _use(client, "codex-cli")
    assert r.status_code == 200, r.text
    want_model = _promote_model(client, "openai")
    assert r.json()["promoted"] == {"provider": "openai", "model": want_model}
    assert _default(client) == ("openai", want_model)


def test_use_model_is_persisted_like_make_default(tmp_path, monkeypatch):
    """The press survives a restart: a fresh daemon on the same root still
    answers with the promoted default (config.toml, not just memory)."""
    _clis(monkeypatch, claude=True)
    r = _use(_client(tmp_path), "claude-cli")
    assert r.status_code == 200, r.text

    reborn = _client(tmp_path)
    assert _default(reborn) == ("anthropic", _promote_model(reborn, "anthropic"))


@pytest.mark.parametrize("provider", ["ollama", "custom"])
def test_use_model_local_endpoint_takes_the_first_discovered_model(tmp_path, monkeypatch, provider):
    """Ollama / custom are promoted with the FIRST model the endpoint lists --
    not ``config.ollama_model`` ('llama3.1', which the wizard never told the
    user to pull)."""
    _script_discovery(monkeypatch, ["llama3.2", "qwen3:8b"])
    client = _client(tmp_path)
    _configure_local(client, provider)
    assert _default(client)[0] == "mock"  # configuring alone never promotes

    r = _use(client, provider)
    assert r.status_code == 200, r.text
    assert r.json() == {"promoted": {"provider": provider, "model": "llama3.2"}, "reason": ""}
    assert _default(client) == (provider, "llama3.2")


def test_use_model_accepts_an_available_api_provider(tmp_path, monkeypatch):
    """Any available API provider is accepted (here ``anthropic`` inherited
    through the signed-in CLI -- no key, so nothing autopromoted it)."""
    _clis(monkeypatch, claude=True)
    client = _client(tmp_path)

    r = _use(client, "anthropic")
    assert r.status_code == 200, r.text
    assert r.json()["promoted"] == {
        "provider": "anthropic",
        "model": _promote_model(client, "anthropic"),
    }


def test_use_model_treats_an_empty_default_like_mock(tmp_path, monkeypatch):
    _clis(monkeypatch, claude=True)
    client = _client(tmp_path)
    client.app.state.platform.config.default_provider = ""

    r = _use(client, "claude-cli")
    assert r.status_code == 200, r.text
    assert r.json()["promoted"]["provider"] == "anthropic"


def test_use_model_never_replaces_a_users_own_choice(tmp_path, monkeypatch):
    """ANTI-VACUITY: the user already chose OpenAI. The press answers 200 with
    ``promoted: null`` and one sentence naming that choice -- and changes
    NOTHING (no silent provider switch: cloud-vs-local is the user's call)."""
    _clis(monkeypatch, claude=True)
    client = _client(tmp_path)
    assert client.post("/connections/openai/key", json={"key": "sk-openai-x"}).status_code == 200
    assert client.post("/connections/openai/default").status_code == 200
    before = _default(client)
    assert before[0] == "openai"

    r = _use(client, "claude-cli")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["promoted"] is None
    assert isinstance(body["reason"], str) and body["reason"].strip()
    assert "openai" in body["reason"].lower()
    assert _default(client) == before
    # ... and the reborn daemon agrees: nothing was persisted either.
    assert _default(_client(tmp_path)) == before


@pytest.mark.parametrize(
    ("provider", "setup", "named"),
    [
        ("no-such-model", "none", "no-such-model"),  # unknown
        ("claude-cli", "none", "claude"),  # not installed ("Claude Code" is fine)
        ("claude-cli", "signed_out", "claude"),  # installed, said it is signed out
        ("xai", "none", "xai"),  # API provider with no key ("xAI" is fine)
        ("ollama", "none", "ollama"),  # local endpoint never configured
    ],
)
def test_use_model_refuses_unknown_or_unavailable_with_409(
    tmp_path, monkeypatch, provider, setup, named
):
    """Unknown or unusable right now -> 409, one plain sentence that names the
    provider, and the default stays mock."""
    if setup == "signed_out":
        _clis(monkeypatch, claude=True, signed_in=False)
    client = _client(tmp_path)

    r = _use(client, provider)
    assert r.status_code == 409, r.text
    detail = r.json().get("detail")
    assert isinstance(detail, str) and detail.strip(), r.text
    assert named in detail.lower(), detail
    assert _default(client)[0] == "mock"


# === W2-2: GET /onboarding model block + end-user environment checks ==========


def test_onboarding_model_block_on_a_fresh_install(tmp_path):
    model = _client(tmp_path).get("/onboarding").json()["model"]
    assert model["default_provider"] == "mock"
    assert isinstance(model["default_model"], str)
    assert model["is_mock"] is True
    assert model["usable"] == []  # nothing real is connected


def test_onboarding_model_block_lists_usable_real_providers(tmp_path, monkeypatch):
    _clis(monkeypatch, claude=True)
    client = _client(tmp_path)

    model = client.get("/onboarding").json()["model"]
    assert model["is_mock"] is True
    usable = {u["provider"]: u for u in model["usable"]}
    assert "claude-cli" in usable
    assert "mock" not in usable
    assert "codex-cli" not in usable  # not installed -> not usable
    for row in model["usable"]:
        assert set(row) >= {"provider", "label"}
        assert isinstance(row["label"], str) and row["label"].strip()


def test_onboarding_model_block_follows_the_press(tmp_path, monkeypatch):
    _clis(monkeypatch, claude=True)
    client = _client(tmp_path)
    assert _use(client, "claude-cli").status_code == 200

    model = client.get("/onboarding").json()["model"]
    assert model["is_mock"] is False
    assert model["default_provider"] == "anthropic"
    assert model["default_model"] == _promote_model(client, "anthropic")


def test_onboarding_checks_drop_the_developer_toolchain_rows(tmp_path):
    """A packaged-app user is never told uv / git / Node / pnpm are missing (or
    handed an ``irm | iex`` to paste)."""
    checks = _client(tmp_path).get("/onboarding").json()["doctor"]["checks"]
    names = {c["name"] for c in checks}
    assert not (names & DEV_ROWS), names & DEV_ROWS
    assert checks, "the end-user rows themselves must survive"
    assert not any("irm " in (c.get("fix") or "") for c in checks)


def test_onboarding_check_rows_carry_level_and_a_plain_label(tmp_path):
    checks = _client(tmp_path).get("/onboarding").json()["doctor"]["checks"]
    for c in checks:
        assert c["level"] in ("required", "recommended"), c
        label = c.get("label")
        assert isinstance(label, str) and label.strip(), c
        # Plain words, not the mono identifier ("local_ocr", "guide_docs").
        assert "_" not in label, c


def test_doctor_cli_keeps_the_developer_toolchain_rows():
    """GUARD (passes today; must stay green): the gate lives in the /onboarding
    payload, never in ``doctor.CHECKS`` -- ``ironjarvis doctor`` still reports
    the toolchain for source installs."""
    doctor_mod = importlib.import_module("iron_jarvis.onboarding.doctor")
    names = {c["name"] for c in doctor_mod.doctor()["checks"]}
    assert DEV_ROWS <= names


def test_onboarding_runs_no_runtime_probe_and_no_discovery(tmp_path, monkeypatch):
    """GUARD (passes today; must stay green once the model block lands):
    /onboarding is fetched on every Overview load, so it never runs the
    runtime doctor (PRAGMA integrity_check, endpoint probes) and never lists a
    local endpoint's models -- the ``usable`` list reads cached availability."""
    doctor_mod = importlib.import_module("iron_jarvis.onboarding.doctor")
    hits: list[str] = []
    monkeypatch.setattr(doctor_mod, "runtime_checks", lambda *a, **k: hits.append("runtime") or [])
    monkeypatch.setattr(
        doctor_mod, "check_model_targets", lambda *a, **k: hits.append("targets") or {}
    )
    calls = _script_discovery(monkeypatch, ["llama3.2"])
    client = _client(tmp_path)
    _configure_local(client, "ollama")
    calls.clear()

    r = client.get("/onboarding")
    assert r.status_code == 200
    assert hits == []
    assert calls == []


# === W2-3: /health subscription-CLI rows ======================================


def test_health_cli_rows_carry_install_signin_and_fix(tmp_path, monkeypatch):
    _clis(monkeypatch, claude=True, signed_in=True)
    rows = _health_rows(_client(tmp_path))

    claude = rows["claude-cli"]
    assert claude["installed"] is True
    assert claude["signed_in"] is True
    assert claude["sign_in_fix"] == SIGN_IN_FIX["claude"]

    codex = rows["codex-cli"]
    assert codex["installed"] is False
    assert codex["signed_in"] is None
    assert codex["sign_in_fix"] == SIGN_IN_FIX["codex"]


def test_health_cli_row_signed_out_says_how_to_sign_in(tmp_path, monkeypatch):
    _clis(monkeypatch, claude=True, signed_in=False)
    claude = _health_rows(_client(tmp_path))["claude-cli"]
    assert claude["installed"] is True
    assert claude["signed_in"] is False
    assert claude["available"] is False
    assert claude["sign_in_fix"] == SIGN_IN_FIX["claude"]


def test_health_cli_row_unknown_signin_stays_null(tmp_path, monkeypatch):
    _clis(monkeypatch, claude=True, signed_in=None)
    claude = _health_rows(_client(tmp_path))["claude-cli"]
    assert claude["installed"] is True
    assert claude["signed_in"] is None  # unknown is not signed-out
    assert claude["sign_in_fix"] == SIGN_IN_FIX["claude"]


# === W2-4: the checklist tells the truth ======================================


def test_step1_on_a_fresh_install_calls_mock_a_scripted_demo(tmp_path):
    step = _step(_client(tmp_path), "connect_ai")
    assert step["done"] is False
    detail = step["detail"].lower()
    assert "works right now" not in detail
    assert "demo" in detail
    assert "works" not in step["title"].lower()


def test_step1_is_not_done_while_answers_still_come_from_mock(tmp_path, monkeypatch):
    """Claude Code is signed in but the default is still mock: every answer is
    the offline script, so step 1 must not tick (nor say "ready for full
    power") and stays the next step."""
    _clis(monkeypatch, claude=True)
    client = _client(tmp_path)
    onb = client.get("/onboarding").json()
    step = next(s for s in onb["checklist"] if s["key"] == "connect_ai")
    assert step["done"] is False
    assert "full power" not in step["detail"].lower()
    assert onb["next_step"] is not None and onb["next_step"]["key"] == "connect_ai"


def test_step1_ticks_once_the_user_presses_use_model(tmp_path, monkeypatch):
    """ANTI-VACUITY for the test above: after the explicit press step 1 is
    done and is no longer the next step."""
    _clis(monkeypatch, claude=True)
    client = _client(tmp_path)
    assert _use(client, "claude-cli").status_code == 200

    onb = client.get("/onboarding").json()
    assert next(s for s in onb["checklist"] if s["key"] == "connect_ai")["done"] is True
    assert onb["next_step"] is None or onb["next_step"]["key"] != "connect_ai"


@pytest.mark.parametrize(
    "lesson",
    [
        {"source": "reflection", "text": "Worked well for 'hello': Done."},
        {"source": "preference", "status": "proposed"},
        {"source": "preference", "status": "declined"},
    ],
    ids=["reflection", "proposed-preference", "declined-preference"],
)
def test_teach_style_ignores_reflections_and_unconfirmed_lessons(tmp_path, lesson):
    """Every finished (even mock) session writes a reflection lesson; a noticed
    preference waits for the user. Neither is the user teaching it."""
    client = _client(tmp_path)
    _add_lesson(client, **lesson)
    assert _step(client, "teach_style")["done"] is False


@pytest.mark.parametrize(
    "lesson",
    [
        {"source": "preference", "status": "confirmed"},
        {"source": "preference", "status": None},  # pre-v1.305 row = confirmed
        {"source": "feedback", "status": "confirmed"},
    ],
    ids=["confirmed-preference", "legacy-null-status", "feedback-lesson"],
)
def test_teach_style_counts_a_confirmed_user_lesson(tmp_path, lesson):
    """ANTI-VACUITY: a lesson the user actually taught still ticks the step."""
    client = _client(tmp_path)
    _add_lesson(client, **lesson)
    assert _step(client, "teach_style")["done"] is True


def test_teach_style_counts_any_session_feedback(tmp_path):
    """ANTI-VACUITY: a thumbs on a finished session is teaching it."""
    from iron_jarvis.learning.models import FeedbackRecord

    client = _client(tmp_path)
    with session_scope(client.app.state.platform.engine) as db:
        db.add(FeedbackRecord(session_id="sess_x", rating="up"))
        db.commit()
    assert _step(client, "teach_style")["done"] is True


def _quoted_example(text: str) -> str:
    m = re.search(r'"([^"]+)"', text) or re.search(r"“([^”]+)”", text)
    assert m, f"the step must quote an example phrase: {text!r}"
    return m.group(1)


def test_teach_style_example_phrase_really_arms_remember_preference(tmp_path):
    """The phrase the checklist tells the user to type must arm the preference
    tool ("remember: I like short answers" armed recall/ltm_* instead)."""
    from iron_jarvis.tools.autoselect import select_auto_tools

    step = _step(_client(tmp_path), "teach_style")
    example = _quoted_example(step["action"])
    assert "remember_preference" in select_auto_tools(example), example


@pytest.mark.parametrize(
    "ask",
    ["remember that my dentist is Dr Lee", "remember: my dentist is Dr Lee"],
)
def test_genuine_remember_asks_still_route_to_long_term_memory(ask):
    """GUARD (passes today; must stay green): if the preference vocabulary
    grows a "remember: ..." form, a fact to remember is still an LTM write,
    not a style preference."""
    from iron_jarvis.tools.autoselect import select_auto_tools

    tools = select_auto_tools(ask)
    assert "ltm_append" in tools
    assert "remember_preference" not in tools
