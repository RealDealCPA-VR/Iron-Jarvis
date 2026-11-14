"""Recaps of finished work: the SECOND independent review (v1.492.0).

The reviewer's own proofs against the first round of fixes (a chat in a
parent folder, facts in other spellings, the marker file, forget), then the
pins for what it described without a test. The feature's own tests are in
``tests/test_work_recaps_v1492.py``; the first review's are in
``tests/test_work_recaps_review_v1492.py``.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import textwrap
from datetime import timedelta
from types import SimpleNamespace

from pathlib import Path
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
from iron_jarvis.workmap import keys
from iron_jarvis.workmap import keys as K
from iron_jarvis.workmap import recap as R
from iron_jarvis.workmap.index import covered, work_map
from iron_jarvis.workmap.models import RECAP_KEPT, WorkEntry, WorkForgotten, WorkRecap

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
# 2. recaps across folders: a chat in a PARENT folder
# --------------------------------------------------------------------------- #
def test_a_chat_in_the_parent_folder_is_not_told_every_clients_recap(scene):
    clients = scene.tmp / "Clients"
    hart = clients / "Hartleys"
    smith = clients / "Smith"
    for p in (hart, smith):
        p.mkdir(parents=True)
    _talk(scene, "Hartleys March reconciliation", folder=hart)
    _pass(scene)
    assert len(scene.calls) == 1
    # A chat bound to the folder ABOVE the clients (Documents, "Clients", a home folder).
    told = B.work_block(scene.platform, folder=str(clients), query="what is the weather like")
    assert "310.40" not in told and "customer refund" not in told, told


def test_a_chat_in_a_sibling_folder_is_not_told(scene):
    clients = scene.tmp / "Clients"
    hart = clients / "Hartleys"
    smith = clients / "Smith"
    for p in (hart, smith):
        p.mkdir(parents=True)
    _talk(scene, "Hartleys March reconciliation", folder=hart)
    _pass(scene)
    told = B.work_block(scene.platform, folder=str(smith), query="Hartleys March reconciliation please")
    assert "310.40" not in told and "customer refund" not in told, told


def test_a_chat_in_a_subfolder_is_not_told_the_parents_recap(scene):
    hart = scene.tmp / "Clients" / "Hartleys"
    sub = hart / "2026" / "payroll"
    sub.mkdir(parents=True)
    _talk(scene, "Hartleys March reconciliation", folder=hart)
    _pass(scene)
    told = B.work_block(scene.platform, folder=str(sub), query="Hartleys March reconciliation please")
    assert "310.40" not in told, told


# --------------------------------------------------------------------------- #
# 5. starvation by WAIT_RECENT, and "never raises"
# --------------------------------------------------------------------------- #
def test_conversations_asked_a_short_while_ago_do_not_starve_the_ones_behind(scene):
    # EXAMINE_MAX conversations whose call failed once (asked, not settled) sit
    # in WAIT_RECENT for an hour. They are counted as "examined" every pass and
    # never put in _WAITING, so the pass never reaches a healthy older one.
    scene.answer = RuntimeError("no answer")
    refs = [_talk(scene, f"Failing {i}", minutes_ago=20.0 + i) for i in range(R.EXAMINE_MAX)]
    for _ in refs:
        _pass(scene)
    assert all(_recap(scene, r) is not None and _recap(scene, r).tries == 1 for r in refs)
    older = _talk(scene, "Healthy older work", minutes_ago=200.0)
    scene.answer = GOOD
    n = len(scene.calls)
    for _ in range(3):
        _pass(scene)
    assert len(scene.calls) > n and _recap(scene, older) is not None, (
        "an older conversation was never reached while 12 newer ones waited out their hour"
    )


def test_one_conversation_that_cannot_be_read_does_not_stop_every_pass(scene, monkeypatch):
    bad = _talk(scene, "Unreadable", minutes_ago=20.0)
    older = _talk(scene, "Older work", minutes_ago=50.0)
    real = R.lines_of

    def lines_of(messages):
        got = real(messages)
        if got and "Unreadable" in json.dumps(scene_titles.get("probe", "")):
            pass
        return got

    scene_titles = {}
    # A row whose reading raises (deeply nested JSON -> RecursionError is not a ValueError).
    with session_scope(scene.engine) as db:
        rec = db.get(ChatThreadRecord, bad)
        rec.messages_json = "[" * 100000 + "]" * 100000
        db.add(rec)
        db.commit()
    raised = 0
    for _ in range(3):
        try:
            _pass(scene)
        except Exception:  # noqa: BLE001
            raised += 1
    assert raised == 0, "a conversation with unreadable messages raised out of the pass"
    assert _recap(scene, older) is not None, "the conversation behind it was never reached"


# --------------------------------------------------------------------------- #
# 3. the marker file
# --------------------------------------------------------------------------- #
def test_a_corrupt_marker_loses_todays_count_after_a_restart(scene):
    for _ in range(5):
        R.note_call(scene.home)
    (scene.home / R.MARKER).write_text("{not json")
    R.reset_calls()  # a restart
    assert R.calls_today(scene.home) == 0  # documents the behaviour (not a failure)


def test_the_clock_moving_back_does_not_forget_todays_calls(scene):
    now = utcnow().replace(tzinfo=None)
    for _ in range(R.RECAPS_PER_DAY):
        R.note_call(scene.home, now=now)
    R.reset_calls()  # a restart
    earlier = now - timedelta(hours=1)  # the clock was set back an hour
    assert R.calls_today(scene.home, now=earlier) >= R.RECAPS_PER_DAY


# --------------------------------------------------------------------------- #
# 6. forget / remember
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("key,forgotten,want", [
    ("c:/x/books", "c:/x/books", True),
    ("c:/x/books/sub", "c:/x/books", True),
    ("c:/x/bookshop", "c:/x/books", False),
    ("c:/x/books.ij-worktrees/try-1", "c:/x/books", True),
    ("c:/x/books.ij-worktrees-other/try-1", "c:/x/books", False),
    ("c:/x/books.ij-worktrees", "c:/x/books", False),
    ("c:/x/books2.ij-worktrees/try-1", "c:/x/books", False),
    ("c:/x/books_2", "c:/x/books", False),
    ("c:/x", "c:/x/books", False),
    ("", "c:/x/books", False),
    ("c:/x/books", "", False),
])
def test_covered(key, forgotten, want):
    assert covered(key, forgotten) is want


def test_forget_does_not_take_like_named_folders(scene):
    names = ["a_b", "axb", "a%b", "aZZb", "a_b.ij-worktrees/t1", "a_bc", "a_b.ij-worktrees-x/t1", "a_b/sub"]
    refs = {}
    for i, n in enumerate(names):
        p = scene.tmp / "x" / n
        p.mkdir(parents=True)
        refs[n] = _talk(scene, f"Work in {n}", folder=p, minutes_ago=30.0 + i)
    scene.wm.refresh(force=True)
    got = scene.wm.forget(str(scene.tmp / "x" / "a_b"))
    scene.wm.refresh(force=True)
    with session_scope(scene.engine) as db:
        left = {n for n, r in refs.items() if db.get(WorkEntry, f"chat:{r}") is not None}
    assert left == {"axb", "a%b", "aZZb", "a_bc", "a_b.ij-worktrees-x/t1"}, (left, got)


def test_remember_a_folder_spelled_another_way(scene):
    repo = scene.tmp / "Books"
    repo.mkdir()
    ref = _talk(scene, "Work in the repository", folder=repo)
    scene.wm.refresh(force=True)
    assert scene.wm.forget(str(repo) + "\\")["ok"]
    spelled = str(repo).replace("\\", "/").upper() + "/sub/.."
    assert scene.wm.remember(spelled) == {"ok": True}
    scene.wm.refresh(force=True)
    with session_scope(scene.engine) as db:
        assert db.get(WorkEntry, f"chat:{ref}") is not None


@pytest.mark.parametrize("folder", ["C:", "C:\\", "c:/", "C:/x/..", "/", "\\", "", "  "])
def test_the_top_of_a_drive_is_refused(scene, folder):
    assert scene.wm.forget(folder) == {"ok": False, "removed": 0}


@pytest.mark.parametrize("folder", [".", "..", "books", "\\\\server", "\\\\?\\C:\\"])
def test_a_folder_that_names_nothing_real_is_refused(scene, folder):
    # "." / a bare name / a server with no share: ok True and a junk row kept.
    got = scene.wm.forget(folder)
    with session_scope(scene.engine) as db:
        rows = [r.folder_key for r in db.exec(__import__("sqlmodel").select(WorkForgotten))]
    assert got["ok"] is False, (got, rows)


def test_forgetting_while_a_recap_is_in_flight_keeps_nothing(scene):
    ref = _talk(scene)
    scene.wm.refresh(force=True)
    job, _ = R.next_job(scene.platform)
    assert job is not None
    scene.wm.forget(str(scene.folder))
    out = asyncio.run(R.write_one(scene.platform, job))
    assert _recap(scene, ref) is None, out


# --------------------------------------------------------------------------- #
# 1. the regexes on hostile input (in a child process, with a deadline)
# --------------------------------------------------------------------------- #
def test_the_check_is_linear_on_hostile_input():
    code = textwrap.dedent(
        """
        import time
        from iron_jarvis.workmap import recap as R
        n = 200_000
        hostile = [
            "1." * n, "1," * n, "1" * n, ("1" * 50 + " k ") * (n // 53), "a." * n, "a.bbbbbbbb" * (n // 10) + "-",
            "'" * n, "'a" * n, '"' * n, "\\u201c" * n, "x.y-" * (n // 4), "a/" * n, "@" * n, "a.b " * (n // 4),
            ("w" * 30 + ".") * (n // 31), "9.9.9.9," * (n // 8), ".a" * n, "-." * n,
        ]
        worst = 0.0
        for h in hostile:
            for text in (h, "DONE: " + h, "DONE: " + h[:215], "\\n".join("OPEN: " + h[i:i+200] for i in range(0, 12000, 200))):
                t = time.perf_counter()
                R.figures_in(text)
                R.unsupported_figure(text, set())
                R.unsupported_name(text[:5000], "x", set())
                R.check(text, shown=h)
                worst = max(worst, time.perf_counter() - t)
        print("WORST", round(worst, 3))
        """
    )
    got = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=240)
    assert got.returncode == 0, got.stderr[-2000:]
    worst = float(got.stdout.strip().split()[-1])
    print(got.stdout)
    assert worst < 5.0, got.stdout


def test_a_reply_marked_low_trust_afterwards_takes_the_recap_away(scene):
    # covers_key hashes role + content only, and the "same lines" re-stamp runs
    # BEFORE the low-trust check: a kept recap survives a line turning low trust.
    ref = _talk(scene)
    _pass(scene)
    assert _recap(scene, ref).state == RECAP_KEPT
    with session_scope(scene.engine) as db:
        rec = db.get(ChatThreadRecord, ref)
        msgs = json.loads(rec.messages_json)
        msgs[-1]["trust"] = "low"
        rec.messages_json = json.dumps(msgs)
        rec.updated_at = utcnow() - timedelta(minutes=20)
        db.add(rec)
        db.commit()
    R.reset_calls()
    _pass(scene)
    row = _recap(scene, ref)
    told = B.work_block(scene.platform, folder=str(scene.folder), query="what is left here")
    assert row.state != RECAP_KEPT and "310.40" not in told, (row.state, row.reason, told[-300:])


def test_known_file_numbers_do_not_vouch_for_other_figures():
    shown = R.transcript_text(R.lines_of([
        {"role": "user", "content": "tidy the books please, all of them"},
        {"role": "assistant", "content": "All done."},
    ]))
    got = R.check("OPEN: 2026 invoices are still unpaid", shown=shown,
                  known_paths={r"C:\work\ledger_2026_q1.xlsx"})
    assert got.lines == [], got.lines


# =========================================================================== #
# what the second review described without a test
# =========================================================================== #
REC = TALK + [
    ("user", "the report has 15 pages and covers 2026"),
    ("user", "send it to bob@hartleys.com when done, it lives in C:\\Clients\\Hartleys"),
]


def _rec():
    return R.transcript_text(R.lines_of([{"role": r, "content": c} for r, c in REC]))


@pytest.mark.parametrize("line", [
    # a scale in another spelling ("15" is on record, "15 thousand" is not)
    "DECIDED: The fee is 15 grand",
    "DECIDED: The fee is 15-thousand",
    "DECIDED: The fee is 15 trillion",
    "DECIDED: The fee is 15B",
    "DECIDED: The fee is 15 mil",
    "DECIDED: The fee is 15 millions",
    "DECIDED: The fee is 15 lakh",
    "DECIDED: The rate is .15",
    # an invented quotation in other marks, matched or not
    "DECIDED: The user said «write off the other deposit»",
    "DECIDED: The user said „write off the other deposit“",
    "DECIDED: The user said ‚write off the other deposit‘",
    "DECIDED: The user said 「write off the other deposit」",
    "DECIDED: The user said ‹write off the other deposit›",
    "DECIDED: The user said `write off the other deposit`",
    "DECIDED: The user said “write off the other deposit\"",
    "DECIDED: The user said \"write off the other deposit”",
    # a file name with a short or long ending, a dot-file, a PART of a real name
    "DONE: Wrote parser.c",
    "DONE: Wrote books.sqlite",
    "DONE: Wrote report.numbers",
    "DONE: Wrote data.parquet",
    "DONE: Wrote the keys to .env",
    "DONE: Saved h.xlsx",
    "DONE: Saved march.xlsx",
    # a part of a real address or path
    "OPEN: Write to b@hartleys.com",
    "OPEN: Write to bob@hartleys.co",
    "DONE: Filed it in C:\\Clients\\Hart",
])
def test_an_invented_fact_in_another_spelling_is_dropped(line):
    assert R.check(line, shown=_rec()).lines == [], line


@pytest.mark.parametrize("line", [
    "DONE: The report has 15 pages and covers 2026",
    "OPEN: Write to <bob@hartleys.com>",
    "DONE: Filed it in `C:\\Clients\\Hartleys`",
    "DECIDED: The user said \"leave the other for me to ask about.\"",
    "OPEN: Ask about the A/R balance and/or the refund, n/a for now",
    "DONE: Matched all but 2 deposits",                    # "Two deposits" is on record
    "OPEN: Check the U.S. rules, e.g. for the refund",
    "DONE: Saved recon_march.xlsx",
    "OPEN: The bank's letter and the client's reply are still missing",
])
def test_a_true_line_in_an_everyday_spelling_is_kept(line):
    got = R.check(line, shown=_rec())
    assert len(got.lines) == 1, (line, got)


def test_a_short_scale_must_be_glued_to_its_number():
    talk = TALK + [("user", "we got 2 K-1 forms and the budget is 25k")]
    shown = R.transcript_text(R.lines_of([{"role": r, "content": c} for r, c in talk]))
    assert R.check("DONE: Filed the 2 K-1 forms", shown=shown).lines
    assert R.check("DECIDED: The budget is 25k", shown=shown).lines
    assert R.check("DECIDED: The budget is 25", shown=shown).lines == []
    assert R.check("DECIDED: The budget is 2k", shown=shown).lines == []


def test_pass_once_reports_a_record_that_cannot_be_read(scene, monkeypatch):
    # A database fault belongs on the loop's health row: it is not swallowed.
    def broken(platform, now=None):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(R, "next_job", broken)
    with pytest.raises(RuntimeError):
        asyncio.run(R.pass_once(scene.platform))
    seen: list[tuple[bool, object]] = []

    async def one_tick():
        task = asyncio.ensure_future(
            R.recap_loop(scene.platform, lambda ok, exc: seen.append((ok, exc)), interval=0.01)
        )
        while not seen:
            await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(one_tick())
    assert seen[0][0] is False and "locked" in str(seen[0][1])


def test_a_marker_that_cannot_be_read_just_now_is_never_written_over(scene, monkeypatch):
    before = R.since(scene.home)
    R.note_call(scene.home)
    _talk(scene)
    real = Path.read_text

    def in_use(self, *a, **k):
        if self.name == R.MARKER:
            raise PermissionError("in use by another program")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", in_use)
    assert R.since(scene.home) is None
    R.note_call(scene.home)
    R.note_off(scene.home)
    assert R.back_on(scene.home) is False
    assert _pass(scene) == {"skipped": "the marker could not be kept"}
    assert not scene.calls
    monkeypatch.setattr(Path, "read_text", real)
    marker = json.loads((scene.home / R.MARKER).read_text())
    assert R.since(scene.home) == before, "since was written over after one unreadable read"
    assert len(marker["calls"]) == 1 and "off" not in marker


def test_a_marker_that_holds_rubbish_starts_over_from_now(scene):
    # Not a burst: the safe direction.
    (scene.home / R.MARKER).write_text("{not json")
    _talk(scene, minutes_ago=60.0)
    _pass(scene)
    assert not scene.calls
    assert R.since(scene.home) > (utcnow() - timedelta(minutes=5)).replace(tzinfo=None)


def test_back_on_moves_the_start_once_and_says_go_on(scene):
    old = R.since(scene.home)
    assert R.back_on(scene.home) is True and R.since(scene.home) == old  # they were not off
    R.note_off(scene.home)
    assert R.back_on(scene.home) is True
    new = R.since(scene.home)
    assert new > old
    assert R.back_on(scene.home) is True and R.since(scene.home) == new


def test_a_sessions_own_copy_counts_as_its_folder(scene):
    # "Exactly the turn's folder" counts a session's own copy as the folder.
    assert K.folder_key(K.home_folder(str(scene.tmp / "Books.ij-worktrees" / "try-1"))) == \
        K.folder_key(str(scene.tmp / "Books"))
