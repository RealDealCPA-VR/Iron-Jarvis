"""v1.403.0 — THE CODING-SESSION TEAM (daemon side).

The user's decision (2026-10-28): "Lead plans and checks but never edits
files, Workers do the code, Reviewer checks every change. If no worker can
take a piece, the lead may do it and must say so." A planner TOLD to hand
work out handed out nothing in 108 benchmark runs, so the roles are
ENFORCED (``harness/team``), not suggested.

What these pins hold, through the REAL lanes (the stream lane and the POST
lane in harness mode), the real registry, the real ``delegate`` door, the
real team router, the real finish check and the real model router — only
the models are scripted adapters on the real provider manager:

1. THE LEAD IS LOCKED: the tools the lead's model RECEIVES hold no tool that
   changes a file or runs something (both lanes), its brief names its team,
   and a write it invents anyway is refused "not armed" with nothing on disk.
2. PIECES GO TO THE SESSION'S WORKERS: a role piece is routed among the
   session's workers only; a worker runs on ITS model with the worker rules
   and the coding tools; a name off the session's team is refused.
3. NO WORKER COULD TAKE IT: the lead is unlocked for the rest of the reply,
   the refusal says so, the record lists it; a worker that FAILED unlocks
   nothing.
4. THE REVIEWER: runs without "Hand work to my team first", and once more
   after a fix — never three times; the finish check's tests are the
   workers' own command.
5. The record ``team`` rides the done frame / POST answer LAST.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Session, ToolInvocation
from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.chat_stream import stream_chat_turn
from iron_jarvis.daemon.chat_turn import run_chat_turn
from iron_jarvis.daemon.routes.chat import _approvals
from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
from iron_jarvis.harness import finish as F
from iron_jarvis.harness import helpers as H
from iron_jarvis.harness import team as T
from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
from iron_jarvis.platform import build_platform
from iron_jarvis.providers import router as router_mod
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMResponse, ToolCall

LEAD = ("google", "gemini-3.8-flash")
LOCAL = ("ollama", "qwen3:8b")
CLOUD = ("codex-cli", "gpt-6-astra")
PROVIDERS = (LEAD[0], LOCAL[0], CLOUD[0])
SESSION = "build:team-session"
REQUEST = "set X to 2 in app.py"
#: Every real tool that changes a file or runs something (the lead holds none).
CHANGERS = frozenset({
    "write_file", "edit_file", "multi_edit", "apply_patch", "notebook_edit",
    "shell", "process_start", "process_write", "process_stop", "page_click", "page_type",
})


def _first_user(messages: Any) -> str:
    for m in messages or ():
        if getattr(m, "role", "") == "user":
            return str(getattr(m, "content", "") or "")
    return ""


class Scripted(LLMAdapter):
    """One provider's model. The coding session's turn (its system prompt
    carries the IJ harness block) plays ``world.main``; a helper on the CLOUD
    provider plays ``world.reviewer``; any other helper ``world.worker``."""

    def __init__(self, provider: str, model: "str | None", world: SimpleNamespace) -> None:
        self.provider = provider
        self.model = model or {LEAD[0]: LEAD[1], LOCAL[0]: LOCAL[1], CLOUD[0]: CLOUD[1]}[provider]
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
               "tools": [t["name"] for t in tools or []], "specs": list(tools or []),
               "system": system or "", "messages": list(messages or []), "task": _first_user(messages)}
        w.calls.append(rec)
        if main:
            move: Any = w.main.pop(0) if (w.main and tools) else "Done."
        elif self.provider == CLOUD[0]:
            move = w.reviewer.pop(0) if w.reviewer else "Looks fine.\nVERDICT: approved"
        else:
            move = w.worker.pop(0) if w.worker else "I did the piece."
        if callable(move):
            move = move(rec)
            if inspect.isawaitable(move):
                move = await move
        if isinstance(move, BaseException):
            raise move
        usage = {"input_tokens": 5, "output_tokens": 2}
        resp = (LLMResponse(text=move, usage=usage) if isinstance(move, str)
                else LLMResponse(text="", tool_calls=list(move), finish_reason="tool_use", usage=usage))
        if resp.text:
            yield {"type": "text", "text": resp.text}
        yield {"type": "final", "response": resp}


def wire(p: Any, monkeypatch: Any, ws: Path) -> SimpleNamespace:
    w = SimpleNamespace(p=p, calls=[], main=[], worker=[], reviewer=[], ws=ws, notes=[], down=set())
    p.config.ollama_base_url = "http://127.0.0.1:11434"
    p.providers.configure_local(ollama_base_url="http://127.0.0.1:11434", ollama_model=LOCAL[1],
                                custom_base_url="", custom_model="")
    for name in PROVIDERS:
        p.providers.register(name, lambda model=None, n=name: Scripted(n, model, w))
    real = p.providers.available
    monkeypatch.setattr(p.providers, "available",
                        lambda name: (name not in w.down) if name in PROVIDERS else real(name))
    monkeypatch.setattr(p.providers, "inherited_from", lambda name: None)
    monkeypatch.setattr(router_mod.random, "uniform", lambda a, b: 0.0)
    # The setting is OFF: a session's own team needs no "Hand work to my team
    # first" (pinned below).
    p.config.team_first = False
    return w


@pytest.fixture
def world(tmp_path, monkeypatch):
    p = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "app.py").write_bytes(b"X = 1\n")
    return wire(p, monkeypatch, ws)


def members(p: Any, *, reviewer: bool = True) -> None:
    """A worker on the LOCAL model and a reviewer on the CLOUD one."""
    p.agents_registry.register("qwen", "You are qwen.", [], description="qwen codes",
                               provider=LOCAL[0], model=LOCAL[1], roles=["code", "test", "read"])
    if reviewer:
        p.agents_registry.register("rev", "You are rev.", [], description="rev reviews",
                                   provider=CLOUD[0], model=CLOUD[1], roles=["review"])


TEAM = T.SessionTeam(workers=("qwen",), reviewer="rev")


def body(ws: Path, text: str = REQUEST, **kw) -> ChatBody:
    return ChatBody(messages=[ChatMessageBody(role="user", content=text)], provider=LEAD[0], model=LEAD[1],
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


async def stream(p: Any, b: ChatBody, *, team: "T.SessionTeam | None" = TEAM, **kw) -> dict:
    """The stream lane as a Build session's turn — with the session's team
    armed exactly as ``routes/build.start_build_turn`` arms it."""
    approvals = _approvals(SimpleNamespace(platform=p))
    token = T.set_team(team)
    try:
        gen = await stream_chat_turn(p, BUILTIN_PERSONAS, b, harness=kw.pop("harness", "generic"),
                                     session_id=kw.pop("session_id", SESSION),
                                     round_budget=kw.pop("round_budget", 40), stay_in_chat=True, **kw)
        frames: list[tuple[str, dict]] = []
        async for chunk in gen:
            for event, data in frames_of([chunk]):
                frames.append((event, data))
                if event == "approval":
                    raise AssertionError(f"a card nobody expected: {data}")
                if event == "helper" and isinstance(data.get("ask"), dict):
                    approvals.resolve(str(data["ask"].get("id") or ""), "deny")
    finally:
        T.reset_team(token)
    done = [d for e, d in frames if e == "done"]
    return {"done": done[0] if done else None, "frames": frames}


async def post(p: Any, b: ChatBody, *, team: "T.SessionTeam | None" = TEAM, **kw) -> dict:
    token = T.set_team(team)
    try:
        return await run_chat_turn(p, BUILTIN_PERSONAS, b, harness=kw.pop("harness", "generic"),
                                   session_id=SESSION, round_budget=kw.pop("round_budget", 40),
                                   stay_in_chat=True, **kw)
    finally:
        T.reset_team(token)


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


def lead_calls(w: SimpleNamespace) -> list[dict]:
    return [c for c in w.calls if c["main"]]


def worker_calls(w: SimpleNamespace) -> list[dict]:
    return [c for c in w.calls if not c["main"] and c["provider"] == LOCAL[0]]


def reviewer_calls(w: SimpleNamespace) -> list[dict]:
    return [c for c in w.calls if not c["main"] and c["provider"] == CLOUD[0]]


def piece(cid: str, task: str, role: str = "code") -> list[ToolCall]:
    return [ToolCall(id=cid, name="delegate", arguments={"role": role, "task": task, "piece": task[:40]})]


def answer_note(w: SimpleNamespace, answer: str = "Nothing else needed changing.") -> Any:
    def _move(rec: dict) -> str:
        note = next((str(m.content) for m in reversed(rec["messages"])
                     if getattr(m, "role", "") == "user" and str(m.content).startswith(F.NOTE_OPENING)), "")
        w.notes.append(note)
        return answer

    return _move


def app_text(ws: Path) -> str:
    """app.py as text (write_file writes the OS's line ends)."""
    return (ws / "app.py").read_bytes().decode("utf-8").replace("\r\n", "\n")


def write_x(value: int) -> list[ToolCall]:
    return [ToolCall(id=f"wx{value}", name="write_file", arguments={"path": "app.py", "content": f"X = {value}\n"})]


# --------------------------------------------------------------------------- #
# 1. the lead is locked
# --------------------------------------------------------------------------- #


async def test_the_lead_receives_no_tool_that_changes_a_file_or_runs_something_stream(world):
    members(world.p)
    world.main = ["I read the project."]
    out = await stream(world.p, body(world.ws, "what does app.py do?"))
    (first, *_rest) = lead_calls(world)
    got = set(first["tools"])
    assert not got & CHANGERS, sorted(got & CHANGERS)
    assert {"read_file", "grep", "glob", "list_files", "plan_update", "delegate", "spawn_agent",
            "list_agents"} <= got
    # What the lead is shown is exactly the ONE rule's answer over the turn's tools.
    assert T.lead_tools(sorted(got), world.p.registry) == sorted(got)
    # Its brief names the team and its job (BEFORE the planner: in the system prompt).
    assert T.TEAM_BRIEF_HEADER in first["system"] and "custom:qwen" in first["system"]
    assert "custom:rev" in first["system"] and "You do not edit files or run commands yourself" in first["system"]
    # The model team's list is not ALSO shown (one source).
    assert "# Your model team" not in first["system"]
    # The helper tools lead with the session's workers, in their own words.
    spec = next(s for s in first["specs"] if s["name"] == "delegate")
    assert spec["description"].startswith("You lead this session's team: hand each piece to a worker")
    assert "custom:qwen" in spec["description"]
    # The record, LAST.
    done = out["done"]
    assert list(done)[-1] == "team"
    assert done["team"] == {"lead": "gemini-3.8-flash", "workers": ["qwen3:8b"], "reviewer": "gpt-6-astra",
                            "pieces": 0, "lead_did": []}


async def test_the_lead_receives_no_changing_tool_post_lane(world, monkeypatch):
    """The POST lane cards nothing, so it arms an ask-tier helper door only
    where the user's permissions allow it — and then its lead is locked the
    same way (lock-step)."""
    members(world.p)
    monkeypatch.setitem(world.p.permissions._base, "delegate", "allow")
    world.main = ["I read the project."]
    out = await post(world.p, body(world.ws, "what does app.py do?"))
    (first, *_rest) = lead_calls(world)
    assert not set(first["tools"]) & CHANGERS
    assert "read_file" in first["tools"] and "delegate" in first["tools"]
    assert T.TEAM_BRIEF_HEADER in first["system"]
    assert list(out)[-1] == "team" and out["team"]["lead"] == "gemini-3.8-flash"


async def test_a_lead_with_no_way_to_hand_work_out_is_never_locked(world):
    """The POST lane with the helper doors at their default (ask) tier arms
    no door — the turn runs as a solo turn would, never a lead that can do
    nothing at all."""
    members(world.p)
    world.main = ["I read the project."]
    out = await post(world.p, body(world.ws, "what does app.py do?"))
    (first, *_rest) = lead_calls(world)
    assert "delegate" not in first["tools"] and "write_file" in first["tools"]
    assert T.TEAM_BRIEF_HEADER not in first["system"]
    assert "team" not in out


async def test_a_write_the_lead_invents_is_refused_not_armed_and_nothing_changes(world):
    members(world.p)
    world.main = [write_x(9), "I could not write it."]
    out = await stream(world.p, body(world.ws))
    rows = [r for r in ledger(world.p) if r.session_id == SESSION and r.tool == "write_file"]
    assert len(rows) == 1 and not rows[0].ok
    assert "is not one of this agent's tools" in str(rows[0].output)
    assert app_text(world.ws) == "X = 1\n"
    assert out["done"]["team"]["lead_did"] == []


async def test_a_solo_session_is_exactly_as_before(world):
    """No team armed: the full coding set, no brief, no ``team`` key."""
    members(world.p)
    world.main = ["I read it."]
    out = await stream(world.p, body(world.ws, "what does app.py do?"), team=None)
    (first, *_rest) = lead_calls(world)
    assert {"write_file", "edit_file", "apply_patch"} <= set(first["tools"])
    assert T.TEAM_BRIEF_HEADER not in first["system"]
    assert "team" not in out["done"]


async def test_plan_mode_is_unchanged_by_a_team(world):
    members(world.p)
    world.main = ["A plan."]
    out = await stream(world.p, body(world.ws, "plan it"), plan_mode=True)
    (first, *_rest) = lead_calls(world)
    assert T.TEAM_BRIEF_HEADER not in first["system"]
    assert "team" not in out["done"]


# --------------------------------------------------------------------------- #
# 2. pieces go to the session's workers
# --------------------------------------------------------------------------- #


async def test_a_code_piece_goes_to_the_sessions_worker_on_its_own_model_with_the_worker_rules(world):
    # A member OFF the session's team that the whole model team's router
    # would ask first (same model, made first, first by name): never asked.
    world.p.agents_registry.register("aaa-coder", "You code.", [], provider=LOCAL[0], model=LOCAL[1],
                                     roles=["code", "test", "read"])
    members(world.p)
    world.main = [piece("p1", "Set X to 2 in app.py and say what you changed."),
                  "The worker set X to 2.", answer_note(world)]
    world.worker = [write_x(2), "Changed app.py: X = 2. No tests named."]
    out = await stream(world.p, body(world.ws))
    assert app_text(world.ws) == "X = 2\n"
    (kid, *rest) = helper_sessions(world.p)
    assert (kid.provider, kid.model) == LOCAL and kid.agent_name == "custom:qwen"
    wcalls = worker_calls(world)
    assert wcalls, "the worker never ran"
    # The worker rules ride its system prompt; it holds the coding tools.
    assert T.WORKER_RULES_HEADER in wcalls[0]["system"] and T.WORKER_RULES in wcalls[0]["system"]
    assert {"write_file", "edit_file", "shell"} <= set(wcalls[0]["tools"])
    # A worker starts no helper of its own.
    assert not set(wcalls[0]["tools"]) & {"delegate", "spawn_agent", "list_agents"}
    assert out["done"]["team"]["pieces"] == 1
    assert out["done"]["team"]["lead_did"] == []


async def test_a_member_off_the_sessions_team_is_refused_in_one_sentence(world):
    members(world.p)
    world.p.agents_registry.register("other", "You are other.", [], provider=LOCAL[0], model=LOCAL[1],
                                     roles=["code"])
    world.main = [
        [ToolCall(id="o1", name="delegate", arguments={"agent_type": "custom:other", "task": "set X"})],
        [ToolCall(id="o2", name="delegate", arguments={"agent_type": "builder", "task": "set X"})],
        "Neither could take it.",
    ]
    out = await stream(world.p, body(world.ws))
    assert not helper_sessions(world.p)
    tool_msgs = [m for m in lead_calls(world)[-1]["messages"] if getattr(m, "role", "") == "tool"]
    texts = [str(m.content) for m in tool_msgs]
    assert any("'custom:other' is not on this session's team" in t for t in texts), texts
    assert any("'builder' is not on this session's team" in t for t in texts), texts
    assert out["done"]["team"]["lead_did"] == []  # a refused NAME unlocks nothing


async def test_a_review_piece_goes_only_to_the_sessions_reviewer_who_only_reads(world):
    members(world.p)
    world.main = [piece("r1", "Review app.py.", role="review"), "The reviewer read it."]
    world.reviewer = [[ToolCall(id="rw", name="write_file", arguments={"path": "app.py", "content": "HACK\n"})],
                      "Fine.\nVERDICT: approved"]
    await stream(world.p, body(world.ws, "review app.py"))
    (kid,) = helper_sessions(world.p)
    assert kid.agent_name == "custom:rev" and json.loads(kid.options_json)[H.REVIEW_ONLY_OPTION] is True
    assert set(reviewer_calls(world)[0]["tools"]) <= set(H.REVIEW_TOOLS)
    assert app_text(world.ws) == "X = 1\n"


async def test_a_review_piece_with_no_reviewer_is_refused_and_unlocks_nothing(world):
    members(world.p, reviewer=False)
    world.main = [piece("r1", "Review app.py.", role="review"), "No reviewer."]
    out = await stream(world.p, body(world.ws, "review app.py"), team=T.SessionTeam(workers=("qwen",)))
    assert not helper_sessions(world.p)
    texts = [str(m.content) for m in lead_calls(world)[-1]["messages"] if getattr(m, "role", "") == "tool"]
    assert any(T.NO_REVIEWER_REFUSAL in t for t in texts)
    assert out["done"]["team"]["lead_did"] == [] and out["done"]["team"]["reviewer"] == ""
    assert not set(lead_calls(world)[-1]["tools"]) & CHANGERS


# --------------------------------------------------------------------------- #
# 3. no worker could take it -> the lead may do it, and says so
# --------------------------------------------------------------------------- #


async def test_no_worker_could_take_it_unlocks_the_lead_for_the_rest_of_the_reply(world):
    members(world.p)
    world.down.add(LOCAL[0])  # the worker's box is not answering
    world.main = [piece("p1", "Set X to 2 in app.py."), write_x(2),
                  "No worker could take it, so I set X to 2 myself.", answer_note(world)]
    out = await stream(world.p, body(world.ws))
    calls = lead_calls(world)
    # Round 1: locked. Round 2 (after the refusal): the coding tools.
    assert not set(calls[0]["tools"]) & CHANGERS
    assert {"write_file", "edit_file", "shell"} <= set(calls[1]["tools"])
    # The refusal the lead read says it may do the piece itself.
    tool_text = next(str(m.content) for m in calls[1]["messages"]
                     if getattr(m, "role", "") == "tool" and getattr(m, "tool_call_id", "") == "p1")
    assert tool_text.startswith("No worker could take this: ")
    assert "You may do this piece yourself now; say in your reply that you did it yourself and why." in tool_text
    assert "its box is not answering" in tool_text
    # The lead's write RAN, and the record says why it did the piece itself.
    assert app_text(world.ws) == "X = 2\n"
    did = out["done"]["team"]["lead_did"]
    assert len(did) == 1 and "skipped qwen (its box is not answering)" in did[0]["reason"]
    assert not did[0]["reason"].endswith("Do this piece yourself.")
    assert out["done"]["team"]["pieces"] == 0


async def test_no_worker_could_take_it_unlocks_the_lead_post_lane(world, monkeypatch):
    members(world.p)
    monkeypatch.setitem(world.p.permissions._base, "delegate", "allow")
    world.down.add(LOCAL[0])
    world.main = [piece("p1", "Set X to 2 in app.py."), write_x(2), "I did it myself.", "Nothing else."]
    out = await post(world.p, body(world.ws))
    calls = lead_calls(world)
    assert not set(calls[0]["tools"]) & CHANGERS
    assert "write_file" in calls[1]["tools"]
    assert app_text(world.ws) == "X = 2\n"
    assert len(out["team"]["lead_did"]) == 1


async def test_a_worker_that_failed_unlocks_nothing(world):
    members(world.p)
    world.main = [piece("p1", "Set X to 2 in app.py."), "The worker failed; I will hand it again later."]
    world.worker = [RuntimeError("the worker's model fell over")]
    out = await stream(world.p, body(world.ws))
    (kid,) = helper_sessions(world.p)
    assert kid.agent_name == "custom:qwen"
    calls = lead_calls(world)
    assert len(calls) >= 2 and not set(calls[1]["tools"]) & CHANGERS
    assert out["done"]["team"]["lead_did"] == []
    assert app_text(world.ws) == "X = 1\n"


# --------------------------------------------------------------------------- #
# 4. the reviewer: without team-first, twice after a fix, never three times
# --------------------------------------------------------------------------- #


async def test_the_reviewer_checks_the_change_without_team_first(world):
    members(world.p)
    assert world.p.config.team_first is False
    world.main = [piece("p1", "Set X to 2 in app.py."), "The worker set X to 2.", answer_note(world)]
    world.worker = [write_x(2), "Changed app.py."]
    world.reviewer = ["Good.\nVERDICT: approved"]
    out = await stream(world.p, body(world.ws))
    rows = [r for r in ledger(world.p) if r.session_id == SESSION and r.called_as == F.REVIEW_CALLED_AS]
    assert len(rows) == 1
    rv = out["done"]["finish"]["review"]
    assert rv["state"] == "done" and rv["member"] == "rev" and rv["verdict"] == "approved"
    assert rv["rounds"] == 1  # a team turn's record counts its reviews
    # The note tells the LEAD to hand fixes to a worker, never "with your tools".
    (note,) = world.notes
    assert F.TEAM_FIX_ASK in note and F.TEAM_REVIEW_ASK in note


async def test_a_second_review_after_the_fix_and_never_a_third(world):
    members(world.p)
    world.main = [
        piece("p1", "Set X to 2 in app.py."),
        "The worker set X to 2.",
        # answers note 1 (changes needed) by handing the fix out
        piece("p2", "Add a test for X in test_app.py."),
        "The worker added the test.",
        # answers note 2 (changes needed AGAIN) by handing another fix out
        piece("p3", "Name the test better."),
        "Done: renamed.",
    ]
    world.worker = [
        write_x(2), "Changed app.py.",
        [ToolCall(id="wt", name="write_file", arguments={"path": "test_app.py", "content": "def test_x():\n    pass\n"})],
        "Added test_app.py.",
        [ToolCall(id="wt2", name="write_file", arguments={"path": "test_app.py",
                                                         "content": "def test_x_is_two():\n    pass\n"})],
        "Renamed it.",
    ]
    world.reviewer = ["Nothing tests X.\nVERDICT: changes needed",
                      "The test name says nothing.\nVERDICT: changes needed",
                      "Never asked.\nVERDICT: approved"]
    out = await stream(world.p, body(world.ws))
    rows = [r for r in ledger(world.p) if r.session_id == SESSION and r.called_as == F.REVIEW_CALLED_AS]
    assert len(rows) == 2, "exactly two reviews"
    assert len(world.reviewer) == 1, "a third review was asked for"
    second_task = json.loads(rows[1].args_json)["task"]
    assert F.SECOND_REVIEW_ASK in second_task and "test_app.py" in second_task
    assert "+X = 2" not in second_task  # the second review reads the FIX, not the first change
    rv = out["done"]["finish"]["review"]
    assert rv["rounds"] == 2 and rv["verdict"] == "changes_needed"
    assert F.saved_record(out["done"]["finish"])["review"]["rounds"] == 2
    assert out["done"]["team"]["pieces"] == 3
    # The second note says it was the last review.
    lead_msgs = [str(m.content) for c in lead_calls(world) for m in c["messages"]
                 if getattr(m, "role", "") == "user" and "this was the last review" in str(m.content)]
    assert lead_msgs


async def test_an_approved_first_review_is_the_only_one(world):
    members(world.p)
    world.main = [piece("p1", "Set X to 2."), "Done.", piece("p2", "Tidy."), "Tidied."]
    world.worker = [write_x(2), "Changed.", write_x(3), "Tidied."]
    world.reviewer = ["Good.\nVERDICT: approved", "Never.\nVERDICT: changes needed"]
    out = await stream(world.p, body(world.ws))
    rows = [r for r in ledger(world.p) if r.session_id == SESSION and r.called_as == F.REVIEW_CALLED_AS]
    assert len(rows) == 1 and out["done"]["finish"]["review"]["rounds"] == 1


async def test_the_finish_checks_tests_are_the_workers_own_command(world, monkeypatch):
    members(world.p, reviewer=False)
    monkeypatch.setitem(world.p.permissions._base, "shell", "allow")
    (world.ws / "test_mini.py").write_bytes(
        b"import unittest\nimport app\n\n\nclass T(unittest.TestCase):\n"
        b"    def test_x(self):\n        self.assertEqual(app.X, 2)\n")
    world.main = [piece("p1", "Set X to 2 in app.py and run python -m unittest -q test_mini."),
                  "The worker set X and the tests pass.", answer_note(world)]
    world.worker = [write_x(2),
                    [ToolCall(id="ws1", name="shell", arguments={"command": "python -m unittest -q test_mini"})],
                    "Changed app.py; python -m unittest -q test_mini passed."]
    out = await stream(world.p, body(world.ws), team=T.SessionTeam(workers=("qwen",)))
    fin = out["done"]["finish"]
    assert fin["tests"] == "passed" and fin["command"] == "python -m unittest -q test_mini"
    rows = [r for r in ledger(world.p) if r.session_id == SESSION and r.called_as == F.CALLED_AS]
    assert len(rows) == 1 and rows[0].tool == "shell" and rows[0].ok
    # The lead still holds no command tool (the check's own call was let through).
    assert all(not set(c["tools"]) & CHANGERS for c in lead_calls(world))


# --------------------------------------------------------------------------- #
# the units
# --------------------------------------------------------------------------- #


def test_lead_tools_drops_every_changer_and_keeps_the_doors():
    names = ["read_file", "write_file", "edit_file", "multi_edit", "apply_patch", "notebook_edit",
             "glob", "grep", "list_files", "shell", "process_start", "process_read", "process_write",
             "process_stop", "plan_update", "view_image", "web_fetch", "web_search", "page_open",
             "page_click", "page_type", "page_read", "page_screenshot", "delegate", "spawn_agent",
             "list_agents", "skill_search", "skill_load", "ask_user", "mcp__pack__write"]
    kept = T.lead_tools(names)
    assert not set(kept) & CHANGERS
    assert "mcp__pack__write" not in kept  # an app tool not declared read-only
    assert {"read_file", "glob", "grep", "list_files", "process_read", "plan_update", "view_image",
            "web_fetch", "web_search", "page_open", "page_read", "page_screenshot", "delegate",
            "spawn_agent", "list_agents", "skill_search", "skill_load", "ask_user"} <= set(kept)
    # Every chat "file-changing" tool and every registry mutating tool is locked.
    from iron_jarvis.daemon.chat_turn import _FILE_CHANGING_TOOLS
    from iron_jarvis.tools.registry import _MUTATING_TOOLS

    assert not set(T.lead_tools(sorted(_FILE_CHANGING_TOOLS | _MUTATING_TOOLS))) - T.LEAD_KEEPS


def test_clean_record_and_the_team_record_shape():
    assert T.clean_record(None) is None and T.clean_record({"workers": []}) is None
    assert T.clean_record({"workers": ["a", "a", "b", "c"], "reviewer": "a"}) == {"workers": ["a", "b"],
                                                                                  "reviewer": ""}
    turn = T.TeamTurn(team=TEAM, lead_label="L", worker_labels=("W",), reviewer_label="R")
    assert list(turn.record()) == list(T.RECORD_KEYS)
    assert turn.no_worker("review", "x") == ""
    assert turn.take_unlock() is False and turn.lead_did == []  # a review no one takes unlocks nothing
    note = turn.no_worker("code", "No team member can take coding now: skipped qwen (busy). Do this piece yourself.")
    assert note == T.UNLOCK_NOTE.format(reason="No team member can take coding now: skipped qwen (busy).")
    assert turn.take_unlock() is True and turn.take_unlock() is False
    assert T.saved_record(turn.record())["lead_did"] == [
        {"reason": "No team member can take coding now: skipped qwen (busy)."}]


def test_attempts_never_run_as_the_team_lead():
    from iron_jarvis.harness import attempts as A

    token = T.set_team(TEAM)
    try:
        plan = SimpleNamespace(command_rule=None, summary=None)
        a = SimpleNamespace(pin=None, target=SimpleNamespace(provider="x", model="y"), git=None, folder="")
        ctx = A._arm(plan, a)
        assert ctx.run(T.current) is None
        assert T.current() is TEAM
    finally:
        T.reset_team(token)
