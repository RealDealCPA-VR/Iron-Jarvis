"""v1.417.0 (C1.11) — a Build › Code card answered from the PHONE reaches the
autonomy metrics as the phone's answer (``actor: "phone"``), always ONCE —
:class:`iron_jarvis.hosted.news.PhoneWatch` drives the service's own
``answer`` with it."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from iron_jarvis.hosted import news as NEWS
from iron_jarvis.hosted.driver import Ask


async def test_the_phone_answers_once_and_says_it_was_the_phone(monkeypatch):
    called: list = []
    loop = asyncio.get_running_loop()
    fut = loop.create_future()

    class _Approvals:
        def request(self, tool, args, session_id=""):
            return "apv_1", fut

        def pop(self, _id):
            return None

    class _Svc:
        platform = SimpleNamespace(approvals=_Approvals())
        engine = None

        async def answer(self, sid, ask_id, decision, message="", answers=None, *, actor="app"):
            called.append((sid, ask_id, decision, actor))

    class _Phone:
        def tick(self):
            pass

        def close(self, _d):
            pass

    monkeypatch.setattr(NEWS._BuildAppPhoneAsk, "make", staticmethod(lambda *a, **k: _Phone()))
    monkeypatch.setattr("iron_jarvis.hosted.store.get", lambda engine, sid: SimpleNamespace(mode="ask", title="T"))
    ask = Ask("ask_1", "approval", "Bash", 1, {}, {"type": "approval"}, "", True)
    live = SimpleNamespace(sid="app_1", asks={"ask_1": ask})
    watch = NEWS.PhoneWatch(_Svc(), live, ask, after_s=0.0)
    watch.start()
    await asyncio.sleep(0.1)
    fut.set_result("conversation")  # the phone's "approve" — still ONCE
    await asyncio.wait_for(watch.task, 5)
    assert called == [("app_1", "ask_1", "allow", "phone")]
