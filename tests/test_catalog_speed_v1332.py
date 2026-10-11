"""v1.332.0 speed package S1: the model catalog and the provider clients.

Every pin here COUNTS calls (probes, subprocesses, client builds, catalog
builds); none compares a clock reading with a bar. Concurrency is proven with
a ``threading.Barrier`` that a serial loop can never pass.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import types

import httpx
import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import connections as C
from iron_jarvis.fleet.models import FleetNode
from iron_jarvis.providers import cli_detect, discovery
from iron_jarvis.providers.adapters.anthropic import AnthropicAdapter
from iron_jarvis.providers.adapters.base import LLMMessage

#: The real builder, before any fixture wraps it.
_REAL_BUILD = C.build_selectable_models
#: The real listing fetch, before the autouse fixture stubs it.
_REAL_GET_JSON = discovery._get_json


@pytest.fixture(autouse=True)
def _fresh_memos(monkeypatch):
    # No live network from this file: a test that saves a (fake) cloud key and
    # builds the catalog would otherwise ask the real provider's model list.
    # Tests that need an answer replace this with their own fake.
    def _no_network(url, headers):
        raise httpx.ConnectError("no network in tests")

    monkeypatch.setattr(discovery, "_get_json", _no_network)
    discovery.clear_cache()
    cli_detect.forget_detection_memos()
    yield
    discovery.clear_cache()
    cli_detect.forget_detection_memos()


class _Clock:
    """A monotonic clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


def _wait_until(check, timeout: float = 10.0) -> bool:
    done = threading.Event()
    for _ in range(int(timeout / 0.01)):
        if check():
            return True
        done.wait(0.01)
    return check()


# --------------------------------------------------------------------------- #
# 1. Ollama: no probe when there is nothing to probe
# --------------------------------------------------------------------------- #


class _CountingHttp:
    """Stands in for ``httpx.Client``; counts every client made (= every probe)."""

    def __init__(self, *, answer: dict | None) -> None:
        self.answer = answer
        self.made = 0

    def __call__(self, *a, **kw):
        self.made += 1
        outer = self

        class _Client:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get(self, url, **k):
                if outer.answer is None:
                    raise httpx.ConnectError("refused")
                return httpx.Response(200, json=outer.answer)

        return _Client()


@pytest.fixture
def ollama_probe(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_detect, "_OLLAMA_PROBE_FORCED_OFF", False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "no-local-appdata"))
    monkeypatch.setattr(cli_detect.shutil, "which", lambda name: None)
    http = _CountingHttp(answer=None)
    monkeypatch.setattr(httpx, "Client", http)
    return http


def test_no_ollama_binary_and_no_ollama_host_means_no_probe_at_all(ollama_probe):
    assert cli_detect.detect_ollama() == []
    assert cli_detect.detect_ollama(configured_url="http://10.0.0.5:11434") == []
    assert ollama_probe.made == 0


def test_an_installed_ollama_is_probed_once_and_lists_its_models(ollama_probe, monkeypatch):
    monkeypatch.setattr(cli_detect.shutil, "which", lambda name: "C:/Ollama/ollama.exe")
    ollama_probe.answer = {"models": [{"name": "llama3:8b"}]}
    found = cli_detect.detect_ollama()
    assert [m.model for m in found] == ["llama3:8b"] and found[0].available
    assert ollama_probe.made == 1


def test_a_setting_pointing_at_this_machines_ollama_is_probed_without_a_binary(ollama_probe):
    cli_detect.detect_ollama(configured_url="http://127.0.0.1:11434")
    assert ollama_probe.made == 1


def test_a_probe_that_got_no_answer_is_not_repeated_within_30_seconds(ollama_probe, monkeypatch):
    monkeypatch.setattr(cli_detect.shutil, "which", lambda name: "C:/Ollama/ollama.exe")
    clock = _Clock()
    monkeypatch.setattr(cli_detect, "time", clock)
    for _ in range(3):
        assert cli_detect.detect_ollama() == []
    assert ollama_probe.made == 1
    clock.now += 29
    cli_detect.detect_ollama()
    assert ollama_probe.made == 1
    clock.now += 2  # 31 s after the miss
    cli_detect.detect_ollama()
    assert ollama_probe.made == 2


