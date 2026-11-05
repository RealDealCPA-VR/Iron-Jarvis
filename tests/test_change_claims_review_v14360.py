"""v1.436.0 (chat-first plan Wave 5 group K) — the INDEPENDENT REVIEW's pins.

Measured on replies the builder's table did not hold: 22 of 81 true replies
or non-claims got a FALSE note (a CPA's "depreciation schedule", "Schedule E",
"three goals to your plan", "a project in Asana", "the default model in
config.py", "a rogue agent" in a story, "…because you changed it in
Settings", "a Google Calendar reminder"…), and 11 of 58 false claims got NO
note — among them the "summary excuse" letting one true earlier change excuse
every later false one of the same kind ("I set your theme to Daylight" then a
false "…to Midnight"; "turned on" then a false "turned off"; "paused" then a
false "deleted"), and another agent's line in the chat excusing Jarvis.

Each guard here was red before its fix (mutation-checked: each one put back
turns its row red).
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest

from iron_jarvis.comm.threads import agent_line_label
from iron_jarvis.daemon import change_claims as cc
from iron_jarvis.daemon.change_claims import claimed_change_note, claims_this_reply_local
from tests.test_change_claims_v14360 import ROOT, S, _app, _body, _lane, _Scripted


def note(reply: str, used=(), cards=(), *, history=(), local=True) -> str:
    return claimed_change_note(reply, list(used), list(cards), history=history, reply_local_only=local)


def _m(role: str, content: str) -> SimpleNamespace:
    return SimpleNamespace(role=role, content=content)


# --------------------------------------------------------------------------- #
# FALSE notes the first cut put under true replies and non-claims.
# --------------------------------------------------------------------------- #

#: (reply, the turn's successful tools)
NO_NOTE: list[tuple[str, list[str]]] = [
    # A CPA's schedules are documents, never one of Iron Jarvis's schedules.
    ("I updated the depreciation schedule below.", []),
    ("I added the rental income to Schedule E.", []),
    ("I've filled in Schedule C with the numbers from your 1099s.", []),
    ("I've updated the amortization schedule for the new loan rate.", []),
    ("I changed the payment schedule to monthly.", []),
    ("I created a schedule of payments for the loan.", []),
    ("I've added depreciation to the fixed asset schedule.", []),
    ("I removed the duplicate schedule entries from the CSV.", []),
    ("I paused the workout schedule in your fitness plan.", []),
    ("I added the meeting to your work schedule.", []),
    ("I updated your schedule for next week.", []),
    # A record noun that is not the thing the clause is about.
    ("I've added three goals to your plan.", []),
    ("I added a Goals section with three goals for 2027.", []),
    ("I added the new goal to the OKR doc.", []),
    ("I created a goal-tracking spreadsheet for you.", []),
    ("I updated the notification email template.", []),
    ("I changed the approval workflow in the policy document.", []),
    ("I've added a cron schedule to the workflow file.", []),
    # Another product's records, with or without its own tool this turn.
    ("I created a project in Asana for the Smith engagement.", ["mcp__asana__create_project"]),
    ("I created an automation rule in Zapier.", []),
    ("I've muted the notifications in Teams.", ["mcp__teams__mute"]),
    ("I turned off the alerts in Datadog.", []),
    ("I created the project board in Trello.", []),
    ("I set up a GitHub Actions workflow that runs the tests.", []),
    ("I've deleted the old webhook from your Stripe dashboard.", []),
    ("I added a new agent to the CrewAI config.", []),
    ("I've removed the agent from the support queue.", []),
    ("I enabled two-factor authentication on your GitHub account.", []),
    ("I enabled notifications for the new channel in Slack.", []),
    ("I've turned on notifications in the Slack app for you.", ["mcp__slack__set_prefs"]),
    ("I turned on dark mode for you.", ["browser_click"]),
    # Stories.
    ("I created a new agent persona for the story.", []),
    ("In the story, I created a rogue agent named Vex.", []),
    # Code: a coding turn's settings words are about the code.
    ("I've changed the default model to Sonnet in config.py.", ["edit_file"]),
    ("I've added dark mode support to the settings page.", ["edit_file"]),
    # A thing made is never a setting changed.
    ("I've set up a Google Calendar reminder for Friday.", []),
    # The user's own change.
    ("Your default model is now Sonnet because you changed it in Settings.", []),
    # An undo of something that is not one of Iron Jarvis's changes.
    ("I rolled back the release.", []),
    ("I've undone the formatting changes in your doc.", []),
    # Iron Jarvis's own words quoting a tool, never the model's claim.
    ("The model finished without writing an answer. Here is what project_create returned:\n"
     "The project has been created.", []),
    # A completion followed by a state that is no setting or record.
    ("Done! The meeting is cancelled.", []),
    ("Done! Your Notion page is deleted.", []),
    ("All done — the old files are gone.", []),
    # A dated report of an EARLIER change.
    ("The nightly schedule was created on October 3 and runs at 2 AM.", []),
    ("The bookkeeper agent was paused on Monday.", []),
    ("I archived the Smith project in September.", []),
    ("I turned on read aloud yesterday.", []),
    ("I paused the bookkeeper agent last week.", []),
    # "What did you change?": the change history was read this turn.
    ("Here's what I changed today: I turned on read aloud and switched your theme to Daylight.",
     ["changes_list"]),
    # Code a coding chat writes with no tool.
    ("I built a new agent using LangChain for you.", []),
    ("I created a simple agent in Python that calls the API.", []),
]


def test_a_settings_own_words_never_veto_it():
    """The code and content vetoes are judged per setting: "endpoint",
    "code", "file", "repo" are some settings' OWN words."""
    for key, reply in (
        ("custom_base_url", "I've changed the Custom endpoint URL setting."),
        ("profile_share_claude_code", "I've changed the Share my profile with Claude Code setting."),
        ("search_roots", "I've changed the Extra folders for file search setting."),
        ("self_dev_root", "I've changed the Self-development repo folder setting."),
        ("account_groups", "I've changed the Account groups setting."),
    ):
        assert note(reply), (key, reply)  # a claim, with no change: the note
        assert note(reply, ["config_set"], [S(key, 1, 2)]) == "", (key, reply)  # with it: none


