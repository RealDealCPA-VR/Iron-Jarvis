"""v1.353.0 independent re-review — a signed-in app is not sent to connect.

The integrator widened the chat's app vocabulary so "find my tax files in
Google Drive" arms ``app_connect`` (G41's acceptance wording), and a failed
``app_connect`` for an app that signs in through the browser leaves the
"Connect Google Drive in Settings" button (``doors.failure_door_for``).
Measured before this fix: a Drive the user HAD signed in to got the same
answer — "Google Drive signs in through the browser — connect it from the
Connections page." plus the Connect button — on every such question.

Driven through the real app (``create_app``), the real registry, the real
``AppConnectTool`` and the real doors; the model is the only double (the 3B
harness), as in tests/test_integrate_wave3_v13530.py.
"""

from __future__ import annotations

import asyncio

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import run_chat_turn
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.providers.adapters.base import ToolCall
from iron_jarvis.settings import records
from tests.test_chat_guide_media_doors_v13530 import (  # the 3B harness, reused
    DRIVE_DOOR,
    PAIR_DOOR,
    _body,
    _done,
    _PostModel,
    _stream,
    _StreamModel,
    _clean_env,  # noqa: F401 — autouse: no developer key makes the install look connected
)


@pytest.fixture()
def app(tmp_path, monkeypatch):
    import iron_jarvis.daemon.routes.chat as chat_routes

    app = create_app(str(tmp_path))
    monkeypatch.setattr(chat_routes, "_ASK_POLL_S", 0.05)
    return app


def _sign_in_drive(platform) -> None:
    platform.connections._upsert(
        "google_drive", status="connected", method="oauth", account="me@example.com"
    )


def _finished(frames) -> list[dict]:
    return [d for ev, d in frames if ev == "tool_call" and d.get("status") == "finished"]


def _ask_drive(app) -> list:
    model = _StreamModel([[ToolCall(id="c1", name="app_connect", arguments={"app": "google_drive"})], []])
    app.state.platform.router.stream = model
    cards: list = []
    frames = _stream(app, _body("find my tax files in Google Drive"), cards=cards)
    assert "app_connect" in model.tools[0], "anti-vacuity: the sentence armed app_connect"
    assert cards == [], "the call changes nothing, so it never cards"
    return frames


def test_a_signed_in_drive_is_said_to_be_connected_and_leaves_no_connect_button(app):
    # CONTROL first, on the same app: Drive not signed in → the fix button.
    frames = _ask_drive(app)
    assert _done(frames)["doors"] == [DRIVE_DOOR]
    assert _finished(frames)[0]["ok"] is False

    _sign_in_drive(app.state.platform)
    frames = _ask_drive(app)
    finished = _finished(frames)
    assert finished and finished[0]["ok"] is True
    assert "already connected" in finished[0]["output"]
    assert "connect it from the Connections page" not in finished[0]["output"]
    assert "me@example.com" not in finished[0]["output"], "the account name never reaches the model"
    assert _done(frames)["doors"] == [], "no 'Connect Google Drive in Settings' on a signed-in Drive"


def test_a_signed_in_drive_in_the_post_lane_leaves_no_connect_button(app):
    platform = app.state.platform

    def ask() -> dict:
        platform.router = _PostModel(
            [[ToolCall(id="c1", name="app_connect", arguments={"app": "google_drive"})], []]
        )
        body = ChatBody(
            messages=[{"role": "user", "content": "find my tax files in Google Drive"}],
            tools=["app_connect"],
        )
        return asyncio.run(run_chat_turn(platform, {}, body))

    assert ask()["doors"] == [DRIVE_DOOR], "control: not signed in → the button"
    _sign_in_drive(platform)
    assert ask()["doors"] == []


def test_the_signed_in_check_is_the_registrys_and_never_raises():
    class _Platform:
        class connections:  # noqa: N801 — a stand-in attribute bag
            @staticmethod
            def status():
                return [{"provider": "google_drive", "connected": True}, {"provider": "dropbox", "connected": False}]

    assert records._oauth_signed_in(_Platform, "google_drive") is True
    assert records._oauth_signed_in(_Platform, "dropbox") is False
    assert records._oauth_signed_in(_Platform, "onedrive") is False

    class _Broken:
        class connections:  # noqa: N801
            @staticmethod
            def status():
                raise RuntimeError("database is locked")

    assert records._oauth_signed_in(_Broken, "google_drive") is False
    assert records._oauth_signed_in(object(), "google_drive") is False


def test_the_browser_answer_is_true_whether_or_not_a_browser_is_paired(app):
    model = _StreamModel([[ToolCall(id="c1", name="app_connect", arguments={"app": "browser"})], []])
    app.state.platform.router.stream = model
    frames = _stream(app, _body("connect my browser"))
    out = _finished(frames)[0]["output"]
    assert "set up in Settings › Connections › Browser" in out
    assert "turn on browser access" in out
    assert _done(frames)["doors"] == [PAIR_DOOR], "the SAME door a browser refusal leaves"


def test_the_tool_tells_a_real_model_the_drive_ids(app):
    """G41 needs the model to CALL app_connect for a drive; its description
    now names the three ids (it listed only MCP apps)."""
    spec = next(s for s in app.state.platform.registry.specs(["app_connect"]) if s["name"] == "app_connect")
    for cid in ("google_drive", "onedrive", "dropbox"):
        assert cid in spec["description"], cid