def test_redetect_forgets_that_ollama_did_not_answer(ollama_probe, monkeypatch, tmp_path):
    monkeypatch.setattr(cli_detect.shutil, "which", lambda name: "C:/Ollama/ollama.exe")
    client = TestClient(create_app(str(tmp_path)))
    cli_detect.detect_ollama()
    cli_detect.detect_ollama()
    assert ollama_probe.made == 1
    assert client.post("/providers/rescan").status_code == 200
    assert ollama_probe.made == 2  # Re-detect really asked again


# --------------------------------------------------------------------------- #
# 2. Grok: the 20 s fallback runs at most once per 10 minutes
# --------------------------------------------------------------------------- #


@pytest.fixture
def grok_without_cache(monkeypatch, tmp_path):
    home = tmp_path / "grokhome"
    home.mkdir()
    (home / "auth.json").write_text(json.dumps({"x::y": {"key": "tok"}}), encoding="utf-8")
    monkeypatch.setenv("GROK_HOME", str(home))
    monkeypatch.setattr(cli_detect, "_grok_binary", lambda: "C:/grok/grok.exe")
    runs: list[list[str]] = []

    def fake_run(argv, **kw):
        runs.append(list(argv))
        return types.SimpleNamespace(stdout="grok-build\ngrok-fast\n", returncode=0)

    import subprocess

    monkeypatch.setattr(subprocess, "run", fake_run)
    return home, runs


def test_the_grok_fallback_runs_once_across_several_catalog_builds(grok_without_cache):
    _home, runs = grok_without_cache
    for _ in range(4):
        found = [m for m in cli_detect.detect_cli_providers() if m.provider == "grok-cli"]
        assert [m.model for m in found] == ["grok-build", "grok-fast"]
    assert len(runs) == 1


def test_a_new_grok_cache_file_is_read_at_once(grok_without_cache):
    home, runs = grok_without_cache
    cache = home / "models_cache.json"
    cache.write_text("{not json", encoding="utf-8")
    os.utime(cache, ns=(1_000_000_000, 1_000_000_000))
    cli_detect.detect_grok()
    cli_detect.detect_grok()
    assert len(runs) == 1
    # Rewritten (a new mtime): the memo made before the change is not used.
    cache.write_text("{still not json", encoding="utf-8")
    os.utime(cache, ns=(2_000_000_000, 2_000_000_000))
    cli_detect.detect_grok()
    assert len(runs) == 2
    # A good cache file is read straight away, no subprocess at all.
    cache.write_text(json.dumps({"models": {"grok-new": {"info": {"name": "Grok New"}}}}), encoding="utf-8")
    assert [m.model for m in cli_detect.detect_grok()] == ["grok-new"]
    assert len(runs) == 2


def test_redetect_forgets_the_grok_fallback(grok_without_cache):
    _home, runs = grok_without_cache
    cli_detect.detect_grok()
    cli_detect.forget_detection_memos()
    cli_detect.detect_grok()
    assert len(runs) == 2


# --------------------------------------------------------------------------- #
# 3. Anthropic: reuse the client per (key, event loop)
# --------------------------------------------------------------------------- #


class _A:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeSdk:
    def __init__(self, key: str) -> None:
        self.key = key
        outer = self

        class _Messages:
            async def create(self, **kw):
                outer.calls += 1
                return _A(
                    content=[_A(type="text", text="hi")],
                    stop_reason="end_turn",
                    usage=_A(input_tokens=1, output_tokens=1),
                )

        self.calls = 0
        self.messages = _Messages()


