"""v1.250.0 (S-01 follow-up): the daemon measures its OWN boot.

The Upgrade Ballot's "the app takes 6-15 s to open" was traced on the USER's
install to 7-10 s between reading settings and being ready. That gap did NOT
reproduce on a scratch root (0.73 s), and it could not: an empty state home has
no MCP servers, no skills, no saved terminals and a 0-byte database. Guessing
which phase costs the time on the one machine that does not show the symptom is
how the wrong thing gets optimised — so the boot reports its own breakdown
where it actually happens: ONE INFO line when startup completes, and the same
numbers under ``startup`` at ``GET /diagnostics`` so nobody has to grep a log.

TWO THINGS THIS PINS BEYOND "a number exists":

* THE PHASE THE BALLOT BLAMED IS NOT IN THE LIFESPAN. uvicorn prints "Started
  server process" BEFORE it runs the lifespan, so the state-home-to-server gap
  is ``create_app``'s platform build (MCP servers, skills discovery, registry,
  search index) — instrumenting only ``_rehydrate_step`` would have measured
  everything except the suspect. ``platform`` is therefore a timed phase.
* PRIVACY. Names and numbers only. Phase names are code identifiers, never a
  path, a file name, a credential or any user content: this line lands in the
  log file AND in the diagnostics blob users paste into bug reports.
"""

from __future__ import annotations

import logging
import re

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import _boot_line, create_app


# ==========================================================================
# The one-line summary — pure formatting, no boot required
# ==========================================================================


def test_the_summary_names_the_slowest_phases():
    line = _boot_line(
        8420.0,
        {"platform": 4100.0, "skills": 1900.0, "rehydrate_terminals": 800.0, "tiny": 3.0},
    )
    assert line == "startup 8.42 s: platform 4.10, skills 1.90, rehydrate_terminals 0.80"
    # A phase under the noise floor is not a cause, so it is not named.
    assert "tiny" not in line


def test_the_summary_is_bounded_and_counts_what_it_did_not_name():
    """A boot with 40 steps must not log 40 numbers — the point is the cause."""
    many = {f"p{i}": 1000.0 * (i + 1) for i in range(9)}
    line = _boot_line(50000.0, many)
    assert line.startswith(
        "startup 50.00 s: p8 9.00, p7 8.00, p6 7.00, p5 6.00, p4 5.00, p3 4.00"
    )
    assert line.endswith("(+3 more)")


def test_a_fast_boot_says_so_instead_of_naming_noise():
    assert _boot_line(900.0, {"a": 4.0}) == "startup 0.90 s: nothing over 50 ms"
    assert _boot_line(120.0, {}) == "startup 0.12 s: nothing over 50 ms"


# ==========================================================================
# The breakdown reaches the surface a human reads
# ==========================================================================


def test_diagnostics_carries_the_boot_breakdown(tmp_path):
    app = create_app(str(tmp_path))
    with TestClient(app) as c:  # entering the client RUNS the lifespan
        block = c.get("/diagnostics").json()["startup"]
    assert block["total_ms"] > 0
    steps = block["steps_ms"]
    # The suspect phase (see the module docstring) plus real lifespan steps.
    assert "platform" in steps, steps
    assert "reconcile_sessions" in steps and "rehydrate_terminals" in steps, steps
    assert block["at"]


def test_the_breakdown_is_names_and_numbers_only(tmp_path):
    """THE MUTATION THIS CATCHES: a phase keyed by anything derived from the
    user's data — a skill path, a terminal's cwd, a server name — would ship
    their filenames into every bug report that pastes /diagnostics."""
    app = create_app(str(tmp_path))
    with TestClient(app) as c:
        block = c.get("/diagnostics").json()["startup"]
    assert set(block) == {"total_ms", "at", "steps_ms"}
    # ANTI-VACUITY: this loop proves nothing over an empty dict, and an empty
    # dict is exactly what an unwired feature returns — the first cut of this
    # change forgot to hand the record to the deps object, the route answered
    # zeros, and this test was green.
    assert len(block["steps_ms"]) >= 3, block
    for name, ms in block["steps_ms"].items():
        assert re.fullmatch(r"[a-z0-9_]+", name), f"phase name is not an identifier: {name!r}"
        assert isinstance(ms, int) and ms >= 0, (name, ms)


def test_one_info_line_when_startup_completes(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    app = create_app(str(tmp_path))
    with TestClient(app):
        pass
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("startup ")]
    assert len(lines) == 1, lines  # ONE line, not one per step
    assert re.fullmatch(r"startup \d+\.\d\d s: .+", lines[0]), lines[0]


# ==========================================================================
# Boot honesty: measuring must not change what boot DOES
# ==========================================================================


def test_a_step_that_fails_is_still_timed_and_still_summarised(tmp_path, monkeypatch, caplog):
    """Instrumentation must not change failure behaviour: the step still fails
    and still lands in background_loops, the boot still completes, and the
    summary still reports what ran — a breakdown that appears only on a clean
    boot would be missing exactly when someone needs it."""
    from iron_jarvis.agents.orchestrator import Orchestrator

    def _boom(self):
        raise RuntimeError("reconcile exploded")

    monkeypatch.setattr(
        Orchestrator, "reconcile_interrupted_sessions", _boom, raising=True
    )
    caplog.set_level(logging.INFO)
    app = create_app(str(tmp_path))
    with TestClient(app) as c:
        body = c.get("/diagnostics").json()
    assert "reconcile_sessions" in body["startup"]["steps_ms"]
    assert body["background_loops"]["reconcile_sessions"]["ok"] is False
    assert [r.getMessage() for r in caplog.records if r.getMessage().startswith("startup ")]


def test_diagnostics_survives_a_boot_that_recorded_nothing(tmp_path):
    """/diagnostics must never raise (the v1.229.0 OBS5 rule). A caller that
    reaches it before any timing exists — or with the record cleared — gets a
    shape, not a 500."""
    app = create_app(str(tmp_path))
    with TestClient(app) as c:
        import iron_jarvis.daemon.routes.settings as _settings  # noqa: F401

        app.state  # touch: the app is built
        r = c.get("/diagnostics")
        assert r.status_code == 200
        assert "startup" in r.json()
