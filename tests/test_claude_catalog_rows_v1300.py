"""v1.300.0 review — a keyless (inherited) ``anthropic`` keeps its curated rows.

``GET /models`` lists the Claude subscription's LIVE picker for ``claude-cli``
and, because a keyless ``anthropic`` is SERVED by the claude CLI
(``manager.inherited_from``), for ``anthropic`` too. The first cut REPLACED
every curated ``anthropic`` row with the catalog, so ``claude-sonnet-4-6``,
``claude-fable-5`` and (when the live picker did not name it) ``claude-opus-4-8``
vanished from the picker list — and ``templates.analyze_requirements`` checks
a template's pinned model against EXACTLY that list
(``routes/connections.selectable_models``), so a template pinned to
``anthropic · claude-sonnet-4-6`` warned "isn't connected right now" while
that id runs live through the claude-cli adapter.

The rule pinned here: the catalog rows come FIRST (live picker order, their
label / 1M / credits fields), then every curated row the catalog does not
already cover, deduplicated by CANONICAL id (``[1m]`` variants and the
picker's aliases never double up). Driven through the REAL app factory.

Offline: the handshake chokepoint ``claude_models._run_handshake`` is a fake.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents import dynamic
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers import claude_models as cm
from iron_jarvis.providers.manager import ProviderManager

ACCOUNT = {"email": "user@example.test", "subscriptionType": "Claude Max"}

#: The account's picker (claude 2.1.288 shape).
LIVE_ROWS = [
    {"value": "default", "resolvedModel": "claude-opus-5-5", "displayName": "Default (recommended)",
     "description": "Opus 5.5 · Best for everyday, complex tasks"},
    {"value": "opus", "resolvedModel": "claude-opus-5-5", "displayName": "Opus",
     "description": "Opus 5.5 · Best for everyday, complex tasks"},
    {"value": "claude-fable-5-1[1m]", "resolvedModel": "claude-fable-5-1", "displayName": "Fable",
     "description": "Fable 5.1 · Most capable for your hardest and longest-running tasks"},
    {"value": "sonnet", "resolvedModel": "claude-sonnet-5-5", "displayName": "Sonnet",
     "description": "Sonnet 5.5 · Efficient for routine tasks"},
    {"value": "haiku", "resolvedModel": "claude-haiku-4-5-20251001", "displayName": "Haiku",
     "description": "Haiku 4.5 · Fastest for quick answers"},
]

#: This PC's account also offers Opus 4.8 (a curated id) in its picker.
WITH_OPUS_48 = LIVE_ROWS + [
    {"value": "claude-opus-4-8[1m]", "resolvedModel": "claude-opus-4-8", "displayName": "Opus 4.8",
     "description": "Opus 4.8 · Previous generation"},
]

CATALOG_IDS = ["claude-opus-5-5", "claude-fable-5-1", "claude-sonnet-5-5", "claude-haiku-4-5-20251001"]


def _stdout(models) -> str:
    return json.dumps({
        "type": "control_response",
        "response": {"subtype": "success", "request_id": "ij-picker",
                     "response": {"models": models, "account": ACCOUNT, "commands": []}},
    }) + "\n"


@pytest.fixture(autouse=True)
def _clean_module():
    cm.reset()
    cm.set_home(None)
    yield
    deadline = time.monotonic() + 5
    while cm._refreshing and time.monotonic() < deadline:
        time.sleep(0.01)
    cm.reset()
    cm.set_home(None)


@pytest.fixture
def inherited(monkeypatch):
    """claude is installed; anthropic has NO key -> it inherits claude-cli."""
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(ProviderManager, "_cli_binary_present", staticmethod(lambda b: b == "claude"))


def _prime(monkeypatch, rows) -> None:
    monkeypatch.setattr(cm, "_run_handshake", lambda timeout: _stdout(rows))
    assert cm.discover(force=True)["error"] is None


def _rows(models, provider):
    return [m for m in models if m["provider"] == provider]


def _client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


def _canon(model: str) -> str:
    base = model.lower()
    base = base[:-4] if base.endswith("[1m]") else base
    return cm.PINNED_ALIASES.get(base, base)


def test_inherited_anthropic_keeps_curated_rows_after_the_catalog(tmp_path, inherited, monkeypatch):
    _prime(monkeypatch, LIVE_ROWS)
    models = _client(tmp_path).get("/models").json()["models"]
    anth = _rows(models, "anthropic")
    assert anth, "the inherited anthropic lists nothing"
    assert all(m["inherited_from"] == "claude-cli" for m in anth)
    ids = [m["model"] for m in anth]
    # Catalog first, in the live picker's order, carrying its fields.
    assert ids[: len(CATALOG_IDS)] == CATALOG_IDS
    sonnet55 = anth[CATALOG_IDS.index("claude-sonnet-5-5")]
    assert sonnet55["label"] == "Sonnet 5.5" and sonnet55["context_window"] == 1_000_000
    # Then the curated ids the catalog does not cover — in curated order.
    assert ids[len(CATALOG_IDS):] == ["claude-opus-4-8", "claude-sonnet-4-6", "claude-fable-5"]
    # claude-haiku-4-5 IS covered (alias of the picker's dated Haiku 4.5).
    assert "claude-haiku-4-5" not in ids
    # Never one canonical model twice.
    canon = [_canon(i) for i in ids]
    assert len(canon) == len(set(canon))
    # Every row is selectable (the honesty flag the pickers read).
    assert all(m["available"] for m in anth)


def test_a_curated_id_the_live_picker_names_appears_once_as_the_catalog_row(
    tmp_path, inherited, monkeypatch
):
    _prime(monkeypatch, WITH_OPUS_48)
    models = _client(tmp_path).get("/models").json()["models"]
    anth = _rows(models, "anthropic")
    ids = [m["model"] for m in anth]
    assert ids.count("claude-opus-4-8") == 1
    opus48 = next(m for m in anth if m["model"] == "claude-opus-4-8")
    assert opus48["label"] == "Opus 4.8" and opus48["native"] == "claude-opus-4-8[1m]"
    assert ids.index("claude-opus-4-8") < ids.index("claude-sonnet-4-6")  # catalog block first
    assert ids[-2:] == ["claude-sonnet-4-6", "claude-fable-5"]


def test_1m_variants_and_aliases_in_the_curated_list_never_double_up(
    tmp_path, inherited, monkeypatch
):
    extra = [
        {"provider": "anthropic", "model": "claude-sonnet-5-5[1m]"},
        {"provider": "anthropic", "model": "Claude-Opus-5-5"},
        {"provider": "anthropic", "model": "haiku"},
        {"provider": "claude-cli", "model": "claude-fable-5-1[1m]"},
        {"provider": "claude-cli", "model": "claude-sonnet-4-6"},
    ]
    monkeypatch.setattr(dynamic, "KNOWN_MODELS", list(dynamic.KNOWN_MODELS) + extra)
    _prime(monkeypatch, LIVE_ROWS)
    models = _client(tmp_path).get("/models").json()["models"]
    for prov in ("anthropic", "claude-cli"):
        ids = [m["model"] for m in _rows(models, prov) if m["model"] != "subscription"]
        canon = [_canon(i) for i in ids]
        assert len(canon) == len(set(canon)), (prov, ids)
    anth_ids = [m["model"] for m in _rows(models, "anthropic")]
    assert "claude-sonnet-5-5[1m]" not in anth_ids and "haiku" not in anth_ids
    assert "Claude-Opus-5-5" not in anth_ids
    # claude-cli gets the same treatment: subscription, catalog, then uncovered curated.
    cli = [m["model"] for m in _rows(models, "claude-cli")]
    assert cli[0] == "subscription"
    assert cli[1 : 1 + len(CATALOG_IDS)] == CATALOG_IDS
    assert cli[1 + len(CATALOG_IDS):] == ["claude-sonnet-4-6"]


def test_claude_cli_rows_unchanged_by_the_merge(tmp_path, inherited, monkeypatch):
    """Before v1.300.0 claude-cli's only curated row was "subscription"; it
    stays first and the live rows follow it, nothing else."""
    _prime(monkeypatch, LIVE_ROWS)
    models = _client(tmp_path).get("/models").json()["models"]
    cli = [m["model"] for m in _rows(models, "claude-cli")]
    assert cli == ["subscription"] + CATALOG_IDS


def test_template_pinned_to_curated_sonnet_is_ready_not_disconnected(
    tmp_path, inherited, monkeypatch
):
    _prime(monkeypatch, LIVE_ROWS)
    client = _client(tmp_path)
    for model in ("claude-sonnet-4-6", "claude-opus-5-5", "claude-haiku-4-5"):
        r = client.post("/templates", json={
            "name": f"pinned {model}", "task": "Summarise the inbox",
            "provider": "anthropic", "model": model,
        })
        assert r.status_code == 200, r.text
    rows = {t["model"]: t for t in client.get("/templates").json()["templates"]}
    for model in ("claude-sonnet-4-6", "claude-opus-5-5", "claude-haiku-4-5"):
        req = next(x for x in rows[model]["requirements"] if x["key"] == "model")
        assert req["ok"] is True, req["detail"]
        assert "isn't connected" not in req["detail"]


def test_analyze_requirements_directly_against_the_routes_list(tmp_path, inherited, monkeypatch):
    """The pure checker, fed the route's own list (``selectable_models``)."""
    from iron_jarvis.templates import analyze_requirements

    _prime(monkeypatch, LIVE_ROWS)
    models = _client(tmp_path).get("/models").json()["models"]
    reqs = analyze_requirements(
        "Summarise the inbox", "anthropic", "claude-sonnet-4-6", None,
        selectable_models=models, live_tools=[], has_secret=lambda k: None,
        comm_config={}, agent_names=[],
    )
    model_req = next(x for x in reqs if x["key"] == "model")
    assert model_req["ok"] is True, model_req["detail"]
    # Anti-vacuity: an id nobody offers still warns.
    reqs = analyze_requirements(
        "Summarise the inbox", "anthropic", "claude-nonexistent-9", None,
        selectable_models=models, live_tools=[], has_secret=lambda k: None,
        comm_config={}, agent_names=[],
    )
    assert next(x for x in reqs if x["key"] == "model")["ok"] is False


