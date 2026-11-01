"""v1.421.0 — the REVIEW of Wave 1 part A (a coding session's helpers).

Each block is a defect the independent review found in the builder's commit,
pinned red first, or a guarantee the builder's own pins left unchecked:

* A HELPER ON A SMALL WINDOW LOST ITS TASK. The standard (``harness/
  standard``) rides the helper's SYSTEM prompt, which the agent runtime's
  transcript planner (``context/agent_window.plan_agent_transcript``) cannot
  shrink: it was capped at 16,000 characters whatever the helper's model. A
  team worker on a local model loaded at 4,096 tokens (an Ollama default) got
  ~4,300 tokens of standard in a 4,096-token window, so the planner clipped
  the worker's TASK to 400 characters ("the task is larger than this model's
  context window"). Measured before the fix: a 1,297-character piece arrived
  as 400 characters. Now the section is composed to a share of the helper
  model's KNOWN window (``daemon/chat_turn._context_window``, the one ladder),
  what was shortened or left out is SAID, and an unknown window keeps the
  full section byte for byte.
* THE NEW LEDGER COLUMN ON AN OLDER DATABASE: ``toolinvocation.shell_syntax``
  is added by the additive reconciler; a row an older app wrote reads (NULL →
  the platform's shell), the finish check's ``helper_commands`` reads it, and
  a new row writes the language.
"""

from __future__ import annotations

import json
import sqlite3

from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Session, ToolInvocation
from iron_jarvis.harness import standard as STD
from iron_jarvis.harness import team as T
from iron_jarvis.platform import build_platform
from tests import test_session_team_v1403 as _team
from tests.test_session_team_v1403 import LOCAL, answer_note, body, members, piece, stream

#: The coding-session team's world (real platform, scripted models).
world = _team.world

#: A piece long enough that a clip to the planner's 400-character floor shows.
TASK = "Set X to 2 in app.py and add a test for it. " + "One more detail of the piece. " * 40


def _big_folder(ws) -> None:
    """A folder whose rule files and guardrails fill the full section."""
    (ws / "AGENTS.md").write_text("RULE: keep the style of the file you edit.\n" * 220, encoding="utf-8")
    hd = ws / ".ironjarvis" / "harness"
    hd.mkdir(parents=True, exist_ok=True)
    (hd / "guardrails.md").write_text("Never touch vendor/ or generated/. " * 260, encoding="utf-8")


def _worker_call(w) -> dict:
    return next(c for c in w.calls if not c["main"] and c["provider"] == LOCAL[0])


# --------------------------------------------------------------------------- #
# 1. a helper on a small window keeps its task
# --------------------------------------------------------------------------- #


async def test_a_helper_on_a_small_window_keeps_its_task_and_is_told_what_was_cut(world):
    """The worker's model is known to read 4,096 tokens at once (a pin, the
    ladder's top rung): its piece arrives WHOLE, it still gets the standard's
    floor and checks and its own brief (cut to the window's share, said), and
    the section stays within that share and says what was cut to fit."""
    _big_folder(world.ws)
    sub = world.ws / ".ironjarvis" / "harness" / "sub_agents"
    sub.mkdir(parents=True, exist_ok=True)
    (sub / "qwen.md").write_text(
        "---\ndescription: The worker's brief.\n---\nBRIEF-HEAD-MARK " + "Write small commits. " * 140,
        encoding="utf-8",
    )
    world.p.config.model_context_windows = {f"{LOCAL[0]}::{LOCAL[1]}": 4096}
    members(world.p, reviewer=False)
    world.main = [piece("p1", TASK), "Done.", answer_note(world)]
    world.worker = ["Wrote it."]
    out = await stream(world.p, body(world.ws), team=T.SessionTeam(workers=("qwen",)))
    assert out["done"], out["frames"][-3:]
    call = _worker_call(world)
    first = str(call["messages"][0].content)
    assert TASK.strip() in first, f"the worker's task was cut to {len(first)} characters"
    system = call["system"]
    assert STD.STANDARD_HEADER in system and STD.CHECKS_HEADING in system
    assert "taskkill /im" in system, "the guardrail floor is never cut"
    assert "4,096 tokens at once" in system, "the cut was not said"
    assert "BRIEF-HEAD-MARK" in system, "the worker's own brief"
    assert STD.BRIEF_CUT.format(rel=".ironjarvis/harness/sub_agents/qwen.md") in system
    at = system.index(STD.STANDARD_HEADER)
    section = system[at:system.index(STD.CHECKS_CLOSE, at) + len(STD.CHECKS_CLOSE)]
    assert len(section) <= STD.window_cap(4096), len(section)


async def test_an_unknown_window_keeps_the_full_section(world):
    """No window known for the helper's model: the section the builder
    shipped, byte for byte (the planner assumes its own 32,000 tokens)."""
    _big_folder(world.ws)
    members(world.p, reviewer=False)
    world.main = [piece("p1", TASK), "Done.", answer_note(world)]
    world.worker = ["Wrote it."]
    await stream(world.p, body(world.ws), team=T.SessionTeam(workers=("qwen",)))
    system = _worker_call(world)["system"]
    assert "tokens at once" not in system
    assert STD.CUT_LINE.split("{", 1)[0] in system, "a full section this size is shortened, and says so"


def test_the_window_share_is_the_full_section_at_the_planners_own_assumed_window():
    """A model known to read 32,000 tokens or more and a model whose window is
    unknown (the planner assumes 32,000) get the SAME section."""
    assert STD.window_cap(None) is None
    assert STD.window_cap(32_000) is None and STD.window_cap(200_000) is None
    assert STD.window_cap(8_192) < STD.STANDARD_CAP
    assert STD.window_cap(4_096) < STD.window_cap(8_192)


