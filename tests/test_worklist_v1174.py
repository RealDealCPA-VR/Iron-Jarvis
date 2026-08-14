"""The durable worklist (v1.174.0, P3) — the agent FINISHES the job.

THE MEASURED FAILURE. "Rename all files in this folder to a name that is more
appropriate given the content", 26 entries, real folder: FAILED — reached max
steps, ZERO files renamed, 12 steps / 18 tool calls, three documents read
TWICE. Nothing the run learned outlived it, because the only record of "which
files have I looked at" was the transcript.

Every test here pins one property that failure needs, and each one has a silent
failure mode — the reason it is asserted on VALUES, not on the absence of an
exception:

* adding is idempotent, and a second survey after a RENAME queues nothing (the
  keys have changed; matching only on keys would re-do the whole job, forever);
* claiming is a compare-and-swap, so two subagents cannot take the same file
  (a read-then-write hands it to both, and both would rename it);
* RECLAIMING is a compare-and-swap too, under a barrier-synchronised race —
  its first version predicated only on ``status == 'doing'`` and handed all
  three items to BOTH agents in 4 of 40 trials;
* a claim that never reported back is reclaimable AND SAID SO (otherwise a
  crashed chunk is invisibly abandoned — "0 pending" over unfinished work), and
  a claim held by a run that has ENDED comes back immediately — there is no
  other agent to wait for;
* the scope is the JOB — the department's root session for a live team, and its
  project/folder + task for a RE-RUN, because `rerun_session` clones the inputs
  into a fresh session id and a session-keyed board would start empty every
  time (the wave's "a re-run does no work twice" is exactly this test);
* two chats in different folders never share a board, though both run under the
  literal session id "chat";
* a result path is a FILE READ and goes through `core/fs_policy`, and a report
  that read only the first 200 rows says so;
* an unknown key fails LOUDLY (a silent no-op would let an agent believe it had
  recorded progress it had not);
* "done" is checkable against the disk, not believed from prose;
* the table is in ``SQLModel.metadata`` at BOOT (the v1.151.2 lesson) and the
  four tools are permitted by default (an unknown key resolves to "ask", and a
  headless "ask" is a DENY — the feature would be invisible-dead on the user's
  install).
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import types
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.ids import utcnow
from iron_jarvis.core.models import AgentRun, AgentState, AgentType
from iron_jarvis.core.models import Session as SessionRow
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.worklist import (
    MAX_ITEMS_PER_ADD,
    WORKLIST_TOOL_NAMES,
    WorklistStore,
    normalize_key,
    register,
)
from iron_jarvis.worklist.models import DOING, DONE, FAILED, PENDING, WorklistItem

BOARD = "root-session"


@pytest.fixture
def platform(tmp_path):
    return build_platform(str(tmp_path))


@pytest.fixture
def store(platform) -> WorklistStore:
    assert platform.worklist is not None, "build_platform must attach the worklist"
    return platform.worklist


def ctx_for(platform, tmp_path, session_id="s1", run_id="r1") -> ToolContext:
    return ToolContext(
        workspace=Path(tmp_path),
        session_id=session_id,
        agent_run_id=run_id,
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )


def board_of(platform, ctx: ToolContext) -> str:
    """The board a ToolContext's calls actually land on.

    Never hardcode a session id here: the board follows the JOB (project/folder
    + task) when a session row exists and ``(session, workspace)`` when one does
    not, precisely so a re-run and a second chat cannot collide. A test that
    asserted against the literal session id would be asserting the bug.
    """
    return platform.worklist.board_id_for(ctx.session_id, ctx.agent_run_id, ctx.workspace)


def keys(items) -> list[str]:
    return [i.key for i in items]


# --------------------------------------------------------------------------- #
# (1) Idempotence — the resume property.
# --------------------------------------------------------------------------- #
def test_adding_the_same_survey_twice_adds_nothing_and_resets_nothing(store):
    files = [(f"C:/docs/{n}.pdf", "") for n in ("a", "b", "c")]
    first = store.add(BOARD, files)
    assert first["added"] == 3 and first["total"] == 3

    store.finish(BOARD, "C:/docs/a.pdf", status=DONE, note="renamed")

    second = store.add(BOARD, files)
    assert second["added"] == 0, "a re-survey must not queue work that is tracked"
    assert second["existing"] == 3
    assert second["total"] == 3, "no duplicate rows"
    # The mutation this catches: an `add` that overwrites status would put the
    # finished item back in the queue, and the job would never end.
    assert store.get(BOARD, "C:/docs/a.pdf").status == DONE
    assert store.summary(BOARD)["done"] == 1


def test_a_rerun_over_the_RENAMED_files_queues_nothing(store):
    """THE ACCEPTANCE PROPERTY, in miniature.

    Run 1 renames ``scan001.pdf`` to ``1099-INT Vanguard 2025.pdf``. Run 2
    surveys the same folder and sees only the NEW name — which is not the key
    of anything. Matching on keys alone, run 2 would queue it and rename an
    already-correct file; the loop never terminates.
    """
    store.add(BOARD, [("C:/tax/scan001.pdf", "")])
    store.finish(
        BOARD,
        "C:/tax/scan001.pdf",
        status=DONE,
        note="renamed from content",
        result_key="C:/tax/1099-INT Vanguard 2025.pdf",
    )

    resurvey = store.add(BOARD, [("C:/tax/1099-INT Vanguard 2025.pdf", "")])
    assert resurvey["added"] == 0
    assert resurvey["produced"] == 1
    assert resurvey["produced_keys"] == ["C:/tax/1099-INT Vanguard 2025.pdf"]
    assert resurvey["remaining"] == 0 and resurvey["complete"] is True


def test_a_result_of_a_FAILED_item_is_still_queued(store):
    """Only a *done* item's result is treated as already-produced. A failed
    attempt's leftover output must not silence the item that still needs work."""
    store.add(BOARD, [("C:/tax/x.pdf", "")])
    store.finish(
        BOARD, "C:/tax/x.pdf", status=FAILED, note="unreadable", result_key="C:/tax/y.pdf"
    )
    again = store.add(BOARD, [("C:/tax/y.pdf", "")])
    assert again["added"] == 1 and again["produced"] == 0


