"""Next leaps 28-a (R2.17): the words of the folder's coding record, the
summary the model menus read, and chat's ``coding_record``.

Pinned:
* every sentence carries its count; under five decided pieces "(too few to
  judge)"; nothing on record says nothing; work not decided is said apart and
  NEVER counted in an "of N" (the mutation "count undecided as thrown" goes
  red here);
* no percentage and no "best" anywhere (D-28.1: the record chooses nothing);
* the summary groups by tool and model, orders by decided pieces, and never
  reads the nightly test's rows (28-c's "replay");
* chat: the words that arm ``coding_record`` (and plain chat that does not),
  its answer from the same rows for the session's folder or a named one,
  Local only work left out off this network, the phone naming no folder;
  the tool is a reader (allow), refused to another computer and found by the
  capability lookup.
"""

from __future__ import annotations

import asyncio
import re

import pytest

from iron_jarvis.core.db import init_db
from iron_jarvis.harness import scoreboard as SB
from iron_jarvis.settings import records_scoreboard as RS

FOLDER = "C:/code/shop"


@pytest.fixture()
def engine(tmp_path):
    from sqlmodel import create_engine

    eng = create_engine(f"sqlite:///{(tmp_path / 'ij.db').as_posix()}")
    init_db(eng)
    return eng


def piece(engine, n: int, *, verdict: str = "", checked: str = "", tool: str = "codex", model: str = "gpt-x",
          provider: str = "", folder: str = FOLDER, source: str = SB.SOURCE_WORK, local_only: bool = False) -> None:
    how = {SB.KEPT: SB.HOW_COMMITTED, SB.THROWN: SB.HOW_DISCARDED}.get(verdict, "")
    assert SB.note(engine, session_id=f"s{n}", ref=f"s{n}:t1", folder=folder,
                   surface=SB.SURFACE_TOOL if tool != SB.TOOL_JARVIS else SB.SURFACE_CODE, tool=tool,
                   provider=provider, model=model, checked=checked, verdict=verdict, how=how, source=source,
                   local_only=local_only)


# --------------------------------------------------------------------------- #
# the words
# --------------------------------------------------------------------------- #


def test_nothing_on_record_says_nothing():
    assert SB.words(0, 0, 0, 0, 0) == ""


def test_four_decided_pieces_are_too_few_to_judge_and_five_are_not():
    assert SB.words(3, 1, 0, 0, 0) == "In this folder: kept 3 of 4 (too few to judge)"
    assert SB.words(2, 1, 0, 0, 0) == "In this folder: kept 2 of 3 (too few to judge)"
    assert SB.words(4, 1, 0, 0, 0) == "In this folder: kept 4 of 5"
    assert SB.words(7, 2, 0, 8, 1) == "In this folder: kept 7 of 9, passed its checks 8 of 9"


def test_work_not_decided_is_said_apart_and_never_counted_in_an_of():
    assert SB.words(7, 2, 3, 8, 1) == "In this folder: kept 7 of 9, passed its checks 8 of 9; 3 not decided yet"
    assert SB.words(0, 0, 3, 0, 0) == "In this folder: 3 not decided yet"
    assert SB.words(0, 0, 2, 2, 0) == "In this folder: passed its checks 2 of 2; 2 not decided yet"
    # Undecided never moves the "of N": the same decided counts read the same.
    assert SB.words(2, 1, 40, 0, 0).startswith("In this folder: kept 2 of 3 (too few to judge)")


def test_every_sentence_carries_its_count_and_no_percentage_or_best():
    for args in [(1, 0, 0, 0, 0), (0, 1, 0, 0, 0), (7, 2, 3, 8, 1), (0, 0, 1, 1, 0), (5, 0, 0, 0, 5)]:
        said = SB.words(*args)
        assert said.startswith(SB.LEAD)
        for part in re.split(r"[;,]", said[len(SB.LEAD):]):
            assert re.search(r"\d", part), (args, part)
        assert "%" not in said and "best" not in said.lower()


def test_the_summary_counts_by_tool_and_model_and_undecided_is_never_thrown(engine):
    piece(engine, 1, verdict=SB.KEPT, checked=SB.CHECK_PASSED)
    piece(engine, 2, verdict=SB.KEPT, checked=SB.CHECK_FAILED)
    piece(engine, 3, verdict=SB.THROWN)
    piece(engine, 4)  # not decided yet
    piece(engine, 5, verdict=SB.KEPT, tool="claude-code", model="")
    got = SB.summary(engine, FOLDER)
    assert got["folder"] == SB.folder_key(FOLDER) and got["min_samples"] == SB.MIN_SAMPLES == 5
    codex, claude = got["rows"]
    assert (codex["tool"], codex["model"]) == ("codex", "gpt-x")
    assert {k: codex[k] for k in ("kept", "thrown", "undecided", "passed", "failed", "samples")} == {
        "kept": 2, "thrown": 1, "undecided": 1, "passed": 1, "failed": 1, "samples": 3}
    assert codex["words"] == "In this folder: kept 2 of 3 (too few to judge), passed its checks 1 of 2; 1 not decided yet"
    assert (claude["tool"], claude["model"], claude["samples"]) == ("claude-code", "", 1)
    # Another spelling of the same folder is the same folder; another folder is not.
    assert SB.summary(engine, "c:\\code\\shop\\")["rows"] == got["rows"]
    assert SB.summary(engine, "C:/code/other")["rows"] == []


