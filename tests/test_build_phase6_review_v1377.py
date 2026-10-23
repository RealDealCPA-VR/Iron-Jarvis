"""v1.377.0 review (IJ harness part 2, Phase 6): the review's daemon-side
pins that are not the environment, the git guard or the branch point.

* A REWOUND or BRANCHED question's "/" SKILL goes back in the box (the doer
  left it out: the saved question named the skill but not WHERE it came
  from — the folder's own harness skills or the user's — and the box needs
  both to send it again). A question now saves ``skillSource`` ("folder" |
  "yours"); the rewind preview / answer and a question branch's draft carry
  ``skill_source`` beside ``skill``. An older line with no source hands back
  no source (the screen then restores nothing rather than guess).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import sessions as S
from tests.test_build_sessions_v1367 import Script, asgi, asgi_get, messages_of, new_session, open_turn, wire
from tests.test_build_slash_at_v1373 import FOLDER_SKILL_MARK, _folder_skill


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    return folder


async def test_a_folder_skills_question_saves_where_the_skill_came_from(app, work):
    _folder_skill(work, "release-notes", FOLDER_SKILL_MARK)
    sid = new_session(app, work)["id"]
    wire(app, Script(["Notes written."]))
    status, raw = await open_turn(app, sid, {"text": "notes", "turn_id": "sk-f", "folder_skill": "release-notes"})
    assert status == 200, raw
    question = messages_of(app, sid)[0]
    assert question["skill"] == "release-notes" and question["skillSource"] == "folder"


def _lines(app, sid: str, extra: dict) -> None:
    S.append_lines(app.state.platform.engine, sid, [
        {"role": "user", "content": "first", "turnId": "t1"},
        {"role": "assistant", "content": "answer", "turnId": "t1"},
        {"role": "user", "content": "use the skill", "turnId": "t2", **extra},
        {"role": "assistant", "content": "done", "turnId": "t2"},
    ])


@pytest.mark.parametrize("source", ["folder", "yours"])
async def test_rewind_and_a_question_branch_hand_back_the_skill_and_its_source(app, work, source):
    sid = new_session(app, work)["id"]
    _lines(app, sid, {"skill": "tidy", "skillSource": source})
    status, preview = await asgi_get(app, f"/build/sessions/{sid}/rewind?to=t2")
    assert status == 200 and (preview["skill"], preview["skill_source"]) == ("tidy", source)
    status, view = await asgi(app, "POST", f"/build/sessions/{sid}/branch", {"turn_id": "t2", "at": "question"})
    assert status == 201, view
    assert (view["draft"]["skill"], view["draft"]["skill_source"]) == ("tidy", source)
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": "t2"})
    assert status == 200 and (out["skill"], out["skill_source"]) == ("tidy", source)


async def test_an_older_line_with_no_source_hands_back_none(app, work):
    sid = new_session(app, work)["id"]
    _lines(app, sid, {"skill": "tidy"})
    status, preview = await asgi_get(app, f"/build/sessions/{sid}/rewind?to=t2")
    assert status == 200 and preview["skill"] == "tidy" and preview["skill_source"] == ""
    _lines(app, sid, {"skill": "tidy", "skillSource": "elsewhere"})
    status, view = await asgi(app, "POST", f"/build/sessions/{sid}/branch", {"turn_id": "t2", "at": "question"})
    assert status == 201 and view["draft"]["skill_source"] == ""


# --------------------------------------------------------------------------- #
# Next step (follow-ups): the account pin, Local only, a refusal, a running turn
# --------------------------------------------------------------------------- #


class _Rec:
    """``router.complete`` double recording the pin, the hold and the Local
    only scope each call ran under; ``fail`` raises instead of answering."""

    def __init__(self, text: str = '["Run the tests?"]', fail: "Exception | None" = None) -> None:
        self.text, self.fail = text, fail
        self.calls: list[dict] = []

    async def __call__(self, **kw):
        from types import SimpleNamespace

        from iron_jarvis.iron_proxy.pin import current as current_pin
        from iron_jarvis.providers import egress
        from iron_jarvis.providers.adapters.base import LLMResponse
        from iron_jarvis.providers.hold import current_hold

        hold = current_hold()
        pin = current_pin()
        self.calls.append({**kw, "pin_id": getattr(pin, "profile_id", None),
                           "hold": (getattr(hold, "provider", None), getattr(hold, "model", None)),
                           "local_only": egress.current_scope().on})
        if self.fail is not None:
            raise self.fail
        return SimpleNamespace(provider=kw.get("provider"), model=kw.get("model"), reason="explicit",
                               response=LLMResponse(text=self.text, usage={"input_tokens": 3, "output_tokens": 2}))


async def _on(app) -> None:
    status, _ = await asgi(app, "PUT", "/settings/values", {"values": {"chat_followups": True}})
    assert status == 200


def _turn_lines(app, sid: str) -> None:
    S.append_lines(app.state.platform.engine, sid, [
        {"role": "user", "content": "add a test", "turnId": "t1"},
        {"role": "assistant", "content": "Added a test.", "turnId": "t1"},
    ])


async def test_followups_run_on_the_sessions_pinned_account(app, work, monkeypatch):
    from iron_jarvis.daemon.routes import build as build_routes
    from iron_jarvis.iron_proxy.pin import AccountPin

    await _on(app)
    sid = new_session(app, work)["id"]
    _turn_lines(app, sid)
    monkeypatch.setattr(build_routes, "turn_account",
                        lambda platform, tid, setup: (AccountPin(profile_id="acct-work", ij_provider="claude-cli",
                                                                 title="Work"), "claude-cli"))
    rec = _Rec()
    app.state.platform.router.complete = rec
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/followups", {})
    assert status == 200 and out["suggestions"] == ["Run the tests?"]
    assert rec.calls[0]["pin_id"] == "acct-work", "the call ran on the session's own account"
    assert rec.calls[0]["provider"] == "claude-cli" and rec.calls[0]["pin"] is True


async def test_a_local_only_session_asks_a_cloud_model_nothing(app, work):
    await _on(app)
    sid = new_session(app, work, local_only=True)["id"]
    _turn_lines(app, sid)
    rec = _Rec()
    app.state.platform.router.complete = rec
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/followups", {})
    assert status == 200 and out["suggestions"] == [] and out["reason"]
    assert rec.calls == [], "a local-only session's words never went to a cloud model"


async def test_a_local_only_session_on_a_local_model_asks_inside_the_local_only_scope(app, work, monkeypatch):
    from iron_jarvis.providers import egress

    await _on(app)
    sid = new_session(app, work, local_only=True)["id"]
    _turn_lines(app, sid)
    monkeypatch.setattr(egress, "stays_on_this_network", lambda *a, **k: True)
    rec = _Rec()
    app.state.platform.router.complete = rec
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/followups", {})
    assert status == 200 and out["suggestions"] == ["Run the tests?"]
    assert rec.calls[0]["local_only"] is True


async def test_a_model_failure_or_a_running_turn_gives_no_buttons_and_never_a_500(app, work):
    from iron_jarvis.daemon.routes import build as build_routes

    await _on(app)
    sid = new_session(app, work)["id"]
    _turn_lines(app, sid)
    rec = _Rec(fail=RuntimeError("Anthropic's safety check declined this request"))
    app.state.platform.router.complete = rec
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/followups", {})
    assert status == 200 and out["suggestions"] == [] and out["reason"]
    assert "safety" not in out["reason"], "the model's own words never reach the reason"
    rec2 = _Rec()
    app.state.platform.router.complete = rec2
    build_routes._STARTING.add(sid)
    try:
        status, out = await asgi(app, "POST", f"/build/sessions/{sid}/followups", {})
    finally:
        build_routes._STARTING.discard(sid)
    assert status == 200 and out["suggestions"] == [] and rec2.calls == []
