"""v1.352.0 (product-readiness wave 2, lane 2B) — one set of approval words,
a bell ask that says what and why, and whether the action can be undone.

What must hold, each through the REAL code (the real app factory, the real
stream lane, the real runtime pause, the real pending listing):

* G25 — every ask carries the tool's undo verdict. The stream lane's
  ``approval`` frame and the runtime's ``approval.requested`` payload carry
  ``reversible: true`` for a tool the undo journal can put back (a file
  save), ``false`` for one it cannot (a command, a sent message) and leave the
  key OUT for a read-only tool; ``GET /chat/approvals/pending`` copies it
  key-by-key for the bell.
* G24 — the listing names the asking RUN: ``task`` (the first line of its
  task, masked, cut) and ``origin`` ("schedule:nightly-brief"), on every row,
  from the same Session read — never the call's arguments.
* G24 — Settings shows the chat chip's own three level names: the schema's
  ``device.approval_mode`` labels equal ``PERMISSION_LEVELS`` in
  ``dashboard/lib/permissionLevels.ts``, and "Don't ask" typed in chat finds
  ``yolo``.
* G24 — the browser sidebar's wide answer says what it does: "Allow all page
  actions" (it turns Auto-allow on for EVERY tab), never "Always allow"
  (which in the chat means ONE exact call).
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from iron_jarvis.agents.runtime import AgentRuntime
from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import AgentType
from iron_jarvis.core.models import Session as SessionRow
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import chat as chat_routes
from iron_jarvis.settings import schema
from iron_jarvis.settings.tools import _coerce  # type: ignore[attr-defined]
from iron_jarvis.tools.base import Reversibility, Tool
from iron_jarvis.tools.reversible import reversible_flag
from tests.test_approval_modes_v1188 import _body, _calls_stream
from tests.test_chat_approvals_v1187 import _ASK_MSG, _asgi_post, _drive_stream, _shell_call_stream

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    # CRLF-normalised: CI checks out with autocrlf (the v1.232.1 rule).
    return (ROOT / rel).read_text(encoding="utf-8").replace("\r\n", "\n")


# --------------------------------------------------------------------------- #
# The one helper both lanes read.
# --------------------------------------------------------------------------- #


class _Declared(Tool):
    name = "declared_probe"

    async def execute(self, args, ctx):  # pragma: no cover - never run
        raise AssertionError("a probe for its declared attributes only")


def test_the_verdict_follows_the_tools_own_undo_contract():
    rev, irr, ro = _Declared(), _Declared(), _Declared()
    rev.reversibility = Reversibility.REVERSIBLE
    irr.reversibility = Reversibility.IRREVERSIBLE
    ro.reversibility = Reversibility.READONLY
    assert reversible_flag(rev) is True
    assert reversible_flag(irr) is False
    # A read changes nothing: no key, so the card says nothing about undo.
    assert reversible_flag(ro) is None
    # No tool (the daemon's own plan ask): no key.
    assert reversible_flag(None) is None
    # A tool that never declared itself is the fail-safe IRREVERSIBLE: "may
    # not be undoable", never a promise.
    assert reversible_flag(_Declared()) is False
    # A garbled value is wording only: no line, never a wrong promise.
    odd = _Declared()
    odd.reversibility = "sideways"  # type: ignore[assignment]
    assert reversible_flag(odd) is None


def test_the_real_registry_answers_true_for_a_file_save_and_false_for_a_send(tmp_path):
    platform = create_app(str(tmp_path)).state.platform
    reg = platform.registry
    assert reversible_flag(reg.get("write_file")) is True  # the undo journal keeps its pre-image
    assert reversible_flag(reg.get("notify")) is False  # a message that left cannot come back
    assert reversible_flag(reg.get("shell")) is False


# --------------------------------------------------------------------------- #
# G25 — the stream lane's approval frame (the chat card).
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_file_save_card_says_it_can_be_undone(tmp_path):
    app = create_app(str(tmp_path))
    app.state.platform.router.stream = _calls_stream(
        [("write_file", {"path": "note.md", "content": "# hi"})]
    )
    seen: list[dict] = []

    async def approve(data):
        seen.append(data)
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "once"})
        assert status == 200

    await _drive_stream(
        app, _body("write a note file for me", "always_ask", tools=["write_file"]), approve
    )
    assert [d["tool"] for d in seen] == ["write_file"], "anti-vacuity: the card must come"
    assert seen[0]["reversible"] is True


@pytest.mark.asyncio
async def test_a_command_card_says_it_may_not_be_undoable(tmp_path):
    app = create_app(str(tmp_path))
    app.state.platform.router.stream = _shell_call_stream()
    seen: list[dict] = []

    async def deny(data):
        seen.append(data)
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "deny"})
        assert status == 200

    await _drive_stream(
        app, {"messages": [{"role": "user", "content": _ASK_MSG}], "auto_tools": True}, deny
    )
    assert [d["tool"] for d in seen] == ["shell"]
    assert seen[0]["reversible"] is False


def test_the_stream_frame_reads_the_one_helper():
    """MIRROR NOTE (lock-step): the frame takes the verdict from
    `tools/reversible.reversible_flag`, the runtime's payload from the same."""
    chat = _read("src/iron_jarvis/daemon/routes/chat.py")
    runtime = _read("src/iron_jarvis/agents/runtime.py")
    assert "_rev = _reversible_flag(_t)" in chat and '_frame["reversible"] = _rev' in chat
    assert "_rev = reversible_flag(tool)" in runtime and 'requested["reversible"] = _rev' in runtime