def _std(window: "int | None"):
    from iron_jarvis.harness import components as C
    from iron_jarvis.harness.finish import FinishConfig

    return STD.build(
        rules_text="# Project folder instructions (AGENTS.md)\n" + "R" * 9_000,
        layer=C.EMPTY_LAYER, library=["L" * 1_500], finish=FinishConfig(),
        window_of=lambda provider, model: window,
    )


def test_a_section_bigger_than_its_share_leaves_parts_out_and_never_keeps_them_whole():
    """A window so small that even the never-cut parts fill its share: the
    rules and the lessons are LEFT OUT and said to be — never kept whole
    (``folder_rules._shares`` answers "no cap" for a budget at or below
    zero)."""
    text = _std(2_048).for_helper(["builder"], provider="ollama", model="tiny")
    assert "R" * 200 not in text and "L" * 200 not in text, "a part rode along whole"
    assert "left out" in text and "2,048 tokens at once" in text
    assert "taskkill /im" in text and STD.CHECK_TESTS in text


def test_a_part_given_too_little_room_is_left_out_never_a_stub():
    """A window whose share leaves each part a little room (here under
    ``MIN_PART_CHARS`` each): the parts are LEFT OUT and said to be — never
    cut to a stub that is mostly the trim marker."""
    text = _std(3_500).for_helper(["builder"], provider="ollama", model="small")
    assert "middle trimmed" not in text, "a part was cut to a stub"
    assert "R" * 20 not in text and "L" * 20 not in text
    assert "left out the folder's instruction files, the lessons" in text, text


def test_helpers_on_one_model_get_one_section_and_unknown_is_byte_identical():
    std = _std(8_192)
    a = std.for_helper(["builder"], provider="ollama", model="m")
    b = std.for_helper(["builder"], provider="ollama", model="m")
    assert a == b
    full = _std(None)
    assert full.for_helper(["builder"], provider="ollama", model="m") == full.for_helper(["builder"])
    assert len(a) < len(full.for_helper(["builder"]))


def test_a_window_lookup_that_fails_keeps_the_full_section():
    def broken(provider, model):
        raise RuntimeError("no ladder")

    from iron_jarvis.harness.finish import FinishConfig

    std = STD.build(rules_text="# x\n" + "R" * 9_000, finish=FinishConfig(), window_of=broken)
    assert std.for_helper(["builder"], provider="p", model="m") == std.for_helper(["builder"])


# --------------------------------------------------------------------------- #
# 2. the new ledger column on an older database
# --------------------------------------------------------------------------- #


def test_an_old_database_gains_shell_syntax_and_both_kinds_of_row_read(tmp_path):
    """A database made before v1.421.0 has no ``toolinvocation.shell_syntax``.
    The additive reconciler adds it at boot; a row written before reads (NULL:
    the platform's shell — the finish check re-runs it in cmd.exe, as
    before), and a new row writes the language."""
    from iron_jarvis.harness.finish import helper_commands

    home = tmp_path / "home"
    first = build_platform(str(home))
    with session_scope(first.engine) as db:
        # A helper of the Build session ``build:old-db`` (its run's option).
        db.add(Session(id="helper_s1", task="help", options_json=json.dumps({"build_session": "old-db"})))
        db.commit()
        db.add(ToolInvocation(id="tool_old", session_id="helper_s1", agent_run_id="r1", tool="shell",
                              args_json='{"command": "pytest -q"}', ok=True, output="ok", mode="allow",
                              confinement="native-unconfined"))
        db.commit()
    first.engine.dispose()
    db_path = next(home.rglob("ironjarvis.db"))
    con = sqlite3.connect(str(db_path))
    con.execute("ALTER TABLE toolinvocation DROP COLUMN shell_syntax")  # … made old
    cols = {r[1] for r in con.execute("PRAGMA table_info(toolinvocation)")}
    assert "shell_syntax" not in cols and "called_as" in cols
    con.commit()
    con.close()

    platform = build_platform(str(home))  # init_db → _reconcile_additive_columns
    con = sqlite3.connect(str(db_path))
    cols = {r[1] for r in con.execute("PRAGMA table_info(toolinvocation)")}
    con.close()
    assert "shell_syntax" in cols, "the reconciler did not add toolinvocation.shell_syntax"
    with session_scope(platform.engine) as db:
        old = db.get(ToolInvocation, "tool_old")
        assert old is not None and old.tool == "shell" and not old.shell_syntax
        db.add(ToolInvocation(id="tool_new", session_id="helper_s1", agent_run_id="r1", tool="shell",
                              args_json='{"command": "pytest -q; echo ok"}', ok=True, output="ok",
                              mode="allow", confinement="native-unconfined", shell_syntax="bash"))
        db.commit()
    with session_scope(platform.engine) as db:
        rows = db.exec(select(ToolInvocation).where(ToolInvocation.tool == "shell")).all()
        assert sorted((r.id, r.shell_syntax or "") for r in rows) == [("tool_new", "bash"), ("tool_old", "")]
    # The finish check reads both: the old row in the platform's shell, the
    # new one in bash.
    got = helper_commands(platform.engine, "build:old-db", 0)
    assert [(args["command"], syntax) for _tool, args, _h, syntax in got] == [
        ("pytest -q", ""), ("pytest -q; echo ok", "bash"),
    ], got
