"""v1.357.0 integrate (product-readiness Wave 7): the lanes joined.

(a) G61 x G76 — a task another computer running Iron Jarvis sent
    (``task_door``'s ``remote:<caller>`` origin, lane 7A) reads in the ONE
    vocabulary (lane 7C) wherever it is said: the phone/desktop alert
    (``comm/notifier``) says the bell's "A task from <caller> …", it is
    unattended for the background lane AND Emergency stop, and Emergency
    stop's own sentence names it. A REAL door task (real app factory, real
    door, real orchestrator; only the run itself made to fail) writes its
    ``session.completed`` event to
    ``dashboard/__tests__/fixtures/wave7-seams-v13570.json`` (regenerate with
    ``IJ_REGEN_WAVE7_SEAMS=1``), and ``wave7-seams-v13570.test.tsx`` feeds that
    very event through the REAL bell, OriginChip and approval words.

(b) G82 x G84 — the Tools catalog and the Directory read the ONE catalog
    file (same ids, same names, the Tools row's ``directory_twin`` is the card
    that saves the same pack); a configured web service's ``rest_call`` is
    carded in the streaming chat lane, never shown to (and refused for) a
    LOW-TRUST turn, and carded EVERY time in a LOCAL-ONLY chat even in yolo.

Wave-6 follow-up (2), daemon half: an app a chat has on that the user has
since switched off is still sent in ``connectors`` (the page keeps it, says
Off) and arms nothing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.agents import orchestrator as orch_mod
from iron_jarvis.agents import runtime as runtime_mod
from iron_jarvis.agents import task_door as td
from iron_jarvis.agents.orchestrator import (
    EMERGENCY_STOP_REFUSAL,
    emergency_stop_covers,
    is_unattended_origin,
)
from iron_jarvis.comm.notifier import format_event
from iron_jarvis.connectors import catalog
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import EventRecord
from iron_jarvis.core.trust import LOW_TRUST_DENY
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.auth import TASK_DOOR_PATH
from iron_jarvis.daemon.chat_turn import _resolve_connectors
from iron_jarvis.daemon.routes.chat import stream_chat_turn
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.settings import schema as settings_schema
from tests.test_another_jarvis_v13570 import B_HOST, _settled
from tests.test_chat_approvals_v1187 import _asgi_post, _drive_stream
from tests.test_rest_call_v13570 import ASK, KEY, NAME, _add_hookup, _finished, _scripted, _Server

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "dashboard" / "__tests__" / "fixtures" / "wave7-seams-v13570.json"


def _src(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8").replace("\r\n", "\n")


@pytest.fixture
def svc():
    """Lane 7B's fake CRM (a real HTTP server on this machine)."""
    srv = _Server()
    other = _Server()
    srv.steal_url = other.base + "/steal"
    srv.other = other
    yield srv
    srv.close()
    other.close()


# =========================================================================== #
# (a) G61 x G76: a remote task is said one way, from the door to the phone.
# =========================================================================== #


def test_the_dashboard_and_the_daemon_name_a_remote_task_with_one_set_of_words():
    """lib/remoteTask is the dashboard's twin of task_door's origin; the
    phone's subject table is the bell's."""
    ts = _src("dashboard/lib/remoteTask.ts")
    assert f'export const REMOTE_KIND = "{td.REMOTE_TASK_ORIGIN}";' in ts
    assert f'export const REMOTE_DEFAULT_CALLER = "{td.DEFAULT_CALLER}";' in ts
    assert "return `A task from ${who}`;" in ts
    # The phone/desktop alert's subject, from the notifier's own table.
    for origin, want in (
        (td.remote_origin("desk"), "A task from desk"),
        ("remote:", f"A task from {td.DEFAULT_CALLER}"),
    ):
        line = format_event(
            {"type": "session.completed", "payload": {"origin": origin, "status": "failed", "ok": False}}
        )
        assert line == f"{want} could not finish", line
    # Control: an unknown door still says "A run" (the table is what decides).
    assert format_event(
        {"type": "session.completed", "payload": {"origin": "nosuchdoor:x", "status": "failed", "ok": False}}
    ) == "A run could not finish"