# --------------------------------------------------------------------------- #
# G24 + G25 — the runtime ask and the bell's listing.
# --------------------------------------------------------------------------- #


@pytest.fixture
def rt(tmp_path):
    app = create_app(str(tmp_path))
    platform = app.state.platform
    published: list[dict] = []
    real_publish = platform.event_bus.publish

    async def spy(type, payload=None, session_id=None, **kw):
        published.append({
            "type": str(getattr(type, "value", type)),
            "payload": payload or {},
            "session_id": session_id,
        })
        return await real_publish(type, payload, session_id=session_id, **kw)

    platform.event_bus.publish = spy
    return SimpleNamespace(app=app, platform=platform, runtime=AgentRuntime(platform), published=published)


def _asks(rt, session_id: str) -> list[dict]:
    return [
        p["payload"] for p in rt.published
        if p["type"] == str(EventType.APPROVAL_REQUESTED) and p["session_id"] == session_id
    ]


async def _wait_for(pred, tries: int = 400) -> bool:
    for _ in range(tries):
        if pred():
            return True
        await asyncio.sleep(0.01)
    return False


async def _listing(app) -> list[dict]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
        r = await c.get("/chat/approvals/pending")
    assert r.status_code == 200, r.text
    return r.json()["approvals"]


def _row(platform, sid: str, origin: str, task: str) -> None:
    with session_scope(platform.engine) as db:
        db.add(SessionRow(id=sid, origin=origin, task=task))
        db.commit()


async def _ask_and_list(rt, sid: str, origin: str, tool: str, args: dict) -> tuple[dict, list[dict]]:
    """Pause a run on `tool` through the REAL runtime, read the bell's listing
    while it waits, then answer it."""
    sess = SimpleNamespace(
        id=sid, origin=origin, approval_mode="", agent_name="", project_id=None, trust="full",
    )
    tc = SimpleNamespace(name=tool, arguments=dict(args))
    agent_def = get_agent_definition(AgentType.BUILDER)
    task = asyncio.create_task(rt.runtime._pause_for_approval(sess, tc, agent_def, set()))
    try:
        assert await _wait_for(lambda: bool(_asks(rt, sid))), "the run never asked"
        # v1.384.3: wait for the ask to be LISTED, not only published. The
        # spy records the ask before the real publish writes the event row
        # the listing reads (the v1.373.1 shape), so one read could come back
        # empty on a loaded runner (v1.384.2's Release gate: IndexError).
        rows: list[dict] = []
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 15.0
        while True:
            rows = [r for r in await _listing(rt.app) if r.get("session_id") == sid]
            if rows or loop.time() > deadline:
                break
            await asyncio.sleep(0.05)
        payload = _asks(rt, sid)[-1]
        assert rt.platform.approvals.resolve(payload["approval_id"], "once")
        await asyncio.wait_for(task, 10)
    finally:
        if not task.done():
            task.cancel()
    return payload, rows


@pytest.mark.asyncio
async def test_a_schedules_ask_names_the_run_its_task_and_that_it_can_be_undone(rt):
    """THE ACCEPTANCE (daemon half): a background schedule asks to save a file
    — the bell's listing says whose run it is (`origin`), what it was for (the
    task's first line) and that the save can be undone; never the file's
    content (the call's arguments)."""
    rt.platform.permissions._base["write_file"] = "ask"  # the user's own "ask first" for saves
    _row(rt.platform, "session_nb", "schedule:nightly-brief",
         "Write the nightly brief\nUse yesterday's notes and the calendar.")
    secret = "SECRET-BRIEF-BODY-2026"
    payload, rows = await _ask_and_list(
        rt, "session_nb", "schedule:nightly-brief", "write_file",
        {"path": "brief.md", "content": secret},
    )
    assert payload["reversible"] is True
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["tool"] == "write_file"
    assert row["origin"] == "schedule:nightly-brief"
    assert row["task"] == "Write the nightly brief"
    assert row["reversible"] is True
    assert secret not in json.dumps(rows), "the call's arguments never reach the listing"


