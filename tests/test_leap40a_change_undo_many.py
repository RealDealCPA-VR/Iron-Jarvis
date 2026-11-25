"""Next leaps 40-a (R2.18, D-40.1): "undo everything from the 3 pm run" in chat.

ONE card names every change it will undo, the ones that cannot be undone
FIRST with their reason; it is a once-only SAFETY card (Enter never allows
it, the phone never gets it). After it, the reply says "12 files put back"
and names the one it could not put back, with why. The card's list is what
runs: a change that lands while the card waits is not undone, and a denied
card undoes nothing.

Through the real stream lane (``/chat/stream``: the arming, the gate, the
card answered through ``/chat/approvals``), the real write tool and the real
``perform_undo``.
"""

from __future__ import annotations

import json

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.harness import stall
from iron_jarvis.settings import records_day as RD
from iron_jarvis.settings import records_history as H
from tests.test_chat_approvals_v1187 import _drive_stream
from tests.test_chat_history_tools_v14350 import _approver, _ctx, _finished, _out
from tests.test_leap40a_day_changes import _app, _local_utc, _run, _sent, _stamp, _write
from tests.test_settings_chat_tools_v1321 import _body, _calls_stream

UNDO = "change_undo"


async def _three_pm(tmp_path, *, extra_run: bool = True):
    """A 3 pm run that wrote 13 files and sent a message, and a 9 am run."""
    app, p = _app(tmp_path)
    day = RD.today_local()
    ws = tmp_path / "ws"
    ws.mkdir()
    for i in range(13):
        (ws / f"r{i}.txt").write_text(f"old {i}\n", encoding="utf-8")
    morning = _run(p, "Sort the inbox", at=_local_utc(day, 9, 0), ws=ws) if extra_run else None
    if morning:
        _stamp(p, [await _write(p, ws, morning, "inbox.txt", "sorted\n")], _local_utc(day, 9, 1))
    run = _run(p, "Rename the client files", at=_local_utc(day, 15, 2), ws=ws)
    ids = [await _write(p, ws, run, f"r{i}.txt", f"new {i}\n") for i in range(13)]
    _stamp(p, ids, _local_utc(day, 15, 3))
    # An old row that kept a journal entry for a call that left this PC: it
    # is still never a change (records_history.list_changes leaves it out).
    _sent(p, run, "notify", _local_utc(day, 15, 20), journal=True)
    return app, p, ws, run, ids, morning


@pytest.mark.asyncio
async def test_undo_everything_from_the_3_pm_run_puts_twelve_back_through_one_card_and_names_the_one_it_could_not(tmp_path):
    app, p, ws, run, ids, morning = await _three_pm(tmp_path)
    (ws / "r7.txt").write_text("the user's own edit\n", encoding="utf-8")  # changed since
    p.router.stream = _calls_stream([(UNDO, {"run": "the 3 pm run"})])
    seen: list = []
    frames = await _drive_stream(app, _body("undo everything from the 3 pm run"), _approver(app, seen))
    (card,) = seen
    assert card["tool"] == UNDO
    assert card["once_only"] is True and card.get("once_reason") == H.UNDO_REASON and card["can_always"] is False
    words = card.get("cost_note") or ""
    # The ones that cannot be undone come first, with why; then every change by name.
    assert words.startswith("Can't be undone"), words
    assert "sent a notification" in words and "left this PC" in words
    assert words.index("Can't be undone") < words.index("Undo these 13 changes")
    assert "the run “Rename the client files” (started 3:02 pm)" in words
    for i in range(13):
        assert f"r{i}.txt" in words, (i, words)
    assert "inbox.txt" not in words, "the 9 am run is not the 3 pm run"
    (fin,) = _finished(frames, UNDO)
    out = _out(fin)
    assert fin["ok"] is True, out
    assert out.startswith("12 files put back."), out
    assert "Not put back: r7.txt (it was changed again since" in out
    for i in range(13):
        want = "the user's own edit\n" if i == 7 else f"old {i}\n"
        assert (ws / f"r{i}.txt").read_text(encoding="utf-8") == want
    assert (ws / "inbox.txt").read_text(encoding="utf-8") == "sorted\n"