@pytest.mark.parametrize("reply,used", NO_NOTE)
def test_no_false_note_under_a_true_reply_or_a_non_claim(reply, used):
    assert note(reply, used) == "", reply


# --------------------------------------------------------------------------- #
# Notes the first cut MISSED.
# --------------------------------------------------------------------------- #

MUST_NOTE: list[tuple[str, str]] = [
    ("You're all set — Sonnet is your default.", "“Default model” was not changed"),
    ("Done! The nightly backup schedule is gone.", "No schedule was removed in this reply."),
    ("Done! Read aloud is on.", "Read replies aloud"),
    ("All set! Your Slack destination is connected.", "Slack was not connected"),
    ("I've revoked that always-allow.", "No always-allow was revoked"),
    # "for" never makes it someone else's: a schedule FOR a report is ours.
    ("I've created a schedule for your weekly report.", "No schedule was created"),
    ("I created a new workflow for invoice reminders.", "No saved workflow was created"),
    ("I paused every schedule.", "No schedule was paused"),
    # A destination reached with "to" is Iron Jarvis's own, never "elsewhere".
    ("I created a schedule that posts to Slack every morning.", "No schedule was created"),
]


@pytest.mark.parametrize("reply,words", MUST_NOTE)
def test_a_false_claim_the_first_cut_missed_gets_its_note(reply, words):
    got = note(reply)
    assert words in got, (reply, got)


def test_a_state_after_done_is_never_a_was_gone_sentence():
    got = note("The schedule is now gone.")
    assert "No schedule was removed in this reply." in got and "was gone" not in got, got


# --------------------------------------------------------------------------- #
# The summary excuse: same kind, same direction, no other value.
# --------------------------------------------------------------------------- #


def _hist(said: str, asked: str = "and now?") -> list[SimpleNamespace]:
    return [_m("user", "please"), _m("assistant", said), _m("user", asked)]


def test_a_true_earlier_change_never_excuses_a_new_value():
    hist = _hist("I've set your theme to Daylight.", "now make it midnight")
    assert "“Theme”" in note("I've switched your theme to Midnight.", history=hist)
    # The same value is a summary.
    assert note("Your theme is now Daylight.", history=hist) == ""