@pytest.mark.asyncio
async def test_a_send_says_it_may_not_be_undoable_and_a_long_masked_task_is_cut(rt):
    task_line = "Email the client their summary " + "x" * 300 + " token=sk-ant-REALLOOKINGKEY123456789"
    _row(rt.platform, "session_mail", "goal:g1", f"\n\n  {task_line}\nsecond line")
    payload, rows = await _ask_and_list(
        rt, "session_mail", "goal:g1", "notify", {"message": "your summary is ready"},
    )
    assert payload["reversible"] is False
    row = rows[0]
    assert row["reversible"] is False
    assert row["origin"] == "goal:g1"
    # The first NON-BLANK line, folded and cut to the bell's one line.
    assert row["task"].startswith("Email the client their summary x")
    assert len(row["task"]) == chat_routes._ASK_TASK_CHARS and row["task"].endswith("…")


@pytest.mark.asyncio
async def test_a_plan_ask_carries_no_undo_verdict_and_a_missing_row_reads_null(rt):
    """The plan approval has no tool behind it (no undo line), and a session
    the database never saw lists `task`/`origin` as null — every row has both
    keys, like `mission_id`/`project_id`."""
    from iron_jarvis.agents.runtime import PLAN_APPROVAL_TOOL

    payload, rows = await _ask_and_list(
        rt, "session_ghost", "chat", PLAN_APPROVAL_TOOL, {"steps": ["a", "b"]},
    )
    assert "reversible" not in payload
    row = rows[0]
    assert "reversible" not in row
    assert row["task"] is None and row["origin"] is None


def test_the_task_line_helper_masks_folds_and_never_raises():
    assert chat_routes._ask_task_line("") is None
    assert chat_routes._ask_task_line(None) is None
    assert chat_routes._ask_task_line("  \n\t\n  Close   the books \nnext") == "Close the books"
    masked = chat_routes._ask_task_line("deploy with Authorization: Bearer abcdefghijklmnopqrstuvwxyz123")
    assert "abcdefghijklmnopqrstuvwxyz123" not in (masked or "")


# --------------------------------------------------------------------------- #
# G24 — the same three level names in Settings.
# --------------------------------------------------------------------------- #


def test_settings_names_the_levels_exactly_as_the_chat_chip_does():
    ts = _read("dashboard/lib/permissionLevels.ts")
    chip = {
        key: re.search(rf"{key}: \{{\s*label: \"([^\"]+)\"", ts).group(1)  # type: ignore[union-attr]
        for key in ("always_ask", "approve_for_me", "accept_edits", "auto", "yolo")
    }
    # v1.406.0 part 2: five levels, renamed for the one switch.
    assert chip == {
        "always_ask": "Ask before every action", "approve_for_me": "Ask first",
        "accept_edits": "Accept edits", "auto": "Auto", "yolo": "Don't ask",
    }
    row = schema.get("device.approval_mode")
    assert dict(row.options) == chip
    # Order = the chip's menu: strictest first.
    assert [v for v, _ in row.options] == ["always_ask", "approve_for_me", "accept_edits", "auto", "yolo"]
    # The chat's settings tools match a label as well as a value.
    assert _coerce(row, "Don't ask") == "yolo"
    assert _coerce(row, "ask when risky") == "approve_for_me"
    # The floor still keys on the VALUE.
    assert row.tier_for("yolo") == "ask-floor"
    # v1.406.0 part 2: Auto lets calls through with no card, so turning it on
    # from chat asks first too; Accept edits does not.
    assert row.tier_for("auto") == "ask-floor"
    assert row.tier_for("accept_edits") == "allow"
    # "Ask first" now names the default; the old words keep their level.
    assert _coerce(row, "Ask first") == "approve_for_me"
    assert _coerce(row, "Ask before every action") == "always_ask"


def test_agent_setup_reads_the_same_level_names():
    card = _read("dashboard/components/agents/SetupCard.tsx")
    block = card[card.index("const APPROVAL_OPTIONS"):card.index("];", card.index("const APPROVAL_OPTIONS"))]
    assert "PERMISSION_LEVELS.always_ask.label" in block
    assert "PERMISSION_LEVELS.approve_for_me.label" in block
    assert "PERMISSION_LEVELS.yolo.label" in block and "disabled: true" in block
    for old in ("Ask me as usual", "Approve for me", "Always ask me"):
        assert old not in card, old


# --------------------------------------------------------------------------- #
# G24 — the browser sidebar's wide answer says what it does.
# --------------------------------------------------------------------------- #


def test_the_sidebars_wide_answer_names_every_tab():
    html = _read("extensions/chrome/src/sidepanel/sidepanel.html")
    button = re.search(r'<button id="approve-always"[^>]*title="([^"]*)"[^>]*>([^<]*)<', html)
    assert button, "the sidebar's wide answer is gone"
    title, label = button.group(1), button.group(2).strip()
    assert label == "Allow all page actions"
    assert "every tab" in title and "Payments, passwords" in title
    assert ">Always allow<" not in html
