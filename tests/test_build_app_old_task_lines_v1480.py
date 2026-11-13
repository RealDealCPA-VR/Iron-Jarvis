"""v1.481.1 (the owner: "address the risk"): old "A helper …" lines in Build ›
Code read in the words their session's own record supports.

From v1.418.0 to v1.477.1 every task Claude Code said had ended was logged as
"A helper finished / failed / was stopped: <summary>". v1.477.2 fixed new
lines; lines already in a session's log kept the old words. Counted read-only
in the owner's logs: 618 such lines, 549 of them about shell commands.

The command steps here are written by the REAL driver
(``claude.ClaudeDriver``) from Claude Code's frames; the old lines are written
exactly as the old code wrote them (its template and ``one_line(summary,
300)``).
"""

from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import adopt as AD
from iron_jarvis.hosted import claude as C
from iron_jarvis.hosted import history as H
from iron_jarvis.hosted import old_task_lines as OTL
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from iron_jarvis.hosted.events import EventLog, make, one_line

#: The template v1.418.0 .. v1.477.1 wrote (``words.SYS_TASK_DONE`` then).
OLD_TEMPLATE = {
    "completed": "A helper finished: {summary}",
    "failed": "A helper failed: {summary}",
    "killed": "A helper was stopped: {summary}",
    "stopped": "A helper was stopped: {summary}",
}


def old_line(summary: str, status: str = "completed") -> dict:
    """A line the way the old code logged it."""
    return make("notice", text=OLD_TEMPLATE[status].format(summary=one_line(summary, 300)))


def _steps(*commands: tuple[str, dict, str | None]) -> list[dict]:
    """Command steps written by the real driver: (tool id, Bash input, result
    text or None for a step whose ending is not in the list)."""
    got: list[dict] = []
    d = C.ClaudeDriver(write=None, emit=got.append, ask=lambda a: None,
                       on_session_id=lambda s: None, mode="ask", cwd="")
    for i, (tid, inp, result) in enumerate(commands):
        d.on_message({"type": "assistant", "message": {"id": f"m{i}", "content": [
            {"type": "tool_use", "id": tid, "name": "Bash", "input": inp}]}})
        if result is not None:
            d.on_message({"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": tid, "content": result}]}})
    for ev in got:
        ev.pop("_full", None)
    return got


def _rows(tid: str, steps: list[dict]) -> list[dict]:
    return [e for e in steps if e.get("item") == tid]


def _texts(events: list[dict]) -> list[str]:
    return [e["text"] for e in events if e.get("type") == "notice"]


# --------------------------------------------------------------------------- #
# The reading itself
# --------------------------------------------------------------------------- #
def test_an_old_line_about_a_foreground_command_is_not_shown():
    steps = _steps(("t1", {"command": 'cd "C:/work/shop" && grep -n "heading:" lib/nav.ts',
                           "description": "Find the nav headings"}, "lib/nav.ts:4: heading: 'Shop'"))
    events = steps + [old_line("Find the nav headings")]
    shown, hidden = OTL.reword(events)
    assert hidden == 1
    assert _texts(shown) == [], "the command's own step row already shows how it ended"
    assert [e["type"] for e in shown] == ["tool", "tool"], "the step itself stays"


def test_an_old_line_about_a_background_command_names_it_one():
    steps = _steps(("t1", {"command": "npm run dev", "description": "Start the dev server"},
                    "Command running in background with ID: b1. Output is being written to: C:/out.txt"))
    shown, hidden = OTL.reword(steps + [old_line("Start the dev server", "stopped")])
    assert hidden == 0
    assert _texts(shown) == ["A background command was stopped: Start the dev server"]


@pytest.mark.parametrize("result", [
    "Command running in background with ID: b1. Output is being written to: C:/o.txt",
    "Command running in the background on build-box with ID: b1. Output is being written there to: /o",
    "Command was manually backgrounded by user with ID: b1. Output is being written to: C:/o.txt",
    "Command was moved to the background (ID: b1) so that a message that arrived while it was running could be read.",
    "Command did not complete within its 120s timeout and was moved to the background (ID: b1). Output is being written to: C:/o.txt",
])
def test_each_of_claude_codes_background_sentences_is_read_from_the_step(result):
    steps = _steps(("t1", {"command": "npm run build", "description": "Build the site"}, result))
    shown, _ = OTL.reword(steps + [old_line("Build the site")])
    assert _texts(shown) == ["A background command finished: Build the site"]


