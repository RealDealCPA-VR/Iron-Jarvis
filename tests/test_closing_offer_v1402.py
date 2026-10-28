"""v1.402.0 (part 4, "ask about intent, decide the rest") — A REPLY THAT ENDS
ON AN OFFER IS TOLD TO DO IT.

On the user's own install 9 of 67 chat replies ended in a question, and the
commonest shape was the offer ("Want me to also add tests?"): the turn ended
and the user typed "yes". ``harness/closing_offer.ends_on_offer`` reads the
reply's last sentence deterministically; when it offers more work, a coding
session's turn (BOTH chat lanes, lock-step) and an agent run's final answer
get ONE nudge (``CLOSING_OFFER_NUDGE``) and the loop goes on — never in plain
chat, never in plan mode, never in low trust, never without two rounds left,
never twice. The reply the user keeps is the first answer, a blank line, and
what came after it.

Driven through the REAL lanes and the REAL orchestrator + runtime on a real
platform; only the model is scripted.
"""

from __future__ import annotations

import asyncio

import pytest

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.core.events import EventType
from iron_jarvis.core.nudges import CLOSING_QUESTION
from iron_jarvis.daemon.chat_stream import stream_chat_turn
from iron_jarvis.daemon.chat_turn import run_chat_turn
from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
from iron_jarvis.harness import closing_offer as CO
from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.adapters.mock import MockLLMAdapter
from tests._fakes.harness_lane import WHO, body, call, frames_of, no_finish_check, post, stream, wire

NUDGE = CO.CLOSING_OFFER_NUDGE
SEP = CO.SEPARATOR

# --------------------------------------------------------------------------- #
# the reading: a table of endings
# --------------------------------------------------------------------------- #

OFFERS = [
    "Done. Want me to also add tests?",
    "Would you like me to add a test for the empty case?",
    "Do you want me to update the README too?",
    "Shall I proceed?",
    "Should I go ahead and apply the migration?",
    "Should I continue with the remaining three files?",
    "Should I start on the frontend part?",
    "Let me know if you'd like me to wire it into the CLI.",
    "Let me know if you want me to add type hints as well.",
    "If you'd like, I can also refactor the parser.",
    "If you want, I can add a --dry-run flag.",
    "I can also add logging if you want.",
    "I can also write a migration script if you'd like.",
    "Happy to add the integration tests too if you want.",
    "Want me to run the full test suite?",
    "Next, shall I add pagination?",
    "Shall I go ahead, or would you rather review the diff first?",
    "Should I proceed with the remaining endpoints?",
    "I can also update the docs — want me to?",
    "Do you want me to fix the two lint warnings as well?",
    "Would you like me to go ahead and implement this?",
    "**Want me to add a CHANGELOG entry?**",
    "- Want me to also handle the Windows paths?",
    "Ready for me to apply the patch?",
    "Say the word and I'll add the caching layer.",
    "I fixed the bug in parser.py and the tests pass.\n\nWant me to add a regression test?",
    "Should I also update the config schema?",
    "Would you like me to proceed? \U0001F642",
    "If that looks right, I can apply the same fix to the other two modules — just let me know.",
    "The parser now handles empty input.\n\n```python\nparse('')\n```\n\nWant me to add the same guard to the lexer?",
    "Done!\nWant me to also add tests?",
    "I'd be happy to add the retry logic too if that's helpful.",
    "The fix is in.\r\n\r\nShould I also apply it to the v2 branch?",
    # review (v1.402.0): common phrasings the first cut missed
    "Would it help if I added a regression test?",
    "Let me know and I'll add the tests.",
    "Should I add tests as well?",
    "Should I write a migration for the new column?",
    "Should I fix the other two call sites as well?",
    "Need me to add type hints?",
    "Let me know if I should add a --verbose flag.",
    "Next, I could add pagination to the list endpoint.",
    "Happy to add tests for this too.",
    # work whose words merely look like an outward action
    "Should I update the release notes?",
    "Want me to add a POST endpoint for it?",
    "Want me to send a test request to the dev server?",
    "Want me to remove the unused imports?",
]

