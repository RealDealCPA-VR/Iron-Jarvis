"""v1.439.0 review of chat-first Wave 6 group D: the dashboard's new doors are
fed what the REAL daemon answers, never an invented shape.

This file drives a real ``create_app`` and records, into
``dashboard/__tests__/fixtures/wave6-reconnect-v1439.json``, the answers of:

* ``DELETE /goals/{id}`` (with one standing grant to revoke, and the 404);
* ``GET /build/sessions/{id}/ci`` (no watch, and a watching record whose
  ``next_check_at`` is the daemon's own naive-UTC isoformat);
* ``GET /triggers`` with a saved calendar link, and
  ``DELETE /triggers/calendar/url`` (removed, then nothing left to remove).

``dashboard/__tests__/wave6-reconnect-review-v1439.test.tsx`` decodes the same
file. Regenerate after an intended change with ``IJ_REGEN_WAVE6_RECONNECT=1``.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import ci_fix as CI

FIXTURE = (
    Path(__file__).resolve().parents[1] / "dashboard" / "__tests__" / "fixtures" / "wave6-reconnect-v1439.json"
)

#: A fixed moment, written the way the record stores it (naive UTC).
NEXT_CHECK = datetime(2026, 11, 6, 18, 16, 58, 831240)


def _record(tmp_path: Path) -> dict:
    app = create_app(str(tmp_path))
    out: dict = {}
    with TestClient(app) as c:
        p = app.state.platform
        made = c.post(
            "/goals",
            json={"name": "inbox zero", "contract_text": "Create a file summarizing it.", "budget": {"max_tokens": 1000}},
        )
        assert made.status_code == 200, made.text
        gid = made.json()["goal"]["id"]
        p.grants.create("goal", gid, "write_file", "", "write any file", 30)
        gone = c.delete(f"/goals/{gid}")
        assert gone.status_code == 200, gone.text
        body = gone.json()
        # Ids are random: keep the shape and the count, not the values.
        out["goal_delete"] = {
            "deleted": "GOAL_ID" if body.get("deleted") == gid else body.get("deleted"),
            "revoked_grants": ["GRANT_ID" for _ in body.get("revoked_grants", [])],
        }
        again = c.delete(f"/goals/{gid}")
        out["goal_delete_missing"] = {"status": again.status_code, "body": again.json()}

        out["ci_none"] = c.get("/build/sessions/t1/ci").json()
        CI.ensure(p.engine, "t1", {"pr_url": "https://github.com/o/r/pull/7", "pr_number": "7", "branch": "ij/x"})
        CI.save(p.engine, "t1", state=CI.WATCHING, rounds=1, next_check_at=NEXT_CHECK, failed=["tests"])
        out["ci_watching"] = c.get("/build/sessions/t1/ci").json()

        saved = c.post("/triggers/calendar", json={"enabled": True, "ics_url": "https://cal.example.com/x.ics"})
        assert saved.status_code == 200, saved.text
        out["triggers_saved_link"] = c.get("/triggers").json()
        out["calendar_url_removed"] = c.delete("/triggers/calendar/url").json()
        out["calendar_url_none"] = c.delete("/triggers/calendar/url").json()
    return out


def test_the_dashboards_fixture_is_what_the_daemon_answers(tmp_path):
    got = _record(tmp_path)
    # The facts the dashboard relies on, said here so a reader sees them.
    assert got["goal_delete"] == {"deleted": "GOAL_ID", "revoked_grants": ["GRANT_ID"]}
    assert got["goal_delete_missing"]["status"] == 404
    assert got["ci_none"] == {"watch": None, "on": True}
    assert got["ci_watching"]["watch"]["next_check_at"] == NEXT_CHECK.isoformat()  # no zone
    assert "triggers" in got["triggers_saved_link"]  # nested, never flat
    assert got["triggers_saved_link"]["triggers"]["calendar"]["has_url"] is True
    assert got["calendar_url_removed"]["removed"] is True
    assert got["calendar_url_none"]["removed"] is False
    text = json.dumps(got, indent=1, sort_keys=True) + "\n"
    assert "cal.example.com" not in text  # the link is a secret, never echoed
    if os.environ.get("IJ_REGEN_WAVE6_RECONNECT") == "1" or not FIXTURE.exists():
        if not FIXTURE.exists() and os.environ.get("IJ_REGEN_WAVE6_RECONNECT") != "1":
            raise AssertionError(f"missing fixture {FIXTURE}; run with IJ_REGEN_WAVE6_RECONNECT=1")
        FIXTURE.write_bytes(text.encode("utf-8"))
    assert FIXTURE.read_bytes().decode("utf-8").replace("\r\n", "\n") == text
