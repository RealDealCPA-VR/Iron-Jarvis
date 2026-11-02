"""v1.424.0 (wave 4, D2): Codex models are listed from CODEX'S OWN list.

The audit (gap D2): the ``codex`` CLI subscription offered ONE row, "Codex's
default", in every picker; a keyless ``openai`` served by codex-cli listed the
curated API ids (``gpt-4o``, ``gpt-4o-mini``, ``gpt-5.5``) a ChatGPT plan does
not run, so a pick of one failed inside Codex. Now ``providers.codex_models``
reads ``<CODEX_HOME>/models_cache.json`` (the file Codex writes; read off the
loop, cached) and:

* ``GET /models`` lists Codex's offered models for codex-cli after its
  "subscription" row (Codex's names, its order, the plan's default marked as
  ``cli_default``, each model's own levels incl. "Extra"); a keyless
  ``openai`` lists exactly those (no gpt-4o); nothing read = today's rows;
* a stored pick of a model Codex does not hold is REFUSED in words before
  anything starts (``CodexModelNotOffered``, ``no_failover``) — never sent,
  never moved to another model or provider;
* Build › Code's Codex list (``hosted/service``) offers the same models, each
  with its own effort levels.

NEVER THE USER'S ``~/.codex``: every test points ``CODEX_HOME`` at a temp
folder holding ``tests/fixtures/codex_models_cache_v14240.json`` — the SHAPE of
this PC's real file (codex-cli 0.157.0, read 2026-11-02): slugs and the model
fields only, no account data.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import connections as conn_routes
from iron_jarvis.providers import codex_models as CM
from iron_jarvis.providers import reasoning
from iron_jarvis.providers.adapters.codex_native import adapter as codex_adapter
from iron_jarvis.providers.manager import ProviderManager

FIXTURE = Path(__file__).parent / "fixtures" / "codex_models_cache_v14240.json"
#: What Codex offers in the fixture (visibility "list"), in its priority order.
OFFERED = ["gpt-6-astra", "gpt-6-sol", "gpt-6-luna", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"]
#: The rows Codex holds but does not offer.
HIDDEN = ["gpt-reserve", "codex-auto-review"]


@pytest.fixture(autouse=True)
def codex_home(tmp_path, monkeypatch):
    home = tmp_path / "codexhome"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    CM.reset()
    conn_routes.invalidate_model_catalog()
    yield home
    CM.reset()
    conn_routes.invalidate_model_catalog()


def _with_list(home: Path, config: str = "") -> dict:
    shutil.copyfile(FIXTURE, home / "models_cache.json")
    if config:
        (home / "config.toml").write_text(config, encoding="utf-8")
    data = CM.read_catalog()
    assert data is not None
    return data


@pytest.fixture
def codex_signed_in(monkeypatch):
    """codex is installed; openai has NO key -> it is served by codex-cli."""
    for var in ("OPENAI_API_KEY",):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(ProviderManager, "_cli_binary_present", staticmethod(lambda b: b == "codex"))


def _rows(models, provider):
    return [m for m in models if m["provider"] == provider]


# --------------------------------------------------------------------------- #
# the reader
# --------------------------------------------------------------------------- #


def test_the_reader_keeps_codexs_names_order_levels_and_the_plans_default(codex_home):
    _with_list(codex_home)
    assert [r["id"] for r in CM.offered()] == OFFERED
    astra = CM.lookup("gpt-6-astra")
    assert astra["label"] == "GPT-6-Astra" and astra["window"] == 258_400
    assert astra["efforts"] == ["low", "medium", "high", "xhigh", "max", "ultra"]
    # Iron Jarvis offers its own words only, in its order (no max / ultra).
    assert CM.effort_levels("gpt-6-astra") == ("low", "medium", "high", "xhigh")
    # No config.toml model: the first OFFERED row by priority starts.
    assert CM.default_model() == "gpt-6-astra"
    # A hidden row is KNOWN (Codex runs it by name) but not offered.
    assert CM.runs("gpt-reserve") is True and "gpt-reserve" not in [r["id"] for r in CM.offered()]
    assert CM.runs("gpt-4o") is False
    # A placeholder is Codex's own default: never refused, never "not known".
    assert CM.runs("subscription") is None
    assert CM.lookup("subscription")["id"] == "gpt-6-astra"


def test_codexs_own_config_names_the_default_and_a_profile_wins(codex_home):
    _with_list(codex_home, 'model = "gpt-5.6-terra"\n')
    assert CM.default_model() == "gpt-5.6-terra"
    (codex_home / "config.toml").write_text(
        'model = "gpt-5.6-terra"\nprofile = "fast"\n[profiles.fast]\nmodel = "gpt-6-luna"\n',
        encoding="utf-8",
    )
    CM.read_catalog()
    assert CM.default_model() == "gpt-6-luna"
    # A configured model Codex's list does not hold: the default is not known
    # (never a guess).
    (codex_home / "config.toml").write_text('model = "gpt-9-imaginary"\n', encoding="utf-8")
    CM.read_catalog()
    assert CM.default_model() == ""


def test_no_list_means_nothing_is_known(codex_home):
    assert CM.read_catalog() is None
    assert CM.offered() == [] and CM.runs("gpt-4o") is None and CM.effort_levels("gpt-6-sol") is None
    # The reasoning table and the adapter fall back to before.
    assert reasoning.reasoning_levels("codex-cli", "gpt-6-sol") == reasoning.LEVELS
    assert codex_adapter.not_offered_error("gpt-4o") is None


def test_the_reasoning_table_reads_each_models_levels(codex_home):
    _with_list(codex_home)
    assert reasoning.reasoning_levels("codex-cli", "gpt-6-luna") == ("low", "medium", "high", "xhigh")
    assert reasoning.supports("codex-cli", "gpt-6-astra", "xhigh")
    # The adapter puts "Extra" on the wire (it used to drop it).
    assert codex_adapter._effort_on_the_wire("xhigh") == "xhigh"
    assert codex_adapter._effort_on_the_wire("max") == ""


def test_the_window_rung_reads_the_same_one_reading(codex_home, monkeypatch):
    """ONE reader: ``chat_turn.refresh_codex_windows`` parses through
    ``codex_models`` and its reading refreshes the menus' too."""
    from iron_jarvis.daemon import chat_turn

    monkeypatch.setattr(chat_turn, "_CODEX_CATALOG", {"at": 0.0, "rows": None, "busy": False})
    shutil.copyfile(FIXTURE, codex_home / "models_cache.json")
    rows = chat_turn.refresh_codex_windows()
    assert rows["gpt-6-sol"] == 258_400 and "\x00hidden:gpt-reserve" in rows
    assert [r["id"] for r in CM.offered()] == OFFERED, "the window read did not refresh the list"