def test_normalization_makes_one_file_one_item(store):
    assert normalize_key("C:\\Tax\\A.PDF") == normalize_key("c:/tax//a.pdf")
    report = store.add(
        BOARD,
        [("C:\\Tax\\A.PDF", ""), ("c:/tax/a.pdf", ""), ("C:/tax//A.pdf/", "")],
    )
    assert report["added"] == 1, "separator flavour and case are not new work"
    assert report["duplicate"] == 2
    assert report["total"] == 1
    # And the DISPLAY form is what the agent typed, not the normalized one.
    assert store.get(BOARD, "c:/TAX/a.PDF").key == "C:\\Tax\\A.PDF"


# --------------------------------------------------------------------------- #
# (2) Claiming — chunked delegation without collisions.
# --------------------------------------------------------------------------- #
def test_two_subagents_never_claim_the_same_item(store):
    store.add(BOARD, [(f"C:/f/{i:02d}.pdf", "") for i in range(26)])

    first, _ = store.claim(BOARD, "child-1", 5)
    second, _ = store.claim(BOARD, "child-2", 5)

    assert len(first) == 5 and len(second) == 5
    assert set(keys(first)).isdisjoint(keys(second)), "a file handed to two agents"
    assert len(set(keys(first)) | set(keys(second))) == 10
    summary = store.summary(BOARD)
    assert summary["doing"] == 10 and summary["pending"] == 16
    assert {i.claimed_by for i in first} == {"child-1"}
    assert {i.claimed_by for i in second} == {"child-2"}


def test_concurrent_claims_hand_out_each_item_exactly_once(store):
    """The compare-and-swap, under a real race.

    The sequential test above passes even for a naive read-then-write, because
    each call commits before the next begins. Four THREADS claiming at once is
    what distinguishes them: with the ``WHERE status = 'pending'`` predicate the
    loser updates zero rows and simply gets fewer items; without it, both
    writers stamp the same rows and two agents rename the same file.
    """
    from concurrent.futures import ThreadPoolExecutor

    store.add(BOARD, [(f"C:/f/{i:02d}.pdf", "") for i in range(12)])
    with ThreadPoolExecutor(max_workers=4) as pool:
        batches = list(
            pool.map(lambda n: store.claim(BOARD, f"child-{n}", 3)[0], range(4))
        )

    handed = [row.key for batch in batches for row in batch]
    assert len(handed) == len(set(handed)), f"an item was claimed twice: {handed}"
    # A loser gets FEWER items, never someone else's — so the total handed out
    # may be under 12, and the books must still balance exactly.
    summary = store.summary(BOARD)
    assert summary["doing"] == len(handed)
    assert summary["pending"] == 12 - len(handed)
    # Each row belongs to exactly the agent whose batch reported it.
    for n, batch in enumerate(batches):
        for row in batch:
            assert store.get(BOARD, row.key).claimed_by == f"child-{n}"
    # Nothing is STRANDED: whatever a loser missed is still claimable, and one
    # more pass drains the list.
    rest, _ = store.claim(BOARD, "mop-up", 25)
    assert len(rest) == 12 - len(handed)
    assert set(keys(rest)).isdisjoint(handed)
    assert store.summary(BOARD)["pending"] == 0


def test_a_claim_is_bounded_and_defaults_to_five(store):
    store.add(BOARD, [(f"C:/f/{i}.pdf", "") for i in range(60)])
    assert len(store.claim(BOARD, "c", 999)[0]) == 25  # MAX_CLAIM
    assert len(store.claim(BOARD, "c", 0)[0]) == 1
    assert len(store.claim(BOARD, "c", "not a number")[0]) == 5


def test_claiming_an_exhausted_worklist_returns_nothing(store):
    store.add(BOARD, [("C:/f/only.pdf", "")])
    store.finish(BOARD, "C:/f/only.pdf", status=DONE)
    got, reclaimed = store.claim(BOARD, "child", 5)
    assert got == [] and reclaimed == 0
    assert store.summary(BOARD)["complete"] is True


def test_a_dead_claim_is_reclaimed_and_a_live_one_is_not(store):
    """An agent that died mid-chunk leaves items in `doing`. Nothing else would
    ever hand them out — the resumed run would report 0 pending over work that
    was never done. Both directions, because a reclaim window that is always
    open would let two live agents collide."""
    store.add(BOARD, [("C:/f/dead.pdf", ""), ("C:/f/live.pdf", "")])
    dead, _ = store.claim(BOARD, "crashed", 1)
    live, _ = store.claim(BOARD, "healthy", 1)
    assert keys(dead) == ["C:/f/dead.pdf"] and keys(live) == ["C:/f/live.pdf"]

    with session_scope(store.engine) as db:
        row = db.exec(
            select(WorklistItem).where(WorklistItem.key == "C:/f/dead.pdf")
        ).first()
        row.claimed_at = utcnow() - timedelta(seconds=3600)
        db.add(row)
        db.commit()

    got, reclaimed = store.claim(BOARD, "resumer", 5, stale_seconds=900)
    assert keys(got) == ["C:/f/dead.pdf"], "the fresh claim must NOT be stolen"
    assert reclaimed == 1
    assert store.get(BOARD, "C:/f/dead.pdf").claimed_by == "resumer"
    assert store.get(BOARD, "C:/f/live.pdf").claimed_by == "healthy"


def test_finishing_clears_the_claim_so_a_done_item_is_never_reclaimed(store):
    store.add(BOARD, [("C:/f/one.pdf", "")])
    store.claim(BOARD, "child", 1)
    store.finish(BOARD, "C:/f/one.pdf", status=DONE)
    row = store.get(BOARD, "C:/f/one.pdf")
    assert row.claimed_by == "" and row.claimed_at is None and row.claim_token == ""
    got, reclaimed = store.claim(BOARD, "later", 5, stale_seconds=1)
    assert got == [] and reclaimed == 0


