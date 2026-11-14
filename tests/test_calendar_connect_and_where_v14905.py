"""A calendar is connected through ``app_connect``; the Guide knows where each
setting is (v1.490.5).

Two requests the practice set kept showing, on both models:

* "Hook up my Google calendar to you": the intent is "connect an outside app
  (Notion, GitHub, Google Drive, a calendar...)". ``app_disconnect`` already
  takes the calendar link off (v1.489.0), but ``app_connect`` answered a
  calendar with a refusal that named another tool. It now shows the calendar
  link's secure card itself: the same card ``config_secret`` shows, nothing
  saved by the call, no approval card.
* "Where do I find the button to change the font": a question for the Guide.
  The Guide had no page that says where a setting is, so the model answered
  from the feature lookup instead. Each setting now has a Guide page with its
  place in the Settings page's own words, and a "where is it" question ranks
  that page first.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import textwrap

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.capability_search import index as CI
from iron_jarvis.capability_search import join as CJ
from iron_jarvis.capability_search import tool as CT
from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.guide import corpus as G
from iron_jarvis.settings import records, records_apps, schema
from iron_jarvis.tools.base import ToolContext

pytestmark = pytest.mark.usefixtures("no_network")


@pytest.fixture(scope="module")
def platform(tmp_path_factory):
    c = TestClient(create_app(str(tmp_path_factory.mktemp("home"))))
    yield c.app.state.platform
    try:
        c.app.state.platform.scheduler.shutdown()
    except Exception:  # noqa: BLE001
        pass


def _ctx(p, tmp_path):
    return ToolContext(workspace=tmp_path, session_id="chat", agent_run_id="chat", config=p.config,
                       event_bus=p.event_bus, engine=p.engine)


def _connect(p, tmp_path, app):
    tool = p.registry.get("app_connect")
    ctx = _ctx(p, tmp_path)

    async def go():
        return await tool.spend_check({"app": app}, ctx), await tool.execute({"app": app}, ctx)

    return asyncio.run(go())


# --- the calendar is connected through app_connect ---------------------------


@pytest.mark.parametrize("app", ["google calendar", "google_calendar", "calendar", "iCal", "outlook_calendar"])
def test_a_calendar_shows_the_calendar_links_secure_card(platform, tmp_path, app):
    check, r = _connect(platform, tmp_path, app)
    assert r.ok is True, r.error
    (req,) = r.data["secret_requests"]
    assert req["name"] == "calendar_ics_url" and req["label"] == "Calendar link" and req["request_id"]
    assert r.data["calendar"] is True
    # The secure card is the consent: no approval card for a call that saves nothing.
    assert check is not None and check.ask is False
    assert platform.secrets.exists("calendar_ics_url") is False
    # The page draws the card from the result, as it does for config_secret.
    cards = chat_turn.config_cards_from_result("app_connect", r)
    assert [c["name"] for c in cards if c.get("kind") == "secret"] == ["calendar_ics_url"]


def test_the_words_say_the_link_is_read_and_never_written_to(platform, tmp_path):
    """Seen live: asked for a calendar it could add appointments to, a model
    opened the link's card and said appointments could be added through it."""
    _check, r = _connect(platform, tmp_path, "calendar")
    assert "it can read the appointments, it cannot add or change them" in r.output
    assert "never ask for it in chat" in r.output
    assert "already saved" not in r.output and r.data["already_saved"] is False


def test_a_saved_link_is_said_so_the_user_knows_a_new_one_replaces_it(platform, tmp_path):
    platform.secrets.set("calendar_ics_url", "https://calendar.example.com/private/basic.ics")
    try:
        _check, r = _connect(platform, tmp_path, "google calendar")
        assert r.ok and r.output.endswith(records.CALENDAR_SAVED_WORDS) and r.data["already_saved"] is True
    finally:
        platform.secrets.delete("calendar_ics_url")


@pytest.mark.parametrize("app", ["medical_records", "notion_x", "topical", "physical_therapy"])
def test_an_unknown_app_that_is_no_calendar_keeps_its_refusal(platform, tmp_path, app):
    """"ical" inside another word is not the calendar."""
    _check, r = _connect(platform, tmp_path, app)
    assert r.ok is False and r.error.startswith(f"There is no app called {app!r}. Known: ")
    assert "calendar" not in r.error.lower().split("known:")[0] and not r.data