# --------------------------------------------------------------------------- #
# GET /models
# --------------------------------------------------------------------------- #


def test_models_list_codexs_own_models_with_the_plans_default_marked(tmp_path, codex_home, codex_signed_in):
    _with_list(codex_home)
    models = TestClient(create_app(str(tmp_path / "home"))).get("/models").json()["models"]
    codex = _rows(models, "codex-cli")
    ids = [m["model"] for m in codex]
    # The "subscription" row (Codex's own default) stays first, then Codex's list.
    assert ids == ["subscription"] + OFFERED
    astra = codex[1]
    assert astra["label"] == "GPT-6-Astra" and astra["cli_default"] is True
    assert [m.get("cli_default") for m in codex[2:]] == [False] * (len(OFFERED) - 1)
    assert astra["reasoning"] == ["low", "medium", "high", "xhigh"]
    assert all(m["available"] for m in codex)
    assert not set(HIDDEN) & set(ids)


def test_a_keyless_openai_served_by_codex_offers_only_what_codex_runs(tmp_path, codex_home, codex_signed_in):
    _with_list(codex_home)
    models = TestClient(create_app(str(tmp_path / "home"))).get("/models").json()["models"]
    openai = _rows(models, "openai")
    assert openai and all(m["inherited_from"] == "codex-cli" for m in openai)
    assert [m["model"] for m in openai] == OFFERED
    for curated in ("gpt-4o", "gpt-4o-mini", "gpt-5.5"):
        assert curated not in [m["model"] for m in models if m["provider"] in ("openai", "codex-cli")]


def test_no_list_keeps_todays_rows_exactly(tmp_path, codex_home, codex_signed_in):
    models = TestClient(create_app(str(tmp_path / "home"))).get("/models").json()["models"]
    assert [m["model"] for m in _rows(models, "codex-cli")] == ["subscription"]
    assert [m["model"] for m in _rows(models, "openai")] == ["gpt-4o", "gpt-4o-mini", "gpt-5.5"]


def test_an_api_key_keeps_the_pay_per_use_rows(tmp_path, codex_home, codex_signed_in, monkeypatch):
    """With a real OpenAI key the `openai` rows are the API's — never Codex's
    (the key is the user's pay-per-use choice; nothing folds it away)."""
    from iron_jarvis.providers import discovery

    _with_list(codex_home)
    app = create_app(str(tmp_path / "home"))
    mgr = app.state.platform.providers
    real = mgr._present
    monkeypatch.setattr(mgr, "_present", lambda name: True if name == "openai" else real(name))
    monkeypatch.setattr(discovery, "discover_models", lambda *a, **k: [])
    assert mgr.inherited_from("openai") is None
    models = TestClient(app).get("/models").json()["models"]
    assert [m["model"] for m in _rows(models, "openai")] == ["gpt-4o", "gpt-4o-mini", "gpt-5.5"]
    # codex-cli itself still lists Codex's own models.
    assert [m["model"] for m in _rows(models, "codex-cli")] == ["subscription"] + OFFERED


