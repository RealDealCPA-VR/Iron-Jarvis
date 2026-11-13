"""A lookup's match the reply ALREADY holds takes its place, and the note
names it (v1.484.1).

Seen live: "Whenever an email arrives from the diocese treasurer, file it in
the diocese folder" had `reflex_create` armed already (the rule words bring it).
The lookup listed it second; the join skipped it as held and filled its three
places with the lower matches ("make a file", a folder's coding defaults), and
the note told the model "Now among your tools: redact_pii, config_change,
build_defaults_set. Call the one that fits": the one tool that did the job was
never named. Now a held match counts as one of the lookup's places and the note
says "Already among your tools in this reply: `reflex_create`."

v1.489.0 (moved on purpose): `webhook_update` brings its family's maker,
`webhook_add`, which takes no place (``MAKER_FOR``).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.capability_search import join as J
from iron_jarvis.capability_search import tool as CT_TOOL
from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMMessage

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(300)]

NAME = CT_TOOL.NAME


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    c = TestClient(create_app(str(tmp_path_factory.mktemp("home"))))
    c.app.state.platform.config.capability_search = True
    J.FOUND.clear()
    yield c.app
    J.FOUND.clear()
    try:
        c.app.state.platform.scheduler.shutdown()
    except Exception:  # noqa: BLE001
        pass


def _row(tool):
    return {"kind": "tool", "id": tool, "tool": tool}


def _join(app, rows, *, held=(), turn="turn-held-1"):
    p = app.state.platform
    ctx = SimpleNamespace(session_id="chat", turn_id=turn)
    J.FOUND.take(J.turn_key(ctx))
    J.FOUND.note(J.turn_key(ctx), rows)
    state = dict(
        tools_used=[NAME], armed=[NAME], auto_armed=[NAME], ask_armed=list(held),
        tool_specs=p.registry.specs([NAME, *held]), overrides={NAME: "allow", **{h: "ask" for h in held}},
        armed_grant={NAME},
        msgs=[LLMMessage(role="tool", tool_call_id="c1", name=NAME, content="found")], ctx=ctx,
        found_asks=set(),
    )
    body = SimpleNamespace(auto_tools=True, granted_tools=[])
    joined = CT.join_found_tools(SimpleNamespace(platform=p), body, can_ask=True, **state)
    return joined, state


ROWS = [_row("webhook_update"), _row("reflex_create"), _row("redact_pii"),
        _row("build_defaults_set"), _row("goal_add")]


def test_a_held_match_takes_its_place_and_is_named(app):
    joined, state = _join(app, ROWS, held=("reflex_create",))
    # Three places: webhook_update, reflex_create (held), redact_pii. The two
    # lower matches no longer fill in for the held one.
    assert joined == ["webhook_update", "webhook_add", "redact_pii"], joined
    note = state["msgs"][-1].content
    assert "Already among your tools in this reply: `reflex_create`." in note
    assert "build_defaults_set" not in note and "goal_add" not in note


def test_nothing_held_joins_as_before(app):
    joined, state = _join(app, ROWS, turn="turn-held-2")
    assert joined == ["webhook_update", "webhook_add", "reflex_create", "redact_pii"], joined
    assert "Already among your tools" not in state["msgs"][-1].content


def test_held_settings_tools_take_one_place_together(app):
    rows = [{"kind": "setting", "id": "text_size", "tool": "config_set"},
            {"kind": "setting", "id": "theme", "tool": "config_change"},
            _row("agent_create"), _row("webhook_update"), _row("redact_pii")]
    joined, state = _join(app, rows, held=("config_set", "config_list", "config_change"), turn="turn-held-3")
    # The settings family (all held) holds ONE place; two tool matches join.
    assert joined == ["agent_create", "webhook_update", "webhook_add"], joined
    # The family is not listed as "already" (it is the settings reader and writers).
    assert "Already among your tools" not in state["msgs"][-1].content


def test_a_held_match_alone_never_asks_to_look_again(app):
    rows = [_row("reflex_create")]
    joined, state = _join(app, rows, held=("reflex_create",), turn="turn-held-4")
    note = state["msgs"][-1].content
    assert joined == [] and "`reflex_create`" in note and J.LOOK_AGAIN not in note


def test_a_held_match_beside_a_tool_that_never_joins_is_no_reason_to_look_again(app):
    rows = [_row("reflex_create"), _row("shell")]
    joined, state = _join(app, rows, held=("reflex_create",), turn="turn-held-5")
    note = state["msgs"][-1].content
    assert joined == [] and "`shell` did not join" in note
    assert "`reflex_create`" in note and J.LOOK_AGAIN not in note


def test_only_a_held_match_within_the_lookups_places_is_named(app):
    rows = [_row("webhook_update"), _row("redact_pii"), _row("goal_add"), _row("reflex_create")]
    joined, state = _join(app, rows, held=("reflex_create",), turn="turn-held-6")
    assert joined == ["webhook_update", "webhook_add", "redact_pii", "goal_add"], joined
    assert "Already among your tools" not in state["msgs"][-1].content