@pytest.mark.asyncio
async def test_the_card_is_a_safety_card_enter_and_the_phone_never_allow_it(tmp_path):
    assert H.UNDO_REASON in stall.SAFETY_REASONS
    assert not stall.phone_may_ask(once_only=True, local_ask=False, priced=False)
    from iron_jarvis.agents import mission

    # Review (40-a): one card undoes one change or many, so its title and the
    # ledger's line say neither ("wants to undo what it changed").
    assert mission.ASK_WORDS[UNDO] == "undo what it changed"
    assert mission.fallback_line(UNDO, True) == "undid what it changed"
    app, p, ws, run, ids, _m = await _three_pm(tmp_path)
    p.router.stream = _calls_stream([(UNDO, {"run": "the 3 pm run"})])
    seen: list = []
    await _drive_stream(app, _body("undo everything from the 3 pm run", mode="yolo"), _approver(app, seen, "deny"))
    assert [s["tool"] for s in seen] == [UNDO], "it cards even in Don't ask"
    assert seen[0]["once_only"] is True and seen[0].get("once_reason") == H.UNDO_REASON, seen[0]
    assert all((ws / f"r{i}.txt").read_text(encoding="utf-8") == f"new {i}\n" for i in range(13)), "a denied card undoes nothing"


@pytest.mark.asyncio
async def test_the_cards_list_is_what_runs_a_change_landing_while_it_waits_stays(tmp_path):
    app, p, ws, run, ids, _m = await _three_pm(tmp_path, extra_run=False)
    p.router.stream = _calls_stream([(UNDO, {"run": "the 3 pm run"})])

    async def newer_then_approve(data):
        await _write(p, ws, run, "late.txt", "written while the card waited\n")
        from tests.test_chat_approvals_v1187 import _asgi_post

        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "once"})
        assert status == 200

    frames = await _drive_stream(app, _body("undo everything from the 3 pm run"), newer_then_approve)
    (fin,) = _finished(frames, UNDO)
    assert fin["ok"] is True and _out(fin).startswith("13 files put back."), _out(fin)
    assert (ws / "late.txt").read_text(encoding="utf-8") == "written while the card waited\n"


@pytest.mark.asyncio
async def test_undo_by_ids_names_each_and_a_long_list_is_refused_in_words(tmp_path):
    app, p, ws, run, ids, _m = await _three_pm(tmp_path, extra_run=False)
    p.router.stream = _calls_stream([(UNDO, {"action_ids": ids[:2]})])
    seen: list = []
    frames = await _drive_stream(app, _body("undo these two changes"), _approver(app, seen))
    assert "Undo these 2 changes: r1.txt, r0.txt." in seen[0]["cost_note"], seen[0]["cost_note"]
    (fin,) = _finished(frames, UNDO)
    assert _out(fin).startswith("2 files put back.")
    tool = p.registry.get(UNDO)
    ctx = _ctx(p, tmp_path)
    check = await tool.spend_check({"action_ids": [f"tool_{i}" for i in range(51)]}, ctx)
    assert check is not None and check.ask is False
    res = await tool.execute({"action_ids": [f"tool_{i}" for i in range(51)]}, ctx)
    assert res.ok is False and "50" in (res.error or "")


@pytest.mark.asyncio
async def test_a_time_no_run_started_near_names_the_days_runs(tmp_path):
    app, p, ws, run, ids, _m = await _three_pm(tmp_path)
    p.router.stream = _calls_stream([(UNDO, {"run": "the 11 pm run"})])
    # Review (40-a): a card here is answered at once (denied) and fails the
    # test fast, instead of waiting out the card's time.
    seen: list = []
    frames = await _drive_stream(app, _body("undo everything from the 11 pm run"), _approver(app, seen, "deny"))
    assert seen == [], "no run near the time asked: no card"
    (fin,) = _finished(frames, UNDO)
    out = _out(fin)
    assert fin["ok"] is False and "No run started near 11:00 pm" in out
    assert "“Sort the inbox” at 9:00 am" in out and "“Rename the client files” at 3:02 pm" in out
    assert all((ws / f"r{i}.txt").read_text(encoding="utf-8") == f"new {i}\n" for i in range(13))


