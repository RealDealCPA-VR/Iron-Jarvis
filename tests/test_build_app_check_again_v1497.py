"""v1.497.0: Build › Code's reply is CHECKED AGAIN (``hosted/check_again``).

A clean reply the user started whose OWN record says it is not done — a file
its final words say it saved that is not in the session's folder, or a reply
that ends by giving up after real work — is sent back ONCE through the
carry-on path (a hidden ``user`` event with ``carry.why == "check"``), never
as the user's bubble. At most two follow-ups per user message; the same
problems twice = stuck; a "Blocked:" line, Stop, a waiting message, Emergency
stop, another carry-on and a share spelling all leave the reply alone. When the
limit or stuck ends it with a problem still open, ONE notice says so.

The coding tool is the in-process ``FakeDriver`` the v1.418.0 tests use: the
test writes what the tool "said" with ``svc._emit`` and ends its turn.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import carry_on as CO
from iron_jarvis.hosted import check_again as CA
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from iron_jarvis.hosted.events import make

from ._build_app_helpers_v1409 import stop_all
from .test_build_app_history_v1418 import fake_tool, until


@pytest.fixture
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.fixture
def nudges(monkeypatch):
    seen: list[tuple[str, dict]] = []

    async def note(bus, kind, *, session_id=None, **facts):
        seen.append((kind, dict(facts)))

    import iron_jarvis.core.nudges as N

    monkeypatch.setattr(N, "note_nudge", note)
    return seen


async def started(app, monkeypatch, work, text="make the report"):
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    row = store.create(app.state.platform.engine, harness="claude-code", folder=str(work))
    await svc.start_turn(row.id, text, "t1")
    await until(lambda: row.id in drivers and drivers[row.id].sent)
    return svc, row, drivers


def reply(svc, sid, text, *, stop="completed", step=True):
    live = svc.live[sid]
    if step:
        svc._emit(live, make("tool", item="s1", kind="command", name="Bash", title="Run the build",
                             input="python build.py", status="done"))
    svc._emit(live, make("text_done", item="a1", text=text))
    svc._emit(live, make("done", stop=stop))


async def settle(svc, sid, wait=0.3):
    await until(lambda: svc.live[sid].turn is None or svc.live[sid].turn.running)
    await asyncio.sleep(wait)


async def next_send(drivers, sid, n):
    await until(lambda: len(drivers[sid].sent) >= n)
    return drivers[sid].sent[n - 1][0]


def _events(svc, sid):
    live = svc.live[sid]
    live.log.flush()
    return live.log.read(500)[0]


def notices(svc, sid):
    return [e.get("text", "") for e in _events(svc, sid) if e.get("type") == "notice"]


def users(svc, sid):
    return [e for e in _events(svc, sid) if e.get("type") == "user"]


# --------------------------------------------------------------------------- #
# The claimed file
# --------------------------------------------------------------------------- #
async def test_a_reply_claiming_a_missing_file_is_sent_back_exactly_once(app, work, monkeypatch, nudges):
    svc, row, drivers = await started(app, monkeypatch, work)
    reply(svc, row.id, "Done! I saved report.xlsx with the totals.")
    note = await next_send(drivers, row.id, 2)
    assert note.startswith(CA.NOTE_HEAD) and "`report.xlsx`" in note and "Blocked:" in note
    # Drawn as Iron Jarvis's own line (the carry mark), never the user's bubble.
    follow = users(svc, row.id)[-1]
    assert follow["carry"]["why"] == "check"
    assert follow["carry"]["note"] == (
        "Iron Jarvis sent it back: the reply said report.xlsx was saved, and it is not in the folder."
    )
    # The follow-up really writes it: nothing more is sent, no verdict.
    (work / "report.xlsx").write_bytes(b"x")
    reply(svc, row.id, "Now I created report.xlsx in the folder.")
    await settle(svc, row.id)
    assert len(drivers[row.id].sent) == 2
    assert not any(n.startswith("Checked by Iron Jarvis") for n in notices(svc, row.id))
    assert [k for k, _ in nudges] == ["checked_again"]
    assert nudges[0][1]["surface"] == "build_app"
    await stop_all(app)


async def test_a_file_that_is_in_the_folder_is_never_sent_back(app, work, monkeypatch, nudges):
    svc, row, drivers = await started(app, monkeypatch, work)
    (work / "out").mkdir()
    (work / "out" / "Report.xlsx").write_bytes(b"x")
    reply(svc, row.id, "Done! I saved report.xlsx with the totals.")
    await settle(svc, row.id)
    assert len(drivers[row.id].sent) == 1 and nudges == []
    await stop_all(app)


async def test_a_name_one_of_its_steps_mentions_is_left_alone(app, work, monkeypatch, nudges):
    svc, row, drivers = await started(app, monkeypatch, work)
    live = svc.live[row.id]
    svc._emit(live, make("tool", item="s2", kind="command", name="Bash", title="Copy it",
                         input="cp out.xlsx ../elsewhere/report.xlsx", status="done"))
    reply(svc, row.id, "Done! I saved report.xlsx.", step=False)
    await settle(svc, row.id)
    assert len(drivers[row.id].sent) == 1
    await stop_all(app)


async def test_a_stopped_reply_is_never_sent_back(app, work, monkeypatch, nudges):
    svc, row, drivers = await started(app, monkeypatch, work)
    reply(svc, row.id, "Done! I saved report.xlsx.", stop="interrupted")
    await settle(svc, row.id)
    assert len(drivers[row.id].sent) == 1 and nudges == []
    await stop_all(app)


async def test_a_failed_reply_is_never_sent_back(app, work, monkeypatch, nudges):
    svc, row, drivers = await started(app, monkeypatch, work)
    reply(svc, row.id, "Done! I saved report.xlsx.", stop="failed")
    await settle(svc, row.id)
    assert len(drivers[row.id].sent) == 1
    await stop_all(app)


async def test_a_waiting_message_goes_first_and_nothing_is_sent_back(app, work, monkeypatch, nudges):
    svc, row, drivers = await started(app, monkeypatch, work)
    await svc.queue_add(row.id, "then add a chart")
    reply(svc, row.id, "Done! I saved report.xlsx.")
    second = await next_send(drivers, row.id, 2)
    assert second == "then add a chart"
    await asyncio.sleep(0.3)
    assert len(drivers[row.id].sent) == 2 and nudges == []
    reply(svc, row.id, "Added the chart.")
    await stop_all(app)


async def test_the_same_problem_twice_stops_with_the_verdict(app, work, monkeypatch, nudges):
    svc, row, drivers = await started(app, monkeypatch, work)
    reply(svc, row.id, "Done! I saved report.xlsx.")
    await next_send(drivers, row.id, 2)
    reply(svc, row.id, "Sorry about that, I saved report.xlsx now.")
    await until(lambda: any(n.startswith("Checked by Iron Jarvis") for n in notices(svc, row.id)))
    await asyncio.sleep(0.2)
    assert len(drivers[row.id].sent) == 2
    verdict = [n for n in notices(svc, row.id) if n.startswith("Checked by Iron Jarvis")]
    assert verdict == [
        "Checked by Iron Jarvis: this is not finished. It was sent back once and stopped there: "
        "the last try changed nothing. Tell it to keep going and it will carry on."
    ]
    await stop_all(app)


async def test_at_most_two_follow_ups_then_the_verdict(app, work, monkeypatch, nudges):
    svc, row, drivers = await started(app, monkeypatch, work)
    reply(svc, row.id, "Done! I saved a.xlsx.")
    await next_send(drivers, row.id, 2)
    reply(svc, row.id, "Done! I saved b.xlsx.")
    third = await next_send(drivers, row.id, 3)
    assert "(Check 2 of at most 2.)" in third and "`b.xlsx`" in third
    reply(svc, row.id, "Done! I saved c.xlsx.")
    await until(lambda: any(n.startswith("Checked by Iron Jarvis") for n in notices(svc, row.id)))
    await asyncio.sleep(0.2)
    assert len(drivers[row.id].sent) == 3
    assert any("sent back twice" in n and "the most tries (2)" in n for n in notices(svc, row.id))
    assert [k for k, _ in nudges] == ["checked_again", "checked_again"]
    await stop_all(app)


async def test_a_blocked_line_ends_it_without_a_verdict(app, work, monkeypatch, nudges):
    svc, row, drivers = await started(app, monkeypatch, work)
    reply(svc, row.id, "Done! I saved report.xlsx.")
    await next_send(drivers, row.id, 2)
    reply(svc, row.id, "I saved report.xlsx earlier, I thought.\n\nBlocked: the disk is read-only; "
                       "the user must free space.")
    await settle(svc, row.id, 0.4)
    assert len(drivers[row.id].sent) == 2
    assert not any(n.startswith("Checked by Iron Jarvis") for n in notices(svc, row.id))
    await stop_all(app)


async def test_a_new_user_message_starts_its_own_count(app, work, monkeypatch, nudges):
    svc, row, drivers = await started(app, monkeypatch, work)
    reply(svc, row.id, "Done! I saved a.xlsx.")
    await next_send(drivers, row.id, 2)
    reply(svc, row.id, "Here is the answer, nothing saved.")
    await settle(svc, row.id)
    await svc.start_turn(row.id, "now the second one", "t2")
    await next_send(drivers, row.id, 3)
    reply(svc, row.id, "Done! I saved b.xlsx.")
    fourth = await next_send(drivers, row.id, 4)
    assert fourth.startswith(CA.NOTE_HEAD) and "(Check" not in fourth
    reply(svc, row.id, "No file this time.")
    await stop_all(app)


async def test_a_share_spelling_is_never_looked_up(app, work, monkeypatch, nudges):
    touched: list[str] = []
    real_scandir, real_stat = os.scandir, os.stat

    def guard(path, *a, **k):
        if str(path).replace("/", "\\").startswith("\\\\"):
            touched.append(str(path))
            raise AssertionError("a share was looked up")
        return None

    def scandir(path=".", *a, **k):
        guard(path)
        return real_scandir(path, *a, **k)

    def stat(path, *a, **k):
        guard(path)
        return real_stat(path, *a, **k)

    monkeypatch.setattr(os, "scandir", scandir)
    monkeypatch.setattr(os, "stat", stat)
    svc, row, drivers = await started(app, monkeypatch, work)
    reply(svc, row.id, "Done! I saved \\\\host\\share\\x.xlsx and //host/share/y.xlsx for you.")
    await settle(svc, row.id)
    assert len(drivers[row.id].sent) == 1 and touched == []
    await stop_all(app)


async def test_a_folder_on_a_share_is_never_walked(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("walked")

    monkeypatch.setattr(os, "scandir", boom)
    assert CA.missing_files(["x.xlsx"], "\\\\host\\share\\proj", []) == []
    assert CA.missing_files(["x.xlsx"], "//host/share/proj", []) == []


async def test_a_name_the_reply_puts_elsewhere_is_left_alone(tmp_path):
    folder = str(tmp_path)
    for text in ("I saved C:\\Users\\Someone\\Desktop\\x.xlsx", "I saved ~/Desktop/x.xlsx",
                 "I saved \\\\host\\share\\x.xlsx"):
        assert CA.missing_files(["x.xlsx"], folder, [], text) == [], text
    assert CA.missing_files(["x.xlsx"], folder, [], "I saved x.xlsx") == ["x.xlsx"]


def test_a_walk_that_cannot_finish_says_nothing(tmp_path):
    for i in range(30):
        (tmp_path / f"f{i}.txt").write_bytes(b"")
    assert CA.files_by_name(str(tmp_path), max_entries=10) is None
    assert CA.missing_files(["x.xlsx"], str(tmp_path), []) == ["x.xlsx"]
    import iron_jarvis.hosted.check_again as M

    real = M.files_by_name
    try:
        M.files_by_name = lambda folder, **k: None
        assert CA.missing_files(["x.xlsx"], str(tmp_path), []) == []
    finally:
        M.files_by_name = real


def test_the_walk_follows_no_link(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.xlsx").write_bytes(b"")
    inner = tmp_path / "work"
    inner.mkdir()
    try:
        os.symlink(outside, inner / "link", target_is_directory=True)
    except OSError:
        pytest.skip("this PC cannot make a link")
    assert "x.xlsx" not in (CA.files_by_name(str(inner)) or set())


# --------------------------------------------------------------------------- #
# Giving up
# --------------------------------------------------------------------------- #
GIVE_UP = "I tried several things but I cannot do this. You will need to do this yourself."


async def test_a_give_up_after_work_is_sent_back_once(app, work, monkeypatch, nudges):
    svc, row, drivers = await started(app, monkeypatch, work)
    reply(svc, row.id, GIVE_UP)
    note = await next_send(drivers, row.id, 2)
    assert CA.GIVE_UP_WORDS in note
    assert users(svc, row.id)[-1]["carry"]["note"].startswith(
        "Iron Jarvis sent it back: the reply ended by saying it could not be done")
    reply(svc, row.id, GIVE_UP)
    await settle(svc, row.id)
    assert len(drivers[row.id].sent) == 2
    assert [k for k, _ in nudges] == ["gave_up"]
    await stop_all(app)


async def test_a_give_up_with_no_work_is_left_alone(app, work, monkeypatch, nudges):
    svc, row, drivers = await started(app, monkeypatch, work)
    reply(svc, row.id, GIVE_UP, step=False)
    await settle(svc, row.id)
    assert len(drivers[row.id].sent) == 1
    await stop_all(app)


# --------------------------------------------------------------------------- #
# What it never does
# --------------------------------------------------------------------------- #
async def test_never_while_emergency_stop_is_on(app, work, monkeypatch, nudges):
    svc, row, drivers = await started(app, monkeypatch, work)
    app.state.platform.config.autonomy_kill_switch = True
    reply(svc, row.id, "Done! I saved report.xlsx.")
    await settle(svc, row.id)
    assert len(drivers[row.id].sent) == 1
    await stop_all(app)


async def test_another_carry_on_is_never_judged(app, work, monkeypatch, nudges):
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    row = store.create(app.state.platform.engine, harness="claude-code", folder=str(work))
    await svc.start_turn(row.id, CO.CONTINUE_AFTER_RESTART, "t1", carry={"why": "restart", "from": "t0"})
    await until(lambda: row.id in drivers and drivers[row.id].sent)
    reply(svc, row.id, "Done! I saved report.xlsx.")
    await settle(svc, row.id)
    assert len(drivers[row.id].sent) == 1
    await stop_all(app)


async def test_an_open_card_or_a_limit_card_leaves_it_alone(app, work, monkeypatch, nudges):
    svc, row, drivers = await started(app, monkeypatch, work)
    live = svc.live[row.id]
    svc._emit(live, make("error", title="limit", fix="", technical="", fix_kind="account_limit"))
    reply(svc, row.id, "Done! I saved report.xlsx.")
    await settle(svc, row.id)
    assert len(drivers[row.id].sent) == 1
    await stop_all(app)


async def test_an_archived_session_is_never_sent_back(app, work, monkeypatch, nudges):
    svc, row, drivers = await started(app, monkeypatch, work)
    store.update(app.state.platform.engine, row.id, archived_at=S.utcnow())
    reply(svc, row.id, "Done! I saved report.xlsx.")
    await settle(svc, row.id)
    assert len(drivers[row.id].sent) == 1
    await stop_all(app)


async def test_retry_of_a_failed_follow_up_sends_its_own_words_again(app, work, monkeypatch, nudges):
    svc, row, drivers = await started(app, monkeypatch, work)
    reply(svc, row.id, "Done! I saved report.xlsx.")
    note = await next_send(drivers, row.id, 2)
    live = svc.live[row.id]
    svc._emit(live, make("error", title="It broke", fix="", technical=""))
    svc._emit(live, make("done", stop="failed"))
    await until(lambda: svc.live[row.id].turn is None)
    await asyncio.to_thread(live.log.flush)
    got = await CO.continue_cut(svc, row.id)
    assert got["ok"]
    again = await next_send(drivers, row.id, 3)
    assert again == note
    carry = users(svc, row.id)[-1]["carry"]
    assert carry["why"] == "check" and carry["pressed"] and carry["retry"]
    reply(svc, row.id, "Nothing more.")
    await stop_all(app)


def test_the_words_are_masked_and_cut():
    long = "a" * 200 + ".xlsx"
    assert len(CA.shown(long)) <= CA.NAME_CHARS + 1
    line = CA.line_for([long], False)
    assert "a" * 120 not in line


async def test_an_open_card_leaves_it_alone(app, work, monkeypatch, nudges):
    svc, row, drivers = await started(app, monkeypatch, work)
    live = svc.live[row.id]
    live.asks["r1"] = object()
    reply(svc, row.id, "Done! I saved report.xlsx.")
    await settle(svc, row.id)
    assert len(drivers[row.id].sent) == 1
    live.asks.clear()
    await stop_all(app)


async def test_a_helpers_piece_is_never_judged(app, work, monkeypatch, nudges):
    svc, row, drivers = await started(app, monkeypatch, work)
    svc.handoff_turns["child"] = "t1"
    reply(svc, row.id, "Done! I saved report.xlsx.")
    await settle(svc, row.id)
    assert len(drivers[row.id].sent) == 1
    svc.handoff_turns.clear()
    await stop_all(app)
