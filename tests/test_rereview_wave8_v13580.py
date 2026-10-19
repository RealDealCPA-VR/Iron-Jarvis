"""v1.358.0 re-review — two places where a surface still said something the
run (or the app) was not, after the integrator's seams.

(1) ONE VERDICT PER RUN, the cancel path. A scheduled workflow parked on the
    user reads "Needs you" and the home line counts it; the integrator made
    the person's ANSWER settle the row (``workflow.completed``). But the
    Workflows page's Cancel of a WAITING run ends it inside the route — no
    engine loop runs, so no ``workflow.completed`` is published — and the row
    kept "Needs you" (and "1 scheduled task needs a look") over a run whose
    own page said Stopped, until the next fire. The route now settles it
    through ``platform.settle_stopped_waiting``.

(2) THE CHAT AND THE CARD AGREE ON A SIGN-IN, the first read. The chat's
    apps section reads Microsoft 365's sign-in verdict through
    ``signin_seen``. After a restart nothing has been asked yet, so the
    FIRST chat turn naming the app read ``None`` and told the model "not
    signed in yet … point to its Sign in button" — for a user who IS signed
    in. ``app_states`` runs off the loop, so it now waits (bounded) for the
    one check its read starts (``signin_seen.seen_settled``).
"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from iron_jarvis.connectors import app_names, signin_seen
from iron_jarvis.daemon.app import create_app
from iron_jarvis.scheduling import run_outcome as ro

# The lanes' own harnesses (fixtures are registered by import).
from tests.test_connections_honest_v13580 import (  # noqa: F401 — pytest fixtures
    CARD_ID,
    _connect_ms365,
    _fresh,
    _platform,
    ms365,
    signin_file,
)
from tests.test_integrate_wave8_v13580 import _park, _schedule_row


# =========================================================================== #
# (1) a parked scheduled workflow that is CANCELLED settles its row
# =========================================================================== #
def test_cancelling_a_parked_scheduled_run_settles_its_row(tmp_path):
    """MUTATION: drop the ``settle_stopped_waiting`` call in the cancel route
    -> the row keeps ``needs_you`` and the home line still counts it (red)."""
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        run_id = _park(client)
        assert _schedule_row(client, "fire-gated")["last_status"] == "needs_you"
        assert client.get("/ui/status-line").json()["short_schedules"] == 1
        link_before = _schedule_row(client, "fire-gated")["last_session_id"]

        r = client.post(f"/workflows/runs/{run_id}/cancel")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "cancelled"

        row = _schedule_row(client, "fire-gated")
        assert (row["last_status"], row["last_detail"]) == ro.workflow_stopped_waiting("gated")
        assert row["last_detail"] == "Your workflow “gated” was stopped before you answered."
        assert row["last_session_id"] == link_before, "the schedule's link is kept"
        assert client.get("/ui/status-line").json()["short_schedules"] == 0
        # Settled once: a second cancel is refused by the route and the row
        # stays as the first cancel left it.
        assert client.post(f"/workflows/runs/{run_id}/cancel").status_code == 409
        assert _schedule_row(client, "fire-gated")["last_status"] == ro.STATUS_CANCELLED


def test_a_later_fire_keeps_its_own_say_when_an_old_parked_run_is_cancelled(tmp_path):
    """The cancel restamps a row ONLY while it still holds the parked
    verdict — a later fire that wrote its own words is never overwritten."""
    from sqlmodel import select

    from iron_jarvis.core.db import session_scope
    from iron_jarvis.scheduling.models import ScheduledTaskRecord

    with TestClient(create_app(str(tmp_path / "home"))) as client:
        run_id = _park(client)
        platform = client.app.state.platform
        with session_scope(platform.engine) as db:
            rec = db.exec(
                select(ScheduledTaskRecord).where(ScheduledTaskRecord.name == "fire-gated")
            ).one()
            rec.last_status, rec.last_detail = "error", "The run failed: a later fire."
            db.add(rec)
            db.commit()
        assert client.post(f"/workflows/runs/{run_id}/cancel").status_code == 200
        row = _schedule_row(client, "fire-gated")
        assert (row["last_status"], row["last_detail"]) == ("error", "The run failed: a later fire.")


def test_cancelling_a_run_no_schedule_parked_touches_no_row(tmp_path):
    """A waiting run started by hand (Run on the Workflows page) is in no
    schedule's map: its cancel restamps nothing."""
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        r = client.post(
            "/workflows",
            json={"name": "solo", "steps": [{"name": "Ask", "kind": "ask", "message": "Go?"}]},
        )
        assert r.status_code < 300, r.text
        run = client.post("/workflows/run", json={"name": "solo"}).json()
        deadline = time.monotonic() + 30
        while client.get(f"/workflows/runs/{run['id']}").json()["status"] != "waiting":
            assert time.monotonic() < deadline, "the hand-started run never parked"
            time.sleep(0.05)
        platform = client.app.state.platform
        assert platform.settle_stopped_waiting(run["id"]) is False
        assert client.post(f"/workflows/runs/{run['id']}/cancel").status_code == 200


# =========================================================================== #
# (2) the first chat read after a restart waits for the sign-in verdict
# =========================================================================== #
def _apps_row(c: TestClient) -> dict:
    p = _platform(c)
    live = app_names.live_tools(p, [CARD_ID])
    (row,) = app_names.app_states(p, [CARD_ID], live)
    return row


def test_the_first_chat_read_after_a_restart_says_signed_in_when_it_is(ms365, signin_file):  # noqa: F811 — pytest fixtures
    """Signed in, then the daemon restarts (no verdict in memory): the chat's
    apps section must not tell the model "not signed in yet". MUTATION:
    ``_signin_unfinished`` back on ``signin_seen.seen`` -> the first read is
    ``signin`` (red)."""
    _connect_ms365(ms365)
    signin_file.write_text("dana@contoso.com", encoding="utf-8")
    signin_seen.forget()  # a restart: nothing has been asked yet
    row = _apps_row(ms365)
    assert row["state"] == "connected", row
    assert "connected (24 tools)" in app_names.apps_section([row])


def test_the_first_chat_read_still_says_sign_in_when_nobody_signed_in(ms365, signin_file):  # noqa: F811 — pytest fixtures
    """The control: the wait reads the app's real answer — not signed in
    stays "signin" (never green on a wait that timed out or a guess)."""
    _connect_ms365(ms365)
    signin_seen.forget()
    row = _apps_row(ms365)
    assert row["state"] == "signin", row
    assert "not signed in yet" in app_names.apps_section([row])


def test_seen_settled_is_bounded_and_never_a_second_call(monkeypatch):
    """No verdict and a check that never lands: the reader waits ``wait_s``
    and answers ``None`` — it starts ONE background check (``seen``'s), never
    calls the app itself."""
    signin_seen.forget()
    started: list[object] = []

    def _never_runs(fn):  # the background check is "started" but never lands
        started.append(fn)

    t0 = time.monotonic()
    got = signin_seen.seen_settled(object(), "srv", "id", wait_s=0.3, spawn=_never_runs)
    took = time.monotonic() - t0
    assert got is None
    assert len(started) == 1
    assert 0.25 <= took < 5.0, took
    # A verdict already known is answered at once (no wait).
    signin_seen.record("srv", signin_seen.Verdict(True, "dana@contoso.com", "check"), "id")
    t0 = time.monotonic()
    got = signin_seen.seen_settled(object(), "srv", "id", wait_s=5.0, spawn=_never_runs)
    assert got is not None and got.signed_in and time.monotonic() - t0 < 1.0
    signin_seen.forget()