#: Review (v1.402.0): an offer whose action is the USER'S decision — it reaches
#: other people, money or the shared history, or cannot be taken back. The
#: reply keeps its question (the git guard asks before every commit and push
#: for the same reason), so these are NOT offers the nudge acts on.
USER_OWNED = [
    "Let me know if you'd like me to send this to the client.",
    "Would you like me to email the summary to Dana?",
    "Want me to post this to the team's Slack channel?",
    "Shall I publish the post?",
    "Want me to share it with the team?",
    "Would you like me to commit these changes?",
    "Want me to push it?",
    "Say the word and I'll push the branch.",
    "Should I go ahead and merge the PR?",
    "Should I merge this into main?",
    "Want me to open a pull request?",
    "Should I deploy it?",
    "Shall we ship it?",
    "Want me to release v2?",
    "Shall I submit the form?",
    "Would you like me to pay the invoice?",
    "Should I buy the domain?",
    "Shall I delete the old files?",
    "Is it okay if I delete the cache folder?",
    "Want me to remove the old migration files?",
    "Want me to drop the users table?",
    "Should I proceed with sending the invoices?",
    "I can send it to the team if you'd like.",
    "Should I run the migration against the production database?",
    "Would you like me to send it, or keep it as a draft?",
]

NOT_OFFERS = [
    # a genuine question about intent: nothing offered
    "Which colour should the button be?",
    "Do you prefer A or B?",
    "What should the default timeout be?",
    "Do you want the report as a PDF or a Word file?",
    "Here is the plan:\n1. Parse.\n2. Validate.\n\nDoes this plan match what you want?",
    "Would you like to review the diff first?",
    "Can you confirm the API key is set?",
    "I can't run the tests without the API key — let me know where it lives.",
    "I couldn't find a config file; let me know if there is one somewhere else.",
    # a choice between alternatives is a decision question, not an offer
    "Would you like me to use Postgres or SQLite?",
    "Should I use tabs or spaces?",
    "I added the tests. Should the CLI flag be called --force or --yes?",
    "Do you want me to keep the old flag or remove it?",
    # inside a code block or a quote
    "Here is a sketch:\n\n```python\n# Want me to add tests?\nx = 1\n```",
    "```python\nprint('Want me to?')\n```",
    "> Shall I proceed?",
    "Some notes:\n\n```\nprint(1)\n\nWant me to add tests?",  # an UNCLOSED block
    # earlier in the reply, not the ending
    "Want me to add tests? I went ahead and added them in tests/test_x.py.",
    "Why did the test fail? Because the fixture was stale; I fixed it.",
    "Should I have used a different approach? No — the existing helper already covers it.",
    # done, nothing offered
    "All done — the build passes.",
    "Let me know if you have any questions.",
    "Let me know what you think.",
    "Hope this helps!",
    "Feel free to ask if you need anything else.",
    "Next steps you could take: deploy to staging.",
    "",
    # an offer to EXPLAIN is not work
    "Let me know if you'd like me to explain any part of this.",
    "Would you like me to walk you through the changes?",
    # vague: nothing specific to do
    "Is there anything else you'd like me to do?",
    "Want me to change anything else?",
    "Would you like me to make any other changes?",
    # DECIDED: a reply that is done and only parks an idea for LATER
    "If you'd like, I can add rate limiting later.",
    "Done.\n\nA possible follow-up for another time: caching the lookups.",
    # review (v1.402.0): an offer to explain, phrased "Shall I …"; a vague
    # "further changes"; a pleasantry with "too"
    "Shall I explain how the parser works?",
    "Let me know if you'd like me to make further changes.",
    "Glad to hear it works for you too!",
    "Happy to report the tests pass too.",
    "Next, I can't go on without the API key.",
    "Next, I could not reproduce the crash.",
    "The tests pass. I could also add a benchmark in a follow-up PR if you want.",
    "All four endpoints are fixed and the tests pass.\n\nIf you'd like, I can also add rate limiting in the future.",
]


def test_the_tables_are_big_enough():
    assert len(OFFERS) >= 25 and len(NOT_OFFERS) >= 25


@pytest.mark.parametrize("text", OFFERS)
def test_an_ending_that_offers_more_work_is_an_offer(text):
    assert CO.ends_on_offer(text), repr(CO.last_sentence(text))


