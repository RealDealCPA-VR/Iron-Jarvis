"""Third north-star fix plan, group 17 (part): every way in meets the gate.

PG1 (the one high finding, decision K1). A HELD-BACK app tool (an MCP tool
that arrived in a later update of a connected app and can change things,
``Tool.quarantined``) asks every time in chat until it is trusted on the app's
own page, WHATEVER is switched on for the chat. Before this, switching the app
on for the chat armed every one of its tools by name, each armed name rode
``session_allow``, and the gate's one exception for a held-back tool ("its own
name is in session_allow") was met by the arming — the tool ran with no card.
Now the gate takes the CARD's answers apart (``card_allow``) and a held-back
tool is lifted only by them; an ``allow`` from an override or the base policy
lifts it no more than a grant does. Proved through the REAL lanes: only the
model and each tool's ``execute`` are doubles.

PG7. "Always allow exactly this" dropped every argument whose name starts with
``_``, so an exact approval for an app tool's ``{"_id": "a"}`` covered
``{"_id": "b"}``. Only the app's own control keys are dropped now.

PG5 (decision K5, as settled by the lead). ``web_search`` / ``web_fetch``
always run without asking (so unattended work can search): Settings offers
Allow or Never for them, refuses "Ask first" in words, and shows a stored
"ask" from an older version as Allow.

PG10. The refusal when nobody can be asked is in plain words, names no
``allow_tools`` and no ``permissions.<tool>``, and never advises a Settings
change Settings refuses (a floor tool).
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from sqlmodel import select

from iron_jarvis.core.approvals import flush_interrupt_notes
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import PermissionMode, ToolInvocation
from iron_jarvis.daemon.chat_stream import stream_chat_turn
from iron_jarvis.daemon.chat_turn import run_chat_turn
from iron_jarvis.daemon.routes import chat as chat_routes
from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
from iron_jarvis.mcp.tools import MCPRemoteTool
from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.base import ToolContext, ToolResult
from iron_jarvis.tools.permissions import (
    DENY_FLOOR_TOOLS,
    PermissionEngine,
    headless_ask_resolver,
)

CLAUDE = ("anthropic", "claude-sonnet-4-5")
APP = "notes"
HELD = f"mcp__{APP}__delete_page"  # arrived in the app's update: held back
KEPT = f"mcp__{APP}__update_page"  # was there when the app was connected


# --------------------------------------------------------------------------- #
# Lane harness: the real registry, gate, approvals and ledger.
# --------------------------------------------------------------------------- #
def _frames(chunk: str) -> list[tuple[str, dict]]:
    out = []
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
    """Each round with tools makes the next scripted calls; then "Done."."""

    def __init__(self, moves: list) -> None:
        self.moves = list(moves)

    async def stream(self, *, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        move = self.moves.pop(0) if (self.moves and tools) else "Done."
        if isinstance(move, str):
            resp = LLMResponse(text=move, usage={"input_tokens": 3, "output_tokens": 1})
            yield {"type": "text", "text": move}
        else:
            resp = LLMResponse(text="", tool_calls=list(move), usage={"input_tokens": 3, "output_tokens": 1})
        yield {"type": "final", "response": resp, "provider": CLAUDE[0], "model": CLAUDE[1]}

    async def complete(self, *, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        move = self.moves.pop(0) if (self.moves and tools) else "Done."
        if isinstance(move, str):
            return RouteResult(LLMResponse(text=move, usage={}), *CLAUDE)
        return RouteResult(LLMResponse(text="", tool_calls=list(move), usage={}), *CLAUDE)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    platform = build_platform(str(tmp_path / "home"))
    monkeypatch.setattr(chat_routes, "_ASK_POLL_S", 0.05)
    ran: dict[str, list[dict]] = {}
    for remote, held in (("update_page", False), ("delete_page", True)):
        spec = {"name": remote, "inputSchema": {"type": "object", "properties": {}}}
        tool = MCPRemoteTool.from_spec(SimpleNamespace(), APP, spec)
        tool.quarantined = held
        ran[tool.name] = []

        async def _fake(args, ctx, _n=tool.name):
            ran[_n].append(dict(args))
            return ToolResult(ok=True, output=f"{_n} ran")

        tool.execute = _fake  # type: ignore[method-assign]
        platform.registry.register(tool, mcp=True)

    def wire(moves):
        model = Model(moves)
        monkeypatch.setattr(platform.router, "stream", model.stream)
        monkeypatch.setattr(platform.router, "complete", model.complete)

    return SimpleNamespace(platform=platform, ran=ran, wire=wire)


def _call(name: str, cid: str) -> list[ToolCall]:
    return [ToolCall(id=cid, name=name, arguments={})]


def _body(mode: str = "approve_for_me", **kw) -> ChatBody:
    """The app switched ON for the chat (its tools armed by name)."""
    return ChatBody(
        messages=[ChatMessageBody(role="user", content="tidy up my pages")],
        provider=CLAUDE[0], model=CLAUDE[1], approval_mode=mode, connectors=[APP], **kw,
    )


async def _stream(platform, body, answers: list[str]) -> dict:
    approvals = chat_routes._approvals(SimpleNamespace(platform=platform))
    queue = list(answers)
    gen = await stream_chat_turn(platform, BUILTIN_PERSONAS, body)
    frames = []
    async for chunk in gen:
        for event, data in _frames(chunk):
            frames.append((event, data))
            if event == "approval":
                assert approvals.resolve(str(data["id"]), queue.pop(0) if queue else "deny")
    await flush_interrupt_notes()
    assert [d for e, d in frames if e == "done"], frames[-4:]
    return {"cards": [d for e, d in frames if e == "approval"]}


def _ledger(platform) -> list[tuple[str, bool]]:
    with session_scope(platform.engine) as db:
        rows = db.exec(select(ToolInvocation).order_by(ToolInvocation.created_at)).all()
        return [(r.tool, bool(r.ok)) for r in rows]


# --------------------------------------------------------------------------- #
# PG1 — the stream lane (the dashboard's lane).
# --------------------------------------------------------------------------- #
async def test_stream_lane_cards_a_held_back_tool_its_app_switched_on(env):
    """The app is on for the chat. Its original tool runs unasked (the
    control); the held-back one shows a card that says it is new, and the
    user's No keeps it from running."""
    env.wire([_call(KEPT, "k1"), _call(HELD, "h1")])
    out = await _stream(env.platform, _body(), ["deny"])
    assert env.ran[KEPT] == [{}], "control: the app's original tool runs with no card"
    assert [c["tool"] for c in out["cards"]] == [HELD], out["cards"]
    assert out["cards"][0]["quarantined"] is True
    assert out["cards"][0]["can_always"] is False
    assert out["cards"][0]["why"] == "it is new in its app"
    assert env.ran[HELD] == [], "the held-back tool ran with no card"
    assert (HELD, False) in _ledger(env.platform)