def test_a_command_whose_input_asked_for_the_background_is_one():
    steps = _steps(("t1", {"command": "npm run dev", "description": "Start the dev server",
                           "run_in_background": True}, "started"))
    shown, _ = OTL.reword(steps + [old_line("Start the dev server")])
    assert _texts(shown) == ["A background command finished: Start the dev server"]


def test_a_command_whose_ending_is_not_in_the_list_is_called_a_command():
    steps = _steps(("t1", {"command": "pytest -q", "description": "Run the tests"}, None))
    shown, hidden = OTL.reword(steps + [old_line("Run the tests", "failed")])
    assert hidden == 0
    assert _texts(shown) == ["A command failed: Run the tests"]


def test_claude_codes_own_sentences_are_said_as_new_lines_say_them():
    events = [old_line('Background command "npm run dev" completed (exit code 0)'),
              old_line('Monitor "Wait for tsc and vitest to finish" stream ended')]
    shown, _ = OTL.reword(events)
    assert _texts(shown) == ['Background command "npm run dev" completed (exit code 0)',
                             'A background task finished: Monitor "Wait for tsc and vitest to finish" stream ended']


@pytest.mark.parametrize("summary,status", [
    ("I fixed the P0 and both P1s on fix/cart; the suite is green.", "completed"),
    ("APPROVED: no. P0 0, P1 2, P2 7.", "completed"),
    ("Agent terminated early due to an API error: overloaded", "failed"),
    ('Background agent "Review the cart" didn\'t finish before the previous session ended', "stopped"),
    ("Orphaned by a previous Claude Code process exit and reported in an aggregate summary.", "stopped"),
])
def test_a_line_no_command_explains_is_left_as_it_was(summary, status):
    """In the owner's logs every such line was a helper's report or Claude
    Code's own words about an agent."""
    steps = _steps(("t1", {"command": "npm test", "description": "Run the tests"}, "ok"))
    before = steps + [old_line(summary, status)]
    shown, hidden = OTL.reword(before)
    assert hidden == 0
    assert _texts(shown) == [OLD_TEMPLATE[status].format(summary=one_line(summary, 300))]


def test_a_description_longer_than_a_title_still_names_its_command():
    desc = "Check every route under the reports folder for a handler that reads the ledger without a lock " * 2
    steps = _steps(("t1", {"command": "rg -n ledger src/reports", "description": desc}, "3 matches"))
    assert steps[0]["title"].endswith("…"), "the title is the description cut at 90"
    shown, hidden = OTL.reword(steps + [old_line(desc)])
    assert hidden == 1 and _texts(shown) == []


def test_a_command_with_no_description_is_named_by_its_command_line():
    cmd = 'cd "C:/work/shop" && npm run lint -- --fix'
    steps = _steps(("t1", {"command": cmd}, "ok"))
    assert steps[0]["title"].startswith("Run ")
    shown, hidden = OTL.reword(steps + [old_line(cmd)])
    assert hidden == 1 and _texts(shown) == []


def test_only_the_first_line_of_a_multi_line_command_is_compared():
    cmd = "python - <<'EOF'\nprint('hello')\nEOF"
    steps = _steps(("t1", {"command": cmd}, "hello"))
    shown, hidden = OTL.reword(steps + [old_line(cmd)])
    assert hidden == 1 and _texts(shown) == []


def test_the_nearest_command_before_the_line_decides():
    bg = "Command running in background with ID: b1. Output is being written to: C:/o.txt"
    first = _steps(("t1", {"command": "npm run dev", "description": "Start the server"}, bg))
    second = _steps(("t2", {"command": "npm run dev -- --port 4000", "description": "Start the server"}, "listening"))
    events = first + [old_line("Start the server")] + second + [old_line("Start the server")]
    shown, hidden = OTL.reword(events)
    assert hidden == 1
    assert _texts(shown) == ["A background command finished: Start the server"]


def test_a_line_before_any_command_is_not_matched_to_a_later_one():
    steps = _steps(("t1", {"command": "npm test", "description": "Run the tests"}, "ok"))
    shown, hidden = OTL.reword([old_line("Run the tests")] + steps)
    assert hidden == 0
    assert _texts(shown) == ["A helper finished: Run the tests"]


def test_lines_written_by_todays_code_are_left_alone():
    events = [make("notice", text="A background command finished: Start the dev server"),
              make("notice", text="A command finished: Run the tests"),
              make("notice", text="A background task finished: Monitor \"x\" stream ended"),
              make("notice", text="A helper finished: The cart total is fixed.")]
    before = copy.deepcopy(events)
    shown, hidden = OTL.reword(events)
    assert shown == before and hidden == 0


