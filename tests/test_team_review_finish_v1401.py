"""v1.401.0 — TEAM REVIEW AT THE FINISH.

The benchmark of 2026-10-27 measured it: a Sonnet planner shown its model
team, with "Hand work to my team first" on and the words proven to reach it,
handed the team work in 0 of 108 runs. Soft guidance does not move the
planner, so Iron Jarvis does it ITSELF: when the setting is on and a member
holds the Review role, the finish check of a Build reply that changed files
hands the change to the team's reviewer — one more ``delegate`` call
(``role: review``) of the check's own round — and the reviewer's report goes
into the one note the planner answers.

What these pins hold, through the REAL lanes (the stream lane and the POST
lane in harness mode), the real registry, the real ``delegate`` door, the
real team router and the real model router — only the models are scripted
adapters registered on the real provider manager:

1. OFF (the setting off, no member with the Review role, low trust, no
   ``delegate`` in the turn, ``review = false`` in the folder) is exactly the
   check it was: the same calls, the same note, no ``review`` on the record.
2. ON: the review call comes after the test calls, asks for the ROLE, its
   task holds the request, the coder's words and a real diff; its ledger row
   says ``called_as`` "team review".
3. The reviewer is READ-ONLY: it is armed with the turn's reading tools only
   (never its own notebook either); a write or a command it calls is refused
   "not armed" and nothing on disk changes.
4. The verdict is the report's LAST line only, never from a low-trust run,
   never from a report the runtime wrote.
5. The review is the harness's call, never the reply's own work: not in
   ``tools_used``, not a possible writer, not a command that changed files.
6. A refusal by the team router is said in its own words on the record, and
   the note says nothing about a review. Stop during the review is a stop.
7. The record rides the done frame, the POST answer and the saved reply line
   (whitelisted); the benchmark counts the review as member work — never as
   a piece the PLANNER handed (review: ``team_report.planner_handed``).
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlmodel import select

from iron_jarvis.agents import model_team as MT
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentType, Session, SessionStatus, ToolInvocation
from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.chat_stream import stream_chat_turn
from iron_jarvis.daemon.chat_turn import run_chat_turn
from iron_jarvis.daemon.routes.chat import _approvals
from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
from iron_jarvis.harness import finish as F
from iron_jarvis.harness import helpers as H
from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
from iron_jarvis.platform import build_platform
from iron_jarvis.providers import router as router_mod
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMResponse, ToolCall
from iron_jarvis.tools.base import ToolResult

PLANNER = ("google", "gemini-3.8-flash")
LOCAL = ("ollama", "qwen3:8b")
CLOUD = ("codex-cli", "gpt-6-astra")
PROVIDERS = (PLANNER[0], LOCAL[0], CLOUD[0])
SESSION = "build:team-review"
REQUEST = "set X to 2 in app.py"
VERDICT_BAD = "app.py sets X = 2 but nothing tests it.\nVERDICT: changes needed"


# --------------------------------------------------------------------------- #
# the world: scripted adapters behind the REAL router
# --------------------------------------------------------------------------- #


def _first_user(messages: Any) -> str:
    for m in messages or ():
        if getattr(m, "role", "") == "user":
            return str(getattr(m, "content", "") or "")
    return ""


class Scripted(LLMAdapter):
    """One provider's model. The coding session's turn (its system prompt
    carries the IJ harness block) plays ``world.main``; any other call is a
    helper and plays ``world.reviewer`` (one move per call)."""

    def __init__(self, provider: str, model: "str | None", world: SimpleNamespace) -> None:
        self.provider = provider
        self.model = model or {PLANNER[0]: PLANNER[1], LOCAL[0]: LOCAL[1], CLOUD[0]: CLOUD[1]}[provider]
        self.world = world

    def capabilities(self):
        return {"tool_use": True, "vision": True}

    async def complete(self, *, system, messages, tools, **kw):
        final = None
        async for frame in self.stream(system=system, messages=messages, tools=tools, **kw):
            if frame["type"] == "final":
                final = frame["response"]
        return final

    async def stream(self, *, system, messages, tools, **kw):
        w = self.world
        main = "# IJ harness" in (system or "")
        rec = {"provider": self.provider, "model": self.model, "main": main,
               "tools": [t["name"] for t in tools or []], "system": system or "",
               "messages": list(messages or []), "task": _first_user(messages)}
        w.calls.append(rec)
        if main:
            move: Any = w.main.pop(0) if (w.main and tools) else "Done."
        else:
            move = w.reviewer.pop(0) if w.reviewer else "Looks fine.\nVERDICT: approved"
        if callable(move):
            move = move(rec)
            if inspect.isawaitable(move):
                move = await move
        usage = {"input_tokens": 5, "output_tokens": 2}
        resp = (LLMResponse(text=move, usage=usage) if isinstance(move, str)
                else LLMResponse(text="", tool_calls=list(move), finish_reason="tool_use", usage=usage))
        if resp.text:
            yield {"type": "text", "text": resp.text}
        yield {"type": "final", "response": resp}


def wire(p: Any, monkeypatch: Any, ws: Path) -> SimpleNamespace:
    w = SimpleNamespace(p=p, calls=[], main=[], reviewer=[], ws=ws, notes=[])
    p.config.ollama_base_url = "http://127.0.0.1:11434"
    p.providers.configure_local(ollama_base_url="http://127.0.0.1:11434", ollama_model=LOCAL[1],
                                custom_base_url="", custom_model="")
    for name in PROVIDERS:
        p.providers.register(name, lambda model=None, n=name: Scripted(n, model, w))
    real = p.providers.available
    monkeypatch.setattr(p.providers, "available", lambda name: True if name in PROVIDERS else real(name))
    monkeypatch.setattr(p.providers, "inherited_from", lambda name: None)
    monkeypatch.setattr(router_mod.random, "uniform", lambda a, b: 0.0)
    p.config.team_first = True
    return w


@pytest.fixture
def world(tmp_path, monkeypatch):
    p = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "app.py").write_bytes(b"X = 1\n")
    return wire(p, monkeypatch, ws)


def reviewer(p: Any, name: str = "rev", who: tuple[str, str] = LOCAL, roles=("review",)) -> Any:
    return p.agents_registry.register(name, f"You are {name}.", [], description=f"{name} reviews code",
                                      provider=who[0], model=who[1], roles=list(roles))


def capture_finish(monkeypatch) -> list:
    """Every FinishCheck the lanes build (both call ``chat_turn.finish_for_turn``)."""
    got: list = []
    real = chat_turn.finish_for_turn

    async def wrapped(*a, **kw):
        check = await real(*a, **kw)
        got.append(check)
        return check

    monkeypatch.setattr(chat_turn, "finish_for_turn", wrapped)
    return got


def body(ws: Path, text: str = REQUEST, who: tuple[str, str] = PLANNER, **kw) -> ChatBody:
    return ChatBody(messages=[ChatMessageBody(role="user", content=text)], provider=who[0], model=who[1],
                    workspace_dir=str(ws), approval_mode=kw.pop("approval_mode", "yolo"), **kw)


def frames_of(chunks: list[str]) -> list[tuple[str, dict]]:
    out = []
    for chunk in chunks:
        for block in chunk.split("\n\n"):
            event, data = "", []
            for line in block.splitlines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].strip())
            if event:
                out.append((event, json.loads("".join(data)) if data else {}))
    return out


async def stream(p: Any, b: ChatBody, *, session_id: str = SESSION, **kw) -> dict:
    approvals = _approvals(SimpleNamespace(platform=p))
    gen = await stream_chat_turn(p, BUILTIN_PERSONAS, b, harness=kw.pop("harness", "generic"),
                                 session_id=session_id, round_budget=kw.pop("round_budget", 30),
                                 stay_in_chat=True, **kw)
    frames: list[tuple[str, dict]] = []
    helper_asks: list[dict] = []
    async for chunk in gen:
        for event, data in frames_of([chunk]):
            frames.append((event, data))
            if event == "approval":
                raise AssertionError(f"a card nobody expected: {data}")
            if event == "helper" and isinstance(data.get("ask"), dict):
                # A helper parked on an ask of its own: noted and DECLINED (a
                # read-only reviewer holds nothing it could ask about — a
                # pin below says none came; answering keeps a broken build
                # from hanging the test instead of failing it).
                helper_asks.append(data["ask"])
                approvals.resolve(str(data["ask"].get("id") or ""), "deny")
    done = [d for e, d in frames if e == "done"]
    return {"done": done[0] if done else None, "frames": frames, "helper_asks": helper_asks}


async def post(p: Any, b: ChatBody, *, session_id: str = SESSION, **kw) -> dict:
    return await run_chat_turn(p, BUILTIN_PERSONAS, b, harness=kw.pop("harness", "generic"),
                               session_id=session_id, round_budget=kw.pop("round_budget", 30),
                               stay_in_chat=True, **kw)


def ledger(p: Any) -> list[ToolInvocation]:
    with session_scope(p.engine) as db:
        rows = list(db.exec(select(ToolInvocation).order_by(ToolInvocation.created_at)).all())
        for r in rows:
            db.expunge(r)
        return rows


def helper_sessions(p: Any) -> list[Session]:
    with session_scope(p.engine) as db:
        rows = list(db.exec(select(Session).where(Session.origin == H.HELPER_ORIGIN)).all())
        for r in rows:
            db.expunge(r)
        return rows


def write_then_end(w: SimpleNamespace, answer: str = "1. met: X is 2.\nNothing else needed changing.") -> None:
    """The planner writes app.py, ends; then answers the finish note (kept)."""

    def answer_note(rec: dict) -> str:
        note = next((str(m.content) for m in reversed(rec["messages"])
                     if getattr(m, "role", "") == "user" and str(m.content).startswith(F.NOTE_OPENING)), "")
        w.notes.append(note)
        return answer

    w.main = [
        [ToolCall(id="w1", name="write_file", arguments={"path": "app.py", "content": "X = 2\n"})],
        "I set X to 2 in app.py.",
        answer_note,
    ]


def helper_calls(w: SimpleNamespace) -> list[dict]:
    return [c for c in w.calls if not c["main"]]


# --------------------------------------------------------------------------- #
# 1. off is exactly the check it was
# --------------------------------------------------------------------------- #


async def test_with_the_setting_off_the_check_is_exactly_what_it_was(world, monkeypatch):
    reviewer(world.p)
    world.p.config.team_first = False
    got = capture_finish(monkeypatch)
    write_then_end(world)
    out = await stream(world.p, body(world.ws))
    done = out["done"]
    (check,) = got
    # No review call was made, nothing was handed to anyone, and the record
    # and the note are the check's own.
    assert check.review is None and not check._review_wanted
    assert not helper_calls(world) and not helper_sessions(world.p)
    assert "review" not in done["finish"]
    assert list(done["finish"]) == ["tests", "command", "removed", "kept", "points", "unmet", "after"]
    (note,) = world.notes
    assert "A review by your team" not in note
    assert not [r for r in ledger(world.p) if r.tool == "delegate"]


async def test_off_and_on_with_no_reviewer_make_the_same_note(tmp_path, monkeypatch):
    """The note with the setting OFF is byte for byte the note with it ON but
    no member holding the Review role (the feature does not apply)."""
    notes = {}
    for on in (False, True):
        p = build_platform(str(tmp_path / f"home-{on}"))
        ws = tmp_path / f"ws-{on}"
        ws.mkdir()
        (ws / "app.py").write_bytes(b"X = 1\n")
        w = wire(p, monkeypatch, ws)
        p.config.team_first = on
        # A member WITHOUT the Review role.
        reviewer(p, "coder", roles=("code",))
        write_then_end(w)
        out = await stream(p, body(ws))
        assert "review" not in out["done"]["finish"]
        assert not helper_calls(w)
        notes[on] = w.notes[0].replace(str(ws), "<ws>")
    assert notes[False] == notes[True]


# --------------------------------------------------------------------------- #
# 2 + 3 + 5. ON, end to end through the stream lane
# --------------------------------------------------------------------------- #


async def test_on_the_finish_hands_the_change_to_the_reviewer_who_only_reads(world, monkeypatch):
    reviewer(world.p)
    got = capture_finish(monkeypatch)
    write_then_end(world)
    world.reviewer = [
        # A reviewer that tries to change things: refused, nothing on disk moves.
        [ToolCall(id="r-w", name="write_file", arguments={"path": "app.py", "content": "HACKED\n"}),
         ToolCall(id="r-s", name="shell", arguments={"command": "echo pwned > pwned.txt"})],
        [ToolCall(id="r-r", name="read_file", arguments={"path": "app.py"})],
        VERDICT_BAD,
    ]
    out = await stream(world.p, body(world.ws))
    done, frames = out["done"], out["frames"]
    (check,) = got

    # -- the call: after the tests, asking for the ROLE, with a brief.
    rows = [r for r in ledger(world.p) if r.session_id == SESSION and r.tool == "delegate"]
    assert len(rows) == 1 and rows[0].called_as == F.REVIEW_CALLED_AS == "team review"
    args = json.loads(rows[0].args_json)
    assert args["role"] == "review" and args["piece"] == F.REVIEW_PIECE
    task = args["task"]
    assert task.startswith(F.REVIEW_BRIEF_LEAD)
    assert REQUEST in task and "I set X to 2 in app.py." in task
    assert "--- a/app.py" in task and "+++ b/app.py" in task
    assert "-X = 1" in task and "+X = 2" in task

    # -- the reviewer: routed to the member, on ITS model, read-only.
    (kid,) = helper_sessions(world.p)
    assert (kid.provider, kid.model) == LOCAL and kid.agent_name == "custom:rev"
    assert json.loads(kid.options_json)[H.REVIEW_ONLY_OPTION] is True
    assert kid.task.endswith(MT.review_task("review", "").strip())  # the verdict line was asked for
    rcalls = helper_calls(world)
    assert len(rcalls) == 3 and {c["provider"] for c in rcalls} == {LOCAL[0]}
    for c in rcalls:
        assert set(c["tools"]) <= set(H.REVIEW_TOOLS), c["tools"]
        assert "notebook" not in c["tools"]
    assert "read_file" in rcalls[0]["tools"]
    assert out["helper_asks"] == []  # it held nothing it could ask about
    kid_rows = {r.tool: r for r in ledger(world.p) if r.session_id == kid.id}
    # The registry's "not armed" refusal, in the ledger's words.
    for tool in ("write_file", "shell"):
        assert not kid_rows[tool].ok and "is not one of this agent's tools" in str(kid_rows[tool].output)
    assert kid_rows["read_file"].ok
    assert (world.ws / "app.py").read_text(encoding="utf-8") == "X = 2\n"
    assert not (world.ws / "pwned.txt").exists()

    # -- the note: its report, fenced, with the verdict and how to answer it.
    (note,) = world.notes
    section = note.split("## A review by your team\n", 1)[1]
    assert section.startswith("rev (on qwen3:8b) read this reply's change. Its verdict: changes needed.")
    assert "nothing tests it" in section and F.REVIEW_ASK in section
    assert note.index("## A review by your team") > note.index("## The request, point by point")

    # -- the record, on the done frame.
    assert done["finish"]["review"] == {"state": "done", "member": "rev", "model": "qwen3:8b",
                                        "verdict": "changes_needed"}
    assert F.saved_record(done["finish"])["review"] == done["finish"]["review"]
    # -- its row: helper frames with the role.
    hf = [d for e, d in frames if e == "helper" and d.get("id") == F.REVIEW_CALL_ID]
    assert hf and hf[-1]["state"] == "done" and hf[-1].get("role") == "review"
    assert hf[-1].get("agent") == "custom:rev"

    # -- 5. the harness's call, never the reply's own work.
    assert "delegate" not in done["tools_used"]
    assert chat_turn.ran_local_writer(world.p, done["tools_used"]) is False
    # A carry-on of this reply reads the review's row as the harness's, never
    # as a command or a hand-off that could have changed files.
    upto = F.ledger_mark(world.p.engine)
    assert upto > 0 and F.ran_unjournaled(world.p.engine, SESSION, 0, upto) is False
    assert check.may_have_written() is False
    assert F.helpers_that_changed(world.p.engine, SESSION, 0) == []


async def test_the_review_is_ledgered_after_the_test_calls_of_the_same_round(world, monkeypatch):
    """With a test command to run again, the round is [the test call, the
    review] — the tests first."""
    reviewer(world.p)
    monkeypatch.setitem(world.p.permissions._base, "shell", "allow")
    (world.ws / "test_mini.py").write_bytes(
        b"import unittest\nimport app\n\n\nclass T(unittest.TestCase):\n"
        b"    def test_x(self):\n        self.assertEqual(app.X, 2)\n")
    world.main = [
        [ToolCall(id="w1", name="write_file", arguments={"path": "app.py", "content": "X = 2\n"})],
        [ToolCall(id="s1", name="shell", arguments={"command": "python -m unittest -q test_mini"})],
        "Done: X is 2 and the tests pass.",
        "1. met: X is 2.\nNothing needed changing.",
    ]
    out = await stream(world.p, body(world.ws, REQUEST + " and run the tests"))
    harness_rows = [r for r in ledger(world.p) if r.session_id == SESSION and r.called_as in F.HARNESS_CALLED_AS]
    assert [r.called_as for r in harness_rows] == [F.CALLED_AS, F.REVIEW_CALLED_AS]
    assert out["done"]["finish"]["tests"] == "passed"
    assert out["done"]["finish"]["review"]["state"] == "done"
    assert out["done"]["finish"]["review"]["verdict"] == "approved"


# --------------------------------------------------------------------------- #
# 1. when it does not apply
# --------------------------------------------------------------------------- #


async def test_no_member_holds_review_no_call_no_record(world, monkeypatch):
    reviewer(world.p, "coder", roles=("code", "test"))
    write_then_end(world)
    out = await stream(world.p, body(world.ws))
    assert "review" not in out["done"]["finish"]
    assert not helper_calls(world)
    assert not [r for r in ledger(world.p) if r.tool == "delegate"]


async def test_a_folder_that_turned_it_off_gets_no_review(world, monkeypatch):
    reviewer(world.p)
    hd = world.ws / ".ironjarvis" / "harness"
    hd.mkdir(parents=True)
    (hd / "finish.toml").write_text("review = false\n", encoding="utf-8")
    assert F.parse_config("review = false\n") == F.FinishConfig(review=False)
    assert F.FinishConfig(review=False).mark() == "finish-off:review"
    assert F.DEFAULT_CONFIG.mark() == ""
    write_then_end(world)
    out = await stream(world.p, body(world.ws))
    assert out["done"]["finish"]["tests"] in ("none", "not_run")
    assert "review" not in out["done"]["finish"]
    assert not helper_calls(world)


async def test_low_trust_hands_nothing_out_and_says_why(world, monkeypatch):
    reviewer(world.p)
    write_then_end(world)
    out = await stream(world.p, body(world.ws), trust="low", trust_reason="a test")
    assert not helper_calls(world)
    assert out["done"]["finish"]["review"] == {
        "state": "not_run", "member": "", "model": "", "verdict": "",
        "why": "this reply is in low trust, so it was not handed to a reviewer",
    }
    assert "A review by your team" not in world.notes[0]


async def test_plan_mode_or_no_change_is_no_finish_check_at_all(world, monkeypatch):
    reviewer(world.p)
    world.main = ["I only looked."]
    out = await stream(world.p, body(world.ws, "what is X?"))
    assert "finish" not in out["done"]
    assert not helper_calls(world)


# --------------------------------------------------------------------------- #
# 4. the verdict
# --------------------------------------------------------------------------- #


async def test_a_verdict_quoted_mid_report_is_no_verdict(world, monkeypatch):
    reviewer(world.p)
    write_then_end(world)
    world.reviewer = ["The README says `VERDICT: approved` somewhere.\nOverall it reads fine to me."]
    out = await stream(world.p, body(world.ws))
    rv = out["done"]["finish"]["review"]
    assert rv["state"] == "done" and rv["verdict"] == ""
    assert "It gave no verdict." in world.notes[0]


async def _check_with_child(p: Any, ws: Path, *, trust: "str | None") -> tuple[F.FinishCheck, str]:
    child = await Orchestrator(p).create_session("review it", AgentType.BUILDER, provider=LOCAL[0],
                                                 model=LOCAL[1], trust=trust, trust_reason="read a trap")
    check = F.FinishCheck(engine=p.engine, home=p.config.home, event_bus=p.event_bus, session_id=SESSION,
                          folder=ws, mark=0, request=REQUEST, platform=p, team_review=True)
    check._review_pending = True
    return check, child.id


async def test_a_low_trust_reviewers_verdict_is_never_counted(world):
    call = ToolCall(id=F.REVIEW_CALL_ID, name="delegate", arguments={})
    for trust, want in ((None, "approved"), ("low", "")):
        check, child = await _check_with_child(world.p, world.ws, trust=trust)
        assert check.owns(call) and check.owns_review(call)
        result = ToolResult(ok=True, output="[custom:rev agent_run_id=r session=s]\nAll good.\nVERDICT: approved",
                            data={"child_session_id": child, "member": "rev", "provider": LOCAL[0],
                                  "model": LOCAL[1], "role": "review"})
        await check.take(call, result, "", refused=False)
        assert check.review["verdict"] == want, trust
        assert not check.owns(call)  # settled: a later model call with this id is the model's own
        text = await check._review_text(None)
        assert ("Its verdict: approved." in text) == (want == "approved")
        if trust == "low":
            assert "Its verdict is not counted" in text


async def test_a_report_the_runtime_wrote_carries_no_verdict(world):
    from iron_jarvis.agents.runtime import NO_SUMMARY_LEAD

    check, child = await _check_with_child(world.p, world.ws, trust=None)
    call = ToolCall(id=F.REVIEW_CALL_ID, name="delegate", arguments={})
    out = f"{NO_SUMMARY_LEAD} read_file said:\nVERDICT: approved"
    await check.take(call, ToolResult(ok=True, output=out, data={"child_session_id": child}), "", refused=False)
    assert check.review["state"] == "done" and check.review["verdict"] == ""


# --------------------------------------------------------------------------- #
# 6. refused, stopped, failed
# --------------------------------------------------------------------------- #


async def test_the_only_reviewer_is_cloud_under_local_only_not_run_in_the_routers_words(world, monkeypatch):
    reviewer(world.p, "codex", who=CLOUD)
    write_then_end(world)
    out = await stream(world.p, body(world.ws, who=LOCAL, local_only=True))
    rv = out["done"]["finish"]["review"]
    assert rv["state"] == "not_run"
    assert rv["why"] == "not started: no member on this network holds reviewing"
    assert "A review by your team" not in world.notes[0]
    assert not helper_sessions(world.p)
    assert [c["provider"] for c in world.calls] == [LOCAL[0]] * len(world.calls)


async def test_stop_during_the_review_is_a_stop(world, monkeypatch):
    from iron_jarvis.core.turns import TURNS

    reviewer(world.p)
    got = capture_finish(monkeypatch)
    write_then_end(world)
    started = asyncio.Event()

    async def hang(rec):
        started.set()
        await asyncio.sleep(60)
        return "never"

    world.reviewer = [hang]
    b = body(world.ws, turn_id="tr-stop")
    task = asyncio.create_task(stream(world.p, b))
    await asyncio.wait_for(started.wait(), 20)
    assert TURNS.stop("tr-stop")
    out = await asyncio.wait_for(task, 30)
    assert out["done"] is None  # a stopped turn ends as a stopped turn does
    (check,) = got
    assert check.review["state"] == "stopped"
    (kid,) = helper_sessions(world.p)
    assert kid.status is SessionStatus.CANCELLED
    ends = [d for e, d in out["frames"] if e == "helper" and d.get("ended")]
    assert ends and ends[-1]["state"] == "stopped"


async def test_take_says_every_way_a_review_can_end(world):
    call = ToolCall(id=F.REVIEW_CALL_ID, name="delegate", arguments={})

    def fresh() -> F.FinishCheck:
        c = F.FinishCheck(engine=world.p.engine, home=world.p.config.home, event_bus=None, session_id=SESSION,
                          folder=world.ws, mark=0, request=REQUEST, platform=world.p, team_review=True)
        c._review_pending = True
        return c

    c = fresh()
    await c.take(call, None, H.STOPPED_RESULT, refused=False)
    assert c.review["state"] == "stopped"
    c = fresh()
    await c.take(call, ToolResult(ok=False, error="not armed: delegate is not one of this turn's tools"),
                 "", refused=True)
    assert c.review["state"] == "not_run" and c.review["why"].startswith("not armed")
    c = fresh()
    await c.take(call, ToolResult(ok=False, error="No team member can take reviewing now: …",
                                  data={"role": "review", "reason": "not started: no team member can take "
                                        "reviewing now", "state": "not_started"}), "", refused=False)
    assert c.review == {"state": "not_run", "member": "", "model": "", "verdict": "",
                        "why": "not started: no team member can take reviewing now"}
    c = fresh()
    await c.take(call, ToolResult(ok=False, error="the reviewer gave up",
                                  data={"child_session_id": "s1", "state": "failed", "member": "rev",
                                        "provider": LOCAL[0], "model": LOCAL[1]}), "", refused=False)
    assert c.review == {"state": "failed", "member": "rev", "model": "qwen3:8b", "verdict": "",
                        "why": "the reviewer gave up"}
    assert await c._review_text(None) == ""  # no report: nothing for the note
    c = fresh()
    c.refused_call(call, "repeated")
    assert c.review["state"] == "not_run"
    # Handed out and never settled: said on the record.
    c = fresh()
    rec = await c.record()
    assert rec["review"] == {"state": "not_run", "member": "", "model": "", "verdict": "",
                             "why": "the review did not run"}


# --------------------------------------------------------------------------- #
# the brief's caps
# --------------------------------------------------------------------------- #


def _patched(before: "bytes | None") -> tuple:
    if before is None:
        return ("file_delete", {}, {}, None)
    return ("files_patch", {}, {}, {"existed": True, "data": base64.b64encode(before).decode()})


def test_the_change_is_bounded_and_says_what_it_left_out(tmp_path):
    folder = tmp_path / "f"
    folder.mkdir()
    files = F.TurnFiles()

    def add(rel: str, before: "bytes | None", after: "bytes | None") -> None:
        t = folder / rel
        t.parent.mkdir(parents=True, exist_ok=True)
        if after is not None:
            t.write_bytes(after)
        files.changed.append(t)
        files.first[F._key(t)] = _patched(before)

    # One huge file: its diff is cut at the per-file cap, and says so.
    big = "".join(f"line {i}\n" for i in range(20_000)).encode()
    add("big.py", b"", big)
    # A binary file and a too-big one: named, never diffed.
    add("logo.png", b"\x89PNG\x00\x01", b"\x89PNG\x00\x02")
    add("huge.txt", b"a", b"x" * (F.MAX_SPEC_FILE_BYTES + 10))
    # Many ordinary files: the whole is capped; the rest are named.
    for i in range(12):
        add(f"pkg/m{i}.py", ("v = 0\n" * 200).encode(), ("v = 1\n" * 200).encode())
    text = F.change_diff(folder, tmp_path / "home", files, ["out/made_by_a_command.log"])
    assert "[the rest of the diff of `big.py` is left out: read the file yourself]" in text
    assert "- `logo.png`: binary file changed (read it yourself)" in text
    assert "- `huge.txt`: too big to show (read it yourself)" in text
    assert "Not shown, to keep this short (read them yourself): " in text
    assert "`out/made_by_a_command.log`" in text and "Changed by a command" in text
    # The diff part stays within its cap (plus the cut notes).
    diff_part = text.split("Also changed:", 1)[0]
    assert len(diff_part) <= F.MAX_REVIEW_DIFF_CHARS + 400
    big_part = text.split("+++ b/big.py", 1)[1].split("--- a/", 1)[0]
    assert len(big_part) <= F.MAX_REVIEW_FILE_DIFF_CHARS + 200
    # A created file diffs from nothing, a deleted one to nothing.
    files2 = F.TurnFiles()
    made = folder / "new.py"
    made.write_bytes(b"print('hi')\n")
    files2.changed.append(made)
    files2.first[F._key(made)] = _patched(None)
    gone = folder / "old.py"
    files2.changed.append(gone)
    files2.first[F._key(gone)] = _patched(b"x = 1\n")
    text2 = F.change_diff(folder, tmp_path / "home", files2)
    assert "--- /dev/null\n+++ b/new.py" in text2 and "+print('hi')" in text2
    assert "--- a/old.py\n+++ /dev/null" in text2 and "-x = 1" in text2


def test_the_brief_masks_secrets_and_cuts_long_parts():
    secret = "sk-ant-api03-" + "A" * 40
    brief = F.review_brief("x" * 10_000 + f" key {secret}", f"I used {secret}", f"+API_KEY={secret}\n")
    assert secret not in brief
    assert brief.startswith(F.REVIEW_BRIEF_LEAD)
    assert F.CHANGE_BEGINS in brief and F.CHANGE_ENDS in brief
    assert len(brief.split("## What the coder")[0]) < F.MAX_REVIEW_REQUEST_CHARS + 1_000


# --------------------------------------------------------------------------- #
# 7. the record on the line (whitelisted)
# --------------------------------------------------------------------------- #


def test_the_saved_line_keeps_the_review_and_drops_anything_else():
    raw = {"tests": "none", "command": "", "removed": 0, "kept": 0, "points": 1, "unmet": 0, "after": "",
           "review": {"state": "done", "member": "rev", "model": "qwen3:8b", "verdict": "changes_needed",
                      "why": "never for a finished review", "report": "never on the line"}}
    assert F.saved_record(raw)["review"] == {"state": "done", "member": "rev", "model": "qwen3:8b",
                                             "verdict": "changes_needed"}
    raw["review"] = {"state": "not_run", "member": "", "model": "", "verdict": "great", "why": "x" * 500}
    kept = F.saved_record(raw)["review"]
    assert kept["verdict"] == "" and len(kept["why"]) <= F.MAX_REVIEW_WHY_CHARS
    for bad in ({"state": "lovely"}, "done", None, [], {"member": "rev"}):
        raw["review"] = bad
        assert "review" not in F.saved_record(raw)
    raw["review"] = {"state": "done", "member": 3, "model": None, "verdict": "approved"}
    assert F.saved_record(raw)["review"] == {"state": "done", "member": "", "model": "",
                                             "verdict": "approved"}
    assert F.RECORD_KEYS[-1] == "review"


# --------------------------------------------------------------------------- #
# 10. the POST lane mirror
# --------------------------------------------------------------------------- #


async def test_the_post_lane_hands_the_review_read_only_too(world, monkeypatch):
    reviewer(world.p)
    # The POST lane arms no ask-tier tool: a user who set delegate to allow.
    monkeypatch.setitem(world.p.permissions._base, "delegate", "allow")
    write_then_end(world)
    world.reviewer = [
        [ToolCall(id="r-w", name="write_file", arguments={"path": "app.py", "content": "HACKED\n"})],
        VERDICT_BAD,
    ]
    out = await post(world.p, body(world.ws))
    assert out["finish"]["review"] == {"state": "done", "member": "rev", "model": "qwen3:8b",
                                       "verdict": "changes_needed"}
    (kid,) = helper_sessions(world.p)
    assert json.loads(kid.options_json)[H.REVIEW_ONLY_OPTION] is True
    for c in helper_calls(world):
        assert set(c["tools"]) <= set(H.REVIEW_TOOLS), c["tools"]
    assert (world.ws / "app.py").read_text(encoding="utf-8") == "X = 2\n"
    assert "delegate" not in out["tools_used"]
    assert "A review by your team" in world.notes[0]


async def test_the_post_lane_without_delegate_in_its_gate_makes_no_review(world, monkeypatch):
    """The POST lane arms an ask-tier tool only when the user allowed it:
    with ``delegate`` at its default tier it is not in the turn's gate, so no
    review is handed out — never a call the registry then refuses."""
    reviewer(world.p)
    write_then_end(world)
    out = await post(world.p, body(world.ws))
    assert "review" not in out["finish"]
    assert not [r for r in ledger(world.p) if r.tool == "delegate"]
    assert not helper_calls(world)


# --------------------------------------------------------------------------- #
# helper_definition: the guarantee, at its source
# --------------------------------------------------------------------------- #


def test_a_review_only_parent_cuts_any_definition_to_the_reading_tools(world):
    from iron_jarvis.agents.types import get_agent_definition

    builder = get_agent_definition(AgentType.BUILDER)
    turn = ("read_file", "write_file", "edit_file", "glob", "grep", "list_files", "shell", "process_start",
            "web_fetch", "page_open", "delegate", "spawn_agent", "notebook")
    parent = H.HelperParent(session_id=SESSION, folder="", provider="p", model="m", tools=turn,
                            review_only=True)
    for custom in (False, True):
        out = H.helper_definition(parent, builder, custom=custom)
        assert out.tools == ["read_file", "glob", "grep", "list_files"]
    # With NO turn tools a reviewer is cut to nothing — never left with its own.
    empty = H.HelperParent(session_id=SESSION, folder="", provider="p", model="m", review_only=True)
    assert H.helper_definition(empty, builder).tools == []
    # An ordinary helper is unchanged by the field.
    plain = H.HelperParent(session_id=SESSION, folder="", provider="p", model="m", tools=turn)
    assert "write_file" in H.helper_definition(plain, builder).tools
    assert plain.options() == {"build_session": "team-review"}
    assert parent.options() == {"build_session": "team-review", H.REVIEW_ONLY_OPTION: True}


# --------------------------------------------------------------------------- #
# 11. the benchmark counts the review as member work
# --------------------------------------------------------------------------- #


async def test_a_team_first_bench_run_records_the_review_as_member_work(tmp_path, monkeypatch):
    from iron_jarvis.harness_bench import team as T
    from iron_jarvis.harness_bench import team_report as TRP
    from iron_jarvis.harness_bench.bench import run_bench
    from iron_jarvis.harness_bench.catalog import load_tasks
    from tests._fakes import bench_team_v1396 as W

    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    platform = build_platform(str(tmp_path / "root"))
    w = W.wire(platform, monkeypatch)
    # A planner that does NOT hand pieces out (the live night's): only the
    # finish check's review reaches the team.
    w.team_words = "words no prompt carries"
    path = tmp_path / "team.toml"
    path.write_text(W.team_toml().replace("[planner]\n", "[planner]\nteam_first = true\n", 1), encoding="utf-8")
    spec = T.load_team_file(path)
    res = await run_bench(runner=T.JarvisTeamRunner(platform, team=spec, round_budget=12),
                          home=platform.config.home, tasks=load_tasks(), names=[W.TASK_B], seed=0)
    (rec,) = res.records
    assert rec["ok_run"] is True, rec.get("error")
    # The planner wrote the fix itself; the finish check handed the change to
    # the reviewer member (codex).
    fin = rec["extra"]["finish"]
    assert fin["review"] == {"state": "done", "member": "codex", "model": "gpt-6-astra", "verdict": "approved"}
    (row,) = [h for h in rec["helpers"] if h["id"] == F.REVIEW_CALL_ID]
    assert (row["member"], row["role"], row["state"]) == ("codex", "review", "done")
    assert rec["parts"]["codex"]["model_calls"] >= 1
    assert rec["parts"]["codex"]["pieces"].get("review") == 1
    assert TRP.handed_to_a_member(rec) is True
    # Review (v1.401.0): team work, but NOT the planner's — the report never
    # says "the planner handed" for the review Iron Jarvis handed out itself.
    assert TRP.planner_handed(rec) is False and TRP.finish_reviewed(rec) is True
    assert TRP.handed_line(res.records) == (
        "The planner handed at least one piece to a team member in 0 of 1 run. Iron Jarvis's own "
        "finish check handed the change to the team's reviewer in 1 of 1 run.")
    assert platform.config.team_first is False  # put back after the run
