"""Cross-feature integration — proves the robust feature set is wired & working.

Exercises the WIRED platform end-to-end: shared secrets, integrations,
communication, webhooks, file search, scheduler, long-term memory, and dynamic
agents — all offline.
"""

from __future__ import annotations

from iron_jarvis.platform import build_platform


async def test_secrets_roundtrip_and_listing_never_leaks(tmp_path):
    p = build_platform(str(tmp_path))
    p.secrets.set("anthropic_api_key", "sk-secret-123", kind="api_key")
    assert p.secrets.get("anthropic_api_key") == "sk-secret-123"  # server-side only
    listed = p.secrets.list()
    assert any(s["name"] == "anthropic_api_key" for s in listed)
    assert all("value" not in s for s in listed)  # never exposes values


async def test_integration_framework_register_and_test(tmp_path):
    p = build_platform(str(tmp_path))
    ids = {i["id"] for i in p.integrations.list_status()}
    assert "mock" in ids and "rest_api" in ids
    p.integrations.enable("mock", True)
    p.integrations.configure("mock", {})
    assert p.integrations.test("mock", p.secrets.get)["ok"] is True


async def test_communication_notifier_sends(tmp_path):
    p = build_platform(str(tmp_path))
    assert p.notifier.channels()  # at least the offline mock channel
    result = p.notifier.notify("deploy finished")
    assert any(v.get("ok") for v in result.values())


async def test_webhooks_inbound_dispatch(tmp_path):
    p = build_platform(str(tmp_path))
    p.inbound_webhooks.register("ci", lambda body: {"received": body.get("x")})
    res = await p.inbound_webhooks.dispatch("ci", {"x": 42})
    assert res.get("received") == 42


async def test_filesearch_finds_seeded_file(tmp_path):
    (tmp_path / "note.md").write_text("arc reactor cyan accent", encoding="utf-8")
    p = build_platform(str(tmp_path))
    hits = p.filesearch.search("arc reactor", mode="content")
    assert any("note.md" in h["path"] for h in hits)


async def test_scheduler_runs_a_workflow(tmp_path):
    p = build_platform(str(tmp_path))
    p.scheduler.add_task(
        "nightly",
        "0 0 * * *",
        kind="workflow",
        payload={"name": "wf", "steps": [{"name": "s", "agent": "builder", "task": "make a file"}]},
    )
    assert any(t.name == "nightly" for t in p.scheduler.list())
    await p.scheduler.run_now("nightly")  # runs the workflow via the run_callback
    assert p.scheduler.get("nightly").last_run is not None


async def test_long_term_memory_append_and_search(tmp_path):
    p = build_platform(str(tmp_path))
    assert p.ltm.default_source() == "brain"
    p.ltm.append("Q3 Plan", "hire two engineers and ship the dashboard", source="brain")
    hits = p.ltm.search("hire engineers")
    assert hits and any(
        "q3" in h["title"].lower() or "engineer" in h["snippet"].lower() for h in hits
    )


async def test_dynamic_agent_create_and_register(tmp_path):
    p = build_platform(str(tmp_path))
    p.agents_registry.register(
        "analyst", "You analyze data.", ["read_file", "file_search", "ltm_search"]
    )
    assert any(r.name == "analyst" for r in p.agents_registry.list())
    # a fresh registry recovers it from persistence
    from iron_jarvis.agents.dynamic import DynamicAgentRegistry

    assert DynamicAgentRegistry(p.engine).load().get("analyst") is not None


def test_all_new_feature_tools_registered(tmp_path):
    p = build_platform(str(tmp_path))
    need = {
        "secret_list", "secret_set", "integration_list", "integration_test",
        "notify", "file_search", "ltm_search", "ltm_append",
        "list_agents", "create_agent", "spawn_agent",
    }
    assert need <= set(p.registry.names())
