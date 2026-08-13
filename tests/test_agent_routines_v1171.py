"""v1.171.0 P3 — per-agent routines + the identity anchor.

Contract 3: a task schedule may name WHO does the work (``agent_type`` in the
payload) — validated at ADD time against builtin agent types + existing
dynamic agents, decoded onto every GET /schedules row, and resolved at FIRE
time exactly the way POST /agents/{name}/spawn resolves (dynamic record
first, then builtin; absent = builder, byte-for-byte today's behaviour).

Contract 4: a dynamic agent's COMPOSED system prompt begins with the identity
anchor sentence; the stored record and every builtin definition are untouched.

What is guarded, each with a silent failure mode:
  - ADD-time validation refuses unknown/non-string names with a 422 — a typo'd
    agent otherwise fails at 3am, or worse, silently runs as builder;
  - the decoded row value is the EXACT payload string; absent/garbage is "",
    never a coerced or invented name;
  - a fire that names a builtin runs the session AS that type (a dropped
    agent_type runs builder and nobody notices — the row still stamps ok);
  - a fire that names a dynamic agent hands its REAL definition (anchored
    prompt, base type, pinned provider/model) to run_session — losing the
    definition runs a bare builder with the same green outcome;
  - a dynamic agent deleted after scheduling fails the fire HONESTLY with a
    recorded error naming the agent — never a silent builder run;
  - the anchor is applied at COMPOSITION time only: stored records keep
    exactly what the user typed, repeated composition never stacks anchors,
    and builtin definitions stay byte-identical.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents.dynamic import identity_anchor
from iron_jarvis.agents.types import _DEFINITIONS, get_agent_definition
from iron_jarvis.core.models import AgentType
from iron_jarvis.daemon.app import create_app


@pytest.fixture()
def client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def _platform(client):
    return client.app.state.platform


def _add(client, name: str, payload: dict, kind: str = "task"):
    return client.post(
        "/schedules",
        json={"name": name, "cron": "0 9 * * *", "kind": kind, "payload": payload},
    )


def _row(client, name: str) -> dict:
    rows = client.get("/schedules").json()["schedules"]
    return next(t for t in rows if t["name"] == name)


def _session(client, session_id: str) -> dict:
    # GET /sessions/{id} returns {session, transcript} — NESTED (CLAUDE.md).
    r = client.get(f"/sessions/{session_id}")
    assert r.status_code == 200, r.text
    return r.json()["session"]


# --------------------------------------------------------------- ADD-time


def test_add_accepts_builtin_agent_type(client):
    r = _add(client, "daily-research", {"task": "Dig.", "agent_type": "researcher"})
    assert r.status_code == 200, r.text


def test_add_accepts_existing_dynamic_agent(client):
    _platform(client).agents_registry.register("remy", "Be Remy.", ["read_file"])
    r = _add(client, "remy-routine", {"task": "Do the rounds.", "agent_type": "remy"})
    assert r.status_code == 200, r.text


def test_add_refuses_unknown_agent_with_422_naming_it(client):
    r = _add(client, "ghost-routine", {"task": "x", "agent_type": "ghost"})
    assert r.status_code == 422
    assert "ghost" in r.json()["detail"]


def test_add_refuses_non_string_agent_type(client):
    # Refused, never coerced — str(123) could phantom-match a dynamic agent
    # literally named "123".
    r = _add(client, "numeric-agent", {"task": "x", "agent_type": 123})
    assert r.status_code == 422


def test_add_refuses_whitespace_only_agent_type(client):
    r = _add(client, "blank-agent", {"task": "x", "agent_type": "   "})
    assert r.status_code == 422


def test_add_without_agent_type_still_works(client):
    # The pre-v1.171 payload shape is untouched — absent = builder.
    r = _add(client, "plain", {"task": "As before."})
    assert r.status_code == 200, r.text


def test_add_validation_is_task_kind_only(client):
    # Only the task lane consumes agent_type; a workflow payload carrying one
    # is not validated (decode still exposes it honestly).
    r = _add(
        client,
        "wf-with-agent",
        {"workflow": "some-flow", "agent_type": "ghost"},
        kind="workflow",
    )
    assert r.status_code == 200, r.text


# ------------------------------------------------------------ GET decode


def test_list_exposes_decoded_agent_type(client):
    _add(client, "typed", {"task": "x", "agent_type": "researcher"})
    assert _row(client, "typed")["agent_type"] == "researcher"


def test_agent_type_empty_when_absent(client):
    _add(client, "untyped", {"task": "x"})
    assert _row(client, "untyped")["agent_type"] == ""


def test_non_string_agent_type_decodes_empty_never_coerced(client):
    # Bypass ADD validation the way a corrupt/legacy row would look: write the
    # payload directly through the scheduler. The decode must refuse, not
    # coerce, and the list must survive.
    _platform(client).scheduler.add_task(
        "legacy", "0 9 * * *", kind="task", payload={"task": "x", "agent_type": 123}
    )
    r = client.get("/schedules")
    assert r.status_code == 200, r.text
    row = next(t for t in r.json()["schedules"] if t["name"] == "legacy")
    assert row["agent_type"] == ""
    assert row["agent_type"] != "123"


def test_agent_type_and_project_id_coexist(client):
    _add(client, "both", {"task": "x", "agent_type": "reviewer", "project_id": "proj_z"})
    row = _row(client, "both")
    assert row["agent_type"] == "reviewer"
    assert row["project_id"] == "proj_z"


# ------------------------------------------------------------- FIRE time


def test_fire_without_agent_type_runs_builder_exactly_today(client):
    _add(client, "default-fire", {"task": "Do the usual."})
    ran = client.post("/schedules/default-fire/run").json()
    assert ran["last_status"] == "ok", ran
    assert _session(client, ran["last_session_id"])["agent_type"] == "builder"


def test_fire_with_builtin_runs_that_agent_type(client):
    _add(client, "research-fire", {"task": "Dig deep.", "agent_type": "researcher"})
    ran = client.post("/schedules/research-fire/run").json()
    assert ran["last_status"] == "ok", ran
    assert _session(client, ran["last_session_id"])["agent_type"] == "researcher"


def test_fire_with_dynamic_agent_uses_its_definition(client, monkeypatch):
    platform = _platform(client)
    platform.agents_registry.register(
        "remy",
        "Handle the morning rounds.",
        ["read_file"],
        provider="mock",
        model="mock-1",
    )
    _add(client, "remy-rounds", {"task": "Morning rounds.", "agent_type": "remy"})

    orch = platform.orchestrator
    calls: dict = {}
    real_create = orch.create_session
    real_run = orch.run_session

    async def create_spy(task_text, *args, **kwargs):
        calls["create_args"] = args
        calls["create_kwargs"] = kwargs
        return await real_create(task_text, *args, **kwargs)

    async def run_spy(session_id, definition=None):
        calls["definition"] = definition
        return await real_run(session_id, definition=definition)

    monkeypatch.setattr(orch, "create_session", create_spy)
    monkeypatch.setattr(orch, "run_session", run_spy)

    ran = client.post("/schedules/remy-rounds/run").json()
    assert ran["last_status"] == "ok", ran
    # The REAL definition reached run_session (a lost definition runs a bare
    # builder with the same green row — the exact silent degradation this
    # wave forbids)…
    definition = calls.get("definition")
    assert definition is not None
    assert definition.system_prompt.startswith(
        "You are remy, a persistent named agent on this machine."
    )
    assert "Handle the morning rounds." in definition.system_prompt
    # …the session runs under the record's BASE type…
    assert _session(client, ran["last_session_id"])["agent_type"] == "builder"
    # …and the record's pinned provider/model flowed through (spawn parity).
    assert calls["create_kwargs"].get("provider") == "mock"
    assert calls["create_kwargs"].get("model") == "mock-1"


def test_fire_with_deleted_dynamic_agent_fails_honestly(client):
    platform = _platform(client)
    platform.agents_registry.register("temp", "Short-lived.", [])
    _add(client, "orphaned", {"task": "Run as temp.", "agent_type": "temp"})
    platform.agents_registry.remove("temp")

    ran = client.post("/schedules/orphaned/run").json()
    assert ran["last_status"] == "error", ran
    assert "temp" in ran["last_detail"]
    # NO session ran — silent degradation to builder would leave one behind.
    assert ran["last_session_id"] == ""
    assert client.get("/sessions").json()["sessions"] == []


def test_fire_without_definition_keeps_the_legacy_run_session_signature(client, monkeypatch):
    # The absent-agent path (and the builtin path — both resolve NO dynamic
    # definition) must call ``run_session(session_id)`` with NO ``definition``
    # kwarg: pre-v1.171 callers/stubs accept only ``session_id``, and an
    # unconditional ``definition=None`` turns every such fire into a TypeError
    # recorded as last_status='error' (the exact tests/test_steward_schedule.py
    # fallout this pins against).
    _add(client, "legacy-plain", {"task": "As before."})
    _add(client, "legacy-builtin", {"task": "Dig.", "agent_type": "researcher"})
    orch = _platform(client).orchestrator
    real_run = orch.run_session

    async def run_legacy(session_id):  # pre-v1.171 signature — no definition
        return await real_run(session_id)

    monkeypatch.setattr(orch, "run_session", run_legacy)
    for name in ("legacy-plain", "legacy-builtin"):
        ran = client.post(f"/schedules/{name}/run").json()
        assert ran["last_status"] == "ok", (name, ran)


def test_fire_treats_non_string_agent_type_as_absent_matching_the_decode(
    client, monkeypatch
):
    # A legacy/corrupt row carrying agent_type=123 (insertable below ADD
    # validation, exactly like the decode test above) must fire the way the
    # list DISPLAYS it — as builder — never str()-coerced to "123", which
    # would phantom-match a dynamic agent literally named "123" or error
    # "scheduled agent '123' no longer exists" while the row shows builder.
    platform = _platform(client)
    platform.agents_registry.register("123", "I am the number.", [])
    platform.scheduler.add_task(
        "legacy-num", "0 9 * * *", kind="task", payload={"task": "x", "agent_type": 123}
    )
    orch = platform.orchestrator
    calls: dict = {}
    real_run = orch.run_session

    async def run_spy(session_id, definition=None):
        calls["definition"] = definition
        calls["called"] = True
        return await real_run(session_id, definition=definition)

    monkeypatch.setattr(orch, "run_session", run_spy)
    ran = client.post("/schedules/legacy-num/run").json()
    assert ran["last_status"] == "ok", ran
    assert calls.get("called") is True
    # NOT the "123" dynamic record — the non-string was treated as absent.
    assert calls.get("definition") is None
    assert _session(client, ran["last_session_id"])["agent_type"] == "builder"


def test_fire_refuses_supervisor_based_dynamic_agent(client):
    # Mirrors the spawn route's 409: the builtin supervisor path would
    # silently discard the record's custom prompt.
    platform = _platform(client)
    platform.agents_registry.register("boss", "Coordinate.", [], base_type="supervisor")
    _add(client, "boss-routine", {"task": "Coordinate stuff.", "agent_type": "boss"})
    ran = client.post("/schedules/boss-routine/run").json()
    assert ran["last_status"] == "error", ran
    assert "supervisor" in ran["last_detail"]
    assert ran["last_session_id"] == ""


# ------------------------------------------------------ identity anchor


def test_dynamic_definition_begins_with_the_exact_anchor_sentence(client):
    registry = _platform(client).agents_registry
    registry.register("remy", "Handle the rounds.", ["read_file"])
    definition = registry.definition("remy")
    # Pinned by VALUE (contract 4) — a reworded anchor is a different product.
    assert definition.system_prompt == (
        "You are remy, a persistent named agent on this machine.\n\n"
        "Handle the rounds."
    )
    assert identity_anchor("remy") == (
        "You are remy, a persistent named agent on this machine."
    )


def test_anchor_never_reaches_the_stored_record(client):
    registry = _platform(client).agents_registry
    registry.register("remy", "Handle the rounds.", [])
    registry.definition("remy")  # composition must not write back
    assert registry.get("remy").system_prompt == "Handle the rounds."


def test_repeated_composition_never_stacks_anchors(client):
    registry = _platform(client).agents_registry
    registry.register("remy", "Handle the rounds.", [])
    first = registry.definition("remy").system_prompt
    second = registry.definition("remy").system_prompt
    assert first == second
    assert first.count("persistent named agent on this machine") == 1


def test_empty_stored_prompt_composes_to_the_anchor_alone(client):
    registry = _platform(client).agents_registry
    registry.register("blank", "", [])
    assert registry.definition("blank").system_prompt == identity_anchor("blank")


def test_builtin_definitions_stay_byte_unchanged(client):
    before = {t: get_agent_definition(t).system_prompt for t in _DEFINITIONS}
    registry = _platform(client).agents_registry
    registry.register("remy", "Handle the rounds.", [])
    registry.definition("remy")
    after = {t: get_agent_definition(t).system_prompt for t in _DEFINITIONS}
    assert after == before
    for agent_type, prompt in after.items():
        assert "persistent named agent" not in prompt, agent_type.value
    # And the fire-time default is a builtin type, so this covers it too.
    assert AgentType.BUILDER in after
