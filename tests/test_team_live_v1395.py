"""v1.395.0 (the long-jobs plan's Phase 6): THE PROOF — a scripted day.

Everything real but the models, as Phase 5's proof (``test_model_team_live_
v1394``): the app factory, the Build route and its durable turn, the stream
lane in harness mode, the dialect translation of Codex's ``spawn_agent`` and
Qwen's ``delegate``, both helper doors, the TEAM ROUTER, the real router with
its hold and account pin, and Iron-Proxy's real client against the fake
Iron-Proxy (never the user's: every test has its own data dir). The "models"
are adapter doubles registered on the real provider manager that lease their
account exactly as the real CLI adapters do — and report the vendor's usage
headers back, as the real Claude adapter does, so an account's percent moves
the way it moves in life: after a call.

The day (decisions 7 and 8):

1. The planner (a coding session on Codex) asks for ``role:code`` while the
   local box is busy: Claude Code takes it on its lowest-usage account. That
   call reports 83% of the 5-hour window — and still FINISHES. The next code
   piece goes to Claude Code's OTHER account; nothing new starts on the busy
   one.
2. The local box is free: the next code piece goes LOCAL (a good record);
   then, the box busy again, to the other Claude account.
3. The local member fails coding twice: the third piece is not given to it
   first.
4. Every cloud account near its limit: the local member takes it as the last
   resort and passes — and is first again.
5. A Local only session never reaches a cloud member.

Every helper row on the saved reply line carries its pick's reason (the
Timeline's own record), and the router's record says where each piece ran.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from iron_jarvis.agents import team_record as TR
from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import sessions as S
from iron_jarvis.iron_proxy import accounts
from iron_jarvis.iron_proxy import pin as P
from iron_jarvis.providers import hold as HOLD
from iron_jarvis.providers import router as router_mod
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMResponse, ProviderError, ToolCall
from tests._fakes.team_world_v1395 import FakeSampler, iso
from tests.test_build_accounts_v1368 import enable, isolate, make_fake, picks
from tests.test_build_sessions_v1367 import frames_of, messages_of, open_turn

LOCAL = ("ollama", "qwen3:8b")
CODEX = ("codex-cli", "subscription")


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    yield from isolate(tmp_path, monkeypatch)


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    monkeypatch.setattr(router_mod.random, "uniform", lambda a, b: 0.0)


@pytest.fixture
def fake(tmp_path):
    f = make_fake(tmp_path)
    yield f
    f.stop()


@pytest.fixture
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    return folder


def _first_user(messages) -> str:
    for m in messages or ():
        if getattr(m, "role", "") == "user":
            return str(getattr(m, "content", "") or "")
    return ""


class Day:
    """What the doubles saw, and what the planner does next."""

    def __init__(self) -> None:
        self.main: list[Any] = []
        self.calls: list[dict[str, Any]] = []
        #: providers whose helper calls fail (a crashed piece)
        self.fail: set[str] = set()
        #: provider -> the ratelimit headers its NEXT helper call reports
        self.report: dict[str, dict[str, str]] = {}
        #: provider -> the moves its next helper calls make (a list of tool
        #: calls, a text, or a callable that may await); text when empty
        self.helper_moves: dict[str, list[Any]] = {}


class Double(LLMAdapter):
    """One provider's model. A subscription CLI leases its account through
    ``iron_proxy.accounts.lease`` off the loop (the real adapters' own call)
    and reports success with the vendor's usage headers, as the real Claude
    adapter does. The planner's turn (its system prompt carries the IJ harness
    block) plays ``Day.main``; a helper answers its task."""

    def __init__(self, provider: str, model: str | None, day: Day) -> None:
        self.provider = provider
        self.model = model or "subscription"
        self.day = day

    def capabilities(self):
        return {"tool_use": True, "vision": True}

    async def complete(self, *, system, messages, tools, **kw):
        final = None
        async for frame in self.stream(system=system, messages=messages, tools=tools, **kw):
            if frame["type"] == "final":
                final = frame["response"]
        return final

    async def stream(self, *, system, messages, tools, **kw):
        lease = None
        if self.provider in ("claude-cli", "codex-cli"):
            lease = await asyncio.to_thread(accounts.lease, self.provider)
        main = "# IJ harness" in (system or "")
        hold = HOLD.current_hold()
        self.day.calls.append({
            "provider": self.provider, "main": main,
            "account": lease.profile_id if lease is not None else None,
            "hold": (hold.provider, hold.model) if hold else None, "pin": P.current(),
            "task": _first_user(messages),
        })
        headers = None
        if main:
            move: Any = self.day.main.pop(0) if (self.day.main and tools) else "Done for now."
        else:
            if self.provider in self.day.fail:
                raise ProviderError(f"{self.provider}: the model stopped answering", transient=False)
            headers = self.day.report.pop(self.provider, None)
            queue = self.day.helper_moves.get(self.provider) or []
            move = queue.pop(0) if (queue and tools) else f"{self.provider} did: {_first_user(messages)[:40]}"
        if callable(move):
            move = move()
            if inspect.isawaitable(move):
                move = await move
        usage = {"input_tokens": 10, "output_tokens": 4}
        resp = (LLMResponse(text=move, usage=usage) if isinstance(move, str)
                else LLMResponse(text="", tool_calls=list(move), finish_reason="tool_use", usage=usage))
        if lease is not None:
            await accounts.report_success_async(lease, usage, 5, self.model, headers=headers)
        if resp.text:
            yield {"type": "text", "text": resp.text}
        yield {"type": "final", "response": resp}


def wire(app, monkeypatch, day: Day) -> FakeSampler:
    platform = app.state.platform
    mgr = platform.providers
    platform.config.ollama_base_url = "http://127.0.0.1:11434"
    mgr.configure_local(ollama_base_url="http://127.0.0.1:11434", ollama_model=LOCAL[1],
                        custom_base_url="", custom_model="")
    for name in ("claude-cli", "codex-cli", "ollama"):
        mgr.register(name, lambda model=None, n=name: Double(n, model or (LOCAL[1] if n == "ollama" else None), day))
    real = mgr.available
    names = ("claude-cli", "codex-cli", "ollama")
    monkeypatch.setattr(mgr, "available", lambda name: True if name in names else real(name))
    monkeypatch.setattr(mgr, "inherited_from", lambda name: None)
    sampler = FakeSampler()
    platform.fleet_sampler = sampler
    return sampler


def team(app) -> None:
    reg = app.state.platform.agents_registry
    reg.register("glm", "You write code.", [], description="local coder",
                 provider=LOCAL[0], model=LOCAL[1], roles=["read", "code"])
    reg.register("claude-code", "You write code.", [], description="writes code on the Claude plan",
                 provider="claude-cli", model="subscription", roles=["code", "review"])
    # Yesterday: glm wrote five pieces of code that all passed their checks
    # (v1.395.0 review: on the model it runs on — the record is that model's).
    for i in range(5):
        TR.note_run(app.state.platform.engine, member="glm", role="code", session_id=f"yday-{i}",
                    scope="build:yesterday", finished=True, provider=LOCAL[0], model=LOCAL[1])
        TR.credit_check(app.state.platform.engine, [f"yday-{i}"], TR.CHECK_PASSED)


def spawn(cid: str, task: str, role: str = "code") -> list[ToolCall]:
    """Codex's own helper tool, asking for a ROLE (its schema has no role
    argument: ``agent_type: "role:<id>"``)."""
    return [ToolCall(id=cid, name="spawn_agent", arguments={"message": task, "agent_type": f"role:{role}"})]


async def run_turn(app, sid: str, text: str, turn_id: str) -> list[tuple[str, dict]]:
    status, raw = await open_turn(app, sid, {"text": text, "turn_id": turn_id}, timeout=60)
    assert status == 200, raw
    frames = frames_of(raw)
    assert frames and frames[-1][0] == "done", frames[-3:]
    return frames


def saved_helpers(app, sid: str) -> dict[str, dict]:
    line = [m for m in messages_of(app, sid) if m.get("role") == "assistant"][-1]
    return {h["id"]: h for h in line.get("helpers") or []}


def helper_calls(day: Day, start: int = 0) -> list[dict]:
    return [c for c in day.calls[start:] if not c["main"]]


FIXTURE = (Path(__file__).resolve().parents[1] / "dashboard" / "__tests__" / "fixtures"
           / "team-router-timeline-v1395.json")


def _scrub(value: Any, folder: str) -> Any:
    text = json.dumps(value)
    for form in {folder, folder.replace("\\", "/"), json.dumps(folder)[1:-1]}:
        text = text.replace(form, "<work>")
    return json.loads(text)


def _check_fixture(question: dict, line: dict, folder: str) -> None:
    """Regenerate with IJ_REGEN_TEAM_ROUTER=1; otherwise a fresh recording must
    carry the fixture's shape and each helper's reason."""
    if os.environ.get("IJ_REGEN_TEAM_ROUTER") == "1":
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps({
            "_doc": "v1.395.0: a REAL recorded coding reply whose planner asked for role:code twice; "
                    "the team router's reasons ride each helper (tests/test_team_live_v1395.py). "
                    "Regenerate with IJ_REGEN_TEAM_ROUTER=1.",
            "question": _scrub(question, folder),
            "line": _scrub(line, folder),
        }, indent=1) + "\n", encoding="utf-8", newline="\n")
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert sorted(fx["line"]) == sorted(line)
    assert sorted({k for h in fx["line"]["helpers"] for k in h}) == sorted({k for h in line["helpers"] for k in h})
    assert [(h["role"], h["reason"]) for h in fx["line"]["helpers"]] == [
        (h["role"], h["reason"]) for h in line["helpers"]]


async def test_a_scripted_day_local_first_limits_watched(app, fake, work, monkeypatch):
    await enable(app)
    day = Day()
    sampler = wire(app, monkeypatch, day)
    team(app)
    svc = app.state.platform.iron_proxy
    sid = S.create(app.state.platform.engine, str(work), "",
                   {"provider": CODEX[0], "model": CODEX[1], "approval_mode": "yolo"}).id

    async def refresh() -> None:
        # Iron-Proxy's watch loop re-reads the accounts every 30 s; here, now.
        await asyncio.to_thread(svc.refresh_accounts)

    # (v1.395.0 review) The spare account has numbers of its own, as a real
    # Claude account does after any call: 45% of its 5-hour window — above
    # c-work's 38%, so the morning's first piece still goes to c-work. (With
    # NO numbers, the router would let it take only a few new pieces before
    # its numbers arrive: ``model_team.UNKNOWN_BURST_MAX``.)
    fake.states["c-spare"]["windows"] = [{"kind": "5h", "usedPercent": 45, "windowMins": 300,
                                          "resetsAt": iso(9000)}]
    await refresh()

    # ------------------------------------------------------------------ 1
    # Morning. The local box is busy. Claude Code takes the first code
    # piece on its lowest-usage account (c-work, 38% known); that very call
    # reports 83% of the 5-hour window — and still finishes.
    sampler.waiting["ollama"] = 3
    day.report["claude-cli"] = {
        "anthropic-ratelimit-unified-5h-utilization": "0.83",
        "anthropic-ratelimit-unified-5h-reset": str(int(time.time()) + 5400),
    }

    async def a_carries_on():
        # Piece A is still RUNNING after its first call put c-work at 83%:
        # Iron-Proxy's numbers are re-read now, and this second call of the
        # same run is on c-work too — work already running is never stopped.
        await refresh()
        return "Wrote util.py."

    day.helper_moves["claude-cli"] = [
        [ToolCall(id="a-read", name="read_file", arguments={"path": "app.py"})],
        a_carries_on,
    ]

    async def round_two():
        await refresh()
        return spawn("p-b", "Piece B: write cli.py.")

    day.main = [spawn("p-a", "Piece A: write util.py."), round_two, "Done: A and B written."]
    start = len(day.calls)
    await run_turn(app, sid, "Write util.py and cli.py.", "day-1")
    rows = saved_helpers(app, sid)
    assert rows["p-a"]["state"] == "done"  # the work in flight finished
    assert rows["p-a"]["role"] == "code"
    assert rows["p-a"]["reason"] == "Claude Code (Work Max): the local box is busy"
    assert rows["p-b"]["reason"] == ("Claude Code (Spare): the local box is busy; "
                                     "skipped Claude Code (Work Max): 83% of its 5-hour window")
    calls = helper_calls(day, start)
    a = [c for c in calls if c["task"].startswith("Piece A")]
    b = [c for c in calls if c["task"].startswith("Piece B")]
    # Two calls of piece A's run, BOTH on c-work: the second came after the
    # first had put the account at 83% — the work in flight finished there.
    assert len(a) == 2 and all(c["account"] == "c-work" and c["provider"] == "claude-cli" for c in a)
    assert b and all(c["account"] == "c-spare" for c in b)
    # Iron-Proxy really learned the 83% from that call.
    assert max(w["usedPercent"] for w in fake.states["c-work"]["windows"]) == 83
    # The dashboard draws THIS recorded reply's helper rows and Timeline:
    # dashboard/__tests__/fixtures/team-router-timeline-v1395.json.
    line = [m for m in messages_of(app, sid) if m.get("role") == "assistant"][-1]
    question = [m for m in messages_of(app, sid) if m.get("role") == "user"][-1]
    _check_fixture(question, line, str(work))

    # ------------------------------------------------------------------ 2
    # The local box is free: the next code piece goes LOCAL (its record from
    # yesterday is good); with the box busy again, to the OTHER account.
    sampler.waiting.clear()

    def box_busy():
        sampler.waiting["ollama"] = 2
        return spawn("p-d", "Piece D: write io.py.")

    day.main = [spawn("p-c", "Piece C: write parse.py."), box_busy, "Done: C and D."]
    start = len(day.calls)
    await run_turn(app, sid, "Write parse.py and io.py.", "day-2")
    rows = saved_helpers(app, sid)
    assert rows["p-c"]["reason"] == "local: good record for coding"
    assert rows["p-d"]["reason"] == ("Claude Code (Spare): the local box is busy; "
                                     "skipped Claude Code (Work Max): 83% of its 5-hour window")
    calls = helper_calls(day, start)
    assert [c["provider"] for c in calls if c["task"].startswith("Piece C")] == ["ollama"]
    assert {c["hold"] for c in calls if c["task"].startswith("Piece C")} == {LOCAL}

    # Nothing NEW started on the busy account after it passed 80%: every
    # helper call since piece A leased another account.
    after_a = next(i for i, c in enumerate(day.calls) if c is a[-1]) + 1
    assert all(c["account"] != "c-work" for c in helper_calls(day, after_a))
    # ...and Iron-Proxy was asked for c-work only by piece A's two calls (its
    # pinned pick each time).
    assert [(q["profileId"], q.get("pinned")) for q in picks(fake) if q.get("profileId") == "c-work"] == [
        ("c-work", "1")] * len(a)

    # ------------------------------------------------------------------ 3
    # (v1.395.0 review) The local box stops answering for one piece: that is
    # the PROVIDER failing, not glm at coding — its record is untouched and it
    # is still first. Then glm writes two pieces the REVIEWER says need
    # changes: they count against glm (decision 7: the reviewer, not "it
    # finished"), and the next code piece is not given to glm first.
    sampler.waiting.clear()
    day.fail.add("ollama")

    def box_back():
        day.fail.discard("ollama")
        return spawn("p-e", "Piece E: write a.py.")

    day.helper_moves["claude-cli"] = ["a.py and b.py miss the empty-input case.\nVERDICT: changes needed"]
    day.main = [spawn("p-x", "Piece X: write z.py."), box_back, spawn("p-f", "Piece F: write b.py."),
                spawn("p-r", "Piece R: review a.py and b.py.", role="review"),
                spawn("p-g", "Piece G: write c.py."), "Done: E, F and G, reviewed."]
    start = len(day.calls)
    await run_turn(app, sid, "Write a.py, b.py and c.py, and have them reviewed.", "day-3")
    rows = saved_helpers(app, sid)
    assert rows["p-x"]["state"] == "failed"
    assert rows["p-x"]["reason"] == rows["p-e"]["reason"] == rows["p-f"]["reason"] == (
        "local: good record for coding")
    assert rows["p-e"]["state"] == rows["p-f"]["state"] == "done"
    assert rows["p-r"]["reason"] == ("Claude Code (Spare): no local member holds reviewing; "
                                     "skipped Claude Code (Work Max): 83% of its 5-hour window")
    assert rows["p-g"]["reason"] == ("Claude Code (Spare): the local member failed coding 2 times in a row; "
                                     "skipped Claude Code (Work Max): 83% of its 5-hour window")
    assert rows["p-g"]["state"] == "done"
    rec = TR.role_record(app.state.platform.engine, "glm", "code", LOCAL)
    assert (rec.streak, rec.status) == (2, TR.NOT_GOOD)

    # ------------------------------------------------------------------ 4
    # Every cloud account near its limit: the work goes LOCAL as the last
    # resort; the local member passes again — and is first again.
    day.fail.clear()
    fake.states["c-spare"]["windows"] = [{"kind": "5h", "usedPercent": 91, "windowMins": 300,
                                          "resetsAt": iso(2400)}]
    await refresh()

    async def spare_resets():
        fake.states["c-spare"]["windows"] = [{"kind": "5h", "usedPercent": 5, "windowMins": 300,
                                              "resetsAt": iso(18000)}]
        await refresh()
        return spawn("p-i", "Piece I: write e.py.")

    day.main = [spawn("p-h", "Piece H: write d.py."), spare_resets, "Done: H and I."]
    await run_turn(app, sid, "Write d.py and e.py.", "day-4")
    rows = saved_helpers(app, sid)
    assert rows["p-h"]["reason"] == ("local: every cloud account that can take coding is near its limit; "
                                     "its record is failed the last 2 in a row (6 of 8 passed)")
    assert rows["p-h"]["state"] == "done"
    assert rows["p-i"]["reason"] == "local: good record for coding"

    # ------------------------------------------------------------------ 5
    # Local only: a session on the local model never reaches a cloud member,
    # even with the local box busy.
    sampler.waiting["ollama"] = 4
    lo = S.create(app.state.platform.engine, str(work), "",
                  {"provider": LOCAL[0], "model": LOCAL[1], "approval_mode": "yolo", "local_only": True}).id
    day.main = [[ToolCall(id="p-l", name="delegate", arguments={"role": "code", "task": "Piece L: write f.py."})],
                "Done locally."]
    picks_before = len(picks(fake))
    start = len(day.calls)
    await run_turn(app, lo, "Write f.py, locally.", "day-5")
    rows = saved_helpers(app, lo)
    assert rows["p-l"]["reason"] == "local: nothing else can take coding now; its box is busy"
    assert {c["provider"] for c in day.calls[start:]} == {"ollama"}
    assert not [q for q in picks(fake)[picks_before:] if q["provider"] in ("anthropic", "openai")]

    # The record says where every piece of the day ran, in order.
    from sqlalchemy import literal_column
    from sqlmodel import select

    from iron_jarvis.core.db import session_scope

    with session_scope(app.state.platform.engine) as db:
        rows = db.exec(select(TR.TeamRoleRun).order_by(literal_column("teamrolerun.rowid")))
        today = [(r.member, r.role, r.finished, r.review, r.provider, r.account)
                 for r in rows if r.scope != "build:yesterday"]
    # (v1.395.0 review) Piece X (the box not answering) wrote NO row; the
    # reviewer's "changes needed" judged E and F — only them, the pieces of
    # its own reply, never the morning's A–D; each row keeps the model it ran
    # on and the account it was pinned to.
    cc, g = ("claude-code", "claude-cli"), ("glm", "ollama")
    assert today == [
        (cc[0], "code", True, "", cc[1], "c-work"), (cc[0], "code", True, "", cc[1], "c-spare"),
        (g[0], "code", True, "", g[1], ""), (cc[0], "code", True, "", cc[1], "c-spare"),
        (g[0], "code", True, TR.REVIEW_BAD, g[1], ""), (g[0], "code", True, TR.REVIEW_BAD, g[1], ""),
        (cc[0], "review", True, "", cc[1], "c-spare"), (cc[0], "code", True, "", cc[1], "c-spare"),
        (g[0], "code", True, "", g[1], ""), (g[0], "code", True, "", g[1], ""),
        (g[0], "code", True, "", g[1], ""),
    ]
