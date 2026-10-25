"""v1.378.0 review (IJ harness part 2, Phase 7) — a Build reply's saved steps
say what they touched, in the words the live row used.

The walkthrough's gap 3: after a Build turn, its steps read "Ran · shell",
"Changed · edit_file" — the chat page saves each step's safe target (it works
it out in the browser, ``lib/workTarget``), but a Build session's reply line
is written by the daemon (``routes/build._ReplyWatch``), which saved none,
while the SAME steps read "Ran python", "Changed cart.py" live.
``harness/step_target.py`` is the daemon's twin, held to the table the
TypeScript test writes (``dashboard/__tests__/fixtures/step-target-v1378.json``,
``step-target-twin-v1378.test.ts``, ``IJ_REGEN_STEP_TARGET=1``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from iron_jarvis.harness.step_target import KIND_OF, step_target

FIXTURE = Path(__file__).resolve().parents[1] / "dashboard" / "__tests__" / "fixtures" / "step-target-v1378.json"
TABLE = json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_twin_knows_exactly_the_tools_the_dashboard_words():
    assert sorted(KIND_OF) == TABLE["kinds"]


@pytest.mark.parametrize("case", TABLE["cases"], ids=lambda c: f"{c['name']}:{json.dumps(c['args'])[:40]}")
def test_the_twin_says_what_the_dashboard_says(case):
    assert step_target(case["name"], case["args"]) == case["target"]


def _frame(watch, event: str, data: dict) -> None:
    watch.see(event, json.dumps(data))


def test_a_build_reply_line_saves_each_steps_target_before_its_detail():
    from iron_jarvis.daemon.routes.build import _ReplyWatch

    w = _ReplyWatch()
    calls = [
        ("c1", "read_file", {"path": "C:\\work\\shop\\cart.py"}),
        ("c2", "edit_file", {"path": "C:\\work\\shop\\cart.py", "old": "a", "new": "b"}),
        ("c3", "shell", {"command": "python -m pytest -q"}),
        ("c4", "mcp__gmail__search_messages", {"query": "invoice"}),
    ]
    for cid, name, args in calls:
        _frame(w, "tool_call", {"id": cid, "name": name, "status": "started", "args": args})
        _frame(w, "tool_call", {"id": cid, "name": name, "status": "finished", "ok": True,
                                "detail": {"input": json.dumps(args), "output": "ok"}})
    _frame(w, "token", {"text": "Done."})
    _frame(w, "done", {"reply": "Done."})
    steps = w.steps
    assert [s.get("target") for s in steps] == ["cart.py", "cart.py", "python", None]
    # Saved like the chat page's: the target before the detail, and no key
    # at all when there is nothing safe to say.
    # (v1.390.0, moved on purpose: the Timeline's `start` — ms from the
    # reply's start — sits beside `ms`, before the target and the detail.)
    assert list(steps[0]) == ["name", "ok", "ms", "start", "target", "detail"]
    assert "target" not in steps[3]
    reply = [ln for ln in w.lines("t1", "done") if ln.get("role") == "assistant"][-1]
    assert [s.get("target") for s in reply["steps"]] == ["cart.py", "cart.py", "python", None]


def test_a_step_whose_start_was_not_seen_saves_no_target():
    from iron_jarvis.daemon.routes.build import _ReplyWatch

    w = _ReplyWatch()
    _frame(w, "tool_call", {"id": "x", "name": "read_file", "status": "finished", "ok": True})
    assert w.steps and "target" not in w.steps[0]
