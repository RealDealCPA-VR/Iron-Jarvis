"""v1.352.0 integrate — Wave 2's seams, each joined ONCE through real code.

The four lanes pinned their own halves against shapes they wrote by hand.
This file joins them to the daemon that really sends the data:

* THE CONTRACT FIXTURE — ``dashboard/__tests__/fixtures/receipt-seams-v13520.json``
  holds what the REAL daemon sends; ``dashboard/__tests__/receipt-seams-
  v13520.test.tsx`` feeds the same file through the real dashboard components.
  A change to either side's shape fails one of the two.

  - ``receipts``: wave 1 (G18) put ``provider`` / ``model`` / ``agent_name`` /
    ``route_note`` / ``on_this_network`` / ``activity`` on ``GET
    /sessions/{id}/result``; lane 2A renders them on the helper agent's card
    (RunResultCard) and settled row (lib/agentRun stamp + AgentRunReceipt).
    Two real runs (a cloud model, a local one) read through the real route,
    and the line the daemon APPENDED to the chat they were asked in (the
    orchestrator's own delivery, which since this release hands the provider
    manager through, so the line's card says where it ran too).
  - ``asks``: lane 2B's one approval vocabulary. ONE real ask (a file save)
    as every surface receives it: the stream lane's ``approval`` frame (the
    chat card, the dock, a Build pane), the runtime's ``approval.requested``
    payload (a project's paused task, the chat's dock for an escalated run),
    the bell's ``GET /chat/approvals/pending`` row, the session row's
    ``waiting_on`` (the session page) and the mission view's coordinator
    ``waiting_on`` (the mission card). Each carries the same undo verdict.

* ONE ASK-WORDS TABLE: ``agents/mission.ASK_WORDS`` (the activity log's "needed
  your OK to save a file") is pinned EQUAL to ``dashboard/lib/toolWords.ts``.
* The retired approval level names typed in chat ("approve for me",
  "auto-approve") still mean the same level.

Regenerate the fixture after an INTENDED shape change:
``IJ_REGEN_RECEIPT_SEAMS=1 uv run pytest tests/test_receipt_seams_v13520.py``
and commit it.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from iron_jarvis.agents import mission
from iron_jarvis.agents.runtime import AgentRuntime
from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import AgentType, ChatThreadRecord
from iron_jarvis.core.models import Session as SessionRow
from iron_jarvis.daemon.app import create_app
from iron_jarvis.settings import schema
from iron_jarvis.settings.tools import _coerce  # type: ignore[attr-defined]
from tests.test_approval_modes_v1188 import _body, _calls_stream
from tests.test_chat_approvals_v1187 import _asgi_post, _drive_stream
from tests.test_session_receipt_v13510 import _seed_delegated_run

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "dashboard" / "__tests__" / "fixtures" / "receipt-seams-v13520.json"
REGEN = os.environ.get("IJ_REGEN_RECEIPT_SEAMS") == "1"
#: Stand-ins for the values that differ on every run (a byte-stable file).
STAMP = "2026-10-18T09:00:00"
APPROVAL_ID = "apr_fixture"
CALL_ID = "call_fixture"


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8")) if FIXTURE.exists() else {}


def _check(section: str, actual: dict) -> None:
    """The daemon's answer equals the committed fixture (or rewrites it)."""
    data = _fixture()
    if REGEN:
        data["_comment"] = (
            "v1.352.0 integrate: what the REAL daemon sends for wave 2's seams. "
            "Written by tests/test_receipt_seams_v13520.py (IJ_REGEN_RECEIPT_SEAMS=1); "
            "read by dashboard/__tests__/receipt-seams-v13520.test.tsx."
        )
        data[section] = actual
        FIXTURE.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return
    assert section in data, f"fixture has no {section!r}: regenerate it"
    assert data[section] == actual, (
        f"the daemon's {section!r} no longer matches the dashboard's contract fixture; "
        "if the change is intended, regenerate it"
    )


# --------------------------------------------------------------------------- #
# receipts: the helper agent's card and row read the wave-1 receipt
# --------------------------------------------------------------------------- #


def _thread(engine) -> str:
    rec = ChatThreadRecord(
        title="tax",
        messages_json=json.dumps([{"role": "user", "content": "read the K-1"}]),
    )
    with session_scope(engine) as db:
        db.add(rec)
        db.commit()
        db.refresh(rec)
        return rec.id