def test_a_remote_task_is_unattended_and_emergency_stop_says_so():
    origin = td.remote_origin("desk")
    assert is_unattended_origin(origin) and emergency_stop_covers(origin)
    assert not origin.startswith(runtime_mod.ATTENDED_ORIGINS)
    assert not origin.startswith(runtime_mod.ASKING_ORIGINS)
    # The sentence the sender reads quoted in the door's 409 names it, in
    # the canon's unit of work (VOCABULARY.md: task, never job).
    assert "tasks from another computer" in EMERGENCY_STOP_REFUSAL
    assert "job" not in EMERGENCY_STOP_REFUSAL.lower()
    row = next(s for s in settings_schema.SETTINGS if s.key == "autonomy_kill_switch")
    assert "tasks from another computer" in row.help and "job" not in row.help.lower()


@pytest.mark.timeout(120)
async def test_a_real_door_task_that_fails_reads_in_the_bells_words_on_the_phone(tmp_path, monkeypatch):
    monkeypatch.setenv("IRONJARVIS_HOST_ALLOWLIST", "b-pc")
    app = create_app(str(tmp_path))

    async def boom(platform, session):
        raise RuntimeError("the supervisor fell over")

    monkeypatch.setattr(orch_mod, "run_supervised", boom)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=f"http://{B_HOST}"
    ) as client:
        token = (await client.post("/agents/task-door/enable", json={"name": "laptop"})).json()["token"]
        r = await client.post(
            TASK_DOOR_PATH,
            json={"task": "summarise the notes"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 202, r.text
        (row,) = await _settled(app)

    with session_scope(app.state.platform.engine) as db:
        ev = next(
            e
            for e in db.exec(select(EventRecord).where(EventRecord.type == "session.completed"))
            if e.session_id == row.id
        )
        payload = json.loads(ev.payload_json)
    assert payload["origin"] == "remote:laptop"
    line = format_event({"type": ev.type, "payload": payload, "session_id": row.id})
    title, _, said = line.partition(" — ")
    assert title == "A task from laptop could not finish", line
    assert said and "RuntimeError" not in said, "the summary is plain words, never a class name"

    keep = ("origin", "status", "ok", "outcome", "summary")
    contract = {
        "remote_completed": {
            "type": ev.type,
            "session_id": "session_remote_task",
            "payload": {k: payload.get(k) for k in keep if k in payload},
        },
        "phone_title": title,
        "default_caller": td.DEFAULT_CALLER,
    }
    if os.environ.get("IJ_REGEN_WAVE7_SEAMS") == "1":
        CONTRACT.write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    on_disk = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert on_disk == json.loads(json.dumps(contract)), (
        "the dashboard's wave-7 contract drifted from the daemon; regenerate with IJ_REGEN_WAVE7_SEAMS=1"
    )


# =========================================================================== #
# (b) G82 x G84: one catalog; a web service's call is carded, kept away, asked.
# =========================================================================== #


def test_the_tools_catalog_and_the_directory_read_one_file(tmp_path):
    client = TestClient(create_app(str(tmp_path / "home")))
    tools_rows = client.get("/mcp/catalog").json()["catalog"]
    cards = {c["id"]: c for c in client.get("/connectors").json()["connectors"]}
    twins = [r for r in tools_rows if r.get("directory_twin")]
    assert {r["id"] for r in twins} == {"github", "filesystem", "fetch", "memory", "box"}
    for row in twins:
        card = cards[row["directory_twin"]]
        assert card["name"] == row["name"], (row["id"], card["name"], row["name"])
    # Every Tools row is built from the one file, in its order.
    assert [r["id"] for r in tools_rows] == [r["id"] for r in catalog.mcp_catalog_rows()]
    # The dashboard keeps no hand list of its own for the Tools-side link.
    tools_page = _src("dashboard/components/settings/pages/ToolsPage.tsx")
    assert "DIRECTORY_TWINS" not in tools_page
    assert "entry.directory_twin" in tools_page and "hasDirectoryTwin(entry) && (" in tools_page


def _sse_frames(raw: str) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for block in raw.split("\n\n"):
        ev = data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                data = line[6:]
        if ev and data:
            try:
                out.append((ev, json.loads(data)))
            except ValueError:
                pass
    return out


@pytest.mark.timeout(120)
async def test_a_low_trust_turn_never_sees_the_service_and_a_call_is_refused(tmp_path, monkeypatch, svc):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    await _add_hookup(app, svc.base)
    call = ("rest_call", {"service": NAME, "path": "/contacts/42"})
    body = ChatBody(
        messages=[{"role": "user", "content": ASK}], tools=["rest_call"], auto_tools=True, approval_mode="yolo"
    )

    # Control: the same explicit pick at full trust in yolo shows it and runs.
    seen = _scripted(p, monkeypatch, [[call]])
    gen = await stream_chat_turn(p, {}, body)
    frames = _sse_frames("".join([c async for c in gen]))
    assert "rest_call" in seen["tools"][0]
    assert [f["ok"] for f in _finished(frames)] == [True]
    assert [(m, pth) for m, pth, _a, _b in svc.seen] == [("GET", "/api/contacts/42")]

    svc.seen.clear()
    seen = _scripted(p, monkeypatch, [[call]])
    gen = await stream_chat_turn(p, {}, body, trust="low", trust_reason="started from another computer")
    frames = _sse_frames("".join([c async for c in gen]))
    assert "rest_call" in LOW_TRUST_DENY
    assert all("rest_call" not in names for names in seen["tools"]), seen["tools"]
    # A call the low-trust turn was never offered is never run (no finished
    # step at all), and the receipt says what was kept away.
    assert _finished(frames) == []
    assert svc.seen == [], "nothing reached the service"
    done = next(d for ev, d in frames if ev == "done")
    assert done["trust"] == "low" and done["trust_note"] == "low trust: 1 tool kept away"
    assert done["tools_used"] == []


@pytest.mark.timeout(120)
async def test_a_local_only_chat_cards_every_call_even_in_yolo(tmp_path, monkeypatch, svc):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    await _add_hookup(app, svc.base)
    call = ("rest_call", {"service": NAME, "path": "/contacts/42"})
    _scripted(p, monkeypatch, [[call], [call]])
    cards: list[dict] = []

    async def widest(data):
        cards.append(data)
        assert len(svc.seen) == len(cards) - 1, "nothing reached the service before the user answered"
        st, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "conversation"})
        assert st == 200

    frames = await _drive_stream(
        app,
        {
            "messages": [{"role": "user", "content": ASK}],
            "tools": ["rest_call"],
            "approval_mode": "yolo",
            "local_only": True,
        },
        widest,
    )
    # Two reads, two cards: yolo and the widest answer skip neither, and the
    # card never offers "Always" (it leaves this PC).
    assert [c["tool"] for c in cards] == ["rest_call", "rest_call"], cards
    assert all(c.get("can_always") is False for c in cards)
    assert [(m, pth) for m, pth, _a, _b in svc.seen] == [("GET", "/api/contacts/42")] * 2
    assert all(f["ok"] for f in _finished(frames))
    assert KEY not in json.dumps(frames)


