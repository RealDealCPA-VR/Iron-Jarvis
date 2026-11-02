"""v1.426.0 (Wave 6, E2) — the chat's coding offer opens a coding session
with the request already typed; it is never retyped.

THE AUDIT: "Chat's 'use a coding session' offer opens Build › Code empty: the
request is retyped" (``tools/autoselect.coding_offer``, v1.420.0). The offer
is wired into BOTH chat lanes (``chat_turn.with_coding_offer``, MIRROR NOTE)
and now opens Chat › Code — the coding session the owner uses — where the
dashboard puts the user's request into the start composer's box
(``dashboard/lib/codingStart``: this tab's sessionStorage, never the
address) with the chat's project folder chosen; nothing is sent until the
user presses Send.

Pinned here: the door's address and words, the same door in both lanes (a
real stream turn and a real POST turn; the model only answers in words), and
the dashboard's own copy of the address held equal to the daemon's.
"""

from __future__ import annotations

import re
from pathlib import Path

from iron_jarvis.tools.autoselect import CODING_OFFER_HREF, CODING_OFFER_LABEL, coding_offer
from tests.test_connect_door_v13590 import (  # noqa: F401 — fixtures by name
    _PostModel,
    _StreamModel,
    _body,
    _clean_env,
    _done,
    _stream,
    app,
)
from tests.test_lane_j_review_v1420 import SOFTWARE, _post_res, _stream_done

ROOT = Path(__file__).resolve().parent.parent
DOOR = {"href": "/chat?view=code", "label": "Work on this in a coding session (Chat › Code)"}


def test_the_door_opens_chat_code_and_says_so():
    assert coding_offer("build me a todo app") == DOOR
    assert (CODING_OFFER_HREF, CODING_OFFER_LABEL) == (DOOR["href"], DOOR["label"])
    # The address never carries the request (the dashboard hands it over
    # through this tab's own storage): a door is the same for every request.
    assert coding_offer("fix the failing test in my repo") == DOOR


def test_both_lanes_end_a_request_to_build_software_with_the_same_door(app):  # noqa: F811
    done = _stream_done(app, SOFTWARE)
    assert DOOR in done["doors"], done["doors"]
    res = _post_res(app, SOFTWARE)
    assert DOOR in res["doors"], res["doors"]


def test_the_dashboards_copy_of_the_address_is_the_daemons():
    src = (ROOT / "dashboard" / "lib" / "codingStart.ts").read_text(encoding="utf-8")
    m = re.search(r'export const CODING_OFFER_HREF = "([^"]+)";', src)
    assert m and m.group(1) == CODING_OFFER_HREF
    # …and it is the chat page's own Code view address (lib/chatSurface).
    surface = (ROOT / "dashboard" / "lib" / "chatSurface.ts").read_text(encoding="utf-8")
    assert f'export const CODE_VIEW_HREF = "{CODING_OFFER_HREF}";' in surface


def test_the_chat_page_hands_the_door_to_the_coding_start():
    """The chat page's door press goes through lib/codingStart (the request
    before the reply, the chat's project folder), and the Code view takes it."""
    page = (ROOT / "dashboard" / "app" / "chat" / "page.tsx").read_text(encoding="utf-8")
    assert "onOpen={(d) => h.openDoor(i, d)}" in page
    # v1.426.0 review (moved on purpose): the chat's Local only rides along.
    assert "startCodingFromDoor(messagesRef.current, index, door.href, atFilesRoot, localOnlyRef.current)" in (
        " ".join(page.split())
    )
    view = (ROOT / "dashboard" / "components" / "harness" / "IjHarnessView.tsx").read_text(encoding="utf-8")
    assert "peekCodingStart()" in view and "clearCodingStart()" in view
