"""Third north-star fix plan, group 20 ("what the screen says is what
happens"), a coding session's own settings: RB3 (decision G14), RB4 and RB14
(decision G22).

- RB3: a STRICTER permission level (or Plan) set while a reply runs applies
  to that reply from its next step; a LOOSER one applies from the user's next
  message, never to the running reply. The PATCH answers which (``applies``:
  ``"now"`` / ``"next_reply"``), and nothing when no reply runs.
- RB4: "Branch from here" and the hold that keeps a session Local only after
  it read an app kept on the user's own models — the one writer of that hold
  (``mcp/tools._hold_chat_local``, after a successful call of such an app)
  never runs in a coding session: the app's own tool refuses there first. So
  no coding session holds one, and the branch keeps Local only as its parent
  has it (the switch, which it always copied).
- RB14: the session's reply limit goes with a branch made from it.

Driven through the REAL app factory, the real build routes, the real stream
lane and the real registry ``invoke``; only the model is a scripted double
(commands really run where they are allowed). Nothing here reaches a real
model or service, and no value is shaped like a real credential.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as build_routes
from iron_jarvis.harness import sessions as S
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_build_sessions_v1367 import (
    Script,
    asgi,
    new_session,
    open_turn,
    setup_of,
    turn_frames,
    turn_log,
    until,
    wire,
)
from tests.test_chat_turn_stop_v1241 import _asgi_post


@pytest.fixture()
def app(tmp_path):
    a = create_app(str(tmp_path / "home"))
    a.state.platform.config.chat_files_root = str(tmp_path / "chat-files")
    return a


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_bytes(b"x = 1\n")
    return folder


#: A model on this PC (Local only allows it).
LOCAL = ("ollama", "llama3.1")


def bash(call_id: str, command: str) -> list[ToolCall]:
    return [ToolCall(id=call_id, name="Bash", arguments={"command": command})]


def rows(app, tool: str) -> list[ToolInvocation]:
    with session_scope(app.state.platform.engine) as db:
        out = [r for r in db.exec(select(ToolInvocation).order_by(ToolInvocation.created_at)) if r.tool == tool]
        for r in out:
            db.expunge(r)
        return out


def ran(app, word: str) -> bool:
    """Whether a command naming ``word`` RAN and succeeded (its own output)."""
    return any(r.ok and word in (r.output or "") for r in rows(app, "shell"))


def cards_of(app, turn_id: str) -> list[dict]:
    return [d for e, d in turn_frames(app, turn_id) if e == "approval"]


async def start(app, sid: str, turn_id: str, text: str = "go"):
    drop = asyncio.Event()
    task = asyncio.create_task(open_turn(app, sid, {"text": text, "turn_id": turn_id}, drop=drop, timeout=60))
    return drop, task


async def card_or_end(app, turn_id: str, n: int) -> "dict | None":
    """Card ``n`` of the reply, or None once the reply ended without it."""
    def seen():
        got = cards_of(app, turn_id)[n:n + 1]
        if got:
            return got
        lg = turn_log(app, turn_id)
        return ["ended"] if lg is not None and lg.status != "running" else None

    got = await until(seen, 30)
    return got[0] if got and got[0] != "ended" else None


async def answer(app, card: dict, decision: str) -> None:
    status, out = await _asgi_post(app, f"/chat/approvals/{card['id']}", {"decision": decision})
    assert status == 200, out


async def finish(app, turn_id: str, drop, task) -> None:
    lg = await until(lambda: turn_log(app, turn_id))
    assert lg is not None, "the reply never started"
    await until(lambda: lg.status != "running", 60)
    drop.set()
    await task


def patch_between_rounds(app, sid: str, body: dict, then: list, seen: dict):
    """A model move: the user changes the session WHILE the model thinks about
    its next step (the reply is running), then the model asks for ``then``."""
    async def move(_script):
        seen["patch"] = await asgi(app, "PATCH", f"/build/sessions/{sid}", body)
        return then

    return move


# --------------------------------------------------------------------------- #
# RB3 (decision G14): a stricter level applies from the next step
# --------------------------------------------------------------------------- #


async def test_a_stricter_level_set_while_a_reply_runs_asks_from_its_next_step(app, work):
    """"Don't ask" -> "Ask before every action" mid-reply: the next command
    pauses on a card (it used to run unasked until the reply ended)."""
    sid = new_session(app, work, approval_mode="yolo")["id"]
    seen: dict = {}
    wire(app, Script([
        bash("c1", "echo IJ-ONE"),
        patch_between_rounds(app, sid, {"approval_mode": "always_ask"}, bash("c2", "echo IJ-TWO"), seen),
        "Done.",
    ]))
    drop, task = await start(app, sid, "lv-strict")
    card = await card_or_end(app, "lv-strict", 0)
    assert card is not None, "the stricter level did not reach the running reply: its command ran unasked"
    assert card["tool"] == "shell"
    await answer(app, card, "deny")
    await finish(app, "lv-strict", drop, task)
    assert ran(app, "IJ-ONE"), "the step before the change ran at the old level"
    assert not ran(app, "IJ-TWO"), "the user's No was kept"
    status, view = seen["patch"]
    assert status == 200, view
    assert view["applies"] == "now", view.get("applies")
    assert setup_of(app, sid)["approval_mode"] == "always_ask"


async def test_a_looser_level_set_while_a_reply_runs_applies_from_the_next_message(app, work):
    """"Ask before every action" -> "Don't ask" mid-reply: the running reply
    still asks; the user's NEXT message runs at "Don't ask"."""
    sid = new_session(app, work, approval_mode="always_ask")["id"]
    seen: dict = {}
    wire(app, Script([
        bash("c1", "echo IJ-ONE"),
        patch_between_rounds(app, sid, {"approval_mode": "yolo"}, bash("c2", "echo IJ-TWO"), seen),
        "Done.",
    ]))
    drop, task = await start(app, sid, "lv-loose")
    first = await card_or_end(app, "lv-loose", 0)
    assert first is not None and first["tool"] == "shell"
    await answer(app, first, "once")
    second = await card_or_end(app, "lv-loose", 1)
    assert second is not None, "a looser level loosened the running reply"
    await answer(app, second, "deny")
    await finish(app, "lv-loose", drop, task)
    assert ran(app, "IJ-ONE") and not ran(app, "IJ-TWO")
    status, view = seen["patch"]
    assert status == 200 and view["applies"] == "next_reply", view

    # The next message runs at the level the user chose.
    wire(app, Script([bash("c3", "echo IJ-THREE"), "Done."]))
    drop, task = await start(app, sid, "lv-next")
    assert await card_or_end(app, "lv-next", 0) is None, "the next message still asked"
    await finish(app, "lv-next", drop, task)
    assert ran(app, "IJ-THREE")


