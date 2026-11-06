"""v1.438.0 (chat-first plan Wave 4 group C) — the independent review's pins.

Each pin was written FIRST and seen red against the builder's commit, then
the fix made it green (and a mutation of the fix turned it red again):

* ARMING, new sentences both ways — someone else's preference ("the
  client's preference for paper statements"), a preference that is only a
  modifier ("the preference cookie banner"), a teacher's lesson ("a lesson
  about depreciation to my CPE course", "a lesson for the new intern"), a
  numbered course unit ("lesson 3 slides"), a marketing persona with words
  between ("a persona for our ideal small-business client", "for each
  customer segment"), a persona's column or slide — none arms a writer;
  "my laptop doesn't start with Windows anymore" never arms the app's Start
  with Windows switch; "make Jarvis open automatically when I log in" does.
* UNDO never adds a SECOND copy of a preference the user kept again since
  (a forget's Undo after "keep answers short" was said again), and never
  removes a persona that has become the DEFAULT persona since it was made.
* A profile row changed from chat says so when "Use my profile" is off —
  the change is saved but nothing reads it until the profile is on.
"""

from __future__ import annotations

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.learning.models import LessonRecord
from iron_jarvis.settings import records as R
from iron_jarvis.settings import records_self as RS
from iron_jarvis.settings.trigger import settings_ask
from tests.test_chat_approvals_v1187 import _drive_stream
from tests.test_records_self_v14380 import _persona, _undo
from tests.test_settings_chat_tools_v1321 import _body
from tests.test_settings_records_v1321 import _app, _approve_all, _card

WRITERS = frozenset(RS.SELF_TOOL_TIERS)


# --------------------------------------------------------------------------- #
# arming, the reviewer's own sentences
# --------------------------------------------------------------------------- #

NOT_OURS = [
    "forget the client's preference for paper statements",
    "update the client's mailing preference in the CRM",
    "delete the partner's preference note from the engagement letter",
    "edit John's lesson notes",
    "change Sarah's persona in the role-play script",
    "forget the preference cookie banner, it's fine",
    "delete the preference center link from the newsletter footer",
    "edit the lesson 3 slides for the bookkeeping class",
    "change the lesson title in the Canvas course",
    "change the lesson about depreciation in the Canvas course",
    "delete the lesson on reconciliations for my CPE course",
    "add a lesson about depreciation to my CPE course",
    "add a new lesson to our onboarding course",
    "add a lesson for the new intern on reconciliations",
    "add a lesson to the training binder for new staff",
    "create a persona for our ideal small-business client",
    "make a persona for each customer segment in the deck",
    "rename the persona column in the spreadsheet",
    "remove the default persona from the slide deck template",
    "remove the persona photo from the website",
]

STILL_OURS = [
    ("forget my preference for Arial", "preference_forget"),
    ("please change my preference about greetings to say Hi", "preference_edit"),
    ("change my lesson about rounding to say round to cents", "preference_edit"),
    ("add a lesson: Lacerte imports need the tie-out", "lesson_add"),
    ("add a lesson: always cite the section for each client", "lesson_add"),
    ("make a persona for drafting client emails", "persona_create"),
    ("delete Jarvis's CPA persona", "persona_delete"),
    ("update the persona named Reviewer so it cites sources", "persona_update"),
    ("create a persona that sounds like a seasoned tax partner", "persona_create"),
    ("remove the pirate persona from the chat menu", "persona_delete"),
]


@pytest.mark.parametrize("text", NOT_OURS)
def test_someone_elses_preference_lesson_or_persona_arms_no_writer(text):
    assert not set(R.record_tools_for(text)) & WRITERS, text


@pytest.mark.parametrize("text,tool", STILL_OURS)
def test_the_users_own_still_arm(text, tool):
    assert tool in R.record_tools_for(text), text


def test_a_machine_that_starts_with_windows_is_not_the_apps_switch():
    for text in (
        "my laptop doesn't start with Windows anymore, it hangs at the logo",
        "the printer won't start with windows",
        "my PC starts with Windows 11 now",
    ):
        ask = settings_ask(text)
        assert "start_with_windows" not in ask.keys, (text, ask)
    # …while the app's own switch is still reached.
    for text in ("start Iron Jarvis with Windows", "don't start with windows anymore",
                 "make Iron Jarvis start at login"):
        ask = settings_ask(text)
        assert ask.writers and "start_with_windows" in ask.keys, (text, ask)


def test_jarvis_opening_when_i_log_in_arms_the_settings_writer():
    for text in ("make Jarvis open automatically when I log in to my computer",
                 "I want Iron Jarvis to start when I sign in"):
        assert settings_ask(text).writers, text
    # Not a request about the app opening by itself.
    assert not settings_ask("open the email when I log in tomorrow").writers


# --------------------------------------------------------------------------- #
# Undo never duplicates, never strands the default persona
# --------------------------------------------------------------------------- #


def _kept_with_text(p, text: str) -> int:
    with session_scope(p.engine) as db:
        rows = db.exec(select(LessonRecord).where(LessonRecord.text == text)).all()
        return len(rows)


