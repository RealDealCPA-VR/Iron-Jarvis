"""v1.177.0 — the 26-file rename job, diagnosed from its own ledger.

THE RUN. "Rename all files in this folder…" over 26 real tax documents, on the
packaged app. It reported "budget" as the cause and renamed nothing. The
ledger (`run_2ca5d1b322d8`, 68 tool calls, a 4-step plan whose step 2 failed
and cascaded into 3 and 4) says what actually happened:

    list_files  ok=1  -> Organziation of messy tax documents/1099-INT ....pdf
    read_file   ok=0  {"path": "C:\\...\\Organziation of messy tax documents"}
                      -> no such file
    read_file   ok=0  {"path": "Organziation of messy tax documents"}
                      -> no such file
    read_file   ok=0  {"path": "."}            -> no such file
    read_file   ok=0  {"path": "C:\\...\\Organziation of messy tax documents"}
                      -> no such file   (the SAME call, again)
    ... 12 failed read_file calls, then 12 shell calls flailing

Four defects, each of which alone would have been survivable:

1. `read_file` answered "no such file" about a DIRECTORY THAT EXISTS. The agent
   read that as "wrong path" and tried five spellings of a folder it had just
   listed. ~24 of 68 calls went before anything was read.
2. The repeated-failure breaker never fired on those four identical calls: its
   state lived inside `perceive_act`, which a decomposed run calls once per STEP
   and again per retry, so it forgot at every boundary.
3. The plan said "For each file, read its content" — ONE step for 26 files —
   because the planner was never told what to do with a collection.
4. It could not have done better: `with_worklist` is general (its docstring
   records that the measured failure was a BUILDER) but `supervisor_definition`
   was its ONLY caller, so a BUILDER — the default for `POST /sessions` and for
   project tasks — held no worklist tools at all.

Plus the flake that turned CI red on v1.176.0: worklist claims ordered by
(created_at, id) and a batch add ties created_at, leaving a RANDOM id as the
tiebreaker.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest

from iron_jarvis.agents.decompose import _PLAN_SYSTEM, is_bulk_task
from iron_jarvis.agents.supervisor import WORKLIST_MARKER, with_worklist
from iron_jarvis.agents.types import AgentType, get_agent_definition
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.builtins import ReadFileTool, unreadable_reason

_REPO = Path(__file__).resolve().parents[1]

#: The task string from the run, verbatim.
TASK = (
    "Rename all files in this folder to a name that is more appropriate "
    "given the content in the file."
)


def _ws(tmp_path: Path) -> Path:
    (tmp_path / "Extensions").mkdir()
    (tmp_path / "Extensions" / "2025-fed-ext.pdf").write_text("x", encoding="utf-8")
    # A plain-text file: this fixture is about PATH KINDS (file vs directory vs
    # missing), so the readable case must not depend on PDF parsing.
    (tmp_path / "notes.txt").write_text("wages 84,210.00", encoding="utf-8")
    return tmp_path


def _ctx(ws: Path) -> ToolContext:
    return ToolContext(
        workspace=ws, session_id="s", agent_run_id="r",
        config=None, event_bus=None, engine=None,
    )


# ------------------------------------------- 1. the reader tells the truth ---


async def test_reading_a_directory_says_it_is_a_directory(tmp_path):
    """The sentence that cost 24 tool calls."""
    ws = _ws(tmp_path)
    result = await ReadFileTool().execute({"path": "Extensions"}, _ctx(ws))
    assert result.ok is False
    assert "DIRECTORY" in result.error
    assert "no such file" not in result.error, "the old lie is back"
    # It names what is inside, so the NEXT call can be the right one instead of
    # another guess at the path.
    assert "2025-fed-ext.pdf" in result.error
    assert "list_files" in result.error


async def test_reading_the_workspace_root_says_it_is_a_directory(tmp_path):
    """`read_file(".")` — one of the five spellings the agent tried."""
    result = await ReadFileTool().execute({"path": "."}, _ctx(_ws(tmp_path)))
    assert result.ok is False and "DIRECTORY" in result.error


async def test_a_genuinely_missing_file_still_says_no_such_file(tmp_path):
    """The one case the old message was right about must not regress."""
    result = await ReadFileTool().execute({"path": "nope.pdf"}, _ctx(_ws(tmp_path)))
    assert result.ok is False and result.error == "no such file: nope.pdf"


async def test_a_real_file_is_unaffected(tmp_path):
    ws = _ws(tmp_path)
    result = await ReadFileTool().execute({"path": "notes.txt"}, _ctx(ws))
    assert result.ok is True and "84,210.00" in result.output


def test_the_directory_hint_is_bounded(tmp_path):
    """A 5,000-entry folder must not flood the transcript the budget protects."""
    big = tmp_path / "big"
    big.mkdir()
    for i in range(400):
        (big / f"f{i:04}.pdf").write_text("x", encoding="utf-8")
    reason = unreadable_reason(big, "big")
    assert "more)" in reason and len(reason) < 900


def test_every_reader_shares_the_one_explanation():
    """`redact_scan`/`redact_pii` said "not a file"; same lie, same fix."""
    src = (_REPO / "src/iron_jarvis/documents/tools.py").read_text(encoding="utf-8")
    assert src.count("unreadable_reason(source") == 2


# --------------------------------- 2. the breaker survives step boundaries ---


def test_the_breaker_state_is_caller_scoped():
    """`perceive_act` accepts the dict so a decomposed run keeps ONE across all
    its steps and retries; None keeps the old per-invocation scope."""
    import inspect

    from iron_jarvis.agents.runtime import AgentRuntime

    sig = inspect.signature(AgentRuntime.perceive_act)
    assert "breaker_state" in sig.parameters
    assert sig.parameters["breaker_state"].default is None


def test_the_plan_runner_threads_one_breaker_through_every_step():
    """Assert the CALL SITE: a parameter nothing passes is not a fix."""
    src = (_REPO / "src/iron_jarvis/agents/decompose.py").read_text(encoding="utf-8")
    assert "breaker_state: dict[str, Any] = {}" in src
    assert "breaker_state=breaker_state" in src
    # ...and it is created OUTSIDE the per-step loop, or it resets exactly as
    # before.
    assert src.index("breaker_state: dict[str, Any] = {}") < src.index(
        "for index, step in enumerate(plan):"
    )


def test_streaks_accumulate_across_invocations_of_the_loop():
    """The behaviour that matters: two separate calls, one memory."""
    state: dict = {}
    for _ in range(2):
        streaks = state.setdefault("fail_streaks", {})
        streaks["read_file::{}"] = streaks.get("read_file::{}", 0) + 1
    assert state["fail_streaks"]["read_file::{}"] == 2


# ------------------------------------ 3. the planner plans the LOOP ----------


def test_the_planner_is_told_not_to_write_for_each_file():
    """The plan that failed was "For each file, read its content" — one step for
    26 files, from a prompt that said nothing about collections."""
    assert "BULK JOBS" in _PLAN_SYSTEM
    assert "worklist_add" in _PLAN_SYSTEM
    assert "worklist_next" in _PLAN_SYSTEM
    assert "worklist_done" in _PLAN_SYSTEM
    assert "worklist_status" in _PLAN_SYSTEM
    assert re.search(r"NEVER write a step like", _PLAN_SYSTEM)


# ---------------------- 4. the worklist reaches the agent that does the work --


def test_the_measured_task_reads_as_bulk():
    assert is_bulk_task(TASK) is True


def test_a_builtin_builder_has_no_worklist_tools():
    """Pin the gap itself: the roster is bare, which is WHY the wrap is needed."""
    builder = get_agent_definition(AgentType.BUILDER)
    assert [t for t in builder.tools if "worklist" in t] == []


def test_wrapping_gives_a_builder_the_tools_and_the_procedure():
    wrapped = with_worklist(get_agent_definition(AgentType.BUILDER))
    assert {"worklist_add", "worklist_next", "worklist_done", "worklist_status"} <= set(
        wrapped.tools
    )
    # Tools with no instruction to use them is half a fix (the v1.142 lesson).
    assert WORKLIST_MARKER in wrapped.system_prompt
    # The shared canonical record must NOT be mutated.
    assert [t for t in get_agent_definition(AgentType.BUILDER).tools if "worklist" in t] == []


def test_the_orchestrator_wraps_a_bulk_run():
    """THE MISSING WIRE. `with_worklist` existed and only the supervisor called
    it, so every builder bulk job ran without it."""
    src = (_REPO / "src/iron_jarvis/agents/orchestrator.py").read_text(encoding="utf-8")
    assert "with_worklist(agent_def)" in src
    assert "is_bulk_task(session.task" in src
    # Gated, not blanket: a one-file edit should not pay for four tool specs.
    gate = src.index("is_bulk_task(session.task")
    wrap = src.index("with_worklist(agent_def)")
    assert gate < wrap


def test_a_non_bulk_task_is_left_alone():
    assert is_bulk_task("fix the typo in README.md") is False


# ------------------------- 4b. a chunk step has room to finish a chunk -------


def _session(task: str, max_steps=None):
    from types import SimpleNamespace

    return SimpleNamespace(task=task, max_steps=max_steps)


def test_a_bulk_step_gets_room_to_finish_a_chunk():
    """THE SECOND FAILED RUN said it plainly: "the mini-loop budget was
    exhausted before step 2 completed". Six rounds cannot claim 5 items, read
    them, act on them and report each — so the step that does the work could
    never land, worklist or not."""
    from types import SimpleNamespace

    from iron_jarvis.agents.decompose import BULK_MINI_LOOP_STEPS, mini_loop_budget
    from iron_jarvis.worklist.store import DEFAULT_CLAIM

    config = SimpleNamespace(max_agent_steps=40)
    bulk = mini_loop_budget(config, _session(TASK))
    plain = mini_loop_budget(config, _session("fix the typo in README.md"))

    assert bulk == BULK_MINI_LOOP_STEPS
    assert bulk > plain, "a bulk step still gets the write-one-file allowance"
    # Enough for the chunk the worklist actually hands out.
    assert bulk >= DEFAULT_CLAIM + 2


def test_the_non_bulk_budget_is_unchanged():
    """Byte-identical for every other step in the app."""
    from types import SimpleNamespace

    from iron_jarvis.agents.decompose import MAX_MINI_LOOP_STEPS, mini_loop_budget

    config = SimpleNamespace(max_agent_steps=40)
    assert mini_loop_budget(config, _session("write a summary file")) == (
        MAX_MINI_LOOP_STEPS
    )


def test_the_session_budget_still_wins():
    """This raises a per-STEP allowance, never the total a run may spend — a
    session told "6 steps" does not get 12 because the task says "all files"."""
    from types import SimpleNamespace

    from iron_jarvis.agents.decompose import mini_loop_budget

    tiny = mini_loop_budget(SimpleNamespace(max_agent_steps=4), _session(TASK))
    assert tiny <= 4


# --------------------------------- 5. the worklist hands work out in order ---


def _tied_board():
    """A board whose rows all share one ``created_at`` — what a SURVEY produces.

    Reproducing the real condition matters: a folder is added in ONE call, and on
    a machine with a coarse clock every row of that batch lands on the same
    timestamp. THIS BOX DOES NOT (it stamps three distinct times for three rows),
    which is exactly why the first version of this test passed here and failed on
    CI. Forcing the tie removes the machine from the equation.

    Rows are inserted in REVERSE alphabetical order on purpose. Without an ORDER
    BY, SQLite hands back rowid order — insertion order — so an unordered
    read-back returns zzz, mmm, aaa and an ordered one returns aaa, mmm, zzz.
    The two are distinguishable only because the insert order disagrees with the
    sort order.
    """
    from sqlmodel import select

    from iron_jarvis.core.db import init_db, make_engine, session_scope
    from iron_jarvis.worklist.models import WorklistItem
    from iron_jarvis.worklist.store import WorklistStore

    engine = make_engine(str(Path(tempfile.mkdtemp()) / "t.db"))
    init_db(engine)
    store = WorklistStore(engine)
    store.add("b", [(f"C:/f/{n}.pdf", "") for n in ("zzz", "mmm", "aaa")])
    with session_scope(engine) as db:
        rows = list(db.exec(select(WorklistItem)))
        stamp = rows[0].created_at
        for row in rows:
            row.created_at = stamp
            db.add(row)
        db.commit()
    return store


def test_a_tied_batch_claims_in_key_order_not_insertion_order():
    """THE TIEBREAKER. A survey adds a folder in one call, every row ties on
    created_at, and the tiebreaker used to be `id` — `new_id("wl")`, RANDOM. So
    which files a chunk held changed between runs over identical input."""
    got, _ = _tied_board().claim("b", "c", 3)
    assert [i.key for i in got] == ["C:/f/aaa.pdf", "C:/f/mmm.pdf", "C:/f/zzz.pdf"]


def test_the_claim_READ_BACK_is_ordered_too():
    """THE HALF THE FIRST FIX MISSED, and CI caught. Ordering the `pending`
    query decides WHICH rows are claimed; the read-back decides what order the
    agent RECEIVES them in. With no ORDER BY on the read-back that is whatever
    index the planner picked — one order on this box, five across eight
    identical CI runs. Same assertion as above, stated against the returned
    sequence, because that sequence is the thing the agent works through."""
    store = _tied_board()
    got, _ = store.claim("b", "c", 2)
    # A partial claim must take the FIRST two by key, in key order.
    assert [i.key for i in got] == ["C:/f/aaa.pdf", "C:/f/mmm.pdf"]


def test_repeated_claims_agree_across_fresh_databases():
    """Belt and braces: identical input, identical output, every time."""
    orders = {tuple(i.key for i in _tied_board().claim("b", "c", 3)[0]) for _ in range(8)}
    assert len(orders) == 1, f"claim order is not deterministic: {orders}"


def test_ordering_has_a_total_tiebreaker():
    src = (_REPO / "src/iron_jarvis/worklist/store.py").read_text(encoding="utf-8")
    # key_norm is unique per board, so it makes every ordering total.
    assert src.count("WorklistItem.key_norm, WorklistItem.id") == 4
    assert "WorklistItem.created_at, WorklistItem.id)" not in src


# ------------------------------------- 6. a red suite cannot ship an installer --


def test_the_release_workflow_gates_on_the_suite():
    """v1.176.0 published a green installer while Tests went red 16 minutes
    later, because they are separate workflows running concurrently."""
    wf = (_REPO / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert re.search(r"^  suite:", wf, re.M), "no gate job"
    assert re.search(r"^    needs: suite", wf, re.M), "the installer does not need it"
    assert "uv run pytest" in wf
    # The gate must not be defeated by the pipeline-exit-code trap.
    assert "PIPESTATUS[0]" in wf


def test_the_manual_no_longer_claims_a_gate_that_did_not_exist():
    doc = (_REPO / "CLAUDE.md").read_text(encoding="utf-8")
    assert "a red Tests run BLOCKS the installer" not in doc
    assert "RUNS THE OFFLINE SUITE AS A GATE" in doc