CONNECTS = [
    "Hook up my Google calendar to you so you can see when my meetings are, I'm always double-booked.",
    "connect my calendar",
    "Please connect the Outlook calendar.",
    "link my outlook calendar and tell me what is on today",
    "can you sync my icloud calendar with Jarvis",
    "Add my calendar so you know when I am free",
    "wire up my calendar feed to the assistant",
    "join up my calendar to this app",
]
NOT_CONNECTS = [
    "sync my calendar with my phone",
    "add the calendar invite to the email",
    "add my calendar event for Friday to the sheet",
    "connect my calendar app to Outlook on my laptop",
    "link the calendar page from the footer",
    "the calendar on the wall is from last year",
    "set up a calendar of posts for the shop",
]


@pytest.mark.parametrize("text", CONNECTS)
def test_asking_to_connect_a_calendar_offers_the_connect_tool(text):
    assert records_apps._CALENDAR_ON.search(text), text
    assert "app_connect" in records_apps.apps_tools_for(text)
    assert "app_connect" in records.record_tools_for(text)


@pytest.mark.parametrize("text", NOT_CONNECTS)
def test_a_thing_on_a_calendar_or_another_program_does_not(text):
    assert not records_apps._CALENDAR_ON.search(text), text


def test_the_calendar_words_are_linear_on_hostile_input():
    code = textwrap.dedent("""
        import time
        from iron_jarvis.settings import records_apps as RA
        for bad in ("connect " * 20000, "hook up my " * 12000, "connect my google " * 8000,
                    "connect my calendar " * 8000 + "x", "sync" + " " * 120000 + "calendar"):
            t = time.perf_counter()
            RA._CALENDAR_ON.search(bad[:128000])
            assert time.perf_counter() - t < 5, len(bad)
        print("ok")
    """)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.stdout.strip() == "ok", out.stderr[-600:]


# --- the lookup's line for the calendar link ---------------------------------


def _calendar_entry(platform):
    return next(e for e in CI.build(platform).entries if e.id == "calendar_ics_url")


def test_the_lookup_names_the_connect_tool_for_a_reply_that_holds_it(platform):
    line, row = CT.entry_line(platform, _calendar_entry(platform))
    assert ('when tool app_connect is among your tools, connect it by calling app_connect with '
            '`app` "calendar"; otherwise set it with tool config_secret.') in line
    assert "Either shows the user a secure card" in line and "Never ask for the value in chat." in line
    assert row["connector"] == "app_connect" and row["tool"] == "config_secret"
    # Removing it is still said (v1.489.0).
    assert 'To REMOVE the saved value, call tool app_disconnect with `app` "calendar"' in line


def test_the_lookup_never_brings_the_connect_tool_itself(platform):
    """A looked-up tool that changes something always shows an approval card.
    The connect tool is offered by the user's own words; the lookup brings only
    the secure card's tool, so a looked-up calendar request stays ONE card."""
    _line, row = CT.entry_line(platform, _calendar_entry(platform))
    assert CJ._wanted([row]) == ["config_secret", "app_disconnect"]


def test_a_connect_tool_set_to_never_run_is_not_named(platform, monkeypatch):
    real = CT._mode
    monkeypatch.setattr(CT, "_mode", lambda p, tool: "deny" if tool == "app_connect" else real(p, tool))
    line, row = CT.entry_line(platform, _calendar_entry(platform))
    assert "app_connect" not in line and "connector" not in row
    assert "set with tool config_secret, which shows the user a secure card" in line


def test_another_private_value_keeps_its_line(platform):
    entry = next(e for e in CI.build(platform).entries if e.id == "voice_transcribe_key")
    line, row = CT.entry_line(platform, entry)
    assert "app_connect" not in line and "connector" not in row
    assert set(CI.SECRET_CONNECTOR) == {"calendar_ics_url"}


# --- the Guide knows where each setting is ------------------------------------


def test_every_setting_has_a_guide_page_with_its_place():
    pages = dict(G.settings_places())
    rows = [r for r in schema.SETTINGS if not getattr(r, "pattern", False) and CI.setting_place(r)]
    assert len(pages) == len({r.label for r in rows}) and len(rows) > 100
    for r in rows:
        lines = pages[f"{G.SETTINGS_PLACE_HEAD}: {r.label}"]
        assert lines[0].startswith(f"{r.label} is in Settings › ") or r.label in {x.label for x in rows if x is not r}
    text = pages[f"{G.SETTINGS_PLACE_HEAD}: Text size"]
    assert text[0] == "Text size is in Settings › Appearance."
    assert "font size" in text[1] and text[1].startswith("Also called: ")


def test_a_setting_page_holds_no_value(platform):
    """Labels, other names and help only: what a setting is set to is never there."""
    platform.config.backup_mirror_dir = r"D:\\Secret Client Backups"
    try:
        joined = "\n".join("\n".join(lines) for _h, lines in G.settings_places())
        assert "Secret Client Backups" not in joined
    finally:
        platform.config.backup_mirror_dir = ""


