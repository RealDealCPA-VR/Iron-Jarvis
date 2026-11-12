"""v1.477.2 (a USER REPORT, a screenshot of a wall of grey lines): Build › Code
called EVERY task Claude Code said had ended "a helper" — "A helper finished:
cd "C:/…" && grep …". Those were not helpers finishing. They were shell
COMMANDS, and the text after the colon was the command line.

What Claude Code really sends (READ from the 2.1.322 program on this PC,
read-only; the frame shapes are the ones recorded for 2.1.316 in
``tests/fixtures/build_app_v1409/claude_full_mode_edit_v1418.json``):

* ``task_started`` carries ``task_type`` (``local_bash`` for a shell command,
  ``local_agent`` / ``local_workflow`` / ``remote_agent`` /
  ``in_process_teammate`` for a helper) and ``is_backgrounded``.
  ``task_notification`` carries NEITHER, so what ended is read from the start.
* A command that runs in the FOREGROUND for two seconds is tracked as a task
  too (``is_backgrounded: false``); when it ends there, its notification's
  ``summary`` is the command line (or the command's own description). That
  command already has its step row on the screen.
* A command sent to the background later says so (``task_updated`` with
  ``patch.is_backgrounded: true``, and ``background_tasks_changed`` lists it);
  when it ends, Claude Code's summary is its own sentence, ``Background command
  "…" completed (exit code 0)``. A background command that is STOPPED sends
  only its description.

These pins drive the REAL driver with those frames.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import claude as C
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import words as W
from tests._build_app_helpers_v1409 import FIX, client, frames, new_session, stop_all, use_fake

COMMAND_LINE = 'cd "C:/work/shop" && grep -n "heading:" lib/nav.ts'


def _driver(got: list) -> C.ClaudeDriver:
    return C.ClaudeDriver(write=None, emit=got.append, ask=lambda a: got.append(a.event),
                          on_session_id=lambda s: None, mode="ask", cwd="")


def _task(d: C.ClaudeDriver, sub: str, tid: str, **extra) -> None:
    d.on_message({"type": "system", "subtype": sub, "task_id": tid, **extra})


#: "Claude Code started a background task: …" (v1.418.0) is said for a
#: background task with no step of its own; these pins read how tasks ENDED.
STARTED = W.SYS_TASK_STARTED.split("{")[0]


def _notices(got: list) -> list[str]:
    return [e["text"] for e in got if e.get("type") == "notice" and not e["text"].startswith(STARTED)]


# --------------------------------------------------------------------------- #
# The report itself: a command is never called a helper
# --------------------------------------------------------------------------- #
def test_an_ended_background_command_and_an_ended_helper_read_as_what_they_were():
    got: list = []
    d = _driver(got)
    _task(d, "task_started", "sh1", task_type="local_bash", is_backgrounded=True, tool_use_id="toolu_sh1",
          description="npm run dev")
    _task(d, "task_started", "ag1", task_type="local_agent", is_backgrounded=True, tool_use_id="toolu_ag1",
          description="Fix the cart total")
    # A background command that is stopped sends only its description.
    _task(d, "task_notification", "sh1", status="stopped", summary="npm run dev", tool_use_id="toolu_sh1")
    _task(d, "task_notification", "ag1", status="completed", summary="The cart total is fixed.",
          tool_use_id="toolu_ag1")
    said = _notices(got)
    assert said == ["A background command was stopped: npm run dev",
                    "A helper finished: The cart total is fixed."]
    assert "helper" not in said[0].lower(), "a command is never called a helper"


@pytest.mark.parametrize("status,words", [
    ("completed", "A background command finished: build the site"),
    ("failed", "A background command failed: build the site"),
    ("killed", "A background command was stopped: build the site"),
    ("stopped", "A background command was stopped: build the site"),
])
def test_each_way_a_background_command_ends_has_its_own_words(status, words):
    got: list = []
    d = _driver(got)
    _task(d, "task_started", "sh1", task_type="local_bash", is_backgrounded=True)
    _task(d, "task_notification", "sh1", status=status, summary="build the site")
    assert _notices(got) == [words]


@pytest.mark.parametrize("status,words", [
    ("completed", "A helper finished: built it"),
    ("failed", "A helper failed: built it"),
    ("killed", "A helper was stopped: built it"),
    ("stopped", "A helper was stopped: built it"),
])
@pytest.mark.parametrize("task_type", sorted(C.HELPER_TASK_TYPES))
def test_every_helper_type_keeps_the_helper_words(task_type, status, words):
    got: list = []
    d = _driver(got)
    _task(d, "task_started", "ag1", task_type=task_type, is_backgrounded=True)
    _task(d, "task_notification", "ag1", status=status, summary="built it")
    assert _notices(got) == [words]


def test_claude_codes_own_sentence_for_a_background_command_is_said_once():
    """Its summary already says "Background command … completed (exit code
    0)": said as it is, never "A background command finished: Background
    command …"."""
    got: list = []
    d = _driver(got)
    _task(d, "task_started", "sh1", task_type="local_bash", is_backgrounded=True)
    own = 'Background command "Run the whole test suite" completed (exit code 0)'
    _task(d, "task_notification", "sh1", status="completed", summary=own)
    _task(d, "task_started", "sh2", task_type="local_bash", is_backgrounded=True)
    failed = 'Background command "Run the whole test suite" failed with exit code 1'
    _task(d, "task_notification", "sh2", status="failed", summary=failed)
    assert _notices(got) == [own, failed]


