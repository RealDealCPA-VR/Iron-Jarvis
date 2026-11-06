"""v1.437.0 — Chat › Code's NEW-SESSION BOX: "Try on…" rides the FIRST message
of a session the box has just made.

The box (dashboard ``components/harness/HarnessStartComposer``) sends ONE
``POST /build/sessions`` carrying what its chips say — the folder, the branch
switch, the model, the account it was picked under, the effort, Plan, the
team, Local only — and then the first message carries ``try_on``. These pins
drive exactly that pair of requests through the REAL app factory, the real
routes, real git in a scratch repository and the real Iron-Proxy client
against the fake Iron-Proxy; only the models are the scripted doubles of
``tests/test_build_attempts_v1397.py``. Nothing reaches a real model, the
user's gateway or the running app.

Each pin is the daemon's answer the dashboard relies on:

  * a session made a moment ago (on its own branch, or working in its folder
    directly, with an account, an effort and Local only set AT CREATE) takes
    its very first message as attempts — the card lists exactly the picked
    models, the question is the session's first line and names it;
  * a session with NO folder refuses the picks in words BEFORE anything is
    written, so the box can take the empty session back
    (``DELETE …?only_if_empty=1``) and reopen with the words — and those
    words are about Try on (the box says them under itself, not on the
    folder chip);
  * a team set at create (the box's Team) does not stop the first message's
    picks: each attempt works alone.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import attempts as A
from tests.test_build_accounts_v1368 import enable, isolate, make_fake
from tests.test_build_attempts_v1397 import (
    CLAUDE,
    CODEX,
    GIT,
    LOCAL,
    World,
    card_of,
    make_calc_repo,
    passing_script,
    wire_models,
)
from tests.test_build_git_v1375 import no_real_gh
from tests.test_build_sessions_v1367 import asgi, frames_of, messages_of, open_turn, setup_of

pytestmark = pytest.mark.skipif(GIT is None, reason="git is not installed")


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    no_real_gh(monkeypatch)
    yield from isolate(tmp_path, monkeypatch)


@pytest.fixture
def fake(tmp_path):
    f = make_fake(tmp_path)
    yield f
    f.stop()


@pytest.fixture
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture
def repo(tmp_path) -> Path:
    return make_calc_repo(tmp_path / "proj")


async def start_box_create(app, body: dict) -> dict:
    """The box's ONE create (what `startCreateBody` sends)."""
    status, view = await asgi(app, "POST", "/build/sessions", body)
    assert status == 201, view
    return view


async def delete_if_empty(app, sid: str) -> int:
    """``DELETE /build/sessions/{sid}?only_if_empty=1`` in-process (the box's
    own take-back; the shared helper sends no query string)."""
    path = f"/build/sessions/{sid}"
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "DELETE", "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"only_if_empty=1",
        "root_path": "",
        "headers": [(b"host", b"127.0.0.1:8787"), (b"content-length", b"0")],
        "server": ("127.0.0.1", 8787), "client": ("127.0.0.1", 51234),
    }
    sent = {"done": False}

    async def receive():
        if not sent["done"]:
            sent["done"] = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    status = 0

    async def send(msg):
        nonlocal status
        if msg["type"] == "http.response.start":
            status = msg["status"]

    await app(scope, receive, send)
    return status


async def first_message(app, sid: str, text: str, picks: list[tuple[str, str]], turn_id: str):
    return await open_turn(
        app, sid, {"text": text, "turn_id": turn_id, "try_on": [{"provider": p, "model": m} for p, m in picks]},
        timeout=120.0,
    )


async def test_the_first_message_of_a_session_the_box_just_made_is_tried_on_the_picked_models(
    app, fake, repo, monkeypatch,
):
    await enable(app)
    world = wire_models(app, monkeypatch, World({"codex-cli": passing_script(), "ollama": ["Looked at it."]}))
    # Everything the box's chips can say, at create: its own branch, a model
    # picked under an account, an effort, a posture.
    view = await start_box_create(app, {
        "folder": str(repo), "branch": True, "provider": CLAUDE[0], "model": CLAUDE[1],
        "account": "c-spare", "reasoning": "high", "approval_mode": "yolo",
    })
    sid = view["id"]
    setup = setup_of(app, sid)
    assert setup.get("account") == "c-spare" and setup.get("reasoning") == "high"
    assert messages_of(app, sid) == [], "a just-made session holds nothing"
    status, raw = await first_message(app, sid, "fix add in calc.py", [CODEX, LOCAL], "sb-1")
    assert status == 200, raw
    card = card_of(frames_of(raw))
    assert [(i["provider"], i["model"]) for i in card["items"]] == [CODEX, LOCAL]
    assert {c["provider"] for c in world.calls} == {"codex-cli", "ollama"}
    # The question is the session's FIRST line, and it names the session.
    users = [m for m in messages_of(app, sid) if m.get("role") == "user"]
    assert [m.get("content") for m in users] == ["fix add in calc.py"]
    status, got = await asgi(app, "GET", f"/build/sessions/{sid}")
    assert status == 200 and got["title"] == "fix add in calc.py", got.get("title")