@pytest.mark.parametrize("text", NOT_OFFERS)
def test_an_ending_that_offers_no_work_is_not_an_offer(text):
    assert not CO.ends_on_offer(text), repr(CO.last_sentence(text))


@pytest.mark.parametrize("text", USER_OWNED)
def test_review_an_offer_to_send_publish_push_pay_or_delete_keeps_its_question(text):
    """Review (v1.402.0): the first cut nudged "Want me to send this to the
    client?", "Shall I delete the old files?" and "Want me to push it?" —
    and the nudge said "do it now". Those are the user's to decide."""
    assert CO.user_owns(CO.last_sentence(text)), repr(CO.last_sentence(text))
    assert not CO.ends_on_offer(text), repr(CO.last_sentence(text))


def test_review_the_nudge_itself_refuses_outward_and_irreversible_actions():
    """An offer that only points back ("Next I would push and deploy. Shall I
    proceed?") still reads as an offer — the nudge's own words keep it from
    being taken as permission."""
    assert CO.ends_on_offer("The fix is ready. Next I would push to main and deploy it. Shall I proceed?")
    for verb in ("send", "post", "share", "publish", "commit", "push", "merge", "deploy", "delete",
                 "pay for", "buy"):
        assert verb in NUDGE, verb
    assert "those are the user's decisions" in NUDGE


def test_decided_a_done_reply_with_an_idea_for_later_keeps_its_ending_but_an_offer_now_is_one():
    done = "All four endpoints are fixed and the tests pass.\n\n"
    assert not CO.ends_on_offer(done + "If you'd like, I can add rate limiting later.")
    assert CO.ends_on_offer(done + "If you'd like, I can also add rate limiting.")


def test_the_gate_reads_only_an_ending_with_no_call_armed_and_rounds_left():
    def wants(**kw):
        args = {"has_calls": False, "round_index": 0, "rounds": 10, "armed": True}
        args.update(kw)
        return CO.ClosingOffer().wants("Done. Want me to also add tests?", **args)

    assert wants()  # the control
    assert not wants(has_calls=True)
    assert not wants(armed=False)
    assert not wants(truncated=True)
    assert not wants(low_trust=True)
    assert not wants(plan_mode=True)
    assert not wants(after_finish=True)
    assert not wants(round_index=8) and wants(round_index=7)  # 1 left / 2 left
    offer = CO.ClosingOffer()
    assert offer.take("A?") == CO.CLOSING_OFFER_NUDGE and offer.fired
    assert not offer.wants("Want me to?", has_calls=False, round_index=0, rounds=10, armed=True)


def test_review_a_resumed_reply_past_its_finish_note_is_not_nudged():
    """Review (v1.402.0): a reply resumed after a restart whose cut part had
    already sent the finish note (``FinishCheck.checked_before``) is past the
    check — its next ending is the check's answer, which ends the reply."""
    from types import SimpleNamespace

    from iron_jarvis.daemon.chat_turn import closing_offer_wanted

    def wanted(**finish):
        harness = SimpleNamespace(closing_offer=CO.ClosingOffer(), plan_mode=False)
        check = SimpleNamespace(**{"ran": False, "checked_before": False, **finish})
        return closing_offer_wanted(harness, check, "Done. Want me to also add tests?", has_calls=False,
                                    round_index=0, rounds=10, armed=True, truncated=False, trust_state={})

    assert wanted() is True  # the control
    assert wanted(ran=True) is False
    assert wanted(checked_before=True) is False


def test_the_join_never_says_a_text_twice():
    assert CO.join("A. Want me to?", "Did it.") == "A. Want me to?" + SEP + "Did it."
    assert CO.join("A. Want me to?", "A. Want me to?") == "A. Want me to?"
    assert CO.join("A. Want me to?", "") == "A. Want me to?"
    assert CO.join("A. Want me to?", "A. Want me to?\n\nDid it.") == "A. Want me to?\n\nDid it."


# --------------------------------------------------------------------------- #
# the chat lanes (a coding session's turn)
# --------------------------------------------------------------------------- #