def test_a_helper_whose_answer_starts_with_those_words_is_still_a_helper():
    got: list = []
    d = _driver(got)
    _task(d, "task_started", "ag1", task_type="local_agent", is_backgrounded=True)
    _task(d, "task_notification", "ag1", status="completed", summary="Background command syntax is documented now.")
    assert _notices(got) == ["A helper finished: Background command syntax is documented now."]


# --------------------------------------------------------------------------- #
# A command that ran in the foreground: its own step row says how it ended
# --------------------------------------------------------------------------- #
def test_a_foreground_command_with_its_own_step_says_nothing_more():
    """The wall in the screenshot: 121 of these in one session."""
    got: list = []
    d = _driver(got)
    for n, status in enumerate(["completed", "failed", "stopped"]):
        tid = f"fg{n}"
        _task(d, "task_started", tid, task_type="local_bash", is_backgrounded=False, tool_use_id=f"toolu_{n}",
              description=COMMAND_LINE)
        assert d.background_tasks == 1, "still counted while it runs (v1.417.0)"
        _task(d, "task_notification", tid, status=status, summary=COMMAND_LINE, tool_use_id=f"toolu_{n}")
        assert d.background_tasks == 0
    assert _notices(got) == [], "the command's own step row already shows how it ended"


def test_a_foreground_command_with_no_step_of_its_own_is_still_said_as_a_command():
    got: list = []
    d = _driver(got)
    _task(d, "task_started", "fg1", task_type="local_bash", is_backgrounded=False)
    _task(d, "task_notification", "fg1", status="completed", summary=COMMAND_LINE)
    assert _notices(got) == [f"A command finished: {COMMAND_LINE}"]


def test_a_command_sent_to_the_background_later_ends_as_a_background_command():
    got: list = []
    d = _driver(got)
    _task(d, "task_started", "sh1", task_type="local_bash", is_backgrounded=False, tool_use_id="toolu_1",
          description="npm run build")
    _task(d, "task_updated", "sh1", patch={"is_backgrounded": True})
    assert _notices(got) == [], "the update itself says nothing, as before"
    _task(d, "task_notification", "sh1", status="stopped", summary="npm run build", tool_use_id="toolu_1")
    assert _notices(got) == ["A background command was stopped: npm run build"]


def test_claude_codes_list_of_background_tasks_marks_a_command_as_in_the_background():
    got: list = []
    d = _driver(got)
    _task(d, "task_started", "sh1", task_type="local_bash", is_backgrounded=False, tool_use_id="toolu_1")
    d.on_message({"type": "system", "subtype": "background_tasks_changed",
                  "tasks": [{"task_id": "sh1", "task_type": "local_bash", "description": "npm run dev"},
                            {"task_id": "ghost", "task_type": "local_bash", "description": "x"}]})
    _task(d, "task_notification", "sh1", status="stopped", summary="npm run dev", tool_use_id="toolu_1")
    assert _notices(got) == ["A background command was stopped: npm run dev"]


