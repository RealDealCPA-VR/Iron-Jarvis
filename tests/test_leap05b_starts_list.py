"""Leap 05-b (R2.9): every start in one place.

* ``GET /starts`` is ONE list built from the stored objects themselves (no
  table of its own): each schedule and each rule, with what it does and what
  it last did; each row's id loads from its own store;
* ``?workflow=<name>`` lists only what runs that saved workflow (the canvas's
  trigger node reads it);
* ``GET /starts/kinds`` says which kinds of signal are not set up yet, in
  words, with the page that sets each up, and folds in ``GET /triggers``;
  never a secret;
* chat's ``start_list`` answers "what runs on its own here?" from the same
  list (allow tier, read only), and the question arms it.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.daemon.app import create_app
from iron_jarvis.reflex.models import ReflexRule
from iron_jarvis.scheduling.models import ScheduledTaskRecord
from iron_jarvis.tools.base import ToolContext

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(300)]


@pytest.fixture()
def desk(tmp_path):
    c = TestClient(create_app(str(tmp_path / "home")))
    p = c.app.state.platform
    yield c, p
    try:
        p.scheduler.shutdown()
    except Exception:  # noqa: BLE001
        pass


def _setup(p):
    nightly = p.scheduler.add_task("Nightly report", "0 2 * * *", kind="task", payload={"task": "Write the report"})
    month = p.scheduler.add_task("Month end run", "0 6 1 * *", kind="workflow", payload={"workflow": "Month end"})
    inbox = p.reflex.add(name="Inbox", source="webhook", match="inbox", action="workflow", target="Month end")
    why = p.reflex.add(name="Tell me why", source="run_ended", match="", action="session",
                       task_template="Say why", start={"what": "schedule", "name": "Nightly report", "outcome": "any"})
    return nightly, month, inbox, why


def test_each_rows_id_loads_from_its_own_store(desk):
    """MUTATION (red): give a schedule row ``store: "rule"`` in
    ``starts.list_starts`` -> its id does not load from the rules table."""
    c, p = desk
    nightly, month, inbox, why = _setup(p)
    rows = c.get("/starts").json()["starts"]
    assert {r["id"] for r in rows} == {nightly.id, month.id, inbox.id, why.id}
    tables = {"schedule": ScheduledTaskRecord, "rule": ReflexRule}
    with session_scope(p.engine) as db:
        for row in rows:
            rec = db.get(tables[row["store"]], row["id"])
            assert rec is not None and rec.name == row["name"], row
    by = {r["id"]: r for r in rows}
    assert by[nightly.id]["when_words"] == "Every day at 2:00 AM."
    assert by[nightly.id]["does_words"] == "Does: Write the report."
    assert by[nightly.id]["last_words"] == "Not run yet." and by[nightly.id]["href"] == "/schedules"
    assert by[why.id]["kind"] == "run_ended" and by[why.id]["href"] == f"/reflex?rule={why.id}"
    assert by[why.id]["when_words"] == (
        "When the schedule “Nightly report” fails or falls short (part failed, needs you, or did nothing)."
    )
    assert by[inbox.id]["last_words"] == "Has not run yet."
    for row in rows:
        assert "cron" not in row["when_words"], row
        assert set(row) == {"id", "kind", "name", "when_words", "does_words", "enabled", "last_words", "href", "store"}


def test_only_what_runs_a_workflow_when_asked(desk):
    c, p = desk
    nightly, month, inbox, why = _setup(p)
    rows = c.get("/starts", params={"workflow": "month END"}).json()["starts"]
    assert sorted(r["id"] for r in rows) == sorted([month.id, inbox.id])


def test_the_kinds_say_what_is_not_set_up_with_the_page_and_no_secret(desk):
    c, p = desk
    p.secrets.set("calendar_ics_url", "https://cal.example.invalid/private-feed.ics", kind="password")
    body = c.get("/starts/kinds").json()
    kinds = body["kinds"]
    assert kinds["email"]["ready"] is False and kinds["email"]["door"]["href"] == "/channels"
    assert kinds["email"]["words"].startswith("A rule started by email needs a mailbox that listens.")
    # The link is saved but the calendar is off: not ready, and the link is never said.
    assert kinds["calendar"]["ready"] is False and kinds["calendar"]["door"]["href"] == "/reflex"
    assert kinds["schedule"]["ready"] and kinds["run_ended"]["ready"] and kinds["webhook"]["ready"]
    assert body["triggers"] == c.get("/triggers").json()["triggers"]
    assert body["triggers"]["calendar"]["has_url"] is True
    assert "private-feed" not in c.get("/starts/kinds").text


def test_start_list_answers_from_the_one_list_and_the_question_arms_it(desk, tmp_path):
    """MUTATION (red): drop ``runs_on_its_own`` from ``_record_tools_named``
    -> the question arms nothing."""
    from iron_jarvis.settings import records as R

    c, p = desk
    nightly, month, inbox, why = _setup(p)
    assert R.record_tools_for("What runs on its own here?") == ["start_list"]
    assert "start_list" in R.record_tools_for("Show me all my automations")
    assert p.permissions._base.get("start_list") == "allow"  # noqa: SLF001
    ctx = ToolContext(workspace=tmp_path, session_id="chat", agent_run_id="chat",
                      config=p.config, event_bus=p.event_bus, engine=p.engine)
    r = asyncio.run(p.registry.invoke("start_list", {}, ctx, p.permissions))
    assert r.ok, r.error
    assert r.output.startswith("4 things run on their own here:")
    assert "- Schedule “Nightly report”: Every day at 2:00 AM. Does: Write the report. Not run yet." in r.output
    assert "- Rule “Tell me why”: When the schedule “Nightly report” fails" in r.output
    assert "Not set up yet, so no rule can hear it: email (Open Notifications in Settings)" in r.output
    assert [row["id"] for row in r.data["starts"]] == [row["id"] for row in c.get("/starts").json()["starts"]]
    only = asyncio.run(p.registry.invoke("start_list", {"workflow": "Month end"}, ctx, p.permissions))
    assert only.output.startswith("2 things run the workflow “Month end”:")


def test_a_failing_task_said_in_chat_arms_the_rule_and_its_readers():
    from iron_jarvis.settings import records as R

    got = R.record_tools_for("If the nightly report fails, tell me why and try once more.")
    assert {"reflex_create", "start_list", "schedule_list"} <= set(got)
    assert "schedule_create" not in got, "a rule about a schedule's end makes no new schedule"
    assert "reflex_create" not in R.record_tools_for("Rename these files; if one fails, skip it.")
