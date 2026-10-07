"""v1.312.0 (W4-2) — a conversation's grants survive a reopen.

The chat page records "Allow for this conversation" in the thread setup as
``granted_tools`` and sends it on every turn. The page writes it on
``PUT /chat/threads/{id}`` and reads it back on reopen — but the daemon's
setup whitelist (``routes/chat.py::_clean_setup``) is what decides what is
stored, and a key it does not know is dropped silently. That is the v1.279.0
"reasoning" bug class: the page round-trip test passes against a mocked GET
while the real daemon forgets the grant, so the card asks again after a
reopen, a thread switch or a reload.

These pins drive the REAL app factory (PUT then GET), not the page's mock.

Rules pinned:
* ``granted_tools`` round-trips: a deduplicated list of non-empty stripped
  strings, order kept.
* It is NOT capped at the arming cap (_MAX_ARMED_TOOLS) — a grant is not an
  arming, and the page records grants past the cap on purpose.
* Mistyped values are dropped, and an empty list stores nothing (``has_setup``
  stays honest).
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes.chat import _MAX_ARMED_TOOLS, _clean_setup


def test_clean_setup_keeps_granted_tools_deduplicated_and_stripped():
    out = json.loads(
        _clean_setup(
            {
                "tools": ["read_file"],
                "granted_tools": [" shell ", "write_file", "shell", "", 7, None],
            }
        )
    )
    assert out == {"tools": ["read_file"], "granted_tools": ["shell", "write_file"]}


def test_granted_tools_alone_is_a_setup_and_empty_is_nothing():
    assert json.loads(_clean_setup({"granted_tools": ["shell"]})) == {"granted_tools": ["shell"]}
    assert _clean_setup({"granted_tools": []}) == ""
    assert _clean_setup({"granted_tools": "shell"}) == ""


def test_granted_tools_is_not_capped_at_the_arming_cap():
    names = [f"tool_{i}" for i in range(_MAX_ARMED_TOOLS + 4)]
    out = json.loads(_clean_setup({"granted_tools": names}))
    assert out["granted_tools"] == names


def test_granted_tools_round_trips_through_a_saved_thread(tmp_path):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    r = client.put(
        "/chat/threads/new",
        json={
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ],
            "setup": {"tools": ["read_file"], "granted_tools": ["shell", "write_file"]},
        },
    )
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    got = client.get(f"/chat/threads/{tid}").json()
    assert got["setup"]["granted_tools"] == ["shell", "write_file"], got["setup"]
    assert got["setup"]["tools"] == ["read_file"]