@pytest.fixture()
def env(tmp_path, monkeypatch):
    platform = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "app.py").write_bytes(b"X = 1\n")
    events: list[tuple[str, dict, str | None]] = []
    real = platform.event_bus.publish

    async def spy(event_type, payload=None, *a, **kw):
        if str(getattr(event_type, "value", event_type)) == str(EventType.AUTONOMY_NUDGED):
            events.append((str(event_type), dict(payload or {}), kw.get("session_id")))
        return await real(event_type, payload, *a, **kw)

    monkeypatch.setattr(platform.event_bus, "publish", spy)
    return platform, ws, events


async def _lane(lane: str, platform, b, **kw) -> tuple[dict, list]:
    if lane == "stream":
        out = await stream(platform, b, **kw)
        assert not out["errors"], out["errors"]
        return out["done"], out["frames"]
    return await post(platform, b, **kw), []


def _nudges(model) -> int:
    """How many times the nudge was SENT (each call carries the whole
    conversation, so a nudge is counted by its first appearance)."""
    return max((sum(1 for m in c["messages"] if m.role == "user" and m.content == NUDGE) for c in model.calls),
               default=0)


FIRST = "app.py sets X to 1 and nothing reads it. Want me to also add a test for it?"
TEST_FILE = "def test_x():\n    import app\n    assert app.X == 1\n"


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_an_offer_with_rounds_left_gets_one_nudge_and_the_next_round_does_the_work(env, lane, monkeypatch):
    platform, ws, events = env
    no_finish_check(ws)
    model = wire(platform, monkeypatch, [
        FIRST,
        [call("write_file", "w1", path="test_app.py", content=TEST_FILE)],
        "Added test_app.py; it checks X.",
    ])
    done, frames = await _lane(lane, platform, body(ws, "what does app.py do?"))
    assert len(model.calls) == 3, [c["messages"][-1].content[:50] for c in model.calls]
    # The second call was sent the first answer, then the nudge.
    sent = model.calls[1]["messages"]
    assert sent[-1].role == "user" and sent[-1].content == NUDGE
    assert sent[-2].role == "assistant" and sent[-2].content == FIRST
    # ...and the scripted model's next round RAN a tool.
    assert (ws / "test_app.py").read_text(encoding="utf-8") == TEST_FILE
    assert _nudges(model) == 1
    # The reply reads naturally: the first answer, a blank line, the rest.
    assert done["reply"] == FIRST + SEP + "Added test_app.py; it checks X."
    assert done["reply"].count(FIRST) == 1
    # ONE autonomy.nudged event, of this kind.
    assert [e[1]["kind"] for e in events] == [CLOSING_QUESTION]
    assert events[0][1]["surface"] == "build" and events[0][2] == "build:phase3-test"
    if lane == "stream":
        tokens = "".join(d["text"] for e, d in frames if e == "token")
        assert tokens == FIRST + SEP + "Added test_app.py; it checks X."


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_the_nudge_never_fires_twice_in_a_reply(env, lane, monkeypatch):
    platform, ws, events = env
    no_finish_check(ws)
    again = "That one needs your call: shall I proceed? I recommend yes."
    second = "Before I add it — want me to also add a fixture file?"
    model = wire(platform, monkeypatch, [FIRST, second, again])
    done, _ = await _lane(lane, platform, body(ws, "what does app.py do?"))
    assert len(model.calls) == 2
    assert _nudges(model) == 1
    assert done["reply"] == FIRST + SEP + second
    assert len(events) == 1


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_nudge_already_in_the_transcript_counts_as_fired(env, lane, monkeypatch):
    """A reply resumed after a restart carries its earlier rounds — the
    nudge among them. A fresh turn's state never sends it a second time."""
    platform, ws, events = env
    no_finish_check(ws)
    model = wire(platform, monkeypatch, [FIRST])
    b = ChatBody(messages=[
        ChatMessageBody(role="user", content="what does app.py do?"),
        ChatMessageBody(role="assistant", content="It sets X. Want me to add a test?"),
        ChatMessageBody(role="user", content=NUDGE),
    ], provider=WHO[0], model=WHO[1], workspace_dir=str(ws), approval_mode="yolo")
    done, _ = await _lane(lane, platform, b)
    assert len(model.calls) == 1 and not events
    assert done["reply"] == FIRST


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_never_in_plain_chat(tmp_path, lane, monkeypatch):
    """No harness: the reply ends where the model ended it — one model call,
    the reply byte for byte the model's, and the nudge's reader never asked."""
    platform = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir()
    model = wire(platform, monkeypatch, [FIRST])
    asked: list = []
    real_wants = CO.ClosingOffer.wants

    def spy(self, *a, **k):
        asked.append(a)
        return real_wants(self, *a, **k)

    monkeypatch.setattr(CO.ClosingOffer, "wants", spy)
    b = ChatBody(messages=[ChatMessageBody(role="user", content="add a test for app.py")],
                 provider=WHO[0], model=WHO[1], workspace_dir=str(ws), tools=["write_file"],
                 approval_mode="yolo")
    if lane == "stream":
        gen = await stream_chat_turn(platform, BUILTIN_PERSONAS, b)
        frames = frames_of([c async for c in gen])
        done = [d for e, d in frames if e == "done"][0]
    else:
        done = await run_chat_turn(platform, BUILTIN_PERSONAS, b)
    assert len(model.calls) == 1
    assert _nudges(model) == 0 and asked == []
    assert done["reply"] == FIRST


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_never_in_low_trust(env, lane, monkeypatch):
    platform, ws, events = env
    no_finish_check(ws)
    model = wire(platform, monkeypatch, [FIRST, "Done."])
    done, _ = await _lane(lane, platform, body(ws, "what does app.py do?"), trust="low", trust_reason="test")
    assert len(model.calls) == 1 and _nudges(model) == 0 and not events
    assert done["reply"].startswith(FIRST)


