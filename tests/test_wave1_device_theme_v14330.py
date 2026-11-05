"""v1.433.0 (Wave 1 B, decision D1): the device "Theme" row names the
built-in themes of the dashboard, and only those.

Before: ``device.theme`` was a free-text row ("a built-in Mark or one of your
own") that nothing on screen read, so chat's ``config_set`` could store any
word, the screen ignored it, and ``config_list`` reported a theme the screen
did not show. The dashboard now applies the row live (lib/deviceSettings.ts)
and the theme switcher saves it, so the row takes exactly the ids the
dashboard can paint: ``dashboard/lib/theme.ts`` THEMES, id and name, in order.
A theme of the user's own is a screen-only choice and clears the row.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.settings import schema
from iron_jarvis.settings.tools import _coerce
from iron_jarvis.settings.writer import SettingError

ROOT = Path(__file__).resolve().parents[1]
THEME_TS = ROOT / "dashboard" / "lib" / "theme.ts"


def _dashboard_themes() -> list[tuple[str, str]]:
    """(id, name) for every entry of lib/theme.ts THEMES, in order."""
    src = THEME_TS.read_text(encoding="utf-8").replace("\r\n", "\n")
    start = src.index("export const THEMES: Mark[] = [")
    end = src.index("\n];", start)
    block = src[start:end]
    entries = re.findall(r"\{\s*id:\s*\"([^\"]+)\",[^{}]*?\bname:\s*\"([^\"]+)\"", block, re.S)
    assert entries, "could not read THEMES from lib/theme.ts"
    return entries


def test_the_theme_row_names_exactly_the_dashboards_built_in_themes():
    d = schema.get("device.theme")
    assert d.type == "enum" and d.store == "device"
    assert list(d.options) == _dashboard_themes()


def test_the_help_text_says_a_theme_of_your_own_is_screen_only():
    help_text = schema.get("device.theme").help.lower()
    assert "made yourself" in help_text and "screen" in help_text


def test_chat_can_name_a_theme_by_its_name():
    d = schema.get("device.theme")
    assert _coerce(d, "Daylight") == "mark1"
    assert _coerce(d, "liquid glass") == "mark8"


@pytest.fixture
def writer(tmp_path):
    a = create_app(str(tmp_path))
    TestClient(a)
    return a.state.d.platform.config_writer


def test_the_writer_takes_a_built_in_theme_and_refuses_anything_else(writer):
    writer.apply({"device.theme": "mark23"}, actor="test", device_id="dev_theme01")
    assert writer.current("device.theme", device_id="dev_theme01") == "mark23"
    for bad in ("custom-abc123", "neon", ""):
        with pytest.raises(SettingError):
            writer.apply({"device.theme": bad}, actor="test", device_id="dev_theme01")
    assert writer.current("device.theme", device_id="dev_theme01") == "mark23"


def test_a_theme_of_your_own_clears_the_row(writer):
    # The dashboard PUTs null for a palette the user made (chooseTheme).
    writer.apply({"device.theme": "mark1"}, actor="test", device_id="dev_theme02")
    writer.apply({"device.theme": None}, actor="test", device_id="dev_theme02")
    assert writer.current("device.theme", device_id="dev_theme02") is None


def test_the_device_route_round_trips_the_theme_and_names_its_options(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    r = client.put(
        "/settings/device",
        json={"device_id": "dev_theme03", "values": {"device.theme": "mark8"}},
    )
    assert r.status_code == 200, r.text
    got = client.get("/settings/device", params={"device_id": "dev_theme03"}).json()
    assert got["values"]["device.theme"] == "mark8"
    row = next(s for s in client.get("/settings/schema").json()["settings"] if s["key"] == "device.theme")
    assert [(o["value"], o["label"]) for o in row["options"]] == _dashboard_themes()
