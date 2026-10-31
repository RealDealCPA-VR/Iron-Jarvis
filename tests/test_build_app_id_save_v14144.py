"""v1.414.4: a Build › Code reply ends only after the tool's own session id
is on the row.

The id was saved in the background when the tool reported it, with nothing
waiting for it, so a read right after the reply ("Open in terminal", a reopen,
a test) could find it empty — three CI gates read it before it landed. Here
the save is held back 0.8 s and the row is read the moment the reply ends.
"""

from __future__ import annotations

import asyncio

from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from tests._build_app_helpers_v1409 import client, new_session, stop_all
from tests.test_build_app_opencode_v1413 import MODEL, app, use_fake_oc, work  # noqa: F401 (fixtures)


async def test_the_reply_ends_with_the_tools_session_id_saved(app, work, tmp_path, monkeypatch):
    real = store.update

    def slow_update(engine, sid, **fields):
        if "harness_session_id" in fields:
            import time

            time.sleep(0.8)
        return real(engine, sid, **fields)

    monkeypatch.setattr(S.store, "update", slow_update)
    use_fake_oc(monkeypatch, "oc_resume", tmp_path / "fake.log")
    async with client(app) as c:
        s = await new_session(c, work, harness="opencode", model=MODEL, mode="full", confirm=True)
        await asyncio.wait_for(c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "x", "turn_id": "t1"}), 30)
        hid = store.get(app.state.platform.engine, s["id"]).harness_session_id
    await stop_all(app)
    assert hid.startswith("ses_"), "the row names the tool's session the moment the reply ends"