@pytest.mark.parametrize(("question", "label"), [
    ("where do i find the button to change the font", "Text size"),
    ("I am not sure where the button is to switch on the reminders in this program, could you kindly "
     "show me how to find it.", "Calendar reminders"),
    ("where is the setting for the backup copy folder", "Backup copy folder"),
])
def test_a_where_question_finds_the_settings_own_page_first(platform, question, label):
    idx = G.GuideIndex(platform)
    labels = {r.label for r in schema.SETTINGS}
    if label not in labels:
        pytest.skip(f"no setting labelled {label!r} in this version")
    score, top = idx.search(question, 5)[0]
    assert top.live and top.heading == f"{G.SETTINGS_PLACE_HEAD}: {label}", (top.heading, score)
    assert "is in Settings › " in top.text[:120]


@pytest.mark.parametrize("text", [
    "where do i find the button to change the font",
    "Where is the backup switch?",
    "which page has the update setting",
    "show me how to find the option for read aloud",
    "how do I get to the settings page for approvals",
])
def test_these_ask_where(text):
    assert G.is_where_question(text)


@pytest.mark.parametrize("text", [
    "how do updates install",
    "what is a memory base",
    "make the text bigger",
    "show me my schedules",
    "find the invoice from March",
])
def test_these_do_not(text):
    assert not G.is_where_question(text)


def test_a_question_that_does_not_ask_where_is_ranked_as_before(platform):
    """The bonus is for "where is it" only: a how-does-it-work question keeps
    the manual's own sections above the per-setting pages."""
    idx = G.GuideIndex(platform)
    plain = idx.search("how do updates install", 8)
    assert plain and not plain[0][1].heading.startswith(G.SETTINGS_PLACE_HEAD)
    with_bonus = dict((s.heading, sc) for sc, s in idx.search("where is the update channel setting", 50))
    without = dict((s.heading, sc) for sc, s in idx.search("the update channel setting", 50))
    head = next(h for h in with_bonus if h.startswith(G.SETTINGS_PLACE_HEAD) and h in without)
    assert with_bonus[head] == pytest.approx(without[head] * G._WHERE_BONUS)


def test_the_guide_agents_list_of_live_pages_has_one_line_for_them(platform):
    know = G.GuideIndex(platform).base_knowledge(char_budget=200000)
    tail = know.split("Live catalogs guide_search can reach: ")[1]
    assert tail.count(G.SETTINGS_PLACE_HEAD) == 1
    assert f"{G.SETTINGS_PLACE_HEAD} (one page per setting" in tail


def test_the_where_words_are_linear_on_hostile_input():
    code = textwrap.dedent("""
        import time
        from iron_jarvis.guide import corpus as G
        for bad in ("find " * 30000, "show me " * 20000, "get to " * 20000 + "x", "find" + " a" * 60000):
            t = time.perf_counter()
            G.is_where_question(bad)
            assert time.perf_counter() - t < 5
        print("ok")
    """)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.stdout.strip() == "ok", out.stderr[-600:]


# --- where / how questions go to the Guide ------------------------------------


def test_the_lookups_brief_sends_a_where_question_to_the_guide():
    block = chat_turn.CAPABILITY_BLOCK
    assert "call `guide_search`, not `find_capability`, when you hold it" in block
    assert "do not make the change for them unless they ask" in block
    assert len(block.split()) < 400


def test_the_lookup_tool_says_it_is_not_for_a_where_question(platform):
    desc = platform.registry.get("find_capability").description
    assert "It is NOT for a question about WHERE something is in the app or HOW the user does it by hand" in desc
    assert "when you hold guide_search, call that for such a question" in desc


def test_an_answer_that_lists_a_setting_points_a_where_question_at_the_guide(platform):
    """Seen live: the model looked "change the font" up, got the Text size
    setting, and answered from the list with the Guide among its tools."""
    text, data = CT.answer(platform, "text size / font size / bigger text")
    assert any(row["kind"] == "setting" for row in data["matches"])
    assert CT.WHERE_HINT in text and text.index(CT.WHERE_HINT) < text.index(CT.HOW_TO_USE)
    assert "did not ask you to change it" in CT.WHERE_HINT


def test_an_answer_with_no_setting_has_no_such_hint(platform, monkeypatch):
    idx = CI.index_for(platform)
    tools_only = [(1.0, e) for e in idx.entries if e.kind == "tool" and e.id in ("rename_file", "list_files")]
    assert len(tools_only) == 2
    monkeypatch.setattr(type(idx), "search", lambda self, query, k: tools_only)
    text, data = CT.answer(platform, "rename files in a folder / move a file")
    assert [row["kind"] for row in data["matches"]] == ["tool", "tool"]
    assert CT.WHERE_HINT not in text and CT.HOW_TO_USE in text
