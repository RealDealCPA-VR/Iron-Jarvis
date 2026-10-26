"""A coding session's helper cut off by a restart is not offered Continue
(v1.391.0, the Phase 2 review's open item). Its reply carries on by itself
after the restart and may hand the same work out again, so a Continue on the
helper in the interrupted list (the bell row, the status note) would run the
work twice. A mission's teammate was already left out (v1.309.0); a plain job
still shows.
"""

from __future__ import annotations

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Session, SessionStatus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes.sessions import interrupted_rows
from iron_jarvis.harness.helpers import HELPER_ORIGIN


def _active(app, task: str, origin: str | None) -> str:
    with session_scope(app.state.platform.engine) as db:
        s = Session(task=task, status=SessionStatus.ACTIVE, origin=origin)
        db.add(s)
        db.commit()
        return s.id


def test_a_cut_off_helper_is_not_offered_continue_but_a_plain_job_is(tmp_path):
    app = create_app(str(tmp_path))
    helper = _active(app, "read the tests", HELPER_ORIGIN)
    job = _active(app, "rename the Q1 files", None)
    chat_job = _active(app, "summarize the report", "chat")
    assert app.state.orchestrator.reconcile_interrupted_sessions() >= 3
    with session_scope(app.state.platform.engine) as db:
        listed = {s.id for s in interrupted_rows(db)}
    assert helper not in listed
    assert job in listed and chat_job in listed