@pytest.fixture
def sdk_builds(monkeypatch):
    built: list[_FakeSdk] = []

    def fake_build(key):
        client = _FakeSdk(key)
        built.append(client)
        return client

    monkeypatch.setattr(AnthropicAdapter, "_build", staticmethod(fake_build))
    return built


def _ask(adapter: AnthropicAdapter):
    return adapter.complete(system="", messages=[LLMMessage(role="user", content="hi")], tools=[])


def test_two_calls_on_one_loop_build_one_client(sdk_builds):
    adapter = AnthropicAdapter(api_key="sk-ant-api-1")

    async def two():
        await _ask(adapter)
        await _ask(adapter)

    asyncio.run(two())
    assert len(sdk_builds) == 1 and sdk_builds[0].calls == 2


def test_a_different_key_or_a_different_loop_builds_a_new_client(sdk_builds):
    key = {"v": "sk-ant-api-1"}
    adapter = AnthropicAdapter(credential=lambda: key["v"])

    async def one():
        await _ask(adapter)

    asyncio.run(one())
    asyncio.run(one())  # a new loop (a schedule fire runs under asyncio.run)
    assert len(sdk_builds) == 2

    async def rotate():
        await _ask(adapter)
        key["v"] = "sk-ant-api-2"
        await _ask(adapter)
        await _ask(adapter)

    asyncio.run(rotate())
    assert [c.key for c in sdk_builds[2:]] == ["sk-ant-api-1", "sk-ant-api-2"]


def test_a_client_is_never_handed_to_a_loop_it_was_not_built_on(sdk_builds):
    adapter = AnthropicAdapter(api_key="sk-ant-api-1")
    first = asyncio.new_event_loop()
    second = asyncio.new_event_loop()
    try:
        first.run_until_complete(_ask(adapter))
        second.run_until_complete(_ask(adapter))  # both loops still open
        first.run_until_complete(_ask(adapter))
    finally:
        first.close()
        second.close()
    assert len(sdk_builds) == 2
    assert [c.calls for c in sdk_builds] == [2, 1]


def test_the_replaced_client_seam_still_wins(sdk_builds):
    adapter = AnthropicAdapter(api_key="sk-ant-api-1")
    fake = _FakeSdk("seam")
    adapter._client = lambda: fake  # noqa: SLF001 — the suite's long-standing seam
    asyncio.run(_ask(adapter))
    assert fake.calls == 1 and sdk_builds == []


def test_a_seam_patched_on_the_class_still_wins(sdk_builds, monkeypatch):
    fake = _FakeSdk("class seam")
    monkeypatch.setattr(AnthropicAdapter, "_client", lambda self: fake)
    asyncio.run(_ask(AnthropicAdapter(api_key="sk-ant-api-1")))
    assert fake.calls == 1 and sdk_builds == []


def test_an_account_token_is_still_refused_before_any_client_exists(sdk_builds):
    adapter = AnthropicAdapter(api_key="sk-ant-oat-abc")
    with pytest.raises(RuntimeError, match="OAuth account tokens"):
        asyncio.run(_ask(adapter))
    assert sdk_builds == []


# --------------------------------------------------------------------------- #
# 4. Discovery at the same time; a stale list never blocks
# --------------------------------------------------------------------------- #