def test_pending_hands_the_claim_back(store):
    store.add(BOARD, [("C:/f/one.pdf", "")])
    store.claim(BOARD, "child", 1)
    store.finish(BOARD, "C:/f/one.pdf", status=PENDING, note="not my area")
    assert store.summary(BOARD)["pending"] == 1
    again, _ = store.claim(BOARD, "other", 1)
    assert keys(again) == ["C:/f/one.pdf"]


# --------------------------------------------------------------------------- #
# (3) Scope — one department, one list.
# --------------------------------------------------------------------------- #
def _link(engine, parent_session, child_session) -> None:
    """Persist a parent run and a child run, as `delegate` does."""
    with session_scope(engine) as db:
        parent = AgentRun(
            id="run-parent", session_id=parent_session, agent_type=AgentType.SUPERVISOR
        )
        child = AgentRun(
            id="run-child",
            session_id=child_session,
            parent_id="run-parent",
            agent_type=AgentType.BUILDER,
        )
        db.add(parent)
        db.add(child)
        db.commit()


def test_a_delegated_child_resolves_to_its_parents_board(store):
    _link(store.engine, "sup-session", "kid-session")
    assert store.board_id_for("kid-session", "run-child") == "sup-session"
    assert store.board_id_for("sup-session", "run-parent") == "sup-session"
    # And an unrelated task keeps its own scope — never a shared/global list.
    assert store.board_id_for("other-session", "no-such-run") == "other-session"


def test_one_teams_items_are_invisible_to_another(store):
    store.add("team-a", [("C:/a.pdf", "")])
    store.add("team-b", [("C:/b.pdf", "")])
    assert store.summary("team-a")["total"] == 1
    assert keys(store.items("team-b")) == ["C:/b.pdf"]
    got, _ = store.claim("team-b", "child", 5)
    assert keys(got) == ["C:/b.pdf"]


# --------------------------------------------------------------------------- #
# (4) Honesty — refusals, bounds, and a checkable "done".
# --------------------------------------------------------------------------- #
async def test_reporting_an_unknown_key_fails_loudly_and_records_nothing(
    platform, tmp_path
):
    ctx = ctx_for(platform, tmp_path)
    await platform.registry.invoke("worklist_add", {"items": ["a.pdf"]}, ctx, platform.permissions)
    result = await platform.registry.invoke(
        "worklist_done", {"key": "typo.pdf", "status": "done"}, ctx, platform.permissions
    )
    assert result.ok is False
    assert "typo.pdf" in (result.error or "")
    assert platform.worklist.summary(board_of(platform, ctx))["done"] == 0, (
        "nothing may be recorded"
    )


async def test_an_unknown_status_is_refused_by_name(platform, tmp_path):
    ctx = ctx_for(platform, tmp_path)
    await platform.registry.invoke("worklist_add", {"items": ["a.pdf"]}, ctx, platform.permissions)
    bad = await platform.registry.invoke(
        "worklist_done", {"key": "a.pdf", "status": "mostly"}, ctx, platform.permissions
    )
    assert bad.ok is False and "mostly" in (bad.error or "")
    assert platform.worklist.get(board_of(platform, ctx), "a.pdf").status == PENDING
    # Common synonyms are accepted rather than bounced (a bulk job must not die
    # on vocabulary), and they land on the REAL status.
    for said, meant in (("completed", DONE), ("error", FAILED)):
        ok = await platform.registry.invoke(
            "worklist_done", {"key": "a.pdf", "status": said}, ctx, platform.permissions
        )
        assert ok.ok is True
        assert platform.worklist.get(board_of(platform, ctx), "a.pdf").status == meant


def test_an_oversized_survey_is_capped_and_says_so(store):
    report = store.add(BOARD, [(f"C:/f/{i}.pdf", "") for i in range(MAX_ITEMS_PER_ADD + 7)])
    assert report["added"] == MAX_ITEMS_PER_ADD
    assert report["skipped_cap"] == 7, "a silently short queue reads as complete"


def test_done_records_a_fingerprint_and_verify_reports_a_vanished_result(
    store, tmp_path
):
    produced = tmp_path / "1099-INT Vanguard 2025.pdf"
    produced.write_bytes(b"%PDF-1.4 fake")
    store.add(BOARD, [("scan001.pdf", "")])
    store.finish(
        BOARD,
        "scan001.pdf",
        status=DONE,
        result_key=str(produced),
        result_sha256="deadbeef",
        result_size=produced.stat().st_size,
    )
    assert store.stale_results(BOARD) == []

    produced.unlink()
    stale = store.stale_results(BOARD)
    assert keys(stale) == ["scan001.pdf"], "a done item whose product is gone is STALE"


async def test_the_status_report_counts_rows_not_prose(platform, tmp_path):
    ctx = ctx_for(platform, tmp_path)
    await platform.registry.invoke(
        "worklist_add", {"items": ["a.pdf", "b.pdf", "c.pdf"]}, ctx, platform.permissions
    )
    await platform.registry.invoke("worklist_next", {"count": 2}, ctx, platform.permissions)
    await platform.registry.invoke(
        "worklist_done", {"key": "a.pdf", "status": "done"}, ctx, platform.permissions
    )
    await platform.registry.invoke(
        "worklist_done", {"key": "b.pdf", "status": "failed", "note": "image-only scan"},
        ctx, platform.permissions,
    )
    status = await platform.registry.invoke("worklist_status", {}, ctx, platform.permissions)
    assert status.ok
    assert status.data["summary"]["done"] == 1
    assert status.data["summary"]["failed"] == 1
    assert status.data["summary"]["pending"] == 1
    assert "1 of 3 done" in status.output
    assert "image-only scan" in status.output, "a failure must name its reason"
    assert [i["key"] for i in status.data["pending"]] == ["c.pdf"]