async def test_never_in_plan_mode(env, monkeypatch):
    platform, ws, events = env
    no_finish_check(ws)
    plan = "Plan: 1. add test_app.py. 2. run it. Shall I proceed?"
    model = wire(platform, monkeypatch, [plan])
    out = await stream(platform, body(ws, "plan a test for app.py"), plan_mode=True)
    assert len(model.calls) == 1 and _nudges(model) == 0 and not events
    assert out["done"]["reply"].startswith(plan)


@pytest.mark.parametrize("lane", ["stream", "post"])
@pytest.mark.parametrize(("budget", "nudged"), [(2, False), (3, True)])
async def test_only_with_two_rounds_left(env, lane, budget, nudged, monkeypatch):
    """Round 0 of a 2-round reply has ONE round left: no nudge. Of a 3-round
    reply, two: the nudge (one round to work, one to say what it did)."""
    platform, ws, events = env
    no_finish_check(ws)
    model = wire(platform, monkeypatch, [FIRST, "No test needed after all: X is a constant."])
    await _lane(lane, platform, body(ws, "what does app.py do?"), round_budget=budget)
    assert (_nudges(model) == 1) is nudged
    assert len(events) == (1 if nudged else 0)


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_tool_round_is_never_nudged(env, lane, monkeypatch):
    """Words that end on an offer BESIDE a tool call are not the reply's end."""
    platform, ws, events = env
    no_finish_check(ws)
    model = wire(platform, monkeypatch, [
        ("Reading it first. Want me to also add tests?", [call("read_file", "r1", path="app.py")]),
        "X is 1.",
    ])
    done, _ = await _lane(lane, platform, body(ws, "what does app.py do?"))
    assert len(model.calls) == 2 and _nudges(model) == 0 and not events
    assert done["reply"] == "X is 1."


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_it_runs_before_the_finish_check_and_each_runs_once(env, lane, monkeypatch):
    """A reply that CHANGED files and ends on an offer: the offer is nudged
    first; the finish check runs when the model ends again; neither twice."""
    platform, ws, events = env
    offer = "Set X to 2. Want me to also add a test for it?"

    def answer(m):
        assert m.calls[-1]["messages"][-1].content.startswith("FINISH CHECK."), "the finish note"
        return "1. met: app.py says X = 2.\nAll set."

    model = wire(platform, monkeypatch, [
        [call("write_file", "w1", path="app.py", content="X = 2\n")],
        offer,
        [call("write_file", "w2", path="test_app.py", content=TEST_FILE)],
        "Added test_app.py.",
        answer,
        "Want me to also add a docstring?",  # never reached: the reply ended
    ])
    done, _ = await _lane(lane, platform, body(ws, "set X to 2 in app.py"))
    assert len(model.calls) == 5, [c["messages"][-1].content[:40] for c in model.calls]
    # ORDER: the offer's nudge is the third call's last message, the finish
    # check's note the fifth's.
    assert model.calls[2]["messages"][-1].content == NUDGE
    assert model.calls[4]["messages"][-1].content.startswith("FINISH CHECK.")
    assert _nudges(model) == 1
    notes = {m.content for c in model.calls for m in c["messages"]
             if m.role == "user" and str(m.content).startswith("FINISH CHECK.")}
    assert len(notes) == 1
    assert "finish" in done
    # The reply: the offer's answer, then the finish check's draft and answer.
    assert done["reply"] == offer + SEP + "Added test_app.py." + SEP + "All set."
    assert len(events) == 1


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_decided_never_after_the_finish_check_has_run(env, lane, monkeypatch):
    """DECIDED: once the finish check has run, its answer ENDS the reply — an
    offer at the end of that answer is not nudged (and the reply keeps the
    draft first, its answer second, exactly as before this release)."""
    platform, ws, events = env
    model = wire(platform, monkeypatch, [
        [call("write_file", "w1", path="app.py", content="X = 2\n")],
        "Set X to 2.",
        "1. met: app.py says X = 2.\nWant me to also add a docstring?",
    ])
    done, _ = await _lane(lane, platform, body(ws, "set X to 2 in app.py"))
    assert len(model.calls) == 3 and _nudges(model) == 0 and not events
    assert done["reply"] == "Set X to 2." + SEP + "Want me to also add a docstring?"


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_silent_finish_after_the_nudge_still_gets_its_summary(env, lane, monkeypatch):
    """The work ran, the model ended with no words: the final-answer nudge
    (v1.246.0) asks for the summary, and the reply is the offer's answer,
    then that summary — never the offer alone, which would read as undone."""
    platform, ws, events = env
    no_finish_check(ws)
    model = wire(platform, monkeypatch, [
        FIRST,
        [call("write_file", "w1", path="test_app.py", content=TEST_FILE)],
        "",
    ])
    done, _ = await _lane(lane, platform, body(ws, "what does app.py do?"))
    # The tool-less summary call answers "Done." (the scripted model's rule).
    assert done["reply"] == FIRST + SEP + "Done."
    assert _nudges(model) == 1


