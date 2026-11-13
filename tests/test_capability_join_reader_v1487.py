"""A tool that changes a record by its NAME brings that record's list along
(v1.487.0).

Seen live: "the Friday 4pm reminder for the Pereira filings is wrong, please
move it to 3:30 instead" — the lookup found `schedule_update`, which takes the
schedule's name, and the model asked the user for it. Now `schedule_list`
joins right after it and takes none of the lookup's three places; the same for
goals, workflows and kept preferences. A reader added only for a writer that
did not join (denied, say) does not join on its own.

v1.489.0 (moved on purpose): the family's maker rides along too
(``MAKER_FOR``, ``tests/test_capability_join_maker_v1489.py``), so
`schedule_create` and `goal_add` appear after their lists below.
"""

from __future__ import annotations

import pytest

from iron_jarvis.capability_search import join as J
from tests.test_capability_join_held_v1484 import _join, _row, app  # noqa: F401 — the fixture

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(300)]


def test_a_named_record_writer_brings_its_list_without_taking_a_place(app):  # noqa: F811
    rows = [_row("schedule_update"), _row("reflex_create"), _row("redact_pii"), _row("goal_add")]
    joined, state = _join(app, rows, turn="turn-reader-1")
    assert joined == ["schedule_update", "schedule_list", "schedule_create", "reflex_create", "redact_pii"], joined
    assert "schedule_list" in state["armed"] and state["overrides"]["schedule_list"] == "allow"


def test_a_held_writer_still_brings_its_list(app):  # noqa: F811
    joined, _ = _join(app, [_row("schedule_update")], held=("schedule_update",), turn="turn-reader-2")
    assert joined == ["schedule_list", "schedule_create"], joined


def test_the_list_is_not_brought_for_a_writer_that_did_not_join(app):  # noqa: F811
    p = app.state.platform
    before = dict(p.permissions._base)
    p.permissions._base["schedule_update"] = "deny"
    try:
        joined, _ = _join(app, [_row("schedule_update"), _row("redact_pii")], turn="turn-reader-3")
    finally:
        p.permissions._base.clear()
        p.permissions._base.update(before)
    assert joined == ["redact_pii"], joined


def test_a_list_the_lookup_matched_itself_still_joins(app):  # noqa: F811
    joined, _ = _join(app, [_row("schedule_list")], turn="turn-reader-4")
    assert joined == ["schedule_list", "schedule_create"], joined


def test_every_writer_names_a_reader_chat_can_bring():
    can = J.joinable()
    for writer, reader in J.READER_FOR.items():
        assert writer in can and reader in can, (writer, reader)


def test_wanted_puts_the_reader_right_after_its_writer():
    rows = [_row("goal_update"), {"kind": "setting", "id": "x", "tool": "config_set"}]
    assert J._wanted(rows) == ["goal_update", "goal_list", "goal_add", "config_set", "config_list"]
    assert J._wanted([_row("redact_pii")]) == ["redact_pii"]


def test_a_list_already_held_takes_no_place_either(app):  # noqa: F811
    rows = [_row("schedule_update"), _row("reflex_create"), _row("redact_pii"), _row("goal_add")]
    joined, _ = _join(app, rows, held=("schedule_list",), turn="turn-reader-5")
    assert joined == ["schedule_update", "schedule_create", "reflex_create", "redact_pii"], joined
