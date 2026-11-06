"""v1.442.0 (chat-first plan, Wave 7 group C): A THEME OF YOUR OWN COLOURS,
FROM CHAT.

The walkthrough's request 12, "Change the theme to something green and calm",
had nowhere to land: ``device.theme`` takes only the built-in themes (none is
green) and the theme maker was a screen-only form. ``device.theme_colors`` is
the theme maker's picks as one per-device setting, through the existing
machinery: the schema row, THE writer (validation, the linked-row rule, the
journal), ``config_list`` / ``config_set`` and the real stream lane, and the
normal ``setting_restore`` Undo. The screen side (building the theme with the
theme maker's own generator) is pinned in
``dashboard/__tests__/wave7-theme-colors-v1442.test.tsx``.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import UndoJournal
from iron_jarvis.daemon.app import create_app
from iron_jarvis.settings import schema, theme_colors
from iron_jarvis.settings.trigger import settings_ask
from iron_jarvis.settings.writer import SettingError
from iron_jarvis.tools.base import ToolContext
from tests.test_chat_approvals_v1187 import _drive_stream
from tests.test_chat_arming_table_v14340 import S, SET, WRITERS, check, lanes
from tests.test_settings_chat_tools_v1321 import _body, _calls_stream, _done, _undo_card

KEY = "device.theme_colors"
DEV = "dev_wave7th1"
GREEN = {"accent": "#2F855A", "surface": "tinted", "mode": "dark", "name": "Green and calm"}
GREEN_CLEAN = {"accent": "#2f855a", "surface": "tinted", "mode": "dark", "name": "Green and calm"}


@pytest.fixture
def app(tmp_path):
    a = create_app(str(tmp_path))
    TestClient(a)
    return a


@pytest.fixture
def writer(app):
    return app.state.d.platform.config_writer


# ------------------------------------------------------------------ the row


def test_the_row_is_a_per_device_allow_tier_dict_in_appearance():
    d = schema.get(KEY)
    assert (d.type, d.store, d.tier, d.group) == ("dict", "device", "allow", "appearance")
    assert d.normalize is theme_colors.normalize
    # The help says what each field means, so the model can pick colours.
    for word in ("accent", "#rrggbb", "surface", "neutral", "tinted", "mode", "dark", "light", "readable"):
        assert word in d.help.lower(), word
    # The built-in row points at it.
    assert KEY in schema.get("device.theme").help


def test_the_schema_route_and_the_device_route_serve_the_row(app):
    c = TestClient(app)
    rows = {r["key"]: r for r in c.get("/settings/schema").json()["settings"]}
    assert rows[KEY]["type"] == "dict" and rows[KEY]["store"] == "device"
    values = c.get(f"/settings/device?device_id={DEV}").json()["values"]
    assert KEY in values and values[KEY] is None


# ------------------------------------------------------------ validation


def test_normalize_gives_one_canonical_form():
    assert theme_colors.normalize(GREEN) == GREEN_CLEAN
    assert theme_colors.normalize({"accent": "0f0"}) == {"accent": "#00ff00", "surface": "neutral", "mode": "dark"}
    assert theme_colors.normalize({"accent": "#123456", "second": "#ABCDEF", "surface": "warm", "mode": "system"}) == {
        "accent": "#123456", "second": "#abcdef", "surface": "warm", "mode": "system",
    }
    # An empty second colour / name is "none".
    assert "second" not in theme_colors.normalize({"accent": "#123456", "second": "", "name": "  "})
    # Control characters and runs of spaces in a name are cleaned.
    assert theme_colors.normalize({"accent": "#123456", "name": "Sea\nside   calm"})["name"] == "Sea side calm"


@pytest.mark.parametrize(
    "bad, words",
    [
        ({"surface": "tinted"}, "need a main colour"),
        ({"accent": "green"}, "#rrggbb"),
        ({"accent": "#12345"}, "#rrggbb"),
        ({"accent": "#123456", "second": "blue"}, "second colour"),
        ({"accent": "#123456", "surface": "glossy"}, "neutral, cool, warm, tinted"),
        ({"accent": "#123456", "mode": "night"}, "dark, light, system"),
        ({"accent": "#123456", "name": "x" * 41}, "40 characters"),
        ({"accent": "#123456", "name": 5}, "name written as text"),
        ({"accent": "#123456", "font": "Inter"}, "not font"),
        ("green", "set of name: value pairs"),
    ],
)
def test_the_writer_refuses_a_bad_value_in_plain_words_and_changes_nothing(writer, bad, words):
    writer.apply({KEY: GREEN}, actor="test", device_id=DEV)
    with pytest.raises(SettingError) as exc:
        writer.apply({KEY: bad}, actor="test", device_id=DEV)
    msg = str(exc.value)
    assert msg.startswith("Your own theme colours"), msg
    assert words.lower() in msg.lower(), msg
    assert writer.current(KEY, device_id=DEV) == GREEN_CLEAN


def test_the_writer_stores_the_canonical_form(writer):
    writer.apply({KEY: GREEN}, actor="test", device_id=DEV)
    assert writer.current(KEY, device_id=DEV) == GREEN_CLEAN


# ------------------------------------------------- one theme at a time


def test_colours_clear_a_built_in_theme_and_a_built_in_theme_clears_colours(writer):
    writer.apply({"device.theme": "mark2"}, actor="test", device_id=DEV)
    out = writer.apply({KEY: GREEN}, actor="test", device_id=DEV)
    assert writer.current("device.theme", device_id=DEV) is None
    assert writer.current(KEY, device_id=DEV) == GREEN_CLEAN
    assert set(out.updated) == {KEY, "device.theme"}
    assert [c["key"] for c in out.changed] == [KEY, "device.theme"]  # the asked key first
    out = writer.apply({"device.theme": "mark1"}, actor="test", device_id=DEV)
    assert writer.current(KEY, device_id=DEV) is None
    assert writer.current("device.theme", device_id=DEV) == "mark1"
    assert set(out.updated) == {"device.theme", KEY}


def test_a_built_in_theme_with_no_colours_set_touches_only_its_row(writer):
    out = writer.apply({"device.theme": "mark8"}, actor="test", device_id=DEV)
    assert out.updated == ["device.theme"]


def test_both_rows_named_in_one_change_are_taken_as_given(writer):
    writer.apply({"device.theme": "mark2"}, actor="test", device_id=DEV)
    writer.apply({"device.theme": None, KEY: GREEN}, actor="test", device_id=DEV)
    assert writer.current("device.theme", device_id=DEV) is None
    assert writer.current(KEY, device_id=DEV) == GREEN_CLEAN


def test_the_rule_is_per_device(writer):
    writer.apply({"device.theme": "mark23"}, actor="test", device_id="dev_other01")
    writer.apply({KEY: GREEN}, actor="test", device_id=DEV)
    assert writer.current("device.theme", device_id="dev_other01") == "mark23"


def test_the_journaled_undo_puts_both_rows_back(app, writer):
    writer.apply({"device.theme": "mark2"}, actor="test", device_id=DEV)
    out = writer.apply({KEY: GREEN}, actor="test", device_id=DEV)
    with session_scope(app.state.platform.engine) as db:
        row = db.exec(select(UndoJournal).where(UndoJournal.action_id == out.action_id)).one()
    assert json.loads(row.pre_inline)["prior"] == {KEY: None, "device.theme": "mark2"}
    r = TestClient(app).post(f"/undo/{out.action_id}")
    assert r.status_code == 200, r.text
    assert writer.current("device.theme", device_id=DEV) == "mark2"
    assert writer.current(KEY, device_id=DEV) is None


# ---------------------------------------------------- the chat's tools


def _ctx(app, tmp_path, device_id=DEV):
    p = app.state.platform
    return ToolContext(
        workspace=tmp_path, session_id="chat", agent_run_id="chat", device_id=device_id,
        config=p.config, event_bus=p.event_bus, engine=p.engine,
    )


def test_config_list_shows_the_row_and_tells_the_model_its_shape(app, tmp_path):
    reg = app.state.platform.registry
    res = asyncio.run(reg.get("config_list").execute({"query": "theme"}, _ctx(app, tmp_path)))
    keys = [r["key"] for r in res.data["settings"]]
    assert "device.theme" in keys and KEY in keys
    line = next(ln for ln in res.output.splitlines() if f"({KEY})" in ln)
    assert "config_set" in line and '"accent"' in line and "surface" in line and "mode" in line
    assert "readable" in line


def test_config_set_applies_and_says_it_in_words(app, tmp_path, writer):
    writer.apply({"device.theme": "mark2"}, actor="test", device_id=DEV)
    reg = app.state.platform.registry
    res = asyncio.run(reg.get("config_set").execute({"key": KEY, "value": json.dumps(GREEN)}, _ctx(app, tmp_path)))
    assert res.ok, res.error
    assert writer.current(KEY, device_id=DEV) == GREEN_CLEAN
    assert writer.current("device.theme", device_id=DEV) is None
    # In words, never a Python dict.
    assert "Green and calm (main colour #2f855a, tinted background, dark)" in res.output
    assert "{" not in res.output


def test_config_set_without_a_screen_is_refused(app, tmp_path):
    reg = app.state.platform.registry
    res = asyncio.run(reg.get("config_set").execute({"key": KEY, "value": GREEN}, _ctx(app, tmp_path, "")))
    assert not res.ok and "per device" in res.error


def test_the_tools_undo_snapshot_holds_the_row_it_clears(app, tmp_path, writer):
    writer.apply({"device.theme": "mark8"}, actor="test", device_id=DEV)
    tool = app.state.platform.registry.get("config_set")
    snap = asyncio.run(tool.capture_undo({"key": KEY, "value": GREEN}, _ctx(app, tmp_path)))
    assert json.loads(snap["pre_inline"])["prior"] == {KEY: None, "device.theme": "mark8"}
    # And the other way round.
    writer.apply({KEY: GREEN}, actor="test", device_id=DEV)
    snap = asyncio.run(tool.capture_undo({"key": "device.theme", "value": "mark1"}, _ctx(app, tmp_path)))
    assert json.loads(snap["pre_inline"])["prior"] == {"device.theme": None, KEY: GREEN_CLEAN}
    # Nothing to clear: only the row itself.
    snap = asyncio.run(tool.capture_undo({"key": KEY, "value": None}, _ctx(app, tmp_path)))
    assert list(json.loads(snap["pre_inline"])["prior"]) == [KEY]


@pytest.mark.asyncio
async def test_the_stream_lane_sets_the_colours_with_no_card_and_its_undo_restores_the_theme(tmp_path):
    app = create_app(str(tmp_path))
    w = app.state.platform.config_writer
    w.apply({"device.theme": "mark23"}, actor="test", device_id=DEV)
    app.state.platform.router.stream = _calls_stream([("config_set", {"key": KEY, "value": GREEN})])

    async def never(data):  # pragma: no cover
        raise AssertionError(f"an allow-tier setting asked: {data}")

    frames = await _drive_stream(
        app, _body("change the theme to something green and calm", device_id=DEV), never
    )
    (card,) = _done(frames)["config_cards"]
    assert card["key"] == KEY and card["new"] == GREEN_CLEAN and card["change_id"]
    assert w.current(KEY, device_id=DEV) == GREEN_CLEAN
    assert w.current("device.theme", device_id=DEV) is None
    await _undo_card(app, card)
    assert w.current(KEY, device_id=DEV) is None
    assert w.current("device.theme", device_id=DEV) == "mark23"


def test_a_claim_that_the_theme_changed_is_backed_by_the_colours_card():
    from iron_jarvis.daemon.change_claims import claimed_change_note

    note = claimed_change_note(
        "I changed your theme to a calm green.", ["config_set"],
        [{"kind": "change", "key": KEY, "label": "Your own theme colours", "old": None, "new": GREEN_CLEAN}],
    )
    assert note == ""


# ---------------------------------------------------------- the trigger

#: Requests: the reader AND the writers, through the REAL arming of both lanes.
YES = [
    "change the theme to something green and calm",
    "make the app purple",
    "use a dark blue theme",
    "switch to a green theme",
    "give me a calm green look",
    "make my own theme with teal",
    "set a custom theme",
    "change the theme colors to orange",
    "turn Jarvis a calm green",
    "make the app look more purple",
    "I want a soft pink theme",
    "use a teal colour scheme",
    "pick a warm orange palette",
    "create a new colour theme with emerald",
    "set the theme colours to navy and gold",
    "make the dashboard green",
    "go with a muted sage theme",
    "please use a dark green theme for the app",
]
#: Near-misses: no settings or record writer.
NO = [
    "the green theme in my slides",
    "a calm color for the logo",
    "the theme of the essay",
    "pick a green for the logo",
    "what's the theme of this book",
    "make my slides purple",
    "use a dark blue theme for my presentation",
    "change the colour of the chart to green",
    "paint the wall green",
    "make the button purple in my css",
    "pick a calm palette for my website",
    "set a custom theme for my blog",
    "I need a green theme for the party invitations",
    "make the logo green",
    "use green and blue in the chart",
    "create a new theme for my wordpress site",
    "give me a warm color scheme for the living room",
    "choose a purple look for the wedding",
    "the app is green on my phone",
    "my favourite colour is green",
    "make my own theme with my logo",
    "use a dark theme with my brand fonts",
]


@pytest.fixture(scope="module")
def d(tmp_path_factory):
    home = tmp_path_factory.mktemp("w7arming") / "home"
    return SimpleNamespace(platform=create_app(str(home)).state.platform)


@pytest.mark.parametrize("text", YES)
def test_a_request_for_your_own_colours_arms_config_set_in_both_lanes(d, text):
    assert settings_ask(text).writers, text
    assert check(d, text, SET, (S,)) == ""


@pytest.mark.parametrize("text", NO)
def test_a_near_miss_arms_no_writer(d, text):
    assert not settings_ask(text).writers, text
    armed, ask = lanes(d, text)
    assert not ((set(armed) | set(ask)) & WRITERS), (text, armed, ask)


def test_a_question_about_theme_colours_arms_only_the_reader():
    ask = settings_ask("what colors can the theme be?")
    assert ask.reader and not ask.writers


def test_the_trigger_patterns_stay_linear():
    """A new regex over user text gets a pathological-input pin, in a child
    process with a deadline (the v1.421.0 rule)."""
    import subprocess
    import sys

    code = (
        "import time\n"
        "from iron_jarvis.settings.trigger import _settings_ask\n"
        "for t in ['use a ' + 'green and ' * 600 + 'theme for x', 'make the app ' + 'very ' * 900,"
        " 'use ' + 'a green, ' * 500, 'give me ' + 'calmish-' * 600 + 'look with x']:\n"
        "    s = time.perf_counter(); _settings_ask.__wrapped__(t[:4000]); assert time.perf_counter() - s < 2, t[:40]\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True, timeout=60)