@pytest.mark.timeout(120)
async def test_the_streaming_chat_cards_a_named_service_before_it_calls_it(tmp_path, monkeypatch, svc):
    """Seam with the words: the card the user answers names the tool by the
    dashboard's own phrase key (lib/toolWords ``rest_call``)."""
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    await _add_hookup(app, svc.base)
    _scripted(p, monkeypatch, [[("rest_call", {"service": NAME, "path": "/contacts/42"})]])
    cards: list[dict] = []

    async def once(data):
        cards.append(data)
        assert svc.seen == []
        st, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "once"})
        assert st == 200

    await _drive_stream(app, {"messages": [{"role": "user", "content": ASK}], "auto_tools": True}, once)
    assert [c["tool"] for c in cards] == ["rest_call"]
    assert len(svc.seen) == 1
    words = _src("dashboard/lib/toolWords.ts")
    assert 'rest_call: "call one of your saved web services",' in words
    from iron_jarvis.agents.mission import ASK_WORDS

    assert ASK_WORDS["rest_call"] == "call one of your saved web services"


# =========================================================================== #
# Wave-6 follow-up (2), daemon half.
# =========================================================================== #


def test_a_switched_off_app_still_in_a_chats_connections_arms_nothing(tmp_path):
    client = TestClient(create_app(str(tmp_path / "home")))
    d = SimpleNamespace(platform=client.app.state.platform)
    # Saved in the chat, but no pack loaded (switched off): nothing armed,
    # nothing raised — the page says Off beside it.
    assert _resolve_connectors(d, SimpleNamespace(connectors=["slack"])) == ([], [])
    words = _src("dashboard/lib/extensionWords.ts")
    assert "export const PACK_OFF_IN_CHAT_LINE" in words
