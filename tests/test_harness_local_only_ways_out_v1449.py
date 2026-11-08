"""v1.449.0 (Wave B, G-N item 8) — a LOCAL ONLY refusal in Chat › Code has the
chat page's two ways out.

The chat page's refusal line offers "Choose a local model" and "Send to <model>
once" (v1.332.0); a coding session's failure line offered neither — it read
the refusal's sentence and a Retry that would only be refused again. The
daemon half, pinned here: ``routes/build.recorded_turn`` stamps the reply line
``errorKind: "local_only"`` (+ ``errorProvider``) for a ``LocalOnlyRefused``
(the stream lane's error frame, ``code: "local_only"``); ``BuildTurnBody.
allow_cloud_once`` rides the turn into ``ChatBody.allow_cloud_once`` (the chat
page's field: honoured for THIS turn, when it names the chosen provider); the
session view carries the apps holding the session local
(``local_only_holds``), so the screen offers no "Send once" there (the chat
page's rule). The screen half is ``dashboard/__tests__/harness-local-only-
ways-out-v1449.test.tsx``.
"""

from __future__ import annotations

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import sessions as S
from iron_jarvis.providers import egress as _egress
from iron_jarvis.providers.adapters.base import ProviderError
from tests.test_build_sessions_v1367 import (
    CLAUDE,
    Script,
    asgi_get,
    frames_of,
    messages_of,
    new_session,
    open_turn,
    wire,
)
from tests.test_harness_gaps_failure_receipt_v1389 import _Failing


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    return folder


def _reply(app, sid: str) -> dict:
    return [m for m in messages_of(app, sid) if m.get("role") == "assistant"][-1]


async def test_a_local_only_refusal_marks_the_line_with_its_kind_and_provider(app, work):
    sid = new_session(app, work)["id"]
    wire(app, _Failing(_egress.LocalOnlyRefused("openai", "gpt-6")))
    status, raw = await open_turn(app, sid, {"text": "Add a cart", "turn_id": "lo-refused"})
    assert status == 200, raw
    [err] = [d for ev, d in frames_of(raw) if ev == "error"]
    assert err["code"] == "local_only" and err["provider"] == "openai"
    line = _reply(app, sid)
    assert line["errorKind"] == "local_only"
    assert line["errorProvider"] == "openai"
    assert "local only" in line["error"].lower(), line["error"]
    assert line.get("errorTitle") in (None, ""), "a refusal has no separate headline"


async def test_an_ordinary_failure_keeps_no_local_only_kind(app, work):
    sid = new_session(app, work)["id"]
    wire(app, _Failing(ProviderError("openai API error 500: boom", status_code=500)))
    status, _raw = await open_turn(app, sid, {"text": "Add a cart", "turn_id": "lo-control"})
    assert status == 200
    line = _reply(app, sid)
    assert line.get("errorKind") != "local_only"
    assert "errorProvider" not in line or line["errorProvider"] != "openai" or line.get("errorKind") in ("sign_in", "key")


class _ScopeReader(Script):
    """A scripted model that records the Local only scope its call ran under."""

    def __init__(self) -> None:
        super().__init__(["Done."])
        self.scopes: list[dict] = []

    async def _next(self, system, messages, tools):
        scope = _egress.current_scope()
        self.scopes.append({"on": bool(scope.on), "allowed_once": str(getattr(scope, "allowed_once", "") or "")})
        return await super()._next(system, messages, tools)


async def test_send_once_rides_the_turn_as_allow_cloud_once(app, work):
    sid = new_session(app, work, local_only=True)["id"]
    script = wire(app, _ScopeReader())
    status, _raw = await open_turn(
        app, sid, {"text": "Add a cart", "turn_id": "lo-once", "allow_cloud_once": CLAUDE[0]},
    )
    assert status == 200
    assert script.scopes, "the model was never called"
    assert script.scopes[0] == {"on": True, "allowed_once": CLAUDE[0]}


async def test_a_once_naming_another_provider_is_ignored(app, work):
    sid = new_session(app, work, local_only=True)["id"]
    script = wire(app, _ScopeReader())
    status, _raw = await open_turn(
        app, sid, {"text": "Add a cart", "turn_id": "lo-other", "allow_cloud_once": "openai"},
    )
    assert status == 200
    if script.scopes:  # the lane may refuse the cloud pick before any call
        assert script.scopes[0]["allowed_once"] == ""


async def test_the_view_shows_the_apps_holding_the_session_local(app, work):
    sid = new_session(app, work, local_only=True)["id"]
    engine = app.state.platform.engine
    S.update(engine, sid, setup={"local_only_holds": ["microsoft_365"]})
    status, body = await asgi_get(app, f"/build/sessions/{sid}")
    assert status == 200, body
    assert body["setup"]["local_only"] is True
    assert body["setup"]["local_only_holds"] == ["microsoft_365"]
    # A session nobody holds carries no key (byte-identical to before).
    other = new_session(app, work)["id"]
    status, body = await asgi_get(app, f"/build/sessions/{other}")
    assert status == 200 and "local_only_holds" not in body["setup"]
