"""v1.417.0 (C1.3b/C1.7) — a project checker's card says Iron Jarvis asks,
and names the checker; under "Don't ask" it shows no card at all (decision
D1). Driven through the REAL stream lane with the fake ruff on the session's
PATH (tests/test_harness_checkers_v1387)."""

from __future__ import annotations

from iron_jarvis.core import auto_approve as A
from iron_jarvis.harness import checkers as C
from tests._fakes.harness_lane import body, call, stream, wire
from tests.test_harness_checkers_v1387 import _ruff_on, env, finish_note  # noqa: F401 — `env` is a fixture


async def test_a_checker_card_names_iron_jarvis_and_the_checker(env, monkeypatch):  # noqa: F811 — the fixture
    platform, ws, _log = env
    _ruff_on(ws)
    asked: list[dict] = []
    wire(platform, monkeypatch, [[call("write_file", "w1", path="app.py", content="import os\n")], "Done."])
    await stream(platform, body(ws, approval_mode="approve_for_me"), answer=lambda f: (asked.append(f) or "once"))
    cards = [f for f in asked if str(f.get("call_id", "")).startswith(C.CALL_ID_PREFIX)]
    assert len(cards) == 1, asked
    assert cards[0]["asker"] == "iron_jarvis"
    assert cards[0]["why"] == A.checker_why("ruff") == "Iron Jarvis runs the project's own checker ruff"


async def test_under_dont_ask_the_checker_runs_with_no_card(env, monkeypatch):  # noqa: F811 — the fixture
    platform, ws, log = env
    _ruff_on(ws)
    model = wire(platform, monkeypatch, [[call("write_file", "w1", path="app.py", content="import os\n")], "Done."])
    await stream(platform, body(ws, approval_mode="yolo"))  # any card fails the stream helper
    assert log.exists()
    # v1.420.0 (C3.3): the findings reach the finish note.
    assert "Check (ruff): 1 problem in app.py" in finish_note(model)
