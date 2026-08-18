"""v1.188.0 — the approval POSTURE: one dropdown, three promises.

``approval_mode`` rides the chat body and decides how v1.187.0's mid-turn ask
behaves. Each mode is a different promise to the user and each is tested as
the promise, not the plumbing:

* ``always_ask``      "always ask before editing files or using the internet"
                      — a write_document the engine would ALLOW still cards,
                      and a conversation grant stops exactly that re-carding;
* ``approve_for_me``  the default and v1.187.0's behaviour byte-for-byte —
                      an allowed write runs cardless, an ask-tier call cards;
* ``yolo``            no cards — ask-tier calls are auto-granted because the
                      user said so up front, and THE DENY FLOOR IS NOT A
                      MODE: a base ``deny`` refuses in yolo exactly as
                      everywhere else, engine-level.

Unknown mode strings coerce to the DEFAULT (never to yolo — a future client's
new mode name must not degrade to auto-approve), and the posture persists
with the thread through ``_clean_setup``, default stored as nothing.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

import iron_jarvis.daemon.routes.chat as chat_routes
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import (
    _FILE_WRITING_TOOLS,
    APPROVAL_MODES,
    STRICT_ASK_TOOLS,
    normalize_approval_mode,
)
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall

from tests.test_chat_approvals_v1187 import (  # the v1.187.0 harness, reused
    _asgi_post,
    _drive_stream,
)

_ASK_MSG = "run the command `echo yolo-run` in the terminal for me"


def _calls_stream(specs: list[tuple[str, dict]]):
    """router.stream stub emitting one tool call per round, then answering."""
    rounds = {"n": 0}

    async def fake_stream(*, provider=None, model=None, system, messages,
                          tools, session_id=None, task_class=None):
        fake_stream.systems.append(system)
        i = rounds["n"]
        if i < len(specs):
            rounds["n"] += 1
            name, args = specs[i]
            resp = LLMResponse(text="", tool_calls=[
                ToolCall(id=f"c{i}", name=name, arguments=args),
            ])
            yield {"type": "final", "response": resp,
                   "provider": "mock", "model": "mock"}
        else:
            yield {"type": "final", "response": LLMResponse(text="done."),
                   "provider": "mock", "model": "mock"}

    fake_stream.systems = []
    return fake_stream


def _body(text: str, mode: str, tools: list[str] | None = None) -> dict:
    out: dict = {
        "messages": [{"role": "user", "content": text}],
        "auto_tools": True,
        "approval_mode": mode,
    }
    if tools:
        out["tools"] = tools
    return out


# --------------------------------------------------------------------------- #
# yolo — no cards, and the floor still holds.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_yolo_runs_the_ask_tier_without_a_card(tmp_path):
    app = create_app(str(tmp_path))
    app.state.platform.router.stream = _calls_stream(
        [("shell", {"command": "echo yolo-run"})]
    )

    async def never(data):  # pragma: no cover — the point is it never fires
        raise AssertionError(f"yolo asked anyway: {data}")

    frames = await _drive_stream(app, _body(_ASK_MSG, "yolo"), never)

    kinds = [ev for ev, _ in frames]
    assert "approval" not in kinds
    finished = next(
        d for ev, d in frames
        if ev == "tool_call" and d.get("status") == "finished"
    )
    assert finished["ok"] is True
    assert "yolo-run" in finished.get("output", "")
    done = next(d for ev, d in frames if ev == "done")
    assert "shell" in (done.get("tools_used") or [])


@pytest.mark.asyncio
async def test_yolo_cannot_lift_a_base_deny(tmp_path):
    """THE FLOOR IS NOT A MODE. A tool the policy hard-denies is refused in
    yolo exactly as everywhere else — the posture chooses when to put a human
    between an ASKABLE call and its run, never what the engine answers."""
    app = create_app(str(tmp_path))
    # The engine copies its policy at construction (`self._base`) — mutating
    # config afterwards changes nothing, which is itself worth knowing. Set
    # the base the engine actually reads.
    app.state.platform.permissions._base["shell"] = "deny"
    app.state.platform.router.stream = _calls_stream(
        [("shell", {"command": "echo never"})]
    )

    async def never(data):  # pragma: no cover
        raise AssertionError("a hard deny must never render a card")

    frames = await _drive_stream(app, _body(_ASK_MSG, "yolo"), never)

    assert all(ev != "approval" for ev, _ in frames)
    finished = next(
        d for ev, d in frames
        if ev == "tool_call" and d.get("status") == "finished"
    )
    assert finished["ok"] is False
    assert "denied" in finished.get("output", "").lower()
    done = next(d for ev, d in frames if ev == "done")
    assert "shell" not in (done.get("tools_used") or [])


# --------------------------------------------------------------------------- #
# always_ask — writes and web card too, once each until granted.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_always_ask_cards_a_write_the_engine_would_allow(tmp_path):
    """The posture's whole point: write_document is ALLOW by policy and armed
    by the user, and strict mode still puts a card in front of it — armed_grant
    starts as every armed tool, so a set that could not tell WHO granted would
    make this mode a silent no-op on exactly the common case."""
    app = create_app(str(tmp_path))
    app.state.platform.router.stream = _calls_stream(
        [("write_file", {"path": "note.md", "content": "# hi"})]
    )

    seen = []

    async def approve(data):
        seen.append(data["tool"])
        status, _ = await _asgi_post(
            app, f"/chat/approvals/{data['id']}", {"decision": "once"}
        )
        assert status == 200

    frames = await _drive_stream(
        app,
        _body("write a note file for me", "always_ask", tools=["write_file"]),
        approve,
    )

    assert seen == ["write_file"], "strict mode never asked"
    finished = next(
        d for ev, d in frames
        if ev == "tool_call" and d.get("status") == "finished"
    )
    assert finished["ok"] is True, finished
    done = next(d for ev, d in frames if ev == "done")
    assert "write_file" in (done.get("tools_used") or [])


@pytest.mark.asyncio
async def test_always_ask_conversation_grant_stops_the_recarding(tmp_path):
    """Two writes, one answer: 'for this conversation' is what turns strict
    mode from a nag into a decision that sticks."""
    app = create_app(str(tmp_path))
    app.state.platform.router.stream = _calls_stream([
        ("write_file", {"path": "a.md", "content": "a"}),
        ("write_file", {"path": "b.md", "content": "b"}),
    ])

    answered = []

    async def grant(data):
        answered.append(data["id"])
        status, _ = await _asgi_post(
            app, f"/chat/approvals/{data['id']}", {"decision": "conversation"}
        )
        assert status == 200

    frames = await _drive_stream(
        app,
        _body("write two note files", "always_ask", tools=["write_file"]),
        grant,
    )

    assert len(answered) == 1, "the second write must not re-card"
    ran = [
        d for ev, d in frames
        if ev == "tool_call" and d.get("status") == "finished" and d.get("ok")
    ]
    assert len(ran) == 2


@pytest.mark.asyncio
async def test_always_ask_denial_of_a_write_is_honoured(tmp_path):
    """A refusal of a policy-ALLOWED call still refuses — the user outranks
    the policy in the strict direction, always."""
    app = create_app(str(tmp_path))
    app.state.platform.router.stream = _calls_stream(
        [("write_file", {"path": "no.md", "content": "x"})]
    )

    async def deny(data):
        status, _ = await _asgi_post(
            app, f"/chat/approvals/{data['id']}", {"decision": "deny"}
        )
        assert status == 200

    frames = await _drive_stream(
        app, _body("write a note file", "always_ask", tools=["write_file"]),
        deny,
    )

    finished = next(
        d for ev, d in frames
        if ev == "tool_call" and d.get("status") == "finished"
    )
    assert finished["ok"] is False
    assert "declined" in finished.get("output", "")
    done = next(d for ev, d in frames if ev == "done")
    assert "write_file" in (done.get("denied_tools") or [])
    assert "write_file" not in (done.get("tools_used") or [])


# --------------------------------------------------------------------------- #
# approve_for_me — the default, and v1.187.0 unchanged.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_default_mode_never_cards_an_allowed_write(tmp_path):
    """The middle posture must stay exactly what shipped yesterday: allowed
    tools run, only the engine's ask tier pauses. Driven with the DEFAULT
    (no approval_mode in the body at all)."""
    app = create_app(str(tmp_path))
    app.state.platform.router.stream = _calls_stream(
        [("write_file", {"path": "ok.md", "content": "fine"})]
    )

    async def never(data):  # pragma: no cover
        raise AssertionError("the default mode carded an allowed write")

    body = _body("write a note file", "")
    del body["approval_mode"]
    body["tools"] = ["write_file"]
    frames = await _drive_stream(app, body, never)

    assert all(ev != "approval" for ev, _ in frames)
    done = next(d for ev, d in frames if ev == "done")
    assert "write_file" in (done.get("tools_used") or [])


# --------------------------------------------------------------------------- #
# The vocabulary, and where the posture persists.
# --------------------------------------------------------------------------- #


def test_unknown_modes_coerce_to_the_default_never_to_yolo():
    assert normalize_approval_mode("yolo") == "yolo"
    assert normalize_approval_mode("ALWAYS_ASK") == "always_ask"
    assert normalize_approval_mode("") == "approve_for_me"
    # A future client's new mode name degrades to today's behaviour — never
    # to auto-approve, which would turn a version skew into a standing grant.
    for weird in ("full_auto", "trust", "auto-approve", None, 3):
        assert normalize_approval_mode(weird) == "approve_for_me", weird
    assert set(APPROVAL_MODES) == {"always_ask", "approve_for_me", "yolo"}


def test_strict_set_covers_writes_and_web_and_derives_from_the_writers():
    # Derived, not re-listed: a writer added to _FILE_WRITING_TOOLS is
    # strict-gated for free, which is what keeps the two vocabularies from
    # drifting the way the remember ladder did.
    assert _FILE_WRITING_TOOLS <= STRICT_ASK_TOOLS
    assert {"web_search", "web_fetch", "rename_file", "edit_file"} <= STRICT_ASK_TOOLS
    # Read-only tools stay out — carding read_document would make strict mode
    # unusable, and "ask before edits and internet" does not mean reads.
    assert "read_document" not in STRICT_ASK_TOOLS
    assert "read_file" not in STRICT_ASK_TOOLS


def test_the_posture_persists_with_the_thread_and_default_stores_nothing(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    tid = client.put(
        "/chat/threads/new",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "setup": {"tools": ["write_document"], "approval_mode": "yolo"},
        },
    ).json()["id"]
    setup = client.get(f"/chat/threads/{tid}").json()["setup"]
    assert setup["approval_mode"] == "yolo"

    # The DEFAULT is stored as nothing at all — a stray string must never
    # reload as a posture the user did not pick.
    tid2 = client.put(
        "/chat/threads/new",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "setup": {"tools": ["write_document"], "approval_mode": "approve_for_me"},
        },
    ).json()["id"]
    setup2 = client.get(f"/chat/threads/{tid2}").json()["setup"]
    assert "approval_mode" not in setup2

    # Garbage coerces before storing — "" would strip it, an unknown stores
    # nothing rather than something a future build might interpret.
    tid3 = client.put(
        "/chat/threads/new",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "setup": {"tools": ["write_document"], "approval_mode": "full_auto"},
        },
    ).json()["id"]
    setup3 = client.get(f"/chat/threads/{tid3}").json()["setup"]
    assert "approval_mode" not in setup3
