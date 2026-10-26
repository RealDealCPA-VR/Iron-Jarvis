"""v1.394.0 (the long-jobs plan's Phase 5): THE LIVE PROOF, on a scratch app.

The plan's "done when": a coding session on Claude hands a READING task to a
LOCAL member and a CODING task to a CODEX member. The Timeline shows three
models, and each helper's model calls went ONLY to its own provider —
checked against the router's own record (``provider.routed`` per session)
and the adapters'. Under Local only the planner never sees the cloud member,
and a request for it is refused in words.

Everything real but the models: the app factory, the Build route and its
durable turn, the stream lane in harness mode, the dialect translation of
Claude Code's ``Agent`` tool, both helper doors, the REAL router with its
hold and account pin, and Iron-Proxy's real client against the fake
Iron-Proxy (never the user's: every test has its own data dir). The three
"models" are adapter doubles registered on the real provider manager that
lease their account exactly as the real CLI adapters do.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from iron_jarvis.core.events import EventType
from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import helpers as H
from iron_jarvis.harness import sessions as S
from iron_jarvis.iron_proxy import accounts
from iron_jarvis.iron_proxy import pin as P
from iron_jarvis.providers import hold as HOLD
from iron_jarvis.providers import router as router_mod
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMResponse, ToolCall
from tests.test_build_accounts_v1368 import enable, isolate, make_fake, picks
from tests.test_build_sessions_v1367 import frames_of, messages_of, open_turn

LOCAL = ("ollama", "qwen3:8b")
CLAUDE = ("claude-cli", "claude-sonnet-4-5")


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


class World:
    """What the three model doubles saw, and what the main turn should do."""

    def __init__(self, main: list) -> None:
        self.main = list(main)
        self.calls: list[dict[str, Any]] = []


class ModelDouble(LLMAdapter):
    """One provider's model. A subscription CLI (claude-cli / codex-cli)
    leases its account through ``iron_proxy.accounts.lease`` off the loop —
    the real adapters' own call, so the account pin is really exercised. The
    main turn (its system prompt carries the IJ harness block) plays
    ``World.main``; a helper's run answers its task."""

    def __init__(self, provider: str, model: str | None, world: World, endpoint: str = "") -> None:
        self.provider = provider
        self.model = model or "subscription"
        self.world = world
        self._endpoint = endpoint

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
        self.world.calls.append({
            "provider": self.provider, "model": self.model, "main": main,
            "account": lease.profile_id if lease is not None else None,
            "pin": P.current(), "hold": (hold.provider, hold.model) if hold else None,
            "system": system or "", "task": _first_user(messages),
        })
        if main:
            move = self.world.main.pop(0) if (self.world.main and tools) else "Done: the team finished."
        else:
            move = f"{self.provider} did: {_first_user(messages)[:60]}"
        usage = {"input_tokens": 10, "output_tokens": 4}
        if isinstance(move, str):
            resp = LLMResponse(text=move, usage=usage)
        else:
            resp = LLMResponse(text="", tool_calls=list(move), finish_reason="tool_use", usage=usage)
        if lease is not None:
            await accounts.report_success_async(lease, usage, 5, self.model)
        if resp.text:
            yield {"type": "text", "text": resp.text}
        yield {"type": "final", "response": resp}


def wire(app, monkeypatch, world: World) -> None:
    platform = app.state.platform
    mgr = platform.providers
    platform.config.ollama_base_url = "http://127.0.0.1:11434"
    mgr.configure_local(ollama_base_url="http://127.0.0.1:11434", ollama_model=LOCAL[1],
                        custom_base_url="", custom_model="")
    mgr.register("claude-cli", lambda model=None: ModelDouble("claude-cli", model, world))
    mgr.register("codex-cli", lambda model=None: ModelDouble("codex-cli", model, world))
    mgr.register("ollama", lambda model=None: ModelDouble("ollama", model or LOCAL[1], world,
                                                          endpoint="http://127.0.0.1:11434"))
    real = mgr.available
    names = ("claude-cli", "codex-cli", "ollama")
    monkeypatch.setattr(mgr, "available", lambda name: True if name in names else real(name))
    monkeypatch.setattr(mgr, "inherited_from", lambda name: None)


def team(app, *, codex_account: str = "x-plus") -> None:
    reg = app.state.platform.agents_registry
    reg.register("local-reader", "You read and summarize.", [], description="reads and summarizes",
                 provider=LOCAL[0], model=LOCAL[1], roles=["read"])
    reg.register("codex-coder", "You write code.", [], description="writes code",
                 provider="codex-cli", model="subscription", roles=["code", "test"],
                 account=codex_account)


def agent_call(cid: str, who: str, prompt: str) -> ToolCall:
    return ToolCall(id=cid, name="Agent", arguments={"description": who, "prompt": prompt,
                                                     "subagent_type": f"custom:{who}"})


def routed_by_session(app) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for e in app.state.platform.event_bus.history:
        if e.type == EventType.PROVIDER_ROUTED:
            out.setdefault(str(e.session_id or ""), set()).add(str(e.payload.get("resolved_provider")))
    return out