def _receipt_case(client, sid: str, provider: str, model: str, *, ollama_url: str = "") -> dict:
    p = client.app.state.platform
    if ollama_url:
        # The user's own Ollama on this PC (Settings › Models), as the
        # provider manager and the config both carry it.
        p.config.ollama_base_url = ollama_url
        if hasattr(p.providers, "_ollama_base_url"):
            p.providers._ollama_base_url = ollama_url
    _seed_delegated_run(p.engine, sid=sid, provider=provider, model=model)
    tid = _thread(p.engine)
    with session_scope(p.engine) as db:
        row = db.get(SessionRow, sid)
        row.options_json = json.dumps({"chat_thread_id": tid})
        db.add(row)
        db.commit()
        db.refresh(row)
        db.expunge(row)
    result = client.get(f"/sessions/{sid}/result")
    assert result.status_code == 200, result.text
    # The orchestrator's OWN delivery (every terminal path calls it).
    out = p.orchestrator._deliver_to_chat_sync(row)
    assert out.get("ok") is True, out
    thread = client.get(f"/chat/threads/{tid}")
    assert thread.status_code == 200, thread.text
    line = dict(thread.json()["messages"][-1])
    line["at"] = STAMP
    # The session row's fields the page's finalize stamps the hand-off from
    # (lib/agentRun RunRowLike), off GET /sessions/{id}.
    session = client.get(f"/sessions/{sid}").json()["session"]
    row_keys = ("created_at", "finished_at", "status", "outcome", "summary", "provider", "model", "workspace_path")
    return {
        "result": result.json(),
        "line": line,
        "row": {k: session.get(k) for k in row_keys},
    }


def test_the_receipts_the_helper_agent_card_reads(tmp_path):
    from fastapi.testclient import TestClient

    # One home per run: the seeded ledger uses fixed row ids.
    cloud = _receipt_case(TestClient(create_app(str(tmp_path / "cloud"))), "sess_cloud", "anthropic", "claude-sonnet-4-6")
    local = _receipt_case(TestClient(create_app(str(tmp_path / "local"))), "sess_local", "ollama", "llama3.2", ollama_url="http://127.0.0.1:11434")
    # What the dashboard renders, said here in the daemon's terms:
    for case, where in ((cloud, False), (local, True)):
        res = case["result"]
        assert res["on_this_network"] is where
        assert res["agent_name"] == "custom:tax-reader"
        assert res["route_note"] and res["activity"]
        # One vocabulary: the ask line uses the approval card's words.
        assert "tax-reader needed your OK to run a command" in [a["text"] for a in res["activity"]]
        # The line the daemon appended carries THE SAME receipt, where included.
        assert case["line"]["receipt"] == res
    _check("receipts", {"cloud": cloud, "local": local})


# --------------------------------------------------------------------------- #
# asks: one real ask, as each surface receives it
# --------------------------------------------------------------------------- #


async def _wait_for(pred, tries: int = 400) -> bool:
    for _ in range(tries):
        if pred():
            return True
        await asyncio.sleep(0.01)
    return False


def _stable_ask(d: dict) -> dict:
    out = dict(d)
    for key in ("id", "approval_id"):
        if key in out:
            out[key] = APPROVAL_ID
    if "call_id" in out:
        out["call_id"] = CALL_ID
    for key in ("requested_at", "args_hash"):
        if key in out:
            out[key] = f"<{key}>"
    return out