async def test_plan_switched_on_while_a_reply_runs_stops_its_changes_from_the_next_step(app, work):
    """Plan is the strictest: from the next step the running reply changes
    nothing (no command, no file written; no card for a call that cannot
    run), and reading goes on."""
    sid = new_session(app, work, approval_mode="yolo")["id"]
    seen: dict = {}
    wire(app, Script([
        bash("c1", "echo IJ-ONE"),
        patch_between_rounds(app, sid, {"mode": "plan"}, [
            ToolCall(id="c2", name="Bash", arguments={"command": "echo IJ-TWO"}),
            ToolCall(id="c3", name="Write", arguments={"file_path": "made.txt", "content": "new"}),
            ToolCall(id="c4", name="Read", arguments={"file_path": str(work / "app.py")}),
        ], seen),
        "Done.",
    ]))
    drop, task = await start(app, sid, "lv-plan")
    await finish(app, "lv-plan", drop, task)
    assert cards_of(app, "lv-plan") == [], "no card for a call Plan does not run"
    assert ran(app, "IJ-ONE")
    assert not ran(app, "IJ-TWO"), "a command ran after Plan was switched on"
    assert not (work / "made.txt").exists(), "a file was written after Plan was switched on"
    refused = [r for r in rows(app, "shell") if "IJ-TWO" in str(r.args_json or "")]
    assert refused and refused[-1].ok is False
    assert build_routes.PLAN_NOW_REFUSAL in (refused[-1].output or ""), refused[-1].output
    assert any(r.ok for r in rows(app, "read_file")), "reading goes on in Plan"
    status, view = seen["patch"]
    assert status == 200 and view["applies"] == "now", view
    assert view["mode"] == S.MODE_PLAN