def test_the_requests_arm_the_undo_and_near_misses_do_not():
    for text in (
        "undo everything from the 3 pm run",
        "undo everything from this morning's run",
        "revert all the changes from the 3pm task",
        "put back everything you changed today",
        "undo all of these changes",
        "undo these two changes",
        "roll back everything the 15:00 schedule did",
    ):
        assert UNDO in H.history_tools_for(text), text
    for text in (
        "undo everything in this paragraph",
        "undo the damage the storm did",
        "undo all the formatting in my letter",
        "how do I undo everything from a run?",
        "what should I do about these changes to the lease?",
        "revert the contract to the original wording",
    ):
        assert UNDO not in H.history_tools_for(text), text
    assert CT is not None  # both lanes arm through records.record_tools_for (one function)


@pytest.mark.asyncio
async def test_changes_list_by_date_lists_the_day_and_what_cannot_be_undone(tmp_path):
    app, p, ws, run, ids, _m = await _three_pm(tmp_path)
    res = await p.registry.get("changes_list").execute({"date": RD.today_local().isoformat()}, _ctx(p, tmp_path))
    assert res.ok, res.error
    out = res.output
    assert "From the run “Rename the client files” (started 3:02 pm):" in out
    assert "Can't be undone" in out and "sent a notification" in out
    assert "r12.txt" in out and "inbox.txt" in out
    assert "Commands Iron Jarvis ran" in out
    data = res.data
    assert data["date"] == RD.today_local().isoformat() and data["cannot"][0]["what"] == "sent a notification"
    # Review (40-a): narrowed to one kind, the list leaves out what cannot be
    # undone, and never says there was none of it.
    for args in ({"date": "today", "kind": "file"}, {"kind": "file"}):
        narrowed = await p.registry.get("changes_list").execute(args, _ctx(p, tmp_path))
        assert narrowed.ok, narrowed.error
        assert "there were none" not in narrowed.output, narrowed.output
        assert "file changes only" in narrowed.output and "1 action" in narrowed.output, narrowed.output
        assert "sent a notification" not in narrowed.output and narrowed.data["cannot"] == []
    quiet = await p.registry.get("changes_list").execute({"kind": "memory"}, _ctx(p, tmp_path))
    assert "there were none" not in quiet.output and "1 action" in quiet.output, quiet.output
    # Review (40-a): the sent message kept a journal entry, yet it is only
    # ever under "Can't be undone", never one of the changes.
    (sent,) = data["cannot"][0]["ids"]
    assert sent not in {r["id"] for r in data["changes"]} and sent not in out.split("Can't be undone")[0]
    by_run = await p.registry.get("changes_list").execute({"run": "the 3 pm run"}, _ctx(p, tmp_path))
    assert "inbox.txt" not in by_run.output and "r0.txt" in by_run.output
    assert sent not in {r["id"] for r in by_run.data["changes"]} and by_run.data["cannot"][0]["ids"] == [sent]
    with session_scope(p.engine) as db:
        assert not list(db.exec(select(ToolInvocation).where(ToolInvocation.undone_at != None)))  # noqa: E711
    assert json.dumps(data)  # plain data, names only


@pytest.mark.asyncio
async def test_everything_from_a_run_includes_the_helpers_it_started(tmp_path):
    """A run's helpers run as their own runs (``AgentRun.parent_id``); "undo
    everything from the 3 pm run" names and puts back their changes too."""
    from iron_jarvis.core.models import AgentRun

    app, p, ws, run, ids, _m = await _three_pm(tmp_path, extra_run=False)
    helper = _run(p, "check the renamed files", at=_local_utc(RD.today_local(), 15, 40), ws=ws)
    with session_scope(p.engine) as db:
        db.add(AgentRun(id="run_lead", session_id=run))
        db.add(AgentRun(id="run_helper", session_id=helper, parent_id="run_lead"))
        db.commit()
    (ws / "helper.txt").write_text("before\n", encoding="utf-8")
    await _write(p, ws, helper, "helper.txt", "the helper's edit\n")
    p.router.stream = _calls_stream([(UNDO, {"run": "the 3 pm run"})])
    seen: list = []
    frames = await _drive_stream(app, _body("undo everything from the 3 pm run"), _approver(app, seen))
    assert "helper.txt" in seen[0]["cost_note"] and "Undo these 14 changes" in seen[0]["cost_note"]
    (fin,) = _finished(frames, UNDO)
    assert _out(fin).startswith("14 files put back."), _out(fin)
    assert (ws / "helper.txt").read_text(encoding="utf-8") == "before\n"


