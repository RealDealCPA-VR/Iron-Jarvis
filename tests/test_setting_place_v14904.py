"""A setting's lookup line says WHERE it is on the Settings page (v1.490.4).

Seen live, in every run: asked "where do I find the button to change the
font", Jarvis read the lookup's line for Text size (a link and the key
`device.text_size`) and told the user to open "Settings → Device", a section
that does not exist; for the calendar reminders it said "it's under the
calendar setting". The line now names the section in the Settings page's own
words, which the schema that draws the page provides.
"""
from __future__ import annotations

import pytest

from iron_jarvis.capability_search import index as I
from iron_jarvis.capability_search import tool as T
from iron_jarvis.platform import build_platform
from iron_jarvis.settings import schema


@pytest.fixture(scope="module")
def platform(tmp_path_factory):
    return build_platform(tmp_path_factory.mktemp("home"))


def _row(key: str):
    return next(r for r in schema.SETTINGS if r.key == key)


def _entry(platform, key: str):
    return next(e for e in I.index_for(platform).entries if e.kind == "setting" and e.id == key)


def test_the_place_is_the_settings_pages_own_words():
    assert I.setting_place(_row("device.text_size")) == "Settings › Appearance"
    assert I.setting_place(_row("calendar_trigger_enabled")) == "Settings › Connections, under Calendar"
    assert I.setting_place(_row("update_channel")) == "Settings › System, under Updates"


def test_every_setting_has_a_place_in_a_section_the_page_draws():
    labels = {g[1] for g in schema.GROUPS}
    for row in schema.SETTINGS:
        if getattr(row, "pattern", False):
            continue
        place = I.setting_place(row)
        assert place.startswith("Settings › "), row.key
        assert place[len("Settings › "):].split(",")[0].split(" (")[0] in labels, (row.key, place)
        assert ("(in More options)" in place) is bool(row.advanced), row.key
        if row.section:
            assert f", under {row.section}" in place, row.key


def test_a_group_the_page_does_not_draw_is_not_given_a_place():
    class Odd:
        group = "nowhere"
        section = "Things"
        advanced = False

    assert I.setting_place(Odd()) == ""
    assert I.setting_place(object()) == ""


def test_the_lookups_line_names_the_section_and_keeps_the_link(platform):
    line, row = T.entry_line(platform, _entry(platform, "device.text_size"))
    assert "by hand in Settings › Appearance (the link /settings?focus=device.text_size opens it)." in line
    assert "Device" not in line.replace("device.text_size", "")
    assert row["where"] == "Settings › Appearance" and row["href"] == "/settings?focus=device.text_size"
    line, _ = T.entry_line(platform, _entry(platform, "calendar_trigger_enabled"))
    assert "in Settings › Connections, under Calendar (the link /settings?focus=calendar_trigger_enabled opens it)" in line


def test_a_setting_with_no_known_place_keeps_the_old_line(platform):
    entry = _entry(platform, "device.text_size")
    bare = I.Capability(kind="setting", id=entry.id, title=entry.title, tool=entry.tool,
                        changes=True, href=entry.href, text=entry.text)
    line, row = T.entry_line(platform, bare)
    assert line.endswith("or by hand at /settings?focus=device.text_size.")
    assert "where" not in row


def test_a_never_run_setting_tool_still_says_where(platform, monkeypatch):
    monkeypatch.setattr(T, "_mode", lambda _p, _tool: "deny")
    line, _ = T.entry_line(platform, _entry(platform, "device.text_size"))
    assert "changed only by hand, in Settings › Appearance (the link /settings?focus=device.text_size opens it)." in line


def test_the_place_is_not_part_of_what_a_search_matches(platform):
    """The search words are unchanged, so no setting is found by a section's
    name alone and the generated phrasings stay current."""
    entry = _entry(platform, "calendar_trigger_enabled")
    assert "Settings ›" not in entry.text