async def test_verify_says_nothing_was_checked_rather_than_all_clear(
    platform, tmp_path
):
    """A vacuous 'verified' over zero recorded results is the kind of true
    statement that reads as an assurance. It must not be said."""
    ctx = ctx_for(platform, tmp_path)
    await platform.registry.invoke("worklist_add", {"items": ["a.pdf"]}, ctx, platform.permissions)
    await platform.registry.invoke(
        "worklist_done", {"key": "a.pdf", "status": "done"}, ctx, platform.permissions
    )
    out = await platform.registry.invoke(
        "worklist_status", {"verify": True}, ctx, platform.permissions
    )
    assert "Nothing to verify" in out.output
    assert "Verified" not in out.output


async def test_next_on_an_empty_list_tells_the_agent_to_survey_first(
    platform, tmp_path
):
    ctx = ctx_for(platform, tmp_path)
    empty = await platform.registry.invoke("worklist_next", {}, ctx, platform.permissions)
    assert empty.ok is True and empty.data["claimed"] == []
    assert "worklist_add" in empty.output

    await platform.registry.invoke("worklist_add", {"items": ["a.pdf"]}, ctx, platform.permissions)
    await platform.registry.invoke(
        "worklist_done", {"key": "a.pdf", "status": "done"}, ctx, platform.permissions
    )
    finished = await platform.registry.invoke("worklist_next", {}, ctx, platform.permissions)
    assert finished.data["claimed"] == []
    assert "do not start new work" in finished.output


async def test_add_accepts_the_shapes_a_model_actually_sends(platform, tmp_path):
    ctx = ctx_for(platform, tmp_path)
    r1 = await platform.registry.invoke(
        "worklist_add",
        {"items": [{"key": "C:/f/a.pdf", "label": "1099"}, "C:/f/b.pdf"]},
        ctx,
        platform.permissions,
    )
    assert r1.data["added"] == 2
    r2 = await platform.registry.invoke(
        "worklist_add", {"items": "C:/f/c.pdf\nC:/f/d.pdf"}, ctx, platform.permissions
    )
    assert r2.data["added"] == 2 and r2.data["total"] == 4
    empty = await platform.registry.invoke("worklist_add", {}, ctx, platform.permissions)
    assert empty.ok is False and "items" in (empty.error or "")


# --------------------------------------------------------------------------- #
# (5) The end-to-end shape of the failed job.
# --------------------------------------------------------------------------- #
async def test_a_run_that_stops_early_is_resumed_exactly_where_it_stopped(
    platform, tmp_path
):
    """The real job: 22 files, run 1 gets through 8 and stops at its step
    ceiling, run 2 picks up the other 14 and no file is touched twice."""
    files = [f"C:/tax/doc{i:02d}.pdf" for i in range(22)]
    ctx_a = ctx_for(platform, tmp_path, run_id="run-1")
    await platform.registry.invoke("worklist_add", {"items": files}, ctx_a, platform.permissions)

    handled: list[str] = []
    for _chunk in range(2):  # two chunks of 4 → 8 items, then the run dies
        claimed = await platform.registry.invoke(
            "worklist_next", {"count": 4}, ctx_a, platform.permissions
        )
        for item in claimed.data["claimed"]:
            handled.append(item["key"])
            await platform.registry.invoke(
                "worklist_done",
                {
                    "key": item["key"],
                    "status": "done",
                    "result_path": item["key"].replace("doc", "1099-INT "),
                },
                ctx_a,
                platform.permissions,
            )
    assert len(handled) == 8

    # Run 2: a fresh survey of the folder (same names, since this fixture does
    # not really move files) then work the rest.
    ctx_b = ctx_for(platform, tmp_path, run_id="run-2")
    resurvey = await platform.registry.invoke(
        "worklist_add", {"items": files}, ctx_b, platform.permissions
    )
    assert resurvey.data["added"] == 0 and resurvey.data["existing"] == 22

    second: list[str] = []
    while True:
        claimed = await platform.registry.invoke(
            "worklist_next", {"count": 5}, ctx_b, platform.permissions
        )
        rows = claimed.data["claimed"]
        if not rows:
            break
        for item in rows:
            second.append(item["key"])
            await platform.registry.invoke(
                "worklist_done", {"key": item["key"], "status": "done"},
                ctx_b, platform.permissions,
            )
    assert len(second) == 14
    assert set(second).isdisjoint(handled), "run 2 must never redo run 1's work"
    assert sorted(second + handled) == sorted(files)
    assert platform.worklist.summary(board_of(platform, ctx_b))["complete"] is True


# --------------------------------------------------------------------------- #
# (6) Wiring — registration, permissions, the table, the prompt, the route.
# --------------------------------------------------------------------------- #
def test_the_four_tools_are_registered_and_allowed_on_both_copies(platform):
    for name in WORKLIST_TOOL_NAMES:
        assert platform.registry.get(name) is not None, f"{name} not registered"
        # Display (config.permissions) and enforcement (the engine's own COPY)
        # must agree — seeding one side alone is the drift v1.170.0 documented.
        assert platform.config.permissions[name] == "allow"
        assert platform.permissions.mode_for(name).value == "allow"


def test_a_user_set_permission_is_never_overwritten(tmp_path):
    """`setdefault`, not assignment: a user who denied a tool in config.toml
    keeps that decision through the next boot."""
    root = tmp_path / "home"
    root.mkdir()
    (root / ".ironjarvis").mkdir()
    (root / ".ironjarvis" / "config.toml").write_text(
        "[permissions]\nworklist_add = \"deny\"\n", encoding="utf-8"
    )
    p = build_platform(str(root))
    assert p.config.permissions["worklist_add"] == "deny"
    assert p.permissions.mode_for("worklist_add").value == "deny"


