"""Readiness 12A review — the failure words reach EVERY door a failed run is
read from, not only the ones the lane drove.

The lane gave a failed run one plain sentence (``failure_words.
run_failure_words``) through ``Orchestrator._finalize_failed``, and drove a
TASK schedule. Three doors kept their own raw text:

* a WORKFLOW's agent step whose run raised (``run_session`` re-raises after
  finalizing): the step's summary was ``f"{type(exc).__name__}: {exc}"`` —
  "ProviderError: codex-cli: CLI exited 1: error: unexpected status 400 …" —
  on the Workflows page, in the scheduled workflow's Schedules row ("stopped
  at its step …: ProviderError: …"), and on the phone;
* a job started FROM THE PHONE whose run raised: "I hit a problem:
  ProviderError: codex-cli: …" (``comm/inbound._run_problem``);
* a schedule fire that failed BEFORE any run existed: the row recorded
  ``str(exc)`` (the doer's open point 4).

Each pin drives the REAL code (the app factory, the workflow engine, the
orchestrator, the phone's job runner) and was RED on the lane's commit.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers import failure_words as fw
from iron_jarvis.providers.adapters.base import ProviderError
from iron_jarvis.scheduling import run_outcome as ro
from iron_jarvis.workflows.models import WorkflowRunRecord
from tests.test_schedule_outcomes_v13510 import _add_task, _mock_channel, _schedule_messages

#: The audit's own example (0d.6): an adapter's untyped CLI failure.
CODEX_400 = "codex-cli: CLI exited 1: error: unexpected status 400 Bad Request: model is not supported"
#: What every surface says instead (failure_words.run_failure_words).
CODEX_REASON = (
    "Codex couldn't finish this task. Run it again. If it happens again, "
    "choose another model in Settings."
)
RAW_BITS = ("ProviderError", "codex-cli", "CLI exited", "unexpected status", "Bad Request")


def _raw(text: str) -> list[str]:
    return [b for b in RAW_BITS if b in str(text or "")]


def _failing_run(exc):
    async def run(session, agent_def, *a, **kw):
        raise exc

    return run


# --------------------------------------------------------------------------- #
# 1. A workflow's agent step whose run raised
# --------------------------------------------------------------------------- #
def _scheduled_workflow_fire(tmp_path, monkeypatch, run=None) -> dict:
    app = create_app(str(tmp_path))
    client = TestClient(app)
    platform = app.state.platform
    monkeypatch.setattr(platform.orchestrator.runtime, "run", run or _failing_run(ProviderError(CODEX_400)))
    r = client.post(
        "/workflows",
        json={"name": "weekly-digest", "steps": [{"name": "Draft", "kind": "agent", "task": "Draft the digest."}]},
    )
    assert r.status_code < 300, r.text
    r = client.post(
        "/schedules",
        json={"name": "digest", "cron": "0 3 * * *", "kind": "workflow", "payload": {"workflow": "weekly-digest"}},
    )
    assert r.status_code == 200, r.text
    mock = _mock_channel(app)
    before = len(mock.sent)
    body = client.post("/schedules/digest/run").json()
    with session_scope(platform.engine) as db:
        runs = list(db.exec(select(WorkflowRunRecord).where(WorkflowRunRecord.workflow_name == "weekly-digest")))
        outputs = [json.loads(r.outputs_json or "{}") for r in runs]
        statuses = [r.status for r in runs]
    step = (outputs[0] if outputs else {}).get("Draft") or {}
    page = client.get(f"/sessions/{step['session_id']}").json()["session"] if step.get("session_id") else {}
    return {"body": body, "outputs": outputs, "statuses": statuses, "sent": mock.sent[before:],
            "schedule_lines": _schedule_messages(mock, before, "digest"), "page": page}


def test_a_workflow_agent_step_that_raised_reads_the_runs_plain_words(tmp_path, monkeypatch):
    """RED on the lane's commit: the step summary, the Schedules row and the
    phone read "ProviderError: codex-cli: CLI exited 1: …"."""
    got = _scheduled_workflow_fire(tmp_path, monkeypatch)
    # Anti-vacuity: the REAL engine ran the step and the run failed on it.
    assert got["statuses"] == ["failed"], got
    step = got["outputs"][0]["Draft"]
    assert step["status"] == "failed", step
    # The Workflows page's step row: the run's own plain sentence.
    assert step["summary"] == f"The run failed: {CODEX_REASON}", step
    # The Schedules row: names the step, in plain words, no raw text.
    body = got["body"]
    assert body["last_status"] == "error", body
    assert "Codex couldn't finish this task" in body["last_detail"], body
    assert _raw(body["last_detail"]) == [], body["last_detail"]
    # Every line a destination received (the schedule's own and the bell's).
    assert got["schedule_lines"], got["sent"]
    for line in got["sent"]:
        assert _raw(line) == [], line


def test_the_step_reads_exactly_what_its_run_page_says(tmp_path, monkeypatch):
    """The step's summary IS the run's own (``_finalize_failed`` keeps a run's
    prose when it wrote some before failing) — one sentence, read in two
    places, never two renderings of the same failure."""

    async def half_then_fail(session, agent_def, *a, **kw):
        session.summary = "Wrote the outline, then stopped."
        raise ProviderError(CODEX_400)

    got = _scheduled_workflow_fire(tmp_path, monkeypatch, run=half_then_fail)
    step, page = got["outputs"][0]["Draft"], got["page"]
    assert page["summary"].startswith("Wrote the outline, then stopped."), page
    assert step["summary"] == page["summary"], (step, page)
    assert page["failure_detail"] == CODEX_400


# --------------------------------------------------------------------------- #
# 2. A job started from the phone whose run raised
# --------------------------------------------------------------------------- #
async def test_a_phone_started_job_that_failed_hears_the_plain_words():
    """RED on the lane's commit: the phone heard "I hit a problem:
    ProviderError: codex-cli: CLI exited 1: error: unexpected status 400 …"
    (``comm/inbound._run_problem``); no hover can fold it away there."""
    from iron_jarvis.comm import inbound

    said = inbound._run_problem(ProviderError(CODEX_400))
    assert said == CODEX_REASON
    assert _raw(said) == []
    # A safety refusal keeps its own run sentence (v1.378.0), unchanged.
    from iron_jarvis.providers.adapters.base import ProviderRefusal

    # (partial: a refusal that cut a streamed answer — a phone JOB showed none
    # of it, so the phone never hears "The reply above is incomplete".)
    def refusal():
        return ProviderRefusal("claude-cli: declined", provider="claude-cli", partial=True)

    expected = fw.refusal_reason(refusal())
    assert "incomplete" not in expected
    assert inbound._run_problem(refusal()) == expected
    # Never the class name, whatever the failure.
    assert "RuntimeError" not in inbound._run_problem(RuntimeError("database is locked"))


async def test_the_phones_job_runner_delivers_the_plain_words(monkeypatch):
    """Through the REAL job runner (``InboundPoller._deliver_session``): the
    line put on the thread and sent carries no raw text."""
    from iron_jarvis.comm import inbound

    sent: list[str] = []

    class _Ch:
        def send(self, body, chat_id=None):
            sent.append(body)
            return {"ok": True}

    class _Orch:
        async def run_session(self, sid):
            raise ProviderError(CODEX_400)

    poller = inbound.InboundPoller.__new__(inbound.InboundPoller)
    poller.orchestrator = _Orch()
    poller.reply_prefix = ""
    poller.max_reply_chars = 4000
    poller._shutting_down = False
    poller._identity_lock = lambda name, sender: _NullLock()  # noqa: E731
    poller._safe_append = lambda *a, **k: None  # noqa: E731
    msg = SimpleNamespace(sender_id="u1", reply_to="c1")
    await poller._deliver_session("phone", _Ch(), msg, SimpleNamespace(id="s1"), tid="", display="", chat_on=False)
    assert sent and all(_raw(s) == [] for s in sent), sent
    assert CODEX_REASON in sent[0], sent


class _NullLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


# --------------------------------------------------------------------------- #
# 3. A schedule fire that failed before any run existed
# --------------------------------------------------------------------------- #
def test_a_fire_that_failed_before_any_run_never_records_the_raw_text(tmp_path, monkeypatch):
    """RED on the lane's commit: ``reason = str(exc)`` — the row and the
    phone read the raw text of a failure that happened before a run existed
    (here: the orchestrator could not create the run)."""
    app = create_app(str(tmp_path))
    client = TestClient(app)
    platform = app.state.platform
    _add_task(client, "nightly")

    async def refuse(*a, **kw):
        raise ProviderError(CODEX_400)

    monkeypatch.setattr(platform.orchestrator, "create_session", refuse)
    mock = _mock_channel(app)
    before = len(mock.sent)
    body = client.post("/schedules/nightly/run").json()
    assert body["last_status"] == "error", body
    assert body.get("last_session_id") in (None, ""), body  # no run existed
    assert body["last_detail"] == ro.failure_detail(CODEX_REASON), body
    for line in _schedule_messages(mock, before, "nightly"):
        assert _raw(line) == [], line


def test_an_own_failure_before_any_run_never_points_at_a_run(tmp_path, monkeypatch):
    """Iron Jarvis's own fault before a run existed: plain words that never
    say "open the run" (there is none — the row has no link)."""
    app = create_app(str(tmp_path))
    client = TestClient(app)
    platform = app.state.platform
    _add_task(client, "nightly")

    async def boom(*a, **kw):
        raise RuntimeError("sqlite3.OperationalError: disk I/O error at 0x7ff3")

    monkeypatch.setattr(platform.orchestrator, "create_session", boom)
    body = client.post("/schedules/nightly/run").json()
    assert body["last_status"] == "error", body
    detail = body["last_detail"]
    assert "disk I/O" not in detail and "0x7ff3" not in detail and "OperationalError" not in detail, detail
    assert "open the run" not in detail, detail
    assert "Iron Jarvis couldn't start this task" in detail, detail


def test_the_schedules_own_worded_refusals_keep_their_sentence(tmp_path):
    """A fire the schedule itself refused in words (its workflow was deleted)
    keeps that sentence — the generic words never replace a real reason —
    and it reads as a sentence for a person, not "scheduled workflow
    'weekly-digest' not found"."""
    app = create_app(str(tmp_path))
    client = TestClient(app)
    r = client.post(
        "/workflows",
        json={"name": "weekly-digest", "steps": [{"name": "Draft", "kind": "agent", "task": "Draft it."}]},
    )
    assert r.status_code < 300, r.text
    r = client.post(
        "/schedules",
        json={"name": "digest", "cron": "0 3 * * *", "kind": "workflow", "payload": {"workflow": "weekly-digest"}},
    )
    assert r.status_code == 200, r.text
    assert client.delete("/workflows/weekly-digest").status_code == 200
    body = client.post("/schedules/digest/run").json()
    assert body["last_status"] == "error", body
    assert body["last_detail"] == ro.failure_detail(
        "the workflow “weekly-digest” this schedule runs no longer exists. "
        "Edit the schedule to pick another, or remove it"
    ), body


