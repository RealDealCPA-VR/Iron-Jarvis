"""v1.407.2 — Ask first asks before an edit of Git's own files and of Iron
Jarvis's (or Claude Code's) own rules.

The user's decision (2026-10-29), found by v1.407.0's review: the file tools
are allow-tier, so under Ask first (the default) and Ask before every action
an agent could write ``.git/config`` (a ``core.fsmonitor`` then runs on Iron
Jarvis's own ``git status``), ``.git/hooks/*``, ``.ironjarvis/**`` (its own
permission rules and harness layer) or ``.claude/**`` with NO card. The same
path check the eased postures use (``core/auto_approve.sensitive_kind``) now
turns such an edit into a once-only card through ``tools/base.
spend_checks_for`` — the ONE helper both chat lanes and the agent runtime
call — except under "Don't ask" (yolo), which the user chose.

Every lane test drives the REAL registry, permission engine, approvals
registry and ledger, and the REAL ``write_file`` (the file on disk is the
proof): only the model is scripted (the v1.270.1 rule — a stub of ``invoke``
cannot see what the gate would have done).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import select

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.types import AgentDefinition
from iron_jarvis.core import auto_approve as A
from iron_jarvis.core.approvals import flush_interrupt_notes
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import AgentType, SessionStatus, ToolInvocation
from iron_jarvis.daemon.chat_stream import stream_chat_turn
from iron_jarvis.daemon.chat_turn import run_chat_turn
from iron_jarvis.daemon.routes import chat as chat_routes
from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.adapters.mock import MockLLMAdapter
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.base import ToolContext, guarded_edit_check, spend_checks_for

CLAUDE = ("anthropic", "claude-sonnet-4-5")

#: One path per sensitive family the user named, plus the rest of the table.
FAMILIES = [
    (".git/config", "git"),
    (".git/hooks/pre-commit", "git"),
    (".ironjarvis/harness/plugins.toml", "jarvis"),
    (".claude/settings.json", "claude"),
    ("CLAUDE.md", "rules"),
]
MORE = [
    (".gitconfig", "git"), ("lib/.gitmodules", "git"), (".gitattributes", "git"),
    (".vscode/tasks.json", "editor"), (".github/workflows/ci.yml", "ci"),
    ("sub/../.git/config", "git"), (".GIT/Config", "git"), (".git./config", "git"),
    (".git ./config", "git"), (".git::$DATA/config", "git"), ("GIT~1/config", "unknown"),
    ("deep/er/.ironjarvis/x.md", "jarvis"), ("pkg\\.claude\\commands\\x.md", "claude"),
]
#: The rules files the user named ("CLAUDE.md/AGENTS.md rules files") and
#: Iron Jarvis's own (``.ironjarvis.md``) — at the root and nested (Claude
#: Code and Codex read nested ones), any case.
RULES = [
    ("AGENTS.md", "rules"), (".ironjarvis.md", "rules"),
    ("claude.md", "rules"), ("CLAUDE.local.md", "rules"), ("AGENTS.local.md", "rules"),
    ("AGENTS.override.md", "rules"), ("packages/web/CLAUDE.md", "rules"), ("AGENTS.md.", "rules"),
]
ORDINARY = ["a.py", "src/git.py", "docs/.github/README.md", ".vscode/extensions.json",
            ".github/README.md", "ironjarvis/x.py", "gitconfig.txt", "docs/github.md",
            "claude.md.bak", "notes/agents.txt", "CLAUDE.md/notes.txt", "my-claude.md"]


# --------------------------------------------------------------------------- #
# the ONE path check
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path,kind", FAMILIES + MORE + RULES)
def test_every_family_is_guarded_with_its_own_plain_words(tmp_path, path, kind):
    assert A.sensitive_kind(path, tmp_path) == kind
    hit = A.guarded_edit("write_file", {"path": path, "content": "x"}, tmp_path)
    assert hit is not None and hit[0] == kind
    assert A.GUARD_WHY[kind] in hit[1] and hit[1].startswith("Write ")
    # The eased postures' rule reads the same function.
    assert A.sensitive_path(path, tmp_path) is True


@pytest.mark.parametrize("path", ORDINARY)
def test_an_ordinary_file_is_not_guarded(tmp_path, path):
    assert A.sensitive_kind(path, tmp_path) == ""
    assert A.guarded_edit("write_file", {"path": path, "content": "x"}, tmp_path) is None
    assert A.sensitive_path(path, tmp_path) is False


def test_the_words_say_plainly_why():
    assert "Git's own settings" in A.GUARD_WHY["git"]
    assert "Iron Jarvis's own safety rules" in A.GUARD_WHY["jarvis"]


def test_every_edit_tool_is_read_and_a_patch_by_its_heads(tmp_path):
    for name in ("write_file", "edit_file", "multi_edit", "write_document"):
        assert A.guarded_edit(name, {"path": ".git/config"}, tmp_path) is not None, name
    assert A.guarded_edit("notebook_edit", {"notebook_path": ".claude/x.ipynb"}, tmp_path) is not None
    patch = "*** Begin Patch\n*** Update File: a.py\n@@\n-x\n+y\n*** Add File: .git/hooks/post-checkout\n+echo\n*** End Patch"
    hit = A.guarded_edit("apply_patch", {"patch": patch}, tmp_path)
    assert hit is not None and hit[1].startswith("Patch .git/hooks/post-checkout")
    # Not an edit tool: never read here (the command guards are their own).
    assert A.guarded_edit("shell", {"command": "echo x > .git/config"}, tmp_path) is None
    assert A.guarded_edit("read_file", {"path": ".git/config"}, tmp_path) is None


def test_a_folder_whose_own_path_holds_a_sensitive_name_guards_only_below_it(tmp_path):
    """A checkout under ``.claude/worktrees`` (or a CI runner's short-named
    TEMP): only the part BELOW the folder is the edit's target."""
    ws = tmp_path / ".claude" / "worktrees" / "wt"
    ws.mkdir(parents=True)
    assert A.guarded_edit("write_file", {"path": str(ws / "a.py")}, ws) is None
    assert A.guarded_edit("write_file", {"path": "a.py"}, ws) is None
    assert A.guarded_edit("write_file", {"path": str(ws / ".git" / "config")}, ws)[0] == "git"
    # The written form keeps a short folder name the resolve cannot read.
    short = str(ws).replace("worktrees", "WORKTR~9")
    assert A._written_parts(short + "\\a.py", Path(short)) == ["a.py"]


