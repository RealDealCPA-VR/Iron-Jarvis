"""A custom agent is an EMPLOYEE: a job card, a monthly allowance, a day off
(v1.295.0, wave 1 — the HTTP doors in ``daemon/routes/agents.py``).

Drives the REAL app factory (``create_app``) over a ``TestClient`` — every
assertion below is about what a user of the Agents page can reach, not what a
library function returns:

* ``POST /agents`` takes the whole job card and ``GET /agents`` echoes it,
  with ``allowance.status == "unlimited"`` for zero bounds;
* ``PATCH /agents/{name}`` is PARTIAL — one field changes, every other keeps
  (the old route re-passed the record's provider/model, so a model change was
  silently a no-op);
* every refusal is a plain-words 422 (supervisor base, ``yolo``, a step
  budget outside the session bounds, a negative allowance, a manager that is
  the agent itself or does not exist, a blank skill);
* ``POST /agents/{name}/pause`` gives a day off: the row and the roster say so
  and ``POST /agents/{name}/spawn`` answers 409 WITH THE REASON before any
  session row exists; ``/resume`` lets the next spawn run (offline MockLLM);
* an agent over its monthly allowance (seeded ``Session`` rows stamped with
  its roster name) is refused the same way, naming the allowance;
* the roster rows carry ``paused`` / ``pause_reason`` / ``allowance`` /
  ``reports_to``, and an OLDER entry without those attributes still
  serializes (the rail must never lose an agent to a missing field).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Session, SessionStatus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import agents as agents_routes

NAME = "tax-reader"
ROSTER = f"custom:{NAME}"

#: Every field of the job card, with a duplicate skill and a padded deny tool
#: so the echo test proves the dedupe/strip.
FULL = {
    "name": NAME,
    "system_prompt": "read tax documents",
    "tools": ["read_file"],
    "description": "reads client tax documents",
    "provider": "mock",
    "model": "mock-1",
    "base_type": "researcher",
    "approval_mode": "always_ask",
    "max_steps": 7,
    "allowance_tokens": 0,
    "allowance_usd": 0.0,
    "reports_to": "builder",
    "skills": ["tax", "ocr", "tax"],
    "deny_tools": ["shell", " shell "],
}

#: The keys GET /agents showed BEFORE v1.295.0 — all must survive.
LEGACY_KEYS = {
    "name", "description", "provider", "model", "system_prompt", "tools",
    "effective_tools", "avatar", "face",
}
#: The job-card keys added by v1.295.0.
EMPLOYEE_KEYS = {
    "base_type", "approval_mode", "max_steps", "reports_to", "skills",
    "deny_tools", "paused", "allowance",
}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(str(tmp_path))) as c:
        yield c


def _hire(client, **over):
    body = {**FULL, **over}
    r = client.post("/agents", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _row(client, name=NAME):
    rows = {r["name"]: r for r in client.get("/agents").json()["dynamic"]}
    assert name in rows, f"{name} missing from GET /agents: {sorted(rows)}"
    return rows[name]


def _roster_entry(client, name=ROSTER):
    r = client.get("/agents/roster")
    assert r.status_code == 200, r.text
    rows = {e["name"]: e for e in r.json()["roster"]}
    assert name in rows, f"{name} missing from the roster: {sorted(rows)}"
    return rows[name]


def _sessions_for(client, roster_name=ROSTER) -> list[Session]:
    platform = client.app.state.platform
    with session_scope(platform.engine) as db:
        return list(db.exec(select(Session).where(Session.agent_name == roster_name)))


# --------------------------------------------------------------------------- #
# The job card: create echoes everything
# --------------------------------------------------------------------------- #
def test_hiring_with_every_field_shows_the_whole_job_card(client):
    created = _hire(client)
    assert created == {
        "name": NAME, "provider": "mock", "model": "mock-1", "base_type": "researcher"
    }
    row = _row(client)
    assert LEGACY_KEYS <= set(row), sorted(LEGACY_KEYS - set(row))
    assert EMPLOYEE_KEYS <= set(row), sorted(EMPLOYEE_KEYS - set(row))
    assert row["base_type"] == "researcher"
    assert row["approval_mode"] == "always_ask"
    assert row["max_steps"] == 7
    assert row["reports_to"] == "builder"
    assert row["skills"] == ["tax", "ocr"], "skills are deduped, order kept"
    assert row["deny_tools"] == ["shell"], "deny_tools are stripped and deduped"
    assert row["paused"] is None
    assert row["tools"] == ["read_file"] and row["description"] == FULL["description"]
    allowance = row["allowance"]
    assert allowance["status"] == "unlimited"
    assert allowance["pct"] is None
    assert allowance["tokens"] == 0 and allowance["usd"] == 0.0
    assert allowance["spent_tokens"] == 0 and allowance["runs"] == 0
    assert set(allowance) == {
        "month", "tokens", "usd", "spent_tokens", "spent_usd", "runs", "pct",
        "left_tokens", "left_usd", "status",
    }


def test_a_bounded_allowance_reads_ok_with_nothing_spent(client):
    _hire(client, allowance_tokens=1000, allowance_usd=5.0)
    allowance = _row(client)["allowance"]
    assert allowance["status"] == "ok"
    assert allowance["pct"] == 0
    assert allowance["left_tokens"] == 1000 and allowance["left_usd"] == 5.0


# --------------------------------------------------------------------------- #
# PATCH is partial: one field changes, the rest keep
# --------------------------------------------------------------------------- #
def _job_card(row: dict) -> dict:
    return {k: row[k] for k in (
        "system_prompt", "tools", "description", "provider", "model", "base_type",
        "approval_mode", "max_steps", "reports_to", "skills", "deny_tools",
    )}


def test_patching_the_model_changes_the_model_and_nothing_else(client):
    _hire(client)
    before = _job_card(_row(client))
    r = client.patch(f"/agents/{NAME}", json={"model": "mock-2"})
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "mock-2", "the PATCH answers with the updated row"
    assert EMPLOYEE_KEYS <= set(r.json()), "the PATCH answers with the GET row shape"
    after = _job_card(_row(client))
    assert after["model"] == "mock-2"
    assert after["provider"] == "mock", "provider was not in the body — it keeps"
    assert {k: v for k, v in after.items() if k != "model"} == {
        k: v for k, v in before.items() if k != "model"
    }


def test_patching_the_provider_and_base_type_each_change_only_themselves(client):
    _hire(client)
    before = _job_card(_row(client))
    assert client.patch(f"/agents/{NAME}", json={"provider": "openai"}).status_code == 200
    after = _job_card(_row(client))
    assert after["provider"] == "openai"
    assert after["model"] == "mock-1", "model was not in the body — it keeps"
    assert {k: v for k, v in after.items() if k != "provider"} == {
        k: v for k, v in before.items() if k != "provider"
    }
    assert client.patch(f"/agents/{NAME}", json={"base_type": "builder"}).status_code == 200
    after2 = _job_card(_row(client))
    assert after2["base_type"] == "builder"
    assert {k: v for k, v in after2.items() if k != "base_type"} == {
        k: v for k, v in after.items() if k != "base_type"
    }


def test_patching_one_job_card_field_keeps_the_other_job_card_fields(client):
    _hire(client)
    before = _job_card(_row(client))
    r = client.patch(f"/agents/{NAME}", json={"allowance_tokens": 500})
    assert r.status_code == 200, r.text
    row = _row(client)
    assert row["allowance"]["tokens"] == 500 and row["allowance"]["status"] == "ok"
    assert _job_card(row) == before, "skills/deny_tools/reports_to/… untouched"
    r = client.patch(f"/agents/{NAME}", json={"skills": ["payroll"]})
    assert r.status_code == 200, r.text
    row = _row(client)
    assert row["skills"] == ["payroll"]
    assert row["deny_tools"] == ["shell"] and row["reports_to"] == "builder"
    assert row["allowance"]["tokens"] == 500, "an earlier PATCH's allowance keeps"


def test_patching_an_unknown_agent_is_404(client):
    assert client.patch("/agents/nobody", json={"model": "x"}).status_code == 404


def test_clear_max_steps_puts_the_budget_back_to_the_default(client):
    _hire(client, max_steps=7)
    assert _row(client)["max_steps"] == 7
    # `max_steps: null` is KEEP on a PATCH — the budget must survive it.
    assert client.patch(f"/agents/{NAME}", json={"max_steps": None}).status_code == 200
    assert _row(client)["max_steps"] == 7
    r = client.patch(f"/agents/{NAME}", json={"clear_max_steps": True})
    assert r.status_code == 200, r.text
    assert r.json()["max_steps"] is None
    assert _row(client)["max_steps"] is None
    assert _row(client)["approval_mode"] == "always_ask", "the rest of the card keeps"
    # Clearing an already-default budget is a harmless no-op, not an error.
    assert client.patch(f"/agents/{NAME}", json={"clear_max_steps": True}).status_code == 200


def test_clear_max_steps_with_a_value_is_refused_in_one_sentence(client):
    _hire(client, max_steps=7)
    r = client.patch(f"/agents/{NAME}", json={"clear_max_steps": True, "max_steps": 9})
    assert r.status_code == 422, r.text
    assert isinstance(r.json()["detail"], str)
    assert "clear_max_steps" in r.json()["detail"] and "max_steps=9" in r.json()["detail"]
    assert _row(client)["max_steps"] == 7, "a refused edit changes nothing"


def test_hiring_an_existing_name_is_409_and_keeps_its_job_card(client):
    _hire(client, allowance_tokens=1000, skills=["tax"])
    before = _job_card(_row(client))
    r = client.post(
        "/agents",
        json={"name": NAME, "system_prompt": "something else", "description": "new"},
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == (
        f"{NAME} already exists — edit it on its row (PATCH) instead of re-creating it"
    )
    row = _row(client)
    assert _job_card(row) == before, "the re-create must not touch the job card"
    assert row["allowance"]["tokens"] == 1000 and row["skills"] == ["tax"]
    assert len(client.get("/agents").json()["dynamic"]) == 1


# --------------------------------------------------------------------------- #
# Plain-words 422s — never silent coercion
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "field, value, words",
    [
        ("base_type", "supervisor", ("supervisor", "discard")),
        ("base_type", "wizard", ("wizard", "builtin")),
        ("approval_mode", "yolo", ("yolo", "blast radius", "approve_for_me")),
        ("approval_mode", "sometimes", ("sometimes", "always_ask")),
        ("max_steps", 0, ("between 1 and 200",)),
        ("max_steps", 201, ("between 1 and 200",)),
        ("max_steps", 2.5, ("whole number",)),
        ("max_steps", True, ("boolean",)),
        ("allowance_tokens", -1, ("allowance_tokens", "0 (unlimited)")),
        ("allowance_usd", -0.5, ("allowance_usd", "0 (unlimited)")),
        ("reports_to", ROSTER, ("itself",)),
        ("reports_to", NAME, ("itself",)),
        ("reports_to", "custom:nobody", ("does not exist",)),
        ("reports_to", "ceo", ("ceo", "builtin")),
        ("skills", ["tax", ""], ("skills", "blank")),
        ("deny_tools", ["   "], ("deny_tools", "blank")),
    ],
)
def test_a_bad_job_card_is_refused_in_plain_words_and_nothing_is_hired(
    client, field, value, words
):
    r = client.post("/agents", json={**FULL, field: value})
    assert r.status_code == 422, r.text
    for word in words:
        assert word in r.text, (word, r.text)
    assert NAME not in {x["name"] for x in client.get("/agents").json()["dynamic"]}, (
        "a refused hire must leave no row behind"
    )


def test_the_same_refusals_apply_on_patch(client):
    _hire(client)
    before = _job_card(_row(client))
    for body, word in (
        ({"base_type": "supervisor"}, "supervisor"),
        ({"approval_mode": "yolo"}, "yolo"),
        ({"max_steps": 500}, "between 1 and 200"),
        ({"allowance_usd": -1}, "0 (unlimited)"),
        ({"reports_to": ROSTER}, "itself"),
        ({"reports_to": "custom:ghost"}, "does not exist"),
        ({"deny_tools": [""]}, "blank"),
    ):
        r = client.patch(f"/agents/{NAME}", json=body)
        assert r.status_code == 422, (body, r.text)
        assert word in r.text, (body, r.text)
    assert _job_card(_row(client)) == before, "a refused edit changes nothing"


def test_reports_to_an_existing_custom_agent_is_accepted(client):
    _hire(client, name="lead", reports_to="")
    _hire(client, reports_to="custom:lead")
    assert _row(client)["reports_to"] == "custom:lead"
    # ...and clearing it (= reports to the user) is a plain PATCH.
    assert client.patch(f"/agents/{NAME}", json={"reports_to": ""}).status_code == 200
    assert _row(client)["reports_to"] == ""


# --------------------------------------------------------------------------- #
# The day off: pause / resume / spawn
# --------------------------------------------------------------------------- #
def test_pause_refuses_the_spawn_with_the_reason_and_resume_lets_it_run(client):
    # provider/model blank → the platform default (offline MockLLM) runs the spawn.
    _hire(client, provider="", model="")
    r = client.post(f"/agents/{NAME}/pause", json={"reason": "on holiday until Monday"})
    assert r.status_code == 200, r.text
    assert r.json()["paused"]["reason"] == "on holiday until Monday"
    assert r.json()["paused"]["at"], "the pause is timestamped"
    assert EMPLOYEE_KEYS <= set(r.json()), "pause answers with the GET row shape"
    row = _row(client)
    assert row["paused"]["reason"] == "on holiday until Monday"
    assert row["paused"]["at"] == r.json()["paused"]["at"]

    entry = _roster_entry(client)
    assert entry["paused"] is True
    assert entry["pause_reason"] == "on holiday until Monday"
    assert entry["healthy"] is False, "a paused agent is not offered work"

    r = client.post(f"/agents/{NAME}/spawn", json={"task": "read this K-1", "wait": True})
    assert r.status_code == 409, r.text
    assert "on holiday until Monday" in r.json()["detail"]
    assert NAME in r.json()["detail"]
    assert _sessions_for(client) == [], "a refused spawn creates no session row"

    r = client.post(f"/agents/{NAME}/resume")
    assert r.status_code == 200, r.text
    assert r.json()["paused"] is None
    assert _row(client)["paused"] is None
    assert _roster_entry(client)["paused"] is False
    assert _roster_entry(client)["healthy"] is True

    r = client.post(f"/agents/{NAME}/spawn", json={"task": "read this K-1", "wait": True})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "completed"
    assert len(_sessions_for(client)) == 1


def test_a_pause_with_no_reason_still_has_one(client):
    _hire(client)
    r = client.post(f"/agents/{NAME}/pause", json={})
    assert r.status_code == 200, r.text
    assert r.json()["paused"]["reason"] == agents_routes.PAUSE_DEFAULT_REASON
    spawn = client.post(f"/agents/{NAME}/spawn", json={"task": "x", "wait": True})
    assert spawn.status_code == 409
    assert agents_routes.PAUSE_DEFAULT_REASON in spawn.json()["detail"]


def test_pause_and_resume_404_for_an_unknown_agent(client):
    assert client.post("/agents/nobody/pause", json={"reason": "x"}).status_code == 404
    assert client.post("/agents/nobody/resume").status_code == 404
    # A builtin has no job card to pause either.
    assert client.post("/agents/builder/pause", json={"reason": "x"}).status_code == 404


def test_the_spawn_takes_the_job_cards_posture_and_step_budget(client):
    _hire(client, provider="", model="", approval_mode="always_ask", max_steps=7)
    r = client.post(f"/agents/{NAME}/spawn", json={"task": "read this K-1", "wait": True})
    assert r.status_code == 200, r.text
    assert r.json()["approval_mode"] == "always_ask"
    platform = client.app.state.platform
    with session_scope(platform.engine) as db:
        row = db.get(Session, r.json()["id"])
        assert row.approval_mode == "always_ask"
        assert row.max_steps == 7
    # An explicit posture on the spawn body still wins (v1.232.0 contract).
    r = client.post(
        f"/agents/{NAME}/spawn",
        json={"task": "again", "wait": True, "approval_mode": "approve_for_me"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["approval_mode"] == "approve_for_me"


# --------------------------------------------------------------------------- #
# The monthly allowance: an exhausted agent is refused, naming the allowance
# --------------------------------------------------------------------------- #
def _seed_spend(client, *, runs: int, in_tokens: int, out_tokens: int) -> None:
    platform = client.app.state.platform
    with session_scope(platform.engine) as db:
        for i in range(runs):
            db.add(
                Session(
                    task=f"seeded {i}",
                    agent_name=ROSTER,
                    provider="mock",
                    model="mock-1",
                    status=SessionStatus.COMPLETED,
                    input_tokens=in_tokens,
                    output_tokens=out_tokens,
                )
            )
        db.commit()


def test_an_exhausted_allowance_refuses_the_spawn_and_raising_it_lets_it_run(client):
    _hire(client, provider="", model="", allowance_tokens=1000)
    _seed_spend(client, runs=2, in_tokens=600, out_tokens=600)
    allowance = _row(client)["allowance"]
    assert allowance["spent_tokens"] == 2400 and allowance["runs"] == 2
    assert allowance["status"] == "exhausted"
    assert allowance["pct"] == 240 and allowance["left_tokens"] == 0

    r = client.post(f"/agents/{NAME}/spawn", json={"task": "read this K-1", "wait": True})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert "allowance" in detail and NAME in detail
    assert "2,400 of 1,000 tokens" in detail, detail
    assert len(_sessions_for(client)) == 2, "the refusal created no session row"

    # The sentence says "raise it on the Agents page" — and that is a PATCH.
    assert client.patch(f"/agents/{NAME}", json={"allowance_tokens": 10_000}).status_code == 200
    assert _row(client)["allowance"]["status"] == "ok"
    r = client.post(f"/agents/{NAME}/spawn", json={"task": "read this K-1", "wait": True})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "completed"


def _auto_pause(client) -> dict:
    """Drive the REAL post-run hook (``allowance.after_run``) so the pause on
    the record is the backend's own, never a hand-typed reason: 900 tokens →
    the 80% warning (warned month recorded), 300 more → exhausted → paused."""
    import asyncio

    from iron_jarvis.agents.allowance import after_run, month_key

    platform = client.app.state.platform
    registry = platform.agents_registry
    _seed_spend(client, runs=1, in_tokens=450, out_tokens=450)
    warned = asyncio.run(after_run(registry, platform.engine, None, ROSTER))
    assert warned["action"] == "warned", warned
    assert registry.get(NAME).allowance_warned_month == month_key()
    _seed_spend(client, runs=1, in_tokens=150, out_tokens=150)
    paused = asyncio.run(after_run(registry, platform.engine, None, ROSTER))
    assert paused["action"] == "paused", paused
    row = _row(client)
    assert row["paused"]["reason"].startswith(agents_routes.AUTO_PAUSE_PREFIX), row["paused"]
    assert row["allowance"]["status"] == "exhausted" and row["allowance"]["spent_tokens"] == 1200
    assert client.post(f"/agents/{NAME}/spawn", json={"task": "x", "wait": True}).status_code == 409
    return row


def test_the_auto_pause_prefix_is_the_backends_own_wording():
    """The route keys the auto-resume on the reason ``after_run`` writes; the
    backend holds no constant for it, so pin the literal at its source."""
    import inspect

    from iron_jarvis.agents import allowance

    src = inspect.getsource(allowance.after_run)
    assert f'reason = f"{agents_routes.AUTO_PAUSE_PREFIX} (' in src, (
        "after_run's auto-pause wording moved — update AUTO_PAUSE_PREFIX"
    )
    assert not agents_routes.PAUSE_DEFAULT_REASON.startswith(agents_routes.AUTO_PAUSE_PREFIX)


def test_raising_the_allowance_lifts_an_auto_pause_and_resets_the_warning(client):
    _hire(client, provider="", model="", allowance_tokens=1000)
    _auto_pause(client)
    r = client.patch(f"/agents/{NAME}", json={"allowance_tokens": 5000})
    assert r.status_code == 200, r.text
    assert r.json()["paused"] is None, "the PATCH answers with the fresh row"
    assert r.json()["allowance"]["status"] == "ok"
    assert r.json()["allowance"]["tokens"] == 5000
    row = _row(client)
    assert row["paused"] is None and row["allowance"]["status"] == "ok"
    registry = client.app.state.platform.agents_registry
    assert registry.get(NAME).allowance_warned_month == "", "the new bound warns afresh"
    assert registry.get(NAME).paused_reason == "" and registry.get(NAME).paused_at is None
    spawn = client.post(f"/agents/{NAME}/spawn", json={"task": "x", "wait": True})
    assert spawn.status_code == 200, spawn.text


def test_clearing_the_bound_to_unlimited_lifts_an_auto_pause_too(client):
    _hire(client, provider="", model="", allowance_tokens=1000)
    _auto_pause(client)
    r = client.patch(f"/agents/{NAME}", json={"allowance_tokens": 0})
    assert r.status_code == 200, r.text
    assert r.json()["paused"] is None and r.json()["allowance"]["status"] == "unlimited"


def test_a_raise_that_is_still_too_small_keeps_the_auto_pause(client):
    _hire(client, provider="", model="", allowance_tokens=1000)
    before = _auto_pause(client)
    r = client.patch(f"/agents/{NAME}", json={"allowance_tokens": 1100})
    assert r.status_code == 200, r.text
    assert r.json()["paused"] == before["paused"], "1,200 of 1,100 is still exhausted"
    assert r.json()["allowance"]["status"] == "exhausted"
    assert client.app.state.platform.agents_registry.get(NAME).allowance_warned_month != ""


def test_a_users_own_pause_is_never_lifted_by_raising_the_allowance(client):
    _hire(client, provider="", model="", allowance_tokens=1000)
    _seed_spend(client, runs=1, in_tokens=600, out_tokens=600)
    assert client.post(f"/agents/{NAME}/pause", json={"reason": "on holiday"}).status_code == 200
    r = client.patch(f"/agents/{NAME}", json={"allowance_tokens": 5000})
    assert r.status_code == 200, r.text
    assert r.json()["paused"]["reason"] == "on holiday"
    assert r.json()["allowance"]["status"] == "ok"
    spawn = client.post(f"/agents/{NAME}/spawn", json={"task": "x", "wait": True})
    assert spawn.status_code == 409 and "on holiday" in spawn.json()["detail"]


def test_a_patch_that_does_not_touch_the_allowance_keeps_an_auto_pause(client):
    _hire(client, provider="", model="", allowance_tokens=1000)
    before = _auto_pause(client)
    # Even once the month's spend is gone (as on the 1st), a description edit
    # or a re-send of the SAME bound is not "raising the allowance".
    platform = client.app.state.platform
    with session_scope(platform.engine) as db:
        for row in db.exec(select(Session).where(Session.agent_name == ROSTER)):
            db.delete(row)
        db.commit()
    for body in ({"description": "still paused"}, {"allowance_tokens": 1000}):
        r = client.patch(f"/agents/{NAME}", json=body)
        assert r.status_code == 200, r.text
        assert r.json()["paused"] == before["paused"], body
    # ...while an actual raise lifts it.
    r = client.patch(f"/agents/{NAME}", json={"allowance_tokens": 1001})
    assert r.status_code == 200 and r.json()["paused"] is None


def test_a_warning_level_allowance_still_runs(client):
    _hire(client, provider="", model="", allowance_tokens=1000)
    _seed_spend(client, runs=1, in_tokens=400, out_tokens=450)
    assert _row(client)["allowance"]["status"] == "warning"
    r = client.post(f"/agents/{NAME}/spawn", json={"task": "x", "wait": True})
    assert r.status_code == 200, r.text


# --------------------------------------------------------------------------- #
# The roster carries the employee fields — and never drops an older entry
# --------------------------------------------------------------------------- #
def test_roster_rows_carry_allowance_paused_and_reports_to(client):
    _hire(client, allowance_tokens=1000, reports_to="builder")
    entry = _roster_entry(client)
    assert {"paused", "pause_reason", "allowance", "reports_to"} <= set(entry)
    assert entry["paused"] is False and entry["pause_reason"] == ""
    assert entry["reports_to"] == "builder"
    assert entry["allowance"]["status"] == "ok" and entry["allowance"]["tokens"] == 1000
    # The pre-v1.295.0 keys are all still there.
    assert {
        "name", "kind", "description", "delegable", "healthy", "stats", "line",
        "activity", "last_active", "last_message", "avatar", "face",
    } <= set(entry)


def test_an_older_roster_entry_without_the_new_fields_still_serializes(client, monkeypatch):
    from iron_jarvis.agents import roster as roster_mod

    # A real pre-v1.295.0 construction (no paused/allowance/reports_to kwargs)
    # and a duck-typed entry with NO such attributes at all.
    older = roster_mod.RosterEntry(
        name="custom:old", kind="dynamic", description="an old hand",
        delegable=True, healthy=True, stats=None,
    )
    duck = SimpleNamespace(
        name="custom:duck", kind="dynamic", description="no new attrs",
        delegable=True, healthy=True, stats=None,
        line=lambda: "custom:duck — no new attrs (no runs yet)",
    )
    monkeypatch.setattr(roster_mod, "build_roster", lambda platform: [older, duck])
    r = client.get("/agents/roster")
    assert r.status_code == 200, r.text
    rows = {e["name"]: e for e in r.json()["roster"]}
    assert set(rows) == {"custom:old", "custom:duck"}, "neither entry may drop"
    for row in rows.values():
        assert row["paused"] is False
        assert row["pause_reason"] == ""
        assert row["allowance"] is None
        assert row["reports_to"] == ""
    assert rows["custom:duck"]["activity"] == "unknown"


# --------------------------------------------------------------------------- #
# The wire shapes
# --------------------------------------------------------------------------- #
def test_the_schemas_carry_the_job_card():
    from iron_jarvis.daemon.schemas import AgentCreate, AgentPatch, PauseBody

    card = {
        "base_type", "approval_mode", "max_steps", "allowance_tokens",
        "allowance_usd", "reports_to", "skills", "deny_tools",
    }
    assert card <= set(AgentCreate.model_fields)
    assert card | {"provider", "model", "base_type"} <= set(AgentPatch.model_fields)
    for field in card | {"provider", "model", "system_prompt", "tools", "description"}:
        assert AgentPatch.model_fields[field].default is None, (
            f"AgentPatch.{field} must default to None (= keep)"
        )
    assert PauseBody().reason == ""
    assert AgentPatch.model_fields["clear_max_steps"].default is False
