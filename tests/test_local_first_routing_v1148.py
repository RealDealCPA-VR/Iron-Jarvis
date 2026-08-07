"""Local-first routing (v1.148.0) — the setting that could never fire.

``prefer_local_when_capable`` shipped in v1.89.0 and, on the machine that
reported this, could not work at all: every path in the platform's local oracle
was hardwired to Ollama — the ``ollama_base_url`` gate, the provider name, and
the model. A user whose hardware is a fleet node (``fleet-custom``), an
LM Studio / vLLM endpoint (``custom``), or OpenCode got ``None`` on every call.
That is most local-fleet users, and precisely the group the setting is for.

It also could not be turned ON: the four knobs were absent from
``_SETTINGS_KEYS``, so config.toml was the only editor.

And "local" had two definitions that disagreed — ``manager.health()`` called
``grok-cli`` local (a client for xAI's HOSTED API) while ``usage_view`` did not
call ``opencode-cli`` local (in this daemon it is). A router that prefers local
models is only as trustworthy as its answer to "what is local".
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.local import (
    is_local_provider,
    local_ladder,
    local_models,
    model_size_b,
)
from iron_jarvis.providers.routing import derive_tiers


# --------------------------------------------------------------------------- #
# (1) ONE definition of "local".
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "provider", ["ollama", "custom", "opencode-cli", "fleet-a1b2", "FLEET-X"]
)
def test_own_hardware_is_local(provider):
    assert is_local_provider(provider) is True


@pytest.mark.parametrize(
    "provider",
    ["anthropic", "openai", "google", "openrouter", "claude-cli", "codex-cli", "mock"],
)
def test_hosted_and_subscription_clis_are_not_local(provider):
    assert is_local_provider(provider) is False


def test_grok_cli_is_not_local():
    """It is a terminal client for xAI's HOSTED API. Calling it local reported
    someone else's GPUs as the user's own — in the picker AND in the routing
    that now depends on this answer."""
    assert is_local_provider("grok-cli") is False


def test_health_and_the_router_agree_on_local(tmp_path):
    """The two definitions had drifted apart; this fails if they ever do again."""
    client = TestClient(create_app(str(tmp_path)))
    for row in client.get("/health").json()["providers"]:
        if row.get("class") == "browser":
            continue  # vault-backed browser sessions aren't a routing target
        assert (row["class"] == "local") == is_local_provider(row["provider"]), row


def test_the_usage_rollup_uses_the_same_definition():
    from iron_jarvis.eval.usage_view import is_local_provider as usage_is_local

    assert usage_is_local("fleet-spark") is True
    assert usage_is_local("grok-cli") is False
    # ...while keeping its own opencode/<sub-provider> rule.
    assert usage_is_local("opencode/anthropic") is False
    assert usage_is_local("opencode/spark") is True


# --------------------------------------------------------------------------- #
# (2) Model sizes — the ladder's ordering key.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "model,expected",
    [
        ("qwen2.5-coder:14b", 14.0),
        ("gpt-oss-120b", 120.0),
        ("llama-3.3-70B-Instruct", 70.0),
        ("qwen3-30b-a3b", 30.0),        # MoE: total, not the active count
        ("mistral-7b-instruct-q4_0", 7.0),
        ("deepseek-v3.1:671b", 671.0),
    ],
)
def test_parameter_counts_are_read_from_the_id(model, expected):
    assert model_size_b(model) == expected


@pytest.mark.parametrize(
    "model", ["claude-opus-4-8", "gpt-5", "llama3.1", "fleet", "", "claude-20241022"]
)
def test_an_unstated_size_is_none_not_a_guess(model):
    """Guessing small would route hard work to a model that can't do it;
    guessing large would strand a small one. None means unknown."""
    assert model_size_b(model) is None


# --------------------------------------------------------------------------- #
# (3) The ladder: smallest first, user override honoured.
# --------------------------------------------------------------------------- #
class _Mgr:
    def __init__(self, available: set[str]):
        self._a = available

    def available(self, p):
        return p in self._a


def _cfg(**kw):
    base = dict(
        ollama_base_url=None, ollama_model="", custom_base_url=None, custom_model="",
        routing_local_ladder=[],
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def pool(monkeypatch):
    """A fleet: a 14B, a 32B and a 120B, plus a cloud model."""
    entries = [
        {"provider": "fleet-b", "model": "qwen3-32b"},
        {"provider": "anthropic", "model": "claude-opus-4-8"},
        {"provider": "fleet-c", "model": "gpt-oss-120b"},
        {"provider": "fleet-a", "model": "qwen2.5-coder:14b"},
    ]
    monkeypatch.setattr(
        "iron_jarvis.providers.routing.connected_real_models", lambda m, c: entries
    )
    return entries


def test_local_models_are_ordered_smallest_first(pool):
    got = [e["model"] for e in local_models(_Mgr(set()), _cfg())]
    assert got == ["qwen2.5-coder:14b", "qwen3-32b", "gpt-oss-120b"]


def test_cloud_models_are_not_in_the_ladder(pool):
    assert all(e["provider"] != "anthropic" for e in local_models(_Mgr(set()), _cfg()))


def test_a_configured_ladder_wins_and_keeps_its_order(pool):
    cfg = _cfg(routing_local_ladder=["fleet-c:gpt-oss-120b", "fleet-a:qwen2.5-coder:14b"])
    assert [e["provider"] for e in local_ladder(_Mgr(set()), cfg)] == ["fleet-c", "fleet-a"]


def test_a_ladder_rung_whose_machine_is_off_is_skipped_not_an_error(pool):
    cfg = _cfg(routing_local_ladder=["fleet-zz:missing", "fleet-a:qwen2.5-coder:14b"])
    assert [e["provider"] for e in local_ladder(_Mgr(set()), cfg)] == ["fleet-a"]


def test_unknown_sizes_sort_last_rather_than_being_guessed(monkeypatch):
    monkeypatch.setattr(
        "iron_jarvis.providers.routing.connected_real_models",
        lambda m, c: [
            {"provider": "custom", "model": "house-model"},
            {"provider": "fleet-a", "model": "qwen2.5-coder:14b"},
        ],
    )
    assert [e["model"] for e in local_models(_Mgr(set()), _cfg())] == [
        "qwen2.5-coder:14b",
        "house-model",
    ]


# --------------------------------------------------------------------------- #
# (4) Auto tiers become local-first — and cloud is reached only by escalation.
# --------------------------------------------------------------------------- #
_FLEET = [
    {"provider": "fleet-a", "model": "qwen2.5-coder:14b"},
    {"provider": "fleet-b", "model": "qwen3-32b"},
    {"provider": "fleet-c", "model": "gpt-oss-120b"},
    {"provider": "anthropic", "model": "claude-opus-4-8"},
]


def test_local_first_fills_every_tier_from_the_users_own_hardware():
    tiers = derive_tiers(_FLEET, local_first=True)
    assert tiers["light"] == ("fleet-a", "qwen2.5-coder:14b")
    assert tiers["heavy"] == ("fleet-c", "gpt-oss-120b")
    assert all(p.startswith("fleet-") for p, _ in tiers.values())


def test_without_local_first_the_old_mapping_is_untouched():
    """The default path must be byte-identical — this is opt-in."""
    assert derive_tiers(_FLEET) == derive_tiers(_FLEET, local_first=False)
    assert derive_tiers(_FLEET)["heavy"][0] == "anthropic"


def test_local_first_falls_back_to_cloud_when_no_local_model_is_connected():
    cloud = [{"provider": "anthropic", "model": "claude-opus-4-8"}]
    assert derive_tiers(cloud, local_first=True) == derive_tiers(cloud)


def test_a_single_local_model_takes_every_tier():
    one = [{"provider": "custom", "model": "qwen3-32b"}, *_FLEET[3:]]
    tiers = derive_tiers(one, local_first=True)
    assert set(tiers.values()) == {("custom", "qwen3-32b")}


# --------------------------------------------------------------------------- #
# (5) THE BUG: the oracle now fires for hardware that isn't Ollama.
# --------------------------------------------------------------------------- #
def _oracle_platform(tmp_path, monkeypatch, *, quality: float | None, pool_entries):
    """A real app whose observability reports a fixed quality score."""
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    platform.config.prefer_local_when_capable = True
    monkeypatch.setattr(
        "iron_jarvis.providers.routing.connected_real_models",
        lambda m, c: pool_entries,
    )
    monkeypatch.setattr(
        type(platform.observability),
        "local_quality",
        lambda self, provider, task_class=None, min_samples=3, model=None: quality,
    )
    return client, platform


def test_the_oracle_fires_for_a_fleet_node_with_no_ollama_configured(
    tmp_path, monkeypatch
):
    """The reported machine exactly: a Spark behind fleet-custom, ollama_base_url
    unset. Before v1.148.0 this returned None on every call."""
    client, platform = _oracle_platform(
        tmp_path,
        monkeypatch,
        quality=0.9,
        pool_entries=[{"provider": "fleet-custom", "model": "qwen3-32b"}],
    )
    assert not platform.config.ollama_base_url
    assert platform.router._local_oracle("builder") == ("fleet-custom", "qwen3-32b")


def test_the_oracle_prefers_the_SMALLEST_qualifying_rung(tmp_path, monkeypatch):
    """"the smallest model likely to complete the task" — the brief's words."""
    client, platform = _oracle_platform(
        tmp_path, monkeypatch, quality=0.9, pool_entries=_FLEET
    )
    assert platform.router._local_oracle("builder") == ("fleet-a", "qwen2.5-coder:14b")