# --------------------------------------------------------------------------- #
# a model Codex does not hold is refused, never moved
# --------------------------------------------------------------------------- #


def test_a_stored_pick_codex_cannot_run_is_refused_in_words_and_never_moved(codex_home):
    _with_list(codex_home)
    err = codex_adapter.not_offered_error("gpt-4o")
    assert err is not None and err.no_failover is True and err.transient is False
    text = str(err)
    assert "gpt-4o" in text and "GPT-6-Astra" in text and "nothing was sent" in text
    assert codex_adapter.not_offered_error("gpt-6-sol") is None
    assert codex_adapter.not_offered_error("subscription") is None
    assert codex_adapter.not_offered_error("gpt-reserve") is None  # hidden, but Codex runs it


async def test_the_router_refuses_it_without_trying_any_other_provider(tmp_path, codex_home, codex_signed_in):
    _with_list(codex_home)
    app = create_app(str(tmp_path / "home"))
    platform = app.state.platform
    tried: list[str] = []

    from iron_jarvis.providers.adapters.base import LLMAdapter, LLMResponse

    class Spare(LLMAdapter):
        provider = "anthropic"
        model = "x"

        async def complete(self, **kw):
            tried.append("anthropic")
            return LLMResponse(text="from a provider nobody picked")

    platform.providers.register("anthropic", lambda model=None: Spare())
    from iron_jarvis.providers.adapters.base import LLMMessage

    with pytest.raises(Exception) as caught:
        await platform.router.complete(
            system="s", messages=[LLMMessage(role="user", content="hi")], tools=[],
            provider="openai", model="gpt-4o",
        )
    assert getattr(caught.value, "no_failover", False) is True
    assert "Codex doesn't offer gpt-4o" in str(caught.value)
    assert tried == [], "the refused pick was moved to another provider"


# --------------------------------------------------------------------------- #
# Build › Code's Codex list
# --------------------------------------------------------------------------- #


def test_build_code_offers_codexs_models_each_with_its_own_levels(tmp_path, codex_home):
    from iron_jarvis.hosted import service as HS

    _with_list(codex_home)
    app = create_app(str(tmp_path / "home"))
    svc = HS.service_for(app.state.platform)
    codex = next(h for h in svc.harnesses() if h["id"] == HS.CODEX)
    ids = [m["id"] for m in codex["models"]]
    assert ids == [""] + OFFERED
    luna = next(m for m in codex["models"] if m["id"] == "gpt-6-luna")
    assert luna["label"] == "GPT-6-Luna" and luna["efforts"] == ["low", "medium", "high", "xhigh"]
    # The default row carries the default model's levels.
    assert codex["models"][0]["efforts"] == ["low", "medium", "high", "xhigh"]
    # Each model's own levels decide what a session may set.
    assert HS.check_effort(HS.CODEX, "xhigh", "gpt-6-luna") == "xhigh"
    with pytest.raises(HS.Refused):
        HS.check_effort(HS.CODEX, "minimal", "gpt-6-luna")


def test_build_code_without_a_list_is_unchanged(tmp_path, codex_home):
    from iron_jarvis.hosted import service as HS

    app = create_app(str(tmp_path / "home"))
    codex = next(h for h in HS.service_for(app.state.platform).harnesses() if h["id"] == HS.CODEX)
    assert [m["id"] for m in codex["models"]] == [""]
    assert "efforts" not in codex["models"][0]
    assert HS.check_effort(HS.CODEX, "minimal", "gpt-6-luna") == "minimal"


def test_the_fixture_carries_no_account_data():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert set(data) == {"models"}
    text = FIXTURE.read_text(encoding="utf-8").lower()
    for word in ("identity", "etag", "email", "account", "token"):
        assert word not in text


def test_a_reading_of_another_codex_home_is_never_answered(codex_home, tmp_path, monkeypatch):
    """The reading is keyed by the home it is OF: after ``CODEX_HOME`` moves
    (a test's own folder, an account's home), the old list is not this one's."""
    _with_list(codex_home)
    assert CM.offered()
    other = tmp_path / "other-home"
    other.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(other))
    assert CM.catalog() is None and CM.runs("gpt-4o") is None


def test_with_codex_home_unset_the_suite_never_reads_the_real_one(monkeypatch):
    """Several suites DELETE ``CODEX_HOME`` (Iron-Proxy isolation); the reader
    then falls back to the suite's empty folder (tests/conftest.py), never
    the developer's ``~/.codex``."""
    from pathlib import Path as _P

    monkeypatch.delenv("CODEX_HOME", raising=False)
    assert CM.TEST_HOME_FALLBACK is not None
    assert CM.codex_home() == _P(CM.TEST_HOME_FALLBACK)
    assert CM.codex_home() != _P.home() / ".codex"
