"""v1.414.0 — the desktop app says Build › Code's work too.

The update's busy warning (``describeBusyWork``, lifted from desktop/main.js
and run under node) names a Build › Code reply by its tool (the daemon's
``/system/activity`` ``code_replies`` + ``code_tools``); the tray's
run-outcome notice for a Build › Code ``build.turn_finished`` opens that
session in Build › Code (the one-notice fixture holds the words equal with
the bell; this pins the link stands for every build_app id spelling).
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.test_desktop_reliability_v1249 import _describe, _lift, _run, requires_node

_NOTICE = """
__LIFTED__
const cases = JSON.parse(process.argv[2]);
process.stdout.write(JSON.stringify({ out: cases.map((c) => runOutcomeNotice(c)) }) + "\\n");
"""


@requires_node
def test_the_update_warning_names_a_build_code_reply_by_its_tool(tmp_path):
    said = _describe(
        [
            {"code_replies": 1, "code_tools": ["Claude Code"], "busy": True},
            {"code_replies": 2, "code_tools": ["Codex", "Pi", "Codex"], "busy": True},
            {"code_replies": 1, "busy": True},
        ],
        tmp_path,
    )
    assert said[0] == "1 coding session in Build › Code working (Claude Code)"
    assert said[1] == "2 coding sessions in Build › Code working (Codex, Pi)"
    assert said[2] == "1 coding session in Build › Code working"


@requires_node
def test_the_tray_notice_opens_a_build_code_session_in_build(tmp_path):
    script = _NOTICE.replace("__LIFTED__", _lift("function runOutcomePlainText", "function runOutcomeNotice"))
    out = _run(script, tmp_path, "notice", json.dumps([
        {"id": "e1", "type": "build.turn_finished", "session_id": "build_app:app_9", "ts": "2026-10-30T10:00:00",
         "payload": {"thread_id": "app_9", "surface": "build_app", "title": "Fix it", "seconds": 75, "outcome": "failed"}},
        # No thread id on the payload: the session id names it.
        {"id": "e2", "type": "build.turn_finished", "session_id": "build_app:app_8", "ts": "2026-10-30T10:00:00",
         "payload": {"title": "Fix it", "seconds": 75, "outcome": "done"}},
        # A Chat › Code finish keeps its own link.
        {"id": "e3", "type": "build.turn_finished", "session_id": "build:bs_1", "ts": "2026-10-30T10:00:00",
         "payload": {"thread_id": "bs_1", "title": "Fix it", "seconds": 75, "outcome": "done"}},
    ]))["out"]
    assert out[0]["href"] == "/terminals?view=code&code=app_9"
    assert out[0]["title"] == "Your coding session could not finish"
    assert out[1]["href"] == "/terminals?view=code&code=app_8"
    assert out[2]["href"] == "/chat?view=code&session=bs_1"
