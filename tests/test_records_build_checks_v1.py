"""22-a (R1.9): Iron Jarvis's check after each Build › Code reply, from chat
(``settings/records_build``): "stop running tests after replies here" turns it
off for the folder on a card with an Undo; "did the tests pass on Codex's last
reply?" is answered from Iron Jarvis's OWN record in the session's log.

Arming goes through the functions both chat lanes call (``_lanes``); the
round trip goes through the REAL stream lane with a scripted model.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import folder_defaults as FD
from iron_jarvis.hosted import store
from iron_jarvis.hosted.events import EventLog, make
from iron_jarvis.settings import records as R
from iron_jarvis.settings import records_build as RB
from tests.test_chat_approvals_v1187 import _drive_stream
from tests.test_records_build_v14380 import _undo
from tests.test_records_projects_v14350 import _lanes
from tests.test_settings_chat_tools_v1321 import _body
from tests.test_settings_records_v1321 import _app, _approve_all, _card

TOOLS = frozenset(RB.CHECK_TOOLS)

REQUESTS = [
    "stop running tests after replies here",
    "Stop running the tests after each reply in D:\\code\\shop",
    "don't run tests after replies in this repo",
    "turn off the check after each reply in my shop folder",
    "turn on the check after every reply in this folder",
    "run the tests after each reply in this repo with pytest -q",
    "always run my tests after Codex replies",
    "after each reply, run the tests in this folder",
    "please stop running tests after every coding reply",
    "disable the tests after replies for this repo",
]
QUESTIONS = [
    "did the tests pass on Codex's last reply?",
    "Did the tests pass after the last reply?",
    "were the tests green on Claude Code's reply?",
    "what did Iron Jarvis's check say about the last reply?",
]
NEAR_MISSES = [
    "run the tests",
    "I ran the tests after lunch",
    "stop the tests",
    "what tests should I write for the parser?",
    "after each reply say thanks",
    "the check arrived after the reply from the bank",
    "can you test my reply to the landlord?",
    "stop running",
    "reply to the email after you check the calendar",
    "my kid's tests are after school",
]


@pytest.fixture(scope="module")
def d(tmp_path_factory):
    app = create_app(str(tmp_path_factory.mktemp("checks") / "home"))
    return SimpleNamespace(platform=app.state.platform)


def test_a_request_offers_the_switch_in_the_stream_lane_and_a_question_only_the_reader(d):
    bad = []
    for text in REQUESTS:
        armed, ask = _lanes(d, text)
        if "build_checks_set" not in ask or set(armed) & {"build_checks_set"}:
            bad.append(("request", text, sorted(set(ask) & TOOLS)))
    for text in QUESTIONS:
        armed, ask = _lanes(d, text)
        if "coding_reply_check" not in set(ask) | set(armed) or "build_checks_set" in set(ask) | set(armed):
            bad.append(("question", text, sorted((set(ask) | set(armed)) & TOOLS)))
    for text in NEAR_MISSES:
        armed, ask = _lanes(d, text)
        if (set(ask) | set(armed)) & TOOLS:
            bad.append(("near miss", text, sorted((set(ask) | set(armed)) & TOOLS)))
    assert bad == []


def test_the_patterns_are_linear():
    code = textwrap.dedent(
        """
        from iron_jarvis.settings.records_build import build_tools_for
        N = 40_000
        for c in ("stop running tests after " + " " * N + "replies", "after" + " " * N + "each reply run tests",
                  "did " + "x " * N + "tests pass on the reply", "after each " * 4000 + "reply run tests",
                  "stop " * 8000 + "tests after replies"):
            build_tools_for(c)
        print("ok")
        """
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert out.stdout.strip() == "ok", out.stderr


def test_the_two_are_registered_with_their_tiers_and_kept_from_low_trust(tmp_path):
    from iron_jarvis.core.trust import LOW_TRUST_DENY

    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    for name in TOOLS:
        assert p.registry.get(name) is not None, name
        assert R.RECORD_TOOL_TIERS[name] == RB.TIERS[name], name
        assert p.permissions.authorize(name, {}).allowed is (name in RB.READERS), name
        assert (name in LOW_TRUST_DENY) is (name == "build_checks_set"), name


@pytest.mark.asyncio
async def test_off_from_chat_asks_on_a_card_and_undo_puts_back_what_the_folder_had(tmp_path):
    folder = tmp_path / "shop"
    folder.mkdir()
    app = _app(tmp_path / "home", [("build_checks_set", {"folder": str(folder), "run": False})])
    p = app.state.platform
    FD.save_checks(p.engine, str(folder), {"command": "npm test", "confirmed_at": "x", "source": "card"})
    seen: list[str] = []
    frames = await _drive_stream(app, _body(f"stop running tests after replies in {folder}"),
                                 await _approve_all(app, seen))
    assert seen == ["build_checks_set"], "turning the check off asks first"
    assert FD.read_checks(p.engine, str(folder))["off"] is True
    card = _card(frames, "Check after each reply")
    assert card["old"].startswith("on: `npm test`") and card["new"] == "off"
    out = await _undo(app, card)
    assert "put back" in out["output"]
    assert FD.read_checks(p.engine, str(folder))["command"] == "npm test"


@pytest.mark.asyncio
async def test_on_from_chat_keeps_the_named_command_and_undo_refuses_over_a_newer_answer(tmp_path):
    folder = tmp_path / "shop"
    folder.mkdir()
    app = _app(tmp_path / "home", [("build_checks_set", {"folder": str(folder), "run": True,
                                                          "command": "python -m pytest -q"})])
    p = app.state.platform
    frames = await _drive_stream(app, _body(f"run the tests after each reply in {folder} with python -m pytest -q"),
                                 await _approve_all(app, []))
    kept = FD.read_checks(p.engine, str(folder))
    assert kept["command"] == "python -m pytest -q" and kept["source"] == "chat"
    card = _card(frames, "Check after each reply")
    # The user answered the card under a reply since: Undo leaves it.
    FD.save_checks(p.engine, str(folder), {"off": True, "at": "later"})
    refused = await _undo(app, card, status=409)
    assert "changed again" in refused["detail"]
    assert FD.read_checks(p.engine, str(folder)) == {"off": True, "at": "later"}


@pytest.mark.asyncio
async def test_on_with_no_command_and_nothing_proposed_is_refused_in_words(tmp_path):
    folder = tmp_path / "shop"
    folder.mkdir()
    app = _app(tmp_path / "home", [("build_checks_set", {"folder": str(folder), "run": True})])
    tool = next(t for t in RB.build_record_tools(app.state.platform) if t.name == "build_checks_set")
    res = await tool.execute({"folder": str(folder), "run": True}, None)
    assert not res.ok and "found no test command" in res.error
    hdir = folder / ".ironjarvis" / "harness"
    hdir.mkdir(parents=True)
    (hdir / "finish.toml").write_text("tests = false\n", encoding="utf-8")
    res = await tool.execute({"folder": str(folder), "run": True, "command": "npm test"}, None)
    assert not res.ok and "finish.toml" in res.error
    assert FD.read_checks(app.state.platform.engine, str(folder)) is None


@pytest.mark.asyncio
async def test_the_reader_answers_from_iron_jarvis_s_own_record(tmp_path):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    work = tmp_path / "work"
    work.mkdir()
    row = store.create(p.engine, harness="codex", folder=str(work))
    store.update(p.engine, row.id, title="rate limit")
    tool = next(t for t in RB.build_record_tools(p) if t.name == "coding_reply_check")
    none_yet = await tool.execute({"session": "Codex"}, None)
    assert none_yet.ok and "has not checked any of its replies yet" in none_yet.output
    log = EventLog(p.config.home, row.id)
    log.append(make("text_done", item="a1", text="All 14 tests pass!", turn="t1"))
    log.append(make("proof", for_turn="t1", state="done", tests="failed", why="ran", command="npm test", ran=14,
                    passed=13, failed=1, by="iron_jarvis",
                    line="Checked by Iron Jarvis: 14 tests ran, 13 passed."))
    log.flush()
    log.close()
    got = await tool.execute({"session": "codex"}, None)
    assert got.ok and "Checked by Iron Jarvis: 14 tests ran, 13 passed." in got.output
    assert "All 14 tests pass" not in got.output, "the tool's own words are never the answer"
    assert got.data["check"]["tests"] == "failed"
    nothing = await tool.execute({"session": "grok"}, None)
    assert not nothing.ok