def test_a_command_whose_place_was_never_said_is_said_and_never_guessed():
    """A start with no ``is_backgrounded`` at all (a task kept from before
    this version): not known to be in the foreground, so it is not kept
    quiet, and not known to be in the background, so it is not called that."""
    got: list = []
    d = _driver(got)
    _task(d, "task_started", "sh1", task_type="local_bash", tool_use_id="toolu_1")
    _task(d, "task_notification", "sh1", status="completed", summary=COMMAND_LINE, tool_use_id="toolu_1")
    assert _notices(got) == [f"A command finished: {COMMAND_LINE}"]


# --------------------------------------------------------------------------- #
# Anything else, and a task whose start was never seen
# --------------------------------------------------------------------------- #
def test_another_kind_of_task_is_a_task():
    got: list = []
    d = _driver(got)
    _task(d, "task_started", "mon1", task_type="monitor_mcp", is_backgrounded=True)
    _task(d, "task_notification", "mon1", status="completed", summary='Monitor "Wait for the build" stream ended')
    _task(d, "task_started", "x1", task_type="")
    _task(d, "task_notification", "x1", status="failed", summary="it broke")
    assert _notices(got) == ['A background task finished: Monitor "Wait for the build" stream ended',
                             "A task failed: it broke"]


def test_a_task_whose_start_was_never_seen_is_a_task_never_a_helper():
    got: list = []
    d = _driver(got)
    _task(d, "task_notification", "lost", status="completed", summary=COMMAND_LINE, tool_use_id="toolu_9")
    _task(d, "task_notification", "lost2", status="completed",
          summary='Background command "Run the tests" completed (exit code 0)')
    assert _notices(got) == [f"A task finished: {COMMAND_LINE}",
                             'Background command "Run the tests" completed (exit code 0)']


def test_the_pure_function_says_a_task_when_it_is_told_nothing():
    frame = {"type": "system", "subtype": "task_notification", "status": "completed", "summary": "created"}
    assert C.system_notice(frame) == "A task finished: created"
    assert C.system_notice(frame, task_type="local_agent") == "A helper finished: created"
    assert C.system_notice(frame, task_type="local_bash", background=True) == "A background command finished: created"
    assert C.system_notice(frame, task_type="local_bash", background=None) == "A command finished: created"
    assert C.system_notice({**frame, "tool_use_id": "T"}, task_type="local_bash", background=False) == ""
    assert C.system_notice({**frame, "status": "running"}, task_type="local_agent") == "", "no known ending: nothing"
    assert C.system_notice({**frame, "summary": ""}, task_type="local_agent") == "", "nothing to say: nothing"


def test_every_subject_and_ending_is_worded():
    assert set(W.SYS_TASK_ENDED) == {"completed", "failed", "killed", "stopped"}
    for subject in W.SYS_TASK_SUBJECT.values():
        for ending in W.SYS_TASK_ENDED.values():
            line = W.SYS_TASK_DONE.format(who=subject, ended=ending, summary="x")
            assert line.endswith(": x") and line[0] == "A"
    assert [s for s in W.SYS_TASK_SUBJECT.values() if "helper" in s.lower()] == ["A helper"]


# --------------------------------------------------------------------------- #
# The bookkeeping around it is unchanged
# --------------------------------------------------------------------------- #
def test_the_place_is_forgotten_with_the_task_and_kept_across_a_restart():
    got: list = []
    d = _driver(got)
    _task(d, "task_started", "sh1", task_type="local_bash", is_backgrounded=True)
    _task(d, "task_started", "fg1", task_type="local_bash", is_backgrounded=False, tool_use_id="toolu_1")
    kept = json.loads(json.dumps(d.state()))
    later: list = []
    d2 = _driver(later)
    d2.restore(kept)
    _task(d2, "task_notification", "sh1", status="stopped", summary="npm run dev")
    _task(d2, "task_notification", "fg1", status="completed", summary=COMMAND_LINE, tool_use_id="toolu_1")
    assert _notices(later) == ["A background command was stopped: npm run dev"]
    assert d2._task_place == {}, "nothing is kept for a task that ended"