# --------------------------------------------------------------------------- #
# the agent runtime
# --------------------------------------------------------------------------- #


def _agent(tmp_path, steps: list, monkeypatch=None, **session_kw):
    """Run one agent job on a scripted model. ``steps``: per model call, a
    reply text or a list of ToolCalls. Returns (session, calls, events)."""
    p = build_platform(str(tmp_path / "home"))
    calls: list[list] = []
    events: list[dict] = []
    real = p.event_bus.publish

    async def spy(event_type, payload=None, *a, **kw):
        if str(getattr(event_type, "value", event_type)) == str(EventType.AUTONOMY_NUDGED):
            events.append({**dict(payload or {}), "_session": kw.get("session_id")})
        return await real(event_type, payload, *a, **kw)

    p.event_bus.publish = spy  # type: ignore[method-assign]
    script = list(steps)

    class Scripted(MockLLMAdapter):
        async def complete(self, *, system, messages, tools, **kw):
            calls.append(list(messages))
            move = script.pop(0) if script else "Done."
            if isinstance(move, list):
                return LLMResponse(tool_calls=move, finish_reason="tool_use")
            return LLMResponse(text=move, finish_reason="stop")

    p.providers.register("mock", lambda model=None: Scripted())
    orch = Orchestrator(p)

    async def go():
        s = await orch.create_session("list the files and say what is there", provider="mock",
                                      model="mock-1", **session_kw)
        return await orch.run_session(s.id)

    return asyncio.run(go()), calls, events


