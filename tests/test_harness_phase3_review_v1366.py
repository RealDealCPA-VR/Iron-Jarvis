"""v1.366.0 (IJ harness Phase 3) — the REVIEW's pins.

Each block closes a gap the doer's own pins left, or pins a defect the review
fixed:

* the ledger's new ``called_as`` column reaches a database made by the
  PRE-Phase-3 schema (the reconciler adds it; old rows read; new rows write);
* NO DIALECT CALL EXECUTES A TOOL OUTSIDE THE TURN'S GATE — every dialect,
  every shown name, alias and lower-cased name, every argument shape that
  picks another target, under every plan shape (stream, POST, low trust, a
  ceiling, Local only), through the REAL registry with every tool's
  ``execute`` replaced by a recorder; plus two more lane shapes end to end
  (Codex's ``write_stdin`` typing in the POST lane, Claude's background
  ``Bash`` under low trust in the stream lane);
* a JUNCTION (Windows; no privilege needed, unlike a symbolic link) cannot
  carry the folder's harness layer out of the project;
* a folder's plugin check under Local only and under approve-for-me runs
  nothing on its own;
* the text-tool-call nudge cannot loop, never executes the text, and (the
  review's fix) is never sent to a turn that offered no tool;
* a ``plugins.toml`` naming one tool twice is refused (the review's fix: the
  plan read the first rule while the overrides applied both);
* LOCK-STEP: every harness seam the POST lane calls, the stream lane calls.
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys
from pathlib import Path

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.harness import components as C
from iron_jarvis.harness import dialects as D
from iron_jarvis.harness import mode as M
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import ToolCall
from iron_jarvis.tools.base import ToolContext, ToolResult

from .test_harness_mode_lanes_v1366 import (  # the lane pins' helpers
    CLAUDE,
    GPT,
    Model,
    _body,
    _ledger,
    _post,
    _stream,
    _tool_messages,
    _write_plugins,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """The lane pins' own fixture (test_harness_mode_lanes_v1366.env): a real
    platform, a folder with a.py, and a scripted model wired into the router."""
    platform = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.py").write_text("x = 1\n", encoding="utf-8")

    def wire(moves, who=CLAUDE):
        model = Model(*who, moves)
        monkeypatch.setattr(platform.router, "stream", model.stream)
        monkeypatch.setattr(platform.router, "complete", model.complete)
        return model

    return platform, ws, wire


# --------------------------------------------------------------------------- #
# 1. The ledger column on an OLD database
# --------------------------------------------------------------------------- #


def test_an_old_database_gains_called_as_and_both_kinds_of_row_read(tmp_path):
    """A database made before v1.366.0 has no ``toolinvocation.called_as``. The
    additive reconciler adds it at boot; a row written before reads (NULL →
    the model's name was not recorded), and a new row writes the name."""
    home = tmp_path / "home"
    first = build_platform(str(home))  # today's schema, one row written …
    with session_scope(first.engine) as db:
        db.add(ToolInvocation(id="tool_old", session_id="chat", agent_run_id="chat", tool="read_file",
                              args_json="{}", ok=True, output="x", mode="allow"))
        db.commit()
    first.engine.dispose()
    db_path = next(home.rglob("ironjarvis.db"))
    con = sqlite3.connect(str(db_path))
    con.execute("ALTER TABLE toolinvocation DROP COLUMN called_as")  # … made old
    cols = {r[1] for r in con.execute("PRAGMA table_info(toolinvocation)")}
    assert "called_as" not in cols and "grant_id" in cols
    con.commit()
    con.close()

    platform = build_platform(str(home))  # init_db → _reconcile_additive_columns
    con = sqlite3.connect(str(db_path))
    cols = {r[1] for r in con.execute("PRAGMA table_info(toolinvocation)")}
    con.close()
    assert "called_as" in cols, "the reconciler did not add toolinvocation.called_as"
    with session_scope(platform.engine) as db:
        old = db.get(ToolInvocation, "tool_old")
        assert old is not None and old.tool == "read_file" and not old.called_as
        db.add(ToolInvocation(session_id="chat", agent_run_id="chat", tool="shell", args_json="{}",
                              ok=False, output="", mode="deny", called_as="Bash"))
        db.commit()
    with session_scope(platform.engine) as db:
        rows = db.exec(select(ToolInvocation).where(ToolInvocation.tool == "shell")).all()
        assert [r.called_as for r in rows] == ["Bash"]


# --------------------------------------------------------------------------- #
# 2. No dialect call executes a tool outside the gate — every shape
# --------------------------------------------------------------------------- #


def _variants(tool: D.DialectTool) -> list[dict]:
    """Argument shapes that steer a translation to each of its targets."""
    base: dict = {}
    for key in tool.input_schema.get("required", []):
        base[key] = "1" if key.endswith("id") else "a.py"
    out = [dict(base)]
    out.append({**base, "file_path": "a.png"})                     # Read → view_image
    out.append({**base, "run_in_background": True})                # Bash → process_start
    out.append({**base, "is_background": True})                    # qwen shell → process_start
    out.append({**base, "chars": "y\n"})                           # write_stdin → process_write
    out.append({**base, "chars": "\u0003"})                        # write_stdin → process_stop
    out.append({**base, "chars": ""})                              # write_stdin → process_read
    return out


def _plans(platform) -> list[tuple[str, D.Dialect, M.ToolPlan]]:
    from iron_jarvis.providers import egress

    out = []
    for dialect in D.DIALECTS.values():
        reg, perms = platform.registry, platform.permissions
        out.append(("stream", dialect, M.plan_tools(reg, perms, dialect, family="x", stream=True)))
        out.append(("post", dialect, M.plan_tools(reg, perms, dialect, family="x", stream=False)))
        out.append(("low", dialect, M.plan_tools(reg, perms, dialect, family="x", stream=True, low_trust=True)))
        out.append(("ceiling", dialect, M.plan_tools(
            reg, perms, dialect, family="x", stream=True,
            tool_ceiling=frozenset(M.CODING_TOOLS) - {"shell", "process_start", "process_write", "view_image"},
        )))
        with egress.local_only_scope(True, False):
            out.append(("local-post", dialect, M.plan_tools(
                reg, perms, dialect, family="x", stream=False, local_only=True)))
    return out


async def test_no_dialect_call_executes_a_tool_outside_the_turns_gate(tmp_path, monkeypatch):
    platform = build_platform(str(tmp_path / "home"))
    ran: list[str] = []
    for name in platform.registry.names():
        tool = platform.registry.get(name)

        async def execute(args, ctx, _name=name):
            ran.append(_name)
            return ToolResult(ok=True, output="recorded")

        monkeypatch.setattr(tool, "execute", execute)
    ctx = ToolContext(workspace=tmp_path, session_id="chat", agent_run_id="chat",
                      config=platform.config, event_bus=platform.event_bus, engine=platform.engine)
    checked = outside = 0
    for label, dialect, plan in _plans(platform):
        gate = plan.gate
        turn = M.HarnessTurn(requested=dialect.id, plan=plan, layer=C.EMPTY_LAYER,
                             tree=C.compose(dialect_section="", dialect_id=dialect.id), rendered="")
        names = []
        for tool in dialect.tools:
            names += [tool.name, tool.name.lower(), *tool.aliases]
        names += list(M.CODING_TOOLS) + ["escalate_to_agent", "delete_file", "repl"]
        for n in names:
            tool = dialect.lookup(n)
            for args in (_variants(tool) if tool is not None else [{"path": "a.py", "command": "echo"}]):
                call = ToolCall(id=f"{label}-{n}", name=n, arguments=dict(args))
                (real,) = turn.translate_calls([call])
                ran.clear()
                await platform.registry.invoke(
                    real.name, real.arguments, ctx, platform.permissions, {},
                    session_allow=set(gate), allowed_names=set(gate),
                )
                checked += 1
                if real.name not in gate:
                    outside += 1
                    assert ran == [], (label, dialect.id, n, args, real.name, sorted(gate))
                else:
                    assert ran in ([], [real.name]), (label, n, ran)
    assert checked > 500 and outside > 50, (checked, outside)


async def test_codex_typing_in_the_post_lane_never_reaches_process_write(env):
    """The POST lane arms no ask-tier tool, so Codex's write_stdin (offered
    for its polling) cannot TYPE: process_write is refused as not armed."""
    platform, ws, wire = env
    model = wire([[ToolCall(id="w1", name="write_stdin", arguments={"session_id": "1", "chars": "rm -rf\n"})]],
                 who=GPT)
    out = await _post(platform, _body(ws, who=GPT), harness=True)
    assert "write_stdin" in model.calls[0]["tools"]
    assert out["tools_used"] == []
    assert [(r.tool, r.called_as, r.ok) for r in _ledger(platform)] == [("process_write", "write_stdin", False)]
    assert "not one of this agent's tools" in _ledger(platform)[0].output


async def test_a_background_bash_under_low_trust_never_starts_a_program(env, monkeypatch):
    """Low trust keeps process_start away; a Bash with run_in_background
    translates to it and is refused, ledgered under the name the model used."""
    platform, ws, wire = env
    started: list = []

    async def execute(args, ctx):
        started.append(args)
        return ToolResult(ok=True, output="started")

    monkeypatch.setattr(platform.registry.get("process_start"), "execute", execute)
    wire([[ToolCall(id="b1", name="Bash", arguments={"command": "python -m http.server",
                                                     "run_in_background": True})]])
    await _stream(platform, _body(ws), harness=True, trust="low")
    assert started == []
    rows = [(r.tool, r.called_as, r.ok) for r in _ledger(platform)]
    assert rows == [("process_start", "Bash", False)], rows


# --------------------------------------------------------------------------- #
# 3. A junction cannot carry the layer out of the project
# --------------------------------------------------------------------------- #


def _junction(target: Path, link: Path) -> None:
    if sys.platform != "win32":
        pytest.skip("junctions are a Windows feature")
    import _winapi

    _winapi.CreateJunction(str(target), str(link))


def _invalid_note(folder: Path) -> str:
    layer = C.read_project_layer(str(folder))
    assert layer.present and not layer.used, layer
    return layer.note


def test_a_junctioned_ironjarvis_folder_is_refused(tmp_path):
    outside = tmp_path / "elsewhere"
    (outside / "harness").mkdir(parents=True)
    (outside / "harness" / "system_prompt.md").write_text("OUTSIDE PROMPT", encoding="utf-8")
    proj = tmp_path / "proj"
    proj.mkdir()
    _junction(outside, proj / ".ironjarvis")
    assert "outside the project folder" in _invalid_note(proj)


def test_a_junctioned_skill_folder_is_refused(tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "SKILL.md").write_text("---\ndescription: Use when anything\n---\nOUTSIDE BODY", encoding="utf-8")
    proj = tmp_path / "proj"
    hd = proj / ".ironjarvis" / "harness"
    (hd / "skills").mkdir(parents=True)
    _junction(outside, hd / "skills" / "leak")
    assert "points outside the harness folder" in _invalid_note(proj)


def test_a_junctioned_sub_agents_folder_is_refused(tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "r.md").write_text("---\ndescription: a reviewer\n---\nbody", encoding="utf-8")
    proj = tmp_path / "proj"
    hd = proj / ".ironjarvis" / "harness"
    hd.mkdir(parents=True)
    _junction(outside, hd / "sub_agents")
    assert "points outside the harness folder" in _invalid_note(proj)


# --------------------------------------------------------------------------- #
# 4. A folder's check never runs on its own under Local only / approve-for-me
# --------------------------------------------------------------------------- #


_CHECK = '[[checks]]\nfiles = ["*.py"]\ncommand = "echo checked"\n'


@pytest.fixture()
def shell_runs(env, monkeypatch):
    platform, _ws, _wire = env
    ran: list[dict] = []

    async def execute(args, ctx):
        ran.append(dict(args))
        return ToolResult(ok=True, output="checked fine")

    monkeypatch.setattr(platform.registry.get("shell"), "execute", execute)
    return ran


async def test_a_check_never_runs_in_a_local_only_chat(env, shell_runs):
    platform, ws, wire = env
    _write_plugins(ws, _CHECK)
    who = ("mock", "scripted")  # a local model: the turn itself may run
    model = wire([[ToolCall(id="e1", name="edit_file", arguments={"path": "a.py", "old": "x = 1", "new": "x = 2"})]],
                 who=who)
    body = _body(ws, who=who, granted_tools=["shell"], local_only=True)
    await _stream(platform, body, harness="generic")
    assert (ws / "a.py").read_text(encoding="utf-8") == "x = 2\n"
    assert shell_runs == []
    assert "Check not run (this chat is local only, so commands ask first)" in (
        _tool_messages(model.calls[1])[0].content)


async def test_a_check_under_approve_for_me_asks_nobody_and_runs_nothing(env, shell_runs):
    platform, ws, wire = env
    _write_plugins(ws, '[[checks]]\nfiles = ["*.py"]\ncommand = "echo checked"\n')
    model = wire([[ToolCall(id="e1", name="Edit", arguments={
        "file_path": "a.py", "old_string": "x = 1", "new_string": "x = 2"})]])
    got = await _stream(platform, _body(ws, approval_mode="approve_for_me"), harness=True)
    assert [e for e, _ in got["frames"] if e == "approval"] == []  # the check never cards
    assert shell_runs == []
    assert "Check not run (running it needs the user's approval)" in _tool_messages(model.calls[1])[0].content
    assert ("shell", "plugin check", False) in [(r.tool, r.called_as, r.ok) for r in _ledger(platform)]


async def test_a_check_in_the_post_lane_never_sends_the_model_to_a_command_tool_it_lacks(env, shell_runs):
    """The POST lane arms no command tool, so the refused check must not say
    "run it yourself with your command tool" (review fix)."""
    platform, ws, wire = env
    _write_plugins(ws, _CHECK)
    model = wire([[ToolCall(id="e1", name="Edit", arguments={
        "file_path": "a.py", "old_string": "x = 1", "new_string": "x = 2"})]])
    await _post(platform, _body(ws), harness=True)
    content = _tool_messages(model.calls[1])[0].content
    assert shell_runs == []
    assert "Check not run (this turn cannot run commands)" in content
    assert "with your command tool" not in content


# --------------------------------------------------------------------------- #
# 5. The text-tool-call nudge: bounded, never executed, only with tools
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_model_that_only_ever_writes_calls_as_text_is_nudged_once(env, lane):
    platform, ws, wire = env
    text = '<tool_call>{"name": "Bash", "arguments": {"command": "echo hi> made.txt"}}</tool_call>'
    model = wire([text] * 10)
    body = _body(ws)
    out = (await _stream(platform, body, harness=True))["done"] if lane == "stream" else await _post(
        platform, body, harness=True)
    assert len(model.calls) == 2, "one nudge, then the second text reply ends the turn"
    assert _ledger(platform) == [] and not (ws / "made.txt").exists()
    assert out["route"]["harness"]["nudged"] is True


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_turn_that_offered_no_tool_is_never_nudged(env, monkeypatch, lane):
    """A text-only model in harness mode is offered no tool; a reply that
    happens to contain a tool-call tag is its answer, not a call to retry."""
    from iron_jarvis.daemon import chat_turn

    platform, ws, wire = env
    monkeypatch.setattr(chat_turn, "is_text_only_pick", lambda *a, **k: True)
    model = wire([])
    model.moves = []

    async def stream(*, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        from iron_jarvis.providers.adapters.base import LLMResponse

        calls.append(list(tools or []))
        resp = LLMResponse(text="GLM writes <tool_call>Read</tool_call> when it calls a tool.",
                           usage={"input_tokens": 3, "output_tokens": 1})
        yield {"type": "text", "text": resp.text}
        yield {"type": "final", "response": resp, "provider": "anthropic", "model": "claude-x"}

    async def complete(*, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        from iron_jarvis.providers.adapters.base import LLMResponse
        from iron_jarvis.providers.router import RouteResult

        calls.append(list(tools or []))
        return RouteResult(LLMResponse(text="GLM writes <tool_call>Read</tool_call> when it calls a tool.",
                                       usage={"input_tokens": 3, "output_tokens": 1}), "anthropic", "claude-x")

    calls: list = []
    monkeypatch.setattr(platform.router, "stream", stream)
    monkeypatch.setattr(platform.router, "complete", complete)
    body = _body(ws)
    out = (await _stream(platform, body, harness=True))["done"] if lane == "stream" else await _post(
        platform, body, harness=True)
    assert calls == [[]], calls
    assert out["route"]["harness"]["nudged"] is False
    assert "<tool_call>Read</tool_call>" in out["reply"]


# --------------------------------------------------------------------------- #
# 6. plugins.toml: one rule per tool
# --------------------------------------------------------------------------- #


def test_a_tool_named_by_two_rules_voids_the_layer(tmp_path):
    hd = tmp_path / ".ironjarvis" / "harness"
    hd.mkdir(parents=True)
    (hd / "plugins.toml").write_text(
        '[[rules]]\ntool = "write_file"\naction = "ask"\n[[rules]]\ntool = "write_file"\naction = "deny"\n',
        encoding="utf-8",
    )
    layer = C.read_project_layer(str(tmp_path), known_tools={"write_file"})
    assert not layer.used and "two rules for write_file" in layer.note


# --------------------------------------------------------------------------- #
# 7. LOCK-STEP: every harness seam is called in BOTH lanes
# --------------------------------------------------------------------------- #

#: Each needle is a CALL (never a definition), the same words in both lanes.
_SEAMS = (
    "harness = turn_harness(harness)" + "|" + "harness = _chat_turn.turn_harness(harness)",
    "harness_turn_knobs(harness, round_budget, stay_in_chat)",
    "_harness, _harness_rules = await _build_harness(d, body, harness,"
    + "|" + "_harness, _harness_rules = await _chat_turn._build_harness( d, body, harness,",
    "system = _harness.system_without_exits(system,", "_grounding.harness = True",
    "system += _harness_rules + _harness.block()", "tool_specs = _harness.specs(d.platform.registry)",
    "armed, auto_armed = list(_harness.plan.armed), []",
    "_harness.narrow(overrides, d.platform.registry, d.platform.permissions)",
    "calls = _harness.translate_calls(calls)", "tool_calls=_model_calls",
    "_harness.wants_nudge(", "LLMMessage(role=\"user\", content=TEXT_TOOL_CALL_NUDGE)",
    "_harness.ctx_for(ctx, tc) if _harness is not None else ctx",
    "await _harness.after_edit(", "harness_check_runner( d, ctx, overrides, lambda: armed_grant,",
    "harness_check_gate(_trust_state)", "_harness.model_text(tc,",
    "name=(_harness.called_as(tc) if _harness is not None else tc.name)",
    '**({"harness": _harness.route_info()} if _harness is not None else {})',
    "if _harness is None: system += _tools_block",
    "workflow_draft = _draft_from_calls(calls, armed) if _harness is None else None",
    ") if _harness is None else None",
    "if not text_only_pick and _harness is None:",
)


def _src(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8").replace("\r\n", "\n")


@pytest.mark.parametrize("seam", _SEAMS)
def test_every_harness_seam_is_in_both_lanes(seam):
    """A source pin (CRLF normalised at the reader, whitespace folded: the
    two lanes indent differently). ``a|b`` = the POST lane's words | the
    stream lane's, where only the module prefix differs."""
    norm = lambda s: re.sub(r"\s+", " ", s).replace("( ", "(")  # noqa: E731
    post = norm(_src("src/iron_jarvis/daemon/chat_turn.py"))
    stream = norm(_src("src/iron_jarvis/daemon/routes/chat.py"))
    post_needle, _, stream_needle = seam.partition("|")
    assert norm(post_needle) in post, f"the POST lane lost {post_needle!r}"
    assert norm(stream_needle or post_needle) in stream, f"the stream lane lost {stream_needle or post_needle!r}"
    assert os.path.exists(ROOT / "src/iron_jarvis/harness/mode.py")
