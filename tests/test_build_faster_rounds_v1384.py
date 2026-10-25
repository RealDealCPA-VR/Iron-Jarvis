"""v1.384.0 — FASTER ROUNDS: fewer, fuller steps.

The user's 117-minute Build reply took 60 model rounds of 2-6 minutes each,
~2 tool calls a round (47 read_file, 46 grep, 22 shell) and changed nothing
before the round budget ended it. Each round is a whole model call, so the
cost is the NUMBER of rounds. Two plain instructions now ride the harness's
DEFAULT layer (``harness/components.py`` — every dialect inherits them, a
folder's own file replaces them like any other default):

* read or search several independent files in ONE reply (the lane runs a
  round's readers at the same time — and ``grep`` / ``glob``, the coding
  readers the user's reply used most, are now among them);
* given a list of changes, make and check one before reading for the next;
  never read the whole codebase before the first change.

They are part of the harness block, which is rendered BEFORE the history
planner (its cost is counted), and so they change the harness fingerprint.
"""

from __future__ import annotations

import asyncio

import pytest

from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import components as C
from iron_jarvis.harness import dialects as D
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_build_sessions_v1367 import Script, frames_of, new_session, open_turn, wire

# (review) the batch sentence claims no simultaneity: GPT reads through commands.
BATCH = "ask for them together in ONE reply, not one per step"
INCREMENTAL = "make a change and check it before you read for the next one"
NOT_ALL = "Do not read the whole codebase before your first change"


@pytest.mark.parametrize("dialect", sorted(D.DIALECTS))
def test_every_dialect_is_told_to_batch_reads_and_work_one_change_at_a_time(dialect):
    text = C.render(C.compose(dialect_section="Your tools: x.", dialect_id=dialect))
    assert BATCH in text and INCREMENTAL in text and NOT_ALL in text
    # In the sections they belong to.
    how = text.split("## How you work\n", 1)[1].split("\n## ", 1)[0]
    tools = text.split("## Tools\n", 1)[1].split("\n## ", 1)[0]
    assert INCREMENTAL in how and NOT_ALL in how
    assert BATCH in tools


def test_a_folders_own_files_replace_them_like_any_default():
    layer = C.ProjectLayer(present=True, valid=True, texts={
        "system_prompt": "Our own way of working.", "tool_guidance": "Our own tool rules.",
    })
    text = C.render(C.compose(dialect_section="Your tools: x.", dialect_id="claude", project=layer))
    assert BATCH not in text and INCREMENTAL not in text


def test_grep_and_glob_run_with_a_rounds_other_readers():
    for name in ("grep", "glob", "read_file"):
        assert chat_turn._batchable_call(name, {"pattern": "x"}), name
    assert not chat_turn._batchable_call("grep", {"pattern": "x", "_store_as": "hits"})
    assert not chat_turn._batchable_call("shell", {"command": "dir"})


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    return folder


async def test_a_build_turn_sends_the_guidance_and_runs_a_replys_reads_together(app, work, monkeypatch):
    """OLD: grep and glob ran one after another (they were not batchable)."""
    from iron_jarvis.harness.tools import GlobTool
    from iron_jarvis.tools.builtins import GrepTool, ReadFileTool

    meet = asyncio.Barrier(3)
    met: list[str] = []

    def together(cls, label):
        real = cls.execute

        async def execute(self, args, ctx):
            await asyncio.wait_for(meet.wait(), 10)  # serial: the first call times out
            met.append(label)
            return await real(self, args, ctx)

        monkeypatch.setattr(cls, "execute", execute)

    together(GrepTool, "grep")
    together(GlobTool, "glob")
    together(ReadFileTool, "read")
    sid = new_session(app, work)["id"]
    script = wire(app, Script([
        [
            ToolCall(id="g1", name="Grep", arguments={"pattern": "x"}),
            ToolCall(id="g2", name="Glob", arguments={"pattern": "*.py"}),
            ToolCall(id="r1", name="Read", arguments={"file_path": "app.py"}),
        ],
        "Read them all at once.",
    ]))
    status, raw = await open_turn(app, sid, {"text": "look around", "turn_id": "fr-1"}, timeout=60)
    assert status == 200, raw
    frames = frames_of(raw)
    assert frames[-1][0] == "done", frames[-3:]
    finished = {d["name"]: d.get("ok") for e, d in frames if e == "tool_call" and d.get("status") == "finished"}
    assert finished == {"grep": True, "glob": True, "read_file": True}, finished
    assert sorted(met) == ["glob", "grep", "read"]
    system = script.calls[0]["system"]
    assert BATCH in system and INCREMENTAL in system
    # Inside the harness block (rendered before the planner counts the prompt).
    assert system.index("# IJ harness") < system.index(BATCH)