def test_a_true_earlier_change_never_excuses_the_opposite_one():
    assert "Read replies aloud" in note("I've turned off read aloud.", history=_hist("I've turned on read aloud."))
    assert "No agent was deleted" in note(
        "I've deleted the bookkeeper agent.", history=_hist("I paused the bookkeeper agent.")
    )
    assert "No agent was resumed" in note(
        "I resumed the bookkeeper agent.", history=_hist("I paused the bookkeeper agent.")
    )
    # A summary in the same direction is still excused.
    assert note("Read aloud is now on, so replies will be spoken.", history=_hist("I've turned on read aloud.")) == ""
    assert note("Yes, Sonnet is now your default model.", history=_hist("I've set your default model to Sonnet.")) == ""


def test_a_recap_in_the_state_words_is_excused():
    """"connected", "turned" and the other verb words are never a VALUE."""
    hist = _hist("I've connected Notion — 12 tools are ready.")
    assert note("Notion is now connected.", history=hist) == ""
    assert note("I've switched your theme to Daylight.", history=_hist("Your theme is now Daylight.")) == ""


def test_only_messages_before_this_turns_question_excuse():
    hist = [_m("user", "turn on read aloud"), _m("assistant", "I've turned on read aloud.")]
    assert "Read replies aloud" in note("I've turned on read aloud.", history=hist)


def test_another_agents_line_in_the_chat_is_no_excuse():
    line = agent_line_label("hermes")
    assert line.startswith(cc._AGENT_LINE_PREFIX)  # the one sentence, kept in step
    hist = [_m("user", "@hermes turn on read aloud"), _m("assistant", f"{line}\nI've turned on read aloud."),
            _m("user", "did it work?")]
    assert "Read replies aloud" in note("Yes — I've turned on read aloud.", history=hist)


def test_an_earlier_waiting_app_note_is_no_excuse():
    first = "I've connected Notion."
    first += claimed_change_note(
        first, ["app_connect"], [{"kind": "secret", "name": "app.notion", "label": "Notion token",
                                  "request_id": "seq_1"}], reply_local_only=True,
    )
    assert "Notion is not connected yet" in first, first
    got = note("Notion is now connected.", history=_hist(first))
    assert "Notion was not connected in this reply." in got, got


# --------------------------------------------------------------------------- #
# "This reply was kept Local only": an offer is no claim.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("reply", [
    "I can turn Local only on right away.",
    "Local only takes effect right away for new chats.",
    "If you like, Local only applies to this reply too.",
])
def test_an_offer_or_the_new_chats_setting_is_no_this_reply_claim(reply):
    assert not claims_this_reply_local(reply), reply


# --------------------------------------------------------------------------- #
# Linear: the review's own shapes, in a child process with a deadline.
# --------------------------------------------------------------------------- #

_SHAPES = {
    "in_the": "I created a schedule in the " * 6000,
    "of": "a schedule of " * 10000,
    "you_changed": "you changed " * 12000,
    "done_bang": "Done! " * 20000,
    "done_state": "Done! The schedule is gone. " * 3000,
    "undo": "I've undone that change. Undone. " * 3000,
    "theme": "I've switched your theme to Midnight and turned off read aloud. " * 400,
}


def test_the_reviews_shapes_finish_in_time_in_a_child_process():
    code = textwrap.dedent(
        """
        import json, sys, time
        sys.path.insert(0, %r)
        from iron_jarvis.daemon.change_claims import claimed_change_note
        from types import SimpleNamespace
        cases = json.loads(sys.stdin.read())
        out = {}
        for name, text in cases.items():
            hist = [SimpleNamespace(role="assistant", content=text), SimpleNamespace(role="user", content="q")] * 6
            t = time.perf_counter()
            claimed_change_note(text, [], [], history=hist, reply_local_only=False)
            out[name] = time.perf_counter() - t
        print(json.dumps(out))
        """
    ) % str(ROOT / "src")
    r = subprocess.run(
        [sys.executable, "-c", code], input=json.dumps(_SHAPES), capture_output=True,
        text=True, timeout=120, encoding="utf-8",
    )
    assert r.returncode == 0, r.stderr[-2000:]
    times = json.loads(r.stdout.strip().splitlines()[-1])
    slow = {k: v for k, v in times.items() if v > 8.0}
    assert not slow, slow