async def test_stream_lane_allow_once_runs_it_once_and_the_next_call_asks_again(env):
    env.wire([_call(HELD, "h1"), _call(HELD, "h2")])
    out = await _stream(env.platform, _body(), ["once", "deny"])
    assert [c["tool"] for c in out["cards"]] == [HELD, HELD], "the second call must ask again"
    assert env.ran[HELD] == [{}], "Allow once runs exactly the call it was asked about"
    assert _ledger(env.platform).count((HELD, True)) == 1


def _spy_invoke(env, monkeypatch) -> list[dict]:
    """Record what each lane hands the REAL ``registry.invoke`` (which still
    runs): the held-back tool's call must carry the card answers apart."""
    seen: list[dict] = []
    real = env.platform.registry.invoke

    async def spy(name, args, ctx, perms, *a, **kw):
        seen.append({"name": name, **kw})
        return await real(name, args, ctx, perms, *a, **kw)

    monkeypatch.setattr(env.platform.registry, "invoke", spy)
    return seen


async def test_both_lanes_hand_the_gate_the_card_answers_apart_from_armed_names(env, monkeypatch):
    seen = _spy_invoke(env, monkeypatch)
    env.wire([_call(HELD, "h1"), _call(KEPT, "k1")])
    await _stream(env.platform, _body(), ["once"])
    held = [s for s in seen if s["name"] == HELD]
    assert len(held) == 1 and HELD in held[0]["session_allow"], "arming put the name in the grant"
    assert held[0]["card_allow"] == frozenset({HELD}), "the card's own answer, apart"
    assert "card_allow" not in [k for s in seen if s["name"] == KEPT for k in s], "others: byte-identical"

    seen.clear()
    env.wire([_call(HELD, "h2")])
    await run_chat_turn(env.platform, BUILTIN_PERSONAS, _body())
    (post,) = [s for s in seen if s["name"] == HELD]
    assert post["card_allow"] == frozenset() and HELD in post["session_allow"]


async def test_stream_lane_allow_for_this_conversation_covers_the_later_call(env):
    env.wire([_call(HELD, "h1"), _call(HELD, "h2")])
    out = await _stream(env.platform, _body(), ["conversation"])
    assert [c["tool"] for c in out["cards"]] == [HELD]
    assert env.ran[HELD] == [{}, {}]


