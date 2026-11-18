"""Fourth fix plan, group 28, C-DT-1: a run started from a chat opens its CHAT.

A run the user started from a chat (origin ``chat``, a chat thread id on the
row) delivers its reply into that chat (``agents/chat_delivery``). Its tray
notice used to open ``/sessions/<id>``, the Agent runs page, so the user had
to find the chat again. Now the ``session.completed`` payload names the chat
(``chat_thread_id``, always present, ``None`` when there is none) and the
desktop app's tray notice opens ``/chat?thread=<id>``, as ``remote.message``
already does. A run started any other way keeps today's link.

Driven through the real app (``create_app``, the offline mock model) and the
real ``desktop/main.js`` functions under node, lifted the way
``tests/test_one_notice_v13600.py`` lifts them.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.events import EventType
from iron_jarvis.daemon.app import create_app

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "desktop" / "main.js"

requires_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")


def _completed_events(platform) -> list[dict]:
    seen: list[dict] = []

    def handler(ev) -> None:
        if ev.type == EventType.SESSION_COMPLETED:
            seen.append(
                {
                    "id": f"evt_{len(seen)}",
                    "type": ev.type.value if hasattr(ev.type, "value") else str(ev.type),
                    "session_id": ev.session_id,
                    "ts": "2026-11-18T09:00:00",
                    "payload": dict(ev.payload or {}),
                }
            )

    platform.event_bus.add_handler(handler)
    return seen


def _run_from_chat(tmp_path: Path, **body) -> tuple[str, dict]:
    """One real ``POST /sessions`` (the chat page's hand-off door), waited on;
    returns the session id and its ``session.completed`` event."""
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        seen = _completed_events(client.app.state.platform)
        r = client.post("/sessions", json={"task": "Write a short report.", "wait": True, **body})
        assert r.status_code == 200, r.text
        sid = r.json()["id"]
    mine = [e for e in seen if e["session_id"] == sid]
    assert len(mine) == 1, seen
    return sid, mine[0]


# --------------------------------------------------------------------------- #
# The completion payload names the chat
# --------------------------------------------------------------------------- #


def test_a_run_started_from_a_chat_names_that_chat_when_it_finishes(tmp_path):
    _sid, ev = _run_from_chat(tmp_path, origin="chat", chat_thread_id=" chat-a ")
    assert ev["payload"]["origin"] == "chat"
    assert ev["payload"]["chat_thread_id"] == "chat-a"  # trimmed, as the row keeps it


def test_a_run_with_no_chat_says_so_and_never_drops_the_key(tmp_path):
    _sid, ev = _run_from_chat(tmp_path, origin="chat")
    assert "chat_thread_id" in ev["payload"]
    assert ev["payload"]["chat_thread_id"] is None


# --------------------------------------------------------------------------- #
# The tray notice opens the chat (desktop/main.js under node)
# --------------------------------------------------------------------------- #


def _lift() -> str:
    src = MAIN.read_text(encoding="utf-8").replace("\r\n", "\n")
    start = src.index("const RUN_OUTCOME_AUDIT_TYPES")
    tail = src.index("function runOutcomePath", start)
    end = src.index("\n}\n", tail) + 3
    return src[start:end]


_NOTICES = """
const input = JSON.parse(process.argv[2]);
const out = input.events.map((e) => runOutcomeNotice(e));
process.stdout.write(JSON.stringify({ out }) + "\\n");
"""


def _tray(tmp_path: Path, events: list[dict]) -> list[dict | None]:
    f = tmp_path / "g28d_run_outcome.js"
    f.write_text(_lift() + "\n" + _NOTICES, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(f), json.dumps({"events": events})],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        env={**os.environ},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])["out"]


@requires_node
def test_the_tray_notice_of_a_chat_started_run_opens_its_chat(tmp_path):
    _sid, ev = _run_from_chat(tmp_path, origin="chat", chat_thread_id="chat-a")
    (notice,) = _tray(tmp_path, [ev])
    assert notice is not None
    assert notice["title"].startswith("The agent you started from chat")
    assert notice["href"] == "/chat?thread=chat-a"


@requires_node
def test_a_chat_started_run_with_no_chat_keeps_its_run_link(tmp_path):
    sid, ev = _run_from_chat(tmp_path, origin="chat")
    (notice,) = _tray(tmp_path, [ev])
    assert notice is not None
    assert notice["href"] == f"/sessions/{sid}"


@requires_node
def test_the_chat_id_is_escaped_and_other_runs_keep_todays_links(tmp_path):
    def ev(sid: str, origin: str, chat: object, status: str = "completed") -> dict:
        return {
            "id": f"evt_{sid}",
            "type": "session.completed",
            "session_id": sid,
            "ts": "2026-11-18T09:00:00",
            "payload": {
                "status": status,
                "ok": status == "completed",
                "summary": "",
                "origin": origin,
                "project_id": None,
                "outcome": "completed" if status == "completed" else None,
                "chat_thread_id": chat,
            },
        }

    odd, blank, mission, failed_mission = _tray(
        tmp_path,
        [
            ev("s1", "chat", "a b&c"),
            ev("s2", "chat", "   "),
            # A mission keeps its mission link even when a chat is named.
            ev("s3", "job:mission", "chat-a"),
            ev("s4", "job:mission", "chat-a", status="failed"),
        ],
    )
    assert odd["href"] == "/chat?thread=a%20b%26c"
    assert blank["href"] == "/sessions/s2"
    assert mission["href"] == "/agents?mission=s3"
    assert failed_mission["href"] == "/agents?mission=s4"