def test_the_worklist_table_is_visible_to_the_reconciler_at_boot(tmp_path):
    """The v1.151.2 lesson, for this table.

    A table created lazily by a store is INVISIBLE to
    ``_reconcile_additive_columns``: fresh databases (every test) get it, and
    the next additive column never reaches the user's existing install —
    silently. Run in a CLEAN interpreter, because import side effects cannot be
    undone and a same-process check would pass either way.
    """
    db = tmp_path / "fresh.db"
    out = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(f"""
            import json, sqlite3
            from sqlmodel import SQLModel
            # What a daemon boot imports, in boot order.
            import iron_jarvis.daemon.app  # noqa: F401
            at_import = "worklistitem" in SQLModel.metadata.tables
            from iron_jarvis.core.db import open_db
            open_db(r{str(db)!r})
            con = sqlite3.connect(r{str(db)!r})
            on_disk = [r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
            con.close()
            print(json.dumps({{"at_import": at_import, "on_disk": on_disk}}))
        """)],
        capture_output=True, text=True, timeout=180,
    )
    assert out.returncode == 0, f"{out.stdout}\n{out.stderr}"
    result = json.loads([ln for ln in out.stdout.splitlines() if ln.startswith("{")][-1])
    assert result["at_import"] is True, (
        "worklistitem was absent from SQLModel.metadata before the database was "
        "opened, so _reconcile_additive_columns cannot see it — the next column "
        "added to it will never reach an existing install"
    )
    assert "worklistitem" in result["on_disk"], "boot must CREATE the table"


def test_db_can_register_the_worklist_table_WITHOUT_any_other_import(tmp_path):
    """Contract 6's second clause, proven sufficient.

    The test above imports ``iron_jarvis.daemon.app`` first, so what it really
    proves is that SOME module's import list happens to reach
    ``worklist.models`` — today ``platform.py``'s. ``core/db.py``'s own
    docstring rejects that precedent by name: "``agents.remote`` is here even
    though ``platform.py`` happens to import it at module load: relying on that
    is relying on an UNRELATED module's import list never changing."

    Verified in a clean interpreter that imports ONLY ``core.db``: with
    ``"..worklist.models"`` in ``_LATE_MODEL_MODULES`` the table is registered
    and created with no other import in the process. ``_LATE_MODEL_MODULES``
    lives in a coordinator-owned file, so this test injects the entry the same
    way ``_register_late_models`` consumes it — which is exactly what makes the
    one-line addition both necessary and enough.
    """
    db = tmp_path / "late.db"
    out = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(f"""
            import json, sqlite3
            from sqlmodel import SQLModel
            import iron_jarvis.core.db as db  # the ONLY import: no platform
            if "..worklist.models" not in db._LATE_MODEL_MODULES:
                db._LATE_MODEL_MODULES = db._LATE_MODEL_MODULES + ("..worklist.models",)
            db.open_db(r{str(db)!r})
            in_metadata = "worklistitem" in SQLModel.metadata.tables
            con = sqlite3.connect(r{str(db)!r})
            on_disk = [r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
            con.close()
            print(json.dumps({{"in_metadata": in_metadata, "on_disk": on_disk}}))
        """)],
        capture_output=True, text=True, timeout=180,
    )
    assert out.returncode == 0, f"{out.stdout}\n{out.stderr}"
    result = json.loads([ln for ln in out.stdout.splitlines() if ln.startswith("{")][-1])
    assert result["in_metadata"] is True, (
        "the reconciler still cannot see worklistitem when db.py registers it "
        "itself — the model module is not import-safe on its own"
    )
    assert "worklistitem" in result["on_disk"]


def test_the_supervisor_carries_the_worklist_pattern_without_mutating_the_roster():
    from iron_jarvis.agents import supervisor as sup
    from iron_jarvis.agents.types import get_agent_definition

    canonical = get_agent_definition(AgentType.SUPERVISOR)
    before_prompt = canonical.system_prompt
    before_tools = list(canonical.tools)

    first = sup.supervisor_definition()
    second = sup.supervisor_definition()

    assert "worklist_next" in first.tools and "worklist_add" in first.tools
    assert "worklist_add" in first.system_prompt
    assert "SURVEY ONCE" in first.system_prompt
    # The shared definition must be UNTOUCHED — appending in place would grow
    # the prompt on every run in the process (the specs() trap, v1.165.0).
    assert canonical.system_prompt == before_prompt
    assert canonical.tools == before_tools
    assert first.system_prompt == second.system_prompt
    assert first.tools == second.tools


async def test_run_supervised_actually_uses_the_composed_definition(platform, tmp_path):
    """Assert the CALL SITE. A composed definition nothing passes to the
    runtime is a prompt the model never sees — and every test of the composer
    alone would still be green (the v1.163.0 lesson)."""
    from iron_jarvis.agents import supervisor as sup

    seen: dict = {}

    class CaptureRuntime:
        def __init__(self, _platform):
            pass

        async def run(self, session, agent_def):
            seen["def"] = agent_def
            return AgentRun(session_id=session.id, agent_type=AgentType.SUPERVISOR)

    real = sup.AgentRuntime
    sup.AgentRuntime = CaptureRuntime
    try:
        session = types.SimpleNamespace(id="sup-1")
        await sup.run_supervised(platform, session)
    finally:
        sup.AgentRuntime = real

    assert "worklist_next" in seen["def"].tools
    assert "worklist_status" in seen["def"].system_prompt


def test_the_http_read_serves_a_child_session_from_its_root_board(platform, tmp_path):
    app = FastAPI()
    register(app, types.SimpleNamespace(platform=platform))
    client = TestClient(app)

    _link(platform.engine, "sup-session", "kid-session")
    platform.worklist.add("sup-session", [("C:/f/a.pdf", ""), ("C:/f/b.pdf", "")])
    platform.worklist.finish("sup-session", "C:/f/a.pdf", status=DONE)

    body = client.get("/worklist/kid-session").json()
    assert body["board_id"] == "sup-session", (
        "the response must name the board actually served, never echo the child id"
    )
    assert body["summary"]["done"] == 1 and body["summary"]["total"] == 2
    assert sorted(i["key"] for i in body["items"]) == ["C:/f/a.pdf", "C:/f/b.pdf"]
    assert body["clipped"] is False

    unrelated = client.get("/worklist/nobody").json()
    assert unrelated["items"] == [] and unrelated["summary"]["total"] == 0


