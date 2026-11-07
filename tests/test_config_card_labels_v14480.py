"""v1.448.0 (Wave A, A3) — a "Setting changed" card names the option, never
the wire value.

``settings/tools.value_label(row, value)`` is the one rule: an enum row's
option LABEL ("vscode" → "VS Code"), the AI service's plain name for
``default_provider`` ("claude-cli" → "Claude Code"), a theme of your own
colours in words, any other dict as its keys — and ``None`` for free text, a
number or a switch (the card's own words serve). The change tools put
``old_label`` / ``new_label`` on their ``config_change`` and both chat lanes'
ONE ``config_cards_from_result`` carry them onto the card; the reply's own
sentence says the names too.
"""

from __future__ import annotations

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import config_card_from_result, config_cards_from_result
from iron_jarvis.settings import schema
from iron_jarvis.settings.tools import value_label
from iron_jarvis.tools.base import ToolResult
from tests.test_chat_approvals_v1187 import _asgi_post, _drive_stream
from tests.test_settings_chat_tools_v1321 import _body, _calls_stream, _done


# ----------------------------------------------------------------- the rule


def test_an_enum_row_gives_the_options_label():
    row = schema.get("code_editor")
    assert value_label(row, "vscode") == "VS Code"
    assert value_label(row, "cursor") == "Cursor"
    assert value_label(row, "auto") == "Automatic"
    assert value_label(row, "VSCODE") == "VS Code"  # the value is matched case-blind
    assert value_label(row, "not-an-option") is None


def test_the_approval_level_and_the_theme_read_as_their_names():
    assert value_label(schema.get("device.approval_mode"), "approve_for_me") == "Ask before commands"
    assert value_label(schema.get("device.approval_mode"), "auto") == "Auto"
    assert value_label(schema.get("device.theme"), "mark1") == "Daylight"


def test_a_provider_row_gives_the_ai_services_plain_name():
    row = schema.get("default_provider")
    assert value_label(row, "claude-cli") == "Claude Code"
    assert value_label(row, "codex-cli") == "Codex"
    assert value_label(row, "anthropic") == "Anthropic"
    assert value_label(row, "mock") == "the demo model"


def test_a_dict_reads_as_its_keys_and_a_theme_of_your_own_in_words():
    row = schema.get("default_model")  # no options: the dict rule alone decides
    assert value_label(row, {"Work": ["a"], "Home": ["b"]}) == "Work, Home"
    assert value_label(row, {}) is None
    words = value_label(row, {"accent": "#3b82f6", "surface": "neutral", "mode": "dark"})
    assert isinstance(words, str) and words and "{" not in words


def test_free_text_a_number_a_switch_and_nothing_have_no_label():
    assert value_label(schema.get("default_model"), "claude-opus-4-8") is None
    assert value_label(schema.get("max_agent_steps"), 33) is None
    assert value_label(schema.get("autonomy_dry_run"), True) is None
    assert value_label(schema.get("default_model"), "") is None
    assert value_label(schema.get("default_model"), None) is None
    assert value_label(None, "x") is None  # never raises


# ------------------------------------------------------------- the card


def _result(change: dict) -> ToolResult:
    return ToolResult(ok=True, output="Changed.", data={"config_change": change})


def test_the_card_carries_the_labels_only_when_the_tool_gave_them():
    base = {"key": "code_editor", "label": "Code editor", "old": "auto", "new": "vscode", "restart": False, "change_id": "c1"}
    card = config_card_from_result("config_set", _result({**base, "old_label": "Automatic", "new_label": "VS Code"}))
    assert card["old_label"] == "Automatic" and card["new_label"] == "VS Code"
    assert card["old"] == "auto" and card["new"] == "vscode"  # the wire value stays (an Undo keys on it)
    # A free-text row's card has NO label keys at all: the v1.321.0 shape.
    plain = config_card_from_result("config_set", _result(base))
    assert "old_label" not in plain and "new_label" not in plain
    # An empty or non-string label is dropped, never shown as "".
    odd = config_card_from_result("config_set", _result({**base, "old_label": "", "new_label": 7}))
    assert "old_label" not in odd and "new_label" not in odd
    # Both lanes go through the ONE list builder.
    (one,) = config_cards_from_result("config_set", _result({**base, "new_label": "VS Code"}))
    assert one["new_label"] == "VS Code" and "old_label" not in one


# ------------------------------------------------ through the real lane


@pytest.mark.asyncio
async def test_an_enum_change_from_chat_cards_the_names_and_says_them(tmp_path):
    app = create_app(str(tmp_path))
    cfg = app.state.platform.config
    assert cfg.code_editor == "auto"
    app.state.platform.router.stream = _calls_stream([("config_set", {"key": "code_editor", "value": "vscode"})])

    async def never(data):  # pragma: no cover
        raise AssertionError(f"an allow-tier setting asked: {data}")

    frames = await _drive_stream(app, _body("open files in VS Code from now on"), never)
    done = _done(frames)
    (card,) = done["config_cards"]
    assert card["key"] == "code_editor" and card["old"] == "auto" and card["new"] == "vscode"
    assert card["old_label"] == "Automatic" and card["new_label"] == "VS Code"
    assert cfg.code_editor == "vscode"
    # The reply's own sentence names the options too, never the wire value.
    tool_frames = [d for ev, d in frames if ev == "tool_call" and d.get("status") == "finished"]
    said = " ".join(str(d.get("output") or "") for d in tool_frames)
    assert "Automatic → VS Code" in said and "auto → vscode" not in said


@pytest.mark.asyncio
async def test_a_provider_change_from_chat_names_the_ai_service(tmp_path):
    app = create_app(str(tmp_path))
    cfg = app.state.platform.config
    assert cfg.default_provider == "mock"
    app.state.platform.router.stream = _calls_stream([("config_change", {"key": "default_provider", "value": "claude-cli"})])

    async def approve(data):
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "once"})
        assert status == 200

    frames = await _drive_stream(app, _body("make Claude Code the default provider"), approve)
    (card,) = _done(frames)["config_cards"]
    assert card["key"] == "default_provider"
    assert card["old_label"] == "the demo model" and card["new_label"] == "Claude Code"
    assert cfg.default_provider == "claude-cli"


@pytest.mark.asyncio
async def test_a_free_text_change_from_chat_has_no_label_keys(tmp_path):
    app = create_app(str(tmp_path))
    app.state.platform.router.stream = _calls_stream([("config_change", {"key": "max_agent_steps", "value": "33"})])

    async def approve(data):
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "once"})
        assert status == 200

    frames = await _drive_stream(app, _body("change the max steps setting to 33"), approve)
    (card,) = _done(frames)["config_cards"]
    assert card["new"] == 33 and "old_label" not in card and "new_label" not in card