@pytest.mark.asyncio
async def test_one_ask_as_every_surface_receives_it(tmp_path):
    # 1. The stream lane's frame (the chat card, the dock, a Build pane).
    chat_app = create_app(str(tmp_path / "chat"))
    chat_app.state.platform.router.stream = _calls_stream(
        [("write_file", {"path": "brief.md", "content": "# Brief"})]
    )
    frames: list[dict] = []

    async def allow(data):
        frames.append(data)
        status, _ = await _asgi_post(chat_app, f"/chat/approvals/{data['id']}", {"decision": "once"})
        assert status == 200

    await _drive_stream(chat_app, _body("write the brief file", "always_ask", tools=["write_file"]), allow)
    assert [f["tool"] for f in frames] == ["write_file"], "anti-vacuity: the card must come"

    # 2-5. The runtime's pause (an agent run that asks) and its four readers.
    app = create_app(str(tmp_path / "run"))
    platform = app.state.platform
    platform.permissions._base["write_file"] = "ask"
    published: list[dict] = []
    real_publish = platform.event_bus.publish

    async def spy(type, payload=None, session_id=None, **kw):
        published.append({"type": str(getattr(type, "value", type)), "payload": payload or {}, "sid": session_id})
        return await real_publish(type, payload, session_id=session_id, **kw)

    platform.event_bus.publish = spy
    sid = "session_brief"
    with session_scope(platform.engine) as db:
        db.add(SessionRow(id=sid, origin="schedule:nightly-brief", task="Write the nightly brief"))
        db.commit()
    sess = SimpleNamespace(
        id=sid, origin="schedule:nightly-brief", approval_mode="", agent_name="", project_id=None, trust="full",
    )
    tc = SimpleNamespace(name="write_file", arguments={"path": "brief.md", "content": "# Brief"})
    runtime = AgentRuntime(platform)
    task = asyncio.create_task(runtime._pause_for_approval(sess, tc, get_agent_definition(AgentType.BUILDER), set()))

    def asks() -> list[dict]:
        return [p["payload"] for p in published if p["type"] == str(EventType.APPROVAL_REQUESTED) and p["sid"] == sid]

    try:
        assert await _wait_for(lambda: bool(asks())), "the run never asked"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
            # Wait for the ask to be LISTED, not only published: the spy
            # records it before the real publish writes the event row the
            # listing reads (the v1.373.1 shape), so one read could come back
            # without it on a loaded runner ("coroutine raised StopIteration"
            # at the `next` below). Bounded at 15 s.
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 15.0
            while True:
                listing = (await c.get("/chat/approvals/pending")).json()["approvals"]
                if any(r.get("session_id") == sid for r in listing) or loop.time() > deadline:
                    break
                await asyncio.sleep(0.05)
            row = (await c.get(f"/sessions/{sid}")).json()["session"]
        view = await asyncio.to_thread(mission.mission_view, platform, sid)
        requested = asks()[-1]
        assert platform.approvals.resolve(requested["approval_id"], "once")
        await asyncio.wait_for(task, 10)
    finally:
        if not task.done():
            task.cancel()
    pending_row = next(r for r in listing if r.get("session_id") == sid)
    surfaces = {
        "stream_frame": _stable_ask(frames[0]),
        "requested": _stable_ask({k: v for k, v in requested.items() if k != "args"}),
        "pending_row": _stable_ask(pending_row),
        "waiting_on": _stable_ask(row["waiting_on"]),
        "mission_waiting": _stable_ask(view["coordinator"]["waiting_on"]),
    }
    # The one verdict, on every surface (G25).
    for name, data in surfaces.items():
        assert data["tool"] == "write_file", name
        assert data.get("reversible") is True, (name, data)
    # The listing never carries the call's arguments; the bell names the run.
    assert "args" not in pending_row and "# Brief" not in json.dumps(pending_row)
    assert pending_row["origin"] == "schedule:nightly-brief"
    assert pending_row["task"] == "Write the nightly brief"
    _check("asks", surfaces)


# --------------------------------------------------------------------------- #
# one ask-words table; the retired level names
# --------------------------------------------------------------------------- #


def _tool_words_ts(tool: str) -> str:
    src = (ROOT / "dashboard" / "lib" / "toolWords.ts").read_text(encoding="utf-8").replace("\r\n", "\n")
    m = re.search(rf'^\s*{re.escape(tool)}:\s*"([^"]+)"', src, re.M)
    assert m, f"lib/toolWords.ts has no phrase for {tool}"
    return m.group(1)


def test_the_activity_logs_ask_words_are_the_approval_cards():
    assert mission.ASK_WORDS, "anti-vacuity"
    for tool, words in mission.ASK_WORDS.items():
        assert words == _tool_words_ts(tool), tool
    assert mission.ask_words("write_file") == "save a file"
    # A tool nobody described keeps its own name, never invented words.
    # Readiness 12A (G22/G24, moved on purpose): the card's own words (mission.card_words).
    assert mission.ask_words("read_file") == "read a file"


def test_the_retired_level_names_still_mean_the_same_level():
    d = next(s for s in schema.SETTINGS if s.key == "device.approval_mode")
    assert _coerce(d, "Approve for me") == "approve_for_me"
    assert _coerce(d, "auto-approve") == "yolo"
    assert _coerce(d, "Ask for approval") == "always_ask"
    # The current names and values are unchanged.
    assert _coerce(d, "Ask when risky") == "approve_for_me"
    assert _coerce(d, "Don't ask") == "yolo"
    assert _coerce(d, "yolo") == "yolo"
    # Anything else stays as typed (the writer then refuses it in words).
    assert _coerce(d, "sometimes") == "sometimes"


# --------------------------------------------------------------------------- #
# lane 2D's route is wired into the real app (not registered by its test)
# --------------------------------------------------------------------------- #


def test_settings_access_is_served_by_the_real_app_factory(tmp_path):
    """G27: Settings › Permissions & ledger reads ``GET /settings/access``.
    The lane shipped the module; the integrator registers it in ``create_app``
    — this pin fails if that line goes (the lane's own test registers the
    route itself when it is missing, so it cannot see the gap)."""
    from fastapi.testclient import TestClient

    app = create_app(str(tmp_path))
    assert any(getattr(r, "path", "") == "/settings/access" for r in app.routes)
    r = TestClient(app).get("/settings/access")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"reads", "writes"}
    # v1.356.0 (G81, updated on purpose): + the user's own folders (`chosen`)
    # and whether the installation's own limit is on (`install`).
    assert set(body["reads"]) == {"limited", "folders", "chosen", "install"}
    assert "chat_files_dir" in body["writes"]
