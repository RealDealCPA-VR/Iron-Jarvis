"""v1.503.0 (north-star fix plan 3.10) — setting and record claims stay
judged after other tools, a claimed SEND is checked, and an agent run's
summary gets the same check.

1. ``change_claims.claimed_change_note`` stopped judging every setting or
   record claim once a command, code, a hand-off, a workflow run or a custom
   tool ran, and cleared every setting and record claim after any app
   (``mcp__``) tool. A claim that names one of Iron Jarvis's settings by its
   label ("Read replies aloud") or one of its record kinds (a schedule, an
   agent) is now judged after a command, code, a user-made tool or an app's
   tool too: only a config card or a record tool changes those, and both name
   what they changed. Kept as before, on purpose: after a hand-off or a
   workflow run (that run holds Iron Jarvis's own record tools and its calls
   are in ITS record, not this reply's), a record named after an app's tool
   whose own name says it made one ("mcp__asana__create_project" and "I
   created the project"), a record after an action on a web page, and a
   setting named only by an everyday word ("dark mode") after any of these.
2. A reply that claims an outward action ("I emailed it", "sent to Slack",
   "posted to #general", "messaged Priya on Telegram") is answered only by a
   send that SUCCEEDED this reply (``notify``, ``rest_call``, an agent
   message, an app's tool that is not a plain read, an action on a web
   page), or by a command / code / hand-off / user-made tool, which may have
   sent it. Otherwise the same end-of-reply note line the other kinds get,
   and the same check-again problem.
3. ``change_claims.judge_run_summary(summary, *, engine, session_id,
   custom_tools=())`` judges a finished agent run's summary against that
   run's own ledger.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session as DB
from sqlmodel import SQLModel, create_engine

from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.daemon import change_claims as cc
from iron_jarvis.daemon import until_done as U
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import config_cards_from_result
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMResponse, ToolCall
from iron_jarvis.tools.base import ToolResult

ALOUD = "“Read replies aloud” was not changed in this reply."
NOT_SENT = "No message was sent in this reply."


def note(reply: str, used=(), cards=(), *, history=(), custom=()) -> str:
    return cc.claimed_change_note(
        reply, list(used), list(cards), history=history, reply_local_only=True, custom_tools=custom,
    )


def S(key: str, new=True) -> dict:
    (card,) = config_cards_from_result(
        "config_set",
        ToolResult(ok=True, output="", data={"config_change": {"key": key, "label": key, "old": False, "new": new,
                                                                "restart": False, "change_id": "chg_x"}}),
    )
    return card


# --------------------------------------------------------------------------- #
# 1. Judged after other tools.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("used", [["shell"], ["run_code"], ["process_start"], ["pane_send"],
                                  ["mcp__github__create_issue"]])
def test_a_setting_named_by_its_label_is_judged_after_any_command_or_app_tool(used):
    assert ALOUD in note("I've turned on Read replies aloud.", used), used


def test_a_user_made_tool_does_not_hide_a_setting_named_by_its_label():
    assert ALOUD in note("I've turned on Read replies aloud.", ["bump"], custom=["bump"])
    assert ALOUD in note("I've turned on Read replies aloud.", ["custom:bump"])


@pytest.mark.parametrize("used", [["shell"], ["run_code"], ["mcp__github__create_issue"]])
def test_one_of_our_records_is_judged_after_a_command_or_an_unrelated_app_tool(used):
    out = note("I paused the bookkeeper agent.", used)
    assert "No agent was paused in this reply." in out, (used, out)
    out = note("I created a schedule that runs the triage workflow every morning.", used)
    assert "No schedule was created in this reply." in out, (used, out)


def test_the_true_claim_after_a_command_is_answered_by_its_card_or_record_tool():
    assert note("I've turned on Read replies aloud.", ["shell", "config_set"], [S("device.read_aloud")]) == ""
    assert note("I paused the bookkeeper agent.", ["shell", "agent_pause"]) == ""


@pytest.mark.parametrize("used", [["delegate"], ["spawn_agent"], ["workflow_run"], ["assign_work"],
                                  ["delegate_remote"]])
def test_after_a_hand_off_nothing_is_judged(used):
    """Kept on purpose: the helper or the workflow holds Iron Jarvis's own
    record and settings tools, and its calls are in its own record."""
    assert note("I've turned on Read replies aloud.", used) == ""
    assert note("I paused the bookkeeper agent.", used) == ""


def test_an_app_tool_that_made_that_kind_of_thing_answers_the_record_claim():
    assert note("I created the project.", ["mcp__asana__create_project"]) == ""
    assert note("I've added the webhook.", ["mcp__github__create_webhook"]) == ""


def test_after_a_web_page_action_only_a_setting_named_by_its_label_is_judged():
    assert note("I created a new project.", ["browser_click"]) == ""
    assert note("I paused the bookkeeper agent.", ["browser_type"]) == ""
    assert ALOUD in note("I've turned on Read replies aloud.", ["browser_click"])


def test_an_everyday_word_for_a_setting_is_not_judged_after_a_command():
    assert note("I turned on dark mode.", ["shell"]) == ""
    assert note("I turned on dark mode.", ["mcp__figma__update_file"]) == ""
    # …and is judged, as before, when nothing like that ran.
    assert note("I turned on dark mode.", []) != ""


def test_the_ledger_recap_and_a_coding_turn_keep_their_rules():
    assert note("I turned on Read replies aloud at 3:12.", ["changes_list"]) == ""
    assert "No schedule" not in note("I updated the scheduler.", ["edit_file", "shell"])


# --------------------------------------------------------------------------- #
# 2. "Sent" is a kind.
# --------------------------------------------------------------------------- #


MUST_SEND_NOTE = [
    ("I emailed the report to Dana.", [], NOT_SENT),
    ("Sent the summary to your Slack channel.", [], "No message was sent to Slack in this reply."),
    ("I've posted the update to #general on Slack.", [], "No message was sent to Slack in this reply."),
    ("Done — I messaged Priya on Telegram with the totals.", [], "No message was sent to Telegram in this reply."),
    ("The reminder has been sent to your phone.", [], NOT_SENT),
    ("I forwarded the invoice to accounting@example.com.", [], NOT_SENT),
    ("I've notified the team on Discord.", [], "No message was sent to Discord in this reply."),
    ("I emailed it to Dana.", ["read_file", "list_files"], NOT_SENT),
    ("I emailed it to Dana.", ["mcp__gmail__search_emails"], NOT_SENT),
    ("Your message was successfully sent to the client.", [], NOT_SENT),
    ("I texted you the meeting time.", [], NOT_SENT),
]

NO_SEND_NOTE = [
    ("I can email it to Dana if you like.", []),
    ("Want me to post this to Slack?", []),
    ("I didn't send anything yet.", []),
    ("I haven't emailed Dana.", []),
    ("Nothing was sent.", []),
    ("You sent me the invoice yesterday.", []),
    ("Dana emailed you the contract on Monday.", []),
    ("The invoice was sent to the client last week.", []),
    ("Here is a draft email you can send to Dana:", []),
    ("I posted the adjusting journal entries to the trial balance.", []),
    ("I drafted the message to Priya below.", []),
    ("Once you approve, I'll send it to Slack.", []),
    ("Your emails from the sent folder are listed below.", []),
    ("I've sent you the summary below.", []),
    ("The email Bob sent says the deadline moved.", []),
    ("If it fails, I emailed nobody.", []),
    ("I sent the summary to Slack.", ["notify"]),
    ("I posted it to #general.", ["mcp__slack__post_message"]),
    ("I emailed it to Dana.", ["mcp__gmail__send_email"]),
    ("I sent the alert to Slack.", ["shell"]),
    ("I sent the alert to Slack.", ["rest_call"]),
    ("I messaged the bookkeeper agent.", ["message_agent"]),
    ("I emailed it to Dana.", ["browser_click"]),
    ("I emailed it to Dana.", ["delegate"]),
]


@pytest.mark.parametrize("reply,used,words", MUST_SEND_NOTE)
def test_a_claimed_send_with_no_send_gets_the_note(reply, used, words):
    out = note(reply, used)
    assert words in out, (reply, used, out)
    assert out.startswith("\n\n_Note: ") and out.endswith("_")


@pytest.mark.parametrize("reply,used", NO_SEND_NOTE)
def test_no_send_note_for_an_offer_a_denial_someone_elses_send_or_a_real_send(reply, used):
    assert "was sent" not in note(reply, used), (reply, used)


def test_the_send_note_joins_the_other_kinds_in_one_line():
    out = note("I've turned on Read replies aloud and emailed the report to Dana.", [])
    assert out.count("_Note:") == 1, out
    assert ALOUD in out and NOT_SENT in out, out


def test_an_earlier_send_excuses_a_summary_of_it_and_its_own_note_does_not():
    hist = [SimpleNamespace(role="user", content="email it"),
            SimpleNamespace(role="assistant", content="I emailed the report to Dana."),
            SimpleNamespace(role="user", content="did it go?")]
    assert note("Yes — I emailed the report to Dana.", [], history=hist) == ""
    hist[1] = SimpleNamespace(role="assistant", content=f"I emailed the report to Dana.\n\n_Note: {NOT_SENT}_")
    assert NOT_SENT in note("Yes — I emailed the report to Dana.", [], history=hist)


def test_the_send_check_is_linear_and_bounded():
    import re

    pats = [v.pattern for v in vars(cc).values() if isinstance(v, re.Pattern)]
    for p in pats:
        assert ".*" not in p and ".+" not in p, p
    import time

    def cost(n: int) -> float:
        text = "I emailed the report to Dana and sent it to Slack, " * n
        best = float("inf")
        for _ in range(3):
            t = time.perf_counter()
            note(text, [])
            best = min(best, time.perf_counter() - t)
        return best

    small, big = cost(100), cost(400)
    assert big < max(small, 0.002) * 9, (small, big)


# --------------------------------------------------------------------------- #
# The check-again loop reads the send too.
# --------------------------------------------------------------------------- #


def test_the_loop_puts_a_claimed_send_and_names_the_send_tool_it_holds():
    f = U.Facts(reply="I emailed the report to Dana.", tools_used=[], armed={"notify", "read_file"})
    probs = [p for p in U.find_problems(f) if p.key.startswith("claimed_sent:")]
    assert len(probs) == 1, U.find_problems(f)
    assert "notify" in probs[0].words and "not sent" in probs[0].words, probs[0].words
    assert not [p for p in U.find_problems(f) if p.key.startswith("claimed_change:")]


def test_the_loop_with_no_send_tool_asks_only_to_say_so():
    f = U.Facts(reply="I emailed the report to Dana.", tools_used=[], armed={"read_file"})
    (p,) = [p for p in U.find_problems(f) if p.key.startswith("claimed_sent:")]
    assert "No tool you hold in this reply can send it" in p.words, p.words


def test_the_loop_has_nothing_to_put_after_a_real_send():
    f = U.Facts(reply="I emailed the report to Dana.", tools_used=["notify"], armed={"notify"})
    assert U.find_problems(f) == []


# --------------------------------------------------------------------------- #
# Both lanes: no send, and a send that failed (no channel is set up).
# --------------------------------------------------------------------------- #


class _Scripted(LLMAdapter):
    provider = "anthropic"
    model = "claude-opus-4-8"

    def __init__(self, calls, final) -> None:
        self.calls, self.final, self.n = list(calls), final, 0

    async def complete(self, *, system, messages, tools, **_kw):
        self.n += 1
        if self.n <= len(self.calls):
            name, args = self.calls[self.n - 1]
            return LLMResponse(text="", tool_calls=[ToolCall(id=f"c{self.n}", name=name, arguments=args)])
        return LLMResponse(text=self.final)


def _lane(tmp_path, lane, calls, final, body, *, channels_fail: bool = False):
    app = create_app(str(tmp_path))
    if channels_fail:
        # The real notify tool, its every channel failing (as a Slack that
        # refuses the token would): it reports ok=False honestly.
        notifier = app.state.platform.registry.get("notify")._notifier
        notifier.notify = lambda message, channels=None: {"slack": {"ok": False, "error": "refused"}}
    model = _Scripted(calls, final)
    app.state.platform.providers.get = lambda prov, m=None, _m=model: _m
    client = TestClient(app)
    if lane == "post":
        r = client.post("/chat", json=body)
        assert r.status_code == 200, r.text
        return app, r.json()
    r = client.post("/chat/stream", json=body)
    assert r.status_code == 200, r.text
    for block in r.text.split("\n\n"):
        lines = block.strip().splitlines()
        if lines and lines[0] == "event: done":
            return app, json.loads("".join(ln[6:] for ln in lines[1:] if ln.startswith("data: ")))
    raise AssertionError(r.text[-1500:])


@pytest.mark.parametrize("lane", ["post", "stream"])
def test_lane_a_claimed_send_with_no_tool_gets_the_note(tmp_path, lane):
    body = {"messages": [{"role": "user", "content": "tell the team the totals are in"}]}
    _app, out = _lane(tmp_path, lane, [], "I've posted the update to your Slack channel.", body)
    assert out["tools_used"] == [], out["tools_used"]
    assert "No message was sent to Slack in this reply." in out["reply"], out["reply"]


@pytest.mark.parametrize("lane", ["post", "stream"])
def test_lane_a_send_that_failed_then_a_claim_gets_the_note(tmp_path, lane):
    body = {"messages": [{"role": "user", "content": "send the team a notification that the totals are in"}],
            "tools": ["notify"], "approval_mode": "approve_for_me"}
    app, out = _lane(tmp_path, lane, [("notify", {"message": "totals are in"})], "I sent the team a notification.",
                     body, channels_fail=True)
    assert "notify" not in out["tools_used"], out["tools_used"]
    from iron_jarvis.core.db import session_scope
    from sqlmodel import select

    with session_scope(app.state.platform.engine) as db:
        rows = db.exec(select(ToolInvocation).where(ToolInvocation.tool == "notify")).all()
    assert rows and not any(r.ok for r in rows), "anti-vacuity: notify ran through the registry and failed"
    assert NOT_SENT in out["reply"], out["reply"]


# --------------------------------------------------------------------------- #
# 3. A finished agent run's summary, against its own ledger.
# --------------------------------------------------------------------------- #


def _ledger(tmp_path, rows):
    engine = create_engine(f"sqlite:///{tmp_path / 'ledger.db'}")
    SQLModel.metadata.create_all(engine, tables=[ToolInvocation.__table__])
    with DB(engine) as db:
        for tool, ok in rows:
            db.add(ToolInvocation(session_id="sess_1", agent_run_id="run_1", tool=tool, ok=ok))
        db.add(ToolInvocation(session_id="sess_other", agent_run_id="run_2", tool="notify", ok=True))
        db.commit()
    return engine


def test_a_run_summary_is_judged_against_its_own_ledger(tmp_path):
    engine = _ledger(tmp_path, [("read_file", True), ("notify", False)])
    out = cc.judge_run_summary(
        "I emailed the report to Dana and turned on Read replies aloud.", engine=engine, session_id="sess_1",
    )
    assert "No message was sent in this run." in out, out
    assert "“Read replies aloud” was not changed in this run." in out, out
    assert "in this reply" not in out, out


def test_a_run_whose_send_succeeded_gets_no_send_line(tmp_path):
    engine = _ledger(tmp_path, [("notify", True)])
    assert cc.judge_run_summary("I emailed the report to Dana.", engine=engine, session_id="sess_1") == ""


def test_a_run_summary_with_an_unreadable_ledger_says_nothing():
    assert cc.judge_run_summary("I emailed the report to Dana.", engine=object(), session_id="x") == ""