def test_the_nightly_tests_rows_are_never_read_with_the_real_work(engine):
    piece(engine, 1, verdict=SB.KEPT)
    piece(engine, 2, verdict=SB.THROWN, source=SB.SOURCE_REPLAY)
    (row,) = SB.summary(engine, FOLDER)["rows"]
    assert (row["kept"], row["thrown"]) == (1, 0)
    assert SB.settle_session(engine, "s2", SB.KEPT, SB.HOW_MERGED) == 0


def test_one_piece_is_one_row_and_a_verdict_is_never_rewritten_by_a_note(engine, monkeypatch):
    # The folder counts as a repository, so only the verdict rule keeps the
    # rewind below from throwing the kept piece away.
    monkeypatch.setattr(SB, "has_git", lambda folder: True)
    assert SB.note(engine, session_id="s1", ref="s1:t1", folder=FOLDER, surface=SB.SURFACE_TOOL, tool="codex")
    assert not SB.note(engine, session_id="s1", ref="s1:t1", folder=FOLDER, surface=SB.SURFACE_TOOL, tool="codex",
                       checked=SB.CHECK_PASSED, verdict=SB.THROWN, how=SB.HOW_DISCARDED)
    assert SB.settle_session(engine, "s1", SB.KEPT, SB.HOW_COMMITTED) == 1
    assert SB.settle_session(engine, "s1", SB.THROWN, SB.HOW_DISCARDED) == 0, "a decided piece keeps its verdict"
    assert SB.rewound(engine, "s1", ["t1"]) == 0
    (row,) = SB.summary(engine, FOLDER)["rows"]
    assert (row["kept"], row["thrown"], row["passed"]) == (1, 0, 1)
    # No folder (a session started with none) is no piece.
    assert not SB.note(engine, session_id="s9", ref="s9:t1", folder="", surface=SB.SURFACE_TOOL, tool="codex")


# --------------------------------------------------------------------------- #
# chat: coding_record
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("text, want", [
    ("which model does best in this folder?", True),
    ("Which coding tool works best on this repo?", True),
    ("what model did I keep the most here", True),
    ("show me the coding record", True),
    ("how often have I kept Codex's work here?", True),
    ("what's the best model for this folder", True),
    ("fix the failing test in calc.py", False),
    ("which file has the bug?", False),
    ("make the model menu blue", False),
])
def test_the_words_that_arm_the_coding_record(text, want):
    assert (RS.scoreboard_tools_for(text) == [RS.NAME]) is want
    from iron_jarvis.settings.records import record_tools_for

    assert (RS.NAME in record_tools_for(text)) is want


class _Platform:
    def __init__(self, engine) -> None:
        self.engine = engine


def _answer(engine, named: str = "", *, phone: bool = False, withhold: bool = False) -> object:
    return RS.report(_Platform(engine), None, named, "", phone=phone, withhold=withhold)


def test_chat_answers_from_the_same_rows_with_every_count(engine):
    for n in range(1, 6):
        piece(engine, n, verdict=SB.KEPT if n < 5 else SB.THROWN, checked=SB.CHECK_PASSED)
    piece(engine, 6, tool=SB.TOOL_JARVIS, provider="anthropic", model="claude-x")
    got = _answer(engine, FOLDER)
    assert got.ok
    out = got.output
    assert out.startswith(RS.LEAD)
    assert f"Folder {SB.folder_key(FOLDER)}:" in out
    assert "- Codex · gpt-x: In this folder: kept 4 of 5, passed its checks 5 of 5" in out
    assert "- Iron Jarvis · anthropic/claude-x: In this folder: 1 not decided yet" in out
    assert RS.CHOOSE_NOTE in out and "best" not in out.replace(RS.CHOOSE_NOTE, "").lower().replace(
        "does best", "")
    assert got.data["min_samples"] == 5 and len(got.data["rows"]) == 2


def test_chat_with_nothing_on_record_says_so_and_never_makes_a_figure_up(engine):
    assert RS.NOTHING in _answer(engine).output
    assert RS.NOTHING_HERE.format(folder=SB.folder_key(FOLDER)) in _answer(engine, FOLDER).output