def test_nothing_given_is_changed_and_a_list_with_no_old_line_is_handed_back():
    steps = _steps(("t1", {"command": "npm run dev", "description": "Start"},
                    "Command running in background with ID: b1."))
    events = steps + [old_line("Start")]
    before = copy.deepcopy(events)
    shown, _ = OTL.reword(events)
    assert events == before, "the live log's own dicts are never changed"
    assert shown[-1] is not events[-1]
    plain = steps + [make("notice", text="Claude Code compacted the conversation to make room.")]
    assert OTL.reword(plain) == (plain, 0)
    assert OTL.reword(plain)[0] is plain


def test_the_whole_shape_of_an_old_session():
    """A turn as an old log holds it: helpers' reports and commands' ends mixed."""
    bg = "Command running in background with ID: b9. Output is being written to: C:/o.txt"
    s1 = _steps(("t1", {"command": "npm test", "description": "Run the unit tests"}, "41 passed"))
    s2 = _steps(("t2", {"command": "npm run dev", "description": "Start the dev server"}, bg))
    s3 = _steps(("t3", {"command": 'cd "C:/w" && git status', "description": "See what changed"}, "clean"))
    events = (s1 + [old_line("Run the unit tests")]
              + s2 + [old_line("I fixed both P1s on fix/cart.")]
              + s3 + [old_line("See what changed"), old_line("Start the dev server", "stopped"),
                      old_line('Background command "npm run build" completed (exit code 0)')])
    shown, hidden = OTL.reword(events)
    assert hidden == 2
    assert _texts(shown) == [
        "A helper finished: I fixed both P1s on fix/cart.",
        "A background command was stopped: Start the dev server",
        'Background command "npm run build" completed (exit code 0)',
    ]
    assert not any("helper" in t.lower() and "command" in t.lower() for t in _texts(shown))


# --------------------------------------------------------------------------- #
# Where history is served: the view, "Load earlier", a rejoined reply
# --------------------------------------------------------------------------- #
@pytest.fixture
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


def _write_log(svc: S.HostedService, sid: str, events: list[dict]) -> None:
    """Through the real log writer (masking and cutting as in production)."""
    log = EventLog(svc.home, sid)
    for ev in events:
        log.append(dict(ev))
    log.flush()


async def test_a_sessions_view_reads_old_lines_the_new_way_and_counts_true(app, tmp_path):
    svc = S.service_for(app.state.platform)
    work = tmp_path / "work"
    work.mkdir()
    row = store.create(app.state.platform.engine, harness="claude-code", folder=str(work))
    s1 = _steps(("t1", {"command": "npm test", "description": "Run the unit tests"}, "41 passed"))
    s2 = _steps(("t2", {"command": "npm run dev", "description": "Start the dev server"},
                 "Command running in background with ID: b9."))
    await asyncio.to_thread(_write_log, svc, row.id, [make("user", text="fix the cart")] + s1
                            + [old_line("Run the unit tests")] + s2
                            + [old_line("Start the dev server", "stopped"),
                               old_line("I fixed the cart total.")])
    view = await svc.view(row.id)
    assert _texts(view["events"]) == ["A background command was stopped: Start the dev server",
                                      "A helper finished: I fixed the cart total."]
    assert view["events_total"] == len(view["events"]), "no 'earlier steps' that are not there"
    assert view["older"] is None
    # The file itself is untouched.
    raw = (S.session_dir(svc.home, row.id) / "events.jsonl").read_text("utf-8")
    assert "A helper finished: Run the unit tests" in raw


async def test_load_earlier_reads_old_lines_the_same_way(app, tmp_path):
    svc = S.service_for(app.state.platform)
    work = tmp_path / "work"
    work.mkdir()
    row = store.create(app.state.platform.engine, harness="claude-code", folder=str(work))
    s1 = _steps(("t1", {"command": "npm test", "description": "Run the unit tests"}, "41 passed"))
    filler = [make("user", text=f"message {i}") for i in range(30)]
    await asyncio.to_thread(_write_log, svc, row.id, s1 + [old_line("Run the unit tests"),
                                                          old_line('Monitor "Watch the build" stream ended')] + filler)
    view = await svc.view(row.id, limit=10)
    assert view["older"]
    page = await svc.events_page(row.id, view["older"], limit=500)
    assert _texts(page["events"]) == ['A background task finished: Monitor "Watch the build" stream ended']
    assert [e["type"] for e in page["events"]][:2] == ["tool", "tool"]


