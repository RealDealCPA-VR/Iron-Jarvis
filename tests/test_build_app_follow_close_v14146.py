"""v1.414.6: a follower that stops listening while a chunk is being SENT
stops counting as watching the reply.

The Release gate timed out in `test_a_waiting_pi_card_is_on_the_bell_by_name_only`
waiting for a cancelled request to stop "watching" its reply. The heartbeat
response (`routes/chat.HeartbeatStreamingResponse`) read the reply's stream in
a plain `async for`; a cancel that landed during `send` (not inside the
stream's own wait) left the stream suspended and its follower registered for
as long as anything held the cancelled request — and an unwatched reply is
what puts its card on the bell and the phone. The response now closes the
stream when it stops. Here every chunk's send takes 0.5 s, so the cancel lands
in a send.
"""

from __future__ import annotations

import asyncio

import httpx

from iron_jarvis.hosted import service as S
from tests._build_app_helpers_v1409 import new_session, stop_all, until
from tests.test_build_app_pi_v1411 import _fast_handled, app, use_fake_pi, work  # noqa: F401 (fixtures)


async def test_a_cancel_during_a_send_stops_watching_the_reply(app, work, tmp_path, monkeypatch):
    use_fake_pi(monkeypatch, "pi_edit_confirm", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)

    async def slow_sends(scope, receive, send):
        async def slow(msg):
            if msg.get("type") == "http.response.body" and msg.get("body"):
                await asyncio.sleep(0.5)
            await send(msg)

        await app(scope, receive, slow)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=slow_sends), base_url="http://testserver", timeout=60) as c:
        s = await new_session(c, work, harness="pi")
        task = asyncio.ensure_future(c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "do it", "turn_id": "t1"}))
        rid = await until(lambda: svc.live.get(s["id"]) and next(iter(svc.live[s["id"]].asks), None))
        await asyncio.sleep(0.1)  # the card's chunk is being sent now
        task.cancel()
        await until(lambda: not svc.turn("t1").watched, timeout=5.0)
        await c.post(f"/build/app/sessions/{s['id']}/answer", json={"request_id": rid, "decision": "deny"})
    await stop_all(app)
