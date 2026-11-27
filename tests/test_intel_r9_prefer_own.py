"""The intelligence plan, release 9: "Prefer my own hardware" reads what
really happened, not just that the run ended.

A local model's quality (``observability.local_quality``, the one number the
router's local-first oracle and GET /routing/quality both read) used to be the
average of each session's ``Evaluation.completion`` — 1.0 whenever the last
run ENDED completed. Now a session whose record says the job was not done
(``Session.outcome``) scores 0.0, a rehearsal is not evidence, and a session
with no outcome keeps its completion. Through the served route.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun, AgentType, Session
from iron_jarvis.daemon.app import create_app
from iron_jarvis.eval.models import Evaluation


def _seed(platform, outcomes, *, model="qwen3:8b") -> None:
    with session_scope(platform.engine) as db:
        for outcome in outcomes:
            sess = Session(task="t", provider="ollama", model=model, outcome=outcome)
            db.add(sess)
            run = AgentRun(agent_type=AgentType("builder"), provider="ollama", model=model)
            run.session_id = sess.id
            db.add(run)
            db.add(Evaluation(session_id=sess.id, agent_run_id=run.id, completion=1.0))
        db.commit()


def _avg(tmp_path, outcomes):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    _seed(platform, outcomes)
    rows = [r for r in client.get("/routing/quality").json()["rows"]
            if r["provider"] == "ollama" and r["task_class"] is None]
    assert len(rows) == 1, rows
    direct = platform.observability.local_quality("ollama", model="qwen3:8b", min_samples=1)
    assert rows[0]["avg"] == pytest.approx(direct)
    return direct


def test_a_run_that_ended_but_did_not_do_the_job_does_not_count_as_done(tmp_path):
    # Every run ENDED completed (completion 1.0); the record says otherwise.
    avg = _avg(tmp_path, ["completed", "completed_with_failures", "did_nothing", "needs_you"])
    assert avg == pytest.approx(0.25)


def test_a_rehearsal_is_not_evidence_and_an_old_row_keeps_its_completion(tmp_path):
    avg = _avg(tmp_path, ["rehearsed", None, "completed", "completed_with_failures"])
    assert avg == pytest.approx(2 / 3)


def test_a_model_whose_jobs_were_done_still_clears(tmp_path):
    assert _avg(tmp_path, ["completed", "completed", "completed"]) == pytest.approx(1.0)