async def run_turn(app, sid: str, text: str, turn_id: str) -> list[tuple[str, dict]]:
    status, raw = await open_turn(app, sid, {"text": text, "turn_id": turn_id}, timeout=60)
    assert status == 200, raw
    frames = frames_of(raw)
    assert frames and frames[-1][0] == "done", frames[-3:]
    return frames


# --------------------------------------------------------------------------- #
# Claude hands reading to a local member and coding to a Codex member
# --------------------------------------------------------------------------- #


async def test_a_claude_session_hands_reading_to_a_local_member_and_coding_to_a_codex_member(
        app, fake, work, monkeypatch):
    await enable(app)
    world = World([
        [agent_call("h-read", "local-reader", "Read app.py and summarize it."),
         agent_call("h-code", "codex-coder", "Write util.py with a helper.")],
        "Done: read by the local member, written by Codex.",
    ])
    wire(app, monkeypatch, world)
    team(app)
    sid = S.create(app.state.platform.engine, str(work), "",
                   {"provider": CLAUDE[0], "model": CLAUDE[1], "approval_mode": "yolo"}).id
    fake.queries.clear()
    frames = await run_turn(app, sid, "Use the team.", "team-1")

    # The planner saw the team, each member with its model, place and cost.
    main = [c for c in world.calls if c["main"]]
    assert main and all(c["provider"] == "claude-cli" for c in main)
    system = main[0]["system"]
    assert "# Your model team" in system
    assert "- custom:local-reader — reads and summarizes [qwen3:8b, this PC, free; roles: read]" in system
    assert "custom:codex-coder — writes code [Codex, cloud, your plan" in system

    # Each helper ran on ITS OWN model, held there, on its own account.
    reader = [c for c in world.calls if not c["main"] and c["provider"] == "ollama"]
    coder = [c for c in world.calls if not c["main"] and c["provider"] == "codex-cli"]
    assert reader and all(c["hold"] == LOCAL and c["pin"] is None for c in reader)
    assert coder and all(c["hold"] == ("codex-cli", "subscription") for c in coder)
    # The Codex helper leased ITS member's account, never the session's Claude pin.
    assert {c["account"] for c in coder} == {"x-plus"}
    assert {c["account"] for c in main} == {"c-work"}
    claude_picks = [q for q in picks(fake) if q["provider"] == "anthropic"]
    codex_picks = [q for q in picks(fake) if q["provider"] == "openai"]
    assert claude_picks and all(q.get("profileId") == "c-work" for q in claude_picks)
    assert codex_picks and all(q.get("profileId") == "x-plus" and q.get("pinned") == "1" for q in codex_picks)
    # Nothing but the main reply went to Claude.
    assert not [c for c in world.calls if not c["main"] and c["provider"] == "claude-cli"]

    # The router's own record: each helper session reached ONLY its member's
    # provider; the session's reply only Claude.
    by = routed_by_session(app)
    helpers = {d["id"]: d for e, d in frames if e == "helper" and d.get("session")}
    read_sid, code_sid = helpers["h-read"]["session"], helpers["h-code"]["session"]
    assert by[read_sid] == {"ollama"}
    assert by[code_sid] == {"codex-cli"}
    others = set().union(*(v for k, v in by.items() if k not in (read_sid, code_sid)))
    assert others == {"claude-cli"}

    # THE TIMELINE: three models — the reply's calls on Claude, each helper's
    # row on its own model — from the saved line (a reload draws the same).
    line = [m for m in messages_of(app, sid) if m.get("role") == "assistant"][-1]
    assert {(c["provider"]) for c in line["calls"]} == {"claude-cli"}
    rows = {h["id"]: h for h in line["helpers"]}
    assert (rows["h-read"]["provider"], rows["h-read"]["model"]) == LOCAL
    assert rows["h-code"]["provider"] == "codex-cli"
    assert rows["h-read"]["agent"] == "custom:local-reader" and rows["h-code"]["agent"] == "custom:codex-coder"
    assert rows["h-read"]["state"] == rows["h-code"]["state"] == "done"
    models = {c["provider"] for c in line["calls"]} | {h["provider"] for h in line["helpers"]}
    assert models == {"claude-cli", "ollama", "codex-cli"}
    # The model the user talks to stayed the one they picked.
    done = dict(frames)["done"]
    assert done["route"]["provider"] == "claude-cli"
    assert done["route"]["account"]["id"] == "c-work"

    # The dashboard draws THIS recorded reply (its Timeline and helper rows):
    # dashboard/__tests__/fixtures/model-team-timeline-v1394.json. Regenerate
    # with IJ_REGEN_TEAM_TIMELINE=1; otherwise a fresh recording must carry
    # the fixture's shape and its three models.
    question = [m for m in messages_of(app, sid) if m.get("role") == "user"][-1]
    _check_fixture(question, line, str(work))


TEAM_FIXTURE = (Path(__file__).resolve().parents[1] / "dashboard" / "__tests__" / "fixtures"
                / "model-team-timeline-v1394.json")