def test_a_command_just_before_what_is_served_is_read_but_never_shown():
    """In the owner's logs 4 of the 311 old lines a view showed named a
    command that sat just before the view's first event."""
    steps = _steps(("t1", {"command": "npm test", "description": "Run the unit tests"}, "41 passed"))
    line = old_line("Run the unit tests")
    shown, hidden = OTL.reword([line, make("user", text="next")], steps)
    assert hidden == 1
    assert [(e["type"], e.get("text")) for e in shown] == [("user", "next")], "the context is never returned"


def _log_with_line_at_the_window_start(home, sid: str) -> tuple[EventLog, int]:
    """A log whose newest ``n`` events start with an old line about a command
    that sits just before them."""
    steps = _steps(("t1", {"command": "npm test", "description": "Run the unit tests"}, "41 passed"))
    early = [make("user", text=f"early {i}") for i in range(20)]
    later = [make("user", text=f"later {i}") for i in range(8)]
    log = EventLog(home, sid)
    for ev in early + steps + [old_line("Run the unit tests")] + later:
        log.append(dict(ev))
    log.flush()
    return log, 1 + len(later)


def test_the_view_from_a_file_reads_the_record_just_before_it(tmp_path):
    log, n = _log_with_line_at_the_window_start(tmp_path / "home", "s1")
    got = H.file_view(log.path, log.meta_path, n)
    assert [e["text"] for e in got["events"]] == [f"later {i}" for i in range(8)]
    assert got["older"], "older events are still there to load"
    total = len(H._read_events(log.path))
    assert got["events_total"] == total - 1


def test_load_earlier_reads_the_record_just_before_its_page(tmp_path):
    log, n = _log_with_line_at_the_window_start(tmp_path / "home", "s1")
    events = H._read_events(log.path)
    # The page that ends where the newest 4 'later' events begin.
    first_later = next(i for i, e in enumerate(events) if e.get("text") == "later 4")
    cursor = H.cursor_for(events[first_later], first_later, 0)
    page = H.read_page(log.path, log.meta_path, cursor, n - 4)
    assert [e["text"] for e in page["events"]] == [f"later {i}" for i in range(4)]


def test_the_view_from_memory_reads_the_record_just_before_it(tmp_path):
    log, n = _log_with_line_at_the_window_start(tmp_path / "home", "s1")
    tail = H.TailLog(tmp_path / "home", "s1")
    tail.seed()
    got = tail.served(n)
    assert [e["text"] for e in got["events"]] == [f"later {i}" for i in range(8)]
    assert got["events_total"] == tail.prior_total - 1


def test_the_view_from_memory_reads_what_its_seed_held_before_the_kept_events(tmp_path):
    """Memory keeps the newest ``keep`` events of the file; the seed also keeps
    the events just before those, for this reading alone."""
    log, n = _log_with_line_at_the_window_start(tmp_path / "home", "s1")
    tail = H.TailLog(tmp_path / "home", "s1", keep=n)
    tail.seed()
    assert len(tail.prior) == n and any(e.get("type") == "tool" for e in tail.context)
    got = tail.served(n)
    assert [e["text"] for e in got["events"]] == [f"later {i}" for i in range(8)]


async def test_a_reply_rejoined_after_a_restart_replays_old_lines_the_same_way():
    s1 = _steps(("t1", {"command": "npm test", "description": "Run the unit tests"}, "41 passed"))
    logged = [{**e, "turn": "turn-7"} for e in
              [make("user", text="fix it")] + s1 + [old_line("Run the unit tests"), old_line("I fixed it.")]]

    class Log:
        def read(self, limit, turn=None):
            return [e for e in logged if e.get("turn") == turn], len(logged), 0

    svc = SimpleNamespace(clock=lambda: 100.0, turns={})
    live = SimpleNamespace(log=Log(), sid="s1", turn=None)
    await AD._turn_back(svc, live, "turn-7", "2026-11-01T10:00:00Z")
    assert _texts(live.turn.events) == ["A helper finished: I fixed it."]
    assert {e["title"] for e in live.turn.events if e["type"] == "tool"} == {"Run the unit tests"}, \
        "the step itself (its running and finished rows) is still there"
    assert not json.dumps(live.turn.events).count("A helper finished: Run the unit tests")