def test_a_state_kept_by_an_older_version_never_hides_a_background_command():
    """A session the code host kept across the update has no place recorded
    for its open tasks: their ends are said, not kept quiet."""
    got: list = []
    d = _driver(got)
    _task(d, "task_started", "sh1", task_type="local_bash", is_backgrounded=True, tool_use_id="toolu_1")
    kept = json.loads(json.dumps(d.state()))
    kept.pop("_task_place")
    later: list = []
    d2 = _driver(later)
    d2.restore(kept)
    _task(d2, "task_notification", "sh1", status="stopped", summary="npm run dev", tool_use_id="toolu_1")
    assert _notices(later) == ["A command was stopped: npm run dev"]


def test_the_tools_exit_forgets_every_place():
    d = _driver([])
    _task(d, "task_started", "sh1", task_type="local_bash", is_backgrounded=True)
    _task(d, "task_started", "fg1", task_type="local_bash", is_backgrounded=False, tool_use_id="toolu_1")
    assert d._task_place == {"sh1": True, "fg1": False}
    d.on_exit()
    assert d._task_place == {} and d.background_tasks == 0


def test_a_place_is_kept_only_for_a_task_that_is_open():
    """An update or a list entry for a task Iron Jarvis is not tracking adds
    nothing (the table must not grow with tasks that never end here)."""
    d = _driver([])
    _task(d, "task_updated", "ghost", patch={"is_backgrounded": True})
    d.on_message({"type": "system", "subtype": "background_tasks_changed",
                  "tasks": [{"task_id": "ghost2", "task_type": "local_bash", "description": "x"}]})
    assert d._task_place == {}


def test_a_task_the_user_stopped_from_the_session_tab_is_still_not_said_twice():
    got: list = []
    d = _driver(got)
    _task(d, "task_started", "sh1", task_type="local_bash", is_backgrounded=True)
    d._stopping.add("sh1")
    _task(d, "task_notification", "sh1", status="stopped", summary="npm run dev")
    assert _notices(got) == [] and d._task_place == {}


def test_the_recorded_helper_run_still_reads_a_helper_finished():
    """The one real run recorded for v1.418.0 (a helper agent in the
    background): its words did not move."""
    got: list = []
    d = _driver(got)
    steps = json.loads((FIX / "claude_full_mode_edit_v1418.json").read_text(encoding="utf-8"))["steps"]
    for s in steps:
        if "out" in s and "reply_to_control" not in s:
            d.on_message(s["out"])
    assert [t for t in _notices(got) if "finished" in t] == ["A helper finished: created"]


# --------------------------------------------------------------------------- #
# Through the real service: what the session's record holds
# --------------------------------------------------------------------------- #
@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


async def test_the_sessions_record_holds_the_new_words(tmp_path, work, monkeypatch):
    app = create_app(str(tmp_path / "home"))
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work)
        r = await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "build it", "turn_id": "t1"})
        assert frames(r.text)[-1]["type"] == "done"
        drv = svc.live[s["id"]].driver
        _task(drv, "task_started", "fg1", task_type="local_bash", is_backgrounded=False, tool_use_id="toolu_1",
              description=COMMAND_LINE)
        _task(drv, "task_notification", "fg1", status="completed", summary=COMMAND_LINE, tool_use_id="toolu_1")
        _task(drv, "task_started", "sh1", task_type="local_bash", is_backgrounded=True, description="npm run dev")
        _task(drv, "task_notification", "sh1", status="stopped", summary="npm run dev")
        _task(drv, "task_started", "ag1", task_type="local_agent", is_backgrounded=True)
        _task(drv, "task_notification", "ag1", status="completed", summary="The cart total is fixed.")
        view = (await c.get(f"/build/app/sessions/{s['id']}")).json()
        said = [e["text"] for e in view["events"] if e["type"] == "notice" and ("helper" in e["text"].lower()
                                                                               or "command" in e["text"].lower())]
        assert said == ["A background command was stopped: npm run dev",
                        "A helper finished: The cart total is fixed."]
    await stop_all(app)