# --------------------------------------------------------------------------- #
# 4. The browser sidebar's "not armed" footnote (the lane's open point 2)
# --------------------------------------------------------------------------- #
async def test_the_browser_sidebars_footnote_names_no_tools_menu(tmp_path):
    """RED on the lane's commit: a sidebar turn (the browser add-on's panel —
    the ONLY caller that passes ``arm_family``) ended "— turn on Web &
    research in the chat's Tools menu." The sidebar has no Tools menu. Driven
    through the REAL stream lane with the panel's own seam."""
    from tests.test_chat_recovery_v1344 import _FakeTool, _app, _body, _done, _frames, _script

    from iron_jarvis.daemon.chat_turn import NOT_ARMED_ELSEWHERE, refusal_footnotes
    from iron_jarvis.daemon.routes.chat import stream_chat_turn
    from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall

    armed, web = _FakeTool("lookup_fake"), _FakeTool("web_search")
    platform = _app(tmp_path, armed, web)
    _, stream = _script([
        LLMResponse(text="", tool_calls=[ToolCall(id="c1", name="web_search", arguments={"q": "x"})]),
        LLMResponse(text="That tool was not available."),
    ])
    platform.router.stream = stream
    gen = await stream_chat_turn(platform, {}, _body("lookup_fake"), arm_family=frozenset({"lookup_fake"}))
    reply = _done(_frames("".join([c async for c in gen])))["reply"]
    assert web.calls == [], "anti-vacuity: the gate refused the unarmed tool"
    words = NOT_ARMED_ELSEWHERE["sidebar"]
    assert reply.endswith(f"\n\n_Note: Jarvis couldn't search the web ({words})._"), reply
    for place in ("Tools menu", "Web & research", "for this chat"):
        assert place not in reply, (place, reply)
    # The chat page's words are untouched (no arm_family = the chat page).
    assert "Tools menu" in refusal_footnotes([("web_search", "not armed")])