async def test_the_patch_says_nothing_about_when_while_no_reply_runs(app, work):
    sid = new_session(app, work, approval_mode="yolo")["id"]
    status, view = await asgi(app, "PATCH", f"/build/sessions/{sid}", {"approval_mode": "always_ask"})
    assert status == 200 and "applies" not in view, view
    status, view = await asgi(app, "PATCH", f"/build/sessions/{sid}", {"mode": "plan"})
    assert status == 200 and "applies" not in view, view


async def test_local_only_changed_while_a_reply_runs_says_it_applies_from_the_next_message(app, work):
    """Local only is read once when a reply starts: switched on (or off)
    while one runs, the PATCH says it applies from the next message — in its
    own key, beside the level's answer when one PATCH changes both."""
    sid = new_session(app, work, approval_mode="yolo")["id"]
    seen: dict = {}
    wire(app, Script([
        bash("c1", "echo IJ-ONE"),
        patch_between_rounds(app, sid, {"local_only": True, "approval_mode": "always_ask"}, "Done.", seen),
    ]))
    drop, task = await start(app, sid, "lo-1")
    card = await card_or_end(app, "lo-1", 0)
    if card is not None:
        await answer(app, card, "deny")
    await finish(app, "lo-1", drop, task)
    status, view = seen["patch"]
    assert status == 200, view
    assert view.get("applies_local_only") == "next_reply", view.get("applies_local_only")
    assert view["applies"] == "now", "the level's own answer is kept"
    assert setup_of(app, sid)["local_only"] is True
    # Off while a reply runs (a session on a model of this PC): the same answer.
    sid = new_session(app, work, approval_mode="yolo", provider=LOCAL[0], model=LOCAL[1], local_only=True)["id"]
    seen.clear()
    wire(app, Script([
        bash("c2", "echo IJ-TWO"),
        patch_between_rounds(app, sid, {"local_only": False}, "Done.", seen),
    ], who=LOCAL))
    drop, task = await start(app, sid, "lo-2")
    card = await card_or_end(app, "lo-2", 0)
    if card is not None:
        await answer(app, card, "deny")
    await finish(app, "lo-2", drop, task)
    status, view = seen["patch"]
    assert status == 200 and view.get("applies_local_only") == "next_reply", view
    assert "applies" not in view
    # No reply running: no such key.
    status, view = await asgi(app, "PATCH", f"/build/sessions/{sid}", {"local_only": True})
    assert status == 200 and "applies_local_only" not in view, view


@pytest.mark.parametrize("old,new,plan_old,plan_new,want", [
    ("yolo", "always_ask", False, False, "now"),
    ("approve_for_me", "always_ask", False, False, "now"),
    ("auto", "accept_edits", False, False, "now"),
    ("", "always_ask", False, False, "now"),  # "" is the default, Ask before commands
    ("always_ask", "yolo", False, False, "next_reply"),
    ("accept_edits", "auto", False, False, "next_reply"),
    ("yolo", "yolo", False, False, ""),
    ("yolo", "yolo", False, True, "now"),
    ("always_ask", "always_ask", True, False, "next_reply"),
    # Leaving Plan for a stricter level: the reply stays in Plan until the
    # next message (leaving Plan lets it change things).
    ("yolo", "always_ask", True, False, "next_reply"),
])
def test_which_way_a_change_applies(old, new, plan_old, plan_new, want):
    before = {"approval_mode": old, **({"mode": "plan"} if plan_old else {})}
    after = {"approval_mode": new, **({"mode": "plan"} if plan_new else {})}
    assert build_routes.level_applies(before, after) == want