async def test_stream_lane_a_conversation_answer_sent_back_by_the_page_covers_it(env):
    """The page keeps "Allow for this conversation" as ``granted_tools`` and
    sends it with every turn: that is a card answer, kept apart from arming."""
    env.wire([_call(HELD, "h1")])
    out = await _stream(env.platform, _body(granted_tools=[HELD]), [])
    assert out["cards"] == [] and env.ran[HELD] == [{}]


async def test_stream_lane_dont_ask_still_cards_a_held_back_tool(env):
    """"Don't ask" is a standing posture, a blanket answer: the held-back
    tool is lifted by no blanket answer (the v1.299.0 quarantine note), so it
    is carded under it too. Its sibling runs unasked."""
    env.wire([_call(KEPT, "k1"), _call(HELD, "h1")])
    out = await _stream(env.platform, _body(mode="yolo"), ["deny"])
    assert env.ran[KEPT] == [{}]
    assert [c["tool"] for c in out["cards"]] == [HELD]
    assert env.ran[HELD] == []


async def test_stream_lane_a_sibling_answer_never_covers_the_held_back_tool(env, monkeypatch):
    """Both app tools brought in by the message (ask-armed: each cards). They
    share the app's permission key, which used to make ONE card for both: the
    sibling's "Allow once" ran the held-back tool too. It has its own card."""
    from iron_jarvis.tools import autoselect

    monkeypatch.setattr(autoselect, "select_ask_tools", lambda text: [KEPT, HELD])
    env.wire([_call(KEPT, "k1") + _call(HELD, "h1")])
    body = ChatBody(
        messages=[ChatMessageBody(role="user", content="tidy up my pages")],
        provider=CLAUDE[0], model=CLAUDE[1], approval_mode="approve_for_me", tools=[], auto_tools=True,
    )
    out = await _stream(env.platform, body, ["once", "deny"])
    assert [c["tool"] for c in out["cards"]] == [KEPT, HELD], out["cards"]
    assert env.ran[KEPT] == [{}]
    assert env.ran[HELD] == [], "the sibling's answer ran the held-back tool"


# --------------------------------------------------------------------------- #
# PG1 — the POST lane (an API caller, the phone): it cannot card, so it refuses.
# --------------------------------------------------------------------------- #
async def test_post_lane_refuses_a_held_back_tool_its_app_switched_on(env):
    env.wire([_call(KEPT, "k1"), _call(HELD, "h1")])
    await run_chat_turn(env.platform, BUILTIN_PERSONAS, _body())
    await flush_interrupt_notes()
    assert env.ran[KEPT] == [{}], "control: the original tool runs as before"
    assert env.ran[HELD] == [], "the held-back tool ran with nobody asked"
    rows = _ledger(env.platform)
    assert (HELD, False) in rows
    with session_scope(env.platform.engine) as db:
        row = db.exec(select(ToolInvocation).where(ToolInvocation.tool == HELD)).first()
        assert "new in its app" in (row.output or ""), row.output


async def test_post_lane_runs_a_held_back_tool_the_user_allowed_for_the_conversation(env):
    env.wire([_call(HELD, "h1")])
    await run_chat_turn(env.platform, BUILTIN_PERSONAS, _body(granted_tools=[HELD]))
    assert env.ran[HELD] == [{}]


# --------------------------------------------------------------------------- #
# PG1 — the gate itself (defence in depth).
# --------------------------------------------------------------------------- #
def _engine(**base) -> PermissionEngine:
    return PermissionEngine(base or {"app_tool": "ask"}, ask_resolver=headless_ask_resolver())


def test_gate_an_allow_override_never_lifts_a_held_back_tool():
    e = _engine(app_tool="ask")
    d = e.authorize("app_tool", {}, {"app_tool": "allow"}, call_name="x__held", quarantined=True)
    assert d.allowed is False and d.mode is PermissionMode.ASK


def test_gate_an_allow_base_policy_never_lifts_a_held_back_tool():
    d = _engine(app_tool="allow").authorize("app_tool", {}, call_name="x__held", quarantined=True)
    assert d.allowed is False


def test_gate_an_armed_name_never_lifts_a_held_back_tool_when_card_answers_are_given():
    d = _engine().authorize(
        "app_tool", {}, None, {"x__held", "app_tool"},
        call_name="x__held", quarantined=True, card_allow=set(),
    )
    assert d.allowed is False


