"""Leap 05-b (R2.9): asking for a rule that something not set up yet would
start gets a plain answer and the page that sets it up.

* chat's ``reflex_create`` refuses, in words, a rule started by email while no
  mailbox listens, by a calendar event while no calendar is read, by Slack or
  by the phone while nothing listens there — and makes NOTHING; the reply
  carries the door to the page that sets it up (``daemon/doors``);
* the same rule is made once that source listens;
* the words never use "start" as a noun on screen (the master plan's calm
  note for R2.9: "a rule started by email").

Through the real stream lane (``/chat/stream``, the approval card answered)
and the registry.
"""

from __future__ import annotations

import asyncio
import re

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon import doors
from iron_jarvis.daemon.app import create_app
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.triggers import readiness
from tests.test_chat_approvals_v1187 import _drive_stream
from tests.test_settings_chat_tools_v1321 import _body, _done
from tests.test_settings_records_v1321 import _app, _approve_all

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(300)]

EMAIL_RULE = {"name": "Tax office mail", "source": "email", "match": "IRS", "action": "session",
              "task_template": "File it in the Tax folder"}


def _ctx(p, tmp_path) -> ToolContext:
    return ToolContext(workspace=tmp_path, session_id="chat", agent_run_id="chat",
                       config=p.config, event_bus=p.event_bus, engine=p.engine)


def _invoke(p, name: str, args: dict, ctx: ToolContext):
    return asyncio.run(p.registry.invoke(name, args, ctx, p.permissions, session_allow=[name]))


@pytest.mark.asyncio
async def test_an_email_rule_with_no_mailbox_that_listens_is_refused_with_the_door(tmp_path):
    """MUTATION (red): drop the readiness check from
    ``ReflexCreateTool.execute`` -> the rule is made and no door is left."""
    app = _app(tmp_path, [("reflex_create", EMAIL_RULE)])
    p = app.state.platform
    seen: list[str] = []
    frames = await _drive_stream(
        app, _body("when an email about a 1099 happens, start a session"),
        await _approve_all(app, seen),
    )
    assert p.reflex.list() == [], "nothing is made"
    done = _done(frames)
    assert done["config_cards"] == []
    fins = [d for ev, d in frames if ev == "tool_call" and d.get("status") == "finished"]
    (fin,) = [d for d in fins if d.get("tool", d.get("name")) == "reflex_create"]
    words = fin.get("error") or fin.get("output") or ""
    assert "A rule started by email needs a mailbox that listens. Open Notifications to add one" in words
    assert {"href": "/channels", "label": "Open Notifications in Settings"} in (done.get("doors") or [])


@pytest.mark.parametrize(
    "source,door",
    [
        ("calendar", {"href": "/reflex", "label": "Set up your calendar on Rules"}),
        ("slack", {"href": "/channels", "label": "Set up Slack in Settings"}),
        ("comm", {"href": "/channels", "label": "Open Notifications in Settings"}),
    ],
)
def test_calendar_slack_and_phone_rules_are_refused_until_they_listen(tmp_path, source, door):
    c = TestClient(create_app(str(tmp_path / "home")))
    p = c.app.state.platform
    r = _invoke(p, "reflex_create", {"name": "x", "source": source, "match": "standup", "action": "session"},
                _ctx(p, tmp_path))
    assert not r.ok and p.reflex.list() == []
    words, want_door = readiness.NOT_READY[source]
    assert r.error == words
    assert r.data["door"] == door == want_door
    assert doors.failure_door_for("reflex_create", {"source": source}, r) == door


def test_the_rule_is_made_once_its_source_listens(tmp_path, monkeypatch):
    c = TestClient(create_app(str(tmp_path / "home")))
    p = c.app.state.platform
    monkeypatch.setattr(readiness, "email_status",
                        lambda _p: {"enabled": True, "credentialed": True, "channels": ["mail"]})
    r = _invoke(p, "reflex_create", EMAIL_RULE, _ctx(p, tmp_path))
    assert r.ok, r.error
    (rule,) = p.reflex.list()
    assert (rule.source, rule.match) == ("email", "IRS")
    # GET /starts/kinds says the same thing the tool read.
    kinds = c.get("/starts/kinds").json()
    assert kinds["kinds"]["email"]["ready"] is True
    assert kinds["kinds"]["calendar"] == {"ready": False, **dict(zip(("words", "door"), readiness.NOT_READY["calendar"]))}


def test_a_door_is_never_built_from_the_tools_text(tmp_path):
    """A failed result can only open a page this module names."""
    from iron_jarvis.tools.base import ToolResult

    bad = ToolResult(ok=False, error="x", data={"door": {"href": "https://evil.example/", "label": "Click"}})
    assert doors.failure_door_for("reflex_create", {}, bad) is None
    odd = ToolResult(ok=False, error="x", data={"door": {"href": "/channels", "label": "Anything at all"}})
    assert doors.failure_door_for("reflex_create", {}, odd) == {"href": "/channels", "label": "Open Notifications in Settings"}


def test_the_words_never_use_start_as_a_noun():
    """The master plan's calm note for R2.9: never "start" as a noun on
    screen ("what starts a rule", "rules started by email")."""
    noun = re.compile(r"\b(?:an?|the|email|calendar|slack|phone|every|your|this)\s+starts?\b", re.IGNORECASE)
    for words, door in readiness.NOT_READY.values():
        assert not noun.search(words), words
        assert not re.search(r"\bstarts\b", words), words
        assert door["href"].startswith("/") and not door["href"].startswith("//")
