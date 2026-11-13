"""A tool that changes or lists a kind of record brings that record's MAKER
along (v1.489.0).

Seen live, side by side on the practice set: "send me an overview every Friday
at 16:00" was looked up and brought `schedule_delete`, `schedule_list` and
`schedule_update`, never `schedule_create`; "reach me by Slack or email when a
job finishes" brought `channel_remove` and `channel_alerts`, never
`channel_connect`. The model then said what it could do and asked, or drafted a
workflow. The words for changing a schedule and for making one are the same
words, so the lookup cannot be trusted to rank the maker first. Now the maker
joins right after any member of its family and takes none of the lookup's
three places. It still asks first (a change a lookup brought always shows a
card), and it is not brought for a member that did not join.
"""

from __future__ import annotations

import pytest

from iron_jarvis.capability_search import join as J
from tests.test_capability_join_held_v1484 import _join, _row, app  # noqa: F401 — the fixture

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(300)]


def test_a_family_member_brings_the_maker_without_taking_a_place(app):  # noqa: F811
    rows = [_row("channel_remove"), _row("channel_alerts"), _row("redact_pii"), _row("goal_add")]
    joined, state = _join(app, rows, turn="turn-maker-1")
    # Three places: channel_remove, channel_alerts, redact_pii. The maker rides free.
    assert joined == ["channel_remove", "channel_connect", "channel_alerts", "redact_pii"], joined
    assert "channel_connect" in state["ask_armed"]
    assert state["overrides"]["channel_connect"] == "ask"
    # A change a lookup brought always shows a card: the maker is no exception.
    assert "channel_connect" in state["found_asks"]


def test_the_schedule_list_the_live_run_saw_now_holds_the_maker(app):  # noqa: F811
    rows = [_row("schedule_delete"), _row("schedule_list"), _row("schedule_update")]
    joined, _ = _join(app, rows, turn="turn-maker-2")
    assert "schedule_create" in joined, joined
    assert joined.index("schedule_create") < joined.index("schedule_update"), joined


def test_a_held_member_still_brings_the_maker(app):  # noqa: F811
    joined, _ = _join(app, [_row("goal_update")], held=("goal_update",), turn="turn-maker-3")
    assert joined == ["goal_list", "goal_add"], joined


def test_the_maker_is_not_brought_for_a_member_that_did_not_join(app):  # noqa: F811
    p = app.state.platform
    before = dict(p.permissions._base)
    p.permissions._base["channel_remove"] = "deny"
    try:
        joined, _ = _join(app, [_row("channel_remove"), _row("redact_pii")], turn="turn-maker-4")
    finally:
        p.permissions._base.clear()
        p.permissions._base.update(before)
    assert joined == ["redact_pii"], joined


def test_a_maker_set_to_never_run_does_not_join(app):  # noqa: F811
    p = app.state.platform
    before = dict(p.permissions._base)
    p.permissions._base["channel_connect"] = "deny"
    try:
        joined, _ = _join(app, [_row("channel_remove")], turn="turn-maker-5")
    finally:
        p.permissions._base.clear()
        p.permissions._base.update(before)
    assert joined == ["channel_remove"], joined


def test_a_maker_the_lookup_matched_itself_takes_its_own_place(app):  # noqa: F811
    rows = [_row("schedule_create"), _row("reflex_create"), _row("redact_pii"), _row("goal_add")]
    joined, _ = _join(app, rows, turn="turn-maker-6")
    assert joined == ["schedule_create", "reflex_create", "redact_pii"], joined


def test_every_family_names_tools_chat_can_bring_and_real_tools(app):  # noqa: F811
    can = J.joinable()
    registry = app.state.platform.registry
    for member, maker in J.MAKER_FOR.items():
        assert member in can and maker in can, (member, maker)
        assert registry.get(member) is not None and registry.get(maker) is not None, (member, maker)
        # A maker is never its own member, and never a command or script runner.
        assert maker not in J.MAKER_FOR and maker not in J.NEVER_JOINED, maker


def test_wanted_puts_reader_then_maker_right_after_the_member():
    assert J._wanted([_row("schedule_update"), _row("redact_pii")]) == [
        "schedule_update", "schedule_list", "schedule_create", "redact_pii",
    ]
    assert J._wanted([_row("channel_alerts")]) == ["channel_alerts", "channel_connect"]
    assert J._wanted([_row("redact_pii")]) == ["redact_pii"]