async def test_a_session_made_to_work_in_its_folder_directly_is_tried_on_too(app, fake, repo, monkeypatch):
    await enable(app)
    world = wire_models(app, monkeypatch, World({"ollama": ["Done."]}))
    view = await start_box_create(app, {"folder": str(repo), "branch": False, "approval_mode": "yolo"})
    status, raw = await first_message(app, view["id"], "fix add.", [LOCAL], "sb-2")
    assert status == 200, raw
    assert [i["provider"] for i in card_of(frames_of(raw))["items"]] == ["ollama"]
    assert {c["provider"] for c in world.calls} == {"ollama"}


async def test_local_only_set_at_create_holds_for_the_first_message_s_picks(app, fake, repo, monkeypatch):
    await enable(app)
    world = wire_models(app, monkeypatch, World({"ollama": ["Done."]}))
    view = await start_box_create(app, {"folder": str(repo), "approval_mode": "yolo", "local_only": True})
    sid = view["id"]
    assert setup_of(app, sid).get("local_only") is True
    # A cloud pick is refused in words and nothing is written (the box never
    # offers one under Local only; this is the daemon's own guard).
    status, raw = await first_message(app, sid, "fix add.", [CODEX], "sb-3")
    assert status == 400, raw
    assert "Local only" in json.loads(raw)["detail"]
    assert messages_of(app, sid) == [] and world.calls == []
    status, raw = await first_message(app, sid, "fix add.", [LOCAL], "sb-4")
    assert status == 200, raw
    assert [i["provider"] for i in card_of(frames_of(raw))["items"]] == ["ollama"]


async def test_picks_on_a_session_with_no_folder_are_refused_before_anything_is_written(app, fake, monkeypatch):
    await enable(app)
    world = wire_models(app, monkeypatch, World({}))
    view = await start_box_create(app, {"folder": "", "approval_mode": "yolo"})
    sid = view["id"]
    status, raw = await first_message(app, sid, "fix add.", [CODEX, LOCAL], "sb-5")
    assert status == 409, raw
    detail = json.loads(raw)["detail"]
    assert detail == A.NEEDS_GIT
    # The words are about Try on (the box says them under itself).
    assert detail.startswith("Trying on several models")
    assert world.calls == [] and messages_of(app, sid) == []
    # So the box can take the empty session back with the daemon's own guard.
    status = await delete_if_empty(app, sid)
    assert status in (200, 204), status
    status, _ = await asgi(app, "GET", f"/build/sessions/{sid}")
    assert status == 404


async def test_a_session_made_with_a_team_still_tries_its_first_message_on_the_picks(app, fake, repo, monkeypatch):
    """The box's Team and "Try on…" together: the team rides the create, the
    picks the first message — and each attempt works alone (v1.403.0)."""
    await enable(app)
    world = wire_models(app, monkeypatch, World({"ollama": ["Done."]}))
    app.state.platform.agents_registry.register(
        "glm-w", "You are glm.", [], provider="ollama", model="glm-local", roles=["code", "test", "read"],
    )
    view = await start_box_create(app, {
        "folder": str(repo), "approval_mode": "yolo", "team": {"workers": ["glm-w"], "reviewer": ""},
    })
    sid = view["id"]
    assert setup_of(app, sid).get("team") == {"workers": ["glm-w"], "reviewer": ""}
    status, raw = await first_message(app, sid, "fix add.", [LOCAL], "sb-6")
    assert status == 200, raw
    assert [i["provider"] for i in card_of(frames_of(raw))["items"]] == ["ollama"]
    assert {c["provider"] for c in world.calls} == {"ollama"}
