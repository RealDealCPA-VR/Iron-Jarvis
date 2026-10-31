"""v1.418.0 (C4.8): a message sent while Build › Code's reply runs WAITS.

It goes as the next message when the reply ends CLEANLY (the daemon sends
it: a closed page changes nothing); after Stop, a failure or an account's
limit card nothing is sent and the queue is HELD — the page takes the words
back into the box. No reply running: refused with ``code: not_working`` (the
page sends it as a message). The queue survives a restart in the session's
own folder, and then it is held (nothing runs to send it after).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import queued as Q
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from iron_jarvis.hosted.events import make

from ._build_app_helpers_v1409 import client, stop_all
from .test_build_app_history_v1418 import fake_tool, until


@pytest.fixture
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


async def started(svc, monkeypatch, app, work):
    drivers = fake_tool(svc, monkeypatch)
    row = store.create(app.state.platform.engine, harness="claude-code", folder=str(work))
    await svc.start_turn(row.id, "first", "t1")
    await until(lambda: row.id in drivers and drivers[row.id].sent)
    return row, drivers


async def test_a_queued_message_goes_as_the_next_message_after_a_clean_end(app, work, monkeypatch):
    svc = S.service_for(app.state.platform)
    row, drivers = await started(svc, monkeypatch, app, work)
    async with client(app) as c:
        r = await c.post(f"/build/app/sessions/{row.id}/queue", json={"text": "then add a test"})
        assert r.status_code == 200, r.text
        assert [q["text"] for q in r.json()["queued"]] == ["then add a test"] and r.json()["queue_held"] is False
        view = (await c.get(f"/build/app/sessions/{row.id}")).json()
        assert [q["text"] for q in view["queued"]] == ["then add a test"]
        # Never a second turn while the first runs.
        assert [t for t, _ in drivers[row.id].sent] == ["first"]
        svc._emit(svc.live[row.id], make("done", stop="completed"))
        await until(lambda: len(drivers[row.id].sent) == 2)
        assert drivers[row.id].sent[1] == ("then add a test", [])
        view = (await c.get(f"/build/app/sessions/{row.id}")).json()
        assert view["queued"] == [] and view["running"]
        # And the next turn's question is in the record, in order.
        users = [e["text"] for e in view["events"] if e["type"] == "user"]
        assert users == ["first", "then add a test"]
    svc._emit(svc.live[row.id], make("done", stop="completed"))
    await stop_all(app)


@pytest.mark.parametrize("how", ["interrupted", "failed", "limit"])
async def test_after_stop_a_failure_or_a_limit_card_nothing_is_sent_and_the_words_come_back(app, work, monkeypatch, how):
    svc = S.service_for(app.state.platform)
    row, drivers = await started(svc, monkeypatch, app, work)
    async with client(app) as c:
        await c.post(f"/build/app/sessions/{row.id}/queue", json={"text": "wait for me", "files": []})
        live = svc.live[row.id]
        if how == "limit":
            svc._emit(live, make("error", title="limit", fix="", technical="", fix_kind="account_limit"))
            svc._emit(live, make("done", stop="completed"))
        else:
            svc._emit(live, make("done", stop=how))
        await until(lambda: live.turn is None)
        await asyncio.sleep(0.2)
        assert [t for t, _ in drivers[row.id].sent] == ["first"]
        view = (await c.get(f"/build/app/sessions/{row.id}")).json()
        assert view["queue_held"] is True and [q["text"] for q in view["queued"]] == ["wait for me"]
        taken = (await c.post(f"/build/app/sessions/{row.id}/queue/take")).json()
        assert [q["text"] for q in taken["taken"]] == ["wait for me"]
        view = (await c.get(f"/build/app/sessions/{row.id}")).json()
        assert view["queued"] == [] and view["queue_held"] is False
    await stop_all(app)


async def test_no_reply_running_is_refused_with_not_working(app, work):
    row = store.create(app.state.platform.engine, harness="claude-code", folder=str(work))
    async with client(app) as c:
        r = await c.post(f"/build/app/sessions/{row.id}/queue", json={"text": "hi"})
        assert r.status_code == 409 and r.json()["code"] == "not_working"
        r = await c.post(f"/build/app/sessions/{row.id}/queue", json={"text": "  "})
        assert r.status_code == 400


async def test_a_reply_that_already_ended_takes_no_waiting_message(app, work, monkeypatch):
    svc = S.service_for(app.state.platform)
    row, drivers = await started(svc, monkeypatch, app, work)
    svc._emit(svc.live[row.id], make("done", stop="completed"))
    await until(lambda: svc.live[row.id].turn is None)
    async with client(app) as c:
        r = await c.post(f"/build/app/sessions/{row.id}/queue", json={"text": "too late"})
        assert r.status_code == 409 and r.json()["code"] == "not_working"
    assert svc.live[row.id].queue is None or svc.live[row.id].queue.items == []
    await stop_all(app)


async def test_a_waiting_message_can_be_removed_and_the_queue_is_bounded(app, work, monkeypatch):
    svc = S.service_for(app.state.platform)
    row, drivers = await started(svc, monkeypatch, app, work)
    async with client(app) as c:
        ids = []
        for i in range(Q.MAX_QUEUED):
            r = await c.post(f"/build/app/sessions/{row.id}/queue", json={"text": f"m{i}"})
            ids.append(r.json()["id"])
        full = await c.post(f"/build/app/sessions/{row.id}/queue", json={"text": "one more"})
        assert full.status_code == 409 and "already waiting" in full.json()["detail"]
        r = await c.post(f"/build/app/sessions/{row.id}/queue/{ids[0]}/remove")
        assert [q["text"] for q in r.json()["queued"]] == [f"m{i}" for i in range(1, Q.MAX_QUEUED)]
        gone = await c.post(f"/build/app/sessions/{row.id}/queue/{ids[0]}/remove")
        assert gone.status_code == 404
    svc._emit(svc.live[row.id], make("done", stop="interrupted"))
    await stop_all(app)


async def test_the_queue_is_kept_in_the_sessions_folder_and_after_a_restart_it_is_held(app, work, monkeypatch, tmp_path):
    svc = S.service_for(app.state.platform)
    row, drivers = await started(svc, monkeypatch, app, work)
    await svc.queue_add(row.id, "survive me")
    path = Q.queue_path(svc.home, row.id)
    await until(lambda: path.exists() and "survive me" in path.read_text("utf-8"))
    await stop_all(app)
    app2 = create_app(str(tmp_path / "home"))
    svc2 = S.service_for(app2.state.platform)
    view = await svc2.view(row.id)
    assert [q["text"] for q in view["queued"]] == ["survive me"] and view["queue_held"] is True
    assert json.loads(path.read_text("utf-8"))["items"][0]["text"] == "survive me"
    await stop_all(app2)


async def test_a_queue_from_before_a_restart_is_never_sent_behind_a_new_reply(app, work, monkeypatch, tmp_path):
    """Review (v1.418.0): the restart that left messages waiting (a shutdown
    returns before holding them) — the next process must not send them after
    a NEW reply ends cleanly: they wait for the user, in the box."""
    svc = S.service_for(app.state.platform)
    row, drivers = await started(svc, monkeypatch, app, work)
    await svc.queue_add(row.id, "from before")
    path = Q.queue_path(svc.home, row.id)
    await until(lambda: path.exists() and "from before" in path.read_text("utf-8"))
    await stop_all(app)
    app2 = create_app(str(tmp_path / "home"))
    svc2 = S.service_for(app2.state.platform)
    drivers2 = fake_tool(svc2, monkeypatch)
    await svc2.start_turn(row.id, "a new message", "t2")
    await until(lambda: row.id in drivers2 and drivers2[row.id].sent)
    view = await svc2.view(row.id)  # the page reads the session mid-reply
    assert [q["text"] for q in view["queued"]] == ["from before"] and view["queue_held"] is True
    svc2._emit(svc2.live[row.id], make("done", stop="completed"))
    await until(lambda: svc2.live[row.id].turn is None)
    await asyncio.sleep(0.3)
    assert [t for t, _ in drivers2[row.id].sent] == ["a new message"]
    view = await svc2.view(row.id)
    assert view["queue_held"] is True and [q["text"] for q in view["queued"]] == ["from before"]
    await stop_all(app2)


async def test_a_queued_message_goes_with_the_model_chosen_while_it_waited(app, work, monkeypatch):
    """Review (v1.418.0): a waiting message is sent with the session's CURRENT
    model and mode (what the chips say when it goes), never what they said
    when it was queued."""
    svc = S.service_for(app.state.platform)
    row, drivers = await started(svc, monkeypatch, app, work)
    await svc.queue_add(row.id, "then this")
    seen: list[tuple[str, str]] = []
    orig = svc.start_turn

    async def spy(sid, text, turn_id="", files=None, *a, **kw):
        r = await svc._row(sid)
        seen.append((r.model, r.mode))
        return await orig(sid, text, turn_id, files, *a, **kw)

    monkeypatch.setattr(svc, "start_turn", spy)
    await asyncio.to_thread(store.update, app.state.platform.engine, row.id, model="claude-opus-x", mode="plan")
    svc._emit(svc.live[row.id], make("done", stop="completed"))
    await until(lambda: len(drivers[row.id].sent) == 2)
    assert seen == [("claude-opus-x", "plan")]
    svc._emit(svc.live[row.id], make("done", stop="completed"))
    await stop_all(app)


async def test_while_the_next_waiting_message_starts_the_rest_are_not_handed_back(app, work, monkeypatch):
    """Review (v1.418.0): between a clean end and the queued message's turn
    starting there is a moment with nothing running. A view in that moment
    (the page reads the session right after the reply ends) must not call
    the rest of the queue HELD — the page would pull a message the daemon was
    about to send back into the box."""
    svc = S.service_for(app.state.platform)
    row, drivers = await started(svc, monkeypatch, app, work)
    await svc.queue_add(row.id, "second")
    await svc.queue_add(row.id, "third")
    gate = asyncio.Event()
    entered = asyncio.Event()
    orig = svc.start_turn

    async def slow(sid, text, turn_id="", files=None, *a, **kw):
        entered.set()
        await gate.wait()
        return await orig(sid, text, turn_id, files, *a, **kw)

    monkeypatch.setattr(svc, "start_turn", slow)
    svc._emit(svc.live[row.id], make("done", stop="completed"))
    await asyncio.wait_for(entered.wait(), 10)
    view = await svc.view(row.id)
    assert not view["running"]
    assert [q["text"] for q in view["queued"]] == ["third"] and view["queue_held"] is False
    gate.set()
    await until(lambda: len(drivers[row.id].sent) == 2)
    svc._emit(svc.live[row.id], make("done", stop="completed"))
    await until(lambda: len(drivers[row.id].sent) == 3)
    assert [t for t, _ in drivers[row.id].sent] == ["first", "second", "third"]
    svc._emit(svc.live[row.id], make("done", stop="completed"))
    await stop_all(app)