def test_outside_the_folder_is_left_to_the_tools_own_refusal(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    assert A.guarded_edit("write_file", {"path": str(tmp_path / ".git" / "config")}, ws) is None
    assert A.guarded_edit("write_file", {"path": ".git/config"}, None) is None


async def test_the_one_helper_makes_it_a_once_only_card_and_dont_ask_drops_it(tmp_path):
    sc = guarded_edit_check("write_file", {"path": ".git/config"}, tmp_path)
    assert sc is not None and sc.ask and sc.once_only and sc.reason == A.GUARD_REASON
    assert sc.dont_ask_skips and sc.attended_only
    platform = build_platform(str(tmp_path / "home"))
    ctx = ToolContext(workspace=tmp_path, session_id="chat", agent_run_id="r", config=platform.config,
                      event_bus=platform.event_bus, engine=platform.engine)
    calls = [ToolCall(id="1", name="write_file", arguments={"path": ".git/config", "content": "x"}),
             ToolCall(id="2", name="write_file", arguments={"path": "a.py", "content": "x"})]
    for mode in ("approve_for_me", "always_ask", "accept_edits", "auto", ""):
        got = await spend_checks_for(platform.registry, calls, ctx, approval_mode=mode)
        assert list(got) == [id(calls[0])], mode
        assert got[id(calls[0])].reason == "guarded"
    assert await spend_checks_for(platform.registry, calls, ctx, approval_mode="yolo") == {}


# --------------------------------------------------------------------------- #
# the lanes, real
# --------------------------------------------------------------------------- #
def _frames(chunks: list[str]) -> list[tuple[str, dict]]:
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


class Model:
    def __init__(self, moves: list) -> None:
        self.moves = list(moves)

    def _next(self, tools):
        return self.moves.pop(0) if (self.moves and tools) else "Done."

    async def stream(self, *, system, messages, tools, provider=None, model=None, **kw):
        move = self._next(tools)
        if isinstance(move, str):
            resp = LLMResponse(text=move, usage={"input_tokens": 3, "output_tokens": 1})
            yield {"type": "text", "text": move}
        else:
            resp = LLMResponse(text="", tool_calls=list(move), usage={"input_tokens": 3, "output_tokens": 1})
        yield {"type": "final", "response": resp, "provider": CLAUDE[0], "model": CLAUDE[1]}

    async def complete(self, *, system, messages, tools, provider=None, model=None, **kw):
        move = self._next(tools)
        if isinstance(move, str):
            return RouteResult(LLMResponse(text=move, usage={}), *CLAUDE)
        return RouteResult(LLMResponse(text="", tool_calls=list(move), usage={}), *CLAUDE)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    platform = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(chat_routes, "_ASK_POLL_S", 0.05)

    def wire(moves):
        model = Model(moves)
        monkeypatch.setattr(platform.router, "stream", model.stream)
        monkeypatch.setattr(platform.router, "complete", model.complete)
        return model

    return SimpleNamespace(platform=platform, ws=ws, wire=wire)


def _write(path: str, cid: str = "w1", content: str = "[core]\n\tfsmonitor = calc\n") -> ToolCall:
    return ToolCall(id=cid, name="write_file", arguments={"path": path, "content": content})


def _body(ws: Path, mode: str, **kw) -> ChatBody:
    return ChatBody(
        messages=[ChatMessageBody(role="user", content="tidy up the repo settings")],
        provider=CLAUDE[0], model=CLAUDE[1], workspace_dir=str(ws), approval_mode=mode,
        tools=["write_file"], **kw,
    )


async def _stream(platform, body, *, answer: str = "deny", **kw) -> dict:
    approvals = chat_routes._approvals(SimpleNamespace(platform=platform))
    gen = await stream_chat_turn(platform, BUILTIN_PERSONAS, body, **kw)
    frames = []
    async for chunk in gen:
        for event, data in _frames([chunk]):
            frames.append((event, data))
            if event == "approval" and answer:
                assert approvals.resolve(str(data["id"]), answer)
    await flush_interrupt_notes()
    done = [d for e, d in frames if e == "done"]
    assert done, frames[-4:]
    return {"done": done[0], "cards": [d for e, d in frames if e == "approval"]}


def _ledger(platform, tool: str = "write_file") -> list[ToolInvocation]:
    with session_scope(platform.engine) as db:
        rows = list(db.exec(select(ToolInvocation).where(ToolInvocation.tool == tool)))
        for r in rows:
            db.expunge(r)
        return rows


@pytest.mark.parametrize("mode", ["approve_for_me", "always_ask"])
@pytest.mark.parametrize("path,kind", FAMILIES)
async def test_the_stream_lane_cards_once_only_and_a_deny_never_writes(env, mode, path, kind):
    env.wire([[_write(path)]])
    out = await _stream(env.platform, _body(env.ws, mode), answer="deny")
    (card,) = out["cards"]
    assert card["tool"] == "write_file"
    assert card["once_only"] is True and card["once_reason"] == "guarded"
    assert card["can_always"] is False
    assert A.GUARD_WHY[kind] in card["cost_note"]
    assert not (env.ws / path).exists()
    (row,) = _ledger(env.platform)
    assert row.ok is False and "declined" in (row.output or "") and row.verdict.value == "ask"


async def test_the_stream_lane_writes_after_allow_once_and_a_wider_answer_is_read_as_once(env):
    env.wire([[_write(".git/config", "w1")], [_write(".git/hooks/pre-commit", "w2", "echo hi\n")]])
    out = await _stream(env.platform, _body(env.ws, "approve_for_me"), answer="conversation")
    # The second edit cards AGAIN: "for this conversation" granted nothing.
    assert [c["tool"] for c in out["cards"]] == ["write_file", "write_file"]
    assert (env.ws / ".git" / "config").read_text(encoding="utf-8").startswith("[core]")
    assert (env.ws / ".git" / "hooks" / "pre-commit").exists()
    assert [r.ok for r in _ledger(env.platform)] == [True, True]


async def test_control_an_ordinary_file_runs_with_no_card_under_ask_first(env):
    env.wire([[_write("notes.txt", content="hello")]])
    out = await _stream(env.platform, _body(env.ws, "approve_for_me"))
    assert out["cards"] == []
    assert (env.ws / "notes.txt").read_text(encoding="utf-8") == "hello"
    assert [r.ok for r in _ledger(env.platform)] == [True]


async def test_dont_ask_leaves_it_uncarded_the_user_chose_it(env):
    env.wire([[_write(".git/config")]])
    out = await _stream(env.platform, _body(env.ws, "yolo"))
    assert out["cards"] == []
    assert (env.ws / ".git" / "config").exists()
    assert [r.ok for r in _ledger(env.platform)] == [True]


@pytest.mark.parametrize("mode", ["accept_edits", "auto"])
async def test_the_eased_postures_card_it_once_only_too(env, mode):
    env.wire([[_write(".ironjarvis/harness/plugins.toml", content="[[rules]]\n")]])
    out = await _stream(env.platform, _body(env.ws, mode))
    (card,) = out["cards"]
    assert card["once_only"] is True and card["once_reason"] == "guarded"
    assert not (env.ws / ".ironjarvis").exists()
    assert "auto_approved" not in out["done"]["route"]


async def test_a_coding_session_cards_its_dialects_write_of_git_settings(env):
    """The IJ harness (a coding session) runs through the same lane — Claude's
    ``Write`` with ``file_path`` translates to ``write_file``."""
    env.wire([[ToolCall(id="w1", name="Write", arguments={"file_path": ".git/config", "content": "x"})]])
    body = ChatBody(messages=[ChatMessageBody(role="user", content="fix the config")],
                    provider=CLAUDE[0], model=CLAUDE[1], workspace_dir=str(env.ws),
                    approval_mode="approve_for_me")
    out = await _stream(env.platform, body, harness=True)
    (card,) = out["cards"]
    assert card["tool"] == "write_file" and card["once_reason"] == "guarded"
    assert not (env.ws / ".git").exists()


async def test_a_night_task_cards_it_and_its_card_wait_declines_it(env):
    """The night shift lets file edits run unasked (`harness/night.
    RUNS_UNASKED`); a guarded edit still cards, nobody answers, and the
    night's own card wait declines it — its normal path."""
    from iron_jarvis.harness import night as N

    env.wire([[_write(".git/hooks/pre-commit", content="echo pwned\n")]])
    token = N.set_turn(N.NightTurn(task_id="night_guard", card_wait_s=0.3))
    try:
        out = await _stream(env.platform, _body(env.ws, "approve_for_me"), answer="")
    finally:
        N.reset_turn(token)
    (card,) = out["cards"]
    assert card["once_reason"] == "guarded" and card["timeout_s"] >= 1
    assert not (env.ws / ".git").exists()
    (row,) = _ledger(env.platform)
    assert row.ok is False


def test_the_guard_is_a_safety_card_everywhere():
    """Never sent to the phone (a safety card), and the dashboard's reason
    whitelist knows the same word."""
    from iron_jarvis.harness import stall

    assert A.GUARD_REASON in stall.SAFETY_REASONS
    ts = (Path(__file__).resolve().parents[1] / "dashboard" / "lib" / "onceReason.ts").read_text(encoding="utf-8")
    assert f'"{A.GUARD_REASON}"' in ts


@pytest.mark.parametrize("path,kind", FAMILIES)
async def test_the_post_lane_refuses_it_in_plain_words(env, path, kind):
    env.wire([[_write(path)]])
    out = await run_chat_turn(env.platform, BUILTIN_PERSONAS, _body(env.ws, "approve_for_me"))
    assert not (env.ws / path).exists()
    (row,) = _ledger(env.platform)
    assert row.ok is False
    assert A.GUARD_WHY[kind] in (row.output or "")
    assert "this lane cannot ask for approval" in (row.output or "")
    assert out["reply"]


async def test_the_post_lane_under_dont_ask_and_an_ordinary_file_run_as_before(env):
    env.wire([[_write(".git/config", "w1"), _write("ok.txt", "w2", "fine")]])
    await run_chat_turn(env.platform, BUILTIN_PERSONAS, _body(env.ws, "yolo"))
    assert (env.ws / ".git" / "config").exists() and (env.ws / "ok.txt").exists()
    env2 = env.platform
    env.wire([[_write("ok2.txt", "w3", "fine")]])
    await run_chat_turn(env2, BUILTIN_PERSONAS, _body(env.ws, "approve_for_me"))
    assert (env.ws / "ok2.txt").exists()


# --------------------------------------------------------------------------- #
# the agent runtime (helpers, teammates, a chat's escalation, the doors)
# --------------------------------------------------------------------------- #
DEFN = AgentDefinition(type=AgentType.BUILDER, system_prompt="t", tools=["write_file"])


def _run_env(tmp_path, path: str):
    p = build_platform(str(tmp_path / "home"))
    turn = LLMResponse(tool_calls=[_write(path)], finish_reason="tool_use")
    p.providers.register(
        "mock", lambda model=None: MockLLMAdapter(script=[turn, LLMResponse(text="Done.", finish_reason="stop")])
    )
    ws = tmp_path / "ws"
    ws.mkdir()
    asked: list[dict] = []
    return p, Orchestrator(p), ws, asked


@pytest.mark.parametrize("answer", ["once", "deny"])
async def test_an_attended_run_pauses_once_only_and_writes_only_on_allow(tmp_path, answer):
    p, orch, ws, asked = _run_env(tmp_path, ".git/config")

    def _answer(e):
        if e.type == EventType.APPROVAL_REQUESTED:
            asked.append(dict(e.payload))
            p.approvals.resolve(e.payload["approval_id"], answer)

    p.event_bus.add_handler(_answer)
    s = await orch.create_session("fix it", AgentType.BUILDER, origin="chat", workspace_root=str(ws))
    done = await asyncio.wait_for(orch.run_session(s.id, DEFN), timeout=60)
    assert done.status is SessionStatus.COMPLETED
    (ask,) = asked
    assert ask["tool"] == "write_file" and ask["once_only"] is True
    assert ask["once_reason"] == "guarded" and ask["can_always"] is False
    assert A.GUARD_WHY["git"] in ask["cost_note"]
    assert (ws / ".git" / "config").exists() is (answer == "once")
    (row,) = _ledger(p)
    assert row.ok is (answer == "once")


@pytest.mark.parametrize("origin", ["schedule:nightly", "goal:g1", "reflex:r1", None])
async def test_an_unattended_run_refuses_it_in_words_with_nobody_asked(tmp_path, origin):
    p, orch, ws, asked = _run_env(tmp_path, ".ironjarvis/harness/plugins.toml")
    p.event_bus.add_handler(
        lambda e: asked.append(e) if e.type == EventType.APPROVAL_REQUESTED else None
    )
    s = await orch.create_session("fix it", AgentType.BUILDER, origin=origin, workspace_root=str(ws))
    await asyncio.wait_for(orch.run_session(s.id, DEFN), timeout=60)
    assert asked == []
    assert not (ws / ".ironjarvis").exists()
    (row,) = _ledger(p)
    assert row.ok is False
    assert "nobody is watching this run" in (row.output or "") or "no way to ask" in (row.output or "")
    assert A.GUARD_WHY["jarvis"] in (row.output or "")


async def test_control_an_ordinary_file_in_a_run_needs_no_pause(tmp_path):
    p, orch, ws, asked = _run_env(tmp_path, "notes.txt")
    p.event_bus.add_handler(
        lambda e: asked.append(e) if e.type == EventType.APPROVAL_REQUESTED else None
    )
    s = await orch.create_session("fix it", AgentType.BUILDER, origin="schedule:x", workspace_root=str(ws))
    await asyncio.wait_for(orch.run_session(s.id, DEFN), timeout=60)
    assert asked == [] and (ws / "notes.txt").exists()
