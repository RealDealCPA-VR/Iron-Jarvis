"""v1.445.0 (ease-of-use review item 2): the demo reply's note names the REAL
way out, where it is read.

Before: every demo reply ended "Choose a model above to get real answers."
On the chat page "above" was the composer's model menu, where every real row
was greyed and the reason was a hover tooltip only, so the note pointed at a
dead end. Now the chat page has a "Connect a model" button just below the
message box for as long as the demo is the default (dashboard
components/chat/DemoConnectChoice), and the note says so. Where that button
does not exist (a Build terminal's chat, Chat › Code, the phone) the note
names the place instead: Settings › Connections.

MIRROR NOTE (lock-step): both chat lanes pass ``surface=surface_for(body)``;
each lane is driven end to end here, with and without a Build pane.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import (
    DEMO_NOTE_FIX_CHAT,
    DEMO_NOTE_FIX_ELSEWHERE,
    demo_note,
    demo_note_fix,
)
from iron_jarvis.providers.adapters.mock import DEMO_OPENING
from iron_jarvis.providers.failure_words import (
    SURFACE_BUILD,
    SURFACE_PANE,
    SURFACE_PHONE,
    phone_surface,
    reset_surface,
    set_build_surface,
    set_sidebar_surface,
)

OLD_WORDS = "Choose a model above"


def test_the_two_sentences_are_plain_and_name_a_real_press():
    assert DEMO_NOTE_FIX_CHAT == (
        "To get real answers, connect a model with the button just below the message box."
    )
    assert DEMO_NOTE_FIX_ELSEWHERE == (
        "To get real answers, connect a model in Iron Jarvis under Settings › Connections."
    )


@pytest.mark.parametrize("reply", [DEMO_OPENING, ""])
@pytest.mark.parametrize("tools", [[], ["write_file"]])
def test_on_the_chat_page_the_note_points_at_the_button_below_the_box(reply, tools):
    note = demo_note(tools, reply)
    assert DEMO_NOTE_FIX_CHAT in note, note
    assert OLD_WORDS not in note, note
    assert note.count("_Note:") == 1


def test_the_short_note_says_only_what_to_do():
    assert demo_note([], DEMO_OPENING) == f"\n\n_Note: {DEMO_NOTE_FIX_CHAT}_"


@pytest.mark.parametrize("surface", [SURFACE_PANE, SURFACE_PHONE, SURFACE_BUILD])
def test_where_there_is_no_such_button_the_note_names_the_place(surface):
    note = demo_note([], DEMO_OPENING, surface=surface)
    assert note == f"\n\n_Note: {DEMO_NOTE_FIX_ELSEWHERE}_"
    assert DEMO_NOTE_FIX_CHAT not in demo_note([], "", surface=surface)


def test_the_surface_is_read_from_the_turn_when_none_is_given():
    with phone_surface():
        assert demo_note_fix() == DEMO_NOTE_FIX_ELSEWHERE
    token = set_build_surface()
    try:
        assert demo_note_fix() == DEMO_NOTE_FIX_ELSEWHERE
    finally:
        reset_surface(token)
    assert demo_note_fix() == DEMO_NOTE_FIX_CHAT


def test_the_sidebar_still_strips_the_lane_note():
    """The browser sidebar says the demo in its own line and strips the lane's
    note (browser/panel.without_lane_demo_note, which calls demo_note OUTSIDE
    the sidebar's surface). The sidebar reads the chat's words, so the note
    the lane made INSIDE the sidebar's turn is the one it strips."""
    from iron_jarvis.browser.panel import without_lane_demo_note

    token = set_sidebar_surface()
    try:
        made = DEMO_OPENING + demo_note([], DEMO_OPENING)
    finally:
        reset_surface(token)
    assert without_lane_demo_note(made) == DEMO_OPENING


def _stream_done(client: TestClient, body: dict) -> dict:
    r = client.post("/chat/stream", json=body)
    assert r.status_code == 200, r.text
    done = None
    for block in r.text.split("\n\n"):
        lines = block.strip().splitlines()
        if lines and lines[0] == "event: done":
            done = json.loads("".join(ln[6:] for ln in lines[1:] if ln.startswith("data: ")))
    assert done is not None, r.text[-1500:]
    return done


@pytest.mark.parametrize("lane", ["post", "stream"])
@pytest.mark.parametrize("pane", ["", "pane-1"])
def test_both_lanes_word_the_note_for_where_it_is_read(tmp_path, lane, pane):
    with TestClient(create_app(str(tmp_path))) as client:  # fresh install: the demo
        body: dict = {"messages": [{"role": "user", "content": "What can you do?"}]}
        if pane:
            body["pane_id"] = pane
        out = client.post("/chat", json=body).json() if lane == "post" else _stream_done(client, body)
    assert out["route"]["provider"] == "mock" and out["route"]["reason"] == "mock"
    reply = out["reply"]
    want = DEMO_NOTE_FIX_ELSEWHERE if pane else DEMO_NOTE_FIX_CHAT
    other = DEMO_NOTE_FIX_CHAT if pane else DEMO_NOTE_FIX_ELSEWHERE
    assert want in reply, reply
    assert other not in reply and OLD_WORDS not in reply, reply
    # Said once: the reply's own first sentence names the demo, the note only
    # says what to do.
    assert reply.count("demo model") == 1, reply
    assert reply.count("_Note:") == 1, reply