AGENT_FIRST = "There is one file, notes.txt. Want me to also list the folder above it?"
OFFER_CUT_LEAD_TEXT = "reached its step limit before finishing that"
LIST = [ToolCall(id="l1", name="list_files", arguments={"path": "."})]


def test_an_agent_runs_final_answer_that_offers_gets_one_nudge_and_carries_on(tmp_path):
    s, calls, events = _agent(tmp_path, [AGENT_FIRST, LIST, "Listed it: nothing else is there."])
    assert len(calls) == 3
    assert calls[1][-1].role == "user" and calls[1][-1].content == NUDGE
    assert calls[1][-2].role == "assistant" and calls[1][-2].content == AGENT_FIRST
    assert any(m.role == "tool" for m in calls[2])  # the next step RAN a tool
    assert s.summary == AGENT_FIRST + SEP + "Listed it: nothing else is there."
    assert [(e["kind"], e["surface"], e["_session"]) for e in events] == [(CLOSING_QUESTION, "agent", s.id)]


def test_an_agent_run_is_nudged_once_only(tmp_path):
    again = "Should I also check the parent of that folder?"
    s, calls, events = _agent(tmp_path, [AGENT_FIRST, again, "never reached"])
    assert len(calls) == 2
    assert s.summary == AGENT_FIRST + SEP + again
    assert len(events) == 1


def test_an_agent_run_without_two_steps_left_is_not_nudged(tmp_path):
    s, calls, events = _agent(tmp_path, [AGENT_FIRST], max_steps=2)
    assert len(calls) == 1 and not events
    assert s.summary == AGENT_FIRST


def test_an_agent_run_in_low_trust_is_not_nudged(tmp_path):
    s, calls, events = _agent(tmp_path, [AGENT_FIRST], trust="low", trust_reason="test")
    assert len(calls) == 1 and not events
    assert s.summary == AGENT_FIRST


def test_an_unattended_run_is_nudged_too(tmp_path):
    """Nobody is there to type "yes" — the nudge matters most here."""
    s, calls, events = _agent(tmp_path, [AGENT_FIRST, LIST, "Done listing."], origin="schedule:nightly")
    assert len(events) == 1 and s.summary == AGENT_FIRST + SEP + "Done listing."


def test_a_silent_agent_finish_after_the_nudge_says_what_it_ran(tmp_path):
    s, calls, events = _agent(tmp_path, [AGENT_FIRST, LIST, ""])
    assert s.summary.startswith(AGENT_FIRST + SEP + "The model finished without writing a summary.")
    assert "list_files x1" in s.summary


def test_an_agent_answer_that_is_not_an_offer_is_unchanged(tmp_path):
    s, calls, events = _agent(tmp_path, ["There is one file, notes.txt."])
    assert len(calls) == 1 and not events and s.summary == "There is one file, notes.txt."


def test_the_runtime_reads_only_the_flat_lanes_answer_with_tools_and_never_a_reviewers(tmp_path):
    """A decomposed step's answer (``transcript`` False) is not the run's, a
    run with no tools cannot do the work, and a read-only reviewer's verdict
    is its report's last line — none is nudged."""
    from types import SimpleNamespace

    from iron_jarvis.agents.runtime import AgentRuntime

    rt = AgentRuntime(build_platform(str(tmp_path / "home")))
    session = SimpleNamespace(id="s1", trust="full", options_json=None)

    def wanted(**kw):
        args = {"transcript": True, "tool_specs": [{"name": "list_files"}], "messages": [], "steps_left": 10}
        args.update(kw)
        return rt._closing_offer_wanted(args.pop("session", session), AGENT_FIRST, **args)

    assert wanted() is True  # the control
    assert wanted(transcript=False) is False
    assert wanted(tool_specs=[]) is False
    assert wanted(steps_left=1) is False and wanted(steps_left=2) is True
    reviewer = SimpleNamespace(id="s2", trust="full", options_json='{"review_only": true}')
    assert wanted(session=reviewer) is False


