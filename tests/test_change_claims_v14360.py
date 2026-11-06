"""v1.436.0 (chat-first plan Wave 5 group K) — the model never claims a
setting or a record it did not change.

A reply that says it changed a setting or one of the user's records ("Done —
read aloud is now on", "I paused the bookkeeper agent", "Undone.") is judged
against what THIS turn's successful calls and their cards really changed
(``daemon/change_claims.claimed_change_note``); a claim with no change gets
one plain note naming the setting by its label. Pinned here:

* the both-ways table: at least 40 replies that MUST get the note (with
  their turn's record) and 40 that must NOT (offers, questions, the user's
  own action, quoting, how a setting works, an earlier turn, the same claim
  WITH its change);
* the summary excuse (an earlier assistant message claimed it) and its
  limit (that message's own note said it was not made);
* "this reply was kept Local only" (``chat_local_only`` applies from the
  NEXT message);
* the REAL lanes — POST /chat and /chat/stream — with a scripted model and
  the real ``registry.invoke``: a claim with no tool, after a refused tool,
  after a declined card, after a tool that ran and failed, and a true claim
  after Allow once (no note);
* linear: pathological replies in a child process with a deadline.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon import change_claims as cc
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.change_claims import (
    claimed_change_note,
    claims_in,
    claims_this_reply_local,
)
from iron_jarvis.daemon.chat_turn import config_cards_from_result
from iron_jarvis.providers import egress
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMResponse, ToolCall
from iron_jarvis.tools.base import ToolResult
from tests.test_chat_approvals_v1187 import _asgi_post, _drive_stream

ROOT = Path(__file__).resolve().parents[1]


def S(key: str, old=False, new=True) -> dict:
    """The card a successful settings write produces — made by the REAL
    helper both lanes call."""
    (card,) = config_cards_from_result(
        "config_set",
        ToolResult(ok=True, output="", data={"config_change": {"key": key, "label": key, "old": old, "new": new,
                                                                "restart": False, "change_id": "chg_x"}}),
    )
    return card


def R(tool: str, key: str, new="changed") -> dict:
    """The card a successful record tool produces (``records._card``'s shape),
    through the REAL helper."""
    (card,) = config_cards_from_result(
        tool,
        ToolResult(ok=True, output="", data={"record_change": {
            "title": "Changed", "key": key, "label": key, "old": None, "new": new, "change_id": "rec_x"}}),
    )
    return card


def note(reply: str, used=(), cards=(), *, history=(), local=True, custom=()) -> str:
    return claimed_change_note(
        reply, list(used), list(cards), history=history, reply_local_only=local, custom_tools=custom,
    )


# --------------------------------------------------------------------------- #
# The both-ways table.
# --------------------------------------------------------------------------- #

#: (reply, the turn's successful tools, their cards, words the note must hold)
MUST_NOTE: list[tuple[str, list[str], list[dict], str]] = [
    # A claim with no tool at all.
    ("I've turned on read aloud.", [], [], "“Read replies aloud” was not changed"),
    ("Done — read aloud is now on.", [], [], "Read replies aloud"),
    ("Done — your default model is now Sonnet.", [], [], "“Default model”"),
    ("I switched you to the Daylight theme.", [], [], "“Theme”"),
    ("I've switched you to dark mode.", ["config_list"], [], "“Theme”"),
    ("Turned on Auto tools for this chat.", [], [], "Auto tools in chat"),
    ("I set your approvals to Don't ask.", [], [], "Approvals in chat"),
    ("I've turned on the emergency stop.", [], [], "Emergency stop"),
    ("I changed the max steps setting to 33.", [], [], "Max steps per run"),
    ("I've turned off follow-up questions.", [], [], "Suggest follow-up questions"),
    ("Keep this PC awake is now on.", [], [], "Keep this PC awake while work runs"),
    ("Your default model has been changed to Opus.", [], [], "Default model"),
    ("Sure — I've made Sonnet your default model.", [], [], "Default model"),
    ("I've switched your update channel to Stable.", [], [], "Update channel"),
    ("I've created a schedule that runs every weekday at 8am.", [], [], "No schedule was created"),
    ("I've deleted the nightly schedule.", [], [], "No schedule was deleted"),
    ("The schedule has been created.", [], [], "No schedule was created"),
    ("I scheduled the triage workflow to run every morning.", [], [], "No schedule was created"),
    ("I created a new workflow called Inbox triage.", [], [], "No saved workflow was created"),
    ("I've updated the triage workflow.", [], [], "No saved workflow was updated"),
    ("I've disconnected Notion.", [], [], "Notion was not disconnected"),
    ("Notion is now connected.", [], [], "Notion was not connected"),
    ("I've connected GitHub.", [], [], "GitHub was not connected"),
    ("I paused the bookkeeper agent.", [], [], "No agent was paused"),
    ("I resumed the bookkeeper agent.", [], [], "No agent was resumed"),
    ("I've set up a webhook for new invoices.", [], [], "No webhook was set up"),
    ("I removed the watcher on the Downloads folder.", [], [], "No watcher was removed"),
    ("I've added a goal to finish the Smith return.", [], [], "No goal was created"),
    ("I archived the old Smith project.", [], [], "No project was archived"),
    ("I created a project called Smith 2026.", [], [], "No project was created"),
    ("I've turned off notifications for failed runs.", [], [], "No notification destination"),
    ("I've revoked the always-allow for web search.", [], [], "No always-allow was revoked"),
    ("Local only is now on.", [], [], "Local only was not changed"),
    ("This chat is now Local only.", [], [], "Local only was not changed"),
    ("I turned off Local only for this chat.", [], [], "Local only was not changed"),
    ("Undone.", [], [], "Nothing was undone"),
    ("I've undone that change.", [], [], "Nothing was undone"),
    ("That change has been undone.", [], [], "Nothing was undone"),
    # Two claims in one sentence: each is judged.
    ("I've turned on read aloud and switched the theme to Daylight.", [], [], "“Theme”"),
    # A DIFFERENT setting changed this turn does not answer the claim.
    ("I've turned on read aloud.", ["config_set"], [S("device.theme")], "Read replies aloud"),
    # The tool ran but was refused / failed / declined: it is not in tools_used.
    ("I've turned off the emergency stop.", ["config_list"], [], "Emergency stop"),
    # A record tool of ANOTHER kind does not answer it.
    ("I paused the bookkeeper agent.", ["schedule_update"], [R("schedule_update", "schedule.nightly")],
     "No agent was paused"),
    # Waiting on a card: the app's key was asked for, nothing connected yet.
    ("I've connected Notion.", ["app_connect"],
     [{"kind": "secret", "name": "app.notion", "label": "Notion token", "request_id": "seq_1"}],
     "Notion is not connected yet: it waits for its key on the card"),
    ("I've connected Notion.", ["app_connect"],
     [R("app_connect", "app.notion", new="waiting for its token")],
     "Notion is not connected yet"),
]

#: (reply, the turn's successful tools, their cards)
MUST_NOT_NOTE: list[tuple[str, list[str], list[dict]]] = [
    # Offers, questions, futures.
    ("I can turn on read aloud for you.", [], []),
    ("Shall I turn on read aloud?", [], []),
    ("Do you want me to switch the theme to Daylight?", [], []),
    ("Want me to pause the bookkeeper agent?", [], []),
    ("Have I changed the theme?", [], []),
    ("So the schedule has been created?", [], []),
    ("Read aloud will be on once you approve the card.", [], []),
    ("I'll turn on read aloud.", [], []),
    ("I'd turn on read aloud if I were you.", [], []),
    ("If you want, I can switch the theme to Daylight.", [], []),
    ("I've asked for approval to turn on read aloud — approve the card.", [], []),
    ("I'm turning on read aloud now.", [], []),
    # The user's own action.
    ("You turned read aloud on yesterday.", [], []),
    ("You turned it on in Settings.", [], []),
    ("Your accountant updated the workflow last week.", [], []),
    ("The agent paused the schedule.", [], []),
    # How a setting works; instructions.
    ("When read aloud is on, each reply is spoken.", [], []),
    ("Turning on read aloud makes Jarvis speak each reply.", [], []),
    ("To turn on read aloud, open Settings and press the switch.", [], []),
    ("Set the theme to Daylight in Settings › Appearance.", [], []),
    ("The read aloud setting is now available on the Appearance page.", [], []),
    # An earlier turn.
    ("Earlier I turned on read aloud for you.", [], []),
    ("Read aloud is already on.", [], []),
    ("I turned on read aloud in my last reply.", [], []),
    ("Your schedule has been paused since Monday.", [], []),
    # Denials and attempts.
    ("I haven't changed the theme.", [], []),
    ("I didn't turn on read aloud because the card was declined.", [], []),
    ("I would have turned it on, but the setting asks first.", [], []),
    ("I tried to turn it on but the change was refused.", [], []),
    ("I wasn't able to change the theme.", [], []),
    # Quoting, code, a block quote.
    ("\"I've turned on read aloud\" is what I would say after the change.", [], []),
    ("Here is how: `I've turned on read aloud` appears after the change.", [], []),
    ("> I've turned on read aloud.", [], []),
    ("```\nI've turned on read aloud.\n```", [], []),
    # The user's own content, never a setting or a record.
    ("I've changed the theme of your essay to something more formal.", [], []),
    ("I created a schedule in your spreadsheet for the project tasks.", [], []),
    ("I've set up the project folder structure.", [], []),
    ("I scheduled a meeting with your accountant for Friday.", [], []),
    ("I've added a goal of 10 steps to the plan.", [], []),
    ("I updated the model to include depreciation.", [], []),
    ("I've updated your model with the new depreciation numbers.", [], []),
    # Each guard on its own (the trigger alone reads these as settings):
    ("I updated the calendar with your client meetings.", [], []),  # the user's content leads
    ("I updated the editor with your new section headings.", [], []),  # "the <everyday word>"
    ("I've updated the browser with your saved logins.", [], []),  # "the <everyday word>"
    ("I've switched your model to the newer version.", [], []),  # a model needs "default" or a name
    ("I changed the document to dark mode.", [], []),  # the user's content leads
    ("I changed the email to ask first before sending.", [], []),
    ("I set the browser to open links in a new tab.", [], []),
    ("I created an agent class in agents.py.", [], []),
    ("I added a Notion page for the meeting notes.", [], []),
    ("I've connected to your Notion workspace and found three pages.", [], []),
    ("I've connected the memory of the two sessions.", [], []),
    # v1.438.0 (Wave 4 group C): a preference is a record now, so the claim
    # is judged — moved on purpose: no note when the preference WAS saved
    # (tests/test_change_claims_self_v14380.py holds the false one).
    ("I saved your preference for shorter answers.", ["remember_preference"], []),
    ("I removed the duplicate rows from the table.", [], []),
    # The same claims WITH their change this turn.
    ("I've turned on read aloud.", ["config_set"], [S("device.read_aloud")]),
    ("Done — your default model is now Sonnet.", ["config_set"], [S("default_model", "opus", "sonnet")]),
    # A family: the provider and the model are one thing to a reader.
    ("Done — your default model is now Sonnet.", ["config_set"], [S("default_provider", "openai", "anthropic")]),
    ("I've created a schedule that runs every weekday at 8am.", ["schedule_create"], []),
    ("I paused the bookkeeper agent.", ["agent_pause"], [R("agent_pause", "agent.bookkeeper")]),
    ("I've disconnected Notion.", ["app_disconnect"], [R("app_disconnect", "app.notion", None)]),
    ("I've connected Notion.", ["app_connect"], [R("app_connect", "app.notion", new="12 tools")]),
    ("Undone.", ["config_undo"], []),
    ("I've undone that change.", ["change_undo"], []),
    ("Local only is now on for this chat. It applies from your next message.", ["chat_local_only"], []),
    ("I set your focused project to Smith 2026.", ["project_focus"], [R("project_focus", "project.p1")]),
    # A tool that may change anything without naming it: nothing is judged.
    ("I've turned on read aloud.", ["shell"], []),
    ("I paused the bookkeeper agent.", ["delegate"], []),
    # A coding turn's record words are about code.
    ("I've updated the scheduler in scheduler.py.", ["edit_file"], []),
    ("I've updated the triage workflow.", ["edit_file"], []),
]


def test_the_table_is_big_enough_both_ways():
    assert len(MUST_NOTE) >= 40, len(MUST_NOTE)
    assert len(MUST_NOT_NOTE) >= 40, len(MUST_NOT_NOTE)


@pytest.mark.parametrize("reply,used,cards,words", MUST_NOTE)
def test_a_claimed_change_with_no_change_gets_the_note(reply, used, cards, words):
    got = note(reply, used, cards)
    assert got.startswith("\n\n_Note: ") and got.endswith("_"), (reply, got)
    assert words in got, (reply, got)
    assert "in this reply" in got
    # A setting is named by its label, never its key.
    for key in ("device.", "default_model", "autonomy_kill_switch", "max_agent_steps", "chat_followups"):
        assert key not in got, (key, got)


@pytest.mark.parametrize("reply,used,cards", MUST_NOT_NOTE)
def test_no_note_without_a_claim_or_with_its_change(reply, used, cards):
    assert note(reply, used, cards) == "", reply


def test_the_note_is_one_line_and_at_most_four_sentences():
    reply = (
        "I've turned on read aloud. I switched you to the Daylight theme. I paused the bookkeeper "
        "agent. I've deleted the nightly schedule. I've disconnected Notion. Undone."
    )
    got = note(reply)
    assert got.count("_Note:") == 1 and "\n" not in got.strip(), got
    assert got.count("in this reply") == 4, got


# --------------------------------------------------------------------------- #
# The summary excuse (how far back a claim may refer).
# --------------------------------------------------------------------------- #


def _m(role: str, content: str) -> SimpleNamespace:
    return SimpleNamespace(role=role, content=content)


def test_an_earlier_assistant_claim_of_the_same_kind_excuses_a_summary():
    history = [
        _m("user", "turn on read aloud"),
        _m("assistant", "I've turned on read aloud."),
        _m("user", "is it on?"),
    ]
    assert note("Yes — I turned on read aloud for you.", history=history) == ""
    # Anti-vacuity: the same reply with no earlier claim gets the note.
    assert note("Yes — I turned on read aloud for you.", history=history[2:]) != ""
    # ANOTHER kind claimed earlier excuses nothing.
    other = [_m("user", "x"), _m("assistant", "I switched you to the Daylight theme."), _m("user", "y")]
    assert "Read replies aloud" in note("I've turned on read aloud.", history=other)


def test_an_earlier_claim_its_own_note_said_was_not_made_is_no_excuse():
    first = "I've turned on read aloud."
    first += note(first)
    assert "Read replies aloud" in first
    history = [_m("user", "turn on read aloud"), _m("assistant", first), _m("user", "please actually do it")]
    assert "Read replies aloud" in note("Done — read aloud is now on.", history=history)


def test_the_users_own_words_and_this_turns_question_excuse_nothing():
    history = [_m("user", "I've turned on read aloud myself"), _m("user", "and now?")]
    assert note("I've turned on read aloud.", history=history) != ""


# --------------------------------------------------------------------------- #
# "This reply was kept Local only".
# --------------------------------------------------------------------------- #

THIS_REPLY_LOCAL = [
    "Local only is now on, so this reply stays on your PC.",
    "This chat is now Local only, including this reply.",
    "Local only applies to this reply too.",
    "Local only is on immediately.",
    "This reply is Local only.",
]
NOT_THIS_REPLY_LOCAL = [
    "Local only is now on. It applies from your next message.",
    "This reply was already being written, so it is not Local only.",
    "Local only will apply from your next message, not this reply.",
    "Is this reply Local only?",
    "This reply stays on your PC.",  # no Local only claim: a local model may answer
]


@pytest.mark.parametrize("reply", THIS_REPLY_LOCAL)
def test_a_reply_claiming_it_ran_local_only_gets_the_note_when_it_did_not(reply):
    assert claims_this_reply_local(reply), reply
    got = note(reply, ["chat_local_only"], local=False)
    assert "This reply was not kept Local only; Local only applies from your next message." in got, got
    # The same words in a reply that WAS Local only are true.
    assert note(reply, ["chat_local_only"], local=True) == ""


@pytest.mark.parametrize("reply", NOT_THIS_REPLY_LOCAL)
def test_no_this_reply_note_for_the_true_words(reply):
    assert not claims_this_reply_local(reply), reply
    assert note(reply, ["chat_local_only"], local=False) == ""


def test_the_reply_scope_is_read_from_the_turn_itself():
    reply = "Local only is on immediately."
    with egress.local_only_scope(True):
        assert claimed_change_note(reply, ["chat_local_only"], []) == ""
    assert "not kept Local only" in claimed_change_note(reply, ["chat_local_only"], [])
    # A cloud send the user allowed once is no Local only reply.
    with egress.local_only_scope(True, "anthropic"):
        assert "not kept Local only" in claimed_change_note(reply, ["chat_local_only"], [])


def test_without_the_tool_this_turn_the_note_does_not_promise_the_next_message():
    got = note("Local only is now on, so this reply stays on your PC.", [], local=False)
    assert "This reply was not kept Local only." in got and "next message" not in got, got
    assert "Local only was not changed in this reply." in got, got


# --------------------------------------------------------------------------- #
# The record's limits.
# --------------------------------------------------------------------------- #


def test_a_custom_tool_or_an_undo_answers_every_claim():
    assert note("I've turned on read aloud.", ["bump"], custom=["bump"]) == ""
    assert note("I've turned on read aloud.", ["custom:bump"]) == ""
    assert note("I paused the bookkeeper agent.", ["config_undo"]) == ""


def test_a_settings_write_without_a_card_is_not_known_so_not_judged():
    assert note("I've turned on read aloud.", ["config_set"], []) == ""


def test_a_coding_turn_still_judges_a_settings_claim():
    assert "Read replies aloud" in note("I've turned on read aloud.", ["edit_file"])


def test_an_app_whose_name_is_an_everyday_word_is_never_read_from_a_claim():
    """``connectors.app_names`` reads "connect … box" as the Box app; a claim
    never does ("memory", "fetch", "box" are words first)."""
    from iron_jarvis.connectors.app_names import apps_named

    assert apps_named("set connect box today") == ["box"]  # the matcher alone
    assert cc._app_ids("connect box today", "set", "connect box today".split()) == []
    assert cc._app_ids("notion", "connected", ["notion"]) == ["notion"]  # a brand name is read


def test_never_raises_and_says_nothing_on_nonsense():
    assert claimed_change_note(None, None, None) == ""  # type: ignore[arg-type]
    assert claimed_change_note("", [], []) == ""
    assert claims_in("\x00" * 50) == []


# --------------------------------------------------------------------------- #
# Linear: pathological replies, in a child process with a deadline.
# --------------------------------------------------------------------------- #

_PATHOLOGICAL = {
    "verbs": "I've turned on " * 9000,
    "copulas": "x is now on, " * 9000,
    "is_is": "is " * 30000,
    "local": "local only " * 12000 + "this reply",
    "backticks": "`" * 90000,
    "quotes": '"' * 90000,
    "curly": "“" * 90000,
    "dots": "a.b" * 40000,
    "dashes": "- " * 50000,
    "subjects": "I " * 60000,
    "done": "Done — " * 20000,
    "words": "schedule workflow agent project " * 6000,
    "hyphens": "a-" * 60000,
    "fences": "```\n" * 30000,
    "notes": "_Note: x in this reply_\n" * 5000,
}


def test_pathological_replies_finish_in_time_in_a_child_process():
    code = textwrap.dedent(
        """
        import json, sys, time
        sys.path.insert(0, %r)
        from iron_jarvis.daemon.change_claims import claimed_change_note
        from types import SimpleNamespace
        cases = json.loads(sys.stdin.read())
        out = {}
        for name, text in cases.items():
            hist = [SimpleNamespace(role="assistant", content=text), SimpleNamespace(role="user", content="q")]
            t = time.perf_counter()
            claimed_change_note(text, [], [], history=hist, reply_local_only=False)
            out[name] = time.perf_counter() - t
        print(json.dumps(out))
        """
    ) % str(ROOT / "src")
    r = subprocess.run(
        [sys.executable, "-c", code], input=json.dumps(_PATHOLOGICAL), capture_output=True,
        text=True, timeout=120, encoding="utf-8",
    )
    assert r.returncode == 0, r.stderr[-2000:]
    times = json.loads(r.stdout.strip().splitlines()[-1])
    slow = {k: v for k, v in times.items() if v > 8.0}
    assert not slow, slow


def test_four_times_the_text_costs_about_four_times_never_sixteen():
    """RATIOS, never a wall-clock bar (the v1.290.0 detections pattern)."""
    import time

    def cost(n: int) -> float:
        text = "I've turned on read aloud and the agent is now paused, " * n
        best = float("inf")
        for _ in range(3):
            t = time.perf_counter()
            claimed_change_note(text, [], [], reply_local_only=False)
            best = min(best, time.perf_counter() - t)
        return best

    small, big = cost(100), cost(400)  # both below the scan cap
    assert big < max(small, 0.002) * 9, (small, big)


def test_every_pattern_here_is_a_bounded_one():
    """No unbounded repeat beside another (the shape that went quadratic in
    v1.434.0's schedule words): every ``*``/``+`` in this module's patterns
    is on a single class with nothing unbounded next to it."""
    import re

    pats = [v.pattern for v in vars(cc).values() if isinstance(v, re.Pattern)]
    pats += [p.pattern for p in cc._THIS_REPLY_LOCAL]
    assert pats
    for p in pats:
        assert ".*" not in p and ".+" not in p, p


# --------------------------------------------------------------------------- #
# The REAL lanes: a scripted model, the real registry.invoke.
# --------------------------------------------------------------------------- #


class _Scripted(LLMAdapter):
    """A real-model stand-in (never the demo): each round calls the next
    scripted tool, then answers *final*."""

    provider = "anthropic"
    model = "claude-opus-4-8"

    def __init__(self, calls: list[tuple[str, dict]], final: str) -> None:
        self.calls, self.final, self.n = list(calls), final, 0

    async def complete(self, *, system, messages, tools, **_kw):
        self.n += 1
        if self.n <= len(self.calls):
            name, args = self.calls[self.n - 1]
            return LLMResponse(text="", tool_calls=[ToolCall(id=f"c{self.n}", name=name, arguments=args)])
        return LLMResponse(text=self.final)


def _app(tmp_path, calls, final):
    app = create_app(str(tmp_path))
    model = _Scripted(calls, final)
    app.state.platform.providers.get = lambda prov, m=None, _m=model: _m
    return app


def _body(text: str, mode: str = "approve_for_me") -> dict:
    return {"messages": [{"role": "user", "content": text}], "auto_tools": True, "approval_mode": mode}


def _stream_done(client: TestClient, body: dict) -> dict:
    r = client.post("/chat/stream", json=body)
    assert r.status_code == 200, r.text
    done = None
    for block in r.text.split("\n\n"):
        lines = block.strip().splitlines()
        if lines and lines[0] == "event: done":
            done = json.loads("".join(ln[6:] for ln in lines[1:] if ln.startswith("data: ")))
    assert done is not None, r.text[-1500:]
    return done


def _lane(app, lane: str, body: dict) -> dict:
    client = TestClient(app)
    if lane == "post":
        r = client.post("/chat", json=body)
        assert r.status_code == 200, r.text
        return r.json()
    return _stream_done(client, body)


@pytest.mark.parametrize("lane", ["post", "stream"])
def test_lane_a_claim_with_no_tool_gets_the_note(tmp_path, lane):
    app = _app(tmp_path, [], "Done — read aloud is now on.")
    out = _lane(app, lane, _body("turn on read aloud"))
    assert out["route"]["reason"] != "mock" and out["tools_used"] == [], out["route"]
    assert out["reply"].startswith("Done — read aloud is now on.")
    assert "“Read replies aloud” was not changed in this reply." in out["reply"], out["reply"]


@pytest.mark.parametrize("lane", ["post", "stream"])
def test_lane_a_claim_after_a_refused_tool_gets_the_note(tmp_path, lane):
    """The emergency stop cannot be turned OFF through the allow tool: the
    registry refuses the call, the model says it did anyway."""
    app = _app(tmp_path, [("config_set", {"key": "autonomy_kill_switch", "value": False})],
               "I've turned off the emergency stop.")
    app.state.platform.config.autonomy_kill_switch = True
    out = _lane(app, lane, _body("turn off the emergency stop"))
    assert "config_set" not in out["tools_used"], out["tools_used"]
    assert app.state.platform.config.autonomy_kill_switch is True
    assert "“Emergency stop” was not changed in this reply." in out["reply"], out["reply"]


@pytest.mark.parametrize("lane", ["post", "stream"])
def test_lane_a_true_claim_after_an_allow_tier_change_gets_no_note(tmp_path, lane):
    app = _app(tmp_path, [("config_set", {"key": "autonomy_dry_run", "value": "on"})],
               "Done — dry-run mode is now on.")
    cfg = app.state.platform.config
    cfg.autonomy_dry_run = False
    out = _lane(app, lane, _body("turn on dry run mode for autonomy"))
    assert "config_set" in out["tools_used"] and cfg.autonomy_dry_run is True, out["tools_used"]
    assert out["config_cards"] and out["config_cards"][0]["key"] == "autonomy_dry_run"
    assert "_Note:" not in out["reply"], out["reply"]


def _done(frames):
    return next(d for ev, d in frames if ev == "done")


@pytest.mark.asyncio
async def test_stream_a_declined_card_then_a_claim_gets_the_note(tmp_path):
    app = _app(tmp_path, [("config_change", {"key": "max_agent_steps", "value": "33"})],
               "I've changed the max steps setting to 33.")
    before = app.state.platform.config.max_agent_steps
    seen = []

    async def deny(data):
        seen.append(data["tool"])
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "deny"})
        assert status == 200

    done = _done(await _drive_stream(app, _body("change the max steps setting to 33"), deny))
    assert seen == ["config_change"], seen
    assert app.state.platform.config.max_agent_steps == before
    assert "config_change" not in done["tools_used"]
    assert "“Max steps per run” was not changed in this reply." in done["reply"], done["reply"]


@pytest.mark.asyncio
async def test_stream_a_true_claim_after_allow_once_gets_no_note(tmp_path):
    app = _app(tmp_path, [("config_change", {"key": "max_agent_steps", "value": "33"})],
               "I've changed the max steps setting to 33.")

    async def once(data):
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "once"})
        assert status == 200

    done = _done(await _drive_stream(app, _body("change the max steps setting to 33"), once))
    assert app.state.platform.config.max_agent_steps == 33
    assert "config_change" in done["tools_used"], done["tools_used"]
    assert "_Note:" not in done["reply"], done["reply"]


@pytest.mark.asyncio
async def test_stream_a_record_tool_that_ran_and_failed_then_a_claim_gets_the_note(tmp_path):
    """Allowed on its card, the tool RAN and failed (there is no such
    schedule): nothing was deleted, the reply says it was."""
    app = _app(tmp_path, [("schedule_delete", {"name": "nightly"})], "I've deleted the nightly schedule.")
    seen = []

    async def once(data):
        seen.append(data["tool"])
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "once"})
        assert status == 200

    done = _done(await _drive_stream(app, _body("delete my nightly schedule"), once))
    assert seen == ["schedule_delete"], "the call must reach its card and run"
    assert "schedule_delete" not in done["tools_used"], done["tools_used"]
    from sqlmodel import select

    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import ToolInvocation

    with session_scope(app.state.platform.engine) as db:
        rows = db.exec(select(ToolInvocation).where(ToolInvocation.tool == "schedule_delete")).all()
        assert [r.ok for r in rows] == [False], "the call ran through registry.invoke and failed"
    assert "No schedule was deleted in this reply." in done["reply"], done["reply"]


@pytest.mark.parametrize("lane", ["post", "stream"])
def test_lane_a_reply_claiming_it_ran_local_only_gets_the_note(tmp_path, lane):
    app = _app(tmp_path, [], "Local only is now on, so this reply stays on your PC.")
    out = _lane(app, lane, _body("keep this chat on my computer"))
    assert "This reply was not kept Local only." in out["reply"], out["reply"]
    assert "Local only was not changed in this reply." in out["reply"], out["reply"]


@pytest.mark.parametrize("lane", ["post", "stream"])
def test_lane_a_summary_of_an_earlier_turns_change_gets_no_note(tmp_path, lane):
    app = _app(tmp_path, [], "Yes — I turned on read aloud for you.")
    body = {
        "messages": [
            {"role": "user", "content": "turn on read aloud"},
            {"role": "assistant", "content": "I've turned on read aloud."},
            {"role": "user", "content": "is read aloud on?"},
        ],
        "auto_tools": True,
    }
    out = _lane(app, lane, body)
    assert "_Note:" not in out["reply"], out["reply"]


def test_both_lanes_append_the_same_call_right_after_the_file_note():
    """MIRROR NOTE (lock-step): the two lanes, read as source."""
    post = (ROOT / "src" / "iron_jarvis" / "daemon" / "chat_turn.py").read_text(encoding="utf-8").replace("\r\n", "\n")
    stream = (ROOT / "src" / "iron_jarvis" / "daemon" / "routes" / "chat.py").read_text(encoding="utf-8").replace("\r\n", "\n")
    call = "claimed_change_note(\n{ind}    reply, tools_used, config_cards, history=body.messages, custom_tools=_custom_ran,\n{ind})"
    assert call.format(ind="            ") in post.replace("reply += claimed_change_note(", "claimed_change_note(")
    assert call.format(ind="                ") in stream.replace("reply += _chat_turn.claimed_change_note(", "claimed_change_note(")
    for src in (post, stream):
        at = src.index("claimed_change_note(\n")
        before = src[:at]
        assert before.rindex("_claimed_write_note(reply, tools_used") > before.rindex("_creation_honesty_note(body")
