"""v1.344.0 ("Jarvis looks after itself") integration pins: the seams BETWEEN
the five lanes, driven on the real app (``create_app``).

* The five self-care tools sit in the real registry with object schemas and
  the contract's tiers: the readers are allow tier (``core/config.py``'s
  defaults -- a headless run may check on itself), the repairs ask, and
  ONLY the repairs are in ``LOW_TRUST_DENY``.
* A failure sentence through ``POST /chat`` (the real selector, the real
  registry filter) arms the readers and never a repair; a repair imperative
  ask-arms the repair through ``select_ask_tools``.
* The three session doors the page posts ``prior_tool_results`` to
  (``POST /sessions``, ``/sessions/{id}/continue``, ``/agents/{slug}/spawn``)
  store it on the row's options -- the one line each handler needed so the
  validated body does not fall on the floor (lane P's "needs").
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import PermissionMode, Session
from iron_jarvis.core.trust import LOW_TRUST_DENY
from iron_jarvis.daemon.app import create_app
from iron_jarvis.tools.autoselect import select_ask_tools
from iron_jarvis.tools.selfcare import SELFCARE_READERS, SELFCARE_REPAIRS, SELFCARE_TOOLS

ROW_A = {"tool": "app_health", "args_summary": "{}", "ok": True, "output_head": "Packs: 1 failed."}
ROW_B = {"tool": "pack_status", "args_summary": '{"name": "github"}', "ok": False, "output_head": "did not start"}

AGENT = {
    "name": "health-reader",
    "system_prompt": "look after the app",
    "tools": ["read_file"],
    "description": "reads the app's health",
    "provider": "mock",
    "model": "mock-1",
    "base_type": "researcher",
}


def _options(client: TestClient, sid: str) -> dict:
    platform = client.app.state.platform
    with session_scope(platform.engine) as db:
        row = db.get(Session, sid)
        assert row is not None, sid
        return json.loads(row.options_json or "{}")


def test_the_five_tools_sit_in_the_real_registry_with_the_contracts_tiers(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        platform = client.app.state.platform
        for name in SELFCARE_TOOLS:
            tool = platform.registry.get(name)
            assert tool is not None, f"{name} is not registered"
            assert tool.spec()["input_schema"]["type"] == "object", name
        engine = platform.permissions
        for name in SELFCARE_READERS:
            assert engine.mode_for(name) is PermissionMode.ALLOW, name
            assert name not in LOW_TRUST_DENY, name
        for name in SELFCARE_REPAIRS:
            assert engine.mode_for(name) is PermissionMode.ASK, name
            assert name in LOW_TRUST_DENY, name


def test_a_failure_sentence_arms_the_readers_through_post_chat_and_never_a_repair(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        r = client.post(
            "/chat",
            json={
                "messages": [{"role": "user", "content": "something is broken, why did my pack fail?"}],
                "auto_tools": True,
            },
        )
        assert r.status_code == 200, r.text
        armed = set(r.json()["auto_armed"])
        assert "app_health" in armed, armed
        assert armed & set(SELFCARE_READERS), armed
        assert not (armed & set(SELFCARE_REPAIRS)), armed
        # a repair imperative is visible-but-carded (the stream lane's ask set)
        assert "pack_restart" in select_ask_tools("restart the github pack")
        assert "provider_rescan" in select_ask_tools("rescan providers")
        assert not (set(select_ask_tools("why did my pack fail?")) & set(SELFCARE_REPAIRS))


def test_post_sessions_and_continue_store_prior_tool_results_on_the_row(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        r = client.post("/sessions", json={"task": "say hi", "wait": True, "prior_tool_results": [ROW_A]})
        assert r.status_code == 200, r.text
        parent = r.json()["id"]
        assert _options(client, parent)["prior_tool_results"] == [ROW_A]
        # nothing carried = no key (the body is byte-identical to before)
        r = client.post("/sessions", json={"task": "say hi", "wait": True})
        assert r.status_code == 200, r.text
        assert "prior_tool_results" not in _options(client, r.json()["id"])
        # a continuation carries ITS turn's list, never the parent's
        r = client.post(
            f"/sessions/{parent}/continue",
            json={"message": "and again", "wait": True, "prior_tool_results": [ROW_B]},
        )
        assert r.status_code == 200, r.text
        child = r.json()["id"]
        assert child != parent
        assert _options(client, child)["prior_tool_results"] == [ROW_B]
        r = client.post(f"/sessions/{child}/continue", json={"message": "once more", "wait": True})
        assert r.status_code == 200, r.text
        assert "prior_tool_results" not in _options(client, r.json()["id"])


def test_the_custom_agent_spawn_door_stores_prior_tool_results_on_the_row(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        r = client.post("/agents", json=AGENT)
        assert r.status_code == 200, r.text
        r = client.post(
            f"/agents/{AGENT['name']}/spawn",
            json={"task": "check the app", "wait": True, "prior_tool_results": [ROW_A, ROW_B]},
        )
        assert r.status_code == 200, r.text
        assert _options(client, r.json()["id"])["prior_tool_results"] == [ROW_A, ROW_B]
        r = client.post(f"/agents/{AGENT['name']}/spawn", json={"task": "check the app", "wait": True})
        assert r.status_code == 200, r.text
        assert "prior_tool_results" not in _options(client, r.json()["id"])