def test_the_item_statuses_are_exactly_the_four_the_ui_renders():
    from iron_jarvis.worklist import STATUSES

    assert STATUSES == (PENDING, DOING, DONE, FAILED)


# --------------------------------------------------------------------------- #
# (7) THE RECLAIM RACE — the defect that handed one file to two agents.
# --------------------------------------------------------------------------- #
def _age_claims(store, item_keys, *, seconds: int = 3600) -> None:
    """Backdate a claim so the stale window has passed."""
    old = utcnow() - timedelta(seconds=seconds)
    with session_scope(store.engine) as db:
        for key in item_keys:
            row = db.exec(
                select(WorklistItem).where(
                    WorklistItem.board_id == BOARD, WorklistItem.key == key
                )
            ).first()
            row.claimed_at = old
            row.updated_at = old
            db.add(row)
        db.commit()


def test_two_agents_reclaiming_at_once_never_get_the_same_item(store):
    """THE MEASURED DEFECT, pinned.

    The pending path was a genuine compare-and-swap; the stale-RECLAIM path was
    not. Its UPDATE's only predicate was ``status == 'doing'``, which is true of
    every row the scan had just selected AS doing — so agent A stamped the rows
    and read them back, then agent B stamped the SAME rows and read them back,
    and both were told "Claimed 3 item(s). They are yours." Measured at 4 of 40
    barrier-synchronised trials before the fix; on the acceptance folder that is
    two siblings renaming the same tax document, which is worse than the bug the
    feature exists to prevent.

    Barrier-synchronised and REPEATED, because a race that fires ~10% of the
    time passes a single-shot test nine times out of ten.
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor

    for trial in range(20):
        store.clear(BOARD)
        files = [f"C:/tax/t{trial}-{i}.pdf" for i in range(3)]
        store.add(BOARD, [(f, "") for f in files])
        crashed, _ = store.claim(BOARD, "crashed-run", 3)
        assert len(crashed) == 3
        _age_claims(store, files)

        gate = threading.Barrier(2)

        def take(name: str):
            gate.wait(timeout=10)
            return store.claim(BOARD, name, 3, stale_seconds=900)[0]

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(take, [f"a{trial}", f"b{trial}"]))

        assert set(keys(first)).isdisjoint(keys(second)), (
            f"trial {trial}: the same item was reclaimed by BOTH agents — "
            f"{sorted(set(keys(first)) & set(keys(second)))}"
        )
        # And the books balance: exactly one agent got the chunk, nothing was
        # stranded, and every row belongs to whoever was told it does.
        assert len(first) + len(second) == 3, "a reclaimable chunk went missing"
        for name, batch in ((f"a{trial}", first), (f"b{trial}", second)):
            for row in batch:
                assert store.get(BOARD, row.key).claimed_by == name
        assert store.summary(BOARD)["doing"] == 3


def test_a_claim_from_an_ENDED_run_is_reoffered_without_waiting(store):
    """A run that hit its step ceiling mid-chunk leaves items in `doing` with a
    dead `claimed_by`. Nothing released them, so a resumed run was told "N are
    in progress with another agent — wait for them" about an agent that no
    longer exists, and the only way out was the 15-minute stale window."""
    store.add(BOARD, [("C:/f/one.pdf", ""), ("C:/f/two.pdf", "")])
    with session_scope(store.engine) as db:
        db.add(AgentRun(id="run-ended", session_id="s", state=AgentState.FAILED))
        db.add(AgentRun(id="run-live", session_id="s", state=AgentState.RUNNING))
        db.commit()
    dead, _ = store.claim(BOARD, "run-ended", 1)
    live, _ = store.claim(BOARD, "run-live", 1)
    dead_key, live_key = keys(dead)[0], keys(live)[0]

    got, reclaimed = store.claim(BOARD, "resumer", 5, stale_seconds=900)

    assert keys(got) == [dead_key], "the ended run's item, with no waiting"
    assert reclaimed == 1, "and the tool must be able to SAY it was reclaimed"
    assert store.get(BOARD, live_key).claimed_by == "run-live", (
        "a RUNNING agent's claim must never be stolen"
    )


def test_release_run_hands_back_only_that_runs_claims(store):
    store.add(BOARD, [(f"C:/f/{i}.pdf", "") for i in range(4)])
    mine, _ = store.claim(BOARD, "run-1", 2)
    theirs, _ = store.claim(BOARD, "run-1x", 1)

    assert store.release_run("run-1") == 2
    summary = store.summary(BOARD)
    assert summary["pending"] == 3 and summary["doing"] == 1
    row = store.get(BOARD, keys(mine)[0])
    assert row.status == PENDING and row.claimed_by == "" and row.claim_token == ""
    assert store.get(BOARD, keys(theirs)[0]).claimed_by == "run-1x"
    assert store.release_run("run-1") == 0, "releasing twice releases nothing more"
    assert store.release_run("") == 0


async def test_next_does_not_tell_a_resumed_run_to_wait_for_a_ghost(platform, tmp_path):
    ctx = ctx_for(platform, tmp_path)
    board = board_of(platform, ctx)
    platform.worklist.add(board, [("C:/f/a.pdf", "")])
    platform.worklist.claim(board, "someone-else", 1)

    out = await platform.registry.invoke("worklist_next", {}, ctx, platform.permissions)

    assert out.data["claimed"] == []
    assert "Wait for them" not in out.output, "advice about an agent that may not exist"
    assert "worklist_done" in out.output and "pending" in out.output, (
        "the way out must be named, not implied"
    )


# --------------------------------------------------------------------------- #
# (8) THE BOARD FOLLOWS THE JOB — without this, a re-run does everything twice.
# --------------------------------------------------------------------------- #
TASK = (
    "Rename all files in this folder to a name that is more appropriate given "
    "the content"
)


def _job_session(engine, sid: str, task: str, *, project_id=None, workspace="") -> None:
    """Persist a Session row the way `create_session` does."""
    with session_scope(engine) as db:
        db.add(
            SessionRow(
                id=sid, task=task, project_id=project_id, workspace_path=str(workspace)
            )
        )
        db.commit()


async def test_a_RERUN_in_a_brand_new_session_queues_nothing(platform, tmp_path):
    """THE WAVE'S ACCEPTANCE CRITERION: "a re-run does no work twice".

    `orchestrator.rerun_session` (and the user re-posting the task) CLONES the
    inputs into a fresh session id. With the board keyed on the root session id
    the whole produced/result_norm mechanism was unreachable on every path the
    user actually takes: run 2 surveyed the already-renamed folder, saw 26
    unknown filenames, and renamed them all again.
    """
    folder = tmp_path / "Organziation of messy tax documents"
    folder.mkdir()
    _job_session(platform.engine, "sess-1", TASK, workspace=folder)
    _job_session(platform.engine, "sess-2", TASK, workspace=folder)  # the re-run
    ctx1 = ctx_for(platform, folder, session_id="sess-1", run_id="run-1")
    ctx2 = ctx_for(platform, folder, session_id="sess-2", run_id="run-2")

    originals = [str(folder / f"scan{i:03d}.pdf") for i in range(5)]
    first = await platform.registry.invoke(
        "worklist_add", {"items": originals}, ctx1, platform.permissions
    )
    assert first.data["added"] == 5
    renamed = []
    for path in originals[:3]:
        new_path = path.replace("scan", "1099-INT ")
        renamed.append(new_path)
        await platform.registry.invoke(
            "worklist_done",
            {"key": path, "status": "done", "result_path": new_path},
            ctx1,
            platform.permissions,
        )

    # Run 2 surveys the folder as it NOW stands: three renamed files and two
    # untouched originals.
    resurvey = await platform.registry.invoke(
        "worklist_add", {"items": renamed + originals[3:]}, ctx2, platform.permissions
    )
    assert resurvey.data["added"] == 0, "the re-run queued work it had already done"
    assert resurvey.data["produced"] == 3, "the renamed files are its own output"
    assert resurvey.data["existing"] == 2
    assert board_of(platform, ctx1) == board_of(platform, ctx2)

    # And what genuinely remains is still claimable by the new session.
    rest = await platform.registry.invoke(
        "worklist_next", {"count": 10}, ctx2, platform.permissions
    )
    assert sorted(i["key"] for i in rest.data["claimed"]) == sorted(originals[3:])


def test_two_different_jobs_never_share_a_board(platform, tmp_path):
    """The other direction, and the one that makes job-scoping safe: sharing a
    board between two DIFFERENT tasks would make the second one a no-op — it
    would find every file already 'done' and do nothing at all."""
    store = platform.worklist
    a = tmp_path / "clientA"
    b = tmp_path / "clientB"
    a.mkdir()
    b.mkdir()
    _job_session(platform.engine, "same-folder-1", TASK, workspace=a)
    _job_session(
        platform.engine,
        "same-folder-2",
        "Summarize every file in this folder",
        workspace=a,
    )
    _job_session(platform.engine, "other-folder", TASK, workspace=b)
    _job_session(platform.engine, "proj-1", TASK, project_id="project-alpha")
    _job_session(platform.engine, "proj-2", TASK, project_id="project-beta")

    names = ("same-folder-1", "same-folder-2", "other-folder", "proj-1", "proj-2")
    boards = {name: store.board_for_root(name) for name in names}
    assert len(set(boards.values())) == 5, f"two jobs collided: {boards}"
    # The same job asked for twice IS one board (that is the point).
    _job_session(platform.engine, "same-folder-1-again", TASK, workspace=a)
    assert store.board_for_root("same-folder-1-again") == boards["same-folder-1"]


async def test_two_chats_in_different_folders_never_see_each_others_work(
    platform, tmp_path
):
    """Chat runs EVERY turn as session id "chat" with no AgentRun row. Keyed on
    that id alone, every conversation the user ever has shares ONE permanent
    board: a bulk job armed in a chat about client A leaves pending items that a
    later chat about client B claims and is told are its own work — the model is
    handed another client's file paths and instructed to process them. This is
    the collision `repl.session.namespace_key` exists to prevent."""
    a = tmp_path / "clientA"
    b = tmp_path / "clientB"
    a.mkdir()
    b.mkdir()
    ctx_a = ctx_for(platform, a, session_id="chat", run_id="chat")
    ctx_b = ctx_for(platform, b, session_id="chat", run_id="chat")

    await platform.registry.invoke(
        "worklist_add",
        {"items": [str(a / "1040 Smith.pdf")]},
        ctx_a,
        platform.permissions,
    )
    intruder = await platform.registry.invoke(
        "worklist_next", {}, ctx_b, platform.permissions
    )

    assert intruder.data["claimed"] == [], "client A's files were handed to client B"
    assert "EMPTY" in intruder.output
    assert board_of(platform, ctx_a) != board_of(platform, ctx_b)
    # ...and the first chat still has its own list.
    mine = await platform.registry.invoke(
        "worklist_next", {}, ctx_a, platform.permissions
    )
    assert [i["key"] for i in mine.data["claimed"]] == [str(a / "1040 Smith.pdf")]


# --------------------------------------------------------------------------- #
# (9) A result path is a FILE READ, and a status report never over-claims.
# --------------------------------------------------------------------------- #
async def test_a_result_path_outside_the_allowed_roots_is_never_opened(
    platform, tmp_path
):
    """`worklist_done` fingerprints a model-supplied ABSOLUTE path. That is a
    file read, and reporting it differently for a readable vs an unreadable file
    is an existence-and-size oracle for any path on the disk — including the
    protected roots `read_file` refuses. The v1.160.0 lesson was that a SECOND
    path around fs_policy is how the app's own Fernet key became reachable."""
    ctx = ctx_for(platform, tmp_path)
    board = board_of(platform, ctx)
    secrets = Path(platform.config.home) / "secrets"
    secrets.mkdir(parents=True, exist_ok=True)
    real = secrets / ".secrets.key"
    real.write_bytes(b"fernet-key-material-that-must-not-be-measured")
    absent = secrets / ".secrets.key.absent"

    await platform.registry.invoke(
        "worklist_add", {"items": ["a.pdf", "b.pdf"]}, ctx, platform.permissions
    )
    got_real = await platform.registry.invoke(
        "worklist_done",
        {"key": "a.pdf", "status": "done", "result_path": str(real)},
        ctx,
        platform.permissions,
    )
    got_absent = await platform.registry.invoke(
        "worklist_done",
        {"key": "b.pdf", "status": "done", "result_path": str(absent)},
        ctx,
        platform.permissions,
    )

    # The bookkeeping still succeeds — refusing a fingerprint must not lose the
    # honest progress report that was being recorded.
    assert got_real.ok is True and got_absent.ok is True
    assert platform.worklist.get(board, "a.pdf").status == DONE
    assert platform.worklist.get(board, "a.pdf").result_sha256 is None
    assert platform.worklist.get(board, "a.pdf").result_size is None
    assert "outside the allowed roots" in got_real.output
    # THE ORACLE: what the two answers say ABOUT THE PATH must be identical.
    # Before the gate, the existing key said "Result recorded" and the absent
    # one said "could not be read" — that difference alone reports whether a
    # file is there, and (via the fingerprint) how big it is.
    assert got_real.output.splitlines()[1] == got_absent.output.splitlines()[1]
    assert str(real) not in got_real.output, "the refusal states no file fact"
    # And an ordinary path is still fingerprinted, so the gate did not simply
    # disable the feature.
    produced = tmp_path / "1099-INT Vanguard 2025.pdf"
    produced.write_bytes(b"%PDF-1.4 fake")
    await platform.registry.invoke(
        "worklist_add", {"items": ["c.pdf"]}, ctx, platform.permissions
    )
    ok = await platform.registry.invoke(
        "worklist_done",
        {"key": "c.pdf", "status": "done", "result_path": str(produced)},
        ctx,
        platform.permissions,
    )
    assert "Result recorded" in ok.output
    assert platform.worklist.get(board, "c.pdf").result_sha256 is not None


