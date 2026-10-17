"""v1.345.0 ("an @ reply comes home") integration pins: the seams BETWEEN the
three lanes, driven on the real app (``create_app``, the offline mock adapter).

* The three session doors the page opens from chat (``POST /sessions``,
  ``/sessions/{id}/continue``, ``/agents/{slug}/spawn``) hand
  ``chat_thread_id`` to the orchestrator so the row's options carry it (lane
  T's "needs" -- without the one line in each handler the validated body
  fell on the floor and no terminal path could ever append).
* Through the real routes, a session asked from a chat leaves ONE line in
  that chat (``sessionId``, ``agentType``, ``panelWho``, the reply, the
  created file's ABSOLUTE path in ``documents``, the ledger ``receipt``);
  a follow-up carries its own chat and never a second line for the parent.
* The daemon-appended PANEL line is the page's ``roundMessages`` shape and
  the daemon-appended SESSION line is the page's ``finalize`` shape under the
  daemon's keys (``sessionId`` -> ``fromSession``, ``receipt`` ->
  ``runResult``, which ``lib/chatReread.adoptDaemonLines`` stands in); the
  page's own words for a run with no summary are the daemon's constants; the
  ``panelWho`` the daemon stamps is the key the panel route answers as ``who``
  for ``mode: "session"`` -- the key the page stores on an @ hand-off.
  Documented difference: the page composes ``panelWho`` only for an @
  hand-off and never ``agentType``/``documents``; the daemon line always
  carries ``panelWho`` + ``agentType`` (+ ``documents`` when files exist).
* A page holding the stamp from BEFORE an append 409s on its PUT and the
  append's stamp passes -- the signal the page re-reads on.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents import chat_delivery
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentType, Session, SessionStatus
from iron_jarvis.daemon import chat_append
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes.agents import panel_reply_lines

REPO = Path(__file__).resolve().parents[1]
PAGE = REPO / "dashboard" / "app" / "chat" / "page.tsx"
REREAD = REPO / "dashboard" / "lib" / "chatReread.ts"

AGENT = {
    "name": "brief-writer",
    "system_prompt": "write briefs",
    "tools": ["read_file", "write_file"],
    "description": "writes briefs",
    "provider": "mock",
    "model": "mock-1",
    "base_type": "builder",
}


def _src(path: Path) -> str:
    # CI's Windows runner checks out with CRLF (the v1.232.1 rule).
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _options(client: TestClient, sid: str) -> dict:
    platform = client.app.state.platform
    with session_scope(platform.engine) as db:
        row = db.get(Session, sid)
        assert row is not None, sid
        return json.loads(row.options_json or "{}")


def _saved_chat(client: TestClient, text: str) -> dict:
    made = client.put("/chat/threads/new", json={"messages": [{"role": "user", "content": text}]})
    assert made.status_code == 200, made.text
    return made.json()


def _thread(client: TestClient, tid: str) -> dict:
    r = client.get(f"/chat/threads/{tid}")
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------- #
# The three doors hand chat_thread_id to the orchestrator
# --------------------------------------------------------------------------- #
def test_post_sessions_and_continue_store_chat_thread_id_on_the_row(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        r = client.post("/sessions", json={"task": "say hi", "wait": True, "chat_thread_id": " chat-a "})
        assert r.status_code == 200, r.text
        parent = r.json()["id"]
        assert _options(client, parent)["chat_thread_id"] == "chat-a"  # trimmed
        # no id, no key
        r = client.post("/sessions", json={"task": "say hi", "wait": True})
        assert r.status_code == 200, r.text
        assert "chat_thread_id" not in _options(client, r.json()["id"])
        # a follow-up carries ITS OWN chat ...
        r = client.post(
            f"/sessions/{parent}/continue",
            json={"message": "and again", "wait": True, "chat_thread_id": "chat-b"},
        )
        assert r.status_code == 200, r.text
        child = r.json()["id"]
        assert _options(client, child)["chat_thread_id"] == "chat-b"
        # ... and never inherits the parent's
        r = client.post(f"/sessions/{child}/continue", json={"message": "once more", "wait": True})
        assert r.status_code == 200, r.text
        assert "chat_thread_id" not in _options(client, r.json()["id"])


def test_the_custom_agent_spawn_door_stores_chat_thread_id_on_the_row(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        r = client.post("/agents", json=AGENT)
        assert r.status_code in (200, 201), r.text
        r = client.post(
            f"/agents/{AGENT['name']}/spawn",
            json={"task": "write the brief", "wait": True, "chat_thread_id": "chat-c"},
        )
        assert r.status_code == 200, r.text
        assert _options(client, r.json()["id"])["chat_thread_id"] == "chat-c"
        r = client.post(f"/agents/{AGENT['name']}/spawn", json={"task": "write the brief", "wait": True})
        assert r.status_code == 200, r.text
        assert "chat_thread_id" not in _options(client, r.json()["id"])


# --------------------------------------------------------------------------- #
# Through the real routes: ONE line comes home, a follow-up never doubles it
# --------------------------------------------------------------------------- #
def test_a_session_asked_from_a_chat_leaves_one_line_there_and_a_follow_up_never_a_second(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        asked = _saved_chat(client, "@builder write RESULT.md")
        tid, stale = asked["id"], asked["updated_at"]
        # the mock adapter's default loop writes RESULT.md through the real tool
        r = client.post(
            "/sessions",
            json={"task": "write RESULT.md", "wait": True, "chat_thread_id": tid, "origin": "chat"},
        )
        assert r.status_code == 200, r.text
        sid = r.json()["id"]
        assert r.json()["status"] == "completed"
        got = _thread(client, tid)
        lines = [m for m in got["messages"] if m.get("sessionId") == sid]
        assert len(lines) == 1, got["messages"]
        line = lines[0]
        assert line["role"] == "assistant"
        assert line["content"] == "Done. Wrote RESULT.md summarizing the task."
        assert line["agentType"] == "builder"
        assert line["panelWho"] == "builtin:builder"
        assert line["at"].endswith("+00:00")
        docs = line["documents"]
        assert docs and all(Path(p).is_absolute() for p in docs), docs
        assert any(Path(p).name == "RESULT.md" and Path(p).is_file() for p in docs), docs
        assert line["receipt"]["found"] is True and line["receipt"]["session_id"] == sid
        assert got["updated_at"] != stale
        # the stale page 409s; the appended stamp passes (the re-read signal)
        stale_put = client.put(
            f"/chat/threads/{tid}",
            json={"messages": got["messages"], "if_updated_at": stale},
        )
        assert stale_put.status_code == 409, stale_put.text
        fresh_put = client.put(
            f"/chat/threads/{tid}",
            json={"messages": got["messages"], "if_updated_at": got["updated_at"]},
        )
        assert fresh_put.status_code == 200, fresh_put.text
        # a follow-up asked from NOWHERE: no second line for the parent, none for itself
        r = client.post(f"/sessions/{sid}/continue", json={"message": "and again", "wait": True})
        assert r.status_code == 200, r.text
        child = r.json()["id"]
        after = _thread(client, tid)["messages"]
        assert len([m for m in after if m.get("sessionId") == sid]) == 1
        assert not [m for m in after if m.get("sessionId") == child]
        # a follow-up asked from the SAME chat: its own ONE line, the parent's still one
        r = client.post(
            f"/sessions/{sid}/continue",
            json={"message": "and once more", "wait": True, "chat_thread_id": tid},
        )
        assert r.status_code == 200, r.text
        child2 = r.json()["id"]
        final = _thread(client, tid)["messages"]
        assert len([m for m in final if m.get("sessionId") == sid]) == 1
        assert len([m for m in final if m.get("sessionId") == child2]) == 1


def test_a_second_delivery_of_the_same_session_is_skipped_by_session_id(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        asked = _saved_chat(client, "@builder write RESULT.md")
        tid = asked["id"]
        r = client.post("/sessions", json={"task": "write RESULT.md", "wait": True, "chat_thread_id": tid})
        assert r.status_code == 200, r.text
        sid = r.json()["id"]
        platform = client.app.state.platform
        before = _thread(client, tid)
        # the page's OWN compose (fromSession) for the same session is the same identity
        out = chat_append.append_to_chat_thread(
            platform.engine, tid, [{"role": "assistant", "content": "twice?", "fromSession": sid}]
        )
        assert out["ok"] is True and out["appended"] == 0 and out["skipped"] == 1
        after = _thread(client, tid)
        assert after["messages"] == before["messages"]
        assert after["updated_at"] == before["updated_at"]  # a no-op leaves the stamp alone


# --------------------------------------------------------------------------- #
# Shape equality: daemon line == page line
# --------------------------------------------------------------------------- #
def _page_block(name: str) -> str:
    src = _src(PAGE)
    start = src.index(f"function {name}(")
    return src[start : start + 6000]


def test_the_daemon_panel_line_is_the_pages_roundmessages_shape():
    block = _page_block("roundMessages")
    # the keys the page writes on an agent's panel line, read off the source
    page_keys = set(re.findall(r"^\s+(?:\.\.\.\(.*?\{ )?(\w+)(?=:|\s*\})", block[: block.index("];")], re.M))
    page_keys = {k for k in page_keys if k in chat_append.LINE_KEYS or k == "role"}
    assert page_keys == {"role", "content", "panelWho", "panelThreadId", "panelError", "panelNote"}, page_keys
    assert page_keys <= set(chat_append.LINE_KEYS)
    round_out = {
        "entries": [
            {"who": "user", "content": "@builder @critic plan it"},
            {"who": "builtin:builder", "content": "A plan."},
            {"who": "builtin:critic", "content": "", "error": "boom"},
        ],
        "context": {"chat_dropped": 2},
    }
    lines = panel_reply_lines(round_out, "room-1")
    assert lines == [
        {
            "role": "assistant",
            "content": "A plan.",
            "panelWho": "builtin:builder",
            "panelThreadId": "room-1",
            "panelNote": "2 earlier messages did not fit the agent's context window",
        },
        {
            "role": "assistant",
            "content": "(no reply)",
            "panelWho": "builtin:critic",
            "panelThreadId": "room-1",
            "panelError": True,
        },
    ]
    # the page's exact words for the note and the empty reply
    assert "did not fit the agent's context window" in block
    assert '"(no reply)"' in block
    one = panel_reply_lines({"entries": [{"who": "x", "content": "y"}], "context": {"chat_dropped": 1}}, "r")
    assert one[0]["panelNote"] == "1 earlier message did not fit the agent's context window"
    # additive, page-renderable keys the page's compose cannot know
    pend = panel_reply_lines({"entries": [{"who": "remote:h", "content": "working", "pending": True,
                                           "documents": ["C:/x/a.md"]}]}, "r")
    assert pend[0]["panelKind"] == "progress" and pend[0]["documents"] == ["C:/x/a.md"]


def _fake_session(status: SessionStatus, summary: str, sid: str = "s-1", agent_name: str = ""):
    return SimpleNamespace(
        id=sid,
        status=status,
        summary=summary,
        agent_type=AgentType.BUILDER,
        agent_name=agent_name,
    )


def _page_compose(session, run_result, who: str | None) -> dict:
    """page.tsx finalize()'s compose, transcribed (the source pin below keeps
    the words in step)."""
    status = session.status.value
    summary = (session.summary or "").strip()
    content = (
        (summary or "(no response)")
        if status == "completed"
        else (summary or f"The agent stopped before finishing ({status}). Please try again.")
    )
    line = {"role": "assistant", "content": content, "fromSession": session.id}
    if run_result:
        line["runResult"] = run_result
    if who:
        line["panelWho"] = who
    return line


def _adopt(line: dict) -> dict:
    """lib/chatReread.adoptDaemonLines, transcribed: sessionId -> fromSession,
    a RunResult-shaped receipt -> runResult, each only when absent."""
    out = dict(line)
    if line.get("sessionId") and not line.get("fromSession"):
        out["fromSession"] = line["sessionId"]
    rec = line.get("receipt")
    if "runResult" not in line and isinstance(rec, dict) and isinstance(rec.get("found"), bool) and isinstance(rec.get("session_id"), str):
        out["runResult"] = rec
    return out


@pytest.mark.parametrize(
    "status, summary",
    [
        (SessionStatus.COMPLETED, "Done. Wrote RESULT.md summarizing the task."),
        (SessionStatus.COMPLETED, "   "),
        (SessionStatus.FAILED, ""),
        (SessionStatus.CANCELLED, "stopped mid-way"),
    ],
)
def test_the_daemon_session_line_equals_the_pages_finalize_line(status, summary):
    s = _fake_session(status, summary)
    result = {"found": True, "session_id": s.id, "documents": ["C:/w/RESULT.md"], "tools": []}
    who = chat_delivery.participant_key_for(s)
    daemon = _adopt(chat_delivery.line_for_session(s, result, who))
    page = _page_compose(s, result, who)
    shared = {"role", "content", "fromSession", "runResult", "panelWho"}
    assert {k: daemon[k] for k in shared} == {k: page[k] for k in shared}
    # what the daemon adds that the page's compose never had: the agent and
    # the files, read by nothing the receipt/footer keys on
    assert set(daemon) - set(page) == {"sessionId", "receipt", "agentType", "documents"}
    assert daemon["agentType"] == "builder" and daemon["documents"] == ["C:/w/RESULT.md"]
    # no ledger card: no receipt, no documents -- exactly the page when /result fails
    bare = _adopt(chat_delivery.line_for_session(s, None, who))
    assert {k: bare[k] for k in ("role", "content", "fromSession", "panelWho")} == {
        k: _page_compose(s, None, who)[k] for k in ("role", "content", "fromSession", "panelWho")
    }
    assert "runResult" not in bare and "documents" not in bare


def test_the_pages_finalize_words_are_the_daemons_constants():
    block = _page_block("finalize")
    assert f'"{chat_delivery.NO_RESPONSE}"' in block
    ts = chat_delivery.STOPPED_SENTENCE.replace("{status}", "${status}")
    assert f"`{ts}`" in block
    # the page's compose keys, read off the source
    compose = block[block.index("const full: ChatMessage[] = [") : block.index("setMessages(full)")]
    assert "fromSession: id" in compose and "runResult" in compose and "panelWho: who" in compose
    # and the adopter stands the daemon's keys in for them
    reread = _src(REREAD)
    assert "fromSession: m.sessionId" in reread and "runResult: m.receipt" in reread


def test_the_daemons_panelwho_is_the_key_the_panel_route_answers_for_a_session(tmp_path):
    """The page stores `res.who` from POST /chat/panel mode "session" as the
    hand-off's panelWho; the daemon's terminal line must carry the same key
    (builtin:<type> / dynamic:<slug>), never the contract prose's agent:<type>."""
    with TestClient(create_app(str(tmp_path))) as client:
        r = client.post("/chat/panel", json={"message": "@builder write the brief", "hands": True})
        assert r.status_code == 200, r.text
        assert r.json()["mode"] == "session"
        who = r.json()["who"]
        s = _fake_session(SessionStatus.COMPLETED, "ok")
        assert chat_delivery.participant_key_for(s) == who == "builtin:builder"
        r = client.post("/agents", json=AGENT)
        assert r.status_code in (200, 201), r.text
        r = client.post("/chat/panel", json={"message": f"@{AGENT['name']} write the brief", "hands": True})
        assert r.status_code == 200, r.text
        assert r.json()["mode"] == "session"
        custom = _fake_session(SessionStatus.COMPLETED, "ok", agent_name=f"custom:{AGENT['name']}")
        assert chat_delivery.participant_key_for(custom) == r.json()["who"] == f"dynamic:{AGENT['name']}"