def test_gate_the_cards_own_answer_lifts_it_and_a_base_deny_still_denies():
    ok = _engine().authorize(
        "app_tool", {}, {"app_tool": "allow"}, set(),
        call_name="x__held", quarantined=True, card_allow={"x__held"},
    )
    assert ok.allowed is True and ok.reason == "granted for this task"
    no = _engine(app_tool="deny").authorize(
        "app_tool", {}, None, None, call_name="x__held", quarantined=True, card_allow={"x__held"},
    )
    assert no.allowed is False and no.mode is PermissionMode.DENY
    # A trusted tool is untouched: its override still allows it.
    plain = _engine().authorize("app_tool", {}, {"app_tool": "allow"}, call_name="x__kept")
    assert plain.allowed is True


def test_registry_takes_card_answers_apart_from_armed_names(env, tmp_path):
    """Through the REAL ``registry.invoke``: the armed names in
    ``session_allow`` lift nothing once the caller hands its card answers
    apart; the card's own answer runs the call."""
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = ToolContext(
        workspace=ws, session_id="s1", agent_run_id="r1", config=env.platform.config,
        event_bus=env.platform.event_bus, engine=env.platform.engine,
    )

    def run(**kw):
        return asyncio.run(env.platform.registry.invoke(
            HELD, {}, ctx, env.platform.permissions, {HELD: "allow", "mcp_call": "allow"}, **kw,
        ))

    assert run(session_allow={HELD, "mcp_call"}, card_allow=set()).ok is False
    assert env.ran[HELD] == []
    assert run(session_allow={HELD, "mcp_call"}, card_allow={HELD}).ok is True
    assert env.ran[HELD] == [{}]


# --------------------------------------------------------------------------- #
# PG7 — "Always allow exactly this" keeps a tool's own underscore argument.
# --------------------------------------------------------------------------- #
def test_an_exact_grant_for_one_record_never_covers_another(tmp_path):
    from iron_jarvis.core.grants import GrantStore, args_hash

    platform = build_platform(str(tmp_path / "home"))
    store = GrantStore(platform.engine, platform.event_bus)
    store.create("chat", "chat", KEPT, args_hash(KEPT, {"_id": "a"}), "label")
    scopes = [("chat", "chat")]
    assert store.match(scopes, KEPT, {"_id": "a"}) is not None, "control: the exact call matches"
    assert store.match(scopes, KEPT, {"_id": "b"}) is None, "a grant for _id a covered _id b"
    # Through the gate a standing grant is read by.
    e = PermissionEngine({"mcp_call": "ask"}, ask_resolver=headless_ask_resolver())
    other = e.authorize("mcp_call", {"_id": "b"}, grants=store, scopes=scopes, call_name=KEPT)
    assert other.allowed is False


def test_only_the_apps_own_control_keys_are_left_out_of_a_calls_identity():
    from iron_jarvis.core.grants import CONTROL_ARGS, args_hash

    assert CONTROL_ARGS == frozenset({"_store_as", "_isolate"})
    base = args_hash("shell", {"command": "git status"})
    # The lane / registry add these; the card and the gate must agree.
    assert args_hash("shell", {"command": "git status", "_store_as": "out"}) == base
    assert args_hash("shell", {"command": "git status", "_isolate": True}) == base
    # A tool's own underscore argument is part of the call.
    assert args_hash("t", {"_id": "a"}) != args_hash("t", {"_id": "b"})
    assert args_hash("t", {"_id": "a"}) != args_hash("t", {})


def test_a_grant_stored_under_the_old_rule_never_matches_more_than_before(tmp_path):
    """A row minted before this release hashed ``{"_id": "a"}`` as ``{}``.
    It now matches NO call with an ``_id`` (asking again is the safe side),
    and still matches what it matched before for a call with none."""
    import hashlib

    from iron_jarvis.core.grants import GrantStore

    old = hashlib.sha256((KEPT + "\n" + "{}").encode("utf-8")).hexdigest()
    platform = build_platform(str(tmp_path / "home"))
    store = GrantStore(platform.engine, platform.event_bus)
    store.create("chat", "chat", KEPT, old, "label")
    scopes = [("chat", "chat")]
    assert store.match(scopes, KEPT, {"_id": "a"}) is None
    assert store.match(scopes, KEPT, {"_id": "b"}) is None
    assert store.match(scopes, KEPT, {}) is not None


# --------------------------------------------------------------------------- #
# PG5 — web search / web fetch: Allow or Never, said in words.
# --------------------------------------------------------------------------- #
def _writer(tmp_path):
    from iron_jarvis.daemon.routes.settings import config_writer

    platform = build_platform(str(tmp_path / "home"))
    return platform, config_writer(SimpleNamespace(platform=platform))


