"""GET /routing/quality — the model report card (v1.169.0, P3).

Auto-tier silently judges local models on ``observability.local_quality``
(avg completion per (provider, model, task_class) vs ``local_quality_bar`` /
``local_quality_min_samples``) — a judgment the user could never see. The
endpoint exposes it read-only.

What is pinned here, and why VALUES are asserted throughout:

* ``avg``/``clears`` must be the ROUTER'S OWN numbers — the tests seed real
  AgentRun + Evaluation rows and check the endpoint against a hand-computed
  average AND against ``observability.local_quality`` itself, so a second
  drifting implementation cannot ship.
* ``clears`` uses ``>=`` (a model AT the bar clears it) and the REAL
  min_samples gate — an insufficient-evidence model reports its avg but never
  clears, exactly like ``_local_oracle``.
* Cloud providers NEVER appear: the bar judges local models only.
* Per-task-class rows follow the recorded data; per-model granularity matches
  what ``_local_oracle`` actually judges (it passes the rung's model).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun, AgentType
from iron_jarvis.daemon.app import create_app
from iron_jarvis.eval.models import Evaluation


def _seed(platform, provider: str, model: str, task_class: str, scores) -> None:
    """One evaluated session per score: an AgentRun on (provider, model) with
    the given agent type, plus its Evaluation carrying the completion score."""
    with session_scope(platform.engine) as db:
        for score in scores:
            run = AgentRun(
                agent_type=AgentType(task_class),
                provider=provider,
                model=model,
            )
            # session_id defaults to "" — give each run its own session so the
            # session->evaluation join is real.
            run.session_id = f"sess-{run.id}"
            db.add(run)
            db.add(
                Evaluation(
                    session_id=run.session_id,
                    agent_run_id=run.id,
                    completion=float(score),
                )
            )
        db.commit()


def _client(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    return client, client.app.state.platform


def _rows(client) -> list[dict]:
    resp = client.get("/routing/quality")
    assert resp.status_code == 200
    return resp.json()["rows"]


def _row(rows, provider, model, task_class):
    hits = [
        r
        for r in rows
        if r["provider"] == provider
        and r["model"] == model
        and r["task_class"] == task_class
    ]
    assert len(hits) == 1, (provider, model, task_class, rows)
    return hits[0]


# --------------------------------------------------------------------------- #
# Shape + config surface.
# --------------------------------------------------------------------------- #


def test_empty_install_reports_config_and_no_rows(tmp_path):
    client, _ = _client(tmp_path)
    body = client.get("/routing/quality").json()
    assert body["bar"] == 0.75  # the config default
    assert body["min_samples"] == 3
    assert body["rows"] == []


def test_config_knobs_are_live_and_change_the_verdict(tmp_path):
    client, platform = _client(tmp_path)
    # avg 0.6 over 2 sessions: under the defaults (bar .75, min 3) this is
    # "not enough evidence"; under bar .5 / min 2 it CLEARS.
    _seed(platform, "ollama", "qwen2.5:14b", "builder", [0.6, 0.6])
    before = _row(_rows(client), "ollama", "qwen2.5:14b", None)
    assert before["clears"] is False
    assert before["samples"] == 2

    r = client.put(
        "/settings",
        json={"values": {"local_quality_bar": 0.5, "local_quality_min_samples": 2}},
    )
    assert r.status_code == 200
    body = client.get("/routing/quality").json()
    assert body["bar"] == 0.5
    assert body["min_samples"] == 2
    after = _row(body["rows"], "ollama", "qwen2.5:14b", None)
    assert after["avg"] == pytest.approx(0.6)
    assert after["bar"] == 0.5
    assert after["min_samples"] == 2
    assert after["clears"] is True


# --------------------------------------------------------------------------- #
# The three honest states, with hand-computed VALUES.
# --------------------------------------------------------------------------- #


def test_clearing_row_reports_the_routers_own_numbers(tmp_path):
    client, platform = _client(tmp_path)
    _seed(platform, "ollama", "qwen2.5:14b", "builder", [0.9, 0.8, 0.7])
    row = _row(_rows(client), "ollama", "qwen2.5:14b", None)
    assert row["avg"] == pytest.approx(0.8)  # (0.9+0.8+0.7)/3
    assert row["samples"] == 3
    assert row["bar"] == 0.75
    assert row["min_samples"] == 3
    assert row["clears"] is True
    # Value identity with the function the router itself consults — the
    # endpoint must never grow a second implementation that can drift.
    assert row["avg"] == pytest.approx(
        platform.observability.local_quality(
            "ollama", task_class=None, min_samples=1, model="qwen2.5:14b"
        )
    )


def test_below_bar_reports_avg_and_does_not_clear(tmp_path):
    client, platform = _client(tmp_path)
    _seed(platform, "ollama", "qwen2.5:14b", "builder", [0.5, 0.5, 0.6])
    row = _row(_rows(client), "ollama", "qwen2.5:14b", None)
    assert row["avg"] == pytest.approx((0.5 + 0.5 + 0.6) / 3)
    assert row["samples"] == 3
    assert row["clears"] is False


def test_not_enough_evidence_reports_avg_but_never_clears(tmp_path):
    # A stellar average over too few sessions must NOT clear — this is the
    # exact "don't trust optimism" gate _local_oracle applies.
    client, platform = _client(tmp_path)
    _seed(platform, "ollama", "qwen2.5:14b", "builder", [0.95, 0.95])
    row = _row(_rows(client), "ollama", "qwen2.5:14b", None)
    assert row["avg"] == pytest.approx(0.95)
    assert row["samples"] == 2
    assert row["min_samples"] == 3
    assert row["clears"] is False


def test_avg_exactly_at_the_bar_clears(tmp_path):
    # ``>=`` — a model AT the bar clears it. Kills the ``>`` mutation, which
    # would silently demote every model sitting exactly on the line.
    client, platform = _client(tmp_path)
    _seed(platform, "ollama", "qwen2.5:14b", "builder", [0.75, 0.75, 0.75])
    row = _row(_rows(client), "ollama", "qwen2.5:14b", None)
    assert row["avg"] == pytest.approx(0.75)
    assert row["clears"] is True


# --------------------------------------------------------------------------- #
# Locality: cloud never appears; fleet-* does.
# --------------------------------------------------------------------------- #


def test_cloud_providers_never_appear(tmp_path):
    client, platform = _client(tmp_path)
    for cloud in ("openai", "anthropic", "claude-cli", "openrouter"):
        _seed(platform, cloud, "some-model", "builder", [0.9, 0.9, 0.9])
    _seed(platform, "ollama", "qwen2.5:14b", "builder", [0.8, 0.8, 0.8])
    rows = _rows(client)
    providers = {r["provider"] for r in rows}
    assert providers == {"ollama"}


def test_fleet_prefixed_providers_are_local(tmp_path):
    client, platform = _client(tmp_path)
    _seed(platform, "fleet-spark", "gpt-oss-120b", "builder", [0.8, 0.9, 1.0])
    row = _row(_rows(client), "fleet-spark", "gpt-oss-120b", None)
    assert row["avg"] == pytest.approx(0.9)
    assert row["clears"] is True


# --------------------------------------------------------------------------- #
# Per-task-class and per-model granularity — what the router actually judges.
# --------------------------------------------------------------------------- #


def test_task_class_rows_follow_the_data(tmp_path):
    client, platform = _client(tmp_path)
    _seed(platform, "ollama", "qwen2.5:14b", "builder", [0.9, 0.9, 0.9])
    _seed(platform, "ollama", "qwen2.5:14b", "researcher", [0.2, 0.2, 0.2])
    rows = _rows(client)
    agg = _row(rows, "ollama", "qwen2.5:14b", None)
    assert agg["avg"] == pytest.approx(0.55)  # all six evaluations
    assert agg["samples"] == 6
    assert agg["clears"] is False
    builder = _row(rows, "ollama", "qwen2.5:14b", "builder")
    assert builder["avg"] == pytest.approx(0.9)
    assert builder["samples"] == 3
    assert builder["clears"] is True
    researcher = _row(rows, "ollama", "qwen2.5:14b", "researcher")
    assert researcher["avg"] == pytest.approx(0.2)
    assert researcher["samples"] == 3
    assert researcher["clears"] is False
    # No invented classes: only the two the data carries (plus the aggregate).
    assert {r["task_class"] for r in rows} == {None, "builder", "researcher"}


def test_models_on_one_provider_are_judged_separately(tmp_path):
    # One endpoint can serve a 14B and a 120B; their track records are not
    # interchangeable (the exact reason _local_oracle passes the model).
    client, platform = _client(tmp_path)
    _seed(platform, "custom", "qwen2.5:14b", "builder", [0.9, 0.9, 0.9])
    _seed(platform, "custom", "gpt-oss-120b", "builder", [0.3, 0.3, 0.3])
    rows = _rows(client)
    small = _row(rows, "custom", "qwen2.5:14b", None)
    big = _row(rows, "custom", "gpt-oss-120b", None)
    assert small["avg"] == pytest.approx(0.9)
    assert small["samples"] == 3
    assert small["clears"] is True
    assert big["avg"] == pytest.approx(0.3)
    assert big["samples"] == 3
    assert big["clears"] is False


# --------------------------------------------------------------------------- #
# Connected-but-unproven local models get an honest zero-sample row.
# --------------------------------------------------------------------------- #


def test_connected_local_model_with_no_runs_gets_a_zero_sample_row(
    tmp_path, monkeypatch
):
    client, _ = _client(tmp_path)
    monkeypatch.setattr(
        "iron_jarvis.providers.routing.connected_real_models",
        lambda m, c: [
            {"provider": "custom", "model": "qwen3-32b"},
            {"provider": "openai", "model": "gpt-4o"},  # cloud: must not leak in
        ],
    )
    rows = _rows(client)
    row = _row(rows, "custom", "qwen3-32b", None)
    assert row["avg"] is None
    assert row["samples"] == 0
    assert row["clears"] is False
    assert all(r["provider"] != "openai" for r in rows)


def test_routable_fleet_node_gets_zero_sample_row_without_any_runs(tmp_path):
    """The honest zero-sample state must be REACHABLE for fleet endpoints.

    ``connected_real_models`` never enumerates ``fleet-*`` providers (its pool
    is KNOWN_MODELS + the two config slots), so without registry seeding a
    freshly added, registered node was silently ABSENT — the Connections
    endpoint block rendered nothing instead of "not enough evidence yet
    (0 of 3)". Seeded from the fleet registry's routable nodes."""
    from iron_jarvis.fleet.models import FleetNode

    client, platform = _client(tmp_path)
    platform.fleet.add(
        FleetNode(
            id="spark",
            label="Spark",
            base_url="http://127.0.0.1:9/v1",
            source="user",
            routable=True,
            default_model="gpt-oss-120b",
        )
    )
    # What POST /fleet/nodes does on add: the node becomes a provider NOW.
    platform.fleet.register_providers(platform.providers)
    row = _row(_rows(client), "fleet-spark", "gpt-oss-120b", None)
    assert row["avg"] is None
    assert row["samples"] == 0
    assert row["clears"] is False