def test_below_the_quality_bar_nothing_is_preferred(tmp_path, monkeypatch):
    client, platform = _oracle_platform(
        tmp_path, monkeypatch, quality=0.10, pool_entries=_FLEET
    )
    assert platform.router._local_oracle("builder") is None


def test_no_evidence_means_no_preference(tmp_path, monkeypatch):
    """local_quality returns None below min_samples — a fresh install must route
    exactly as it did before the setting was turned on."""
    client, platform = _oracle_platform(
        tmp_path, monkeypatch, quality=None, pool_entries=_FLEET
    )
    assert platform.router._local_oracle("builder") is None


def test_the_setting_off_is_a_no_op(tmp_path, monkeypatch):
    client, platform = _oracle_platform(
        tmp_path, monkeypatch, quality=0.99, pool_entries=_FLEET
    )
    platform.config.prefer_local_when_capable = False
    assert platform.router._local_oracle("builder") is None


def test_the_oracle_never_raises_on_a_broken_pool(tmp_path, monkeypatch):
    client, platform = _oracle_platform(
        tmp_path, monkeypatch, quality=0.9, pool_entries=[]
    )
    monkeypatch.setattr(
        "iron_jarvis.providers.routing.connected_real_models",
        lambda m, c: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert platform.router._local_oracle("builder") is None


# --------------------------------------------------------------------------- #
# (6) The knobs are reachable at all.
# --------------------------------------------------------------------------- #
def test_the_local_first_settings_are_readable_and_writable(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    settings = client.get("/settings").json()["settings"]
    for key in (
        "prefer_local_when_capable",
        "local_quality_bar",
        "local_quality_min_samples",
        "routing_local_ladder",
        "decompose_local_tasks",
    ):
        assert key in settings, f"{key} is not reachable from the Settings API"

    r = client.put(
        "/settings",
        json={
            "values": {
                "prefer_local_when_capable": True,
                "local_quality_bar": 0.8,
                "routing_local_ladder": ["fleet-a:qwen2.5-coder:14b", "fleet-c:gpt-oss-120b"],
            }
        },
    )
    assert r.status_code == 200
    out = r.json()["settings"]
    assert out["prefer_local_when_capable"] is True
    assert out["local_quality_bar"] == 0.8
    assert out["routing_local_ladder"] == [
        "fleet-a:qwen2.5-coder:14b",
        "fleet-c:gpt-oss-120b",
    ]


def test_the_ladder_survives_a_restart(tmp_path):
    root = str(tmp_path)
    with TestClient(create_app(root)) as c1:
        c1.put(
            "/settings",
            json={"values": {"routing_local_ladder": ["custom:qwen3-32b"]}},
        )
    with TestClient(create_app(root)) as c2:
        assert c2.get("/settings").json()["settings"]["routing_local_ladder"] == [
            "custom:qwen3-32b"
        ]


# --------------------------------------------------------------------------- #
# (7) The picker gets what it needs to sort local-first.
# --------------------------------------------------------------------------- #
def test_models_carry_where_they_run_and_how_big_they_are(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    models = client.get("/models").json()["models"]
    assert models, "no selectable models at all"
    assert all("kind" in m for m in models)
    assert {m["kind"] for m in models} <= {"local", "cli", "api"}
    for m in models:
        assert (m["kind"] == "local") == is_local_provider(m["provider"])
        assert m["size_b"] == model_size_b(m["model"])
