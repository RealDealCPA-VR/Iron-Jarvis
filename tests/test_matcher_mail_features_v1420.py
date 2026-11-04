"""v1.420.0 (C6.1) — the matcher reads the user's MAIL and questions about
Iron Jarvis's FEATURES, as the real chat lanes meet them.

Probed at v1.415.0: "summarize my latest emails" armed ``web_search`` +
``web_fetch`` ("latest" read as a web signal; "my latest emails" was not mail
words, so no mail app and no Connect door), and "what apps can you work
with?", "what are missions", "what is build for" armed nothing.

The phrasings live in ``tests/test_matcher_tables_v13610.py`` (DOOR,
GUIDE_ROWS, MAIL_WEB, CODING — both ways); this file drives the lanes:

* fresh install: both lanes put ONE mail choice door under the reply and
  show the model no web tool;
* Microsoft 365 connected: the words mean that app — its tools ask-armed in
  the stream lane, no door, no web tool;
* the Guide's three lookups are shown for a feature question, never for the
  everyday sense ("what are missions in the Bible");
* the coding offer is a ``daemon.doors`` entry (a lane can add it to its
  list); nothing is armed by it.

The model is the only double (``test_chat_matches_meant_v13600``'s).
"""

from __future__ import annotations

import pytest

from iron_jarvis.daemon import doors
from iron_jarvis.tools.autoselect import coding_offer

from tests.test_chat_matches_meant_v13600 import (  # noqa: F401 — fixtures
    MAIL_DOOR,
    TOOLS_LOADED,
    _clean,
    _connect,
    _fresh_records,
    _post_turn,
    _section,
    _stream_turn,
    app,
    client,
    spawned,
)

WEB = {"web_search", "web_fetch"}
GUIDE = {"guide_search", "app_search", "app_status"}


@pytest.mark.parametrize("lane", ["stream", "post"])
@pytest.mark.parametrize("text", ["summarize my latest emails", "any new mail?"])
def test_fresh_install_fresh_mail_offers_the_mail_choice_and_no_web(app, lane, text):  # noqa: F811
    """MUTATION: take the freshness words out of ``_MAIL_FRESH`` -> no door
    and web_search shown (red)."""
    out, model = (_stream_turn if lane == "stream" else _post_turn)(app.state.platform, text)
    assert out["doors"] == [MAIL_DOOR], out["doors"]
    shown = set(model.tools[0])
    assert not (WEB & shown), sorted(shown)
    section = _section(model.systems[0])
    assert "- Email: no mail app is connected" in section, section
    assert "Gmail: not connected" not in section


@pytest.mark.timeout(180)
def test_an_outlook_user_who_says_summarize_my_latest_emails_gets_microsoft_365(client):  # noqa: F811
    """THE ACCEPTANCE: Microsoft 365 connected (its tools from the
    stand-in server), Gmail not. The stream lane ask-arms Microsoft 365's
    tools, no web tool, no door; the model is told about Microsoft 365.

    v1.428.0 (moved on purpose): the locked card loads its read and draft
    tools (``TOOLS_LOADED``, not 24); its Connect sends the account's email.
    The model never calls a tool here, so no Local only is needed."""
    r = _connect(client, "common")
    assert r.status_code == 200 and r.json()["tools_loaded"] == TOOLS_LOADED, r.text
    platform = client.app.state.d.platform
    out, model = _stream_turn(platform, "summarize my latest emails")
    section = _section(model.systems[0])
    assert "Microsoft 365 (Outlook, Calendar, OneDrive):" in section, section
    assert "Gmail" not in section, section
    armed = model.tools[0]
    assert any(str(n).startswith("mcp__microsoft_365__") for n in armed), armed
    assert not (WEB & set(armed)), armed
    assert out["doors"] == [], out["doors"]


@pytest.mark.parametrize("lane", ["stream", "post"])
@pytest.mark.parametrize(
    "text, armed",
    [
        ("what apps can you work with?", True),
        ("what are missions", True),
        ("what is build for", True),
        ("what does the Code view do", True),
        ("how do I use Build", True),
        ("what are missions in the Bible", False),
        ("what is the build for the release", False),
    ],
)
def test_both_lanes_show_the_guide_for_a_feature_question_only(app, lane, text, armed):  # noqa: F811
    """MUTATION: drop the ``guide_feature`` group from ``_GUIDE_RX`` -> the
    first four rows lose the Guide (red); drop ``_FEATURE_END`` -> the Bible
    and release rows get it (red)."""
    _out, model = (_stream_turn if lane == "stream" else _post_turn)(app.state.platform, text)
    shown = set(model.tools[0])
    assert (GUIDE <= shown) is armed, (text, sorted(shown))


def test_the_coding_offer_is_a_door_entry_a_lane_can_add():
    """The offer has a door's shape: ``collect_doors`` keeps it beside the
    Connect door (one address each), and ``with_connect_door`` never drops
    it."""
    offer = coding_offer("build me a todo app")
    assert offer is not None and set(offer) == {"href", "label"}
    kept = doors.collect_doors(doors.with_connect_door([offer], [{"id": "gmail", "name": "Gmail"}]))
    assert offer in kept and len(kept) == 2, kept
    assert coding_offer("") is None and coding_offer(None) is None  # type: ignore[arg-type]