def _app(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    return client, client.app.state.d


def _add_node(d, node_id: str, *, protocol: str = "openai", key_name: str = "", default: str = "m-default"):
    node = FleetNode(
        id=node_id,
        label=f"Box {node_id}",
        base_url=f"http://{node_id}.test:8000/v1",
        routable=True,
        default_model=default,
        api_key_name=key_name,
        protocol=protocol,
    )
    d.fleet.add(node)
    d.fleet.register_providers(d.platform.providers)
    return node


def test_every_provider_and_endpoint_is_asked_at_the_same_time_and_merged_in_order(tmp_path, monkeypatch):
    client, d = _app(tmp_path)
    assert client.post("/connections/anthropic/key", json={"key": "sk-ant-test"}).status_code == 200
    assert client.post("/connections/openai/key", json={"key": "sk-test"}).status_code == 200
    _add_node(d, "alpha")
    _add_node(d, "beta")
    asked = ["anthropic", "openai", "fleet-alpha", "fleet-beta"]
    lists = {
        "anthropic": ["claude-opus-4-8", "claude-new-1"],
        "openai": ["gpt-5.5", "gpt-new"],
        "fleet-alpha": ["qwen3-coder"],
        "fleet-beta": ["llama4", "gemma4"],
    }

    def serial_fake(provider, credential, *, base_url="", protocol="openai"):
        return list(lists[provider])

    monkeypatch.setattr(discovery, "discover_models", serial_fake)
    expected = C.build_selectable_models(d)

    # Every call waits for all four to have started (a serial loop breaks the
    # barrier), then they finish in REVERSE order of asking, so a merge that
    # followed completion order would put the rows in a different order.
    barrier = threading.Barrier(len(asked), timeout=5)
    finished = {p: threading.Event() for p in asked}

    def concurrent_fake(provider, credential, *, base_url="", protocol="openai"):
        barrier.wait()
        later = asked[asked.index(provider) + 1:]
        for p in later:
            assert finished[p].wait(5)
        finished[provider].set()
        return list(lists[provider])

    monkeypatch.setattr(discovery, "discover_models", concurrent_fake)
    got = C.build_selectable_models(d)
    assert got == expected
    names = {(m["provider"], m["model"]) for m in got}
    assert ("anthropic", "claude-new-1") in names and ("fleet-beta", "gemma4") in names


def test_a_stale_list_is_served_at_once_while_one_refresh_runs(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(discovery, "time", clock)
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def fake_models(base_url, key=""):
        calls.append(base_url)
        if len(calls) == 1:
            return ["old-model"]
        started.set()
        release.wait(5)
        return ["new-model"]

    monkeypatch.setattr(discovery, "_openai_compatible_models", fake_models)
    get = lambda: discovery.discover_models("custom", lambda: "", base_url="http://h:1/v1")  # noqa: E731
    assert get() == ["old-model"]
    clock.now += 601  # past the 10-minute window
    assert get() == ["old-model"]  # served without waiting for the server
    assert started.wait(5)
    assert get() == ["old-model"]  # still refreshing: no second refresh
    assert len(calls) == 2
    gen = discovery.generation()
    release.set()
    assert _wait_until(lambda: get() == ["new-model"])
    assert len(calls) == 2
    assert discovery.generation() > gen  # the catalog learns the list moved


def test_a_failed_fetch_is_asked_again_after_60_seconds_not_10_minutes(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(discovery, "time", clock)
    calls: list[str] = []

    def failing(base_url, key=""):
        calls.append(base_url)
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(discovery, "_openai_compatible_models", failing)
    get = lambda: discovery.discover_models("custom", lambda: "", base_url="http://h:2/v1")  # noqa: E731
    assert get() == []
    clock.now += 59
    assert get() == [] and len(calls) == 1
    clock.now += 2
    assert get() == []
    assert _wait_until(lambda: len(calls) == 2)


# --------------------------------------------------------------------------- #
# 5. A fleet endpoint lists its real models, asked the way it chats
# --------------------------------------------------------------------------- #


def _fleet_rows(client, provider: str) -> list[dict]:
    return [m for m in client.get("/models").json()["models"] if m["provider"] == provider]


def test_an_openai_endpoint_lists_its_v1_models_ids(tmp_path, monkeypatch):
    client, d = _app(tmp_path)
    _add_node(d, "gpu")
    seen: list[tuple[str, dict]] = []

    def fake_get_json(url, headers):
        seen.append((url, dict(headers)))
        return {"data": [{"id": "qwen3-coder"}, {"id": "llama4"}]}

    monkeypatch.setattr(discovery, "_get_json", fake_get_json)
    rows = _fleet_rows(client, "fleet-gpu")
    assert [r["model"] for r in rows] == ["qwen3-coder", "llama4"]
    assert all(r["name"] == "Box gpu" and r["source"] == "endpoint" for r in rows)
    assert seen == [("http://gpu.test:8000/v1/models", {})]


def test_an_anthropic_endpoint_is_asked_with_x_api_key_and_anthropic_version(tmp_path, monkeypatch):
    client, d = _app(tmp_path)
    d.platform.secrets.set("box_key", "sk-box-1", kind="api_key")
    _add_node(d, "proxy", protocol="anthropic", key_name="box_key")
    seen: list[tuple[str, dict]] = []

    def fake_get_json(url, headers):
        seen.append((url, dict(headers)))
        if "after_id" not in url:
            return {"data": [{"id": "claude-a"}], "has_more": True, "last_id": "claude-a"}
        return {"data": [{"id": "claude-b"}], "has_more": False}

    monkeypatch.setattr(discovery, "_get_json", fake_get_json)
    rows = _fleet_rows(client, "fleet-proxy")
    assert [r["model"] for r in rows] == ["claude-a", "claude-b"]
    assert seen[0][0] == "http://proxy.test:8000/v1/models?limit=100"
    assert seen[1][0] == "http://proxy.test:8000/v1/models?limit=100&after_id=claude-a"
    assert all(h == {"anthropic-version": "2023-06-01", "x-api-key": "sk-box-1"} for _u, h in seen)


def test_a_dead_endpoint_keeps_its_default_model_and_is_asked_once(tmp_path, monkeypatch):
    client, d = _app(tmp_path)
    _add_node(d, "dead", default="my-default")
    seen: list[str] = []

    def refused(url, headers):
        seen.append(url)
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(discovery, "_get_json", refused)
    assert [r["model"] for r in _fleet_rows(client, "fleet-dead")] == ["my-default"]
    assert seen == ["http://dead.test:8000/v1/models"]  # no /api/tags second try


def test_a_key_saved_for_the_custom_slot_asks_its_endpoint_row_again(tmp_path, monkeypatch):
    """The Custom slot is also the "fleet-custom" endpoint, on the same vault
    key: a key saved on the Connections page must re-ask BOTH lists, or the
    endpoint row keeps the keyless answer (nothing) for another minute."""
    client, d = _app(tmp_path)
    d.platform.config.custom_base_url = "http://box.test:8000/v1"
    d.platform.config.custom_model = "m-default"

    def fake_get_json(url, headers):
        listed = [{"id": "cloud-model"}] if headers.get("Authorization") == "Bearer sk-box" else []
        return {"data": listed}

    monkeypatch.setattr(discovery, "_get_json", fake_get_json)
    assert [r["model"] for r in _fleet_rows(client, "fleet-custom")] == ["m-default"]
    assert client.post("/connections/custom/key", json={"key": "sk-box"}).status_code == 200
    assert [r["model"] for r in _fleet_rows(client, "fleet-custom")] == ["cloud-model"]


# --------------------------------------------------------------------------- #
# 6. The catalog is cached, and every change that matters rebuilds it
# --------------------------------------------------------------------------- #


@pytest.fixture
def counted(tmp_path, monkeypatch):
    client, d = _app(tmp_path)
    builds: list[int] = []
    real = C.build_selectable_models

    def counting(dd, **kw):
        builds.append(1)
        return real(dd, **kw)

    monkeypatch.setattr(C, "build_selectable_models", counting)
    # The Claude catalog refreshes on its own thread when a CLI is signed in;
    # keep it still so it never moves a fingerprint mid-test.
    monkeypatch.setattr(d.platform.providers, "claude_catalog", lambda: {"models": []}, raising=False)
    return client, d, builds


def test_one_build_serves_get_models_and_get_templates(counted):
    client, _d, builds = counted
    first = client.get("/models").json()
    assert client.get("/templates").status_code == 200
    assert client.get("/models").json() == first
    assert len(builds) == 1


def test_a_settings_change_rebuilds_the_catalog(counted):
    client, _d, builds = counted
    client.get("/models")
    r = client.put("/settings", json={"values": {"chat_followups": True}})
    assert r.status_code == 200, r.text
    client.get("/models")
    assert len(builds) == 2


def test_redetect_rebuilds_the_catalog(counted, monkeypatch):
    client, _d, builds = counted
    from iron_jarvis.providers import manager

    client.get("/models")
    # Pin the CLI presence generation so only the route's own invalidation
    # can move the fingerprint.
    monkeypatch.setattr(manager, "invalidate_cli_presence", lambda binary=None: None)
    assert client.post("/providers/rescan").status_code == 200
    client.get("/models")
    assert len(builds) == 2


def test_a_cli_sign_in_and_sign_out_rebuild_the_catalog(counted, monkeypatch):
    client, d, builds = counted
    providers = d.platform.providers
    signed = {"in": False}
    monkeypatch.setattr(providers, "_cli_binary_present", lambda binary: binary == "claude")
    monkeypatch.setattr(providers, "_cli_signed_in", lambda binary: signed["in"])

    def claude_rows():
        return [m for m in client.get("/models").json()["models"] if m["provider"] == "claude-cli"]

    assert claude_rows() and not any(m["available"] for m in claude_rows())
    signed["in"] = True
    assert all(m["available"] for m in claude_rows())
    signed["in"] = False
    assert not any(m["available"] for m in claude_rows())
    assert len(builds) == 3


def test_a_grok_sign_out_is_seen_although_no_curated_row_names_grok(counted, monkeypatch, tmp_path):
    client, _d, _builds = counted
    home = tmp_path / "grok-signed-in"
    home.mkdir()
    (home / "auth.json").write_text(json.dumps({"x::y": {"key": "tok"}}), encoding="utf-8")
    (home / "models_cache.json").write_text(
        json.dumps({"models": {"grok-build": {"info": {"name": "Grok Build"}}}}), encoding="utf-8"
    )
    monkeypatch.setenv("GROK_HOME", str(home))
    monkeypatch.setattr(cli_detect, "_grok_binary", lambda: "C:/grok/grok.exe")

    def grok_available():
        return {m["available"] for m in client.get("/models").json()["models"] if m["provider"] == "grok-cli"}

    assert grok_available() == {True}
    (home / "auth.json").unlink()  # signed out on disk, no route involved
    assert grok_available() == {False}


def test_a_removed_key_never_reads_available_from_the_cache(counted):
    client, d, builds = counted
    assert client.post("/connections/anthropic/key", json={"key": "sk-ant-test"}).status_code == 200

    def anthropic_available():
        rows = [m for m in client.get("/models").json()["models"] if m["provider"] == "anthropic"]
        return {m["available"] for m in rows}

    assert anthropic_available() == {True}
    d.platform.secrets.delete("anthropic_api_key")  # gone WITHOUT any route
    assert anthropic_available() == {False}


def test_a_fleet_add_and_a_new_fleet_child_rebuild_but_a_quiet_sampler_pass_does_not(counted, monkeypatch):
    client, d, builds = counted
    monkeypatch.setattr(discovery, "_get_json", lambda url, headers: {"data": []})
    client.get("/models")
    _add_node(d, "proxy")  # straight into the registry, no route
    client.get("/models")
    assert len(builds) == 2
    child = FleetNode(id="proxy-coder", label="coder", base_url="http://x", source="topology",
                      parent_id="proxy", alias="coder")
    d.fleet.absorb_children("proxy", [child])
    client.get("/models")
    assert len(builds) == 3
    same = FleetNode(**child.model_dump())
    for _ in range(3):  # the sampler hands the same children every pass
        d.fleet.absorb_children("proxy", [same])
        client.get("/models")
    assert len(builds) == 3


def test_callers_that_edit_their_rows_do_not_change_the_cached_catalog(counted):
    _client, d, builds = counted
    first = C.selectable_models(d)
    pristine = json.loads(json.dumps(first))
    first[0]["model"] = "poisoned"
    first[0].setdefault("reasoning", []).append("poisoned")
    first.append({"provider": "x", "model": "y"})
    second = C.selectable_models(d)  # served from the cache this time
    assert second == pristine
    second[0]["model"] = "poisoned too"
    second[0].setdefault("reasoning", []).append("poisoned too")
    second.append({"provider": "x", "model": "z"})
    assert C.selectable_models(d) == pristine
    assert len(builds) == 1


def test_an_expired_catalog_is_served_at_once_while_one_rebuild_runs(counted, monkeypatch):
    _client, d, builds = counted
    clock = _Clock()
    monkeypatch.setattr(C, "time", clock)
    old = C.selectable_models(d)
    started = threading.Event()
    release = threading.Event()
    marker = {"provider": "marker", "model": "rebuilt"}

    def slow(dd, **kw):
        builds.append(1)
        started.set()
        release.wait(5)
        return _REAL_BUILD(dd, **kw) + [dict(marker)]

    monkeypatch.setattr(C, "build_selectable_models", slow)
    clock.now += 61
    assert C.selectable_models(d) == old  # served while the rebuild waits
    assert started.wait(5)
    assert C.selectable_models(d) == old  # one rebuild at a time
    assert len(builds) == 2
    release.set()
    assert _wait_until(lambda: C.selectable_models(d)[-1] == marker)
    assert len(builds) == 2


def test_get_models_answers_304_to_the_etag_it_sent(counted):
    client, _d, _builds = counted
    first = client.get("/models")
    tag = first.headers.get("etag")
    assert first.status_code == 200 and tag and tag.startswith('W/"')
    again = client.get("/models", headers={"If-None-Match": tag})
    assert again.status_code == 304 and again.content == b""
    assert client.get("/models", headers={"If-None-Match": 'W/"other"'}).status_code == 200


def test_the_catalog_rows_are_the_rows_the_builder_makes(counted):
    """The cache changes WHEN rows are built, never WHAT they are."""
    client, d, _builds = counted
    assert client.get("/models").json()["models"] == C.build_selectable_models(d)


def test_the_cached_catalog_lives_on_the_platform_so_the_sidebar_shares_it(counted):
    _client, d, builds = counted
    from types import SimpleNamespace

    C.selectable_models(d)
    sidebar_deps = SimpleNamespace(platform=d.platform, fleet=getattr(d.platform, "fleet", None))
    C.selectable_models(sidebar_deps)
    assert len(builds) == 1


def test_the_shared_tls_context_is_built_once_and_handed_to_every_client(monkeypatch):
    from iron_jarvis.core import http as core_http

    ctx = core_http.ssl_context()
    assert core_http.ssl_context() is ctx
    seen: list[dict] = []
    monkeypatch.setattr(httpx, "Client", lambda **kw: seen.append(kw) or kw)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: seen.append(kw) or kw)
    core_http.client(timeout=1)
    core_http.async_client(timeout=2)
    core_http.client(verify=False)
    assert [k.get("verify") for k in seen] == [ctx, ctx, False]


def test_a_dead_endpoint_gets_a_short_connect_limit_not_the_whole_budget(monkeypatch):
    """A refused local port waits out the whole timeout on Windows, so the
    listing caps the CONNECT phase at 1.5 s while the answer keeps 4 s."""
    from iron_jarvis.core import http as core_http

    seen = []

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": []}

    def fake_get(url, headers=None, timeout=None, **kw):
        seen.append(timeout)
        return _Resp()

    monkeypatch.setattr(core_http, "get", fake_get)
    _REAL_GET_JSON("http://node.test/v1/models", {})
    assert len(seen) == 1
    t = seen[0]
    assert isinstance(t, httpx.Timeout)
    assert t.connect == 1.5
    assert t.read == 4.0