async def test_the_status_report_counts_the_remainder_from_the_summary(
    platform, tmp_path
):
    """"… and N more" was computed from the TRUNCATED 200-row read, so a board
    with 250 pending items reported "… and 175 more" when 225 remained. The
    panel derives its "+N more" from the summary counts; the two surfaces must
    not disagree about the same board."""
    ctx = ctx_for(platform, tmp_path)
    board = board_of(platform, ctx)
    platform.worklist.add(board, [(f"C:/f/{i:03d}.pdf", "") for i in range(250)])

    out = await platform.registry.invoke(
        "worklist_status", {}, ctx, platform.permissions
    )

    assert "0 of 250 done" in out.output
    assert "… and 225 more" in out.output, "the remainder must come from the counts"
    assert "175" not in out.output, "the truncated read must not set the remainder"
    assert "first 200" in out.output, "a capped read has to say it was capped"
    assert out.data["clipped"] is True


async def test_verify_says_how_many_results_it_actually_checked(platform, tmp_path):
    """"Verified: all 200 recorded result file(s) are present" over a 400-item
    board is a clean bill of health for work nobody looked at."""
    ctx = ctx_for(platform, tmp_path)
    board = board_of(platform, ctx)
    outputs = tmp_path / "out"
    outputs.mkdir()
    platform.worklist.add(board, [(f"item{i:03d}", "") for i in range(210)])
    for i in range(210):
        produced = outputs / f"renamed{i:03d}.pdf"
        produced.write_bytes(b"%PDF-1.4")
        platform.worklist.finish(
            board,
            f"item{i:03d}",
            status=DONE,
            result_key=str(produced),
            result_size=produced.stat().st_size,
        )

    out = await platform.registry.invoke(
        "worklist_status", {"verify": True}, ctx, platform.permissions
    )

    assert "the first 200 of 210" in out.output
    assert "all 200" not in out.output, "a capped check must never read as complete"
    assert out.data["checkable"] == 210 and out.data["checked"] == 200
    # Under the cap it says "all N" again, and it is true.
    small = platform.worklist.verify_results(board, limit=500)
    assert small["checkable"] == 210 and small["checked"] == 210
    assert small["clipped"] is False and small["stale"] == []


