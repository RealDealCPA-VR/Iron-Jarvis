"""v1.442.0 (Wave 7 group C) REVIEW: the theme-colour trigger.

Each pin was red on 19e2e474:

* the "use a … theme" request shape read ``\\s+(?:a|…)?\\s*`` — two
  whitespace runs that can split a long run of spaces any way, so "use"
  followed by 4,000 spaces took about 5 s (quadratic), in a pattern run on
  every chat message;
* near-misses armed a settings writer: a theme, palette or look for an
  event, a chart, a poster or a time ("…at the baby shower", "…this
  Christmas", "…when you draw the chart", "…, then draw a poster"), a theme
  song, a sentence whose subject is something else ("the slides should use
  a dark blue theme"), and "make the dashboard green in Power BI".
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from iron_jarvis.settings.trigger import settings_ask

#: Requests that must still arm a writer (the builder's own rows plus more).
YES = [
    "use a dark blue theme",
    "use a dark blue theme please",
    "use a dark blue theme, please",
    "use a green theme instead",
    "use a green theme from now on",
    "switch to a green theme.",
    "can you switch to a calm green theme",
    "hey jarvis, use a teal palette",
    "please use a dark green theme for the app",
    "use a green theme in the app",
    "give me a calm green look",
    "I want a soft pink theme",
    "make my own theme with teal",
    "set a custom theme",
    "create a new colour theme with emerald",
    "make the app purple",
    "make Jarvis green",
    "make the dashboard green",
]

#: Near-misses that must arm NO settings writer.
NO = [
    "I want a soft pink theme at the baby shower",
    "I'd like a red and gold theme this Christmas",
    "use a green palette when you draw the chart",
    "choose a purple palette, then draw a poster",
    "go with a bold red look tonight",
    "give me a dark look at the numbers",
    "the slides should use a dark blue theme",
    "make the dashboard green in Power BI",
    "create a new theme song for my podcast",
    "use a calm theme music for the video",
    "make my own theme park ride",
]


@pytest.mark.parametrize("text", YES)
def test_a_theme_request_still_arms_a_writer(text):
    assert settings_ask(text).writers, text


@pytest.mark.parametrize("text", NO)
def test_a_theme_near_miss_arms_no_writer(text):
    assert not settings_ask(text).writers, text


def test_a_long_run_of_spaces_after_a_request_verb_is_linear():
    """The pathological input the builder's pin did not try: whitespace."""
    code = (
        "import time\n"
        "from iron_jarvis.settings.trigger import _settings_ask\n"
        "for verb in ['use', 'switch to', 'i want', 'give me', 'change it to', 'make me', 'make',"
        " 'make the app', 'please use']:\n"
        "    for filler in [' ', ' \\t', ', ', ' -']:\n"
        "        t = (verb + filler * 4000)[:3990] + 'x'\n"
        "        s = time.perf_counter(); _settings_ask.__wrapped__(t)\n"
        "        assert time.perf_counter() - s < 0.5, (verb, filler)\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True, timeout=120)


# ------------------------------------------------- the writer's linked rule
# Two guarantees no pin held on 19e2e474 (each mutation stayed green):
# clearing the colours must never clear a built-in theme, and a write that
# names BOTH rows with a value is one theme too many.

from fastapi.testclient import TestClient  # noqa: E402

from iron_jarvis.daemon.app import create_app  # noqa: E402
from iron_jarvis.settings.writer import SettingError  # noqa: E402

DEV = "dev_wave7rev1"
GREEN = {"accent": "#2f855a", "surface": "tinted", "mode": "dark"}


@pytest.fixture
def writer(tmp_path):
    a = create_app(str(tmp_path))
    TestClient(a)
    return a.state.d.platform.config_writer


def test_clearing_the_colours_leaves_a_built_in_theme_alone(writer):
    writer.apply({"device.theme": "mark2"}, actor="test", device_id=DEV)
    out = writer.apply({"device.theme_colors": None}, actor="test", device_id=DEV)
    assert writer.current("device.theme", device_id=DEV) == "mark2"
    assert out.changed == []


def test_both_theme_rows_with_a_value_in_one_write_are_refused(writer):
    writer.apply({"device.theme": "mark2"}, actor="test", device_id=DEV)
    with pytest.raises(SettingError) as e:
        writer.apply(
            {"device.theme": "mark1", "device.theme_colors": GREEN}, actor="test", device_id=DEV
        )
    assert "one" in str(e.value).lower()
    assert writer.current("device.theme", device_id=DEV) == "mark2"
    assert writer.current("device.theme_colors", device_id=DEV) is None


def test_the_device_route_says_so_in_words(tmp_path):
    c = TestClient(create_app(str(tmp_path)))
    r = c.put(
        "/settings/device",
        json={"device_id": DEV, "values": {"device.theme": "mark1", "device.theme_colors": GREEN}},
    )
    assert r.status_code == 400, r.text
