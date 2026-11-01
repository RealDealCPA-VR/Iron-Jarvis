"""v1.420.0 (C6.1) REVIEW — the mail and feature readings, through BOTH real
chat lanes. Each row was wrong on the lane's first cut:

* "reply to the latest email" / "forward the newest email to Dana" / "delete
  my old emails" named no mail app, and "latest" armed WEB SEARCH for an
  email reply;
* a pasted email ("proofread my latest email below") and mail in hand ("the
  latest email about the invoice said ...") armed web search for "latest";
* "What can Jarvis connect to?" did not reach the Guide, and "what is
  build.gradle for" did (the "." of a file name read as a sentence's end).

The rows themselves live in ``tests/test_matcher_tables_v13610.py`` (DOOR,
GUIDE_ROWS, MAIL_WEB, CODING); this file drives the lanes. The model is the
only double (``test_chat_matches_meant_v13600``'s).
"""

from __future__ import annotations

import pytest

from tests.test_chat_matches_meant_v13600 import (  # noqa: F401 — fixtures
    MAIL_DOOR,
    _clean,
    _fresh_records,
    _post_turn,
    _stream_turn,
    app,
    spawned,
)

WEB = {"web_search", "web_fetch"}
GUIDE = {"guide_search", "app_search", "app_status"}


@pytest.mark.parametrize("lane", ["stream", "post"])
@pytest.mark.parametrize(
    "text",
    ["reply to the latest email", "forward the newest email to Dana", "delete my old emails"],
)
def test_acting_on_the_users_own_mail_offers_the_mail_choice_and_no_web(app, lane, text):  # noqa: F811
    """MUTATION: drop the "act on THE latest email" reading -> no door and,
    for "latest", web search shown (red)."""
    out, model = (_stream_turn if lane == "stream" else _post_turn)(app.state.platform, text)
    assert out["doors"] == [MAIL_DOOR], (text, out["doors"])
    assert not (WEB & set(model.tools[0])), (text, sorted(model.tools[0]))


@pytest.mark.parametrize("lane", ["stream", "post"])
@pytest.mark.parametrize(
    "text, web",
    [
        ("proofread my latest email below", False),
        ("the latest email about the invoice said it was due Friday", False),
        ("search the web for the latest emails from Apple about the iPhone", True),
        ("search for the latest news", True),
    ],
)
def test_latest_before_a_mail_word_is_never_a_web_search(app, lane, text, web):  # noqa: F811
    """MUTATION: put the bare "latest" back in the web words -> the pasted
    and in-hand emails show web search (red). Neither offers a door."""
    out, model = (_stream_turn if lane == "stream" else _post_turn)(app.state.platform, text)
    assert bool(WEB & set(model.tools[0])) is web, (text, sorted(model.tools[0]))
    assert out["doors"] == [], (text, out["doors"])


@pytest.mark.parametrize("lane", ["stream", "post"])
@pytest.mark.parametrize(
    "text, armed",
    [
        ("What can Jarvis connect to?", True),
        ("what apps does Jarvis work with?", True),
        ("what is build.gradle for", False),
        ("what does the code do", False),
        ("which services do you support for payment", False),
    ],
)
def test_the_guide_reads_jarvis_as_you_and_a_file_name_as_a_name(app, lane, text, armed):  # noqa: F811
    """MUTATION: "you" only (no "Jarvis") -> the first two lose the Guide;
    any "." as a sentence's end -> build.gradle gets it (red)."""
    _out, model = (_stream_turn if lane == "stream" else _post_turn)(app.state.platform, text)
    assert (GUIDE <= set(model.tools[0])) is armed, (text, sorted(model.tools[0]))