@pytest.mark.parametrize("tool", ["web_search", "web_fetch"])
def test_settings_refuses_ask_first_for_the_web_readers_in_words(tmp_path, tool):
    from iron_jarvis.settings.writer import SettingError
    from iron_jarvis.settings.schema import WEB_READERS_ASK_REFUSAL

    platform, w = _writer(tmp_path)
    with pytest.raises(SettingError) as exc:
        w.apply({f"permissions.{tool}": "ask"}, actor="chat", journal=False)
    assert str(exc.value) == WEB_READERS_ASK_REFUSAL
    assert "Never" in str(exc.value) and "without asking" in str(exc.value)
    assert platform.config.permissions.get(tool) != "ask"
    # Never and Allow are both accepted.
    w.apply({f"permissions.{tool}": "deny"}, actor="chat", journal=False)
    assert platform.permissions.mode_for(tool) is PermissionMode.DENY
    w.apply({f"permissions.{tool}": "allow"}, actor="chat", journal=False)
    assert platform.permissions.mode_for(tool) is PermissionMode.ALLOW


@pytest.mark.parametrize("tool", ["web_search", "web_fetch"])
def test_the_choices_for_the_web_readers_hold_no_ask(tool):
    from iron_jarvis.settings import schema
    from iron_jarvis.tools.permissions import READ_ONLY_WEB_TOOLS

    assert frozenset(schema.WEB_READER_TOOLS) == READ_ONLY_WEB_TOOLS
    d = schema.get(f"permissions.{tool}")
    assert [v for v, _ in d.options] == ["allow", "deny"]
    assert "without asking" in d.help and "Never" in d.help
    assert schema.pattern_arg(d, f"permissions.{tool}") == tool
    # Every other tool keeps all three.
    assert [v for v, _ in schema.get("permissions.write_file").options] == ["allow", "ask", "deny"]


@pytest.mark.parametrize("tool", ["web_search", "web_fetch"])
def test_a_stored_ask_from_an_older_version_runs_and_reads_as_allow(tmp_path, tool):
    platform, w = _writer(tmp_path)
    platform.config.permissions[tool] = "ask"
    platform.permissions._base[tool] = "ask"
    assert w.current(f"permissions.{tool}") == "allow"
    assert platform.permissions.mode_for(tool) is PermissionMode.ALLOW
    # Another tool's stored "ask" is shown as it is.
    platform.config.permissions["write_file"] = "ask"
    assert w.current("permissions.write_file") == "ask"


def test_a_chat_request_for_ask_first_gets_the_same_sentence(tmp_path):
    """Chat's settings tool (the protected tier a tool's approval takes) says
    the writer's sentence, for "ask" and for the option's old words."""
    from iron_jarvis.settings.schema import WEB_READERS_ASK_REFUSAL
    from iron_jarvis.settings.tools import ConfigChangeProtectedTool

    platform, w = _writer(tmp_path)
    platform.config_writer = w
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = ToolContext(
        workspace=ws, session_id="s1", agent_run_id="r1", config=platform.config,
        event_bus=platform.event_bus, engine=platform.engine,
    )
    tool = ConfigChangeProtectedTool(platform)
    for value in ("ask", "Ask first"):
        res = asyncio.run(tool.execute({"key": "permissions.web_search", "value": value}, ctx))
        assert res.ok is False and res.error == WEB_READERS_ASK_REFUSAL, res.error
    assert platform.config.permissions.get("web_search") in (None, "allow")


# --------------------------------------------------------------------------- #
# PG10 — the refusal when nobody can be asked.
# --------------------------------------------------------------------------- #
def test_the_nobody_to_ask_refusal_is_plain_and_names_no_engine_words():
    reason = _engine(write_file="ask").authorize("write_file", {}).reason
    # The marker other readers match on (agents/fix_request, detections/ledger).
    assert "needs approval and nothing here could ask" in reason
    for word in ("allow_tools", "permissions.", "resolver", "headless"):
        assert word not in reason, reason
    assert "Settings" in reason


@pytest.mark.parametrize("tool", sorted(DENY_FLOOR_TOOLS))
def test_a_floor_tool_refusal_never_advises_a_setting_settings_refuses(tool):
    reason = _engine(**{tool: "ask"}).authorize(tool, {}).reason
    assert "needs approval and nothing here could ask" in reason
    assert "Settings" not in reason and "allow_tools" not in reason, reason