def test_local_only_work_is_left_out_off_this_network_and_the_phone_names_no_folder(engine):
    piece(engine, 1, verdict=SB.KEPT)
    piece(engine, 2, verdict=SB.THROWN, local_only=True)
    on_pc = _answer(engine, FOLDER).output
    assert "kept 1 of 2" in on_pc and RS.LOCAL_HELD not in on_pc
    away = _answer(engine, FOLDER, withhold=True).output
    assert "kept 1 of 1" in away and RS.LOCAL_HELD in away
    phone = _answer(engine, FOLDER, phone=True, withhold=True)
    assert SB.folder_key(FOLDER) not in phone.output and RS.PHONE_FOLDER in phone.output
    assert all(r["folder"] == "" for r in phone.data["rows"])


def test_a_clients_folder_the_turn_may_not_be_told_about_is_never_named_asked_or_listed(engine, tmp_path):
    """The client wall (review): a confirmed client's folder that the turn
    may not be told about is left out whether the chat names it or lists
    every folder with a record; its key, its models and its counts never
    show, one sentence says something was left out; a turn that names the
    client is told."""
    from iron_jarvis.workmap.clients import store, wall

    work = tmp_path / "Work"
    hart, shop = work / "Hartleys", work / "Shop"
    for f in (hart, shop):
        f.mkdir(parents=True)
    plat = _Platform(engine)
    store.create(plat, folder=str(hart), name="Hartleys")
    piece(engine, 1, verdict=SB.KEPT, folder=str(hart), model="gpt-hart")
    piece(engine, 2, verdict=SB.THROWN, folder=str(hart), model="gpt-hart")
    piece(engine, 3, verdict=SB.KEPT, folder=str(shop), model="gpt-shop")
    hart_key, shop_key = SB.folder_key(str(hart)), SB.folder_key(str(shop))
    turn = wall.turn_wall(engine, str(shop), "which model does best in this folder?")

    named = RS.report(plat, turn, str(hart), "", phone=False, withhold=False)
    assert named.ok
    assert hart_key not in named.output.lower() and "gpt-hart" not in named.output and "of 2" not in named.output
    assert RS.HELD in named.output and named.data["left_out"] == 1 and named.data["rows"] == []

    # No folder named: the listing (``SB.folders``) holds the client's folder.
    assert set(SB.folders(engine)) == {hart_key, shop_key}
    listed = RS.report(plat, turn, "", "", phone=False, withhold=False)
    assert hart_key not in listed.output.lower() and "gpt-hart" not in listed.output
    assert f"Folder {shop_key}:" in listed.output and "gpt-shop" in listed.output
    assert RS.HELD in listed.output and listed.data["left_out"] == 1
    assert {r["folder"] for r in listed.data["rows"]} == {shop_key}

    # The owner names the client: told.
    told = RS.report(plat, wall.turn_wall(engine, str(shop), "which model does best for Hartleys?"), str(hart), "",
                     phone=False, withhold=False)
    assert "gpt-hart" in told.output and "kept 1 of 2" in told.output and told.data["left_out"] == 0


def test_the_wall_is_asked_about_every_spelling_the_record_keeps(engine, monkeypatch):
    """The record's key folds case on every OS; the wall's own key keeps it
    on Linux. So the wall is asked about each spelling on record (one no is
    a no), never only about the folded key."""
    from iron_jarvis.workmap.clients import wall

    piece(engine, 1, verdict=SB.KEPT, folder="/home/u/Work/Hartleys")
    piece(engine, 2, verdict=SB.KEPT, folder="/home/u/work/hartleys")
    asked: list[str] = []

    def strict(turn, cmap, *, folder, **_k):
        asked.append(str(folder))
        return str(folder) != "/home/u/Work/Hartleys"

    monkeypatch.setattr(wall, "may_tell", strict)
    got = RS.report(_Platform(engine), None, "/home/u/work/hartleys", "", phone=False, withhold=False)
    assert "/home/u/Work/Hartleys" in asked
    assert got.data["left_out"] == 1 and got.data["rows"] == [] and "kept" not in got.output


def test_the_tool_is_a_reader_refused_to_another_computer_and_found_by_the_lookup():
    from iron_jarvis.capability_search.join import OWN_PLACE
    from iron_jarvis.capability_search.phrasings import PHRASINGS
    from iron_jarvis.core.config import default_permissions
    from iron_jarvis.core.trust import REMOTE_RUN_DENY

    assert default_permissions()[RS.NAME] == "allow"
    assert RS.NAME in REMOTE_RUN_DENY and RS.NAME in OWN_PLACE
    assert len(PHRASINGS[RS.NAME]) == 8
    tool = RS.CodingRecordTool(_Platform(None))
    assert tool.returns_untrusted_content is True and tool.input_schema["properties"].keys() == {"folder"}


def test_the_tool_runs_off_the_loop(engine, monkeypatch):
    import threading

    seen: list[bool] = []
    real = RS.report

    def watched(*a, **k):
        seen.append(threading.current_thread() is threading.main_thread())
        return real(*a, **k)

    monkeypatch.setattr(RS, "report", watched)
    tool = RS.CodingRecordTool(_Platform(engine))
    got = asyncio.run(tool.execute({"folder": FOLDER}, type("Ctx", (), {"thread_id": ""})()))
    assert got.ok and seen == [False]