def test_review_a_child_run_is_never_nudged(tmp_path):
    """Review (v1.402.0): a delegated or spawned teammate, a Build helper or a
    mission's member hands its answer to the MODEL that gave out the piece —
    no user waits on its "Want me to …?", and a child widening its own piece
    can collide with its siblings'."""
    from types import SimpleNamespace

    from iron_jarvis.agents.runtime import AgentRuntime
    from iron_jarvis.agents.team import MISSION_MEMBER_ORIGIN
    from iron_jarvis.harness.helpers import HELPER_ORIGIN

    rt = AgentRuntime(build_platform(str(tmp_path / "home")))

    def wanted(session, **kw):
        return rt._closing_offer_wanted(session, AGENT_FIRST, transcript=True,
                                        tool_specs=[{"name": "list_files"}], messages=[], steps_left=10, **kw)

    top = SimpleNamespace(id="s1", trust="full", options_json=None, origin="chat")
    assert wanted(top) is True  # the control
    assert wanted(top, child=True) is False
    for origin in (HELPER_ORIGIN, MISSION_MEMBER_ORIGIN):
        assert wanted(SimpleNamespace(id="s2", trust="full", options_json=None, origin=origin)) is False


def test_review_a_delegated_childs_offer_is_its_answer_unchanged(tmp_path):
    """Through the REAL runtime: a child run (``parent_id`` set) that ends on
    an offer returns that answer as it is — one model call, no nudge."""
    from iron_jarvis.agents.runtime import AgentRuntime
    from iron_jarvis.agents.types import get_agent_definition

    p = build_platform(str(tmp_path / "home"))
    calls: list = []

    class Scripted(MockLLMAdapter):
        async def complete(self, *, system, messages, tools, **kw):
            calls.append(list(messages))
            return LLMResponse(text=AGENT_FIRST, finish_reason="stop")

    p.providers.register("mock", lambda model=None: Scripted())
    orch = Orchestrator(p)

    async def go():
        s = await orch.create_session("list the files", provider="mock", model="mock-1")
        return await AgentRuntime(p).run(s, get_agent_definition(s.agent_type), parent_id="parent-run")

    run = asyncio.run(go())
    assert len(calls) == 1 and run.result == AGENT_FIRST
    assert not any(m.role == "user" and m.content == NUDGE for m in calls[0])


def test_review_extra_work_that_runs_out_of_steps_keeps_the_finished_answer(tmp_path):
    """Review (v1.402.0): the nudge sent a FINISHED run on to the work it
    offered; when that work ran out of steps the whole run ended FAILED with
    "reached max steps" and the answer was lost. The answer stands; what the
    extra steps did, and that they were cut, follows it."""
    from iron_jarvis.agents.runtime import OFFER_CUT_LEAD

    s, calls, events = _agent(tmp_path, [AGENT_FIRST, LIST, LIST, LIST], max_steps=3)
    assert len(calls) == 3 and len(events) == 1
    assert s.status.value == "completed", (s.status, s.summary)
    assert s.summary.startswith(AGENT_FIRST + SEP + OFFER_CUT_LEAD), s.summary
    assert "list_files x2" in s.summary


def test_review_a_run_that_never_finished_still_fails_at_its_step_limit(tmp_path):
    """The control: no closing offer, the step limit is the old failure."""
    s, calls, events = _agent(tmp_path, [LIST, LIST, LIST], max_steps=3)
    assert len(calls) == 3 and not events
    assert s.status.value == "failed", (s.status, s.summary)
    assert OFFER_CUT_LEAD_TEXT not in (s.summary or "")


def test_the_nudge_in_a_runs_messages_is_its_record(tmp_path):
    from iron_jarvis.agents.runtime import _closing_offer_reply
    from iron_jarvis.providers.adapters.base import LLMMessage

    msgs = [
        LLMMessage(role="user", content="task"),
        LLMMessage(role="assistant", content=AGENT_FIRST),
        LLMMessage(role="user", content=NUDGE),
    ]
    assert CO.nudge_index(msgs) == 2
    assert _closing_offer_reply(msgs, "Did it.") == AGENT_FIRST + SEP + "Did it."
    # Nothing done after it, and no words: the first answer stands alone.
    assert _closing_offer_reply(msgs, "") == AGENT_FIRST
    assert _closing_offer_reply(msgs[:2], "Did it.") == "Did it."
