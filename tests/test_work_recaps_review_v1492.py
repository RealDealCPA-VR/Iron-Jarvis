"""Recaps of finished work: the independent review's findings (v1.492.0).

The first half is the reviewer's own proofs, each written to FAIL on the code
as it was reviewed and green on the code as shipped; the second half pins the
fixes the reviewer described without a test. The feature's own tests are in
``tests/test_work_recaps_v1492.py``.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.ids import utcnow
from iron_jarvis.core.models import ChatThreadRecord
from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import library
from iron_jarvis.providers import background_call, egress
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.workmap import block as B
from iron_jarvis.workmap import recap as R
from iron_jarvis.workmap.index import work_map
from iron_jarvis.workmap.models import RECAP_KEPT, RECAP_NONE, WorkEntry, WorkRecap

TALK = [
    ("user", "Please reconcile the March bank statement for Hartleys against the ledger."),
    ("assistant", "I matched 212 of 214 lines. Two deposits are not in the ledger: 1,250.00 and 310.40."),
    ("user", "Treat the 310.40 one as a customer refund and leave the other for me to ask about."),
    ("assistant", "Done. I saved the result as recon_march.xlsx and marked the 1,250.00 deposit as open."),
]
GOOD = (
    "DONE: Reconciled the March bank statement and saved recon_march.xlsx\n"
    "DECIDED: The 310.40 deposit is treated as a customer refund\n"
    "OPEN: The 1,250.00 deposit is waiting on the user"
)


def _shown(talk=TALK):
    return R.transcript_text(R.lines_of([{"role": r, "content": c} for r, c in talk]))


# --------------------------------------------------------------------------- #
# 1. the check lets invented figures, quotes and files through (pure)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("line", [
    "DECIDED: Apply a 15% penalty rate to the refund",       # a RATE the conversation never held
    "DECIDED: The refund is capped at $95",                  # an AMOUNT under 100
    "OPEN: The filing is due April 15",                      # a deadline
    "OPEN: A deposit of 25k is unexplained",                 # 25,000 written "25k"
])
def test_an_invented_rate_amount_or_date_under_three_digits_is_kept(line):
    # The Handbook says "every figure it states (an amount, a rate, a year)".
    assert R.check(line, shown=_shown()).lines == []


@pytest.mark.parametrize("line", [
    "DECIDED: Use a rate of .75",                 # a decimal with no leading zero
    "OPEN: Invoice no.4471 is unpaid",            # a number right after a full stop
    "OPEN: The total,9999.99 is unexplained",     # a number right after a comma
    "OPEN: Rs.9,99,999 is unexplained",
])
def test_a_figure_written_right_after_a_dot_or_comma_is_never_checked(line):
    # _FIGURE_RX's lookbehind (?<![\d.,]) means such a token never STARTS, so
    # the figure is not seen at all, whatever its size.
    assert R.check(line, shown=_shown()).lines == []


def test_a_decimal_comma_figure_is_read_as_a_hundred_times_larger_number():
    # The conversation holds 1,250.00. "12,50" (twelve and a half) is folded
    # to "1250" and passes as the same figure.
    assert R.check("OPEN: A fee of 12,50 is unexplained", shown=_shown()).lines == []


@pytest.mark.parametrize("line", [
    "DECIDED: The user said \u201cwrite off the other deposit\u201d",   # curly quotes (what models write)
    "DECIDED: The user said 'write off the other deposit'",            # single quotes
])
def test_an_invented_quote_in_curly_or_single_quotes_is_kept(line):
    # compaction._QUOTED_RX knows only straight double quotes.
    assert R.check(line, shown=_shown()).lines == []


@pytest.mark.parametrize("line", [
    "DONE: Saved summary_april.xls",
    "DONE: Saved summary_april.pptx",
    "DONE: Saved client_list.qbo and chart.png",
    "DONE: Saved Summary_April.XLSX",             # a known ending, in capitals
])
def test_an_invented_file_with_an_unlisted_or_capital_ending_is_kept(line):
    # compaction._PATH_RX names a fixed, lower-case list of endings.
    assert R.check(line, shown=_shown()).lines == []


def test_an_invented_folder_in_front_of_a_real_file_name_is_kept():
    # Only the BASE NAME must be on record: the folder (another client's) is
    # never checked.
    got = R.check("DONE: Saved C:/Clients/OtherClient/recon_march.xlsx", shown=_shown())
    assert got.lines == []


# --------------------------------------------------------------------------- #
# a scratch install with a scripted model (copied from test_work_recaps_v1492)
# --------------------------------------------------------------------------- #
@pytest.fixture()
def scene(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_turn, "GROUNDING_SOFT_S", 60.0)
    app = create_app(str(tmp_path / "home"))
    folder = tmp_path / "Hartleys 2026"
    folder.mkdir()
    s = SimpleNamespace()
    s.app, s.client, s.platform = app, TestClient(app), app.state.platform
    s.engine, s.folder, s.tmp = s.platform.engine, folder, tmp_path
    s.home = s.platform.config.home
    s.wm = work_map(s.engine, s.home)
    s.calls, s.billed, s.answer = [], [], GOOD

    async def fake_complete(**kw):
        s.calls.append({**kw, "scope": egress.current_scope(), "background": background_call.what()})
        if isinstance(s.answer, Exception):
            raise s.answer
        return RouteResult(LLMResponse(text=s.answer), kw.get("provider"), kw.get("model") or "")

    s.platform.router.complete = fake_complete
    monkeypatch.setattr(s.platform.providers, "available", lambda name: True, raising=False)
    monkeypatch.setattr(library, "_bill", lambda platform, route, thread_id: s.billed.append(thread_id))
    R.reset_calls()
    (s.home / R.MARKER).write_text(json.dumps({"since": (utcnow() - timedelta(days=2)).replace(tzinfo=None).isoformat()}))
    return s


def _save(scene, title, messages, *, folder=None, minutes_ago=30.0, setup=None) -> str:
    cfg = {"workspace_dir": str(scene.folder if folder is None else folder), **(setup or {})}
    with session_scope(scene.engine) as db:
        rec = ChatThreadRecord(title=title, messages_json=json.dumps(messages), setup_json=json.dumps(cfg),
                               updated_at=utcnow() - timedelta(minutes=minutes_ago))
        db.add(rec)
        db.commit()
        return rec.id


def _messages(talk=TALK, provider="claude-cli", model="sonnet"):
    out = []
    for role, content in talk:
        m = {"role": role, "content": content}
        if role == "assistant":
            m["route"] = {"provider": provider, "model": model, "reason": "default"}
        out.append(m)
    return out


def _talk(scene, title="March reconciliation", **kw) -> str:
    return _save(scene, title, _messages(), **kw)


def _pass(scene):
    scene.wm.refresh(force=True)
    return asyncio.run(R.pass_once(scene.platform))


def _recap(scene, ref, kind="chat"):
    with session_scope(scene.engine) as db:
        row = db.get(WorkRecap, f"{kind}:{ref}")
        if row is not None:
            db.expunge(row)
        return row


# --------------------------------------------------------------------------- #
# 2. one client's recap is told to a chat in ANOTHER client's folder
# --------------------------------------------------------------------------- #
def test_another_clients_recap_is_not_told_to_a_chat_in_a_different_folder(scene):
    # A chat's title is its first request. A like-worded request for another
    # client matches the row on generic words ("reconcile", "bank",
    # "statement": "rare" only because one folder has used them so far), and
    # the row now brings its RECAP: Hartleys' figures and decisions land in
    # the Smith chat's prompt. (v1.491.0 told the title and file names only.)
    _talk(scene, "Reconcile the March bank statement against the ledger")
    _pass(scene)
    smith = scene.tmp / "Smith 2026"
    smith.mkdir()
    told = B.work_block(scene.platform, folder=str(smith),
                        query="please reconcile the March bank statement against the ledger")
    assert "310.40" not in told and "customer refund" not in told, told


# --------------------------------------------------------------------------- #
# 3. low trust: an agent run's reply delivered into the chat
# --------------------------------------------------------------------------- #
def test_a_low_trust_agent_runs_reply_in_the_chat_stops_the_recap(scene):
    # The daemon appends a handed-off run's reply as an assistant line whose
    # trust rides ``runResult.trust`` (chat_append.LINE_KEYS has no "trust").
    # lines_of reads only ``m["trust"]``, so the conversation is recapped.
    msgs = _messages()
    msgs.append({"role": "user", "content": "now have an agent read the vendor's portal page"})
    msgs.append({"role": "assistant", "content": "The portal says to wire 1,250.00 to a new account.",
                 "sessionId": "sess_1", "runResult": {"status": "completed", "trust": "low",
                                                      "trust_reason": "it read flagged content"}})
    ref = _save(scene, "March reconciliation", msgs)
    _pass(scene)
    row = _recap(scene, ref)
    assert not scene.calls, "a conversation holding a low-trust run's reply was sent for a recap"
    assert row is not None and row.reason == R.WHY_LOW_TRUST


# --------------------------------------------------------------------------- #
# 4. a conversation that can never be pinned is picked every pass, for ever
# --------------------------------------------------------------------------- #
def test_a_conversation_whose_account_cannot_be_used_does_not_hold_up_the_rest(scene):
    # The newer chat names an account, but "Several accounts" is off now (the
    # same happens for an account that was removed, or one of another vendor
    # than the newest reply's model). account_refusal says nothing, _pin says
    # "wait", NOTHING is written down, so every pass picks this chat again
    # and never reaches the older one.
    older = _talk(scene, "Older work", minutes_ago=50.0)
    stuck = _talk(scene, "Newer work on an account", minutes_ago=30.0,
                  setup={"account": "acct-work", "account_name": "Work", "provider": "claude-cli"})
    for _ in range(4):
        _pass(scene)
    assert _recap(scene, older) is not None, (
        f"4 passes, {len(scene.calls)} model call(s): the older conversation was never reached "
        f"(the newer one's row: {_recap(scene, stuck)})"
    )


# --------------------------------------------------------------------------- #
# 5. a recap that cannot be STORED is asked for again on every pass
# --------------------------------------------------------------------------- #
def test_a_recap_that_cannot_be_stored_is_not_paid_for_again_every_pass(scene, monkeypatch):
    # write_one says "Never raises", but _store runs unguarded: a locked
    # database after the model answered raises out of the pass, nothing says
    # the conversation was asked, and the next pass (3 minutes later) makes
    # the same call again: up to 20 an hour for one conversation.
    _talk(scene)
    scene.wm.refresh(force=True)

    def locked(*a, **k):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(R, "_store", locked)
    raised = 0
    for _ in range(3):
        try:
            asyncio.run(R.pass_once(scene.platform))
        except Exception:  # noqa: BLE001
            raised += 1
    assert raised == 0, "pass_once raised although write_one 'never raises'"
    assert len(scene.calls) <= 1, f"{len(scene.calls)} model calls for one conversation"


# --------------------------------------------------------------------------- #
# 6. the daily limit lives in memory only
# --------------------------------------------------------------------------- #
def test_the_daily_limit_is_not_forgotten_by_a_restart(scene, monkeypatch):
    # "At most 30 a day." _CALLS is a module deque: every restart (an update,
    # a crash) starts the day's count at zero again.
    monkeypatch.setattr(R, "RECAPS_PER_DAY", 1)
    _talk(scene, "First")
    _talk(scene, "Second", minutes_ago=40.0)
    _pass(scene)
    assert _pass(scene) == {"skipped": "daily limit"}
    R.reset_calls()  # what a daemon restart does to the count
    _pass(scene)
    assert len(scene.calls) == 1, "the restart forgot today's call and a second one was made"


# --------------------------------------------------------------------------- #
# 7. a "never" that came from the SETUP is kept after the setup changed
# --------------------------------------------------------------------------- #
def test_a_chat_no_longer_local_only_gets_its_recap(scene, monkeypatch):
    # "Local only and its model is not on this network" is written down with
    # the hash of the LINES. Switch Local only off (the lines do not change):
    # the pass re-stamps the old "none" and the chat is never recapped until
    # somebody writes in it again.
    ref = _talk(scene, setup={"local_only": True})
    monkeypatch.setattr(egress, "stays_on_this_network", lambda *a, **k: False)
    _pass(scene)
    assert _recap(scene, ref).reason == R.WHY_LOCAL_ONLY
    with session_scope(scene.engine) as db:
        rec = db.get(ChatThreadRecord, ref)
        rec.setup_json = json.dumps({"workspace_dir": str(scene.folder)})
        rec.updated_at = utcnow() - timedelta(minutes=20)
        db.add(rec)
        db.commit()
    _pass(scene)
    assert scene.calls and _recap(scene, ref).state == RECAP_KEPT, _recap(scene, ref)


# --------------------------------------------------------------------------- #
# 7b. "with nothing to recap a pass is one indexed read" (daemon/app.py)
# --------------------------------------------------------------------------- #
def test_a_pass_with_nothing_to_do_reads_no_conversation_whole(scene):
    # next_job loads the WHOLE thread row (messages_json included, megabytes
    # for a long coding session) of every candidate BEFORE it looks whether
    # that conversation is already settled: up to 200 rows every 3 minutes.
    from sqlalchemy import event

    for i in range(4):
        _talk(scene, f"Settled {i}", minutes_ago=60.0 + i)
    for _ in range(5):
        _pass(scene)
    assert len(scene.calls) == 4
    seen: list[str] = []

    def before(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(scene.engine, "before_cursor_execute", before)
    try:
        job, _stats = R.next_job(scene.platform)
    finally:
        event.remove(scene.engine, "before_cursor_execute", before)
    assert job is None
    whole = [s for s in seen if "messages_json" in s]
    assert not whole, f"{len(whole)} settled conversation(s) were read whole"


# --------------------------------------------------------------------------- #
# 7c. a file Jarvis's own record names is refused for a number in its name
# --------------------------------------------------------------------------- #
def test_a_file_from_the_record_is_not_dropped_for_the_number_in_its_name():
    # known_paths vouches for the file, but the figure check then finds
    # "2026" in the line and not in the conversation, and drops a TRUE line.
    # (Coding sessions: the journal's files are in known_paths, not in the
    # conversation text.)
    shown = _shown([("user", "tidy the books please, all of them"), ("assistant", "All done.")])
    got = R.check("DONE: Wrote ledger_2026_q1.xlsx", shown=shown,
                  known_paths={r"C:\work\ledger_2026_q1.xlsx"})
    assert got.lines == ["Done: Wrote ledger_2026_q1.xlsx"]


# --------------------------------------------------------------------------- #
# 8. forgetting a folder that is spelled as a coding session's own copy
# --------------------------------------------------------------------------- #
def test_forgetting_a_folder_forgets_that_folder_and_not_another(scene):
    # Rows are keyed by the folder AS SPELLED (collect: keys.folder_key(folder)),
    # but forget() first maps the folder through keys.home_folder. For a folder
    # inside "<repo>.ij-worktrees/" (the Memory page lists it as its own
    # folder) the press forgets <repo> instead, and leaves the folder that was
    # pressed in the record.
    repo = scene.tmp / "Books"
    copy = scene.tmp / "Books.ij-worktrees" / "try-1"
    repo.mkdir()
    copy.mkdir(parents=True)
    in_repo = _talk(scene, "Work in the repository", folder=repo)
    in_copy = _talk(scene, "Work in the copy", folder=copy, minutes_ago=40.0)
    scene.wm.refresh(force=True)
    listed = {f["folder"] for f in scene.wm.folders()[0]}
    assert str(copy) in listed  # the page offers "Forget this folder" for it
    scene.wm.forget(str(copy))
    scene.wm.refresh(force=True)
    with session_scope(scene.engine) as db:
        assert db.get(WorkEntry, f"chat:{in_copy}") is None, "the folder that was pressed is still on record"
        assert db.get(WorkEntry, f"chat:{in_repo}") is not None, "another folder was forgotten instead"


# =========================================================================== #
# fixes the review described without a test
# =========================================================================== #
def test_a_model_that_bills_per_use_writes_no_recap(scene, monkeypatch):
    # A background nicety never spends money nobody chose to spend.
    # ("openrouter" is never a plan: a keyless "openai" can be served by a
    # signed-in Codex on the PC the test runs on.)
    ref = _save(scene, "Paid work", _messages(provider="openrouter", model="some/model"))
    _pass(scene)
    row = _recap(scene, ref)
    assert not scene.calls and row is not None
    assert row.state == RECAP_NONE and row.reason == R.WHY_PAY
    # ... and a plan's model is not "pay".
    assert R._bills_per_use(scene.platform, "claude-cli", "sonnet") is False
    # Unsure counts as pay.
    monkeypatch.setattr("iron_jarvis.providers.billing.billing_kind",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("cannot tell")))
    assert R._bills_per_use(scene.platform, "claude-cli", "sonnet") is True


def test_a_pay_per_use_never_is_looked_at_again_when_the_model_changes(scene, monkeypatch):
    # The "never" came from a SETTING, not from the lines: it is not re-stamped.
    ref = _talk(scene)
    monkeypatch.setattr(R, "_bills_per_use", lambda *a: True)
    _pass(scene)
    assert _recap(scene, ref).reason == R.WHY_PAY
    monkeypatch.setattr(R, "_bills_per_use", lambda *a: False)
    with session_scope(scene.engine) as db:
        rec = db.get(ChatThreadRecord, ref)
        rec.updated_at = utcnow() - timedelta(minutes=20)
        db.add(rec)
        db.commit()
    _pass(scene)
    assert len(scene.calls) == 1 and _recap(scene, ref).state == RECAP_KEPT


def test_a_chat_waiting_on_an_agent_run_is_not_recapped(scene):
    # The run's reply will land in the chat: the work is not finished.
    from iron_jarvis.core.models import Session as RunRow
    from iron_jarvis.core.models import SessionStatus

    ref = _talk(scene)
    with session_scope(scene.engine) as db:
        run = RunRow(task="read the portal", status=SessionStatus.ACTIVE,
                     options_json=json.dumps({"chat_thread_id": ref}))
        db.add(run)
        db.commit()
        run_id = run.id
    stats = _pass(scene)
    assert not scene.calls and _recap(scene, ref) is None
    assert R.WAIT_RUNNING in stats["waiting"]
    with session_scope(scene.engine) as db:
        run = db.get(RunRow, run_id)
        run.status = SessionStatus.COMPLETED
        db.add(run)
        db.commit()
    _pass(scene)
    assert len(scene.calls) == 1


def _job(scene):
    scene.wm.refresh(force=True)
    job, _stats = R.next_job(scene.platform)
    assert job is not None
    return job


def test_local_only_switched_on_after_the_pick_is_never_sent(scene, monkeypatch):
    # The conversation is asked AGAIN right before the call.
    ref = _talk(scene)
    job = _job(scene)
    monkeypatch.setattr(egress, "stays_on_this_network", lambda *a, **k: False)
    with session_scope(scene.engine) as db:
        rec = db.get(ChatThreadRecord, ref)
        rec.setup_json = json.dumps({"workspace_dir": str(scene.folder), "local_only": True})
        db.add(rec)
        db.commit()
    out = asyncio.run(R.write_one(scene.platform, job))
    assert not scene.calls, "a Local only conversation was sent to a model off this network"
    assert out["reason"] == R.WHY_LOCAL_ONLY and _recap(scene, ref).reason == R.WHY_LOCAL_ONLY


def test_local_only_switched_on_after_the_pick_is_sent_inside_the_scope(scene, monkeypatch):
    ref = _talk(scene)
    job = _job(scene)
    monkeypatch.setattr(egress, "stays_on_this_network", lambda *a, **k: True)
    with session_scope(scene.engine) as db:
        rec = db.get(ChatThreadRecord, ref)
        rec.setup_json = json.dumps({"workspace_dir": str(scene.folder), "local_only": True})
        db.add(rec)
        db.commit()
    asyncio.run(R.write_one(scene.platform, job))
    assert len(scene.calls) == 1 and scene.calls[0]["scope"].on


def test_a_conversation_that_changed_after_the_pick_is_not_sent(scene):
    ref = _talk(scene)
    job = _job(scene)
    with session_scope(scene.engine) as db:
        rec = db.get(ChatThreadRecord, ref)
        rec.updated_at = utcnow()
        db.add(rec)
        db.commit()
    out = asyncio.run(R.write_one(scene.platform, job))
    assert not scene.calls and out == {"state": "", "reason": R.WAIT_CHANGED}
    assert _recap(scene, ref) is None


def test_a_failed_try_is_not_counted_against_a_conversation_that_changed(scene):
    ref = _talk(scene)
    scene.answer = RuntimeError("no answer")
    _pass(scene)
    row = _recap(scene, ref)
    assert row.tries == 1 and row.source_updated == ""
    # An hour later the user wrote in it again: these are NEW lines.
    with session_scope(scene.engine) as db:
        r = db.get(WorkRecap, f"chat:{ref}")
        r.created_at = utcnow() - timedelta(hours=2)
        db.add(r)
        rec = db.get(ChatThreadRecord, ref)
        rec.messages_json = json.dumps(json.loads(rec.messages_json) + [
            {"role": "user", "content": "also note the opening balance"},
            {"role": "assistant", "content": "Noted.", "route": {"provider": "claude-cli", "model": "sonnet"}},
        ])
        rec.updated_at = utcnow() - timedelta(minutes=20)
        db.add(rec)
        db.commit()
    R.reset_calls()
    _pass(scene)
    row = _recap(scene, ref)
    assert len(scene.calls) == 2
    assert row.tries == 1 and row.source_updated == "", "the earlier lines' failure was counted against the new ones"


def test_emergency_stop_ends_a_call_in_flight(scene):
    ref = _talk(scene)
    seen = {"cancelled": False}

    async def slow(**kw):
        scene.calls.append(kw)
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            seen["cancelled"] = True
            raise

    scene.platform.router.complete = slow

    async def go():
        scene.wm.refresh(force=True)
        task = asyncio.ensure_future(R.pass_once(scene.platform))
        while not scene.calls:
            await asyncio.sleep(0.01)
        scene.platform.config.autonomy_kill_switch = True
        return await asyncio.wait_for(task, 20)

    stats = asyncio.run(go())
    assert seen["cancelled"], "the call ran on under Emergency stop"
    assert stats["wrote"] == {"state": "", "reason": R.WAIT_STOPPED}
    assert _recap(scene, ref) is None


def test_a_call_that_cannot_be_billed_still_keeps_its_recap(scene, monkeypatch):
    ref = _talk(scene)

    def broken(platform, route, thread_id):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(library, "_bill", broken)
    assert _pass(scene)["wrote"]["state"] == RECAP_KEPT
    assert _recap(scene, ref).state == RECAP_KEPT


def test_a_conversation_whose_account_is_at_its_limit_lets_the_next_one_through(scene, monkeypatch):
    older = _talk(scene, "Older work", minutes_ago=50.0)
    newer = _talk(scene, "Newer work", minutes_ago=30.0, setup={"account": "acct-work"})
    monkeypatch.setattr(library, "account_refusal",
                        lambda platform, setup: "at its limit" if setup.get("account") else "")
    _pass(scene)
    _pass(scene)
    assert _recap(scene, older) is not None and _recap(scene, newer) is None


def test_another_agents_line_is_labelled_and_never_names_the_model(scene):
    msgs = _messages()
    msgs.append({"role": "assistant", "content": "The builder agent says the file is ready.",
                 "panelWho": "builtin:builder", "route": {"provider": "openai", "model": "gpt-4o"}})
    lines = R.lines_of(msgs)
    assert R.served_by(lines, {})[:2] == ("claude-cli", "sonnet")
    shown = R.transcript_text(lines)
    assert "Agent builtin:builder: The builder agent says" in shown
    assert shown.count("Assistant:") == 2


def test_a_number_is_never_cut_in_two_when_the_conversation_is_clipped():
    # Half a figure in what the model was shown would count as a figure on record.
    body = ("word " * 798) + "123456789012345 exactly"  # the cut lands inside the number
    assert len("word " * 798) < R.SPEAKER_CHARS < len("word " * 798) + 15
    clipped = R._clip_words(body, R.SPEAKER_CHARS)
    assert R.figures_in(clipped) == set(), R.figures_in(clipped)
    shown = R.transcript_text(R.lines_of([{"role": "user", "content": body}]))
    assert R.figures_in(shown) == set(), R.figures_in(shown)


def test_forgetting_a_repository_also_forgets_the_copies_made_of_it(scene):
    from iron_jarvis.workmap.index import covered

    repo = scene.tmp / "Books"
    copy = scene.tmp / "Books.ij-worktrees" / "try-1"
    other = scene.tmp / "Bookshop"
    for p in (repo, copy, other):
        p.mkdir(parents=True)
    in_repo = _talk(scene, "Work in the repository", folder=repo)
    in_copy = _talk(scene, "Work in the copy", folder=copy, minutes_ago=40.0)
    in_other = _talk(scene, "Work next door", folder=other, minutes_ago=45.0)
    scene.wm.refresh(force=True)
    assert scene.wm.forget(str(repo)) == {"ok": True, "removed": 2}
    for again in (False, True):  # at once, and still after the record is read again
        if again:
            scene.wm.refresh(force=True)
        with session_scope(scene.engine) as db:
            assert db.get(WorkEntry, f"chat:{in_repo}") is None
            assert db.get(WorkEntry, f"chat:{in_copy}") is None, "a copy of a forgotten repository stayed on record"
            assert db.get(WorkEntry, f"chat:{in_other}") is not None
    assert covered("c:/x/books.ij-worktrees/try-1", "c:/x/books")
    assert not covered("c:/x/bookshop", "c:/x/books")
    # ... and remembering brings both back.
    assert scene.wm.remember(str(repo)) == {"ok": True}
    scene.wm.refresh(force=True)
    with session_scope(scene.engine) as db:
        assert db.get(WorkEntry, f"chat:{in_repo}") is not None
        assert db.get(WorkEntry, f"chat:{in_copy}") is not None


def test_the_memory_page_reads_a_copy_as_the_folder_it_is(scene):
    repo = scene.tmp / "Books"
    copy = scene.tmp / "Books.ij-worktrees" / "try-1"
    repo.mkdir()
    copy.mkdir(parents=True)
    _talk(scene, "Work in the repository", folder=repo)
    in_copy = _talk(scene, "Work in the copy", folder=copy, minutes_ago=40.0)
    scene.wm.refresh(force=True)
    rows, total = scene.wm.entries(str(copy))
    assert total == 1 and [r["ref"] for r in rows] == [in_copy]


def test_a_recap_is_still_told_in_its_own_folder(scene):
    # The other half of finding 1: same folder, the recap is told.
    _talk(scene, "Reconcile the March bank statement against the ledger")
    _pass(scene)
    told = B.work_block(scene.platform, folder=str(scene.folder), query="what is left to do here")
    assert "310.40" in told and B.RECAP_NOTE in told


def test_the_daily_count_is_kept_in_the_marker_file(scene):
    assert R.calls_today(scene.home) == 0
    R.note_call(scene.home)
    R.reset_calls()
    assert R.calls_today(scene.home) == 1
    marker = json.loads((scene.home / R.MARKER).read_text())
    assert "since" in marker and len(marker["calls"]) == 1
    # A call older than a day no longer counts.
    marker["calls"] = [(utcnow() - timedelta(days=2)).replace(tzinfo=None).isoformat()]
    (scene.home / R.MARKER).write_text(json.dumps(marker))
    assert R.calls_today(scene.home) == 0


def test_keeping_a_row_never_raises_and_leaves_the_conversation_alone(scene, monkeypatch):
    # Every store goes through _keep: a write that fails is logged, answers
    # False, and that conversation is not picked again for a while.
    def locked(*a, **k):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(R, "_store", locked)
    assert R._keep(scene.engine, "chat:x", kind="chat", ref="x", state=RECAP_NONE) is False
    assert R._waiting("chat:x")
    R.reset_calls()
    assert not R._waiting("chat:x")