@pytest.mark.asyncio
async def test_a_forget_undo_never_adds_a_second_copy_of_a_preference_kept_again(tmp_path):
    app = _app(tmp_path, [("preference_forget", {"preference": "short answers"})])
    p = app.state.platform
    p.learning.note_preference("Keep answers short.")
    frames = await _drive_stream(app, _body("forget my preference about short answers"), await _approve_all(app, []))
    card = _card(frames, "Preference forgotten")
    # The user says it again (a new row, a new id) before pressing Undo.
    p.learning.note_preference("Keep answers short.")
    r = _undo(app, card)
    assert r.status_code in (200, 409), r.text
    assert _kept_with_text(p, "Keep answers short.") == 1, "Undo must not keep the same preference twice"


@pytest.mark.asyncio
async def test_a_persona_create_undo_refuses_once_it_is_the_default_persona(tmp_path):
    app = _app(tmp_path, [("persona_create", {"name": "Auditor", "prompt": "You are a skeptical auditor."})])
    p = app.state.platform
    frames = await _drive_stream(app, _body("create a new persona called Auditor"), await _approve_all(app, []))
    card = _card(frames, "Persona created")
    assert _persona(p, "auditor") is not None
    # The user makes it the default persona (Settings or chat), THEN presses Undo.
    p.config_writer.apply({"default_persona": "auditor"}, actor="settings_page")
    r = _undo(app, card)
    assert r.status_code == 409 and "default" in r.text.lower(), r.text
    assert _persona(p, "auditor") is not None, "the default persona must not be removed under the user"


# --------------------------------------------------------------------------- #
# a profile change while the profile is off says so
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_profile_row_changed_while_the_profile_is_off_says_nothing_reads_it(tmp_path):
    app = _app(tmp_path, [("config_set", {"key": "profile.language", "value": "es"})])
    p = app.state.platform
    from iron_jarvis.profile.store import ProfileStore

    ProfileStore(p.engine).save({"enabled": False})
    frames = await _drive_stream(app, _body("answer me in Spanish from now on"), await _approve_all(app, []))
    outs = [d.get("output", "") for ev, d in frames if ev == "tool_call" and d.get("status") == "finished"]
    assert ProfileStore(p.engine).get().language == "es"
    assert any("Use my profile" in o for o in outs), outs


@pytest.mark.asyncio
async def test_a_profile_row_changed_while_the_profile_is_on_says_nothing_extra(tmp_path):
    app = _app(tmp_path, [("config_set", {"key": "profile.language", "value": "es"})])
    frames = await _drive_stream(app, _body("answer me in Spanish from now on"), await _approve_all(app, []))
    outs = [d.get("output", "") for ev, d in frames if ev == "tool_call" and d.get("status") == "finished"]
    assert outs and not any("Use my profile" in o for o in outs), outs


# --------------------------------------------------------------------------- #
# the safety doors, driven (not only the tables)
# --------------------------------------------------------------------------- #


def test_a_low_trust_turn_never_forgets_or_removes(tmp_path):
    from tests.test_records_projects_review_v14350 import _stream

    app = _app(tmp_path / "home", [("preference_forget", {"preference": "short answers"}),
                                   ("persona_create", {"name": "Auditor", "prompt": "You audit."})])
    p = app.state.platform
    row = p.learning.note_preference("Keep answers short.")
    frames, cards = _stream(
        app, _body("forget my preference about short answers and create a new persona called Auditor"),
        trust="low", trust_reason="from the phone",
    )
    assert cards == []
    with session_scope(p.engine) as db:
        assert db.get(LessonRecord, row.id) is not None
    assert _persona(p, "auditor") is None


@pytest.mark.asyncio
async def test_the_post_lane_never_runs_them(tmp_path):
    from iron_jarvis.daemon.chat_turn import run_chat_turn
    from iron_jarvis.daemon.schemas import ChatBody
    from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
    from iron_jarvis.providers.router import RouteResult

    app = _app(tmp_path / "home", [])
    p = app.state.platform
    row = p.learning.note_preference("Keep answers short.")
    moves = [[ToolCall(id="c0", name="preference_forget", arguments={"preference": "short answers"})],
             [ToolCall(id="c1", name="lesson_add", arguments={"text": "Always add a summary."})]]

    async def complete(**kw):
        if moves and kw.get("tools") is not None:
            return RouteResult(LLMResponse(text="", tool_calls=moves.pop(0), usage={}), "mock", "mock")
        return RouteResult(LLMResponse(text="Done.", usage={}), "mock", "mock")

    p.router.complete = complete
    body = ChatBody(messages=[{"role": "user", "content": "forget my preference about short answers and add "
                               "a lesson: always add a summary"}], auto_tools=True)
    await run_chat_turn(p, {}, body)
    with session_scope(p.engine) as db:
        assert db.get(LessonRecord, row.id) is not None
    assert _kept_with_text(p, "Always add a summary.") == 0