def _scrub(value: Any, folder: str) -> Any:
    """The recording without this run's temporary folder (a path is not a fact
    the dashboard draws)."""
    text = json.dumps(value)
    for form in {folder, folder.replace("\\", "/"), json.dumps(folder)[1:-1]}:
        text = text.replace(form, "<work>")
    return json.loads(text)


def _models_of(line: dict) -> set[str]:
    return {c["provider"] for c in line.get("calls") or []} | {
        h.get("provider") for h in line.get("helpers") or []}


def _check_fixture(question: dict, line: dict, folder: str) -> None:
    import os

    if os.environ.get("IJ_REGEN_TEAM_TIMELINE") == "1":
        TEAM_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        TEAM_FIXTURE.write_text(json.dumps({
            "_doc": "v1.394.0: a REAL recorded coding reply on Claude that handed reading to a "
                    "local team member and coding to a Codex member (tests/test_model_team_live_"
                    "v1394.py). Regenerate with IJ_REGEN_TEAM_TIMELINE=1.",
            "question": _scrub(question, folder),
            "line": _scrub(line, folder),
        }, indent=1) + "\n", encoding="utf-8", newline="\n")
    fx = json.loads(TEAM_FIXTURE.read_text(encoding="utf-8"))
    assert sorted(fx["line"]) == sorted(k for k in line)
    assert sorted({k for h in fx["line"]["helpers"] for k in h}) == sorted({k for h in line["helpers"] for k in h})
    assert _models_of(fx["line"]) == _models_of(line) == {"claude-cli", "ollama", "codex-cli"}


async def test_a_codex_member_with_no_account_takes_its_vendors_first_usable_account_pinned(
        app, fake, work, monkeypatch):
    """v1.394.0 review (moved on purpose): a coding session never runs "first
    free, rotating" — a member with no account of its own (and no group) takes
    its vendor's first usable account, PINNED; never the session's Claude pin.
    (tests/test_model_team_review_v1394.py pins the whole rule.)"""
    await enable(app)
    world = World([[agent_call("h-code", "codex-coder", "Write it.")], "Done."])
    wire(app, monkeypatch, world)
    team(app, codex_account="")
    sid = S.create(app.state.platform.engine, str(work), "",
                   {"provider": CLAUDE[0], "model": CLAUDE[1], "approval_mode": "yolo"}).id
    fake.queries.clear()
    await run_turn(app, sid, "go", "team-2")
    coder = [c for c in world.calls if not c["main"] and c["provider"] == "codex-cli"]
    assert coder and all(c["pin"] is not None and c["pin"].ij_provider == "codex-cli" for c in coder)
    codex_picks = [q for q in picks(fake) if q["provider"] == "openai"]
    # The first usable Codex account, pinned — never Claude's.
    assert codex_picks and all(
        q.get("profileId") == "x-plus" and q.get("pinned") == "1" for q in codex_picks)


# --------------------------------------------------------------------------- #
# Local only: the planner never sees the cloud member; asking for it is refused
# --------------------------------------------------------------------------- #


async def test_under_local_only_the_planner_never_sees_the_cloud_member_and_a_request_is_refused(
        app, fake, work, monkeypatch):
    await enable(app)
    # A local model speaks its own dialect (qwen3 -> Qwen Code's tools, which
    # keep Jarvis's own ``delegate``).
    world = World([
        [ToolCall(id="h-code", name="delegate",
                  arguments={"agent_type": "custom:codex-coder", "task": "Write util.py."}),
         ToolCall(id="h-read", name="delegate",
                  arguments={"agent_type": "custom:local-reader", "task": "Read app.py."})],
        "Done locally.",
    ])
    wire(app, monkeypatch, world)
    team(app)
    sid = S.create(app.state.platform.engine, str(work), "",
                   {"provider": LOCAL[0], "model": LOCAL[1], "approval_mode": "yolo",
                    "local_only": True}).id
    frames = await run_turn(app, sid, "Use the team, locally.", "team-lo")
    main = [c for c in world.calls if c["main"]]
    assert main
    for c in main:
        assert "custom:local-reader" in c["system"]
        assert "codex-coder" not in c["system"]
    ends = {d["id"]: d for e, d in frames if e == "helper" and d.get("ended")}
    assert ends["h-code"]["state"] == "failed"
    assert "this work is Local only" in ends["h-code"]["result"]
    assert ends["h-read"]["state"] == "done"
    # Nothing reached Codex (or any cloud model) at all.
    assert {c["provider"] for c in world.calls} == {"ollama"}
    assert not [q for q in picks(fake) if q["provider"] == "openai"]
    assert set().union(*routed_by_session(app).values()) == {"ollama"}


def test_the_helper_frames_name_the_member(app):
    """The row a helper draws carries its member's name (the agent) and, once
    its run says so, its model (``HelperCall`` reads the run's ``meta``)."""
    call = H.HelperCall(call_id="c", agent="custom:local-reader", task="t")
    call._take({"event": "meta", "data": {"provider": "ollama", "model": "qwen3:8b"}})
    frame = call.frame()
    assert (frame["agent"], frame["provider"], frame["model"]) == ("custom:local-reader", "ollama", "qwen3:8b")