# --------------------------------------------------------------------------- #
# The REAL lanes.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lane", ["post", "stream"])
def test_lane_a_cpa_schedule_reply_gets_no_note(tmp_path, lane):
    app = _app(tmp_path, [], "I updated the depreciation schedule below and added the rental income to Schedule E.")
    out = _lane(app, lane, _body("update my depreciation schedule"))
    assert out["route"]["reason"] != "mock"
    assert "_Note:" not in out["reply"], out["reply"]


@pytest.mark.parametrize("lane", ["post", "stream"])
def test_lane_a_new_value_after_a_true_earlier_change_gets_the_note(tmp_path, lane):
    app = _app(tmp_path, [], "I've switched your theme to Midnight.")
    body = {
        "messages": [
            {"role": "user", "content": "set my theme to daylight"},
            {"role": "assistant", "content": "I've set your theme to Daylight."},
            {"role": "user", "content": "now make it midnight"},
        ],
        "auto_tools": True,
    }
    out = _lane(app, lane, body)
    assert "“Theme” was not changed in this reply." in out["reply"], out["reply"]


@pytest.mark.parametrize("lane", ["post", "stream"])
def test_lane_a_claimed_file_gets_the_file_note_and_no_second_note(tmp_path, lane):
    """The file twin speaks for a claimed FILE; this check never doubles it."""
    app = _app(tmp_path, [], "I've saved the nightly schedule to schedule.xlsx.")
    out = _lane(app, lane, _body("save my schedule"))
    assert out["reply"].count("_Note:") == 1, out["reply"]
    assert "schedule.xlsx" in out["reply"].split("_Note:")[1], out["reply"]
    assert "No schedule was" not in out["reply"], out["reply"]


class _Demo(_Scripted):
    provider = "mock"
    model = "mock"


@pytest.mark.parametrize("lane", ["post", "stream"])
def test_lane_a_demo_reply_gets_the_demo_note_never_a_change_note(tmp_path, lane):
    """Placement: the check sits in the lanes' real-model branch only."""
    from iron_jarvis.daemon.app import create_app

    app = create_app(str(tmp_path))
    model = _Demo([], "Done — read aloud is now on.")
    app.state.platform.providers.get = lambda prov, m=None, _m=model: _m
    out = _lane(app, lane, _body("turn on read aloud"))
    assert out["route"]["reason"] == "mock", out["route"]
    assert "was not changed in this reply" not in out["reply"], out["reply"]


@pytest.mark.asyncio
async def test_stream_local_only_turned_on_mid_reply_says_this_reply_was_not(tmp_path):
    """Group I's rule through the real stream lane: ``chat_local_only`` turns
    it on from the NEXT message, so a reply saying it stays on the PC gets
    the one "this reply" note — and the switch itself, which really changed,
    gets none."""
    import json as _json

    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import ChatThreadRecord
    from tests.test_chat_approvals_v1187 import _drive_stream

    app = _app(tmp_path, [("chat_local_only", {"on": True})],
               "Local only is now on, so this reply stays on your PC.")
    p = app.state.platform
    with session_scope(p.engine) as db:
        db.add(ChatThreadRecord(id="chat_k", messages_json="[]", setup_json="{}"))
        db.commit()

    async def never(data):  # pragma: no cover - turning it ON never cards
        raise AssertionError(data)

    frames = await _drive_stream(app, {**_body("keep this chat on my PC"), "thread_id": "chat_k"}, never)
    done = next(d for ev, d in frames if ev == "done")
    assert "chat_local_only" in done["tools_used"], done["tools_used"]
    with session_scope(p.engine) as db:
        assert _json.loads(db.get(ChatThreadRecord, "chat_k").setup_json).get("local_only") is True
    assert "This reply was not kept Local only; Local only applies from your next message." in done["reply"]
    assert "Local only was not changed" not in done["reply"], done["reply"]
