"""v1.422.0 review (Wave 2 "Never lose work", B1): a FAILED reply's kept rounds
(``build-failed:<turn>``) belong to the ONE session that made them.

THE DEFECTS (found in review):

1. BRANCH FROM HERE copied the failed reply's line WITH ``canContinue`` (it is
   a ``LINE_KEYS`` key) and its ``turnId``, and the daemon found a failed
   reply's kept rounds by the TURN ID alone — so "Continue from where it
   stopped" in the BRANCH took the ORIGINAL session's kept rounds (moving them
   away: the original's own Continue then answered "can no longer be
   continued"), and carried on in the branch's folder from rounds whose work
   was done in the original's (a branch on its own git branch does not carry
   uncommitted work). Now the kept rounds are found only for the session that
   made them, and a branch's copied line offers no Continue.

2. DELETING A SESSION left its failed reply's kept rounds — a copy of the
   whole conversation, every tool output included — in the database (and
   every backup) for a week: nothing but a newer turn of THAT session or the
   seven-day prune ever removed them. Now a deleted session's kept rounds go
   with it.

Driven through the REAL app factory, the real stream lane and the real tool
registry; only the model (the router) is a scripted double, and the
patience's waits return at once.
"""

from __future__ import annotations

from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import reply_record as RR
from tests.test_build_continue_failed_v14220 import (  # noqa: F401 — fixtures
    _fail_mid_reply,
    _reply,
    _write,
    home,
    no_waits,
    work,
)
from tests.test_build_sessions_v1367 import Script, asgi, new_session, open_turn, wire


async def test_a_branch_never_takes_the_original_sessions_kept_rounds(home, work):
    app = create_app(home)
    sid = new_session(app, work)["id"]
    await _fail_mid_reply(app, sid)
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/branch", {"turn_id": "bt-f", "at": "reply"})
    assert status == 201, out
    bid = out["id"]
    # The branch's copy of the failed reply offers no Continue (its rounds
    # are the original's, worked in the original's folder).
    copied = _reply(app, bid, "bt-f")
    assert copied.get("error") and "canContinue" not in copied, copied
    # Asked anyway, it is refused — and the original's kept rounds stay put.
    wire(app, Script(["never asked"]))
    status, raw = await open_turn(app, bid, {"text": "", "continue_turn": "bt-f", "turn_id": "bt-b"})
    assert status == 409 and "can no longer be continued" in raw, raw[-300:]
    assert RR.has_failed(app.state.platform.engine, "bt-f"), "the branch took the original's kept rounds"
    # The original still carries on from them.
    wire(app, Script([_write("c3", "three.txt", "3"), "All three made."]))
    status, raw = await open_turn(app, sid, {"text": "", "continue_turn": "bt-f", "turn_id": "bt-c"}, timeout=60)
    assert status == 200 and "event: done" in raw, raw[-400:]
    assert _reply(app, sid, "bt-c")["content"] == "All three made."


async def test_deleting_a_session_removes_its_failed_replys_kept_rounds(home, work):
    app = create_app(home)
    sid = new_session(app, work)["id"]
    await _fail_mid_reply(app, sid)
    assert RR.has_failed(app.state.platform.engine, "bt-f")
    status, out = await asgi(app, "DELETE", f"/build/sessions/{sid}")
    assert status == 200, out
    assert not RR.has_failed(app.state.platform.engine, "bt-f"), "a deleted session's conversation was kept"