# --------------------------------------------------------------------------- #
# (10) The pattern has to reach the agent that actually runs a bulk job.
# --------------------------------------------------------------------------- #
def test_any_bulk_agent_can_carry_the_worklist_pattern_without_mutating_the_roster():
    """The traced failure was a BUILDER, not a supervisor: `SessionCreate.
    agent_type` defaults to "builder", and `orchestrator.run_session` sends
    everything except SUPERVISOR to `runtime.run` with the CANONICAL definition.
    Handing a builder the four tools with no instruction to use them fixes the
    roster half of the v1.142 hole and leaves the prompt half open."""
    from iron_jarvis.agents import supervisor as sup
    from iron_jarvis.agents.types import get_agent_definition

    for agent_type in (AgentType.BUILDER, AgentType.PLANNER):
        canonical = get_agent_definition(agent_type)
        before = (canonical.system_prompt, list(canonical.tools))

        built = sup.with_worklist(canonical)

        assert sup.WORKLIST_MARKER in built.system_prompt
        assert "worklist_add" in built.system_prompt
        for name in WORKLIST_TOOL_NAMES:
            assert name in built.tools, f"{agent_type} did not get {name}"
        assert (canonical.system_prompt, list(canonical.tools)) == before, (
            "the shared definition was mutated — the specs() trap (v1.165.0)"
        )
        # Idempotent: wrapping twice must not double the prompt.
        twice = sup.with_worklist(built)
        assert twice.system_prompt == built.system_prompt
        assert twice.tools == built.tools