def test_the_live_pickers_alias_covers_a_curated_alias_row(tmp_path, inherited, monkeypatch):
    """The picker decides what "sonnet" means: when it resolves to a model the
    pinned alias table does not know, a curated "sonnet" row is STILL the
    picker's row (covered by its value), never a second Sonnet."""
    rows = [dict(r) for r in LIVE_ROWS]
    rows[3] = {"value": "sonnet", "resolvedModel": "claude-sonnet-6", "displayName": "Sonnet",
               "description": "Sonnet 6 · Efficient for routine tasks"}
    monkeypatch.setattr(
        dynamic, "KNOWN_MODELS",
        list(dynamic.KNOWN_MODELS) + [{"provider": "anthropic", "model": "sonnet"}],
    )
    _prime(monkeypatch, rows)
    models = _client(tmp_path).get("/models").json()["models"]
    ids = [m["model"] for m in _rows(models, "anthropic")]
    assert "claude-sonnet-6" in ids
    assert "sonnet" not in ids
    assert "claude-sonnet-4-6" in ids  # anti-vacuity: uncovered curated ids stay


def test_a_folded_curated_id_rides_its_covering_row_as_an_alias(tmp_path, inherited, monkeypatch):
    """Second review pass: ``claude-haiku-4-5`` is folded into the dated catalog
    row (no duplicate in the picker) but must still answer for a pinned
    template — it rides that row's ``aliases``, once."""
    _prime(monkeypatch, LIVE_ROWS)
    models = _client(tmp_path).get("/models").json()["models"]
    anth = [m for m in models if m.get("provider") == "anthropic"]
    dated = next(m for m in anth if m.get("model") == "claude-haiku-4-5-20251001")
    assert dated.get("aliases") == ["claude-haiku-4-5"]
    assert sum(1 for m in anth if "claude-haiku-4-5" in (m.get("aliases") or [])) == 1
    assert not any(m.get("model") == "claude-haiku-4-5" for m in anth)