def test_a_level_the_user_did_not_change_never_tightens_a_reply_that_runs_its_own():
    """A reply may run with a level of its own (the night's, an unattended
    start's "Ask before commands"): only a level the user SET since it
    started is taken, and never a looser one."""
    watch = build_routes.LevelWatch(engine=None, thread_id="t", level="always_ask", plan=False)
    assert watch.tighten("approve_for_me", False, ("always_ask", False)) == ("approve_for_me", False)
    assert watch.tighten("approve_for_me", False, ("yolo", False)) == ("approve_for_me", False)
    watch = build_routes.LevelWatch(engine=None, thread_id="t", level="yolo", plan=False)
    assert watch.tighten("yolo", False, ("always_ask", False)) == ("always_ask", False)
    assert watch.tighten("yolo", False, ("yolo", True)) == ("yolo", True)
    assert watch.tighten("yolo", False, None) == ("yolo", False), "unread: the reply keeps its level"
    # A reply that started in Plan is in Plan already: nothing flips.
    watch = build_routes.LevelWatch(engine=None, thread_id="t", level="yolo", plan=True)
    assert watch.tighten("yolo", False, ("yolo", True)) == ("yolo", False)


# --------------------------------------------------------------------------- #
# RB4: nothing writes a Local only hold on a coding session
# --------------------------------------------------------------------------- #


async def test_the_one_writer_of_a_local_only_hold_never_runs_in_a_coding_session(app, work):
    """The hold is written only after a SUCCESSFUL call of an app kept on the
    user's own models (``mcp/tools._hold_chat_local``). In a coding session
    that app's own tool refuses before it reaches the app, so no hold is ever
    written there — RB4 is not a defect today."""
    from iron_jarvis.mcp.tools import CHAT_ONLY_APP_REFUSAL, MCPRemoteTool
    from iron_jarvis.providers import egress
    from iron_jarvis.providers import failure_words as fw
    from iron_jarvis.tools.base import ToolContext

    class _Client:
        calls: list[str] = []

        async def call_tool(self, name, args):
            self.calls.append(name)
            return {"content": [{"type": "text", "text": "answered"}]}

    client = _Client()
    tool = MCPRemoteTool.from_spec(client, "mailbox", {
        "name": "list_mail", "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    })
    tool.locked = True
    tool.local_only = True
    sid = new_session(app, work, local_only=True)["id"]
    p = app.state.platform
    ctx = ToolContext(workspace=work, session_id=S.session_id_for(sid), agent_run_id="", config=p.config,
                      event_bus=p.event_bus, engine=p.engine, thread_id=sid, turn_id="hold-1")
    token = fw.set_build_surface()
    try:
        result = await tool.execute({}, ctx)
    finally:
        fw.reset_surface(token)
    assert result.ok is False and result.error == CHAT_ONLY_APP_REFUSAL.format(app="mailbox")
    assert client.calls == [], "the app was reached from a coding session"
    assert egress.HOLDS_KEY not in setup_of(app, sid), "a coding session got a Local only hold"


async def test_a_branch_keeps_local_only_as_its_parent_has_it(app, work):
    sid = new_session(app, work, local_only=True)["id"]
    _two_turns(app, sid)
    status, view = await asgi(app, "POST", f"/build/sessions/{sid}/branch", {"turn_id": "t1", "at": "reply"})
    assert status == 201, view
    assert view["setup"]["local_only"] is True
    assert setup_of(app, view["id"])["local_only"] is True


# --------------------------------------------------------------------------- #
# RB14 (decision G22): the reply limit goes with the branch
# --------------------------------------------------------------------------- #


def _two_turns(app, sid: str) -> None:
    for turn_id, question in (("t1", "first"), ("t2", "second")):
        S.append_lines(app.state.platform.engine, sid, [{"role": "user", "content": question, "turnId": turn_id}])
        S.append_lines(app.state.platform.engine, sid, [{"role": "assistant", "content": f"answer to {question}",
                                                         "turnId": turn_id}])


async def test_a_branch_keeps_the_sessions_reply_limit(app, work):
    sid = new_session(app, work)["id"]
    status, out = await asgi(app, "PATCH", f"/build/sessions/{sid}", {"cap": {"minutes": 30, "usd": 2.5}})
    assert status == 200, out
    want = setup_of(app, sid)["cap"]
    assert want
    _two_turns(app, sid)
    status, view = await asgi(app, "POST", f"/build/sessions/{sid}/branch", {"turn_id": "t1", "at": "reply"})
    assert status == 201, view
    assert setup_of(app, view["id"]).get("cap") == want, "the reply limit did not go with the branch"
    assert view["setup"].get("cap") == want