def test_unreachable_fleet_node_is_not_seeded(tmp_path):
    """The seed is gated on availability — a node the sampler marked
    unreachable is not "connected", so it gets no phantom zero-sample row
    (recorded RUNS on it would still appear via the SQL pass)."""
    from iron_jarvis.fleet.models import FleetNode

    client, platform = _client(tmp_path)
    platform.fleet.add(
        FleetNode(
            id="spark",
            label="Spark",
            base_url="http://127.0.0.1:9/v1",
            source="user",
            routable=True,
            default_model="gpt-oss-120b",
        )
    )
    platform.fleet.register_providers(platform.providers)
    platform.fleet.set_reachable("spark", False)
    assert all(r["provider"] != "fleet-spark" for r in _rows(client))


def test_opencode_local_models_get_zero_sample_rows(tmp_path, monkeypatch):
    """opencode-cli has no KNOWN_MODELS entry, so its allowlisted local models
    never reach ``connected_real_models`` either — they are seeded from the
    manager's own allowlist when the provider is available."""
    client, platform = _client(tmp_path)
    monkeypatch.setattr(
        platform.providers, "_cli_binary_present", lambda binary: binary == "opencode"
    )
    monkeypatch.setattr(
        platform.providers, "_opencode_cache", ["ollama/qwen3:32b"]
    )
    row = _row(_rows(client), "opencode-cli", "ollama/qwen3:32b", None)
    assert row["avg"] is None
    assert row["samples"] == 0
    assert row["clears"] is False