@pytest.mark.asyncio
async def test_a_local_only_runs_names_stay_out_of_a_turn_that_may_not_recall_them(tmp_path):
    """The history never tells a turn that may not recall Local only work the
    NAME of a change made there, nor the name of the run it came from."""
    app, p = _app(tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    day = RD.today_local()
    secret_run = _run(p, "Hartley garden plans", at=_local_utc(day, 15, 0), ws=ws,
                      options={"local_only": True})
    await _write(p, ws, secret_run, "hartley-plans.txt", "x\n")
    ctx = _ctx(p, tmp_path)
    listing = p.registry.get("changes_list")
    res = await listing.execute({"date": "today"}, ctx)
    assert res.ok, res.error
    assert "Hartley" not in res.output and "hartley-plans" not in res.output, res.output
    assert "Hartley" not in json.dumps(res.data) and "hartley-plans" not in json.dumps(res.data)
    assert "From Local only work:" in res.output
    # Review (40-a): a Local only run with NO change of its own is not named
    # either — not in the reply, not in data["source"].
    _run(p, "Hartley pond survey", at=_local_utc(day, 13, 0), ws=ws, options={"local_only": True})
    quiet = await listing.execute({"run": "the 1 pm run"}, ctx)
    assert quiet.ok, quiet.error
    assert "Hartley" not in quiet.output and "Hartley" not in json.dumps(quiet.data), (quiet.output, quiet.data)
    assert quiet.data["source"]["name"] == H.LOCAL_SOURCE_WORDS
    # Review (40-a): no run near the time asked, or no time given — the
    # day's runs are named, a Local only run only as "a Local only run",
    # in changes_list's refusal and in change_undo's alike.
    _run(p, "Sort the inbox", at=_local_utc(day, 9, 0), ws=ws)
    undo = p.registry.get(UNDO)
    for args in ({"run": "the 11 pm run"}, {"run": "the morning run"}):
        res = await listing.execute(args, ctx)
        assert res.ok is False and "Hartley" not in (res.error or ""), res.error
        assert "a Local only run at 3:00 pm" in res.error and "“Sort the inbox” at 9:00 am" in res.error, res.error
        check = await undo.spend_check(args, ctx)
        assert check is not None and check.ask is False
        res = await undo.execute(args, ctx)
        assert res.ok is False and "Hartley" not in (res.error or ""), res.error
        assert "a Local only run at 3:00 pm" in res.error, res.error
    # The person's own page names it.
    from fastapi.testclient import TestClient

    page = TestClient(app).get("/changes/day?date=today").text
    assert "Hartley garden plans" in page and "hartley-plans.txt" in page


@pytest.mark.asyncio
async def test_only_a_local_only_chats_changes_read_as_local_only_work(tmp_path):
    """Review (40-a, a note): every chat reply is one source ("chat") until
    40-b stamps the turn, so changes_list's day splits chat's changes: a
    Local only chat's read "From Local only work:" with no name, and an
    ordinary chat's stay "In chat:", named."""
    from iron_jarvis.core.models import AgentRun, ChatThreadRecord

    app, p = _app(tmp_path)
    ws = tmp_path / "ws"
    day = RD.today_local()
    with session_scope(p.engine) as db:
        db.add(ChatThreadRecord(id="chat_lo", setup_json=json.dumps({"local_only": True})))
        db.add(AgentRun(id="run_lo_turn", session_id="chat", thread_id="chat_lo",
                        created_at=_local_utc(day, 10, 0), finished_at=_local_utc(day, 10, 5)))
        db.commit()
    _stamp(p, [await _write(p, ws, "chat", "garden-plans.txt", "x\n")], _local_utc(day, 10, 2))
    _stamp(p, [await _write(p, ws, "chat", "shopping.txt", "y\n")], _local_utc(day, 11, 0))
    res = await p.registry.get("changes_list").execute({"date": "today"}, _ctx(p, tmp_path))
    assert res.ok, res.error
    lines = res.output.splitlines()
    assert "garden-plans" not in res.output, res.output
    heads = [i for i, line in enumerate(lines) if line.endswith(":") and not line.startswith("- ")]
    shop = next(i for i, line in enumerate(lines) if "shopping.txt" in line)
    lo = next(i for i, line in enumerate(lines) if H.LOCAL_WORK_WORDS in line)
    assert lines[max(h for h in heads if h < shop)] == "In chat:", res.output
    assert lines[max(h for h in heads if h < lo)] == "From Local only work:", res.output


@pytest.mark.asyncio
async def test_the_3_pm_run_is_never_a_helpers_run_or_a_rehearsal(tmp_path):
    """Review (40-a): "the 3:41 pm run" is the run a person started — never a
    helper's run (it is part of its lead's), never a rehearsal (it changed
    nothing) — even when one of them started nearer the time asked."""
    from iron_jarvis.core.models import AgentRun

    app, p = _app(tmp_path)
    ws = tmp_path / "ws"
    day = RD.today_local()
    lead = _run(p, "Rename the client files", at=_local_utc(day, 15, 2), ws=ws)
    helper = _run(p, "check the renamed files", at=_local_utc(day, 15, 40), ws=ws)
    _run(p, "Rename the client files", at=_local_utc(day, 15, 41), ws=ws, options={"rehearsal_id": "rh_1"})
    with session_scope(p.engine) as db:
        db.add(AgentRun(id="run_lead", session_id=lead))
        db.add(AgentRun(id="run_helper", session_id=helper, parent_id="run_lead"))
        db.commit()
    source, family, problem = RD.resolve_run(p.engine, "the 3:41 pm run")
    assert problem == "" and source["id"] == lead, (source, problem)
    assert family == [lead, helper]
    _s, _f, words = RD.resolve_run(p.engine, "the run")
    assert "at 3:02 pm" in words and "3:40" not in words and "3:41" not in words, words


@pytest.mark.asyncio
async def test_the_result_answers_every_change_the_card_named(tmp_path):
    """Review (40-a): "The result says how many were put back and names each
    that was not, with why" — a change whose before-copy is gone (the card's
    "Not undone") and one already undone are in the reply too."""
    from pathlib import Path

    from fastapi.testclient import TestClient

    from iron_jarvis.core.models import UndoJournal

    app, p = _app(tmp_path)
    day = RD.today_local()
    ws = tmp_path / "ws"
    ws.mkdir()
    for name in ("r0.txt", "r1.txt", "r2.txt"):
        (ws / name).write_text(f"old {name}\n", encoding="utf-8")
    (ws / "big.txt").write_text("x" * 20_000 + "\n", encoding="utf-8")
    run = _run(p, "Rename the client files", at=_local_utc(day, 15, 2), ws=ws)
    ids = [await _write(p, ws, run, n, f"new {n}\n") for n in ("r0.txt", "r1.txt", "r2.txt", "big.txt")]
    _stamp(p, ids, _local_utc(day, 15, 3))
    with session_scope(p.engine) as db:
        ref = db.get(UndoJournal, ids[3]).pre_ref
    assert ref, "a big before-copy is kept as a file under undo/"
    (Path(p.config.home) / "undo" / ref).unlink()
    assert TestClient(app).post("/changes/undo", json={"action_ids": [ids[2]]}).json()["reverted"]
    p.router.stream = _calls_stream([(UNDO, {"run": "the 3 pm run"})])
    seen: list = []
    frames = await _drive_stream(app, _body("undo everything from the 3 pm run"), _approver(app, seen))
    note = seen[0]["cost_note"]
    assert "Undo these 2 changes" in note and "Not undone: big.txt" in note and "1 was already undone" in note, note
    (fin,) = _finished(frames, UNDO)
    out = _out(fin)
    assert fin["ok"] is True and out.startswith("2 files put back."), out
    assert f"Not put back: big.txt ({H.NO_COPY_KEPT})." in out, out
    assert "1 was already undone." in out, out
    assert (ws / "r0.txt").read_text(encoding="utf-8") == "old r0.txt\n"


@pytest.mark.asyncio
async def test_with_no_card_held_nothing_is_undone(tmp_path):
    """Fail closed: a many-at-once undo runs only the list ITS card named."""
    app, p, ws, run, ids, _m = await _three_pm(tmp_path, extra_run=False)
    res = await p.registry.get(UNDO).execute({"run": "the 3 pm run"}, _ctx(p, tmp_path))
    assert res.ok is False and "no card named these changes" in (res.error or "")
    assert all((ws / f"r{i}.txt").read_text(encoding="utf-8") == f"new {i}\n" for i in range(13))
