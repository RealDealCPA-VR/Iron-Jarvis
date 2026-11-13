"""A setting asked for without a clear value is PICKED on a card (v1.489.0).

Seen live on the practice set: "Can the app look different? Maybe a dark
background, or a soft blue, whatever is easiest on the eyes" and "I would like
the good one to be the one that answers me every time" each found the right
setting and then ended on a list of the choices in words. The user had to
answer in chat and the next reply had to find the setting all over again.

Now the settings tools may be called with the key and no value:

* a setting with FIXED CHOICES shows them on a question card (the same card a
  missing address uses, `tools/details`), and the pick is the call's value;
* the default model shows the models that can be chosen now, each with its AI
  service; a pick on another service sets both rows (and so asks first);
* any other setting called without a value shows no card and says what is
  missing;
* the pick goes through the SAME tier rule as a typed value: a protected
  choice still needs the protected tool and its approval card.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.mcp.interact import elicitation_fields
from iron_jarvis.settings import tools as ST
from iron_jarvis.tools import details as D
from iron_jarvis.tools.base import ToolContext
from tests.test_settings_chat_tools_v1321 import _done
from tests.test_tool_details_v1489 import _app, _finished, _run

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(300)]

CHANGE_TOOLS = ("config_set", "config_change", "config_change_protected")
ASK = "change the update channel setting"

ROWS = [
    {"provider": "mock", "model": "demo", "label": "Demo", "available": True},
    {"provider": "mock", "model": "quick", "label": "Quick", "available": True},
    {"provider": "claude-cli", "model": "sonnet", "label": "Sonnet 5.5", "available": True},
    {"provider": "claude-cli", "model": "haiku", "label": "Haiku 4.5", "available": True},
    {"provider": "openai", "model": "gpt-6", "label": "gpt-6", "available": False},
    {"provider": "claude-cli", "model": "sonnet", "label": "Sonnet 5.5", "available": True},
]


def _ctx(p, tmp_path):
    return ToolContext(workspace=tmp_path, session_id="chat", agent_run_id="chat", config=p.config,
                       event_bus=p.event_bus, engine=p.engine)


# =========================================================================== #
# What the model is shown, and which calls ask
# =========================================================================== #
def test_the_model_may_leave_the_value_out_and_is_told_when(tmp_path):
    reg = create_app(str(tmp_path / "ij")).state.platform.registry
    for name in CHANGE_TOOLS:
        tool = reg.get(name)
        spec = tool.spec()
        assert spec["input_schema"].get("required") == ["key"], name
        assert "WITHOUT `value`" in spec["description"] and "picks one on a card" in spec["description"], name
        assert "NOT clear" in spec["description"], "only when the value is not clear: a clear one is just set"
        assert tool.input_schema["required"] == ["key", "value"], "the tool's own schema is untouched"
    assert "WITHOUT" not in reg.get("config_list").spec()["description"]


def test_only_a_setting_with_fixed_choices_asks(tmp_path):
    tool = create_app(str(tmp_path / "ij")).state.platform.registry.get("config_set")
    q = tool.missing_details({"key": "update_channel"})
    assert list(q) == ["value"]
    assert q["value"]["enum"] == ["fresh", "stable"]
    assert q["value"]["enumNames"] == ["Fresh (every release)", "Stable (out for a day first)"]
    assert q["value"]["title"] == "Update channel"
    assert tool.missing_details({"key": "update_channel", "value": "  "}) == q
    # A value was given: nothing to ask.
    assert tool.missing_details({"key": "update_channel", "value": "stable"}) == {}
    assert tool.missing_details({"key": "chat_followups", "value": False}) == {}
    # No fixed choices (a number, a switch, free text), no key, an unknown key.
    for args in ({"key": "max_agent_steps"}, {"key": "chat_followups"}, {"key": "backup_mirror_dir"},
                 {"key": "no_such_setting"}, {}, {"key": None}):
        assert tool.missing_details(args) == {}, args
    # The card is one the chat can draw, with the choices' own names.
    fields, reason = elicitation_fields(D.ask_params(tool, q))
    assert not reason and len(fields) == 1
    assert fields[0]["enum"] == ["fresh", "stable"] and fields[0]["title"] == "Update channel"


def test_the_join_note_says_it_once_for_the_settings_tools(tmp_path):
    from iron_jarvis.capability_search import join as J

    reg = create_app(str(tmp_path / "ij")).state.platform.registry
    said = J._asks_itself(reg, ["config_set", "config_change", "config_list", "model_server_add"])
    lines = list(said.values())
    assert len(lines) == 2, lines
    settings = next(s for s in lines if "config_set" in s)
    assert "`config_set`, `config_change`: " in settings and "WITHOUT `value`" in settings
    assert "which value to use when you leave it out" not in settings, "never the general sentence: most settings do not ask"
    assert any(s.startswith("`model_server_add` asks the user itself") for s in lines)


# =========================================================================== #
# The real stream lane
# =========================================================================== #
@pytest.mark.asyncio
async def test_the_user_picks_on_a_card_and_the_setting_is_changed(tmp_path):
    app = _app(tmp_path, [("config_set", {"key": "update_channel"})])
    p = app.state.platform
    assert p.config.update_channel == "fresh"
    frames, order = await _run(app, ASK, answer={"value": "stable"})
    assert [k for k, _ in order] == ["question"], "an allow-tier setting: the pick is the whole decision"
    q = order[0][1]
    assert q["pack"] == "Iron Jarvis"
    assert q["fields"][0]["enum"] == ["fresh", "stable"] and q["fields"][0]["title"] == "Update channel"
    assert p.config.update_channel == "stable"
    done = _finished(frames, "config_set")
    assert len(done) == 1 and done[0]["ok"] is True
    cards = _done(frames)["config_cards"]
    assert len(cards) == 1 and cards[0]["key"] == "update_channel" and cards[0]["new"] == "stable"


@pytest.mark.asyncio
async def test_decline_changes_nothing(tmp_path):
    app = _app(tmp_path, [("config_set", {"key": "update_channel"})])
    frames, order = await _run(app, ASK, answer=None)
    assert [k for k, _ in order] == ["question"]
    assert app.state.platform.config.update_channel == "fresh"
    done = _finished(frames, "config_set")
    assert len(done) == 1 and done[0]["ok"] is False
    assert _done(frames)["config_cards"] == []


def test_a_choice_that_is_not_one_of_them_is_no_answer(tmp_path):
    # (The card's own route refuses such an answer with a 400 before it gets
    # here; this is the second lock.)
    tool = create_app(str(tmp_path / "ij")).state.platform.registry.get("config_set")
    q = tool.missing_details({"key": "update_channel"})["value"]
    assert D._clean("nightly", q) is None
    assert D._clean(" stable ", q) == "stable"


@pytest.mark.asyncio
async def test_a_protected_pick_still_needs_the_protected_tool_and_its_card(tmp_path):
    # Fresh is the protected choice of this setting (it widens what reaches
    # the PC). Picked on the card through the allow tool: refused, naming the
    # right tool AND the key and value (the model's own call held no value).
    app = _app(tmp_path, [
        ("config_set", {"key": "update_channel"}),
        ("config_change_protected", {"key": "update_channel", "value": "fresh"}),
    ])
    p = app.state.platform
    p.config.update_channel = "stable"
    frames, order = await _run(app, ASK, answer={"value": "fresh"})
    assert [k for k, _ in order] == ["question", "approval"], order
    first = _finished(frames, "config_set")
    assert len(first) == 1 and first[0]["ok"] is False
    words = str(first[0])
    assert "config_change_protected" in words and "key 'update_channel', value fresh" in words
    assert order[1][1]["tool"] == "config_change_protected"
    assert order[1][1]["args"] == {"key": "update_channel", "value": "fresh"}
    assert p.config.update_channel == "fresh"


@pytest.mark.asyncio
async def test_a_setting_with_no_fixed_choices_shows_no_card(tmp_path):
    app = _app(tmp_path, [("config_set", {"key": "max_agent_steps"})])
    before = app.state.platform.config.max_agent_steps
    frames, order = await _run(app, "change the max agent steps setting", answer={"value": "3"})
    assert order == []
    done = _finished(frames, "config_set")
    assert len(done) == 1 and done[0]["ok"] is False and "missing required: value" in str(done[0])
    assert app.state.platform.config.max_agent_steps == before


@pytest.mark.asyncio
async def test_a_chat_that_cannot_show_the_card_is_told_to_ask_in_words(tmp_path):
    app = _app(tmp_path, [("config_set", {"key": "update_channel"})])
    frames, order = await _run(app, ASK, answer={"value": "stable"}, cards=False)
    assert order == []
    done = _finished(frames, "config_set")
    assert len(done) == 1 and done[0]["ok"] is False
    assert "Ask the user for it in words" in str(done[0]) and "Update channel" in str(done[0])
    assert app.state.platform.config.update_channel == "fresh"


# =========================================================================== #
# The default model
# =========================================================================== #
def _with_models(tmp_path):
    app = create_app(str(tmp_path / "ij"))
    p = app.state.platform
    p._model_catalog = SimpleNamespace(_entry=SimpleNamespace(rows=ROWS))
    return app, p


def test_the_default_model_card_offers_the_models_that_can_be_chosen_now(tmp_path):
    _app_, p = _with_models(tmp_path)
    tool = p.registry.get("config_set")
    q = tool.missing_details({"key": "default_model"})["value"]
    assert q["enum"] == ["claude-cli::sonnet", "claude-cli::haiku"], q["enum"]
    assert not any(v.startswith("mock::") for v in q["enum"]), "the demo model is never offered"
    assert not any("gpt-6" in v for v in q["enum"]), "a model that is not available is never offered"
    assert len(q["enum"]) == len(set(q["enum"])), "each model once"
    assert q["enumNames"][q["enum"].index("claude-cli::sonnet")] == "Sonnet 5.5 (Claude Code)"
    # Nothing kept yet: no card (the model is told what is missing instead).
    p._model_catalog = SimpleNamespace(_entry=None)
    assert tool.missing_details({"key": "default_model"}) == {}
    # One model only: nothing to pick between.
    p._model_catalog = SimpleNamespace(_entry=SimpleNamespace(rows=ROWS[2:3]))
    assert tool.missing_details({"key": "default_model"}) == {}


def test_a_pick_writes_the_model_and_its_service_only_when_it_differs(tmp_path):
    _app_, p = _with_models(tmp_path)
    tool = p.registry.get("config_set")
    current = p.config.default_provider
    assert tool._writes("default_model", f"{current}::quick") == {"default_model": "quick"}
    assert tool._writes("default_model", "claude-cli::sonnet") == {
        "default_provider": "claude-cli", "default_model": "sonnet"}
    # A plain model id, another key, or a broken pick: exactly one row.
    assert tool._writes("default_model", "sonnet") == {"default_model": "sonnet"}
    assert tool._writes("default_model", "::sonnet") == {"default_model": "::sonnet"}
    assert tool._writes("default_persona", "a::b") == {"default_persona": "a::b"}


@pytest.mark.asyncio
async def test_a_model_on_the_same_service_is_set_without_a_card(tmp_path):
    _app_, p = _with_models(tmp_path)
    ctx = _ctx(p, tmp_path)
    current = p.config.default_provider
    out = await p.registry.get("config_set").execute({"key": "default_model", "value": f"{current}::quick"}, ctx)
    assert out.ok, out.error
    assert p.config.default_model == "quick" and p.config.default_provider == current
    assert out.data["config_change"]["key"] == "default_model" and out.data["config_change"]["new"] == "quick"


@pytest.mark.asyncio
async def test_a_model_on_another_service_asks_first_and_sets_both_and_undo_puts_both_back(tmp_path):
    _app_, p = _with_models(tmp_path)
    ctx = _ctx(p, tmp_path)
    before = (p.config.default_provider, p.config.default_model)
    assert before[0] != "claude-cli"
    args = {"key": "default_model", "value": "claude-cli::sonnet"}
    # Through the allow tool: refused, naming the tool that asks first.
    refused = await p.registry.get("config_set").execute(dict(args), ctx)
    assert not refused.ok and "config_change" in refused.error
    assert "key 'default_model', value claude-cli::sonnet" in refused.error
    assert (p.config.default_provider, p.config.default_model) == before
    # Through the asking tool: both rows, and the Undo copy holds both.
    tool = p.registry.get("config_change")
    undo = await tool.capture_undo(dict(args), ctx)
    prior = json.loads(undo["pre_inline"])["prior"]
    assert prior == {"default_provider": before[0], "default_model": before[1]}
    out = await tool.execute(dict(args), ctx)
    assert out.ok, out.error
    assert (p.config.default_provider, p.config.default_model) == ("claude-cli", "sonnet")
    assert "Changed Default model" in out.output and "Also changed Default AI" in out.output
    assert out.data["config_change"]["key"] == "default_model"


@pytest.mark.asyncio
async def test_the_model_pick_card_in_the_real_lane(tmp_path):
    app = _app(tmp_path, [
        ("config_set", {"key": "default_model"}),
        ("config_change", {"key": "default_model", "value": "claude-cli::haiku"}),
    ])
    p = app.state.platform
    p._model_catalog = SimpleNamespace(_entry=SimpleNamespace(rows=ROWS))
    frames, order = await _run(app, "change the default model setting", answer={"value": "claude-cli::haiku"})
    assert [k for k, _ in order] == ["question", "approval"], order
    assert "claude-cli::haiku" in order[0][1]["fields"][0]["enum"]
    assert order[1][1]["tool"] == "config_change"
    assert (p.config.default_provider, p.config.default_model) == ("claude-cli", "haiku")