# --------------------------------------------------------------------------- #
# An empty-model key must describe ONE population, not two.
# --------------------------------------------------------------------------- #


def test_empty_model_row_counts_samples_over_the_whole_provider(tmp_path):
    """``_row`` coerces model "" -> None, and ``local_quality(model=None)``
    judges the provider across ALL its models — so the row's ``samples`` must
    count that same population. The old per-key count produced clears=True
    with samples=0, which the UI renders as "not enough evidence yet",
    HIDING a real verdict behind a row whose avg and samples described
    different worlds."""
    client, platform = _client(tmp_path)
    _seed(platform, "ollama", "llama3.1", "builder", [0.9, 0.9, 0.9])
    # One run recorded with an EMPTY model string and no evaluation — the
    # exact shape that used to produce the incoherent row.
    with session_scope(platform.engine) as db:
        run = AgentRun(agent_type=AgentType("builder"), provider="ollama", model="")
        run.session_id = f"sess-{run.id}"
        db.add(run)
        db.commit()
    rows = _rows(client)
    row = _row(rows, "ollama", "", None)
    assert row["samples"] == 3  # every evaluated session of the provider
    assert row["avg"] == pytest.approx(0.9)
    assert row["clears"] is True  # 3 >= min_samples and 0.9 >= 0.75
    # Value identity with the population the avg actually judges.
    assert row["avg"] == pytest.approx(
        platform.observability.local_quality(
            "ollama", task_class=None, min_samples=1, model=None
        )
    )
    # The named-model row keeps its own (narrower) population.
    named = _row(rows, "ollama", "llama3.1", None)
    assert named["samples"] == 3
    assert named["avg"] == pytest.approx(0.9)
